from __future__ import annotations

import re
from dataclasses import dataclass, field

from rapidfuzz import fuzz

from .identify import parse_version
from .models import Candidate, CatalogTrack, Query, norm
from .version import Version, agrees, record_version, release_counts, says_remaster, split_version

THRESHOLD = 80
LENGTH_SLACK_S = 3  # encoding slack between releases of one recording
W_ARTIST, W_TITLE, W_VERSION, W_DURATION = 25, 25, 20, 30


@dataclass
class Score:
    total: int
    artist: int
    title: int
    version: int
    duration: int
    isrc: bool
    notes: list[str] = field(default_factory=list)


@dataclass
class Decision:
    auto: bool
    chosen: Candidate | None
    reason: str


_norm = norm


def _is_original(v: str) -> bool:
    return bool(re.search(r"\boriginal\b", v, re.IGNORECASE))


def candidate_version(cand: Candidate) -> str:
    if cand.mix_name:
        return cand.mix_name
    _, v = parse_version(cand.title)
    return v or "Original Mix"


def candidate_release(cand: Candidate, asked: Version | None = None) -> Version:
    """The candidate's version as far as `asked` goes: its title always counts, and Deezer's album when the
    remaster was asked for -- Deezer lists some remasters with no version text, on an album that says it
    (issue #172). An album alone never turns a track the owner already has into another version."""
    return split_version(candidate_version(cand), cand.title, cand.album if release_counts(asked) else None)


def eligible(query: Query, cand: Candidate) -> bool:
    """Is `cand` the version the query names? Every candidate is, when it names none."""
    if not query.version:
        return True
    asked = split_version(query.version)
    return agrees(asked, candidate_release(cand, asked))


def _candidate_title(cand: Candidate) -> str:
    t, _ = parse_version(cand.title)
    return t


def candidate_query(cand: Candidate) -> Query:
    """The query that describes exactly this candidate: its own title spelling and its own version."""
    return Query(raw=f"{cand.artist} {cand.title}", artist=cand.artist, title=_candidate_title(cand),
                 version=candidate_version(cand), duration_s=cand.duration_s)


def versions_agree(a: str | None, b: str | None) -> bool:
    return fuzz.token_set_ratio(_norm(a), _norm(b)) >= 80


def same_version(cand: Candidate, catalog: CatalogTrack, asked: str | None = None) -> bool:
    a = split_version(asked)
    return agrees(candidate_release(cand, a), record_version(catalog, a))


def _original(cand: Candidate) -> bool:
    """What a request that names no version takes: the original arrangement, and not a record whose own
    title says remaster ("LSD (Remastered 2024)" is turned down, as before issue #172). One on an album
    that says remaster still counts -- classic catalogue sits there -- but loses ties (`_plain`) and is
    filed flagged "version unconfirmed" (`filing._lossy_version`)."""
    v = candidate_release(cand)
    return v.is_original and not v.remaster


def _plain(cand: Candidate) -> bool:
    """`_original`, and on no album that says remaster either: first among equals for an original request."""
    return _original(cand) and not says_remaster(cand.album)


def _version_points(query: Query, cand: Candidate) -> int:
    if query.version:
        return W_VERSION if eligible(query, cand) else 0
    return W_VERSION if _original(cand) else 0


def _duration_points(cand_s: int | None, ref_s: int | None, notes: list[str]) -> int:
    if cand_s is None or ref_s is None:
        notes.append("duration unknown")
        return W_DURATION
    diff = abs(cand_s - ref_s)
    if diff <= 2:
        return W_DURATION
    if diff >= 15:
        return 0
    return round(W_DURATION * (15 - diff) / 13)


