from __future__ import annotations

import hashlib
import logging
import ntpath
import os
import re
import shutil
import sys
from datetime import date, datetime
from pathlib import Path

from .match import candidate_release, candidate_version
from .models import Candidate, CatalogTrack, Track
from .store import Store
from .version import (
    EXTENDED,
    Version,
    extended_target,
    record_version,
    same_arrangement,
    same_owned_version,
    split_version,
)

log = logging.getLogger(__name__)
_BAD = re.compile(r'[\\/:*?"<>|]')
# Windows' own rules on top of `_BAD`, which every system already gets. Applied on Windows only, so a
# Mac library keeps the names it has always had: an artist folder that was "Aux" there stays "Aux".
#
# A name whose part before the first dot is one of these opens a device, not a file -- `NUL.flac` is
# the null device in any folder -- whatever the case and whatever spaces follow it.
#
# The list is Microsoft's ("Naming Files, Paths, and Namespaces"), including the superscript ¹ ² ³ that
# Windows reads as digits in COM# and LPT#. CONIN$ and CONOUT$ are not on that page; they are the console
# names CreateFile documents opening, and Python's own `os.path.isreserved` counts them among the DOS
# device names, so they are kept out the same way.
_WINDOWS_DIGITS = "123456789¹²³"
_WINDOWS_RESERVED = frozenset({"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$",
                               *(f"COM{d}" for d in _WINDOWS_DIGITS), *(f"LPT{d}" for d in _WINDOWS_DIGITS)})
# Control characters, on every system: a NUL ends a name early in C, and an escape sequence repaints the
# terminal of whoever lists the folder. The whitespace ones (tab, newline, \x1c-\x1f) are not here: those
# are turned into a space below, so "A\tB" stays two words.
_CONTROL = re.compile(r"[\x00-\x08\x0e-\x1b\x7f]")
# MAX_PATH is 260 UTF-16 units with the terminating NUL. Long paths need a registry switch that is off
# by default, and Rekordbox and Explorer trip over them even when it is on, so the library stays inside
# the classic limit rather than depending on it.
WINDOWS_MAX_PATH = 259
# The least of a title worth keeping, counting the " ~abcdef" that keeps two cut titles apart.
_MIN_TITLE_UNITS = 16


def _windows_segment(s: str) -> str:
    stem, dot, rest = s.partition(".")
    if stem.rstrip(" ").upper() in _WINDOWS_RESERVED:
        # After the device name rather than in front of it, so the folder still sorts under its letter.
        s = f"{stem.rstrip(' ')}_{dot}{rest}"
    return s


def sanitize(segment: str) -> str:
    s = _BAD.sub("-", _CONTROL.sub("", segment))
    s = " ".join(s.split()).strip(" .")
    if sys.platform != "win32":
        return s[:120] or "Unknown"
    # The cut can land on a space or a dot, and Windows drops a trailing one without a word: the file
    # would then be written under a name other than the one the library records.
    s = s[:120].rstrip(" .")
    return _windows_segment(s) if s else "Unknown"


# macOS (APFS) and Linux allow 255 bytes of UTF-8 in one file or folder name
NAME_MAX_BYTES = 255
# The most of a cut file name the artist may take, leaving the rest for the title
_ARTIST_MAX_BYTES = 100


def _cut_utf8(s: str, limit: int) -> str:
    """`s` cut to at most `limit` bytes of UTF-8, never in the middle of a character."""
    return s.encode("utf-8")[:limit].decode("utf-8", "ignore")


def _fit_folder_bytes(name: str) -> str:
    """An artist folder name cut to NAME_MAX_BYTES: 120 characters of Japanese is 360 bytes, past what
    macOS and Linux allow (ENG-7). Only a name that could never have been created is cut, so every existing
    folder keeps its name. Windows counts the whole path instead (`_fit_windows_path`)."""
    if sys.platform == "win32" or len(name.encode("utf-8")) <= NAME_MAX_BYTES:
        return name
    return _cut_utf8(name, NAME_MAX_BYTES).rstrip(" .") or "Unknown"


