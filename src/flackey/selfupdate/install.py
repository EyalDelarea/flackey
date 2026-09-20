"""Unpack a verified update beside the installed app, and hand the swap to the helper.

A process cannot replace the bundle it is executing from, so the work is split in two: everything here
happens while the app is alive and can still show an error, and the swap itself happens afterwards in
`packaging/update_helper.c`. Nothing here ever removes or moves the installed app.
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
# Hidden, and unique per attempt so a path planted in advance can never be the one that gets used. Kept
# in step with `staging_name_ok` in packaging/update_helper.c, which refuses anything else.
STAGING_PREFIX = ".Flackey-staging-"
STAGING_RANDOM_CHARS = 10
# `ditto -x -k` of a `--keepParent` archive writes `<dest>/Flackey.app`, so the unpack needs a directory
# of its own before the bundle inside it can be renamed to the staging name.
UNPACK_PREFIX = ".Flackey-unpack-"
STEP_TIMEOUT_S = 300  # unpacking ~100MB; generous, but not "forever" if ditto wedges


class StagingError(Exception):
    """A step between verification and a bundle ready to swap in did not complete. Carries a sentence
    written for the owner, because that is what the Settings row shows."""


@dataclass(frozen=True)
class StagedUpdate:
    """A verified bundle sitting in /Applications, waiting for the app to quit."""

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
    # Inside the bundle the executable is Contents/MacOS/Flackey, so the bundle is three levels up.
    macos = Path(sys.executable).resolve().parent
    if macos.name != "MacOS" or macos.parent.name != "Contents":
        return None
    bundle = macos.parent.parent
    return bundle if bundle.suffix == ".app" else None


def helper_path() -> Path | None:
    """The helper this build carries, or None when it is not there.

    Not resolved through `tools.tool_path`, deliberately: that falls back to the PATH and to Homebrew,
    and a `flackey-update-helper` found on the PATH is exactly the thing not to run."""
    from ..tools import bundled_bin_dir

    root = bundled_bin_dir()
    if root is None:
        return None
    candidate = root / HELPER_NAME
    return candidate if candidate.is_file() and os.access(candidate, os.X_OK) else None


def seamless_available(applications: Path = APPLICATIONS) -> bool:
    """Whether this copy of Flackey can replace itself in place. False means "use the installer", never
    "this update failed"."""
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
    """Unpack a *verified* archive into `applications` and return the staged bundle.

    Never call this with bytes that have not been through `signature.verify` -- unpacking is the first
    step that writes attacker-influenced content to disk.

    Inside /Applications rather than a temp folder because `rename` cannot cross filesystems and the
    swap at the end is a rename."""
    sweep(applications)
    suffix = secrets.token_hex(STAGING_RANDOM_CHARS // 2)
    unpack = applications / f"{UNPACK_PREFIX}{suffix}"
    staged = applications / f"{STAGING_PREFIX}{suffix}.app"
    # A path that already exists was not made by this call, so unpacking into it would be unpacking over
    # somebody else's directory. Refuse instead.
    for path in (unpack, staged):
        if path.exists() or path.is_symlink():
            raise StagingError("Could not prepare the update: that folder is already in use.")
    try:
        unpack.mkdir(mode=0o700)
    except OSError as exc:
        log.exception("could not create %s", unpack)
        raise StagingError("Could not write to the Applications folder to prepare the update.") from exc

    try:
        # `ditto`, not Python's zipfile: the bundle contains symlinks and the resource forks of a signed
        # app, and a zipfile round-trip flattens both into something macOS refuses to launch.
        _run(["/usr/bin/ditto", "-x", "-k", str(archive), str(unpack)], "ditto -x",
             "The update file could not be unpacked. Download it again.")
        inner = unpack / BUNDLE_NAME
        if not inner.is_dir() or inner.is_symlink():
            raise StagingError("The update file did not contain Flackey.app.")
        # `ditto` propagates a quarantine attribute onto everything it extracts, and a quarantined
        # ad-hoc bundle is unopenable. Stripped before the rename, so the staging path is already clean.
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
    """Throw away a staged bundle. Only ever removes a path under the staging name, so a bug in a caller
    cannot turn this into a delete of the installed app."""
    if staged is None or not staged.path.name.startswith(STAGING_PREFIX):
        return
    shutil.rmtree(staged.path, ignore_errors=True)


def sweep(applications: Path) -> None:
    """Remove ~200MB staging directories left over from earlier attempts -- pressing Update and then
    closing the window without answering the restart prompt leaves one behind every time.

    Called at the top of `stage`. If an older bundle had been armed for install-on-quit its path is now
    gone, which the helper already refuses rather than acts on."""
    try:
        entries = list(applications.iterdir())
    except OSError:
        return
    for entry in entries:
        if not entry.name.startswith((STAGING_PREFIX, UNPACK_PREFIX)):
            continue
        try:
            # Never follow a link out of the directory, and never delete what is not ours.
            if entry.is_symlink() or entry.lstat().st_uid != os.getuid():
                continue
        except OSError:
            continue
        log.info("removing a staged update left over from an earlier attempt: %s", entry)
        shutil.rmtree(entry, ignore_errors=True)


def launch_helper(staged: StagedUpdate, *, log_path: Path | None = None) -> None:
    """Copy the helper out of the bundle and spawn it detached. The last thing the app does.

    Copied out now rather than at staging time: "install on quit" may be hours later, and a writable
    copy of a program whose job is to replace an app in /Applications should not sit in a temp directory
    for a whole session. It cannot run from inside the bundle it is about to swap away either.

    Raises StagingError if the copy fails, so the caller can abort the *update* rather than the quit."""
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
        # `start_new_session` is the whole point: a child in this session would die with it, and the
        # helper has to outlive the process that spawned it.
        subprocess.Popen(argv, start_new_session=True, close_fds=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as exc:
        shutil.rmtree(workdir, ignore_errors=True)
        log.exception("could not spawn the update helper")
        raise StagingError("The update could not be started. Flackey is unchanged.") from exc
    log.info("update helper spawned for %s", staged.path)


class Pending:
    """Whether this app, when it next stops, should replace itself.

    Process-wide because the fact is: the press happens on a uvicorn worker thread and the quit happens
    on the AppKit main thread, and no object already spans both."""

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
        """Spawn the helper if one is armed. Called from the quit path once the server and sidecar are
        down, so the bundle being swapped is not one anything is still reading.

        Never raises: this runs inside the `finally` that ends the program."""
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
