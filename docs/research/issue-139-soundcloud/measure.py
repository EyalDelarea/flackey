"""Spike measurement for #139: how many SoundCloud tracks get a Deezer fingerprint reference."""
import asyncio
import json
import re
import sys
from collections import Counter

import httpx

from flackey.identify import interpretations
from flackey.reference import find_deezer_record
from flackey.models import Candidate
from flackey.worker.records import query_candidate

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/130 Safari/537.36"}
SOURCES = {
    "label (UKF uploads)": ("user_tracks", 415653, 20),
    "free-DL search": ("search", "free download tech house", 15),
    "bootleg search": ("search", "bootleg edit", 15),
    "playlist melodic-house-techno": ("playlist_url", "https://soundcloud.com/franc_marti/sets/melodic-house-techno", 20),
    "playlist tech-house-2025": ("playlist_url", "https://soundcloud.com/housemusic-district/sets/tech-house-2025", 25),
    "mix search": ("search", "boiler room mix", 6),
}


async def main():
    out = []
    async with httpx.AsyncClient(timeout=30, follow_redirects=True, headers=UA) as http:
        page = (await http.get("https://soundcloud.com/discover")).text
        cid = re.search(r'"hydratable":"apiClient","data":\{"id":"([^"]+)"', page).group(1)
        api = "https://api-v2.soundcloud.com"
        for label, (how, arg, n) in SOURCES.items():
            if how == "user_tracks":
                tracks = (await http.get(f"{api}/users/{arg}/tracks", params={"client_id": cid, "limit": n})).json()["collection"]
            elif how == "search":
                tracks = (await http.get(f"{api}/search/tracks", params={"client_id": cid, "q": arg, "limit": n})).json()["collection"]
            else:
                pl = (await http.get(f"{api}/resolve", params={"client_id": cid, "url": arg})).json()
                ids = [t["id"] for t in pl["tracks"]][:n]
                tracks = (await http.get(f"{api}/tracks", params={"client_id": cid, "ids": ",".join(map(str, ids))})).json()
            for t in tracks[:n]:
                pm = t.get("publisher_metadata") or {}
                dur = round((t.get("full_duration") or t.get("duration") or 0) / 1000)
                row = {"src": label, "url": t["permalink_url"], "title": t["title"], "uploader": t["user"]["username"],
                       "pub_artist": pm.get("artist"), "isrc": pm.get("isrc"), "dur": dur, "policy": t.get("policy")}
                if dur > 30 * 60:
                    row["outcome"] = "skipped: mix"
                    out.append(row)
                    continue
                q = interpretations(t["title"], t["user"]["username"], dur)[0]
                row["query"] = f"{q.artist} | {q.title} | {q.version}"
                cand = query_candidate(q, 0)
                if pm.get("isrc"):
                    isrc_cand = Candidate(**{**cand.__dict__, "isrc": pm["isrc"]})
                    did, why = await find_deezer_record(isrc_cand, http)
                    row["isrc_ref"] = bool(did and "isrc" in why)
                did, why = await find_deezer_record(cand, http)
                row["text_ref"] = bool(did)
                row["why"] = why
                out.append(row)
    json.dump(out, open(sys.argv[1], "w"), indent=1)
    by = Counter()
    for r in out:
        s = r["src"]
        by[s, "n"] += 1
        if "outcome" in r:
            by[s, "mix"] += 1
            continue
        by[s, "isrc"] += bool(r["isrc"])
        by[s, "isrc_ref"] += bool(r.get("isrc_ref"))
        by[s, "text_ref"] += r["text_ref"]
        by[s, "any_ref"] += bool(r.get("isrc_ref") or r["text_ref"])
    for s in SOURCES:
        print(f"{s}: n={by[s,'n']} mix={by[s,'mix']} has_isrc={by[s,'isrc']} isrc->deezer={by[s,'isrc_ref']} "
              f"text->deezer={by[s,'text_ref']} any_ref={by[s,'any_ref']}")


asyncio.run(main())
