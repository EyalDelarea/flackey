"""Opt-in usage stats (spec: docs/specs/2026-10-27-opt-in-telemetry.md, PR #196).

Callers use the module-level functions: `emit`, `notes`, `pop_notes`. They are no-ops until `install` has
put a client in place and the owner has opted in, so the tests and every code path with stats off run
exactly as before. This package imports nothing from `worker`, `web` or `source` (import-linter layers).
"""
from __future__ import annotations

import os
from functools import partial

from .client import ENV_INTERVAL, ENV_KEY, ENV_MODE, ENV_URL, FILES, Telemetry, build_url, local_url

__all__ = ["FILES", "Telemetry", "build_url", "current", "emit", "enabled", "install", "local_url", "notes", "pop_notes",
           "uninstall", "update_handed_over"]

_current: Telemetry | None = None


def current() -> Telemetry | None:
    return _current


def enabled() -> bool:
    return _current is not None and _current.enabled


def emit(event: str, **fields) -> None:
    t = _current
    if t is not None:
        t.emit(event, **fields)


def notes(request_id: int) -> dict:
    t = _current
    return t.notes(request_id) if t is not None else {}


def pop_notes(request_id: int) -> dict:
    t = _current
    return t.pop_notes(request_id) if t is not None else {}


def update_handed_over(to_version: str, how: str) -> None:
    t = _current
    if t is not None:
        t.update_handed_over(to_version, how)


def from_settings(settings, version: str, environ=os.environ) -> Telemetry:
    from ..config import build_telemetry, save_settings

    mode = (environ.get(ENV_MODE) or "on").strip().lower()
    try:
        interval = float(environ[ENV_INTERVAL]) if environ.get(ENV_INTERVAL) else None
    except ValueError:
        interval = None
    built_url, built_key = build_telemetry()
    return Telemetry(settings.data_dir, version=version, enabled=settings.telemetry_enabled,
                     install_id=settings.telemetry_install_id,
                     install_secret=getattr(settings, "telemetry_install_secret", None),
                     opted_in=settings.telemetry_opted_in,
                     asked=settings.telemetry_asked,
                     url=environ.get(ENV_URL), built_url=built_url, key=environ.get(ENV_KEY) or built_key,
                     mode=mode, interval_s=interval,
                     persist=partial(save_settings, settings))


def install(t: Telemetry | None) -> None:
    global _current
    _current = t


def uninstall() -> None:
    install(None)
