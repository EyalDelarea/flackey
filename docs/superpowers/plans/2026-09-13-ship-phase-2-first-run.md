# Ship phase 2: a stranger's first run — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a fresh install of Flackey, on a Mac that is not the owner's, complete setup and fetch a track: Telegram works with keys shipped in the build or is skippable, the DJ Library is shared on Soulseek, and the Soulseek listen port is opened from the app or the user is told exactly how to open it.

**Architecture:** Three independent seams. (1) `config.load_settings` gains a lowest-precedence source, `flackey/assets/build.json`, that CI fills from secrets; the wizard and Settings can also save the two Telegram keys, and `TelegramLogin.reconfigure()` rebuilds the client without a restart. A skipped Telegram step turns `source_enabled` off, and the worker runs whenever Telegram is authorized *or* the source is off. (2) `slskd_config.write_credentials` writes `shares.directories`, and a library-folder change rewrites it. (3) Two new leaf modules, `portmap` (NAT-PMP then UPnP) and `portcheck` (the Soulseek project's own port test), are driven by a `Sharing` service whose state rides on the shared `status` dict, so health, SSE and a `SharingPanel` in Settings, the Soulseek step and the Uploads tab all read one thing.

**Tech Stack:** Python 3.12, FastAPI, httpx + respx, pytest-asyncio (auto mode), pydantic-settings, PyYAML; React 19 + TypeScript, Vitest + Testing Library. Layering is enforced by import-linter (`[tool.importlinter]` in pyproject.toml, `exhaustive = true`, so every new module must be added to `layers`).

**Spec:** The roadmap page (claude.ai artifact "Flackey Ship Roadmap", decisions D1 and D7, work items "shares", "telegram_step", "port_check", "friends_readme", "fresh_run_test") and the owner's chat answers on 2026-09-13. Decisions in force: ship a Flackey api_id in the build with an override in Settings, Telegram step skippable; open the port from the app, verify, guide by hand when closed.

## Global Constraints

- Python `>=3.12,<3.13`; run everything with `uv run …`. Line length 100 (ruff). Tests: `uv run pytest -q`, `uv run ruff check src tests`, `uv run lint-imports`.
- Web: `npm --prefix web test -- --run` and `npm --prefix web run build` (tsc, strict) must stay green.
- Never log or return a secret: Telegram api_hash is treated like the slskd API key — it may be saved, never echoed in a GET (`/api/settings` reports `telegram_configured` only).
- The app must start and show setup with nothing configured. No new required env vars.
- Copy addresses the user, names things by what they see ("Soulseek", "Telegram", "your router"), never "slskd", "NAT-PMP", "UPnP" in UI text.
- Commit after every task with a message in the existing style (imperative summary, a body that says why). Do not push.

---

## File structure

| File | Responsibility |
|---|---|
| `src/flackey/config.py` | + `BUILD_DEFAULTS_PATH`, `_read_build_defaults()`, lowest-precedence merge in `load_settings` |
| `src/flackey/telegram.py` | + `TelegramLogin.reconfigure()` |
| `src/flackey/web/telegram.py` | + `POST /api/telegram/keys`, `POST /api/telegram/skip` |
| `src/flackey/worker.py` | `run_forever` runs while authorized **or** source off |
| `src/flackey/app.py` | `supervise_worker(..., run_when=)`, on_authorized re-enables the source, wires `Sharing` |
| `src/flackey/slskd_config.py` | `write_credentials(..., library_root=)`, + `write_share(data_dir, library_root, previous=None)` |
| `src/flackey/web/library.py` | PUT /settings rewrites the share on a folder change; POST /setup/soulseek passes the library root |
| `src/flackey/portmap.py` (new, leaf) | gateway/LAN discovery, NAT-PMP, UPnP AddPortMapping/DeletePortMapping |
| `src/flackey/portcheck.py` (new, leaf) | `check_port()` against `tools.slsknet.org/porttest.php` |
| `src/flackey/sharing.py` (new, beside soulseek_link) | `Sharing` service: map, verify, renew, expose state on `status["sharing"]` |
| `src/flackey/web/sharing.py` (new) | `GET /api/sharing`, `POST /api/sharing/check` |
| `src/flackey/web/__init__.py` | health gains `telegram_configured`, `source_enabled`, `sharing`; mounts the sharing router |
| `web/src/api.ts` | types + calls for the above |
| `web/src/components/setup/TelegramStep.tsx` | key fields when unconfigured, Skip link |
| `web/src/components/setup/ReadyStep.tsx` | copy reflects what is connected; "nowhere to fetch from" state |
| `web/src/components/SharingPanel.tsx` (new) | one component, `compact` for the wizard, full for Settings |
| `web/src/components/setup/SoulseekStep.tsx` | compact sharing line after sign-in |
| `web/src/components/SettingsPage.tsx` | Telegram keys override, Sharing row |
| `web/src/components/uploads/UploadsPage.tsx` | closed-port banner |
| `web/src/App.tsx`, `web/src/components/Sidebar.tsx` | skip wiring; banner only when the source is on |
| `packaging/build_app.sh`, `.gitignore`, `.env.example` | write `build.json` from env for local builds; ignore it |
| `README.md`, `packaging/README-for-friends.md` | Telegram, sharing and port paragraphs |
| `pyproject.toml` | import-linter layers |

Tasks 1–4 (Telegram), 5 (shares) and 6–9 (sharing) are independent of each other. 10 (docs) and 11 (fresh run) come last.

---

### Task 1: Build-time Telegram defaults in `load_settings`

**Files:**
- Modify: `src/flackey/config.py` (after `REPO_ENV`, and `load_settings`)
- Modify: `.gitignore`, `.env.example`, `packaging/build_app.sh`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `BUILD_DEFAULTS_PATH: Path` (module constant, `Path(__file__).with_name("assets") / "build.json"`), `_read_build_defaults(path=BUILD_DEFAULTS_PATH) -> dict` (only `telegram_api_id`, `telegram_api_hash`). `load_settings(env_file=None, build_defaults: Path | None = None)` — `build_defaults` overrides the path for tests.
- Precedence after this task: environment > .env > settings.json > build.json > class defaults.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_config.py`)

```python
def test_build_defaults_fill_telegram_keys_when_nothing_else_does(tmp_path: Path):
    env = tmp_path / ".env"
    env.write_text(f"DATA_DIR={tmp_path / 'data'}\n")
    build = tmp_path / "build.json"
    build.write_text(json.dumps({"telegram_api_id": 4242, "telegram_api_hash": "a" * 32}))
    s = load_settings(env, build_defaults=build)
    assert s.telegram_api_id == 4242 and s.telegram_api_hash == "a" * 32
    assert s.telegram_configured is True


def test_settings_file_and_env_beat_build_defaults(tmp_path: Path):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "settings.json").write_text(json.dumps({"telegram_api_hash": "from-file"}))
    build = tmp_path / "build.json"
    build.write_text(json.dumps({"telegram_api_id": 4242, "telegram_api_hash": "from-build"}))
    s = load_settings(_env(tmp_path), build_defaults=build)   # _env sets TELEGRAM_API_ID=123
    assert s.telegram_api_id == 123 and s.telegram_api_hash == "from-file"


def test_build_defaults_are_ignored_when_missing_or_broken(tmp_path: Path):
    env = tmp_path / ".env"
    env.write_text(f"DATA_DIR={tmp_path / 'data'}\n")
    assert load_settings(env, build_defaults=tmp_path / "absent.json").telegram_api_id is None
    broken = tmp_path / "broken.json"
    broken.write_text("{not json")
    assert load_settings(env, build_defaults=broken).telegram_api_id is None
    other_keys = tmp_path / "other.json"
    other_keys.write_text(json.dumps({"web_port": 1, "telegram_api_id": "not-an-int"}))
    s = load_settings(env, build_defaults=other_keys)
    assert s.web_port == 8765 and s.telegram_api_id is None   # only the two telegram keys, and only valid ones


def test_build_defaults_never_land_in_settings_json(tmp_path: Path):
    env = tmp_path / ".env"
    env.write_text(f"DATA_DIR={tmp_path / 'data'}\n")
    build = tmp_path / "build.json"
    build.write_text(json.dumps({"telegram_api_id": 4242, "telegram_api_hash": "a" * 32}))
    s = load_settings(env, build_defaults=build)
    save_settings(s, library_root=tmp_path / "lib")
    assert json.loads(s.settings_path.read_text()) == {"library_root": str(tmp_path / "lib")}
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_config.py -q -k build_defaults`
Expected: FAIL, `TypeError: load_settings() got an unexpected keyword argument 'build_defaults'`

- [ ] **Step 3: Implement**

In `src/flackey/config.py`, after `REPO_ENV = ...`:

```python
# Telegram keys baked into a packaged build. CI writes this file from repository secrets before
# PyInstaller runs (see .github/workflows and packaging/build_app.sh); a checkout has no such file and
# reads .env instead. Lowest precedence of all: anything the owner sets, in the environment or in
# settings.json, wins over what the build carries. Same location trick as desktop.APP_ICON -- inside the
# bundle `__file__` is <MEIPASS>/flackey/config.pyc and the assets folder is unpacked beside it.
BUILD_DEFAULTS_PATH = Path(__file__).with_name("assets") / "build.json"
BUILD_DEFAULT_KEYS = ("telegram_api_id", "telegram_api_hash")


def _read_build_defaults(path: Path = BUILD_DEFAULTS_PATH) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        log.warning("ignoring unreadable build defaults %s: %s", path, e)
        return {}
    if not isinstance(data, dict):
        return {}
    out = {}
    if isinstance(data.get("telegram_api_id"), int) and data["telegram_api_id"] > 0:
        out["telegram_api_id"] = data["telegram_api_id"]
    if isinstance(data.get("telegram_api_hash"), str) and data["telegram_api_hash"]:
        out["telegram_api_hash"] = data["telegram_api_hash"]
    return out
```

Change the signature and the merge in `load_settings`:

```python
def load_settings(env_file: Path | None = None, build_defaults: Path | None = None) -> Settings:
    """Precedence: environment > .env > settings.json in the data folder > build.json inside a packaged
    build > defaults. When nothing in that chain set an API key, fall back to the one flackey already
    generated for itself in the managed slskd.yml (see slskd_config.write_credentials) -- that file is
    the key's only copy."""
    if env_file is None:
        # `./.env` when run from the repo root; otherwise the repo's own .env, so `crate` works from any cwd
        env_file = Path(".env") if Path(".env").exists() else REPO_ENV
    base = Settings(_env_file=env_file)
    from_file = _read_settings_file(base.settings_path)
    from_build = _read_build_defaults(build_defaults or BUILD_DEFAULTS_PATH)
    overrides = {k: v for k, v in from_build.items() if k not in base.model_fields_set}
    overrides.update({k: v for k, v in from_file.items() if k not in base.model_fields_set})
```

(the rest of the function is unchanged: `if not overrides: result = base` …). Note `save_settings` only writes the keys it is given, so build defaults never reach settings.json — that is what the fourth test pins.

Append to `.gitignore`:

```
# Telegram keys baked into a packaged build; written by CI or build_app.sh, never committed
src/flackey/assets/build.json
```

In `packaging/build_app.sh`, before `echo "==> bundle"`:

```bash
echo "==> build defaults"
# The Telegram keys a packaged copy carries. From CI these come from repository secrets; locally, export
# FLACKEY_TELEGRAM_API_ID and FLACKEY_TELEGRAM_API_HASH before running this script, or leave them unset
# to build a copy whose setup screen asks for keys.
BUILD_JSON="src/flackey/assets/build.json"
if [[ -n "${FLACKEY_TELEGRAM_API_ID:-}" && -n "${FLACKEY_TELEGRAM_API_HASH:-}" ]]; then
  printf '{"telegram_api_id": %s, "telegram_api_hash": "%s"}\n' \
    "$FLACKEY_TELEGRAM_API_ID" "$FLACKEY_TELEGRAM_API_HASH" > "$BUILD_JSON"
  echo "    Telegram keys: baked in"
