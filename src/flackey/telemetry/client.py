"""The opt-in usage stats client (spec §6.1). Off unless the owner said yes, and built so it cannot affect a
download: `emit` never raises and never awaits, and at most waits out a local file write, never the network;
the sender runs on its own task with its own HTTP client; a dead collector costs a bounded local file.

Where the events go, and nowhere else:
- a release build carries its collector in build.json (packaging writes it from CI secrets): an HTTPS
  address and the release key, sent as `X-Flackey-Key`. Nothing in the repository names a real host.
- `FLACKEY_TELEMETRY_URL` overrides it for development and the local demo, and only a loopback address is
  accepted there (`local_url`), so an environment variable can never point the app at someone else's server.
- a checkout, a dev build or the demo with neither has no address at all: the events are kept in the
  outbox for the owner to read, and never sent.
"""
from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import email.utils
import gzip
import json
import logging
import os
import platform
import random
import secrets
import sys
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable
from ipaddress import ip_address
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from .schema import SCHEMA_VERSION, schema

log = logging.getLogger(__name__)

ENV_MODE = "FLACKEY_TELEMETRY"            # "off" overrides everything; "local" builds events, never sends
ENV_URL = "FLACKEY_TELEMETRY_URL"         # a collector on this computer (dev, demo); loopback only
ENV_KEY = "FLACKEY_TELEMETRY_KEY"         # the release key to send with it, for testing a keyed collector
KEY_HEADER = "X-Flackey-Key"
ENV_INTERVAL = "FLACKEY_TELEMETRY_INTERVAL_S"
OUTBOX = "telemetry-outbox.jsonl"
SENT = "telemetry-sent.jsonl"
# One owed delete per line: "<install id> <install secret>". The secret goes with its ID, so a delete still owed
# after New ID proves ownership of the OLD ID. Written 0600: it holds secrets.
PENDING_DELETE = "telemetry-delete-pending"
STATE = "telemetry-state.json"
MARKER = "telemetry-session"
# When the next delete may go and why the last one failed: kept on disk so a restart neither forgets the
# delete nor starts hammering the collector again.
DELETE_RETRY = "telemetry-delete-retry.json"
# A delete the collector refused for good (wrong secret, or a request it can't read): retrying can never help,
# so it is dropped and this records when and why, for Settings > Privacy. Holds no ID and no secret.
DELETE_REFUSED = "telemetry-delete-refused.json"
# An update handed over to a restart or an installer: which version, which way, and when. Read once on the next
# start to say whether it took (`update_step` stage `restart`), then removed.
UPDATE_PENDING = "telemetry-update-pending.json"
UPDATE_HOW = ("seamless", "installer")
FILES = (OUTBOX, SENT, PENDING_DELETE, STATE, MARKER, DELETE_RETRY, DELETE_REFUSED, UPDATE_PENDING)
MAX_QUEUE = 500
MAX_OUTBOX_EVENTS = 2000
MAX_OUTBOX_BYTES = 1_000_000
BATCH = 200
SENT_KEEP = 500
DEFAULT_INTERVAL_S = 15 * 60
JITTER_S = 5 * 60
MIN_INTERVAL_S = 5.0
MAX_BACKOFF_S = 6 * 3600
# A backlog leaves one batch at a time, the next one this long after the last: at most 3 a minute against
# the collector's per-IP limit, never a burst.
DRAIN_GAP_S = 20.0
# A refused or failed delete is retried from a minute later, doubling to MAX_BACKOFF_S, for as long as it
# takes (the opt-out promised it). Every wait is jittered by this fraction so clients never line up.
DELETE_BACKOFF_MIN_S = 60.0
BACKOFF_JITTER = 0.2
TIMEOUT_S = 10.0
TICK_S = 5.0
STATE_SAVE_EVERY_S = 60.0
SOURCE_CHANGE_EVERY_S = 3600.0


def local_url(raw: str | None) -> str | None:
    """The collector's base URL when it is on this computer, else None. Loopback names and addresses only:
    this branch is a local demo, and no build may send anywhere else."""
    if not raw:
        return None
    try:
        parts = urlsplit(raw.strip())
        host = parts.hostname or ""
        port = parts.port
    except ValueError:
        return None
    if parts.scheme != "http" or parts.username or parts.password:
        return None
    if host != "localhost":
        try:
            if not ip_address(host).is_loopback:
                return None
        except ValueError:
            return None
    netloc = f"[{host}]" if ":" in host else host
    return f"http://{netloc}{f':{port}' if port else ''}{parts.path.rstrip('/')}"


def build_url(raw: str | None) -> str | None:
    """The collector a release build carries: HTTPS to a named host, no user, query or fragment; else None.
    Only ever read from build.json, never from the environment or the settings file."""
    if not raw:
        return None
    try:
        parts = urlsplit(raw.strip())
        host = parts.hostname or ""
        port = parts.port
    except ValueError:
        return None
    if parts.scheme != "https" or not host or parts.username or parts.password or parts.query or parts.fragment:
        return None
    netloc = f"[{host}]" if ":" in host else host
    return f"https://{netloc}{f':{port}' if port else ''}{parts.path.rstrip('/')}"


