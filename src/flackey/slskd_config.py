"""flackey owns the slskd sidecar's configuration file, so the user never has to open or edit
slskd.yml by hand. The file holds three secrets -- the Soulseek password, the slskd web UI password,
and the API key flackey generates for itself -- and is created 0600 from the moment it exists."""
from __future__ import annotations

import logging
import os
import secrets
import tempfile
from pathlib import Path

import yaml

log = logging.getLogger(__name__)

FLACKEY_WEB_USERNAME = "flackey"
# The name of our entry inside slskd's own config file -- not a Python identifier, a key in a third-party
# document that already exists on disk.
API_KEY_NAME = "flackey"
WEB_PORT = 5030
WEB_IP_ADDRESS = "127.0.0.1"
API_KEY_CIDR = "127.0.0.1/32"
SOULSEEK_LISTEN_PORT = 50300


class SlskdConfigError(Exception):
    """Invalid input or an unwritable config path. Never carries a secret value."""


def config_path(data_dir: Path) -> Path:
    return data_dir / "slskd" / "slskd.yml"


def read_listen_port(data_dir: Path) -> int:
    """The port peers actually connect to, read from the managed config rather than assumed: the owner
    may have changed it, and a settings screen that states the default while the sidecar listens
    elsewhere is worse than saying nothing."""
    try:
        config = yaml.safe_load(config_path(data_dir).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return SOULSEEK_LISTEN_PORT
    if not isinstance(config, dict):
        return SOULSEEK_LISTEN_PORT
    port = (config.get("soulseek") or {}).get("listen_port") if isinstance(config.get("soulseek"), dict) else None
    return int(port) if isinstance(port, int) else SOULSEEK_LISTEN_PORT


def _load_or_none(path: Path) -> dict | None:
    """The parsed config, or None when the file is missing, unreadable, malformed, or not a mapping."""
    if not path.exists():
        return None
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return None
    return data if isinstance(data, dict) else None


def read_api_key(data_dir: Path) -> str | None:
    """The flackey API key from the managed config, or None when there is no config, it cannot be
    parsed, or it holds no key. Never raises, never logs the value."""
    data = _load_or_none(config_path(data_dir))
    if data is None:
        return None
    try:
        key = data["web"]["authentication"]["api_keys"][API_KEY_NAME]["key"]
    except (KeyError, TypeError):
        return None
    return key or None


def read_username(data_dir: Path) -> str | None:
    """The configured Soulseek username (not a secret), or None."""
    data = _load_or_none(config_path(data_dir))
    if data is None:
        return None
    try:
        username = data["soulseek"]["username"]
    except (KeyError, TypeError):
        return None
    return username or None


def read_password(data_dir: Path) -> str | None:
    """The saved Soulseek password, or None. This is the one function here that hands a secret back to a
    caller, and it exists because Soulseek has no way to recover one: a username is bound to the password
    it was first claimed with, logging in with a different one is rejected rather than treated as a change,
    and the protocol's own change-password message needs a session that only the current password can open.
    An owner who loses this string loses the account, so flackey has to be able to show it to them.
    Never raises, never logs the value -- same contract as `read_api_key`."""
    data = _load_or_none(config_path(data_dir))
    if data is None:
        return None
    try:
        password = data["soulseek"]["password"]
    except (KeyError, TypeError):
        return None
    return password or None


def read_web_credentials(data_dir: Path) -> tuple[str, str] | None:
    """Credentials for slskd's own web login, distinct from the Soulseek network account."""
    data = _load_or_none(config_path(data_dir))
    if data is None:
        return None
    try:
        auth = data["web"]["authentication"]
        username, password = auth["username"], auth["password"]
    except (KeyError, TypeError):
        return None
    return (username, password) if username and password else None


def _submapping(parent: dict, key: str, path: Path) -> dict:
    """`parent[key]` as a dict, installing an empty one when the key is absent or explicitly null (a
    hand-written file's bare `web:` line parses to `None` -- that is just "nothing here yet", the same
    as the key being missing). Any other non-mapping value (a list, a string, ...) makes the file as
    unusable as one that parses to a list or string at the top level, so it raises the same way."""
    value = parent.get(key)
    if value is None:
        value = {}
        parent[key] = value
    elif not isinstance(value, dict):
        raise SlskdConfigError(f"{path} does not hold a valid slskd configuration.")
    return value


def _load_config(path: Path) -> dict:
    """The config we are about to rewrite, or an empty one when there is no file yet. Unlike
    `_load_or_none`, which answers questions and may shrug, this is the read before a write: an
    unreadable or invalid file must stop the write, because overwriting it would throw away an
    owner's settings we could not understand."""
    if not path.exists():
        return {}
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as e:
        raise SlskdConfigError(f"could not read {path}: {e.strerror or e}") from e
    try:
        loaded = yaml.safe_load(raw)
    except yaml.YAMLError as e:
        raise SlskdConfigError(f"{path} does not hold a valid slskd configuration.") from e
    if not isinstance(loaded, dict):
        raise SlskdConfigError(f"{path} does not hold a valid slskd configuration.")
    return loaded


def write_credentials(data_dir: Path, username: str, password: str,
                      library_root: Path | None = None) -> str:
    """Create or update the managed slskd.yml with these Soulseek credentials and return the
    flackey API key -- generated on the first write, preserved on every later one. With a
    `library_root`, that folder is also made one of the shared directories. Raises
    SlskdConfigError on invalid input or an unwritable path."""
    username = username.strip()
    if not username:
        raise SlskdConfigError("Soulseek username must not be empty.")
    if not password:
        raise SlskdConfigError("Soulseek password must not be empty.")

    path = config_path(data_dir)
    config = _load_config(path)

    web = _submapping(config, "web", path)
    web.setdefault("port", WEB_PORT)
    auth = _submapping(web, "authentication", path)
    auth.setdefault("username", FLACKEY_WEB_USERNAME)
    if not auth.get("password"):
        auth["password"] = secrets.token_urlsafe(24)
    api_keys = _submapping(auth, "api_keys", path)
    key_entry = _submapping(api_keys, API_KEY_NAME, path)
    if not key_entry.get("key"):
        key_entry["key"] = secrets.token_hex(32)  # slskd requires at least 16 characters
    _pin_to_this_machine(config, path)

    soulseek = _submapping(config, "soulseek", path)
    soulseek["username"] = username
    soulseek["password"] = password
    soulseek.setdefault("listen_port", SOULSEEK_LISTEN_PORT)

    directories = _submapping(config, "directories", path)
    directories.setdefault("downloads", str(data_dir / "slskd" / "downloads"))
    directories.setdefault("incomplete", str(data_dir / "slskd" / "incomplete"))

    if library_root is not None:
        _set_share(config, path, library_root, data_dir)

    _atomic_write(path, config)
    log.info("wrote slskd config: %s", path)
    return key_entry["key"]


def _pin_to_this_machine(config: dict, path: Path) -> None:
    """The settings that keep slskd's web UI and API answering this machine only. Forced on every write
    rather than filled in when missing: a config written by an older build, or edited by hand, must be put
    back the next time flackey writes it, not left as it is.

    `ip_address` binds the HTTP listener only: slskd's HTTPS listener has its own settings and stays on
    0.0.0.0, so with `ip_address` alone the sidecar's web UI answered from the LAN on 5031 (measured
    2026-09-09: HTTP 200 from this machine's own LAN address, while 5030 and flackey's own 8765 refused).
    Nothing here needs HTTPS -- the only client is flackey over loopback -- so that listener is turned off
    outright. The API key's `cidr` is pinned only when flackey's key is there: a share write on a file
    without one adds none."""
    config["remote_configuration"] = False
    web = _submapping(config, "web", path)
    web["ip_address"] = WEB_IP_ADDRESS
    _submapping(web, "https", path)["disabled"] = True
    auth = web.get("authentication")
    keys = auth.get("api_keys") if isinstance(auth, dict) else None
    entry = keys.get(API_KEY_NAME) if isinstance(keys, dict) else None
    if isinstance(entry, dict):
        entry["cidr"] = API_KEY_CIDR


def _atomic_write(path: Path, config: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f"{path.name}.tmp"
    tmp.unlink(missing_ok=True)  # a stale tmp from a previous crashed write must not block O_EXCL forever
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            yaml.safe_dump(config, f, default_flow_style=False, sort_keys=False)
        os.replace(tmp, path)
    except OSError as e:
        tmp.unlink(missing_ok=True)
        raise SlskdConfigError(f"could not write {path}: {e.strerror or e}") from e


def is_network_path(raw: str) -> bool:
    """A UNC path (`\\\\server\\share`, `//server/share`, or a `\\\\?\\` device path), read off the string
    itself: resolving one asks the network, which can hang on a server that is not there."""
    return raw.startswith(("\\\\", "//"))


# Inside the home folder: where every app keeps its sign-ins, tokens and caches. Compared without regard
# to case, because APFS and NTFS both ignore it.
_APP_SUPPORT = ("library", "appdata")
# Under ~/Library, the folders macOS puts cloud drives in (Dropbox, OneDrive and Google Drive in
# CloudStorage, iCloud Drive in Mobile Documents): a DJ library inside one is an ordinary choice.
_CLOUD_DRIVES = ("cloudstorage", "mobile documents")


def _parts_under(path: Path, base: Path) -> tuple[str, ...] | None:
    """`path`'s parts below `base`, case-folded, or None when it is not inside it."""
    p = [s.casefold() for s in path.parts]
    b = [s.casefold() for s in base.parts]
    return tuple(p[len(b):]) if p[:len(b)] == b else None


def _inside_temp(root: Path, base: Path) -> bool:
    """Windows keeps the temp folder under AppData\\Local. Nothing there is a sign-in, so a folder inside
    it is not refused for being inside AppData."""
    temp = Path(tempfile.gettempdir()).resolve()
    return _parts_under(temp, base) is not None and _parts_under(root, temp) is not None


def unsafe_share_reason(library_root: Path, data_dir: Path) -> str | None:
    """Why this folder must never be the library, or None. The library is shared with the whole Soulseek
    network, so a folder that holds the home directory, Flackey's own data, or any app's keys and sign-ins
    would hand strangers the Telegram session and every saved password. Resolved first, so a symlink to ~
    is caught too."""
    if is_network_path(str(library_root)):
        return "That folder is on another computer. Choose a folder on this computer or a drive plugged into it."
    # Only the path as given: a folder on a mapped drive (Z:\) resolves to its UNC path, and an owner
    # who already chose one must not be locked out of every settings save by this check.
    root = library_root.expanduser().resolve()
    home = Path.home().resolve()
    if root == Path(root.anchor) or root == home or home.is_relative_to(root):
        return "That folder holds your whole home folder. Choose a folder just for your music."
    if data_dir.expanduser().resolve().is_relative_to(root):
        return "That folder holds Flackey's own settings and sign-ins. Choose a folder just for your music."
    below = _parts_under(root, home)
    if below and any(part.startswith(".") for part in below):  # ~/.ssh, ~/.config, ~/Music/.secret
        return "That is a hidden folder, where apps keep keys and settings. Choose a folder just for your music."
    if below and below[0] in _APP_SUPPORT:
        cloud = below[0] == "library" and len(below) > 2 and below[1] in _CLOUD_DRIVES
        if not cloud and not _inside_temp(root, home / below[0]):
            return "That folder is where apps keep their settings and sign-ins. Choose a folder just for your music."
    return None


def _set_share(config: dict, path: Path, library_root: Path, data_dir: Path,
               previous: Path | None = None) -> None:
    """Make the library folder one of slskd's shared directories. Sharing is what keeps a Soulseek
    user in good standing -- many peers refuse anyone who offers nothing -- and the spec
    (2026-09-07 §2) says the whole DJ Library is shared. Other entries are the owner's and stay; a
    previous library folder goes, because it is the same share moved, not a second one. Raises
    SlskdConfigError for a folder `unsafe_share_reason` refuses."""
    reason = unsafe_share_reason(library_root, data_dir)
    if reason:
        raise SlskdConfigError(reason)
    shares = _submapping(config, "shares", path)
    dirs = shares.get("directories")
    if not isinstance(dirs, list):
        dirs = []
    dirs = [d for d in dirs if isinstance(d, str)]
    if previous is not None and str(previous) != str(library_root):
        dirs = [d for d in dirs if d != str(previous)]
    if str(library_root) not in dirs:
        dirs.append(str(library_root))
    shares["directories"] = dirs


def write_share(data_dir: Path, library_root: Path, previous: Path | None = None) -> bool:
    """Point the share at the library folder, on a config that already exists. False when Soulseek
    was never set up (no file), so a folder change on a Telegram-only install writes nothing. Raises
    SlskdConfigError when the file is present but unreadable or invalid."""
    path = config_path(data_dir)
    if not path.exists():
        return False
    config = _load_config(path)
    try:
        _set_share(config, path, library_root, data_dir, previous)
    except SlskdConfigError:
        # A refused folder leaves the share as it was, but the listener and key are still pinned: the
        # startup repair is often the only write a config gets.
        _pin_to_this_machine(config, path)
        _atomic_write(path, config)
        raise
    _pin_to_this_machine(config, path)
    _atomic_write(path, config)
    log.info("slskd share now %s", library_root)
    return True
