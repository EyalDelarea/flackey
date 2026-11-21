from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
from collections.abc import Callable, Iterator
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import FileResponse

from .. import __version__, tidy
from ..config import (
    EXTENDED_MIXES,
    FILING_FORMATS,
    LIBRARY_LAYOUTS,
    Settings,
    default_filing_format,
    save_settings,
)
from ..export import (
    PLAYLIST_DIR,
    filed_at,
    mark_imported,
    new_playlist_path,
    new_tracks,
    playlist_names,
    write_new_playlist,
    write_playlists,
)
from ..library import library_reachable, missing_tracks, prune_missing_tracks
from ..logsetup import LOG_FILE
from ..slskd_binary import SLSKD_VERSION, SlskdBinaryError, is_installed
from ..slskd_binary import install as install_slskd
from ..slskd_config import (
    SlskdConfigError,
    is_network_path,
    read_listen_port,
    read_password,
    read_username,
    read_web_credentials,
    unsafe_share_reason,
    write_credentials,
    write_share,
)
from ..slskd_process import SlskdProcess
from ..store import Store
from ..tools import no_window, tool_path
from ..trust import flagged, measure, receipt, record, spectrogram_of
from ..youtube import ytdlp_available
from . import SETUP_DONE_KEY, Bundles, to_dict
from .guard import from_the_app

log = logging.getLogger(__name__)

LIBRARY_LIMIT = 10_000
# What a browser can be handed and actually play. Every extension the Deezer source can land (see
# `EXT_BY_MIME` in source/deezer_bot.py) is here, which is the set `rejected_audio` ever sees; anything
# else is refused rather than served under a guessed type.
AUDIO_TYPES = {".mp3": "audio/mpeg", ".flac": "audio/flac", ".wav": "audio/wav",
               ".m4a": "audio/mp4", ".aiff": "audio/aiff", ".aif": "audio/aiff", ".ogg": "audio/ogg"}


def reveal_path(path: Path) -> None:
    """Show `path` in the system's file manager: Finder on macOS, File Explorer on Windows, whatever
    `xdg-open` picks elsewhere."""
    if sys.platform == "win32":
        if path.is_dir():
            # A folder is opened, the way double-clicking it would. `os.startfile` hands it to the shell
            # directly: no child process of ours, so no console to hide.
            os.startfile(path)  # a path this app built, never one taken from the request
            return
        # A file is shown selected in its folder. A command *line*, not an argument list: Explorer parses
        # its own command line rather than argv, and it wants the quotes after the comma --
        # `/select,"C:\a b\x.flac"`. The list form would make Popen quote the whole argument
        # (`"/select,C:\a b\x.flac"`) at the first space, which Explorer answers by opening Documents.
        # A Windows path cannot contain `"`, so nothing in it can close the quote early. Explorer
        # answers 1 even when it worked, so the exit code says nothing and is not read.
        cmd = f'explorer /select,"{path}"'
    elif sys.platform == "darwin":
        # `-R` reveals the path in a Finder window, selected; a bare `open` on a directory instead
        # *launches* it, which is wrong for a `.app`/`.rbxml`/other bundle directory.
        cmd = ["open", "-R", str(path)]
    else:
        cmd = ["xdg-open", str(path if path.is_dir() else path.parent)]
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **no_window())


def _revealable(settings: Settings, store: Store) -> Iterator[Path]:
    """Every folder and file the UI has a "show in Finder" button for, each one built here out of settings
    and the database. `/reveal` matches the asked-for name against this list and then opens the *listed*
    path, so a name that came in over HTTP is only ever compared as a string -- it never reaches the
    filesystem, and there is no containment check to get subtly wrong."""
    yield settings.library_root
    yield settings.data_dir
    yield settings.data_dir / LOG_FILE
    yield new_playlist_path(settings.library_root)
    playlists = store.list_playlists()
    names = playlist_names(playlists)
    for pl in playlists:
        yield settings.library_root / PLAYLIST_DIR / f"{names[pl.id]}.m3u8"


def _contained(stored: str | Path | None, directory: Path) -> Path | None:
    """The file a database row points at, resolved, when it exists inside `directory`; else None. Both
    sides are resolved before comparing, so neither `..` in the stored path nor a symlink inside the
    folder can reach a file outside it."""
    if not stored:
        return None
    path = Path(stored).resolve()
    if not path.is_relative_to(directory.resolve()) or not path.is_file():
        return None
    return path


LOOPBACK = {"127.0.0.1", "localhost", "::1", "[::1]"}


def _host_port(url: str) -> tuple[str, int]:
    """The sidecar's host and port off its own URL rather than assumed, so this stays true for an owner
    who moved it -- including one pointing flackey at slskd on another machine."""
    rest = url.split("://", 1)[-1].split("/", 1)[0]
    host, _, tail = rest.rpartition(":")
    if not host:
        host, tail = rest, ""
    return (host or "127.0.0.1"), (int(tail) if tail.isdigit() else (443 if url.startswith("https") else 80))


