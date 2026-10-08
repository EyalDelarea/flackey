from __future__ import annotations

import os
import shutil
import struct
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import httpx
from mutagen.aiff import AIFF
from mutagen.flac import FLAC, Picture
from mutagen.id3 import (
    APIC,
    COMM,
    ID3,
    TALB,
    TBPM,
    TCON,
    TDRC,
    TIT2,
    TKEY,
    TPE1,
    TPE2,
    TPUB,
    TSRC,
    TXXX,
    TYER,
    ID3NoHeaderError,
)
from mutagen.wave import WAVE

from .models import CatalogTrack, Verdict, source_label


class TagError(Exception):
    pass


# 2.3, not mutagen's default of 2.4, because 2.3 is the version every DJ tool reads and nothing we write
# needs 2.4. WAV also gets RIFF INFO below because that is the metadata source Rekordbox documents for
# WAVE files; the ID3 block stays as a best-effort compatibility/artwork fallback for other readers.
# The one frame that does not carry over is TDRC, and TYER stands in for it below.
ID3_VERSION = 3
INFO_TEXT_ENCODING = "utf-8"


def comment_for(verdict: Verdict, catalog: CatalogTrack, source: str = "deezer_bot") -> str:
    kind = f"verified {verdict.bitrate_kbps} kbps" if verdict.fmt == "mp3" else f"verified {verdict.fmt}"
    return (f"flackey: {kind} · cutoff {verdict.cutoff_hz / 1000:.1f} kHz · beatport {catalog.id}"
            f" · via {source_label(source)}")


def _values(catalog: CatalogTrack, verdict: Verdict, source: str = "deezer_bot") -> dict[str, str]:
    return {
        "title": catalog.display_title,
        "artist": catalog.artist,
        "album": catalog.release_name or catalog.title,
        "albumartist": catalog.artist,
        "genre": catalog.genre,
        "label": catalog.label,
        "catalognumber": catalog.catalog_number or "",
        "date": catalog.release_date or "",
        "isrc": catalog.isrc or "",
        # No BPM and no key: Beatport's figures are unreliable for older catalog (82 BPM and "D Major" for a
        # 137 BPM D minor track), and Rekordbox analyzes both on import anyway.
        "bpm": "",
        "key": "",
        "mix": catalog.mix_name,
        "comment": comment_for(verdict, catalog, source),
    }


def _id3_target(path: Path):
    """(container, tags). MP3 saves a bare ID3 block; WAV/AIFF keep ID3 inside their chunk list."""
    ext = path.suffix.lower()
    if ext == ".mp3":
        try:
            ID3(path).delete(path)
        except ID3NoHeaderError:
            pass
        return None, ID3()
    f = WAVE(path) if ext == ".wav" else AIFF(path)
    if f.tags is None:
        f.add_tags()
    f.tags.clear()
    return f, f.tags


def _write_id3(path: Path, v: dict[str, str], artwork: bytes | None, mime: str) -> None:
    container, tags = _id3_target(path)
    tags.add(TIT2(encoding=3, text=v["title"]))
    tags.add(TPE1(encoding=3, text=v["artist"]))
    tags.add(TALB(encoding=3, text=v["album"]))
    tags.add(TPE2(encoding=3, text=v["albumartist"]))
    tags.add(TCON(encoding=3, text=v["genre"]))
    tags.add(TPUB(encoding=3, text=v["label"]))
    if v["date"]:
        # TDRC is a 2.4 frame; a 2.3 reader skips it. TYER is what rekordbox looks for, so write both and
        # let each reader take the one it knows. `read_tags` still prefers the full date from TDRC.
        tags.add(TDRC(encoding=3, text=v["date"]))
        tags.add(TYER(encoding=3, text=v["date"][:4]))
    if v["isrc"]:
        tags.add(TSRC(encoding=3, text=v["isrc"]))
    if v["bpm"]:
        tags.add(TBPM(encoding=3, text=v["bpm"]))
    if v["key"]:
        tags.add(TKEY(encoding=3, text=v["key"]))
    if v["catalognumber"]:
        tags.add(TXXX(encoding=3, desc="CATALOGNUMBER", text=v["catalognumber"]))
    tags.add(TXXX(encoding=3, desc="MIXNAME", text=v["mix"]))
    tags.add(COMM(encoding=3, lang="eng", desc="", text=v["comment"]))
    if artwork:
        tags.add(APIC(encoding=3, mime=mime, type=3, desc="Cover", data=artwork))
    if container is None:
        tags.save(path, v2_version=ID3_VERSION)
    else:
        container.save(v2_version=ID3_VERSION)


