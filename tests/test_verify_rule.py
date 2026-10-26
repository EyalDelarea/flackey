"""The decision rule and the evidence plumbing, without any audio tool (issue #97)."""
import io

import numpy as np
import pytest

from flackey import evidence, opus, quant
from flackey.config import Settings
from flackey.models import NO_EDGE_HZ, Verdict
from flackey.verify import (
    QUANT_COVER_MIN,
    QUANT_Z_STRONG,
    QUANT_Z_WEAK,
    VerifyError,
    _skip_wav_header,
    decide,
    edge_finding,
    quant_finding,
    summary,
)


def analysis(fmt="flac", edge=None, reach=0.0, judged=2, aac_z=4.0, mp3_z=4.0, kbps=1000):
    return {
        "version": "2.0", "probe": {"fmt": fmt, "bitrate_kbps": kbps},
        "spectral": {"judged": judged, "edge_hz": edge, "edge_drop_db": 30.0 if edge else None,
                     "frames_above_edge": reach if edge else None},
        "quant": None if fmt == "mp3" else {"aac": {"codec": "aac", "z": aac_z}, "mp3": {"codec": "mp3", "z": mp3_z}},
        "integrity": {"checked": [], "failed": None}, "container": {"max_blocksize": 4096},
    }


@pytest.mark.parametrize("kw,tier", [
    ({}, "clear"),                                              # full band, no traces
    ({"edge": 16_500}, "reject"),                               # an MP3 128's lowpass
    ({"edge": 19_400, "mp3_z": QUANT_Z_STRONG + 1}, "reject"),  # LAME 256: its grid agrees
    ({"edge": 19_400, "mp3_z": QUANT_Z_WEAK + 1}, "reject"),
    ({"edge": 19_400}, "unverified"),                           # a mastering lowpass, as far as anyone can tell
    ({"edge": 18_900}, "reject"),                               # below any genuine master's filter
    ({"edge": 20_100}, "unverified"),                           # LAME 320, Opus -- or a converter's filter
    ({"edge": 20_100, "mp3_z": QUANT_Z_WEAK + 1}, "reject"),    # two ambiguous families agreeing
    ({"edge": 21_800}, "clear"),                                # above every encoder's lowpass
    ({"edge": 16_500, "reach": 0.2}, "clear"),                  # sound reaches past it: a roll-off
    ({"aac_z": QUANT_Z_STRONG + 1}, "reject"),                  # AAC 256 keeps the band, not the grid
    ({"mp3_z": QUANT_Z_WEAK + 1}, "unverified"),                # faint traces alone
    ({"judged": 0}, "unverified"),                              # too little sound
])
def test_the_lossless_rule(kw, tier):
    got, reason, cutoff = decide(analysis(**kw), "flac")
    assert got == tier, reason
    if kw.get("edge") and not kw.get("reach"):
        assert cutoff == kw["edge"]


def _segments(n_with, n=8, z=QUANT_Z_STRONG + 10):
    return [z] * n_with + [4.0] * (n - n_with)


@pytest.mark.parametrize("kw,tier", [
    ({"mp3_seg": _segments(8)}, "reject"),                      # the whole file carries the grid
    ({"mp3_seg": _segments(int(8 * QUANT_COVER_MIN))}, "reject"),
    ({"mp3_seg": _segments(2)}, "unverified"),                  # a sample from an MP3 inside a master
    ({"mp3_seg": _segments(2), "edge": 19_400}, "unverified"),  # the edge does not get to lean on it
    ({"mp3_seg": _segments(2), "edge": 16_500}, "reject"),      # an edge that rejects alone still does
    ({"mp3_seg": _segments(2, n=3)}, "reject"),                 # too few stretches to count: as before
])
def test_traces_in_part_of_the_track_are_not_a_reject(kw, tier):
    a = analysis(mp3_z=QUANT_Z_STRONG + 20, edge=kw.get("edge"))
    a["quant"]["mp3"]["segments"] = kw["mp3_seg"]
    got, reason, _ = decide(a, "flac")
    assert got == tier, reason
    if tier == "unverified":
        assert reason == "lossless, but MP3-like traces in part of the track"
        assert summary({**a, "tier": got})["quant"] == {"finding": "partial", "codec": "mp3",
                                                         "z": QUANT_Z_STRONG + 20, "cover": 0.25}


def test_the_loudest_windows_are_the_same_with_segments_kept():
    """Keeping each stretch's loudest windows leaves the whole file's loudest -- the detectors' input --
    exactly as before, and a stretch too short to fill its count is not offered for judging."""
    rng = np.random.default_rng(5)
    x = (rng.standard_normal((44100 * 10, 2)) * np.linspace(0.1, 1, 44100 * 10)[:, None]).astype(np.float32)
    whole = quant.TopRegions(quant.MP3_BLOCK, quant.MP3_SPAN, quant.MP3_WINDOWS, 2)
    parts = quant.TopRegions(quant.MP3_BLOCK, quant.MP3_SPAN, quant.MP3_WINDOWS, 2, segment=44100 * 3)
    for i in range(0, len(x), 30_000):
        whole.feed(x[i:i + 30_000])
        parts.feed(x[i:i + 30_000])
    assert np.array_equal(whole.regions(), parts.regions())
    assert parts.segments() == [0, 1, 2, 3]
    assert parts.regions(1).shape == (quant.MP3_WINDOWS, quant.MP3_SPAN, 2)


