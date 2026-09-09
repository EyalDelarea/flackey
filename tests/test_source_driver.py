"""Covers DeezerBotSource.fetch's error mapping: findings that download_media used to sit outside
both the timeout and the UnauthorizedError -> SourceUnauthorized mapping (see final-fix-report)."""
import asyncio
from pathlib import Path

import pytest
from telethon.errors import UnauthorizedError

from krater.models import Candidate
from krater.source.base import SourceTimeout, SourceUnauthorized
from krater.source.deezer_bot import DeezerBotSource


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
