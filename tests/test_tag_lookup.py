"""The Deezer + Discogs fallback for tracks Beatport could not tag (issue #146). Every response is served
from tests/fixtures/tag_lookup through respx: nothing here reaches the network."""
import json
from pathlib import Path

import httpx
import pytest
import respx

from flackey.deezer import DeezerApi, parse_album_json
from flackey.models import CatalogTrack
from flackey.tag_lookup import (
    Discogs,
    FoundTags,
    TagLookup,
    Wanted,
    check_track,
    discogs_match,
    fill_missing,
    parse_duration,
    pick_genre,
    split_title,
    title_key,
    version_key,
)

FIX = Path(__file__).parent / "fixtures" / "tag_lookup"
SEARCH = "https://api.discogs.com/database/search"
H = [{"name": "Hallucinogen", "anv": "", "join": ""}]


def fixture(name: str) -> dict:
    return json.loads((FIX / f"{name}.json").read_text(encoding="utf-8"))


SEARCHES = {"Alpha Centauri": "discogs_search_alpha_centauri", "Gamma Goblins Pt. 2": "discogs_search_gamma_goblins",
            "Snarling": "discogs_search_snarling", "Into the Void": "discogs_search_into_the_void"}


def serve_discogs(router: respx.Router, status: int = 200) -> dict[str, list]:
    """Discogs from the fixtures; answers with which URLs were asked for, in order."""
    asked: dict[str, list] = {"search": [], "release": []}

    def search(request: httpx.Request) -> httpx.Response:
        track = request.url.params.get("track")
        asked["search"].append(dict(request.url.params))
        if status != 200:
            return httpx.Response(status, json={"message": "nope"})
        name = SEARCHES.get(track)
        return httpx.Response(200, json=fixture(name) if name else {"pagination": {}, "results": []})

    def release(request: httpx.Request, rid: str) -> httpx.Response:
        asked["release"].append(int(rid))
        path = FIX / f"discogs_release_{rid}.json"
        return httpx.Response(200, json=fixture(path.stem)) if path.exists() else httpx.Response(404)

    router.get(SEARCH).mock(side_effect=search)
    router.get(url__regex=r"https://api\.discogs\.com/releases/(?P<rid>\d+)$").mock(side_effect=release)
    return asked


class NoWait:
    """A clock that never sleeps for real but remembers every wait it was asked for."""

    def __init__(self):
        self.t = 0.0
        self.waits: list[float] = []

    def clock(self) -> float:
        return self.t

    async def sleep(self, s: float) -> None:
        self.waits.append(round(s, 3))
        self.t += s


def discogs(http, cache_dir=None, clock: NoWait | None = None) -> Discogs:
    clock = clock or NoWait()
    return Discogs(http, cache_dir, clock=clock.clock, sleep=clock.sleep)


# ---- titles, versions, lengths ------------------------------------------------------------------------

def test_pt_reads_as_part():
    assert title_key("Gamma Goblins Pt. 2") == title_key("Gamma Goblins Part 2") == "gamma goblins part 2"
    assert title_key("Gamma Goblins Pt 2") == title_key("Gamma Goblins Pt.2") == "gamma goblins part 2"
    assert title_key("Rock & Roll") == title_key("Rock and Roll")


def test_a_remix_in_the_title_is_the_version():
    assert split_title("Snarling (Remix)") == ("Snarling", ["Remix"])
    assert split_title("Alpha Centauri (Remastered 2024) (Original Mix)") == (
        "Alpha Centauri", ["Remastered 2024", "Original Mix"])
    assert version_key(["Remix"]) == "remix"


def test_a_remaster_and_an_original_mix_are_no_version():
    for v in (["Original Mix"], ["Remastered 2024"], ["2024 Remaster"], ["Digital Remaster"], []):
        assert version_key(v) == "", v
    assert version_key(["Vini Vici Remix"]) == "vini vici remix"


def test_wanted_reads_version_and_remaster():
    w = Wanted.of("Etnica", "Snarling (Remix)", None, 490)
    assert (w.title, w.version, w.remaster) == ("Snarling", "remix", False)
    w = Wanted.of("Hallucinogen", "Alpha Centauri", "Remastered 2024", 620)
    assert (w.title, w.version, w.remaster, w.remaster_year) == ("Alpha Centauri", "", True, "2024")
    w = Wanted.of("Hallucinogen", "Alpha Centauri", "Original Mix", 616, album="The Lone Deranger (Remastered)")
    assert (w.remaster, w.remaster_year) == (True, None)
    assert Wanted.of("Astral Projection, Yahel", "X", None, 1).first_artist == "Astral Projection"


