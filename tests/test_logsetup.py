import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path

import pytest

from flackey.config import Settings
from flackey.logsetup import configure_logging, log_startup_banner

_THIRD_PARTY_LOGGERS = ("telethon", "httpx", "httpcore", "uvicorn", "uvicorn.error", "uvicorn.access", "asyncio")
LINE_RE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3} DEBUG   flackey\.x: hello$")


@pytest.fixture(autouse=True)
def _restore_logging():
    """configure_logging mutates the root logger process-wide; leave it as it was found so this file's
    tests don't leak handlers (and stray file writes) into the rest of the suite."""
    root = logging.getLogger()
    handlers = list(root.handlers)
    level = root.level
    third_party = {name: logging.getLogger(name).level for name in _THIRD_PARTY_LOGGERS}
    yield
    for h in list(root.handlers):
        if h not in handlers:
            root.removeHandler(h)
            h.close()
    root.handlers[:] = handlers
    root.setLevel(level)
    for name, lvl in third_party.items():
        logging.getLogger(name).setLevel(lvl)


def test_configure_logging_creates_the_log_file(tmp_path: Path):
    log_path = configure_logging(tmp_path)
    assert log_path == tmp_path / "flackey.log"
    assert log_path.exists()


def test_flackey_logger_lands_in_the_file_at_debug_with_a_timestamp(tmp_path: Path):
    configure_logging(tmp_path)
    logging.getLogger("flackey.x").debug("hello")
    lines = (tmp_path / "flackey.log").read_text(encoding="utf-8").splitlines()
    assert any(LINE_RE.match(line) for line in lines), lines


def test_third_party_debug_noise_does_not_land_in_the_file(tmp_path: Path):
    configure_logging(tmp_path)
    logging.getLogger("telethon").debug("noise")
    assert "noise" not in (tmp_path / "flackey.log").read_text(encoding="utf-8")


def test_configure_logging_twice_leaves_one_file_handler(tmp_path: Path):
    configure_logging(tmp_path)
    configure_logging(tmp_path)
    file_handlers = [h for h in logging.getLogger().handlers if isinstance(h, RotatingFileHandler)]
    assert len(file_handlers) == 1


def test_banner_never_logs_the_api_hash(tmp_path: Path):
    log_path = configure_logging(tmp_path)
    settings = Settings(_env_file=None, telegram_api_id=1, telegram_api_hash="s3cret",
                        data_dir=tmp_path, library_root=tmp_path / "lib")
    log_startup_banner(settings, "9.9.9")
    for h in logging.getLogger().handlers:
        h.flush()
    text = log_path.read_text(encoding="utf-8")
    assert "s3cret" not in text
    assert "9.9.9" in text and "telegram configured: yes" in text


def test_banner_says_whether_soulseek_is_on_without_the_key(tmp_path, caplog):
    from flackey.config import Settings
    from flackey.logsetup import log_startup_banner

    caplog.set_level("INFO")
    log_startup_banner(Settings(_env_file=None, data_dir=tmp_path, slskd_api_key="very-secret-key"), "0.1.0")
    assert "soulseek: yes" in caplog.text and "very-secret-key" not in caplog.text


def test_banner_names_the_helper_programs_the_machine_is_missing(tmp_path, caplog, monkeypatch):
    """The README tells a friend to send back the log file when something goes wrong, and the commonest
    thing that will be wrong is that they skipped `brew install ffmpeg chromaprint`. Naming the helpers
    here answers that from the log alone, instead of from a failed row further down."""
    from flackey import tools
    from flackey.config import Settings
    from flackey.logsetup import log_startup_banner

    monkeypatch.setattr(tools, "BREW_BINS", ())
    monkeypatch.setattr(tools.shutil, "which", lambda _: None)
    caplog.set_level("INFO")

    log_startup_banner(Settings(_env_file=None, data_dir=tmp_path), "0.1.0")

    assert "missing helpers: ffmpeg, ffprobe, fpcalc" in caplog.text


def test_the_log_filename_agrees_with_the_project_file_prefix():
    # `config.FILE_PREFIX` names the live database and the files a migration renames; `LOG_FILE` names the
    # log beside them. They must spell the same project name, but config and logsetup are siblings on one
    # layer line and cannot import each other, so the agreement is asserted here instead of derived. A
    # rename that updates one and forgets the other leaves the app writing its log under the old name.
    from flackey.config import FILE_PREFIX
    from flackey.logsetup import LOG_FILE

    assert LOG_FILE == f"{FILE_PREFIX}.log"
