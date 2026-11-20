"""The helper reopens the installed app when it cannot swap the update in (audit SHL-3).

The owner pressed Restart; the app quit; the swap was refused. Every refusal leaves the installed bundle
as it was, so it is reopened rather than leaving the owner with no Flackey at all. Never while the old
app is still running, and never when the app quit without asking to come back.

Compiled with `FLACKEY_OPEN_PATH` pointing at a stub that records its arguments, so no test ever runs
the real `open` -- the stand-in bundles declare Flackey's bundle id, and opening one would register it
with LaunchServices. macOS only.
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest

from .test_update_helper import (
    PARENT_ALIVE,
    REFUSED,
    SOURCE,
    STAGING,
    TARGET,
    make_bundle,
    marker_of,
    run,
)

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="the helper is macOS-only")


@pytest.fixture
def tree(tmp_path):
    """The same stand-in for /Applications as test_update_helper's: installed bundle plus a staged one."""
    applications = tmp_path / "Applications"
    applications.mkdir()
    make_bundle(applications / TARGET, marker="old")
    make_bundle(applications / STAGING, marker="new")
    return applications


@pytest.fixture(scope="session")
def stubbed(tmp_path_factory) -> tuple[Path, Path]:
    """(helper, record): a helper whose relaunch runs a stub that appends its arguments to `record`."""
    where = tmp_path_factory.mktemp("stubbed-helper")
    record = where / "opened.txt"
    stub = where / "open-stub"
    stub.write_text(f'#!/bin/sh\nprintf "%s\\n" "$@" >> "{record}"\n')
    stub.chmod(0o755)
    out = where / "flackey-update-helper"
    subprocess.run(["clang", "-O2", "-Wall", "-Wextra", "-Werror", f'-DFLACKEY_OPEN_PATH="{stub}"',
                    "-o", str(out), str(SOURCE)], check=True, capture_output=True)
    # A source that stopped honouring the define would quietly run the real `open` on a test bundle.
    assert str(stub).encode() in out.read_bytes(), "the helper was not built with the stub opener"
    return out, record


@pytest.fixture
def opened(stubbed):
    helper, record = stubbed
    record.unlink(missing_ok=True)

    def read(wait_s: float = 5.0) -> list[str]:
        """What the stub was asked to open. The spawn is not waited on, so give it a moment."""
        deadline = time.monotonic() + wait_s
        while time.monotonic() < deadline:
            if record.exists():
                return record.read_text().split()
            time.sleep(0.05)
        return []

    return helper, read


def test_a_refused_update_reopens_the_installed_app(opened, tree):
    helper, read = opened
    subprocess.run(["rm", "-rf", str(tree / STAGING)], check=True)
    make_bundle(tree / STAGING, bundle_id="com.example.something-else")

    result = run(helper, tree, relaunch="1")

    assert result.returncode == REFUSED
    assert marker_of(tree / TARGET) == "old"
    assert read() == [str(tree / TARGET)]


def test_a_staging_name_refused_before_anything_is_opened_still_reopens_the_app(opened, tree):
    helper, read = opened
    assert run(helper, tree, staging="not-a-staging-name", relaunch="1").returncode == REFUSED
    assert read() == [str(tree / TARGET)]


def test_a_successful_swap_opens_the_new_version(opened, tree):
    helper, read = opened
    assert run(helper, tree, relaunch="1").returncode == 0
    assert marker_of(tree / TARGET) == "new"
    assert read() == [str(tree / TARGET)]


def test_nothing_is_reopened_when_the_app_did_not_ask_to_come_back(opened, tree):
    helper, read = opened
    subprocess.run(["rm", "-rf", str(tree / STAGING)], check=True)
    assert run(helper, tree, relaunch="0").returncode == REFUSED
    assert read(wait_s=0.5) == []


def test_nothing_is_reopened_while_the_old_app_is_still_running(opened, tree):
    """Two Flackeys on one database would be worse than no update."""
    helper, read = opened
    alive = subprocess.Popen(["/bin/sh", "-c", "sleep 120"])
    try:
        result = run(helper, tree, pid=alive.pid, relaunch="1")
    finally:
        alive.kill()
        alive.wait()

    assert result.returncode == PARENT_ALIVE
    assert read(wait_s=0.5) == []
