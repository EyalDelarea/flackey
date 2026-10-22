import asyncio

import httpx
import pytest
import respx

from flackey.deezer import DeezerApi, DeezerError, parse_track_json, track_by_isrc

SAMPLE = {"id": 1754956977, "isrc": "UKU932231081", "title": "Into the Void", "title_short": "Into the Void",
          "title_version": "", "duration": 442, "release_date": "2022-06-03",
          "artist": {"name": "Astral Projection"}, "album": {"title": "Into the Void"}}


def test_parse_track_json():
    t = parse_track_json(SAMPLE)
    assert (t.id, t.isrc, t.artist, t.title, t.duration_s) == (
        1754956977, "UKU932231081", "Astral Projection", "Into the Void", 442)
    assert t.album == "Into the Void" and t.release_date == "2022-06-03" and t.title_version is None


def test_parse_track_json_drops_deezers_no_date_placeholder():
    assert parse_track_json({**SAMPLE, "release_date": "0000-00-00"}).release_date is None


async def test_track_by_isrc_reads_the_record_with_a_short_timeout():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"], seen["timeout"] = request.url.path, request.extensions["timeout"]["read"]
        return httpx.Response(200, json=SAMPLE)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        t = await track_by_isrc(c, "UKU932231081")
    assert (t.release_date, t.duration_s) == ("2022-06-03", 442)
    assert seen == {"path": "/track/isrc:UKU932231081", "timeout": 5.0}


async def test_track_by_isrc_refuses_a_record_filed_under_another_isrc():
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=SAMPLE))) as c:
        assert await track_by_isrc(c, "GBDUW0000059") is None


@pytest.mark.parametrize("response", [
    httpx.Response(200, json={"error": {"type": "Exception", "message": "Quota limit exceeded", "code": 4}}),
    httpx.Response(200, json={"error": {"type": "DataException", "message": "no data", "code": 800}}),
    httpx.Response(500), httpx.Response(200, text="<html>"),
], ids=["quota-body", "no-data-body", "http-500", "not-json"])
async def test_track_by_isrc_raises_for_any_failed_lookup(response):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: response)) as c:
        with pytest.raises(DeezerError):
            await track_by_isrc(c, "UKU932231081")


async def test_track_by_isrc_gives_up_after_its_timeout_in_all():
    """httpx's timeout is per phase: a server that trickles its answer could hold the filing for several of
    them. The whole call has the one budget."""
    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(5)
        return httpx.Response(200, json=SAMPLE)

    async with httpx.AsyncClient(transport=httpx.MockTransport(slow)) as c:
        with pytest.raises(DeezerError, match="longer than 0.05 s"):
            await track_by_isrc(c, "UKU932231081", timeout=0.05)


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
