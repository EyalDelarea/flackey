import asyncio

from flackey.app import supervise_worker


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


async def test_run_wakes_a_waiting_thread_even_when_startup_fails_before_the_server_exists():
    from unittest.mock import patch

    from flackey.app import ServerHandle, run

    handle = ServerHandle()

    # Simulate startup failure before server exists by raising from migrate_legacy_data_dir
    with patch("flackey.app.migrate_legacy_data_dir", side_effect=RuntimeError("boom")):
        try:
            await run(object(), handle=handle)  # type: ignore
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
