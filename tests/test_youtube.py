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
