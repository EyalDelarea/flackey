import asyncio
import logging
import subprocess
from pathlib import Path

import pytest

from flackey import worker as worker_mod
from flackey.catalog import CatalogUnavailable
from flackey.config import Settings
from flackey.fingerprint import AcousticReference, FingerprintError, FingerprintResult
from flackey.match import decide
from flackey.models import (
    RETRYABLE_STATES,
    Candidate,
    CatalogTrack,
    Query,
    RequestKind,
    RequestState,
)
from flackey.notify import MemoryNotifier
from flackey.reference import Identification
from flackey.source import SourceNotFound, SourceTimeout, SourceUnauthorized
from flackey.store import Store
from flackey.worker import Worker, catalog_candidate
from flackey.youtube import YouTubeError
from tests.conftest import requires_ffmpeg

pytestmark = requires_ffmpeg

CT = CatalogTrack(id=16552105, isrc="UKU932231081", artist="Astral Projection", title="Into the Void",
                  mix_name="Original Mix", label="Sacred Technology", genre="Psy-Trance", release_date="2022-06-03",
                  bpm=142, key="A Major", duration_ms=442816, artwork_url="https://img/x.jpg")
# The fake source produces a 3-second file and `good_cand()` says duration_s=3. Tests that must reach
# filing therefore override CT with duration_ms=3000 (full duration points, so `decide` auto-accepts) and
# use the raw text "astral projection into the void" (so `best_match` finds CT: the text "q" scores 0).
# Tests that only exercise the park/error paths can keep "q" and the 442 s CT.
TEXT = "astral projection into the void"


def _mp3(path: Path, kbps: int = 320, seconds: int = 3) -> Path:
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    f"anoisesrc=color=white:seed=1:sample_rate=44100:duration={seconds}",
                    "-c:a", "libmp3lame", "-b:a", f"{kbps}k", str(path)], check=True)
    return path


class FakeCatalog:
    def __init__(self, tracks=None, fail=False):
        self.tracks, self.fail = tracks or [], fail

    async def search(self, query: Query):
        if self.fail:
            raise CatalogUnavailable("403")
        return self.tracks


class FakeSource:
    name = "deezer_bot"

    def __init__(self, cands=None, error=None, kbps=320, fetch_error=None):
        self.cands, self.error, self.kbps, self.fetch_error = cands or [], error, kbps, fetch_error
        self.fetched = []
        self.searches = 0

    async def search(self, query: Query):
        self.searches += 1
        if self.error:
            raise self.error
        return [Candidate(**c.__dict__) for c in self.cands]

    async def fetch(self, cand: Candidate, dest_dir: Path) -> Path:
        self.fetched.append(cand.source_ref)
        if self.fetch_error:
            raise self.fetch_error
        dest_dir.mkdir(parents=True, exist_ok=True)
        return _mp3(dest_dir / f"{cand.deezer_id}.mp3", self.kbps)


async def no_art(url: str):
    return None


def good_cand() -> Candidate:
    return Candidate(source="deezer_bot", source_ref="dz_track:1754956977:send", artist="Astral Projection",
                     title="Into the Void", duration_s=3, deezer_id=1754956977, isrc="UKU932231081", rank=1)


@pytest.fixture
def env(tmp_path: Path, monkeypatch):
    settings = Settings(_env_file=None, telegram_api_id=1, telegram_api_hash="h",
                        library_root=tmp_path / "lib", data_dir=tmp_path / "data")
    store = Store(settings.db_path)
    notifier = MemoryNotifier()

    # Every filed request now fetches an acoustic reference first, and `good_cand()` carries a real Deezer
    # id -- without this the suite would call api.deezer.com for it.
    async def fake_deezer(deezer_id, http, tmp_dir):
        return AcousticReference("deezer", str(deezer_id), [[1, 2, 3]], [1, 2, 3], 0.0, 30.0)

    monkeypatch.setattr(worker_mod, "deezer_reference", fake_deezer)

    # Since issue #68 every YT_TRACK request with a source_url fetches its video before choosing a record,
    # so without this the suite would run yt-dlp. A permanent failure on purpose: Task 15 makes transient
    # yt-dlp failures retry, and the fixture must stay off that ladder. Tests about the audio path
    # override it with `_video_ref`.
    async def fake_youtube(url, tmp_dir, *, duration_s=None):
        raise FingerprintError("no video audio in tests")

    monkeypatch.setattr(worker_mod, "youtube_reference", fake_youtube)

    # Since issue #68 the lossy copy is fingerprinted before it is filed, and every request in this file
    # files the lossy copy. Without this the suite would run fpcalc against the fake reference above --
    # which answers "skipped" wherever chromaprint is not installed (CI) and "failed" wherever it is,
    # because three made-up frames match no real audio. Answered the way `fingerprint.check` answers:
    # "skipped" carrying the caller's reason when there is no reference at all, "matched" otherwise.
    # Tests about a download that is the wrong recording live in `test_worker_lossless.py`, which has a
    # fake whose result they can set.
    async def fake_check(path, reference, *, minimum, missing=""):
        if reference is None:
            return FingerprintResult("skipped", None, None, missing or "no acoustic reference for this request")
        return FingerprintResult("matched", 0.98, 12.3, f"{reference.label} found at 12.3 s, score 0.98",
                                 [1, 2, 3], [4, 5, 6], reference.label)

    monkeypatch.setattr(worker_mod, "fingerprint_check", fake_check)
    return settings, store, notifier


async def _video_ref(url, tmp_dir, *, duration_s=None):
    """A stand-in for the request's own video, for the tests where audio picks the record."""
    return AcousticReference("youtube", "abc", [[1, 2, 3]], [1, 2, 3, 4], 0.0, 30.0)


def make_worker(env, source, catalog):
    settings, store, notifier = env
    return Worker(store, source, catalog, notifier, settings, artwork_fetch=no_art)


async def test_happy_path_files_tags_exports(env):
    settings, store, notifier = env
    ct = CatalogTrack(**{**CT.__dict__, "duration_ms": 3000})
    w = make_worker(env, FakeSource([good_cand()]), FakeCatalog([ct]))
    rid = store.add_request(TEXT, RequestKind.TEXT)
    r = await w.process(rid)
    assert r.state == RequestState.DONE and r.confidence == 100 and r.chosen_candidate_id is not None
    track = store.get_track(r.track_id)
    assert track.path == settings.library_root / "Astral Projection" / "Astral Projection - Into the Void.mp3"
    assert track.path.exists() and track.isrc == "UKU932231081"
    assert track.verified_at is not None and track.spectrogram_path.exists()
    assert not list(settings.tmp_dir.glob("*.mp3"))
    assert not (settings.library_root / "Playlists").exists()  # no playlist, no export
    assert notifier.sent[-1][0].startswith("Done: Astral Projection – Into the Void")
    assert "BPM" not in notifier.sent[-1][0] and "A Major" not in notifier.sent[-1][0]  # Rekordbox analyzes both


