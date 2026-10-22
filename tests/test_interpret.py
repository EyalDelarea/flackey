"""The readings of a noisy YouTube title (`identify.interpretations`) and the shadow comparison that labels
them (`worker.interpret`). Nothing here may change what a request searches for first or which record it
files, except that the other readings are tried on Soulseek after every spelling it already had."""
import asyncio
import logging
import sqlite3
from datetime import UTC, datetime, timedelta
from itertools import pairwise

import pytest

from flackey import reference as ref_mod
from flackey.config import Settings
from flackey.fingerprint import AcousticReference, FingerprintResult
from flackey.identify import interpretations, parse_youtube_title
from flackey.lossless import search_text
from flackey.models import Candidate, Query, RequestKind, RequestState
from flackey.notify import MemoryNotifier
from flackey.reference import DeezerSearchError, Identification
from flackey.source import SourceNotFound
from flackey.store import Store
from flackey.worker import Worker
from flackey.worker import acoustic as acoustic_mod
from flackey.worker import interpret as interpret_mod
from flackey.worker import pipeline as pipeline_mod
from flackey.worker.interpret import (
    AGREED,
    AMBIGUOUS,
    CONFIRMED,
    SKIPPED,
    ReadingResult,
    accepted,
    classify,
    preview_order,
    same_record,
)
from flackey.worker.records import query_candidate
from flackey.youtube import YouTubeError
from tests.conftest import requires_ffmpeg
from tests.test_worker import FakeCatalog, FakeSource, good_cand
from tests.test_worker_lossless import (
    CT3,
    FakeProvider,
    lenv,  # noqa: F401  (the fixture)
    lf,
    make,
)


def _rows(readings: list[Query]) -> list[tuple]:
    return [(q.artist, q.title, q.version) for q in readings]


# ---- the readings -----------------------------------------------------------------------------------------


def test_a_label_before_the_artist_gives_the_last_dash_reading():
    got = interpretations("Label - Artist - Title (Extended Mix)", "Label", 300)
    assert _rows(got) == [("Label", "Artist - Title", "Extended Mix"), ("Artist", "Title", "Extended Mix")]
    assert all(q.duration_s == 300 and q.raw == "Label - Artist - Title (Extended Mix)" for q in got)


def test_the_readings_come_in_order_and_are_capped_at_four():
    # Six distinct readings exist here; the first four, in the documented order, are kept.
    got = interpretations("C & D - A x B - Title (Ibiza Anthem)", "Mixes", 300)
    assert _rows(got) == [
        ("C & D", "A x B - Title (Ibiza Anthem)", None),    # 1: today's parse
        ("C & D", "A x B - Title", None),                   # 2: the leftover bracket dropped
        ("A x B", "Title (Ibiza Anthem)", None),            # 3: split at the last dash
        ("C", "A x B - Title (Ibiza Anthem)", None),        # 4: the first credited artist, of reading 1
    ]


def test_a_leftover_bracket_is_dropped_but_a_version_or_a_feature_never_is():
    assert _rows(interpretations("Artist (UK) - Title (Ibiza Anthem) (Someone Remix)", "x")) == [
        ("Artist (UK)", "Title (Ibiza Anthem)", "Someone Remix"), ("Artist", "Title", "Someone Remix")]
    assert _rows(interpretations("Artist - Title (feat. Someone)", "x")) == [
        ("Artist", "Title (feat. Someone)", None)]


def test_no_dash_reads_the_title_alone_unless_the_channel_is_youtubes_own():
    assert _rows(interpretations("Bicep Glue", "Ninja Tune")) == [("Ninja Tune", "Bicep Glue", None),
                                                                   (None, "Bicep Glue", None)]
    assert _rows(interpretations("Glue", "Bicep - Topic")) == [("Bicep", "Glue", None)]
    assert _rows(interpretations("Glue", None)) == [(None, "Glue", None)]


def test_a_collaboration_is_also_read_under_its_first_name():
    for sep in ("x", "&", "vs", "vs.", "X"):
        got = interpretations(f"Alpha {sep} Beta - Title", "x")
        assert _rows(got) == [(f"Alpha {sep} Beta", "Title", None), ("Alpha", "Title", None)]
    assert len(interpretations("Malcolm X - Title", "x")) == 1


