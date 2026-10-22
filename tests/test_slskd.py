import asyncio
import json
from dataclasses import replace
from pathlib import Path
from urllib.parse import quote

import httpx
import pytest
import respx

from flackey.source.lossless import (
    LosslessError,
    LosslessUnavailable,
    SlowRule,
    TooSlow,
    TransferProgress,
)
from flackey.source.slskd import (
    SlskdClient,
    SoulseekProvider,
    TransferBudget,
    local_path_for,
    parse_response,
)

BASE = "http://slskd.test/api/v0"


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    async def sleep(self, s: float) -> None:
        self.t += s


def load(fixtures: Path, name: str):
    return json.loads((fixtures / "slskd" / name).read_text())


@pytest.fixture
def provider(tmp_path: Path):
    clock = Clock()
    http = httpx.AsyncClient()
    client = SlskdClient("http://slskd.test", "secret-key", http, sleep=clock.sleep, clock=clock)
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    return SoulseekProvider(client, downloads, sleep=clock.sleep, clock=clock), clock, downloads


def test_parse_response_derives_extension_and_peer_fields(fixtures: Path):
    resp = load(fixtures, "responses_completed.json")
    files = [f for r in resp for f in parse_response(r)]
    flacs = [f for f in files if f.extension == "flac"]
    assert len(flacs) == 7 and all(f.provider == "soulseek" for f in flacs)
    assert all(f.get("extension", "") == "" for r in resp for f in r["files"])  # slskd left every extension empty
    queued = next(f for f in flacs if f.username == "brunebrunberg")
    assert (queued.bit_depth, queued.sample_rate, queued.length_s) == (16, 44100, 442)
    assert queued.has_free_slot is False and queued.queue_length == 8 and queued.upload_speed_bps == 1445117
    assert queued.name == "02 Hallucinogen - Orphic Thrench.flac"


def test_local_path_is_derived_and_contained(tmp_path: Path):
    from flackey.lossless import LosslessFile
    base = {"provider": "soulseek", "username": "u", "extension": "flac", "size": 1, "length_s": 1,
            "bitrate_kbps": None, "sample_rate": None, "bit_depth": None, "has_free_slot": True,
            "upload_speed_bps": 0, "queue_length": 0}
    f = LosslessFile(path="Musique\\Sorted\\Albums\\Hallucinogen\\Twisted\\02. Hallucinogen - Orphic Thrench.flac", **base)
    assert local_path_for(tmp_path, f) == (tmp_path / "Twisted" / "02. Hallucinogen - Orphic Thrench.flac").resolve()
    bare = LosslessFile(path="song.flac", **base)
    assert local_path_for(tmp_path, bare) == (tmp_path / "song.flac").resolve()
    # only the last folder segment and the file name are used, so a traversal can only come from those two
    with pytest.raises(LosslessError):
        local_path_for(tmp_path, LosslessFile(path="..\\..\\x.flac", **base))          # folder segment ".."
    with pytest.raises(LosslessError):
        local_path_for(tmp_path, LosslessFile(path="C:\\Music\\..\\..\\x.flac", **base))
    with pytest.raises(LosslessError):
        local_path_for(tmp_path, LosslessFile(path="a\\..", **base))                    # name ".." resolves to the root


def test_local_path_mirrors_slskd_sanitizing_on_windows(tmp_path: Path):
    """slskd on Windows swaps the characters Windows forbids for `_` in the folder and the file name
    (FileSafety.SanitizeFilename in slskd 0.26.0); elsewhere it only forbids `/` and NUL, so a Mac keeps the
    peer's characters as they are."""
    from flackey.lossless import LosslessFile
    base = {"provider": "soulseek", "username": "u", "extension": "flac", "size": 1, "length_s": 1,
            "bitrate_kbps": None, "sample_rate": None, "bit_depth": None, "has_free_slot": True,
            "upload_speed_bps": 0, "queue_length": 0}
    f = LosslessFile(path='Music\\What? Live: "Mix"\\01. A|B <Edit>*.flac', **base)
    assert local_path_for(tmp_path, f, windows=True) == (
        tmp_path / "What_ Live_ _Mix_" / "01. A_B _Edit__.flac").resolve()
    assert local_path_for(tmp_path, f, windows=False) == (
        tmp_path / 'What? Live: "Mix"' / "01. A|B <Edit>*.flac").resolve()
    tab = LosslessFile(path="Dir\tX\\a\x01b.flac", **base)
    assert local_path_for(tmp_path, tab, windows=True) == (tmp_path / "Dir_X" / "a_b.flac").resolve()


@respx.mock
async def test_health_reports_login_state_and_unreachable(provider, fixtures: Path):
    p, _, _ = provider
    respx.get(f"{BASE}/application").mock(return_value=httpx.Response(200, json=load(fixtures, "application.json")))
    assert await p.health() == {"status": "ok", "username": "flackey-dj"}
    app = load(fixtures, "application.json")
    app["server"]["isLoggedIn"] = False
    respx.get(f"{BASE}/application").mock(return_value=httpx.Response(200, json=app))
    assert (await p.health())["status"] == "not_logged_in"
    respx.get(f"{BASE}/application").mock(side_effect=httpx.ConnectError("refused"))
    assert (await p.health())["status"] == "unreachable"


@respx.mock
async def test_search_waits_for_completion_retries_429_and_sends_key(provider, fixtures: Path):
    p, clock, _ = provider
    sid = "eca1dc3d-5356-4fcf-9714-a17530d25655"
    post = respx.post(f"{BASE}/searches").mock(side_effect=[httpx.Response(429), httpx.Response(200, json={"id": sid})])
    state = respx.get(f"{BASE}/searches/{sid}").mock(side_effect=[
        httpx.Response(200, json=load(fixtures, "search_in_progress.json")),
        httpx.Response(200, json=load(fixtures, "search_in_progress.json")),
        httpx.Response(200, json=load(fixtures, "search_completed.json"))])
    respx.get(f"{BASE}/searches/{sid}/responses").mock(return_value=httpx.Response(200, json=load(fixtures, "responses_completed.json")))
    delete = respx.delete(f"{BASE}/searches/{sid}").mock(return_value=httpx.Response(204))
    raw = []
    files = await p.search("Hallucinogen Orphic Thrench", wait_s=30, on_raw=lambda n, o: raw.append(n))
    assert len(files) == sum(len(r["files"]) for r in load(fixtures, "responses_completed.json"))
    assert post.call_count == 2 and state.call_count == 3 and delete.called
    assert json.loads(post.calls[1].request.content) == {"searchText": "Hallucinogen Orphic Thrench", "searchTimeout": 5000,
                                                         "responseLimit": 100}
    assert post.calls[1].request.headers["X-API-Key"] == "secret-key"
    assert raw == ["search", "responses"] and clock.t >= 1.0