async def test_process_survives_a_request_removed_while_notifying(env, caplog):
    """A DELETE /api/requests/{id} is legal the instant a request goes terminal, so it can land in the window
    between the terminal-state commit and the awaited notifier.send() that follows it. The trailing re-fetch
    at the end of process() must tolerate the row being gone instead of letting KeyError escape run_forever()."""
    settings, store, _ = env
    caplog.set_level(logging.DEBUG, logger="flackey.worker")
    ct = CatalogTrack(**{**CT.__dict__, "duration_ms": 3000})
    rid = store.add_request(TEXT, RequestKind.TEXT)

    class VanishingNotifier:
        def __init__(self):
            self.sent = []

        async def send(self, text, buttons=None):
            self.sent.append((text, buttons))
            store.delete_request(rid)  # simulates a Remove click landing mid-notify

    w = Worker(store, FakeSource([good_cand()]), FakeCatalog([ct]), VanishingNotifier(), settings,
              artwork_fetch=no_art)
    r = await w.process(rid)
    assert r is None
    assert f"req#{rid} removed while finishing" in caplog.text
    with pytest.raises(KeyError):
        store.get_request(rid)


async def test_worker_logs_state_transitions(env, caplog):
    _, store, _ = env
    caplog.set_level(logging.DEBUG, logger="flackey.worker")
    ct = CatalogTrack(**{**CT.__dict__, "duration_ms": 3000})
    w = make_worker(env, FakeSource([good_cand()]), FakeCatalog([ct]))
    rid = store.add_request(TEXT, RequestKind.TEXT)
    await w.process(rid)
    assert f"req#{rid} queued -> identifying" in caplog.text


async def test_worker_logs_parking_for_review(env, caplog):
    """A transition that used to go straight through store.update_request (no DEBUG line) now does too."""
    _, store, _ = env
    caplog.set_level(logging.DEBUG, logger="flackey.worker")
    w = make_worker(env, FakeSource([good_cand()]), FakeCatalog([]))
    rid = store.add_request("q", RequestKind.TEXT)
    await w.process(rid)
    # match only what's after the arrow: `req` is threaded through _process unmutated, so the "from" half of
    # a later transition in the same call still shows its state as of the top-of-_process fetch (queued here)
    assert "-> awaiting_review" in caplog.text


async def test_duplicate_is_skipped(env):
    _settings, store, notifier = env
    ct = CatalogTrack(**{**CT.__dict__, "duration_ms": 3000})
    src = FakeSource([good_cand()])
    w = make_worker(env, src, FakeCatalog([ct]))
    await w.process(store.add_request(TEXT, RequestKind.TEXT))
    r = await w.process(store.add_request(TEXT, RequestKind.TEXT))
    assert r.state == RequestState.DUPLICATE and r.track_id is not None and len(src.fetched) == 1
    assert notifier.sent[-1][0].startswith("Already in library")


async def test_duplicate_is_rechecked_when_review_resumes(env):
    _settings, store, _notifier = env
    src = FakeSource([good_cand()])
    parked = store.add_request("q", RequestKind.TEXT)   # raw text nothing matches: parks below threshold
    await make_worker(env, src, FakeCatalog([])).process(parked)       # parks: confidence below threshold
    assert store.get_request(parked).state == RequestState.AWAITING_REVIEW
    ct = CatalogTrack(**{**CT.__dict__, "duration_ms": 3000})
    w = make_worker(env, src, FakeCatalog([ct]))
    await w.process(store.add_request(TEXT, RequestKind.TEXT))         # meanwhile the same track gets filed
    await w.choose(parked, store.get_candidates(parked)[0].id)
    r = await w.process(parked)                                        # must not fetch it a second time
    assert r.state == RequestState.DUPLICATE and len(src.fetched) == 1


async def test_low_confidence_parks_then_choice_resumes(env):
    _settings, store, notifier = env
    ct = CatalogTrack(**{**CT.__dict__, "duration_ms": 3000})
    other = Candidate(source="deezer_bot", source_ref="dz_track:5:send", artist="Someone", title="Else",
                      duration_s=100, deezer_id=5, rank=1)
    w = make_worker(env, FakeSource([other]), FakeCatalog([ct]))
    rid = store.add_request(TEXT, RequestKind.TEXT)
    r = await w.process(rid)
    assert r.state == RequestState.AWAITING_REVIEW and "below" in r.flag_reason
    text, buttons = notifier.sent[-1]
    assert text.splitlines()[1].startswith("1. Someone – Else") and buttons[0].label == "1"
    assert "Video: " not in text  # a text request has no reference length
    assert buttons[0].data == f"pick:{rid}:{store.get_candidates(rid)[0].id}" and buttons[-1].data == f"cancel:{rid}"
    await w.choose(rid, store.get_candidates(rid)[0].id)
    assert store.get_request(rid).state == RequestState.QUEUED
    r = await w.process(rid)
    assert r.state == RequestState.DONE
    assert r.confidence == store.get_candidates(rid)[0].score  # the picked candidate's score, not the preselected one


async def test_choose_validates_candidate_and_state(env):
    _settings, store, _notifier = env
    w = make_worker(env, FakeSource([good_cand()]), FakeCatalog([]))
    a = store.add_request("q", RequestKind.TEXT)
    b = store.add_request("q", RequestKind.TEXT)
    await w.process(a)
    await w.process(b)
    foreign = store.get_candidates(b)[0].id
    with pytest.raises(ValueError):
        await w.choose(a, foreign)
    store.set_state(a, RequestState.DONE)
    with pytest.raises(ValueError):
        await w.choose(a, store.get_candidates(a)[0].id)


async def test_fetch_failure_backs_off_without_duplicating_candidates(env):
    _settings, store, notifier = env
    ct = CatalogTrack(**{**CT.__dict__, "duration_ms": 3000})
    src = FakeSource([good_cand()], fetch_error=SourceTimeout("slow"))
    w = make_worker(env, src, FakeCatalog([ct]))
    rid = store.add_request(TEXT, RequestKind.TEXT)
    r = await w.process(rid)
    assert r.state == RequestState.QUEUED and r.attempts == 1 and r.retry_after is not None
    assert "Retrying in 30 s" in notifier.sent[-1][0]
    assert store.due_queued() == []                          # backoff: not picked up again immediately
    r = await w.process(rid)                                # forced second attempt
    assert r.attempts == 2 and src.searches == 1 and len(store.get_candidates(rid)) == 1


async def test_not_on_beatport_files_when_confident(env):
    """No Beatport record, but the candidate convinces on text: the download's own check decides."""
    _settings, store, _notifier = env
    w = make_worker(env, FakeSource([good_cand()]), FakeCatalog([]))
    rid = store.add_request(TEXT, RequestKind.TEXT)
    r = await w.process(rid)
    assert r.state == RequestState.DONE
    assert store.get_request(rid).catalog_track_id < 0       # tagged from the fallback catalogue


def remix_cand() -> Candidate:
    return Candidate(source="deezer_bot", source_ref="dz_track:7:send", artist="Astral Projection",
                     title="Into the Void (Extended Remix)", duration_s=3, deezer_id=7, isrc="REMIX0001", rank=1)


async def _park_then_choose(w, store, rid):
    r = await w.process(rid)
    assert r.state == RequestState.AWAITING_REVIEW
    await w.choose(rid, store.get_candidates(rid)[0].id)
    return await w.process(rid)