def envelope_platform() -> dict:
    if sys.platform == "darwin":
        os_name, major = "macos", (platform.mac_ver()[0] or "").split(".")[0]
    elif sys.platform == "win32":
        os_name = "windows"
        build = sys.getwindowsversion().build if hasattr(sys, "getwindowsversion") else 0
        major = "11" if build >= 22000 else "10"
    else:
        # The schema has no Linux major yet (its os_major list is macOS and Windows numbers), so a Linux
        # envelope does not validate and the client stays quiet there. See the PR's notes.
        os_name, major = "linux", ""
    machine = platform.machine().lower()
    arch = "arm64" if machine in ("arm64", "aarch64") else "x64"
    return {"os": os_name, "os_major": major, "arch": arch}


def retry_after_s(r: httpx.Response, now: float) -> float | None:
    """The collector's Retry-After, in seconds from `now` (delta-seconds or an HTTP date), capped."""
    raw = (r.headers.get("retry-after") or "").strip()
    if not raw:
        return None
    if raw.isdigit():
        return min(float(raw), float(MAX_BACKOFF_S))
    try:
        when = email.utils.parsedate_to_datetime(raw)
    except (TypeError, ValueError, IndexError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=dt.UTC)
    return min(max(0.0, when.timestamp() - now), float(MAX_BACKOFF_S))


def detect_channel() -> str:
    if getattr(sys, "frozen", False):
        return "packaged"
    if Path("/.dockerenv").exists():
        return "docker"
    return "checkout"


def _today() -> dt.date:
    return dt.datetime.now(dt.UTC).date()


def new_secret() -> str:
    """The install secret: 32 random bytes, URL-safe (43 characters). Proves to the collector that a batch or a
    delete comes from the install that first used the ID, so knowing the ID alone is not enough. Never shown,
    never logged, never in a bug report (`report.known_secrets`)."""
    return secrets.token_urlsafe(32)


def _write_private(path: Path, text: str) -> None:
    """Write `text` readable by this user only, replacing the file whole."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.unlink(missing_ok=True)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


DELETE_REFUSALS = frozenset({(403, "not_owner"), (400, "invalid")})
# A batch can also be refused for a schema the collector no longer takes (410 schema_retired).
BATCH_REFUSALS = DELETE_REFUSALS | {(410, "schema_retired")}


# `dropped` leaves as the lower edge of the `n` bucket (0, 1, 2, 5, 20, 100), not the exact count: the schema
# types it as a plain int, so a coarse value is the most it can carry without a schema change.
DROPPED_STEPS = (100, 20, 5, 2, 1)


def coarse_dropped(count: int) -> int:
    return next((step for step in DROPPED_STEPS if count >= step), 0)


def _parse_lines(lines: list[str]) -> tuple[list[str], list[dict]]:
    """The lines that hold one event each, and those events. A line that does not parse (a write cut short by
    a crash, a hand edit) or is not an event is skipped, so one bad line never blocks the rest."""
    kept, events = [], []
    for ln in lines:
        try:
            ev = json.loads(ln)
        except ValueError:
            continue
        if isinstance(ev, dict):
            kept.append(ln)
            events.append(ev)
    return kept, events


def _refusal(r: httpx.Response, refusals: frozenset[tuple[int, str]] = DELETE_REFUSALS) -> str | None:
    """The collector's own permanent refusal, read from its JSON `error` (delete: 403 not_owner, 400 invalid;
    a batch also 410 schema_retired). Anything else -- a bare 400 or 403, an HTML page, JSON that isn't the
    collector's -- comes from something in between (a proxy, a WAF, a geo-block) and is retried."""
    if r.status_code not in {code for code, _ in refusals}:
        return None
    try:
        err = r.json().get("error")
    except (ValueError, AttributeError):
        return None
    return err if (r.status_code, err) in refusals else None