else
  rm -f "$BUILD_JSON"
  echo "    Telegram keys: none (setup will ask)"
fi
```

Append to `.env.example` a comment block:

```
# For a packaged build, the Telegram keys are baked in from FLACKEY_TELEGRAM_API_ID /
# FLACKEY_TELEGRAM_API_HASH at build time (packaging/build_app.sh); these two are for development only.
```

- [ ] **Step 4: Run the tests**

Run: `uv run pytest tests/test_config.py -q && uv run ruff check src tests`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/flackey/config.py tests/test_config.py .gitignore .env.example packaging/build_app.sh
git commit -m "feat: read Telegram keys baked into a packaged build, below everything the owner sets"
```

---

### Task 2: `TelegramLogin.reconfigure()` and the keys/skip routes

**Files:**
- Modify: `src/flackey/telegram.py` (class `TelegramLogin`)
- Modify: `src/flackey/web/telegram.py` (router signature and two routes)
- Modify: `src/flackey/web/__init__.py:130` (`telegram.router(login, status)` → `telegram.router(login, status, settings)`)
- Test: `tests/test_telegram.py`, `tests/test_web.py`

**Interfaces:**
- Produces: `async TelegramLogin.reconfigure() -> None` — sets `configured = True`, rebuilds the client via `make_client`, connects it. `POST /api/telegram/keys` body `{"api_id": int|str, "api_hash": str}` → `{"configured": true}`; 400 on bad input; 503 when `login is None`. `POST /api/telegram/skip` → `{"source_enabled": false}`, persists `source_enabled=False` via `save_settings`.
- Consumes: `save_settings` from `config.py`, `FILE_KEYS` already includes the three keys.

- [ ] **Step 1: Failing tests**

Append to `tests/test_telegram.py` (see the top of that file for the existing fake client; add a minimal one here so the test is self-contained):

```python
async def test_reconfigure_rebuilds_and_connects_the_client():
    from flackey.telegram import TelegramLogin

    class Client:
        def __init__(self):
            self.connected = False

        async def connect(self):
            self.connected = True

        def is_connected(self):
            return self.connected

        async def is_user_authorized(self):
            return False

    made = []

    def make():
        c = Client()
        made.append(c)
        return c

    login = TelegramLogin(Client(), False, make_client=make)
    assert (await login.status())["configured"] is False
    await login.reconfigure()
    assert login.configured is True and login.client is made[-1] and made[-1].connected
    assert (await login.status()) == {"authorized": False, "configured": True, "phone_masked": None}
```

Append to `tests/test_web.py`:

```python
def test_telegram_keys_route_saves_and_reconfigures(tmp_path: Path):
    from flackey.telegram import TelegramLogin

    class Client:
        async def connect(self): pass
        def is_connected(self): return True
        async def is_user_authorized(self): return False

    login = TelegramLogin(Client(), False, make_client=Client)
    app, _, settings = make(tmp_path, login=login)
    settings.telegram_api_id = None; settings.telegram_api_hash = None
    c = TestClient(app)
    assert c.post("/api/telegram/keys", json={"api_id": "abc", "api_hash": "x"}).status_code == 400
    assert c.post("/api/telegram/keys", json={"api_id": 12, "api_hash": ""}).status_code == 400
    r = c.post("/api/telegram/keys", json={"api_id": "4242", "api_hash": "b" * 32})
    assert r.status_code == 200 and r.json() == {"configured": True}
    assert login.configured is True
    saved = json.loads(settings.settings_path.read_text())
    assert saved["telegram_api_id"] == 4242 and saved["telegram_api_hash"] == "b" * 32
    assert "b" * 32 not in c.get("/api/settings").text
    assert c.get("/api/telegram/status").json()["configured"] is True


def test_telegram_skip_route_turns_the_source_off(tmp_path: Path):
    app, _, settings = make(tmp_path)
    c = TestClient(app)
    r = c.post("/api/telegram/skip")
    assert r.status_code == 200 and r.json() == {"source_enabled": False}
    assert settings.source_enabled is False
    assert json.loads(settings.settings_path.read_text())["source_enabled"] is False
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_telegram.py tests/test_web.py -q -k "reconfigure or telegram_keys or telegram_skip"`
Expected: FAIL (`AttributeError: reconfigure`, 404s).

- [ ] **Step 3: Implement**

In `src/flackey/telegram.py`, add to `TelegramLogin` after `status()`:

```python
    async def reconfigure(self) -> None:
        """Keys arrived after startup (the setup screen or Settings saved them): build a client that
        carries them and connect it. `make_client` closes over the live Settings object, so the values
        it reads are the ones just saved. Without this the wizard would have to say "restart the app"."""
        if self.make_client is None:
            self.configured = True
            return
        self._cancel_qr()
        self.client = self.make_client()
        self.configured = True
        await self.client.connect()
        log.info("Telegram client rebuilt with the keys just saved")
```

In `src/flackey/web/telegram.py`:

```python
from ..config import Settings, save_settings
...
def router(login: TelegramLogin | None, status: dict, settings: Settings | None = None) -> APIRouter:
```

and add before `return r`:

```python
    @r.post("/keys")
    async def keys(body: dict) -> dict:
        """Save an api_id and api_hash the owner made at my.telegram.org and bring Telegram up under
        them. Most copies never see this: a packaged build carries keys already (config.BUILD_DEFAULTS)."""
        if settings is None:
            raise HTTPException(503, "Settings are not available in this process")
        raw_id = str(body.get("api_id") or "").strip()
        api_hash = str(body.get("api_hash") or "").strip()
        if not raw_id.isdigit() or int(raw_id) <= 0:
            raise HTTPException(400, "The API id is a number, for example 1234567.")
        if len(api_hash) < 16:
            raise HTTPException(400, "The API hash is the long string next to the id at my.telegram.org.")
        save_settings(settings, telegram_api_id=int(raw_id), telegram_api_hash=api_hash)
        await guard(need().reconfigure())
        return {"configured": True}

    @r.post("/skip")
    async def skip() -> dict:
        """The owner chose not to connect Telegram. The bot is one of two sources, so turning it off is a
        real mode (worker.py checks `source_enabled`), not a missing step; signing in later turns it on."""
        if settings is None:
            raise HTTPException(503, "Settings are not available in this process")
        save_settings(settings, source_enabled=False)
        return {"source_enabled": False}
```

In `src/flackey/web/__init__.py` change the mount to `app.include_router(telegram.router(login, status, settings))`.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_telegram.py tests/test_web.py -q && uv run ruff check src tests && uv run lint-imports`
Expected: pass; `web` already sits above `config` in the layers so no contract change.

- [ ] **Step 5: Commit**

```bash
git add src/flackey/telegram.py src/flackey/web/telegram.py src/flackey/web/__init__.py tests/test_telegram.py tests/test_web.py
git commit -m "feat: accept Telegram keys from the setup screen, and let the step be skipped"
```

---

### Task 3: The worker runs without Telegram when the source is off

**Files:**
- Modify: `src/flackey/worker.py:312` (`run_forever` loop condition)
- Modify: `src/flackey/app.py` (`supervise_worker`, `on_authorized`, health flags)
- Modify: `src/flackey/web/__init__.py` (health adds `telegram_configured`, `source_enabled`)
- Test: `tests/test_app.py`, `tests/test_worker.py`, `tests/test_web.py`

**Interfaces:**
- Produces: `supervise_worker(worker, status, poll_s=1.0, run_when: Callable[[], bool] | None = None)`; default `run_when` is `lambda: bool(status.get("telegram_authorized"))`. Health JSON gains `"telegram_configured": bool` and `"source_enabled": bool`.
- The on_authorized callback in `app._run` becomes a function that sets `status["telegram_authorized"] = True` **and** `save_settings(settings, source_enabled=True)` — a sign-in re-enables the source a skip turned off.

- [ ] **Step 1: Failing tests**

`tests/test_app.py`:

```python
async def test_supervisor_runs_the_worker_when_run_when_says_so_without_telegram():
    status = {"telegram_authorized": False, "worker_running": False}
    runs = []
    source_on = [False]

    class W:
        async def run_forever(self):
            runs.append(status["worker_running"])
            source_on[0] = True   # stop after one run

    task = asyncio.create_task(supervise_worker(W(), status, poll_s=0.01,
                                                run_when=lambda: status["telegram_authorized"] or not source_on[0]))
    await asyncio.sleep(0.05)
    assert runs == [True] and status["worker_running"] is False
    task.cancel()
```

`tests/test_worker.py` — find the existing helper that builds a worker (`make_worker` or the fixture near the top; reuse it) and add:

```python
async def test_run_forever_keeps_going_with_telegram_signed_out_when_the_source_is_off(tmp_path):
    w, *_ = make_worker(tmp_path)          # whatever the file's builder is called; it returns the worker first
    w.settings.source_enabled = False
    w.status["telegram_authorized"] = False
    ticks = []

    async def stop_after_two():
        ticks.append(1)
        if len(ticks) == 2:
            w.settings.source_enabled = True   # now the loop condition is false on the next check

    w._maintenance = stop_after_two
    await asyncio.wait_for(w.run_forever(poll_s=0.01), timeout=2)
    assert len(ticks) == 2
```

`tests/test_web.py` — extend `test_health`'s expected dict with `"telegram_configured": True, "source_enabled": True` (the `make()` settings carry keys), and add:

```python
def test_health_says_when_the_source_is_off(tmp_path: Path):
    app, _, settings = make(tmp_path)
    settings.source_enabled = False
    assert TestClient(app).get("/api/health").json()["source_enabled"] is False
```

- [ ] **Step 2: Verify failure**

Run: `uv run pytest tests/test_app.py tests/test_worker.py tests/test_web.py -q -k "run_when or source_is_off or test_health"`
Expected: FAIL.

- [ ] **Step 3: Implement**

`src/flackey/worker.py:312`:

```python
            while self.status.get("telegram_authorized", True) or not self.settings.source_enabled:
```

and the log line after the loop becomes conditional so a source-off worker that stops on Ctrl-C does not claim a login is required:

```python
        if self.settings.source_enabled:
            log.error("worker stopped: %s (sign in from the setup screen in the UI)", LOGIN_REQUIRED)
```

`src/flackey/app.py`:

```python
async def supervise_worker(worker, status: dict, poll_s: float = 1.0,
                           run_when: Callable[[], bool] | None = None) -> None:
    """The worker stops itself when the Telegram session dies; start it again once the setup screen
    has signed the account back in. `run_when` is the gate: by default "Telegram is authorized", and
    app._run widens it to "or the Telegram source is switched off", because a Soulseek-only copy has
    nothing to sign in to."""
    if run_when is None:
        run_when = lambda: bool(status.get("telegram_authorized"))  # noqa: E731
    while True:
        if run_when():
            status["worker_running"] = True
            ...   # unchanged body
```

In `_run`: replace the `on_authorized=lambda: status.__setitem__("telegram_authorized", True)` with a named function defined just above `make_client`:

```python
    def on_authorized() -> None:
        status["telegram_authorized"] = True
        if not settings.source_enabled:
            # A sign-in after a skipped setup step: the bot is a source again.
            save_settings(settings, source_enabled=True)
```

(import `save_settings` from `.config`). Pass `on_authorized=on_authorized` in both `TelegramLogin(...)` constructions (the unconfigured branch too, since `reconfigure()` can make it configured later). Then:

```python
        await run_until_server_stops(
            serve(server, url, handle),
            supervise_worker(worker, status,
                             run_when=lambda: bool(status.get("telegram_authorized")) or not settings.source_enabled),
            close_streams_on_exit(server, bus))
