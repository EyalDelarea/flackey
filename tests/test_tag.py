import struct
import subprocess
from pathlib import Path

import httpx
import pytest
from mutagen.id3 import ID3

from flackey.models import CatalogTrack, Verdict
from flackey.tag import (
    TagError,
    comment_for,
    fetch_artwork,
    read_tags,
    write_tags,
)
from tests.conftest import requires_ffmpeg

CT = CatalogTrack(id=16552105, isrc="UKU932231081", artist="Astral Projection", title="Into the Void",
                  mix_name="Original Mix", label="Sacred Technology", genre="Psy-Trance", sub_genre="Goa Trance",
                  catalog_number="SACTEC169", release_name="Into the Void", release_date="2022-06-03",
                  bpm=142, key="A Major", duration_ms=442816)
V = Verdict(True, "mp3", 320, 19800, "ok")
PNG_1x1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d4944415478da63f8cfc0"
    "0000030101009a1c2b0f0000000049454e44ae426082")
JPEG_HEAD = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + b"\0" * 32   # the magic is all fetch_artwork reads


def _make(tmp_path: Path, ext: str) -> Path:
    p = tmp_path / f"t.{ext}"
    codec = {"mp3": ["-c:a", "libmp3lame", "-b:a", "320k"], "flac": ["-c:a", "flac"],
             "wav": ["-c:a", "pcm_s16le"], "aiff": ["-c:a", "pcm_s16be"]}[ext]
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "anoisesrc=duration=1:sample_rate=44100", *codec, str(p)], check=True)
    return p


@requires_ffmpeg
@pytest.mark.parametrize("ext", ["mp3", "flac", "wav", "aiff"])
def test_write_and_read_tags(tmp_path: Path, ext: str):
    p = _make(tmp_path, ext)
    write_tags(p, CT, V, PNG_1x1, "image/png")
    t = read_tags(p)
    assert t["title"] == "Into the Void" and t["artist"] == "Astral Projection"
    assert t["album"] == "Into the Void" and t["albumartist"] == "Astral Projection"
    assert t["genre"] == "Psy-Trance" and t["label"] == "Sacred Technology"
    assert t["catalognumber"] == "SACTEC169" and t["date"] == "2022-06-03" and t["year"] == "2022"
    assert t["isrc"] == "UKU932231081"
    # never written: Beatport's BPM and key are unreliable for old catalog (82 BPM, "D Major" for a 137 BPM
    # D minor track); Rekordbox analyzes both on import
    assert t["bpm"] == "" and t["key"] == ""
    assert t["mix"] == "Original Mix" and t["has_artwork"] == "yes"
    assert t["comment"] == "flackey: verified 320 kbps · cutoff 19.8 kHz · beatport 16552105 · via Deezer"


@requires_ffmpeg
def test_wav_carries_rekordbox_documented_riff_info(tmp_path: Path):
    p = _make(tmp_path, "wav")
    write_tags(p, CT, V, PNG_1x1, "image/png")
    raw = p.read_bytes()
    assert b"LIST" in raw and b"INFO" in raw
    for marker in (b"INAM", b"IART", b"IPRD", b"IGNR", b"ICRD", b"ICMT", b"ISRC", b"IPUB", b"ICAT", b"IMIX"):
        assert marker in raw
    assert read_tags(p)["title"] == "Into the Void"


@requires_ffmpeg
def test_remix_goes_into_title(tmp_path: Path):
    p = _make(tmp_path, "mp3")
    ct = CatalogTrack(**{**CT.__dict__, "mix_name": "Vini Vici Remix"})
    write_tags(p, ct, V, None)
    t = read_tags(p)
    assert t["title"] == "Into the Void (Vini Vici Remix)" and t["has_artwork"] == "no"


@requires_ffmpeg
def test_missing_optional_fields(tmp_path: Path):
    p = _make(tmp_path, "mp3")
    ct = CatalogTrack(id=1, artist="A", title="T", mix_name="Original Mix", label="L", genre="G")
    write_tags(p, ct, Verdict(True, "mp3", 320, 19800, "ok"), None)
    t = read_tags(p)
    assert t["bpm"] == "" and t["key"] == "" and t["year"] == ""


