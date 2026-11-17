"""Opt-in usage stats: off means silent, the schema is the collector's, nothing leaves but labels, nothing
goes anywhere but loopback, and opting out deletes."""
from __future__ import annotations

import asyncio
import datetime as dt
import gzip
import hashlib
import json
import logging
import re
import sys

import httpx
import pytest

from flackey import telemetry
from flackey.telemetry import buckets
from flackey.telemetry.client import (
    DELETE_RETRY,
    DRAIN_GAP_S,
    FILES,
    MAX_BACKOFF_S,
    OUTBOX,
    PENDING_DELETE,
    SENT,
    STATE,
    Telemetry,
    build_url,
    local_url,
    retry_after_s,
)
from flackey.telemetry.errors import code_location
from flackey.telemetry.schema import SCHEMA_PATH, schema

# The collector's schemas/v1.json.sha256 (EyalDelarea/flackey-telemetry). A change on either side must
# change both, or the collector refuses what the app sends.
COLLECTOR_V1_SHA256 = "f0f0df16fd88cda39e9dddd19a81353164422533f52c9e6a9d410910042b69ce"
MAC = {"os": "macos", "os_major": "26", "arch": "arm64"}
FORBIDDEN = ("name", "title", "artist", "path", "url", "query", "user", "peer", "message", "ip", "email")


def make(tmp_path, *, enabled=False, url="http://127.0.0.1:9", transport=None, **kw):
    saved = {}
    t = Telemetry(tmp_path, version="0.6.1", enabled=enabled, install_id=kw.pop("install_id", None), url=url,
                  persist=lambda **u: saved.update(u), transport=transport, channel="checkout",
                  platform_fields=MAC, **kw)
    return t, saved


def run_tick(t: Telemetry, transport: httpx.MockTransport) -> None:
    async def go():
        async with httpx.AsyncClient(transport=transport) as http:
            await t.tick(http)
    asyncio.run(go())


class Collector:
    """Accepts like the real one: gzip JSON, and owner proof by trust on first use (collector migration 012):
    the first batch registers an ID's secret, a later batch or delete with another secret is 403."""

    def __init__(self, status: int = 202):
        self.status, self.batches, self.deletes = status, [], []
        self.owners: dict[str, str] = {}
        self.delete_secrets: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/delete":
            body = json.loads(request.content)
            assert set(body) == {"install_id", "install_secret"}
            owner = self.owners.get(body["install_id"])
            if owner is not None and owner != body["install_secret"]:
                return httpx.Response(403, json={"error": "not_owner"})
            self.deletes.append(body["install_id"])
            self.delete_secrets.append(body["install_secret"])
            return httpx.Response(202, json={})
        assert request.headers["content-encoding"] == "gzip"
        body = json.loads(gzip.decompress(request.content))
        self.batches.append(body)
        if self.status == 202:
            owner = self.owners.setdefault(body["install_id"], body["install_secret"])
            if owner != body["install_secret"]:
                return httpx.Response(403, json={"error": "not_owner"})
        return httpx.Response(self.status, json={})


def test_schema_is_the_collectors():
    assert hashlib.sha256(SCHEMA_PATH.read_bytes()).hexdigest() == COLLECTOR_V1_SHA256


def test_every_string_field_is_bounded_and_no_field_name_can_hold_content():
    s = schema()
    for event, fields in {"common": s.common, **s.events}.items():
        for key, f in fields.items():
            assert not any(word in key.split("_") for word in FORBIDDEN), (event, key)
            assert f.kind in ("enum", "enum_array", "bool", "int", "uuid4", "date", "regex"), (event, key)
            if f.kind == "enum":
                assert all(len(v) <= 50 and " " not in v.strip() or v in ("club mix",) for v in f.values)


@pytest.mark.parametrize(("fn", "bucket", "samples"), [
    (buckets.dur, "dur", [0, 0.5, 3, 10, 30, 120, 600, 1800, 7200, 30000, 100000, 1e7]),
    (buckets.n, "n", [0, 1, 3, 10, 50, 1000]),
    (buckets.size_mb, "size", [1e6, 20e6, 40e6, 90e6, 500e6]),
    (buckets.length, "length", [60, 200, 360, 500, 900]),
    (buckets.score, "score", [10, 60, 80, 90, 99, 0.97]),
    (buckets.speed_kbps, "speed", [10, 100, 500, 5000]),
    (buckets.edge_khz, "edge", [None, 14000, 15500, 16500, 17500, 18500, 19500, 20000, 21000, 21800, 22050]),
    (buckets.coverage, "coverage", [None, 0.1, 0.3, 0.6, 0.9]),
])
def test_bucket_labels_are_the_schemas(fn, bucket, samples):
    allowed = set(schema().raw["buckets"][bucket])
    assert {fn(x) for x in samples} <= allowed


