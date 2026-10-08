"""The acoustic reference a download is fingerprinted against, and the one call that checks it."""
from __future__ import annotations

import logging
from pathlib import Path

from ..fingerprint import AcousticReference, FingerprintResult
from ..fingerprint import check as fingerprint_check
from ..models import Candidate, Request, RequestKind
from ..reference import deezer_reference, find_deezer_record, youtube_reference
from ..youtube import YouTubeError
from .records import REFERENCE_ERRORS, Acoustic

log = logging.getLogger(__name__)


class AcousticMixin:
    """Part of `Worker`, which supplies the state these methods read."""

    async def _fingerprint(self, path: Path, reference: AcousticReference | None,
                           missing: str = "") -> FingerprintResult:
        """The recording check, for every caller: the lossy copy, each lossless pick, and a kept copy the
        owner accepts. On the CPU semaphore because fpcalc decodes the whole file; it belongs with the
        other ffmpeg work."""
        async with self._cpu:
            return await fingerprint_check(path, reference, minimum=self.settings.lossless_fingerprint_min,
                                           missing=missing)

    async def _video_reference(self, req: Request) -> tuple[AcousticReference | None, str]:
        """The video's own audio as the reference (issue #67), stored once found, or None and the reason.
        Playlist entries are YT_TRACK rows with their own source_url, so they qualify too."""
        stored = self.store.get_reference(req.id)
        if stored and stored["kind"] == "youtube":
            return AcousticReference.from_dict(stored), ""
        if req.kind != RequestKind.YT_TRACK or not req.source_url:
            return None, "not a YouTube request"
        try:
            async with self._cpu:
                ref = await youtube_reference(req.source_url, self.settings.tmp_dir / f"req{req.id}",
                                              duration_s=req.query_duration_s)
        except YouTubeError as e:
            # Transient until proven otherwise: yt-dlp fails for a minute (429, a network blip) far more
            # often than for good, and `_process` puts the request on the quick ladder for it.
            log.warning("req#%d: no video reference: %s", req.id, e)
            return None, f"video: {e or type(e).__name__}"
        except REFERENCE_ERRORS as e:
            # The audio came and could not be read (ffmpeg, fpcalc, a broken file): no retry -- the next
            # pass would hand the same bytes to the same tools.
            log.warning("req#%d: video audio unreadable: %s", req.id, e)
            return None, f"video audio: {e or type(e).__name__}"
        self.store.set_reference(req.id, ref.to_dict())
        return ref, ""


    async def _acoustic_reference(self, req: Request, cand: Candidate) -> Acoustic:
        """The request's own video when there is one, else the candidate's Deezer preview. Fetched once and
        kept beside the request: every pick, the lossy fallback, a retry and the library sweep reuse it.
        A failure to get one is a fact about this request, worded for the attempt row and the owner."""
        stored = self.store.get_reference(req.id)
        if stored:
            return Acoustic(AcousticReference.from_dict(stored))
        ref, why = await self._video_reference(req)
        reasons = [why] if ref is None and why else []
        deezer_id = cand.deezer_id
        if ref is None and deezer_id is None:
            # No bot record (the bot is off, or found nothing): Deezer's public catalogue still has the
            # preview for most tracks, and without one a request with no video cannot be checked at all.
            deezer_id, found = await find_deezer_record(cand, self.http)
            log.info("req#%d deezer lookup: %s", req.id, found)
            if deezer_id is None:
                reasons.append(found)
        if ref is None and deezer_id:
            try:
                async with self._cpu:
                    ref = await deezer_reference(deezer_id, self.http, self.settings.tmp_dir)
            except REFERENCE_ERRORS as e:
                reasons.append(f"deezer preview: {e or type(e).__name__}")
        if ref is None:
            return Acoustic(None, "; ".join(reasons) or "no video and no Deezer record to fingerprint against")
        self.store.set_reference(req.id, ref.to_dict())
        return Acoustic(ref)
