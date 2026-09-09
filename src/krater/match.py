from __future__ import annotations

import re
from dataclasses import dataclass, field

from rapidfuzz import fuzz

from .identify import parse_version
from .models import Candidate, CatalogTrack, Query, norm

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


def _candidate_title(cand: Candidate) -> str:
    t, _ = parse_version(cand.title)
    return t


def candidate_query(cand: Candidate) -> Query:
    """The query that describes exactly this candidate: its own title spelling and its own version."""
    return Query(raw=f"{cand.artist} {cand.title}", artist=cand.artist, title=_candidate_title(cand),
                 version=candidate_version(cand), duration_s=cand.duration_s)


def same_version(cand: Candidate, catalog: CatalogTrack) -> bool:
    return fuzz.token_set_ratio(_norm(candidate_version(cand)), _norm(catalog.mix_name)) >= 80


def _version_points(query: Query, cand: Candidate) -> int:
    cv = _norm(candidate_version(cand))
    if query.version:
        return W_VERSION if fuzz.token_set_ratio(_norm(query.version), cv) >= 80 else 0
    return W_VERSION if _is_original(cv) else 0


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
    isrc = bool(catalog and catalog.isrc and cand.isrc and catalog.isrc == cand.isrc)
    total = 100 if isrc else artist_pts + title_pts + version_pts + duration_pts
    return Score(total=total, artist=artist_pts, title=title_pts, version=version_pts,
                 duration=duration_pts, isrc=isrc, notes=notes)


def decide(query: Query, cands: list[Candidate], catalog: CatalogTrack | None) -> Decision:
    if not cands:
        return Decision(False, None, "no candidates from source")
    eligible: list[Candidate] = []
    for c in cands:
        c.score = score_candidate(query, c, catalog).total
        if query.version:
            if fuzz.token_set_ratio(_norm(query.version), _norm(candidate_version(c))) >= 80:
                eligible.append(c)
        else:
            eligible.append(c)
    if not eligible:
        top = max(cands, key=lambda c: c.score or 0)
        return Decision(False, top, f"no candidate matches requested version '{query.version}'")
    if query.version:
        eligible.sort(key=lambda c: (-(c.score or 0), c.rank))
    else:  # originals win ties; the bot's own order breaks the rest
        eligible.sort(key=lambda c: (-(c.score or 0), 0 if _is_original(candidate_version(c)) else 1, c.rank))
    top = eligible[0]
    pinned = not query.version and _pinned_by_length(query, top, eligible)
    if not query.version and not pinned and not any(_is_original(candidate_version(c)) for c in eligible):
        return Decision(False, top, "only remixes/edits available; no version was requested")
    if not query.version and not pinned and not _is_original(candidate_version(top)):
        return Decision(False, top, f"best match is a {candidate_version(top)}; no version was requested")
    if catalog is None:
        return Decision(False, top, "not found on Beatport; information cannot be verified")
    if (top.score or 0) < THRESHOLD:
        return Decision(False, top, f"confidence {top.score}% below {THRESHOLD}%")
    if pinned and not _is_original(candidate_version(top)):
        return Decision(True, top, f"confidence {top.score}%; the {candidate_version(top)} is the video's length")
    return Decision(True, top, f"confidence {top.score}%")


def _within_slack(c: Candidate, ref_s: int | None) -> bool:
    return ref_s is not None and c.duration_s is not None and abs(c.duration_s - ref_s) <= LENGTH_SLACK_S


def _pinned_by_length(query: Query, top: Candidate, eligible: list[Candidate]) -> bool:
    """The video's length singles out `top` (an edit or a remix): it matches, and no original does."""
    if _is_original(candidate_version(top)) or not _within_slack(top, query.duration_s):
        return False
    return not any(_is_original(candidate_version(c)) and _within_slack(c, query.duration_s) for c in eligible)