def test_strength_and_share_labels_are_the_schemas():
    allowed = set(schema().raw["buckets"]["strength"])
    assert {buckets.strength(v, "mp3") for v in (1, 5, 9, 11, 14, 20, 30)} <= allowed
    assert {buckets.strength(v, "opus") for v in (1, 3, 5, 8, 12, 20)} <= allowed
    shares = set(schema().raw["buckets"]["share"])
    assert {buckets.share(0, 10), buckets.share(3, 10), buckets.share(8, 10), buckets.share(95, 100),
            buckets.share(10, 10), buckets.share(0, 0)} <= shares


@pytest.mark.parametrize("url", ["http://127.0.0.1:8790", "http://localhost:8790/", "http://[::1]:8790",
                                 "http://127.0.0.2:1"])
def test_loopback_collectors_are_accepted(url):
    assert local_url(url) is not None


@pytest.mark.parametrize("url", ["https://telemetry.example.com", "http://10.0.0.5:8790",
                                 "http://192.168.1.2", "http://example.com", "http://localhost.evil.com",
                                 "http://user:pw@127.0.0.1:1", "ftp://127.0.0.1", "", None, "127.0.0.1:8790"])
def test_anything_off_this_computer_is_refused(url):
    assert local_url(url) is None


# ---- finding 2: a release build can reach its collector; nothing else can redirect it --------------------------
# Only reserved test names (.example, .invalid): no real host is named anywhere in this repository.
@pytest.mark.parametrize(("raw", "want"), [
    ("https://collector.example", "https://collector.example"),
    ("https://collector.example:8443/base/", "https://collector.example:8443/base"),
])
def test_a_build_s_https_collector_is_accepted(raw, want):
    assert build_url(raw) == want


@pytest.mark.parametrize("raw", ["http://collector.example", "https://user:pw@collector.example",
                                 "https://collector.example/?k=v", "https://collector.example/#x", "https://",
                                 "ftp://collector.example", "collector.example", "", None])
def test_anything_but_plain_https_is_refused_for_a_build(raw):
    assert build_url(raw) is None


def test_the_build_s_collector_and_key_are_used_and_the_key_is_sent(tmp_path):
    seen = []

    def collector(request: httpx.Request) -> httpx.Response:
        seen.append((str(request.url), request.headers.get("x-flackey-key")))
        return httpx.Response(202, json={})

    t, _ = make(tmp_path, url=None, built_url="https://collector.invalid", key="release-key-1")
    assert t.url == "https://collector.invalid" and t.sending is False
    t.opt_in()
    t.emit("app_started", filing_format="aiff")
    run_tick(t, httpx.MockTransport(collector))
    assert seen and all(u == "https://collector.invalid/v1/batch" and k == "release-key-1" for u, k in seen)


def test_the_environment_may_only_point_at_this_computer_even_in_a_keyed_build(tmp_path):
    t, _ = make(tmp_path, url="http://127.0.0.1:8790", built_url="https://collector.invalid", key="k")
    assert t.url == "http://127.0.0.1:8790", "the local demo overrides the build's address"
    t, _ = make(tmp_path, url="https://elsewhere.invalid", built_url="https://collector.invalid", key="k")
    assert t.url is None, "a refused override sends nothing rather than quietly falling back"


def test_a_stop_is_never_shown_as_sent(tmp_path):
    """Final review 2026-11-16: with a key the collector does not accept, it answers 202 {"stop": true} and
    stores nothing. The app used to record that batch as sent."""
    def stop(_request):
        return httpx.Response(202, json={"stop": True})

    t, _ = make(tmp_path)
    t.opt_in()
    t.emit("app_started", filing_format="aiff")
    run_tick(t, httpx.MockTransport(stop))
    assert t.snapshot()["sent"] == []
    assert t.describe()["last_send"]["status"] == "failed"
    assert "stop" in t.describe()["last_send"]["error"]
    assert t._stopped and t._dropped > 0


