import asyncio

import pytest

from flackey.app import start_sidecar, supervise_worker
from flackey.slskd_binary import SlskdBinaryError


async def test_supervisor_starts_the_worker_when_telegram_becomes_authorized():
    status = {"telegram_authorized": False, "worker_running": False}
    runs = []

    class W:
        async def run_forever(self):
            runs.append(status["worker_running"])
            status["telegram_authorized"] = False   # what the real worker does when the session dies

    task = asyncio.create_task(supervise_worker(W(), status, poll_s=0.01))
    await asyncio.sleep(0.05)
    assert runs == []
    status["telegram_authorized"] = True
    await asyncio.sleep(0.05)
    assert runs == [True] and status["worker_running"] is False
    task.cancel()


async def test_supervisor_keeps_running_after_worker_crash():
    status = {"telegram_authorized": True, "worker_running": False}
    runs = []

    class CrashingW:
        async def run_forever(self):
            runs.append(status["worker_running"])
            if len(runs) == 1:
                raise RuntimeError("worker crashed")
            status["telegram_authorized"] = False  # stop retrying once the restart is proven

    task = asyncio.create_task(supervise_worker(CrashingW(), status, poll_s=0.01))
    loop = asyncio.get_event_loop()
    deadline = loop.time() + 2.0
    while len(runs) < 2 and loop.time() < deadline:
        await asyncio.sleep(0.01)
    assert len(runs) >= 2                  # a genuine restart happened, not just the first crash
    assert status["worker_running"] is False
    task.cancel()


async def test_streams_close_once_the_server_is_told_to_exit():
    from flackey.app import close_streams_on_exit
    from flackey.events import EventBus

    class FakeServer:
        should_exit = False

    server, bus = FakeServer(), EventBus()
    q = bus.subscribe()
    task = asyncio.create_task(close_streams_on_exit(server, bus, poll_s=0.01))
    await asyncio.sleep(0.03)
    assert q.empty()
    server.should_exit = True
    await asyncio.wait_for(task, 1.0)
    assert q.get_nowait() is None


async def test_background_loops_stop_when_the_server_returns():
    """Ctrl-C makes uvicorn's serve() return; the supervisor loop is endless and must be cancelled with it."""
    from flackey.app import run_until_server_stops

    async def serve():
        await asyncio.sleep(0.01)

    async def endless():
        while True:
            await asyncio.sleep(0.01)

    await asyncio.wait_for(run_until_server_stops(serve(), endless()), 1.0)


async def test_serve_publishes_the_url_once_the_server_is_listening():
    from flackey.app import ServerHandle, serve

    class FakeServer:
        started = False
        should_exit = False

        async def serve(self):
            await asyncio.sleep(0.01)
            self.started = True
            while not self.should_exit:
                await asyncio.sleep(0.01)

    opened = []
    server, handle = FakeServer(), ServerHandle(on_started=opened.append)
    task = asyncio.create_task(serve(server, "http://localhost:1", handle))
    await asyncio.wait_for(asyncio.to_thread(handle.started.wait, 1.0), 2.0)
    assert handle.url == "http://localhost:1" and handle.error is None and opened == ["http://localhost:1"]
    handle.stop()                      # what the desktop window does when it closes
    await asyncio.wait_for(task, 1.0)  # serve() returns because should_exit was set


async def test_serve_reports_a_startup_failure_through_the_handle():
    from flackey.app import ServerHandle, serve

    class FailingServer:
        started = False
        should_exit = False

        async def serve(self):
            raise OSError("address already in use")

    handle = ServerHandle()
    try:
        await serve(FailingServer(), "http://localhost:1", handle)
    except OSError:
        pass
    else:
        raise AssertionError("bind error must propagate")
    assert handle.started.is_set() and isinstance(handle.error, OSError) and handle.url is None


