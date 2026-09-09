from pathlib import Path

from krater.export import build_m3u8, playlist_names, write_playlist, write_playlists
from krater.models import Playlist, Track
from krater.store import Store


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
        "/lib/Goa/L/B - U (Remix).mp3",
        "#EXTINF:442,A - T",
        "/lib/Psy-Trance/L/A - T.mp3",
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
    assert written == [lib / "Playlists" / "Goa Set.m3u8"]
    text = written[0].read_text(encoding="utf-8")
    assert text.startswith("#EXTM3U\n#PLAYLIST:Goa Set\n") and str(lib / "Psy-Trance" / "L" / "A - T.mp3") in text
    assert write_playlists(s, lib) == written  # idempotent, atomic replace
    assert not list((lib / "Playlists").glob("*.tmp"))
