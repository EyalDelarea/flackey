import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("FLACKEY_LIVE") != "1", reason="live test")


async def test_search_and_fetch_astral(tmp_path: Path):
    from telethon import TelegramClient

    from flackey.config import load_settings
    from flackey.deezer import DeezerApi
    from flackey.models import Query
    from flackey.source.deezer_bot import DeezerBotSource

    s = load_settings(Path(".env"))
    client = TelegramClient(str(s.session_path), s.telegram_api_id, s.telegram_api_hash)
    await client.connect()
    assert await client.is_user_authorized()
    src = DeezerBotSource(client, s.source_bot_username, DeezerApi())
    cands = await src.search(Query(raw="astral projection into the void"))
    hit = next((c for c in cands if c.deezer_id == 1754956977), None)  # the bot's ordering is not guaranteed
    assert hit is not None and hit.isrc == "UKU932231081"
    path = await src.fetch(hit, tmp_path)
    assert path.suffix == ".mp3" and path.stat().st_size > 15_000_000
    await client.disconnect()
