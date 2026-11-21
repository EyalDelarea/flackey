from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse

from .. import telemetry
from ..config import Settings
from ..deezer import DeezerApi, DeezerError
from ..inbox import BadLink, Inbox
from ..models import FAILED_STATES, SWEEPABLE_STATES, TERMINAL_STATES, RequestState
from ..store import Store
from ..telemetry import buckets
from ..worker import CANCELLABLE, Worker, usage
from . import Bundles, to_dict
from .library import reveal_path

log = logging.getLogger(__name__)
RECENT = 500


def router(store: Store, worker: Worker, inbox: Inbox, bundles: Bundles, settings: Settings) -> APIRouter:
    r = APIRouter(prefix="/api")

    def bundle_or_404(rid: int) -> dict:
        try:
            return bundles.request(rid)
        except KeyError:
            raise HTTPException(404, "request not found")

    def unlink_under(directory: Path, path: str | None) -> None:
        """Best-effort: a removed row shouldn't leave an orphan file behind. Only ever touches a path under
        `directory` - never anything else a stray/legacy path might point at."""
        if not path:
            return
        file = Path(path)
        # Both sides resolved, or `rejected/../../x` would pass a check that only compares the spelling.
        if file.resolve().is_relative_to(directory.resolve()):
            try:
                file.unlink()
            except OSError as e:
                log.debug("could not remove %s: %s", file, e)

    def unlink_evidence(rejection) -> None:
        """Both files a rejection can leave behind: the spectrogram PNG, and the refused copy itself, which
        is kept so the owner can listen before deciding. `attempt`
        rows reach here too and carry only the PNG, so the audio half is read defensively."""
        if not rejection:
            return
        unlink_under(settings.spectrogram_dir, rejection.spectrogram_path)
        unlink_under(settings.rejected_dir, getattr(rejection, "audio_path", None))

    def forget(rid: int) -> None:
        """One request row and the evidence files it owns. Never the filed track: see `delete_request`."""
        rejection = store.get_rejection_for_request(rid)
        attempt = store.get_attempt_for_request(rid)
        held = store.held_lossless(rid)          # a Soulseek copy held back beside the filed one (issue #97)
        worker.forget_comparison(rid)
        store.delete_request(rid)
        unlink_evidence(rejection)
        unlink_evidence(attempt)
        unlink_evidence(held)

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
        request_added(store, sub, "ui")
        return {"summary": sub.summary(), "request_ids": sub.request_ids, "playlist_id": sub.playlist_id,
                "name": sub.name, "total": sub.total, "already_in_library": sub.already_in_library,
                "already_queued": sub.already_queued}

    def action(name: str, req=None, count: int | None = None, ok: bool = True) -> None:
        if not telemetry.enabled():
            return
        telemetry.emit("request_action", action=name, from_state=req.state.value if req else None,
                       failed_stage=(req.failed_stage or "") if req else None,
                       count=buckets.n(count) if count is not None else None, result="ok" if ok else "refused")

    async def act_logged(rid: int, name: str | None, fn):
        """`act`, with the owner's action recorded for usage stats; `name` None works it out for retry."""
        try:
            before = store.get_request(rid)
        except KeyError:
            before = None
        if name is None and before is not None:
            name = "try_now" if before.state.value == "queued" and before.retry_after else "retry"
        try:
            out = await act(rid, fn)
        except HTTPException:
            action(name or "retry", before, ok=False)
            raise
        action(name or "retry", before)
        return out

    def kept(which: str, rejection, result: str) -> None:
        """`keep_anyway`: which refusal was overruled -- its kind, tier, reason code and the detectors that
        fired, bucketed as `quality_check` sends them -- so a check that is overruled often shows up."""
        if not telemetry.enabled():
            return
        try:
            kind = getattr(rejection, "kind", None) if which == "keep_it_anyway" else "quality"
            telemetry.emit("keep_anyway", which=which, result=result,
                           rejection_kind=kind if kind in ("quality", "different_recording") else None,
                           route="soulseek" if which == "keep_lossless" else "telegram",
                           **usage.refusal_fields(rejection))
        except Exception:
            log.debug("usage stats: keep_anyway failed", exc_info=True)

    async def keep(rid: int, which: str, fn):
        rejection = store.get_rejection_for_request(rid) if which == "keep_it_anyway" else store.held_lossless(rid)
        try:
            out = await act(rid, fn)
        except HTTPException:
            kept(which, rejection, "failed")
            raise
        state = out.get("state") if isinstance(out, dict) else None
        kept(which, rejection, "filed" if state == "done" else "could_not_check")
        return out

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

    @r.get("/requests/{rid}/reference/audio")
    async def reference_audio(rid: int):
        """The audio this request was checked against, when it is something a browser can play.

        A rejected row says the file was "a different recording"; this is the recording it differed from,
        so the owner can hear both and judge (issue #92). Only the Deezer half is served: that reference is
        a 30 s preview the CDN still has, resolved and redirected exactly like a candidate's. A YouTube
        reference is the owner's own video, and nothing local keeps its audio -- the page links to it at
        the excerpt offset instead, from the `reference` on the bundle."""
        bundle_or_404(rid)
        stored = store.get_reference(rid)
        if stored is None or stored.get("kind") != "deezer":
            raise HTTPException(404, "no playable reference for this request")
        try:
            track = await DeezerApi().track(int(stored["ref"]))
        except (TypeError, ValueError):
            raise HTTPException(404, "no playable reference for this request")
        except DeezerError as e:
            raise HTTPException(502, f"couldn't reach Deezer: {e}")
        if not track.preview_url:
            raise HTTPException(404, "no playable reference for this request")
        return RedirectResponse(track.preview_url, status_code=302, headers={"Cache-Control": "no-store"})

    @r.post("/requests/{rid}/accept")
    async def accept(rid: int) -> dict:
        """"Keep it anyway" on a rejected row: file the copy the recording or quality check refused."""
        return await keep(rid, "keep_it_anyway", lambda: worker.accept_rejection(rid))

    @r.post("/requests/{rid}/keep-held")
    async def keep_held(rid: int) -> dict:
        """"Keep it anyway" on the Soulseek copy the quality check held back: it replaces the lossy copy
        filed in its place, or is filed where nothing was (issue #97)."""
        return await keep(rid, "keep_lossless", lambda: worker.keep_held_lossless(rid))

    @r.post("/requests/{rid}/cancel")
    async def cancel(rid: int) -> dict:
        return await act_logged(rid, "cancel", lambda: worker.cancel(rid))

    @r.post("/requests/{rid}/retry")
    async def retry(rid: int) -> dict:
        return await act_logged(rid, None, lambda: worker.retry(rid))

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
        action("retry_all", count=len(retried), ok=bool(retried) or not ids)
        return {"retried": retried, "skipped": skipped}

    @r.delete("/requests/{rid}")
    async def delete(rid: int) -> dict:
        try:
            req = store.get_request(rid)
        except KeyError:
            raise HTTPException(404, "request not found")
        if req.state not in TERMINAL_STATES:
            raise HTTPException(409, "That track is still being worked on. Skip it first.")
        action("remove", req)
        forget(rid)
        return {"ok": True}

    @r.post("/requests/remove")
    async def remove(body: dict) -> dict:
        """"Remove these" on the rows the owner is looking at, and Remove on a single row that is still
        live. Unlike DELETE this takes rows that have not finished: a request parked for six hours waiting
        on Soulseek is exactly the one an owner wants gone, and making them Stop each one first and then
        come back to Remove it is two presses per row for one decision. So a live row is stopped here, by
        the same `CANCELLABLE` the Stop button is held to, and then forgotten like any finished one.
        Verifying and filing are refused for the reason Stop refuses them -- a file is moving into the
        library -- and land in `skipped` with ids that are gone, as `retry_failed` does. Only request rows
        go: a filed track and its file stay in the library, the same as DELETE."""
        ids = body.get("ids")
        if not isinstance(ids, list) or any(isinstance(i, bool) or not isinstance(i, int) for i in ids):
            raise HTTPException(400, "ids must be a list of request ids")
        removed: list[int] = []
        skipped: list[int] = []
        for rid in ids:
            try:
                state = store.get_request(rid).state
                if state not in TERMINAL_STATES:
                    if state not in CANCELLABLE:
                        raise ValueError("being checked or filed")
                    await worker.cancel_and_settle(rid)
                forget(rid)
            except (KeyError, ValueError):  # gone, or moved into verify/filing since the page last heard
                skipped.append(rid)
            else:
                removed.append(rid)
        action("remove" if len(ids) == 1 else "remove_many", count=len(removed), ok=bool(removed) or not ids)
        return {"removed": removed, "skipped": skipped}

    @r.post("/requests/clear-failed")
    async def clear_failed() -> dict:
        failed = store.list_requests(FAILED_STATES, limit=100_000)
        rejections = [store.get_rejection_for_request(req.id) for req in failed]
        rejections += [store.held_lossless(req.id) for req in failed]      # issue #97
        attempts = [store.get_attempt_for_request(req.id) for req in failed]
        removed = store.delete_requests(FAILED_STATES)
        for rid in removed:
            worker.forget_comparison(rid)
        for rejection in rejections:
            unlink_evidence(rejection)
        for attempt in attempts:
            unlink_evidence(attempt)
        action("clear_failed", count=len(removed))
        return {"removed": removed}

    def missing_line(req) -> str:
        """A failed request as a shopping-list line: the catalog's name for it where there is one, else what
        the link said, else the link itself."""
        cat = store.get_catalog_track(req.catalog_track_id) if req.catalog_track_id else None
        if cat:
            return f"{cat.artist} - {cat.title}" + (f" ({cat.mix_name})" if cat.mix_name else "")
        if req.query_artist and req.query_title:
            return f"{req.query_artist} - {req.query_title}" + (f" ({req.query_version})" if req.query_version else "")
        return req.raw_text

    @r.post("/requests/export-missing")
    async def export_missing() -> dict:
        """Every failed track, one per line, as a text file for a shopping session (idea 8), shown in the
        file manager. Downloads where there is one, else Flackey's own data folder. Nothing is fetched."""
        failed = sorted(store.list_requests(FAILED_STATES, limit=100_000), key=lambda q: q.id)
        if not failed:
            raise HTTPException(409, "Nothing is missing: the Failed list is empty.")
        downloads = Path.home() / "Downloads"
        folder = downloads if downloads.is_dir() else settings.data_dir
        path = folder / f"Flackey missing tracks {date.today().isoformat()}.txt"
        lines = list(dict.fromkeys(missing_line(q) for q in failed))      # one line per track, in order
        try:
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        except OSError as e:
            raise HTTPException(500, f"Could not write the list to {folder}: {e.strerror or e}")
        reveal_path(path)
        return {"path": str(path), "count": len(lines)}

    return r


def request_added(store: Store, sub, via: str) -> None:
    """`request_added` per paste: its kind, where it came from, and a playlist's size as a bucket."""
    if not telemetry.enabled() or not sub.request_ids:
        return
    try:
        kind = store.get_request(sub.request_ids[0]).kind.value
        if sub.playlist_id is not None:
            kind = {"yt_track": "yt_playlist", "spotify_track": "spotify_playlist"}.get(kind, kind)
        telemetry.emit("request_added", kind=kind, via=via,
                       playlist_tracks=buckets.n(sub.total) if sub.playlist_id is not None else None)
    except Exception:
        log.debug("usage stats: request_added failed", exc_info=True)
