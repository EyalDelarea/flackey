"""The seamless path through `POST /api/update/install`, end to end with the network faked.

The design doc's failure table is the specification, and the single line it all turns on is the one in
`download_archive` that refuses when `verify` says no. Most of what follows is that line, approached
from every direction a release can be wrong: tampered payload, tampered signature, missing signature,
signature that is not a signature.

What "refused" has to mean in every one of them: nothing unpacked, nothing staged, no fallback to the
installer, and an error the owner can read.
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
ARCHIVE_URL = "https://example.test/Flackey-9.9.9.zip"
SIGNATURE_URL = "https://example.test/Flackey-9.9.9.zip.sig"
INSTALLER_URL = "https://example.test/Flackey.pkg"


@pytest.fixture(autouse=True)
def _disarm():
    """`selfupdate.pending` is process-wide, because the thing it records is -- and a test that armed it
    and then failed before disarming would hand the next test an app that thinks it is about to replace
    itself. Cleared on the way in as well as out, so the order tests run in cannot matter."""
    selfupdate.pending.clear()
    yield
    selfupdate.pending.clear()


@pytest.fixture
def private_key():
    return Ed25519PrivateKey.generate()


@pytest.fixture
def archive_bytes(tmp_path) -> bytes:
    """A real ditto archive of a minimal bundle -- the staging step shells out to ditto and will not be
    fooled by a handful of bytes calling itself a zip."""
    app = tmp_path / "src" / "Flackey.app"
    (app / "Contents" / "MacOS").mkdir(parents=True)
    (app / "Contents" / "Info.plist").write_bytes(plistlib.dumps({"CFBundleIdentifier": "com.flackey.app"}))
    exe = app / "Contents" / "MacOS" / "Flackey"
    exe.write_text("#!/bin/sh\nexit 0\n")
    exe.chmod(0o755)
    out = tmp_path / "built.zip"
    subprocess.run(["/usr/bin/ditto", "-c", "-k", "--keepParent", str(app), str(out)], check=True)
    return out.read_bytes()


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
    quits: list[int] = []
    api = create_app(store, worker, Inbox(store, youtube=fake_youtube), settings, status=status,
                     quit_app=lambda: quits.append(1))
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


def serve(payload: bytes, sig: bytes | None, *, archive_size: int | None = None) -> None:
    respx.get(RELEASES_URL).mock(return_value=httpx.Response(
        200, json=feed(archive=archive_size if archive_size is not None else len(payload), sig=sig is not None)))
    respx.get(ARCHIVE_URL).mock(return_value=httpx.Response(200, content=payload))
    if sig is not None:
        respx.get(SIGNATURE_URL).mock(return_value=httpx.Response(200, content=sig.hex().encode() + b"\n"))


def press(c: TestClient, want: str = "staged") -> dict:
    """Press Update and wait for the state it should land in. The download runs as a task, so the answer
    arrives after the POST that started it, and every request gives the server's loop a turn."""
    c.post("/api/update/install", headers=FROM_APP)
    return _settle(c, want)


@respx.mock
def test_a_signed_release_is_downloaded_verified_and_staged(app, archive_bytes, private_key, applications):
    c, _, _, _ = app
    serve(archive_bytes, private_key.sign(archive_bytes))

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
    """The case the whole design exists for: the bytes were changed after they were signed."""
    c, _, _, _ = app
    sig = private_key.sign(archive_bytes)
    serve(archive_bytes[:-1] + bytes([archive_bytes[-1] ^ 0xFF]), sig, archive_size=len(archive_bytes))

    state = press(c, "error")

    assert state["state"] == "error"
    assert "could not be verified" in state["error"]
    assert list(applications.iterdir()) == []


@respx.mock
def test_a_signature_from_another_key_is_refused(app, archive_bytes, applications):
    c, _, _, _ = app
    serve(archive_bytes, Ed25519PrivateKey.generate().sign(archive_bytes))

    assert press(c, "error")["state"] == "error"
    assert list(applications.iterdir()) == []


@respx.mock
def test_a_release_with_no_signature_asset_never_reaches_the_seamless_path(app, archive_bytes, applications):
    """A missing signature is refused, never read as "no signature required". It is caught before the
    download even starts: with no .sig in the release there is no signed pair, so the press falls to the
    installer -- which is the pre-existing flow, not a silent seamless install."""
    c, _, _, _ = app
    respx.get(RELEASES_URL).mock(return_value=httpx.Response(200, json=feed(sig=False)))
    respx.get(INSTALLER_URL).mock(return_value=httpx.Response(200, content=b"12345678"))

    state = press(c, "ready")

    assert state["seamless"] is False
    assert list(applications.iterdir()) == []


@respx.mock
def test_a_signature_asset_that_cannot_be_fetched_is_refused(app, archive_bytes, private_key, applications):
    """Listed in the release, gone by the time it is asked for. A 404 here must refuse rather than
    proceed unsigned -- the design's rule is that missing is a failure."""
    c, _, _, _ = app
    serve(archive_bytes, private_key.sign(archive_bytes))
    respx.get(SIGNATURE_URL).mock(return_value=httpx.Response(404))

    assert press(c, "error")["state"] == "error"
    assert list(applications.iterdir()) == []


@respx.mock
def test_a_signature_asset_that_is_not_a_signature_is_refused(app, archive_bytes, private_key, applications):
    c, _, _, _ = app
    serve(archive_bytes, private_key.sign(archive_bytes))
    respx.get(SIGNATURE_URL).mock(return_value=httpx.Response(200, content=b"<html>404 not found</html>"))

    assert press(c, "error")["state"] == "error"
    assert list(applications.iterdir()) == []


