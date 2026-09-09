import itertools
import json
import subprocess
from pathlib import Path

import httpx
import pytest
import respx

from krater import fingerprint as fp
from krater.fingerprint import FPS, FingerprintError, check, compare, fingerprint
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
    monkeypatch.setattr(fp.shutil, "which", lambda _: None)
    with pytest.raises(FingerprintError):
        fingerprint(tmp_path / "x.wav")


@pytest.fixture
def fake_fpcalc(monkeypatch, pairs: dict):
    """Route `fingerprint()` to the recorded fingerprints by file name: `<id>-preview.mp3` or `<id>-track.flac`."""
    monkeypatch.setattr(fp, "fpcalc_available", lambda: True)

    def fake(path: Path, start_s: float = 0.0) -> list[int]:
        if path.name.startswith("preview-"):                       # check() names the download preview-<deezer id>.mp3
            deezer_id = int(path.stem.split("-")[1])
            return next(p["preview"] for p in pairs.values() if p["deezer_id"] == deezer_id)
        return pairs[path.stem.split("-")[0]]["track_window"]

    monkeypatch.setattr(fp, "fingerprint", fake)
    return fake


@respx.mock
async def test_check_matches_and_fails_by_threshold(tmp_path: Path, fake_fpcalc):
    respx.get("https://api.deezer.com/track/6025986").mock(return_value=httpx.Response(200, json={
        "id": 6025986, "title": "Orphic Thrench", "duration": 442, "preview": "https://cdn.test/6-preview.mp3"}))
    respx.get("https://cdn.test/6-preview.mp3").mock(return_value=httpx.Response(200, content=b"mp3"))
    async with httpx.AsyncClient() as http:
        ok = await check(tmp_path / "6-track.flac", 6025986, http, minimum=0.90, tmp_dir=tmp_path)
        assert ok.status == "matched" and ok.score >= 0.93 and ok.offset_s == pytest.approx(240 / FPS, abs=0.2)
        assert ok.preview and ok.track and "preview" not in ok.to_dict() and ok.to_dict()["score"] == ok.score
        bad = await check(tmp_path / "8-track.flac", 6025986, http, minimum=0.90, tmp_dir=tmp_path)
        assert bad.status == "failed" and bad.score < 0.80 and "below 0.90" in bad.reason
    assert not list(tmp_path.glob("preview-*"))         # the downloaded preview is removed


@respx.mock
async def test_check_is_skipped_when_deezer_or_fpcalc_is_unavailable(tmp_path: Path, fake_fpcalc, monkeypatch):
    api = respx.get("https://api.deezer.com/track/1").mock(return_value=httpx.Response(503))
    async with httpx.AsyncClient() as http:
        r = await check(tmp_path / "6-track.flac", 1, http, minimum=0.9, tmp_dir=tmp_path)
        assert r.status == "skipped" and "503" in r.reason and api.call_count == 3
        respx.get("https://api.deezer.com/track/2").mock(return_value=httpx.Response(200, json={"id": 2, "title": "T", "duration": 1, "preview": ""}))
        r = await check(tmp_path / "6-track.flac", 2, http, minimum=0.9, tmp_dir=tmp_path)
        assert r.status == "skipped" and "no preview" in r.reason
        r = await check(tmp_path / "6-track.flac", None, http, minimum=0.9, tmp_dir=tmp_path)
        assert r.status == "skipped" and "deezer id" in r.reason
        monkeypatch.setattr(fp, "fpcalc_available", lambda: False)
        r = await check(tmp_path / "6-track.flac", 6025986, http, minimum=0.9, tmp_dir=tmp_path)
        assert r.status == "skipped" and "fpcalc" in r.reason


@respx.mock
async def test_check_is_skipped_when_fpcalc_fails(tmp_path: Path, fake_fpcalc, monkeypatch):
    respx.get("https://api.deezer.com/track/6025986").mock(return_value=httpx.Response(200, json={
        "id": 6025986, "title": "T", "duration": 442, "preview": "https://cdn.test/p.mp3"}))
    respx.get("https://cdn.test/p.mp3").mock(return_value=httpx.Response(200, content=b"mp3"))

    def boom(path, start_s=0.0):
        raise FingerprintError("fpcalc timed out")

    monkeypatch.setattr(fp, "fingerprint", boom)
    async with httpx.AsyncClient() as http:
        r = await check(tmp_path / "6-track.flac", 6025986, http, minimum=0.9, tmp_dir=tmp_path)
    assert r.status == "skipped" and r.reason == "fpcalc timed out"