```

Also the startup log: `log.info("flackey started%s", ", worker running" if status["telegram_authorized"] or not settings.source_enabled else "")`.

`src/flackey/web/__init__.py` health dict: add `"telegram_configured": settings.telegram_configured, "source_enabled": settings.source_enabled,` after `"setup_done"`.

- [ ] **Step 4: Run the full suite**

Run: `uv run pytest -q && uv run ruff check src tests && uv run lint-imports`
Expected: pass (fix any health assertions elsewhere in test_web.py that compare the whole dict).

- [ ] **Step 5: Commit**

```bash
git add src/flackey/worker.py src/flackey/app.py src/flackey/web/__init__.py tests/test_app.py tests/test_worker.py tests/test_web.py
git commit -m "feat: run the worker without Telegram when the bot source is switched off"
```

---

### Task 4: Telegram step with key fields and Skip; Ready step copy; banner only when the source is on

**Files:**
- Modify: `web/src/api.ts` (Health type, `telegramKeys`, `skipTelegram`)
- Modify: `web/src/components/setup/TelegramStep.tsx`
- Modify: `web/src/components/setup/ReadyStep.tsx`
- Modify: `web/src/App.tsx`, `web/src/components/Sidebar.tsx`
- Test: `web/src/components/setup/TelegramStep.test.tsx`, `web/src/components/setup/ReadyStep.test.tsx`, `web/src/components/Sidebar.test.tsx`

**Interfaces:**
- `api.telegramKeys(api_id: string, api_hash: string)` → `POST /api/telegram/keys`; `api.skipTelegram()` → `POST /api/telegram/skip`.
- `Health` gains `telegram_configured: boolean; source_enabled: boolean` (optional in the type to keep older fixtures compiling: `telegram_configured?: boolean; source_enabled?: boolean`).
- `TelegramStep` props: `{ onDone: () => void; onSkip: () => void; pollMs?: number }`.
- `ReadyStep` props gain `telegram: 'connected' | 'skipped'` and `soulseek: 'connected' | 'pending' | 'skipped'` (replacing `soulseekPending?: boolean`).
- `Sidebar` prop `telegramAuthorized` stays; new optional `sourceEnabled?: boolean` (default true).

- [ ] **Step 1: Failing tests**

`TelegramStep.test.tsx`, add:

```tsx
it('asks for the API keys when this copy has none, then goes on to the QR', async () => {
  vi.spyOn(api, 'telegramStatus').mockResolvedValue({ authorized: false, configured: false, phone_masked: null })
  vi.spyOn(api, 'telegramKeys').mockResolvedValue({ configured: true })
  const qrStart = vi.spyOn(api, 'qrStart').mockResolvedValue({ id: 'q1', url: 'tg://x', expires_at: '2999-01-01T00:00:00+00:00' })
  vi.spyOn(api, 'qrState').mockResolvedValue({ state: 'waiting' })
  render(<TelegramStep onDone={vi.fn()} onSkip={vi.fn()} pollMs={10} />)
  await waitFor(() => expect(screen.getByText(/needs Telegram API keys/)).toBeInTheDocument())
  expect(qrStart).not.toHaveBeenCalled()
  expect(screen.getByRole('link', { name: /my\.telegram\.org/ })).toHaveAttribute('href', 'https://my.telegram.org/apps')
  fireEvent.change(screen.getByLabelText('API id'), { target: { value: '4242' } })
  fireEvent.change(screen.getByLabelText('API hash'), { target: { value: 'b'.repeat(32) } })
  fireEvent.click(screen.getByText('Save keys'))
  await waitFor(() => expect(api.telegramKeys).toHaveBeenCalledWith('4242', 'b'.repeat(32)))
  await waitFor(() => expect(qrStart).toHaveBeenCalled())
})

it('offers Skip for now on every screen and calls onSkip', async () => {
  vi.spyOn(api, 'qrStart').mockResolvedValue({ id: 'q1', url: 'tg://x', expires_at: '2999-01-01T00:00:00+00:00' })
  vi.spyOn(api, 'qrState').mockResolvedValue({ state: 'waiting' })
  vi.spyOn(api, 'skipTelegram').mockResolvedValue({ source_enabled: false })
  const onSkip = vi.fn()
  render(<TelegramStep onDone={vi.fn()} onSkip={onSkip} pollMs={10} />)
  fireEvent.click(await screen.findByText('Skip for now'))
  await waitFor(() => expect(api.skipTelegram).toHaveBeenCalled())
  expect(onSkip).toHaveBeenCalled()
})
```

Update the three existing TelegramStep tests to pass `onSkip={vi.fn()}`.

`ReadyStep.test.tsx`: replace the two `soulseekPending` tests with:

```tsx
it('says what is connected', async () => {
  vi.spyOn(api, 'tools').mockResolvedValue({ ffmpeg: true, ffprobe: true, yt_dlp: true })
  render(<ReadyStep libraryRoot="/tmp/lib" onStart={vi.fn()} telegram="connected" soulseek="pending" />)
  await waitFor(() => expect(screen.getByText(/Telegram is connected/)).toBeInTheDocument())
  expect(screen.getByText(/next time you open Flackey/)).toBeInTheDocument()
})

it('says there is nowhere to fetch from when both sources were skipped', async () => {
  vi.spyOn(api, 'tools').mockResolvedValue({ ffmpeg: true, ffprobe: true, yt_dlp: true })
  render(<ReadyStep libraryRoot="/tmp/lib" onStart={vi.fn()} telegram="skipped" soulseek="skipped" />)
  await waitFor(() => expect(screen.getByText(/nowhere to fetch from yet/)).toBeInTheDocument())
  expect(screen.getByText('Start digging')).toBeInTheDocument()   // still allowed to finish
})
```

Update the remaining ReadyStep tests to pass `telegram="connected" soulseek="skipped"`.

`Sidebar.test.tsx`, add (mirror the file's existing render helper):

```tsx
it('says Telegram is off, not signed out, when the source is switched off', () => {
  render(<Sidebar tab="download" onTab={() => {}} telegramAuthorized={false} sourceEnabled={false} inset={false} />)
  expect(screen.getByText('Telegram off')).toBeInTheDocument()
  expect(screen.queryByText('Telegram signed out')).not.toBeInTheDocument()
})
```

- [ ] **Step 2: Verify failure**

Run: `npm --prefix web test -- --run`
Expected: the new tests fail (missing props/functions; tsc errors surface at build, not here).

- [ ] **Step 3: Implement**

`web/src/api.ts`: in `Health` add `telegram_configured?: boolean; source_enabled?: boolean;`. In `api`:

```ts
  telegramKeys: (api_id: string, api_hash: string) => post<{ configured: boolean }>('/api/telegram/keys', { api_id, api_hash }),
  skipTelegram: () => post<{ source_enabled: boolean }>('/api/telegram/skip'),
```

`TelegramStep.tsx`: add state `const [needKeys, setNeedKeys] = useState(false); const [apiId, setApiId] = useState(''); const [apiHash, setApiHash] = useState(''); const [keysErr, setKeysErr] = useState<string | null>(null)`. In the status effect: `setNeedKeys(!s.configured)` alongside `setConnected(...)`. Guard the QR effect: `if (connected !== null || needKeys) return` and add `needKeys` to its deps. Add:

```tsx
  const saveKeys = () => {
    if (busy) return
    setBusy(true); setKeysErr(null)
    api.telegramKeys(apiId.trim(), apiHash.trim()).then(() => setNeedKeys(false))
      .catch(e => setKeysErr(e instanceof ApiError ? e.message : 'Could not save the keys. Try again.'))
      .finally(() => setBusy(false))
  }
  const skip = () => {
    if (busy) return
    setBusy(true)
    api.skipTelegram().then(() => onSkip()).catch(fail).finally(() => setBusy(false))
  }
  const skipRow = <div className="row-gap"><button className="btn-link" onClick={skip} disabled={busy}>Skip for now</button></div>
```

Render the keys screen before the QR/phone grid (after the `connected !== null` early return):

```tsx
  if (needKeys) {
    return (<div className="tg-done">
      <h1>This copy needs Telegram API keys</h1>
      <p className="lead">Telegram asks every app to identify itself. Sign in at <a href="https://my.telegram.org/apps" target="_blank" rel="noreferrer">my.telegram.org</a>, create an app under API development tools, and paste its id and hash here. They identify Flackey, not you.</p>
      <div className="fields">
        <div className="field"><label htmlFor="tg-api-id">API id</label>
          <input id="tg-api-id" className="input" inputMode="numeric" value={apiId} onChange={e => setApiId(e.target.value)} disabled={busy} /></div>
        <div className="field"><label htmlFor="tg-api-hash">API hash</label>
          <input id="tg-api-hash" className="input" spellCheck={false} value={apiHash} onChange={e => setApiHash(e.target.value)} disabled={busy} /></div>
      </div>
      <div className="row-gap"><button className="btn-primary" onClick={saveKeys} disabled={busy || !apiId.trim() || !apiHash.trim()}>Save keys</button></div>
      {keysErr && <div className="err">{keysErr}</div>}
      {skipRow}
      <div className="footnote">Skipping leaves Telegram off. Flackey then fetches from Soulseek only, and you can connect Telegram later from Settings.</div>
    </div>)
  }
```

Add `{skipRow}` at the bottom of the QR branch and of the phone branch (just above the existing footnote). Prop type becomes `{ onDone: () => void; onSkip: () => void; pollMs?: number }`.

`ReadyStep.tsx`: props `{ libraryRoot, onStart, error, telegram, soulseek }: { libraryRoot: string; onStart: () => Promise<void>; error?: string | null; telegram: 'connected' | 'skipped'; soulseek: 'connected' | 'pending' | 'skipped' }`. Replace the "You're set" block's lead and hint:

```tsx
  const nothing = telegram === 'skipped' && soulseek === 'skipped'
  const sources = [telegram === 'connected' ? 'Telegram is connected' : null,
    soulseek === 'connected' ? 'Soulseek is connected' : null].filter(Boolean).join(' and ')
  ...
    <h1>{nothing ? 'Almost set' : "You're set"}</h1>
    <p className="lead">{nothing
      ? 'There is nowhere to fetch from yet: Telegram and Soulseek are both off. You can turn either on later from Settings. Your music will be filed into'
      : `${sources || 'Your sources are saved'} and your music will be filed into`}</p>
    <div className="path-well">{libraryRoot}</div>
    {soulseek === 'pending' && <div className="hint-row">Soulseek is saved. It starts looking for lossless copies the next time you open Flackey.</div>}
```

`App.tsx`: add `const [telegramSkipped, setTelegramSkipped] = useState(false)` and `const [soulseekState, setSoulseekState] = useState<'connected' | 'pending' | 'skipped'>('skipped')`; remove `soulseekPending`. Wire:

```tsx
      {current === 2 && <TelegramStep onDone={() => { setTelegramSkipped(false); setStep(reconnecting ? 4 : 3) }} onSkip={() => { setTelegramSkipped(true); setStep(3) }} />}
      {current === 3 && <SoulseekStep onDone={c => { setSoulseekState(c ? 'connected' : 'pending'); setStep(4) }} onSkip={() => { setSoulseekState('skipped'); setStep(4) }} />}
      {current === 4 && <ReadyStep libraryRoot={libraryRoot} onStart={startApp} error={setupError} telegram={telegramSkipped ? 'skipped' : 'connected'} soulseek={soulseekState} />}
