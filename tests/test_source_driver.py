"""Covers DeezerBotSource.fetch's error mapping: findings that download_media used to sit outside
both the timeout and the UnauthorizedError -> SourceUnauthorized mapping (see final-fix-report)."""
import asyncio
from pathlib import Path

import pytest
from telethon.errors import UnauthorizedError

from flackey.config import Settings
from flackey.deezer import DeezerError, DeezerTrack
from flackey.models import Candidate, Query, RequestKind, RequestState
from flackey.notify import MemoryNotifier
from flackey.source.base import SourceTimeout, SourceUnauthorized
from flackey.source.deezer_bot import DeezerBotSource
from flackey.store import Store
from flackey.worker import LOGIN_REQUIRED, Worker

A_USER = object()      # what Telethon's get_me() answers while the session is alive


class FakeDocument:
    mime_type = "audio/mpeg"


class FakeResponseMessage:
    """What conv.get_response(menu_msg) returns: the bot's reply carrying the audio document."""
    document = FakeDocument()
    file = None


class FakeMenuMessage:
    """The cached menu message fetch() clicks on."""
    async def click(self, *args, **kwargs):
        pass


class FakeConversation:
    def __init__(self, response, error=None):
        self._response, self._error = response, error

    async def send_message(self, text):
        if self._error is not None:
            raise self._error

    async def get_response(self, anchor=None):
        if self._error is not None:
            raise self._error
        return self._response


class FakeConversationCtx:
    def __init__(self, conv):
        self._conv = conv

    async def __aenter__(self):
        return self._conv

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeClient:
    """Only the attributes DeezerBotSource touches: conversation() and download_media(), plus the three
    calls `probe_authorized` makes when a ConnectionError has to be classified. `me` is what Telethon's
    get_me() answers -- a user while the session is alive, None once Telegram has revoked it."""
    def __init__(self, response_message, download, conversation_error=None, me=A_USER,
                 connect_error=None):
        self._response_message = response_message
        self._download = download
        self._conversation_error = conversation_error
        self.me, self.connect_error = me, connect_error
        self.connected = True

    def conversation(self, username, timeout=None):
        return FakeConversationCtx(FakeConversation(self._response_message, self._conversation_error))

    async def download_media(self, msg, file=None):
        return await self._download(msg, file)

    # ---- what the probe calls -------------------------------------------
    def is_connected(self):
        return self.connected

    async def connect(self):
        if self.connect_error is not None:
            raise self.connect_error
        self.connected = True

    async def get_me(self):
        return self.me


def _candidate() -> Candidate:
    return Candidate(source="deezer_bot", source_ref="dz_track:1:send", artist="A", title="T", deezer_id=1)


async def test_fetch_maps_unauthorized_download_to_source_unauthorized(tmp_path: Path):
    async def download_raises_unauthorized(msg, file):
        raise UnauthorizedError(request=None, message="SESSION_REVOKED")

    client = FakeClient(FakeResponseMessage(), download_raises_unauthorized)
    source = DeezerBotSource(client, "bot", deezer=None)
    cand = _candidate()
    source._menus[cand.source_ref] = FakeMenuMessage()

    with pytest.raises(SourceUnauthorized):
        await source.fetch(cand, tmp_path)


async def test_fetch_download_hang_raises_source_timeout(tmp_path: Path):
    async def download_hangs(msg, file):
        await asyncio.sleep(10)

    client = FakeClient(FakeResponseMessage(), download_hangs)
    source = DeezerBotSource(client, "bot", deezer=None, fetch_timeout=0.05)
    cand = _candidate()
    source._menus[cand.source_ref] = FakeMenuMessage()

    with pytest.raises(SourceTimeout):
        await source.fetch(cand, tmp_path)


async def test_a_fetch_that_has_to_re_search_does_not_block_on_its_own_lock(tmp_path: Path):
    """There is one DM with the bot, so search and fetch hold a lock. `asyncio.Lock` is not reentrant, and
    fetch's menu-lost path runs a search: reaching it through the public method would have the fetch wait
    forever for a lock it is holding itself."""
    cand = _candidate()

    async def download(msg, file):
        Path(file).write_bytes(b"audio")

    source = DeezerBotSource(FakeClient(FakeResponseMessage(), download), "bot", deezer=None, fetch_timeout=1)
    searched = []

    async def fake_search(query):
        searched.append(query.raw)
        source._menus[cand.source_ref] = FakeMenuMessage()   # what a real search leaves behind
        return []

    source._search_held = fake_search
    got = await asyncio.wait_for(source.fetch(cand, tmp_path), timeout=1)

    assert searched == ["A T"]
    assert got.read_bytes() == b"audio"
    assert not source._bot.locked(), "the lock has to be given back, or the next track never runs"


