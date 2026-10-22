"""The request's own audio as fingerprints (issue #67). `fingerprint` itself is faked here -- what these
tests are about is which excerpt is taken, how many needles come back, and that the audio is deleted."""
import asyncio
import threading
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
    DeezerSearchError,
    deezer_needles,
    deezer_reference,
    find_deezer_record,
    identify_record,
    search_deezer,
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
    respx.get("https://cdn.test/6-preview.mp3").mock(return_value=httpx.Response(200, content=ID3_MP3))
    async with httpx.AsyncClient() as http:
        needles = await deezer_needles(6025986, http, tmp_path)
        ref = await deezer_reference(6025986, http, tmp_path)
    assert len(needles) == len(SUBFRAME_TRIMS_S) and needles[0] == list(range(300))
    assert ref.label == "deezer:6025986" and ref.full == ref.needles[0]
    assert not list(tmp_path.glob("preview-*"))


# ---- what the preview URL is allowed to hand back ---------------------------
# The URL comes out of Deezer's JSON and its body goes to ffmpeg and fpcalc. A 30 s preview is ~0.5 MB
# of MP3; anything else is refused before it is written to disk.
ID3_MP3 = b"ID3\x03\x00\x00\x00\x00\x00\x00" + b"\0" * 64
FRAME_SYNC_MP3 = b"\xff\xfb\x90\x64" + b"\0" * 64


def _preview_at(url: str) -> None:
    respx.get("https://api.deezer.com/track/5").mock(return_value=httpx.Response(200, json={"id": 5, "preview": url}))


@respx.mock
async def test_a_preview_with_no_id3_header_is_read_from_its_frame_sync(tmp_path: Path, fake_fp):
    _preview_at("https://cdn.test/5.mp3")
    respx.get("https://cdn.test/5.mp3").mock(return_value=httpx.Response(200, content=FRAME_SYNC_MP3))
    async with httpx.AsyncClient() as http:
        assert len(await deezer_needles(5, http, tmp_path)) == len(SUBFRAME_TRIMS_S)


@respx.mock
async def test_a_preview_that_is_not_mp3_is_refused_before_it_is_fingerprinted(tmp_path: Path, fake_fp):
    _preview_at("https://cdn.test/5.mp3")
    respx.get("https://cdn.test/5.mp3").mock(return_value=httpx.Response(200, content=b"<html>error</html>"))
    async with httpx.AsyncClient() as http:
        with pytest.raises(FingerprintError, match="not an MP3"):
            await deezer_needles(5, http, tmp_path)
    assert fake_fp == [] and not list(tmp_path.glob("preview-*"))


@respx.mock
async def test_a_plain_http_preview_url_is_not_fetched(tmp_path: Path, fake_fp):
    _preview_at("http://cdn.test/5.mp3")
    cdn = respx.get("http://cdn.test/5.mp3").mock(return_value=httpx.Response(200, content=ID3_MP3))
    async with httpx.AsyncClient() as http:
        with pytest.raises(FingerprintError, match="https"):
            await deezer_needles(5, http, tmp_path)
    assert cdn.call_count == 0


@respx.mock
async def test_a_preview_redirected_to_plain_http_is_refused(tmp_path: Path, fake_fp):
    _preview_at("https://cdn.test/5.mp3")
    respx.get("https://cdn.test/5.mp3").mock(
        return_value=httpx.Response(302, headers={"location": "http://cdn.test/plain.mp3"}))
    respx.get("http://cdn.test/plain.mp3").mock(return_value=httpx.Response(200, content=ID3_MP3))
    async with httpx.AsyncClient() as http:
        with pytest.raises(FingerprintError, match="https"):
            await deezer_needles(5, http, tmp_path)
    assert fake_fp == []


@respx.mock
async def test_a_preview_past_the_size_cap_is_abandoned(tmp_path: Path, fake_fp, monkeypatch):
    monkeypatch.setattr(ref_mod, "MAX_PREVIEW_BYTES", 1000)
    sent = 0

    async def endless():
        nonlocal sent
        yield ID3_MP3
        for _ in range(1000):
            sent += 1
            yield b"\0" * 100

    _preview_at("https://cdn.test/5.mp3")
    respx.get("https://cdn.test/5.mp3").mock(return_value=httpx.Response(200, content=endless()))
    async with httpx.AsyncClient() as http:
        with pytest.raises(FingerprintError, match="larger"):
            await deezer_needles(5, http, tmp_path)
    assert sent < 20 and fake_fp == [] and not list(tmp_path.glob("preview-*"))


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

    async def fake_needles(deezer_id, http, tmp_dir, *, cpu=None):
        if deezer_id == 6:
            raise FingerprintError("deezer has no preview for this track")
        return previews[deezer_id]

    monkeypatch.setattr(ref_mod, "deezer_needles", fake_needles)
    cands = [_cand(8), _cand(6), _cand(7), _cand(9), Candidate("deezer_bot", "x", "A", "B")]
    got = await identify_record(VIDEO, cands, None, tmp_path, minimum=0.9)
    assert got.chosen is cands[2] and got.score == 1.0
    assert got.tried == [(8, 0.167), (6, None), (7, 1.0)] and "deezer:7" in got.reason


async def test_identify_record_refuses_when_no_preview_matches(tmp_path: Path, monkeypatch):
    async def fake_needles(deezer_id, http, tmp_dir, *, cpu=None):
        return [NOT_THE_VIDEO]

    monkeypatch.setattr(ref_mod, "deezer_needles", fake_needles)
    cands = [_cand(n) for n in range(1, 9)]
    got = await identify_record(VIDEO, cands, None, tmp_path, minimum=0.9)
    assert got.chosen is None and len(got.tried) == IDENTIFY_MAX and "none of 5" in got.reason