def test_build_json_carries_the_collector_only_when_the_build_wrote_one(tmp_path):
    from flackey.config import build_telemetry

    assert build_telemetry(tmp_path / "absent.json") == (None, None)
    p = tmp_path / "build.json"
    p.write_text(json.dumps({"telegram_api_id": 1, "telegram_api_hash": "h"}))
    assert build_telemetry(p) == (None, None)
    p.write_text(json.dumps({"telemetry_url": "https://collector.example", "telemetry_key": "r1-key"}))
    assert build_telemetry(p) == ("https://collector.example", "r1-key")
    p.write_text("not json")
    assert build_telemetry(p) == (None, None)


def test_from_settings_reads_the_build_and_lets_the_environment_override(tmp_path, monkeypatch):
    from types import SimpleNamespace

    b = tmp_path / "build.json"
    b.write_text(json.dumps({"telemetry_url": "https://collector.example", "telemetry_key": "r1-key"}))
    monkeypatch.setattr("flackey.config.BUILD_DEFAULTS_PATH", b)
    s = SimpleNamespace(data_dir=tmp_path, telemetry_enabled=False, telemetry_install_id=None,
                        telemetry_opted_in=None, telemetry_asked=False)
    t = telemetry.from_settings(s, "0.6.1", environ={})
    assert (t.url, t.key) == ("https://collector.example", "r1-key")
    t = telemetry.from_settings(s, "0.6.1", environ={"FLACKEY_TELEMETRY_URL": "http://127.0.0.1:8790",
                                                     "FLACKEY_TELEMETRY_KEY": "demo-key"})
    assert (t.url, t.key) == ("http://127.0.0.1:8790", "demo-key")


def test_no_real_collector_host_is_named_in_the_app():
    """Finding 2's rule: the address and key come from CI secrets at build time, never from the repository."""
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    hosts = set()
    for path in [*(root / "src" / "flackey" / "telemetry").rglob("*.py"), root / "src" / "flackey" / "config.py"]:
        hosts |= set(re.findall(r"https://([A-Za-z0-9.-]+)", path.read_text(encoding="utf-8")))
    assert {h for h in hosts if not h.endswith((".example", ".invalid"))} == set()


def test_off_is_silent_and_writes_nothing(tmp_path):
    t, saved = make(tmp_path)
    t.emit("app_started", filing_format="aiff")
    t.health_tick(10, soulseek=True)
    t.source_status("beatport", "ok")
    assert t.start_session() == {}
    t.end_session()
    collector = Collector()
    run_tick(t, httpx.MockTransport(collector))
    assert list(tmp_path.iterdir()) == []
    assert collector.batches == [] and collector.deletes == [] and saved == {}
    assert t.notes(1) == {} and t._notes == {}


def test_module_level_calls_are_no_ops_without_a_client():
    telemetry.uninstall()
    telemetry.emit("app_started")
    assert telemetry.notes(1) == {} and not telemetry.enabled()


def test_env_off_overrides_consent(tmp_path):
    t, _ = make(tmp_path, enabled=True, install_id="5b0e2c1a-0000-4000-8000-000000000000", mode="off")
    assert not t.enabled
    with pytest.raises(ValueError):
        t.opt_in()


def test_opt_in_makes_a_random_id_and_sends_valid_batches(tmp_path):
    collector = Collector()
    t, saved = make(tmp_path)
    t.opt_in()
    assert saved["telemetry_enabled"] is True
    assert saved["telemetry_install_id"] == t.install_id and len(t.install_id) == 36
    t.emit("request_finished", kind="yt_track", state="done", total_time=buckets.dur(400), passes=1)
    run_tick(t, httpx.MockTransport(collector))
    (batch,) = collector.batches
    assert batch["schema"] == 1 and batch["install_id"] == t.install_id and batch["dropped"] == 0
    (ev,) = batch["events"]
    assert ev["event"] == "request_finished" and ev["total_time"] == "5-15m" and ev["channel"] == "checkout"
    assert schema().check_common(ev) is None
    assert (tmp_path / OUTBOX).read_text() == ""
    assert json.loads((tmp_path / SENT).read_text().splitlines()[0]) == ev
    assert t.last_send["status"] == "ok" and t.last_send["events"] == 1   # what Settings > Privacy reads


def test_content_never_survives_the_schema(tmp_path):
    t, _ = make(tmp_path)
    t.opt_in()
    t.emit("request_finished", kind="yt_track", state="done", title="Daft Punk - One More Time",
           failed_stage="/Users/me/Music/DJ Library/x.aiff", error_class="Bad error: path /Users/me",
           peer="someone", url="https://youtu.be/abc")
    ev = t.snapshot()["pending"][0]
    blob = json.dumps(ev)
    for leaked in ("Daft", "/Users", "someone", "youtu", "Bad error"):
        assert leaked not in blob
    assert ev["kind"] == "yt_track"


