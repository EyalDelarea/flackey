"""The opt-in usage stats client (spec §6.1). Off unless the owner said yes, and built so it cannot affect a
download: `emit` never raises, never awaits and never blocks; the sender runs on its own task with its own
HTTP client; a dead collector costs a bounded local file.

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
import gzip
import json
import logging
import os
import platform
import random
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
PENDING_DELETE = "telemetry-delete-pending"
STATE = "telemetry-state.json"
MARKER = "telemetry-session"
FILES = (OUTBOX, SENT, PENDING_DELETE, STATE, MARKER)
MAX_QUEUE = 500
MAX_OUTBOX_EVENTS = 2000
MAX_OUTBOX_BYTES = 1_000_000
BATCH = 200
SENT_KEEP = 500
DEFAULT_INTERVAL_S = 15 * 60
JITTER_S = 5 * 60
MIN_INTERVAL_S = 5.0
MAX_BACKOFF_S = 6 * 3600
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


def detect_channel() -> str:
    if getattr(sys, "frozen", False):
        return "packaged"
    if Path("/.dockerenv").exists():
        return "docker"
    return "checkout"


def _today() -> dt.date:
    return dt.datetime.now(dt.UTC).date()


class Telemetry:
    def __init__(self, data_dir: Path, *, version: str, enabled: bool = False, install_id: str | None = None,
                 opted_in: str | None = None, url: str | None = None, mode: str = "on",
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
        self.opted_in = opted_in
        self._enabled = bool(enabled and install_id)
        self.interval_s = max(MIN_INTERVAL_S, interval_s) if interval_s else DEFAULT_INTERVAL_S
        self._jitter = JITTER_S if interval_s is None else 0.0
        self.persist = persist or (lambda **_kw: None)
        self.clock, self.today, self.transport = clock, today, transport
        self.session_id = str(uuid.uuid4())
        self._seq = 0
        self._lock = threading.Lock()
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
        return self.mode != "off" and self._envelope_ok

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
        self.install_id = str(uuid.uuid4())
        self.opted_in = self.today().isoformat()
        self._enabled = True
        self.persist(telemetry_enabled=True, telemetry_install_id=self.install_id,
                     telemetry_opted_in=self.opted_in)
        info = self.start_session()
        self._next_send = 0.0
        if self.on_opt_in is not None:
            self._safe(self.on_opt_in, info)

    def opt_out(self) -> None:
        """Off, and everything sent is deleted: the delete is queued on disk and retried until the
        collector confirms it. Nothing else of telemetry is left on this computer."""
        old = self.install_id
        self._enabled = False
        self.install_id = None
        self.opted_in = None
        with self._lock:
            self._queue.clear()
            self._notes.clear()
        self._state = {}
        for name in (OUTBOX, SENT, STATE, MARKER):
            (self.data_dir / name).unlink(missing_ok=True)
        if old:
            self._queue_delete(old)
        self.persist(telemetry_enabled=False, telemetry_install_id=None, telemetry_opted_in=None)
        self._next_send = 0.0

    def reset_id(self) -> None:
        """New ID: the old one's data is deleted first, and what was queued under it goes with it."""
        if not self._enabled:
            raise ValueError("usage stats are off")
        old = self.install_id
        with self._lock:
            self._queue.clear()
        (self.data_dir / OUTBOX).unlink(missing_ok=True)
        if old:
            self._queue_delete(old)
        self.install_id = str(uuid.uuid4())
        self.persist(telemetry_install_id=self.install_id)
        self._next_send = 0.0

    def _queue_delete(self, install_id: str) -> None:
        path = self.data_dir / PENDING_DELETE
        ids = self._pending_deletes()
        if install_id not in ids:
            ids.append(install_id)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        path.write_text("".join(f"{i}\n" for i in ids), encoding="utf-8")

    def _pending_deletes(self) -> list[str]:
        try:
            return [ln.strip() for ln in (self.data_dir / PENDING_DELETE).read_text().splitlines() if ln.strip()]
        except OSError:
            return []

    # -- emitting --------------------------------------------------------------------------------------------

    def emit(self, event: str, event_date: str | None = None, **fields) -> None:
        """Queue one event if the owner opted in. Never raises: a bug here must not cost a download."""
        try:
            if not self.enabled:
                return
            clean, dropped = schema().clean(event, fields)
            if dropped:
                self._fields_dropped += len(dropped)
                log.debug("usage stats: %s dropped fields %s", event, dropped)
            with self._lock:
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
        `reason` is a fixed code for why it went down (e.g. cloudflare_challenge), sent only with a change
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
        self.data_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.data_dir / (STATE + ".tmp")
        tmp.write_text(json.dumps(self._state), encoding="utf-8")
        os.replace(tmp, self.data_dir / STATE)
        self._state_saved = self.clock()

    def _bump_state(self, key: str) -> None:
        st = self._load_state()
        self._roll_day()
        st[key] = st.get(key, 0) + 1

    def start_session(self) -> dict:
        """Mark this launch as running, and say how the last one ended (spec `previous_exit`): a marker
        still there is a session that never reached `end_session`."""
        if not self.enabled:
            return {}
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
        return {"previous_exit": previous, "updated_from": last if last and last != self.version else ""}

    def end_session(self) -> None:
        if not self.enabled:
            return
        self._flush()
        self._save_state()
        (self.data_dir / MARKER).unlink(missing_ok=True)

    def _roll_day(self) -> None:
        """Send yesterday's (or an earlier day's) health once, dated that day, and start today's."""
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
                      soulseek_up=up("soulseek"), telegram_up=up("telegram"), beatport_up=up("beatport"),
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
            st = self._load_state()
            self._roll_day()
            st["uptime_s"] = st.get("uptime_s", 0.0) + max(0.0, elapsed_s)
            probes = st.setdefault("probes", {})
            for name, up in sources.items():
                if up is None:
                    continue
                ok, total = probes.get(name, [0, 0])
                probes[name] = [ok + int(bool(up)), total + 1]
        except Exception:
            log.debug("usage stats: health sample failed", exc_info=True)

    # -- the local files ---------------------------------------------------------------------------------------

    def _flush(self) -> None:
        """Move the in-memory queue to the outbox, capped at 2,000 events or 1 MB, oldest dropped first."""
        with self._lock:
            if not self._queue:
                return
            items = list(self._queue)
            self._queue.clear()
        self.data_dir.mkdir(parents=True, exist_ok=True)
        path = self.data_dir / OUTBOX
        lines = self._outbox_lines() + [json.dumps(e, separators=(",", ":")) for e in items]
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
        pending = [json.loads(ln) for ln in self._outbox_lines()] + queued
        try:
            sent = [json.loads(ln) for ln in (self.data_dir / SENT).read_text(encoding="utf-8").splitlines() if ln]
        except OSError:
            sent = []
        return {"pending": pending, "sent": sent}

    def describe(self) -> dict:
        return {"available": self.available, "enabled": self.enabled, "mode": self.mode,
                "collector": self.url, "sending": self.sending,
                "id_prefix": (self.install_id or "")[:8] or None, "opted_in": self.opted_in,
                "pending_delete": bool(self._pending_deletes()),
                "last_delete_confirmed": self.last_delete_confirmed, "last_send": self.last_send,
                "interval_s": self.interval_s}

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
        if self.url is not None and self.mode == "on" and self._pending_deletes():
            await self._send_deletes(http)
        if not self.enabled:
            return
        self._flush()
        if self.clock() - self._state_saved >= STATE_SAVE_EVERY_S:
            self._save_state()
        if not self.sending or self._stopped or self.clock() < self._next_send:
            return
        ok = True
        for _ in range(10):            # drain a backlog in batches, then wait for the next turn
            ok, more = await self._send_batch(http)
            if not ok or not more:
                break
        if ok:
            self._backoff = 0.0
            self._next_send = self.clock() + self.interval_s + random.uniform(-self._jitter, self._jitter)
        else:
            self._backoff = min(MAX_BACKOFF_S, max(self.interval_s, self._backoff * 2))
            self._next_send = self.clock() + self._backoff

    async def _send_batch(self, http: httpx.AsyncClient) -> tuple[bool, bool]:
        """(the send worked or the batch was dropped as refused, there is more to send)."""
        lines = self._outbox_lines()
        if not lines or not self.install_id:
            return True, False
        batch = lines[:BATCH]
        body = {"schema": SCHEMA_VERSION, "install_id": self.install_id,
                "dropped": min(self._dropped, 1_000_000), "events": [json.loads(ln) for ln in batch]}
        data = gzip.compress(json.dumps(body, separators=(",", ":")).encode())
        try:
            headers = {"Content-Type": "application/json", "Content-Encoding": "gzip"}
            if self.key:
                headers[KEY_HEADER] = self.key
            r = await http.post(f"{self.url}/v1/batch", content=data, headers=headers)
        except httpx.HTTPError as e:
            self.last_send = {"status": "failed", "error": type(e).__name__, "at": self._stamp()}
            return False, True
        code = r.status_code
        # What Settings > Privacy shows: "ok", or "failed" with why (kept and retried, or refused and dropped)
        self.last_send = ({"status": "ok", "events": len(batch), "at": self._stamp()} if 200 <= code < 300 else
                          {"status": "failed", "error": f"HTTP {code}, will retry" if code == 429 or code >= 500
                           else f"HTTP {code}, refused and dropped", "at": self._stamp()})
        if r.status_code == 429 or r.status_code >= 500:
            return False, True
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
            # The collector refuses this shape; retrying it would be refused forever (spec §6.1)
            field = ""
            with contextlib.suppress(ValueError, AttributeError):
                field = str(r.json().get("field", ""))[:60]
            log.warning("usage stats: the collector refused a batch (%d %s); dropped it", r.status_code, field)
            self._dropped += len(batch)
        self._write_lines(self.data_dir / OUTBOX, rest)
        return True, bool(rest)

    async def _send_deletes(self, http: httpx.AsyncClient) -> None:
        left = []
        for install_id in self._pending_deletes():
            try:
                r = await http.post(f"{self.url}/v1/delete", json={"install_id": install_id})
                done = 200 <= r.status_code < 300
            except httpx.HTTPError:
                done = False
            if done:
                self.last_delete_confirmed = self._stamp()
                log.info("usage stats: the collector confirmed a delete")
            else:
                left.append(install_id)
        path = self.data_dir / PENDING_DELETE
        if left:
            path.write_text("".join(f"{i}\n" for i in left), encoding="utf-8")
        else:
            path.unlink(missing_ok=True)

    def _stamp(self) -> str:
        return dt.datetime.fromtimestamp(self.clock(), dt.UTC).isoformat(timespec="seconds")
