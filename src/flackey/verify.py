from __future__ import annotations

import itertools
import json
import logging
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import evidence
from .audiofile import DEMUXERS, WrongFormat, arrived_empty, input_args
from .models import NO_EDGE_HZ, Verdict, edge_text
from .tools import no_window, tool_path

log = logging.getLogger(__name__)

MIN_MP3_BITRATE = 320
MIN_MP3_CUTOFF = 18_000
MIN_LOSSLESS_CUTOFF = 20_000
LOSSLESS = {"flac", "wav", "aiff"}
SR = 44_100
FRAME = 4096
BAND_HZ = 250
CLIFF_DB = 20        # an encoder lowpass drops at least this much within 500 Hz; music rarely does
CLIFF_SEARCH_FROM_HZ = 8_000


class VerifyError(Exception):
    """The file could not be read through: a decode that failed, a transfer cut short, a tool that timed out.
    Says nothing about where the audio came from, only that this copy could not be checked (issue #97)."""


class ToolMissing(VerifyError):
    """ffmpeg or ffprobe is not on this machine. A fact about the computer, not the file: every other copy
    would fail the same way, so the caller must not read it as one more bad download."""


class Disguised(VerifyError):
    """The bytes are not the format the name claims -- an MP3, an AAC or an Ogg called .flac. Unlike a plain
    VerifyError this one does say something about the source, and it is the oldest trick there is."""


@dataclass
class Probe:
    fmt: str
    bitrate_kbps: int
    duration_s: float
    sample_rate: int
    bit_depth: int | None = None


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
        raise ToolMissing(f"{name} is not installed")
    cmd = [found, *cmd[1:]]
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=RUN_TIMEOUT_S, check=False, **no_window())
    except subprocess.TimeoutExpired as e:
        raise VerifyError(f"{name} timed out after {RUN_TIMEOUT_S}s") from e
    if p.returncode != 0:
        raise VerifyError(p.stderr.decode(errors="replace")[-400:])
    return p.stdout


def _input(path: Path, tool: str) -> list[str]:
    """The options that pin `path`'s demuxer and protocols (see `audiofile`), checked before any tool runs.
    A missing `tool` is still the first thing reported: it is what a machine without the helpers needs
    to hear, whatever the file is."""
    if tool_path(tool) is None:
        raise ToolMissing(f"{tool} is not installed")
    try:
        # A file with nothing in it is a transfer that never arrived, whatever its first bytes say -- and
        # there is nothing for the tool to read, so it is not run.
        if arrived_empty(path):
            raise VerifyError("nothing arrived: the file is empty or only zero bytes")
        return input_args(path)
    except WrongFormat as e:
        # Anything else under the wrong name is a disguise, whatever it turns out to be.
        raise Disguised(str(e)) from e
    except OSError as e:
        raise VerifyError(str(e)) from e


def probe(path: Path) -> Probe:
    out = _run(["ffprobe", "-v", "error", *_input(path, "ffprobe"), "-print_format", "json", "-show_format", "-show_streams",
                str(path)])
    data = json.loads(out)
    audio = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), None)
    if audio is None:
        raise VerifyError("no audio stream")
    codec = audio.get("codec_name", "")
    fmt = {"pcm_s16le": "wav", "pcm_s24le": "wav", "pcm_s16be": "aiff", "pcm_s24be": "aiff"}.get(codec, codec)
    if fmt == "wav" and str(path).lower().endswith((".aif", ".aiff")):
        fmt = "aiff"
    bitrate = int(audio.get("bit_rate") or data["format"].get("bit_rate") or 0)
    bits = int(audio.get("bits_per_raw_sample") or audio.get("bits_per_sample") or 0) or None
    if fmt == "mp3":
        bits = None
    return Probe(fmt=fmt, bitrate_kbps=round(bitrate / 1000), duration_s=float(data["format"].get("duration", 0)),
                 sample_rate=int(audio.get("sample_rate", 0)), bit_depth=bits)


