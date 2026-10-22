"""Tags for a track Beatport could not describe (issue #146).

Beatport stays the first choice. Since 2026-10-14 its search often answers with a Cloudflare challenge,
and a track filed then used to carry `Unknown / Unknown`. This fills in what two catalogues that need no
login can vouch for, and only the fields that are still empty:

- **Deezer**, when the record that was downloaded is a Deezer one: label, release date, ISRC, duration and
  the 1400 px cover, from that record's own album. Nothing to match -- the chosen record *is* the track.
- **Discogs**: label, catalogue number and year (its CC0 fields), and the genre from its styles. Only from
  a release whose tracklist carries this exact track: the title near-identical once the version text is
  set aside, the same version, and a length within 15 s. With Deezer it still supplies the catalogue
  number and the genre, which Deezer does not have. Never its images: they are Restricted Data.

Nothing is guessed. A field no release passes the rules for stays empty.
"""
from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import json
import logging
import os
import re
import tempfile
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from rapidfuzz import fuzz

from . import __version__
from .catalog import with_original_date
from .deezer import DeezerApi, DeezerError
from .identify import parse_version
from .models import CatalogTrack, is_original, norm

log = logging.getLogger(__name__)

USER_AGENT = f"Flackey/{__version__} +https://github.com/EyalDelarea/flackey"

# How far a Discogs track's length may be from the file's and still be the same recording. Measured on
# the library in issue #146: wide enough for the encoding and fade differences between releases of one
# master, narrow enough to refuse an edit or an extended mix of the same title.
DURATION_GATE_S = 15
# How close the titles must be once the version text is set aside (rapidfuzz `ratio`, 0-100). Strict on
# purpose: the loose first draft counted "Rain" -> "Don't Stop The Rain" as found.
TITLE_MIN = 92
# Discogs releases fetched per track to read their tracklists. Each costs one request of the 25 a minute.
MAX_RELEASES = 4
SEARCH_PER_PAGE = 25


# ---- titles and versions ---------------------------------------------------------------------------

_PART_RE = re.compile(r"\bpt\b\.?", re.IGNORECASE)
_REMASTER_RE = re.compile(r"\bremaster", re.IGNORECASE)
# The remaster wording as a whole, so "2024 Remaster" / "Remastered 2011" / "Digital Remaster" leave
# nothing behind that would read as a version of its own.
_REMASTER_TEXT_RE = re.compile(r"\b(?:(?:19|20)\d{2}\s+)?(?:digital(?:ly)?\s+)?remaster(?:ed)?"
                               r"(?:\s+version)?(?:\s+(?:19|20)\d{2})?\b", re.IGNORECASE)
_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
_DISCOGS_SUFFIX_RE = re.compile(r"\s*\(\d+\)$")     # Discogs' "Artist (2)" disambiguation
_ARTIST_SPLIT_RE = re.compile(r"\s*(?:,|&|\bfeat\b\.?|\bft\b\.?|\bfeaturing\b|\bvs\b\.?)\s*", re.IGNORECASE)


def title_key(title: str) -> str:
    """The title as matching compares it: "Pt." and "Pt" read as "Part", "&" as "and", then `norm`."""
    t = _PART_RE.sub("part ", title)
    t = t.replace("&", " and ")
    return norm(t)


def split_title(title: str) -> tuple[str, list[str]]:
    """The title without its trailing version text, and that text: "Snarling (Remix)" -> ("Snarling",
    ["Remix"]). Repeated, for a title that carries two ("X (Remastered 2024) (Original Mix)")."""
    base, versions = title, []
    while True:
        rest, version = parse_version(base)
        if version is None or not rest:
            return base.strip(), versions
        versions.insert(0, version)
        base = rest


def version_key(versions: list[str]) -> str:
    """What makes the recording a different one: the remix or edit, normalised. A remaster is the same
    recording and an "Original Mix" is no version at all, so both read as ""."""
    parts: list[str] = []
    for v in versions:
        v = _REMASTER_TEXT_RE.sub(" ", v)
        if is_original(v):
            continue
        k = norm(v)
        if k and k not in parts:
            parts.append(k)
    return " ".join(parts)


