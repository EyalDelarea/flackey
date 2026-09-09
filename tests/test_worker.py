import asyncio
import logging
import subprocess
from pathlib import Path

import pytest

from flackey.catalog import CatalogUnavailable
from flackey.config import Settings
from flackey.models import Candidate, CatalogTrack, Query, RequestKind, RequestState
from flackey.notify import MemoryNotifier
from flackey.source import SourceNotFound, SourceTimeout, SourceUnauthorized
from flackey.store import Store
from flackey.worker import Worker
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
def env(tmp_path: Path):
    settings = Settings(_env_file=None, telegram_api_id=1, telegram_api_hash="h",
                        library_root=tmp_path / "lib", data_dir=tmp_path / "data")
    store = Store(settings.db_path)
    notifier = MemoryNotifier()
    return settings, store, notifier


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
    settings, store, notifier = env
    ct = CatalogTrack(**{**CT.__dict__, "duration_ms": 3000})
    src = FakeSource([good_cand()])
    w = make_worker(env, src, FakeCatalog([ct]))
    await w.process(store.add_request(TEXT, RequestKind.TEXT))
    r = await w.process(store.add_request(TEXT, RequestKind.TEXT))
    assert r.state == RequestState.DUPLICATE and r.track_id is not None and len(src.fetched) == 1
    assert notifier.sent[-1][0].startswith("Already in library")


async def test_duplicate_is_rechecked_when_review_resumes(env):
    settings, store, notifier = env
    src = FakeSource([good_cand()])
    parked = store.add_request(TEXT, RequestKind.TEXT)
    await make_worker(env, src, FakeCatalog([])).process(parked)       # parks: not on Beatport
    assert store.get_request(parked).state == RequestState.AWAITING_REVIEW
    ct = CatalogTrack(**{**CT.__dict__, "duration_ms": 3000})
    w = make_worker(env, src, FakeCatalog([ct]))
    await w.process(store.add_request(TEXT, RequestKind.TEXT))         # meanwhile the same track gets filed
    await w.choose(parked, store.get_candidates(parked)[0].id)
    r = await w.process(parked)                                        # must not fetch it a second time
    assert r.state == RequestState.DUPLICATE and len(src.fetched) == 1


async def test_low_confidence_parks_then_choice_resumes(env):
    settings, store, notifier = env
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
    settings, store, notifier = env
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
    settings, store, notifier = env
    ct = CatalogTrack(**{**CT.__dict__, "duration_ms": 3000})
    src = FakeSource([good_cand()], fetch_error=SourceTimeout("slow"))
    w = make_worker(env, src, FakeCatalog([ct]))
    rid = store.add_request(TEXT, RequestKind.TEXT)
    r = await w.process(rid)
    assert r.state == RequestState.QUEUED and r.attempts == 1 and r.retry_after is not None
    assert "Retrying in 30 s" in notifier.sent[-1][0]
    assert store.next_queued() is None                      # backoff: not picked up again immediately
    r = await w.process(rid)                                # forced second attempt
    assert r.attempts == 2 and src.searches == 1 and len(store.get_candidates(rid)) == 1


async def test_not_on_beatport_parks(env):
    settings, store, notifier = env
    w = make_worker(env, FakeSource([good_cand()]), FakeCatalog([]))
    r = await w.process(store.add_request("q", RequestKind.TEXT))
    assert r.state == RequestState.AWAITING_REVIEW and "Beatport" in r.flag_reason


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


async def test_auto_accepted_edit_missing_from_beatport_parks_for_review(env):
    """Only the owner may decide to file a track without a Beatport record behind it."""
    settings, store, notifier = env
    original = CatalogTrack(**{**CT.__dict__, "duration_ms": 544000})
    w, rid = _edit_setup(env, [original])
    r = await w.process(rid)
    assert r.state == RequestState.AWAITING_REVIEW and "not on Beatport" in r.flag_reason
    assert "not on Beatport" in notifier.sent[-1][0]
    await w.choose(rid, store.get_candidates(rid)[1].id)
    r = await w.process(rid)
    track = store.get_track(r.track_id)
    assert r.state == RequestState.DONE and (track.mix_name, track.isrc) == ("Album Edit", "EDIT00001")


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
    settings, store, notifier = env
    w = make_worker(env, FakeSource([good_cand()]), FakeCatalog([]))
    rid = store.add_request("q", RequestKind.TEXT)
    await w.process(rid)
    await w.choose(rid, store.get_candidates(rid)[0].id)
    r = await w.process(rid)
    assert r.state == RequestState.DONE
    track = store.get_track(r.track_id)
    assert track.path.parent == settings.library_root / "Astral Projection" and track.isrc == "UKU932231081"


async def test_source_not_found(env):
    settings, store, notifier = env
    w = make_worker(env, FakeSource(error=SourceNotFound("source bot replied without results: Nothing")), FakeCatalog([CT]))
    r = await w.process(store.add_request("q", RequestKind.TEXT))
    assert r.state == RequestState.NOT_FOUND
    assert r.error_message == "source bot replied without results: Nothing"  # kept, so a not-found can be diagnosed


async def test_source_timeout_retries_then_errors(env):
    settings, store, notifier = env
    w = make_worker(env, FakeSource(error=SourceTimeout("slow")), FakeCatalog([CT]))
    rid = store.add_request("q", RequestKind.TEXT)
    assert (await w.process(rid)).state == RequestState.QUEUED
    assert (await w.process(rid)).state == RequestState.QUEUED
    r = await w.process(rid)
    assert r.state == RequestState.ERROR and r.attempts == 3


async def test_beatport_unavailable_requeues(env):
    settings, store, notifier = env
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


async def test_playlist_membership(env):
    settings, store, notifier = env
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
    settings, store, notifier = env
    w = make_worker(env, FakeSource([good_cand()]), FakeCatalog([]))
    rid = store.add_request("q", RequestKind.TEXT)
    await w.process(rid)
    r = await w.cancel(rid)
    assert r.state == RequestState.CANCELLED


async def test_cancel_refuses_finished_requests(env):
    settings, store, notifier = env
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
    assert status["telegram_authorized"] is False and "crate login" in notifier.sent[-1][0]
    await asyncio.wait_for(w.run_forever(poll_s=0.01), timeout=1)   # returns at once: the worker is paused


async def test_on_start_forgets_tracks_whose_files_are_gone(env):
    settings, store, notifier = env
    store.add_track(path=settings.library_root / "gone.mp3", fmt="mp3", bitrate_kbps=320, cutoff_hz=19800,
                    file_size=1, artist="A", title="T", mix_name="Original Mix", duration_s=1, isrc=None,
                    catalog_track_id=None, request_id=None)
    make_worker(env, FakeSource(), FakeCatalog()).on_start()
    assert store.list_tracks() == []


async def test_on_start_resets_inflight(env):
    settings, store, notifier = env
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
    assert store.next_queued() is None
    r = await w.retry(rid)
    assert r.retry_after is None and r.state == RequestState.QUEUED and r.attempts == 1
    assert store.next_queued().id == rid
    store.set_state(rid, RequestState.ERROR, error_message="boom")
    store.update_request(rid, attempts=3)
    r = await w.retry(rid)
    assert r.state == RequestState.QUEUED and r.attempts == 0 and r.error_message is None
    store.set_state(rid, RequestState.DONE)
    with pytest.raises(ValueError):
        await w.retry(rid)