async def test_searches_take_turns_and_each_one_gets_its_full_budget(provider):
    """slskd creates one search at a time and answers 429 to a concurrent POST. With the whole queue
    searching at once, leaving them all to retry on 429 would spend every request's search budget on
    retries -- so they queue, and the budget starts when a search does, not when it asked."""
    p, clock, _ = provider
    inside, peak, budgets = 0, 0, []

    async def held(text, *, wait_s, on_raw=None):
        nonlocal inside, peak
        inside, peak = inside + 1, max(peak, inside + 1)
        budgets.append((clock.t, wait_s))
        await clock.sleep(10)          # a search that takes ten seconds
        inside -= 1
        return []

    p._search_held = held
    await asyncio.gather(*(p.search("x", wait_s=30) for _ in range(3)))

    assert peak == 1, "three searches were being created at once; slskd answers 429 to that"
    assert [t for t, _ in budgets] == [0, 10, 20]
    assert [w for _, w in budgets] == [30, 30, 30], "waiting your turn must not eat your search time"


@respx.mock
async def test_search_gives_up_at_the_wait_cap(provider, fixtures: Path):
    p, clock, _ = provider
    sid = "s1"
    respx.post(f"{BASE}/searches").mock(return_value=httpx.Response(200, json={"id": sid}))
    respx.get(f"{BASE}/searches/{sid}").mock(return_value=httpx.Response(200, json=load(fixtures, "search_in_progress.json")))
    respx.get(f"{BASE}/searches/{sid}/responses").mock(return_value=httpx.Response(200, json=[]))
    delete = respx.delete(f"{BASE}/searches/{sid}").mock(return_value=httpx.Response(204))
    assert await p.search("x", wait_s=5) == []
    assert 5 <= clock.t < 7 and delete.called


@respx.mock
async def test_unreachable_sidecar_raises_unavailable(provider):
    p, _, _ = provider
    respx.post(f"{BASE}/searches").mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(LosslessUnavailable):
        await p.search("x", wait_s=5)
    respx.post(f"{BASE}/searches").mock(return_value=httpx.Response(502))
    with pytest.raises(LosslessUnavailable):
        await p.search("x", wait_s=5)


def transfer(state: str, done: int, size: int = 51223918, username: str = "peer-alpha",
             filename: str = "Musique\\Sorted\\Albums\\Hallucinogen\\Twisted\\02. Hallucinogen - Orphic Thrench.flac") -> dict:
    return {"username": username, "directories": [{"directory": "Musique\\Sorted\\Albums\\Hallucinogen\\Twisted",
            "files": [{"id": "t1", "username": username, "filename": filename, "size": size, "state": state,
                       "bytesTransferred": done, "averageSpeed": 3_000_000.0}]}]}


def flac_file(size: int = 51223918):
    from flackey.lossless import LosslessFile
    return LosslessFile(provider="soulseek", username="peer-alpha", extension="flac", size=size, length_s=442,
                        path="Musique\\Sorted\\Albums\\Hallucinogen\\Twisted\\02. Hallucinogen - Orphic Thrench.flac",
                        bitrate_kbps=None, sample_rate=44100, bit_depth=16, has_free_slot=True,
                        upload_speed_bps=3_000_000, queue_length=0)


@respx.mock
async def test_enqueue_percent_encodes_a_hostile_username(provider):
    p, _, _ = provider
    evil = "evil/../admin"
    encoded = quote(evil, safe="")
    route = respx.post(f"{BASE}/transfers/downloads/{encoded}").mock(return_value=httpx.Response(201))
    await p.client.enqueue(evil, "song.flac", 1)
    assert route.called
    # the whole hostile string lands as one percent-encoded path segment on the wire, so it can't reach
    # another endpoint (request.url.path would decode %2F back to "/" and mislead this assertion)
    assert route.calls[0].request.url.raw_path == f"/api/v0/transfers/downloads/{encoded}".encode()


@respx.mock
async def test_download_polls_to_completion_and_reports_progress(provider):
    p, _clock, downloads = provider
    f = flac_file(size=1000)   # matches the "InProgress" 1000-bytes-done step below, and what `land` writes
    enqueue = respx.post(f"{BASE}/transfers/downloads/peer-alpha").mock(return_value=httpx.Response(201))
    dest = downloads / "Twisted" / "02. Hallucinogen - Orphic Thrench.flac"

    def land(_request):
        dest.parent.mkdir(exist_ok=True)
        dest.write_bytes(b"x" * f.size)
        return httpx.Response(200, json=transfer("Completed, Succeeded", f.size))

    respx.get(f"{BASE}/transfers/downloads/peer-alpha").mock(side_effect=[
        httpx.Response(200, json=transfer("Queued, Remotely", 0)),
        httpx.Response(200, json=transfer("InProgress", 1000)),
        land])
    seen: list[TransferProgress] = []
    raw = []
    got = await p.download(f, first_byte_s=60, total_s=600, poll_s=2, on_progress=seen.append, on_raw=lambda n, o: raw.append(n))
    # Off the derived path, in the same folder, still the same file: the download hands its caller a path
    # nothing else derives, so the next download of this track is free to clear the derived one.
    assert got != dest.resolve() and got.parent == dest.resolve().parent and got.name.endswith(dest.name)
    assert got.stat().st_size == f.size and not dest.exists()
    assert json.loads(enqueue.calls[0].request.content) == [{"filename": f.path, "size": f.size}]
    assert [s.state for s in seen] == ["Queued, Remotely", "InProgress", "Completed, Succeeded"]
    assert seen[0].first_byte_ms is None and seen[1].first_byte_ms == 4000 and seen[2].bytes == f.size
    assert raw == ["transfer-0", "transfer-1", "transfer-2"]


