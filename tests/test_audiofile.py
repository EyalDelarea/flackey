"""A file is checked for what its first bytes say it is before ffmpeg, ffprobe or fpcalc reads it, and
those tools are then told which demuxer to use and which protocols they may open."""
import json
import subprocess
from pathlib import Path

import pytest

from flackey import audiofile, convert, fingerprint, verify
from flackey.audiofile import WrongFormat, demuxer, input_args, sniff

FLAC = b"fLaC\x00\x00\x00\x22" + b"\x00" * 64
WAV = b"RIFF\x24\x00\x00\x00WAVEfmt " + b"\x00" * 64
RF64 = b"RF64\xff\xff\xff\xffWAVEds64" + b"\x00" * 64
AIFF = b"FORM\x00\x00\x00\x40AIFFCOMM" + b"\x00" * 64
AIFC = b"FORM\x00\x00\x00\x40AIFCFVER" + b"\x00" * 64
MP3_SYNC = b"\xff\xfb\x90\x64" + b"\x00" * 64


def id3(body: bytes, size: int = 20, footer: bool = False) -> bytes:
    syncsafe = bytes([(size >> 21) & 0x7F, (size >> 14) & 0x7F, (size >> 7) & 0x7F, size & 0x7F])
    tag = b"ID3\x04\x00" + bytes([0x10 if footer else 0]) + syncsafe + b"\x00" * size
    return tag + (b"3DI" + b"\x00" * 7 if footer else b"") + body


def put(tmp_path: Path, name: str, payload: bytes) -> Path:
    p = tmp_path / name
    p.write_bytes(payload)
    return p


@pytest.mark.parametrize(("payload", "fmt"), [
    (FLAC, "flac"), (WAV, "wav"), (RF64, "wav"), (AIFF, "aiff"), (AIFC, "aiff"), (MP3_SYNC, "mp3"),
    (id3(MP3_SYNC), "mp3"), (id3(FLAC), "flac"), (id3(FLAC, size=300, footer=True), "flac"),
    (b"\x1aE\xdf\xa3" + b"\x00" * 32, None), (b"", None), (b"not audio", None),
])
def test_sniff_names_the_container_from_its_first_bytes(tmp_path: Path, payload: bytes, fmt: str | None):
    assert sniff(put(tmp_path, "x.bin", payload)) == fmt


@pytest.mark.parametrize(("name", "payload", "fmt"), [
    ("a.flac", FLAC, "flac"), ("a.FLAC", FLAC, "flac"), ("a.wav", WAV, "wav"), ("a.aif", AIFF, "aiff"),
    ("a.aiff", AIFC, "aiff"), ("a.mp3", id3(MP3_SYNC), "mp3"),
])
def test_a_claimed_extension_whose_bytes_agree_gets_its_demuxer(tmp_path: Path, name: str, payload: bytes, fmt: str):
    assert demuxer(put(tmp_path, name, payload)) == fmt


@pytest.mark.parametrize(("name", "payload"), [
    ("a.flac", WAV), ("a.wav", FLAC), ("a.aiff", MP3_SYNC), ("a.mp3", AIFF), ("a.flac", b"not audio"),
    ("a.wav", b"#EXTM3U\n"), ("a.flac", b""),
])
def test_bytes_that_disagree_with_the_extension_are_refused(tmp_path: Path, name: str, payload: bytes):
    with pytest.raises(WrongFormat):
        demuxer(put(tmp_path, name, payload))


def test_an_extension_with_no_claim_is_left_to_the_tool(tmp_path: Path):
    """A YouTube reference arrives as webm, m4a or opus; nothing here forces a demuxer on those."""
    assert demuxer(put(tmp_path, "a.webm", b"\x1aE\xdf\xa3")) is None
    assert input_args(put(tmp_path, "a.m4a", b"....ftyp")) == ["-protocol_whitelist", "file,pipe"]


def test_input_args_force_the_demuxer_and_the_protocols(tmp_path: Path):
    assert input_args(put(tmp_path, "a.flac", FLAC)) == ["-protocol_whitelist", "file,pipe", "-f", "flac"]


class Ran:
    def __init__(self, stdout: bytes = b"") -> None:
        self.cmds: list[list[str]] = []
        self.stdout = stdout

    def __call__(self, cmd, **kw):
        self.cmds.append(list(cmd))
        # Only ffprobe's answer is canned; the fake ffmpeg prints nothing.
        out = self.stdout if not cmd[0].endswith("ffmpeg") else b""
        return subprocess.CompletedProcess(cmd, 0, out, b"")


def before_input(cmd: list[str], src: Path) -> list[str]:
    return cmd[:cmd.index(str(src))]


PROBE_JSON = json.dumps({"streams": [{"codec_type": "audio", "codec_name": "flac", "sample_rate": "44100",
                                      "bits_per_raw_sample": "16"}],
                         "format": {"duration": "60", "bit_rate": "900000"}}).encode()


