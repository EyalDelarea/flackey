from __future__ import annotations

import logging
import os
import re
import shutil
from pathlib import Path

from .match import candidate_version
from .models import Candidate, CatalogTrack, Track
from .store import Store

log = logging.getLogger(__name__)
_BAD = re.compile(r'[\\/:*?"<>|]')


def sanitize(segment: str) -> str:
    s = _BAD.sub("-", segment)
    s = " ".join(s.split()).strip(" .")
    return s[:120] or "Unknown"


def final_path(root: Path, catalog: CatalogTrack, ext: str) -> Path:
    """One folder per artist (the first credited one for a collaboration), whatever playlist asked for
    the track: playlists reach the file through their M3U8. Genre and label live in the tags only."""
    name = f"{sanitize(catalog.artist)} - {sanitize(catalog.display_title)}.{ext.lstrip('.')}"
    return root / sanitize(catalog.artist.split(",")[0]) / name


def file_track(tmp_path: Path, dest: Path) -> Path:
    if dest.exists():
        raise FileExistsError(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.replace(tmp_path, dest)
    except OSError:
        shutil.move(str(tmp_path), str(dest))
    return dest


def prune_missing_tracks(store: Store) -> list[Path]:
    """Drop library rows whose file was removed by hand, so they neither count as duplicates nor
    linger in playlists. Returns the paths that were forgotten."""
    gone = [t for t in store.list_tracks(limit=1_000_000) if not t.path.exists()]
    for t in gone:
        log.warning("library file missing, forgetting it: %s", t.path)
        store.delete_track(t.id)
    return [t.path for t in gone]


def _present(store: Store, t: Track | None) -> Track | None:
    if t is not None and not t.path.exists():
        log.warning("library file missing, forgetting it: %s", t.path)
        store.delete_track(t.id)
        return None
    return t


def find_duplicate(store: Store, catalog: CatalogTrack | None, cand: Candidate) -> Track | None:
    isrc = (catalog.isrc if catalog else None) or cand.isrc
    if isrc:
        t = _present(store, store.find_track_by_isrc(isrc))
        if t:
            return t
    if catalog:
        return _present(store, store.find_track_by_meta(catalog.artist, catalog.title, catalog.mix_name, catalog.duration_s))
    return _present(store, store.find_track_by_meta(cand.artist, cand.title, candidate_version(cand), cand.duration_s))
