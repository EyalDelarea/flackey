from flackey.match import (
    THRESHOLD,
    candidate_version,
    decide,
    identification_order,
    same_version,
    score_candidate,
)
from flackey.models import Candidate, CatalogTrack, Query

CT = CatalogTrack(id=16552105, isrc="UKU932231081", artist="Astral Projection", title="Into the Void",
                  mix_name="Original Mix", label="L", genre="G", duration_ms=442816)
Q = Query(raw="astral projection into the void", artist="Astral Projection", title="Into the Void")


def cand(**kw) -> Candidate:
    base = {"source": "deezer_bot", "source_ref": "dz_track:1:send", "artist": "Astral Projection",
                "title": "Into the Void", "duration_s": 442, "rank": 1}
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


def test_decide_auto_files_without_catalog_when_confident():
    # No Beatport record is no longer a reason to ask the owner: the download is fingerprinted against
    # the request's own recording, and a candidate that does not convince on text still parks.
    d = decide(Q, [cand()], None)
    assert d.auto and "recording check" in d.reason
    assert not decide(Q, [cand(artist="Someone Else", title="Something", duration_s=100)], None).auto


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


# ---- which version: from metadata, not the fingerprint (issue #172) -------------------------------------
# Hallucinogen's "LSD": the 1995 original on "Twisted" and the 2024 remaster on "The Albums Remastered"
# share an ISRC, a length within 0.2 s and a fingerprint (0.96 each against the 2024 video). Deezer lists
# the remaster with no title_version, so only its album says which one it is.
LSD_ISRC = "USA2P0300457"
LSD_RECORD = CatalogTrack(id=7, isrc=LSD_ISRC, artist="Hallucinogen", title="LSD", mix_name="Original Mix",
                          label="Twisted", genre="Psy-Trance", release_name="Twisted", duration_ms=403000)
LSD_REMASTER_Q = Query(raw="Hallucinogen - LSD (Remastered 2024)", artist="Hallucinogen", title="LSD",
                       version="Remastered 2024", duration_s=403)
LSD_Q = Query(raw="Hallucinogen - LSD", artist="Hallucinogen", title="LSD", duration_s=403)


def lsd_pair() -> tuple[Candidate, Candidate]:
    original = Candidate(source="deezer_bot", source_ref="dz:1", artist="Hallucinogen", title="LSD", duration_s=403,
                         deezer_id=1, isrc=LSD_ISRC, rank=0, album="Twisted", release_date="1995-06-01")
    remaster = Candidate(source="deezer_bot", source_ref="dz:2", artist="Hallucinogen", title="LSD", duration_s=403,
                         deezer_id=2, isrc=LSD_ISRC, rank=1, album="The Albums Remastered",
                         release_date="2023-11-24")
    return original, remaster


def test_an_album_that_says_remaster_names_the_candidate_version():
    original, remaster = lsd_pair()
    assert candidate_version(original) == "Original Mix" and candidate_version(remaster) == "Remastered"


def test_a_remaster_request_picks_the_remaster_on_the_text_path():
    original, remaster = lsd_pair()
    d = decide(LSD_REMASTER_Q, [original, remaster], None)
    assert d.chosen is remaster and d.auto


def test_an_original_request_picks_the_original_and_still_takes_a_lone_remaster():
    original, remaster = lsd_pair()
    d = decide(LSD_Q, [remaster, original], None)
    assert d.chosen is original and d.auto
    # A remaster is the original arrangement: alone, it is no "only remixes" refusal
    alone = decide(LSD_Q, [remaster], None)
    assert alone.auto and alone.chosen is remaster


def test_a_remaster_request_tries_only_the_remaster_preview_on_the_youtube_path():
    """The fingerprint matches both previews alike, so the one tried first is the one filed: the original's
    preview must not be tried at all for a remaster video."""
    original, remaster = lsd_pair()
    cands = [original, remaster]
    decide(LSD_REMASTER_Q, cands, None)                       # scores them, as the pipeline does first
    assert identification_order(LSD_REMASTER_Q, cands) == [remaster]
    decide(LSD_Q, cands, None)
    assert identification_order(LSD_Q, cands) == [original, remaster]   # no version: the original first
    assert identification_order(LSD_REMASTER_Q, [original]) == []


def test_a_shared_isrc_settles_a_match_only_when_the_versions_agree():
    original, remaster = lsd_pair()
    assert score_candidate(LSD_Q, original, LSD_RECORD).isrc
    assert not score_candidate(LSD_Q, remaster, LSD_RECORD).isrc
    remaster_record = CatalogTrack(**{**LSD_RECORD.__dict__, "id": 8, "release_name": "The Albums Remastered"})
    assert score_candidate(LSD_REMASTER_Q, remaster, remaster_record).isrc


def test_same_version_reads_the_album_and_the_release():
    original, remaster = lsd_pair()
    assert same_version(original, LSD_RECORD) and not same_version(remaster, LSD_RECORD)
