from pathlib import Path

from typer.testing import CliRunner

from flackey.cli import app
from flackey.models import RequestKind
from flackey.store import Store

runner = CliRunner()


def _env(tmp_path: Path) -> Path:
    env = tmp_path / ".env"
    env.write_text("TELEGRAM_API_ID=1\nTELEGRAM_API_HASH=h\n"
                   f"LIBRARY_ROOT={tmp_path / 'lib'}\nDATA_DIR={tmp_path / 'data'}\n")
    return env


def test_status_and_export(tmp_path: Path):
    env = _env(tmp_path)
    store = Store(tmp_path / "data" / "flackey.sqlite")
    store.add_request("q", RequestKind.TEXT)
    r = runner.invoke(app, ["--env", str(env), "status"])
    assert r.exit_code == 0 and "queued: 1" in r.output and "tracks: 0" in r.output
    tid = store.add_track(path=tmp_path / "lib" / "x.mp3", fmt="mp3", bitrate_kbps=320, cutoff_hz=20000, file_size=1,
                          artist="A", title="T", mix_name="Original Mix", duration_s=1, isrc=None,
                          catalog_track_id=None, request_id=None)
    store.add_playlist_track(store.upsert_playlist("u", "Goa Set"), tid, 1)
    r = runner.invoke(app, ["--env", str(env), "export"])
    assert r.exit_code == 0 and (tmp_path / "lib" / "Playlists" / "Goa Set.m3u8").exists()


def test_add_rejects_plain_text(tmp_path: Path):
    env = _env(tmp_path)
    r = runner.invoke(app, ["--env", str(env), "add", "Astral Projection - Into the Void"])
    assert r.exit_code == 0 and "YouTube" in r.output
    assert Store(tmp_path / "data" / "flackey.sqlite").list_requests() == []


def test_start_without_browser_does_not_need_pywebview(tmp_path: Path, monkeypatch):
    import sys

    import flackey.app as app_mod

    calls = []

    async def fake_run(settings, open_browser=True, handle=None):
        calls.append(open_browser)

    monkeypatch.setattr(app_mod, "run", fake_run)
    monkeypatch.setitem(sys.modules, "webview", None)  # `import webview` now raises ImportError
    r = runner.invoke(app, ["--env", str(_env(tmp_path)), "start", "--no-browser"])
    assert r.exit_code == 0 and "stopped" in r.output and calls == [False]


def test_start_opens_the_desktop_window_by_default(tmp_path: Path, monkeypatch):
    from flackey import desktop

    opened = []
    monkeypatch.setattr(desktop, "run_in_window", lambda settings: opened.append(settings.web_port))
    r = runner.invoke(app, ["--env", str(_env(tmp_path)), "start"])
    assert r.exit_code == 0 and opened == [8765]


def test_lossless_replay_diffs_the_pick_under_the_current_policy(tmp_path: Path, fixtures: Path):
    import json
    import shutil

    from flackey.lossless import PickPolicy, Reference, pick
    from flackey.source.slskd import parse_response

    env = _env(tmp_path)
    store = Store(tmp_path / "data" / "flackey.sqlite")
    rid = store.add_request("hallucinogen orphic thrench", RequestKind.TEXT)
    responses = json.loads((fixtures / "slskd" / "responses_completed.json").read_text())
    files = [f for r in responses for f in parse_response(r)]
    ref = Reference("Hallucinogen", "Orphic Thrench", "Original Mix", 442, 6025986)
    report = pick(files, ref, PickPolicy())
    aid = store.add_attempt(rid, "soulseek", "Hallucinogen Orphic Thrench")
    raw = tmp_path / "data" / "lossless" / "attempts" / str(aid)
    raw.mkdir(parents=True)
    shutil.copy(fixtures / "slskd" / "responses_completed.json", raw / "02-responses.json")
    store.update_attempt(aid, outcome="filed", report=report.to_dict(), raw_dir=str(raw))

    r = runner.invoke(app, ["--env", str(env), "lossless", "replay", str(rid)])
    assert r.exit_code == 0, r.output
    assert f"stored: {report.chosen.username}" in r.output and f"now:    {report.chosen.username}" in r.output
    assert "same pick" in r.output

    env.write_text(env.read_text() + "LOSSLESS_MAX_QUEUE=-1\n")           # nothing can pass the queue rule now
    r = runner.invoke(app, ["--env", str(env), "lossless", "replay", str(rid)])
    assert r.exit_code == 0 and "no pick now" in r.output and "now:    -" in r.output
    assert "queue" in r.output and "<-" in r.output

    r = runner.invoke(app, ["--env", str(env), "lossless", "replay", "999"])
    assert r.exit_code == 1 and "no attempt" in r.output


def test_lossless_replay_reports_a_structurally_malformed_responses_file(tmp_path: Path):
    import json

    from flackey.lossless import PickPolicy, Reference, pick

    env = _env(tmp_path)
    store = Store(tmp_path / "data" / "flackey.sqlite")
    rid = store.add_request("q", RequestKind.TEXT)
    report = pick([], Reference("A", "T", "Original Mix", 100, 1), PickPolicy())
    aid = store.add_attempt(rid, "soulseek", "q")
    raw = tmp_path / "data" / "lossless" / "attempts" / str(aid)
    raw.mkdir(parents=True)
    (raw / "02-responses.json").write_text(json.dumps({"unexpected": "structure"}))  # valid JSON, wrong shape
    store.update_attempt(aid, outcome="filed", report=report.to_dict(), raw_dir=str(raw))

    r = runner.invoke(app, ["--env", str(env), "lossless", "replay", str(rid)])
    assert r.exit_code == 1, r.output
    assert f"stored attempt {aid} is corrupt" in r.output
    assert r.exception is None or isinstance(r.exception, SystemExit)


def test_sweep_writes_the_report_and_touches_nothing_else(tmp_path: Path, monkeypatch):
    from flackey import sweep as sweep_mod

    env = _env(tmp_path)
    seen = []

    async def fake_run(store, settings, http, *, progress=lambda line: None):
        progress("1  file 0.97  record 1.00  A - B  (matched)")
        seen.append(settings.lossless_fingerprint_min)
        return [sweep_mod.SweepRow(1, "A", "B", 298, 300, 0.97, 1.0, "matched")]

    monkeypatch.setattr(sweep_mod, "run", fake_run)
    out = tmp_path / "sweep.md"
    r = runner.invoke(app, ["--env", str(env), "sweep", "--out", str(out)])
    assert r.exit_code == 0, r.output
    assert seen == [0.79] and f"wrote {out}" in r.output and "A - B" in r.output
    assert "| 0.97 | 1.00 |" in out.read_text()
