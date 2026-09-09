"""Bring Soulseek up on an already-running flackey, so the setup wizard can tell the owner whether
their account actually works instead of asking them to restart and find out.

Soulseek has no separate registration step: a username is claimed by signing in with it, and an unused
name becomes yours at that moment. So the wizard's form *is* the sign-up form -- what it was missing was
the answer. This module supplies it: write credentials, restart the sidecar so it reads them, rebuild
the worker's provider list, then watch until the Soulseek server either accepts the login or does not.

It owns the sidecar handle and the worker's provider list together because they have to change together:
a sidecar running under new credentials with providers still built from the old API key is a state where
nothing works and nothing says so.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable

import httpx

from .config import Settings
from .slskd_binary import SlskdBinaryError, is_installed
from .slskd_process import SlskdProcess
from .source.lossless import LosslessError

log = logging.getLogger(__name__)
CONNECT_TIMEOUT_S = 45.0
POLL_S = 1.0
# The Soulseek server does not tell a client *why* a login failed, so this never claims to know. It names
# the likeliest cause and leaves the owner a next step; a wrong guess stated as fact would be worse.
TAKEN_HINT = ("Couldn't sign in to Soulseek. If somebody already uses that name, pick a different one — "
              "an unused name becomes yours the moment it signs in.")


class SoulseekLink:
    def __init__(self, settings: Settings, worker, http: httpx.AsyncClient, *,
                 build_providers: Callable[[Settings, httpx.AsyncClient], list],
                 process_factory: Callable[..., SlskdProcess] = SlskdProcess,
                 installed: Callable[[object], bool] = is_installed,
                 clock: Callable[[], float] = time.monotonic, sleep=asyncio.sleep):
        self._settings, self._worker, self._http = settings, worker, http
        self._build_providers, self._clock, self._sleep = build_providers, clock, sleep
        self._process_factory, self._installed = process_factory, installed
        self.process: SlskdProcess | None = None
        self._task: asyncio.Task | None = None
        self.state: dict = {"state": "idle", "username": None, "error": None}

    def adopt(self, process: SlskdProcess | None) -> None:
        """Take over the sidecar the app started at boot, so a later reconnect stops that one rather
        than leaving it running beside a second copy."""
        self.process = process

    def start_connect(self) -> dict:
        """Kick off a connect in the background and return immediately: signing in takes tens of
        seconds and must not hold the request open. Poll `state` for the answer."""
        if self._task is not None and not self._task.done():
            return dict(self.state)
        self.state = {"state": "connecting", "username": None, "error": None}
        self._task = asyncio.create_task(self._connect())
        return dict(self.state)

    async def _connect(self) -> None:
        try:
            await self._restart_sidecar()
            self._worker.providers = self._build_providers(self._settings, self._http)
            await self._wait_for_login()
        except SlskdBinaryError as e:
            log.warning("soulseek connect: sidecar would not start: %s", e)
            self._fail("Soulseek isn't installed yet. Finish getting it ready, then try again.")
        except Exception:   # a failed sign-in must never take the app down with it
            log.exception("soulseek connect failed")
            self._fail("Something went wrong signing in to Soulseek. Try again.")

    async def _restart_sidecar(self) -> None:
        if self.process is not None:
            await self.process.stop()
            self.process = None
        if not self._installed(self._settings.data_dir):
            raise SlskdBinaryError("slskd is not installed")
        # A fresh handle, because the API key it authenticates with may have just been written.
        self.process = self._process_factory(self._settings.data_dir, self._settings.slskd_url,
                                             self._settings.slskd_api_key or "")
        await self.process.start()

    async def _wait_for_login(self) -> None:
        provider = next(iter(self._worker.providers), None)
        if provider is None:
            self._fail("Soulseek isn't switched on. Save your account details first.")
            return
        deadline = self._clock() + CONNECT_TIMEOUT_S
        last = None
        while True:
            try:
                health = await provider.health()
            except (LosslessError, httpx.HTTPError, OSError):
                health = {"status": "unreachable", "username": None}   # a sidecar still booting: expected
            last = health.get("status")
            if last == "ok":
                self.state = {"state": "connected", "username": health.get("username"), "error": None}
                self._worker.status["lossless_provider"] = {"name": provider.name, **health}
                log.info("soulseek signed in as %s", health.get("username"))
                return
            if self._clock() >= deadline:
                self._fail(TAKEN_HINT if last == "not_logged_in" else
                           "Soulseek didn't answer in time. Check your connection and try again.")
                return
            await self._sleep(POLL_S)

    def _fail(self, message: str) -> None:
        self.state = {"state": "failed", "username": None, "error": message}
