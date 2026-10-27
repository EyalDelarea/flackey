"""What one pass over a file's decoded samples says about it, family by family (issue #97).

Nothing here decides anything. `verify` streams the whole file through ffmpeg once, at its native rate
and depth, and hands the PCM to an `Analyzer`, which measures as it goes:

- integrity: the decoded sample count and the MD5 of the samples, for comparing against what the
  container promised (`integrity`);
- spectral edge: per-channel, whole-file power spectrum, linear power averaged before any logarithm, an
  edge located to the bin rather than to a 250 Hz band, and how often any frame reaches above it;
- quantisation: the loudest windows of the file, for Derrien's MP3 and AAC detectors (`quant`), and a
  steady stretch from each of 32 parts of it, for the Opus detector (`opus`);
- bit usage: how many of the declared bits ever carry anything.

`container_facts` reads what the container itself declares, before or without decoding: FLAC STREAMINFO
(block size, sample count, MD5, vendor), an MP3's Xing/Info header, a WAV/AIFF's data length. A FLAC
written by ffmpeg from a decoded stream carries ffmpeg's vendor string and odd block sizes (issue #97's
corpus trap), which is recorded as a fact and never taken for a verdict: ffmpeg writes those from a
genuine AIFF too.
"""
from __future__ import annotations

import hashlib
import math
from pathlib import Path

import numpy as np

from . import opus, quant

EVIDENCE_VERSION = "2.0"
BAND_HZ = 250
ACTIVE_MS = 1e-7              # a frame quieter than -70 dBFS (mean square) says nothing about its spectrum
EDGE_SEARCH_FROM_HZ = 8_000
EDGE_DROP_DB = 20             # an encoder lowpass falls at least this far within 500 Hz
EDGE_FLOOR_DB = 45            # below the loudest 1-8 kHz band by more than this, a "drop" is noise floor
ABOVE_MARGIN_HZ = 300         # "content above the edge" is looked for this far above it
ABOVE_RISE_DB = 12            # a frame whose level above the edge is within this of its level below
SMOOTH_HZ = 60                # the fine spectrum is smoothed over about this much before locating an edge
MIN_ACTIVE_FRAMES = 20        # about 2 s of sound with something in it
# The quantisation grid is also looked for in each of this many equal stretches of the file, once the whole
# file shows one at least this clearly (issue #97): a grid in a few stretches is a sample, not a transcode
QUANT_SEGMENTS = 8
QUANT_SEGMENT_FROM_Z = 8.0
# The share of stretches carrying it from which the whole file counts as carrying it; the per-stretch search
# stops there, since the rest cannot change that (`verify` judges with the same share)
QUANT_COVER_MIN = 0.5
QUANT_SEGMENT_FALLBACK_S = 30  # the stretch length when the file's length is not known up front
# Opus's own lowpass is 20 kHz. When the fast (ffmpeg/libopus) search finds no Opus traces in a file whose
# edge sits there, the general search -- every alignment, several times the cost -- is run as well.
OPUS_GENERAL_EDGE_HZ = (19_000, 21_000)


def frame_length(sample_rate: int) -> int:
    """About 10.8 Hz per bin at any rate: 4096 at 44.1/48 kHz, 8192 at 88.2/96 kHz."""
    return 1 << max(10, round(math.log2(4096 * sample_rate / 44_100)))


