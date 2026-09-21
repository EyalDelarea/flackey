from pathlib import Path

import pytest

from flackey.models import Candidate, CatalogTrack, Query, RequestKind, RequestState
from flackey.store import Store


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return Store(tmp_path / "t.sqlite")


def _catalog() -> CatalogTrack:
    return CatalogTrack(id=16552105, isrc="UKU932231081", artist="Astral Projection",
                        title="Into the Void", mix_name="Original Mix", label="Sacred Technology",
                        genre="Psy-Trance", sub_genre="Goa Trance", release_date="2022-06-03",
                        bpm=142, key="A Major", duration_ms=442816, artwork_url="https://x/a.jpg")


def test_request_lifecycle(store: Store):
    rid = store.add_request("astral projection into the void", RequestKind.TEXT,
                            query=Query(raw="x", artist="Astral Projection", title="Into the Void"))
    r = store.get_request(rid)
    assert r.state == RequestState.QUEUED
    assert r.query_artist == "Astral Projection"
    assert [q.id for q in store.due_queued()] == [rid]
    store.set_state(rid, RequestState.FETCHING)
    assert store.due_queued() == []
    assert store.reset_inflight() == 1
    assert store.get_request(rid).state == RequestState.QUEUED
    store.set_state(rid, RequestState.ERROR, error_message="boom")
    assert store.get_request(rid).error_message == "boom"
    store.update_request(rid, confidence=96, attempts=2)
    assert store.get_request(rid).confidence == 96


def test_due_queued_returns_the_whole_batch_the_worker_starts_at_once(store: Store):
    """The worker no longer takes one request and awaits it; it starts everything that is due. A row on a
    retry backoff is not due, and nothing that has left the queue is either."""
    a = store.add_request("a", RequestKind.TEXT)
    b = store.add_request("b", RequestKind.TEXT)
    c = store.add_request("c", RequestKind.TEXT)
    d = store.add_request("d", RequestKind.TEXT)
    store.update_request(c, retry_after="2999-01-01T00:00:00+00:00")
    store.set_state(d, RequestState.FETCHING)

    assert [r.id for r in store.due_queued()] == [a, b]
    assert [r.id for r in store.due_queued(limit=1)] == [a]
    store.update_request(c, retry_after="2000-01-01T00:00:00+00:00")
    assert [r.id for r in store.due_queued()] == [a, b, c]


def test_open_request_for_url(store: Store):
    url = "https://www.youtube.com/watch?v=a"
    assert store.open_request_for_url(url) is None
    rid = store.add_request("x", RequestKind.YT_TRACK, source_url=url)
    assert store.open_request_for_url(url).id == rid
    store.set_state(rid, RequestState.FETCHING)
    assert store.open_request_for_url(url).id == rid
    store.set_state(rid, RequestState.DONE)
    assert store.open_request_for_url(url) is None


def test_find_track_by_path(store: Store, tmp_path):
    tid = store.add_track(path=tmp_path / "a.mp3", fmt="mp3", bitrate_kbps=320, cutoff_hz=19800,
                          file_size=1, artist="A", title="T", mix_name="Original Mix", duration_s=1,
                          isrc=None, catalog_track_id=None, request_id=None)
    assert store.find_track_by_path(tmp_path / "a.mp3").id == tid
    assert store.find_track_by_path(tmp_path / "b.mp3") is None


def test_candidates_roundtrip(store: Store):
    rid = store.add_request("q", RequestKind.TEXT)
    saved = store.add_candidates(rid, [
        Candidate(source="deezer_bot", source_ref="dz_track:1:send", artist="A", title="T",
                  mix_name="Original Mix", duration_s=442, deezer_id=1, isrc="X", rank=1, score=96),
        Candidate(source="deezer_bot", source_ref="dz_track:2:send", artist="A", title="T (Remix)", rank=2),
    ])
    assert [c.id for c in saved] == [1, 2]
    got = store.get_candidates(rid)
    assert got[0].isrc == "X" and got[0].score == 96 and got[1].mix_name is None
    assert store.get_candidate(2).source_ref == "dz_track:2:send"