async def test_run_wakes_a_waiting_thread_even_when_startup_fails_before_the_server_exists(tmp_path):
    from unittest.mock import patch

    from flackey.app import ServerHandle, run
    from flackey.config import Settings

    handle = ServerHandle()
    settings = Settings(_env_file=None, data_dir=tmp_path / "data", library_root=tmp_path / "lib")

    # Simulate startup failure before the server exists by raising from log_startup_banner
    with patch("flackey.app.log_startup_banner", side_effect=RuntimeError("boom")):
        try:
            await run(settings, handle=handle)
        except RuntimeError as e:
            assert str(e) == "boom"
        else:
            raise AssertionError("error must propagate")

    # The guarantee: even though the server was never created, the waiting thread wakes up
    assert handle.started.is_set()


def test_build_providers_follows_the_api_key_and_never_logs_it(tmp_path, caplog):
    import httpx

    from flackey.app import build_providers
    from flackey.config import Settings
    from flackey.source.slskd import SoulseekProvider

    caplog.set_level("DEBUG")
    http = httpx.AsyncClient()
    off = Settings(_env_file=None, data_dir=tmp_path)
    assert build_providers(off, http) == []
    on = Settings(_env_file=None, data_dir=tmp_path, slskd_api_key="very-secret-key")
    providers = build_providers(on, http)
    assert len(providers) == 1 and isinstance(providers[0], SoulseekProvider)
    assert providers[0].downloads == tmp_path / "slskd" / "downloads" and providers[0].downloads.is_dir()
    assert "very-secret-key" not in caplog.text


async def test_supervisor_runs_the_worker_when_run_when_says_so_without_telegram():
    """A Soulseek-only copy has no Telegram to sign in to; the gate _run passes in must still start it."""
    status = {"telegram_authorized": False, "worker_running": False}
    runs = []
    source_on = [False]

    class W:
        async def run_forever(self):
            runs.append(status["worker_running"])
            source_on[0] = True   # stop after one run

    task = asyncio.create_task(supervise_worker(
        W(), status, poll_s=0.01,
        run_when=lambda: status["telegram_authorized"] or not source_on[0]))
    await asyncio.sleep(0.05)
    assert runs == [True] and status["worker_running"] is False
    task.cancel()


def test_boot_points_the_soulseek_share_at_the_library_folder(tmp_path):
    """A copy set up before flackey wrote shares at all has a slskd.yml with no `shares` key, so it
    offers peers nothing. Starting the app repairs that rather than waiting for a library move."""
    import yaml

    from flackey.app import repair_share
    from flackey.config import Settings
    from flackey.slskd_config import config_path, write_credentials

    settings = Settings(_env_file=None, data_dir=tmp_path / "data", library_root=tmp_path / "lib",
                        slskd_api_key="k")
    write_credentials(settings.data_dir, "digger", "not-a-real-password")   # no share: the old shape
    repair_share(settings)
    data = yaml.safe_load(config_path(settings.data_dir).read_text())
    assert data["shares"]["directories"] == [str(settings.library_root)]
    assert data["soulseek"]["username"] == "digger"      # the repair touches nothing else

    repair_share(settings)                               # every boot, so it has to stay a no-op
    data = yaml.safe_load(config_path(settings.data_dir).read_text())
    assert data["shares"]["directories"] == [str(settings.library_root)]


def test_boot_writes_no_config_when_soulseek_was_never_set_up(tmp_path, caplog):
    """Neither an install without Soulseek nor one whose slskd.yml cannot be parsed may stop a boot."""
    from flackey.app import repair_share
    from flackey.config import Settings
    from flackey.slskd_config import config_path

    off = Settings(_env_file=None, data_dir=tmp_path / "off", library_root=tmp_path / "lib")
    repair_share(off)
    assert not config_path(off.data_dir).exists()

    on = Settings(_env_file=None, data_dir=tmp_path / "on", library_root=tmp_path / "lib",
                  slskd_api_key="k")
    repair_share(on)                                     # Soulseek on, but nothing written yet
    assert not config_path(on.data_dir).exists()

    path = config_path(on.data_dir)
    path.parent.mkdir(parents=True)
    path.write_text("- not\n- a mapping\n")
    caplog.set_level("WARNING")
    repair_share(on)                                     # must warn, not raise
    assert "Soulseek share" in caplog.text


