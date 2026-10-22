import time

import pytest

from flackey.identify import (
    classify,
    parse_text,
    parse_version,
    parse_youtube_title,
)
from flackey.models import RequestKind


@pytest.mark.parametrize("text,kind", [
    ("astral projection into the void", RequestKind.TEXT),
    ("https://music.youtube.com/watch?v=abc123&si=xyz", RequestKind.YT_TRACK),
    ("https://www.youtube.com/watch?v=abc123", RequestKind.YT_TRACK),
    ("https://youtu.be/abc123", RequestKind.YT_TRACK),
    ("https://music.youtube.com/playlist?list=PL123", RequestKind.YT_PLAYLIST),
    ("https://www.youtube.com/watch?v=abc&list=PL123", RequestKind.YT_PLAYLIST),
    ("check this https://youtu.be/abc123 great", RequestKind.YT_TRACK),
    ("https://open.spotify.com/track/11dFghVXANMlKmJXsNCbNl?si=abc", RequestKind.SPOTIFY_TRACK),
    ("https://open.spotify.com/intl-de/track/11dFghVXANMlKmJXsNCbNl?si=abc", RequestKind.SPOTIFY_TRACK),
    ("https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M?si=abc", RequestKind.SPOTIFY_PLAYLIST),
    ("https://open.spotify.com/intl-de/playlist/37i9dQZF1DXcBWIGoYBM5M?si=abc", RequestKind.SPOTIFY_PLAYLIST),
])
def test_classify(text, kind):
    k, url = classify(text)
    assert k == kind
    assert (url is None) == (kind == RequestKind.TEXT)


def test_classify_strips_tracking_params():
    _, url = classify("https://music.youtube.com/watch?v=abc123&si=xyz")
    assert url == "https://music.youtube.com/watch?v=abc123"


def test_classify_canonical_playlist_url_and_autoplay_mixes():
    assert classify("https://www.youtube.com/watch?v=abc&list=PL123&index=4")[1] == \
        "https://www.youtube.com/playlist?list=PL123"
    assert classify("https://music.youtube.com/playlist?list=PL123")[1] == "https://www.youtube.com/playlist?list=PL123"
    k, url = classify("https://music.youtube.com/watch?v=abc123&list=RDAMVMabc123")
    assert k == RequestKind.YT_TRACK and url == "https://music.youtube.com/watch?v=abc123"


def test_classify_canonical_spotify_track_url():
    _, url = classify("check https://open.spotify.com/intl-de/track/11dFghVXANMlKmJXsNCbNl?si=abc")
    assert url == "https://open.spotify.com/track/11dFghVXANMlKmJXsNCbNl"


def test_classify_canonical_spotify_playlist_url():
    _, url = classify("https://open.spotify.com/intl-de/playlist/37i9dQZF1DXcBWIGoYBM5M?si=abc")
    assert url == "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M"


