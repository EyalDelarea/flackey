# Soulseek Lossless Upgrade Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Before fetching a chosen Deezer candidate, ask Soulseek (through the slskd sidecar) for a lossless file, verify it, prove it is the right recording with a Chromaprint fingerprint against the Deezer preview, convert it to AIFF and file it; fall back to the Deezer MP3 on any miss, and keep a full record of every attempt.

**Architecture:** A pure domain module (`lossless.py`) turns search results into a ranked pick with a stored report. A provider protocol (`source/lossless.py`) hides the network; `source/slskd.py` is the first provider. Adapters `convert.py`, `fingerprint.py` and `attempts.py` do conversion, the same-recording check and the attempt record. The worker gains one step, `_try_lossless`, that returns a converted, verified file or `None`; everything downstream of it is unchanged except for a few new columns.

**Tech Stack:** Python 3.12, httpx + respx, ffmpeg/ffprobe, Chromaprint `fpcalc` (Homebrew), mutagen, rapidfuzz, sqlite3, FastAPI, typer, pytest + pytest-asyncio, import-linter.

**Spec:** `docs/superpowers/specs/2026-09-07-soulseek-lossless-upgrade-design.md` (read it first; section numbers below refer to it). Measurements: `docs/research/2026-09-07-soulseek-spike-findings.md`.

## Global Constraints

- Layering (import-linter, `pyproject.toml`): `cli -> desktop -> app -> web -> worker/events/inbox -> attempts -> adapters -> lossless -> match/identify -> notify/config/models/logsetup`. `attempts` gets its own line because it imports `store`; `convert`, `fingerprint` and `source.*` join the adapters line; `lossless` gets its own line above `match : identify` because it imports `identify` (exact list in Task 12).
- Settings prefix: sidecar-specific settings are `slskd_*`; policy, caps and retention are `lossless_*`. Exact names and defaults in spec §10 and Task 1.
- Nothing from a peer is trusted: the local path is derived and containment-checked (spec §7), ffmpeg runs with `-vn -map 0:a -map_metadata -1` (spec §9), tags are written fresh from Beatport.
- `slskd_api_key` never appears in logs, `/api/settings`, `/api/health` or any error text.
- The worker never raises out of `_try_lossless`; every miss is an attempt row with an outcome from spec §11.
- Tests never sleep for real: providers and the recorder take injectable `sleep` and `clock`.
- Every test that needs ffmpeg is marked with `tests.conftest.requires_ffmpeg`; tests that need `fpcalc` use the `requires_fpcalc` marker added in Task 7.
- Run tests with `uv run pytest -q`, lint with `uv run ruff check src tests`, layering with `uv run lint-imports`.
- Commit after each task; commit messages end with the trailer used elsewhere on this branch.

Recorded fixtures already on the branch (do not regenerate): `tests/fixtures/slskd/*.json` (application, search in progress and completed, responses in progress and completed, one completed download) and `tests/fixtures/fingerprints.json` (six preview/track fingerprint pairs with expected scores).

---

## File structure

| File | Status | Responsibility |
|---|---|---|
| `src/flackey/config.py` | modify | new settings, `soulseek_enabled`, `lossless_enabled`, derived dirs, generalised path saving |
| `src/flackey/models.py` | modify | `Verdict.bit_depth/sample_rate`, `Track.source/source_fmt/bit_depth/sample_rate`, `Request.fetch_source`, new `LosslessAttempt`, `Evidence` |
| `src/flackey/verify.py` | modify | `Probe.bit_depth`; verdicts carry bit depth and sample rate |
| `src/flackey/store.py` | modify | `_ensure_column`, new columns, `lossless_attempts` and `track_evidence` tables and methods |
| `src/flackey/lossless.py` | create | pure gate: `LosslessFile`, `Reference`, `PickPolicy`, rules, rankers, `pick`, `PickReport` JSON |
| `src/flackey/source/lossless.py` | create | `LosslessProvider` protocol, `TransferProgress`, `LosslessError`, `LosslessUnavailable` |
| `src/flackey/source/slskd.py` | create | `SlskdClient` (HTTP) and `SoulseekProvider` |
| `src/flackey/convert.py` | create | `to_format` via ffmpeg |
| `src/flackey/fingerprint.py` | create | `fpcalc` wrapper, `compare`, Deezer preview fetch, `check` |
| `src/flackey/deezer.py` | modify | `DeezerTrack.preview_url` |
| `src/flackey/attempts.py` | create | `AttemptRecorder` (timeline, raw files, row updates), `prune_raw` |
| `src/flackey/worker.py` | modify | `_try_lossless`, verdict pass-through, Done line, start-up cancel, daily maintenance |
| `src/flackey/web/__init__.py`, `web/library.py`, `web/lossless.py` | modify/create | health fields, attempt in request bundle, format block on tracks, `/api/lossless/attempts` |
| `src/flackey/cli.py` | modify | `crate lossless replay <request id>` |
| `src/flackey/app.py` | modify | build the provider from settings, pass it and an `httpx.AsyncClient` to the worker |
| `pyproject.toml`, `.env.example`, `README.md` | modify | layers, keys, sidecar setup |

## Shared interfaces (exact names used across tasks)

```python
# config.Settings (Task 1)
slskd_url: str; slskd_api_key: str | None; slskd_downloads_dir: Path | None; lossless_filing_format: str
lossless_search_wait_s: int; lossless_first_byte_s: int; lossless_transfer_s: int; lossless_poll_s: float
lossless_duration_tolerance_s: int; lossless_title_ratio: int; lossless_require_artist: bool
lossless_max_queue: int | None; lossless_fingerprint_min: float; lossless_max_picks: int; lossless_keep_raw_days: int
Settings.soulseek_enabled -> bool; Settings.lossless_enabled -> bool
Settings.slskd_downloads -> Path; Settings.lossless_raw_dir -> Path

# lossless (Task 4)
LosslessFile(provider, username, path, extension, size, length_s, bitrate_kbps, sample_rate, bit_depth,
             has_free_slot, upload_speed_bps, queue_length); .name; .folder
Reference(artist, title, mix_name, duration_s, deezer_id); reference_for(catalog, cand) -> Reference
search_text(ref) -> str
PickPolicy(...); policy_from_settings(settings) -> PickPolicy
pick(files, ref, policy) -> PickReport; PickReport.to_dict() / PickReport.from_dict(d)

# source.lossless (Task 5)
class LosslessProvider(Protocol):
    name: str
    async def health(self) -> dict                      # {"status": "ok"|"unreachable"|"not_logged_in", "username": str|None}
    async def search(self, text: str, *, wait_s: float, on_raw=None) -> list[LosslessFile]
    async def download(self, file: LosslessFile, *, first_byte_s: float, total_s: float, poll_s: float,
                       on_progress=None, on_raw=None) -> Path    # local path inside the downloads dir
    async def cancel_all(self) -> int
    async def rescan_shares(self) -> None
LosslessError(msg, outcome="transfer_failed"); LosslessUnavailable(LosslessError) with outcome "unavailable"
TransferProgress(state, bytes, size, speed_bps, first_byte_ms)

# convert (Task 6)
to_format(src: Path, fmt: str, bit_depth: int | None) -> Path

# fingerprint (Task 7)
FingerprintResult(status, score, offset_s, reason, preview, track)   # status in {"matched","failed","skipped"}
fpcalc_available() -> bool; fingerprint(path, start_s=0.0) -> list[int]; compare(needle, hay) -> tuple[float, int]
async check(path, deezer_id, http, *, minimum, tmp_dir) -> FingerprintResult

# attempts (Task 8)
AttemptRecorder(store, raw_dir, request_id, provider, query, clock=time.monotonic)
  .id; .event(name, **detail); .raw(name, obj); .finish(outcome, **cols); .elapsed_ms()
prune_raw(raw_dir, keep_days, now=time.time) -> int

# store (Task 3)
add_attempt(request_id, provider, query) -> int; update_attempt(attempt_id, **fields)
get_attempt(attempt_id) -> LosslessAttempt; get_attempt_for_request(request_id) -> LosslessAttempt | None
list_attempts(limit=50, outcome=None) -> list[LosslessAttempt]; attempt_counts(since_hours=24) -> dict[str, int]
mark_open_attempts_interrupted() -> int
add_evidence(track_id, kind, value: dict) -> int; list_evidence(track_id) -> list[Evidence]
add_track(..., source="deezer_bot", source_fmt=None, bit_depth=None, sample_rate=None)
```

---

### Task 1: Settings

**Files:**
- Modify: `src/flackey/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: the `Settings` fields and properties listed under "Shared interfaces"; `FILE_KEYS` gains `slskd_url`, `slskd_api_key`, `slskd_downloads_dir`, `lossless_filing_format`; `PATH_KEYS = ("library_root", "slskd_downloads_dir")`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_config.py`:

```python
import pytest
from pydantic import ValidationError


def test_lossless_is_off_until_an_api_key_is_set(tmp_path: Path):
    s = Settings(_env_file=None, data_dir=tmp_path)
    assert s.soulseek_enabled is False and s.lossless_enabled is False
    assert s.slskd_url == "http://127.0.0.1:5030"
    assert s.slskd_downloads == tmp_path / "slskd" / "downloads"
    assert s.lossless_raw_dir == tmp_path / "lossless" / "attempts"
    assert (s.lossless_filing_format, s.lossless_search_wait_s, s.lossless_first_byte_s, s.lossless_transfer_s,
            s.lossless_poll_s) == ("aiff", 30, 60, 600, 2.0)
    assert (s.lossless_duration_tolerance_s, s.lossless_title_ratio, s.lossless_require_artist,
            s.lossless_max_queue, s.lossless_fingerprint_min, s.lossless_max_picks,
            s.lossless_keep_raw_days) == (3, 90, False, None, 0.90, 2, 30)
    on = Settings(_env_file=None, data_dir=tmp_path, slskd_api_key="k")
    assert on.soulseek_enabled is True and on.lossless_enabled is True


def test_downloads_dir_override_is_expanded_and_saved_as_text(tmp_path: Path):
    s = Settings(_env_file=None, data_dir=tmp_path / "data", slskd_downloads_dir="~/dl")
    assert s.slskd_downloads == Path("~/dl").expanduser()
    save_settings(s, slskd_downloads_dir=tmp_path / "other")
    data = json.loads(s.settings_path.read_text())
    assert data["slskd_downloads_dir"] == str(tmp_path / "other")
    assert load_settings(env_file=tmp_path / "none.env").slskd_downloads_dir is None or True  # file precedence covered below


def test_settings_file_supplies_slskd_keys(tmp_path: Path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "settings.json").write_text(json.dumps({"slskd_api_key": "file-key", "lossless_filing_format": "wav"}))
    env = tmp_path / ".env"
    env.write_text(f"DATA_DIR={data_dir}\n")
    s = load_settings(env)
    assert s.slskd_api_key == "file-key" and s.lossless_filing_format == "wav" and s.soulseek_enabled


def test_filing_format_is_validated(tmp_path: Path):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, data_dir=tmp_path, lossless_filing_format="mp3")
```

Make sure `json` and `load_settings` are imported at the top of the test file (they already are for the existing tests; check).

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_config.py -q`
Expected: 4 failures, `AttributeError: 'Settings' object has no attribute 'soulseek_enabled'` and similar.

- [ ] **Step 3: Implement the settings**

In `src/flackey/config.py`:

```python
FILE_KEYS = ("library_root", "telegram_api_id", "telegram_api_hash",
             "slskd_url", "slskd_api_key", "slskd_downloads_dir", "lossless_filing_format")
PATH_KEYS = ("library_root", "slskd_downloads_dir")
FILING_FORMATS = ("aiff", "wav", "flac")
```

Add to `Settings` after `data_dir`:

```python
    # Lossless upgrade through the slskd sidecar (spec §10). Off until an API key is set.
    slskd_url: str = "http://127.0.0.1:5030"
    slskd_api_key: str | None = None
    slskd_downloads_dir: Path | None = None       # default: <data_dir>/slskd/downloads, see `slskd_downloads`
    lossless_filing_format: str = "aiff"
    lossless_search_wait_s: int = 30
    lossless_first_byte_s: int = 60
    lossless_transfer_s: int = 600
    lossless_poll_s: float = 2.0
    lossless_duration_tolerance_s: int = 3
    lossless_title_ratio: int = 90
    lossless_require_artist: bool = False
    lossless_max_queue: int | None = None
    lossless_fingerprint_min: float = 0.90
    lossless_max_picks: int = 2
    lossless_keep_raw_days: int = 30

    @field_validator("slskd_downloads_dir", mode="after")
    @classmethod
    def _expand_optional(cls, v: Path | None) -> Path | None:
        return v.expanduser() if v else v

    @field_validator("lossless_filing_format", mode="after")
    @classmethod
    def _filing_format(cls, v: str) -> str:
        if v not in FILING_FORMATS:
            raise ValueError(f"lossless_filing_format must be one of {FILING_FORMATS}")
        return v

    @property
    def soulseek_enabled(self) -> bool:
        return bool(self.slskd_api_key)

    @property
    def lossless_enabled(self) -> bool:
        return self.soulseek_enabled

    @property
    def slskd_downloads(self) -> Path:
        return self.slskd_downloads_dir or self.data_dir / "slskd" / "downloads"

    @property
    def lossless_raw_dir(self) -> Path:
        return self.data_dir / "lossless" / "attempts"
```

In `save_settings`, replace the two `library_root` special cases:

```python
        setattr(settings, k, Path(v).expanduser() if k in PATH_KEYS else v)
    ...
        data[k] = str(getattr(settings, k)) if k in PATH_KEYS else getattr(settings, k)
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_config.py -q`
Expected: all pass. Remove the throwaway `or True` assertion line from `test_downloads_dir_override_is_expanded_and_saved_as_text` if you kept it; the meaningful assertions are the two above it.

- [ ] **Step 5: Commit**

```bash
git add src/flackey/config.py tests/test_config.py
git commit -m "feat(config): slskd and lossless settings; soulseek is on only when an API key is set"
```

---

### Task 2: Bit depth and sample rate in probe and verdicts

**Files:**
- Modify: `src/flackey/models.py` (`Verdict`), `src/flackey/verify.py` (`Probe`, `probe`, `verify`)
- Test: `tests/test_verify.py`

**Interfaces:**
- Produces: `Probe.bit_depth: int | None`; `Verdict.bit_depth: int | None = None`, `Verdict.sample_rate: int | None = None`, both filled by `verify()`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_verify.py`:

```python
def test_probe_reports_bit_depth_and_sample_rate(flac: Path, real320: Path):
    p = probe(flac)
    assert (p.fmt, p.bit_depth, p.sample_rate) == ("flac", 16, 44100)
    m = probe(real320)
    assert (m.fmt, m.bit_depth, m.sample_rate) == ("mp3", None, 44100)


def test_verdict_carries_bit_depth_and_sample_rate(flac: Path, tmp_path: Path):
    v = verify(flac, tmp_path)
    assert v.passed and v.bit_depth == 16 and v.sample_rate == 44100
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_verify.py -q -k "bit_depth"`
Expected: FAIL with `AttributeError: 'Probe' object has no attribute 'bit_depth'`.

- [ ] **Step 3: Implement**

`models.py`, `Verdict`:

```python
@dataclass
class Verdict:
    passed: bool
    fmt: str
    bitrate_kbps: int
    cutoff_hz: int
    reason: str
    spectrogram_path: Path | None = None
    bit_depth: int | None = None      # lossless only; None for mp3
    sample_rate: int | None = None
```

`verify.py`:

```python
@dataclass
class Probe:
    fmt: str
    bitrate_kbps: int
    duration_s: float
    sample_rate: int
    bit_depth: int | None = None
```

In `probe()`, before the `return`:

```python
    bits = int(audio.get("bits_per_raw_sample") or audio.get("bits_per_sample") or 0) or None
    if fmt == "mp3":
        bits = None
    return Probe(fmt=fmt, bitrate_kbps=round(bitrate / 1000), duration_s=float(data["format"].get("duration", 0)),
                 sample_rate=int(audio.get("sample_rate", 0)), bit_depth=bits)
```

In `verify()`, every `Verdict(...)` construction gains `bit_depth=pr.bit_depth, sample_rate=pr.sample_rate` as trailing keyword arguments. There are six of them; change all six.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_verify.py tests/test_tag.py tests/test_worker.py -q`
Expected: all pass (the tag and worker tests construct `Verdict` positionally or by keyword and must still work).

- [ ] **Step 5: Commit**

```bash
git add src/flackey/models.py src/flackey/verify.py tests/test_verify.py
git commit -m "feat(verify): probe and verdict carry bit depth and sample rate"
```

---

### Task 3: Store: migration helper, new columns, attempts and evidence tables

**Files:**
- Modify: `src/flackey/models.py` (`Track`, `Request`, new `LosslessAttempt`, `Evidence`), `src/flackey/store.py`
- Test: `tests/test_store.py`

**Interfaces:**
- Produces: the store methods under "Shared interfaces"; `Track.source`, `Track.source_fmt`, `Track.bit_depth`, `Track.sample_rate`; `Request.fetch_source`; `Store._ensure_column(table, column, ddl)`.
- Attempt outcomes (spec §11): `filed`, `no_pick`, `first_byte_timeout`, `transfer_timeout`, `transfer_failed`, `verify_failed`, `fingerprint_failed`, `convert_failed`, `unavailable`, `interrupted`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_store.py`:

```python
def test_ensure_column_adds_once_and_survives_reopen(tmp_path: Path):
    db = tmp_path / "m.sqlite"
    s1 = Store(db)
    cols = {r[1] for r in s1.conn.execute("PRAGMA table_info(tracks)")}
    assert {"source", "source_fmt", "bit_depth", "sample_rate"} <= cols
    assert "fetch_source" in {r[1] for r in s1.conn.execute("PRAGMA table_info(requests)")}
    s1.conn.close()
    s2 = Store(db)  # second open must not fail on ALTER TABLE
    assert s2.conn.execute("SELECT COUNT(*) FROM tracks").fetchone()[0] == 0


def test_track_source_columns_round_trip(store: Store, tmp_path: Path):
    tid = store.add_track(path=tmp_path / "a.aiff", fmt="aiff", bitrate_kbps=1411, cutoff_hz=22050, file_size=1,
                          artist="A", title="T", mix_name="Original Mix", duration_s=1, isrc=None,
                          catalog_track_id=None, request_id=None, source="soulseek", source_fmt="flac",
                          bit_depth=16, sample_rate=44100)
    t = store.get_track(tid)
    assert (t.source, t.source_fmt, t.bit_depth, t.sample_rate) == ("soulseek", "flac", 16, 44100)
    old = store.add_track(path=tmp_path / "b.mp3", fmt="mp3", bitrate_kbps=320, cutoff_hz=20000, file_size=1,
                          artist="A", title="U", mix_name="Original Mix", duration_s=1, isrc=None,
                          catalog_track_id=None, request_id=None)
    assert store.get_track(old).source == "deezer_bot" and store.get_track(old).source_fmt is None
    assert store.stats()["by_source"] == {"deezer_bot": 1, "soulseek": 1}


def test_attempt_lifecycle(store: Store):
    rid = store.add_request("q", RequestKind.TEXT)
    aid = store.add_attempt(rid, "soulseek", "Hallucinogen Orphic Thrench")
    a = store.get_attempt(aid)
    assert a.request_id == rid and a.provider == "soulseek" and a.outcome is None and a.timeline == []
    store.update_attempt(aid, timeline=[{"t_ms": 1, "event": "search_started", "detail": {}}],
                         report={"seen": 3}, fingerprint={"score": 0.98}, first_byte_ms=1200, total_ms=20000,
                         outcome="filed", spectrogram_path="/x.png", raw_dir="/raw/1")
    a = store.get_attempt(aid)
    assert a.outcome == "filed" and a.report == {"seen": 3} and a.fingerprint == {"score": 0.98}
    assert a.timeline[0]["event"] == "search_started" and a.first_byte_ms == 1200 and a.raw_dir == "/raw/1"
    assert store.get_attempt_for_request(rid).id == aid
    assert store.get_attempt_for_request(rid + 1) is None
    assert [x.id for x in store.list_attempts()] == [aid]
    assert store.list_attempts(outcome="no_pick") == []
    assert store.attempt_counts() == {"filed": 1}


def test_open_attempts_become_interrupted_on_start(store: Store):
    rid = store.add_request("q", RequestKind.TEXT)
    a1 = store.add_attempt(rid, "soulseek", "q")
    a2 = store.add_attempt(rid, "soulseek", "q")
    store.update_attempt(a2, outcome="no_pick")
    assert store.mark_open_attempts_interrupted() == 1
    assert store.get_attempt(a1).outcome == "interrupted" and store.get_attempt(a2).outcome == "no_pick"


def test_evidence_rows(store: Store, tmp_path: Path):
    tid = store.add_track(path=tmp_path / "a.aiff", fmt="aiff", bitrate_kbps=1411, cutoff_hz=22050, file_size=1,
                          artist="A", title="T", mix_name="Original Mix", duration_s=1, isrc=None,
                          catalog_track_id=None, request_id=None)
    store.add_evidence(tid, "recording_match", {"score": 0.98, "offset_s": 48.0, "reference": "deezer:6025986"})
    store.add_evidence(tid, "source", {"provider": "soulseek", "username": "loginty"})
    rows = store.list_evidence(tid)
    assert [r.kind for r in rows] == ["recording_match", "source"] and rows[0].value["score"] == 0.98
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_store.py -q`
Expected: the five new tests fail (`AttributeError` / `TypeError: add_track() got an unexpected keyword argument 'source'`).