def _with_opus(score, cover=0.7, **kw):
    a = analysis(**kw)
    a["quant"]["opus"] = {"codec": "opus", "score": score, "null_max": 1.0, "cover": cover}
    return a


@pytest.mark.parametrize("a,tier,finding", [
    (_with_opus(0.0), "clear", "clean"),
    (_with_opus(opus.OPUS_STRONG), "reject", "strong"),                 # Opus keeps every band to 20 kHz
    (_with_opus(opus.OPUS_WEAK), "unverified", "weak"),
    (_with_opus(opus.OPUS_WEAK, edge=20_000), "reject", "weak"),        # its lowpass and its traces agree
    (_with_opus(500.0, cover=0.1), "unverified", "partial"),            # a sample, not the track
])
def test_the_opus_rule(a, tier, finding):
    got, reason, _ = decide(a, "flac")
    assert got == tier, reason
    assert summary({**a, "tier": got})["quant"]["finding"] == finding
    if tier == "reject":
        assert "Opus" in reason


def test_the_strongest_family_speaks_for_the_grid():
    """A strong Opus finding is not hidden by a clean MP3 z that happens to be numerically larger."""
    kind, codec, value = quant_finding({"aac": {"codec": "aac", "z": 7.0}, "mp3": {"codec": "mp3", "z": 7.5},
                                        "opus": {"codec": "opus", "score": 40.0, "cover": 0.8}})
    assert (kind, codec, value) == ("strong", "opus", 40.0)


def test_clipped_windows_are_passed_over():
    x = np.full((44100, 2), 0.1, np.float32)
    x[20_000:20_010] = 1.0                                     # one loud, clipped spot
    top = quant.TopRegions(quant.MP3_BLOCK, quant.MP3_SPAN, 4, 2, clip=quant.CLIP)
    top.feed(x)
    assert np.abs(top.regions()).max() < quant.CLIP


@pytest.mark.parametrize(("hits", "searched", "tier"), [
    (lambda i: True, 4, "reject"),            # half the stretches carry it: the rest are not searched
    (lambda i: i % 4 == 0, 8, "unverified"),  # never enough: every stretch is searched, cover 2/8
])
def test_the_stretch_search_stops_once_enough_carry_the_grid(hits, searched, tier):
    class Found:
        def __init__(self, z):
            self.z = z

        def to_dict(self):
            return {"codec": "mp3", "z": self.z}

    calls = []

    def detect(regions, sr):
        calls.append(1)
        return Found(QUANT_Z_STRONG + 10 if len(calls) == 1 or hits(len(calls) - 2) else 2.0)

    a = evidence.Analyzer(44100, 2, 16, quant_detectors=True, expected_samples=44100 * 16)
    a.feed((np.random.default_rng(1).normal(0, 0.1, (44100 * 16, 2)) * 32767).astype("<i2").tobytes())
    r = a._detect(a._mp3, detect)
    assert len(r["segments"]) == searched and r["stretches"] == evidence.QUANT_SEGMENTS
    an = analysis(mp3_z=r["z"])
    an["quant"]["mp3"] = r
    assert decide(an, "flac")[0] == tier


def test_a_master_clipped_almost_throughout_shows_no_grid():
    """Its few unclipped windows are too few to weigh: one window's best alignment can stand out by chance."""
    rng = np.random.default_rng(97)
    x = np.clip(rng.normal(0, 3, (44100 * 20, 2)), -1, 1)
    x[44100:44100 + 8000] *= 0.01                              # one short unclipped spot
    a = evidence.Analyzer(44100, 2, 16, quant_detectors=True, expected_samples=len(x))
    a.feed((x * 32767).astype("<i2").tobytes())
    q = a.finish()["quant"]
    assert 0 < len(a._aac.regions()) < quant.AAC_WINDOWS and 0 < len(a._mp3.regions()) < quant.MP3_WINDOWS
    assert q["aac"] is None and q["mp3"] is None


def test_the_opus_stretches_are_frame_aligned_and_spread_over_the_file():
    total = 44100 * 300
    ch = opus.Chunks(44100, 2, total)
    assert len(ch.spans) == opus.REGIONS
    assert all(s % 882 == 0 for s, _ in ch.spans)
    assert ch.spans[0][0] < total / opus.REGIONS < ch.spans[1][0] and ch.spans[-1][1] <= total
    x = np.arange(total, dtype=np.float32)[:, None].repeat(2, 1) / total
    for i in range(0, total, 100_000):
        ch.feed(x[i:i + 100_000])
    for (s, e), buf in zip(ch.spans, ch.bufs):
        assert len(buf) == e - s and buf[0, 0] == x[s, 0]


