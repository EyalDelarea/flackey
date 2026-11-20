"""Unpack a verified update beside the installed app; the swap itself is update_helper.c.

A process cannot replace the bundle it is executing from. Nothing here touches the installed app.
"""

from __future__ import annotations

import hashlib
import logging
import os
import secrets
import shutil
import stat
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
    # `manifest(path)` as staging left it; checked again right before the helper is spawned. None only
    # for one built by hand rather than by `stage`.
    manifest: bytes | None = None

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
    """False means "use the installer", never "this update failed".

    Always False on Windows, and said first rather than left to fall out of the checks below: the swap
    is `ditto`, `xattr` and a C helper that renames a `.app`, none of which exist there, and `sweep`
    calls `os.getuid`, which Windows does not have. Windows updates by running Flackey-Setup.exe."""
    if sys.platform == "win32":
        return False
    bundle = running_bundle()
    return (signature.seamless_updates_configured()
            and bundle is not None
            and bundle == applications / BUNDLE_NAME
            and helper_path() is not None)


def stray_copy(applications: Path = APPLICATIONS) -> Path | None:
    """The bundle this process runs from, when it is not the one in Applications but Applications has one:
    a local build or an old copy that Spotlight or a double-click opened instead (2026-10-14). It cannot
    swap itself, and running the installer from it only reinstalls the copy that was already there, so the
    owner is sent to that one. With nothing in Applications the installer is how the app gets there."""
    if sys.platform != "darwin":
        return None
    bundle = running_bundle()
    installed = applications / BUNDLE_NAME
    return bundle if bundle is not None and bundle != installed and installed.is_dir() else None


def _run(argv: list[str], what: str, message: str) -> None:
    try:
        subprocess.run(argv, check=True, capture_output=True, timeout=STEP_TIMEOUT_S)
    except subprocess.CalledProcessError as exc:
        log.error("%s failed (rc=%d): %s", what, exc.returncode, exc.stderr.decode("utf-8", "replace"))
        raise StagingError(message) from exc
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.exception("%s did not run", what)
        raise StagingError(message) from exc


def manifest(bundle: Path) -> bytes:
    """A sha256 over every entry in `bundle`: its relative path, type and permission bits, and a file's
    contents or a symlink's target. Symlinks are never followed."""
    digest = hashlib.sha256()
    for root, dirs, files in os.walk(bundle, followlinks=False):
        dirs.sort()
        for name in sorted(dirs + files):
            entry = Path(root) / name
            st = entry.lstat()
            digest.update(os.fsencode(entry.relative_to(bundle)) + b"\0" + str(st.st_mode).encode() + b"\0")
            if stat.S_ISLNK(st.st_mode):
                digest.update(os.fsencode(os.readlink(entry)))
            elif stat.S_ISREG(st.st_mode):
                with entry.open("rb") as f:
                    digest.update(hashlib.file_digest(f, "sha256").digest())
            digest.update(b"\0")
    return digest.digest()


