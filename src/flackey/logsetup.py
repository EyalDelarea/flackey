"""Shareable, timestamped logging. Stdlib only: callers pass in whatever they need (a data dir, a
settings-shaped object) rather than this module reaching for `config` itself, so it stays usable from
the very bottom of the import graph."""

from __future__ import annotations

import logging
import platform
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_FILE = "flackey.log"
MAX_BYTES = 2_000_000
BACKUPS = 5
FORMAT = "%(asctime)s.%(msecs)03d %(levelname)-7s %(name)s: %(message)s"
DATEFMT = "%Y-%m-%d %H:%M:%S"

# Third-party loggers that are far too chatty at DEBUG; capped at INFO regardless of --verbose.
_QUIET_LOGGERS = ("telethon", "httpx", "httpcore", "uvicorn", "uvicorn.error", "uvicorn.access", "asyncio")
_INSTALLED_ATTR = "_flackey_handler"


def configure_logging(data_dir: Path, *, verbose: bool = False) -> Path:
    """Console at INFO (DEBUG with --verbose); the file in the data folder always at DEBUG for flackey.*
    and INFO for everything else (telethon, httpx, uvicorn are far too chatty at DEBUG). Returns the log path."""
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    for h in [h for h in root.handlers if getattr(h, _INSTALLED_ATTR, False)]:
        root.removeHandler(h)
        h.close()  # release the file descriptor: re-configuring never leaks one

    formatter = logging.Formatter(FORMAT, datefmt=DATEFMT)

    console = logging.StreamHandler()
    console.setLevel(logging.DEBUG if verbose else logging.INFO)
    console.setFormatter(formatter)
    setattr(console, _INSTALLED_ATTR, True)
    root.addHandler(console)

    data_dir.mkdir(parents=True, exist_ok=True)
    log_path = data_dir / LOG_FILE
    log_path.touch(exist_ok=True)  # so "Show logs" in the UI always has a file to reveal
    file_handler = RotatingFileHandler(log_path, maxBytes=MAX_BYTES, backupCount=BACKUPS, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    setattr(file_handler, _INSTALLED_ATTR, True)
    root.addHandler(file_handler)

    for name in _QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.INFO)

    return log_path


def log_startup_banner(settings, version: str) -> None:
    """One INFO line per fact a bug report needs. Never the api id/hash or a phone number."""
    log = logging.getLogger(__name__)
    log.info("flackey version: %s", version)
    log.info("platform: %s", platform.platform())
    log.info("python: %s", platform.python_version())
    log.info("data dir: %s", settings.data_dir)
    log.info("library root: %s", settings.library_root)
    log.info("web port: %s", settings.web_port)
    log.info("telegram configured: %s", "yes" if settings.telegram_configured else "no")
    log.info("soulseek: %s", "yes" if settings.soulseek_enabled else "no")
