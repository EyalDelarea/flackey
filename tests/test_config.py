import json
import shutil
import sys
from pathlib import Path

from flackey.config import (
    XDG_DATA_DIR,
    Settings,
    default_data_dir,
    load_settings,
    migrate_legacy_data_dir,
    save_settings,
)


def _env(tmp_path: Path, extra: str = "") -> Path:
    env = tmp_path / ".env"
    env.write_text(f"TELEGRAM_API_ID=123\nTELEGRAM_API_HASH=abc\nDATA_DIR={tmp_path / 'data'}\n{extra}")
    return env


def test_load_settings_from_env_file(tmp_path: Path):
    s = load_settings(_env(tmp_path, "LIBRARY_ROOT=~/Music/DJ Library\nWEB_PORT=9000\n"))
    assert s.telegram_api_id == 123 and s.web_port == 9000
    assert s.library_root == Path("~/Music/DJ Library").expanduser()
    assert s.data_dir == tmp_path / "data" and s.settings_path == tmp_path / "data" / "settings.json"


def test_default_data_dir_is_mac_native_on_darwin():
    if sys.platform == "darwin":
        assert default_data_dir() == Path("~/Library/Application Support/Flackey").expanduser()
    else:
        assert default_data_dir() == XDG_DATA_DIR.expanduser()


def test_credentials_are_optional(tmp_path: Path):
    env = tmp_path / ".env"
    env.write_text(f"DATA_DIR={tmp_path / 'data'}\n")
    s = load_settings(env)
    assert s.telegram_api_id is None and s.telegram_configured is False


def test_settings_file_fills_what_env_leaves_unset(tmp_path: Path):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "settings.json").write_text(json.dumps(
        {"library_root": str(tmp_path / "from-file"), "telegram_api_id": 999, "telegram_api_hash": "file"}))
    s = load_settings(_env(tmp_path))
    assert s.library_root == tmp_path / "from-file"           # only the file set it
    assert s.telegram_api_id == 123 and s.telegram_api_hash == "abc"  # env wins


def test_save_settings_writes_only_the_keys_it_was_given(tmp_path: Path):
    # telegram_api_id/telegram_api_hash here came only from the env (.env), never from settings.json;
    # saving the library folder must not silently copy them into the file.
    s = load_settings(_env(tmp_path, "WEB_PORT=9000\n"))
    out = save_settings(s, library_root=tmp_path / "new")
    assert out is s and s.library_root == tmp_path / "new"
    data = json.loads(s.settings_path.read_text())
    assert data == {"library_root": str(tmp_path / "new")}
    assert load_settings(_env(tmp_path)).library_root == tmp_path / "new"


def test_save_settings_preserves_keys_written_by_an_earlier_save(tmp_path: Path):
    s = load_settings(_env(tmp_path, "WEB_PORT=9000\n"))
    save_settings(s, telegram_api_id=999, telegram_api_hash="file-hash")
    save_settings(s, library_root=tmp_path / "new")
    data = json.loads(s.settings_path.read_text())
    assert data == {"telegram_api_id": 999, "telegram_api_hash": "file-hash", "library_root": str(tmp_path / "new")}


def test_web_host_defaults_to_loopback(tmp_path: Path):
    assert load_settings(_env(tmp_path)).web_host == "127.0.0.1"
    assert load_settings(_env(tmp_path, "WEB_HOST=0.0.0.0\n")).web_host == "0.0.0.0"


def _legacy(tmp_path: Path, monkeypatch, name: str = "legacy") -> tuple[Path, Path]:
    """A populated old-name data folder and the new folder it should end up in."""
    legacy, new = tmp_path / name, tmp_path / "new"
    (legacy / "spectrograms").mkdir(parents=True)
    (legacy / "krater.sqlite").write_text("old-db")        # previous name: renamed on the way
    (legacy / "krater.sqlite-wal").write_text("wal")
    (legacy / "krater.log").write_text("log")
    (legacy / "notes.txt").write_text("kept")              # no generation prefix: carried across as is
    (legacy / "spectrograms" / "1.png").write_bytes(b"png")
    monkeypatch.setattr("flackey.config.legacy_data_dirs", lambda: (legacy,))
    monkeypatch.setattr("flackey.config.default_data_dir", lambda: new)
    return legacy, new


