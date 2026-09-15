from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx


class SpotifyError(Exception):
    pass


@dataclass
class SpotifyTrack:
    id: str
    title: str
    artist: str
    duration_s: int | None
    source_url: str


@dataclass
class SpotifyPlaylist:
    id: str
    title: str
    tracks: list[SpotifyTrack]
    source_url: str


_NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
    re.DOTALL,
)


def _entity_id(url: str, entity_type: str) -> str:
    parts = [part for part in urlparse(url).path.split("/") if part]
    try:
        return parts[parts.index(entity_type) + 1]
    except (ValueError, IndexError) as e:
        raise SpotifyError(f"not a Spotify {entity_type} URL") from e


def _next_data(text: str) -> dict:
    m = _NEXT_DATA_RE.search(text)
    if not m:
        raise SpotifyError("Spotify embed page did not include entity data")
    try:
        return json.loads(html.unescape(m.group(1)))
    except json.JSONDecodeError as e:
        raise SpotifyError(f"unexpected Spotify embed data: {e}") from e


def _entity_from_embed(text: str) -> dict:
    try:
        return _next_data(text)["props"]["pageProps"]["state"]["data"]["entity"]
    except (KeyError, TypeError) as e:
        raise SpotifyError(f"unexpected Spotify embed data: {e}") from e


def _track_from_entity(entity: dict, source_url: str) -> SpotifyTrack:
    artists = entity.get("artists") or []
    duration_ms = entity.get("duration")
    return SpotifyTrack(
        id=entity["id"],
        title=entity.get("title") or entity["name"],
        artist=", ".join(a["name"] for a in artists if a.get("name")),
        duration_s=None if duration_ms is None else round(int(duration_ms) / 1000),
        source_url=source_url,
    )


def _track_from_playlist_item(item: dict) -> SpotifyTrack:
    uri = item["uri"]
    track_id = uri.rsplit(":", 1)[-1]
    duration_ms = item.get("duration")
    return SpotifyTrack(
        id=track_id,
        title=item["title"],
        artist=(item.get("subtitle") or "").replace("\xa0", " "),
        duration_s=None if duration_ms is None else round(int(duration_ms) / 1000),
        source_url=f"https://open.spotify.com/track/{track_id}",
    )


def parse_embed_html(text: str, source_url: str) -> SpotifyTrack:
    try:
        return _track_from_entity(_entity_from_embed(text), source_url)
    except (KeyError, TypeError, ValueError) as e:
        raise SpotifyError(f"unexpected Spotify embed data: {e}") from e


def parse_playlist_embed_html(text: str, source_url: str) -> SpotifyPlaylist:
    try:
        entity = _entity_from_embed(text)
        return SpotifyPlaylist(
            id=entity["id"],
            title=entity.get("title") or entity["name"],
            tracks=[_track_from_playlist_item(t) for t in entity.get("trackList") or [] if t.get("entityType") == "track"],
            source_url=source_url,
        )
    except (KeyError, TypeError, ValueError) as e:
        raise SpotifyError(f"unexpected Spotify embed data: {e}") from e


async def fetch_spotify_track(url: str, client: httpx.AsyncClient | None = None) -> SpotifyTrack:
    track_id = _entity_id(url, "track")
    source_url = f"https://open.spotify.com/track/{track_id}"
    http = client or httpx.AsyncClient(timeout=20, follow_redirects=True)
    try:
        r = await http.get(f"https://open.spotify.com/embed/track/{track_id}")
    except httpx.HTTPError as e:
        raise SpotifyError(str(e)) from e
    finally:
        if client is None:
            await http.aclose()
    if r.status_code != 200:
        raise SpotifyError(f"spotify http {r.status_code}")
    return parse_embed_html(r.text, source_url)


async def fetch_spotify_playlist(url: str, client: httpx.AsyncClient | None = None) -> SpotifyPlaylist:
    playlist_id = _entity_id(url, "playlist")
    source_url = f"https://open.spotify.com/playlist/{playlist_id}"
    http = client or httpx.AsyncClient(timeout=20, follow_redirects=True)
    try:
        r = await http.get(f"https://open.spotify.com/embed/playlist/{playlist_id}")
    except httpx.HTTPError as e:
        raise SpotifyError(str(e)) from e
    finally:
        if client is None:
            await http.aclose()
    if r.status_code != 200:
        raise SpotifyError(f"spotify http {r.status_code}")
    return parse_playlist_embed_html(r.text, source_url)
