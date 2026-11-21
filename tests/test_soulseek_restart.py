"""Restarting a slskd that died mid-session (audit SHL-7). The sidecar and the sleeps are fakes."""
from __future__ import annotations

import asyncio

import pytest

from flackey import slskd_process
from flackey.config import Settings
from flackey.slskd_process import SlskdProcess, SlskdStartTimeout
from flackey.soulseek_link import SoulseekLink


class FakeProc:
    def __init__(self, pid: int = 4242, returncode: int | None = None):
        self.pid, self.returncode = pid, returncode


def test_a_crashed_sidecar_reads_as_died_and_a_stopped_one_does_not(tmp_path):
    sidecar = SlskdProcess(tmp_path, "http://x", "k")
    sidecar._proc = FakeProc(pid=777)
    slskd_process.record_pid(tmp_path, 777)
    assert sidecar.died is False  # still running

    sidecar._proc.returncode = -9  # killed from outside: the pid file still names it
    assert sidecar.died is True

    slskd_process.clear_pid(tmp_path, 777)  # what every stop does
    assert sidecar.died is False


class Sidecar:
    """A stand-in for SlskdProcess as the supervisor sees it."""

    def __init__(self, died=False, fail_times=0):
        self.died, self.fail_times, self.starts = died, fail_times, 0

    async def start(self, **kw):
        self.starts += 1
        if self.fail_times:
            self.fail_times -= 1
            raise SlskdStartTimeout("slow")
        self.died = False

    async def stop(self, **kw):
        pass


def _link(tmp_path, *, factory=None, sleeps=None, soulseek=True):
    settings = Settings(_env_file=None, telegram_api_id=1, telegram_api_hash="h",
                        library_root=tmp_path / "lib", data_dir=tmp_path / "data",
                        slskd_api_key="k" if soulseek else None)

    async def sleep(s):
        if sleeps is not None:
            sleeps.append(s)
            if len(sleeps) > 40:
                raise asyncio.CancelledError
        await asyncio.sleep(0)

    class Worker:
        providers, status = [], {}

        def replace_providers(self, p):
            self.providers = list(p)

    return SoulseekLink(settings, Worker(), None, build_providers=lambda *_: [],
                        process_factory=factory or (lambda *a, **kw: Sidecar()),
                        installed=lambda _: True, sleep=sleep)


async def _supervise_until(link, done, sleeps):
    task = asyncio.create_task(link.supervise(poll_s=10, max_backoff_s=80, confirm_s=2))
    for _ in range(200):
        await asyncio.sleep(0)
        if done():
            break
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_a_sidecar_that_died_is_started_again(tmp_path):
    sleeps: list[float] = []
    link = _link(tmp_path, sleeps=sleeps)
    link.adopt(sidecar := Sidecar(died=True))
    await _supervise_until(link, lambda: sidecar.starts == 1, sleeps)
    assert sidecar.starts == 1 and sidecar.died is False
    assert 2 in sleeps  # looked twice before restarting


async def test_a_restart_that_keeps_failing_backs_off_to_a_cap(tmp_path):
    sleeps: list[float] = []
    link = _link(tmp_path, sleeps=sleeps)
    link.adopt(sidecar := Sidecar(died=True, fail_times=10))
    await _supervise_until(link, lambda: sidecar.starts >= 6, sleeps)
    assert sidecar.starts >= 6
    assert [s for s in sleeps if s != 2][:6] == [10, 20, 40, 80, 80, 80]


async def test_a_running_or_stopped_sidecar_is_left_alone(tmp_path):
    sleeps: list[float] = []
    link = _link(tmp_path, sleeps=sleeps)
    link.adopt(sidecar := Sidecar(died=False))
    await _supervise_until(link, lambda: len(sleeps) > 5, sleeps)
    assert sidecar.starts == 0


async def test_soulseek_switched_off_is_never_restarted(tmp_path):
    sleeps: list[float] = []
    link = _link(tmp_path, sleeps=sleeps, soulseek=False)
    link.adopt(sidecar := Sidecar(died=True))
    await _supervise_until(link, lambda: len(sleeps) > 5, sleeps)
    assert sidecar.starts == 0


async def test_a_sidecar_the_wizard_replaced_meanwhile_is_not_restarted(tmp_path):
    """The confirm look sees a different handle: the wizard has just swapped in a fresh one."""
    link = _link(tmp_path)
    link.adopt(old := Sidecar(died=True))
    fresh = Sidecar()

    async def sleep(s):
        if s == 2:
            link.process = fresh
        await asyncio.sleep(0)

    link._sleep = sleep
    task = asyncio.create_task(link.supervise(poll_s=10, confirm_s=2))
    for _ in range(50):
        await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert old.starts == 0 and fresh.starts == 0


