"""A library on a drive that is not plugged in: never created, never filed into, and filed in full once
the drive is back. The drives here live under a fake /Volumes in the test's own folder, and "plugged in"
means the test says that folder is a mount point."""
import os
import sys
from pathlib import Path

import pytest

from flackey import library
from flackey.library import DriveMissing, library_reachable, make_dirs, missing_drive
from flackey.models import RequestKind, RequestState
from flackey.notify import drive_missing_note
from flackey.worker import Worker
from flackey.worker import filing as filing_mod
from tests.appclient import AppClient
from tests.conftest import requires_ffmpeg
from tests.test_worker import TEXT, FakeSource, env, good_cand, make_worker, no_art  # noqa: F401 -- env is a fixture

# The fake /Volumes is a POSIX path; Windows' own rule is the drive-letter test below
posix_only = pytest.mark.skipif(sys.platform == "win32", reason="a fake /Volumes is a POSIX path")


@pytest.fixture
def volumes(tmp_path: Path, monkeypatch):
    """A fake /Volumes, and the set of its drives that count as plugged in."""
    root = tmp_path / "Volumes"
    root.mkdir()
    monkeypatch.setattr(library, "VOLUMES", root)
    mounted: set[str] = set()
    real = os.path.ismount
    monkeypatch.setattr(library.os.path, "ismount",
                        lambda p: os.path.realpath(p) in mounted or real(p))

    def plug(name: str) -> Path:
        drive = root / name
        drive.mkdir(exist_ok=True)
        mounted.add(os.path.realpath(drive))
        return drive

    def unplug(name: str) -> None:
        mounted.discard(os.path.realpath(root / name))

    return root, plug, unplug


# -- reachability -------------------------------------------------------------------------------------------

@posix_only
def test_a_folder_on_an_unplugged_drive_is_missing_its_drive(volumes):
    root, plug, unplug = volumes
    lib = root / "WD" / "DJ Backup 2026-11-12" / "Music" / "DJ Library"
    assert missing_drive(lib) == "WD"
    assert not library_reachable(lib)
    plug("WD")
    assert missing_drive(lib) is None
    unplug("WD")
    assert missing_drive(lib) == "WD"


@posix_only
def test_a_leftover_mount_point_is_not_the_drive_even_with_files_in_it(volumes):
    """An empty /Volumes/WD on the boot disk is what an unplugged drive can leave behind; whatever was
    written into it is on the boot disk, so it is not the library."""
    root, _, _ = volumes
    lib = root / "WD" / "DJ Library"
    (lib / "Artist").mkdir(parents=True)
    (lib / "Artist" / "a.mp3").write_bytes(b"x")
    assert missing_drive(lib) == "WD"
    assert not library_reachable(lib)


@posix_only
def test_folders_off_the_volumes_folder_have_no_drive_to_miss(volumes, tmp_path):
    root, _, _ = volumes
    assert missing_drive(tmp_path / "Music" / "DJ Library") is None
    assert missing_drive(root) is None
    # /Volumes/Macintosh HD is a symlink to the boot disk itself
    boot = tmp_path / "boot"
    (boot / "Users").mkdir(parents=True)
    (root / "Macintosh HD").symlink_to(boot)
    assert missing_drive(root / "Macintosh HD" / "Users") is None


@posix_only
def test_a_symlink_into_an_unplugged_drive_counts_as_that_drive(volumes, tmp_path):
    root, _, _ = volumes
    link = tmp_path / "DJ Library"
    link.symlink_to(root / "WD" / "DJ Library")
    assert missing_drive(link) == "WD"


def test_a_missing_windows_drive_letter(monkeypatch):
    monkeypatch.setattr(library.sys, "platform", "win32")
    present = {"C:"}
    monkeypatch.setattr(library, "_drive_present", lambda d: d.upper() in present)
    assert missing_drive(Path("e:/Music/DJ Library")) == "E:"
    assert missing_drive(Path("C:/Users/me/Music")) is None
    present.add("E:")
    assert missing_drive(Path("e:/Music/DJ Library")) is None


