import asyncio
import json
import logging
import subprocess
import sys
import time
import types
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import pytest
import respx

import flackey.web.update
from flackey import __version__
from flackey.config import Settings, default_filing_format
from flackey.deezer import DeezerApi
from flackey.events import EventBus, Status
from flackey.inbox import Inbox
from flackey.models import Candidate, CatalogTrack, RequestKind, RequestState
from flackey.notify import MemoryNotifier
from flackey.selfupdate import signature as _signature
from flackey.store import Store
from flackey.web import create_app
from flackey.web.update import RELEASES_URL
from flackey.worker import Worker
from flackey.youtube import YouTubeEntry, YouTubeError
from tests.appclient import AppClient


class DummySource:
    name = "x"

    async def search(self, q):
        return []

    async def fetch(self, c, d):
        raise NotImplementedError


class DummyCatalog:
    async def search(self, q):
        return []


async def fake_youtube(url: str):
    if "bad" in url:
        raise YouTubeError("boom")
    return "Astral Projection - Into The Void", [
        YouTubeEntry(url, "Astral Projection - Into The Void", "Astral Projection - Topic", 442)]


def make(tmp_path: Path, **kw):
    settings = Settings(_env_file=None, telegram_api_id=1, telegram_api_hash="h",
                        library_root=tmp_path / "lib", data_dir=tmp_path / "data")
    store = Store(settings.db_path)
    worker = Worker(store, DummySource(), DummyCatalog(), MemoryNotifier(), settings)
    return create_app(store, worker, Inbox(store, youtube=fake_youtube), settings, **kw), store, settings


@pytest.fixture(autouse=True)
def _keyless_build(monkeypatch):
    """A build with no release key, whose updates take the plain installer path; a keyed build's installer
    must verify, which `test_web_seamless_update.py` covers."""
    monkeypatch.setattr("flackey.selfupdate.signature.baked_public_key", lambda: None)


@pytest.fixture
def client(tmp_path: Path):
    app, store, settings = make(tmp_path)
    return AppClient(app), store, settings


@pytest.fixture
def client_with_worker(tmp_path: Path):
    """The uploads route reads `worker.providers`, so this hands the worker back to the test."""
    settings = Settings(_env_file=None, telegram_api_id=1, telegram_api_hash="h",
                        library_root=tmp_path / "lib", data_dir=tmp_path / "data")
    store = Store(settings.db_path)
    worker = Worker(store, DummySource(), DummyCatalog(), MemoryNotifier(), settings)
    return AppClient(create_app(store, worker, Inbox(store, youtube=fake_youtube), settings)), worker


def test_health(client):
    c, _, _ = client
    from flackey.config import platform_name
    from flackey.fingerprint import fpcalc_available
    assert c.get("/api/health").json() == {"ok": True, "version": __version__, "platform": platform_name(),
                                          "telegram_authorized": True,
                                          "worker_running": False, "setup_done": False,
                                          "telegram_configured": True, "source_enabled": True,
                                          "sharing": None,
                                          "lossless": {"enabled": False, "provider": None, "fpcalc": fpcalc_available(),
                                                       "attempts_24h": {}, "raw_mb": 0.0}}


@pytest.mark.parametrize("raw,word", [("darwin", "mac"), ("win32", "windows"), ("linux", "linux")])
def test_health_says_which_system_the_page_is_running_on(client, monkeypatch, raw, word):
    """The page draws a Mac title bar or a Windows one from this, and nothing inside WebView2 can tell."""
    c, _, _ = client
    # Health also looks for fpcalc, and `shutil.which` on a pretend Windows reaches for `_winapi`.
    monkeypatch.setattr("flackey.web.fpcalc_available", lambda: False)
    monkeypatch.setattr(sys, "platform", raw)
    assert c.get("/api/health").json()["platform"] == word


def test_health_reflects_shared_status(tmp_path):
    status = Status(None, telegram_authorized=False, worker_running=False, setup_done=True)
    app, _, _ = make(tmp_path, status=status)
    c = AppClient(app)
    assert c.get("/api/health").json()["telegram_authorized"] is False
    status["telegram_authorized"] = True
    assert c.get("/api/health").json()["telegram_authorized"] is True


@respx.mock
def test_update_reports_new_installer(client):
    c, _, _ = client
    respx.get(RELEASES_URL).mock(return_value=httpx.Response(200, json=[{
        "draft": False, "prerelease": False, "tag_name": "v9.9.9",
        "published_at": "2026-09-15T10:00:00Z", "html_url": "https://github.com/EyalDelarea/flackey/releases/tag/v9.9.9",
        "assets": [{"name": "Flackey.pkg", "size": 12345678,
                    "browser_download_url": "https://github.com/EyalDelarea/flackey/releases/download/v9.9.9/Flackey.pkg"}],
    }]))
    assert c.get("/api/update").json() == {
        "ok": True, "current": __version__, "newer": True, "available": True, "verifiable": True, "latest": "9.9.9",
        "url": "https://github.com/EyalDelarea/flackey/releases/download/v9.9.9/Flackey.pkg", "release_url": "https://github.com/EyalDelarea/flackey/releases/tag/v9.9.9",
        "size": 12345678, "size_label": "12.3 MB", "published_at": "2026-09-15T10:00:00Z",
        "published_date": "2026-09-15", "prerelease": False, "installer_signature_url": None,
        # A release carrying only the pkg: there is nothing signed to install in place, so the press
        # leads to Installer.app exactly as it did before.
        "seamless": False, "archive_url": None, "archive_size": None, "signature_url": None,
    }


@respx.mock
def test_update_says_current_release_is_up_to_date(client):
    c, _, _ = client
    respx.get(RELEASES_URL).mock(return_value=httpx.Response(200, json=[{
        "draft": False, "prerelease": False, "tag_name": "v0.1.0",
        "published_at": "2026-09-15T10:00:00Z",
        "assets": [{"name": "Flackey.pkg", "size": 123,
                    "browser_download_url": "https://github.com/EyalDelarea/flackey/releases/download/v9.9.9/Flackey.pkg"}],
    }]))
    body = c.get("/api/update").json()
    assert body["ok"] is True and body["newer"] is False and body["available"] is False and body["latest"] == "0.1.0"


@respx.mock
def test_update_flags_newer_release_missing_its_installer(client):
    # Reproduces the v0.1.3 incident: a release was tagged and published (so its tag sorts newer than
    # the running version) but the installer job failed before an asset was attached. "Up to date" would
    # be a lie here -- the fix is to say a newer version exists without offering a dead-end download.
    # The mocked tag bumps the running version's own patch number, so this stays true regardless of
    # what __version__ happens to be (a plain release-day version bump shouldn't break this test).
    major, minor, patch = (int(p) for p in __version__.split("."))
    newer_version = f"{major}.{minor}.{patch + 1}"
    newer_tag = f"v{newer_version}"
    c, _, _ = client
    respx.get(RELEASES_URL).mock(return_value=httpx.Response(200, json=[{
        "draft": False, "prerelease": False, "tag_name": newer_tag,
        "published_at": "2026-09-17T10:38:25Z", "html_url": f"https://github.com/EyalDelarea/flackey/releases/tag/{newer_tag}",
        "assets": [],
    }]))
    body = c.get("/api/update").json()
    assert body["ok"] is True and body["newer"] is True and body["available"] is False
    assert body["latest"] == newer_version and body["url"] is None
    assert body["release_url"] == f"https://github.com/EyalDelarea/flackey/releases/tag/{newer_tag}"


@respx.mock
def test_update_ignores_prerelease_releases(client):
    # v0.1.0-v0.1.2 all shipped as GitHub prereleases; none of them should ever read as an available
    # update, no matter how their version number compares to the running one.
    c, _, _ = client
    respx.get(RELEASES_URL).mock(return_value=httpx.Response(200, json=[{
        "draft": False, "prerelease": True, "tag_name": "v9.9.9",
        "published_at": "2026-09-15T10:00:00Z",
        "assets": [{"name": "Flackey.pkg", "size": 123,
                    "browser_download_url": "https://github.com/EyalDelarea/flackey/releases/download/v9.9.9/Flackey.pkg"}],
    }]))
    body = c.get("/api/update").json()
    assert body["ok"] is True and body["newer"] is False and body["available"] is False and body["latest"] is None


INSTALLER_URL = "https://github.com/EyalDelarea/flackey/releases/download/v9.9.9/Flackey.pkg"
INCOMPLETE = "The download arrived incomplete. Check your connection and try again."
# What the app's own page sends. A stranger's page cannot: inventing a header makes the request
# preflighted, and the preflight is refused.
FROM_APP = {"x-flackey-app": "1"}
# `AppClient` sends the header on every request; an empty value is how a test leaves it out.
NOT_FROM_APP = {"x-flackey-app": ""}


def _release_feed(size: int | None = 8, assets: bool = True):
    return [{"draft": False, "prerelease": False, "tag_name": "v9.9.9",
             "published_at": "2026-09-15T10:00:00Z", "html_url": "https://github.com/EyalDelarea/flackey/releases/tag/v9.9.9",
             "assets": [{"name": "Flackey.pkg", "size": size, "browser_download_url": INSTALLER_URL}]
             if assets else []}]


REAL_INSTALLER_NAME = flackey.web.update.installer_name
REAL_BAKED_PUBLIC_KEY = _signature.baked_public_key
# Taken at import, before conftest swaps it for a tripwire on every test.
REAL_OPEN_INSTALLER = flackey.web.update.open_installer


@pytest.fixture(autouse=True)
def _the_mac_installer_by_default(monkeypatch):
    """The update tests below are written against the Mac's `Flackey.pkg`. Pinned so they say the same
    thing when the suite runs on Windows, where the real name is `Flackey-Setup.exe`; the tests that
    are about the Windows name put the real function back."""
    monkeypatch.setattr(flackey.web.update, "installer_name", lambda: "Flackey.pkg")


def _pretend_windows(monkeypatch) -> None:
    """Windows, as far as `web/update.py` can tell. Only that module's `sys` is swapped: patching the
    real `sys.platform` on a Mac sends the rest of the process (httpx, asyncio, `shutil.which`) down
    Windows paths it cannot take here."""
    monkeypatch.setattr(flackey.web.update, "installer_name", REAL_INSTALLER_NAME)
    monkeypatch.setattr(flackey.web.update, "sys", types.SimpleNamespace(platform="win32"))


def test_the_installer_is_the_pkg_on_macos_and_setup_exe_on_windows(monkeypatch):
    monkeypatch.setattr(flackey.web.update, "sys", types.SimpleNamespace(platform="darwin"))
    assert REAL_INSTALLER_NAME() == "Flackey.pkg"
    monkeypatch.setattr(flackey.web.update, "sys", types.SimpleNamespace(platform="win32"))
    assert REAL_INSTALLER_NAME() == "Flackey-Setup.exe"


@respx.mock
def test_windows_offers_setup_exe_and_never_the_pkg(monkeypatch):
    """A release carries both installers, and Windows must pick its own. (That Windows is never offered
    the seamless swap is `seamless_available`'s to decide; test_selfupdate_install.py pins it.)"""
    _pretend_windows(monkeypatch)
    feed = _release_feed(size=8)
    download = "https://github.com/EyalDelarea/flackey/releases/download/v9.9.9/"
    feed[0]["assets"] += [{"name": "Flackey-Setup.exe", "size": 99,
                           "browser_download_url": download + "Flackey-Setup.exe"},
                          {"name": "Flackey-Setup.exe.sig", "size": 129,
                           "browser_download_url": download + "Flackey-Setup.exe.sig"}]
    respx.get(RELEASES_URL).mock(return_value=httpx.Response(200, json=feed))
    info = asyncio.run(flackey.web.update.latest_release())
    assert info["url"] == download + "Flackey-Setup.exe" and info["size"] == 99
    assert info["installer_signature_url"] == download + "Flackey-Setup.exe.sig"
    assert info["available"] is True and info["seamless"] is False


def test_windows_opens_the_installer_through_the_shell(monkeypatch, tmp_path):
    """`os.startfile` is a double-click: SmartScreen's prompt for an unsigned download appears as it
    would for one the owner fetched in a browser. The Mac's `open` is never tried."""
    started = []
    monkeypatch.setattr(flackey.web.update, "sys", types.SimpleNamespace(platform="win32"))
    monkeypatch.setattr(flackey.web.update.os, "startfile", started.append, raising=False)
    monkeypatch.setattr(flackey.web.update.subprocess, "Popen",
                        lambda *a, **k: pytest.fail("ran `open` on Windows"))
    # conftest swaps `open_installer` for a tripwire; this test is the one place the real one runs.
    monkeypatch.setattr(flackey.web.update, "open_installer", REAL_OPEN_INSTALLER)
    installer = tmp_path / "Flackey-Setup.exe"
    flackey.web.update.open_installer(installer)
    assert started == [installer]


@respx.mock
def test_windows_quits_once_setup_exe_is_open_so_it_can_replace_the_app(tmp_path, monkeypatch):
    """Windows cannot overwrite a running Flackey.exe, so the app gets out of the installer's way."""
    _pretend_windows(monkeypatch)
    events = []
    monkeypatch.setattr("flackey.web.update.open_installer", lambda p: events.append(("open", p.name)))
    app, _, settings = make(tmp_path, quit_app=lambda: events.append(("quit", None)))
    c = AppClient(app)
    respx.get(RELEASES_URL).mock(return_value=httpx.Response(200, json=[{
        **_release_feed()[0], "assets": [{"name": "Flackey-Setup.exe", "size": 8,
                                          "browser_download_url": INSTALLER_URL}]}]))
    respx.get(INSTALLER_URL).mock(return_value=httpx.Response(200, content=b"EXE-DATA"))
    c.post("/api/update/install", headers=FROM_APP)
    _settle(c, "ready")
    assert (settings.data_dir / "updates" / "Flackey-Setup.exe").read_bytes() == b"EXE-DATA"
    assert events == [("open", "Flackey-Setup.exe"), ("quit", None)]


@respx.mock
def test_windows_stays_open_when_setup_exe_would_not_start(tmp_path, monkeypatch):
    """Quitting after a failed open would leave the owner with no app and no installer."""
    _pretend_windows(monkeypatch)

    def refuses(path):
        raise OSError("no association")

    monkeypatch.setattr("flackey.web.update.open_installer", refuses)
    app, _, _ = make(tmp_path, quit_app=lambda: pytest.fail("quit without an installer running"))
    c = AppClient(app)
    respx.get(RELEASES_URL).mock(return_value=httpx.Response(200, json=[{
        **_release_feed()[0], "assets": [{"name": "Flackey-Setup.exe", "size": 8,
                                          "browser_download_url": INSTALLER_URL}]}]))
    respx.get(INSTALLER_URL).mock(return_value=httpx.Response(200, content=b"EXE-DATA"))
    c.post("/api/update/install", headers=FROM_APP)
    assert "Flackey-Setup.exe" in _settle(c, "ready")["error"]


@respx.mock
def test_the_mac_keeps_running_while_installer_app_is_open(tmp_path, monkeypatch):
    """Installer.app swaps a bundle that is not in use until the next launch; the owner may be busy."""
    monkeypatch.setattr(flackey.web.update, "sys", types.SimpleNamespace(platform="darwin"))
    opened = []
    monkeypatch.setattr("flackey.web.update.open_installer", opened.append)
    app, _, _ = make(tmp_path, quit_app=lambda: pytest.fail("quit on the Mac"))
    c = AppClient(app)
    respx.get(RELEASES_URL).mock(return_value=httpx.Response(200, json=_release_feed(size=8)))
    respx.get(INSTALLER_URL).mock(return_value=httpx.Response(200, content=b"PKG-DATA"))
    c.post("/api/update/install", headers=FROM_APP)
    _settle(c, "ready")
    assert len(opened) == 1


def _settle(c, want: str) -> dict:
    """The download runs as a task, so the answer arrives after the POST that started it. Every request
    gives the server's loop a turn, which is what actually moves it along."""
    body = {}
    for _ in range(300):
        body = c.get("/api/update/progress").json()
        if body["state"] == want:
            return body
        time.sleep(0.01)
    raise AssertionError(f"download never reached {want!r}, stuck at {body.get('state')!r}: {body}")


@respx.mock
def test_update_install_downloads_the_installer_then_opens_it(client, monkeypatch):
    opened = []
    monkeypatch.setattr("flackey.web.update.open_installer", opened.append)
    c, _, settings = client
    respx.get(RELEASES_URL).mock(return_value=httpx.Response(200, json=_release_feed(size=8)))
    respx.get(INSTALLER_URL).mock(return_value=httpx.Response(200, content=b"PKG-DATA"))
    assert c.post("/api/update/install", headers=FROM_APP).json()["state"] == "downloading"
    body = _settle(c, "ready")
    assert body["percent"] == 100 and body["version"] == "9.9.9" and body["error"] is None
    target = settings.data_dir / "updates" / "Flackey.pkg"
    assert target.read_bytes() == b"PKG-DATA"
    # Opened from a private copy of the bytes it read, not from the download itself.
    [copy] = opened
    assert copy.name == "Flackey.pkg" and copy != target and copy.read_bytes() == b"PKG-DATA"


