import json
import sys
from pathlib import Path

from flackey.config import (
    XDG_DATA_DIR,
    Settings,
    default_data_dir,
    load_settings,
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


def test_lossless_is_filed_as_aiff_so_rekordbox_can_read_the_tag(tmp_path: Path):
    """AIFF stays the default because Rekordbox's WAVE metadata path is RIFF INFO, not ID3/APIC artwork.
    WAV remains selectable for owners who want it, but AIFF is still the richest Rekordbox import path."""
    assert load_settings(_env(tmp_path)).lossless_filing_format == "aiff"


def test_web_host_defaults_to_loopback(tmp_path: Path):
    assert load_settings(_env(tmp_path)).web_host == "127.0.0.1"
    assert load_settings(_env(tmp_path, "WEB_HOST=0.0.0.0\n")).web_host == "0.0.0.0"


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
            s.lossless_poll_s) == ("aiff", 30, 60, 600, 2.0)
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


def test_the_window_size_survives_a_round_trip_through_the_settings_file(tmp_path: Path):
    # The only settings-file key that is not a scalar. JSON has no tuple, so it lands as a list and has to
    # come back as the pair `desktop.startup_size` unpacks -- and it has to stay unset until the owner has
    # actually resized the window, or a first launch would be pinned to whatever default wrote it.
    s = load_settings(_env(tmp_path))
    assert s.window_size is None
    save_settings(s, window_size=[880, 615])

    assert json.loads(s.settings_path.read_text())["window_size"] == [880, 615]
    assert load_settings(_env(tmp_path)).window_size == (880, 615)


def test_one_unreadable_key_does_not_take_the_rest_of_the_file_with_it(tmp_path: Path):
    # `Settings(**overrides)` reports every problem in one ValidationError, and answering that by falling
    # back to a Settings with no file values at all meant a single bad key silently reset the library
    # folder, the Telegram keys and the slskd key for that run -- with only a log line nobody sees in a
    # windowed app. `window_size` is the key that makes this routine: it is rewritten on every quit, so a
    # half-finished write or a hand edit is not the rare event a settings-screen submit is.
    s = load_settings(_env(tmp_path))
    s.settings_path.parent.mkdir(parents=True, exist_ok=True)
    s.settings_path.write_text(json.dumps({
        "window_size": ["wide", "tall"],
        "library_root": str(tmp_path / "survives"),
        "auto_update_check": False,
        "slskd_api_key": "kept",
    }))

    reloaded = load_settings(_env(tmp_path))
    assert reloaded.window_size is None            # the only value dropped
    assert reloaded.library_root == tmp_path / "survives"
    assert reloaded.auto_update_check is False
    assert reloaded.slskd_api_key == "kept"


def test_a_file_with_nothing_usable_in_it_falls_back_to_the_defaults(tmp_path: Path):
    # The other end of the same loop: when dropping the bad keys leaves nothing, the answer is the same
    # Settings the chain would have produced without the file, not an exception on the way to the window.
    s = load_settings(_env(tmp_path))
    s.settings_path.parent.mkdir(parents=True, exist_ok=True)
    s.settings_path.write_text(json.dumps({"window_size": ["wide", "tall"],
                                           "lossless_filing_format": "mp3"}))

    reloaded = load_settings(_env(tmp_path))
    assert reloaded.window_size is None
    assert reloaded.lossless_filing_format == "aiff"


def test_build_defaults_fill_telegram_keys_when_nothing_else_does(tmp_path: Path):
    env = tmp_path / ".env"
    env.write_text(f"DATA_DIR={tmp_path / 'data'}\n")
    build = tmp_path / "build.json"
    build.write_text(json.dumps({"telegram_api_id": 4242, "telegram_api_hash": "a" * 32}))
    s = load_settings(env, build_defaults=build)
    assert s.telegram_api_id == 4242 and s.telegram_api_hash == "a" * 32
    assert s.telegram_configured is True


def test_settings_file_and_env_beat_build_defaults(tmp_path: Path):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "settings.json").write_text(json.dumps({"telegram_api_hash": "from-file"}))
    build = tmp_path / "build.json"
    build.write_text(json.dumps({"telegram_api_id": 4242, "telegram_api_hash": "from-build"}))
    # _env(tmp_path) also sets TELEGRAM_API_HASH, which would beat the settings file and defeat the
    # point of this test (settings.json beating build.json for the hash); set only the id here.
    env = tmp_path / ".env"
    env.write_text(f"TELEGRAM_API_ID=123\nDATA_DIR={tmp_path / 'data'}\n")
    s = load_settings(env, build_defaults=build)
    assert s.telegram_api_id == 123 and s.telegram_api_hash == "from-file"


def test_build_defaults_are_ignored_when_missing_or_broken(tmp_path: Path):
    env = tmp_path / ".env"
    env.write_text(f"DATA_DIR={tmp_path / 'data'}\n")
    assert load_settings(env, build_defaults=tmp_path / "absent.json").telegram_api_id is None
    broken = tmp_path / "broken.json"
    broken.write_text("{not json")
    assert load_settings(env, build_defaults=broken).telegram_api_id is None
    other_keys = tmp_path / "other.json"
    other_keys.write_text(json.dumps({"web_port": 1, "telegram_api_id": "not-an-int"}))
    s = load_settings(env, build_defaults=other_keys)
    assert s.web_port == 8765 and s.telegram_api_id is None   # only the two telegram keys, and only valid ones


def test_build_defaults_never_land_in_settings_json(tmp_path: Path):
    env = tmp_path / ".env"
    env.write_text(f"DATA_DIR={tmp_path / 'data'}\n")
    build = tmp_path / "build.json"
    build.write_text(json.dumps({"telegram_api_id": 4242, "telegram_api_hash": "a" * 32}))
    s = load_settings(env, build_defaults=build)
    save_settings(s, library_root=tmp_path / "lib")
    assert json.loads(s.settings_path.read_text()) == {"library_root": str(tmp_path / "lib")}
