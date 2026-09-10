from __future__ import annotations

import asyncio
import logging
import shutil
import time
import zlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol

import httpx

from .attempts import AttemptRecorder, prune_raw
from .catalog import CatalogUnavailable, best_match
from .config import Settings
from .convert import ConvertError, to_format
from .export import write_playlist
from .fingerprint import FPS, FingerprintResult
from .fingerprint import check as fingerprint_check
from .identify import parse_text
from .library import file_track, final_path, find_duplicate, prune_missing_tracks
from .lossless import Reference, pick, policy_from_settings, reference_for, search_text
from .match import candidate_query, candidate_version, decide, same_version
from .models import (
    MISS_REASON,
    Candidate,
    CatalogTrack,
    Query,
    Request,
    RequestState,
    Track,
    Verdict,
    source_label,
)
from .notify import Button, Notifier
from .source import (
    LosslessError,
    LosslessProvider,
    Source,
    SourceError,
    SourceNotFound,
    SourceTimeout,
    SourceUnauthorized,
    TransferProgress,
)
from .store import Store
from .tag import fetch_artwork, write_tags
from .verify import VerifyError, probe, verify

log = logging.getLogger(__name__)
MAX_ATTEMPTS = 3
RETRY_BACKOFF_S = (30, 120)  # wait before attempt 2, before attempt 3
REVIEW_BUTTONS = 5
CANCELLABLE = {RequestState.QUEUED, RequestState.AWAITING_REVIEW, RequestState.ERROR}
LOGIN_REQUIRED = "Telegram login required"
RETRY_LOSSLESS_OUTCOMES = {"unavailable", "interrupted"}   # spec §5: only these let a request try again
# Outcomes that say nothing about the *next* peer, so the attempt moves on to the next ranked
# survivor. Two kinds sit here: the file was wrong (verify, fingerprint) and the peer would not
# send it (rejected the transfer, or queued us past the first-byte cap). The second kind is the
# one to keep in mind -- a peer refusing an upload is a fact about that peer, never about the
# file, so giving up on the whole attempt there threw away survivors that were still good.
# Not here: transfer_timeout (bytes were flowing, just too slowly -- another peer on the same
# link is unlikely to do better), and unavailable/interrupted, which the request-level retry
# (RETRY_LOSSLESS_OUTCOMES) already handles.
SECOND_PICK_AFTER = {"verify_failed", "fingerprint_failed", "transfer_failed", "first_byte_timeout"}
MAINTENANCE_EVERY_S = 86_400
LOSSLESS_HEALTH_EVERY_S = 60        # once the provider answers "ok"
LOSSLESS_HEALTH_SETTLING_S = 5      # while it is still connecting, or has gone away


class CatalogLike(Protocol):
    async def search(self, query: Query) -> list[CatalogTrack]: ...


def _mmss(seconds: int | None) -> str:
    if seconds is None:
        return "?:??"
    return f"{seconds // 60}:{seconds % 60:02d}"


CATALOG_SOURCE = "beatport"
# Set on the request when a file was accepted without the acoustic fingerprint, so the one guarantee that
# was not met is visible on the row rather than buried in the attempt's JSON.
NO_FINGERPRINT_FLAG = ("filed on the Beatport match alone: no Deezer id, so the recording could not be "
                       "fingerprinted -- only the spectral check ran")


def catalog_candidate(catalog: CatalogTrack) -> Candidate:
    """A stand-in for the Deezer candidate, built from the Beatport record -- the mirror of
    `_fallback_catalog`, which builds a catalog track from a candidate.

    The Telegram bot is a third party that can go silent for hours (it did), and without a candidate the
    whole request used to die at the source search even when Beatport had identified the track and a peer
    was holding the file. Nothing downstream actually needs Deezer: `lossless.reference_for` takes artist,
    title, mix name and duration from the catalog whenever one is present, so every pick rule already runs
    on Beatport data rather than on anything a peer said.

    `deezer_id` stays None, and that is the real cost: `fingerprint.check` returns "skipped" rather than
    running, so identity rests on the duration, title and version rules plus the spectral verify. Callers
    must set `NO_FINGERPRINT_FLAG` on the request. It is never persisted as a candidate row -- a retry
    re-matches Beatport, which is cheap, instead of resuming from a candidate the source never offered.
    """
    return Candidate(source=CATALOG_SOURCE, source_ref=f"{CATALOG_SOURCE}:{catalog.id}",
                     artist=catalog.artist, title=catalog.title, mix_name=catalog.mix_name,
                     duration_s=catalog.duration_s, isrc=catalog.isrc)