def test_catalog_upsert(store: Store):
    ct = _catalog()
    store.upsert_catalog_track(ct)
    ct.bpm = 143
    store.upsert_catalog_track(ct)
    assert store.get_catalog_track(16552105).bpm == 143
    assert store.get_catalog_track(1) is None


def test_tracks_and_duplicates(store: Store, tmp_path: Path):
    store.upsert_catalog_track(_catalog())
    tid = store.add_track(path=tmp_path / "a.mp3", fmt="mp3", bitrate_kbps=320, cutoff_hz=19800,
                          file_size=100, artist="Astral Projection", title="Into the Void",
                          mix_name="Original Mix", duration_s=443, isrc="UKU932231081",
                          catalog_track_id=16552105, request_id=None)
    assert store.find_track_by_isrc("UKU932231081").id == tid
    assert store.find_track_by_isrc("nope") is None
    assert store.find_track_by_meta("astral projection", "into the void", "original mix", 445).id == tid
    assert store.find_track_by_meta("astral projection", "into the void", "original mix", 460) is None
    assert store.find_track_by_meta("astral projection", "into the void", "vini vici remix", 443) is None
    assert store.list_tracks(search="void")[0].id == tid
    assert store.list_tracks(search="zzz") == []


def test_playlists(store: Store, tmp_path: Path):
    pid = store.upsert_playlist("https://youtube.com/playlist?list=1", "Goa Set")
    assert store.upsert_playlist("https://youtube.com/playlist?list=1", "Goa Set renamed") == pid
    tid = store.add_track(path=tmp_path / "a.mp3", fmt="mp3", bitrate_kbps=320, cutoff_hz=19800,
                          file_size=1, artist="a", title="t", mix_name="Original Mix", duration_s=1,
                          isrc=None, catalog_track_id=None, request_id=None)
    store.add_playlist_track(pid, tid, 1)
    store.add_playlist_track(pid, tid, 1)  # idempotent
    p = store.get_playlist(pid)
    assert p.name == "Goa Set renamed" and p.track_ids == [tid] and p.track_positions == [1]
    assert [x.id for x in store.list_playlists()] == [pid]


def test_rejections_and_stats(store: Store, tmp_path: Path):
    rid = store.add_request("q", RequestKind.TEXT)
    rj = store.add_rejection(rid, "cutoff 16000 Hz below 18000", 320, 16000, tmp_path / "s.png")
    assert store.get_rejection(rj).reason.startswith("cutoff")
    assert len(store.list_rejections()) == 1
    s = store.stats()
    assert s["rejections"] == 1 and s["tracks"] == 0
    assert s["requests_by_state"]["queued"] == 1


def test_delete_request_removes_candidates_and_rejections_but_not_the_id(store: Store, tmp_path: Path):
    rid = store.add_request("q", RequestKind.TEXT)
    store.add_candidates(rid, [Candidate(source="s", source_ref="r", artist="A", title="T", rank=1)])
    store.add_rejection(rid, "cutoff", 320, 16000, None)
    store.set_state(rid, RequestState.REJECTED)
    other = store.add_request("keep", RequestKind.TEXT)
    store.delete_request(rid)
    with pytest.raises(KeyError):
        store.get_request(rid)
    assert store.get_candidates(rid) == []
    assert store.get_rejection_for_request(rid) is None
    assert store.get_request(other).id == other  # untouched
    with pytest.raises(KeyError):
        store.delete_request(999)


def test_delete_requests_removes_only_the_given_states(store: Store):
    rejected = store.add_request("r", RequestKind.TEXT)
    store.set_state(rejected, RequestState.REJECTED)
    errored = store.add_request("e", RequestKind.TEXT)
    store.set_state(errored, RequestState.ERROR, error_message="boom")
    done = store.add_request("d", RequestKind.TEXT)
    store.set_state(done, RequestState.DONE)
    queued = store.add_request("q", RequestKind.TEXT)
    removed = store.delete_requests({RequestState.REJECTED, RequestState.ERROR})
    assert sorted(removed) == sorted([rejected, errored])
    assert store.get_request(done).id == done
    assert store.get_request(queued).id == queued
    with pytest.raises(KeyError):
        store.get_request(rejected)
    with pytest.raises(KeyError):
        store.get_request(errored)
    assert store.delete_requests({RequestState.REJECTED}) == []  # nothing left in that state


