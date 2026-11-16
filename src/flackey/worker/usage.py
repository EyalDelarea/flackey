"""The worker's usage-stats events, built field by field from enums, counts and buckets (spec §3.2). Every
function here returns at once when stats are off, and none of them raises: a bug in telemetry must never
cost a download."""
from __future__ import annotations

import datetime as dt
import json
import logging
import time

from .. import telemetry
from ..models import TERMINAL_STATES, Request, RequestState, Verdict
from ..telemetry import buckets

TAG_SOURCES = ("deezer", "discogs", "deezer+discogs")
log = logging.getLogger(__name__)
FORMATS = ("flac", "aiff", "wav", "alac", "mp3")


def _age_s(stamp: str | None) -> float | None:
    """Seconds since a store timestamp (UTC, `YYYY-MM-DD HH:MM:SS` or ISO)."""
    if not stamp:
        return None
    try:
        t = dt.datetime.fromisoformat(stamp)
    except ValueError:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=dt.UTC)
    return (dt.datetime.now(dt.UTC) - t).total_seconds()


def on_state(worker, req: Request, state: RequestState) -> None:
    """Called by `Worker._set_state` after the row is written."""
    if not telemetry.enabled():
        return
    try:
        notes = telemetry.notes(req.id)
        if state == RequestState.IDENTIFYING and "wait_to_start" not in notes:
            notes["wait_to_start"] = buckets.dur(_age_s(req.created_at))
        if state in TERMINAL_STATES:
            request_finished(worker, req.id, state)
    except Exception:
        log.debug("usage stats: state hook failed", exc_info=True)


def request_finished(worker, request_id: int, state: RequestState) -> None:
    store = worker.store
    r = store.get_request(request_id)
    notes = telemetry.pop_notes(request_id)
    _, outcome = store.lossless_outcome(request_id)
    track = None
    if r.track_id and state == RequestState.DONE:
        try:
            track = store.get_track(r.track_id)
        except KeyError:
            track = None
    route = "none"
    filed = source_fmt = bit_depth = ""
    if track is not None:
        route = "soulseek_lossless" if track.source == "soulseek" else "deezer_mp3"
        filed = track.fmt if track.fmt in ("aiff", "wav", "flac", "mp3") else ""
        sf = (track.source_fmt or "").lower()
        source_fmt = sf if sf in FORMATS + ("wav",) else ("other" if sf else "")
        bit_depth = {16: "16", 24: "24"}.get(track.bit_depth or 0, "other" if track.bit_depth else "")
    tag_source = None
    if state == RequestState.DONE and r.catalog_track_id:
        ct = store.get_catalog_track(r.catalog_track_id)
        if ct is not None:
            tag_source = "beatport" if ct.id > 0 else (ct.tag_source if ct.tag_source in TAG_SOURCES else "none")
    ready = worker._bot_ready()
    checks_run, refused = notes.get("checks_run", 0), notes.get("checks_refused", 0)
    telemetry.emit(
        "request_finished", kind=r.kind.value, state=state.value, failed_stage=r.failed_stage or "",
        route=route, reviewed=bool(r.reviewed), passes=min(r.attempts, 20),
        lossless_outcome=outcome or "",
        lossless_no_results=bool(outcome and store.lossless_no_results(request_id, outcome)),
        catalog="blocked" if worker._catalog_blocked else ("ok" if r.catalog_track_id else "no_match"),
        bot="ready" if ready else ("off" if not worker.settings.source_enabled else "paused"),
        verify_tier=notes.get("tier", ""), filed_format=filed, source_format=source_fmt, bit_depth=bit_depth,
        checks_run=min(checks_run, 20), checks_refused=min(refused, 20),
        refused_then_passed=bool(refused and state == RequestState.DONE),
        retries_by_owner=min(notes.get("owner_retries", 0), 20), tag_source=tag_source,
        wait_to_start=notes.get("wait_to_start"), total_time=buckets.dur(_age_s(r.created_at)),
        error_class=notes.get("error_class", "") if state == RequestState.ERROR else "")


def quality_check(request_id: int, verdict: Verdict, summary: dict | None, *, route: str, rule: str,
                  run_s: float | None) -> None:
    """One verify verdict, as labels. `summary` is `verify.summary(verdict.analysis)`; its numbers are
    bucketed here and never sent as they are."""
    if not telemetry.enabled():
        return
    try:
        notes = telemetry.notes(request_id)
        notes["checks_run"] = notes.get("checks_run", 0) + 1
        tier = verdict.level
        if tier == "reject":
            notes["checks_refused"] = notes.get("checks_refused", 0) + 1
        notes["tier"] = tier
        s = summary or {}
        edge = s.get("edge") or {}
        quant = s.get("quant") or {}
        codec = quant.get("codec") or ""
        fired = detectors_fired(s)
        vendor = ((s.get("container") or {}).get("vendor") or "").lower()
        family = "none" if not vendor else ("libflac" if "libflac" in vendor or "reference libflac" in vendor
                                             else "ffmpeg" if "lavf" in vendor or "ffmpeg" in vendor else "other")
        blocksize = (s.get("container") or {}).get("max_blocksize")
        fmt = (verdict.fmt or "").lower()
        telemetry.emit(
            "quality_check", route=route, claimed_format=fmt if fmt in FORMATS else "other",
            sample_rate=buckets.sample_rate(verdict.sample_rate),
            bit_depth={16: "16", 24: "24", 32: "32"}.get(verdict.bit_depth or 0,
                                                         "other" if verdict.bit_depth else ""),
            tier=tier, edge_finding=edge.get("finding"),
            edge=buckets.edge_khz(edge.get("edge_hz")) if edge else None,
            quant_finding=quant.get("finding"), quant_codec=codec if codec in ("mp3", "aac", "opus") else None,
            detectors_fired=fired,
            strength=buckets.strength(quant.get("score", quant.get("z")), codec) if quant else None,
            coverage=buckets.coverage(quant.get("cover")) if quant else None,
            integrity_failed=bool((s.get("integrity") or {}).get("failed")) if s else None,
            encoder_family=family if fmt == "flac" else None,
            odd_blocksize=(blocksize not in (None, 4096)) if fmt == "flac" and blocksize is not None else None,
            rule=rule, shadow_tier=s.get("shadow_tier", ""),
            detector_version=detector_version(s),
            candidate_index=min(notes["checks_run"], 255), run_time=buckets.dur(run_s))
    except Exception:
        log.debug("usage stats: quality_check failed", exc_info=True)


