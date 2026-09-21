from pathlib import Path

from flackey.lossless import HARD_RULES, IDENTITY_RANKERS, PEER_RANKERS, PickPolicy, Reference, pick
from flackey.models import RequestKind
from flackey.replay import render, run
from flackey.store import Store
from tests.test_lossless import mk


def test_replay_reruns_stored_reports_with_the_given_rules(tmp_path: Path):
    store = Store(tmp_path / "t.sqlite")
    rid = store.add_request("Hallucinogen - Orphic Thrench", RequestKind.YT_TRACK)
    store.update_request(rid, query_duration_s=442)
    ref = Reference("Hallucinogen", "Orphic Thrench", "Original Mix", 442, None, 442)
    # the stored report: today's rules rejected the 600 s file on duration, nothing survived
    report = pick([mk(username="far", length_s=600)], ref, PickPolicy())
    aid = store.add_attempt(rid, "soulseek", "Hallucinogen Orphic Thrench")
    store.update_attempt(aid, outcome="no_pick", report=report.to_dict())
    rows = run(store, rules=HARD_RULES, rankers=IDENTITY_RANKERS + PEER_RANKERS)
    assert len(rows) == 1 and rows[0].survivors == 1 and rows[0].top[0][1:] == (600, 158) and rows[0].asked_s == 442
    assert "| 158 |" in render(rows)
