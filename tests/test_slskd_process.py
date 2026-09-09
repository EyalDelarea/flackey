import time
from pathlib import Path

import httpx
import pytest
import respx

from flackey import slskd_process
from flackey.slskd_binary import SlskdBinaryError
from flackey.slskd_process import SlskdProcess

BASE = "http://slskd.test/api/v0"


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    async def sleep(self, s: float) -> None:
        self.t += s


class FakeProc:
    """A double for asyncio.subprocess.Process: exposes just what stop() touches."""

    def __init__(self):
        self.returncode: int | None = None
        self.terminated = 0
        self.killed = 0

    def terminate(self) -> None:
        self.terminated += 1

    def kill(self) -> None:
        self.killed += 1


async def test_stop_on_never_started_supervisor_is_safe(tmp_path: Path):
    proc = SlskdProcess(tmp_path, "http://slskd.test", "key")
    await proc.stop()
    assert proc.running is False


@respx.mock
async def test_start_returns_quietly_when_something_already_answers(tmp_path: Path, monkeypatch):
    respx.get(f"{BASE}/application").mock(return_value=httpx.Response(200, json={"ok": True}))
    proc = SlskdProcess(tmp_path, "http://slskd.test", "key")

    async def unexpected_spawn(*args, **kwargs):
        raise AssertionError("must not spawn a second slskd when one already answers")

    monkeypatch.setattr(slskd_process.asyncio, "create_subprocess_exec", unexpected_spawn)

    await proc.start()

    assert proc.running is False  # this instance never spawned anything of its own


@respx.mock
async def test_start_raises_when_not_installed_and_nothing_answers(tmp_path: Path):
    respx.get(f"{BASE}/application").mock(side_effect=httpx.ConnectError("refused"))
    proc = SlskdProcess(tmp_path, "http://slskd.test", "key")

    with pytest.raises(SlskdBinaryError):
        await proc.start()


@respx.mock
async def test_wait_healthy_returns_false_on_timeout_without_real_sleep(tmp_path: Path):
    respx.get(f"{BASE}/application").mock(side_effect=httpx.ConnectError("refused"))
    clock = Clock()
    proc = SlskdProcess(tmp_path, "http://slskd.test", "key", clock=clock, sleep=clock.sleep)

    wall_start = time.perf_counter()
    result = await proc.wait_healthy(timeout_s=5)
    wall_elapsed = time.perf_counter() - wall_start

    assert result is False
    assert wall_elapsed < 1.0  # never performed a real wait
    assert clock.t >= 5  # but the injected clock did advance past the timeout


@respx.mock
async def test_wait_healthy_returns_true_once_something_answers(tmp_path: Path):
    respx.get(f"{BASE}/application").mock(return_value=httpx.Response(200, json={"ok": True}))
    clock = Clock()
    proc = SlskdProcess(tmp_path, "http://slskd.test", "key", clock=clock, sleep=clock.sleep)

    assert await proc.wait_healthy(timeout_s=5) is True
    assert clock.t == 0.0  # answered on the first probe, no sleeping needed


async def test_stop_sends_sigterm_then_waits_for_graceful_exit(tmp_path: Path):
    fake = FakeProc()
    clock = Clock()
    calls = {"n": 0}

    async def sleep(s: float) -> None:
        clock.t += s
        calls["n"] += 1
        if calls["n"] == 1:
            fake.returncode = 0  # exits cleanly on the first poll tick

    proc = SlskdProcess(tmp_path, "http://slskd.test", "key", clock=clock, sleep=sleep)
    proc._proc = fake

    await proc.stop(timeout_s=5)

    assert fake.terminated == 1
    assert fake.killed == 0
    assert proc.running is False


async def test_stop_escalates_to_sigkill_after_sigterm_times_out(tmp_path: Path):
    fake = FakeProc()
    clock = Clock()

    async def sleep(s: float) -> None:
        clock.t += s
        if fake.killed:
            fake.returncode = -9  # only dies once SIGKILL has actually been sent

    proc = SlskdProcess(tmp_path, "http://slskd.test", "key", clock=clock, sleep=sleep)
    proc._proc = fake

    await proc.stop(timeout_s=1)

    assert fake.terminated == 1
    assert fake.killed == 1
    assert proc.running is False


async def test_stop_is_safe_to_call_twice(tmp_path: Path):
    fake = FakeProc()
    clock = Clock()

    async def sleep(s: float) -> None:
        clock.t += s
        fake.returncode = 0

    proc = SlskdProcess(tmp_path, "http://slskd.test", "key", clock=clock, sleep=sleep)
    proc._proc = fake

    await proc.stop()
    await proc.stop()  # no error; self._proc is already None, so this is a pure no-op

    assert fake.terminated == 1  # signaled exactly once, not twice
