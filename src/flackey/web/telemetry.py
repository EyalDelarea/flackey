"""Settings > Privacy and the Welcome step's opt-in (spec §5): the toggle, New ID, the local "what's sent"
view, the event catalogue, and the one route the page posts its own setup-step events through."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .. import telemetry
from ..telemetry.schema import schema

# Only what the page itself knows: which wizard step it showed and how the owner left it.
UI_EVENTS = ("setup_step", "setup_finished")
# `emit`'s own parameters and the fields it sets on every event; never taken from the page.
_RESERVED = frozenset({"self", "event", "event_date", "session_id", "seq"})


def _client() -> telemetry.Telemetry:
    t = telemetry.current()
    if t is None:
        raise HTTPException(503, "usage stats are not running in this process")
    return t


def router() -> APIRouter:
    r = APIRouter(prefix="/api")

    @r.get("/telemetry")
    async def state() -> dict:
        t = telemetry.current()
        if t is None:
            return {"available": False, "enabled": False}
        return t.describe()

    @r.put("/telemetry")
    async def toggle(body: dict) -> dict:
        on = body.get("enabled")
        if not isinstance(on, bool):
            raise HTTPException(400, "enabled must be true or false")
        t = _client()
        try:
            t.opt_in() if on else t.opt_out()
        except ValueError as e:
            raise HTTPException(409, str(e))
        t.mark_asked()
        return t.describe()

    @r.post("/telemetry/asked")
    async def asked() -> dict:
        """Not now, a dismissed one-time prompt, or a Welcome step left without a choice: stats stay as
        they are (off), and the question is not asked again."""
        t = _client()
        t.mark_asked()
        return t.describe()

    @r.post("/telemetry/new-id")
    async def new_id() -> dict:
        t = _client()
        try:
            t.reset_id()
        except ValueError as e:
            raise HTTPException(409, str(e))
        return t.describe()

    @r.get("/telemetry/log")
    async def log() -> dict:
        return _client().snapshot()

    @r.get("/telemetry/catalog")
    async def catalog() -> dict:
        """Every event and field that can ever be sent, straight from the schema."""
        raw = schema().raw
        return {"schema": raw["schema"], "common": raw["common"], "events": raw["events"],
                "buckets": raw["buckets"]}

    @r.post("/telemetry/ui")
    async def ui_event(body: dict) -> dict:
        event, fields = body.get("event"), body.get("fields")
        if event not in UI_EVENTS or not isinstance(fields, dict):
            raise HTTPException(400, "not an event the page may send")
        # The envelope is the app's to fill: `event_date` would skip the schema (a bad one gets the whole
        # batch refused), and `event` or `self` would collide with `emit`'s own parameters.
        if any(str(k) in _RESERVED or str(k) in schema().common for k in fields):
            raise HTTPException(400, "not a field the page may send")
        telemetry.emit(event, **{str(k): v for k, v in fields.items()})
        return {"ok": True}

    return r
