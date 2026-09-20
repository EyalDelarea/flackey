"""Staging a verified update, and the handover to the helper.

Every test here checks one face of the same rule: nothing in `install` ever touches the installed
bundle, and on every failure path what it created is gone and what was already there is untouched.
"""
from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from flackey.selfupdate import install, signature

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="ditto and xattr are macOS-only")


@pytest.fixture
def bundle(tmp_path) -> Path:
    """A minimal Flackey.app, including a symlink -- the reason the archive is made with `ditto` and not
    `zip`, since a zip round-trip flattens symlinks and the real bundle is full of them."""
    app = tmp_path / "source" / "Flackey.app"
    (app / "Contents" / "MacOS").mkdir(parents=True)
    (app / "Contents" / "Info.plist").write_bytes(plistlib.dumps({"CFBundleIdentifier": "com.flackey.app"}))
    exe = app / "Contents" / "MacOS" / "Flackey"
    exe.write_text("#!/bin/sh\nexit 0\n")
    exe.chmod(0o755)
    (app / "Contents" / "Frameworks").mkdir()
    (app / "Contents" / "Frameworks" / "Current").symlink_to("../MacOS")
    return app


@pytest.fixture
def archive(tmp_path, bundle) -> Path:
    out = tmp_path / "Flackey-9.9.9.zip"
    subprocess.run(["/usr/bin/ditto", "-c", "-k", "--keepParent", str(bundle), str(out)], check=True)
    return out


@pytest.fixture
def applications(tmp_path) -> Path:
    d = tmp_path / "Applications"
    d.mkdir()
    return d


def test_staging_unpacks_the_bundle_under_a_name_the_helper_will_accept(archive, applications):
    staged = install.stage(archive, "9.9.9", applications=applications)

    assert staged.path.parent == applications
    assert staged.path.name.startswith(install.STAGING_PREFIX) and staged.path.suffix == ".app"
    assert staged.version == "9.9.9" and staged.relaunch is True
    assert (staged.path / "Contents" / "MacOS" / "Flackey").is_file()
    # The symlink survived, which is the whole reason for ditto.
    assert (staged.path / "Contents" / "Frameworks" / "Current").is_symlink()
    # Nothing left over from the unpack step.
    assert [p.name for p in applications.iterdir()] == [staged.path.name]


def test_the_staged_bundle_carries_no_quarantine_and_is_not_group_writable(archive, applications):
    subprocess.run(["/usr/bin/xattr", "-w", "com.apple.quarantine", "0081;0;Safari;", str(archive)],
                   check=True)

    staged = install.stage(archive, "9.9.9", applications=applications)

    for path in (staged.path, staged.path / "Contents" / "MacOS" / "Flackey"):
        listed = subprocess.run(["/usr/bin/xattr", str(path)], capture_output=True, text=True, check=False)
        assert "com.apple.quarantine" not in listed.stdout
        assert not path.stat().st_mode & 0o022


def test_a_second_update_supersedes_the_first_rather_than_piling_up(archive, applications):
    first = install.stage(archive, "9.9.9", applications=applications)
    second = install.stage(archive, "9.9.9", applications=applications)

    assert first.path != second.path
    assert not first.path.exists()
    assert [p.name for p in applications.iterdir()] == [second.path.name]


def test_a_failed_attempt_still_clears_what_the_last_one_left(tmp_path, applications, archive):
    stale = install.stage(archive, "9.9.9", applications=applications)
    junk = tmp_path / "Flackey-9.9.9.zip"
    junk.write_bytes(b"not a zip")

    with pytest.raises(install.StagingError):
        install.stage(junk, "9.9.9", applications=applications)

    assert not stale.path.exists()
    assert list(applications.iterdir()) == []


def test_the_sweep_leaves_alone_anything_it_did_not_create(archive, applications):
    other = applications / "SomeoneElse.app"
    other.mkdir()
    installed = applications / "Flackey.app"
    installed.mkdir()
    hidden = applications / ".hidden-but-not-ours"
    hidden.mkdir()

    install.stage(archive, "9.9.9", applications=applications)

    assert other.is_dir() and installed.is_dir() and hidden.is_dir()


def test_the_sweep_does_not_follow_a_symlink_out_of_the_folder(tmp_path, archive, applications):
    elsewhere = tmp_path / "precious"
    elsewhere.mkdir()
    (elsewhere / "file").write_text("keep me")
    (applications / f"{install.STAGING_PREFIX}planted.app").symlink_to(elsewhere)

    install.stage(archive, "9.9.9", applications=applications)

    assert (elsewhere / "file").read_text() == "keep me"


