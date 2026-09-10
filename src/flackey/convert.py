"""Lossless-to-lossless conversion for filing (spec §9). Audio only, metadata dropped, PCM at the source
bit depth; the spike proved the decoded samples are identical before and after."""
from __future__ import annotations

import logging
import subprocess
import time
from pathlib import Path

from .tools import tool_path

log = logging.getLogger(__name__)
RUN_TIMEOUT_S = 300
CODECS = {("aiff", 16): "pcm_s16be", ("aiff", 24): "pcm_s24be", ("wav", 16): "pcm_s16le", ("wav", 24): "pcm_s24le"}


class ConvertError(Exception):
    pass


def _run_ffmpeg(src: Path, dst: Path, codec: str) -> Path:
    # Absolute path, not the bare name: a .app launched from Finder gets a PATH without the Homebrew
    # prefixes, so the bare name resolves to nothing there even when ffmpeg is plainly installed.
    cmd = [tool_path("ffmpeg") or "ffmpeg", "-v", "error", "-y", "-i", str(src), "-vn", "-map", "0:a", "-map_metadata", "-1",
           "-c:a", codec, str(dst)]
    t0 = time.monotonic()
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=RUN_TIMEOUT_S, check=False)
    except subprocess.TimeoutExpired as e:
        dst.unlink(missing_ok=True)
        raise ConvertError(f"ffmpeg timed out after {RUN_TIMEOUT_S}s") from e
    if p.returncode != 0 or not dst.exists():
        dst.unlink(missing_ok=True)
        raise ConvertError(p.stderr.decode(errors="replace")[-400:] or "ffmpeg produced no file")
    log.info("converted %s -> %s (%s) in %.1f s", src.name, dst.name, codec, time.monotonic() - t0)
    return dst


def _distinct(src: Path, fmt: str) -> Path:
    """`src` renamed to a `fmt` suffix, guaranteed never to be `src` itself.

    ffmpeg refuses outright when its output names its input ("Output ... same as Input #0"), and a peer's
    file often already carries the filing format's own extension -- an .aiff download while
    `lossless_filing_format` is "aiff", which is the default. The comparison is case-insensitive because
    macOS is: writing `x.wav` while reading `x.WAV` is the same collision, spelled differently."""
    dst = src.with_suffix(f".{fmt}")
    return dst if dst.name.lower() != src.name.lower() else src.with_name(f"{src.stem}.clean.{fmt}")


def to_format(src: Path, fmt: str, bit_depth: int | None) -> Path:
    """Write a new file next to `src` and return it; the result is always a distinct path from `src`,
    never `src` itself (see `_distinct`). `fmt` "flac" rebuilds the container through ffmpeg too, so a
    peer's APPLICATION/CUESHEET/foreign-ID3 blocks never reach the library (tagging happens afterwards,
    unaffected): `-c:a copy` when `src` is already a flac (bit-identical, no re-encode), `-c:a flac` when
    `src` is wav/aiff/aif (a real but still-lossless encode; PCM cannot be copied into a FLAC container)."""
    if fmt == "flac":
        return _run_ffmpeg(src, _distinct(src, "flac"), "copy" if src.suffix.lower() == ".flac" else "flac")
    bits = 16 if bit_depth in (None, 16) else 24
    codec = CODECS.get((fmt, bits))
    if codec is None:
        raise ConvertError(f"no codec for {fmt} at {bits} bit")
    return _run_ffmpeg(src, _distinct(src, fmt), codec)
