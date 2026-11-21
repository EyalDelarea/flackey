from pathlib import Path

from flackey.export import (
    NEW_PLAYLIST_NAME,
    build_m3u8,
    filed_at,
    mark_imported,
    new_playlist_path,
    new_tracks,
    playlist_names,
    write_new_playlist,
    write_playlist,
    write_playlists,
)
from flackey.models import Playlist, Track
from flackey.store import Store


def _track(tid: int, path: str, artist: str = "A", title: str = "T", duration_s: int | None = 442) -> Track:
    return Track(id=tid, path=Path(path), fmt="mp3", bitrate_kbps=320, cutoff_hz=20000, file_size=1,
                 artist=artist, title=title, mix_name="Original Mix", duration_s=duration_s, isrc=None,
                 catalog_track_id=None, request_id=None, added_at="2026-09-03T00:00:00+00:00")


def _playlist(pid: int, name: str, track_ids: list[int]) -> Playlist:
    return Playlist(id=pid, source_url=f"u{pid}", name=name, created_at="", updated_at="", track_ids=track_ids)


def test_build_m3u8_keeps_playlist_order_and_skips_unknown_tracks():
    tracks = {1: _track(1, "/lib/Psy-Trance/L/A - T.mp3"),
              2: _track(2, "/lib/Goa/L/B - U (Remix).mp3", "B", "U", None)}
    out = build_m3u8(_playlist(1, "Goa Set", [2, 99, 1]), tracks)
    assert out.splitlines() == [
        "#EXTM3U",
        "#PLAYLIST:Goa Set",
        "#EXTINF:-1,B - U",
        # The path as this system writes it: backslashes on Windows, which is what Rekordbox there reads.
        str(Path("/lib/Goa/L/B - U (Remix).mp3")),
        "#EXTINF:442,A - T",
        str(Path("/lib/Psy-Trance/L/A - T.mp3")),
    ]


def test_playlist_names_are_safe_and_unique():
    names = playlist_names([_playlist(1, "Goa: Set/2026?", []), _playlist(2, "Liked Music", []),
                               _playlist(3, "Liked Music", [])])
    assert names == {1: "Goa- Set-2026-", 2: "Liked Music", 3: "Liked Music (3)"}


def test_write_playlist_writes_single_playlist_with_correct_path(tmp_path: Path):
    s = Store(tmp_path / "db.sqlite")
    lib = tmp_path / "lib"
    tid = s.add_track(path=lib / "Psy-Trance" / "L" / "A - T.mp3", fmt="mp3", bitrate_kbps=320, cutoff_hz=20000,
                      file_size=1, artist="A", title="T", mix_name="Original Mix", duration_s=442, isrc=None,
                      catalog_track_id=None, request_id=None)
    pid1 = s.upsert_playlist("https://music.youtube.com/playlist?list=1", "Goa Set")
    s.add_playlist_track(pid1, tid, 1)
    # Create a second playlist with the same name to test collision suffix
    pid2 = s.upsert_playlist("https://music.youtube.com/playlist?list=2", "Goa Set")
    written1 = write_playlist(s, pid1, lib)
    written2 = write_playlist(s, pid2, lib)
    # First playlist gets the base name
    assert written1 == lib / "Playlists" / "Goa Set.m3u8"
    # Second playlist with same name gets collision suffix
    assert written2 == lib / "Playlists" / "Goa Set (2).m3u8"
    # Verify content matches build_m3u8
    tracks1 = {tid: s.get_track(tid)}
    expected1 = build_m3u8(s.get_playlist(pid1), tracks1)
    assert written1.read_text(encoding="utf-8") == expected1


def test_write_playlists_writes_one_file_per_playlist(tmp_path: Path):
    s = Store(tmp_path / "db.sqlite")
    lib = tmp_path / "lib"
    tid = s.add_track(path=lib / "Psy-Trance" / "L" / "A - T.mp3", fmt="mp3", bitrate_kbps=320, cutoff_hz=20000,
                      file_size=1, artist="A", title="T", mix_name="Original Mix", duration_s=442, isrc=None,
                      catalog_track_id=None, request_id=None)
    pid = s.upsert_playlist("https://music.youtube.com/playlist?list=1", "Goa Set")
    s.add_playlist_track(pid, tid, 1)
    written = write_playlists(s, lib)
    assert written == [lib / "Playlists" / "Goa Set.m3u8", new_playlist_path(lib)]
    text = written[0].read_text(encoding="utf-8")
    assert text.startswith("#EXTM3U\n#PLAYLIST:Goa Set\n") and str(lib / "Psy-Trance" / "L" / "A - T.mp3") in text
    assert write_playlists(s, lib) == written  # idempotent, atomic replace
    assert not list((lib / "Playlists").glob("*.tmp"))


