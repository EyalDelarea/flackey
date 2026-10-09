"""The slim ffmpeg/ffprobe the app ships (packaging/build_ffmpeg.sh, issue #137) against a full build.

The slim build is configured with --disable-everything and then only what Flackey's command lines use,
so a component missing from that list shows up as a call that fails in a packaged app and nowhere
else. Every other test runs on a full ffmpeg, which it also needs to make its fixtures (lavfi, LAME,
libopus are all absent from the slim one). This runs the app's own functions twice, once per build, on
every format they meet -- downloads, conversions, YouTube and Deezer reference audio -- and requires the
same answers.

Only when FLACKEY_SLIM_FFMPEG names a folder holding the slim binaries: CI's Linux audio job builds
them, and the Windows installer job points it at the ones it is about to ship.
"""

import os
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from flackey.audiofile import DEMUXERS
from flackey.convert import to_format
from flackey.fingerprint import compare, fingerprint
from flackey.verify import band_levels_db, probe, verify
from tests.conftest import requires_ffmpeg, requires_fpcalc

SLIM = os.environ.get("FLACKEY_SLIM_FFMPEG")
pytestmark = [requires_ffmpeg, requires_fpcalc,
              pytest.mark.skipif(not SLIM, reason="FLACKEY_SLIM_FFMPEG not set")]

# Two tones stepping in pitch over pink noise: enough structure for chromaprint to fingerprint.
SIGNAL = ("aevalsrc='0.3*sin(2*PI*(220+110*floor(t/2))*t)+0.2*sin(2*PI*(330+55*floor(t/3))*t)"
          "|0.3*sin(2*PI*(275+40*floor(t/2.5))*t)':s=44100:d=40")
NOISE = "anoisesrc=c=pink:a=0.08:d=40:r=44100"
# name -> encoder arguments, each from the 24-bit master
FIXTURES = {
    "src24.flac": ["-c:a", "flac", "-sample_fmt", "s32"],
    "src16.flac": ["-c:a", "flac", "-sample_fmt", "s16"],
    "w16.wav": ["-c:a", "pcm_s16le"],
    "w24.wav": ["-c:a", "pcm_s24le"],
    "a16.aiff": ["-c:a", "pcm_s16be"],
    "a24.aiff": ["-c:a", "pcm_s24be"],
    "m320.mp3": ["-c:a", "libmp3lame", "-b:a", "320k"],
    "yt.webm": ["-c:a", "libopus", "-b:a", "128k"],
    "yt.m4a": ["-c:a", "aac", "-b:a", "128k"],
    "yt.ogg": ["-c:a", "libopus", "-b:a", "96k"],
}
TOOLS = ("ffmpeg", "ffprobe")


@pytest.fixture(scope="module")
def full_ffmpeg() -> str:
    return shutil.which("ffmpeg")


@pytest.fixture(scope="module")
def fixtures(tmp_path_factory, full_ffmpeg: str) -> Path:
    d = tmp_path_factory.mktemp("slim")
    master = d / "master.wav"
    run = [full_ffmpeg, "-hide_banner", "-loglevel", "error", "-y"]
    subprocess.run([*run, "-f", "lavfi", "-i", SIGNAL, "-f", "lavfi", "-i", NOISE, "-filter_complex",
                    "[0][1]amix=inputs=2:normalize=0,aformat=channel_layouts=stereo", "-c:a", "pcm_s24le",
                    str(master)], check=True)
    cover = d / "cover.png"
    subprocess.run([*run, "-f", "lavfi", "-i", "testsrc=s=300x300:d=1", "-frames:v", "1", str(cover)], check=True)
    for name, enc in FIXTURES.items():
        subprocess.run([*run, "-i", str(master), *enc, str(d / name)], check=True)
    # Cover art: a video stream the slim build has no decoder for, which every call must step around.
    subprocess.run([*run, "-i", str(master), "-i", str(cover), "-map", "0:a", "-map", "1", "-c:a", "flac",
                    "-c:v", "png", "-disposition:v", "attached_pic", str(d / "cover.flac")], check=True)
    subprocess.run([*run, "-i", str(master), "-i", str(cover), "-map", "0:a", "-map", "1", "-c:a", "libmp3lame",
                    "-b:a", "320k", "-c:v", "mjpeg", "-disposition:v", "attached_pic", "-id3v2_version", "3",
                    str(d / "cover.mp3")], check=True)
    master.unlink()
    cover.unlink()
    return d


