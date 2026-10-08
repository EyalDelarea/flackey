from pathlib import Path
from typing import ClassVar

import pytest

import flackey.youtube as yt
from flackey.youtube import fetch_audio, parse_ytdlp_json, video_id


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


async def test_fetch_audio_downloads_into_the_folder(tmp_path: Path, monkeypatch):
    """The reference audio (issue #67): the best audio-only stream, in whatever container YouTube serves,
    landed in this request's own folder under a name the caller chose rather than the video's title."""
    class FakeYDL:
        def __init__(self, opts): self.opts = opts
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def extract_info(self, url, download=True):
            assert download and self.opts["format"] == "bestaudio/best" and self.opts["noplaylist"]
            out = Path(self.opts["outtmpl"].replace("%(ext)s", "webm"))
            out.write_bytes(b"audio")
            return {"id": "jNQXAC9IVRw", "ext": "webm"}
        def prepare_filename(self, info):
            return self.opts["outtmpl"].replace("%(ext)s", info["ext"])
    monkeypatch.setattr("flackey.youtube.YoutubeDL", FakeYDL)
    got = await fetch_audio("https://www.youtube.com/watch?v=jNQXAC9IVRw", tmp_path / "req1")
    assert got == tmp_path / "req1" / "reference.webm" and got.read_bytes() == b"audio"


def test_video_id():
    assert video_id("https://www.youtube.com/watch?v=jNQXAC9IVRw") == "jNQXAC9IVRw"
    assert video_id("https://youtu.be/jNQXAC9IVRw") == "jNQXAC9IVRw"


# ---- bounds on what a hostile link can cost (disk, memory, a worker thread) ----------------------------
# `asyncio.wait_for` gives up waiting on the thread but cannot stop it: the download would carry on
# filling the disk after the request had already been told it timed out. So the bounds live inside the
# yt-dlp call itself, where they do stop it.


class _RecordingYDL:
    """A YoutubeDL that records the options it was built with and downloads nothing."""
    built: ClassVar[list[dict]] = []

    def __init__(self, opts):
        self.opts = opts
        _RecordingYDL.built.append(opts)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def extract_info(self, url, download=True):
        return {"id": "x", "ext": "webm"}

    def prepare_filename(self, info):
        return self.opts["outtmpl"].replace("%(ext)s", info["ext"])


def _audio_opts(tmp_path: Path, monkeypatch) -> dict:
    _RecordingYDL.built = []
    monkeypatch.setattr(yt, "YoutubeDL", _RecordingYDL)
    with pytest.raises(yt.YouTubeError):  # nothing was written; only the options are under test here
        yt._download_audio("https://www.youtube.com/watch?v=x", tmp_path)
    return _RecordingYDL.built[0]


def test_the_reference_download_is_capped_in_size(tmp_path: Path, monkeypatch):
    assert _audio_opts(tmp_path, monkeypatch)["max_filesize"] == yt.MAX_REFERENCE_BYTES


def test_the_reference_download_refuses_a_video_longer_than_the_cap(tmp_path: Path, monkeypatch):
    accept = _audio_opts(tmp_path, monkeypatch)["match_filter"]
    assert accept({"duration": 6 * 60}, incomplete=False) is None
    assert accept({"duration": yt.MAX_REFERENCE_S}, incomplete=False) is None
    assert "long" in accept({"duration": yt.MAX_REFERENCE_S + 1}, incomplete=False)


def test_the_reference_download_refuses_a_live_stream_or_an_unknown_length(tmp_path: Path, monkeypatch):
    accept = _audio_opts(tmp_path, monkeypatch)["match_filter"]
    assert accept({"duration": 60, "is_live": True}, incomplete=False)
    assert accept({"duration": 60, "live_status": "is_upcoming"}, incomplete=False)
    assert accept({"duration": None}, incomplete=False)
    # A partial record (yt-dlp's first look, before the formats are read) can lack a duration yet:
    # refusing it there would refuse videos that turn out to be fine.
    assert accept({"duration": None}, incomplete=True) is None


