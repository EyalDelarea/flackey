from __future__ import annotations

import json
import logging
import os
import shutil
import sys
from pathlib import Path

from pydantic import Field, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .slskd_config import read_api_key, repoint_slskd_config

log = logging.getLogger(__name__)
XDG_DATA_DIR = Path("~/.config/flackey")
# Every name this project has shipped under, newest first. Two renames are behind us -- cratedigger, then
# krater -- and before the Mac-native move the data lived under ~/.config, so a machine can still be
# carrying any of these folders. `migrate_legacy_data_dir` walks them in order and takes the first that
# exists. Never derive them from the current name: that is exactly the bug where the migration looks for
# its own destination, finds nothing, and orphans the library. A third rename costs one entry per tuple.
LEGACY_APP_DIR_NAMES = ("Krater", "Cratedigger")
LEGACY_XDG_DIRS = (Path("~/.config/krater"), Path("~/.config/cratedigger"))
LEGACY_FILE_PREFIXES = ("krater", "cratedigger")   # <name>.sqlite, its -wal/-shm sidecars, <name>.log
FILE_PREFIX = "flackey"
# Rebuilt from the running venv on every launch (see desktop.build_bundle) and carries an absolute-path
# pyvenv.cfg, so copying one would only carry a stale bundle under the wrong name across. Every
# generation's bundle is skipped, not only the newest.
SKIP_ON_MIGRATE = tuple(f"{name}.app" for name in LEGACY_APP_DIR_NAMES)
FILE_KEYS = ("library_root", "telegram_api_id", "telegram_api_hash",
             "slskd_url", "slskd_api_key", "slskd_downloads_dir", "lossless_filing_format",
             "source_enabled")
PATH_KEYS = ("library_root", "slskd_downloads_dir")
FILING_FORMATS = ("aiff", "wav", "flac")


def default_data_dir() -> Path:
    if sys.platform == "darwin":
        return Path("~/Library/Application Support/Flackey").expanduser()
    return XDG_DATA_DIR.expanduser()


