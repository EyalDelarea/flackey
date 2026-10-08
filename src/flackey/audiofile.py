"""What an audio file is, from its first bytes, before ffmpeg, ffprobe or fpcalc reads it.

A peer or a bot names its file; the name is not evidence. Left to probe, ffmpeg picks a demuxer from the
content, and some of its demuxers (playlists, concat lists, HLS) open further files or URLs named inside
the input. So a file whose extension claims a format is checked against that format's magic bytes and
then read with that demuxer forced, and every tool run may only open local files and pipes. An extension
that claims nothing (a YouTube reference's webm or m4a) keeps the tool's own probe, still under the
protocol whitelist."""
from __future__ import annotations

from pathlib import Path

HEAD_BYTES = 16
# The ffmpeg demuxer each claimed extension is read with.
DEMUXERS = {".flac": "flac", ".wav": "wav", ".aif": "aiff", ".aiff": "aiff", ".mp3": "mp3"}
PROTOCOLS = ["-protocol_whitelist", "file,pipe"]


class WrongFormat(ValueError):
    """The file's bytes are not the format its extension claims."""


def _id3_end(head: bytes) -> int:
    """Where the audio starts after an ID3v2 tag at the front, or 0 when there is none."""
    if len(head) < 10 or head[:3] != b"ID3" or any(b & 0x80 for b in head[6:10]):
        return 0
    size = (head[6] << 21) | (head[7] << 14) | (head[8] << 7) | head[9]
    return 10 + size + (10 if head[5] & 0x10 else 0)


def _container(head: bytes) -> str | None:
    if head[:4] == b"fLaC":
        return "flac"
    if head[:4] in (b"RIFF", b"RF64") and head[8:12] == b"WAVE":
        return "wav"
    if head[:4] == b"FORM" and head[8:12] in (b"AIFF", b"AIFC"):
        return "aiff"
    if len(head) >= 2 and head[0] == 0xFF and head[1] & 0xE0 == 0xE0 and head[1] & 0x06:
        return "mp3"
    return None


def sniff(path: Path) -> str | None:
    """"flac", "wav", "aiff" or "mp3" when the first bytes say so, else None. An ID3v2 tag in front is
    skipped: MP3s carry one, and some taggers put one ahead of a FLAC too."""
    with open(path, "rb") as f:
        head = f.read(HEAD_BYTES)
        start = _id3_end(head)
        if not start:
            return _container(head)
        f.seek(start)
        after = f.read(HEAD_BYTES)
    # Anything else behind an ID3 tag is read as MP3: the mp3 demuxer resyncs past padding, and forcing
    # it is what keeps the file away from every other demuxer.
    return "flac" if _container(after) == "flac" else "mp3"


def demuxer(path: Path) -> str | None:
    """The demuxer `path`'s extension claims, once its bytes agree; None for an extension that claims no
    format here. Raises WrongFormat when the bytes say otherwise, OSError when the file cannot be read."""
    claimed = DEMUXERS.get(path.suffix.lower())
    if claimed is None:
        return None
    actual = sniff(path)
    if actual != claimed:
        raise WrongFormat(f"{path.name} is not a {claimed} file" + (f" (it is {actual})" if actual else ""))
    return claimed


def input_args(path: Path) -> list[str]:
    """The ffmpeg/ffprobe options that go right before `-i path` (or before `path` for ffprobe)."""
    fmt = demuxer(path)
    return PROTOCOLS + (["-f", fmt] if fmt else [])