@pytest.mark.parametrize("title,expected", [
    ("Into the Void", ("Into the Void", None)),
    ("Into the Void (Original Mix)", ("Into the Void", "Original Mix")),
    ("Into the Void (Vini Vici Remix)", ("Into the Void", "Vini Vici Remix")),
    ("Into the Void - Extended Mix", ("Into the Void", "Extended Mix")),
    ("Into the Void [Radio Edit]", ("Into the Void", "Radio Edit")),
    ("Into the Void (Live at Ozora)", ("Into the Void", "Live at Ozora")),
    ("Into the Void (feat. Someone)", ("Into the Void (feat. Someone)", None)),
    ("Into the Void (Louis Futon Flip)", ("Into the Void", "Louis Futon Flip")),
    ("Into the Void (Someone Mashup)", ("Into the Void", "Someone Mashup")),
    ("Into the Void (Someone Mash-Up)", ("Into the Void", "Someone Mash-Up")),
    ("Into the Void (Someone Refix)", ("Into the Void", "Someone Refix")),
    ("Into the Void (Flipside)", ("Into the Void (Flipside)", None)),  # whole words only
    ("Into the Void (Refixation)", ("Into the Void (Refixation)", None)),
    ("Into the Void (Someone Mash Up)", ("Into the Void", "Someone Mash Up")),
    ("Into the Void (Someone Refix 2023)", ("Into the Void", "Someone Refix 2023")),
    ("Into the Void - Someone Flip", ("Into the Void", "Someone Flip")),
    ("Into the Void (Remixed by XYZ Music)", ("Into the Void", "Remixed by XYZ Music")),
    # a feature credit is not a version, whatever the name in it
    ("Like A Pimp (feat. Lil Flip)", ("Like A Pimp (feat. Lil Flip)", None)),
    ("Into the Void (feat. Flip)", ("Into the Void (feat. Flip)", None)),
    ("Into the Void (ft. DJ Flip)", ("Into the Void (ft. DJ Flip)", None)),
    ("Into the Void (feat. Club Dogo)", ("Into the Void (feat. Club Dogo)", None)),
    # flip is a version only as the last word, after whoever made it
    ("Into the Void (Flip Flop)", ("Into the Void (Flip Flop)", None)),
    ("Into the Void (Flip It)", ("Into the Void (Flip It)", None)),
    ("Into the Void (Flip The Switch)", ("Into the Void (Flip The Switch)", None)),
    ("Into the Void (Flip Side)", ("Into the Void (Flip Side)", None)),
    ("Into the Void - Flip Side", ("Into the Void - Flip Side", None)),
    ("Into the Void (Flip The Script)", ("Into the Void (Flip The Script)", None)),
    ("Into the Void (The Flip)", ("Into the Void (The Flip)", None)),
    ("Into the Void (Lil Flip)", ("Into the Void (Lil Flip)", None)),
    ("Into the Void (Mash Up The Dance)", ("Into the Void (Mash Up The Dance)", None)),
])
def test_parse_version(title, expected):
    assert parse_version(title) == expected


def test_parse_text_with_dash():
    q = parse_text("Astral Projection - Into the Void (Vini Vici Remix)")
    assert (q.artist, q.title, q.version) == ("Astral Projection", "Into the Void", "Vini Vici Remix")


def test_parse_text_bare():
    q = parse_text("astral projection into the void")
    assert q.artist is None and q.title is None and q.raw == "astral projection into the void"


def test_parse_youtube_title_removes_noise():
    q = parse_youtube_title("Astral Projection - Into The Void (Official Video) [HD]", "Astral Projection", 442)
    assert (q.artist, q.title, q.version, q.duration_s) == ("Astral Projection", "Into The Void", None, 442)


def test_parse_youtube_title_drops_label_and_channel_tags_but_keeps_features():
    assert parse_youtube_title("Astral Projection - Into The Void [Iboga Records]").title == "Into The Void"
    assert parse_youtube_title("Astral Projection - Into The Void (Iboga Records)").title == "Into The Void"
    assert parse_youtube_title("Astral Projection - Into The Void (Sacred Technology)", "Sacred Technology").title == \
        "Into The Void"
    q = parse_youtube_title("Astral Projection - Into The Void (feat. Someone)")
    assert q.title == "Into The Void (feat. Someone)"
    q = parse_youtube_title("Astral Projection - Into The Void (Vini Vici Remix) [Iboga Records]")
    assert (q.title, q.version) == ("Into The Void", "Vini Vici Remix")


def test_parse_youtube_title_noise_words_need_word_boundaries():
    q = parse_youtube_title("Rusko - Lyrical Assassin", "Rusko", 300)
    assert (q.artist, q.title) == ("Rusko", "Lyrical Assassin")
    q = parse_youtube_title("Shpongle - Divine Moments of Truth (HD)", "Shpongle", 300)
    assert q.title == "Divine Moments of Truth"


def test_parse_youtube_title_uses_topic_uploader_when_no_dash():
    q = parse_youtube_title("Into the Void", "Astral Projection - Topic", 442)
    assert (q.artist, q.title) == ("Astral Projection", "Into the Void")


def test_parse_youtube_title_keeps_remix():
    q = parse_youtube_title("Astral Projection - Into The Void (Vini Vici Remix) | Official Audio")
    assert q.version == "Vini Vici Remix" and q.title == "Into The Void"


