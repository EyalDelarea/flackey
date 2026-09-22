from pathlib import Path

import pytest

from flackey.config import Settings
from flackey.inbox import NOT_A_LINK, SPOTIFY_FAIL, YT_FAIL, BadLink, Inbox
from flackey.models import RequestKind, RequestState
from flackey.spotify import SpotifyError, SpotifyPlaylist, SpotifyTrack
from flackey.store import Store
from flackey.youtube import YouTubeEntry, YouTubeError


@pytest.fixture
def inbox(tmp_path: Path):
    settings = Settings(_env_file=None, telegram_api_id=1, telegram_api_hash="h",
                        library_root=tmp_path / "lib", data_dir=tmp_path / "data")
    store = Store(settings.db_path)

    async def fake_youtube(url: str):
        if "bad" in url:
            raise YouTubeError("boom")
        if "gone" in url:
            return "Deleted video", []
        if "list=" in url:
            return "Goa Set", [
                YouTubeEntry("https://www.youtube.com/watch?v=a", "Astral Projection - Into The Void (Official)", "AP", 442),
                YouTubeEntry("https://www.youtube.com/watch?v=b", "X - Y", "X", 300),
            ]
        return "Astral Projection - Into The Void", [
            YouTubeEntry(url, "Astral Projection - Into The Void", "Astral Projection - Topic", 442)]

    async def fake_spotify(url: str):
        if "bad" in url:
            raise SpotifyError("boom")
        return SpotifyTrack("11dFghVXANMlKmJXsNCbNl", "Cut To The Feeling", "Carly Rae Jepsen", 208,
                            "https://open.spotify.com/track/11dFghVXANMlKmJXsNCbNl")

    async def fake_spotify_playlist(url: str):
        if "bad" in url:
            raise SpotifyError("boom")
        return SpotifyPlaylist("pl1", "Spotify Set", [
            SpotifyTrack("a", "Into The Void", "Astral Projection", 442, "https://open.spotify.com/track/a"),
            SpotifyTrack("b", "Y", "X", 300, "https://open.spotify.com/track/b"),
        ], "https://open.spotify.com/playlist/pl1")

    return Inbox(store, youtube=fake_youtube, spotify_track=fake_spotify,
                 spotify_playlist=fake_spotify_playlist), store


async def test_plain_text_is_a_bad_link(inbox):
    ib, store = inbox
    with pytest.raises(BadLink, match=NOT_A_LINK):
        await ib.submit("Astral Projection - Into the Void")
    with pytest.raises(BadLink, match=NOT_A_LINK):
        await ib.submit("https://open.spotify.com/album/1")
    assert store.list_requests() == []


async def test_track_link_queues_one_request(inbox):
    ib, store = inbox
    s = await ib.submit("https://youtu.be/abc")
    assert s.summary() == "Queued" and s.total == 1 and len(s.request_ids) == 1 and s.playlist_id is None
    r = store.get_request(s.request_ids[0])
    assert r.kind == RequestKind.YT_TRACK and r.state == RequestState.QUEUED
    assert r.query_artist == "Astral Projection" and r.query_title == "Into The Void" and r.query_duration_s == 442
    assert r.source_url == "https://www.youtube.com/watch?v=abc"


async def test_spotify_track_link_queues_one_request(inbox):
    ib, store = inbox
    s = await ib.submit("https://open.spotify.com/intl-de/track/11dFghVXANMlKmJXsNCbNl?si=abc")
    assert s.summary() == "Queued" and s.total == 1 and len(s.request_ids) == 1 and s.playlist_id is None
    r = store.get_request(s.request_ids[0])
    assert r.kind == RequestKind.SPOTIFY_TRACK and r.state == RequestState.QUEUED
    assert r.raw_text == "Carly Rae Jepsen - Cut To The Feeling"
    assert r.query_artist == "Carly Rae Jepsen" and r.query_title == "Cut To The Feeling"
    assert r.query_duration_s == 208
    assert r.source_url == "https://open.spotify.com/track/11dFghVXANMlKmJXsNCbNl"


async def test_playlist_link_queues_each_entry_and_skips_library_hits(inbox):
    ib, store = inbox
    store.add_track(path=Path("/x.mp3"), fmt="mp3", bitrate_kbps=320, cutoff_hz=20000, file_size=1,
                    artist="X", title="Y", mix_name="Original Mix", duration_s=300, isrc=None,
                    catalog_track_id=None, request_id=None)
    s = await ib.submit("https://www.youtube.com/playlist?list=PL1")
    assert s.name == "Goa Set" and s.total == 2 and s.already_in_library == 1 and len(s.request_ids) == 1
    assert s.summary() == 'Queued 1 of 2 from "Goa Set" (1 already in library)'
    assert store.get_playlist(s.playlist_id).track_ids == [1]
    again = await ib.submit("https://www.youtube.com/playlist?list=PL1")
    assert again.already_queued == 1 and again.request_ids == []
    assert again.summary() == 'Queued 0 of 2 from "Goa Set" (1 already in library, 1 already queued)'


