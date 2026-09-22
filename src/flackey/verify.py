from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np

from .models import Verdict
from .tools import tool_path

MIN_MP3_BITRATE = 320
MIN_MP3_CUTOFF = 18_000
MIN_LOSSLESS_CUTOFF = 21_000
LOSSLESS = {"flac", "wav", "aiff"}
FRAME = 4096
BAND_HZ = 250
CLIFF_DB = 20        # heuristic evidence of filtering, not proof of encoding history
CLIFF_SEARCH_FROM_HZ = 8_000
DETECTOR_VERSION = "2.0"
MIN_ACTIVE_FRAMES = 12
MIN_EDGE_SUPPORT = 0.75


class VerifyError(Exception):
    pass


@dataclass
class Probe:
    fmt: str
    bitrate_kbps: int
    duration_s: float
    sample_rate: int
    bit_depth: int | None = None
    channels: int = 1
    codec: str = ""
    container: str = ""


RUN_TIMEOUT_S = 300


def _run(cmd: list[str]) -> bytes:
    # The caller names the tool; this is where it turns into a path that survives a Finder launch.
    # `name` is kept for the message: an absolute path in a timeout error tells the reader nothing
    # they did not already know, and buries the one word that matters.
    name = cmd[0]
    found = tool_path(name)
    if found is None:
        # Say it here rather than handing `subprocess` a bare name and letting FileNotFoundError travel
        # up as a failed row whose message is a filename. `tool_path` has already looked in the bundle,
        # on the PATH and in both Homebrew prefixes, so None means the machine really does not have it.
        raise VerifyError(f"{name} is not installed")
    cmd = [found, *cmd[1:]]
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=RUN_TIMEOUT_S, check=False)
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
    container = data.get("format", {}).get("format_name", "")
    # Endianness identifies a PCM encoding, not its container. Keep the existing
    # filing allowlist (16/24-bit PCM); unsupported depths must not be silently truncated.
    pcm = codec in {"pcm_s16le", "pcm_s24le", "pcm_s16be", "pcm_s24be"}
    fmt = container if pcm and container in {"wav", "aiff"} else codec
    try:
        bitrate = int(audio.get("bit_rate") or data["format"].get("bit_rate") or 0)
        bits = int(audio.get("bits_per_raw_sample") or audio.get("bits_per_sample") or 0) or None
        result = Probe(fmt, round(bitrate / 1000), float(data["format"].get("duration", 0)),
                       int(audio.get("sample_rate", 0)), None if fmt == "mp3" else bits,
                       int(audio.get("channels", 0)), codec, container)
    except (ValueError, TypeError, KeyError) as e:
        raise VerifyError("invalid audio stream metadata") from e
    if not np.isfinite(result.duration_s) or result.duration_s <= 0 or result.sample_rate <= 0 or result.channels <= 0:
        raise VerifyError("invalid audio duration, sample rate or channel count")
    return result


def _window(path: Path, window_s: int, duration_s: float | None) -> tuple[float, float]:
    dur = probe(path).duration_s if duration_s is None else duration_s
    if dur <= window_s:
        return 0.0, dur
    return (dur - window_s) / 2, float(window_s)


def _windows(duration: float, budget: float = 60) -> list[tuple[float, float]]:
    """Three non-overlapping excerpts spanning the track; at most budget seconds."""
    length = min(duration, budget) / 3
    return [(float(start), length) for start in np.linspace(0, duration - length, 3)]


def _decode(path: Path, pr: Probe, start: float, length: float) -> np.ndarray:
    pcm = _run(["ffmpeg", "-v", "error", "-ss", f"{start:.6f}", "-t", f"{length:.6f}",
                "-i", str(path), "-map", "0:a:0", "-vn", "-c:a", "pcm_f64le", "-f", "f64le", "-"])
    values = np.frombuffer(pcm, dtype="<f8")
    if values.size % pr.channels or not np.isfinite(values).all():
        raise VerifyError("invalid decoded audio samples")
    return values.reshape(-1, pr.channels)


