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


@pytest.mark.parametrize("title,expected", [
    ("Into the Void", ("Into the Void", None)),
    ("Into the Void (Original Mix)", ("Into the Void", "Original Mix")),
    ("Into the Void (Vini Vici Remix)", ("Into the Void", "Vini Vici Remix")),
    ("Into the Void - Extended Mix", ("Into the Void", "Extended Mix")),
    ("Into the Void [Radio Edit]", ("Into the Void", "Radio Edit")),
    ("Into the Void (Live at Ozora)", ("Into the Void", "Live at Ozora")),
    ("Into the Void (feat. Someone)", ("Into the Void (feat. Someone)", None)),
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