```

Banner: `const sourceOn = h.source_enabled ?? true` and `const banner = sourceOn && !authorized ? <Banner …/> : undefined`. Pass `sourceEnabled={sourceOn}` to `Shell` and on to `Sidebar` (add the prop to `Shell`'s props and forward it).

`Sidebar.tsx`: `footer(telegramAuthorized, lossless, sourceEnabled = true)`: the Telegram line becomes `sourceEnabled ? { ok: telegramAuthorized, text: telegramAuthorized ? 'Telegram connected' : 'Telegram signed out' } : { ok: true, text: 'Telegram off' }`.

- [ ] **Step 4: Run tests and the type-checked build**

Run: `npm --prefix web test -- --run && npm --prefix web run build`
Expected: pass. Fix any test elsewhere (App tests, Shell tests) that renders `ReadyStep`/`TelegramStep` with the old props.

- [ ] **Step 5: Commit**

```bash
git add web/src
git commit -m "feat: let the Telegram step take keys or be skipped, and say what the Ready step is ready with"
```

---

### Task 5: Share the library folder through slskd

**Files:**
- Modify: `src/flackey/slskd_config.py` (`write_credentials` signature, new `write_share`, helper `_set_share`)
- Modify: `src/flackey/web/library.py` (PUT /settings, POST /setup/soulseek)
- Test: `tests/test_slskd_config.py`, `tests/test_web.py`

**Interfaces:**
- `write_credentials(data_dir, username, password, library_root: Path | None = None) -> str` — when `library_root` is given, `shares.directories` contains `str(library_root)`.
- `write_share(data_dir: Path, library_root: Path, previous: Path | None = None) -> bool` — returns False without touching anything when no slskd.yml exists (Soulseek was never set up); otherwise ensures `str(library_root)` is in `shares.directories`, drops `str(previous)` if present and different, keeps every other entry (an owner's extra hand-written share survives), writes atomically 0600, returns True.
- PUT /api/settings: when `library_root` changes, calls `write_share(settings.data_dir, new, previous=old)`; when it returns True and `link` is not None, schedules `link.rescan_shares()` (add that coroutine to `SoulseekLink`: `for p in self._worker.providers: await p.rescan_shares()` wrapped in try/except LosslessError logging at warning).

- [ ] **Step 1: Failing tests** (append to `tests/test_slskd_config.py`)

```python
def test_credentials_write_shares_the_library_folder(tmp_path: Path):
    write_credentials(tmp_path, "digger", "not-a-real-password", library_root=tmp_path / "DJ Library")
    data = yaml.safe_load(config_path(tmp_path).read_text())
    assert data["shares"]["directories"] == [str(tmp_path / "DJ Library")]


def test_credentials_write_keeps_a_hand_written_share_and_adds_the_library(tmp_path: Path):
    path = config_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text(yaml.safe_dump({"shares": {"directories": ["/Volumes/Extra/Music"]}}))
    write_credentials(tmp_path, "digger", "not-a-real-password", library_root=tmp_path / "DJ Library")
    data = yaml.safe_load(path.read_text())
    assert data["shares"]["directories"] == ["/Volumes/Extra/Music", str(tmp_path / "DJ Library")]


def test_write_share_moves_the_share_with_the_library_folder(tmp_path: Path):
    from flackey.slskd_config import write_share
    old, new = tmp_path / "old", tmp_path / "new"
    write_credentials(tmp_path, "digger", "not-a-real-password", library_root=old)
    assert write_share(tmp_path, new, previous=old) is True
    data = yaml.safe_load(config_path(tmp_path).read_text())
    assert data["shares"]["directories"] == [str(new)]
    assert data["soulseek"]["username"] == "digger"           # nothing else touched
    assert config_path(tmp_path).stat().st_mode & 0o777 == 0o600
    assert write_share(tmp_path, new, previous=old) is True   # idempotent
    assert yaml.safe_load(config_path(tmp_path).read_text())["shares"]["directories"] == [str(new)]


def test_write_share_does_nothing_without_a_config(tmp_path: Path):
    from flackey.slskd_config import write_share
    assert write_share(tmp_path, tmp_path / "lib") is False
    assert not config_path(tmp_path).exists()
```

Append to `tests/test_web.py`:

```python
def test_changing_the_library_folder_moves_the_soulseek_share(tmp_path: Path):
    import yaml
    from flackey.slskd_config import config_path, write_credentials
    app, _, settings = make(tmp_path)
    write_credentials(settings.data_dir, "digger", "not-a-real-password", library_root=settings.library_root)
    c = TestClient(app)
    new = tmp_path / "moved"
    assert c.put("/api/settings", json={"library_root": str(new)}).status_code == 200
    assert yaml.safe_load(config_path(settings.data_dir).read_text())["shares"]["directories"] == [str(new)]


def test_soulseek_setup_shares_the_library(tmp_path: Path):
    import yaml
    from flackey.slskd_config import config_path
    app, _, settings = make(tmp_path)
    c = TestClient(app)
    assert c.post("/api/setup/soulseek", json={"username": "digger", "password": "not-a-real-password"}).status_code == 200
    data = yaml.safe_load(config_path(settings.data_dir).read_text())
    assert data["shares"]["directories"] == [str(settings.library_root)]
```

- [ ] **Step 2: Verify failure**

Run: `uv run pytest tests/test_slskd_config.py tests/test_web.py -q -k "share"`
Expected: FAIL.

- [ ] **Step 3: Implement**

In `src/flackey/slskd_config.py`, signature `def write_credentials(data_dir: Path, username: str, password: str, library_root: Path | None = None) -> str:` and, just before `_atomic_write(path, config)`:

```python
    if library_root is not None:
        _set_share(config, path, library_root)
```

Add the helper and the public function after `_atomic_write`:

```python
def _set_share(config: dict, path: Path, library_root: Path, previous: Path | None = None) -> None:
    """Make the library folder one of slskd's shared directories. Sharing is what keeps a Soulseek user in
    good standing -- many peers refuse anyone who offers nothing -- and the spec (2026-09-07 §2) says the
    whole DJ Library is shared. Other entries are the owner's and stay; a previous library folder goes,
    because it is the same share moved, not a second one."""
    shares = _submapping(config, "shares", path)
    dirs = shares.get("directories")
    if not isinstance(dirs, list):
        dirs = []
    dirs = [d for d in dirs if isinstance(d, str)]
    if previous is not None and str(previous) != str(library_root):
        dirs = [d for d in dirs if d != str(previous)]
    if str(library_root) not in dirs:
        dirs.append(str(library_root))
    shares["directories"] = dirs


def write_share(data_dir: Path, library_root: Path, previous: Path | None = None) -> bool:
    """Point the share at the library folder, on a config that already exists. False when Soulseek was never
    set up (no file), so a folder change on a Telegram-only install writes nothing. Raises
    SlskdConfigError when the file is present but unreadable or invalid."""
    path = config_path(data_dir)
    if not path.exists():
        return False
    config = _load_config(path)
    _set_share(config, path, library_root, previous)
    _atomic_write(path, config)
    log.info("slskd share now %s", library_root)
    return True
```

If `write_credentials` reads the file inline (it does today: the `if path.exists(): … config = loaded` block), extract that block into `_load_config(path) -> dict` (raising `SlskdConfigError` exactly as the inline code does) and call it from both places.

`src/flackey/web/library.py`: import `write_share`; in `post_soulseek_setup` pass `library_root=settings.library_root` to `write_credentials`. In `put_settings`, before `save_settings(...)`: `previous = settings.library_root`; after it:

```python
        if path != previous:
            try:
                moved = write_share(settings.data_dir, path, previous=previous)
            except SlskdConfigError as e:
                log.warning("library folder changed but the Soulseek share was not updated: %s", e)
                moved = False
            if moved and link is not None:
                asyncio.create_task(link.rescan_shares())
```

(`import asyncio` at the top.) In `src/flackey/soulseek_link.py` add:

```python
    async def rescan_shares(self) -> None:
        """After the shared folder moved: ask the running providers to rescan. Best effort -- slskd also
        watches its config file, and the daily maintenance rescan is the backstop."""
        for p in list(self._worker.providers):
            try:
                await p.rescan_shares()
            except (LosslessError, httpx.HTTPError, OSError) as e:
                log.warning("%s: rescan after the library moved failed: %s", p.name, e)
```

- [ ] **Step 4: Run**

Run: `uv run pytest -q && uv run ruff check src tests && uv run lint-imports`
Expected: pass. Existing `test_fresh_write_produces_the_full_template_at_mode_0600` still passes (no `library_root` → no `shares` key).

- [ ] **Step 5: Commit**

```bash
git add src/flackey/slskd_config.py src/flackey/web/library.py src/flackey/soulseek_link.py tests/test_slskd_config.py tests/test_web.py
git commit -m "feat: share the DJ Library through Soulseek, and move the share with the folder"
```

---

### Task 6: `portmap` — NAT-PMP then UPnP, pure Python, injectable transport

**Files:**
- Create: `src/flackey/portmap.py`
- Modify: `pyproject.toml` (`[tool.importlinter]` layers: add `"portmap : portcheck"` as the **last** entry)
- Test: `tests/test_portmap.py`

**Interfaces (produces):**

```python
@dataclass(frozen=True)
class Mapping:
    protocol: str            # "natpmp" | "upnp"
    gateway: str             # router address the mapping was made on
    internal_port: int
    external_port: int
    lease_s: int
    control_url: str | None = None   # UPnP only; needed to delete
    service_type: str | None = None  # UPnP only

def default_gateway(run=subprocess.run) -> str | None
def lan_ip(gateway: str | None = None) -> str | None
async def natpmp_map(gateway: str, port: int, lease_s: int, *, transport=_udp_exchange) -> Mapping | None
async def upnp_map(port: int, lease_s: int, http: httpx.AsyncClient, *, discover=_ssdp_discover, internal_ip: str | None = None) -> Mapping | None
async def map_port(port: int, lease_s: int = 3600, http: httpx.AsyncClient | None = None, *, gateway: str | None = None, natpmp=natpmp_map, upnp=upnp_map) -> Mapping | None
async def unmap_port(mapping: Mapping, http: httpx.AsyncClient | None = None, *, transport=_udp_exchange) -> None
```

Every function returns `None` (or does nothing) on failure and logs at INFO; nothing here raises to callers.

- [ ] **Step 1: Failing tests** (`tests/test_portmap.py`)

```python
import struct

import httpx
import pytest
import respx

from flackey import portmap
from flackey.portmap import Mapping, default_gateway, map_port, natpmp_map, unmap_port, upnp_map


def test_default_gateway_parses_route_output_on_mac():
    class R:
        stdout = "   route to: default\ndestination: default\n       mask: default\n    gateway: 10.0.0.1\n  interface: en0\n"
        returncode = 0
    assert default_gateway(run=lambda *a, **k: R()) == "10.0.0.1"


def test_default_gateway_is_none_when_the_command_fails():
    def boom(*a, **k):
        raise OSError("no route")
    assert default_gateway(run=boom) is None


async def test_natpmp_map_sends_a_tcp_mapping_request_and_reads_the_reply():
    sent = []

    async def transport(gateway, payload, timeout):
        sent.append((gateway, payload))
        # version 0, opcode 128+2, result 0, epoch, private port, mapped public port, lifetime
        return struct.pack("!BBHIHHI", 0, 130, 0, 1234, 50300, 50300, 3600)

    m = await natpmp_map("10.0.0.1", 50300, 3600, transport=transport)
    assert m == Mapping("natpmp", "10.0.0.1", 50300, 50300, 3600)
    gateway, payload = sent[0]
    assert gateway == "10.0.0.1"
    assert payload == struct.pack("!BBHHHI", 0, 2, 0, 50300, 50300, 3600)


async def test_natpmp_map_returns_none_on_error_result_or_no_reply():
    async def refused(gateway, payload, timeout):
        return struct.pack("!BBHIHHI", 0, 130, 2, 1234, 50300, 0, 0)   # result 2 = not authorized

    async def silent(gateway, payload, timeout):
        raise TimeoutError

    assert await natpmp_map("10.0.0.1", 50300, 3600, transport=refused) is None
    assert await natpmp_map("10.0.0.1", 50300, 3600, transport=silent) is None


DESC = """<?xml version="1.0"?><root xmlns="urn:schemas-upnp-org:device-1-0">
<device><friendlyName>Test Router</friendlyName><deviceList><device><deviceList><device>
<serviceList><service><serviceType>urn:schemas-upnp-org:service:WANIPConnection:1</serviceType>
<controlURL>/ctl/IPConn</controlURL></service></serviceList>
</device></deviceList></device></deviceList></device></root>"""


