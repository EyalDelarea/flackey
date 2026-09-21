"""Fingerprint every filed track against the audio of the video that asked for it (issues #67, #68, #70).
Two questions per track: is the file the video's recording (file vs the video's needles), and would audio
identification have chosen the same Deezer record (the filed candidate's preview vs the video's full
fingerprint)? Report only: it writes nothing but the reference beside each request, which the worker
reuses."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import httpx

from .config import Settings
from .fingerprint import AcousticReference, FingerprintError, check, compare
from .models import RequestKind, Track
from .reference import deezer_needles, youtube_reference
from .store import Store
from .youtube import YouTubeError

# The same set `worker.REFERENCE_ERRORS` uses for the same call; it cannot be imported across the layer
# line. A sweep runs over the whole library, so one unreadable file must cost one row, not the report.
REFERENCE_ERRORS = (YouTubeError, FingerprintError, OSError, ValueError)


@dataclass(frozen=True)
class SweepRow:
    track_id: int
    artist: str
    title: str
    file_s: int | None
    video_s: int | None
    file_score: float | None        # the file against the video
    record_score: float | None      # the filed Deezer candidate's preview against the video
    note: str


async def run(store: Store, settings: Settings, http: httpx.AsyncClient, *,
              progress: Callable[[str], None] = lambda line: None) -> list[SweepRow]:
    rows: list[SweepRow] = []
    for t in store.list_tracks(limit=1_000_000):
        row = await _one(store, settings, http, t)
        rows.append(row)
        progress(f"{row.track_id:>4}  file {_fmt(row.file_score)}  record {_fmt(row.record_score)}  "
                 f"{row.artist} - {row.title}  ({row.note})")
    return rows


def _fmt(score: float | None) -> str:
    return "  - " if score is None else f"{score:.2f}"


async def _one(store: Store, settings: Settings, http: httpx.AsyncClient, t: Track) -> SweepRow:
    base: dict = {"track_id": t.id, "artist": t.artist, "title": t.title, "file_s": t.duration_s,
                  "video_s": None, "file_score": None, "record_score": None}
    if t.request_id is None:
        return SweepRow(**base, note="no request")
    try:
        req = store.get_request(t.request_id)
    except KeyError:
        return SweepRow(**base, note="request removed")
    base["video_s"] = req.query_duration_s
    if req.kind != RequestKind.YT_TRACK or not req.source_url:
        return SweepRow(**base, note="not a YouTube request")
    stored = store.get_reference(req.id)
    if stored and stored["kind"] == "youtube":
        ref = AcousticReference.from_dict(stored)
    else:
        try:
            ref = await youtube_reference(req.source_url, settings.tmp_dir / "sweep",
                                          duration_s=req.query_duration_s)
        except REFERENCE_ERRORS as e:
            return SweepRow(**base, note=f"no reference: {e or type(e).__name__}")
        store.set_reference(req.id, ref.to_dict())
    result = await check(t.path, ref, minimum=settings.lossless_fingerprint_min)
    base["file_score"] = result.score
    note = result.status if result.status != "skipped" else f"skipped: {result.reason}"
    if req.chosen_candidate_id is not None:
        cand = store.get_candidate(req.chosen_candidate_id)
        if cand.deezer_id:
            try:
                needles = await deezer_needles(cand.deezer_id, http, settings.tmp_dir / "sweep")
                base["record_score"] = round(max(compare(n, ref.full)[0] for n in needles), 3)
            except REFERENCE_ERRORS as e:
                note += f"; record: {e or type(e).__name__}"
    return SweepRow(**base, note=note)


def render(rows: list[SweepRow], minimum: float) -> str:
    scored = sorted((r for r in rows if r.file_score is not None), key=lambda r: r.file_score)
    unscored = [r for r in rows if r.file_score is None]
    lines = [f"# Library fingerprint sweep ({len(rows)} tracks, threshold {minimum:.2f})", "",
             "| track | artist - title | file s | video s | file | record | note |",
             "|---|---|---|---|---|---|---|"]
    for r in scored + unscored:
        lines.append(f"| {r.track_id} | {r.artist} - {r.title} | {r.file_s or '?'} | {r.video_s or '?'} | "
                     f"{_cell(r.file_score)} | {_cell(r.record_score)} | {r.note} |")
    below = [r for r in scored if r.file_score < minimum]
    disagree = [r for r in rows if r.record_score is not None and r.record_score < minimum]
    lines += ["", (f"File below threshold: {len(below)}. Record preview below threshold: "
                   f"{len(disagree)}. Unscored: {len(unscored)}.")]
    return "\n".join(lines) + "\n"


def _cell(score: float | None) -> str:
    return "-" if score is None else f"{score:.2f}"
