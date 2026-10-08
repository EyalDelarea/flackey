import sys
from pathlib import Path

import pytest
import yaml

from flackey.slskd_config import (
    SlskdConfigError,
    config_path,
    read_api_key,
    read_password,
    read_username,
    unsafe_share_reason,
    write_credentials,
    write_share,
)


def test_config_path_is_under_data_dir_slskd(tmp_path: Path):
    assert config_path(tmp_path) == tmp_path / "slskd" / "slskd.yml"


def test_fresh_write_produces_the_full_template_at_mode_0600(tmp_path: Path):
    key = write_credentials(tmp_path, "digger", "not-a-real-password")
    path = config_path(tmp_path)
    assert sys.platform == "win32" or path.stat().st_mode & 0o777 == 0o600

    data = yaml.safe_load(path.read_text())
    assert data["remote_configuration"] is False
    assert data["web"]["port"] == 5030
    assert data["web"]["ip_address"] == "127.0.0.1"
    assert data["web"]["https"]["disabled"] is True
    assert data["web"]["authentication"]["username"] == "flackey"
    assert data["web"]["authentication"]["password"]  # generated, non-empty
    assert data["web"]["authentication"]["api_keys"]["flackey"]["key"] == key
    assert data["web"]["authentication"]["api_keys"]["flackey"]["cidr"] == "127.0.0.1/32"
    assert data["soulseek"]["username"] == "digger"
    assert data["soulseek"]["password"] == "not-a-real-password"
    assert data["soulseek"]["listen_port"] == 50300
    assert data["directories"]["downloads"] == str(tmp_path / "slskd" / "downloads")
    assert data["directories"]["incomplete"] == str(tmp_path / "slskd" / "incomplete")
    assert len(key) >= 16  # slskd's own minimum


def test_https_is_turned_off_even_on_a_config_written_before_the_fix(tmp_path: Path):
    """`web.ip_address` binds the HTTP listener only; slskd's HTTPS listener stayed on 0.0.0.0 and
    answered from the LAN on 5031. A config written by the older code has no `https` section at all, so
    this must be forced on every write rather than filled in only when missing."""
    path = config_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(yaml.safe_dump({"web": {"port": 5030, "ip_address": "127.0.0.1"},
                                    "soulseek": {"username": "old", "password": "old-fake"}}))
    write_credentials(tmp_path, "digger", "not-a-real-password")
    assert yaml.safe_load(path.read_text())["web"]["https"]["disabled"] is True


def test_second_write_preserves_generated_secrets_and_other_keys(tmp_path: Path):
    key1 = write_credentials(tmp_path, "digger", "not-a-real-password")
    path = config_path(tmp_path)
    before = yaml.safe_load(path.read_text())
    web_password_before = before["web"]["authentication"]["password"]

    key2 = write_credentials(tmp_path, "someone-else", "another-fake-password")
    assert key2 == key1
    assert sys.platform == "win32" or path.stat().st_mode & 0o777 == 0o600  # the rewrite path stays 0600 too, not just the first write

    after = yaml.safe_load(path.read_text())
    assert after["web"]["authentication"]["password"] == web_password_before
    assert after["web"]["authentication"]["api_keys"]["flackey"]["key"] == key1
    assert after["web"]["port"] == 5030
    assert after["soulseek"]["listen_port"] == 50300
    assert after["directories"]["downloads"] == str(tmp_path / "slskd" / "downloads")
    assert after["directories"]["incomplete"] == str(tmp_path / "slskd" / "incomplete")
    assert after["soulseek"]["username"] == "someone-else"
    assert after["soulseek"]["password"] == "another-fake-password"


def test_write_fills_missing_secrets_on_a_hand_written_file(tmp_path: Path):
    path = config_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("soulseek:\n  username: old\n")
    path.chmod(0o644)  # a hand-written file is not 0600; the rewrite must still end up 0600
    key = write_credentials(tmp_path, "digger", "not-a-real-password")
    assert sys.platform == "win32" or path.stat().st_mode & 0o777 == 0o600
    data = yaml.safe_load(path.read_text())
    assert data["web"]["authentication"]["password"]
    assert data["web"]["authentication"]["api_keys"]["flackey"]["key"] == key