def _fallback_catalog(cand: Candidate) -> CatalogTrack:
    # negative so it never collides with a Beatport id; crc32 (not hash()) so it is stable across processes
    fallback_id = -(cand.deezer_id or zlib.crc32(cand.source_ref.encode()) or 1)
    return CatalogTrack(id=fallback_id, isrc=cand.isrc,
                        artist=cand.artist, title=cand.title, mix_name=candidate_version(cand),
                        label="Unknown", genre="Unknown", duration_ms=(cand.duration_s or 0) * 1000 or None)


@dataclass
class LosslessHit:
    path: Path                 # converted file in tmp_dir; to_format always rebuilds (flac included), so
                               # this is never `src` -- the only temp file left
    verdict: Verdict           # cutoff from the FLAC verify; fmt, bitrate, bit depth, sample rate re-probed after conversion
    fingerprint: FingerprintResult
    provider: str
    source_fmt: str
    attempt_id: int


def format_line(verdict: Verdict, source: str, source_fmt: str | None) -> str:
    """The end-user's view of what was downloaded (spec §4): `AIFF 16-bit/44.1 kHz, from FLAC via Soulseek`."""
    if verdict.fmt == "mp3":
        head = f"MP3 {verdict.bitrate_kbps} kbps"
    else:
        head = verdict.fmt.upper()
        if verdict.bit_depth:
            head += f" {verdict.bit_depth}-bit"
        if verdict.sample_rate:
            head += f"/{verdict.sample_rate / 1000:g} kHz"
        if source_fmt and source_fmt != verdict.fmt:
            head += f", from {source_fmt.upper()}"
    return f"{head} via {source_label(source)}"


