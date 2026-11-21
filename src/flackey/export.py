from __future__ import annotations

from pathlib import Path

from .library import sanitize
from .models import Playlist, Track
from .store import Store

PLAYLIST_DIR = "Playlists"
# The playlist Flackey keeps on its own: every track filed since the owner last said "I imported these"
# into Rekordbox. Flackey cannot see inside Rekordbox, so the owner's button press is the only marker.
NEW_PLAYLIST_NAME = "Flackey - New"
# Settings key holding the newest filed time the owner has said is in Rekordbox (ISO UTC, as `_now`).
# Unset means never pressed: every track counts as new, which is the truth as far as Flackey knows.
IMPORTED_THROUGH_KEY = "rekordbox_imported_through"


def playlist_names(playlists: list[Playlist]) -> dict[int, str]:
    """Safe, unique file stem per playlist for its M3U8. The New playlist's name is held back, so a
    playlist the owner happened to call "Flackey - New" gets its id added rather than being overwritten."""
    names: dict[int, str] = {}
    taken: set[str] = {NEW_PLAYLIST_NAME}
    for p in sorted(playlists, key=lambda p: p.id):
        safe = sanitize(p.name)
        if safe in taken:
            safe = f"{safe} ({p.id})"
        taken.add(safe)
        names[p.id] = safe
    return names


def _one_line(text: str) -> str:
    """Names come from Spotify, Deezer and the bot; a line break in one would start a new playlist entry,
    which Rekordbox would then import as a path."""
    return " ".join("".join(" " if ord(c) < 32 or ord(c) == 127 else c for c in text).split())


def build_m3u8(playlist: Playlist, tracks: dict[int, Track]) -> str:
    lines = ["#EXTM3U", f"#PLAYLIST:{_one_line(playlist.name)}"]
    for tid in playlist.track_ids:
        t = tracks.get(tid)
        if t is None:  # deleted from the index after it was added to the playlist
            continue
        secs = -1 if t.duration_s is None else t.duration_s
        lines.append(f"#EXTINF:{secs},{_one_line(f'{t.artist} - {t.title}')}")
        lines.append(str(t.path))
    return "\n".join(lines) + "\n"


def _write_text(out: Path, text: str) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(out)  # a reader (Rekordbox import) never sees a half-written file
    return out


def _write(store: Store, playlist: Playlist, name: str, library_root: Path) -> Path:
    tracks = {}
    for tid in playlist.track_ids:
        try:
            tracks[tid] = store.get_track(tid)
        except KeyError:
            pass
    return _write_text(library_root / PLAYLIST_DIR / f"{name}.m3u8", build_m3u8(playlist, tracks))


def write_playlist(store: Store, playlist_id: int, library_root: Path) -> Path:
    names = playlist_names(store.list_playlists())
    return _write(store, store.get_playlist(playlist_id), names[playlist_id], library_root)


def write_playlists(store: Store, library_root: Path) -> list[Path]:
    playlists = store.list_playlists()
    names = playlist_names(playlists)
    return [*(_write(store, p, names[p.id], library_root) for p in playlists), write_new_playlist(store, library_root)]


def new_playlist_path(library_root: Path) -> Path:
    return library_root / PLAYLIST_DIR / f"{NEW_PLAYLIST_NAME}.m3u8"


def filed_at(t: Track) -> str:
    """When the track's current file landed: `Store.tracks_filed_since` orders and filters by the same."""
    return max(t.added_at, t.verified_at or t.added_at)


def new_tracks(store: Store) -> list[Track]:
    """Tracks filed since the owner last said they imported into Rekordbox, oldest first."""
    return store.tracks_filed_since(store.get_setting(IMPORTED_THROUGH_KEY))


def write_new_playlist(store: Store, library_root: Path) -> Path:
    """Rewrite the New playlist from the database. Written even when empty, so importing it after a
    "nothing new" press clears what Rekordbox showed last time rather than leaving the old list there."""
    tracks = new_tracks(store)
    playlist = Playlist(id=0, source_url="", name=NEW_PLAYLIST_NAME, created_at="", updated_at="",
                        track_ids=[t.id for t in tracks])
    return _write_text(new_playlist_path(library_root), build_m3u8(playlist, {t.id: t for t in tracks}))


def mark_imported(store: Store, through: str) -> None:
    """The owner imported the New playlist as they last saw it. `through` is the newest filed time that list
    held, not the time of the press: a track filed while they were in Rekordbox stays new."""
    store.set_setting(IMPORTED_THROUGH_KEY, through)
