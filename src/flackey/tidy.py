"""Tidy the library's artist folders: merge one artist's split folders ("Miranda" and "Miranda!") and take
leaked track numbers ("02. Man With No Name") out of folder and file names.

Opt-in, two steps, and never silent. `plan` only looks: it lists every move it would make. `apply` moves
exactly that list and nothing else, and only when handed the same list back -- the owner's confirmation of
what they saw. Moving a file changes its path, so Rekordbox shows a moved track as missing until it is
relinked; the page says so before the owner confirms.

Only files the library knows (a row in `tracks`) are moved; anything else in a folder is left where it is.
Nothing is ever deleted: a folder is removed only when it is empty after the moves (`os.rmdir` refuses
anything else). Collaboration folders are left alone: "Duvdev & Shidapu" is not the same artist as
"Duvdev" (`library.artist_key`)."""
from __future__ import annotations

import logging
import os
from dataclasses import asdict, dataclass
from pathlib import Path

from .export import write_playlists
from .library import (
    artist_folders,
    artist_key,
    has_track_number,
    library_reachable,
    preferred_folder,
    strip_track_number,
)
from .store import Store

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Move:
    track_id: int
    source: str
    dest: str


@dataclass
class Group:
    """One artist's folders and what tidying them would do."""
    folder: str                   # the folder that stays (or the clean name a lone numbered folder becomes)
    merges: list[str]             # the other spellings whose files move into it
    moves: list[Move]
    left_behind: int              # files the library does not know, left where they are
    conflicts: list[str]          # files not moved because the name is already taken in the folder that stays


def _clean_file_name(name: str) -> str:
    return strip_track_number(name)


def plan(store: Store, root: Path, layout: str = "artist") -> list[Group]:
    """Every artist folder that tidying would change, with the moves it would make. Looks only. Only a
    library filed by artist has artist folders; under a date layout there is nothing to tidy."""
    if layout != "artist" or not library_reachable(root):
        return []
    by_key: dict[str, list[Path]] = {}
    for d in artist_folders(root):
        by_key.setdefault(artist_key(d.name), []).append(d)
    known: dict[Path, list] = {}
    for t in store.list_tracks(limit=1_000_000):
        known.setdefault(t.path.parent, []).append(t)
    groups = []
    for folders in by_key.values():
        keep = preferred_folder(folders)
        target = root / strip_track_number(keep.name) if has_track_number(keep.name) else keep
        moves, conflicts, left = [], [], 0
        taken = {p.name.casefold() for p in _files(target)} if target.is_dir() else set()
        for d in sorted(folders, key=lambda f: (f != keep, f.name)):
            tracks = {t.path.name: t for t in known.get(d, [])}
            left += sum(1 for p in _files(d) if p.name not in tracks)
            for name, t in sorted(tracks.items()):
                dest = target / _clean_file_name(name)
                if dest == t.path:
                    continue
                if dest.name.casefold() in taken and not (d == target and dest.name.casefold() == name.casefold()):
                    conflicts.append(str(t.path))
                    continue
                taken.add(dest.name.casefold())
                moves.append(Move(t.id, str(t.path), str(dest)))
        if moves or conflicts:
            groups.append(Group(folder=target.name, merges=sorted(d.name for d in folders if d != target),
                                moves=moves, left_behind=left, conflicts=conflicts))
    return sorted(groups, key=lambda g: g.folder.casefold())


def _files(d: Path) -> list[Path]:
    try:
        return [p for p in d.iterdir() if p.is_file() and not p.name.startswith(".")]
    except OSError:
        return []


def as_json(groups: list[Group]) -> list[dict]:
    return [asdict(g) for g in groups]


class PlanChanged(Exception):
    """The library changed between the preview and the confirmation: nothing was moved."""


def apply(store: Store, root: Path, confirmed: list[dict], layout: str = "artist") -> dict:
    """Move exactly the files the owner confirmed, which must be the plan as it stands now. Each file is
    renamed and its row repointed in turn; a file that cannot be moved is skipped and reported, and its row
    keeps the old path. Playlists are rewritten from the rows at the end."""
    groups = plan(store, root, layout)
    planned = [asdict(m) for g in groups for m in g.moves]
    if not planned or planned != confirmed:
        raise PlanChanged("The library changed since the preview. Review the list again.")
    moved: list[Path] = []
    failed: list[str] = []
    try:
        _move_all(store, planned, moved, failed)
    finally:
        for d in {p.parent for p in moved}:
            try:
                d.rmdir()                    # only ever an empty folder; anything left in it stays
            except OSError:
                pass
        write_playlists(store, root)         # playlists follow the rows, whatever got moved
    return {"moved": len(moved), "failed": failed}


def _move_all(store: Store, planned: list[dict], moved: list[Path], failed: list[str]) -> None:
    for m in planned:
        source, dest = Path(m["source"]), Path(m["dest"])
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.exists() and not _same_file(source, dest):
                raise FileExistsError(dest)
            # One volume -- the library -- so a rename: never a copy that could leave two files or half of one
            os.rename(source, dest)
        except OSError as e:
            log.warning("tidy: could not move %s -> %s: %s", source, dest, e)
            failed.append(str(source))
            continue
        try:
            store.update_track(m["track_id"], path=dest)
        except Exception:
            os.rename(dest, source)          # the row is the record: put the file back where it says
            failed.append(str(source))
            raise
        moved.append(source)
        log.info("tidy: moved %s -> %s", source, dest)


def _same_file(a: Path, b: Path) -> bool:
    """A rename that only changes case on a case-insensitive disk: the destination "exists" because it is
    the source itself."""
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False
