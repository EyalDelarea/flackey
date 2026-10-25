"""Run the quality check over a folder and compare the old rule with the new one (issue #97).

Read-only by construction: every file is opened by ffprobe, ffmpeg and mutagen for reading, no spectrogram
is drawn, and nothing is written but the two report files the caller names. Meant for a library the owner
already trusts, where every disagreement between the rules is worth a listen.

    python -m flackey.verify_report <folder> <out.jsonl> [--jobs N]

writes one JSON line per file and prints a summary table: the old verdict against the new tier, and each
disagreement with its reason. The lines carry the file's path, so the output belongs outside the repo.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from .audiofile import DEMUXERS
from .verify import VerifyError, summary, verify


def audio_files(folder: Path) -> list[Path]:
    return sorted(p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() in DEMUXERS
                  and not p.name.startswith("._"))


def check(path: Path) -> dict:
    """One file's row: the old verdict, the new tier and the numbers behind it, or the error."""
    t0 = time.monotonic()
    row: dict = {"path": str(path)}
    try:
        v = verify(path, None, rule="tiers")
    except VerifyError as e:
        row.update(new="damaged" if str(e).startswith("damaged") else "error", error=str(e)[:300])
    else:
        a = v.analysis or {}
        legacy = a.get("legacy") or {}
        row.update(fmt=v.fmt, bits=v.bit_depth, sample_rate=v.sample_rate, new=v.level, reason=v.reason,
                   old="clear" if legacy.get("passed", v.passed) else "reject", old_cutoff_hz=legacy.get("cutoff_hz"),
                   evidence=summary(a))
    row["seconds"] = round(time.monotonic() - t0, 1)
    return row


def table(rows: list[dict]) -> str:
    """The summary, aggregate only: counts by format, the old verdict against the new tier, and every
    disagreement by number rather than by name."""
    out = [f"files: {len(rows)}", "by format: " + ", ".join(f"{k} {n}" for k, n in
                                                         sorted(Counter(r.get("fmt", "?") for r in rows).items()))]
    out.append("old \\ new: " + ", ".join(f"{o}->{n}: {c}" for (o, n), c in
                                          sorted(Counter((r.get("old", "-"), r["new"]) for r in rows).items())))
    out.append("new tiers: " + ", ".join(f"{k} {n}" for k, n in sorted(Counter(r["new"] for r in rows).items())))
    for i, r in enumerate(rows, 1):
        if r.get("old") != r["new"]:
            ev = r.get("evidence") or {}
            q = ev.get("quant") or {}
            e = ev.get("edge") or {}
            out.append(f"  track {i}: {r.get('fmt', '?')} old={r.get('old', '-')} new={r['new']} "
                       f"edge={e.get('edge_hz')} ({e.get('finding')}) quant={q.get('codec')} z={q.get('z')} "
                       f"| {r.get('reason') or r.get('error')}")
    secs = [r["seconds"] for r in rows]
    if secs:
        out.append(f"seconds per file: median {sorted(secs)[len(secs) // 2]}, max {max(secs)}")
    return "\n".join(out)


def run(folder: Path, out: Path, jobs: int = 2) -> list[dict]:
    files = audio_files(folder)
    rows: list[dict] = []
    with out.open("w") as f, ProcessPoolExecutor(max_workers=jobs) as pool:
        futures = {pool.submit(check, p): p for p in files}
        for n, fut in enumerate(as_completed(futures), 1):
            row = fut.result()
            rows.append(row)
            f.write(json.dumps(row) + "\n")
            f.flush()
            print(f"[{n}/{len(files)}] {row['new']}", file=sys.stderr, flush=True)
    rows.sort(key=lambda r: r["path"])
    return rows


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m flackey.verify_report", description=__doc__.splitlines()[0])
    ap.add_argument("folder", type=Path)
    ap.add_argument("out", type=Path, help="JSONL, one line per file (contains paths: keep it out of the repo)")
    ap.add_argument("--jobs", type=int, default=2)
    args = ap.parse_args(argv)
    rows = run(args.folder, args.out, args.jobs)
    print(table(rows))


if __name__ == "__main__":
    main()