@pytest.mark.parametrize("title", [
    "Artist - Title",
    "Artist - Title - Extended Mix",            # the third segment is a version, not a title
    "UB40 - Red Red Wine - Live",
    "SGR053 - Artist - Track",                  # the last-dash reading is the baseline already
    "Artist - Track (FREE DL) [Label Records] [NM022]",
    "Artist - Title (Original Mix)",
])
def test_a_title_with_one_sensible_reading_answers_with_just_that_one(title):
    got = interpretations(title, "x", 200)
    assert len(got) == 1 and got[0] == parse_youtube_title(title, "x", 200)


@pytest.mark.parametrize("title,uploader", [
    ("Label - Artist - Title (Extended Mix)", "Label"), ("Bicep Glue", "Ninja Tune"),
    ("TB Premiere: Artist - Track [FREE DL]", "TB"), ("C & D - A x B - Title (Ibiza Anthem)", "Mixes"),
    ("Label   -  Artist \t-   Title  (Ibiza Anthem)", "Label"),     # the same whitespace collapse
    ("Artist - Free Dl", "Label"), ("Label - Artist - .mp3", "Label"),  # the fallback that keeps the artist
    ("Label - Artist - " + "Title " * 80, "Label"),                   # the same 300-character cap
])
def test_the_first_reading_is_always_the_query_the_pipeline_searches_with(title, uploader):
    assert interpretations(title, uploader, 99)[0] == parse_youtube_title(title, uploader, 99)


# ---- the classification, as pure functions ------------------------------------------------------------------


def _dz(i: int, artist="Astral Projection", title="Into the Void", duration_s=3, **kw) -> Candidate:
    return Candidate(source="deezer_api", source_ref=f"deezer:{i}", artist=artist, title=title,
                     duration_s=duration_s, deezer_id=i, rank=kw.pop("rank", 0), **kw)


AP = Query(raw="x", artist="Astral Projection", title="Into the Void", duration_s=3)
LABEL = Query(raw="x", artist="Label", title="Astral Projection - Into the Void", duration_s=3)


def test_accepted_keeps_what_decide_would_take_best_first():
    got = accepted(AP, [_dz(1, title="Something Else", rank=0), _dz(2, rank=1),
                        _dz(3, mix_name="Someone Remix", rank=2), _dz(4, duration_s=60, rank=3)])
    assert [c.deezer_id for c in got] == [2]
    remix = Query(raw="x", artist="Astral Projection", title="Into the Void", version="Someone Remix", duration_s=3)
    assert [c.deezer_id for c in accepted(remix, [_dz(2), _dz(3, mix_name="Someone Remix")])] == [3]
    # With no original of the video's length, the version that is that length is accepted too (`decide`'s
    # pin); the original still scores higher, as it does in `decide`
    assert [c.deezer_id for c in accepted(AP, [_dz(2, duration_s=9), _dz(3, mix_name="Someone Edit")])] == [2, 3]


def test_a_reading_with_no_artist_must_match_the_length_instead():
    alone = Query(raw="x", artist=None, title="Into the Void", duration_s=3)
    assert [c.deezer_id for c in accepted(alone, [_dz(1), _dz(2, duration_s=9)])] == [1]
    assert accepted(Query(raw="x", title="Into the Void"), [_dz(1)]) == []    # no length known: nothing


def test_same_record_by_id_isrc_or_words():
    assert same_record(_dz(1), _dz(1, artist="Other"))
    assert same_record(_dz(1, isrc="X1"), _dz(2, artist="Other", isrc="X1"))
    assert same_record(_dz(1), _dz(2))                                  # one recording on two releases
    assert not same_record(_dz(1), _dz(2, mix_name="Someone Remix"))


def test_previews_are_shared_across_readings_deduped_and_capped():
    a = ReadingResult(AP, [_dz(1), _dz(2), _dz(3)])
    b = ReadingResult(LABEL, [_dz(2), _dz(4), _dz(5)])
    assert [c.deezer_id for c in preview_order([a, b], skip=set())] == [1, 2, 4, 3, 5]
    assert [c.deezer_id for c in preview_order([a, b], skip={1, 4})] == [2, 3, 5]
    assert len(preview_order([ReadingResult(AP, [_dz(i) for i in range(20)])], set())) == 8


def test_classify_confirmed_names_the_first_reading_that_accepted_the_match():
    out = classify([ReadingResult(LABEL, []), ReadingResult(AP, [_dz(7)])], matched=7)
    assert (out.klass, out.reading) == (CONFIRMED, 1)
    # A match no reading accepted is not a confirmation of any of them
    assert classify([ReadingResult(LABEL, []), ReadingResult(AP, [_dz(7)])], matched=99).klass == AGREED


