"""flackey owns the slskd sidecar's configuration file, so the user never has to open or edit
slskd.yml by hand. The file holds three secrets -- the Soulseek password, the slskd web UI password,
and the API key flackey generates for itself -- and is created 0600 from the moment it exists."""
from __future__ import annotations

import logging
import os
import re
import secrets
import sys
import tempfile
from pathlib import Path, PurePath

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
    # Re-entering credentials does not change what is shared: a folder already in the share is held to
    # the rules it was saved under, so a grandfathered one is not refused here (see `_set_share`).
    shares = config.get("shares")
    already_shared = library_root is not None and isinstance(shares, dict) and \
        isinstance(shares.get("directories"), list) and str(library_root) in shares["directories"]

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
        _set_share(config, path, library_root, data_dir, saved=already_shared)

    _atomic_write(path, config)
    log.info("wrote slskd config: %s", path)
    return key_entry["key"]


# What slskd must never share out of the library (`shares.filters`: .NET regexes that slskd 0.26.0
# matches, unanchored and ignoring case, against the full local path of every folder and file it scans).
# slskd lists every folder below a share first and then filters each one on its own path, so a folder that
# matches is left out but the folders inside it are not: a filter has to match those too. A playlist export
# names every track by its full path, the owner's user name included, so the M3U files stay off the network
# wherever they are, and so does the library's own Playlists folder (export.py's PLAYLIST_DIR, which this
# layer cannot import).
PLAYLIST_FILE_FILTER = r"\.m3u8?$"
# Files the OS or another app drops next to the music, never the music itself: dot-files and dot-folders
# (.DS_Store; the AppleDouble `._x.flac` an exFAT drive grows, which carries each file's xattrs, the URL it
# was downloaded from included; .git; sync markers) and Windows' Thumbs.db / desktop.ini. Only the last
# part of the path is matched, so a library on a drive under some dot-folder is not hidden whole; what is
# inside a dot-folder is matched by `hidden_folder_filter`, anchored to the library for the same reason.
HIDDEN_FILE_FILTER = r"[\\/]\.[^\\/]*$"
SYSTEM_FILE_FILTER = r"[\\/](thumbs\.db|desktop\.ini)$"
_FORCED_FILTERS = (PLAYLIST_FILE_FILTER, HIDDEN_FILE_FILTER, SYSTEM_FILE_FILTER)
_PLAYLIST_DIR_TAIL = r"[\\/]Playlists([\\/]|$)"
# Any part below the library that starts with a dot. One `.*` before a fixed character: linear.
_HIDDEN_DIR_TAIL = r"[\\/](.*[\\/])?\."


def playlist_dir_filter(library_root: Path) -> str:
    """Anchored to the library, so a library that itself sits under some folder named Playlists is
    still shared."""
    return "^" + re.escape(str(library_root)) + _PLAYLIST_DIR_TAIL


def hidden_folder_filter(library_root: PurePath) -> str:
    """Everything inside a dot-folder at any depth below the library (`.git/objects/...`). On a Mac and
    Linux slskd already skips those, because .NET counts a dot-name as hidden there; on Windows only the
    hidden attribute counts, and a `.git` folder copied onto the drive does not have it."""
    return "^" + re.escape(str(library_root)) + _HIDDEN_DIR_TAIL


def _filters(config: dict, path: Path) -> list[str]:
    shares = _submapping(config, "shares", path)
    raw = shares.get("filters")
    filters = [f for f in raw if isinstance(f, str)] if isinstance(raw, list) else []
    shares["filters"] = filters
    return filters


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
    # Not a listener setting, but forced the same way: a config written before the filter existed gets it.
    filters = _filters(config, path)
    filters.extend(f for f in _FORCED_FILTERS if f not in filters)


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
# Folders that hold everything else the owner keeps. A music folder inside one is fine; the folder itself
# would share every document in it. Pictures holds the Photos library.
_EVERYDAY = ("desktop", "documents", "downloads", "movies", "pictures", "videos")
# APFS and NTFS ignore letter case unless formatted otherwise; the file systems Linux uses count it.
CASE_INSENSITIVE_DISK = sys.platform in ("darwin", "win32")


