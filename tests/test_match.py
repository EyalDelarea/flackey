from flackey.match import THRESHOLD, candidate_version, decide, score_candidate
from flackey.models import Candidate, CatalogTrack, Query

CT = CatalogTrack(id=16552105, isrc="UKU932231081", artist="Astral Projection", title="Into the Void",
                  mix_name="Original Mix", label="L", genre="G", duration_ms=442816)
Q = Query(raw="astral projection into the void", artist="Astral Projection", title="Into the Void")


def cand(**kw) -> Candidate:
    base = dict(source="deezer_bot", source_ref="dz_track:1:send", artist="Astral Projection",
                title="Into the Void", duration_s=442, rank=1)
    return Candidate(**{**base, **kw})


def test_exact_original_scores_high():
    s = score_candidate(Q, cand(), CT)
    assert s.total >= 95 and s.version == 20 and s.duration == 30


def test_isrc_match_is_certain():
    s = score_candidate(Q, cand(isrc="UKU932231081", duration_s=300), CT)
    assert s.total == 100 and s.isrc


def test_remix_without_requested_version_loses_version_points():
    remix = cand(title="Into the Void (Vini Vici Remix)", mix_name="Vini Vici Remix")
    s = score_candidate(Q, remix, CT)
    # artist 25 + title 25 + version 0 + duration 30 = exactly 80: the threshold alone would let it through,
    # so `decide` must park a non-original top pick whenever no version was requested.
    assert s.version == 0 and s.total == THRESHOLD
    d = decide(Q, [remix], CT)
    assert not d.auto and "version" in d.reason.lower()


def test_decide_picks_lower_ranked_original_over_remix():
    remix = cand(mix_name="Vini Vici Remix", rank=1)                           # 80: full duration, no version points
    original = cand(source_ref="dz_track:2:send", duration_s=453, rank=2)       # 82: 10 s off, 12 duration points
    d = decide(Q, [remix, original], CT)
    assert d.auto and d.chosen.source_ref == "dz_track:2:send"


def test_requested_version_must_match():
    q = Query(raw="", artist="Astral Projection", title="Into the Void", version="Vini Vici Remix")
    assert score_candidate(q, cand(mix_name="Vini Vici Remix"), None).version == 20
    assert score_candidate(q, cand(), None).version == 0


def test_duration_decays_linearly():
    assert score_candidate(Q, cand(duration_s=443), CT).duration == 30
    assert score_candidate(Q, cand(duration_s=450), CT).duration < 30
    assert score_candidate(Q, cand(duration_s=470), CT).duration == 0


def test_duration_uses_youtube_length_without_catalog():
    q = Query(raw="", artist="Astral Projection", title="Into the Void", duration_s=442)
    assert score_candidate(q, cand(duration_s=442), None).duration == 30
    assert score_candidate(q, cand(duration_s=600), None).duration == 0


def test_raw_query_without_catalog_penalises_partial_token_matches():
    q = Query(raw="astral projection into the void")
    full = score_candidate(q, cand(), None)
    subset = score_candidate(q, cand(artist="The Void", title="Into the Void"), None)
    assert full.artist + full.title == 50 and subset.artist + subset.title < 35


def test_candidate_version_falls_back_to_title():
    assert candidate_version(cand()) == "Original Mix"
    assert candidate_version(cand(title="Into the Void (Radio Edit)")) == "Radio Edit"
    assert candidate_version(cand(mix_name="Extended Mix")) == "Extended Mix"


def test_decide_auto_when_confident():
    d = decide(Q, [cand(), cand(source_ref="dz_track:2:send", title="Into the Void (Live)", rank=2)], CT)
    assert d.auto and d.chosen.source_ref == "dz_track:1:send"


def test_decide_parks_when_below_threshold():
    d = decide(Q, [cand(artist="Someone Else", title="Something", duration_s=100)], CT)
    assert not d.auto and "below" in d.reason and d.chosen is not None


def test_decide_parks_without_catalog():
    d = decide(Q, [cand()], None)
    assert not d.auto and "Beatport" in d.reason


def test_decide_parks_when_only_remixes_and_no_version_requested():
    d = decide(Q, [cand(mix_name="Vini Vici Remix"), cand(mix_name="Radio Edit", rank=2)], CT)
    assert not d.auto and "version" in d.reason.lower()


def test_decide_excludes_wrong_versions_when_version_requested():
    q = Query(raw="", artist="Astral Projection", title="Into the Void", version="Vini Vici Remix")
    d = decide(q, [cand(), cand(mix_name="Vini Vici Remix", source_ref="dz_track:2:send", rank=2)], CT)
    assert d.chosen.source_ref == "dz_track:2:send"


def test_decide_no_candidates():
    d = decide(Q, [], CT)
    assert not d.auto and d.chosen is None


def test_is_original_word_boundary_excludes_aboriginal():
    # "Aboriginal Mix" must not be treated as an original mix just because it contains "original"
    s = score_candidate(Q, cand(mix_name="Aboriginal Mix"), CT)
    assert s.version == 0


def test_is_original_word_boundary_matches_original_variants():
    for mix in ["Original Mix", "Original", "Original Version"]:
        assert score_candidate(Q, cand(mix_name=mix), CT).version == 20


def test_duration_prefers_youtube_length_over_catalog_length():
    # the video is what was asked for; a wrong Beatport release must not redefine the target length
    q = Query(raw="", artist="Astral Projection", title="People Can Fly", duration_s=596)
    wrong_release = CatalogTrack(**{**CT.__dict__, "duration_ms": 429000})
    assert score_candidate(q, cand(duration_s=595), wrong_release).duration == 30
    assert score_candidate(q, cand(duration_s=429), wrong_release).duration == 0


def test_video_length_picks_an_edit_over_a_longer_original():
    # the video is 532 s; Deezer offers the 544 s "original" and a 531 s "Album Edit". The edit is what
    # was asked for, so it is accepted even though no version was requested.
    q = Query(raw="", artist="Filteria", title="Dog Days Bliss", duration_s=532)
    ct = CatalogTrack(**{**CT.__dict__, "artist": "Filteria", "title": "Dog Days Bliss", "duration_ms": 544000})
    edit = cand(artist="Filteria", title="Dog Days Bliss", mix_name="Album Edit", duration_s=531, source_ref="dz_track:2:send", rank=1)
    original = cand(artist="Filteria", title="Dog Days Bliss", duration_s=544, rank=2)
    d = decide(q, [edit, original], ct)
    assert d.auto and d.chosen is edit


def test_remix_matching_video_length_still_parks_when_an_original_also_matches():
    q = Query(raw="", artist="Astral Projection", title="Into the Void", duration_s=442)
    remix = cand(mix_name="Vini Vici Remix", duration_s=442, source_ref="dz_track:2:send", rank=1)
    d = decide(q, [remix, cand(rank=2)], CT)
    assert d.auto and d.chosen.source_ref == "dz_track:1:send"
