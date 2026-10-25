"""Labelled corpus for the lossless check (issue #97): transcodes and genuine controls made from masters.

    PYTHONPATH=src python docs/research/issue97_corpus.py <masters folder> <scratch folder> [--n 16] [--jobs 3]

Takes up to `--n` lossless masters (a seeded sample), cuts a 90 s excerpt of each into the scratch folder,
and makes from it every recipe below. Each output is written as FLAC through ffmpeg with `-frame_size 4096`
(its default block size on a decoded lossy file is tiny and gives the answer away) and the block size is
asserted. Every file then goes through `verify.analyze` once and `verify.decide`, and the script prints the
detection rate per transcode recipe and the false-positive rate per genuine control.

Only aggregates leave this script: it prints no file names, and the scratch folder is the caller's to
delete. A control is only counted as genuine when its master's own excerpt comes out clear; the masters
are real downloads, so one of them may itself be a transcode, and it is reported as excluded instead.
"""
from __future__ import annotations

import argparse
import json
import random
import subprocess
import sys
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from mutagen.flac import FLAC

from flackey.verify import VerifyError, analyze, decide, probe

EXCERPT_S = 90
# name -> (encoder args, extension); decoded back to 44.1 kHz and written as FLAC
TRANSCODES = {
    "mp3_128": (["-c:a", "libmp3lame", "-b:a", "128k"], "mp3"),
    "mp3_192": (["-c:a", "libmp3lame", "-b:a", "192k"], "mp3"),
    "mp3_256": (["-c:a", "libmp3lame", "-b:a", "256k"], "mp3"),
    "mp3_320": (["-c:a", "libmp3lame", "-b:a", "320k"], "mp3"),
    "mp3_v0": (["-c:a", "libmp3lame", "-q:a", "0"], "mp3"),
    "aac_128": (["-c:a", "aac", "-b:a", "128k"], "m4a"),
    "aac_256": (["-c:a", "aac", "-b:a", "256k"], "m4a"),
    "aac_at_256": (["-c:a", "aac_at", "-b:a", "256k"], "m4a"),
    "opus_128": (["-c:a", "libopus", "-b:a", "128k"], "opus"),
    "opus_256": (["-c:a", "libopus", "-b:a", "256k"], "opus"),
}
# name -> filter chain (and sample format) applied to the master; all genuine, none lossy
CONTROLS = {
    "native": ([], "s16"),
    "native_24bit": ([], "s32"),
    "brickwall_20k": (["aresample=40000", "aresample=44100"], "s16"),
    "brickwall_20_5k": (["aresample=41000", "aresample=44100"], "s16"),
    "resample_48k_091": (["aresample=48000", "aresample=44100:cutoff=0.91"], "s16"),
    # Steep: a long filter just under the new rate's Nyquist, the sharpest edge a genuine chain draws
    "steep_19_8k": (["aresample=40000:filter_size=256:cutoff=0.99", "aresample=44100:filter_size=256"], "s16"),
    "steep_20_3k": (["aresample=41000:filter_size=256:cutoff=0.99", "aresample=44100:filter_size=256"], "s16"),
    "limited_loud": (["volume=8dB", "alimiter=limit=0.89:level=false"], "s16"),
    "clipped": (["volume=10dB"], "s16"),
    "quieter": (["volume=-12dB"], "s16"),
}


def ff(*args: str) -> None:
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)


def to_flac(src: Path, out: Path, filters: list[str] | None = None, sample_fmt: str = "s16") -> Path:
    af = ["-af", ",".join(filters)] if filters else []
    ff("-i", str(src), *af, "-ar", "44100", "-c:a", "flac", "-frame_size", "4096", "-sample_fmt", sample_fmt,
       *(["-bits_per_raw_sample", "24"] if sample_fmt == "s32" else []), str(out))
    bs = FLAC(out).info.max_blocksize
    assert bs == 4096, f"block size {bs}"
    return out


