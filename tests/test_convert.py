import hashlib
import json
import subprocess
from pathlib import Path

import pytest
from mutagen.flac import FLAC, Picture

from flackey.convert import ConvertError, _distinct, to_format
from flackey.verify import probe
from tests.conftest import requires_ffmpeg

pytestmark = requires_ffmpeg


def decode(path: Path) -> tuple[str, int]:
    """(md5 of the decoded stereo s32 PCM, samples per channel): the audio, independent of container."""
    out = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vn", "-map", "0:a", "-f", "s32le", "-ac", "2", "-"],
                         capture_output=True, check=True).stdout
    return hashlib.md5(out).hexdigest(), len(out) // 8


def streams(path: Path) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
                         capture_output=True, check=True).stdout
    return json.loads(out)


@pytest.fixture(scope="module")
def tagged_flac(tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("convert")
    wav = d / "noise.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "anoisesrc=color=pink:seed=7:duration=3:sample_rate=44100",
                    "-ac", "2", "-c:a", "pcm_s16le", str(wav)], check=True)
    flac = d / "noise.flac"
    subprocess.run(["ffmpeg", "-v", "error", "-i", str(wav), "-c:a", "flac", str(flac)], check=True)
    f = FLAC(flac)
    f["title"], f["artist"], f["comment"] = ["Peer Title"], ["Peer Artist"], ["ripped by someone"]
    pic = Picture()
    pic.type, pic.mime, pic.data = 3, "image/png", bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c6360000002000155"
        "0d0a2f0000000049454e44ae426082")
    f.add_picture(pic)
    f.save()
    return flac


def test_aiff_has_identical_audio_and_no_foreign_metadata(tagged_flac: Path):
    out = to_format(tagged_flac, "aiff", 16)
    assert out == tagged_flac.with_suffix(".aiff") and out.exists()
    assert decode(out) == decode(tagged_flac)
    info = streams(out)
    assert probe(out).fmt == "aiff" and probe(out).bit_depth == 16
    assert [s["codec_type"] for s in info["streams"]] == ["audio"]          # the picture stream is gone
    tags = {k.lower() for k in (info["format"].get("tags") or {})}
    assert not tags & {"title", "artist", "comment"}


def test_wav_and_24_bit_codecs(tagged_flac: Path):
    wav = to_format(tagged_flac, "wav", None)
    assert probe(wav).fmt == "wav" and decode(wav) == decode(tagged_flac)
    hi = to_format(tagged_flac, "aiff", 24)
    assert probe(hi).bit_depth == 24 and probe(hi).fmt == "aiff"


def test_flac_from_flac_rebuilds_the_container_without_touching_src(tagged_flac: Path):
    out = to_format(tagged_flac, "flac", 16)
    assert out != tagged_flac and out.exists()                 # a new path, src untouched
    assert out.parent == tagged_flac.parent and out.name == "noise.clean.flac"
    assert tagged_flac.exists()                                 # src was not clobbered in place
    assert decode(out) == decode(tagged_flac)                   # bit-identical audio (-c:a copy)
    info = streams(out)
    assert probe(out).fmt == "flac"
    assert [s["codec_type"] for s in info["streams"]] == ["audio"]          # the picture stream is gone
    tags = {k.lower() for k in (info["format"].get("tags") or {})}
    assert not tags & {"title", "artist", "comment"}


def test_flac_from_wav_encodes(tmp_path: Path):
    wav = tmp_path / "noise.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "anoisesrc=color=pink:seed=3:duration=2:sample_rate=44100",
                    "-ac", "2", "-c:a", "pcm_s16le", str(wav)], check=True)
    out = to_format(wav, "flac", 16)
    assert out != wav and out.exists() and out.suffix == ".flac"
    assert probe(out).fmt == "flac"
    assert decode(out) == decode(wav)                            # still lossless despite the real encode


def test_bad_input_raises(tmp_path: Path):
    junk = tmp_path / "junk.flac"
    junk.write_bytes(b"not audio")
    with pytest.raises(ConvertError):
        to_format(junk, "aiff", 16)
    with pytest.raises(ConvertError):
        to_format(junk, "mp3", 16)
    with pytest.raises(ConvertError):
        to_format(junk, "flac", 16)


@pytest.mark.parametrize("fmt", ["wav", "aiff"])
def test_a_source_already_in_the_filing_format_is_not_handed_to_ffmpeg_as_its_own_output(tmp_path: Path, fmt: str):
    # The live failure: a peer's .wav arrives while `lossless_filing_format` is "wav" (the default), so
    # `src.with_suffix(".wav")` is `src` and ffmpeg exits with "Output ... same as Input #0". Every WAV a
    # peer offered failed this way; only FLAC sources ever got filed.
    src = tmp_path / f"track.{fmt}"
    codec = "pcm_s16le" if fmt == "wav" else "pcm_s16be"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                    "anoisesrc=color=pink:seed=3:duration=1:sample_rate=44100", "-ac", "2", "-c:a", codec,
                    str(src)], check=True)
    out = to_format(src, fmt, 16)

    assert out != src and out.exists() and src.exists()
    assert probe(out).fmt == fmt and decode(out) == decode(src)


def test_the_distinct_name_ignores_case_because_the_filesystem_does(tmp_path: Path):
    # On macOS `x.WAV` and `x.wav` are one file, so a case-sensitive comparison would still collide.
    assert _distinct(Path("/t/x.WAV"), "wav") == Path("/t/x.clean.wav")
    assert _distinct(Path("/t/x.flac"), "wav") == Path("/t/x.wav")
