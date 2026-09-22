from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse

from ..config import Settings
from ..deezer import DeezerApi, DeezerError
from ..inbox import BadLink, Inbox
from ..models import FAILED_STATES, SWEEPABLE_STATES, TERMINAL_STATES, RequestState
from ..store import Store
from ..worker import Worker
from . import Bundles, to_dict

RECENT = 500


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

    @r.get("/candidates/{cid}/preview")
    async def preview(cid: int):
        """Send the browser to Deezer's 30-second sample for this candidate.

        Resolved here, at play time, rather than stored: the preview URL Deezer hands back is signed and
        expires about fifteen minutes out, so a column would hold a dead link by the time anyone pressed
        play. Only `candidates.deezer_id` is durable, and that is what this route turns into a URL.

        A redirect rather than a proxy -- the `<audio>` element follows the 302 to the CDN itself, so
        nothing streams through this process. `no-store` is load-bearing: cache the redirect and a replay
        twenty minutes later chases a signature that has since expired."""
        try:
            candidate = store.get_candidate(cid)
        except KeyError:
            raise HTTPException(404, "candidate not found")
        if candidate.deezer_id is None:
            raise HTTPException(404, "no preview for this candidate")
        try:
            track = await DeezerApi().track(candidate.deezer_id)
        except DeezerError as e:
            raise HTTPException(502, f"couldn't reach Deezer: {e}")
        if not track.preview_url:
            raise HTTPException(404, "no preview for this candidate")
        return RedirectResponse(track.preview_url, status_code=302, headers={"Cache-Control": "no-store"})

    @r.post("/requests/{rid}/cancel")
    async def cancel(rid: int) -> dict:
        return await act(rid, lambda: worker.cancel(rid))

    @r.post("/requests/{rid}/retry")
    async def retry(rid: int) -> dict:
        return await act(rid, lambda: worker.retry(rid))

    @r.post("/requests/retry-failed")
    async def retry_failed(body: dict) -> dict:
        """"Retry all" on the Failed tab: the same `worker.retry` the per-row button calls, once per id,
        so one place keeps deciding what re-queuing *means*. What the batch may touch is narrower than what
        the button may, and that is decided here: `SWEEPABLE_STATES` leaves out CANCELLED, so a sweep never
        restarts tracks the owner stopped on purpose (issue #92) even if a stale page sends their ids. The
        rest of the ids come from the rows the owner can see, so the batch honours whatever they have
        filtered down to. An id the worker refuses -- deleted, stopped, or moved on since the page last
        heard -- lands in `skipped` instead of failing the whole batch, and the caller can tell the owner
        that nothing was re-queued."""
        ids = body.get("ids")
        if not isinstance(ids, list) or any(isinstance(i, bool) or not isinstance(i, int) for i in ids):
            raise HTTPException(400, "ids must be a list of request ids")
        retried: list[int] = []
        skipped: list[int] = []
        for rid in ids:
            try:
                if store.get_request(rid).state not in SWEEPABLE_STATES:
                    raise ValueError("not swept by Retry all")
                await worker.retry(rid)
            except (KeyError, ValueError):  # gone, or not in a state that can be retried
                skipped.append(rid)
            else:
                retried.append(rid)
        return {"retried": retried, "skipped": skipped}

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
