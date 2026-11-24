"""The helper never leaves a bundle it did not check at Flackey.app (security review S11).

The helper hashes the staged bundle through a descriptor, then swaps by name. Something that renames
another folder onto the staging name in between would get that folder swapped in. So after the swap the
helper checks that what now sits at Flackey.app is the very directory it hashed, and swaps back if not.

The gap is a few instructions wide, so the test does not race it: it builds a copy of the helper with a
hook, passed with clang's `-include`, that does the rename exactly there. The hook is compiled in only
when `FLACKEY_TEST_BEFORE_SWAP` is defined, which the release build never does (asserted below). Built
with the stub opener too, so the test can see that nothing is reopened afterwards. macOS only.
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest

from .test_update_helper import REFUSED, SOURCE, STAGING, TARGET, make_bundle, marker_of, run

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="the helper is macOS-only")

DECOY = "Decoy.app"
ASIDE = "checked-aside"

# Moves the checked bundle out of the way and a different one onto its name, between the last check and
# the swap: what another process running as the owner could do in that gap.
HOOK = f"""
#include <stdio.h>
static void flackey_test_before_swap(int dirfd, const char *staging) {{
    if (renameat(dirfd, staging, dirfd, "{ASIDE}") != 0) perror("hook: move the checked bundle aside");
    if (renameat(dirfd, "{DECOY}", dirfd, staging) != 0) perror("hook: move the decoy in");
}}
#define FLACKEY_TEST_BEFORE_SWAP flackey_test_before_swap
"""


@pytest.fixture(scope="session")
def raced(tmp_path_factory) -> tuple[Path, Path]:
    """(helper, record): a helper that swaps a decoy onto the staging name just before its swap, and whose
    relaunch runs a stub that appends its arguments to `record`."""
    where = tmp_path_factory.mktemp("raced-helper")
    record = where / "opened.txt"
    stub = where / "open-stub"
    stub.write_text(f'#!/bin/sh\nprintf "%s\\n" "$@" >> "{record}"\n')
    stub.chmod(0o755)
    hook = where / "hook.h"
    hook.write_text(HOOK)
    out = where / "flackey-update-helper"
    subprocess.run(["clang", "-O2", "-Wall", "-Wextra", "-Werror", f'-DFLACKEY_OPEN_PATH="{stub}"',
                    "-include", str(hook), "-o", str(out), str(SOURCE)], check=True, capture_output=True)
    # A source that stopped calling the hook would make the test below pass without any race happening.
    assert b"hook: move the decoy in" in out.read_bytes(), "the helper was not built with the hook"
    return out, record


@pytest.fixture
def tree(tmp_path):
    applications = tmp_path / "Applications"
    applications.mkdir()
    make_bundle(applications / TARGET, marker="old")
    make_bundle(applications / STAGING, marker="new")
    # Shaped like a Flackey bundle, so nothing but its identity tells it apart.
    make_bundle(applications / DECOY, marker="decoy")
    return applications


def opened_paths(record: Path, wait_s: float) -> list[str]:
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        if record.exists():
            return record.read_text().split()
        time.sleep(0.05)
    return []


def test_a_bundle_renamed_in_after_the_check_is_swapped_back_out(raced, tree, tmp_path):
    helper, record = raced
    record.unlink(missing_ok=True)
    log = tmp_path / "helper.log"

    result = run(helper, tree, relaunch="1", log=log)

    assert result.returncode == REFUSED, result.stderr
    # The installed app is the one that was there before; the decoy never stays at Flackey.app.
    assert marker_of(tree / TARGET) == "old"
    assert marker_of(tree / STAGING) == "decoy"
    assert marker_of(tree / ASIDE) == "new"
    text = log.read_text()
    assert "swapped back" in text
    assert "swapped .Flackey" not in text
    # Not reopened: the folder was tampered with, and the owner should see that rather than a relaunch.
    assert opened_paths(record, wait_s=0.5) == []


def test_the_release_build_never_compiles_the_test_hook():
    """The hook is only ever included by the test above. The release build's compile line must not
    define it or force-include anything."""
    build = (Path(__file__).resolve().parents[1] / "packaging" / "build_app.sh").read_text()
    lines = [line for line in build.splitlines() if "update_helper.c" in line and "clang" in line]
    assert len(lines) == 1, lines
    assert "-D" not in lines[0] and "-include" not in lines[0]
    assert "FLACKEY_TEST_BEFORE_SWAP" not in build
