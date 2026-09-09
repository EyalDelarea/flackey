# Flackey Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A single Python process that receives track requests through the owner's Telegram bot, fetches audio from @DeezerMusicBot through the owner's account, verifies quality, tags from Beatport, files into the permanent library, writes M3U8 playlist files for Rekordbox import, and exposes a JSON API for the UI.

**Architecture:** One asyncio process (`crate start`) running an aiogram inbox bot, a pipeline worker, and a FastAPI server, all sharing one SQLite store. External systems (Beatport, Deezer public API, the source bot, ffmpeg, yt-dlp) are each isolated behind a small module with pure, fixture-testable parsing functions. The React UI is a separate follow-up plan that consumes the JSON API built here.

**Tech Stack:** Python 3.12, uv, Telethon 1.44, aiogram 3, FastAPI + uvicorn, curl_cffi, httpx, yt-dlp, mutagen, rapidfuzz, numpy, typer, pydantic-settings, pytest + pytest-asyncio, ffmpeg/ffprobe (Homebrew).

**Spec:** `docs/superpowers/specs/2026-09-03-flackey-design.md`
**Source bot protocol:** `docs/source-bot-protocol.md`

## Global Constraints

- Python 3.12 exactly (`requires-python = ">=3.12,<3.13"`), managed by uv. Run everything as `uv run ...`.
- Configuration only from `.env` via `flackey.config.Settings`; never read `os.environ` elsewhere.
- Secrets (`.env`, `*.session`) are git-ignored; never print them, never commit them.
- Library root default `~/Music/DJ Library`; layout `<Genre>/<Label>/<Artist> - <Title> (<Mix Name>).<ext>`; files are never moved after filing.
- Quality floor: MP3 must probe ≥ 320 kbps and show spectral content to ≥ 18 000 Hz; FLAC/WAV/AIFF must show content ≥ 20 000 Hz; anything else is rejected and deleted, with the rejection record and spectrogram PNG kept.
- Confidence threshold 80. Below 80, or no Beatport entry, or only non-original versions when no version was requested: park in `awaiting_review`.
- Only messages from `OWNER_TELEGRAM_ID` are processed; all others ignored silently.
- Key notation in tags is Beatport's classical form, e.g. `A Major`.
- Every module has tests; commit after each task with a message of the form `feat(<module>): ...` or `test(<module>): ...` ending with the two attribution trailers used in the repo's first commit.
- No audio BPM/key analysis. No YouTube audio fallback. No backup feature.

---

## File Structure

```
pyproject.toml
src/flackey/
  __init__.py
  config.py        Settings (pydantic-settings) loaded from .env
  models.py        dataclasses + enums shared by all modules
  store.py         SQLite schema and every read/write (only DB-touching module)
  identify.py      input classification, text/YouTube-title parsing, playlist expansion
  catalog.py       Beatport search (curl_cffi) + pure HTML/JSON parsing
  deezer.py        Deezer public API client (track by id -> isrc etc.)
  match.py         confidence scoring + decision
  verify.py        ffprobe bitrate, spectral cutoff, spectrogram PNG
  tag.py           mutagen tagging + artwork fetch
  library.py       final path computation, sanitizing, filing, duplicate check
  export.py        M3U8 playlist writer (spec §8: no rekordbox.xml, on purpose)
  source/
    __init__.py
    base.py        Source protocol
    deezer_bot.py  Telethon-driven @DeezerMusicBot implementation + pure parsers
  worker.py        the pipeline (uses everything above through interfaces)
  notify.py        Notifier protocol + TelegramNotifier
  inbox.py         aiogram bot: owner messages -> requests; review buttons
  web.py           FastAPI JSON API
  app.py           process wiring: Telethon client, bot, worker, web server in one event loop
  cli.py           typer app: start, status, export, add, login
tests/
  __init__.py      empty; makes `from tests.conftest import ...` importable under pytest's default import mode
  conftest.py
  fixtures/beatport_search_astral.html   (already committed)
  test_config.py test_store.py test_identify.py test_catalog.py test_deezer.py
  test_match.py test_verify.py test_tag.py test_library.py test_export.py
  test_source_parsers.py test_worker.py test_inbox.py test_web.py test_cli.py
  live/test_source_live.py   opt-in, hits the real source bot (FLACKEY_LIVE=1)
web/dist/index.html   placeholder until the UI plan builds the React bundle here
Dockerfile
README.md
uv.lock          created by `uv sync` in Task 1 and committed; the Dockerfile copies it
```

---

### Task 1: Project skeleton, settings, models

**Files:**
- Create: `pyproject.toml`, `src/flackey/__init__.py`, `src/flackey/config.py`, `src/flackey/models.py`, `tests/__init__.py` (empty), `tests/conftest.py`, `tests/test_config.py`, `tests/test_models.py`, `web/dist/index.html` (placeholder)

**Interfaces:**
- Produces: `Settings` with fields `telegram_api_id: int`, `telegram_api_hash: str`, `inbox_bot_token: str`, `owner_telegram_id: int`, `source_bot_username: str`, `library_root: Path`, `web_port: int`, `data_dir: Path` (default `~/.config/flackey`), and `def load_settings(env_file: Path | None = None) -> Settings`.
- Produces: `models.RequestKind`, `models.RequestState`, `models.Query`, `models.Candidate`, `models.CatalogTrack`, `models.Verdict`, `models.Track`, `models.Request` (exact definitions in Step 3).

- [ ] **Step 1: Write pyproject.toml**

```toml
[project]
name = "flackey"
version = "0.1.0"
description = "Personal DJ library builder: Telegram inbox -> verified, tagged tracks -> Rekordbox"
requires-python = ">=3.12,<3.13"
dependencies = [
  "telethon>=1.44,<2",
  "aiogram>=3.13,<4",
  "fastapi>=0.115",
  "uvicorn[standard]>=0.30",
  "curl_cffi>=0.7",
  "httpx>=0.27",
  "yt-dlp>=2026.8",
  "mutagen>=1.47",
  "rapidfuzz>=3.9",
  "numpy>=2.0",
  "typer>=0.12",
  "pydantic-settings>=2.4",
]

[project.scripts]
crate = "flackey.cli:app"

[dependency-groups]
dev = ["pytest>=8", "pytest-asyncio>=0.24", "respx>=0.21", "ruff>=0.6"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/flackey"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]

[tool.ruff]
line-length = 100
```

Create an empty `src/flackey/__init__.py` containing `__version__ = "0.1.0"`.

- [ ] **Step 2: Write the failing settings test**

`tests/test_config.py`:

```python
from pathlib import Path
from flackey.config import load_settings


def test_load_settings_from_env_file(tmp_path: Path):
    env = tmp_path / ".env"
    env.write_text(
        "TELEGRAM_API_ID=123\nTELEGRAM_API_HASH=abc\nINBOX_BOT_TOKEN=t:ok\n"
        "OWNER_TELEGRAM_ID=42\nSOURCE_BOT_USERNAME=DeezerMusicBot\n"
        "LIBRARY_ROOT=~/Music/DJ Library\nWEB_PORT=9000\n"
    )
    s = load_settings(env)
    assert s.telegram_api_id == 123
    assert s.owner_telegram_id == 42
    assert s.web_port == 9000
    assert s.library_root == Path("~/Music/DJ Library").expanduser()
    assert s.data_dir == Path("~/.config/flackey").expanduser()
```

- [ ] **Step 3: Run it to verify it fails**

Run: `uv run pytest tests/test_config.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'flackey.config'`

- [ ] **Step 4: Write config.py and models.py**

`src/flackey/config.py`:

```python
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    telegram_api_id: int
    telegram_api_hash: str
    inbox_bot_token: str
    owner_telegram_id: int
    source_bot_username: str = "DeezerMusicBot"
    library_root: Path = Path("~/Music/DJ Library")
    web_port: int = 8765
    data_dir: Path = Path("~/.config/flackey")

    @field_validator("library_root", "data_dir", mode="after")
    @classmethod
    def _expand(cls, v: Path) -> Path:
        return v.expanduser()

    @property
    def db_path(self) -> Path:
        return self.data_dir / "flackey.sqlite"

    @property
    def session_path(self) -> Path:
        return self.data_dir / "owner"

    @property
    def spectrogram_dir(self) -> Path:
        return self.data_dir / "spectrograms"

    @property
    def tmp_dir(self) -> Path:
        return self.data_dir / "tmp"


REPO_ENV = Path(__file__).resolve().parents[2] / ".env"


def load_settings(env_file: Path | None = None) -> Settings:
    if env_file is None:
        # `./.env` when run from the repo root; otherwise the repo's own .env, so `crate` works from any cwd
        env_file = Path(".env") if Path(".env").exists() else REPO_ENV
    return Settings(_env_file=env_file)
```

`src/flackey/models.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path


class RequestKind(StrEnum):
    TEXT = "text"
    YT_TRACK = "yt_track"
    YT_PLAYLIST = "yt_playlist"


class RequestState(StrEnum):
    QUEUED = "queued"
    IDENTIFYING = "identifying"
    AWAITING_REVIEW = "awaiting_review"
    FETCHING = "fetching"
    VERIFYING = "verifying"
    FILING = "filing"
    DONE = "done"
    DUPLICATE = "duplicate"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    NOT_FOUND = "not_found"
    ERROR = "error"


INFLIGHT_STATES = {
    RequestState.IDENTIFYING,
    RequestState.FETCHING,
    RequestState.VERIFYING,
    RequestState.FILING,
}
TERMINAL_STATES = {
    RequestState.DONE,
    RequestState.DUPLICATE,
    RequestState.REJECTED,
    RequestState.CANCELLED,
    RequestState.NOT_FOUND,
    RequestState.ERROR,
}


@dataclass
class Query:
    raw: str
    artist: str | None = None
    title: str | None = None
    version: str | None = None
    duration_s: int | None = None

    def search_text(self) -> str:
        if self.artist and self.title:
            base = f"{self.artist} {self.title}"
            return f"{base} {self.version}" if self.version else base
        return self.raw


@dataclass
class Candidate:
    source: str
    source_ref: str
    artist: str
    title: str
    mix_name: str | None = None
    duration_s: int | None = None
    deezer_id: int | None = None
    isrc: str | None = None
    rank: int = 0
    score: int | None = None
    catalog_track_id: int | None = None
    id: int | None = None
    request_id: int | None = None


@dataclass
class CatalogTrack:
    id: int
    artist: str
    title: str
    mix_name: str
    label: str
    genre: str
    isrc: str | None = None
    sub_genre: str | None = None
    catalog_number: str | None = None
    release_name: str | None = None
    release_date: str | None = None  # YYYY-MM-DD
    bpm: int | None = None
    key: str | None = None
    duration_ms: int | None = None
    artwork_url: str | None = None

    @property
    def duration_s(self) -> int | None:
        return None if self.duration_ms is None else round(self.duration_ms / 1000)

    @property
    def year(self) -> str | None:
        return self.release_date[:4] if self.release_date else None


@dataclass
class Verdict:
    passed: bool
    fmt: str
    bitrate_kbps: int
    cutoff_hz: int
    reason: str
    spectrogram_path: Path | None = None


@dataclass
class Track:
    id: int
    path: Path
    fmt: str
    bitrate_kbps: int
    cutoff_hz: int
    file_size: int
    artist: str
    title: str
    mix_name: str
    duration_s: int | None
    isrc: str | None
    catalog_track_id: int | None
    request_id: int | None
    added_at: str
    verified_at: str | None = None          # spec §4.2; same instant as added_at in v1
    spectrogram_path: Path | None = None    # spec §6: the PNG is kept for passed tracks too


@dataclass
class Request:
    id: int
    created_at: str
    updated_at: str
    raw_text: str
    kind: RequestKind
    state: RequestState
    playlist_id: int | None = None
    playlist_position: int | None = None
    source_url: str | None = None
    query_artist: str | None = None
    query_title: str | None = None
    query_version: str | None = None
    query_duration_s: int | None = None
    chosen_candidate_id: int | None = None
    catalog_track_id: int | None = None
    confidence: int | None = None
    flag_reason: str | None = None
    error_message: str | None = None
    attempts: int = 0
    retry_after: str | None = None  # ISO timestamp; `next_queued` skips the request until then (backoff)
    track_id: int | None = None

    def query(self) -> Query:
        return Query(
            raw=self.raw_text,
            artist=self.query_artist,
            title=self.query_title,
            version=self.query_version,
            duration_s=self.query_duration_s,
        )


@dataclass
class Playlist:
    id: int
    source_url: str
    name: str
    created_at: str
    updated_at: str
    track_ids: list[int] = field(default_factory=list)


@dataclass
class Rejection:
    id: int
    request_id: int
    reason: str
    bitrate_kbps: int | None
    cutoff_hz: int | None
    spectrogram_path: str | None
    created_at: str
```

`tests/conftest.py`:

```python
import shutil
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixtures() -> Path:
    return FIXTURES


def has_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


requires_ffmpeg = pytest.mark.skipif(not has_ffmpeg(), reason="ffmpeg not installed")
```

`tests/__init__.py`: an empty file. Without it, `from tests.conftest import requires_ffmpeg` (used by Tasks 7, 8, 12) raises `ModuleNotFoundError: No module named 'tests'` at collection time and aborts the whole run. `tests/live/` needs no `__init__.py`.

`web/dist/index.html` (placeholder so the static mount in Task 14, the Dockerfile in Task 15, and `crate start` all work before the UI plan builds the React bundle):

```html
<h1>flackey</h1><p>UI not built yet. API at <a href="/api/health">/api/health</a>.</p>
```

`.gitignore` already handles this (committed with the plan revision): `/dist/` and `web/dist/*` with `!web/dist/index.html`. Git cannot re-include a file under an ignored *directory*, which is why the patterns end in `/*` and `/dist/` is anchored to the root. `git add web/dist/index.html` must succeed; if it says "paths are ignored" the patterns were changed back.

`tests/test_models.py`:

```python
from flackey.models import CatalogTrack, Query


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
```

- [ ] **Step 5: Install and run tests**

Run: `uv sync && uv run pytest tests/test_config.py tests/test_models.py -v`
Expected: 3 PASS. `uv sync` creates `uv.lock`; commit it (the Dockerfile in Task 15 does `COPY pyproject.toml uv.lock`).

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock src tests web/dist/index.html .gitignore
git commit -m "feat(core): project skeleton, settings, shared models"
```

---

### Task 2: SQLite store

**Files:**
- Create: `src/flackey/store.py`, `tests/test_store.py`

**Interfaces:**
- Consumes: `models.*`
- Produces: class `Store(path: Path)` with methods (all synchronous):
  - `add_request(raw_text: str, kind: RequestKind, *, source_url: str | None = None, playlist_id: int | None = None, playlist_position: int | None = None, query: Query | None = None) -> int`
  - `get_request(request_id: int) -> Request`
  - `list_requests(states: set[RequestState] | None = None, limit: int = 200) -> list[Request]`
  - `next_queued() -> Request | None` (oldest `queued` whose `retry_after` is NULL or in the past; retries with backoff wait here)
  - `update_request(request_id: int, **fields) -> None` (any column; sets `updated_at`)
  - `set_state(request_id: int, state: RequestState, *, error_message: str | None = None, flag_reason: str | None = None) -> None`
  - `reset_inflight() -> int` (in-flight states -> queued; returns count)
  - `add_candidates(request_id: int, candidates: list[Candidate]) -> list[Candidate]` (returns copies with `id`, `request_id`)
  - `get_candidates(request_id: int) -> list[Candidate]`
  - `get_candidate(candidate_id: int) -> Candidate`
  - `upsert_catalog_track(track: CatalogTrack) -> None`, `get_catalog_track(track_id: int) -> CatalogTrack | None`
  - `add_track(*, path: Path, fmt: str, bitrate_kbps: int, cutoff_hz: int, file_size: int, artist: str, title: str, mix_name: str, duration_s: int | None, isrc: str | None, catalog_track_id: int | None, request_id: int | None, spectrogram_path: Path | None = None) -> int` (sets `added_at` and `verified_at` to now)
  - `open_request_for_url(source_url: str) -> Request | None` (a request for that URL in any non-terminal state; lets a re-sent playlist skip tracks that are already queued or in flight)
  - `get_track(track_id: int) -> Track`, `list_tracks(search: str | None = None, limit: int = 500) -> list[Track]`
  - `find_track_by_isrc(isrc: str) -> Track | None`, `find_track_by_path(path: Path) -> Track | None`
  - `find_track_by_meta(artist: str, title: str, mix_name: str, duration_s: int | None, tolerance_s: int = 3) -> Track | None`. Judgment call, deliberately stricter than spec §3.4's "duration within 3 s": when either duration is unknown the row still counts as a duplicate, because a false "already in library" costs one Telegram reply while a false miss costs a download and a second file with the same name.
  - `upsert_playlist(source_url: str, name: str) -> int`, `get_playlist(playlist_id: int) -> Playlist`, `list_playlists() -> list[Playlist]`, `add_playlist_track(playlist_id: int, track_id: int, position: int) -> None`
  - `add_rejection(request_id: int, reason: str, bitrate_kbps: int | None, cutoff_hz: int | None, spectrogram_path: Path | None) -> int`, `list_rejections(limit: int = 200) -> list[Rejection]`, `get_rejection(rejection_id: int) -> Rejection`
  - `stats() -> dict` with keys `requests_by_state: dict[str,int]`, `tracks: int`, `rejections: int`, `bytes: int`, `by_genre: dict[str,int]`, `by_label: dict[str,int]`, `requests_per_week: list[tuple[str,int]]`
  - `get_setting(key: str, default: str | None = None) -> str | None`, `set_setting(key: str, value: str) -> None`

- [ ] **Step 1: Write the failing tests**

`tests/test_store.py`:

```python
from pathlib import Path

import pytest

from flackey.models import Candidate, CatalogTrack, Query, RequestKind, RequestState
from flackey.store import Store


@pytest.fixture
def store(tmp_path: Path) -> Store:
    return Store(tmp_path / "t.sqlite")


def _catalog() -> CatalogTrack:
    return CatalogTrack(id=16552105, isrc="UKU932231081", artist="Astral Projection",
                        title="Into the Void", mix_name="Original Mix", label="Sacred Technology",
                        genre="Psy-Trance", sub_genre="Goa Trance", release_date="2022-06-03",
                        bpm=142, key="A Major", duration_ms=442816, artwork_url="https://x/a.jpg")


def test_request_lifecycle(store: Store):
    rid = store.add_request("astral projection into the void", RequestKind.TEXT,
                            query=Query(raw="x", artist="Astral Projection", title="Into the Void"))
    r = store.get_request(rid)
    assert r.state == RequestState.QUEUED
    assert r.query_artist == "Astral Projection"
    assert store.next_queued().id == rid
    store.set_state(rid, RequestState.FETCHING)
    assert store.next_queued() is None
    assert store.reset_inflight() == 1
    assert store.get_request(rid).state == RequestState.QUEUED
    store.set_state(rid, RequestState.ERROR, error_message="boom")
    assert store.get_request(rid).error_message == "boom"
    store.update_request(rid, confidence=96, attempts=2)
    assert store.get_request(rid).confidence == 96


def test_next_queued_is_fifo(store: Store):
    a = store.add_request("a", RequestKind.TEXT)
    b = store.add_request("b", RequestKind.TEXT)
    assert store.next_queued().id == a
    store.set_state(a, RequestState.DONE)
    assert store.next_queued().id == b


def test_next_queued_honours_retry_after(store: Store):
    a = store.add_request("a", RequestKind.TEXT)
    b = store.add_request("b", RequestKind.TEXT)
    store.update_request(a, retry_after="2999-01-01T00:00:00+00:00")
    assert store.next_queued().id == b
    store.update_request(a, retry_after="2000-01-01T00:00:00+00:00")
    assert store.next_queued().id == a


def test_open_request_for_url(store: Store):
    url = "https://www.youtube.com/watch?v=a"
    assert store.open_request_for_url(url) is None
    rid = store.add_request("x", RequestKind.YT_TRACK, source_url=url)
    assert store.open_request_for_url(url).id == rid
    store.set_state(rid, RequestState.FETCHING)
    assert store.open_request_for_url(url).id == rid
    store.set_state(rid, RequestState.DONE)
    assert store.open_request_for_url(url) is None


def test_find_track_by_path(store: Store, tmp_path):
    tid = store.add_track(path=tmp_path / "a.mp3", fmt="mp3", bitrate_kbps=320, cutoff_hz=19800,
                          file_size=1, artist="A", title="T", mix_name="Original Mix", duration_s=1,
                          isrc=None, catalog_track_id=None, request_id=None)
    assert store.find_track_by_path(tmp_path / "a.mp3").id == tid
    assert store.find_track_by_path(tmp_path / "b.mp3") is None


def test_candidates_roundtrip(store: Store):
    rid = store.add_request("q", RequestKind.TEXT)
    saved = store.add_candidates(rid, [
        Candidate(source="deezer_bot", source_ref="dz_track:1:send", artist="A", title="T",
                  mix_name="Original Mix", duration_s=442, deezer_id=1, isrc="X", rank=1, score=96),
        Candidate(source="deezer_bot", source_ref="dz_track:2:send", artist="A", title="T (Remix)", rank=2),
    ])
    assert [c.id for c in saved] == [1, 2]
    got = store.get_candidates(rid)
    assert got[0].isrc == "X" and got[0].score == 96 and got[1].mix_name is None
    assert store.get_candidate(2).source_ref == "dz_track:2:send"


def test_catalog_upsert(store: Store):
    ct = _catalog()
    store.upsert_catalog_track(ct)
    ct.bpm = 143
    store.upsert_catalog_track(ct)
    assert store.get_catalog_track(16552105).bpm == 143
    assert store.get_catalog_track(1) is None


def test_tracks_and_duplicates(store: Store, tmp_path: Path):
    store.upsert_catalog_track(_catalog())
    tid = store.add_track(path=tmp_path / "a.mp3", fmt="mp3", bitrate_kbps=320, cutoff_hz=19800,
                          file_size=100, artist="Astral Projection", title="Into the Void",
                          mix_name="Original Mix", duration_s=443, isrc="UKU932231081",
                          catalog_track_id=16552105, request_id=None)
    assert store.find_track_by_isrc("UKU932231081").id == tid
    assert store.find_track_by_isrc("nope") is None
    assert store.find_track_by_meta("astral projection", "into the void", "original mix", 445).id == tid
    assert store.find_track_by_meta("astral projection", "into the void", "original mix", 460) is None
    assert store.find_track_by_meta("astral projection", "into the void", "vini vici remix", 443) is None
    assert store.list_tracks(search="void")[0].id == tid
    assert store.list_tracks(search="zzz") == []


def test_playlists(store: Store, tmp_path: Path):
    pid = store.upsert_playlist("https://youtube.com/playlist?list=1", "Goa Set")
    assert store.upsert_playlist("https://youtube.com/playlist?list=1", "Goa Set renamed") == pid
    tid = store.add_track(path=tmp_path / "a.mp3", fmt="mp3", bitrate_kbps=320, cutoff_hz=19800,
                          file_size=1, artist="a", title="t", mix_name="Original Mix", duration_s=1,
                          isrc=None, catalog_track_id=None, request_id=None)
    store.add_playlist_track(pid, tid, 1)
    store.add_playlist_track(pid, tid, 1)  # idempotent
    p = store.get_playlist(pid)
    assert p.name == "Goa Set renamed" and p.track_ids == [tid]
    assert [x.id for x in store.list_playlists()] == [pid]


def test_rejections_and_stats(store: Store, tmp_path: Path):
    rid = store.add_request("q", RequestKind.TEXT)
    rj = store.add_rejection(rid, "cutoff 16000 Hz below 18000", 320, 16000, tmp_path / "s.png")
    assert store.get_rejection(rj).reason.startswith("cutoff")
    assert len(store.list_rejections()) == 1
    s = store.stats()
    assert s["rejections"] == 1 and s["tracks"] == 0
    assert s["requests_by_state"]["queued"] == 1