def test_on_authorized_turns_the_source_back_on_in_status_and_on_disk(tmp_path):
    """Signing in after a skipped Telegram step is what makes the bot a source again. The page reads
    `source_enabled` from health, so the flag has to reach `status` -- and in the same event as
    `telegram_authorized`, or the sidebar keeps saying "Telegram off"."""
    import json

    from flackey.app import make_on_authorized
    from flackey.config import Settings
    from flackey.events import EventBus, Status

    settings = Settings(_env_file=None, data_dir=tmp_path / "data", library_root=tmp_path / "lib",
                        source_enabled=False)
    published: list[dict] = []

    class Recorder(EventBus):
        def publish(self, name, data):
            published.append(data)

    status = Status(Recorder(), telegram_authorized=False, source_enabled=False)
    make_on_authorized(settings, status)()

    assert status["source_enabled"] is True and status["telegram_authorized"] is True
    assert published == [{"telegram_authorized": True, "source_enabled": True}]
    assert settings.source_enabled is True
    assert json.loads(settings.settings_path.read_text())["source_enabled"] is True


async def test_desktop_window_url_uses_the_exact_host_the_server_bound_to(tmp_path):
    """"localhost" can resolve to the IPv6 loopback before 127.0.0.1 -- if anything else on the machine
    is listening on the same port over IPv6, a webview pointed at "localhost" silently loads that instead
    of Flackey. The URL handed to the window (and to `webbrowser.open`) must name the literal host the
    server bound to, `settings.web_host`, not a hostname that can resolve to a different address."""
    from flackey.app import ServerHandle, run
    from flackey.config import Settings

    settings = Settings(_env_file=None, data_dir=tmp_path / "data", library_root=tmp_path / "lib",
                        web_host="127.0.0.1", web_port=0, source_enabled=False)
    handle = ServerHandle()
    task = asyncio.create_task(run(settings, open_browser=False, handle=handle))
    await asyncio.wait_for(asyncio.to_thread(handle.started.wait, 10.0), 15.0)
    try:
        assert handle.error is None
        assert handle.url is not None and handle.url.startswith(f"http://{settings.web_host}:")
        assert "localhost" not in handle.url
    finally:
        handle.stop()
        await asyncio.wait_for(task, 5.0)


async def test_desktop_window_url_falls_back_to_localhost_for_a_wildcard_bind(tmp_path):
    """Docker binds web_host="0.0.0.0"; nothing is directly connectable at that address, so the URL
    handed to on_started (and logged) should still say "localhost", not the wildcard itself."""
    from flackey.app import ServerHandle, run
    from flackey.config import Settings

    settings = Settings(_env_file=None, data_dir=tmp_path / "data", library_root=tmp_path / "lib",
                        web_host="0.0.0.0", web_port=0, source_enabled=False)
    handle = ServerHandle()
    task = asyncio.create_task(run(settings, open_browser=False, handle=handle))
    await asyncio.wait_for(asyncio.to_thread(handle.started.wait, 10.0), 15.0)
    try:
        assert handle.error is None
        assert handle.url is not None and handle.url.startswith("http://localhost:")
    finally:
        handle.stop()
        await asyncio.wait_for(task, 5.0)


# ---- the session watcher (issue #91) ------------------------------------
class FakeLogin:
    """What the watcher reads: the live client and whether keys are configured at all. `client` is a
    plain attribute because log_out() and reconfigure() reassign it -- the watcher must re-read it."""

    def __init__(self, connected=False, configured=True):
        self.client = FakeTelethon(connected)
        self.configured = configured