async def test_chosen_remix_is_tagged_from_its_own_catalog_record(env):
    settings, store, _ = env
    original = CatalogTrack(**{**CT.__dict__, "duration_ms": 3000})
    remix = CatalogTrack(**{**CT.__dict__, "id": 99, "isrc": "REMIX0001", "mix_name": "Extended Remix",
                            "label": "Remix Label", "duration_ms": 3000})
    w = make_worker(env, FakeSource([remix_cand()]), FakeCatalog([original, remix]))
    rid = store.add_request(TEXT, RequestKind.TEXT)
    r = await _park_then_choose(w, store, rid)
    assert r.state == RequestState.DONE and r.catalog_track_id == 99
    track = store.get_track(r.track_id)
    assert (track.mix_name, track.isrc) == ("Extended Remix", "REMIX0001")
    assert track.path.parent == settings.library_root / "Astral Projection"


async def test_chosen_remix_without_catalog_record_is_tagged_from_the_candidate(env):
    settings, store, _ = env
    original = CatalogTrack(**{**CT.__dict__, "duration_ms": 3000})
    w = make_worker(env, FakeSource([remix_cand()]), FakeCatalog([original]))
    rid = store.add_request(TEXT, RequestKind.TEXT)
    r = await _park_then_choose(w, store, rid)
    assert r.state == RequestState.DONE and r.catalog_track_id < 0
    track = store.get_track(r.track_id)
    assert (track.mix_name, track.isrc) == ("Extended Remix", "REMIX0001")
    assert track.path.parent == settings.library_root / "Astral Projection"


async def test_auto_accepted_edit_is_tagged_from_its_own_catalog_record(env):
    """The video's length pins an Album Edit (auto-accepted); the catalog was matched for the Original Mix."""
    settings, store, _ = env
    original = CatalogTrack(**{**CT.__dict__, "duration_ms": 544000})
    edit = CatalogTrack(**{**CT.__dict__, "id": 99, "isrc": "EDIT00001", "mix_name": "Album Edit",
                           "label": "Edit Label", "duration_ms": 531000})
    w, rid = _edit_setup(env, [original, edit])
    r = await w.process(rid)
    assert r.state == RequestState.DONE and r.catalog_track_id == 99
    track = store.get_track(r.track_id)
    assert (track.mix_name, track.isrc) == ("Album Edit", "EDIT00001")
    assert track.path.parent == settings.library_root / "Astral Projection"


def _edit_setup(env, catalog_tracks):
    orig_cand = Candidate(source="deezer_bot", source_ref="dz_track:1:send", artist="Astral Projection",
                          title="Into the Void", duration_s=544, deezer_id=1, rank=1)
    edit_cand = Candidate(source="deezer_bot", source_ref="dz_track:2:send", artist="Astral Projection",
                          title="Into the Void (Album Edit)", duration_s=531, deezer_id=2, isrc="EDIT00001", rank=2)
    _, store, _ = env
    w = make_worker(env, FakeSource([orig_cand, edit_cand]), FakeCatalog(catalog_tracks))
    rid = store.add_request("Into the Void", RequestKind.YT_TRACK, source_url="https://youtu.be/x",
                            query=Query(raw="Into the Void", artist="Astral Projection", title="Into the Void", duration_s=532))
    return w, rid


async def test_auto_accepted_edit_missing_from_beatport_files_on_the_fingerprint(env):
    """The pinned edit has no Beatport record of its own: it files anyway, checked against the video."""
    _settings, store, _notifier = env
    original = CatalogTrack(**{**CT.__dict__, "duration_ms": 544000})
    w, rid = _edit_setup(env, [original])
    r = await w.process(rid)
    assert r.state == RequestState.DONE
    track = store.get_track(r.track_id)
    assert (track.mix_name, track.isrc) == ("Album Edit", "EDIT00001")
    assert store.get_request(rid).catalog_track_id < 0       # tagged from the fallback catalogue


async def test_review_message_shows_the_video_length(env):
    _, store, notifier = env
    other = Candidate(source="deezer_bot", source_ref="dz_track:5:send", artist="Someone", title="Else",
                      duration_s=100, deezer_id=5, rank=1)
    w = make_worker(env, FakeSource([other]), FakeCatalog([CT]))
    rid = store.add_request("Into the Void", RequestKind.YT_TRACK, source_url="https://youtu.be/x",
                            query=Query(raw="Into the Void", artist="Astral Projection", title="Into the Void", duration_s=443))
    r = await w.process(rid)
    assert r.state == RequestState.AWAITING_REVIEW
    assert notifier.sent[-1][0].splitlines()[0] == "Review needed: Into the Void · Video: 7:23"


async def test_chosen_without_beatport_is_tagged_from_the_candidate(env):
    settings, store, _notifier = env
    w = make_worker(env, FakeSource([good_cand()]), FakeCatalog([]))
    rid = store.add_request("q", RequestKind.TEXT)
    await w.process(rid)
    await w.choose(rid, store.get_candidates(rid)[0].id)
    r = await w.process(rid)
    assert r.state == RequestState.DONE
    track = store.get_track(r.track_id)
    assert track.path.parent == settings.library_root / "Astral Projection" and track.isrc == "UKU932231081"


async def test_a_youtube_request_files_the_candidate_whose_preview_is_the_video(env, monkeypatch):
    """Issue #68: text cannot tell these two apart -- both score 100 against the Beatport record, so
    `decide` auto-files the first, 'Between The Lines'. The video's audio says the second one is the
    track. Audio wins, no review, and the confidence is the audio score, not the text score."""
    _settings, store, notifier = env
    # Neither is on Beatport (#70's case), so nothing but the audio can separate them and the file is
    # tagged from the record the audio chose.
    wrong = good_cand()
    wrong.title, wrong.deezer_id, wrong.source_ref, wrong.isrc = "Between The Lines", 1, "dz_track:1:send", None
    right = good_cand()
    right.title, right.deezer_id, right.source_ref, right.isrc = "Nothing but a Title", 2, "dz_track:2:send", None

    async def fake_identify(reference, cands, http, tmp_dir, *, minimum, limit=5):
        assert reference.label == "youtube:abc" and minimum == 0.79
        chosen = next(c for c in cands if c.deezer_id == 2)
        return Identification(chosen, 0.97, [(1, 0.41), (2, 0.97)],
                              "deezer:2 preview matches the video, score 0.97")

    monkeypatch.setattr(worker_mod, "youtube_reference", _video_ref)
    monkeypatch.setattr(worker_mod, "identify_record", fake_identify)
    w = make_worker(env, FakeSource([wrong, right]), FakeCatalog([CT]))
    # #70's real mistake: the video is titled after the record it is not. Text reads the title and agrees
    # with itself; only the audio knows better.
    rid = store.add_request("Astral Projection - Between The Lines", RequestKind.YT_TRACK,
                            source_url="https://www.youtube.com/watch?v=abc")
    r = await w.process(rid)
    assert r.state == RequestState.DONE and r.confidence == 97
    assert store.get_track(r.track_id).title == "Nothing but a Title"
    assert not [m for m in notifier.sent if m[0].startswith("Review needed")]
    # What text alone would have done, so the override is not vacuous: it auto-files the wrong record at
    # 100%. The stored scores are the ones `decide` wrote on the way through.
    by_title = {c.title: c.score for c in store.get_candidates(rid)}
    assert by_title == {"Between The Lines": 100, "Nothing but a Title": 86}
    text_only = decide(store.get_request(rid).query(), [wrong, right], None)
    assert text_only.auto and text_only.chosen.title == "Between The Lines"


