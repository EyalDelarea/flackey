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
        return httpx.Response(200, content=b"art-bytes")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        data = await fetch_artwork("https://example.com/art.jpg", client)
    assert data == b"art-bytes"


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
        return httpx.Response(200, content=b"art-bytes")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await fetch_artwork("https://example.com/art.jpg", client)
        assert client.is_closed is False


async def test_fetch_artwork_owns_and_closes_client_when_none_passed(monkeypatch: pytest.MonkeyPatch):
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"art-bytes")

    real_async_client = httpx.AsyncClient
    created: list[httpx.AsyncClient] = []

    def fake_async_client(*args, **kwargs):
        client = real_async_client(*args, **{**kwargs, "transport": httpx.MockTransport(handler)})
        created.append(client)
        return client

    monkeypatch.setattr(httpx, "AsyncClient", fake_async_client)
    data = await fetch_artwork("https://example.com/art.jpg")
    assert data == b"art-bytes"
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
