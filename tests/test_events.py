import asyncio
import json

from flackey.events import EventBus, Status, format_sse


async def test_publish_reaches_every_subscriber_and_unsubscribe_stops_it():
    bus = EventBus()
    a, b = bus.subscribe(), bus.subscribe()
    bus.publish("request", {"id": 1})
    assert await a.get() == ("request", {"id": 1}) and await b.get() == ("request", {"id": 1})
    bus.unsubscribe(a)
    bus.publish("queue", None)
    assert await asyncio.wait_for(b.get(), 1) == ("queue", None)
    assert a.empty() and bus.subscribers == 1


async def test_full_queue_drops_instead_of_blocking():
    bus = EventBus(maxsize=2)
    q = bus.subscribe()
    for i in range(5):
        bus.publish("n", i)
    assert q.qsize() == 2


async def test_status_publishes_on_change():
    bus = EventBus()
    q = bus.subscribe()
    status = Status(bus, telegram_authorized=True)
    status["telegram_authorized"] = False
    assert await q.get() == ("status", {"telegram_authorized": False})
    status.update(worker_running=True)
    assert await q.get() == ("status", {"telegram_authorized": False, "worker_running": True})
    assert dict(Status(None, a=1)) == {"a": 1}   # no bus: a plain dict


def test_format_sse():
    assert format_sse("request", {"id": 1}) == 'event: request\ndata: {"id": 1}\n\n'
    assert json.loads(format_sse("queue", None).split("data: ")[1]) is None


async def test_close_ends_every_open_stream():
    """Ctrl-C hangs otherwise: uvicorn's shutdown awaits Server.wait_closed(), which on Python 3.12 waits for
    every open connection, and an SSE stream never ends on its own."""
    from flackey.web.stream import event_stream

    bus = EventBus()
    seen = []

    async def consume():
        async for chunk in event_stream(bus, [("status", {"ok": True})], heartbeat_s=10):
            seen.append(chunk)

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.01)
    bus.publish("track", {"id": 1})
    await asyncio.sleep(0.01)
    bus.close()
    await asyncio.wait_for(task, 1.0)
    assert len(seen) == 2 and bus.subscribers == 0