def test_build_m3u8_keeps_names_that_carry_line_breaks_on_one_line():
    """A line break in a name would start an entry of its own, which Rekordbox imports as a path."""
    tracks = {1: _track(1, "/lib/A/A - T.mp3", "A\n/Users/x/secret.mp3", "T\r\n#EXTINF")}
    out = build_m3u8(_playlist(1, "Set\n/etc/passwd", [1]), tracks)
    assert out.splitlines() == ["#EXTM3U", "#PLAYLIST:Set /etc/passwd",
                                "#EXTINF:442,A /Users/x/secret.mp3 - T #EXTINF", str(Path("/lib/A/A - T.mp3"))]


# ---- the "Flackey - New" playlist ------------------------------------------------------------------------
def _add(s: Store, lib: Path, title: str, added: str) -> int:
    tid = s.add_track(path=lib / "A" / f"A - {title}.flac", fmt="flac", bitrate_kbps=900, cutoff_hz=22000,
                      file_size=1, artist="A", title=title, mix_name="Original Mix", duration_s=300, isrc=None,
                      catalog_track_id=None, request_id=None)
    s.update_track(tid, added_at=added, verified_at=added)
    return tid


def test_new_playlist_holds_every_track_until_the_first_import(tmp_path: Path):
    s, lib = Store(tmp_path / "db.sqlite"), tmp_path / "lib"
    _add(s, lib, "Two", "2026-11-02T00:00:00+00:00")
    _add(s, lib, "One", "2026-11-01T00:00:00+00:00")
    assert [t.title for t in new_tracks(s)] == ["One", "Two"]   # oldest first, the order they arrived
    out = write_new_playlist(s, lib)
    assert out == new_playlist_path(lib) == lib / "Playlists" / f"{NEW_PLAYLIST_NAME}.m3u8"
    text = out.read_text(encoding="utf-8")
    assert text.startswith(f"#EXTM3U\n#PLAYLIST:{NEW_PLAYLIST_NAME}\n")
    assert text.index("A - One.flac") < text.index("A - Two.flac")


def test_marking_imported_keeps_only_what_landed_after_the_list_the_owner_saw(tmp_path: Path):
    s, lib = Store(tmp_path / "db.sqlite"), tmp_path / "lib"
    _add(s, lib, "Old", "2026-11-01T00:00:00+00:00")
    seen = new_tracks(s)
    # Filed while the owner was in Rekordbox: not in the list they imported, so it must stay new
    _add(s, lib, "During", "2026-11-03T00:00:00+00:00")
    mark_imported(s, filed_at(seen[-1]))
    assert [t.title for t in new_tracks(s)] == ["During"]
    assert "A - Old.flac" not in write_new_playlist(s, lib).read_text(encoding="utf-8")


def test_an_upgraded_file_is_new_again(tmp_path: Path):
    """An upgrade puts a new file at a new path, which Rekordbox has to import like any new track."""
    s, lib = Store(tmp_path / "db.sqlite"), tmp_path / "lib"
    tid = _add(s, lib, "T", "2026-11-01T00:00:00+00:00")
    mark_imported(s, "2026-11-02T00:00:00+00:00")
    assert new_tracks(s) == []
    s.update_track(tid, path=lib / "A" / "A - T.aiff", verified_at="2026-11-05T00:00:00+00:00")
    assert [t.id for t in new_tracks(s)] == [tid]


def test_an_empty_new_playlist_is_still_written(tmp_path: Path):
    s, lib = Store(tmp_path / "db.sqlite"), tmp_path / "lib"
    assert write_new_playlist(s, lib).read_text(encoding="utf-8") == f"#EXTM3U\n#PLAYLIST:{NEW_PLAYLIST_NAME}\n"


def test_a_playlist_called_like_the_new_one_does_not_overwrite_it():
    assert playlist_names([_playlist(4, NEW_PLAYLIST_NAME, [])]) == {4: f"{NEW_PLAYLIST_NAME} (4)"}