def test_write_fills_missing_secrets_when_web_section_is_a_bare_key(tmp_path: Path):
    # `web:` with nothing under it parses to `web: None` -- must still land on the 400/raise path
    # cleanly rather than crashing with an AttributeError deep inside dict.setdefault.
    path = config_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("web:\nsoulseek:\n  username: old\n")
    key = write_credentials(tmp_path, "digger", "not-a-real-password")
    data = yaml.safe_load(path.read_text())
    assert data["web"]["authentication"]["api_keys"]["flackey"]["key"] == key


@pytest.mark.parametrize("content", ["- a\n- list\n", "just a string\n"])
def test_a_config_that_parses_to_a_non_mapping_raises(tmp_path: Path, content):
    path = config_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(content)
    with pytest.raises(SlskdConfigError):
        write_credentials(tmp_path, "digger", "not-a-real-password")


def test_empty_username_raises_and_message_has_no_password(tmp_path: Path):
    with pytest.raises(SlskdConfigError) as ei:
        write_credentials(tmp_path, "", "not-a-real-password")
    assert "not-a-real-password" not in str(ei.value)


def test_whitespace_only_username_raises_and_message_has_no_username_or_password(tmp_path: Path):
    with pytest.raises(SlskdConfigError) as ei:
        write_credentials(tmp_path, "   ", "not-a-real-password")
    assert "   " not in str(ei.value)
    assert "not-a-real-password" not in str(ei.value)


def test_empty_password_raises_and_message_has_no_username(tmp_path: Path):
    with pytest.raises(SlskdConfigError) as ei:
        write_credentials(tmp_path, "a-real-looking-username", "")
    assert "a-real-looking-username" not in str(ei.value)


def test_read_api_key_returns_none_for_missing_and_malformed_config(tmp_path: Path):
    assert read_api_key(tmp_path) is None
    path = config_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("not: [valid, yaml, :::\n")
    assert read_api_key(tmp_path) is None
    path.write_text("- a\n- list\n")
    assert read_api_key(tmp_path) is None
    path.write_text("web:\n  authentication: {}\n")
    assert read_api_key(tmp_path) is None


def test_read_username_returns_none_then_the_username_after_a_write(tmp_path: Path):
    assert read_username(tmp_path) is None
    write_credentials(tmp_path, "digger", "not-a-real-password")
    assert read_username(tmp_path) == "digger"


def test_read_password_returns_none_for_missing_malformed_and_passwordless_configs(tmp_path: Path):
    """The one reader that hands a secret back still has to fail like the others: an absent, unparseable
    or half-written config is an ordinary state on the way to a first setup, not an error to raise from."""
    assert read_password(tmp_path) is None
    path = config_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("not: [valid, yaml, :::\n")
    assert read_password(tmp_path) is None
    path.write_text("- a\n- list\n")
    assert read_password(tmp_path) is None
    path.write_text("soulseek:\n  username: digger\n")
    assert read_password(tmp_path) is None
    path.write_text("soulseek:\n  username: digger\n  password: ''\n")
    assert read_password(tmp_path) is None


def test_read_password_returns_what_was_written(tmp_path: Path):
    """Soulseek has no password reset, so an owner who cannot read this string back has lost the account.
    That is the whole reason this reader exists."""
    write_credentials(tmp_path, "digger", "not-a-real-password")
    assert read_password(tmp_path) == "not-a-real-password"


def test_credentials_write_shares_the_library_folder(tmp_path: Path):
    write_credentials(tmp_path, "digger", "not-a-real-password",
                      library_root=tmp_path / "DJ Library")
    data = yaml.safe_load(config_path(tmp_path).read_text())
    assert data["shares"]["directories"] == [str(tmp_path / "DJ Library")]


def test_credentials_write_keeps_a_hand_written_share_and_adds_the_library(tmp_path: Path):
    path = config_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(yaml.safe_dump({"shares": {"directories": ["/Volumes/Extra/Music"]}}))
    write_credentials(tmp_path, "digger", "not-a-real-password",
                      library_root=tmp_path / "DJ Library")
    data = yaml.safe_load(path.read_text())
    assert data["shares"]["directories"] == ["/Volumes/Extra/Music", str(tmp_path / "DJ Library")]


def test_write_share_moves_the_share_with_the_library_folder(tmp_path: Path):
    from flackey.slskd_config import write_share
    old, new = tmp_path / "old", tmp_path / "new"
    write_credentials(tmp_path, "digger", "not-a-real-password", library_root=old)
    assert write_share(tmp_path, new, previous=old) is True
    data = yaml.safe_load(config_path(tmp_path).read_text())
    assert data["shares"]["directories"] == [str(new)]
    assert data["soulseek"]["username"] == "digger"           # nothing else touched
    assert sys.platform == "win32" or config_path(tmp_path).stat().st_mode & 0o777 == 0o600
    assert write_share(tmp_path, new, previous=old) is True   # idempotent
    assert yaml.safe_load(config_path(tmp_path).read_text())["shares"]["directories"] == [str(new)]


