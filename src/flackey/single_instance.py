"""One Flackey per Windows user, and one per data folder everywhere: a second launch hands over to the
first instead of starting again.

Kept apart from `desktop` and free of flackey imports so the packaged app can check before it loads
anything heavy -- on an Arm PC emulating x64 those imports take minutes, and a second double-click
meanwhile would otherwise sit through all of them just to find out it isn't needed.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import IO

WINDOW_TITLE = "Flackey"  # the main window's title, which `desktop.APP_NAME` also is

SINGLE_INSTANCE_MUTEX = "FlackeySingleInstance"  # per logon session: Windows' default namespace for a name
_ERROR_ALREADY_EXISTS = 183
_SW_RESTORE = 9
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_held: dict[str, int] = {}  # name -> mutex handle, held for the life of the process; Windows lets go at exit
LOCK_FILE_NAME = "flackey.lock"
# data dir -> the open lock file. Module scope so the lock lives as long as the process: a file object
# that was garbage-collected would close and quietly let go of it.
_dir_locks: dict[str, IO[bytes]] = {}
_NS_ACTIVATE_IGNORING_OTHER_APPS = 1 << 1


def claim_single_instance(name: str = SINGLE_INSTANCE_MUTEX) -> bool:
    """True when this is the only Flackey running for this user, and from now on it is the one.

    macOS never starts an app twice, so this is Windows only. There each double-click on the shortcut is a
    new process, and on a slow first launch -- no window for ten seconds or more on an Arm PC emulating
    x64 -- people click again. The copies then open the same database and port at once: one dies on
    "database is locked" in an error box, another on the port. A named mutex is how Windows programs
    tell: the first process creates it, later ones find it already there. True as well when the mutex
    cannot be made at all, since starting is better than refusing to start.

    Claiming again from the process that holds it is True, not a refusal: the packaged app claims first
    thing, before the imports that take minutes on a slow PC, and `run_in_window` checks again."""
    if sys.platform != "win32":
        return True
    if name in _held:
        return True
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    handle = kernel32.CreateMutexW(None, False, name)
    if not handle:
        return True
    if ctypes.get_last_error() == _ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(handle)
        return False
    _held[name] = handle
    return True


def focus_running_window(title: str = WINDOW_TITLE) -> bool:
    """Bring the running Flackey's window to the front, restoring it if minimized. Matched on the title
    *and* on the program behind it, so a File Explorer window open on a folder called Flackey is left
    alone. False when there is no window yet (the other copy is still starting, and will show one) or
    off Windows."""
    if sys.platform != "win32":
        return False
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32")
    kernel32 = ctypes.WinDLL("kernel32")
    kernel32.OpenProcess.restype = wintypes.HANDLE
    ours = os.path.normcase(os.path.abspath(sys.executable))
    found: list[int] = []

    def image_of(hwnd: int) -> str:
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(pid))
        process = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
        if not process:
            return ""
        try:
            buf = ctypes.create_unicode_buffer(32768)
            size = wintypes.DWORD(len(buf))
            ok = kernel32.QueryFullProcessImageNameW(process, 0, buf, ctypes.byref(size))
            return os.path.normcase(buf.value) if ok else ""
        finally:
            kernel32.CloseHandle(process)

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visit(hwnd, _lparam):
        text = ctypes.create_unicode_buffer(256)
        user32.GetWindowTextW(hwnd, text, len(text))
        if text.value == title and user32.IsWindowVisible(hwnd) and image_of(hwnd) == ours:
            found.append(hwnd)
            return False  # stop enumerating
        return True

    user32.EnumWindows(visit, 0)
    if not found:
        return False
    hwnd = wintypes.HWND(found[0])
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, _SW_RESTORE)  # only when minimized: on a maximized one it would un-maximize
    return bool(user32.SetForegroundWindow(hwnd))


class DataDirInUse(RuntimeError):
    """Another Flackey process already runs on this data folder."""


def claim_data_dir(data_dir: Path) -> bool:
    """True when no other Flackey process is using `data_dir`, and from now on this one is.

    The named mutex above is Windows only and per user; nothing stopped a second Mac copy -- a stray
    local build opened by Spotlight beside the installed app, or `flackey start` in a terminal -- from
    starting on the same folder. That copy then failed to bind the port and, on its way out, its exit
    guard stopped the slskd it found by pid file: the *first* copy's, which kept running with Soulseek
    dead. An exclusive lock on a file in the data folder is what tells the two apart: the first process
    takes it, later ones find it held. The kernel lets go when the holder exits, crash or not, so there is
    no stale lock to clean up.

    Idempotent within a process: the packaged app claims in `launch.py` and `run_in_window` claims again.
    True as well when the lock file cannot be made at all (a read-only folder), since starting is better
    than refusing to start. The holder writes its pid into the file so a second copy can bring it forward
    (`holder_pid`)."""
    key = os.path.abspath(data_dir)
    if key in _dir_locks:
        return True
    try:
        Path(key).mkdir(parents=True, exist_ok=True)
        f = open(os.path.join(key, LOCK_FILE_NAME), "a+b")  # noqa: SIM115 -- held for the life of the process
    except OSError:
        return True
    try:
        _lock_file(f)
    except OSError:
        f.close()
        return False
    if sys.platform != "win32":  # a locked byte cannot be read by anyone else on Windows; nobody needs it there
        try:
            f.seek(0)
            f.truncate()
            f.write(str(os.getpid()).encode())
            f.flush()
        except OSError:
            pass
    _dir_locks[key] = f
    return True


def release_data_dir(data_dir: Path) -> None:
    """Let go of the lock taken by `claim_data_dir`. For tests; the app keeps it until it exits."""
    f = _dir_locks.pop(os.path.abspath(data_dir), None)
    if f is not None:
        f.close()


def _lock_file(f: IO[bytes]) -> None:
    """Non-blocking exclusive lock on `f`; OSError when another open file holds it."""
    if sys.platform == "win32":
        import msvcrt

        f.seek(0)
        msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl

        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def holder_pid(data_dir: Path) -> int | None:
    """The pid the lock holder wrote, or None when there is none to read."""
    try:
        text = (Path(data_dir) / LOCK_FILE_NAME).read_text().strip()
    except (OSError, UnicodeDecodeError):
        return None
    return int(text) if text.isdigit() and int(text) > 1 else None


def focus_process(pid: int | None) -> bool:
    """Bring the app running as `pid` to the front on macOS. False when there is no such app (it is still
    starting, or it is a headless `flackey start`), off macOS, or without AppKit."""
    if pid is None or sys.platform != "darwin":
        return False
    try:
        import AppKit
    except ImportError:
        return False
    app = AppKit.NSRunningApplication.runningApplicationWithProcessIdentifier_(pid)
    if app is None:
        return False
    return bool(app.activateWithOptions_(_NS_ACTIVATE_IGNORING_OTHER_APPS))


def hand_over(data_dir: Path) -> bool:
    """A second launch's way out: bring the copy that is running forward. True when a window came forward."""
    return focus_running_window() or focus_process(holder_pid(data_dir))