def legacy_data_dirs() -> tuple[Path, ...]:
    """Every folder an earlier version kept its data in, newest first."""
    xdg = tuple(p.expanduser() for p in LEGACY_XDG_DIRS)
    if sys.platform != "darwin":
        return xdg
    support = Path("~/Library/Application Support").expanduser()
    return tuple(support / name for name in LEGACY_APP_DIR_NAMES) + xdg


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Identify the software, not the person. Live in settings.json (or .env for development); a packaged
    # copy ships with them prefilled. Optional so the app can start and show the setup screen without them.
    telegram_api_id: int | None = None
    telegram_api_hash: str | None = None
    source_bot_username: str = "DeezerMusicBot"
    # The Telegram bot is one of two ways to get audio, and the flakier one: it is a third party that can
    # stop answering without any signal beyond a timeout. Turning it off skips it entirely -- no search, no
    # fetch -- so a request goes straight to Soulseek on the Beatport match instead of burning three
    # 30-second timeouts per track first. See `Worker._process`.
    source_enabled: bool = True
    library_root: Path = Path("~/Music/DJ Library")
    web_port: int = 8765
    # Loopback by default: the JSON API is unauthenticated and must never bind 0.0.0.0 outside a
    # container. The Dockerfile sets WEB_HOST=0.0.0.0 as an image-level ENV; leave this unset elsewhere.
    web_host: str = "127.0.0.1"
    data_dir: Path = Field(default_factory=default_data_dir)
    # How many requests the worker runs at once; None means every queued track at the same time. This is a
    # limit for the machine (ffmpeg, sockets, file handles), not for correctness: the slow stage is a
    # Soulseek transfer from one peer, upload slots are per-peer, and the two stages that *are* shared --
    # the one Deezer bot conversation and slskd's single-search endpoint -- hold their own locks. Ten is
    # past the point where more tracks make a playlist finish sooner (a home connection is saturated well
    # before then) and still short of a hundred open transfers on a laptop. See `Worker.run_forever`.
    max_concurrent_requests: int | None = 10

    # Lossless upgrade through the slskd sidecar (spec §10). Off until an API key is set.
    slskd_url: str = "http://127.0.0.1:5030"
    slskd_api_key: str | None = None
    slskd_downloads_dir: Path | None = None       # default: <data_dir>/slskd/downloads, see `slskd_downloads`
    # AIFF, not WAV: rekordbox reads no ID3 tag out of a WAV at all. Not after `data`, not moved in front of
    # it, and not when the tag is a single kilobyte -- all three import with the filename as the title and no
    # artist, album, genre or cover. For a WAV it reads only the RIFF `LIST/INFO` chunk, which has no artwork
    # field, so no WAV can carry a cover it will ever show. AIFF is the same PCM at the same size in a
    # container it tags fully, and every CDJ plays it.
    lossless_filing_format: str = "aiff"
    lossless_search_wait_s: int = 30
    lossless_first_byte_s: int = 60
    # How long to sit in one peer's queue before moving to the next survivor. The peers holding a rare goa
    # FLAC are queue-deep and have no free slot; that wait is normal Soulseek, not a failed transfer, so it
    # gets its own budget rather than being counted against `lossless_first_byte_s` (see slskd.download).
    lossless_queue_wait_s: int = 300
    # A transfer is alive while bytes keep arriving, so this is how long it may go without a new one --
    # not how long it may run in total. `lossless_transfer_s` is only the floor of the absolute ceiling,
    # which grows with the file so that a 67 MB FLAC from an honest 100 kB/s peer is not cut at 90 %;
    # below `lossless_min_rate_kbps` the peer is trickling and the next survivor is the better bet.
    lossless_stall_s: int = 60
    lossless_transfer_s: int = 600
    lossless_min_rate_kbps: int = 160
    lossless_poll_s: float = 2.0
    lossless_duration_tolerance_s: int = 3
    lossless_title_ratio: int = 90
    lossless_require_artist: bool = False
    lossless_max_queue: int | None = None
    lossless_fingerprint_min: float = 0.90
    lossless_max_picks: int = 4
    lossless_keep_raw_days: int = 30

    @field_validator("library_root", "data_dir", mode="after")
    @classmethod
    def _expand(cls, v: Path) -> Path:
        return v.expanduser()

    @field_validator("slskd_downloads_dir", mode="after")
    @classmethod
    def _expand_optional(cls, v: Path | None) -> Path | None:
        return v.expanduser() if v else v

    @field_validator("lossless_filing_format", mode="after")
    @classmethod
    def _filing_format(cls, v: str) -> str:
        if v not in FILING_FORMATS:
            raise ValueError(f"lossless_filing_format must be one of {FILING_FORMATS}")
        return v

    @property
    def telegram_configured(self) -> bool:
        return bool(self.telegram_api_id and self.telegram_api_hash)

    @property
    def soulseek_enabled(self) -> bool:
        return bool(self.slskd_api_key)

    @property
    def lossless_enabled(self) -> bool:
        return self.soulseek_enabled

    @property
    def slskd_downloads(self) -> Path:
        return self.slskd_downloads_dir or self.data_dir / "slskd" / "downloads"

    @property
    def lossless_raw_dir(self) -> Path:
        return self.data_dir / "lossless" / "attempts"

    @property
    def settings_path(self) -> Path:
        return self.data_dir / "settings.json"

    @property
    def db_path(self) -> Path:
        # Derived, never spelled out: this is the live database name, and a rename that updates the
        # legacy prefixes but misses a literal here starts the app on an empty database with the real
        # library orphaned beside it under the old name.
        return self.data_dir / f"{FILE_PREFIX}.sqlite"

    @property
    def session_path(self) -> Path:
        return self.data_dir / "owner"

    @property
    def spectrogram_dir(self) -> Path:
        return self.data_dir / "spectrograms"

    @property
    def tmp_dir(self) -> Path:
        return self.data_dir / "tmp"


