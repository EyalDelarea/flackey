"""Traces of an earlier MP3 or AAC encode in decoded PCM: Derrien's quantisation-grid detectors (issue #97).

A lossy encoder quantises its transform coefficients on a power-law grid. Decode that file to PCM and
the samples look like any other music, but run the same transform again *at the encoder's frame
alignment* and the coefficients fall back near that grid: the rounding error is far smaller than
genuine audio gives. The detectors below search every alignment, a few scalefactors and every block
shape (long, start, stop, short), count how often a band's rounding error falls below a threshold set for
a 1 % false-alarm rate, and report the highest rate found. Bandwidth plays no part, so this is the one
check that can see an AAC 256 or a LAME V0 transcode, and the one a resampler's or a mastering
engineer's steep lowpass cannot fool.

Ported from the original MATLAB reference implementation:

    Olivier Derrien, CNRS/PRISM, Marseille, France: "True-lossless Audio Detector" for MPEG AAC
    (version 1.4, June 2018) and for MP3 (version 1.1, July 2018).
    https://github.com/craft-and-code/lac-transcoded

    MIT License. Copyright (c) 2018 Olivier Derrien.

    Permission is hereby granted, free of charge, to any person obtaining a copy of this software and
    associated documentation files (the "Software"), to deal in the Software without restriction,
    including without limitation the rights to use, copy, modify, merge, publish, distribute,
    sublicense, and/or sell copies of the Software, and to permit persons to whom the Software is
    furnished to do so, subject to the following conditions:

    The above copyright notice and this permission notice shall be included in all copies or
    substantial portions of the Software.

    THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT
    NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
    NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES
    OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN
    CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.

What differs from the MATLAB, and why:
- The loops over alignments, windows, bands and scalefactors are numpy array operations; the arithmetic
  is the reference's band for band. MATLAB's `round` is half away from zero, and every value rounded
  here is non-negative, so it is `floor(x + 0.5)`.
- The reference AAC script runs 64 windows x 64 scalefactors against the significance threshold its own
  comment gives for 8 x 8. Here the count and its threshold are always a pair from that comment's table
  (`AAC_SIGNIFICANCE`).
- The reference models AAC's sine window only. ffmpeg's own `aac` encoder uses the Kaiser-Bessel-derived
  window, which the sine model barely sees (likelihood 0.020 where KBD gives 0.106 on the same transcode,
  issue #97), so the AAC search runs once per window family and keeps the stronger.
- Every result also carries `z`: how far the best alignment stands above all the others, in robust
  standard deviations. A genuine file has no preferred alignment, so its best is just the largest of
  ~1000 similar values; a transcode's best is the encoder's frame grid and stands far apart. It scales
  with the material, which a fixed threshold does not.
- The loudest windows are chosen by the caller from the whole file (`TopRegions`), so a ten-minute track
  is never held in memory for this.
- `erfinv` is computed by bisection on `math.erf`: SciPy for one function is a heavy thing to ship.
"""
from __future__ import annotations

import heapq
import math
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass

import numpy as np

# --- shared ----------------------------------------------------------------------------------------------

REF_PROBA = 1e-2         # false-alarm rate each band's threshold is set for
MU = 1 / 12              # mean squared rounding error of a uniformly distributed value
CHUNK = 128              # alignments evaluated at once: bounds the (alignments, scalefactors, coefs) arrays


def _erfinv(y: float) -> float:
    lo, hi = -6.0, 6.0
    for _ in range(100):
        mid = (lo + hi) / 2
        if math.erf(mid) < y:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def _threshold(width: int) -> float:
    """The rounding-error energy below which a band of `width` coefficients counts as quantised: the lower
    1 % tail for uniformly distributed values (the reference's tau_sb)."""
    sigma = math.sqrt(1 / width / 180)
    return MU - math.sqrt(2) * sigma * _erfinv(math.erf(MU / sigma / math.sqrt(2)) - 2 * REF_PROBA)


def _mdct(x: np.ndarray) -> np.ndarray:
    """MDCT along the last axis, the reference's FFT formulation: 2N samples in, N coefficients out."""
    n2 = x.shape[-1]
    n = n2 // 2
    t = np.arange(n2)
    f = np.arange(n)
    X = np.fft.fft(x * np.exp(-1j * np.pi * t / 2 / n), axis=-1)[..., :n]
    return np.real(X * np.exp(-1j * np.pi * (f + 0.5) * (n + 1) / 2 / n))


