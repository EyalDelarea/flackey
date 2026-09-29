"""Entry point for the packaged app, on the Mac and on Windows.

Deliberately not a second way of starting Flackey: it does what `flackey start` does, in the same order,
and then calls the same `run_in_window`. Logging is configured first because a windowed bundle has no
console, so the log file in the data dir is the only place a traceback can go.
"""

from __future__ import annotations

import logging
import multiprocessing
import os
import sys

from flackey.config import load_settings
from flackey.logsetup import configure_logging


def ensure_std_streams() -> None:
    """Give a windowed Windows build somewhere to write.

    PyInstaller's `console=False` Windows executable is a GUI-subsystem program, and Python starts one
    with `sys.stdout` and `sys.stderr` set to None rather than to a stream. Anything that touches them
    then fails on the attribute rather than on the write: uvicorn's log formatter calls
    `sys.stderr.isatty()` while it is being set up, which kills the server thread before the window
    has a page to show, and `configure_logging`'s console handler would capture None as its stream.
    `os.devnull` is what a console-less process should write to -- the log file in the data dir is
    where everything worth keeping already goes.

    Must run before `configure_logging`, because `logging.StreamHandler()` captures `sys.stderr` when it
    is constructed. A no-op everywhere a stream exists, which is every Mac launch and every terminal."""
    for name in ("stdout", "stderr"):
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))  # noqa: SIM115 - lives as long as the process


def main() -> None:
    ensure_std_streams()
    settings = load_settings()
    configure_logging(settings.data_dir)
    log = logging.getLogger("flackey.launch")
    try:
        from flackey.desktop import run_in_window

        run_in_window(settings)
    except Exception:
        # Nothing above this catches, and nothing below prints: without this the app would close on
        # startup with no window, no message and an empty log.
        log.exception("the app failed to start")
        raise


if __name__ == "__main__":
    # PyInstaller re-executes the bundle to make a child process. Without this a child re-enters this
    # module and opens a second window instead of doing the work it was spawned for.
    multiprocessing.freeze_support()
    sys.exit(main())
