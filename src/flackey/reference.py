"""The audio the owner actually asked for, as fingerprints (issue #67). For a YouTube request that is the
video itself: it needs no Deezer id and no Beatport record, and it exists for every such request. The
Deezer 30 s preview is the reference for requests with no audio of their own (text, Spotify). Above
`youtube`, `verify` and `fingerprint` in the import layers because it needs all three; the worker and
the sweep are its callers."""
from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import uuid4

import httpx

from .fingerprint import SUBFRAME_TRIMS_S, AcousticReference, FingerprintError, fingerprint
from .verify import VerifyError, probe
from .youtube import fetch_audio, video_id

EXCERPT_S = 30.0          # the Deezer preview's shape; `compare` needs the needle shorter than the hay
DEEZER_TRACK = "https://api.deezer.com/track/{id}"
DEEZER_TRIES = 3


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
