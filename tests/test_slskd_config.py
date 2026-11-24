import re
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


@pytest.mark.parametrize("inside", ["Desktop", "Documents", "Downloads", "downloads"])
def test_the_desktop_documents_and_downloads_folders_themselves_are_never_shared(tmp_path: Path,
                                                                                fake_home: Path, inside):
    """They hold everything else the owner keeps. A music folder made inside one is fine."""
    assert unsafe_share_reason(fake_home / inside, tmp_path / "data")
    assert unsafe_share_reason(fake_home / inside / "DJ Library", tmp_path / "data") is None


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


def test_os_and_app_clutter_is_never_shared(tmp_path: Path, fake_home: Path):
    """Dot-files carry metadata, not music: an AppleDouble `._x.flac` holds the URL the file came from.
    Matched on the last part only, so a library under some dot-folder on a drive is still shared. (A fake
    home, because on Windows the real temp folder sits inside it, where a dot-folder is refused.)"""
    lib = tmp_path / ".drive" / "DJ Library"
    write_credentials(tmp_path, "digger", "not-a-real-password", library_root=lib)
    filters = yaml.safe_load(config_path(tmp_path).read_text())["shares"]["filters"]
    for junk in [".DS_Store", "Artist/._Artist - Track.flac", ".git", ".git/config", "Artist/Thumbs.db",
                 "Artist/desktop.ini", "Artist/.syncthing.Track.flac.tmp"]:
        assert _hidden(filters, lib / junk) or _hidden(filters, (lib / junk).parent), junk
    assert not _hidden(filters, lib)
    assert not _hidden(filters, lib / "Artist")
    assert not _hidden(filters, lib / "Artist" / "Artist - Track.flac")
    assert not _hidden(filters, lib / "Artist" / "Mr. Thumbs.db.flac")


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
    assert len(filters) == len(set(filters)) == 5                   # three forced, two for the library
    assert _hidden(filters, new / "Playlists") and not _hidden(filters, old / "Playlists")
    assert _hidden(filters, new / ".git" / "x") and not _hidden(filters, old / ".git" / "x")


# SEC-1 (audit 2026-11-20): a whole drive, the folder that lists the drives, or a system folder is never
# the library. The rules are read off the path's parts, so both platforms' rules run on every OS.
@pytest.mark.parametrize("raw", ["/", "/Volumes", "/volumes", "/Volumes/Backup", "/Volumes/My Passport",
                                 "/etc", "/etc/ssh", "/System", "/System/Library", "/Library",
                                 "/Library/Keychains", "/usr", "/usr/local", "/usr/local/music", "/bin",
                                 "/sbin", "/private", "/private/etc", "/private/var/db", "/Applications",
                                 "/Applications/Flackey.app", "/opt", "/opt/homebrew", "/dev", "/cores",
                                 "/var", "/var/root", "/Users/Shared", "/tmp"])
def test_mac_system_folders_and_whole_drives_are_never_shared(raw):
    from pathlib import PurePosixPath

    from flackey.slskd_config import system_folder_reason
    assert system_folder_reason(PurePosixPath(raw), windows=False, case_sensitive=False)


@pytest.mark.parametrize("raw", ["C:\\", "D:\\", "c:\\", "C:\\Windows", "C:\\Windows\\System32",
                                 "C:\\windows\\temp\\x", "C:\\Program Files", "C:\\Program Files\\Flackey",
                                 "C:\\Program Files (x86)", "C:\\ProgramData", "C:\\ProgramData\\Microsoft",
                                 "D:\\$Recycle.Bin", "D:\\System Volume Information", "C:\\Users\\Public",
                                 "C:\\Recovery"])
def test_windows_system_folders_and_drive_roots_are_never_shared(raw):
    from pathlib import PureWindowsPath

    from flackey.slskd_config import system_folder_reason
    assert system_folder_reason(PureWindowsPath(raw), windows=True, case_sensitive=False)


@pytest.mark.parametrize("raw", ["/Volumes/Backup/Music", "/Volumes/X/DJ Library/House", "/Users/a/Music",
                                 "/Users/Shared/Music", "/library", "/music", "/data/music", "/srv/music",
                                 "/mnt/usb/Music", "/home/dj/Music", "/Systemish", "/Volumes2/x"])
def test_ordinary_posix_music_folders_stay_allowed(raw):
    """`/library` is where the Docker image mounts the library; an external drive is fine one folder down."""
    from pathlib import PurePosixPath

    from flackey.slskd_config import system_folder_reason
    assert system_folder_reason(PurePosixPath(raw), windows=False, case_sensitive=True) is None
    if raw != "/library":   # on Linux `/library` and `/Library` are two different folders
        assert system_folder_reason(PurePosixPath(raw), windows=False, case_sensitive=False) is None