def _riff_chunk(kind: bytes, payload: bytes) -> bytes:
    return kind + struct.pack("<I", len(payload)) + payload + (b"\0" if len(payload) % 2 else b"")


def _info_text(value: str) -> bytes:
    return value.encode(INFO_TEXT_ENCODING) + b"\0"


def _info_list(values: dict[str, str]) -> bytes:
    # Rekordbox documents RIFF INFO, not WAV ID3, as its WAVE tag source. These are the standard INFO
    # fields that line up with Flackey's catalog data; richer values remain in the best-effort ID3 block.
    mapping = {"INAM": "title", "IART": "artist", "IPRD": "album", "IGNR": "genre", "ICRD": "date",
               "ICMT": "comment", "ISRC": "isrc", "IPUB": "label", "ICAT": "catalognumber",
               "IMIX": "mix"}
    body = b"INFO" + b"".join(_riff_chunk(k.encode("ascii"), _info_text(values[src]))
                              for k, src in mapping.items() if values[src])
    return _riff_chunk(b"LIST", body)


# A lossless WAV runs to hundreds of megabytes, so the RIFF rewrite never holds one in memory: it walks
# the chunk headers and copies each payload through a buffer this size.
_COPY_CHUNK = 1024 * 1024
# A RIFF INFO list is a few hundred bytes of text. One claiming more is read only this far.
_MAX_INFO_BYTES = 1024 * 1024


def _is_riff_wave(head: bytes) -> bool:
    return len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WAVE"


def _riff_chunks(f, total: int):
    """(kind, size, payload offset) for each chunk header that fits in the file's `total` bytes, walked by
    seeking from one header to the next. Odd-sized chunks are followed by a pad byte; the RIFF size field
    is not trusted -- the physical end of the file is."""
    pos = 12
    while pos + 8 <= total:
        f.seek(pos)
        header = f.read(8)
        kind, size = header[:4], struct.unpack("<I", header[4:8])[0]
        yield kind, size, pos + 8
        pos = pos + 8 + size + (size % 2)


def _peek(f, offset: int, n: int) -> bytes:
    f.seek(offset)
    return f.read(n)


def _copy_exact(src, dst, n: int) -> None:
    """`n` bytes from `src` to `dst`, at most `_COPY_CHUNK` at a time. `shutil.copyfileobj` has no length
    bound: it copies to the end of the file."""
    while n > 0:
        block = src.read(min(n, _COPY_CHUNK))
        if not block:
            raise TagError("the file shrank while it was being rewritten")
        dst.write(block)
        n -= len(block)


def _copy_without_info(src, dst, path: Path, total: int, info: bytes) -> None:
    """Every chunk but the old INFO lists, with the new one just before the first `data` chunk (or at the
    end when there is none). Each chunk is re-emitted with a zero pad byte when its size is odd, which is
    what the in-memory version did."""
    inserted = False
    for kind, size, offset in _riff_chunks(src, total):
        if offset + size > total:
            raise TagError(f"{path} has a truncated RIFF chunk")
        if kind == b"LIST" and _peek(src, offset, min(size, 4)) == b"INFO":
            continue
        if kind == b"data" and not inserted:
            dst.write(info)
            inserted = True
        dst.write(kind + struct.pack("<I", size))
        src.seek(offset)
        _copy_exact(src, dst, size)
        if size % 2:
            dst.write(b"\0")
    if not inserted:
        dst.write(info)