async def test_the_bot_conversation_is_held_by_one_track_at_a_time(tmp_path: Path):
    """Telethon refuses a second exclusive conversation on the same chat, and the bot's replies carry no
    request id -- so this one stage really does have to take turns. The file transfer that follows does
    not: it is an ordinary download that says nothing to the bot, and holding the lock across it would put
    every track's bytes behind every other track's."""
    # Neither download may finish until both have started. If the lock covered the file transfer, the
    # second track could never reach its own download and this would time out.
    both_downloading = asyncio.Barrier(2)

    async def download(msg, file):
        Path(file).write_bytes(b"audio")
        await asyncio.wait_for(both_downloading.wait(), timeout=1)

    source = DeezerBotSource(FakeClient(FakeResponseMessage(), download), "bot", deezer=None, fetch_timeout=1)
    asking, peak = 0, 0
    original = source._ask_for_file

    async def watched(cand):
        nonlocal asking, peak
        asking, peak = asking + 1, max(peak, asking + 1)
        try:
            await asyncio.sleep(0)      # a chance for the other track to interleave, if it can
            return await original(cand)
        finally:
            asking -= 1

    source._ask_for_file = watched

    async def one(n: int):
        cand = Candidate(source="deezer_bot", source_ref=f"dz_track:{n}:send", artist="A", title="T", deezer_id=n)
        source._menus[cand.source_ref] = FakeMenuMessage()
        await source.fetch(cand, tmp_path)

    await asyncio.wait_for(asyncio.gather(one(1), one(2)), timeout=2)
    assert peak == 1, "two tracks were talking to the bot at once"
    assert not source._bot.locked()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["1.mp3", "2.mp3"]


# ---- enrichment -----------------------------------------------------------
# `_enrich` already fetches the Deezer record for every candidate's metadata, so whether Deezer has a
# 30 s sample costs nothing extra to remember -- and the page needs it before it renders, to decide
# whether to draw a play control at all.


class FakeDeezer:
    """Only the one method `_enrich` calls. `answer` is either a DeezerTrack or an exception to raise."""
    def __init__(self, answer):
        self._answer = answer

    async def track(self, track_id: int):
        if isinstance(self._answer, Exception):
            raise self._answer
        return self._answer


def _deezer_track(preview: str | None) -> DeezerTrack:
    return DeezerTrack(id=1, isrc="X", title="T", artist="A", album=None, duration_s=224,
                       release_date=None, title_version=None, preview_url=preview)


async def test_enrich_records_that_deezer_has_a_sample():
    source = DeezerBotSource(FakeClient(None, None), "bot",
                             deezer=FakeDeezer(_deezer_track("https://cdn/x.mp3")))
    assert (await source._enrich(_candidate())).has_preview is True


async def test_enrich_records_that_deezer_has_no_sample():
    source = DeezerBotSource(FakeClient(None, None), "bot", deezer=FakeDeezer(_deezer_track(None)))
    assert (await source._enrich(_candidate())).has_preview is False


async def test_enrich_leaves_the_sample_unknown_when_the_lookup_fails():
    """A lookup that never answered knows nothing. Recording False here would hide the play control on a
    candidate that does have a sample, and the owner would have no way to find out."""
    for err in (DeezerError("deezer http 500"), ValueError("not json")):
        source = DeezerBotSource(FakeClient(None, None), "bot", deezer=FakeDeezer(err))
        assert (await source._enrich(_candidate())).has_preview is None

# ---- a revoked session vs. a network blip (issue #91) --------------------
# Telethon's update loop disconnects the client itself when Telegram revokes the key, so every later
# call raises a plain builtin ConnectionError("Cannot send requests while disconnected") -- the same
# class a wifi drop raises. These cover both readings of it, at all three places this source talks to
# Telegram: the search conversation, the click that asks for the file, and the download.
DISCONNECTED = ConnectionError("Cannot send requests while disconnected")


def _revoked_client(**kw) -> FakeClient:
    """A client whose socket is down and whose session Telegram has thrown out: get_me() answers None."""
    client = FakeClient(FakeResponseMessage(), kw.pop("download", None), me=None, **kw)
    client.connected = False
    return client


async def test_search_on_a_revoked_session_pauses_the_worker():
    source = DeezerBotSource(_revoked_client(conversation_error=DISCONNECTED), "bot", deezer=None)
    with pytest.raises(SourceUnauthorized):
        await source.search(Query(raw="a t"))


async def test_search_on_a_network_blip_stays_retryable():
    """The regression guard. A ConnectionError is what a wifi drop raises too, and mapping those to
    SourceUnauthorized would pause the worker and raise a "sign in again" banner over a session that
    is perfectly fine -- a worse bug than the one being fixed."""
    # The probe reaches Telegram and the session is alive: the call failed for some other reason.
    alive = FakeClient(FakeResponseMessage(), None, conversation_error=DISCONNECTED, me=A_USER)
    alive.connected = False
    with pytest.raises(SourceTimeout):
        await DeezerBotSource(alive, "bot", deezer=None).search(Query(raw="a t"))

    # The probe cannot reach Telegram either, so nothing says the session is gone.
    unreachable = FakeClient(FakeResponseMessage(), None, conversation_error=DISCONNECTED,
                             connect_error=ConnectionError("no route"))
    unreachable.connected = False
    with pytest.raises(SourceTimeout):
        await DeezerBotSource(unreachable, "bot", deezer=None).search(Query(raw="a t"))


