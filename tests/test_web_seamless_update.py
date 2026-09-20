"""The seamless path through `POST /api/update/install`, with the network faked.

"Refused" means: nothing unpacked, nothing staged, no fallback to the installer, and a readable error.
"""
from __future__ import annotations

import plistlib
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
import respx
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi.testclient import TestClient

from flackey.config import Settings
from flackey.events import Status
from flackey.inbox import Inbox
from flackey.notify import MemoryNotifier
from flackey.selfupdate import install as selfupdate
from flackey.selfupdate import signature
from flackey.store import Store
from flackey.web import create_app
from flackey.web.update import RELEASES_URL
from flackey.worker import Worker

from .test_web import DummyCatalog, DummySource, _settle, fake_youtube

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="staging shells out to ditto and xattr")

FROM_APP = {"x-flackey-app": "1"}
VERSION = "9.9.9"
ARCHIVE_URL = "https://example.test/Flackey-9.9.9.zip"
SIGNATURE_URL = "https://example.test/Flackey-9.9.9.zip.sig"
INSTALLER_URL = "https://example.test/Flackey.pkg"


class EndlessStream(httpx.AsyncByteStream):
    """Never ends, and counts what was pulled. Stops well past where a correct reader gives up."""

    LIMIT = 10_000

    def __init__(self) -> None:
        self.pulled = 0

    async def __aiter__(self):
        while self.pulled < self.LIMIT:
            self.pulled += 1
            yield b"a" * 1024
        raise AssertionError("the signature read was never bounded")


@pytest.fixture(autouse=True)
def _disarm():
    """`selfupdate.pending` is process-wide. Cleared both ways, so test order cannot matter."""
    selfupdate.pending.clear()
    yield
    selfupdate.pending.clear()


@pytest.fixture
def private_key():
    return Ed25519PrivateKey.generate()


@pytest.fixture
def archive_bytes(tmp_path) -> bytes:
    """A real ditto archive: staging shells out to ditto and will not take a few fake bytes."""
    app = tmp_path / "src" / "Flackey.app"
    (app / "Contents" / "MacOS").mkdir(parents=True)
    (app / "Contents" / "Info.plist").write_bytes(plistlib.dumps({"CFBundleIdentifier": "com.flackey.app"}))
    exe = app / "Contents" / "MacOS" / "Flackey"
    exe.write_text("#!/bin/sh\nexit 0\n")
    exe.chmod(0o755)
    out = tmp_path / "built.zip"
    subprocess.run(["/usr/bin/ditto", "-c", "-k", "--keepParent", str(app), str(out)], check=True)
    return out.read_bytes()


class Quit(list):
    """The window's close as the router receives it: a list of the presses that reached it, plus a
    switch that makes closing fail."""

    fail = False

    def __call__(self) -> None:
        if self.fail:
            raise RuntimeError("the window would not close")
        self.append(1)


@pytest.fixture
def applications(tmp_path) -> Path:
    d = tmp_path / "Applications"
    d.mkdir()
    return d


@pytest.fixture
def app(tmp_path, applications, private_key, monkeypatch):
    """A client whose build looks, to the update code, like a packaged copy in /Applications with the
    test's public key baked into it."""
    monkeypatch.setattr(signature, "baked_public_key",
                        lambda: signature.encode_public_key(private_key.public_key()))
    monkeypatch.setattr(selfupdate, "seamless_available", lambda *a, **k: True)
    # Staging is the real thing; only where it stages is redirected.
    real_stage = selfupdate.stage
    monkeypatch.setattr(selfupdate, "stage",
                        lambda archive, version, **kw: real_stage(archive, version,
                                                                  applications=applications, **kw))
    settings = Settings(_env_file=None, telegram_api_id=1, telegram_api_hash="h",
                        library_root=tmp_path / "lib", data_dir=tmp_path / "data")
    store = Store(settings.db_path)
    worker = Worker(store, DummySource(), DummyCatalog(), MemoryNotifier(), settings)
    status = Status(None, telegram_authorized=True, worker_running=False, setup_done=True)
    quits = Quit()
    api = create_app(store, worker, Inbox(store, youtube=fake_youtube), settings, status=status,
                     quit_app=quits)
    return TestClient(api), settings, status, quits


def feed(*, archive: int | None = 1024, sig: bool = True, installer: int | None = 8) -> list[dict]:
    assets = []
    if installer is not None:
        assets.append({"name": "Flackey.pkg", "size": installer, "browser_download_url": INSTALLER_URL})
    if archive is not None:
        assets.append({"name": "Flackey-9.9.9.zip", "size": archive, "browser_download_url": ARCHIVE_URL})
    if sig:
        assets.append({"name": "Flackey-9.9.9.zip.sig", "size": 129,
                       "browser_download_url": SIGNATURE_URL})
    return [{"draft": False, "prerelease": False, "tag_name": "v9.9.9",
             "published_at": "2026-09-15T10:00:00Z", "html_url": "https://example.test/releases/v9.9.9",
             "assets": assets}]