def test_linux_tells_library_and_Library_apart():
    """Docker mounts the library at `/library`. On a Mac, whose disk ignores case, that is `/Library`."""
    from pathlib import PurePosixPath

    from flackey.slskd_config import system_folder_reason
    assert system_folder_reason(PurePosixPath("/library"), windows=False, case_sensitive=True) is None
    assert system_folder_reason(PurePosixPath("/Library"), windows=False, case_sensitive=True)
    assert system_folder_reason(PurePosixPath("/library"), windows=False, case_sensitive=False)


@pytest.mark.parametrize("raw", ["D:\\Music", "E:\\DJ\\Library", "C:\\Users\\dj\\Music",
                                 "C:\\Users\\Public\\Music", "D:\\Windowsish\\x", "C:\\Programs\\Music"])
def test_ordinary_windows_music_folders_stay_allowed(raw):
    from pathlib import PureWindowsPath

    from flackey.slskd_config import system_folder_reason
    assert system_folder_reason(PureWindowsPath(raw), windows=True, case_sensitive=False) is None


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX paths")
@pytest.mark.parametrize("raw", ["/", "/Volumes", "/Volumes/Backup", "/etc", "/usr/local", "/System",
                                 "/Library", "/private", "/Applications", "/bin"])
def test_unsafe_share_reason_refuses_whole_drives_and_system_folders(fake_home: Path, raw):
    assert unsafe_share_reason(Path(raw), Path("/nowhere/data"))


@pytest.mark.skipif(sys.platform != "darwin", reason="/Volumes is a Mac folder")
def test_unsafe_share_reason_allows_a_folder_on_an_external_drive(fake_home: Path):
    assert unsafe_share_reason(Path("/Volumes/Backup/Music"), Path("/nowhere/data")) is None


@pytest.mark.skipif(sys.platform != "win32", reason="Windows paths")
@pytest.mark.parametrize("raw", ["C:\\", "D:\\", "C:\\Windows", "C:\\Program Files", "C:\\ProgramData"])
def test_unsafe_share_reason_refuses_windows_drive_roots_and_system_folders(fake_home: Path, raw):
    assert unsafe_share_reason(Path(raw), Path("C:\\nowhere\\data"))


def test_the_home_folder_itself_is_never_shared(tmp_path: Path, fake_home: Path):
    assert unsafe_share_reason(fake_home, tmp_path / "data")
    assert unsafe_share_reason(fake_home / "Music", tmp_path / "data") is None


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX paths")
def test_choosing_a_whole_drive_is_refused_and_leaves_the_share_as_it_was(tmp_path: Path, fake_home: Path):
    data = tmp_path / "data"
    write_credentials(data, "dj", "pw", library_root=fake_home / "Music")

    with pytest.raises(SlskdConfigError):
        write_share(data, Path("/Volumes/MyDrive"), previous=fake_home / "Music")

    assert yaml.safe_load(config_path(data).read_text())["shares"]["directories"] == [str(fake_home / "Music")]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX paths")
@pytest.mark.parametrize("saved_folder", ["/Volumes/MyDrive", "/Users/Shared"])
def test_a_saved_whole_drive_keeps_being_shared_with_a_warning(tmp_path: Path, fake_home: Path, caplog,
                                                              saved_folder):
    """A folder saved before the whole-drive and system-folder rules existed is not quietly dropped from
    the share by an update: the startup repair keeps sharing it and logs why it would be refused now."""
    data = tmp_path / "data"
    write_credentials(data, "dj", "pw")
    caplog.set_level("WARNING")

    assert write_share(data, Path(saved_folder), saved=True) is True

    assert yaml.safe_load(config_path(data).read_text())["shares"]["directories"] == [saved_folder]
    assert "would not be accepted" in caplog.text