@posix_only
def test_nothing_is_ever_created_on_an_unplugged_drive(volumes):
    root, plug, _ = volumes
    lib = root / "WD" / "DJ Library"
    with pytest.raises(DriveMissing) as e:
        make_dirs(lib / "Artist")
    assert e.value.drive == "WD"
    assert not (root / "WD").exists()
    plug("WD")
    make_dirs(lib / "Artist")
    assert (lib / "Artist").is_dir()


# -- the worker ---------------------------------------------------------------------------------------------

@pytest.fixture
def unplugged(env, volumes):  # noqa: F811 -- env is the worker fixture
    settings, store, notifier = env
    root, plug, unplug = volumes
    settings.library_root = root / "WD" / "DJ Library"
    return settings, store, notifier, root, plug, unplug


@posix_only
@requires_ffmpeg
async def test_a_checked_track_waits_for_the_drive_then_is_filed_in_full(unplugged):
    settings, store, notifier, root, plug, _ = unplugged
    w = make_worker((settings, store, notifier), FakeSource([good_cand()]))
    rid = store.add_request(TEXT, RequestKind.TEXT)

    r = await w.process(rid)

    assert r.state == RequestState.WAITING_FOR_DRIVE and r.track_id is None
    assert not (root / "WD").exists(), "the drive's mount point must never be created"
    [(req, held, filing)] = store.waiting_for_drive()
    assert req.id == rid and held.parent == settings.drive_wait_dir and held.is_file()
    assert filing["track"]["artist"] == "Astral Projection" and store.list_tracks() == []
    assert not notifier.sent or not notifier.sent[-1][0].startswith("Done:")

    # Still unplugged: the loop's look leaves it waiting and says so
    await w._watch_drive()
    assert store.get_request(rid).state == RequestState.WAITING_FOR_DRIVE
    assert w.status["library_drive"] == {"connected": False, "name": "WD", "waiting": 1}

    plug("WD")
    await w._watch_drive()

    r = store.get_request(rid)
    assert r.state == RequestState.DONE
    track = store.get_track(r.track_id)
    assert track.path == settings.library_root / "Astral Projection" / "Astral Projection - Into the Void.mp3"
    assert track.path.exists() and track.isrc == "UKU932231081" and track.source == "deezer_bot"
    assert store.evidence_of(track.id, "recording_match")["status"] == "matched"
    assert not held.exists() and store.waiting_for_drive() == []
    new = settings.library_root / "Playlists" / "Flackey - New.m3u8"
    assert "Astral Projection - Into the Void.mp3" in new.read_text()
    assert notifier.sent[-1][0].startswith("Done: Astral Projection – Into the Void")
    assert w.status["library_drive"] == {"connected": True, "name": None, "waiting": 0}


@posix_only
@requires_ffmpeg
async def test_a_waiting_track_is_filed_as_it_would_have_been_at_once(unplugged):
    """The same request, filed straight onto a plugged-in drive and filed after waiting, ends up with the
    same row and the same evidence."""
    settings, store, notifier, _root, plug, unplug = unplugged
    w = make_worker((settings, store, notifier), FakeSource([good_cand()]))
    plug("WD")
    direct = store.get_track((await w.process(store.add_request(TEXT, RequestKind.TEXT))).track_id)
    direct_evidence = {k: store.evidence_of(direct.id, k) for k in ("recording_match", "fingerprint", "verification")}
    store.delete_tracks([direct.id])
    direct.path.unlink()

    unplug("WD")
    rid = store.add_request(TEXT, RequestKind.TEXT)
    assert (await w.process(rid)).state == RequestState.WAITING_FOR_DRIVE
    plug("WD")
    await w._watch_drive()
    later = store.get_track(store.get_request(rid).track_id)

    same = ("path", "fmt", "bitrate_kbps", "cutoff_hz", "artist", "title", "mix_name", "duration_s", "isrc",
            "catalog_track_id", "source", "source_fmt", "bit_depth", "sample_rate", "tag_date")
    assert {f: getattr(later, f) for f in same} == {f: getattr(direct, f) for f in same}
    assert {k: store.evidence_of(later.id, k) for k in direct_evidence} == direct_evidence