def _fit_name_bytes(artist: str, title: str, ext: str) -> str:
    """The file name, with the title (and a very long artist) cut so the name fits NAME_MAX_BYTES. A name
    that fits is returned as it always was; a cut title ends in " ~" and six hex digits of the full
    title's hash, as on Windows, so two long titles that differ only at the end stay two files."""
    name = f"{artist} - {title}.{ext}"
    if len(name.encode("utf-8")) <= NAME_MAX_BYTES:
        return name
    artist = _cut_utf8(artist, _ARTIST_MAX_BYTES).rstrip(" .")
    tag = " ~" + hashlib.sha256(title.encode("utf-8")).hexdigest()[:6]
    budget = NAME_MAX_BYTES - len(f"{artist} - .{ext}{tag}".encode())
    cut = _cut_utf8(title, budget).rstrip(" .")
    log.info("title cut to fit the file name limit: %r -> %r", title, cut + tag)
    return f"{artist} - {cut}{tag}.{ext}"


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


# The date layouts of `config.LIBRARY_LAYOUTS` name the folder after the day the track was filed, in ISO form so
# Finder and Explorer list the folders in order.
_DATE_FOLDER = {"month": "%Y-%m", "day": "%Y-%m-%d"}


def final_path(root: Path, catalog: CatalogTrack, ext: str, layout: str = "artist",
               when: date | None = None) -> Path:
    """Where a newly filed track goes. `layout` picks the folder: one per artist (the first credited one
    for a collaboration), one per month or day the track was filed (`when`, today if not given), or the
    library folder itself. Whatever playlist asked for the track, playlists reach the file through their
    M3U8; genre and label live in the tags only.

    On Windows the title is shortened when it has to be, so the whole path stays under MAX_PATH; see
    `_fit_windows_path`."""
    if layout == "artist":
        folder = root / _fit_folder_bytes(sanitize(catalog.artist.split(",")[0]))
    elif layout in _DATE_FOLDER:
        folder = root / (when or datetime.now().astimezone().date()).strftime(_DATE_FOLDER[layout])
    elif layout == "flat":
        folder = root
    else:
        raise ValueError(f"unknown library layout {layout!r}")
    return refile_path(folder, catalog, ext)


def refile_path(folder: Path, catalog: CatalogTrack, ext: str) -> Path:
    """The track's file name inside `folder`. A lossless upgrade calls this with the folder the track
    is already in, so a replaced file stays where the owner put it -- or where an earlier layout put it --
    rather than moving into this month's folder."""
    artist, title, ext = sanitize(catalog.artist), sanitize(catalog.display_title), ext.lstrip(".")
    if sys.platform == "win32":
        return folder / _fit_windows_path(folder, artist, title, ext)
    return folder / _fit_name_bytes(artist, title, ext)


class DriveMissing(OSError):
    """The folder a file was going to is on a drive that is not plugged in (`missing_drive`)."""

    def __init__(self, drive: str):
        super().__init__(f"the drive {drive} is not connected")
        self.drive = drive


def make_dirs(folder: Path) -> None:
    """`folder.mkdir(parents=True)`, except on a drive that is not plugged in: there it would create the
    drive's mount point on the boot disk (or fail on a /Volumes the owner cannot write), so it raises
    DriveMissing instead and creates nothing."""
    drive = missing_drive(folder)
    if drive is not None:
        raise DriveMissing(drive)
    folder.mkdir(parents=True, exist_ok=True)


def file_track(tmp_path: Path, dest: Path) -> Path:
    if dest.exists():
        raise FileExistsError(dest)
    make_dirs(dest.parent)
    try:
        os.replace(tmp_path, dest)
    except OSError:
        shutil.move(str(tmp_path), str(dest))
    return dest


def clear_request_tmp(tmp_dir: Path, request_id: int) -> None:
    """Delete what a pass of this request downloaded into `tmp_dir`: its fetch folder and its lossless
    copy (`req<id>-lossless.<ext>`, and the converted file beside it). Not `-held` or `-accepted`, which
    belong to other steps."""
    shutil.rmtree(tmp_dir / f"req{request_id}", ignore_errors=True)
    try:
        leftovers = list(tmp_dir.glob(f"req{request_id}-lossless*"))
    except OSError:
        return
    for p in leftovers:
        try:
            p.unlink(missing_ok=True)
        except OSError as e:
            log.warning("could not delete %s: %s", p, e)