def test_a_refused_batch_is_dropped_and_a_down_collector_keeps_it(tmp_path):
    t, _ = make(tmp_path)
    t.opt_in()
    t.emit("app_started", filing_format="aiff")
    down = Collector(status=503)
    run_tick(t, httpx.MockTransport(down))
    assert len(down.batches) == 1 and (tmp_path / OUTBOX).read_text().strip()
    assert t._next_send > t.clock()            # backed off, kept
    assert t.last_send["status"] == "failed" and "retry" in t.last_send["error"]
    t._next_send = 0
    refused = Collector(status=400)
    run_tick(t, httpx.MockTransport(refused))
    assert (tmp_path / OUTBOX).read_text() == "" and t._dropped == 1


def test_no_collector_means_the_outbox_only(tmp_path):
    t, _ = make(tmp_path, url=None)
    t.opt_in()
    t.emit("app_started", filing_format="aiff")
    collector = Collector()
    run_tick(t, httpx.MockTransport(collector))
    assert collector.batches == [] and (tmp_path / OUTBOX).read_text().strip()


def test_opt_out_deletes_everything_here_and_asks_the_collector_to_delete(tmp_path):
    collector = Collector()
    t, saved = make(tmp_path)
    t.opt_in()
    old = t.install_id
    t.emit("app_started", filing_format="aiff")
    run_tick(t, httpx.MockTransport(collector))
    t.opt_out()
    assert saved["telemetry_enabled"] is False and saved["telemetry_install_id"] is None
    assert (tmp_path / PENDING_DELETE).read_text().split()[0] == old
    for name in (OUTBOX, SENT, STATE):
        assert not (tmp_path / name).exists()
    run_tick(t, httpx.MockTransport(collector))
    assert collector.deletes == [old]
    assert not (tmp_path / PENDING_DELETE).exists() and t.last_delete_confirmed
    assert sorted(p.name for p in tmp_path.iterdir()) == []
    assert set(FILES) >= {OUTBOX, SENT, PENDING_DELETE, STATE}


def test_new_id_deletes_the_old_ones_data(tmp_path):
    collector = Collector()
    t, _ = make(tmp_path)
    t.opt_in()
    old = t.install_id
    t.reset_id()
    assert t.install_id != old
    run_tick(t, httpx.MockTransport(collector))
    assert collector.deletes == [old]


def test_a_crashed_session_and_yesterdays_health(tmp_path):
    day = [dt.date(2026, 11, 15)]
    t, _ = make(tmp_path, today=lambda: day[0])
    t.opt_in()                       # session 1, never ended: a crash
    t.health_tick(3600, soulseek=True, telegram=None, beatport=False)
    t._save_state()
    day[0] = dt.date(2026, 11, 16)
    t2, _ = make(tmp_path, enabled=True, install_id=t.install_id, today=lambda: day[0])
    info = t2.start_session()
    assert info["previous_exit"] == "crash"
    (health,) = [e for e in t2.snapshot()["pending"] if e["event"] == "health_daily"]
    assert health["event_date"] == "2026-11-15" and health["uptime"] == "1-6h"
    assert health["soulseek_up"] == "100%" and health["telegram_up"] == "off" and health["beatport_up"] == "0"
    assert health["crashed_sessions"] == 0 and health["sessions"] == 1
    t2.end_session()
    t3, _ = make(tmp_path, enabled=True, install_id=t.install_id, today=lambda: day[0])
    assert t3.start_session()["previous_exit"] == "clean"


def test_a_recovery_inside_the_hour_is_sent_so_a_source_never_reads_down_while_up(tmp_path):
    """Audit 2026-11-16: Soulseek went ok -> not_logged_in, signed back in a minute later, and the recovery
    was never sent, so the dashboard showed it down while it filed three tracks."""
    now = [1000.0]
    t, _ = make(tmp_path, clock=lambda: now[0])
    t.opt_in()
    t.source_status("soulseek", "ok")
    t.source_status("soulseek", "not_logged_in")
    now[0] += 60
    t.source_status("soulseek", "ok")
    changes = [e for e in t.snapshot()["pending"] if e["event"] == "source_status_changed"]
    assert [(e["from"], e["to"]) for e in changes] == [("ok", "not_logged_in"), ("not_logged_in", "ok")]


