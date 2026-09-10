from __future__ import annotations

import asyncio
import logging
import threading
import webbrowser
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field

import httpx
import uvicorn
from telethon import TelegramClient

from . import __version__
from .catalog import BeatportCatalog
from .config import Settings, migrate_legacy_data_dir
from .deezer import DeezerApi
from .events import EventBus, Status
from .inbox import Inbox
from .logsetup import log_startup_banner
from .notify import LogNotifier
from .slskd_binary import SlskdBinaryError
from .slskd_process import SlskdProcess
from .soulseek_link import SoulseekLink
from .source.deezer_bot import DeezerBotSource
from .source.lossless import LosslessProvider
from .source.slskd import SlskdClient, SoulseekProvider
from .store import Store
from .telegram import TelegramLogin
from .tools import resource_dir
from .web import create_app
from .worker import Worker

log = logging.getLogger(__name__)
# Resolves to <repo>/web/dist under `uv sync` (editable install) and in the Docker image, which is how
# this project is run. A wheel install would resolve elsewhere; the static mount is guarded by
# `ui_dir.exists()` in create_app, so the failure mode is a 404 on `/`, never a crash.
UI_DIR = resource_dir() / "web" / "dist"
WEB_SERVER_START_TIMEOUT_S = 30


@dataclass
class ServerHandle:
    """Filled in while the server starts so another thread (the desktop window in desktop.py) can learn the
    URL, learn that startup failed, and stop the server. `started` is set exactly once: on success (url
    set), on a startup error (error set), or when run() ends without ever starting."""
    on_started: Callable[[str], None] | None = None
    started: threading.Event = field(default_factory=threading.Event)
    url: str | None = None
    error: BaseException | None = None
    server: uvicorn.Server | None = None

    def stop(self) -> None:
        """Safe from any thread: uvicorn polls should_exit on its own loop."""
        if self.server is not None:
            self.server.should_exit = True


async def serve(server: uvicorn.Server, url: str, handle: ServerHandle,
                timeout_s: float = WEB_SERVER_START_TIMEOUT_S) -> None:
    """Run the server; once it is listening, publish the URL through `handle` and call on_started."""
    task = asyncio.create_task(server.serve())
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    try:
        while not server.started:
            if task.done():
                await task  # re-raises the bind error (port taken, etc.)
                raise RuntimeError("web server did not start")  # serve() returned without ever starting
            if loop.time() >= deadline:
                task.cancel()
                raise RuntimeError("web server did not start")
            await asyncio.sleep(0.1)
    except BaseException as e:
        handle.error = e
        handle.started.set()
        raise
    handle.server = server
    handle.url = url
    handle.started.set()
    log.info("UI at %s", url)
    if handle.on_started is not None:
        handle.on_started(url)
    await task


async def supervise_worker(worker, status: dict, poll_s: float = 1.0) -> None:
    """The worker stops itself when the Telegram session dies; start it again once the setup screen
    has signed the account back in."""
    while True:
        if status.get("telegram_authorized"):
            status["worker_running"] = True
            try:
                await worker.run_forever()
            except Exception:
                log.exception("worker crashed; retrying in %ss", poll_s)
            finally:
                status["worker_running"] = False
        await asyncio.sleep(poll_s)


async def close_streams_on_exit(server, bus: EventBus, poll_s: float = 0.2) -> None:
    """uvicorn's Ctrl-C handler only flips `should_exit`; its shutdown then awaits Server.wait_closed(), which
    on Python 3.12 waits for every open connection -- and the page's SSE stream never ends on its own. Closing
    the bus ends the streams, so the connections close and the shutdown completes."""
    while not server.should_exit:
        await asyncio.sleep(poll_s)
    bus.close()


async def run_until_server_stops(serve: Coroutine, *loops: Coroutine) -> None:
    """Run the server with its endless background loops; when serve() returns (Ctrl-C), cancel the loops so
    the group finishes. A failing task still cancels the rest (TaskGroup semantics)."""
    async with asyncio.TaskGroup() as tg:
        tasks = [tg.create_task(c) for c in loops]
        tg.create_task(serve).add_done_callback(lambda _: [t.cancel() for t in tasks])


def build_providers(settings: Settings, http: httpx.AsyncClient) -> list[LosslessProvider]:
    """One provider per configured network (spec §10). Only the sidecar URL and folder are logged, never the key."""
    providers: list[LosslessProvider] = []
    if settings.soulseek_enabled:
        settings.slskd_downloads.mkdir(parents=True, exist_ok=True)
        client = SlskdClient(settings.slskd_url, settings.slskd_api_key or "", http)
        providers.append(SoulseekProvider(client, settings.slskd_downloads))
        log.info("Soulseek on: slskd at %s, downloads in %s", settings.slskd_url, settings.slskd_downloads)
    return providers