def _mdct_basis(window: np.ndarray) -> np.ndarray:
    """The same transform as a (2N, N) matrix with the window folded in, for the short MP3 transforms
    applied to thousands of 36- and 12-sample vectors at once."""
    return _mdct(np.eye(len(window)) * window[:, None])


class _Bands:
    """Contiguous scalefactor bands over a coefficient vector, each with its own threshold. `per_coef` is
    how many values each coefficient index stands for: the short windows' coefficients are laid out
    coefficient-major, so one band of a short block covers that many windows."""

    def __init__(self, edges: list[tuple[int, int]], per_coef: int = 1):
        self.lo = edges[0][0] * per_coef
        self.hi = edges[-1][1] * per_coef
        self.starts = np.array([(lo - edges[0][0]) * per_coef for lo, _ in edges])
        self.widths = np.array([(hi - lo) * per_coef for lo, hi in edges])
        self.tau = np.array([_threshold(int(w)) for w in self.widths])
        self.n = len(edges)

    def counts(self, coefs: np.ndarray, sf: np.ndarray, offset: float) -> np.ndarray:
        """Per row (one alignment), how many (band, scalefactor) pairs leave a rounding error below the
        band's threshold. `offset` is the reference's 60 (AAC) or 80 (MP3)."""
        a = np.abs(coefs[:, self.lo:self.hi]).astype(np.float32)
        with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
            sf_max = 4 * np.log2(np.maximum.reduceat(a, self.starts, axis=1)) + 16 / 3 + offset   # (O, B)
            scale = np.exp2(-3 / 16 * (sf[None, :, None] * sf_max[:, None, :] - offset))          # (O, K, B)
            scaled = (a ** 0.75)[:, None, :] * np.repeat(scale.astype(np.float32), self.widths, axis=2)
            scaled -= np.floor(scaled + 0.5)
            scaled *= scaled
            err = np.add.reduceat(scaled, self.starts, axis=2)                                   # (O, K, B)
            return np.sum(err / self.widths < self.tau, axis=(1, 2))     # NaN (a silent band) never counts


@dataclass
class QuantResult:
    codec: str                  # "aac" or "mp3": the encoder whose grid was searched for
    likelihood: float           # the highest rate of below-threshold bands over every alignment
    threshold: float | None     # the reference's significance threshold for this configuration, if any
    significant: bool | None    # likelihood > threshold: traces of an earlier encode, by the reference
    z: float                    # the best alignment above the rest, in robust standard deviations
    offset: int                 # the alignment that scored it, in samples
    mode: str                   # "LR", "MS" or "mono": the channel pairing that scored it
    window: str                 # AAC: "sine" or "kbd", the window family that scored it; "" for MP3
    windows: int                # how many of the loudest windows were analysed

    def to_dict(self) -> dict:
        d = asdict(self)
        d["likelihood"] = round(self.likelihood, 4)
        d["z"] = round(self.z, 1)
        return d


def _peak(p: np.ndarray) -> tuple[int, float, float]:
    """The best alignment, its rate, and how far it stands above the others (median and MAD of every
    alignment more than two samples away from it)."""
    best = int(np.argmax(p))
    rest = np.delete(p, np.arange(max(0, best - 2), min(len(p), best + 3)))
    med = float(np.median(rest))
    mad = float(np.median(np.abs(rest - med))) * 1.4826
    return best, float(p[best]), (float(p[best]) - med) / max(mad, 1e-4)


def _per_window(shape_counts: list[np.ndarray], totals: list[int]) -> tuple[np.ndarray, np.ndarray]:
    """The block shape with the highest rate wins the window, per alignment (ties to the first, as
    MATLAB's `max`); only its counts are kept."""
    counts = np.stack(shape_counts, axis=1)
    t = np.array(totals, dtype=float)
    idx = np.argmax(counts / t[None, :], axis=1)
    return counts[np.arange(len(idx)), idx], t[idx]


# --- choosing the loudest windows ------------------------------------------------------------------------