@respx.mock
async def test_upnp_map_finds_the_wan_service_and_posts_add_port_mapping():
    respx.get("http://10.0.0.1:1900/desc.xml").mock(return_value=httpx.Response(200, text=DESC))
    soap = respx.post("http://10.0.0.1:1900/ctl/IPConn").mock(return_value=httpx.Response(200, text="<ok/>"))

    async def discover(timeout):
        return ["http://10.0.0.1:1900/desc.xml"]

    async with httpx.AsyncClient() as http:
        m = await upnp_map(50300, 3600, http, discover=discover, internal_ip="10.0.0.5")
    assert m == Mapping("upnp", "10.0.0.1", 50300, 50300, 3600,
                        control_url="http://10.0.0.1:1900/ctl/IPConn",
                        service_type="urn:schemas-upnp-org:service:WANIPConnection:1")
    body = soap.calls[0].request.content.decode()
    assert "<NewExternalPort>50300</NewExternalPort>" in body and "<NewInternalClient>10.0.0.5</NewInternalClient>" in body
    assert "<NewProtocol>TCP</NewProtocol>" in body and "<NewLeaseDuration>3600</NewLeaseDuration>" in body
    assert soap.calls[0].request.headers["SOAPAction"] == '"urn:schemas-upnp-org:service:WANIPConnection:1#AddPortMapping"'


@respx.mock
async def test_upnp_map_returns_none_when_the_router_refuses():
    respx.get("http://10.0.0.1:1900/desc.xml").mock(return_value=httpx.Response(200, text=DESC))
    respx.post("http://10.0.0.1:1900/ctl/IPConn").mock(return_value=httpx.Response(500, text="<fault/>"))

    async def discover(timeout):
        return ["http://10.0.0.1:1900/desc.xml"]

    async with httpx.AsyncClient() as http:
        assert await upnp_map(50300, 3600, http, discover=discover, internal_ip="10.0.0.5") is None


async def test_upnp_map_returns_none_when_nothing_answers_ssdp():
    async def discover(timeout):
        return []

    async with httpx.AsyncClient() as http:
        assert await upnp_map(50300, 3600, http, discover=discover, internal_ip="10.0.0.5") is None


async def test_map_port_tries_natpmp_first_then_upnp():
    calls = []

    async def natpmp(gateway, port, lease_s, **kw):
        calls.append("natpmp")
        return None

    async def upnp(port, lease_s, http, **kw):
        calls.append("upnp")
        return Mapping("upnp", "10.0.0.1", port, port, lease_s, control_url="u", service_type="s")

    m = await map_port(50300, 3600, gateway="10.0.0.1", natpmp=natpmp, upnp=upnp)
    assert m is not None and m.protocol == "upnp" and calls == ["natpmp", "upnp"]


async def test_map_port_skips_natpmp_without_a_gateway():
    async def natpmp(*a, **k):
        raise AssertionError("must not be called")

    async def upnp(port, lease_s, http, **kw):
        return None

    assert await map_port(50300, 3600, gateway=None, natpmp=natpmp, upnp=upnp) is None


async def test_unmap_sends_a_zero_lifetime_natpmp_request():
    sent = []

    async def transport(gateway, payload, timeout):
        sent.append(payload)
        return struct.pack("!BBHIHHI", 0, 130, 0, 1, 50300, 0, 0)

    await unmap_port(Mapping("natpmp", "10.0.0.1", 50300, 50300, 3600), transport=transport)
    assert sent[0] == struct.pack("!BBHHHI", 0, 2, 0, 50300, 0, 0)


@respx.mock
async def test_unmap_posts_delete_port_mapping_for_upnp():
    soap = respx.post("http://10.0.0.1:1900/ctl/IPConn").mock(return_value=httpx.Response(200, text="<ok/>"))
    m = Mapping("upnp", "10.0.0.1", 50300, 50300, 3600, control_url="http://10.0.0.1:1900/ctl/IPConn",
                service_type="urn:schemas-upnp-org:service:WANIPConnection:1")
    async with httpx.AsyncClient() as http:
        await unmap_port(m, http)
    assert "DeletePortMapping" in soap.calls[0].request.headers["SOAPAction"]
```

- [ ] **Step 2: Verify failure**

Run: `uv run pytest tests/test_portmap.py -q`
Expected: `ModuleNotFoundError: flackey.portmap`.

- [ ] **Step 3: Implement `src/flackey/portmap.py`**

```python
"""Ask the router to open the Soulseek listen port.

Two protocols, tried in order: NAT-PMP (RFC 6886, one UDP round trip to the gateway; Apple routers and
many others) and UPnP IGD (SSDP discovery, then one SOAP call to the WANIPConnection service; most
consumer routers). That is what the official Soulseek client and every BitTorrent client do, and it is
why their users have an open port without ever logging in to the router.

Everything here fails soft: a router that answers neither, a VPN, a carrier-grade NAT, a double NAT --
all come back as None, and `sharing.py` then verifies from outside and tells the user what to do by hand.
No new dependencies: the two protocols are a few dozen bytes each.
"""

from __future__ import annotations

import asyncio
import logging
import re
import socket
import struct
import subprocess
import sys
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit
from xml.etree import ElementTree

import httpx

log = logging.getLogger(__name__)

NATPMP_PORT = 5351
SSDP_ADDR = ("239.255.255.250", 1900)
WAN_SERVICES = ("urn:schemas-upnp-org:service:WANIPConnection:2",
                "urn:schemas-upnp-org:service:WANIPConnection:1",
                "urn:schemas-upnp-org:service:WANPPPConnection:1")
IGD_TYPES = ("urn:schemas-upnp-org:device:InternetGatewayDevice:2",
             "urn:schemas-upnp-org:device:InternetGatewayDevice:1")
DESCRIPTION = "Flackey Soulseek"


@dataclass(frozen=True)
class Mapping:
    protocol: str            # "natpmp" | "upnp"
    gateway: str
    internal_port: int
    external_port: int
    lease_s: int
    control_url: str | None = None
    service_type: str | None = None


# ---- where the router is -------------------------------------------------------------------------

def default_gateway(run=subprocess.run) -> str | None:
    """The default route's next hop. `route -n get default` on macOS, `ip route` elsewhere. None when
    there is no default route or the command is missing; a VPN's utun default reports no gateway."""
    if sys.platform == "darwin":
        cmd, pattern = ["route", "-n", "get", "default"], r"gateway:\s*([0-9.]+)"
    else:
        cmd, pattern = ["ip", "route", "show", "default"], r"default via ([0-9.]+)"
    try:
        out = run(cmd, capture_output=True, text=True, timeout=3, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    m = re.search(pattern, out.stdout or "")
    return m.group(1) if m else None


def lan_ip(gateway: str | None = None) -> str | None:
    """This machine's address on the router's network: the source address a UDP socket picks to reach
    the gateway. No packet is sent."""
    target = gateway or "10.255.255.255"
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect((target, 9))
            return s.getsockname()[0]
    except OSError:
        return None


# ---- NAT-PMP -------------------------------------------------------------------------------------

async def _udp_exchange(gateway: str, payload: bytes, timeout: float) -> bytes:
    """One request, one reply, on the calling thread's behalf via to_thread so the loop never blocks."""
    def go() -> bytes:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(timeout)
            s.sendto(payload, (gateway, NATPMP_PORT))
            data, _ = s.recvfrom(64)
            return data
    return await asyncio.to_thread(go)


async def natpmp_map(gateway: str, port: int, lease_s: int, *, transport=_udp_exchange) -> Mapping | None:
    """RFC 6886 §3.3: opcode 2 is a TCP mapping; a reply's opcode is the request's plus 128 and result 0
    means granted. The router may hand back a different public port; the mapping records it."""
    request = struct.pack("!BBHHHI", 0, 2, 0, port, port, lease_s)
    try:
        reply = await transport(gateway, request, 2.0)
    except (OSError, TimeoutError, asyncio.TimeoutError) as e:
        log.info("NAT-PMP: no answer from %s (%s)", gateway, e.__class__.__name__)
        return None
    if len(reply) < 16:
        return None
    _, opcode, result, _, private, public, lifetime = struct.unpack("!BBHIHHI", reply[:16])
    if opcode != 130 or result != 0 or private != port:
        log.info("NAT-PMP: %s refused (opcode %d, result %d)", gateway, opcode, result)
        return None
    return Mapping("natpmp", gateway, port, public, lifetime)


# ---- UPnP ----------------------------------------------------------------------------------------

async def _ssdp_discover(timeout: float) -> list[str]:
    """LOCATION URLs of every Internet Gateway Device that answers an M-SEARCH, deduplicated."""
    def go() -> list[str]:
        found: list[str] = []
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP) as s:
            s.settimeout(timeout)
            for st in IGD_TYPES:
                msg = (f"M-SEARCH * HTTP/1.1\r\nHOST: {SSDP_ADDR[0]}:{SSDP_ADDR[1]}\r\n"
                       f'MAN: "ssdp:discover"\r\nMX: 2\r\nST: {st}\r\n\r\n').encode()
                s.sendto(msg, SSDP_ADDR)
            try:
                while True:
                    data, _ = s.recvfrom(2048)
                    m = re.search(rb"(?im)^LOCATION:\s*(\S+)", data)
                    if m:
                        loc = m.group(1).decode(errors="replace")
                        if loc not in found:
                            found.append(loc)
            except (TimeoutError, OSError):
                pass
        return found
    return await asyncio.to_thread(go)


def _find_wan_service(xml_text: str, base: str) -> tuple[str, str] | None:
    """(service_type, absolute controlURL) of the first WAN*Connection service in a device description."""
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError:
        return None
    ns = {"d": "urn:schemas-upnp-org:device-1-0"}
    for st in WAN_SERVICES:
        for svc in root.iter("{urn:schemas-upnp-org:device-1-0}service"):
            if (svc.findtext("d:serviceType", namespaces=ns) or "").strip() == st:
                url = (svc.findtext("d:controlURL", namespaces=ns) or "").strip()
                if url:
                    return st, urljoin(base, url)
    return None


def _soap(action: str, service_type: str, args: dict[str, str]) -> tuple[dict, str]:
    body = "".join(f"<{k}>{v}</{k}>" for k, v in args.items())
    envelope = ('<?xml version="1.0"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
                's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
                f'<u:{action} xmlns:u="{service_type}">{body}</u:{action}></s:Body></s:Envelope>')
    return {"Content-Type": 'text/xml; charset="utf-8"', "SOAPAction": f'"{service_type}#{action}"'}, envelope


async def upnp_map(port: int, lease_s: int, http: httpx.AsyncClient, *, discover=_ssdp_discover,
                   internal_ip: str | None = None) -> Mapping | None:
    try:
        locations = await discover(3.0)
    except OSError as e:
        log.info("UPnP: discovery failed (%s)", e)
        return None
    if not locations:
        log.info("UPnP: no gateway answered")
        return None
    for loc in locations:
        gateway = urlsplit(loc).hostname or ""
        try:
            desc = await http.get(loc, timeout=5)
            desc.raise_for_status()
        except httpx.HTTPError as e:
            log.info("UPnP: could not read %s (%s)", loc, e.__class__.__name__)
            continue
        found = _find_wan_service(desc.text, loc)
        if found is None:
            continue
        service_type, control = found
        client_ip = internal_ip or lan_ip(gateway)
        if client_ip is None:
            return None
        headers, body = _soap("AddPortMapping", service_type, {
            "NewRemoteHost": "", "NewExternalPort": str(port), "NewProtocol": "TCP",
            "NewInternalPort": str(port), "NewInternalClient": client_ip, "NewEnabled": "1",
            "NewPortMappingDescription": DESCRIPTION, "NewLeaseDuration": str(lease_s)})
        try:
            r = await http.post(control, content=body, headers=headers, timeout=5)
        except httpx.HTTPError as e:
            log.info("UPnP: %s did not answer AddPortMapping (%s)", gateway, e.__class__.__name__)
            continue
        if r.status_code != 200:
            log.info("UPnP: %s refused AddPortMapping (%d)", gateway, r.status_code)
            continue
        return Mapping("upnp", gateway, port, port, lease_s, control_url=control, service_type=service_type)
    return None


# ---- the two together ------------------------------------------------------------------------------

