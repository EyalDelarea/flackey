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
                      confirm_s=kw.get("confirm_s", 120), confirm_gap_s=kw.get("confirm_gap_s", 60),
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


def closed_then(*readings):
    """A checker that answers each reading in turn (True open, False closed, None could not tell),
    repeating the last one once they run out."""
    answers = list(readings)

    async def checker(port, http):
        r = answers.pop(0) if len(answers) > 1 else answers[0]
        return PortCheck(r, "1.2.3.4" if r is not None else None,
                         None if r is not None else "Could not reach the port test service.")
    return checker


async def test_one_closed_reading_is_not_reported_as_closed():
    """Port checks can false-alarm, so the sidebar must not go amber on the first one."""
    sharing, status, _ = make(check=PortCheck(False, "1.2.3.4"))
    state = await sharing.refresh()
    assert state["reachable"] is False and state["closed_confirmed"] is False
    assert status["sharing"]["closed_confirmed"] is False


async def test_a_second_closed_reading_a_minute_later_confirms_it():
    now = [0.0]
    sharing, _, _ = make(clock=lambda: now[0])
    sharing._check = closed_then(False)
    await sharing.refresh()
    now[0] = 90.0
    assert (await sharing.refresh())["closed_confirmed"] is True


async def test_two_closed_readings_seconds_apart_do_not_confirm():
    """At launch the sign-in and the loop's first tick both check within seconds: those two are one
    reading as far as a false alarm goes."""
    now = [0.0]
    sharing, _, _ = make(clock=lambda: now[0])
    sharing._check = closed_then(False)
    await sharing.refresh()
    now[0] = 5.0
    assert (await sharing.refresh())["closed_confirmed"] is False


async def test_an_open_reading_clears_a_confirmed_closed_port():
    now = [0.0]
    sharing, _, _ = make(clock=lambda: now[0])
    sharing._check = closed_then(False, False, True)
    await sharing.refresh()
    now[0] = 90.0
    assert (await sharing.refresh())["closed_confirmed"] is True
    now[0] = 200.0
    state = await sharing.refresh()
    assert state["reachable"] is True and state["closed_confirmed"] is False


async def test_an_unknown_reading_neither_confirms_nor_clears():
    now = [0.0]
    sharing, _, _ = make(clock=lambda: now[0])
    sharing._check = closed_then(False, None, False, None)
    await sharing.refresh()
    now[0] = 90.0
    assert (await sharing.refresh())["closed_confirmed"] is False    # unknown is not a second "closed"
    now[0] = 100.0
    assert (await sharing.refresh())["closed_confirmed"] is True
    now[0] = 200.0
    assert (await sharing.refresh())["closed_confirmed"] is True     # and not an "open" either


async def test_run_forever_rechecks_soon_after_a_first_closed_reading():
    """The confirmation lands in about two minutes, not after the half-hour recheck."""
    now, slept, sleep = ticking(3)
    sharing, status, calls = make(lease_s=10_000, recheck_s=1800, confirm_s=120, confirm_gap_s=60,
                                  check=PortCheck(False, "1.2.3.4"), clock=lambda: now[0], sleep=sleep)
    await run_ticks(sharing)
    assert slept[0] == 120                       # one closed reading: come back soon
    assert status["sharing"]["closed_confirmed"] is True
    assert slept[1:] == [1800, 1800]             # confirmed: back to the ordinary schedule
    assert calls["check"] == 3                   # at 0, at 120 (confirms), at 1920


async def test_fix_asks_the_router_again_even_with_a_mapping_held():
    sharing, _, calls = make()
    await sharing.refresh()
    await sharing.refresh()
    assert calls["map"] == 1                     # an ordinary recheck keeps the live mapping
    sharing.start_fix()
    await sharing._task
    assert calls["map"] == 2 and calls["check"] == 3


async def test_turning_soulseek_off_clears_a_confirmed_closed_port():
    now = [0.0]
    sharing, status, _ = make(clock=lambda: now[0], check=PortCheck(False, "1.2.3.4"))
    await sharing.refresh()
    now[0] = 90.0
    await sharing.refresh()
    assert status["sharing"]["closed_confirmed"] is True
    sharing._settings.soulseek_enabled = False
    await sharing._tick(None)
    assert status["sharing"]["closed_confirmed"] is False and status["sharing"]["enabled"] is False
