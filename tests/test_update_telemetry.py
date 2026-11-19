"""The `update_step` usage stat: emitted from the real updater paths, categories only, and nothing at all
with stats off. The seamless (staging) half is darwin-only and lives in test_web_seamless_update.py."""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest
import respx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import flackey.web.update
from flackey import __version__, telemetry
from flackey.selfupdate import signature
from flackey.telemetry.client import FILES, UPDATE_PENDING, Telemetry
from flackey.web.update import RELEASES_URL
from tests.appclient import AppClient

from .test_web import FROM_APP, INSTALLER_URL, _release_feed, _settle, make

MAC = {"os": "macos", "os_major": "26", "arch": "arm64"}
INSTALLER_SIG_URL = INSTALLER_URL + ".sig"
ID = "5b0e2c1a-0000-4000-8000-000000000000"


def stats(tmp_path, *, enabled=True, version=__version__) -> Telemetry:
    return Telemetry(tmp_path / "stats", version=version, enabled=enabled, install_id=ID if enabled else None,
                     mode="local", persist=lambda **_: None, channel="checkout", platform_fields=MAC)


def steps(t: Telemetry) -> list[dict]:
    keep = ("stage", "result", "to_version", "trigger", "error_code", "time")
    return [{k: e[k] for k in keep if k in e} for e in t._queue if e["event"] == "update_step"]


@pytest.fixture(autouse=True)
def _keyless_build(monkeypatch):
    monkeypatch.setattr(signature, "baked_public_key", lambda: None)
    monkeypatch.setattr(flackey.web.update, "installer_name", lambda: "Flackey.pkg")


@pytest.fixture
def tel(tmp_path):
    t = stats(tmp_path)
    telemetry.install(t)
    yield t
    telemetry.uninstall()


@pytest.fixture
def client(tmp_path):
    app, _, settings = make(tmp_path)
    return AppClient(app), settings


def up_to_date():
    return [{"draft": False, "prerelease": False, "tag_name": f"v{__version__}", "assets": []}]


# -- the check ------------------------------------------------------------------------------------------------

@respx.mock
def test_an_up_to_date_check_is_none_available(client, tel):
    c, _ = client
    respx.get(RELEASES_URL).mock(return_value=httpx.Response(200, json=up_to_date()))
    c.get("/api/update?trigger=manual")
    [step] = steps(tel)
    assert step["stage"] == "check" and step["result"] == "none_available" and step["trigger"] == "manual"
    assert step["error_code"] == "" and "to_version" not in step and step["time"] == "<1s"


@respx.mock
def test_a_check_that_finds_an_update_names_the_version(client, tel):
    c, _ = client
    respx.get(RELEASES_URL).mock(return_value=httpx.Response(200, json=_release_feed(size=8)))
    c.get("/api/update?trigger=auto")
    assert steps(tel) == [{"stage": "check", "result": "ok", "to_version": "9.9.9", "trigger": "auto",
                           "error_code": "", "time": "<1s"}]


@respx.mock
def test_a_newer_release_without_its_installer_still_counts_as_found(client, tel):
    """`ok` means "a newer release exists"; whether it can be installed is the download's to say."""
    c, _ = client
    respx.get(RELEASES_URL).mock(return_value=httpx.Response(200, json=_release_feed(assets=False)))
    c.get("/api/update?trigger=auto")
    assert [(s["result"], s["to_version"]) for s in steps(tel)] == [("ok", "9.9.9")]


@respx.mock
def test_a_check_that_cannot_reach_github_is_a_network_failure(client, tel):
    c, _ = client
    respx.get(RELEASES_URL).mock(side_effect=httpx.ConnectError("no route"))
    assert c.get("/api/update?trigger=auto").json()["ok"] is False
    assert steps(tel) == [{"stage": "check", "result": "failed", "trigger": "auto", "error_code": "network",
                           "time": "<1s"}]


@pytest.mark.parametrize("answer", [httpx.Response(200, json={"not": "a list"}), httpx.Response(403),
                                    httpx.Response(200, content=b"<html>")])
@respx.mock
def test_a_check_github_answers_badly_is_other(client, tel, answer):
    c, _ = client
    respx.get(RELEASES_URL).mock(return_value=answer)
    c.get("/api/update?trigger=manual")
    assert [(s["result"], s["error_code"]) for s in steps(tel)] == [("failed", "other")]


@respx.mock
def test_a_check_with_no_or_a_made_up_trigger_sends_none(client, tel):
    c, _ = client
    respx.get(RELEASES_URL).mock(return_value=httpx.Response(200, json=up_to_date()))
    c.get("/api/update")
    c.get("/api/update?trigger=https://example.com")
    assert [s.get("trigger") for s in steps(tel)] == [None, None]


