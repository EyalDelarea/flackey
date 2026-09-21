from __future__ import annotations

import asyncio
import datetime as dt
import logging
import re
import subprocess
import webbrowser
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException, Request

from .. import __version__
from ..config import Settings
from ..events import Status
from ..selfupdate import install as selfupdate
from ..selfupdate import signature

log = logging.getLogger(__name__)

RELEASES_URL = "https://api.github.com/repos/EyalDelarea/flackey/releases?per_page=10"
INSTALLER_NAME = "Flackey.pkg"
SIGNATURE_SUFFIX = ".sig"
MAX_SIGNATURE_BYTES = 4096
# Per read, not for the whole transfer: a single deadline would abort a slow but healthy download.
DOWNLOAD_TIMEOUT = httpx.Timeout(30.0, connect=10.0)
PROGRESS_KEY = "update_download"
INCOMPLETE = "The download arrived incomplete. Check your connection and try again."


APP_HEADER = "x-flackey-app"


def from_the_app(request: Request) -> None:
    """Refuse a press that some other page made the browser send.

    Flackey listens on loopback, and these endpoints are wanted for what they *do*, which CORS does
    not hide. Inventing a header forces a preflight, which only `create_app`'s origins satisfy. Not
    an `Origin` check: a WKWebView on http:// and a dev-server proxy make that unguessable here."""
    if not request.headers.get(APP_HEADER):
        raise HTTPException(403, "That request did not come from Flackey.")


class ShortDownload(Exception):
    """The body did not match the size the release declared -- too few bytes or too many."""


def _version_tuple(v: str) -> tuple[int, int, int]:
    m = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", v.strip())
    if not m:
        return (0, 0, 0)
    return tuple(int(p) for p in m.groups())


def _size_mb(size: int | None) -> str | None:
    return f"{size / 1e6:.1f} MB" if size else None


def archive_name(version: str) -> str:
    """Built from the version, not matched by pattern, so somebody else's zip cannot be mistaken
    for this one."""
    return f"Flackey-{version}.zip"