class Analyzer:
    """Fed the raw little-endian PCM of one decode, in chunks of any size."""

    def __init__(self, sample_rate: int, channels: int, bits: int, *, quant_detectors: bool,
                 expected_samples: int | None = None):
        self.sr, self.ch, self.bits = sample_rate, channels, bits
        self.width = 2 if bits == 16 else 3
        self.md5 = hashlib.md5()
        self.samples = 0                       # per channel
        self._carry = b""
        self._pending = np.empty((0, channels), dtype=np.float32)
        self.n = frame_length(sample_rate)
        self._hann = np.hanning(self.n).astype(np.float32)
        self._norm = float(np.sum(self._hann ** 2))
        freqs = np.fft.rfftfreq(self.n, 1 / sample_rate)
        self.freqs = freqs
        self._band_of = np.minimum((freqs // BAND_HZ).astype(int), int(sample_rate / 2 // BAND_HZ))
        self.n_bands = int(self._band_of.max()) + 1
        onehot = np.zeros((len(freqs), self.n_bands))
        onehot[np.arange(len(freqs)), self._band_of] = 1
        self._to_bands = onehot / onehot.sum(axis=0)              # bins -> mean power per 250 Hz band
        self._power = np.zeros((channels, len(freqs)))          # summed over active frames
        self._active = np.zeros(channels, dtype=int)
        self._frames: list[np.ndarray] = []                     # (channels, bands) dB per frame, float32
        self._or = 0                                            # every sample's bits OR-ed together
        self._peak = 0
        self.quant = quant_detectors and round(sample_rate / 1000) in quant.AAC_BANDS_LONG
        if self.quant:
            seg = (-(-expected_samples // QUANT_SEGMENTS) if expected_samples
                   else QUANT_SEGMENT_FALLBACK_S * sample_rate)
            self._aac = quant.TopRegions(quant.AAC_LONG, quant.AAC_SPAN, quant.AAC_WINDOWS, channels, seg,
                                         clip=quant.CLIP)
            self._mp3 = quant.TopRegions(quant.MP3_BLOCK, quant.MP3_SPAN, quant.MP3_WINDOWS, channels, seg,
                                         clip=quant.CLIP)
        self._opus = (opus.Chunks(sample_rate, channels, expected_samples)
                      if self.quant and sample_rate in opus.RATES and expected_samples else None)

    def feed(self, raw: bytes) -> None:
        self.md5.update(raw)
        data = self._carry + raw
        step = self.width * self.ch
        usable = len(data) - len(data) % step
        self._carry = data[usable:]
        if not usable:
            return
        if self.width == 2:
            ints = np.frombuffer(data[:usable], dtype="<i2").astype(np.int32)
        else:
            b = np.frombuffer(data[:usable], dtype=np.uint8).reshape(-1, 3).astype(np.int32)
            ints = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
            ints = np.where(ints & 0x800000, ints - 0x1000000, ints)
        self._or |= int(np.bitwise_or.reduce(ints)) if len(ints) else 0
        self._peak = max(self._peak, int(np.abs(ints).max()) if len(ints) else 0)
        x = (ints.astype(np.float32) / float(1 << (8 * self.width - 1))).reshape(-1, self.ch)
        self.samples += len(x)
        if self.quant:
            self._aac.feed(x)
            self._mp3.feed(x)
        if self._opus is not None:
            self._opus.feed(x)
        self._spectrum(x)

    def _spectrum(self, x: np.ndarray) -> None:
        buf = np.concatenate([self._pending, x])
        whole = len(buf) // self.n
        self._pending = buf[whole * self.n:]
        if not whole:
            return
        frames = buf[:whole * self.n].reshape(whole, self.n, self.ch).transpose(0, 2, 1)   # (F, ch, n)
        ms = np.mean(frames.astype(np.float64) ** 2, axis=2)                           # (F, ch)
        power = np.abs(np.fft.rfft(frames * self._hann, axis=2)) ** 2 / self._norm      # (F, ch, bins)
        active = ms > ACTIVE_MS
        self._power += np.einsum("fc,fcb->cb", active, power)
        self._active += active.sum(axis=0)
        db = 10 * np.log10(power @ self._to_bands + 1e-30)
        db[~active] = np.nan
        self._frames.append(db.astype(np.float32))

    def finish(self) -> dict:
        frames = np.concatenate(self._frames) if self._frames else np.empty((0, self.ch, self.n_bands))
        channels = [self._channel(c, frames[:, c, :]) for c in range(self.ch)]
        out = {
            "samples": self.samples,
            "md5": self.md5.hexdigest(),
            "spectral": summarise_channels(channels),
            "bits": self._bit_usage(),
        }
        if self.quant:
            out["quant"] = {"aac": self._detect(self._aac, quant.detect_aac),
                            "mp3": self._detect(self._mp3, quant.detect_mp3),
                            "opus": self._detect_opus(out["spectral"])}
        return out

    def _detect_opus(self, spectral: dict) -> dict | None:
        if self._opus is None:
            return None
        regions = self._opus.regions()
        found = opus.detect_opus(regions, self.sr)
        if found is None:
            return None
        lo, hi = OPUS_GENERAL_EDGE_HZ
        edge = spectral.get("edge_hz")
        if found.score < opus.OPUS_STRONG and edge is not None and lo <= edge <= hi:
            wider = opus.detect_opus(regions, self.sr, general=True)
            if wider is not None and wider.score > found.score:
                found = wider
        return found.to_dict()

    def _detect(self, top: quant.TopRegions, detect) -> dict | None:
        """One detector over the whole file's loudest windows and, when that finds a grid, over each
        segment's: `segments` holds each one's z, so the rule can ask how much of the file carries it, and
        `stretches` how many could be measured. Each stretch costs a whole search, so once QUANT_COVER_MIN
        of them carry it the rest are not searched: `segments` is then cut short, and the share a floor.

        Nothing is measured from fewer than the detector's full count of windows: a master clipped almost
        throughout leaves only a stray unclipped window or two, and one window's best alignment can stand out
        by chance (issue #97)."""
        regions = top.regions()
        if len(regions) < top.count:
            return None
        found = detect(regions, self.sr)
        if found is None:
            return None
        out = found.to_dict()
        if found.z >= QUANT_SEGMENT_FROM_Z:
            stretches = top.segments()
            enough = math.ceil(QUANT_COVER_MIN * len(stretches))
            zs: list[float] = []
            for s in stretches:
                part = detect(top.regions(s), self.sr)
                zs.append(round(part.z, 1) if part is not None else 0.0)
                if sum(z >= QUANT_SEGMENT_FROM_Z for z in zs) >= enough:
                    break
            out["segments"], out["stretches"] = zs, len(stretches)
        return out

    def _bit_usage(self) -> dict:
        if not self._or:
            return {"declared": self.bits, "used": 0}
        trailing = (self._or & -self._or).bit_length() - 1
        return {"declared": self.bits, "used": 8 * self.width - trailing,
                "peak_dbfs": round(20 * math.log10(self._peak / (1 << (8 * self.width - 1))), 1)}

    def _channel(self, c: int, frame_db: np.ndarray) -> dict:
        active = int(self._active[c])
        out: dict = {"active_frames": active, "edge_hz": None, "edge_drop_db": None, "bandwidth_hz": None,
                     "frames_above_edge": None}
        if active < MIN_ACTIVE_FRAMES:
            return out
        return {**out, **find_edge(self._power[c] / active, self.freqs, frame_db, self.sr)}


def _band_levels(power: np.ndarray, freqs: np.ndarray, sr: int) -> tuple[np.ndarray, np.ndarray]:
    band_of = np.minimum((freqs // BAND_HZ).astype(int), int(sr / 2 // BAND_HZ))
    sums = np.bincount(band_of, weights=power)
    counts = np.bincount(band_of)
    starts = np.arange(len(sums)) * BAND_HZ
    return starts, 10 * np.log10(sums / np.maximum(counts, 1) + 1e-30)


def find_edge(power: np.ndarray, freqs: np.ndarray, frame_db: np.ndarray, sr: int) -> dict:
    """The highest frequency where the mean spectrum falls by EDGE_DROP_DB within 500 Hz and stays down,
    located at the half-drop point of the smoothed fine spectrum; how far the content above it sits below
    the content under it; and in what share of sounding frames anything reaches above it.

    `power` is the linear mean power per FFT bin over the file's sounding frames; `frame_db` the per-frame
    250 Hz band levels (NaN for a silent frame)."""
    starts, db = _band_levels(power, freqs, sr)
    nyq = sr / 2
    ref_band = (starts >= 1000) & (starts < min(8000, nyq))
    reference = float(db[ref_band].max()) if ref_band.any() else float(db.max())
    audible = np.flatnonzero(db >= reference - 60)
    bandwidth = int(min(nyq, starts[audible[-1]] + BAND_HZ)) if len(audible) else None
    result: dict = {"bandwidth_hz": bandwidth, "reference_db": round(reference, 1)}
    edge_band = None
    # Boundary b sits between band b-1 and band b. Drop measured across one band (b-1 vs b+1, 500 Hz)
    for b in range(len(starts) - 2, 0, -1):
        if starts[b] < EDGE_SEARCH_FROM_HZ:
            break
        before = db[b - 1]
        if before < reference - EDGE_FLOOR_DB or before - db[b + 1] < EDGE_DROP_DB:
            continue
        tail = db[b + 1:]
        if before - float(np.quantile(tail, 0.9)) < EDGE_DROP_DB:
            continue                      # a notch that recovers above, not an edge
        edge_band = b
        break
    if edge_band is None:
        return result
    before = float(db[edge_band - 1])
    after = float(np.median(db[edge_band + 1:]))
    # Locate the step on the fine spectrum: the first bin from below where it crosses the half-drop level
    k = max(1, round(SMOOTH_HZ / (freqs[1] - freqs[0])))
    fine = 10 * np.log10(np.convolve(power, np.ones(k) / k, mode="same") + 1e-30)
    lo_hz, hi_hz = starts[edge_band - 1], starts[edge_band] + 2 * BAND_HZ
    idx = np.flatnonzero((freqs >= lo_hz) & (freqs <= hi_hz))
    half = (before + after) / 2
    below = idx[fine[idx] < half]
    if len(below) and below[0] > 0:
        i = int(below[0])
        f0, f1, d0, d1 = freqs[i - 1], freqs[i], fine[i - 1], fine[i]
        edge = float(f0 + (f1 - f0) * (d0 - half) / (d0 - d1)) if d0 != d1 else float(f1)
    else:
        edge = float(starts[edge_band])
    result.update(edge_hz=round(edge), edge_drop_db=round(before - after, 1))
    # In each sounding frame, does anything reach above the edge? Compared with the same frame just below
    lo = int((edge - ABOVE_MARGIN_HZ - 1000) // BAND_HZ)
    hi = int((edge + ABOVE_MARGIN_HZ) // BAND_HZ) + 1
    if len(frame_db) and lo >= 0 and hi + 4 <= frame_db.shape[1]:
        # fmax skips NaN (a silent frame) without nanmax's warning; an all-silent row stays NaN
        under = np.fmax.reduce(frame_db[:, lo:lo + 4], axis=1)
        over = np.fmax.reduce(frame_db[:, hi:hi + 4], axis=1)
        sounding = np.isfinite(under) & (under >= reference - EDGE_FLOOR_DB)
        if sounding.any():
            reach = over[sounding] >= under[sounding] - ABOVE_RISE_DB
            result["frames_above_edge"] = round(float(np.mean(reach)), 4)
    return result


def summarise_channels(channels: list[dict]) -> dict:
    """The file's spectral facts: each channel's, and the most suspicious channel's edge as the file's.
    A channel with too little sound to judge does not vote."""
    judged = [c for c in channels if c["active_frames"] >= MIN_ACTIVE_FRAMES]
    edged = [c for c in judged if c.get("edge_hz") is not None]
    worst = min(edged, key=lambda c: c["edge_hz"]) if edged else None
    return {
        "channels": channels,
        "judged": len(judged),
        "edge_hz": worst["edge_hz"] if worst else None,
        "edge_drop_db": worst["edge_drop_db"] if worst else None,
        "frames_above_edge": worst.get("frames_above_edge") if worst else None,
        "edged_channels": len(edged),
        "bandwidth_hz": max((c["bandwidth_hz"] or 0 for c in judged), default=None) or None,
    }


# --- what the container declares -----------------------------------------------------------------------

def container_facts(path: Path, fmt: str) -> dict:
    """What the file says about itself, read without decoding it. Unreadable headers are reported, not
    raised: the decode is what judges whether the file can be read through."""
    try:
        if fmt == "flac":
            from mutagen.flac import FLAC
            f = FLAC(path)
            i = f.info
            vendor = f.tags.vendor if f.tags is not None else None
            return {"container": "flac", "min_blocksize": i.min_blocksize, "max_blocksize": i.max_blocksize,
                    "total_samples": i.total_samples, "md5": f"{i.md5_signature:032x}" if i.md5_signature else None,
                    "bits": i.bits_per_sample, "vendor": vendor,
                    "ffmpeg_made": bool(vendor and vendor.startswith("Lavf")),
                    "reference_blocksize": i.max_blocksize in (4096, 4608)}
        if fmt in ("wav", "aiff"):
            if fmt == "wav":
                from mutagen.wave import WAVE as Reader
            else:
                from mutagen.aiff import AIFF as Reader
            i = Reader(path).info
            return {"container": fmt, "expected_samples": round(i.length * i.sample_rate)}
        if fmt == "mp3":
            from mutagen.mp3 import MP3
            i = MP3(path).info
            return {"container": "mp3", "expected_samples": round(i.length * i.sample_rate),
                    "length_header": _has_xing(path, i.frame_offset), "bitrate_mode": str(i.bitrate_mode)}
    except Exception as e:  # noqa: BLE001 - mutagen raises many types on a malformed header
        return {"container": fmt, "unreadable": f"{type(e).__name__}: {e}"[:200]}
    return {"container": fmt}


def _has_xing(path: Path, frame_offset: int) -> bool:
    with open(path, "rb") as f:
        f.seek(frame_offset)
        head = f.read(200)
    return b"Xing" in head or b"Info" in head


MP3_LENGTH_SLACK_S = 1.0      # encoder delay and padding, and a few frames a glitch costs: a cut-off transfer loses far more


def integrity(facts: dict, decoded: int, md5: str, sample_rate: int, native_depth: bool) -> dict:
    """Which of the container's promises the decode kept, and which could not be checked. `failed` names
    the first broken promise, or is None."""
    out: dict = {"decoded_samples": decoded, "checked": [], "failed": None}
    kind = facts.get("container")
    if kind == "flac":
        total = facts.get("total_samples") or 0
        if total:
            out["checked"].append("sample_count")
            if decoded != total:
                out["failed"] = f"decoded {decoded} samples, STREAMINFO says {total}"
        else:
            out["sample_count"] = "absent"
        if facts.get("md5") is None:
            out["md5"] = "absent"
        elif not native_depth:
            out["md5"] = "not checked at this bit depth"
        else:
            out["checked"].append("md5")
            if out["failed"] is None and md5 != facts["md5"]:
                out["failed"] = "the decoded audio does not match the MD5 in STREAMINFO"
        out["checked"].append("frame_crc")
    elif kind in ("wav", "aiff") and "expected_samples" in facts:
        out["checked"].append("data_length")
        if abs(decoded - facts["expected_samples"]) > quant.MP3_BLOCK:
            out["failed"] = f"decoded {decoded} samples, the data chunk holds {facts['expected_samples']}"
    elif kind == "mp3":
        if facts.get("length_header"):
            out["checked"].append("length_header")
            if decoded < facts["expected_samples"] - MP3_LENGTH_SLACK_S * sample_rate:
                out["failed"] = (f"decoded {decoded / sample_rate:.1f} s, the length header says "
                                 f"{facts['expected_samples'] / sample_rate:.1f} s")
        else:
            out["length_header"] = "absent"
    return out
