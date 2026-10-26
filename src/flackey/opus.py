"""Traces of an earlier Opus encode in decoded PCM (issue #97).

Opus (its CELT layer, all of it at music bitrates) codes each band of a 20 ms MDCT frame as an integer
pulse vector scaled to the band's energy (PVQ). Decode it, and the samples look like any other music; but
resample them back to 48 kHz and run CELT's own transform at the encoder's frame alignment -- the 960-bin
MDCT with its 120-sample low-overlap window, after the same 0.85 pre-emphasis -- and the 3.2-15.6 kHz
bands land back on integer vectors: m x |v| / max|v| is a vector of whole numbers for some small m. Genuine
audio has no alignment where that happens more than by chance (about one band slice in thirteen frames).

The search finds the best alignment on a few steady regions of the file, then counts fitting slices at
that alignment on *other* regions (held-out), next to 64 random alignments at the same phase: a transcode's
alignment keeps fitting where it was not chosen, a chance best does not. The score is that held-out count.

A 44.1 kHz copy moves the 48 kHz frame grid by a fraction of a sample, so the alignment is an offset
(0..959) and a phase. `_family` searches only what an ffmpeg/libopus decode can give: the decoder's
pre-skip (312 samples) puts the grid at 648 mod 960, and cutting k whole samples off the 44.1 kHz copy
moves it by k x 160/147. That is the fast search (one phase per k). `_general` searches every offset at
eight phases and refines, for a copy cut at 48 kHz or decoded by something else; it costs several times
more and is only run when asked (`general=True`).

The steadiest regions are chosen because CELT codes an attack with short blocks, which a long transform
cannot see; the loudest windows that suit the MP3/AAC detectors are often exactly those.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

N = 960                     # CELT's 20 ms frame at 48 kHz
OV = 120                    # its window overlap
SUP = N + OV                # the window's nonzero support
PREEMPH = np.float32(0.8500061035)
EB = [e * 8 for e in [0, 1, 2, 3, 4, 5, 6, 8, 10, 12, 14, 16, 20, 24, 28, 34, 40, 48, 60, 78, 100]]  # 25 Hz bins
TAU = 3e-4                  # mean squared distance to the integer vector, scoring
TAU_SEARCH = 1e-2           # looser for the search, which must survive a phase 1/16 off
MMAX = 16                   # largest pulse magnitude tried when scoring
MMAX_SEARCH = 8
MINNZ = 0.5                 # share of a slice's entries that must be nonzero pulses
LEVEL_FLOOR_DB = -84.0      # slices quieter than this are ignored (16-bit rounding of test tones can fit)
FRAME_MIN = 3               # fitting slices for a frame to count as fitting
FRAMES = 2                  # consecutive CELT frames per region
M0 = N                      # 48 kHz margin before a region (the FFT resample wraps at its ends)
PRESKIP_GRID = 648          # window-support start mod 960 of an untrimmed libopus decode (pre-skip 312)
NULLS = 64
REGIONS = 32                # one per equal stretch of the file
SEARCH_REGIONS = 4          # of those, the steadiest: the alignment search; the rest are held out
RATES = {44_100: (882, 147, 160), 48_000: (960, 1, 1)}   # rate -> (samples per CELT frame, up/down ratio)
CHUNK_FRAMES = 100          # a captured stretch is this many CELT frames long (2 s)

# Held-out fitting slices: at or above OPUS_STRONG an Opus transcode; OPUS_WEAK and up, traces. The highest
# a genuine file reached in the prototype's corpus was 3, the lowest Opus 11 (issue #97).
OPUS_STRONG = 10.0
OPUS_WEAK = 4.0
# ...and only when it beats the best of the random alignments this many times over. Coarse, crushed audio
# fits the lattice at any alignment; a transcode's own grid beats chance 100-fold or more (issue #97).
OPUS_OVER_CHANCE = 2.0


def _window() -> np.ndarray:
    i = np.arange(OV)
    w = np.sin(np.pi / 2 * np.sin(np.pi * (i + 0.5) / (2 * OV)) ** 2)
    return np.concatenate([w, np.ones(N - OV), w[::-1]]).astype(np.float32)


_WSUP = _window()
_n = np.arange(N // 2)
_PRE = np.exp(-1j * np.pi * (4 * _n + 1) / (4 * N)).astype(np.complex64)
_POST = np.exp(-1j * np.pi * _n / N).astype(np.complex64)


def _dct4(u: np.ndarray) -> np.ndarray:
    t = (u[..., 0::2] + 1j * u[..., ::-2]) * _PRE
    T = np.fft.fft(t, axis=-1) * _POST
    X = np.empty(u.shape, np.float32)
    X[..., 0::2] = T.real
    X[..., ::-2] = -T.imag
    return X


def _mdct(s: np.ndarray) -> np.ndarray:
    """CELT's MDCT of (..., SUP) samples of the window support: fold, then a DCT-IV."""
    h = N // 2
    w = _WSUP[:OV]
    head = s[..., :OV] * w
    tail = s[..., SUP - OV:] * w[::-1]
    u = np.empty(s.shape[:-1] + (N,), np.float32)
    u[..., :h] = -s[..., 1019:539:-1]
    u[..., 0:60] = -tail[..., 59::-1] - tail[..., 60:]
    u[..., h:] = -s[..., 539:59:-1]
    u[..., h + 420:] = -head[..., 119:59:-1] + head[..., :60]
    return _dct4(u)


