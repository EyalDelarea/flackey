"""The one version model (issue #172): an arrangement, and whether it is a remaster."""
import pytest

from flackey.models import CatalogTrack
from flackey.version import (
    Version,
    agrees,
    check_version,
    extended_mix_name,
    file_cut,
    record_version,
    same_owned_version,
    split_version,
    with_remaster,
)


@pytest.mark.parametrize("text, expected", [
    (None, Version()),
    ("Original Mix", Version()),
    ("Remastered 2024", Version("", True, "2024")),
    ("2011 Remaster", Version("", True, "2011")),
    ("Digital Remaster", Version("", True, None)),
    ("Remastered Version", Version("", True, None)),
    ("Original Mix - Remastered", Version("", True, None)),
    ("Extended Mix", Version(cut="extended")),
    ("Extended Mix Remastered 2024", Version("", True, "2024", "extended")),
    ("Vini Vici Remix", Version("vini vici remix")),
])
def test_a_version_text_is_an_arrangement_and_a_remaster_flag(text, expected):
    assert split_version(text) == expected


def test_context_can_only_say_remaster():
    """An album called "Remixes" makes no track a remix; an album that says remaster makes it the remaster."""
    assert split_version(None, "LSD", "The Albums Remastered (2023)") == Version("", True, "2023")
    assert split_version("Original Mix", "Twisted") == Version()
    assert split_version(None, "The Remixes") == Version()


def test_a_beatport_record_is_a_remaster_when_its_release_says_so():
    rec = CatalogTrack(id=1, artist="Hallucinogen", title="LSD", mix_name="Original Mix", label="Twisted",
                       genre="Psy-Trance", release_name="The Albums Remastered")
    asked = split_version("Remastered 2024")
    assert record_version(rec, asked) == Version("", True, None)
    assert not record_version(CatalogTrack(**{**rec.__dict__, "release_name": "Twisted"}), asked).remaster


@pytest.mark.parametrize("asked", [None, "Radio Edit", "Extended Mix", "Vini Vici Remix"])
def test_a_remastered_release_says_nothing_unless_the_remaster_was_asked_for(asked):
    """Classic catalogue sits on "Remastered" releases: a request for the track, or for its radio edit,
    extended mix or a remix, still means the record as filed."""
    mix = asked or "Original Mix"
    rec = CatalogTrack(id=1, artist="A", title="T", mix_name=mix, label="L", genre="G",
                       release_name="Greatest Hits (Remastered)")
    have = record_version(rec, split_version(asked))
    assert not have.remaster and agrees(split_version(asked), have)


def test_a_track_with_no_version_anywhere_is_the_original():
    rec = CatalogTrack(id=1, artist="A", title="T", mix_name=None, label="L", genre="G", release_name=None)
    assert record_version(rec) == record_version(rec, split_version("Remastered")) == Version()
    assert split_version(None, None) == Version()
    assert check_version(Version(), path="", tags={}) is None


def test_agreement_is_the_arrangement_and_the_remaster_flag_not_the_year():
    remaster_2024 = split_version("Remastered 2024")
    assert agrees(remaster_2024, split_version(None, "The Albums Remastered (2023)"))   # peers' 2023 box set
    assert agrees(remaster_2024, split_version("Remastered"))
    assert not agrees(remaster_2024, split_version("Original Mix"))
    assert not agrees(Version(), remaster_2024)
    assert agrees(split_version("Extended"), split_version("Extended Mix"))
    assert not agrees(split_version("Extended Mix"), split_version("Extended Mix Remastered"))
    assert not agrees(split_version("Vini Vici Remix"), Version())


def test_a_record_that_does_not_word_its_remaster_is_given_the_word():
    assert with_remaster("Original Mix", True) == "Remastered"
    assert with_remaster(None, True) == "Remastered"
    assert with_remaster("Remastered 2024", True) == "Remastered 2024"
    assert with_remaster("Extended Mix", True) == "Extended Mix Remastered"
    assert with_remaster("Original Mix", False) == "Original Mix"