def test_classify_agreed_ambiguous_and_skipped():
    assert classify([ReadingResult(LABEL, [_dz(1)]), ReadingResult(AP, [_dz(1)])], None).klass == AGREED
    assert classify([ReadingResult(LABEL, []), ReadingResult(AP, [_dz(1)])], None).klass == AGREED
    differ = classify([ReadingResult(LABEL, [_dz(1, artist="Label")]), ReadingResult(AP, [_dz(2, title="X")])], None)
    assert (differ.klass, differ.reading) == (AMBIGUOUS, None)
    assert classify([ReadingResult(LABEL, []), ReadingResult(AP, [])], None).klass == AMBIGUOUS
    failed = classify([ReadingResult(LABEL, [_dz(1)]), ReadingResult(AP, error="http 500")], None)
    assert failed.klass == SKIPPED
    # The same words split in two places search for the same thing: agreed whatever they found
    split = Query(raw="x", artist="Label Astral", title="Projection - Into the Void", duration_s=3)
    assert classify([ReadingResult(LABEL, []), ReadingResult(split, [_dz(1)])], None).klass == AGREED


# ---- the worker, in shadow ----------------------------------------------------------------------------------

NOISY = "Label - Astral Projection - Into the Void"
URL = "https://www.youtube.com/watch?v=abc"


class CountingCatalog(FakeCatalog):
    def __init__(self, tracks=None):
        super().__init__(tracks)
        self.searches = 0

    async def search(self, query):
        self.searches += 1
        return await super().search(query)


class RecordingSource(FakeSource):
    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.texts: list[str] = []

    async def search(self, query):
        self.texts.append(query.search_text())
        return await super().search(query)


def shadow_video(videos: list[str]):
    async def fake_youtube(url, tmp_dir, *, duration_s=None):
        videos.append(url)
        return AcousticReference("youtube", "abc", [[1, 2, 3]], [1, 2, 3, 4], 0.0, 30.0)
    return fake_youtube


class OrderedProvider(FakeProvider):
    """A Soulseek that writes into the same log as the fake Deezer, so the order of the two can be read."""

    def __init__(self, *a, log: list, **kw):
        super().__init__(*a, **kw)
        self.log = log

    async def search(self, text, *, wait_s, on_raw=None):
        self.log.append(f"slskd:{text}")
        return await super().search(text, wait_s=wait_s, on_raw=on_raw)


@pytest.fixture
def shadow(lenv, monkeypatch):  # noqa: F811
    """A noisy YouTube request with two readings, a video, and a fake Deezer whose answers a test sets."""
    settings, store, _, _, _, _ = lenv
    events: list[str] = []
    answers: dict[str, list[Candidate] | Exception] = {}
    videos: list[str] = []

    async def fake_search(text, http, *, limit=10):
        events.append(f"deezer:{text}")
        got = answers.get(text, [])
        if isinstance(got, Exception):
            raise got
        return [Candidate(**c.__dict__) for c in got]

    monkeypatch.setattr(acoustic_mod, "youtube_reference", shadow_video(videos))
    monkeypatch.setattr(interpret_mod, "search_deezer", fake_search)
    monkeypatch.setattr(interpret_mod, "DEEZER_INTERVAL_S", 0)
    provider = OrderedProvider(settings.slskd_downloads, [lf("a")], audio=lenv[3].audio, log=events)

    def add() -> int:
        readings = interpretations(NOISY, "Label", 3)
        assert len(readings) == 2
        return store.add_request(NOISY, RequestKind.YT_TRACK, source_url=URL, query=readings[0],
                                 interpretations=readings)

    return store, provider, events, answers, videos, add


async def run(w, rid: int):
    """One pass, then the comparison it started in the background."""
    r = await w.process(rid)
    await w._shadow_settled()
    return r


BASE_TEXT = "Label Astral Projection - Into the Void"
ALT_TEXT = "Astral Projection Into the Void"


