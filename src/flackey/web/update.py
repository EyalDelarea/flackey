from __future__ import annotations

import asyncio
import datetime as dt
import logging
import re
import subprocess
import webbrowser
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException

from .. import __version__
from ..config import Settings
from ..events import Status

log = logging.getLogger(__name__)

RELEASES_URL = "https://api.github.com/repos/EyalDelarea/flackey/releases?per_page=10"
INSTALLER_NAME = "Flackey.pkg"
# The installer is ~100MB over a link that can stall for a moment without being dead, so the budget is
# per read rather than for the whole transfer: a single deadline aborts a slow but healthy download.
DOWNLOAD_TIMEOUT = httpx.Timeout(30.0, connect=10.0)
# Where the download's state lives on the shared status dict, which fans every change out over SSE --
# the page cannot poll its way through a download it may navigate away from and come back to.
PROGRESS_KEY = "update_download"
INCOMPLETE = "The download arrived incomplete. Check your connection and try again."


class ShortDownload(Exception):
    """The body did not match the size the release declared -- too few bytes or too many. Its own type
    because it is neither an HTTP error nor a disk error, and saying "check your connection" about a
    stream that ended early is the honest reading of both."""


def _version_tuple(v: str) -> tuple[int, int, int]:
    m = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", v.strip())
    if not m:
        return (0, 0, 0)
    return tuple(int(p) for p in m.groups())


def _size_mb(size: int | None) -> str | None:
    return f"{size / 1e6:.1f} MB" if size else None


