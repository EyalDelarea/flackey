from __future__ import annotations

import asyncio
import logging
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi.responses import FileResponse

from .. import __version__
from ..config import FILING_FORMATS, Settings, save_settings
from ..export import PLAYLIST_DIR, playlist_names
from ..logsetup import LOG_FILE
from ..slskd_binary import SLSKD_VERSION, SlskdBinaryError, is_installed
from ..slskd_binary import install as install_slskd
from ..slskd_config import (
    SlskdConfigError,
    read_listen_port,
    read_password,
    read_username,
    write_credentials,
    write_share,
)
from ..slskd_process import SlskdProcess
from ..store import Store
from ..tools import tool_path
from ..youtube import ytdlp_available
from . import SETUP_DONE_KEY, Bundles, to_dict

log = logging.getLogger(__name__)

LIBRARY_LIMIT = 10_000


def reveal_in_finder(path: Path) -> None:
    if sys.platform == "darwin":
        # `-R` reveals the path in a Finder window, selected; a bare `open` on a directory instead
        # *launches* it, which is wrong for a `.app`/`.rbxml`/other bundle directory.
        cmd = ["open", "-R", str(path)]
    else:
        cmd = ["xdg-open", str(path if path.is_dir() else path.parent)]
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _inside(path: Path, roots: list[Path]) -> bool:
    try:
        resolved = path.resolve()
    except OSError:
        return False
    return any(resolved == r.resolve() or r.resolve() in resolved.parents for r in roots)


LOOPBACK = {"127.0.0.1", "localhost", "::1", "[::1]"}


def _host_port(url: str) -> tuple[str, int]:
    """The sidecar's host and port off its own URL rather than assumed, so this stays true for an owner
    who moved it -- including one pointing flackey at slskd on another machine."""
    rest = url.split("://", 1)[-1].split("/", 1)[0]
    host, _, tail = rest.rpartition(":")
    if not host:
        host, tail = rest, ""
    return (host or "127.0.0.1"), (int(tail) if tail.isdigit() else (443 if url.startswith("https") else 80))