@requires_ffmpeg
async def test_the_pipelines_own_match_confirms_the_reading_that_accepts_it(lenv, shadow, monkeypatch, caplog):  # noqa: F811
    """lenv's identification matches the bot's record; the second reading's Deezer search accepts that same
    record, so the label is `confirmed` by reading 1 and no preview is downloaded twice."""
    store, provider, events, answers, videos, add = shadow
    caplog.set_level(logging.INFO, logger="flackey.worker.interpret")
    answers[ALT_TEXT] = [_dz(good_cand().deezer_id)]

    async def no_previews(*a, **kw):
        raise AssertionError("a preview the pipeline already compared was fetched again")

    monkeypatch.setattr(interpret_mod, "identify_record", no_previews)
    source, catalog = RecordingSource([good_cand()]), CountingCatalog([CT3])
    w = make(lenv, provider=provider, source=source, catalog=catalog)
    rid = add()
    r = await run(w, rid)
    assert r.state == RequestState.DONE
    assert store.get_interpretation_outcome(rid) == (CONFIRMED, 1)
    assert source.searches == 1 and catalog.searches == 1 and videos == [URL]
    # The bot was asked exactly what it was asked before: today's parse, label and all
    assert source.texts == [BASE_TEXT]
    assert sorted(e for e in events if e.startswith("deezer:")) == [f"deezer:{ALT_TEXT}", f"deezer:{BASE_TEXT}"]
    assert [e for e in events if e.startswith("slskd:")] == [f"slskd:{ALT_TEXT}"]   # what it searched before
    line = next(m for m in caplog.messages if m.startswith(f"req#{rid} interpretations:"))
    assert "confirmed by r1" in line and "previews 0" in line and f'r0 "Label - {NOISY[8:]}": none' in line


@requires_ffmpeg
async def test_a_preview_that_is_the_video_confirms_the_reading(lenv, shadow, monkeypatch, caplog):  # noqa: F811
    store, provider, _, answers, _, add = shadow
    caplog.set_level(logging.INFO, logger="flackey.worker.interpret")
    bot = good_cand()
    answers[ALT_TEXT] = [_dz(bot.deezer_id), _dz(55)]

    async def pipeline_no_match(reference, cands, http, tmp_dir, *, minimum, limit=5, cpu=None):
        return Identification(None, None, [(c.deezer_id, 0.3) for c in cands], "none is the video")

    previews: list[list[int]] = []

    async def shadow_match(reference, cands, http, tmp_dir, *, minimum, limit=5, cpu=None):
        previews.append([c.deezer_id for c in cands])
        return Identification(cands[0], 0.95, [(cands[0].deezer_id, 0.95)], "match")

    monkeypatch.setattr(pipeline_mod, "identify_record", pipeline_no_match)
    monkeypatch.setattr(interpret_mod, "identify_record", shadow_match)
    w = make(lenv, provider=provider, source=FakeSource([bot]), catalog=FakeCatalog([]))
    rid = add()
    r = await run(w, rid)
    assert previews == [[55]]                      # the bot's record was compared already; not again
    assert store.get_interpretation_outcome(rid) == (CONFIRMED, 1)
    # Shadow only: the pipeline still found no record and went to Soulseek on the request's own words
    assert store.get_request(rid).query_artist == "Label" and r.chosen_candidate_id is None
    assert "previews 1" in next(m for m in caplog.messages if f"req#{rid} interpretations" in m)


@requires_ffmpeg
async def test_readings_that_lead_to_one_record_are_agreed(lenv, shadow, monkeypatch):  # noqa: F811
    store, provider, _, answers, _, add = shadow
    answers[BASE_TEXT] = [_dz(55)]       # the label's words do not accept it ...
    answers[ALT_TEXT] = [_dz(55)]        # ... the artist's do

    async def no_match(reference, cands, http, tmp_dir, *, minimum, limit=5, cpu=None):
        return Identification(None, None, [(c.deezer_id, 0.2) for c in cands], "none")

    monkeypatch.setattr(pipeline_mod, "identify_record", no_match)
    monkeypatch.setattr(interpret_mod, "identify_record", no_match)
    w = make(lenv, provider=provider)
    rid = add()
    await run(w, rid)
    assert store.get_interpretation_outcome(rid) == (AGREED, None)


@requires_ffmpeg
async def test_readings_that_lead_to_different_records_are_ambiguous(lenv, shadow, monkeypatch, caplog):  # noqa: F811
    store, provider, _, answers, _, add = shadow
    caplog.set_level(logging.INFO, logger="flackey.worker.interpret")
    answers[BASE_TEXT] = [_dz(66, artist="Label", title="Astral Projection - Into the Void")]
    answers[ALT_TEXT] = [_dz(55)]

    async def no_match(reference, cands, http, tmp_dir, *, minimum, limit=5, cpu=None):
        return Identification(None, None, [(c.deezer_id, 0.2) for c in cands], "none")

    monkeypatch.setattr(pipeline_mod, "identify_record", no_match)
    monkeypatch.setattr(interpret_mod, "identify_record", no_match)
    w = make(lenv, provider=provider)
    rid = add()
    r = await run(w, rid)
    assert store.get_interpretation_outcome(rid) == (AMBIGUOUS, None)
    assert r.state not in (RequestState.AWAITING_REVIEW,)          # never parked for the owner in this phase
    line = next(m for m in caplog.messages if f"req#{rid} interpretations" in m)
    assert "ambiguous" in line and "deezer:66" in line and "deezer:55" in line and "previews 2" in line


