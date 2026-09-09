"""Throwaway spike: search slskd for every track already in the krater library and measure
responsiveness and lossless availability. Writes probe_search.json (raw) and probe_search.md (table)."""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

import httpx
from rapidfuzz import fuzz

HERE = Path(__file__).parent
APP = Path.home() / "Library/Application Support/Krater/slskd"
KEY = (APP / "api_key").read_text().strip()
BASE = "http://127.0.0.1:5030/api/v0"
H = {"X-API-Key": KEY}
LOSSLESS = {"flac", "wav", "aiff", "aif"}
MAX_WAIT_S = 60.0
LENGTH_TOL_S = 3


def norm(s: str) -> str:
    s = re.sub(r"(?<=\w)\.(?=\w)", "", (s or "").lower())
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", s).split())


def first_artist(a: str) -> str:
    a = re.sub(r"\(.*?\)", "", a)
    return a.split(",")[0].strip()


def queries(t: dict) -> list[str]:
    artist, title, mix = first_artist(t["artist"]), t["title"], t["mix_name"] or ""
    base_title = re.sub(r"\(.*?\)", "", title).strip()
    qs = []
    if not re.search(r"\boriginal\b", mix, re.I):
        qs.append(f"{artist} {base_title} {mix}")
    qs.append(f"{artist} {base_title}")
    qs.append(base_title)
    return [" ".join(q.split()) for q in qs]


def run_search(text: str) -> tuple[dict, list[dict], list[tuple[float, int]]]:
    t0 = time.monotonic()
    for _ in range(20):
        r = httpx.post(f"{BASE}/searches", headers=H, json={"searchText": text, "searchTimeout": 15000}, timeout=30)
        if r.status_code != 429:
            break
        time.sleep(1)
    r.raise_for_status()
    sid = r.json()["id"]
    timeline: list[tuple[float, int]] = []
    state = {}
    while time.monotonic() - t0 < MAX_WAIT_S:
        time.sleep(0.5)
        state = httpx.get(f"{BASE}/searches/{sid}", headers=H, timeout=30).json()
        timeline.append((round(time.monotonic() - t0, 1), state["responseCount"]))
        if "Completed" in state["state"]:
            break
    responses = httpx.get(f"{BASE}/searches/{sid}/responses", headers=H, timeout=30).json()
    httpx.delete(f"{BASE}/searches/{sid}", headers=H, timeout=30)
    return state, responses, timeline


def classify(t: dict, responses: list[dict]) -> dict:
    want = norm(f"{first_artist(t['artist'])} {t['title']}")
    files = []
    for r in responses:
        for f in r["files"]:
            name = f["filename"].replace("\\", "/")
            base = name.rsplit("/", 1)[-1]
            ext = (f.get("extension") or base.rsplit(".", 1)[-1]).lower()
            length = f.get("length")
            dur_ok = length is not None and t["duration_s"] is not None and abs(length - t["duration_s"]) <= LENGTH_TOL_S
            files.append({
                "user": r["username"], "slot": r["hasFreeUploadSlot"], "speed_kbs": r["uploadSpeed"] // 1000,
                "queue": r["queueLength"], "ext": ext, "bitrate": f.get("bitRate"), "sr": f.get("sampleRate"),
                "bits": f.get("bitDepth"), "length": length, "size_mb": round(f["size"] / 1e6, 1),
                "size": f["size"], "dur_ok": dur_ok, "match": fuzz.token_set_ratio(want, norm(base)), "name": name,
            })
    lossless = [f for f in files if f["ext"] in LOSSLESS]
    good_lossless = [f for f in lossless if f["dur_ok"] and f["match"] >= 80]
    good_mp3_320 = [f for f in files if f["ext"] == "mp3" and f["dur_ok"] and f["match"] >= 80 and (f["bitrate"] or 0) >= 320]
    good_lossless.sort(key=lambda f: (not f["slot"], f["queue"], -f["speed_kbs"]))
    return {"files": files, "n_files": len(files), "n_lossless": len(lossless), "n_good_lossless": len(good_lossless),
            "n_good_mp3_320": len(good_mp3_320), "best_lossless": good_lossless[0] if good_lossless else None,
            "n_no_length": sum(1 for f in files if f["length"] is None)}


def main() -> None:
    tracks = json.loads((HERE / "tracks.json").read_text())
    out = []
    for t in tracks:
        row = {"track": t, "attempts": []}
        for q in queries(t):
            state, responses, timeline = run_search(q)
            c = classify(t, responses)
            first = next((s for s, n in timeline if n > 0), None)
            row["attempts"].append({"query": q, "state": state["state"], "responses": state["responseCount"],
                                    "first_response_s": first, "timeline": timeline, **c})
            print(f"{t['artist']} - {t['title']!r} q={q!r}: {state['responseCount']} peers, {c['n_files']} files, "
                  f"{c['n_lossless']} lossless, {c['n_good_lossless']} good lossless, {c['n_good_mp3_320']} good mp3-320, "
                  f"first {first}s", flush=True)
            if c["n_good_lossless"] or c["n_good_mp3_320"]:
                break
        out.append(row)
    (HERE / "probe_search.json").write_text(json.dumps(out, indent=1))

    lines = ["| # | Track | Query used | First resp | Peers | Files | Lossless | Good lossless | Good MP3-320 | Best lossless (user, slot, queue, speed, format) |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for row in out:
        t, a = row["track"], row["attempts"][-1]
        b = a["best_lossless"]
        best = (f"{b['user']}, {'slot' if b['slot'] else 'no slot'}, q{b['queue']}, {b['speed_kbs']} kB/s, "
                f"{b['ext']} {b['sr']}/{b['bits']}") if b else "-"
        lines.append(f"| {t['id']} | {t['artist']} - {t['title']} ({t['mix_name']}) | {a['query']} | {a['first_response_s']} s | "
                     f"{a['responses']} | {a['n_files']} | {a['n_lossless']} | {a['n_good_lossless']} | {a['n_good_mp3_320']} | {best} |")
    (HERE / "probe_search.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))


if __name__ == "__main__":
    sys.exit(main())
