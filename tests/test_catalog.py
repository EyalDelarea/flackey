import pytest

from flackey.catalog import with_original_date
from flackey.models import CatalogTrack

# ---- the original release's date --------------------------------------------------------------------
# A catalogue lists a recording on several releases, and the one a track is tagged from can be a later
# compilation: its date tagged "One More Time" 2005. Other sources (the Deezer record that was downloaded,
# Discogs' earliest year) vouch for an earlier one.


def _dp(id, *, mix="Original Mix", release="Musique Vol. 1 1993 - 2005", date="2005-03-14"):
    return CatalogTrack(id=id, isrc="GBDUW0000059", artist="Daft Punk", title="One More Time", mix_name=mix,
                        label="L", genre="G", release_name=release, release_date=date, duration_ms=320_000)


def test_the_original_date_is_the_earliest_date_another_source_vouches_for():
    chosen = _dp(1)
    dated = with_original_date(chosen, also=["2001-03-12", "2000-11-13"])
    assert (dated.release_date, dated.original_date, dated.tag_date, dated.year) == (
        "2005-03-14", "2000-11-13", "2000-11-13", "2000")
    assert chosen.original_date is None                       # a copy; the record passed in is left alone


@pytest.mark.parametrize("also", [
    [],                         # nothing else known
    ["2006-06-01"],             # later dates say nothing
    ["2005-01-03"],             # the same year: nothing to change
    ["not a date", None],
], ids=["alone", "later", "same-year", "garbage"])
def test_the_releases_own_date_stands_without_an_earlier_one(also):
    chosen = _dp(1)
    assert with_original_date(chosen, also=also) is chosen


def test_a_remaster_keeps_its_own_year():
    """Asked for, or named in the record: the remaster's year is what tells it from the original."""
    remaster = _dp(1, mix="Remastered 2021", date="2021-02-22")
    assert with_original_date(remaster, also=["2001-03-12"]) is remaster
    plain = _dp(1, date="2021-02-22")
    assert with_original_date(plain, also=["2001-03-12"], asked="Remastered 2021") is plain
    assert with_original_date(plain, also=["2001-03-12"]).original_date == "2001-03-12"


def test_another_sources_date_counts_and_an_earlier_one_found_before_is_kept():
    chosen = _dp(1)
    assert with_original_date(chosen, also=["2001-03-07"]).original_date == "2001-03-07"
    assert with_original_date(chosen, also=["2001"]).original_date == "2001"           # Discogs gives a year
    known = with_original_date(chosen, also=["2001-03-07"])
    assert with_original_date(known, also=["2003-01-01"]) is known
