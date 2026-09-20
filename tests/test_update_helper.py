"""The helper's refusals, against real directories.

Each rule gets a test that watches it refuse and confirms the target was untouched. Compiled from
source per session, so it cannot pass on last month's binary. macOS only.
"""
from __future__ import annotations

import os
import plistlib
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="the helper is macOS-only")

SOURCE = Path(__file__).resolve().parents[1] / "packaging" / "update_helper.c"
STAGING = ".Flackey-staging-abc123.app"
TARGET = "Flackey.app"

# Exit codes from update_helper.c. Named here so a test asserting "refused" cannot accidentally pass on
# "crashed", which is the failure mode that would make this whole file worthless.
REFUSED = 3
SWAP_BLOCKED = 4
PARENT_ALIVE = 6


@pytest.fixture(scope="session")
def helper(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("helper") / "flackey-update-helper"
    subprocess.run(["clang", "-O2", "-Wall", "-Wextra", "-Werror", "-o", str(out), str(SOURCE)],
                   check=True, capture_output=True)
    return out


def make_bundle(path: Path, *, bundle_id: str = "com.flackey.app", marker: str = "new") -> Path:
    """A directory shaped enough like an app bundle to satisfy the helper: an Info.plist that declares
    the identifier and an executable at Contents/MacOS/Flackey."""
    macos = path / "Contents" / "MacOS"
    macos.mkdir(parents=True)
    (path / "Contents" / "Info.plist").write_bytes(plistlib.dumps({
        "CFBundleIdentifier": bundle_id, "CFBundleName": "Flackey"}))
    exe = macos / "Flackey"
    exe.write_text(f"#!/bin/sh\necho {marker}\n")
    exe.chmod(0o755)
    (path / "marker").write_text(marker)
    return path


@pytest.fixture
def tree(tmp_path):
    """A stand-in for /Applications: the installed bundle, and a staged one waiting to replace it."""
    applications = tmp_path / "Applications"
    applications.mkdir()
    make_bundle(applications / TARGET, marker="old")
    make_bundle(applications / STAGING, marker="new")
    return applications


def run(helper: Path, applications: Path, staging: str = STAGING, *, pid: int | None = None,
        relaunch: str = "0", log: Path | None = None) -> subprocess.CompletedProcess:
    """The parent pid defaults to one already exited, so the helper's wait returns immediately."""
    if pid is None:
        done = subprocess.Popen(["/usr/bin/true"])
        done.wait()
        pid = done.pid
    argv = [str(helper), str(pid), str(applications), staging, relaunch]
    if log is not None:
        argv.append(str(log))
    return subprocess.run(argv, capture_output=True, text=True, timeout=120, check=False)


def marker_of(bundle: Path) -> str:
    return (bundle / "marker").read_text()


def test_the_swap_happens_and_the_old_bundle_ends_up_at_the_staging_path(helper, tree, tmp_path):
    log = tmp_path / "helper.log"
    result = run(helper, tree, log=log)

    assert result.returncode == 0, result.stderr
    # The point of RENAME_SWAP rather than two renames: both names exist throughout, and afterwards the
    # installed path holds the new bundle.
    assert marker_of(tree / TARGET) == "new"
    # ...and the old one, which the helper then deletes as tidying.
    assert not (tree / STAGING).exists()
    assert "swapped" in log.read_text()


def test_a_symlink_where_the_staged_bundle_should_be_is_refused(helper, tree, tmp_path):
    elsewhere = make_bundle(tmp_path / "elsewhere.app", marker="planted")
    staged = tree / STAGING
    subprocess.run(["rm", "-rf", str(staged)], check=True)
    staged.symlink_to(elsewhere)

    assert run(helper, tree).returncode == REFUSED
    assert marker_of(tree / TARGET) == "old"
    assert elsewhere.is_dir() and marker_of(elsewhere) == "planted"


def test_a_symlink_where_the_installed_app_should_be_is_refused(helper, tree, tmp_path):
    real = make_bundle(tmp_path / "real.app", marker="real")
    target = tree / TARGET
    subprocess.run(["rm", "-rf", str(target)], check=True)
    target.symlink_to(real)

    assert run(helper, tree).returncode == REFUSED
    assert marker_of(real) == "real"


def test_a_staging_name_the_app_would_never_produce_is_refused(helper, tree):
    for name in ("Flackey.app", ".Flackey-staging-.app", ".Flackey-staging-ab.app",
                 ".Flackey-staging-abc123", ".Flackey-staging-abc/123.app", "../evil.app",
                 ".Flackey-staging-abc-123.app"):
        assert run(helper, tree, staging=name).returncode in (REFUSED, 2), name
    assert marker_of(tree / TARGET) == "old"


def test_a_staged_bundle_we_own_is_swapped_in(helper, tree):
    """Positive case only -- the refusal needs root to set up. A uid check that rejected everything
    would pass every refusal test here and ship an app that can never update."""
    assert (tree / STAGING).stat().st_uid == os.getuid()
    assert run(helper, tree).returncode == 0
    assert marker_of(tree / TARGET) == "new"


def test_a_bundle_that_is_not_flackey_is_refused(helper, tree):
    subprocess.run(["rm", "-rf", str(tree / STAGING)], check=True)
    make_bundle(tree / STAGING, bundle_id="com.example.something-else")

    assert run(helper, tree).returncode == REFUSED
    assert marker_of(tree / TARGET) == "old"


def test_a_staged_bundle_with_no_info_plist_is_refused(helper, tree):
    (tree / STAGING / "Contents" / "Info.plist").unlink()

    assert run(helper, tree).returncode == REFUSED
    assert marker_of(tree / TARGET) == "old"


def test_a_staged_bundle_with_no_executable_is_refused(helper, tree):
    (tree / STAGING / "Contents" / "MacOS" / "Flackey").unlink()

    assert run(helper, tree).returncode == REFUSED
    assert marker_of(tree / TARGET) == "old"


def test_a_quarantined_staged_bundle_is_refused(helper, tree, tmp_path):
    subprocess.run(["/usr/bin/xattr", "-w", "com.apple.quarantine",
                    "0081;00000000;Safari;", str(tree / STAGING)], check=True)
    log = tmp_path / "helper.log"

    assert run(helper, tree, log=log).returncode == REFUSED
    assert marker_of(tree / TARGET) == "old"
    assert "quarantine" in log.read_text()


def test_a_missing_staged_bundle_is_refused(helper, tree):
    subprocess.run(["rm", "-rf", str(tree / STAGING)], check=True)

    assert run(helper, tree).returncode == REFUSED
    assert marker_of(tree / TARGET) == "old"


def test_a_missing_installed_app_is_refused(helper, tree):
    subprocess.run(["rm", "-rf", str(tree / TARGET)], check=True)

    assert run(helper, tree).returncode == REFUSED
    assert (tree / STAGING).is_dir()


def test_a_file_where_a_bundle_should_be_is_refused(helper, tree):
    subprocess.run(["rm", "-rf", str(tree / STAGING)], check=True)
    (tree / STAGING).write_text("not a bundle")

    assert run(helper, tree).returncode == REFUSED
    assert marker_of(tree / TARGET) == "old"


def test_it_refuses_while_the_app_it_is_replacing_is_still_running(helper, tree):
    alive = subprocess.Popen(["/bin/sh", "-c", "sleep 120"])
    try:
        # The helper's own wait is 60s; the test's timeout is what stops this hanging if that changes.
        result = run(helper, tree, pid=alive.pid)
    finally:
        alive.kill()
        alive.wait()

    assert result.returncode == PARENT_ALIVE
    assert marker_of(tree / TARGET) == "old"


def test_a_directory_that_is_not_a_directory_is_refused(helper, tmp_path):
    plain = tmp_path / "not-a-dir"
    plain.write_text("x")

    assert run(helper, plain).returncode == REFUSED


def test_bad_arguments_are_a_usage_error_and_touch_nothing(helper, tree):
    assert subprocess.run([str(helper)], capture_output=True, check=False).returncode == 2
    assert run(helper, tree, pid=0).returncode == 2
    assert marker_of(tree / TARGET) == "old"