- [ ] **Step 3: Implement models**

`models.py`: add to `Track` after `spectrogram_path`:

```python
    source: str = "deezer_bot"              # provider name; "soulseek" for a lossless upgrade
    source_fmt: str | None = None           # format as downloaded ("flac") when it differs from fmt
    bit_depth: int | None = None
    sample_rate: int | None = None
```

Add to `Request` after `track_id`:

```python
    fetch_source: str | None = None         # "soulseek" or "deezer" while FETCHING; cleared after
```

Add after `Rejection`:

```python
@dataclass
class LosslessAttempt:
    id: int
    request_id: int
    provider: str
    created_at: str
    query: str
    outcome: str | None = None
    report: dict | None = None
    timeline: list = field(default_factory=list)
    fingerprint: dict | None = None
    spectrogram_path: str | None = None
    first_byte_ms: int | None = None
    total_ms: int | None = None
    raw_dir: str | None = None


ATTEMPT_OUTCOMES = ("filed", "no_pick", "first_byte_timeout", "transfer_timeout", "transfer_failed",
                    "verify_failed", "fingerprint_failed", "convert_failed", "unavailable", "interrupted")


@dataclass
class Evidence:
    id: int
    track_id: int
    kind: str
    value: dict
    created_at: str
```

- [ ] **Step 4: Implement the store**

`store.py`: import `json`, and `Evidence`, `LosslessAttempt` from models. Append to `SCHEMA`:

```sql
CREATE TABLE IF NOT EXISTS lossless_attempts (
  id INTEGER PRIMARY KEY AUTOINCREMENT, request_id INTEGER NOT NULL, provider TEXT NOT NULL,
  created_at TEXT NOT NULL, query TEXT NOT NULL, outcome TEXT, report_json TEXT, timeline_json TEXT,
  fingerprint_json TEXT, spectrogram_path TEXT, first_byte_ms INTEGER, total_ms INTEGER, raw_dir TEXT
);
CREATE INDEX IF NOT EXISTS lossless_attempts_request ON lossless_attempts(request_id);
CREATE TABLE IF NOT EXISTS track_evidence (
  id INTEGER PRIMARY KEY AUTOINCREMENT, track_id INTEGER NOT NULL, kind TEXT NOT NULL,
  value_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS track_evidence_track ON track_evidence(track_id);
```

In `__init__`, after `self.conn.executescript(SCHEMA)`:

```python
        # The schema script is CREATE IF NOT EXISTS only; columns added after a table shipped go here.
        self._ensure_column("tracks", "source", "TEXT NOT NULL DEFAULT 'deezer_bot'")
        self._ensure_column("tracks", "source_fmt", "TEXT")
        self._ensure_column("tracks", "bit_depth", "INTEGER")
        self._ensure_column("tracks", "sample_rate", "INTEGER")
        self._ensure_column("requests", "fetch_source", "TEXT")
```

Add the helper next to `_renormalize`:

```python
    def _ensure_column(self, table: str, column: str, ddl: str) -> None:
        cols = {r[1] for r in self.conn.execute(f"PRAGMA table_info({table})")}
        if column not in cols:
            self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
            self.conn.commit()
```

`add_track` gains keyword parameters `source: str = "deezer_bot", source_fmt: str | None = None, bit_depth: int | None = None, sample_rate: int | None = None` and writes them (extend the INSERT column list and the values tuple; the `_norm` columns stay last). `_row_to_track` needs no change because `Track(**d)` accepts the new columns.

`stats()`: add

```python
        by_source = {r[0]: r[1] for r in self.conn.execute("SELECT source, COUNT(*) FROM tracks GROUP BY source")}
```

and include `"by_source": by_source` in the returned dict.

Attempts and evidence, new section before `# ---- settings`:

```python
    # ---- lossless attempts and evidence (spec §11, §17, §18) --------------
    _ATTEMPT_JSON = {"report": "report_json", "timeline": "timeline_json", "fingerprint": "fingerprint_json"}

    def add_attempt(self, request_id: int, provider: str, query: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO lossless_attempts (request_id, provider, created_at, query, timeline_json) VALUES (?,?,?,?,'[]')",
            (request_id, provider, _now(), query))
        self.conn.commit()
        return int(cur.lastrowid)

    def update_attempt(self, attempt_id: int, **fields) -> None:
        cols, vals = [], []
        for k, v in fields.items():
            col = self._ATTEMPT_JSON.get(k, k)
            cols.append(f"{col}=?")
            vals.append(json.dumps(v) if k in self._ATTEMPT_JSON else v)
        self.conn.execute(f"UPDATE lossless_attempts SET {', '.join(cols)} WHERE id=?", vals + [attempt_id])
        self.conn.commit()

    def _row_to_attempt(self, r: sqlite3.Row) -> LosslessAttempt:
        d = dict(r)
        return LosslessAttempt(
            id=d["id"], request_id=d["request_id"], provider=d["provider"], created_at=d["created_at"],
            query=d["query"], outcome=d["outcome"],
            report=json.loads(d["report_json"]) if d["report_json"] else None,
            timeline=json.loads(d["timeline_json"]) if d["timeline_json"] else [],
            fingerprint=json.loads(d["fingerprint_json"]) if d["fingerprint_json"] else None,
            spectrogram_path=d["spectrogram_path"], first_byte_ms=d["first_byte_ms"], total_ms=d["total_ms"],
            raw_dir=d["raw_dir"])

    def get_attempt(self, attempt_id: int) -> LosslessAttempt:
        r = self.conn.execute("SELECT * FROM lossless_attempts WHERE id=?", (attempt_id,)).fetchone()
        if r is None:
            raise KeyError(attempt_id)
        return self._row_to_attempt(r)

    def get_attempt_for_request(self, request_id: int) -> LosslessAttempt | None:
        r = self.conn.execute("SELECT * FROM lossless_attempts WHERE request_id=? ORDER BY id DESC LIMIT 1",
                              (request_id,)).fetchone()
        return None if r is None else self._row_to_attempt(r)

    def list_attempts(self, limit: int = 50, outcome: str | None = None) -> list[LosslessAttempt]:
        if outcome:
            rows = self.conn.execute("SELECT * FROM lossless_attempts WHERE outcome=? ORDER BY id DESC LIMIT ?",
                                     (outcome, limit)).fetchall()
        else:
            rows = self.conn.execute("SELECT * FROM lossless_attempts ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [self._row_to_attempt(r) for r in rows]

    def attempt_counts(self, since_hours: int = 24) -> dict[str, int]:
        since = (datetime.now(timezone.utc) - timedelta(hours=since_hours)).isoformat(timespec="seconds")
        return {r[0]: r[1] for r in self.conn.execute(
            "SELECT COALESCE(outcome, 'open'), COUNT(*) FROM lossless_attempts WHERE created_at >= ? GROUP BY 1",
            (since,))}

    def mark_open_attempts_interrupted(self) -> int:
        cur = self.conn.execute("UPDATE lossless_attempts SET outcome='interrupted' WHERE outcome IS NULL")
        self.conn.commit()
        return cur.rowcount

    def add_evidence(self, track_id: int, kind: str, value: dict) -> int:
        cur = self.conn.execute(
            "INSERT INTO track_evidence (track_id, kind, value_json, created_at) VALUES (?,?,?,?)",
            (track_id, kind, json.dumps(value), _now()))
        self.conn.commit()
        return int(cur.lastrowid)

    def list_evidence(self, track_id: int) -> list[Evidence]:
        rows = self.conn.execute("SELECT * FROM track_evidence WHERE track_id=? ORDER BY id", (track_id,)).fetchall()
        return [Evidence(id=r["id"], track_id=r["track_id"], kind=r["kind"], value=json.loads(r["value_json"]),
                         created_at=r["created_at"]) for r in rows]
```

`timedelta` must be imported from `datetime` at the top of `store.py`.

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_store.py tests/test_web.py tests/test_worker.py -q`
Expected: all pass. `test_web` bundles serialise `Request` and `Track` through `to_dict`, so the new fields simply appear.

- [ ] **Step 6: Commit**

```bash
git add src/flackey/models.py src/flackey/store.py tests/test_store.py
git commit -m "feat(store): first column migration, track source columns, lossless attempts and evidence tables"
```

---

### Task 4: The gate: `lossless.py` domain module

**Files:**
- Create: `src/flackey/lossless.py`
- Test: `tests/test_lossless.py`

**Interfaces:**
- Consumes: `models.norm`, `models.is_original`, `models.Candidate`, `models.CatalogTrack`, `identify.parse_version`, `config.Settings` (only in `policy_from_settings`; import inside the function is not needed, `config` is a leaf layer).
- Produces: `LosslessFile`, `Reference`, `reference_for`, `search_text`, `file_title`, `PickPolicy`, `policy_from_settings`, `Rejection`, `PickReport`, `RULES`, `RANKERS`, `pick`.

Measured on the spike's 467 lossless file names (2026-09-07): `token_set_ratio` on the file's title tokens keeps every correct pick, including album titles such as "Still Dreaming (Anything Can Happen)" and "orphic_thrench_remastered"; `token_sort_ratio` at 90 would have rejected 35 of them. Remixes and alternate mixes are the version rule's job, not the title rule's. Identity is settled later by the fingerprint (Task 7).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_lossless.py`:

```python
import json

from flackey.config import Settings
from flackey.lossless import (
    RANKERS, LosslessFile, PickPolicy, PickReport, Reference, file_title, pick, policy_from_settings,
    reference_for, search_text,
)
from flackey.models import Candidate, CatalogTrack

REF = Reference(artist="Hallucinogen", title="Orphic Thrench", mix_name="Original Mix", duration_s=442, deezer_id=6025986)


def mk(**kw) -> LosslessFile:
    base = dict(provider="soulseek", username="peer", path="Music\\Twisted\\02. Hallucinogen - Orphic Thrench.flac",
                extension="flac", size=51_223_918, length_s=442, bitrate_kbps=None, sample_rate=44100, bit_depth=16,
                has_free_slot=True, upload_speed_bps=3_000_000, queue_length=0)
    base.update(kw)
    return LosslessFile(**base)


def rejected_by(f: LosslessFile, ref: Reference = REF, policy: PickPolicy = PickPolicy()) -> str | None:
    report = pick([f], ref, policy)
    return report.rejections[0].rule if report.rejections else None


def test_file_title_strips_numbers_separators_artist_and_hash():
    assert file_title("02. Hallucinogen - Orphic Thrench.flac", "Hallucinogen") == ("orphic thrench", None)
    assert file_title("09-mindsphere--depth_of_consciousness-6920ae5a.flac", "Mindsphere") == ("depth of consciousness", None)
    assert file_title("Sun_Project_-_01_Space_Dwarfs.flac", "SUN Project") == ("space dwarfs", None)
    assert file_title("Astral Projection - 09 - Still Dreaming (Anything Can Happen).flac", "Astral Projection") == (
        "still dreaming anything can happen", None)
    # "(Rmx)" is not a version for identify.parse_version; the version rule catches it from the leftover tokens
    assert file_title("08 - Space Dwarfs (Space Tribe Rmx).flac", "SUN Project") == ("space dwarfs space tribe rmx", None)


def test_name_and_folder_split_backslash_paths():
    f = mk()
    assert f.name == "02. Hallucinogen - Orphic Thrench.flac" and f.folder == "Music\\Twisted"


def test_extension_has_length_and_size_rules():
    assert rejected_by(mk(extension="mp3")) == "extension"
    assert rejected_by(mk(length_s=None)) == "has_length"
    assert rejected_by(mk(size=1_000)) == "plausible_size"                       # 18 kbps: truncated
    assert rejected_by(mk(size=400_000_000)) == "plausible_size"                 # 7000 kbps: absurd
    assert rejected_by(mk(sample_rate=96000)) == "plausible_size"                # hi-res: no CDJ plays it
    assert rejected_by(mk(extension="wav", size=78_000_000)) is None             # 1411 kbps wav


def test_duration_rule_uses_tolerance_and_passes_without_reference_duration():
    assert rejected_by(mk(length_s=446)) == "duration"
    assert rejected_by(mk(length_s=445)) is None
    assert rejected_by(mk(length_s=300), Reference("Hallucinogen", "Orphic Thrench", "Original Mix", None)) is None


def test_title_rule_keeps_album_titles_and_rejects_other_tracks():
    dreaming = Reference("Astral Projection", "Dreaming (Anything Can Happen)", "Original Mix", 470)
    assert rejected_by(mk(path="x\\01-09. Still Dreaming (Anything Can Happen).flac", length_s=470), dreaming) is None
    assert rejected_by(mk(path="x\\08 - Still Dreaming.flac", length_s=470), dreaming) == "title"
    assert rejected_by(mk(path="x\\03 - L.S.D.flac")) == "title"
    assert rejected_by(mk(path="x\\02-hallucinogen-orphic_thrench_remastered.flac")) is None


def test_version_rule_both_directions():
    assert rejected_by(mk(path="x\\0102 - Orphic Thrench Oliver Lieb remix.flac")) == "version"
    assert rejected_by(mk(path="x\\08 - Orphic Thrench (1997 Mix).flac")) == "version"
    assert rejected_by(mk(path="x\\08 - Orphic Thrench (Space Tribe Rmx).flac")) == "version"
    assert rejected_by(mk(path="x\\08 - Orphic Thrench (Original Mix).flac")) is None
    remix = Reference("Hallucinogen", "Orphic Thrench", "Oliver Lieb Remix", 442)
    assert rejected_by(mk(path="x\\0102 - Orphic Thrench Oliver Lieb remix.flac"), remix) is None
    assert rejected_by(mk(path="x\\02. Hallucinogen - Orphic Thrench.flac"), remix) == "version"


def test_artist_queue_and_banned_rules():
    assert rejected_by(mk(path="x\\02. Orphic Thrench.flac"), policy=PickPolicy(require_artist=True)) == "artist"
    assert rejected_by(mk(path="x\\Hallucinogen\\02. Orphic Thrench.flac"), policy=PickPolicy(require_artist=True)) is None
    assert rejected_by(mk(queue_length=5), policy=PickPolicy(max_queue_length=2)) == "queue"
    assert rejected_by(mk(username="bad"), policy=PickPolicy(banned_users=frozenset({"bad"}))) == "banned_user"


def test_rankers_prefer_slot_then_16bit_then_queue_then_speed_then_size():
    a = mk(username="a", has_free_slot=False, queue_length=0)
    b = mk(username="b", bit_depth=24, size=100_000_000)
    c = mk(username="c", queue_length=3)
    d = mk(username="d", upload_speed_bps=1_000_000)
    e = mk(username="e")
    report = pick([a, b, c, d, e], REF, PickPolicy())
    assert [f.username for f in report.survivors] == ["e", "d", "c", "b", "a"]
    assert report.chosen.username == "e"
    assert [n for n, _ in RANKERS] == ["free_slot", "bit_depth", "queue_length", "upload_speed", "size"]


def test_report_summary_and_json_round_trip():
    report = pick([mk(), mk(extension="mp3", username="m"), mk(length_s=100, size=6_000_000, username="l")], REF, PickPolicy())
    assert report.seen == 3 and report.chosen.username == "peer"
    assert report.summary == "3 files: 1 extension, 1 duration; chose peer (slot, q0, flac)"
    again = PickReport.from_dict(json.loads(json.dumps(report.to_dict())))
    assert again.chosen == report.chosen and again.policy == report.policy and again.reference == report.reference
    assert [(r.rule, r.file.username) for r in again.rejections] == [("extension", "m"), ("duration", "l")]


def test_pick_never_chooses_a_rejected_file():
    files = [mk(username=f"u{i}", length_s=442 + (i % 7), extension="flac" if i % 3 else "mp3") for i in range(40)]
    report = pick(files, REF, PickPolicy())
    rejected = {r.file for r in report.rejections}
    assert report.chosen not in rejected and not (set(report.survivors) & rejected)
    assert len(report.survivors) + len(report.rejections) == 40
    assert pick([], REF, PickPolicy()).summary == "0 files: nothing left"


def test_reference_and_search_text():
    cat = CatalogTrack(id=1, artist="Astral Projection, Someone", title="Dreaming (Anything Can Happen)",
                       mix_name="Original Mix", label="L", genre="G", duration_ms=470_000)
    cand = Candidate(source="deezer_bot", source_ref="dz_track:8095320:send", artist="Astral Projection",
                     title="Dreaming (Anything Can Happen)", duration_s=471, deezer_id=8095320)
    ref = reference_for(cat, cand)
    assert ref == Reference("Astral Projection, Someone", "Dreaming (Anything Can Happen)", "Original Mix", 470, 8095320)
    assert search_text(ref) == "Astral Projection Dreaming"
    remix = Reference("Goasia", "Love & Peace", "Filteria Remix", 500)
    assert search_text(remix) == "Goasia Love & Peace Filteria Remix"
    no_cat = reference_for(None, Candidate(source="deezer_bot", source_ref="r", artist="A", title="T (Club Edit)",
                                           duration_s=3, deezer_id=9))
    assert no_cat == Reference("A", "T", "Club Edit", 3, 9)


def test_policy_from_settings(tmp_path):
    s = Settings(_env_file=None, data_dir=tmp_path, lossless_title_ratio=85, lossless_max_queue=4,
                 lossless_require_artist=True)
    p = policy_from_settings(s)
    assert (p.title_ratio, p.max_queue_length, p.require_artist, p.duration_tolerance_s) == (85, 4, True, 3)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_lossless.py -q`
Expected: `ModuleNotFoundError: No module named 'flackey.lossless'`.

- [ ] **Step 3: Implement the module**

Create `src/flackey/lossless.py`:

