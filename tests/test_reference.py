"""The request's own audio as fingerprints (issue #67). `fingerprint` itself is faked here -- what these
tests are about is which excerpt is taken, how many needles come back, and that the audio is deleted."""
from pathlib import Path

import httpx
import pytest
import respx

from flackey import fingerprint as fp
from flackey import reference as ref_mod
from flackey.fingerprint import SUBFRAME_TRIMS_S, FingerprintError
from flackey.reference import EXCERPT_S, deezer_needles, deezer_reference, youtube_reference


@pytest.fixture
def fake_fp(monkeypatch):
    calls = []

    def fake(path: Path, start_s=0.0, length_s=None):
        calls.append((path.name, start_s, length_s))
        return [1, 2, 3] if length_s else list(range(300))

    monkeypatch.setattr(fp, "fingerprint", fake)
    monkeypatch.setattr(ref_mod, "fingerprint", fake)
    return calls


async def test_youtube_reference_cuts_the_middle_and_deletes_the_audio(tmp_path: Path, monkeypatch, fake_fp):
    async def fake_fetch(url, dest_dir):
        dest_dir.mkdir(parents=True, exist_ok=True)
        p = dest_dir / "reference.webm"
        p.write_bytes(b"x")
        return p

    monkeypatch.setattr(ref_mod, "fetch_audio", fake_fetch)
    ref = await youtube_reference("https://www.youtube.com/watch?v=SEfza8xb4fU", tmp_path, duration_s=353)
    assert ref.kind == "youtube" and ref.ref == "SEfza8xb4fU" and len(ref.needles) == len(SUBFRAME_TRIMS_S)
    assert ref.full == list(range(300))
    assert ref.excerpt_start_s == pytest.approx((353 - EXCERPT_S) / 2) and ref.excerpt_s == EXCERPT_S
    assert [c[2] for c in fake_fp] == [None] + [EXCERPT_S] * len(SUBFRAME_TRIMS_S)
    assert not (tmp_path / "reference.webm").exists()


@respx.mock
async def test_deezer_needles_download_the_preview_once_and_remove_it(tmp_path: Path, fake_fp):
    respx.get("https://api.deezer.com/track/6025986").mock(return_value=httpx.Response(200, json={
        "id": 6025986, "preview": "https://cdn.test/6-preview.mp3"}))
    respx.get("https://cdn.test/6-preview.mp3").mock(return_value=httpx.Response(200, content=b"mp3"))
    async with httpx.AsyncClient() as http:
        needles = await deezer_needles(6025986, http, tmp_path)
        ref = await deezer_reference(6025986, http, tmp_path)
    assert len(needles) == len(SUBFRAME_TRIMS_S) and needles[0] == list(range(300))
    assert ref.label == "deezer:6025986" and ref.full == ref.needles[0]
    assert not list(tmp_path.glob("preview-*"))


@respx.mock
async def test_deezer_needles_raise_when_there_is_no_preview(tmp_path: Path, fake_fp):
    respx.get("https://api.deezer.com/track/2").mock(return_value=httpx.Response(200, json={"id": 2, "preview": ""}))
    api = respx.get("https://api.deezer.com/track/1").mock(return_value=httpx.Response(503))
    async with httpx.AsyncClient() as http:
        with pytest.raises(FingerprintError, match="no preview"):
            await deezer_needles(2, http, tmp_path)
        with pytest.raises(FingerprintError, match="503"):
            await deezer_needles(1, http, tmp_path)
    assert api.call_count == 3
