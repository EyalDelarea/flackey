"""Which version of a recording a record, a request or a file is (issue #172): its arrangement -- the remix,
edit or dub, "" for the original -- whether it is a remaster, and of which year, and its cut: "extended" for
an extended, club or 12" version (issue #187), "" for the normal one.

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
from dataclasses import asdict, dataclass, field

from rapidfuzz import fuzz

from .models import CatalogTrack, is_original, norm

_REMASTER_RE = re.compile(r"\bremaster", re.IGNORECASE)
# The remaster wording as a whole, so "2024 Remaster" / "Remastered 2011" / "Digital Remaster" leave
# nothing behind that would read as an arrangement of its own.
_REMASTER_TEXT_RE = re.compile(r"\b(?:(?:19|20)\d{2}\s+)?(?:digital(?:ly)?\s+)?remaster(?:ed)?"
                               r"(?:\s+version)?(?:\s+(?:19|20)\d{2})?\b", re.IGNORECASE)
_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
# The words for an extended cut, matched on the raw text: `norm` would strip the quote that makes "12''" a
# record size. Peers write the quote as '', a curly or a straight double quote, an underscore (a file name
# cannot hold a double quote on Windows) or "inch".
_CUT_RE = re.compile(r"\bextended\b|\bclub\s+(?:mix|version|edit)\b"
                     r"|(?<![\d.])12\s*(?:\"|''|\u201d|\u2033|_|-?\s*inch\b|in\b)", re.IGNORECASE)
# What is left of an extended version's name once the cut's own words are gone, when it names no
# arrangement of its own: "Extended Mix", "12'' Version", "Original Extended Mix", "Extended Edit"
_GENERIC_WORDS = frozenset({"mix", "version", "edit", "cut", "original"})
EXTENDED = "extended"
# From this token_set ratio two arrangements are one ("Extended" and "Extended Mix")
SAME_ARRANGEMENT = 80


def says_remaster(*texts: str | None) -> bool:
    """Any of `texts` calls itself a remaster: "Remastered 2024", "2011 Remaster", "The Albums Remastered"."""
    return any(t and _REMASTER_RE.search(t) for t in texts)


def says_extended(*texts: str | None) -> bool:
    """Any of `texts` names an extended cut: "Extended Mix", "(Extended)", "Club Mix", "12'' Version"."""
    return any(t and _CUT_RE.search(t) for t in texts)


def arrangement(version: str | None) -> str:
    """The remix or edit `version` names, normalised; "" for the original, remastered or not, extended or
    not. "Liquid Soul Extended Remix" is Liquid Soul's remix, at its extended length."""
    v = _REMASTER_TEXT_RE.sub(" ", version or "")
    if _CUT_RE.search(v):
        v = _CUT_RE.sub(" ", v)
        if set(norm(v).split()) <= _GENERIC_WORDS:
            return ""
    return "" if is_original(v) else norm(v)


def named_version(version: str | None) -> str:
    """`version` as matching has always read it: its whole name, extended cut included, normalised, and ""
    for the original. "X Extended Remix" agrees with "X Remix" this way, as it did before issue #187, and
    "Club Mix" is not "Extended Mix"."""
    v = _REMASTER_TEXT_RE.sub(" ", version or "")
    return "" if is_original(v) else norm(v)


@dataclass(frozen=True)
class Version:
    arrangement: str = ""          # "" is the original arrangement
    remaster: bool = False
    year: str | None = None        # the remaster's year, when its wording names one
    cut: str = ""                  # "extended", or "" for the normal length
    # The whole name as matching reads it (`named_version`); not part of what makes two Versions equal
    named: str = field(default="", compare=False)

    @property
    def is_original(self) -> bool:
        """The original arrangement at its normal length -- a remaster of it included. An extended mix is
        not: a request that names no version means the track as released, and matching never took the
        Extended Mix for it (`match._original`)."""
        return not self.arrangement and not self.cut

    @property
    def label(self) -> str:
        """What `agrees` compares: the name as read, or for a Version built by hand its parts."""
        return self.named or " ".join(p for p in (self.arrangement, self.cut) if p)


def split_version(version: str | None, *context: str | None) -> Version:
    """`version` (a mix name, a title's version text) read as a Version. `context` -- a title, an album, a
    release name, a peer's path -- can only say *remaster*: an album called "Remixes" makes no track a remix."""
    remaster_texts = [t for t in (version, *context) if t and _REMASTER_RE.search(t)]
    # the first year any of them names: "Remastered" from Deezer's album, "Remastered 2024" from the request
    year = next((m.group(0) for t in remaster_texts if (m := _YEAR_RE.search(t))), None)
    return Version(arrangement(version), bool(remaster_texts), year, EXTENDED if says_extended(version) else "",
                   named_version(version))


def record_version(record: CatalogTrack, asked: Version | None = None) -> Version:
    """A Beatport (or stand-in) record's version as far as `asked` goes. Beatport files some remasters as
    "Original Mix" on a release that says "Remastered", so the release name counts when the remaster was
    asked for. It does not otherwise: classic catalogue sits on remastered releases, and a request that
    names no version means that track, already filed as "Original Mix" (`release_counts`)."""
    return split_version(record.mix_name, record.title, record.release_name if release_counts(asked) else None)


def release_counts(asked: Version | None) -> bool:
    """Whether a release's or album's own wording says which version a record is: only when the remaster
    was asked for. The record's title and mix always do."""
    return asked is not None and asked.remaster