async def test_a_youtube_request_with_no_matching_preview_does_not_file_the_text_winner(env, monkeypatch):
    _settings, store, notifier = env

    async def fake_identify(reference, cands, http, tmp_dir, *, minimum, limit=5):
        return Identification(None, None, [(c.deezer_id, 0.3) for c in cands],
                              "none of 1 Deezer previews is the video's recording")

    monkeypatch.setattr(worker_mod, "youtube_reference", _video_ref)
    monkeypatch.setattr(worker_mod, "identify_record", fake_identify)
    w = make_worker(env, FakeSource([good_cand()]), FakeCatalog([CT]))
    rid = store.add_request(TEXT, RequestKind.YT_TRACK, source_url="https://www.youtube.com/watch?v=abc")
    r = await w.process(rid)
    # Since issue #69 a request with no record goes to Soulseek on its own words -- but this raw text
    # carries no " - ", so nothing parsed an artist and a title out of it and there is nothing to search
    # with. It parks, still carrying the identification's reason for the owner to read, and the text
    # winner is still not filed, which is what this test is here for.
    assert r.state == RequestState.NOT_FOUND and "none of 1" in r.error_message
    assert "nothing to search for" in r.error_message
    assert r.track_id is None and notifier.sent[-1][0].startswith("Could not identify")


async def test_no_record_and_no_soulseek_errors_instead_of_filing_the_deezer_copy(env, monkeypatch):
    """The other half of issue #69's gate: the words are there to search with, but this worker has no
    lossless provider. Nothing may reach `source.fetch` -- a lossy copy of a record nothing chose is
    exactly the wrong file this epic exists to stop (spec §7)."""
    _settings, store, _notifier = env

    async def fake_identify(reference, cands, http, tmp_dir, *, minimum, limit=5):
        return Identification(None, None, [(c.deezer_id, 0.3) for c in cands],
                              "none of 1 Deezer previews is the video's recording")

    monkeypatch.setattr(worker_mod, "youtube_reference", _video_ref)
    monkeypatch.setattr(worker_mod, "identify_record", fake_identify)
    source = FakeSource([good_cand()])
    w = make_worker(env, source, FakeCatalog([CT]))
    rid = store.add_request("Astral Projection - Into the Void", RequestKind.YT_TRACK,
                            source_url="https://www.youtube.com/watch?v=abc")
    r = await w.process(rid)
    assert r.state == RequestState.ERROR and r.track_id is None and source.fetched == []
    assert "Soulseek is switched off" in r.error_message


async def test_audio_beats_a_text_score_that_would_have_auto_filed(env, monkeypatch):
    """The epic's one rule: a text score of 95 does not overrule the audio, and there is no Choose window
    on this path -- the request parks instead of asking."""
    _settings, store, notifier = env
    ct = CatalogTrack(**{**CT.__dict__, "duration_ms": 3000})

    async def fake_identify(reference, cands, http, tmp_dir, *, minimum, limit=5):
        return Identification(None, None, [(1754956977, 0.31)], "none of 1 Deezer previews is the video's recording")

    monkeypatch.setattr(worker_mod, "youtube_reference", _video_ref)
    monkeypatch.setattr(worker_mod, "identify_record", fake_identify)
    w = make_worker(env, FakeSource([good_cand()]), FakeCatalog([ct]))
    rid = store.add_request(TEXT, RequestKind.YT_TRACK, source_url="https://www.youtube.com/watch?v=abc")
    r = await w.process(rid)
    assert store.get_candidates(rid)[0].score == 100        # text would have auto-filed it
    assert r.state == RequestState.NOT_FOUND and r.chosen_candidate_id is None
    assert not [m for m in notifier.sent if m[0].startswith("Review needed")]


async def test_without_a_video_reference_text_still_decides(env, monkeypatch):
    _settings, store, _notifier = env
    ct = CatalogTrack(**{**CT.__dict__, "duration_ms": 3000})
    called = []

    async def fake_identify(*a, **kw):
        called.append(1)

    monkeypatch.setattr(worker_mod, "identify_record", fake_identify)   # env's fake_youtube already fails
    w = make_worker(env, FakeSource([good_cand()]), FakeCatalog([ct]))
    rid = store.add_request(TEXT, RequestKind.YT_TRACK, source_url="https://www.youtube.com/watch?v=abc")
    r = await w.process(rid)
    assert r.state == RequestState.DONE and called == [] and store.get_reference(rid)["kind"] == "deezer"


async def test_video_audio_failure_retries_before_the_text_path(env, monkeypatch):
    """The video's audio identifies the record, so a yt-dlp blip earns the quick ladder; after it the pass
    goes on by text (checked by the preview on download), so a removed video cannot block the request."""
    settings, store, notifier = env
    ct = CatalogTrack(**{**CT.__dict__, "duration_ms": 3000})
    calls = []

    async def flaky_youtube(url, tmp_dir, *, duration_s=None):
        calls.append(url)
        raise YouTubeError("HTTP Error 429: Too Many Requests")

    monkeypatch.setattr(worker_mod, "youtube_reference", flaky_youtube)
    w = Worker(store, FakeSource([good_cand()]), FakeCatalog([ct]), notifier, settings, artwork_fetch=no_art)
    rid = store.add_request(TEXT, RequestKind.YT_TRACK, source_url="https://www.youtube.com/watch?v=abc")
    r = await w.process(rid)
    assert r.state == RequestState.QUEUED and r.attempts == 1
    assert r.flag_reason == "video audio unavailable, will retry"
    assert "Retrying in 30 s" in notifier.sent[-1][0] and "429" in notifier.sent[-1][0]
    store.update_request(rid, retry_after=None)
    r = await w.process(rid)
    assert r.state == RequestState.QUEUED and r.attempts == 2
    store.update_request(rid, retry_after=None)
    r = await w.process(rid)
    assert r.state == RequestState.DONE and len(calls) >= 3 and store.get_reference(rid)["kind"] == "deezer"


async def test_unreadable_video_audio_goes_straight_to_the_text_path(env, monkeypatch):
    """The other half of the split: the audio arrived and could not be read, which the next pass would only
    repeat. `env`'s own fake raises exactly this, which is why every other test here chooses by text at once."""
    settings, store, notifier = env
    ct = CatalogTrack(**{**CT.__dict__, "duration_ms": 3000})
    w = Worker(store, FakeSource([good_cand()]), FakeCatalog([ct]), notifier, settings, artwork_fetch=no_art)
    rid = store.add_request(TEXT, RequestKind.YT_TRACK, source_url="https://www.youtube.com/watch?v=abc")
    r = await w.process(rid)
    assert r.state == RequestState.DONE and r.attempts == 0


