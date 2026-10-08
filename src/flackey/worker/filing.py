"""Verify the lossy copy, file a track into the library, keep a refused copy, and upgrade a filed
track to a lossless one."""
from __future__ import annotations

import asyncio
import logging
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path

from ..export import write_playlist
from ..fingerprint import FPS, AcousticReference, FingerprintResult
from ..library import file_track, final_path, refile_path
from ..models import Candidate, CatalogTrack, Request, RequestState, Track, Verdict
from ..tag import write_tags
from ..verify import verify
from .records import Acoustic, LosslessHit, _fallback_catalog, _mmss, format_line, query_candidate

log = logging.getLogger(__name__)


class FilingMixin:
    """Part of `Worker`, which supplies the state these methods read."""

    async def accept_rejection(self, request_id: int) -> Request:
        """"Keep it anyway" on a track the recording check refused (issue #92).

        The one rejection an ear can overturn. `fingerprint_check` answers "is this the same recording as
        the reference" with a number, and a number needs a floor; below it the pipeline must refuse, because
        the alternative is filing whatever a search happened to return. But on older music the floor is
        genuinely wrong sometimes -- a remaster, a re-press, a different mix of the same take -- and the
        only instrument that can tell is the owner's ear. So the refused file is kept (see `_keep_rejected`),
        the row plays it, and this files those same bytes: not a fresh search that might land on a different
        copy, but the copy they listened to.

        The check is re-run rather than skipped. It will fail again -- these are the same bytes against the
        same reference -- and that is the point: the track is filed carrying a `recording_match` row saying
        `failed` and at what score, so the library's own record says this one was kept on the owner's say-so
        rather than claiming it passed.

        The rejection row itself stays. It is what happened, it is the handle `unlink_spectrogram` deletes
        the PNG by, and with the request now DONE no page draws it. Only `audio_path` is cleared, because
        the file it pointed at has moved into the library."""
        req = self.store.get_request(request_id)
        if req.state != RequestState.REJECTED:
            raise ValueError(f"request {request_id} is {req.state.value}; only a rejected track can be kept anyway")
        rejection = self.store.get_rejection_for_request(request_id)
        if rejection is None or rejection.kind != "different_recording" or not rejection.audio_path:
            raise ValueError("this track failed the quality check, not the recording check; there is no "
                             "copy of it to keep")
        kept = Path(rejection.audio_path)
        if not kept.exists():
            self.store.clear_rejection_audio(rejection.id)
            raise ValueError("the copy of this track is no longer on disk")
        cand = (self.store.get_candidate(req.chosen_candidate_id) if req.chosen_candidate_id
                else query_candidate(req.query(), req.id))
        catalog = self.store.get_catalog_track(req.catalog_track_id) if req.catalog_track_id else None
        # Into tmp_dir first: `write_tags` and `file_track` both work on the file in place, and the kept
        # copy must not be the thing that is tagged and moved -- a duplicate or a failure part-way would
        # otherwise leave the row pointing at a file that has been edited or is gone.
        self.settings.tmp_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.settings.tmp_dir / f"req{req.id}-accepted{kept.suffix}"
        shutil.copy2(kept, tmp)
        try:
            async with self._cpu:
                verdict = await asyncio.to_thread(verify, tmp, self.settings.spectrogram_dir,
                                                  f"req{req.id}-accepted")
            if not verdict.passed:
                # It passed the spectral check the first time round, so this is a file that has changed
                # under us. Filing it would put a copy in the library that no check ever vouched for.
                raise ValueError(f"the kept copy no longer passes the quality check: {verdict.reason}")
            stored = self.store.get_reference(req.id)
            fp = None
            if stored is not None:
                fp = await self._fingerprint(tmp, AcousticReference.from_dict(stored))
            await self._file_track(req, cand, catalog, tmp, verdict, fp, None, self.source.name, None)
        finally:
            tmp.unlink(missing_ok=True)   # gone already when file_track moved it; garbage otherwise
        self.store.clear_rejection_audio(rejection.id)
        kept.unlink(missing_ok=True)
        return self.store.get_request(request_id)


    async def _verify_and_file(self, req: Request, cand: Candidate, catalog: CatalogTrack | None, tmp: Path,
                               hit: LosslessHit | None = None, acoustic: Acoustic | None = None) -> None:
        if hit is None:
            self._set_state(req, RequestState.VERIFYING)
            # ffprobe plus two ffmpeg passes take seconds on a 7-minute file: keep the event loop
            # (inbox bot, API) free
            async with self._cpu:
                verdict = await asyncio.to_thread(verify, tmp, self.settings.spectrogram_dir,
                                                  f"req{req.id}-{tmp.stem}")
            if not verdict.passed:
                self.store.add_rejection(req.id, verdict.reason, verdict.bitrate_kbps, verdict.cutoff_hz,
                                         verdict.spectrogram_path)
                self._set_state(req, RequestState.REJECTED)
                log.info("req#%d rejected: %s", req.id, verdict.reason)
                await self.notifier.send(f"Rejected: {cand.artist} – {cand.title}\n{verdict.reason}")
                return
            # The lossy copy is checked against the same reference, at the same threshold, as a peer's
            # file (spec §7). It used to be filed on the spectral check alone -- which says the audio is
            # really 320 kbps, and nothing at all about it being the recording that was asked for. This is
            # the last path that could file a track no fingerprint vouched for.
            fp = await self._fingerprint(tmp, acoustic.reference if acoustic else None,
                                         missing=acoustic.missing if acoustic else "")
            log.info("req#%d lossy copy fingerprint: %s %s", req.id, fp.status, fp.reason)
            if fp.status == "failed":
                reason = f"a different recording: {fp.reason}"
                # Kept, not deleted -- the one verdict an ear can overturn (issue #92). The check says this
                # audio is genuine and is not the recording that was asked for, and on older music that can
                # be true and still be the copy the owner wants: a remaster, a re-press, a different mix of
                # the same take all score below the floor. So the file is moved out of `tmp` before the
                # `finally` that deletes it, the row plays it, and "Keep it anyway" files these very bytes.
                # A quality rejection is not kept: "this is an MP3 wearing a FLAC extension" is a fact
                # about the file, not a matter of taste, and there is nothing to listen for.
                kept = self._keep_rejected(req, tmp)
                # No cutoff on this one. This branch is only reached with `verdict.passed` already true, so
                # `verdict.cutoff_hz` here is a *healthy* number -- and the page turns any cutoff it is given
                # into "it stops at N kHz, it was blown up from a smaller file", which would tell the owner a
                # genuine 320 kbps file is a fake and quote a frequency that is fine as the proof. What this
                # file failed is the fingerprint, so the row says so in `kind` and carries no spectral
                # evidence it did not fail on. The spectrogram stays: it is a true picture of the audio, and
                # the rejection row is the only handle `unlink_spectrogram` has for deleting the PNG later.
                self.store.add_rejection(req.id, reason, verdict.bitrate_kbps, None,
                                         verdict.spectrogram_path, kind="different_recording",
                                         audio_path=kept)
                self._set_state(req, RequestState.REJECTED)
                tail = "\nYou can listen to it in the app and keep it anyway." if kept else ""
                await self.notifier.send(f"Rejected: {cand.artist} – {cand.title}\n{reason}{tail}")
                return
            if fp.status == "skipped":
                # Nothing to check against, so nothing to file on, and nothing a retry would change on its
                # own: the owner is told what is missing and the row waits for them (spec §6).
                reason = (f"the recording could not be checked acoustically ({fp.reason}). "
                          "Use Try again once it can be checked")
                self._set_state(req, RequestState.ERROR, attempts=req.attempts + 1, error_message=reason)
                await self.notifier.send(f"Could not verify: {req.raw_text}\n{reason}")
                return
        else:
            verdict = hit.verdict                     # verified and fingerprinted inside the attempt
            fp = hit.fingerprint
        source = hit.provider if hit else self.source.name
        source_fmt = hit.source_fmt if hit else None
        await self._file_track(req, cand, catalog, tmp, verdict, fp, hit, source, source_fmt)


    def _keep_rejected(self, req: Request, tmp: Path) -> Path | None:
        """Move the refused file out of `tmp_dir` before the pass that produced it cleans up, and answer
        with where it went. Best effort: a copy that cannot be kept costs the owner the listen, not the
        rejection, so a full disk leaves a row that explains itself and offers no player."""
        try:
            self.settings.rejected_dir.mkdir(parents=True, exist_ok=True)
            kept = self.settings.rejected_dir / f"req{req.id}-{int(time.time())}{tmp.suffix}"
            shutil.move(str(tmp), str(kept))
        except OSError as e:
            log.warning("req#%d: could not keep the refused copy: %s", req.id, e)
            return None
        log.info("req#%d kept the refused copy at %s", req.id, kept.name)
        return kept


    async def _file_track(self, req: Request, cand: Candidate, catalog: CatalogTrack | None, tmp: Path,
                          verdict: Verdict, fp: FingerprintResult | None, hit: LosslessHit | None,
                          source: str, source_fmt: str | None) -> None:
        """Tag the file, move it into the library and write the row -- everything after a file has been
        judged fit to keep.

        Its own method because there are now two ways to reach it. The pipeline gets here by passing every
        check; `accept_rejection` gets here because the owner listened to a file the fingerprint refused and
        said keep it anyway (issue #92). One tail, so a track filed by hand is tagged, named, counted,
        added to its playlist and announced exactly like every other track -- the alternative was a second
        copy of this drifting away from the first."""
        self._set_state(req, RequestState.FILING)
        if catalog is None:
            catalog = _fallback_catalog(cand)
            self.store.upsert_catalog_track(catalog)
            self.store.update_request(req.id, catalog_track_id=catalog.id)
        artwork = await self.artwork_fetch(catalog.artwork_url) if catalog.artwork_url else None
        await asyncio.to_thread(write_tags, tmp, catalog, verdict, artwork, source=source)
        dest = final_path(self.settings.library_root, catalog, tmp.suffix.lstrip("."),
                          layout=self.settings.library_layout)
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
        # None only when the check could not be run at all at accept time -- no stored reference, no fpcalc.
        # A track with no recording_match row says "not checked", which is true; a fabricated passing one
        # would not be.
        if fp is not None:
            self._record_evidence(track_id, fp, hit)
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

        acoustic = await self._acoustic_reference(req, cand)
        hit = await self._try_lossless(req, cand, catalog, acoustic)
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
        # The replacement goes where the track already is: under a date layout, recomputing the path
        # would move a track filed months ago into today's folder.
        dest = refile_path(t.path.parent, catalog, hit.path.suffix.lstrip("."))
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
        self._record_evidence(t.id, hit.fingerprint, hit)
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


    def _record_evidence(self, track_id: int, fp: FingerprintResult, hit: LosslessHit | None) -> None:
        """What the filed file was checked against. `source` names the peer and the attempt behind it, so
        it only exists for a lossless hit; the recording check is recorded for the lossy copy too, because
        since issue #68 that copy is fingerprinted as well and the owner should be able to see it."""
        if hit is not None:
            self.store.add_evidence(track_id, "source", {"provider": hit.provider, "source_fmt": hit.source_fmt,
                                                         "attempt_id": hit.attempt_id})
        self.store.add_evidence(track_id, "recording_match", {"status": fp.status, "score": fp.score, "offset_s": fp.offset_s,
                                                              "reference": fp.reference, "reason": fp.reason})
        if fp.track:
            self.store.add_evidence(track_id, "fingerprint", {"frames": fp.track, "fps": FPS})
