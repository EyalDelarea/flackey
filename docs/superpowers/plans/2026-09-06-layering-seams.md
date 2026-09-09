# Layering Seams Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the four misplaced seams the layout audit found, and make the module tiering an enforced rule, without moving the package into subfolders.

**Architecture:** Keep `src/flackey/` flat. Move naming logic onto the model, split yt-dlp I/O out of the pure parser, put the Telegram notifier in its own module beside the Notifier port, then add an import-linter "layers" contract that pins the order cli → app → web/inbox → worker → adapters → domain → leaves.

**Tech Stack:** Python 3.12, uv, pytest (asyncio_mode=auto), ruff, import-linter (new dev dependency).

**Spec:** The audit text pasted in the session on 2026-09-06 plus the response to it. Summary of findings: (1) `TelegramNotifier` lives in `inbox.py` while its Protocol is in `notify.py`; (2) `identify.py` mixes pure parsing with a yt-dlp subprocess; (3) `library.py` imports `display_title` from `tag.py` for a filename; (4) `source/` is a one-adapter seam and stays as is.

## Global Constraints

- No behavior changes. Every existing test keeps passing; tests are only moved or re-pointed at new import paths.
- Package stays flat: no new subpackages under `src/flackey/`.
- `crate = "flackey.cli:app"` in `pyproject.toml` and `UI_DIR` in `app.py` are untouched.
- Run tests with `uv run pytest -q` (must pass) and `uv run ruff check src tests`. Ruff has 40 pre-existing errors at baseline (RUF059, I001 and friends); the rule is "no new ruff errors": the total must not rise above 40, and no error may point at a line you added or moved. Where a step below says "ruff clean", read it as this rule.
- Commit after each task. Conventional-commit prefix, body ends with the session trailers already configured for this repo.

---

### Task 1: `display_title` becomes a `CatalogTrack` property

**Files:**
- Modify: `src/flackey/models.py:77-101` (CatalogTrack dataclass)
- Modify: `src/flackey/tag.py:1-40` (remove `_is_original`, `display_title`; use the property)
- Modify: `src/flackey/library.py:8-24` (drop the tag import; use the property)
- Modify: `tests/test_tag.py:8-16, 88-98` (remove import and the moved test)
- Test: `tests/test_models.py`

**Interfaces:**
- Produces: `CatalogTrack.display_title -> str` property. Returns `title` when `mix_name` contains the word "original", else `f"{title} ({mix_name})"`.

- [ ] **Step 1: Write the failing test in `tests/test_models.py`**

Append to the end of the file:

```python
import pytest

from flackey.models import CatalogTrack


def _ct(mix: str) -> CatalogTrack:
    return CatalogTrack(id=1, artist="Astral Projection", title="Into the Void", mix_name=mix,
                        label="Trust in Trance", genre="Psy-Trance")


@pytest.mark.parametrize("mix,expected", [
    ("Aboriginal Mix", "Into the Void (Aboriginal Mix)"),
    ("Original Mix", "Into the Void"),
    ("Original", "Into the Void"),
    ("Original Version", "Into the Void"),
])
def test_display_title_word_boundary_original(mix: str, expected: str):
    assert _ct(mix).display_title == expected
```

If `tests/test_models.py` already imports `pytest` or `CatalogTrack`, do not duplicate the import lines.

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_models.py -q`
Expected: FAIL with `AttributeError: 'CatalogTrack' object has no attribute 'display_title'`

- [ ] **Step 3: Add the property to `models.py`**

Add `import re` to the module imports if it is not there. Inside `class CatalogTrack`, after the `year` property, add:

```python
    @property
    def display_title(self) -> str:
        if re.search(r"\boriginal\b", self.mix_name, re.IGNORECASE):
            return self.title
        return f"{self.title} ({self.mix_name})"
