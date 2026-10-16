"""`flackey tags-dry-run`: what the Deezer + Discogs fallback (issue #146) would write for tracks Beatport
did tag, beside Beatport's own tags, so the fallback can be checked against real tracks.

Each track is looked up the way filing would look it up with Beatport down: keyed by the record that was
downloaded (its Deezer id, artist, title, version and length), never by Beatport's spelling. Reads the
library and the two APIs; writes nothing but the report. Discogs answers are cached, so a second run over
the same tracks costs no requests."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from .match import candidate_version
from .models import CatalogTrack, Track, norm
from .store import Store
from .tag_lookup import FoundTags, TagLookup


@dataclass
class DryRunRow:
    track: Track
    beatport: CatalogTrack
    found: FoundTags
    keyed_by: str        # "deezer" when the downloaded record was a Deezer one, else "track"


def stand_in_for(store: Store, track: Track, beatport: CatalogTrack) -> tuple[CatalogTrack, int | None, str]:
    """The stand-in record filing would have built without Beatport, and the Deezer id it would look up."""
    cand = None
    if track.request_id is not None:
        try:
            req = store.get_request(track.request_id)
            if req.chosen_candidate_id is not None:
                cand = store.get_candidate(req.chosen_candidate_id)
        except KeyError:
            cand = None
    if cand is not None:
        stand_in = CatalogTrack(id=-1, artist=cand.artist, title=cand.title, mix_name=candidate_version(cand),
                                label="Unknown", genre="Unknown", isrc=cand.isrc,
                                duration_ms=(cand.duration_s or 0) * 1000 or None)
        return stand_in, cand.deezer_id, "deezer" if cand.deezer_id else "track"
    duration = track.duration_s or beatport.duration_s
    stand_in = CatalogTrack(id=-1, artist=track.artist, title=track.title, mix_name=track.mix_name,
                            label="Unknown", genre="Unknown", duration_ms=(duration or 0) * 1000 or None)
    return stand_in, None, "track"


async def run(store: Store, lookup: TagLookup, *, limit: int = 500,
              progress: Callable[[str], None] | None = None) -> list[DryRunRow]:
    rows: list[DryRunRow] = []
    # The whole library, then the limit: it counts Beatport-tagged tracks, not rows (a library is thousands)
    every = store.list_tracks(limit=1_000_000)
    tracks = [t for t in every if t.catalog_track_id and t.catalog_track_id > 0][:limit]
    for n, t in enumerate(tracks, start=1):
        beatport = store.get_catalog_track(t.catalog_track_id)
        if beatport is None:
            continue
        stand_in, deezer_id, keyed_by = stand_in_for(store, t, beatport)
        found = await lookup.lookup(stand_in, deezer_id)
        rows.append(DryRunRow(t, beatport, found, keyed_by))
        if progress:
            progress(f"[{n}/{len(tracks)}] {t.artist} - {t.title}: {'+'.join(found.sources) or 'nothing'}")
    return rows


def _year(date: str | None) -> str:
    return (date or "")[:4]


def _agree(a: str | None, b: str | None) -> bool | None:
    """None when either side is empty: nothing to compare."""
    if not a or not b or a == "Unknown" or b == "Unknown":
        return None
    return norm(a) == norm(b)


FIELDS = (
    ("Label", lambda bp: bp.label, lambda f: f.label),
    ("Cat. no.", lambda bp: bp.catalog_number, lambda f: f.catalog_number),
    ("Year", lambda bp: _year(bp.release_date), lambda f: _year(f.release_date)),
    ("Genre", lambda bp: bp.genre, lambda f: f.genre),
    ("Sub-genre", lambda bp: bp.sub_genre, lambda f: f.sub_genre),
    ("ISRC", lambda bp: bp.isrc, lambda f: f.isrc),
)


def _cell(text: str | None) -> str:
    return (text or "–").replace("|", "\\|")


def render(rows: list[DryRunRow]) -> str:
    total = len(rows)
    lines = ["# Tags without Beatport: dry run", "",
             f"{total} Beatport-tagged tracks, looked up as if Beatport had been unavailable.", ""]
    if total:
        by_source: dict[str, int] = {}
        for r in rows:
            key = "+".join(r.found.sources) or "nothing"
            by_source[key] = by_source.get(key, 0) + 1
        lines.append("| Tags from | Tracks |")
        lines.append("|---|---|")
        for key, n in sorted(by_source.items(), key=lambda kv: -kv[1]):
            lines.append(f"| {key} | {n} ({n * 100 // total} %) |")
        lines += ["", "| Field | Filled | Agrees with Beatport |", "|---|---|---|"]
        for name, bp_of, found_of in FIELDS:
            filled = [r for r in rows if found_of(r.found)]
            verdicts = [v for r in filled if (v := _agree(bp_of(r.beatport), found_of(r.found))) is not None]
            agree = f"{sum(verdicts)}/{len(verdicts)}" if verdicts else "–"
            lines.append(f"| {name} | {len(filled)}/{total} | {agree} |")
        cover = sum(1 for r in rows if r.found.artwork_url)
        lines.append(f"| Cover | {cover}/{total} | – |")
    lines += ["", "## Tracks", "",
              "Each cell is Beatport → fallback. `–` is empty; **bold** marks a disagreement.", "",
              "| Track | Keyed by | From | " + " | ".join(n for n, _, _ in FIELDS) + " | Notes |",
              "|---|---|---|" + "---|" * len(FIELDS) + "---|"]
    for r in rows:
        cells = []
        for _, bp_of, found_of in FIELDS:
            bp, new = bp_of(r.beatport), found_of(r.found)
            text = f"{_cell(bp)} → {_cell(new)}"
            cells.append(f"**{text}**" if _agree(bp, new) is False else text)
        name = _cell(f"{r.track.artist} – {r.track.title} ({r.track.mix_name})")
        notes = _cell("; ".join(r.found.notes[:3]))
        lines.append(f"| {name} | {r.keyed_by} | {'+'.join(r.found.sources) or '–'} | "
                     + " | ".join(cells) + f" | {notes} |")
    return "\n".join(lines) + "\n"