async def map_port(port: int, lease_s: int = 3600, http: httpx.AsyncClient | None = None, *,
                   gateway: str | None = None, natpmp=natpmp_map, upnp=upnp_map) -> Mapping | None:
    """NAT-PMP first (one packet, no discovery), UPnP second. `gateway` defaults to the default route's
    next hop; without one there is nobody to ask NAT-PMP, but SSDP is multicast and is still tried."""
    if gateway is None:
        gateway = default_gateway()
    if gateway is not None:
        m = await natpmp(gateway, port, lease_s)
        if m is not None:
            log.info("port %d opened on %s via NAT-PMP for %ds", port, gateway, m.lease_s)
            return m
    own = http is None
    http = http or httpx.AsyncClient()
    try:
        m = await upnp(port, lease_s, http)
    finally:
        if own:
            await http.aclose()
    if m is not None:
        log.info("port %d opened on %s via UPnP for %ds", port, m.gateway, lease_s)
    return m


async def unmap_port(mapping: Mapping, http: httpx.AsyncClient | None = None, *, transport=_udp_exchange) -> None:
    """Release on quit. Failure is silent by design: the lease expires on its own."""
    try:
        if mapping.protocol == "natpmp":
            await transport(mapping.gateway, struct.pack("!BBHHHI", 0, 2, 0, mapping.internal_port, 0, 0), 2.0)
        elif mapping.protocol == "upnp" and mapping.control_url and mapping.service_type:
            headers, body = _soap("DeletePortMapping", mapping.service_type, {
                "NewRemoteHost": "", "NewExternalPort": str(mapping.external_port), "NewProtocol": "TCP"})
            own = http is None
            http = http or httpx.AsyncClient()
            try:
                await http.post(mapping.control_url, content=body, headers=headers, timeout=5)
            finally:
                if own:
                    await http.aclose()
    except (OSError, TimeoutError, asyncio.TimeoutError, httpx.HTTPError) as e:
        log.info("could not release the port mapping (%s)", e.__class__.__name__)
```

Add to `pyproject.toml` layers list, as the last element: `"portmap : portcheck",` (Task 7 adds `portcheck`; import-linter is fine with a named module that does not exist yet? It is **not** — so in this task add only `"portmap",` and Task 7 changes that line to `"portmap : portcheck",`).

- [ ] **Step 4: Run**

Run: `uv run pytest tests/test_portmap.py -q && uv run ruff check src tests && uv run lint-imports`
Expected: pass.

- [ ] **Step 5: Commit**

```bash
git add src/flackey/portmap.py tests/test_portmap.py pyproject.toml
git commit -m "feat: ask the router to open the Soulseek port, by NAT-PMP and then UPnP"
```

---

### Task 7: `portcheck` — the Soulseek project's own port test

**Files:**
- Create: `src/flackey/portcheck.py`
- Modify: `pyproject.toml` (layers: `"portmap",` → `"portmap : portcheck",`)
- Test: `tests/test_portcheck.py`

**Interfaces (produces):**

```python
PORT_TEST_URL = "http://tools.slsknet.org/porttest.php"

@dataclass(frozen=True)
class PortCheck:
    reachable: bool | None      # None: the test could not be run
    public_ip: str | None
    error: str | None = None

async def check_port(port: int, http: httpx.AsyncClient) -> PortCheck
```

The page (fetched 2026-09-13) contains, in its text, `IP: 82.166.148.116 Port: 50300/tcp CLOSED.` or `… OPEN.`; the check parses exactly that with `re.search(r"IP:\s*([0-9.]+).*?Port:\s*(\d+)/tcp\s+(OPEN|CLOSED)", text, re.S | re.I)` after stripping tags. A different page shape → `reachable=None, error="The port test gave an answer Flackey could not read."`; a network error → `reachable=None, error="Could not reach the port test service."`. The test hits the caller's own public address, so it works through a VPN too (and then tests the VPN's exit, which is the point).

- [ ] **Step 1: Failing tests** (`tests/test_portcheck.py`)

```python
import httpx
import respx

from flackey.portcheck import PORT_TEST_URL, PortCheck, check_port

PAGE = ("<html><body><div>IP: <b>82.166.148.116</b></div><div>Port: 50300/tcp <b>{verdict}</b>. "
        "Your router and/or Soulseek client needs to be configured correctly.</div></body></html>")


@respx.mock
async def test_closed_port_is_reported_with_the_public_address():
    route = respx.get(PORT_TEST_URL).mock(return_value=httpx.Response(200, text=PAGE.format(verdict="CLOSED")))
    async with httpx.AsyncClient() as http:
        assert await check_port(50300, http) == PortCheck(False, "82.166.148.116")
    assert route.calls[0].request.url.params["port"] == "50300"


@respx.mock
async def test_open_port():
    respx.get(PORT_TEST_URL).mock(return_value=httpx.Response(200, text=PAGE.format(verdict="OPEN")))
    async with httpx.AsyncClient() as http:
        assert await check_port(50300, http) == PortCheck(True, "82.166.148.116")


@respx.mock
async def test_unreadable_page_and_network_errors_are_unknown_not_closed():
    respx.get(PORT_TEST_URL).mock(return_value=httpx.Response(200, text="<html>maintenance</html>"))
    async with httpx.AsyncClient() as http:
        r = await check_port(50300, http)
    assert r.reachable is None and r.public_ip is None and "could not read" in r.error
    respx.get(PORT_TEST_URL).mock(side_effect=httpx.ConnectError("down"))
    async with httpx.AsyncClient() as http:
        r = await check_port(50300, http)
    assert r.reachable is None and "Could not reach" in r.error
```

- [ ] **Step 2: Verify failure**

Run: `uv run pytest tests/test_portcheck.py -q` → `ModuleNotFoundError`.

- [ ] **Step 3: Implement `src/flackey/portcheck.py`**

```python
"""Is the Soulseek listen port reachable from the internet?

Asked of the Soulseek project's own port test, the one the official client and Nicotine+ use: it
connects back to the caller's public address on the given port and says OPEN or CLOSED. Asking from
inside would not work -- most home routers do not hairpin -- and it has to be the address the Soulseek
server sees, which through a VPN is the VPN's exit. This is a plain HTML page, so the parse is pinned to
the two phrases it has carried for years and anything else is reported as "could not tell", never as
closed.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import httpx

log = logging.getLogger(__name__)
PORT_TEST_URL = "http://tools.slsknet.org/porttest.php"
_VERDICT = re.compile(r"IP:\s*([0-9.]+).*?Port:\s*(\d+)/tcp\s+(OPEN|CLOSED)", re.S | re.I)


@dataclass(frozen=True)
class PortCheck:
    reachable: bool | None
    public_ip: str | None
    error: str | None = None


async def check_port(port: int, http: httpx.AsyncClient) -> PortCheck:
    try:
        r = await http.get(PORT_TEST_URL, params={"port": port}, timeout=15,
                           headers={"User-Agent": "Flackey (port check)"})
        r.raise_for_status()
    except httpx.HTTPError as e:
        log.info("port check unavailable: %s", e.__class__.__name__)
        return PortCheck(None, None, "Could not reach the port test service.")
    text = re.sub(r"<[^>]+>", " ", r.text)
    m = _VERDICT.search(text)
    if m is None or int(m.group(2)) != port:
        log.warning("port check page had no verdict for port %d", port)
        return PortCheck(None, None, "The port test gave an answer Flackey could not read.")
    return PortCheck(m.group(3).upper() == "OPEN", m.group(1))
```

- [ ] **Step 4: Run**

Run: `uv run pytest tests/test_portcheck.py -q && uv run ruff check src tests && uv run lint-imports`

- [ ] **Step 5: Commit**

```bash
git add src/flackey/portcheck.py tests/test_portcheck.py pyproject.toml
git commit -m "feat: ask the Soulseek project's port test whether the listen port is reachable"
```

---

### Task 8: `Sharing` service, health, routes

**Files:**
- Create: `src/flackey/sharing.py`
- Create: `src/flackey/web/sharing.py`
- Modify: `src/flackey/web/__init__.py` (health `sharing`, mount router, `sharing=None` kwarg)
- Modify: `src/flackey/app.py` (construct, run, release, refresh after Soulseek connects)
- Modify: `src/flackey/soulseek_link.py` (`on_connected` callback)
- Modify: `pyproject.toml` (layers: `"soulseek_link"` → `"soulseek_link : sharing"`)
- Test: `tests/test_sharing.py`, `tests/test_web.py`

**Interfaces (produces):**

```python
class Sharing:
    def __init__(self, settings, status: dict, http, *, port: int,
                 mapper=portmap.map_port, unmapper=portmap.unmap_port, checker=portcheck.check_port,
                 gateway=portmap.default_gateway, lan=portmap.lan_ip,
                 clock=time.monotonic, sleep=asyncio.sleep, lease_s=3600, recheck_s=1800)
    state: dict   # mirrored to status["sharing"] on every change
    async def refresh(self) -> dict      # map (if not mapped) then check; returns the new state
    def start_refresh(self) -> dict      # background task, returns state (with checking=True)
    async def run_forever(self) -> None  # first refresh when settings.soulseek_enabled; renew and recheck on schedule
    async def release(self) -> None      # unmap on quit
```

State shape (also the JSON of `GET /api/sharing` and `health["sharing"]`):

```json
{"port": 50300, "enabled": true, "checking": false, "mapping": null | "natpmp" | "upnp",
 "reachable": null | true | false, "public_ip": null | "1.2.3.4", "lan_ip": null | "10.0.0.5",
 "gateway": null | "10.0.0.1", "checked_at": null | "2026-09-13T16:00:00+00:00", "error": null | "…"}
```

Routes: `GET /api/sharing` → state; `POST /api/sharing/check` → `start_refresh()` result (200), or 409 when Soulseek is not enabled. When `sharing is None` (tests that do not pass one) `GET` returns `{"port": null, "enabled": false, "checking": false, "mapping": null, "reachable": null, "public_ip": null, "lan_ip": null, "gateway": null, "checked_at": null, "error": null}` and `POST` 409.

- [ ] **Step 1: Failing tests** (`tests/test_sharing.py`)

```python
import asyncio

from flackey.portcheck import PortCheck
from flackey.portmap import Mapping
from flackey.sharing import Sharing


class S:
    soulseek_enabled = True


def make(**kw):
    status = {}
    calls = {"map": 0, "unmap": 0, "check": 0}

    async def mapper(port, lease_s, http, **k):
        calls["map"] += 1
        return kw.get("mapping", Mapping("natpmp", "10.0.0.1", port, port, lease_s))

    async def unmapper(mapping, http):
        calls["unmap"] += 1

    async def checker(port, http):
        calls["check"] += 1
        return kw.get("check", PortCheck(True, "1.2.3.4"))

    sharing = Sharing(S(), status, http=None, port=50300, mapper=mapper, unmapper=unmapper, checker=checker,
                      gateway=lambda: "10.0.0.1", lan=lambda g=None: "10.0.0.5", lease_s=100, recheck_s=1000,
                      clock=kw.get("clock", lambda: 0.0), sleep=kw.get("sleep", asyncio.sleep))
    return sharing, status, calls


async def test_refresh_maps_then_checks_and_mirrors_state_into_status():
    sharing, status, calls = make()
    state = await sharing.refresh()
    assert calls == {"map": 1, "unmap": 0, "check": 1}
    assert state["mapping"] == "natpmp" and state["reachable"] is True and state["public_ip"] == "1.2.3.4"
    assert state["lan_ip"] == "10.0.0.5" and state["gateway"] == "10.0.0.1" and state["port"] == 50300
    assert state["checking"] is False and state["checked_at"] is not None and state["error"] is None
    assert status["sharing"] == state


async def test_refresh_reports_a_closed_port_after_a_failed_mapping():
    sharing, status, calls = make(mapping=None, check=PortCheck(False, "1.2.3.4"))
    state = await sharing.refresh()
    assert state["mapping"] is None and state["reachable"] is False


async def test_refresh_keeps_unknown_when_the_check_could_not_run():
    sharing, _, _ = make(check=PortCheck(None, None, "Could not reach the port test service."))
    state = await sharing.refresh()
    assert state["reachable"] is None and state["error"] == "Could not reach the port test service."


async def test_start_refresh_marks_checking_and_finishes_in_the_background():
    sharing, status, calls = make()
    first = sharing.start_refresh()
    assert first["checking"] is True
    await asyncio.sleep(0.05)
    assert status["sharing"]["checking"] is False and calls["check"] == 1
    sharing.start_refresh(); sharing.start_refresh()          # a refresh already running is not doubled
    await asyncio.sleep(0.05)
    assert calls["check"] == 2


async def test_run_forever_renews_the_lease_and_rechecks_on_schedule():
    now = [0.0]
    slept = []

    async def sleep(s):
        slept.append(s)
        now[0] += s
        if len(slept) >= 3:
            raise asyncio.CancelledError

    sharing, status, calls = make(clock=lambda: now[0], sleep=sleep)
    try:
        await sharing.run_forever()
    except asyncio.CancelledError:
        pass
    assert calls["map"] >= 2                     # renewed at least once within three ticks of lease_s/2
    assert all(s <= 50 for s in slept)           # never sleeps past half the lease


async def test_release_unmaps():
    sharing, _, calls = make()
    await sharing.refresh()
    await sharing.release()
    assert calls["unmap"] == 1


async def test_disabled_soulseek_does_nothing():
    sharing, status, calls = make()
    sharing._settings.soulseek_enabled = False
    slept = []

    async def sleep(s):
        slept.append(s)
        raise asyncio.CancelledError

    sharing._sleep = sleep
    try:
        await sharing.run_forever()
    except asyncio.CancelledError:
        pass
    assert calls["map"] == 0 and status["sharing"]["enabled"] is False
```

`tests/test_web.py`:

```python
def test_sharing_routes_without_a_service(client):
    c, _, _ = client
    assert c.get("/api/sharing").json()["enabled"] is False
    assert c.post("/api/sharing/check").status_code == 409


def test_sharing_routes_with_a_service(tmp_path: Path):
    class FakeSharing:
        state = {"port": 50300, "enabled": True, "checking": False, "mapping": None, "reachable": False,
                 "public_ip": "1.2.3.4", "lan_ip": "10.0.0.5", "gateway": "10.0.0.1", "checked_at": None, "error": None}
        started = 0

        def start_refresh(self):
            self.started += 1
            return {**self.state, "checking": True}

    sharing = FakeSharing()
    app, _, _ = make(tmp_path, sharing=sharing)
    c = TestClient(app)
    assert c.get("/api/sharing").json()["reachable"] is False
    assert c.post("/api/sharing/check").json()["checking"] is True and sharing.started == 1
```

Also extend `test_health`'s expected dict with `"sharing": None` (health reports `status.get("sharing")`).

- [ ] **Step 2: Verify failure** — `uv run pytest tests/test_sharing.py tests/test_web.py -q -k sharing`.

- [ ] **Step 3: Implement**

`src/flackey/sharing.py`:

```python
"""Keep the Soulseek listen port open and know whether it is.