def test_split_ignores_a_dash_inside_brackets():
    q = parse_youtube_title("Granada (Remix - 98)", uploader="Granada - Topic")
    assert (q.artist, q.title, q.version) == ("Granada", "Granada", "Remix - 98")
    q = parse_text("Artist - Title (Some - Thing)")
    assert (q.artist, q.title) == ("Artist", "Title (Some - Thing)")
    q = parse_text("Artist [Live - 1997] - Title")
    assert (q.artist, q.title) == ("Artist [Live - 1997]", "Title")
    q = parse_text("Artist [a (b) - c] - Title")  # nested: depth, not an in-bracket flag
    assert (q.artist, q.title) == ("Artist [a (b) - c]", "Title")


@pytest.mark.parametrize("title,expected", [
    ("Artist - Track (FREE DL) [Label Records] [NM022]", ("Artist", "Track", None)),
    ("TB Premiere: Artist - Track", ("Artist", "Track", None)),
    ("Premiere: Artist - Track", ("Artist", "Track", None)),
    ("Some Blog Premiere | Artist - Track", ("Artist", "Track", None)),
    ("Some Blog Premiere - Artist - Track (Someone Remix)", ("Artist", "Track", "Someone Remix")),
    ("[XYZ Premiere] Artist - Track", ("Artist", "Track", None)),
    ("*PREMIERE* Artist - Track [FREE DL]", ("Artist", "Track", None)),
    ("Artist - Track *FREE DOWNLOAD*", ("Artist", "Track", None)),
    ("Artist - Track (free d/l)", ("Artist", "Track", None)),
    ("SGR053 - Artist - Track", ("Artist", "Track", None)),
    ("SGR053 - Artist - Track (Someone Remix) [FREE DL]", ("Artist", "Track", "Someone Remix")),
    ("[SGR053] Artist - Track", ("Artist", "Track", None)),
    ("Artist - Track (SGR053) (Original Mix)", ("Artist", "Track", "Original Mix")),
    ("Artist - Track [AUM-042]", ("Artist", "Track", None)),
    ("Artist - Track [Label Records] (Someone Remix)", ("Artist", "Track", "Someone Remix")),
    ("Artist - Track [Anjunadeep Music] (Extended Mix)", ("Artist", "Track", "Extended Mix")),
    ("Artist - Track (Black Hole Recordings) (feat. Someone)", ("Artist", "Track (feat. Someone)", None)),
    ("Artist - Track.wav", ("Artist", "Track", None)),
    ("Artist - Track.WAV (Someone Remix)", ("Artist", "Track", "Someone Remix")),
    ("Artist - Track.aiff", ("Artist", "Track", None)),
    ("Artist - Track /unreleased", ("Artist", "Track", None)),
    ("Artist - Track [unreleased]", ("Artist", "Track", None)),
    ("Artist - Track (Unreleased) (Someone Edit)", ("Artist", "Track", "Someone Edit")),
    ("Artist - Track (Louis Futon Flip)", ("Artist", "Track", "Louis Futon Flip")),
])
def test_parse_youtube_title_strips_upload_junk(title, expected):
    q = parse_youtube_title(title, "x")
    assert (q.artist, q.title, q.version) == expected


