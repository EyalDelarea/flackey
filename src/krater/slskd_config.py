"""krater owns the slskd sidecar's configuration file, so the user never has to open or edit
slskd.yml by hand. The file holds three secrets -- the Soulseek password, the slskd web UI password,
and the API key krater generates for itself -- and is created 0600 from the moment it exists."""
from __future__ import annotations

import logging
import os
import secrets
from pathlib import Path

import yaml

log = logging.getLogger(__name__)

KRATER_WEB_USERNAME = "krater"
# The name of our entry inside slskd's own config file -- not a Python identifier, a key in a third-party
# document that already exists on disk. Renaming it without carrying the old one across would leave
# `read_api_key` finding nothing, which turns the lossless provider off with no error anywhere; hence both
# the fallback below and the rewrite in `repoint_slskd_config`.
API_KEY_NAME = "krater"
LEGACY_API_KEY_NAME = "cratedigger"
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
        config = yaml.safe_load(config_path(data_dir).read_text())
    except (OSError, yaml.YAMLError):
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
    except (OSError, yaml.YAMLError):
        return None
    return data if isinstance(data, dict) else None


def read_api_key(data_dir: Path) -> str | None:
    """The krater API key from the managed config, or None when there is no config, it cannot be
    parsed, or it holds no key. Never raises, never logs the value."""
    data = _load_or_none(config_path(data_dir))
    if data is None:
        return None
    for name in (API_KEY_NAME, LEGACY_API_KEY_NAME):
        try:
            key = data["web"]["authentication"]["api_keys"][name]["key"]
        except (KeyError, TypeError):
            continue
        if key:
            return key
    return None


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


def write_credentials(data_dir: Path, username: str, password: str) -> str:
    """Create or update the managed slskd.yml with these Soulseek credentials and return the
    krater API key -- generated on the first write, preserved on every later one. Raises
    SlskdConfigError on invalid input or an unwritable path."""
    username = username.strip()
    if not username:
        raise SlskdConfigError("Soulseek username must not be empty.")
    if not password:
        raise SlskdConfigError("Soulseek password must not be empty.")

    path = config_path(data_dir)
    config: dict = {}
    if path.exists():
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
        config = loaded

    config.setdefault("remote_configuration", False)
    web = _submapping(config, "web", path)
    web.setdefault("port", WEB_PORT)
    web.setdefault("ip_address", WEB_IP_ADDRESS)
    # `ip_address` binds the HTTP listener only: slskd's HTTPS listener has its own settings and stays on
    # 0.0.0.0, so with just the two lines above the sidecar's web UI answered from the LAN on 5031
    # (measured 2026-09-09: HTTP 200 from this machine's own LAN address, while 5030 and krater's
    # own 8765 refused). Nothing here needs HTTPS -- the only client is krater over loopback -- so
    # the listener is turned off outright. Forced, not setdefault: a config written before this fix must
    # be corrected on the next credential write, not left as it is.
    _submapping(web, "https", path)["disabled"] = True
    auth = _submapping(web, "authentication", path)
    auth.setdefault("username", KRATER_WEB_USERNAME)
    if not auth.get("password"):
        auth["password"] = secrets.token_urlsafe(24)
    api_keys = _submapping(auth, "api_keys", path)
    if API_KEY_NAME not in api_keys and LEGACY_API_KEY_NAME in api_keys:
        api_keys[API_KEY_NAME] = api_keys.pop(LEGACY_API_KEY_NAME)   # the rename, if the migration missed it
    key_entry = _submapping(api_keys, API_KEY_NAME, path)
    if not key_entry.get("key"):
        key_entry["key"] = secrets.token_hex(32)  # slskd requires at least 16 characters
    key_entry.setdefault("cidr", API_KEY_CIDR)

    soulseek = _submapping(config, "soulseek", path)
    soulseek["username"] = username
    soulseek["password"] = password
    soulseek.setdefault("listen_port", SOULSEEK_LISTEN_PORT)

    directories = _submapping(config, "directories", path)
    directories.setdefault("downloads", str(data_dir / "slskd" / "downloads"))
    directories.setdefault("incomplete", str(data_dir / "slskd" / "incomplete"))

    _atomic_write(path, config)
    log.info("wrote slskd config: %s", path)
    return key_entry["key"]


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


def repoint_slskd_config(old_data_dir: Path, new_data_dir: Path) -> None:
    """Fix up the copied slskd.yml after the data folder has been renamed: it still holds absolute paths
    into the folder that is about to be removed, and files our API key under the old project name.

    Only paths that actually sit under the old folder are moved -- a downloads folder the owner pointed
    somewhere else of their own accord is left exactly where they put it, and `shares.directories` names
    the music library, which this rename does not touch. Never raises: a sidecar whose config needs a
    hand edit is a bad day, a migration that aborts half way through is a worse one."""
    path = config_path(new_data_dir)
    config = _load_or_none(path)
    if not config:
        return
    changed = False
    directories = config.get("directories")
    if isinstance(directories, dict):
        for key in ("downloads", "incomplete"):
            moved = _moved_under(directories.get(key), old_data_dir, new_data_dir)
            if moved is not None:
                directories[key], changed = moved, True
                log.info("slskd %s folder follows the renamed data folder", key)
    try:
        api_keys = config["web"]["authentication"]["api_keys"]
    except (KeyError, TypeError):
        api_keys = None
    if isinstance(api_keys, dict) and LEGACY_API_KEY_NAME in api_keys and API_KEY_NAME not in api_keys:
        api_keys[API_KEY_NAME] = api_keys.pop(LEGACY_API_KEY_NAME)
        changed = True
        log.info("slskd api key entry renamed to %s", API_KEY_NAME)   # the name, never the key
    if not changed:
        return
    try:
        _atomic_write(path, config)
    except SlskdConfigError as e:
        log.error("could not update %s after the rename: %s", path, e)


def _moved_under(value: object, old: Path, new: Path) -> str | None:
    """`value` rebased from `old` to `new`, or None when it is not a path inside `old`."""
    if not isinstance(value, str):
        return None
    try:
        rel = Path(value).relative_to(old)
    except ValueError:
        return None
    return str(new / rel)
