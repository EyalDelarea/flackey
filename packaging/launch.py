"""Entry point for the packaged Mac app.

Deliberately not a second way of starting Flackey: it does what `crate start` does, in the same order,
and then calls the same `run_in_window`. The order is the part that matters -- every entry point
migrates the data dir before anything touches it, and logging is configured first because a windowed
bundle has no console, so the log file in the data dir is the only place a traceback can go.
"""

from __future__ import annotations

import logging
import multiprocessing
import sys

from flackey.config import load_settings, migrate_legacy_data_dir
from flackey.logsetup import configure_logging


def main() -> None:
    settings = load_settings()
    configure_logging(settings.data_dir)
    migrate_legacy_data_dir(settings)
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