def test_write_share_does_nothing_without_a_config(tmp_path: Path):
    from flackey.slskd_config import write_share
    assert write_share(tmp_path, tmp_path / "lib") is False
    assert not config_path(tmp_path).exists()


@pytest.mark.parametrize("where", ["home", "above-home", "data-dir", "above-data-dir"])
def test_a_share_that_would_hold_home_or_flackeys_data_is_refused(tmp_path: Path, monkeypatch, where):
    """Shared with the whole Soulseek network: never the home folder, or the folder with the Telegram session."""
    home = tmp_path / "home"
    data = home / "Library" / "Flackey"
    data.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))  # what Path.home() reads on Windows
    write_credentials(data, "dj", "pw", library_root=home / "Music")
    folder = {"home": home, "above-home": tmp_path, "data-dir": data, "above-data-dir": home / "Library"}[where]

    assert unsafe_share_reason(folder, data)
    with pytest.raises(SlskdConfigError):
        write_share(data, folder)
    assert yaml.safe_load(config_path(data).read_text())["shares"]["directories"] == [str(home / "Music")]


def test_a_music_folder_is_a_fine_share(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    assert unsafe_share_reason(tmp_path / "Music" / "DJ Library", tmp_path / "Library" / "Flackey") is None


def test_a_windows_library_path_survives_the_yaml_round_trip(tmp_path: Path, monkeypatch):
    """A Windows path is full of backslashes -- `C:\\Users\\...` holds a `\\U`, which a hand-built
    double-quoted YAML string would read as a unicode escape and reject. The file is written by
    `yaml.safe_dump`, which quotes whatever needs it, so the share slskd reads is the folder that was
    given, character for character, including one outside ASCII."""
    from flackey.slskd_config import read_listen_port, write_share
    # Off Windows the path is relative and resolves against the working directory, which in a checkout
    # under a hidden folder (a git worktree in ~/.something) the share guard would rightly refuse.
    monkeypatch.chdir(tmp_path)
    windows = "C:\\Users\\Ünal\\Music\\DJ Library"
    write_credentials(tmp_path, "digger", "not-a-real-password", library_root=Path(windows))
    assert yaml.safe_load(config_path(tmp_path).read_text(encoding="utf-8"))["shares"]["directories"] == [windows]
    moved = "D:\\Müzik\\new\\x"
    assert write_share(tmp_path, Path(moved), previous=Path(windows)) is True
    assert yaml.safe_load(config_path(tmp_path).read_text(encoding="utf-8"))["shares"]["directories"] == [moved]
    assert read_listen_port(tmp_path) == 50300


def test_an_undecodable_config_reads_as_absent_rather_than_crashing_startup(tmp_path: Path):
    """`read_listen_port` and `read_api_key` run while the app starts; a file that is not UTF-8 (a hand
    edit saved in a Windows code page) must read as "no config", not raise out of `app._run`."""
    from flackey.slskd_config import SOULSEEK_LISTEN_PORT, read_listen_port
    path = config_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"soulseek:\n  listen_port: 1234\n  username: \xff\xfe\x81\n")
    assert read_listen_port(tmp_path) == SOULSEEK_LISTEN_PORT
    assert read_api_key(tmp_path) is None


@pytest.mark.parametrize("raw", [r"\\nas\music", "//nas/music", r"\\?\C:\Music", r"\\nas\music\DJ Library"])
def test_a_network_path_is_never_shared(raw):
    """Checked on the string, before anything resolves it: resolving an unreachable server can hang."""
    assert unsafe_share_reason(Path(raw), Path("/nowhere/data"))


@pytest.mark.parametrize("raw", [r"\\nas\music", "//nas/music", r"\\?\UNC\nas\music"])
def test_is_network_path_reads_both_spellings(raw):
    from flackey.slskd_config import is_network_path
    assert is_network_path(raw)


@pytest.mark.parametrize("raw", ["/Volumes/X/Music", "D:\\Music", "~/Music/DJ Library", "/Users/a/Music"])
def test_local_paths_are_not_network_paths(raw):
    from flackey.slskd_config import is_network_path
    assert not is_network_path(raw)