async def test_identify_record_says_so_when_no_candidate_carries_a_deezer_id(tmp_path: Path, monkeypatch):
    """33 certified tracks have no Deezer id at all. Identification was never possible there, and the
    reason the owner reads must say that rather than blame the audio."""
    async def fake_needles(deezer_id, http, tmp_dir, *, cpu=None):
        raise AssertionError("nothing to fetch")

    monkeypatch.setattr(ref_mod, "deezer_needles", fake_needles)
    got = await identify_record(VIDEO, [Candidate("deezer_bot", "x", "A", "B")], None, tmp_path, minimum=0.9)
    assert got.chosen is None and got.score is None and got.tried == []
    assert got.reason == "no Deezer record to check the video against"


async def test_identify_record_separates_an_unfetchable_preview_from_a_mismatch(tmp_path: Path, monkeypatch):
    """Every preview failed to download: the audio never spoke, so the reason may not say it disagreed."""
    async def fake_needles(deezer_id, http, tmp_dir, *, cpu=None):
        raise FingerprintError("preview download http 403")

    monkeypatch.setattr(ref_mod, "deezer_needles", fake_needles)
    got = await identify_record(VIDEO, [_cand(1), _cand(2)], None, tmp_path, minimum=0.9)
    assert got.chosen is None and got.tried == [(1, None), (2, None)]
    assert got.reason == "none of the 2 Deezer previews could be fetched"


async def test_identify_record_never_raises_on_an_unexpected_failure(tmp_path: Path, monkeypatch):
    async def fake_needles(deezer_id, http, tmp_dir, *, cpu=None):
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


async def test_a_preview_lookup_refused_with_a_200_is_a_lookup_failure_not_no_preview(tmp_path: Path):
    """The track lookup gets the same 200-and-an-error-body quota answer. Read as "no preview", a Spotify
    request was told its record has no sample, and the shadow comparison counted a refused preview as a
    record that is not the video."""
    asked = []

    def quota(request):
        asked.append(str(request.url))
        return httpx.Response(200, json={"error": {"type": "Exception", "message": "Quota limit exceeded",
                                                   "code": 4}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(quota)) as http:
        with pytest.raises(FingerprintError, match="deezer lookup failed: .*Quota limit exceeded"):
            await deezer_needles(5, http, tmp_path)
        with pytest.raises(FingerprintError, match="deezer lookup failed"):
            await deezer_reference(5, http, tmp_path)
        got = await identify_record(VIDEO, [_cand(5)], http, tmp_path, minimum=0.9)
    assert got.chosen is None and got.tried == [(5, None)] and "could be fetched" in got.reason
    assert len(asked) == 3                                  # one lookup each: a refusal is not retried
    assert not list(tmp_path.glob("preview-*"))


async def test_a_deezer_search_refused_with_a_200_is_an_error_not_an_empty_answer():
    """Deezer answers its quota with HTTP 200 and an error body. Read as "no results", every reading of a
    noisy title would have found nothing, and the request counted as ambiguous."""
    def quota(request):
        return httpx.Response(200, json={"error": {"type": "Exception", "message": "Quota limit exceeded",
                                                   "code": 4}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(quota)) as http:
        with pytest.raises(DeezerSearchError, match="Quota limit exceeded"):
            await search_deezer("x y", http)
        found, why = await find_deezer_record(Candidate("query", "q", "A", "T"), http)
    assert found is None and why.startswith("deezer search failed")


async def test_deezer_search_keeps_only_previewable_records():
    def answer(request):
        return httpx.Response(200, json={"data": [
            {"id": 1, "title": "T", "artist": {"name": "A"}, "duration": 200, "preview": "https://p/1.mp3"},
            {"id": 2, "title": "T", "artist": {"name": "A"}, "duration": 200, "preview": ""}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(answer)) as http:
        assert [c.deezer_id for c in await search_deezer("A T", http)] == [1]


async def test_the_cpu_slot_is_held_for_fpcalc_only_and_a_cancel_waits_for_it(tmp_path: Path, monkeypatch):
    """The slot is for fpcalc, not for Deezer's network. And a thread cannot be stopped: a cancel arriving
    while fpcalc runs waits for it, so the slot is not handed on while fpcalc still holds the CPU, and the
    preview is not deleted from under it."""
    cpu = asyncio.Semaphore(1)
    held: list[tuple[str, bool]] = []
    started, release = threading.Event(), threading.Event()

    async def fake_url(deezer_id, http):
        held.append(("url", cpu.locked()))
        return "https://p/1.mp3"

    async def fake_download(url, http):
        held.append(("download", cpu.locked()))
        return b"ID3 preview"

    def fake_needles(path, start_s, length_s):
        held.append(("fpcalc", cpu.locked()))
        started.set()
        release.wait(5)
        held.append(("preview still there", path.exists()))
        return [[1, 2, 3]]

    monkeypatch.setattr(ref_mod, "_preview_url", fake_url)
    monkeypatch.setattr(ref_mod, "_download_preview", fake_download)
    monkeypatch.setattr(ref_mod, "_needles", fake_needles)
    task = asyncio.create_task(deezer_needles(1, None, tmp_path, cpu=cpu))
    await asyncio.to_thread(started.wait, 5)
    task.cancel()
    await asyncio.sleep(0.05)
    assert not task.done() and cpu.locked()          # still waiting on fpcalc, still holding its slot
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert held == [("url", False), ("download", False), ("fpcalc", True), ("preview still there", True)]
    assert not cpu.locked() and not list(tmp_path.glob("preview-*"))
