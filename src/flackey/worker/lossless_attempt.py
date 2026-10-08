"""One request's lossless path: each spelling, each provider, each ranked pick, recorded
as an attempt row."""
from __future__ import annotations

import asyncio
import logging
import shutil
from dataclasses import replace
from pathlib import Path

from ..attempts import AttemptRecorder
from ..convert import ConvertError, to_format
from ..lossless import (
    Reference,
    pick,
    policy_from_settings,
    reference_for,
    search_text,
    transfer_ceiling_s,
)
from ..models import Candidate, CatalogTrack, Request, norm
from ..source import LosslessError, LosslessProvider, TransferProgress
from ..verify import VerifyError, probe, verify
from .policy import SECOND_PICK_AFTER, SECOND_SEARCH_AFTER
from .records import (
    CATALOG_SOURCE,
    QUERY_SOURCE,
    Acoustic,
    LosslessHit,
    catalog_candidate,
    query_candidate,
)

log = logging.getLogger(__name__)
# Kept free on top of what a pick needs, so filing it never fills the disk the library and database are on.
DISK_MARGIN_BYTES = 256 * 1024 * 1024
# How far the length a file probes at may sit from the length its peer advertised. Encoders round, and a
# peer's client reports whole seconds; a gap past this is a different file than the one the caps passed.
DURATION_SLACK_S = 10
DURATION_SLACK_FRACTION = 0.05


def free_bytes(path: Path) -> int:
    """Free space on the volume `path` is on, or will be on once it is created."""
    for p in (path, *path.parents):
        if p.exists():
            return shutil.disk_usage(p).free
    return 0


# How much shorter than the download a lossless conversion may come out: container rounding only.
CONVERT_SLACK_S = 1.0


def duration_agrees(advertised_s: float | None, probed_s: float) -> bool:
    if advertised_s is None:
        return False
    return abs(probed_s - advertised_s) <= max(DURATION_SLACK_S, advertised_s * DURATION_SLACK_FRACTION)