@respx.mock
def test_update_install_refuses_a_release_whose_installer_is_missing(client):
    c, _, _ = client
    respx.get(RELEASES_URL).mock(return_value=httpx.Response(200, json=_release_feed(assets=False)))
    r = c.post("/api/update/install", headers=FROM_APP)
    assert r.status_code == 409 and "isn't published yet" in r.json()["detail"]
    assert c.get("/api/update/progress").json()["state"] == "idle"


@respx.mock
def test_update_install_refuses_when_there_is_nothing_newer(client):
    c, _, _ = client
    respx.get(RELEASES_URL).mock(return_value=httpx.Response(200, json=[{
        "draft": False, "prerelease": False, "tag_name": f"v{__version__}", "assets": [],
    }]))
    r = c.post("/api/update/install", headers=FROM_APP)
    assert r.status_code == 409 and r.json()["detail"] == "Flackey is already up to date."


@respx.mock
def test_update_install_reports_a_download_that_dies_partway(client, monkeypatch):
    # The failure the owner is most likely to hit, and the one that must not leave a half-written pkg
    # behind: Installer.app calls a truncated file corrupt and never mentions the download.
    monkeypatch.setattr("flackey.web.update.open_installer", lambda p: pytest.fail("opened a broken pkg"))

    async def dies_partway():
        yield b"PKG-"
        raise httpx.ReadError("connection went away")

    c, _, settings = client
    respx.get(RELEASES_URL).mock(return_value=httpx.Response(200, json=_release_feed(size=8)))
    respx.get(INSTALLER_URL).mock(return_value=httpx.Response(200, content=dies_partway()))
    c.post("/api/update/install", headers=FROM_APP)
    body = _settle(c, "error")
    assert body["error"] == "The download stopped before it finished. Check your connection and try again."
    assert not (settings.data_dir / "updates" / "Flackey.pkg").exists()
    assert not (settings.data_dir / "updates" / "Flackey.pkg.part").exists()


@respx.mock
def test_update_install_reports_a_folder_it_cannot_write_to(client):
    c, _, settings = client
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    (settings.data_dir / "updates").write_text("not a folder")
    respx.get(RELEASES_URL).mock(return_value=httpx.Response(200, json=_release_feed(size=8)))
    respx.get(INSTALLER_URL).mock(return_value=httpx.Response(200, content=b"PKG-DATA"))
    c.post("/api/update/install", headers=FROM_APP)
    assert _settle(c, "error")["error"] == "Could not save the installer. The disk may be full."


@respx.mock
def test_update_install_reopens_a_finished_download_instead_of_fetching_it_again(client, monkeypatch):
    opened = []
    monkeypatch.setattr("flackey.web.update.open_installer", opened.append)
    c, _, _ = client
    respx.get(RELEASES_URL).mock(return_value=httpx.Response(200, json=_release_feed(size=8)))
    route = respx.get(INSTALLER_URL).mock(return_value=httpx.Response(200, content=b"PKG-DATA"))
    c.post("/api/update/install", headers=FROM_APP)
    _settle(c, "ready")
    assert c.post("/api/update/install", headers=FROM_APP).json()["state"] == "ready"
    assert route.call_count == 1 and len(opened) == 2


@respx.mock
def test_update_install_will_not_start_a_second_download(tmp_path, monkeypatch):
    # Context-managed on purpose: a bare TestClient gives every request its own event loop and drains the
    # download before the next one lands, so the press-it-twice case can only be reached with one loop
    # spanning both requests -- which is what the real server has.
    monkeypatch.setattr("flackey.web.update.open_installer", lambda p: None)

    async def slowly():
        yield b"PKG-"
        await asyncio.sleep(0.3)
        yield b"DATA"

    feed = respx.get(RELEASES_URL).mock(return_value=httpx.Response(200, json=_release_feed(size=8)))
    route = respx.get(INSTALLER_URL).mock(return_value=httpx.Response(200, content=slowly()))
    app, _, _ = make(tmp_path)
    with AppClient(app) as c:
        c.post("/api/update/install", headers=FROM_APP)
        assert c.post("/api/update/install", headers=FROM_APP).json()["state"] == "downloading"
        assert feed.call_count == 1 and route.call_count == 1
        _settle(c, "ready")


@respx.mock
def test_update_install_says_so_when_the_installer_will_not_open(client, monkeypatch):
    # The file is there and correct, so this stays "ready" -- but it may not claim the installer is open.
    def refuses(path):
        raise RuntimeError("LaunchServices said no")

    monkeypatch.setattr("flackey.web.update.open_installer", refuses)
    c, _, settings = client
    respx.get(RELEASES_URL).mock(return_value=httpx.Response(200, json=_release_feed(size=8)))
    respx.get(INSTALLER_URL).mock(return_value=httpx.Response(200, content=b"PKG-DATA"))
    c.post("/api/update/install", headers=FROM_APP)
    body = _settle(c, "ready")
    assert "would not open" in body["error"]
    assert (settings.data_dir / "updates" / "Flackey.pkg").read_bytes() == b"PKG-DATA"


@respx.mock
def test_update_install_recovers_from_a_download_cancelled_under_it(client, monkeypatch):
    # A cancel at shutdown raises straight past `except Exception`. "Downloading" is the state that
    # refuses the next attempt, so a task that dies holding it would wedge the button for good.
    monkeypatch.setattr("flackey.web.update.open_installer", lambda p: None)

    async def cancelled():
        yield b"PKG-"
        raise asyncio.CancelledError()

    c, _, _ = client
    respx.get(RELEASES_URL).mock(return_value=httpx.Response(200, json=_release_feed(size=8)))
    respx.get(INSTALLER_URL).mock(return_value=httpx.Response(200, content=cancelled()))
    c.post("/api/update/install", headers=FROM_APP)
    assert _settle(c, "error")["error"] == "The download stopped unexpectedly. Try again."
    # And the retry it refused before is allowed through again.
    respx.get(INSTALLER_URL).mock(return_value=httpx.Response(200, content=b"PKG-DATA"))
    c.post("/api/update/install", headers=FROM_APP)
    assert _settle(c, "ready")["percent"] == 100


@pytest.mark.parametrize("path", ["/api/update/install", "/api/update/release"])
@pytest.mark.parametrize("headers", [
    {},                                                          # a plain form POST from another page
    {"origin": "https://evil.test"},                             # ...and one that admits where it is from
    {"content-type": "application/x-www-form-urlencoded"},       # the simple-request content type
    {"host": "flackey.local"},                                   # a rebound host
], ids=["no-header", "hostile-origin", "form-encoded", "rebound-host"])
@respx.mock
def test_update_side_effects_refuse_a_press_from_another_page(client, monkeypatch, path, headers):
    # Loopback is reachable from any site the owner happens to open, and CORS hides the reply but not the
    # download-and-open -- a real Apple installer asking for an admin password at a stranger's timing.
    monkeypatch.setattr("flackey.web.update.open_installer", lambda p: pytest.fail("installer opened"))
    monkeypatch.setattr("flackey.web.update.open_url", lambda u: pytest.fail("browser opened"))
    feed = respx.get(RELEASES_URL).mock(return_value=httpx.Response(200, json=_release_feed(size=8)))
    c, _, _ = client
    r = c.post(path, headers={**NOT_FROM_APP, **headers})
    if "host" in headers:
        assert r.status_code == 400 and r.json()["detail"] == "Unknown host."
    else:
        assert r.status_code == 403 and r.json()["detail"] == "That request did not come from Flackey."
    # Refused before it looks anything up, let alone fetches it.
    assert feed.call_count == 0
    assert c.get("/api/update/progress").json()["state"] == "idle"


@respx.mock
def test_update_release_still_works_for_the_app_itself(client, monkeypatch):
    opened = []
    monkeypatch.setattr("flackey.web.update.open_url", opened.append)
    c, _, _ = client
    respx.get(RELEASES_URL).mock(return_value=httpx.Response(200, json=_release_feed()))
    assert c.post("/api/update/release", headers=FROM_APP).status_code == 200
    assert opened == ["https://github.com/EyalDelarea/flackey/releases/tag/v9.9.9"]


@respx.mock
def test_update_will_not_offer_an_installer_with_no_declared_size(client):
    # The declared size bounds the download and decides whether it arrived whole. Without one there is
    # no ceiling and no completeness check, so there is nothing safe to offer.
    c, _, _ = client
    respx.get(RELEASES_URL).mock(return_value=httpx.Response(200, json=[{
        "draft": False, "prerelease": False, "tag_name": "v9.9.9",
        "published_at": "2026-09-15T10:00:00Z", "html_url": "https://github.com/EyalDelarea/flackey/releases/tag/v9.9.9",
        "assets": [{"name": "Flackey.pkg", "browser_download_url": INSTALLER_URL}],
    }]))
    body = c.get("/api/update").json()
    assert body["newer"] is True and body["available"] is False and body["size"] is None
    r = c.post("/api/update/install", headers=FROM_APP)
    assert r.status_code == 409 and "isn't published yet" in r.json()["detail"]


@respx.mock
def test_update_will_not_offer_an_installer_whose_size_is_zero(client):
    c, _, _ = client
    respx.get(RELEASES_URL).mock(return_value=httpx.Response(200, json=_release_feed(size=0)))
    body = c.get("/api/update").json()
    assert body["newer"] is True and body["available"] is False


@respx.mock
def test_update_install_survives_two_presses_landing_together(tmp_path, monkeypatch):
    # The guard used to sit on the far side of the release lookup, and that lookup awaits. Two presses
    # inside that window both read "idle", both passed it, and both opened the same .part file to write
    # over each other -- so the check has to be settled before anything yields.
    monkeypatch.setattr("flackey.web.update.open_installer", lambda p: None)
    unhurried = flackey.web.update.latest_release

    async def slow_lookup():
        await asyncio.sleep(0.2)
        return await unhurried()

    monkeypatch.setattr("flackey.web.update.latest_release", slow_lookup)
    respx.get(RELEASES_URL).mock(return_value=httpx.Response(200, json=_release_feed(size=8)))
    route = respx.get(INSTALLER_URL).mock(return_value=httpx.Response(200, content=b"PKG-DATA"))
    app, _, _ = make(tmp_path)
    with AppClient(app) as c, ThreadPoolExecutor(max_workers=2) as pool:
        both = [pool.submit(c.post, "/api/update/install", headers=FROM_APP) for _ in range(2)]
        assert [f.result().status_code for f in both] == [200, 200]
        _settle(c, "ready")
    assert route.call_count == 1


@respx.mock
def test_update_install_refuses_a_body_shorter_than_the_release_says(client, monkeypatch):
    # A connection closed cleanly partway is not an HTTP error, so nothing upstream objects. Promoting
    # that to Flackey.pkg is exactly what the .part-then-rename dance exists to stop.
    monkeypatch.setattr("flackey.web.update.open_installer", lambda p: pytest.fail("opened a short pkg"))
    c, _, settings = client
    respx.get(RELEASES_URL).mock(return_value=httpx.Response(200, json=_release_feed(size=8)))
    respx.get(INSTALLER_URL).mock(return_value=httpx.Response(200, content=b"PKG-"))
    c.post("/api/update/install", headers=FROM_APP)
    assert _settle(c, "error")["error"] == INCOMPLETE
    assert not (settings.data_dir / "updates" / "Flackey.pkg").exists()
    assert not (settings.data_dir / "updates" / "Flackey.pkg.part").exists()


@respx.mock
def test_update_install_stops_a_body_longer_than_the_release_says(client, monkeypatch):
    monkeypatch.setattr("flackey.web.update.open_installer", lambda p: pytest.fail("opened an overrun pkg"))
    c, _, settings = client
    respx.get(RELEASES_URL).mock(return_value=httpx.Response(200, json=_release_feed(size=4)))
    respx.get(INSTALLER_URL).mock(return_value=httpx.Response(200, content=b"PKG-DATA-AND-MORE"))
    c.post("/api/update/install", headers=FROM_APP)
    assert _settle(c, "error")["error"] == INCOMPLETE
    assert not (settings.data_dir / "updates" / "Flackey.pkg").exists()


@respx.mock
def test_update_progress_reaches_the_page_over_the_status_stream(tmp_path, monkeypatch):
    # The page can be left and come back to mid-download, so progress rides the status event rather than
    # living in the component that started it.
    monkeypatch.setattr("flackey.web.update.open_installer", lambda p: None)
    bus = EventBus()
    status = Status(bus, telegram_authorized=True, worker_running=False, setup_done=True)
    app, _, _ = make(tmp_path, status=status, bus=bus)
    c = AppClient(app)
    respx.get(RELEASES_URL).mock(return_value=httpx.Response(200, json=_release_feed(size=8)))
    respx.get(INSTALLER_URL).mock(return_value=httpx.Response(200, content=b"PKG-DATA"))
    c.post("/api/update/install", headers=FROM_APP)
    _settle(c, "ready")
    assert status["update_download"]["state"] == "ready"
    assert status["update_download"]["version"] == "9.9.9"


@respx.mock
def test_update_release_opens_the_page_in_the_real_browser(client, monkeypatch):
    # `window.open` does nothing inside pywebview, so the release link is opened from here instead.
    opened = []
    monkeypatch.setattr("flackey.web.update.open_url", opened.append)
    c, _, _ = client
    respx.get(RELEASES_URL).mock(return_value=httpx.Response(200, json=_release_feed()))
    assert c.post("/api/update/release", headers=FROM_APP).json() == {"ok": True, "url": "https://github.com/EyalDelarea/flackey/releases/tag/v9.9.9"}
    assert opened == ["https://github.com/EyalDelarea/flackey/releases/tag/v9.9.9"]


@respx.mock
def test_update_release_says_so_when_github_cannot_be_reached(client, monkeypatch):
    monkeypatch.setattr("flackey.web.update.open_url", lambda u: pytest.fail("opened a page it never found"))
    c, _, _ = client
    respx.get(RELEASES_URL).mock(side_effect=httpx.ConnectError("offline"))
    r = c.post("/api/update/release", headers=FROM_APP)
    assert r.status_code == 502 and r.json()["detail"] == "Could not check for updates."


# ---- update hardening -----------------------------------------------------------------------------------
def _release(tag: str, *, draft: bool = False, prerelease: bool = False) -> dict:
    base = f"https://github.com/EyalDelarea/flackey/releases/download/{tag}/"
    return {"draft": draft, "prerelease": prerelease, "tag_name": tag,
            "html_url": f"https://github.com/EyalDelarea/flackey/releases/tag/{tag}",
            "assets": [{"name": "Flackey.pkg", "size": 8, "browser_download_url": base + "Flackey.pkg"}]}


@respx.mock
def test_update_offers_the_highest_version_not_the_first_listed():
    """The feed is ordered by creation, not by version: a hotfix for an older line can come first."""
    respx.get(RELEASES_URL).mock(return_value=httpx.Response(200, json=[
        _release("v9.9.8"), _release("v9.10.0"), _release("v9.9.10"),
        _release("v99.0.0", prerelease=True), _release("v50.0.0", draft=True),
        _release("v60.0.0-rc1"), _release("v70.0.0 "), _release("v８０.0.0"),
    ]))
    info = asyncio.run(flackey.web.update.latest_release())
    assert info["latest"] == "9.10.0"
    assert info["url"] == "https://github.com/EyalDelarea/flackey/releases/download/v9.10.0/Flackey.pkg"


@pytest.mark.parametrize("text, parsed", [
    ("1.2.3", (1, 2, 3)), ("v1.2.3", (1, 2, 3)), ("v10.0.12", (10, 0, 12)),
    (" 1.2.3", None), ("1.2.3\n", None), ("1.2.3 ", None), ("1.2", None), ("1.2.3.4", None),
    ("1.2.3-rc1", None), ("V1.2.3", None), ("１.2.3", None), ("1.٢.3", None), ("", None),
])
def test_versions_are_read_strictly(text, parsed):
    assert flackey.web.update.parse_version(text) == parsed