@respx.mock
async def test_cancel_all_leaves_this_runs_own_downloads_alone(provider):
    """The worker sweeps the previous run's transfers whenever slskd first answers, which can be after this
    run's first download went in: that one must survive the sweep."""
    p, _clock, downloads = provider
    f = flac_file(size=1000)
    respx.post(f"{BASE}/transfers/downloads/peer-alpha").mock(return_value=httpx.Response(201))
    dest = downloads / "Twisted" / "02. Hallucinogen - Orphic Thrench.flac"

    def land(_request):
        dest.parent.mkdir(exist_ok=True)
        dest.write_bytes(b"x" * f.size)
        return httpx.Response(200, json=transfer("Completed, Succeeded", f.size))

    respx.get(f"{BASE}/transfers/downloads/peer-alpha").mock(side_effect=[land])
    p.begin_run()
    await p.download(f, first_byte_s=60, total_s=600, poll_s=2)
    ours, old = transfer("InProgress", 5), transfer("InProgress", 5, username="other")
    respx.get(f"{BASE}/transfers/downloads").mock(return_value=httpx.Response(200, json=[ours, old]))
    mine = respx.delete(f"{BASE}/transfers/downloads/peer-alpha/t1").mock(return_value=httpx.Response(204))
    theirs = respx.delete(f"{BASE}/transfers/downloads/other/t1").mock(return_value=httpx.Response(204))
    assert await p.cancel_all() == 1 and theirs.called and not mine.called


def _landing(dest: Path, f):
    def land(_request):
        dest.parent.mkdir(exist_ok=True)
        dest.write_bytes(b"x" * f.size)
        return httpx.Response(200, json=transfer("Completed, Succeeded", f.size))
    return land


@respx.mock
async def test_a_restarted_worker_sweeps_what_the_crashed_run_left(provider):
    """`supervise_worker` restarts the loop on the same provider. The crashed run's transfers are the new
    run's leftovers, so they must not stay protected as "this run's own" (#178), and the record of what
    was enqueued must not outlive the sweep it is for."""
    p, _clock, downloads = provider
    f = flac_file(size=1000)
    respx.post(f"{BASE}/transfers/downloads/peer-alpha").mock(return_value=httpx.Response(201))
    dest = downloads / "Twisted" / "02. Hallucinogen - Orphic Thrench.flac"
    respx.get(f"{BASE}/transfers/downloads/peer-alpha").mock(side_effect=[_landing(dest, f)])
    listing = respx.get(f"{BASE}/transfers/downloads")
    cancel = respx.delete(f"{BASE}/transfers/downloads/peer-alpha/t1").mock(return_value=httpx.Response(204))

    p.begin_run()                                           # run 1
    await p.download(f, first_byte_s=60, total_s=600, poll_s=2)
    listing.mock(return_value=httpx.Response(200, json=[transfer("InProgress", 5)]))
    assert await p.cancel_all() == 0 and not cancel.called  # run 1's own transfer survives its late sweep
    assert not p._enqueued                                  # swept: nothing left to protect, nothing kept

    p.begin_run()                                           # run 2, after the crash
    assert await p.cancel_all() == 1 and cancel.called      # run 1's orphan is run 2's leftover


@respx.mock
async def test_a_restarted_run_keeps_its_own_transfer_through_a_late_sweep(provider):
    """Run 2 starts with slskd down, a track enqueues once it answers, then the retried sweep lands."""
    p, _clock, downloads = provider
    f = flac_file(size=1000)
    listing = respx.get(f"{BASE}/transfers/downloads")
    listing.mock(return_value=httpx.Response(200, json=[]))
    p.begin_run()
    assert await p.cancel_all() == 0                        # run 1 swept with nothing to do

    p.begin_run()                                           # run 2
    listing.mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(LosslessUnavailable):
        await p.cancel_all()
    respx.post(f"{BASE}/transfers/downloads/peer-alpha").mock(return_value=httpx.Response(201))
    dest = downloads / "Twisted" / "02. Hallucinogen - Orphic Thrench.flac"
    respx.get(f"{BASE}/transfers/downloads/peer-alpha").mock(side_effect=[_landing(dest, f)])
    await p.download(f, first_byte_s=60, total_s=600, poll_s=2)
    listing.mock(side_effect=None, return_value=httpx.Response(200, json=[transfer("InProgress", 5)]))
    mine = respx.delete(f"{BASE}/transfers/downloads/peer-alpha/t1").mock(return_value=httpx.Response(204))
    assert await p.cancel_all() == 0 and not mine.called


@respx.mock
async def test_a_provider_with_no_sweep_owed_records_nothing(provider):
    """One built after the sweep, by a reconnect with nothing pending: no sweep will ever clear its record."""
    p, _clock, downloads = provider
    f = flac_file(size=1000)
    respx.post(f"{BASE}/transfers/downloads/peer-alpha").mock(return_value=httpx.Response(201))
    dest = downloads / "Twisted" / "02. Hallucinogen - Orphic Thrench.flac"
    respx.get(f"{BASE}/transfers/downloads/peer-alpha").mock(side_effect=[_landing(dest, f)])
    await p.download(f, first_byte_s=60, total_s=600, poll_s=2)
    assert not p._enqueued


def test_a_rebuilt_provider_shares_the_record_of_the_one_it_replaces(provider, tmp_path: Path):
    """A reconnect rebuilds the provider while the old one's sweep is still owed: the new one sweeps, and
    must know what the old one enqueued -- and still enqueues, from passes already under way."""
    old, _clock, downloads = provider
    old.begin_run()
    old._enqueued.add(("peer-alpha", "a.flac"))
    new = SoulseekProvider(old.client, downloads)
    new.begin_run(previous=old)
    old._enqueued.add(("peer-beta", "b.flac"))
    assert new._enqueued == {("peer-alpha", "a.flac"), ("peer-beta", "b.flac")}


