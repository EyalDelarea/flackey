from pathlib import Path

import pytest

from flackey import tidy
from flackey.store import Store


def _file(s: Store, path: Path, artist: str = "Miranda") -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"audio")
    return s.add_track(path=path, fmt="aiff", bitrate_kbps=1411, cutoff_hz=21500, file_size=5, artist=artist,
                       title="T", mix_name="Original Mix", duration_s=300, isrc=None, catalog_track_id=None,
                       request_id=None)


def _confirm(groups) -> list[dict]:
    return [tidy.asdict(m) for g in groups for m in g.moves]


@pytest.fixture
def lib(tmp_path: Path):
    s = Store(tmp_path / "db.sqlite")
    root = tmp_path / "lib"
    root.mkdir()
    return s, root


def test_the_preview_only_looks(lib):
    s, root = lib
    _file(s, root / "Miranda!" / "Miranda! - A.aiff")
    _file(s, root / "Miranda!" / "Miranda! - B.aiff")
    t = _file(s, root / "Miranda" / "Miranda - C.aiff")
    groups = tidy.plan(s, root)
    assert [(g.folder, g.merges) for g in groups] == [("Miranda!", ["Miranda"])]
    assert [(m.track_id, Path(m.dest)) for m in groups[0].moves] == [(t, root / "Miranda!" / "Miranda - C.aiff")]
    assert (root / "Miranda" / "Miranda - C.aiff").exists()          # nothing moved by looking


def test_confirming_moves_exactly_the_preview_and_repoints_rows_and_playlists(lib):
    s, root = lib
    _file(s, root / "Miranda!" / "Miranda! - A.aiff")
    _file(s, root / "Miranda!" / "Miranda! - B.aiff")
    t = _file(s, root / "Miranda" / "Miranda - C.aiff")
    pid = s.upsert_playlist("https://example.com/p", "Set")
    s.add_playlist_track(pid, t, 0)
    out = tidy.apply(s, root, _confirm(tidy.plan(s, root)))
    assert out == {"moved": 1, "failed": []}
    new = root / "Miranda!" / "Miranda - C.aiff"
    assert new.exists() and not (root / "Miranda").exists()          # the emptied folder is gone
    assert s.get_track(t).path == new
    assert "Miranda!/Miranda - C.aiff" in (root / "Playlists" / "Set.m3u8").read_text()
    assert tidy.plan(s, root) == []


def test_a_changed_library_moves_nothing(lib):
    s, root = lib
    _file(s, root / "Miranda!" / "Miranda! - A.aiff")
    _file(s, root / "Miranda" / "Miranda - C.aiff")
    _file(s, root / "Miranda!" / "Miranda! - B.aiff")
    seen = _confirm(tidy.plan(s, root))
    _file(s, root / "Miranda" / "Miranda - D.aiff")             # a new file that the owner never saw
    with pytest.raises(tidy.PlanChanged):
        tidy.apply(s, root, seen)
    assert (root / "Miranda" / "Miranda - C.aiff").exists()
    with pytest.raises(tidy.PlanChanged):
        tidy.apply(s, root, [])                                        # nothing confirmed, nothing moved


def test_files_the_library_does_not_know_stay_and_keep_their_folder(lib):
    s, root = lib
    _file(s, root / "Miranda!" / "Miranda! - A.aiff")
    _file(s, root / "Miranda" / "Miranda - C.aiff")
    (root / "Miranda" / "cover.jpg").write_bytes(b"img")
    groups = tidy.plan(s, root)
    assert groups[0].left_behind == 1
    tidy.apply(s, root, _confirm(groups))
    assert (root / "Miranda" / "cover.jpg").exists()


def test_a_name_already_taken_is_left_alone_not_overwritten(lib):
    s, root = lib
    _file(s, root / "Miranda!" / "Miranda - C.aiff")
    _file(s, root / "Miranda!" / "Miranda! - A.aiff")
    _file(s, root / "Miranda" / "Miranda - C.aiff")
    groups = tidy.plan(s, root)
    assert groups[0].moves == [] and groups[0].conflicts == [str(root / "Miranda" / "Miranda - C.aiff")]


def test_a_leaked_track_number_comes_out_of_a_lone_folder(lib):
    s, root = lib
    t = _file(s, root / "02. Man With No Name" / "02. Man With No Name - Teleport.aiff")
    groups = tidy.plan(s, root)
    assert groups[0].folder == "Man With No Name"
    tidy.apply(s, root, _confirm(groups))
    assert s.get_track(t).path == root / "Man With No Name" / "Man With No Name - Teleport.aiff"
    assert not (root / "02. Man With No Name").exists()


def test_a_collaboration_folder_is_not_merged_into_a_member(lib):
    s, root = lib
    _file(s, root / "Duvdev" / "Duvdev - A.aiff")
    _file(s, root / "Duvdev & Shidapu" / "Duvdev & Shidapu - B.aiff")
    assert tidy.plan(s, root) == []


def test_a_date_layout_has_nothing_to_tidy(lib):
    s, root = lib
    _file(s, root / "Miranda!" / "Miranda! - A.aiff")
    _file(s, root / "Miranda" / "Miranda - C.aiff")
    assert tidy.plan(s, root, layout="month") == []