async def test_source_not_found(env):
    _settings, store, _notifier = env
    w = make_worker(env, FakeSource(error=SourceNotFound("source bot replied without results: Nothing")), FakeCatalog([CT]))
    r = await w.process(store.add_request("q", RequestKind.TEXT))
    # Since issue #69 "not on Deezer" is not a verdict on its own: the request falls through to the
    # request's own words, and only lands here because "q" parses to neither an artist nor a title. The
    # bot's own sentence is still on the row, which is what a not-found is diagnosed from.
    assert r.state == RequestState.NOT_FOUND
    assert r.error_message == ("could not identify this track: the Deezer bot found nothing (source bot "
                               "replied without results: Nothing) and no Beatport match; no artist and "
                               "title could be read from the request, so there is nothing to search for")
    # Three of the four failure states say where they stopped by their own name (issue #60), so only
    # `error` carries a stage: a not-found only ever comes out of identify.
    assert r.failed_stage is None


async def test_source_timeout_retries_then_errors(env):
    _settings, store, _notifier = env
    w = make_worker(env, FakeSource(error=SourceTimeout("slow")), FakeCatalog([CT]))
    rid = store.add_request("q", RequestKind.TEXT)
    assert (await w.process(rid)).state == RequestState.QUEUED
    assert (await w.process(rid)).state == RequestState.QUEUED
    r = await w.process(rid)
    assert r.state == RequestState.ERROR and r.attempts == 3


async def test_beatport_unavailable_requeues(env):
    _settings, store, _notifier = env
    w = make_worker(env, FakeSource([good_cand()]), FakeCatalog(fail=True))
    rid = store.add_request("q", RequestKind.TEXT)
    r = await w.process(rid)
    assert r.state == RequestState.QUEUED and r.attempts == 1 and "Beatport unreachable" in r.flag_reason


async def test_verification_failure_rejects_and_deletes(env):
    settings, store, notifier = env
    ct = CatalogTrack(**{**CT.__dict__, "duration_ms": 3000})
    w = make_worker(env, FakeSource([good_cand()], kbps=128), FakeCatalog([ct]))
    rid = store.add_request(TEXT, RequestKind.TEXT)
    r = await w.process(rid)
    assert r.state == RequestState.REJECTED
    rj = store.list_rejections()[0]
    assert "bitrate" in rj.reason and Path(rj.spectrogram_path).exists()
    assert Path(rj.spectrogram_path).name == f"req{rid}-1754956977.png"
    assert not list(settings.tmp_dir.glob("*.mp3"))
    assert notifier.sent[-1][0].startswith("Rejected")


async def _rejected_as_a_different_recording(env, monkeypatch):
    """Drive a request to the one rejection the owner is allowed to overrule: the file is genuine audio
    (the spectral check passes) and the recording check says it is not the track that was asked for."""
    _settings, store, _notifier = env
    ct = CatalogTrack(**{**CT.__dict__, "duration_ms": 3000})
    w = make_worker(env, FakeSource([good_cand()]), FakeCatalog([ct]))

    async def wrong_recording(path, reference, *, minimum, missing=""):
        return FingerprintResult("failed", 0.77, None, f"best score 0.77 below {minimum:.2f}",
                                 None, [4, 5, 6], reference.label if reference else None)

    monkeypatch.setattr(worker_mod, "fingerprint_check", wrong_recording)
    rid = store.add_request(TEXT, RequestKind.TEXT)
    assert (await w.process(rid)).state == RequestState.REJECTED
    return rid, w


async def test_a_different_recording_is_kept_so_the_owner_can_hear_it(env, monkeypatch):
    """Issue #92: below the fingerprint floor is a number, and on older music the number is sometimes
    wrong. The file is moved aside instead of deleted so the row can play it -- out of `tmp_dir`, which
    the pass empties behind itself, and into `rejected_dir`, which it does not touch."""
    settings, store, notifier = env
    rid, _w = await _rejected_as_a_different_recording(env, monkeypatch)

    rj = store.get_rejection_for_request(rid)
    assert rj.kind == "different_recording" and rj.reason.startswith("a different recording")
    kept = Path(rj.audio_path)
    assert kept.exists() and kept.parent == settings.rejected_dir
    assert not list(settings.tmp_dir.glob("**/*.mp3"))      # the pass still left tmp_dir empty
    assert "keep it anyway" in notifier.sent[-1][0]


async def test_a_quality_rejection_keeps_nothing(env):
    """The other verdict is a fact about the file, not a matter of taste: an MP3 wearing a FLAC extension
    is one whatever it sounds like. Nothing is kept, and the row offers nothing to listen to."""
    settings, store, _notifier = env
    ct = CatalogTrack(**{**CT.__dict__, "duration_ms": 3000})
    w = make_worker(env, FakeSource([good_cand()], kbps=128), FakeCatalog([ct]))
    rid = store.add_request(TEXT, RequestKind.TEXT)

    assert (await w.process(rid)).state == RequestState.REJECTED

    assert store.get_rejection_for_request(rid).audio_path is None
    assert not list(settings.rejected_dir.glob("*")) if settings.rejected_dir.exists() else True


async def test_keep_it_anyway_files_the_copy_the_owner_listened_to(env, monkeypatch):
    """The whole point of keeping the bytes: what gets filed is the copy that was played, not whatever a
    fresh search would turn up an hour later. And it is filed carrying the failed match, so the library's
    own record says it was kept on the owner's say-so rather than claiming it passed."""
    settings, store, notifier = env
    rid, w = await _rejected_as_a_different_recording(env, monkeypatch)
    kept = Path(store.get_rejection_for_request(rid).audio_path)

    r = await w.accept_rejection(rid)

    assert r.state == RequestState.DONE
    track = store.get_track(r.track_id)
    assert track.path == settings.library_root / "Astral Projection" / "Astral Projection - Into the Void.mp3"
    assert track.path.exists()
    match = next(e for e in store.list_evidence(r.track_id) if e.kind == "recording_match")
    assert match.value["status"] == "failed" and match.value["score"] == 0.77
    # The kept copy has moved into the library, so nothing points at it any more and nothing is left behind.
    assert store.get_rejection_for_request(rid).audio_path is None
    assert not kept.exists()
    assert not list(settings.tmp_dir.glob("*"))
    assert notifier.sent[-1][0].startswith("Done: Astral Projection – Into the Void")


async def test_keep_it_anyway_refuses_every_other_row(env, monkeypatch):
    """One button, one meaning. It files a specific set of bytes; a row that has none -- because it failed
    the quality check, because the copy was cleaned up, because it never got that far -- must not pretend
    to have them."""
    settings, store, _notifier = env
    rid, w = await _rejected_as_a_different_recording(env, monkeypatch)

    other = store.add_request("q", RequestKind.TEXT)
    store.set_state(other, RequestState.ERROR, error_message="x")
    with pytest.raises(ValueError, match="only a rejected track"):
        await w.accept_rejection(other)

    quality = store.add_request("q2", RequestKind.TEXT)
    store.add_rejection(quality, "bitrate too low", 128, 15000, None)
    store.set_state(quality, RequestState.REJECTED)
    with pytest.raises(ValueError, match="no copy of it to keep"):
        await w.accept_rejection(quality)

    # The row still says there is a copy, and there is not: the promise is withdrawn rather than repeated.
    rj = store.get_rejection_for_request(rid)
    Path(rj.audio_path).unlink()
    with pytest.raises(ValueError, match="no longer on disk"):
        await w.accept_rejection(rid)
    assert store.get_rejection_for_request(rid).audio_path is None
    assert settings.rejected_dir.exists()


