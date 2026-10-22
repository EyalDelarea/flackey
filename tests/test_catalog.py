from pathlib import Path

import pytest

from flackey.catalog import (
    BeatportCatalog,
    CatalogParseError,
    _is_the_song,
    _length_pins_a_version,
    best_match,
    parse_search_html,
    with_original_date,
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
    # bonus and fall behind a genuine (if less exact) "Original Mix" match. The less exact title has to
    # stay above TITLE_FLOOR (#63) -- a record whose title disagrees outright is now refused outright,
    # so "Into Nowhere" would never reach the comparison this test is about.
    q = Query(raw="", artist="Astral Projection", title="Into the Void")
    aboriginal = CatalogTrack(id=1, artist="Astral Projection", title="Into the Void",
                               mix_name="Aboriginal Mix", label="L", genre="G")
    genuine_original = CatalogTrack(id=2, artist="Astral Projection", title="Into the Voids",
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


def _row(**kw) -> CatalogTrack:
    base = {"id": 1, "artist": "New Born", "title": "Between The Lines", "mix_name": "Original Mix",
            "label": "L", "genre": "G", "duration_ms": 355000}
    return CatalogTrack(**{**base, **kw})


def test_best_match_refuses_a_record_whose_title_disagrees():
    # req#205: artist 100, title 44, average 72.2 -- accepted today, and the file was mistagged for it
    q = Query(raw="", artist="New Born", title="Nothing but a Title", duration_s=353)
    assert best_match(q, [_row()]) is None


def test_best_match_keeps_a_superset_title():
    # token_set_ratio scores a polluted-but-correct title at 100; the floor must not touch it
    q = Query(raw="", artist="Sheyba", title="Ganesh - Flying Rhino Records - 1995", duration_s=431)
    t = _row(artist="Sheyba", title="Ganesh", duration_ms=431000)
    assert best_match(q, [t]) is t


def test_source_isrc_bypasses_the_title_floor():
    q = Query(raw="", artist="New Born", title="Nothing but a Title")
    t = _row(isrc="QT6EC2645809")
    assert best_match(q, [t]) is None
    assert best_match(q, [t], preferred_isrc="qt6ec2645809") is t


def test_only_the_asked_song_by_the_asked_artist_can_pin_a_version_by_length():
    """Darude's "Sandstorm", official video (232 s, no version asked): Beatport has the 443 s original and a
    different act's remix that happens to be the video's length -- "D'rude", with the remix credit inside its
    title. The remix pinned itself by length, outscored the original and named the request until Soulseek's
    copies of it failed the fingerprint. A length pins a version of *this* song only: the same artist, and a
    title that says nothing the asked one does not."""
    q = Query(raw="", artist="Darude", title="Sandstorm", duration_s=232)
    original = _ct(1, "Darude", title="Sandstorm", dur_s=443, date="2000-01-01")
    remix = _ct(2, "D'rude", title="Sandstorm (TML 2025 RMX)", mix="Extended Version", dur_s=231, date="2025-07-01")
    assert not _length_pins_a_version(q, [original, remix])
    assert best_match(q, [original, remix]) is original
    assert best_match(q, [remix]) is None
    # The same remix credited to the asked artist, under a plain title, is still the one the length singles out
    album = _ct(3, "Darude", title="Sandstorm", mix="Album Edit", dur_s=231)
    assert best_match(q, [original, remix, album]) is album


# ---- the original release's date --------------------------------------------------------------------
# Beatport's search for "Daft Punk One More Time" lists the recording on several releases. The one that wins
# the match can be a later compilation, and its date tagged the track 2005.

def _dp(id, *, isrc="GBDUW0000059", artist="Daft Punk", mix="Original Mix", release="Musique Vol. 1 1993 - 2005",
        date="2005-03-14", dur_s=320, title="One More Time"):
    return CatalogTrack(id=id, isrc=isrc, artist=artist, title=title, mix_name=mix, label="L", genre="G",
                        release_name=release, release_date=date, duration_ms=dur_s * 1000 if dur_s else None)


def test_the_original_date_is_the_earliest_release_of_the_same_recording():
    chosen = _dp(1)
    album = _dp(2, artist="Daft Punk, Romanthony", mix="Album Version", release="Discovery", date="2001-03-12")
    remix = _dp(3, isrc="GBDUW0000060", mix="Romanthony's Unplugged", release="One More Time", date="2000-11-13")
    radio = _dp(4, mix="Radio Edit", release="One More Time", date="2000-11-13", dur_s=230)   # a different cut
    dated = with_original_date(chosen, [chosen, album, remix, radio])
    assert (dated.release_date, dated.original_date, dated.tag_date, dated.year) == (
        "2005-03-14", "2001-03-12", "2001-03-12", "2001")
    assert chosen.original_date is None                       # a copy, the search result is left alone


@pytest.mark.parametrize("others", [
    [],                                                                   # Beatport has no other release of it
    [_dp(2, date="2006-06-01")],                                          # later releases say nothing
    [_dp(2, date="2005-01-03")],                                          # the same year: nothing to change
    [_dp(2, isrc="GBDUW0000099", date="2001-03-12")],                     # another ISRC: another recording
    [_dp(2, isrc=None, mix="Album Version", date="2001-03-12")],          # no ISRC to say so, and another mix
    [_dp(2, isrc=None, dur_s=None, date="2001-03-12")],                   # no length to compare
    [_dp(2, mix="2001 Remaster", release="Discovery (Remastered)", date="2001-03-12")],  # never a remaster
    [_dp(2, date="not a date")],
], ids=["alone", "later", "same-year", "other-isrc", "other-mix", "no-length", "remaster", "garbage"])
def test_the_releases_own_date_stands_without_an_earlier_release_of_the_recording(others):
    chosen = _dp(1)
    assert with_original_date(chosen, [chosen, *others]) is chosen


def test_without_isrcs_the_same_act_title_mix_and_length_is_the_same_recording():
    chosen = _dp(1, isrc=None)
    album = _dp(2, isrc=None, release="Discovery", date="2001-03-12", dur_s=322)
    assert with_original_date(chosen, [album]).original_date == "2001-03-12"


def test_a_remaster_keeps_its_own_year():
    """Asked for, or named in the record: the remaster's year is what tells it from the original."""
    album = _dp(2, release="Discovery", date="2001-03-12")
    remaster = _dp(1, mix="Remastered 2021", date="2021-02-22")
    assert with_original_date(remaster, [album]) is remaster
    plain = _dp(1, date="2021-02-22")
    assert with_original_date(plain, [album], asked="Remastered 2021") is plain
    assert with_original_date(plain, [album]).original_date == "2001-03-12"


def test_another_sources_date_counts_and_an_earlier_one_found_before_is_kept():
    chosen = _dp(1)
    assert with_original_date(chosen, also=["2001-03-07"]).original_date == "2001-03-07"
    assert with_original_date(chosen, also=["2001"]).original_date == "2001"           # Discogs gives a year
    known = with_original_date(chosen, also=["2001-03-07"])
    assert with_original_date(known, also=["2003-01-01"]) is known


@pytest.mark.parametrize("asked, credited, title", [
    ("Darude", "Darude, Ashley Wallbridge", "Sandstorm"),             # Beatport credits every act
    ("Artist ft. Singer", "Artist, Singer", "Sandstorm"),             # the video credits the singer as feat.
    ("Above & Beyond", "Above & Beyond, Zoë Johnston", "Sandstorm"),  # an act with "&" in its own name
    ("Tiësto", "Tiesto", "Sandstorm"),                                # an accent one side spells and one not
    ("Darude", "Darude", "Sandstorm (feat. Singer)"),                 # a feature credit inside the title
    ("Darude", "Darude", "Sandstorm feat. Singer"),                   # ... or after it, bracketless
    ("Artist", "Artist feat. Singer", "Sandstorm"),                   # Beatport naming the feature in the act
], ids=["co-credit", "feat", "ampersand-act", "accent", "feat-in-title", "bare-feat-in-title",
        "feat-in-credit"])
def test_the_asked_act_among_the_credits_still_pins_its_edit_by_length(asked, credited, title):
    q = Query(raw="", artist=asked, title="Sandstorm", duration_s=232)
    original = _ct(1, credited, title=title, dur_s=443, date="2000-01-01")
    edit = _ct(2, credited, title=title, mix="Radio Edit", dur_s=231, date="2000-01-01")
    assert _length_pins_a_version(q, [original, edit])
    assert best_match(q, [original, edit]) is edit


@pytest.mark.parametrize("title", [
    "Sandstorm feat. Singer - TML Remix",
    "Sandstorm ft Singer – TML Remix",
    "Sandstorm featuring Singer (TML Remix)",
])
def test_a_bracketless_feature_credit_does_not_hide_the_remix_after_it(title):
    """`_FEAT_RE` ate an unbracketed "feat. X" to the end of the text, remix credit and all, so this
    remix by the asked act read as the plain song and could pin itself by length (#178)."""
    q = Query(raw="", artist="Darude", title="Sandstorm", duration_s=232)
    assert not _is_the_song(q, _ct(1, "Darude", title=title, dur_s=231))


def test_an_asked_title_with_a_bracketless_feature_keeps_its_remix_words():
    """The same pattern reads the asked title: "X feat. Y - TML Remix" asked is Beatport's "X (TML Remix)"."""
    q = Query(raw="", artist="Darude", title="Sandstorm feat. Singer - TML Remix", duration_s=232)
    assert _is_the_song(q, _ct(1, "Darude", title="Sandstorm (TML Remix)", dur_s=231))


def test_an_act_with_an_ampersand_is_one_act_not_two():
    """Only commas and feature words split a credit: "Above & Beyond" is not "Above" and "Beyond"."""
    q = Query(raw="", artist="Beyond", title="Sandstorm", duration_s=232)
    assert not _is_the_song(q, _ct(1, "Above & Beyond", title="Sandstorm", dur_s=231))
