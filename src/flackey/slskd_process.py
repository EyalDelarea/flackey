"""Supervise the slskd sidecar process: start it, wait for it to become healthy, stop it.

`slskd_config.py` owns the config file and API key; this module owns the process only — it takes
the URL and API key as plain constructor arguments and never reads slskd.yml itself.

On Windows the same code runs with two differences worth knowing. The asyncio subprocess needs the
Proactor event loop, which is what `asyncio.run` gives a Windows thread by default and what both entry
points use (`desktop.start_server_thread` and `cli.start`); uvicorn never gets to choose, because the
app awaits `Server.serve()` inside that loop and only `Server.run()` consults uvicorn's loop factory.
And `terminate()` is TerminateProcess, a hard kill rather than SIGTERM -- see `stop`.

Flackey owns the slskd it runs for the whole of its life, and that slskd must not outlive the app. The
pid goes in a file in slskd's app dir, so every way out -- the window closing, Cmd+Q, `atexit`, SIGTERM,
the server's own `finally` -- can stop it through `stop_owned` without a handle on the `SlskdProcess`
that started it, and so the next launch can adopt (and then own) one an earlier run left behind. Before
any signal the pid's executable is checked against the bundled binary's path: a pid reused by another
program, or a slskd the owner runs by hand, is never touched.
"""
from __future__ import annotations

import asyncio
import atexit
import ctypes
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

import httpx

from .slskd_binary import SlskdBinaryError, binary_path, install_dir, is_installed

log = logging.getLogger(__name__)


class SlskdStartTimeout(SlskdBinaryError):
    """slskd is installed and was launched but never answered in time: slow, not missing."""

_POLL_INTERVAL_S = 0.2
_WAIT_REQUEST_TIMEOUT_S = 5.0
# How long a freshly spawned slskd gets to answer. A warm start on a Mac takes a few seconds, but a slow PC
# is another matter: in the Windows test VM (x64 slskd emulated on ARM, on a fresh install busy updating
# and scanning) Windows took up to 90 s to launch the new slskd.exe and slskd another minute to finish
# starting. Only a slskd that never starts ever waits this long.
SLSKD_START_TIMEOUT_S = 180.0
_SIGKILL_GRACE_S = 5.0
# How long a slskd gets to shut down on SIGTERM when the app is quitting, before SIGKILL. Short: a quit
# blocks on it, and slskd's state is in SQLite, which survives an abrupt stop.
TERM_GRACE_S = 4.0
_EXIT_KILL_GRACE_S = 2.0
PID_FILE_NAME = "flackey-slskd.pid"
_CREATE_NO_WINDOW = 0x08000000  # subprocess.CREATE_NO_WINDOW, which only a Windows build defines


def _no_window() -> dict:
    """A copy of `tools.no_window`, which this module sits below in the import layers and may not
    import: on Windows, start slskd without the console window a GUI app's console child otherwise
    gets (slskd is a console program, and that window would sit open for as long as the app runs).
    Empty elsewhere, where POSIX `Popen` rejects any `creationflags`."""
    if sys.platform == "win32":
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", _CREATE_NO_WINDOW)}
    return {}


# --- Which process is ours -------------------------------------------------------------------------------
# No psutil: it is not a dependency, and each platform answers "what executable is pid N" in a few lines.
# `exe_of` returning None means dead, a zombie, or not inspectable -- all of which mean "do not signal".

def _exe_of_darwin(pid: int) -> str | None:
    buf = ctypes.create_string_buffer(4096)
    try:
        n = ctypes.CDLL("/usr/lib/libproc.dylib").proc_pidpath(pid, buf, len(buf))
    except OSError:
        return None
    return os.fsdecode(buf.value) if n > 0 else None


def _pids_darwin() -> list[int]:
    lib = ctypes.CDLL("/usr/lib/libproc.dylib")
    n = lib.proc_listallpids(None, 0)
    if n <= 0:
        return []
    arr = (ctypes.c_int * (n + 64))()
    got = lib.proc_listallpids(arr, ctypes.sizeof(arr))
    return [p for p in arr[:max(got, 0)] if p > 0]


