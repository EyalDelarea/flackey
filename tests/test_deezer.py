import httpx
import pytest
import respx

from krater.deezer import DeezerApi, DeezerError, parse_track_json

SAMPLE = {"id": 1754956977, "isrc": "UKU932231081", "title": "Into the Void", "title_short": "Into the Void",
          "title_version": "", "duration": 442, "release_date": "2022-06-03",
          "artist": {"name": "Astral Projection"}, "album": {"title": "Into the Void"}}


def test_parse_track_json():
    t = parse_track_json(SAMPLE)
    assert (t.id, t.isrc, t.artist, t.title, t.duration_s) == (
        1754956977, "UKU932231081", "Astral Projection", "Into the Void", 442)
    assert t.album == "Into the Void" and t.release_date == "2022-06-03" and t.title_version is None


def test_parse_track_json_keeps_version():
    t = parse_track_json({**SAMPLE, "title": "Into the Void (Vini Vici Remix)", "title_version": "(Vini Vici Remix)"})
    assert t.title_version == "Vini Vici Remix"


def test_parse_track_json_error():
    with pytest.raises(DeezerError):
        parse_track_json({"error": {"type": "DataException", "message": "no data"}})


@respx.mock
async def test_track_fetches_public_api():
    respx.get("https://api.deezer.com/track/1754956977").mock(return_value=httpx.Response(200, json=SAMPLE))
    async with httpx.AsyncClient() as c:
        t = await DeezerApi(c).track(1754956977)
    assert t.isrc == "UKU932231081"


@respx.mock
async def test_track_http_error():
    respx.get("https://api.deezer.com/track/1").mock(return_value=httpx.Response(500))
    async with httpx.AsyncClient() as c:
        with pytest.raises(DeezerError):
            await DeezerApi(c).track(1)


def test_parse_track_json_keeps_preview_url():
    t = parse_track_json({"id": 1, "title": "T", "artist": {"name": "A"}, "duration": 3, "preview": "https://cdn/x.mp3"})
    assert t.preview_url == "https://cdn/x.mp3"
    assert parse_track_json({"id": 1, "title": "T", "duration": 3, "preview": ""}).preview_url is None