Sharing is the half of Soulseek that keeps an account in good standing, and it needs one inbound TCP
port. This service asks the router to open it (portmap), asks the Soulseek project's port test whether
it is open (portcheck), keeps the lease alive while the app runs, and publishes what it found on the
shared status dict so health, the event stream and the pages all read the same thing. What it cannot
do is open a port on a router that will not be asked, or on a VPN: then `reachable` is False and the UI
shows the user how to do it by hand.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime

from . import portcheck, portmap

log = logging.getLogger(__name__)
LEASE_S = 3600
RECHECK_S = 1800


def empty_state(port: int | None = None, enabled: bool = False) -> dict:
    return {"port": port, "enabled": enabled, "checking": False, "mapping": None, "reachable": None,
            "public_ip": None, "lan_ip": None, "gateway": None, "checked_at": None, "error": None}


class Sharing:
    def __init__(self, settings, status: dict, http, *, port: int, mapper=portmap.map_port,
                 unmapper=portmap.unmap_port, checker=portcheck.check_port, gateway=portmap.default_gateway,
                 lan=portmap.lan_ip, clock=time.monotonic, sleep=asyncio.sleep,
                 lease_s: int = LEASE_S, recheck_s: int = RECHECK_S):
        self._settings, self._status, self._http, self._port = settings, status, http, port
        self._map, self._unmap, self._check = mapper, unmapper, checker
        self._gateway, self._lan, self._clock, self._sleep = gateway, lan, clock, sleep
        self._lease_s, self._recheck_s = lease_s, recheck_s
        self._mapping: portmap.Mapping | None = None
        self._mapped_at: float | None = None
        self._task: asyncio.Task | None = None
        self.state = empty_state(port, bool(settings.soulseek_enabled))
        self._publish()

    def _publish(self) -> None:
        self._status["sharing"] = dict(self.state)

    def _set(self, **changes) -> None:
        self.state.update(changes)
        self._publish()

    async def refresh(self) -> dict:
        """Map if there is no live mapping, then verify from outside. Serialized by `_lock` so a manual
        "Check again" during the scheduled renewal does not race it."""
        gateway = self._gateway()
        self._set(enabled=bool(self._settings.soulseek_enabled), checking=True, error=None,
                  gateway=gateway, lan_ip=self._lan(gateway))
        try:
            if self._mapping is None:
                self._mapping = await self._map(self._port, self._lease_s, self._http, gateway=gateway)
                self._mapped_at = self._clock() if self._mapping else None
            result = await self._check(self._port, self._http)
        except Exception:   # never let a network hiccup take the app down
            log.exception("sharing check failed")
            self._set(checking=False, error="Something went wrong checking the port.")
            return dict(self.state)
        self._set(checking=False, mapping=self._mapping.protocol if self._mapping else None,
                  reachable=result.reachable, public_ip=result.public_ip, error=result.error,
                  checked_at=datetime.now(UTC).isoformat(timespec="seconds"))
        log.info("sharing: port %d %s%s", self._port,
                 {True: "reachable", False: "closed", None: "unknown"}[result.reachable],
                 f" (opened via {self._mapping.protocol})" if self._mapping else "")
        return dict(self.state)

    def start_refresh(self) -> dict:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self.refresh())
            self._set(checking=True)
        return dict(self.state)

    async def run_forever(self) -> None:
        """Renew the mapping before half its lease is up, and recheck reachability every `recheck_s`. A copy
        without Soulseek only waits, and picks up when the wizard turns it on."""
        last_check: float | None = None
        while True:
            if not self._settings.soulseek_enabled:
                if self.state["enabled"]:
                    self._set(enabled=False)
                await self._sleep(min(self._lease_s / 2, self._recheck_s))
                continue
            now = self._clock()
            if self._mapping is not None and self._mapped_at is not None and now - self._mapped_at >= self._lease_s / 2:
                self._mapping, self._mapped_at = None, None   # renew: a fresh request replaces the lease
            if self._mapping is None or last_check is None or now - last_check >= self._recheck_s:
                await self.refresh()
                last_check = self._clock()
            await self._sleep(min(self._lease_s / 2, self._recheck_s))

    async def release(self) -> None:
        if self._mapping is not None:
            await self._unmap(self._mapping, self._http)
            self._mapping = None
```

`src/flackey/web/sharing.py`:

```python
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..sharing import empty_state


def router(sharing=None) -> APIRouter:
    r = APIRouter(prefix="/api/sharing")

    @r.get("")
    async def get_sharing() -> dict:
        return dict(sharing.state) if sharing is not None else empty_state()

    @r.post("/check")
    async def check() -> dict:
        if sharing is None or not sharing.state.get("enabled"):
            raise HTTPException(409, "Soulseek isn't set up yet, so there is no port to check.")
        return sharing.start_refresh()

    return r
```

`src/flackey/web/__init__.py`: `create_app(..., link=None, sharing=None)`; import `sharing as sharing_routes` in the local import line (rename to avoid clashing with the kwarg: `from . import library, lossless, pick, requests, stream, telegram` and `from . import sharing as sharing_web`); health adds `"sharing": status.get("sharing")`; mount `app.include_router(sharing_web.router(sharing))`.

`src/flackey/soulseek_link.py`: constructor gains `on_connected: Callable[[], None] = lambda: None`, stored, and `_wait_for_login` calls `self._on_connected()` right after `log.info("soulseek signed in as %s", ...)`.

`src/flackey/app.py` in `_run`, after `link = SoulseekLink(...)`:

```python
    sharing = Sharing(settings, status, http, port=read_listen_port(settings.data_dir))
    link = SoulseekLink(settings, worker, http, build_providers=build_providers,
                        on_connected=sharing.start_refresh)
```

(import `from .sharing import Sharing` and `from .slskd_config import read_listen_port`; `read_listen_port` already exists.) Pass `sharing=sharing` to `create_app`, add `sharing.run_forever()` to `run_until_server_stops(...)`, and in the `finally:` block, before stopping the sidecar, `await sharing.release()`.

`pyproject.toml`: `"soulseek_link : sharing",`. `sharing` imports `portmap`/`portcheck` (bottom layer) only.

- [ ] **Step 4: Run** — `uv run pytest -q && uv run ruff check src tests && uv run lint-imports`.

- [ ] **Step 5: Commit**

```bash
git add src/flackey/sharing.py src/flackey/web/sharing.py src/flackey/web/__init__.py src/flackey/app.py src/flackey/soulseek_link.py pyproject.toml tests/test_sharing.py tests/test_web.py
git commit -m "feat: open and verify the Soulseek port while the app runs, and report it on health"
```

---

### Task 9: `SharingPanel` in Settings, the Soulseek step and the Uploads tab

**Files:**
- Modify: `web/src/api.ts` (`SharingState`, `Health.sharing`, `api.sharing`, `api.checkSharing`)
- Create: `web/src/components/SharingPanel.tsx`, `web/src/components/SharingPanel.test.tsx`
- Modify: `web/src/components/SettingsPage.tsx`, `web/src/components/setup/SoulseekStep.tsx`, `web/src/components/uploads/UploadsPage.tsx`
- Modify: `web/src/app.css` (a few rules for `.sharing`)
- Test: `SharingPanel.test.tsx`, `UploadsPage.test.tsx`, `SoulseekStep.test.tsx`

**Interfaces:**

```ts
export interface SharingState { port: number | null; enabled: boolean; checking: boolean; mapping: 'natpmp' | 'upnp' | null
  reachable: boolean | null; public_ip: string | null; lan_ip: string | null; gateway: string | null; checked_at: string | null; error: string | null }
// Health: sharing?: SharingState | null
api.sharing: () => call<SharingState>('/api/sharing')
api.checkSharing: () => post<SharingState>('/api/sharing/check')
// <SharingPanel state={SharingState | null} onCheck={() => void} compact?: boolean />
```

Copy (verbatim, tests pin it):
- reachable true: **"Other Soulseek users can download from you."**
- reachable false: **"Your Soulseek port is closed, so most people cannot download from you."** Then, full mode only, the instructions block: "To open it, forward TCP port {port} on your router to this Mac ({lan_ip}). Your router's address is {gateway}. If you use a VPN, its exit address ({public_ip}) is what other users see, so the forward has to be set up in the VPN app or provider instead." Each `{…}` shown only when known.
- checking: **"Checking whether other people can reach you…"**
- reachable null with error: the error text. reachable null without error and not checking: **"Not checked yet."**
- The button reads "Check again" (or "Checking…" while checking, disabled). Compact mode has no instructions and no gateway line, only the status sentence and, when closed, "You can open it later from Settings › Sharing."

- [ ] **Step 1: Failing tests**

`SharingPanel.test.tsx`:

```tsx
import { render, screen, fireEvent } from '@testing-library/react'
import SharingPanel from './SharingPanel'
import type { SharingState } from '../api'

const state = (over: Partial<SharingState> = {}): SharingState => ({ port: 50300, enabled: true, checking: false, mapping: null,
  reachable: null, public_ip: null, lan_ip: null, gateway: null, checked_at: null, error: null, ...over })

it('says people can download when the port is reachable', () => {
  render(<SharingPanel state={state({ reachable: true, mapping: 'natpmp' })} onCheck={vi.fn()} />)
  expect(screen.getByText('Other Soulseek users can download from you.')).toBeInTheDocument()
})