def _slice_plan(lo_band: int = 11, hi_band: int = 18, min_len: int = 6, depth_max: int = 3) -> dict:
    """Bands 11-18 (3.2-15.6 kHz) split as CELT splits them, into slices of `min_len` or more."""
    g: dict[int, list[int]] = {}
    for b in range(lo_band, hi_band + 1):
        lo, hi = EB[b], EB[b + 1]
        w = hi - lo
        d = 0
        while d < depth_max and (w >> (d + 1)) >= min_len and w % (1 << (d + 1)) == 0:
            d += 1
        sl = w >> d
        for k in range(1 << d):
            g.setdefault(sl, []).append(lo + k * sl)
    return {n: np.asarray(s)[:, None] + np.arange(n)[None, :] for n, s in g.items()}


_PLAN = _slice_plan()
_FLOOR = np.float32(10 ** (LEVEL_FLOOR_DB / 10) * N)


def _lattice_hits(v: np.ndarray, loud: np.ndarray, mmax: int, tau: float) -> np.ndarray:
    a = np.abs(v)
    u = a / np.maximum(a.max(-1, keepdims=True), 1e-30)
    need = max(3, int(np.ceil(MINNZ * v.shape[-1])))
    hit = np.zeros(v.shape[:-1], bool)
    for m in range(1, mmax + 1):
        y = m * u
        r = np.floor(y + 0.5)
        d = y - r
        hit |= ((d * d).mean(-1) < tau) & ((r > 0).sum(-1) >= need)
    return hit & loud


def _frame_hits(c: np.ndarray, reps: tuple[str, ...], mmax: int = MMAX, tau: float = TAU) -> np.ndarray:
    """c: (channels, ..., N) MDCTs. Fitting slices per frame; a slice counts once over L/R/M/S."""
    tot = 0
    for n, ix in _PLAN.items():
        if c.shape[0] == 1:
            v = c[0][..., ix]
            tot = tot + _lattice_hits(v, (v * v).mean(-1) > _FLOOR, mmax, tau).sum(-1)
            continue
        vl, vr = c[0][..., ix], c[1][..., ix]
        el, er = (vl * vl).sum(-1, keepdims=True), (vr * vr).sum(-1, keepdims=True)
        loud = ((el + er)[..., 0] / (2 * n)) > _FLOOR
        nl, nr = np.sqrt(el) + 1e-30, np.sqrt(er) + 1e-30
        hit = 0
        for r in reps:
            v = vl if r == "L" else vr if r == "R" else vl / nl + vr / nr if r == "M" else vr / nr - vl / nl
            hit = hit | _lattice_hits(v, loud, mmax, tau)
        tot = tot + hit.sum(-1)
    return tot


