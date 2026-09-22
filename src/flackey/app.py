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
from .config import Settings, save_settings
from .deezer import DeezerApi
from .events import EventBus, Status
from .inbox import Inbox
from .logsetup import log_startup_banner
from .notify import LogNotifier
from .sharing import Sharing
from .slskd_binary import SlskdBinaryError
from .slskd_config import SlskdConfigError, read_listen_port, write_share
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
    # Set by the desktop window once it exists, which is after the server has already started -- hence
    # a slot read at call time rather than a callback passed in at construction.
    on_quit: Callable[[], None] | None = None

    def stop(self) -> None:
        """Safe from any thread: uvicorn polls should_exit on its own loop."""
        if self.server is not None:
            self.server.should_exit = True

    def quit_app(self) -> None:
        """End the program from a request handler, the way the owner closing the window would.

        Falls back to stopping the server when there is no window: `flackey start --no-browser` has
        nothing to close."""
        if self.on_quit is not None:
            self.on_quit()
        else:
            self.stop()


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


async def supervise_worker(worker, status: dict, poll_s: float = 1.0,
                           run_when: Callable[[], bool] | None = None) -> None:
    """The worker stops itself when the Telegram session dies; start it again once the setup screen
    has signed the account back in. `run_when` is the gate: by default "Telegram is authorized", and
    app._run widens it to "or the Telegram source is switched off", because a Soulseek-only copy has
    nothing to sign in to."""
    if run_when is None:
        def run_when() -> bool:
            return bool(status.get("telegram_authorized"))
    while True:
        if run_when():
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


def repair_share(settings: Settings) -> None:
    """Every start: make sure slskd is still sharing the library folder. Idempotent, and a no-op when
    there is no slskd.yml, so a fresh or Telegram-only install pays nothing for it. It is here for the
    copies set up before flackey wrote a share at all -- they would otherwise offer peers nothing (and
    be refused uploads for it) until the owner happened to move their library folder."""
    if not settings.soulseek_enabled:
        return
    try:
        write_share(settings.data_dir, settings.library_root)
    except SlskdConfigError as e:
        log.warning("could not point the Soulseek share at the library folder: %s", e)


def make_on_authorized(settings: Settings, status: dict) -> Callable[[], None]:
    """What a finished Telegram sign-in changes. `source_enabled` goes into `status` beside
    `telegram_authorized`, in one update, so the single event that follows carries both: the page reads
    the source flag from health, and a sign-in that published only "authorized" left the sidebar saying
    "Telegram off" until the next full refresh."""
    def on_authorized() -> None:
        status.update(telegram_authorized=True, source_enabled=True)
        if not settings.source_enabled:
            # A sign-in after a skipped setup step: the bot is a source again.
            save_settings(settings, source_enabled=True)
    return on_authorized


async def _run(settings: Settings, handle: ServerHandle) -> None:
    for d in (settings.data_dir, settings.tmp_dir, settings.spectrogram_dir, settings.rejected_dir,
              settings.library_root, settings.lossless_raw_dir):
        d.mkdir(parents=True, exist_ok=True)
    repair_share(settings)
    log_startup_banner(settings, __version__)
    store = Store(settings.db_path)
    bus = EventBus()
    status = Status(bus, telegram_authorized=True, worker_running=False,
                    setup_done=store.get_setting("setup_done") == "1",
                    source_enabled=settings.source_enabled)
    on_authorized = make_on_authorized(settings, status)

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
        log.info("Telegram keys not configured: the setup screen asks for them, or set them in Settings")
        login = TelegramLogin(client, False, on_authorized=on_authorized, make_client=make_client)
    else:
        await client.connect()
        if not await client.is_user_authorized():
            status["telegram_authorized"] = False
            log.error("Telegram login required, sign in from the setup screen in the UI; the worker is paused")
        login = TelegramLogin(client, True, on_authorized=on_authorized, make_client=make_client)

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
    # The port has to come from the sidecar's own config: the owner may have changed it, and
    # opening a different one than slskd listens on would look like it worked and share nothing.
    sharing = Sharing(settings, status, http, port=read_listen_port(settings.data_dir))
    link = SoulseekLink(settings, worker, http, build_providers=build_providers,
                        on_connected=sharing.start_refresh)
    link.adopt(slskd_process)
    api = create_app(store, worker, inbox, settings, ui_dir=UI_DIR, status=status, bus=bus, login=login,
                     link=link, sharing=sharing, quit_app=handle.quit_app)
    server = uvicorn.Server(uvicorn.Config(api, host=settings.web_host, port=settings.web_port,
                                           log_level="warning", log_config=None))

    # Must match `settings.web_host` exactly, not just resolve to the same machine: "localhost" can
    # resolve to the IPv6 loopback first, and if anything else is listening on this port over IPv6 (a
    # stray dev server, say), the window silently loads that instead of failing to connect. The
    # exception is a wildcard bind (Docker's web_host="0.0.0.0"): nothing there is directly connectable,
    # so the desktop-window/browser-opening and log-message cases both want "localhost" instead --
    # and open_browser is never true in that mode anyway (`flackey start --no-browser`).
    display_host = "localhost" if settings.web_host in ("0.0.0.0", "::") else settings.web_host
    url = f"http://{display_host}:{settings.web_port}"

    worker_runs = status["telegram_authorized"] or not settings.source_enabled
    log.info("flackey started%s", ", worker running" if worker_runs else "")
    try:
        await run_until_server_stops(
            serve(server, url, handle),
            supervise_worker(worker, status,
                             run_when=lambda: bool(status.get("telegram_authorized"))
                             or not settings.source_enabled),
            close_streams_on_exit(server, bus),
            sharing.run_forever())
    finally:
        server.should_exit = True
        await sharing.release()       # give the port back before the sidecar that used it goes away
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