def test_unsupported_extension(tmp_path: Path):
    p = tmp_path / "x.ogg"
    p.write_bytes(b"OggS")
    with pytest.raises(TagError):
        write_tags(p, CT, V, None)


def test_comment_for():
    assert comment_for(V, CT) == "flackey: verified 320 kbps · cutoff 19.8 kHz · beatport 16552105 · via Deezer"


@requires_ffmpeg
def test_read_tags_on_untagged_mp3_returns_empty(tmp_path: Path):
    p = _make(tmp_path, "mp3")
    ID3(str(p)).delete(str(p))
    t = read_tags(p)
    assert t["title"] == "" and t["artist"] == "" and t["comment"] == "" and t["has_artwork"] == "no"


async def test_fetch_artwork_returns_body_on_200():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=PNG_1x1)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        data = await fetch_artwork("https://example.com/art.jpg", client)
    assert data == PNG_1x1


async def test_fetch_artwork_accepts_a_jpeg():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=JPEG_HEAD)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await fetch_artwork("https://example.com/art.jpg", client) == JPEG_HEAD


async def test_fetch_artwork_refuses_a_body_that_is_not_an_image():
    """The catalog's URL is someone else's server; whatever it answers goes into every tag we write."""
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"<html>not a cover</html>")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await fetch_artwork("https://example.com/art.jpg", client) is None


async def test_fetch_artwork_refuses_a_plain_http_url_without_asking():
    asked: list = []

    async def handler(request: httpx.Request) -> httpx.Response:
        asked.append(request.url)
        return httpx.Response(200, content=PNG_1x1)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await fetch_artwork("http://example.com/art.jpg", client) is None
    assert asked == []


async def test_fetch_artwork_refuses_a_redirect_down_to_plain_http():
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.scheme == "https":
            return httpx.Response(302, headers={"location": "http://example.com/art.jpg"})
        return httpx.Response(200, content=PNG_1x1)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True) as client:
        assert await fetch_artwork("https://example.com/art.jpg", client) is None


async def test_fetch_artwork_abandons_a_body_past_the_cap(monkeypatch: pytest.MonkeyPatch):
    """Streamed with no declared length, so only counting the bytes can stop it: the transfer is dropped
    at the ceiling instead of being read whole and measured afterwards."""
    import flackey.tag as tag_mod

    monkeypatch.setattr(tag_mod, "MAX_ARTWORK_BYTES", 1000)
    sent = 0

    async def endless():
        nonlocal sent
        yield PNG_1x1
        for _ in range(1000):
            sent += 1
            yield b"\0" * 100

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=endless())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await fetch_artwork("https://example.com/art.jpg", client) is None
    assert sent < 20, "the body was read past the cap"


async def test_fetch_artwork_refuses_a_declared_length_past_the_cap(monkeypatch: pytest.MonkeyPatch):
    import flackey.tag as tag_mod

    monkeypatch.setattr(tag_mod, "MAX_ARTWORK_BYTES", 1000)

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=PNG_1x1 + b"\0" * 2000)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await fetch_artwork("https://example.com/art.jpg", client) is None


async def test_fetch_artwork_returns_none_on_non_200():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        data = await fetch_artwork("https://example.com/art.jpg", client)
    assert data is None


async def test_fetch_artwork_returns_none_on_transport_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection failed", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        data = await fetch_artwork("https://example.com/art.jpg", client)
    assert data is None


async def test_fetch_artwork_does_not_close_passed_in_client():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=PNG_1x1)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await fetch_artwork("https://example.com/art.jpg", client)
        assert client.is_closed is False


async def test_fetch_artwork_owns_and_closes_client_when_none_passed(monkeypatch: pytest.MonkeyPatch):
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=PNG_1x1)

    real_async_client = httpx.AsyncClient
    created: list[httpx.AsyncClient] = []

    def fake_async_client(*args, **kwargs):
        client = real_async_client(*args, **{**kwargs, "transport": httpx.MockTransport(handler)})
        created.append(client)
        return client

    monkeypatch.setattr(httpx, "AsyncClient", fake_async_client)
    data = await fetch_artwork("https://example.com/art.jpg")
    assert data == PNG_1x1
    assert created[0].is_closed is True