def open_installer(path: Path) -> None:
    subprocess.Popen(["open", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def open_url(url: str) -> None:
    """`window.open` from the page does nothing inside pywebview."""
    webbrowser.open(url)


async def latest_release() -> dict:
    """The download runs this lookup again for itself: the client never says what to fetch, so a
    stale page cannot talk the app into downloading something else."""
    try:
        async with httpx.AsyncClient(timeout=5, follow_redirects=True) as client:
            res = await client.get(RELEASES_URL)
            res.raise_for_status()
        releases = res.json()
    except (httpx.HTTPError, ValueError):
        return {"ok": False, "current": __version__, "newer": False, "available": False,
                "error": "Could not check for updates."}
    if not isinstance(releases, list):
        return {"ok": False, "current": __version__, "newer": False, "available": False,
                "error": "Release feed did not look right."}
    latest = next((item for item in releases
                    if isinstance(item, dict) and not item.get("draft") and not item.get("prerelease")), None)
    assets = latest.get("assets", []) if latest else []

    def asset(name: str) -> dict | None:
        return next((a for a in assets if isinstance(a, dict) and a.get("name") == name), None)

    installer = asset(INSTALLER_NAME)
    tag = str(latest.get("tag_name") or "") if latest else ""
    latest_version = tag.removeprefix("v")
    archive = asset(archive_name(latest_version)) if latest_version else None
    archive_sig = asset(archive_name(latest_version) + SIGNATURE_SUFFIX) if latest_version else None
    # A newer tag can exist before its installer is built, so "newer" and "downloadable" are
    # separate; conflating them made a broken release read back as "up to date".
    newer = bool(latest and _version_tuple(latest_version) > _version_tuple(__version__))
    def declared_size(a: dict | None) -> int | None:
        """The only thing bounding the download and the only thing that can say it arrived whole."""
        raw = a.get("size") if a else None
        return raw if isinstance(raw, int) and not isinstance(raw, bool) and raw > 0 else None

    size = declared_size(installer)
    available = size is not None and newer
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
            "latest": latest_version or None,
            "url": installer.get("browser_download_url") if installer else None,
            "release_url": latest.get("html_url") if latest else None,
            "size": size, "size_label": _size_mb(size),
            "published_at": published, "published_date": date,
            "prerelease": bool(latest.get("prerelease")) if latest else False,
            "seamless": seamless,
            "archive_url": archive.get("browser_download_url") if seamless else None,
            "archive_size": archive_size if seamless else None,
            "signature_url": archive_sig.get("browser_download_url") if seamless else None}


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
        return updates_dir() / INSTALLER_NAME

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
                async with httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT, follow_redirects=True) as client:
                    async with client.stream("GET", url) as res:
                        res.raise_for_status()
                        shown = -1
                        async for chunk in res.aiter_bytes():
                            received += len(chunk)
                            # Anything past the declared size is not the download.
                            if total and received > total:
                                raise ShortDownload(f"body ran past the {total} bytes the release declared")
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
            async with (httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT, follow_redirects=True) as client,
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

    async def download_archive(url: str, total: int | None, sig_url: str, version: str) -> None:
        """Fetch the bundle, prove it, stage it, and stop -- nothing is swapped until the owner
        answers. No failure here falls back to the installer."""
        nonlocal staged
        archive = updates_dir() / archive_name(version)
        try:
            received = await stream_to(url, total, archive, version, seamless=True)
        except ShortDownload:
            log.warning("update archive from %s was incomplete", url, exc_info=True)
            publish(state="error", version=version, seamless=True, error=INCOMPLETE)
            return
        except httpx.HTTPError:
            log.warning("update archive from %s failed", url, exc_info=True)
            publish(state="error", version=version, seamless=True,
                    error="The download stopped before it finished. Check your connection and try again.")
            return
        except OSError:
            log.warning("could not write the update archive to %s", archive, exc_info=True)
            publish(state="error", version=version, seamless=True,
                    error="Could not save the update. The disk may be full.")
            return

        publish(state="verifying", percent=100, received=received, total=total, version=version,
                seamless=True)
        sig = await fetch_signature(sig_url)
        try:
            payload = archive.read_bytes()
        except OSError:
            log.exception("could not read back the downloaded archive at %s", archive)
            remove_download(archive)
            publish(state="error", version=version, seamless=True,
                    error="Could not read the downloaded update. Try again.")
            return
        # Below this line the bytes get unpacked into /Applications and then executed. The version is
        # part of what was signed, so an older archive re-published under this tag fails here.
        if sig is None or not signature.verify_archive(version, payload, sig):
            log.error("the update archive for %s did not match its signature; refusing to install it",
                      version)
            remove_download(archive)
            publish(state="error", version=version, seamless=True,
                    error="This update could not be verified, so Flackey did not install it. "
                          "Download it from the release page instead.")
            return
        del payload

        try:
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

    async def download(url: str, total: int | None, version: str) -> None:
        received = 0
        target = target_path()
        try:
            received = await stream_to(url, total, target, version)
        except ShortDownload:
            log.warning("update download from %s was incomplete", url, exc_info=True)
            publish(state="error", version=version, error=INCOMPLETE)
            return
        except httpx.HTTPError:
            log.warning("update download from %s failed", url, exc_info=True)
            publish(state="error", version=version,
                    error="The download stopped before it finished. Check your connection and try again.")
            return
        except OSError:
            log.warning("could not write the update to %s", target, exc_info=True)
            publish(state="error", version=version,
                    error="Could not save the installer. The disk may be full.")
            return
        except Exception:
            # "downloading" is what blocks the next attempt, so it must never be the last word.
            log.exception("update download from %s failed unexpectedly", url)
            publish(state="error", version=version,
                    error="The download failed. The log has the details.")
            return
        publish(state="ready", percent=100, received=received, total=total, version=version,
                path=str(target))
        try:
            open_installer(target)
        except Exception:
            log.warning("could not open the downloaded installer at %s", target, exc_info=True)
            # Still ready: the file is there and correct, only the last step needs a hand.
            publish(state="ready", percent=100, received=received, total=total, version=version,
                    path=str(target), error="The installer downloaded but would not open. "
                                            "Open Flackey.pkg from the app data folder to finish.")

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
            open_installer(Path(state["path"]))
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
            raise HTTPException(409, f"Version {info['latest']} is out, but its installer isn't published yet."
                                if info["newer"] else "Flackey is already up to date.")
        # Chosen once, never as a recovery: a failed seamless attempt does not retry as an installer
        # download, because fetching a second payload with less checking answers nothing.
        if info["seamless"]:
            publish(state="downloading", total=info["archive_size"], version=info["latest"],
                    seamless=True)
            task = asyncio.create_task(download_archive(
                info["archive_url"], info["archive_size"], info["signature_url"], info["latest"]))
        else:
            publish(state="downloading", total=info["size"], version=info["latest"])
            task = asyncio.create_task(download(info["url"], info["size"], info["latest"]))
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