def score_candidate(query: Query, cand: Candidate, catalog: CatalogTrack | None) -> Score:
    notes: list[str] = []
    ref_artist = catalog.artist if catalog else (query.artist or "")
    ref_title = catalog.title if catalog else (query.title or "")
    if not ref_artist and not ref_title:
        ref_title = query.raw
        # token_sort: a candidate whose tokens are a subset of the query must not score 100 (see catalog.best_match)
        a = fuzz.token_sort_ratio(_norm(query.raw), _norm(f"{cand.artist} {cand.title}"))
        artist_pts = round(W_ARTIST * a / 100)
        title_pts = round(W_TITLE * a / 100)
    else:
        artist_pts = round(W_ARTIST * fuzz.token_set_ratio(_norm(ref_artist), _norm(cand.artist)) / 100)
        title_pts = round(W_TITLE * fuzz.token_set_ratio(_norm(ref_title), _norm(_candidate_title(cand))) / 100)
    version_pts = _version_points(query, cand)
    # the video's length is what was asked for; a wrongly matched Beatport release must not redefine it
    ref_dur = query.duration_s or (catalog.duration_s if catalog else None)
    duration_pts = _duration_points(cand.duration_s, ref_dur, notes)
    # A remaster can carry the original's ISRC (Hallucinogen's "LSD", 2026-10-20): the ISRC names the
    # recording, so it settles the match only when the versions agree too.
    asked = split_version(query.version)
    isrc = bool(catalog and catalog.isrc and cand.isrc and catalog.isrc == cand.isrc
                and agrees(record_version(catalog, asked), candidate_release(cand, asked)))
    total = 100 if isrc else artist_pts + title_pts + version_pts + duration_pts
    return Score(total=total, artist=artist_pts, title=title_pts, version=version_pts,
                 duration=duration_pts, isrc=isrc, notes=notes)


def identification_order(query: Query, cands: list[Candidate]) -> list[Candidate]:
    """The candidates whose previews are tried against the request's own audio, in the order tried: text
    score first, and only the requested version's when the query names one. The fingerprint cannot tell a
    remaster from its original (both 0.96 against the 2024 "LSD" video), so a preview of the wrong version
    would match just as well and file the wrong one (issue #172). Call after `decide`, which scores them."""
    ordered = sorted(cands, key=lambda c: (-(c.score or 0), 0 if _plain(c) else 1))
    return [c for c in ordered if eligible(query, c)]


def decide(query: Query, cands: list[Candidate], catalog: CatalogTrack | None) -> Decision:
    if not cands:
        return Decision(False, None, "no candidates from source")
    ok: list[Candidate] = []
    for c in cands:
        c.score = score_candidate(query, c, catalog).total
        if eligible(query, c):
            ok.append(c)
    if not ok:
        top = max(cands, key=lambda c: c.score or 0)
        return Decision(False, top, f"no candidate matches requested version '{query.version}'")
    if query.version:
        ok.sort(key=lambda c: (-(c.score or 0), c.rank))
    else:  # originals win ties, and the original release a remaster of it; the bot's own order breaks the rest
        ok.sort(key=lambda c: (-(c.score or 0), 0 if _plain(c) else 1 if _original(c) else 2,
                               c.rank))
    top = ok[0]
    pinned = not query.version and _pinned_by_length(query, top, ok)
    if not query.version and not pinned and not any(_original(c) for c in ok):
        return Decision(False, top, "only remixes/edits available; no version was requested")
    if not query.version and not pinned and not _original(top):
        return Decision(False, top, f"best match is a {candidate_version(top)}; no version was requested")
    if (top.score or 0) < THRESHOLD:
        return Decision(False, top, f"confidence {top.score}% below {THRESHOLD}%")
    if pinned and not _original(top):
        return Decision(True, top, f"confidence {top.score}%; the {candidate_version(top)} is the video's length")
    if catalog is None:
        return Decision(True, top, f"confidence {top.score}%; not on Beatport, the recording check decides")
    return Decision(True, top, f"confidence {top.score}%")


def _within_slack(c: Candidate, ref_s: int | None) -> bool:
    return ref_s is not None and c.duration_s is not None and abs(c.duration_s - ref_s) <= LENGTH_SLACK_S


def _pinned_by_length(query: Query, top: Candidate, eligible: list[Candidate]) -> bool:
    """The video's length singles out `top` (an edit or a remix): it matches, and no original does. Never a
    remaster: it is the original's length, so the length cannot say the video was the remaster (issue #172)."""
    if candidate_release(top).is_original or not _within_slack(top, query.duration_s):
        return False
    return not any(_original(c) and _within_slack(c, query.duration_s) for c in eligible)