```python
"""The gate that turns a provider's search results into one pick, with a report that explains every
rejection (spec §6). Pure: no I/O, no clock. Rules are cheap filters against downloading the wrong file;
the fingerprint check (fingerprint.py) is what proves identity after the download."""
from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict, dataclass, field

from rapidfuzz import fuzz

from .config import Settings
from .identify import parse_version
from .models import Candidate, CatalogTrack, is_original, norm

LOSSLESS_EXTENSIONS = frozenset({"flac", "wav", "aiff", "aif"})
MAX_SAMPLE_RATE = 48_000                       # CDJs play 44.1 and 48 kHz; nothing higher gets downloaded
SIZE_KBPS = {"flac": (400, 2500), "wav": (1400, 4700), "aiff": (1400, 4700), "aif": (1400, 4700)}
VERSION_WORDS = frozenset({"remix", "rmx", "mix", "edit", "version", "dub", "rework", "bootleg", "mashup",
                           "live", "instrumental", "acoustic", "vip", "remixed"})
_TRACK_NO = re.compile(r"^\s*[\[(]?\d{1,3}[\])]?\s*[.\-_)]?\s*")
_TRAILING_HASH = re.compile(r"-[0-9a-f]{6,}$")
_PARENS = re.compile(r"\(.*?\)")


@dataclass(frozen=True)
class LosslessFile:
    provider: str
    username: str
    path: str                      # as the peer reported it, backslash separated
    extension: str
    size: int
    length_s: int | None
    bitrate_kbps: int | None
    sample_rate: int | None
    bit_depth: int | None
    has_free_slot: bool
    upload_speed_bps: int
    queue_length: int

    @property
    def name(self) -> str:
        return self.path.replace("\\", "/").rsplit("/", 1)[-1]

    @property
    def folder(self) -> str:
        parts = self.path.replace("\\", "/").rsplit("/", 1)
        return self.path[: len(self.path) - len(parts[-1]) - 1] if len(parts) == 2 else ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Reference:
    artist: str
    title: str
    mix_name: str | None
    duration_s: int | None
    deezer_id: int | None = None

    @property
    def is_original(self) -> bool:
        return self.mix_name is None or is_original(self.mix_name)


def first_artist(artist: str) -> str:
    return _PARENS.sub("", artist).split(",")[0].strip()


def reference_for(catalog: CatalogTrack | None, cand: Candidate) -> Reference:
    if catalog is not None:
        return Reference(catalog.artist, catalog.title, catalog.mix_name, catalog.duration_s or cand.duration_s,
                         cand.deezer_id)
    title, version = parse_version(cand.title)
    return Reference(cand.artist, title, cand.mix_name or version or "Original Mix", cand.duration_s, cand.deezer_id)


def search_text(ref: Reference) -> str:
    """What is typed into the network: first artist, title without parentheses, mix name only when it is a
    real version (the spike: this form found every track once the search was allowed to complete)."""
    text = f"{first_artist(ref.artist)} {_PARENS.sub('', ref.title)}"
    if not ref.is_original:
        text += f" {ref.mix_name}"
    return " ".join(text.split())


def file_title(name: str, artist: str) -> tuple[str, str | None]:
    """Normalised title tokens of a peer's file name with the artist's tokens removed, plus the version text
    `identify.parse_version` finds. Handles "02. A - T.flac", "09-a--t_x-6920ae5a.flac", "A_-_01_T.flac"."""
    stem = name.rsplit(".", 1)[0] if "." in name else name
    stem = re.sub(r"_+|--", " ", stem)
    stem = _TRAILING_HASH.sub("", _TRACK_NO.sub("", stem))
    seg = stem.split(" - ")[-1] if " - " in stem else stem
    seg = _TRACK_NO.sub("", seg)
    title, version = parse_version(seg.strip())
    artist_tokens = set(norm(artist).split())
    tokens = [t for t in norm(title).split() if t not in artist_tokens]
    return " ".join(tokens), version


@dataclass(frozen=True)
class PickPolicy:
    lossless_extensions: frozenset[str] = LOSSLESS_EXTENSIONS
    duration_tolerance_s: int = 3
    title_ratio: int = 90
    require_artist: bool = False
    max_queue_length: int | None = None
    banned_users: frozenset[str] = frozenset()

    def to_dict(self) -> dict:
        d = asdict(self)
        d["lossless_extensions"] = sorted(self.lossless_extensions)
        d["banned_users"] = sorted(self.banned_users)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> PickPolicy:
        return cls(lossless_extensions=frozenset(d["lossless_extensions"]), duration_tolerance_s=d["duration_tolerance_s"],
                   title_ratio=d["title_ratio"], require_artist=d["require_artist"],
                   max_queue_length=d["max_queue_length"], banned_users=frozenset(d["banned_users"]))


def policy_from_settings(settings: Settings) -> PickPolicy:
    return PickPolicy(duration_tolerance_s=settings.lossless_duration_tolerance_s, title_ratio=settings.lossless_title_ratio,
                      require_artist=settings.lossless_require_artist, max_queue_length=settings.lossless_max_queue)


Rule = Callable[[LosslessFile, Reference, PickPolicy], str | None]


def rule_extension(f: LosslessFile, ref: Reference, p: PickPolicy) -> str | None:
    return None if f.extension in p.lossless_extensions else f"extension {f.extension!r}"


def rule_has_length(f: LosslessFile, ref: Reference, p: PickPolicy) -> str | None:
    return "no length reported" if f.length_s is None else None


def rule_plausible_size(f: LosslessFile, ref: Reference, p: PickPolicy) -> str | None:
    if f.sample_rate and f.sample_rate > MAX_SAMPLE_RATE:
        return f"sample rate {f.sample_rate} above {MAX_SAMPLE_RATE}"
    lo, hi = SIZE_KBPS.get(f.extension, (400, 4700))
    kbps = f.size * 8 / 1000 / max(f.length_s or 1, 1)
    if not lo <= kbps <= hi:
        return f"{kbps:.0f} kbps does not fit a {f.extension} of {f.length_s} s"
    return None


def rule_duration(f: LosslessFile, ref: Reference, p: PickPolicy) -> str | None:
    if ref.duration_s is None or f.length_s is None:
        return None
    if abs(f.length_s - ref.duration_s) > p.duration_tolerance_s:
        return f"length {f.length_s} s vs {ref.duration_s} s"
    return None


def rule_title(f: LosslessFile, ref: Reference, p: PickPolicy) -> str | None:
    title, _ = file_title(f.name, ref.artist)
    score = fuzz.token_set_ratio(norm(ref.title), title)
    if score < p.title_ratio:
        return f"title {title!r} scores {score:.0f} < {p.title_ratio}"
    return None


def rule_version(f: LosslessFile, ref: Reference, p: PickPolicy) -> str | None:
    title, version = file_title(f.name, ref.artist)
    extra = [t for t in title.split() if t not in set(norm(ref.title).split())]
    words = set(norm(version or "").split()) | set(extra)
    if ref.is_original:
        hit = words & VERSION_WORDS
        if hit and "original" not in words:
            return f"looks like a version ({' '.join(sorted(hit))}) but the reference is the original"
        return None
    want = [w for w in norm(ref.mix_name).split() if w not in VERSION_WORDS]
    have = set(norm(f"{version or ''} {title}").split())
    missing = [w for w in want if w not in have]
    if missing:
        return f"version words {missing} missing for {ref.mix_name!r}"
    return None


def rule_artist(f: LosslessFile, ref: Reference, p: PickPolicy) -> str | None:
    if p.require_artist and norm(first_artist(ref.artist)) not in norm(f.path):
        return "artist not in path"
    return None


def rule_queue(f: LosslessFile, ref: Reference, p: PickPolicy) -> str | None:
    if p.max_queue_length is not None and f.queue_length > p.max_queue_length:
        return f"queue {f.queue_length} > {p.max_queue_length}"
    return None


def rule_banned_user(f: LosslessFile, ref: Reference, p: PickPolicy) -> str | None:
    return "banned user" if f.username in p.banned_users else None


RULES: list[tuple[str, Rule]] = [
    ("extension", rule_extension), ("has_length", rule_has_length), ("plausible_size", rule_plausible_size),
    ("duration", rule_duration), ("title", rule_title), ("version", rule_version), ("artist", rule_artist),
    ("queue", rule_queue), ("banned_user", rule_banned_user),
]

RANKERS: list[tuple[str, Callable[[LosslessFile], object]]] = [
    ("free_slot", lambda f: not f.has_free_slot),
    ("bit_depth", lambda f: {16: 0, 24: 1}.get(f.bit_depth or 0, 2)),   # CD master first; unknown last
    ("queue_length", lambda f: f.queue_length),
    ("upload_speed", lambda f: -f.upload_speed_bps),
    ("size", lambda f: f.size),
]


@dataclass
class Rejection:
    file: LosslessFile
    rule: str
    reason: str


@dataclass
class PickReport:
    reference: Reference
    policy: PickPolicy
    seen: int
    rejections: list[Rejection] = field(default_factory=list)
    survivors: list[LosslessFile] = field(default_factory=list)
    chosen: LosslessFile | None = None
    summary: str = ""

    def to_dict(self) -> dict:
        return {"reference": asdict(self.reference), "policy": self.policy.to_dict(), "seen": self.seen,
                "rejections": [{"file": r.file.to_dict(), "rule": r.rule, "reason": r.reason} for r in self.rejections],
                "survivors": [f.to_dict() for f in self.survivors],
                "chosen": self.chosen.to_dict() if self.chosen else None, "summary": self.summary}

    @classmethod
    def from_dict(cls, d: dict) -> PickReport:
        return cls(reference=Reference(**d["reference"]), policy=PickPolicy.from_dict(d["policy"]), seen=d["seen"],
                   rejections=[Rejection(LosslessFile(**r["file"]), r["rule"], r["reason"]) for r in d["rejections"]],
                   survivors=[LosslessFile(**f) for f in d["survivors"]],
                   chosen=LosslessFile(**d["chosen"]) if d["chosen"] else None, summary=d["summary"])


def pick(files: list[LosslessFile], ref: Reference, policy: PickPolicy,
         rules: list[tuple[str, Rule]] = RULES, rankers=RANKERS) -> PickReport:
    report = PickReport(reference=ref, policy=policy, seen=len(files))
    for f in files:
        for name, rule in rules:
            reason = rule(f, ref, policy)
            if reason:
                report.rejections.append(Rejection(f, name, reason))
                break
        else:
            report.survivors.append(f)
    report.survivors.sort(key=lambda f: tuple(fn(f) for _, fn in rankers))
    report.chosen = report.survivors[0] if report.survivors else None
    counts = Counter(r.rule for r in report.rejections)
    parts = [f"{n} {rule}" for rule, n in counts.most_common()]
    head = f"{len(files)} files: " + (", ".join(parts) if parts else "")
    if report.chosen:
        c = report.chosen
        tail = f"chose {c.username} ({'slot' if c.has_free_slot else 'no slot'}, q{c.queue_length}, {c.extension})"
    else:
        tail = "nothing left"
    report.summary = f"{head}; {tail}" if parts else f"{head}{tail}"
    return report
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_lossless.py -q`
Expected: all pass. If `test_version_rule_both_directions` fails on the "(1997 Mix)" case, check what `identify.parse_version` returns for "Orphic Thrench (1997 Mix)": the rule handles both a parsed version ("1997 Mix" contains "mix") and unparsed trailing words, so the failure would be in `file_title`, not the rule. If `test_report_summary_and_json_round_trip` fails on the summary string, check `Counter.most_common` ordering for equal counts: it preserves insertion order, and rejection order follows file order, so "1 extension, 1 duration" is right for the input given.

- [ ] **Step 5: Commit**

```bash
git add src/flackey/lossless.py tests/test_lossless.py
git commit -m "feat(lossless): rule-pipeline gate with rankers and a replayable pick report"
```

---

### Task 5: Provider protocol and the slskd adapter

**Files:**
- Create: `src/flackey/source/lossless.py`, `src/flackey/source/slskd.py`
- Modify: `src/flackey/source/__init__.py` (re-export the new names)
- Test: `tests/test_slskd.py` (uses `tests/fixtures/slskd/*.json`)

**Interfaces:**
- Consumes: `lossless.LosslessFile`.
- Produces: `LosslessProvider`, `LosslessError(msg, outcome)`, `LosslessUnavailable`, `TransferProgress`, `SlskdClient`, `SoulseekProvider`, `parse_response(resp) -> list[LosslessFile]`, `local_path_for(downloads, file) -> Path`.
- slskd facts (spec §7, fixtures): `searchTimeout` is milliseconds; `POST /searches` returns 429 while another search is being created; `GET /searches/{id}` has `state` containing `Completed` when done and `responses` empty until then; `GET /searches/{id}/responses` returns `[{username, hasFreeUploadSlot, uploadSpeed, queueLength, files: [{filename, size, extension, length, bitRate, sampleRate, bitDepth}]}]` where `extension` may be `""`; `POST /transfers/downloads/{username}` with `[{filename, size}]` returns 201 and no transfer id; `GET /transfers/downloads/{username}` returns `{username, directories: [{directory, files: [{id, filename, size, state, bytesTransferred, averageSpeed}]}]}` and `GET /transfers/downloads` a list of those; transfer states seen: `Requested`, `Queued, Remotely`, `Queued, Locally`, `Initializing`, `InProgress`, `Completed, Succeeded`, `Completed, Cancelled`, `Completed, Errored`; a completed file lands at `<downloads>/<last folder segment>/<file name>`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_slskd.py`:

```python
import json
from pathlib import Path

import httpx
import pytest
import respx

from flackey.source.lossless import LosslessError, LosslessUnavailable, TransferProgress
from flackey.source.slskd import SlskdClient, SoulseekProvider, local_path_for, parse_response

BASE = "http://slskd.test/api/v0"


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    async def sleep(self, s: float) -> None:
        self.t += s


def load(fixtures: Path, name: str):
    return json.loads((fixtures / "slskd" / name).read_text())


@pytest.fixture
def provider(tmp_path: Path):
    clock = Clock()
    http = httpx.AsyncClient()
    client = SlskdClient("http://slskd.test", "secret-key", http, sleep=clock.sleep, clock=clock)
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    return SoulseekProvider(client, downloads, sleep=clock.sleep, clock=clock), clock, downloads


def test_parse_response_derives_extension_and_peer_fields(fixtures: Path):
    resp = load(fixtures, "responses_completed.json")
    files = [f for r in resp for f in parse_response(r)]
    flacs = [f for f in files if f.extension == "flac"]
    assert len(flacs) == 7 and all(f.provider == "soulseek" for f in flacs)
    assert all(r["files"][0].get("extension", "") == "" for r in resp)      # slskd left every extension empty
    queued = next(f for f in flacs if f.username == "brunebrunberg")
    assert (queued.bit_depth, queued.sample_rate, queued.length_s) == (16, 44100, 442)
    assert queued.has_free_slot is False and queued.queue_length == 8 and queued.upload_speed_bps == 1445117
    assert queued.name == "02 Hallucinogen - Orphic Thrench.flac"


def test_local_path_is_derived_and_contained(tmp_path: Path):
    from flackey.lossless import LosslessFile
    base = dict(provider="soulseek", username="u", extension="flac", size=1, length_s=1, bitrate_kbps=None,
                sample_rate=None, bit_depth=None, has_free_slot=True, upload_speed_bps=0, queue_length=0)
    f = LosslessFile(path="Musique\\Sorted\\Albums\\Hallucinogen\\Twisted\\02. Hallucinogen - Orphic Thrench.flac", **base)
    assert local_path_for(tmp_path, f) == (tmp_path / "Twisted" / "02. Hallucinogen - Orphic Thrench.flac").resolve()
    bare = LosslessFile(path="song.flac", **base)
    assert local_path_for(tmp_path, bare) == (tmp_path / "song.flac").resolve()
    # only the last folder segment and the file name are used, so a traversal can only come from those two
    with pytest.raises(LosslessError):
        local_path_for(tmp_path, LosslessFile(path="..\\..\\x.flac", **base))          # folder segment ".."
    with pytest.raises(LosslessError):
        local_path_for(tmp_path, LosslessFile(path="C:\\Music\\..\\..\\x.flac", **base))
    with pytest.raises(LosslessError):
        local_path_for(tmp_path, LosslessFile(path="a\\..", **base))                    # name ".." resolves to the root


@respx.mock
async def test_health_reports_login_state_and_unreachable(provider, fixtures: Path):
    p, _, _ = provider
    respx.get(f"{BASE}/application").mock(return_value=httpx.Response(200, json=load(fixtures, "application.json")))
    assert await p.health() == {"status": "ok", "username": "flackey-dj"}
    app = load(fixtures, "application.json")
    app["server"]["isLoggedIn"] = False
    respx.get(f"{BASE}/application").mock(return_value=httpx.Response(200, json=app))
    assert (await p.health())["status"] == "not_logged_in"
    respx.get(f"{BASE}/application").mock(side_effect=httpx.ConnectError("refused"))
    assert (await p.health())["status"] == "unreachable"


@respx.mock
async def test_search_waits_for_completion_retries_429_and_sends_key(provider, fixtures: Path):
    p, clock, _ = provider
    sid = "eca1dc3d-5356-4fcf-9714-a17530d25655"
    post = respx.post(f"{BASE}/searches").mock(side_effect=[httpx.Response(429), httpx.Response(200, json={"id": sid})])
    state = respx.get(f"{BASE}/searches/{sid}").mock(side_effect=[
        httpx.Response(200, json=load(fixtures, "search_in_progress.json")),
        httpx.Response(200, json=load(fixtures, "search_in_progress.json")),
        httpx.Response(200, json=load(fixtures, "search_completed.json"))])
    respx.get(f"{BASE}/searches/{sid}/responses").mock(return_value=httpx.Response(200, json=load(fixtures, "responses_completed.json")))
    delete = respx.delete(f"{BASE}/searches/{sid}").mock(return_value=httpx.Response(204))
    raw = []
    files = await p.search("Hallucinogen Orphic Thrench", wait_s=30, on_raw=lambda n, o: raw.append(n))
    assert len(files) == sum(len(r["files"]) for r in load(fixtures, "responses_completed.json"))
    assert post.call_count == 2 and state.call_count == 3 and delete.called
    assert json.loads(post.calls[1].request.content) == {"searchText": "Hallucinogen Orphic Thrench", "searchTimeout": 5000,
                                                         "responseLimit": 100}
    assert post.calls[1].request.headers["X-API-Key"] == "secret-key"
    assert raw == ["search", "responses"] and clock.t >= 1.0


@respx.mock
async def test_search_gives_up_at_the_wait_cap(provider, fixtures: Path):
    p, clock, _ = provider
    sid = "s1"
    respx.post(f"{BASE}/searches").mock(return_value=httpx.Response(200, json={"id": sid}))
    respx.get(f"{BASE}/searches/{sid}").mock(return_value=httpx.Response(200, json=load(fixtures, "search_in_progress.json")))
    respx.get(f"{BASE}/searches/{sid}/responses").mock(return_value=httpx.Response(200, json=[]))
    delete = respx.delete(f"{BASE}/searches/{sid}").mock(return_value=httpx.Response(204))
    assert await p.search("x", wait_s=5) == []
    assert 5 <= clock.t < 7 and delete.called


@respx.mock
async def test_unreachable_sidecar_raises_unavailable(provider):
    p, _, _ = provider
    respx.post(f"{BASE}/searches").mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(LosslessUnavailable):
        await p.search("x", wait_s=5)
    respx.post(f"{BASE}/searches").mock(return_value=httpx.Response(502))
    with pytest.raises(LosslessUnavailable):
        await p.search("x", wait_s=5)


def transfer(state: str, done: int, size: int = 51223918, username: str = "loginty",
             filename: str = "Musique\\Sorted\\Albums\\Hallucinogen\\Twisted\\02. Hallucinogen - Orphic Thrench.flac") -> dict:
    return {"username": username, "directories": [{"directory": "Musique\\Sorted\\Albums\\Hallucinogen\\Twisted",
            "files": [{"id": "t1", "username": username, "filename": filename, "size": size, "state": state,
                       "bytesTransferred": done, "averageSpeed": 3_000_000.0}]}]}


def flac_file(size: int = 51223918):
    from flackey.lossless import LosslessFile
    return LosslessFile(provider="soulseek", username="loginty", extension="flac", size=size, length_s=442,
                        path="Musique\\Sorted\\Albums\\Hallucinogen\\Twisted\\02. Hallucinogen - Orphic Thrench.flac",
                        bitrate_kbps=None, sample_rate=44100, bit_depth=16, has_free_slot=True,
                        upload_speed_bps=3_000_000, queue_length=0)


@respx.mock
async def test_download_polls_to_completion_and_reports_progress(provider):
    p, clock, downloads = provider
    f = flac_file()
    enqueue = respx.post(f"{BASE}/transfers/downloads/loginty").mock(return_value=httpx.Response(201))
    dest = downloads / "Twisted" / "02. Hallucinogen - Orphic Thrench.flac"

    def land(_request):
        dest.parent.mkdir(exist_ok=True)
        dest.write_bytes(b"x")
        return httpx.Response(200, json=transfer("Completed, Succeeded", f.size))

    respx.get(f"{BASE}/transfers/downloads/loginty").mock(side_effect=[
        httpx.Response(200, json=transfer("Queued, Remotely", 0)),
        httpx.Response(200, json=transfer("InProgress", 1000)),
        land])
    seen: list[TransferProgress] = []
    raw = []
    got = await p.download(f, first_byte_s=60, total_s=600, poll_s=2, on_progress=seen.append, on_raw=lambda n, o: raw.append(n))
    assert got == dest.resolve()
    assert json.loads(enqueue.calls[0].request.content) == [{"filename": f.path, "size": f.size}]
    assert [s.state for s in seen] == ["Queued, Remotely", "InProgress", "Completed, Succeeded"]
    assert seen[0].first_byte_ms is None and seen[1].first_byte_ms == 4000 and seen[2].bytes == f.size
    assert raw == ["transfer-0", "transfer-1", "transfer-2"]


@respx.mock
async def test_download_first_byte_cap_cancels(provider):
    p, clock, _ = provider
    respx.post(f"{BASE}/transfers/downloads/loginty").mock(return_value=httpx.Response(201))
    respx.get(f"{BASE}/transfers/downloads/loginty").mock(return_value=httpx.Response(200, json=transfer("Queued, Remotely", 0)))
    cancel = respx.delete(f"{BASE}/transfers/downloads/loginty/t1").mock(return_value=httpx.Response(204))
    with pytest.raises(LosslessError) as e:
        await p.download(flac_file(), first_byte_s=60, total_s=600, poll_s=2)
    assert e.value.outcome == "first_byte_timeout" and cancel.called and cancel.calls[0].request.url.params["remove"] == "true"
    assert 60 <= clock.t <= 64


@respx.mock
async def test_download_total_cap_and_overrun_and_failure(provider):
    p, clock, _ = provider
    f = flac_file()
    respx.post(f"{BASE}/transfers/downloads/loginty").mock(return_value=httpx.Response(201))
    respx.delete(f"{BASE}/transfers/downloads/loginty/t1").mock(return_value=httpx.Response(204))
    respx.get(f"{BASE}/transfers/downloads/loginty").mock(return_value=httpx.Response(200, json=transfer("InProgress", 100)))
    with pytest.raises(LosslessError) as e:
        await p.download(f, first_byte_s=60, total_s=10, poll_s=2)
    assert e.value.outcome == "transfer_timeout"
    respx.get(f"{BASE}/transfers/downloads/loginty").mock(return_value=httpx.Response(200, json=transfer("InProgress", f.size + 1)))
    with pytest.raises(LosslessError) as e:
        await p.download(f, first_byte_s=60, total_s=600, poll_s=2)
    assert e.value.outcome == "transfer_failed" and "bytes" in str(e.value)
    respx.get(f"{BASE}/transfers/downloads/loginty").mock(return_value=httpx.Response(200, json=transfer("Completed, Errored", 100)))
    with pytest.raises(LosslessError) as e:
        await p.download(f, first_byte_s=60, total_s=600, poll_s=2)
    assert e.value.outcome == "transfer_failed"


