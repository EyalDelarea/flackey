from pathlib import Path

import pytest

from krater.library import file_track, final_path, find_duplicate, sanitize
from krater.models import Candidate, CatalogTrack
from krater.store import Store

CT = CatalogTrack(id=1, isrc="UKU932231081", artist="Astral Projection", title="Into the Void",
                  mix_name="Original Mix", label="Sacred Technology", genre="Psy-Trance", duration_ms=442816)


@pytest.mark.parametrize("raw,expected", [
    ("Psy-Trance", "Psy-Trance"),
    ("AC/DC", "AC-DC"),
    ('What: "Is" This?', "What- -Is- This-"),
    ("  spaced   out  ", "spaced out"),
    ("...dots...", "dots"),
    ("", "Unknown"),
    ("x" * 200, "x" * 120),
])
def test_sanitize(raw, expected):
    assert sanitize(raw) == expected


def test_final_path_files_under_the_artist(tmp_path: Path):
    assert final_path(tmp_path, CT, "mp3") == tmp_path / "Astral Projection" / "Astral Projection - Into the Void.mp3"


def test_final_path_files_a_collaboration_under_the_first_artist(tmp_path: Path):
    ct = CatalogTrack(**{**CT.__dict__, "artist": "Abeber Project, Filteria", "mix_name": "Abeber Project Remix"})
    p = final_path(tmp_path, ct, "mp3")
    assert p == tmp_path / "Abeber Project" / "Abeber Project, Filteria - Into the Void (Abeber Project Remix).mp3"


def test_final_path_includes_remix(tmp_path: Path):
    ct = CatalogTrack(**{**CT.__dict__, "mix_name": "Vini Vici Remix"})
    assert final_path(tmp_path, ct, "flac").name == "Astral Projection - Into the Void (Vini Vici Remix).flac"


def test_file_track_moves_and_refuses_overwrite(tmp_path: Path):
    src = tmp_path / "in.mp3"
    src.write_bytes(b"x")
    dest = tmp_path / "lib" / "G" / "L" / "a.mp3"
    assert file_track(src, dest) == dest
    assert dest.read_bytes() == b"x" and not src.exists()
    src.write_bytes(b"y")
    with pytest.raises(FileExistsError):
        file_track(src, dest)


def test_find_duplicate_by_isrc_then_meta(tmp_path: Path):
    store = Store(tmp_path / "s.sqlite")
    cand = Candidate(source="deezer_bot", source_ref="r", artist="Astral Projection", title="Into the Void",
                     duration_s=442)
    assert find_duplicate(store, CT, cand) is None
    (tmp_path / "a.mp3").write_bytes(b"x")  # a row whose file is gone no longer counts as a duplicate
    store.add_track(path=tmp_path / "a.mp3", fmt="mp3", bitrate_kbps=320, cutoff_hz=19800, file_size=1,
                    artist="Astral Projection", title="Into the Void", mix_name="Original Mix", duration_s=443,
                    isrc="UKU932231081", catalog_track_id=1, request_id=None)
    assert find_duplicate(store, CT, cand) is not None
    no_isrc = CatalogTrack(**{**CT.__dict__, "isrc": None})
    assert find_duplicate(store, no_isrc, cand) is not None  # meta match
    assert find_duplicate(store, None, Candidate(source="x", source_ref="r", artist="Other", title="Song")) is None


def _add(store, path, title="Into the Void", duration_s=443):
    return store.add_track(path=path, fmt="mp3", bitrate_kbps=320, cutoff_hz=19800, file_size=1,
                           artist="Hallucinogen", title=title, mix_name="Original Mix", duration_s=duration_s,
                           isrc=None, catalog_track_id=None, request_id=None)


def test_find_duplicate_ignores_punctuation_in_title(tmp_path: Path):
    store = Store(tmp_path / "s.sqlite")
    f = tmp_path / "lsd.mp3"; f.write_bytes(b"x")
    _add(store, f, title="L.S.D.", duration_s=403)
    cand = Candidate(source="deezer_bot", source_ref="r", artist="Hallucinogen", title="LSD", duration_s=404)
    assert find_duplicate(store, None, cand) is not None


def test_find_duplicate_drops_rows_whose_file_is_gone(tmp_path: Path):
    store = Store(tmp_path / "s.sqlite")
    tid = _add(store, tmp_path / "deleted-by-hand.mp3")
    cand = Candidate(source="deezer_bot", source_ref="r", artist="Hallucinogen", title="Into the Void", duration_s=443)
    assert find_duplicate(store, None, cand) is None
    assert store.list_tracks() == [] and tid not in [t.id for t in store.list_tracks()]


def test_prune_missing_tracks_removes_rows_and_playlist_membership(tmp_path: Path):
    from krater.library import prune_missing_tracks
    store = Store(tmp_path / "s.sqlite")
    kept = tmp_path / "kept.mp3"; kept.write_bytes(b"x")
    a = _add(store, kept); b = _add(store, tmp_path / "gone.mp3", title="Other")
    pid = store.upsert_playlist("u", "P"); store.add_playlist_track(pid, a, 1); store.add_playlist_track(pid, b, 2)
    assert prune_missing_tracks(store) == [tmp_path / "gone.mp3"]
    assert [t.id for t in store.list_tracks()] == [a]
    assert store.get_playlist(pid).track_ids == [a]
