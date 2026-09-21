import itertools
import json
import subprocess
from pathlib import Path

import pytest

from flackey import fingerprint as fp
from flackey.fingerprint import (
    FPS,
    AcousticReference,
    FingerprintError,
    check,
    compare,
    fingerprint,
)
from tests.conftest import requires_ffmpeg, requires_fpcalc


@pytest.fixture
def pairs(fixtures: Path) -> dict:
    return json.loads((fixtures / "fingerprints.json").read_text())


def test_compare_finds_each_preview_in_its_own_track(pairs: dict):
    for tid, p in pairs.items():
        score, offset = compare(p["preview"], p["track_window"])
        assert score >= 0.93, (tid, score)
        assert abs(offset - p["expected_offset_in_window"]) <= 1, (tid, offset)
        assert abs(score - p["expected_score"]) < 0.02


def test_compare_rejects_every_cross_pair(pairs: dict):
    for a, b in itertools.permutations(pairs, 2):
        score, _ = compare(pairs[a]["preview"], pairs[b]["track_window"])
        assert score < 0.80, (a, b, score)


def test_compare_edge_cases():
    assert compare([], [1, 2, 3]) == (0.0, -1)
    assert compare([1, 2, 3], [1, 2]) == (0.0, -1)          # needle longer than hay: nothing to slide
    assert compare([7, 7], [1, 7, 7, 1]) == (1.0, 1)


@requires_ffmpeg
@requires_fpcalc
def test_real_fpcalc_locates_a_cut_at_the_right_offset(tmp_path: Path):
    whole = tmp_path / "whole.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "anoisesrc=color=pink:seed=3:duration=40:sample_rate=44100",
                    "-ac", "2", str(whole)], check=True)
    cut = tmp_path / "cut.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-ss", "20", "-t", "10", "-i", str(whole), str(cut)], check=True)
    hay = fingerprint(whole)
    assert 300 <= len(hay) <= 330                      # 40 s at 8.06 frames/s
    best = max(compare(fingerprint(cut, s), hay) for s in fp.SUBFRAME_TRIMS_S)
    assert best[0] >= 0.85 and abs(best[1] / FPS - 20) < 0.5


def test_fingerprint_raises_when_fpcalc_is_missing(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(fp, "tool_path", lambda _: None)
    with pytest.raises(FingerprintError):
        fingerprint(tmp_path / "x.wav")


@pytest.fixture
def fake_fpcalc(monkeypatch, pairs: dict):
    """Route `fingerprint()` to the recorded track window by file name: `<id>-track.flac`. The needles now
    come off the `AcousticReference` the caller is handed, so nothing downloads a preview here."""
    monkeypatch.setattr(fp, "fpcalc_available", lambda: True)

    def fake(path: Path, start_s: float = 0.0, length_s: float | None = None) -> list[int]:
        return pairs[path.stem.split("-")[0]]["track_window"]

    monkeypatch.setattr(fp, "fingerprint", fake)
    return fake


def _ref(pairs: dict, tid: str) -> AcousticReference:
    return AcousticReference("deezer", str(pairs[tid]["deezer_id"]), [pairs[tid]["preview"]], pairs[tid]["preview"],
                             0.0, 30.0)


async def test_check_matches_and_fails_by_threshold(tmp_path: Path, fake_fpcalc, pairs: dict):
    ok = await check(tmp_path / "6-track.flac", _ref(pairs, "6"), minimum=0.90)
    assert ok.status == "matched" and ok.score >= 0.93 and ok.offset_s == pytest.approx(240 / FPS, abs=0.2)
    assert ok.reference == "deezer:6025986" and ok.to_dict()["reference"] == ok.reference
    assert ok.preview and ok.track and "preview" not in ok.to_dict()
    bad = await check(tmp_path / "8-track.flac", _ref(pairs, "6"), minimum=0.90)
    assert bad.status == "failed" and bad.score < 0.80 and "below 0.90" in bad.reason


async def test_check_is_skipped_without_a_reference_or_fpcalc(tmp_path: Path, fake_fpcalc, pairs: dict, monkeypatch):
    r = await check(tmp_path / "6-track.flac", None, minimum=0.9, missing="video: yt-dlp timed out")
    assert r.status == "skipped" and r.reason == "video: yt-dlp timed out" and r.reference is None
    monkeypatch.setattr(fp, "fpcalc_available", lambda: False)
    r = await check(tmp_path / "6-track.flac", _ref(pairs, "6"), minimum=0.9)
    assert r.status == "skipped" and "fpcalc" in r.reason


async def test_check_is_skipped_when_fpcalc_fails(tmp_path: Path, fake_fpcalc, pairs: dict, monkeypatch):
    def boom(path, start_s=0.0, length_s=None):
        raise FingerprintError("fpcalc timed out")

    monkeypatch.setattr(fp, "fingerprint", boom)
    r = await check(tmp_path / "6-track.flac", _ref(pairs, "6"), minimum=0.9)
    assert r.status == "skipped" and r.reason == "fpcalc timed out"


def test_acoustic_reference_round_trips():
    ref = AcousticReference("youtube", "SEfza8xb4fU", [[1, 2], [3, 4]], [1, 2, 3, 4, 5], 161.5, 30.0)
    assert AcousticReference.from_dict(ref.to_dict()) == ref and ref.label == "youtube:SEfza8xb4fU"


@requires_ffmpeg
@requires_fpcalc
def test_fingerprint_can_take_a_bounded_excerpt(tmp_path: Path):
    whole = tmp_path / "whole.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                    "anoisesrc=color=pink:seed=3:duration=40:sample_rate=44100", "-ac", "2", str(whole)], check=True)
    part = fingerprint(whole, 10.0, 10.0)
    # 10 s at 8.06 frames/s, less the 16 frames chromaprint's classifier spends on context. The same
    # arithmetic is why a real 30 s Deezer preview is the 221 frames the recorded fixtures hold.
    assert 50 <= len(part) <= 70
    whole_fp = fingerprint(whole)
    score, offset = compare(part, whole_fp)
    assert score >= 0.85 and abs(offset / FPS - 10) < 0.5     # found where it was cut from
    assert 210 <= len(fingerprint(whole, 5.0, 30.0)) <= 230   # the reference excerpt's shape
