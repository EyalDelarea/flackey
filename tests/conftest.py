import shutil
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixtures() -> Path:
    return FIXTURES


@pytest.fixture(autouse=True)
def _no_build_defaults(monkeypatch, tmp_path):
    """Keep every test hermetic against a real packaged build. `load_settings` falls back to
    `flackey.config.BUILD_DEFAULTS_PATH` (src/flackey/assets/build.json) when no `build_defaults=`
    override is given; on a machine where someone ran packaging/build_app.sh locally with the
    FLACKEY_TELEGRAM_* vars set, that file exists and would leak real Telegram keys into any test that
    doesn't pass its own `build_defaults=`. Point every test at a path that never exists instead."""
    monkeypatch.setattr("flackey.config.BUILD_DEFAULTS_PATH", tmp_path / "no-build.json")


def has_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


requires_ffmpeg = pytest.mark.skipif(not has_ffmpeg(), reason="ffmpeg not installed")


def has_fpcalc() -> bool:
    return shutil.which("fpcalc") is not None


requires_fpcalc = pytest.mark.skipif(not has_fpcalc(), reason="fpcalc (chromaprint) not installed")


@pytest.fixture(autouse=True)
def _no_public_deezer_lookup(monkeypatch):
    """The worker asks Deezer's public API for a record when the bot gave none, and the worker tests use a
    real `httpx.AsyncClient`. Without this every such test would reach api.deezer.com. Tests of the lookup
    itself call `reference.find_deezer_record` directly, and worker tests that want a record patch this."""
    async def none(cand, http):
        return None, "no deezer lookup in tests"

    monkeypatch.setattr("flackey.worker.acoustic.find_deezer_record", none)


@pytest.fixture(autouse=True)
def _never_open_the_real_installer(monkeypatch):
    """`open_installer` shells out to `open`, so a test that reaches it throws a modal Installer.app
    dialog at whoever is running the suite. Tests that mean to reach this path stub it themselves."""
    monkeypatch.setattr("flackey.web.update.open_installer",
                        lambda path: pytest.fail(f"a test opened the real installer: {path} -- stub "
                                                 "flackey.web.update.open_installer in the test"))
