import sqlite3
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


def test_a_second_search_replaces_the_candidates_but_keeps_the_chosen_one(store: Store):
    """The list is the current search's answer, not a log of every pass -- since issue #69 a request with
    no chosen record goes to Soulseek and comes back round the retry ladder, so it is written more than
    once. The row the request points at is the exception: no foreign key protects it, and `upgrade()`,
    the sweep and the request page all dereference `chosen_candidate_id`."""
    def cand(ref: str, rank: int) -> Candidate:
        return Candidate(source="deezer_bot", source_ref=ref, artist="A", title="T", rank=rank)

    rid = store.add_request("q", RequestKind.TEXT)
    store.add_candidates(rid, [cand("dz:1", 1), cand("dz:2", 2)])
    [kept] = store.add_candidates(rid, [cand("dz:3", 1)])
    assert [c.source_ref for c in store.get_candidates(rid)] == ["dz:3"]

    store.update_request(rid, chosen_candidate_id=kept.id)
    store.add_candidates(rid, [cand("dz:4", 1), cand("dz:5", 2)])
    assert sorted(c.source_ref for c in store.get_candidates(rid)) == ["dz:3", "dz:4", "dz:5"]
    assert store.get_candidate(kept.id).source_ref == "dz:3"      # never stranded the pointer


def test_catalog_upsert(store: Store):
    ct = _catalog()
    store.upsert_catalog_track(ct)
    ct.bpm = 143
    store.upsert_catalog_track(ct)
    assert store.get_catalog_track(16552105).bpm == 143
    assert store.get_catalog_track(1) is None


def test_catalog_keeps_where_fallback_tags_came_from(store: Store):
    ct = _catalog()
    ct.id, ct.tag_source = -7, "discogs"
    store.upsert_catalog_track(ct)
    assert store.get_catalog_track(-7).tag_source == "discogs"
    assert store.get_catalog_track(-7).label == ct.label


def test_the_original_date_and_the_deezer_date_are_kept(store: Store):
    ct = _catalog()
    ct.original_date = "1996-11-01"
    store.upsert_catalog_track(ct)
    got = store.get_catalog_track(ct.id)
    assert (got.release_date, got.original_date, got.year) == (ct.release_date, "1996-11-01", "1996")
    rid = store.add_request("q", RequestKind.TEXT)
    [c] = store.add_candidates(rid, [Candidate(source="deezer_bot", source_ref="dz:1", artist="A", title="T",
                                               release_date="2001-03-07")])
    assert store.get_candidate(c.id).release_date == "2001-03-07"


def _no_pick_attempt(store: Store, rid: int, seen: int) -> None:
    aid = store.add_attempt(rid, "soulseek", "q")
    store.update_attempt(aid, outcome="no_pick", report={"seen": seen, "survivors": [], "summary": "s"})


def test_an_empty_search_is_worked_out_once_with_the_verdict(store: Store, monkeypatch):
    """The queue is drawn on every push; reading every attempt row's JSON for each `no_pick` request each
    time is what the stored flag saves."""
    empty, some = store.add_request("a", RequestKind.TEXT), store.add_request("b", RequestKind.TEXT)
    _no_pick_attempt(store, empty, 0)
    _no_pick_attempt(store, some, 12)
    store.set_lossless_verdict(empty, "no_pick")
    store.set_lossless_verdict(some, "no_pick")
    monkeypatch.setattr(store, "attempts_for_request", lambda rid: pytest.fail("read the attempt rows again"))
    assert store.lossless_no_results(empty, "no_pick") is True
    assert store.lossless_no_results(some, "no_pick") is False
    assert store.lossless_no_results(empty, "queued") is False


def test_an_empty_search_from_before_the_column_is_worked_out_on_first_read_and_kept(store: Store):
    rid = store.add_request("a", RequestKind.TEXT)
    _no_pick_attempt(store, rid, 0)
    store.conn.execute("UPDATE requests SET lossless_verdict='no_pick', lossless_no_results=NULL WHERE id=?", (rid,))
    assert store.lossless_no_results(rid, "no_pick") is True
    assert store.conn.execute("SELECT lossless_no_results FROM requests WHERE id=?", (rid,)).fetchone()[0] == 1
    # A pass still running has no verdict: worked out from its rows, and nothing stored for it.
    running = store.add_request("b", RequestKind.TEXT)
    _no_pick_attempt(store, running, 0)
    assert store.lossless_no_results(running, "no_pick") is True
    assert store.conn.execute("SELECT lossless_no_results FROM requests WHERE id=?", (running,)).fetchone()[0] is None
    store.set_lossless_verdict(rid, None)                      # a new pass starts: the old answer goes with it
    assert store.conn.execute("SELECT lossless_no_results FROM requests WHERE id=?", (rid,)).fetchone()[0] is None


