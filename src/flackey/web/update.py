from __future__ import annotations

import asyncio
import datetime as dt
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import webbrowser
from pathlib import Path
from urllib.parse import unquote, urlsplit

import httpx
from fastapi import APIRouter, HTTPException, Request

from .. import __version__
from ..config import Settings
from ..events import Status
from ..selfupdate import install as selfupdate
from ..selfupdate import signature
from .guard import from_the_app

log = logging.getLogger(__name__)

RELEASES_URL = "https://api.github.com/repos/EyalDelarea/flackey/releases?per_page=10"
# Where a release's files and page may live. The feed is trusted to name the release, not to send the app
# anywhere it likes: an asset or page link outside the repo is treated as missing. Checked on the link
# the feed gives, after decoding, so `..` cannot walk it out of the folder.
RELEASE_PATH = "/EyalDelarea/flackey/releases/"
ASSET_PATH = RELEASE_PATH + "download/"
# Every hop of every fetch, redirects included, must be HTTPS to one of these. GitHub answers an asset
# link with a 302 to its CDN: release-assets.githubusercontent.com today, objects.githubusercontent.com
# before that.
FEED_HOSTS = frozenset({"api.github.com"})
DOWNLOAD_HOSTS = frozenset({"github.com", "objects.githubusercontent.com",
                            "release-assets.githubusercontent.com"})
MAX_REDIRECTS = 5
# Whatever the feed declares, nothing larger is fetched: a real installer is about a tenth of this.
MAX_DOWNLOAD_BYTES = 512 * 1024 * 1024
# The private folder each installer is opened from (see `private_copy`).
INSTALLER_COPY_PREFIX = "installer-"
_VERSION = re.compile(r"v?(\d+)\.(\d+)\.(\d+)", re.ASCII)
INSTALLER_NAME = "Flackey.pkg"
# The Windows installer, under the same name on every release so this lookup never has to guess a
# version into it. Inno Setup builds it; there is no seamless path on Windows yet, so it is the only one.
WINDOWS_INSTALLER_NAME = "Flackey-Setup.exe"
SIGNATURE_SUFFIX = ".sig"
MAX_SIGNATURE_BYTES = 4096
# Per read, not for the whole transfer: a single deadline would abort a slow but healthy download.
DOWNLOAD_TIMEOUT = httpx.Timeout(30.0, connect=10.0)
PROGRESS_KEY = "update_download"
INCOMPLETE = "The download arrived incomplete. Check your connection and try again."


class ShortDownload(Exception):
    """The body did not match the size the release declared -- too few bytes or too many."""


class RefusedHost(httpx.HTTPError):
    """A fetch, or a redirect it was sent on, that leaves GitHub or HTTPS. An `HTTPError` so every caller
    already words it as the failed download it is."""


class Unverified(Exception):
    """The installer on disk is not the one the release signed, or this copy cannot tell."""


def parse_version(v: object) -> tuple[int, int, int] | None:
    """`1.2.3` or `v1.2.3` and nothing else: no padding, no suffix, no digits from other scripts."""
    m = _VERSION.fullmatch(v) if isinstance(v, str) else None
    return (int(m[1]), int(m[2]), int(m[3])) if m else None


def github_link(url: object, path_prefix: str) -> str | None:
    """`url` when it is a plain https://github.com link under `path_prefix`, else None."""
    if not isinstance(url, str):
        return None
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if parts.scheme != "https" or parts.netloc != "github.com" or parts.query or parts.fragment:
        return None
    path = unquote(parts.path)
    if not path.startswith(path_prefix) or "\\" in path or any(ord(c) < 0x21 or ord(c) == 0x7F for c in path):
        return None
    if any(segment in (".", "..") for segment in path.split("/")):
        return None
    return url


def _only(hosts: frozenset[str]):
    async def check(request: httpx.Request) -> None:
        url = request.url
        if url.scheme != "https" or url.host not in hosts or url.port not in (None, 443) or url.userinfo:
            raise RefusedHost(f"refused to fetch from {url.scheme}://{url.host}")
    return check


def github_client(hosts: frozenset[str], timeout) -> httpx.AsyncClient:
    """Follows redirects, but only a few, and each one is checked against `hosts` before it is sent."""
    return httpx.AsyncClient(timeout=timeout, follow_redirects=True, max_redirects=MAX_REDIRECTS,
                             event_hooks={"request": [_only(hosts)]})


def can_verify() -> bool:
    return signature.seamless_updates_configured()


