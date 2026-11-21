"""Keep the Soulseek listen port open and know whether it is.

Sharing is the half of Soulseek that keeps an account in good standing, and it needs one inbound TCP
port. This service asks the router to open it (portmap), asks the Soulseek project's port test whether
it is open (portcheck), keeps the lease alive while the app runs, and publishes what it found on the
shared status dict so health, the event stream and the pages all read the same thing. What it cannot
do is open a port on a router that will not be asked, or on a VPN: then `reachable` is False and the UI
shows the user how to do it by hand.

Nothing in here may raise. `run_forever` is a member of the app's TaskGroup, so an exception escaping
it would take the web server down with it, and `release` runs in the shutdown path ahead of stopping
the sidecar. A router that stops answering is an ordinary Tuesday, not a reason to lose the app.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime

from . import portcheck, portmap

log = logging.getLogger(__name__)
LEASE_S = 3600
RECHECK_S = 1800
# A closed port is only reported as closed once a second check, at least CONFIRM_GAP_S after the first,
# says so too: the port test can false-alarm (a router slow to apply a fresh mapping, a blip at the test
# server), and the sidebar turning amber over one bad reading would cry wolf. After a first closed
# reading the loop rechecks after CONFIRM_S rather than waiting the full RECHECK_S. The gap matters at
# launch, where the sign-in and the loop's first tick both check within seconds of each other.
CONFIRM_S = 120
CONFIRM_GAP_S = 60
CHECK_FAILED = "Something went wrong checking the port."


def empty_state(port: int | None = None, enabled: bool = False) -> dict:
    return {"port": port, "enabled": enabled, "checking": False, "mapping": None, "reachable": None,
            "public_ip": None, "lan_ip": None, "gateway": None, "checked_at": None, "error": None,
            "closed_confirmed": False}


class Sharing:
    def __init__(self, settings, status: dict, http, *, port: int, mapper=portmap.map_port,
                 unmapper=portmap.unmap_port, checker=portcheck.check_port,
                 gateway=portmap.default_gateway, lan=portmap.lan_ip, clock=time.monotonic,
                 sleep=asyncio.sleep, lease_s: int = LEASE_S, recheck_s: int = RECHECK_S,
                 confirm_s: int = CONFIRM_S, confirm_gap_s: int = CONFIRM_GAP_S):
        self._settings, self._status, self._http, self._port = settings, status, http, port
        self._map, self._unmap, self._check = mapper, unmapper, checker
        self._gateway, self._lan, self._clock, self._sleep = gateway, lan, clock, sleep
        self._lease_s, self._recheck_s = lease_s, recheck_s
        self._confirm_s, self._confirm_gap_s = confirm_s, confirm_gap_s
        # When the current run of closed readings began; None while the port reads open (or was never
        # read). An unknown reading -- the test service down -- neither starts nor ends a run.
        self._closed_since: float | None = None
        self._mapping: portmap.Mapping | None = None
        self._mapped_at: float | None = None
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self.state = empty_state(port, bool(settings.soulseek_enabled))
        self._publish()

    @property
    def can_check(self) -> bool:
        """Whether there is a port worth checking, read from the setting at call time. Not
        `state["enabled"]`, which only turns true inside a refresh: the wizard's Soulseek step has the
        account set up in memory at once, so gating on the state answered "Check again" with a 409 until
        the next scheduled tick -- up to half an hour later."""
        return bool(self._settings.soulseek_enabled)

    def _publish(self) -> None:
        # A copy, so a later mutation of `self.state` cannot change what was published without
        # going through _set (which is what makes the status dict's setitem fire an SSE event).
        self._status["sharing"] = dict(self.state)

    def _set(self, **changes) -> None:
        self.state.update(changes)
        self._publish()

    def _lease(self) -> float:
        """The lease actually in force. A router may grant far less than the hour we asked for --
        NAT-PMP replies with its own lifetime -- and renewal has to follow what it granted."""
        granted = self._mapping.lease_s if self._mapping is not None else 0
        return float(granted if 0 < granted < self._lease_s else self._lease_s)

    @property
    def _confirming(self) -> bool:
        """One closed reading in, not yet confirmed by a second."""
        return self._closed_since is not None and not self.state["closed_confirmed"]

    def _recheck_due_s(self) -> float:
        return float(self._confirm_s if self._confirming else self._recheck_s)

    def _tick_s(self) -> float:
        """Never sleep past half the lease in force, nor past the recheck (or confirm) interval."""
        return min(self._lease() / 2, self._recheck_due_s())

    def _closed_reading(self, reachable: bool | None) -> bool:
        """Fold one reading into the closed run; answers whether the port now counts as closed."""
        if reachable is True:
            self._closed_since = None
            return False
        confirmed = bool(self.state["closed_confirmed"])
        if reachable is False:
            now = self._clock()
            if self._closed_since is None:
                self._closed_since = now
            elif now - self._closed_since >= self._confirm_gap_s:
                confirmed = True
        return confirmed

    async def refresh(self, *, remap: bool = False) -> dict:
        """Map if there is no live mapping, then verify from outside. Serialized by `_lock` so a
        manual "Check again" during the scheduled renewal does not race it. `remap` asks the router
        again even when a mapping is held -- the Fix button, for a router that forgot or refused it."""
        async with self._lock:
            if remap:
                self._mapping, self._mapped_at = None, None
            return await self._refresh()

    async def _refresh(self) -> dict:
        self._set(enabled=bool(self._settings.soulseek_enabled), checking=True, error=None)
        try:
            # default_gateway shells out to `route`/`ip` and lan_ip opens a socket: both block, so
            # neither may run on the event loop.
            gateway = await asyncio.to_thread(self._gateway)
            self._set(gateway=gateway, lan_ip=await asyncio.to_thread(self._lan, gateway))
            if self._mapping is None:
                self._mapping = await self._map(self._port, self._lease_s, self._http,
                                                gateway=gateway)
                self._mapped_at = self._clock() if self._mapping else None
            result = await self._check(self._port, self._http)
        except Exception:
            # Deliberately broad: this runs on a background loop that owns the app's lifetime, and a
            # router or a port-test server having a bad minute must never take the app down. The
            # traceback goes to the log and the user gets `error` in the state.
            log.exception("sharing check failed")
            self._set(checking=False, error=CHECK_FAILED)
            return dict(self.state)
        self._set(checking=False, mapping=self._mapping.protocol if self._mapping else None,
                  reachable=result.reachable, public_ip=result.public_ip, error=result.error,
                  closed_confirmed=self._closed_reading(result.reachable),
                  checked_at=datetime.now(UTC).isoformat(timespec="seconds"))
        log.info("sharing: port %d %s%s", self._port,
                 {True: "reachable", False: "closed", None: "unknown"}[result.reachable],
                 f" (opened via {self._mapping.protocol})" if self._mapping else "")
        return dict(self.state)

    def start_refresh(self, *, remap: bool = False) -> dict:
        """Kick a refresh off in the background: mapping and checking take tens of seconds and must
        not hold a request open. The caller polls the state (health or the event stream)."""
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self.refresh(remap=remap))
            self._set(checking=True)
        return dict(self.state)

    def start_fix(self) -> dict:
        """Ask the router to open the port again, then check it. Only worth offering while the port
        is closed and no mapping is held: with a mapping in place the router already said yes, and
        what blocks the port is past it -- a VPN or a second router -- which asking again cannot reach."""
        return self.start_refresh(remap=True)

    async def run_forever(self) -> None:
        """Renew the mapping before the lease in force is half gone, and recheck reachability every
        `recheck_s`. A copy without Soulseek only waits, and picks up when the wizard turns it on."""
        last_check: float | None = None
        while True:
            try:
                last_check = await self._tick(last_check)
            except Exception:
                # Same reasoning as refresh(), one level up: this coroutine is a member of the app's
                # TaskGroup, so raising here would cancel the web server.
                log.exception("sharing loop failed; carrying on")
            await self._sleep(self._tick_s())

    async def _tick(self, last_check: float | None) -> float | None:
        if not self._settings.soulseek_enabled:
            if self.state["enabled"]:
                self._closed_since = None
                self._set(enabled=False, closed_confirmed=False)
            return last_check
        now = self._clock()
        if self._mapped_at is not None and now - self._mapped_at >= self._lease() / 2:
            self._mapping, self._mapped_at = None, None   # renew: a fresh request replaces the lease
        if self._mapping is None or last_check is None or now - last_check >= self._recheck_due_s():
            await self.refresh()
            return self._clock()
        return last_check

    async def release(self) -> None:
        """Give the port back on quit. Best effort: this runs before the sidecar is stopped, so a
        router that will not listen must not cost us the rest of the shutdown."""
        if self._task is not None and not self._task.done():
            self._task.cancel()   # a check still in flight has nothing left to report
        if self._mapping is None:
            return
        try:
            await self._unmap(self._mapping, self._http)
        except Exception:
            log.exception("could not release the port mapping")
        finally:
            self._mapping, self._mapped_at = None, None