@respx.mock
async def test_cancel_all_and_rescan(provider):
    p, _, _ = provider
    respx.get(f"{BASE}/transfers/downloads").mock(return_value=httpx.Response(200, json=[
        transfer("InProgress", 5), transfer("Completed, Succeeded", 10, username="other")]))
    cancel = respx.delete(f"{BASE}/transfers/downloads/loginty/t1").mock(return_value=httpx.Response(204))
    assert await p.cancel_all() == 1 and cancel.called
    rescan = respx.put(f"{BASE}/shares").mock(return_value=httpx.Response(200))
    await p.rescan_shares()
    assert rescan.called
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_slskd.py -q`
Expected: `ModuleNotFoundError: No module named 'flackey.source.lossless'`.

- [ ] **Step 3: Implement the protocol module**

Create `src/flackey/source/lossless.py`:

```python
"""What the worker talks to when it asks a network for a lossless file (spec §5.1). One implementation today
(slskd.SoulseekProvider); the gate, caps, attempt record and conversion never see anything but this."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ..lossless import LosslessFile


class LosslessError(Exception):
    """A miss with an attempt outcome attached (spec §11)."""

    def __init__(self, msg: str, outcome: str = "transfer_failed"):
        super().__init__(msg)
        self.outcome = outcome


class LosslessUnavailable(LosslessError):
    """The sidecar is down or not logged in: nothing to do with this request."""

    def __init__(self, msg: str):
        super().__init__(msg, "unavailable")


@dataclass
class TransferProgress:
    state: str
    bytes: int
    size: int
    speed_bps: float
    first_byte_ms: int | None


RawSink = Callable[[str, object], None]


class LosslessProvider(Protocol):
    name: str

    async def health(self) -> dict: ...

    async def search(self, text: str, *, wait_s: float, on_raw: RawSink | None = None) -> list[LosslessFile]: ...

    async def download(self, file: LosslessFile, *, first_byte_s: float, total_s: float, poll_s: float,
                       on_progress: Callable[[TransferProgress], None] | None = None,
                       on_raw: RawSink | None = None) -> Path: ...

    async def cancel_all(self) -> int: ...

    async def rescan_shares(self) -> None: ...
```

Add to `src/flackey/source/__init__.py`:

```python
from .lossless import LosslessError, LosslessProvider, LosslessUnavailable, TransferProgress
```

and extend `__all__` with those four names.

- [ ] **Step 4: Implement the slskd adapter**

Create `src/flackey/source/slskd.py`:

```python
"""slskd REST adapter (spec §7): a thin HTTP client and the first LosslessProvider. Facts about the API that
the spike established are in the tests' docstring and in the spec; nothing here is guessed."""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from pathlib import Path

import httpx

from ..lossless import LosslessFile
from .lossless import LosslessError, LosslessUnavailable, RawSink, TransferProgress

log = logging.getLogger(__name__)
SEARCH_IDLE_MS = 5000       # slskd's own "no new responses for this long" timeout; milliseconds
RESPONSE_LIMIT = 100
SEARCH_POLL_S = 0.5


class SlskdClient:
    def __init__(self, base_url: str, api_key: str, http: httpx.AsyncClient, *,
                 sleep=asyncio.sleep, clock: Callable[[], float] = time.monotonic):
        self.base = base_url.rstrip("/") + "/api/v0"
        self._headers = {"X-API-Key": api_key}
        self.http, self.sleep, self.clock = http, sleep, clock

    async def _call(self, method: str, path: str, *, timeout: float = 30, **kw) -> httpx.Response:
        try:
            r = await self.http.request(method, self.base + path, headers=self._headers, timeout=timeout, **kw)
        except httpx.HTTPError as e:
            raise LosslessUnavailable(f"slskd unreachable: {type(e).__name__}") from e
        if r.status_code >= 500:
            raise LosslessUnavailable(f"slskd http {r.status_code}")
        return r

    async def application(self) -> dict:
        r = await self._call("GET", "/application", timeout=5)
        return r.json()

    async def start_search(self, text: str, *, deadline: float) -> str:
        body = {"searchText": text, "searchTimeout": SEARCH_IDLE_MS, "responseLimit": RESPONSE_LIMIT}
        while True:
            r = await self._call("POST", "/searches", json=body)
            if r.status_code == 429 and self.clock() < deadline:
                await self.sleep(1)      # another search is being created; the wait shares the search budget
                continue
            if r.status_code >= 400:
                raise LosslessError(f"search rejected: http {r.status_code}", "no_pick")
            return r.json()["id"]

    async def search_state(self, sid: str) -> dict:
        return (await self._call("GET", f"/searches/{sid}")).json()

    async def search_responses(self, sid: str) -> list[dict]:
        return (await self._call("GET", f"/searches/{sid}/responses")).json()

    async def delete_search(self, sid: str) -> None:
        try:
            await self._call("DELETE", f"/searches/{sid}")
        except LosslessError:
            log.debug("could not delete search %s", sid)

    async def enqueue(self, username: str, filename: str, size: int) -> None:
        r = await self._call("POST", f"/transfers/downloads/{username}", json=[{"filename": filename, "size": size}])
        if r.status_code >= 400:
            raise LosslessError(f"enqueue rejected: http {r.status_code} {r.text[:120]}", "transfer_failed")

    async def downloads(self, username: str | None = None) -> list[dict]:
        """Every download file object slskd knows, flattened (for one user when given)."""
        r = await self._call("GET", "/transfers/downloads" + (f"/{username}" if username else ""))
        users = r.json()
        if isinstance(users, dict):
            users = [users]
        return [f for u in users for d in u.get("directories", []) for f in d.get("files", [])]

    async def cancel_download(self, username: str, transfer_id: str) -> None:
        try:
            await self._call("DELETE", f"/transfers/downloads/{username}/{transfer_id}", params={"remove": "true"})
        except LosslessError:
            log.debug("could not cancel transfer %s of %s", transfer_id, username)

    async def rescan_shares(self) -> None:
        await self._call("PUT", "/shares")


def parse_response(resp: dict) -> list[LosslessFile]:
    out = []
    for f in resp.get("files", []):
        name = f["filename"]
        ext = (f.get("extension") or (name.rsplit(".", 1)[-1] if "." in name else "")).lower()
        out.append(LosslessFile(
            provider="soulseek", username=resp["username"], path=name, extension=ext, size=int(f["size"]),
            length_s=f.get("length"), bitrate_kbps=f.get("bitRate"), sample_rate=f.get("sampleRate"),
            bit_depth=f.get("bitDepth"), has_free_slot=bool(resp.get("hasFreeUploadSlot")),
            upload_speed_bps=int(resp.get("uploadSpeed") or 0), queue_length=int(resp.get("queueLength") or 0)))
    return out


def local_path_for(downloads: Path, file: LosslessFile) -> Path:
    """Where slskd writes a completed file: <downloads>/<last remote folder segment>/<file name>. The result must
    resolve inside the downloads folder; the peer chose both strings (spec §7, §16.2)."""
    folder = file.folder.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
    candidate = downloads / folder / file.name if folder else downloads / file.name
    root, resolved = downloads.resolve(), candidate.resolve()
    if resolved == root or not resolved.is_relative_to(root):
        raise LosslessError(f"peer path escapes the downloads folder: {file.path!r}", "transfer_failed")
    return resolved


class SoulseekProvider:
    name = "soulseek"

    def __init__(self, client: SlskdClient, downloads: Path, *, sleep=asyncio.sleep,
                 clock: Callable[[], float] = time.monotonic):
        self.client, self.downloads, self.sleep, self.clock = client, downloads, sleep, clock

    async def health(self) -> dict:
        try:
            app = await self.client.application()
        except LosslessUnavailable:
            return {"status": "unreachable", "username": None}
        server = app.get("server") or {}
        return {"status": "ok" if server.get("isLoggedIn") else "not_logged_in",
                "username": (app.get("user") or {}).get("username")}

    async def search(self, text: str, *, wait_s: float, on_raw: RawSink | None = None) -> list[LosslessFile]:
        deadline = self.clock() + wait_s
        sid = await self.client.start_search(text, deadline=deadline)
        try:
            state = await self.client.search_state(sid)
            while "Completed" not in state.get("state", "") and self.clock() < deadline:
                await self.sleep(SEARCH_POLL_S)
                state = await self.client.search_state(sid)
            if on_raw:
                on_raw("search", state)
            responses = await self.client.search_responses(sid)   # empty file lists until Completed (spike)
            if on_raw:
                on_raw("responses", responses)
        finally:
            await self.client.delete_search(sid)
        return [f for r in responses for f in parse_response(r)]

    async def _find(self, file: LosslessFile) -> dict | None:
        for t in await self.client.downloads(file.username):
            if t.get("filename") == file.path:
                return t
        return None

    async def download(self, file: LosslessFile, *, first_byte_s: float, total_s: float, poll_s: float,
                       on_progress: Callable[[TransferProgress], None] | None = None,
                       on_raw: RawSink | None = None) -> Path:
        dest = local_path_for(self.downloads, file)     # containment before anything is enqueued
        await self.client.enqueue(file.username, file.path, file.size)
        t0, first_byte_at, last_state, n = self.clock(), None, None, 0
        while True:
            await self.sleep(poll_s)
            tr = await self._find(file)
            now = self.clock()
            if tr is None:
                if now - t0 > first_byte_s:
                    raise LosslessError("transfer never appeared in slskd", "first_byte_timeout")
                continue
            done, state = int(tr.get("bytesTransferred") or 0), str(tr.get("state") or "")
            if done > 0 and first_byte_at is None:
                first_byte_at = now
            first_byte_ms = None if first_byte_at is None else int((first_byte_at - t0) * 1000)
            if state != last_state:
                if on_raw:
                    on_raw(f"transfer-{n}", tr)
                n, last_state = n + 1, state
            if on_progress:
                on_progress(TransferProgress(state, done, file.size, float(tr.get("averageSpeed") or 0), first_byte_ms))
            if done > file.size:
                await self.client.cancel_download(file.username, tr["id"])
                raise LosslessError(f"peer sent {done} bytes for a {file.size} byte file", "transfer_failed")
            if state.startswith("Completed"):
                if "Succeeded" in state and dest.exists():
                    return dest
                raise LosslessError(f"transfer ended {state}" if "Succeeded" not in state
                                    else "completed but no file at the derived path", "transfer_failed")
            if first_byte_at is None and now - t0 > first_byte_s:
                await self.client.cancel_download(file.username, tr["id"])
                raise LosslessError(f"no bytes within {first_byte_s:.0f} s", "first_byte_timeout")
            if now - t0 > total_s:
                await self.client.cancel_download(file.username, tr["id"])
                raise LosslessError(f"not finished within {total_s:.0f} s", "transfer_timeout")

    async def cancel_all(self) -> int:
        n = 0
        for t in await self.client.downloads():
            if not str(t.get("state", "")).startswith("Completed"):
                await self.client.cancel_download(t["username"], t["id"])
                n += 1
        return n

    async def rescan_shares(self) -> None:
        await self.client.rescan_shares()
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_slskd.py -q`
Expected: all pass. `pytest-asyncio` is configured in `pyproject.toml` (`asyncio_mode = "auto"`); check `[tool.pytest.ini_options]` if the async tests are reported as skipped.

- [ ] **Step 6: Commit**

```bash
git add src/flackey/source/lossless.py src/flackey/source/slskd.py src/flackey/source/__init__.py tests/test_slskd.py tests/fixtures/slskd
git commit -m "feat(source): lossless provider protocol and the slskd adapter with caps, containment and fixtures"
```

---

### Task 6: Conversion: `convert.py`

**Files:**
- Create: `src/flackey/convert.py`
- Test: `tests/test_convert.py`

**Interfaces:**
- Consumes: nothing from earlier tasks (ffmpeg only).
- Produces: `to_format(src: Path, fmt: str, bit_depth: int | None) -> Path` writing `<src stem>.<fmt>` next to `src`; `ConvertError(Exception)`; `CODECS` table.

Spec §9: the peer's file is never filed as-is when the filing format is AIFF or WAV. ffmpeg decodes the audio stream only (`-vn -map 0:a`), drops every tag and picture the peer left (`-map_metadata -1`), and writes PCM at the source bit depth (16 or 24; anything else becomes 24). The spike proved the decoded PCM is byte-identical before and after (`docs/research/2026-09-07-soulseek-spike-findings.md`, conversion section). Tags are written afterwards from Beatport by the existing `tag.write_tags`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_convert.py`:

```python
import hashlib
import json
import subprocess
from pathlib import Path

import pytest
from mutagen.flac import FLAC, Picture

from flackey.convert import ConvertError, to_format
from flackey.verify import probe
from tests.conftest import requires_ffmpeg

pytestmark = requires_ffmpeg


def decode(path: Path) -> tuple[str, int]:
    """(md5 of the decoded stereo s32 PCM, samples per channel): the audio, independent of container."""
    out = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-vn", "-map", "0:a", "-f", "s32le", "-ac", "2", "-"],
                         capture_output=True, check=True).stdout
    return hashlib.md5(out).hexdigest(), len(out) // 8


def streams(path: Path) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
                         capture_output=True, check=True).stdout
    return json.loads(out)


@pytest.fixture(scope="module")
def tagged_flac(tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("convert")
    wav = d / "noise.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "anoisesrc=color=pink:seed=7:duration=3:sample_rate=44100",
                    "-ac", "2", "-c:a", "pcm_s16le", str(wav)], check=True)
    flac = d / "noise.flac"
    subprocess.run(["ffmpeg", "-v", "error", "-i", str(wav), "-c:a", "flac", str(flac)], check=True)
    f = FLAC(flac)
    f["title"], f["artist"], f["comment"] = ["Peer Title"], ["Peer Artist"], ["ripped by someone"]
    pic = Picture()
    pic.type, pic.mime, pic.data = 3, "image/png", bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c6360000002000155"
        "0d0a2f0000000049454e44ae426082")
    f.add_picture(pic)
    f.save()
    return flac


def test_aiff_has_identical_audio_and_no_foreign_metadata(tagged_flac: Path):
    out = to_format(tagged_flac, "aiff", 16)
    assert out == tagged_flac.with_suffix(".aiff") and out.exists()
    assert decode(out) == decode(tagged_flac)
    info = streams(out)
    assert probe(out).fmt == "aiff" and probe(out).bit_depth == 16
    assert [s["codec_type"] for s in info["streams"]] == ["audio"]          # the picture stream is gone
    tags = {k.lower() for k in (info["format"].get("tags") or {})}
    assert not tags & {"title", "artist", "comment"}


def test_wav_and_24_bit_codecs(tagged_flac: Path):
    wav = to_format(tagged_flac, "wav", None)
    assert probe(wav).fmt == "wav" and decode(wav) == decode(tagged_flac)
    hi = to_format(tagged_flac, "aiff", 24)
    assert probe(hi).bit_depth == 24 and probe(hi).fmt == "aiff"


def test_flac_target_is_a_no_op(tagged_flac: Path):
    assert to_format(tagged_flac, "flac", 16) == tagged_flac


def test_bad_input_raises(tmp_path: Path):
    junk = tmp_path / "junk.flac"
    junk.write_bytes(b"not audio")
    with pytest.raises(ConvertError):
        to_format(junk, "aiff", 16)
    with pytest.raises(ConvertError):
        to_format(junk, "mp3", 16)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_convert.py -q`
Expected: `ModuleNotFoundError: No module named 'flackey.convert'`.

- [ ] **Step 3: Implement**

Create `src/flackey/convert.py`:

```python
"""Lossless-to-lossless conversion for filing (spec §9). Audio only, metadata dropped, PCM at the source
bit depth; the spike proved the decoded samples are identical before and after."""
from __future__ import annotations

import logging
import subprocess
import time
from pathlib import Path

log = logging.getLogger(__name__)
RUN_TIMEOUT_S = 300
CODECS = {("aiff", 16): "pcm_s16be", ("aiff", 24): "pcm_s24be", ("wav", 16): "pcm_s16le", ("wav", 24): "pcm_s24le"}


class ConvertError(Exception):
    pass


def to_format(src: Path, fmt: str, bit_depth: int | None) -> Path:
    """Write `<src stem>.<fmt>` next to `src` and return it. `fmt` "flac" returns `src` untouched."""
    if fmt == "flac":
        return src
    bits = 16 if bit_depth in (None, 16) else 24
    codec = CODECS.get((fmt, bits))
    if codec is None:
        raise ConvertError(f"no codec for {fmt} at {bits} bit")
    dst = src.with_suffix(f".{fmt}")
    cmd = ["ffmpeg", "-v", "error", "-y", "-i", str(src), "-vn", "-map", "0:a", "-map_metadata", "-1",
           "-c:a", codec, str(dst)]
    t0 = time.monotonic()
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=RUN_TIMEOUT_S)
    except subprocess.TimeoutExpired as e:
        dst.unlink(missing_ok=True)
        raise ConvertError(f"ffmpeg timed out after {RUN_TIMEOUT_S}s") from e
    if p.returncode != 0 or not dst.exists():
        dst.unlink(missing_ok=True)
        raise ConvertError(p.stderr.decode(errors="replace")[-400:] or "ffmpeg produced no file")
    log.info("converted %s -> %s (%s) in %.1f s", src.name, dst.name, codec, time.monotonic() - t0)
    return dst
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_convert.py -q`
Expected: 4 passed. If `test_aiff_has_identical_audio_and_no_foreign_metadata` fails on the picture stream, ffmpeg kept the attached picture as a video stream: confirm the command has both `-vn` and `-map 0:a` (either alone leaves it in some versions).

- [ ] **Step 5: Commit**

```bash
git add src/flackey/convert.py tests/test_convert.py
git commit -m "feat(convert): lossless to AIFF/WAV with metadata dropped and PCM proven identical"
```

---

### Task 7: Same-recording check: `fingerprint.py` and the Deezer preview URL

**Files:**
- Create: `src/flackey/fingerprint.py`
- Modify: `src/flackey/deezer.py` (`DeezerTrack.preview_url`, `parse_track_json`), `tests/conftest.py` (`requires_fpcalc`)
- Test: `tests/test_fingerprint.py`, `tests/test_deezer.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `FingerprintError`, `FPS = 8.06`, `SUBFRAME_TRIMS_S = (0.0, 0.031, 0.062, 0.093)`, `fpcalc_available() -> bool`, `fingerprint(path, start_s=0.0) -> list[int]`, `compare(needle, hay) -> tuple[float, int]`, `FingerprintResult(status, score, offset_s, reason, preview, track)` with `.to_dict()` (no raw lists), `async check(path, deezer_id, http, *, minimum, tmp_dir) -> FingerprintResult`, `DeezerTrack.preview_url`.

Spec §16.1 and the findings doc: Chromaprint raw fingerprints (`fpcalc -raw -json -length 0`) are 32-bit ints at 8.06 frames per second. The Deezer 30 s preview, fingerprinted at four sub-frame trims, is slid along the whole track; the best bit-agreement is the score. Measured: correct pairs 0.97 to 0.99, wrong pairs 0.53 to 0.76, threshold 0.90. The check needs `fpcalc` (Homebrew `chromaprint`) and one Deezer API call plus one CDN download; when either is missing the status is `skipped`, never `failed`, and the worker still files (spec §16.1).

- [ ] **Step 1: Write the failing tests**

Add to `tests/conftest.py`:

```python
def has_fpcalc() -> bool:
    return shutil.which("fpcalc") is not None


requires_fpcalc = pytest.mark.skipif(not has_fpcalc(), reason="fpcalc (chromaprint) not installed")
```

Add to `tests/test_deezer.py`:

```python
def test_parse_track_json_keeps_preview_url():
    t = parse_track_json({"id": 1, "title": "T", "artist": {"name": "A"}, "duration": 3, "preview": "https://cdn/x.mp3"})
    assert t.preview_url == "https://cdn/x.mp3"
    assert parse_track_json({"id": 1, "title": "T", "duration": 3, "preview": ""}).preview_url is None
```

Create `tests/test_fingerprint.py`:

```python
import itertools
import json
import subprocess
from pathlib import Path

import httpx
import pytest
import respx

from flackey import fingerprint as fp
from flackey.fingerprint import FPS, FingerprintError, FingerprintResult, check, compare, fingerprint
from tests.conftest import requires_ffmpeg, requires_fpcalc


@pytest.fixture(scope="module")
def pairs(fixtures: Path) -> dict:
    return json.loads((fixtures / "fingerprints.json").read_text())


def test_compare_finds_each_preview_in_its_own_track(pairs: dict):
    for tid, p in pairs.items():
        score, offset = compare(p["preview"], p["track_window"])
        assert score >= 0.93, (tid, score)
        assert abs(offset - p["expected_offset_in_window"]) <= 1, (tid, offset)
        assert abs(score - p["expected_score"]) < 0.02


def test_compare_rejects_every_cross_pair(pairs: dict):
    for a, b in itertools.permutations(pairs, 2):
        score, _ = compare(pairs[a]["preview"], pairs[b]["track_window"])
        assert score < 0.80, (a, b, score)


def test_compare_edge_cases():
    assert compare([], [1, 2, 3]) == (0.0, -1)
    assert compare([1, 2, 3], [1, 2]) == (0.0, -1)          # needle longer than hay: nothing to slide
    assert compare([7, 7], [1, 7, 7, 1]) == (1.0, 1)


@requires_ffmpeg
@requires_fpcalc
def test_real_fpcalc_locates_a_cut_at_the_right_offset(tmp_path: Path):
    whole = tmp_path / "whole.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "anoisesrc=color=pink:seed=3:duration=40:sample_rate=44100",
                    "-ac", "2", str(whole)], check=True)
    cut = tmp_path / "cut.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-ss", "20", "-t", "10", "-i", str(whole), str(cut)], check=True)
    hay = fingerprint(whole)
    assert 300 <= len(hay) <= 330                      # 40 s at 8.06 frames/s
    best = max(compare(fingerprint(cut, s), hay) for s in fp.SUBFRAME_TRIMS_S)
    assert best[0] >= 0.85 and abs(best[1] / FPS - 20) < 0.5


