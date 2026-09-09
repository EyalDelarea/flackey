from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException

from ..config import Settings
from ..inbox import BadLink, Inbox
from ..models import TERMINAL_STATES, RequestState
from ..store import Store
from ..worker import Worker
from . import Bundles, to_dict

RECENT = 500
FAILED_STATES = {RequestState.REJECTED, RequestState.NOT_FOUND, RequestState.ERROR, RequestState.CANCELLED}


def router(store: Store, worker: Worker, inbox: Inbox, bundles: Bundles, settings: Settings) -> APIRouter:
    r = APIRouter(prefix="/api")

    def bundle_or_404(rid: int) -> dict:
        try:
            return bundles.request(rid)
        except KeyError:
            raise HTTPException(404, "request not found")

    def unlink_spectrogram(rejection) -> None:
        """Best-effort: a removed row shouldn't leave an orphan PNG behind. Only ever touches a path under
        settings.spectrogram_dir - never anything else a stray/legacy path might point at."""
        if rejection and rejection.spectrogram_path:
            png = Path(rejection.spectrogram_path)
            if png.is_relative_to(settings.spectrogram_dir):
                try:
                    png.unlink()
                except OSError:
                    pass

    @r.get("/queue")
    async def queue() -> list:
        open_states = {s for s in RequestState if s not in TERMINAL_STATES}
        seen: dict[int, None] = {}
        for req in store.list_requests(open_states, limit=100_000) + store.list_recent_requests(limit=RECENT):
            seen.setdefault(req.id, None)
        return [bundles.request(rid) for rid in sorted(seen, reverse=True)]

    @r.get("/requests/{rid}")
    async def request_detail(rid: int) -> dict:
        return bundle_or_404(rid)

    @r.post("/requests")
    async def submit(body: dict) -> dict:
        try:
            sub = await inbox.submit((body.get("url") or "").strip())
        except BadLink as e:
            raise HTTPException(400, str(e))
        return {"summary": sub.summary(), "request_ids": sub.request_ids, "playlist_id": sub.playlist_id,
                "name": sub.name, "total": sub.total, "already_in_library": sub.already_in_library,
                "already_queued": sub.already_queued}

    async def act(rid: int, fn):
        bundle_or_404(rid)
        try:
            return to_dict(await fn())
        except ValueError as e:  # wrong state for that action
            raise HTTPException(400, str(e))

    @r.post("/requests/{rid}/choose/{cid}")
    async def choose(rid: int, cid: int) -> dict:
        try:
            store.get_candidate(cid)
        except KeyError:
            raise HTTPException(404, "candidate not found")
        return await act(rid, lambda: worker.choose(rid, cid))

    @r.post("/requests/{rid}/cancel")
    async def cancel(rid: int) -> dict:
        return await act(rid, lambda: worker.cancel(rid))

    @r.post("/requests/{rid}/retry")
    async def retry(rid: int) -> dict:
        return await act(rid, lambda: worker.retry(rid))

    @r.delete("/requests/{rid}")
    async def delete(rid: int) -> dict:
        try:
            req = store.get_request(rid)
        except KeyError:
            raise HTTPException(404, "request not found")
        if req.state not in TERMINAL_STATES:
            raise HTTPException(409, "That track is still being worked on. Skip it first.")
        rejection = store.get_rejection_for_request(rid)
        attempt = store.get_attempt_for_request(rid)
        store.delete_request(rid)
        unlink_spectrogram(rejection)
        unlink_spectrogram(attempt)
        return {"ok": True}

    @r.post("/requests/clear-failed")
    async def clear_failed() -> dict:
        failed = store.list_requests(FAILED_STATES, limit=100_000)
        rejections = [store.get_rejection_for_request(req.id) for req in failed]
        attempts = [store.get_attempt_for_request(req.id) for req in failed]
        removed = store.delete_requests(FAILED_STATES)
        for rejection in rejections:
            unlink_spectrogram(rejection)
        for attempt in attempts:
            unlink_spectrogram(attempt)
        return {"removed": removed}

    return r
