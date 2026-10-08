from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadCancelled

log = logging.getLogger(__name__)

# Every entry of a pasted playlist becomes a request row, so an unbounded listing is an unbounded queue.
# Generous: a DJ's longest real playlist is a few hundred tracks.
MAX_PLAYLIST_ENTRIES = 500
# The reference fetch (issue #67) is one track's audio. Thirty minutes is the same ceiling the lossless
# rules put on a track; past it the link is a mix, a stream or an album upload, and none of those is a
# reference for one recording.
MAX_REFERENCE_S = 30 * 60
# Opus audio for thirty minutes is ~30 MB; this leaves room for a lossless or high-bitrate stream while
# keeping one hostile link from filling the disk.
MAX_REFERENCE_BYTES = 200 * 1024 * 1024


class YouTubeError(Exception):
    pass


class ReferenceAborted(DownloadCancelled):
    """Raised from the progress hook to stop a reference download. A `DownloadCancelled` because yt-dlp
    lets that one through untouched; an OSError there would be rewrapped as an unavailable video."""


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
        title = data.get("title") or "Playlist"
        entries = [e for e in data.get("entries") or [] if e]
        if len(entries) > MAX_PLAYLIST_ENTRIES:
            # The backstop for `playlistend`: whatever yt-dlp returned, no more than this many rows.
            log.warning("playlist %r has %d entries; only the first %d are read",
                        title, len(entries), MAX_PLAYLIST_ENTRIES)
            entries = entries[:MAX_PLAYLIST_ENTRIES]
        elif (data.get("playlist_count") or 0) > len(entries) == MAX_PLAYLIST_ENTRIES:
            log.warning("playlist %r has %d entries; only the first %d are read",
                        title, data["playlist_count"], MAX_PLAYLIST_ENTRIES)
        return title, [entry(e) for e in entries]
    return data.get("title") or "", [entry(data)]


YOUTUBE_FETCH_TIMEOUT_S = 120


def ytdlp_available() -> bool:
    """Always true, and asserted rather than assumed. yt-dlp ships inside the app as a library, so the
    setup screen must not go looking for a `yt-dlp` binary on the PATH: in a packaged build there is
    never one there, and a red row against a dependency the app is carrying reads as "broken" to
    someone who has just opened it for the first time."""
    return YoutubeDL is not None


# The same switches the command line used: the flat listing of a playlist, and no download. `playlistend`
# stops yt-dlp paging through a listing past the cap rather than reading it all and dropping the rest.
_YDL_OPTS = {"extract_flat": True, "quiet": True, "no_warnings": True, "skip_download": True,
             "playlistend": MAX_PLAYLIST_ENTRIES}


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


_LIVE = {"is_live", "is_upcoming", "post_live"}


def _reference_filter(rejected: list[str]):
    """A yt-dlp `match_filter` that refuses what cannot be one track's reference: a live or upcoming
    stream, a video of unknown length, or one past MAX_REFERENCE_S. yt-dlp skips a refused video
    without raising, so the reason is kept in `rejected` for `_download_audio` to report."""
    def accept(info: dict, *, incomplete: bool = False) -> str | None:
        reason = None
        duration = info.get("duration")
        if info.get("is_live") or info.get("live_status") in _LIVE:
            reason = "the link is a live stream, not a track"
        elif duration is None:
            # yt-dlp's first look at an entry can come before its length is known; only the full record
            # is judged on it.
            reason = None if incomplete else "the video's length is unknown"
        elif duration > MAX_REFERENCE_S:
            reason = f"the video is too long for a reference ({round(duration)}s > {MAX_REFERENCE_S}s)"
        if reason:
            rejected.append(reason)
        return reason
    return accept


def _abort_hook(deadline: float):
    """A progress hook that stops the transfer. `asyncio.wait_for` stops waiting on the worker thread but
    cannot stop the thread, and `max_filesize` only fires when the server declares a length up front;
    this runs inside the download itself, so it bounds both the time and the bytes regardless."""
    def hook(status: dict) -> None:
        if time.monotonic() > deadline:
            raise ReferenceAborted(f"yt-dlp timed out after {YOUTUBE_FETCH_TIMEOUT_S}s fetching the audio")
        if (status.get("downloaded_bytes") or 0) > MAX_REFERENCE_BYTES:
            raise ReferenceAborted(f"the audio is larger than {MAX_REFERENCE_BYTES // (1024 * 1024)} MB")
    return hook


def _download_audio(url: str, dest_dir: Path) -> Path:
    """Blocking. The best audio-only stream, whatever container YouTube serves (opus in webm, usually):
    fpcalc decodes it directly, so nothing is transcoded."""
    rejected: list[str] = []
    opts = {**_AUDIO_OPTS, "outtmpl": str(dest_dir / "reference.%(ext)s"),
            "max_filesize": MAX_REFERENCE_BYTES, "match_filter": _reference_filter(rejected),
            "progress_hooks": [_abort_hook(time.monotonic() + YOUTUBE_FETCH_TIMEOUT_S)]}
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        path = Path(ydl.prepare_filename(info))
    if not path.exists():
        # A refused video, or one `max_filesize` turned away: yt-dlp says so on screen and returns.
        raise YouTubeError(rejected[-1] if rejected else "yt-dlp downloaded no audio")
    return path


def _discard_partial(dest_dir: Path) -> None:
    """What an aborted download leaves behind (yt-dlp keeps its `.part` file to resume from)."""
    for p in dest_dir.glob("reference.*"):
        p.unlink(missing_ok=True)


async def fetch_audio(url: str, dest_dir: Path) -> Path:
    """The video's audio on disk, for fingerprinting against (issue #67). Same library, same timeout and
    same error shape as `fetch_youtube`; the caller deletes the file when it has what it needs."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    try:
        return await asyncio.wait_for(asyncio.to_thread(_download_audio, url, dest_dir),
                                      timeout=YOUTUBE_FETCH_TIMEOUT_S)
    except TimeoutError:
        # The thread is still running here; its own deadline hook stops it, and the partial file it
        # leaves is the caller's request folder to clear.
        raise YouTubeError(f"yt-dlp timed out after {YOUTUBE_FETCH_TIMEOUT_S}s fetching the audio") from None
    except YouTubeError:
        _discard_partial(dest_dir)
        raise
    except Exception as e:
        _discard_partial(dest_dir)
        raise YouTubeError(str(e).strip()[-500:] or "yt-dlp failed") from e