def test_an_archive_without_flackey_inside_it_is_refused(tmp_path, applications):
    other = tmp_path / "Something.app"
    other.mkdir()
    (other / "file").write_text("x")
    zipped = tmp_path / "other.zip"
    subprocess.run(["/usr/bin/ditto", "-c", "-k", "--keepParent", str(other), str(zipped)], check=True)

    with pytest.raises(install.StagingError):
        install.stage(zipped, "9.9.9", applications=applications)
    # And nothing of the attempt is left behind for the next one to trip over.
    assert list(applications.iterdir()) == []


def test_an_unreadable_archive_is_refused_and_leaves_nothing_behind(tmp_path, applications):
    with pytest.raises(install.StagingError):
        install.stage(tmp_path / "does-not-exist.zip", "9.9.9", applications=applications)
    assert list(applications.iterdir()) == []


def test_a_garbage_archive_is_refused_and_leaves_nothing_behind(tmp_path, applications):
    junk = tmp_path / "Flackey-9.9.9.zip"
    junk.write_bytes(b"this is not a zip file")

    with pytest.raises(install.StagingError):
        install.stage(junk, "9.9.9", applications=applications)
    assert list(applications.iterdir()) == []


def test_staging_never_touches_what_is_already_installed(archive, applications):
    installed = applications / "Flackey.app"
    (installed / "Contents").mkdir(parents=True)
    (installed / "Contents" / "marker").write_text("installed")

    install.stage(archive, "9.9.9", applications=applications)

    assert (installed / "Contents" / "marker").read_text() == "installed"


def test_discard_only_ever_removes_a_staging_path(applications):
    installed = applications / "Flackey.app"
    installed.mkdir()
    install.discard(install.StagedUpdate(path=installed, version="9.9.9"))
    assert installed.is_dir()

    staged = applications / f"{install.STAGING_PREFIX}abc123.app"
    staged.mkdir()
    install.discard(install.StagedUpdate(path=staged, version="9.9.9"))
    assert not staged.exists()


def test_an_installed_build_with_a_key_and_a_helper_is_offered_the_seamless_path(tmp_path, monkeypatch):
    """The only test here that asserts True. Every other one pins a reason to refuse, so a
    `seamless_available` broken into always answering False would leave them all green."""
    bundle = tmp_path / "Applications" / "Flackey.app"
    frameworks = bundle / "Contents" / "Frameworks"
    (frameworks / "bin").mkdir(parents=True)
    (bundle / "Contents" / "MacOS").mkdir(parents=True)
    exe = bundle / "Contents" / "MacOS" / "Flackey"
    exe.touch()
    helper = frameworks / "bin" / install.HELPER_NAME
    helper.touch()
    helper.chmod(0o755)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    monkeypatch.setattr(sys, "_MEIPASS", str(frameworks), raising=False)
    monkeypatch.setattr(install.signature, "PUBLIC_KEY_HEX",
                        signature.encode_public_key(
                            Ed25519PrivateKey.generate().public_key()).hex())

    assert install.running_bundle() == bundle
    assert install.helper_path() == helper
    assert install.seamless_available(applications=tmp_path / "Applications") is True


def test_a_checkout_is_never_offered_a_seamless_update():
    assert install.running_bundle() is None
    assert install.seamless_available() is False


def test_seamless_is_refused_when_the_app_is_not_the_one_in_applications(tmp_path, monkeypatch):
    elsewhere = tmp_path / "Downloads" / "Flackey.app"
    elsewhere.mkdir(parents=True)
    monkeypatch.setattr(install.signature, "seamless_updates_configured", lambda: True)
    monkeypatch.setattr(install, "helper_path", lambda: tmp_path / "helper")
    monkeypatch.setattr(install, "running_bundle", lambda: elsewhere)

    assert install.seamless_available(applications=tmp_path / "Applications") is False


def test_seamless_is_refused_when_the_build_carries_no_helper(tmp_path, monkeypatch):
    applications = tmp_path / "Applications"
    monkeypatch.setattr(install.signature, "seamless_updates_configured", lambda: True)
    monkeypatch.setattr(install, "running_bundle", lambda: applications / "Flackey.app")
    monkeypatch.setattr(install, "helper_path", lambda: None)

    assert install.seamless_available(applications=applications) is False