def _bands(x: np.ndarray, sr: int) -> tuple[np.ndarray, np.ndarray]:
    """Overlapping Hann windows; linear mean power per band, referenced to full scale.

    Summation happens before the logarithm. Channels are never mixed. Silence
    and frames >50 dB below the loudest frame do not vote in the analysis.
    """
    if len(x) < FRAME:
        return np.empty(0), np.empty((0, 0))
    frames = np.lib.stride_tricks.sliding_window_view(x, FRAME)[::FRAME // 2]
    rms = np.mean(frames ** 2, axis=1)
    active = rms > max(1e-8, float(rms.max()) * 1e-5)
    frames = frames[active]
    if not len(frames):
        return np.empty(0), np.empty((0, 0))
    hann = np.hanning(FRAME)
    power = np.abs(np.fft.rfft(frames * hann, axis=1)) ** 2 / (FRAME * np.sum(hann ** 2))
    freqs = np.fft.rfftfreq(FRAME, 1 / sr)
    edges = np.arange(0, sr / 2 + BAND_HZ, BAND_HZ)
    starts = edges[:-1]
    bands = np.stack([power[:, (freqs >= lo) & (freqs < hi)].mean(axis=1)
                      for lo, hi in zip(edges[:-1], edges[1:])], axis=1)
    return starts, bands


def _channel_evidence(x: np.ndarray, sr: int) -> dict:
    hz, bands = _bands(x, sr)
    result = {"active_frames": len(bands), "bandwidth_hz": None, "cutoff_hz": None,
              "edge_drop_db": None, "edge_support": 0.0}
    if len(bands) < MIN_ACTIVE_FRAMES:
        return result
    db = 10 * np.log10(bands.mean(axis=0) + 1e-30)
    frame_db = 10 * np.log10(bands + 1e-30)
    reference = float(db[(hz >= 1000) & (hz < min(8000, sr / 2))].max())
    audible = np.flatnonzero(db >= reference - 55)
    result["bandwidth_hz"] = int(min(sr / 2, hz[audible[-1]] + BAND_HZ)) if len(audible) else None
    high = (hz >= 8000) & (hz < min(16000, sr / 2))
    if high.any():
        # Diagnostic statistical features, not independent proof of a codec. Keep
        # these for corpus calibration rather than inventing a probability score.
        result["high_band_variation_db"] = round(float(np.std(frame_db[:, high], axis=0).mean()), 2)
        local = frame_db[:, 1:-1] - (frame_db[:, :-2] + frame_db[:, 2:]) / 2
        result["spectral_hole_fraction"] = round(float(np.mean(local[:, high[1:-1]] < -20)), 4)
    candidates = []
    for i in range(1, len(hz) - 4):
        if hz[i] < CLIFF_SEARCH_FROM_HZ or hz[i] > sr / 2 - 1000:
            continue
        before, after = db[i - 1], db[i + 1]
        drop = float(before - after)
        # Exclude cliffs buried in the noise floor and notches that recover above
        # the edge. Require attenuation across time as well as in the average.
        if before < reference - 40 or drop < CLIFF_DB:
            continue
        if float(np.quantile(db[i + 1:], .9)) > before - CLIFF_DB:
            continue
        eligible = frame_db[:, i - 1] > reference - 45
        if int(eligible.sum()) < MIN_ACTIVE_FRAMES:
            continue
        support = float(np.mean((frame_db[:, i - 1] - frame_db[:, i + 1])[eligible] >= CLIFF_DB))
        if support >= MIN_EDGE_SUPPORT:
            candidates.append((drop, int(hz[i]), support))
    if candidates:
        drop, cutoff, support = max(candidates)  # strongest meaningful edge, not highest residual cliff
        result.update(cutoff_hz=cutoff, edge_drop_db=round(drop, 2), edge_support=round(support, 3))
    return result


def analyze(path: Path, pr: Probe | None = None, window_s: int = 60) -> dict:
    """Versioned measurements and a heuristic assessment; no claimed authenticity probability."""
    pr = pr or probe(path)
    if pr.sample_rate > 192000 or pr.sample_rate < 8000 or pr.channels > 8:
        raise VerifyError("audio exceeds analysis limits (8–192 kHz, up to 8 channels)")
    observations = []
    used_bits = 0
    for start, length in _windows(pr.duration_s, window_s):
        pcm = _decode(path, pr, start, length)
        for channel in range(pr.channels):
            row = _channel_evidence(pcm[:, channel], pr.sample_rate)
            observations.append({"start_s": round(start, 3), "length_s": round(length, 3),
                                 "channel": channel, **row})
        if pr.fmt in LOSSLESS and pr.bit_depth and 16 < pr.bit_depth <= 32 and len(pcm):
            integers = np.rint(pcm * 2 ** (pr.bit_depth - 1)).astype(np.int64)
            occupied = int(np.bitwise_or.reduce(integers.ravel()))
            if occupied:
                used_bits = max(used_bits, pr.bit_depth - ((occupied & -occupied).bit_length() - 1))
    active = [o for o in observations if o["bandwidth_hz"] is not None]
    threshold = MIN_MP3_CUTOFF if pr.fmt == "mp3" else MIN_LOSSLESS_CUTOFF
    suspect = [o for o in active if o["cutoff_hz"] is not None and o["cutoff_hz"] < threshold]
    warnings = []
    if used_bits and used_bits <= 16:
        warnings.append(f"sampled PCM uses at most {used_bits} significant bits in a {pr.bit_depth}-bit stream; "
                        "consistent with bit padding, not proof of lossy encoding")
    # Every active channel/window votes. One quiet section or unusual channel
    # cannot condemn the entire track; disagreement is explicitly inconclusive.
    ratio = len(suspect) / len(active) if active else 0
    if not active or max(o["bandwidth_hz"] for o in active) < 8000:
        status, reason = "inconclusive", "insufficient active broadband audio for transcode analysis"
    elif ratio >= MIN_EDGE_SUPPORT:
        cutoff = int(np.median([o["cutoff_hz"] for o in suspect]))
        status = "suspicious"
        reason = (f"suspected transcode: sustained cutoff near {cutoff} Hz in {len(suspect)}/{len(active)} "
                  "active channel excerpts; original filtering can produce the same evidence")
    elif suspect:
        status, reason = "inconclusive", "spectral evidence differs between channels or sections"
    else:
        status, reason = "clear", "no suspicious sustained cutoff detected; source history is unverified"
    cutoffs = [o["cutoff_hz"] for o in active if o["cutoff_hz"] is not None]
    cutoff = int(np.median(cutoffs)) if cutoffs else None
    bandwidth = int(np.median([o["bandwidth_hz"] for o in active])) if active else None
    return {"version": DETECTOR_VERSION, "status": status, "reason": reason, "probe": asdict(pr),
            "cutoff_hz": cutoff, "bandwidth_hz": bandwidth, "observations": observations,
            "warnings": warnings, "sampled_significant_bits": used_bits or None}


def band_levels_db(path: Path, window_s: int = 60, duration_s: float | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Diagnostic middle-window spectrum, combining channel powers without cancellation."""
    pr = probe(path)
    start, length = _window(path, window_s, duration_s or pr.duration_s)
    pcm = _decode(path, pr, start, length)
    spectra = [_bands(pcm[:, c], pr.sample_rate) for c in range(pr.channels)]
    valid = [(hz, bands) for hz, bands in spectra if len(bands)]
    if not valid:
        raise VerifyError("insufficient active audio to analyze")
    return valid[0][0], 10 * np.log10(np.mean([b.mean(axis=0) for _, b in valid], axis=0) + 1e-30)


def spectral_cutoff_hz(path: Path, window_s: int = 60, duration_s: float | None = None) -> int:
    """Compatibility summary: detected edge, otherwise measured bandwidth; 0 if unavailable."""
    result = analyze(path, window_s=window_s)
    return result["cutoff_hz"] or result["bandwidth_hz"] or 0


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
    report = {"version": DETECTOR_VERSION, "probe": asdict(pr), "integrity": "not_checked"}
    if pr.fmt == "mp3" and pr.bitrate_kbps < MIN_MP3_BITRATE:
        report.update(status="policy_rejected", reason=f"bitrate {pr.bitrate_kbps} kbps below {MIN_MP3_BITRATE}")
    else:
        # A spectrum excerpt is not an integrity check. Decode the entire selected
        # stream, fail on decoder errors, and discard PCM without buffering it.
        _run(["ffmpeg", "-v", "error", "-xerror", "-err_detect", "explode", "-i", str(path),
              "-map", "0:a:0", "-vn", "-f", "null", "-"])
        report = analyze(path, pr)
        report["integrity"] = "full_decode_passed"
    png = spectrogram_png(path, spectrogram_dir / f"{name or path.stem}.png", duration_s=pr.duration_s)
    return Verdict(report["status"] == "clear", pr.fmt, pr.bitrate_kbps,
                   report.get("cutoff_hz") or report.get("bandwidth_hz") or 0,
                   report["reason"], png, bit_depth=pr.bit_depth, sample_rate=pr.sample_rate, analysis=report)
