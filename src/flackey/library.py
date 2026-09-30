from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import sys
from pathlib import Path

from .match import candidate_version
from .models import Candidate, CatalogTrack, Track
from .store import Store

log = logging.getLogger(__name__)
_BAD = re.compile(r'[\\/:*?"<>|]')
# Windows' own rules on top of `_BAD`, which every system already gets. Applied on Windows only, so a
# Mac library keeps the names it has always had: an artist folder that was "Aux" there stays "Aux".
#
# A name whose part before the first dot is one of these opens a device, not a file -- `NUL.flac` is
# the null device in any folder -- whatever the case and whatever spaces follow it.
_WINDOWS_RESERVED = frozenset({"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                               *(f"LPT{i}" for i in range(1, 10))})
_WINDOWS_CONTROL = re.compile(r"[\x00-\x1f]")
# MAX_PATH is 260 UTF-16 units with the terminating NUL. Long paths need a registry switch that is off
# by default, and Rekordbox and Explorer trip over them even when it is on, so the library stays inside
# the classic limit rather than depending on it.
WINDOWS_MAX_PATH = 259
# The least of a title worth keeping, counting the " ~abcdef" that keeps two cut titles apart.
_MIN_TITLE_UNITS = 16


def _windows_segment(s: str) -> str:
    s = _WINDOWS_CONTROL.sub("-", s)
    stem, dot, rest = s.partition(".")
    if stem.rstrip(" ").upper() in _WINDOWS_RESERVED:
        # After the device name rather than in front of it, so the folder still sorts under its letter.
        s = f"{stem.rstrip(' ')}_{dot}{rest}"
    return s


def sanitize(segment: str) -> str:
    s = _BAD.sub("-", segment)
    s = " ".join(s.split()).strip(" .")
    if sys.platform != "win32":
        return s[:120] or "Unknown"
    # The cut can land on a space or a dot, and Windows drops a trailing one without a word: the file
    # would then be written under a name other than the one the library records.
    s = s[:120].rstrip(" .")
    return _windows_segment(s) if s else "Unknown"


def _utf16_units(s: str) -> int:
    """Windows counts path length in UTF-16 units, in which an emoji is two."""
    return len(s.encode("utf-16-le")) // 2


def _fit_windows_path(folder: Path, artist: str, title: str, ext: str) -> str:
    """The file name, with the title cut short enough for the whole path to fit in MAX_PATH.

    A cut title ends in " ~" and six hex digits of the full title's hash: two long titles that differ
    only at the end -- an Extended Mix and a Radio Edit -- would otherwise cut to the same name, and the
    second would be refused as already there. The cut is deterministic -- the same title and extension
    always give the same name -- though an .mp3 and its .flac upgrade may cut at different lengths; the
    upgrade repoints the row to whatever path this returns, so nothing needs the two stems to match. The
    artist folder is never cut here: every track under it shares it, so shortening it for one would
    split the artist in two."""
    name = f"{artist} - {title}.{ext}"
    spare = WINDOWS_MAX_PATH - _utf16_units(str(folder)) - 1  # the separator before the name
    if _utf16_units(name) <= spare:
        return name
    tag = " ~" + hashlib.sha256(title.encode("utf-8")).hexdigest()[:6]
    budget = spare - _utf16_units(f"{artist} - .{ext}") - _utf16_units(tag)
    # A library folder this deep has no room for a name worth reading. The write then fails with an
    # error naming the path, which tells the owner more than a file called "~1a2b3c.flac" would.
    budget = max(budget, _MIN_TITLE_UNITS - len(tag))
    cut = title
    while cut and _utf16_units(cut) > budget:
        cut = cut[:-1]
    cut = cut.rstrip(" .")
    log.info("title cut to fit the Windows path limit: %r -> %r", title, cut + tag)
    return f"{artist} - {cut}{tag}.{ext}"


def final_path(root: Path, catalog: CatalogTrack, ext: str) -> Path:
    """One folder per artist (the first credited one for a collaboration), whatever playlist asked for
    the track: playlists reach the file through their M3U8. Genre and label live in the tags only.

    On Windows the title is shortened when it has to be, so the whole path stays under MAX_PATH; see
    `_fit_windows_path`."""
    folder = root / sanitize(catalog.artist.split(",")[0])
    artist, title, ext = sanitize(catalog.artist), sanitize(catalog.display_title), ext.lstrip(".")
    if sys.platform == "win32":
        return folder / _fit_windows_path(folder, artist, title, ext)
    return folder / f"{artist} - {title}.{ext}"


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