def same_version(a: str, b: str) -> bool:
    if a == b:
        return True
    # token_sort, not token_set: a bare "remix" is not the same as "bad company remix"
    return bool(a and b) and fuzz.token_sort_ratio(a, b) >= 90


@dataclass
class Wanted:
    """The track being tagged, as Discogs is searched and checked for it."""
    artist: str
    title: str                  # without the version text
    version: str                # version_key; "" for the original
    duration_s: int | None
    remaster: bool = False
    remaster_year: str | None = None

    @classmethod
    def of(cls, artist: str, title: str, mix_name: str | None, duration_s: int | None,
           album: str | None = None) -> Wanted:
        base, versions = split_title(title)
        if mix_name:
            versions.append(mix_name)
        texts = versions + ([album] if album else [])
        remaster_text = next((t for t in texts if _REMASTER_RE.search(t)), None)
        year = _YEAR_RE.search(remaster_text) if remaster_text else None
        return cls(artist=artist, title=base, version=version_key(versions), duration_s=duration_s,
                   remaster=remaster_text is not None, remaster_year=year.group(0) if year else None)

    @property
    def first_artist(self) -> str:
        return _ARTIST_SPLIT_RE.split(self.artist.strip(), maxsplit=1)[0].strip()


def parse_duration(text: str | None) -> int | None:
    """Discogs' "6:47" or "1:02:03" in seconds; None for the empty string most tracks carry."""
    if not text or not re.fullmatch(r"\d+(?::\d{1,2}){1,2}", text.strip()):
        return None
    total = 0
    for part in text.strip().split(":"):
        total = total * 60 + int(part)
    return total or None


def _discogs_name(name: str) -> str:
    return _DISCOGS_SUFFIX_RE.sub("", (name or "").strip()).rstrip("*").strip()


def _artist_line(artists: list[dict]) -> str:
    """Discogs' artist credits as one line, the way they read on the release."""
    out = ""
    for a in artists:
        out += _discogs_name(a.get("anv") or a.get("name") or "")
        join = (a.get("join") or "").strip()
        out += f" {join} " if join and join != "," else (", " if join == "," else " ")
    return " ".join(out.split()).strip(" ,")


def artist_matches(wanted: Wanted, artists: list[dict]) -> bool:
    if not artists:
        return False
    first = norm(_discogs_name(artists[0].get("anv") or artists[0].get("name") or ""))
    if first and first == norm(wanted.first_artist):
        return True
    names = {norm(_discogs_name(a.get("name") or "")) for a in artists}
    if norm(wanted.first_artist) in names:
        return True
    return fuzz.token_sort_ratio(norm(wanted.artist), norm(_artist_line(artists))) >= 90


def _tracks(tracklist: list[dict]):
    for t in tracklist or []:
        if t.get("type_", "track") == "track":
            yield t
        yield from t.get("sub_tracks") or []


@dataclass
class TrackCheck:
    passed: bool
    why: str


def check_track(wanted: Wanted, track: dict, release_artists: list[dict]) -> TrackCheck:
    """Whether a Discogs tracklist entry is the wanted recording: same artist, near-identical title, same
    version, and a length within the gate. Every rule must pass; a missing length fails the gate."""
    base, versions = split_title(track.get("title") or "")
    score = fuzz.ratio(title_key(wanted.title), title_key(base))
    if score < TITLE_MIN:
        return TrackCheck(False, f"title {base!r} ({score:.0f})")
    if not artist_matches(wanted, track.get("artists") or release_artists):
        return TrackCheck(False, f"artist on {base!r}")
    theirs = version_key(versions)
    if not same_version(wanted.version, theirs):
        return TrackCheck(False, f"version {theirs or 'original'!r} != {wanted.version or 'original'!r}")
    length = parse_duration(track.get("duration"))
    if length is None or wanted.duration_s is None:
        return TrackCheck(False, "no length to compare")
    if abs(length - wanted.duration_s) > DURATION_GATE_S:
        return TrackCheck(False, f"length {length}s vs {wanted.duration_s}s")
    return TrackCheck(True, "ok")