def make(master: Path, work: Path, i: int) -> list[tuple[str, str, Path]]:
    """(label, recipe, file) for one master."""
    d = work / f"m{i:02d}"
    d.mkdir(parents=True, exist_ok=True)
    dur = probe(master).duration_s
    start = max(0.0, dur / 2 - EXCERPT_S / 2)
    wav = d / "excerpt.wav"
    ff("-ss", f"{start:.1f}", "-t", str(EXCERPT_S), "-i", str(master), "-c:a", "pcm_s24le", "-ar", "44100", str(wav))
    out: list[tuple[str, str, Path]] = []
    for name, (filters, fmt) in CONTROLS.items():
        out.append(("genuine", name, to_flac(wav, d / f"{name}.flac", filters, fmt)))
    for name, (enc, ext) in TRANSCODES.items():
        lossy = d / f"{name}.{ext}"
        ff("-i", str(wav), "-ar", "48000" if ext == "opus" else "44100", *enc, str(lossy))
        out.append(("transcode", name, to_flac(lossy, d / f"{name}.flac")))
        lossy.unlink()
    wav.unlink()
    return out


def judge(item: tuple[int, str, str, Path]) -> dict:
    i, label, recipe, path = item
    try:
        a = analyze(path)
        tier, reason, _ = decide(a, "flac")
        q = a.get("quant") or {}
        return {"master": i, "label": label, "recipe": recipe, "tier": tier, "reason": reason,
                "judged": a["spectral"].get("judged"),
                "edge_hz": a["spectral"].get("edge_hz"), "reach": a["spectral"].get("frames_above_edge"),
                "aac_z": (q.get("aac") or {}).get("z"), "mp3_z": (q.get("mp3") or {}).get("z")}
    except VerifyError as e:
        return {"master": i, "label": label, "recipe": recipe, "tier": "error", "reason": str(e)[:200]}


def redecide(r: dict) -> dict:
    """The rule as it stands now, from a row's stored measurements: thresholds can move without
    re-running the decode."""
    if r["tier"] == "error":
        return r
    a = {"probe": {"fmt": "flac", "bitrate_kbps": 0},
         "spectral": {"judged": r.get("judged", 2), "edge_hz": r["edge_hz"], "frames_above_edge": r["reach"]},
         "quant": {"aac": {"codec": "aac", "z": r["aac_z"]} if r["aac_z"] is not None else None,
                   "mp3": {"codec": "mp3", "z": r["mp3_z"]} if r["mp3_z"] is not None else None}}
    tier, reason, _ = decide(a, "flac")
    return {**r, "tier": tier, "reason": reason}


def report(rows: list[dict]) -> str:
    clean_masters = {r["master"] for r in rows if r["recipe"] == "native" and r["tier"] == "clear"}
    masters = {r["master"] for r in rows}
    lines = [f"masters: {len(masters)}, natively clear (genuine controls counted): {len(clean_masters)}",
             "", "| recipe | n | reject | unverified | clear |", "|---|---|---|---|---|"]
    by: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        if r["label"] == "genuine" and r["master"] not in clean_masters and r["recipe"] != "native":
            continue
        by[r["recipe"]][r["tier"]] += 1
    for recipe in [*TRANSCODES, *CONTROLS]:
        c = by[recipe]
        n = sum(c.values())
        if n:
            lines.append(f"| {recipe} | {n} | {c['reject'] / n:.0%} | {c['unverified'] / n:.0%} | "
                         f"{c['clear'] / n:.0%} |" + (f" errors {c['error']}" if c["error"] else ""))
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("masters", type=Path)
    ap.add_argument("work", type=Path)
    ap.add_argument("--n", type=int, default=16)
    ap.add_argument("--jobs", type=int, default=3)
    ap.add_argument("--seed", type=int, default=97)
    ap.add_argument("--redecide", action="store_true", help="re-apply the rule to <work>/rows.jsonl only")
    args = ap.parse_args()
    if args.redecide:
        rows = [redecide(json.loads(line)) for line in (args.work / "rows.jsonl").read_text().splitlines()]
        print(report(rows))
        return
    pool_of = sorted(p for p in args.masters.rglob("*") if p.suffix.lower() in (".aiff", ".aif", ".flac", ".wav")
                     and not p.name.startswith("._"))
    chosen = random.Random(args.seed).sample(pool_of, min(args.n, len(pool_of)))
    with ProcessPoolExecutor(args.jobs) as pool:
        made = list(pool.map(make, chosen, [args.work] * len(chosen), range(len(chosen))))
        items = [(i, label, recipe, p) for i, files in enumerate(made) for label, recipe, p in files]
        rows = list(pool.map(judge, items))
    (args.work / "rows.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    print(report(rows))
    print(f"rows: {args.work / 'rows.jsonl'}", file=sys.stderr)


if __name__ == "__main__":
    main()
