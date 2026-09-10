from __future__ import annotations

import asyncio
from dataclasses import dataclass

from yt_dlp import YoutubeDL


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


def ytdlp_available() -> bool:
    """Always true, and asserted rather than assumed. yt-dlp ships inside the app as a library, so the
    setup screen must not go looking for a `yt-dlp` binary on the PATH: in a packaged build there is
    never one there, and a red row against a dependency the app is carrying reads as "broken" to
    someone who has just opened it for the first time."""
    return YoutubeDL is not None


# The same switches the command line used: the flat listing of a playlist, and no download.
_YDL_OPTS = {"extract_flat": True, "quiet": True, "no_warnings": True, "skip_download": True}


def _extract(url: str) -> dict:
    """Blocking: yt-dlp is synchronous, so `fetch_youtube` runs this on a worker thread."""
    with YoutubeDL(dict(_YDL_OPTS)) as ydl:
        info = ydl.extract_info(url, download=False)
        # `sanitize_info` is what `--dump-single-json` prints, so `parse_ytdlp_json` keeps reading
        # exactly the shape it was written against.
        return ydl.sanitize_info(info)


async def fetch_youtube(url: str) -> tuple[str, list[YouTubeEntry]]:
    """Read a link through the yt-dlp library rather than a `yt-dlp` binary on the PATH.

    The binary was a second, separately-installed copy: absent inside a packaged .app, and on a
    developer's machine some other version than the one this project pins. The library is already a
    dependency, so calling it directly removes an external requirement instead of adding one."""
    try:
        data = await asyncio.wait_for(asyncio.to_thread(_extract, url), timeout=YOUTUBE_FETCH_TIMEOUT_S)
    except TimeoutError:
        raise YouTubeError(f"yt-dlp timed out after {YOUTUBE_FETCH_TIMEOUT_S}s") from None
    except Exception as e:  # yt-dlp raises its own DownloadError hierarchy for anything network-side
        raise YouTubeError(str(e).strip()[-500:] or "yt-dlp failed") from e
    try:
        return parse_ytdlp_json(data)
    except (AttributeError, KeyError, TypeError) as e:
        raise YouTubeError(f"unexpected yt-dlp output: {e}") from e
