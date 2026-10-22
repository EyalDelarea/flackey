"""Which version of a recording a record, a request or a file is (issue #172): its arrangement -- the remix,
edit or dub, "" for the original -- and whether it is a remaster, and of which year.

One model for every caller, because three disagreed: the title parser called "Remastered 2024" a version,
the Soulseek gate did not count it as one, and the Discogs lookup read it as the same recording. All three
were partly right. A remaster is the *same arrangement* on a *different release*: the remix rules compare
arrangements, and whether it is the remaster is a separate question asked of release metadata.

The audio fingerprint cannot answer that question, by design. Chromaprint ignores EQ, compression and
loudness, which is what a remaster changes: both "LSD" versions scored 0.96 against the 2024 video, 0.2 s
apart in length (2026-10-20). It proves the recording family, never the release, so the version has to come
from what the metadata says -- the record's title, mix and album, the peer's path, the file's own tags."""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass

from rapidfuzz import fuzz

from .models import CatalogTrack, is_original, norm

_REMASTER_RE = re.compile(r"\bremaster", re.IGNORECASE)
# The remaster wording as a whole, so "2024 Remaster" / "Remastered 2011" / "Digital Remaster" leave
# nothing behind that would read as an arrangement of its own.
_REMASTER_TEXT_RE = re.compile(r"\b(?:(?:19|20)\d{2}\s+)?(?:digital(?:ly)?\s+)?remaster(?:ed)?"
                               r"(?:\s+version)?(?:\s+(?:19|20)\d{2})?\b", re.IGNORECASE)
_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
# From this token_set ratio two arrangements are one ("Extended" and "Extended Mix")
SAME_ARRANGEMENT = 80


def says_remaster(*texts: str | None) -> bool:
    """Any of `texts` calls itself a remaster: "Remastered 2024", "2011 Remaster", "The Albums Remastered"."""
    return any(t and _REMASTER_RE.search(t) for t in texts)


def arrangement(version: str | None) -> str:
    """The remix or edit `version` names, normalised; "" for the original, remastered or not."""
    v = _REMASTER_TEXT_RE.sub(" ", version or "")
    return "" if is_original(v) else norm(v)


@dataclass(frozen=True)
class Version:
    arrangement: str = ""          # "" is the original arrangement
    remaster: bool = False
    year: str | None = None        # the remaster's year, when its wording names one

    @property
    def is_original(self) -> bool:
        """The original arrangement -- a remaster of it included."""
        return not self.arrangement


def split_version(version: str | None, *context: str | None) -> Version:
    """`version` (a mix name, a title's version text) read as a Version. `context` -- a title, an album, a
    release name, a peer's path -- can only say *remaster*: an album called "Remixes" makes no track a remix."""
    remaster_texts = [t for t in (version, *context) if t and _REMASTER_RE.search(t)]
    # the first year any of them names: "Remastered" from Deezer's album, "Remastered 2024" from the request
    year = next((m.group(0) for t in remaster_texts if (m := _YEAR_RE.search(t))), None)
    return Version(arrangement(version), bool(remaster_texts), year)


def record_version(record: CatalogTrack) -> Version:
    """A Beatport (or stand-in) record's version: Beatport often files a remaster as "Original Mix" on a
    release that says "Remastered"."""
    return split_version(record.mix_name, record.title, record.release_name)


def same_arrangement(a: str, b: str) -> bool:
    if not a or not b:
        return a == b
    return fuzz.token_set_ratio(a, b) >= SAME_ARRANGEMENT


def agrees(asked: Version, have: Version) -> bool:
    """Is `have` the version `asked` names? The same arrangement, and a remaster exactly when one was asked
    for. The years need not agree: peers and Deezer file the 2024 "LSD" remaster under the box set's 2023.
    A date alone never makes a record the remaster -- a 2025 compilation of the original is not one -- it
    only confirms a downloaded file (`check_version`)."""
    return same_arrangement(asked.arrangement, have.arrangement) and asked.remaster == have.remaster


def with_remaster(mix_name: str | None, remaster: bool) -> str | None:
    """`mix_name` worded so that it says remaster when the release is one, for a record whose mix does not."""
    if not remaster or says_remaster(mix_name):
        return mix_name
    return "Remastered" if not arrangement(mix_name) else f"{mix_name} Remastered"


@dataclass(frozen=True)
class VersionCheck:
    """What the downloaded file's own name and tags say about its version, after the fingerprint has
    proved the recording. `unconfirmed` files the track with a flag rather than claiming the version."""
    status: str                    # "confirmed" | "unconfirmed"
    reason: str

    def to_dict(self) -> dict:
        return asdict(self)


_TAG_FIELDS = ("album", "title", "mix", "comment")


def check_version(asked: Version, *, path: str = "", tags: dict[str, str] | None = None,
                  metadata: tuple[str | None, ...] = (), release_year: str | None = None) -> VersionCheck | None:
    """Evidence for the version of a file whose recording the fingerprint has already matched. `path` is
    the peer's, `tags` the file's own as it arrived, `metadata` what the record it was fetched as says
    (Deezer's album and title), `release_year` that record's or the file's date.

    A remaster is confirmed when anything there says remaster, or the date is the remaster's year or
    later. An original is only ever questioned, never confirmed: nothing saying remaster is the normal
    case, so only a file that does say it gets a check (None otherwise)."""
    tags = tags or {}
    texts = [path, *(tags.get(k) for k in _TAG_FIELDS), *metadata]
    named = says_remaster(*texts)
    year = (tags.get("date") or "")[:4] or (release_year or "")[:4] or None
    if not asked.remaster:
        if named:
            return VersionCheck("unconfirmed", "the file says remaster, and the original was asked for")
        return None
    if named:
        return VersionCheck("confirmed", "its name or tags say remaster")
    if asked.year and year and year >= asked.year:
        return VersionCheck("confirmed", f"dated {year}, the remaster's year or later")
    return VersionCheck("unconfirmed", "nothing in its name or tags says it is the remaster")