# Older than this, a file in tmp_dir at startup belongs to no pass still running: a Stop that landed while
# ffmpeg was writing, or a quit or crash mid-download.
TMP_SWEEP_AGE_S = 24 * 3600


def sweep_tmp(tmp_dir: Path, now: float, max_age_s: float = TMP_SWEEP_AGE_S) -> int:
    """Delete what is left in `tmp_dir` from earlier runs. Returns how many entries went."""
    n = 0
    try:
        entries = list(tmp_dir.iterdir())
    except OSError:
        return 0
    for p in entries:
        try:
            if now - p.stat().st_mtime < max_age_s:
                continue
            if p.is_dir():
                shutil.rmtree(p)
            else:
                p.unlink()
            n += 1
        except OSError as e:
            log.warning("could not delete leftover %s: %s", p, e)
    return n


# A refresh that would forget more than half the library at once, once at least this many rows would go,
# is a drive that is not there rather than files the owner deleted (ENG-1). Below the floor a small library
# still prunes normally: one track deleted out of two is just a deleted track.
PRUNE_FLOOR = 10
PRUNE_MAX_SHARE = 0.5
# export.PLAYLIST_DIR; export imports this module, so the name is repeated rather than imported
_PLAYLIST_DIR = "Playlists"


# Where macOS mounts every drive but the boot disk. A module constant so tests can point it at a folder of
# their own; nothing on Windows or Linux mounts here.
VOLUMES = Path("/Volumes")


def _drive_present(drive: str) -> bool:
    """Whether a Windows drive letter ("E:") has a disk behind it right now."""
    return os.path.exists(drive + "\\")


def missing_drive(path: Path) -> str | None:
    """The name of the drive `path` is on, when that drive is not plugged in; None when it is, and for a
    folder on the boot disk.

    On a Mac that is a folder under /Volumes/<name> with nothing mounted at /Volumes/<name>. An empty folder
    standing there is not the drive: it is a mount point left behind on the boot disk, and anything written
    into it would land on the boot disk and later hide the real drive, which macOS then mounts as
    "<name> 1". On Windows it is a drive letter with no disk behind it. Nothing here creates anything, so
    asking is always safe -- which is the point: every `mkdir` of the library asks this first."""
    if sys.platform == "win32":
        drive = ntpath.splitdrive(str(path))[0]
        if len(drive) != 2:          # no drive letter, or a \\server\share path (refused as a library anyway)
            return None
        return None if _drive_present(drive) else drive.upper()
    # Resolved, so a symlink into /Volumes counts and /Volumes/Macintosh HD (a symlink to /) does not
    real, volumes = Path(os.path.realpath(path)), Path(os.path.realpath(VOLUMES))
    try:
        name = real.relative_to(volumes).parts[0]
    except (ValueError, IndexError):
        return None
    return None if os.path.ismount(volumes / name) else name


def library_reachable(library_root: Path) -> bool:
    """Whether the library folder is there to be looked in. A drive that is unplugged or a share that is
    offline has no folder at all, or -- when something recreated the mount point on the boot disk -- an
    empty one, holding at most a `Playlists` folder an export put there. Either way a missing file then
    says nothing about whether the owner deleted it."""
    if missing_drive(library_root) is not None:
        return False
    try:
        if not library_root.is_dir():
            return False
        return any(not e.name.startswith(".") and e.name != _PLAYLIST_DIR for e in library_root.iterdir())
    except OSError:
        return False


def _gone(t: Track, library_root: Path, reachable: bool) -> bool:
    """The file was removed, as opposed to sitting somewhere that cannot be looked at right now."""
    if t.path.exists():
        return False
    if t.path.is_relative_to(library_root):
        return reachable
    # Filed under an earlier library folder: only a file whose own folder is still there counts as removed
    return t.path.parent.is_dir()


def missing_tracks(store: Store, library_root: Path) -> tuple[list[Track], int]:
    """The library rows whose file was removed, and how many rows there are in all. Empty while the
    library folder is missing or empty: an unplugged drive is not a deleted library."""
    reachable = library_reachable(library_root)
    tracks = store.list_tracks(limit=1_000_000)
    if not reachable and any(not t.path.exists() for t in tracks):
        log.warning("library folder %s is missing or empty (drive unplugged?); not forgetting any tracks",
                    library_root)
    return [t for t in tracks if _gone(t, library_root, reachable)], len(tracks)