async def test_playlist_membership(env):
    settings, store, _notifier = env
    ct = CatalogTrack(**{**CT.__dict__, "duration_ms": 3000})
    w = make_worker(env, FakeSource([good_cand()]), FakeCatalog([ct]))
    pid = store.upsert_playlist("https://youtube.com/playlist?list=1", "Goa Set")
    rid = store.add_request(TEXT, RequestKind.YT_TRACK, playlist_id=pid, playlist_position=3)
    r = await w.process(rid)
    assert r.state == RequestState.DONE and store.get_playlist(pid).track_ids == [r.track_id]
    # files live under the artist whatever playlist asked for them; the M3U8 points at them from Playlists/
    assert store.get_track(r.track_id).path == settings.library_root / "Astral Projection" / "Astral Projection - Into the Void.mp3"
    m3u = settings.library_root / "Playlists" / "Goa Set.m3u8"
    assert m3u.exists() and str(store.get_track(r.track_id).path) in m3u.read_text(encoding="utf-8")


async def test_cancel(env):
    _settings, store, _notifier = env
    w = make_worker(env, FakeSource([good_cand()]), FakeCatalog([]))
    rid = store.add_request("q", RequestKind.TEXT)
    await w.process(rid)
    r = await w.cancel(rid)
    assert r.state == RequestState.CANCELLED


async def test_cancel_refuses_finished_requests(env):
    _settings, store, _notifier = env
    w = make_worker(env, FakeSource(), FakeCatalog())
    rid = store.add_request("q", RequestKind.TEXT)
    store.set_state(rid, RequestState.DONE)
    with pytest.raises(ValueError):
        await w.cancel(rid)
    assert store.get_request(rid).state == RequestState.DONE


async def test_unauthorized_session_pauses_worker_and_keeps_request(env):
    settings, store, notifier = env
    status = {"telegram_authorized": True}
    w = Worker(store, FakeSource(error=SourceUnauthorized("session revoked")), FakeCatalog([CT]), notifier, settings,
               artwork_fetch=no_art, status=status)
    rid = store.add_request("q", RequestKind.TEXT)
    r = await w.process(rid)
    assert r.state == RequestState.QUEUED and r.attempts == 0 and "login" in r.flag_reason.lower()
    assert status["telegram_authorized"] is False and "flackey login" in notifier.sent[-1][0]
    await asyncio.wait_for(w.run_forever(poll_s=0.01), timeout=1)   # returns at once: the worker is paused


async def test_on_start_forgets_tracks_whose_files_are_gone(env):
    settings, store, _notifier = env
    store.add_track(path=settings.library_root / "gone.mp3", fmt="mp3", bitrate_kbps=320, cutoff_hz=19800,
                    file_size=1, artist="A", title="T", mix_name="Original Mix", duration_s=1, isrc=None,
                    catalog_track_id=None, request_id=None)
    make_worker(env, FakeSource(), FakeCatalog()).on_start()
    assert store.list_tracks() == []


async def test_on_start_resets_inflight(env):
    _settings, store, _notifier = env
    w = make_worker(env, FakeSource(), FakeCatalog())
    rid = store.add_request("q", RequestKind.TEXT)
    store.set_state(rid, RequestState.FETCHING)
    w.on_start()
    assert store.get_request(rid).state == RequestState.QUEUED


async def test_retry_clears_backoff_and_requeues_errors(env):
    settings, store, notifier = env
    w = Worker(store, FakeSource(), FakeCatalog(), notifier, settings, artwork_fetch=no_art)
    rid = store.add_request("q", RequestKind.TEXT)
    store.update_request(rid, retry_after="2999-01-01T00:00:00+00:00", attempts=1, flag_reason="Beatport unreachable, will retry")
    assert store.due_queued() == []
    r = await w.retry(rid)
    assert r.retry_after is None and r.state == RequestState.QUEUED and r.attempts == 1
    assert [q.id for q in store.due_queued()] == [rid]
    store.set_state(rid, RequestState.ERROR, error_message="boom")
    store.update_request(rid, attempts=3)
    r = await w.retry(rid)
    assert r.state == RequestState.QUEUED and r.attempts == 0 and r.error_message is None
    store.set_state(rid, RequestState.DONE)
    with pytest.raises(ValueError):
        await w.retry(rid)
    store.set_state(rid, RequestState.NOT_FOUND, error_message="Deezer was switched off")
    r = await w.retry(rid)
    assert r.state == RequestState.QUEUED and r.error_message is None and r.lossless_retry == 1


async def test_retry_restarts_a_track_the_owner_stopped(env):
    """Issue #92: a stopped track is the owner's decision, so the owner may undo it. The state that used
    to be a dead end now comes straight back to the queue, keeping the version they had already chosen --
    changing your mind about stopping is not a reason to be asked to pick again."""
    settings, store, notifier = env
    w = Worker(store, FakeSource(), FakeCatalog(), notifier, settings, artwork_fetch=no_art)
    rid = store.add_request("q", RequestKind.TEXT)
    cid = store.add_candidates(rid, [good_cand()])[0].id
    store.update_request(rid, chosen_candidate_id=cid, attempts=2,
                         flag_reason="Beatport unreachable, will retry")
    await w.cancel(rid)
    assert store.get_request(rid).state == RequestState.CANCELLED

    r = await w.retry(rid)

    assert r.state == RequestState.QUEUED
    assert r.chosen_candidate_id == cid
    # The row said "will retry" when it was stopped and the Failed tab refused to print that promise;
    # coming back is what makes the promise true again, so the stale flag goes rather than being shown.
    assert r.flag_reason is None and r.attempts == 0
    assert [q.id for q in store.due_queued()] == [rid]


async def test_retry_still_refuses_a_rejected_track(env):
    """The one failure with no attempt left to repeat: the file was checked, failed and deleted. Widening
    retry to CANCELLED must not widen it to this -- `accept_rejection` is the way back here."""
    settings, store, notifier = env
    w = Worker(store, FakeSource(), FakeCatalog(), notifier, settings, artwork_fetch=no_art)
    rid = store.add_request("q", RequestKind.TEXT)
    store.set_state(rid, RequestState.REJECTED)
    with pytest.raises(ValueError, match="nothing to retry"):
        await w.retry(rid)