@respx.mock
def test_the_lookups_inside_install_and_release_are_not_checks(client, tel, monkeypatch):
    monkeypatch.setattr("flackey.web.update.open_url", lambda url: None)
    c, _ = client
    respx.get(RELEASES_URL).mock(return_value=httpx.Response(200, json=up_to_date()))
    c.post("/api/update/install", headers=FROM_APP)
    c.post("/api/update/release", headers=FROM_APP)
    assert steps(tel) == []


@respx.mock
def test_no_check_is_recorded_with_stats_off(client, tmp_path):
    c, _ = client
    off = stats(tmp_path, enabled=False)
    telemetry.install(off)
    try:
        respx.get(RELEASES_URL).mock(return_value=httpx.Response(200, json=_release_feed(size=8)))
        c.get("/api/update?trigger=auto")
        respx.get(RELEASES_URL).mock(side_effect=httpx.ConnectError("no route"))
        c.get("/api/update?trigger=manual")
    finally:
        telemetry.uninstall()
    assert list(off._queue) == []
    assert not (tmp_path / "stats").exists()


# -- the installer path: download, open, and the next start ------------------------------------------------

@respx.mock
def test_a_downloaded_and_opened_installer_reports_both_and_leaves_a_marker(client, tel, monkeypatch):
    monkeypatch.setattr("flackey.web.update.open_installer", lambda p: None)
    c, _ = client
    respx.get(RELEASES_URL).mock(return_value=httpx.Response(200, json=_release_feed(size=8)))
    respx.get(INSTALLER_URL).mock(return_value=httpx.Response(200, content=b"PKG-DATA"))
    c.post("/api/update/install", headers=FROM_APP)
    _settle(c, "ready")
    got = steps(tel)
    assert [(s["stage"], s["result"], s["error_code"]) for s in got] == [
        ("download", "ok", ""), ("install", "ok", "")]
    assert all(s["to_version"] == "9.9.9" and s["trigger"] == "manual" for s in got)
    assert got[0]["time"] == "<1s"
    marker = json.loads((tel.data_dir / UPDATE_PENDING).read_text())
    assert marker["to"] == "9.9.9" and marker["how"] == "installer"


@respx.mock
def test_a_reopened_installer_is_not_counted_again(client, tel, monkeypatch):
    monkeypatch.setattr("flackey.web.update.open_installer", lambda p: None)
    c, _ = client
    respx.get(RELEASES_URL).mock(return_value=httpx.Response(200, json=_release_feed(size=8)))
    respx.get(INSTALLER_URL).mock(return_value=httpx.Response(200, content=b"PKG-DATA"))
    c.post("/api/update/install", headers=FROM_APP)
    _settle(c, "ready")
    c.post("/api/update/install", headers=FROM_APP)
    assert [s["stage"] for s in steps(tel)] == ["download", "install"]


@respx.mock
def test_a_download_that_dies_partway_is_a_network_failure(client, tel, monkeypatch):
    monkeypatch.setattr("flackey.web.update.open_installer", lambda p: pytest.fail("opened"))

    async def dies_partway():
        yield b"PKG-"
        raise httpx.ReadError("connection went away")

    c, _ = client
    respx.get(RELEASES_URL).mock(return_value=httpx.Response(200, json=_release_feed(size=8)))
    respx.get(INSTALLER_URL).mock(return_value=httpx.Response(200, content=dies_partway()))
    c.post("/api/update/install", headers=FROM_APP)
    _settle(c, "error")
    assert [(s["stage"], s["result"], s["error_code"]) for s in steps(tel)] == [
        ("download", "failed", "network")]
    assert not (tel.data_dir / UPDATE_PENDING).exists()


@pytest.mark.parametrize("answer", [httpx.Response(200, content=b"PKG"), httpx.Response(404)])
@respx.mock
def test_a_short_or_refused_download_is_download_failed(client, tel, monkeypatch, answer):
    monkeypatch.setattr("flackey.web.update.open_installer", lambda p: pytest.fail("opened"))
    c, _ = client
    respx.get(RELEASES_URL).mock(return_value=httpx.Response(200, json=_release_feed(size=8)))
    respx.get(INSTALLER_URL).mock(return_value=answer)
    c.post("/api/update/install", headers=FROM_APP)
    _settle(c, "error")
    assert [(s["stage"], s["result"], s["error_code"]) for s in steps(tel)] == [
        ("download", "failed", "download_failed")]