@respx.mock
async def test_download_first_byte_cap_cancels(provider):
    # "Initializing", not "Queued, Remotely": the transfer has left the peer's queue and still sends nothing,
    # which is the peer refusing rather than the peer being busy. See the queue tests at the end of the file.
    p, clock, _ = provider
    respx.post(f"{BASE}/transfers/downloads/peer-alpha").mock(return_value=httpx.Response(201))
    respx.get(f"{BASE}/transfers/downloads/peer-alpha").mock(return_value=httpx.Response(200, json=transfer("Initializing", 0)))
    cancel = respx.delete(f"{BASE}/transfers/downloads/peer-alpha/t1").mock(return_value=httpx.Response(204))
    with pytest.raises(LosslessError) as e:
        await p.download(flac_file(), first_byte_s=60, total_s=600, poll_s=2)
    assert e.value.outcome == "first_byte_timeout" and cancel.called and cancel.calls[0].request.url.params["remove"] == "true"
    assert 62 <= clock.t <= 66


@respx.mock
async def test_download_total_cap_and_overrun_and_failure(provider):
    p, _clock, _ = provider
    f = flac_file()
    respx.post(f"{BASE}/transfers/downloads/peer-alpha").mock(return_value=httpx.Response(201))
    respx.delete(f"{BASE}/transfers/downloads/peer-alpha/t1").mock(return_value=httpx.Response(204))
    respx.get(f"{BASE}/transfers/downloads/peer-alpha").mock(return_value=httpx.Response(200, json=transfer("InProgress", 100)))
    with pytest.raises(LosslessError) as e:
        await p.download(f, first_byte_s=60, total_s=10, poll_s=2)
    assert e.value.outcome == "transfer_timeout"
    respx.get(f"{BASE}/transfers/downloads/peer-alpha").mock(return_value=httpx.Response(200, json=transfer("InProgress", f.size + 1)))
    with pytest.raises(LosslessError) as e:
        await p.download(f, first_byte_s=60, total_s=600, poll_s=2)
    assert e.value.outcome == "transfer_failed" and "bytes" in str(e.value)
    respx.get(f"{BASE}/transfers/downloads/peer-alpha").mock(return_value=httpx.Response(200, json=transfer("Completed, Errored", 100)))
    with pytest.raises(LosslessError) as e:
        await p.download(f, first_byte_s=60, total_s=600, poll_s=2)
    assert e.value.outcome == "transfer_failed"


@respx.mock
async def test_a_stale_file_at_the_derived_path_is_removed_before_enqueue_so_the_fresh_download_wins(provider):
    p, _clock, downloads = provider
    f = flac_file(size=5)
    dest = downloads / "Twisted" / "02. Hallucinogen - Orphic Thrench.flac"
    dest.parent.mkdir(parents=True)
    dest.write_bytes(b"STALE")     # a leftover file from a previous, unrelated download at the same derived name

    def enqueue_side_effect(_request):
        assert not dest.exists()   # pre-clean already ran before slskd is even asked to start a transfer
        return httpx.Response(201)

    respx.post(f"{BASE}/transfers/downloads/peer-alpha").mock(side_effect=enqueue_side_effect)

    def land(_request):
        dest.write_bytes(b"fresh")     # the real transfer lands on the now-empty derived path
        return httpx.Response(200, json=transfer("Completed, Succeeded", f.size))

    respx.get(f"{BASE}/transfers/downloads/peer-alpha").mock(side_effect=[land])
    got = await p.download(f, first_byte_s=60, total_s=600, poll_s=2)
    assert got.read_bytes() == b"fresh"


@respx.mock
async def test_a_directory_at_the_derived_path_fails_the_download_instead_of_being_deleted(provider):
    p, _clock, downloads = provider
    f = flac_file()
    dest = downloads / "Twisted" / "02. Hallucinogen - Orphic Thrench.flac"
    dest.mkdir(parents=True)   # a peer filename collides with a folder name already on disk
    with pytest.raises(LosslessError) as e:
        await p.download(f, first_byte_s=60, total_s=600, poll_s=2)
    assert e.value.outcome == "transfer_failed"
    assert dest.is_dir()       # never deleted - only files are cleared, not directories


@respx.mock
async def test_a_peer_advertising_a_different_size_than_slskd_wrote_is_not_a_failure(provider):
    """The search response's size and slskd's own byte count describe different things. Only the second one
    measures the file on disk, so a shortfall against the peer's claim must not read as a stale file - the
    existing `done > file.size` guard already covers the overshoot direction."""
    p, _clock, downloads = provider
    f = flac_file(size=1000)                      # what the peer claimed in its search response
    dest = downloads / "Twisted" / "02. Hallucinogen - Orphic Thrench.flac"
    respx.post(f"{BASE}/transfers/downloads/peer-alpha").mock(return_value=httpx.Response(201))

    def land(_request):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"y" * 999)              # what slskd actually wrote: one byte fewer
        return httpx.Response(200, json=transfer("Completed, Succeeded", 999))

    respx.get(f"{BASE}/transfers/downloads/peer-alpha").mock(side_effect=[land])
    got = await p.download(f, first_byte_s=5, total_s=10, poll_s=0)
    assert got.read_bytes() == b"y" * 999


@respx.mock
async def test_a_completed_transfer_whose_file_is_the_wrong_size_fails_instead_of_being_returned(provider):
    p, _clock, downloads = provider
    f = flac_file(size=10)
    dest = downloads / "Twisted" / "02. Hallucinogen - Orphic Thrench.flac"
    respx.post(f"{BASE}/transfers/downloads/peer-alpha").mock(return_value=httpx.Response(201))

    def land(_request):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"short")     # 5 bytes, not the 10 slskd told us the file should be
        return httpx.Response(200, json=transfer("Completed, Succeeded", f.size))

    respx.get(f"{BASE}/transfers/downloads/peer-alpha").mock(side_effect=[land])
    with pytest.raises(LosslessError) as e:
        await p.download(f, first_byte_s=60, total_s=600, poll_s=2)
    assert e.value.outcome == "transfer_failed"
    assert "5" in str(e.value) and "10" in str(e.value)