async def test_retry_takes_back_exactly_the_retryable_states(env):
    """Three surfaces draw a retry button from this one decision -- the row's Try again, the Failed tab's
    Retry all, and the playlist import list -- and the Failed tab now puts it into words on every row. Walk
    every state rather than trust the handful the tests above happen to exercise, so widening the scope is
    something a person has to come here and choose."""
    settings, store, notifier = env
    w = Worker(store, FakeSource(), FakeCatalog(), notifier, settings, artwork_fetch=no_art)
    rid = store.add_request("q", RequestKind.TEXT)
    for state in RequestState:
        # retry_after cleared each time: a queued row with a backoff is the *other* thing retry accepts
        # ("Try now"), and it would mask the answer for QUEUED itself.
        store.update_request(rid, state=state, retry_after=None)
        if state in RETRYABLE_STATES:
            assert (await w.retry(rid)).state == RequestState.QUEUED, state
        else:
            with pytest.raises(ValueError, match="nothing to retry"):
                await w.retry(rid)


# ---- where a failure stopped (issue #60) -------------------------------------------------------------
# `error` is a catch-all across five call sites spanning three stages, and by the time a row is in it the
# state it failed out of has been overwritten. `_set_state` stamps `failed_stage` from the row as it stands
# at the moment of the transition, so the Failed tab can say Search / Download / Verify instead of one
# undifferentiated lump. It reads the *store*, never the `req` snapshot the caller is holding: in every
# test below that snapshot still says `queued`, an answer that would file the commonest failures under
# Search. Each of these therefore fails outright if the derivation is moved to `req.state`.


async def test_a_failure_while_identifying_is_stamped_search(env):
    """The quick ladder spent while the row is still `identifying`: nothing was downloaded and nothing was
    checked, the track was never pinned down to begin with."""
    _settings, store, _notifier = env
    w = make_worker(env, FakeSource(error=SourceTimeout("slow")), FakeCatalog([CT]))
    rid = store.add_request("q", RequestKind.TEXT)
    for _ in range(worker_mod.MAX_ATTEMPTS - 1):
        assert (await w.process(rid)).state == RequestState.QUEUED
    r = await w.process(rid)
    assert r.state == RequestState.ERROR and r.failed_stage == "search"


async def test_a_failure_while_fetching_is_stamped_download(env):
    """The stale-snapshot case in full: `_fetch_verify_file` moves the row to `fetching` and goes on using
    the same `req` object, which still reads `queued`. The peers refusing to send a file is the single
    biggest real population on the Failed tab, and it is exactly the one a `req.state` reading would
    mislabel."""
    _settings, store, _notifier = env
    ct = CatalogTrack(**{**CT.__dict__, "duration_ms": 3000})
    w = make_worker(env, FakeSource([good_cand()], fetch_error=SourceTimeout("slow")), FakeCatalog([ct]))
    rid = store.add_request(TEXT, RequestKind.TEXT)
    for _ in range(worker_mod.MAX_ATTEMPTS - 1):
        assert (await w.process(rid)).state == RequestState.QUEUED
    r = await w.process(rid)
    assert r.state == RequestState.ERROR and r.failed_stage == "download"


async def test_a_failure_while_verifying_is_stamped_verify(env, monkeypatch):
    """A file did arrive; nothing could vouch for it being this recording. That is a different question
    from whether anything could be found, and the row now says which one it was."""
    _settings, store, _notifier = env

    async def unverifiable(path, reference, *, minimum, missing=""):
        return FingerprintResult("skipped", None, None, "video: yt-dlp timed out")

    monkeypatch.setattr(worker_mod, "fingerprint_check", unverifiable)
    ct = CatalogTrack(**{**CT.__dict__, "duration_ms": 3000})
    w = make_worker(env, FakeSource([good_cand()]), FakeCatalog([ct]))
    rid = store.add_request(TEXT, RequestKind.TEXT)
    r = await w.process(rid)
    assert r.state == RequestState.ERROR and "could not be checked acoustically" in r.error_message
    assert r.failed_stage == "verify"


async def test_the_last_resort_except_stamps_the_stage_the_row_was_in(env, monkeypatch):
    """`process`'s `except Exception` is the one terminal that knows nothing about where it came from --
    under a design where each call site passed its own stage it is the one that could only ever say
    "unknown". The row knows, so it gets a real answer: this one falls over while `filing`, the stage no
    other test here reaches."""
    _settings, store, _notifier = env

    def boom(*args, **kw):
        raise RuntimeError("the tagger fell over")

    monkeypatch.setattr(worker_mod, "write_tags", boom)
    ct = CatalogTrack(**{**CT.__dict__, "duration_ms": 3000})
    w = make_worker(env, FakeSource([good_cand()]), FakeCatalog([ct]))
    rid = store.add_request(TEXT, RequestKind.TEXT)
    r = await w.process(rid)
    assert r.state == RequestState.ERROR and r.error_message == "the tagger fell over"
    assert r.failed_stage == "verify"      # filing is the back half of verify on the ladder, not "unknown"


async def test_retry_clears_the_stage_the_last_run_stopped_at(env):
    """Cosmetic rather than load-bearing -- the browser only reads the column on a row that is in `error`,
    and this row is queued again -- but a row that keeps it is carrying a fact about a run that has been
    thrown away."""
    _settings, store, _notifier = env
    w = make_worker(env, FakeSource(), FakeCatalog())
    rid = store.add_request("q", RequestKind.TEXT)
    store.set_state(rid, RequestState.ERROR, error_message="peers refused")
    store.update_request(rid, attempts=3, failed_stage="download")
    assert (await w.retry(rid)).failed_stage is None


def test_the_catalog_stand_in_carries_beatport_data_and_no_deezer_id():
    # Built when the source cannot offer a candidate. Everything `lossless.reference_for` needs comes off
    # the Beatport record; `deezer_id` stays None, so a request with no video of its own has nothing to
    # fingerprint against and the attempt ends `fingerprint_unavailable` rather than filing (issue #68).
    ct = CatalogTrack(**{**CT.__dict__, "duration_ms": 412_000})
    cand = catalog_candidate(ct)

    assert (cand.artist, cand.title, cand.mix_name) == (ct.artist, ct.title, ct.mix_name)
    assert cand.duration_s == 412 and cand.isrc == ct.isrc
    assert cand.deezer_id is None
    assert cand.source == "beatport" and cand.source_ref == f"beatport:{ct.id}"


# ---- every track at once, and stopping one that is running -----------------------------------------

class SlowSource(FakeSource):
    """A source whose fetch parks until it is released, so a test can look at the queue mid-flight."""

    def __init__(self, cands=None):
        super().__init__(cands or [good_cand()])
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.in_flight = 0
        self.peak = 0

    async def fetch(self, cand: Candidate, dest_dir: Path) -> Path:
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)
        self.started.set()
        try:
            await self.release.wait()
            return await super().fetch(cand, dest_dir)
        finally:
            self.in_flight -= 1


async def _settle(n: int = 3) -> None:
    for _ in range(n):
        await asyncio.sleep(0)