REMASTER = split_version("Remastered 2024")


@pytest.mark.parametrize("path, tags, expected", [
    # the path says it
    ("The Albums Remastered (2023)\\01. LSD.flac", {}, "confirmed"),
    ("x\\01-hallucinogen-lsd_(remastered_2024).flac", {}, "confirmed"),
    # the path does not, the tags do
    ("Twisted\\01. LSD.flac", {"album": "The Albums (Remastered)"}, "confirmed"),
    ("Twisted\\01. LSD.flac", {"title": "LSD (2024 Remaster)"}, "confirmed"),
    # a comment is too often about something else ("remastered by", a rip note)
    ("Twisted\\01. LSD.flac", {"comment": "remastered by Simon Posford"}, "unconfirmed"),
    # the date is the remaster's year or later
    ("Twisted\\01. LSD.flac", {"date": "2024-03-01"}, "confirmed"),
    ("Twisted\\01. LSD.flac", {"date": "2025"}, "confirmed"),
    # nothing says it: filed with the flag, not claimed
    ("Twisted\\01. LSD.flac", {"album": "Twisted", "date": "1995"}, "unconfirmed"),
    ("Twisted\\01. LSD.flac", {}, "unconfirmed"),
])
def test_a_remaster_is_confirmed_by_its_path_its_tags_or_its_date(path, tags, expected):
    assert check_version(REMASTER, path=path, tags=tags).status == expected


def test_a_remaster_with_no_year_asked_is_not_confirmed_by_a_date():
    assert check_version(split_version("Remastered"), path="x\\LSD.flac", tags={"date": "2024"}).status \
        == "unconfirmed"


def test_an_original_is_questioned_only_by_a_file_that_says_remaster():
    assert check_version(Version(), path="Twisted\\01. LSD.flac", tags={"album": "Twisted"}) is None
    flagged = check_version(Version(), path="Twisted\\01. LSD (2024 Remaster).flac")
    assert flagged.status == "unconfirmed" and "original" in flagged.reason
    assert check_version(Version(), path="x\\LSD.flac", tags={"title": "LSD (Remastered)"}).status \
        == "unconfirmed"
    # what is about more than this one track never questions it: a peer's folder, an album, a comment
    assert check_version(Version(), path="The Albums Remastered (2023)\\01. LSD.flac") is None
    assert check_version(Version(), path="x/LSD.flac", tags={"album": "Remastered Classics",
                                                             "comment": "remastered rip"}) is None


def test_the_record_it_was_fetched_as_is_evidence_too():
    """The Deezer copy is that record: its album saying remaster is what confirms it."""
    assert check_version(REMASTER, metadata=("The Albums Remastered", "LSD")).status == "confirmed"
    assert check_version(REMASTER, metadata=("Twisted", "LSD"), release_year="1995").status == "unconfirmed"


# Issue #187: an extended / club / 12" cut is the same arrangement at another length, the way a remaster is
# the same arrangement on another release.
@pytest.mark.parametrize("text, arrangement", [
    ("Extended Mix", ""),
    ("Extended", ""),
    ("Extended Version", ""),
    ("Club Mix", ""),
    ("12'' Version", ""),
    ('Original 12" Mix', ""),
    ("12 Inch Version", ""),
    ("12_ club mix", ""),
    ("Original Extended Mix", ""),
    ("Extended Edit", ""),
    # someone else's extended remix is still their remix
    ("Liquid Soul Extended Remix", "liquid soul remix"),
    ("JJ Stockholm Club Mix", "jj stockholm"),
    ("Shep Pettibone 12'' Mix", "shep pettibone mix"),
    ("Vocal Club Mix", "vocal"),
])
def test_an_extended_cut_is_read_apart_from_the_arrangement(text, arrangement):
    v = split_version(text)
    assert v.cut == "extended" and v.arrangement == arrangement