class TopRegions:
    """The `count` loudest windows of a stream fed in chunks, keeping only those windows' samples.

    The reference measures window `w`'s energy over 2 x `block` samples of the mid channel from (w-1) x
    `block`, and analyses `span` samples from the same start (the window plus every alignment the search
    tries). Only windows whose span lies wholly inside the stream are candidates."""

    def __init__(self, block: int, span: int, count: int, channels: int):
        self.block, self.span, self.count, self.channels = block, span, count, channels
        self._buf = np.empty((0, channels), dtype=np.float32)
        self._buf_start = 0                     # absolute index of _buf[0]
        self._next = 0                          # next window start to evaluate
        self._heap: list[tuple[float, int, np.ndarray]] = []

    def feed(self, chunk: np.ndarray) -> None:
        self._buf = np.concatenate([self._buf, chunk.astype(np.float32, copy=False)])
        end = self._buf_start + len(self._buf)
        while self._next + self.span <= end:
            rel = self._next - self._buf_start
            mid = self._buf[rel:rel + 2 * self.block].sum(axis=1, dtype=np.float64)
            item = (float(np.dot(mid, mid)), -self._next)
            if len(self._heap) < self.count or item > self._heap[0][:2]:
                entry = (*item, self._buf[rel:rel + self.span].copy())
                if len(self._heap) < self.count:
                    heapq.heappush(self._heap, entry)
                else:
                    heapq.heapreplace(self._heap, entry)
            self._next += self.block
        drop = self._next - self._buf_start
        if drop > 0:
            self._buf = self._buf[drop:]
            self._buf_start += drop

    def regions(self) -> np.ndarray:
        """(windows, span, channels) as float64, loudest first; fewer than `count` for a short stream."""
        items = sorted(self._heap, key=lambda it: (it[0], it[1]), reverse=True)
        if not items:
            return np.empty((0, self.span, self.channels))
        return np.stack([it[2] for it in items]).astype(np.float64)


def regions_of(x: np.ndarray, block: int, span: int, count: int) -> np.ndarray:
    """`TopRegions` over a whole array at once (tests, and short files)."""
    x = x.reshape(len(x), -1)
    top = TopRegions(block, span, count, x.shape[1])
    top.feed(x)
    return top.regions()


def _energy_db(regions: np.ndarray, block: int) -> float:
    return float(np.mean([10 * math.log10(float(np.dot(r[:2 * block], r[:2 * block])) + 1e-300)
                          for r in regions]))


def _band_edges(low: list[int], top: int) -> list[tuple[int, int]]:
    """The reference's 1-based band start indices as 0-based [lo, hi) slices, the last ending at `top`."""
    starts = [s - 1 for s in low]
    return list(zip(starts, starts[1:] + [top]))


# --- AAC ---------------------------------------------------------------------------------------------------

AAC_LONG = 1024
AAC_SHORT = 128
AAC_SHORTS = AAC_LONG // AAC_SHORT
AAC_SHORT_OFFSET = (AAC_LONG - AAC_SHORT) // 2
AAC_SPAN = 3 * AAC_LONG                  # one 2048-sample window plus every alignment 0..1023
# The reference's table: windows (= scalefactors) and the significance threshold for that many
AAC_SIGNIFICANCE = {8: 0.031, 16: 0.019, 32: 0.0145, 64: 0.0125}
AAC_WINDOWS = 16
_AAC_LOW = [1, 5, 9, 13, 17, 21, 25, 29, 33, 37, 41, 49, 57, 65, 73, 81, 89, 97, 109, 121, 133, 145, 161,
            177, 197, 217, 241, 265, 293, 321, 353, 385, 417, 449, 481, 513, 545, 577, 609, 641, 673, 705,
            737, 769, 801, 833, 865, 897, 929]
AAC_BANDS_LONG = {32: _AAC_LOW + [961, 993], 44: _AAC_LOW, 48: _AAC_LOW}
AAC_BANDS_SHORT = [1, 5, 9, 13, 17, 21, 29, 37, 45, 57, 69, 81, 97, 113]


def _sine(n: int) -> np.ndarray:
    return np.sin(np.pi * (2 * np.arange(n) + 1) / n / 2)


