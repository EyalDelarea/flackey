"""Report one failed track (issue #165): what the store knows about the request, a line that says what went
wrong, and the log lines that are about it.

The general bug report sends the log tail and nothing per-request, which is what made "a Flackey bug" and
"Soulseek just didn't have it" look the same from the other end. Everything here is read from rows the
worker already writes; nothing new is recorded for it. The text it returns is not redacted yet --
`report.build_report` runs it through the same `Redactor` as everything else it sends."""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta, tzinfo

from ..models import (
    MISS_REASON,
    Candidate,
    CatalogTrack,
    LosslessAttempt,
    Rejection,
    Request,
    RequestState,
    source_label,
)
from ..store import Store

# The diagnosis is a subject line before it is anything else, and an error message can run to a paragraph.
DIAGNOSIS_CHARS = 160
# Either side of the request's own lifetime. A record is written a moment after the line that led to it,
# and the warning that explains a failure (Soulseek dropping, a disk filling) can land just before it.
LOG_MARGIN = timedelta(minutes=2)

# How each attempt outcome reads after "N copies found, M tried, ...". Terse on purpose: this is for the
# developer triaging at a glance, and the owner's own wording of the same outcomes is `MISS_REASON`. Every
# outcome has one -- `test_every_attempt_outcome_has_a_diagnosis` holds that line.
ATTEMPT_PHRASE = {
    "filed": "a copy was filed",
    "no_pick": "none matched closely enough",
    "queued": "stuck in the peers' queues",
    "first_byte_timeout": "the peers never started sending",
    "transfer_timeout": "the transfers stalled part-way",
    "transfer_failed": "the transfers failed",
    "verify_failed": "not really lossless",
    "fingerprint_failed": "a different recording",
    "fingerprint_unavailable": "nothing to fingerprint them against",
    "convert_failed": "the conversion failed",
    "unavailable": "not reachable",
    "interrupted": "interrupted before it finished",
}


@dataclass
class FailureRecord:
    request: Request
    rejections: list[Rejection] = field(default_factory=list)
    attempts: list[LosslessAttempt] = field(default_factory=list)
    catalog: CatalogTrack | None = None
    candidate: Candidate | None = None


def load(store: Store, request_id: int) -> FailureRecord:
    """Raises KeyError for a request that is not there any more -- removed while the dialog was open."""
    req = store.get_request(request_id)
    candidate = None
    if req.chosen_candidate_id is not None:
        try:
            candidate = store.get_candidate(req.chosen_candidate_id)
        except KeyError:
            pass
    return FailureRecord(
        request=req, rejections=store.rejections_for_request(request_id),
        attempts=store.attempts_for_request(request_id),
        catalog=store.get_catalog_track(req.catalog_track_id) if req.catalog_track_id else None,
        candidate=candidate)


def _cap(text: str, n: int = DIAGNOSIS_CHARS) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[:n - 1].rstrip() + "…"