def test_a_saved_folder_is_still_held_to_the_older_rules(tmp_path: Path, fake_home: Path):
    """Only the new rules are relaxed for a saved folder: the home folder and hidden folders never are."""
    data = tmp_path / "data"
    write_credentials(data, "dj", "pw", library_root=fake_home / "Music")
    for folder in (fake_home, fake_home / ".ssh"):
        with pytest.raises(SlskdConfigError):
            write_share(data, folder, saved=True)
    assert yaml.safe_load(config_path(data).read_text())["shares"]["directories"] == [str(fake_home / "Music")]


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX paths")
def test_re_entering_credentials_keeps_a_grandfathered_share(tmp_path: Path, fake_home: Path, caplog):
    """The share folder does not change when the owner signs in to Soulseek again, so a whole drive that
    was already shared is not refused (which would 400 the setup) -- only warned about."""
    data = tmp_path / "data"
    write_credentials(data, "dj", "pw")
    write_share(data, Path("/Volumes/MyDrive"), saved=True)     # shared before the new rules
    caplog.set_level("WARNING")

    write_credentials(data, "dj2", "pw2", library_root=Path("/Volumes/MyDrive"))

    after = yaml.safe_load(config_path(data).read_text())
    assert after["shares"]["directories"] == ["/Volumes/MyDrive"] and after["soulseek"]["username"] == "dj2"
    assert "would not be accepted" in caplog.text


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX paths")
def test_first_soulseek_setup_on_a_whole_drive_is_refused(tmp_path: Path, fake_home: Path):
    """Setting Soulseek up is when the folder first gets shared, so the new rules apply."""
    with pytest.raises(SlskdConfigError):
        write_credentials(tmp_path / "data", "dj", "pw", library_root=Path("/Volumes/MyDrive"))


# Security review 2026-11-24, M1: a Mac's disk ignores letter case, but `resolve` keeps the case it was
# given, so the home folder typed in another case used to pass as an ordinary folder.
@pytest.mark.skipif(sys.platform != "darwin", reason="the Mac's disk ignores case")
def test_the_real_home_folder_in_another_case_is_never_shared(tmp_path: Path):
    home = str(Path.home())
    for raw in (home.lower(), home.upper(), home.swapcase(), "/users", "/USERS", "/" + home.split("/")[1].lower()):
        assert unsafe_share_reason(Path(raw), tmp_path / "data"), raw
    assert unsafe_share_reason(Path(home.lower()) / "Music" / "DJ Library", tmp_path / "data") is None


@pytest.mark.parametrize("spell", [str.lower, str.upper, str.swapcase])
def test_home_and_data_folders_in_another_case_are_refused_where_the_disk_ignores_case(
        tmp_path: Path, fake_home: Path, monkeypatch, spell):
    """Run on every OS by telling the check the disk ignores case: where the typed spelling does not exist
    (Linux), the case-folded parts decide; where it does (a Mac), the disk itself says it is the same."""
    import flackey.slskd_config as sc
    monkeypatch.setattr(sc, "CASE_INSENSITIVE_DISK", True)
    data = fake_home / "Library" / "Flackey"
    data.mkdir(parents=True)
    for folder in (fake_home, fake_home.parent, data, data.parent):
        assert unsafe_share_reason(Path(spell(str(folder))), data), spell(str(folder))
    other = tmp_path / "elsewhere"
    assert unsafe_share_reason(Path(spell(str(other))), Path(spell(str(other.parent)) + "-data")) is None


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="a case-sensitive disk")
def test_linux_does_not_treat_another_spelling_as_the_home_folder(tmp_path: Path, fake_home: Path):
    """On a disk that counts case, `/HOME/x` is not `/home/x`: no folder by that name, no refusal for it."""
    assert unsafe_share_reason(Path(str(fake_home).swapcase()), tmp_path / "data") is None


# L1: a saved library folder the rules refuse stops being shared, instead of the startup repair leaving it.
@pytest.mark.parametrize("refused", ["home", "hidden"])
def test_a_saved_library_the_rules_refuse_is_taken_out_of_the_share(tmp_path: Path, fake_home: Path, refused):
    data = tmp_path / "data"
    folder = {"home": fake_home, "hidden": fake_home / ".ssh"}[refused]
    write_credentials(data, "dj", "pw")
    path = config_path(data)
    config = yaml.safe_load(path.read_text())
    config["shares"] = {"directories": ["/Volumes/Extra/Music", str(folder)], "filters": []}
    path.write_text(yaml.safe_dump(config))     # shared by a build that let it through

    with pytest.raises(SlskdConfigError):
        write_share(data, folder, saved=True)

    after = yaml.safe_load(path.read_text())
    assert after["shares"]["directories"] == ["/Volumes/Extra/Music"]   # the owner's own entry stays
    assert after["web"]["ip_address"] == "127.0.0.1"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX paths")
def test_a_hand_added_share_the_rules_would_refuse_stays_but_is_logged(tmp_path: Path, fake_home: Path, caplog):
    """Other entries are the owner's: the startup repair names them in the log and leaves them."""
    data = tmp_path / "data"
    lib = fake_home / "Music"
    write_credentials(data, "dj", "pw")
    path = config_path(data)
    config = yaml.safe_load(path.read_text())
    config["shares"] = {"directories": [str(fake_home / "Documents"), "/Volumes/Extra/Music", str(lib)]}
    path.write_text(yaml.safe_dump(config))
    caplog.set_level("WARNING")

    assert write_share(data, lib, saved=True) is True

    assert yaml.safe_load(path.read_text())["shares"]["directories"] == [
        str(fake_home / "Documents"), "/Volumes/Extra/Music", str(lib)]
    assert str(fake_home / "Documents") in caplog.text
    assert "/Volumes/Extra/Music" not in caplog.text