@respx.mock
def test_a_download_that_cannot_be_saved_is_other(client, tel):
    c, settings = client
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    (settings.data_dir / "updates").write_text("not a folder")
    respx.get(RELEASES_URL).mock(return_value=httpx.Response(200, json=_release_feed(size=8)))
    respx.get(INSTALLER_URL).mock(return_value=httpx.Response(200, content=b"PKG-DATA"))
    c.post("/api/update/install", headers=FROM_APP)
    _settle(c, "error")
    assert [(s["stage"], s["result"], s["error_code"]) for s in steps(tel)] == [("download", "failed", "other")]


def test_a_full_disk_is_no_space():
    import errno
    assert flackey.web.update.download_error(OSError(errno.ENOSPC, "No space left on device")) == "no_space"
    assert flackey.web.update.download_error(PermissionError(errno.EACCES, "denied")) == "other"


@respx.mock
def test_a_cancelled_download_is_cancelled(client, tel, monkeypatch):
    monkeypatch.setattr("flackey.web.update.open_installer", lambda p: None)

    async def cancelled():
        yield b"PKG-"
        raise asyncio.CancelledError()

    c, _ = client
    respx.get(RELEASES_URL).mock(return_value=httpx.Response(200, json=_release_feed(size=8)))
    respx.get(INSTALLER_URL).mock(return_value=httpx.Response(200, content=cancelled()))
    c.post("/api/update/install", headers=FROM_APP)
    _settle(c, "error")
    assert [(s["stage"], s["result"]) for s in steps(tel)] == [("download", "cancelled")]


@respx.mock
def test_an_installer_that_will_not_open_is_install_failed_and_leaves_no_marker(client, tel, monkeypatch):
    def refuses(path):
        raise RuntimeError("LaunchServices said no: /Users/someone/secret")

    monkeypatch.setattr("flackey.web.update.open_installer", refuses)
    c, _ = client
    respx.get(RELEASES_URL).mock(return_value=httpx.Response(200, json=_release_feed(size=8)))
    respx.get(INSTALLER_URL).mock(return_value=httpx.Response(200, content=b"PKG-DATA"))
    c.post("/api/update/install", headers=FROM_APP)
    _settle(c, "ready")
    assert [(s["stage"], s["result"], s["error_code"]) for s in steps(tel)] == [
        ("download", "ok", ""), ("install", "failed", "install_failed")]
    assert not (tel.data_dir / UPDATE_PENDING).exists()
    assert "secret" not in json.dumps(list(tel._queue))


@respx.mock
def test_the_installer_path_sends_nothing_and_writes_nothing_with_stats_off(client, tmp_path, monkeypatch):
    monkeypatch.setattr("flackey.web.update.open_installer", lambda p: None)
    off = stats(tmp_path, enabled=False)
    telemetry.install(off)
    try:
        c, _ = client
        respx.get(RELEASES_URL).mock(return_value=httpx.Response(200, json=_release_feed(size=8)))
        respx.get(INSTALLER_URL).mock(return_value=httpx.Response(200, content=b"PKG-DATA"))
        c.post("/api/update/install", headers=FROM_APP)
        _settle(c, "ready")
    finally:
        telemetry.uninstall()
    assert list(off._queue) == []
    assert not (tmp_path / "stats").exists()


# -- a keyed build's installer is verified ---------------------------------------------------------------------

@pytest.fixture
def keyed(monkeypatch):
    key = Ed25519PrivateKey.generate()
    monkeypatch.setattr(signature, "baked_public_key", lambda: signature.encode_public_key(key.public_key()))
    monkeypatch.setattr("flackey.web.update.open_installer", lambda p: None)
    monkeypatch.setattr("flackey.selfupdate.install.stray_copy", lambda *a, **k: None)
    monkeypatch.setattr("flackey.selfupdate.install.seamless_available", lambda *a, **k: False)
    return key


def serve_pkg(payload: bytes, sig: httpx.Response | Exception) -> None:
    feed = _release_feed(size=len(payload))
    feed[0]["assets"].append({"name": "Flackey.pkg.sig", "size": 129, "browser_download_url": INSTALLER_SIG_URL})
    respx.get(RELEASES_URL).mock(return_value=httpx.Response(200, json=feed))
    respx.get(INSTALLER_URL).mock(return_value=httpx.Response(200, content=payload))
    if isinstance(sig, Exception):
        respx.get(INSTALLER_SIG_URL).mock(side_effect=sig)
    else:
        respx.get(INSTALLER_SIG_URL).mock(return_value=sig)


def pkg_sig(key, payload: bytes, version: str = "9.9.9") -> httpx.Response:
    raw = key.sign(signature.signing_message(version, payload, signature.INSTALLER_DOMAIN))
    return httpx.Response(200, content=raw.hex().encode())