def must_verify() -> bool:
    """A build that carries the release key checks every installer against it. A packaged build always
    must: one whose key is missing or malformed is broken, and opening unchecked installers is the last
    thing it should fall back to. Only a source checkout without a key opens what it downloads as is."""
    return can_verify() or bool(getattr(sys, "frozen", False))


def _size_mb(size: int | None) -> str | None:
    return f"{size / 1e6:.1f} MB" if size else None


def archive_name(version: str) -> str:
    """Built from the version, not matched by pattern, so somebody else's zip cannot be mistaken
    for this one."""
    return f"Flackey-{version}.zip"


def _release_page(latest: dict | None) -> str | None:
    return github_link(latest.get("html_url"), RELEASE_PATH) if latest else None


def installer_name() -> str:
    """The release asset this system installs from: the `.pkg` on the Mac, `Flackey-Setup.exe` on
    Windows. Also the name the download is saved under, and the one the "open it yourself" message
    names, so the three can never disagree."""
    return WINDOWS_INSTALLER_NAME if sys.platform == "win32" else INSTALLER_NAME


def open_installer(path: Path) -> None:
    if sys.platform == "win32":
        # The shell runs the .exe as a double-click would -- including SmartScreen's prompt for an
        # unsigned download, which is the owner's to answer, and the UAC prompt if Inno asks for one.
        os.startfile(path)
        return
    subprocess.Popen(["open", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def open_url(url: str) -> None:
    """`window.open` from the page does nothing inside pywebview."""
    webbrowser.open(url)


async def latest_release() -> dict:
    """The download runs this lookup again for itself: the client never says what to fetch, so a
    stale page cannot talk the app into downloading something else."""
    try:
        async with github_client(FEED_HOSTS, 5) as client:
            res = await client.get(RELEASES_URL)
            res.raise_for_status()
        releases = res.json()
    except (httpx.HTTPError, ValueError):
        return {"ok": False, "current": __version__, "newer": False, "available": False,
                "error": "Could not check for updates."}
    if not isinstance(releases, list):
        return {"ok": False, "current": __version__, "newer": False, "available": False,
                "error": "Release feed did not look right."}
    # The highest version, not the first listed: the feed is in creation order, and a fix for an older
    # line can be published after a newer release. A tag that is not a plain version is not a release.
    releases_by_version = [(version, item) for item in releases
                           if isinstance(item, dict) and not item.get("draft") and not item.get("prerelease")
                           and (version := parse_version(item.get("tag_name"))) is not None]
    latest = max(releases_by_version, key=lambda pair: pair[0])[1] if releases_by_version else None
    assets = latest.get("assets", []) if latest else []
    if not isinstance(assets, list):
        assets = []

    def asset(name: str) -> dict | None:
        return next((a for a in assets if isinstance(a, dict) and a.get("name") == name
                     and github_link(a.get("browser_download_url"), ASSET_PATH)), None)

    installer = asset(installer_name())
    installer_sig = asset(installer_name() + SIGNATURE_SUFFIX)
    tag = str(latest.get("tag_name") or "") if latest else ""
    latest_version = tag.removeprefix("v")
    archive = asset(archive_name(latest_version)) if latest_version else None
    archive_sig = asset(archive_name(latest_version) + SIGNATURE_SUFFIX) if latest_version else None
    # A newer tag can exist before its installer is built, so "newer" and "downloadable" are
    # separate; conflating them made a broken release read back as "up to date".
    newer = bool(latest and (parse_version(latest_version) or (0, 0, 0))
                 > (parse_version(__version__) or (0, 0, 0)))

    def declared_size(a: dict | None) -> int | None:
        """The only thing bounding the download and the only thing that can say it arrived whole."""
        raw = a.get("size") if a else None
        return (raw if isinstance(raw, int) and not isinstance(raw, bool) and 0 < raw <= MAX_DOWNLOAD_BYTES
                else None)

    size = declared_size(installer)
    # A build that must verify only opens an installer that verifies: otherwise a release stripped of its
    # signed files would walk every copy onto the unchecked installer path.
    verifiable = not must_verify() or can_verify()
    available = (size is not None and newer and verifiable
                 and (installer_sig is not None or not must_verify()))
    archive_size = declared_size(archive)
    # Will pressing Update replace the app in place, or open an installer? A release missing either
    # half of the signed pair is not one to install seamlessly, and nor is a build that cannot
    # verify -- which is the old flow, not a failure.
    seamless = bool(newer and archive_size is not None and archive_sig
                    and archive.get("browser_download_url")
                    and archive_sig.get("browser_download_url")
                    and selfupdate.seamless_available())
    published = latest.get("published_at") if latest else None
    date = None
    if isinstance(published, str):
        try:
            date = dt.datetime.fromisoformat(published).date().isoformat()
        except ValueError:
            date = published
    return {"ok": True, "current": __version__, "newer": newer, "available": available,
            "verifiable": verifiable,
            "latest": latest_version or None,
            "url": installer.get("browser_download_url") if installer else None,
            "installer_signature_url": installer_sig.get("browser_download_url") if installer_sig else None,
            "release_url": _release_page(latest),
            "size": size, "size_label": _size_mb(size),
            "published_at": published, "published_date": date,
            "prerelease": bool(latest.get("prerelease")) if latest else False,
            "seamless": seamless,
            "archive_url": archive.get("browser_download_url") if seamless else None,
            "archive_size": archive_size if seamless else None,
            "signature_url": archive_sig.get("browser_download_url") if seamless else None}


def private_copy(root: Path, name: str, payload: bytes) -> Path:
    """Write `payload` to a file nobody else has had a chance to touch, and return it.

    Checking the bytes and then opening a path is two reads of that path, and anything that can write to
    it in between swaps what gets opened. So what is opened is never the download: it is a new file,
    created exclusively (`O_EXCL`, so not a file or link planted in advance) inside a folder `mkdtemp`
    has just made for this user alone, holding the very bytes that were checked."""
    root.mkdir(parents=True, exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix=INSTALLER_COPY_PREFIX, dir=root))
    path = folder / name
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(payload)
    return path


def remove_private_copies(root: Path) -> None:
    """The folders earlier presses opened installers from. Only swept when a new download starts: an
    installer may still be reading the latest one."""
    for old in root.glob(INSTALLER_COPY_PREFIX + "*") if root.is_dir() else ():
        shutil.rmtree(old, ignore_errors=True)


def read_checked(path: Path, version: str, sig: bytes | None) -> bytes:
    """The installer's bytes, read once, and verified when this build must verify. Raises Unverified
    when it does not check out (or cannot be checked), OSError when it cannot be read."""
    if path.stat().st_size > MAX_DOWNLOAD_BYTES:
        raise Unverified(f"{path} is larger than any installer")
    payload = path.read_bytes()
    if must_verify() and not (can_verify() and sig is not None and signature.verify_archive(
            version, payload, sig, domain=signature.INSTALLER_DOMAIN)):
        raise Unverified(f"{path} did not match its signature")
    return payload


def router(status: Status | dict | None = None, settings: Settings | None = None,
           quit_app=None) -> APIRouter:
    r = APIRouter(prefix="/api")
    # Server-side, not in React: a busy flag in the page is lost the moment the owner leaves
    # Settings, and the download it was describing is not.
    #
    # idle -> downloading -> ready, or idle -> downloading -> verifying -> staged -> installing.
    state: dict = {"state": "idle", "percent": 0, "received": 0, "total": None, "version": None,
                   "path": None, "error": None, "seamless": False, "busy": 0}
    # Held only so the running download is not garbage collected: asyncio keeps a bare task weakly.
    task: asyncio.Task | None = None
    staged: selfupdate.StagedUpdate | None = None
    # What the installer on disk was checked against, kept here rather than in `state` (which the page
    # sees) so the "Open installer" button can check it again before every open.
    checked: dict = {"version": None, "sig": None}

    def publish(**fields) -> None:
        state.update({"percent": 0, "received": 0, "total": None, "version": None, "path": None,
                      "error": None, "seamless": False, "busy": 0, **fields})
        if status is not None:
            status[PROGRESS_KEY] = dict(state)

    def in_flight() -> int:
        """How many fetches the worker has open, for the restart prompt."""
        progress = status.get("fetch_progress") if status is not None else None
        return len(progress) if isinstance(progress, list) else 0

    def updates_dir() -> Path:
        # Beside the log and the database rather than a temp folder the OS may sweep.
        root = settings.data_dir if settings is not None else Path.home()
        return root / "updates"

    def target_path() -> Path:
        return updates_dir() / installer_name()

    def remove_download(path: Path | None) -> None:
        if path is None:
            return
        try:
            path.unlink(missing_ok=True)
        except OSError:
            log.warning("could not remove the partial download at %s", path)

    def finished(t: asyncio.Task) -> None:
        """A task cancelled at shutdown raises past `except Exception`, and the "downloading" it
        leaves behind would refuse every retry for the rest of the session."""
        if not t.cancelled() and t.exception() is not None:
            log.error("update download ended badly", exc_info=t.exception())
        if state["state"] in ("downloading", "verifying"):
            publish(state="error", version=state["version"], seamless=state["seamless"],
                    error="The download stopped unexpectedly. Try again.")

    async def stream_to(url: str, total: int | None, target: Path, version: str,
                        seamless: bool = False) -> int:
        """Fetch `url` to `target`, publishing progress. Returns the byte count.

        Written to a .part name and renamed on the last byte: a truncated file at the real name gets
        called corrupt by Installer.app, or fails its signature check, with nothing pointing at the
        download. Raises ShortDownload, httpx.HTTPError or OSError -- the callers word each one."""
        received = 0
        partial = target.with_name(target.name + ".part")
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            with partial.open("wb") as fh:
                async with (
                    github_client(DOWNLOAD_HOSTS, DOWNLOAD_TIMEOUT) as client,
                    client.stream("GET", url) as res,
                ):
                    res.raise_for_status()
                    shown = -1
                    async for chunk in res.aiter_bytes():
                        received += len(chunk)
                        # Anything past the declared size is not the download.
                        if received > (total or MAX_DOWNLOAD_BYTES):
                            raise ShortDownload(f"body ran past the {total or MAX_DOWNLOAD_BYTES} bytes "
                                                "it may have")
                        fh.write(chunk)
                        percent = int(received * 100 / total) if total else 0
                        # Only on a whole-number move: per-chunk is thousands of SSE frames.
                        if percent != shown:
                            shown = percent
                            publish(state="downloading", percent=percent, received=received,
                                    total=total, version=version, seamless=seamless)
            # A connection closed cleanly partway is not an HTTP error, so counting is what catches it.
            if total and received != total:
                raise ShortDownload(f"got {received} of {total} bytes")
            partial.replace(target)
        except BaseException:
            remove_download(partial)
            raise
        return received

    async def fetch_signature(url: str) -> bytes | None:
        """The detached signature, or None for every way this can go wrong -- the caller refuses on
        all of them alike, because a signature that cannot be fetched is missing.

        Abandoned at the ceiling rather than fetched and then measured: `res.content` on an endless
        stream is an out-of-memory kill before there is anything to measure."""
        body = bytearray()
        try:
            async with (github_client(DOWNLOAD_HOSTS, DOWNLOAD_TIMEOUT) as client,
                        client.stream("GET", url) as res):
                res.raise_for_status()
                async for chunk in res.aiter_bytes():
                    body += chunk
                    if len(body) > MAX_SIGNATURE_BYTES:
                        log.warning("the signature asset at %s ran past %d bytes, so it is not a "
                                    "signature", url, MAX_SIGNATURE_BYTES)
                        return None
        except httpx.HTTPError:
            log.warning("could not fetch the update signature from %s", url, exc_info=True)
            return None
        return signature.decode_signature(bytes(body))

    async def fetch(url: str, total: int | None, target: Path, version: str, *, seamless: bool,
                    noun: str, written: str, save_error: str) -> int | None:
        """`stream_to`, with its three expected failures logged and published. None when one of them
        happened; anything else propagates to the caller."""
        try:
            return await stream_to(url, total, target, version, seamless=seamless)
        except ShortDownload:
            log.warning("%s from %s was incomplete", noun, url, exc_info=True)
            publish(state="error", version=version, seamless=seamless, error=INCOMPLETE)
        except httpx.HTTPError:
            log.warning("%s from %s failed", noun, url, exc_info=True)
            publish(state="error", version=version, seamless=seamless,
                    error="The download stopped before it finished. Check your connection and try again.")
        except OSError:
            log.warning("could not write %s to %s", written, target, exc_info=True)
            publish(state="error", version=version, seamless=seamless, error=save_error)
        return None

    async def signed(path: Path, version: str, sig_url: str | None) -> bool | None:
        """Whether the archive matches its detached signature; None when it cannot be read back. A pass
        records the bytes' hash, and `selfupdate.stage` unpacks nothing else."""
        sig = await fetch_signature(sig_url) if sig_url else None
        try:
            payload = path.read_bytes()
        except OSError:
            log.exception("could not read back the download at %s", path)
            return None
        return sig is not None and signature.verify_archive(version, payload, sig)

    async def download_archive(url: str, total: int | None, sig_url: str, version: str) -> None:
        """Fetch the bundle, prove it, stage it, and stop -- nothing is swapped until the owner
        answers. No failure here falls back to the installer."""
        nonlocal staged
        archive = updates_dir() / archive_name(version)
        received = await fetch(url, total, archive, version, seamless=True, noun="update archive",
                               written="the update archive",
                               save_error="Could not save the update. The disk may be full.")
        if received is None:
            return

        publish(state="verifying", percent=100, received=received, total=total, version=version,
                seamless=True)
        ok = await signed(archive, version, sig_url)
        if ok is None:
            remove_download(archive)
            publish(state="error", version=version, seamless=True,
                    error="Could not read the downloaded update. Try again.")
            return
        # Below this line the bytes get unpacked into /Applications and then executed. The version is
        # part of what was signed, so an older archive re-published under this tag fails here.
        if not ok:
            log.error("the update archive for %s did not match its signature; refusing to install it",
                      version)
            remove_download(archive)
            publish(state="error", version=version, seamless=True,
                    error="This update could not be verified, so Flackey did not install it. "
                          "Download it from the release page instead.")
            return

        try:
            # `stage` reads the archive once more and unpacks only bytes whose hash verified above.
            new = selfupdate.stage(archive, version, relaunch=True)
        except selfupdate.StagingError as exc:
            log.error("could not stage the verified update for %s: %s", version, exc)
            remove_download(archive)
            publish(state="error", version=version, seamless=True, error=str(exc))
            return
        except Exception:
            log.exception("unexpected failure staging the update for %s", version)
            remove_download(archive)
            publish(state="error", version=version, seamless=True,
                    error="The update could not be prepared. The log has the details.")
            return
        remove_download(archive)
        staged = new
        publish(state="staged", percent=100, received=received, total=total, version=version,
                path=str(new.path), seamless=True, busy=in_flight())

    def hand_over_to_installer(path: Path) -> None:
        """Open the installer, and on Windows get out of its way.

        Windows cannot replace a program file that is running, so Flackey quits once Setup.exe is up
        and the installer finds nothing of ours in use. Inno Setup's CloseApplications would ask the
        owner to close Flackey itself otherwise; this saves them the dialog. The Mac keeps running
        instead: Installer.app swaps a bundle that is not in use until the next launch, and the owner
        may want to finish what they were doing first.

        The quit is the same one the seamless restart uses (`ServerHandle.quit_app`), so it closes the
        window, stops the server and slskd, and lets the queue resume on the next start. Not reached
        when the installer did not open: the owner then still has a running app and a message."""
        open_installer(path)
        if sys.platform == "win32" and quit_app is not None:
            log.info("installer opened; quitting so it can replace Flackey")
            try:
                quit_app()
            except Exception:
                log.exception("could not close Flackey after opening the installer")

    async def download(url: str, total: int | None, version: str, sig_url: str | None) -> None:
        target = target_path()
        try:
            received = await fetch(url, total, target, version, seamless=False, noun="update download",
                                   written="the update",
                                   save_error="Could not save the installer. The disk may be full.")
        except Exception:
            # "downloading" is what blocks the next attempt, so it must never be the last word.
            log.exception("update download from %s failed unexpectedly", url)
            publish(state="error", version=version,
                    error="The download failed. The log has the details.")
            return
        if received is None:
            return
        sig = None
        if must_verify():
            # Installer.app runs the package's scripts as root once the owner types their password, so
            # it is checked like the seamless archive before anything opens it. Setup.exe on Windows is
            # held to the same rule: it is the release job that signs it, alongside the .pkg.
            publish(state="verifying", percent=100, received=received, total=total, version=version)
            sig = await fetch_signature(sig_url) if sig_url else None
        checked.update(version=version, sig=sig)
        await open_checked(target, version, received, total)

    async def open_checked(target: Path, version: str, received: int, total: int | None) -> None:
        """Check the installer on disk (again) and open a private copy of exactly the bytes checked.
        Both the first open and every "Open installer" press come through here."""
        try:
            payload = read_checked(target, version, checked["sig"])
        except Unverified:
            log.error("the installer for %s did not match its signature; refusing to open it", version)
            remove_download(target)
            publish(state="error", version=version,
                    error="This update could not be verified, so Flackey did not open it. "
                          "Download it from the release page instead.")
            return
        except OSError:
            log.exception("could not read back the installer at %s", target)
            remove_download(target)
            publish(state="error", version=version, error="Could not read the downloaded update. Try again.")
            return
        try:
            copy = private_copy(updates_dir(), installer_name(), payload)
        except OSError:
            copy = None
            log.warning("could not write a private copy of the installer", exc_info=True)
        publish(state="ready", percent=100, received=received, total=total, version=version,
                path=str(target))
        try:
            if copy is None:
                raise OSError("no private copy to open")
            hand_over_to_installer(copy)
        except Exception:
            log.warning("could not open the downloaded installer at %s", target, exc_info=True)
            # Still ready: the file is there and correct, only the last step needs a hand.
            publish(state="ready", percent=100, received=received, total=total, version=version,
                    path=str(target), error="The installer downloaded but would not open. "
                                            f"Open {installer_name()} from the app data folder to finish.")

    @r.get("/update")
    async def update() -> dict:
        return await latest_release()

    @r.get("/update/progress")
    async def progress() -> dict:
        return dict(state)

    @r.post("/update/install")
    async def install(request: Request) -> dict:
        nonlocal task
        from_the_app(request)
        # `state`, not the task handle, is what says a download is in flight: a second press should
        # read back the first one's progress, not start another 100MB fetch over the top of it.
        if state["state"] in ("downloading", "verifying"):
            return dict(state)
        if state["state"] == "staged" and staged is not None:
            return dict(state)
        if state["state"] == "installing":
            return dict(state)
        # Re-open what is already on disk rather than fetching it again. This is also what the
        # "Open installer" button presses, so the two paths stay one endpoint.
        if state["state"] == "ready" and state["path"] and Path(state["path"]).exists():
            await open_checked(Path(state["path"]), state["version"], state["received"], state["total"])
            return dict(state)
        # Claimed before the lookup below, which awaits: two presses that both got past the checks
        # while it ran would interleave their writes onto the same part-file.
        publish(state="downloading", version=state["version"])
        try:
            info = await latest_release()
        except Exception:
            publish(state="idle")
            raise
        if not info.get("ok"):
            publish(state="idle")
            raise HTTPException(502, info.get("error") or "Could not check for updates.")
        if not info["available"] and not info["seamless"]:
            publish(state="idle")
            if info["newer"] and not info.get("verifiable", True):
                raise HTTPException(409, "This copy of Flackey cannot check updates, so it will not install "
                                         "one. Download it from the release page instead.")
            raise HTTPException(409, f"Version {info['latest']} is out, but its installer isn't published yet."
                                if info["newer"] else "Flackey is already up to date.")
        # A new download: the copies earlier presses opened installers from are done with.
        remove_private_copies(updates_dir())
        # Chosen once, never as a recovery: a failed seamless attempt does not retry as an installer
        # download, because fetching a second payload with less checking answers nothing.
        if info["seamless"]:
            publish(state="downloading", total=info["archive_size"], version=info["latest"],
                    seamless=True)
            task = asyncio.create_task(download_archive(
                info["archive_url"], info["archive_size"], info["signature_url"], info["latest"]))
        else:
            publish(state="downloading", total=info["size"], version=info["latest"])
            task = asyncio.create_task(download(info["url"], info["size"], info["latest"],
                                                info.get("installer_signature_url")))
        task.add_done_callback(finished)
        return dict(state)

    @r.post("/update/restart")
    async def restart(request: Request) -> dict:
        """Commit the staged update and close the app. The quit is the commit, so this refuses
        unless something really is staged."""
        from_the_app(request)
        if state["state"] != "staged" or staged is None:
            raise HTTPException(409, "There is no update ready to install.")
        selfupdate.pending.arm(staged)
        publish(state="installing", percent=100, version=state["version"], seamless=True,
                path=state["path"])
        if quit_app is None:
            log.warning("nothing to quit: the staged update will install when Flackey next stops")
            return dict(state)
        try:
            quit_app()
        except Exception:
            # Deliberately still armed: quitting by hand runs the same `finally` that hands over, so
            # disarming here would make the message below a lie.
            log.exception("could not close the window to install the update")
            publish(state="staged", percent=100, version=state["version"], seamless=True,
                    path=state["path"], busy=in_flight(),
                    error="Flackey could not close itself. Quit Flackey and it will install on the way out.")
        return dict(state)

    @r.post("/update/release")
    async def release(request: Request) -> dict:
        from_the_app(request)
        info = await latest_release()
        url = info.get("release_url")
        if not url:
            raise HTTPException(502, info.get("error") or "Could not find the release page.")
        open_url(url)
        return {"ok": True, "url": url}

    return r
