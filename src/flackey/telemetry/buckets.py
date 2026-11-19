"""Every measured value leaves as one of these labels, never as the number (spec §3.1). The edges are the
schema's (`assets/telemetry_schema_v1.json`, "buckets"); `test_telemetry` checks every label made here is
one the schema lists."""
from __future__ import annotations

_DUR = ((1, "<1s"), (5, "1-5s"), (15, "5-15s"), (60, "15-60s"), (300, "1-5m"), (900, "5-15m"),
        (3600, "15-60m"), (6 * 3600, "1-6h"), (24 * 3600, "6-24h"), (3 * 86400, "1-3d"))


def dur(seconds: float | None) -> str | None:
    if seconds is None or seconds < 0:
        return None
    for edge, label in _DUR:
        if seconds < edge:
            return label
    return ">3d"


def n(count: int | None) -> str | None:
    if count is None or count < 0:
        return None
    if count <= 1:
        return str(count)
    for edge, label in ((5, "2-4"), (20, "5-19"), (100, "20-99")):
        if count < edge:
            return label
    return "100+"


def size_mb(nbytes: int | None) -> str | None:
    if nbytes is None or nbytes < 0:
        return None
    mb = nbytes / 1e6
    for edge, label in ((10, "<10"), (30, "10-30"), (60, "30-60"), (120, "60-120")):
        if mb < edge:
            return label
    return "120+"


def length(seconds: float | None) -> str | None:
    if seconds is None or seconds < 0:
        return None
    for edge, label in ((180, "<3m"), (300, "3-5m"), (420, "5-7m"), (600, "7-10m")):
        if seconds < edge:
            return label
    return "10m+"


def score(pct: float | None) -> str | None:
    """A 0-100 score, or a 0-1 fraction (fingerprint scores are fractions)."""
    if pct is None or pct < 0:
        return None
    if pct <= 1:
        pct *= 100
    for edge, label in ((50, "<50"), (70, "50-69"), (85, "70-84"), (95, "85-94")):
        if pct < edge:
            return label
    return "95+"


def speed_kbps(kbps: float | None) -> str | None:
    if kbps is None or kbps < 0:
        return None
    for edge, label in ((50, "<50"), (200, "50-200"), (1000, "200-1000")):
        if kbps < edge:
            return label
    return "1000+"


def edge_khz(hz: float | None) -> str:
    """The spectral ceiling, edges aligned to verify.py's thresholds; no edge measured is "none"."""
    if hz is None or hz <= 0 or hz >= 22_050:
        return "none"
    k = hz / 1000
    for edge, label in ((15, "<15"), (16, "15-16"), (17, "16-17"), (18, "17-18"), (19, "18-19"),
                        (19.9, "19-19.9"), (20.5, "19.9-20.5"), (21.5, "20.5-21.5")):
        if k < edge:
            return label
    return "21.5+"


def strength(value: float | None, codec: str | None) -> str | None:
    """The firing detector's value: an Opus score, or an MP3/AAC z."""
    if value is None:
        return None
    if codec == "opus":
        edges = ((2, "<2"), (4, "2-4"), (7, "4-7"), (10, "7-10"), (15, "10-15"))
        top = "15+"
    else:
        edges = ((4, "<4"), (8, "4-8"), (10, "8-10"), (12, "10-12"), (16, "12-16"), (24, "16-24"))
        top = "24+"
    for edge, label in edges:
        if value < edge:
            return label
    return top


def coverage(share: float | None) -> str:
    if share is None:
        return "n/a"
    for edge, label in ((0.25, "<0.25"), (0.5, "0.25-0.5"), (0.75, "0.5-0.75")):
        if share < edge:
            return label
    return "0.75-1"


def share(ok: int, total: int, *, off: bool = False) -> str:
    """How many of a day's health probes answered: "off" when the source is not in use."""
    if off or total <= 0:
        return "off"
    if ok <= 0:
        return "0"
    if ok >= total:
        return "100%"
    pct = ok / total
    if pct < 0.5:
        return "<50%"
    if pct < 0.9:
        return "50-90%"
    return "90-99%"


def sample_rate(hz: int | None) -> str | None:
    if not hz:
        return None
    if hz < 44_100:
        return "<44.1k"
    if hz == 44_100:
        return "44.1k"
    if hz <= 48_000:
        return "48k"
    if hz <= 96_000:
        return "88.2-96k"
    return ">96k"


def gain(seconds: float | None) -> str | None:
    """How much longer an extended mix is than the filed track; under 30 s is not offered."""
    if seconds is None or seconds < 30:
        return None
    for edge, label in ((60, "30-60s"), (120, "1-2m"), (240, "2-4m")):
        if seconds < edge:
            return label
    return "4m+"


def days_since(days: float | None) -> str | None:
    if days is None or days < 0:
        return None
    if days < 7:
        return "<1w"
    if days < 28:
        return "1-4w"
    if days < 90:
        return "1-3mo"
    return "3mo+"