@respx.mock
@pytest.mark.parametrize("url", [
    "https://github.com/EyalDelarea/flackey/releases/download/../../../evil/x/Flackey.pkg",
    "https://github.com/EyalDelarea/flackey/releases/download/v9.9.9/%2e%2e/%2E%2E/evil/Flackey.pkg",
    "https://github.com/EyalDelarea/flackey/releases/download/v9.9.9/..%2f..%2fevil/Flackey.pkg",
    "https://github.com/EyalDelarea/flackey/releases/download/v9.9.9\\..\\Flackey.pkg",
    "https://github.com:444/EyalDelarea/flackey/releases/download/v9.9.9/Flackey.pkg",
    "http://github.com/EyalDelarea/flackey/releases/download/v9.9.9/Flackey.pkg",
    "https://github.com/EyalDelarea/flackey/releases/download/v9.9.9/Flackey.pkg?x=1",
])
def test_an_asset_link_that_leaves_the_release_folder_is_treated_as_missing(url):
    feed = _release_feed(size=8)
    feed[0]["assets"][0]["browser_download_url"] = url
    respx.get(RELEASES_URL).mock(return_value=httpx.Response(200, json=feed))
    info = asyncio.run(flackey.web.update.latest_release())
    assert info["url"] is None and info["available"] is False


@respx.mock
def test_an_installer_larger_than_any_real_one_is_not_offered():
    respx.get(RELEASES_URL).mock(return_value=httpx.Response(
        200, json=_release_feed(size=flackey.web.update.MAX_DOWNLOAD_BYTES + 1)))
    info = asyncio.run(flackey.web.update.latest_release())
    assert info["newer"] is True and info["available"] is False and info["size"] is None


@respx.mock
def test_the_download_follows_github_to_its_own_cdn(client, monkeypatch):
    opened = []
    monkeypatch.setattr("flackey.web.update.open_installer", opened.append)
    c, _, _ = client
    cdn = "https://release-assets.githubusercontent.com/github-production-release-asset/1/abc"
    respx.get(RELEASES_URL).mock(return_value=httpx.Response(200, json=_release_feed(size=8)))
    respx.get(INSTALLER_URL).mock(return_value=httpx.Response(302, headers={"location": cdn}))
    respx.get(cdn).mock(return_value=httpx.Response(200, content=b"PKG-DATA"))
    c.post("/api/update/install", headers=FROM_APP)
    _settle(c, "ready")
    assert len(opened) == 1 and opened[0].read_bytes() == b"PKG-DATA"


@respx.mock
@pytest.mark.parametrize("where", ["https://evil.test/Flackey.pkg", "http://objects.githubusercontent.com/x",
                                   "https://github.com.evil.test/x"])
def test_the_download_will_not_follow_a_redirect_off_github(client, monkeypatch, where):
    monkeypatch.setattr("flackey.web.update.open_installer", lambda p: pytest.fail("installer opened"))
    c, _, settings = client
    respx.get(RELEASES_URL).mock(return_value=httpx.Response(200, json=_release_feed(size=8)))
    respx.get(INSTALLER_URL).mock(return_value=httpx.Response(302, headers={"location": where}))
    elsewhere = respx.get(where).mock(return_value=httpx.Response(200, content=b"PKG-DATA"))
    c.post("/api/update/install", headers=FROM_APP)
    assert _settle(c, "error")["state"] == "error"
    assert elsewhere.call_count == 0
    assert not (settings.data_dir / "updates" / "Flackey.pkg").exists()


@respx.mock
def test_the_release_feed_is_only_read_from_github():
    respx.get(RELEASES_URL).mock(return_value=httpx.Response(302, headers={"location": "https://evil.test/feed"}))
    evil = respx.get("https://evil.test/feed").mock(return_value=httpx.Response(200, json=_release_feed()))
    info = asyncio.run(flackey.web.update.latest_release())
    assert info["ok"] is False and evil.call_count == 0


@respx.mock
@pytest.mark.parametrize("key", ["missing", "malformed"])
def test_a_packaged_build_without_a_usable_key_opens_no_installer(client, monkeypatch, key):
    """A packaged copy whose release key is gone or mangled cannot verify anything, so it must not fall
    back to opening an unchecked installer -- that is exactly what a broken build would otherwise do."""
    monkeypatch.setattr(flackey.web.update, "sys", types.SimpleNamespace(platform="darwin", frozen=True))
    monkeypatch.setattr("flackey.selfupdate.signature.PUBLIC_KEY_HEX", "" if key == "missing" else "zz12")
    # The autouse keyless stub, swapped back for the real reader of the (now broken) constant.
    monkeypatch.setattr("flackey.selfupdate.signature.baked_public_key", REAL_BAKED_PUBLIC_KEY)
    monkeypatch.setattr("flackey.web.update.open_installer", lambda p: pytest.fail("installer opened"))
    c, _, _ = client
    respx.get(RELEASES_URL).mock(return_value=httpx.Response(200, json=_release_feed(size=8)))
    pkg = respx.get(INSTALLER_URL).mock(return_value=httpx.Response(200, content=b"PKG-DATA"))
    body = c.get("/api/update").json()
    assert body["newer"] is True and body["available"] is False
    r = c.post("/api/update/install", headers=FROM_APP)
    assert r.status_code == 409 and "cannot check" in r.json()["detail"]
    assert pkg.call_count == 0


def _keyed(monkeypatch):
    """A build carrying a release key the test holds the private half of."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from flackey.selfupdate import signature as sig_module
    private = Ed25519PrivateKey.generate()
    monkeypatch.setattr(sig_module, "baked_public_key", lambda: sig_module.encode_public_key(private.public_key()))
    monkeypatch.setattr("flackey.selfupdate.install.seamless_available", lambda *a, **k: False)

    def sign_pkg(payload: bytes, version: str = "9.9.9") -> bytes:
        return private.sign(sig_module.signing_message(version, payload, sig_module.INSTALLER_DOMAIN)).hex().encode()
    return sign_pkg


def _signed_feed() -> list[dict]:
    feed = _release_feed(size=8)
    feed[0]["assets"].append({"name": "Flackey.pkg.sig", "size": 129, "browser_download_url": INSTALLER_URL + ".sig"})
    return feed


@respx.mock
def test_the_installer_opened_is_a_private_copy_of_the_verified_bytes(client, monkeypatch):
    """Verified in memory, then opened from disk, is two reads -- and the file can change between them.
    What gets opened is a fresh file in a folder only this user can enter, written from the bytes that
    were checked."""
    sign_pkg = _keyed(monkeypatch)
    opened = []
    monkeypatch.setattr("flackey.web.update.open_installer", lambda p: opened.append((p, p.read_bytes())))
    c, _, settings = client
    respx.get(RELEASES_URL).mock(return_value=httpx.Response(200, json=_signed_feed()))
    respx.get(INSTALLER_URL).mock(return_value=httpx.Response(200, content=b"PKG-DATA"))
    respx.get(INSTALLER_URL + ".sig").mock(return_value=httpx.Response(200, content=sign_pkg(b"PKG-DATA")))
    c.post("/api/update/install", headers=FROM_APP)
    _settle(c, "ready")
    [(path, data)] = opened
    assert data == b"PKG-DATA" and path.name == "Flackey.pkg"
    assert path != settings.data_dir / "updates" / "Flackey.pkg"
    if sys.platform != "win32":
        assert path.parent.stat().st_mode & 0o777 == 0o700
        assert path.stat().st_mode & 0o077 == 0


@respx.mock
def test_open_installer_again_checks_the_file_again(client, monkeypatch):
    """The "Open installer" button reopens what is on disk, and that file may not be the one verified."""
    sign_pkg = _keyed(monkeypatch)
    opened = []
    monkeypatch.setattr("flackey.web.update.open_installer", lambda p: opened.append(p.read_bytes()))
    c, _, settings = client
    respx.get(RELEASES_URL).mock(return_value=httpx.Response(200, json=_signed_feed()))
    respx.get(INSTALLER_URL).mock(return_value=httpx.Response(200, content=b"PKG-DATA"))
    respx.get(INSTALLER_URL + ".sig").mock(return_value=httpx.Response(200, content=sign_pkg(b"PKG-DATA")))
    c.post("/api/update/install", headers=FROM_APP)
    _settle(c, "ready")
    assert c.post("/api/update/install", headers=FROM_APP).json()["state"] == "ready"
    assert opened == [b"PKG-DATA", b"PKG-DATA"]

    (settings.data_dir / "updates" / "Flackey.pkg").write_bytes(b"EVILDATA")
    body = c.post("/api/update/install", headers=FROM_APP).json()
    assert body["state"] == "error" and "could not be verified" in body["error"]
    assert opened == [b"PKG-DATA", b"PKG-DATA"]
    assert not (settings.data_dir / "updates" / "Flackey.pkg").exists()


def test_no_other_origin_is_granted_cors(client):
    """The UI is served from this origin and the Vite dev server proxies `/api`, so nobody else gets a pass."""
    c, _, _ = client
    for origin in ("http://localhost:5173", "https://evil.test"):
        r = c.options("/api/health", headers={"Origin": origin, "Access-Control-Request-Method": "POST",
                                              "Access-Control-Request-Headers": "x-flackey-app"})
        assert "access-control-allow-origin" not in r.headers


@pytest.mark.parametrize("host", ["evil.test", "evil.test:8765", "localhost.evil.test", "127.0.0.1.nip.io", ""])
def test_a_name_other_than_localhost_is_refused_on_every_path(client, host):
    """DNS rebinding: a page that re-points its own name at 127.0.0.1 still sends that name as Host."""
    c, _, _ = client
    for method, path in (("GET", "/api/health"), ("GET", "/api/setup/soulseek/password"), ("GET", "/"),
                         ("PUT", "/api/settings")):
        r = c.request(method, path, headers={"host": host})
        assert r.status_code == 400 and r.json()["detail"] == "Unknown host.", (method, path)


@pytest.mark.parametrize("host", ["localhost:8765", "127.0.0.1:8765", "[::1]:8765", "192.168.1.20:8765",
                                  "LOCALHOST"])
def test_loopback_and_addresses_are_answered(client, host):
    c, _, _ = client
    assert c.get("/api/health", headers={"host": host}).status_code == 200


def test_an_allowed_host_name_is_answered(tmp_path):
    s = Settings(_env_file=None, library_root=tmp_path / "lib2", data_dir=tmp_path / "data2",
                 web_allowed_hosts="nas.local, Other.Example")
    store = Store(s.db_path)
    api = create_app(store, Worker(store, DummySource(), DummyCatalog(), MemoryNotifier(), s),
                     Inbox(store, youtube=fake_youtube), s)
    c = AppClient(api)
    assert c.get("/api/health", headers={"host": "nas.local:8765"}).status_code == 200
    assert c.get("/api/health", headers={"host": "other.example"}).status_code == 200
    assert c.get("/api/health", headers={"host": "evil.test"}).status_code == 400


@pytest.mark.parametrize("method, path", [
    ("POST", "/api/telegram/logout"), ("POST", "/api/setup/reset"), ("POST", "/api/requests/clear-failed"),
    ("POST", "/api/library/refresh"), ("PUT", "/api/settings"), ("DELETE", "/api/requests/1"),
])
def test_a_state_change_without_the_app_header_is_refused(client, method, path):
    """A body-less POST is a CORS simple request: any page can fire it blind. The header forces a preflight."""
    c, _, _ = client
    r = c.request(method, path, headers=NOT_FROM_APP)
    assert r.status_code == 403 and r.json()["detail"] == "That request did not come from Flackey."


@pytest.mark.parametrize("path", ["/api/setup/soulseek/password", "/api/setup/slskd/credentials"])
def test_a_secret_is_only_read_with_the_app_header(client, path):
    c, _, _ = client
    assert c.get(path, headers=NOT_FROM_APP).status_code == 403
    assert c.get(path).status_code == 404  # with the header it gets as far as "nothing saved"


# ---- the per-launch API token ---------------------------------------------------------------------------
NO_TOKEN = {"x-flackey-token": ""}


@pytest.mark.parametrize("method, path", [
    ("GET", "/api/health"), ("GET", "/api/setup/soulseek/password"), ("POST", "/api/telegram/qr"),
    ("POST", "/api/telegram/logout"), ("PUT", "/api/settings"), ("POST", "/api/update/install"),
    ("GET", "/api/events"), ("GET", "/api/rejections/1/audio"), ("OPTIONS", "/api/health"),
])
@pytest.mark.parametrize("token", ["", "wrong", "x" * 43, "ünïcødé".encode("latin-1")])
def test_every_api_request_needs_the_launch_token(client, method, path, token):
    """Another program on this machine, another account on it, or a LAN host in Docker can reach the
    port; only whoever was handed the token can use it. A token that is not even ASCII is a 401, never a
    500 from the comparison."""
    c, _, _ = client
    r = c.request(method, path, headers={"x-flackey-token": token})
    assert r.status_code == 401 and r.json()["detail"].startswith("Flackey needs its access key"), (method, path)


def test_the_page_itself_is_served_without_the_token_and_never_carries_it(tmp_path):
    ui = tmp_path / "ui"
    ui.mkdir()
    (ui / "index.html").write_text("<!doctype html><div id=root></div>")
    app, _, _ = make(tmp_path, ui_dir=ui)
    c = AppClient(app, headers=NO_TOKEN)
    r = c.get("/")
    assert r.status_code == 200 and app.state.api_token not in r.text


def test_a_given_token_is_the_one_asked_for(tmp_path):
    app, _, _ = make(tmp_path, api_token="t" * 40)
    assert app.state.api_token == "t" * 40
    c = AppClient(app, headers=NO_TOKEN)
    assert c.get("/api/health", headers={"x-flackey-token": "t" * 40}).status_code == 200


def test_without_a_given_token_each_app_makes_its_own(tmp_path):
    first, _, _ = make(tmp_path)
    second, _, _ = make(tmp_path / "two")
    assert len(first.state.api_token) >= 43 and first.state.api_token != second.state.api_token


def test_the_session_cookie_lets_media_elements_read_but_not_act(client):
    """`<audio>`, `<img>` and `EventSource` cannot send a header. The cookie they ride on is set only by
    a request that had the token, and opens reads -- never a state change and never a saved secret."""
    c, _, _ = client
    assert c.post("/api/session", headers=NO_TOKEN).status_code == 401
    r = c.post("/api/session")
    assert r.status_code == 200
    cookie = r.headers["set-cookie"]
    assert "flackey_session=" in cookie and "HttpOnly" in cookie and "samesite=strict" in cookie.lower()
    assert "Path=/api" in cookie
    value = c.cookies.get("flackey_session")
    assert value and c.app.state.api_token not in value
    # The cookie alone, as the media elements send it.
    assert c.get("/api/health", headers=NO_TOKEN).status_code == 200
    assert c.get("/api/rejections/999/audio", headers=NO_TOKEN).status_code == 404
    assert c.post("/api/setup/reset", headers=NO_TOKEN).status_code == 401
    assert c.put("/api/settings", headers=NO_TOKEN, json={}).status_code == 401
    assert c.get("/api/setup/soulseek/password", headers=NO_TOKEN).status_code == 403


def test_a_forged_session_cookie_is_refused(client):
    c, _, _ = client
    c.cookies.set("flackey_session", "forged")
    assert c.get("/api/health", headers=NO_TOKEN).status_code == 401


def test_the_session_route_still_needs_the_app_header(client):
    c, _, _ = client
    assert c.post("/api/session", headers=NOT_FROM_APP).status_code == 403


@pytest.mark.parametrize("path", ["/", "/api/health", "/api/nope"])
def test_no_page_can_frame_the_app(client, path):
    """Clickjacking: a site that frames the UI could steer the owner's clicks on it."""
    c, _, _ = client
    for headers in ({}, NO_TOKEN):
        r = c.get(path, headers=headers)
        assert r.headers["x-frame-options"] == "DENY", path
        assert r.headers["content-security-policy"] == "frame-ancestors 'none'", path
        assert r.headers["x-content-type-options"] == "nosniff", path


@pytest.mark.parametrize("where", ["home", "above-home", "root", "data-dir", "symlink-to-home"])
def test_a_library_folder_that_would_share_home_or_flackeys_data_is_refused(client, tmp_path, monkeypatch,
                                                                             where):
    """The library is shared with the whole Soulseek network."""
    c, _, settings = client
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))  # what Path.home() reads on Windows
    link = tmp_path / "link"
    link.symlink_to(home)
    # The top of the drive the test runs on: `/` on a Mac, `C:\` on Windows (where `/` is not absolute).
    folder = {"home": home, "above-home": tmp_path, "root": Path(tmp_path.anchor), "data-dir": settings.data_dir,
              "symlink-to-home": link}[where]

    r = c.put("/api/settings", json={"library_root": str(folder)})

    assert r.status_code == 400 and "Choose a folder just for your music" in r.json()["detail"]
    assert c.get("/api/settings").json()["library_root"] != str(folder)


@pytest.mark.parametrize("raw", [r"\\nas\music", "//nas/music"])
def test_a_network_library_folder_is_refused(client, raw):
    """Refused on the string, before `mkdir` could go looking for the server."""
    c, _, settings = client
    before = str(settings.library_root)
    r = c.put("/api/settings", json={"library_root": raw})
    assert r.status_code == 400 and "another computer" in r.json()["detail"]
    assert c.get("/api/settings").json()["library_root"] == before