def test_migrate_legacy_data_dir_copies_everything_then_removes_the_old_folder(tmp_path: Path, monkeypatch):
    legacy, new = _legacy(tmp_path, monkeypatch)
    s = Settings(_env_file=None, data_dir=new)
    assert migrate_legacy_data_dir(s) is True
    assert (new / "flackey.sqlite").read_text() == "old-db"   # renamed, and it is the same database
    assert (new / "flackey.sqlite-wal").read_text() == "wal"
    assert (new / "flackey.log").read_text() == "log"
    assert (new / "notes.txt").read_text() == "kept"          # untouched: it carries no generation prefix
    assert (new / "spectrograms" / "1.png").read_bytes() == b"png"
    assert not legacy.exists()
    assert migrate_legacy_data_dir(s) is False               # nothing left to move


def test_migrate_walks_every_legacy_folder_and_takes_the_first_that_exists(tmp_path: Path, monkeypatch):
    # Two renames are behind this project, so a machine can hold either old folder. The newest that
    # exists wins; an older one that also exists is not merged on top of it.
    newer, new = _legacy(tmp_path, monkeypatch, name="newer")
    older = tmp_path / "older"
    older.mkdir()
    (older / "cratedigger.sqlite").write_text("ancient")
    monkeypatch.setattr("flackey.config.legacy_data_dirs", lambda: (tmp_path / "absent", newer, older))
    assert migrate_legacy_data_dir(Settings(_env_file=None, data_dir=new)) is True
    assert (new / "flackey.sqlite").read_text() == "old-db"
    assert older.exists() and not newer.exists()


def test_migrate_is_skipped_when_data_dir_was_overridden(tmp_path: Path, monkeypatch):
    _legacy(tmp_path, monkeypatch)
    other = Settings(_env_file=None, data_dir=tmp_path / "custom")
    assert migrate_legacy_data_dir(other) is False           # DATA_DIR was set (Docker): leave it alone
    assert not (tmp_path / "custom").exists()


def test_a_failed_copy_keeps_the_old_folder_and_leaves_no_half_written_new_one(tmp_path: Path, monkeypatch):
    legacy, new = _legacy(tmp_path, monkeypatch)

    def boom(*_a, **_kw):
        raise OSError("disk full")

    monkeypatch.setattr("flackey.config.shutil.copytree", boom)
    s = Settings(_env_file=None, data_dir=new)
    assert migrate_legacy_data_dir(s) is False
    assert legacy.exists() and not new.exists()              # not crashed, not half-moved


def test_an_incomplete_copy_keeps_both_folders_rather_than_deleting_the_source(tmp_path: Path, monkeypatch):
    # The whole reason this copies instead of moving. If the verify cannot account for every file, the
    # owner is left with two folders and a log line -- never with the only copy deleted.
    legacy, new = _legacy(tmp_path, monkeypatch)
    real = shutil.copytree

    def truncating(src, dst, *a, **kw):
        # copytree recurses through the module global, so this wrapper sees the subdirectories too; only
        # the outermost call has finished copying and is the one to damage.
        out = real(src, dst, *a, **kw)
        if Path(dst) == new:
            (Path(out) / "krater.sqlite").write_text("")      # a short file the size check must catch
        return out

    monkeypatch.setattr("flackey.config.shutil.copytree", truncating)
    assert migrate_legacy_data_dir(Settings(_env_file=None, data_dir=new)) is False
    assert legacy.exists() and (legacy / "krater.sqlite").read_text() == "old-db"
    assert (new / "krater.sqlite").exists()                  # left verbatim, un-renamed, for inspection


