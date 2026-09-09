import shutil
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixtures() -> Path:
    return FIXTURES


def has_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


requires_ffmpeg = pytest.mark.skipif(not has_ffmpeg(), reason="ffmpeg not installed")


def has_fpcalc() -> bool:
    return shutil.which("fpcalc") is not None


requires_fpcalc = pytest.mark.skipif(not has_fpcalc(), reason="fpcalc (chromaprint) not installed")
