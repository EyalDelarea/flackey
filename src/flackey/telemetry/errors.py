"""Crash capture for `error_caught` (spec §3.2). Only code coordinates leave: the exception's class, and the
innermost flackey frame's module, function and line. Never the message, other frames, locals or a path."""
from __future__ import annotations

import re
import sys
import threading
from types import TracebackType

from . import emit

_TOP = re.compile(r"^[a-z][a-z0-9_]{0,30}$")


def code_location(tb: TracebackType | None) -> dict:
    """The innermost frame in flackey's own code; failing that, the innermost frame's top package name."""
    ours, last = None, None
    while tb is not None:
        name = tb.tb_frame.f_globals.get("__name__", "") or ""
        last = (name, tb)
        if name == "flackey" or name.startswith("flackey."):
            ours = (name, tb)
        tb = tb.tb_next
    if ours is not None:
        name, frame_tb = ours
        return {"module": name, "function": frame_tb.tb_frame.f_code.co_name,
                "line": min(frame_tb.tb_lineno, 65535)}
    if last is not None:
        top = last[0].split(".")[0]
        return {"module": top} if _TOP.match(top) else {}
    return {}


def report(where: str, exc: BaseException, fatal: bool = False) -> None:
    try:
        emit("error_caught", where=where, exc_class=type(exc).__name__, fatal=fatal,
             **code_location(exc.__traceback__))
    except Exception:  # noqa: BLE001, S110 - crash capture must never be the second crash
        pass


_installed = False


def install_hooks() -> None:
    """Chain onto `sys.excepthook` and `threading.excepthook`, once per process; the previous hooks still
    run, and with stats off `report` is a no-op."""
    global _installed
    if _installed:
        return
    _installed = True
    prev_sys, prev_thread = sys.excepthook, threading.excepthook

    def on_sys(tp, value, tb):
        if value is not None and not isinstance(value, KeyboardInterrupt):
            report("excepthook", value, fatal=True)
        prev_sys(tp, value, tb)

    def on_thread(args):
        if args.exc_value is not None and not isinstance(args.exc_value, SystemExit):
            report("server_thread", args.exc_value)
        prev_thread(args)

    sys.excepthook = on_sys
    threading.excepthook = on_thread


def asyncio_handler(loop, context: dict) -> None:
    exc = context.get("exception")
    if isinstance(exc, BaseException):
        report("asyncio", exc)
    loop.default_exception_handler(context)