@respx.mock
async def test_cancel_all_and_rescan(provider):
    p, _, _ = provider
    respx.get(f"{BASE}/transfers/downloads").mock(return_value=httpx.Response(200, json=[
        transfer("InProgress", 5), transfer("Completed, Succeeded", 10, username="other")]))
    cancel = respx.delete(f"{BASE}/transfers/downloads/peer-alpha/t1").mock(return_value=httpx.Response(204))
    assert await p.cancel_all() == 1 and cancel.called
    rescan = respx.put(f"{BASE}/shares").mock(return_value=httpx.Response(200))
    await p.rescan_shares()
    assert rescan.called


@respx.mock
async def test_uploads_flattens_the_envelope_and_reports_who_is_pulling(provider):
    """The uploads view answers "who is taking from my shared library". slskd returns the same
    user -> directories -> files envelope as downloads, so this flattens it the same way and keeps only
    fields the UI renders as text. Peer-chosen strings are never used to build a path here."""
    p, _, _ = provider
    respx.get("http://slskd.test/api/v0/transfers/uploads").mock(return_value=httpx.Response(200, json=[
        {"username": "digger", "directories": [{"directory": "DJ Library\\Astral Projection", "files": [
            {"id": "u1", "username": "digger", "filename": "DJ Library\\Astral Projection\\Dancing Galaxy.aiff",
             "size": 1000, "state": "InProgress", "bytesTransferred": 250, "averageSpeed": 1_500_000.0,
             "startedAt": "2026-09-08T02:00:00Z", "endedAt": None}]}]},
        {"username": "peer2", "directories": [{"directory": "DJ Library", "files": [
            {"id": "u2", "username": "peer2", "filename": "DJ Library\\x.flac", "size": 400, "state":
             "Completed, Succeeded", "bytesTransferred": 400, "averageSpeed": 900_000.0,
             "startedAt": "2026-09-08T01:00:00Z", "endedAt": "2026-09-08T01:00:04Z"}]}]},
    ]))
    rows = await p.uploads()
    assert [u["peer"] for u in rows] == ["digger", "peer2"]           # newest start first
    assert rows[0] == {"id": "u1", "peer": "digger", "file": "Dancing Galaxy.aiff",
                       "folder": "DJ Library/Astral Projection", "size": 1000, "bytes": 250, "pct": 25,
                       "state": "InProgress", "speed_bps": 1_500_000.0,
                       "started_at": "2026-09-08T02:00:00Z", "ended_at": None}
    assert rows[1]["pct"] == 100


@respx.mock
async def test_uploads_survives_an_empty_feed_and_a_zero_byte_file(provider):
    """No uploads yet is the normal state for a fresh install, and a zero-size entry must not divide by zero."""
    p, _, _ = provider
    respx.get("http://slskd.test/api/v0/transfers/uploads").mock(return_value=httpx.Response(200, json=[]))
    assert await p.uploads() == []
    respx.get("http://slskd.test/api/v0/transfers/uploads").mock(return_value=httpx.Response(200, json=[
        {"username": "p", "directories": [{"directory": "d", "files": [
            {"id": "z", "filename": "d\\a.flac", "size": 0, "state": "Queued", "bytesTransferred": 0}]}]}]))
    row = (await p.uploads())[0]
    assert row["pct"] == 0 and row["peer"] == "p" and row["file"] == "a.flac" and row["speed_bps"] == 0.0


async def test_two_downloads_deriving_the_same_path_take_it_in_turns(provider):
    """`_remove_stale_file` deletes whatever sits at the derived path before enqueueing. Two peers can offer
    the same folder and file name, and slskd writes both to the same place -- so without this the second
    download's pre-clean would delete the first one's file while it was still arriving."""
    p, _clock, downloads = provider
    dest = downloads / "Twisted" / "02. Hallucinogen - Orphic Thrench.flac"
    order = []

    async def hold(tag: str):
        async with p._path_lock(dest):
            order.append(f"{tag} in")
            await asyncio.sleep(0)
            order.append(f"{tag} out")

    await asyncio.gather(hold("a"), hold("b"))
    assert order == ["a in", "a out", "b in", "b out"]
    assert p._paths == {}, "the lock is dropped once nobody holds or wants it"


async def test_downloads_to_different_paths_do_not_wait_for_each_other(provider):
    """The whole point: two tracks from two peers have nothing to do with each other."""
    p, _clock, downloads = provider
    order = []

    async def hold(tag: str):
        async with p._path_lock(downloads / f"{tag}.flac"):
            order.append(f"{tag} in")
            await asyncio.sleep(0)
            order.append(f"{tag} out")

    await asyncio.gather(hold("a"), hold("b"))
    assert order == ["a in", "b in", "a out", "b out"]


