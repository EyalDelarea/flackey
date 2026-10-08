"""The worker itself: its state, its lifecycle, the owner's actions on a request, and the guard around
each pass. The stages a pass runs through are the mixins it is built from."""
from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Awaitable, Callable

import httpx

from ..attempts import prune_raw
from ..config import Settings
from ..library import prune_missing_tracks
from ..models import RETRYABLE_STATES, Request, RequestState
from ..notify import Notifier
from ..source import LosslessError, LosslessProvider, Source, SourceUnauthorized
from ..store import Store
from ..tag import fetch_artwork
from .acoustic import AcousticMixin
from .filing import FilingMixin
from .lossless_attempt import LosslessMixin
from .pipeline import PipelineMixin
from .policy import (
    CANCELLABLE,
    LOGIN_REQUIRED,
    LOSSLESS_HEALTH_EVERY_S,
    LOSSLESS_HEALTH_SETTLING_S,
    MAINTENANCE_EVERY_S,
    STAGE_OF_STATE,
)
from .records import CatalogLike
from .recovery import RecoveryMixin

log = logging.getLogger(__name__)


class Worker(PipelineMixin, RecoveryMixin, FilingMixin, LosslessMixin, AcousticMixin):
    def __init__(self, store: Store, source: Source, catalog: CatalogLike, notifier: Notifier,
                 settings: Settings,
                 artwork_fetch: Callable[[str], Awaitable[bytes | None]] = fetch_artwork,
                 status: dict | None = None,
                 providers: list[LosslessProvider] | None = None,
                 http: httpx.AsyncClient | None = None,
                 clock: Callable[[], float] = time.monotonic):
        self.store, self.source, self.catalog, self.notifier, self.settings = store, source, catalog, notifier, settings
        self.artwork_fetch = artwork_fetch
        # shared with /api/health (Task 14): {"telegram_authorized": bool}; the worker flips it to False
        # when the Telethon session dies and then stops, leaving every request queued for the next run
        self.status = status if status is not None else {"telegram_authorized": True}
        self.providers = list(providers or [])
        self.http = http or httpx.AsyncClient(timeout=20)
        self.clock = clock
        self._last_maintenance: float | None = None
        self._last_health: float | None = None
        # request id -> the task running it. This, not the row's state, is what stops a request being
        # started twice: a task that has been created has not run yet, so the next poll would still see
        # QUEUED and start a second one. Entries are removed by a done-callback rather than by `process`
        # itself, so a request `_retry_or_fail` has put back on the queue cannot be re-claimed before its
        # own task has finished unwinding.
        self._tasks: dict[int, asyncio.Task] = {}
        # Where each in-flight transfer has got to, keyed by request id. It used to be a single slot
        # because there was only ever one download; with the whole queue moving at once every row needs
        # its own bar. Published as a list because `Status.__setitem__` is what fires the SSE event --
        # mutating a nested dict in place would change nothing on screen.
        self._progress: dict[int, dict] = {}
        # Verify, fingerprint and convert are ffmpeg and fpcalc: CPU, not waiting. Fourteen at once do not
        # finish the playlist any sooner, they just take the machine down with them. Network stages stay
        # unbounded -- this bounds only the part where more parallelism buys nothing.
        self._cpu = asyncio.Semaphore(os.cpu_count() or 2)


    def _set_state(self, req: Request, state: RequestState, **kw) -> None:
        """Every state change goes through here so a remote log shows the full path of a request. `update_request`
        (not the narrower `store.set_state`) so callers can carry whatever extra fields the transition needs
        (chosen_candidate_id, track_id, attempts, retry_after, ...) alongside the new state."""
        title = (req.query_title or req.raw_text or "")[:80]
        log.debug("req#%d %s -> %s %s", req.id, req.state, state, title)
        if state == RequestState.AWAITING_REVIEW:
            # Recorded here rather than at the two call sites that park a request, so a third one cannot
            # forget to. Nothing clears it: the ladder's Choose rung is the fact that this track once
            # waited on a person, which stays true after they have answered.
            kw.setdefault("reviewed", 1)
        if state == RequestState.ERROR:
            # The row still holds the stage this request is failing out of; once `update_request` runs it
            # does not. Derived here rather than passed by each caller so no terminal path can be mute --
            # including ones that do not exist yet, and including `process`'s last-resort `except`, which
            # has no idea where it came from. Read from the *store*: `req` is a snapshot the transitions
            # above it do not refresh, so on the fetch path it still says `queued` while the row says
            # `fetching`. `setdefault`, so a caller that does know better keeps the last word.
            try:
                was = self.store.get_request(req.id).state
            except KeyError:
                was = req.state   # removed mid-flight; the snapshot is all that is left to ask
            kw.setdefault("failed_stage", STAGE_OF_STATE.get(was, "unknown"))
        self.store.update_request(req.id, state=state, **kw)


    def on_start(self) -> None:
        n = self.store.reset_inflight()
        if n:
            log.info("re-queued %d in-flight requests", n)
        gone = prune_missing_tracks(self.store)
        if gone:
            log.info("forgot %d library rows whose files were removed", len(gone))
        n = self.store.mark_open_attempts_interrupted()
        if n:
            log.info("marked %d unfinished lossless attempts interrupted", n)


    async def startup(self) -> None:
        """on_start plus the async parts: cancel transfers the sidecar kept running, then the first maintenance."""
        self.on_start()
        for p in self.providers:
            try:
                n = await p.cancel_all()
                if n:
                    log.info("cancelled %d %s downloads left from the previous run", n, p.name)
            except LosslessError as e:
                log.warning("%s: could not cancel old downloads: %s", p.name, e)
        await self._maintenance(force=True)
        await self.refresh_lossless_health(force=True)


    async def refresh_lossless_health(self, force: bool = False) -> None:
        """Keep `status["lossless_provider"]` answering "can we reach Soulseek *now*" rather than "could we
        during the last attempt". The sidebar dot reads this: populated only inside `_attempt`, it stayed
        null until the first lossless download of the session -- blank on exactly the cold start where
        someone is looking at it. Self-throttled, so the idle loop can call it every tick."""
        now = self.clock()
        # Slow while it is healthy, quick while it is not: slskd takes ten-odd seconds to reach the
        # Soulseek server after a restart, and a flat 60 s left the sidebar saying "signing in" for most
        # of a minute after it had already signed in (measured 2026-09-09).
        was_ok = (self.status.get("lossless_provider") or {}).get("status") == "ok"
        every = LOSSLESS_HEALTH_EVERY_S if was_ok else LOSSLESS_HEALTH_SETTLING_S
        if not force and self._last_health is not None and now - self._last_health < every:
            return
        self._last_health = now
        for p in self.providers:
            try:
                self.status["lossless_provider"] = {"name": p.name, **await p.health()}
            except Exception:  # a health probe must never take the worker down (spec: no raise out of lossless)
                log.exception("%s: health probe failed", p.name)
                self.status["lossless_provider"] = {"name": p.name, "status": "unreachable", "username": None}


    async def _maintenance(self, force: bool = False) -> None:
        """Daily: prune raw attempt folders and ask providers to rescan the shared library (spec §7, §17.3)."""
        now = self.clock()
        if not force and self._last_maintenance is not None and now - self._last_maintenance < MAINTENANCE_EVERY_S:
            return
        self._last_maintenance = now
        await asyncio.to_thread(prune_raw, self.settings.lossless_raw_dir, self.settings.lossless_keep_raw_days)
        for p in self.providers:
            try:
                await p.rescan_shares()
            except LosslessError as e:
                log.warning("%s: rescan failed: %s", p.name, e)


    def _start_due(self) -> None:
        """Put every queued request that is due on its own task. Claiming happens here, synchronously and
        before the first await: `create_task` only schedules, so a request whose row still says QUEUED when
        the next tick comes round would otherwise be started a second time and filed twice."""
        cap = self.settings.max_concurrent_requests
        room = None if cap is None else cap - len(self._tasks)
        if room is not None and room <= 0:
            return
        for req in self.store.due_queued():
            if req.id in self._tasks:
                continue
            task = asyncio.create_task(self.process(req.id), name=f"req{req.id}")
            self._tasks[req.id] = task
            task.add_done_callback(lambda t, rid=req.id: self._tasks.pop(rid, None))
            if room is not None:
                room -= 1
                if room == 0:
                    return


    async def run_forever(self, poll_s: float = 2.0) -> None:
        """Run every queued track at once, each on its own task.

        The queue used to be a line: one request off the head of it, awaited to the end, then the next. On
        a fourteen-track playlist that meant thirteen tracks watching one download at 100 kB/s. Two tracks
        share nothing that makes an order necessary -- the peer sending one has its own upload slot, and
        the two stages that *are* shared (the single Deezer bot conversation, slskd's single-search
        endpoint) hold their own locks inside the components that own them. So the loop's job is no longer
        to take turns; it is to keep starting whatever is due and to wait for the lot at the end."""
        await self.startup()
        try:
            while self.status.get("telegram_authorized", True) or not self.settings.source_enabled:
                await self._maintenance()
                self._start_due()
                await self.refresh_lossless_health()
                await asyncio.sleep(poll_s)
        finally:
            # Ctrl-C cancels this coroutine; the tracks it started are separate tasks and would otherwise
            # outlive it, still writing to the store the app is closing. Each one puts itself back on the
            # queue as it unwinds (see `process`), so the next run picks up where this one stopped.
            await self._stop_all()
        # Unguarded on purpose: reaching this line means the loop ended normally, which only happens
        # when the source is on and Telegram is signed out (cancellation skips it entirely).
        log.error("worker stopped: %s (sign in from the setup screen in the UI)", LOGIN_REQUIRED)


    async def _stop_all(self) -> None:
        tasks = list(self._tasks.values())
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


    async def choose(self, request_id: int, candidate_id: int) -> Request:
        req = self.store.get_request(request_id)
        cand = self.store.get_candidate(candidate_id)
        if cand.request_id != request_id:
            raise ValueError(f"candidate {candidate_id} does not belong to request {request_id}")
        if req.state != RequestState.AWAITING_REVIEW:
            raise ValueError(f"request {request_id} is {req.state.value}, not awaiting review")
        self._set_state(req, RequestState.QUEUED, chosen_candidate_id=candidate_id, flag_reason=None,
                        confidence=cand.score, retry_after=None)
        return self.store.get_request(request_id)


    async def cancel(self, request_id: int) -> Request:
        """Stop a request, including one that is downloading right now.

        Nothing between the state check and `task.cancel()` awaits, so this runs to completion before the
        worker task can take another step: the row cannot slip from FETCHING into VERIFYING underneath the
        check, and the CANCELLED written here cannot be overwritten by a state the task was about to set.
        The task then unwinds from wherever it was suspended -- `SoulseekProvider.download` tells the
        sidecar to stop the transfer and deletes the partial file on its way out."""
        req = self.store.get_request(request_id)
        if req.state not in CANCELLABLE:
            raise ValueError(f"request {request_id} is {req.state.value}; a track can be stopped while it "
                             f"is queued, waiting for a choice, downloading or failed -- not while its "
                             f"file is being checked or filed")
        self._set_state(req, RequestState.CANCELLED)
        task = self._tasks.get(request_id)
        if task is not None:
            task.cancel()
        return self.store.get_request(request_id)


    async def cancel_and_settle(self, request_id: int, timeout: float = 10.0) -> Request:
        """`cancel`, then wait for the task to finish unwinding. For a caller about to delete the row: the
        unwind still writes on its way out (it clears `fetch_source`, and the Soulseek attempt deletes its
        partial file), and deleting under it would race those writes. Bounded, because a sidecar that never
        answers must not hold the route open; past the timeout the writes land on a row that is gone, which
        is a no-op UPDATE rather than an orphan."""
        task = self._tasks.get(request_id)
        req = await self.cancel(request_id)
        if task is not None:
            await asyncio.wait({task}, timeout=timeout)
        return req


    async def retry(self, request_id: int) -> Request:
        """"Try now" on a backoff, "Try again" on a failure.

        Only the three states in `RETRYABLE_STATES` come back. ERROR and NOT_FOUND are the pipeline running
        out of road, and another pass really can end differently. CANCELLED is the owner's own decision, and
        it comes back for exactly that reason (issue #92): pressing Try again on a track you stopped is you
        changing your mind, not the app overturning a verdict. It is kept out of "Retry all" instead -- see
        `SWEEPABLE_STATES` -- so an afternoon of deliberate stops cannot be undone by one press.

        REJECTED is the one failure that stays out. A file was checked, failed and thrown away, so there is
        no attempt to repeat: the way back is `accept_rejection` when the owner has listened and decided the
        recording is fine after all, or submitting the link again for a fresh search.
        """
        req = self.store.get_request(request_id)
        if req.state == RequestState.QUEUED and req.retry_after is not None:
            self.store.update_request(request_id, retry_after=None)
        elif req.state in RETRYABLE_STATES:
            # `lossless_retry`: "Try again" on a failure is the owner asking for another look at the
            # providers, which is exactly what `_lossless_miss_line` tells them the button does. A
            # not-found row can also become actionable after its source is enabled or a catalog changes.
            # Without this flag a request whose last pass did not find a candidate could never reach
            # Soulseek again.
            # Granted whatever the last outcome was, including `verify_failed`: the pick loop starts again
            # at the first survivor, so a press can re-download a file already proven wrong. That is the
            # price of the button meaning what it says -- peers come and go, so the same search an hour
            # later is not the same file list, and the alternative is a dead end the owner cannot leave.
            self._set_state(req, RequestState.QUEUED, attempts=0, retry_after=None,
                            error_message=None, flag_reason=None, failed_stage=None, lossless_retry=1)
        else:
            raise ValueError(f"request {request_id} is {req.state.value}; nothing to retry")
        return self.store.get_request(request_id)


    async def process(self, request_id: int) -> Request | None:
        try:
            await self._process(request_id)
        except asyncio.CancelledError:
            self._on_cancelled(request_id)
            raise    # never swallowed: a cancellation that does not propagate is a task that never stops
        except SourceUnauthorized as e:
            # not the request's fault: keep it queued, attempts untouched, and pause the worker
            self._set_state(self.store.get_request(request_id), RequestState.QUEUED, flag_reason=LOGIN_REQUIRED)
            if self.status.get("telegram_authorized", True):
                self.status["telegram_authorized"] = False
                log.error("%s: %s", LOGIN_REQUIRED, e)
                await self.notifier.send(f"{LOGIN_REQUIRED}: {e}\nRun `flackey login` and restart. "
                                         f"Requests stay queued.")
        except Exception as e:
            log.exception("req#%d failed", request_id)
            self._set_state(self.store.get_request(request_id), RequestState.ERROR, error_message=str(e)[:500])
            await self.notifier.send(f"Error on request {request_id}: {str(e)[:200]}")
        try:
            return self.store.get_request(request_id)
        except KeyError:
            # a terminal state (done/rejected/not_found/error) makes DELETE /api/requests/{id} legal at once;
            # a Remove click can land while we're still awaiting the notifier.send() that follows the commit.
            # The row is already gone and handled - nothing left for us to return.
            log.debug("req#%d removed while finishing", request_id)
            return None


    def _on_cancelled(self, request_id: int) -> None:
        """Two things cancel a task: the owner pressing Stop, and the app closing. `cancel()` has already
        written CANCELLED in the first case, so a row still in an in-flight state here is the second one --
        and it goes back on the queue rather than staying in a state nothing will move it out of."""
        self._progress.pop(request_id, None)
        try:
            req = self.store.get_request(request_id)
        except KeyError:
            return
        if req.state == RequestState.CANCELLED:
            log.info("req#%d stopped", request_id)
            return
        log.info("req#%d interrupted; back on the queue", request_id)
        self._set_state(req, RequestState.QUEUED, flag_reason=None)