class _Rate:
    """The geometry of one input rate: its CELT frame length and the region length to resample."""

    def __init__(self, sample_rate: int):
        self.block, self.down, self.up = RATES[sample_rate]
        need48 = M0 + N + FRAMES * N + SUP + N // 2
        self.seg = (need48 * self.down // self.up // self.down + 1) * self.down

    def to48(self, seg: np.ndarray, delta: float) -> np.ndarray:
        """Resampled to 48 kHz by FFT (a multiple of `down` samples in), advanced by `delta` 48 kHz
        samples, then pre-emphasised as CELT does."""
        if self.up == self.down and not delta:
            y = seg.astype(np.float32, copy=True)
        else:
            n = len(seg)
            num = n // self.down * self.up
            x = np.fft.rfft(seg, axis=0)
            m = num // 2 + 1
            y_f = np.zeros((m, seg.shape[1]), complex)
            k = min(m, x.shape[0])
            y_f[:k] = x[:k]
            if delta:
                y_f *= np.exp(2j * np.pi * np.arange(m) * delta / num)[:, None]
            y = (np.fft.irfft(y_f, num, axis=0) * (num / n)).astype(np.float32)
        y[1:] -= PREEMPH * y[:-1]
        return y

    def coefs(self, seg: np.ndarray, delta: float, offsets, mono: bool = False) -> np.ndarray:
        """MDCT (channels, offsets, FRAMES, N) of one region at those 48 kHz offsets."""
        y = self.to48(seg, delta)
        if mono:
            y = y.sum(1, keepdims=True)
        win = sliding_window_view(y, SUP, axis=0)
        st = M0 + np.asarray(offsets)[:, None] + np.arange(FRAMES)[None, :] * N
        return _mdct(np.moveaxis(win[st], 2, 0))


def steadiest(chunk: np.ndarray, rate: _Rate) -> tuple[float, int] | None:
    """Within one captured stretch (its start a whole number of CELT frames into the file): among the
    frame-aligned regions louder than the stretch's median, the least transient. (sort key, start)."""
    nb = (len(chunk) - rate.seg) // rate.block
    if nb < 1:
        return None
    hp = np.diff(chunk.sum(1), prepend=0).astype(np.float64)
    sub = rate.block // 4
    e_sub = np.add.reduceat(hp * hp, np.arange(0, len(hp) - sub, sub))
    per = rate.seg // sub
    lead = M0 * rate.down // rate.up
    cand = []
    for i in range(nb):
        k = (i * rate.block + lead) // sub
        w = e_sub[k:k + per]
        if len(w) == per:
            cand.append((w.mean(), w.max() / (w.mean() + 1e-20), i))
    if not cand:
        return None
    med = np.median([c[0] for c in cand])
    e, tr, i = min((c for c in cand if c[0] >= med), key=lambda c: c[1])
    if e <= 0:
        return None
    return tr / np.log10(e + 1e-20 + 10), i * rate.block


class Chunks:
    """Captures REGIONS stretches of CHUNK_FRAMES CELT frames, one centred in each equal part of a stream
    of `expected` samples, each starting a whole number of frames into it, from PCM fed in chunks."""

    def __init__(self, sample_rate: int, channels: int, expected: int):
        self.rate = _Rate(sample_rate)
        length = CHUNK_FRAMES * self.rate.block
        starts = []
        for j in range(REGIONS):
            centre = (j + 0.5) * expected / REGIONS
            starts.append(max(0, int((centre - length / 2) // self.rate.block) * self.rate.block))
        self.spans = sorted({(s, s + length) for s in starts})
        self.bufs = [np.zeros((0, channels), np.float32) for _ in self.spans]
        self.pos = 0

    def feed(self, x: np.ndarray) -> None:
        a, b = self.pos, self.pos + len(x)
        for k, (s, e) in enumerate(self.spans):
            if s < b and e > a:
                self.bufs[k] = np.concatenate([self.bufs[k], x[max(s, a) - a:min(e, b) - a]])
        self.pos = b

    def regions(self) -> list[tuple[float, np.ndarray]]:
        """(sort key, region samples) per stretch that held one, steadiest first."""
        out = []
        for (s, _), buf in zip(self.spans, self.bufs):
            if len(buf) < self.rate.seg + self.rate.block:
                continue
            found = steadiest(buf, self.rate)
            if found is not None:
                key, start = found
                out.append((key, buf[start:start + self.rate.seg]))
        out.sort(key=lambda r: r[0])
        return out


@dataclass
class OpusResult:
    codec: str              # "opus"
    score: float            # fitting slices at the chosen alignment, on the held-out regions
    null_max: float         # the best of NULLS random alignments on the same regions
    cover: float            # share of the held-out regions where that alignment fits a frame
    offset: int
    phase: float
    search: str             # "family" or "general"
    regions: int            # held-out regions scored

    def to_dict(self) -> dict:
        d = asdict(self)
        d["score"], d["null_max"], d["cover"], d["phase"] = (round(self.score, 1), round(self.null_max, 1),
                                                              round(self.cover, 2), round(self.phase, 4))
        return d


def _family(rate: _Rate, search: list[np.ndarray]) -> tuple[int, int, float]:
    """(stage hits, offset, phase) over the alignments an ffmpeg/libopus decode, cut by whole samples
    at the file's rate, can have."""
    hyps: dict[int, set[int]] = {}
    for k in range(rate.block):
        t = (PRESKIP_GRID - k * rate.up / rate.down) % N
        o = int(np.floor(t + 1e-9)) % N
        q = round((t - np.floor(t + 1e-9)) * rate.down) % rate.down
        hyps.setdefault(q, set()).add(o)
    if rate.up == rate.down:                     # 48 kHz: every whole-sample offset is one hypothesis
        hyps = {0: set(range(N))}
    groups = [(q, sorted(offs)) for q, offs in sorted(hyps.items())]
    flat = [(o, q / rate.down) for q, offs in groups for o in offs]
    total = np.zeros(len(flat))
    for s in search:                             # every hypothesis of one region scored in one pass
        c = np.concatenate([rate.coefs(s, q / rate.down, offs) for q, offs in groups], axis=1)
        total += _frame_hits(c, ("M",)).sum(-1)
    i = int(np.argmax(total))
    return int(total[i]), flat[i][0], flat[i][1]


def _general(rate: _Rate, search: list[np.ndarray], phases: int = 8, ncand: int = 3) -> tuple[int, int, float]:
    """(stage hits, offset, phase) over every offset at `phases` phases, the best few refined."""
    allo = np.arange(N)
    deltas = np.arange(phases) / phases - 0.5
    score = np.zeros((phases, N))
    for j, d in enumerate(deltas):
        for s in search:
            score[j] += _frame_hits(rate.coefs(s, d, allo, mono=True), ("M",), MMAX_SEARCH, TAU_SEARCH).sum(-1)
    span = int(128 / phases)
    cands = []
    for f in np.argsort(-score, axis=None)[:ncand]:
        j, o = divmod(int(f), N)
        best = (-1, float(deltas[j]))
        for dd in deltas[j] + np.arange(-span, span + 1) / 128:
            h = sum(_frame_hits(rate.coefs(s, dd, [o]), ("M",), tau=TAU_SEARCH).sum() for s in search)
            if h > best[0]:
                best = (int(h), float(dd))
        cands.append((best[0], o, best[1]))
    return max(cands)


def _held_out(rate: _Rate, held: list[np.ndarray], o: int, d: float) -> tuple[float, float, float]:
    """(score, best null, cover) of alignment (o, d) on the held-out regions. Frames that repeat exactly
    (a loop) are merged, so a looped bar is counted once."""
    rng = np.random.default_rng(0)
    nulls = rng.choice(np.setdiff1d(np.arange(N), np.arange(o - 16, o + 17) % N), NULLS, replace=False)
    offs = np.concatenate([[o], nulls])
    per, sigs = [], []
    for s in held:
        c = rate.coefs(s, d, offs)                       # (ch, O, FRAMES, N)
        per.append(_frame_hits(c, ("L", "R", "M", "S")))
        sigs.append(c[:, 0].sum(0))
    hits = np.concatenate(per, axis=1)                   # (O, regions x FRAMES)
    sig = np.abs(np.concatenate(sigs, axis=0)[..., EB[11]:EB[19]]).reshape(hits.shape[1], -1)
    sig = sig / (np.linalg.norm(sig, axis=1, keepdims=True) + 1e-30)
    weight = 1.0 / (sig @ sig.T > 0.999).sum(1)
    tot = (hits * weight).sum(1)
    fits = hits[0].reshape(len(held), FRAMES).max(axis=1) >= FRAME_MIN
    return float(tot[0]), float(tot[1:].max()), float(fits.mean())


def detect_opus(regions: list[tuple[float, np.ndarray]], sample_rate: int, *, general: bool = False
                ) -> OpusResult | None:
    """Opus traces in `regions` (from `Chunks.regions`, steadiest first). None when the rate is not
    44.1 or 48 kHz, or there are too few regions to hold any out."""
    if sample_rate not in RATES or len(regions) < SEARCH_REGIONS + 4:
        return None
    rate = _Rate(sample_rate)
    segs = [r.astype(np.float32) for _, r in regions]
    search, held = segs[:SEARCH_REGIONS], segs[SEARCH_REGIONS:]
    _, o, d = (_general if general else _family)(rate, search)
    score, null_max, cover = _held_out(rate, held, o, d)
    return OpusResult("opus", score, null_max, cover, o, d, "general" if general else "family", len(held))
