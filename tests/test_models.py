import pytest

from flackey.models import CatalogTrack, Query, norm


def test_query_search_text_prefers_structured_fields():
    q = Query(raw="whatever", artist="Astral Projection", title="Into the Void")
    assert q.search_text() == "Astral Projection Into the Void"
    q.version = "Vini Vici Remix"
    assert q.search_text() == "Astral Projection Into the Void Vini Vici Remix"
    assert Query(raw="free text").search_text() == "free text"


def test_catalog_track_derived_fields():
    t = CatalogTrack(id=1, artist="a", title="t", mix_name="Original Mix", label="l",
                     genre="g", release_date="2022-06-03", duration_ms=442816)
    assert t.duration_s == 443
    assert t.year == "2022"


def _ct(mix: str) -> CatalogTrack:
    return CatalogTrack(id=1, artist="Astral Projection", title="Into the Void", mix_name=mix,
                        label="Trust in Trance", genre="Psy-Trance")


@pytest.mark.parametrize("mix,expected", [
    ("Aboriginal Mix", "Into the Void (Aboriginal Mix)"),
    ("Original Mix", "Into the Void"),
    ("Original", "Into the Void"),
    ("Original Version", "Into the Void"),
])
def test_display_title_word_boundary_original(mix: str, expected: str):
    assert _ct(mix).display_title == expected


def test_query_search_text_drops_original_mix():
    # Deezer titles never carry "Original Mix"; the source bot answers "No results" when it is appended
    q = Query(raw="", artist="Goasia", title="Love & Peace", version="Original Mix")
    assert q.search_text() == "Goasia Love & Peace"


def test_norm_joins_dotted_acronyms_and_strips_punctuation():
    assert norm("L.S.D.") == "lsd"
    assert norm("Love & Peace") == "love peace"
    assert norm("S.U.N. Project") == "sun project"
