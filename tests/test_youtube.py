from flackey.youtube import parse_ytdlp_json


def test_parse_ytdlp_playlist_json():
    data = {"_type": "playlist", "title": "Goa Set", "entries": [
        {"url": "https://www.youtube.com/watch?v=a", "title": "X - Y", "uploader": "X", "duration": 300},
        {"id": "b", "title": "Z", "channel": "Z - Topic", "duration": None},
    ]}
    name, entries = parse_ytdlp_json(data)
    assert name == "Goa Set" and len(entries) == 2
    assert entries[1].url == "https://www.youtube.com/watch?v=b"
    assert entries[1].uploader == "Z - Topic"


def test_parse_ytdlp_video_json():
    data = {"_type": "video", "title": "X - Y", "webpage_url": "https://www.youtube.com/watch?v=a",
            "uploader": "X", "duration": 301.4}
    name, entries = parse_ytdlp_json(data)
    assert name == "X - Y" and entries[0].duration_s == 301 and entries[0].url.endswith("v=a")


async def test_a_link_is_read_without_any_yt_dlp_on_the_path(monkeypatch):
    """yt-dlp is a Python dependency of this project, but this module used to shell out to a `yt-dlp`
    *binary* -- a separate install that a packaged .app does not have and cannot put on its PATH. Pasting
    a YouTube link is the app's front door, so in a bundle the front door was locked. Reading the link
    through the library that is already inside the app fixes that, and pins the version to the one the
    lockfile chose rather than whatever the machine happens to have."""
    import flackey.youtube as yt

    monkeypatch.setenv("PATH", "")
    info = {"_type": "playlist", "title": "Set", "entries": [{"id": "abc", "title": "Track", "duration": 61.4}]}

    class FakeYoutubeDL:
        def __init__(self, opts):
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def extract_info(self, url, download):
            assert download is False, "reading a link must never start a download"
            return info

        def sanitize_info(self, data):
            return data

    monkeypatch.setattr(yt, "YoutubeDL", FakeYoutubeDL)

    name, entries = await yt.fetch_youtube("https://youtu.be/abc")

    assert name == "Set"
    assert [e.title for e in entries] == ["Track"]
    assert entries[0].duration_s == 61
