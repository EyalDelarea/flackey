from __future__ import annotations

from pathlib import Path

from .library import sanitize
from .models import Playlist, Track
from .store import Store

PLAYLIST_DIR = "Playlists"


def playlist_names(playlists: list[Playlist]) -> dict[int, str]:
    """Safe, unique file stem per playlist for its M3U8."""
    names: dict[int, str] = {}
    taken: set[str] = set()
    for p in sorted(playlists, key=lambda p: p.id):
        safe = sanitize(p.name)
        if safe in taken:
            safe = f"{safe} ({p.id})"
        taken.add(safe)
        names[p.id] = safe
    return names


def build_m3u8(playlist: Playlist, tracks: dict[int, Track]) -> str:
    lines = ["#EXTM3U", f"#PLAYLIST:{playlist.name}"]
    for tid in playlist.track_ids:
        t = tracks.get(tid)
        if t is None:  # deleted from the index after it was added to the playlist
            continue
        secs = -1 if t.duration_s is None else t.duration_s
        lines.append(f"#EXTINF:{secs},{t.artist} - {t.title}")
        lines.append(str(t.path))
    return "\n".join(lines) + "\n"


def _write(store: Store, playlist: Playlist, name: str, library_root: Path) -> Path:
    out_dir = library_root / PLAYLIST_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    tracks = {}
    for tid in playlist.track_ids:
        try:
            tracks[tid] = store.get_track(tid)
        except KeyError:
            pass
    out = out_dir / f"{name}.m3u8"
    tmp = out.with_name(out.name + ".tmp")
    tmp.write_text(build_m3u8(playlist, tracks), encoding="utf-8")
    tmp.replace(out)  # a reader (Rekordbox import) never sees a half-written file
    return out


def write_playlist(store: Store, playlist_id: int, library_root: Path) -> Path:
    names = playlist_names(store.list_playlists())
    return _write(store, store.get_playlist(playlist_id), names[playlist_id], library_root)


def write_playlists(store: Store, library_root: Path) -> list[Path]:
    playlists = store.list_playlists()
    names = playlist_names(playlists)
    return [_write(store, p, names[p.id], library_root) for p in playlists]
