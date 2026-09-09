from __future__ import annotations

from statistics import median

from fastapi import APIRouter, HTTPException

from ..models import ATTEMPT_OUTCOMES, MISS_REASON
from ..source.lossless import LosslessError
from ..store import Store


def _row(a) -> dict:
    chosen = (a.report or {}).get("chosen") or {}
    return {"id": a.id, "request_id": a.request_id, "provider": a.provider, "created_at": a.created_at, "query": a.query,
            "outcome": a.outcome, "peer": chosen.get("username"),
            "file": chosen["path"].replace("\\", "/").rsplit("/", 1)[-1] if chosen.get("path") else None,
            "first_byte_ms": a.first_byte_ms, "total_ms": a.total_ms,
            "score": (a.fingerprint or {}).get("score"), "summary": (a.report or {}).get("summary")}


def _summary(rows: list[dict]) -> dict:
    done = [u for u in rows if "Completed" in u["state"] and "Succeeded" in u["state"]]
    return {"total": len(rows), "active": len([u for u in rows if "Completed" not in u["state"]]),
            "completed": len(done), "peers": len({u["peer"] for u in rows}),
            "bytes": sum(u["bytes"] for u in rows)}


def router(store: Store, worker=None) -> APIRouter:
    r = APIRouter(prefix="/api/lossless")

    @r.get("/attempts")
    async def attempts(limit: int = 50, outcome: str | None = None) -> dict:
        if outcome is not None and outcome not in ATTEMPT_OUTCOMES:
            raise HTTPException(400, f"unknown outcome; one of {', '.join(ATTEMPT_OUTCOMES)}")
        rows = store.list_attempts(limit=limit, outcome=outcome)
        counts: dict[str, int] = {}
        for a in rows:
            counts[a.outcome or "open"] = counts.get(a.outcome or "open", 0) + 1
        first = [a.first_byte_ms for a in rows if a.first_byte_ms is not None]
        total = [a.total_ms for a in rows if a.total_ms is not None and a.outcome == "filed"]
        return {"attempts": [_row(a) for a in rows], "counts": counts,
                "median_first_byte_ms": median(first) if first else None,
                "median_total_ms": median(total) if total else None}

    @r.get("/uploads")
    async def uploads() -> dict:
        """Who is pulling from the shared library. Peer names and file names are peer- or disk-chosen strings:
        they travel as text for display and are never used to build a path or a URL here."""
        providers = [p for p in getattr(worker, "providers", []) if hasattr(p, "uploads")]
        if not providers:
            return {"enabled": False, "provider": None, "uploads": [], "summary": _summary([]), "error": None}
        p = providers[0]
        try:
            rows = await p.uploads()
        except LosslessError as e:
            return {"enabled": True, "provider": p.name, "uploads": [], "summary": _summary([]), "error": str(e)}
        return {"enabled": True, "provider": p.name, "uploads": rows, "summary": _summary(rows), "error": None}

    @r.get("/upgradable")
    async def upgradable(limit: int = 200) -> dict:
        """Every filed track still on the lossy copy, with why its lossless attempt missed. The monitor half
        of "handle L.S.D-like events": a miss is a row here rather than a thing that quietly happened once."""
        out = []
        for t in store.lossy_tracks(limit=limit):
            attempt = store.get_attempt_for_request(t.request_id) if t.request_id is not None else None
            outcome = attempt.outcome if attempt else None
            out.append({
                "track_id": t.id, "artist": t.artist, "title": t.title, "mix_name": t.mix_name,
                "fmt": t.fmt, "bitrate_kbps": t.bitrate_kbps, "added_at": t.added_at,
                "outcome": outcome,
                "reason": MISS_REASON.get(outcome) if outcome else "no lossless search ran for this one",
                # A track can only be retried if there is still a request and a recorded choice to search from
                "upgradable": t.request_id is not None and attempt is not None,
            })
        return {"tracks": out, "total": len(out)}

    @r.post("/upgrade/{track_id}")
    async def upgrade(track_id: int) -> dict:
        """Search again for one track and swap the file if a lossless copy turns up. Deliberately manual:
        re-pathing a filed track breaks any Rekordbox entry pointing at the old name."""
        if worker is None:
            raise HTTPException(503, "the worker is not running")
        try:
            store.get_track(track_id)
        except KeyError:
            raise HTTPException(404, f"no track {track_id}") from None
        try:
            message = await worker.upgrade(track_id)
        except ValueError as e:
            raise HTTPException(409, str(e)) from None
        t = store.get_track(track_id)
        return {"ok": True, "message": message, "upgraded": t.source_fmt is not None}

    return r