def test_parse_duration():
    assert parse_duration("10:16") == 616
    assert parse_duration("1:02:03") == 3723
    assert parse_duration("") is None and parse_duration(None) is None and parse_duration("ten") is None


def test_check_track_is_strict_about_the_title():
    wanted = Wanted.of("Somebody", "Rain", None, 420)
    track = {"title": "Don't Stop The Rain", "duration": "7:00"}
    assert not check_track(wanted, track, [{"name": "Somebody"}]).passed
    assert check_track(wanted, {"title": "Rain", "duration": "7:00"}, [{"name": "Somebody"}]).passed


def test_check_track_checks_the_version():
    original = Wanted.of("Hallucinogen", "Alpha Centauri", "Original Mix", 616)
    assert not check_track(original, {"title": "Alpha Centauri (Edit)", "duration": "10:16"}, H).passed
    remix = Wanted.of("Hallucinogen", "Alpha Centauri", "Remix", 616)
    assert not check_track(remix, {"title": "Alpha Centauri (Bad Company Remix)", "duration": "10:16"}, H).passed
    assert check_track(remix, {"title": "Alpha Centauri (Remix)", "duration": "10:16"}, H).passed


def test_check_track_gates_the_length():
    wanted = Wanted.of("Hallucinogen", "Alpha Centauri", None, 616)
    assert check_track(wanted, {"title": "Alpha Centauri", "duration": "10:31"}, H).passed       # 15 s
    assert not check_track(wanted, {"title": "Alpha Centauri", "duration": "10:32"}, H).passed   # 16 s
    assert not check_track(wanted, {"title": "Alpha Centauri", "duration": ""}, H).passed        # unknown
    assert not check_track(Wanted.of("Hallucinogen", "Alpha Centauri", None, None),
                           {"title": "Alpha Centauri", "duration": "10:16"}, H).passed


def test_check_track_checks_the_artist():
    wanted = Wanted.of("Hallucinogen", "Alpha Centauri", None, 616)
    assert not check_track(wanted, {"title": "Alpha Centauri", "duration": "10:16"},
                           [{"name": "Someone Else"}]).passed
    # Discogs' "(2)" disambiguation and a track credit on a various-artists release
    assert check_track(wanted, {"title": "Alpha Centauri", "duration": "10:16",
                                "artists": [{"name": "Hallucinogen (2)"}]}, [{"name": "Various"}]).passed


# ---- genre ------------------------------------------------------------------------------------------

def test_genre_takes_the_most_specific_style_not_the_first():
    assert pick_genre(["Trance", "Goa Trance", "Psy-Trance"]) == ("Psy-Trance", "Goa Trance")
    assert pick_genre(["House", "Deep House"]) == ("Deep House", "Deep House")
    assert pick_genre(["Techno", "Minimal"]) == ("Minimal / Deep Tech", "Minimal")


def test_genre_falls_back_to_order_and_never_guesses():
    assert pick_genre(["Trance"]) == ("Trance (Main Floor)", "Trance")
    assert pick_genre(["Ambient", "Goa Trance"]) == ("Ambient / Experimental", "Ambient")   # a tie: order
    assert pick_genre(["Synth-pop"]) == (None, "Synth-pop")
    assert pick_genre([]) == (None, None)


# ---- Discogs ----------------------------------------------------------------------------------------

@respx.mock
async def test_discogs_takes_the_earliest_release_for_an_original():
    asked = serve_discogs(respx.mock)
    async with httpx.AsyncClient() as http:
        m = await discogs_match(discogs(http), Wanted.of("Hallucinogen", "Alpha Centauri", "Original Mix", 616))
    assert (m.release_id, m.label, m.catalog_number, m.year) == (1001, "Twisted Records", "TWSCD4", "1997")
    assert (m.genre, m.sub_genre) == ("Psy-Trance", "Goa Trance")
    assert asked["release"] == [1001]          # the earliest passed first: no other release fetched
    assert asked["search"][0]["artist"] == "Hallucinogen" and asked["search"][0]["track"] == "Alpha Centauri"


@respx.mock
async def test_discogs_takes_the_remaster_year_for_a_remaster():
    serve_discogs(respx.mock)
    async with httpx.AsyncClient() as http:
        m = await discogs_match(discogs(http), Wanted.of("Hallucinogen", "Alpha Centauri (2024 Remaster)", None, 620))
    assert (m.release_id, m.catalog_number, m.year) == (1002, "TWSCD4R", "2024")