it('explains the forward when the port is closed, with the addresses it knows', () => {
  render(<SharingPanel state={state({ reachable: false, lan_ip: '10.0.0.5', gateway: '10.0.0.1', public_ip: '1.2.3.4' })} onCheck={vi.fn()} />)
  expect(screen.getByText(/Your Soulseek port is closed/)).toBeInTheDocument()
  expect(screen.getByText(/forward TCP port 50300 on your router to this Mac \(10\.0\.0\.5\)/)).toBeInTheDocument()
  expect(screen.getByText(/Your router's address is 10\.0\.0\.1/)).toBeInTheDocument()
  expect(screen.getByText(/exit address \(1\.2\.3\.4\)/)).toBeInTheDocument()
})

it('compact mode keeps the sentence and points at Settings', () => {
  render(<SharingPanel state={state({ reachable: false })} onCheck={vi.fn()} compact />)
  expect(screen.getByText(/Your Soulseek port is closed/)).toBeInTheDocument()
  expect(screen.getByText(/Settings › Sharing/)).toBeInTheDocument()
  expect(screen.queryByText(/forward TCP port/)).not.toBeInTheDocument()
})

it('Check again calls onCheck and is disabled while checking', () => {
  const onCheck = vi.fn()
  const { rerender } = render(<SharingPanel state={state()} onCheck={onCheck} />)
  fireEvent.click(screen.getByText('Check again'))
  expect(onCheck).toHaveBeenCalled()
  rerender(<SharingPanel state={state({ checking: true })} onCheck={onCheck} />)
  expect(screen.getByText('Checking…')).toBeDisabled()
  expect(screen.getByText('Checking whether other people can reach you…')).toBeInTheDocument()
})
```

`UploadsPage.test.tsx`, add (the file's `serve` helper stubs `fetch` for one URL; extend it to answer `/api/sharing` too, or stub `api.sharing` with `vi.spyOn`):

```tsx
it('warns when the port is closed', async () => {
  serve(feed())
  vi.spyOn(api, 'sharing').mockResolvedValue({ port: 50300, enabled: true, checking: false, mapping: null, reachable: false,
    public_ip: null, lan_ip: null, gateway: null, checked_at: null, error: null })
  render(<UploadsPage inset={false} />)
  await waitFor(() => expect(screen.getByText(/Your Soulseek port is closed/)).toBeInTheDocument())
})
```

`SoulseekStep.test.tsx`, add next to the existing "signed in" test (reuse its mocks for `saveSoulseek`/`soulseekConnectStatus`):

```tsx
it('shows the sharing line once signed in', async () => {
  // …same arrangement as the test that reaches "Signed in as", plus:
  vi.spyOn(api, 'sharing').mockResolvedValue({ port: 50300, enabled: true, checking: false, mapping: 'upnp', reachable: true,
    public_ip: '1.2.3.4', lan_ip: null, gateway: null, checked_at: null, error: null })
  // …drive to the connected state, then:
  await waitFor(() => expect(screen.getByText('Other Soulseek users can download from you.')).toBeInTheDocument())
})
```

- [ ] **Step 2: Verify failure** — `npm --prefix web test -- --run`.

- [ ] **Step 3: Implement**

`api.ts`: add the interface, `sharing?: SharingState | null` on `Health`, and the two calls.

`SharingPanel.tsx`:

```tsx
import type { SharingState } from '../api'

export default function SharingPanel({ state, onCheck, compact = false }: { state: SharingState | null; onCheck: () => void; compact?: boolean }) {
  const s = state
  const checking = !!s?.checking
  const sentence = checking ? 'Checking whether other people can reach you…'
    : s?.reachable === true ? 'Other Soulseek users can download from you.'
    : s?.reachable === false ? 'Your Soulseek port is closed, so most people cannot download from you.'
    : s?.error ?? 'Not checked yet.'
  const tone = checking ? '' : s?.reachable === true ? ' ok' : s?.reachable === false ? ' warn' : ''
  return (<div className={`sharing${compact ? ' compact' : ''}`}>
    <div className={`hint-row${tone}`}>{sentence}</div>
    {s?.reachable === false && (compact
      ? <div className="hint-row">You can open it later from Settings › Sharing.</div>
      : <div className="sharing-how">To open it, forward TCP port {s.port} on your router to this Mac{s.lan_ip ? ` (${s.lan_ip})` : ''}.
          {s.gateway ? ` Your router's address is ${s.gateway}.` : ''}
          {s.public_ip ? ` If you use a VPN, its exit address (${s.public_ip}) is what other users see, so the forward has to be set up in the VPN app or provider instead.` : ' If you use a VPN, the forward has to be set up in the VPN app or provider instead.'}</div>)}
    {!compact && <div className="row-gap"><button className="btn-secondary" onClick={onCheck} disabled={checking}>{checking ? 'Checking…' : 'Check again'}</button></div>}
  </div>)
}
```

`SettingsPage.tsx`: in the Soulseek `group`, after the Password row, when `s.soulseek_enabled`:

```tsx
        {s.soulseek_enabled && <div className="srow"><div className="srow-body"><div className="k">Sharing</div>
          <SharingPanel state={live.health?.sharing ?? null} onCheck={() => { api.checkSharing().catch(() => undefined) }} /></div></div>}
```

Also the Telegram override: after the Telegram row add a `details className="tech"` with summary "Use your own Telegram API keys", two inputs (ids `settings-tg-api-id`, `settings-tg-api-hash`), a Save button calling `api.telegramKeys` and showing the error in `.err`; on success show the hint "Saved. Sign in again from the Telegram row." (SSE carries the new `telegram_configured` through `live.health`; nothing else to refresh).

`SoulseekStep.tsx`: state `const [sharing, setSharing] = useState<SharingState | null>(null)`; when `connected` becomes true start polling `api.sharing()` every `POLL_MS` until `!s.checking` (stop on unmount, same pattern as `pollConnect`). Render, right after the "Signed in as …" hint-row: `{connected && <SharingPanel state={sharing} onCheck={() => {}} compact />}`.

`UploadsPage.tsx`: state `sharing`; in `load()` also `setSharing(await api.sharing().catch(() => null))`; render under the feed banners: `{sharing?.reachable === false && <Banner tone="amber" text="Your Soulseek port is closed, so most people cannot download from you. Settings › Sharing shows how to open it." />}`.

`app.css`: `.sharing .hint-row.ok { color: var(--ok) }` (use the existing token names the file already has for `.hint-row.ok`/`.warn` — check they exist; `SoulseekStep` uses `hint-row ok` and `hint-row warn` already, so likely nothing new is needed) and `.sharing-how { font-size: 13px; color: var(--text-2); max-width: 60ch; margin-top: 6px }` using whatever secondary text token `app.css` defines (grep `--text` first and use the existing one).

- [ ] **Step 4: Run** — `npm --prefix web test -- --run && npm --prefix web run build`.

- [ ] **Step 5: Commit**

```bash
git add web/src
git commit -m "feat: show whether people can reach you on Soulseek, and how to open the port when they cannot"
```

---

### Task 10: README and README-for-friends

**Files:**
- Modify: `README.md` (Setup / first launch paragraph, "Notes on Telegram behaviour", "Lossless via Soulseek" step 2 and the router sentence)
- Modify: `packaging/README-for-friends.md` ("First run" Telegram paragraph, new "Sharing" section, "Known rough edges")

- [ ] **Step 1: Edit `README.md`**

In "Usage", after the first-launch paragraph, replace the sentence about Telegram sign-in so it reads:

> The first launch walks you through a short setup: pick the library folder, then sign in to Telegram (scan a QR code with the Telegram app, or use a phone number instead), then a Soulseek account. Either source can be skipped and turned on later from Settings; with both off the app files nothing. A packaged build carries Flackey's own Telegram API keys; from a checkout, put yours in `.env` (`TELEGRAM_API_ID`, `TELEGRAM_API_HASH`) or paste them into the Telegram step, which offers fields whenever this copy has none.

In "Lossless via Soulseek", the `slskd.yml` example: keep it, and add after step 3:

> The setup screen writes all of this for you, including `shares.directories`, which is the DJ Library: sharing is what keeps a Soulseek account in good standing. Flackey also asks your router to open TCP 50300 (NAT-PMP, then UPnP) every time it starts, and checks from outside whether the port answers; Settings › Sharing shows the result and, when the port is closed, what to forward by hand. Behind a VPN the forward has to be made on the VPN's side.

- [ ] **Step 2: Edit `packaging/README-for-friends.md`**

Replace the **Telegram** paragraph under "First run" with:

> **Telegram.** One of the two ways Flackey finds audio is a Telegram bot. This build already carries the keys Telegram needs to know which app is talking; you sign in with your own Telegram account by scanning a QR code, and that is all. You can skip it and use Soulseek only.

Add a section after "Where things go":

> ## Sharing on Soulseek
>
> Soulseek is give and take: your DJ Library is shared read-only, and many users refuse to send to anyone who shares nothing. For people to reach you, one port has to be open on your router. Flackey asks the router to open it every time it starts and then checks from outside whether that worked. Settings › Sharing shows the answer. If it says the port is closed, forward TCP 50300 to this Mac on your router (the panel shows the addresses), or, if you are on a VPN, in the VPN's settings. Downloading works either way; sharing back is what needs the port.

In "Known rough edges" drop the line about installing ffmpeg and chromaprint only if Task 11 confirms they are bundled (phase 3 bundles them; leave the line until then).

- [ ] **Step 3: Commit**

```bash
git add README.md packaging/README-for-friends.md
git commit -m "docs: say how Telegram keys, sharing and the port work for someone who is not the owner"
```

---

### Task 11: Fresh-machine run of the built app (owner's session, not a subagent)

**Files:** none changed unless the run finds a bug.

- [ ] **Step 1: Build** — `packaging/build_app.sh` (no `FLACKEY_TELEGRAM_*` set, so the Telegram step must show the key fields). Expected: `Built packaging/build/dist/Flackey.app`.
- [ ] **Step 2: Launch against a scratch data folder** —

```bash
SCRATCH=$(mktemp -d)/flackey-fresh; mkdir -p "$SCRATCH"
open --env DATA_DIR="$SCRATCH/data" --env LIBRARY_ROOT="$SCRATCH/lib" --env WEB_PORT=8799 packaging/build/dist/Flackey.app
```

- [ ] **Step 3: Drive the wizard through the served UI** at `http://127.0.0.1:8799` with the Playwright MCP: Welcome → Get started; Folder → Continue; Telegram shows "This copy needs Telegram API keys" → Skip for now; Soulseek → Skip for now; Ready says "nowhere to fetch from yet" → Start digging; main screen shows the sidebar line "Telegram off" and no amber banner. Then `tail -20 "$SCRATCH/data/flackey.log"`: no traceback, and a line `sharing:` is absent (Soulseek off).
- [ ] **Step 4: Quit the app** (`osascript -e 'quit app "Flackey"'`), remove `$SCRATCH`, and record the outcome in the roadmap page's notes via `write_db`.

---

## Self-review

- **Coverage.** D1 (shipped keys + override + skip): Tasks 1, 2, 4; Settings override in Task 9. Worker without Telegram: Task 3. Shares: Task 5. D7 (map, verify, guide, Uploads banner, Settings panel, Soulseek step line): Tasks 6–9. README/friends: Task 10. Fresh run: Task 11.
- **Types.** `Mapping` fields and `PortCheck` fields are used identically in Tasks 6, 7, 8. `SharingState` keys in Task 9 equal `empty_state()` in Task 8. `supervise_worker(run_when=)` name is the same in Task 3's test and implementation. `TelegramStep` props `onDone`/`onSkip` match `App.tsx`. `ReadyStep` props `telegram`/`soulseek` match `App.tsx`.
- **Layers.** New modules and their rows: `sharing` beside `soulseek_link`; `portmap : portcheck` last. `web/sharing.py` lives in the `web` package, already a layer.
- **Placeholders.** None: every step carries its code. Task 3's worker test names `make_worker` with a note to use the file's real builder; the implementer must look, and the test body is complete either way.
