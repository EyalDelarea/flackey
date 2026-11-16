"""Opt-in usage stats: off means silent, the schema is the collector's, nothing leaves but labels, nothing
goes anywhere but loopback, and opting out deletes."""
from __future__ import annotations

import asyncio
import datetime as dt
import gzip
import hashlib
import json

import httpx
import pytest

from flackey import telemetry
from flackey.telemetry import buckets
from flackey.telemetry.client import (
    FILES,
    OUTBOX,
    PENDING_DELETE,
    SENT,
    STATE,
    Telemetry,
    local_url,
)
from flackey.telemetry.errors import code_location
from flackey.telemetry.schema import SCHEMA_PATH, schema

# The collector's schemas/v1.json.sha256 (EyalDelarea/flackey-telemetry). A change on either side must
# change both, or the collector refuses what the app sends.
COLLECTOR_V1_SHA256 = "b61dc277283d151ef04bd4a981f3c538567f3499e4e48ec52fd2c7c184a4c05e"
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
    """Accepts like the real one: gzip JSON, validated against the same schema."""

    def __init__(self, status: int = 202):
        self.status, self.batches, self.deletes = status, [], []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/delete":
            self.deletes.append(json.loads(request.content)["install_id"])
            return httpx.Response(202, json={})
        assert request.headers["content-encoding"] == "gzip"
        body = json.loads(gzip.decompress(request.content))
        self.batches.append(body)
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
    assert (tmp_path / PENDING_DELETE).read_text().strip() == old
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


def test_source_changes_are_sent_once_an_hour_at_most(tmp_path):
    now = [1000.0]
    t, _ = make(tmp_path, clock=lambda: now[0])
    t.opt_in()
    t.source_status("beatport", "ok")
    t.source_status("beatport", "blocked")
    now[0] += 60
    t.source_status("beatport", "ok")
    changes = [e for e in t.snapshot()["pending"] if e["event"] == "source_status_changed"]
    assert [(e["from"], e["to"]) for e in changes] == [("ok", "blocked")]


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