def test_settings(store: Store):
    assert store.get_setting("x") is None
    store.set_setting("x", "1")
    store.set_setting("x", "2")
    assert store.get_setting("x") == "2"


def test_listeners_fire_on_request_track_and_bulk_changes(tmp_path):
    from flackey.models import RequestKind, RequestState
    store = Store(tmp_path / "s.sqlite")
    seen = []
    store.listeners.append(lambda kind, oid: seen.append((kind, oid)))
    rid = store.add_request("q", RequestKind.TEXT)
    store.set_state(rid, RequestState.FETCHING)
    tid = store.add_track(path=tmp_path / "a.mp3", fmt="mp3", bitrate_kbps=320, cutoff_hz=20000, file_size=1,
                          artist="A", title="T", mix_name="Original Mix", duration_s=1, isrc=None,
                          catalog_track_id=None, request_id=rid)
    store.reset_inflight()
    assert seen == [("request", rid), ("request", rid), ("track", tid), ("queue", 0)]


def test_rejection_for_request_and_playlist_tracks(tmp_path):
    from flackey.models import RequestKind
    store = Store(tmp_path / "s.sqlite")
    rid = store.add_request("q", RequestKind.TEXT)
    assert store.get_rejection_for_request(rid) is None
    rj = store.add_rejection(rid, "cutoff", 320, 16000, None)
    assert store.get_rejection_for_request(rid).id == rj
    ids = [store.add_track(path=tmp_path / f"{i}.mp3", fmt="mp3", bitrate_kbps=320, cutoff_hz=20000, file_size=1,
                           artist="A", title=f"T{i}", mix_name="Original Mix", duration_s=1, isrc=None,
                           catalog_track_id=None, request_id=None) for i in range(3)]
    pid = store.upsert_playlist("u", "P")
    store.add_playlist_track(pid, ids[2], 1)
    store.add_playlist_track(pid, ids[0], 2)
    assert [t.id for t in store.list_tracks(playlist_id=pid)] == [ids[2], ids[0]]
    assert [t.id for t in store.list_tracks(search="t1", playlist_id=pid)] == []
    assert [r.id for r in store.list_recent_requests(limit=1)] == [rid]


def test_ensure_column_adds_once_and_survives_reopen(tmp_path: Path):
    db = tmp_path / "m.sqlite"
    s1 = Store(db)
    cols = {r[1] for r in s1.conn.execute("PRAGMA table_info(tracks)")}
    assert {"source", "source_fmt", "bit_depth", "sample_rate"} <= cols
    assert "fetch_source" in {r[1] for r in s1.conn.execute("PRAGMA table_info(requests)")}
    s1.conn.close()
    s2 = Store(db)  # second open must not fail on ALTER TABLE
    assert s2.conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0] == 0


def test_track_source_columns_round_trip(store: Store, tmp_path: Path):
    tid = store.add_track(path=tmp_path / "a.aiff", fmt="aiff", bitrate_kbps=1411, cutoff_hz=22050, file_size=1,
                          artist="A", title="T", mix_name="Original Mix", duration_s=1, isrc=None,
                          catalog_track_id=None, request_id=None, source="soulseek", source_fmt="flac",
                          bit_depth=16, sample_rate=44100)
    t = store.get_track(tid)
    assert (t.source, t.source_fmt, t.bit_depth, t.sample_rate) == ("soulseek", "flac", 16, 44100)
    old = store.add_track(path=tmp_path / "b.mp3", fmt="mp3", bitrate_kbps=320, cutoff_hz=20000, file_size=1,
                          artist="A", title="U", mix_name="Original Mix", duration_s=1, isrc=None,
                          catalog_track_id=None, request_id=None)
    assert store.get_track(old).source == "deezer_bot" and store.get_track(old).source_fmt is None
    assert store.stats()["by_source"] == {"deezer_bot": 1, "soulseek": 1}


