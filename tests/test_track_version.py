"""The one version model (issue #172): an arrangement, and whether it is a remaster."""
import pytest

from flackey.models import CatalogTrack
from flackey.version import (
    Version,
    agrees,
    check_version,
    record_version,
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
    ("Extended Mix", Version("extended mix")),
    ("Extended Mix Remastered 2024", Version("extended mix", True, "2024")),
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
    assert record_version(rec) == Version("", True, None)
    assert not record_version(CatalogTrack(**{**rec.__dict__, "release_name": "Twisted"})).remaster


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
    ("Twisted\\01. LSD.flac", {"comment": "remastered by Simon Posford"}, "confirmed"),
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
    flagged = check_version(Version(), path="The Albums Remastered (2023)\\01. LSD.flac")
    assert flagged.status == "unconfirmed" and "original" in flagged.reason
    assert check_version(Version(), path="x\\LSD.flac", tags={"album": "The Albums Remastered"}).status \
        == "unconfirmed"


def test_the_record_it_was_fetched_as_is_evidence_too():
    """The Deezer copy is that record: its album saying remaster is what confirms it."""
    assert check_version(REMASTER, metadata=("The Albums Remastered", "LSD")).status == "confirmed"
    assert check_version(REMASTER, metadata=("Twisted", "LSD"), release_year="1995").status == "unconfirmed"