@respx.mock
async def test_discogs_remaster_from_the_album_title():
    serve_discogs(respx.mock)
    async with httpx.AsyncClient() as http:
        m = await discogs_match(discogs(http), Wanted.of("Hallucinogen", "Alpha Centauri", None, 620,
                                                         album="The Albums (Remastered)"))
    assert m.year == "2024"


@respx.mock
async def test_discogs_remaster_with_no_matching_remaster_release_takes_no_year():
    serve_discogs(respx.mock)
    notes: list[str] = []
    async with httpx.AsyncClient() as http:
        m = await discogs_match(discogs(http), Wanted.of("Hallucinogen", "Alpha Centauri", "Remastered 2011", 616),
                                notes=notes)
    assert (m.label, m.catalog_number, m.year) == (None, None, None)
    assert m.genre == "Psy-Trance"           # the style is the music's, whichever release says it
    assert any("remaster" in n for n in notes)


@respx.mock
async def test_discogs_matches_pt_against_part():
    serve_discogs(respx.mock)
    async with httpx.AsyncClient() as http:
        m = await discogs_match(discogs(http), Wanted.of("Infected Mushroom", "Gamma Goblins Pt. 2", None, 485))
    assert (m.label, m.catalog_number, m.year, m.genre) == ("BNE", "BNECD 2", "2000", "Psy-Trance")


@respx.mock
async def test_discogs_matches_a_remix_written_in_the_title():
    asked = serve_discogs(respx.mock)
    async with httpx.AsyncClient() as http:
        d = discogs(http)
        remix = await discogs_match(d, Wanted.of("Etnica", "Snarling (Remix)", None, 492))
        original = await discogs_match(d, Wanted.of("Etnica", "Snarling", "Original Mix", 492))
    assert remix is not None and remix.catalog_number == "DAT 012"
    assert original is None                    # 7:40 original is 32 s off; the 8:10 remix is not the original
    assert all(s["track"] == "Snarling" for s in asked["search"])   # searched without the version text


@respx.mock
async def test_discogs_finds_nothing_for_an_unknown_track():
    serve_discogs(respx.mock)
    notes: list[str] = []
    async with httpx.AsyncClient() as http:
        assert await discogs_match(discogs(http), Wanted.of("Nobody", "Nothing", None, 300), notes=notes) is None
    assert notes == ["discogs: no release found"]


async def test_discogs_waits_between_requests():
    clock = NoWait()
    with respx.mock as router:
        serve_discogs(router)
        async with httpx.AsyncClient() as http:
            d = discogs(http, clock=clock)
            await d.get("/releases/1001")
            await d.get("/releases/1002")
            await d.get("/releases/1003")
    assert clock.waits == [2.4, 2.4]           # 25 a minute


async def test_discogs_answers_come_from_the_cache_the_second_time(tmp_path):
    with respx.mock as router:
        asked = serve_discogs(router)
        async with httpx.AsyncClient() as http:
            wanted = Wanted.of("Hallucinogen", "Alpha Centauri", None, 616)
            first = await discogs_match(discogs(http, tmp_path), wanted)
            second = await discogs_match(discogs(http, tmp_path), wanted)
    assert first == second
    assert len(asked["search"]) == 1 and asked["release"] == [1001]


async def test_discogs_cache_expires(tmp_path):
    now = [1000.0]
    with respx.mock as router:
        asked = serve_discogs(router)
        async with httpx.AsyncClient() as http:
            d = Discogs(http, tmp_path, clock=NoWait().clock, sleep=NoWait().sleep, now=lambda: now[0])
            await d.get("/releases/1001")
            now[0] += Discogs.CACHE_TTL_S + 1
            await d.get("/releases/1001")
    assert asked["release"] == [1001, 1001]


async def test_a_corrupt_cache_entry_is_fetched_again(tmp_path):
    with respx.mock as router:
        asked = serve_discogs(router)
        async with httpx.AsyncClient() as http:
            d = discogs(http, tmp_path)
            await d.get("/releases/1001")
            [entry] = tmp_path.glob("*.json")
            entry.write_text("[1, 2]", encoding="utf-8")
            assert (await d.get("/releases/1001"))["id"] == 1001
    assert asked["release"] == [1001, 1001]
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize("status", [401, 429, 500])
async def test_a_refusing_discogs_is_a_note_not_an_error(status):
    with respx.mock as router:
        serve_discogs(router, status=status)
        async with httpx.AsyncClient() as http:
            found = await TagLookup(None, discogs(http)).lookup(_stand_in("Hallucinogen", "Alpha Centauri", 616))
    assert not found.any and found.genre is None
    assert any(str(status) in n for n in found.notes)


