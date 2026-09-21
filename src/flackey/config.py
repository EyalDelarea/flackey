from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

from pydantic import Field, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .slskd_config import read_api_key

log = logging.getLogger(__name__)
XDG_DATA_DIR = Path("~/.config/flackey")
FILE_PREFIX = "flackey"
FILE_KEYS = ("library_root", "telegram_api_id", "telegram_api_hash",
             "slskd_url", "slskd_api_key", "slskd_downloads_dir", "lossless_filing_format",
             "source_enabled", "auto_update_check", "window_size")
PATH_KEYS = ("library_root", "slskd_downloads_dir")
FILING_FORMATS = ("aiff", "wav", "flac")


def default_data_dir() -> Path:
    if sys.platform == "darwin":
        return Path("~/Library/Application Support/Flackey").expanduser()
    return XDG_DATA_DIR.expanduser()


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
    # On by default so an owner ends up on the latest release without hunting for it; /api/update still
    # only ever surfaces a stable release with a built installer, never a prerelease or a broken one.
    auto_update_check: bool = True
    # The size the desktop window was last closed at, written by `desktop.remember_window_size`. None
    # until the owner has moved a corner, which is what lets a first launch open at `desktop.MAIN_SIZE`
    # without that default then overwriting every later choice. Stored rather than derived because the
    # window is gone by the time the next launch needs to know how big it was.
    window_size: tuple[int, int] | None = None
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
    # AIFF by default: Rekordbox plays WAV, but documents RIFF INFO -- not WAV ID3/APIC -- as its WAVE
    # metadata source, and that text path has no reliable embedded artwork field. AIFF is the same PCM at
    # the same size in a container Rekordbox imports with richer tags and cover art. WAV remains selectable
    # for owners who prefer plain PCM interchange.
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
    lossless_fingerprint_min: float = 0.79
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

# Telegram keys baked into a packaged build. packaging/build_app.sh writes this file from
# FLACKEY_TELEGRAM_API_ID and FLACKEY_TELEGRAM_API_HASH before PyInstaller runs; the release workflow
# exports those from repository secrets. A checkout has no such file and reads .env instead.
# Lowest precedence of all: anything the owner sets, in the environment or in
# settings.json, wins over what the build carries. Same location trick as desktop.APP_ICON -- inside the
# bundle `__file__` is <MEIPASS>/flackey/config.pyc and the assets folder is unpacked beside it.
BUILD_DEFAULTS_PATH = Path(__file__).with_name("assets") / "build.json"


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


def _error_summary(e: ValidationError) -> str:
    """Where each problem is and what kind it is -- never what the value was.

    pydantic renders a `ValidationError` with the offending input embedded in it, and for an error that
    belongs to the model rather than to one field the input it quotes is the whole dict it was handed.
    That dict is the settings file, so logging the exception itself would put `slskd_api_key` and
    `telegram_api_hash` in plaintext into flackey.log -- which is the file a bug report bundles up."""
    return ", ".join(
        f"{'.'.join(str(part) for part in err.get('loc') or ()) or '<whole file>'} ({err.get('type')})"
        for err in e.errors()) or "no detail"


def _settings_dropping_invalid(env_file: Path | None, overrides: dict, base: Settings) -> Settings:
    """Build `Settings` from the file-derived values, dropping only the keys that will not validate.

    One unreadable value used to cost the whole file. `Settings(**overrides)` reports every problem in a
    single `ValidationError`, and the caller answered it by falling back to a `Settings` with no file
    values in it at all -- so one malformed key silently took `library_root`, the Telegram keys and the
    slskd key down with it for that run, leaving only a log line that nobody reads in a windowed app.

    That was survivable while every file key was written by an owner submitting the settings screen. It is
    not now that `window_size` is rewritten on every quit: a half-finished write or a hand-edited file
    would reset everything else the owner had configured. pydantic names the offending key in each error's
    `loc`, so drop those and try again. Every pass removes at least one key, so this terminates; running
    out of keys means nothing in the file was usable, which is exactly `base`."""
    remaining = dict(overrides)
    while remaining:
        try:
            return Settings(_env_file=env_file, **remaining)
        except ValidationError as e:
            bad = {str(err["loc"][0]) for err in e.errors() if err.get("loc")} & remaining.keys()
            if not bad:
                # Not `e`: see `_error_summary`. Nothing reaches here while every validator on `Settings`
                # belongs to a field, because a field error names its field and is dropped above -- it is
                # the first model-level validator that makes this live, and by then the value being
                # rejected is the whole file.
                log.warning("ignoring %s entirely: %s", base.settings_path, _error_summary(e))
                return base
            log.warning("ignoring invalid %s in %s", ", ".join(sorted(bad)), base.settings_path)
            for key in bad:
                del remaining[key]
    return base


def load_settings(env_file: Path | None = None, build_defaults: Path | None = None) -> Settings:
    """Precedence: environment > .env > settings.json in the data folder > build.json inside a packaged
    build > defaults. When nothing in that chain set an API key, fall back to the one flackey already
    generated for itself in the managed slskd.yml (see slskd_config.write_credentials) -- that file is
    the key's only copy."""
    if env_file is None:
        # `./.env` when run from the repo root; otherwise the repo's own .env, so `flackey` works from any cwd
        env_file = Path(".env") if Path(".env").exists() else REPO_ENV
    base = Settings(_env_file=env_file)
    from_file = _read_settings_file(base.settings_path)
    from_build = _read_build_defaults(build_defaults or BUILD_DEFAULTS_PATH)
    overrides = {k: v for k, v in from_build.items() if k not in base.model_fields_set}
    overrides.update({k: v for k, v in from_file.items() if k not in base.model_fields_set})
    result = base if not overrides else _settings_dropping_invalid(env_file, overrides, base)
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