def test_fingerprint_raises_when_fpcalc_is_missing(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(fp.shutil, "which", lambda _: None)
    with pytest.raises(FingerprintError):
        fingerprint(tmp_path / "x.wav")


@pytest.fixture
def fake_fpcalc(monkeypatch, pairs: dict):
    """Route `fingerprint()` to the recorded fingerprints by file name: `<id>-preview.mp3` or `<id>-track.flac`."""
    monkeypatch.setattr(fp, "fpcalc_available", lambda: True)

    def fake(path: Path, start_s: float = 0.0) -> list[int]:
        if path.name.startswith("preview-"):                       # check() names the download preview-<deezer id>.mp3
            deezer_id = int(path.stem.split("-")[1])
            return next(p["preview"] for p in pairs.values() if p["deezer_id"] == deezer_id)
        return pairs[path.stem.split("-")[0]]["track_window"]

    monkeypatch.setattr(fp, "fingerprint", fake)
    return fake


@respx.mock
async def test_check_matches_and_fails_by_threshold(tmp_path: Path, fake_fpcalc):
    respx.get("https://api.deezer.com/track/6025986").mock(return_value=httpx.Response(200, json={
        "id": 6025986, "title": "Orphic Thrench", "duration": 442, "preview": "https://cdn.test/6-preview.mp3"}))
    respx.get("https://cdn.test/6-preview.mp3").mock(return_value=httpx.Response(200, content=b"mp3"))
    async with httpx.AsyncClient() as http:
        ok = await check(tmp_path / "6-track.flac", 6025986, http, minimum=0.90, tmp_dir=tmp_path)
        assert ok.status == "matched" and ok.score >= 0.93 and ok.offset_s == pytest.approx(240 / FPS, abs=0.2)
        assert ok.preview and ok.track and "preview" not in ok.to_dict() and ok.to_dict()["score"] == ok.score
        bad = await check(tmp_path / "8-track.flac", 6025986, http, minimum=0.90, tmp_dir=tmp_path)
        assert bad.status == "failed" and bad.score < 0.80 and "below 0.90" in bad.reason
    assert not list(tmp_path.glob("preview-*"))         # the downloaded preview is removed


@respx.mock
async def test_check_is_skipped_when_deezer_or_fpcalc_is_unavailable(tmp_path: Path, fake_fpcalc, monkeypatch):
    api = respx.get("https://api.deezer.com/track/1").mock(return_value=httpx.Response(503))
    async with httpx.AsyncClient() as http:
        r = await check(tmp_path / "6-track.flac", 1, http, minimum=0.9, tmp_dir=tmp_path)
        assert r.status == "skipped" and "503" in r.reason and api.call_count == 3
        respx.get("https://api.deezer.com/track/2").mock(return_value=httpx.Response(200, json={"id": 2, "title": "T", "duration": 1, "preview": ""}))
        r = await check(tmp_path / "6-track.flac", 2, http, minimum=0.9, tmp_dir=tmp_path)
        assert r.status == "skipped" and "no preview" in r.reason
        r = await check(tmp_path / "6-track.flac", None, http, minimum=0.9, tmp_dir=tmp_path)
        assert r.status == "skipped" and "deezer id" in r.reason
        monkeypatch.setattr(fp, "fpcalc_available", lambda: False)
        r = await check(tmp_path / "6-track.flac", 6025986, http, minimum=0.9, tmp_dir=tmp_path)
        assert r.status == "skipped" and "fpcalc" in r.reason


@respx.mock
async def test_check_is_skipped_when_fpcalc_fails(tmp_path: Path, fake_fpcalc, monkeypatch):
    respx.get("https://api.deezer.com/track/6025986").mock(return_value=httpx.Response(200, json={
        "id": 6025986, "title": "T", "duration": 442, "preview": "https://cdn.test/p.mp3"}))
    respx.get("https://cdn.test/p.mp3").mock(return_value=httpx.Response(200, content=b"mp3"))

    def boom(path, start_s=0.0):
        raise FingerprintError("fpcalc timed out")

    monkeypatch.setattr(fp, "fingerprint", boom)
    async with httpx.AsyncClient() as http:
        r = await check(tmp_path / "6-track.flac", 6025986, http, minimum=0.9, tmp_dir=tmp_path)
    assert r.status == "skipped" and r.reason == "fpcalc timed out"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_fingerprint.py tests/test_deezer.py -q`
Expected: import errors for `flackey.fingerprint` and `requires_fpcalc`; `test_parse_track_json_keeps_preview_url` fails with `AttributeError: preview_url`.

- [ ] **Step 3: Implement the Deezer change**

In `src/flackey/deezer.py`, add a trailing field to `DeezerTrack`:

```python
    preview_url: str | None = None
```

and in `parse_track_json` add `preview_url=data.get("preview") or None,` to the constructor call.

- [ ] **Step 4: Implement the module**

Create `src/flackey/fingerprint.py`:

```python
"""Same-recording check (spec §16.1): Chromaprint raw fingerprints of the Deezer 30 s preview slid along the
downloaded file. Numbers measured in the spike: 8.06 frames/s, correct pairs score 0.97-0.99, wrong 0.53-0.76."""
from __future__ import annotations

import asyncio
import json
import logging
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

import httpx
import numpy as np

log = logging.getLogger(__name__)
FPS = 8.06                                        # chromaprint hop: 1365 samples at 11025 Hz
SUBFRAME_TRIMS_S = (0.0, 0.031, 0.062, 0.093)     # a quarter frame each; the preview's cut is never frame-aligned
FPCALC_TIMEOUT_S = 120
DEEZER_TRACK = "https://api.deezer.com/track/{id}"
DEEZER_TRIES = 3


class FingerprintError(Exception):
    pass


def fpcalc_available() -> bool:
    return shutil.which("fpcalc") is not None


def fingerprint(path: Path, start_s: float = 0.0) -> list[int]:
    """Raw 32-bit frames for the whole file, optionally starting `start_s` seconds in (via an ffmpeg trim)."""
    if not fpcalc_available():
        raise FingerprintError("fpcalc not installed (brew install chromaprint)")
    src = path
    tmp = None
    try:
        if start_s:
            tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
            tmp.close()
            src = Path(tmp.name)
            r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{start_s}", "-i", str(path), "-vn", "-map", "0:a",
                                str(src)], capture_output=True, timeout=FPCALC_TIMEOUT_S)
            if r.returncode != 0:
                raise FingerprintError(r.stderr.decode(errors="replace")[-300:])
        r = subprocess.run(["fpcalc", "-raw", "-json", "-length", "0", str(src)], capture_output=True, timeout=FPCALC_TIMEOUT_S)
        if r.returncode != 0:
            raise FingerprintError(r.stderr.decode(errors="replace")[-300:] or "fpcalc failed")
        return [int(x) for x in json.loads(r.stdout)["fingerprint"]]
    except subprocess.TimeoutExpired as e:
        raise FingerprintError(f"{e.cmd[0]} timed out after {FPCALC_TIMEOUT_S}s") from e
    finally:
        if tmp is not None:
            Path(tmp.name).unlink(missing_ok=True)


def compare(needle: list[int], hay: list[int]) -> tuple[float, int]:
    """Best (1 - mean bit disagreement) over every offset of `needle` inside `hay`, and that offset in frames."""
    n = len(needle)
    if n == 0 or len(hay) < n:
        return 0.0, -1
    a = np.asarray(needle, dtype=np.int64).astype(np.uint32)
    h = np.asarray(hay, dtype=np.int64).astype(np.uint32)
    windows = np.lib.stride_tricks.sliding_window_view(h, n)           # (offsets, n)
    diff = np.bitwise_xor(windows, a)
    bits = np.unpackbits(diff.view(np.uint8), axis=-1).sum(axis=-1)    # popcount per window
    off = int(bits.argmin())
    return float(1 - bits[off] / (32 * n)), off


@dataclass
class FingerprintResult:
    status: str                      # "matched" | "failed" | "skipped"
    score: float | None
    offset_s: float | None
    reason: str
    preview: list[int] | None = None
    track: list[int] | None = None

    def to_dict(self) -> dict:
        return {"status": self.status, "score": self.score, "offset_s": self.offset_s, "reason": self.reason,
                "preview_frames": len(self.preview) if self.preview else None,
                "track_frames": len(self.track) if self.track else None}


async def _preview_url(deezer_id: int, http: httpx.AsyncClient) -> str | None:
    last = "no response"
    for i in range(DEEZER_TRIES):
        try:
            r = await http.get(DEEZER_TRACK.format(id=deezer_id), timeout=20)
        except httpx.HTTPError as e:
            last = type(e).__name__
        else:
            if r.status_code == 200:
                return r.json().get("preview") or None
            last = f"deezer http {r.status_code}"
            if r.status_code < 500:
                break
        if i < DEEZER_TRIES - 1:
            await asyncio.sleep(0)
    raise FingerprintError(last)


async def check(path: Path, deezer_id: int | None, http: httpx.AsyncClient, *, minimum: float,
                tmp_dir: Path) -> FingerprintResult:
    """Never raises. `skipped` when the check cannot run; `failed` only when it ran and the score is low."""
    if deezer_id is None:
        return FingerprintResult("skipped", None, None, "no deezer id for this request")
    if not fpcalc_available():
        return FingerprintResult("skipped", None, None, "fpcalc not installed")
    preview = tmp_dir / f"preview-{deezer_id}.mp3"
    try:
        url = await _preview_url(deezer_id, http)
        if not url:
            return FingerprintResult("skipped", None, None, "deezer has no preview for this track")
        r = await http.get(url, timeout=30, follow_redirects=True)
        if r.status_code != 200 or not r.content:
            return FingerprintResult("skipped", None, None, f"preview download http {r.status_code}")
        preview.write_bytes(r.content)
        track_fp = await asyncio.to_thread(fingerprint, path)
        best, best_pv = (0.0, -1), None
        for trim in SUBFRAME_TRIMS_S:
            pv = await asyncio.to_thread(fingerprint, preview, trim)
            got = compare(pv, track_fp)
            if got > best:
                best, best_pv = got, pv
        score, offset = best
        offset_s = round(offset / FPS, 1) if offset >= 0 else None
        if score >= minimum:
            return FingerprintResult("matched", round(score, 3), offset_s, f"preview found at {offset_s} s, score {score:.2f}",
                                     best_pv, track_fp)
        return FingerprintResult("failed", round(score, 3), offset_s, f"best score {score:.2f} below {minimum:.2f}",
                                 best_pv, track_fp)
    except (FingerprintError, httpx.HTTPError, OSError, ValueError) as e:
        return FingerprintResult("skipped", None, None, str(e) or type(e).__name__)
    finally:
        preview.unlink(missing_ok=True)
```

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_fingerprint.py tests/test_deezer.py -q`
Expected: all pass; the real-fpcalc test is skipped on a machine without `fpcalc`. If `test_compare_finds_each_preview_in_its_own_track` fails on `expected_score`, the fixture's preview was recorded at its best sub-frame trim and `compare` on that single alignment must reproduce the stored score within 0.02; check the `uint32` cast (negative ints from `fpcalc` must wrap, which `astype(np.uint32)` from `int64` does).

- [ ] **Step 6: Commit**

```bash
git add src/flackey/fingerprint.py src/flackey/deezer.py tests/conftest.py tests/test_fingerprint.py tests/test_deezer.py tests/fixtures/fingerprints.json
git commit -m "feat(fingerprint): Chromaprint same-recording check against the Deezer preview"
```

---

### Task 8: The attempt record: `attempts.py`

**Files:**
- Create: `src/flackey/attempts.py`
- Test: `tests/test_attempts.py`

**Interfaces:**
- Consumes: `store.Store.add_attempt/update_attempt/get_attempt` (Task 3), `models.ATTEMPT_OUTCOMES`.
- Produces: `AttemptRecorder(store, raw_dir, request_id, provider, query, clock=time.monotonic)` with `.id`, `.event(name, **detail)`, `.raw(name, obj)`, `.finish(outcome, **cols)`, `.elapsed_ms()`, `.dir`; `prune_raw(raw_dir, keep_days, now=time.time) -> int`; `raw_size_bytes(raw_dir) -> int`.

Spec §17: one INFO line per phase prefixed `req=<id> slsk=<attempt id>`; the timeline is written to the row at every event so a crash leaves a partial record; raw slskd objects go to `<raw_dir>/<attempt id>/<name>.json`; folders older than `keep_days` are pruned once a day. The key never reaches this module.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_attempts.py`:

```python
import json
import logging
import os
import time
from pathlib import Path

import pytest

from flackey.attempts import AttemptRecorder, prune_raw, raw_size_bytes
from flackey.models import RequestKind
from flackey.store import Store


@pytest.fixture
def store(tmp_path: Path) -> Store:
    s = Store(tmp_path / "db.sqlite")
    s.add_request("x", RequestKind.TEXT)       # request id 1
    return s


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def test_recorder_creates_row_and_folder_and_writes_timeline(store: Store, tmp_path: Path, caplog):
    clock = Clock()
    raw = tmp_path / "raw"
    with caplog.at_level(logging.INFO, logger="flackey.attempts"):
        rec = AttemptRecorder(store, raw, 1, "soulseek", "Hallucinogen Orphic Thrench", clock=clock)
        clock.t += 1.5
        rec.event("search_completed", responses=12, files=40)
    row = store.get_attempt(rec.id)
    assert row.request_id == 1 and row.provider == "soulseek" and row.query == "Hallucinogen Orphic Thrench"
    assert row.raw_dir == str(raw / str(rec.id)) and rec.dir.is_dir()
    assert row.timeline == [{"t_ms": 1500, "event": "search_completed", "detail": {"responses": 12, "files": 40}}]
    assert f"req=1 slsk={rec.id} search_completed responses=12 files=40" in caplog.text
    assert rec.elapsed_ms() == 1500


def test_raw_files_are_numbered_and_json(store: Store, tmp_path: Path):
    rec = AttemptRecorder(store, tmp_path / "raw", 1, "soulseek", "q")
    rec.raw("search", {"state": "Completed"})
    rec.raw("responses", [{"username": "u"}])
    rec.raw("fingerprint", {"preview": [1, 2], "track": [3]})
    names = sorted(p.name for p in rec.dir.iterdir())
    assert names == ["01-search.json", "02-responses.json", "03-fingerprint.json"]
    assert json.loads((rec.dir / "02-responses.json").read_text()) == [{"username": "u"}]


def test_finish_validates_outcome_and_copies_columns(store: Store, tmp_path: Path, caplog):
    clock = Clock()
    rec = AttemptRecorder(store, tmp_path / "raw", 1, "soulseek", "q", clock=clock)
    clock.t += 3
    with caplog.at_level(logging.INFO, logger="flackey.attempts"):
        rec.finish("filed", first_byte_ms=800, fingerprint={"status": "matched", "score": 0.98},
                   spectrogram_path="/tmp/s.png", report={"summary": "3 files"})
    row = store.get_attempt(rec.id)
    assert (row.outcome, row.total_ms, row.first_byte_ms) == ("filed", 3000, 800)
    assert row.fingerprint == {"status": "matched", "score": 0.98} and row.report == {"summary": "3 files"}
    assert row.timeline[-1]["event"] == "outcome" and row.timeline[-1]["detail"] == {"outcome": "filed"}
    assert f"req=1 slsk={rec.id} outcome=filed total_ms=3000" in caplog.text
    with pytest.raises(ValueError):
        rec.finish("nonsense")


def test_raw_write_failure_does_not_break_the_attempt(store: Store, tmp_path: Path):
    rec = AttemptRecorder(store, tmp_path / "raw", 1, "soulseek", "q")
    rec.raw("search", {"x": float("nan")})           # not strict JSON but json.dumps allows it
    rec.raw("bytes", object())                         # unserialisable: logged, not raised
    assert (rec.dir / "01-search.json").exists() and not (rec.dir / "02-bytes.json").exists()


def test_prune_raw_removes_old_folders_only(tmp_path: Path):
    raw = tmp_path / "raw"
    for i, age_days in enumerate((1, 31, 45), start=1):
        d = raw / str(i)
        d.mkdir(parents=True)
        (d / "01-search.json").write_text("{}")
        old = time.time() - age_days * 86400
        os.utime(d, (old, old))
    (raw / "stray.txt").write_text("keep")
    assert raw_size_bytes(raw) == 2 * 3 + 4
    assert prune_raw(raw, keep_days=30) == 2
    assert sorted(p.name for p in raw.iterdir()) == ["1", "stray.txt"]
    assert prune_raw(tmp_path / "missing", keep_days=30) == 0 and raw_size_bytes(tmp_path / "missing") == 0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_attempts.py -q`
Expected: `ModuleNotFoundError: No module named 'flackey.attempts'`.

- [ ] **Step 3: Implement**

Create `src/flackey/attempts.py`:

```python
"""The record of one lossless attempt (spec §17): log lines, a timeline persisted on every event, raw provider
objects on disk, and the outcome row. Pruning of old raw folders lives here too."""
from __future__ import annotations

import json
import logging
import shutil
import time
from collections.abc import Callable
from pathlib import Path

from .models import ATTEMPT_OUTCOMES
from .store import Store

log = logging.getLogger(__name__)


class AttemptRecorder:
    def __init__(self, store: Store, raw_dir: Path, request_id: int, provider: str, query: str,
                 clock: Callable[[], float] = time.monotonic):
        self.store, self.request_id, self.provider, self.query, self.clock = store, request_id, provider, query, clock
        self.t0 = clock()
        self.timeline: list[dict] = []
        self._seq = 0
        self.id = store.add_attempt(request_id, provider, query)
        self.dir = raw_dir / str(self.id)
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            log.warning("req=%d slsk=%d cannot create raw folder %s: %s", request_id, self.id, self.dir, e)
        store.update_attempt(self.id, raw_dir=str(self.dir))

    def elapsed_ms(self) -> int:
        return int((self.clock() - self.t0) * 1000)

    def event(self, name: str, **detail) -> None:
        self.timeline.append({"t_ms": self.elapsed_ms(), "event": name, "detail": detail})
        log.info("req=%d slsk=%d %s%s", self.request_id, self.id, name,
                 "".join(f" {k}={v}" for k, v in detail.items()))
        self.store.update_attempt(self.id, timeline=self.timeline)

    def raw(self, name: str, obj: object) -> None:
        """Write the provider's object exactly as received; a failure here never fails the attempt."""
        self._seq += 1
        path = self.dir / f"{self._seq:02d}-{name}.json"
        try:
            path.write_text(json.dumps(obj, ensure_ascii=False))
        except (OSError, TypeError, ValueError) as e:
            log.warning("req=%d slsk=%d could not write %s: %s", self.request_id, self.id, path.name, e)

    def finish(self, outcome: str, **cols) -> None:
        if outcome not in ATTEMPT_OUTCOMES:
            raise ValueError(f"unknown attempt outcome {outcome!r}")
        total = self.elapsed_ms()
        self.timeline.append({"t_ms": total, "event": "outcome", "detail": {"outcome": outcome}})
        self.store.update_attempt(self.id, outcome=outcome, total_ms=total, timeline=self.timeline, **cols)
        log.info("req=%d slsk=%d outcome=%s total_ms=%d", self.request_id, self.id, outcome, total)


def prune_raw(raw_dir: Path, keep_days: int, now: Callable[[], float] = time.time) -> int:
    """Delete attempt folders whose mtime is older than `keep_days`. Rows are untouched (spec §17.3)."""
    if not raw_dir.is_dir():
        return 0
    cutoff = now() - keep_days * 86400
    n = 0
    for d in raw_dir.iterdir():
        if d.is_dir() and d.name.isdigit() and d.stat().st_mtime < cutoff:
            shutil.rmtree(d, ignore_errors=True)
            n += 1
    if n:
        log.info("pruned %d lossless attempt folders older than %d days", n, keep_days)
    return n