def test_settings(store: Store):
    assert store.get_setting("x") is None
    store.set_setting("x", "1")
    store.set_setting("x", "2")
    assert store.get_setting("x") == "2"
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_store.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'flackey.store'`

- [ ] **Step 3: Write store.py**

```python
from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .models import (
    INFLIGHT_STATES,
    TERMINAL_STATES,
    Candidate,
    CatalogTrack,
    Playlist,
    Query,
    Rejection,
    Request,
    RequestKind,
    RequestState,
    Track,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS requests (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  raw_text TEXT NOT NULL, kind TEXT NOT NULL, state TEXT NOT NULL,
  playlist_id INTEGER, playlist_position INTEGER, source_url TEXT,
  query_artist TEXT, query_title TEXT, query_version TEXT, query_duration_s INTEGER,
  chosen_candidate_id INTEGER, catalog_track_id INTEGER, confidence INTEGER,
  flag_reason TEXT, error_message TEXT, attempts INTEGER NOT NULL DEFAULT 0, retry_after TEXT, track_id INTEGER
);
CREATE TABLE IF NOT EXISTS candidates (
  id INTEGER PRIMARY KEY AUTOINCREMENT, request_id INTEGER NOT NULL, rank INTEGER NOT NULL,
  source TEXT NOT NULL, source_ref TEXT NOT NULL, artist TEXT NOT NULL, title TEXT NOT NULL,
  mix_name TEXT, duration_s INTEGER, deezer_id INTEGER, isrc TEXT, score INTEGER, catalog_track_id INTEGER
);
CREATE TABLE IF NOT EXISTS catalog_tracks (
  id INTEGER PRIMARY KEY, isrc TEXT, artist TEXT NOT NULL, title TEXT NOT NULL, mix_name TEXT NOT NULL,
  label TEXT NOT NULL, genre TEXT NOT NULL, sub_genre TEXT, catalog_number TEXT, release_name TEXT,
  release_date TEXT, bpm INTEGER, key TEXT, duration_ms INTEGER, artwork_url TEXT, fetched_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tracks (
  id INTEGER PRIMARY KEY AUTOINCREMENT, path TEXT NOT NULL UNIQUE, fmt TEXT NOT NULL,
  bitrate_kbps INTEGER NOT NULL, cutoff_hz INTEGER NOT NULL, file_size INTEGER NOT NULL,
  artist TEXT NOT NULL, title TEXT NOT NULL, mix_name TEXT NOT NULL, duration_s INTEGER,
  isrc TEXT, catalog_track_id INTEGER, request_id INTEGER, added_at TEXT NOT NULL,
  verified_at TEXT, spectrogram_path TEXT,
  artist_norm TEXT NOT NULL, title_norm TEXT NOT NULL, mix_norm TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS tracks_isrc ON tracks(isrc);
CREATE TABLE IF NOT EXISTS playlists (
  id INTEGER PRIMARY KEY AUTOINCREMENT, source_url TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS playlist_tracks (
  playlist_id INTEGER NOT NULL, track_id INTEGER NOT NULL, position INTEGER NOT NULL,
  PRIMARY KEY (playlist_id, track_id)
);
CREATE TABLE IF NOT EXISTS rejections (
  id INTEGER PRIMARY KEY AUTOINCREMENT, request_id INTEGER NOT NULL, reason TEXT NOT NULL,
  bitrate_kbps INTEGER, cutoff_hz INTEGER, spectrogram_path TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def norm(s: str | None) -> str:
    # same normaliser as match._norm / catalog._norm (spec §5.1): lowercase, punctuation stripped
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", (s or "").lower()).split())


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)

    # ---- requests -------------------------------------------------------
    def add_request(self, raw_text: str, kind: RequestKind, *, source_url: str | None = None,
                    playlist_id: int | None = None, playlist_position: int | None = None,
                    query: Query | None = None) -> int:
        q = query or Query(raw=raw_text)
        cur = self.conn.execute(
            "INSERT INTO requests (created_at, updated_at, raw_text, kind, state, playlist_id, "
            "playlist_position, source_url, query_artist, query_title, query_version, query_duration_s) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (_now(), _now(), raw_text, str(kind), str(RequestState.QUEUED), playlist_id,
             playlist_position, source_url, q.artist, q.title, q.version, q.duration_s),
        )
        self.conn.commit()
        return int(cur.lastrowid)

    def _row_to_request(self, r: sqlite3.Row) -> Request:
        d = dict(r)
        d["kind"] = RequestKind(d["kind"])
        d["state"] = RequestState(d["state"])
        return Request(**d)

    def get_request(self, request_id: int) -> Request:
        r = self.conn.execute("SELECT * FROM requests WHERE id=?", (request_id,)).fetchone()
        if r is None:
            raise KeyError(request_id)
        return self._row_to_request(r)

    def list_requests(self, states: set[RequestState] | None = None, limit: int = 200) -> list[Request]:
        if states:
            marks = ",".join("?" * len(states))
            rows = self.conn.execute(
                f"SELECT * FROM requests WHERE state IN ({marks}) ORDER BY id DESC LIMIT ?",
                [str(s) for s in states] + [limit]).fetchall()
        else:
            rows = self.conn.execute("SELECT * FROM requests ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [self._row_to_request(r) for r in rows]

    def next_queued(self) -> Request | None:
        r = self.conn.execute(
            "SELECT * FROM requests WHERE state=? AND (retry_after IS NULL OR retry_after <= ?) "
            "ORDER BY id ASC LIMIT 1",
            (str(RequestState.QUEUED), _now())).fetchone()
        return None if r is None else self._row_to_request(r)

    def open_request_for_url(self, source_url: str) -> Request | None:
        marks = ",".join("?" * len(TERMINAL_STATES))
        r = self.conn.execute(
            f"SELECT * FROM requests WHERE source_url=? AND state NOT IN ({marks}) ORDER BY id LIMIT 1",
            [source_url] + [str(s) for s in TERMINAL_STATES]).fetchone()
        return None if r is None else self._row_to_request(r)

    def update_request(self, request_id: int, **fields) -> None:
        fields["updated_at"] = _now()
        cols = ", ".join(f"{k}=?" for k in fields)
        vals = [str(v) if isinstance(v, (RequestState, RequestKind)) else v for v in fields.values()]
        self.conn.execute(f"UPDATE requests SET {cols} WHERE id=?", vals + [request_id])
        self.conn.commit()

    def set_state(self, request_id: int, state: RequestState, *, error_message: str | None = None,
                  flag_reason: str | None = None) -> None:
        fields: dict = {"state": state}
        if error_message is not None:
            fields["error_message"] = error_message
        if flag_reason is not None:
            fields["flag_reason"] = flag_reason
        self.update_request(request_id, **fields)

    def reset_inflight(self) -> int:
        marks = ",".join("?" * len(INFLIGHT_STATES))
        cur = self.conn.execute(
            f"UPDATE requests SET state=?, updated_at=? WHERE state IN ({marks})",
            [str(RequestState.QUEUED), _now()] + [str(s) for s in INFLIGHT_STATES])
        self.conn.commit()
        return cur.rowcount

    # ---- candidates -----------------------------------------------------
    def add_candidates(self, request_id: int, candidates: list[Candidate]) -> list[Candidate]:
        out = []
        for c in candidates:
            cur = self.conn.execute(
                "INSERT INTO candidates (request_id, rank, source, source_ref, artist, title, mix_name, "
                "duration_s, deezer_id, isrc, score, catalog_track_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (request_id, c.rank, c.source, c.source_ref, c.artist, c.title, c.mix_name,
                 c.duration_s, c.deezer_id, c.isrc, c.score, c.catalog_track_id))
            out.append(Candidate(**{**c.__dict__, "id": int(cur.lastrowid), "request_id": request_id}))
        self.conn.commit()
        return out

    def _row_to_candidate(self, r: sqlite3.Row) -> Candidate:
        return Candidate(**dict(r))

    def get_candidates(self, request_id: int) -> list[Candidate]:
        rows = self.conn.execute("SELECT * FROM candidates WHERE request_id=? ORDER BY rank", (request_id,))
        return [self._row_to_candidate(r) for r in rows]

    def get_candidate(self, candidate_id: int) -> Candidate:
        r = self.conn.execute("SELECT * FROM candidates WHERE id=?", (candidate_id,)).fetchone()
        if r is None:
            raise KeyError(candidate_id)
        return self._row_to_candidate(r)

    # ---- catalog --------------------------------------------------------
    def upsert_catalog_track(self, t: CatalogTrack) -> None:
        self.conn.execute(
            "INSERT INTO catalog_tracks (id, isrc, artist, title, mix_name, label, genre, sub_genre, "
            "catalog_number, release_name, release_date, bpm, key, duration_ms, artwork_url, fetched_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET isrc=excluded.isrc, "
            "artist=excluded.artist, title=excluded.title, mix_name=excluded.mix_name, label=excluded.label, "
            "genre=excluded.genre, sub_genre=excluded.sub_genre, catalog_number=excluded.catalog_number, "
            "release_name=excluded.release_name, release_date=excluded.release_date, bpm=excluded.bpm, "
            "key=excluded.key, duration_ms=excluded.duration_ms, artwork_url=excluded.artwork_url, "
            "fetched_at=excluded.fetched_at",
            (t.id, t.isrc, t.artist, t.title, t.mix_name, t.label, t.genre, t.sub_genre, t.catalog_number,
             t.release_name, t.release_date, t.bpm, t.key, t.duration_ms, t.artwork_url, _now()))
        self.conn.commit()

    def get_catalog_track(self, track_id: int) -> CatalogTrack | None:
        r = self.conn.execute("SELECT * FROM catalog_tracks WHERE id=?", (track_id,)).fetchone()
        if r is None:
            return None
        d = dict(r)
        d.pop("fetched_at")
        return CatalogTrack(**d)

    # ---- tracks ---------------------------------------------------------
    def add_track(self, *, path: Path, fmt: str, bitrate_kbps: int, cutoff_hz: int, file_size: int,
                  artist: str, title: str, mix_name: str, duration_s: int | None, isrc: str | None,
                  catalog_track_id: int | None, request_id: int | None,
                  spectrogram_path: Path | None = None) -> int:
        now = _now()
        cur = self.conn.execute(
            "INSERT INTO tracks (path, fmt, bitrate_kbps, cutoff_hz, file_size, artist, title, mix_name, "
            "duration_s, isrc, catalog_track_id, request_id, added_at, verified_at, spectrogram_path, "
            "artist_norm, title_norm, mix_norm) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (str(path), fmt, bitrate_kbps, cutoff_hz, file_size, artist, title, mix_name, duration_s, isrc,
             catalog_track_id, request_id, now, now, str(spectrogram_path) if spectrogram_path else None,
             norm(artist), norm(title), norm(mix_name)))
        self.conn.commit()
        return int(cur.lastrowid)

    def _row_to_track(self, r: sqlite3.Row) -> Track:
        d = dict(r)
        for k in ("artist_norm", "title_norm", "mix_norm"):
            d.pop(k)
        d["path"] = Path(d["path"])
        d["spectrogram_path"] = Path(d["spectrogram_path"]) if d.get("spectrogram_path") else None
        return Track(**d)

    def get_track(self, track_id: int) -> Track:
        r = self.conn.execute("SELECT * FROM tracks WHERE id=?", (track_id,)).fetchone()
        if r is None:
            raise KeyError(track_id)
        return self._row_to_track(r)

    def list_tracks(self, search: str | None = None, limit: int = 500) -> list[Track]:
        if search:
            like = f"%{norm(search)}%"
            rows = self.conn.execute(
                "SELECT * FROM tracks WHERE artist_norm LIKE ? OR title_norm LIKE ? OR mix_norm LIKE ? "
                "ORDER BY id DESC LIMIT ?", (like, like, like, limit)).fetchall()
        else:
            rows = self.conn.execute("SELECT * FROM tracks ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [self._row_to_track(r) for r in rows]

    def find_track_by_isrc(self, isrc: str) -> Track | None:
        r = self.conn.execute("SELECT * FROM tracks WHERE isrc=? LIMIT 1", (isrc,)).fetchone()
        return None if r is None else self._row_to_track(r)

    def find_track_by_meta(self, artist: str, title: str, mix_name: str, duration_s: int | None,
                           tolerance_s: int = 3) -> Track | None:
        rows = self.conn.execute(
            "SELECT * FROM tracks WHERE artist_norm=? AND title_norm=? AND mix_norm=?",
            (norm(artist), norm(title), norm(mix_name))).fetchall()
        for r in rows:
            if duration_s is None or r["duration_s"] is None or abs(r["duration_s"] - duration_s) <= tolerance_s:
                return self._row_to_track(r)
        return None

    def find_track_by_path(self, path: Path) -> Track | None:
        r = self.conn.execute("SELECT * FROM tracks WHERE path=?", (str(path),)).fetchone()
        return None if r is None else self._row_to_track(r)

    # ---- playlists ------------------------------------------------------
    def upsert_playlist(self, source_url: str, name: str) -> int:
        self.conn.execute(
            "INSERT INTO playlists (source_url, name, created_at, updated_at) VALUES (?,?,?,?) "
            "ON CONFLICT(source_url) DO UPDATE SET name=excluded.name, updated_at=excluded.updated_at",
            (source_url, name, _now(), _now()))
        self.conn.commit()
        return int(self.conn.execute("SELECT id FROM playlists WHERE source_url=?", (source_url,)).fetchone()[0])

    def get_playlist(self, playlist_id: int) -> Playlist:
        r = self.conn.execute("SELECT * FROM playlists WHERE id=?", (playlist_id,)).fetchone()
        if r is None:
            raise KeyError(playlist_id)
        ids = [row[0] for row in self.conn.execute(
            "SELECT track_id FROM playlist_tracks WHERE playlist_id=? ORDER BY position", (playlist_id,))]
        return Playlist(**dict(r), track_ids=ids)

    def list_playlists(self) -> list[Playlist]:
        ids = [r[0] for r in self.conn.execute("SELECT id FROM playlists ORDER BY id")]
        return [self.get_playlist(i) for i in ids]

    def add_playlist_track(self, playlist_id: int, track_id: int, position: int) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO playlist_tracks (playlist_id, track_id, position) VALUES (?,?,?)",
            (playlist_id, track_id, position))
        self.conn.execute("UPDATE playlists SET updated_at=? WHERE id=?", (_now(), playlist_id))
        self.conn.commit()

    # ---- rejections -----------------------------------------------------
    def add_rejection(self, request_id: int, reason: str, bitrate_kbps: int | None, cutoff_hz: int | None,
                      spectrogram_path: Path | None) -> int:
        cur = self.conn.execute(
            "INSERT INTO rejections (request_id, reason, bitrate_kbps, cutoff_hz, spectrogram_path, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (request_id, reason, bitrate_kbps, cutoff_hz,
             None if spectrogram_path is None else str(spectrogram_path), _now()))
        self.conn.commit()
        return int(cur.lastrowid)

    def get_rejection(self, rejection_id: int) -> Rejection:
        r = self.conn.execute("SELECT * FROM rejections WHERE id=?", (rejection_id,)).fetchone()
        if r is None:
            raise KeyError(rejection_id)
        return Rejection(**dict(r))

    def list_rejections(self, limit: int = 200) -> list[Rejection]:
        rows = self.conn.execute("SELECT * FROM rejections ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [Rejection(**dict(r)) for r in rows]

    # ---- stats / settings ----------------------------------------------
    def stats(self) -> dict:
        by_state = {r[0]: r[1] for r in self.conn.execute(
            "SELECT state, COUNT(*) FROM requests GROUP BY state")}
        tracks = self.conn.execute("SELECT COUNT(*), COALESCE(SUM(file_size),0) FROM tracks").fetchone()
        rejections = self.conn.execute("SELECT COUNT(*) FROM rejections").fetchone()[0]
        by_genre = {r[0]: r[1] for r in self.conn.execute(
            "SELECT c.genre, COUNT(*) FROM tracks t JOIN catalog_tracks c ON c.id=t.catalog_track_id "
            "GROUP BY c.genre ORDER BY 2 DESC")}
        by_label = {r[0]: r[1] for r in self.conn.execute(
            "SELECT c.label, COUNT(*) FROM tracks t JOIN catalog_tracks c ON c.id=t.catalog_track_id "
            "GROUP BY c.label ORDER BY 2 DESC")}
        per_week = [(r[0], r[1]) for r in self.conn.execute(
            "SELECT strftime('%Y-W%W', created_at), COUNT(*) FROM requests GROUP BY 1 ORDER BY 1")]
        return {"requests_by_state": by_state, "tracks": tracks[0], "bytes": tracks[1],
                "rejections": rejections, "by_genre": by_genre, "by_label": by_label,
                "requests_per_week": per_week}

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        r = self.conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return default if r is None else r[0]

    def set_setting(self, key: str, value: str) -> None:
        self.conn.execute("INSERT INTO settings (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET "
                          "value=excluded.value", (key, value))
        self.conn.commit()
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_store.py -v`
Expected: 11 PASS

- [ ] **Step 5: Commit**

```bash
git add src/flackey/store.py tests/test_store.py
git commit -m "feat(store): sqlite schema and repository"
```

---

### Task 3: Identify inputs (classification, text parsing, YouTube expansion)

**Files:**
- Create: `src/flackey/identify.py`, `tests/test_identify.py`

**Interfaces:**
- Consumes: `models.Query`, `models.RequestKind`
- Produces:
  - `classify(text: str) -> tuple[RequestKind, str | None]` returns kind and the cleaned URL (None for text)
  - `parse_version(title: str) -> tuple[str, str | None]` strips a version from a title: `"Into the Void (Vini Vici Remix)"` -> `("Into the Void", "Vini Vici Remix")`
  - `parse_text(raw: str) -> Query` handles `Artist - Title`, `Artist – Title`, and bare text
  - `parse_youtube_title(title: str, uploader: str | None = None, duration_s: int | None = None) -> Query` removes YouTube noise, splits artist/title
  - `@dataclass YouTubeEntry(url: str, title: str, uploader: str | None, duration_s: int | None)`
  - `async fetch_youtube(url: str) -> tuple[str, list[YouTubeEntry]]` returns `(playlist_or_video_title, entries)`; a single video yields one entry. Runs `yt-dlp --dump-single-json --flat-playlist --no-warnings <url>` via `asyncio.create_subprocess_exec`. Raises `YouTubeError(str)` on failure.
  - `parse_ytdlp_json(data: dict) -> tuple[str, list[YouTubeEntry]]` pure.

- [ ] **Step 1: Write the failing tests**

`tests/test_identify.py`:

```python
import pytest

from flackey.identify import (
    classify, parse_text, parse_version, parse_youtube_title, parse_ytdlp_json,
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
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_identify.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write identify.py**

```python
from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse, urlunparse, urlencode

from .models import Query, RequestKind

_URL_RE = re.compile(r"https?://[^\s]+")
_NOISE_RE = re.compile(
    r"\s*[\(\[\|]?\s*\b(official\s*(music\s*)?(video|audio|visualizer|lyric\s*video)?|hd|hq|4k|"
    r"lyrics?|visualizer|full\s*track|free\s*download|out\s*now|premiere)\b\s*[\)\]]?\s*",
    re.IGNORECASE,
)  # the \b anchors matter: without them "Lyrical Assassin" loses its "Lyric"
_VERSION_WORDS = re.compile(
    r"\b(remix|mix|edit|version|dub|rework|bootleg|remaster(ed)?|live|instrumental|acapella|"
    r"extended|radio|club|vip|original)\b", re.IGNORECASE)
_TRAIL_RE = re.compile(r"\s*[\(\[]([^\)\]]+)[\)\]]\s*$")
_DASH_VERSION_RE = re.compile(r"\s+[-–—]\s+([^-–—]+)$")


class YouTubeError(Exception):
    pass


@dataclass
class YouTubeEntry:
    url: str
    title: str
    uploader: str | None
    duration_s: int | None


def _clean_url(url: str) -> str:
    p = urlparse(url)
    q = parse_qs(p.query)
    keep = {k: v for k, v in q.items() if k in ("v", "list")}
    return urlunparse((p.scheme, p.netloc, p.path, "", urlencode(keep, doseq=True), ""))


def classify(text: str) -> tuple[RequestKind, str | None]:
    m = _URL_RE.search(text)
    if not m:
        return RequestKind.TEXT, None
    url = m.group(0).rstrip(".,;)")
    p = urlparse(url)
    host = p.netloc.lower()
    if not any(h in host for h in ("youtube.com", "youtu.be")):
        return RequestKind.TEXT, None
    q = parse_qs(p.query)
    lid = q.get("list", [""])[0]
    if lid and not lid.startswith("RD"):  # RD… lists are YouTube's autoplay "mixes", not playlists
        # one canonical URL per playlist, whatever track it was shared from
        return RequestKind.YT_PLAYLIST, f"https://www.youtube.com/playlist?list={lid}"
    if "youtu.be" in host:
        vid = p.path.strip("/")
        return RequestKind.YT_TRACK, f"https://www.youtube.com/watch?v={vid}"
    return RequestKind.YT_TRACK, _clean_url(url)


def parse_version(title: str) -> tuple[str, str | None]:
    t = title.strip()
    m = _TRAIL_RE.search(t)
    if m and _VERSION_WORDS.search(m.group(1)):
        return t[: m.start()].strip(), m.group(1).strip()
    m = _DASH_VERSION_RE.search(t)
    if m and _VERSION_WORDS.search(m.group(1)):
        return t[: m.start()].strip(), m.group(1).strip()
    return t, None


def _strip_noise(s: str) -> str:
    s = re.sub(r"\s*\|.*$", "", s)  # "Title | Official Audio"
    prev = None
    while prev != s:
        prev = s
        s = _NOISE_RE.sub(" ", s)
    s = re.sub(r"\(\s*\)|\[\s*\]", "", s)
    return " ".join(s.split()).strip(" -–—")


def _split_artist_title(s: str) -> tuple[str | None, str]:
    parts = re.split(r"\s+[-–—]\s+", s, maxsplit=1)
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip()
    return None, s.strip()


def parse_text(raw: str) -> Query:
    artist, rest = _split_artist_title(raw.strip())
    if artist is None:
        return Query(raw=raw.strip())
    title, version = parse_version(rest)
    return Query(raw=raw.strip(), artist=artist, title=title, version=version)


_FEAT_RE = re.compile(r"\b(feat|ft|featuring)\b\.?", re.IGNORECASE)


def _strip_label_tag(s: str, uploader: str | None) -> str:
    """Drop a trailing [Label Name] / (Channel Name) that is neither a version nor a feature credit."""
    m = _TRAIL_RE.search(s)
    if not m:
        return s
    inner = m.group(1)
    channel = re.sub(r"\s*-\s*Topic$", "", uploader or "").strip().lower()
    if _VERSION_WORDS.search(inner) or _FEAT_RE.search(inner):
        return s
    if inner.strip().lower() == channel or m.group(0).lstrip().startswith("[") or "records" in inner.lower():
        return s[: m.start()].strip()
    return s


def parse_youtube_title(title: str, uploader: str | None = None, duration_s: int | None = None) -> Query:
    cleaned = _strip_label_tag(_strip_noise(title), uploader)
    artist, rest = _split_artist_title(cleaned)
    if artist is None and uploader:
        artist = re.sub(r"\s*-\s*Topic$", "", uploader).strip() or None
    t, version = parse_version(rest)
    return Query(raw=title, artist=artist, title=t, version=version, duration_s=duration_s)


def parse_ytdlp_json(data: dict) -> tuple[str, list[YouTubeEntry]]:
    def entry(e: dict) -> YouTubeEntry:
        url = e.get("url") or e.get("webpage_url") or f"https://www.youtube.com/watch?v={e['id']}"
        if url.startswith("http") and "youtube.com/watch" not in url and "youtu.be" not in url and e.get("id"):
            url = f"https://www.youtube.com/watch?v={e['id']}"
        d = e.get("duration")
        return YouTubeEntry(url=url, title=e.get("title") or "", uploader=e.get("uploader") or e.get("channel"),
                            duration_s=None if d is None else int(round(d)))

    if data.get("_type") == "playlist":
        return data.get("title") or "Playlist", [entry(e) for e in data.get("entries") or [] if e]
    return data.get("title") or "", [entry(data)]


async def fetch_youtube(url: str) -> tuple[str, list[YouTubeEntry]]:
    proc = await asyncio.create_subprocess_exec(
        "yt-dlp", "--dump-single-json", "--flat-playlist", "--no-warnings", url,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, err = await proc.communicate()
    if proc.returncode != 0:
        raise YouTubeError(err.decode(errors="replace").strip()[-500:] or "yt-dlp failed")
    try:
        return parse_ytdlp_json(json.loads(out))
    except (json.JSONDecodeError, KeyError) as e:
        raise YouTubeError(f"unexpected yt-dlp output: {e}") from e
```

- [ ] **Step 4: Run tests, fix regex edge cases until green**

Run: `uv run pytest tests/test_identify.py -v`
Expected: all PASS. The `(feat. Someone)` case must stay in the title because "feat" is not a version word.

- [ ] **Step 5: Commit**

```bash
git add src/flackey/identify.py tests/test_identify.py
git commit -m "feat(identify): classify inputs, parse titles, expand YouTube playlists"
```

---

### Task 4: Beatport catalog

**Files:**
- Create: `src/flackey/catalog.py`, `tests/test_catalog.py`
- Uses fixture: `tests/fixtures/beatport_search_astral.html` (already committed; 12 real track records embedded in a `__NEXT_DATA__` script tag)

**Interfaces:**
- Consumes: `models.CatalogTrack`, `models.Query`
- Produces:
  - `parse_search_html(html: str) -> list[CatalogTrack]` pure; raises `CatalogParseError` if the `__NEXT_DATA__` block is missing.
  - `class CatalogError(Exception)`, `class CatalogParseError(CatalogError)`, `class CatalogUnavailable(CatalogError)` (network/403/5xx).
  - `class BeatportCatalog` with `async search(query: Query) -> list[CatalogTrack]` (runs the blocking curl_cffi call in a thread via `asyncio.to_thread`), `search_url(text: str) -> str`, and `query_text(query: Query) -> str` (the text actually searched: artist + title without the version when both are known, else `query.search_text()`).
  - `def best_match(query: Query, tracks: list[CatalogTrack]) -> CatalogTrack | None` picks by fuzzy artist+title, respecting a requested version and preferring Original Mix otherwise. Returns None when nothing scores above 70.

- [ ] **Step 1: Write the failing tests**

`tests/test_catalog.py`:

```python
from pathlib import Path

import pytest

from flackey.catalog import BeatportCatalog, CatalogParseError, best_match, parse_search_html
from flackey.models import Query


@pytest.fixture
def tracks(fixtures: Path):
    return parse_search_html((fixtures / "beatport_search_astral.html").read_text())


def test_parse_search_html_extracts_full_records(tracks):
    assert len(tracks) == 12
    t = tracks[0]
    assert t.id == 16552105
    assert (t.artist, t.title, t.mix_name) == ("Astral Projection", "Into the Void", "Original Mix")
    assert (t.label, t.genre, t.sub_genre) == ("Sacred Technology", "Psy-Trance", "Goa Trance")
    assert (t.bpm, t.key, t.isrc) == (142, "A Major", "UKU932231081")
    assert t.release_date == "2022-06-03" and t.year == "2022"
    assert t.duration_ms == 442816 and t.duration_s == 443
    assert t.catalog_number == "SACTEC169"
    assert t.artwork_url.endswith(".jpg") and "1400x1400" in t.artwork_url


def test_parse_search_html_joins_multiple_artists():
    html = ('<script id="__NEXT_DATA__" type="application/json">{"props":{"pageProps":{"dehydratedState":'
            '{"queries":[{"state":{"data":{"data":[{"track_id":1,"track_name":"T","mix_name":"Original Mix",'
            '"artists":[{"artist_name":"A"},{"artist_name":"B"}],"label":{"label_name":"L"},'
            '"genre":[{"genre_name":"G"}],"bpm":140,"key_name":"A Minor","length":1000}]}}}]}}}}</script>')
    t = parse_search_html(html)[0]
    assert t.artist == "A, B" and t.sub_genre is None and t.isrc is None


def test_parse_search_html_without_next_data_raises():
    with pytest.raises(CatalogParseError):
        parse_search_html("<html>Just a moment...</html>")


def test_query_text_drops_version_only_when_artist_and_title_known():
    q = Query(raw="", artist="Astral Projection", title="Into the Void", version="Vini Vici Remix")
    assert BeatportCatalog.query_text(q) == "Astral Projection Into the Void"
    q = Query(raw="Into The Void (Vini Vici Remix)", artist=None, title="Into The Void", version="Vini Vici Remix")
    assert BeatportCatalog.query_text(q) == "Into The Void (Vini Vici Remix)"
    assert BeatportCatalog.query_text(Query(raw="free text")) == "free text"


def test_search_url_encodes_query():
    assert BeatportCatalog.search_url("astral projection into the void") == \
        "https://www.beatport.com/search/tracks?q=astral%20projection%20into%20the%20void"


def test_best_match_prefers_original_mix_and_exact_title(tracks):
    q = Query(raw="", artist="Astral Projection", title="Into the Void")
    m = best_match(q, tracks)
    assert m.id == 16552105  # the 2022 Sacred Technology original, not the 89 BPM re-release


def test_best_match_honours_requested_version(tracks):
    q = Query(raw="", artist="Axtral", title="Astral Projection", version="Kulage Remix")
    assert best_match(q, tracks).mix_name == "Kulage Remix"


def test_best_match_returns_none_for_unrelated(tracks):
    assert best_match(Query(raw="", artist="Daft Punk", title="One More Time"), tracks) is None


def test_best_match_uses_raw_text_when_unstructured(tracks):
    assert best_match(Query(raw="astral projection into the void"), tracks).id == 16552105
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_catalog.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write catalog.py**

```python
from __future__ import annotations

import asyncio
import json
import re
from urllib.parse import quote

from rapidfuzz import fuzz

from .models import CatalogTrack, Query

_NEXT_RE = re.compile(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S)
BROWSER = "chrome"


class CatalogError(Exception):
    pass


class CatalogParseError(CatalogError):
    pass


class CatalogUnavailable(CatalogError):
    pass


def _walk_tracks(obj):
    if isinstance(obj, dict):
        if "track_id" in obj and "track_name" in obj and "bpm" in obj:
            yield obj
            return
        for v in obj.values():
            yield from _walk_tracks(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk_tracks(v)


def _record(d: dict) -> CatalogTrack:
    genres = d.get("genre") or []
    genre = genres[0].get("genre_name") if genres else "Unknown"
    sub = (d.get("sub_genre") or {}).get("sub_genre_name")
    release = d.get("release") or {}
    art = release.get("release_image_dynamic_uri")
    if art:
        art = art.replace("{w}", "1400").replace("{h}", "1400")
    date = d.get("publish_date") or d.get("release_date")
    return CatalogTrack(
        id=int(d["track_id"]),
        artist=", ".join(a.get("artist_name", "") for a in d.get("artists") or []) or "Unknown",
        title=d.get("track_name") or "",
        mix_name=d.get("mix_name") or "Original Mix",
        label=(d.get("label") or {}).get("label_name") or "Unknown",
        genre=genre,
        isrc=d.get("isrc") or None,
        sub_genre=sub,
        catalog_number=d.get("catalog_number"),
        release_name=release.get("release_name"),
        release_date=date[:10] if date else None,
        bpm=int(d["bpm"]) if d.get("bpm") else None,
        key=d.get("key_name"),
        duration_ms=int(d["length"]) if d.get("length") else None,
        artwork_url=art,
    )


def parse_search_html(html: str) -> list[CatalogTrack]:
    m = _NEXT_RE.search(html)
    if not m:
        raise CatalogParseError("no __NEXT_DATA__ block (blocked or redesigned page)")
    data = json.loads(m.group(1))
    seen: set[int] = set()
    out: list[CatalogTrack] = []
    for d in _walk_tracks(data):
        t = _record(d)
        if t.id not in seen:
            seen.add(t.id)
            out.append(t)
    return out


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", s.lower()).strip()


def _is_original(mix: str) -> bool:
    return "original" in mix.lower()


def best_match(query: Query, tracks: list[CatalogTrack]) -> CatalogTrack | None:
    if not tracks:
        return None
    want_version = _norm(query.version) if query.version else None
    scored: list[tuple[float, CatalogTrack]] = []
    for t in tracks:
        if want_version:
            if fuzz.token_set_ratio(want_version, _norm(t.mix_name)) < 80:
                continue
        if query.artist and query.title:
            a = fuzz.token_set_ratio(_norm(query.artist), _norm(t.artist))
            ti = fuzz.token_set_ratio(_norm(query.title), _norm(t.title))
            s = 0.5 * a + 0.5 * ti
        else:
            # token_sort, not token_set: token_set scores 100 for any *subset* of tokens, so
            # "The Void - Into the Void" ties the real record for "astral projection into the void"
            s = fuzz.token_sort_ratio(_norm(query.raw), _norm(f"{t.artist} {t.title}"))
        if not want_version and not _is_original(t.mix_name):
            s -= 25
        scored.append((s, t))
    if not scored:
        return None
    scored.sort(key=lambda x: (-x[0], x[1].id))
    s, t = scored[0]
    return t if s >= 70 else None


class BeatportCatalog:
    BASE = "https://www.beatport.com/search/tracks?q="

    @staticmethod
    def search_url(text: str) -> str:
        return BeatportCatalog.BASE + quote(text)

    def _get(self, url: str) -> str:
        from curl_cffi import requests

        r = requests.get(url, impersonate=BROWSER, timeout=30)
        if r.status_code in (403, 429, 503) or r.status_code >= 500:
            raise CatalogUnavailable(f"beatport http {r.status_code}")
        return r.text

    @staticmethod
    def query_text(query: Query) -> str:
        if query.version and query.artist and query.title:
            return f"{query.artist} {query.title}"
        return query.search_text()

    async def search(self, query: Query) -> list[CatalogTrack]:
        try:
            html = await asyncio.to_thread(self._get, self.search_url(self.query_text(query)))
        except CatalogUnavailable:
            raise
        except Exception as e:  # network errors
            raise CatalogUnavailable(str(e)) from e
        return parse_search_html(html)
```

Note on the search text: when a version is requested we search artist + title only and let `best_match` filter by version, because Beatport's search ranks poorly with remixer names appended.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_catalog.py -v`
Expected: 9 PASS. If `test_best_match_prefers_original_mix_and_exact_title` picks id 16552105's 89 BPM sibling, the tie-break on `id` is wrong: both are "Original Mix" with the same artist/title, so the scores tie and the lower id (16552105, older) must win. Verify the sort key.

- [ ] **Step 5: Commit**

```bash
git add src/flackey/catalog.py tests/test_catalog.py
git commit -m "feat(catalog): beatport search page parser and best-match selection"
```

---

### Task 5: Deezer public API client

**Files:**
- Create: `src/flackey/deezer.py`, `tests/test_deezer.py`

**Interfaces:**
- Produces:
  - `@dataclass DeezerTrack(id: int, isrc: str | None, title: str, artist: str, album: str | None, duration_s: int, release_date: str | None, title_version: str | None)`
  - `parse_track_json(data: dict) -> DeezerTrack` pure; raises `DeezerError` if `data` has an `error` key.
  - `class DeezerApi(client: httpx.AsyncClient | None = None)` with `async track(track_id: int) -> DeezerTrack`; GET `https://api.deezer.com/track/{id}`; raises `DeezerError` on HTTP or API error.

- [ ] **Step 1: Write the failing tests**

`tests/test_deezer.py`:

```python
import httpx
import pytest
import respx

from flackey.deezer import DeezerApi, DeezerError, parse_track_json

SAMPLE = {"id": 1754956977, "isrc": "UKU932231081", "title": "Into the Void", "title_short": "Into the Void",
          "title_version": "", "duration": 442, "release_date": "2022-06-03",
          "artist": {"name": "Astral Projection"}, "album": {"title": "Into the Void"}}


def test_parse_track_json():
    t = parse_track_json(SAMPLE)
    assert (t.id, t.isrc, t.artist, t.title, t.duration_s) == (
        1754956977, "UKU932231081", "Astral Projection", "Into the Void", 442)
    assert t.album == "Into the Void" and t.release_date == "2022-06-03" and t.title_version is None


def test_parse_track_json_keeps_version():
    t = parse_track_json({**SAMPLE, "title": "Into the Void (Vini Vici Remix)", "title_version": "(Vini Vici Remix)"})
    assert t.title_version == "Vini Vici Remix"


def test_parse_track_json_error():
    with pytest.raises(DeezerError):
        parse_track_json({"error": {"type": "DataException", "message": "no data"}})


@respx.mock
async def test_track_fetches_public_api():
    respx.get("https://api.deezer.com/track/1754956977").mock(return_value=httpx.Response(200, json=SAMPLE))
    async with httpx.AsyncClient() as c:
        t = await DeezerApi(c).track(1754956977)
    assert t.isrc == "UKU932231081"


@respx.mock
async def test_track_http_error():
    respx.get("https://api.deezer.com/track/1").mock(return_value=httpx.Response(500))
    async with httpx.AsyncClient() as c:
        with pytest.raises(DeezerError):
            await DeezerApi(c).track(1)
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_deezer.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write deezer.py**

```python
from __future__ import annotations

from dataclasses import dataclass

import httpx


class DeezerError(Exception):
    pass


@dataclass
class DeezerTrack:
    id: int
    isrc: str | None
    title: str
    artist: str
    album: str | None
    duration_s: int
    release_date: str | None
    title_version: str | None


def parse_track_json(data: dict) -> DeezerTrack:
    if "error" in data:
        raise DeezerError(str(data["error"]))
    version = (data.get("title_version") or "").strip().strip("()").strip() or None
    title = data.get("title_short") or data.get("title") or ""
    return DeezerTrack(
        id=int(data["id"]),
        isrc=data.get("isrc") or None,
        title=title,
        artist=(data.get("artist") or {}).get("name") or "",
        album=(data.get("album") or {}).get("title"),
        duration_s=int(data.get("duration") or 0),
        release_date=data.get("release_date"),
        title_version=version,
    )


class DeezerApi:
    BASE = "https://api.deezer.com"

    def __init__(self, client: httpx.AsyncClient | None = None):
        self._client = client

    async def track(self, track_id: int) -> DeezerTrack:
        client = self._client or httpx.AsyncClient(timeout=20)
        try:
            r = await client.get(f"{self.BASE}/track/{track_id}")
        except httpx.HTTPError as e:
            raise DeezerError(str(e)) from e
        finally:
            if self._client is None:
                await client.aclose()
        if r.status_code != 200:
            raise DeezerError(f"deezer http {r.status_code}")
        return parse_track_json(r.json())
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_deezer.py -v`
Expected: 5 PASS

- [ ] **Step 5: Commit**

```bash
git add src/flackey/deezer.py tests/test_deezer.py
git commit -m "feat(deezer): public api client for isrc and duration"
```

---

### Task 6: Confidence scoring and decision

**Files:**
- Create: `src/flackey/match.py`, `tests/test_match.py`

**Interfaces:**
- Consumes: `models.Query`, `models.Candidate`, `models.CatalogTrack`, `identify.parse_version`
- Produces:
  - `THRESHOLD = 80`
  - `@dataclass Score(total: int, artist: int, title: int, version: int, duration: int, isrc: bool, notes: list[str])`
  - `score_candidate(query: Query, cand: Candidate, catalog: CatalogTrack | None) -> Score`
  - `@dataclass Decision(auto: bool, chosen: Candidate | None, reason: str)`
  - `decide(query: Query, cands: list[Candidate], catalog: CatalogTrack | None) -> Decision` mutates each `cand.score`; `chosen` is the top-scoring eligible candidate (also when parked, so the UI can preselect it).
  - `candidate_version(cand: Candidate) -> str` returns `cand.mix_name` or a version parsed from the title, or `"Original Mix"`.

Scoring, exactly as the spec's table: artist 25, title 25, version 20, duration 30; ISRC equality with the catalog overrides to 100. Duration uses the catalog's duration when there is a catalog track, else the query's YouTube duration, else full marks with a note.

- [ ] **Step 1: Write the failing tests**

`tests/test_match.py`:

```python
from flackey.match import THRESHOLD, candidate_version, decide, score_candidate
from flackey.models import Candidate, CatalogTrack, Query

CT = CatalogTrack(id=16552105, isrc="UKU932231081", artist="Astral Projection", title="Into the Void",
                  mix_name="Original Mix", label="L", genre="G", duration_ms=442816)
Q = Query(raw="astral projection into the void", artist="Astral Projection", title="Into the Void")


def cand(**kw) -> Candidate:
    base = dict(source="deezer_bot", source_ref="dz_track:1:send", artist="Astral Projection",
                title="Into the Void", duration_s=442, rank=1)
    return Candidate(**{**base, **kw})


def test_exact_original_scores_high():
    s = score_candidate(Q, cand(), CT)
    assert s.total >= 95 and s.version == 20 and s.duration == 30


def test_isrc_match_is_certain():
    s = score_candidate(Q, cand(isrc="UKU932231081", duration_s=300), CT)
    assert s.total == 100 and s.isrc


def test_remix_without_requested_version_loses_version_points():
    remix = cand(title="Into the Void (Vini Vici Remix)", mix_name="Vini Vici Remix")
    s = score_candidate(Q, remix, CT)
    # artist 25 + title 25 + version 0 + duration 30 = exactly 80: the threshold alone would let it through,
    # so `decide` must park a non-original top pick whenever no version was requested.
    assert s.version == 0 and s.total == THRESHOLD
    d = decide(Q, [remix], CT)
    assert not d.auto and "version" in d.reason.lower()


def test_decide_picks_lower_ranked_original_over_remix():
    remix = cand(mix_name="Vini Vici Remix", rank=1)                           # 80: full duration, no version points
    original = cand(source_ref="dz_track:2:send", duration_s=453, rank=2)       # 82: 10 s off, 12 duration points
    d = decide(Q, [remix, original], CT)
    assert d.auto and d.chosen.source_ref == "dz_track:2:send"


def test_requested_version_must_match():
    q = Query(raw="", artist="Astral Projection", title="Into the Void", version="Vini Vici Remix")
    assert score_candidate(q, cand(mix_name="Vini Vici Remix"), None).version == 20
    assert score_candidate(q, cand(), None).version == 0


def test_duration_decays_linearly():
    assert score_candidate(Q, cand(duration_s=443), CT).duration == 30
    assert score_candidate(Q, cand(duration_s=450), CT).duration < 30
    assert score_candidate(Q, cand(duration_s=470), CT).duration == 0


def test_duration_uses_youtube_length_without_catalog():
    q = Query(raw="", artist="Astral Projection", title="Into the Void", duration_s=442)
    assert score_candidate(q, cand(duration_s=442), None).duration == 30
    assert score_candidate(q, cand(duration_s=600), None).duration == 0


def test_raw_query_without_catalog_penalises_partial_token_matches():
    q = Query(raw="astral projection into the void")
    full = score_candidate(q, cand(), None)
    subset = score_candidate(q, cand(artist="The Void", title="Into the Void"), None)
    assert full.artist + full.title == 50 and subset.artist + subset.title < 35


def test_candidate_version_falls_back_to_title():
    assert candidate_version(cand()) == "Original Mix"
    assert candidate_version(cand(title="Into the Void (Radio Edit)")) == "Radio Edit"
    assert candidate_version(cand(mix_name="Extended Mix")) == "Extended Mix"


def test_decide_auto_when_confident():
    d = decide(Q, [cand(), cand(source_ref="dz_track:2:send", title="Into the Void (Live)", rank=2)], CT)
    assert d.auto and d.chosen.source_ref == "dz_track:1:send"


def test_decide_parks_when_below_threshold():
    d = decide(Q, [cand(artist="Someone Else", title="Something", duration_s=100)], CT)
    assert not d.auto and "below" in d.reason and d.chosen is not None


def test_decide_parks_without_catalog():
    d = decide(Q, [cand()], None)
    assert not d.auto and "Beatport" in d.reason


def test_decide_parks_when_only_remixes_and_no_version_requested():
    d = decide(Q, [cand(mix_name="Vini Vici Remix"), cand(mix_name="Radio Edit", rank=2)], CT)
    assert not d.auto and "version" in d.reason.lower()


def test_decide_excludes_wrong_versions_when_version_requested():
    q = Query(raw="", artist="Astral Projection", title="Into the Void", version="Vini Vici Remix")
    d = decide(q, [cand(), cand(mix_name="Vini Vici Remix", source_ref="dz_track:2:send", rank=2)], CT)
    assert d.chosen.source_ref == "dz_track:2:send"


def test_decide_no_candidates():
    d = decide(Q, [], CT)
    assert not d.auto and d.chosen is None
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_match.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write match.py**

```python
from __future__ import annotations

import re
from dataclasses import dataclass, field

from rapidfuzz import fuzz

from .identify import parse_version
from .models import Candidate, CatalogTrack, Query

THRESHOLD = 80
W_ARTIST, W_TITLE, W_VERSION, W_DURATION = 25, 25, 20, 30


@dataclass
class Score:
    total: int
    artist: int
    title: int
    version: int
    duration: int
    isrc: bool
    notes: list[str] = field(default_factory=list)


@dataclass
class Decision:
    auto: bool
    chosen: Candidate | None
    reason: str


def _norm(s: str | None) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", (s or "").lower()).strip()


def _is_original(v: str) -> bool:
    return "original" in v.lower()


def candidate_version(cand: Candidate) -> str:
    if cand.mix_name:
        return cand.mix_name
    _, v = parse_version(cand.title)
    return v or "Original Mix"


def _candidate_title(cand: Candidate) -> str:
    t, _ = parse_version(cand.title)
    return t


def _version_points(query: Query, cand: Candidate) -> int:
    cv = _norm(candidate_version(cand))
    if query.version:
        return W_VERSION if fuzz.token_set_ratio(_norm(query.version), cv) >= 80 else 0
    return W_VERSION if _is_original(cv) else 0


def _duration_points(cand_s: int | None, ref_s: int | None, notes: list[str]) -> int:
    if cand_s is None or ref_s is None:
        notes.append("duration unknown")
        return W_DURATION
    diff = abs(cand_s - ref_s)
    if diff <= 2:
        return W_DURATION
    if diff >= 15:
        return 0
    return round(W_DURATION * (15 - diff) / 13)


def score_candidate(query: Query, cand: Candidate, catalog: CatalogTrack | None) -> Score:
    notes: list[str] = []
    ref_artist = catalog.artist if catalog else (query.artist or "")
    ref_title = catalog.title if catalog else (query.title or "")
    if not ref_artist and not ref_title:
        ref_title = query.raw
        # token_sort: a candidate whose tokens are a subset of the query must not score 100 (see catalog.best_match)
        a = fuzz.token_sort_ratio(_norm(query.raw), _norm(f"{cand.artist} {cand.title}"))
        artist_pts = round(W_ARTIST * a / 100)
        title_pts = round(W_TITLE * a / 100)
    else:
        artist_pts = round(W_ARTIST * fuzz.token_set_ratio(_norm(ref_artist), _norm(cand.artist)) / 100)
        title_pts = round(W_TITLE * fuzz.token_set_ratio(_norm(ref_title), _norm(_candidate_title(cand))) / 100)
    version_pts = _version_points(query, cand)
    ref_dur = catalog.duration_s if catalog else query.duration_s
    duration_pts = _duration_points(cand.duration_s, ref_dur, notes)
    isrc = bool(catalog and catalog.isrc and cand.isrc and catalog.isrc == cand.isrc)
    total = 100 if isrc else artist_pts + title_pts + version_pts + duration_pts
    return Score(total=total, artist=artist_pts, title=title_pts, version=version_pts,
                 duration=duration_pts, isrc=isrc, notes=notes)


def decide(query: Query, cands: list[Candidate], catalog: CatalogTrack | None) -> Decision:
    if not cands:
        return Decision(False, None, "no candidates from source")
    eligible: list[Candidate] = []
    for c in cands:
        c.score = score_candidate(query, c, catalog).total
        if query.version:
            if fuzz.token_set_ratio(_norm(query.version), _norm(candidate_version(c))) >= 80:
                eligible.append(c)
        else:
            eligible.append(c)
    if not eligible:
        top = max(cands, key=lambda c: c.score or 0)
        return Decision(False, top, f"no candidate matches requested version '{query.version}'")
    if query.version:
        eligible.sort(key=lambda c: (-(c.score or 0), c.rank))
    else:  # originals win ties; the bot's own order breaks the rest
        eligible.sort(key=lambda c: (-(c.score or 0), 0 if _is_original(candidate_version(c)) else 1, c.rank))
    top = eligible[0]
    if not query.version and not any(_is_original(candidate_version(c)) for c in eligible):
        return Decision(False, top, "only remixes/edits available; no version was requested")
    if not query.version and not _is_original(candidate_version(top)):
        return Decision(False, top, f"best match is a {candidate_version(top)}; no version was requested")
    if catalog is None:
        return Decision(False, top, "not found on Beatport; information cannot be verified")
    if (top.score or 0) < THRESHOLD:
        return Decision(False, top, f"confidence {top.score}% below {THRESHOLD}%")
    return Decision(True, top, f"confidence {top.score}%")
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_match.py -v`
Expected: 15 PASS

- [ ] **Step 5: Commit**

```bash
git add src/flackey/match.py tests/test_match.py
git commit -m "feat(match): confidence scoring and auto/park decision"
```

---

### Task 7: Quality verification (bitrate + spectral cutoff + spectrogram)

**Files:**
- Create: `src/flackey/verify.py`, `tests/test_verify.py`

**Interfaces:**
- Consumes: `models.Verdict`
- Produces:
  - `@dataclass Probe(fmt: str, bitrate_kbps: int, duration_s: float, sample_rate: int)`
  - `probe(path: Path) -> Probe` via ffprobe JSON (`-show_format -show_streams`); `fmt` is `mp3`, `flac`, `wav`, `aiff`, `aac`, `opus`, or the codec name.
  - `spectral_cutoff_hz(path: Path, window_s: int = 60, duration_s: float | None = None) -> int` decodes a mono 44.1 kHz window from the middle of the file with ffmpeg to raw PCM, computes an averaged power spectrum with numpy (4096-sample Hann frames, 250 Hz bands), and looks for an **encoder lowpass cliff**: the highest place above 8 kHz where the level drops by ≥ 20 dB across two adjacent bands (500 Hz). It returns that frequency rounded to 50 Hz, or `22050` (Nyquist) when there is no cliff, which is what genuine lossless audio looks like. `duration_s`, when given, saves a second ffprobe call.
  - `spectrogram_png(path: Path, out: Path, window_s: int = 60, duration_s: float | None = None) -> Path` via ffmpeg `showspectrumpic=s=1200x400:legend=1:scale=log:color=intensity` over the same middle window.
  - `verify(path: Path, spectrogram_dir: Path, name: str | None = None) -> Verdict` applying the Global Constraints floor: MP3 needs bitrate ≥ 320 and cutoff ≥ 18 000; flac/wav/aiff need cutoff ≥ 20 000; other formats fail with reason `unsupported format` and no spectrogram. The PNG is written to `spectrogram_dir / f"{name or path.stem}.png"`; the worker passes `name=f"req{request_id}-{path.stem}"` so retries of the same track never overwrite an earlier rejection's picture.
  - `MIN_MP3_BITRATE = 320`, `MIN_MP3_CUTOFF = 18_000`, `MIN_LOSSLESS_CUTOFF = 20_000`, `CLIFF_DB = 20`.

Why a cliff detector and not "12 dB above the noise floor": the floor-based version measures the floor in the 21 to 22 kHz band, which for genuine lossless audio *is* the signal, so it returns 0 for every real FLAC; and a peak-relative margin fails the other way on real music, whose natural roll-off puts 20 kHz 40 to 60 dB under the midrange. An encoder lowpass is different from a natural roll-off in one robust way: it is a wall, tens of dB within a few hundred hertz. Measured with this exact algorithm on 2026-09-03 (60 s window, 250 Hz bands):

| file | cutoff |
|---|---|
| white or pink noise, WAV/FLAC | 22 050 |
| white/pink noise, LAME 320 | 20 250 |
| white/pink noise, LAME 128 then 320 ("fake 320") | 16 750 / 17 000 |
| Astral Projection – Into the Void, genuine Deezer 320 | 20 250 |
| same track re-encoded at 256 / 192 / 128 | 19 500 / 18 750 / 16 750 |
| same track 128 → 320, or 128 → FLAC | 16 750 |

Note for the owner, not a change to make: with `MIN_MP3_CUTOFF = 18_000` a 192 kbps source upsampled to 320 (18 750 Hz) passes the cutoff test. Raising the floor to 19 000 would catch it and still pass every genuine LAME 320 we measured; that is a spec decision, so leave the constant and mention it in the live-run report.

Test fixtures are generated at test time with ffmpeg, so no binary files are committed: white noise (flat spectrum) and pink noise (music-like 1/f roll-off), each as a genuine 320 kbps MP3 (cutoff ≈ 20 kHz), a fake 320 made by encoding at 128 kbps then re-encoding at 320 (cutoff ≈ 17 kHz), and a FLAC.

- [ ] **Step 1: Write the failing tests**

`tests/test_verify.py`:

```python
import subprocess
from pathlib import Path

import pytest

from flackey.verify import probe, spectral_cutoff_hz, spectrogram_png, verify
from tests.conftest import requires_ffmpeg

pytestmark = requires_ffmpeg


def _ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args], check=True)


def _noise(dir_: Path, color: str) -> Path:
    p = dir_ / f"{color}.wav"
    _ffmpeg("-f", "lavfi", "-i", f"anoisesrc=color={color}:seed=1:sample_rate=44100:duration=20",
            "-ac", "2", str(p))
    return p


def _encode(wav: Path, kbps: int, name: str) -> Path:
    p = wav.with_name(name)
    _ffmpeg("-i", str(wav), "-c:a", "libmp3lame", "-b:a", f"{kbps}k", str(p))
    return p


@pytest.fixture(scope="module")
def audio_dir(tmp_path_factory) -> Path:
    return tmp_path_factory.mktemp("audio")


@pytest.fixture(scope="module")
def noise_wav(audio_dir: Path) -> Path:
    return _noise(audio_dir, "white")


@pytest.fixture(scope="module")
def pink_wav(audio_dir: Path) -> Path:
    return _noise(audio_dir, "pink")  # 1/f roll-off: the closest synthetic stand-in for music


@pytest.fixture(scope="module")
def real320(noise_wav: Path) -> Path:
    return _encode(noise_wav, 320, "real320.mp3")


@pytest.fixture(scope="module")
def fake320(noise_wav: Path) -> Path:
    low = _encode(noise_wav, 128, "low128.mp3")
    return _encode(low, 320, "fake320.mp3")


@pytest.fixture(scope="module")
def flac(noise_wav: Path) -> Path:
    p = noise_wav.with_name("x.flac")
    _ffmpeg("-i", str(noise_wav), "-c:a", "flac", str(p))
    return p


@pytest.fixture(scope="module")
def pink_flac(pink_wav: Path) -> Path:
    p = pink_wav.with_name("pink.flac")
    _ffmpeg("-i", str(pink_wav), "-c:a", "flac", str(p))
    return p


@pytest.fixture(scope="module")
def pink_fake_flac(pink_wav: Path) -> Path:
    low = _encode(pink_wav, 128, "pink128.mp3")
    p = pink_wav.with_name("pink_fake.flac")
    _ffmpeg("-i", str(low), "-c:a", "flac", str(p))
    return p


def test_probe_mp3(real320: Path):
    pr = probe(real320)
    assert pr.fmt == "mp3" and pr.bitrate_kbps == 320 and 19 < pr.duration_s < 21 and pr.sample_rate == 44100


def test_probe_flac(flac: Path):
    assert probe(flac).fmt == "flac"


def test_cutoff_distinguishes_real_from_fake(real320: Path, fake320: Path, flac: Path):
    assert spectral_cutoff_hz(real320) >= 19_000
    assert spectral_cutoff_hz(fake320) <= 17_500
    assert spectral_cutoff_hz(flac) >= 20_500


def test_cutoff_on_music_like_spectrum(pink_wav: Path, pink_flac: Path, pink_fake_flac: Path):
    # a natural roll-off is not a cliff: full-band pink noise must read as Nyquist, not as "cut at 1 kHz"
    assert spectral_cutoff_hz(pink_flac) == 22_050
    assert spectral_cutoff_hz(_encode(pink_wav, 320, "pink320.mp3")) >= 19_000
    assert spectral_cutoff_hz(pink_fake_flac) <= 17_500


def test_spectrogram_png_written(real320: Path, tmp_path: Path):
    out = spectrogram_png(real320, tmp_path / "s.png")
    assert out.exists() and out.stat().st_size > 10_000


def test_verify_verdicts(real320: Path, fake320: Path, flac: Path, pink_fake_flac: Path, tmp_path: Path):
    ok = verify(real320, tmp_path)
    assert ok.passed and ok.fmt == "mp3" and ok.bitrate_kbps == 320 and ok.spectrogram_path.exists()
    bad = verify(fake320, tmp_path)
    assert not bad.passed and "cutoff" in bad.reason and bad.spectrogram_path.exists()
    assert verify(flac, tmp_path).passed
    assert not verify(pink_fake_flac, tmp_path).passed


def test_verify_names_spectrogram(real320: Path, tmp_path: Path):
    assert verify(real320, tmp_path, name="req7-123").spectrogram_path == tmp_path / "req7-123.png"


def test_verify_rejects_low_bitrate(noise_wav: Path, tmp_path: Path):
    low = tmp_path / "low.mp3"
    _ffmpeg("-i", str(noise_wav), "-c:a", "libmp3lame", "-b:a", "192k", str(low))
    v = verify(low, tmp_path)
    assert not v.passed and "bitrate" in v.reason


def test_verify_unsupported_format_skips_spectrogram(noise_wav: Path, tmp_path: Path):
    opus = tmp_path / "x.opus"
    _ffmpeg("-i", str(noise_wav), "-c:a", "libopus", "-b:a", "128k", str(opus))
    v = verify(opus, tmp_path)
    assert not v.passed and "unsupported" in v.reason and v.spectrogram_path is None
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_verify.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write verify.py**

```python
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .models import Verdict

MIN_MP3_BITRATE = 320
MIN_MP3_CUTOFF = 18_000
MIN_LOSSLESS_CUTOFF = 20_000
LOSSLESS = {"flac", "wav", "aiff"}
SR = 44_100
FRAME = 4096
BAND_HZ = 250
CLIFF_DB = 20        # an encoder lowpass drops at least this much within 500 Hz; music never does
CLIFF_SEARCH_FROM_HZ = 8_000


class VerifyError(Exception):
    pass


@dataclass
class Probe:
    fmt: str
    bitrate_kbps: int
    duration_s: float
    sample_rate: int


def _run(cmd: list[str]) -> bytes:
    p = subprocess.run(cmd, capture_output=True)
    if p.returncode != 0:
        raise VerifyError(p.stderr.decode(errors="replace")[-400:])
    return p.stdout


def probe(path: Path) -> Probe:
    out = _run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)])
    data = json.loads(out)
    audio = next((s for s in data.get("streams", []) if s.get("codec_type") == "audio"), None)
    if audio is None:
        raise VerifyError("no audio stream")
    codec = audio.get("codec_name", "")
    fmt = {"pcm_s16le": "wav", "pcm_s24le": "wav", "pcm_s16be": "aiff", "pcm_s24be": "aiff"}.get(codec, codec)
    if fmt == "wav" and str(path).lower().endswith((".aif", ".aiff")):
        fmt = "aiff"
    bitrate = int(audio.get("bit_rate") or data["format"].get("bit_rate") or 0)
    return Probe(fmt=fmt, bitrate_kbps=round(bitrate / 1000), duration_s=float(data["format"].get("duration", 0)),
                 sample_rate=int(audio.get("sample_rate", 0)))


def _window(path: Path, window_s: int, duration_s: float | None) -> tuple[float, float]:
    dur = probe(path).duration_s if duration_s is None else duration_s
    if dur <= window_s:
        return 0.0, dur
    return (dur - window_s) / 2, float(window_s)


def band_levels_db(path: Path, window_s: int = 60, duration_s: float | None = None) -> tuple[np.ndarray, np.ndarray]:
    """Average power per 250 Hz band over the middle window. Returns (band start Hz, level dB)."""
    start, length = _window(path, window_s, duration_s)
    pcm = _run(["ffmpeg", "-v", "error", "-ss", f"{start:.2f}", "-t", f"{length:.2f}", "-i", str(path),
                "-ac", "1", "-ar", str(SR), "-f", "s16le", "-"])
    x = np.frombuffer(pcm, dtype=np.int16).astype(np.float64) / 32768.0
    n = (len(x) // FRAME) * FRAME
    if n == 0:
        raise VerifyError("audio too short to analyze")
    frames = x[:n].reshape(-1, FRAME) * np.hanning(FRAME)
    power = np.mean(np.abs(np.fft.rfft(frames, axis=1)) ** 2, axis=0)
    db = 10 * np.log10(power + 1e-20)
    freqs = np.fft.rfftfreq(FRAME, 1 / SR)
    edges = np.arange(0, SR / 2 + BAND_HZ, BAND_HZ)
    levels = np.array([db[(freqs >= lo) & (freqs < hi)].mean() for lo, hi in zip(edges[:-1], edges[1:])])
    return edges[:-1], levels


def spectral_cutoff_hz(path: Path, window_s: int = 60, duration_s: float | None = None) -> int:
    """Frequency of the encoder lowpass cliff, or Nyquist (22050) when the content reaches the top.

    A lossy encoder zeroes everything above its lowpass, so the level falls by tens of dB within
    a few hundred hertz. Natural music rolls off gradually, a few dB per band, and never makes such
    a step. We take the *highest* cliff so a notch lower in the spectrum cannot masquerade as the edge.
    """
    starts, levels = band_levels_db(path, window_s, duration_s)
    region = starts >= CLIFF_SEARCH_FROM_HZ
    hz, db = starts[region], levels[region]
    drops = db[:-2] - db[2:]                      # level lost across two adjacent bands (500 Hz)
    cliffs = np.where(drops >= CLIFF_DB)[0]
    if len(cliffs) == 0:
        return SR // 2
    i = int(cliffs[-1])
    return int(round(hz[i + 1] / 50) * 50)


def spectrogram_png(path: Path, out: Path, window_s: int = 60, duration_s: float | None = None) -> Path:
    start, length = _window(path, window_s, duration_s)
    out.parent.mkdir(parents=True, exist_ok=True)
    _run(["ffmpeg", "-v", "error", "-y", "-ss", f"{start:.2f}", "-t", f"{length:.2f}", "-i", str(path),
          "-filter_complex", "[0:a]showspectrumpic=s=1200x400:legend=1:scale=log:color=intensity[o]",
          "-map", "[o]", "-frames:v", "1", str(out)])
    return out


def verify(path: Path, spectrogram_dir: Path, name: str | None = None) -> Verdict:
    pr = probe(path)
    if pr.fmt != "mp3" and pr.fmt not in LOSSLESS:
        return Verdict(False, pr.fmt, pr.bitrate_kbps, 0, f"unsupported format {pr.fmt}", None)
    png = spectrogram_png(path, spectrogram_dir / f"{name or path.stem}.png", duration_s=pr.duration_s)
    if pr.fmt == "mp3" and pr.bitrate_kbps < MIN_MP3_BITRATE:
        return Verdict(False, pr.fmt, pr.bitrate_kbps, 0, f"bitrate {pr.bitrate_kbps} kbps below {MIN_MP3_BITRATE}", png)
    cutoff = spectral_cutoff_hz(path, duration_s=pr.duration_s)
    if pr.fmt == "mp3":
        if cutoff < MIN_MP3_CUTOFF:
            return Verdict(False, pr.fmt, pr.bitrate_kbps, cutoff,
                           f"cutoff {cutoff} Hz below {MIN_MP3_CUTOFF}: upsampled from a lower bitrate", png)
        return Verdict(True, pr.fmt, pr.bitrate_kbps, cutoff, f"genuine {pr.bitrate_kbps} kbps, content to {cutoff} Hz", png)
    if cutoff < MIN_LOSSLESS_CUTOFF:
        return Verdict(False, pr.fmt, pr.bitrate_kbps, cutoff,
                       f"lossless container but cutoff {cutoff} Hz: lossy source", png)
    return Verdict(True, pr.fmt, pr.bitrate_kbps, cutoff, f"lossless, content to {cutoff} Hz", png)
```

- [ ] **Step 4: Run tests and calibrate**

Run: `uv run pytest tests/test_verify.py -v`
Expected: 9 PASS. If a cutoff test fails, do not tune constants blind: dump the band levels with

```
uv run python -c "from pathlib import Path; from flackey.verify import band_levels_db; import sys
hz, db = band_levels_db(Path(sys.argv[1])); print(*(f'{int(h)}:{d:.0f}' for h, d in zip(hz, db) if h >= 14000))" <file>
```

and look at where the level falls. Expected shapes: white/pink 320 falls by ~60 dB between 20 000 and 21 000; the fakes fall by ~60 dB between 16 500 and 17 500; WAV/FLAC of either noise shows no step at all (a few dB across the whole range for pink). If the cliff is there but smaller than 20 dB the encoder build differs from the one measured; lower `CLIFF_DB` no further than 15. Keep the constants in Global Constraints unchanged.

- [ ] **Step 5: Commit**

```bash
git add src/flackey/verify.py tests/test_verify.py
git commit -m "feat(verify): bitrate probe, spectral cutoff detection, spectrogram"
```

---

### Task 8: Tagging with artwork

**Files:**
- Create: `src/flackey/tag.py`, `tests/test_tag.py`

**Interfaces:**
- Consumes: `models.CatalogTrack`, `models.Verdict`
- Produces:
  - `write_tags(path: Path, catalog: CatalogTrack, verdict: Verdict, artwork: bytes | None, artwork_mime: str = "image/jpeg") -> None` for `.mp3`, `.wav`, `.aiff`/`.aif` (ID3v2.4, via mutagen `ID3`, `WAVE`, `AIFF`) and `.flac` (Vorbis comments + picture), i.e. every format `verify` can pass. Other extensions raise `TagError`.
  - `read_tags(path: Path) -> dict[str, str]` returns a flat dict with keys `title, artist, album, albumartist, genre, label, catalognumber, date, year, isrc, bpm, key, mix, comment, has_artwork` for tests and the UI.
  - `async fetch_artwork(url: str, client: httpx.AsyncClient | None = None) -> bytes | None` GET with 20 s timeout; returns None on any failure (artwork is optional).
  - `comment_for(verdict: Verdict, catalog: CatalogTrack) -> str` = `flackey: verified 320 kbps · cutoff 19.8 kHz · beatport 16552105` (the exact spec §7 form; for lossless: `verified flac`). Task 10 reuses it so the ID3 comment and the rekordbox.xml comment are identical.

Title written is `"<Title> (<Mix Name>)"` unless the mix is an original mix, then just `"<Title>"`. Rekordbox shows the title tag verbatim, and DJs expect the remix name in it.

- [ ] **Step 1: Write the failing tests**

`tests/test_tag.py`:

```python
import subprocess
from pathlib import Path

import pytest

from flackey.models import CatalogTrack, Verdict
from flackey.tag import TagError, comment_for, read_tags, write_tags
from tests.conftest import requires_ffmpeg

pytestmark = requires_ffmpeg

CT = CatalogTrack(id=16552105, isrc="UKU932231081", artist="Astral Projection", title="Into the Void",
                  mix_name="Original Mix", label="Sacred Technology", genre="Psy-Trance", sub_genre="Goa Trance",
                  catalog_number="SACTEC169", release_name="Into the Void", release_date="2022-06-03",
                  bpm=142, key="A Major", duration_ms=442816)
V = Verdict(True, "mp3", 320, 19800, "ok")
PNG_1x1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d4944415478da63f8cfc0"
    "0000030101009a1c2b0f0000000049454e44ae426082")


def _make(tmp_path: Path, ext: str) -> Path:
    p = tmp_path / f"t.{ext}"
    codec = {"mp3": ["-c:a", "libmp3lame", "-b:a", "320k"], "flac": ["-c:a", "flac"],
             "wav": ["-c:a", "pcm_s16le"], "aiff": ["-c:a", "pcm_s16be"]}[ext]
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    "anoisesrc=duration=1:sample_rate=44100", *codec, str(p)], check=True)
    return p


