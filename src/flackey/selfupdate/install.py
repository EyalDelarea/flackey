"""Unpack a verified update beside the installed app; the swap itself is update_helper.c.

A process cannot replace the bundle it is executing from. Nothing here touches the installed app.
"""

from __future__ import annotations

import logging
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from . import signature

log = logging.getLogger(__name__)

APPLICATIONS = Path("/Applications")
BUNDLE_NAME = "Flackey.app"
HELPER_NAME = "flackey-update-helper"
# Kept in step with `staging_name_ok` in update_helper.c, which refuses anything else.
STAGING_PREFIX = ".Flackey-staging-"
STAGING_RANDOM_CHARS = 10
# `ditto -x -k` of a `--keepParent` archive writes `<dest>/Flackey.app`, so it needs its own directory.
UNPACK_PREFIX = ".Flackey-unpack-"
STEP_TIMEOUT_S = 300


class StagingError(Exception):
    """Carries a sentence for the owner: it is what the Settings row shows."""


@dataclass(frozen=True)
class StagedUpdate:
    path: Path          # /Applications/.Flackey-staging-XXXXXXXXXX.app
    version: str
    relaunch: bool = True

    @property
    def directory(self) -> Path:
        return self.path.parent


def running_bundle() -> Path | None:
    """The `.app` this process is running from, or None from a checkout."""
    if not getattr(sys, "frozen", False):
        return None
    macos = Path(sys.executable).resolve().parent
    if macos.name != "MacOS" or macos.parent.name != "Contents":
        return None
    bundle = macos.parent.parent
    return bundle if bundle.suffix == ".app" else None


def helper_path() -> Path | None:
    # Not `tools.tool_path`: that falls back to the PATH, and a helper found there is the thing not
    # to run.
    from ..tools import bundled_bin_dir

    root = bundled_bin_dir()
    if root is None:
        return None
    candidate = root / HELPER_NAME
    return candidate if candidate.is_file() and os.access(candidate, os.X_OK) else None


def seamless_available(applications: Path = APPLICATIONS) -> bool:
    """False means "use the installer", never "this update failed"."""
    bundle = running_bundle()
    return (signature.seamless_updates_configured()
            and bundle is not None
            and bundle == applications / BUNDLE_NAME
            and helper_path() is not None)


def _run(argv: list[str], what: str, message: str) -> None:
    try:
        subprocess.run(argv, check=True, capture_output=True, timeout=STEP_TIMEOUT_S)
    except subprocess.CalledProcessError as exc:
        log.error("%s failed (rc=%d): %s", what, exc.returncode, exc.stderr.decode("utf-8", "replace"))
        raise StagingError(message) from exc
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.exception("%s did not run", what)
        raise StagingError(message) from exc