@respx.mock
def test_a_verified_installer_reports_the_signature_check(client, tel, keyed):
    c, _ = client
    serve_pkg(b"PKG-DATA", pkg_sig(keyed, b"PKG-DATA"))
    c.post("/api/update/install", headers=FROM_APP)
    _settle(c, "ready")
    assert [(s["stage"], s["result"]) for s in steps(tel)] == [
        ("download", "ok"), ("verify_signature", "ok"), ("install", "ok")]


@respx.mock
def test_a_bad_installer_signature_is_signature_invalid(client, tel, keyed):
    c, _ = client
    serve_pkg(b"PKG-DATA", pkg_sig(keyed, b"PKG-DATA", version="9.9.8"))
    c.post("/api/update/install", headers=FROM_APP)
    _settle(c, "error")
    assert [(s["stage"], s["result"], s["error_code"]) for s in steps(tel)] == [
        ("download", "ok", ""), ("verify_signature", "failed", "signature_invalid")]
    assert not (tel.data_dir / UPDATE_PENDING).exists()


@respx.mock
def test_a_signature_that_could_not_be_fetched_is_network_not_invalid(client, tel, keyed):
    c, _ = client
    serve_pkg(b"PKG-DATA", httpx.ConnectError("no route"))
    c.post("/api/update/install", headers=FROM_APP)
    _settle(c, "error")
    assert [(s["stage"], s["result"], s["error_code"]) for s in steps(tel)] == [
        ("download", "ok", ""), ("verify_signature", "failed", "network")]


# -- the next start --------------------------------------------------------------------------------------------

def handed_over(tmp_path, how: str, to: str = "9.9.9", at: float = 0.0) -> None:
    t = stats(tmp_path, version="0.6.1")
    t.clock = lambda: at
    t.update_handed_over(to, how)


@pytest.mark.parametrize("how", ["seamless", "installer"])
def test_the_next_start_on_the_new_version_reports_the_restart(tmp_path, how):
    handed_over(tmp_path, how, at=1000.0)
    t = stats(tmp_path, version="9.9.9")
    t.clock = lambda: 1030.0
    t.start_session()
    assert steps(t) == [{"stage": "restart", "result": "ok", "to_version": "9.9.9", "trigger": "manual",
                         "error_code": "", "time": "15-60s"}]
    assert not (t.data_dir / UPDATE_PENDING).exists()


def test_a_seamless_update_that_did_not_take_is_install_failed(tmp_path):
    handed_over(tmp_path, "seamless")
    t = stats(tmp_path, version="0.6.1")
    t.start_session()
    assert [(s["stage"], s["result"], s["error_code"]) for s in steps(t)] == [
        ("restart", "failed", "install_failed")]
    assert not (t.data_dir / UPDATE_PENDING).exists()


def test_an_installer_the_owner_closed_is_not_called_a_failure(tmp_path):
    handed_over(tmp_path, "installer")
    t = stats(tmp_path, version="0.6.1")
    t.start_session()
    assert steps(t) == []
    assert not (t.data_dir / UPDATE_PENDING).exists()


def test_a_marker_is_reported_once(tmp_path):
    handed_over(tmp_path, "seamless")
    stats(tmp_path, version="9.9.9").start_session()
    again = stats(tmp_path, version="9.9.9")
    again.start_session()
    assert steps(again) == []


@pytest.mark.parametrize("raw", ["not json", json.dumps(["9.9.9"]), json.dumps({"to": 9, "how": "seamless"}),
                                 json.dumps({"to": "/Users/x", "how": "seamless", "at": 0})])
def test_a_damaged_marker_is_dropped_without_sending_what_it_held(tmp_path, raw):
    t = stats(tmp_path, version="9.9.9")
    t.data_dir.mkdir(parents=True)
    (t.data_dir / UPDATE_PENDING).write_text(raw)
    t.start_session()
    assert all("to_version" not in s for s in steps(t))
    assert "/Users" not in json.dumps(list(t._queue))
    assert not (t.data_dir / UPDATE_PENDING).exists()


def test_stats_off_writes_no_marker_and_reports_nothing(tmp_path):
    off = stats(tmp_path, enabled=False, version="0.6.1")
    off.update_handed_over("9.9.9", "seamless")
    assert not (tmp_path / "stats").exists()
    off.start_session()
    assert list(off._queue) == []


def test_opting_out_removes_the_marker():
    assert UPDATE_PENDING in FILES


def test_the_module_level_call_is_a_no_op_without_a_client():
    telemetry.uninstall()
    telemetry.update_handed_over("9.9.9", "seamless")
