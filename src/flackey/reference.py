"""The audio the owner actually asked for, as fingerprints (issue #67). For a YouTube request that is the
video itself: it needs no Deezer id and no Beatport record, and it exists for every such request. The
Deezer 30 s preview is the reference for requests with no audio of their own (text, Spotify). Above
`youtube`, `verify` and `fingerprint` in the import layers because it needs all three; the worker and
the sweep are its callers."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import httpx

from .fingerprint import SUBFRAME_TRIMS_S, AcousticReference, FingerprintError, compare, fingerprint
from .models import Candidate
from .verify import VerifyError, probe
from .youtube import fetch_audio, video_id

log = logging.getLogger(__name__)

EXCERPT_S = 30.0          # the Deezer preview's shape; `compare` needs the needle shorter than the hay
DEEZER_TRACK = "https://api.deezer.com/track/{id}"
DEEZER_TRIES = 3
# Previews tried per request: the bot's menu is text-ordered, so the record is in the first few.
IDENTIFY_MAX = 5


def _needles(path: Path, start_s: float, length_s: float | None) -> list[list[int]]:
    return [fingerprint(path, start_s + trim, length_s) for trim in SUBFRAME_TRIMS_S]


async def youtube_reference(url: str, tmp_dir: Path, *, duration_s: float | None = None) -> AcousticReference:
    """Fetch the video's audio, fingerprint all of it and EXCERPT_S from its middle at each sub-frame trim,
    delete the audio. Raises YouTubeError (fetch) or FingerprintError (ffmpeg, fpcalc, unreadable audio)."""
    audio = await fetch_audio(url, tmp_dir)
    try:
        length = float(duration_s) if duration_s else await asyncio.to_thread(_length_s, audio)
        start = max((length - EXCERPT_S) / 2, 0.0)
        full = await asyncio.to_thread(fingerprint, audio)
        needles = await asyncio.to_thread(_needles, audio, start, EXCERPT_S)
    finally:
        audio.unlink(missing_ok=True)
    return AcousticReference("youtube", video_id(url), needles, full, round(start, 3), EXCERPT_S)


def _length_s(audio: Path) -> float:
    try:
        return probe(audio).duration_s
    except VerifyError as e:
        raise FingerprintError(f"cannot read the video's audio: {e}") from e


async def _preview_url(deezer_id: int, http: httpx.AsyncClient) -> str | None:
    last = "no response"
    for i in range(DEEZER_TRIES):
        try:
            r = await http.get(DEEZER_TRACK.format(id=deezer_id), timeout=20)
        except httpx.HTTPError as e:
            last = type(e).__name__
        else:
            if r.status_code == 200:
                return r.json().get("preview") or None
            last = f"deezer http {r.status_code}"
            if r.status_code < 500:
                break
        if i < DEEZER_TRIES - 1:
            await asyncio.sleep(0)
    raise FingerprintError(last)


async def deezer_needles(deezer_id: int, http: httpx.AsyncClient, tmp_dir: Path) -> list[list[int]]:
    """The Deezer preview at each sub-frame trim. Unique file name per call: two requests for one
    recording can be here at once. Raises FingerprintError when Deezer has no preview or the download
    fails."""
    tmp_dir.mkdir(parents=True, exist_ok=True)
    preview = tmp_dir / f"preview-{deezer_id}-{uuid4().hex[:8]}.mp3"
    try:
        url = await _preview_url(deezer_id, http)
        if not url:
            raise FingerprintError("deezer has no preview for this track")
        try:
            r = await http.get(url, timeout=30, follow_redirects=True)
        except httpx.HTTPError as e:
            raise FingerprintError(f"preview download {type(e).__name__}") from e
        if r.status_code != 200 or not r.content:
            raise FingerprintError(f"preview download http {r.status_code}")
        preview.write_bytes(r.content)
        # No length bound: the preview is already 30 s, and asking ffmpeg to cut it would re-encode the
        # trim-0 needle that today's check reads straight off the file.
        return await asyncio.to_thread(_needles, preview, 0.0, None)
    finally:
        preview.unlink(missing_ok=True)


async def deezer_reference(deezer_id: int, http: httpx.AsyncClient, tmp_dir: Path) -> AcousticReference:
    """The Deezer preview as the reference, for a request that has no audio of its own."""
    needles = await deezer_needles(deezer_id, http, tmp_dir)
    return AcousticReference("deezer", str(deezer_id), needles, needles[0], 0.0, EXCERPT_S)


@dataclass(frozen=True)
class Identification:
    """Which Deezer record the request's own audio says it is (issue #68)."""
    chosen: Candidate | None
    score: float | None
    tried: list[tuple[int, float | None]]   # (deezer_id, best score; None when the preview could not be fetched)
    reason: str


async def identify_record(reference: AcousticReference, cands: list[Candidate], http: httpx.AsyncClient,
                          tmp_dir: Path, *, minimum: float, limit: int = IDENTIFY_MAX) -> Identification:
    """Which Deezer candidate is the recording the owner pointed at (issue #68): the first whose 30 s preview
    is found inside the reference's full fingerprint at or above `minimum`. The preview is the needle and
    the reference's full fingerprint is the hay -- the other way round `compare` would return 0.0 for
    everything, because a 30 s needle cannot hold a whole track. Candidates are tried in the order given
    (text score, best first). A text score never overrules this: a candidate whose preview does not match is
    not the track, however its title reads. Never raises; a preview that cannot be fetched counts as tried
    with no score."""
    tried: list[tuple[int, float | None]] = []
    for cand in [c for c in cands if c.deezer_id][:limit]:
        try:
            needles = await deezer_needles(cand.deezer_id, http, tmp_dir)
            score = round(max(compare(n, reference.full)[0] for n in needles), 3)
        except FingerprintError as e:
            log.info("identify: deezer:%d preview unavailable: %s", cand.deezer_id, e)
            tried.append((cand.deezer_id, None))
            continue
        except Exception:
            # Identification must never sink a request: an unforeseen failure here is one preview missing,
            # not a verdict about the recording.
            log.exception("identify: deezer:%d could not be compared", cand.deezer_id)
            tried.append((cand.deezer_id, None))
            continue
        tried.append((cand.deezer_id, score))
        if score >= minimum:
            return Identification(cand, score, tried,
                                  f"deezer:{cand.deezer_id} preview matches the video, score {score:.2f}")
    # Three different silences, and the owner acts on each differently: nothing to ask (no Deezer record at
    # all -- true of 33 tracks in the library), nobody answered (every preview failed to download), or the
    # audio answered no. Only the last is a verdict about the recording.
    if not tried:
        return Identification(None, None, tried, "no Deezer record to check the video against")
    if all(s is None for _, s in tried):
        return Identification(None, None, tried,
                              f"none of the {len(tried)} Deezer previews could be fetched")
    return Identification(None, None, tried, f"none of {len(tried)} Deezer previews is the video's recording")
