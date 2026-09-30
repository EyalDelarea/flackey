"""The request's own audio as fingerprints (issue #67). `fingerprint` itself is faked here -- what these
tests are about is which excerpt is taken, how many needles come back, and that the audio is deleted."""
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
import respx

from flackey import fingerprint as fp
from flackey import reference as ref_mod
from flackey.fingerprint import SUBFRAME_TRIMS_S, AcousticReference, FingerprintError
from flackey.models import Candidate
from flackey.reference import (
    EXCERPT_S,
    IDENTIFY_MAX,
    deezer_needles,
    deezer_reference,
    find_deezer_record,
    identify_record,
    youtube_reference,
)


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


# ---- identifying the record by audio (issue #68) ---------------------------
# `compare(needle, hay)`: the candidate's 30 s preview is the NEEDLE, the video's full fingerprint is the
# HAY. FULL stands in for the video; a preview "is" the video when it is a slice of it.
FULL = list(range(100, 400))
VIDEO = AcousticReference("youtube", "abc", [FULL[135:376]], FULL, 135.0, 30.0)
NOT_THE_VIDEO = [v ^ 0xFFFFFFFF for v in FULL[50:290]]      # same length, every bit flipped: 0.167


def _cand(deezer_id: int | None) -> Candidate:
    return Candidate("deezer_bot", f"dz_track:{deezer_id}:send", "A", "B", deezer_id=deezer_id)


async def test_identify_record_picks_the_first_preview_that_is_the_video(tmp_path: Path, monkeypatch):
    previews = {7: [FULL[50:290]],          # a slice of the video: the same recording
                8: [NOT_THE_VIDEO],         # every bit differs
                9: [FULL[10:250]]}          # also the video, but 7 is tried first and wins

    async def fake_needles(deezer_id, http, tmp_dir):
        if deezer_id == 6:
            raise FingerprintError("deezer has no preview for this track")
        return previews[deezer_id]

    monkeypatch.setattr(ref_mod, "deezer_needles", fake_needles)
    cands = [_cand(8), _cand(6), _cand(7), _cand(9), Candidate("deezer_bot", "x", "A", "B")]
    got = await identify_record(VIDEO, cands, None, tmp_path, minimum=0.9)
    assert got.chosen is cands[2] and got.score == 1.0
    assert got.tried == [(8, 0.167), (6, None), (7, 1.0)] and "deezer:7" in got.reason


async def test_identify_record_refuses_when_no_preview_matches(tmp_path: Path, monkeypatch):
    async def fake_needles(deezer_id, http, tmp_dir):
        return [NOT_THE_VIDEO]

    monkeypatch.setattr(ref_mod, "deezer_needles", fake_needles)
    cands = [_cand(n) for n in range(1, 9)]
    got = await identify_record(VIDEO, cands, None, tmp_path, minimum=0.9)
    assert got.chosen is None and len(got.tried) == IDENTIFY_MAX and "none of 5" in got.reason


async def test_identify_record_says_so_when_no_candidate_carries_a_deezer_id(tmp_path: Path, monkeypatch):
    """33 certified tracks have no Deezer id at all. Identification was never possible there, and the
    reason the owner reads must say that rather than blame the audio."""
    async def fake_needles(deezer_id, http, tmp_dir):
        raise AssertionError("nothing to fetch")

    monkeypatch.setattr(ref_mod, "deezer_needles", fake_needles)
    got = await identify_record(VIDEO, [Candidate("deezer_bot", "x", "A", "B")], None, tmp_path, minimum=0.9)
    assert got.chosen is None and got.score is None and got.tried == []
    assert got.reason == "no Deezer record to check the video against"


async def test_identify_record_separates_an_unfetchable_preview_from_a_mismatch(tmp_path: Path, monkeypatch):
    """Every preview failed to download: the audio never spoke, so the reason may not say it disagreed."""
    async def fake_needles(deezer_id, http, tmp_dir):
        raise FingerprintError("preview download http 403")

    monkeypatch.setattr(ref_mod, "deezer_needles", fake_needles)
    got = await identify_record(VIDEO, [_cand(1), _cand(2)], None, tmp_path, minimum=0.9)
    assert got.chosen is None and got.tried == [(1, None), (2, None)]
    assert got.reason == "none of the 2 Deezer previews could be fetched"


