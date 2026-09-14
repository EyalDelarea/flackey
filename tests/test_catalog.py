from pathlib import Path

import pytest

from flackey.catalog import (
    BeatportCatalog,
    CatalogParseError,
    _length_pins_a_version,
    best_match,
    parse_search_html,
)
from flackey.models import CatalogTrack, Query


@pytest.fixture
def tracks(fixtures: Path):
    return parse_search_html((fixtures / "beatport_search_astral.html").read_text())


def test_parse_search_html_extracts_full_records(tracks):
    assert len(tracks) == 12
    t = tracks[0]
    assert t.id == 16552105
    assert (t.artist, t.title, t.mix_name) == ("Astral Projection", "Into the Void", "Original Mix")
    assert (t.label, t.genre, t.sub_genre) == ("Sacred Technology", "Psy-Trance", "Goa Trance")
    assert (t.bpm, t.key, t.isrc) == (142, "A Major", "UKU932231081")
    assert t.release_date == "2022-06-03" and t.year == "2022"
    assert t.duration_ms == 442816 and t.duration_s == 443
    assert t.catalog_number == "SACTEC169"
    assert t.artwork_url.endswith(".jpg") and "1400x1400" in t.artwork_url


def test_parse_search_html_joins_multiple_artists():
    html = ('<script id="__NEXT_DATA__" type="application/json">{"props":{"pageProps":{"dehydratedState":'
            '{"queries":[{"state":{"data":{"data":[{"track_id":1,"track_name":"T","mix_name":"Original Mix",'
            '"artists":[{"artist_name":"A"},{"artist_name":"B"}],"label":{"label_name":"L"},'
            '"genre":[{"genre_name":"G"}],"bpm":140,"key_name":"A Minor","length":1000}]}}}]}}}}</script>')
    t = parse_search_html(html)[0]
    assert t.artist == "A, B" and t.sub_genre is None and t.isrc is None


def test_parse_search_html_without_next_data_raises():
    with pytest.raises(CatalogParseError):
        parse_search_html("<html>Just a moment...</html>")


def test_query_text_drops_version_only_when_artist_and_title_known():
    q = Query(raw="", artist="Astral Projection", title="Into the Void", version="Vini Vici Remix")
    assert BeatportCatalog.query_text(q) == "Astral Projection Into the Void"
    q = Query(raw="Into The Void (Vini Vici Remix)", artist=None, title="Into The Void", version="Vini Vici Remix")
    assert BeatportCatalog.query_text(q) == "Into The Void (Vini Vici Remix)"
    assert BeatportCatalog.query_text(Query(raw="free text")) == "free text"


def test_search_url_encodes_query():
    assert BeatportCatalog.search_url("astral projection into the void") == \
        "https://www.beatport.com/search/tracks?q=astral%20projection%20into%20the%20void"


def test_best_match_prefers_original_mix_and_exact_title(tracks):
    q = Query(raw="", artist="Astral Projection", title="Into the Void")
    m = best_match(q, tracks)
    assert m.id == 16552105  # the 2022 Sacred Technology original, not the 89 BPM re-release


def test_best_match_honours_requested_version(tracks):
    q = Query(raw="", artist="Axtral", title="Astral Projection", version="Kulage Remix")
    assert best_match(q, tracks).mix_name == "Kulage Remix"


def test_best_match_returns_none_for_unrelated(tracks):
    assert best_match(Query(raw="", artist="Daft Punk", title="One More Time"), tracks) is None


def test_best_match_uses_raw_text_when_unstructured(tracks):
    assert best_match(Query(raw="astral projection into the void"), tracks).id == 16552105


def test_best_match_word_boundary_excludes_aboriginal_mix():
    # "Aboriginal Mix" must not be treated as an original mix just because it contains "original":
    # an exact-title match tagged "Aboriginal Mix" should lose the no-version-requested original-mix
    # bonus and fall behind a genuine (if less exact) "Original Mix" match.
    q = Query(raw="", artist="Astral Projection", title="Into the Void")
    aboriginal = CatalogTrack(id=1, artist="Astral Projection", title="Into the Void",
                               mix_name="Aboriginal Mix", label="L", genre="G")
    genuine_original = CatalogTrack(id=2, artist="Astral Projection", title="Into Nowhere",
                                     mix_name="Original Mix", label="L", genre="G")
    m = best_match(q, [aboriginal, genuine_original])
    assert m.id == 2


def _ct(id, artist, title="People Can Fly", mix="Original Mix", dur_s=None, date=None):
    return CatalogTrack(id=id, artist=artist, title=title, mix_name=mix, label="L", genre="G",
                        duration_ms=dur_s * 1000 if dur_s else None, release_date=date)


def test_best_match_prefers_release_whose_length_matches_the_video():
    q = Query(raw="", artist="Astral Projection", title="People Can Fly", duration_s=596)
    tracks = [_ct(1, "Astral Projection, Alien Project", dur_s=429), _ct(2, "Astral Projection", dur_s=596)]
    assert best_match(q, tracks).id == 2