def raw_size_bytes(raw_dir: Path) -> int:
    if not raw_dir.is_dir():
        return 0
    return sum(p.stat().st_size for p in raw_dir.rglob("*") if p.is_file())
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_attempts.py -q`
Expected: 5 passed. If `test_raw_write_failure_does_not_break_the_attempt` fails because `json.dumps(object())` produced a file, the `TypeError` is raised before `write_text` runs, so no file exists; check that the sequence number is still consumed (the test expects `02-bytes.json` absent, not `02-search.json`).

- [ ] **Step 5: Commit**

```bash
git add src/flackey/attempts.py tests/test_attempts.py
git commit -m "feat(attempts): attempt recorder with timeline, raw provider objects and daily pruning"
```

---

### Task 9: The worker step `_try_lossless`

**Files:**
- Modify: `src/flackey/worker.py`, `src/flackey/tag.py` (`comment_for`/`write_tags` gain `source`), `src/flackey/models.py` (`source_label`)
- Test: `tests/test_worker_lossless.py` (new file; the existing `tests/test_worker.py` keeps passing), `tests/test_tag.py` (two expected strings)

**Interfaces:**
- Consumes: everything under "Shared interfaces": `lossless.reference_for/search_text/policy_from_settings/pick`, `source.lossless.LosslessProvider/LosslessError/TransferProgress`, `convert.to_format/ConvertError`, `fingerprint.check/FingerprintResult/FPS`, `attempts.AttemptRecorder/prune_raw`, store attempt and evidence methods, `Settings.lossless_enabled` and the `lossless_*` fields, `Verdict.bit_depth/sample_rate`, `verify.probe/VerifyError`.
- Produces: `Worker(..., providers: list[LosslessProvider] | None = None, http: httpx.AsyncClient | None = None, clock=time.monotonic)`; `Worker.startup()` (async: `on_start()` + cancel open downloads + first maintenance); `Worker._maintenance(force=False)`; `Worker._try_lossless(req, cand, catalog) -> LosslessHit | None`; `LosslessHit(path, verdict, fingerprint, provider, source_fmt, attempt_id)`; `format_line(verdict, source, source_fmt) -> str`; `models.source_label(name) -> str`; `tag.write_tags(..., source="deezer_bot")`; `status["lossless_provider"]` = `{"name", "status", "username"}` after every health check.

Spec §5 (flow), §8 (caps), §9 (conversion inside the attempt, FLAC deleted after conversion), §11 (no candidate rows, no rejection rows for Soulseek misses), §12 (restart handling), §17 (timeline events), §18 (evidence rows). The re-entry rule: a request runs a new attempt only when it has no attempt row or its last outcome is `unavailable` or `interrupted`. The second pick (spec §6.2): after `verify_failed` or `fingerprint_failed` only, when more than `lossless_first_byte_s` of the total budget (search wait + first byte + transfer caps) is left, up to `lossless_max_picks` picks.

- [ ] **Step 1: Write the failing tests**

Update the two expected comment strings in `tests/test_tag.py` (lines 52 and 81) to end with ` · via Deezer`:

```python
    assert t["comment"] == "flackey: verified 320 kbps · cutoff 19.8 kHz · beatport 16552105 · via Deezer"
    ...
    assert comment_for(V, CT) == "flackey: verified 320 kbps · cutoff 19.8 kHz · beatport 16552105 · via Deezer"
```

Create `tests/test_worker_lossless.py`:

```python
"""The worker's lossless step with a fake provider. Every test asserts the attempt row's outcome, that the
timeline ends with the outcome event, that tmp_dir is empty afterwards, and which source the file came from."""
import shutil
import subprocess
from pathlib import Path

import httpx
import pytest

from flackey.config import Settings
from flackey.convert import ConvertError
from flackey.fingerprint import FingerprintResult
from flackey.lossless import LosslessFile
from flackey.models import CatalogTrack, RequestKind, RequestState
from flackey.notify import MemoryNotifier
from flackey.source import LosslessError, SourceTimeout, TransferProgress
from flackey.store import Store
from flackey import worker as worker_mod
from flackey.worker import Worker, format_line
from tests.conftest import requires_ffmpeg
from tests.test_worker import CT, TEXT, FakeCatalog, FakeSource, _mp3, good_cand, no_art

pytestmark = requires_ffmpeg
CT3 = CatalogTrack(**{**CT.__dict__, "duration_ms": 3000})


def _flac(path: Path, seconds: int = 3) -> Path:
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    f"anoisesrc=color=white:seed=2:sample_rate=44100:duration={seconds}", "-ac", "2", "-c:a", "flac",
                    str(path)], check=True)
    return path


def _fake_flac(path: Path) -> Path:
    """A lossy source inside a FLAC container: verify rejects it (cutoff far below 20 kHz)."""
    mp3 = _mp3(path.with_suffix(".mp3"), kbps=64)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(mp3), "-c:a", "flac", str(path)], check=True)
    mp3.unlink()
    return path


def lf(username: str = "a", **kw) -> LosslessFile:
    base = dict(provider="soulseek", username=username, path="Void\\02. Astral Projection - Into the Void.flac",
                extension="flac", size=320_000, length_s=3, bitrate_kbps=None, sample_rate=44100, bit_depth=16,
                has_free_slot=True, upload_speed_bps=2_000_000, queue_length=0)
    base.update(kw)
    return LosslessFile(**base)


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class FakeProvider:
    name = "soulseek"

    def __init__(self, downloads: Path, files=None, *, audio: dict[str, Path] | None = None, health="ok",
                 search_error=None, download_error=None):
        self.downloads, self.files, self.audio = downloads, files or [], audio or {}
        self.health_status, self.search_error, self.download_error = health, search_error, download_error
        self.searches, self.downloaded, self.cancelled, self.rescans = [], [], 0, 0

    async def health(self):
        return {"status": self.health_status, "username": "flackey-dj"}

    async def search(self, text, *, wait_s, on_raw=None):
        self.searches.append(text)
        if self.search_error:
            raise self.search_error
        if on_raw:
            on_raw("search", {"state": "Completed, TimedOut", "fileCount": len(self.files)})
            on_raw("responses", [{"username": f.username, "files": [{"filename": f.path, "size": f.size}]} for f in self.files])
        return list(self.files)

    async def download(self, file, *, first_byte_s, total_s, poll_s, on_progress=None, on_raw=None):
        self.downloaded.append(file.username)
        if on_progress:
            on_progress(TransferProgress("Queued, Remotely", 0, file.size, 0.0, None))
        if self.download_error:
            raise self.download_error
        dest = self.downloads / "Void" / file.name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(self.audio[file.username], dest)
        if on_progress:
            on_progress(TransferProgress("InProgress", 10, file.size, 1e6, 800))
            on_progress(TransferProgress("Completed, Succeeded", file.size, file.size, 1e6, 800))
        if on_raw:
            on_raw("transfer-0", {"state": "Completed, Succeeded"})
        return dest

    async def cancel_all(self):
        self.cancelled += 1
        return 0

    async def rescan_shares(self):
        self.rescans += 1


@pytest.fixture
def lenv(tmp_path: Path, monkeypatch):
    settings = Settings(_env_file=None, telegram_api_id=1, telegram_api_hash="h", library_root=tmp_path / "lib",
                        data_dir=tmp_path / "data", slskd_api_key="k", lossless_poll_s=0.01, lossless_search_wait_s=5,
                        lossless_first_byte_s=10, lossless_transfer_s=20)
    store = Store(settings.db_path)
    downloads = settings.slskd_downloads
    downloads.mkdir(parents=True)
    good = _flac(tmp_path / "good.flac")
    provider = FakeProvider(downloads, [lf("a")], audio={"a": good, "b": good})
    matched = FingerprintResult("matched", 0.98, 12.3, "preview found at 12.3 s, score 0.98", [1, 2, 3], [4, 5, 6])

    async def fake_check(path, deezer_id, http, *, minimum, tmp_dir):
        return fake_check.result

    fake_check.result = matched
    monkeypatch.setattr(worker_mod, "fingerprint_check", fake_check)
    return settings, store, MemoryNotifier(), provider, fake_check, Clock()


def make(lenv, provider=None, source=None, catalog=None, **kw):
    settings, store, notifier, default_provider, _, clock = lenv
    providers = kw.pop("providers", [provider or default_provider])
    return Worker(store, source or FakeSource([good_cand()]), catalog or FakeCatalog([CT3]), notifier, settings,
                  artwork_fetch=no_art, providers=providers, http=httpx.AsyncClient(), clock=clock, **kw)


def attempt_of(store: Store, rid: int):
    a = store.get_attempt_for_request(rid)
    assert a is not None and a.timeline[-1]["event"] == "outcome" and a.timeline[-1]["detail"]["outcome"] == a.outcome
    return a


async def test_hit_files_aiff_with_evidence_done_line_and_raw_record(lenv):
    settings, store, notifier, provider, _, _ = lenv
    w = make(lenv)
    rid = store.add_request(TEXT, RequestKind.TEXT)
    r = await w.process(rid)
    assert r.state == RequestState.DONE and r.fetch_source is None
    t = store.get_track(r.track_id)
    assert t.path.suffix == ".aiff" and t.path.exists() and (t.source, t.source_fmt, t.bit_depth, t.sample_rate) == (
        "soulseek", "flac", 16, 44100)
    assert t.fmt == "aiff"
    assert w.source.fetched == []                               # Deezer never asked
    a = attempt_of(store, rid)
    assert a.outcome == "filed" and a.first_byte_ms == 800 and a.total_ms is not None
    assert a.report["chosen"]["username"] == "a" and a.fingerprint["status"] == "matched"
    assert [e["event"] for e in a.timeline][:7] == ["search_started", "search_completed", "pick", "enqueue",
                                                     "transfer_state", "transfer_state", "first_byte"]
    assert {"verify", "fingerprint", "convert"} <= {e["event"] for e in a.timeline}
    assert sorted(p.name for p in Path(a.raw_dir).iterdir()) == ["01-search.json", "02-responses.json", "03-transfer-0.json",
                                                                  "04-fingerprint.json"]
    assert not list(settings.tmp_dir.iterdir())
    assert not list(settings.slskd_downloads.rglob("*.flac"))   # moved out of the sidecar's folder
    kinds = {e.kind: e.value for e in store.list_evidence(t.id)}
    assert kinds["source"] == {"provider": "soulseek", "source_fmt": "flac", "attempt_id": a.id}
    assert kinds["recording_match"]["score"] == 0.98 and kinds["recording_match"]["reference"] == "deezer:1754956977"
    assert kinds["fingerprint"]["frames"] == [4, 5, 6]
    done = notifier.sent[-1][0]
    assert "AIFF 16-bit/44.1 kHz, from FLAC via Soulseek" in done and "content to" in done and "kHz" in done
    assert store.stats()["by_source"] == {"soulseek": 1}
    assert w.status["lossless_provider"] == {"name": "soulseek", "status": "ok", "username": "flackey-dj"}


async def test_no_pick_falls_back_to_deezer(lenv):
    settings, store, notifier, provider, _, _ = lenv
    provider.files = [lf("a", extension="mp3")]
    w = make(lenv)
    rid = store.add_request(TEXT, RequestKind.TEXT)
    r = await w.process(rid)
    assert r.state == RequestState.DONE and w.source.fetched == ["dz_track:1754956977:send"]
    assert attempt_of(store, rid).outcome == "no_pick" and provider.downloaded == []
    t = store.get_track(r.track_id)
    assert (t.source, t.source_fmt, t.path.suffix) == ("deezer_bot", None, ".mp3")
    assert "MP3 320 kbps via Deezer" in notifier.sent[-1][0]
    assert not list(settings.tmp_dir.iterdir()) and len(store.get_candidates(rid)) == 1


async def test_provider_down_is_unavailable_and_costs_no_search(lenv):
    settings, store, _, provider, _, _ = lenv
    provider.health_status = "unreachable"
    w = make(lenv)
    rid = store.add_request(TEXT, RequestKind.TEXT)
    r = await w.process(rid)
    assert r.state == RequestState.DONE and provider.searches == [] and attempt_of(store, rid).outcome == "unavailable"
    assert w.status["lossless_provider"]["status"] == "unreachable"


@pytest.mark.parametrize("outcome", ["first_byte_timeout", "transfer_timeout", "transfer_failed"])
async def test_download_failures_fall_back_with_their_outcome(lenv, outcome):
    settings, store, _, provider, _, _ = lenv
    provider.files = [lf("a"), lf("b")]
    provider.download_error = LosslessError("boom", outcome)
    w = make(lenv)
    rid = store.add_request(TEXT, RequestKind.TEXT)
    r = await w.process(rid)
    assert r.state == RequestState.DONE and store.get_track(r.track_id).source == "deezer_bot"
    a = attempt_of(store, rid)
    assert a.outcome == outcome and provider.downloaded == ["a"]          # download failures do not try pick 2
    assert not list(settings.tmp_dir.iterdir())


async def test_verify_failure_tries_the_second_pick(lenv, tmp_path: Path):
    settings, store, _, provider, _, _ = lenv
    provider.files = [lf("a"), lf("b", queue_length=1)]
    provider.audio["a"] = _fake_flac(tmp_path / "fake.flac")
    w = make(lenv)
    rid = store.add_request(TEXT, RequestKind.TEXT)
    r = await w.process(rid)
    a = attempt_of(store, rid)
    assert a.outcome == "filed" and provider.downloaded == ["a", "b"]
    assert [e["detail"]["pick"] for e in a.timeline if e["event"] == "enqueue"] == [1, 2]
    assert store.get_track(r.track_id).source == "soulseek" and a.spectrogram_path
    assert not list(settings.tmp_dir.iterdir())


async def test_verify_failure_on_every_pick_falls_back_without_a_rejection_row(lenv, tmp_path: Path):
    settings, store, _, provider, _, _ = lenv
    fake = _fake_flac(tmp_path / "fake.flac")
    provider.files = [lf("a"), lf("b"), lf("c")]
    provider.audio = {"a": fake, "b": fake, "c": fake}
    w = make(lenv)
    rid = store.add_request(TEXT, RequestKind.TEXT)
    r = await w.process(rid)
    assert r.state == RequestState.DONE and store.get_track(r.track_id).source == "deezer_bot"
    a = attempt_of(store, rid)
    assert a.outcome == "verify_failed" and provider.downloaded == ["a", "b"]      # lossless_max_picks = 2
    assert store.get_rejection_for_request(rid) is None
    assert not list(settings.tmp_dir.iterdir())


async def test_second_pick_needs_budget(lenv, tmp_path: Path):
    settings, store, _, provider, _, clock = lenv
    provider.files = [lf("a"), lf("b")]
    provider.audio["a"] = _fake_flac(tmp_path / "fake.flac")
    original = provider.download

    async def slow(file, **kw):
        clock.t += 28                                         # 35 s budget - 10 s first-byte cap = 25 s: gone
        return await original(file, **kw)

    provider.download = slow
    w = make(lenv)
    rid = store.add_request(TEXT, RequestKind.TEXT)
    await w.process(rid)
    a = attempt_of(store, rid)
    assert a.outcome == "verify_failed" and provider.downloaded == ["a"]
    assert any(e["event"] == "budget_exhausted" for e in a.timeline)


async def test_fingerprint_failure_falls_back_and_keeps_the_fingerprints(lenv):
    settings, store, _, provider, fake_check, _ = lenv
    fake_check.result = FingerprintResult("failed", 0.61, 40.0, "best score 0.61 below 0.90", [9], [8])
    w = make(lenv)
    rid = store.add_request(TEXT, RequestKind.TEXT)
    r = await w.process(rid)
    a = attempt_of(store, rid)
    assert a.outcome == "fingerprint_failed" and a.fingerprint["score"] == 0.61
    assert (Path(a.raw_dir) / "04-fingerprint.json").read_text() == '{"preview": [9], "track": [8]}'
    assert store.get_track(r.track_id).source == "deezer_bot" and not list(settings.tmp_dir.iterdir())


async def test_fingerprint_skipped_still_files_and_says_so(lenv):
    settings, store, _, provider, fake_check, _ = lenv
    fake_check.result = FingerprintResult("skipped", None, None, "fpcalc not installed")
    w = make(lenv)
    rid = store.add_request(TEXT, RequestKind.TEXT)
    r = await w.process(rid)
    assert attempt_of(store, rid).outcome == "filed"
    kinds = {e.kind: e.value for e in store.list_evidence(r.track_id)}
    assert kinds["recording_match"] == {"status": "skipped", "score": None, "offset_s": None,
                                        "reference": "deezer:1754956977", "reason": "fpcalc not installed"}
    assert "fingerprint" not in kinds


async def test_convert_failure_falls_back(lenv, monkeypatch):
    settings, store, _, provider, _, _ = lenv

    def boom(src, fmt, bit_depth):
        raise ConvertError("ffmpeg exploded")

    monkeypatch.setattr(worker_mod, "to_format", boom)
    w = make(lenv)
    rid = store.add_request(TEXT, RequestKind.TEXT)
    r = await w.process(rid)
    assert attempt_of(store, rid).outcome == "convert_failed" and store.get_track(r.track_id).source == "deezer_bot"
    assert not list(settings.tmp_dir.iterdir())


async def test_flac_filing_format_keeps_the_file(lenv):
    settings, store, notifier, provider, _, _ = lenv
    settings.lossless_filing_format = "flac"
    w = make(lenv)
    rid = store.add_request(TEXT, RequestKind.TEXT)
    r = await w.process(rid)
    t = store.get_track(r.track_id)
    assert t.path.suffix == ".flac" and (t.fmt, t.source_fmt) == ("flac", "flac")
    assert "FLAC 16-bit/44.1 kHz via Soulseek" in notifier.sent[-1][0]
    assert not list(settings.tmp_dir.iterdir())


async def test_miss_then_deezer_failure_runs_no_second_attempt(lenv):
    settings, store, _, provider, _, _ = lenv
    provider.files = [lf("a", extension="mp3")]
    source = FakeSource([good_cand()], fetch_error=SourceTimeout("slow"))
    w = make(lenv, source=source)
    rid = store.add_request(TEXT, RequestKind.TEXT)
    r = await w.process(rid)
    assert r.state == RequestState.QUEUED and r.attempts == 1 and r.fetch_source is None
    store.update_request(rid, retry_after=None)
    r = await w.process(rid)
    assert r.attempts == 2 and len(provider.searches) == 1          # the miss is remembered; no second attempt
    assert len(store.list_attempts()) == 1 and len(store.get_candidates(rid)) == 1


async def test_unexpected_error_inside_the_attempt_is_recorded(lenv):
    settings, store, _, provider, _, _ = lenv
    provider.search_error = RuntimeError("bug")
    w = make(lenv)
    rid = store.add_request(TEXT, RequestKind.TEXT)
    r = await w.process(rid)
    a = attempt_of(store, rid)
    assert r.state == RequestState.DONE and a.outcome == "transfer_failed"
    assert any(e["event"] == "error" and "bug" in e["detail"]["message"] for e in a.timeline)


async def test_startup_marks_open_attempts_interrupted_cancels_and_rescans(lenv):
    settings, store, _, provider, _, _ = lenv
    rid = store.add_request(TEXT, RequestKind.TEXT)
    open_id = store.add_attempt(rid, "soulseek", "q")
    w = make(lenv)
    await w.startup()
    assert store.get_attempt(open_id).outcome == "interrupted" and provider.cancelled == 1 and provider.rescans == 1
    r = await w.process(rid)                                   # interrupted does not block re-entry
    assert store.get_attempt_for_request(rid).outcome == "filed" and r.state == RequestState.DONE


async def test_maintenance_runs_once_a_day(lenv, tmp_path: Path):
    settings, store, _, provider, _, clock = lenv
    old = settings.lossless_raw_dir / "1"
    old.mkdir(parents=True)
    import os
    import time
    os.utime(old, (time.time() - 40 * 86400,) * 2)
    w = make(lenv)
    await w._maintenance(force=True)
    assert not old.exists() and provider.rescans == 1
    await w._maintenance()
    assert provider.rescans == 1
    clock.t += 86_401
    await w._maintenance()
    assert provider.rescans == 2


async def test_lossless_is_off_without_a_key_or_providers(lenv, tmp_path: Path):
    settings, store, _, provider, _, _ = lenv
    w = make(lenv, providers=[])
    rid = store.add_request(TEXT, RequestKind.TEXT)
    assert (await w.process(rid)).state == RequestState.DONE and store.list_attempts() == []
    settings.slskd_api_key = None
    w = make(lenv)
    rid = store.add_request(TEXT, RequestKind.TEXT)
    assert (await w.process(rid)).state == RequestState.DONE and store.list_attempts() == [] and provider.searches == []


def test_format_line():
    from flackey.models import Verdict
    assert format_line(Verdict(True, "mp3", 320, 19500, "r"), "deezer_bot", None) == "MP3 320 kbps via Deezer"
    v = Verdict(True, "aiff", 1411, 22050, "r", bit_depth=16, sample_rate=44100)
    assert format_line(v, "soulseek", "flac") == "AIFF 16-bit/44.1 kHz, from FLAC via Soulseek"
    assert format_line(v, "soulseek", "aiff") == "AIFF 16-bit/44.1 kHz via Soulseek"
    assert format_line(Verdict(True, "wav", 2304, 22050, "r", bit_depth=24, sample_rate=48000), "soulseek", "flac") == (
        "WAV 24-bit/48 kHz, from FLAC via Soulseek")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_worker_lossless.py tests/test_tag.py -q`
Expected: `ImportError: cannot import name 'format_line'` and the two comment-string failures.

- [ ] **Step 3: `models.source_label` and the tag comment**

In `src/flackey/models.py`, near the top-level helpers:

```python
SOURCE_LABELS = {"deezer_bot": "Deezer", "soulseek": "Soulseek"}


def source_label(name: str) -> str:
    """How a source is named to the owner: the provider name is an identifier, not copy."""
    return SOURCE_LABELS.get(name, name)
```

In `src/flackey/tag.py`:

```python
def comment_for(verdict: Verdict, catalog: CatalogTrack, source: str = "deezer_bot") -> str:
    kind = f"verified {verdict.bitrate_kbps} kbps" if verdict.fmt == "mp3" else f"verified {verdict.fmt}"
    return (f"flackey: {kind} · cutoff {verdict.cutoff_hz / 1000:.1f} kHz · beatport {catalog.id}"
            f" · via {source_label(source)}")
```

`_values(catalog, verdict, source)` passes `source` through to `comment_for`, and `write_tags` gains a trailing keyword `source: str = "deezer_bot"` that it forwards to `_values`. Import `source_label` from `.models`.

- [ ] **Step 4: Implement the worker changes**

Imports at the top of `src/flackey/worker.py` (add to the existing ones):

```python
import shutil
import time
from dataclasses import dataclass, replace

import httpx

