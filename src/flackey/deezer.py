from __future__ import annotations

import asyncio
from dataclasses import dataclass

import httpx


class DeezerError(Exception):
    pass


@dataclass
class DeezerTrack:
    id: int
    isrc: str | None
    title: str
    artist: str
    album: str | None
    duration_s: int
    release_date: str | None
    title_version: str | None
    preview_url: str | None = None
    album_id: int | None = None


def _date(value) -> str | None:
    """Deezer writes 0000-00-00 for a record it has no date for."""
    date = str(value or "").strip()
    return date if date and not date.startswith("0000") else None


def parse_track_json(data: dict) -> DeezerTrack:
    if "error" in data:
        raise DeezerError(str(data["error"]))
    version = (data.get("title_version") or "").strip().strip("()").strip() or None
    title = data.get("title_short") or data.get("title") or ""
    return DeezerTrack(
        id=int(data["id"]),
        isrc=data.get("isrc") or None,
        title=title,
        artist=(data.get("artist") or {}).get("name") or "",
        album=(data.get("album") or {}).get("title"),
        duration_s=int(data.get("duration") or 0),
        release_date=_date(data.get("release_date")),
        title_version=version,
        preview_url=data.get("preview") or None,
        album_id=int((data.get("album") or {}).get("id") or 0) or None,
    )


# Deezer serves its covers at any square size from the image's md5; 1400 is what Beatport's art was
# written at, so a Deezer-tagged file carries the same size of cover as a Beatport-tagged one.
COVER_PX = 1400
_COVER_URL = "https://e-cdns-images.dzcdn.net/images/cover/{md5}/{px}x{px}-000000-80-0-0.jpg"


@dataclass
class DeezerAlbum:
    id: int
    title: str
    label: str | None
    release_date: str | None    # YYYY-MM-DD
    cover_url: str | None


def parse_album_json(data: dict) -> DeezerAlbum:
    if "error" in data:
        raise DeezerError(str(data["error"]))
    md5 = data.get("md5_image")
    cover = _COVER_URL.format(md5=md5, px=COVER_PX) if md5 else None
    return DeezerAlbum(
        id=int(data["id"]),
        title=data.get("title") or "",
        label=(data.get("label") or "").strip() or None,
        release_date=_date(data.get("release_date")),
        cover_url=cover,
    )


class DeezerApi:
    BASE = "https://api.deezer.com"

    def __init__(self, client: httpx.AsyncClient | None = None):
        self._client = client

    async def _get(self, path: str) -> dict:
        client = self._client or httpx.AsyncClient(timeout=20)
        try:
            r = await client.get(f"{self.BASE}/{path}")
        except httpx.HTTPError as e:
            raise DeezerError(str(e)) from e
        finally:
            if self._client is None:
                await client.aclose()
        if r.status_code != 200:
            raise DeezerError(f"deezer http {r.status_code}")
        return r.json()   # a non-JSON 200 raises ValueError, which callers already treat as a failed lookup

    async def track(self, track_id: int) -> DeezerTrack:
        return parse_track_json(await self._get(f"track/{track_id}"))

    async def album(self, album_id: int) -> DeezerAlbum:
        return parse_album_json(await self._get(f"album/{album_id}"))


# Short on purpose: the answer only ever moves a year in the tags, and a track waits on it while filing.
ISRC_TIMEOUT_S = 5.0


async def track_by_isrc(http: httpx.AsyncClient, isrc: str, *, timeout: float = ISRC_TIMEOUT_S) -> DeezerTrack | None:
    """Deezer's record for this ISRC, from the public API (no account), or None when the record it answers
    with is filed under another ISRC. Raises DeezerError for any failed lookup -- including the
    200-with-an-error-body Deezer answers a spent quota or an unknown ISRC ("no data") with -- and for one
    that takes longer than `timeout` in all: httpx's own timeout is per phase, not for the whole call.

    The caller still has to compare lengths: Deezer files DJ-mix and compilation cuts under the original's
    ISRC, and such a record's date is not the recording's."""
    try:
        async with asyncio.timeout(timeout):
            r = await http.get(f"{DeezerApi.BASE}/track/isrc:{isrc}", timeout=timeout)
    except TimeoutError as e:
        raise DeezerError(f"deezer took longer than {timeout:g} s") from e
    except httpx.HTTPError as e:
        raise DeezerError(str(e) or type(e).__name__) from e
    if r.status_code != 200:
        raise DeezerError(f"deezer http {r.status_code}")
    try:
        track = parse_track_json(r.json())
    except (ValueError, KeyError, TypeError) as e:
        raise DeezerError(f"deezer sent no track ({e})") from e
    return track if (track.isrc or "").upper() == isrc.upper() else None