def _parts_under(path: Path, base: Path) -> tuple[str, ...] | None:
    """`path`'s parts below `base`, case-folded, or None when it is not inside it."""
    p = [s.casefold() for s in path.parts]
    b = [s.casefold() for s in base.parts]
    return tuple(p[len(b):]) if p[:len(b)] == b else None


def _is_or_holds(root: Path, inner: Path) -> bool:
    """Whether `inner` is `root` or inside it, however `root` was spelled. Both are resolved, but `resolve`
    keeps the letter case it was given on a Mac, so `/users/x` is not equal to `/Users/x` as a Path even
    though the disk opens the same folder for both. Where both exist the disk is asked, which is also right
    on a drive formatted to count case; for a folder not made yet, the case-folded parts stand in."""
    try:
        root_stat = root.stat()
    except (OSError, ValueError):
        root_stat = None
    folded_root = [s.casefold() for s in root.parts]
    for p in (inner, *inner.parents):
        if p == root:
            return True
        if root_stat is not None:
            try:
                if os.path.samestat(p.stat(), root_stat):
                    return True
                continue
            except (OSError, ValueError):
                pass
        if CASE_INSENSITIVE_DISK and [s.casefold() for s in p.parts] == folded_root:
            return True
    return False


def _cloud_drive_rest(below: tuple[str, ...]) -> tuple[str, ...] | None:
    """The parts below a cloud drive's own top folder, or None when `below` (the parts under the home
    folder) is not in one. The top of a cloud drive holds everything the owner syncs -- with iCloud's or
    OneDrive's folder sync on, their Desktop and Documents too -- so it is held to the home folder's rules."""
    if len(below) > 2 and below[0] == "library" and below[1] in _CLOUD_DRIVES:
        rest = below[3:]  # past CloudStorage/<provider>, or Mobile Documents/com~apple~CloudDocs
        return rest[1:] if rest[:1] == ("my drive",) else rest  # Google Drive's own top is one folder down
    if below and (below[0] == "onedrive" or below[0].startswith("onedrive - ")):  # Windows: ~\OneDrive
        return below[1:]
    return None


def _inside_temp(root: Path, base: Path) -> bool:
    """Windows keeps the temp folder under AppData\\Local. Nothing there is a sign-in, so a folder inside
    it is not refused for being inside AppData."""
    temp = Path(tempfile.gettempdir()).resolve()
    return _parts_under(temp, base) is not None and _parts_under(root, temp) is not None


# Top-level folders that belong to the system, refused with everything inside them. Spelled as the system
# spells them, for Linux, where case counts: the Docker image keeps the library in `/library`, which is not
# the Mac's `/Library`. On a Mac `/etc`, `/var` and `/tmp` resolve into `/private`, so that is listed too.
_POSIX_SYSTEM = frozenset({"Applications", "Library", "System", "bin", "boot", "cores", "dev", "etc", "lib",
                           "lib64", "opt", "private", "proc", "run", "sbin", "sys", "tmp", "usr", "var"})
# The same at the top of any Windows drive.
_WINDOWS_SYSTEM = frozenset({"windows", "program files", "program files (x86)", "programdata",
                             "$recycle.bin", "system volume information", "recovery", "perflogs"})
# Folders every account on the machine can write to. A music folder inside one is fine; the folder itself
# holds whatever anyone left there.
_POSIX_PUBLIC = ("Users", "Shared")
_WINDOWS_PUBLIC = ("users", "public")
WHOLE_DRIVE = "That is a whole drive. Make a folder for your music on it, and choose that folder."
SYSTEM_FOLDER = "That is a system folder. Choose a folder just for your music."
PUBLIC_FOLDER = "That folder holds more than music. Choose a folder just for your music, or make one inside it."


