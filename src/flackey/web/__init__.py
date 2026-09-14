from __future__ import annotations

import dataclasses
import logging
from enum import Enum
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .. import __version__
from ..attempts import raw_size_bytes
from ..config import Settings
from ..events import EventBus, Status
from ..fingerprint import fpcalc_available
from ..inbox import Inbox
from ..models import Verdict
from ..store import Store
from ..telegram import TelegramLogin
from ..worker import Worker, format_line

log = logging.getLogger(__name__)
DEV_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]  # Vite dev server
SETUP_DONE_KEY = "setup_done"
NO_UI = ("<h1>flackey</h1><p>The UI is not built. Run <code>npm --prefix web install &amp;&amp; "
         "npm --prefix web run build</code>, then reload. API at <a href='/api/health'>/api/health</a>.</p>")
# Every route is `async def` on purpose: FastAPI runs plain `def` routes in a threadpool, and the Store's
# sqlite connection was created on the event-loop thread. The handlers do only sqlite reads of a few
# milliseconds, so they can stay on the loop.


def to_dict(obj: Any) -> Any:
    if dataclasses.is_dataclass(obj):
        return {f.name: to_dict(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, list):
        return [to_dict(x) for x in obj]
    if isinstance(obj, dict):
        return {k: to_dict(v) for k, v in obj.items()}
    return obj


class Bundles:
    """Everything the page needs about one request, in one JSON object."""

    def __init__(self, store: Store):
        self.store = store

    def request(self, rid: int) -> dict:
        r = self.store.get_request(rid)  # KeyError propagates
        catalog = self.store.get_catalog_track(r.catalog_track_id) if r.catalog_track_id else None
        track = None
        if r.track_id:
            try:
                track = self.track(r.track_id)
            except KeyError:
                track = None
        return {"request": to_dict(r), "candidates": to_dict(self.store.get_candidates(rid)),
                "catalog": to_dict(catalog), "track": track,
                "rejection": to_dict(self.store.get_rejection_for_request(rid)),
                "attempt": to_dict(self.store.get_attempt_for_request(rid))}

    def track(self, tid: int) -> dict:
        t = self.store.get_track(tid)
        d = to_dict(t)
        d["catalog"] = to_dict(self.store.get_catalog_track(t.catalog_track_id)) if t.catalog_track_id else None
        verdict = Verdict(True, t.fmt, t.bitrate_kbps, t.cutoff_hz, "", bit_depth=t.bit_depth, sample_rate=t.sample_rate)
        d["format"] = {"fmt": t.fmt, "bit_depth": t.bit_depth, "sample_rate": t.sample_rate, "source": t.source,
                       "source_fmt": t.source_fmt, "label": format_line(verdict, t.source, t.source_fmt)}
        d["evidence"] = [{"kind": e.kind, "value": e.value} for e in self.store.list_evidence(tid)]
        return d


def create_app(store: Store, worker: Worker, inbox: Inbox, settings: Settings, ui_dir: Path | None = None,
               status: Status | dict | None = None, bus: EventBus | None = None, opener=None, picker=None,
               login: TelegramLogin | None = None, link=None, sharing=None) -> FastAPI:
    # here, not at module top: the routers import `Bundles` from this module
    from . import library, lossless, pick, requests, stream, telegram
    from . import sharing as sharing_web  # aliased: `sharing` here is the service, not the module

    bus = bus if bus is not None else EventBus()
    if status is None:
        status = Status(bus, telegram_authorized=True, worker_running=False,
                        setup_done=store.get_setting(SETUP_DONE_KEY) == "1")
    elif isinstance(status, Status) and status.bus is None:
        status.bus = bus
    bundles = Bundles(store)

    def on_change(kind: str, oid: int) -> None:
        if kind == "request":
            bus.publish("request", bundles.request(oid))
        elif kind == "track":
            bus.publish("track", bundles.track(oid))
        else:
            bus.publish("queue", None)

    store.listeners.append(on_change)

    app = FastAPI(title="flackey", version=__version__)
    app.add_middleware(CORSMiddleware, allow_origins=DEV_ORIGINS, allow_methods=["*"], allow_headers=["*"])

    @app.exception_handler(HTTPException)
    async def _http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
        if exc.status_code >= 500:
            log.error("%s %s -> %d %s", request.method, request.url.path, exc.status_code, exc.detail)
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail}, headers=exc.headers)

    @app.exception_handler(Exception)
    async def _unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(status_code=500, content={"detail": "Something went wrong. The log has the details."})

    @app.get("/api/health")
    async def health() -> dict:
        return {"ok": True, "version": __version__,
                "telegram_authorized": bool(status.get("telegram_authorized", True)),
                "worker_running": bool(status.get("worker_running", False)),
                "setup_done": bool(status.get("setup_done", False)),
                "telegram_configured": settings.telegram_configured,
                # From `status`, so a sign-in or a skip reaches the page on the event that follows it;
                # the setting is the fallback for a process that never puts the flag in `status`.
                "source_enabled": bool(status.get("source_enabled", settings.source_enabled)),
                "sharing": status.get("sharing"),
                "lossless": {"enabled": settings.lossless_enabled, "provider": status.get("lossless_provider"),
                             "fpcalc": fpcalc_available(), "attempts_24h": store.attempt_counts(24),
                             "raw_mb": round(raw_size_bytes(settings.lossless_raw_dir) / 1e6, 1)}}

    app.include_router(requests.router(store, worker, inbox, bundles, settings))
    app.include_router(stream.router(bus, status))
    app.include_router(library.router(store, settings, status, bundles, link=link,
                                      **({"opener": opener} if opener else {})))
    app.include_router(telegram.router(login, status, settings))
    app.include_router(pick.router(**({"picker": picker} if picker else {})))
    app.include_router(lossless.router(store, worker))
    app.include_router(sharing_web.router(sharing))

    if ui_dir is not None and ui_dir.exists():
        app.mount("/", StaticFiles(directory=str(ui_dir), html=True), name="ui")
    else:
        @app.get("/", response_class=HTMLResponse)
        async def no_ui() -> str:
            return NO_UI
    return app
