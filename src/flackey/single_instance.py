"""One Flackey per Windows user: a second launch hands over to the first instead of starting again.

Kept apart from `desktop` and free of flackey imports so the packaged app can check before it loads
anything heavy -- on an Arm PC emulating x64 those imports take minutes, and a second double-click
meanwhile would otherwise sit through all of them just to find out it isn't needed.
"""

from __future__ import annotations

import os
import sys

WINDOW_TITLE = "Flackey"  # the main window's title, which `desktop.APP_NAME` also is

SINGLE_INSTANCE_MUTEX = "FlackeySingleInstance"  # per logon session: Windows' default namespace for a name
_ERROR_ALREADY_EXISTS = 183
_SW_RESTORE = 9
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_held: dict[str, int] = {}  # name -> mutex handle, held for the life of the process; Windows lets go at exit


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