```

- [ ] **Step 4: Run the new test to verify it passes**

Run: `uv run pytest tests/test_models.py -q`
Expected: PASS

- [ ] **Step 5: Switch `tag.py` to the property**

Delete `_is_original` and `display_title` (lines 21-26). In `_values`, change `"title": display_title(catalog),` to `"title": catalog.display_title,`. The `re` import is still used by nothing else in `tag.py` after this; remove it if ruff reports it unused.

- [ ] **Step 6: Switch `library.py` to the property**

Remove the line `from .tag import display_title`. In `final_path`, change `sanitize(display_title(catalog))` to `sanitize(catalog.display_title)`.

- [ ] **Step 7: Remove the moved test from `tests/test_tag.py`**

Delete `display_title,` from the `from flackey.tag import (...)` block and delete the whole `test_display_title_word_boundary_original` function (the parametrize decorator plus the function, lines 88-98).

- [ ] **Step 8: Run the full suite and ruff**

Run: `uv run pytest -q && uv run ruff check src tests`
Expected: all tests pass, ruff clean. `grep -rn display_title src tests` should show only `models.py`, `tag.py`, `library.py`, `test_models.py`.

- [ ] **Step 9: Commit**

```bash
git add src/flackey/models.py src/flackey/tag.py src/flackey/library.py tests/test_models.py tests/test_tag.py
git commit -m "refactor(models): make display_title a CatalogTrack property so library stops importing tag"
```

---

### Task 2: Split yt-dlp I/O out of `identify.py` into `youtube.py`

**Files:**
- Create: `src/flackey/youtube.py`
- Modify: `src/flackey/identify.py:1-34, 129-161`
- Modify: `src/flackey/inbox.py:10`
- Modify: `tests/test_identify.py:3-5, 93-115`
- Modify: `tests/test_inbox.py:6`
- Create: `tests/test_youtube.py`

**Interfaces:**
- Produces: module `flackey.youtube` exporting `YouTubeError`, `YouTubeEntry`, `parse_ytdlp_json(data: dict) -> tuple[str, list[YouTubeEntry]]`, `YOUTUBE_FETCH_TIMEOUT_S`, `async fetch_youtube(url: str) -> tuple[str, list[YouTubeEntry]]`. Same signatures as today.
- `flackey.identify` keeps only `classify`, `parse_version`, `parse_text`, `parse_youtube_title` and their private helpers.

- [ ] **Step 1: Create `tests/test_youtube.py` by moving the two ytdlp tests**

Cut `test_parse_ytdlp_playlist_json` and `test_parse_ytdlp_video_json` (lines 93-115) out of `tests/test_identify.py` and paste them into a new `tests/test_youtube.py` with this header:

```python
from flackey.youtube import parse_ytdlp_json
```

In `tests/test_identify.py`, remove `parse_ytdlp_json,` from the import block so it reads:

```python
from flackey.identify import (
    classify, parse_text, parse_version, parse_youtube_title,
)
```

- [ ] **Step 2: Run the new test file to verify it fails**

Run: `uv run pytest tests/test_youtube.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'flackey.youtube'`

- [ ] **Step 3: Create `src/flackey/youtube.py`**

Move these verbatim from `identify.py`: the `YouTubeError` class, the `YouTubeEntry` dataclass, `parse_ytdlp_json`, `YOUTUBE_FETCH_TIMEOUT_S`, and `fetch_youtube`. The new file is:

```python
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass


class YouTubeError(Exception):
    pass


@dataclass
class YouTubeEntry:
    url: str
    title: str
    uploader: str | None
    duration_s: int | None


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


YOUTUBE_FETCH_TIMEOUT_S = 120


async def fetch_youtube(url: str) -> tuple[str, list[YouTubeEntry]]:
    proc = await asyncio.create_subprocess_exec(
        "yt-dlp", "--dump-single-json", "--flat-playlist", "--no-warnings", url,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=YOUTUBE_FETCH_TIMEOUT_S)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise YouTubeError(f"yt-dlp timed out after {YOUTUBE_FETCH_TIMEOUT_S}s") from None
    if proc.returncode != 0:
        raise YouTubeError(err.decode(errors="replace").strip()[-500:] or "yt-dlp failed")
    try:
        return parse_ytdlp_json(json.loads(out))
    except (json.JSONDecodeError, KeyError) as e:
        raise YouTubeError(f"unexpected yt-dlp output: {e}") from e