from .attempts import AttemptRecorder, prune_raw
from .convert import ConvertError, to_format
from .fingerprint import FPS, FingerprintResult
from .fingerprint import check as fingerprint_check
from .lossless import Reference, pick, policy_from_settings, reference_for, search_text
from .models import Candidate, CatalogTrack, Query, Request, RequestState, Verdict, source_label
from .source import LosslessError, LosslessProvider, Source, SourceError, SourceNotFound, SourceTimeout, SourceUnauthorized, TransferProgress
from .verify import VerifyError, probe, verify
```

Module-level additions:

```python
RETRY_LOSSLESS_OUTCOMES = {"unavailable", "interrupted"}   # spec §5: only these let a request try again
SECOND_PICK_AFTER = {"verify_failed", "fingerprint_failed"}
MAINTENANCE_EVERY_S = 86_400


@dataclass
class LosslessHit:
    path: Path                 # converted (or FLAC when filing as flac) file in tmp_dir; the only temp file left
    verdict: Verdict           # cutoff from the FLAC verify; fmt, bitrate, bit depth, sample rate re-probed after conversion
    fingerprint: FingerprintResult
    provider: str
    source_fmt: str
    attempt_id: int


def format_line(verdict: Verdict, source: str, source_fmt: str | None) -> str:
    """The end-user's view of what was downloaded (spec §4): `AIFF 16-bit/44.1 kHz, from FLAC via Soulseek`."""
    if verdict.fmt == "mp3":
        head = f"MP3 {verdict.bitrate_kbps} kbps"
    else:
        head = verdict.fmt.upper()
        if verdict.bit_depth:
            head += f" {verdict.bit_depth}-bit"
        if verdict.sample_rate:
            head += f"/{verdict.sample_rate / 1000:g} kHz"
        if source_fmt and source_fmt != verdict.fmt:
            head += f", from {source_fmt.upper()}"
    return f"{head} via {source_label(source)}"
```

Constructor:

```python
    def __init__(self, store: Store, source: Source, catalog: CatalogLike, notifier: Notifier,
                 settings: Settings,
                 artwork_fetch: Callable[[str], Awaitable[bytes | None]] = fetch_artwork,
                 status: dict | None = None,
                 providers: list[LosslessProvider] | None = None,
                 http: httpx.AsyncClient | None = None,
                 clock: Callable[[], float] = time.monotonic):
        ... existing lines ...
        self.providers = list(providers or [])
        self.http = http or httpx.AsyncClient(timeout=20)
        self.clock = clock
        self._last_maintenance: float | None = None
```

Lifecycle:

```python
    def on_start(self) -> None:
        ... existing body ...
        n = self.store.mark_open_attempts_interrupted()
        if n:
            log.info("marked %d unfinished lossless attempts interrupted", n)

    async def startup(self) -> None:
        """on_start plus the async parts: cancel transfers the sidecar kept running, then the first maintenance."""
        self.on_start()
        for p in self.providers:
            try:
                n = await p.cancel_all()
                if n:
                    log.info("cancelled %d %s downloads left from the previous run", n, p.name)
            except LosslessError as e:
                log.warning("%s: could not cancel old downloads: %s", p.name, e)
        await self._maintenance(force=True)

    async def _maintenance(self, force: bool = False) -> None:
        """Daily: prune raw attempt folders and ask providers to rescan the shared library (spec §7, §17.3)."""
        now = self.clock()
        if not force and self._last_maintenance is not None and now - self._last_maintenance < MAINTENANCE_EVERY_S:
            return
        self._last_maintenance = now
        await asyncio.to_thread(prune_raw, self.settings.lossless_raw_dir, self.settings.lossless_keep_raw_days)
        for p in self.providers:
            try:
                await p.rescan_shares()
            except LosslessError as e:
                log.warning("%s: rescan failed: %s", p.name, e)

    async def run_forever(self, poll_s: float = 2.0) -> None:
        await self.startup()
        while self.status.get("telegram_authorized", True):
            await self._maintenance()
            req = self.store.next_queued()
            ...unchanged...
```

`_fetch_verify_file` becomes:

```python
    async def _fetch_verify_file(self, req: Request, cand: Candidate, catalog: CatalogTrack | None) -> None:
        dup = find_duplicate(self.store, catalog, cand)  # again: a reviewed request may have waited for hours
        if dup:
            await self._mark_duplicate(req, dup.id, dup.path)
            return
        self._set_state(req, RequestState.FETCHING)
        hit = await self._try_lossless(req, cand, catalog) if self._lossless_allowed(req) else None
        if hit is None:
            self.store.update_request(req.id, fetch_source=self.source.name)
            try:
                tmp = await self.source.fetch(cand, self.settings.tmp_dir)
            except SourceUnauthorized:
                self.store.update_request(req.id, fetch_source=None)
                raise
            except (SourceTimeout, SourceError) as e:
                self.store.update_request(req.id, fetch_source=None)
                await self._retry_or_fail(req, f"source error: {e}")
                return
        else:
            tmp = hit.path
        try:
            await self._verify_and_file(req, cand, catalog, tmp, hit)
        finally:
            tmp.unlink(missing_ok=True)  # gone already when file_track moved it; garbage in every other outcome
            self.store.update_request(req.id, fetch_source=None)

    def _lossless_allowed(self, req: Request) -> bool:
        if not self.providers or not self.settings.lossless_enabled:
            return False
        last = self.store.get_attempt_for_request(req.id)
        return last is None or last.outcome in RETRY_LOSSLESS_OUTCOMES
```

`_verify_and_file` signature and body changes (the rest stays as it is):

```python
    async def _verify_and_file(self, req: Request, cand: Candidate, catalog: CatalogTrack | None, tmp: Path,
                               hit: LosslessHit | None = None) -> None:
        if hit is None:
            self._set_state(req, RequestState.VERIFYING)
            verdict = await asyncio.to_thread(verify, tmp, self.settings.spectrogram_dir, f"req{req.id}-{tmp.stem}")
            if not verdict.passed:
                ...unchanged rejection block...
                return
        else:
            verdict = hit.verdict                     # verified and fingerprinted inside the attempt
        source = hit.provider if hit else self.source.name
        source_fmt = hit.source_fmt if hit else None
        ...
        await asyncio.to_thread(write_tags, tmp, catalog, verdict, artwork, source=source)
        ...
        track_id = self.store.add_track(
            ...existing keywords..., spectrogram_path=verdict.spectrogram_path,
            source=source, source_fmt=source_fmt, bit_depth=verdict.bit_depth, sample_rate=verdict.sample_rate)
        if hit:
            self._record_evidence(track_id, hit, cand)
        ...
        parts = [f"Done: {catalog.artist} – {catalog.title} ({catalog.mix_name})", _mmss(catalog.duration_s or cand.duration_s),
                 format_line(verdict, source, source_fmt), f"content to {verdict.cutoff_hz / 1000:.1f} kHz",
                 f"{catalog.genre} / {catalog.label}"]
        if conf is not None:
            parts.append(f"{conf}%")
        await self.notifier.send(" · ".join(parts))

    def _record_evidence(self, track_id: int, hit: LosslessHit, cand: Candidate) -> None:
        fp = hit.fingerprint
        self.store.add_evidence(track_id, "source", {"provider": hit.provider, "source_fmt": hit.source_fmt,
                                                     "attempt_id": hit.attempt_id})
        self.store.add_evidence(track_id, "recording_match", {"status": fp.status, "score": fp.score, "offset_s": fp.offset_s,
                                                              "reference": f"deezer:{cand.deezer_id}", "reason": fp.reason})
        if fp.track:
            self.store.add_evidence(track_id, "fingerprint", {"frames": fp.track, "fps": FPS})
```

The `verdict.reason` that used to sit in the Done line is replaced by the format line plus the cutoff; check `tests/test_worker.py` for assertions on the old text (`grep -n "genuine\|content to" tests/test_worker.py`) and update them to the new wording if any exist.

The attempt itself:

```python
    # ---- lossless ---------------------------------------------------------
    async def _try_lossless(self, req: Request, cand: Candidate, catalog: CatalogTrack | None) -> LosslessHit | None:
        """Ask each provider in turn for a verified, fingerprinted, converted lossless file. Never raises;
        every miss is an attempt row with an outcome (spec §5, §11)."""
        ref = reference_for(catalog, cand)
        query = search_text(ref)
        policy = policy_from_settings(self.settings)
        for provider in self.providers:
            rec = AttemptRecorder(self.store, self.settings.lossless_raw_dir, req.id, provider.name, query, clock=self.clock)
            self.store.update_request(req.id, fetch_source=provider.name)
            try:
                hit = await self._attempt(provider, rec, req, ref, policy)
            except Exception as e:  # noqa: BLE001 - a bug in the lossless path must not take the request down
                log.exception("req#%d lossless attempt %d crashed", req.id, rec.id)
                rec.event("error", type=type(e).__name__, message=str(e)[:300])
                rec.finish("transfer_failed")
                hit = None
            if hit is not None:
                return hit
        return None

    async def _attempt(self, provider: LosslessProvider, rec: AttemptRecorder, req: Request, ref: Reference,
                       policy) -> LosslessHit | None:
        s = self.settings
        health = await provider.health()
        self.status["lossless_provider"] = {"name": provider.name, **health}
        if health.get("status") != "ok":
            rec.event("unavailable", status=health.get("status"))
            rec.finish("unavailable")
            return None
        rec.event("search_started", query=rec.query)
        try:
            files = await provider.search(rec.query, wait_s=s.lossless_search_wait_s, on_raw=rec.raw)
        except LosslessError as e:
            rec.event("search_failed", error=str(e))
            rec.finish(e.outcome)
            return None
        rec.event("search_completed", files=len(files), peers=len({f.username for f in files}))
        report = pick(files, ref, policy)
        self.store.update_attempt(rec.id, report=report.to_dict())
        rec.event("pick", summary=report.summary)
        if report.chosen is None:
            rec.finish("no_pick")
            return None
        budget_s = s.lossless_search_wait_s + s.lossless_first_byte_s + s.lossless_transfer_s
        outcome = "no_pick"
        for n, file in enumerate(report.survivors[:s.lossless_max_picks], start=1):
            if n > 1 and rec.elapsed_ms() / 1000 > budget_s - s.lossless_first_byte_s:
                rec.event("budget_exhausted", pick=n)
                break
            hit, outcome = await self._download_and_check(provider, rec, req, ref, file, n)
            if hit is not None:
                rec.finish("filed")
                return hit
            if outcome not in SECOND_PICK_AFTER:
                break
        rec.finish(outcome)
        return None

    async def _download_and_check(self, provider: LosslessProvider, rec: AttemptRecorder, req: Request, ref: Reference,
                                  file, n: int) -> tuple[LosslessHit | None, str]:
        s = self.settings
        rec.event("enqueue", pick=n, peer=file.username, file=file.name, size=file.size)
        seen = {"state": None, "first_byte": False}

        def progress(p: TransferProgress) -> None:
            if p.state != seen["state"]:
                seen["state"] = p.state
                rec.event("transfer_state", state=p.state, pct=round(100 * p.bytes / max(p.size, 1)),
                          speed_kbps=round(p.speed_bps / 1000))
            if p.first_byte_ms is not None and not seen["first_byte"]:
                seen["first_byte"] = True
                rec.event("first_byte", ms=p.first_byte_ms)
                self.store.update_attempt(rec.id, first_byte_ms=p.first_byte_ms)

        try:
            landed = await provider.download(file, first_byte_s=s.lossless_first_byte_s, total_s=s.lossless_transfer_s,
                                             poll_s=s.lossless_poll_s, on_progress=progress, on_raw=rec.raw)
        except LosslessError as e:
            rec.event("transfer_failed", error=str(e))
            return None, e.outcome
        rec.event("completed", ms=rec.elapsed_ms(), bytes=landed.stat().st_size)
        s.tmp_dir.mkdir(parents=True, exist_ok=True)
        tmp = s.tmp_dir / f"req{req.id}-lossless.{file.extension}"    # never the peer's file name (spec §16.2)
        try:
            shutil.move(str(landed), str(tmp))
        except OSError as e:
            rec.event("move_failed", error=str(e))
            landed.unlink(missing_ok=True)
            return None, "transfer_failed"
        hit, outcome = await self._check_and_convert(rec, req, ref, file, tmp)
        if hit is None:
            tmp.unlink(missing_ok=True)
        return hit, outcome

    async def _check_and_convert(self, rec: AttemptRecorder, req: Request, ref: Reference, file,
                                 tmp: Path) -> tuple[LosslessHit | None, str]:
        s = self.settings
        try:
            verdict = await asyncio.to_thread(verify, tmp, s.spectrogram_dir, f"req{req.id}-lossless-{rec.id}")
        except VerifyError as e:
            rec.event("verify_failed", error=str(e))
            return None, "verify_failed"
        if verdict.spectrogram_path:
            self.store.update_attempt(rec.id, spectrogram_path=str(verdict.spectrogram_path))
        rec.event("verify", passed=verdict.passed, cutoff_hz=verdict.cutoff_hz, reason=verdict.reason)
        if not verdict.passed:
            return None, "verify_failed"
        fp = await fingerprint_check(tmp, ref.deezer_id, self.http, minimum=s.lossless_fingerprint_min, tmp_dir=s.tmp_dir)
        rec.raw("fingerprint", {"preview": fp.preview, "track": fp.track})
        self.store.update_attempt(rec.id, fingerprint=fp.to_dict())
        rec.event("fingerprint", status=fp.status, score=fp.score, offset_s=fp.offset_s, reason=fp.reason)
        if fp.status == "failed":
            return None, "fingerprint_failed"
        t0 = self.clock()
        try:
            out = await asyncio.to_thread(to_format, tmp, s.lossless_filing_format, verdict.bit_depth)
            if out != tmp:
                tmp.unlink(missing_ok=True)                       # at most one temp file from here on (spec §9)
                pr = await asyncio.to_thread(probe, out)
                verdict = replace(verdict, fmt=pr.fmt, bitrate_kbps=pr.bitrate_kbps, bit_depth=pr.bit_depth,
                                  sample_rate=pr.sample_rate)
        except (ConvertError, VerifyError) as e:
            rec.event("convert_failed", error=str(e))
            tmp.with_suffix(f".{s.lossless_filing_format}").unlink(missing_ok=True)
            return None, "convert_failed"
        rec.event("convert", fmt=verdict.fmt, ms=int((self.clock() - t0) * 1000))
        return LosslessHit(out, verdict, fp, rec.provider, file.extension, rec.id), "filed"
```

`rec.query` and `rec.provider` are attributes set in `AttemptRecorder.__init__` (Task 8). `LosslessError`, `LosslessProvider`, `TransferProgress` are re-exported by `flackey.source` (Task 5).

- [ ] **Step 5: Run the tests**

Run: `uv run pytest tests/test_worker_lossless.py tests/test_worker.py tests/test_tag.py -q`
Expected: all pass. Likely first failures and their causes:
- `test_hit_files_aiff...` on the raw file list: `rec.raw` is called by the fake provider for `search`, `responses`, `transfer-0`, then by the worker for `fingerprint`, so four files; if `03-transfer-0.json` is missing, the fake's `on_raw` call order differs from the test.
- `test_verify_failure_tries_the_second_pick`: `_fake_flac` must fail verify (`MIN_LOSSLESS_CUTOFF` in `verify.py`); a 64 kbps MP3 source has a cutoff near 11 kHz.
- Any existing `tests/test_worker.py` test that asserts the old Done line wording (`verdict.reason`); update to the new wording.

- [ ] **Step 6: Commit**

```bash
git add src/flackey/worker.py src/flackey/tag.py src/flackey/models.py tests/test_worker_lossless.py tests/test_tag.py tests/test_worker.py
git commit -m "feat(worker): try a lossless provider before Deezer, with attempt records, evidence rows and a format line"
```

---

### Task 10: Web: health, attempts endpoint, request and track bundles, settings

**Files:**
- Modify: `src/flackey/web/__init__.py` (health, `Bundles.request/track`), `src/flackey/web/library.py` (settings, tools), `src/flackey/web/requests.py` (attempt spectrogram unlink), `src/flackey/store.py` (`delete_request`/`delete_requests` also drop attempt rows; `delete_attempts_for_request` not needed)
- Create: `src/flackey/web/lossless.py`
- Test: `tests/test_web.py`

**Interfaces:**
- Consumes: `store.attempt_counts/list_attempts/get_attempt_for_request/list_evidence`, `attempts.raw_size_bytes`, `fingerprint.fpcalc_available`, `worker.format_line`, `Settings.lossless_enabled/soulseek_enabled/lossless_raw_dir`, `status["lossless_provider"]`.
- Produces: `/api/health` gains `"lossless": {"enabled", "provider", "fpcalc", "attempts_24h", "raw_mb"}`; `GET /api/lossless/attempts?limit=50&outcome=` returns `{"attempts": [...], "counts": {...}, "median_first_byte_ms", "median_total_ms"}`; request bundle gains `"attempt"`; track bundle gains `"format"` and `"evidence"`; `/api/settings` gains `soulseek_enabled`, `slskd_url`, `lossless_filing_format` (never the key); `PUT /api/settings` accepts `slskd_url`, `slskd_api_key`, `lossless_filing_format`; `/api/tools` gains `fpcalc`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_web.py`, change `test_health` to:

```python
def test_health(client):
    c, _, _ = client
    from flackey.fingerprint import fpcalc_available
    assert c.get("/api/health").json() == {"ok": True, "version": "0.1.0", "telegram_authorized": True,
                                          "worker_running": False, "setup_done": False,
                                          "lossless": {"enabled": False, "provider": None, "fpcalc": fpcalc_available(),
                                                       "attempts_24h": {}, "raw_mb": 0.0}}
```

Append:

```python
def _attempt(store, rid, outcome, *, first_byte_ms=None, total_ms=None, score=None, peer="p"):
    aid = store.add_attempt(rid, "soulseek", "q")
    store.update_attempt(aid, outcome=outcome, first_byte_ms=first_byte_ms, total_ms=total_ms,
                         report={"summary": "s", "chosen": {"username": peer, "path": "x\\f.flac"} if peer else None},
                         fingerprint={"status": "matched", "score": score} if score else None,
                         timeline=[{"t_ms": 0, "event": "outcome", "detail": {"outcome": outcome}}])
    return aid


def test_lossless_attempts_endpoint_lists_counts_and_medians(client):
    c, store, _ = client
    r1 = store.add_request("a", RequestKind.TEXT)
    r2 = store.add_request("b", RequestKind.TEXT)
    _attempt(store, r1, "filed", first_byte_ms=500, total_ms=40_000, score=0.98)
    _attempt(store, r2, "no_pick", peer=None)
    _attempt(store, r2, "filed", first_byte_ms=1500, total_ms=80_000, score=0.95)
    body = c.get("/api/lossless/attempts").json()
    assert body["counts"] == {"filed": 2, "no_pick": 1}
    assert body["median_first_byte_ms"] == 1000 and body["median_total_ms"] == 60_000
    assert [a["outcome"] for a in body["attempts"]] == ["filed", "no_pick", "filed"]       # newest first
    top = body["attempts"][0]
    assert top["request_id"] == r2 and top["peer"] == "p" and top["file"] == "f.flac" and top["score"] == 0.95
    assert "timeline" not in top and "report" not in top
    assert [a["id"] for a in c.get("/api/lossless/attempts?outcome=no_pick").json()["attempts"]] == [2]
    assert c.get("/api/health").json()["lossless"]["attempts_24h"] == {"filed": 2, "no_pick": 1}


def test_request_bundle_carries_the_attempt_and_delete_drops_it(client, tmp_path: Path):
    c, store, settings = client
    rid = store.add_request("a", RequestKind.TEXT)
    aid = _attempt(store, rid, "verify_failed", peer="p")
    png = settings.spectrogram_dir / "req1-lossless-1.png"
    png.parent.mkdir(parents=True, exist_ok=True)
    png.write_bytes(b"png")
    store.update_attempt(aid, spectrogram_path=str(png))
    bundle = c.get(f"/api/requests/{rid}").json()
    assert bundle["attempt"]["id"] == aid and bundle["attempt"]["timeline"][-1]["event"] == "outcome"
    assert bundle["attempt"]["report"]["summary"] == "s"
    store.update_request(rid, state=RequestState.NOT_FOUND)
    assert c.delete(f"/api/requests/{rid}").status_code == 200
    assert store.list_attempts() == [] and not png.exists()


def test_track_bundle_has_format_and_evidence(client, tmp_path: Path):
    c, store, _ = client
    tid = store.add_track(path=tmp_path / "a.aiff", fmt="aiff", bitrate_kbps=1411, cutoff_hz=22050, file_size=5,
                          artist="A", title="T", mix_name="Original Mix", duration_s=1, isrc=None,
                          catalog_track_id=None, request_id=None, source="soulseek", source_fmt="flac",
                          bit_depth=16, sample_rate=44100)
    store.add_evidence(tid, "recording_match", {"status": "matched", "score": 0.98})
    t = c.get("/api/library").json()[0]
    assert t["format"] == {"fmt": "aiff", "bit_depth": 16, "sample_rate": 44100, "source": "soulseek",
                           "source_fmt": "flac", "label": "AIFF 16-bit/44.1 kHz, from FLAC via Soulseek"}
    assert t["evidence"] == [{"kind": "recording_match", "value": {"status": "matched", "score": 0.98}}]
    legacy = store.add_track(path=tmp_path / "b.mp3", fmt="mp3", bitrate_kbps=320, cutoff_hz=19800, file_size=5,
                             artist="B", title="T", mix_name="Original Mix", duration_s=1, isrc=None,
                             catalog_track_id=None, request_id=None)
    t = next(x for x in c.get("/api/library").json() if x["id"] == legacy)
    assert t["format"]["label"] == "MP3 320 kbps via Deezer" and t["evidence"] == []


def test_settings_never_expose_the_api_key_and_accept_lossless_keys(tmp_path: Path):
    app, store, settings = make(tmp_path)
    c = TestClient(app)
    settings.slskd_api_key = "secret-key-value"
    out = c.get("/api/settings").json()
    assert "secret-key-value" not in json.dumps(out) and out["soulseek_enabled"] is True
    assert out["slskd_url"] == "http://127.0.0.1:5030" and out["lossless_filing_format"] == "aiff"
    r = c.put("/api/settings", json={"library_root": str(tmp_path / "lib"), "slskd_api_key": "new-key",
                                     "slskd_url": "http://127.0.0.1:5031", "lossless_filing_format": "wav"})
    assert r.status_code == 200 and "new-key" not in r.text and r.json()["lossless_filing_format"] == "wav"
    saved = json.loads(settings.settings_path.read_text())
    assert saved["slskd_api_key"] == "new-key" and saved["slskd_url"] == "http://127.0.0.1:5031"
    assert c.put("/api/settings", json={"library_root": str(tmp_path / "lib"), "lossless_filing_format": "mp3"}).status_code == 400
    assert "secret" not in json.dumps(c.get("/api/health").json())
    assert "fpcalc" in c.get("/api/tools").json()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_web.py -q`