# ---- Deezer -----------------------------------------------------------------------------------------

def test_deezer_album_cover_is_1400px():
    album = parse_album_json(fixture("deezer_album_302127"))
    assert album.label == "Sacred Technology" and album.release_date == "2022-06-03"
    assert album.cover_url == ("https://e-cdns-images.dzcdn.net/images/cover/"
                               "2e018122cb56986277102d2041a592c8/1400x1400-000000-80-0-0.jpg")
    assert parse_album_json({"id": 1, "release_date": "0000-00-00", "label": " "}).release_date is None
    assert parse_album_json({"id": 1}).label is None and parse_album_json({"id": 1}).cover_url is None


def _stand_in(artist: str, title: str, seconds: int | None, mix: str = "Original Mix", **kw) -> CatalogTrack:
    return CatalogTrack(id=-1, artist=artist, title=title, mix_name=mix, label="Unknown", genre="Unknown",
                        duration_ms=seconds * 1000 if seconds else None, **kw)


def serve_deezer(router: respx.Router, status: int = 200) -> None:
    router.get("https://api.deezer.com/track/1754956977").mock(
        return_value=httpx.Response(status, json=fixture("deezer_track_1754956977")))
    router.get("https://api.deezer.com/album/302127").mock(
        return_value=httpx.Response(200, json=fixture("deezer_album_302127")))


async def test_deezer_first_and_discogs_for_the_catalog_number_and_genre():
    with respx.mock as router:
        serve_deezer(router)
        asked = serve_discogs(router)
        async with httpx.AsyncClient() as http:
            lookup = TagLookup(DeezerApi(http), discogs(http))
            found = await lookup.lookup(_stand_in("Astral Projection", "Into the Void", 442), 1754956977)
    # Deezer's album: label, date, ISRC, duration, cover
    assert (found.label, found.release_date, found.isrc, found.duration_ms) == (
        "Sacred Technology", "2022-06-03", "UKU932231081", 442000)
    assert found.artwork_url.endswith("/1400x1400-000000-80-0-0.jpg")
    # Discogs: the number from the release on Deezer's label (not the 1996 original), and the genre
    assert (found.catalog_number, found.genre, found.sub_genre) == ("STCD012", "Psy-Trance", "Psy-Trance")
    assert found.sources == ["deezer", "discogs"]
    assert asked["release"] == [3002]


async def test_a_failed_deezer_leaves_discogs():
    with respx.mock as router:
        serve_deezer(router, status=500)
        serve_discogs(router)
        async with httpx.AsyncClient() as http:
            found = await TagLookup(DeezerApi(http), discogs(http)).lookup(
                _stand_in("Astral Projection", "Into the Void", 442), 1754956977)
    assert found.sources == ["discogs"]
    assert (found.label, found.catalog_number, found.release_date) == ("Trust In Trance Records", "TIT 1001", "1996")
    assert found.artwork_url is None           # never a Discogs image
    assert any(n.startswith("deezer:") for n in found.notes)


async def test_discogs_only_never_writes_an_image():
    with respx.mock as router:
        serve_discogs(router)
        async with httpx.AsyncClient() as http:
            found = await TagLookup(DeezerApi(http), discogs(http)).lookup(
                _stand_in("Hallucinogen", "Alpha Centauri", 616))
    assert found.sources == ["discogs"] and found.artwork_url is None
    assert (found.label, found.catalog_number, found.release_date) == ("Twisted Records", "TWSCD4", "1997")


# ---- filling ---------------------------------------------------------------------------------------

def test_fill_missing_only_fills_empty_fields():
    found = FoundTags(label="Discogs Label", genre="Psy-Trance", catalog_number="CAT 1", release_date="1997",
                      isrc="XX", sources=["discogs"])
    beatport_ish = CatalogTrack(id=-5, artist="A", title="T", mix_name="Original Mix", label="Kept Label",
                                genre="Unknown", isrc="KEPT")
    out = fill_missing(beatport_ish, found)
    assert (out.label, out.isrc) == ("Kept Label", "KEPT")
    assert (out.genre, out.catalog_number, out.release_date, out.tag_source) == (
        "Psy-Trance", "CAT 1", "1997", "discogs")
    assert fill_missing(out, FoundTags(genre="House", sources=["discogs"])) == out     # filled once, kept


def test_fill_missing_with_nothing_found_changes_nothing():
    stand_in = _stand_in("A", "T", 300)
    assert fill_missing(stand_in, FoundTags()) is stand_in