def system_folder_reason(root: PurePath, *, windows: bool, case_sensitive: bool) -> str | None:
    """Why `root` (absolute, already resolved) is a whole drive or a system folder, or None. Read off the
    path's parts alone, so either platform's rules can be checked on any machine."""
    parts = root.parts[1:]
    if not parts:
        return WHOLE_DRIVE  # `/`, `C:\\`
    if windows or not case_sensitive:
        parts = tuple(p.casefold() for p in parts)
    if windows:
        if parts[0] in _WINDOWS_SYSTEM:
            return SYSTEM_FOLDER
        return PUBLIC_FOLDER if parts == _WINDOWS_PUBLIC else None

    def named(part: str, name: str) -> bool:
        return part == name if case_sensitive else part == name.casefold()

    if named(parts[0], "Volumes") and len(parts) <= 2:  # the list of drives, or the top of one
        return WHOLE_DRIVE
    if any(named(parts[0], name) for name in _POSIX_SYSTEM):
        return SYSTEM_FOLDER
    if len(parts) == 2 and all(named(p, n) for p, n in zip(parts, _POSIX_PUBLIC, strict=True)):
        return PUBLIC_FOLDER
    return None


def drive_or_system_reason(library_root: Path) -> str | None:
    """`system_folder_reason` for this machine, on the resolved folder. The temp folder sits under a
    system folder on a Mac (/private/var/folders) and on Linux (/tmp), and nothing in it is anyone's
    documents or keys, so it is let through."""
    root = library_root.expanduser().resolve()
    if _parts_under(root, Path(tempfile.gettempdir()).resolve()) is not None:
        return None
    return system_folder_reason(root, windows=os.name == "nt",
                                case_sensitive=sys.platform not in ("darwin", "win32"))


def unsafe_share_reason(library_root: Path, data_dir: Path, *, drive_rules: bool = True) -> str | None:
    """Why this folder must never be the library, or None. The library is shared with the whole Soulseek
    network, so a folder that holds the home directory, Flackey's own data, or any app's keys and sign-ins
    would hand strangers the Telegram session and every saved password. Resolved first, so a symlink to ~
    is caught too. `drive_rules=False` leaves out the whole-drive and system-folder rules (audit
    2026-11-20), for a folder the owner saved before they existed."""
    if is_network_path(str(library_root)):
        return "That folder is on another computer. Choose a folder on this computer or a drive plugged into it."
    # Only the path as given: a folder on a mapped drive (Z:\) resolves to its UNC path, and an owner
    # who already chose one must not be locked out of every settings save by this check.
    root = library_root.expanduser().resolve()
    home = Path.home().resolve()
    if root == Path(root.anchor) or _is_or_holds(root, home):
        return "That folder holds your whole home folder. Choose a folder just for your music."
    if _is_or_holds(root, data_dir.expanduser().resolve()):
        return "That folder holds Flackey's own settings and sign-ins. Choose a folder just for your music."
    if drive_rules:
        reason = drive_or_system_reason(root)
        if reason:
            return reason
    below = _parts_under(root, home)
    if below and any(part.startswith(".") for part in below):  # ~/.ssh, ~/.config, ~/Music/.secret
        return "That is a hidden folder, where apps keep keys and settings. Choose a folder just for your music."
    if below and len(below) == 1 and below[0] in _EVERYDAY:
        return "That folder holds more than music. Choose a folder just for your music, or make one inside it."
    in_cloud = _cloud_drive_rest(below) if below else None
    if in_cloud is not None and (not in_cloud or (len(in_cloud) == 1 and in_cloud[0] in _EVERYDAY)):
        return "That folder holds more than music. Choose a folder just for your music, or make one inside it."
    if below and below[0] in _APP_SUPPORT:
        cloud = below[0] == "library" and len(below) > 2 and below[1] in _CLOUD_DRIVES
        if not cloud and not _inside_temp(root, home / below[0]):
            return "That folder is where apps keep their settings and sign-ins. Choose a folder just for your music."
    return None