# ---- genre ---------------------------------------------------------------------------------------

# Discogs style -> (Beatport genre, how specific that genre is). Rekordbox keeps one genre list, so a
# Discogs-tagged track lands in the same genre a Beatport-tagged one would. 2 names one Beatport genre
# precisely; 1 is an umbrella style ("Trance", "House") whose Beatport genre is only the broad one. A
# style missing here never becomes a genre: an unmapped name is left out rather than guessed at.
STYLE_GENRE: dict[str, tuple[str, int]] = {
    "Goa Trance": ("Psy-Trance", 2),
    "Psy-Trance": ("Psy-Trance", 2),
    "Trance": ("Trance (Main Floor)", 1),
    "Progressive Trance": ("Trance (Main Floor)", 1),
    "Hard Trance": ("Trance (Main Floor)", 2),
    "Tech Trance": ("Trance (Raw / Deep / Hypnotic)", 2),
    "Ambient": ("Ambient / Experimental", 2),
    "Dark Ambient": ("Ambient / Experimental", 2),
    "Experimental": ("Ambient / Experimental", 2),
    "Drone": ("Ambient / Experimental", 2),
    "Downtempo": ("Downtempo", 2),
    "Trip Hop": ("Downtempo", 2),
    "IDM": ("Electronica", 2),
    "Leftfield": ("Electronica", 1),
    "House": ("House", 1),
    "Acid House": ("House", 2),
    "Garage House": ("House", 2),
    "Deep House": ("Deep House", 2),
    "Tech House": ("Tech House", 2),
    "Progressive House": ("Progressive House", 2),
    "Afro House": ("Afro House", 2),
    "Techno": ("Techno (Peak Time / Driving)", 1),
    "Hard Techno": ("Hard Techno", 2),
    "Dub Techno": ("Techno (Raw / Deep / Hypnotic)", 2),
    "Minimal": ("Minimal / Deep Tech", 2),
    "Minimal Techno": ("Minimal / Deep Tech", 2),
    "Electro": ("Electro (Classic / Detroit / Modern)", 1),
    "Breakbeat": ("Breaks / Breakbeat / UK Bass", 2),
    "Breaks": ("Breaks / Breakbeat / UK Bass", 2),
    "Big Beat": ("Breaks / Breakbeat / UK Bass", 2),
    "Drum n Bass": ("Drum & Bass", 2),
    "Jungle": ("Drum & Bass", 2),
    "Dubstep": ("Dubstep", 2),
    "Grime": ("140 / Deep Dubstep / Grime", 2),
    "UK Garage": ("UK Garage / Bassline", 2),
    "Speed Garage": ("UK Garage / Bassline", 2),
    "Bassline": ("UK Garage / Bassline", 2),
    "Hardcore": ("Hard Dance / Hardcore / Neo Rave", 2),
    "Happy Hardcore": ("Hard Dance / Hardcore / Neo Rave", 2),
    "Gabber": ("Hard Dance / Hardcore / Neo Rave", 2),
    "Hardstyle": ("Hard Dance / Hardcore / Neo Rave", 2),
    "Hard House": ("Hard Dance / Hardcore / Neo Rave", 2),
    "Disco": ("Nu Disco / Disco", 2),
    "Nu-Disco": ("Nu Disco / Disco", 2),
    "Italo-Disco": ("Nu Disco / Disco", 2),
    "Euro House": ("Dance / Pop", 2),
    "Eurodance": ("Dance / Pop", 2),
}


def pick_genre(styles: list[str]) -> tuple[str | None, str | None]:
    """(genre, sub-genre) from a release's styles. Discogs lists styles in no meaningful order -- "Trance,
    Goa Trance, Psy-Trance" is common -- so the first is not trusted: the style that maps to the most
    specific Beatport genre wins, and Discogs' order only breaks a tie. The sub-genre is that style, so the
    two always agree. With no mapped style there is no genre, and the sub-genre is the first style."""
    best: tuple[int, int, str, str] | None = None
    for i, style in enumerate(styles):
        mapped = STYLE_GENRE.get(style)
        if mapped is None:
            continue
        key = (-mapped[1], i, mapped[0], style)
        if best is None or key < best:
            best = key
    if best is None:
        return None, (styles[0] if styles else None)
    return best[2], best[3]