@pytest.mark.parametrize("ext", ["mp3", "flac", "wav", "aiff"])
def test_write_and_read_tags(tmp_path: Path, ext: str):
    p = _make(tmp_path, ext)
    write_tags(p, CT, V, PNG_1x1, "image/png")
    t = read_tags(p)
    assert t["title"] == "Into the Void" and t["artist"] == "Astral Projection"
    assert t["album"] == "Into the Void" and t["albumartist"] == "Astral Projection"
    assert t["genre"] == "Psy-Trance" and t["label"] == "Sacred Technology"
    assert t["catalognumber"] == "SACTEC169" and t["date"] == "2022-06-03" and t["year"] == "2022"
    assert t["isrc"] == "UKU932231081" and t["bpm"] == "142" and t["key"] == "A Major"
    assert t["mix"] == "Original Mix" and t["has_artwork"] == "yes"
    assert t["comment"] == "flackey: verified 320 kbps · cutoff 19.8 kHz · beatport 16552105"


def test_remix_goes_into_title(tmp_path: Path):
    p = _make(tmp_path, "mp3")
    ct = CatalogTrack(**{**CT.__dict__, "mix_name": "Vini Vici Remix"})
    write_tags(p, ct, V, None)
    t = read_tags(p)
    assert t["title"] == "Into the Void (Vini Vici Remix)" and t["has_artwork"] == "no"


