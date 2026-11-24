"""What an audio file is, from its first bytes, before ffmpeg, ffprobe or fpcalc reads it.

A peer or a bot names its file; the name is not evidence. Left to probe, ffmpeg picks a demuxer from the
content, and some of its demuxers (playlists, concat lists, HLS) open further files or URLs named inside
the input. So a file whose extension claims a format is checked against that format's magic bytes and
then read with that demuxer forced, and every tool run may only open local files and pipes. An extension
that claims nothing (a YouTube reference's webm or m4a) keeps the tool's own probe, still under the
protocol whitelist.

The layout behind the magic bytes is walked too, header by header, before any parser holds it: a WAV or
AIFF of a few chunks and a FLAC of a few metadata blocks are what real files are, and mutagen keeps every
chunk and every block (pictures included) in memory."""
from __future__ import annotations

from pathlib import Path

HEAD_BYTES = 16
# The ffmpeg demuxer each claimed extension is read with.
DEMUXERS = {".flac": "flac", ".wav": "wav", ".aif": "aiff", ".aiff": "aiff", ".mp3": "mp3"}
PROTOCOLS = ["-protocol_whitelist", "file,pipe"]
# A real file has a handful of RIFF/IFF chunks or FLAC metadata blocks; past this many it is not one.
MAX_CHUNKS = 4096
# All the FLAC metadata blocks together, as their headers declare them: tags and pictures are read whole
# by the tag parser. Flackey's own artwork is at most 10 MB (tag.MAX_ARTWORK_BYTES).
MAX_FLAC_METADATA_BYTES = 64 * 1024 * 1024


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
    check_layout(path, claimed)
    return claimed


def check_layout(path: Path, fmt: str) -> None:
    """Raise WrongFormat when `path`'s chunks (WAV, AIFF) or metadata blocks (FLAC) are more, or declare
    more, than MAX_CHUNKS and MAX_FLAC_METADATA_BYTES allow. Only headers are read, at most MAX_CHUNKS of
    them. A file that ends early or whose headers stop making sense passes: that is damage, which the
    decode judges, not a disguise."""
    with open(path, "rb") as f:
        if fmt == "flac":
            problem = _flac_layout(f)
        elif fmt in ("wav", "aiff"):
            problem = _chunk_layout(f, little=fmt == "wav")
        else:
            return
    if problem:
        raise WrongFormat(f"{path.name} is not a valid {fmt} file: {problem}")


def _chunk_layout(f, *, little: bool) -> str | None:
    """RIFF/RF64 (little-endian sizes) or IFF (big-endian): a 12-byte header, then chunks of an 8-byte header
    and a body padded to an even length."""
    if _container(f.read(12)) not in ("wav", "aiff"):
        return None                            # not this layout at all: the parser says so on its own
    end = f.seek(0, 2)
    offset, count = 12, 0
    while offset + 8 <= end:
        count += 1
        if count > MAX_CHUNKS:
            return f"more than {MAX_CHUNKS} chunks"
        f.seek(offset + 4)
        size = int.from_bytes(f.read(4), "little" if little else "big")
        offset += 8 + size + (size & 1)
    return None


def _flac_layout(f) -> str | None:
    """'fLaC' (behind any ID3 tag), then metadata blocks of a 4-byte header -- last-block flag and type in
    the first byte, a 24-bit length -- until the one flagged last."""
    start = _id3_end(f.read(HEAD_BYTES))
    f.seek(start)
    if f.read(4) != b"fLaC":
        return None
    count = total = 0
    while (head := f.read(4)) and len(head) == 4:
        count += 1
        total += int.from_bytes(head[1:], "big")
        if count > MAX_CHUNKS:
            return f"more than {MAX_CHUNKS} metadata blocks"
        if total > MAX_FLAC_METADATA_BYTES:
            return f"more than {MAX_FLAC_METADATA_BYTES} bytes of metadata"
        if head[0] & 0x80:
            return None
        f.seek(int.from_bytes(head[1:], "big"), 1)
    return None


def arrived_empty(path: Path) -> bool:
    """Nothing arrived to judge: a file shorter than any header, or nothing but zero bytes -- what a transfer
    that never wrote its data leaves behind. A truncated transfer of a real file is not this: it keeps its
    header and still reads as what it claims. So anything else under the wrong name is a disguise (an AAC,
    an Ogg, an MP3 called .flac), not damage (issue #97)."""
    with open(path, "rb") as f:
        chunk = f.read(HEAD_BYTES)
        if len(chunk) < HEAD_BYTES:
            return True
        while chunk:
            if chunk.strip(b"\0"):
                return False
            chunk = f.read(1 << 20)
    return True


def input_args(path: Path) -> list[str]:
    """The ffmpeg/ffprobe options that go right before `-i path` (or before `path` for ffprobe)."""
    fmt = demuxer(path)
    return PROTOCOLS + (["-f", fmt] if fmt else [])