def _pcm(ffmpeg: str, path: Path) -> np.ndarray:
    """What a file decodes to, read with the full build so only the file under test varies."""
    out = subprocess.run([ffmpeg, "-v", "error", "-i", str(path), "-map", "0:a", "-f", "s32le", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(out, dtype=np.int32)


def _answers(src: Path, work: Path, full_ffmpeg: str) -> dict:
    """Everything the app derives from one file, the way the app derives it."""
    work.mkdir()
    pr = probe(src)
    v = verify(src, work)  # turns the YouTube formats away before any tool runs
    out = {
        "probe": (pr.fmt, pr.bitrate_kbps, pr.bit_depth, pr.sample_rate),
        "duration": pr.duration_s,
        "verdict": (v.passed, v.fmt, v.bitrate_kbps),
        "cutoff": v.cutoff_hz,
        "spectrogram": v.spectrogram_path is not None and v.spectrogram_path.stat().st_size > 0,
        "bands": band_levels_db(src, window_s=20)[1] if src.suffix in DEMUXERS else None,
        "fingerprints": [fingerprint(src), *(fingerprint(src, 5.0 + t, 20.0) for t in (0.0, 0.023))],
        "converted": {},
    }
    if src.suffix in (".flac", ".wav", ".aiff"):
        for fmt, bits in (("flac", None), ("wav", 16), ("wav", 24), ("aiff", 16), ("aiff", 24)):
            copy = work / src.name
            shutil.copy(src, copy)
            out["converted"][f"{fmt}{bits}"] = _pcm(full_ffmpeg, to_format(copy, fmt, bits)).tobytes()
            copy.unlink()
    return out


@pytest.mark.parametrize("name", [*FIXTURES, "cover.flac", "cover.mp3"])
def test_slim_build_gives_the_full_builds_answers(name, fixtures, full_ffmpeg, tmp_path, monkeypatch):
    src = fixtures / name
    full = _answers(src, tmp_path / "full", full_ffmpeg)
    monkeypatch.setenv("PATH", SLIM + os.pathsep + os.environ["PATH"])
    assert all(os.path.samefile(Path(shutil.which(t)).parent, SLIM) for t in TOOLS)
    slim = _answers(src, tmp_path / "slim", full_ffmpeg)

    # Exact wherever the answer is exact: lossless audio decodes to the same samples in any FFmpeg.
    for key in ("probe", "verdict", "spectrogram", "converted"):
        assert slim[key] == full[key], key
    if src.suffix != ".mp3" and src.suffix in DEMUXERS:
        assert slim["cutoff"] == full["cutoff"]
        np.testing.assert_allclose(slim["bands"], full["bands"], atol=0.01)  # resampler float rounding
        assert slim["fingerprints"] == full["fingerprints"]
        return
    # A lossy decoder is not bit-exact across FFmpeg versions or builds: a sample lands 1 LSB apart, and
    # newer versions trim an mp3's encoder delay differently. The full build here may be a newer FFmpeg
    # than the 6.1 we ship (Homebrew's, on a Mac), so lossy answers are held to what the app acts on:
    # the cutoff verify judges by (not the band levels under it, which shift with the trimmed delay)
    # and whether the fingerprints still match.
    assert slim["duration"] == pytest.approx(full["duration"], abs=0.1)
    assert slim["cutoff"] == pytest.approx(full["cutoff"], abs=500)
    for got, want in zip(slim["fingerprints"], full["fingerprints"], strict=True):
        score, _ = compare(got[5:-5], want)
        assert score > 0.95
