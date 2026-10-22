from __future__ import annotations

import asyncio
import dataclasses
import json
import re
import unicodedata
from collections.abc import Iterable
from urllib.parse import quote

from rapidfuzz import fuzz

from .models import TITLE_FLOOR, CatalogTrack, Query, norm
from .version import Version, agrees, record_version, says_remaster, split_version

_NEXT_RE = re.compile(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.DOTALL)
BROWSER = "chrome"
DURATION_SLACK_S = 3
ARTIST_SAME = 90      # token_sort ratio from which a record's artist is the asked one, spelling aside


class CatalogError(Exception):
    pass


class CatalogParseError(CatalogError):
    pass


class CatalogUnavailable(CatalogError):
    pass


def _walk_tracks(obj):
    if isinstance(obj, dict):
        if "track_id" in obj and "track_name" in obj and "bpm" in obj:
            yield obj
            return
        for v in obj.values():
            yield from _walk_tracks(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk_tracks(v)


def _record(d: dict) -> CatalogTrack:
    genres = d.get("genre") or []
    genre = genres[0].get("genre_name") if genres else "Unknown"
    sub = (d.get("sub_genre") or {}).get("sub_genre_name")
    release = d.get("release") or {}
    art = release.get("release_image_dynamic_uri")
    if art:
        art = art.replace("{w}", "1400").replace("{h}", "1400")
    date = d.get("publish_date") or d.get("release_date")
    return CatalogTrack(
        id=int(d["track_id"]),
        artist=", ".join(a.get("artist_name", "") for a in d.get("artists") or []) or "Unknown",
        title=d.get("track_name") or "",
        mix_name=d.get("mix_name") or "Original Mix",
        label=(d.get("label") or {}).get("label_name") or "Unknown",
        genre=genre,
        isrc=d.get("isrc") or None,
        sub_genre=sub,
        catalog_number=d.get("catalog_number"),
        release_name=release.get("release_name"),
        release_date=date[:10] if date else None,
        bpm=int(d["bpm"]) if d.get("bpm") else None,
        key=d.get("key_name"),
        duration_ms=int(d["length"]) if d.get("length") else None,
        artwork_url=art,
    )


def parse_search_html(html: str) -> list[CatalogTrack]:
    m = _NEXT_RE.search(html)
    if not m:
        raise CatalogParseError("no __NEXT_DATA__ block (blocked or redesigned page)")
    data = json.loads(m.group(1))
    seen: set[int] = set()
    out: list[CatalogTrack] = []
    for d in _walk_tracks(data):
        t = _record(d)
        if t.id not in seen:
            seen.add(t.id)
            out.append(t)
    return out


_norm = norm


def _is_original(mix: str) -> bool:
    return bool(re.search(r"\boriginal\b", mix, re.IGNORECASE))


def _matches_length(t: CatalogTrack, wanted_s: int | None) -> bool:
    return wanted_s is not None and t.duration_s is not None and abs(t.duration_s - wanted_s) <= DURATION_SLACK_S


def _length_pins_a_version(query: Query, tracks: list[CatalogTrack]) -> bool:
    """The requested length singles out something that is not tagged "Original Mix", and no original mix is
    that length. Then the tag is Beatport's filing, not a different recording, and the flat original-mix
    bonus is describing the wrong release: an album cut tagged "Album Edit" loses to a later compilation
    tagged "Original Mix" whose master exists on no other release. `match._pinned_by_length` decides the same
    question for source candidates; this is that rule where the catalogue itself is being chosen.

    Only a release of the asked song can pin (`_is_the_song`): the rule is about how Beatport filed one
    recording, and a length is no evidence that some other act's remix is it."""
    if query.duration_s is None or query.version:
        return False
    return (any(_matches_length(t, query.duration_s) and not _is_original(t.mix_name) and _is_the_song(query, t)
                for t in tracks)
            and not any(_matches_length(t, query.duration_s) and _is_original(t.mix_name) for t in tracks))


def _is_the_song(query: Query, t: CatalogTrack) -> bool:
    """The asked act among the record's credited ones, under a title that says nothing the asked one does
    not. A superset title passes the title floor -- "Sandstorm (TML 2025 RMX)" for "Sandstorm" scores 100 --
    and it is exactly how a remix credit hidden in the title reads; with "D'rude" at 83 for "Darude" that
    remix, the video's length by chance, pinned itself over Darude's original (2026-10-22).

    Credits are compared act by act, accents folded: Beatport lists every act ("Darude, Ashley
    Wallbridge"), a video credits a singer as "ft." where Beatport adds them as an act, and "Tiësto" is
    "Tiesto" on one side or the other. A feature credit is not part of a title either."""
    if not (query.artist and query.title):
        return False
    asked = {_fold(_FEAT_RE.sub(" ", query.artist)), _fold(_CREDIT_SPLIT_RE.split(query.artist)[0])}
    credited = {_fold(t.artist)} | {_fold(a) for a in _RECORD_ACTS_RE.split(t.artist)}
    if not any(a and c and fuzz.token_sort_ratio(a, c) >= ARTIST_SAME for a in asked for c in credited):
        return False
    return set(_fold(_FEAT_RE.sub(" ", t.title)).split()) <= set(_fold(_FEAT_RE.sub(" ", query.title)).split())


# "feat. X" / "ft X" / "featuring X" to the end of its bracket; bracketless, up to a dash or a bracket, so
# "Sandstorm feat. X - TML Remix" keeps the remix credit that makes it another song (#178)
_FEAT_RE = re.compile(r"[(\[]\s*\b(?:feat|ft|featuring)\b\.?[^)\]]*[)\]]?"
                      r"|\b(?:feat|ft|featuring)\b\.?(?:(?!\s+[-–—]\s)[^()\[\]])*", re.IGNORECASE)
# Where a record's credit splits into acts: Beatport's ", " and a featured act it put in the name. Not "&",
# "x" or "vs": on this side they are as likely part of one act's name ("Above & Beyond").
_RECORD_ACTS_RE = re.compile(r"\s*(?:,|\b(?:feat|ft|featuring)\b\.?)\s*", re.IGNORECASE)
# Where the asked artist's main act ends: "A & B", "A x B", "A vs B", "A, B", "A feat. B"
_CREDIT_SPLIT_RE = re.compile(r"\s*(?:,|&|\bx\b|\bvs\b\.?|\bfeat\b\.?|\bft\b\.?|\bfeaturing\b)\s*",
                              re.IGNORECASE)


def _fold(s: str) -> str:
    """`norm`, after taking the accents off: "Tiësto" and "Zoë" would otherwise lose a letter to it."""
    return _norm("".join(ch for ch in unicodedata.normalize("NFKD", s) if not unicodedata.combining(ch)))


def _same_isrc(t: CatalogTrack, isrc: str | None) -> bool:
    return bool(isrc and t.isrc and t.isrc.upper() == isrc.upper())


def best_match(query: Query, tracks: list[CatalogTrack], preferred_isrc: str | None = None) -> CatalogTrack | None:
    if not tracks:
        return None
    want_version = _norm(query.version) if query.version else None
    # The asked version against each record's, its release name counted: Beatport files some remasters as
    # "Original Mix" on a release that says "Remastered" (issue #172)
    asked = split_version(query.version) if query.version else Version()
    pinned = _length_pins_a_version(query, tracks)
    scored: list[tuple[float, CatalogTrack]] = []
    agreeing: dict[int, bool] = {}
    for t in tracks:
        agreeing[t.id] = agrees(asked, record_version(t))
        if want_version and not agreeing[t.id]:
            continue
        if query.artist and query.title:
            # token_sort for the artist: "Hallucinogen In Dub" must not equal "Hallucinogen"
            a = fuzz.token_sort_ratio(_norm(query.artist), _norm(t.artist))
            ti = fuzz.token_set_ratio(_norm(query.title), _norm(t.title))
            if ti < TITLE_FLOOR and not (_same_isrc(t, preferred_isrc) and agreeing[t.id]):
                # A weighted average lets artist 100 carry title 44 over the bar. The source's ISRC is
                # the one thing that outranks the title: it names the recording itself.
                continue
            s = 0.5 * a + 0.5 * ti
        else:
            # token_sort, not token_set: token_set scores 100 for any *subset* of tokens, so
            # "The Void - Into the Void" ties the real record for "astral projection into the void"
            s = fuzz.token_sort_ratio(_norm(query.raw), _norm(f"{t.artist} {t.title}"))
        if not want_version and not _is_original(t.mix_name) and not (
                pinned and _matches_length(t, query.duration_s) and _is_the_song(query, t)):
            s -= 25
        if query.duration_s and t.duration_s:
            # the video's length picks the release: 15 s off costs the same as a wrong version; a few
            # seconds is encoding slack between releases of the same recording, not a difference
            s -= min(max(abs(t.duration_s - query.duration_s) - DURATION_SLACK_S, 0), 15) * 25 / 15
        scored.append((s, t))
    if not scored:
        return None
    # equal scores: the earliest release (the original album, not a later compilation), then the lowest id
    # Identical artist/title/mix releases can have different masters and labels. When the source
    # identifies the recording by ISRC, that evidence outranks a release-date tie-breaker.
    # A remaster can share the original's ISRC, so the ISRC only counts for the asked version, and an
    # original request takes the original release over its remaster on a tie.
    scored.sort(key=lambda x: (-round(x[0], 3), not agreeing[x[1].id],
                               not _same_isrc(x[1], preferred_isrc),
                               x[1].release_date or "9999", x[1].id))
    s, t = scored[0]
    return t if s >= 70 else None


#: A remaster names itself in its title, mix or release ("Remastered 2024", "2011 Remaster"). Its year is
#: the point of it, so nothing here dates one back to the original.
_DATE_RE = re.compile(r"(?:19|20)\d{2}(?:-\d{2}-\d{2})?")


is_remaster = says_remaster


def _date(value: str | None) -> str | None:
    """A YYYY or YYYY-MM-DD date, or None for anything else."""
    return value if value and _DATE_RE.fullmatch(value) else None


def _same_recording(a: CatalogTrack, b: CatalogTrack) -> bool:
    """Beatport's records of one recording on different releases. The ISRC names a recording, so two that
    both carry one decide it alone (a remaster has its own); otherwise the same act, title and mix at the
    same length. Either way a length that differs is a different cut."""
    if a.duration_s is not None and b.duration_s is not None and abs(a.duration_s - b.duration_s) > DURATION_SLACK_S:
        return False
    if a.isrc and b.isrc:
        return a.isrc.upper() == b.isrc.upper()
    return (a.duration_s is not None and b.duration_s is not None and _fold(a.artist) == _fold(b.artist)
            and _fold(a.title) == _fold(b.title)
            and (_fold(a.mix_name) == _fold(b.mix_name) or (_is_original(a.mix_name) and _is_original(b.mix_name))))


def earliest_date(record: CatalogTrack, dates: Iterable[str | None], asked: str | None = None) -> str | None:
    """The earliest of `dates` when its year is before the record's own, else None. A remaster keeps its
    own year, whether the record or the request (`asked`, its version text) says it is one: that release
    is what was asked for, and its year is what tells it from the original."""
    if is_remaster(record.title, record.mix_name, record.release_name, asked):
        return None
    own = _date(record.release_date)
    found = sorted(d for d in map(_date, dates) if d)
    if not found or (own and found[0][:4] >= own[:4]):
        return None
    return found[0]


def with_original_date(record: CatalogTrack, tracks: Iterable[CatalogTrack] = (), also: Iterable[str | None] = (),
                       asked: str | None = None) -> CatalogTrack:
    """`record` with `original_date` set from the earliest of Beatport's other releases of the same recording
    in `tracks` -- the search results already in hand -- and any `also` date another source vouched for.
    Beatport often sells a recording only on a later compilation or reissue, and its date is that release's:
    "One More Time" was tagged 2005. No release that calls itself a remaster counts as the original, and a
    date already found is kept unless an earlier one turns up. Nothing new is fetched for this."""
    same = [t.release_date for t in tracks if t.id != record.id and _same_recording(record, t)
            and not is_remaster(t.title, t.mix_name, t.release_name)]
    earliest = earliest_date(record, [record.original_date, *same, *also], asked)
    return dataclasses.replace(record, original_date=earliest) if earliest != record.original_date else record


class BeatportCatalog:
    BASE = "https://www.beatport.com/search/tracks?q="
    # Beatport is a web page being scraped, not an API we are entitled to. With the whole queue running at
    # once, fourteen simultaneous search pages is exactly the pattern that earns a 403 -- and a 403 is a
    # `CatalogUnavailable` on every one of those requests, so the queue would back off as a body instead
    # of identifying anything. A handful in flight keeps the lookups quick and the site unbothered.
    CONCURRENCY = 3

    def __init__(self) -> None:
        self._slots = asyncio.Semaphore(self.CONCURRENCY)

    @staticmethod
    def search_url(text: str) -> str:
        return BeatportCatalog.BASE + quote(text)

    def _get(self, url: str) -> str:
        from curl_cffi import requests

        r = requests.get(url, impersonate=BROWSER, timeout=30)
        if r.status_code in (403, 429, 503) or r.status_code >= 500:
            raise CatalogUnavailable(f"beatport http {r.status_code}")
        return r.text

    @staticmethod
    def query_text(query: Query) -> str:
        if query.version and query.artist and query.title:
            return f"{query.artist} {query.title}"
        return query.search_text()

    async def search(self, query: Query) -> list[CatalogTrack]:
        try:
            async with self._slots:
                html = await asyncio.to_thread(self._get, self.search_url(self.query_text(query)))
        except CatalogUnavailable:
            raise
        except Exception as e:  # network errors
            raise CatalogUnavailable(str(e)) from e
        return parse_search_html(html)