# ---- Discogs ---------------------------------------------------------------------------------------

class DiscogsError(Exception):
    pass


class Discogs:
    """Discogs' public API without a key: at most `per_minute` requests (Discogs allows about 25 a minute
    unauthenticated, per IP), and every answer cached on disk so a retry, a re-tag or the dry run does not
    spend that budget twice."""
    BASE = "https://api.discogs.com"
    PER_MINUTE = 25
    CACHE_TTL_S = 30 * 24 * 3600

    def __init__(self, http: httpx.AsyncClient, cache_dir: Path | None, *, per_minute: int = PER_MINUTE,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
                 now: Callable[[], float] = time.time):
        self.http, self.cache_dir = http, cache_dir
        self.interval = 60 / per_minute
        self.clock, self.sleep, self.now = clock, sleep, now
        self._lock = asyncio.Lock()
        self._next_at = 0.0

    def _cache_path(self, url: str) -> Path | None:
        if self.cache_dir is None:
            return None
        return self.cache_dir / f"{hashlib.sha256(url.encode()).hexdigest()[:40]}.json"

    def _cache_read(self, url: str) -> tuple[bool, dict | None]:
        path = self._cache_path(url)
        if path is None:
            return False, None
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False, None
        if (not isinstance(entry, dict) or entry.get("url") != url
                or self.now() - (entry.get("at") or 0) > self.CACHE_TTL_S):
            return False, None
        return True, entry.get("body")

    def _cache_write(self, url: str, body: dict | None) -> None:
        path = self._cache_path(url)
        if path is None:
            return
        tmp = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"url": url, "at": self.now(), "body": body}, f)
            os.replace(tmp, path)
        except (OSError, TypeError, ValueError) as e:
            log.debug("discogs cache write failed: %s", e)
            if tmp is not None:
                Path(tmp).unlink(missing_ok=True)

    async def get(self, path: str, params: dict | None = None) -> dict | None:
        """The JSON at `path`, or None for a 404. Raises DiscogsError for anything else that is not a 200,
        including the 429 of a spent budget and the 401 Discogs would answer if search ever needs a key."""
        url = str(httpx.URL(self.BASE + path, params=params))
        hit, body = self._cache_read(url)
        if hit:
            return body
        async with self._lock:
            wait = self._next_at - self.clock()
            if wait > 0:
                await self.sleep(wait)
            self._next_at = self.clock() + self.interval
            try:
                r = await self.http.get(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                                        timeout=20)
            except httpx.HTTPError as e:
                raise DiscogsError(f"discogs: {e}") from e
        if r.status_code == 404:
            self._cache_write(url, None)
            return None
        if r.status_code != 200:
            raise DiscogsError(f"discogs http {r.status_code}")
        try:
            body = r.json()
        except ValueError as e:
            raise DiscogsError(f"discogs sent no JSON ({e})") from e
        self._cache_write(url, body)
        return body


@dataclass
class DiscogsMatch:
    release_id: int
    label: str | None
    catalog_number: str | None
    year: str | None
    genre: str | None
    sub_genre: str | None
    styles: list[str] = field(default_factory=list)
    # Every year among the releases that passed and could have been chosen, the chosen one's included: the
    # label and number come from one release, but the recording is as old as the earliest of them.
    years: list[str] = field(default_factory=list)


def _label_of(labels: list[dict]) -> tuple[str | None, str | None]:
    if not labels:
        return None, None
    name = _discogs_name(labels[0].get("name") or "") or None
    catno = (labels[0].get("catno") or "").strip()
    return name, (catno if catno and catno.lower() != "none" else None)


def _same_label(a: str | None, b: str | None) -> bool:
    if not a or not b:
        return False
    strip = re.compile(r"\b(records?|recordings|music|ltd|inc)\b")
    na, nb = norm(strip.sub(" ", norm(a))), norm(strip.sub(" ", norm(b)))
    return bool(na and nb) and (na == nb or fuzz.token_sort_ratio(na, nb) >= 90)