REPO_ENV = Path(__file__).resolve().parents[2] / ".env"

# Telegram keys baked into a packaged build. CI writes this file from repository secrets before
# PyInstaller runs (see .github/workflows and packaging/build_app.sh); a checkout has no such file and
# reads .env instead. Lowest precedence of all: anything the owner sets, in the environment or in
# settings.json, wins over what the build carries. Same location trick as desktop.APP_ICON -- inside the
# bundle `__file__` is <MEIPASS>/flackey/config.pyc and the assets folder is unpacked beside it.
BUILD_DEFAULTS_PATH = Path(__file__).with_name("assets") / "build.json"
BUILD_DEFAULT_KEYS = ("telegram_api_id", "telegram_api_hash")


def _read_build_defaults(path: Path = BUILD_DEFAULTS_PATH) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        log.warning("ignoring unreadable build defaults %s: %s", path, e)
        return {}
    if not isinstance(data, dict):
        return {}
    out = {}
    if isinstance(data.get("telegram_api_id"), int) and data["telegram_api_id"] > 0:
        out["telegram_api_id"] = data["telegram_api_id"]
    if isinstance(data.get("telegram_api_hash"), str) and data["telegram_api_hash"]:
        out["telegram_api_hash"] = data["telegram_api_hash"]
    return out