def test_attempt_lifecycle(store: Store):
    rid = store.add_request("q", RequestKind.TEXT)
    aid = store.add_attempt(rid, "soulseek", "Hallucinogen Orphic Thrench")
    a = store.get_attempt(aid)
    assert a.request_id == rid and a.provider == "soulseek" and a.outcome is None and a.timeline == []
    store.update_attempt(aid, timeline=[{"t_ms": 1, "event": "search_started", "detail": {}}],
                         report={"seen": 3}, fingerprint={"score": 0.98}, first_byte_ms=1200, total_ms=20000,
                         outcome="filed", spectrogram_path="/x.png", raw_dir="/raw/1")
    a = store.get_attempt(aid)
    assert a.outcome == "filed" and a.report == {"seen": 3} and a.fingerprint == {"score": 0.98}
    assert a.timeline[0]["event"] == "search_started" and a.first_byte_ms == 1200 and a.raw_dir == "/raw/1"
    assert store.get_attempt_for_request(rid).id == aid
    assert store.get_attempt_for_request(rid + 1) is None
    assert [x.id for x in store.list_attempts()] == [aid]
    assert store.list_attempts(outcome="no_pick") == []
    assert store.attempt_counts() == {"filed": 1}


def test_open_attempts_become_interrupted_on_start(store: Store):
    rid = store.add_request("q", RequestKind.TEXT)
    a1 = store.add_attempt(rid, "soulseek", "q")
    a2 = store.add_attempt(rid, "soulseek", "q")
    store.update_attempt(a2, outcome="no_pick")
    assert store.mark_open_attempts_interrupted() == 1
    assert store.get_attempt(a1).outcome == "interrupted" and store.get_attempt(a2).outcome == "no_pick"


def test_evidence_rows(store: Store, tmp_path: Path):
    tid = store.add_track(path=tmp_path / "a.aiff", fmt="aiff", bitrate_kbps=1411, cutoff_hz=22050, file_size=1,
                          artist="A", title="T", mix_name="Original Mix", duration_s=1, isrc=None,
                          catalog_track_id=None, request_id=None)
    store.add_evidence(tid, "recording_match", {"score": 0.98, "offset_s": 48.0, "reference": "deezer:6025986"})
    store.add_evidence(tid, "source", {"provider": "soulseek", "username": "loginty"})
    rows = store.list_evidence(tid)
    assert [r.kind for r in rows] == ["recording_match", "source"] and rows[0].value["score"] == 0.98


def test_a_request_parked_before_the_column_existed_still_earns_its_choose_rung(tmp_path: Path):
    """The `reviewed` column arrived with a DEFAULT of 0, so every row already in the database reads as
    never-reviewed -- including a request sitting in awaiting_review at the moment of the upgrade. That row
    is the one the owner is most likely to be looking at when they next open the app, and the rung would
    vanish from under them the instant they picked. Being parked *is* the proof it was sent for review, so
    the flag is restored on open."""
    path = tmp_path / "t.sqlite"
    store = Store(path)
    rid = store.add_request("astral projection into the void", RequestKind.TEXT)
    store.set_state(rid, RequestState.AWAITING_REVIEW)
    store.conn.execute("UPDATE requests SET reviewed=0 WHERE id=?", (rid,))   # as an older build left it
    store.conn.commit()
    store.conn.close()

    assert Store(path).get_request(rid).reviewed


def test_request_reference_is_kept_beside_the_request(tmp_path):
    """Its own table, not a column on `requests`: a full fingerprint is ~50 KB and `list_requests` feeds
    the UI. Written once and reused by every retry, the lossy fallback and the library sweep."""
    store = Store(tmp_path / "t.sqlite")
    rid = store.add_request("x", RequestKind.YT_TRACK, source_url="https://www.youtube.com/watch?v=a")
    assert store.get_reference(rid) is None
    ref = {"kind": "youtube", "ref": "a", "needles": [[1]], "full": [1, 2], "excerpt_start_s": 0, "excerpt_s": 30}
    store.set_reference(rid, ref)
    assert store.get_reference(rid) == ref
    store.set_reference(rid, {**ref, "ref": "b"})
    assert store.get_reference(rid)["ref"] == "b"
    store.set_reference(rid, None)
    assert store.get_reference(rid) is None
