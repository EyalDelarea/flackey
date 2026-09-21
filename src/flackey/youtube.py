from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlparse

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


# Audio only, one video: this is the reference fetch, not the playlist read `_YDL_OPTS` is for.
_AUDIO_OPTS = {"format": "bestaudio/best", "quiet": True, "no_warnings": True, "noplaylist": True}


def video_id(url: str) -> str:
    """The `v=` (or youtu.be path) id, which is what names a reference. The URL itself when it is neither,
    so a reference is still labelled with something an owner can recognise."""
    p = urlparse(url)
    if "youtu.be" in p.netloc:
        return p.path.strip("/")
    return parse_qs(p.query).get("v", [""])[0] or url


def _download_audio(url: str, dest_dir: Path) -> Path:
    """Blocking. The best audio-only stream, whatever container YouTube serves (opus in webm, usually):
    fpcalc decodes it directly, so nothing is transcoded."""
    opts = {**_AUDIO_OPTS, "outtmpl": str(dest_dir / "reference.%(ext)s")}
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        return Path(ydl.prepare_filename(info))


async def fetch_audio(url: str, dest_dir: Path) -> Path:
    """The video's audio on disk, for fingerprinting against (issue #67). Same library, same timeout and
    same error shape as `fetch_youtube`; the caller deletes the file when it has what it needs."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    try:
        return await asyncio.wait_for(asyncio.to_thread(_download_audio, url, dest_dir),
                                      timeout=YOUTUBE_FETCH_TIMEOUT_S)
    except TimeoutError:
        raise YouTubeError(f"yt-dlp timed out after {YOUTUBE_FETCH_TIMEOUT_S}s fetching the audio") from None
    except Exception as e:
        raise YouTubeError(str(e).strip()[-500:] or "yt-dlp failed") from e