def test_missing_optional_fields(tmp_path: Path):
    p = _make(tmp_path, "mp3")
    ct = CatalogTrack(id=1, artist="A", title="T", mix_name="Original Mix", label="L", genre="G")
    write_tags(p, ct, Verdict(True, "mp3", 320, 19800, "ok"), None)
    t = read_tags(p)
    assert t["bpm"] == "" and t["key"] == "" and t["year"] == ""


def test_unsupported_extension(tmp_path: Path):
    p = tmp_path / "x.ogg"
    p.write_bytes(b"OggS")
    with pytest.raises(TagError):
        write_tags(p, CT, V, None)


def test_comment_for():
    assert comment_for(V, CT) == "flackey: verified 320 kbps · cutoff 19.8 kHz · beatport 16552105"
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_tag.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write tag.py**

```python
from __future__ import annotations

from pathlib import Path

import httpx
from mutagen.aiff import AIFF
from mutagen.flac import FLAC, Picture
from mutagen.id3 import (
    APIC, COMM, ID3, TALB, TBPM, TCON, TDRC, TIT2, TKEY, TPE1, TPE2, TPUB, TSRC, TXXX, ID3NoHeaderError,
)
from mutagen.wave import WAVE

from .models import CatalogTrack, Verdict


class TagError(Exception):
    pass


def _is_original(mix: str) -> bool:
    return "original" in mix.lower()


def display_title(catalog: CatalogTrack) -> str:
    return catalog.title if _is_original(catalog.mix_name) else f"{catalog.title} ({catalog.mix_name})"


def comment_for(verdict: Verdict, catalog: CatalogTrack) -> str:
    kind = f"verified {verdict.bitrate_kbps} kbps" if verdict.fmt == "mp3" else f"verified {verdict.fmt}"
    return f"flackey: {kind} · cutoff {verdict.cutoff_hz / 1000:.1f} kHz · beatport {catalog.id}"


def _values(catalog: CatalogTrack, verdict: Verdict) -> dict[str, str]:
    return {
        "title": display_title(catalog),
        "artist": catalog.artist,
        "album": catalog.release_name or catalog.title,
        "albumartist": catalog.artist,
        "genre": catalog.genre,
        "label": catalog.label,
        "catalognumber": catalog.catalog_number or "",
        "date": catalog.release_date or "",
        "isrc": catalog.isrc or "",
        "bpm": str(catalog.bpm) if catalog.bpm else "",
        "key": catalog.key or "",
        "mix": catalog.mix_name,
        "comment": comment_for(verdict, catalog),
    }


def _id3_target(path: Path):
    """(container, tags). MP3 saves a bare ID3 block; WAV/AIFF keep ID3 inside their chunk list (mutagen
    WAVE/AIFF), which Rekordbox reads the same way."""
    ext = path.suffix.lower()
    if ext == ".mp3":
        try:
            ID3(path).delete(path)
        except ID3NoHeaderError:
            pass
        return None, ID3()
    f = WAVE(path) if ext == ".wav" else AIFF(path)
    if f.tags is None:
        f.add_tags()
    f.tags.clear()
    return f, f.tags


def _write_id3(path: Path, v: dict[str, str], artwork: bytes | None, mime: str) -> None:
    container, tags = _id3_target(path)
    tags.add(TIT2(encoding=3, text=v["title"]))
    tags.add(TPE1(encoding=3, text=v["artist"]))
    tags.add(TALB(encoding=3, text=v["album"]))
    tags.add(TPE2(encoding=3, text=v["albumartist"]))
    tags.add(TCON(encoding=3, text=v["genre"]))
    tags.add(TPUB(encoding=3, text=v["label"]))
    if v["date"]:
        tags.add(TDRC(encoding=3, text=v["date"]))
    if v["isrc"]:
        tags.add(TSRC(encoding=3, text=v["isrc"]))
    if v["bpm"]:
        tags.add(TBPM(encoding=3, text=v["bpm"]))
    if v["key"]:
        tags.add(TKEY(encoding=3, text=v["key"]))
    if v["catalognumber"]:
        tags.add(TXXX(encoding=3, desc="CATALOGNUMBER", text=v["catalognumber"]))
    tags.add(TXXX(encoding=3, desc="MIXNAME", text=v["mix"]))
    tags.add(COMM(encoding=3, lang="eng", desc="", text=v["comment"]))
    if artwork:
        tags.add(APIC(encoding=3, mime=mime, type=3, desc="Cover", data=artwork))
    if container is None:
        tags.save(path, v2_version=4)
    else:
        container.save()


def _write_flac(path: Path, v: dict[str, str], artwork: bytes | None, mime: str) -> None:
    f = FLAC(path)
    f.delete()
    f.clear_pictures()
    mapping = {"TITLE": "title", "ARTIST": "artist", "ALBUM": "album", "ALBUMARTIST": "albumartist",
               "GENRE": "genre", "LABEL": "label", "ORGANIZATION": "label", "CATALOGNUMBER": "catalognumber",
               "DATE": "date", "ISRC": "isrc", "BPM": "bpm", "INITIALKEY": "key", "MIXNAME": "mix",
               "COMMENT": "comment"}
    for k, src in mapping.items():
        if v[src]:
            f[k] = v[src]
    if artwork:
        pic = Picture()
        pic.type = 3
        pic.mime = mime
        pic.data = artwork
        f.add_picture(pic)
    f.save()


ID3_EXTS = {".mp3", ".wav", ".aiff", ".aif"}  # every format verify() can pass except FLAC carries ID3


def write_tags(path: Path, catalog: CatalogTrack, verdict: Verdict, artwork: bytes | None,
               artwork_mime: str = "image/jpeg") -> None:
    v = _values(catalog, verdict)
    ext = path.suffix.lower()
    if ext in ID3_EXTS:
        _write_id3(path, v, artwork, artwork_mime)
    elif ext == ".flac":
        _write_flac(path, v, artwork, artwork_mime)
    else:
        raise TagError(f"unsupported extension {ext}")


def _id3_tags(path: Path):
    """The ID3 tag object for a path: read-only view for read_tags; see _id3_target for writing."""
    ext = path.suffix.lower()
    if ext == ".mp3":
        return ID3(path)
    f = WAVE(path) if ext == ".wav" else AIFF(path)
    return f.tags if f.tags is not None else ID3()


def read_tags(path: Path) -> dict[str, str]:
    ext = path.suffix.lower()
    out = {k: "" for k in ("title", "artist", "album", "albumartist", "genre", "label", "catalognumber",
                           "date", "year", "isrc", "bpm", "key", "mix", "comment")}
    if ext in ID3_EXTS:
        t = _id3_tags(path)
        def g(frame: str) -> str:
            fr = t.getall(frame)
            return str(fr[0].text[0]) if fr and fr[0].text else ""
        out.update(title=g("TIT2"), artist=g("TPE1"), album=g("TALB"), albumartist=g("TPE2"), genre=g("TCON"),
                   label=g("TPUB"), date=g("TDRC"), isrc=g("TSRC"), bpm=g("TBPM"), key=g("TKEY"))
        for fr in t.getall("TXXX"):
            if fr.desc == "CATALOGNUMBER":
                out["catalognumber"] = str(fr.text[0])
            if fr.desc == "MIXNAME":
                out["mix"] = str(fr.text[0])
        comm = t.getall("COMM")
        out["comment"] = str(comm[0].text[0]) if comm else ""
        out["has_artwork"] = "yes" if t.getall("APIC") else "no"
    elif ext == ".flac":
        f = FLAC(path)
        def g(k: str) -> str:
            return f[k][0] if k in f else ""
        out.update(title=g("TITLE"), artist=g("ARTIST"), album=g("ALBUM"), albumartist=g("ALBUMARTIST"),
                   genre=g("GENRE"), label=g("LABEL"), catalognumber=g("CATALOGNUMBER"), date=g("DATE"),
                   isrc=g("ISRC"), bpm=g("BPM"), key=g("INITIALKEY"), mix=g("MIXNAME"), comment=g("COMMENT"))
        out["has_artwork"] = "yes" if f.pictures else "no"
    else:
        raise TagError(f"unsupported extension {ext}")
    out["year"] = out["date"][:4] if out["date"] else ""
    return out


async def fetch_artwork(url: str, client: httpx.AsyncClient | None = None) -> bytes | None:
    own = client is None
    c = client or httpx.AsyncClient(timeout=20, follow_redirects=True)
    try:
        r = await c.get(url)
        if r.status_code == 200 and r.content:
            return r.content
        return None
    except httpx.HTTPError:
        return None
    finally:
        if own:
            await c.aclose()
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_tag.py -v`
Expected: 8 PASS

- [ ] **Step 5: Commit**

```bash
git add src/flackey/tag.py tests/test_tag.py
git commit -m "feat(tag): id3 and vorbis tagging with beatport metadata and artwork"
```

---

### Task 9: Library paths, filing, duplicate detection

**Files:**
- Create: `src/flackey/library.py`, `tests/test_library.py`

**Interfaces:**
- Consumes: `store.Store`, `models.CatalogTrack`, `models.Candidate`, `models.Track`
- Produces:
  - `sanitize(segment: str) -> str` replaces `/ \ : * ? " < > |` with `-`, collapses whitespace, strips leading/trailing dots and spaces, truncates to 120 chars, returns `Unknown` for empty.
  - `final_path(root: Path, catalog: CatalogTrack, ext: str) -> Path` = `root / sanitize(genre) / sanitize(label) / f"{sanitize(artist)} - {sanitize(display_title)}.{ext}"` where `display_title` is from `tag.display_title`.
  - `file_track(tmp_path: Path, dest: Path) -> Path` creates parent dirs and moves atomically (`os.replace`, falling back to `shutil.move` across filesystems). Raises `FileExistsError` if `dest` exists.
  - `find_duplicate(store: Store, catalog: CatalogTrack | None, cand: Candidate) -> Track | None` checks ISRC (catalog's or candidate's) then artist/title/mix/duration.

