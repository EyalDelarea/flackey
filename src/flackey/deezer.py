from __future__ import annotations

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
        release_date=data.get("release_date"),
        title_version=version,
        preview_url=data.get("preview") or None,
    )


class DeezerApi:
    BASE = "https://api.deezer.com"

    def __init__(self, client: httpx.AsyncClient | None = None):
        self._client = client

    async def track(self, track_id: int) -> DeezerTrack:
        client = self._client or httpx.AsyncClient(timeout=20)
        try:
            r = await client.get(f"{self.BASE}/track/{track_id}")
        except httpx.HTTPError as e:
            raise DeezerError(str(e)) from e
        finally:
            if self._client is None:
                await client.aclose()
        if r.status_code != 200:
            raise DeezerError(f"deezer http {r.status_code}")
        return parse_track_json(r.json())