def test_ffprobe_and_ffmpeg_in_verify_are_pinned_to_the_demuxer(tmp_path: Path, monkeypatch):
    src = put(tmp_path, "x.flac", FLAC)
    ran = Ran(PROBE_JSON)
    monkeypatch.setattr(verify, "tool_path", lambda name: f"/bin/{name}")
    monkeypatch.setattr(verify.subprocess, "run", ran)
    verify.probe(src)
    with pytest.raises(verify.VerifyError):      # the fake ffmpeg returns no samples
        verify.band_levels_db(src, duration_s=60)
    verify.spectrogram_png(src, tmp_path / "s.png", duration_s=60)
    assert len(ran.cmds) == 3
    for cmd in ran.cmds:
        head = before_input(cmd, src)
        assert head[-1] == "-i" or cmd[0].endswith("ffprobe")
        assert ["-protocol_whitelist", "file,pipe"] == head[head.index("-protocol_whitelist"):][:2]
        assert head[head.index("-f") + 1] == "flac"


def test_verify_refuses_a_mismatched_file_before_running_anything(tmp_path: Path, monkeypatch):
    ran = Ran(PROBE_JSON)
    monkeypatch.setattr(verify, "tool_path", lambda name: f"/bin/{name}")
    monkeypatch.setattr(verify.subprocess, "run", ran)
    with pytest.raises(verify.VerifyError, match="not a flac"):
        verify.verify(put(tmp_path, "x.flac", WAV), tmp_path)
    assert ran.cmds == []


def test_convert_pins_the_demuxer_and_refuses_a_mismatch(tmp_path: Path, monkeypatch):
    ran = Ran()
    monkeypatch.setattr(convert, "tool_path", lambda name: f"/bin/{name}")
    monkeypatch.setattr(convert.subprocess, "run", ran)
    with pytest.raises(convert.ConvertError, match="not a wav"):
        convert.to_format(put(tmp_path, "x.wav", FLAC), "aiff", 16)
    assert ran.cmds == []
    src = put(tmp_path, "y.wav", WAV)
    with pytest.raises(convert.ConvertError):    # the fake ffmpeg writes no file
        convert.to_format(src, "aiff", 16)
    head = before_input(ran.cmds[0], src)
    assert head[-1] == "-i" and head[head.index("-f") + 1] == "wav"
    assert head[head.index("-protocol_whitelist") + 1] == "file,pipe"


def test_fpcalc_is_told_the_format_and_refuses_a_mismatch(tmp_path: Path, monkeypatch):
    ran = Ran(json.dumps({"fingerprint": [1, 2, 3]}).encode())
    monkeypatch.setattr(fingerprint, "tool_path", lambda name: f"/bin/{name}")
    monkeypatch.setattr(fingerprint.subprocess, "run", ran)
    with pytest.raises(fingerprint.FingerprintError, match="not a flac"):
        fingerprint.fingerprint(put(tmp_path, "x.flac", AIFF))
    assert ran.cmds == []
    src = put(tmp_path, "y.aiff", AIFF)
    assert fingerprint.fingerprint(src) == [1, 2, 3]
    assert before_input(ran.cmds[0], src)[-2:] == ["-format", "aiff"]


def test_the_fingerprint_trim_pins_the_demuxer(tmp_path: Path, monkeypatch):
    def run(cmd, **kw):
        ran.cmds.append(list(cmd))
        if cmd[0].endswith("ffmpeg"):
            Path(cmd[-1]).write_bytes(WAV)       # what ffmpeg would write: a wav
        return subprocess.CompletedProcess(cmd, 0, json.dumps({"fingerprint": [7]}).encode(), b"")

    ran = Ran()
    monkeypatch.setattr(fingerprint, "tool_path", lambda name: f"/bin/{name}")
    monkeypatch.setattr(fingerprint.subprocess, "run", run)
    src = put(tmp_path, "y.mp3", id3(MP3_SYNC))
    assert fingerprint.fingerprint(src, 10, 30) == [7]
    trim, calc = ran.cmds
    head = before_input(trim, src)
    assert head[-1] == "-i" and head[head.index("-f") + 1] == "mp3"
    assert head[head.index("-protocol_whitelist") + 1] == "file,pipe"
    assert calc[-3:-1] == ["-format", "wav"]


def test_the_module_reads_only_the_head(tmp_path: Path):
    """A multi-gigabyte file is sniffed from its first bytes, not read whole."""
    big = put(tmp_path, "x.flac", FLAC + b"\x00" * (audiofile.HEAD_BYTES * 4))
    assert demuxer(big) == "flac"


def test_convert_bounds_the_output_and_refuses_one_that_hit_the_bound(tmp_path: Path, monkeypatch):
    """ffmpeg exits 0 when `-fs` cuts it short, so reaching the ceiling counts as a failure, not a file."""
    written = {"bytes": 10}

    def run(cmd, **kw):
        ran.cmds.append(list(cmd))
        Path(cmd[-1]).write_bytes(b"\x00" * written["bytes"])
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    ran = Ran()
    monkeypatch.setattr(convert, "tool_path", lambda name: f"/bin/{name}")
    monkeypatch.setattr(convert.subprocess, "run", run)
    monkeypatch.setattr(convert, "MAX_OUTPUT_BYTES", 100)
    src = put(tmp_path, "y.wav", WAV)
    out = convert.to_format(src, "aiff", 16)
    tail = ran.cmds[0][ran.cmds[0].index(str(src)) + 1:]
    assert tail[tail.index("-t") + 1] == str(convert.MAX_OUTPUT_S)
    assert tail[tail.index("-fs") + 1] == "100"
    out.unlink()
    written["bytes"] = 100
    with pytest.raises(convert.ConvertError, match="limit"):
        convert.to_format(src, "aiff", 16)
    assert not out.exists()