@pytest.mark.parametrize("failure", [DeezerSearchError("http 503"), "slow"])
@requires_ffmpeg
async def test_a_deezer_failure_or_a_slow_deezer_fails_open(lenv, shadow, monkeypatch, failure):  # noqa: F811
    store, provider, _, answers, _, add = shadow
    if failure == "slow":
        monkeypatch.setattr(interpret_mod, "BUDGET_S", 0.05)

        async def slow(text, http, *, limit=10):
            await asyncio.sleep(5)

        monkeypatch.setattr(interpret_mod, "search_deezer", slow)
    else:
        answers[BASE_TEXT] = answers[ALT_TEXT] = failure
    w = make(lenv, provider=provider)
    rid = add()
    r = await run(w, rid)
    assert r.state == RequestState.DONE
    assert store.get_interpretation_outcome(rid) == (SKIPPED, None)


@requires_ffmpeg
async def test_a_bug_in_the_comparison_costs_only_the_label(lenv, shadow, monkeypatch, caplog):  # noqa: F811
    store, provider, _, answers, _, add = shadow
    answers[BASE_TEXT] = RuntimeError("unforeseen")
    w = make(lenv, provider=provider)
    rid = add()
    r = await run(w, rid)
    assert r.state == RequestState.DONE and store.get_interpretation_outcome(rid) == (None, None)
    assert "comparing the title's readings failed" in caplog.text


@requires_ffmpeg
async def test_no_video_leaves_the_request_unlabelled_without_asking_deezer(lenv, shadow, monkeypatch):  # noqa: F811
    """A video that will not come says nothing about the title: no label, so a later pass that has the video
    can still compare."""
    store, provider, events, _, _, add = shadow
    from flackey.fingerprint import FingerprintError

    async def unreadable(url, tmp_dir, *, duration_s=None):
        raise FingerprintError("unreadable")

    monkeypatch.setattr(acoustic_mod, "youtube_reference", unreadable)
    w = make(lenv, provider=provider)
    rid = add()
    await run(w, rid)
    assert store.get_interpretation_outcome(rid) == (None, None)
    assert not [e for e in events if e.startswith("deezer:")]


@requires_ffmpeg
async def test_a_failing_video_is_fetched_once_a_pass_and_compared_on_the_pass_that_has_it(lenv, shadow,  # noqa: F811
                                                                                            monkeypatch):
    """No bot record: the comparison waits for the fingerprint step's own fetch instead of fetching first,
    so a yt-dlp failure is one try per pass, not two, and costs no label."""
    store, provider, _, _, videos, add = shadow
    calls: list[str] = []

    async def failing(url, tmp_dir, *, duration_s=None):
        calls.append(url)
        raise YouTubeError("HTTP Error 429")

    monkeypatch.setattr(acoustic_mod, "youtube_reference", failing)
    w = make(lenv, provider=provider, source=FakeSource(error=SourceNotFound("no")), catalog=FakeCatalog([]))
    rid = add()
    r = await run(w, rid)
    assert calls == [URL] and r.state == RequestState.QUEUED
    assert store.get_interpretation_outcome(rid) == (None, None)
    monkeypatch.setattr(acoustic_mod, "youtube_reference", shadow_video(videos))
    store.update_request(rid, retry_after=None)
    r = await run(w, rid)
    assert videos == [URL] and r.state == RequestState.DONE
    assert store.get_interpretation_outcome(rid)[0] == AMBIGUOUS     # this Deezer knows neither reading


@requires_ffmpeg
async def test_a_duplicate_costs_no_video_download(lenv, shadow, monkeypatch):  # noqa: F811
    """No bot record and the file is already in the library: the request ends before any video is fetched,
    and so does the comparison."""
    settings = lenv[0]
    store, provider, events, _, videos, add = shadow
    filed = settings.library_root / "x.aiff"
    filed.parent.mkdir(parents=True, exist_ok=True)
    filed.write_bytes(b"x")
    store.add_track(path=filed, fmt="aiff", bitrate_kbps=1411, cutoff_hz=22050,
                    file_size=1, artist="Label", title="Astral Projection - Into the Void", mix_name="Original Mix",
                    duration_s=3, isrc=None, catalog_track_id=None, request_id=None)
    w = make(lenv, provider=provider, source=FakeSource(error=SourceNotFound("no")), catalog=FakeCatalog([]))
    rid = add()
    r = await run(w, rid)
    assert r.state == RequestState.DUPLICATE and videos == [] and not events


