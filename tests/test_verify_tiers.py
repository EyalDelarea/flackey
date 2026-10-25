"""The three-tier check on real audio (issue #97): transcodes, genuine controls and damaged copies, all
synthetic -- pink noise is the closest stand-in for music that ffmpeg can make on its own.

Every FLAC made from a decoded lossy file is written with `-frame_size 4096`. ffmpeg's encoder otherwise
picks a tiny block size for such audio, which a check could learn to read instead of the audio itself."""
import json
import subprocess
from pathlib import Path

import pytest
from mutagen.flac import FLAC

from flackey import verify_report
from flackey.verify import VerifyError, analyze, verify
from tests.conftest import requires_ffmpeg

pytestmark = requires_ffmpeg


def _ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args], check=True)


def _flac(src: Path, out: Path, *filters: str, sample_fmt: str = "s16") -> Path:
    af = ["-af", ",".join(filters)] if filters else []
    _ffmpeg("-i", str(src), *af, "-ar", "44100", "-c:a", "flac", "-frame_size", "4096", "-sample_fmt", sample_fmt,
            str(out))
    assert FLAC(out).info.max_blocksize == 4096
    return out


@pytest.fixture(scope="module")
def d(tmp_path_factory) -> Path:
    return tmp_path_factory.mktemp("tiers")


@pytest.fixture(scope="module")
def pink(d: Path) -> Path:
    p = d / "pink.wav"
    _ffmpeg("-f", "lavfi", "-i", "anoisesrc=color=pink:seed=7:sample_rate=44100:duration=20:amplitude=0.5",
            "-ac", "2", "-c:a", "pcm_s24le", str(p))
    return p


def _transcode(pink: Path, d: Path, name: str, *enc: str, ext: str) -> Path:
    lossy = d / f"{name}.{ext}"
    _ffmpeg("-i", str(pink), *enc, str(lossy))
    return _flac(lossy, d / f"{name}.flac")


def test_a_genuine_flac_is_clear_and_its_promises_are_checked(pink: Path, d: Path):
    v = verify(_flac(pink, d / "genuine.flac"), None)
    assert v.passed and v.tier == "clear" and v.level == "clear"
    integ = v.analysis["integrity"]
    assert integ["failed"] is None and {"sample_count", "md5", "frame_crc"} <= set(integ["checked"])
    assert v.analysis["container"]["max_blocksize"] == 4096


def test_a_24_bit_flac_is_decoded_at_its_own_depth(pink: Path, d: Path):
    v = verify(_flac(pink, d / "g24.flac", sample_fmt="s32"), None)
    assert v.tier == "clear" and "md5" in v.analysis["integrity"]["checked"]
    assert v.analysis["bits"]["declared"] == 24


def test_an_mp3_128_in_a_flac_is_rejected_on_its_edge(pink: Path, d: Path):
    v = verify(_transcode(pink, d, "m128", "-c:a", "libmp3lame", "-b:a", "128k", ext="mp3"), None)
    assert not v.passed and v.tier == "reject" and v.cutoff_hz < 18_000 and "cutoff" in v.reason


def test_an_aac_256_in_a_flac_is_rejected_on_its_quantisation(pink: Path, d: Path):
    """The case the old check could not see: AAC at 256 kbps keeps the whole band, and only the grid its
    quantiser left behind gives it away."""
    v = verify(_transcode(pink, d, "a256", "-c:a", "aac", "-b:a", "256k", ext="m4a"), None)
    assert v.analysis["legacy"]["passed"]                      # the old check let it through
    assert v.tier == "reject" and "AAC" in v.reason


def test_an_mp3_320_in_a_flac_is_not_cleared(pink: Path, d: Path):
    v = verify(_transcode(pink, d, "m320", "-c:a", "libmp3lame", "-b:a", "320k", ext="mp3"), None)
    assert v.tier in ("reject", "unverified")


@pytest.mark.parametrize("name,filters", [
    ("brickwall", ("aresample=40000", "aresample=44100")),         # a converter's anti-alias filter
    ("resampled", ("aresample=48000", "aresample=44100:cutoff=0.91")),
    ("limited", ("volume=6dB", "alimiter=limit=0.9:level=false")),
])
def test_a_genuine_master_with_a_hard_top_or_a_loud_master_is_never_rejected(pink: Path, d: Path, name, filters):
    v = verify(_flac(pink, d / f"{name}.flac", *filters), None)
    assert v.tier != "reject", v.reason


def test_measurement_only_mode_keeps_the_old_verdict_and_records_the_new(pink: Path, d: Path):
    flac = d / "a256.flac"
    if not flac.exists():
        _transcode(pink, d, "a256", "-c:a", "aac", "-b:a", "256k", ext="m4a")
    v = verify(flac, None, rule="legacy")
    assert v.passed and v.tier is None and v.level == "clear"       # today's rule decides
    assert v.analysis["tier"] == "clear" and v.analysis["shadow_tier"] == "reject"   # the new one on record


def test_an_aac_reject_quotes_no_edge(pink: Path, d: Path):
    """Refused on the grid alone: the cutoff says "no edge", so the page never calls it an upscale."""
    flac = d / "a256.flac"
    if not flac.exists():
        _transcode(pink, d, "a256", "-c:a", "aac", "-b:a", "256k", ext="m4a")
    v = verify(flac, None)
    assert v.tier == "reject" and v.cutoff_hz == 22_050