class Telemetry:
    def __init__(self, data_dir: Path, *, version: str, enabled: bool = False, install_id: str | None = None,
                 install_secret: str | None = None, opted_in: str | None = None, asked: bool = False, url: str | None = None, mode: str = "on",
                 built_url: str | None = None, key: str | None = None,
                 interval_s: float | None = None, persist: Callable[..., None] | None = None,
                 clock: Callable[[], float] = time.time, today: Callable[[], dt.date] = _today,
                 transport: httpx.AsyncBaseTransport | None = None, channel: str | None = None,
                 platform_fields: dict | None = None):
        self.data_dir = Path(data_dir)
        self.version = version
        self.mode = mode if mode in ("on", "local", "off") else "on"
        # The environment may only name this computer; a release build's own address comes from build.json.
        # A refused override sends nothing rather than falling back: whoever set it meant somewhere else.
        self.url = local_url(url) if url else build_url(built_url)
        if url and self.url is None:
            log.warning("usage stats: refusing a collector that is not on this computer; nothing will be sent")
        elif built_url and not url and self.url is None:
            log.warning("usage stats: this build's collector address is not HTTPS; nothing will be sent")
        self.key = key or None
        self.install_id = install_id if enabled else None
        self.install_secret = install_secret if enabled else None
        self.opted_in = opted_in
        self.asked = bool(asked)
        self._enabled = bool(enabled and install_id)
        self.interval_s = max(MIN_INTERVAL_S, interval_s) if interval_s else DEFAULT_INTERVAL_S
        self._jitter = JITTER_S if interval_s is None else 0.0
        self.persist = persist or (lambda **_kw: None)
        if self._enabled and not self.install_secret:
            # Opted in before the secret existed (a pre-release build): it gets one now. Its ID has never been
            # registered with a secret on a collector that requires one, so the first batch registers this.
            self.install_secret = new_secret()
            self.persist(telemetry_install_secret=self.install_secret)
        self.clock, self.today, self.transport = clock, today, transport
        self.session_id = str(uuid.uuid4())
        self._seq = 0
        self._lock = threading.Lock()     # the in-memory queue; held only for a moment
        # The day's state and the local files. Re-entrant: _bump_state rolls the day, which emits.
        # Order is always _io then _lock.
        self._io = threading.RLock()
        # Bumped by opt-in, opt-out and New ID. A send that started under an older generation writes nothing
        # back when it returns: the files it read belong to an ID that is gone.
        self._gen = 0
        self._queue: deque[dict] = deque(maxlen=MAX_QUEUE)
        self._dropped = 0                 # events lost to a full queue or outbox, sent as `dropped`
        self._fields_dropped = 0          # fields that failed the schema (never sent, only counted)
        self._notes: dict[int, dict] = {}
        # source -> {"sent": the state the collector last heard, "before": the state that change left,
        # "at": when it was sent, "recovered": a recovery was already sent inside this hour}
        self._sources: dict[str, dict] = {}
        self._state: dict = {}
        self._state_saved = 0.0
        self._next_send = 0.0
        self._backoff = 0.0
        self._stopped = False             # the collector said stop for this run
        self.on_opt_in: Callable[[dict], None] | None = None
        self.last_delete_confirmed: str | None = None
        self.last_send: dict | None = None
        self._base = {"app_version": version, "channel": channel or detect_channel(),
                      **(platform_fields or envelope_platform())}
        try:
            bad = schema().check_common({**self._base, "event": "app_started", "session_id": self.session_id,
                                         "seq": 0, "event_date": self.today().isoformat()})
        except (OSError, ValueError, KeyError):   # a build without the schema file: stats unavailable, app fine
            bad = "schema file"
        self._envelope_ok = bad is None
        if bad is not None:
            log.warning("usage stats are unavailable on this system: its %s is outside the schema", bad)

    # -- consent ---------------------------------------------------------------------------------------------

    @property
    def available(self) -> bool:
        """Whether to offer stats at all. A packaged or Docker build with no collector baked in could never
        send, so it never asks; a checkout without one keeps the outbox for the developer, and the local
        demo (`FLACKEY_TELEMETRY=local`) never sends by design."""
        if self.mode == "off" or not self._envelope_ok:
            return False
        return self.url is not None or self.mode == "local" or self._base.get("channel") == "checkout"

    @property
    def enabled(self) -> bool:
        return self._enabled and self.available

    @property
    def sending(self) -> bool:
        return self.enabled and self.mode == "on" and self.url is not None

    def opt_in(self) -> None:
        if not self.available:
            raise ValueError("usage stats are switched off on this computer")
        if self._enabled:
            return
        with self._io:
            self._gen += 1
            with self._lock:
                self._queue.clear()
            # Only ever written while stats were on, so one found now is from before an opt-out: stale.
            (self.data_dir / UPDATE_PENDING).unlink(missing_ok=True)
            self.install_id = str(uuid.uuid4())
            self.install_secret = new_secret()
            self.opted_in = self.today().isoformat()
            self._enabled = True
        self.persist(telemetry_enabled=True, telemetry_install_id=self.install_id,
                     telemetry_install_secret=self.install_secret, telemetry_opted_in=self.opted_in)
        info = self.start_session()
        self._next_send = 0.0
        if self.on_opt_in is not None:
            self._safe(self.on_opt_in, info)

    def opt_out(self) -> None:
        """Off, and everything sent is deleted: the delete is queued on disk and retried until the
        collector confirms it. Nothing else of telemetry is left on this computer."""
        with self._io:
            old, old_secret = self.install_id, self.install_secret
            self._gen += 1
            self._enabled = False
            self.install_id = None
            self.install_secret = None
            self.opted_in = None
            with self._lock:
                self._queue.clear()
                self._notes.clear()
                self._dropped = 0         # with the queue, so an emit can't count the old ID's drop after
            self._state.clear()
            for name in (OUTBOX, SENT, STATE, MARKER, UPDATE_PENDING):
                (self.data_dir / name).unlink(missing_ok=True)
        if old and old_secret:
            self._queue_delete(old, old_secret)
        self.persist(telemetry_enabled=False, telemetry_install_id=None, telemetry_install_secret=None,
                     telemetry_opted_in=None)
        self._next_send = 0.0

    def mark_asked(self) -> None:
        """The owner has answered the usage-stats question (either way, or by dismissing it), so the
        one-time prompt is never shown again. Changes nothing about whether stats are on."""
        if self.asked:
            return
        self.asked = True
        self.persist(telemetry_asked=True)

    def reset_id(self) -> None:
        """New ID: the old one's data is deleted first, and what was queued under it goes with it."""
        if not self._enabled:
            raise ValueError("usage stats are off")
        with self._io:
            old, old_secret = self.install_id, self.install_secret
            self._gen += 1
            with self._lock:
                self._queue.clear()
                self._dropped = 0         # the old ID's count, never sent as the new ID's
            (self.data_dir / OUTBOX).unlink(missing_ok=True)
            self.install_id = str(uuid.uuid4())
            self.install_secret = new_secret()
        if old and old_secret:
            self._queue_delete(old, old_secret)   # the OLD pair: the collector knows the old ID by the old secret
        self.persist(telemetry_install_id=self.install_id, telemetry_install_secret=self.install_secret)
        self._next_send = 0.0

    def _queue_delete(self, install_id: str, install_secret: str) -> None:
        # Under _io, as _send_deletes settles the owed list under it: neither can overwrite the other's write.
        with self._io:
            owed = self._pending_deletes()
            if install_id not in {i for i, _ in owed}:
                owed.append((install_id, install_secret))
            self.data_dir.mkdir(parents=True, exist_ok=True)
            self._write_pending(owed)
            # A fresh delete is tried at once, whatever an older one was waiting on.
            (self.data_dir / DELETE_RETRY).unlink(missing_ok=True)

    def _delete_retry(self) -> dict:
        try:
            d = json.loads((self.data_dir / DELETE_RETRY).read_text(encoding="utf-8"))
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    def _delete_due(self) -> float:
        """When the owed delete may go. Never more than MAX_BACKOFF_S past the moment this first sees it: a
        time written while the clock was ahead is pulled in and saved, so once the clock is put right the
        delete waits at most the longest backoff, not for the old "future"."""
        r = self._delete_retry()
        try:
            nxt = float(r.get("next", 0) or 0)
        except (TypeError, ValueError):
            nxt = 0.0
        limit = self.clock() + MAX_BACKOFF_S
        if nxt > limit:
            nxt = limit
            with contextlib.suppress(OSError):
                (self.data_dir / DELETE_RETRY).write_text(json.dumps({**r, "next": nxt}), encoding="utf-8")
        return nxt

    def _jittered(self, s: float) -> float:
        return s * random.uniform(1 - BACKOFF_JITTER, 1 + BACKOFF_JITTER) if self._jitter else s

    def _pending_deletes(self) -> list[tuple[str, str]]:
        """Every owed delete as (install id, install secret). A line without a secret (written by a pre-release
        build) cannot prove ownership, so it is dropped rather than retried forever."""
        try:
            lines = (self.data_dir / PENDING_DELETE).read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        owed = []
        for ln in lines:
            parts = ln.split()
            if len(parts) == 2:
                owed.append((parts[0], parts[1]))
        return owed

    def _write_pending(self, owed: list[tuple[str, str]]) -> None:
        _write_private(self.data_dir / PENDING_DELETE, "".join(f"{i} {s}\n" for i, s in owed))

    # -- emitting --------------------------------------------------------------------------------------------

    def emit(self, event: str, event_date: str | None = None, **fields) -> None:
        """Queue one event if the owner opted in. Never raises: a bug here must not cost a download. Never waits
        on the network; `request_finished` also counts the day under _io, so it can wait out a local file write
        (a flush or a send's bookkeeping), never a request in flight."""
        try:
            if not self.enabled:
                return
            clean, dropped = schema().clean(event, fields)
            if dropped:
                self._fields_dropped += len(dropped)
                log.debug("usage stats: %s dropped fields %s", event, dropped)
            with self._lock:
                if not self._enabled:             # opt-out may have landed since the check above
                    return
                ev = {"event": event, "session_id": self.session_id, "seq": self._seq,
                      "event_date": event_date or self.today().isoformat(), **self._base, **clean}
                self._seq += 1
                if len(self._queue) == self._queue.maxlen:
                    self._dropped += 1
                self._queue.append(ev)
            if event == "request_finished":
                self._bump_state("requests_finished")
        except Exception:
            log.debug("usage stats: could not build %s", event, exc_info=True)

    def _safe(self, fn: Callable, *args) -> None:
        try:
            fn(*args)
        except Exception:
            log.debug("usage stats: hook failed", exc_info=True)

    def notes(self, request_id: int) -> dict:
        """Per-request facts kept in memory until the request finishes (timestamps, counters). A throwaway
        dict while stats are off, so nothing accumulates."""
        if not self.enabled:
            return {}
        return self._notes.setdefault(request_id, {})

    def pop_notes(self, request_id: int) -> dict:
        return self._notes.pop(request_id, {})

    def source_status(self, source: str, state: str, reason: str | None = None) -> None:
        """A transition of one source's health (spec §3.2), at most one change an hour per source, with two
        exceptions so the dashboard never ends on a stale state (audit 2026-11-16: Soulseek dropped, came
        back a minute later, and the recovery was never sent, so it read as down while it filed tracks):

        - a recovery inside the hour (back to the state the last change left) is sent at once, once;
        - any other change held back by the hour is sent when the hour is up, as one net change, if the
          source is still not in the state last sent.

        So a flapping source costs at most three events an hour, and the last one sent is the real state.
        `reason` is a fixed code for why it went down (e.g. not_logged_in), sent only with a change
        away from ok or open."""
        if not self.enabled:
            return
        now = self.clock()
        src = self._sources.get(source)
        if src is None:
            self._sources[source] = {"sent": state, "before": None, "at": float("-inf"), "recovered": False}
            return
        if state == src["sent"]:
            return
        why = reason if state not in ("ok", "open") else None
        if now - src["at"] >= SOURCE_CHANGE_EVERY_S:
            self.emit("source_status_changed", source=source, reason=why, **{"from": src["sent"], "to": state})
            src.update(sent=state, before=src["sent"], at=now, recovered=False)
        elif state == src["before"] and not src["recovered"]:
            self.emit("source_status_changed", source=source, reason=why, **{"from": src["sent"], "to": state})
            src.update(sent=state, before=src["sent"], recovered=True)

    # -- sessions and the daily health rollup ----------------------------------------------------------------

    def _load_state(self) -> dict:
        if not self._state:      # filled in place: callers hold on to this dict
            try:
                loaded = json.loads((self.data_dir / STATE).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                loaded = {}
            if isinstance(loaded, dict):
                self._state.update(loaded)
        return self._state

    def _save_state(self) -> None:
        if not self.enabled:
            return
        with self._io:                # a worker thread's _bump_state must not change the dict mid-dump
            text = json.dumps(self._state)
            self.data_dir.mkdir(parents=True, exist_ok=True)
            tmp = self.data_dir / (STATE + ".tmp")
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, self.data_dir / STATE)
        self._state_saved = self.clock()

    def _bump_state(self, key: str) -> None:
        with self._io:
            st = self._load_state()
            self._roll_day()
            st[key] = st.get(key, 0) + 1

    def start_session(self) -> dict:
        """Mark this launch as running, and say how the last one ended (spec `previous_exit`): a marker
        still there is a session that never reached `end_session`. Never raises: a full or read-only disk
        costs this launch's stats, never the launch."""
        if not self.enabled:
            return {}
        try:
            with self._io:
                return self._start_session()
        except Exception:
            log.debug("usage stats: could not start the session", exc_info=True)
            return {}

    def _start_session(self) -> dict:
        st = self._load_state()
        marker = self.data_dir / MARKER
        if not st:
            previous = "first_run"
        elif marker.exists():
            previous = "crash"
        else:
            previous = "clean"
        self._roll_day()
        st["sessions"] = st.get("sessions", 0) + 1
        if previous == "crash":
            st["crashed_sessions"] = st.get("crashed_sessions", 0) + 1
        last = st.get("last_version")
        st["last_version"] = self.version
        self.data_dir.mkdir(parents=True, exist_ok=True)
        marker.write_text(self.session_id, encoding="utf-8")
        self._save_state()
        self._update_outcome()
        return {"previous_exit": previous, "updated_from": last if last and last != self.version else ""}

    def update_handed_over(self, to_version: str, how: str) -> None:
        """An update left the app's hands: a staged bundle armed for the swap on quit (`seamless`), or an
        installer opened (`installer`). The next start says whether it took."""
        if not self.enabled or how not in UPDATE_HOW:
            return
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            _write_private(self.data_dir / UPDATE_PENDING,
                           json.dumps({"to": to_version, "how": how, "at": self.clock()}))
        except OSError:
            log.debug("usage stats: could not note the pending update", exc_info=True)

    def _update_outcome(self) -> None:
        """Whether the update the last session handed over is the version now running. A seamless swap that
        did not take is a failure; an installer the owner closed is not, so it is not reported."""
        path = self.data_dir / UPDATE_PENDING
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raw = None
        path.unlink(missing_ok=True)
        if not isinstance(raw, dict) or raw.get("how") not in UPDATE_HOW or not isinstance(raw.get("to"), str):
            return
        to = raw["to"]
        at = raw.get("at")
        from .buckets import dur
        took = dur(self.clock() - at) if isinstance(at, (int, float)) and not isinstance(at, bool) else None
        if to == self.version:
            self.emit("update_step", stage="restart", result="ok", to_version=to, trigger="manual",
                      error_code="", time=took)
        elif raw["how"] == "seamless":
            self.emit("update_step", stage="restart", result="failed", to_version=to, trigger="manual",
                      error_code="install_failed", time=took)

    def end_session(self) -> None:
        """Never raises: shutdown still has to stop slskd and give the port back after this."""
        if not self.enabled:
            return
        try:
            for step in (self._flush, self._save_state):
                try:
                    step()
                except Exception:
                    log.debug("usage stats: could not save at quit", exc_info=True)
        finally:
            with contextlib.suppress(Exception):
                (self.data_dir / MARKER).unlink(missing_ok=True)

    def _roll_day(self) -> None:
        """Send yesterday's (or an earlier day's) health once, dated that day, and start today's. Under _io,
        so the worker thread and the loop rolling at midnight together send it once."""
        with self._io:
            self._roll_day_locked()

    def _roll_day_locked(self) -> None:
        st = self._load_state()
        today = self.today().isoformat()
        day = st.get("day")
        if day == today:
            return
        if day and st.get("sessions"):
            probes = st.get("probes", {})

            def up(name: str) -> str:
                from .buckets import share
                ok, total = probes.get(name, [0, 0])
                return share(ok, total)

            from .buckets import dur, n
            self.emit("health_daily", event_date=day, uptime=dur(st.get("uptime_s", 0)),
                      soulseek_up=up("soulseek"), telegram_up=up("telegram"),
                      sessions=min(st.get("sessions", 0), 255),
                      crashed_sessions=min(st.get("crashed_sessions", 0), 255),
                      requests_finished=n(st.get("requests_finished", 0)))
        keep = {k: st[k] for k in ("last_version",) if k in st}
        st.clear()
        st.update(keep, day=today, sessions=0, crashed_sessions=0, requests_finished=0, uptime_s=0.0,
                  probes={})

    def health_tick(self, elapsed_s: float, **sources: bool | None) -> None:
        """One sample of each source's health (True up, False down, None not in use) and the time since
        the last sample, for the day's `health_daily`."""
        if not self.enabled:
            return
        try:
            with self._io:
                self._health_tick(elapsed_s, sources)
        except Exception:
            log.debug("usage stats: health sample failed", exc_info=True)

    def _health_tick(self, elapsed_s: float, sources: dict) -> None:
        st = self._load_state()
        self._roll_day()
        st["uptime_s"] = st.get("uptime_s", 0.0) + max(0.0, elapsed_s)
        probes = st.setdefault("probes", {})
        for name, up in sources.items():
            if up is None:
                continue
            ok, total = probes.get(name, [0, 0])
            probes[name] = [ok + int(bool(up)), total + 1]

    # -- the local files ---------------------------------------------------------------------------------------

    def _flush(self) -> None:
        """Move the in-memory queue to the outbox, capped at 2,000 events or 1 MB, oldest dropped first.
        Under _io, so an opt-out can't land between reading the outbox and writing it back."""
        with self._io:
            self._flush_locked()

    def _flush_locked(self) -> None:
        with self._lock:
            if not self._queue:
                return
            items = list(self._queue)
            self._queue.clear()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        path = self.data_dir / OUTBOX
        raw = self._outbox_lines()
        good, _ = _parse_lines(raw)
        self._dropped += len(raw) - len(good)
        lines = good + [json.dumps(e, separators=(",", ":")) for e in items]
        over = max(0, len(lines) - MAX_OUTBOX_EVENTS)
        while sum(len(ln) + 1 for ln in lines[over:]) > MAX_OUTBOX_BYTES and over < len(lines):
            over += 1
        if over:
            self._dropped += over
            lines = lines[over:]
        self._write_lines(path, lines)

    def _outbox_lines(self) -> list[str]:
        try:
            return [ln for ln in (self.data_dir / OUTBOX).read_text(encoding="utf-8").splitlines() if ln]
        except OSError:
            return []

    @staticmethod
    def _write_lines(path: Path, lines: list[str]) -> None:
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text("".join(f"{ln}\n" for ln in lines), encoding="utf-8")
        os.replace(tmp, path)

    def _record_sent(self, lines: list[str]) -> None:
        path = self.data_dir / SENT
        try:
            old = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln]
        except OSError:
            old = []
        self._write_lines(path, (old + lines)[-SENT_KEEP:])

    def snapshot(self) -> dict:
        """What the owner sees under Settings > Privacy > What's sent: the pending events and the last 500
        sent, exactly as they leave (before gzip)."""
        with self._lock:
            queued = list(self._queue)
        pending = _parse_lines(self._outbox_lines())[1] + queued
        try:
            sent_lines = [ln for ln in (self.data_dir / SENT).read_text(encoding="utf-8").splitlines() if ln]
        except OSError:
            sent_lines = []
        sent = _parse_lines(sent_lines)[1]
        return {"pending": pending, "sent": sent}

    def describe(self) -> dict:
        return {"available": self.available, "enabled": self.enabled, "mode": self.mode,
                "collector": self.url, "sending": self.sending,
                "id_prefix": (self.install_id or "")[:8] or None, "opted_in": self.opted_in, "asked": self.asked,
                "pending_delete": bool(self._pending_deletes()), "delete_retry": self._describe_delete_retry(),
                "delete_refused": self._delete_refused(),
                "last_delete_confirmed": self.last_delete_confirmed, "last_send": self.last_send,
                "interval_s": self.interval_s}

    def _delete_refused(self) -> dict | None:
        """For Settings > Privacy: the last delete the collector refused for good, as {at, reason}."""
        try:
            d = json.loads((self.data_dir / DELETE_REFUSED).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return {"at": d.get("at"), "reason": d.get("reason")} if isinstance(d, dict) else None

    def _describe_delete_retry(self) -> dict | None:
        """For Settings > Privacy: a delete that has failed, how often, why, and when it goes again."""
        r = self._delete_retry() if self._pending_deletes() else {}
        if not r.get("attempts"):
            return None
        nxt = r.get("next")
        return {"attempts": int(r["attempts"]), "error": r.get("error"),
                "next_at": dt.datetime.fromtimestamp(nxt, dt.UTC).isoformat(timespec="seconds")
                if isinstance(nxt, (int, float)) else None}

    # -- sending -----------------------------------------------------------------------------------------------

    async def run(self) -> None:
        """The sender: flush, delete what is owed, send what is due. Supervised by its caller; a quiet
        sleep while stats are off and nothing is owed."""
        async with httpx.AsyncClient(timeout=TIMEOUT_S, transport=self.transport) as http:
            while True:
                try:
                    await self.tick(http)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.debug("usage stats: sender tick failed", exc_info=True)
                await asyncio.sleep(TICK_S)

    async def tick(self, http: httpx.AsyncClient) -> None:
        if (self.url is not None and self.mode == "on" and self._pending_deletes()
                and self.clock() >= self._delete_due()):
            await self._send_deletes(http)
        if not self.enabled:
            return
        self._flush()
        if self.clock() - self._state_saved >= STATE_SAVE_EVERY_S:
            self._save_state()
        if not self.sending or self._stopped or self.clock() < self._next_send:
            return
        # One batch per turn. A backlog drains DRAIN_GAP_S apart; a failure backs off (jittered, and never
        # sooner than the collector's Retry-After), so a down collector costs one request per wait.
        ok, more, wait = await self._send_batch(http)
        if ok:
            self._backoff = 0.0
            self._next_send = self.clock() + (DRAIN_GAP_S if more else
                                              self.interval_s + random.uniform(-self._jitter, self._jitter))
        else:
            self._backoff = min(MAX_BACKOFF_S, max(self.interval_s, self._backoff * 2))
            self._next_send = self.clock() + min(MAX_BACKOFF_S, max(self._jittered(self._backoff), wait or 0.0))

    async def _send_batch(self, http: httpx.AsyncClient) -> tuple[bool, bool, float | None]:
        """(the send worked or the batch was dropped as refused, there is more to send, the collector's
        Retry-After in seconds when it gave one)."""
        with self._io:
            gen = self._gen
            raw = self._outbox_lines()
            lines, events = _parse_lines(raw)
            if len(lines) != len(raw):          # a corrupt line: skipped for good, counted, never blocks the rest
                self._dropped += len(raw) - len(lines)
                self._write_lines(self.data_dir / OUTBOX, lines)
            install_id, install_secret, dropped = self.install_id, self.install_secret, self._dropped
        if not lines or not install_id or not install_secret:
            return True, False, None
        batch = lines[:BATCH]
        body = {"schema": SCHEMA_VERSION, "install_id": install_id, "install_secret": install_secret,
                "dropped": coarse_dropped(dropped), "events": events[:BATCH]}
        data = gzip.compress(json.dumps(body, separators=(",", ":")).encode())
        try:
            headers = {"Content-Type": "application/json", "Content-Encoding": "gzip"}
            if self.key:
                headers[KEY_HEADER] = self.key
            r = await http.post(f"{self.url}/v1/batch", content=data, headers=headers)
        except httpx.HTTPError as e:
            self.last_send = {"status": "failed", "error": type(e).__name__, "at": self._stamp()}
            return False, True, None
        with self._io:
            if self._gen != gen:
                # Opt-out or New ID landed while this was on the wire: the outbox and sent log it read are
                # gone with that ID, and must not be written back (nor go later under a new ID).
                return True, False, None
            return self._after_batch(r, lines, batch)

    def _after_batch(self, r: httpx.Response, lines: list[str], batch: list[str]) -> tuple[bool, bool, float | None]:
        code = r.status_code
        refused = _refusal(r, BATCH_REFUSALS)
        # What Settings > Privacy shows: "ok", or "failed" with why (kept and retried, or refused and dropped)
        if 200 <= code < 300:
            self.last_send = {"status": "ok", "events": len(batch), "at": self._stamp()}
        elif refused:
            self.last_send = {"status": "failed", "error": f"HTTP {code}, refused and dropped", "at": self._stamp()}
        elif code == 429 or code >= 500:
            self.last_send = {"status": "failed", "error": f"HTTP {code}, will retry", "at": self._stamp()}
        else:
            # Not the collector's answer (a proxy, WAF, geo-block or captive portal): it never saw the batch,
            # so the batch stays and goes again on the usual backoff. The outbox cap still bounds it.
            self.last_send = {"status": "failed", "at": self._stamp(),
                              "error": f"Couldn't reach the stats server (HTTP {code}), will retry"}
        if not (200 <= code < 300 or refused):
            return False, True, retry_after_s(r, self.clock())
        rest = lines[len(batch):]
        stop = False
        if 200 <= r.status_code < 300:
            with contextlib.suppress(ValueError, AttributeError):
                stop = bool(r.json().get("stop"))
        if stop:
            # "stop" (a revoked or missing key, or the collector's kill switch) means nothing was stored. Never
            # shown as sent: the batch is dropped, counted, and this run sends no more.
            self._stopped = True
            self._dropped += len(batch)
            self.last_send = {"status": "failed", "error": "the collector asked this version to stop sending; "
                              "nothing was stored", "at": self._stamp()}
            log.warning("usage stats: the collector said stop; %d events dropped", len(batch))
        elif 200 <= r.status_code < 300:
            self._record_sent(batch)
            self._dropped = 0
        else:
            # The collector's own refusal (invalid, not_owner, schema_retired): it would be refused forever (spec §6.1)
            field = ""
            with contextlib.suppress(ValueError, AttributeError):
                field = str(r.json().get("field", ""))[:60]
            log.warning("usage stats: the collector refused a batch (%d %s %s); dropped it", code, refused, field)
            self._dropped += len(batch)
            if refused == "schema_retired":
                # Every batch of this schema would be refused the same way: stop for this run rather than
                # drain the outbox batch by batch. What is left stays, and goes nowhere until an update.
                self._stopped = True
                self._write_lines(self.data_dir / OUTBOX, rest)
                return True, False, None
        self._write_lines(self.data_dir / OUTBOX, rest)
        return True, bool(rest), None

    async def _send_deletes(self, http: httpx.AsyncClient) -> None:
        """Every owed delete, one after another. A 2xx clears one. The collector's own refusal (403 not_owner:
        another secret owns the ID; 400 invalid: it can't read the request) can never succeed, so that one is
        dropped and recorded for Settings > Privacy. Anything else (a bare 403 from a geo-block, a 429, a
        network error) keeps it and backs off -- doubling from DELETE_BACKOFF_MIN_S to MAX_BACKOFF_S, jittered,
        never sooner than Retry-After -- with the wait kept on disk across restarts."""
        left, error, wait = [], None, None
        owed = self._pending_deletes()
        for install_id, install_secret in owed:
            refused = None
            try:
                r = await http.post(f"{self.url}/v1/delete",
                                    json={"install_id": install_id, "install_secret": install_secret})
                done = 200 <= r.status_code < 300
                if not done:
                    refused = _refusal(r)
                    error = f"HTTP {r.status_code}"
                    wait = retry_after_s(r, self.clock())
            except httpx.HTTPError as e:
                done, error = False, type(e).__name__
            if done:
                self.last_delete_confirmed = self._stamp()
                log.info("usage stats: the collector confirmed a delete")
            elif refused:
                _write_private(self.data_dir / DELETE_REFUSED,
                               json.dumps({"at": self._stamp(), "reason": refused}))
                # Never the ID or the secret: only that one was refused, and why.
                log.warning("usage stats: the collector refused a delete for good (%s); not retrying", refused)
            else:
                left.append((install_id, install_secret))
        path = self.data_dir / PENDING_DELETE
        retry = self.data_dir / DELETE_RETRY
        # An opt-out or New ID while these were on the wire queued another delete: keep it. Re-read and
        # written back under _io, which _queue_delete also takes, so one queued now waits rather than is lost.
        with self._io:
            tried = {i for i, _ in owed}
            left += [p for p in self._pending_deletes() if p[0] not in tried]
            if not left:
                path.unlink(missing_ok=True)
                retry.unlink(missing_ok=True)
                return
            self._write_pending(left)
            attempts = int(self._delete_retry().get("attempts", 0) or 0) + 1
            backoff = min(float(MAX_BACKOFF_S), DELETE_BACKOFF_MIN_S * 2 ** min(attempts - 1, 20))
            delay = min(float(MAX_BACKOFF_S), max(self._jittered(backoff), wait or 0.0))
            retry.write_text(json.dumps({"attempts": attempts, "next": self.clock() + delay, "error": error}),
                             encoding="utf-8")
        log.info("usage stats: a delete did not go through (%s); retrying in %.0fs", error, delay)

    def _stamp(self) -> str:
        return dt.datetime.fromtimestamp(self.clock(), dt.UTC).isoformat(timespec="seconds")