@pytest.mark.parametrize("title,expected", [
    ("Rusko - Lyrical Assassin", ("Rusko", "Lyrical Assassin", None)),
    ("Artist - Track (feat. Someone)", ("Artist", "Track (feat. Someone)", None)),
    ("Artist ft. Someone - Track [Iboga Records]", ("Artist ft. Someone", "Track", None)),
    ("Artist - Track (Live at Ozora)", ("Artist", "Track", "Live at Ozora")),
    ("Artist - Track (2019 Remaster)", ("Artist", "Track", "2019 Remaster")),
    ("Artist - Track (VIP)", ("Artist", "Track", "VIP")),
    ("Artist - Track (VIP 2019)", ("Artist", "Track", "VIP 2019")),  # catalogue-shaped, but a version
    ("Artist - Track (1998)", ("Artist", "Track (1998)", None)),
    ("Artist - Track (PART 12)", ("Artist", "Track (PART 12)", None)),
    ("Artist - Track (Original Mix)", ("Artist", "Track", "Original Mix")),
    ("Artist - Records of the Past", ("Artist", "Records of the Past", None)),
    ("Artist - Track (Live at Abbey Road Music)", ("Artist", "Track", "Live at Abbey Road Music")),
    ("K-391 - Summertime", ("K-391", "Summertime", None)),
    ("MK2 - Track - Extended Mix", ("MK2", "Track", "Extended Mix")),
    ("UB40 - Red Red Wine - Live", ("UB40", "Red Red Wine", "Live")),  # a band, not a catalogue number
    ("*NSYNC - Bye Bye Bye", ("*NSYNC", "Bye Bye Bye", None)),
    ("Artist - Unreleased Dreams", ("Artist", "Unreleased Dreams", None)),
    ("Artist - World Premiere", ("Artist", "World", None)),  # as before: "premiere" is noise anywhere
])
def test_parse_youtube_title_cleanup_leaves_real_names_alone(title, expected):
    q = parse_youtube_title(title, "x")
    assert (q.artist, q.title, q.version) == expected


@pytest.mark.parametrize("title,expected", [
    ("*", ("x", "*", None)),
    ("[FREE DL]", ("x", "[FREE DL]", None)),
    ("Premiere:", ("x", "Premiere:", None)),
    ("Official Video", ("x", "Official Video", None)),
    ("Artist - (Someone Remix)", ("Artist", "(Someone Remix)", None)),
])
def test_cleanup_never_leaves_an_empty_title(title, expected):
    q = parse_youtube_title(title, "x")
    assert (q.artist, q.title, q.version) == expected


