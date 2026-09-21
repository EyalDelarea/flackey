"""Same-recording check (spec §16.1): Chromaprint raw fingerprints of a ~30 s reference excerpt (the
request's own video, or the Deezer preview) slid along the downloaded file. Numbers measured in the spike:
8.06 frames/s, correct pairs score 0.97-0.99, wrong 0.53-0.76.

This module only compares. Fetching the reference audio and turning it into an `AcousticReference` is
`reference.py`'s job, which is why nothing here talks to the network any more."""
from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .tools import tool_path

log = logging.getLogger(__name__)
FPS = 8.06                                        # chromaprint hop: 1365 samples at 11025 Hz
SUBFRAME_TRIMS_S = (0.0, 0.031, 0.062, 0.093)     # a quarter frame each; the preview's cut is never frame-aligned
FPCALC_TIMEOUT_S = 120


class FingerprintError(Exception):
    pass


def fpcalc_available() -> bool:
    return tool_path("fpcalc") is not None


def fingerprint(path: Path, start_s: float = 0.0, length_s: float | None = None) -> list[int]:
    """Raw 32-bit frames for the whole file, or for the `length_s` seconds starting `start_s` seconds in
    (via an ffmpeg trim). A bounded excerpt is what makes a needle: `compare` slides the needle along the
    hay, so the reference excerpt has to be shorter than whatever it is looked for in."""
    fpcalc = tool_path("fpcalc")
    if fpcalc is None:
        raise FingerprintError("fpcalc not installed (brew install chromaprint)")
    src = path
    tmp = None
    try:
        if start_s or length_s:
            tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)  # noqa: SIM115 - path must outlive this block
            tmp.close()
            src = Path(tmp.name)
            ffmpeg = tool_path("ffmpeg")
            if ffmpeg is None:
                raise FingerprintError("ffmpeg is not installed")
            trim = ["-ss", f"{start_s}"] + (["-t", f"{length_s}"] if length_s else [])
            r = subprocess.run([ffmpeg, "-v", "error", "-y", *trim, "-i", str(path), "-vn", "-map", "0:a",
                                str(src)], capture_output=True, timeout=FPCALC_TIMEOUT_S, check=False)
            if r.returncode != 0:
                raise FingerprintError(r.stderr.decode(errors="replace")[-300:])
        r = subprocess.run([fpcalc, "-raw", "-json", "-length", "0", str(src)], capture_output=True,
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
class AcousticReference:
    """The audio the owner pointed at, as fingerprints. `needles` are a short excerpt at each sub-frame trim
    (SUBFRAME_TRIMS_S), because a cut is never frame-aligned: the needle when a download is the hay.
    `full` is the whole reference audio: the hay when a Deezer preview is the needle (identifying the
    record, issue #68). Persisted once per request so every pick, the lossy fallback, a retry and the
    library sweep reuse it."""
    kind: str                      # "youtube" | "deezer"
    ref: str                       # video id, or Deezer track id
    needles: list[list[int]]
    full: list[int]
    excerpt_start_s: float
    excerpt_s: float

    @property
    def label(self) -> str:
        return f"{self.kind}:{self.ref}"

    def to_dict(self) -> dict:
        return {"kind": self.kind, "ref": self.ref, "needles": self.needles, "full": self.full,
                "excerpt_start_s": self.excerpt_start_s, "excerpt_s": self.excerpt_s}

    @classmethod
    def from_dict(cls, d: dict) -> AcousticReference:
        return cls(d["kind"], str(d["ref"]), [list(n) for n in d["needles"]], list(d["full"]),
                   float(d["excerpt_start_s"]), float(d["excerpt_s"]))


@dataclass
class FingerprintResult:
    status: str                      # "matched" | "failed" | "skipped"
    score: float | None
    offset_s: float | None
    reason: str
    preview: list[int] | None = None   # the reference needle that scored best; named for the attempt's raw JSON
    track: list[int] | None = None
    reference: str | None = None       # which audio it was compared against, `AcousticReference.label`

    def to_dict(self) -> dict:
        return {"status": self.status, "score": self.score, "offset_s": self.offset_s, "reason": self.reason,
                "preview_frames": len(self.preview) if self.preview else None,
                "track_frames": len(self.track) if self.track else None,
                "reference": self.reference}


async def check(path: Path, reference: AcousticReference | None, *, minimum: float,
                missing: str = "") -> FingerprintResult:
    """Never raises. `skipped` when the check cannot run (no reference, no fpcalc, fpcalc failed) --
    `missing` says why there is no reference; `failed` only when it ran and the best score is low."""
    if reference is None:
        return FingerprintResult("skipped", None, None, missing or "no acoustic reference for this request")
    if not fpcalc_available():
        return FingerprintResult("skipped", None, None, "fpcalc not installed", reference=reference.label)
    try:
        track_fp = await asyncio.to_thread(fingerprint, path)
    except (FingerprintError, OSError, ValueError) as e:
        return FingerprintResult("skipped", None, None, str(e) or type(e).__name__, reference=reference.label)
    best, best_needle = (0.0, -1), None
    for needle in reference.needles:
        got = compare(needle, track_fp)
        if got > best:
            best, best_needle = got, needle
    score, offset = best
    offset_s = round(offset / FPS, 1) if offset >= 0 else None
    if score >= minimum:
        return FingerprintResult("matched", round(score, 3), offset_s,
                                 f"{reference.label} found at {offset_s} s, score {score:.2f}",
                                 best_needle, track_fp, reference.label)
    return FingerprintResult("failed", round(score, 3), offset_s, f"best score {score:.2f} below {minimum:.2f}",
                             best_needle, track_fp, reference.label)