# L2: folders that hold more than music, including the roots of cloud drives and their Desktop and Documents.
@pytest.mark.parametrize("inside", ["Pictures", "Movies", "Videos", "pictures",
                                    "Library/Mobile Documents/com~apple~CloudDocs",
                                    "Library/Mobile Documents/com~apple~CloudDocs/Desktop",
                                    "Library/Mobile Documents/com~apple~CloudDocs/Documents",
                                    "Library/CloudStorage/Dropbox", "Library/CloudStorage/Dropbox/Desktop",
                                    "Library/CloudStorage/OneDrive-Personal/Documents",
                                    "Library/CloudStorage/GoogleDrive-dj@example.com",
                                    "Library/CloudStorage/GoogleDrive-dj@example.com/My Drive",
                                    "Library/CloudStorage/GoogleDrive-dj@example.com/My Drive/Documents",
                                    "OneDrive", "OneDrive/Desktop", "OneDrive/Documents",
                                    "OneDrive - Contoso", "OneDrive - Contoso/Documents"])
def test_cloud_drive_roots_and_everyday_folders_are_never_shared(tmp_path: Path, fake_home: Path, inside):
    assert unsafe_share_reason(fake_home / inside, tmp_path / "data")


@pytest.mark.parametrize("inside", ["Pictures/DJ Library", "OneDrive/Music/DJ", "OneDrive - Contoso/DJ",
                                    "Library/CloudStorage/OneDrive-Personal/DJ",
                                    "Library/CloudStorage/GoogleDrive-dj@example.com/My Drive/DJ",
                                    "Library/Mobile Documents/com~apple~CloudDocs/Documents/DJ",
                                    "OneDriveMusic"])
def test_a_music_folder_inside_a_cloud_drive_or_pictures_is_fine(tmp_path: Path, fake_home: Path, inside):
    assert unsafe_share_reason(fake_home / inside, tmp_path / "data") is None


# Second pass: slskd lists every folder below a share and filters each one on its own path, so a filter that
# matched only a dot-folder's own name left everything inside it shared.
def test_files_inside_a_dot_folder_at_any_depth_are_never_shared(tmp_path: Path, fake_home: Path):
    lib = tmp_path / ".drive" / "DJ Library"
    write_credentials(tmp_path, "digger", "not-a-real-password", library_root=lib)
    filters = yaml.safe_load(config_path(tmp_path).read_text())["shares"]["filters"]
    for inside in [".git/objects", ".git/objects/ab/cdef", "Artist/.hidden/sub/Track.flac",
                   ".Trashes/501/Track.flac", "Artist/.sync/x"]:
        assert _hidden(filters, lib / inside), inside
    assert not _hidden(filters, lib)
    assert not _hidden(filters, lib / "Artist" / "Album" / "Artist - Track.flac")
    assert not _hidden(filters, lib / "Artist" / "Mr. Thumbs.db.flac")


def test_the_dot_folder_filter_reads_windows_paths():
    """slskd on Windows hands the filters backslashed paths; the regexes are .NET's, built from
    the same characters `re` reads here."""
    from pathlib import PureWindowsPath

    from flackey.slskd_config import _FORCED_FILTERS, hidden_folder_filter
    lib = PureWindowsPath("D:\\.drive\\DJ Library")
    filters = [*_FORCED_FILTERS, hidden_folder_filter(lib)]
    for inside in [".git", ".git\\objects\\ab\\cdef", "Artist\\.hidden\\sub\\Track.flac"]:
        assert any(re.search(f, str(lib / inside), re.IGNORECASE) for f in filters), inside
    for fine in ["", "Artist\\Artist - Track.flac", "Artist\\Mr. Thumbs.db.flac"]:
        assert not any(re.search(f, str(lib / fine), re.IGNORECASE) for f in filters), fine


def test_the_dot_folder_filter_follows_the_library(tmp_path: Path):
    old, new = tmp_path / "old", tmp_path / "new"
    write_credentials(tmp_path, "digger", "not-a-real-password", library_root=old)
    assert write_share(tmp_path, new, previous=old) is True
    filters = yaml.safe_load(config_path(tmp_path).read_text())["shares"]["filters"]
    assert _hidden(filters, new / ".git" / "objects" / "x")
    assert not _hidden(filters, old / "Artist" / ".x" / "y")