def test_an_upsert_keeps_the_earliest_original_date_and_never_erases_one(store: Store):
    ct = _catalog()
    for given, stored in ((None, None), ("2001-03-12", "2001-03-12"), (None, "2001-03-12"),
                          ("2003-01-01", "2001-03-12"), ("1999-11-01", "1999-11-01")):
        ct.original_date = given
        store.upsert_catalog_track(ct)
        assert store.get_catalog_track(ct.id).original_date == stored, given


def test_tag_source_column_is_added_to_an_old_database(tmp_path: Path):
    """The column is additive: a database from before issue #146 gains it on open, its rows read as None."""
    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE catalog_tracks (id INTEGER PRIMARY KEY, isrc TEXT, artist TEXT NOT NULL, "
                 "title TEXT NOT NULL, mix_name TEXT NOT NULL, label TEXT NOT NULL, genre TEXT NOT NULL, "
                 "sub_genre TEXT, catalog_number TEXT, release_name TEXT, release_date TEXT, bpm INTEGER, "
                 "key TEXT, duration_ms INTEGER, artwork_url TEXT, fetched_at TEXT NOT NULL)")
    conn.execute("INSERT INTO catalog_tracks (id, artist, title, mix_name, label, genre, fetched_at) "
                 "VALUES (5, 'A', 'T', 'Original Mix', 'L', 'G', 'now')")
    conn.commit()
    conn.close()
    assert Store(db).get_catalog_track(5).tag_source is None


def test_tracks_and_duplicates(store: Store, tmp_path: Path):
    store.upsert_catalog_track(_catalog())
    tid = store.add_track(path=tmp_path / "a.mp3", fmt="mp3", bitrate_kbps=320, cutoff_hz=19800,
                          file_size=100, artist="Astral Projection", title="Into the Void",
                          mix_name="Original Mix", duration_s=443, isrc="UKU932231081",
                          catalog_track_id=16552105, request_id=None)
    assert [t.id for t in store.find_tracks_by_isrc("UKU932231081")] == [tid]
    assert store.find_tracks_by_isrc("nope") == []
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
    # The spectral check is what a rejection used to mean, and still what one means when nobody says
    # otherwise -- rows written before the column existed read the same way.
    assert store.get_rejection(rj).kind == "quality"
    other = store.add_rejection(rid, "a different recording: best score 0.61 below 0.79", 320, None, None,
                                kind="different_recording")
    assert store.get_rejection(other).kind == "different_recording"
    assert store.get_rejection(other).cutoff_hz is None
    assert len(store.list_rejections()) == 2
    s = store.stats()
    assert s["rejections"] == 2 and s["tracks"] == 0
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
    assert {"fetch_source", "failed_stage"} <= {r[1] for r in s1.conn.execute("PRAGMA table_info(requests)")}
    s1.conn.close()
    s2 = Store(db)  # second open must not fail on ALTER TABLE
    assert s2.conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0] == 0


# The candidates table as it shipped before `has_preview`, so the migration can be tested against a
# database that really predates the column rather than one Store just created.
CANDIDATES_BEFORE_HAS_PREVIEW = """
CREATE TABLE candidates (
  id INTEGER PRIMARY KEY AUTOINCREMENT, request_id INTEGER NOT NULL, rank INTEGER NOT NULL,
  source TEXT NOT NULL, source_ref TEXT NOT NULL, artist TEXT NOT NULL, title TEXT NOT NULL,
  mix_name TEXT, duration_s INTEGER, deezer_id INTEGER, isrc TEXT, score INTEGER, catalog_track_id INTEGER
)"""


def test_a_database_written_before_has_preview_gains_the_column_and_reads_back_unknown(tmp_path: Path):
    """Every candidate row in the owner's database predates this column. It has to open, and those rows
    have to read as None -- nobody ever asked Deezer about them, which is not the same as no sample."""
    db = tmp_path / "old.sqlite"
    conn = sqlite3.connect(db)
    conn.executescript(CANDIDATES_BEFORE_HAS_PREVIEW)
    conn.execute("INSERT INTO candidates (request_id, rank, source, source_ref, artist, title) "
                 "VALUES (1, 1, 'deezer_bot', 'dz_track:1:send', 'A', 'T')")
    conn.commit()
    conn.close()

    store = Store(db)  # CREATE TABLE IF NOT EXISTS leaves the old table alone; _ensure_column does the work
    assert "has_preview" in {r[1] for r in store.conn.execute("PRAGMA table_info(candidates)")}
    [old] = store.get_candidates(1)
    assert old.has_preview is None


def test_has_preview_round_trips_in_all_three_states(store: Store):
    """True, False and None are three different answers to the page: play it, do not offer it, and we
    never asked. SQLite stores 1/0/NULL, so the mapper has to hand back bools rather than ints."""
    rid = store.add_request("q", RequestKind.TEXT)
    store.add_candidates(rid, [
        Candidate(source="deezer_bot", source_ref="dz:1", artist="A", title="T", rank=1, has_preview=True),
        Candidate(source="deezer_bot", source_ref="dz:2", artist="A", title="T", rank=2, has_preview=False),
        Candidate(source="deezer_bot", source_ref="dz:3", artist="A", title="T", rank=3),
    ])
    got = store.get_candidates(rid)
    assert [c.has_preview for c in got] == [True, False, None]
    assert got[0].has_preview is True and got[1].has_preview is False
    assert store.get_candidate(got[1].id).has_preview is False


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
    store.add_evidence(tid, "source", {"provider": "soulseek", "username": "peer-alpha"})
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


