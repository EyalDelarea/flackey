"""Flackey owns the slskd it runs, including one it adopted from an earlier run, and stops it at quit --
but never signals a process that is not its own bundled slskd."""
import atexit
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest
import respx

from flackey import slskd_process
from flackey.slskd_binary import binary_path
from flackey.slskd_process import SlskdProcess

BASE = "http://slskd.test/api/v0"
APPLICATION = {"version": {"current": "0.22.5"}, "server": {"state": "Connected, LoggedIn", "isLoggedIn": True}}


class FakeOS:
    """The process table and the signals sent into it: `exe` maps a live pid to its executable path, and a
    signal of the kind that ends a process takes it out of the table (after `survives` more of them)."""

    def __init__(self, exe: dict[int, str], survives_term: bool = False):
        self.exe = dict(exe)
        self.sent: list[tuple[int, str]] = []
        self.survives_term = survives_term

    def exe_of(self, pid: int) -> str | None:
        return self.exe.get(pid)

    def pids(self) -> list[int]:
        return list(self.exe)

    def terminate(self, pid: int) -> None:
        self.sent.append((pid, "term"))
        if not self.survives_term:
            self.exe.pop(pid, None)

    def kill(self, pid: int) -> None:
        self.sent.append((pid, "kill"))
        self.exe.pop(pid, None)


@pytest.fixture
def fake_os(monkeypatch):
    def install(exe: dict[int, str], **kw) -> FakeOS:
        fake = FakeOS(exe, **kw)
        monkeypatch.setattr(slskd_process, "exe_of", fake.exe_of)
        monkeypatch.setattr(slskd_process, "list_pids", fake.pids)
        monkeypatch.setattr(slskd_process, "_terminate", fake.terminate)
        monkeypatch.setattr(slskd_process, "_kill", fake.kill)
        return fake
    return install


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.t += s

    async def asleep(self, s: float) -> None:
        self.t += s


def ours(data_dir: Path) -> str:
    return str(binary_path(data_dir))


def test_stop_owned_stops_the_slskd_named_in_the_pid_file(tmp_path, fake_os):
    fake = fake_os({777: ours(tmp_path)})
    slskd_process.record_pid(tmp_path, 777)
    clock = Clock()

    assert slskd_process.stop_owned(tmp_path, clock=clock, sleep=clock.sleep) is True

    assert fake.sent == [(777, "term")]
    assert slskd_process.read_pid(tmp_path) is None


def test_stop_owned_never_signals_a_foreign_process(tmp_path, fake_os):
    """The pid in the file was reused by something else after slskd died: leave it alone."""
    fake = fake_os({777: "/Applications/Safari.app/Contents/MacOS/Safari"})
    slskd_process.record_pid(tmp_path, 777)

    assert slskd_process.stop_owned(tmp_path, sleep=lambda s: None) is False

    assert fake.sent == []
    assert slskd_process.read_pid(tmp_path) is None  # a stale file is cleared, not kept


def test_stop_owned_without_a_pid_file_finds_our_slskd_by_its_path(tmp_path, fake_os):
    """An orphan from a build before the pid file existed is still ours."""
    fake = fake_os({5: "/usr/sbin/somethingd", 6: ours(tmp_path), 7: "/other/slskd"})
    clock = Clock()

    assert slskd_process.stop_owned(tmp_path, clock=clock, sleep=clock.sleep) is True
    assert fake.sent == [(6, "term")]


def test_stop_owned_escalates_to_kill_after_the_grace(tmp_path, fake_os):
    fake = fake_os({777: ours(tmp_path)}, survives_term=True)
    slskd_process.record_pid(tmp_path, 777)
    clock = Clock()

    assert slskd_process.stop_owned(tmp_path, clock=clock, sleep=clock.sleep) is True

    assert fake.sent == [(777, "term"), (777, "kill")]
    assert clock.t <= slskd_process.TERM_GRACE_S + slskd_process._SIGKILL_GRACE_S + 1


def test_stop_owned_is_idempotent(tmp_path, fake_os):
    fake = fake_os({777: ours(tmp_path)})
    slskd_process.record_pid(tmp_path, 777)
    clock = Clock()

    assert slskd_process.stop_owned(tmp_path, clock=clock, sleep=clock.sleep) is True
    assert slskd_process.stop_owned(tmp_path, clock=clock, sleep=clock.sleep) is False
    assert fake.sent == [(777, "term")]


@respx.mock
async def test_an_adopted_slskd_is_owned_and_stopped_at_quit(tmp_path, fake_os, monkeypatch):
    """The bug: the next launch adopted the slskd left running, and then never stopped it either."""
    respx.get(f"{BASE}/application").mock(return_value=httpx.Response(200, json=APPLICATION))
    fake = fake_os({901: ours(tmp_path)})

    async def unexpected_spawn(*args, **kwargs):
        raise AssertionError("an answering slskd of ours is adopted, not doubled")

    monkeypatch.setattr(slskd_process.asyncio, "create_subprocess_exec", unexpected_spawn)
    clock = Clock()
    proc = SlskdProcess(tmp_path, "http://slskd.test", "key", clock=clock, sleep=clock.asleep)

    await proc.start()
    assert proc.owned_pid == 901
    assert slskd_process.read_pid(tmp_path) == 901

    await proc.stop()
    assert fake.sent == [(901, "term")]
    assert proc.owned_pid is None and slskd_process.read_pid(tmp_path) is None