def sign(private: Ed25519PrivateKey, payload: bytes, version: str = VERSION) -> bytes:
    """What the release publishes: the signature binds the version as well as the bytes."""
    return private.sign(signature.signing_message(version, payload))


def serve(payload: bytes, sig: bytes | None, *, archive_size: int | None = None) -> None:
    respx.get(RELEASES_URL).mock(return_value=httpx.Response(
        200, json=feed(archive=archive_size if archive_size is not None else len(payload), sig=sig is not None)))
    respx.get(ARCHIVE_URL).mock(return_value=httpx.Response(200, content=payload))
    if sig is not None:
        respx.get(SIGNATURE_URL).mock(return_value=httpx.Response(200, content=sig.hex().encode() + b"\n"))


def press(c: TestClient, want: str = "staged") -> dict:
    """Press Update and wait for the state it should land in: the download runs as a task, so the
    answer arrives after the POST that started it."""
    c.post("/api/update/install", headers=FROM_APP)
    return _settle(c, want)


@respx.mock
def test_a_signed_release_is_downloaded_verified_and_staged(app, archive_bytes, private_key, applications):
    c, _, _, _ = app
    serve(archive_bytes, sign(private_key, archive_bytes))

    state = press(c)

    assert state["state"] == "staged" and state["seamless"] is True and state["version"] == "9.9.9"
    staged = Path(state["path"])
    assert staged.parent == applications and staged.name.startswith(selfupdate.STAGING_PREFIX)
    assert (staged / "Contents" / "MacOS" / "Flackey").is_file()
    # Staged, and stopped. Nothing is swapped and the app has not quit -- an app that vanished
    # mid-sentence because a download finished would be worse than the installer it replaced.
    assert selfupdate.pending.staged is None


@respx.mock
def test_a_tampered_payload_is_refused_and_nothing_is_staged(app, archive_bytes, private_key, applications):
    c, _, _, _ = app
    sig = sign(private_key, archive_bytes)
    serve(archive_bytes[:-1] + bytes([archive_bytes[-1] ^ 0xFF]), sig, archive_size=len(archive_bytes))

    state = press(c, "error")

    assert state["state"] == "error"
    assert "could not be verified" in state["error"]
    assert list(applications.iterdir()) == []


@respx.mock
def test_a_signature_from_another_key_is_refused(app, archive_bytes, applications):
    c, _, _, _ = app
    serve(archive_bytes, sign(Ed25519PrivateKey.generate(), archive_bytes))

    assert press(c, "error")["state"] == "error"
    assert list(applications.iterdir()) == []


@respx.mock
def test_an_older_archive_republished_under_a_newer_tag_is_refused(app, archive_bytes, private_key,
                                                                   applications):
    """Release-write access plus an old signed pair is not enough: the tag is part of what was signed."""
    c, _, _, _ = app
    serve(archive_bytes, sign(private_key, archive_bytes, version="9.9.8"))

    state = press(c, "error")

    assert state["state"] == "error" and "could not be verified" in state["error"]
    assert list(applications.iterdir()) == []
    assert selfupdate.pending.staged is None


@respx.mock
def test_a_release_with_no_signature_asset_never_reaches_the_seamless_path(app, archive_bytes,
                                                                        applications, monkeypatch):
    c, _, _, _ = app
    opened: list = []
    monkeypatch.setattr("flackey.web.update.open_installer", opened.append)
    respx.get(RELEASES_URL).mock(return_value=httpx.Response(200, json=feed(sig=False)))
    respx.get(INSTALLER_URL).mock(return_value=httpx.Response(200, content=b"12345678"))

    state = press(c, "ready")

    assert state["seamless"] is False
    assert [p.name for p in opened] == ["Flackey.pkg"]
    assert list(applications.iterdir()) == []


@respx.mock
def test_a_signature_asset_that_cannot_be_fetched_is_refused(app, archive_bytes, private_key, applications):
    c, _, _, _ = app
    serve(archive_bytes, sign(private_key, archive_bytes))
    respx.get(SIGNATURE_URL).mock(return_value=httpx.Response(404))

    assert press(c, "error")["state"] == "error"
    assert list(applications.iterdir()) == []


@respx.mock
def test_a_signature_asset_that_is_not_a_signature_is_refused(app, archive_bytes, private_key, applications):
    c, _, _, _ = app
    serve(archive_bytes, sign(private_key, archive_bytes))
    respx.get(SIGNATURE_URL).mock(return_value=httpx.Response(200, content=b"<html>404 not found</html>"))

    assert press(c, "error")["state"] == "error"
    assert list(applications.iterdir()) == []


@respx.mock
def test_an_endless_signature_asset_is_abandoned_rather_than_read(app, archive_bytes, private_key,
                                                                  applications):
    """The counter is the assertion: this fails if the bound moves back to the finished body."""
    c, _, _, _ = app
    serve(archive_bytes, sign(private_key, archive_bytes))
    stream = EndlessStream()
    respx.get(SIGNATURE_URL).mock(return_value=httpx.Response(200, stream=stream))

    assert press(c, "error")["state"] == "error"
    # 4096-byte ceiling, 1KB chunks: it should give up after a handful, not after ten thousand.
    assert stream.pulled < 20
    assert list(applications.iterdir()) == []