def stage(archive: Path, version: str, *, applications: Path = APPLICATIONS,
          relaunch: bool = True) -> StagedUpdate:
    """Unpack a *verified* archive into `applications`. Never call this with unverified bytes.

    Inside /Applications because `rename` cannot cross filesystems and the swap is a rename."""
    sweep(applications)
    suffix = secrets.token_hex(STAGING_RANDOM_CHARS // 2)
    unpack = applications / f"{UNPACK_PREFIX}{suffix}"
    staged = applications / f"{STAGING_PREFIX}{suffix}.app"
    # An existing path was not made by this call, so unpacking into it would be unpacking over
    # somebody else's directory.
    for path in (unpack, staged):
        if path.exists() or path.is_symlink():
            raise StagingError("Could not prepare the update: that folder is already in use.")
    try:
        unpack.mkdir(mode=0o700)
    except OSError as exc:
        log.exception("could not create %s", unpack)
        raise StagingError("Could not write to the Applications folder to prepare the update.") from exc

    try:
        # `ditto`, not zipfile: a zipfile round-trip flattens the bundle's symlinks and resource forks
        # into something macOS refuses to launch.
        _run(["/usr/bin/ditto", "-x", "-k", str(archive), str(unpack)], "ditto -x",
             "The update file could not be unpacked. Download it again.")
        inner = unpack / BUNDLE_NAME
        if not inner.is_dir() or inner.is_symlink():
            raise StagingError("The update file did not contain Flackey.app.")
        # `ditto` propagates quarantine onto everything it extracts, and a quarantined ad-hoc bundle
        # is unopenable.
        _run(["/usr/bin/xattr", "-rc", str(inner)], "xattr -rc",
             "The update could not be prepared for installation.")
        _run(["/bin/chmod", "-R", "go-w", str(inner)], "chmod -R go-w",
             "The update could not be prepared for installation.")
        inner.rename(staged)
    except Exception:
        shutil.rmtree(unpack, ignore_errors=True)
        shutil.rmtree(staged, ignore_errors=True)
        raise
    shutil.rmtree(unpack, ignore_errors=True)
    return StagedUpdate(path=staged, version=version, relaunch=relaunch)


def discard(staged: StagedUpdate | None) -> None:
    """Only ever removes a path under the staging name, so a caller's bug cannot delete the app."""
    if staged is None or not staged.path.name.startswith(STAGING_PREFIX):
        return
    shutil.rmtree(staged.path, ignore_errors=True)


def sweep(applications: Path) -> None:
    """Remove ~200MB staging directories abandoned by earlier attempts."""
    try:
        entries = list(applications.iterdir())
    except OSError:
        return
    for entry in entries:
        if not entry.name.startswith((STAGING_PREFIX, UNPACK_PREFIX)):
            continue
        try:
            if entry.is_symlink() or entry.lstat().st_uid != os.getuid():
                continue
        except OSError:
            continue
        log.info("removing a staged update left over from an earlier attempt: %s", entry)
        shutil.rmtree(entry, ignore_errors=True)


def launch_helper(staged: StagedUpdate, *, log_path: Path | None = None) -> None:
    """Copy the helper out of the bundle and spawn it detached. The last thing the app does.

    It cannot run from inside the bundle it is about to swap away. Copied out now rather than at
    staging time, so it is not left sitting in a temp directory for a whole session."""
    helper = helper_path()
    if helper is None:
        raise StagingError("This copy of Flackey cannot install updates by itself.")
    try:
        workdir = Path(tempfile.mkdtemp(prefix="flackey-update-"))
        os.chmod(workdir, 0o700)
        runner = workdir / HELPER_NAME
        shutil.copy2(helper, runner)
        os.chmod(runner, 0o700)
    except OSError as exc:
        log.exception("could not copy the update helper out of the bundle")
        raise StagingError("The update could not be started. Flackey is unchanged.") from exc

    argv = [str(runner), str(os.getpid()), str(staged.directory), staged.path.name,
            "1" if staged.relaunch else "0"]
    if log_path is not None:
        argv.append(str(log_path))
    try:
        # `start_new_session`: a child in this session would die with it, and the helper has to
        # outlive the process that spawned it.
        subprocess.Popen(argv, start_new_session=True, close_fds=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as exc:
        shutil.rmtree(workdir, ignore_errors=True)
        log.exception("could not spawn the update helper")
        raise StagingError("The update could not be started. Flackey is unchanged.") from exc
    log.info("update helper spawned for %s", staged.path)


class Pending:
    """Whether this app, when it next stops, should replace itself.

    Process-wide: the press lands on a uvicorn worker thread and the quit on the AppKit main thread."""

    def __init__(self) -> None:
        self._staged: StagedUpdate | None = None

    def arm(self, staged: StagedUpdate) -> None:
        self._staged = staged

    @property
    def staged(self) -> StagedUpdate | None:
        return self._staged

    def clear(self) -> None:
        self._staged = None

    def run(self, *, log_path: Path | None = None) -> bool:
        """Spawn the helper if one is armed. Never raises: this runs in the `finally` that ends the
        program."""
        staged = self._staged
        self._staged = None
        if staged is None:
            return False
        try:
            launch_helper(staged, log_path=log_path)
        except StagingError:
            log.error("the update was staged at %s but the helper could not be started; Flackey is "
                      "unchanged and the staged copy is still there", staged.path)
            return False
        except Exception:
            log.exception("unexpected failure starting the update helper")
            return False
        return True


pending = Pending()