def detectors_fired(s: dict) -> list[str]:
    """Which evidence families spoke up in a `verify.summary`: the spectral edge, and a codec's traces."""
    edge, quant = s.get("edge") or {}, s.get("quant") or {}
    codec = quant.get("codec") or ""
    fired = []
    if edge.get("finding") in ("unambiguous", "ambiguous"):
        fired.append("spectral")
    if quant.get("finding") in ("weak", "partial", "strong") and codec in ("mp3", "aac", "opus"):
        fired.append(codec)
    return fired


def reason_code(s: dict) -> str | None:
    """The refusal's reason as one of the schema's codes, read from the summary's findings the way
    `verify.decide` reads them. Never the reason sentence itself: that carries the measured numbers."""
    if not s:
        return None
    if s.get("tier") == "clear":
        return "clear"
    if "error" in s:
        return "detector_crashed"
    if (s.get("integrity") or {}).get("failed"):
        return "damaged"
    edge = (s.get("edge") or {}).get("finding")
    quant = (s.get("quant") or {}).get("finding")
    if edge == "unambiguous":
        return "edge_unambiguous"
    if quant in ("strong", "partial"):
        return f"quant_{quant}"
    if edge == "ambiguous":
        return "edge_and_quant_weak" if quant == "weak" else "edge_ambiguous"
    if quant == "weak":
        return "quant_weak"
    return "insufficient" if edge == "insufficient" else None


def detector_version(s: dict) -> str | None:
    """The detector's version as "major.minor" (verify's EVIDENCE_VERSION is the string "2.0"). Audit
    2026-11-16: this used to be sent only when it was an int, so it was never sent at all."""
    v = s.get("version") if s else None
    return str(v) if isinstance(v, (str, int, float)) and not isinstance(v, bool) and v != "" else None


def refusal_fields(rejection) -> dict:
    """What `keep_anyway` says about the refusal being overruled: its tier, reason code, the detectors
    that fired and their bucketed measures. Built from the stored `verify.summary`, so it is the same
    vocabulary as `quality_check`; a refusal for a different recording has no audio findings."""
    try:
        s = json.loads(rejection.analysis) if rejection is not None and rejection.analysis else {}
    except (TypeError, ValueError):
        s = {}
    if not isinstance(s, dict) or not s:
        return {}
    edge, quant = s.get("edge") or {}, s.get("quant") or {}
    codec = quant.get("codec") or ""
    return {"tier": s.get("tier"), "reason_code": reason_code(s), "detectors_fired": detectors_fired(s),
            "edge": buckets.edge_khz(edge.get("edge_hz")) if edge else None,
            "strength": buckets.strength(quant.get("score", quant.get("z")), codec) if quant else None,
            "coverage": buckets.coverage(quant.get("cover")) if quant else None,
            "detector_version": detector_version(s)}


def error_class(request_id: int, exc: BaseException) -> None:
    if telemetry.enabled():
        telemetry.notes(request_id)["error_class"] = type(exc).__name__


def owner_retry(request_id: int) -> None:
    if telemetry.enabled():
        n = telemetry.notes(request_id)
        n["owner_retries"] = n.get("owner_retries", 0) + 1


def review_answered(store, req: Request, answer: str, candidate_id: int | None = None) -> None:
    if not telemetry.enabled():
        return
    try:
        cands = store.get_candidates(req.id)
        rank = None
        if candidate_id is not None:
            ids = [c.id for c in cands]
            if candidate_id in ids:
                i = ids.index(candidate_id) + 1
                rank = str(i) if i <= 5 else "other"
        scores = [c.score for c in cands if getattr(c, "score", None) is not None]
        telemetry.emit("review_answered", answer=answer, picked_rank=rank, candidates=buckets.n(len(cands)),
                       top_score=buckets.score(max(scores)) if scores else None,
                       wait=buckets.dur(_age_s(req.updated_at)))
    except Exception:
        log.debug("usage stats: review_answered failed", exc_info=True)


class Timer:
    def __init__(self) -> None:
        self.t0 = time.monotonic()

    def s(self) -> float:
        return time.monotonic() - self.t0