def _write_wav_info(path: Path, values: dict[str, str]) -> None:
    """Replace the file's RIFF INFO list. Written to a temp file beside it and moved over it, so a failure
    part-way leaves the original as it was; the source is closed before the move (Windows refuses to
    replace an open file)."""
    total = path.stat().st_size
    info = _info_list(values)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    tmp = Path(name)
    try:
        with os.fdopen(fd, "wb") as dst, open(path, "rb") as src:
            if not _is_riff_wave(src.read(12)):
                raise TagError(f"{path} is not a RIFF/WAVE file")
            dst.write(b"RIFF\0\0\0\0WAVE")
            _copy_without_info(src, dst, path, total, info)
            body = dst.tell() - 8
            dst.seek(4)
            dst.write(struct.pack("<I", body))
        shutil.copymode(path, tmp)   # mkstemp makes the file 0600; a library file keeps its own mode
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _parse_info(payload: bytes) -> dict[str, str]:
    out: dict[str, str] = {}
    sub = 4
    while sub + 8 <= len(payload):
        code = payload[sub:sub + 4].decode("ascii", errors="ignore")
        n = struct.unpack("<I", payload[sub + 4:sub + 8])[0]
        value = payload[sub + 8:sub + 8 + n].rstrip(b"\0").decode(INFO_TEXT_ENCODING, errors="replace")
        out[code] = value
        sub = sub + 8 + n + (n % 2)
    return out


def _read_wav_info(path: Path) -> dict[str, str]:
    """The RIFF INFO values, reading only the LIST INFO payloads. Never raises on a malformed file: a
    truncated chunk is read as far as the file goes, as it always was."""
    total = path.stat().st_size
    out: dict[str, str] = {}
    with open(path, "rb") as f:
        if not _is_riff_wave(f.read(12)):
            return {}
        for kind, size, offset in _riff_chunks(f, total):
            if kind != b"LIST" or _peek(f, offset, min(size, 4)) != b"INFO":
                continue
            out.update(_parse_info(_peek(f, offset, min(size, _MAX_INFO_BYTES))))
    return out


def _write_wav(path: Path, v: dict[str, str], artwork: bytes | None, mime: str) -> None:
    _write_id3(path, v, artwork, mime)
    _write_wav_info(path, v)


def _write_flac(path: Path, v: dict[str, str], artwork: bytes | None, mime: str) -> None:
    f = FLAC(path)
    f.delete()
    f.clear_pictures()
    mapping = {"TITLE": "title", "ARTIST": "artist", "ALBUM": "album", "ALBUMARTIST": "albumartist",
               "GENRE": "genre", "LABEL": "label", "ORGANIZATION": "label", "CATALOGNUMBER": "catalognumber",
               "DATE": "date", "ISRC": "isrc", "BPM": "bpm", "INITIALKEY": "key", "MIXNAME": "mix",
               "COMMENT": "comment"}
    for k, src in mapping.items():
        if v[src]:
            f[k] = v[src]
    if artwork:
        pic = Picture()
        pic.type = 3
        pic.mime = mime
        pic.data = artwork
        f.add_picture(pic)
    f.save()


ID3_EXTS = {".mp3", ".aiff", ".aif"}  # WAV has ID3 too, but also needs RIFF INFO for Rekordbox.


def write_tags(path: Path, catalog: CatalogTrack, verdict: Verdict, artwork: bytes | None,
               artwork_mime: str = "image/jpeg", source: str = "deezer_bot") -> None:
    v = _values(catalog, verdict, source)
    ext = path.suffix.lower()
    if ext in ID3_EXTS:
        _write_id3(path, v, artwork, artwork_mime)
    elif ext == ".wav":
        _write_wav(path, v, artwork, artwork_mime)
    elif ext == ".flac":
        _write_flac(path, v, artwork, artwork_mime)
    else:
        raise TagError(f"unsupported extension {ext}")


def _id3_tags(path: Path):
    """The ID3 tag object for a path: read-only view for read_tags; see _id3_target for writing."""
    ext = path.suffix.lower()
    if ext == ".mp3":
        try:
            return ID3(path)
        except ID3NoHeaderError:
            return ID3()
    f = WAVE(path) if ext == ".wav" else AIFF(path)
    return f.tags if f.tags is not None else ID3()


