import asyncio

from flackey.events import EventBus
from flackey.web.stream import event_stream


async def test_stream_sends_initial_then_published_then_heartbeat():
    bus = EventBus()
    gen = event_stream(bus, [("status", {"telegram_authorized": True})], heartbeat_s=0.05)
    assert await anext(gen) == 'event: status\ndata: {"telegram_authorized": true}\n\n'
    assert bus.subscribers == 1
    bus.publish("queue", None)
    assert await anext(gen) == "event: queue\ndata: null\n\n"
    assert await asyncio.wait_for(anext(gen), 1) == ": ping\n\n"
    await gen.aclose()
    assert bus.subscribers == 0
