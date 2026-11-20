"""A slskd that quits on start (audit SHL-6), its log growing (SHL-12), and settings written from two
threads (SHL-13).

The subprocess is a fake and the clock is injected: no test starts slskd or waits for real.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from flackey import slskd_process
from flackey.config import Settings, save_settings
from flackey.slskd_process import SlskdExited, SlskdProcess, SlskdStartTimeout
from flackey.soulseek_link import SoulseekLink


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    async def sleep(self, s: float) -> None:
        self.t += s


class FakeProc:
    def __init__(self, pid: int = 4242, returncode: int | None = None):
        self.pid, self.returncode = pid, returncode
        self.terminated = 0

    def terminate(self) -> None:
        self.terminated += 1
        self.returncode = self.returncode if self.returncode is not None else 0

    def kill(self) -> None:
        self.returncode = -9


def _spawning(monkeypatch, proc: FakeProc, answers=None):
    async def spawn(*args, **kwargs):
        return proc

    seq = iter(answers or [])

    async def check(self, client=None):
        return next(seq, "ConnectError: refused")

    monkeypatch.setattr(slskd_process, "is_installed", lambda _: True)
    monkeypatch.setattr(slskd_process.asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(SlskdProcess, "_check", check)


def _link(tmp_path, *, factory):
    settings = Settings(_env_file=None, telegram_api_id=1, telegram_api_hash="h",
                        library_root=tmp_path / "lib", data_dir=tmp_path / "data", slskd_api_key="k")

    class Worker:
        providers, status = [], {}

        def replace_providers(self, p):
            self.providers = list(p)

    return SoulseekLink(settings, Worker(), None, build_providers=lambda *_: [], process_factory=factory,
                        installed=lambda _: True)


# --- SHL-6: a slskd that quits on start ---------------------------------------------------------------

async def test_a_slskd_that_quits_on_start_is_reported_at_once_not_after_three_minutes(tmp_path, monkeypatch, caplog):
    proc = FakeProc(returncode=1)  # exited before the first health check: the port was taken
    _spawning(monkeypatch, proc)
    (tmp_path / "slskd.log").write_text("starting\nFailed to bind to address http://0.0.0.0:5030: address already in use\n")
    clock = Clock()
    sidecar = SlskdProcess(tmp_path, "http://127.0.0.1:5030", "key", clock=clock, sleep=clock.sleep)

    with pytest.raises(SlskdExited) as raised:
        await sidecar.start(timeout_s=180)

    assert clock.t < 5  # not the whole SLSKD_START_TIMEOUT_S
    assert "exit code 1" in str(raised.value) and "port" in str(raised.value)
    assert "address already in use" in caplog.text  # slskd's own reason, copied into our log


async def test_a_slow_slskd_is_still_a_timeout(tmp_path, monkeypatch):
    proc = FakeProc()
    _spawning(monkeypatch, proc)
    clock = Clock()
    sidecar = SlskdProcess(tmp_path, "http://127.0.0.1:5030", "key", clock=clock, sleep=clock.sleep)
    with pytest.raises(SlskdStartTimeout):
        await sidecar.start(timeout_s=3)


async def test_the_wizard_names_a_taken_port_rather_than_slowness(tmp_path):
    class Exits:
        async def start(self, **kw):
            raise SlskdExited("slskd quit on start (exit code 1)")

        async def stop(self, **kw):
            pass

    link = _link(tmp_path, factory=lambda *a, **kw: Exits())
    link.start_connect()
    await link._task
    assert link.state["state"] == "failed"
    assert "port" in link.state["error"] and "too long" not in link.state["error"]


# --- SHL-12: slskd.log rotation -----------------------------------------------------------------------

async def test_a_large_slskd_log_is_moved_aside_on_start(tmp_path, monkeypatch):
    log_path = tmp_path / "slskd.log"
    log_path.write_bytes(b"x" * (slskd_process.LOG_ROTATE_BYTES + 1))
    _spawning(monkeypatch, FakeProc(), answers=["refused", None])
    clock = Clock()
    await SlskdProcess(tmp_path, "http://127.0.0.1:5030", "key", clock=clock, sleep=clock.sleep).start()

    assert (tmp_path / "slskd.log.1").stat().st_size == slskd_process.LOG_ROTATE_BYTES + 1
    assert log_path.stat().st_size == 0


def test_a_small_slskd_log_is_left_alone(tmp_path):
    log_path = tmp_path / "slskd.log"
    log_path.write_text("short")
    slskd_process.rotate_log(log_path)
    assert log_path.read_text() == "short" and not (tmp_path / "slskd.log.1").exists()


# --- SHL-13: settings written from two threads --------------------------------------------------------

def test_settings_saved_from_several_threads_at_once_never_fail_or_lose_a_key(tmp_path):
    settings = Settings(_env_file=None, telegram_api_id=1, telegram_api_hash="h",
                        library_root=tmp_path / "lib", data_dir=tmp_path / "data")
    errors: list[BaseException] = []

    def saver(i: int) -> None:
        for n in range(100):
            try:
                if i % 2:
                    save_settings(settings, window_size=[600 + n, 500])
                else:
                    save_settings(settings, auto_update_check=bool(n % 2))
            except BaseException as e:  # noqa: BLE001 -- any failure fails the test
                errors.append(e)

    threads = [threading.Thread(target=saver, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    data = json.loads(settings.settings_path.read_text())
    assert "window_size" in data and "auto_update_check" in data
    assert list(Path(settings.data_dir).glob("*.tmp")) == []
