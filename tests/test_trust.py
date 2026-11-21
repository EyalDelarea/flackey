from pathlib import Path

import pytest

from flackey import trust
from flackey.models import Verdict
from flackey.store import Store
from flackey.verify import VerifyError


def _track(s: Store, path: Path, **kw) -> int:
    return s.add_track(path=path, fmt="aiff", bitrate_kbps=1411, cutoff_hz=21500, file_size=1, artist="A",
                       title="T", mix_name="Original Mix", duration_s=300, isrc=None, catalog_track_id=None,
                       request_id=None, **kw)


def _by_label(r: dict) -> dict[str, dict]:
    return {c["label"]: c for c in r["checks"]}


def test_receipt_reads_the_evidence_filed_with_the_track(tmp_path: Path):
    s = Store(tmp_path / "db.sqlite")
    rid = s.add_request("Astral Projection - Mahadeva", "text")
    tid = s.add_track(path=tmp_path / "x.aiff", fmt="aiff", bitrate_kbps=1411, cutoff_hz=21500, file_size=1,
                      artist="A", title="T", mix_name="Original Mix", duration_s=300, isrc=None,
                      catalog_track_id=None, request_id=rid, source="soulseek", source_fmt="flac")
    s.add_evidence(tid, "recording_match", {"status": "matched", "score": 0.982, "reference": "Deezer preview"})
    s.add_evidence(tid, "verification", {"tier": "clear", "reason": "lossless, content to 21.5 kHz"})
    r = trust.receipt(s, s.get_track(tid))
    checks = _by_label(r)
    assert r["asked_for"] == "Astral Projection - Mahadeva" and r["lossless_source"] is True
    assert checks["Same recording"] == {"label": "Same recording", "value": "98.2% match", "ok": True,
                                        "note": "Deezer preview"}
    assert checks["Quality check"]["value"] == "passed" and checks["Quality check"]["ok"] is True
    assert checks["Spectrum"]["value"] == "content to 21.5 kHz" and checks["Spectrum"]["ok"] is None
    assert r["kept_anyway"] is False and r["recheck"] is None


def test_a_file_kept_anyway_says_so(tmp_path: Path):
    s = Store(tmp_path / "db.sqlite")
    tid = _track(s, tmp_path / "x.aiff")
    s.add_evidence(tid, "verification", {"tier": "reject", "reason": "cutoff at 16 kHz"})
    s.add_evidence(tid, "quality_check", {"status": "failed", "kept_by": "owner", "reason": "cutoff at 16 kHz"})
    r = trust.receipt(s, s.get_track(tid))
    assert r["kept_anyway"] is True and r["unverified"] is False
    assert _by_label(r)["Quality check"]["value"] == "kept anyway" and _by_label(r)["Quality check"]["ok"] is False


def test_an_old_track_with_no_evidence_says_what_is_missing_rather_than_passing(tmp_path: Path):
    s = Store(tmp_path / "db.sqlite")
    tid = _track(s, tmp_path / "x.aiff")
    checks = _by_label(trust.receipt(s, s.get_track(tid)))
    assert checks["Same recording"]["value"] == "not checked" and checks["Same recording"]["ok"] is None
    assert checks["Quality check"]["value"] == "passed (older check)"


def test_recheck_records_todays_answer_without_touching_the_file(tmp_path: Path, monkeypatch):
    s = Store(tmp_path / "db.sqlite")
    f = tmp_path / "x.aiff"
    f.write_bytes(b"audio")
    tid = _track(s, f)
    before = s.get_track(tid)
    monkeypatch.setattr(trust, "verify", lambda path, out, name, rule: Verdict(
        True, "aiff", 1411, 16000, "one sign of a lossy source", None, tier="unverified"))
    value = trust.measure(before, tmp_path / "spectrograms")
    trust.record(s, before, value)
    assert value["status"] == "checked" and value["tier"] == "unverified" and trust.flagged(value)
    assert f.read_bytes() == b"audio" and s.get_track(tid) == before   # the row and the file are as they were
    assert trust.receipt(s, before)["recheck"]["tier"] == "unverified"


def test_recheck_of_a_clear_file_is_not_flagged(tmp_path: Path, monkeypatch):
    f = tmp_path / "x.aiff"
    f.write_bytes(b"audio")
    s = Store(tmp_path / "db.sqlite")
    t = s.get_track(_track(s, f))
    monkeypatch.setattr(trust, "verify", lambda *a: Verdict(True, "aiff", 1411, 22050, "lossless", None, tier="clear"))
    assert not trust.flagged(trust.measure(t, tmp_path))


@pytest.mark.parametrize("exists, raised, status", [(False, None, "missing"), (True, VerifyError("damaged"), "error")])
def test_recheck_says_when_it_could_not_check(tmp_path: Path, monkeypatch, exists, raised, status):
    f = tmp_path / "x.aiff"
    if exists:
        f.write_bytes(b"audio")
    s = Store(tmp_path / "db.sqlite")
    t = s.get_track(_track(s, f))

    def boom(*a):
        raise raised
    monkeypatch.setattr(trust, "verify", boom)
    value = trust.measure(t, tmp_path)
    assert value["status"] == status and trust.flagged(value)


def test_record_skips_a_track_forgotten_while_it_was_checked(tmp_path: Path):
    s = Store(tmp_path / "db.sqlite")
    t = s.get_track(_track(s, tmp_path / "x.aiff"))
    s.delete_track(t.id)
    trust.record(s, t, {"status": "missing", "checked_at": "now"})
    assert s.list_evidence(t.id) == []