@requires_ffmpeg
async def test_one_reading_or_one_label_already_means_no_comparison(lenv, shadow):  # noqa: F811
    store, provider, events, _, _, add = shadow
    w = make(lenv, provider=provider)
    plain = store.add_request("Astral Projection - Into the Void", RequestKind.YT_TRACK, source_url=URL,
                              query=Query(raw="", artist="Astral Projection", title="Into the Void", duration_s=3))
    await run(w, plain)
    labelled = add()
    store.set_interpretation_outcome(labelled, AGREED)            # an earlier pass already compared them
    await run(w, labelled)
    assert not [e for e in events if e.startswith("deezer:")]
    assert store.get_interpretation_outcome(labelled) == (AGREED, None)


@requires_ffmpeg
async def test_the_other_readings_are_the_last_spellings_and_name_the_file_they_find(lenv, shadow, monkeypatch):  # noqa: F811
    """No record anywhere: Soulseek searches the request's own words first, then the other reading, and a
    copy found by the other reading is filed under that reading's words. The video is fetched once for
    both the comparison and the fingerprint."""
    settings, _, _, _, _, _ = lenv
    store, _, events, _, videos, add = shadow
    provider = OrderedProvider(settings.slskd_downloads, audio=lenv[3].audio, log=events,
                               files_for=lambda text: [lf("a", path="x\\Astral Projection - Into the Void.flac")]
                               if text == ALT_TEXT else [])
    w = make(lenv, provider=provider, source=FakeSource(error=SourceNotFound("no")), catalog=FakeCatalog([]))
    rid = add()
    r = await run(w, rid)
    assert provider.searches == [BASE_TEXT, ALT_TEXT]
    assert r.state == RequestState.DONE and videos == [URL]
    t = store.get_track(r.track_id)
    assert (t.artist, t.title) == ("Astral Projection", "Into the Void")
    assert store.get_request(rid).query_artist == "Label"             # the request's query is unchanged
    assert store.get_interpretation_outcome(rid)[0] == AMBIGUOUS     # this Deezer knows neither reading


@requires_ffmpeg
async def test_the_other_readings_come_after_every_spelling_the_ladder_already_had(lenv, shadow):  # noqa: F811
    store, provider, _, _, _, add = shadow
    w = make(lenv, provider=provider)
    req = store.get_request(add())
    cand = good_cand()
    cand.artist = "A.P."
    assert [search_text(r) for r in w._search_references(req, cand, None)] == ["A.P. Into the Void", BASE_TEXT, ALT_TEXT]
    assert w._search_references(req, cand, None)[-1].from_query
    # A reading that spells what an earlier rung already searched is not searched twice
    assert [search_text(r) for r in w._search_references(req, cand, CT3)] == [
        "Astral Projection Into the Void", BASE_TEXT]


@requires_ffmpeg
async def test_a_reading_with_no_artist_is_never_a_soulseek_spelling(lenv, shadow):  # noqa: F811
    store, provider, _, _, _, _ = shadow
    w = make(lenv, provider=provider)
    readings = interpretations("Bicep Glue", "Ninja Tune", 3)
    assert [q.artist for q in readings] == ["Ninja Tune", None]
    rid = store.add_request("Bicep Glue", RequestKind.YT_TRACK, source_url=URL, query=readings[0],
                            interpretations=readings)
    req = store.get_request(rid)
    assert [search_text(r) for r in w._search_references(req, query_candidate(req.query(), rid), None)] == [
        "Ninja Tune Bicep Glue"]


@pytest.mark.parametrize("damaged", ["not json", '{"artist": "A"}', "[1, 2]", '[{"artist": 5, "title": "T"}]',
                                     '[{"title": "T"}]', "null"])
@requires_ffmpeg
async def test_damaged_readings_cost_the_readings_never_the_request(lenv, shadow, damaged):  # noqa: F811
    settings = lenv[0]
    store, _, events, _, _, add = shadow
    provider = OrderedProvider(settings.slskd_downloads, audio=lenv[3].audio, log=events,
                               files_for=lambda text: [lf("a")] if text == BASE_TEXT else [])
    w = make(lenv, provider=provider, source=FakeSource(error=SourceNotFound("no")), catalog=FakeCatalog([]))
    rid = add()
    store.conn.execute("UPDATE requests SET interpretations=? WHERE id=?", (damaged, rid))
    assert store.get_interpretations(rid) == []
    r = await run(w, rid)
    assert r.state == RequestState.DONE and provider.searches == [BASE_TEXT]   # Soulseek still searched
    assert not [e for e in events if e.startswith("deezer:")]


