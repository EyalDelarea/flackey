"""Covers DeezerBotSource.fetch's error mapping: findings that download_media used to sit outside
both the timeout and the UnauthorizedError -> SourceUnauthorized mapping (see final-fix-report)."""
import asyncio
from pathlib import Path

import pytest
from telethon.errors import UnauthorizedError

from flackey.deezer import DeezerError, DeezerTrack
from flackey.models import Candidate
from flackey.source.base import SourceTimeout, SourceUnauthorized
from flackey.source.deezer_bot import DeezerBotSource


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
    def __init__(self, response):
        self._response = response

    async def get_response(self, anchor=None):
        return self._response


class FakeConversationCtx:
    def __init__(self, conv):
        self._conv = conv

    async def __aenter__(self):
        return self._conv

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeClient:
    """Only the attributes DeezerBotSource.fetch touches: conversation() and download_media()."""
    def __init__(self, response_message, download):
        self._response_message = response_message
        self._download = download

    def conversation(self, username, timeout=None):
        return FakeConversationCtx(FakeConversation(self._response_message))

    async def download_media(self, msg, file=None):
        return await self._download(msg, file)


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