@pytest.mark.parametrize("title,expected", [
    # a leading catalogue-shaped artist before a dash-version is the artist ("A - T - Extended Mix")
    ("UFO361 - Track - Remix", ("UFO361", "Track", "Remix")),
    ("BLINK-182 - All The Small Things - Live", ("BLINK-182", "All The Small Things", "Live")),
    ("DJ 3000 - Track - Original Mix", ("DJ 3000", "Track", "Original Mix")),
    ("DJ3000 - Track - Extended Mix", ("DJ3000", "Track", "Extended Mix")),
    ("ATB 2000 - Track - Remix", ("ATB 2000", "Track", "Remix")),
    ("ABC123 - Track - Extended Mix", ("ABC123", "Track", "Extended Mix")),
    ("TR 909 - Track - Extended Mix", ("TR 909", "Track", "Extended Mix")),
    ("ABBA1974 - Waterloo - Live", ("ABBA1974", "Waterloo", "Live")),
    ("SGR053 - Artist - Track - Extended Mix", ("Artist", "Track", "Extended Mix")),
    ("David Banner - Like A Pimp (feat. Lil Flip)", ("David Banner", "Like A Pimp (feat. Lil Flip)", None)),
    # versions glued to digits or written as rmx are not catalogue numbers
    ("Artist - Track (VIP2019)", ("Artist", "Track (VIP2019)", None)),
    ("Artist - Track (RMX 2023)", ("Artist", "Track (RMX 2023)", None)),
    ("Artist - Track (RMX2019)", ("Artist", "Track (RMX2019)", None)),
    ("Artist - Track (Remixed by XYZ Music)", ("Artist", "Track", "Remixed by XYZ Music")),
    # an artist credited as Premiere
    ("Artist & Premiere - Track", ("Artist & Premiere", "Track", None)),
    ("Artist ft. Premiere - Track", ("Artist ft. Premiere", "Track", None)),
    ("Artist x Premiere - Track", ("Artist x Premiere", "Track", None)),
    # "Music" in parentheses is a word, not a label
    ("Joey Negro - Make A Move On Me (Feel The Music)", ("Joey Negro", "Make A Move On Me (Feel The Music)", None)),
    ("Todd Terry - Weekend (House Music)", ("Todd Terry", "Weekend (House Music)", None)),
    ("Artist - Track (Theme Music)", ("Artist", "Track (Theme Music)", None)),
    ("Artist - Track (Body Music) (Extended Mix)", ("Artist", "Track (Body Music)", "Extended Mix")),
    ("Artist - Track (Field Recordings)", ("Artist", "Track (Field Recordings)", None)),
    ("Artist - Track [Anjunadeep Music]", ("Artist", "Track", None)),
    # numbered parts, gear and places are not catalogue numbers
    ("Artist - Track (VOLUME 12)", ("Artist", "Track (VOLUME 12)", None)),
    ("Artist - Track (NUMBER 12)", ("Artist", "Track (NUMBER 12)", None)),
    ("Artist - Track (PART12)", ("Artist", "Track (PART12)", None)),
    ("Artist - Track (PARTE 012)", ("Artist", "Track (PARTE 012)", None)),
    ("Artist - Track (CD 012)", ("Artist", "Track (CD 012)", None)),
    ("Artist - Track (DISC 012)", ("Artist", "Track (DISC 012)", None)),
    ("Artist - Track (TR-808)", ("Artist", "Track (TR-808)", None)),
    ("Artist - Track (TB303)", ("Artist", "Track (TB303)", None)),
    ("Artist - Track (UB40)", ("Artist", "Track (UB40)", None)),
    ("Artist - Track (AREA 51)", ("Artist", "Track (AREA 51)", None)),
    ("Artist - Track (KISS 100)", ("Artist", "Track (KISS 100)", None)),
    ("Chopin - Nocturne (BWV 1007)", ("Chopin", "Nocturne (BWV 1007)", None)),
    ("Artist - Track (ADE2019)", ("Artist", "Track (ADE2019)", None)),
    ("Artist - Up/Unreleased Love", ("Artist", "Up/Unreleased Love", None)),
    # what a removed prefix or tag leaves behind
    ("FREE DL: Artist - Track", ("Artist", "Track", None)),
    ("FREE DOWNLOAD: Artist - Track", ("Artist", "Track", None)),
    ("FREE DOWNLOAD | Artist - Track", ("Artist", "Track", None)),
    ("Artist - Track 【FREE DL】", ("Artist", "Track", None)),
    ("Artist - Track.mp3 - Extended Mix", ("Artist", "Track", "Extended Mix")),
    ("Artist - Track - * - Extended Mix", ("Artist", "Track", "Extended Mix")),
    ("Artist - Track (Live) / Unreleased", ("Artist", "Track", "Live")),
    # cleanup that takes the whole title side keeps the artist and the title as written
    ("Artist - .mp3", ("Artist", ".mp3", None)),
    ("Artist - *", ("Artist", "*", None)),
    ("Artist - Free Dl", ("Artist", "Free Dl", None)),
])
def test_parse_youtube_title_review_cases(title, expected):
    q = parse_youtube_title(title, "x")
    assert (q.artist, q.title, q.version) == expected


@pytest.mark.parametrize("title,uploader,expected", [
    # the real titles: the genre before the "|" was read as the title, the bracket stayed on the artist
    ("HARD TECHNO | The Prodigy - No Good (Samuel Moriero & RBX Remix) [FREE DL]", "LOST IN RAVE",
     ("The Prodigy", "No Good", "Samuel Moriero & RBX Remix")),
    ("[TECHNO] Patrick Hollo - Low [Subwoofer Records]", "Subwoofer Records", ("Patrick Hollo", "Low", None)),
    # a leading genre tag, bracketed or before a ":" or "|"
    ("TECHNO | Artist - Track", "x", ("Artist", "Track", None)),
    ("Melodic Techno | Artist - Track (Extended Mix)", "x", ("Artist", "Track", "Extended Mix")),
    ("Melodic Techno: Artist - Track", "x", ("Artist", "Track", None)),
    ("[Psytrance] Artist - Track", "x", ("Artist", "Track", None)),
    ("(Psytrance) Artist - Track", "x", ("Artist", "Track", None)),
    ("[Melodic Techno] Artist - Track", "x", ("Artist", "Track", None)),
    ("[DnB] Artist - Track", "x", ("Artist", "Track", None)),
    ("[Drum & Bass] Artist - Track", "x", ("Artist", "Track", None)),
    ("[Tech House] Artist - Track (Extended Mix)", "x", ("Artist", "Track", "Extended Mix")),
    ("[Electro] - Pegboard Nerds - Hero [Monstercat Release]", "Monstercat", ("Pegboard Nerds", "Hero", None)),
    ("[FREE DL] [TECHNO] Artist - Track", "x", ("Artist", "Track", None)),
    # the "|" part that reads "Artist - Title", over a label, a genre or the channel's own name
    ("Label | Artist - Track [FREE DL]", "x", ("Artist", "Track", None)),
    ("Label | Artist - Track - Extended Mix", "x", ("Artist", "Track", "Extended Mix")),
    ("LOST IN RAVE | Artist - Track", "LOST IN RAVE", ("Artist", "Track", None)),
    ("Channel | Track", "Channel", ("Channel", "Track", None)),
    ("Techno | Premiere | Artist - Track", "x", ("Artist", "Track", None)),
])
def test_a_genre_tag_label_or_channel_before_the_track_is_dropped(title, uploader, expected):
    q = parse_youtube_title(title, uploader)
    assert (q.artist, q.title, q.version) == expected