def router(store: Store, settings: Settings, status: dict, bundles: Bundles,
           opener: Callable[[Path], None] = reveal_in_finder, link=None) -> APIRouter:
    r = APIRouter(prefix="/api")

    # Progress for the in-flight (if any) slskd install, scoped to this router instance -- a single
    # process serves one setup wizard at a time. Never holds anything from slskd.yml/the API key; the
    # only strings it can carry are SlskdBinaryError messages, which by construction never dump bytes
    # or secrets (see slskd_binary.py).
    slskd_install_state: dict = {"state": "idle", "done": 0, "total": 0, "error": None}

    # Rescans started after a library move, held here for as long as they run: the event loop keeps only
    # a weak reference to a task, so one nobody else holds can be collected half way through.
    rescans: set[asyncio.Task] = set()

    def _install_slskd_in_background(data_dir: Path) -> None:
        """Runs on FastAPI's background threadpool (BackgroundTasks), so the tens-of-seconds
        download+verify+extract never blocks the event loop or the POST response."""
        def on_progress(done: int, total: int) -> None:
            slskd_install_state["done"] = done
            slskd_install_state["total"] = total
            if total and done >= total:
                slskd_install_state["state"] = "extracting"

        try:
            install_slskd(data_dir, on_progress=on_progress)
        except SlskdBinaryError as e:
            slskd_install_state.update(state="error", error=str(e))
            log.warning("slskd install failed: %s", e)
        else:
            slskd_install_state.update(state="done", error=None)

    @r.get("/rejections/{rjid}/spectrogram.png")
    async def spectrogram(rjid: int):
        try:
            rj = store.get_rejection(rjid)
        except KeyError:
            raise HTTPException(404, "not found")
        if not rj.spectrogram_path or not Path(rj.spectrogram_path).exists():
            raise HTTPException(404, "no spectrogram")
        return FileResponse(rj.spectrogram_path, media_type="image/png")

    @r.get("/library")
    async def library(q: str | None = None, playlist_id: int | None = None) -> list:
        return [bundles.track(t.id) for t in store.list_tracks(search=q, limit=LIBRARY_LIMIT, playlist_id=playlist_id)]

    @r.get("/playlists")
    async def playlists() -> list:
        pls = store.list_playlists()
        names = playlist_names(pls)
        out = []
        for p in pls:
            d = to_dict(p)
            d["file"] = str(settings.library_root / PLAYLIST_DIR / f"{names[p.id]}.m3u8")
            out.append(d)
        return out

    @r.get("/stats")
    async def stats() -> dict:
        s = store.stats()
        s["playlists"] = len(store.list_playlists())
        s["library_root"] = str(settings.library_root)
        s["playlist_dir"] = str(settings.library_root / PLAYLIST_DIR)
        return s

    def _ports(settings: Settings) -> dict:
        sidecar_host, sidecar_port = _host_port(settings.slskd_url)
        return {"app": {"port": settings.web_port, "host": settings.web_host,
                        "public": settings.web_host not in LOOPBACK},
                "sidecar": {"port": sidecar_port, "host": sidecar_host, "public": sidecar_host not in LOOPBACK},
                # Bound to every interface by the Soulseek protocol itself: peers dial in to download from
                # the shared library, so this one cannot be loopback and is always reported as public.
                "soulseek_listen": {"port": read_listen_port(settings.data_dir), "host": "0.0.0.0",
                                    "public": True}}

    def settings_out() -> dict:
        # Ports, so the owner can answer "what is this app opening on my machine?" without reading the
        # config: `app` and `sidecar` are bound to 127.0.0.1 and answer only from this machine;
        # `soulseek_listen` is the one real inbound port, because peers connect to it to download from
        # the shared library -- that is how Soulseek works, and it cannot be loopback.
        return {"library_root": str(settings.library_root), "data_dir": str(settings.data_dir),
                "version": __version__, "telegram_configured": settings.telegram_configured,
                "log_path": str(settings.data_dir / LOG_FILE),
                "soulseek_enabled": settings.soulseek_enabled, "slskd_url": settings.slskd_url,
                "slskd_downloads_dir": str(settings.slskd_downloads),
                "lossless_filing_format": settings.lossless_filing_format,
                "ports": _ports(settings),
                "ranking": {"max_picks": settings.lossless_max_picks,
                            "duration_tolerance_s": settings.lossless_duration_tolerance_s,
                            "title_ratio": settings.lossless_title_ratio,
                            "require_artist": settings.lossless_require_artist,
                            "max_queue": settings.lossless_max_queue,
                            "fingerprint_min": settings.lossless_fingerprint_min},
                "filing_formats": list(FILING_FORMATS)}

    @r.get("/settings")
    async def get_settings() -> dict:
        return settings_out()

    @r.put("/settings")
    async def put_settings(body: dict) -> dict:
        raw = (body.get("library_root") or "").strip()
        path = Path(raw).expanduser() if raw else None
        if path is None or not path.is_absolute():
            raise HTTPException(400, "Choose a folder by its full path, for example ~/Music/DJ Library.")
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise HTTPException(400, f"That folder cannot be used: {e.strerror or e}")
        extra: dict = {}
        if "lossless_filing_format" in body:
            if body["lossless_filing_format"] not in FILING_FORMATS:
                raise HTTPException(400, f"Filing format must be one of {', '.join(FILING_FORMATS)}.")
            extra["lossless_filing_format"] = body["lossless_filing_format"]
        if body.get("slskd_url"):
            extra["slskd_url"] = str(body["slskd_url"]).rstrip("/")
        if body.get("slskd_api_key"):
            extra["slskd_api_key"] = str(body["slskd_api_key"]).strip()
        previous = settings.library_root
        save_settings(settings, library_root=path, **extra)
        # The share follows the folder: leaving it pointed at the old path would offer peers a folder
        # the owner no longer fills, and share nothing of the one they do.
        if path != previous:
            try:
                moved = write_share(settings.data_dir, path, previous=previous)
            except SlskdConfigError as e:
                log.warning("library folder changed but the Soulseek share was not updated: %s", e)
                moved = False
            if moved and link is not None:
                task = asyncio.create_task(link.rescan_shares())
                rescans.add(task)
                task.add_done_callback(rescans.discard)
        return settings_out()

    @r.post("/reveal")
    async def reveal(body: dict) -> dict:
        path = Path(body.get("path") or "")
        if not path.is_absolute() or not _inside(path, [settings.library_root, settings.data_dir]):
            raise HTTPException(400, "only files inside the library or app data folder can be shown")
        if not path.exists():
            raise HTTPException(404, "that file is no longer there")
        opener(path)
        return {"ok": True}

    @r.get("/tools")
    async def tools() -> dict:
        return {"ffmpeg": tool_path("ffmpeg") is not None, "ffprobe": tool_path("ffprobe") is not None,
                "yt_dlp": ytdlp_available(), "fpcalc": tool_path("fpcalc") is not None}

    @r.post("/setup/done")
    async def setup_done() -> dict:
        store.set_setting(SETUP_DONE_KEY, "1")
        status["setup_done"] = True
        return {"setup_done": True}

    @r.post("/setup/reset")
    async def setup_reset() -> dict:
        store.set_setting(SETUP_DONE_KEY, "0")
        status["setup_done"] = False
        return {"setup_done": False}

    @r.get("/setup/soulseek")
    async def get_soulseek_setup() -> dict:
        username = read_username(settings.data_dir)
        return {"configured": username is not None, "username": username}

    @r.get("/setup/soulseek/password")
    async def get_soulseek_password() -> dict:
        """Hand the owner back the Soulseek password flackey generated for them.

        The only route in flackey that returns a secret, and it is here because Soulseek has no password
        reset: the name is bound to the password it was claimed with, and an owner who cannot produce that
        string cannot sign in from anywhere else, ever. Keeping it unreadable would not protect them from
        anything -- the file is 0600 under their own account, so anything that can call this can already
        read it -- it would only make the account unrecoverable. Deliberately its own path rather than a
        field on GET /setup/soulseek, which the wizard polls: a secret must not ride along on a poll."""
        password = read_password(settings.data_dir)
        if password is None:
            raise HTTPException(404, "No Soulseek password is saved.")
        return {"username": read_username(settings.data_dir), "password": password}

    @r.post("/setup/soulseek")
    async def post_soulseek_setup(body: dict) -> dict:
        """Write the given Soulseek credentials into the managed slskd.yml and keep the resulting API
        key in memory only -- never handed to `save_settings`, so it still lives in exactly one place,
        the 0600 slskd.yml. `restart_required` is always true: flackey builds its provider list and
        starts the slskd sidecar (if installed) once at startup (`build_providers` / `SlskdProcess.start`
        in app.py, around line 120) and does not restart either on a credential change, so credentials
        written here take effect the next time flackey starts, not on this already-running process."""
        username = str(body.get("username") or "")
        password = str(body.get("password") or "")
        try:
            key = write_credentials(settings.data_dir, username, password,
                                    library_root=settings.library_root)
        except SlskdConfigError as e:
            raise HTTPException(400, str(e))
        settings.slskd_api_key = key
        if link is None:
            return {"ok": True, "restart_required": True, "connecting": False}
        # With a link present the credentials take effect now: it restarts the sidecar under them and
        # rebuilds the worker's providers. Signing in takes tens of seconds, so the answer arrives from
        # GET /setup/soulseek/status rather than from this response.
        link.start_connect()
        return {"ok": True, "restart_required": False, "connecting": True}

    @r.post("/setup/soulseek/connect")
    async def post_soulseek_connect() -> dict:
        """Sign in again with the credentials already saved. The status line is not a place to be stuck:
        if it says "signing in" and stays there, this is the action that does something about it."""
        if link is None:
            raise HTTPException(409, "Soulseek can't be reconnected on this build. Restart flackey.")
        if not settings.soulseek_enabled:
            raise HTTPException(409, "No Soulseek account is saved yet.")
        return link.start_connect()

    @r.get("/setup/soulseek/status")
    async def get_soulseek_status() -> dict:
        """Where the sign-in got to. Soulseek claims a username at first login, so "connected" is also
        the only confirmation that a new account now exists."""
        if link is None:
            return {"state": "idle", "username": None, "error": None}
        return dict(link.state)

    @r.get("/setup/slskd")
    async def get_slskd_setup() -> dict:
        # `running` is a live health probe -- true whenever *something* answers on slskd_url, whether
        # it's a copy this process spawned or one the owner started by hand. That's deliberate: it
        # matches SlskdProcess.start()'s own "don't spawn a second one" check, and it's a different
        # question from SlskdProcess.running (which means "I own a live subprocess").
        process = SlskdProcess(settings.data_dir, settings.slskd_url, settings.slskd_api_key or "")
        running = await process.probe()
        return {"installed": is_installed(settings.data_dir), "running": running, "version": SLSKD_VERSION}

    @r.post("/setup/slskd")
    async def post_slskd_setup(background_tasks: BackgroundTasks) -> dict:
        """Starts the install and returns immediately -- the download+extract takes tens of seconds, so
        it never runs on the request; poll GET /setup/slskd/progress for status."""
        if is_installed(settings.data_dir):
            slskd_install_state.update(state="done", done=0, total=0, error=None)
            return {"state": slskd_install_state["state"]}
        slskd_install_state.update(state="downloading", done=0, total=0, error=None)
        background_tasks.add_task(_install_slskd_in_background, settings.data_dir)
        return {"state": slskd_install_state["state"]}

    @r.get("/setup/slskd/progress")
    async def get_slskd_progress() -> dict:
        return dict(slskd_install_state)

    return r