def test_two_generations_of_the_same_database_are_never_collapsed_onto_one_name(tmp_path: Path, monkeypatch):
    # `krater.sqlite` and `cratedigger.sqlite` both want to become `flackey.sqlite`. `Path.rename`
    # overwrites silently on POSIX, so the older code lost one of them -- and worse, could pair one
    # database's `-wal` with the other's main file, which SQLite either refuses to open or reads as
    # corruption. The newest generation takes the name; the older keeps its own and is left for a human.
    legacy, new = _legacy(tmp_path, monkeypatch)
    (legacy / "cratedigger.sqlite").write_text("ancient-db")
    (legacy / "cratedigger.sqlite-wal").write_text("ancient-wal")
    assert migrate_legacy_data_dir(Settings(_env_file=None, data_dir=new)) is True
    assert (new / "flackey.sqlite").read_text() == "old-db"        # krater won: it is the newer generation
    assert (new / "flackey.sqlite-wal").read_text() == "wal"       # and its own wal came with it
    assert (new / "cratedigger.sqlite").read_text() == "ancient-db"    # not renamed, and not destroyed
    assert (new / "cratedigger.sqlite-wal").read_text() == "ancient-wal"


def test_the_app_bundle_is_not_carried_across(tmp_path: Path, monkeypatch):
    # It is rebuilt from the running venv on every launch and holds an absolute-path pyvenv.cfg, so a
    # copy is only a stale bundle under the wrong name.
    legacy, new = _legacy(tmp_path, monkeypatch)
    for older in ("Krater.app", "Cratedigger.app"):          # every generation's bundle, not only the newest
        (legacy / older / "Contents" / "MacOS").mkdir(parents=True)
        (legacy / older / "Contents" / "MacOS" / older[:-4]).write_text("exe")
    assert migrate_legacy_data_dir(Settings(_env_file=None, data_dir=new)) is True
    assert not (new / "Krater.app").exists() and not (new / "Cratedigger.app").exists()
    assert not (new / "Flackey.app").exists()


def test_settings_migrates_legacy_data_dir_before_creating_it(tmp_path: Path, monkeypatch):
    # Every `flackey` entry point (`status`, `export`, `add`, `login`, not just `start`) must migrate before
    # anything creates the new data_dir, or the migration becomes a permanent no-op the moment any of
    # them runs first.
    from flackey.cli import _settings, _state

    legacy, new = _legacy(tmp_path, monkeypatch)
    env = tmp_path / ".env"
    env.write_text(f"TELEGRAM_API_ID=1\nTELEGRAM_API_HASH=h\nLIBRARY_ROOT={tmp_path / 'lib'}\nDATA_DIR={new}\n")
    monkeypatch.setitem(_state, "env", env)

    assert not new.exists()  # the default-shaped data dir is absent before this call
    s = _settings()
    assert s.data_dir == new
    assert (new / "flackey.sqlite").read_text() == "old-db" and not legacy.exists()


def test_load_settings_ignores_a_settings_file_with_an_invalid_value(tmp_path: Path):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "settings.json").write_text(json.dumps({"library_root": 123}))
    s = load_settings(_env(tmp_path))
    assert s.library_root == Path("~/Music/DJ Library").expanduser()  # falls back to the base settings


import pytest
from pydantic import ValidationError


def test_lossless_is_off_until_an_api_key_is_set(tmp_path: Path):
    s = Settings(_env_file=None, data_dir=tmp_path)
    assert s.soulseek_enabled is False and s.lossless_enabled is False
    assert s.slskd_url == "http://127.0.0.1:5030"
    assert s.slskd_downloads == tmp_path / "slskd" / "downloads"
    assert s.lossless_raw_dir == tmp_path / "lossless" / "attempts"
    assert (s.lossless_filing_format, s.lossless_search_wait_s, s.lossless_first_byte_s, s.lossless_transfer_s,
            s.lossless_poll_s) == ("wav", 30, 60, 600, 2.0)
    assert (s.lossless_duration_tolerance_s, s.lossless_title_ratio, s.lossless_require_artist,
            s.lossless_max_queue, s.lossless_fingerprint_min, s.lossless_max_picks,
            s.lossless_keep_raw_days) == (3, 90, False, None, 0.90, 4, 30)
    on = Settings(_env_file=None, data_dir=tmp_path, slskd_api_key="k")
    assert on.soulseek_enabled is True and on.lossless_enabled is True