async def test_identify_record_never_raises_on_an_unexpected_failure(tmp_path: Path, monkeypatch):
    async def fake_needles(deezer_id, http, tmp_dir):
        raise RuntimeError("something nobody predicted")

    monkeypatch.setattr(ref_mod, "deezer_needles", fake_needles)
    got = await identify_record(VIDEO, [_cand(1)], None, tmp_path, minimum=0.9)
    assert got.chosen is None and got.tried == [(1, None)]


# ---- a Deezer record from the public API, for when the bot gave none ----------------------------------------

def _hit(id_: int, title: str, version: str = "", duration: int = 442, preview: bool = True) -> dict:
    return {"id": id_, "title": title, "title_short": title, "title_version": version, "duration": duration,
            "artist": {"name": "Astral Projection"}, "preview": f"https://cdn.test/{id_}.mp3" if preview else ""}


VOID = Candidate(source="query", source_ref="query:1", artist="Astral Projection", title="Into the Void",
                 mix_name="Original mix", duration_s=442)


@respx.mock
async def test_the_isrc_names_the_record_without_a_search():
    respx.get("https://api.deezer.com/track/isrc:UKU932231081").mock(
        return_value=httpx.Response(200, json=_hit(1754956977, "Into the Void")))
    search = respx.get("https://api.deezer.com/search")
    async with httpx.AsyncClient() as http:
        found, why = await find_deezer_record(replace(VOID, isrc="UKU932231081"), http)
    assert found == 1754956977 and "isrc" in why and not search.called


@respx.mock
async def test_an_unknown_isrc_falls_back_to_the_search():
    respx.get("https://api.deezer.com/track/isrc:XX0000000000").mock(
        return_value=httpx.Response(200, json={"error": {"type": "DataException", "code": 800}}))
    respx.get("https://api.deezer.com/search").mock(
        return_value=httpx.Response(200, json={"data": [_hit(1754956977, "Into the Void")]}))
    async with httpx.AsyncClient() as http:
        found, _ = await find_deezer_record(replace(VOID, isrc="XX0000000000"), http)
    assert found == 1754956977


@respx.mock
async def test_the_search_takes_the_requested_version_not_the_first_hit():
    route = respx.get("https://api.deezer.com/search").mock(return_value=httpx.Response(200, json={"data": [
        _hit(1, "Into the Void", "(Vini Vici Remix)", duration=410),
        _hit(2, "Into the Void", duration=442)]}))
    async with httpx.AsyncClient() as http:
        found, why = await find_deezer_record(VOID, http)
    assert found == 2 and "by search" in why
    assert route.calls.last.request.url.params["q"] == "Astral Projection Into the Void"   # no "Original mix"


@respx.mock
async def test_a_remix_alone_is_not_taken_for_the_original():
    """A remix's preview would vouch for a remix download: no record beats the wrong one."""
    respx.get("https://api.deezer.com/search").mock(return_value=httpx.Response(200, json={"data": [
        _hit(1, "Into the Void", "(Vini Vici Remix)", duration=410)]}))
    async with httpx.AsyncClient() as http:
        found, why = await find_deezer_record(VOID, http)
    assert found is None and "no deezer record is this recording" in why


@respx.mock
async def test_a_record_of_another_length_is_not_taken():
    respx.get("https://api.deezer.com/search").mock(return_value=httpx.Response(200, json={"data": [
        _hit(1, "Into the Void", duration=240)]}))
    async with httpx.AsyncClient() as http:
        found, _ = await find_deezer_record(VOID, http)
    assert found is None


@respx.mock
async def test_records_without_a_preview_are_no_use():
    respx.get("https://api.deezer.com/search").mock(return_value=httpx.Response(200, json={"data": [
        _hit(2, "Into the Void", preview=False)]}))
    async with httpx.AsyncClient() as http:
        found, why = await find_deezer_record(VOID, http)
    assert found is None and "no previewable record" in why


@respx.mock
async def test_deezer_down_is_a_reason_not_an_exception():
    respx.get("https://api.deezer.com/search").mock(side_effect=httpx.ConnectError("offline"))
    async with httpx.AsyncClient() as http:
        found, why = await find_deezer_record(VOID, http)
    assert found is None and why.startswith("deezer search failed")