def test_measurement_only_mode_passes_what_the_old_check_passed_even_when_the_decode_stumbles(pink: Path, d: Path,
                                                                                              monkeypatch):
    from flackey import verify as verify_mod

    def broken(*_a, **_k):
        raise VerifyError("decoder hiccup")

    monkeypatch.setattr(verify_mod, "analyze", broken)
    flac = _flac(pink, d / "stumble.flac")
    v = verify_mod.verify(flac, d / "png", rule="legacy")
    assert v.passed and v.analysis["error"] == "decoder hiccup"
    with pytest.raises(VerifyError):
        verify_mod.verify(flac, d / "png2", rule="tiers")
    assert not list((d / "png2").glob("*.png"))                  # the picture of a failed check is not left behind


@pytest.mark.parametrize("rule", ["tiers", "legacy"])
def test_a_crash_in_the_check_files_the_copy_as_unverified(pink: Path, d: Path, monkeypatch, rule):
    """A bug in the detectors is not a fact about the file: no refusal, no failed request, no deleted copy."""
    from flackey import verify as verify_mod

    def crash(*_a, **_k):
        raise IndexError("detector bug")

    monkeypatch.setattr(verify_mod, "analyze", crash)
    v = verify_mod.verify(_flac(pink, d / f"crash-{rule}.flac"), None, rule=rule)
    assert v.passed and "IndexError" in v.analysis["error"]
    assert v.level == ("unverified" if rule == "tiers" else "clear")


def test_a_lossless_file_below_cd_rate_is_rejected_as_before(pink: Path, d: Path):
    low = d / "half.flac"
    _ffmpeg("-i", str(pink), "-ar", "22050", "-c:a", "flac", "-sample_fmt", "s16", str(low))
    v = verify(low, None)
    assert v.analysis["legacy"]["passed"] is False
    assert v.tier == "reject" and v.cutoff_hz == 11_025


def test_a_glitch_in_an_mp3_is_concealed_as_before(pink: Path, d: Path):
    good = d / "glitch320.mp3"
    _ffmpeg("-i", str(pink), "-c:a", "libmp3lame", "-b:a", "320k", str(good))
    raw = bytearray(good.read_bytes())
    mid = len(raw) // 2
    raw[mid:mid + 2048] = bytes(2048)
    bad = d / "glitched320.mp3"
    bad.write_bytes(raw)
    assert verify(bad, None).tier in ("clear", "unverified")


def test_a_flipped_byte_in_a_flac_is_damage_not_a_transcode(pink: Path, d: Path):
    good = _flac(pink, d / "flip.flac")
    raw = bytearray(good.read_bytes())
    raw[len(raw) // 2] ^= 0xFF
    bad = d / "flipped.flac"
    bad.write_bytes(raw)
    with pytest.raises(VerifyError):
        verify(bad, None)


def test_a_flac_cut_short_is_damage(pink: Path, d: Path):
    good = _flac(pink, d / "cut.flac")
    short = d / "short.flac"
    short.write_bytes(good.read_bytes()[: good.stat().st_size * 2 // 3])
    with pytest.raises(VerifyError):
        verify(short, None)
    # Measured, not raised, when the decode itself gets through: the report sees which promise broke
    a = analyze(short) if _decodes(short) else None
    if a is not None:
        assert a["integrity"]["failed"]


def _decodes(path: Path) -> bool:
    return subprocess.run(["ffmpeg", "-v", "error", "-xerror", "-err_detect", "crccheck+explode", "-i", str(path),
                           "-f", "wav", "-"], capture_output=True, check=False).returncode == 0


def test_an_mp3_cut_short_is_damage(pink: Path, d: Path):
    full = d / "full320.mp3"
    _ffmpeg("-i", str(pink), "-c:a", "libmp3lame", "-b:a", "320k", str(full))
    short = d / "short320.mp3"
    short.write_bytes(full.read_bytes()[: full.stat().st_size // 2])
    with pytest.raises(VerifyError, match="damaged"):
        verify(short, None)


def test_ffmpegs_own_block_size_is_recorded(pink: Path, d: Path):
    lossy = d / "bs.mp3"
    _ffmpeg("-i", str(pink), "-c:a", "libmp3lame", "-b:a", "128k", str(lossy))
    tiny = d / "tiny_blocks.flac"
    _ffmpeg("-i", str(lossy), "-c:a", "flac", str(tiny))
    facts = analyze(tiny)["container"]
    assert facts["ffmpeg_made"] and "max_blocksize" in facts


def test_the_folder_report_compares_both_rules(pink: Path, d: Path, tmp_path: Path, capsys):
    folder = tmp_path / "lib"
    folder.mkdir()
    _flac(pink, folder / "one.flac")
    _transcode(pink, folder, "two", "-c:a", "aac", "-b:a", "256k", ext="m4a")
    (folder / "two.m4a").unlink()
    out = tmp_path / "r.jsonl"
    verify_report.main([str(folder), str(out), "--jobs", "1"])
    rows = [json.loads(line) for line in out.read_text().splitlines()]
    assert sorted((r["old"], r["new"]) for r in rows) == [("clear", "clear"), ("clear", "reject")]
    printed = capsys.readouterr().out
    assert "files: 2" in printed and "clear->reject: 1" in printed and "one.flac" not in printed