- [ ] **Step 1: Write the failing tests**

`tests/test_library.py`:

```python
from pathlib import Path

import pytest

from flackey.library import file_track, final_path, find_duplicate, sanitize
from flackey.models import Candidate, CatalogTrack
from flackey.store import Store

CT = CatalogTrack(id=1, isrc="UKU932231081", artist="Astral Projection", title="Into the Void",
                  mix_name="Original Mix", label="Sacred Technology", genre="Psy-Trance", duration_ms=442816)


@pytest.mark.parametrize("raw,expected", [
    ("Psy-Trance", "Psy-Trance"),
    ("AC/DC", "AC-DC"),
    ('What: "Is" This?', "What- -Is- This-"),
    ("  spaced   out  ", "spaced out"),
    ("...dots...", "dots"),
    ("", "Unknown"),
    ("x" * 200, "x" * 120),
])
def test_sanitize(raw, expected):
    assert sanitize(raw) == expected


def test_final_path_layout(tmp_path: Path):
    p = final_path(tmp_path, CT, "mp3")
    assert p == tmp_path / "Psy-Trance" / "Sacred Technology" / "Astral Projection - Into the Void.mp3"


def test_final_path_includes_remix(tmp_path: Path):
    ct = CatalogTrack(**{**CT.__dict__, "mix_name": "Vini Vici Remix"})
    assert final_path(tmp_path, ct, "flac").name == "Astral Projection - Into the Void (Vini Vici Remix).flac"


def test_file_track_moves_and_refuses_overwrite(tmp_path: Path):
    src = tmp_path / "in.mp3"
    src.write_bytes(b"x")
    dest = tmp_path / "lib" / "G" / "L" / "a.mp3"
    assert file_track(src, dest) == dest
    assert dest.read_bytes() == b"x" and not src.exists()
    src.write_bytes(b"y")
    with pytest.raises(FileExistsError):
        file_track(src, dest)


def test_find_duplicate_by_isrc_then_meta(tmp_path: Path):
    store = Store(tmp_path / "s.sqlite")
    cand = Candidate(source="deezer_bot", source_ref="r", artist="Astral Projection", title="Into the Void",
                     duration_s=442)
    assert find_duplicate(store, CT, cand) is None
    store.add_track(path=tmp_path / "a.mp3", fmt="mp3", bitrate_kbps=320, cutoff_hz=19800, file_size=1,
                    artist="Astral Projection", title="Into the Void", mix_name="Original Mix", duration_s=443,
                    isrc="UKU932231081", catalog_track_id=1, request_id=None)
    assert find_duplicate(store, CT, cand) is not None
    no_isrc = CatalogTrack(**{**CT.__dict__, "isrc": None})
    assert find_duplicate(store, no_isrc, cand) is not None  # meta match
    assert find_duplicate(store, None, Candidate(source="x", source_ref="r", artist="Other", title="Song")) is None
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_library.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write library.py**

```python
from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

from .match import candidate_version
from .models import Candidate, CatalogTrack, Track
from .store import Store
from .tag import display_title

_BAD = re.compile(r'[\\/:*?"<>|]')


def sanitize(segment: str) -> str:
    s = _BAD.sub("-", segment)
    s = " ".join(s.split()).strip(" .")
    return s[:120] or "Unknown"


def final_path(root: Path, catalog: CatalogTrack, ext: str) -> Path:
    name = f"{sanitize(catalog.artist)} - {sanitize(display_title(catalog))}.{ext.lstrip('.')}"
    return root / sanitize(catalog.genre) / sanitize(catalog.label) / name