@respx.mock
def test_a_truncated_download_is_reported_as_a_download_problem(app, archive_bytes, private_key,
                                                                applications):
    c, _, _, _ = app
    serve(archive_bytes[:100], sign(private_key, archive_bytes), archive_size=len(archive_bytes))

    state = press(c, "error")

    assert state["state"] == "error"
    assert "connection" in state["error"]
    assert list(applications.iterdir()) == []


@respx.mock
def test_a_verification_failure_never_falls_back_to_the_installer(app, archive_bytes, applications,
                                                                  monkeypatch):
    c, _, _, _ = app
    opened: list = []
    monkeypatch.setattr("flackey.web.update.open_installer", opened.append)
    serve(archive_bytes, sign(Ed25519PrivateKey.generate(), archive_bytes))

    assert press(c, "error")["state"] == "error"
    assert opened == []


@respx.mock
def test_restart_arms_the_helper_and_closes_the_window(app, archive_bytes, private_key):
    c, _, _, quits = app
    serve(archive_bytes, sign(private_key, archive_bytes))
    press(c)

    body = c.post("/api/update/restart", headers=FROM_APP).json()

    assert body["state"] == "installing"
    assert quits == [1]
    armed = selfupdate.pending.staged
    assert armed is not None and armed.relaunch is True
    selfupdate.pending.clear()


@respx.mock
def test_a_window_that_will_not_close_leaves_the_update_armed_and_says_so(app, archive_bytes,
                                                                          private_key):
    c, _, _, quits = app
    quits.fail = True
    serve(archive_bytes, sign(private_key, archive_bytes))
    press(c)

    body = c.post("/api/update/restart", headers=FROM_APP).json()

    assert body["state"] == "staged"
    assert "Quit Flackey" in body["error"]
    assert selfupdate.pending.staged is not None
    selfupdate.pending.clear()


@respx.mock
def test_install_on_quit_arms_the_helper_without_reopening_the_app(app, archive_bytes, private_key):
    c, _, _, quits = app
    serve(archive_bytes, sign(private_key, archive_bytes))
    press(c)

    body = c.post("/api/update/later", headers=FROM_APP).json()

    assert body["state"] == "staged" and body["deferred"] is True
    assert quits == []
    armed = selfupdate.pending.staged
    assert armed is not None and armed.relaunch is False
    selfupdate.pending.clear()


@respx.mock
def test_pressing_update_while_the_app_is_closing_does_not_start_a_fresh_download(app, archive_bytes,
                                                                                  private_key,
                                                                                  applications):
    c, _, _, _ = app
    serve(archive_bytes, sign(private_key, archive_bytes))
    press(c)
    c.post("/api/update/restart", headers=FROM_APP)

    body = c.post("/api/update/install", headers=FROM_APP).json()

    assert body["state"] == "installing"
    assert len(list(applications.iterdir())) == 1


def test_restart_refuses_when_nothing_is_staged(app):
    c, _, _, quits = app

    assert c.post("/api/update/restart", headers=FROM_APP).status_code == 409
    assert c.post("/api/update/later", headers=FROM_APP).status_code == 409
    assert quits == []
    assert selfupdate.pending.staged is None


def test_a_page_that_is_not_flackeys_cannot_trigger_a_restart(app):
    c, _, _, quits = app

    assert c.post("/api/update/restart").status_code == 403
    assert c.post("/api/update/later").status_code == 403
    assert quits == []


@respx.mock
def test_the_restart_prompt_says_how_many_transfers_are_in_flight(app, archive_bytes, private_key):
    c, _, status, _ = app
    status["fetch_progress"] = [{"id": 1}, {"id": 2}, {"id": 3}]
    serve(archive_bytes, sign(private_key, archive_bytes))

    assert press(c)["busy"] == 3


@respx.mock
def test_pressing_update_again_while_one_is_staged_does_not_download_it_twice(app, archive_bytes,
                                                                             private_key, applications):
    c, _, _, _ = app
    serve(archive_bytes, sign(private_key, archive_bytes))
    first = press(c)

    assert press(c)["path"] == first["path"]
    assert len(list(applications.iterdir())) == 1


@respx.mock
def test_a_build_with_no_key_takes_the_installer_path(app, archive_bytes, monkeypatch, applications):
    c, _, _, _ = app
    monkeypatch.setattr(selfupdate, "seamless_available", lambda *a, **k: False)
    opened: list = []
    monkeypatch.setattr("flackey.web.update.open_installer", opened.append)
    respx.get(RELEASES_URL).mock(return_value=httpx.Response(200, json=feed()))
    respx.get(INSTALLER_URL).mock(return_value=httpx.Response(200, content=b"12345678"))

    state = press(c, "ready")

    assert state["state"] == "ready" and state["seamless"] is False
    assert opened and list(applications.iterdir()) == []