class FakeTelethon:
    def __init__(self, connected):
        self.connected = connected

    def is_connected(self):
        return self.connected


def _probe(*answers):
    """A probe that answers the given list in order and repeats the last answer after that."""
    seen = []

    async def probe(client):
        seen.append(client)
        return answers[min(len(seen), len(answers)) - 1]

    probe.seen = seen
    return probe


async def _until(predicate, timeout=1.0):
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout
    while not predicate() and loop.time() < deadline:
        await asyncio.sleep(0.005)
    return predicate()


async def test_a_revoked_session_flips_the_flag_with_no_request_in_flight():
    """Acceptance criterion 1 of issue #91. Telethon disconnects itself when Telegram revokes the key;
    with an idle queue nothing calls the source, so nothing else would ever notice."""
    from flackey.app import watch_telegram_session

    status = {"telegram_authorized": True}
    login, probe = FakeLogin(connected=False), _probe(False)
    task = asyncio.create_task(watch_telegram_session(login, status, poll_s=0.01, probe=probe))

    assert await _until(lambda: status["telegram_authorized"] is False)
    await asyncio.sleep(0.05)
    assert len(probe.seen) == 1, "the flag is the guard: the watcher must not keep probing after it flips"
    task.cancel()


async def test_a_disconnect_with_a_healthy_or_unknown_session_does_not_sign_the_owner_out():
    """Closing the app disconnects the client too, and so does a wifi drop. Neither is a revoked
    session, and reporting one would pause the worker and cover the app in a sign-in banner."""
    from flackey.app import watch_telegram_session

    for answer in (True, None):
        status = {"telegram_authorized": True}
        login = FakeLogin(connected=False)
        task = asyncio.create_task(
            watch_telegram_session(login, status, poll_s=0.01, probe=_probe(answer)))
        await asyncio.sleep(0.08)
        assert status["telegram_authorized"] is True, f"a probe answering {answer} signed the owner out"
        task.cancel()


async def test_the_watcher_leaves_a_connected_client_alone():
    from flackey.app import watch_telegram_session

    status = {"telegram_authorized": True}
    login, probe = FakeLogin(connected=True), _probe(False)
    task = asyncio.create_task(watch_telegram_session(login, status, poll_s=0.01, probe=probe))
    await asyncio.sleep(0.08)
    assert probe.seen == [] and status["telegram_authorized"] is True
    task.cancel()


async def test_the_watcher_says_nothing_about_a_copy_with_no_telegram_keys():
    """An unconfigured client was never connected and never signed in; calling it would raise rather
    than answer, which is why status() does not either."""
    from flackey.app import watch_telegram_session

    status = {"telegram_authorized": False}
    login, probe = FakeLogin(connected=False, configured=False), _probe(False)
    task = asyncio.create_task(watch_telegram_session(login, status, poll_s=0.01, probe=probe))
    await asyncio.sleep(0.05)
    assert probe.seen == []
    task.cancel()


async def test_a_client_rebuilt_mid_probe_is_not_acted_on():
    """log_out() and reconfigure() replace login.client. A verdict about the object that was thrown
    away says nothing about the one that replaced it."""
    from flackey.app import watch_telegram_session

    status = {"telegram_authorized": True}
    login = FakeLogin(connected=False)
    probed = []

    async def probe(client):
        probed.append(client)
        login.client = FakeTelethon(connected=True)    # a sign-out landing while we waited
        return False

    task = asyncio.create_task(watch_telegram_session(login, status, poll_s=0.01, probe=probe))
    await _until(lambda: probed != [])
    await asyncio.sleep(0.05)
    assert status["telegram_authorized"] is True
    task.cancel()


class _Sidecar:
    def __init__(self, error: BaseException | None = None):
        self.error, self.started = error, 0

    async def start(self) -> None:
        self.started += 1
        if self.error is not None:
            raise self.error