def file_track(tmp_path: Path, dest: Path) -> Path:
    if dest.exists():
        raise FileExistsError(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.replace(tmp_path, dest)
    except OSError:
        shutil.move(str(tmp_path), str(dest))
    return dest


def find_duplicate(store: Store, catalog: CatalogTrack | None, cand: Candidate) -> Track | None:
    isrc = (catalog.isrc if catalog else None) or cand.isrc
    if isrc:
        t = store.find_track_by_isrc(isrc)
        if t:
            return t
    if catalog:
        return store.find_track_by_meta(catalog.artist, catalog.title, catalog.mix_name, catalog.duration_s)
    return store.find_track_by_meta(cand.artist, cand.title, candidate_version(cand), cand.duration_s)
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_library.py -v`
Expected: 11 PASS

- [ ] **Step 5: Commit**

```bash
git add src/flackey/library.py tests/test_library.py
git commit -m "feat(library): final paths, atomic filing, duplicate detection"
```

---

### Task 10: M3U8 playlist export

**Files:**
- Create: `src/flackey/export.py`, `tests/test_export.py`

**Interfaces:**
- Consumes: `store.Store` (`list_playlists`, `get_playlist`, `get_track`), `models.Playlist`, `models.Track`
- Produces:
  - `PLAYLIST_DIR = "Playlists"`
  - pure `playlist_filenames(playlists: list[Playlist]) -> dict[int, str]` (playlist id -> `<safe name>.m3u8`; the first playlist with a given name keeps it, later ones get ` (<id>)` appended)
  - pure `build_m3u8(playlist: Playlist, tracks: dict[int, Track]) -> str`
  - `write_playlist(store: Store, playlist_id: int, library_root: Path) -> Path` (one file, atomic replace)
  - `write_playlists(store: Store, library_root: Path) -> list[Path]` (all of them, used by `crate export`)

Why M3U8 and not `rekordbox.xml`: spec §8. Rekordbox's XML bridge refreshes an already-imported track from the XML, which would wipe the owner's hot cues and memory cues; an M3U8 import only adds tracks. Paths are absolute so Rekordbox resolves them regardless of where the playlist file is imported from; `crate export` regenerates them after a library move.

- [ ] **Step 1: Write the failing tests**

`tests/test_export.py`:

```python
from pathlib import Path

from flackey.export import PLAYLIST_DIR, build_m3u8, playlist_filenames, write_playlists
from flackey.models import Playlist, Track
from flackey.store import Store


def _track(tid: int, path: str, artist: str = "A", title: str = "T", duration_s: int | None = 442) -> Track:
    return Track(id=tid, path=Path(path), fmt="mp3", bitrate_kbps=320, cutoff_hz=20000, file_size=1,
                 artist=artist, title=title, mix_name="Original Mix", duration_s=duration_s, isrc=None,
                 catalog_track_id=None, request_id=None, added_at="2026-09-03T00:00:00+00:00")


def _playlist(pid: int, name: str, track_ids: list[int]) -> Playlist:
    return Playlist(id=pid, source_url=f"u{pid}", name=name, created_at="", updated_at="", track_ids=track_ids)


def test_build_m3u8_keeps_playlist_order_and_skips_unknown_tracks():
    tracks = {1: _track(1, "/lib/Psy-Trance/L/A - T.mp3"),
              2: _track(2, "/lib/Goa/L/B - U (Remix).mp3", "B", "U", None)}
    out = build_m3u8(_playlist(1, "Goa Set", [2, 99, 1]), tracks)
    assert out.splitlines() == [
        "#EXTM3U",
        "#PLAYLIST:Goa Set",
        "#EXTINF:-1,B - U",
        "/lib/Goa/L/B - U (Remix).mp3",
        "#EXTINF:442,A - T",
        "/lib/Psy-Trance/L/A - T.mp3",
    ]


def test_playlist_filenames_are_safe_and_unique():
    names = playlist_filenames([_playlist(1, "Goa: Set/2026?", []), _playlist(2, "Liked Music", []),
                                _playlist(3, "Liked Music", [])])
    assert names == {1: "Goa_ Set_2026_.m3u8", 2: "Liked Music.m3u8", 3: "Liked Music (3).m3u8"}


def test_write_playlists_writes_one_file_per_playlist(tmp_path: Path):
    s = Store(tmp_path / "db.sqlite")
    lib = tmp_path / "lib"
    tid = s.add_track(path=lib / "Psy-Trance" / "L" / "A - T.mp3", fmt="mp3", bitrate_kbps=320, cutoff_hz=20000,
                      file_size=1, artist="A", title="T", mix_name="Original Mix", duration_s=442, isrc=None,
                      catalog_track_id=None, request_id=None)
    pid = s.upsert_playlist("https://music.youtube.com/playlist?list=1", "Goa Set")
    s.add_playlist_track(pid, tid, 1)
    written = write_playlists(s, lib)
    assert written == [lib / PLAYLIST_DIR / "Goa Set.m3u8"]
    text = written[0].read_text(encoding="utf-8")
    assert text.startswith("#EXTM3U\n#PLAYLIST:Goa Set\n") and str(lib / "Psy-Trance" / "L" / "A - T.mp3") in text
    assert write_playlists(s, lib) == written  # idempotent, atomic replace
    assert not list((lib / PLAYLIST_DIR).glob("*.tmp"))
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_export.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write export.py**

```python
from __future__ import annotations

import re
from pathlib import Path

from .models import Playlist, Track
from .store import Store

PLAYLIST_DIR = "Playlists"
_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def playlist_filenames(playlists: list[Playlist]) -> dict[int, str]:
    names: dict[int, str] = {}
    taken: set[str] = set()
    for p in sorted(playlists, key=lambda p: p.id):
        safe = (_UNSAFE.sub("_", p.name).strip().rstrip(".") or "playlist")[:120]
        if safe in taken:
            safe = f"{safe} ({p.id})"
        taken.add(safe)
        names[p.id] = f"{safe}.m3u8"
    return names


def build_m3u8(playlist: Playlist, tracks: dict[int, Track]) -> str:
    lines = ["#EXTM3U", f"#PLAYLIST:{playlist.name}"]
    for tid in playlist.track_ids:
        t = tracks.get(tid)
        if t is None:  # deleted from the index after it was added to the playlist
            continue
        secs = -1 if t.duration_s is None else t.duration_s
        lines.append(f"#EXTINF:{secs},{t.artist} - {t.title}")
        lines.append(str(t.path))
    return "\n".join(lines) + "\n"


def _write(store: Store, playlist: Playlist, filename: str, library_root: Path) -> Path:
    out_dir = library_root / PLAYLIST_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    tracks = {}
    for tid in playlist.track_ids:
        try:
            tracks[tid] = store.get_track(tid)
        except KeyError:
            pass
    out = out_dir / filename
    tmp = out.with_name(out.name + ".tmp")
    tmp.write_text(build_m3u8(playlist, tracks), encoding="utf-8")
    tmp.replace(out)  # a reader (Rekordbox import) never sees a half-written file
    return out


def write_playlist(store: Store, playlist_id: int, library_root: Path) -> Path:
    playlists = store.list_playlists()
    names = playlist_filenames(playlists)
    playlist = store.get_playlist(playlist_id)
    return _write(store, playlist, names[playlist_id], library_root)


def write_playlists(store: Store, library_root: Path) -> list[Path]:
    playlists = store.list_playlists()
    names = playlist_filenames(playlists)
    return [_write(store, store.get_playlist(p.id), names[p.id], library_root) for p in sorted(playlists, key=lambda p: p.id)]
```

`store.get_track` raises `KeyError` for an unknown id (Task 2). `store.list_playlists()` returns playlists with `track_ids` in position order (Task 2).

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_export.py -v`
Expected: 3 PASS

- [ ] **Step 5: Commit**

```bash
git add src/flackey/export.py tests/test_export.py
git commit -m "feat(export): m3u8 playlist files for rekordbox import"
```

---

### Task 11: Source interface and @DeezerMusicBot driver

**Files:**
- Create: `src/flackey/source/__init__.py`, `src/flackey/source/base.py`, `src/flackey/source/deezer_bot.py`, `tests/test_source_parsers.py`
- Read: `docs/source-bot-protocol.md`

**Interfaces:**
- Consumes: `models.Query`, `models.Candidate`, `deezer.DeezerApi`
- Produces:
  - `source/base.py`: `class SourceError(Exception)`, `class SourceNotFound(SourceError)`, `class SourceTimeout(SourceError)`, and `class Source(Protocol)` with `name: str`, `async search(query: Query) -> list[Candidate]`, `async fetch(cand: Candidate, dest_dir: Path) -> Path`.
  - `source/deezer_bot.py`:
    - `@dataclass ButtonInfo(text: str, data: str, row: int = 0, col: int = 0)`, `@dataclass ResultMenu(candidates: list[Candidate], deezer_enabled: bool, deezer_toggle: ButtonInfo | None)`. `row`/`col` matter: the bot reuses callback data across rows (`Tracks ✅` and `Deezer ✅` are both `page:1`, see `docs/source-bot-protocol.md`), and Telethon's `click(data=...)` fires the *first* button with that data, so toggles must be clicked by position.
    - `parse_result_menu(buttons: list[list[ButtonInfo]]) -> ResultMenu` pure: result buttons are those whose `data` matches `^dz_track:(\d+):send$`; label `"<n>. <Artist> - <Title>"` is split at the first `" - "` after the number; `deezer_enabled` is True when a button text starts with `Deezer` and contains `✅`.
    - `class DeezerBotSource(client: TelegramClient, bot_username: str, deezer: DeezerApi, search_timeout: float = 30, fetch_timeout: float = 90)` implementing `Source` with `name = "deezer_bot"`; `search` sends the query text, waits for the bot's next message with buttons, toggles Deezer on if needed and waits for the edit, parses, enriches every `dz_track` candidate with `DeezerApi.track(id)` (isrc, duration, title_version -> `mix_name`, ignoring API failures per candidate), and returns at most 7 candidates ranked in menu order; raises `SourceNotFound` when no `dz_track` buttons (or when the bot answers with keyboard-less text and then nothing for 5 s); `SourceTimeout` on timeouts; `SourceUnauthorized` when Telethon raises any `UnauthorizedError` (revoked or expired session). `fetch` clicks the candidate's button (matched by `source_ref` equal to the callback data), waits for the next message from the bot carrying an audio document (anchored on the menu message with `conv.get_response(menu_msg)`, because the conversation itself sent nothing), downloads it to `dest_dir / f"{cand.deezer_id}.<ext>"` with the extension from the document's filename or mime (`audio/mpeg` -> mp3, `audio/flac` -> flac), and returns the path.

Telethon specifics: use `client.conversation(bot_username, timeout=...)` with `conv.send_message(text)` and `conv.get_response()` for the search; for the edited message after a toggle click use `conv.wait_event(events.MessageEdited(chats=bot))`; for fetch, `await msg.click(data=cand.source_ref.encode())` then `conv.get_response()` looped until a message with `.audio` or `.document` arrives or the timeout elapses. Do not download in `search`.

- [ ] **Step 1: Write the failing parser tests**

`tests/test_source_parsers.py`:

```python
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
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_source_parsers.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write the source package**

`src/flackey/source/__init__.py`:

```python
from .base import Source, SourceError, SourceNotFound, SourceTimeout, SourceUnauthorized

__all__ = ["Source", "SourceError", "SourceNotFound", "SourceTimeout", "SourceUnauthorized"]
```

`src/flackey/source/base.py`:

```python
from __future__ import annotations

from pathlib import Path
from typing import Protocol

from ..models import Candidate, Query


class SourceError(Exception):
    pass


class SourceNotFound(SourceError):
    pass


class SourceTimeout(SourceError):
    pass


class SourceUnauthorized(SourceError):
    """The owner's Telegram session was revoked or expired; nothing will work until `crate login`."""


class Source(Protocol):
    name: str

    async def search(self, query: Query) -> list[Candidate]: ...

    async def fetch(self, cand: Candidate, dest_dir: Path) -> Path: ...
```

`src/flackey/source/deezer_bot.py`:

```python
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from pathlib import Path

from telethon import TelegramClient, events
from telethon.errors import UnauthorizedError
from telethon.tl.custom import Message

from ..deezer import DeezerApi, DeezerError
from ..models import Candidate, Query
from .base import SourceNotFound, SourceTimeout, SourceUnauthorized

_TRACK_RE = re.compile(r"^dz_track:(\d+):send$")
_LABEL_RE = re.compile(r"^\d+\.\s*(.*)$")
MAX_CANDIDATES = 7
MENU_CACHE = 200          # menu messages kept for fetch(); oldest evicted first
NO_RESULT_GRACE_S = 5.0   # a text reply with no keyboard, then silence this long, means "nothing found"
EXT_BY_MIME = {"audio/mpeg": "mp3", "audio/flac": "flac", "audio/x-flac": "flac", "audio/wav": "wav"}


@dataclass
class ButtonInfo:
    text: str
    data: str
    row: int = 0
    col: int = 0


@dataclass
class ResultMenu:
    candidates: list[Candidate] = field(default_factory=list)
    deezer_enabled: bool = False
    deezer_toggle: ButtonInfo | None = None


def parse_result_menu(buttons: list[list[ButtonInfo]]) -> ResultMenu:
    menu = ResultMenu()
    rank = 0
    for ri, row in enumerate(buttons):
        for ci, b in enumerate(row):
            m = _TRACK_RE.match(b.data or "")
            if m:
                rank += 1
                label = _LABEL_RE.sub(r"\1", b.text).strip()
                artist, sep, title = label.partition(" - ")
                if not sep:
                    artist, title = "", label
                menu.candidates.append(Candidate(source="deezer_bot", source_ref=b.data, artist=artist.strip(),
                                                 title=title.strip(), deezer_id=int(m.group(1)), rank=rank))
            elif b.text.startswith("Deezer"):
                menu.deezer_toggle = ButtonInfo(b.text, b.data, ri, ci)
                menu.deezer_enabled = "✅" in b.text
    return menu


def _buttons(msg: Message) -> list[list[ButtonInfo]]:
    rows = []
    for ri, row in enumerate(msg.buttons or []):
        rows.append([ButtonInfo(b.text or "", (b.data or b"").decode(errors="replace"), ri, ci)
                     for ci, b in enumerate(row)])
    return rows


class DeezerBotSource:
    name = "deezer_bot"

    def __init__(self, client: TelegramClient, bot_username: str, deezer: DeezerApi,
                 search_timeout: float = 30, fetch_timeout: float = 90):
        self.client = client
        self.bot_username = bot_username
        self.deezer = deezer
        self.search_timeout = search_timeout
        self.fetch_timeout = fetch_timeout
        self._menus: dict[str, Message] = {}

    async def _enrich(self, c: Candidate) -> Candidate:
        try:
            t = await self.deezer.track(c.deezer_id)
        except DeezerError:
            return c
        c.isrc, c.duration_s = t.isrc, t.duration_s
        if t.artist:
            c.artist = t.artist
        if t.title:
            c.title = t.title
        c.mix_name = t.title_version
        return c

    async def search(self, query: Query) -> list[Candidate]:
        try:
            async with self.client.conversation(self.bot_username, timeout=self.search_timeout) as conv:
                await conv.send_message(query.search_text())
                msg: Message = await conv.get_response()
                while not msg.buttons:
                    # The bot's "nothing found" reply has not been captured yet (protocol doc §3). A keyboard-less
                    # text followed by silence is treated as not-found instead of waiting for the full timeout.
                    try:
                        msg = await asyncio.wait_for(conv.get_response(), timeout=NO_RESULT_GRACE_S)
                    except asyncio.TimeoutError:
                        raise SourceNotFound(f"source bot replied without results: {(msg.text or '')[:80]}") from None
                menu = parse_result_menu(_buttons(msg))
                if not menu.deezer_enabled and menu.deezer_toggle is not None:
                    # Register the edit listener *before* clicking (the edit can arrive first), then click by
                    # position, never by data: "Tracks ✅" one row up carries the same "page:1".
                    edited = conv.wait_event(events.MessageEdited(chats=await conv.get_input_chat()))
                    await msg.click(menu.deezer_toggle.row, menu.deezer_toggle.col)
                    msg = (await edited).message
                    menu = parse_result_menu(_buttons(msg))
        except UnauthorizedError as e:
            raise SourceUnauthorized(f"Telegram session rejected ({e.__class__.__name__})") from e
        except asyncio.TimeoutError as e:
            raise SourceTimeout("source bot did not answer the search") from e
        if not menu.candidates:
            raise SourceNotFound("no Deezer results")
        cands = menu.candidates[:MAX_CANDIDATES]
        for c in cands:
            self._menus[c.source_ref] = msg
        while len(self._menus) > MENU_CACHE:
            self._menus.pop(next(iter(self._menus)))
        return [await self._enrich(c) for c in cands]

    async def fetch(self, cand: Candidate, dest_dir: Path) -> Path:
        menu_msg = self._menus.get(cand.source_ref)
        if menu_msg is None:
            # menu lost (restart): re-run the search to get a fresh menu
            await self.search(Query(raw=f"{cand.artist} {cand.title}"))
            menu_msg = self._menus.get(cand.source_ref)
            if menu_msg is None:
                raise SourceNotFound("candidate no longer offered by the source bot")
        dest_dir.mkdir(parents=True, exist_ok=True)
        try:
            async with self.client.conversation(self.bot_username, timeout=self.fetch_timeout) as conv:
                await menu_msg.click(data=cand.source_ref.encode())  # track buttons have unique data
                while True:
                    # This conversation sent nothing itself, so anchor on the menu message: a bare
                    # get_response() raises "No message was sent previously". Repeated calls with the same
                    # anchor advance through the bot's replies (progress text, then the audio document).
                    msg: Message = await conv.get_response(menu_msg)
                    if msg.document is not None:
                        break
        except UnauthorizedError as e:
            raise SourceUnauthorized(f"Telegram session rejected ({e.__class__.__name__})") from e
        except asyncio.TimeoutError as e:
            raise SourceTimeout("source bot did not send the file") from e
        ext = EXT_BY_MIME.get(msg.document.mime_type or "", None)
        if ext is None and msg.file and msg.file.name and "." in msg.file.name:
            ext = msg.file.name.rsplit(".", 1)[1].lower()
        dest = dest_dir / f"{cand.deezer_id}.{ext or 'bin'}"
        await self.client.download_media(msg, file=str(dest))
        return dest
```

- [ ] **Step 4: Run parser tests**

Run: `uv run pytest tests/test_source_parsers.py -v`
Expected: 4 PASS

- [ ] **Step 5: Live smoke test (opt-in, run once now)**

Create the directory `tests/live/` (no `__init__.py`) and `tests/live/test_source_live.py` (skipped unless `FLACKEY_LIVE=1`). Because `pyproject.toml` sets `testpaths = ["tests"]`, this file is collected on every ordinary `uv run pytest` and skipped by the marker; that is intended. When it does run it sends a real message from the owner's Telegram account to the source bot and downloads about 17 MB.

```python
import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("FLACKEY_LIVE") != "1", reason="live test")


async def test_search_and_fetch_astral(tmp_path: Path):
    from telethon import TelegramClient
    from flackey.config import load_settings
    from flackey.deezer import DeezerApi
    from flackey.models import Query
    from flackey.source.deezer_bot import DeezerBotSource

    s = load_settings(Path(".env"))
    client = TelegramClient(str(s.session_path), s.telegram_api_id, s.telegram_api_hash)
    await client.connect()
    assert await client.is_user_authorized()
    src = DeezerBotSource(client, s.source_bot_username, DeezerApi())
    cands = await src.search(Query(raw="astral projection into the void"))
    hit = next((c for c in cands if c.deezer_id == 1754956977), None)  # the bot's ordering is not guaranteed
    assert hit is not None and hit.isrc == "UKU932231081"
    path = await src.fetch(hit, tmp_path)
    assert path.suffix == ".mp3" and path.stat().st_size > 15_000_000
    await client.disconnect()
```

And `tests/live/test_catalog_live.py`, the spec §10 live check for the catalog (the only thing that tells us the `__NEXT_DATA__` layout still matches the live Beatport page):

```python
import os

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("FLACKEY_LIVE") != "1", reason="live test")


async def test_beatport_search_still_parses():
    from flackey.catalog import BeatportCatalog, best_match
    from flackey.models import Query

    q = Query(raw="astral projection into the void")
    tracks = await BeatportCatalog().search(q)
    assert any(t.id == 16552105 for t in tracks)
    assert best_match(q, tracks).id == 16552105
```

Run: `FLACKEY_LIVE=1 uv run pytest tests/live -v`
Expected: 2 PASS in under 60 s. If the bot's reply shape differs from `docs/source-bot-protocol.md`, update the doc and the parser, not the test expectations.

- [ ] **Step 6: Commit**

```bash
git add src/flackey/source tests/test_source_parsers.py tests/live
git commit -m "feat(source): telethon driver for the deezer source bot"
```

---

### Task 12: Notifier protocol and the pipeline worker

**Files:**
- Create: `src/flackey/notify.py`, `src/flackey/worker.py`, `tests/test_worker.py`

**Interfaces:**
- Consumes: everything from Tasks 2 to 11.
- Produces:
  - `notify.py`: `@dataclass Button(label: str, data: str)`, `class Notifier(Protocol)` with `async send(text: str, buttons: list[Button] | None = None) -> None`; `class NullNotifier` (no-op); `class MemoryNotifier` (records `(text, buttons)` tuples in `.sent`, used by tests and the UI's event log).
  - `worker.py`: `class Worker(store: Store, source: Source, catalog: BeatportCatalog-like, notifier: Notifier, settings: Settings, artwork_fetch: Callable[[str], Awaitable[bytes | None]] = tag.fetch_artwork)`
    - `async process(request_id: int) -> Request` runs the pipeline for one request from its current state (`queued` or `awaiting_review` with `chosen_candidate_id` set) to a terminal or parked state and returns the updated request.
    - `async run_forever(poll_s: float = 2.0)` loops: `store.next_queued()` -> `process`, sleeping when idle.
    - `async choose(request_id: int, candidate_id: int) -> Request` sets `chosen_candidate_id`, moves the request to `queued`, so `run_forever` picks it up (resumes at fetch).
    - `async cancel(request_id: int) -> Request`.
    - `on_start()` calls `store.reset_inflight()`.
  - The catalog dependency is typed as a Protocol `CatalogLike` with `async search(query) -> list[CatalogTrack]` so tests use a fake.

Pipeline for `process` (spec §3.2, in the exact order the code below runs it):

1. Query: if the request has no stored `query_*` fields, `identify.parse_text(raw_text)` and persist them (YouTube requests already have them from the inbox).
2. Resume shortcut: if `chosen_candidate_id` is set (a reviewed request, or a retry after a fetch failure), load that candidate and the stored catalog track and jump to step 7.
3. `identifying`: `catalog.search` then `best_match` -> `catalog` (may be None). `CatalogUnavailable` -> `_retry_or_fail` with `flag_reason="Beatport unreachable, will retry"`. Persist the catalog track and `catalog_track_id`.
4. Source search: `SourceNotFound` -> state `not_found`, notify "Not available on Deezer: <query>". `SourceTimeout`/`SourceError` -> `_retry_or_fail`.
5. `decide`, then persist the candidates with their scores (once per request: retries re-enter through step 2 and never come back here). No chosen candidate -> `not_found`.
6. Duplicate check on the chosen candidate (`find_duplicate`) -> `_mark_duplicate`. Then, if not auto: state `awaiting_review`, `flag_reason`, `chosen_candidate_id` preselected, notify with numbered buttons `pick:<request_id>:<candidate_id>` for up to 5 candidates plus `cancel:<request_id>`, return. If auto: persist `chosen_candidate_id` too, so a fetch failure resumes at step 2.
7. `_fetch_verify_file`: duplicate check again (a reviewed request may have been parked for hours), then `fetching`: `source.fetch(candidate, settings.tmp_dir)`; errors -> `_retry_or_fail`.
8. `verifying`: `verify(path, settings.spectrogram_dir, name=f"req{id}-{stem}")`. On fail: delete the file, `add_rejection`, state `rejected`, notify "Rejected: <reason>".
9. `filing`: without a catalog track (reviewer chose a candidate that is not on Beatport) build a minimal `CatalogTrack` with a negative stable id, `label="Unknown"`, `genre="Unknown"`, persist it. `fetch_artwork` if any. `write_tags`. `final_path`; if that file already exists -> `_mark_duplicate` with the library row found by path (or none), delete tmp, return. `file_track`, `add_track`, playlist membership plus `write_playlist` for that playlist, state `done`, notify `Done: <Artist> – <Title> (<Mix>) · m:ss · <verdict> · <bpm> BPM · <key> · <genre> / <label> · <confidence>%`.

`_retry_or_fail` implements spec §9's "retried up to three times with backoff": `attempts += 1`; below `MAX_ATTEMPTS` it re-queues with `retry_after = now + RETRY_BACKOFF_S[attempts - 1]` (30 s, then 120 s) so `next_queued` leaves it alone until then; at the limit it moves to `error` and notifies.

Every exception not listed is caught at the top of `process`, logged, and moves the request to `error` with `error_message=str(e)[:500]`; the worker never dies on one request.

- [ ] **Step 1: Write the failing tests with fakes**

`tests/test_worker.py`:

```python
import asyncio
import subprocess
from pathlib import Path

import pytest

from flackey.catalog import CatalogUnavailable
from flackey.config import Settings
from flackey.models import Candidate, CatalogTrack, Query, RequestKind, RequestState
from flackey.notify import MemoryNotifier
from flackey.source import SourceNotFound, SourceTimeout, SourceUnauthorized
from flackey.store import Store
from flackey.worker import Worker
from tests.conftest import requires_ffmpeg

pytestmark = requires_ffmpeg

CT = CatalogTrack(id=16552105, isrc="UKU932231081", artist="Astral Projection", title="Into the Void",
                  mix_name="Original Mix", label="Sacred Technology", genre="Psy-Trance", release_date="2022-06-03",
                  bpm=142, key="A Major", duration_ms=442816, artwork_url="https://img/x.jpg")
# The fake source produces a 3-second file and `good_cand()` says duration_s=3. Tests that must reach
# filing therefore override CT with duration_ms=3000 (full duration points, so `decide` auto-accepts) and
# use the raw text "astral projection into the void" (so `best_match` finds CT: the text "q" scores 0).
# Tests that only exercise the park/error paths can keep "q" and the 442 s CT.
TEXT = "astral projection into the void"


def _mp3(path: Path, kbps: int = 320, seconds: int = 3) -> Path:
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i",
                    f"anoisesrc=color=white:seed=1:sample_rate=44100:duration={seconds}",
                    "-c:a", "libmp3lame", "-b:a", f"{kbps}k", str(path)], check=True)
    return path


class FakeCatalog:
    def __init__(self, tracks=None, fail=False):
        self.tracks, self.fail = tracks or [], fail

    async def search(self, query: Query):
        if self.fail:
            raise CatalogUnavailable("403")
        return self.tracks


class FakeSource:
    name = "deezer_bot"

    def __init__(self, cands=None, error=None, kbps=320, fetch_error=None):
        self.cands, self.error, self.kbps, self.fetch_error = cands or [], error, kbps, fetch_error
        self.fetched = []
        self.searches = 0

    async def search(self, query: Query):
        self.searches += 1
        if self.error:
            raise self.error
        return [Candidate(**c.__dict__) for c in self.cands]

    async def fetch(self, cand: Candidate, dest_dir: Path) -> Path:
        self.fetched.append(cand.source_ref)
        if self.fetch_error:
            raise self.fetch_error
        dest_dir.mkdir(parents=True, exist_ok=True)
        return _mp3(dest_dir / f"{cand.deezer_id}.mp3", self.kbps)


async def no_art(url: str):
    return None


def good_cand() -> Candidate:
    return Candidate(source="deezer_bot", source_ref="dz_track:1754956977:send", artist="Astral Projection",
                     title="Into the Void", duration_s=3, deezer_id=1754956977, isrc="UKU932231081", rank=1)


@pytest.fixture
def env(tmp_path: Path):
    settings = Settings(_env_file=None, telegram_api_id=1, telegram_api_hash="h", inbox_bot_token="t", owner_telegram_id=1,
                        library_root=tmp_path / "lib", data_dir=tmp_path / "data")
    store = Store(settings.db_path)
    notifier = MemoryNotifier()
    return settings, store, notifier


def make_worker(env, source, catalog):
    settings, store, notifier = env
    return Worker(store, source, catalog, notifier, settings, artwork_fetch=no_art)


async def test_happy_path_files_tags_exports(env):
    settings, store, notifier = env
    ct = CatalogTrack(**{**CT.__dict__, "duration_ms": 3000})
    w = make_worker(env, FakeSource([good_cand()]), FakeCatalog([ct]))
    rid = store.add_request(TEXT, RequestKind.TEXT)
    r = await w.process(rid)
    assert r.state == RequestState.DONE and r.confidence == 100 and r.chosen_candidate_id is not None
    track = store.get_track(r.track_id)
    assert track.path == settings.library_root / "Psy-Trance" / "Sacred Technology" / "Astral Projection - Into the Void.mp3"
    assert track.path.exists() and track.isrc == "UKU932231081"
    assert track.verified_at is not None and track.spectrogram_path.exists()
    assert not list(settings.tmp_dir.glob("*.mp3"))
    assert not (settings.library_root / "Playlists").exists()   # no playlist, no export
    assert notifier.sent[-1][0].startswith("Done: Astral Projection – Into the Void")
    assert "142 BPM" in notifier.sent[-1][0] and "A Major" in notifier.sent[-1][0]


async def test_duplicate_is_skipped(env):
    settings, store, notifier = env
    ct = CatalogTrack(**{**CT.__dict__, "duration_ms": 3000})
    src = FakeSource([good_cand()])
    w = make_worker(env, src, FakeCatalog([ct]))
    await w.process(store.add_request(TEXT, RequestKind.TEXT))
    r = await w.process(store.add_request(TEXT, RequestKind.TEXT))
    assert r.state == RequestState.DUPLICATE and r.track_id is not None and len(src.fetched) == 1
    assert notifier.sent[-1][0].startswith("Already in library")


async def test_duplicate_is_rechecked_when_review_resumes(env):
    settings, store, notifier = env
    src = FakeSource([good_cand()])
    parked = store.add_request(TEXT, RequestKind.TEXT)
    await make_worker(env, src, FakeCatalog([])).process(parked)       # parks: not on Beatport
    assert store.get_request(parked).state == RequestState.AWAITING_REVIEW
    ct = CatalogTrack(**{**CT.__dict__, "duration_ms": 3000})
    w = make_worker(env, src, FakeCatalog([ct]))
    await w.process(store.add_request(TEXT, RequestKind.TEXT))         # meanwhile the same track gets filed
    await w.choose(parked, store.get_candidates(parked)[0].id)
    r = await w.process(parked)                                        # must not fetch it a second time
    assert r.state == RequestState.DUPLICATE and len(src.fetched) == 1


async def test_low_confidence_parks_then_choice_resumes(env):
    settings, store, notifier = env
    ct = CatalogTrack(**{**CT.__dict__, "duration_ms": 3000})
    other = Candidate(source="deezer_bot", source_ref="dz_track:5:send", artist="Someone", title="Else",
                      duration_s=100, deezer_id=5, rank=1)
    w = make_worker(env, FakeSource([other]), FakeCatalog([ct]))
    rid = store.add_request(TEXT, RequestKind.TEXT)
    r = await w.process(rid)
    assert r.state == RequestState.AWAITING_REVIEW and "below" in r.flag_reason
    text, buttons = notifier.sent[-1]
    assert text.splitlines()[1].startswith("1. Someone – Else") and buttons[0].text == "1"
    assert buttons[0].data == f"pick:{rid}:{store.get_candidates(rid)[0].id}" and buttons[-1].data == f"cancel:{rid}"
    await w.choose(rid, store.get_candidates(rid)[0].id)
    assert store.get_request(rid).state == RequestState.QUEUED
    r = await w.process(rid)
    assert r.state == RequestState.DONE
    assert r.confidence == store.get_candidates(rid)[0].score  # the picked candidate's score, not the preselected one


async def test_choose_validates_candidate_and_state(env):
    settings, store, notifier = env
    w = make_worker(env, FakeSource([good_cand()]), FakeCatalog([]))
    a = store.add_request("q", RequestKind.TEXT)
    b = store.add_request("q", RequestKind.TEXT)
    await w.process(a)
    await w.process(b)
    foreign = store.get_candidates(b)[0].id
    with pytest.raises(ValueError):
        await w.choose(a, foreign)
    store.set_state(a, RequestState.DONE)
    with pytest.raises(ValueError):
        await w.choose(a, store.get_candidates(a)[0].id)


async def test_fetch_failure_backs_off_without_duplicating_candidates(env):
    settings, store, notifier = env
    ct = CatalogTrack(**{**CT.__dict__, "duration_ms": 3000})
    src = FakeSource([good_cand()], fetch_error=SourceTimeout("slow"))
    w = make_worker(env, src, FakeCatalog([ct]))
    rid = store.add_request(TEXT, RequestKind.TEXT)
    r = await w.process(rid)
    assert r.state == RequestState.QUEUED and r.attempts == 1 and r.retry_after is not None
    assert "Retrying in 30 s" in notifier.sent[-1][0]
    assert store.next_queued() is None                      # backoff: not picked up again immediately
    r = await w.process(rid)                                # forced second attempt
    assert r.attempts == 2 and src.searches == 1 and len(store.get_candidates(rid)) == 1


async def test_not_on_beatport_parks(env):
    settings, store, notifier = env
    w = make_worker(env, FakeSource([good_cand()]), FakeCatalog([]))
    r = await w.process(store.add_request("q", RequestKind.TEXT))
    assert r.state == RequestState.AWAITING_REVIEW and "Beatport" in r.flag_reason


async def test_chosen_without_beatport_files_under_unknown(env):
    settings, store, notifier = env
    w = make_worker(env, FakeSource([good_cand()]), FakeCatalog([]))
    rid = store.add_request("q", RequestKind.TEXT)
    await w.process(rid)
    await w.choose(rid, store.get_candidates(rid)[0].id)
    r = await w.process(rid)
    assert r.state == RequestState.DONE
    assert store.get_track(r.track_id).path.parent == settings.library_root / "Unknown" / "Unknown"


async def test_source_not_found(env):
    settings, store, notifier = env
    w = make_worker(env, FakeSource(error=SourceNotFound("none")), FakeCatalog([CT]))
    r = await w.process(store.add_request("q", RequestKind.TEXT))
    assert r.state == RequestState.NOT_FOUND


async def test_source_timeout_retries_then_errors(env):
    settings, store, notifier = env
    w = make_worker(env, FakeSource(error=SourceTimeout("slow")), FakeCatalog([CT]))
    rid = store.add_request("q", RequestKind.TEXT)
    assert (await w.process(rid)).state == RequestState.QUEUED
    assert (await w.process(rid)).state == RequestState.QUEUED
    r = await w.process(rid)
    assert r.state == RequestState.ERROR and r.attempts == 3


async def test_beatport_unavailable_requeues(env):
    settings, store, notifier = env
    w = make_worker(env, FakeSource([good_cand()]), FakeCatalog(fail=True))
    rid = store.add_request("q", RequestKind.TEXT)
    r = await w.process(rid)
    assert r.state == RequestState.QUEUED and r.attempts == 1 and "Beatport unreachable" in r.flag_reason


async def test_verification_failure_rejects_and_deletes(env):
    settings, store, notifier = env
    ct = CatalogTrack(**{**CT.__dict__, "duration_ms": 3000})
    w = make_worker(env, FakeSource([good_cand()], kbps=128), FakeCatalog([ct]))
    rid = store.add_request(TEXT, RequestKind.TEXT)
    r = await w.process(rid)
    assert r.state == RequestState.REJECTED
    rj = store.list_rejections()[0]
    assert "bitrate" in rj.reason and Path(rj.spectrogram_path).exists()
    assert Path(rj.spectrogram_path).name == f"req{rid}-1754956977.png"
    assert not list(settings.tmp_dir.glob("*.mp3"))
    assert notifier.sent[-1][0].startswith("Rejected")


async def test_playlist_membership(env):
    settings, store, notifier = env
    ct = CatalogTrack(**{**CT.__dict__, "duration_ms": 3000})
    w = make_worker(env, FakeSource([good_cand()]), FakeCatalog([ct]))
    pid = store.upsert_playlist("https://youtube.com/playlist?list=1", "Goa Set")
    rid = store.add_request(TEXT, RequestKind.YT_TRACK, playlist_id=pid, playlist_position=3)
    r = await w.process(rid)
    assert r.state == RequestState.DONE and store.get_playlist(pid).track_ids == [r.track_id]
    m3u = settings.library_root / "Playlists" / "Goa Set.m3u8"
    assert m3u.exists() and str(store.get_track(r.track_id).path) in m3u.read_text(encoding="utf-8")


async def test_cancel(env):
    settings, store, notifier = env
    w = make_worker(env, FakeSource([good_cand()]), FakeCatalog([]))
    rid = store.add_request("q", RequestKind.TEXT)
    await w.process(rid)
    r = await w.cancel(rid)
    assert r.state == RequestState.CANCELLED


async def test_cancel_refuses_finished_requests(env):
    settings, store, notifier = env
    w = make_worker(env, FakeSource(), FakeCatalog())
    rid = store.add_request("q", RequestKind.TEXT)
    store.set_state(rid, RequestState.DONE)
    with pytest.raises(ValueError):
        await w.cancel(rid)
    assert store.get_request(rid).state == RequestState.DONE


async def test_unauthorized_session_pauses_worker_and_keeps_request(env):
    settings, store, notifier = env
    status = {"telegram_authorized": True}
    w = Worker(store, FakeSource(error=SourceUnauthorized("session revoked")), FakeCatalog([CT]), notifier, settings,
               artwork_fetch=no_art, status=status)
    rid = store.add_request("q", RequestKind.TEXT)
    r = await w.process(rid)
    assert r.state == RequestState.QUEUED and r.attempts == 0 and "login" in r.flag_reason.lower()
    assert status["telegram_authorized"] is False and "crate login" in notifier.sent[-1][0]
    await asyncio.wait_for(w.run_forever(poll_s=0.01), timeout=1)   # returns at once: the worker is paused


async def test_on_start_resets_inflight(env):
    settings, store, notifier = env
    w = make_worker(env, FakeSource(), FakeCatalog())
    rid = store.add_request("q", RequestKind.TEXT)
    store.set_state(rid, RequestState.FETCHING)
    w.on_start()
    assert store.get_request(rid).state == RequestState.QUEUED
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_worker.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write notify.py**

```python
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class Button:
    label: str
    data: str


class Notifier(Protocol):
    async def send(self, text: str, buttons: list[Button] | None = None) -> None: ...


class NullNotifier:
    async def send(self, text: str, buttons: list[Button] | None = None) -> None:
        return None


class MemoryNotifier:
    def __init__(self) -> None:
        self.sent: list[tuple[str, list[Button] | None]] = []

    async def send(self, text: str, buttons: list[Button] | None = None) -> None:
        self.sent.append((text, buttons))
```

- [ ] **Step 4: Write worker.py**

```python
from __future__ import annotations

import asyncio
import logging
import zlib
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Protocol

from .catalog import CatalogUnavailable, best_match
from .config import Settings
from .export import write_playlist
from .identify import parse_text
from .library import file_track, final_path, find_duplicate
from .match import candidate_version, decide
from .models import Candidate, CatalogTrack, Query, Request, RequestState
from .notify import Button, Notifier
from .source import Source, SourceError, SourceNotFound, SourceTimeout, SourceUnauthorized
from .store import Store
from .tag import fetch_artwork, write_tags
from .verify import verify

log = logging.getLogger(__name__)
MAX_ATTEMPTS = 3
RETRY_BACKOFF_S = (30, 120)  # wait before attempt 2, before attempt 3
REVIEW_BUTTONS = 5
CANCELLABLE = {RequestState.QUEUED, RequestState.AWAITING_REVIEW, RequestState.ERROR}
LOGIN_REQUIRED = "Telegram login required"


class CatalogLike(Protocol):
    async def search(self, query: Query) -> list[CatalogTrack]: ...


def _mmss(seconds: int | None) -> str:
    if seconds is None:
        return "?:??"
    return f"{seconds // 60}:{seconds % 60:02d}"


def _fallback_catalog(cand: Candidate) -> CatalogTrack:
    # negative so it never collides with a Beatport id; crc32 (not hash()) so it is stable across processes
    fallback_id = -(cand.deezer_id or zlib.crc32(cand.source_ref.encode()) or 1)
    return CatalogTrack(id=fallback_id, isrc=cand.isrc,
                        artist=cand.artist, title=cand.title, mix_name=candidate_version(cand),
                        label="Unknown", genre="Unknown", duration_ms=(cand.duration_s or 0) * 1000 or None)


class Worker:
    def __init__(self, store: Store, source: Source, catalog: CatalogLike, notifier: Notifier,
                 settings: Settings,
                 artwork_fetch: Callable[[str], Awaitable[bytes | None]] = fetch_artwork,
                 status: dict | None = None):
        self.store, self.source, self.catalog, self.notifier, self.settings = store, source, catalog, notifier, settings
        self.artwork_fetch = artwork_fetch
        # shared with /api/health (Task 14): {"telegram_authorized": bool}; the worker flips it to False
        # when the Telethon session dies and then stops, leaving every request queued for the next run
        self.status = status if status is not None else {"telegram_authorized": True}

    # ---- lifecycle ------------------------------------------------------
    def on_start(self) -> None:
        n = self.store.reset_inflight()
        if n:
            log.info("re-queued %d in-flight requests", n)

    async def run_forever(self, poll_s: float = 2.0) -> None:
        self.on_start()
        while self.status.get("telegram_authorized", True):
            req = self.store.next_queued()
            if req is None:
                await asyncio.sleep(poll_s)
                continue
            await self.process(req.id)
        log.error("worker stopped: %s (run `crate login`, then restart)", LOGIN_REQUIRED)

    async def choose(self, request_id: int, candidate_id: int) -> Request:
        req = self.store.get_request(request_id)
        cand = self.store.get_candidate(candidate_id)
        if cand.request_id != request_id:
            raise ValueError(f"candidate {candidate_id} does not belong to request {request_id}")
        if req.state != RequestState.AWAITING_REVIEW:
            raise ValueError(f"request {request_id} is {req.state.value}, not awaiting review")
        self.store.update_request(request_id, chosen_candidate_id=candidate_id, state=RequestState.QUEUED,
                                  flag_reason=None, confidence=cand.score, retry_after=None)
        return self.store.get_request(request_id)

    async def cancel(self, request_id: int) -> Request:
        req = self.store.get_request(request_id)
        if req.state not in CANCELLABLE:
            raise ValueError(f"request {request_id} is {req.state.value}; only queued, awaiting-review "
                             f"or failed requests can be cancelled")
        self.store.set_state(request_id, RequestState.CANCELLED)
        return self.store.get_request(request_id)

    # ---- pipeline -------------------------------------------------------
    async def process(self, request_id: int) -> Request:
        try:
            await self._process(request_id)
        except SourceUnauthorized as e:
            # not the request's fault: keep it queued, attempts untouched, and pause the worker
            self.store.update_request(request_id, state=RequestState.QUEUED, flag_reason=LOGIN_REQUIRED)
            if self.status.get("telegram_authorized", True):
                self.status["telegram_authorized"] = False
                log.error("%s: %s", LOGIN_REQUIRED, e)
                await self.notifier.send(f"{LOGIN_REQUIRED}: {e}\nRun `crate login` and restart. "
                                         f"Requests stay queued.")
        except Exception as e:  # noqa: BLE001 - the worker must survive any single request
            log.exception("request %s failed", request_id)
            self.store.set_state(request_id, RequestState.ERROR, error_message=str(e)[:500])
            await self.notifier.send(f"Error on request {request_id}: {str(e)[:200]}")
        return self.store.get_request(request_id)

    async def _retry_or_fail(self, req: Request, reason: str, *, flag: str | None = None) -> None:
        attempts = req.attempts + 1
        if attempts < MAX_ATTEMPTS:
            wait = RETRY_BACKOFF_S[min(attempts, len(RETRY_BACKOFF_S)) - 1]
            retry_after = (datetime.now(timezone.utc) + timedelta(seconds=wait)).isoformat(timespec="seconds")
            self.store.update_request(req.id, attempts=attempts, state=RequestState.QUEUED,
                                      flag_reason=flag or reason, retry_after=retry_after)
            await self.notifier.send(f"Attempt {attempts} failed for {req.raw_text}: {reason}\nRetrying in {wait} s.")
        else:
            self.store.update_request(req.id, attempts=attempts, state=RequestState.ERROR, error_message=reason)
            await self.notifier.send(f"Gave up after {attempts} attempts: {req.raw_text}\n{reason}")

    async def _process(self, request_id: int) -> None:
        req = self.store.get_request(request_id)
        query = req.query()
        if query.artist is None and query.title is None:
            query = parse_text(req.raw_text)
            self.store.update_request(req.id, query_artist=query.artist, query_title=query.title,
                                      query_version=query.version)

        if req.chosen_candidate_id is not None:
            cand = self.store.get_candidate(req.chosen_candidate_id)
            catalog = self.store.get_catalog_track(req.catalog_track_id) if req.catalog_track_id else None
        else:
            self.store.set_state(req.id, RequestState.IDENTIFYING)
            try:
                catalog = best_match(query, await self.catalog.search(query))
            except CatalogUnavailable as e:
                await self._retry_or_fail(req, f"Beatport unreachable: {e}", flag="Beatport unreachable, will retry")
                return
            if catalog:
                self.store.upsert_catalog_track(catalog)
                self.store.update_request(req.id, catalog_track_id=catalog.id)

            try:
                cands = await self.source.search(query)
            except SourceNotFound:
                self.store.set_state(req.id, RequestState.NOT_FOUND)
                await self.notifier.send(f"Not available on Deezer: {req.raw_text}")
                return
            except SourceUnauthorized:
                raise  # handled in process(): subclass of SourceError, so it must be caught before it
            except (SourceTimeout, SourceError) as e:
                await self._retry_or_fail(req, f"source error: {e}")
                return

            decision = decide(query, cands, catalog)
            saved = self.store.add_candidates(req.id, cands)
            self.store.update_request(req.id, confidence=decision.chosen.score if decision.chosen else None)
            if decision.chosen is None:
                self.store.set_state(req.id, RequestState.NOT_FOUND)
                await self.notifier.send(f"Not available on Deezer: {req.raw_text}")
                return
            # `decide` returns one of the objects in `cands`; match by identity, not by source_ref
            # (the bot can list the same Deezer id twice)
            chosen = saved[next(i for i, c in enumerate(cands) if c is decision.chosen)]

            dup = find_duplicate(self.store, catalog, chosen)
            if dup:
                await self._mark_duplicate(req, dup.id, dup.path)
                return

            if not decision.auto:
                self.store.update_request(req.id, state=RequestState.AWAITING_REVIEW, flag_reason=decision.reason,
                                          chosen_candidate_id=chosen.id)
                await self._ask_review(req, saved, decision.reason)
                return
            # persist the choice so a fetch failure resumes here instead of searching (and saving candidates) again
            self.store.update_request(req.id, chosen_candidate_id=chosen.id)
            cand = chosen

        await self._fetch_verify_file(req, cand, catalog)

    async def _mark_duplicate(self, req: Request, track_id: int | None, path: Path) -> None:
        self.store.update_request(req.id, state=RequestState.DUPLICATE, track_id=track_id)
        if req.playlist_id is not None and track_id is not None:
            self.store.add_playlist_track(req.playlist_id, track_id, req.playlist_position or 0)
        await self.notifier.send(f"Already in library: {path}")

    async def _ask_review(self, req: Request, cands: list[Candidate], reason: str) -> None:
        lines = [f"Review needed: {req.raw_text}"]
        buttons: list[Button] = []
        # numbered 1..n in *display* order (best first); c.rank is the bot's menu order and would jump around
        for n, c in enumerate(sorted(cands, key=lambda c: (-(c.score or 0), c.rank))[:REVIEW_BUTTONS], start=1):
            lines.append(f"{n}. {c.artist} – {c.title} ({candidate_version(c)}) · {_mmss(c.duration_s)} · {c.score or 0}%")
            buttons.append(Button(str(n), f"pick:{req.id}:{c.id}"))
        lines.append(f"Reason: {reason}")
        buttons.append(Button("Cancel", f"cancel:{req.id}"))
        await self.notifier.send("\n".join(lines), buttons)

    async def _fetch_verify_file(self, req: Request, cand: Candidate, catalog: CatalogTrack | None) -> None:
        dup = find_duplicate(self.store, catalog, cand)  # again: a reviewed request may have waited for hours
        if dup:
            await self._mark_duplicate(req, dup.id, dup.path)
            return
        self.store.set_state(req.id, RequestState.FETCHING)
        try:
            tmp = await self.source.fetch(cand, self.settings.tmp_dir)
        except SourceUnauthorized:
            raise
        except (SourceTimeout, SourceError) as e:
            await self._retry_or_fail(req, f"source error: {e}")
            return
        try:
            await self._verify_and_file(req, cand, catalog, tmp)
        finally:
            tmp.unlink(missing_ok=True)  # gone already when file_track moved it; garbage in every other outcome

    async def _verify_and_file(self, req: Request, cand: Candidate, catalog: CatalogTrack | None, tmp: Path) -> None:
        self.store.set_state(req.id, RequestState.VERIFYING)
        # ffprobe plus two ffmpeg passes take seconds on a 7-minute file: keep the event loop (inbox bot, API) free
        verdict = await asyncio.to_thread(verify, tmp, self.settings.spectrogram_dir, f"req{req.id}-{tmp.stem}")
        if not verdict.passed:
            self.store.add_rejection(req.id, verdict.reason, verdict.bitrate_kbps, verdict.cutoff_hz,
                                     verdict.spectrogram_path)
            self.store.set_state(req.id, RequestState.REJECTED)
            await self.notifier.send(f"Rejected: {cand.artist} – {cand.title}\n{verdict.reason}")
            return

        self.store.set_state(req.id, RequestState.FILING)
        if catalog is None:
            catalog = _fallback_catalog(cand)
            self.store.upsert_catalog_track(catalog)
            self.store.update_request(req.id, catalog_track_id=catalog.id)
        artwork = await self.artwork_fetch(catalog.artwork_url) if catalog.artwork_url else None
        await asyncio.to_thread(write_tags, tmp, catalog, verdict, artwork)
        dest = final_path(self.settings.library_root, catalog, tmp.suffix.lstrip("."))
        if dest.exists():
            existing = self.store.find_track_by_path(dest)  # None if the file was put there by hand
            await self._mark_duplicate(req, existing.id if existing else None, dest)
            return
        file_track(tmp, dest)
        track_id = self.store.add_track(
            path=dest, fmt=verdict.fmt, bitrate_kbps=verdict.bitrate_kbps, cutoff_hz=verdict.cutoff_hz,
            file_size=dest.stat().st_size, artist=catalog.artist, title=catalog.title, mix_name=catalog.mix_name,
            duration_s=catalog.duration_s or cand.duration_s, isrc=catalog.isrc or cand.isrc,
            catalog_track_id=catalog.id, request_id=req.id, spectrogram_path=verdict.spectrogram_path)
        if req.playlist_id is not None:
            self.store.add_playlist_track(req.playlist_id, track_id, req.playlist_position or 0)
            write_playlist(self.store, req.playlist_id, self.settings.library_root)
        self.store.update_request(req.id, state=RequestState.DONE, track_id=track_id)
        conf = self.store.get_request(req.id).confidence
        parts = [f"Done: {catalog.artist} – {catalog.title} ({catalog.mix_name})", _mmss(catalog.duration_s or cand.duration_s),
                 verdict.reason]
        if catalog.bpm:
            parts.append(f"{catalog.bpm} BPM")
        if catalog.key:
            parts.append(catalog.key)
        parts.append(f"{catalog.genre} / {catalog.label}")
        if conf is not None:
            parts.append(f"{conf}%")
        await self.notifier.send(" · ".join(parts))
```

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/test_worker.py -v`
Expected: 17 PASS. `test_happy_path` depends on the fake source producing a 3 s file at 320 kbps whose spectral cutoff is ≥ 18 000 Hz; 3 s of mono white noise at 320 kbps measures 20 000 Hz with the Task 7 algorithm.

- [ ] **Step 6: Commit**

```bash
git add src/flackey/notify.py src/flackey/worker.py tests/test_worker.py
git commit -m "feat(worker): end-to-end pipeline with review, retries, duplicates"
```

---

### Task 13: Telegram inbox bot and notifier

**Files:**
- Create: `src/flackey/inbox.py`, `tests/test_inbox.py`

**Interfaces:**
- Consumes: `store.Store`, `identify.classify/parse_text/parse_youtube_title/fetch_youtube/YouTubeError`, `worker.Worker.choose/cancel`, `notify.Button`, `library.find_duplicate`
- Produces:
  - Pure, testable core: `class Inbox(store: Store, worker: Worker, owner_id: int, youtube: Callable[[str], Awaitable[tuple[str, list[YouTubeEntry]]]] = fetch_youtube)` with
    - `async handle_text(user_id: int, text: str) -> str | None` returns the reply text (None when the sender is not the owner).
    - `async handle_callback(user_id: int, data: str) -> str | None` handles `pick:<rid>:<cid>` and `cancel:<rid>`.
    - `enqueue_text(text) -> int`, `async enqueue_youtube(url, kind) -> str` (creates a playlist record for playlists, one request per entry with `query_*` filled from `parse_youtube_title` and `source_url`, skips entries already in the library by meta match and entries that already have an open request for the same URL, returns the summary reply).
  - Telegram wiring: `build_bot(settings: Settings, inbox: Inbox) -> tuple[Bot, Dispatcher]` registering an aiogram message handler and a callback-query handler that delegate to the two methods above and reply with their return value; `class TelegramNotifier(bot: Bot, owner_id: int)` implementing `Notifier.send` with an `InlineKeyboardMarkup` of one button per row.

Replies (exact strings, tests depend on them):
- unknown input: `Send me a track name, a YouTube Music track link, or a playlist link.`
- text or single link queued: `Queued #<id> (position <n>)`
- playlist: `Queued <k> of <total> from "<name>" (<dupes> already in library)`, with `, <o> already queued` appended inside the parentheses when a re-sent playlist has entries still open
- YouTube failure: `Could not read that link. yt-dlp may need an update.`
- callback pick: `Picked <Artist> – <Title>, resuming.`; cancel: `Cancelled #<id>.`; either on a request that is no longer open: `Request #<id> is no longer awaiting review.`

- [ ] **Step 1: Write the failing tests**

`tests/test_inbox.py`:

```python
from pathlib import Path

import pytest

from flackey.config import Settings
from flackey.identify import YouTubeEntry, YouTubeError
from flackey.inbox import Inbox
from flackey.models import RequestKind, RequestState
from flackey.notify import MemoryNotifier
from flackey.store import Store
from flackey.worker import Worker

OWNER = 123456789


class DummySource:
    name = "x"

    async def search(self, q):
        return []

    async def fetch(self, c, d):
        raise NotImplementedError


class DummyCatalog:
    async def search(self, q):
        return []


@pytest.fixture
def inbox(tmp_path: Path):
    settings = Settings(_env_file=None, telegram_api_id=1, telegram_api_hash="h", inbox_bot_token="t", owner_telegram_id=OWNER,
                        library_root=tmp_path / "lib", data_dir=tmp_path / "data")
    store = Store(settings.db_path)
    worker = Worker(store, DummySource(), DummyCatalog(), MemoryNotifier(), settings)

    async def fake_youtube(url: str):
        if "bad" in url:
            raise YouTubeError("boom")
        if "gone" in url:
            return "Deleted video", []
        if "list=" in url:
            return "Goa Set", [
                YouTubeEntry("https://www.youtube.com/watch?v=a", "Astral Projection - Into The Void (Official)", "AP", 442),
                YouTubeEntry("https://www.youtube.com/watch?v=b", "X - Y", "X", 300),
            ]
        return "Astral Projection - Into The Void", [
            YouTubeEntry(url, "Astral Projection - Into The Void", "Astral Projection - Topic", 442)]

    return Inbox(store, worker, OWNER, youtube=fake_youtube), store


async def test_ignores_strangers(inbox):
    ib, store = inbox
    assert await ib.handle_text(1, "hello") is None
    assert store.list_requests() == []


async def test_text_request_is_queued(inbox):
    ib, store = inbox
    reply = await ib.handle_text(OWNER, "Astral Projection - Into the Void")
    assert reply == "Queued #1 (position 1)"
    r = store.get_request(1)
    assert r.kind == RequestKind.TEXT and r.query_artist == "Astral Projection" and r.query_title == "Into the Void"


async def test_unknown_link_gets_help(inbox):
    ib, _ = inbox
    assert await ib.handle_text(OWNER, "https://open.spotify.com/track/1") == \
        "Send me a track name, a YouTube Music track link, or a playlist link."


async def test_youtube_track(inbox):
    ib, store = inbox
    reply = await ib.handle_text(OWNER, "https://music.youtube.com/watch?v=abc&si=1")
    assert reply == "Queued #1 (position 1)"
    r = store.get_request(1)
    assert r.kind == RequestKind.YT_TRACK and r.source_url == "https://music.youtube.com/watch?v=abc"
    assert (r.query_artist, r.query_title, r.query_duration_s) == ("Astral Projection", "Into The Void", 442)


async def test_youtube_playlist_expands_and_skips_duplicates(inbox, tmp_path: Path):
    ib, store = inbox
    store.add_track(path=tmp_path / "x.mp3", fmt="mp3", bitrate_kbps=320, cutoff_hz=1, file_size=1,
                    artist="X", title="Y", mix_name="Original Mix", duration_s=300, isrc=None,
                    catalog_track_id=None, request_id=None)
    reply = await ib.handle_text(OWNER, "https://music.youtube.com/playlist?list=PL1")
    assert reply == 'Queued 1 of 2 from "Goa Set" (1 already in library)'
    reqs = store.list_requests()
    assert len(reqs) == 1 and reqs[0].playlist_id == 1 and reqs[0].playlist_position == 1
    assert store.get_playlist(1).track_ids == [1]


async def test_resent_playlist_does_not_requeue_open_entries(inbox, tmp_path: Path):
    ib, store = inbox
    store.add_track(path=tmp_path / "x.mp3", fmt="mp3", bitrate_kbps=320, cutoff_hz=1, file_size=1,
                    artist="X", title="Y", mix_name="Original Mix", duration_s=300, isrc=None,
                    catalog_track_id=None, request_id=None)
    await ib.handle_text(OWNER, "https://music.youtube.com/playlist?list=PL1")
    reply = await ib.handle_text(OWNER, "https://music.youtube.com/playlist?list=PL1")
    assert reply == 'Queued 0 of 2 from "Goa Set" (1 already in library, 1 already queued)'
    assert len(store.list_requests()) == 1


async def test_youtube_failure(inbox):
    ib, _ = inbox
    assert await ib.handle_text(OWNER, "https://youtu.be/bad") == "Could not read that link. yt-dlp may need an update."
    assert await ib.handle_text(OWNER, "https://youtu.be/gone") == "Could not read that link. yt-dlp may need an update."


async def test_commands_get_help(inbox):
    ib, store = inbox
    assert await ib.handle_text(OWNER, "/start") == "Send me a track name, a YouTube Music track link, or a playlist link."
    assert store.list_requests() == []


async def test_position_counts_in_flight_requests(inbox):
    ib, store = inbox
    first = store.add_request("a", RequestKind.TEXT)
    store.set_state(first, RequestState.FETCHING)   # the worker is on it right now
    assert await ib.handle_text(OWNER, "b") == "Queued #2 (position 2)"


async def test_callbacks(inbox):
    ib, store = inbox
    from flackey.models import Candidate
    rid = store.add_request("q", RequestKind.TEXT)
    store.set_state(rid, RequestState.AWAITING_REVIEW)
    c = store.add_candidates(rid, [Candidate(source="s", source_ref="r", artist="A", title="T", rank=1)])[0]
    assert await ib.handle_callback(1, f"pick:{rid}:{c.id}") is None
    assert await ib.handle_callback(OWNER, f"pick:{rid}:{c.id}") == "Picked A – T, resuming."
    assert store.get_request(rid).state == RequestState.QUEUED
    assert await ib.handle_callback(OWNER, f"cancel:{rid}") == f"Cancelled #{rid}."
    assert store.get_request(rid).state == RequestState.CANCELLED
    assert await ib.handle_callback(OWNER, "garbage") is None


async def test_stale_callback_buttons(inbox):
    ib, store = inbox
    from flackey.models import Candidate
    rid = store.add_request("q", RequestKind.TEXT)
    store.set_state(rid, RequestState.AWAITING_REVIEW)
    c = store.add_candidates(rid, [Candidate(source="s", source_ref="r", artist="A", title="T", rank=1)])[0]
    store.set_state(rid, RequestState.DONE)   # the owner taps a button on an old review message
    assert await ib.handle_callback(OWNER, f"pick:{rid}:{c.id}") == f"Request #{rid} is no longer awaiting review."
    assert await ib.handle_callback(OWNER, f"cancel:{rid}") == f"Request #{rid} is no longer awaiting review."
    assert store.get_request(rid).state == RequestState.DONE
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_inbox.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write inbox.py**

```python
from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from aiogram import Bot, Dispatcher, F
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from .config import Settings
from .identify import YouTubeEntry, YouTubeError, classify, fetch_youtube, parse_text, parse_youtube_title
from .models import INFLIGHT_STATES, RequestKind, RequestState
from .notify import Button
from .store import Store
from .worker import Worker

log = logging.getLogger(__name__)
HELP = "Send me a track name, a YouTube Music track link, or a playlist link."
YT_FAIL = "Could not read that link. yt-dlp may need an update."


class Inbox:
    def __init__(self, store: Store, worker: Worker, owner_id: int,
                 youtube: Callable[[str], Awaitable[tuple[str, list[YouTubeEntry]]]] = fetch_youtube):
        self.store, self.worker, self.owner_id, self.youtube = store, worker, owner_id, youtube

    def _position(self, request_id: int) -> int:
        # everything ahead of this request: queued *and* currently being processed
        ahead = self.store.list_requests(INFLIGHT_STATES | {RequestState.QUEUED}, limit=100_000)
        ids = sorted(r.id for r in ahead)
        return ids.index(request_id) + 1 if request_id in ids else len(ids)

    def enqueue_text(self, text: str) -> int:
        return self.store.add_request(text, RequestKind.TEXT, query=parse_text(text))

    async def enqueue_youtube(self, url: str, kind: RequestKind) -> str:
        try:
            name, entries = await self.youtube(url)
        except YouTubeError:
            return YT_FAIL
        if not entries:  # deleted, private, or region-blocked video; empty playlist
            return YT_FAIL
        if kind == RequestKind.YT_TRACK:
            e = entries[0]
            q = parse_youtube_title(e.title, e.uploader, e.duration_s)
            rid = self.store.add_request(e.title, RequestKind.YT_TRACK, source_url=url, query=q)
            return f"Queued #{rid} (position {self._position(rid)})"
        pid = self.store.upsert_playlist(url, name)
        queued = dupes = open_ = 0
        for pos, e in enumerate(entries, start=1):
            q = parse_youtube_title(e.title, e.uploader, e.duration_s)
            existing = None
            if q.artist and q.title:
                existing = self.store.find_track_by_meta(q.artist, q.title, q.version or "Original Mix", q.duration_s)
            if existing:
                self.store.add_playlist_track(pid, existing.id, pos)
                dupes += 1
                continue
            if self.store.open_request_for_url(e.url):  # the same playlist sent twice while still in flight
                open_ += 1
                continue
            self.store.add_request(e.title, RequestKind.YT_TRACK, source_url=e.url, playlist_id=pid,
                                   playlist_position=pos, query=q)
            queued += 1
        detail = f"{dupes} already in library" + (f", {open_} already queued" if open_ else "")
        return f'Queued {queued} of {len(entries)} from "{name}" ({detail})'

    async def handle_text(self, user_id: int, text: str) -> str | None:
        if user_id != self.owner_id:
            return None
        if text.startswith("/"):  # /start, /help, anything Telegram's UI sends as a command
            return HELP
        kind, url = classify(text)
        if kind == RequestKind.TEXT:
            if "://" in text:
                return HELP
            rid = self.enqueue_text(text)
            return f"Queued #{rid} (position {self._position(rid)})"
        return await self.enqueue_youtube(url, kind)

    async def handle_callback(self, user_id: int, data: str) -> str | None:
        if user_id != self.owner_id:
            return None
        parts = data.split(":")
        try:
            ids = [int(x) for x in parts[1:]]
        except ValueError:
            return None
        if not ((parts[0] == "pick" and len(ids) == 2) or (parts[0] == "cancel" and len(ids) == 1)):
            return None
        rid = ids[0]
        try:
            if parts[0] == "pick":
                cand = self.store.get_candidate(ids[1])
                await self.worker.choose(rid, ids[1])
                return f"Picked {cand.artist} – {cand.title}, resuming."
            await self.worker.cancel(rid)
            return f"Cancelled #{rid}."
        except (ValueError, KeyError):  # stale button: the request moved on, or the ids do not exist
            return f"Request #{rid} is no longer awaiting review."


class TelegramNotifier:
    def __init__(self, bot: Bot, owner_id: int):
        self.bot, self.owner_id = bot, owner_id

    async def send(self, text: str, buttons: list[Button] | None = None) -> None:
        markup = None
        if buttons:
            markup = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=b.label, callback_data=b.data)] for b in buttons])
        try:
            await self.bot.send_message(self.owner_id, text, reply_markup=markup)
        except Exception:  # noqa: BLE001 - notifications must never break the pipeline
            log.exception("telegram notify failed")


