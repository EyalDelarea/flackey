from pathlib import Path

import pytest

from flackey.config import Settings
from flackey.inbox import NOT_A_LINK, YT_FAIL, BadLink, Inbox
from flackey.models import RequestKind, RequestState
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

    return Inbox(store, youtube=fake_youtube), store


async def test_plain_text_is_a_bad_link(inbox):
    ib, store = inbox
    with pytest.raises(BadLink, match=NOT_A_LINK):
        await ib.submit("Astral Projection - Into the Void")
    with pytest.raises(BadLink, match=NOT_A_LINK):
        await ib.submit("https://open.spotify.com/track/1")
    assert store.list_requests() == []


async def test_track_link_queues_one_request(inbox):
    ib, store = inbox
    s = await ib.submit("https://youtu.be/abc")
    assert s.summary() == "Queued" and s.total == 1 and len(s.request_ids) == 1 and s.playlist_id is None
    r = store.get_request(s.request_ids[0])
    assert r.kind == RequestKind.YT_TRACK and r.state == RequestState.QUEUED
    assert r.query_artist == "Astral Projection" and r.query_title == "Into The Void" and r.query_duration_s == 442
    assert r.source_url == "https://www.youtube.com/watch?v=abc"


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


async def test_unreadable_links_are_bad_links(inbox):
    ib, store = inbox
    with pytest.raises(BadLink, match=YT_FAIL):
        await ib.submit("https://www.youtube.com/watch?v=bad")
    with pytest.raises(BadLink, match=YT_FAIL):
        await ib.submit("https://www.youtube.com/watch?v=gone")
    assert store.list_requests() == []
