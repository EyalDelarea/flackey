import subprocess
from pathlib import Path

import pytest

from krater.verify import probe, spectral_cutoff_hz, spectrogram_png, verify
from tests.conftest import requires_ffmpeg

pytestmark = requires_ffmpeg


def _ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args], check=True)


def _noise(dir_: Path, color: str) -> Path:
    p = dir_ / f"{color}.wav"
    _ffmpeg("-f", "lavfi", "-i", f"anoisesrc=color={color}:seed=1:sample_rate=44100:duration=20",
            "-ac", "2", str(p))
    return p


def _encode(wav: Path, kbps: int, name: str) -> Path:
    p = wav.with_name(name)
    _ffmpeg("-i", str(wav), "-c:a", "libmp3lame", "-b:a", f"{kbps}k", str(p))
    return p


@pytest.fixture(scope="module")
def audio_dir(tmp_path_factory) -> Path:
    return tmp_path_factory.mktemp("audio")


@pytest.fixture(scope="module")
def noise_wav(audio_dir: Path) -> Path:
    return _noise(audio_dir, "white")


@pytest.fixture(scope="module")
def pink_wav(audio_dir: Path) -> Path:
    return _noise(audio_dir, "pink")  # 1/f roll-off: the closest synthetic stand-in for music


@pytest.fixture(scope="module")
def real320(noise_wav: Path) -> Path:
    return _encode(noise_wav, 320, "real320.mp3")


@pytest.fixture(scope="module")
def fake320(noise_wav: Path) -> Path:
    low = _encode(noise_wav, 128, "low128.mp3")
    return _encode(low, 320, "fake320.mp3")


@pytest.fixture(scope="module")
def flac(noise_wav: Path) -> Path:
    p = noise_wav.with_name("x.flac")
    _ffmpeg("-i", str(noise_wav), "-c:a", "flac", str(p))
    return p


@pytest.fixture(scope="module")
def pink_flac(pink_wav: Path) -> Path:
    p = pink_wav.with_name("pink.flac")
    _ffmpeg("-i", str(pink_wav), "-c:a", "flac", str(p))
    return p


@pytest.fixture(scope="module")
def pink_fake_flac(pink_wav: Path) -> Path:
    low = _encode(pink_wav, 128, "pink128.mp3")
    p = pink_wav.with_name("pink_fake.flac")
    _ffmpeg("-i", str(low), "-c:a", "flac", str(p))
    return p


def test_probe_mp3(real320: Path):
    pr = probe(real320)
    assert pr.fmt == "mp3" and pr.bitrate_kbps == 320 and 19 < pr.duration_s < 21 and pr.sample_rate == 44100


def test_probe_flac(flac: Path):
    assert probe(flac).fmt == "flac"


def test_cutoff_distinguishes_real_from_fake(real320: Path, fake320: Path, flac: Path):
    assert spectral_cutoff_hz(real320) >= 19_000
    assert spectral_cutoff_hz(fake320) <= 17_500
    assert spectral_cutoff_hz(flac) >= 20_500


def test_cutoff_on_music_like_spectrum(pink_wav: Path, pink_flac: Path, pink_fake_flac: Path):
    # a natural roll-off is not a cliff: full-band pink noise must read as Nyquist, not as "cut at 1 kHz"
    assert spectral_cutoff_hz(pink_flac) == 22_050
    assert spectral_cutoff_hz(_encode(pink_wav, 320, "pink320.mp3")) >= 19_000
    assert spectral_cutoff_hz(pink_fake_flac) <= 17_500


def test_spectrogram_png_written(real320: Path, tmp_path: Path):
    out = spectrogram_png(real320, tmp_path / "s.png")
    assert out.exists() and out.stat().st_size > 10_000


def test_verify_verdicts(real320: Path, fake320: Path, flac: Path, pink_fake_flac: Path, tmp_path: Path):
    ok = verify(real320, tmp_path)
    assert ok.passed and ok.fmt == "mp3" and ok.bitrate_kbps == 320 and ok.spectrogram_path.exists()
    bad = verify(fake320, tmp_path)
    assert not bad.passed and "cutoff" in bad.reason and bad.spectrogram_path.exists()
    assert verify(flac, tmp_path).passed
    assert not verify(pink_fake_flac, tmp_path).passed


def test_verify_names_spectrogram(real320: Path, tmp_path: Path):
    assert verify(real320, tmp_path, name="req7-123").spectrogram_path == tmp_path / "req7-123.png"


def test_verify_rejects_low_bitrate(noise_wav: Path, tmp_path: Path):
    low = tmp_path / "low.mp3"
    _ffmpeg("-i", str(noise_wav), "-c:a", "libmp3lame", "-b:a", "192k", str(low))
    v = verify(low, tmp_path)
    assert not v.passed and "bitrate" in v.reason


def test_verify_unsupported_format_skips_spectrogram(noise_wav: Path, tmp_path: Path):
    opus = tmp_path / "x.opus"
    _ffmpeg("-i", str(noise_wav), "-c:a", "libopus", "-b:a", "128k", str(opus))
    v = verify(opus, tmp_path)
    assert not v.passed and "unsupported" in v.reason and v.spectrogram_path is None


def test_probe_reports_bit_depth_and_sample_rate(flac: Path, real320: Path):
    p = probe(flac)
    assert (p.fmt, p.bit_depth, p.sample_rate) == ("flac", 16, 44100)
    m = probe(real320)
    assert (m.fmt, m.bit_depth, m.sample_rate) == ("mp3", None, 44100)


def test_verdict_carries_bit_depth_and_sample_rate(flac: Path, tmp_path: Path):
    v = verify(flac, tmp_path)
    assert v.passed and v.bit_depth == 16 and v.sample_rate == 44100