@respx.mock
async def test_stopping_a_download_tells_the_sidecar_and_takes_the_partial_file_with_it(provider):
    """What the owner presses Stop for. Cancelling the task is not enough on its own: the peer keeps
    sending until slskd is told, and the half-file left at the derived path is the stale file the next
    download of the same track would have to clean up."""
    p, _clock, downloads = provider
    p.sleep = asyncio.sleep                       # a real suspension, so the cancel can land in the loop
    dest = downloads / "Twisted" / "02. Hallucinogen - Orphic Thrench.flac"
    dest.parent.mkdir()
    respx.post(f"{BASE}/transfers/downloads/peer-alpha").mock(return_value=httpx.Response(201))
    polled = asyncio.Event()

    def poll(_request):
        dest.write_bytes(b"half a file")
        polled.set()
        return httpx.Response(200, json=transfer("InProgress", 100))

    respx.get(f"{BASE}/transfers/downloads/peer-alpha").mock(side_effect=poll)
    cancel = respx.delete(f"{BASE}/transfers/downloads/peer-alpha/t1").mock(return_value=httpx.Response(204))

    task = asyncio.create_task(p.download(flac_file(), first_byte_s=60, total_s=600, poll_s=0.001))
    await asyncio.wait_for(polled.wait(), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert cancel.called and cancel.calls[0].request.url.params["remove"] == "true"
    assert not dest.exists()
    assert p._paths == {}, "a cancelled download still hands back the path it was holding"


@respx.mock
async def test_time_in_the_peers_queue_does_not_count_against_the_first_byte_cap(provider):
    """A peer with no free slot parks us in "Queued, Remotely" until their queue reaches us -- 127 people
    deep on the goa FLACs the owner asks for. That wait is a fact about the peer's popularity, not about
    whether they will send, so the first-byte cap only starts once the transfer leaves the queue."""
    p, _clock, downloads = provider
    f = flac_file(size=1000)
    respx.post(f"{BASE}/transfers/downloads/peer-alpha").mock(return_value=httpx.Response(201))
    dest = downloads / "Twisted" / "02. Hallucinogen - Orphic Thrench.flac"

    def land(_request):
        dest.parent.mkdir(exist_ok=True)
        dest.write_bytes(b"x" * f.size)
        return httpx.Response(200, json=transfer("Completed, Succeeded", f.size))

    queued = [httpx.Response(200, json=transfer("Queued, Remotely", 0))] * 60   # 120 s at poll_s=2
    respx.get(f"{BASE}/transfers/downloads/peer-alpha").mock(side_effect=[
        *queued, httpx.Response(200, json=transfer("InProgress", 500)), land])
    got = await p.download(f, first_byte_s=60, total_s=600, poll_s=2, queue_wait_s=600)
    # `_claim` moves the finished file off the derived path, so compare on the name, not the path.
    assert got.parent == dest.resolve().parent and got.name.endswith(dest.name)


@respx.mock
async def test_a_queue_that_never_reaches_us_is_queued_not_a_refusal(provider):
    p, clock, _ = provider
    respx.post(f"{BASE}/transfers/downloads/peer-alpha").mock(return_value=httpx.Response(201))
    respx.get(f"{BASE}/transfers/downloads/peer-alpha").mock(
        return_value=httpx.Response(200, json=transfer("Queued, Remotely", 0)))
    cancel = respx.delete(f"{BASE}/transfers/downloads/peer-alpha/t1").mock(return_value=httpx.Response(204))
    with pytest.raises(LosslessError) as e:
        await p.download(flac_file(), first_byte_s=60, total_s=600, poll_s=2, queue_wait_s=300)
    assert e.value.outcome == "queued" and cancel.called
    assert 300 <= clock.t <= 304


@respx.mock
async def test_a_transfer_that_leaves_the_queue_and_sends_nothing_is_still_a_first_byte_timeout(provider):
    p, clock, _ = provider
    respx.post(f"{BASE}/transfers/downloads/peer-alpha").mock(return_value=httpx.Response(201))
    respx.get(f"{BASE}/transfers/downloads/peer-alpha").mock(side_effect=[
        httpx.Response(200, json=transfer("Queued, Remotely", 0)),
        *([httpx.Response(200, json=transfer("Initializing", 0))] * 200)])
    cancel = respx.delete(f"{BASE}/transfers/downloads/peer-alpha/t1").mock(return_value=httpx.Response(204))
    with pytest.raises(LosslessError) as e:
        await p.download(flac_file(), first_byte_s=60, total_s=600, poll_s=2, queue_wait_s=300)
    assert e.value.outcome == "first_byte_timeout" and cancel.called
    assert 62 <= clock.t <= 66     # the cap runs from leaving the queue at t=2, not from the enqueue

@respx.mock
async def test_a_peer_still_sending_is_never_cut_off_for_taking_a_long_time(provider):
    """The flat transfer cap measured how long the download had been running, so a 67 MB FLAC arriving
    honestly at 100 kB/s -- the real Filteria case -- was cancelled at 90 %. Steady progress is the one
    thing that says a transfer is alive; length is not."""
    p, clock, downloads = provider
    f = flac_file(size=1000)
    dest = downloads / "Twisted" / "02. Hallucinogen - Orphic Thrench.flac"
    respx.post(f"{BASE}/transfers/downloads/peer-alpha").mock(return_value=httpx.Response(201))

    def land(_request):
        dest.parent.mkdir(exist_ok=True)
        dest.write_bytes(b"x" * f.size)
        return httpx.Response(200, json=transfer("Completed, Succeeded", f.size))

    # 200 polls = 400 s of steady progress: far past the 60 s that would have been the whole budget
    crawling = [httpx.Response(200, json=transfer("InProgress", n)) for n in range(1, 201)]
    respx.get(f"{BASE}/transfers/downloads/peer-alpha").mock(side_effect=[*crawling, land])
    got = await p.download(f, first_byte_s=60, total_s=6000, poll_s=2, stall_s=30)
    # `_claim` moves the finished file off the derived path, so compare on the name, not the path.
    assert got.parent == dest.resolve().parent and got.name.endswith(dest.name)
    assert clock.t > 400


@respx.mock
async def test_a_peer_that_stops_sending_midway_is_cut_at_the_stall_bound(provider):
    """The other half: what the flat cap was reaching for is a transfer that died mid-flight, and that is
    now measured from the last byte that arrived rather than from the enqueue."""
    p, clock, _ = provider
    respx.post(f"{BASE}/transfers/downloads/peer-alpha").mock(return_value=httpx.Response(201))
    respx.get(f"{BASE}/transfers/downloads/peer-alpha").mock(side_effect=[
        httpx.Response(200, json=transfer("InProgress", 1)),
        *([httpx.Response(200, json=transfer("InProgress", 500))] * 200)])
    cancel = respx.delete(f"{BASE}/transfers/downloads/peer-alpha/t1").mock(return_value=httpx.Response(204))
    with pytest.raises(LosslessError) as e:
        await p.download(flac_file(), first_byte_s=60, total_s=6000, poll_s=2, stall_s=30)
    assert e.value.outcome == "transfer_timeout" and "stopped" in str(e.value) and cancel.called
    assert 32 <= clock.t <= 38     # 30 s after the last new byte at t=4, not 6000 s after the enqueue


# ---- TransferBudget: the four timeouts on their own, against explicit clock readings ----

def budget(**kw) -> TransferBudget:
    return TransferBudget(t0=100.0, **{"first_byte_s": 60, "total_s": 600, "queue_wait_s": 300, "stall_s": 30, **kw})


def test_budget_a_transfer_slskd_never_lists_times_out_at_the_first_byte_bound():
    b = budget()
    assert b.never_appeared(160.0) is None
    err = b.never_appeared(160.5)
    assert err.outcome == "first_byte_timeout" and "never appeared" in str(err)


def test_budget_queue_time_is_its_own_bound_and_says_queued():
    b = budget()
    b.observe(110.0, 0, "Queued, Remotely")
    assert b.active_at is None
    assert b.expired(399.0, "Queued, Remotely", 0, 1000) is None      # past first_byte_s, still only queued
    err = b.expired(400.5, "Queued, Remotely", 0, 1000)
    assert err.outcome == "queued" and str(err) == "still queued, remotely after 300 s"


def test_budget_first_byte_counts_from_leaving_the_queue():
    b = budget()
    b.observe(350.0, 0, "InProgress")
    assert b.active_at == 350.0
    assert b.expired(410.0, "InProgress", 0, 1000) is None
    err = b.expired(410.5, "InProgress", 0, 1000)
    assert err.outcome == "first_byte_timeout" and str(err) == "no bytes within 60 s"


def test_budget_a_moving_transfer_is_cut_only_when_the_bytes_stop():
    b = budget()
    b.observe(110.0, 10, "InProgress")
    assert b.first_byte_ms == 10_000
    b.observe(130.0, 20, "InProgress")
    b.observe(150.0, 20, "InProgress")                                  # no new byte: moved_at stays 130
    assert b.moved_at == 130.0 and b.expired(160.0, "InProgress", 20, 1000) is None
    err = b.expired(160.5, "InProgress", 20, 1000)
    assert err.outcome == "transfer_timeout" and str(err) == "stopped sending after 20 of 1000 bytes"


def test_budget_the_total_ceiling_catches_a_peer_that_trickles_forever():
    b = budget(total_s=100)
    for t in range(110, 211, 10):
        b.observe(float(t), t, "InProgress")                           # a new byte every poll: never stalls
    assert b.expired(210.0, "InProgress", 210, 1000) is None
    b.observe(210.5, 211, "InProgress")
    err = b.expired(210.5, "InProgress", 211, 1000)
    assert err.outcome == "transfer_timeout" and str(err) == "not finished within 100 s"


# ---- the too-slow rule: a moving transfer measured against the next pick ----

RULE = SlowRule(grace_s=30, finish_s=240, spare_from=0.75, max_rate_bps=200_000)
MB = 1_000_000


def moving(rule: SlowRule | None = RULE, *, first: int = 10_000) -> TransferBudget:
    """First byte at t=110, `first` bytes in by then."""
    b = budget(total_s=6000, slow=rule)
    b.observe(110.0, first, "InProgress")
    return b


def test_budget_a_slow_transfer_is_given_up_once_its_grace_has_passed():
    b = moving()
    b.observe(139.0, 39_000, "InProgress")
    assert b.expired(139.0, "InProgress", 39_000, MB) is None          # 29 s of bytes: too early to say
    b.observe(140.0, 40_000, "InProgress")
    err = b.expired(140.0, "InProgress", 40_000, MB)                   # 1 kB/s, 960 s to go
    assert isinstance(err, TooSlow) and err.outcome == "too_slow"
    assert (err.pct, err.speed_bps, err.left_s) == (4, 1000.0, 960.0)
    assert str(err) == "1 kB/s at 4 %: 960 s more to finish, past the 240 s the next copy is worth"


@pytest.mark.parametrize("rule, first, done", [
    (None, 10_000, 40_000),                                            # no next pick: slow beats nothing
    (RULE, 10_000, 310_000),                                           # 10 kB/s: 69 s to go
    (RULE, 760_000, 761_000),                                          # three quarters in: finish it
    (SlowRule(grace_s=30, finish_s=240, spare_from=0.75, max_rate_bps=1000), 10_000, 40_000),  # not below cap
    (RULE, 10_000, 10_000),                                            # nothing since the first byte: a stall
], ids=["no-rule", "fast-enough", "nearly-done", "at-the-rate-cap", "stalled"])
def test_budget_the_slow_rule_leaves_these_alone(rule, first, done):
    b = moving(rule, first=first)
    b.observe(140.0, done, "InProgress")
    assert b.too_slow(140.0, done, MB) is None and b.expired(140.0, "InProgress", done, MB) is None


def test_budget_a_grace_of_nothing_does_not_divide_by_it():
    b = moving(SlowRule(grace_s=0, finish_s=240, spare_from=0.75, max_rate_bps=200_000))
    assert b.too_slow(110.0, 10_000, MB) is None                        # the first byte's own poll


def test_the_slow_settings_refuse_a_grace_of_nothing():
    from pydantic import ValidationError

    from flackey.config import Settings
    with pytest.raises(ValidationError):
        Settings(_env_file=None, lossless_slow_grace_s=0)


def _play(rule: SlowRule, size: int, rate: float) -> TooSlow | None:
    """A steady transfer of `size` at `rate` bytes/s, polled every 2 s from its first byte to its end."""
    b = budget(total_s=1e9, slow=rule)
    t, done = 100.0, 0
    while done < size:
        t, done = t + 2, min(size, int(done + 2 * rate))
        b.observe(t, done, "InProgress")
        if (err := b.expired(t, "InProgress", done, size)) is not None:
            return err
    return None


def _next(size: int, speed: int):
    return replace(flac_file(size=size), username="next", upload_speed_bps=speed)


def test_the_case_this_was_written_for_switches():
    """2026-10-22: 106 MB at 125 kB/s, 14 minutes. A free-slot peer advertising 1 MB/s projects to 166 s."""
    from flackey.config import Settings
    from flackey.worker.lossless_attempt import slow_rule_for
    err = _play(slow_rule_for(_next(106 * MB, 1_000_000), Settings(_env_file=None)), 106 * MB, 125_000)
    assert isinstance(err, TooSlow) and err.pct == 4 and round(err.left_s) == 816


@pytest.mark.parametrize("size, rate, next_speed", [
    (120 * MB, 400_000, 3_000_000),       # a good home peer on a big 24-bit file: above the 200 kB/s cap
    (40 * MB, 150_000, 3_000_000),        # a 40 MB file is safe from ~148 kB/s (40 MB in 270 s)
    (106 * MB, 125_000, 300_000),         # the next peer's word is not clearly better: 2 x 413 s > 816 s
    (106 * MB, 125_000, 200_000),         # nor is ours below half of it
], ids=["big-file-400kBps", "40MB-150kBps", "next-not-much-faster", "next-not-twice-as-fast"])
def test_a_transfer_that_is_not_clearly_worse_than_the_next_copy_is_kept(size, rate, next_speed):
    from flackey.config import Settings
    from flackey.worker.lossless_attempt import slow_rule_for
    assert _play(slow_rule_for(_next(size, next_speed), Settings(_env_file=None)), size, rate) is None


def _slow_poll(downloads: Path):
    respx.post(f"{BASE}/transfers/downloads/peer-alpha").mock(return_value=httpx.Response(201))
    return downloads / "Twisted" / "02. Hallucinogen - Orphic Thrench.flac"


@respx.mock
async def test_a_slow_transfer_is_cancelled_in_slskd_and_leaves_nothing_behind(provider):
    p, clock, downloads = provider
    dest = _slow_poll(downloads)
    respx.get(f"{BASE}/transfers/downloads/peer-alpha").mock(side_effect=[
        httpx.Response(200, json=transfer("InProgress", 1000 * n)) for n in range(1, 200)])

    def cancel_as_it_lands(_request):
        dest.parent.mkdir(exist_ok=True)
        dest.write_bytes(b"x")                                         # landed between the poll and the cancel
        return httpx.Response(204)

    cancel = respx.delete(f"{BASE}/transfers/downloads/peer-alpha/t1").mock(side_effect=cancel_as_it_lands)
    with pytest.raises(TooSlow) as e:
        await p.download(flac_file(), first_byte_s=60, total_s=6000, poll_s=2, stall_s=60, slow=RULE)
    assert e.value.outcome == "too_slow" and cancel.calls[0].request.url.params["remove"] == "true"
    assert 32 <= clock.t <= 34 and not dest.exists()                  # 30 s after the first byte at t=2


@respx.mock
@pytest.mark.parametrize("answers, switched", [
    ([httpx.Response(500), httpx.Response(204)], True),                # one retry
    ([httpx.Response(404)], True),                                     # already gone: nothing to stop
    ([httpx.Response(500), httpx.Response(500)], False),
    ([httpx.ConnectError("down"), httpx.Response(409)], False),
], ids=["retried", "gone", "refused-twice", "unreachable-then-refused"])
async def test_a_slow_transfer_slskd_will_not_cancel_is_kept_to_the_end(provider, answers, switched):
    """Given up in name while slskd still runs it, it would compete with the pick it was given up for, and
    `cancel_all` never sweeps this run's own transfers. So it is kept, and nothing ends it as slow again."""
    p, _, downloads = provider
    dest = _slow_poll(downloads)
    f = flac_file(size=40_000)

    def land(_request):
        dest.parent.mkdir(exist_ok=True)
        dest.write_bytes(b"x" * f.size)
        return httpx.Response(200, json=transfer("Completed, Succeeded", f.size, size=f.size))

    crawling = [httpx.Response(200, json=transfer("InProgress", 100 * n, size=f.size)) for n in range(1, 60)]
    respx.get(f"{BASE}/transfers/downloads/peer-alpha").mock(side_effect=[*crawling, land])
    cancel = respx.delete(f"{BASE}/transfers/downloads/peer-alpha/t1").mock(side_effect=answers)
    if switched:
        with pytest.raises(TooSlow):
            await p.download(f, first_byte_s=60, total_s=6000, poll_s=2, stall_s=60, slow=RULE)
    else:
        got = await p.download(f, first_byte_s=60, total_s=6000, poll_s=2, stall_s=60, slow=RULE)
        assert got.name.endswith(dest.name) and got.read_bytes() == b"x" * f.size
    assert cancel.call_count == len(answers)


@respx.mock
@pytest.mark.parametrize("after", [
    lambda f: httpx.Response(200, json=transfer("Completed, Cancelled", 3000, size=f.size)),
    lambda f: httpx.Response(200, json=[]),                            # removed after all: no longer listed
], ids=["cancelled", "gone"])
async def test_a_slow_transfer_slskd_stopped_despite_an_error_is_still_the_switch(provider, after):
    """The cancel answered 500 twice, so the transfer was kept -- but slskd had stopped it. Read as the
    `transfer_failed` a Cancelled state otherwise is, the copy would never be the attempt's fallback."""
    p, _, downloads = provider
    dest = _slow_poll(downloads)
    f = flac_file(size=40_000)
    crawling = [httpx.Response(200, json=transfer("InProgress", 100 * n, size=f.size)) for n in range(1, 17)]
    respx.get(f"{BASE}/transfers/downloads/peer-alpha").mock(side_effect=[*crawling, after(f)])
    respx.delete(f"{BASE}/transfers/downloads/peer-alpha/t1").mock(
        side_effect=[httpx.Response(500), httpx.Response(500)])
    with pytest.raises(TooSlow) as e:
        await p.download(f, first_byte_s=60, total_s=6000, poll_s=2, stall_s=60, slow=RULE)
    assert e.value.outcome == "too_slow" and e.value.pct == 4 and not dest.exists()


@respx.mock
async def test_the_replaced_providers_late_sweep_leaves_the_new_ones_record(provider):
    """The old provider's sweep can still land after the swap; the record it shares is the new one's now."""
    old, _clock, downloads = provider
    old.begin_run()
    new = SoulseekProvider(old.client, downloads)
    new.begin_run(previous=old)
    new._enqueued.add(("peer-alpha", "a.flac"))
    respx.get(f"{BASE}/transfers/downloads").mock(return_value=httpx.Response(200, json=[]))
    assert await old.cancel_all() == 0
    assert new._enqueued == {("peer-alpha", "a.flac")}