def test_a_source_going_down_says_why_in_a_code_and_a_recovery_does_not(tmp_path):
    now = [1000.0]
    t, _ = make(tmp_path, clock=lambda: now[0])
    t.opt_in()
    t.source_status("beatport", "ok", reason="unknown")
    t.source_status("beatport", "blocked", reason="cloudflare_challenge")
    now[0] += 60
    t.source_status("beatport", "ok", reason="cloudflare_challenge")
    changes = [e for e in t.snapshot()["pending"] if e["event"] == "source_status_changed"]
    assert [(e["to"], e.get("reason")) for e in changes] == [("blocked", "cloudflare_challenge"), ("ok", None)]


def test_source_changes_are_held_to_an_hour_and_the_held_state_is_sent_when_it_is_up(tmp_path):
    now = [1000.0]
    t, _ = make(tmp_path, clock=lambda: now[0])
    t.opt_in()
    t.source_status("beatport", "ok")
    for _ in range(5):                       # flapping: the drop, one recovery, then held back
        t.source_status("beatport", "blocked")
        now[0] += 60
        t.source_status("beatport", "ok")
        now[0] += 60
    t.source_status("beatport", "blocked")   # where it ends inside the hour

    def sent():
        return [(e["from"], e["to"]) for e in t.snapshot()["pending"] if e["event"] == "source_status_changed"]

    assert sent() == [("ok", "blocked"), ("blocked", "ok")]
    now[0] += 3600
    t.source_status("beatport", "blocked")   # the next watcher tick after the hour
    t.source_status("beatport", "blocked")
    assert sent() == [("ok", "blocked"), ("blocked", "ok"), ("ok", "blocked")]


def test_crash_location_is_code_coordinates_only():
    def boom():
        raise KeyError("/Users/me/Music/secret track.aiff")
    try:
        boom()
    except KeyError as e:
        loc = code_location(e.__traceback__)
    assert loc["module"].startswith("tests") or loc["module"] == "test_telemetry"
    assert "secret" not in json.dumps(loc)


def test_a_full_outbox_drops_the_oldest(tmp_path, monkeypatch):
    monkeypatch.setattr("flackey.telemetry.client.MAX_OUTBOX_EVENTS", 3)
    t, _ = make(tmp_path, url=None)
    t.opt_in()
    for i in range(5):
        t.emit("request_action", action="retry", count=buckets.n(i))
    t._flush()
    assert len((tmp_path / OUTBOX).read_text().splitlines()) == 3 and t._dropped >= 2


def test_a_build_without_the_schema_file_just_has_no_stats(tmp_path, monkeypatch):
    def missing():
        raise FileNotFoundError("telemetry_schema_v1.json")
    monkeypatch.setattr("flackey.telemetry.client.schema", missing)
    t, _ = make(tmp_path, enabled=True, install_id="5b0e2c1a-0000-4000-8000-000000000000")
    assert not t.available and not t.enabled
    t.emit("app_started")
    assert list(tmp_path.iterdir()) == []


def test_usage_stats_are_off_and_unasked_by_default(tmp_path):
    """A fresh install: stats off, never asked -- so an existing owner gets the one-time prompt, and nothing
    is ever on until the owner turns it on."""
    from flackey.config import load_settings

    env = tmp_path / ".env"
    env.write_text(f"DATA_DIR={tmp_path / 'data'}\n")
    s = load_settings(env)
    assert s.telemetry_enabled is False and s.telemetry_asked is False
    t = telemetry.from_settings(s, "0.6.1", environ={"FLACKEY_TELEMETRY_URL": "http://127.0.0.1:9"})
    d = t.describe()
    assert d["enabled"] is False and d["asked"] is False and d["id_prefix"] is None


def test_answering_the_question_is_remembered_and_never_turns_stats_on(tmp_path):
    """Not now (or a dismissed prompt) persists `telemetry_asked` and leaves stats off; opting out later
    keeps it, so the one-time prompt does not come back after a Settings round trip."""
    from functools import partial

    from flackey.config import load_settings, save_settings

    env = tmp_path / ".env"
    env.write_text(f"DATA_DIR={tmp_path / 'data'}\n")
    s = load_settings(env)
    # The envelope is pinned to macOS: on Linux (CI) the schema has no os_major, so stats are unavailable.
    t = Telemetry(s.data_dir, version="0.6.1", url="http://127.0.0.1:9", channel="checkout",
                  platform_fields={"os": "macos", "os_major": "26", "arch": "arm64"},
                  persist=partial(save_settings, s))
    t.mark_asked()
    assert t.asked and not t.enabled
    again = load_settings(env)
    assert again.telemetry_asked is True and again.telemetry_enabled is False
    t.opt_in()
    t.opt_out()
    assert load_settings(env).telemetry_asked is True
    assert telemetry.from_settings(load_settings(env), "0.6.1", environ={}).describe()["asked"] is True


