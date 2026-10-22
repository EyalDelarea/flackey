import itertools
import json
import subprocess
import wave
from pathlib import Path

import numpy as np
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


# Issue #187: an extended mix of a radio edit. The edit's excerpt may hold a splice, so its two halves sit
# apart in the longer version and the whole needle finds neither.
def _spliced(pairs: dict, tid: str, filler: str, gap: int = 120) -> list[int]:
    """A hay holding `tid`'s needle cut in two, `gap` frames of another track between the halves."""
    needle = pairs[tid]["preview"]
    h = len(needle) // 2
    other = pairs[filler]["track_window"]
    return other[:100] + needle[:h] + other[200:200 + gap] + needle[h:] + other[400:500]


def test_a_needle_cut_in_two_is_found_by_its_halves_in_order(pairs: dict):
    hay = _spliced(pairs, "6", "14")
    whole, _ = compare(pairs["6"]["preview"], hay)
    assert whole < 0.9               # one half matching on its own already scores about 0.8
    score, offset = fp.compare_halves(pairs["6"]["preview"], hay)
    assert score == 1.0 and offset == 100
    # out of order, or too far apart, is no edit of this excerpt
    h = len(pairs["6"]["preview"]) // 2
    swapped = pairs["14"]["track_window"][:100] + pairs["6"]["preview"][h:] + pairs["6"]["preview"][:h]
    assert fp.compare_halves(pairs["6"]["preview"], swapped)[0] < 0.79
    far = _spliced(pairs, "6", "14", gap=0) [:100 + h] + pairs["14"]["track_window"] * 3 + pairs["6"]["preview"][h:]
    assert fp.compare_halves(pairs["6"]["preview"], far)[0] < 0.79


def test_halves_still_reject_every_cross_pair(pairs: dict):
    """The halves are shorter needles and score higher by chance, so each must clear the same minimum,
    and in order: no other track's window passes."""
    for a, b in itertools.permutations(pairs, 2):
        score, _ = fp.compare_halves(pairs[a]["preview"], pairs[b]["track_window"])
        assert score < 0.79, (a, b, score)


async def test_check_accepts_halves_only_when_asked(tmp_path: Path, monkeypatch, pairs: dict):
    monkeypatch.setattr(fp, "fpcalc_available", lambda: True)
    hay = _spliced(pairs, "6", "14")
    monkeypatch.setattr(fp, "fingerprint", lambda path, start_s=0.0, length_s=None: hay)
    plain = await check(tmp_path / "x.flac", _ref(pairs, "6"), minimum=0.9)
    assert plain.status == "failed"
    split = await check(tmp_path / "x.flac", _ref(pairs, "6"), minimum=0.9, halves=True)
    assert split.status == "matched" and split.score == 1.0 and "two halves" in split.reason
    assert split.offset_s == pytest.approx(100 / FPS, abs=0.1)


async def test_check_with_halves_still_fails_a_different_track(tmp_path: Path, fake_fpcalc, pairs: dict):
    for a, b in itertools.permutations(pairs, 2):
        r = await check(tmp_path / f"{b}-track.flac", _ref(pairs, a), minimum=0.79, halves=True)
        assert r.status == "failed", (a, b, r.score)


@requires_ffmpeg
@requires_fpcalc
def test_real_fpcalc_finds_an_edit_spliced_inside_the_excerpt(tmp_path: Path):
    """End to end on real fingerprints: a "radio edit" cut out of a longer file with a splice at its
    excerpt's centre is found in the long file by its halves, and not in a different file."""
    def music(seed: int, out: Path) -> Path:
        """90 s of chords that change every quarter second, at random: chroma that a fingerprint can tell
        apart, where noise all looks alike to it."""
        rng = np.random.default_rng(seed)
        rate, step = 22050, 0.25
        t = np.arange(int(rate * step)) / rate
        chords = [sum(np.sin(2 * np.pi * 110 * 2 ** (rng.integers(0, 36) / 12) * t) for _ in range(3))
                  for _ in range(int(90 / step))]
        pcm = (np.concatenate(chords) / 3 * 0.5 * 32767).astype("<i2")
        with wave.open(str(out), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(rate)
            w.writeframes(pcm.tobytes())
        return out

    long = music(3, tmp_path / "extended.wav")
    edit = tmp_path / "edit.wav"
    # 20-45 s and 65-90 s: the edit's middle 30 s (10-40 s of it) holds the splice at 25 s
    subprocess.run(["ffmpeg", "-v", "error", "-i", str(long), "-filter_complex",
                    "[0:a]atrim=20:45,asetpts=PTS-STARTPTS[a];[0:a]atrim=65:90,asetpts=PTS-STARTPTS[b];"
                    "[a][b]concat=n=2:v=0:a=1[o]", "-map", "[o]", str(edit)], check=True)
    needles = [fingerprint(edit, 10.0 + t, 30.0) for t in fp.SUBFRAME_TRIMS_S]
    hay = fingerprint(long)
    assert max(compare(n, hay)[0] for n in needles) < 0.79
    assert max(fp.compare_halves(n, hay)[0] for n in needles) >= 0.85
    other = fingerprint(music(11, tmp_path / "other.wav"))
    assert max(fp.compare_halves(n, other)[0] for n in needles) < 0.79
