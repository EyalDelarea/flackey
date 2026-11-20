"""The download engine audit of 2026-11-20 (ENG-1 .. ENG-10): each test here was a repro of the bug, now
inverted to pin the fix."""
import asyncio
import os
import sqlite3
import subprocess
import time
from pathlib import Path

import pytest

from flackey.library import (
    NAME_MAX_BYTES,
    final_path,
    find_duplicate,
    prune_missing_tracks,
    sanitize,
    sweep_tmp,
)
from flackey.models import Candidate, CatalogTrack, RequestKind, RequestState
from flackey.source import SourceTimeout
from flackey.store import Store
from tests.conftest import requires_ffmpeg
from tests.test_worker import TEXT, FakeSource, env, good_cand, make_worker  # noqa: F401
from tests.test_worker_lossless import _file_on_deezer, detector, lenv, lf, make  # noqa: F401


def _track(store: Store, path: Path, title: str = "T") -> int:
    return store.add_track(path=path, fmt="mp3", bitrate_kbps=320, cutoff_hz=20000, file_size=1, artist="A",
                           title=title, mix_name="Original Mix", duration_s=1, isrc=None, catalog_track_id=None,
                           request_id=None)


# ---- ENG-1: an unplugged library drive is not a deleted library -------------------------------------

def test_prune_forgets_nothing_when_the_library_folder_is_missing(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    root = tmp_path / "Volumes" / "DJ" / "Music"                  # volume not mounted
    tid = _track(store, root / "A" / "A - T.mp3")
    pid = store.upsert_playlist("u", "Set")
    store.add_playlist_track(pid, tid, 1)
    assert prune_missing_tracks(store, root) == []
    assert [t.id for t in store.list_tracks()] == [tid] and store.get_playlist(pid).track_ids == [tid]


def test_prune_forgets_nothing_in_an_empty_stand_in_folder(tmp_path):
    """A mount point recreated on the boot disk: the folder is there, with at most an exported Playlists."""
    store = Store(tmp_path / "db.sqlite")
    root = tmp_path / "Music"
    (root / "Playlists").mkdir(parents=True)
    (root / ".DS_Store").write_bytes(b"")
    tid = _track(store, root / "A" / "A - T.mp3")
    assert prune_missing_tracks(store, root) == []
    assert [t.id for t in store.list_tracks()] == [tid]


def test_prune_forgets_nothing_when_most_of_a_big_library_vanishes_at_once(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    root = tmp_path / "Music"
    (root / "A").mkdir(parents=True)
    kept = root / "A" / "A - kept.mp3"
    kept.write_bytes(b"x")
    _track(store, kept, title="kept")
    for i in range(12):
        _track(store, root / "A" / f"A - {i}.mp3", title=str(i))
    assert prune_missing_tracks(store, root) == []
    assert len(store.list_tracks()) == 13


def test_prune_still_forgets_a_file_deleted_by_hand(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    root = tmp_path / "Music"
    (root / "A").mkdir(parents=True)
    kept = root / "A" / "A - kept.mp3"
    kept.write_bytes(b"x")
    a = _track(store, kept, title="kept")
    _track(store, root / "A" / "A - gone.mp3", title="gone")
    assert prune_missing_tracks(store, root) == [root / "A" / "A - gone.mp3"]
    assert [t.id for t in store.list_tracks()] == [a]


def test_a_duplicate_check_with_the_drive_unplugged_keeps_the_track(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    root = tmp_path / "Volumes" / "DJ" / "Music"
    _track(store, root / "A" / "A - T.mp3")
    cand = Candidate(source="x", source_ref="r", artist="A", title="T", mix_name="Original Mix", duration_s=1)
    assert find_duplicate(store, None, cand, library_root=root) is not None   # still owned, not re-downloaded
    assert len(store.list_tracks()) == 1


# ---- ENG-2: a duplicate joins the playlist file too -------------------------------------------------

async def test_duplicate_in_playlist_writes_the_m3u8(env):  # noqa: F811
    settings, store, _ = env
    w = make_worker(env, FakeSource([good_cand()]))
    await w.process(store.add_request(TEXT, RequestKind.TEXT))
    pid = store.upsert_playlist("https://x/p", "Set")
    r = await w.process(store.add_request(TEXT, RequestKind.YT_TRACK, playlist_id=pid, playlist_position=1))
    assert r.state == RequestState.DUPLICATE
    m3u = settings.library_root / "Playlists" / "Set.m3u8"
    assert m3u.exists() and store.get_track(r.track_id).path.name in m3u.read_text(encoding="utf-8")


async def test_a_pasted_playlist_of_owned_tracks_gets_its_m3u8(tmp_path):
    from flackey.inbox import Inbox
    from flackey.youtube import YouTubeEntry
    store = Store(tmp_path / "db.sqlite")
    root = tmp_path / "lib"
    (root / "A").mkdir(parents=True)
    f = root / "A" / "A - T.mp3"
    f.write_bytes(b"x")
    _track(store, f)

    async def youtube(url):
        return "Set", [YouTubeEntry(url="https://youtu.be/x", title="A - T", uploader="A", duration_s=1)]

    inbox = Inbox(store, youtube=youtube, library_root=lambda: root)
    sub = await inbox.submit("https://www.youtube.com/playlist?list=PLx")
    assert sub.already_in_library == 1
    assert "A - T.mp3" in (root / "Playlists" / "Set.m3u8").read_text(encoding="utf-8")


# ---- ENG-5: Find lossless onto a FLAC of the same name ----------------------------------------------

@requires_ffmpeg
async def test_upgrade_of_a_flac_filed_from_the_bot_replaces_it_in_place(lenv):  # noqa: F811
    settings, store, _, provider, _, _ = lenv
    w, before = await _file_on_deezer(lenv, provider)
    flac = before.path.with_suffix(".flac")
    await asyncio.to_thread(subprocess.run, ["ffmpeg", "-v", "error", "-y", "-i", str(before.path), str(flac)],
                            check=True)
    before.path.unlink()
    store.update_track(before.id, path=flac, fmt="flac")
    settings.lossless_filing_format = "flac"
    provider.files = [lf("a")]
    said = await w.upgrade(before.id)
    after = store.get_track(before.id)
    assert said.startswith("Upgraded") and after.path == flac and flac.exists() and after.source_fmt
    assert [p.name for p in flac.parent.iterdir() if p.name.startswith(".")] == []   # no staged copy left


# ---- ENG-6: nothing downloaded is left in tmp_dir ---------------------------------------------------

async def test_a_bot_timeout_leaves_no_partial_file_in_tmp(env):  # noqa: F811
    settings, store, _ = env

    class Partial(FakeSource):
        async def fetch(self, cand, dest_dir):
            dest_dir.mkdir(parents=True, exist_ok=True)
            (dest_dir / "1754956977.mp3").write_bytes(b"ID3partial")
            raise SourceTimeout("source bot did not send the file")

    w = make_worker(env, Partial([good_cand()]))
    rid = store.add_request(TEXT, RequestKind.TEXT)
    r = await w.process(rid)
    assert r.state == RequestState.QUEUED
    assert not (settings.tmp_dir / f"req{rid}").exists()


@requires_ffmpeg
async def test_stop_during_verify_leaves_nothing_in_tmp(lenv, monkeypatch):  # noqa: F811
    from flackey.worker import lossless_attempt as la
    settings, store, _, provider, _, _ = lenv
    real = la.verify

    def slow_verify(*a, **k):
        time.sleep(0.5)
        return real(*a, **k)

    monkeypatch.setattr(la, "verify", slow_verify)
    w = make(lenv, provider=provider)
    rid = store.add_request(TEXT, RequestKind.TEXT)
    task = asyncio.create_task(w.process(rid))
    w._tasks[rid] = task
    for _ in range(400):
        if (settings.tmp_dir / f"req{rid}-lossless.flac").exists():
            break
        await asyncio.sleep(0.01)
    await w.cancel(rid)
    try:
        await task
    except asyncio.CancelledError:
        pass
    await asyncio.sleep(0.8)          # let the verify thread the Stop could not interrupt run out
    assert [p.name for p in settings.tmp_dir.iterdir() if p.name.startswith(f"req{rid}")] == []


def test_startup_sweep_deletes_only_old_leftovers(tmp_path):
    tmp = tmp_path / "tmp"
    (tmp / "req1").mkdir(parents=True)
    (tmp / "req1" / "x.mp3").write_bytes(b"x")
    (tmp / "req2-lossless.flac").write_bytes(b"x")
    (tmp / "req3-lossless.flac").write_bytes(b"x")
    old = time.time() - 3 * 24 * 3600
    for p in (tmp / "req1", tmp / "req2-lossless.flac"):
        os.utime(p, (old, old))
    assert sweep_tmp(tmp, time.time()) == 2
    assert [p.name for p in tmp.iterdir()] == ["req3-lossless.flac"]


# ---- ENG-7: names fit in 255 bytes ------------------------------------------------------------------

@pytest.mark.skipif(os.name == "nt", reason="the Windows branch cuts by path length instead")
def test_a_long_unicode_name_fits_the_file_name_limit(tmp_path):
    cat = CatalogTrack(id=1, isrc=None, artist="ア" * 100, title="曲" * 100, mix_name="Original Mix",
                       label="l", genre="g")
    p = final_path(tmp_path, cat, "aiff")
    assert len(p.name.encode()) <= NAME_MAX_BYTES and len(p.parent.name.encode()) <= NAME_MAX_BYTES
    p.parent.mkdir(parents=True)
    p.write_bytes(b"x")
    other = CatalogTrack(id=2, isrc=None, artist="ア" * 100, title="曲" * 99 + "歌", mix_name="Original Mix",
                         label="l", genre="g")
    assert final_path(tmp_path, other, "aiff") != p        # two long titles stay two files


@pytest.mark.skipif(os.name == "nt", reason="the Windows branch cuts by path length instead")
def test_a_name_that_fits_is_unchanged(tmp_path):
    cat = CatalogTrack(id=1, isrc=None, artist="Hallucinogen", title="LSD", mix_name="Original Mix",
                       label="l", genre="g")
    assert final_path(tmp_path, cat, "aiff") == tmp_path / "Hallucinogen" / "Hallucinogen - LSD.aiff"
    assert sanitize("x" * 200) == "x" * 120


# ---- ENG-8: a damaged bot download goes up the retry ladder -----------------------------------------

@requires_ffmpeg
async def test_truncated_bot_download_is_retried(env):  # noqa: F811
    _, store, _ = env

    class Trunc(FakeSource):
        async def fetch(self, cand, dest_dir):
            dest_dir.mkdir(parents=True, exist_ok=True)
            p = dest_dir / "1754956977.mp3"
            p.write_bytes(b"ID3\x03\x00\x00\x00\x00\x00\x00" + b"\xff\xfb\x90\x00" * 50)   # cut-off transfer
            return p

    w = make_worker(env, Trunc([good_cand()]))
    rid = store.add_request(TEXT, RequestKind.TEXT)
    r = await w.process(rid)
    assert r.state == RequestState.QUEUED and r.attempts == 1
    assert r.flag_reason == "the file the bot sent was damaged"


# ---- ENG-9: one Find lossless per track at a time ---------------------------------------------------

@requires_ffmpeg
async def test_find_lossless_refuses_a_second_press_while_the_first_runs(lenv):  # noqa: F811
    provider = lenv[3]
    w, t = await _file_on_deezer(lenv, provider)
    w._extending.add(t.id)
    with pytest.raises(ValueError, match="already being fetched"):
        await w.upgrade(t.id)


# ---- ENG-10: a failed multi-statement write is rolled back ------------------------------------------

def test_a_failed_candidate_write_keeps_the_previous_list(tmp_path):
    store = Store(tmp_path / "db.sqlite")
    rid = store.add_request("q", RequestKind.TEXT)
    ok = Candidate(source="deezer_bot", source_ref="a", artist="A", title="T", rank=1)
    store.add_candidates(rid, [ok])
    bad = Candidate(source="deezer_bot", source_ref="b", artist="A", title=None, rank=1)
    with pytest.raises(sqlite3.IntegrityError):
        store.add_candidates(rid, [bad])
    store.add_request("other", RequestKind.TEXT)                 # an unrelated commit
    assert [c.source_ref for c in store.get_candidates(rid)] == ["a"]