@requires_ffmpeg
@pytest.mark.parametrize("ext", ["mp3", "wav", "aiff"])
def test_id3_is_written_as_v23_with_a_year_rekordbox_can_read(tmp_path: Path, ext: str):
    """Rekordbox skips a 2.4 tag outright: the track imports with the filename as its title and no artist,
    album, genre or cover. The tag itself was valid -- ffprobe read ours back in full -- so the fix is the
    version, not the content. Asserted against the bytes, because mutagen models every tag it reads as 2.4
    and would report a 2.3 file's TYER back to us as a TDRC."""
    p = _make(tmp_path, ext)
    write_tags(p, CT, V, PNG_1x1, "image/png")
    raw = p.read_bytes()
    head = raw.find(b"ID3\x03\x00")
    assert head >= 0, "no ID3v2.3 header on disk"
    body = raw[head:head + 4000]
    # mutagen writes TDRC alone when it saves 2.3, and a 2.3 reader skips it: TYER is ours.
    assert b"TYER" in body and b"APIC" in body
    assert read_tags(p)["date"] == "2022-06-03" and read_tags(p)["has_artwork"] == "yes"


# ---- the RIFF INFO rewrite, streamed ---------------------------------------
# A lossless WAV is hundreds of megabytes, and the rewrite used to hold it in memory -- twice, counting
# the read-back. It now walks the chunk headers and copies each payload through a bounded buffer. The
# bytes it produces must be exactly what the in-memory version produced: `_legacy_*` below is that
# version, kept verbatim as the reference.

def _legacy_write_wav_info(path: Path, values: dict[str, str]) -> None:
    from flackey.tag import _info_list, _riff_chunk

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


def _legacy_read_wav_info(path: Path) -> dict[str, str]:
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
            value = payload[sub + 8:sub + 8 + n].rstrip(b"\0").decode("utf-8", errors="replace")
            out[code] = value
            sub = sub + 8 + n + (n % 2)
    return out


def _chunk(kind: bytes, payload: bytes, pad: bool = True) -> bytes:
    return kind + struct.pack("<I", len(payload)) + payload + (b"\xaa" if pad and len(payload) % 2 else b"")


def _wav(*chunks: bytes, trailing: bytes = b"") -> bytes:
    body = b"WAVE" + b"".join(chunks)
    return b"RIFF" + struct.pack("<I", len(body)) + body + trailing


FMT = _chunk(b"fmt ", struct.pack("<HHIIHH", 1, 2, 44100, 176400, 4, 16))
OLD_INFO = _chunk(b"LIST", b"INFO" + _chunk(b"INAM", b"Old title\0") + _chunk(b"IART", b"Old\0"))
WAV_SHAPES = {
    "plain": _wav(FMT, _chunk(b"data", b"\x01\x02" * 50)),
    "odd chunks, padded with junk": _wav(FMT, _chunk(b"junk", b"abc"), _chunk(b"data", b"xyz")),
    "info before and after data": _wav(FMT, OLD_INFO, _chunk(b"data", b"\0" * 8), OLD_INFO),
    "a list that is not info": _wav(FMT, _chunk(b"LIST", b"adtl" + b"\0" * 6), _chunk(b"data", b"\0" * 4)),
    "a list too short to name itself": _wav(FMT, _chunk(b"LIST", b"IN"), _chunk(b"data", b"\0" * 4)),
    "no data chunk": _wav(FMT, OLD_INFO),
    "no final pad": _wav(FMT, _chunk(b"data", b"abcde", pad=False)),
    "trailing junk": _wav(FMT, _chunk(b"data", b"\0" * 6), trailing=b"\x01\x02\x03"),
}
VALUES = {"title": "Into the Void", "artist": "Astral Projection", "album": "Into the Void",
          "genre": "Psy-Trance", "date": "2022-06-03", "comment": "c", "isrc": "", "label": "Sacred Technology",
          "catalognumber": "SACTEC169", "mix": "Original Mix — ünïcode"}