@pytest.mark.parametrize("saved", ["dot-folder", "library", "unc"])
def test_an_unchanged_risky_library_folder_does_not_block_other_settings(client, tmp_path, monkeypatch, saved):
    """The page sends the library folder with every save. A folder chosen before the share rules existed
    must not turn every unrelated toggle into a 400."""
    c, _, settings = client
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    folder = {"dot-folder": home / ".music", "library": home / "Library" / "DJ",
              "unc": Path(r"\\nas\music")}[saved]
    settings.library_root = folder

    r = c.put("/api/settings", json={"library_root": str(folder), "auto_update_check": False})

    assert r.status_code == 200
    assert r.json()["auto_update_check"] is False and r.json()["library_root"] == str(folder)


def test_queue_bundles(client, tmp_path):
    c, store, _ = client
    rid = store.add_request("q", RequestKind.TEXT)
    store.upsert_catalog_track(CatalogTrack(id=7, artist="A", title="T", mix_name="Original Mix", label="L", genre="G"))
    store.update_request(rid, catalog_track_id=7)
    store.add_candidates(rid, [Candidate(source="s", source_ref="r", artist="A", title="T", rank=1, score=90)])
    tid = store.add_track(path=tmp_path / "a.mp3", fmt="mp3", bitrate_kbps=320, cutoff_hz=19800, file_size=5,
                          artist="A", title="T", mix_name="Original Mix", duration_s=1, isrc=None,
                          catalog_track_id=7, request_id=rid)
    store.update_request(rid, state=RequestState.DONE, track_id=tid)
    bad = store.add_request("bad", RequestKind.TEXT)
    store.add_rejection(bad, "cutoff", 320, 16000, None)
    store.set_state(bad, RequestState.REJECTED)
    q = c.get("/api/queue").json()
    assert [b["request"]["id"] for b in q] == [bad, rid]
    assert q[1]["candidates"][0]["score"] == 90 and q[1]["catalog"]["label"] == "L"
    assert q[1]["track"]["path"].endswith("a.mp3") and q[1]["rejection"] is None
    assert q[0]["rejection"]["cutoff_hz"] == 16000 and q[0]["track"] is None
    assert c.get(f"/api/requests/{rid}").json()["request"]["state"] == "done"
    assert c.get("/api/requests/999").status_code == 404


def test_candidate_json_says_whether_deezer_has_a_sample(client):
    """The page decides whether to draw a play control before anyone presses anything, so the answer has
    to ride the bundle. Three values, and `null` is not `false`: nobody ever asked about that candidate."""
    c, store, _ = client
    rid = store.add_request("q", RequestKind.TEXT)
    store.add_candidates(rid, [
        Candidate(source="s", source_ref="a", artist="A", title="T", rank=1, has_preview=True),
        Candidate(source="s", source_ref="b", artist="A", title="T", rank=2, has_preview=False),
        Candidate(source="s", source_ref="c", artist="A", title="T", rank=3),
    ])
    # Both surfaces the page reads: the detail bundle and the list it renders the play buttons in.
    for cands in (c.get(f"/api/requests/{rid}").json()["candidates"],
                  c.get("/api/queue").json()[0]["candidates"]):
        # `is`, not `==`: JSON `1` would satisfy `== True` and break every `=== null` check in the page.
        assert cands[0]["has_preview"] is True
        assert cands[1]["has_preview"] is False
        assert cands[2]["has_preview"] is None


def test_queue_reads_spotify_requests_from_the_persisted_database(client):
    c, store, _ = client
    rid = store.add_request("Astral Projection - Into The Void", RequestKind.SPOTIFY_TRACK,
                            source_url="https://open.spotify.com/track/example")

    response = c.get("/api/queue")

    assert response.status_code == 200
    assert response.json()[0]["request"]["id"] == rid
    assert response.json()[0]["request"]["kind"] == "spotify_track"


def test_queue_keeps_every_open_request_even_past_the_recent_window(client):
    c, store, _ = client
    old = store.add_request("old open", RequestKind.TEXT)
    for i in range(600):
        rid = store.add_request(f"done {i}", RequestKind.TEXT)
        store.set_state(rid, RequestState.DONE)
    ids = [b["request"]["id"] for b in c.get("/api/queue").json()]
    assert old in ids and len(ids) == 501 and ids == sorted(ids, reverse=True)


def test_submit_link(client):
    c, _, _ = client
    r = c.post("/api/requests", json={"url": "https://youtu.be/abc"})
    assert r.status_code == 200 and r.json()["summary"] == "Queued" and len(r.json()["request_ids"]) == 1
    assert c.post("/api/requests", json={"url": "hello"}).status_code == 400
    assert "yt-dlp" in c.post("/api/requests", json={"url": "https://www.youtube.com/watch?v=bad"}).json()["detail"]


def test_choose_cancel_retry(client):
    c, store, _ = client
    rid = store.add_request("q", RequestKind.TEXT)
    cid = store.add_candidates(rid, [Candidate(source="s", source_ref="r", artist="A", title="T", rank=1)])[0].id
    store.set_state(rid, RequestState.AWAITING_REVIEW, flag_reason="below")
    assert c.post(f"/api/requests/{rid}/choose/{cid}").json()["state"] == "queued"
    assert c.post(f"/api/requests/{rid}/choose/{cid}").status_code == 400       # no longer awaiting review
    other = store.add_request("other", RequestKind.TEXT)
    store.set_state(other, RequestState.AWAITING_REVIEW)
    assert c.post(f"/api/requests/{other}/choose/{cid}").status_code == 400     # candidate belongs to `rid`
    assert c.post(f"/api/requests/{other}/choose/999").status_code == 404
    store.set_state(rid, RequestState.AWAITING_REVIEW)
    assert c.post(f"/api/requests/{rid}/cancel").json()["state"] == "cancelled"
    assert c.post(f"/api/requests/{rid}/cancel").status_code == 400            # already cancelled
    store.set_state(other, RequestState.ERROR, error_message="x")
    assert c.post(f"/api/requests/{other}/retry").json()["state"] == "queued"
    assert c.post(f"/api/requests/{other}/retry").status_code == 400
    assert c.post("/api/requests/999/retry").status_code == 404


def test_retry_failed_requeues_the_ids_it_can_and_skips_the_rest(client):
    c, store, _ = client
    errored = store.add_request("e", RequestKind.TEXT)
    store.set_state(errored, RequestState.ERROR, error_message="x")
    not_found = store.add_request("nf", RequestKind.TEXT)
    store.set_state(not_found, RequestState.NOT_FOUND)
    rejected = store.add_request("r", RequestKind.TEXT)
    store.set_state(rejected, RequestState.REJECTED)

    r = c.post("/api/requests/retry-failed", json={"ids": [errored, not_found, rejected, 999]})
    assert r.status_code == 200
    assert r.json() == {"retried": [errored, not_found], "skipped": [rejected, 999]}
    assert store.get_request(errored).state == RequestState.QUEUED
    assert store.get_request(not_found).state == RequestState.QUEUED
    assert store.get_request(rejected).state == RequestState.REJECTED


def test_retry_failed_never_sweeps_a_track_the_owner_stopped(client):
    """Issue #92 gave a stopped track its own Try again, which the per-row route honours. The batch must
    not: "Retry all" pressed once would otherwise restart an afternoon of deliberate stops. The page
    already leaves those ids out, so this is the half that holds when the page is stale."""
    c, store, _ = client
    errored = store.add_request("e", RequestKind.TEXT)
    store.set_state(errored, RequestState.ERROR, error_message="x")
    stopped = store.add_request("c", RequestKind.TEXT)
    store.set_state(stopped, RequestState.CANCELLED)

    r = c.post("/api/requests/retry-failed", json={"ids": [errored, stopped]})

    assert r.json() == {"retried": [errored], "skipped": [stopped]}
    assert store.get_request(stopped).state == RequestState.CANCELLED
    # ...and the row's own button still works on the very same request.
    assert c.post(f"/api/requests/{stopped}/retry").json()["state"] == "queued"


# ---- candidate previews ---------------------------------------------------
# The preview URL Deezer signs expires about fifteen minutes out, so the route resolves it at play time
# from `candidates.deezer_id` and 302s the browser at the CDN. These fake payloads carry the fields
# `parse_track_json` actually reads; `preview` is the one that varies per test.
PREVIEW_URL = ("https://cdnt-preview.dzcdn.net/api/1/1/a/b/c.mp3"
               "?hdnea=exp=1758999999~acl=/api/1/1/a/b/c.mp3*~hmac=deadbeef")


def deezer_track_json(**extra) -> dict:
    return {"id": 3135556, "title": "Harder Better Faster Stronger", "artist": {"name": "Daft Punk"},
            "album": {"title": "Discovery"}, "duration": 224, **extra}


def candidate_with(store, **kw) -> int:
    rid = store.add_request("q", RequestKind.TEXT)
    return store.add_candidates(rid, [Candidate(source="s", source_ref="r", artist="A", title="T",
                                                rank=1, **kw)])[0].id


def test_preview_404s_for_a_candidate_that_does_not_exist(client):
    c, _, _ = client
    r = c.get("/api/candidates/999/preview", follow_redirects=False)
    assert r.status_code == 404 and r.json()["detail"] == "candidate not found"


def test_preview_404s_when_the_candidate_has_no_deezer_id(client):
    # Soulseek results arrive without one, and there is nothing to resolve.
    c, store, _ = client
    cid = candidate_with(store, deezer_id=None)
    r = c.get(f"/api/candidates/{cid}/preview", follow_redirects=False)
    assert r.status_code == 404 and r.json()["detail"] == "no preview for this candidate"


@respx.mock
def test_preview_404s_when_deezer_has_no_sample_for_the_track(client):
    c, store, _ = client
    cid = candidate_with(store, deezer_id=3135556)
    respx.get(f"{DeezerApi.BASE}/track/3135556").mock(
        return_value=httpx.Response(200, json=deezer_track_json()))
    r = c.get(f"/api/candidates/{cid}/preview", follow_redirects=False)
    assert r.status_code == 404 and r.json()["detail"] == "no preview for this candidate"


@respx.mock
def test_preview_redirects_to_the_signed_deezer_sample(client):
    c, store, _ = client
    cid = candidate_with(store, deezer_id=3135556)
    respx.get(f"{DeezerApi.BASE}/track/3135556").mock(
        return_value=httpx.Response(200, json=deezer_track_json(preview=PREVIEW_URL)))
    r = c.get(f"/api/candidates/{cid}/preview", follow_redirects=False)
    assert r.status_code == 302
    # Verbatim, query string and all: the hmac is part of the URL, and a mangled one plays nothing.
    assert r.headers["location"] == PREVIEW_URL
    # Without `no-store` the browser caches the redirect and a replay later chases an expired signature.
    assert r.headers["cache-control"] == "no-store"


@respx.mock
def test_preview_502s_when_deezer_is_unreachable(client):
    # Upstream being down is not the caller's fault, so it is a gateway error rather than a 404.
    c, store, _ = client
    cid = candidate_with(store, deezer_id=3135556)
    respx.get(f"{DeezerApi.BASE}/track/3135556").mock(return_value=httpx.Response(500))
    r = c.get(f"/api/candidates/{cid}/preview", follow_redirects=False)
    assert r.status_code == 502 and "Deezer" in r.json()["detail"]



def test_accept_answers_400_when_there_is_no_copy_to_keep(client):
    """The route is `act` over `worker.accept_rejection`, so the worker keeps deciding what the button
    means; what is pinned here is that a refusal reaches the page as a sentence it can show, not a 500.
    Filing the copy itself is exercised where the files are real, in test_worker.py."""
    c, store, settings = client
    rj, _kept = _kept_rejection(store, settings)
    rid = store.get_rejection(rj).request_id

    assert c.post(f"/api/requests/{rid}/accept").status_code == 400   # still REJECTED? no: not rejected yet
    store.set_state(rid, RequestState.REJECTED)
    store.clear_rejection_audio(rj)
    r = c.post(f"/api/requests/{rid}/accept")
    assert r.status_code == 400 and "no copy of it to keep" in r.json()["detail"]
    assert c.post("/api/requests/999/accept").status_code == 404


@respx.mock
def test_reference_audio_plays_the_deezer_preview_a_request_was_checked_against(client):
    """"A different recording" has to say different from *what*. When the reference is a Deezer preview
    the browser can hear it, resolved at play time like a candidate's because the signature expires."""
    c, store, _ = client
    rid = store.add_request("q", RequestKind.TEXT)
    store.set_reference(rid, {"kind": "deezer", "ref": "3135556", "needles": [[1]], "full": [1],
                              "excerpt_start_s": 0.0, "excerpt_s": 30.0})
    respx.get(f"{DeezerApi.BASE}/track/3135556").mock(
        return_value=httpx.Response(200, json=deezer_track_json(preview=PREVIEW_URL)))

    r = c.get(f"/api/requests/{rid}/reference/audio", follow_redirects=False)

    assert r.status_code == 302 and r.headers["location"] == PREVIEW_URL
    assert r.headers["cache-control"] == "no-store"


def test_reference_audio_404s_for_a_video_reference_and_for_none_at_all(client):
    """The other half of the reference is the owner's own YouTube video, and nothing local keeps its
    audio -- the page links to it at the excerpt offset instead, from `reference` on the bundle."""
    c, store, _ = client
    rid = store.add_request("q", RequestKind.TEXT)
    assert c.get(f"/api/requests/{rid}/reference/audio").status_code == 404
    store.set_reference(rid, {"kind": "youtube", "ref": "abc123", "needles": [[1]], "full": [1],
                              "excerpt_start_s": 131.5, "excerpt_s": 30.0})
    assert c.get(f"/api/requests/{rid}/reference/audio").status_code == 404
    assert c.get("/api/requests/999/reference/audio").status_code == 404

    # The bundle carries what the page needs to draw that link, and none of the fingerprints it does not.
    ref = c.get(f"/api/requests/{rid}").json()["reference"]
    assert ref == {"kind": "youtube", "ref": "abc123", "excerpt_start_s": 131.5}


def test_retry_failed_retries_only_the_ids_it_is_given(client):
    c, store, _ = client
    asked = store.add_request("e", RequestKind.TEXT)
    store.set_state(asked, RequestState.ERROR, error_message="x")
    left_alone = store.add_request("e2", RequestKind.TEXT)
    store.set_state(left_alone, RequestState.ERROR, error_message="x")

    assert c.post("/api/requests/retry-failed", json={"ids": [asked]}).json()["retried"] == [asked]
    assert store.get_request(left_alone).state == RequestState.ERROR


def test_retry_failed_rejects_a_body_that_is_not_a_list_of_ids(client):
    c, _, _ = client
    assert c.post("/api/requests/retry-failed", json={}).status_code == 400
    assert c.post("/api/requests/retry-failed", json={"ids": "1"}).status_code == 400
    assert c.post("/api/requests/retry-failed", json={"ids": ["1"]}).status_code == 400
    assert c.post("/api/requests/retry-failed", json={"ids": []}).json() == {"retried": [], "skipped": []}


def test_delete_request_removes_it_from_the_queue_but_keeps_a_filed_track(client, tmp_path):
    c, store, _ = client
    rid = store.add_request("q", RequestKind.TEXT)
    tid = store.add_track(path=tmp_path / "a.mp3", fmt="mp3", bitrate_kbps=320, cutoff_hz=19800, file_size=5,
                          artist="A", title="T", mix_name="Original Mix", duration_s=1, isrc=None,
                          catalog_track_id=None, request_id=rid)
    store.update_request(rid, state=RequestState.DONE, track_id=tid)
    assert c.delete(f"/api/requests/{rid}").json() == {"ok": True}
    assert rid not in [b["request"]["id"] for b in c.get("/api/queue").json()]
    assert tid in [t["id"] for t in c.get("/api/library").json()]


def test_delete_request_refuses_an_in_flight_one_and_unknown_ones(client):
    c, store, _ = client
    rid = store.add_request("q", RequestKind.TEXT)
    store.set_state(rid, RequestState.FETCHING)
    r = c.delete(f"/api/requests/{rid}")
    assert r.status_code == 409
    assert r.json() == {"detail": "That track is still being worked on. Skip it first."}
    assert c.delete("/api/requests/999").status_code == 404