def _exe_of_linux(pid: int) -> str | None:
    try:
        return os.readlink(f"/proc/{pid}/exe")
    except OSError:
        return None


def _pids_linux() -> list[int]:
    try:
        return [int(d) for d in os.listdir("/proc") if d.isdigit()]
    except OSError:
        return []


_STILL_ACTIVE = 259
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def _kernel32():
    from ctypes import wintypes
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.OpenProcess.restype = wintypes.HANDLE
    k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    k.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    k.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                             ctypes.POINTER(wintypes.DWORD)]
    return k


def _exe_of_windows(pid: int) -> str | None:
    """Never `os.kill(pid, 0)` here: on Windows that is TerminateProcess with exit code 0."""
    from ctypes import wintypes
    k = _kernel32()
    handle = k.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        code = wintypes.DWORD()
        if not k.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value != _STILL_ACTIVE:
            return None
        buf = ctypes.create_unicode_buffer(32768)
        size = wintypes.DWORD(len(buf))
        if not k.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return None
        return buf.value
    finally:
        k.CloseHandle(handle)


def _pids_windows() -> list[int]:
    from ctypes import wintypes
    psapi = ctypes.WinDLL("kernel32")
    count = 4096
    while True:
        arr = (wintypes.DWORD * count)()
        needed = wintypes.DWORD()
        if not psapi.K32EnumProcesses(arr, ctypes.sizeof(arr), ctypes.byref(needed)):
            return []
        if needed.value < ctypes.sizeof(arr):
            return [p for p in arr[:needed.value // ctypes.sizeof(wintypes.DWORD)] if p]
        count *= 2


def exe_of(pid: int) -> str | None:
    """The executable a live process runs, or None when it is gone or cannot be inspected."""
    if pid <= 0:
        return None
    try:
        if sys.platform == "win32":
            return _exe_of_windows(pid)
        if sys.platform == "darwin":
            return _exe_of_darwin(pid)
        return _exe_of_linux(pid)
    except Exception:  # an inspection failure means "don't touch it", never a crash at quit
        log.debug("could not inspect pid %s", pid, exc_info=True)
        return None


def list_pids() -> list[int]:
    try:
        if sys.platform == "win32":
            return _pids_windows()
        if sys.platform == "darwin":
            return _pids_darwin()
        return _pids_linux()
    except Exception:
        log.debug("could not list processes", exc_info=True)
        return []


def _same_path(a: str | Path, b: str | Path) -> bool:
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))


def is_ours(pid: int, data_dir: Path) -> bool:
    """True only for a live process running this data dir's bundled slskd binary."""
    exe = exe_of(pid)
    return exe is not None and _same_path(exe, binary_path(data_dir))


def pid_file(data_dir: Path) -> Path:
    return data_dir / "slskd" / PID_FILE_NAME


def read_pid(data_dir: Path) -> int | None:
    try:
        return int(pid_file(data_dir).read_text().strip())
    except (OSError, ValueError):
        return None