def _private_copy(archive: Path, version: str) -> Path:
    """The verified bytes, written to a file in a fresh 0700 directory, which the caller removes.

    The archive is read once, and those bytes are both the ones checked against what `verify_archive`
    accepted and the ones written out -- so `ditto` never reads a path anybody could have swapped since."""
    try:
        payload = archive.read_bytes()
    except OSError as exc:
        log.exception("could not read back the update archive at %s", archive)
        raise StagingError("The update file could not be unpacked. Download it again.") from exc
    if not signature.was_verified(version, hashlib.sha256(payload).digest()):
        log.error("the archive at %s is not the one that was verified; refusing to unpack it", archive)
        raise StagingError("This update could not be verified, so Flackey did not install it.")
    private = Path(tempfile.mkdtemp(prefix="flackey-update-"))
    copy = private / archive.name
    try:
        fd = os.open(copy, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(payload)
    except OSError as exc:
        shutil.rmtree(private, ignore_errors=True)
        log.exception("could not write a private copy of the update")
        raise StagingError("Could not prepare the update. The disk may be full.") from exc
    return copy


def stage(archive: Path, version: str, *, applications: Path = APPLICATIONS,
          relaunch: bool = True) -> StagedUpdate:
    """Unpack a *verified* archive into `applications`. Refused unless these exact bytes passed
    `signature.verify_archive` for this version.

    Inside /Applications because `rename` cannot cross filesystems and the swap is a rename."""
    sweep(applications)
    copy = _private_copy(archive, version)
    try:
        return _unpack(copy, version, applications, relaunch)
    finally:
        shutil.rmtree(copy.parent, ignore_errors=True)


def _unpack(archive: Path, version: str, applications: Path, relaunch: bool) -> StagedUpdate:
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
        recorded = manifest(staged)
    except Exception:
        shutil.rmtree(unpack, ignore_errors=True)
        shutil.rmtree(staged, ignore_errors=True)
        raise
    shutil.rmtree(unpack, ignore_errors=True)
    return StagedUpdate(path=staged, version=version, relaunch=relaunch, manifest=recorded)


def discard(staged: StagedUpdate | None) -> None:
    """Only ever removes a path under the staging name, so a caller's bug cannot delete the app."""
    if staged is None or not staged.path.name.startswith(STAGING_PREFIX):
        return
    shutil.rmtree(staged.path, ignore_errors=True)


def _ours(name: str) -> bool:
    """A name `stage` makes, held to what `staging_name_ok` in update_helper.c accepts: the prefix, then
    letters and digits only (then `.app` for the staged bundle). Anything else in /Applications is not
    ours to remove, however close its name."""
    for prefix, suffix in ((STAGING_PREFIX, ".app"), (UNPACK_PREFIX, "")):
        if name.startswith(prefix) and name.endswith(suffix):
            middle = name[len(prefix):len(name) - len(suffix)]
            return middle.isascii() and middle.isalnum()
    return False


def _remove(entry: Path) -> None:
    """`rmtree`, except that a folder left read-only (a swapped-out bundle can be) is made writable for its
    owner and the removal retried. Only the folder whose entry could not be removed is touched, never
    through a symlink, and only when it is ours."""
    uid = os.getuid()

    def retry(func, path, exc) -> None:
        parent = os.path.dirname(path)
        if not isinstance(exc, PermissionError):
            raise exc
        st = os.lstat(parent)
        if st.st_uid != uid or stat.S_ISLNK(st.st_mode):
            raise exc
        os.chmod(parent, stat.S_IMODE(st.st_mode) | stat.S_IRWXU, follow_symlinks=False)
        func(path)

    shutil.rmtree(entry, onexc=retry)


_unremovable: set[str] = set()  # leftovers already reported, so a sweep per launch does not repeat itself


def sweep(applications: Path) -> None:
    """Remove ~200MB staging directories abandoned by earlier attempts, and the old bundle a swap leaves
    under the staging name. Run before staging and again at startup.

    A leftover owned by someone else is left alone: the first swap after a `.pkg` install leaves the
    installer's root-owned bundle behind, and only an administrator can delete that."""
    try:
        entries = list(applications.iterdir())
    except OSError:
        return
    for entry in entries:
        if not _ours(entry.name):
            continue
        try:
            st = entry.lstat()
        except OSError:
            continue
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
            continue
        if st.st_uid != os.getuid():
            if entry.name not in _unremovable:
                _unremovable.add(entry.name)
                log.info("cannot remove %s: it belongs to another user (uid %d), most likely the installer's "
                         "copy an update replaced; an administrator can delete it", entry, st.st_uid)
            continue
        log.info("removing a staged update left over from an earlier attempt: %s", entry)
        try:
            _remove(entry)
        except OSError as exc:
            log.warning("could not remove %s: %s", entry, exc)


def launch_helper(staged: StagedUpdate, *, log_path: Path | None = None) -> None:
    """Copy the helper out of the bundle and spawn it detached. The last thing the app does.

    It cannot run from inside the bundle it is about to swap away. Copied out now rather than at
    staging time, so it is not left sitting in a temp directory for a whole session."""
    helper = helper_path()
    if helper is None:
        raise StagingError("This copy of Flackey cannot install updates by itself.")
    # The staged bundle has sat in /Applications since it was unpacked, possibly for a whole session:
    # it is swapped in only if it is still exactly what staging produced.
    try:
        current = manifest(staged.path)
    except OSError:
        log.exception("could not re-read the staged update at %s", staged.path)
        current = None
    if current is None or (staged.manifest is not None and current != staged.manifest):
        log.error("the staged update at %s changed after it was unpacked; discarding it", staged.path)
        discard(staged)
        raise StagingError("The prepared update changed before it could be installed, so Flackey "
                           "did not install it.")
    try:
        workdir = Path(tempfile.mkdtemp(prefix="flackey-update-"))
        os.chmod(workdir, 0o700)
        runner = workdir / HELPER_NAME
        shutil.copy2(helper, runner)
        os.chmod(runner, 0o700)
    except OSError as exc:
        log.exception("could not copy the update helper out of the bundle")
        raise StagingError("The update could not be started. Flackey is unchanged.") from exc

    # The helper hashes the bundle again right before the swap, and refuses one that no longer matches.
    argv = [str(runner), str(os.getpid()), str(staged.directory), staged.path.name,
            "1" if staged.relaunch else "0", current.hex()]
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
                      "unchanged", staged.path)
            return False
        except Exception:
            log.exception("unexpected failure starting the update helper")
            return False
        return True


pending = Pending()