@respx.mock
def test_an_oversized_signature_asset_is_refused(app, archive_bytes, private_key, applications):
    """Nothing declares the .sig's size the way the archive does, so the read is bounded here. 128
    characters of hex is a signature; a megabyte of anything is not."""
    c, _, _, _ = app
    serve(archive_bytes, private_key.sign(archive_bytes))
    respx.get(SIGNATURE_URL).mock(return_value=httpx.Response(200, content=b"a" * 100_000))

    assert press(c, "error")["state"] == "error"
    assert list(applications.iterdir()) == []


@respx.mock
def test_a_truncated_download_is_reported_as_a_download_problem(app, archive_bytes, private_key,
                                                                applications):
    """A dropped connection must not read to the owner as a tampered release. It is caught by the byte
    count before verification ever runs, so the sentence is about the connection."""
    c, _, _, _ = app
    serve(archive_bytes[:100], private_key.sign(archive_bytes), archive_size=len(archive_bytes))

    state = press(c, "error")

    assert state["state"] == "error"
    assert "connection" in state["error"]
    assert list(applications.iterdir()) == []


@respx.mock
def test_a_verification_failure_never_falls_back_to_the_installer(app, archive_bytes, applications,
                                                                  monkeypatch):
    """Decision D2. Whatever went wrong, fetching a second payload from the same release and running it
    through Installer.app is not the answer to it."""
    c, _, _, _ = app
    opened: list = []
    monkeypatch.setattr("flackey.web.update.open_installer", opened.append)
    serve(archive_bytes, Ed25519PrivateKey.generate().sign(archive_bytes))

    assert press(c, "error")["state"] == "error"
    assert opened == []


@respx.mock
def test_restart_arms_the_helper_and_closes_the_window(app, archive_bytes, private_key):
    c, _, _, quits = app
    serve(archive_bytes, private_key.sign(archive_bytes))
    press(c)

    body = c.post("/api/update/restart", headers=FROM_APP).json()

    assert body["state"] == "installing"
    assert quits == [1]
    armed = selfupdate.pending.staged
    assert armed is not None and armed.relaunch is True
    selfupdate.pending.clear()


@respx.mock
def test_install_on_quit_arms_the_helper_without_reopening_the_app(app, archive_bytes, private_key):
    """Somebody who chose "install on quit" asked for the app to go away. Reopening it for them a second
    later is not what they asked for, which is the one thing that differs between the two buttons."""
    c, _, _, quits = app
    serve(archive_bytes, private_key.sign(archive_bytes))
    press(c)

    body = c.post("/api/update/later", headers=FROM_APP).json()

    assert body["state"] == "staged" and body["deferred"] is True
    assert quits == []
    armed = selfupdate.pending.staged
    assert armed is not None and armed.relaunch is False
    selfupdate.pending.clear()


def test_restart_refuses_when_nothing_is_staged(app):
    """Out of order, so it does nothing at all rather than quitting the app for no reason."""
    c, _, _, quits = app

    assert c.post("/api/update/restart", headers=FROM_APP).status_code == 409
    assert c.post("/api/update/later", headers=FROM_APP).status_code == 409
    assert quits == []
    assert selfupdate.pending.staged is None


def test_a_page_that_is_not_flackeys_cannot_trigger_a_restart(app):
    """The same guard #56 put on the other two update routes. These are wanted for what they *do* -- an
    app that quits itself at a stranger's choosing is not an improvement on a downloaded installer."""
    c, _, _, quits = app

    assert c.post("/api/update/restart").status_code == 403
    assert c.post("/api/update/later").status_code == 403
    assert quits == []


@respx.mock
def test_the_restart_prompt_says_how_many_transfers_are_in_flight(app, archive_bytes, private_key):
    """D4: the dialog carries the busy warning. A restart during a Soulseek transfer loses it, and the
    honest thing is to say so and let the owner choose rather than to block the update or take the loss
    quietly."""
    c, _, status, _ = app
    status["fetch_progress"] = [{"id": 1}, {"id": 2}, {"id": 3}]
    serve(archive_bytes, private_key.sign(archive_bytes))

    assert press(c)["busy"] == 3


@respx.mock
def test_pressing_update_again_while_one_is_staged_does_not_download_it_twice(app, archive_bytes,
                                                                             private_key, applications):
    c, _, _, _ = app
    serve(archive_bytes, private_key.sign(archive_bytes))
    first = press(c)

    assert press(c)["path"] == first["path"]
    assert len(list(applications.iterdir())) == 1


@respx.mock
def test_a_build_with_no_key_takes_the_installer_path(app, archive_bytes, monkeypatch, applications):
    """The state this repository ships in until the owner generates the keypair. Not an error: nothing
    was claimed and nothing failed, so the press behaves exactly as it did before this feature existed."""
    c, _, _, _ = app
    monkeypatch.setattr(selfupdate, "seamless_available", lambda *a, **k: False)
    opened: list = []
    monkeypatch.setattr("flackey.web.update.open_installer", opened.append)
    respx.get(RELEASES_URL).mock(return_value=httpx.Response(200, json=feed()))
    respx.get(INSTALLER_URL).mock(return_value=httpx.Response(200, content=b"12345678"))

    state = press(c, "ready")

    assert state["state"] == "ready" and state["seamless"] is False
    assert opened and list(applications.iterdir()) == []