@respx.mock
async def test_a_foreign_slskd_answering_the_port_is_used_but_never_stopped(tmp_path, fake_os):
    respx.get(f"{BASE}/application").mock(return_value=httpx.Response(200, json=APPLICATION))
    fake = fake_os({55: "/opt/homebrew/bin/slskd"})  # the owner's own slskd, started by hand
    proc = SlskdProcess(tmp_path, "http://slskd.test", "key")

    await proc.start()
    await proc.stop()

    assert proc.owned_pid is None and fake.sent == []


async def test_a_stale_slskd_of_ours_that_does_not_answer_is_replaced(tmp_path, fake_os, monkeypatch):
    """Ours, but refusing the current key (or hung): stop it before spawning, or the new one can't bind."""
    fake = fake_os({901: ours(tmp_path)})
    slskd_process.record_pid(tmp_path, 901)
    answers = iter(["HTTP 401: refused", None])

    async def check(self, client=None):
        return next(answers)

    class Spawned:
        pid, returncode = 902, None

    async def spawn(*args, **kwargs):
        fake.exe[902] = ours(tmp_path)
        return Spawned()

    monkeypatch.setattr(SlskdProcess, "_check", check)
    monkeypatch.setattr(slskd_process, "is_installed", lambda _: True)
    monkeypatch.setattr(slskd_process, "binary_problem", lambda _: None)
    monkeypatch.setattr(slskd_process.asyncio, "create_subprocess_exec", spawn)
    clock = Clock()
    proc = SlskdProcess(tmp_path, "http://slskd.test", "key", clock=clock, sleep=clock.asleep)

    await proc.start()

    assert fake.sent == [(901, "term")]
    assert slskd_process.read_pid(tmp_path) == 902  # the new one is recorded for the next quit


def test_atexit_and_signal_guards_stop_slskd(tmp_path, monkeypatch):
    registered, stopped = [], []
    monkeypatch.setattr(atexit, "register", lambda fn, *a, **kw: registered.append((fn, a)))
    monkeypatch.setattr(slskd_process, "stop_owned", lambda data_dir, **kw: stopped.append(data_dir))
    monkeypatch.setattr(slskd_process, "_guarded", set())
    previous = []
    monkeypatch.setattr(signal, "getsignal", lambda s: lambda signum, frame: previous.append(signum))
    handlers = {}
    monkeypatch.setattr(signal, "signal", lambda s, h: handlers.__setitem__(s, h))

    slskd_process.install_exit_guards(tmp_path)
    slskd_process.install_exit_guards(tmp_path)  # a second call adds nothing

    assert len(registered) == 1
    fn, args = registered[0]
    fn(*args)
    assert stopped == [tmp_path]

    handlers[signal.SIGTERM](signal.SIGTERM, None)
    assert stopped == [tmp_path, tmp_path]
    assert previous == [signal.SIGTERM]  # the app's own handling still follows


def test_signal_guards_are_skipped_off_the_main_thread(tmp_path, monkeypatch):
    monkeypatch.setattr(atexit, "register", lambda *a, **kw: None)
    monkeypatch.setattr(slskd_process, "_guarded", set())
    installed = []
    monkeypatch.setattr(signal, "signal", lambda s, h: installed.append(s))

    t = threading.Thread(target=lambda: slskd_process.install_exit_guards(tmp_path))
    t.start()
    t.join()

    assert installed == []


def test_a_real_foreign_process_in_the_pid_file_survives(tmp_path):
    """No fakes: the real platform checks must refuse a pid that is not our slskd."""
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        slskd_process.record_pid(tmp_path, child.pid)
        assert slskd_process.exe_of(child.pid) is not None  # it is alive and inspectable
        assert slskd_process.stop_owned(tmp_path) is False
        time.sleep(0.2)
        assert child.poll() is None
    finally:
        child.kill()
        child.wait()


def test_a_real_copy_of_our_binary_is_stopped(tmp_path):
    """No fakes: a process running from the bundled slskd's path is found and ended."""
    import shutil
    sleep_bin = shutil.which("sleep")
    if sys.platform == "win32" or sleep_bin is None:
        pytest.skip("needs a standalone POSIX `sleep` binary to stand in for slskd")
    target = binary_path(tmp_path)
    target.parent.mkdir(parents=True)
    shutil.copy(sleep_bin, target)
    if sys.platform == "darwin":  # a copied system binary is killed on launch until it is re-signed
        subprocess.run(["codesign", "-s", "-", "-f", str(target)], check=True, capture_output=True)
    child = subprocess.Popen([str(target), "30"])
    try:
        deadline = time.monotonic() + 5
        while slskd_process.exe_of(child.pid) is None and time.monotonic() < deadline:
            time.sleep(0.05)
        if child.poll() is not None:
            pytest.skip("this machine refused to run the copied stand-in binary")
        assert slskd_process.is_ours(child.pid, tmp_path), slskd_process.exe_of(child.pid)
        slskd_process.record_pid(tmp_path, child.pid)
        assert slskd_process.stop_owned(tmp_path) is True
        assert child.wait(timeout=5) is not None
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()
