"""The record of one lossless attempt (spec §17): log lines, a timeline persisted on every event, raw provider
objects on disk, and the outcome row. Pruning of old raw folders lives here too."""
from __future__ import annotations

import json
import logging
import re
import shutil
import time
from collections.abc import Callable
from pathlib import Path

from . import telemetry
from .models import ATTEMPT_OUTCOMES
from .store import Store
from .telemetry import buckets

log = logging.getLogger(__name__)

_UNSAFE_NAME = re.compile(r"[^A-Za-z0-9_.-]")


class AttemptRecorder:
    def __init__(self, store: Store, raw_dir: Path, request_id: int, provider: str, query: str,
                 clock: Callable[[], float] = time.monotonic):
        self.store, self.request_id, self.provider, self.query, self.clock = store, request_id, provider, query, clock
        self.t0 = clock()
        self.timeline: list[dict] = []
        self._seq = 0
        self.id = store.add_attempt(request_id, provider, query)
        self.dir = raw_dir / str(self.id)
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            log.warning("req=%d slsk=%d cannot create raw folder %s: %s", request_id, self.id, self.dir, e)
        try:
            store.update_attempt(self.id, raw_dir=str(self.dir))
        except Exception:  # bookkeeping only; the row and self.dir already exist, don't orphan the attempt
            log.exception("req=%d slsk=%d could not record raw_dir", request_id, self.id)

    def elapsed_ms(self) -> int:
        return int((self.clock() - self.t0) * 1000)

    def event(self, name: str, **detail) -> None:
        self.timeline.append({"t_ms": self.elapsed_ms(), "event": name, "detail": detail})
        log.info("req=%d slsk=%d %s%s", self.request_id, self.id, name,
                 "".join(f" {k}={v}" for k, v in detail.items()))
        self.store.update_attempt(self.id, timeline=self.timeline)

    def raw(self, name: str, obj: object) -> None:
        """Write the provider's object exactly as received; a failure here never fails the attempt.

        `name` may originate from provider-supplied data upstream (e.g. a peer username), so it is
        sanitised and the resulting path is checked to stay inside `self.dir` before anything is
        written -- a crafted name must never escape the attempt's raw folder.
        """
        self._seq += 1
        safe_name = _UNSAFE_NAME.sub("_", name) or "raw"
        path = (self.dir / f"{self._seq:02d}-{safe_name}.json").resolve()
        if self.dir.resolve() != path.parent:
            log.warning("req=%d slsk=%d refusing to write raw file outside attempt dir: %r", self.request_id,
                        self.id, safe_name)
            return
        try:
            path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
        except (OSError, TypeError, ValueError) as e:
            log.warning("req=%d slsk=%d could not write %s: %s", self.request_id, self.id, path.name, e)

    def finish(self, outcome: str, **cols) -> None:
        if outcome not in ATTEMPT_OUTCOMES:
            raise ValueError(f"unknown attempt outcome {outcome!r}")
        total = self.elapsed_ms()
        self.timeline.append({"t_ms": total, "event": "outcome", "detail": {"outcome": outcome}})
        self.store.update_attempt(self.id, outcome=outcome, total_ms=total, timeline=self.timeline, **cols)
        log.info("req=%d slsk=%d outcome=%s total_ms=%d", self.request_id, self.id, outcome, total)
        self._usage(outcome, total)

    def _usage(self, outcome: str, total_ms: int) -> None:
        """`lossless_attempt_finished` (spec §3.2), read off the timeline as counts and buckets only: the
        timeline's `peer=` and `file=` details are never looked at."""
        if not telemetry.enabled():
            return
        try:
            def first(name: str) -> dict | None:
                return next((e for e in self.timeline if e["event"] == name), None)

            names = [e["event"] for e in self.timeline]
            search = first("search_completed")
            started = first("search_started")
            enqueue, byte = first("enqueue"), first("first_byte")
            size = enqueue["detail"].get("size") if enqueue else None
            fp = next((e for e in reversed(self.timeline) if e["event"] == "fingerprint"), None)
            fp_score = fp["detail"].get("score") if fp else None
            transfer_s = None
            done = first("completed")
            if enqueue and done:
                transfer_s = (done["t_ms"] - enqueue["t_ms"]) / 1000
            speed = (size / 1000) / transfer_s if size and transfer_s else None
            telemetry.emit(
                "lossless_attempt_finished", provider="soulseek" if self.provider == "soulseek" else None,
                outcome=outcome, results_seen=buckets.n(search["detail"].get("files")) if search else None,
                picks_tried=min(names.count("enqueue"), 20), slow_switches=min(names.count("too_slow"), 20),
                slow_fallback="slow_fallback" in names,
                search_time=buckets.dur((search["t_ms"] - (started["t_ms"] if started else 0)) / 1000)
                if search else None,
                queue_wait=buckets.dur((byte["t_ms"] - enqueue["t_ms"]) / 1000) if enqueue and byte else None,
                first_byte=buckets.dur(byte["detail"].get("ms", 0) / 1000) if byte else None,
                transfer_time=buckets.dur(total_ms / 1000), speed=buckets.speed_kbps(speed),
                file_size=buckets.size_mb(size) if isinstance(size, int) else None,
                disk_full="disk_full" in names,
                fingerprint_score=buckets.score(fp_score) if isinstance(fp_score, int | float) else None)
        except Exception:
            log.debug("usage stats: attempt event failed", exc_info=True)


def prune_raw(raw_dir: Path, keep_days: int, now: Callable[[], float] = time.time) -> int:
    """Delete attempt folders whose mtime is older than `keep_days`. Rows are untouched (spec §17.3)."""
    if not raw_dir.is_dir():
        return 0
    cutoff = now() - keep_days * 86400
    n = 0
    for d in raw_dir.iterdir():
        if d.is_dir() and d.name.isdigit() and d.stat().st_mtime < cutoff:
            shutil.rmtree(d, ignore_errors=True)
            n += 1
    if n:
        log.info("pruned %d lossless attempt folders older than %d days", n, keep_days)
    return n


def raw_size_bytes(raw_dir: Path) -> int:
    if not raw_dir.is_dir():
        return 0
    return sum(p.stat().st_size for p in raw_dir.rglob("*") if p.is_file())
