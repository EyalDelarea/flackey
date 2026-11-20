"""Tell the owner why Flackey closed when it could not start.

A windowed app that dies before its window exists dies silently: the Dock icon bounces and stops, or on
Windows nothing appears at all, and the only trace is a log file the owner does not know about. This
module turns those exits into one native message box saying what happened and what to do.

Kept free of flackey imports and heavy dependencies, like `single_instance`, so the packaged entry point
can still use it when importing the rest of the app is the thing that failed.
"""
from __future__ import annotations

import errno
import logging
import socket
import subprocess
import sys
from pathlib import Path

log = logging.getLogger(__name__)

TITLE = "Flackey"
_MB_OK, _MB_ICONERROR = 0x0, 0x10
_UNAVAILABLE = {errno.EADDRINUSE, errno.EACCES, getattr(errno, "WSAEADDRINUSE", 10048),
                getattr(errno, "WSAEACCES", 10013)}
# The text arrives as an argument rather than spliced into the script, so a path with a quote in it
# cannot break the AppleScript.
_ALERT_SCRIPT = ('on run argv\n'
                 'display alert (item 1 of argv) message (item 2 of argv) as critical buttons {"OK"} '
                 'default button 1\n'
                 'end run')


def port_in_use(host: str, port: int) -> bool:
    """Whether something else is listening on `host:port`, asked by trying to bind it the way uvicorn
    does. SO_REUSEADDR on POSIX, as uvicorn sets it, so a socket of our own still in TIME_WAIT from a
    relaunch is not mistaken for another program. Not on Windows, where that option lets a bind steal a
    port someone else holds, and the probe would answer "free" for a taken one."""
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    try:
        sock = socket.socket(family, socket.SOCK_STREAM)
    except OSError:
        return False
    try:
        if sys.platform != "win32":
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, port))
    except OSError as exc:
        # EACCES as well: Windows answers that for a port inside a range Hyper-V or WSL has reserved,
        # which is just as unavailable to Flackey as one another program holds.
        return exc.errno in _UNAVAILABLE
    finally:
        sock.close()
    return False


def startup_failure_text(*, port_busy: bool, timed_out: bool, port: int, log_path: Path) -> tuple[str, str]:
    """The headline and the explanation for a start that failed. The port is checked first because it
    is the one cause the owner can do something about on the spot."""
    where = f"Details are in the log at {log_path}"
    if port_busy:
        return ("Flackey can't start because another program is using its port.",
                (f"Another program on this computer is already using port {port}, which Flackey needs.\n\n"
                 f"Quit that program, or restart your computer, then open Flackey again.\n\n{where}"))
    again = f"Open Flackey again. If this keeps happening, restart your computer.\n\n{where}"
    if timed_out:
        return "Flackey took too long to start.", again
    return "Flackey ran into a problem while starting and had to close.", again


def show_alert(headline: str, detail: str) -> bool:
    """A native message box, waiting for OK. Never raises: this runs on the way out of a failed start,
    and a second failure here must not replace the first in the log. False when nothing could be shown
    (no GUI on this platform, or the call failed)."""
    try:
        if sys.platform == "darwin":
            result = subprocess.run(["osascript", "-e", _ALERT_SCRIPT, headline, detail],
                                    capture_output=True, text=True, check=False)
            return result.returncode == 0
        if sys.platform == "win32":
            import ctypes

            ctypes.windll.user32.MessageBoxW(None, f"{headline}\n\n{detail}", TITLE, _MB_OK | _MB_ICONERROR)
            return True
    except Exception:
        log.warning("could not show the startup error", exc_info=True)
    return False
