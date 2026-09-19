"""Unpack a verified update beside the installed app, and hand the swap to the helper.

The shape of this file is dictated by one fact: a running process cannot replace the bundle it is
executing from. So the work is split in two. Everything here happens while the app is alive and can
still show the owner an error -- unpack, normalise, validate, stage. The swap itself happens after this
process is gone, in `packaging/update_helper.c`, which this module copies out and spawns on the way
down.

The invariant every path below is written to keep, taken from the design doc:

    /Applications/Flackey.app always points at a working bundle -- the original or the new one, never
    deleted with no replacement.

Which is why nothing here ever removes or moves the installed app. The only operation that touches it
is the helper's atomic swap, and that is the last thing to happen rather than the first.
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
# Hidden, so a staging directory that outlives a failed attempt is not something the owner trips over in
# Finder, and unique per attempt, so a path planted in advance can never be the one that gets used. Kept
# in step with `staging_name_ok` in packaging/update_helper.c, which refuses anything else.
STAGING_PREFIX = ".Flackey-staging-"
STAGING_RANDOM_CHARS = 10
# `ditto -x -k` of a `--keepParent` archive writes `<dest>/Flackey.app`, so the unpack needs a directory
# of its own before the bundle inside it can be renamed to the staging name.
UNPACK_PREFIX = ".Flackey-unpack-"
STEP_TIMEOUT_S = 300  # unpacking ~100MB; generous, but not "forever" if ditto wedges


class StagingError(Exception):
    """A step between "the signature verified" and "the bundle is ready to swap in" did not complete.

    Carries a sentence written for the owner rather than for the log, because that is what the Settings
    row shows. Every raise site below is a row in the design doc's failure table."""


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
    """The `.app` this process is running from, or None from a checkout.

    Only the packaged build can update itself: `python -m flackey` has no bundle to replace, and a
    development checkout that tried would be replacing whatever `.app` happened to be around it."""
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

    Lives beside ffmpeg in the bundle's `bin` folder -- `tools.bundled_bin_dir()` -- because that is
    already the place PyInstaller unpacks binaries to and already where the app looks for its own
    programs. Not resolved through `tools.tool_path`, deliberately: that falls back to the PATH and to
    Homebrew, and a `flackey-update-helper` found on the PATH is exactly the thing not to run."""
    from ..tools import bundled_bin_dir

    root = bundled_bin_dir()
    if root is None:
        return None
    candidate = root / HELPER_NAME
    return candidate if candidate.is_file() and os.access(candidate, os.X_OK) else None


def seamless_available(applications: Path = APPLICATIONS) -> bool:
    """Whether this copy of Flackey can replace itself in place.

    Three separate reasons it may not be able to, all of which mean "use the installer", none of which
    mean "this update failed":

    - No key is baked in, so nothing could be verified. See `selfupdate/key.py`.
    - The app is not the one in /Applications -- running from a Downloads folder, a second copy, or a
      checkout. Replacing `/Applications/Flackey.app` from there would update an app the owner is not
      looking at.
    - The build carries no helper, which means it was assembled by something other than build_app.sh.
    """
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

    Never call this with bytes that have not been through `signature.verify`. Unpacking is the first
    step that writes attacker-influenced content to disk, and the design puts verification strictly
    before it so that a forged payload never reaches `ditto` at all.

    Inside /Applications rather than a temp folder because `rename` cannot cross filesystems and the
    swap at the end is a rename. On any failure the staged directory is removed and the installed app is
    untouched -- it is not touched until the helper runs, and the helper does not run unless this
    returns."""
    suffix = secrets.token_hex(STAGING_RANDOM_CHARS // 2)
    unpack = applications / f"{UNPACK_PREFIX}{suffix}"
    staged = applications / f"{STAGING_PREFIX}{suffix}{'.app'}"
    # A path that already exists was not made by this call. Reusing it would mean unpacking over
    # somebody else's directory, which is the planted-path shape Sparkle was bitten by; refuse instead.
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
        # Strip quarantine *before* the rename, so what lands at the staging path is already clean --
        # `httpx` sets no quarantine attribute today, but `ditto` propagates one onto every extracted
        # file if the archive ever carries one, and a quarantined ad-hoc bundle is unopenable.
        _run(["/usr/bin/xattr", "-rc", str(inner)], "xattr -rc",
             "The update could not be prepared for installation.")
        # Only the owner can write inside the new bundle. This does not stop it being replaced wholesale
        # -- /Applications is group-writable and this whole design is built on that -- but it narrows
        # editing files inside it from the `admin` group to one account.
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


def launch_helper(staged: StagedUpdate, *, log_path: Path | None = None) -> None:
    """Copy the helper out of the bundle and spawn it detached. The last thing the app does.

    Copied out *now* rather than when the update was staged. "Install on quit" may be hours later, and a
    helper left sitting in a temp directory for a whole session is the stray-binary shape Sparkle warns
    about in its own source -- somebody else's writable copy of a program whose whole job is to replace
    an app in /Applications. A fresh 0700 directory per run, created by `mkdtemp` so the name cannot be
    guessed or pre-planted, and the helper removes it on its way out.

    The helper cannot run from inside the bundle it is about to swap away: the swap would pull the
    filesystem out from under a running executable.

    Raises StagingError if the copy fails, so the caller can abort the *update* rather than the quit --
    the staged bundle is still good and the next attempt can use it."""
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
        # `start_new_session` is the whole point: the helper has to outlive the process that spawned it,
        # and a child in this session dies with it. Detached, it reparents to launchd and waits.
        subprocess.Popen(argv, start_new_session=True, close_fds=True,
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as exc:
        shutil.rmtree(workdir, ignore_errors=True)
        log.exception("could not spawn the update helper")
        raise StagingError("The update could not be started. Flackey is unchanged.") from exc
    log.info("update helper spawned for %s", staged.path)


class Pending:
    """The one thing the web thread and the window thread have to agree on: whether this app, when it
    next stops, should replace itself.

    Process-wide because the fact is process-wide -- the press happens on a uvicorn worker thread and
    the quit happens on the AppKit main thread, and there is no object that already spans both. Kept to
    three methods so that what crosses that boundary is a single assignment of an immutable value."""

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
        """Spawn the helper if one is armed. Called from the quit path once the server has stopped and
        the sidecar is down, so that the bundle being swapped is not one anything is still reading.

        Never raises: this runs inside the `finally` that ends the program, and an exception there would
        replace a clean exit with a traceback for a user who has already been told the app is closing."""
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