def prune_missing_tracks(store: Store, library_root: Path, *, confirmed: bool = False) -> list[Path]:
    """Drop library rows whose file was removed by hand, so they neither count as duplicates nor
    linger in playlists. Returns the paths that were forgotten.

    Forgetting a row also drops its playlist memberships and evidence, and nothing brings those back, so
    an unreachable library is never taken for an empty one: with the library folder missing or empty,
    nothing is forgotten and the log says why. With most of the library missing at once, nothing is
    forgotten either unless the owner `confirmed` it -- a folder that is there and readable but lost most
    of its files was emptied on purpose more often than not (a wipe to download everything again), but
    only the owner can say so."""
    gone, total = missing_tracks(store, library_root)
    if not confirmed and held_back(len(gone), total):
        log.warning("%d of %d library files are missing at once; assuming the drive is not there and "
                    "forgetting none of them", len(gone), total)
        return []
    for t in gone:
        log.warning("library file missing, forgetting it: %s", t.path)
    store.delete_tracks([t.id for t in gone])
    return [t.path for t in gone]


def held_back(gone: int, total: int) -> bool:
    """Too many files missing at once to forget them without asking the owner first."""
    return gone >= PRUNE_FLOOR and gone > PRUNE_MAX_SHARE * total


def present(store: Store, t: Track | None, library_root: Path | None) -> Track | None:
    """`t`, unless its file was removed, in which case the row is forgotten. Without a library folder to
    check against, or with that folder unreachable, a missing file is not proof of anything: the track
    still counts as owned rather than being downloaded again into a folder that is not there."""
    if t is None or t.path.exists():
        return t
    if library_root is None or not _gone(t, library_root, library_reachable(library_root)):
        return t
    log.warning("library file missing, forgetting it: %s", t.path)
    store.delete_track(t.id)
    return None


def find_duplicate(store: Store, catalog: CatalogTrack | None, cand: Candidate,
                   asked: str | None = None, *, library_root: Path | None = None) -> Track | None:
    """The library track this download would duplicate. `asked` is the request's version text: a release
    or album that says remaster only counts when it asks for the remaster, so a classic track on a
    "Remastered" album still finds the copy already filed as "Original Mix" (issue #172)."""
    isrc = (catalog.isrc if catalog else None) or cand.isrc
    a = split_version(asked)
    version = record_version(catalog, a) if catalog else candidate_release(cand, a)
    if isrc:
        # A label can reissue a remaster under the original's ISRC (Hallucinogen's "LSD (Remastered
        # 2024)", 2026-10-20), so the ISRC is only the same track when the version agrees too.
        for t in store.find_tracks_by_isrc(isrc):
            if _same_version(t, version) and present(store, t, library_root):
                return t
    if catalog:
        t = store.find_track_by_meta(catalog.artist, catalog.title, catalog.mix_name, catalog.duration_s)
    else:
        t = store.find_track_by_meta(cand.artist, cand.title, candidate_version(cand), cand.duration_s)
    # Beatport files some remasters as "Original Mix" on a remastered release, so the mix name alone
    # matches the original already filed; the version has to agree here too.
    return present(store, t, library_root) if t is not None and _same_version(t, version) else None


def find_extended(store: Store, artist: str, title: str, mix_name: str | None, *,
                  library_root: Path | None = None) -> Track | None:
    """An extended copy of the `mix_name` version of this song already in the library, at any length: the
    request's length is the radio edit's, so it says nothing about the extended mix's (issue #187)."""
    target = extended_target(mix_name)
    for t in store.find_tracks_by_title(artist, title):
        v = split_version(t.mix_name)
        if v.cut == EXTENDED and same_arrangement(target, v.arrangement) and present(store, t, library_root):
            return t
    return None


def _same_version(t: Track, version: Version) -> bool:
    """A library track's stored mix agrees with `version` (a track with no mix is taken as agreeing). The
    library keeps no release name, so a remaster filed as "Original Mix" reads as the original: asked for
    again, it is fetched once more rather than an original standing in for it."""
    return not t.mix_name or same_owned_version(split_version(t.mix_name), version)