async def test_start_sidecar_starts_the_boot_slskd():
    sidecar = _Sidecar()
    await start_sidecar(sidecar)
    assert sidecar.started == 1


async def test_start_sidecar_without_soulseek_does_nothing():
    await start_sidecar(None)


@pytest.mark.parametrize("error", [SlskdBinaryError("slskd did not become healthy"), RuntimeError("boom")])
async def test_a_sidecar_that_will_not_start_leaves_the_server_running(error):
    """It runs in the server's TaskGroup, where a raise would cancel the server with it."""
    sidecar = _Sidecar(error)
    await start_sidecar(sidecar)
    assert sidecar.started == 1


# ---- the launch token -------------------------------------------------------------------------------
def test_the_launch_token_is_random_unless_the_environment_fixes_one():
    from flackey.app import api_token_from_env

    first, second = api_token_from_env({}), api_token_from_env({})
    assert first != second and len(first) >= 43
    fixed = "d" * 40
    assert api_token_from_env({"FLACKEY_API_TOKEN": fixed}) == fixed
    assert api_token_from_env({"FLACKEY_API_TOKEN": "  "}) not in ("", "  ")  # blank is unset


@pytest.mark.parametrize("weak", ["short", "x" * 31, "spaces in the middle of it make no token at all!"])
def test_a_weak_fixed_token_stops_the_start(weak):
    """A guessable token is worse than none being asked for, because it reads as protection."""
    from flackey.app import api_token_from_env

    with pytest.raises(SystemExit, match="FLACKEY_API_TOKEN"):
        api_token_from_env({"FLACKEY_API_TOKEN": weak})


def test_the_address_with_the_token_puts_it_in_the_fragment():
    """A fragment never leaves the browser: not in a request line, not in a Referer, not in a log."""
    from flackey.app import ServerHandle

    handle = ServerHandle(url="http://127.0.0.1:8765", token="abc")
    assert handle.app_url() == "http://127.0.0.1:8765/#t=abc"
    assert handle.app_url("?titlebar=inset") == "http://127.0.0.1:8765/?titlebar=inset#t=abc"


def test_browser_mode_prints_and_opens_the_address_with_the_token(capsys, monkeypatch):
    from flackey import app as app_module

    opened = []
    monkeypatch.setattr(app_module.webbrowser, "open", opened.append)
    handle = app_module.ServerHandle(url="http://localhost:8765", token="tok")
    app_module.announce(handle, open_browser=True)
    assert opened == ["http://localhost:8765/#t=tok"]
    assert "http://localhost:8765/#t=tok" in capsys.readouterr().out
    app_module.announce(handle, open_browser=False)
    assert opened == ["http://localhost:8765/#t=tok"]
    assert "http://localhost:8765/#t=tok" in capsys.readouterr().out


async def test_the_running_server_asks_for_the_token_it_was_started_with(tmp_path, caplog, monkeypatch):
    import httpx

    from flackey.app import ServerHandle, run
    from flackey.config import Settings

    monkeypatch.setenv("FLACKEY_API_TOKEN", "k" * 40)
    settings = Settings(_env_file=None, data_dir=tmp_path / "data", library_root=tmp_path / "lib",
                        web_host="127.0.0.1", web_port=0, source_enabled=False)
    handle = ServerHandle()
    caplog.set_level("DEBUG")
    task = asyncio.create_task(run(settings, open_browser=False, handle=handle))
    await asyncio.wait_for(asyncio.to_thread(handle.started.wait, 10.0), 15.0)
    try:
        assert handle.error is None and handle.token == "k" * 40
        port = handle.server.servers[0].sockets[0].getsockname()[1]
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as http:
            assert (await http.get("/api/health")).status_code == 401
            assert (await http.get("/api/health", headers={"x-flackey-token": "k" * 40})).status_code == 200
        # The log ends up in bug reports.
        assert "k" * 40 not in caplog.text
    finally:
        handle.stop()
        await asyncio.wait_for(task, 5.0)