def build_bot(settings: Settings, inbox: Inbox) -> tuple[Bot, Dispatcher]:
    bot = Bot(settings.inbox_bot_token)
    dp = Dispatcher()

    @dp.message(F.text)
    async def on_text(message: Message) -> None:
        reply = await inbox.handle_text(message.from_user.id if message.from_user else 0, message.text or "")
        if reply:
            await message.answer(reply)

    @dp.callback_query()
    async def on_callback(cb: CallbackQuery) -> None:
        reply = await inbox.handle_callback(cb.from_user.id, cb.data or "")
        await cb.answer()
        if reply and cb.message:
            await cb.message.answer(reply)

    return bot, dp
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_inbox.py -v`
Expected: 11 PASS

- [ ] **Step 5: Commit**

```bash
git add src/flackey/inbox.py tests/test_inbox.py
git commit -m "feat(inbox): telegram bot handlers, playlist expansion, review callbacks"
```

---

### Task 14: JSON API for the UI

**Files:**
- Create: `src/flackey/web.py`, `tests/test_web.py`

**Interfaces:**
- Consumes: `store.Store`, `worker.Worker.choose/cancel`, `config.Settings`
- Produces: `create_app(store: Store, worker: Worker, settings: Settings, ui_dir: Path | None = None, status: dict | None = None) -> FastAPI`. `status` is a plain dict owned by `app.py` (Task 15) that the process updates in place, e.g. `{"telegram_authorized": False}`; the API reads it on every call so the UI can show the spec §9 "login required" banner. Routes:
  - `GET /api/queue` -> `[Request]` for non-terminal states plus the 50 most recent terminal ones, newest first
  - `GET /api/requests/{id}` -> `{request, candidates, catalog}`
  - `GET /api/review` -> requests in `awaiting_review`, each with `candidates` and `catalog`
  - `POST /api/review/{id}/choose/{candidate_id}` -> updated request; 404 if either id is unknown, 400 if the candidate belongs to another request or the request is not awaiting review (`Worker.choose` raises `ValueError` for both)
  - `POST /api/review/{id}/cancel` -> updated request; 400 when the request is not queued, awaiting review or failed
  - `GET /api/rejections` -> `[Rejection]` with `request_text` added
  - `GET /api/rejections/{id}/spectrogram.png` -> the PNG file (404 if missing)
  - `GET /api/library?q=` -> `[Track]` with `catalog` attached
  - `GET /api/playlists` -> `[Playlist]`
  - `GET /api/stats` -> `store.stats()` plus `library_root` and `playlist_dir` paths
  - `GET /api/health` -> `{"ok": true, "version": "0.1.0", "telegram_authorized": true}` (the last value comes from `status`, default `true`)
  - If `ui_dir` is given and exists, mount it at `/` as static files with `html=True` (the React build from the UI plan lands there).
  - `CORSMiddleware` allowing `http://localhost:5173` and `http://127.0.0.1:5173`, so the UI plan's Vite dev server can call the API without a proxy.
  Dataclasses are serialized with a small `to_dict` helper that converts `Path` to `str` and enums to their values.

- [ ] **Step 1: Write the failing tests**

`tests/test_web.py`:

```python
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from flackey.config import Settings
from flackey.models import Candidate, CatalogTrack, RequestKind, RequestState
from flackey.notify import MemoryNotifier
from flackey.store import Store
from flackey.web import create_app
from flackey.worker import Worker


class DummySource:
    name = "x"

    async def search(self, q):
        return []

    async def fetch(self, c, d):
        raise NotImplementedError


class DummyCatalog:
    async def search(self, q):
        return []


@pytest.fixture
def client(tmp_path: Path):
    settings = Settings(_env_file=None, telegram_api_id=1, telegram_api_hash="h", inbox_bot_token="t", owner_telegram_id=1,
                        library_root=tmp_path / "lib", data_dir=tmp_path / "data")
    store = Store(settings.db_path)
    worker = Worker(store, DummySource(), DummyCatalog(), MemoryNotifier(), settings)
    app = create_app(store, worker, settings)
    return TestClient(app), store, settings


def test_health(client):
    c, _, _ = client
    assert c.get("/api/health").json() == {"ok": True, "version": "0.1.0", "telegram_authorized": True}


def test_health_reports_login_required(client):
    _, store, settings = client
    status = {"telegram_authorized": False}
    c = TestClient(create_app(store, Worker(store, DummySource(), DummyCatalog(), MemoryNotifier(), settings),
                              settings, status=status))
    assert c.get("/api/health").json()["telegram_authorized"] is False
    status["telegram_authorized"] = True
    assert c.get("/api/health").json()["telegram_authorized"] is True


def test_cors_allows_vite_dev_server(client):
    c, _, _ = client
    r = c.options("/api/health", headers={"Origin": "http://localhost:5173",
                                          "Access-Control-Request-Method": "GET"})
    assert r.headers.get("access-control-allow-origin") == "http://localhost:5173"


def test_queue_and_request_detail(client):
    c, store, _ = client
    rid = store.add_request("q", RequestKind.TEXT)
    store.upsert_catalog_track(CatalogTrack(id=7, artist="A", title="T", mix_name="Original Mix", label="L", genre="G"))
    store.update_request(rid, catalog_track_id=7)
    store.add_candidates(rid, [Candidate(source="s", source_ref="r", artist="A", title="T", rank=1, score=90)])
    q = c.get("/api/queue").json()
    assert q[0]["id"] == rid and q[0]["state"] == "queued" and q[0]["kind"] == "text"
    d = c.get(f"/api/requests/{rid}").json()
    assert d["request"]["id"] == rid and d["candidates"][0]["score"] == 90 and d["catalog"]["label"] == "L"
    assert c.get("/api/requests/999").status_code == 404


def test_review_flow(client):
    c, store, _ = client
    rid = store.add_request("q", RequestKind.TEXT)
    cid = store.add_candidates(rid, [Candidate(source="s", source_ref="r", artist="A", title="T", rank=1)])[0].id
    store.set_state(rid, RequestState.AWAITING_REVIEW, flag_reason="below")
    rv = c.get("/api/review").json()
    assert rv[0]["request"]["flag_reason"] == "below" and rv[0]["candidates"][0]["id"] == cid
    assert c.post(f"/api/review/{rid}/choose/{cid}").json()["state"] == "queued"
    assert c.post(f"/api/review/{rid}/choose/{cid}").status_code == 400       # no longer awaiting review
    other = store.add_request("other", RequestKind.TEXT)
    store.set_state(other, RequestState.AWAITING_REVIEW)
    assert c.post(f"/api/review/{other}/choose/{cid}").status_code == 400     # candidate belongs to `rid`
    assert c.post(f"/api/review/{other}/choose/999").status_code == 404
    store.set_state(rid, RequestState.AWAITING_REVIEW)
    assert c.post(f"/api/review/{rid}/cancel").json()["state"] == "cancelled"
    assert c.post(f"/api/review/{rid}/cancel").status_code == 400            # already cancelled


def test_rejections_and_spectrogram(client, tmp_path: Path):
    c, store, _ = client
    rid = store.add_request("bad one", RequestKind.TEXT)
    png = tmp_path / "s.png"
    png.write_bytes(b"\x89PNG\r\n\x1a\n")
    rj = store.add_rejection(rid, "cutoff", 320, 16000, png)
    lst = c.get("/api/rejections").json()
    assert lst[0]["id"] == rj and lst[0]["request_text"] == "bad one"
    r = c.get(f"/api/rejections/{rj}/spectrogram.png")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    assert c.get("/api/rejections/999/spectrogram.png").status_code == 404


def test_library_playlists_stats(client, tmp_path: Path):
    c, store, settings = client
    store.upsert_catalog_track(CatalogTrack(id=7, artist="A", title="T", mix_name="Original Mix", label="L", genre="G"))
    tid = store.add_track(path=tmp_path / "a.mp3", fmt="mp3", bitrate_kbps=320, cutoff_hz=19800, file_size=5,
                          artist="A", title="T", mix_name="Original Mix", duration_s=1, isrc=None,
                          catalog_track_id=7, request_id=None)
    pid = store.upsert_playlist("u", "P")
    store.add_playlist_track(pid, tid, 1)
    lib = c.get("/api/library", params={"q": "t"}).json()
    assert lib[0]["id"] == tid and lib[0]["catalog"]["genre"] == "G" and lib[0]["path"].endswith("a.mp3")
    assert c.get("/api/library", params={"q": "zzz"}).json() == []
    assert c.get("/api/playlists").json()[0]["track_ids"] == [tid]
    s = c.get("/api/stats").json()
    assert s["tracks"] == 1 and s["bytes"] == 5 and s["by_genre"] == {"G": 1}
    assert s["library_root"] == str(settings.library_root)


def test_static_ui_mount(tmp_path: Path):
    settings = Settings(_env_file=None, telegram_api_id=1, telegram_api_hash="h", inbox_bot_token="t", owner_telegram_id=1,
                        library_root=tmp_path / "lib", data_dir=tmp_path / "data")
    store = Store(settings.db_path)
    worker = Worker(store, DummySource(), DummyCatalog(), MemoryNotifier(), settings)
    ui = tmp_path / "ui"
    ui.mkdir()
    (ui / "index.html").write_text("<h1>crate</h1>")
    c = TestClient(create_app(store, worker, settings, ui_dir=ui))
    assert "crate" in c.get("/").text
    assert c.get("/api/health").status_code == 200
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_web.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write web.py**

```python
from __future__ import annotations