def _count(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def _events(a: LosslessAttempt) -> list[dict]:
    return [e for e in a.timeline if isinstance(e, dict)]


def attempt_line(a: LosslessAttempt, of: int = 1) -> str:
    """One lossless attempt as a clause: "Soulseek: 4 copies found, 3 tried, the transfers failed". `of` is
    how many attempts the request made in all, said when it is more than this one."""
    name = source_label(a.provider)
    events = _events(a)
    names = [e.get("event") for e in events]
    report = a.report or {}
    if a.outcome is None:
        line = f"{name}: the attempt never finished"
    elif a.outcome in ("unavailable", "interrupted"):
        line = f"{name}: {ATTEMPT_PHRASE[a.outcome]}"
    elif "search_failed" in names:
        error = next(e.get("detail", {}).get("error") for e in events if e.get("event") == "search_failed")
        line = f"{name}: the search failed" + (f" ({error})" if error else "")
    elif a.outcome == "no_pick":
        seen = report.get("seen")
        if seen is None:
            seen = next((e.get("detail", {}).get("files") for e in events if e.get("event") == "search_completed"),
                        None)
        if seen == 0:
            line = f"{name} returned 0 results"
        elif seen is None:                       # neither the pick nor the timeline counted them
            line = f"{name}: no result matched closely enough"
        else:
            line = f"{name}: {_count(seen, 'result', 'results')}, {ATTEMPT_PHRASE['no_pick']}"
    else:
        survivors = report.get("survivors")
        head = _count(len(survivors), "copy", "copies") + " found" if survivors is not None else "copies found"
        tried = names.count("enqueue")
        if tried:
            head += f", {tried} tried"
        # A full disk is closed as transfer_failed (no peer was at fault), and only the timeline says so.
        why = "the disk was full" if "disk_full" in names else ATTEMPT_PHRASE.get(a.outcome, a.outcome)
        line = f"{name}: {head}, {why}"
    return line + (f" (last of {of} attempts)" if of > 1 else "")


def diagnose(rec: FailureRecord, redact: Callable[[str], str] | None = None) -> str:
    """The one line at the top of the report, from the rows alone, through `redact` before it is cut to
    length: the Redactor matches whole secrets, so one the cut had already halved would go out as its first
    half."""
    text = _diagnosis(rec)
    return _cap(redact(text) if redact is not None else text)


def _diagnosis(rec: FailureRecord) -> str:
    """Each failure state already says where it stopped -- not_found at identify, rejected at verify -- so
    only `error` needs `failed_stage`, the same split `presentation.ts` draws its stage chips from."""
    r = rec.request
    last = rec.attempts[-1] if rec.attempts else None
    lossless = attempt_line(last, len(rec.attempts)) if last is not None else None
    if r.state == RequestState.REJECTED:
        rj = rec.rejections[-1] if rec.rejections else None
        if rj is None:
            return f"Rejected: {r.error_message or 'no rejection was recorded'}"
        if rj.kind == "different_recording":
            why = re.sub(r"^a different recording:?\s*", "", rj.reason)
            return f"Rejected: a different recording ({why})" if why else "Rejected: a different recording"
        # The cutoff is the number the spectrogram turns on, and the one the row quotes too. A file refused
        # before the spectrum was read (a low bitrate, an odd format) carries 0, so its reason says it instead.
        if rj.cutoff_hz:
            return f"Rejected: quality check (edge at {rj.cutoff_hz / 1000:.1f} kHz)"
        return f"Rejected: quality check ({rj.reason})"
    if r.state == RequestState.NOT_FOUND:
        # Only ever set when there was nothing to search with (`_search_on_the_request`), so an attempt row on
        # such a request is from an earlier pass and its message is the one that says what happened this time.
        return f"Not found: {r.error_message or lossless or 'no reason was recorded'}"
    if r.state == RequestState.ERROR:
        # A request that gave up on Soulseek says so in `MISS_REASON`'s words (`_no_route`, and the end of
        # `_wait_for_soulseek`): when the last attempt's sentence is in the message, that attempt is the story.
        why = MISS_REASON.get(last.outcome or "") if last is not None else None
        if lossless and why and why in (r.error_message or ""):
            return lossless
        return f"Error at {r.failed_stage or 'an unrecorded stage'}: {r.error_message or 'no message was recorded'}"
    return f"Not a failure: this track is {r.state.value} now"


def _or_none(v) -> str:
    return "none" if v is None or v == "" else str(v)


def _mmss(s: int | None) -> str | None:
    return None if s is None else f"{s // 60}:{s % 60:02d}"


def _seconds(ms: int | None) -> str:
    return "none" if ms is None else f"{ms / 1000:.1f} s"


def _detail(d) -> str:
    # The same `k=v` shape `AttemptRecorder.event` logs, so a line here and its line in the log read alike.
    return "".join(f" {k}={v}" for k, v in d.items()) if isinstance(d, dict) else ""


def record_text(rec: FailureRecord) -> str:
    """The request as text, for report.txt. Every field is printed, "none" included: an empty error message
    is itself something the reader wants to know rather than wonder about."""
    r = rec.request
    asked = " – ".join(p for p in (r.query_artist, r.query_title) if p) or None
    if asked and r.query_version:
        asked += f" ({r.query_version})"
    if asked and r.query_duration_s:
        asked += f", {_mmss(r.query_duration_s)}"
    matched = None
    if rec.catalog is not None:
        c = rec.catalog
        matched = f"{c.artist} – {c.title} ({c.mix_name}), Beatport {c.id}"
        if c.tag_source:
            matched += f", tags from {c.tag_source}"
    chosen = None
    if rec.candidate is not None:
        c = rec.candidate
        chosen = (f"{c.source} {c.source_ref}: {c.artist} – {c.title} ({c.mix_name or 'Original Mix'})"
                  f"{f', {_mmss(c.duration_s)}' if c.duration_s else ''}, score {_or_none(c.score)}")
    rows = [("Link", r.source_url or r.raw_text)]
    if r.source_url and r.raw_text != r.source_url:
        rows.append(("Pasted", r.raw_text))
    rows += [("Kind", r.kind.value), ("Asked for", asked), ("Matched", matched), ("Chosen copy", chosen),
             ("State", r.state.value), ("Failed stage", r.failed_stage), ("Error", r.error_message),
             ("Flag", r.flag_reason), ("Attempts", r.attempts), ("Fetch source", r.fetch_source),
             ("Created", r.created_at), ("Updated", r.updated_at)]
    lines = [f"This track (request {r.id})"] + [f"{k}: {_or_none(v)}" for k, v in rows]

    lines += ["", f"Rejections ({len(rec.rejections)})" if rec.rejections else "Rejections: none"]
    for rj in rec.rejections:
        lines.append(f"  {rj.created_at} {rj.kind}: {rj.reason}; bitrate {_or_none(rj.bitrate_kbps)} kbps; "
                     f"cutoff {_or_none(rj.cutoff_hz)} Hz")

    lines += ["", f"Lossless attempts ({len(rec.attempts)})" if rec.attempts else "Lossless attempts: none"]
    for n, a in enumerate(rec.attempts, start=1):
        lines.append(f"  {n}. #{a.id} {a.provider}, {a.created_at}, query \"{a.query}\"")
        lines.append(f"     outcome {_or_none(a.outcome)}; first byte {_seconds(a.first_byte_ms)}; "
                     f"total {_seconds(a.total_ms)}")
        lines.append(f"     {attempt_line(a)}")
        if a.report and a.report.get("summary"):
            lines.append(f"     pick: {a.report['summary']}")
        for e in _events(a):
            t = e.get("t_ms")
            at = f"+{t / 1000:.1f} s" if isinstance(t, (int, float)) else "+?"
            lines.append(f"     {at:>9} {e.get('event')}{_detail(e.get('detail'))}")
    return "\n".join(lines) + "\n"


def record_json(rec: FailureRecord) -> str:
    """The rows whole, for the reader who needs more than report.txt shows: every survivor and rejection of
    each pick, the policy it ran under, the fingerprint. JSON rather than text because that is what they
    already are in the database."""
    def attempt(a: LosslessAttempt) -> dict:
        return {"id": a.id, "provider": a.provider, "created_at": a.created_at, "query": a.query,
                "outcome": a.outcome, "first_byte_ms": a.first_byte_ms, "total_ms": a.total_ms,
                "report": a.report, "timeline": a.timeline, "fingerprint": a.fingerprint}
    return json.dumps({"request": asdict(rec.request), "rejections": [asdict(rj) for rj in rec.rejections],
                       "attempts": [attempt(a) for a in rec.attempts]},
                      indent=2, ensure_ascii=False, default=str) + "\n"


_RECORD_START = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\.\d{3} (\w+)\s")
# Every way a log line names a request: the worker's `req#12`, the attempt recorder's `req=12`, and the
# access log's `/api/requests/12/...` for what the owner pressed on the row.
_ANY_REQUEST = re.compile(r"\breq[#=]\d+|/requests/\d+")
_LOUD = {"WARNING", "ERROR", "CRITICAL"}


def _parse_time(stamp: str) -> datetime:
    t = datetime.fromisoformat(stamp)
    return t if t.tzinfo else t.replace(tzinfo=UTC)


def window(r: Request) -> tuple[datetime, datetime]:
    return _parse_time(r.created_at) - LOG_MARGIN, _parse_time(r.updated_at) + LOG_MARGIN


def narrow_log(text: str, request_id: int, start: datetime, end: datetime, tz: tzinfo | None = None) -> str:
    """The records that are about this request: every one that names it, and every warning or error inside
    its lifetime that names no request at all -- Soulseek dropping, Beatport refusing, a crash in a provider
    -- which is the context a request's own lines usually lack. A line naming another request is left out
    however loud it is. A traceback belongs to the record above it and comes along with it.

    The log stamps local time (`logsetup.DATEFMT`) and the store stamps UTC; `tz` is the log's zone, the
    machine's own when not given, which is the one the log was written in."""
    mine = re.compile(rf"\breq[#=]{request_id}(?!\d)|/requests/{request_id}(?!\d)")
    out: list[str] = []
    keep = False
    for line in text.splitlines(keepends=True):
        m = _RECORD_START.match(line)
        if m:
            if mine.search(line):
                keep = True
            elif _ANY_REQUEST.search(line) or m.group(2) not in _LOUD:
                keep = False
            else:
                naive = datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
                at = naive.replace(tzinfo=tz) if tz is not None else naive.astimezone()
                keep = start <= at <= end
        if keep:
            out.append(line)
    return "".join(out)