@pytest.mark.parametrize("shape", sorted(WAV_SHAPES))
def test_the_streamed_wav_rewrite_writes_the_same_bytes_as_the_in_memory_one(tmp_path: Path, shape: str):
    from flackey.tag import _read_wav_info, _write_wav_info

    old, new = tmp_path / "old.wav", tmp_path / "new.wav"
    old.write_bytes(WAV_SHAPES[shape])
    new.write_bytes(WAV_SHAPES[shape])
    _legacy_write_wav_info(old, VALUES)
    _write_wav_info(new, VALUES)
    assert new.read_bytes() == old.read_bytes()
    assert _read_wav_info(new) == _legacy_read_wav_info(new)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["new.wav", "old.wav"], "a temp file was left"


@pytest.mark.parametrize("shape", sorted(WAV_SHAPES))
def test_the_streamed_wav_read_matches_the_in_memory_one(tmp_path: Path, shape: str):
    from flackey.tag import _read_wav_info

    p = tmp_path / "t.wav"
    p.write_bytes(WAV_SHAPES[shape])
    assert _read_wav_info(p) == _legacy_read_wav_info(p)


def test_the_wav_read_tolerates_a_truncated_chunk_the_way_it_always_did(tmp_path: Path):
    from flackey.tag import _read_wav_info

    p = tmp_path / "t.wav"
    p.write_bytes(_wav(FMT, OLD_INFO, b"data" + struct.pack("<I", 10_000) + b"\0" * 10))
    assert _read_wav_info(p) == _legacy_read_wav_info(p) == {"INAM": "Old title", "IART": "Old"}
    p.write_bytes(b"not a riff file at all")
    assert _read_wav_info(p) == {}


def test_a_file_that_is_not_riff_wave_is_refused_with_the_same_error(tmp_path: Path):
    from flackey.tag import _write_wav_info

    p = tmp_path / "t.wav"
    for raw in (b"RIFF", b"RIFX\0\0\0\0WAVE", b"RIFF\0\0\0\0AVI "):
        p.write_bytes(raw)
        with pytest.raises(TagError, match="is not a RIFF/WAVE file"):
            _write_wav_info(p, VALUES)
        assert p.read_bytes() == raw


def test_a_truncated_chunk_is_refused_and_the_file_left_alone(tmp_path: Path):
    from flackey.tag import _write_wav_info

    raw = _wav(FMT, b"data" + struct.pack("<I", 10_000) + b"\0" * 10)
    p = tmp_path / "t.wav"
    p.write_bytes(raw)
    with pytest.raises(TagError, match="has a truncated RIFF chunk"):
        _write_wav_info(p, VALUES)
    assert p.read_bytes() == raw
    assert [x.name for x in tmp_path.iterdir()] == ["t.wav"], "the half-written temp file was left behind"


def test_the_wav_rewrite_never_reads_the_audio_in_one_go(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Deterministic rather than a memory measurement: every read the rewrite and the read-back make is
    recorded, and none may be larger than the copy buffer, against a data chunk many times its size."""
    import builtins

    import flackey.tag as tag_mod

    monkeypatch.setattr(tag_mod, "_COPY_CHUNK", 4096)
    reads: list[int] = []
    real_open = builtins.open

    class Spy:
        def __init__(self, f):
            self._f = f

        def read(self, n=-1):
            data = self._f.read(n)
            reads.append(len(data) if n is None or n < 0 else n)
            return data

        def __getattr__(self, name):
            return getattr(self._f, name)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self._f.close()
            return False

    monkeypatch.setattr(tag_mod, "open", lambda *a, **k: Spy(real_open(*a, **k)), raising=False)
    audio = bytes(range(256)) * 1024                         # 256 KiB, 64 copy buffers' worth
    p = tmp_path / "t.wav"
    p.write_bytes(_wav(FMT, _chunk(b"data", audio)))
    tag_mod._write_wav_info(p, VALUES)
    assert tag_mod._read_wav_info(p)["INAM"] == "Into the Void"
    assert reads and max(reads) <= 4096
    assert audio in p.read_bytes()


def test_the_wav_rewrite_keeps_the_file_s_permissions(tmp_path: Path):
    import os
    import stat

    from flackey.tag import _write_wav_info

    p = tmp_path / "t.wav"
    p.write_bytes(WAV_SHAPES["plain"])
    os.chmod(p, 0o644)
    _write_wav_info(p, VALUES)
    assert stat.S_IMODE(p.stat().st_mode) == 0o644