@pytest.fixture
def fake_home(tmp_path: Path, monkeypatch) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))  # what Path.home() reads on Windows
    return home


@pytest.mark.parametrize("inside", [".ssh", ".config/flackey", ".gnupg/private", "Music/.hidden/stuff",
                                    "Library", "Library/Keychains", "library/Mail", "AppData",
                                    "AppData/Roaming/Telegram Desktop", "appdata/Local/Google/Chrome"])
def test_hidden_and_app_support_folders_are_never_shared(tmp_path: Path, fake_home: Path, inside):
    """Dot-folders hold keys and tokens; ~/Library and AppData hold every app's sign-ins. Matched without
    regard to case, because APFS and NTFS both ignore it."""
    assert unsafe_share_reason(fake_home / inside, tmp_path / "data")


@pytest.mark.parametrize("inside", ["Music/DJ Library", "Library/CloudStorage/Dropbox/DJ",
                                    "Library/Mobile Documents/com~apple~CloudDocs/DJ", "Music.Library",
                                    "Libraryish/Music"])
def test_ordinary_music_folders_including_cloud_drives_stay_allowed(tmp_path: Path, fake_home: Path, inside):
    """Dropbox, OneDrive and Google Drive live under ~/Library/CloudStorage on a Mac, and iCloud Drive
    under ~/Library/Mobile Documents: a DJ library there is a normal choice."""
    assert unsafe_share_reason(fake_home / inside, tmp_path / "data") is None


def test_an_external_drive_is_a_fine_share(tmp_path: Path, fake_home: Path):
    assert unsafe_share_reason(tmp_path / "Volumes" / "X" / "Music", tmp_path / "data") is None


def test_the_temp_folder_inside_appdata_is_not_refused(tmp_path: Path, fake_home: Path, monkeypatch):
    """Windows keeps the temp folder under AppData\\Local. Nothing there is a sign-in, and refusing it
    would refuse every scratch folder a test or a tool hands in."""
    import tempfile
    temp = fake_home / "AppData" / "Local" / "Temp"
    temp.mkdir(parents=True)
    monkeypatch.setattr(tempfile, "tempdir", str(temp))
    assert unsafe_share_reason(temp / "scratch" / "lib", tmp_path / "data") is None
    assert unsafe_share_reason(fake_home / "AppData" / "Local" / "Other", tmp_path / "data")


def test_the_listener_and_api_key_are_pinned_to_this_machine_on_every_write(tmp_path: Path):
    """A hand-edited file that opened the web UI to the network, allowed the key from anywhere, or turned
    remote configuration on is put back on the next write -- forced, like `https.disabled`."""
    path = config_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(yaml.safe_dump({
        "remote_configuration": True,
        "web": {"ip_address": "0.0.0.0", "authentication": {
            "api_keys": {"flackey": {"key": "k" * 32, "cidr": "0.0.0.0/0,::/0"}}}},
        "soulseek": {"username": "old", "password": "old-fake"}}))

    write_credentials(tmp_path, "digger", "not-a-real-password")
    data = yaml.safe_load(path.read_text())
    assert data["remote_configuration"] is False
    assert data["web"]["ip_address"] == "127.0.0.1"
    assert data["web"]["authentication"]["api_keys"]["flackey"] == {"key": "k" * 32, "cidr": "127.0.0.1/32"}

    data["remote_configuration"] = True
    data["web"]["ip_address"] = "0.0.0.0"
    data["web"]["authentication"]["api_keys"]["flackey"]["cidr"] = "0.0.0.0/0"
    path.write_text(yaml.safe_dump(data))
    assert write_share(tmp_path, tmp_path / "lib") is True
    data = yaml.safe_load(path.read_text())
    assert data["remote_configuration"] is False
    assert data["web"]["ip_address"] == "127.0.0.1"
    assert data["web"]["authentication"]["api_keys"]["flackey"]["cidr"] == "127.0.0.1/32"
    assert data["web"]["https"]["disabled"] is True