def test_a_quantisation_only_reject_keeps_the_no_edge_cutoff():
    """The page and "Keep it anyway" key off the cutoff: a quant-only refusal still carries one, and it
    says "no edge", which the page explains on its own terms."""
    tier, reason, cutoff = decide(analysis(aac_z=50), "flac")
    assert tier == "reject" and cutoff == NO_EDGE_HZ and "AAC" in reason


@pytest.mark.parametrize("edge,tier", [(None, "clear"), (16_000, "reject"), (19_000, "unverified"),
                                       (20_300, "clear")])
def test_the_mp3_rule(edge, tier):
    got, _, _ = decide(analysis("mp3", edge=edge, kbps=320), "mp3")
    assert got == tier


def test_findings():
    assert edge_finding({"judged": 0}, "flac") == ("insufficient", None)
    assert edge_finding({"judged": 1, "edge_hz": None}, "flac") == ("none", None)
    assert quant_finding(None) == ("not_run", None, 0.0)
    assert quant_finding({"aac": {"codec": "aac", "z": 30.0}, "mp3": None})[:2] == ("strong", "aac")


def test_summary_is_small_and_complete():
    a = {**analysis(edge=20_100), "tier": "unverified", "legacy": {"passed": True, "cutoff_hz": 20_000}}
    s = summary(a)
    assert s["tier"] == "unverified" and s["edge"]["finding"] == "ambiguous" and s["quant"]["finding"] == "clean"
    assert s["container"] == {"max_blocksize": 4096} and summary(None) is None


def test_verdict_level():
    assert Verdict(True, "flac", 1000, 22050, "", None).level == "clear"
    assert Verdict(False, "flac", 1000, 16000, "", None).level == "reject"
    assert Verdict(True, "flac", 1000, 20100, "", None, tier="unverified").level == "unverified"


def test_wav_header_is_skipped_to_the_data():
    body = b"\x01\x02" * 8
    fmt = b"fmt " + (16).to_bytes(4, "little") + b"\0" * 16
    lst = b"LIST" + (3).to_bytes(4, "little") + b"abc\0"                  # odd size, padded
    wav = b"RIFF" + b"\0\0\0\0" + b"WAVE" + fmt + lst + b"data" + len(body).to_bytes(4, "little") + body
    s = io.BytesIO(wav)
    _skip_wav_header(s)
    assert s.read() == body
    with pytest.raises(VerifyError):
        _skip_wav_header(io.BytesIO(b"not a wav at all"))


def test_integrity_names_the_broken_promise():
    facts = {"container": "flac", "total_samples": 100, "md5": "ab" * 16}
    assert evidence.integrity(facts, 100, "ab" * 16, 44100, True)["failed"] is None
    assert "STREAMINFO" in evidence.integrity(facts, 90, "ab" * 16, 44100, True)["failed"]
    assert "MD5" in evidence.integrity(facts, 100, "cd" * 16, 44100, True)["failed"]
    assert evidence.integrity(facts, 100, "cd" * 16, 44100, False)["md5"] == "not checked at this bit depth"
    mp3 = {"container": "mp3", "expected_samples": 441_000, "length_header": True}
    assert evidence.integrity(mp3, 441_000 - 1000, "", 44100, False)["failed"] is None
    assert "length header" in evidence.integrity(mp3, 200_000, "", 44100, False)["failed"]


def test_the_analyzer_reads_16_and_24_bit_pcm_and_hashes_it():
    rng = np.random.default_rng(1)
    x = (rng.standard_normal((44100 * 3, 2)) * 3000).astype("<i2")
    an = evidence.Analyzer(44100, 2, 16, quant_detectors=False)
    raw = x.tobytes()
    for i in range(0, len(raw), 9999):                         # chunks that split samples and frames
        an.feed(raw[i:i + 9999])
    out = an.finish()
    assert out["samples"] == len(x) and out["bits"]["used"] == 16
    assert out["spectral"]["judged"] == 2 and out["spectral"]["edge_hz"] is None     # white noise: full band


def test_noise_shows_no_quantisation_grid():
    """No false alarm on audio that was never quantised: white noise at two scales."""
    rng = np.random.default_rng(3)
    x = rng.standard_normal((44100 * 6, 2)) * 0.1
    aac = quant.detect_aac(quant.regions_of(x, quant.AAC_LONG, quant.AAC_SPAN, quant.AAC_WINDOWS), 44100)
    mp3 = quant.detect_mp3(quant.regions_of(x, quant.MP3_BLOCK, quant.MP3_SPAN, quant.MP3_WINDOWS), 44100)
    assert aac.z < QUANT_Z_WEAK and mp3.z < QUANT_Z_WEAK


def test_the_rule_setting_is_checked():
    assert Settings(_env_file=None, verification_rule="legacy").verification_rule == "legacy"
    with pytest.raises(ValueError):
        Settings(_env_file=None, verification_rule="strict")
