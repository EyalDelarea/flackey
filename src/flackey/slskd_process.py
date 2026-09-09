"""Supervise the slskd sidecar process: start it, wait for it to become healthy, stop it.

`slskd_config.py` owns the config file and API key; this module owns the process only — it takes
the URL and API key as plain constructor arguments and never reads slskd.yml itself.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from pathlib import Path

import httpx

from .slskd_binary import SlskdBinaryError, binary_path, install_dir, is_installed

log = logging.getLogger(__name__)

_POLL_INTERVAL_S = 0.2
_SIGKILL_GRACE_S = 5.0


class SlskdProcess:
    """Starts, health-checks, and stops the slskd sidecar.

    `clock` and `sleep` are injectable so tests never perform a real wait: `clock` is a zero-arg
    callable returning a monotonic float, `sleep` is an async callable taking seconds.
    """

    def __init__(
        self,
        data_dir: Path,
        url: str,
        api_key: str,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], asyncio.Future[None]] = asyncio.sleep,
    ) -> None:
        self._data_dir = data_dir
        self._url = url.rstrip("/")
        self._api_key = api_key
        self._clock = clock
        self._sleep = sleep
        self._proc: asyncio.subprocess.Process | None = None

    @property
    def running(self) -> bool:
        """True when this instance owns a live subprocess. False for a slskd started by hand or by
        another instance — `stop()` only ever signals a process this instance spawned."""
        return self._proc is not None and self._proc.returncode is None

    async def start(self, *, timeout_s: float = 30) -> None:
        """Launch slskd and wait for it to become healthy.

        If something already answers health checks on `url` — the owner's own slskd, or a previous
        instance this process didn't spawn — this does not start a second one: it checks health first
        and returns quietly.
        """
        if self.running:
            return
        if await self.probe():
            log.info("slskd already answering on %s; not starting a second instance", self._url)
            return

        if not is_installed(self._data_dir):
            raise SlskdBinaryError("slskd is not installed; run the setup install step first")

        app_dir = self._data_dir / "slskd"
        app_dir.mkdir(parents=True, exist_ok=True)
        log_path = self._data_dir / "slskd.log"
        log_file = log_path.open("ab")
        try:
            self._proc = await asyncio.create_subprocess_exec(
                str(binary_path(self._data_dir)),
                "--app-dir",
                str(app_dir),
                cwd=str(install_dir(self._data_dir)),
                stdout=log_file,
                stderr=log_file,
            )
        finally:
            log_file.close()

        healthy = await self.wait_healthy(timeout_s=timeout_s)
        if not healthy:
            # Never leave an orphan behind: if it didn't come up healthy, tear it down before raising.
            await self.stop()
            raise SlskdBinaryError("slskd did not become healthy within the timeout")

    async def probe(self) -> bool:
        """A single health check, no polling. True when something answers `GET .../application` with a
        non-server-error status. Used to detect an already-running slskd without waiting for one."""
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                resp = await client.get(
                    f"{self._url}/api/v0/application",
                    headers={"X-API-Key": self._api_key},
                )
            return resp.status_code < 500
        except httpx.HTTPError:
            return False

    async def wait_healthy(self, *, timeout_s: float = 30) -> bool:
        """Poll `GET <url>/api/v0/application` until it answers or the timeout expires. Returns a
        bool; never raises on an unhealthy or unreachable sidecar."""
        deadline = self._clock() + timeout_s
        while True:
            if await self.probe():
                return True
            if self._clock() >= deadline:
                return False
            await self._sleep(_POLL_INTERVAL_S)

    async def stop(self, *, timeout_s: float = 10) -> None:
        """Send SIGTERM, wait, then SIGKILL if it must. Safe to call when nothing is running, and
        safe to call twice."""
        proc, self._proc = self._proc, None
        if proc is None or proc.returncode is not None:
            return

        try:
            proc.terminate()
        except ProcessLookupError:
            return

        # Polling `proc.returncode` (rather than awaiting `proc.wait()`) after each `await self._sleep`
        # lets a real event loop's child watcher update it in the background while keeping the wait
        # itself on the injectable clock/sleep, so tests never perform a real wait.
        deadline = self._clock() + timeout_s
        while proc.returncode is None and self._clock() < deadline:
            await self._sleep(_POLL_INTERVAL_S)

        if proc.returncode is None:
            try:
                proc.kill()
            except ProcessLookupError:
                return
            deadline = self._clock() + _SIGKILL_GRACE_S
            while proc.returncode is None and self._clock() < deadline:
                await self._sleep(_POLL_INTERVAL_S)
