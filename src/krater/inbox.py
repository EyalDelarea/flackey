from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from .identify import classify, parse_youtube_title
from .models import RequestKind
from .store import Store
from .youtube import YouTubeEntry, YouTubeError, fetch_youtube

log = logging.getLogger(__name__)
NOT_A_LINK = "Paste a YouTube or YouTube Music link: a track or a playlist."
YT_FAIL = "Could not read that link. yt-dlp may need an update."


class BadLink(Exception):
    """The message is plain language, meant to be shown under the paste bar."""


@dataclass
class Submission:
    name: str
    request_ids: list[int] = field(default_factory=list)
    playlist_id: int | None = None
    total: int = 0
    already_in_library: int = 0
    already_queued: int = 0

    def summary(self) -> str:
        if self.playlist_id is None:
            return "Queued"
        detail = [f"{self.already_in_library} already in library"]
        if self.already_queued:
            detail.append(f"{self.already_queued} already queued")
        return f'Queued {len(self.request_ids)} of {self.total} from "{self.name}" ({", ".join(detail)})'


class Inbox:
    def __init__(self, store: Store,
                 youtube: Callable[[str], Awaitable[tuple[str, list[YouTubeEntry]]]] = fetch_youtube):
        self.store, self.youtube = store, youtube

    async def submit(self, text: str) -> Submission:
        kind, url = classify(text)
        if kind == RequestKind.TEXT or url is None:
            raise BadLink(NOT_A_LINK)
        try:
            name, entries = await self.youtube(url)
        except YouTubeError as e:
            log.warning("yt-dlp failed for %s: %s", url, e)
            raise BadLink(YT_FAIL) from e
        if not entries:  # deleted, private, or region-blocked video; empty playlist
            raise BadLink(YT_FAIL)
        if kind == RequestKind.YT_TRACK:
            e = entries[0]
            q = parse_youtube_title(e.title, e.uploader, e.duration_s)
            rid = self.store.add_request(e.title, RequestKind.YT_TRACK, source_url=url, query=q)
            return Submission(name=name, request_ids=[rid], total=1)
        pid = self.store.upsert_playlist(url, name)
        sub = Submission(name=name, playlist_id=pid, total=len(entries))
        for pos, e in enumerate(entries, start=1):
            q = parse_youtube_title(e.title, e.uploader, e.duration_s)
            existing = None
            if q.artist and q.title:
                existing = self.store.find_track_by_meta(q.artist, q.title, q.version or "Original Mix", q.duration_s)
            if existing:
                self.store.add_playlist_track(pid, existing.id, pos)
                sub.already_in_library += 1
                continue
            if self.store.open_request_for_url(e.url):  # the same playlist sent twice while still in flight
                sub.already_queued += 1
                continue
            sub.request_ids.append(self.store.add_request(
                e.title, RequestKind.YT_TRACK, source_url=e.url, playlist_id=pid, playlist_position=pos, query=q))
        return sub
