from __future__ import annotations

import datetime as dt
import re

import httpx
from fastapi import APIRouter

from .. import __version__

RELEASES_URL = "https://api.github.com/repos/EyalDelarea/flackey/releases?per_page=10"


def _version_tuple(v: str) -> tuple[int, int, int]:
    m = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", v.strip())
    if not m:
        return (0, 0, 0)
    return tuple(int(p) for p in m.groups())


def _size_mb(size: int | None) -> str | None:
    return f"{size / 1e6:.1f} MB" if size else None


def router() -> APIRouter:
    r = APIRouter(prefix="/api")

    @r.get("/update")
    async def update() -> dict:
        try:
            async with httpx.AsyncClient(timeout=5, follow_redirects=True) as client:
                res = await client.get(RELEASES_URL)
                res.raise_for_status()
            releases = res.json()
        except (httpx.HTTPError, ValueError):
            return {"ok": False, "current": __version__, "available": False,
                    "error": "Could not check for updates."}
        if not isinstance(releases, list):
            return {"ok": False, "current": __version__, "available": False,
                    "error": "Release feed did not look right."}
        latest = next((item for item in releases if isinstance(item, dict) and not item.get("draft")), None)
        assets = latest.get("assets", []) if latest else []
        installer = next((a for a in assets if isinstance(a, dict) and a.get("name") == "Flackey.pkg"), None)
        tag = str(latest.get("tag_name") or "") if latest else ""
        latest_version = tag.removeprefix("v")
        available = bool(installer and _version_tuple(latest_version) > _version_tuple(__version__))
        published = latest.get("published_at") if latest else None
        date = None
        if isinstance(published, str):
            try:
                date = dt.datetime.fromisoformat(published).date().isoformat()
            except ValueError:
                date = published
        return {"ok": True, "current": __version__, "available": available,
                "latest": latest_version or None,
                "url": installer.get("browser_download_url") if installer else None,
                "size": installer.get("size") if installer else None,
                "size_label": _size_mb(installer.get("size") if installer else None),
                "published_at": published, "published_date": date,
                "prerelease": bool(latest.get("prerelease")) if latest else False}

    return r