@pytest.mark.parametrize("title,expected", [
    # not genre tags: a catalogue number, a version, a word that is no genre alone, a name, a title
    ("[NM022] Artist - Track", ("Artist", "Track", None)),
    ("(Remix) Artist - Track", ("(Remix) Artist", "Track", None)),
    ("(Deep) Artist - Track", ("(Deep) Artist", "Track", None)),
    ("(House Music) Artist - Track", ("(House Music) Artist", "Track", None)),
    ("(Hed) P.E. - Bartender", ("(Hed) P.E.", "Bartender", None)),
    ("House: Track", ("x", "House: Track", None)),                  # no "Artist - Title" after it
    ("[TECHNO]", ("x", "[TECHNO]", None)),                          # nothing after it
    # the first "|" part as before: it has a dash, no later part has one, or several do
    ("Artist - Track | Label", ("Artist", "Track", None)),
    ("Artist - Track Remix | Label - Something", ("Artist", "Track Remix", None)),
    ("Artist | Track", ("x", "Artist", None)),
    ("Artist | Track - Extended Mix", ("x", "Artist", None)),      # "Track - Extended Mix" is no artist
    ("Label | A - T | B - U", ("x", "Label", None)),
])
def test_what_is_not_a_genre_tag_or_a_better_pipe_part_stays(title, expected):
    q = parse_youtube_title(title, "x")
    assert (q.artist, q.title, q.version) == expected


def test_music_in_parentheses_is_a_label_when_it_is_the_channel():
    q = parse_youtube_title("Artist - Track (Armada Music) (Extended Mix)", "Armada Music")
    assert (q.artist, q.title, q.version) == ("Artist", "Track", "Extended Mix")


def test_a_long_run_of_spaces_parses_quickly():
    start = time.perf_counter()
    q = parse_youtube_title("Artist -" + " " * 20_000 + "Track (FREE DL) [NM022]", "x")
    assert time.perf_counter() - start < 0.5
    assert (q.artist, q.title) == ("Artist", "Track")
    parse_youtube_title(" " * 20_000, "x")
    parse_youtube_title("( " * 10_000 + "premiere", "x")
    assert time.perf_counter() - start < 1


def test_a_leading_catalogue_number_that_is_the_channel_stays_the_artist():
    q = parse_youtube_title("ABC123 - Track - Extended Mix", "ABC123 - Topic")
    assert (q.artist, q.title, q.version) == ("ABC123", "Track", "Extended Mix")


@pytest.mark.parametrize("url", [
    "https://youtube.com.evil.example/watch?v=abc",
    "https://notyoutube.com/watch?v=abc",
    "https://youtube.com@127.0.0.1:50300/watch?v=abc",
    "https://evil.example/youtu.be/abc",
])
def test_a_host_that_only_contains_youtube_is_not_youtube(url):
    assert classify(url) == (RequestKind.TEXT, None)


def test_a_youtube_link_keeps_only_the_host_name():
    assert classify("https://user@music.youtube.com:443/watch?v=abc") == (
        RequestKind.YT_TRACK, "https://music.youtube.com/watch?v=abc")