def test_remove_stops_live_rows_and_forgets_them_but_skips_verifying_filing_and_gone(client, tmp_path):
    """"Remove these": a parked row and a downloading one are stopped and then deleted in one press. A row
    whose file is moving into the library is refused like Stop refuses it, and a filed track keeps its
    library entry -- only the request row goes."""
    c, store, settings = client
    parked = store.add_request("parked", RequestKind.TEXT)
    store.update_request(parked, retry_after="2099-01-01T00:00:00+00:00", flag_reason="waiting for Soulseek")
    fetching = store.add_request("fetching", RequestKind.TEXT)
    store.set_state(fetching, RequestState.FETCHING)
    verifying = store.add_request("verifying", RequestKind.TEXT)
    store.set_state(verifying, RequestState.VERIFYING)
    filing = store.add_request("filing", RequestKind.TEXT)
    store.set_state(filing, RequestState.FILING)
    errored = store.add_request("errored", RequestKind.TEXT)
    store.set_state(errored, RequestState.ERROR, error_message="x")
    settings.spectrogram_dir.mkdir(parents=True, exist_ok=True)
    png = settings.spectrogram_dir / "rj.png"
    png.write_bytes(b"png")
    rejected = store.add_request("rejected", RequestKind.TEXT)
    store.add_rejection(rejected, "cutoff", 320, 16000, png)
    store.set_state(rejected, RequestState.REJECTED)
    done = store.add_request("done", RequestKind.TEXT)
    tid = store.add_track(path=tmp_path / "a.mp3", fmt="mp3", bitrate_kbps=320, cutoff_hz=19800, file_size=5,
                          artist="A", title="T", mix_name="Original Mix", duration_s=1, isrc=None,
                          catalog_track_id=None, request_id=done)
    store.update_request(done, state=RequestState.DONE, track_id=tid)

    ids = [parked, fetching, verifying, filing, errored, rejected, done, 999]
    r = c.post("/api/requests/remove", json={"ids": ids})

    assert r.status_code == 200
    assert r.json() == {"removed": [parked, fetching, errored, rejected, done], "skipped": [verifying, filing, 999]}
    left = {b["request"]["id"] for b in c.get("/api/queue").json()}
    assert left == {verifying, filing}
    assert not png.exists()
    assert tid in [t["id"] for t in c.get("/api/library").json()]


def test_remove_waits_for_a_running_task_to_unwind_before_deleting(tmp_path):
    """The stop and the delete are one press, so the task the stop cancelled must be finished with the row
    before it goes -- its unwind still writes."""
    app, store, _ = make(tmp_path)
    c = AppClient(app)
    worker = app.state.worker if hasattr(app.state, "worker") else None
    rid = store.add_request("q", RequestKind.TEXT)
    store.set_state(rid, RequestState.FETCHING)
    seen: list[str] = []

    async def long_fetch():
        try:
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            seen.append("unwound")
            store.update_request(rid, fetch_source=None)
            raise

    async def run():
        task = asyncio.ensure_future(long_fetch())
        w._tasks[rid] = task
        await asyncio.sleep(0)
        await w.cancel_and_settle(rid)
        assert task.done() and seen == ["unwound"]

    w = worker or Worker(store, DummySource(), DummyCatalog(), MemoryNotifier(), Settings(
        _env_file=None, telegram_api_id=1, telegram_api_hash="h",
        library_root=tmp_path / "lib", data_dir=tmp_path / "data"))
    asyncio.run(run())
    assert store.get_request(rid).state == RequestState.CANCELLED
    assert c.post("/api/requests/remove", json={"ids": [rid]}).json() == {"removed": [rid], "skipped": []}


def test_remove_rejects_a_body_that_is_not_a_list_of_ids(client):
    c, _, _ = client
    assert c.post("/api/requests/remove", json={}).status_code == 400
    assert c.post("/api/requests/remove", json={"ids": [True]}).status_code == 400
    assert c.post("/api/requests/remove", json={"ids": []}).json() == {"removed": [], "skipped": []}


def test_pasting_an_open_track_again_says_already_queued(client):
    c, store, _ = client
    assert c.post("/api/requests", json={"url": "https://youtu.be/abc"}).json()["summary"] == "Queued"
    again = c.post("/api/requests", json={"url": "https://youtu.be/abc"})
    assert again.status_code == 200
    assert again.json()["summary"] == "Already queued" and again.json()["request_ids"] == []
    assert len(store.list_requests()) == 1


def test_delete_request_unlinks_a_spectrogram_under_spectrogram_dir_but_leaves_others_alone(client, tmp_path):
    c, store, settings = client
    settings.spectrogram_dir.mkdir(parents=True, exist_ok=True)
    inside = settings.spectrogram_dir / "req1.png"
    inside.write_bytes(b"png")
    outside = tmp_path / "elsewhere.png"
    outside.write_bytes(b"png")

    rid = store.add_request("q", RequestKind.TEXT)
    store.add_rejection(rid, "cutoff", 320, 16000, inside)
    store.set_state(rid, RequestState.REJECTED)
    other = store.add_request("q2", RequestKind.TEXT)
    store.add_rejection(other, "cutoff", 320, 16000, outside)
    store.set_state(other, RequestState.REJECTED)

    assert c.delete(f"/api/requests/{rid}").json() == {"ok": True}
    assert not inside.exists()

    assert c.delete(f"/api/requests/{other}").json() == {"ok": True}
    assert outside.exists()  # not under spectrogram_dir: left alone


def test_clear_failed_also_unlinks_spectrograms_under_spectrogram_dir(client):
    c, store, settings = client
    settings.spectrogram_dir.mkdir(parents=True, exist_ok=True)
    png = settings.spectrogram_dir / "req.png"
    png.write_bytes(b"png")
    rejected = store.add_request("r", RequestKind.TEXT)
    store.add_rejection(rejected, "cutoff", 320, 16000, png)
    store.set_state(rejected, RequestState.REJECTED)

    assert c.post("/api/requests/clear-failed").json() == {"removed": [rejected]}
    assert not png.exists()


def test_clear_failed_removes_only_failed_states(client):
    c, store, _ = client
    rejected = store.add_request("r", RequestKind.TEXT)
    store.set_state(rejected, RequestState.REJECTED)
    not_found = store.add_request("nf", RequestKind.TEXT)
    store.set_state(not_found, RequestState.NOT_FOUND)
    errored = store.add_request("e", RequestKind.TEXT)
    store.set_state(errored, RequestState.ERROR, error_message="x")
    cancelled = store.add_request("c", RequestKind.TEXT)
    store.set_state(cancelled, RequestState.CANCELLED)
    done = store.add_request("d", RequestKind.TEXT)
    store.set_state(done, RequestState.DONE)
    r = c.post("/api/requests/clear-failed")
    assert sorted(r.json()["removed"]) == sorted([rejected, not_found, errored, cancelled])
    ids = [b["request"]["id"] for b in c.get("/api/queue").json()]
    assert done in ids and rejected not in ids and not_found not in ids
    assert errored not in ids and cancelled not in ids


def test_delete_and_clear_failed_publish_a_queue_event(tmp_path):
    bus = EventBus()
    app, store, _ = make(tmp_path, bus=bus)
    c = AppClient(app)
    rid = store.add_request("q", RequestKind.TEXT)
    store.set_state(rid, RequestState.REJECTED)
    other = store.add_request("q2", RequestKind.TEXT)
    store.set_state(other, RequestState.ERROR, error_message="x")
    q = bus.subscribe()  # subscribe after setup so only the deletes' events land
    assert c.delete(f"/api/requests/{rid}").status_code == 200
    assert q.get_nowait() == ("queue", None)
    assert c.post("/api/requests/clear-failed").status_code == 200
    assert q.get_nowait() == ("queue", None)


def test_store_changes_reach_the_bus_as_bundles(tmp_path):
    bus = EventBus()
    q = bus.subscribe()
    app, store, _ = make(tmp_path, bus=bus)
    AppClient(app)  # create_app wires the listener; no request needed
    rid = store.add_request("q", RequestKind.TEXT)
    name, data = q.get_nowait()
    assert name == "request" and data["request"]["id"] == rid and data["candidates"] == []
    store.reset_inflight()
    assert q.get_nowait() == ("queue", None)


async def test_events_endpoint_streams_status_first():
    # httpx's ASGITransport (0.28.1) fully drains an ASGI app's response before TestClient/AsyncClient
    # hands back anything -- even headers -- so it cannot exercise an endpoint whose stream never ends
    # on its own (it awaits the whole call before returning, and this one never returns). Call the
    # route's endpoint directly instead; the SSE wire format itself is pinned byte-for-byte in
    # tests/test_stream.py, so this only needs to prove `stream.router` wires it up correctly.
    from flackey.web.stream import router

    bus = EventBus()
    status = Status(bus, telegram_authorized=True, worker_running=False, setup_done=False)
    route = next(rt for rt in router(bus, status).routes if rt.path == "/api/events")
    resp = await route.endpoint()
    assert resp.media_type == "text/event-stream"
    first = await anext(resp.body_iterator)
    assert first.startswith("event: status\ndata: ")
    assert json.loads(first.split("data: ", 1)[1])["telegram_authorized"] is True
    await resp.body_iterator.aclose()


def test_events_route_is_mounted(client):
    # The test above proves the SSE wiring by calling stream.router()'s endpoint directly (it can't go
    # through the app -- see that test's comment); this proves create_app actually includes that
    # router, without opening the never-ending stream body. `app.include_router(...)` wraps the sub-router
    # in an internal `fastapi.routing._IncludedRouter` on `app.routes` rather than a flattened route with a
    # `.path` (confirmed against the pinned fastapi==0.141.1: `[r.path for r in app.routes]` raises
    # AttributeError on that wrapper), so this resolves the route the public way instead.
    c, _, _ = client
    assert c.app.url_path_for("events") == "/api/events"


def test_spectrogram(client, tmp_path: Path):
    c, store, settings = client
    rid = store.add_request("bad one", RequestKind.TEXT)
    settings.spectrogram_dir.mkdir(parents=True, exist_ok=True)
    png = settings.spectrogram_dir / "s.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n")
    rj = store.add_rejection(rid, "cutoff", 320, 16000, png)
    r = c.get(f"/api/rejections/{rj}/spectrogram.png")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    assert c.get("/api/rejections/999/spectrogram.png").status_code == 404


def test_spectrogram_is_served_only_from_the_spectrogram_folder(client, tmp_path: Path):
    """A row whose path points anywhere else -- written by hand, by an older build, or through `..` --
    is not this route's to read."""
    c, store, settings = client
    settings.spectrogram_dir.mkdir(parents=True, exist_ok=True)
    outside = tmp_path / "secret.png"
    outside.write_bytes(b"\x89PNG\r\n\x1a\nnot yours")
    for path in (outside, settings.spectrogram_dir / ".." / ".." / "secret.png"):
        rj = store.add_rejection(store.add_request("q", RequestKind.TEXT), "cutoff", 320, 16000, path)
        assert c.get(f"/api/rejections/{rj}/spectrogram.png").status_code == 404


def test_rejected_audio_refuses_a_path_that_climbs_out_of_the_folder(client, tmp_path: Path):
    """`is_relative_to` compares strings, so `rejected/../../secret.mp3` passes it unless both sides are
    resolved first."""
    c, store, settings = client
    settings.rejected_dir.mkdir(parents=True, exist_ok=True)
    (tmp_path / "secret.mp3").write_bytes(b"not yours")
    climbing = settings.rejected_dir / ".." / ".." / "secret.mp3"
    rj = store.add_rejection(store.add_request("q", RequestKind.TEXT), "x", 320, None, None,
                             kind="different_recording", audio_path=climbing)
    assert c.get(f"/api/rejections/{rj}/audio").status_code == 404


def test_deleting_a_request_never_unlinks_a_path_that_climbs_out_of_the_folder(client, tmp_path: Path):
    c, store, settings = client
    settings.rejected_dir.mkdir(parents=True, exist_ok=True)
    precious = tmp_path / "precious.mp3"
    precious.write_bytes(b"keep me")
    rid = store.add_request("q", RequestKind.TEXT)
    store.add_rejection(rid, "x", 320, None, None, kind="different_recording",
                        audio_path=settings.rejected_dir / ".." / ".." / "precious.mp3")
    store.set_state(rid, RequestState.REJECTED)

    assert c.delete(f"/api/requests/{rid}").status_code == 200
    assert precious.read_bytes() == b"keep me"


def _kept_rejection(store, settings, name="kept.mp3") -> tuple[int, Path]:
    rid = store.add_request("wrong take", RequestKind.TEXT)
    settings.rejected_dir.mkdir(parents=True, exist_ok=True)
    kept = settings.rejected_dir / name
    kept.write_bytes(b"ID3fake audio")
    rj = store.add_rejection(rid, "a different recording: best score 0.77 below 0.79", 320, None, None,
                             kind="different_recording", audio_path=kept)
    return rj, kept


def test_the_queue_hands_the_page_both_halves_of_the_comparison(client):
    """The block the owner decides with is drawn from two fields on one list payload: the path to the copy
    that was kept, and what it was checked against. Either one missing and the page silently draws nothing
    at all -- no error, no empty state, just a rejected row that looks like every other rejected row. The
    routes that serve the audio are tested above; this pins the list that says there is any to serve."""
    c, store, settings = client
    rj, _kept = _kept_rejection(store, settings)
    rid = store.get_rejection(rj).request_id
    store.set_state(rid, RequestState.REJECTED)
    store.set_reference(rid, {"kind": "deezer", "ref": "3135556", "needles": [[1]], "full": [1],
                              "excerpt_start_s": 95.0, "excerpt_s": 30.0})

    b = next(b for b in c.get("/api/queue").json() if b["request"]["id"] == rid)

    assert b["rejection"]["kind"] == "different_recording"
    assert b["rejection"]["audio_path"].endswith("kept.mp3")
    assert b["reference"] == {"kind": "deezer", "ref": "3135556", "excerpt_start_s": 95.0}


def test_rejected_audio_serves_the_copy_that_was_kept(client):
    c, store, settings = client
    rj, kept = _kept_rejection(store, settings)

    r = c.get(f"/api/rejections/{rj}/audio")

    assert r.status_code == 200 and r.headers["content-type"] == "audio/mpeg"
    assert r.content == kept.read_bytes()


def test_rejected_audio_404s_when_there_is_nothing_to_play(client, tmp_path: Path):
    """Three ways to have no copy, one answer. A quality rejection never kept one; a kept one can be
    filed or cleaned up out from under the row; and a path outside `rejected_dir` is not this route's to
    read, whichever build or hand wrote it there."""
    c, store, settings = client
    rid = store.add_request("q", RequestKind.TEXT)
    quality = store.add_rejection(rid, "bitrate too low", 128, 15000, None)
    assert c.get(f"/api/rejections/{quality}/audio").status_code == 404
    assert c.get("/api/rejections/999/audio").status_code == 404

    rj, kept = _kept_rejection(store, settings, "gone.mp3")
    kept.unlink()
    assert c.get(f"/api/rejections/{rj}/audio").status_code == 404

    elsewhere = tmp_path / "secret.mp3"
    elsewhere.write_bytes(b"not yours")
    stray = store.add_rejection(store.add_request("q2", RequestKind.TEXT), "x", 320, None, None,
                                kind="different_recording", audio_path=elsewhere)
    assert c.get(f"/api/rejections/{stray}/audio").status_code == 404


def test_rejected_audio_refuses_a_format_the_browser_cannot_play(client):
    # Refused rather than served under a guessed type: a wrong content type is a player that fails
    # silently, which reads as a broken feature instead of an unplayable file.
    c, store, settings = client
    rj, _kept = _kept_rejection(store, settings, "odd.aac")
    r = c.get(f"/api/rejections/{rj}/audio")
    assert r.status_code == 415 and "aac" in r.json()["detail"]


def test_delete_takes_the_kept_copy_with_the_row(client):
    c, store, settings = client
    rj, kept = _kept_rejection(store, settings)
    rid = store.get_rejection(rj).request_id
    store.set_state(rid, RequestState.REJECTED)

    assert c.delete(f"/api/requests/{rid}").status_code == 200

    assert not kept.exists()


def test_clear_failed_takes_the_kept_copies_too(client):
    c, store, settings = client
    rj, kept = _kept_rejection(store, settings)
    rid = store.get_rejection(rj).request_id
    store.set_state(rid, RequestState.REJECTED)

    assert c.post("/api/requests/clear-failed").json()["removed"] == [rid]

    assert not kept.exists()


def test_static_ui_mount(tmp_path: Path):
    ui = tmp_path / "ui"
    ui.mkdir()
    (ui / "index.html").write_text("<h1>crate</h1>")
    app, _, _ = make(tmp_path, ui_dir=ui)
    c = AppClient(app)
    assert "crate" in c.get("/").text and c.get("/api/health").status_code == 200


def test_missing_ui_explains_itself(client):
    c, _, _ = client
    r = c.get("/")
    assert r.status_code == 200 and "npm" in r.text


