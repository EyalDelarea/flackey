from __future__ import annotations

import struct
from pathlib import Path

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


def _write_wav_info(path: Path, values: dict[str, str]) -> None:
    raw = path.read_bytes()
    if len(raw) < 12 or raw[:4] != b"RIFF" or raw[8:12] != b"WAVE":
        raise TagError(f"{path} is not a RIFF/WAVE file")
    chunks: list[tuple[bytes, bytes]] = []
    pos = 12
    inserted = False
    info = _info_list(values)
    while pos + 8 <= len(raw):
        kind = raw[pos:pos + 4]
        size = struct.unpack("<I", raw[pos + 4:pos + 8])[0]
        end = pos + 8 + size
        if end > len(raw):
            raise TagError(f"{path} has a truncated RIFF chunk")
        payload = raw[pos + 8:end]
        pos = end + (size % 2)
        if kind == b"LIST" and payload[:4] == b"INFO":
            continue
        if kind == b"data" and not inserted:
            chunks.append((b"_RAW", info))
            inserted = True
        chunks.append((kind, payload))
    if not inserted:
        chunks.append((b"_RAW", info))
    body = b"WAVE" + b"".join(payload if kind == b"_RAW" else _riff_chunk(kind, payload) for kind, payload in chunks)
    path.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)


def _read_wav_info(path: Path) -> dict[str, str]:
    raw = path.read_bytes()
    if len(raw) < 12 or raw[:4] != b"RIFF" or raw[8:12] != b"WAVE":
        return {}
    out: dict[str, str] = {}
    pos = 12
    while pos + 8 <= len(raw):
        kind = raw[pos:pos + 4]
        size = struct.unpack("<I", raw[pos + 4:pos + 8])[0]
        payload = raw[pos + 8:pos + 8 + size]
        pos = pos + 8 + size + (size % 2)
        if kind != b"LIST" or payload[:4] != b"INFO":
            continue
        sub = 4
        while sub + 8 <= len(payload):
            code = payload[sub:sub + 4].decode("ascii", errors="ignore")
            n = struct.unpack("<I", payload[sub + 4:sub + 8])[0]
            value = payload[sub + 8:sub + 8 + n].rstrip(b"\0").decode(INFO_TEXT_ENCODING, errors="replace")
            out[code] = value
            sub = sub + 8 + n + (n % 2)
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


async def fetch_artwork(url: str, client: httpx.AsyncClient | None = None) -> bytes | None:
    own = client is None
    c = client or httpx.AsyncClient(timeout=20, follow_redirects=True)
    try:
        r = await c.get(url)
        if r.status_code == 200 and r.content:
            return r.content
        return None
    except httpx.HTTPError:
        return None
    finally:
        if own:
            await c.aclose()