def test_a_rejected_video_is_an_error_not_a_missing_file(tmp_path: Path, monkeypatch):
    """yt-dlp does not raise when its filter says no: it returns the info and downloads nothing. Without
    a check the caller would be handed a path that does not exist, and fpcalc would be the one to say so."""
    class RejectingYDL(_RecordingYDL):
        def extract_info(self, url, download=True):
            assert self.opts["match_filter"]({"duration": 3 * 3600}, incomplete=False)
            return {"id": "x", "ext": "webm"}

    monkeypatch.setattr(yt, "YoutubeDL", RejectingYDL)
    with pytest.raises(yt.YouTubeError, match="long"):
        yt._download_audio("https://www.youtube.com/watch?v=x", tmp_path)


def test_the_progress_hook_stops_a_download_past_the_byte_cap(tmp_path: Path, monkeypatch):
    """`max_filesize` only fires when the server declares a length up front; a stream that does not is
    stopped by the hook, which yt-dlp lets abort the transfer by raising."""
    hook = _audio_opts(tmp_path, monkeypatch)["progress_hooks"][0]
    hook({"status": "downloading", "downloaded_bytes": 1024})
    with pytest.raises(yt.ReferenceAborted, match="larger"):
        hook({"status": "downloading", "downloaded_bytes": yt.MAX_REFERENCE_BYTES + 1})


def test_the_progress_hook_stops_a_download_past_the_deadline(tmp_path: Path, monkeypatch):
    """The thread outlives `wait_for`'s timeout, so the same deadline is enforced from inside it."""
    now = [1000.0]
    monkeypatch.setattr(yt.time, "monotonic", lambda: now[0])
    hook = _audio_opts(tmp_path, monkeypatch)["progress_hooks"][0]
    hook({"status": "downloading", "downloaded_bytes": 1})
    now[0] += yt.YOUTUBE_FETCH_TIMEOUT_S + 1
    with pytest.raises(yt.ReferenceAborted, match="timed out"):
        hook({"status": "downloading", "downloaded_bytes": 2})


async def test_an_aborted_reference_download_leaves_nothing_behind(tmp_path: Path, monkeypatch):
    """yt-dlp keeps its `.part` file when a hook aborts it; at the byte cap that is the cap itself left on
    disk, per request."""
    class AbortingYDL(_RecordingYDL):
        def extract_info(self, url, download=True):
            Path(self.opts["outtmpl"].replace("%(ext)s", "webm.part")).write_bytes(b"x" * 10)
            self.opts["progress_hooks"][0]({"status": "downloading",
                                            "downloaded_bytes": yt.MAX_REFERENCE_BYTES + 1})

    monkeypatch.setattr(yt, "YoutubeDL", AbortingYDL)
    with pytest.raises(yt.YouTubeError):
        await yt.fetch_audio("https://www.youtube.com/watch?v=x", tmp_path / "req1")
    assert list((tmp_path / "req1").iterdir()) == []


def test_the_playlist_read_asks_yt_dlp_for_a_bounded_number_of_entries():
    assert yt._YDL_OPTS["playlistend"] == yt.MAX_PLAYLIST_ENTRIES


def test_a_playlist_longer_than_the_cap_is_truncated_and_says_so(caplog):
    """The backstop for a listing that arrives longer than asked for: every entry becomes a request row,
    so an unbounded paste is an unbounded queue."""
    data = {"_type": "playlist", "title": "Huge",
            "entries": [{"id": f"v{i}", "title": f"T{i}"} for i in range(yt.MAX_PLAYLIST_ENTRIES + 5)]}
    with caplog.at_level("WARNING", logger="flackey.youtube"):
        name, entries = parse_ytdlp_json(data)
    assert name == "Huge" and len(entries) == yt.MAX_PLAYLIST_ENTRIES
    assert entries[-1].title == f"T{yt.MAX_PLAYLIST_ENTRIES - 1}"
    assert "Huge" in caplog.text and str(yt.MAX_PLAYLIST_ENTRIES) in caplog.text
