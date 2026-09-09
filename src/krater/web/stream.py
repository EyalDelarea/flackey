from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from ..events import EventBus, format_sse

HEARTBEAT_S = 15.0


async def event_stream(bus: EventBus, initial: list[tuple[str, Any]], heartbeat_s: float = HEARTBEAT_S) -> AsyncIterator[str]:
    q = bus.subscribe()
    try:
        for name, data in initial:
            yield format_sse(name, data)
        while not bus.closed:
            try:
                item = await asyncio.wait_for(q.get(), heartbeat_s)
            except TimeoutError:
                yield ": ping\n\n"  # keeps proxies and the browser from closing an idle stream
                continue
            if item is None:  # bus.close(): the server is shutting down
                return
            name, data = item
            yield format_sse(name, data)
    finally:
        bus.unsubscribe(q)


def router(bus: EventBus, status: dict) -> APIRouter:
    r = APIRouter(prefix="/api")

    @r.get("/events")
    async def events() -> StreamingResponse:
        return StreamingResponse(event_stream(bus, [("status", dict(status))]), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    return r