def _set_share(config: dict, path: Path, library_root: Path, data_dir: Path,
               previous: Path | None = None, *, saved: bool = False) -> None:
    """Make the library folder one of slskd's shared directories. Sharing is what keeps a Soulseek
    user in good standing -- many peers refuse anyone who offers nothing -- and the spec
    (2026-09-07 §2) says the whole DJ Library is shared. Other entries are the owner's and stay; a
    previous library folder goes, because it is the same share moved, not a second one. Raises
    SlskdConfigError for a folder `unsafe_share_reason` refuses."""
    reason = unsafe_share_reason(library_root, data_dir, drive_rules=not saved)
    if reason:
        raise SlskdConfigError(reason)
    if saved and (late := drive_or_system_reason(library_root)):
        # Saved before the whole-drive and system-folder rules existed: still shared, as it was, so an
        # update never quietly stops it. The rules apply when the owner next chooses a folder.
        log.warning("the Soulseek share %s would not be accepted if chosen today (%s); still sharing it",
                    library_root, late)
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
    # One Playlists and one dot-folder filter, for the library as it is now: those written for a previous
    # folder go.
    filters = [f for f in _filters(config, path)
               if not (f.startswith("^") and f.endswith((_PLAYLIST_DIR_TAIL, _HIDDEN_DIR_TAIL)))]
    filters.append(playlist_dir_filter(library_root))
    filters.append(hidden_folder_filter(library_root))
    shares["filters"] = filters


def _warn_about_other_shares(config: dict, library_root: Path, data_dir: Path) -> None:
    """The startup repair's look at the rest of the share. Entries other than the library are the owner's,
    added by hand, so they stay; one these rules would refuse as the library is named in the log."""
    shares = config.get("shares")
    dirs = shares.get("directories") if isinstance(shares, dict) else None
    for d in dirs if isinstance(dirs, list) else []:
        if isinstance(d, str) and d != str(library_root) and (reason := unsafe_share_reason(Path(d), data_dir)):
            log.warning("the Soulseek share %s would not be accepted as a library folder (%s); still sharing "
                        "it, since it was added by hand", d, reason)


def _drop_share(config: dict, library_root: Path) -> None:
    shares = config.get("shares")
    if isinstance(shares, dict) and isinstance(shares.get("directories"), list):
        shares["directories"] = [d for d in shares["directories"] if d != str(library_root)]


def write_share(data_dir: Path, library_root: Path, previous: Path | None = None, *,
                saved: bool = False) -> bool:
    """Point the share at the library folder, on a config that already exists. False when Soulseek
    was never set up (no file), so a folder change on a Telegram-only install writes nothing. Raises
    SlskdConfigError when the file is present but unreadable or invalid. `saved` is the startup repair of
    the folder already in settings, which the whole-drive and system-folder rules only warn about."""
    path = config_path(data_dir)
    if not path.exists():
        return False
    config = _load_config(path)
    if saved:
        _warn_about_other_shares(config, library_root, data_dir)
    try:
        _set_share(config, path, library_root, data_dir, previous, saved=saved)
    except SlskdConfigError:
        if saved:
            # The saved folder breaks a rule that is not one of the whole-drive and system-folder ones
            # (those only warn for a saved folder, in `_set_share`): it holds the home folder, Flackey's
            # data or app sign-ins, so it stops being shared rather than staying in the share as it was.
            _drop_share(config, library_root)
        # Otherwise a refused folder leaves the share as it was. Either way the listener and key are still
        # pinned: the startup repair is often the only write a config gets.
        _pin_to_this_machine(config, path)
        _atomic_write(path, config)
        raise
    _pin_to_this_machine(config, path)
    _atomic_write(path, config)
    log.info("slskd share now %s", library_root)
    return True
