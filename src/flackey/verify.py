from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .models import Verdict
from .tools import tool_path

MIN_MP3_BITRATE = 320
MIN_MP3_CUTOFF = 18_000
MIN_LOSSLESS_CUTOFF = 20_000
LOSSLESS = {"flac", "wav", "aiff"}
SR = 44_100
FRAME = 4096
BAND_HZ = 250
CLIFF_DB = 20        # an encoder lowpass drops at least this much within 500 Hz; music never does
CLIFF_SEARCH_FROM_HZ = 8_000


class VerifyError(Exception):
    pass


@dataclass
class Probe:
    fmt: str
    bitrate_kbps: int
    duration_s: float
    sample_rate: int
    bit_depth: int | None = None


RUN_TIMEOUT_S = 300


def _run(cmd: list[str]) -> bytes:
    # The caller names the tool; this is where it turns into a path that survives a Finder launch.
    # `name` is kept for the message: an absolute path in a timeout error tells the reader nothing
    # they did not already know, and buries the one word that matters.
    name = cmd[0]
    cmd = [tool_path(name) or name, *cmd[1:]]
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=RUN_TIMEOUT_S)
    except subprocess.TimeoutExpired as e:
        raise VerifyError(f"{name} timed out after {RUN_TIMEOUT_S}s") from e
    if p.returncode != 0:
        raise VerifyError(p.stderr.decode(errors="replace")[-400:])
    return p.stdout


def probe(path: Path) -> Probe:
    out = _run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)])
    data = json.loads(out)
    audio = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), None)
    if audio is None:
        raise VerifyError("no audio stream")
    codec = audio.get("codec_name", "")
    fmt = {"pcm_s16le": "wav", "pcm_s24le": "wav", "pcm_s16be": "aiff", "pcm_s24be": "aiff"}.get(codec, codec)
    if fmt == "wav" and str(path).lower().endswith((".aif", ".aiff")):
        fmt = "aiff"
    bitrate = int(audio.get("bit_rate") or data["format"].get("bit_rate") or 0)
    bits = int(audio.get("bits_per_raw_sample") or audio.get("bits_per_sample") or 0) or None
    if fmt == "mp3":
        bits = None
    return Probe(fmt=fmt, bitrate_kbps=round(bitrate / 1000), duration_s=float(data["format"].get("duration", 0)),
                 sample_rate=int(audio.get("sample_rate", 0)), bit_depth=bits)


def _window(path: Path, window_s: int, duration_s: float | None) -> tuple[float, float]:
    dur = probe(path).duration_s if duration_s is None else duration_s
    if dur <= window_s:
        return 0.0, dur
    return (dur - window_s) / 2, float(window_s)


def band_levels_db(path: Path, window_s: int = 60, duration_s: float | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Average power per 250 Hz band over the middle window. Returns (band start Hz, level dB)."""
    start, length = _window(path, window_s, duration_s)
    pcm = _run(["ffmpeg", "-v", "error", "-ss", f"{start:.2f}", "-t", f"{length:.2f}", "-i", str(path),
                "-ac", "1", "-ar", str(SR), "-f", "s16le", "-"])
    x = np.frombuffer(pcm, dtype=np.int16).astype(np.float64) / 32768.0
    n = (len(x) // FRAME) * FRAME
    if n == 0:
        raise VerifyError("audio too short to analyze")
    frames = x[:n].reshape(-1, FRAME) * np.hanning(FRAME)
    power = np.mean(np.abs(np.fft.rfft(frames, axis=1)) ** 2, axis=0)
    db = 10 * np.log10(power + 1e-20)
    freqs = np.fft.rfftfreq(FRAME, 1 / SR)
    edges = np.arange(0, SR / 2 + BAND_HZ, BAND_HZ)
    levels = np.array([db[(freqs >= lo) & (freqs < hi)].mean() for lo, hi in zip(edges[:-1], edges[1:])])
    return edges[:-1], levels


def spectral_cutoff_hz(path: Path, window_s: int = 60, duration_s: float | None = None) -> int:
    """Frequency of the encoder lowpass cliff, or Nyquist (22050) when the content reaches the top.

    A lossy encoder zeroes everything above its lowpass, so the level falls by tens of dB within
    a few hundred hertz. Natural music rolls off gradually, a few dB per band, and never makes such
    a step. We take the *highest* cliff so a notch lower in the spectrum cannot masquerade as the edge.
    """
    starts, levels = band_levels_db(path, window_s, duration_s)
    region = starts >= CLIFF_SEARCH_FROM_HZ
    hz, db = starts[region], levels[region]
    drops = db[:-2] - db[2:]                      # level lost across two adjacent bands (500 Hz)
    cliffs = np.where(drops >= CLIFF_DB)[0]
    if len(cliffs) == 0:
        return SR // 2
    i = int(cliffs[-1])
    return int(round(hz[i + 1] / 50) * 50)


def spectrogram_png(path: Path, out: Path, window_s: int = 60, duration_s: float | None = None) -> Path:
    start, length = _window(path, window_s, duration_s)
    out.parent.mkdir(parents=True, exist_ok=True)
    _run(["ffmpeg", "-v", "error", "-y", "-ss", f"{start:.2f}", "-t", f"{length:.2f}", "-i", str(path),
          "-filter_complex", "[0:a]showspectrumpic=s=1200x400:legend=1:scale=log:color=intensity[o]",
          "-map", "[o]", "-frames:v", "1", str(out)])
    return out


def verify(path: Path, spectrogram_dir: Path, name: str | None = None) -> Verdict:
    pr = probe(path)
    if pr.fmt != "mp3" and pr.fmt not in LOSSLESS:
        return Verdict(False, pr.fmt, pr.bitrate_kbps, 0, f"unsupported format {pr.fmt}", None,
                       bit_depth=pr.bit_depth, sample_rate=pr.sample_rate)
    png = spectrogram_png(path, spectrogram_dir / f"{name or path.stem}.png", duration_s=pr.duration_s)
    if pr.fmt == "mp3" and pr.bitrate_kbps < MIN_MP3_BITRATE:
        return Verdict(False, pr.fmt, pr.bitrate_kbps, 0, f"bitrate {pr.bitrate_kbps} kbps below {MIN_MP3_BITRATE}", png,
                       bit_depth=pr.bit_depth, sample_rate=pr.sample_rate)
    cutoff = spectral_cutoff_hz(path, duration_s=pr.duration_s)
    if pr.fmt == "mp3":
        if cutoff < MIN_MP3_CUTOFF:
            return Verdict(False, pr.fmt, pr.bitrate_kbps, cutoff,
                           f"cutoff {cutoff} Hz below {MIN_MP3_CUTOFF}: upsampled from a lower bitrate", png,
                           bit_depth=pr.bit_depth, sample_rate=pr.sample_rate)
        return Verdict(True, pr.fmt, pr.bitrate_kbps, cutoff, f"genuine {pr.bitrate_kbps} kbps, content to {cutoff} Hz", png,
                       bit_depth=pr.bit_depth, sample_rate=pr.sample_rate)
    if cutoff < MIN_LOSSLESS_CUTOFF:
        return Verdict(False, pr.fmt, pr.bitrate_kbps, cutoff,
                       f"lossless container but cutoff {cutoff} Hz: lossy source", png,
                       bit_depth=pr.bit_depth, sample_rate=pr.sample_rate)
    return Verdict(True, pr.fmt, pr.bitrate_kbps, cutoff, f"lossless, content to {cutoff} Hz", png,
                   bit_depth=pr.bit_depth, sample_rate=pr.sample_rate)