async def test_the_whole_queue_runs_at_once_rather_than_taking_turns(env):
    """The point of the change: fourteen tracks in a playlist are fourteen downloads, not a line. Nothing
    about two requests makes an order necessary -- the peers sending them have their own upload slots."""
    _, store, _ = env
    src = SlowSource()
    w = make_worker(env, src, FakeCatalog([CatalogTrack(**{**CT.__dict__, "duration_ms": 3000})]))
    ids = [store.add_request(TEXT, RequestKind.TEXT) for _ in range(5)]

    w._start_due()
    await src.started.wait()
    await _settle()
    assert src.in_flight == 5, "every queued track should be fetching, not one with four behind it"
    assert sorted(w._tasks) == ids

    src.release.set()
    await asyncio.gather(*w._tasks.values())
    # Five pastes of the same track: they all download at once, and exactly one of them lands in the
    # library. Running in parallel must not turn the duplicate check into five copies of one file.
    states = [store.get_request(i).state for i in ids]
    assert states.count(RequestState.DONE) == 1
    assert states.count(RequestState.DUPLICATE) == 4
    assert len(store.list_tracks()) == 1


async def test_a_request_already_running_is_never_started_a_second_time(env):
    """`create_task` only schedules: on the next tick the row still says QUEUED. Claiming has to come from
    the task table, or a track would be downloaded and filed twice."""
    _, store, _ = env
    src = SlowSource()
    w = make_worker(env, src, FakeCatalog([CT]))
    rid = store.add_request(TEXT, RequestKind.TEXT)

    w._start_due()
    w._start_due()                      # same tick, before the task has run at all
    assert len(w._tasks) == 1
    await src.started.wait()
    w._start_due()                      # and again once it is in flight
    assert len(w._tasks) == 1
    src.release.set()
    await asyncio.gather(*w._tasks.values())
    assert src.fetched == ["dz_track:1754956977:send"]
    assert store.get_request(rid).state == RequestState.DONE


async def test_the_owner_can_cap_how_many_run_at_once(env):
    settings, store, _ = env
    settings.max_concurrent_requests = 2
    src = SlowSource()
    w = make_worker(env, src, FakeCatalog([CT]))
    for _ in range(5):
        store.add_request(TEXT, RequestKind.TEXT)

    w._start_due()
    await src.started.wait()
    await _settle()
    assert src.in_flight == 2
    w._start_due()
    assert len(w._tasks) == 2, "no room until one of the two finishes"
    src.release.set()
    await asyncio.gather(*w._tasks.values())


async def test_stopping_a_track_mid_download_cancels_it_and_leaves_it_stopped(env):
    """What the owner asked for: a way out of a transfer that is already running. The row goes to
    CANCELLED and stays there -- the task must not overwrite it on its way out."""
    _, store, _ = env
    src = SlowSource()
    w = make_worker(env, src, FakeCatalog([CT]))
    rid = store.add_request(TEXT, RequestKind.TEXT)

    w._start_due()
    await src.started.wait()
    assert store.get_request(rid).state == RequestState.FETCHING

    r = await w.cancel(rid)
    assert r.state == RequestState.CANCELLED
    await asyncio.gather(*w._tasks.values(), return_exceptions=True)
    assert store.get_request(rid).state == RequestState.CANCELLED
    assert src.in_flight == 0


async def test_stopping_one_track_leaves_the_others_downloading(env):
    _, store, _ = env
    src = SlowSource()
    w = make_worker(env, src, FakeCatalog([CatalogTrack(**{**CT.__dict__, "duration_ms": 3000})]))
    ids = [store.add_request(TEXT, RequestKind.TEXT) for _ in range(3)]

    w._start_due()
    await src.started.wait()
    await _settle()
    await w.cancel(ids[1])
    await _settle()
    assert src.in_flight == 2

    src.release.set()
    await asyncio.gather(*w._tasks.values(), return_exceptions=True)
    states = [store.get_request(i).state for i in ids]
    assert states[1] == RequestState.CANCELLED
    # The other two ran to a conclusion of their own; which of them filed first does not matter, only
    # that stopping the middle one did not touch either.
    assert {states[0], states[2]} == {RequestState.DONE, RequestState.DUPLICATE}


async def test_a_track_interrupted_by_shutdown_goes_back_on_the_queue(env):
    """Ctrl-C cancels the tasks too. That is not the owner stopping a track, so the row must not be left
    in a state nothing will move it out of."""
    _, store, _ = env
    src = SlowSource()
    w = make_worker(env, src, FakeCatalog([CT]))
    rid = store.add_request(TEXT, RequestKind.TEXT)

    w._start_due()
    await src.started.wait()
    await w._stop_all()
    assert store.get_request(rid).state == RequestState.QUEUED
    assert w._tasks == {}


async def test_a_file_being_verified_or_filed_cannot_be_stopped(env):
    """Those stages move the file into the library. There is no safe moment to cut them in half, and they
    are over in seconds -- so the honest answer is that it has already stopped."""
    _, store, _ = env
    w = make_worker(env, FakeSource(), FakeCatalog())
    rid = store.add_request("q", RequestKind.TEXT)
    for state in (RequestState.VERIFYING, RequestState.FILING):
        store.set_state(rid, state)
        with pytest.raises(ValueError, match="checked or filed"):
            await w.cancel(rid)
        assert store.get_request(rid).state == state


async def test_a_request_remembers_that_it_was_sent_for_review(env):
    """The progress ladder shows a Choose rung only on tracks that actually stopped for one, and nothing else
    on the row records that. `chosen_candidate_id` is set by the auto-pick too, and `choose()` clears
    `flag_reason`, so once the owner has picked there is no trace left of the pause -- the ladder would
    either forget the rung the moment they click it, or claim one on every track that never needed it."""
    _, store, _ = env
    ct = CatalogTrack(**{**CT.__dict__, "duration_ms": 3000})
    other = Candidate(source="deezer_bot", source_ref="dz_track:5:send", artist="Someone", title="Else",
                      duration_s=100, deezer_id=5, rank=1)
    w = make_worker(env, FakeSource([other]), FakeCatalog([ct]))
    rid = store.add_request(TEXT, RequestKind.TEXT)
    assert (await w.process(rid)).state == RequestState.AWAITING_REVIEW
    assert store.get_request(rid).reviewed
    await w.choose(rid, store.get_candidates(rid)[0].id)
    assert store.get_request(rid).reviewed          # survives the pick, which clears flag_reason
    assert (await w.process(rid)).state == RequestState.DONE
    assert store.get_request(rid).reviewed          # and survives all the way to filed

    auto = store.add_request(TEXT, RequestKind.TEXT)
    w2 = make_worker(env, FakeSource([good_cand()]), FakeCatalog([ct]))
    assert (await w2.process(auto)).state in (RequestState.DONE, RequestState.DUPLICATE)
    assert not store.get_request(auto).reviewed     # never paused, so it never earns the rung


async def test_run_forever_keeps_going_with_telegram_signed_out_when_the_source_is_off(env):
    """Telegram signed out only stops the worker while the bot is a source; with it switched off the
    loop must keep running so Soulseek requests still get served."""
    w = make_worker(env, FakeSource(), FakeCatalog())
    w.settings.source_enabled = False
    w.status["telegram_authorized"] = False
    ticks = []

    async def stop_after_two(force: bool = False) -> None:
        if force:            # startup()'s own call, not a loop tick
            return
        ticks.append(1)
        if len(ticks) == 2:
            w.settings.source_enabled = True   # now the loop condition is false on the next check

    w._maintenance = stop_after_two
    await asyncio.wait_for(w.run_forever(poll_s=0.01), timeout=2)
    assert len(ticks) == 2
