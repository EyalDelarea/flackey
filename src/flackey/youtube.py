from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass


class YouTubeError(Exception):
    pass


@dataclass
class YouTubeEntry:
    url: str
    title: str
    uploader: str | None
    duration_s: int | None


def parse_ytdlp_json(data: dict) -> tuple[str, list[YouTubeEntry]]:
    def entry(e: dict) -> YouTubeEntry:
        url = e.get("url") or e.get("webpage_url") or f"https://www.youtube.com/watch?v={e['id']}"
        if url.startswith("http") and "youtube.com/watch" not in url and "youtu.be" not in url and e.get("id"):
            url = f"https://www.youtube.com/watch?v={e['id']}"
        d = e.get("duration")
        return YouTubeEntry(url=url, title=e.get("title") or "", uploader=e.get("uploader") or e.get("channel"),
                            duration_s=None if d is None else round(d))

    if data.get("_type") == "playlist":
        return data.get("title") or "Playlist", [entry(e) for e in data.get("entries") or [] if e]
    return data.get("title") or "", [entry(data)]


YOUTUBE_FETCH_TIMEOUT_S = 120


async def fetch_youtube(url: str) -> tuple[str, list[YouTubeEntry]]:
    proc = await asyncio.create_subprocess_exec(
        "yt-dlp", "--dump-single-json", "--flat-playlist", "--no-warnings", url,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=YOUTUBE_FETCH_TIMEOUT_S)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise YouTubeError(f"yt-dlp timed out after {YOUTUBE_FETCH_TIMEOUT_S}s") from None
    if proc.returncode != 0:
        raise YouTubeError(err.decode(errors="replace").strip()[-500:] or "yt-dlp failed")
    try:
        return parse_ytdlp_json(json.loads(out))
    except (json.JSONDecodeError, KeyError) as e:
        raise YouTubeError(f"unexpected yt-dlp output: {e}") from e