class Clock:
    def __init__(self, now: float = 1_800_000_000.0):
        self.now = now

    def __call__(self) -> float:
        return self.now


def test_a_refused_delete_backs_off_keeps_trying_across_restarts_and_says_so(tmp_path):
    """Security review M5: a 403 (a geo-block) on /v1/delete used to be retried every 5 s forever. Now it
    doubles from a minute to the cap, survives a restart without starting over, and Privacy can say so."""
    clock = Clock()
    calls: list[str] = []
    status = [403]

    def collector(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(status[0], json={})

    t, _ = make(tmp_path, clock=clock, interval_s=60)      # an explicit interval turns jitter off
    t.opt_in()
    old = t.install_id
    t.opt_out()
    run_tick(t, httpx.MockTransport(collector))
    assert calls == ["/v1/delete"]
    retry = json.loads((tmp_path / DELETE_RETRY).read_text())
    assert retry["attempts"] == 1 and retry["error"] == "HTTP 403" and retry["next"] == clock.now + 60
    d = t.describe()
    assert d["pending_delete"] and d["delete_retry"]["attempts"] == 1 and d["delete_retry"]["error"] == "HTTP 403"

    for _ in range(11):                                    # ticks inside the wait send nothing
        clock.now += 5
        run_tick(t, httpx.MockTransport(collector))
    assert calls == ["/v1/delete"]

    waits = []
    for _ in range(12):                                    # each failure doubles the wait, up to the cap
        clock.now = json.loads((tmp_path / DELETE_RETRY).read_text())["next"]
        run_tick(t, httpx.MockTransport(collector))
        waits.append(json.loads((tmp_path / DELETE_RETRY).read_text())["next"] - clock.now)
    assert waits[:4] == [120, 240, 480, 960] and max(waits) == MAX_BACKOFF_S and waits[-1] == MAX_BACKOFF_S
    assert len(calls) == 13

    # A restart: the owed delete and its wait are both still there, so nothing goes early...
    t2, _ = make(tmp_path, clock=clock, interval_s=60)
    run_tick(t2, httpx.MockTransport(collector))
    assert len(calls) == 13 and t2.describe()["pending_delete"]
    # ...and once the collector takes it, everything is gone.
    status[0] = 202
    clock.now += MAX_BACKOFF_S
    run_tick(t2, httpx.MockTransport(collector))
    assert len(calls) == 14 and t2.last_delete_confirmed
    assert not (tmp_path / PENDING_DELETE).exists() and not (tmp_path / DELETE_RETRY).exists()
    assert t2.describe()["delete_retry"] is None and old


def test_a_delete_waits_for_retry_after_and_backoff_is_jittered(tmp_path):
    clock = Clock()
    t, _ = make(tmp_path, clock=clock)                     # default interval: jitter on
    t.opt_in()
    t.opt_out()
    run_tick(t, httpx.MockTransport(lambda r: httpx.Response(429, headers={"Retry-After": "3600"})))
    assert json.loads((tmp_path / DELETE_RETRY).read_text())["next"] == clock.now + 3600
    (tmp_path / DELETE_RETRY).unlink()
    run_tick(t, httpx.MockTransport(lambda r: httpx.Response(503)))
    wait = json.loads((tmp_path / DELETE_RETRY).read_text())["next"] - clock.now
    assert 48 <= wait <= 72                                # a minute, give or take 20%


def test_retry_after_reads_seconds_and_http_dates():
    now = 1_800_000_000.0
    r = lambda v: httpx.Response(429, headers={"Retry-After": v})
    assert retry_after_s(r("120"), now) == 120
    assert retry_after_s(r("99999999"), now) == MAX_BACKOFF_S
    date = dt.datetime.fromtimestamp(now + 600, dt.UTC).strftime("%a, %d %b %Y %H:%M:%S GMT")
    assert retry_after_s(r(date), now) == 600
    assert retry_after_s(r("soon"), now) is None and retry_after_s(httpx.Response(429), now) is None


def test_a_backlog_drains_one_batch_at_a_time_a_few_a_minute(tmp_path):
    """Security review L6: a backlog used to leave as up to 10 batches in one burst."""
    clock = Clock()
    collector = Collector()
    t, _ = make(tmp_path, clock=clock, interval_s=900)
    t.opt_in()
    for _ in range(450):
        t.emit("app_started", filing_format="aiff")
    run_tick(t, httpx.MockTransport(collector))
    assert len(collector.batches) == 1 and t._next_send == clock.now + DRAIN_GAP_S
    sent_at = [clock.now]
    for _ in range(12):                                    # a minute of 5 s ticks
        clock.now += 5
        before = len(collector.batches)
        run_tick(t, httpx.MockTransport(collector))
        if len(collector.batches) > before:
            sent_at.append(clock.now)
    assert len(collector.batches) == 3 and sent_at[1] - sent_at[0] >= DRAIN_GAP_S
    assert sum(len(b["events"]) for b in collector.batches) >= 450
    assert t._next_send == sent_at[-1] + 900               # drained: back to the normal interval


def test_a_down_collector_backs_off_and_honours_retry_after(tmp_path):
    clock = Clock()
    t, _ = make(tmp_path, clock=clock, interval_s=60)
    t.opt_in()
    t.emit("app_started", filing_format="aiff")
    run_tick(t, httpx.MockTransport(lambda r: httpx.Response(503, headers={"Retry-After": "7200"})))
    assert t._next_send == clock.now + 7200 and (tmp_path / OUTBOX).read_text().strip()
    clock.now = t._next_send

    def unreachable(request):
        raise httpx.ConnectError("down")
    run_tick(t, httpx.MockTransport(unreachable))
    assert t._next_send == clock.now + 120                 # doubled, kept
    for _ in range(20):
        clock.now = t._next_send
        run_tick(t, httpx.MockTransport(unreachable))
    assert t._next_send - clock.now == MAX_BACKOFF_S and (tmp_path / OUTBOX).read_text().strip()


# --- the install secret (owner proof, collector migration 012) ---------------------------------------------

SECRET_SHAPE = re.compile(r"^[A-Za-z0-9_-]{43}$")


def test_opt_in_makes_a_secret_saves_it_and_sends_it_with_every_batch(tmp_path):
    collector = Collector()
    t, saved = make(tmp_path)
    t.opt_in()
    assert SECRET_SHAPE.fullmatch(t.install_secret)
    assert saved["telemetry_install_secret"] == t.install_secret
    t.emit("app_started", filing_format="aiff")
    run_tick(t, httpx.MockTransport(collector))
    assert collector.batches[0]["install_secret"] == t.install_secret
    assert collector.owners == {t.install_id: t.install_secret}
    other, _ = make(tmp_path / "b")
    other.opt_in()
    assert other.install_secret != t.install_secret


def test_opt_out_wipes_the_secret_and_the_delete_carries_it(tmp_path):
    collector = Collector()
    t, saved = make(tmp_path)
    t.opt_in()
    old, old_secret = t.install_id, t.install_secret
    t.emit("app_started", filing_format="aiff")
    run_tick(t, httpx.MockTransport(collector))
    t.opt_out()
    assert t.install_secret is None and saved["telemetry_install_secret"] is None
    run_tick(t, httpx.MockTransport(collector))
    assert collector.deletes == [old] and collector.delete_secrets == [old_secret]


def test_new_id_makes_a_new_secret_and_the_pending_delete_keeps_the_old_pair(tmp_path):
    """The delete owed after New ID must prove ownership of the OLD ID, so it goes out with the old secret,
    even after a refused try and a restart; the new ID's batches carry the new secret."""
    clock = Clock()
    collector = Collector()
    t, saved = make(tmp_path, clock=clock, interval_s=60)
    t.opt_in()
    old, old_secret = t.install_id, t.install_secret
    t.emit("app_started", filing_format="aiff")
    run_tick(t, httpx.MockTransport(collector))               # the old pair is registered
    t.reset_id()
    assert t.install_secret != old_secret and SECRET_SHAPE.fullmatch(t.install_secret)
    assert saved["telemetry_install_id"] == t.install_id and saved["telemetry_install_secret"] == t.install_secret
    run_tick(t, httpx.MockTransport(lambda r: httpx.Response(503)))   # the delete fails once
    assert (tmp_path / PENDING_DELETE).read_text() == f"{old} {old_secret}\n"
    t2, _ = make(tmp_path, clock=clock, interval_s=60, enabled=True, install_id=t.install_id,
                 install_secret=t.install_secret)            # a restart
    clock.now += MAX_BACKOFF_S
    t2.emit("app_started", filing_format="aiff")
    run_tick(t2, httpx.MockTransport(collector))
    assert collector.deletes == [old] and collector.delete_secrets == [old_secret]
    assert collector.batches[-1]["install_id"] == t.install_id
    assert collector.batches[-1]["install_secret"] == t.install_secret


def test_a_delete_with_the_wrong_secret_stays_owed(tmp_path):
    collector = Collector()
    t, _ = make(tmp_path, interval_s=60)
    t.opt_in()
    collector.owners[t.install_id] = "x" * 43                 # someone else registered this ID first
    t.opt_out()
    run_tick(t, httpx.MockTransport(collector))
    assert collector.deletes == [] and t.describe()["pending_delete"]
    assert t.describe()["delete_retry"]["error"] == "HTTP 403"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX file modes")
def test_the_pending_delete_file_is_private(tmp_path):
    t, _ = make(tmp_path)
    t.opt_in()
    t.opt_out()
    assert (tmp_path / PENDING_DELETE).stat().st_mode & 0o777 == 0o600


def test_a_pending_line_without_a_secret_is_dropped(tmp_path):
    (tmp_path / PENDING_DELETE).write_text("5b0e2c1a-0000-4000-8000-000000000000\n", encoding="utf-8")
    t, _ = make(tmp_path)
    assert t._pending_deletes() == [] and not t.describe()["pending_delete"]


def test_an_install_opted_in_before_the_secret_gets_one(tmp_path):
    t, saved = make(tmp_path, enabled=True, install_id="5b0e2c1a-0000-4000-8000-000000000000")
    assert SECRET_SHAPE.fullmatch(t.install_secret) and saved == {"telemetry_install_secret": t.install_secret}


def test_from_settings_reads_the_secret(tmp_path):
    from types import SimpleNamespace

    s = SimpleNamespace(data_dir=tmp_path, telemetry_enabled=True,
                        telemetry_install_id="5b0e2c1a-0000-4000-8000-000000000000",
                        telemetry_install_secret="s" * 43, telemetry_opted_in=None, telemetry_asked=True)
    assert telemetry.from_settings(s, "0.6.1", environ={}).install_secret == "s" * 43


def test_the_secret_is_never_shown_or_logged(tmp_path, caplog):
    """Not in Settings > Privacy (describe), not in What's sent (snapshot), not in any log line, through
    a send, a refusal, a failing delete and a confirmed one."""
    caplog.set_level(logging.DEBUG)
    collector = Collector()
    t, _ = make(tmp_path, interval_s=60)
    t.opt_in()
    secrets_seen = [t.install_secret]
    t.emit("app_started", filing_format="aiff")
    run_tick(t, httpx.MockTransport(collector))
    t.emit("app_started", filing_format="aiff")
    t._next_send = 0
    run_tick(t, httpx.MockTransport(lambda r: httpx.Response(403, json={"error": "not_owner"})))
    shown = json.dumps(t.describe()) + json.dumps(t.snapshot())
    t.reset_id()
    secrets_seen.append(t.install_secret)
    run_tick(t, httpx.MockTransport(lambda r: httpx.Response(403)))
    t.opt_out()
    (tmp_path / DELETE_RETRY).unlink(missing_ok=True)
    run_tick(t, httpx.MockTransport(collector))
    shown += json.dumps(t.describe())
    for secret in secrets_seen:
        assert secret not in shown
        assert secret not in caplog.text


def test_the_privacy_api_never_returns_the_secret(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from flackey.web import telemetry as telemetry_web

    t, _ = make(tmp_path)
    telemetry.install(t)
    try:
        app = FastAPI()
        app.include_router(telemetry_web.router())
        c = TestClient(app)
        on = c.put("/api/telemetry", json={"enabled": True}).text
        secret = t.install_secret
        bodies = on + c.get("/api/telemetry").text + c.get("/api/telemetry/log").text
        bodies += c.get("/api/telemetry/catalog").text + c.post("/api/telemetry/new-id").text
        assert secret not in bodies and t.install_secret not in bodies
    finally:
        telemetry.uninstall()