@posix_only
@requires_ffmpeg
async def test_a_restart_keeps_the_waiting_track_and_files_it_when_the_drive_is_back(unplugged):
    settings, store, notifier, _root, plug, _ = unplugged
    w = make_worker((settings, store, notifier), FakeSource([good_cand()]))
    rid = store.add_request(TEXT, RequestKind.TEXT)
    await w.process(rid)

    # A restart: nothing in flight is sent back to the queue, and tmp is swept, but the held file stays
    again = Worker(store, FakeSource([good_cand()]), notifier, settings, artwork_fetch=no_art)
    again.on_start()
    assert store.get_request(rid).state == RequestState.WAITING_FOR_DRIVE
    [(_, held, _)] = store.waiting_for_drive()
    assert held.is_file()

    plug("WD")
    await again._watch_drive()
    assert store.get_request(rid).state == RequestState.DONE
    assert store.get_track(store.get_request(rid).track_id).path.exists()


@posix_only
@requires_ffmpeg
async def test_the_owner_is_told_once_each_time_the_drive_goes(unplugged):
    settings, store, notifier, _root, plug, unplug = unplugged
    w = make_worker((settings, store, notifier), FakeSource([good_cand()]))
    told: list[str] = []
    w.on_drive_missing = told.append
    for _ in range(3):
        await w._watch_drive()
    assert told == ["WD"]
    plug("WD")
    await w._watch_drive()
    unplug("WD")
    await w._watch_drive()
    await w._watch_drive()
    assert told == ["WD", "WD"]


def test_the_notification_says_what_happens_next():
    title, body = drive_missing_note("WD")
    assert title == "Your music drive (WD) isn't connected"
    assert body == "Flackey keeps downloading. New tracks will wait and be filed when you plug it in."


@posix_only
@requires_ffmpeg
async def test_find_lossless_is_refused_before_it_downloads_while_the_drive_is_unplugged(unplugged):
    settings, store, notifier, _root, plug, unplug = unplugged
    w = make_worker((settings, store, notifier), FakeSource([good_cand()]))
    plug("WD")
    r = await w.process(store.add_request(TEXT, RequestKind.TEXT))
    unplug("WD")
    with pytest.raises(ValueError, match=r"music drive \(WD\) isn't connected"):
        await w.upgrade(r.track_id)


# -- the settings page --------------------------------------------------------------------------------------

@posix_only
def test_settings_still_save_with_the_saved_folder_on_an_unplugged_drive(volumes, tmp_path):
    from tests.test_web import make
    root, _, _ = volumes
    app, _, settings = make(tmp_path)
    c = AppClient(app)
    lib = root / "WD" / "DJ Library"
    settings.library_root = lib

    r = c.put("/api/settings", json={"library_root": str(lib), "auto_update_check": False})

    assert r.status_code == 200 and r.json()["auto_update_check"] is False
    assert not (root / "WD").exists()


@posix_only
def test_choosing_a_new_folder_on_an_unplugged_drive_is_refused_without_creating_it(volumes, tmp_path):
    from tests.test_web import make
    root, _, _ = volumes
    app, _, _ = make(tmp_path)
    r = AppClient(app).put("/api/settings", json={"library_root": str(root / "WD" / "DJ Library")})
    assert r.status_code == 400 and "The drive WD isn't connected" in r.json()["detail"]
    assert not (root / "WD").exists()


@posix_only
@requires_ffmpeg
async def test_a_drive_pulled_out_mid_copy_leaves_the_track_waiting(unplugged, monkeypatch):
    settings, store, notifier, _, plug, unplug = unplugged
    w = make_worker((settings, store, notifier), FakeSource([good_cand()]))
    rid = store.add_request(TEXT, RequestKind.TEXT)
    await w.process(rid)
    [(_, held, _)] = store.waiting_for_drive()
    plug("WD")

    def yanked(tmp, dest):
        unplug("WD")
        raise OSError(5, "Input/output error")
    monkeypatch.setattr(filing_mod, "file_track", yanked)
    await w._watch_drive()

    assert store.get_request(rid).state == RequestState.WAITING_FOR_DRIVE
    assert held.is_file() and store.list_tracks() == []
