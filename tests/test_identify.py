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
    ("Artist - Track (Armada Music) [Extended Mix]", ("Artist", "Track", "Extended Mix")),
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