def test_library_playlists_stats(client, tmp_path: Path):
    c, store, settings = client
    store.upsert_catalog_track(CatalogTrack(id=7, artist="A", title="T", mix_name="Original Mix", label="TIP Records", genre="G",
                                            release_date="2016-03-01"))
    tid = store.add_track(path=tmp_path / "a.mp3", fmt="mp3", bitrate_kbps=320, cutoff_hz=19800, file_size=5,
                          artist="A", title="T", mix_name="Original Mix", duration_s=1, isrc=None,
                          catalog_track_id=7, request_id=None)
    other = store.add_track(path=tmp_path / "b.mp3", fmt="mp3", bitrate_kbps=320, cutoff_hz=19800, file_size=5,
                            artist="B", title="U", mix_name="Original Mix", duration_s=1, isrc=None,
                            catalog_track_id=None, request_id=None)
    pid = store.upsert_playlist("u", "Goa Set")
    store.add_playlist_track(pid, tid, 1)
    lib = c.get("/api/library", params={"q": "t"}).json()
    assert lib[0]["id"] == tid and lib[0]["catalog"]["genre"] == "G" and lib[0]["path"].endswith("a.mp3")
    assert c.get("/api/library", params={"q": "zzz"}).json() == []
    lib_by_label = c.get("/api/library", params={"q": "TIP"}).json()
    assert [t["id"] for t in lib_by_label] == [tid]
    assert [t["id"] for t in c.get("/api/library", params={"playlist_id": pid}).json()] == [tid]
    assert [t["id"] for t in c.get("/api/library").json()] == [other, tid]
    pl = c.get("/api/playlists").json()[0]
    assert pl["track_ids"] == [tid] and pl["file"] == str(settings.library_root / "Playlists" / "Goa Set.m3u8")
    assert pl["track_positions"] == [1]
    s = c.get("/api/stats").json()
    assert s["tracks"] == 2 and s["bytes"] == 10 and s["by_genre"] == {"G": 1} and s["playlists"] == 1
    assert s["library_root"] == str(settings.library_root)


def test_settings_get_and_put(client, tmp_path: Path):
    c, _, settings = client
    s = c.get("/api/settings").json()
    assert s == {"library_root": str(settings.library_root), "data_dir": str(settings.data_dir),
                 "version": __version__, "telegram_configured": True,
                 "log_path": str(settings.data_dir / "flackey.log"),
                 "soulseek_enabled": False, "slskd_url": settings.slskd_url,
                 "slskd_downloads_dir": str(settings.slskd_downloads),
                 "lossless_filing_format": settings.lossless_filing_format,
                 "library_layout": "artist", "library_layouts": ["artist", "month", "day", "flat"],
                 "auto_update_check": True,
                 "ports": {"app": {"port": 8765, "host": "127.0.0.1", "public": False},
                           "sidecar": {"port": 5030, "host": "127.0.0.1", "public": False},
                           "soulseek_listen": {"port": 50300, "host": "0.0.0.0", "public": True}},
                 "ranking": {"max_picks": 4, "max_queue": None, "fingerprint_min": 0.79},
                 "filing_formats": ["aiff", "wav", "flac"], "default_filing_format": default_filing_format()}
    # Only one of the three is reachable from outside this machine, and it has to be: peers connect
    # inbound to it to download from the shared library.
    assert [k for k, v in s["ports"].items() if v["public"]] == ["soulseek_listen"]
    assert "slskd_api_key" not in c.get("/api/settings").text
    new = tmp_path / "elsewhere"
    r = c.put("/api/settings", json={"library_root": str(new)})
    assert r.status_code == 200 and r.json()["library_root"] == str(new)
    assert settings.library_root == new and new.is_dir() and settings.settings_path.exists()
    assert c.put("/api/settings", json={"library_root": "relative/dir"}).status_code == 400
    assert c.put("/api/settings", json={"library_root": ""}).status_code == 400
    r = c.put("/api/settings", json={"library_root": str(new), "auto_update_check": False})
    assert r.status_code == 200 and r.json()["auto_update_check"] is False
    assert settings.auto_update_check is False
    assert c.get("/api/settings").json()["auto_update_check"] is False


def test_reveal_only_opens_places_the_app_itself_named(tmp_path: Path):
    """The opener is only ever handed a path flackey built: the library folder, the data folder, the log,
    a playlist export, or a filed track's own row. A name that arrives in the request is compared as a
    string and then thrown away, so a real file that was never filed -- and a traversal out of the
    library -- are both simply not on the list."""
    opened = []
    app, store, settings = make(tmp_path, opener=lambda p: opened.append(p))
    c = AppClient(app)
    filed = settings.library_root / "A" / "x.mp3"
    filed.parent.mkdir(parents=True)
    filed.write_bytes(b"x")
    store.add_track(path=filed, fmt="mp3", bitrate_kbps=320, cutoff_hz=19800, file_size=1,
                    artist="A", title="T", mix_name="Original Mix", duration_s=1, isrc=None,
                    catalog_track_id=None, request_id=None)
    log_path = settings.data_dir / "flackey.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("")
    stray = settings.library_root / "A" / "never-filed.mp3"
    stray.write_bytes(b"x")

    assert c.post("/api/reveal", json={"path": str(filed)}).json() == {"ok": True} and opened == [filed]
    assert c.post("/api/reveal", json={"path": str(settings.library_root)}).status_code == 200
    assert c.post("/api/reveal", json={"path": str(settings.data_dir)}).status_code == 200
    assert c.post("/api/reveal", json={"path": str(log_path)}).status_code == 200
    assert c.post("/api/reveal", json={"path": str(stray)}).status_code == 400
    assert c.post("/api/reveal", json={"path": str(tmp_path / "outside.mp3")}).status_code == 400
    assert c.post("/api/reveal", json={"path": str(settings.library_root / ".." / "outside.mp3")}).status_code == 400
    assert c.post("/api/reveal", json={"path": "/etc/passwd"}).status_code == 400
    assert opened == [filed, settings.library_root, settings.data_dir, log_path]

    filed.unlink()
    assert c.post("/api/reveal", json={"path": str(filed)}).status_code == 404


def test_reveal_shows_a_playlist_export_by_its_exported_name(tmp_path: Path):
    """The playlists page hands back the .m3u8 path it computed; /reveal has to recognise that same path
    without trusting it, so the name is rebuilt from the playlist rows rather than taken from the body."""
    opened = []
    app, store, _ = make(tmp_path, opener=lambda p: opened.append(p))
    c = AppClient(app)
    # Two playlists that sanitize to the same stem: the second gets a " (id)" suffix, and /reveal has to
    # recognise that suffixed name too -- which it does by calling the same playlist_names() the route does.
    store.upsert_playlist("https://example.test/p", "Late Night")
    store.upsert_playlist("https://example.test/q", "Late Night")
    exports = [pl["file"] for pl in c.get("/api/playlists").json()]
    assert len({Path(e).name for e in exports}) == 2
    for e in exports:
        Path(e).parent.mkdir(parents=True, exist_ok=True)
        Path(e).write_text("#EXTM3U\n")
        assert c.post("/api/reveal", json={"path": e}).status_code == 200
    assert opened == [Path(e) for e in exports]


def test_unhandled_exception_becomes_a_plain_words_500(tmp_path: Path):
    app, _, _ = make(tmp_path)

    @app.get("/api/boom")
    async def boom():
        raise RuntimeError("kaboom")

    c = AppClient(app, raise_server_exceptions=False)
    r = c.get("/api/boom")
    assert r.status_code == 500
    assert r.json() == {"detail": "Something went wrong. The log has the details."}


def test_reveal_uses_open_dash_r_for_files_and_directories_on_macos(tmp_path: Path, monkeypatch):
    from flackey.web.library import reveal_path

    calls = []
    monkeypatch.setattr("flackey.web.library.subprocess.Popen",
                        lambda cmd, **kw: calls.append((cmd, kw)))
    monkeypatch.setattr("flackey.web.library.sys.platform", "darwin")
    f = tmp_path / "x.mp3"
    f.write_bytes(b"x")
    d = tmp_path / "a_dir"
    d.mkdir()
    reveal_path(f)
    reveal_path(d)
    assert [c for c, _ in calls] == [["open", "-R", str(f)], ["open", "-R", str(d)]]
    assert all("creationflags" not in kw for _, kw in calls)  # the Mac call is the call it always was


def test_reveal_on_windows_selects_a_file_in_explorer_and_opens_a_folder(tmp_path: Path, monkeypatch):
    """A file is shown selected in File Explorer; a folder is opened with `os.startfile`, the way a
    double-click would. The `/select,` argument keeps its quotes after the comma, where Explorer wants
    them -- a path with a space in it otherwise opens Documents instead."""
    import os

    from flackey.web.library import reveal_path

    calls, started = [], []
    monkeypatch.setattr("flackey.web.library.subprocess.Popen",
                        lambda cmd, **kw: calls.append((cmd, kw)))
    monkeypatch.setattr(os, "startfile", started.append, raising=False)
    f = tmp_path / "DJ Library" / "x.flac"
    f.parent.mkdir()
    f.write_bytes(b"x")
    monkeypatch.setattr("flackey.web.library.sys.platform", "win32")
    reveal_path(f)
    reveal_path(f.parent)
    assert [c for c, _ in calls] == [f'explorer /select,"{f}"']
    assert calls[0][1]["creationflags"] == 0x08000000
    assert started == [f.parent]


def test_tools_and_setup_done(client):
    c, store, _ = client
    t = c.get("/api/tools").json()
    assert set(t) == {"ffmpeg", "ffprobe", "yt_dlp", "fpcalc"} and all(isinstance(v, bool) for v in t.values())
    assert c.get("/api/health").json()["setup_done"] is False
    assert c.post("/api/setup/done").json() == {"setup_done": True}
    assert c.get("/api/health").json()["setup_done"] is True and store.get_setting("setup_done") == "1"
    assert c.post("/api/setup/reset").json() == {"setup_done": False}
    assert c.get("/api/health").json()["setup_done"] is False and store.get_setting("setup_done") == "0"


def test_setup_soulseek_get_before_and_after_post(client):
    c, _, settings = client
    assert c.get("/api/setup/soulseek").json() == {"configured": False, "username": None}

    r = c.post("/api/setup/soulseek", json={"username": "digger", "password": "not-a-real-password"})
    assert r.status_code == 200
    # No link wired into this app, so the credentials only take effect on the next start and the
    # response says so rather than promising a connection nothing is going to make.
    assert r.json() == {"ok": True, "restart_required": True, "connecting": False}
    assert c.get("/api/setup/soulseek/status").json() == {"state": "idle", "username": None, "error": None}

    assert c.get("/api/setup/soulseek").json() == {"configured": True, "username": "digger"}
    assert settings.slskd_api_key  # the running process now knows Soulseek is configured


def test_setup_soulseek_password_is_404_until_one_is_saved_and_then_hands_it_back(client):
    """The password is generated for the owner and there is no way to reset it on Soulseek, so the app
    that generated it has to be able to show it to them. It lives on its own path rather than on
    GET /setup/soulseek, which the wizard polls -- a secret must not ride along on a poll."""
    c, _, _ = client
    assert c.get("/api/setup/soulseek/password").status_code == 404
    assert "password" not in c.get("/api/setup/soulseek").json()
    c.post("/api/setup/soulseek", json={"username": "digger", "password": "not-a-real-password"})
    assert c.get("/api/setup/soulseek/password").json() == {"username": "digger",
                                                            "password": "not-a-real-password"}
    assert "password" not in c.get("/api/setup/soulseek").json()


def test_helper_web_login_is_separate_and_only_returned_on_demand(client):
    c, _, settings = client
    assert c.get("/api/setup/slskd/credentials").status_code == 404
    from flackey.slskd_config import read_web_credentials, write_credentials
    write_credentials(settings.data_dir, "digger", "soulseek-secret")
    username, password = read_web_credentials(settings.data_dir)
    assert username == "flackey" and password != "soulseek-secret"
    assert c.get("/api/setup/slskd/credentials").json() == {"username": username, "password": password}
    assert password not in c.get("/api/settings").text


def test_refresh_library_forgets_files_removed_in_finder(client, tmp_path):
    c, store, _ = client
    file = tmp_path / "track.mp3"
    file.write_bytes(b"audio")
    tid = store.add_track(path=file, fmt="mp3", bitrate_kbps=320, cutoff_hz=18000,
                          file_size=5, artist="A", title="T", mix_name="Original Mix",
                          duration_s=100, isrc=None, catalog_track_id=None, request_id=None)
    assert any(t["id"] == tid for t in c.get("/api/library").json())
    file.unlink()
    assert c.post("/api/library/refresh").json() == {"removed": 1}
    assert c.get("/api/library").json() == []
    assert c.get("/api/stats").json()["tracks"] == 0

def test_setup_soulseek_post_asks_the_link_to_connect_when_one_is_wired_in(tmp_path: Path):
    """With a link the wizard can answer "did my account work?" on the spot: the POST starts a connect
    and the status endpoint carries the result, so nothing tells the owner to restart and find out."""
    class FakeLink:
        def __init__(self):
            self.state, self.calls = {"state": "idle", "username": None, "error": None}, 0

        def start_connect(self):
            self.calls += 1
            self.state = {"state": "connecting", "username": None, "error": None}
            return dict(self.state)

    link = FakeLink()
    app, _, _ = make(tmp_path, link=link)
    c = AppClient(app)
    r = c.post("/api/setup/soulseek", json={"username": "digger", "password": "not-a-real-password"})
    assert r.json() == {"ok": True, "restart_required": False, "connecting": True}
    assert link.calls == 1
    assert c.get("/api/setup/soulseek/status").json()["state"] == "connecting"

    link.state = {"state": "connected", "username": "digger", "error": None}
    assert c.get("/api/setup/soulseek/status").json() == {"state": "connected", "username": "digger",
                                                         "error": None}


def test_setup_soulseek_post_writes_the_key_only_into_the_managed_slskd_yml(client):
    # "one copy only": save_settings is never called with the generated key, so settings.json --
    # world-readable in intent, mode 0600 in practice -- never carries a second copy of it.
    c, _, settings = client
    c.post("/api/setup/soulseek", json={"username": "digger", "password": "not-a-real-password"})
    assert not settings.settings_path.exists()
    from flackey.slskd_config import read_api_key
    assert read_api_key(settings.data_dir) == settings.slskd_api_key


def test_setup_soulseek_400_never_carries_the_username_or_password(client):
    c, _, _ = client
    r = c.post("/api/setup/soulseek", json={"username": "", "password": "not-a-real-password"})
    assert r.status_code == 400 and "not-a-real-password" not in r.json()["detail"]

    r = c.post("/api/setup/soulseek", json={"username": "a-real-looking-username", "password": ""})
    assert r.status_code == 400 and "a-real-looking-username" not in r.json()["detail"]


def test_setup_soulseek_key_never_reaches_settings_or_health(client):
    c, _, settings = client
    c.post("/api/setup/soulseek", json={"username": "digger", "password": "not-a-real-password"})
    key = settings.slskd_api_key
    assert key  # the fixture actually generated one, or this test would prove nothing
    r_settings, r_health = c.get("/api/settings"), c.get("/api/health")
    assert "slskd_api_key" not in r_settings.json() and "slskd_api_key" not in r_health.json()
    assert key not in r_settings.text and key not in r_health.text


def test_setup_soulseek_password_never_reaches_a_response_or_a_log(client, caplog):
    """The key has its own leak test above; this one follows the *password*, which is the secret the
    user actually typed and the one that exists nowhere else. It must not come back from any endpoint
    that reports configuration, nor reach a log record at any level."""
    c, _, settings = client
    secret = "not-a-real-password-9d41c0"
    with caplog.at_level(logging.DEBUG):
        saved = c.post("/api/setup/soulseek", json={"username": "digger", "password": secret})
    assert saved.status_code == 200
    assert secret not in saved.text, "password echoed straight back by the endpoint that took it"
    for path in ("/api/setup/soulseek", "/api/settings", "/api/health"):
        assert secret not in c.get(path).text, f"password leaked from {path}"
    assert not [r for r in caplog.records if secret in r.getMessage()]
    # and it is on disk only in the one file, readable only by its owner
    cfg = settings.data_dir / "slskd" / "slskd.yml"
    assert secret in cfg.read_text(encoding="utf-8")
    assert sys.platform == "win32" or cfg.stat().st_mode & 0o777 == 0o600  # Windows has no mode bits


class _FakeInstall:
    """Stands in for slskd_binary.install: records the call, optionally reports progress, and can
    fail. The real one downloads 58 MB, so nothing in this suite may reach it."""

    def __init__(self, *, fail: str | None = None, chunks: list[tuple[int, int]] | None = None):
        self.fail, self.chunks, self.calls = fail, chunks or [], []

    def __call__(self, data_dir, *, on_progress=None):
        self.calls.append(data_dir)
        for done, total in self.chunks:
            if on_progress:
                on_progress(done, total)
        if self.fail:
            from flackey.slskd_binary import SlskdBinaryError
            raise SlskdBinaryError(self.fail)
        return data_dir / "slskd" / "bin" / "slskd"