@requires_ffmpeg
async def test_another_spelling_finding_nothing_never_hides_a_wrong_recording(lenv, shadow, monkeypatch):  # noqa: F811
    """The request's own words find copies the fingerprint refuses, the other reading finds nothing. The
    pass found a different recording, and waits for the people online to change; read as "nothing matched"
    it came back in 15 minutes to download the same wrong copies and told the owner nothing had matched."""
    settings = lenv[0]
    store, _, events, _, _, add = shadow
    fake_check = lenv[4]
    fake_check.result = FingerprintResult("failed", 0.1, None, "best score 0.10 below 0.79")
    provider = OrderedProvider(settings.slskd_downloads, audio=lenv[3].audio, log=events,
                               files_for=lambda text: [lf("a")] if text == BASE_TEXT else [])
    w = make(lenv, provider=provider, source=FakeSource(error=SourceNotFound("no")), catalog=FakeCatalog([]))
    rid = add()
    r = await run(w, rid)
    assert provider.searches == [BASE_TEXT, ALT_TEXT]
    assert store.get_attempt_for_request(rid).outcome == "no_pick"               # the rows stay true ...
    assert store.get_lossless_verdict(rid) == "fingerprint_failed"                # ... the pass says more
    assert r.state == RequestState.QUEUED and r.lossless_retry == 1
    assert r.flag_reason.startswith("waiting for Soulseek: the copies offered were a different recording")
    wait = datetime.fromisoformat(r.retry_after) - datetime.now(UTC)
    assert timedelta(hours=5, minutes=59) < wait <= timedelta(hours=6)


@requires_ffmpeg
async def test_comparisons_run_one_at_a_time_and_space_out_their_deezer_calls(lenv, shadow, monkeypatch):  # noqa: F811
    """A playlist of noisy titles is a queue, not a burst: one comparison at a time, and never two calls to
    Deezer's API closer than DEEZER_INTERVAL_S. And none of it touches Soulseek."""
    store, _, _, _, _, add = shadow
    monkeypatch.setattr(interpret_mod, "DEEZER_INTERVAL_S", 0.03)
    running, most, calls = 0, 0, []

    async def search(text, http, *, limit=10):
        nonlocal running, most
        calls.append(asyncio.get_running_loop().time())
        running += 1
        most = max(most, running)
        await asyncio.sleep(0.01)
        running -= 1
        return [_dz(55)]

    async def no_match(reference, cands, http, tmp_dir, *, minimum, limit=5, cpu=None):
        calls.append(asyncio.get_running_loop().time())
        return Identification(None, None, [(c.deezer_id, 0.2) for c in cands], "none")

    class NoSoulseek(OrderedProvider):
        async def search(self, text, *, wait_s, on_raw=None):
            raise AssertionError("the comparison searched Soulseek")

    monkeypatch.setattr(interpret_mod, "search_deezer", search)
    monkeypatch.setattr(interpret_mod, "identify_record", no_match)
    w = make(lenv, provider=NoSoulseek(lenv[0].slskd_downloads, log=[]))
    video = AcousticReference("youtube", "abc", [[1]], [1], 0.0, 30.0)
    rids = [add() for _ in range(3)]
    for rid in rids:
        w._interpret(store.get_request(rid), video)
    await w._shadow_settled()
    assert [store.get_interpretation_outcome(rid)[0] for rid in rids] == [AGREED] * 3
    assert most == 1 and len(calls) == 3 * 3                     # two searches and one preview each
    assert min(b - a for a, b in pairwise(calls)) >= 0.03 - 0.005


# ---- the comparison's own machinery: a real Worker, no audio tools needed ---------------------------------


@pytest.fixture
def bare(tmp_path, monkeypatch):
    """A Worker and a noisy request, with Deezer's search answering one record both readings accept."""
    settings = Settings(_env_file=None, telegram_api_id=1, telegram_api_hash="h", library_root=tmp_path / "lib",
                        data_dir=tmp_path / "data")
    store = Store(settings.db_path)
    w = Worker(store, FakeSource(), FakeCatalog(), MemoryNotifier(), settings)
    monkeypatch.setattr(interpret_mod, "DEEZER_INTERVAL_S", 0)

    async def search(text, http, *, limit=10):
        return [_dz(55)]

    monkeypatch.setattr(interpret_mod, "search_deezer", search)
    readings = interpretations(NOISY, "Label", 3)
    rid = store.add_request(NOISY, RequestKind.YT_TRACK, source_url=URL, query=readings[0], interpretations=readings)
    return w, store, store.get_request(rid)


