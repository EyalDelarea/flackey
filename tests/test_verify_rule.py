"""The decision rule and the evidence plumbing, without any audio tool (issue #97)."""
import io

import numpy as np
import pytest

from flackey import evidence, quant
from flackey.config import Settings
from flackey.models import NO_EDGE_HZ, Verdict
from flackey.verify import (
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
    ({"edge": 19_400}, "reject"),                               # LAME 256
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