```

Compare against the current `identify.py:129-161` before saving. If the bodies differ, the current file wins; copy it exactly.

- [ ] **Step 4: Remove the moved code from `identify.py`**

Delete the `YouTubeError` class, the `YouTubeEntry` dataclass, `parse_ytdlp_json`, `YOUTUBE_FETCH_TIMEOUT_S`, and `fetch_youtube`. Remove the now-unused imports `asyncio`, `json`, and `dataclass`. Keep `re`, `parse_qs`, `urlparse`, `urlunparse`, `urlencode`, and the models import.

- [ ] **Step 5: Re-point `inbox.py`**

Replace line 10:

```python
from .identify import YouTubeEntry, YouTubeError, classify, fetch_youtube, parse_text, parse_youtube_title
```

with:

```python
from .identify import classify, parse_text, parse_youtube_title
from .youtube import YouTubeEntry, YouTubeError, fetch_youtube
```

- [ ] **Step 6: Re-point `tests/test_inbox.py`**

Replace line 6 `from flackey.identify import YouTubeEntry, YouTubeError` with `from flackey.youtube import YouTubeEntry, YouTubeError`.

- [ ] **Step 7: Run the full suite and ruff**

Run: `uv run pytest -q && uv run ruff check src tests`
Expected: all tests pass, ruff clean. `grep -rn "yt-dlp\|subprocess\|asyncio" src/flackey/identify.py` prints nothing.

- [ ] **Step 8: Commit**

```bash
git add src/flackey/youtube.py src/flackey/identify.py src/flackey/inbox.py tests/test_youtube.py tests/test_identify.py tests/test_inbox.py
git commit -m "refactor(identify): move yt-dlp fetch into youtube.py so identify is pure parsing"
```

---

### Task 3: Move `TelegramNotifier` into `telegram_notify.py`

**Files:**
- Create: `src/flackey/telegram_notify.py`
- Modify: `src/flackey/inbox.py:1-14, 103-116`
- Modify: `src/flackey/app.py:15, 53`
- Test: `tests/test_telegram_notify.py` (new)

**Interfaces:**
- Produces: `flackey.telegram_notify.TelegramNotifier(bot: aiogram.Bot, owner_id: int)` with `async send(text: str, buttons: list[Button] | None = None) -> None`. Identical behavior to today's class at `inbox.py:103`.
- Rationale: the Notifier Protocol and its in-memory fakes stay pure in `notify.py`. The adapter gets its own module, mirroring `source/base.py` + `source/deezer_bot.py`. `notify.py` does not import aiogram, so `worker.py` and `cli.py` keep a light import graph.

- [ ] **Step 1: Write the failing test `tests/test_telegram_notify.py`**

```python
from flackey.notify import Button
from flackey.telegram_notify import TelegramNotifier


class FakeBot:
    def __init__(self, fail: bool = False):
        self.fail, self.calls = fail, []

    async def send_message(self, chat_id, text, reply_markup=None):
        if self.fail:
            raise RuntimeError("telegram down")
        self.calls.append((chat_id, text, reply_markup))


async def test_sends_text_with_inline_buttons():
    bot = FakeBot()
    await TelegramNotifier(bot, 42).send("hi", [Button("Yes", "y:1"), Button("No", "n:1")])
    chat_id, text, markup = bot.calls[0]
    assert (chat_id, text) == (42, "hi")
    rows = markup.inline_keyboard
    assert [(b.text, b.callback_data) for row in rows for b in row] == [("Yes", "y:1"), ("No", "n:1")]


async def test_no_buttons_means_no_markup():
    bot = FakeBot()
    await TelegramNotifier(bot, 42).send("plain")
    assert bot.calls == [(42, "plain", None)]


async def test_send_failure_is_swallowed():
    await TelegramNotifier(FakeBot(fail=True), 42).send("hi")  # must not raise
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_telegram_notify.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'flackey.telegram_notify'`

- [ ] **Step 3: Create `src/flackey/telegram_notify.py`**

```python
from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from .notify import Button

