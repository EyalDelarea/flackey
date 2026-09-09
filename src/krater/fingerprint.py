"""Same-recording check (spec §16.1): Chromaprint raw fingerprints of the Deezer 30 s preview slid along the
downloaded file. Numbers measured in the spike: 8.06 frames/s, correct pairs score 0.97-0.99, wrong 0.53-0.76."""
from __future__ import annotations

import asyncio
import json
import logging
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import httpx
import numpy as np

log = logging.getLogger(__name__)
FPS = 8.06                                        # chromaprint hop: 1365 samples at 11025 Hz
SUBFRAME_TRIMS_S = (0.0, 0.031, 0.062, 0.093)     # a quarter frame each; the preview's cut is never frame-aligned
FPCALC_TIMEOUT_S = 120
DEEZER_TRACK = "https://api.deezer.com/track/{id}"
DEEZER_TRIES = 3


class FingerprintError(Exception):
    pass


def fpcalc_available() -> bool:
    return shutil.which("fpcalc") is not None


def fingerprint(path: Path, start_s: float = 0.0) -> list[int]:
    """Raw 32-bit frames for the whole file, optionally starting `start_s` seconds in (via an ffmpeg trim)."""
    if not fpcalc_available():
        raise FingerprintError("fpcalc not installed (brew install chromaprint)")
    src = path
    tmp = None
    try:
        if start_s:
            tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)  # noqa: SIM115 - path must outlive this block
            tmp.close()
            src = Path(tmp.name)
            r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{start_s}", "-i", str(path), "-vn", "-map", "0:a",
                                str(src)], capture_output=True, timeout=FPCALC_TIMEOUT_S, check=False)
            if r.returncode != 0:
                raise FingerprintError(r.stderr.decode(errors="replace")[-300:])
        r = subprocess.run(["fpcalc", "-raw", "-json", "-length", "0", str(src)], capture_output=True,
                            timeout=FPCALC_TIMEOUT_S, check=False)
        if r.returncode != 0:
            raise FingerprintError(r.stderr.decode(errors="replace")[-300:] or "fpcalc failed")
        return [int(x) for x in json.loads(r.stdout)["fingerprint"]]
    except subprocess.TimeoutExpired as e:
        raise FingerprintError(f"{e.cmd[0]} timed out after {FPCALC_TIMEOUT_S}s") from e
    finally:
        if tmp is not None:
            Path(tmp.name).unlink(missing_ok=True)


def compare(needle: list[int], hay: list[int]) -> tuple[float, int]:
    """Best (1 - mean bit disagreement) over every offset of `needle` inside `hay`, and that offset in frames."""
    n = len(needle)
    if n == 0 or len(hay) < n:
        return 0.0, -1
    a = np.asarray(needle, dtype=np.int64).astype(np.uint32)
    h = np.asarray(hay, dtype=np.int64).astype(np.uint32)
    windows = np.lib.stride_tricks.sliding_window_view(h, n)           # (offsets, n)
    diff = np.bitwise_xor(windows, a)
    bits = np.unpackbits(diff.view(np.uint8), axis=-1).sum(axis=-1)    # popcount per window
    off = int(bits.argmin())
    return float(1 - bits[off] / (32 * n)), off


@dataclass
class FingerprintResult:
    status: str                      # "matched" | "failed" | "skipped"
    score: float | None
    offset_s: float | None
    reason: str
    preview: list[int] | None = None
    track: list[int] | None = None

    def to_dict(self) -> dict:
        return {"status": self.status, "score": self.score, "offset_s": self.offset_s, "reason": self.reason,
                "preview_frames": len(self.preview) if self.preview else None,
                "track_frames": len(self.track) if self.track else None}


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


async def check(path: Path, deezer_id: int | None, http: httpx.AsyncClient, *, minimum: float,
                tmp_dir: Path) -> FingerprintResult:
    """Never raises. `skipped` when the check cannot run; `failed` only when it ran and the score is low."""
    if deezer_id is None:
        return FingerprintResult("skipped", None, None, "no deezer id for this request")
    if not fpcalc_available():
        return FingerprintResult("skipped", None, None, "fpcalc not installed")
    preview = tmp_dir / f"preview-{deezer_id}.mp3"
    try:
        url = await _preview_url(deezer_id, http)
        if not url:
            return FingerprintResult("skipped", None, None, "deezer has no preview for this track")
        r = await http.get(url, timeout=30, follow_redirects=True)
        if r.status_code != 200 or not r.content:
            return FingerprintResult("skipped", None, None, f"preview download http {r.status_code}")
        preview.write_bytes(r.content)
        track_fp = await asyncio.to_thread(fingerprint, path)
        best, best_pv = (0.0, -1), None
        for trim in SUBFRAME_TRIMS_S:
            pv = await asyncio.to_thread(fingerprint, preview, trim)
            got = compare(pv, track_fp)
            if got > best:
                best, best_pv = got, pv
        score, offset = best
        offset_s = round(offset / FPS, 1) if offset >= 0 else None
        if score >= minimum:
            return FingerprintResult("matched", round(score, 3), offset_s, f"preview found at {offset_s} s, score {score:.2f}",
                                     best_pv, track_fp)
        return FingerprintResult("failed", round(score, 3), offset_s, f"best score {score:.2f} below {minimum:.2f}",
                                 best_pv, track_fp)
    except (FingerprintError, httpx.HTTPError, OSError, ValueError) as e:
        return FingerprintResult("skipped", None, None, str(e) or type(e).__name__)
    finally:
        preview.unlink(missing_ok=True)