def read_tags(path: Path) -> dict[str, str]:
    ext = path.suffix.lower()
    out = {k: "" for k in ("title", "artist", "album", "albumartist", "genre", "label", "catalognumber",
                           "date", "year", "isrc", "bpm", "key", "mix", "comment")}
    if ext in ID3_EXTS or ext == ".wav":
        t = _id3_tags(path)
        def g(frame: str) -> str:
            fr = t.getall(frame)
            return str(fr[0].text[0]) if fr and fr[0].text else ""
        out.update(title=g("TIT2"), artist=g("TPE1"), album=g("TALB"), albumartist=g("TPE2"), genre=g("TCON"),
                   label=g("TPUB"), date=g("TDRC"), isrc=g("TSRC"), bpm=g("TBPM"), key=g("TKEY"))
        for fr in t.getall("TXXX"):
            if fr.desc == "CATALOGNUMBER":
                out["catalognumber"] = str(fr.text[0])
            if fr.desc == "MIXNAME":
                out["mix"] = str(fr.text[0])
        comm = t.getall("COMM")
        out["comment"] = str(comm[0].text[0]) if comm else ""
        out["has_artwork"] = "yes" if t.getall("APIC") else "no"
        if ext == ".wav":
            info = _read_wav_info(path)
            info_map = {"INAM": "title", "IART": "artist", "IPRD": "album", "IGNR": "genre",
                        "ICRD": "date", "ICMT": "comment", "ISRC": "isrc", "IPUB": "label",
                        "ICAT": "catalognumber", "IMIX": "mix"}
            out.update({dst: info[src] for src, dst in info_map.items() if info.get(src)})
    elif ext == ".flac":
        f = FLAC(path)
        def g(k: str) -> str:
            return f[k][0] if k in f else ""
        out.update(title=g("TITLE"), artist=g("ARTIST"), album=g("ALBUM"), albumartist=g("ALBUMARTIST"),
                   genre=g("GENRE"), label=g("LABEL"), catalognumber=g("CATALOGNUMBER"), date=g("DATE"),
                   isrc=g("ISRC"), bpm=g("BPM"), key=g("INITIALKEY"), mix=g("MIXNAME"), comment=g("COMMENT"))
        out["has_artwork"] = "yes" if f.pictures else "no"
    else:
        raise TagError(f"unsupported extension {ext}")
    out["year"] = out["date"][:4] if out["date"] else ""
    return out


# A catalog cover is a few hundred kilobytes; the URL is another server's to answer, and its body ends up
# in every tag written, so it is read only this far.
MAX_ARTWORK_BYTES = 10 * 1024 * 1024
# JPEG and PNG: what the catalog serves, and the two formats every DJ tool shows. Anything else is
# refused rather than written under the `image/jpeg` label `write_tags` gives it by default.
_IMAGE_MAGIC = (b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n")


async def _read_capped(r: httpx.Response, cap: int) -> bytes | None:
    """The body, or None the moment it passes `cap` -- the rest is never pulled off the socket."""
    declared = r.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > cap:
        return None
    body = bytearray()
    async for block in r.aiter_bytes():
        body += block
        if len(body) > cap:
            return None
    return bytes(body)


async def fetch_artwork(url: str, client: httpx.AsyncClient | None = None) -> bytes | None:
    """The cover at `url`, or None when there is none worth writing: not https (before the request and
    after any redirect), not a 200, larger than MAX_ARTWORK_BYTES, or not a JPEG or PNG."""
    if urlparse(url).scheme != "https":
        return None
    own = client is None
    c = client or httpx.AsyncClient(timeout=20, follow_redirects=True)
    try:
        async with c.stream("GET", url) as r:
            if r.status_code != 200 or r.url.scheme != "https":
                return None
            body = await _read_capped(r, MAX_ARTWORK_BYTES)
        return body if body and body.startswith(_IMAGE_MAGIC) else None
    except httpx.HTTPError:
        return None
    finally:
        if own:
            await c.aclose()
