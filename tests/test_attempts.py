import json
import logging
import os
from pathlib import Path

import pytest

from flackey.attempts import AttemptRecorder, prune_raw, raw_size_bytes
from flackey.models import RequestKind
from flackey.store import Store


@pytest.fixture
def store(tmp_path: Path) -> Store:
    s = Store(tmp_path / "db.sqlite")
    s.add_request("x", RequestKind.TEXT)       # request id 1
    return s


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def test_recorder_creates_row_and_folder_and_writes_timeline(store: Store, tmp_path: Path, caplog):
    clock = Clock()
    raw = tmp_path / "raw"
    with caplog.at_level(logging.INFO, logger="flackey.attempts"):
        rec = AttemptRecorder(store, raw, 1, "soulseek", "Hallucinogen Orphic Thrench", clock=clock)
        clock.t += 1.5
        rec.event("search_completed", responses=12, files=40)
    row = store.get_attempt(rec.id)
    assert row.request_id == 1 and row.provider == "soulseek" and row.query == "Hallucinogen Orphic Thrench"
    assert row.raw_dir == str(raw / str(rec.id)) and rec.dir.is_dir()
    assert row.timeline == [{"t_ms": 1500, "event": "search_completed", "detail": {"responses": 12, "files": 40}}]
    assert f"req=1 slsk={rec.id} search_completed responses=12 files=40" in caplog.text
    assert rec.elapsed_ms() == 1500


def test_raw_files_are_numbered_and_json(store: Store, tmp_path: Path):
    rec = AttemptRecorder(store, tmp_path / "raw", 1, "soulseek", "q")
    rec.raw("search", {"state": "Completed"})
    rec.raw("responses", [{"username": "u"}])
    rec.raw("fingerprint", {"preview": [1, 2], "track": [3]})
    names = sorted(p.name for p in rec.dir.iterdir())
    assert names == ["01-search.json", "02-responses.json", "03-fingerprint.json"]
    assert json.loads((rec.dir / "02-responses.json").read_text()) == [{"username": "u"}]


def test_finish_validates_outcome_and_copies_columns(store: Store, tmp_path: Path, caplog):
    clock = Clock()
    rec = AttemptRecorder(store, tmp_path / "raw", 1, "soulseek", "q", clock=clock)
    clock.t += 3
    with caplog.at_level(logging.INFO, logger="flackey.attempts"):
        rec.finish("filed", first_byte_ms=800, fingerprint={"status": "matched", "score": 0.98},
                   spectrogram_path="/tmp/s.png", report={"summary": "3 files"})
    row = store.get_attempt(rec.id)
    assert (row.outcome, row.total_ms, row.first_byte_ms) == ("filed", 3000, 800)
    assert row.fingerprint == {"status": "matched", "score": 0.98} and row.report == {"summary": "3 files"}
    assert row.timeline[-1]["event"] == "outcome" and row.timeline[-1]["detail"] == {"outcome": "filed"}
    assert f"req=1 slsk={rec.id} outcome=filed total_ms=3000" in caplog.text
    with pytest.raises(ValueError):
        rec.finish("nonsense")


def test_raw_write_failure_does_not_break_the_attempt(store: Store, tmp_path: Path):
    rec = AttemptRecorder(store, tmp_path / "raw", 1, "soulseek", "q")
    rec.raw("search", {"x": float("nan")})           # not strict JSON but json.dumps allows it
    rec.raw("bytes", object())                         # unserialisable: logged, not raised
    assert (rec.dir / "01-search.json").exists() and not (rec.dir / "02-bytes.json").exists()