async def test_playlist_link_reuses_previously_filed_source_url(inbox):
    ib, store = inbox
    rid = store.add_request("Astral Projection - Into The Void", RequestKind.YT_TRACK,
                            source_url="https://www.youtube.com/watch?v=a")
    tid = store.add_track(path=Path("/ap.mp3"), fmt="mp3", bitrate_kbps=320, cutoff_hz=20000, file_size=1,
                          artist="Astral Projection", title="Into The Void", mix_name="Original Mix",
                          duration_s=442, isrc=None, catalog_track_id=None, request_id=rid)
    store.update_request(rid, track_id=tid, state=RequestState.DONE)

    s = await ib.submit("https://www.youtube.com/playlist?list=PL1")

    assert s.already_in_library == 1
    assert store.get_playlist(s.playlist_id).track_ids == [tid]


async def test_reimport_retries_a_failed_playlist_request_instead_of_duplicating_it(inbox):
    ib, store = inbox
    first = await ib.submit("https://www.youtube.com/playlist?list=PL1")
    failed_id = first.request_ids[0]
    store.set_state(failed_id, RequestState.NOT_FOUND, error_message="Deezer bot was off")

    again = await ib.submit("https://www.youtube.com/playlist?list=PL1")

    assert failed_id in again.request_ids
    assert store.get_request(failed_id).state is RequestState.QUEUED
    assert len([r for r in store.list_recent_requests() if r.playlist_id == first.playlist_id]) == 2
    assert again.already_queued == 1


async def test_reimport_clears_the_stage_the_failed_run_stopped_at(inbox):
    """The re-imported row starts again from the top, so the stage its last run stopped at (issue #60) is
    no longer a fact about it -- cleared beside the attempts, the backoff and the error message."""
    ib, store = inbox
    first = await ib.submit("https://www.youtube.com/playlist?list=PL1")
    failed_id = first.request_ids[0]
    store.set_state(failed_id, RequestState.ERROR, error_message="the peers all refused")
    store.update_request(failed_id, failed_stage="download")

    await ib.submit("https://www.youtube.com/playlist?list=PL1")

    assert store.get_request(failed_id).failed_stage is None


async def test_spotify_playlist_link_queues_each_entry_and_skips_library_hits(inbox):
    ib, store = inbox
    store.add_track(path=Path("/x.mp3"), fmt="mp3", bitrate_kbps=320, cutoff_hz=20000, file_size=1,
                    artist="X", title="Y", mix_name="Original Mix", duration_s=300, isrc=None,
                    catalog_track_id=None, request_id=None)
    s = await ib.submit("https://open.spotify.com/playlist/pl1")
    assert s.name == "Spotify Set" and s.total == 2 and s.already_in_library == 1 and len(s.request_ids) == 1
    assert s.summary() == 'Queued 1 of 2 from "Spotify Set" (1 already in library)'
    assert store.get_playlist(s.playlist_id).track_ids == [1]
    r = store.get_request(s.request_ids[0])
    assert r.kind == RequestKind.SPOTIFY_TRACK
    assert r.raw_text == "Astral Projection - Into The Void"
    assert r.source_url == "https://open.spotify.com/track/a"

    again = await ib.submit("https://open.spotify.com/playlist/pl1")
    assert again.already_queued == 1 and again.request_ids == []
    assert again.summary() == 'Queued 0 of 2 from "Spotify Set" (1 already in library, 1 already queued)'


async def test_spotify_playlist_link_reuses_previously_filed_source_url(inbox):
    ib, store = inbox
    rid = store.add_request("Shpongle - StarShpongled Banner", RequestKind.SPOTIFY_TRACK,
                            source_url="https://open.spotify.com/track/a")
    tid = store.add_track(path=Path("/shpongle.aiff"), fmt="aiff", bitrate_kbps=1411, cutoff_hz=22050, file_size=1,
                          artist="Shpongle", title="Star Shpongled Banner", mix_name="Remastered",
                          duration_s=442, isrc=None, catalog_track_id=None, request_id=rid)
    store.update_request(rid, track_id=tid, state=RequestState.DONE)

    s = await ib.submit("https://open.spotify.com/playlist/pl1")

    assert s.already_in_library == 1
    assert store.get_playlist(s.playlist_id).track_ids == [tid]


async def test_spotify_reimport_retries_a_failed_request_without_adding_a_duplicate(inbox):
    ib, store = inbox
    first = await ib.submit("https://open.spotify.com/playlist/pl1")
    failed_id = first.request_ids[0]
    store.set_state(failed_id, RequestState.NOT_FOUND, error_message="no match")

    again = await ib.submit("https://open.spotify.com/playlist/pl1")

    assert failed_id in again.request_ids
    assert store.get_request(failed_id).state is RequestState.QUEUED
    assert len([r for r in store.list_recent_requests() if r.playlist_id == first.playlist_id]) == 2
    assert again.already_queued == 1


async def test_unreadable_links_are_bad_links(inbox):
    ib, store = inbox
    with pytest.raises(BadLink, match=YT_FAIL):
        await ib.submit("https://www.youtube.com/watch?v=bad")
    with pytest.raises(BadLink, match=YT_FAIL):
        await ib.submit("https://www.youtube.com/watch?v=gone")
    with pytest.raises(BadLink, match=SPOTIFY_FAIL):
        await ib.submit("https://open.spotify.com/track/bad")
    with pytest.raises(BadLink, match=SPOTIFY_FAIL):
        await ib.submit("https://open.spotify.com/playlist/bad")
    assert store.list_requests() == []
