"""Lossless-to-lossless conversion for filing (spec §9). Audio only, metadata dropped, PCM at the source
bit depth; the spike proved the decoded samples are identical before and after."""
from __future__ import annotations

import logging
import subprocess
import time
from pathlib import Path

from .audiofile import WrongFormat, input_args
from .tools import no_window, tool_path

log = logging.getLogger(__name__)
RUN_TIMEOUT_S = 300
# The output is bounded whatever the input claims (lossless.py already refuses a track past 1 GB or
# 30 minutes before it downloads). The time ceiling sits above that cap plus the worker's duration slack,
# so no accepted track reaches it, and the worker refuses an output shorter than its input should one
# ever do so: 32 minutes of 24-bit 48 kHz stereo PCM is about 553 MB.
MAX_OUTPUT_S = 32 * 60
MAX_OUTPUT_BYTES = 1_000_000_000
CODECS = {("aiff", 16): "pcm_s16be", ("aiff", 24): "pcm_s24be", ("wav", 16): "pcm_s16le", ("wav", 24): "pcm_s24le"}


class ConvertError(Exception):
    pass


def _run_ffmpeg(src: Path, dst: Path, codec: str) -> Path:
    # Absolute path, not the bare name: a .app launched from Finder gets a PATH without the Homebrew
    # prefixes, so the bare name resolves to nothing there even when ffmpeg is plainly installed. None
    # means it is genuinely absent -- `tool_path` has already looked in all three places -- and saying so
    # beats letting FileNotFoundError surface as a failed row whose message is a filename.
    ffmpeg = tool_path("ffmpeg")
    if ffmpeg is None:
        raise ConvertError("ffmpeg is not installed")
    try:
        pinned = input_args(src)
    except (WrongFormat, OSError) as e:
        raise ConvertError(str(e)) from e
    cmd = [ffmpeg, "-v", "error", "-y", *pinned, "-i", str(src), "-vn", "-map", "0:a", "-map_metadata", "-1",
           "-c:a", codec, "-t", str(MAX_OUTPUT_S), "-fs", str(MAX_OUTPUT_BYTES), str(dst)]
    t0 = time.monotonic()
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=RUN_TIMEOUT_S, check=False, **no_window())
    except subprocess.TimeoutExpired as e:
        dst.unlink(missing_ok=True)
        raise ConvertError(f"ffmpeg timed out after {RUN_TIMEOUT_S}s") from e
    if p.returncode != 0 or not dst.exists():
        dst.unlink(missing_ok=True)
        raise ConvertError(p.stderr.decode(errors="replace")[-400:] or "ffmpeg produced no file")
    if dst.stat().st_size >= MAX_OUTPUT_BYTES:
        # ffmpeg stops at `-fs` and still exits 0: what it wrote is a cut-off track, not a conversion.
        dst.unlink(missing_ok=True)
        raise ConvertError(f"the converted file reached the {MAX_OUTPUT_BYTES} byte limit")
    log.info("converted %s -> %s (%s) in %.1f s", src.name, dst.name, codec, time.monotonic() - t0)
    return dst


def _distinct(src: Path, fmt: str) -> Path:
    """`src` renamed to a `fmt` suffix, guaranteed never to be `src` itself.

    ffmpeg refuses outright when its output names its input ("Output ... same as Input #0"), and a peer's
    file often already carries the filing format's own extension -- an .aiff download while
    `lossless_filing_format` is "aiff", the Mac default. The comparison is case-insensitive because
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