Expected: `test_health` fails on the missing `lossless` key; the new tests fail with 404s and `KeyError`s.

- [ ] **Step 3: Implement**

`src/flackey/store.py`: in `delete_request` and `delete_requests`, add `DELETE FROM lossless_attempts WHERE request_id=?` (and the `IN (sub)` form) next to the candidates and rejections deletes. Update both docstrings ("candidates, rejections and lossless attempts").

`src/flackey/web/__init__.py`:

```python
from ..attempts import raw_size_bytes
from ..fingerprint import fpcalc_available
from ..worker import Worker, format_line
```

(`format_line` comes from `worker`, which `web` already imports; `web` is above `worker` in the layers.)

```python
class Bundles:
    def request(self, rid: int) -> dict:
        ...
        return {"request": to_dict(r), "candidates": ..., "catalog": ..., "track": track,
                "rejection": to_dict(self.store.get_rejection_for_request(rid)),
                "attempt": to_dict(self.store.get_attempt_for_request(rid))}

    def track(self, tid: int) -> dict:
        t = self.store.get_track(tid)
        d = to_dict(t)
        d["catalog"] = ...
        verdict = Verdict(True, t.fmt, t.bitrate_kbps, t.cutoff_hz, "", bit_depth=t.bit_depth, sample_rate=t.sample_rate)
        d["format"] = {"fmt": t.fmt, "bit_depth": t.bit_depth, "sample_rate": t.sample_rate, "source": t.source,
                       "source_fmt": t.source_fmt, "label": format_line(verdict, t.source, t.source_fmt)}
        d["evidence"] = [{"kind": e.kind, "value": e.value} for e in self.store.list_evidence(tid)]
        return d
```

Import `Verdict` from `..models`. Health:

```python
    @app.get("/api/health")
    async def health() -> dict:
        return {"ok": True, "version": __version__,
                "telegram_authorized": bool(status.get("telegram_authorized", True)),
                "worker_running": bool(status.get("worker_running", False)),
                "setup_done": bool(status.get("setup_done", False)),
                "lossless": {"enabled": settings.lossless_enabled, "provider": status.get("lossless_provider"),
                             "fpcalc": fpcalc_available(), "attempts_24h": store.attempt_counts(24),
                             "raw_mb": round(raw_size_bytes(settings.lossless_raw_dir) / 1e6, 1)}}
```

and `app.include_router(lossless.router(store))` next to the other routers (add `lossless` to the local import line).

Create `src/flackey/web/lossless.py`:

```python
from __future__ import annotations

from statistics import median

from fastapi import APIRouter, HTTPException

from ..models import ATTEMPT_OUTCOMES
from ..store import Store


def _row(a) -> dict:
    chosen = (a.report or {}).get("chosen") or {}
    return {"id": a.id, "request_id": a.request_id, "provider": a.provider, "created_at": a.created_at, "query": a.query,
            "outcome": a.outcome, "peer": chosen.get("username"),
            "file": chosen["path"].replace("\\", "/").rsplit("/", 1)[-1] if chosen.get("path") else None,
            "first_byte_ms": a.first_byte_ms, "total_ms": a.total_ms,
            "score": (a.fingerprint or {}).get("score"), "summary": (a.report or {}).get("summary")}


def router(store: Store) -> APIRouter:
    r = APIRouter(prefix="/api/lossless")

    @r.get("/attempts")
    async def attempts(limit: int = 50, outcome: str | None = None) -> dict:
        if outcome is not None and outcome not in ATTEMPT_OUTCOMES:
            raise HTTPException(400, f"unknown outcome; one of {', '.join(ATTEMPT_OUTCOMES)}")
        rows = store.list_attempts(limit=limit, outcome=outcome)
        counts: dict[str, int] = {}
        for a in rows:
            counts[a.outcome or "open"] = counts.get(a.outcome or "open", 0) + 1
        first = [a.first_byte_ms for a in rows if a.first_byte_ms is not None]
        total = [a.total_ms for a in rows if a.total_ms is not None and a.outcome == "filed"]
        return {"attempts": [_row(a) for a in rows], "counts": counts,
                "median_first_byte_ms": median(first) if first else None,
                "median_total_ms": median(total) if total else None}

    return r
```

`src/flackey/web/requests.py`: `unlink_spectrogram` already takes any object with `spectrogram_path`; in `delete` and `clear_failed`, also fetch `store.get_attempt_for_request(rid)` before deleting and call `unlink_spectrogram(attempt)` after.

`src/flackey/web/library.py`:

```python
    def settings_out() -> dict:
        return {"library_root": str(settings.library_root), "data_dir": str(settings.data_dir),
                "version": __version__, "telegram_configured": settings.telegram_configured,
                "log_path": str(settings.data_dir / "flackey.log"),
                "soulseek_enabled": settings.soulseek_enabled, "slskd_url": settings.slskd_url,
                "slskd_downloads_dir": str(settings.slskd_downloads),
                "lossless_filing_format": settings.lossless_filing_format}

    @r.put("/settings")
    async def put_settings(body: dict) -> dict:
        ...existing library_root validation...
        extra: dict = {}
        if "lossless_filing_format" in body:
            if body["lossless_filing_format"] not in FILING_FORMATS:
                raise HTTPException(400, f"Filing format must be one of {', '.join(FILING_FORMATS)}.")
            extra["lossless_filing_format"] = body["lossless_filing_format"]
        if body.get("slskd_url"):
            extra["slskd_url"] = str(body["slskd_url"]).rstrip("/")
        if body.get("slskd_api_key"):
            extra["slskd_api_key"] = str(body["slskd_api_key"]).strip()
        save_settings(settings, library_root=path, **extra)
        return settings_out()
```

Import `FILING_FORMATS` from `..config`. `save_settings` writes only `FILE_KEYS` (Task 1), so the key lands in `settings.json` and nowhere else. `tools()` gains `"fpcalc": shutil.which("fpcalc") is not None`.

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_web.py -q`
Expected: all pass. If `test_lossless_attempts_endpoint_lists_counts_and_medians` fails on `[2]`, `list_attempts` must return newest first (Task 3 orders by `id DESC`); the ids in that test are 1, 2, 3 because the store is fresh.

- [ ] **Step 5: Commit**

```bash
git add src/flackey/web src/flackey/store.py tests/test_web.py
git commit -m "feat(web): lossless health, attempts endpoint, attempt and format in bundles, sidecar settings without the key"
```

---

### Task 11: CLI: `crate lossless replay`

**Files:**
- Modify: `src/flackey/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `store.get_attempt_for_request`, `source.slskd.parse_response`, `lossless.PickReport.from_dict/pick/policy_from_settings/Reference`.
- Produces: `crate lossless replay <request id>`.

Spec §17.4: re-run `pick()` on the stored `responses.json` under the current policy and diff the chosen file and rejection counts against the stored report. This is how a rule change is checked against real responses before it ships.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cli.py`:

```python
def test_lossless_replay_diffs_the_pick_under_the_current_policy(tmp_path: Path, fixtures: Path):
    import json
    import shutil

    from flackey.lossless import PickPolicy, Reference, pick
    from flackey.source.slskd import parse_response

    env = _env(tmp_path)
    store = Store(tmp_path / "data" / "flackey.sqlite")
    rid = store.add_request("hallucinogen orphic thrench", RequestKind.TEXT)
    responses = json.loads((fixtures / "slskd" / "responses_completed.json").read_text())
    files = [f for r in responses for f in parse_response(r)]
    ref = Reference("Hallucinogen", "Orphic Thrench", "Original Mix", 442, 6025986)
    report = pick(files, ref, PickPolicy())
    aid = store.add_attempt(rid, "soulseek", "Hallucinogen Orphic Thrench")
    raw = tmp_path / "data" / "lossless" / "attempts" / str(aid)
    raw.mkdir(parents=True)
    shutil.copy(fixtures / "slskd" / "responses_completed.json", raw / "02-responses.json")
    store.update_attempt(aid, outcome="filed", report=report.to_dict(), raw_dir=str(raw))

    r = runner.invoke(app, ["--env", str(env), "lossless", "replay", str(rid)])
    assert r.exit_code == 0, r.output
    assert f"stored: {report.chosen.username}" in r.output and f"now:    {report.chosen.username}" in r.output
    assert "same pick" in r.output

    env.write_text(env.read_text() + "LOSSLESS_TITLE_RATIO=101\n")        # nothing can pass the title rule now
    r = runner.invoke(app, ["--env", str(env), "lossless", "replay", str(rid)])
    assert r.exit_code == 0 and "no pick now" in r.output and "now:    -" in r.output
    assert "title" in r.output and "<-" in r.output

    r = runner.invoke(app, ["--env", str(env), "lossless", "replay", "999"])
    assert r.exit_code == 1 and "no attempt" in r.output
```

The `fixtures` fixture comes from `tests/conftest.py`. With the default policy the recorded responses pick `msitua` (free slot, 16-bit, queue 0, fastest of the two such peers); the test does not hard-code that, it compares against what `pick()` returns.

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_cli.py -q -k replay`
Expected: exit code 2 from typer (`No such command 'lossless'`).

- [ ] **Step 3: Implement**

In `src/flackey/cli.py`:

```python
lossless_app = typer.Typer(help="Lossless-upgrade tools (Soulseek through slskd)", no_args_is_help=True)
app.add_typer(lossless_app, name="lossless")


@lossless_app.command()
def replay(request_id: int) -> None:
    """Re-run the pick on the stored search responses of a request under the current policy and diff it."""
    import json
    from collections import Counter
    from pathlib import Path

    from .lossless import PickReport, pick, policy_from_settings
    from .source.slskd import parse_response
    from .store import Store

    s = _settings()
    attempt = Store(s.db_path).get_attempt_for_request(request_id)
    if attempt is None or not attempt.report:
        typer.echo(f"no attempt with a pick report for request {request_id}")
        raise typer.Exit(1)
    raw = next(iter(sorted(Path(attempt.raw_dir or "").glob("*-responses.json"))), None)
    if raw is None:
        typer.echo(f"no responses.json under {attempt.raw_dir} (pruned after {s.lossless_keep_raw_days} days)")
        raise typer.Exit(1)
    stored = PickReport.from_dict(attempt.report)
    files = [f for r in json.loads(raw.read_text()) for f in parse_response(r)]
    now = pick(files, stored.reference, policy_from_settings(s))

    def who(report: PickReport) -> str:
        return f"{report.chosen.username} {report.chosen.name}" if report.chosen else "-"

    typer.echo(f"attempt {attempt.id} ({attempt.outcome}) · {len(files)} files · query {attempt.query!r}")
    typer.echo(f"stored: {who(stored)}")
    typer.echo(f"now:    {who(now)}")
    if now.chosen is None:
        typer.echo("no pick now")
    elif stored.chosen == now.chosen:
        typer.echo("same pick")
    else:
        typer.echo("different pick")
    before, after = Counter(r.rule for r in stored.rejections), Counter(r.rule for r in now.rejections)
    for rule in sorted(set(before) | set(after)):
        mark = "" if before[rule] == after[rule] else "  <-"
        typer.echo(f"  {rule:15} stored {before[rule]:3}  now {after[rule]:3}{mark}")
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_cli.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/flackey/cli.py tests/test_cli.py
git commit -m "feat(cli): crate lossless replay diffs a stored pick against the current policy"
```

---

### Task 12: Wiring, layers, env example, README

**Files:**
- Modify: `src/flackey/app.py`, `pyproject.toml`, `.env.example`, `README.md`, `src/flackey/logsetup.py` (banner line)
- Test: `tests/test_app.py`, `tests/test_logsetup.py`

**Interfaces:**
- Consumes: `Settings.soulseek_enabled/slskd_url/slskd_api_key/slskd_downloads/lossless_raw_dir`, `SlskdClient`, `SoulseekProvider`, `Worker(providers=, http=)`.
- Produces: `app.build_providers(settings, http) -> list[LosslessProvider]`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_app.py`:

```python
def test_build_providers_follows_the_api_key_and_never_logs_it(tmp_path, caplog):
    import httpx

    from flackey.app import build_providers
    from flackey.config import Settings
    from flackey.source.slskd import SoulseekProvider

    caplog.set_level("DEBUG")
    http = httpx.AsyncClient()
    off = Settings(_env_file=None, data_dir=tmp_path)
    assert build_providers(off, http) == []
    on = Settings(_env_file=None, data_dir=tmp_path, slskd_api_key="very-secret-key")
    providers = build_providers(on, http)
    assert len(providers) == 1 and isinstance(providers[0], SoulseekProvider)
    assert providers[0].downloads == tmp_path / "slskd" / "downloads" and providers[0].downloads.is_dir()
    assert "very-secret-key" not in caplog.text
```

Append to `tests/test_logsetup.py` (look at how the existing banner test builds its settings and caplog; mirror it):

```python
def test_banner_says_whether_soulseek_is_on_without_the_key(tmp_path, caplog):
    from flackey.config import Settings
    from flackey.logsetup import log_startup_banner

    caplog.set_level("INFO")
    log_startup_banner(Settings(_env_file=None, data_dir=tmp_path, slskd_api_key="very-secret-key"), "0.1.0")
    assert "soulseek: yes" in caplog.text and "very-secret-key" not in caplog.text
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_app.py tests/test_logsetup.py -q`
Expected: `ImportError: cannot import name 'build_providers'` and the banner assertion failing.

- [ ] **Step 3: Implement**

`src/flackey/logsetup.py`, in `log_startup_banner` after the telegram line:

```python
    log.info("soulseek: %s", "yes" if settings.soulseek_enabled else "no")
```

`src/flackey/app.py`:

```python
from .source.lossless import LosslessProvider
from .source.slskd import SlskdClient, SoulseekProvider


def build_providers(settings: Settings, http: httpx.AsyncClient) -> list[LosslessProvider]:
    """One provider per configured network (spec §10). Only the sidecar URL and folder are logged, never the key."""
    providers: list[LosslessProvider] = []
    if settings.soulseek_enabled:
        settings.slskd_downloads.mkdir(parents=True, exist_ok=True)
        client = SlskdClient(settings.slskd_url, settings.slskd_api_key or "", http)
        providers.append(SoulseekProvider(client, settings.slskd_downloads))
        log.info("Soulseek on: slskd at %s, downloads in %s", settings.slskd_url, settings.slskd_downloads)
    return providers
```

In `_run`: add `settings.lossless_raw_dir` to the `mkdir` loop; after `http = httpx.AsyncClient(timeout=20)` build `providers = build_providers(settings, http)` and construct the worker as

```python
    worker = Worker(store, source, BeatportCatalog(), LogNotifier(), settings, status=status,
                    providers=providers, http=http)
```

`pyproject.toml` layers (replace the `layers` list):

```toml
layers = [
  "cli",
  "desktop",
  "app",
  "web",
  "worker : events : inbox",
  "attempts",
  "library : export : tag : verify : store : catalog : deezer : youtube : source : telegram : convert : fingerprint",
  "lossless",
  "match : identify",
  "notify : config : models : logsetup",
]
```

`attempts` sits alone above the adapters because it imports `store`; `source` (the package, including `source.slskd`) imports `lossless`, which imports `identify`, hence the `lossless` line between the adapters and `match : identify`.

`.env.example`, append:

```
# Lossless upgrade through a local slskd sidecar (Soulseek). Off until SLSKD_API_KEY is set.
# The key is also accepted from settings.json (slskd_api_key). Never commit it.
# SLSKD_URL=http://127.0.0.1:5030
# SLSKD_API_KEY=
# SLSKD_DOWNLOADS_DIR=~/Library/Application Support/Flackey/slskd/downloads
# LOSSLESS_FILING_FORMAT=aiff        # aiff (default; Rekordbox reads all tags), wav (loses label/artwork), flac
# LOSSLESS_SEARCH_WAIT_S=30
# LOSSLESS_FIRST_BYTE_S=60
# LOSSLESS_TRANSFER_S=600
# LOSSLESS_TITLE_RATIO=90
# LOSSLESS_REQUIRE_ARTIST=false
# LOSSLESS_MAX_QUEUE=
# LOSSLESS_FINGERPRINT_MIN=0.90
# LOSSLESS_MAX_PICKS=2
# LOSSLESS_KEEP_RAW_DAYS=30
```

`README.md`: add a section `## Lossless via Soulseek` before `## Where data lives` with this content:

```markdown
## Lossless via Soulseek

Optional. With a running [slskd](https://github.com/slskd/slskd) sidecar, every request first asks the
Soulseek network for a FLAC of the matched track. The file is verified (spectral cutoff), proven to be the
same recording as the Deezer match with a Chromaprint fingerprint against Deezer's 30 s preview, converted to
AIFF (audio untouched, peer tags dropped) and tagged from Beatport. On any miss the Deezer MP3 is fetched as
before. The Done line says what you got: `AIFF 16-bit/44.1 kHz, from FLAC via Soulseek` or
`MP3 320 kbps via Deezer`.

1. `brew install chromaprint` (for `fpcalc`; without it the recording check is skipped and shown as such).
2. Download the slskd release for macOS, put it in `~/Library/Application Support/Flackey/slskd/` with a
   `slskd.yml` like:

   ```yaml
   web:
     port: 5030
     authentication:
       api_keys:
         flackey: { key: "<random 32+ chars>", role: readwrite }
   soulseek:
     username: <your soulseek account>
     password: <its password>
     listen_port: 50300
   shares:
     directories: ["~/Music/DJ Library"]
   directories:
     downloads: ~/Library/Application Support/Flackey/slskd/downloads
   ```

   Start it with `./slskd --app-dir "~/Library/Application Support/Flackey/slskd"`. Sharing your library is
   what makes peers serve you; forward TCP 50300 on the router so they can reach it (searching works without).
3. Put the same key in `.env` as `SLSKD_API_KEY=` (or in `settings.json` as `slskd_api_key`). Restart.
4. `/api/health` shows `lossless.provider.status` (`ok`, `not_logged_in`, `unreachable`), whether `fpcalc` is
   present, the last 24 h of attempt outcomes and the size of the raw attempt folder.

Every attempt is recorded: `GET /api/lossless/attempts`, the attempt block on a request, and the raw slskd
responses under `<data dir>/lossless/attempts/<id>/` (pruned after `LOSSLESS_KEEP_RAW_DAYS`, default 30).
`crate lossless replay <request id>` re-runs the pick under the current settings and shows what changed.
A transfer cancelled by a cap can still complete on slskd's side; such files stay in slskd's downloads folder
and can be deleted at any time.
```

- [ ] **Step 4: Run everything**

Run:

```bash
uv run pytest -q
uv run ruff check src tests
uv run lint-imports
```

Expected: all tests pass (fpcalc-dependent ones skip where it is missing), no ruff findings, import-linter reports the contract kept. If `lint-imports` names `flackey.attempts -> flackey.store` as a violation, the `attempts` line is missing or in the wrong place in `pyproject.toml`.

- [ ] **Step 5: Commit**

```bash
git add src/flackey/app.py src/flackey/logsetup.py pyproject.toml .env.example README.md tests/test_app.py tests/test_logsetup.py
git commit -m "feat(app): build the Soulseek provider from settings; layers, env example and README for the lossless upgrade"
```

---

## Done when

- `uv run pytest -q`, `uv run ruff check src tests` and `uv run lint-imports` are clean on `feat/soulseek-spike`.
- With slskd running and `SLSKD_API_KEY` set, a request for a track the spike found (for example Hallucinogen – Orphic Thrench) ends `Done … AIFF 16-bit/44.1 kHz, from FLAC via Soulseek`, `/api/lossless/attempts` shows the attempt as `filed` with a fingerprint score above 0.9, and `<data dir>/lossless/attempts/<id>/` holds the raw responses.
- With slskd stopped, the same request files the Deezer MP3 and the attempt row says `unavailable`.