def _window(path: Path, window_s: int, duration_s: float | None) -> tuple[float, float]:
    dur = probe(path).duration_s if duration_s is None else duration_s
    if dur <= window_s:
        return 0.0, dur
    return (dur - window_s) / 2, float(window_s)


def band_levels_db(path: Path, window_s: int = 60, duration_s: float | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Average power per 250 Hz band over the middle window. Returns (band start Hz, level dB)."""
    start, length = _window(path, window_s, duration_s)
    pcm = _run(["ffmpeg", "-v", "error", "-ss", f"{start:.2f}", "-t", f"{length:.2f}", *_input(path, "ffmpeg"), "-i", str(path),
                "-ac", "1", "-ar", str(SR), "-f", "s16le", "-"])
    x = np.frombuffer(pcm, dtype=np.int16).astype(np.float64) / 32768.0
    n = (len(x) // FRAME) * FRAME
    if n == 0:
        raise VerifyError("audio too short to analyze")
    frames = x[:n].reshape(-1, FRAME) * np.hanning(FRAME)
    power = np.mean(np.abs(np.fft.rfft(frames, axis=1)) ** 2, axis=0)
    db = 10 * np.log10(power + 1e-20)
    freqs = np.fft.rfftfreq(FRAME, 1 / SR)
    edges = np.arange(0, SR / 2 + BAND_HZ, BAND_HZ)
    levels = np.array([db[(freqs >= lo) & (freqs < hi)].mean() for lo, hi in itertools.pairwise(edges)])
    return edges[:-1], levels


def spectral_cutoff_hz(path: Path, window_s: int = 60, duration_s: float | None = None) -> int:
    """Frequency of the encoder lowpass cliff, or NO_EDGE_HZ (Nyquist, 22050) when none is found.

    A lossy encoder zeroes everything above its lowpass, so the level falls by tens of dB within
    a few hundred hertz. Natural music usually rolls off gradually, a few dB per band. Usually, not
    always: a resampler or a mastering lowpass can make the same step, so a cliff is evidence of a
    lossy source rather than proof, and no cliff is not proof of a lossless one either (issue #97).
    We take the *highest* cliff so a notch lower in the spectrum cannot masquerade as the edge.
    """
    starts, levels = band_levels_db(path, window_s, duration_s)
    region = starts >= CLIFF_SEARCH_FROM_HZ
    hz, db = starts[region], levels[region]
    drops = db[:-2] - db[2:]                      # level lost across two adjacent bands (500 Hz)
    cliffs = np.where(drops >= CLIFF_DB)[0]
    if len(cliffs) == 0:
        return NO_EDGE_HZ
    i = int(cliffs[-1])
    return int(round(hz[i + 1] / 50) * 50)


def spectrogram_png(path: Path, out: Path, window_s: int = 60, duration_s: float | None = None) -> Path:
    start, length = _window(path, window_s, duration_s)
    out.parent.mkdir(parents=True, exist_ok=True)
    _run(["ffmpeg", "-v", "error", "-y", "-ss", f"{start:.2f}", "-t", f"{length:.2f}", *_input(path, "ffmpeg"),
          "-i", str(path),
          "-filter_complex", "[0:a]showspectrumpic=s=1200x400:legend=1:scale=log:color=intensity[o]",
          "-map", "[o]", "-frames:v", "1", str(out)])
    return out


# --- issue #97: one streaming decode, evidence families, three tiers ------------------------------------

DETECTOR_VERSION = evidence.EVIDENCE_VERSION
RULES = ("tiers", "legacy")
READ_BYTES = 1 << 20
# Spectral edge in a lossless container (fine half-drop point, Hz). LAME 256 and below end under 19.9 kHz;
# from there to 21.5 kHz genuine masters (converter passbands, resamplers) and LAME 320 / Opus end alike,
# so an edge there alone is never a reject.
EDGE_REJECT_BELOW_HZ = 19_900
# ...on its own word only below this. From here to EDGE_REJECT_BELOW_HZ a mastering lowpass or a steep
# converter filter can end a genuine master just as LAME 256 does (19.1-19.6 kHz), so an edge there needs
# the quantisation family to agree before it rejects; alone it is unverified. LAME 256 always agreed on
# the corpus (z 14.7 and up), so this costs no detection there.
EDGE_REJECT_ALONE_BELOW_HZ = 19_000
EDGE_AMBIGUOUS_TO_HZ = 21_500
# An edge that sounding frames reach past this often is a roll-off, not an encoder's lowpass.
EDGE_REACHED_ABOVE = 0.02
# An MP3's own edge: LAME 320 ends at 20.1-20.6 kHz, 256 at 19.4-19.9 kHz, 128 at 16-17 kHz. Below the old
# floor is still a reject; between it and 320's own edge the file is filed, as unverified: a 320 kbps file
# made from a 256 one, or an encoder with a lower lowpass, and nothing here tells the two apart.
MP3_EDGE_REJECT_BELOW_HZ = MIN_MP3_CUTOFF
MP3_EDGE_CLEAR_FROM_HZ = 19_900
# Derrien quantisation traces: how far the best frame alignment stands above every other (robust z).
QUANT_Z_STRONG = 12.0
QUANT_Z_WEAK = 8.0


def _decode_args(path: Path, pr: Probe) -> tuple[list[str], int]:
    """ffmpeg arguments for one pass over the whole file as little-endian PCM in a WAV stream, and the bit
    depth it comes out at: the file's own for 16- and 24-bit lossless (so a FLAC's MD5 can be checked on
    it), 16 for an MP3, 24 for anything deeper. WAV because the slim ffmpeg the app ships has the wav
    muxer and the pcm_s16le/pcm_s24le encoders, and no raw s24le muxer."""
    bits = 16 if pr.fmt == "mp3" or (pr.bit_depth or 16) <= 16 else 24
    # FLAC's per-frame CRC-16 is only checked under crccheck; explode turns a broken frame into an exit
    # code instead of a concealed glitch. The MP3 decoder conceals by design, so it is left to it.
    detect = ["-xerror", "-err_detect", "crccheck+explode"] if pr.fmt == "flac" else []
    return (["ffmpeg", "-v", "error", *detect, *_input(path, "ffmpeg"), "-i", str(path),
             "-map", "0:a:0", "-vn", "-map_metadata", "-1", "-fflags", "+bitexact",
             "-c:a", f"pcm_s{bits}le", "-f", "wav", "-"], bits)


def _skip_wav_header(stream) -> None:
    """Read a piped WAV stream up to the start of its data chunk."""
    head = stream.read(12)
    if len(head) < 12 or head[:4] != b"RIFF" or head[8:12] != b"WAVE":
        raise VerifyError("the decoder did not produce audio")
    while True:
        chunk = stream.read(8)
        if len(chunk) < 8:
            raise VerifyError("the decoder did not produce audio")
        size = int.from_bytes(chunk[4:], "little")
        if chunk[:4] == b"data":
            return
        stream.read(size + (size & 1))


def _stream(cmd: list[str], sink) -> None:
    """Run `cmd` and hand its WAV stdout, past the header, to `sink` in chunks. Raises like `_run`: a missing
    tool, a timeout, or a non-zero exit (with the tool's last words)."""
    name = cmd[0]
    found = tool_path(name)
    if found is None:
        raise ToolMissing(f"{name} is not installed")
    proc = subprocess.Popen([found, *cmd[1:]], stdout=subprocess.PIPE, stderr=subprocess.PIPE, **no_window())
    err: list[bytes] = []
    drain = threading.Thread(target=lambda: err.append(proc.stderr.read()), daemon=True)
    drain.start()
    timed_out = threading.Event()

    def kill() -> None:
        timed_out.set()
        proc.kill()

    timer = threading.Timer(RUN_TIMEOUT_S, kill)
    timer.start()
    try:
        try:
            _skip_wav_header(proc.stdout)
            while chunk := proc.stdout.read(READ_BYTES):
                sink(chunk)
        except VerifyError:
            if proc.wait() == 0 or timed_out.is_set():
                raise
            # the tool failed before writing audio: its own message says why, below
        code = proc.wait()
    except BaseException:
        proc.kill()
        proc.wait()
        raise
    finally:
        timer.cancel()
        drain.join(timeout=5)
        proc.stdout.close()
        proc.stderr.close()
    if timed_out.is_set():
        raise VerifyError(f"{name} timed out after {RUN_TIMEOUT_S}s")
    if code != 0:
        raise VerifyError(b"".join(err).decode(errors="replace")[-400:] or f"{name} exited with {code}")


def _channels(path: Path) -> int:
    out = _run(["ffprobe", "-v", "error", *_input(path, "ffprobe"), "-select_streams", "a:0",
                "-show_entries", "stream=channels", "-of", "json", str(path)])
    streams = json.loads(out).get("streams") or [{}]
    ch = int(streams[0].get("channels") or 0)
    if not 1 <= ch <= 8:
        raise VerifyError(f"{ch} audio channels cannot be analysed")
    return ch


def analyze(path: Path, pr: Probe | None = None, *, quant_detectors: bool = True) -> dict:
    """Every evidence family's measurements for `path`, from one decode of the whole file. Raises
    VerifyError when the file cannot be read through; a container promise the decode broke is reported in
    `integrity.failed`, not raised, so measurement-only mode can still record it."""
    pr = pr or probe(path)
    if not 0 < pr.sample_rate <= 384_000:
        raise VerifyError(f"sample rate {pr.sample_rate} Hz cannot be analysed")
    facts = evidence.container_facts(path, pr.fmt)
    cmd, bits = _decode_args(path, pr)
    channels = _channels(path)
    an = evidence.Analyzer(pr.sample_rate, channels, bits,
                           quant_detectors=quant_detectors and pr.fmt in LOSSLESS)
    t0 = time.monotonic()
    _stream(cmd, an.feed)
    measured = an.finish()
    if measured["samples"] == 0:
        raise VerifyError("the file decoded to no audio")
    native = pr.fmt == "flac" and facts.get("bits") == bits
    return {
        "version": DETECTOR_VERSION,
        "probe": {"fmt": pr.fmt, "bitrate_kbps": pr.bitrate_kbps, "sample_rate": pr.sample_rate,
                  "bit_depth": pr.bit_depth, "channels": channels, "duration_s": round(pr.duration_s, 2)},
        "container": facts,
        "integrity": evidence.integrity(facts, measured["samples"], measured["md5"], pr.sample_rate, native),
        "spectral": measured["spectral"],
        "quant": measured.get("quant"),
        "bits": measured["bits"],
        "seconds": round(time.monotonic() - t0, 2),
    }


def quant_finding(q: dict | None) -> tuple[str, str | None, float]:
    """The quantisation family: "strong", "weak", "clean" or "not_run", with the codec and its z."""
    found = [(r["z"], r["codec"]) for r in ((q or {}).get("aac"), (q or {}).get("mp3")) if r]
    if not found:
        return "not_run", None, 0.0
    z, codec = max(found)
    if z >= QUANT_Z_STRONG:
        return "strong", codec, z
    if z >= QUANT_Z_WEAK:
        return "weak", codec, z
    return "clean", codec, z


def edge_finding(spec: dict, fmt: str) -> tuple[str, int | None]:
    """The spectral family: "insufficient", "none", "high" (an edge above every threshold), "ambiguous" or
    "unambiguous", and the edge in Hz."""
    if not spec.get("judged"):
        return "insufficient", None
    edge = spec.get("edge_hz")
    if edge is None:
        return "none", None
    reached = spec.get("frames_above_edge")
    if reached is not None and reached > EDGE_REACHED_ABOVE:
        return "none", edge                    # sound reaches past it: a roll-off, not a lowpass
    if fmt == "mp3":
        if edge < MP3_EDGE_REJECT_BELOW_HZ:
            return "unambiguous", edge
        return ("ambiguous" if edge < MP3_EDGE_CLEAR_FROM_HZ else "high"), edge
    if edge < EDGE_REJECT_BELOW_HZ:
        return "unambiguous", edge
    if edge <= EDGE_AMBIGUOUS_TO_HZ:
        return "ambiguous", edge
    return "high", edge


CODEC_NAMES = {"mp3": "MP3", "aac": "AAC"}


def decide(analysis: dict, fmt: str) -> tuple[str, str, int]:
    """The three-tier verdict from the evidence families: (tier, reason, cutoff_hz for the Verdict).

    reject:     one unambiguous family (an edge below EDGE_REJECT_ALONE_BELOW_HZ that nothing reaches
                past; strong quantisation traces), or two families agreeing (an edge below
                EDGE_REJECT_BELOW_HZ, or an ambiguous one, with any quantisation traces);
    unverified: one ambiguous family alone, or too little sound to judge;
    clear:      none of the above.
    An MP3 is judged on its edge alone (below MIN_MP3_CUTOFF a reject, as before; short of
    MP3_EDGE_CLEAR_FROM_HZ unverified): it is lossy by definition, the only question is whether it was
    made from something smaller."""
    edge_kind, edge = edge_finding(analysis.get("spectral") or {}, fmt)
    cutoff = edge if edge is not None and edge_kind != "none" else NO_EDGE_HZ
    if fmt == "mp3":
        if edge_kind == "unambiguous":
            return ("reject", (f"cutoff at {edge} Hz, below {MP3_EDGE_REJECT_BELOW_HZ}: consistent with a "
                    "lower-bitrate source"), cutoff)
        if edge_kind == "ambiguous":
            return ("unverified", (f"{analysis['probe']['bitrate_kbps']} kbps, but nothing above {edge} Hz, "
                    "short of where a 320 kbps encode usually reaches"), cutoff)
        return "clear", f"{analysis['probe']['bitrate_kbps']} kbps, {edge_text(cutoff)}", cutoff
    sr = analysis["probe"].get("sample_rate") or SR
    if sr < SR:
        # Nothing above sr/2 can be in it at all: the old check read that as a lowpass at sr/2, and so does
        # this one. A 22.05 kHz "lossless" file is a phone-call-grade copy whatever made it.
        top = sr // 2
        return "reject", f"lossless container but cutoff at {top} Hz: consistent with a lossy source", top
    q_kind, codec, _ = quant_finding(analysis.get("quant"))
    name = CODEC_NAMES.get(codec or "", "lossy")
    if edge_kind == "unambiguous" and edge >= EDGE_REJECT_ALONE_BELOW_HZ and q_kind not in ("strong", "weak"):
        edge_kind = "ambiguous"         # a genuine master's lowpass can end here too; nothing else agrees
    if edge_kind == "unambiguous":
        return "reject", f"lossless container but cutoff at {edge} Hz: consistent with a lossy source", cutoff
    if q_kind == "strong":
        # The edge, if any, is healthy here: quoting it would read as the reason. NO_EDGE_HZ says "not refused
        # on the spectrum" to the page, and is still a cutoff, so the copy is kept to be overruled.
        return ("reject", ("lossless container but the audio carries the quantisation pattern of an earlier "
                f"{name} encode: consistent with a lossy source"), NO_EDGE_HZ)
    if edge_kind == "ambiguous" and q_kind == "weak":
        return ("reject", (f"lossless container but cutoff at {edge} Hz and traces of an earlier {name} "
                "encode: consistent with a lossy source"), cutoff)
    if edge_kind == "ambiguous":
        return ("unverified", (f"lossless, but nothing above {edge} Hz, which a genuine master and a lossy "
                "source can both show"), cutoff)
    if q_kind == "weak":
        return "unverified", f"lossless, but faint traces of an earlier {name} encode", cutoff
    if edge_kind == "insufficient":
        return "unverified", "lossless, but too little sound to check", cutoff
    return "clear", f"lossless, {edge_text(cutoff)}", cutoff


def legacy_verdict(pr: Probe, cutoff: int) -> tuple[bool, str]:
    """Today's rule, kept exactly: the middle-minute cutoff against one number per format. The verdict in
    measurement-only mode, and the "old check" column of a folder report."""
    if pr.fmt == "mp3":
        if cutoff < MIN_MP3_CUTOFF:
            return False, f"cutoff at {cutoff} Hz, below {MIN_MP3_CUTOFF}: consistent with a lower-bitrate source"
        return True, f"{pr.bitrate_kbps} kbps, {edge_text(cutoff)}"
    if cutoff < MIN_LOSSLESS_CUTOFF:
        return False, f"lossless container but cutoff at {cutoff} Hz: consistent with a lossy source"
    return True, f"lossless, {edge_text(cutoff)}"


def verify(path: Path, spectrogram_dir: Path | None, name: str | None = None, rule: str = "tiers") -> Verdict:
    """The verdict on one downloaded file. `rule` "tiers" is the three-tier rule (issue #97); "legacy" is
    measurement-only mode: today's pass/fail decides, and every new measurement rides along in `analysis`
    (with the tier it would have given) without changing anything. `spectrogram_dir` None skips the
    picture, for a read-only report."""
    if rule not in RULES:
        raise ValueError(f"unknown verification rule {rule!r}")
    if path.suffix.lower() not in DEMUXERS:
        # Nothing to check the bytes against, so no tool reads them with its own probing: only the formats
        # verify can pass have an extension listed there.
        fmt = path.suffix.lstrip(".").lower() or "unknown"
        return Verdict(False, fmt, 0, 0, f"unsupported format {fmt}", None, tier="reject")
    pr = probe(path)
    if pr.fmt != "mp3" and pr.fmt not in LOSSLESS:
        return Verdict(False, pr.fmt, pr.bitrate_kbps, 0, f"unsupported format {pr.fmt}", None,
                       bit_depth=pr.bit_depth, sample_rate=pr.sample_rate, tier="reject")
    png = (spectrogram_png(path, spectrogram_dir / f"{name or path.stem}.png", duration_s=pr.duration_s)
           if spectrogram_dir is not None else None)
    if pr.fmt == "mp3" and pr.bitrate_kbps < MIN_MP3_BITRATE:
        return Verdict(False, pr.fmt, pr.bitrate_kbps, 0, f"bitrate {pr.bitrate_kbps} kbps below {MIN_MP3_BITRATE}",
                       png, bit_depth=pr.bit_depth, sample_rate=pr.sample_rate, tier="reject")
    try:
        legacy_cutoff = spectral_cutoff_hz(path, duration_s=pr.duration_s)
        old_passed, old_reason = legacy_verdict(pr, legacy_cutoff)
        legacy = {"passed": old_passed, "cutoff_hz": legacy_cutoff}
        try:
            analysis = analyze(path, pr)
            tier, reason, cutoff = decide(analysis, pr.fmt)
        except ToolMissing:
            raise
        except VerifyError as e:
            if rule != "legacy":
                raise
            # Measurement only changes nothing: a file the new decode cannot read through still gets
            # today's verdict, with the reason the measurements are missing
            return Verdict(old_passed, pr.fmt, pr.bitrate_kbps, legacy_cutoff, old_reason, png,
                           bit_depth=pr.bit_depth, sample_rate=pr.sample_rate,
                           analysis={"version": DETECTOR_VERSION, "error": str(e)[:300], "legacy": legacy})
        except Exception as e:  # a bug in the detectors is no verdict on the file
            # The bytes decoded (or the error would be a VerifyError): it is the check that broke. Refusing
            # the file, or failing the request and deleting it, would punish the copy for our bug. So the
            # copy is filed, as unverified, and the crash is logged for a report.
            log.exception("the evidence check crashed on %s", path.name)
            crash = {"version": DETECTOR_VERSION, "error": f"{type(e).__name__}: {e}"[:300], "legacy": legacy}
            if rule == "legacy":
                return Verdict(old_passed, pr.fmt, pr.bitrate_kbps, legacy_cutoff, old_reason, png,
                               bit_depth=pr.bit_depth, sample_rate=pr.sample_rate, analysis=crash)
            if not old_passed:
                # The old check still had its say and refused it: that stands, as it did before
                return Verdict(False, pr.fmt, pr.bitrate_kbps, legacy_cutoff, old_reason, png,
                               bit_depth=pr.bit_depth, sample_rate=pr.sample_rate, tier="reject",
                               analysis={**crash, "tier": "reject"})
            head = f"{pr.bitrate_kbps} kbps" if pr.fmt == "mp3" else "lossless"
            return Verdict(True, pr.fmt, pr.bitrate_kbps, legacy_cutoff,
                           f"{head}, but the full check could not finish ({type(e).__name__})", png,
                           bit_depth=pr.bit_depth, sample_rate=pr.sample_rate, tier="unverified",
                           analysis={**crash, "tier": "unverified"})
        analysis["legacy"] = legacy
        if rule == "legacy":
            # Measurement only: today's verdict and numbers decide. The new rule's answer is kept beside it
            # under its own name, so nothing that reads `tier` mistakes it for the verdict.
            analysis.update(tier="clear" if old_passed else "reject", shadow_tier=tier, shadow_reason=reason)
            return Verdict(old_passed, pr.fmt, pr.bitrate_kbps, legacy_cutoff, old_reason, png,
                           bit_depth=pr.bit_depth, sample_rate=pr.sample_rate, analysis=analysis)
        analysis.update(tier=tier, reason=reason)
        failed = analysis["integrity"]["failed"]
        if failed:
            # The copy is damaged, which says nothing about where the audio came from (see VerifyError)
            raise VerifyError(f"damaged: {failed}")
    except VerifyError:
        if png is not None:
            png.unlink(missing_ok=True)      # no verdict comes back, so nothing would ever delete it
        raise
    return Verdict(tier != "reject", pr.fmt, pr.bitrate_kbps, cutoff, reason, png, bit_depth=pr.bit_depth,
                   sample_rate=pr.sample_rate, tier=tier, analysis=analysis)


def summary(analysis: dict | None) -> dict | None:
    """The handful of numbers a verdict rests on, small enough to keep on every rejection and track: the
    tier and each family's finding. The full analysis (per channel, per detector) is for reports."""
    if not analysis or "spectral" not in analysis:
        return None
    spec = analysis["spectral"]
    q_kind, codec, z = quant_finding(analysis.get("quant"))
    edge_kind, _ = edge_finding(spec, analysis["probe"]["fmt"])
    cont = analysis.get("container") or {}
    return {
        "version": analysis["version"], "tier": analysis.get("tier"),
        **({"shadow_tier": analysis["shadow_tier"]} if "shadow_tier" in analysis else {}),
        "edge": {"finding": edge_kind, "edge_hz": spec.get("edge_hz"), "drop_db": spec.get("edge_drop_db"),
                 "frames_above_edge": spec.get("frames_above_edge")},
        "quant": {"finding": q_kind, "codec": codec, "z": round(z, 1)},
        "integrity": {"checked": analysis["integrity"]["checked"], "failed": analysis["integrity"]["failed"]},
        "container": {k: cont[k] for k in ("max_blocksize", "vendor") if k in cont},
        "legacy": analysis.get("legacy"),
    }