VIDEO = AcousticReference("youtube", "abc", [[1]], [1, 2, 3, 4], 0.0, 30.0)


async def test_waiting_for_the_cpu_slot_does_not_spend_the_budget(bare, monkeypatch):
    """The real requests hold the CPU slot longer than the whole budget; the comparison waits its turn and
    still finishes, rather than being written off as `skipped` on a busy machine."""
    w, store, req = bare
    monkeypatch.setattr(interpret_mod, "BUDGET_S", 0.3)
    w._cpu = asyncio.Semaphore(1)                          # a small machine: one slot, and it is taken

    async def preview_url(deezer_id, http):
        return "https://p/55.mp3"

    async def download(url, http):
        return b"ID3"

    monkeypatch.setattr(ref_mod, "_preview_url", preview_url)
    monkeypatch.setattr(ref_mod, "_download_preview", download)
    monkeypatch.setattr(ref_mod, "_needles", lambda path, start, length: [[1, 2, 3]])
    monkeypatch.setattr(ref_mod, "compare", lambda needle, hay: (0.99, 0))

    async def busy():
        async with w._cpu:
            await asyncio.sleep(1.0)

    holder = asyncio.create_task(busy())
    await asyncio.sleep(0)
    w._interpret(req, VIDEO)
    await w._shadow_settled()
    await holder
    assert store.get_interpretation_outcome(req.id) == (CONFIRMED, 0)


async def test_a_preview_that_could_not_be_checked_is_skipped_not_ambiguous(bare, monkeypatch, caplog):
    """Deezer refusing the preview lookup (its quota) is not "this record is not the video": whatever the
    readings found cannot be told apart from what went unchecked."""
    w, store, req = bare
    caplog.set_level(logging.INFO, logger="flackey.worker.interpret")

    async def unfetchable(reference, cands, http, tmp_dir, *, minimum, limit=5, cpu=None):
        return Identification(None, None, [(c.deezer_id, None) for c in cands], "none could be fetched")

    monkeypatch.setattr(interpret_mod, "identify_record", unfetchable)
    w._interpret(req, VIDEO)
    await w._shadow_settled()
    assert store.get_interpretation_outcome(req.id) == (SKIPPED, None)
    assert "1 of 1 previews could not be checked" in caplog.text


async def test_a_failed_write_is_logged_with_its_request_not_lost_in_the_task(bare, monkeypatch, caplog):
    w, store, req = bare

    async def no_match(reference, cands, http, tmp_dir, *, minimum, limit=5, cpu=None):
        return Identification(None, None, [(c.deezer_id, 0.1) for c in cands], "none")

    def locked(*a, **kw):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(interpret_mod, "identify_record", no_match)
    monkeypatch.setattr(store, "set_interpretation_outcome", locked)
    w._interpret(req, VIDEO)
    await w._shadow_settled()
    assert f"req#{req.id}: could not record the comparison" in caplog.text and not w._shadow_tasks

    async def boom():
        raise RuntimeError("unforeseen")

    task = asyncio.create_task(boom())
    await asyncio.wait([task])
    w._shadow_tasks[req.id] = task
    w._shadow_done(req.id, task)                          # what the done callback does with an escaped error
    assert f"req#{req.id}: comparing the title's readings failed" in caplog.text and not w._shadow_tasks


async def test_stopping_a_request_drops_its_comparison_queued_or_running(bare, monkeypatch):
    w, store, req = bare
    gate = asyncio.Event()

    async def stuck(text, http, *, limit=10):
        await gate.wait()
        return []

    monkeypatch.setattr(interpret_mod, "search_deezer", stuck)
    readings = store.get_interpretations(req.id)
    other = store.get_request(store.add_request(NOISY, RequestKind.YT_TRACK, source_url=URL + "2",
                                                query=readings[0], interpretations=readings))
    w._interpret(req, VIDEO)
    w._interpret(other, VIDEO)                             # queued behind the first
    await asyncio.sleep(0.01)
    running, queued = w._shadow_tasks[req.id], w._shadow_tasks[other.id]
    await w.cancel(req.id)
    w.forget_comparison(other.id)
    await asyncio.wait([running, queued])
    assert running.cancelled() and queued.cancelled() and not w._shadow_tasks
    assert store.get_interpretation_outcome(req.id) == (None, None)
    assert not w._shadow.locked()                          # the next comparison can still have its turn