def slskd_url_problem(url: str) -> str | None:
    """Why the slskd address must not be saved, or None. Every request to it carries the slskd API key,
    so plain http is allowed only to this machine; another machine must be reached over https."""
    try:
        parts = urlsplit(url)
    except ValueError:  # an unclosed `[` in the host
        parts = None
    if parts is None or parts.scheme not in ("http", "https") or not parts.hostname:
        return "Enter the slskd address in full, for example http://127.0.0.1:5030."
    if parts.scheme == "http" and parts.hostname not in LOOPBACK:
        return "slskd on another computer must be reached over https, so its key is not sent in the clear."
    return None


def router(store: Store, settings: Settings, status: dict, bundles: Bundles,
           opener: Callable[[Path], None] = reveal_path, link=None) -> APIRouter:
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
        """Served only from `spectrogram_dir`, the one place `verify` writes them -- the same containment
        `rejected_audio` makes, for the same reason."""
        try:
            rj = store.get_rejection(rjid)
        except KeyError:
            raise HTTPException(404, "not found")
        path = _contained(rj.spectrogram_path, settings.spectrogram_dir)
        if path is None:
            raise HTTPException(404, "no spectrogram")
        return FileResponse(path, media_type="image/png")

    @r.get("/rejections/{rjid}/audio")
    async def rejected_audio(rjid: int):
        """The refused copy itself, so the owner can hear what a check would not accept (issues #92, #97).

        Served only from `rejected_dir` -- `_contained`
        is the same containment check `unlink_under` makes, and it is what stops a row whose path
        column was written by an older build (or by hand) from turning this into a read of any file on the
        disk. The media type comes off the extension the source gave the file; an unknown one is refused
        rather than guessed, because a wrong type is a player that fails silently."""
        try:
            rj = store.get_rejection(rjid)
        except KeyError:
            raise HTTPException(404, "not found")
        path = _contained(rj.audio_path, settings.rejected_dir)
        if path is None:
            raise HTTPException(404, "no copy of this track was kept")
        media_type = AUDIO_TYPES.get(path.suffix.lower())
        if media_type is None:
            raise HTTPException(415, f"cannot play a {path.suffix.lstrip('.') or 'file'} here")
        # No `no-store`: unlike the Deezer preview this is a local file with a stable URL, and a replay
        # while the owner compares it against the reference should not re-read it from disk.
        return FileResponse(path, media_type=media_type)

    @r.get("/library")
    async def library(q: str | None = None, playlist_id: int | None = None) -> list:
        return [bundles.track(t.id) for t in store.list_tracks(search=q, limit=LIBRARY_LIMIT, playlist_id=playlist_id)]

    @r.post("/library/refresh")
    async def refresh_library(body: dict | None = None) -> dict:
        """Reconcile files removed in Finder and rewrite affected playlist exports. `missing` is how many
        files are gone but were kept, because so many went at once that only the owner can say they were
        deleted on purpose; sending `{"forget_missing": true}` forgets them. `reachable` is false when the
        library folder is missing or empty (an unplugged drive), and then nothing is ever forgotten."""
        root = settings.library_root
        removed = prune_missing_tracks(store, root, confirmed=bool((body or {}).get("forget_missing")))
        if removed:
            write_playlists(store, root)
        reachable = library_reachable(root)
        missing = len(missing_tracks(store, root)[0]) if reachable and not removed else 0
        return {"removed": len(removed), "missing": missing, "reachable": reachable}

    def new_out() -> dict:
        tracks = new_tracks(store)
        return {"count": len(tracks), "file": str(new_playlist_path(settings.library_root)),
                "through": filed_at(tracks[-1]) if tracks else None}

    @r.get("/library/new")
    async def library_new() -> dict:
        """The "Flackey - New" playlist: how many tracks it holds and where its file is. `through` is the
        newest of them, which the page sends back when the owner says they imported these."""
        return new_out()

    @r.post("/library/new/imported")
    async def library_new_imported(body: dict) -> dict:
        through = body.get("through")
        try:
            datetime.fromisoformat(through)
        except (TypeError, ValueError):
            raise HTTPException(400, "Nothing to mark as imported.") from None
        mark_imported(store, through)
        if library_reachable(settings.library_root):
            try:
                write_new_playlist(store, settings.library_root)
            except OSError as e:
                # The marker is saved, which is what the owner asked for; the file follows on the next filing.
                log.warning("could not rewrite the New playlist: %s", e)
        return new_out()

    def receipt_out(tid: int) -> dict:
        try:
            t = store.get_track(tid)
        except KeyError:
            raise HTTPException(404, "That track is no longer in the library.") from None
        return {**receipt(store, t), "format": bundles.track(tid)["format"]["label"]}

    @r.get("/library/{tid}/receipt")
    async def track_receipt(tid: int) -> dict:
        """Why the library trusts this file: where it came from and every check it went through."""
        return receipt_out(tid)

    @r.get("/library/{tid}/spectrogram.png")
    async def track_spectrogram(tid: int):
        try:
            t = store.get_track(tid)
        except KeyError:
            raise HTTPException(404, "not found") from None
        # Only ever served from `spectrogram_dir`, like a rejection's picture: the stored path is a database
        # value, and this must not become a way to read any file on the disk.
        path = _contained(spectrogram_of(store, t), settings.spectrogram_dir)
        if path is None:
            raise HTTPException(404, "no spectrogram")
        return FileResponse(path, media_type="image/png")

    # Checking the whole library again: one file at a time, in the background, for as long as it takes. In
    # memory only -- a quit stops it, and pressing the button again starts over.
    recheck_state: dict = {"state": "idle", "done": 0, "total": 0, "flagged": 0, "run": 0}
    recheck_tasks: set[asyncio.Task] = set()

    @r.post("/library/{tid}/recheck")
    async def recheck_one(tid: int) -> dict:
        """Check one filed file again with today's check. Reads the file only; nothing is moved or replaced."""
        if recheck_state["state"] == "running":
            raise HTTPException(409, "The library is being checked already. Wait for it to finish.")
        try:
            t = store.get_track(tid)
        except KeyError:
            raise HTTPException(404, "That track is no longer in the library.") from None
        record(store, t, await asyncio.to_thread(measure, t, settings.spectrogram_dir, settings.verification_rule))
        return receipt_out(tid)

    async def _recheck_all(ids: list[int], run: int) -> None:
        try:
            for tid in ids:
                # Stopped, or stopped and started again: a newer run owns the counters now
                if recheck_state["state"] != "running" or recheck_state["run"] != run:
                    return
                try:
                    t = store.get_track(tid)
                except KeyError:     # forgotten while the run was going: nothing to check
                    recheck_state["done"] += 1
                    continue
                value = await asyncio.to_thread(measure, t, settings.spectrogram_dir, settings.verification_rule)
                record(store, t, value)          # a true answer about the file, whichever run asked
                if recheck_state["run"] != run:
                    return
                recheck_state["done"] += 1
                recheck_state["flagged"] += int(flagged(value))
            if recheck_state["state"] == "running" and recheck_state["run"] == run:
                recheck_state["state"] = "done"   # not over a Stop pressed during the last file
        except Exception:
            log.exception("checking the library again stopped")
            if recheck_state["run"] == run:
                recheck_state["state"] = "error"

    @r.get("/library/recheck")
    async def recheck_status() -> dict:
        return dict(recheck_state)

    @r.post("/library/recheck")
    async def recheck_all() -> dict:
        if recheck_state["state"] == "running":
            return dict(recheck_state)
        ids = [t.id for t in store.list_tracks(limit=1_000_000)]
        run = recheck_state["run"] + 1
        recheck_state.update(state="running", done=0, total=len(ids), flagged=0, run=run)
        task = asyncio.create_task(_recheck_all(ids, run))
        recheck_tasks.add(task)
        task.add_done_callback(recheck_tasks.discard)
        return dict(recheck_state)

    @r.post("/library/recheck/stop")
    async def recheck_stop() -> dict:
        if recheck_state["state"] == "running":
            recheck_state["state"] = "stopped"    # the file being checked finishes; no further one starts
        return dict(recheck_state)

    # Tidying artist folders: a preview, then the moves only on the owner's confirmation of that preview.
    tidy_lock = asyncio.Lock()

    @r.get("/library/tidy")
    async def tidy_preview() -> dict:
        groups = await asyncio.to_thread(tidy.plan, store, settings.library_root, settings.library_layout)
        return {"groups": tidy.as_json(groups)}

    @r.post("/library/tidy")
    async def tidy_apply(body: dict) -> dict:
        moves = body.get("moves")
        if not isinstance(moves, list) or not moves:
            raise HTTPException(400, "Nothing to tidy was confirmed.")
        if recheck_state["state"] == "running" or tidy_lock.locked():
            raise HTTPException(409, "Wait for the library check to finish first.")
        async with tidy_lock:
            try:
                out = await asyncio.to_thread(tidy.apply, store, settings.library_root, moves,
                                              settings.library_layout)
            except tidy.PlanChanged as e:
                raise HTTPException(409, str(e)) from None
        return out           # `tidy.apply` rewrote every playlist, the New one included

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
                "library_layout": settings.library_layout, "library_layouts": list(LIBRARY_LAYOUTS),
                "auto_update_check": settings.auto_update_check,
                "extended_mixes": settings.extended_mixes,
                "ports": _ports(settings),
                "ranking": {"max_picks": settings.lossless_max_picks,
                            "max_queue": settings.lossless_max_queue,
                            "fingerprint_min": settings.lossless_fingerprint_min},
                "filing_formats": list(FILING_FORMATS), "default_filing_format": default_filing_format()}

    @r.get("/settings")
    async def get_settings() -> dict:
        return settings_out()

    @r.put("/settings")
    async def put_settings(body: dict) -> dict:
        raw = (body.get("library_root") or "").strip()
        path = Path(raw).expanduser() if raw else None
        # The page sends the library folder with every save. The folder rules are checked only when it
        # changes: an owner whose folder was chosen before a rule existed must still be able to flip an
        # unrelated toggle. (The share itself is still refused for such a folder; see `write_share`.)
        unchanged = path is not None and path == settings.library_root
        if not unchanged:
            if is_network_path(raw):
                # Before anything touches it: `mkdir` and `resolve` on an unreachable server can hang.
                raise HTTPException(400, unsafe_share_reason(Path(raw), settings.data_dir))
            if path is None or not path.is_absolute():
                raise HTTPException(400, "Choose a folder by its full path, for example ~/Music/DJ Library.")
            reason = unsafe_share_reason(path, settings.data_dir)
            if reason:
                raise HTTPException(400, reason)
        if not is_network_path(raw):
            try:
                path.mkdir(parents=True, exist_ok=True)
            except OSError as e:
                raise HTTPException(400, f"That folder cannot be used: {e.strerror or e}")
        extra: dict = {}
        if "lossless_filing_format" in body:
            if body["lossless_filing_format"] not in FILING_FORMATS:
                raise HTTPException(400, f"Filing format must be one of {', '.join(FILING_FORMATS)}.")
            extra["lossless_filing_format"] = body["lossless_filing_format"]
        if "library_layout" in body:
            if body["library_layout"] not in LIBRARY_LAYOUTS:
                raise HTTPException(400, f"Folder layout must be one of {', '.join(LIBRARY_LAYOUTS)}.")
            extra["library_layout"] = body["library_layout"]
        if "auto_update_check" in body:
            extra["auto_update_check"] = bool(body["auto_update_check"])
        if "extended_mixes" in body:
            if body["extended_mixes"] not in EXTENDED_MIXES:
                raise HTTPException(400, f"Extended mixes must be one of {', '.join(EXTENDED_MIXES)}.")
            extra["extended_mixes"] = body["extended_mixes"]
        if body.get("slskd_url"):
            slskd_url = str(body["slskd_url"]).strip().rstrip("/")
            # Checked only when it changes, like the library folder: an address set before this rule (or
            # in the environment) is sent back with every save and must not block the others.
            if slskd_url != settings.slskd_url:
                problem = slskd_url_problem(slskd_url)
                if problem:
                    raise HTTPException(400, problem)
            extra["slskd_url"] = slskd_url
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
        asked = str(body.get("path") or "")
        known = next((p for p in _revealable(settings, store) if str(p) == asked), None)
        if known is None:
            # The other half of what the UI reveals: a filed track. The lookup is a database query on the
            # stored name, so the path that gets opened is the one the library wrote, not the one asked for.
            track = store.find_track_by_path(Path(asked))
            known = track.path if track else None
        if known is None:
            raise HTTPException(400, "only files inside the library or app data folder can be shown")
        if not known.exists():
            raise HTTPException(404, "that file is no longer there")
        opener(known)
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
    async def get_soulseek_password(request: Request) -> dict:
        """Hand the owner back the Soulseek password flackey generated for them.

        The only route in flackey that returns a secret, and it is here because Soulseek has no password
        reset: the name is bound to the password it was claimed with, and an owner who cannot produce that
        string cannot sign in from anywhere else, ever. Keeping it unreadable would not protect them from
        anything -- the file is 0600 under their own account, and with other sites' pages refused (the Host
        check in `web.guard`, and the header below) anything that can call this can already read it -- it would only make the account unrecoverable. Deliberately its own path rather than a
        field on GET /setup/soulseek, which the wizard polls: a secret must not ride along on a poll."""
        from_the_app(request)
        password = read_password(settings.data_dir)
        if password is None:
            raise HTTPException(404, "No Soulseek password is saved.")
        return {"username": read_username(settings.data_dir), "password": password}

    @r.get("/setup/slskd/credentials")
    async def get_slskd_credentials(request: Request) -> dict:
        """Expose the local helper's web-login secret only on an explicit owner request."""
        from_the_app(request)
        credentials = read_web_credentials(settings.data_dir)
        if credentials is None:
            raise HTTPException(404, "No Soulseek helper login is saved.")
        return {"username": credentials[0], "password": credentials[1]}

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