class Worker:
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

    def _set_state(self, req: Request, state: RequestState, **kw) -> None:
        """Every state change goes through here so a remote log shows the full path of a request. `update_request`
        (not the narrower `store.set_state`) so callers can carry whatever extra fields the transition needs
        (chosen_candidate_id, track_id, attempts, retry_after, ...) alongside the new state."""
        title = (req.query_title or req.raw_text or "")[:80]
        log.debug("req#%d %s -> %s %s", req.id, req.state, state, title)
        self.store.update_request(req.id, state=state, **kw)

    # ---- lifecycle ------------------------------------------------------
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

    async def run_forever(self, poll_s: float = 2.0) -> None:
        await self.startup()
        while self.status.get("telegram_authorized", True):
            await self._maintenance()
            req = self.store.next_queued()
            if req is None:
                await self.refresh_lossless_health()
                await asyncio.sleep(poll_s)
                continue
            await self.process(req.id)
        log.error("worker stopped: %s (sign in from the setup screen in the UI)", LOGIN_REQUIRED)

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
        req = self.store.get_request(request_id)
        if req.state not in CANCELLABLE:
            raise ValueError(f"request {request_id} is {req.state.value}; only queued, awaiting-review "
                             f"or failed requests can be cancelled")
        self._set_state(req, RequestState.CANCELLED)
        return self.store.get_request(request_id)

    async def retry(self, request_id: int) -> Request:
        """"Try now" on a backoff, "Try again" on a failure."""
        req = self.store.get_request(request_id)
        if req.state == RequestState.QUEUED and req.retry_after is not None:
            self.store.update_request(request_id, retry_after=None)
        elif req.state == RequestState.ERROR:
            # `lossless_retry`: "Try again" on a failure is the owner asking for another look at the
            # providers, which is exactly what `_lossless_miss_line` tells them the button does. Without it
            # a request whose attempt ended in a non-retryable outcome could never reach Soulseek again.
            # Granted whatever the last outcome was, including `verify_failed`: the pick loop starts again
            # at the first survivor, so a press can re-download a file already proven wrong. That is the
            # price of the button meaning what it says -- peers come and go, so the same search an hour
            # later is not the same file list, and the alternative is a dead end the owner cannot leave.
            self._set_state(req, RequestState.QUEUED, attempts=0, retry_after=None,
                            error_message=None, flag_reason=None, lossless_retry=1)
        else:
            raise ValueError(f"request {request_id} is {req.state.value}; nothing to retry")
        return self.store.get_request(request_id)

    # ---- pipeline -------------------------------------------------------
    async def process(self, request_id: int) -> Request | None:
        try:
            await self._process(request_id)
        except SourceUnauthorized as e:
            # not the request's fault: keep it queued, attempts untouched, and pause the worker
            self._set_state(self.store.get_request(request_id), RequestState.QUEUED, flag_reason=LOGIN_REQUIRED)
            if self.status.get("telegram_authorized", True):
                self.status["telegram_authorized"] = False
                log.error("%s: %s", LOGIN_REQUIRED, e)
                await self.notifier.send(f"{LOGIN_REQUIRED}: {e}\nRun `crate login` and restart. "
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

    async def _retry_or_fail(self, req: Request, reason: str, *, flag: str | None = None) -> None:
        attempts = req.attempts + 1
        if attempts < MAX_ATTEMPTS:
            wait = RETRY_BACKOFF_S[min(attempts, len(RETRY_BACKOFF_S)) - 1]
            retry_after = (datetime.now(UTC) + timedelta(seconds=wait)).isoformat(timespec="seconds")
            self._set_state(req, RequestState.QUEUED, attempts=attempts, flag_reason=flag or reason,
                            retry_after=retry_after)
            await self.notifier.send(f"Attempt {attempts} failed for {req.raw_text}: {reason}\nRetrying in {wait} s.")
        else:
            self._set_state(req, RequestState.ERROR, attempts=attempts, error_message=reason)
            await self.notifier.send(f"Gave up after {attempts} attempts: {req.raw_text}\n{reason}")

    async def _catalog_for(self, req: Request, cand: Candidate, catalog: CatalogTrack | None) -> CatalogTrack | None:
        """The catalog was matched for the video's title, i.e. for the original. When the candidate we are
        downloading is another version (the owner picked a remix, or the video's length pinned an edit),
        re-match Beatport against that version so the tags describe the recording in the file."""
        if catalog is None or same_version(cand, catalog):
            return catalog
        catalog = best_match(candidate_query(cand), await self.catalog.search(candidate_query(cand)))
        if catalog:
            self.store.upsert_catalog_track(catalog)
        self.store.update_request(req.id, catalog_track_id=catalog.id if catalog else None)
        return catalog

    async def _process(self, request_id: int) -> None:
        req = self.store.get_request(request_id)
        query = req.query()
        if query.artist is None and query.title is None:
            query = parse_text(req.raw_text)
            self.store.update_request(req.id, query_artist=query.artist, query_title=query.title,
                                      query_version=query.version)

        if req.chosen_candidate_id is not None:
            cand = self.store.get_candidate(req.chosen_candidate_id)
            catalog = self.store.get_catalog_track(req.catalog_track_id) if req.catalog_track_id else None
            try:
                catalog = await self._catalog_for(req, cand, catalog)
            except CatalogUnavailable as e:
                await self._retry_or_fail(req, f"Beatport unreachable: {e}", flag="Beatport unreachable, will retry")
                return
        else:
            self._set_state(req, RequestState.IDENTIFYING)
            try:
                catalog = best_match(query, await self.catalog.search(query))
            except CatalogUnavailable as e:
                await self._retry_or_fail(req, f"Beatport unreachable: {e}", flag="Beatport unreachable, will retry")
                return
            if catalog:
                self.store.upsert_catalog_track(catalog)
                self.store.update_request(req.id, catalog_track_id=catalog.id)

            cands: list[Candidate] = []
            if self.settings.source_enabled:
                try:
                    cands = await self.source.search(query)
                except SourceUnauthorized:
                    raise  # handled in process(): subclass of SourceError, so it must be caught before it
                except (SourceNotFound, SourceTimeout, SourceError) as e:
                    if catalog is None:
                        # Neither side identified the track. Without a Beatport record there is no reference
                        # to search a lossless provider with, so this is as far as the request goes.
                        if isinstance(e, SourceNotFound):
                            log.info("req#%d not found at source: %s", req.id, e)
                            self._set_state(req, RequestState.NOT_FOUND, error_message=str(e))
                            await self.notifier.send(f"Not available on Deezer: {req.raw_text}")
                        else:
                            await self._retry_or_fail(req, f"source error: {e}")
                        return
                    # Beatport knows the track, so the request is still actionable: fall through to the
                    # catalog-only path rather than retrying a source that may be down for hours.
                    log.info("req#%d source gave nothing (%s); trying the lossless providers on the "
                             "Beatport match alone", req.id, e)

            if not cands:
                # Either the source is switched off, or it failed with a Beatport match already in hand.
                # `catalog_candidate` explains what this costs; the short version is that the pick rules
                # still run entirely on Beatport data and only the fingerprint is lost.
                if catalog is None:
                    # Nothing identified the track: the source offered nothing and Beatport has no match.
                    # Terminal, not a retry -- both halves are the same on the next pass, so backing off
                    # and asking again only delays the same answer. This is where `decide` used to land a
                    # request with an empty candidate list, and it keeps landing there.
                    why = ("no match on Beatport, and the Deezer bot is switched off"
                           if not self.settings.source_enabled else
                           "neither Deezer nor Beatport has a match for it")
                    self._set_state(req, RequestState.NOT_FOUND, error_message=f"could not identify this track: {why}")
                    await self.notifier.send(f"Could not identify: {req.raw_text}")
                    return
                if not self._lossless_allowed(req):
                    # Beatport knows the track, so it exists -- there is just no route to a file right now.
                    # Say which route is missing rather than blaming the source for a setting, the same way
                    # `_lossless_miss_line` stays quiet when the provider is simply off.
                    await self._retry_or_fail(req, "no way to fetch this track: the source is unavailable, and "
                                                   + ("Soulseek is switched off"
                                                      if not (self.providers and self.settings.lossless_enabled)
                                                      else "Soulseek has already looked and found nothing. Use "
                                                           "Try again in the app to search once more"))
                    return
                self.store.update_request(req.id, flag_reason=NO_FINGERPRINT_FLAG)
                await self._fetch_verify_file(req, catalog_candidate(catalog), catalog)
                return

            decision = decide(query, cands, catalog)
            saved = self.store.add_candidates(req.id, cands)
            self.store.update_request(req.id, confidence=decision.chosen.score if decision.chosen else None)
            if decision.chosen is None:
                log.info("req#%d: no acceptable candidate among %d: %s", req.id, len(cands), decision.reason)
                self._set_state(req, RequestState.NOT_FOUND, error_message=decision.reason)
                await self.notifier.send(f"Not available on Deezer: {req.raw_text}")
                return
            # `decide` returns one of the objects in `cands`; match by identity, not by source_ref
            # (the bot can list the same Deezer id twice)
            chosen = saved[next(i for i, c in enumerate(cands) if c is decision.chosen)]

            dup = find_duplicate(self.store, catalog, chosen)
            if dup:
                await self._mark_duplicate(req, dup.id, dup.path)
                return

            if not decision.auto:
                self._set_state(req, RequestState.AWAITING_REVIEW, flag_reason=decision.reason,
                                chosen_candidate_id=chosen.id)
                await self._ask_review(req, saved, decision.reason)
                return
            # persist the choice so a fetch failure resumes here instead of searching (and saving candidates) again
            self.store.update_request(req.id, chosen_candidate_id=chosen.id)
            cand = chosen
            try:
                catalog = await self._catalog_for(req, cand, catalog)
            except CatalogUnavailable as e:
                await self._retry_or_fail(req, f"Beatport unreachable: {e}", flag="Beatport unreachable, will retry")
                return
            if catalog is None:
                # the pinned edit/remix has no Beatport record: only the owner may file it on Deezer's word
                reason = f"the {candidate_version(cand)} is not on Beatport; information cannot be verified"
                self._set_state(req, RequestState.AWAITING_REVIEW, flag_reason=reason)
                await self._ask_review(req, saved, reason)
                return

        await self._fetch_verify_file(req, cand, catalog)

    async def _mark_duplicate(self, req: Request, track_id: int | None, path: Path) -> None:
        self._set_state(req, RequestState.DUPLICATE, track_id=track_id)
        if req.playlist_id is not None and track_id is not None:
            self.store.add_playlist_track(req.playlist_id, track_id, req.playlist_position or 0)
        await self.notifier.send(f"Already in library: {path}")

    async def _ask_review(self, req: Request, cands: list[Candidate], reason: str) -> None:
        head = f"Review needed: {req.raw_text}"
        if req.query_duration_s:
            head += f" · Video: {_mmss(req.query_duration_s)}"  # so a wrong-length pick is visible at a glance
        lines = [head]
        buttons: list[Button] = []
        # numbered 1..n in *display* order (best first); c.rank is the bot's menu order and would jump around
        for n, c in enumerate(sorted(cands, key=lambda c: (-(c.score or 0), c.rank))[:REVIEW_BUTTONS], start=1):
            lines.append(f"{n}. {c.artist} – {c.title} ({candidate_version(c)}) · {_mmss(c.duration_s)} · {c.score or 0}%")
            buttons.append(Button(str(n), f"pick:{req.id}:{c.id}"))
        lines.append(f"Reason: {reason}")
        buttons.append(Button("Cancel", f"cancel:{req.id}"))
        await self.notifier.send("\n".join(lines), buttons)

    async def _fetch_verify_file(self, req: Request, cand: Candidate, catalog: CatalogTrack | None) -> None:
        dup = find_duplicate(self.store, catalog, cand)  # again: a reviewed request may have waited for hours
        if dup:
            await self._mark_duplicate(req, dup.id, dup.path)
            return
        self._set_state(req, RequestState.FETCHING)
        hit = None
        if self._lossless_allowed(req):
            if req.lossless_retry:
                self.store.update_request(req.id, lossless_retry=0)   # one pass, spent now
            hit = await self._try_lossless(req, cand, catalog)
        if hit is None and cand.source == CATALOG_SOURCE:
            # A Beatport stand-in has no source_ref the bot would recognise, so there is no Deezer copy to
            # fall back to -- the providers were the only route and they came up empty. Say so plainly
            # instead of handing the source a candidate it never issued.
            await self._retry_or_fail(req, "no lossless copy found, and the source is unavailable for the "
                                           "lossy fallback")
            return
        if hit is None:
            self.store.update_request(req.id, fetch_source=self.source.name)
            try:
                tmp = await self.source.fetch(cand, self.settings.tmp_dir)
            except SourceUnauthorized:
                self.store.update_request(req.id, fetch_source=None)
                raise
            except (SourceTimeout, SourceError) as e:
                self.store.update_request(req.id, fetch_source=None)
                await self._retry_or_fail(req, f"source error: {e}")
                return
        else:
            tmp = hit.path
        try:
            await self._verify_and_file(req, cand, catalog, tmp, hit)
        finally:
            tmp.unlink(missing_ok=True)  # gone already when file_track moved it; garbage in every other outcome
            self.store.update_request(req.id, fetch_source=None)

    def _lossless_allowed(self, req: Request) -> bool:
        if not self.providers or not self.settings.lossless_enabled:
            return False
        last = self.store.get_attempt_for_request(req.id)
        # Spec §5 stops the *worker* going back to a provider on its own after a definitive miss. It does
        # not bind the owner: `retry()` grants `lossless_retry` on a failed request, which is the button
        # `_lossless_miss_line` points them at. `upgrade()` skips this check outright for the same reason.
        return last is None or last.outcome in RETRY_LOSSLESS_OUTCOMES or bool(req.lossless_retry)

    async def _verify_and_file(self, req: Request, cand: Candidate, catalog: CatalogTrack | None, tmp: Path,
                               hit: LosslessHit | None = None) -> None:
        if hit is None:
            self._set_state(req, RequestState.VERIFYING)
            # ffprobe plus two ffmpeg passes take seconds on a 7-minute file: keep the event loop
            # (inbox bot, API) free
            verdict = await asyncio.to_thread(verify, tmp, self.settings.spectrogram_dir, f"req{req.id}-{tmp.stem}")
            if not verdict.passed:
                self.store.add_rejection(req.id, verdict.reason, verdict.bitrate_kbps, verdict.cutoff_hz,
                                         verdict.spectrogram_path)
                self._set_state(req, RequestState.REJECTED)
                log.info("req#%d rejected: %s", req.id, verdict.reason)
                await self.notifier.send(f"Rejected: {cand.artist} – {cand.title}\n{verdict.reason}")
                return
        else:
            verdict = hit.verdict                     # verified and fingerprinted inside the attempt
        source = hit.provider if hit else self.source.name
        source_fmt = hit.source_fmt if hit else None

        self._set_state(req, RequestState.FILING)
        if catalog is None:
            catalog = _fallback_catalog(cand)
            self.store.upsert_catalog_track(catalog)
            self.store.update_request(req.id, catalog_track_id=catalog.id)
        artwork = await self.artwork_fetch(catalog.artwork_url) if catalog.artwork_url else None
        await asyncio.to_thread(write_tags, tmp, catalog, verdict, artwork, source=source)
        dest = final_path(self.settings.library_root, catalog, tmp.suffix.lstrip("."))
        if dest.exists():
            existing = self.store.find_track_by_path(dest)  # None if the file was put there by hand
            await self._mark_duplicate(req, existing.id if existing else None, dest)
            return
        file_track(tmp, dest)
        log.info("req#%d filed %s -> %s", req.id, f"{catalog.artist} – {catalog.title}",
                dest.relative_to(self.settings.library_root))
        track_id = self.store.add_track(
            path=dest, fmt=verdict.fmt, bitrate_kbps=verdict.bitrate_kbps, cutoff_hz=verdict.cutoff_hz,
            file_size=dest.stat().st_size, artist=catalog.artist, title=catalog.title, mix_name=catalog.mix_name,
            duration_s=catalog.duration_s or cand.duration_s, isrc=catalog.isrc or cand.isrc,
            catalog_track_id=catalog.id, request_id=req.id, spectrogram_path=verdict.spectrogram_path,
            source=source, source_fmt=source_fmt, bit_depth=verdict.bit_depth, sample_rate=verdict.sample_rate)
        if hit:
            self._record_evidence(track_id, hit, cand)
        if req.playlist_id is not None:
            self.store.add_playlist_track(req.playlist_id, track_id, req.playlist_position or 0)
            write_playlist(self.store, req.playlist_id, self.settings.library_root)
        self._set_state(req, RequestState.DONE, track_id=track_id)
        conf = self.store.get_request(req.id).confidence
        parts = [f"Done: {catalog.artist} – {catalog.title} ({catalog.mix_name})", _mmss(catalog.duration_s or cand.duration_s),
                 format_line(verdict, source, source_fmt), f"content to {verdict.cutoff_hz / 1000:.1f} kHz",
                 f"{catalog.genre} / {catalog.label}"]
        if conf is not None:
            parts.append(f"{conf}%")
        msg = " · ".join(parts)
        miss = self._lossless_miss_line(req)
        if miss:
            msg += f"\n{miss}"
        await self.notifier.send(msg)

    def _lossless_miss_line(self, req: Request) -> str | None:
        """The owner asked to be told, not just shown, when a track lands on the lossy copy. Only when a
        lossless attempt actually ran and missed: with the provider off or absent there is nothing to
        report, and saying "no lossless copy" then would blame Soulseek for a setting.

        The outcome is the whole test. A successful hit always finishes its attempt as "filed" before
        `_try_lossless` returns it, so an extra `hit is None` guard at the call site would be a branch no
        input can reach - and this way the line stays right however the file was obtained."""
        attempt = self.store.get_attempt_for_request(req.id)
        if attempt is None or attempt.outcome in (None, "filed"):
            return None
        why = MISS_REASON.get(attempt.outcome, f"the lossless attempt ended in {attempt.outcome}")
        tail = ("It will try again on its own." if attempt.outcome in RETRY_LOSSLESS_OUTCOMES
                else "Use Try again in the app to search Soulseek once more.")
        return f"No lossless copy this time — {why}. {tail}"

    # ---- upgrading a track that was filed on the lossy copy -------------
    async def upgrade(self, track_id: int) -> str:
        """Search Soulseek again for a track already filed from Deezer, and if a lossless copy turns up,
        put it in place of the lossy one. Returns a sentence for the owner either way.

        Its own entry point on purpose. `retry()` only accepts a queued-with-backoff or failed request, and
        this request is DONE; `_lossless_allowed` refuses a second attempt after anything but
        unavailable/interrupted, which is exactly the L.S.D. case (transfer_failed) this exists for. Going
        through `process()` would also re-run identify and match, and re-fetch from Deezer on a miss --
        pointless work whose only effect would be to replace a good file with an identical one.

        Never automatic. Re-pathing a filed track breaks whatever Rekordbox (or any other player) has
        pointing at the old name, so the owner asks for it per track and knows what they asked for.
        """
        t = self.store.get_track(track_id)
        if t.source_fmt:
            raise ValueError(f"{t.artist} - {t.title} is already the lossless copy")
        if not self.providers or not self.settings.lossless_enabled:
            raise ValueError("Soulseek is off, so there is nothing to upgrade from")
        if t.request_id is None:
            raise ValueError("this track has no request to search from")
        try:
            req = self.store.get_request(t.request_id)
        except KeyError:
            raise ValueError("the request this track came from has been removed") from None
        if req.chosen_candidate_id is None:
            raise ValueError("this track was filed without a recorded choice, so there is nothing to match")
        cand = self.store.get_candidate(req.chosen_candidate_id)
        catalog = self.store.get_catalog_track(req.catalog_track_id) if req.catalog_track_id else None

        hit = await self._try_lossless(req, cand, catalog)
        if hit is None:
            miss = self._lossless_miss_line(req)
            return miss or "No lossless copy turned up this time."
        try:
            return await self._replace_with(t, req, cand, catalog, hit)
        finally:
            hit.path.unlink(missing_ok=True)   # spec §13: every outcome leaves tmp_dir empty

    async def _replace_with(self, t: Track, req: Request, cand: Candidate, catalog: CatalogTrack | None,
                            hit: LosslessHit) -> str:
        """File the new copy, repoint the row, rewrite the playlists, and only then delete the old file.

        That order is the whole safety argument: every step before the unlink is recoverable, and the
        library is never without a playable file for this track. The row keeps its id, so playlist
        membership and the fingerprint evidence follow it across the swap for free -- the .m3u8 files on
        disk are the only things holding the old path, and they are rewritten from the row.
        """
        if catalog is None:
            catalog = _fallback_catalog(cand)
            self.store.upsert_catalog_track(catalog)
        artwork = await self.artwork_fetch(catalog.artwork_url) if catalog.artwork_url else None
        await asyncio.to_thread(write_tags, hit.path, catalog, hit.verdict, artwork, source=hit.provider)
        dest = final_path(self.settings.library_root, catalog, hit.path.suffix.lstrip("."))
        if dest != t.path and self.store.find_track_by_path(dest) is not None:
            raise ValueError(f"another track is already filed at {dest.name}")

        old, old_spectrogram = t.path, t.spectrogram_path
        file_track(hit.path, dest)
        self.store.update_track(
            t.id, path=dest, fmt=hit.verdict.fmt, bitrate_kbps=hit.verdict.bitrate_kbps,
            cutoff_hz=hit.verdict.cutoff_hz, file_size=dest.stat().st_size,
            verified_at=datetime.now(UTC).isoformat(timespec="seconds"),
            spectrogram_path=str(hit.verdict.spectrogram_path) if hit.verdict.spectrogram_path else None,
            source=hit.provider, source_fmt=hit.source_fmt, bit_depth=hit.verdict.bit_depth,
            sample_rate=hit.verdict.sample_rate)
        self.store.clear_evidence(t.id)          # the old evidence describes a file that no longer exists
        self._record_evidence(t.id, hit, cand)
        for pid in self.store.playlist_ids_for_track(t.id):
            write_playlist(self.store, pid, self.settings.library_root)
        if old != dest:
            # Last, and only now: everything above can be redone from the row, this cannot be undone.
            old.unlink(missing_ok=True)
        if old_spectrogram and old_spectrogram != hit.verdict.spectrogram_path:
            old_spectrogram.unlink(missing_ok=True)
        line = format_line(hit.verdict, hit.provider, hit.source_fmt)
        log.info("upgraded track#%d %s -> %s", t.id, old.name, dest.name)
        await self.notifier.send(f"Upgraded: {catalog.artist} - {catalog.title} - {line}")
        return f"Upgraded to {line}."

    def _record_evidence(self, track_id: int, hit: LosslessHit, cand: Candidate) -> None:
        fp = hit.fingerprint
        self.store.add_evidence(track_id, "source", {"provider": hit.provider, "source_fmt": hit.source_fmt,
                                                     "attempt_id": hit.attempt_id})
        self.store.add_evidence(track_id, "recording_match", {"status": fp.status, "score": fp.score, "offset_s": fp.offset_s,
                                                              "reference": f"deezer:{cand.deezer_id}", "reason": fp.reason})
        if fp.track:
            self.store.add_evidence(track_id, "fingerprint", {"frames": fp.track, "fps": FPS})

    # ---- lossless ---------------------------------------------------------
    async def _try_lossless(self, req: Request, cand: Candidate, catalog: CatalogTrack | None) -> LosslessHit | None:
        """Ask each provider in turn for a verified, fingerprinted, converted lossless file. Never raises;
        every miss is an attempt row with an outcome (spec §5, §11)."""
        try:
            ref = reference_for(catalog, cand)
            query = search_text(ref)
            policy = policy_from_settings(self.settings)
        except Exception:  # nothing was written yet; the safe fallback is Deezer, no attempt row to close
            log.exception("req#%d could not build a lossless reference/policy", req.id)
            return None
        for provider in self.providers:
            rec = None
            try:
                rec = AttemptRecorder(self.store, self.settings.lossless_raw_dir, req.id, provider.name, query,
                                      clock=self.clock)
                self.store.update_request(req.id, fetch_source=provider.name)
                hit = await self._attempt(provider, rec, req, ref, policy)
            except Exception as e:  # the worker must survive any bug in the lossless path
                log.exception("req#%d lossless attempt %s crashed", req.id, rec.id if rec else "?")
                if rec is not None:
                    try:
                        rec.event("error", type=type(e).__name__, message=str(e)[:300])
                        rec.finish("transfer_failed")
                    except Exception:  # the recovery write can fail too; the row may stay NULL, we still fall back
                        log.exception("req#%d could not record the failed lossless attempt", req.id)
                hit = None
            finally:
                # However this attempt ended, the bar it was driving is over. Leaving the last position
                # published would strand a full-looking bar on a row that has moved on.
                if self.status.get("fetch_progress") is not None:
                    self.status["fetch_progress"] = None
            if hit is not None:
                return hit
        return None

    async def _attempt(self, provider: LosslessProvider, rec: AttemptRecorder, req: Request, ref: Reference,
                       policy) -> LosslessHit | None:
        s = self.settings
        health = await provider.health()
        self.status["lossless_provider"] = {"name": provider.name, **health}
        if health.get("status") != "ok":
            rec.event("unavailable", status=health.get("status"))
            rec.finish("unavailable")
            return None
        rec.event("search_started", query=rec.query)
        try:
            files = await provider.search(rec.query, wait_s=s.lossless_search_wait_s, on_raw=rec.raw)
        except LosslessError as e:
            rec.event("search_failed", error=str(e))
            rec.finish(e.outcome)
            return None
        rec.event("search_completed", files=len(files), peers=len({f.username for f in files}))
        report = pick(files, ref, policy)
        self.store.update_attempt(rec.id, report=report.to_dict())
        rec.event("pick", summary=report.summary)
        if report.chosen is None:
            rec.finish("no_pick")
            return None
        budget_s = s.lossless_search_wait_s + s.lossless_first_byte_s + s.lossless_transfer_s
        outcome = "no_pick"
        for n, file in enumerate(report.survivors[:s.lossless_max_picks], start=1):
            if n > 1 and rec.elapsed_ms() / 1000 > budget_s - s.lossless_first_byte_s:
                rec.event("budget_exhausted", pick=n)
                break
            hit, outcome = await self._download_and_check(provider, rec, req, ref, file, n)
            if hit is not None:
                try:
                    rec.finish("filed")
                except Exception:
                    hit.path.unlink(missing_ok=True)  # spec §13: every outcome leaves tmp_dir empty
                    raise
                return hit
            if outcome not in SECOND_PICK_AFTER:
                break
        rec.finish(outcome)
        return None

    async def _download_and_check(self, provider: LosslessProvider, rec: AttemptRecorder, req: Request, ref: Reference,
                                  file, n: int) -> tuple[LosslessHit | None, str]:
        s = self.settings
        rec.event("enqueue", pick=n, peer=file.username, file=file.name, size=file.size)
        seen = {"state": None, "first_byte": False}

        def progress(p: TransferProgress) -> None:
            # Live, for the row's progress bar. The timeline below records *changes* (that is what a
            # timeline is for); this is the current position, republished on every poll, and cleared in
            # `_try_lossless`'s finally so a bar never outlives the transfer it belongs to.
            self.status["fetch_progress"] = {
                "request_id": req.id, "bytes": p.bytes, "size": p.size, "peer": file.username,
                "pct": round(100 * p.bytes / p.size) if p.size else 0,
                "speed_bps": p.speed_bps, "pick": n, "state": p.state}
            if p.state != seen["state"]:
                seen["state"] = p.state
                rec.event("transfer_state", state=p.state, pct=round(100 * p.bytes / max(p.size, 1)),
                          speed_kbps=round(p.speed_bps / 1000))
            if p.first_byte_ms is not None and not seen["first_byte"]:
                seen["first_byte"] = True
                rec.event("first_byte", ms=p.first_byte_ms)
                self.store.update_attempt(rec.id, first_byte_ms=p.first_byte_ms)

        try:
            landed = await provider.download(file, first_byte_s=s.lossless_first_byte_s, total_s=s.lossless_transfer_s,
                                             poll_s=s.lossless_poll_s, on_progress=progress, on_raw=rec.raw)
        except LosslessError as e:
            rec.event("transfer_failed", error=str(e))
            return None, e.outcome
        rec.event("completed", ms=rec.elapsed_ms(), bytes=landed.stat().st_size)
        s.tmp_dir.mkdir(parents=True, exist_ok=True)
        tmp = s.tmp_dir / f"req{req.id}-lossless.{file.extension}"    # never the peer's file name (spec §16.2)
        try:
            shutil.move(str(landed), str(tmp))
        except OSError as e:
            rec.event("move_failed", error=str(e))
            landed.unlink(missing_ok=True)
            return None, "transfer_failed"
        hit, outcome = await self._check_and_convert(rec, req, ref, file, tmp)
        if hit is None:
            tmp.unlink(missing_ok=True)
        return hit, outcome

    async def _check_and_convert(self, rec: AttemptRecorder, req: Request, ref: Reference, file,
                                 tmp: Path) -> tuple[LosslessHit | None, str]:
        s = self.settings
        try:
            verdict = await asyncio.to_thread(verify, tmp, s.spectrogram_dir, f"req{req.id}-lossless-{rec.id}")
        except VerifyError as e:
            rec.event("verify_failed", error=str(e))
            return None, "verify_failed"
        if verdict.spectrogram_path:
            self.store.update_attempt(rec.id, spectrogram_path=str(verdict.spectrogram_path))
        rec.event("verify", passed=verdict.passed, cutoff_hz=verdict.cutoff_hz, reason=verdict.reason)
        if not verdict.passed:
            return None, "verify_failed"
        fp = await fingerprint_check(tmp, ref.deezer_id, self.http, minimum=s.lossless_fingerprint_min, tmp_dir=s.tmp_dir)
        rec.raw("fingerprint", {"preview": fp.preview, "track": fp.track})
        self.store.update_attempt(rec.id, fingerprint=fp.to_dict())
        rec.event("fingerprint", status=fp.status, score=fp.score, offset_s=fp.offset_s, reason=fp.reason)
        if fp.status == "failed":
            return None, "fingerprint_failed"
        t0 = self.clock()
        out: Path | None = None
        try:
            out = await asyncio.to_thread(to_format, tmp, s.lossless_filing_format, verdict.bit_depth)
            # to_format never returns `src` unchanged, so `tmp` is always the second file here and must
            # always be cleaned up -- at most one temp file from here on (spec §9)
            tmp.unlink(missing_ok=True)
            pr = await asyncio.to_thread(probe, out)
            verdict = replace(verdict, fmt=pr.fmt, bitrate_kbps=pr.bitrate_kbps, bit_depth=pr.bit_depth,
                              sample_rate=pr.sample_rate)
        except (ConvertError, VerifyError) as e:
            rec.event("convert_failed", error=str(e))
            # to_format cleans up its own output on an ffmpeg failure; the only leak possible here is a
            # successful conversion whose probe() then raised -- unlink `out` itself, not a guessed name
            # (the flac branch's output stem does not match `tmp.with_suffix(...)`)
            if out is not None:
                out.unlink(missing_ok=True)
            return None, "convert_failed"
        rec.event("convert", fmt=verdict.fmt, ms=int((self.clock() - t0) * 1000))
        return LosslessHit(out, verdict, fp, rec.provider, file.extension, rec.id), "filed"
