from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from .identify import classify, parse_version, parse_youtube_title
from .models import Query, RequestKind, RequestState
from .spotify import (
    SpotifyError,
    SpotifyPlaylist,
    SpotifyTrack,
    fetch_spotify_playlist,
    fetch_spotify_track,
)
from .store import Store
from .youtube import YouTubeEntry, YouTubeError, fetch_youtube

log = logging.getLogger(__name__)
NOT_A_LINK = "Paste a YouTube, YouTube Music, or Spotify link."
YT_FAIL = "Could not read that link. yt-dlp may need an update."
SPOTIFY_FAIL = "Could not read that Spotify link."


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
                 youtube: Callable[[str], Awaitable[tuple[str, list[YouTubeEntry]]]] = fetch_youtube,
                 spotify_track: Callable[[str], Awaitable[SpotifyTrack]] = fetch_spotify_track,
                 spotify_playlist: Callable[[str], Awaitable[SpotifyPlaylist]] = fetch_spotify_playlist):
        self.store = store
        self.youtube = youtube
        self.spotify_track = spotify_track
        self.spotify_playlist = spotify_playlist

    def _queue_track(self, name: str, kind: RequestKind, url: str, query: Query) -> Submission:
        rid = self.store.add_request(name, kind, source_url=url, query=query)
        return Submission(name=name, request_ids=[rid], total=1)

    def _add_existing_playlist_track(self, playlist_id: int, source_url: str, position: int) -> bool:
        previous = self.store.filed_request_for_url(source_url)
        if previous and previous.track_id is not None:
            self.store.add_playlist_track(playlist_id, previous.track_id, position)
            return True
        return False

    def _retry_failed_playlist_request(self, playlist_id: int, source_url: str, position: int) -> int | None:
        """Reuse this playlist position's failed request instead of growing duplicate history on re-import."""
        previous = self.store.latest_playlist_request_for_url(playlist_id, source_url)
        if previous is None or previous.state not in {RequestState.ERROR, RequestState.NOT_FOUND}:
            return None
        self.store.update_request(previous.id, state=RequestState.QUEUED, playlist_position=position,
                                  attempts=0, retry_after=None, error_message=None, flag_reason=None,
                                  failed_stage=None, lossless_retry=1)
        return previous.id

    @staticmethod
    def _spotify_query(track: SpotifyTrack) -> Query:
        title, version = parse_version(track.title)
        return Query(raw=f"{track.artist} - {track.title}", artist=track.artist, title=title,
                     version=version, duration_s=track.duration_s)

    @staticmethod
    def _spotify_name(track: SpotifyTrack) -> str:
        return f"{track.artist} - {track.title}" if track.artist else track.title

    async def submit(self, text: str) -> Submission:
        kind, url = classify(text)
        if kind == RequestKind.TEXT or url is None:
            raise BadLink(NOT_A_LINK)
        if kind == RequestKind.SPOTIFY_TRACK:
            try:
                track = await self.spotify_track(url)
            except SpotifyError as e:
                log.warning("Spotify failed for %s: %s", url, e)
                raise BadLink(SPOTIFY_FAIL) from e
            return self._queue_track(self._spotify_name(track), RequestKind.SPOTIFY_TRACK, track.source_url,
                                     self._spotify_query(track))
        if kind == RequestKind.SPOTIFY_PLAYLIST:
            try:
                playlist = await self.spotify_playlist(url)
            except SpotifyError as e:
                log.warning("Spotify failed for %s: %s", url, e)
                raise BadLink(SPOTIFY_FAIL) from e
            if not playlist.tracks:
                raise BadLink(SPOTIFY_FAIL)
            pid = self.store.upsert_playlist(playlist.source_url, playlist.title)
            sub = Submission(name=playlist.title, playlist_id=pid, total=len(playlist.tracks))
            for pos, track in enumerate(playlist.tracks, start=1):
                q = self._spotify_query(track)
                if self._add_existing_playlist_track(pid, track.source_url, pos):
                    sub.already_in_library += 1
                    continue
                existing = None
                if q.artist and q.title:
                    existing = self.store.find_track_by_meta(q.artist, q.title, q.version or "Original Mix",
                                                             q.duration_s)
                if existing:
                    self.store.add_playlist_track(pid, existing.id, pos)
                    sub.already_in_library += 1
                    continue
                retried = self._retry_failed_playlist_request(pid, track.source_url, pos)
                if retried is not None:
                    sub.request_ids.append(retried)
                    continue
                if self.store.open_request_for_url(track.source_url):
                    sub.already_queued += 1
                    continue
                sub.request_ids.append(self.store.add_request(
                    self._spotify_name(track), RequestKind.SPOTIFY_TRACK, source_url=track.source_url, playlist_id=pid,
                    playlist_position=pos, query=q))
            return sub
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
            return self._queue_track(e.title, RequestKind.YT_TRACK, url, q)
        pid = self.store.upsert_playlist(url, name)
        sub = Submission(name=name, playlist_id=pid, total=len(entries))
        for pos, e in enumerate(entries, start=1):
            q = parse_youtube_title(e.title, e.uploader, e.duration_s)
            if self._add_existing_playlist_track(pid, e.url, pos):
                sub.already_in_library += 1
                continue
            existing = None
            if q.artist and q.title:
                existing = self.store.find_track_by_meta(q.artist, q.title, q.version or "Original Mix", q.duration_s)
            if existing:
                self.store.add_playlist_track(pid, existing.id, pos)
                sub.already_in_library += 1
                continue
            retried = self._retry_failed_playlist_request(pid, e.url, pos)
            if retried is not None:
                sub.request_ids.append(retried)
                continue
            if self.store.open_request_for_url(e.url):  # the same playlist sent twice while still in flight
                sub.already_queued += 1
                continue
            sub.request_ids.append(self.store.add_request(
                e.title, RequestKind.YT_TRACK, source_url=e.url, playlist_id=pid, playlist_position=pos, query=q))
        return sub
