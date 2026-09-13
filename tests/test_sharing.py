import asyncio
import contextlib

from flackey.portcheck import PortCheck
from flackey.portmap import Mapping
from flackey.sharing import Sharing


class S:
    soulseek_enabled = True


def make(**kw):
    status = {}
    calls = {"map": 0, "unmap": 0, "check": 0}

    async def mapper(port, lease_s, http, **k):
        calls["map"] += 1
        if kw.get("map_raises"):
            raise OSError("boom")
        return kw.get("mapping", Mapping("natpmp", "10.0.0.1", port, port,
                                         kw.get("granted_lease_s", lease_s)))

    async def unmapper(mapping, http):
        calls["unmap"] += 1

    async def checker(port, http):
        calls["check"] += 1
        return kw.get("check", PortCheck(True, "1.2.3.4"))

    sharing = Sharing(S(), status, http=None, port=50300, mapper=mapper, unmapper=unmapper,
                      checker=checker, gateway=lambda: "10.0.0.1", lan=lambda g=None: "10.0.0.5",
                      lease_s=kw.get("lease_s", 100), recheck_s=kw.get("recheck_s", 1000),
                      clock=kw.get("clock", lambda: 0.0), sleep=kw.get("sleep", asyncio.sleep))
    return sharing, status, calls


def ticking(n: int = 3):
    """A fake clock and sleep that advance together and cancel after `n` sleeps, so `run_forever`
    can be run to a known number of ticks."""
    now = [0.0]
    slept: list[float] = []

    async def sleep(s):
        slept.append(s)
        now[0] += s
        if len(slept) >= n:
            raise asyncio.CancelledError

    return now, slept, sleep


async def run_ticks(sharing) -> None:
    try:
        await sharing.run_forever()
    except asyncio.CancelledError:
        pass


async def test_refresh_maps_then_checks_and_mirrors_state_into_status():
    sharing, status, calls = make()
    state = await sharing.refresh()
    assert calls == {"map": 1, "unmap": 0, "check": 1}
    assert state["mapping"] == "natpmp" and state["reachable"] is True
    assert state["public_ip"] == "1.2.3.4"
    assert state["lan_ip"] == "10.0.0.5" and state["gateway"] == "10.0.0.1"
    assert state["port"] == 50300
    assert state["checking"] is False and state["checked_at"] is not None
    assert state["error"] is None
    assert status["sharing"] == state


async def test_refresh_reports_a_closed_port_after_a_failed_mapping():
    sharing, _, _ = make(mapping=None, check=PortCheck(False, "1.2.3.4"))
    state = await sharing.refresh()
    assert state["mapping"] is None and state["reachable"] is False


async def test_refresh_keeps_unknown_when_the_check_could_not_run():
    sharing, _, _ = make(check=PortCheck(None, None, "Could not reach the port test service."))
    state = await sharing.refresh()
    assert state["reachable"] is None
    assert state["error"] == "Could not reach the port test service."


async def test_refresh_never_raises_when_the_router_call_blows_up():
    """The whole point of the broad catch: a network hiccup reports an error, it does not
    propagate into the loop that owns the app."""
    sharing, status, _ = make(map_raises=True)
    state = await sharing.refresh()
    assert state["checking"] is False and state["error"] is not None
    assert state["reachable"] is None
    assert status["sharing"] == state


async def test_start_refresh_marks_checking_and_finishes_in_the_background():
    sharing, status, calls = make()
    first = sharing.start_refresh()
    assert first["checking"] is True
    await sharing._task
    assert status["sharing"]["checking"] is False and calls["check"] == 1
    sharing.start_refresh()
    sharing.start_refresh()          # a refresh already running is not doubled
    await sharing._task
    assert calls["check"] == 2


async def test_run_forever_renews_the_lease_and_rechecks_on_schedule():
    now, slept, sleep = ticking(3)
    sharing, _, calls = make(clock=lambda: now[0], sleep=sleep)
    await run_ticks(sharing)
    assert calls["map"] >= 2                     # renewed at least once within three ticks
    assert all(s <= 50 for s in slept)           # never sleeps past half the lease


async def test_run_forever_rechecks_reachability_without_remapping_a_live_lease():
    now, slept, sleep = ticking(3)
    sharing, _, calls = make(lease_s=1000, recheck_s=100, clock=lambda: now[0], sleep=sleep)
    await run_ticks(sharing)
    assert slept == [100, 100, 100]              # capped by recheck_s, not by the lease
    assert calls["check"] == 3 and calls["map"] == 1


async def test_run_forever_follows_the_lease_the_router_granted_not_the_one_asked_for():
    now, slept, sleep = ticking(3)
    sharing, _, calls = make(granted_lease_s=20, clock=lambda: now[0], sleep=sleep)
    await run_ticks(sharing)
    assert slept == [10, 10, 10]                 # half of the 20s granted, not of the 100 asked
    assert calls["map"] == 3


async def test_run_forever_survives_a_refresh_that_cannot_be_completed():
    now, slept, sleep = ticking(2)
    sharing, status, _ = make(map_raises=True, clock=lambda: now[0], sleep=sleep)
    await run_ticks(sharing)
    assert len(slept) == 2 and status["sharing"]["error"] is not None


async def test_release_unmaps():
    sharing, _, calls = make()
    await sharing.refresh()
    await sharing.release()
    assert calls["unmap"] == 1


async def test_release_never_raises():
    async def unmapper(mapping, http):
        raise OSError("gone")

    sharing, _, _ = make()
    await sharing.refresh()
    sharing._unmap = unmapper
    await sharing.release()          # shutdown continues even when the router will not listen


async def test_disabled_soulseek_does_nothing():
    sharing, status, calls = make()
    sharing._settings.soulseek_enabled = False
    slept = []

    async def sleep(s):
        slept.append(s)
        raise asyncio.CancelledError

    sharing._sleep = sleep
    await run_ticks(sharing)
    assert calls["map"] == 0 and status["sharing"]["enabled"] is False


async def test_release_drops_a_check_still_in_flight():
    """Shutdown must not leave a refresh task behind for the closing loop to complain about."""
    sharing, _, _ = make()
    sharing.start_refresh()
    await sharing.release()
    with contextlib.suppress(asyncio.CancelledError):
        await sharing._task
    assert sharing._task.cancelled()


async def test_release_still_unmaps_when_the_shutdown_was_cancelled():
    """`release()` runs from app._run's `finally`, which on some shutdown paths executes with a
    cancellation already delivered to the task. A single delivered cancellation is not re-raised at
    the next await, so the unmap still goes out -- this pins that down."""
    sharing, _, calls = make()
    await sharing.refresh()

    async def body():
        try:
            await asyncio.sleep(3600)
        finally:
            await sharing.release()

    task = asyncio.create_task(body())
    await asyncio.sleep(0)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    assert calls["unmap"] == 1