def test_downloads_dir_override_is_expanded_and_saved_as_text(tmp_path: Path):
    s = Settings(_env_file=None, data_dir=tmp_path / "data", slskd_downloads_dir="~/dl")
    assert s.slskd_downloads == Path("~/dl").expanduser()
    save_settings(s, slskd_downloads_dir=tmp_path / "other")
    data = json.loads(s.settings_path.read_text())
    assert data["slskd_downloads_dir"] == str(tmp_path / "other")


def test_save_settings_clears_downloads_dir_to_the_derived_default(tmp_path: Path):
    s = Settings(_env_file=None, data_dir=tmp_path / "data", slskd_downloads_dir="~/dl")
    save_settings(s, slskd_downloads_dir=None)
    assert s.slskd_downloads_dir is None
    data = json.loads(s.settings_path.read_text())
    assert data["slskd_downloads_dir"] is None
    assert s.slskd_downloads == tmp_path / "data" / "slskd" / "downloads"


def test_settings_file_supplies_slskd_keys(tmp_path: Path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "settings.json").write_text(json.dumps({"slskd_api_key": "file-key", "lossless_filing_format": "wav"}))
    env = tmp_path / ".env"
    env.write_text(f"DATA_DIR={data_dir}\n")
    s = load_settings(env)
    assert s.slskd_api_key == "file-key" and s.lossless_filing_format == "wav" and s.soulseek_enabled


def test_filing_format_is_validated(tmp_path: Path):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, data_dir=tmp_path, lossless_filing_format="mp3")


def test_load_settings_falls_back_to_the_self_generated_slskd_key(tmp_path: Path):
    from flackey.slskd_config import write_credentials

    data_dir = tmp_path / "data"
    generated = write_credentials(data_dir, "digger", "not-a-real-password")
    s = load_settings(_env(tmp_path))
    assert s.slskd_api_key == generated and s.soulseek_enabled is True


def test_load_settings_prefers_an_explicitly_configured_key_over_the_generated_one(tmp_path: Path):
    from flackey.slskd_config import write_credentials

    data_dir = tmp_path / "data"
    write_credentials(data_dir, "digger", "not-a-real-password")
    s = load_settings(_env(tmp_path, "SLSKD_API_KEY=explicitly-set-key\n"))
    assert s.slskd_api_key == "explicitly-set-key"


def test_load_settings_survives_no_slskd_config_at_all(tmp_path: Path):
    s = load_settings(_env(tmp_path))
    assert s.slskd_api_key is None and s.soulseek_enabled is False


def test_save_settings_leaves_settings_json_at_mode_0600(tmp_path: Path):
    s = load_settings(_env(tmp_path))
    save_settings(s, library_root=tmp_path / "new")
    assert s.settings_path.stat().st_mode & 0o777 == 0o600
    save_settings(s, telegram_api_id=1, telegram_api_hash="h")  # a later write stays 0600 too
    assert s.settings_path.stat().st_mode & 0o777 == 0o600


def test_the_source_toggle_survives_a_round_trip_through_the_settings_file(tmp_path: Path):
    # `source_enabled` is the first false-y settings-file key. `_read_settings_file` drops values that are
    # None or "", and False is neither -- but a later "simplify" to a truthiness check would silently drop
    # the toggle and quietly turn the Deezer bot back on.
    s = load_settings(_env(tmp_path))
    assert s.source_enabled is True
    save_settings(s, source_enabled=False)

    assert json.loads(s.settings_path.read_text())["source_enabled"] is False
    assert load_settings(_env(tmp_path)).source_enabled is False
