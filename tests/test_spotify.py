import json

import httpx
import pytest
import respx

from flackey.spotify import (
    SpotifyError,
    fetch_spotify_playlist,
    fetch_spotify_track,
    parse_embed_html,
    parse_playlist_embed_html,
)

ENTITY = {
    "type": "track",
    "name": "Cut To The Feeling",
    "uri": "spotify:track:11dFghVXANMlKmJXsNCbNl",
    "id": "11dFghVXANMlKmJXsNCbNl",
    "title": "Cut To The Feeling",
    "artists": [{"name": "Carly Rae Jepsen", "uri": "spotify:artist:6sFIWsNpZYqfjUpaCgueju"}],
    "duration": 207959,
}
PLAYLIST_ENTITY = {
    "type": "playlist",
    "name": "Today’s Top Hits",
    "id": "37i9dQZF1DXcBWIGoYBM5M",
    "title": "Today’s Top Hits",
    "trackList": [
        {
            "uri": "spotify:track:3h5T5JypYU7huFiVYhv1dr",
            "title": "BbY WOW",
            "subtitle": "KAROL G,\xa0Judeline,\xa0rusowsky",
            "duration": 225834,
            "entityType": "track",
        },
        {
            "uri": "spotify:episode:skip",
            "title": "Not a track",
            "subtitle": "Someone",
            "duration": 1000,
            "entityType": "episode",
        },
    ],
}


def embed_html(entity: dict = ENTITY) -> str:
    data = {"props": {"pageProps": {"state": {"data": {"entity": entity}}}}}
    return f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script>'


def test_parse_embed_html():
    t = parse_embed_html(embed_html(), "https://open.spotify.com/track/11dFghVXANMlKmJXsNCbNl")
    assert (t.id, t.artist, t.title, t.duration_s) == (
        "11dFghVXANMlKmJXsNCbNl", "Carly Rae Jepsen", "Cut To The Feeling", 208)
    assert t.source_url == "https://open.spotify.com/track/11dFghVXANMlKmJXsNCbNl"


def test_parse_embed_html_error():
    with pytest.raises(SpotifyError):
        parse_embed_html("<html></html>", "https://open.spotify.com/track/x")


def test_parse_playlist_embed_html():
    p = parse_playlist_embed_html(embed_html(PLAYLIST_ENTITY),
                                  "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M")
    assert p.title == "Today’s Top Hits"
    assert len(p.tracks) == 1
    assert p.tracks[0].source_url == "https://open.spotify.com/track/3h5T5JypYU7huFiVYhv1dr"
    assert (p.tracks[0].artist, p.tracks[0].title, p.tracks[0].duration_s) == (
        "KAROL G, Judeline, rusowsky", "BbY WOW", 226)


@respx.mock
async def test_fetch_spotify_track_uses_embed_page():
    respx.get("https://open.spotify.com/embed/track/11dFghVXANMlKmJXsNCbNl").mock(
        return_value=httpx.Response(200, text=embed_html()))
    async with httpx.AsyncClient() as c:
        t = await fetch_spotify_track("https://open.spotify.com/track/11dFghVXANMlKmJXsNCbNl?si=abc", c)
    assert t.artist == "Carly Rae Jepsen"


@respx.mock
async def test_fetch_spotify_track_http_error():
    respx.get("https://open.spotify.com/embed/track/1").mock(return_value=httpx.Response(404))
    async with httpx.AsyncClient() as c:
        with pytest.raises(SpotifyError):
            await fetch_spotify_track("https://open.spotify.com/track/1", c)


@respx.mock
async def test_fetch_spotify_playlist_uses_embed_page():
    respx.get("https://open.spotify.com/embed/playlist/37i9dQZF1DXcBWIGoYBM5M").mock(
        return_value=httpx.Response(200, text=embed_html(PLAYLIST_ENTITY)))
    async with httpx.AsyncClient() as c:
        p = await fetch_spotify_playlist("https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M?si=abc", c)
    assert p.tracks[0].artist == "KAROL G, Judeline, rusowsky"
