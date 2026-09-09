"""Throwaway spike: enqueue the best lossless result for a handful of tracks, time the transfers,
then run flackey's verify() on the files. Reads probe_search_run1.json, writes probe_download.md."""
from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

import httpx
from probe_search import classify, queries, run_search

from flackey.verify import VerifyError, verify

HERE = Path(__file__).parent
APP = Path.home() / "Library/Application Support/Flackey/slskd"
KEY = (APP / "api_key").read_text().strip()
BASE = "http://127.0.0.1:5030/api/v0"
H = {"X-API-Key": KEY}
PICK_IDS = [6, 8, 10, 16, 26, 14]  # five free-slot q0 peers + one queued peer (Goasia, q69)
TOTAL_CAP_S = 600
SPEC_DIR = HERE / "spectrograms"


def find_transfer(username: str, filename: str) -> dict | None:
    r = httpx.get(f"{BASE}/transfers/downloads/{username}", headers=H, timeout=30)
    if r.status_code != 200:
        return None
    for d in r.json().get("directories", []):
        for f in d.get("files", []):
            if f["filename"] == filename:
                return f
    return None


def main() -> None:
    tracks = [t for t in json.loads((HERE / "tracks.json").read_text()) if t["id"] in PICK_IDS]
    picks = []
    for t in tracks:
        for q in queries(t):
            state, responses, _ = run_search(q)
            c = classify(t, responses)
            if c["best_lossless"]:
                picks.append((t, c["best_lossless"]))
                print(f"pick {t['artist']} - {t['title']}: {c['best_lossless']['user']} q{c['best_lossless']['queue']} {c['best_lossless']['name'][-60:]}", flush=True)
                break
    started = {}
    for t, b in picks:
        r = httpx.post(f"{BASE}/transfers/downloads/{b['user']}", headers=H,
                       json=[{"filename": b["name"].replace("/", "\\"), "size": b["size"]}], timeout=30)
        print(f"enqueue {t['artist']} - {t['title']} from {b['user']}: HTTP {r.status_code} {r.text[:200]}", flush=True)
        started[t["id"]] = time.monotonic()
    # slskd needs the exact size; re-read it from the search result if the rounded one was rejected
    results = {}
    t0 = time.monotonic()
    pending = {t["id"]: (t, b) for t, b in picks}
    log = {tid: [] for tid in pending}
    while pending and time.monotonic() - t0 < TOTAL_CAP_S:
        time.sleep(2)
        for tid, (t, b) in list(pending.items()):
            tr = find_transfer(b["user"], b["name"].replace("/", "\\"))
            if tr is None:
                continue
            state = tr["state"]
            pct = round(100 * tr.get("bytesTransferred", 0) / max(tr.get("size", 1), 1))
            elapsed = round(time.monotonic() - started[tid], 1)
            if not log[tid] or log[tid][-1][1] != state:
                log[tid].append((elapsed, state, pct))
                print(f"  {elapsed:6.1f}s {t['title'][:30]:30} {state:28} {pct:3d}% speed={tr.get('averageSpeed', 0) / 1000:.0f} kB/s", flush=True)
            if state.startswith("Completed"):
                results[tid] = {"state": state, "elapsed": elapsed, "speed_kbs": tr.get("averageSpeed", 0) / 1000,
                                "size": tr.get("size"), "log": log[tid]}
                del pending[tid]
    for tid, (t, b) in pending.items():
        results[tid] = {"state": "still " + (log[tid][-1][1] if log[tid] else "unknown"), "elapsed": TOTAL_CAP_S, "log": log[tid]}

    SPEC_DIR.mkdir(exist_ok=True)
    lines = ["| # | Track | Peer | Queue | Transfer states (s) | Done in | Speed | verify | fmt/bitrate/cutoff | reason |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for t, b in picks:
        res = results[t["id"]]
        base = b["name"].replace("\\", "/").rsplit("/", 1)[-1]
        found = list((APP / "downloads").rglob(base))
        v = "-"; fmt = "-"; reason = "not downloaded"
        if found and res["state"].startswith("Completed, Succeeded"):
            try:
                verdict = verify(found[0], SPEC_DIR, f"spike-{t['id']}")
                v = "PASS" if verdict.passed else "REJECT"
                fmt = f"{verdict.fmt}/{verdict.bitrate_kbps}/{verdict.cutoff_hz}"
                reason = verdict.reason or ""
            except VerifyError as e:
                v, reason = "ERROR", str(e)[:80]
        states = " -> ".join(f"{s}@{e}" for e, s, _ in res["log"])
        lines.append(f"| {t['id']} | {t['artist']} - {t['title']} | {b['user']} | q{b['queue']} | {states} | {res['elapsed']} s | "
                     f"{res.get('speed_kbs', 0):.0f} kB/s | {v} | {fmt} | {reason} |")
    (HERE / "probe_download.md").write_text("\n".join(lines) + "\n")
    (HERE / "probe_download.json").write_text(json.dumps({str(k): v for k, v in results.items()}, indent=1))
    print("\n".join(lines))


if __name__ == "__main__":
    sys.exit(main())