def _kbd(n: int, alpha: float) -> np.ndarray:
    """The Kaiser-Bessel-derived window of ISO 14496-3 (alpha 4 for long blocks, 6 for short)."""
    k = np.kaiser(n // 2 + 1, np.pi * alpha)
    half = np.sqrt(np.cumsum(k[:n // 2]) / k.sum())
    return np.concatenate([half, half[::-1]])


def _aac_shapes(long_: np.ndarray, short: np.ndarray) -> tuple[np.ndarray, ...]:
    """long, start, stop and short windows built from one family's long and short halves."""
    left, right = long_[:AAC_LONG], long_[AAC_LONG:]
    s_left, s_right = short[:AAC_SHORT], short[AAC_SHORT:]
    pad1, pad0 = np.ones(AAC_SHORT_OFFSET), np.zeros(AAC_SHORT_OFFSET)
    return (long_, np.concatenate([left, pad1, s_right, pad0]), np.concatenate([pad0, s_left, pad1, right]),
            short)


AAC_FAMILIES = {
    "sine": _aac_shapes(_sine(2 * AAC_LONG), _sine(2 * AAC_SHORT)),
    "kbd": _aac_shapes(_kbd(2 * AAC_LONG, 4), _kbd(2 * AAC_SHORT, 6)),
}


class _AAC:
    def __init__(self, khz: int, nb: int, family: str = "sine"):
        self.long = _Bands(_band_edges(AAC_BANDS_LONG[khz], AAC_LONG)[:-1])     # the last band is skipped
        self.short = _Bands(_band_edges(AAC_BANDS_SHORT, AAC_SHORT)[:-1], AAC_SHORTS)
        self.sf = np.linspace(0.3, 0.7, nb)
        self.wins = AAC_FAMILIES[family]

    def rates(self, regions: np.ndarray) -> np.ndarray:
        """Per alignment, the rate over every window. regions: (windows, AAC_SPAN) of one signal."""
        wl, ws, we, wsh = self.wins
        k = len(self.sf)
        totals = [self.long.n * k] * 3 + [self.short.n * k]
        count = np.zeros(AAC_LONG)
        total = np.zeros(AAC_LONG)
        for r in regions:
            segs_all = np.lib.stride_tricks.sliding_window_view(r, 2 * AAC_LONG)[:AAC_LONG]   # (O, 2048)
            for c0 in range(0, AAC_LONG, CHUNK):
                segs = segs_all[c0:c0 + CHUNK]
                o = len(segs)
                shorts = np.stack([_mdct(segs[:, n1:n1 + 2 * AAC_SHORT] * wsh)
                                   for n1 in AAC_SHORT_OFFSET + AAC_SHORT * np.arange(AAC_SHORTS)], axis=2)
                shape_counts = [self.long.counts(_mdct(segs * w), self.sf, 60) for w in (wl, ws, we)]
                shape_counts.append(self.short.counts(shorts.reshape(o, -1), self.sf, 60))
                c, t = _per_window(shape_counts, totals)
                count[c0:c0 + o] += c
                total[c0:c0 + o] += t
        return count / np.maximum(total, 1)


def detect_aac(regions: np.ndarray, sample_rate: int, nb: int = AAC_WINDOWS) -> QuantResult | None:
    """AAC traces in `regions` (windows, AAC_SPAN, channels), the loudest windows of the mid channel.
    None when the sample rate has no AAC band table (only 32, 44.1 and 48 kHz) or nothing was long enough."""
    khz = round(sample_rate / 1000)
    if khz not in AAC_BANDS_LONG or nb not in AAC_SIGNIFICANCE or not len(regions):
        return None
    regions = regions[:nb]
    thr = AAC_SIGNIFICANCE[nb]
    if regions.shape[2] == 1:
        signals = {"mono": regions[:, :, 0]}
    else:
        left, right = regions[:, :, 0], regions[:, :, 1]
        mid, side = left + right, left - right
        # The reference analyses the louder of L and R, and the louder of M and S
        signals = {"LR": left if _energy_db(left, AAC_LONG) >= _energy_db(right, AAC_LONG) else right,
                   "MS": mid if _energy_db(mid, AAC_LONG) >= _energy_db(side, AAC_LONG) else side}
    jobs = [(family, mode, sig) for family in AAC_FAMILIES for mode, sig in signals.items()]
    # Independent searches, and numpy releases the GIL for the heavy part, so they run side by side
    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
        curves = list(pool.map(lambda job: _AAC(khz, nb, job[0]).rates(job[2]), jobs))
    best = None
    for (family, mode, _), curve in zip(jobs, curves):
        off, p, z = _peak(curve)
        if best is None or p > best.likelihood:
            best = QuantResult("aac", p, thr, p > thr, z, off, mode, family, len(regions))
    return best


# --- MP3 ---------------------------------------------------------------------------------------------------

MP3_SUBBANDS = 32
MP3_GRANULE = 18                          # subband samples per granule
MP3_BLOCK = MP3_SUBBANDS * MP3_GRANULE    # 576 PCM samples per granule
MP3_SPAN = 4 * MP3_BLOCK                  # three granules plus every alignment 0..575
MP3_WINDOWS = 16
MP3_SIGNIFICANCE = {8: 0.025}             # the reference gives a threshold for 8 windows only
MP3_HISTORY = (MP3_GRANULE - 16 + 1) * MP3_SUBBANDS      # 96: where the reference's PQMF buffer starts
MP3_SHORT_OFFSET = 12
MP3_BANDS_LONG = {
    32: ([1, 5, 9, 13, 17, 21, 25, 31, 37, 45, 55, 67, 83, 103, 127, 157, 195, 241, 297, 365, 449], 550),
    44: ([1, 5, 9, 13, 17, 21, 25, 31, 37, 45, 53, 63, 75, 91, 111, 135, 163, 197, 239, 289, 343], 418),
    48: ([1, 5, 9, 13, 17, 21, 25, 31, 37, 43, 51, 61, 73, 89, 107, 129, 157, 191, 231, 277, 331], 384),
}
MP3_BANDS_SHORT = {
    32: ([1, 5, 9, 13, 17, 23, 31, 43, 59, 79, 105, 139], 180),
    44: ([1, 5, 9, 13, 17, 23, 31, 41, 53, 67, 85, 107], 136),
    48: ([1, 5, 9, 13, 17, 23, 29, 39, 51, 65, 81, 101], 126),
}
_ALIAS_C = np.array([-0.6000, -0.5350, -0.3300, -0.1850, -0.0950, -0.0410, -0.0142, -0.0037])
_ALIAS_S = 1 / np.sqrt(1 + _ALIAS_C ** 2)
_ALIAS_A = _ALIAS_C / np.sqrt(1 + _ALIAS_C ** 2)

# ISO 11172-3 analysis window C[i], as the reference lists it
PQMF_WINDOW = np.array([
    0.000000000, -0.000000477, -0.000000477, -0.000000477, -0.000000477, -0.000000477, -0.000000477, -0.000000954,
    -0.000000954, -0.000000954, -0.000000954, -0.000001431, -0.000001431, -0.000001907, -0.000001907, -0.000002384,
    -0.000002384, -0.000002861, -0.000003338, -0.000003338, -0.000003815, -0.000004292, -0.000004768, -0.000005245,
    -0.000006199, -0.000006676, -0.000007629, -0.000008106, -0.000009060, -0.000010014, -0.000011444, -0.000012398,
    -0.000013828, -0.000014782, -0.000016689, -0.000018120, -0.000019550, -0.000021458, -0.000023365, -0.000025272,
    -0.000027657, -0.000030041, -0.000032425, -0.000034809, -0.000037670, -0.000040531, -0.000043392, -0.000046253,
    -0.000049591, -0.000052929, -0.000055790, -0.000059605, -0.000062943, -0.000066280, -0.000070095, -0.000073433,
    -0.000076771, -0.000080585, -0.000083923, -0.000087261, -0.000090599, -0.000093460, -0.000096321, -0.000099182,
    0.000101566, 0.000103951, 0.000105858, 0.000107288, 0.000108242, 0.000108719, 0.000108719, 0.000108242,
    0.000106812, 0.000105381, 0.000102520, 0.000099182, 0.000095367, 0.000090122, 0.000084400, 0.000077724,
    0.000069618, 0.000060558, 0.000050545, 0.000039577, 0.000027180, 0.000013828, -0.000000954, -0.000017166,
    -0.000034332, -0.000052929, -0.000072956, -0.000093937, -0.000116348, -0.000140190, -0.000165462, -0.000191212,
    -0.000218868, -0.000247478, -0.000277042, -0.000307560, -0.000339031, -0.000371456, -0.000404358, -0.000438213,
    -0.000472546, -0.000507355, -0.000542164, -0.000576973, -0.000611782, -0.000646591, -0.000680923, -0.000714302,
    -0.000747204, -0.000779152, -0.000809669, -0.000838757, -0.000866413, -0.000891685, -0.000915051, -0.000935555,
    -0.000954151, -0.000968933, -0.000980854, -0.000989437, -0.000994205, -0.000995159, -0.000991821, -0.000983715,
    0.000971317, 0.000953674, 0.000930786, 0.000902653, 0.000868797, 0.000829220, 0.000783920, 0.000731945,
    0.000674248, 0.000610352, 0.000539303, 0.000462532, 0.000378609, 0.000288486, 0.000191689, 0.000088215,
    -0.000021458, -0.000137329, -0.000259876, -0.000388145, -0.000522137, -0.000661850, -0.000806808, -0.000956535,
    -0.001111031, -0.001269817, -0.001432419, -0.001597881, -0.001766682, -0.001937389, -0.002110004, -0.002283096,
    -0.002457142, -0.002630711, -0.002803326, -0.002974033, -0.003141880, -0.003306866, -0.003467083, -0.003622532,
    -0.003771782, -0.003914356, -0.004048824, -0.004174709, -0.004290581, -0.004395962, -0.004489899, -0.004570484,
    -0.004638195, -0.004691124, -0.004728317, -0.004748821, -0.004752159, -0.004737377, -0.004703045, -0.004649162,
    -0.004573822, -0.004477024, -0.004357815, -0.004215240, -0.004049301, -0.003858566, -0.003643036, -0.003401756,
    0.003134727, 0.002841473, 0.002521515, 0.002174854, 0.001800537, 0.001399517, 0.000971317, 0.000515938,
    0.000033379, -0.000475883, -0.001011848, -0.001573563, -0.002161503, -0.002774239, -0.003411293, -0.004072189,
    -0.004756451, -0.005462170, -0.006189346, -0.006937027, -0.007703304, -0.008487225, -0.009287834, -0.010103703,
    -0.010933399, -0.011775017, -0.012627602, -0.013489246, -0.014358521, -0.015233517, -0.016112804, -0.016994476,
    -0.017876148, -0.018756866, -0.019634247, -0.020506859, -0.021372318, -0.022228718, -0.023074150, -0.023907185,
    -0.024725437, -0.025527000, -0.026310921, -0.027073860, -0.027815342, -0.028532982, -0.029224873, -0.029890060,
    -0.030526638, -0.031132698, -0.031706810, -0.032248020, -0.032754898, -0.033225536, -0.033659935, -0.034055710,
    -0.034412861, -0.034730434, -0.035007000, -0.035242081, -0.035435200, -0.035586357, -0.035694122, -0.035758972,
    0.035780907, 0.035758972, 0.035694122, 0.035586357, 0.035435200, 0.035242081, 0.035007000, 0.034730434,
    0.034412861, 0.034055710, 0.033659935, 0.033225536, 0.032754898, 0.032248020, 0.031706810, 0.031132698,
    0.030526638, 0.029890060, 0.029224873, 0.028532982, 0.027815342, 0.027073860, 0.026310921, 0.025527000,
    0.024725437, 0.023907185, 0.023074150, 0.022228718, 0.021372318, 0.020506859, 0.019634247, 0.018756866,
    0.017876148, 0.016994476, 0.016112804, 0.015233517, 0.014358521, 0.013489246, 0.012627602, 0.011775017,
    0.010933399, 0.010103703, 0.009287834, 0.008487225, 0.007703304, 0.006937027, 0.006189346, 0.005462170,
    0.004756451, 0.004072189, 0.003411293, 0.002774239, 0.002161503, 0.001573563, 0.001011848, 0.000475883,
    -0.000033379, -0.000515938, -0.000971317, -0.001399517, -0.001800537, -0.002174854, -0.002521515, -0.002841473,
    0.003134727, 0.003401756, 0.003643036, 0.003858566, 0.004049301, 0.004215240, 0.004357815, 0.004477024,
    0.004573822, 0.004649162, 0.004703045, 0.004737377, 0.004752159, 0.004748821, 0.004728317, 0.004691124,
    0.004638195, 0.004570484, 0.004489899, 0.004395962, 0.004290581, 0.004174709, 0.004048824, 0.003914356,
    0.003771782, 0.003622532, 0.003467083, 0.003306866, 0.003141880, 0.002974033, 0.002803326, 0.002630711,
    0.002457142, 0.002283096, 0.002110004, 0.001937389, 0.001766682, 0.001597881, 0.001432419, 0.001269817,
    0.001111031, 0.000956535, 0.000806808, 0.000661850, 0.000522137, 0.000388145, 0.000259876, 0.000137329,
    0.000021458, -0.000088215, -0.000191689, -0.000288486, -0.000378609, -0.000462532, -0.000539303, -0.000610352,
    -0.000674248, -0.000731945, -0.000783920, -0.000829220, -0.000868797, -0.000902653, -0.000930786, -0.000953674,
    0.000971317, 0.000983715, 0.000991821, 0.000995159, 0.000994205, 0.000989437, 0.000980854, 0.000968933,
    0.000954151, 0.000935555, 0.000915051, 0.000891685, 0.000866413, 0.000838757, 0.000809669, 0.000779152,
    0.000747204, 0.000714302, 0.000680923, 0.000646591, 0.000611782, 0.000576973, 0.000542164, 0.000507355,
    0.000472546, 0.000438213, 0.000404358, 0.000371456, 0.000339031, 0.000307560, 0.000277042, 0.000247478,
    0.000218868, 0.000191212, 0.000165462, 0.000140190, 0.000116348, 0.000093937, 0.000072956, 0.000052929,
    0.000034332, 0.000017166, 0.000000954, -0.000013828, -0.000027180, -0.000039577, -0.000050545, -0.000060558,
    -0.000069618, -0.000077724, -0.000084400, -0.000090122, -0.000095367, -0.000099182, -0.000102520, -0.000105381,
    -0.000106812, -0.000108242, -0.000108719, -0.000108719, -0.000108242, -0.000107288, -0.000105858, -0.000103951,
    0.000101566, 0.000099182, 0.000096321, 0.000093460, 0.000090599, 0.000087261, 0.000083923, 0.000080585,
    0.000076771, 0.000073433, 0.000070095, 0.000066280, 0.000062943, 0.000059605, 0.000055790, 0.000052929,
    0.000049591, 0.000046253, 0.000043392, 0.000040531, 0.000037670, 0.000034809, 0.000032425, 0.000030041,
    0.000027657, 0.000025272, 0.000023365, 0.000021458, 0.000019550, 0.000018120, 0.000016689, 0.000014782,
    0.000013828, 0.000012398, 0.000011444, 0.000010014, 0.000009060, 0.000008106, 0.000007629, 0.000006676,
    0.000006199, 0.000005245, 0.000004768, 0.000004292, 0.000003815, 0.000003338, 0.000003338, 0.000002861,
    0.000002384, 0.000002384, 0.000001907, 0.000001907, 0.000001431, 0.000001431, 0.000000954, 0.000000954,
    0.000000954, 0.000000954, 0.000000477, 0.000000477, 0.000000477, 0.000000477, 0.000000477, 0.000000477,
])


def _mp3_windows() -> tuple[np.ndarray, ...]:
    nl, ns, fill = 36, 12, 6
    long_ = np.sin((np.arange(nl) + 0.5) * np.pi / nl)
    short = np.sin((np.arange(ns) + 0.5) * np.pi / ns)
    start = np.concatenate([np.sin((np.arange(nl // 2) + 0.5) * np.pi / nl), np.ones(fill),
                            np.sin((np.arange(fill, fill + ns // 2) + 0.5) * np.pi / ns), np.zeros(fill)])
    stop = np.concatenate([np.zeros(fill), np.sin((np.arange(ns // 2) + 0.5) * np.pi / ns), np.ones(fill),
                           np.sin((np.arange(nl // 2, nl) + 0.5) * np.pi / nl)])
    return long_, start, stop, short


class _MP3:
    def __init__(self, khz: int, nb: int = MP3_WINDOWS):
        low, top = MP3_BANDS_LONG[khz]
        self.long = _Bands(_band_edges(low, top))                    # every long band, the last included
        low, top = MP3_BANDS_SHORT[khz]
        self.short = _Bands(_band_edges(low, top)[:-1], 3)            # the last short band is skipped
        self.sf = np.linspace(0.5, 0.7, 8)                            # the reference's eight
        wl, ws, we, wsh = _mp3_windows()
        self.basis = [_mdct_basis(wl), _mdct_basis(ws), _mdct_basis(we)]
        self.short_basis = _mdct_basis(wsh)
        # The polyphase filterbank as 32 FIR filters of 512 taps: subband m at newest sample p is
        # sum_n C[n] cos((2m + 1)(n mod 64 - 16) pi / 64) x[p - n]
        m = np.arange(MP3_SUBBANDS)[None, :]
        n = np.arange(512)[:, None]
        self.fir = PQMF_WINDOW[:, None] * np.cos((2 * m + 1) * ((n % 64) - 16) * np.pi / 64)   # (512, 32)
        # Frequency inversion: odd subbands at odd subband samples change sign
        k = np.arange(2 * MP3_GRANULE)
        self.sign = np.where((np.arange(MP3_SUBBANDS)[:, None] % 2 == 1) & (k[None, :] % 2 == 1), -1.0, 1.0)
        # Aliasing-reduction butterflies across each subband boundary
        sb = np.repeat(np.arange(1, MP3_SUBBANDS), 8)
        i = np.tile(np.arange(1, 9), MP3_SUBBANDS - 1)
        self.alias = (sb * MP3_GRANULE - i, sb * MP3_GRANULE + i - 1, _ALIAS_S[i - 1], _ALIAS_A[i - 1])

    def _butterflies(self, x: np.ndarray) -> np.ndarray:
        i1, i2, cs, ca = self.alias
        out = x.copy()
        a, b = x[:, i1], x[:, i2]
        out[:, i1] = cs * a - ca * b
        out[:, i2] = cs * b + ca * a
        return out

    def rates(self, regions: np.ndarray) -> np.ndarray:
        """Per alignment, the rate over every window. regions: (windows, MP3_SPAN) of one signal."""
        o = MP3_BLOCK
        k = len(self.sf)
        totals = [self.long.n * k] * 3 + [self.short.n * k]
        count = np.zeros(o)
        total = np.zeros(o)
        # Subband sample j of alignment a sits at newest PCM sample HISTORY + 511 + a + 32 j
        pos = np.arange(o)[:, None] + MP3_SUBBANDS * np.arange(2 * MP3_GRANULE)[None, :]   # (O, 36)
        n_pos = o + MP3_SUBBANDS * (2 * MP3_GRANULE - 1)
        for r in regions:
            hist = np.lib.stride_tricks.sliding_window_view(r, 512)[MP3_HISTORY:MP3_HISTORY + n_pos]
            sub = hist[:, ::-1] @ self.fir                                       # (positions, 32)
            ss = sub[pos].transpose(0, 2, 1) * self.sign[None]                   # (O, 32, 36)
            shapes = [self._butterflies((ss @ b).reshape(o, -1)) for b in self.basis]   # index m*18 + f
            short = np.stack([ss[:, :, n1:n1 + 12] @ self.short_basis
                              for n1 in MP3_SHORT_OFFSET + 6 * np.arange(3)], axis=1)  # (O, 3, 32, 6)
            # MATLAB's column-major reshape of (3 windows, 192 lines) is line-major: index line*3 + window
            short = self._butterflies(short.reshape(o, 3, -1).transpose(0, 2, 1).reshape(o, -1))
            shape_counts = [self.long.counts(m, self.sf, 80) for m in shapes]
            shape_counts.append(self.short.counts(short, self.sf, 80))
            c, t = _per_window(shape_counts, totals)
            count += c
            total += t
        return count / np.maximum(total, 1)


def detect_mp3(regions: np.ndarray, sample_rate: int, nb: int = MP3_WINDOWS) -> QuantResult | None:
    """MP3 traces in `regions` (windows, MP3_SPAN, channels), the loudest windows of the mid channel.
    None when the sample rate has no MP3 band table or nothing was long enough."""
    khz = round(sample_rate / 1000)
    if khz not in MP3_BANDS_LONG or not len(regions):
        return None
    det = _MP3(khz)
    regions = regions[:nb]
    thr = MP3_SIGNIFICANCE.get(len(regions))
    if regions.shape[2] == 1:
        curves = {"mono": det.rates(regions[:, :, 0])}
    else:
        left, right = regions[:, :, 0], regions[:, :, 1]
        curves = {"LR": np.maximum(det.rates(left), det.rates(right)),
                  "MS": np.maximum(det.rates((left + right) / 2), det.rates((left - right) / 2))}
    best = None
    for mode, p in curves.items():
        off, v, z = _peak(p)
        if best is None or v > best.likelihood:
            best = QuantResult("mp3", v, thr, None if thr is None else v > thr, z, off, mode, "", len(regions))
    return best