async def test_the_click_that_asks_for_the_file_classifies_a_disconnect(tmp_path: Path):
    """_ask_for_file runs inside fetch's try, so its ConnectionError lands in the same clauses."""
    cand = _candidate()
    revoked = _revoked_client(conversation_error=DISCONNECTED)
    source = DeezerBotSource(revoked, "bot", deezer=None)
    source._menus[cand.source_ref] = FakeMenuMessage()
    with pytest.raises(SourceUnauthorized):
        await source.fetch(cand, tmp_path)

    alive = FakeClient(FakeResponseMessage(), None, conversation_error=DISCONNECTED, me=A_USER)
    alive.connected = False
    source = DeezerBotSource(alive, "bot", deezer=None)
    source._menus[cand.source_ref] = FakeMenuMessage()
    with pytest.raises(SourceTimeout):
        await source.fetch(cand, tmp_path)


async def test_a_download_that_loses_the_connection_is_classified(tmp_path: Path):
    async def download_disconnected(msg, file):
        raise DISCONNECTED

    cand = _candidate()
    revoked = _revoked_client(download=download_disconnected)
    source = DeezerBotSource(revoked, "bot", deezer=None)
    source._menus[cand.source_ref] = FakeMenuMessage()
    with pytest.raises(SourceUnauthorized):
        await source.fetch(cand, tmp_path)

    alive = FakeClient(FakeResponseMessage(), download_disconnected, me=A_USER)
    alive.connected = False
    source = DeezerBotSource(alive, "bot", deezer=None)
    source._menus[cand.source_ref] = FakeMenuMessage()
    with pytest.raises(SourceTimeout):
        await source.fetch(cand, tmp_path)


async def test_a_disk_failure_during_a_download_is_not_a_sign_in_problem(tmp_path: Path):
    """OSError is ConnectionError's parent and a full disk raises it; only the connection failures go
    through the probe, so this must surface as itself rather than as a revoked session."""
    async def download_out_of_space(msg, file):
        raise OSError(28, "No space left on device")

    cand = _candidate()
    source = DeezerBotSource(_revoked_client(download=download_out_of_space), "bot", deezer=None)
    source._menus[cand.source_ref] = FakeMenuMessage()
    with pytest.raises(OSError, match="No space"):
        await source.fetch(cand, tmp_path)


# ---- what the worker makes of it (issue #91) -----------------------------
class _NoCatalog:
    async def search(self, query):
        return []


def _worker(tmp_path: Path, source: DeezerBotSource, status: dict) -> Worker:
    settings = Settings(_env_file=None, telegram_api_id=1, telegram_api_hash="h",
                        library_root=tmp_path / "lib", data_dir=tmp_path / "data")
    return Worker(Store(settings.db_path), source, _NoCatalog(), MemoryNotifier(), settings, status=status)


async def test_a_revoked_session_keeps_the_request_queued_instead_of_burning_it(tmp_path: Path):
    """The bug as it was reported: nineteen requests went to `error` and the sidebar stayed green,
    because the disconnect reached the worker as a generic exception."""
    status = {"telegram_authorized": True}
    source = DeezerBotSource(_revoked_client(conversation_error=DISCONNECTED), "bot", deezer=None)
    w = _worker(tmp_path, source, status)
    rid = w.store.add_request("astral projection into the void", RequestKind.TEXT)

    r = await w.process(rid)

    assert r.state == RequestState.QUEUED
    assert r.flag_reason == LOGIN_REQUIRED
    assert r.attempts == 0, "a revoked session is not the request's fault; it must not spend the ladder"
    assert status["telegram_authorized"] is False, "the flag the banner and the sidebar read"


async def test_a_network_blip_leaves_the_session_alone_and_retries(tmp_path: Path):
    """The same failure with a live session behind it: the request goes back on the quick ladder and
    the app keeps saying it is connected. Asserting the exception type alone would miss this -- a
    transient error the worker does not recognise still burns the row into `error`."""
    status = {"telegram_authorized": True}
    alive = FakeClient(FakeResponseMessage(), None, conversation_error=DISCONNECTED, me=A_USER)
    alive.connected = False
    w = _worker(tmp_path, DeezerBotSource(alive, "bot", deezer=None), status)
    rid = w.store.add_request("astral projection into the void", RequestKind.TEXT)

    r = await w.process(rid)

    assert r.state == RequestState.QUEUED and r.retry_after is not None
    assert r.attempts == 1, "a blip is a failed attempt, and the ladder is what retries it"
    assert status["telegram_authorized"] is True