def test_a_refused_share_still_pins_the_listener_and_key(tmp_path: Path, fake_home: Path):
    data = tmp_path / "data"
    write_credentials(data, "dj", "pw", library_root=fake_home / "Music")
    path = config_path(data)
    config = yaml.safe_load(path.read_text())
    config["web"]["ip_address"] = "0.0.0.0"
    config["web"]["authentication"]["api_keys"]["flackey"]["cidr"] = "0.0.0.0/0"
    config["remote_configuration"] = True
    path.write_text(yaml.safe_dump(config))

    with pytest.raises(SlskdConfigError):
        write_share(data, fake_home / ".ssh")

    after = yaml.safe_load(path.read_text())
    assert after["shares"]["directories"] == [str(fake_home / "Music")]
    assert after["web"]["ip_address"] == "127.0.0.1" and after["remote_configuration"] is False
    assert after["web"]["authentication"]["api_keys"]["flackey"]["cidr"] == "127.0.0.1/32"


def test_a_share_write_adds_no_api_key_to_a_file_without_one(tmp_path: Path):
    path = config_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(yaml.safe_dump({"soulseek": {"username": "old", "password": "old-fake"}}))
    write_share(tmp_path, tmp_path / "lib")
    data = yaml.safe_load(path.read_text())
    assert "authentication" not in data["web"]
    assert data["web"]["ip_address"] == "127.0.0.1" and data["remote_configuration"] is False


def _hidden(filters: list[str], path: Path) -> bool:
    """What slskd 0.26.0 does with `shares.filters` (Shares/ShareScanner.cs): each one is a regex, matched
    unanchored and without regard to case against the full local path of every directory and file."""
    import re
    return any(re.search(f, str(path), re.IGNORECASE) for f in filters)


def test_playlist_exports_are_never_shared(tmp_path: Path):
    """A playlist names every track by its full path, the owner's user name included, so it stays off the
    network: the library's Playlists folder and any .m3u/.m3u8 file are filtered out of the share."""
    lib = tmp_path / "DJ Library"
    write_credentials(tmp_path, "digger", "not-a-real-password", library_root=lib)
    filters = yaml.safe_load(config_path(tmp_path).read_text())["shares"]["filters"]
    assert _hidden(filters, lib / "Playlists")
    assert _hidden(filters, lib / "Playlists" / "Goa Set.m3u8")
    assert _hidden(filters, lib / "Artist" / "old.M3U")
    assert not _hidden(filters, lib / "Artist" / "Artist - Track.flac")
    assert not _hidden(filters, lib / "Artist")
    assert not _hidden(filters, lib)


def test_the_playlist_folder_filter_is_anchored_to_the_library(tmp_path: Path):
    """A library that itself sits under some other folder named Playlists is still shared."""
    lib = tmp_path / "Playlists" / "DJ Library"
    write_credentials(tmp_path, "digger", "not-a-real-password", library_root=lib)
    filters = yaml.safe_load(config_path(tmp_path).read_text())["shares"]["filters"]
    assert not _hidden(filters, lib)
    assert not _hidden(filters, lib / "Artist" / "Artist - Track.flac")
    assert _hidden(filters, lib / "Playlists" / "Set.m3u8")


def test_a_config_written_by_an_older_version_gains_the_filters(tmp_path: Path):
    lib = tmp_path / "lib"
    path = config_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(yaml.safe_dump({"soulseek": {"username": "old", "password": "old-fake"},
                                    "shares": {"directories": [str(lib)], "filters": ["\\.ini$"]}}))
    assert write_share(tmp_path, lib) is True
    filters = yaml.safe_load(path.read_text())["shares"]["filters"]
    assert filters[0] == "\\.ini$"                                  # the owner's own filter stays
    assert _hidden(filters, lib / "Playlists" / "Set.m3u8") and _hidden(filters, lib / "Playlists")


def test_the_filters_are_forced_on_every_write_and_follow_the_library(tmp_path: Path):
    old, new = tmp_path / "old", tmp_path / "new"
    write_credentials(tmp_path, "digger", "not-a-real-password", library_root=old)
    path = config_path(tmp_path)
    data = yaml.safe_load(path.read_text())
    data["shares"]["filters"] = []                                  # removed by hand
    path.write_text(yaml.safe_dump(data))
    write_credentials(tmp_path, "digger", "not-a-real-password")    # a write with no library named
    assert _hidden(yaml.safe_load(path.read_text())["shares"]["filters"], old / "a.m3u8")
    assert write_share(tmp_path, new, previous=old) is True
    assert write_share(tmp_path, new, previous=old) is True         # idempotent
    filters = yaml.safe_load(path.read_text())["shares"]["filters"]
    assert len(filters) == len(set(filters)) == 2
    assert _hidden(filters, new / "Playlists") and not _hidden(filters, old / "Playlists")
