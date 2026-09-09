import json
from pathlib import Path
from urllib.parse import quote

import httpx
import pytest
import respx

from krater.source.lossless import LosslessError, LosslessUnavailable, TransferProgress
from krater.source.slskd import SlskdClient, SoulseekProvider, local_path_for, parse_response

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
    from krater.lossless import LosslessFile
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


@respx.mock
async def test_health_reports_login_state_and_unreachable(provider, fixtures: Path):
    p, _, _ = provider
    respx.get(f"{BASE}/application").mock(return_value=httpx.Response(200, json=load(fixtures, "application.json")))
    assert await p.health() == {"status": "ok", "username": "krater-dj"}
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


def transfer(state: str, done: int, size: int = 51223918, username: str = "loginty",
             filename: str = "Musique\\Sorted\\Albums\\Hallucinogen\\Twisted\\02. Hallucinogen - Orphic Thrench.flac") -> dict:
    return {"username": username, "directories": [{"directory": "Musique\\Sorted\\Albums\\Hallucinogen\\Twisted",
            "files": [{"id": "t1", "username": username, "filename": filename, "size": size, "state": state,
                       "bytesTransferred": done, "averageSpeed": 3_000_000.0}]}]}


def flac_file(size: int = 51223918):
    from krater.lossless import LosslessFile
    return LosslessFile(provider="soulseek", username="loginty", extension="flac", size=size, length_s=442,
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
    enqueue = respx.post(f"{BASE}/transfers/downloads/loginty").mock(return_value=httpx.Response(201))
    dest = downloads / "Twisted" / "02. Hallucinogen - Orphic Thrench.flac"

    def land(_request):
        dest.parent.mkdir(exist_ok=True)
        dest.write_bytes(b"x" * f.size)
        return httpx.Response(200, json=transfer("Completed, Succeeded", f.size))

    respx.get(f"{BASE}/transfers/downloads/loginty").mock(side_effect=[
        httpx.Response(200, json=transfer("Queued, Remotely", 0)),
        httpx.Response(200, json=transfer("InProgress", 1000)),
        land])
    seen: list[TransferProgress] = []
    raw = []
    got = await p.download(f, first_byte_s=60, total_s=600, poll_s=2, on_progress=seen.append, on_raw=lambda n, o: raw.append(n))
    assert got == dest.resolve()
    assert json.loads(enqueue.calls[0].request.content) == [{"filename": f.path, "size": f.size}]
    assert [s.state for s in seen] == ["Queued, Remotely", "InProgress", "Completed, Succeeded"]
    assert seen[0].first_byte_ms is None and seen[1].first_byte_ms == 4000 and seen[2].bytes == f.size
    assert raw == ["transfer-0", "transfer-1", "transfer-2"]


@respx.mock
async def test_download_first_byte_cap_cancels(provider):
    p, clock, _ = provider
    respx.post(f"{BASE}/transfers/downloads/loginty").mock(return_value=httpx.Response(201))
    respx.get(f"{BASE}/transfers/downloads/loginty").mock(return_value=httpx.Response(200, json=transfer("Queued, Remotely", 0)))
    cancel = respx.delete(f"{BASE}/transfers/downloads/loginty/t1").mock(return_value=httpx.Response(204))
    with pytest.raises(LosslessError) as e:
        await p.download(flac_file(), first_byte_s=60, total_s=600, poll_s=2)
    assert e.value.outcome == "first_byte_timeout" and cancel.called and cancel.calls[0].request.url.params["remove"] == "true"
    assert 60 <= clock.t <= 64


@respx.mock
async def test_download_total_cap_and_overrun_and_failure(provider):
    p, _clock, _ = provider
    f = flac_file()
    respx.post(f"{BASE}/transfers/downloads/loginty").mock(return_value=httpx.Response(201))
    respx.delete(f"{BASE}/transfers/downloads/loginty/t1").mock(return_value=httpx.Response(204))
    respx.get(f"{BASE}/transfers/downloads/loginty").mock(return_value=httpx.Response(200, json=transfer("InProgress", 100)))
    with pytest.raises(LosslessError) as e:
        await p.download(f, first_byte_s=60, total_s=10, poll_s=2)
    assert e.value.outcome == "transfer_timeout"
    respx.get(f"{BASE}/transfers/downloads/loginty").mock(return_value=httpx.Response(200, json=transfer("InProgress", f.size + 1)))
    with pytest.raises(LosslessError) as e:
        await p.download(f, first_byte_s=60, total_s=600, poll_s=2)
    assert e.value.outcome == "transfer_failed" and "bytes" in str(e.value)
    respx.get(f"{BASE}/transfers/downloads/loginty").mock(return_value=httpx.Response(200, json=transfer("Completed, Errored", 100)))
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

    respx.post(f"{BASE}/transfers/downloads/loginty").mock(side_effect=enqueue_side_effect)

    def land(_request):
        dest.write_bytes(b"fresh")     # the real transfer lands on the now-empty derived path
        return httpx.Response(200, json=transfer("Completed, Succeeded", f.size))

    respx.get(f"{BASE}/transfers/downloads/loginty").mock(side_effect=[land])
    got = await p.download(f, first_byte_s=60, total_s=600, poll_s=2)
    assert got == dest.resolve()
    assert dest.read_bytes() == b"fresh"


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
    respx.post(f"{BASE}/transfers/downloads/loginty").mock(return_value=httpx.Response(201))

    def land(_request):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"y" * 999)              # what slskd actually wrote: one byte fewer
        return httpx.Response(200, json=transfer("Completed, Succeeded", 999))

    respx.get(f"{BASE}/transfers/downloads/loginty").mock(side_effect=[land])
    assert await p.download(f, first_byte_s=5, total_s=10, poll_s=0) == dest


@respx.mock
async def test_a_completed_transfer_whose_file_is_the_wrong_size_fails_instead_of_being_returned(provider):
    p, _clock, downloads = provider
    f = flac_file(size=10)
    dest = downloads / "Twisted" / "02. Hallucinogen - Orphic Thrench.flac"
    respx.post(f"{BASE}/transfers/downloads/loginty").mock(return_value=httpx.Response(201))

    def land(_request):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"short")     # 5 bytes, not the 10 slskd told us the file should be
        return httpx.Response(200, json=transfer("Completed, Succeeded", f.size))

    respx.get(f"{BASE}/transfers/downloads/loginty").mock(side_effect=[land])
    with pytest.raises(LosslessError) as e:
        await p.download(f, first_byte_s=60, total_s=600, poll_s=2)
    assert e.value.outcome == "transfer_failed"
    assert "5" in str(e.value) and "10" in str(e.value)


@respx.mock
async def test_cancel_all_and_rescan(provider):
    p, _, _ = provider
    respx.get(f"{BASE}/transfers/downloads").mock(return_value=httpx.Response(200, json=[
        transfer("InProgress", 5), transfer("Completed, Succeeded", 10, username="other")]))
    cancel = respx.delete(f"{BASE}/transfers/downloads/loginty/t1").mock(return_value=httpx.Response(204))
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
