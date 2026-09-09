import os

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("KRATER_LIVE") != "1", reason="live test")


async def test_beatport_search_still_parses():
    from krater.catalog import BeatportCatalog, best_match
    from krater.models import Query

    q = Query(raw="astral projection into the void")
    tracks = await BeatportCatalog().search(q)
    assert any(t.id == 16552105 for t in tracks)
    assert best_match(q, tracks).id == 16552105