def record_pid(data_dir: Path, pid: int) -> None:
    path = pid_file(data_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{pid}\n")
    except OSError:
        log.warning("could not record slskd's pid in %s", path, exc_info=True)


def clear_pid(data_dir: Path, pid: int | None = None) -> None:
    """Remove the pid file -- only if it still names `pid`, when one is given, so a stop of an old copy
    never forgets the new one."""
    if pid is not None and read_pid(data_dir) != pid:
        return
    try:
        pid_file(data_dir).unlink(missing_ok=True)
    except OSError:
        pass


def find_ours(data_dir: Path) -> int | None:
    """The pid of a running slskd of ours: the one in the pid file if it checks out, else one found by
    its executable path (a copy left by a build that wrote no pid file)."""
    pid = read_pid(data_dir)
    if pid is not None and is_ours(pid, data_dir):
        return pid
    own = os.getpid()
    for candidate in list_pids():
        if candidate != own and is_ours(candidate, data_dir):
            return candidate
    return None


def _terminate(pid: int) -> None:
    """SIGTERM; on Windows `os.kill` is TerminateProcess, a hard stop (see `SlskdProcess.stop`)."""
    os.kill(pid, signal.SIGTERM)


def _kill(pid: int) -> None:
    os.kill(pid, getattr(signal, "SIGKILL", signal.SIGTERM))


def _send(fn: Callable[[int], None], pid: int, data_dir: Path) -> bool:
    """Signal `pid` only if it is still our slskd at this very moment. False when it is not."""
    if not is_ours(pid, data_dir):
        return False
    try:
        fn(pid)
    except (ProcessLookupError, PermissionError, OSError):
        return False
    return True


_lock = threading.RLock()


def stop_owned(data_dir: Path, *, term_grace_s: float = TERM_GRACE_S,
               kill_grace_s: float = _EXIT_KILL_GRACE_S,
               clock: Callable[[], float] = time.monotonic,
               sleep: Callable[[float], None] = time.sleep) -> bool:
    """Stop our slskd from any thread, with no event loop: SIGTERM, then SIGKILL after `term_grace_s`.
    Bounded, idempotent, and never signals a process that is not this data dir's bundled slskd. True
    when there was one of ours to stop."""
    with _lock:
        pid = find_ours(data_dir)
        if pid is None:
            clear_pid(data_dir)
            return False
        log.info("stopping slskd (pid %s)", pid)
        if _send(_terminate, pid, data_dir):
            deadline = clock() + term_grace_s
            while is_ours(pid, data_dir) and clock() < deadline:
                sleep(_POLL_INTERVAL_S)
            if _send(_kill, pid, data_dir):
                log.warning("slskd ignored SIGTERM for %.0f s; killed it", term_grace_s)
                deadline = clock() + kill_grace_s
                while is_ours(pid, data_dir) and clock() < deadline:
                    sleep(_POLL_INTERVAL_S)
        clear_pid(data_dir, pid)
        return True


_guarded: set[tuple[Path, int | None]] = set()  # (data dir, signal), with None for `atexit`


def install_exit_guards(data_dir: Path, signals: tuple[int, ...] = (signal.SIGTERM, signal.SIGINT)) -> None:
    """Stop our slskd on interpreter exit and on SIGTERM/SIGINT, then let the app's own handling of
    the signal carry on. Signal handlers can only be set from the main thread, so elsewhere only the
    `atexit` guard is installed. Safe to call more than once."""
    if (data_dir, None) not in _guarded:
        _guarded.add((data_dir, None))
        atexit.register(stop_owned, data_dir)
    if threading.current_thread() is not threading.main_thread():
        return
    for signum in signals:
        if (data_dir, signum) in _guarded:
            continue
        _guarded.add((data_dir, signum))
        previous = signal.getsignal(signum)

        def handler(sig, frame, previous=previous):
            try:
                stop_owned(data_dir)
            finally:
                if callable(previous):
                    previous(sig, frame)
                elif previous != signal.SIG_IGN:
                    signal.signal(sig, signal.SIG_DFL)
                    os.kill(os.getpid(), sig)

        try:
            signal.signal(signum, handler)
        except (ValueError, OSError):
            log.debug("could not guard signal %s", signum, exc_info=True)


class SlskdProcess:
    """Starts, health-checks, and stops the slskd sidecar.

    `clock` and `sleep` are injectable so tests never perform a real wait: `clock` is a zero-arg
    callable returning a monotonic float, `sleep` is an async callable taking seconds.
    """

    def __init__(
        self,
        data_dir: Path,
        url: str,
        api_key: str,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], asyncio.Future[None]] = asyncio.sleep,
    ) -> None:
        self._data_dir = data_dir
        self._url = url.rstrip("/")
        self._api_key = api_key
        self._clock = clock
        self._sleep = sleep
        self._proc: asyncio.subprocess.Process | None = None
        # The pid of an already-running slskd of ours that `start` adopted rather than spawned. Kept apart
        # from `_proc` because `running` keeps its "I spawned a live child" meaning.
        self.owned_pid: int | None = None

    @property
    def running(self) -> bool:
        """True when this instance owns a live subprocess. False for a slskd started by hand or by
        another instance — `stop()` only ever signals a process this instance spawned."""
        return self._proc is not None and self._proc.returncode is None

    async def start(self, *, timeout_s: float = SLSKD_START_TIMEOUT_S) -> None:
        """Launch slskd and wait for it to become healthy.

        If something already answers health checks on `url` this does not start a second one. When
        that something is our own bundled slskd (left by an earlier run that did not get to stop it),
        it is adopted and owned: `stop` ends it like one this instance spawned. Anything else -- the
        owner's own slskd -- is used but never stopped.
        """
        if self.running or (self.owned_pid is not None and is_ours(self.owned_pid, self._data_dir)):
            return
        failure = await self._check()
        if failure is None:
            pid = find_ours(self._data_dir)
            if pid is not None:
                self.owned_pid = pid
                record_pid(self._data_dir, pid)
                log.info("slskd already answering on %s; adopted our copy (pid %s)", self._url, pid)
            else:
                log.info("slskd already answering on %s and it is not ours; using it, never stopping it",
                         self._url)
            return
        if "API key" in failure:
            log.warning("something on %s refuses flackey's API key; starting slskd anyway (%s)",
                        self._url, failure)
        stale = find_ours(self._data_dir)
        if stale is not None:
            # Ours, but not answering under the current key: replace it, or the new one cannot bind.
            log.info("replacing a slskd of ours that does not answer (pid %s)", stale)
            await self._stop_pid(stale, timeout_s=_SIGKILL_GRACE_S)

        if not is_installed(self._data_dir):
            raise SlskdBinaryError("slskd is not installed; run the setup install step first")

        app_dir = self._data_dir / "slskd"
        app_dir.mkdir(parents=True, exist_ok=True)
        log_path = self._data_dir / "slskd.log"
        log_file = log_path.open("ab")
        try:
            self._proc = await asyncio.create_subprocess_exec(
                str(binary_path(self._data_dir)),
                "--app-dir",
                str(app_dir),
                cwd=str(install_dir(self._data_dir)),
                stdout=log_file,
                stderr=log_file,
                **_no_window(),
            )
        finally:
            log_file.close()
        if isinstance(getattr(self._proc, "pid", None), int):
            record_pid(self._data_dir, self._proc.pid)

        healthy = await self.wait_healthy(timeout_s=timeout_s)
        if not healthy:
            # Never leave an orphan behind: if it didn't come up healthy, tear it down before raising.
            await self.stop()
            raise SlskdStartTimeout("slskd did not become healthy within the timeout")

    async def probe(self, client: httpx.AsyncClient | None = None) -> bool:
        """A single health check, no polling. True when `GET .../application` answers 200 with slskd's
        own application state, under our API key. Used to detect an already-running slskd without
        waiting for one."""
        return await self._check(client) is None

    async def _check(self, client: httpx.AsyncClient | None = None) -> str | None:
        """One health check: None when slskd answered, else why not (for the log).

        Only a 200 carrying slskd's application state counts. Another program on the port, or a slskd
        that refuses our key, is not a sidecar flackey can use, and taking one for it would hand the
        searches and credentials to whatever answered."""
        try:
            if client is None:
                async with httpx.AsyncClient(timeout=2.0) as own:
                    resp = await self._get_application(own)
            else:
                resp = await self._get_application(client)
        except httpx.HTTPError as exc:
            return f"{type(exc).__name__}: {exc}"
        if resp.status_code in (401, 403):
            return f"HTTP {resp.status_code}: something on that port refused flackey's API key"
        if resp.status_code != 200:
            return f"HTTP {resp.status_code}"
        try:
            body = resp.json()
        except ValueError:
            return "HTTP 200 but not JSON: not slskd"
        if not isinstance(body, dict) or not isinstance(body.get("server"), dict):
            return "HTTP 200 but not slskd's application state"
        return None

    async def _get_application(self, client: httpx.AsyncClient) -> httpx.Response:
        return await client.get(f"{self._url}/api/v0/application", headers={"X-API-Key": self._api_key})

    async def wait_healthy(self, *, timeout_s: float = 30) -> bool:
        """Poll `GET <url>/api/v0/application` until it answers or the timeout expires. Returns a
        bool; never raises on an unhealthy or unreachable sidecar.

        One client serves every poll: building an httpx client sets up TLS, which cost 130 ms a probe on
        a Windows CI runner and far more on a slow PC busy starting slskd itself. Each request gets
        `_WAIT_REQUEST_TIMEOUT_S`, since a slskd just starting can be slow to answer its first one."""
        deadline = self._clock() + timeout_s
        last = "no answer"
        async with httpx.AsyncClient(timeout=_WAIT_REQUEST_TIMEOUT_S) as client:
            while True:
                failure = await self._check(client)
                if failure is None:
                    return True
                last = failure
                if self._clock() >= deadline:
                    log.warning("slskd gave no healthy answer in %.0f s; last try: %s", timeout_s, last)
                    return False
                await self._sleep(_POLL_INTERVAL_S)

    async def stop(self, *, timeout_s: float = 10) -> None:
        """Send SIGTERM, wait, then SIGKILL if it must. Safe to call when nothing is running, and
        safe to call twice.

        On Windows there is no SIGTERM to send: asyncio's `terminate()` is TerminateProcess, which ends
        slskd on the spot with no shutdown of its own. That is accepted rather than worked around. slskd
        keeps its state in SQLite, which survives an abrupt stop, and a transfer cut off mid-file is one
        this app already treats as failed and retries; the alternative (a console control event) needs
        slskd to share a console with this process, which the windowed build deliberately does not have.
        The wait below then returns on the first poll, and the `kill()` fallback is never reached.

        An adopted slskd (`owned_pid`) is stopped the same way, by pid, once its executable checks out."""
        adopted, self.owned_pid = self.owned_pid, None
        if adopted is not None:
            await self._stop_pid(adopted, timeout_s=timeout_s)
        proc, self._proc = self._proc, None
        if proc is None or proc.returncode is not None:
            if proc is not None:
                clear_pid(self._data_dir, getattr(proc, "pid", None))
            return
        try:
            await self._stop_proc(proc, timeout_s)
        finally:
            clear_pid(self._data_dir, getattr(proc, "pid", None))

    async def _stop_pid(self, pid: int, *, timeout_s: float) -> None:
        """Stop a slskd of ours this instance did not spawn: by pid, re-checking before every signal."""
        if _send(_terminate, pid, self._data_dir):
            deadline = self._clock() + timeout_s
            while is_ours(pid, self._data_dir) and self._clock() < deadline:
                await self._sleep(_POLL_INTERVAL_S)
            if _send(_kill, pid, self._data_dir):
                deadline = self._clock() + _SIGKILL_GRACE_S
                while is_ours(pid, self._data_dir) and self._clock() < deadline:
                    await self._sleep(_POLL_INTERVAL_S)
        clear_pid(self._data_dir, pid)

    async def _stop_proc(self, proc: asyncio.subprocess.Process, timeout_s: float) -> None:

        try:
            proc.terminate()
        except ProcessLookupError:
            return

        # Polling `proc.returncode` (rather than awaiting `proc.wait()`) after each `await self._sleep`
        # lets a real event loop's child watcher update it in the background while keeping the wait
        # itself on the injectable clock/sleep, so tests never perform a real wait.
        deadline = self._clock() + timeout_s
        while proc.returncode is None and self._clock() < deadline:
            await self._sleep(_POLL_INTERVAL_S)

        if proc.returncode is None:
            try:
                proc.kill()
            except ProcessLookupError:
                return
            deadline = self._clock() + _SIGKILL_GRACE_S
            while proc.returncode is None and self._clock() < deadline:
                await self._sleep(_POLL_INTERVAL_S)
