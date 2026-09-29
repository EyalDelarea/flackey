"""Supervise the slskd sidecar process: start it, wait for it to become healthy, stop it.

`slskd_config.py` owns the config file and API key; this module owns the process only — it takes
the URL and API key as plain constructor arguments and never reads slskd.yml itself.

On Windows the same code runs with two differences worth knowing. The asyncio subprocess needs the
Proactor event loop, which is what `asyncio.run` gives a Windows thread by default and what both entry
points use (`desktop.start_server_thread` and `cli.start`); uvicorn never gets to choose, because the
app awaits `Server.serve()` inside that loop and only `Server.run()` consults uvicorn's loop factory.
And `terminate()` is TerminateProcess, a hard kill rather than SIGTERM -- see `stop`.
"""
from __future__ import annotations

import asyncio
import logging
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

import httpx

from .slskd_binary import SlskdBinaryError, binary_path, install_dir, is_installed

log = logging.getLogger(__name__)


class SlskdStartTimeout(SlskdBinaryError):
    """slskd is installed and was launched but never answered in time: slow, not missing."""

_POLL_INTERVAL_S = 0.2
_WAIT_REQUEST_TIMEOUT_S = 5.0
# How long a freshly spawned slskd gets to answer. A warm start on a Mac takes a few seconds, but a slow PC
# is another matter: in the Windows test VM (x64 slskd emulated on ARM, on a fresh install busy updating
# and scanning) Windows took up to 90 s to launch the new slskd.exe and slskd another minute to finish
# starting. Only a slskd that never starts ever waits this long.
SLSKD_START_TIMEOUT_S = 180.0
_SIGKILL_GRACE_S = 5.0
_CREATE_NO_WINDOW = 0x08000000  # subprocess.CREATE_NO_WINDOW, which only a Windows build defines


def _no_window() -> dict:
    """A copy of `tools.no_window`, which this module sits below in the import layers and may not
    import: on Windows, start slskd without the console window a GUI app's console child otherwise
    gets (slskd is a console program, and that window would sit open for as long as the app runs).
    Empty elsewhere, where POSIX `Popen` rejects any `creationflags`."""
    if sys.platform == "win32":
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", _CREATE_NO_WINDOW)}
    return {}


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

    async def start(self, *, timeout_s: float = SLSKD_START_TIMEOUT_S) -> None:
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
                **_no_window(),
            )
        finally:
            log_file.close()

        healthy = await self.wait_healthy(timeout_s=timeout_s)
        if not healthy:
            # Never leave an orphan behind: if it didn't come up healthy, tear it down before raising.
            await self.stop()
            raise SlskdStartTimeout("slskd did not become healthy within the timeout")

    async def probe(self, client: httpx.AsyncClient | None = None) -> bool:
        """A single health check, no polling. True when something answers `GET .../application` with a
        non-server-error status. Used to detect an already-running slskd without waiting for one."""
        return await self._check(client) is None

    async def _check(self, client: httpx.AsyncClient | None = None) -> str | None:
        """One health check: None when slskd answered, else why not (for the log)."""
        try:
            if client is None:
                async with httpx.AsyncClient(timeout=2.0) as own:
                    resp = await self._get_application(own)
            else:
                resp = await self._get_application(client)
        except httpx.HTTPError as exc:
            return f"{type(exc).__name__}: {exc}"
        return None if resp.status_code < 500 else f"HTTP {resp.status_code}"

    async def _get_application(self, client: httpx.AsyncClient) -> httpx.Response:
        return await client.get(f"{self._url}/api/v0/application", headers={"X-API-Key": self._api_key})

    async def wait_healthy(self, *, timeout_s: float = 30) -> bool:
        """Poll `GET <url>/api/v0/application` until it answers or the timeout expires. Returns a
        bool; never raises on an unhealthy or unreachable sidecar.

        One client serves every poll: building an httpx client sets up TLS, which cost 130 ms a probe on
        a Windows CI runner and far more on a slow PC busy starting slskd itself. Each request gets
        `_WAIT_REQUEST_TIMEOUT_S`, since a slskd just starting can be slow to answer its first one."""
        deadline = self._clock() + timeout_s
        last = "no answer"
        async with httpx.AsyncClient(timeout=_WAIT_REQUEST_TIMEOUT_S) as client:
            while True:
                failure = await self._check(client)
                if failure is None:
                    return True
                last = failure
                if self._clock() >= deadline:
                    log.warning("slskd gave no healthy answer in %.0f s; last try: %s", timeout_s, last)
                    return False
                await self._sleep(_POLL_INTERVAL_S)

    async def stop(self, *, timeout_s: float = 10) -> None:
        """Send SIGTERM, wait, then SIGKILL if it must. Safe to call when nothing is running, and
        safe to call twice.

        On Windows there is no SIGTERM to send: asyncio's `terminate()` is TerminateProcess, which ends
        slskd on the spot with no shutdown of its own. That is accepted rather than worked around. slskd
        keeps its state in SQLite, which survives an abrupt stop, and a transfer cut off mid-file is one
        this app already treats as failed and retries; the alternative (a console control event) needs
        slskd to share a console with this process, which the windowed build deliberately does not have.
        The wait below then returns on the first poll, and the `kill()` fallback is never reached."""
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