def test_deleting_a_request_takes_its_acoustic_reference_with_it(store: Store):
    """A reference is ~50 KB of fingerprint. `request_references` declares ON DELETE CASCADE, but SQLite
    enforces foreign keys only with `PRAGMA foreign_keys = ON`, which this connection deliberately does not
    set -- so the delete methods enumerate the table by hand, the same way they do candidates, rejections
    and attempts. Without that, every request the owner deleted left its reference behind for ever."""
    rid = store.add_request("q", RequestKind.TEXT)
    store.set_reference(rid, {"duration_s": 300.0, "fp": [1, 2, 3]})
    store.set_state(rid, RequestState.REJECTED)
    kept = store.add_request("keep", RequestKind.TEXT)
    store.set_reference(kept, {"duration_s": 200.0, "fp": [4]})
    store.delete_request(rid)
    assert store.get_reference(rid) is None
    assert store.get_reference(kept) is not None            # only the deleted request's row goes

    bulk = store.add_request("b", RequestKind.TEXT)
    store.set_reference(bulk, {"duration_s": 100.0, "fp": [5]})
    store.set_state(bulk, RequestState.ERROR)
    assert store.delete_requests({RequestState.ERROR}) == [bulk]
    assert store.get_reference(bulk) is None
    assert store.get_reference(kept) is not None


# The requests table as it shipped before the readings of a noisy title were kept.
REQUESTS_BEFORE_INTERPRETATIONS = """
CREATE TABLE requests (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  raw_text TEXT NOT NULL, kind TEXT NOT NULL, state TEXT NOT NULL,
  playlist_id INTEGER, playlist_position INTEGER, source_url TEXT,
  query_artist TEXT, query_title TEXT, query_version TEXT, query_duration_s INTEGER,
  chosen_candidate_id INTEGER, catalog_track_id INTEGER, confidence INTEGER,
  flag_reason TEXT, error_message TEXT, attempts INTEGER NOT NULL DEFAULT 0, retry_after TEXT, track_id INTEGER,
  lossless_retry INTEGER NOT NULL DEFAULT 0
)"""


def test_a_database_written_before_interpretations_gains_the_columns(tmp_path: Path):
    db = tmp_path / "old.sqlite"
    conn = sqlite3.connect(db)
    conn.executescript(REQUESTS_BEFORE_INTERPRETATIONS)
    conn.execute("INSERT INTO requests (created_at, updated_at, raw_text, kind, state) "
                 "VALUES ('t', 't', 'A - B', 'yt_track', 'done')")
    conn.commit()
    conn.close()

    store = Store(db)
    cols = {r[1] for r in store.conn.execute("PRAGMA table_info(requests)")}
    assert {"interpretations", "interpret_class", "interpret_reading"} <= cols
    assert store.get_request(1).raw_text == "A - B"
    assert store.get_interpretations(1) == [] and store.get_interpretation_outcome(1) == (None, None)
    assert store.stats()["interpretations"] == {"confirmed_by_other": 0}


def test_interpretations_round_trip_and_stay_off_the_request(store: Store):
    readings = [Query(raw="L - A - T", artist="L", title="A - T", duration_s=300),
                Query(raw="L - A - T", artist="A", title="T", version="X Remix", duration_s=300)]
    rid = store.add_request("L - A - T", RequestKind.YT_TRACK, query=readings[0], interpretations=readings)
    assert store.get_interpretations(rid) == readings
    before = store.get_request(rid).updated_at
    store.set_interpretation_outcome(rid, "confirmed", 1)
    assert store.get_interpretation_outcome(rid) == ("confirmed", 1)
    # The page is sent every Request field on each refresh; none of this is for it
    r = store.get_request(rid)
    assert not {"interpretations", "interpret_class", "interpret_reading"} & set(vars(r))
    assert r.updated_at == before


def test_stats_count_the_labels_of_noisy_titles(store: Store):
    two = [Query(raw="x", artist="L", title="A - T"), Query(raw="x", artist="A", title="T")]
    for klass, reading in [("confirmed", 0), ("confirmed", 1), ("agreed", None), ("ambiguous", None),
                           ("skipped", None), (None, None)]:
        rid = store.add_request("x", RequestKind.YT_TRACK, interpretations=two)
        if klass:
            store.set_interpretation_outcome(rid, klass, reading)
    store.add_request("plain", RequestKind.YT_TRACK)
    assert store.stats()["interpretations"] == {"confirmed": 2, "agreed": 1, "ambiguous": 1, "skipped": 1,
                                                 "pending": 1, "confirmed_by_other": 1}
