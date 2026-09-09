import json
import logging
import os
from pathlib import Path

import pytest

from krater.attempts import AttemptRecorder, prune_raw, raw_size_bytes
from krater.models import RequestKind
from krater.store import Store


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
    with caplog.at_level(logging.INFO, logger="krater.attempts"):
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
    with caplog.at_level(logging.INFO, logger="krater.attempts"):
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
