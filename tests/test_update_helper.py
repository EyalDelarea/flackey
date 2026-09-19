"""The helper's refusals, against real directories.

This is the one program in the repository that does something irreversible to a path in /Applications,
and it does it detached, with nothing watching. Its input validation is the security boundary, so every
rule in the design doc's "Helper hardening" section gets a test that watches it refuse and confirms the
target was left exactly as it was.

Compiled from source per session rather than taken from a built bundle: the thing under test is
`packaging/update_helper.c`, and a test that quietly passed because it ran last month's binary would be
testing nothing. macOS only -- `renameatx_np` and `RENAME_SWAP` are Darwin, and the CI job that runs
this file is the mac one.
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
    """Run the helper against `applications`. The parent pid defaults to one that has already exited, so
    the wait at the top of the helper returns immediately instead of holding the test for a minute."""
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
    """Rule 2. Following a symlink here is how an attacker turns "replace this staged bundle" into
    "replace whatever I point at", and it is the shape Sparkle was patched for in 2.9.5."""
    elsewhere = make_bundle(tmp_path / "elsewhere.app", marker="planted")
    staged = tree / STAGING
    subprocess.run(["rm", "-rf", str(staged)], check=True)
    staged.symlink_to(elsewhere)

    assert run(helper, tree).returncode == REFUSED
    assert marker_of(tree / TARGET) == "old"
    assert elsewhere.is_dir() and marker_of(elsewhere) == "planted"


def test_a_symlink_where_the_installed_app_should_be_is_refused(helper, tree, tmp_path):
    """The same rule applied to the other entry. A symlink at the target would make the swap replace
    something outside the directory the helper was pointed at."""
    real = make_bundle(tmp_path / "real.app", marker="real")
    target = tree / TARGET
    subprocess.run(["rm", "-rf", str(target)], check=True)
    target.symlink_to(real)

    assert run(helper, tree).returncode == REFUSED
    assert marker_of(real) == "real"


def test_a_staging_name_the_app_would_never_produce_is_refused(helper, tree):
    """Rule 3. The name is the only thing tying the directory about to be swapped in to one this app
    created, and it comes from an argument -- which is exactly what an attacker controls."""
    for name in ("Flackey.app", ".Flackey-staging-.app", ".Flackey-staging-ab.app",
                 ".Flackey-staging-abc123", ".Flackey-staging-abc/123.app", "../evil.app",
                 ".Flackey-staging-abc-123.app"):
        assert run(helper, tree, staging=name).returncode in (REFUSED, 2), name
    assert marker_of(tree / TARGET) == "old"


def test_rule_4_lets_through_a_staged_bundle_we_own(helper, tree):
    """Rule 4, positive case only, and this test does not claim otherwise.

    Handing the staged directory to another uid needs root, so the refusal itself cannot be exercised by
    an unprivileged test suite and is not asserted here. What this does pin down is that the check does
    not refuse the ordinary case -- a rule that rejected everything would pass a suite of refusal tests
    perfectly and ship an app that can never update. The hostile half is one `if` in update_helper.c
    with no else branch, which is why it is written as one line and read rather than mocked."""
    assert (tree / STAGING).stat().st_uid == os.getuid()
    assert run(helper, tree).returncode == 0
    assert marker_of(tree / TARGET) == "new"


def test_a_bundle_that_is_not_flackey_is_refused(helper, tree):
    """Rule 5, identity half."""
    subprocess.run(["rm", "-rf", str(tree / STAGING)], check=True)
    make_bundle(tree / STAGING, bundle_id="com.example.something-else")

    assert run(helper, tree).returncode == REFUSED
    assert marker_of(tree / TARGET) == "old"


def test_a_staged_bundle_with_no_info_plist_is_refused(helper, tree):
    (tree / STAGING / "Contents" / "Info.plist").unlink()

    assert run(helper, tree).returncode == REFUSED
    assert marker_of(tree / TARGET) == "old"


def test_a_staged_bundle_with_no_executable_is_refused(helper, tree):
    """The invariant is that the installed path always points at a *working* bundle. A directory with
    no executable in it would satisfy every other rule and still leave an app that cannot open."""
    (tree / STAGING / "Contents" / "MacOS" / "Flackey").unlink()

    assert run(helper, tree).returncode == REFUSED
    assert marker_of(tree / TARGET) == "old"


def test_a_quarantined_staged_bundle_is_refused(helper, tree, tmp_path):
    """Rule 5, quarantine half. `ditto` propagates com.apple.quarantine onto everything it extracts, and
    a quarantined ad-hoc bundle is refused outright by Gatekeeper -- swapping it in would replace a
    working app with one that cannot be opened at all without a terminal."""
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
    """Nothing to swap with. Refusing rather than renaming the staged bundle into place keeps the one
    operation this program performs to a single atomic step -- the helper never creates the target, it
    only ever exchanges two things that both already exist."""
    subprocess.run(["rm", "-rf", str(tree / TARGET)], check=True)

    assert run(helper, tree).returncode == REFUSED
    assert (tree / STAGING).is_dir()


def test_a_file_where_a_bundle_should_be_is_refused(helper, tree):
    subprocess.run(["rm", "-rf", str(tree / STAGING)], check=True)
    (tree / STAGING).write_text("not a bundle")

    assert run(helper, tree).returncode == REFUSED
    assert marker_of(tree / TARGET) == "old"


def test_it_refuses_while_the_app_it_is_replacing_is_still_running(helper, tree):
    """Swapping under a live app would work -- the running process holds its inode and never notices --
    but the relaunch afterwards would put a second copy on screen, and two Flackeys sharing one database
    is worse than an update that did not happen."""
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