def test_raw_dir_write_failure_does_not_orphan_the_attempt(store: Store, tmp_path: Path, monkeypatch):
    """add_attempt has already created the row by the time __init__ records raw_dir; a failure on
    that second write must not raise out of __init__ and leave the row with a NULL outcome that
    nothing can ever close (task-9-report.md, Fix round 2)."""
    real_update_attempt = store.update_attempt

    def flaky_update_attempt(attempt_id, **fields):
        if "raw_dir" in fields:
            raise RuntimeError("database is locked")
        return real_update_attempt(attempt_id, **fields)

    monkeypatch.setattr(store, "update_attempt", flaky_update_attempt)
    rec = AttemptRecorder(store, tmp_path / "raw", 1, "soulseek", "q")
    assert rec.id is not None and rec.dir.is_dir()      # row and folder exist despite the failed write
    rec.finish("no_pick")
    assert store.get_attempt(rec.id).outcome == "no_pick"  # closes normally afterwards


def test_raw_name_cannot_escape_the_attempt_dir(store: Store, tmp_path: Path):
    """Not in the brief's verbatim test list: added per the controller's non-negotiable security
    note that `raw(name, obj)` must not let a crafted `name` escape `raw_dir`."""
    rec = AttemptRecorder(store, tmp_path / "raw", 1, "soulseek", "q")
    rec.raw("../../escape", {"a": 1})
    assert not (rec.dir.parent.parent / "escape.json").exists()
    assert [p.parent for p in rec.dir.iterdir()] == [rec.dir]


def test_prune_raw_removes_old_folders_only(tmp_path: Path):
    # Governed entirely by the injected `now`, not the wall clock: an arbitrary fixed instant, far
    # from real time, so a regression that hardcoded time.time() inside prune_raw would be caught.
    fixed_now = 2_000_000_000.0

    def now() -> float:
        return fixed_now

    raw = tmp_path / "raw"
    for i, age_days in enumerate((1, 31, 45), start=1):
        d = raw / str(i)
        d.mkdir(parents=True)
        (d / "01-search.json").write_text("{}")
        old = fixed_now - age_days * 86400
        os.utime(d, (old, old))
    (raw / "stray.txt").write_text("keep")
    assert raw_size_bytes(raw) == 2 * 3 + 4
    assert prune_raw(raw, keep_days=30, now=now) == 2
    assert sorted(p.name for p in raw.iterdir()) == ["1", "stray.txt"]
    assert prune_raw(tmp_path / "missing", keep_days=30, now=now) == 0 and raw_size_bytes(tmp_path / "missing") == 0


def test_queue_wait_and_first_byte_are_two_different_waits_on_the_last_pick(store: Store, tmp_path: Path):
    """Review 2026-11-16: the two were the same span. Now queue_wait is enqueue -> out of the peer's queue,
    first_byte is out of the queue -> first byte, both on the pick the outcome is about (the last one)."""
    from flackey import telemetry
    from flackey.telemetry import Telemetry

    t = Telemetry(tmp_path / "usage", version="0.6.1", url=None, channel="checkout",
                  platform_fields={"os": "macos", "os_major": "26", "arch": "arm64"})
    t.opt_in()
    telemetry.install(t)
    clock = Clock()
    try:
        rec = AttemptRecorder(store, tmp_path / "raw", 1, "soulseek", "q", clock=clock)
        rec.event("enqueue", pick=1, peer="first", file="a.flac", size=30_000_000)
        clock.t += 40
        rec.event("too_slow")
        rec.event("enqueue", pick=2, peer="second", file="b.flac", size=30_000_000)
        rec.event("transfer_state", state="Queued, Remotely")
        clock.t += 120                                   # two minutes in the second peer's queue
        rec.event("transfer_state", state="Initializing")
        clock.t += 3                                     # then three seconds to the first byte
        rec.event("first_byte", ms=123_000)
        rec.event("transfer_state", state="InProgress")
        clock.t += 30
        rec.event("completed")
        rec.finish("filed")
    finally:
        telemetry.uninstall()
    (ev,) = [e for e in t.snapshot()["pending"] if e["event"] == "lossless_attempt_finished"]
    assert ev["queue_wait"] == "1-5m" and ev["first_byte"] == "1-5s" and ev["picks_tried"] == 2
    assert "first" not in str(ev.values()) and "a.flac" not in str(ev)