def test_seamless_is_refused_when_no_key_is_baked_in(tmp_path, monkeypatch):
    applications = tmp_path / "Applications"
    monkeypatch.setattr(install, "running_bundle", lambda: applications / "Flackey.app")
    monkeypatch.setattr(install, "helper_path", lambda: tmp_path / "helper")
    monkeypatch.setattr(install.signature, "seamless_updates_configured", lambda: False)

    assert install.seamless_available(applications=applications) is False


def test_the_helper_is_copied_out_of_the_bundle_before_it_is_run(tmp_path, monkeypatch):
    source = tmp_path / "bin" / "flackey-update-helper"
    source.parent.mkdir()
    source.write_text("#!/bin/sh\nexit 0\n")
    source.chmod(0o755)
    monkeypatch.setattr(install, "helper_path", lambda: source)
    spawned: list[list[str]] = []
    monkeypatch.setattr(install.subprocess, "Popen", lambda argv, **kw: spawned.append((argv, kw)))

    staged = install.StagedUpdate(path=tmp_path / "Applications" / f"{install.STAGING_PREFIX}abc123.app",
                                  version="9.9.9", relaunch=True)
    install.launch_helper(staged, log_path=tmp_path / "helper.log")

    argv, kwargs = spawned[0]
    runner = Path(argv[0])
    assert runner != source and runner.name == install.HELPER_NAME
    assert runner.is_file() and os.access(runner, os.X_OK)
    # 0700, and a name `mkdtemp` chose -- not a fixed path something could have been left at.
    assert runner.parent.stat().st_mode & 0o777 == 0o700
    assert argv[1:] == [str(os.getpid()), str(staged.path.parent), staged.path.name, "1",
                        str(tmp_path / "helper.log")]
    # Detached, or it would be killed along with the app it is waiting for.
    assert kwargs["start_new_session"] is True


def test_install_on_quit_tells_the_helper_not_to_reopen_the_app(tmp_path, monkeypatch):
    source = tmp_path / "flackey-update-helper"
    source.write_text("#!/bin/sh\nexit 0\n")
    source.chmod(0o755)
    monkeypatch.setattr(install, "helper_path", lambda: source)
    spawned: list = []
    monkeypatch.setattr(install.subprocess, "Popen", lambda argv, **kw: spawned.append(argv))

    install.launch_helper(install.StagedUpdate(
        path=tmp_path / f"{install.STAGING_PREFIX}abc123.app", version="9.9.9", relaunch=False))

    assert spawned[0][4] == "0"


def test_a_build_with_no_helper_cannot_start_an_update(tmp_path, monkeypatch):
    monkeypatch.setattr(install, "helper_path", lambda: None)

    with pytest.raises(install.StagingError):
        install.launch_helper(install.StagedUpdate(
            path=tmp_path / f"{install.STAGING_PREFIX}abc123.app", version="9.9.9"))


def test_nothing_happens_on_quit_unless_an_update_was_armed(tmp_path, monkeypatch):
    monkeypatch.setattr(install, "launch_helper",
                        lambda *a, **k: pytest.fail("spawned a helper with nothing armed"))
    pending = install.Pending()

    assert pending.run() is False


def test_an_armed_update_is_handed_over_once_and_only_once(tmp_path, monkeypatch):
    runs: list = []
    monkeypatch.setattr(install, "launch_helper", lambda staged, **kw: runs.append(staged))
    pending = install.Pending()
    staged = install.StagedUpdate(path=tmp_path / f"{install.STAGING_PREFIX}abc123.app", version="9.9.9")
    pending.arm(staged)

    assert pending.run() is True
    assert runs == [staged]
    # Disarmed by running it: a second stop of the same process must not spawn a second helper.
    assert pending.run() is False


def test_a_helper_that_will_not_start_does_not_take_the_quit_down_with_it(tmp_path, monkeypatch):
    def boom(staged, **kw):
        raise install.StagingError("no")

    monkeypatch.setattr(install, "launch_helper", boom)
    pending = install.Pending()
    pending.arm(install.StagedUpdate(path=tmp_path / f"{install.STAGING_PREFIX}abc123.app", version="9"))

    assert pending.run() is False


def test_an_unexpected_failure_on_the_way_out_is_also_swallowed(tmp_path, monkeypatch):
    def boom(staged, **kw):
        raise RuntimeError("something nobody predicted")

    monkeypatch.setattr(install, "launch_helper", boom)
    pending = install.Pending()
    pending.arm(install.StagedUpdate(path=tmp_path / f"{install.STAGING_PREFIX}abc123.app", version="9"))

    assert pending.run() is False