def _is_remaster_result(r: dict) -> bool:
    texts = [r.get("title") or ""] + list(r.get("format") or [])
    return any(_REMASTER_RE.search(t) for t in texts)


def _is_remaster_release(rel: dict) -> bool:
    texts = [rel.get("title") or ""]
    for f in rel.get("formats") or []:
        texts += [f.get("text") or ""] + list(f.get("descriptions") or [])
    return any(_REMASTER_RE.search(t) for t in texts)


def _year(value) -> str | None:
    s = str(value or "").strip()
    return s if re.fullmatch(r"(19|20)\d{2}", s) else None


@dataclass
class _Passed:
    release: dict
    remaster: bool


async def discogs_match(discogs: Discogs, wanted: Wanted, prefer_label: str | None = None,
                        notes: list[str] | None = None) -> DiscogsMatch | None:
    """The Discogs release this track is on, by the rules in the module docstring, or None.

    Search results are ordered before any release is fetched: for a remaster, remaster releases first (of
    its year, when the title names one); for a Deezer-tagged track, releases on Deezer's label first, so
    the catalogue number belongs to the label the file says; then the earliest year. Release fields come
    from the first release whose tracklist passes -- for a remaster, only a remaster release counts, since
    the original's label, number and year describe a different release. Styles come from any that pass."""
    notes = notes if notes is not None else []
    if not wanted.title or not wanted.first_artist:
        notes.append("discogs: no artist and title to search for")
        return None
    found = await discogs.get("/database/search", {"type": "release", "artist": wanted.first_artist,
                                                   "track": wanted.title, "per_page": SEARCH_PER_PAGE})
    results = [r for r in (found or {}).get("results") or [] if r.get("type", "release") == "release"]
    if not results:
        notes.append("discogs: no release found")
        return None

    def order(item: tuple[int, dict]):
        i, r = item
        remaster = _is_remaster_result(r)
        return (wanted.remaster and not remaster,
                bool(wanted.remaster and wanted.remaster_year and _year(r.get("year")) != wanted.remaster_year),
                bool(prefer_label) and not any(_same_label(prefer_label, n) for n in r.get("label") or []),
                _year(r.get("year")) or "9999", i)

    def eligible(p: _Passed) -> bool:
        if not wanted.remaster:
            return True
        return p.remaster and (not wanted.remaster_year or _year(p.release.get("year")) == wanted.remaster_year)

    passed: list[_Passed] = []
    seen = 0
    for _, r in sorted(enumerate(results), key=order):
        if seen >= MAX_RELEASES:
            break
        seen += 1
        rel = await discogs.get(f"/releases/{int(r['id'])}")
        if not rel:
            continue
        checks = [check_track(wanted, t, rel.get("artists") or []) for t in _tracks(rel.get("tracklist"))]
        if not any(c.passed for c in checks):
            near = next((c.why for c in checks if not c.why.startswith("title")), None)
            notes.append(f"discogs {rel.get('id')}: {near or 'track not on it'}")
            continue
        passed.append(_Passed(rel, _is_remaster_release(rel)))
        label, _ = _label_of(rel.get("labels") or [])
        if eligible(passed[-1]) and (not prefer_label or _same_label(prefer_label, label)):
            break   # nothing later in the order could be a better answer
    if not passed:
        return None

    styles = list(passed[0].release.get("styles") or [])
    genre, sub_genre = pick_genre(styles)
    usable = [p for p in passed if eligible(p)]
    if not usable:
        notes.append("discogs: the track is a remaster and no remaster release passed; no label, number or year")
        return DiscogsMatch(release_id=int(passed[0].release["id"]), label=None, catalog_number=None,
                            year=None, genre=genre, sub_genre=sub_genre, styles=styles)
    if prefer_label:
        usable.sort(key=lambda p: not _same_label(prefer_label, _label_of(p.release.get("labels") or [])[0]))
    chosen = usable[0].release
    label, catno = _label_of(chosen.get("labels") or [])
    years = sorted({y for y in (_year(p.release.get("year")) for p in usable) if y})
    return DiscogsMatch(release_id=int(chosen["id"]), label=label, catalog_number=catno,
                        year=_year(chosen.get("year")), genre=genre, sub_genre=sub_genre, styles=styles,
                        years=years)


