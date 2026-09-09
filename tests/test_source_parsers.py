from flackey.source.deezer_bot import ButtonInfo, parse_result_menu

MENU = [
    [ButtonInfo("1. Astral Projection - Into the Void", "dz_track:1754956977:send")],
    [ButtonInfo("2. Matan - Astral Projection Into the Void", "dz_track:99:send")],
    [ButtonInfo("3. Some SC Result", "sc_track:5:send")],
    [ButtonInfo("Tracks ✅", "page:1"), ButtonInfo("Albums ☑️", "album_page:1"), ButtonInfo("Artists ☑️", "artist_page:1")],
    [ButtonInfo("Deezer ✅", "page:1"), ButtonInfo("SoundCloud ☑️", "sc_page:1"), ButtonInfo("VK ☑️", "vk_page:1")],
    [ButtonInfo("Close", "delete")],
]


def test_parse_result_menu_extracts_deezer_candidates_only():
    m = parse_result_menu(MENU)
    assert m.deezer_enabled and m.deezer_toggle.text == "Deezer ✅"
    assert [c.deezer_id for c in m.candidates] == [1754956977, 99]
    c = m.candidates[0]
    assert (c.source, c.source_ref, c.rank) == ("deezer_bot", "dz_track:1754956977:send", 1)
    assert (c.artist, c.title) == ("Astral Projection", "Into the Void")
    assert m.candidates[1].artist == "Matan" and m.candidates[1].title == "Astral Projection Into the Void"


def test_parse_result_menu_deezer_disabled():
    menu = [[ButtonInfo("1. X - Y", "sc_track:1:send")],
            [ButtonInfo("Deezer ☑️", "page:1"), ButtonInfo("SoundCloud ✅", "sc_page:1")]]
    m = parse_result_menu(menu)
    assert not m.deezer_enabled and m.candidates == [] and m.deezer_toggle.data == "page:1"


def test_parse_result_menu_records_toggle_position():
    # "Tracks ✅" in row 3 shares callback data "page:1" with "Deezer ✅" in row 4; clicks must go by position
    assert (parse_result_menu(MENU).deezer_toggle.row, parse_result_menu(MENU).deezer_toggle.col) == (4, 0)
    menu = [[ButtonInfo("1. X - Y", "sc_track:1:send")],
            [ButtonInfo("SoundCloud ✅", "sc_page:1"), ButtonInfo("Deezer ☑️", "page:1")]]
    t = parse_result_menu(menu).deezer_toggle
    assert (t.row, t.col) == (1, 1)


def test_parse_result_menu_label_without_dash():
    m = parse_result_menu([[ButtonInfo("1. Untitled", "dz_track:5:send")]])
    assert m.candidates[0].artist == "" and m.candidates[0].title == "Untitled"