@pytest.fixture
def slskd_client(client, monkeypatch):
    """`client` with slskd_url pointed at a closed port. The default is 127.0.0.1:5030, which on a
    developer's own machine is a *live* slskd -- a health probe there would make these tests pass
    here and fail everywhere else."""
    c, store, settings = client
    settings.slskd_url = "http://127.0.0.1:1"
    return c, store, settings


def test_setup_slskd_reports_nothing_installed_and_nothing_running(slskd_client):
    c, _, _ = slskd_client
    from flackey.slskd_binary import SLSKD_VERSION
    assert c.get("/api/setup/slskd").json() == {"installed": False, "running": False,
                                                "version": SLSKD_VERSION}


def test_post_setup_slskd_installs_in_the_background_and_progress_follows_it(slskd_client, monkeypatch):
    c, _, settings = slskd_client
    fake = _FakeInstall(chunks=[(10, 100), (100, 100)])
    monkeypatch.setattr("flackey.web.library.install_slskd", fake)

    assert c.get("/api/setup/slskd/progress").json()["state"] == "idle"
    assert c.post("/api/setup/slskd").json() == {"state": "downloading"}
    # FastAPI runs background tasks after the response; TestClient completes them before returning
    assert fake.calls == [settings.data_dir]
    assert c.get("/api/setup/slskd/progress").json() == {"state": "done", "done": 100, "total": 100,
                                                         "error": None}


def test_post_setup_slskd_surfaces_a_failed_install_as_plain_text(slskd_client, monkeypatch):
    c, _, _ = slskd_client
    monkeypatch.setattr("flackey.web.library.install_slskd",
                        _FakeInstall(fail="downloaded archive failed SHA-256 verification"))
    c.post("/api/setup/slskd")
    progress = c.get("/api/setup/slskd/progress").json()
    assert progress["state"] == "error"
    assert progress["error"] == "downloaded archive failed SHA-256 verification"


def test_post_setup_slskd_does_not_download_again_when_it_is_already_installed(slskd_client, monkeypatch):
    c, _, settings = slskd_client
    from flackey.slskd_binary import binary_path
    binary = binary_path(settings.data_dir)
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    fake = _FakeInstall()
    monkeypatch.setattr("flackey.web.library.install_slskd", fake)

    assert c.post("/api/setup/slskd").json() == {"state": "done"}
    assert fake.calls == []
    assert c.get("/api/setup/slskd").json()["installed"] is True


class FakeLogin:
    def __init__(self):
        self.authorized, self.calls = False, []

    async def status(self):
        return {"authorized": self.authorized, "configured": True, "phone_masked": "+31 •••• ••42" if self.authorized else None}

    async def start_qr(self):
        return {"id": "q1", "url": "tg://login?token=x", "expires_at": "2026-09-06T10:00:30+00:00"}

    def qr_state(self, qr_id):
        return "waiting" if qr_id == "q1" else "unknown"

    async def password(self, pw):
        from flackey.telegram import LoginError
        if pw == "bad":
            raise LoginError("Wrong password. Try again.")
        self.authorized = True
        return "done"

    async def send_code(self, phone):
        self.calls.append(phone)

    async def sign_in(self, phone, code):
        return "password_needed" if code == "2fa" else "done"

    async def log_out(self):
        self.authorized = False


def test_telegram_routes(tmp_path):
    from flackey.events import Status
    status = Status(None, telegram_authorized=True, worker_running=True, setup_done=True)
    login = FakeLogin()
    app, _, _ = make(tmp_path, status=status, login=login)
    c = AppClient(app)
    assert c.get("/api/telegram/status").json()["authorized"] is False
    assert c.post("/api/telegram/qr").json()["id"] == "q1"
    assert c.get("/api/telegram/qr/q1").json() == {"state": "waiting"}
    assert c.post("/api/telegram/password", json={"password": "bad"}).status_code == 400
    assert c.post("/api/telegram/password", json={"password": "ok"}).json() == {"state": "done"}
    assert c.post("/api/telegram/phone", json={"phone": "+31612345642"}).json() == {"ok": True} and login.calls == ["+31612345642"]
    assert c.post("/api/telegram/code", json={"phone": "+31612345642", "code": "2fa"}).json() == {"state": "password_needed"}
    assert c.post("/api/telegram/logout").json() == {"ok": True}
    assert status["telegram_authorized"] is False and login.authorized is False


def test_telegram_routes_without_a_login_service(client):
    c, _, _ = client
    assert c.get("/api/telegram/status").json() == {"authorized": False, "configured": False, "phone_masked": None}
    assert c.post("/api/telegram/qr").status_code == 503


def test_telegram_route_failure_becomes_a_plain_words_503(tmp_path):
    from flackey.events import Status

    class BrokenLogin(FakeLogin):
        async def start_qr(self):
            raise RuntimeError("boom")

    status = Status(None, telegram_authorized=True, worker_running=True, setup_done=True)
    app, _, _ = make(tmp_path, status=status, login=BrokenLogin())
    r = AppClient(app).post("/api/telegram/qr")
    assert r.status_code == 503
    assert r.json()["detail"] == "Telegram isn't reachable right now. Try again in a moment."


def test_telegram_status_survives_a_client_that_was_never_connected(tmp_path):
    # The credential-guard path in app.py builds TelegramLogin(client, False) on a TelegramClient that
    # is deliberately never connected. TelegramLogin.status() short-circuits on `not configured` before
    # touching the client (see tests/test_telegram.py), so this proves the route-level effect: the setup
    # screen's first call must not 500 because of a client that would raise if it were ever called.
    from flackey.telegram import TelegramLogin

    class Disconnected:
        async def is_user_authorized(self):
            raise ConnectionError("Cannot send requests while disconnected")

    login = TelegramLogin(Disconnected(), False)
    app, _, _ = make(tmp_path, login=login)
    r = AppClient(app).get("/api/telegram/status")
    assert r.status_code == 200
    assert r.json() == {"authorized": False, "configured": False, "phone_masked": None}