import dataclasses
from enum import Enum
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .config import Settings
from .models import TERMINAL_STATES, RequestState
from .store import Store
from .worker import Worker

DEV_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]  # Vite dev server (UI plan)
# Every route is `async def` on purpose: FastAPI runs plain `def` routes in a threadpool, and the Store's
# sqlite connection was created on the event-loop thread (check_same_thread=False only makes it *possible*
# to share; the worker and the API would still interleave statements). The handlers do only sqlite reads
# of a few milliseconds, so they can stay on the loop.


def to_dict(obj: Any) -> Any:
    if dataclasses.is_dataclass(obj):
        return {f.name: to_dict(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, Enum):
        return obj.value
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, list):
        return [to_dict(x) for x in obj]
    if isinstance(obj, dict):
        return {k: to_dict(v) for k, v in obj.items()}
    return obj


def create_app(store: Store, worker: Worker, settings: Settings, ui_dir: Path | None = None,
               status: dict | None = None) -> FastAPI:
    app = FastAPI(title="flackey", version=__version__)
    app.add_middleware(CORSMiddleware, allow_origins=DEV_ORIGINS, allow_methods=["*"], allow_headers=["*"])
    status = status if status is not None else {}

    def request_bundle(rid: int) -> dict:
        try:
            r = store.get_request(rid)
        except KeyError:
            raise HTTPException(404, "request not found")
        catalog = store.get_catalog_track(r.catalog_track_id) if r.catalog_track_id else None
        return {"request": to_dict(r), "candidates": to_dict(store.get_candidates(rid)), "catalog": to_dict(catalog)}

    @app.get("/api/health")
    async def health() -> dict:
        return {"ok": True, "version": __version__,
                "telegram_authorized": bool(status.get("telegram_authorized", True))}

    @app.get("/api/queue")
    async def queue() -> list:
        open_states = {s for s in RequestState if s not in TERMINAL_STATES}
        active = store.list_requests(open_states, limit=1000)
        recent = store.list_requests(set(TERMINAL_STATES), limit=50)
        return to_dict(sorted(active + recent, key=lambda r: r.id, reverse=True))

    @app.get("/api/requests/{rid}")
    async def request_detail(rid: int) -> dict:
        return request_bundle(rid)

    @app.get("/api/review")
    async def review() -> list:
        return [request_bundle(r.id) for r in store.list_requests({RequestState.AWAITING_REVIEW}, limit=500)]

    @app.post("/api/review/{rid}/choose/{cid}")
    async def choose(rid: int, cid: int) -> dict:
        try:
            store.get_request(rid)
            store.get_candidate(cid)
        except KeyError:
            raise HTTPException(404, "not found")
        try:
            return to_dict(await worker.choose(rid, cid))
        except ValueError as e:  # wrong request for that candidate, or not awaiting review
            raise HTTPException(400, str(e))

    @app.post("/api/review/{rid}/cancel")
    async def cancel(rid: int) -> dict:
        try:
            store.get_request(rid)
        except KeyError:
            raise HTTPException(404, "not found")
        try:
            return to_dict(await worker.cancel(rid))
        except ValueError as e:  # already done, cancelled, or mid-pipeline
            raise HTTPException(400, str(e))

    @app.get("/api/rejections")
    async def rejections() -> list:
        out = []
        for rj in store.list_rejections():
            d = to_dict(rj)
            try:
                d["request_text"] = store.get_request(rj.request_id).raw_text
            except KeyError:
                d["request_text"] = ""
            out.append(d)
        return out

    @app.get("/api/rejections/{rjid}/spectrogram.png")
    async def spectrogram(rjid: int):
        try:
            rj = store.get_rejection(rjid)
        except KeyError:
            raise HTTPException(404, "not found")
        if not rj.spectrogram_path or not Path(rj.spectrogram_path).exists():
            raise HTTPException(404, "no spectrogram")
        return FileResponse(rj.spectrogram_path, media_type="image/png")

    @app.get("/api/library")
    async def library(q: str | None = None) -> list:
        out = []
        for t in store.list_tracks(search=q):
            d = to_dict(t)
            d["catalog"] = to_dict(store.get_catalog_track(t.catalog_track_id)) if t.catalog_track_id else None
            out.append(d)
        return out

    @app.get("/api/playlists")
    async def playlists() -> list:
        return to_dict(store.list_playlists())

    @app.get("/api/stats")
    async def stats() -> dict:
        s = store.stats()
        s["library_root"] = str(settings.library_root)
        s["playlist_dir"] = str(settings.library_root / "Playlists")
        return s

    if ui_dir is not None and ui_dir.exists():
        app.mount("/", StaticFiles(directory=str(ui_dir), html=True), name="ui")
    return app
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_web.py -v`
Expected: 8 PASS

- [ ] **Step 5: Commit**

```bash
git add src/flackey/web.py tests/test_web.py
git commit -m "feat(web): json api for queue, review, rejections, library, stats"
```

---

### Task 15: CLI, process wiring, Dockerfile, README, first live run

**Files:**
- Create: `src/flackey/cli.py`, `src/flackey/app.py`, `tests/test_cli.py`, `Dockerfile`, `.dockerignore`
- Modify: `README.md`

**Interfaces:**
- `app.py`: `async run(settings: Settings, open_browser: bool = True) -> None` builds `Store`, `TelegramClient` (Telethon), a shared `httpx.AsyncClient` for `DeezerApi`, `DeezerBotSource`, `BeatportCatalog`, aiogram `Bot`/`Dispatcher` through `inbox.build_bot`, `TelegramNotifier`, `Worker`, and the FastAPI app with `ui_dir = <repo>/web/dist` and a `status` dict. It runs bot polling, the worker, and `uvicorn.Server.serve()` in one `asyncio.TaskGroup`, so the first failure or a `SIGINT` cancels the other two before the sessions are closed; `webbrowser.open` fires once the server reports started. If the Telethon session is missing or expired it does **not** exit: it logs `Telegram login required, run: crate login`, sets `status["telegram_authorized"] = False` (surfaced by `/api/health` for the spec §9 banner), and runs the bot and the web server without the worker, so messages still queue and the UI still opens.
- `cli.py` typer app named `crate`:
  - `crate start [--no-browser]` -> `asyncio.run(app.run(...))`
  - `crate status` -> prints counts by state and library size from `store.stats()`
  - `crate export` -> rewrites every M3U8 playlist file and prints their paths
  - `crate login` -> interactive Telethon `client.start()` (phone and code prompts in the terminal; handles the two-step verification password prompt itself, the program never stores the password), then prints the logged-in user and id and sets the session file mode to 600.
  - `crate add "<text or link>"` -> enqueues through `Inbox` exactly like a Telegram message and prints the reply (useful for batch work at the Mac without the phone).

- [ ] **Step 1: Write the failing CLI tests**

`tests/test_cli.py`:

```python
from pathlib import Path

from typer.testing import CliRunner

from flackey.cli import app
from flackey.models import RequestKind
from flackey.store import Store

runner = CliRunner()


def _env(tmp_path: Path) -> Path:
    env = tmp_path / ".env"
    env.write_text("TELEGRAM_API_ID=1\nTELEGRAM_API_HASH=h\nINBOX_BOT_TOKEN=t\nOWNER_TELEGRAM_ID=1\n"
                   f"LIBRARY_ROOT={tmp_path / 'lib'}\nDATA_DIR={tmp_path / 'data'}\n")
    return env


def test_status_and_export(tmp_path: Path):
    env = _env(tmp_path)
    store = Store(tmp_path / "data" / "flackey.sqlite")
    store.add_request("q", RequestKind.TEXT)
    r = runner.invoke(app, ["--env", str(env), "status"])
    assert r.exit_code == 0 and "queued: 1" in r.output and "tracks: 0" in r.output
    tid = store.add_track(path=tmp_path / "lib" / "x.mp3", fmt="mp3", bitrate_kbps=320, cutoff_hz=20000, file_size=1,
                          artist="A", title="T", mix_name="Original Mix", duration_s=1, isrc=None,
                          catalog_track_id=None, request_id=None)
    store.add_playlist_track(store.upsert_playlist("u", "Goa Set"), tid, 1)
    r = runner.invoke(app, ["--env", str(env), "export"])
    assert r.exit_code == 0 and (tmp_path / "lib" / "Playlists" / "Goa Set.m3u8").exists()


def test_add_enqueues_text(tmp_path: Path):
    env = _env(tmp_path)
    r = runner.invoke(app, ["--env", str(env), "add", "Astral Projection - Into the Void"])
    assert r.exit_code == 0 and "Queued #1" in r.output
    assert Store(tmp_path / "data" / "flackey.sqlite").get_request(1).query_artist == "Astral Projection"
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_cli.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write app.py and cli.py**

`src/flackey/app.py`:

```python
from __future__ import annotations

import asyncio
import logging
import webbrowser
from pathlib import Path

import httpx
import uvicorn
from telethon import TelegramClient

from .catalog import BeatportCatalog
from .config import Settings
from .deezer import DeezerApi
from .inbox import Inbox, TelegramNotifier, build_bot
from .source.deezer_bot import DeezerBotSource
from .store import Store
from .web import create_app
from .worker import Worker

log = logging.getLogger(__name__)
# Resolves to <repo>/web/dist under `uv sync` (editable install) and in the Docker image, which is how
# this project is run. A wheel install would resolve elsewhere; the static mount is guarded by
# `ui_dir.exists()` in create_app, so the failure mode is a 404 on `/`, never a crash.
UI_DIR = Path(__file__).resolve().parents[2] / "web" / "dist"


async def run(settings: Settings, open_browser: bool = True) -> None:
    for d in (settings.data_dir, settings.tmp_dir, settings.spectrogram_dir, settings.library_root):
        d.mkdir(parents=True, exist_ok=True)
    store = Store(settings.db_path)
    status: dict = {"telegram_authorized": True}

    client = TelegramClient(str(settings.session_path), settings.telegram_api_id, settings.telegram_api_hash)
    await client.connect()
    if not await client.is_user_authorized():
        status["telegram_authorized"] = False
        log.error("Telegram login required, run: crate login  (bot and UI stay up; the worker is paused)")

    http = httpx.AsyncClient(timeout=20)
    source = DeezerBotSource(client, settings.source_bot_username, DeezerApi(http))
    catalog = BeatportCatalog()
    notifier_holder: dict = {}

    class LateNotifier:  # the bot must be built after the worker, and the notifier needs the bot
        async def send(self, text, buttons=None):
            await notifier_holder["n"].send(text, buttons)

    worker = Worker(store, source, catalog, LateNotifier(), settings, status=status)
    inbox = Inbox(store, worker, settings.owner_telegram_id)
    bot, dp = build_bot(settings, inbox)
    notifier_holder["n"] = TelegramNotifier(bot, settings.owner_telegram_id)

    api = create_app(store, worker, settings, ui_dir=UI_DIR, status=status)
    server = uvicorn.Server(uvicorn.Config(api, host="127.0.0.1", port=settings.web_port, log_level="warning"))

    async def serve_and_open() -> None:
        task = asyncio.create_task(server.serve())
        while not server.started:
            await asyncio.sleep(0.1)
        log.info("UI at http://localhost:%d", settings.web_port)
        if open_browser:
            webbrowser.open(f"http://localhost:{settings.web_port}")
        await task

    log.info("flackey started: bot polling%s", ", worker running" if status["telegram_authorized"] else "")
    try:
        async with asyncio.TaskGroup() as tg:  # one failing or cancelled task cancels the rest
            tg.create_task(dp.start_polling(bot, handle_signals=False))
            tg.create_task(serve_and_open())
            if status["telegram_authorized"]:
                tg.create_task(worker.run_forever())
    finally:
        server.should_exit = True
        await bot.session.close()
        await http.aclose()
        await client.disconnect()
```

`asyncio.run` turns `SIGINT` into a `CancelledError` inside the task group, which cancels all three tasks before the `finally` closes the sessions; `cli.start` catches the resulting `KeyboardInterrupt`.

`src/flackey/cli.py`:

```python
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

import typer

from .config import Settings, load_settings

app = typer.Typer(name="crate", help="Flackey: Telegram inbox -> verified DJ library", no_args_is_help=True)
_state: dict = {}


def _settings() -> Settings:
    return load_settings(_state.get("env"))


@app.callback()
def main(env: Path | None = typer.Option(None, "--env", help="Path to .env (default ./.env)"),
         verbose: bool = typer.Option(False, "--verbose", "-v")) -> None:
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    _state["env"] = env


@app.command()
def start(no_browser: bool = typer.Option(False, "--no-browser")) -> None:
    """Run the inbox bot, the worker, and the web UI until Ctrl-C."""
    from .app import run

    try:
        asyncio.run(run(_settings(), open_browser=not no_browser))
    except KeyboardInterrupt:
        typer.echo("stopped")


@app.command()
def status() -> None:
    """Print queue and library counts."""
    from .store import Store

    s = _settings()
    st = Store(s.db_path).stats()
    for state, n in sorted(st["requests_by_state"].items()):
        typer.echo(f"{state}: {n}")
    typer.echo(f"tracks: {st['tracks']}")
    typer.echo(f"rejections: {st['rejections']}")
    typer.echo(f"library: {st['bytes'] / 1e6:.1f} MB at {s.library_root}")


@app.command()
def export() -> None:
    """Rewrite the M3U8 playlist files (one per YouTube playlist) for Rekordbox import."""
    from .export import write_playlists
    from .store import Store

    s = _settings()
    s.library_root.mkdir(parents=True, exist_ok=True)
    for p in write_playlists(Store(s.db_path), s.library_root):
        typer.echo(str(p))


@app.command()
def add(text: str) -> None:
    """Enqueue a track name or YouTube link, exactly like a Telegram message."""
    from .inbox import Inbox
    from .notify import NullNotifier
    from .store import Store
    from .worker import Worker

    s = _settings()
    store = Store(s.db_path)

    class _NoSource:
        name = "none"

        async def search(self, q):
            return []

        async def fetch(self, c, d):
            raise NotImplementedError

    class _NoCatalog:
        async def search(self, q):
            return []

    inbox = Inbox(store, Worker(store, _NoSource(), _NoCatalog(), NullNotifier(), s), s.owner_telegram_id)
    typer.echo(asyncio.run(inbox.handle_text(s.owner_telegram_id, text)) or "")


@app.command()
def login() -> None:
    """Log the owner's Telegram account in (one-time, interactive)."""
    from telethon import TelegramClient

    s = _settings()
    s.data_dir.mkdir(parents=True, exist_ok=True)

    async def _login() -> None:
        client = TelegramClient(str(s.session_path), s.telegram_api_id, s.telegram_api_hash)
        await client.start()  # prompts for phone, code, and 2FA password in the terminal
        me = await client.get_me()
        typer.echo(f"logged in as {me.first_name} (@{me.username}) id={me.id}")
        await client.disconnect()

    asyncio.run(_login())
    for p in s.data_dir.glob("owner.session*"):
        os.chmod(p, 0o600)
```

- [ ] **Step 4: Run CLI tests**

Run: `uv run pytest tests/test_cli.py -v`
Expected: 2 PASS

- [ ] **Step 5: Dockerfile and .dockerignore**

`Dockerfile`:

```dockerfile
# Secrets are not baked in (.dockerignore drops .env). Run with:
#   docker run --env-file .env -v flackey-data:/data -v /path/to/library:/library -p 8765:8765 flackey
# and log in once beforehand with `docker run --env-file .env -it -v flackey-data:/data flackey uv run crate login`.
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
COPY web/dist ./web/dist
RUN uv sync --frozen --no-dev
ENV DATA_DIR=/data LIBRARY_ROOT=/library WEB_PORT=8765
VOLUME ["/data", "/library"]
EXPOSE 8765
CMD ["uv", "run", "crate", "start", "--no-browser"]
```

`.dockerignore`:

```
.env
.venv
node_modules
web/node_modules
data
*.session*
.git
```

`web/dist/index.html` already exists from Task 1, so `COPY web/dist` works before the UI plan runs.

Verify: `docker build -t flackey .` succeeds (skip if Docker is not installed on the Mac; note it in the commit message).

- [ ] **Step 6: Full test run**

Run: `uv run pytest -v`
Expected: every test passes; live tests skipped.

- [ ] **Step 7: First live run**

1. `uv run crate status` prints zeros.
2. `uv run crate start` in one terminal. Expect the log line `flackey started` and the browser opening the placeholder page.
3. From the phone, send `@digcrate_bot` the text `Astral Projection - Into the Void`. Expect `Queued #1 (position 1)` within a second and a `Done: Astral Projection – Into the Void (Original Mix) · 7:22 · genuine 320 kbps ... · 142 BPM · A Major · Psy-Trance / Sacred Technology · 100%` within a minute.
4. Confirm the file at `~/Music/DJ Library/Psy-Trance/Sacred Technology/Astral Projection - Into the Void.mp3`, run `uv run python -c "from pathlib import Path; from flackey.tag import read_tags; print(read_tags(Path.home()/'Music/DJ Library/Psy-Trance/Sacred Technology/Astral Projection - Into the Void.mp3'))"` and check the tags. The comment must read `flackey: verified 320 kbps · cutoff 20.2 kHz · beatport 16552105` (20 250 Hz was measured on this exact file on 2026-09-03; anything between 19 500 and 20 500 is fine, anything lower means the cliff detector or the download changed).
4b. Real-music sanity check of the verifier on the same file: `uv run python -c "from pathlib import Path; from flackey.verify import spectral_cutoff_hz; print(spectral_cutoff_hz(Path.home()/'Music/DJ Library/Psy-Trance/Sacred Technology/Astral Projection - Into the Void.mp3'))"` prints a value in that same range. This is the only check of the algorithm against music rather than noise, so do not skip it.
5. Send the same text again. Expect `Already in library: ...`.
6. Send a YouTube Music playlist link with 3 to 5 tracks. Expect the `Queued k of n` reply and one `Done`/`Review needed`/`Not available` per track.
7. Drag `~/Music/DJ Library/Psy-Trance` into the Rekordbox collection and confirm the track shows the Beatport tags and artwork. Then File → Import → Playlist, choose `~/Music/DJ Library/Playlists/<name>.m3u8` and confirm the playlist appears with its tracks. Set a hot cue on the track, re-import the same M3U8, and confirm the cue survived: that is the reason there is no rekordbox.xml (spec §8).
8. Record anything that deviated from `docs/source-bot-protocol.md` into that file. Also send the bot a query that cannot exist (`zzqx nonexistent track 48213`) and write its exact no-result reply into the protocol doc §3; `DeezerBotSource.search` currently infers "not found" from a keyboard-less reply followed by 5 s of silence, and the exact text lets a later commit match it directly.

- [ ] **Step 8: README**

Replace `README.md` with: what it does (three sentences), requirements (macOS, Homebrew ffmpeg and yt-dlp, uv, Python 3.12), setup (`cp .env.example .env`, fill values, `uv sync`, `uv run crate login`), usage (`uv run crate start`, message the bot, `crate add`, `crate status`, `crate export`), the Rekordbox import steps from Step 7.7 and why there is no rekordbox.xml (spec §8), the 24-hour note (Telegram keeps unread bot messages for 24 hours while the program is off), what happens when the Telegram session expires (the UI banner, then `crate login`), where data lives (`~/.config/flackey`, `~/Music/DJ Library`), a Docker section with the two `docker run --env-file .env ...` commands from the Dockerfile header, and links to the spec, the plan, and the protocol doc.

- [ ] **Step 9: Commit**

```bash
git add src/flackey/app.py src/flackey/cli.py tests/test_cli.py Dockerfile .dockerignore README.md docs/source-bot-protocol.md
git commit -m "feat(cli): crate start/status/export/add/login, docker image, readme"
```

---

## Self-Review

**Spec coverage.** §3.1 inputs and replies: Task 13. §3.2 steps 1 to 9: Task 12 (identify/catalog/source/decide/fetch/verify/file/export/report). §3.3 review in Telegram: Task 13 callbacks, Task 12 buttons; in the UI: Task 14 routes (the React pages are the follow-up UI plan). §3.4 duplicates and playlist sync: Tasks 9, 12, 13. §3.5 UI: JSON API in Task 14; visual UI deferred to the UI plan by design. §4 architecture and data model: Tasks 1, 2, 15. §4.3 configuration: Task 1. §4.4 lifecycle and resume: Tasks 12, 15. §5 matching: Tasks 4, 6. §6 verification: Task 7. §7 tagging and filing: Tasks 8, 9. §8 playlist export and manual Rekordbox import: Task 10 (decided 2026-09-04: no rekordbox.xml, it would overwrite cues on re-import). §9 error handling: Task 12 (`_retry_or_fail` with backoff via `requests.retry_after`, catch-all), Task 13 (yt-dlp message), Tasks 14 and 15 (`/api/health.telegram_authorized` feeds the login banner; the banner itself is drawn by the UI plan). §10 testing: every task; live smoke test in Task 11. §11 layout: matches. §12 owner inputs: all already collected except the live run.

**Deferred on purpose, matching the spec's "Deferred" list:** backup, launchd, direct Deezer client, non-YouTube links, audio analysis, YouTube audio fallback, Rekordbox DB reading, multi-user. **Deferred beyond the spec list, with the owner's approval of the core/UI split:** the React UI itself (`docs/superpowers/plans/` will get a second plan; this one ships the JSON API and a placeholder page), and format selection on the source bot (the spec's FLAC/WAV quality ladder): @DeezerMusicBot only ever handed out MP3 in the captured sessions, so the pipeline accepts whatever format arrives and `verify`/`write_tags` already handle FLAC, WAV and AIFF for the day a source offers them.

**Type consistency check.** `Store.add_track` keyword signature is used identically in Tasks 9, 10, 12, 13, 14 tests. `Candidate.source_ref` is the callback data in Tasks 11 and 12. `Worker.choose/cancel` are async in Tasks 12, 13, 14. `Verdict` field order `(passed, fmt, bitrate_kbps, cutoff_hz, reason, spectrogram_path)` is used positionally in Tasks 7, 8. `display_title` lives in `tag.py` and is imported by `library.py` and `export.py`. `CatalogTrack` in `tests/test_worker.py` uses `duration_ms=3000` so the 3 second fake file scores full duration points.

**Known judgment calls for the implementer.** (1) In Task 12, the fallback catalog id is negative (crc32 of the source ref when there is no Deezer id) so it never collides with Beatport ids and is stable across restarts. (2) In Task 11, `fetch` keeps the menu message in memory; after a restart it re-searches, which is why `Candidate.source_ref` must be stable across searches (it is: Deezer track ids are stable). (3) `LateNotifier` in `app.py` exists only because the bot must be built after the worker; if that reads badly, build the notifier first with a lazily created `Bot` instead. (4) `Store.find_track_by_meta` counts an unknown duration as a match (stricter than spec §3.4); see Task 2. (5) `Worker._process` re-parses bare-text queries on every retry; it is idempotent and only affects requests inserted without a query, which the inbox never does. (6) `verify` still runs ffprobe once plus two ffmpeg decodes (spectrogram and cutoff); sharing one decode would save about a second per track and is not worth the coupling yet.

**External review, 2026-09-03, two passes.** A first independent review found five blockers and about thirty smaller defects; a second review then extracted every code block, ran the whole suite (130 passed, 1 failed) and read the Telethon and mutagen sources against the plan. Everything from both passes was folded in. From the second pass: `fetch` anchors `conv.get_response(menu_msg)` on the menu message (a bare call raises "No message was sent previously"); raw-text matching uses `token_sort_ratio`, since `token_set_ratio` scores 100 for any token subset; the edit listener is registered before the toggle click; verification and tagging run in `asyncio.to_thread`; an expired Telegram session pauses the worker instead of burning retries; WAV and AIFF are tagged (ID3 inside the container) so `verify` cannot pass a file that `write_tags` then rejects; API routes are async; `cancel` refuses finished requests; a re-sent playlist does not re-queue open entries; `.gitignore` uses `web/dist/*` plus a negation, because a file under an ignored directory cannot be re-included. The ones worth knowing about when reading the code: the spectral cutoff is a cliff detector, not a floor-margin test (Task 7 explains why, with measurements); `tests/__init__.py` is required; the source-bot toggle is clicked by position because the bot reuses callback data; a remix scoring exactly 80 is parked, not auto-accepted; retries back off through `requests.retry_after`; and a missing Telegram login no longer exits the process.
