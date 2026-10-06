import sys
from datetime import date
from pathlib import Path

import pytest

from flackey.library import (
    WINDOWS_MAX_PATH,
    file_track,
    final_path,
    find_duplicate,
    refile_path,
    sanitize,
)
from flackey.models import Candidate, CatalogTrack
from flackey.store import Store

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


@pytest.mark.parametrize("layout,folder", [
    ("artist", "Astral Projection"),
    ("month", "2026-10"),
    ("day", "2026-10-06"),
])
def test_final_path_folder_follows_the_layout(tmp_path: Path, layout, folder):
    p = final_path(tmp_path, CT, "aiff", layout=layout, when=date(2026, 10, 6))
    assert p == tmp_path / folder / "Astral Projection - Into the Void.aiff"


def test_final_path_one_folder_files_straight_into_the_library(tmp_path: Path):
    p = final_path(tmp_path, CT, "aiff", layout="flat", when=date(2026, 10, 6))
    assert p == tmp_path / "Astral Projection - Into the Void.aiff"


def test_final_path_rejects_an_unknown_layout(tmp_path: Path):
    with pytest.raises(ValueError):
        final_path(tmp_path, CT, "aiff", layout="genre")


def test_refile_path_keeps_the_folder_the_track_is_already_in(tmp_path: Path):
    """A lossless upgrade replaces the file where it is: a track filed in 2026-09 must not move into this
    month's folder, and one filed under an artist must not leave it when the layout changed since."""
    folder = tmp_path / "2026-09"
    assert refile_path(folder, CT, "flac") == folder / "Astral Projection - Into the Void.flac"


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
    from flackey.library import prune_missing_tracks
    store = Store(tmp_path / "s.sqlite")
    kept = tmp_path / "kept.mp3"; kept.write_bytes(b"x")
    a = _add(store, kept); b = _add(store, tmp_path / "gone.mp3", title="Other")
    pid = store.upsert_playlist("u", "P"); store.add_playlist_track(pid, a, 1); store.add_playlist_track(pid, b, 2)
    assert prune_missing_tracks(store) == [tmp_path / "gone.mp3"]
    assert [t.id for t in store.list_tracks()] == [a]
    assert store.get_playlist(pid).track_ids == [a]


# ---- Windows file names ------------------------------------------------------------------------

@pytest.fixture
def on_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")


@pytest.mark.parametrize("raw,expected", [
    ("Aux", "Aux_"), ("con", "con_"), ("NUL.remix", "NUL_.remix"), ("COM1", "COM1_"), ("lpt9 ", "lpt9_"),
    ("Auxiliary", "Auxiliary"), ("Con Brio", "Con Brio"), ("COM10", "COM10"),
    ("tab\x01bed", "tab-bed"),
    ("x" * 119 + " y", "x" * 119),  # the cut lands on the space, which Windows would drop by itself
])
def test_sanitize_on_windows_avoids_device_names_and_trailing_spaces(on_windows, raw, expected):
    assert sanitize(raw) == expected


@pytest.mark.parametrize("raw", ["Aux", "x" * 119 + " y", "tab\x01bed"])
def test_sanitize_on_the_mac_keeps_the_names_it_always_gave(monkeypatch, raw):
    monkeypatch.setattr(sys, "platform", "darwin")
    assert sanitize(raw) == " ".join(raw.split())[:120]


def test_a_windows_artist_called_aux_gets_a_folder_windows_will_open(on_windows, tmp_path: Path):
    ct = CatalogTrack(**{**CT.__dict__, "artist": "Aux"})
    assert final_path(tmp_path, ct, "flac").parent == tmp_path / "Aux_"


def _long(title: str) -> CatalogTrack:
    return CatalogTrack(**{**CT.__dict__, "title": title})


def test_a_windows_path_is_cut_to_fit_max_path(on_windows):
    root = Path("C:/Users/someone/Music/Flackey")
    p = final_path(root, _long("A" * 110 + " Extended"), "flac")
    assert len(str(p)) <= WINDOWS_MAX_PATH
    assert p.parent == root / "Astral Projection"
    assert p.name.startswith("Astral Projection - AAAA") and p.suffix == ".flac"


def test_two_long_titles_that_differ_only_at_the_end_stay_two_files(on_windows):
    root = Path("C:/Users/someone/Music/" + "deep/" * 20)
    extended = final_path(root, _long("B" * 115 + " (Extended Mix)"), "flac")
    radio = final_path(root, _long("B" * 115 + " (Radio Edit)"), "flac")
    assert extended != radio
    assert len(str(extended)) <= WINDOWS_MAX_PATH and len(str(radio)) <= WINDOWS_MAX_PATH


def test_the_same_long_title_always_cuts_to_the_same_name(on_windows):
    """A lossless upgrade recomputes the path and has to land where the first copy is."""
    root = Path("C:/Users/someone/Music/" + "deep/" * 20)
    assert final_path(root, _long("C" * 120), "flac").name == final_path(root, _long("C" * 120), "flac").name


def test_an_emoji_counts_twice_toward_the_windows_limit(on_windows):
    root = Path("C:/Users/someone/Music/Flackey")
    p = final_path(root, _long("\U0001f525" * 110), "flac")
    assert len(str(p).encode("utf-16-le")) // 2 <= WINDOWS_MAX_PATH


def test_a_short_windows_path_is_left_as_it_is(on_windows, tmp_path: Path):
    assert final_path(tmp_path, CT, "mp3").name == "Astral Projection - Into the Void.mp3"


def test_the_mac_never_cuts_a_long_title(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    root = Path("/Users/someone/Music/" + "deep/" * 20)
    assert final_path(root, _long("D" * 120), "flac").name == "Astral Projection - " + "D" * 120 + ".flac"


@pytest.mark.parametrize("layout", ["month", "day", "flat"])
def test_every_layout_fits_a_windows_path_to_max_path(on_windows, layout):
    root = Path("C:/Users/someone/Music/" + "deep/" * 20)
    p = final_path(root, _long("E" * 120), "flac", layout=layout, when=date(2026, 10, 6))
    assert len(str(p)) <= WINDOWS_MAX_PATH


def test_a_refiled_windows_path_is_cut_to_fit_its_folder(on_windows):
    folder = Path("C:/Users/someone/Music/" + "deep/" * 20 + "2026-09")
    p = refile_path(folder, _long("F" * 120), "flac")
    assert p.parent == folder and len(str(p)) <= WINDOWS_MAX_PATH