def test_pick_folder_with_fake_picker(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    fake_path = tmp_path / "x"
    fake_picker = lambda initial: fake_path
    app, _, _ = make(tmp_path, picker=fake_picker)
    c = AppClient(app)
    r = c.post("/api/pick-folder", json={"initial": None})
    assert r.status_code == 200 and r.json() == {"path": str(fake_path)}


def test_pick_folder_returns_null_when_cancelled(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    fake_picker = lambda initial: None
    app, _, _ = make(tmp_path, picker=fake_picker)
    c = AppClient(app)
    r = c.post("/api/pick-folder", json={"initial": "/Users/me"})
    assert r.status_code == 200 and r.json() == {"path": None}


def test_pick_folder_returns_500_on_picker_error(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")

    def failing_picker(initial):
        raise RuntimeError("boom")

    app, _, _ = make(tmp_path, picker=failing_picker)
    c = AppClient(app)
    r = c.post("/api/pick-folder", json={"initial": None})
    assert r.status_code == 500 and r.json()["detail"] == "Couldn't open the folder chooser."


def test_pick_folder_returns_501_when_there_is_no_dialog_to_open(tmp_path, monkeypatch):
    """Off macOS with no window to lend its dialog -- `flackey start --no-browser` on Windows or Linux --
    there is nothing to open, and the page keeps its text field."""
    app, _, _ = make(tmp_path)
    c = AppClient(app)
    monkeypatch.setattr(sys, "platform", "linux")
    r = c.post("/api/pick-folder", json={"initial": None})
    assert r.status_code == 501
    assert r.json()["detail"] == "Choosing a folder needs the Flackey window. Type the folder's path instead."


def test_pick_folder_available_reflects_platform(tmp_path, monkeypatch):
    app, _, _ = make(tmp_path)
    c = AppClient(app)
    monkeypatch.setattr(sys, "platform", "darwin")
    r = c.get("/api/pick-folder/available")
    assert r.status_code == 200 and r.json() == {"available": True}
    monkeypatch.setattr(sys, "platform", "linux")
    r = c.get("/api/pick-folder/available")
    assert r.json() == {"available": False}
    monkeypatch.setattr(sys, "platform", "win32")
    assert c.get("/api/pick-folder/available").json() == {"available": False}


def test_on_windows_the_window_s_dialog_is_found_at_request_time(tmp_path, monkeypatch):
    """The Windows dialog belongs to the pywebview window, which is created after the server is already
    serving. So the router asks on every request: unavailable until the window registers its dialog,
    available -- and used -- the moment it has."""
    registered: dict = {}
    app, _, _ = make(tmp_path, find_picker=lambda: registered.get("picker"))
    c = AppClient(app)
    monkeypatch.setattr(sys, "platform", "win32")
    assert c.get("/api/pick-folder/available").json() == {"available": False}
    assert c.post("/api/pick-folder", json={"initial": None}).status_code == 501

    asked: list = []
    chosen = tmp_path / "Music" / "DJ Library"
    registered["picker"] = lambda initial: asked.append(initial) or chosen

    assert c.get("/api/pick-folder/available").json() == {"available": True}
    r = c.post("/api/pick-folder", json={"initial": str(tmp_path)})
    assert r.status_code == 200 and r.json() == {"path": str(chosen)}
    assert asked == [tmp_path]


def test_choose_folder_with_valid_path(monkeypatch, tmp_path):
    from flackey.web.pick import choose_folder
    def mock_run(*args, **kwargs):
        class Result:
            returncode = 0
            stdout = "/Users/me/Music/DJ Library/\n"
            stderr = ""
        return Result()
    monkeypatch.setattr(subprocess, "run", mock_run)
    result = choose_folder(Path("/Users/me"))
    assert result == Path("/Users/me/Music/DJ Library")


def test_choose_folder_when_cancelled(monkeypatch):
    from flackey.web.pick import choose_folder
    def mock_run(*args, **kwargs):
        class Result:
            returncode = 1
            stdout = ""
            stderr = "User canceled."
        return Result()
    monkeypatch.setattr(subprocess, "run", mock_run)
    result = choose_folder(None)
    assert result is None


def test_choose_folder_on_error(monkeypatch):
    from flackey.web.pick import choose_folder
    def mock_run(*args, **kwargs):
        class Result:
            returncode = 1
            stdout = ""
            stderr = "Permission denied"
        return Result()
    monkeypatch.setattr(subprocess, "run", mock_run)
    try:
        choose_folder(None)
        assert False, "Should raise RuntimeError"
    except RuntimeError as e:
        assert str(e) == "Permission denied"


def test_choose_folder_escapes_quotes_and_backslashes(monkeypatch):
    from flackey.web.pick import choose_folder
    captured_cmd = []
    def mock_run(cmd, **kwargs):
        captured_cmd.append(cmd)
        class Result:
            returncode = 0
            stdout = "/Users/me/My \"Crates\"\n"
            stderr = ""
        return Result()
    monkeypatch.setattr(subprocess, "run", mock_run)
    result = choose_folder(Path('/Users/me/My "Crates"'))
    assert result == Path('/Users/me/My "Crates"')
    # Find the osascript command that has the default location
    script_arg = [arg for arg in captured_cmd[0] if "default location" in arg]
    assert len(script_arg) == 1
    # Verify the quote is escaped in the AppleScript string
    assert 'My \\"Crates\\"' in script_arg[0]
    assert 'My "Crates"' not in script_arg[0] or 'My \\"Crates\\"' in script_arg[0]


def _attempt(store, rid, outcome, *, first_byte_ms=None, total_ms=None, score=None, peer="p"):
    aid = store.add_attempt(rid, "soulseek", "q")
    store.update_attempt(aid, outcome=outcome, first_byte_ms=first_byte_ms, total_ms=total_ms,
                         report={"summary": "s", "chosen": {"username": peer, "path": "x\\f.flac"} if peer else None},
                         fingerprint={"status": "matched", "score": score} if score else None,
                         timeline=[{"t_ms": 0, "event": "outcome", "detail": {"outcome": outcome}}])
    return aid


def test_lossless_attempts_endpoint_lists_counts_and_medians(client):
    c, store, _ = client
    r1 = store.add_request("a", RequestKind.TEXT)
    r2 = store.add_request("b", RequestKind.TEXT)
    _attempt(store, r1, "filed", first_byte_ms=500, total_ms=40_000, score=0.98)
    _attempt(store, r2, "no_pick", peer=None)
    _attempt(store, r2, "filed", first_byte_ms=1500, total_ms=80_000, score=0.95)
    body = c.get("/api/lossless/attempts").json()
    assert body["counts"] == {"filed": 2, "no_pick": 1}
    assert body["median_first_byte_ms"] == 1000 and body["median_total_ms"] == 60_000
    assert [a["outcome"] for a in body["attempts"]] == ["filed", "no_pick", "filed"]       # newest first
    top = body["attempts"][0]
    assert top["request_id"] == r2 and top["peer"] == "p" and top["file"] == "f.flac" and top["score"] == 0.95
    assert "timeline" not in top and "report" not in top
    assert [a["id"] for a in c.get("/api/lossless/attempts?outcome=no_pick").json()["attempts"]] == [2]
    assert c.get("/api/health").json()["lossless"]["attempts_24h"] == {"filed": 2, "no_pick": 1}


class _Uploader:
    """A provider that answers the uploads question; `name` is what the page shows when it cannot."""

    name = "soulseek"

    def __init__(self, rows=None, error=None):
        self.rows, self.error = rows or [], error

    async def uploads(self):
        if self.error:
            raise self.error
        return self.rows


def _row(peer="loginty", pct=50, state="InProgress", size=1000):
    return {"id": "u1", "peer": peer, "file": "Dancing Galaxy.aiff", "folder": "DJ Library", "size": size,
            "bytes": size * pct // 100, "pct": pct, "state": state, "speed_bps": 1_500_000.0,
            "started_at": "2026-09-08T02:00:00Z", "ended_at": None}


def test_uploads_endpoint_reports_who_is_pulling_from_the_shared_library(client_with_worker):
    c, worker = client_with_worker
    worker.providers = [_Uploader([_row(), _row(peer="peer2", pct=100, state="Completed, Succeeded", size=400)])]
    body = c.get("/api/lossless/uploads").json()
    assert body["enabled"] is True and body["provider"] == "soulseek" and body["error"] is None
    assert [u["peer"] for u in body["uploads"]] == ["loginty", "peer2"]
    assert body["summary"] == {"total": 2, "active": 1, "completed": 1, "peers": 2, "bytes": 900}


def test_uploads_endpoint_is_calm_when_soulseek_is_off_or_the_sidecar_is_down(client_with_worker):
    """Two different "nothing to show" cases the page must tell apart: no provider configured at all, and a
    provider that cannot be reached right now. Neither is an error status - the tab still renders."""
    from flackey.source.lossless import LosslessUnavailable

    c, worker = client_with_worker
    worker.providers = []
    off = c.get("/api/lossless/uploads")
    assert off.status_code == 200 and off.json() == {
        "enabled": False, "provider": None, "uploads": [],
        "summary": {"total": 0, "active": 0, "completed": 0, "peers": 0, "bytes": 0}, "error": None}

    worker.providers = [_Uploader(error=LosslessUnavailable("slskd unreachable at http://slskd.test: ConnectError"))]
    down = c.get("/api/lossless/uploads").json()
    assert down["enabled"] is True and down["uploads"] == [] and "unreachable" in down["error"]
    # The sidecar's own words (its URL, the transport error) stay in the log; the page gets a plain sentence.
    assert "slskd.test" not in down["error"] and "ConnectError" not in down["error"]
    assert down["summary"]["total"] == 0


def test_request_bundle_carries_the_attempt_and_delete_drops_it(client, tmp_path: Path):
    c, store, settings = client
    rid = store.add_request("a", RequestKind.TEXT)
    aid = _attempt(store, rid, "verify_failed", peer="p")
    png = settings.spectrogram_dir / "req1-lossless-1.png"
    png.parent.mkdir(parents=True, exist_ok=True)
    png.write_bytes(b"png")
    store.update_attempt(aid, spectrogram_path=str(png))
    bundle = c.get(f"/api/requests/{rid}").json()
    assert bundle["attempt"]["id"] == aid and bundle["attempt"]["timeline"][-1]["event"] == "outcome"
    assert bundle["attempt"]["report"]["summary"] == "s"
    store.update_request(rid, state=RequestState.NOT_FOUND)
    assert c.delete(f"/api/requests/{rid}").status_code == 200
    assert store.list_attempts() == [] and not png.exists()


def test_track_bundle_has_format_and_evidence(client, tmp_path: Path):
    c, store, _ = client
    tid = store.add_track(path=tmp_path / "a.aiff", fmt="aiff", bitrate_kbps=1411, cutoff_hz=22050, file_size=5,
                          artist="A", title="T", mix_name="Original Mix", duration_s=1, isrc=None,
                          catalog_track_id=None, request_id=None, source="soulseek", source_fmt="flac",
                          bit_depth=16, sample_rate=44100)
    store.add_evidence(tid, "recording_match", {"status": "matched", "score": 0.98})
    t = c.get("/api/library").json()[0]
    assert t["format"] == {"fmt": "aiff", "bit_depth": 16, "sample_rate": 44100, "source": "soulseek",
                           "source_fmt": "flac", "label": "AIFF 16-bit/44.1 kHz, from FLAC via Soulseek"}
    assert t["evidence"] == [{"kind": "recording_match", "value": {"status": "matched", "score": 0.98}}]
    legacy = store.add_track(path=tmp_path / "b.mp3", fmt="mp3", bitrate_kbps=320, cutoff_hz=19800, file_size=5,
                             artist="B", title="T", mix_name="Original Mix", duration_s=1, isrc=None,
                             catalog_track_id=None, request_id=None)
    t = next(x for x in c.get("/api/library").json() if x["id"] == legacy)
    assert t["format"]["label"] == "MP3 320 kbps via Deezer" and t["evidence"] == []


def test_settings_never_expose_the_api_key_and_accept_lossless_keys(tmp_path: Path):
    app, _, settings = make(tmp_path)
    c = AppClient(app)
    settings.slskd_api_key = "secret-key-value"
    out = c.get("/api/settings").json()
    assert "secret-key-value" not in json.dumps(out) and out["soulseek_enabled"] is True
    assert out["slskd_url"] == "http://127.0.0.1:5030" and out["lossless_filing_format"] == default_filing_format()
    r = c.put("/api/settings", json={"library_root": str(tmp_path / "lib"), "slskd_api_key": "new-key",
                                     "slskd_url": "http://127.0.0.1:5031", "lossless_filing_format": "aiff"})
    assert r.status_code == 200 and "new-key" not in r.text and r.json()["lossless_filing_format"] == "aiff"
    saved = json.loads(settings.settings_path.read_text())
    assert saved["slskd_api_key"] == "new-key" and saved["slskd_url"] == "http://127.0.0.1:5031"
    assert c.put("/api/settings", json={"library_root": str(tmp_path / "lib"), "lossless_filing_format": "mp3"}).status_code == 400
    assert "secret" not in json.dumps(c.get("/api/health").json())
    assert "fpcalc" in c.get("/api/tools").json()


def test_settings_save_the_library_layout_and_refuse_an_unknown_one(tmp_path: Path):
    app, _, settings = make(tmp_path)
    c = AppClient(app)
    r = c.put("/api/settings", json={"library_root": str(tmp_path / "lib"), "library_layout": "day"})
    assert r.status_code == 200 and r.json()["library_layout"] == "day" and settings.library_layout == "day"
    assert json.loads(settings.settings_path.read_text())["library_layout"] == "day"
    bad = c.put("/api/settings", json={"library_root": str(tmp_path / "lib"), "library_layout": "genre"})
    assert bad.status_code == 400 and settings.library_layout == "day"


def test_the_api_key_never_appears_in_settings_or_health_responses(tmp_path: Path):
    # Belt-and-suspenders on top of the brief's test above: assert the *exact* key value is absent
    # from both raw response bodies (not just json.dumps of the parsed dict), covering any accidental
    # leak through a header, an error detail, or a field the parsed-dict check wouldn't catch.
    app, _, settings = make(tmp_path)
    c = AppClient(app)
    key = "sekrit-slskd-api-key-do-not-leak-1234567890"
    settings.slskd_api_key = key
    r_settings = c.get("/api/settings")
    r_health = c.get("/api/health")
    assert key not in r_settings.text and key not in r_health.text
    assert key not in json.dumps(r_settings.json()) and key not in json.dumps(r_health.json())
    # a PUT that changes the key must not echo it back in the response body either
    r_put = c.put("/api/settings", json={"library_root": str(tmp_path / "lib"), "slskd_api_key": "another-" + key})
    assert key not in r_put.text


@pytest.mark.parametrize("url,host,port,public", [
    ("http://127.0.0.1:5030", "127.0.0.1", 5030, False),
    ("http://localhost:9999", "localhost", 9999, False),
    ("https://slskd.lan:5030", "slskd.lan", 5030, True),      # a sidecar on another machine is not private
    ("http://192.168.1.9:5030", "192.168.1.9", 5030, True),
    ("http://127.0.0.1", "127.0.0.1", 80, False),             # no port given: the scheme's default
])
def test_reported_ports_are_read_from_this_install_not_assumed(tmp_path: Path, url, host, port, public):
    """Whatever this owner's install actually uses -- a moved sidecar, a non-default web port, a listen
    port they changed by hand. A settings screen quoting defaults the machine is not using is worse
    than one saying nothing."""
    app, _, settings = make(tmp_path)
    settings.slskd_url = url
    ports = AppClient(app).get("/api/settings").json()["ports"]
    assert ports["sidecar"] == {"port": port, "host": host, "public": public}


def test_the_listen_port_comes_from_the_managed_config_when_one_exists(tmp_path: Path):
    from flackey.slskd_config import config_path
    app, _, settings = make(tmp_path)
    assert AppClient(app).get("/api/settings").json()["ports"]["soulseek_listen"]["port"] == 50300
    p = config_path(settings.data_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"soulseek": {"listen_port": 51515}}))   # JSON is valid YAML
    assert AppClient(app).get("/api/settings").json()["ports"]["soulseek_listen"]["port"] == 51515


def test_a_non_loopback_app_host_is_reported_as_public(tmp_path: Path):
    """Serving the UI on 0.0.0.0 is a real choice an owner can make; the screen must not keep saying
    "this Mac only" once they have."""
    app, _, settings = make(tmp_path)
    settings.web_host = "0.0.0.0"
    assert AppClient(app).get("/api/settings").json()["ports"]["app"]["public"] is True


def test_reconnect_asks_the_link_to_sign_in_again_without_touching_credentials(tmp_path: Path):
    class FakeLink:
        def __init__(self):
            self.state, self.calls = {"state": "idle", "username": None, "error": None}, 0

        def start_connect(self):
            self.calls += 1
            self.state = {"state": "connecting", "username": None, "error": None}
            return dict(self.state)

    link = FakeLink()
    app, _, settings = make(tmp_path, link=link)
    c = AppClient(app)
    assert c.post("/api/setup/soulseek/connect").status_code == 409     # nothing saved yet
    assert link.calls == 0
    settings.slskd_api_key = "k"
    assert c.post("/api/setup/soulseek/connect").json()["state"] == "connecting"
    assert link.calls == 1


def test_reconnect_without_a_link_says_so_rather_than_pretending(tmp_path: Path):
    app, _, settings = make(tmp_path)
    settings.slskd_api_key = "k"
    r = AppClient(app).post("/api/setup/soulseek/connect")
    assert r.status_code == 409 and "Restart flackey" in r.json()["detail"]


def test_telegram_keys_route_saves_and_reconfigures(tmp_path: Path):
    from flackey.telegram import TelegramLogin

    class Client:
        async def connect(self): pass
        async def disconnect(self): pass
        def is_connected(self): return True
        async def is_user_authorized(self): return False

    login = TelegramLogin(Client(), False, make_client=Client)
    app, _, settings = make(tmp_path, login=login)
    settings.telegram_api_id = None; settings.telegram_api_hash = None
    c = AppClient(app)
    assert c.post("/api/telegram/keys", json={"api_id": "abc", "api_hash": "x"}).status_code == 400
    assert c.post("/api/telegram/keys", json={"api_id": 12, "api_hash": ""}).status_code == 400
    # A real hash is exactly 32 hex characters, so a half-copied one is refused here rather than at
    # the first call to Telegram, where the owner would have no idea which field was wrong.
    assert c.post("/api/telegram/keys", json={"api_id": "4242", "api_hash": "b" * 16}).status_code == 400
    assert c.post("/api/telegram/keys", json={"api_id": "4242", "api_hash": "b" * 33}).status_code == 400
    r = c.post("/api/telegram/keys", json={"api_id": "4242", "api_hash": "b" * 32})
    assert r.status_code == 200 and r.json() == {"configured": True}
    assert login.configured is True
    saved = json.loads(settings.settings_path.read_text())
    assert saved["telegram_api_id"] == 4242 and saved["telegram_api_hash"] == "b" * 32
    assert "b" * 32 not in c.get("/api/settings").text
    assert c.get("/api/telegram/status").json()["configured"] is True


def test_telegram_skip_route_turns_the_source_off(tmp_path: Path):
    status = Status(None, telegram_authorized=True, worker_running=False, setup_done=False,
                    source_enabled=True)
    app, _, settings = make(tmp_path, status=status)
    c = AppClient(app)
    r = c.post("/api/telegram/skip")
    assert r.status_code == 200 and r.json() == {"source_enabled": False}
    assert settings.source_enabled is False
    assert json.loads(settings.settings_path.read_text())["source_enabled"] is False
    # In `status` too, so the event that follows tells the page without a refresh -- and so health,
    # which now reads the flag from there, agrees with the setting.
    assert status["source_enabled"] is False
    assert c.get("/api/health").json()["source_enabled"] is False


def test_telegram_source_can_be_enabled_without_signing_in_again(tmp_path: Path):
    status = Status(None, telegram_authorized=True, worker_running=True, setup_done=True,
                    source_enabled=False)
    login = FakeLogin()
    login.authorized = True
    app, _, settings = make(tmp_path, status=status, login=login)
    c = AppClient(app)
    r = c.post("/api/telegram/source", json={"enabled": True})
    assert r.status_code == 200 and r.json() == {"source_enabled": True}
    assert settings.source_enabled is True and status["source_enabled"] is True
    assert json.loads(settings.settings_path.read_text())["source_enabled"] is True
    assert c.get("/api/health").json()["source_enabled"] is True
    assert c.post("/api/telegram/source", json={"enabled": False}).json() == {"source_enabled": False}


def test_telegram_source_cannot_be_enabled_without_an_authorized_session(tmp_path: Path):
    status = Status(None, telegram_authorized=False, worker_running=False, setup_done=True,
                    source_enabled=False)
    app, _, settings = make(tmp_path, status=status, login=FakeLogin())
    settings.source_enabled = False
    c = AppClient(app)
    r = c.post("/api/telegram/source", json={"enabled": True})
    assert r.status_code == 409 and "Sign in to Telegram" in r.json()["detail"]
    assert settings.source_enabled is False


def test_health_takes_the_source_flag_from_the_shared_status(tmp_path: Path):
    """A Telegram sign-in flips `source_enabled` in `status` (app.make_on_authorized); health has to
    read it from there, or the sidebar goes on saying "Telegram off" until the next restart."""
    status = Status(None, telegram_authorized=False, worker_running=False, setup_done=True,
                    source_enabled=False)
    app, _, _ = make(tmp_path, status=status)
    c = AppClient(app)
    assert c.get("/api/health").json()["source_enabled"] is False
    status.update(telegram_authorized=True, source_enabled=True)
    assert c.get("/api/health").json()["source_enabled"] is True


def test_health_says_when_the_source_is_off(tmp_path: Path):
    """The setup screen's "skip" turns the bot source off; the UI reads that back from health."""
    app, _, settings = make(tmp_path)
    settings.source_enabled = False
    assert AppClient(app).get("/api/health").json()["source_enabled"] is False


def test_changing_the_library_folder_moves_the_soulseek_share(tmp_path: Path):
    import yaml

    from flackey.slskd_config import config_path, write_credentials
    app, _, settings = make(tmp_path)
    write_credentials(settings.data_dir, "digger", "not-a-real-password",
                      library_root=settings.library_root)
    c = AppClient(app)
    new = tmp_path / "moved"
    assert c.put("/api/settings", json={"library_root": str(new)}).status_code == 200
    data = yaml.safe_load(config_path(settings.data_dir).read_text())
    assert data["shares"]["directories"] == [str(new)]


def test_soulseek_setup_shares_the_library(tmp_path: Path):
    import yaml

    from flackey.slskd_config import config_path
    app, _, settings = make(tmp_path)
    c = AppClient(app)
    r = c.post("/api/setup/soulseek",
               json={"username": "digger", "password": "not-a-real-password"})
    assert r.status_code == 200
    data = yaml.safe_load(config_path(settings.data_dir).read_text())
    assert data["shares"]["directories"] == [str(settings.library_root)]


def test_a_library_move_asks_a_wired_in_link_to_rescan_the_share(tmp_path: Path):
    """Moving the folder rewrites slskd.yml, but the running sidecar is still indexing the old one until
    something tells it otherwise -- so a link that is wired in gets asked to rescan. Only on a real
    move: re-saving the same folder must not set a rescan going."""
    import threading

    import yaml

    from flackey.slskd_config import config_path, write_credentials

    class FakeLink:
        def __init__(self):
            self.state = {"state": "idle", "username": None, "error": None}
            self.calls, self.rescanned = 0, threading.Event()

        async def rescan_shares(self):
            self.calls += 1
            self.rescanned.set()

    link = FakeLink()
    app, _, settings = make(tmp_path, link=link)
    write_credentials(settings.data_dir, "digger", "not-a-real-password",
                      library_root=settings.library_root)
    new = tmp_path / "moved"
    with AppClient(app) as c:
        assert c.put("/api/settings", json={"library_root": str(new)}).status_code == 200
        assert link.rescanned.wait(2), "the rescan task was scheduled but never ran"
        assert link.calls == 1
        data = yaml.safe_load(config_path(settings.data_dir).read_text())
        assert data["shares"]["directories"] == [str(new)]

        link.rescanned.clear()
        assert c.put("/api/settings", json={"library_root": str(new)}).status_code == 200
        assert not link.rescanned.wait(0.2)      # same folder, nothing moved, nothing to rescan
        assert link.calls == 1


def test_sharing_routes_without_a_service(client):
    c, _, _ = client
    assert c.get("/api/sharing").json()["enabled"] is False
    assert c.post("/api/sharing/check").status_code == 409


def test_sharing_routes_with_a_service(tmp_path: Path):
    class FakeSharing:
        can_check = True

        def __init__(self):
            self.state = {"port": 50300, "enabled": True, "checking": False, "mapping": None,
                          "reachable": False, "public_ip": "1.2.3.4", "lan_ip": "10.0.0.5",
                          "gateway": "10.0.0.1", "checked_at": None, "error": None}
            self.started = 0

        def start_refresh(self):
            self.started += 1
            return {**self.state, "checking": True}

    sharing = FakeSharing()
    app, _, _ = make(tmp_path, sharing=sharing)
    c = AppClient(app)
    assert c.get("/api/sharing").json()["reachable"] is False
    assert c.post("/api/sharing/check").json()["checking"] is True and sharing.started == 1


def _sharing_service(soulseek_enabled: bool):
    """A real Sharing with the network parts faked out, so the route tests below gate on the same
    `can_check` the app does."""
    from flackey.portcheck import PortCheck
    from flackey.sharing import Sharing

    class Setting:
        pass

    async def mapper(port, lease_s, http, **k):
        return None

    async def checker(port, http):
        return PortCheck(True, "1.2.3.4")

    setting = Setting()
    setting.soulseek_enabled = soulseek_enabled
    return setting, Sharing(setting, {}, http=None, port=50300, mapper=mapper, checker=checker,
                            gateway=lambda: "10.0.0.1", lan=lambda g=None: "10.0.0.5")


def test_sharing_check_is_refused_while_soulseek_is_off(tmp_path: Path):
    _, sharing = _sharing_service(soulseek_enabled=False)
    app, _, _ = make(tmp_path, sharing=sharing)
    assert AppClient(app).post("/api/sharing/check").status_code == 409


def test_sharing_check_follows_the_setting_not_the_last_refresh(tmp_path: Path):
    """Setting Soulseek up puts the api key in memory at once, but `state["enabled"]` only turns true
    inside a refresh -- the next of which can be half an hour away. Gating the button on the state made
    "Check again" answer a 409 for all that time."""
    setting, sharing = _sharing_service(soulseek_enabled=False)
    setting.soulseek_enabled = True          # what POST /api/setup/soulseek does, before anything connects
    assert sharing.state["enabled"] is False
    app, _, _ = make(tmp_path, sharing=sharing)
    assert AppClient(app).post("/api/sharing/check").status_code == 200


def test_health_reports_the_sharing_state(tmp_path: Path):
    status = Status(None, telegram_authorized=True, worker_running=False, setup_done=False)
    status["sharing"] = {"port": 50300, "enabled": True}
    app, _, _ = make(tmp_path, status=status)
    assert AppClient(app).get("/api/health").json()["sharing"] == {"port": 50300, "enabled": True}