async def _run(settings: Settings, handle: ServerHandle) -> None:
    migrate_legacy_data_dir(settings)
    for d in (settings.data_dir, settings.tmp_dir, settings.spectrogram_dir, settings.library_root,
              settings.lossless_raw_dir):
        d.mkdir(parents=True, exist_ok=True)
    log_startup_banner(settings, __version__)
    store = Store(settings.db_path)
    bus = EventBus()
    status = Status(bus, telegram_authorized=True, worker_running=False,
                    setup_done=store.get_setting("setup_done") == "1")

    # Telethon's constructor rejects a falsy api_id/api_hash outright (`not api_id or not api_hash`
    # raises ValueError before any network use), so `0`/`""` would crash right here -- verified against
    # telethon/client/telegrambaseclient.py. Placeholder non-empty values satisfy that check; they are
    # never used to talk to Telegram because `client.connect()` is skipped below when unconfigured.
    def make_client() -> TelegramClient:
        return TelegramClient(str(settings.session_path), settings.telegram_api_id or 1,
                              settings.telegram_api_hash or "unconfigured")

    client = make_client()
    if not settings.telegram_configured:
        status["telegram_authorized"] = False
        log.error("Telegram credentials missing: add them to settings.json")
        login = TelegramLogin(client, False, make_client=make_client)
    else:
        await client.connect()
        if not await client.is_user_authorized():
            status["telegram_authorized"] = False
            log.error("Telegram login required, sign in from the setup screen in the UI; the worker is paused")
        login = TelegramLogin(client, True, on_authorized=lambda: status.__setitem__("telegram_authorized", True),
                              make_client=make_client)

    http = httpx.AsyncClient(timeout=20)
    providers = build_providers(settings, http)

    # Lazy by design: this only ever starts an *already-installed* slskd (start() is cheap and
    # never downloads). The download itself is triggered from the setup wizard's
    # `POST /api/setup/slskd` (web/library.py), never from here or at import time -- a first run
    # with no slskd installed must still start the app. A failure here is not fatal: Soulseek is
    # one provider among several, so we log and carry on rather than crash startup.
    slskd_process: SlskdProcess | None = None
    if settings.soulseek_enabled:
        slskd_process = SlskdProcess(settings.data_dir, settings.slskd_url, settings.slskd_api_key or "")
        try:
            await slskd_process.start()
        except SlskdBinaryError as exc:
            log.warning("slskd not started: %s", exc)
    # Sign-out (TelegramLogin.log_out) rebuilds the client and reassigns login.client; the source must
    # dereference it fresh on every call rather than hold the object that was just made unusable.
    source = DeezerBotSource(client, settings.source_bot_username, DeezerApi(http), get_client=lambda: login.client)
    worker = Worker(store, source, BeatportCatalog(), LogNotifier(), settings, status=status,
                    providers=providers, http=http)
    inbox = Inbox(store)

    if not UI_DIR.exists():
        log.warning("UI not built: run `npm --prefix web run build`")
    # The wizard's Soulseek step drives this: saving credentials restarts the sidecar under them and
    # rebuilds `worker.providers`, so the owner learns on the spot whether the account signed in --
    # which, on Soulseek, is also the only confirmation that a new account now exists.
    link = SoulseekLink(settings, worker, http, build_providers=build_providers)
    link.adopt(slskd_process)
    api = create_app(store, worker, inbox, settings, ui_dir=UI_DIR, status=status, bus=bus, login=login,
                     link=link)
    server = uvicorn.Server(uvicorn.Config(api, host=settings.web_host, port=settings.web_port,
                                           log_level="warning", log_config=None))

    url = f"http://localhost:{settings.web_port}"

    log.info("flackey started%s", ", worker running" if status["telegram_authorized"] else "")
    try:
        await run_until_server_stops(serve(server, url, handle), supervise_worker(worker, status),
                                     close_streams_on_exit(server, bus))
    finally:
        server.should_exit = True
        if link.process is not None:      # the link owns the handle after adopt(); it may have replaced it
            await link.process.stop()
        await http.aclose()
        await login.client.disconnect()


async def run(settings: Settings, open_browser: bool = True, handle: ServerHandle | None = None) -> None:
    if handle is None:
        handle = ServerHandle(on_started=webbrowser.open if open_browser else None)
    try:
        await _run(settings, handle)
    finally:
        handle.started.set()  # wakes a waiting thread even when startup died before the server existed