# ---- the lookup ------------------------------------------------------------------------------------

@dataclass
class FoundTags:
    label: str | None = None
    catalog_number: str | None = None
    release_date: str | None = None      # YYYY-MM-DD from Deezer, YYYY from Discogs
    isrc: str | None = None
    duration_ms: int | None = None
    artwork_url: str | None = None       # Deezer's only; never Discogs'
    genre: str | None = None
    sub_genre: str | None = None
    sources: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    discogs_release: int | None = None
    # Other dates the sources vouch for, earliest wins (`catalog.with_original_date`): the years of every
    # Discogs release the track passed on. Left empty for a remaster, whose own year is the one to keep.
    dates: list[str] = field(default_factory=list)

    @property
    def any(self) -> bool:
        return bool(self.sources)


class TagLookup:
    def __init__(self, deezer: DeezerApi | None, discogs: Discogs | None):
        self.deezer, self.discogs = deezer, discogs

    async def lookup(self, catalog: CatalogTrack, deezer_id: int | None = None) -> FoundTags:
        """What Deezer (when `deezer_id` names the downloaded record) and Discogs can vouch for about the
        track `catalog` describes. Network failures are notes, never exceptions: tags are an extra."""
        found = FoundTags()
        album_title: str | None = None
        if deezer_id and self.deezer is not None:
            try:
                track = await self.deezer.track(deezer_id)
                album = await self.deezer.album(track.album_id) if track.album_id else None
            except (DeezerError, ValueError, KeyError, TypeError) as e:
                found.notes.append(f"deezer: {e}")
            else:
                found.isrc = track.isrc
                found.duration_ms = track.duration_s * 1000 if track.duration_s else None
                if album is not None:
                    album_title = album.title
                    found.label, found.release_date, found.artwork_url = (album.label, album.release_date,
                                                                          album.cover_url)
                found.sources.append("deezer")
        if self.discogs is not None:
            duration_s = round(found.duration_ms / 1000) if found.duration_ms else catalog.duration_s
            wanted = Wanted.of(catalog.artist, catalog.title, catalog.mix_name, duration_s, album_title)
            try:
                match = await discogs_match(self.discogs, wanted, prefer_label=found.label, notes=found.notes)
            except (DiscogsError, KeyError, TypeError, ValueError) as e:
                found.notes.append(f"discogs: {e}")
                match = None
            if match is not None:
                took = False
                for name, value in (("label", match.label), ("catalog_number", match.catalog_number),
                                    ("release_date", match.year), ("genre", match.genre),
                                    ("sub_genre", match.sub_genre)):
                    if value and not getattr(found, name):
                        setattr(found, name, value)
                        took = True
                if took:
                    found.sources.append("discogs")
                    found.discogs_release = match.release_id
                if not wanted.remaster:
                    found.dates = list(match.years)
        return found


def _empty(value) -> bool:
    return value is None or value == "" or value == "Unknown"


def fill_missing(catalog: CatalogTrack, found: FoundTags) -> CatalogTrack:
    """`catalog` with each empty field filled from `found`. A field that already has a value keeps it, so
    this can never overwrite what Beatport (or an earlier fill) supplied."""
    changes = {}
    for name in ("label", "genre", "sub_genre", "catalog_number", "release_date", "isrc", "duration_ms",
                 "artwork_url"):
        value = getattr(found, name)
        if value and _empty(getattr(catalog, name)):
            changes[name] = value
    if changes and found.sources and catalog.tag_source is None:
        changes["tag_source"] = "+".join(found.sources)
    filled = dataclasses.replace(catalog, **changes) if changes else catalog
    # Deezer's album can be a later compilation; the earliest release Discogs found the track on is when the
    # recording came out. Only ever earlier, and never for a remaster.
    return with_original_date(filled, also=found.dates)


def default_lookup(http: httpx.AsyncClient, data_dir: Path) -> TagLookup:
    return TagLookup(DeezerApi(http), Discogs(http, data_dir / "cache" / "discogs"))
