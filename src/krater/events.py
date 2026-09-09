from __future__ import annotations

import asyncio
import json
from typing import Any

Event = tuple[str, Any]


class EventBus:
    """Fan-out of (name, data) events to every open SSE connection. Publishing is synchronous and never
    blocks the worker: a subscriber that stops reading loses events once its queue is full, and the page
    reloads its state on reconnect anyway."""

    def __init__(self, maxsize: int = 1000):
        self.maxsize = maxsize
        self.closed = False
        self._queues: list[asyncio.Queue[Event | None]] = []

    def subscribe(self) -> asyncio.Queue[Event | None]:
        q: asyncio.Queue[Event | None] = asyncio.Queue(maxsize=self.maxsize)
        self._queues.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[Event | None]) -> None:
        if q in self._queues:
            self._queues.remove(q)

    def publish(self, name: str, data: Any) -> None:
        for q in list(self._queues):
            try:
                q.put_nowait((name, data))
            except asyncio.QueueFull:
                pass

    def close(self) -> None:
        """Tell every open stream to finish. Ctrl-C hangs otherwise: uvicorn's shutdown awaits
        Server.wait_closed(), which on Python 3.12 waits for every open connection, and an SSE stream never
        ends on its own. A full queue is drained first so the sentinel always lands."""
        self.closed = True
        for q in list(self._queues):
            while q.full():
                q.get_nowait()
            q.put_nowait(None)

    @property
    def subscribers(self) -> int:
        return len(self._queues)


class Status(dict):
    """The app's shared flags ({"telegram_authorized": bool, "worker_running": bool}); every change is
    published as a `status` event so the page can flip its banner without polling."""

    def __init__(self, bus: EventBus | None = None, **initial):
        super().__init__(**initial)
        self.bus = bus

    def __setitem__(self, key, value) -> None:
        super().__setitem__(key, value)
        self._publish()

    def update(self, *args, **kwargs) -> None:
        super().update(*args, **kwargs)
        self._publish()

    def _publish(self) -> None:
        if self.bus is not None:
            self.bus.publish("status", dict(self))


def format_sse(name: str, data: Any) -> str:
    return f"event: {name}\ndata: {json.dumps(data)}\n\n"
