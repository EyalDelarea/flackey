"""Bringing Soulseek up on a running process. No real waits: the clock and sleep are injected, the
sidecar is a fake, and no test touches the network."""
from pathlib import Path

import httpx
import pytest

from flackey.config import Settings
from flackey.slskd_binary import SlskdBinaryError
from flackey.soulseek_link import TAKEN_HINT, SoulseekLink


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class FakeProcess:
    started = 0

    def __init__(self, *a, **kw):
        self.stopped, self.start_error = 0, None

    async def start(self, **kw):
        if self.start_error:
            raise self.start_error
        FakeProcess.started += 1

    async def stop(self, **kw):
        self.stopped += 1


class FakeProvider:
    name = "soulseek"

    def __init__(self, states):
        self.states, self.calls = list(states), 0

    async def health(self):
        self.calls += 1
        s = self.states[min(self.calls - 1, len(self.states) - 1)]
        if isinstance(s, Exception):
            raise s
        return {"status": s, "username": "digger" if s == "ok" else None}


class FakeWorker:
    def __init__(self):
        self.providers, self.status = [], {}


def make(tmp_path: Path, states, *, installed=True, process=None):
    settings = Settings(_env_file=None, telegram_api_id=1, telegram_api_hash="h",
                        library_root=tmp_path / "lib", data_dir=tmp_path / "data", slskd_api_key="k")
    worker, clock, provider = FakeWorker(), Clock(), FakeProvider(states)

    async def sleep(s):
        clock.t += s

    link = SoulseekLink(settings, worker, httpx.AsyncClient(),
                        build_providers=lambda *_: [provider],
                        process_factory=lambda *a, **kw: process or FakeProcess(),
                        installed=lambda _: installed, clock=clock, sleep=sleep)
    return link, worker, provider, clock


async def test_a_successful_sign_in_reports_the_name_and_publishes_the_health(tmp_path: Path):
    link, worker, _, _ = make(tmp_path, ["unreachable", "not_logged_in", "ok"])
    link.start_connect()
    await link._task
    assert link.state == {"state": "connected", "username": "digger", "error": None}
    # The sidebar dot reads this: a sign-in nobody publishes leaves it saying "starting" until the
    # worker's next scheduled probe.
    assert worker.status["lossless_provider"] == {"name": "soulseek", "status": "ok", "username": "digger"}
    assert len(worker.providers) == 1        # rebuilt against the key that was just written


async def test_a_login_the_server_keeps_refusing_names_the_likeliest_cause_without_asserting_it(tmp_path: Path):
    """Soulseek never says *why* a login failed, so the message offers the likely cause and a next step
    rather than claiming to know. A name already in use is the common one."""
    link, _, _, _ = make(tmp_path, ["not_logged_in"])
    link.start_connect()
    await link._task
    assert link.state["state"] == "failed" and link.state["error"] == TAKEN_HINT


async def test_an_unreachable_sidecar_does_not_blame_the_account(tmp_path: Path):
    link, _, _, _ = make(tmp_path, ["unreachable"])
    link.start_connect()
    await link._task
    assert link.state["state"] == "failed" and "didn't answer" in link.state["error"]
    assert TAKEN_HINT not in link.state["error"]


async def test_it_gives_up_rather_than_polling_for_ever(tmp_path: Path):
    link, _, provider, clock = make(tmp_path, ["not_logged_in"])
    link.start_connect()
    await link._task
    assert clock.t <= 46 and provider.calls <= 46        # CONNECT_TIMEOUT_S, one probe a second


async def test_the_old_sidecar_is_stopped_before_a_new_one_starts(tmp_path: Path):
    """Otherwise a copy under the previous credentials keeps the port and the new one never binds."""
    old = FakeProcess()
    link, _, _, _ = make(tmp_path, ["ok"])
    link.adopt(old)
    link.start_connect()
    await link._task
    assert old.stopped == 1 and link.process is not old


async def test_a_missing_sidecar_says_so_instead_of_failing_silently(tmp_path: Path):
    link, _, _, _ = make(tmp_path, ["ok"], installed=False)
    link.start_connect()
    await link._task
    assert link.state["state"] == "failed" and "isn't installed" in link.state["error"]


async def test_a_sidecar_that_will_not_start_is_reported_not_raised(tmp_path: Path):
    proc = FakeProcess()
    proc.start_error = SlskdBinaryError("did not become healthy")
    link, _, _, _ = make(tmp_path, ["ok"], process=proc)
    link.start_connect()
    await link._task
    assert link.state["state"] == "failed"


async def test_a_second_connect_while_one_is_running_does_not_start_another(tmp_path: Path):
    link, _, _, _ = make(tmp_path, ["not_logged_in", "not_logged_in", "ok"])
    first = link.start_connect()
    second = link.start_connect()
    assert first["state"] == second["state"] == "connecting"
    await link._task
    assert link.state["state"] == "connected"


@pytest.mark.parametrize("boom", [httpx.ConnectError("down"), OSError("closed")])
async def test_a_probe_that_raises_is_treated_as_not_yet_up(tmp_path: Path, boom):
    link, _, _, _ = make(tmp_path, [boom, boom, "ok"])
    link.start_connect()
    await link._task
    assert link.state["state"] == "connected"


async def test_a_rescan_reaches_every_provider_even_when_one_of_them_fails(tmp_path: Path):
    """Told the library folder moved, each network gets asked to rescan. One that cannot be reached must
    not stop the others, and nothing may escape: the caller is a settings save that has already been
    written, and slskd's own file watch plus the daily maintenance rescan are the backstop."""
    from flackey.source.lossless import LosslessError

    class Provider:
        def __init__(self, name, error=None):
            self.name, self.error, self.calls = name, error, 0

        async def rescan_shares(self):
            self.calls += 1
            if self.error is not None:
                raise self.error

    bad, good = Provider("bad", LosslessError("sidecar is not answering")), Provider("good")
    link, worker, _, _ = make(tmp_path, ["ok"])
    worker.providers = [bad, good]
    await link.rescan_shares()
    assert bad.calls == 1 and good.calls == 1