class LosslessMixin:
    """Part of `Worker`, which supplies the state these methods read."""

    def _reference(self, req: Request, cand: Candidate, catalog: CatalogTrack | None) -> Reference:
        """`from_query` says the words in this reference are the request's own. That is a fact about where
        `reference_for` read them, not about which candidate was passed: a query candidate with a Beatport
        match still searches and tags from the record, because the catalog wins inside `reference_for`."""
        ref = reference_for(catalog, cand, req.query_duration_s)
        return replace(ref, from_query=True) if cand.source == QUERY_SOURCE and catalog is None else ref


    def _search_references(self, req: Request, cand: Candidate, catalog: CatalogTrack | None) -> list[Reference]:
        """What to type into the network, in order: Beatport's spelling (clean, and for a channel-name artist
        such as `ShpongleMusic` the only text that finds anything), then the chosen record's, then the
        request's own words -- each only when its tokens differ from what came before. A wrong Beatport
        record redirects the first search to a different track (`Power Of Celtic` was searched as `The Power
        Of The Dark Side`); the fingerprint rejecting that search is what lets the next spelling run.

        Every reference keeps `_reference`'s meaning of `from_query` -- these words are the request's own --
        so whichever one comes back attached to the hit says where the tags belong."""
        refs: list[Reference] = []
        if catalog is not None:
            refs.append(self._reference(req, catalog_candidate(catalog), catalog))
        if cand.source not in (CATALOG_SOURCE, QUERY_SOURCE):
            refs.append(self._reference(req, cand, catalog))
        query = req.query()
        if query.artist and query.title:
            refs.append(self._reference(req, query_candidate(query, req.id), None))
        seen: list[set[str]] = []
        out: list[Reference] = []
        for ref in refs:
            tokens = set(norm(search_text(ref)).split())
            if tokens in seen:
                continue
            seen.append(tokens)
            out.append(ref)
        # A bare-artist request on the catalog-less path leaves `refs` empty; the candidate that got this
        # far is still worth one search, which is exactly what ran before the sequence existed.
        return out or [self._reference(req, cand, catalog)]


    async def _try_lossless(self, req: Request, cand: Candidate, catalog: CatalogTrack | None,
                            acoustic: Acoustic) -> LosslessHit | None:
        """Ask each provider in turn for a verified, fingerprinted, converted lossless file, one spelling at
        a time. Never raises; every miss is an attempt row with an outcome (spec §5, §11)."""
        try:
            refs = self._search_references(req, cand, catalog)
            policy = policy_from_settings(self.settings)
        except Exception:  # nothing was written yet; the safe fallback is Deezer, no attempt row to close
            log.exception("req#%d could not build a lossless reference/policy", req.id)
            return None
        for ref in refs:
            query = search_text(ref)
            outcome: str | None = None
            for provider in self.providers:
                rec = None
                try:
                    rec = AttemptRecorder(self.store, self.settings.lossless_raw_dir, req.id, provider.name,
                                          query, clock=self.clock)
                    self.store.update_request(req.id, fetch_source=provider.name)
                    hit = await self._attempt(provider, rec, req, ref, policy, acoustic)
                except Exception as e:  # the worker must survive any bug in the lossless path
                    log.exception("req#%d lossless attempt %s crashed", req.id, rec.id if rec else "?")
                    if rec is not None:
                        # Two writes, two tries: the event is a detail, the outcome is what the next pass
                        # reads back, so a failed event must not skip closing the row.
                        try:
                            rec.event("error", type=type(e).__name__, message=str(e)[:300])
                        except Exception:
                            log.exception("req#%d could not record the lossless attempt's error", req.id)
                        try:
                            rec.finish("transfer_failed")
                        except Exception:  # the recovery write can fail too; the row may stay NULL, we still fall back
                            log.exception("req#%d could not record the failed lossless attempt", req.id)
                    hit = None
                finally:
                    # However this attempt ended, the bar it was driving is over. Leaving the last position
                    # published would strand a full-looking bar on a row that has moved on. Only this
                    # request's bar: the other tracks are still downloading.
                    self._publish_progress(req.id, None)
                if hit is not None:
                    return hit
                if rec is not None:
                    outcome = self.store.get_attempt(rec.id).outcome
            # Only an answer about *this spelling* earns the next one. `outcome` is the last provider's,
            # which is the one `_no_route` and `_lossless_miss_line` will read back.
            if outcome not in SECOND_SEARCH_AFTER:
                break
        return None


    async def _attempt(self, provider: LosslessProvider, rec: AttemptRecorder, req: Request, ref: Reference,
                       policy, acoustic: Acoustic) -> LosslessHit | None:
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
        outcome = "no_pick"
        for n, file in enumerate(report.survivors[:s.lossless_max_picks], start=1):
            # What one pick may cost, for this file: the queue wait is left out because the pick about to
            # start still has its own, and charging it twice is what would close this gate the moment the
            # ceiling grew with the file.
            budget_s = s.lossless_search_wait_s + s.lossless_first_byte_s + transfer_ceiling_s(file.size, s)
            if n > 1 and rec.elapsed_ms() / 1000 > budget_s:
                rec.event("budget_exhausted", pick=n)
                break
            # The download lands in slskd's folder, is moved to tmp_dir and converted next to itself there:
            # room for all three on each, so either folder may share the other's disk. A full disk is no
            # fact about this peer, so no later pick is tried either.
            need = 3 * file.size + DISK_MARGIN_BYTES
            short = [str(d) for d in (s.slskd_downloads, s.tmp_dir) if free_bytes(d) < need]
            if short:
                rec.event("disk_full", pick=n, need_bytes=need, folders=short)
                outcome = "transfer_failed"
                break
            hit, outcome = await self._download_and_check(provider, rec, req, ref, file, n, acoustic)
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
                                  file, n: int, acoustic: Acoustic) -> tuple[LosslessHit | None, str]:
        s = self.settings
        rec.event("enqueue", pick=n, peer=file.username, file=file.name, size=file.size)
        seen = {"state": None, "first_byte": False}

        def progress(p: TransferProgress) -> None:
            # Live, for the row's progress bar. The timeline below records *changes* (that is what a
            # timeline is for); this is the current position, republished on every poll, and cleared in
            # `_try_lossless`'s finally so a bar never outlives the transfer it belongs to.
            self._publish_progress(req.id, {
                "request_id": req.id, "bytes": p.bytes, "size": p.size, "peer": file.username,
                "pct": round(100 * p.bytes / p.size) if p.size else 0,
                "speed_bps": p.speed_bps, "pick": n, "state": p.state})
            if p.state != seen["state"]:
                seen["state"] = p.state
                rec.event("transfer_state", state=p.state, pct=round(100 * p.bytes / max(p.size, 1)),
                          speed_kbps=round(p.speed_bps / 1000))
            if p.first_byte_ms is not None and not seen["first_byte"]:
                seen["first_byte"] = True
                rec.event("first_byte", ms=p.first_byte_ms)
                self.store.update_attempt(rec.id, first_byte_ms=p.first_byte_ms)

        try:
            landed = await provider.download(file, first_byte_s=s.lossless_first_byte_s,
                                             total_s=transfer_ceiling_s(file.size, s), poll_s=s.lossless_poll_s,
                                             queue_wait_s=s.lossless_queue_wait_s, stall_s=s.lossless_stall_s,
                                             on_progress=progress, on_raw=rec.raw)
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
        hit, outcome = await self._check_and_convert(rec, req, ref, file, tmp, acoustic)
        if hit is None:
            tmp.unlink(missing_ok=True)
        return hit, outcome


    async def _check_and_convert(self, rec: AttemptRecorder, req: Request, ref: Reference, file,
                                 tmp: Path, acoustic: Acoustic) -> tuple[LosslessHit | None, str]:
        s = self.settings
        self._publish_phase(req.id, "verifying")
        try:
            async with self._cpu:
                probed = await asyncio.to_thread(probe, tmp)
                if not duration_agrees(file.length_s, probed.duration_s):
                    # The caps in lossless.py judged the peer's word; this is the file that came.
                    rec.event("duration_mismatch", advertised_s=file.length_s, probed_s=round(probed.duration_s, 1))
                    return None, "verify_failed"
                verdict = await asyncio.to_thread(verify, tmp, s.spectrogram_dir, f"req{req.id}-lossless-{rec.id}")
        except VerifyError as e:
            rec.event("verify_failed", error=str(e))
            return None, "verify_failed"
        if verdict.spectrogram_path:
            self.store.update_attempt(rec.id, spectrogram_path=str(verdict.spectrogram_path))
        rec.event("verify", passed=verdict.passed, cutoff_hz=verdict.cutoff_hz, reason=verdict.reason)
        if not verdict.passed:
            return None, "verify_failed"
        self._publish_phase(req.id, "fingerprinting")
        fp = await self._fingerprint(tmp, acoustic.reference, missing=acoustic.missing)
        rec.raw("fingerprint", {"preview": fp.preview, "track": fp.track})
        self.store.update_attempt(rec.id, fingerprint=fp.to_dict())
        rec.event("fingerprint", status=fp.status, score=fp.score, offset_s=fp.offset_s, reason=fp.reason)
        if fp.status == "failed":
            return None, "fingerprint_failed"
        if fp.status == "skipped":
            # Nothing acoustic vouched for the file. Not a fact about this peer, so not SECOND_PICK_AFTER:
            # the reference is missing for every survivor alike (issue #68).
            return None, "fingerprint_unavailable"
        self._publish_phase(req.id, "converting")
        t0 = self.clock()
        out: Path | None = None
        try:
            async with self._cpu:
                out = await asyncio.to_thread(to_format, tmp, s.lossless_filing_format, verdict.bit_depth)
                # to_format never returns `src` unchanged, so `tmp` is always the second file here and must
                # always be cleaned up -- at most one temp file from here on (spec §9)
                tmp.unlink(missing_ok=True)
                pr = await asyncio.to_thread(probe, out)
            if pr.duration_s < probed.duration_s - CONVERT_SLACK_S:
                # ffmpeg exits 0 when its `-t` ceiling ends the output early: a cut-off track, not a copy.
                raise ConvertError(f"the converted file is {pr.duration_s:.1f} s, shorter than the "
                                   f"{probed.duration_s:.1f} s download")
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
        return LosslessHit(out, verdict, fp, rec.provider, file.extension, rec.id, ref), "filed"


    def _publish_phase(self, request_id: int, phase: str) -> None:
        """The bytes have landed but the file is not accepted yet. verify, fingerprint and convert all run
        with the row still FETCHING -- that is the state `cancel` still accepts, so none of them may have a
        state of its own -- and without this the row's platter sits full and silent for the seconds they
        take. Stamped onto the last published position so the peer and size stay put; `_try_lossless`
        clears the whole entry afterwards exactly as before."""
        last = self._progress.get(request_id)
        if last is not None:
            self._publish_progress(request_id, {**last, "phase": phase})


    def _publish_progress(self, request_id: int, position: dict | None) -> None:
        """One row's transfer moved (or ended). Republishes the whole list, because assigning to
        `status` is what raises the SSE event -- editing the list in place would reach nobody."""
        if position is None and self._progress.pop(request_id, None) is None:
            return                                   # nothing was published for this row; say nothing
        if position is not None:
            self._progress[request_id] = position
        self.status["fetch_progress"] = list(self._progress.values())