def test_best_match_artist_superset_is_not_an_exact_artist():
    q = Query(raw="", artist="Hallucinogen", title="Angelic Particles")
    tracks = [_ct(1, "Hallucinogen In Dub", title="Angelic Particles"), _ct(2, "Hallucinogen", title="Angelic Particles")]
    assert best_match(q, tracks).id == 2


def test_best_match_prefers_earliest_release_among_equals():
    q = Query(raw="", artist="Hallucinogen", title="LSD")
    tracks = [_ct(1, "Hallucinogen", title="LSD", dur_s=409, date="2005-09-05"),
              _ct(2, "Hallucinogen", title="LSD", dur_s=403, date="1996-10-01")]
    assert best_match(q, tracks).id == 2


def test_same_named_reissues_use_source_isrc_before_release_date():
    from dataclasses import replace

    q = Query(raw="", artist="1200 Micrograms", title="Acid For Nothing", duration_s=420)
    early = CatalogTrack(id=1, artist="1200 Micrograms", title="Acid For Nothing",
                         mix_name="Original Mix", label="Starbox Music", genre="Psy-Trance",
                         isrc="OLD", release_date="2003-01-01", duration_ms=420000)
    tip = replace(early, id=2, label="Tip Records", isrc="TIP", release_date="2020-03-02")
    assert best_match(q, [early, tip]).id == 1
    assert best_match(q, [early, tip], preferred_isrc="tip").id == 2


def test_best_match_treats_lengths_within_tolerance_as_equal():
    # 604 s vs 605 s is encoding slack, not a different recording: the earliest release still wins
    q = Query(raw="", artist="Celestial Intelligence", title="Inevitable Feelings", duration_s=605)
    tracks = [_ct(1, "Celestial Intelligence", title="Inevitable Feelings", dur_s=605, date="2024-10-05"),
              _ct(2, "Celestial Intelligence", title="Inevitable Feelings", dur_s=604, date="2015-06-05")]
    assert best_match(q, tracks).id == 2


def test_the_videos_length_outweighs_the_original_mix_bonus_when_no_original_is_that_length():
    """Filteria's "Dog Days Bliss": Beatport carries the album (Suntrip, 531 s, tagged "Album Edit") and a
    later compilation (Ovnimoon, 544 s, tagged "Original Mix"). The flat original-mix bonus used to pick the
    compilation, whose 544 s master exists nowhere on Soulseek, and the reference duration it fixed then
    rejected all eight real FLACs. When the requested length singles out one release and no original mix is
    that length, the length wins -- the same rule `match._pinned_by_length` applies to source candidates."""
    q = Query(raw="", artist="Filteria", title="Dog Days Bliss", duration_s=532)
    album = _ct(1, "Filteria", title="Dog Days Bliss", mix="Album Edit", dur_s=531, date="2022-06-25")
    compilation = _ct(2, "Filteria", title="Dog Days Bliss", mix="Original Mix", dur_s=544, date="2015-10-09")
    assert best_match(q, [album, compilation]).id == 1


def test_an_original_mix_that_matches_the_length_still_beats_a_remix_of_the_same_length():
    q = Query(raw="", artist="Filteria", title="Dog Days Bliss", duration_s=532)
    remix = _ct(1, "Filteria", title="Dog Days Bliss", mix="Nova Fractal Remix", dur_s=531, date="2010-01-01")
    original = _ct(2, "Filteria", title="Dog Days Bliss", mix="Original Mix", dur_s=533, date="2013-05-05")
    assert best_match(q, [remix, original]).id == 2


def test_a_remix_far_from_the_videos_length_keeps_losing_to_the_original():
    """S.U.N. Project's "Space Dwarfs": the 559 s original is what the 560 s video is, and the 1997 mixes and
    the Morphic Resonance remix are genuinely other recordings. This is the case the length-pinning rule must
    leave alone -- an original mix is already the right length, so nothing pins a version. The rows are the
    six Beatport actually returns for this search; nothing about this case may change."""
    q = Query(raw="", artist="S.U.N. Project", title="Space Dwarfs", duration_s=560)
    tracks = [_ct(1, "S.U.N. Project", title="Space Dwarfs", mix="Morphic Resonance Remix", dur_s=549, date="2016-05-09"),
              _ct(2, "Sun Project", title="Space Dwarfs", mix="Original Mix", dur_s=559, date="2009-02-20"),
              _ct(3, "Sun Project", title="Space Dwarfs", mix="1997 Mix", dur_s=530, date="2022-12-23"),
              _ct(4, "Sun Project", title="Space Dwarfs", mix="1997 mix", dur_s=529, date="2022-11-05"),
              _ct(5, "Sun Project", title="Space Dwarfs", mix="Atomic Pulse Remix", dur_s=452, date="2009-10-30"),
              _ct(6, "Sun Project", title="Space Dwarfs", mix="1997 Unreleased Mix", dur_s=532, date="2021-09-21")]
    assert not _length_pins_a_version(q, tracks)
    assert best_match(q, tracks).id == 2