@pytest.mark.parametrize("text", [None, "Original Mix", "Radio Edit", "Vini Vici Remix", "Remastered 2024",
                                  "Dub Mix", "Live"])
def test_other_versions_have_no_cut(text):
    assert split_version(text).cut == ""


def test_an_extended_cut_is_never_the_original_and_never_the_radio_edit():
    """What the matcher calls the original (`is_original`) stays what it was before the cut existed: an
    Extended Mix candidate must not be auto-picked for a request that names no version."""
    assert not split_version("Extended Mix").is_original
    assert not agrees(Version(), split_version("Extended Mix"))
    assert not agrees(split_version("Extended Mix"), Version())
    assert not agrees(split_version("Radio Edit"), split_version("Extended Mix"))
    # a club mix is a version of its own, to match and to own (both are still read as an extended cut)
    assert split_version("Club Mix").cut == split_version("Extended Mix").cut == "extended"
    assert not agrees(split_version("Extended Mix"), split_version("Club Mix"))
    assert not same_owned_version(split_version("Extended Mix"), split_version("Club Mix"))


def test_a_remix_s_extended_cut_still_matches_the_remix_but_is_owned_apart():
    """Matching reads "X Extended Remix" as "X Remix", as it always has: a request for the remix may
    still be answered by a candidate named for its extended cut. The library keeps them apart: owning one
    does not hide the other (issue #187)."""
    remix, extended = split_version("Bad Company Remix"), split_version("Bad Company Extended Remix")
    assert agrees(remix, extended) and agrees(extended, remix)
    assert not same_owned_version(remix, extended) and not same_owned_version(extended, remix)
    assert same_owned_version(remix, split_version("Bad Company Remix"))
    assert same_owned_version(Version(), split_version("Original Mix"))
    assert not same_owned_version(split_version("Radio Edit"), split_version("Extended Mix"))


def test_context_never_makes_a_cut():
    """A peer's folder called "Extended Mixes" or an album called "Extended Versions" says nothing about
    this one track."""
    assert split_version(None, "Extended Versions").cut == ""


def test_with_remaster_keeps_an_extended_mix():
    assert with_remaster("Extended Mix", True) == "Extended Mix Remastered"


@pytest.mark.parametrize("path, tags, expected", [
    ("x\\01-02 - Feel So Close (extended mix).flac", {}, "extended"),
    ("x\\01. Titanium (feat. Sia) (Extended).flac", {}, "extended"),
    ("x\\0102 Music Sounds Better With You (12_ club mix).flac", {}, "extended"),
    ("x\\04. New Order - Blue Monday (Original 12'' Version).flac", {}, "extended"),
    ("x\\01 - Sun & Moon (club mix).flac", {}, "extended"),
    ("x\\01 - Feel So Close.flac", {"mix": "Extended Mix"}, "extended"),
    ("x\\01 - Feel So Close.flac", {"title": "Feel So Close (Extended Mix)"}, "extended"),
    # a folder or an album is about more than this track
    ("Extended Mixes\\01 - Feel So Close.flac", {"album": "Extended Versions"}, ""),
    ("x\\01 - Feel So Close (Radio Edit).flac", {}, ""),
    ("x\\01 - Feel So Close.flac", {}, ""),
])
def test_a_file_says_its_cut_by_its_own_name_or_its_title_and_mix_tags(path, tags, expected):
    assert file_cut(path, tags) == expected


@pytest.mark.parametrize("mix, expected", [
    (None, "Extended Mix"),
    ("Original Mix", "Extended Mix"),
    ("Radio Edit", "Extended Mix"),
    ("Remastered", "Extended Mix Remastered"),
    ("Bad Company Remix", "Bad Company Extended Remix"),
    ("Extended Mix", "Extended Mix"),
    ("Club Mix", "Club Mix"),
])
def test_an_extended_file_is_filed_under_a_mix_name_that_says_so(mix, expected):
    assert extended_mix_name(mix) == expected