def open_installer(path: Path) -> None:
    """Hand the .pkg to Installer.app. A plain `open`, not the `-R` that reveals a file in Finder: the
    owner asked to update, so landing them in the installer is the point -- a selected file in a Finder
    window is one more thing to double-click and one more place to lose them."""
    subprocess.Popen(["open", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def open_url(url: str) -> None:
    """Open a page in the real browser. `window.open` from the page does nothing inside pywebview, which
    is why every link the app offered was a dead click."""
    webbrowser.open(url)


async def latest_release() -> dict:
    """The answer `GET /api/update` returns, and the same lookup the download runs again for itself --
    the client never says what to fetch, so a page that has been sitting open on a stale payload cannot
    talk the app into downloading and opening something else."""
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
    # Stable only: a prerelease tag (every 0.1.x release so far) should never trigger an update prompt.
    latest = next((item for item in releases
                    if isinstance(item, dict) and not item.get("draft") and not item.get("prerelease")), None)
    assets = latest.get("assets", []) if latest else []
    installer = next((a for a in assets if isinstance(a, dict) and a.get("name") == INSTALLER_NAME), None)
    tag = str(latest.get("tag_name") or "") if latest else ""
    latest_version = tag.removeprefix("v")
    # A newer tag can exist before its installer is built (e.g. a release job that failed partway
    # through), so "a newer version exists" and "there is something to download" are tracked
    # separately -- conflating them is what made a broken release read back as "up to date".
    newer = bool(latest and _version_tuple(latest_version) > _version_tuple(__version__))
    available = bool(installer) and newer
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
            "size": installer.get("size") if installer else None,
            "size_label": _size_mb(installer.get("size") if installer else None),
            "published_at": published, "published_date": date,
            "prerelease": bool(latest.get("prerelease")) if latest else False}


def router(status: Status | dict | None = None, settings: Settings | None = None) -> APIRouter:
    r = APIRouter(prefix="/api")
    # One download at a time, tracked on the server rather than in the page: a busy flag in React is lost
    # the moment the owner leaves Settings, and the download it was describing is not.
    state: dict = {"state": "idle", "percent": 0, "received": 0, "total": None, "version": None,
                   "path": None, "error": None}
    # Held only so the running download is not garbage collected mid-flight -- asyncio keeps nothing but a
    # weak reference to a bare task. Whether one is in progress is answered by `state`, not by this.
    task: asyncio.Task | None = None

    def publish(**fields) -> None:
        state.update({"percent": 0, "received": 0, "total": None, "version": None, "path": None,
                      "error": None, **fields})
        if status is not None:
            status[PROGRESS_KEY] = dict(state)

    def target_path() -> Path:
        # Beside the log and the database rather than in a temp folder the OS may sweep: "Show in Finder"
        # already points here, so a download that finished but would not open is somewhere findable.
        root = settings.data_dir if settings is not None else Path.home()
        return root / "updates" / INSTALLER_NAME

    def discard(path: Path | None) -> None:
        if path is None:
            return
        try:
            path.unlink(missing_ok=True)
        except OSError:
            log.warning("could not remove the partial download at %s", path)

    def finished(t: asyncio.Task) -> None:
        """Last line of defence for the one state that must never stand: a task cancelled at shutdown
        raises straight past `except Exception`, and a "downloading" left behind by it would refuse every
        retry for the rest of the session."""
        if not t.cancelled() and t.exception() is not None:
            log.error("update download ended badly", exc_info=t.exception())
        if state["state"] == "downloading":
            publish(state="error", version=state["version"],
                    error="The download stopped unexpectedly. Try again.")

    async def download(url: str, total: int | None, version: str) -> None:
        received = 0
        # Bound before the try so the handlers below can clean up after a failure that happened before
        # there was anything to clean up.
        partial: Path | None = None
        try:
            target = target_path()
            # Written under a .part name and renamed only once the last byte lands. A truncated file left
            # at the real name is the one failure worth avoiding outright: macOS opens it, Installer.app
            # calls it corrupt, and nothing says the download was the thing that went wrong.
            partial = target.with_name(target.name + ".part")
            target.parent.mkdir(parents=True, exist_ok=True)
            with partial.open("wb") as fh:
                async with httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT, follow_redirects=True) as client:
                    async with client.stream("GET", url) as res:
                        res.raise_for_status()
                        shown = -1
                        async for chunk in res.aiter_bytes():
                            received += len(chunk)
                            # The release says how big the installer is, so anything past that is not the
                            # installer. Stop at the ceiling rather than writing a stranger's stream to
                            # disk until it runs out of room.
                            if total and received > total:
                                raise ShortDownload(f"body ran past the {total} bytes the release declared")
                            fh.write(chunk)
                            percent = int(received * 100 / total) if total else 0
                            # Only when the whole number moves: a chunk-by-chunk publish is a few thousand
                            # SSE frames for one download, and the page cannot draw them anyway.
                            if percent != shown:
                                shown = percent
                                publish(state="downloading", percent=percent, received=received,
                                        total=total, version=version)
            # A connection closed cleanly partway is not an HTTP error and nothing upstream objects to it,
            # so counting the bytes is the only thing standing between a half-installer and Installer.app
            # calling Flackey corrupt.
            if total and received != total:
                raise ShortDownload(f"got {received} of {total} bytes")
            partial.replace(target)
        except ShortDownload:
            log.warning("update download from %s was incomplete", url, exc_info=True)
            discard(partial)
            publish(state="error", version=version, error=INCOMPLETE)
            return
        except httpx.HTTPError:
            log.warning("update download from %s failed", url, exc_info=True)
            discard(partial)
            publish(state="error", version=version,
                    error="The download stopped before it finished. Check your connection and try again.")
            return
        except OSError:
            log.warning("could not write the update to %s", partial, exc_info=True)
            discard(partial)
            publish(state="error", version=version,
                    error="Could not save the installer. The disk may be full.")
            return
        except Exception:
            # Nothing else is expected here, but "downloading" is the one state that must never be the
            # last word: it is what blocks the next attempt, so an unforeseen failure that left it
            # standing would wedge the button at 0% with no way back.
            log.exception("update download from %s failed unexpectedly", url)
            discard(partial)
            publish(state="error", version=version,
                    error="The download failed. The log has the details.")
            return
        publish(state="ready", percent=100, received=received, total=total, version=version,
                path=str(target))
        try:
            open_installer(target)
        except Exception:
            log.warning("could not open the downloaded installer at %s", target, exc_info=True)
            # Still ready, because the file is there and correct -- only the last step needs a hand.
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
    async def install() -> dict:
        nonlocal task
        # The state, not the task handle, is what says a download is in flight -- the page may press this
        # from two windows, and the second press should read back the first one's progress, not start a
        # second 100MB fetch over the top of it.
        if state["state"] == "downloading":
            return dict(state)
        # Already downloaded and still on disk: re-open it rather than fetching 100MB a second time. This
        # is what the "Open installer" button presses, so the two paths stay one endpoint.
        if state["state"] == "ready" and state["path"] and Path(state["path"]).exists():
            open_installer(Path(state["path"]))
            return dict(state)
        # Claimed here, before the lookup below -- which awaits. Two presses that both got past the check
        # while it was running would each start a download onto the same part-file, interleave their
        # writes, and hand whatever survived to Installer.app. The claim is dropped again on every path
        # that does not go on to start one.
        publish(state="downloading", version=state["version"])
        try:
            info = await latest_release()
        except Exception:
            publish(state="idle")
            raise
        if not info.get("ok"):
            publish(state="idle")
            raise HTTPException(502, info.get("error") or "Could not check for updates.")
        if not info["available"]:
            publish(state="idle")
            raise HTTPException(409, f"Version {info['latest']} is out, but its installer isn't published yet."
                                if info["newer"] else "Flackey is already up to date.")
        publish(state="downloading", total=info["size"], version=info["latest"])
        task = asyncio.create_task(download(info["url"], info["size"], info["latest"]))
        task.add_done_callback(finished)
        return dict(state)

    @r.post("/update/release")
    async def release() -> dict:
        info = await latest_release()
        url = info.get("release_url")
        if not url:
            raise HTTPException(502, info.get("error") or "Could not find the release page.")
        open_url(url)
        return {"ok": True, "url": url}

    return r
