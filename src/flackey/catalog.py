from __future__ import annotations

import asyncio
import json
import re
from urllib.parse import quote

from rapidfuzz import fuzz

from .models import CatalogTrack, Query, norm

_NEXT_RE = re.compile(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.DOTALL)
BROWSER = "chrome"
DURATION_SLACK_S = 3


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
    question for source candidates; this is that rule where the catalogue itself is being chosen."""
    if query.duration_s is None or query.version:
        return False
    return (any(_matches_length(t, query.duration_s) and not _is_original(t.mix_name) for t in tracks)
            and not any(_matches_length(t, query.duration_s) and _is_original(t.mix_name) for t in tracks))


def best_match(query: Query, tracks: list[CatalogTrack]) -> CatalogTrack | None:
    if not tracks:
        return None
    want_version = _norm(query.version) if query.version else None
    pinned = _length_pins_a_version(query, tracks)
    scored: list[tuple[float, CatalogTrack]] = []
    for t in tracks:
        if want_version and fuzz.token_set_ratio(want_version, _norm(t.mix_name)) < 80:
            continue
        if query.artist and query.title:
            # token_sort for the artist: "Hallucinogen In Dub" must not equal "Hallucinogen"
            a = fuzz.token_sort_ratio(_norm(query.artist), _norm(t.artist))
            ti = fuzz.token_set_ratio(_norm(query.title), _norm(t.title))
            s = 0.5 * a + 0.5 * ti
        else:
            # token_sort, not token_set: token_set scores 100 for any *subset* of tokens, so
            # "The Void - Into the Void" ties the real record for "astral projection into the void"
            s = fuzz.token_sort_ratio(_norm(query.raw), _norm(f"{t.artist} {t.title}"))
        if not want_version and not _is_original(t.mix_name) and not (pinned and _matches_length(t, query.duration_s)):
            s -= 25
        if query.duration_s and t.duration_s:
            # the video's length picks the release: 15 s off costs the same as a wrong version; a few
            # seconds is encoding slack between releases of the same recording, not a difference
            s -= min(max(abs(t.duration_s - query.duration_s) - DURATION_SLACK_S, 0), 15) * 25 / 15
        scored.append((s, t))
    if not scored:
        return None
    # equal scores: the earliest release (the original album, not a later compilation), then the lowest id
    scored.sort(key=lambda x: (-round(x[0], 3), x[1].release_date or "9999", x[1].id))
    s, t = scored[0]
    return t if s >= 70 else None


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