def _read_settings_file(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        log.warning("ignoring unreadable %s: %s", path, e)
        return {}
    return {k: v for k, v in data.items() if k in FILE_KEYS and v not in (None, "")}


def load_settings(env_file: Path | None = None, build_defaults: Path | None = None) -> Settings:
    """Precedence: environment > .env > settings.json in the data folder > build.json inside a packaged
    build > defaults. When nothing in that chain set an API key, fall back to the one flackey already
    generated for itself in the managed slskd.yml (see slskd_config.write_credentials) -- that file is
    the key's only copy."""
    if env_file is None:
        # `./.env` when run from the repo root; otherwise the repo's own .env, so `crate` works from any cwd
        env_file = Path(".env") if Path(".env").exists() else REPO_ENV
    base = Settings(_env_file=env_file)
    from_file = _read_settings_file(base.settings_path)
    from_build = _read_build_defaults(build_defaults or BUILD_DEFAULTS_PATH)
    overrides = {k: v for k, v in from_build.items() if k not in base.model_fields_set}
    overrides.update({k: v for k, v in from_file.items() if k not in base.model_fields_set})
    if not overrides:
        result = base
    else:
        try:
            result = Settings(_env_file=env_file, **overrides)
        except ValidationError as e:
            log.warning("ignoring invalid values in %s: %s", base.settings_path, e)
            result = base
    if not result.slskd_api_key:
        result.slskd_api_key = read_api_key(result.data_dir)
    return result


def save_settings(settings: Settings, **updates) -> Settings:
    """Persist only the keys given, merged into whatever settings.json already holds. Mutates `settings`
    in place so every holder of the object (the worker, the API) sees the change without a restart.
    Never writes a file-managed key that was only ever set via the environment or `.env`."""
    for k, v in updates.items():
        if k not in FILE_KEYS:
            raise ValueError(f"{k} is not a settings-file key")
        setattr(settings, k, Path(v).expanduser() if k in PATH_KEYS and v is not None else v)
    data = _read_settings_file(settings.settings_path)
    for k in updates:
        val = getattr(settings, k)
        data[k] = str(val) if k in PATH_KEYS and val is not None else val
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    tmp = settings.settings_path.with_suffix(".json.tmp")
    tmp.unlink(missing_ok=True)  # a stale tmp from a previous crashed write must not block O_EXCL forever
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(json.dumps(data, indent=2))
    os.replace(tmp, settings.settings_path)
    return settings


def migrate_legacy_data_dir(settings: Settings) -> bool:
    """Bring the data folder of an earlier name across, once. Only when the data folder is the default
    (a DATA_DIR override, e.g. Docker, is respected) and the new folder does not exist yet.

    Copy, verify, then remove -- deliberately not `shutil.move`. The two folders can sit on different
    volumes, and a move that fails halfway leaves the owner with a library split across two names and no
    way to tell which half is current. Every step before the removal is recoverable: the worst outcome
    here is two identical folders and a line in the log, never a missing one."""
    if settings.data_dir != default_data_dir() or settings.data_dir.exists():
        return False
    for legacy in legacy_data_dirs():
        if legacy != settings.data_dir and legacy.is_dir() and _adopt_data_dir(legacy, settings.data_dir):
            return True
    return False


def _adopt_data_dir(legacy: Path, dest: Path) -> bool:
    """Copy verbatim, verify, only then rewrite the names and paths that carry the old one, and only then
    remove the source. The order is the whole safety argument -- rewriting before the verify would make
    the verify fail on the files it just changed."""
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(legacy, dest, ignore=shutil.ignore_patterns(*SKIP_ON_MIGRATE), symlinks=True)
    except (OSError, shutil.Error) as e:
        log.error("could not copy app data from %s to %s: %s; keeping the old folder", legacy, dest, e)
        shutil.rmtree(dest, ignore_errors=True)   # a half-written copy must not look like a finished one
        return False
    differ = _files_differing(legacy, dest)
    if differ:
        log.error("app data copied from %s is incomplete (%d files differ, first: %s); keeping both folders",
                  legacy, len(differ), differ[0])
        return False
    _rename_legacy_files(dest)
    repoint_slskd_config(legacy, dest)
    shutil.rmtree(legacy, ignore_errors=True)
    log.info("moved app data from %s to %s", legacy, dest)
    return True


def _files_differing(legacy: Path, dest: Path) -> list[str]:
    """Relative paths whose presence or size does not match, sorted. Size rather than a checksum: this
    runs on every launch until it succeeds and the folder holds hundreds of megabytes of audio, and a
    truncated or missing file -- the only failure a local copy actually produces -- changes the size."""
    before, after = _sizes(legacy), _sizes(dest)
    return sorted(p for p in set(before) | set(after) if before.get(p) != after.get(p))


def _sizes(root: Path) -> dict[str, int]:
    out: dict[str, int] = {}
    for p in root.rglob("*"):
        rel = p.relative_to(root)
        if rel.parts[0] in SKIP_ON_MIGRATE or p.is_symlink() or not p.is_file():
            continue
        try:
            out[str(rel)] = p.stat().st_size
        except OSError:
            out[str(rel)] = -1     # unreadable counts as different, which is what we want it to do
    return out


def _rename_legacy_files(root: Path) -> None:
    """`krater.sqlite` / `cratedigger.sqlite`, their `-wal` and `-shm` sidecars and the matching `.log` all
    carry an older name. Renamed by prefix so the sqlite trio stays consistent: SQLite finds its
    write-ahead log by the `<database name>-wal` convention, so renaming the database on its own would
    strand the pages in it.

    Newest generation first, and a rename whose destination already exists is skipped rather than taken.
    A folder holding both `krater.sqlite` and `cratedigger.sqlite` has two databases wanting the same new
    name: `Path.rename` overwrites silently on POSIX, and pairing one database's `-wal` with another's
    main file is how you get something SQLite refuses to open. Skipping loses nothing -- the older file
    stays under its own name, where it can still be recovered by hand."""
    for prefix in LEGACY_FILE_PREFIXES:
        for p in sorted(root.iterdir()):
            if not p.is_file() or not p.name.startswith(prefix):
                continue
            dest = root / (FILE_PREFIX + p.name[len(prefix):])
            if dest.exists():
                log.warning("not renaming %s: %s already exists", p.name, dest.name)
                continue
            p.rename(dest)