log = logging.getLogger(__name__)


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
```

- [ ] **Step 4: Run the new tests to verify they pass**

Run: `uv run pytest tests/test_telegram_notify.py -q`
Expected: 3 passed

- [ ] **Step 5: Delete the class from `inbox.py` and prune imports**

Delete `class TelegramNotifier` (lines 103-116). Remove `from .notify import Button` and drop `InlineKeyboardButton, InlineKeyboardMarkup` from the `aiogram.types` import if nothing else in `inbox.py` uses them. Check with `grep -n "InlineKeyboard\|Button" src/flackey/inbox.py` and keep whatever is still referenced.

- [ ] **Step 6: Re-point `app.py`**

Replace line 15 `from .inbox import Inbox, TelegramNotifier, build_bot` with:

```python
from .inbox import Inbox, build_bot
from .telegram_notify import TelegramNotifier
```

Line 53 (`notifier_holder["n"] = TelegramNotifier(bot, settings.owner_telegram_id)`) stays as is.

- [ ] **Step 7: Run the full suite and ruff**

Run: `uv run pytest -q && uv run ruff check src tests`
Expected: all pass, ruff clean. `grep -rn TelegramNotifier src tests` shows only `telegram_notify.py`, `app.py`, `test_telegram_notify.py`.

- [ ] **Step 8: Commit**

```bash
git add src/flackey/telegram_notify.py src/flackey/inbox.py src/flackey/app.py tests/test_telegram_notify.py
git commit -m "refactor(notify): move TelegramNotifier out of inbox into its own adapter module"
```

---

### Task 4: Enforce the tiering with import-linter

**Files:**
- Modify: `pyproject.toml` (dev dependency group, new `[tool.importlinter]` section)
- Modify: `README.md` (one line in the dev/test section, wherever `pytest` is mentioned)

**Interfaces:**
- Produces: `uv run lint-imports` exits 0 on the layered graph and fails on any upward import.

- [ ] **Step 1: Add the dev dependency**

Run: `uv add --group dev "import-linter>=2.0"`
Expected: `pyproject.toml` dev group now lists `import-linter`, `uv.lock` updated.

- [ ] **Step 2: Write a deliberately failing contract to prove the tool bites**

Append to `pyproject.toml`:

```toml
[tool.importlinter]
root_package = "flackey"

[[tool.importlinter.contracts]]
name = "cli -> app -> surfaces -> worker -> adapters -> domain -> leaves"
type = "layers"
layers = [
  "flackey.models",
  "flackey.cli",
]
```

Run: `uv run lint-imports`
Expected: FAIL, reporting that `flackey.cli` imports `flackey.models` (via config) against the declared order. This confirms the tool sees the package. If it reports "no packages found", check that `uv sync` has installed the project in editable mode and rerun.

- [ ] **Step 3: Replace with the real contract**

Replace the `layers` list with:

```toml
layers = [
  "flackey.cli",
  "flackey.app",
  "flackey.web : flackey.inbox",
  "flackey.worker",
  "flackey.telegram_notify : flackey.library : flackey.export : flackey.tag : flackey.verify : flackey.store : flackey.catalog : flackey.deezer : flackey.youtube : flackey.source",
  "flackey.match : flackey.identify",
  "flackey.notify : flackey.config : flackey.models",
]
```

The colon means modules in the same line may import each other (library imports tag and store, deezer_bot imports deezer, match imports identify). Each line may import any line below it and none above it.

- [ ] **Step 4: Run the contract**

Run: `uv run lint-imports`
Expected: `Contracts: 1 kept, 0 broken.`

If it reports a broken contract, read the offending import it prints. Every internal import was mapped before this plan was written and fits the order above, so a failure means either a typo in the layer list or a module name that changed in Tasks 1-3. Fix the list, do not add `ignore_imports`.

- [ ] **Step 5: Document the command**

In `README.md`, next to where the test command is documented, add:

```
uv run lint-imports   # module layering: cli -> app -> web/inbox -> worker -> adapters -> domain -> models
```

- [ ] **Step 6: Run everything**

Run: `uv run pytest -q && uv run ruff check src tests && uv run lint-imports`
Expected: all three clean.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml uv.lock README.md
git commit -m "chore(lint): enforce module layering with import-linter"
```

---

## Not in scope

- Moving modules into `domain/`, `adapters/`, `surfaces/` subpackages. The layers contract in Task 4 gives the enforceable rule without the moves. Revisit if the module count roughly doubles.
- Renaming `web.py` to `api.py`. Cosmetic; do it later in its own commit if wanted.
- Making `UI_DIR` in `app.py` robust to relocation. Not needed while `app.py` stays put.
- Touching `source/`. It stays as the documented pluggable seam.