def same_arrangement(a: str, b: str) -> bool:
    if not a or not b:
        return a == b
    return fuzz.token_set_ratio(a, b) >= SAME_ARRANGEMENT


def agrees(asked: Version, have: Version) -> bool:
    """Is `have` the version `asked` names, for matching a record or a candidate? The same name as read
    (`named_version`) and a remaster exactly when one was asked for. The years need not agree: peers and
    Deezer file the 2024 "LSD" remaster under the box set's 2023. A date alone never makes a record the
    remaster -- a 2025 compilation of the original is not one -- it only confirms a downloaded file
    (`check_version`)."""
    return same_arrangement(asked.label, have.label) and asked.remaster == have.remaster


def same_owned_version(asked: Version, have: Version) -> bool:
    """`agrees`, and the same cut: what the library holds, where the extended mix and the radio edit are
    two versions and owning one is not owning the other (issue #187). "X Extended Remix" is not "X Remix"
    here, though a candidate named one may still be fetched for the other."""
    return agrees(asked, have) and asked.cut == have.cut


def with_remaster(mix_name: str | None, remaster: bool) -> str | None:
    """`mix_name` worded so that it says remaster when the release is one, for a record whose mix does not."""
    if not remaster or says_remaster(mix_name):
        return mix_name
    return "Remastered" if split_version(mix_name).is_original else f"{mix_name} Remastered"


# The short versions an extended mix is the long one of: asked for one of these, the extended mix is the
# original's (issue #187)
RADIO_EDITS = frozenset({"radio edit", "radio mix", "radio version", "radio cut", "single version", "single edit",
                         "short edit", "short version", "edit"})


def wants_extended(mix_name: str | None) -> bool:
    """Whether a request for `mix_name` can be answered with its extended mix when the owner prefers one:
    not when it already is one, and not for a remaster, which is a question about the release that the
    extended mix's would only blur."""
    v = split_version(mix_name)
    return not v.cut and not v.remaster


def extended_target(mix_name: str | None) -> str:
    """The arrangement whose extended cut is wanted for a request for `mix_name`: the original's for a radio
    edit ("" either way), the remix's own for a remix."""
    a = split_version(mix_name).arrangement
    return "" if a in RADIO_EDITS else a


_LAST_VERSION_WORD = re.compile(r"\b(remix|mix|rework|dub|edit|version)\s*$", re.IGNORECASE)


def extended_mix_name(mix_name: str | None) -> str:
    """The mix name an extended copy of the `mix_name` record is filed under: "Extended Mix" for the
    original or its radio edit, "Bad Company Extended Remix" for a remix. A name that already says
    extended is kept as it is."""
    if says_extended(mix_name):
        return mix_name or "Extended Mix"
    v = split_version(mix_name)
    if not v.arrangement or v.arrangement == "radio edit":
        return "Extended Mix Remastered" if v.remaster else "Extended Mix"
    m = _LAST_VERSION_WORD.search(mix_name or "")
    return f"{mix_name[:m.start()]}Extended {m.group(1)}" if m else f"{mix_name} (Extended)"


def file_cut(path: str = "", tags: dict[str, str] | None = None) -> str:
    """The cut a downloaded file says it is, by what names this one track: its file name, its title and
    mix tags. A peer's folder ("Extended Mixes") or an album ("Extended Versions") is about more than it."""
    tags = tags or {}
    filename = path.replace("\\", "/").rsplit("/", 1)[-1]
    return EXTENDED if says_extended(filename, tags.get("title"), tags.get("mix")) else ""


@dataclass(frozen=True)
class VersionCheck:
    """What the downloaded file's own name and tags say about its version, after the fingerprint has
    proved the recording. `unconfirmed` files the track with a flag rather than claiming the version."""
    status: str                    # "confirmed" | "unconfirmed"
    reason: str

    def to_dict(self) -> dict:
        return asdict(self)


def check_version(asked: Version, *, path: str = "", tags: dict[str, str] | None = None,
                  metadata: tuple[str | None, ...] = (), release_year: str | None = None) -> VersionCheck | None:
    """Evidence for the version of a file whose recording the fingerprint has already matched. `path` is
    the peer's, `tags` the file's own as it arrived, `metadata` what the record it was fetched as says
    (Deezer's album and title), `release_year` that record's or the file's date.

    A remaster is confirmed when the path, the title, mix or album tag, or the record says remaster, or
    the date is the remaster's year or later. An original is only ever questioned, never confirmed, and
    only by what names this one track -- the file name, its title and mix tags, the record it came as:
    a peer's folder or a comment saying "remastered" is too often about something else (None otherwise)."""
    tags = tags or {}
    if not asked.remaster:
        filename = path.replace("\\", "/").rsplit("/", 1)[-1]
        if says_remaster(filename, tags.get("title"), tags.get("mix"), *metadata):
            return VersionCheck("unconfirmed", "the file says remaster, and the original was asked for")
        return None
    if says_remaster(path, tags.get("title"), tags.get("mix"), tags.get("album"), *metadata):
        return VersionCheck("confirmed", "its name or tags say remaster")
    year = (tags.get("date") or "")[:4] or (release_year or "")[:4] or None
    if asked.year and year and year >= asked.year:
        return VersionCheck("confirmed", f"dated {year}, the remaster's year or later")
    return VersionCheck("unconfirmed", "nothing in its name or tags says it is the remaster")
