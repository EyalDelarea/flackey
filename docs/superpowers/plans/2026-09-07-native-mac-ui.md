# Native Mac UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Flackey look and open like a native Mac app: a pywebview window started by `crate start`, and the React UI restyled to the system font, system light/dark tokens, a vibrancy sidebar, grouped lists, and a new animated Welcome screen.

**Architecture:** The FastAPI/uvicorn server keeps running inside `app.run()`, now on a daemon thread; the main thread owns the pywebview (WKWebView) window and stops the server when the window closes. The page learns it is inside the native window from a `?titlebar=inset` query flag and asks the window to resize through `window.pywebview.api`. The frontend keeps every behaviour and API call; only markup classes, CSS and one presentation field change, plus a new Welcome step in the setup flow.

**Tech Stack:** Python 3.12, FastAPI, uvicorn, pywebview 6.2 (PyObjC), pytest; React 19, TypeScript, Vite, Vitest + Testing Library. Package managers: `uv` (Python), `npm --prefix web` (frontend).

**Spec:** `docs/superpowers/specs/2026-09-07-native-mac-ui-design.md` (read it first; section 3 holds the tokens, section 5 the screens). Mockups: https://claude.ai/code/artifact/bb1324aa-2ed2-4d64-8f52-210d314688b4

## Global Constraints

- Python `>=3.12,<3.13`; `pywebview>=6.2` is already in `pyproject.toml` (added, `uv.lock` updated). `crate start --no-browser` must work when `webview` cannot be imported (lazy import).
- Font: system stack only: `-apple-system, BlinkMacSystemFont, 'SF Pro Text', 'Helvetica Neue', sans-serif`. Mono only for file paths: `'SF Mono', Menlo, Monaco, monospace`. No Google Fonts. No uppercase or letter-spaced labels anywhere.
- Colours only through the CSS variables in `web/src/theme.css` (Task 3). Orange = needs you, green = filed/verified, red = rejected, blue (`--accent`) only for selection, focus and the primary button.
- Borders are `0.5px`. Groups/cards 10px radius, candidate cards and wells 8px, fields 7px, primary button 6px (24px tall), secondary button 5px (22px tall), sidebar items 6px (28px tall).
- No fake title bar or traffic lights in the real page. The only drag strip is the 52px one reserved when the `?titlebar=inset` flag is present.
- Motion: transitions on state changes only (120–200ms). The Welcome rig is the single non-user-triggered animation; every animation is off under `prefers-reduced-motion: reduce`.
- Tests: all existing Vitest and pytest suites keep passing; behaviour tests are updated, never deleted. Run Python tests with `uv run pytest -q --ignore=tests/live` (baseline: 247 passed) and frontend tests with `npm --prefix web test` (baseline: 62 passed). Import layers are checked with `uv run lint-imports` (exhaustive contract: every new module must be added to `[[tool.importlinter.contracts]]` layers in `pyproject.toml`).
- Git: this worktree's shell hook refuses compound commands and anything it cannot prove is not git. Write scripts with the Write tool and run them plainly; run git as `/usr/bin/git -C /Users/delarea/Desktop/code/flackey/.claude/worktrees/native-mac-ui <args>` one command per call. Commit messages end with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- The frontend build (`npm --prefix web run build`) type-checks with `tsc --noEmit`; a task is not done while it fails.

## File map

| File | Responsibility |
|---|---|
| `src/flackey/app.py` | `ServerHandle` + module-level `serve()` (Task 1); `run()` accepts a handle |
| `src/flackey/desktop.py` (new) | Server thread, pywebview window, inset title bar, `WindowApi.resize`, dark detection (Task 2) |
| `src/flackey/cli.py` | `start` opens the window by default; `--no-browser` / `--browser` keep the old paths (Task 2) |
| `web/index.html` | Google Fonts removed (Task 3) |
| `web/src/theme.css` | Tokens (light + dark), base, buttons, fields, groups (Task 3) |
| `web/src/platform.ts` (new) | `insetTitlebar()`, `requestWindowSize()` (Task 3) |
| `web/src/components/Icon.tsx` (new) | Stroke icons (Task 3) |
| `web/src/app.css` | Screen layout CSS, rewritten section by section (Tasks 4–9) |
| `web/src/components/Shell.tsx`, `Sidebar.tsx`, `Banner.tsx` | Native shell (Task 4) |
| `web/src/App.tsx` | Inset flag, window sizes, Welcome → Folder → Telegram → Ready routing, Back (Tasks 4, 8, 9) |
| `web/src/components/download/*` + `presentation.ts` | Toolbar, grouped rows, verified badge (Task 5) |
| `web/src/components/library/*` | Search toolbar, table with header/alt rows/selection/hover reveal (Task 6) |
| `web/src/components/SettingsPage.tsx` | Grouped rows (Task 7) |
| `web/src/components/setup/SetupShell.tsx`, `FolderStep.tsx`, `TelegramStep.tsx`, `ReadyStep.tsx` | Stepper, bottom bar, restyle (Task 8) |
| `web/src/components/setup/WelcomeStep.tsx` (new), `web/public/welcome-rig.jpg` (new) | Animated Welcome (Task 9) |
| `src/flackey/assets/app-icon.png` (new), `web/public/icon.svg` + `icon.png` (new), `web/index.html`, `desktop.py` | Dock icon, menu-bar name, favicon (Task 11) |

Until a screen's task runs, that screen is unstyled (Task 4 replaces `app.css`). That is expected between tasks; every task's tests are behavioural.

---

### Task 1: `ServerHandle` and a module-level `serve()` in `app.py`

**Files:**
- Modify: `src/flackey/app.py` (the `serve_and_open` closure inside `run()`, lines 108–124, and `run()`'s signature/finally)
- Test: `tests/test_app.py`

**Interfaces:**
- Produces: `class ServerHandle` (dataclass: `on_started: Callable[[str], None] | None`, `started: threading.Event`, `url: str | None`, `error: BaseException | None`, `server: uvicorn.Server | None`; method `stop() -> None`), `async def serve(server, url: str, handle: ServerHandle, timeout_s: float = WEB_SERVER_START_TIMEOUT_S) -> None`, and `async def run(settings, open_browser: bool = True, handle: ServerHandle | None = None) -> None`. Task 2 consumes all three.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_app.py`:

```python
async def test_serve_publishes_the_url_once_the_server_is_listening():
    from flackey.app import ServerHandle, serve

    class FakeServer:
        started = False
        should_exit = False

        async def serve(self):
            await asyncio.sleep(0.01)
            self.started = True
            while not self.should_exit:
                await asyncio.sleep(0.01)

    opened = []
    server, handle = FakeServer(), ServerHandle(on_started=opened.append)
    task = asyncio.create_task(serve(server, "http://localhost:1", handle))
    await asyncio.wait_for(asyncio.to_thread(handle.started.wait, 1.0), 2.0)
    assert handle.url == "http://localhost:1" and handle.error is None and opened == ["http://localhost:1"]
    handle.stop()                      # what the desktop window does when it closes
    await asyncio.wait_for(task, 1.0)  # serve() returns because should_exit was set


async def test_serve_reports_a_startup_failure_through_the_handle():
    from flackey.app import ServerHandle, serve

    class FailingServer:
        started = False
        should_exit = False

        async def serve(self):
            raise OSError("address already in use")

    handle = ServerHandle()
    try:
        await serve(FailingServer(), "http://localhost:1", handle)
    except OSError:
        pass
    else:
        raise AssertionError("bind error must propagate")
    assert handle.started.is_set() and isinstance(handle.error, OSError) and handle.url is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest -q tests/test_app.py`
Expected: 2 failures with `ImportError: cannot import name 'ServerHandle'`.

- [ ] **Step 3: Implement `ServerHandle` and `serve()`**

In `src/flackey/app.py` add the imports `import threading`, `from collections.abc import Callable, Coroutine` (replace the existing `Coroutine` import line) and `from dataclasses import dataclass, field`. Add after `WEB_SERVER_START_TIMEOUT_S = 30`:

```python
@dataclass
class ServerHandle:
    """Filled in while the server starts so another thread (the desktop window in desktop.py) can learn the
    URL, learn that startup failed, and stop the server. `started` is set exactly once: on success (url
    set), on a startup error (error set), or when run() ends without ever starting."""
    on_started: Callable[[str], None] | None = None
    started: threading.Event = field(default_factory=threading.Event)
    url: str | None = None
    error: BaseException | None = None
    server: uvicorn.Server | None = None

    def stop(self) -> None:
        """Safe from any thread: uvicorn polls should_exit on its own loop."""
        if self.server is not None:
            self.server.should_exit = True


async def serve(server: uvicorn.Server, url: str, handle: ServerHandle,
                timeout_s: float = WEB_SERVER_START_TIMEOUT_S) -> None:
    """Run the server; once it is listening, publish the URL through `handle` and call on_started."""
    task = asyncio.create_task(server.serve())
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    try:
        while not server.started:
            if task.done():
                await task  # re-raises the bind error (port taken, etc.)
                raise RuntimeError("web server did not start")  # serve() returned without ever starting
            if loop.time() >= deadline:
                task.cancel()
                raise RuntimeError("web server did not start")
            await asyncio.sleep(0.1)
    except BaseException as e:
        handle.error = e
        handle.started.set()
        raise
    handle.server = server
    handle.url = url
    handle.started.set()
    log.info("UI at %s", url)
    if handle.on_started is not None:
        handle.on_started(url)
    await task
```

Change `run()`: signature `async def run(settings: Settings, open_browser: bool = True, handle: ServerHandle | None = None) -> None`. Right after the `server = uvicorn.Server(...)` line, delete the whole `serve_and_open` closure and put:

```python
    if handle is None:
        handle = ServerHandle(on_started=webbrowser.open if open_browser else None)
    url = f"http://localhost:{settings.web_port}"
```

Replace the `run_until_server_stops(serve_and_open(), ...)` call with `run_until_server_stops(serve(server, url, handle), supervise_worker(worker, status), close_streams_on_exit(server, bus))`, and add `handle.started.set()` as the first line of the `finally:` block (so a thread waiting on the handle wakes when startup fails before the server exists).

- [ ] **Step 4: Run the tests**

Run: `uv run pytest -q tests/test_app.py` → all pass. Then `uv run pytest -q --ignore=tests/live` → 249 passed. `uv run ruff check src tests` → clean.

- [ ] **Step 5: Commit**

```
/usr/bin/git -C /Users/delarea/Desktop/code/flackey/.claude/worktrees/native-mac-ui add src/flackey/app.py tests/test_app.py
/usr/bin/git -C /Users/delarea/Desktop/code/flackey/.claude/worktrees/native-mac-ui commit -m "refactor(app): ServerHandle lets another thread learn the URL and stop the server"
```

---

### Task 2: `desktop.py` window and the `crate start` window path

**Files:**
- Create: `src/flackey/desktop.py`
- Modify: `src/flackey/cli.py` (`start` command, lines 31–40), `pyproject.toml` (`layers` list)
- Test: `tests/test_desktop.py` (new), `tests/test_cli.py`

**Interfaces:**
- Consumes: `ServerHandle`, `serve`, `run`, `WEB_SERVER_START_TIMEOUT_S` from `flackey.app` (Task 1).
- Produces: `run_in_window(settings) -> None`, `start_server_thread(settings) -> tuple[threading.Thread, ServerHandle]`, `wait_for_server(handle, timeout_s) -> str`, `inset_titlebar(window) -> bool`, `system_is_dark() -> bool`, `class WindowApi` with `resize(width, height)`. The page (Task 3) relies on: URL suffix `?titlebar=inset` on macOS, and `window.pywebview.api.resize(w, h)`.

Facts verified against the installed pywebview 6.2.1 (`.venv/lib/python3.12/site-packages/webview/`): `frameless=True` hides the traffic lights (cocoa.py lines 701–707), so it is not the inset look; `window.native` is the `NSWindow` (cocoa.py line 595, set before `shown` fires); `create_window` accepts `js_api`, `min_size`, `background_color`, `transparent`, `vibrancy`; event handlers with zero parameters are called with no arguments (event.py line 40); `js_api` exposes public callables and skips names starting with `_` (util.py line 193); uvicorn only installs signal handlers on the main thread (server.py line 325), so `asyncio.run(run(...))` in a daemon thread is fine.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_desktop.py`:

```python
import asyncio
import sys

import pytest

from flackey.app import ServerHandle


def test_wait_for_server_returns_the_url():
    from flackey.desktop import wait_for_server

    h = ServerHandle(url="http://localhost:8765")
    h.started.set()
    assert wait_for_server(h, timeout_s=0.1) == "http://localhost:8765"


def test_wait_for_server_raises_the_startup_error():
    from flackey.desktop import wait_for_server

    h = ServerHandle(error=OSError("port taken"))
    h.started.set()
    with pytest.raises(OSError):
        wait_for_server(h, timeout_s=0.1)


def test_wait_for_server_times_out_as_a_runtime_error():
    from flackey.desktop import wait_for_server

    with pytest.raises(RuntimeError):
        wait_for_server(ServerHandle(), timeout_s=0.05)


def test_server_thread_fills_the_handle_and_reports_a_crash(monkeypatch):
    import flackey.desktop as desktop

    async def fake_run(settings, open_browser=True, handle=None):
        handle.url = "http://localhost:1"
        handle.started.set()
        await asyncio.sleep(0.01)
        raise RuntimeError("boom")

    monkeypatch.setattr(desktop, "run", fake_run)
    thread, handle = desktop.start_server_thread(settings=object())
    assert desktop.wait_for_server(handle, timeout_s=1.0) == "http://localhost:1"
    thread.join(1.0)
    assert not thread.is_alive() and isinstance(handle.error, RuntimeError)


def test_window_api_resize_forwards_to_the_window():
    from flackey.desktop import WindowApi

    class W:
        calls = []

        def resize(self, w, h):
            self.calls.append((w, h))

    api = WindowApi()
    api.resize(720, 540)          # before the window exists: no crash, nothing recorded
    api.attach(W())
    api.resize(720.0, 540.0)      # JS numbers arrive as floats
    assert W.calls == [(720, 540)]


def test_inset_titlebar_needs_a_native_handle():
    from flackey.desktop import inset_titlebar

    class NoNative:
        native = None

    assert inset_titlebar(NoNative()) is False


def test_system_is_dark_is_false_off_macos(monkeypatch):
    from flackey import desktop

    monkeypatch.setattr(sys, "platform", "linux")
    assert desktop.system_is_dark() is False
```

Append to `tests/test_cli.py`:

```python
def test_start_without_browser_does_not_need_pywebview(tmp_path: Path, monkeypatch):
    import sys

    import flackey.app as app_mod

    calls = []

    async def fake_run(settings, open_browser=True, handle=None):
        calls.append(open_browser)

    monkeypatch.setattr(app_mod, "run", fake_run)
    monkeypatch.setitem(sys.modules, "webview", None)  # `import webview` now raises ImportError
    r = runner.invoke(app, ["--env", str(_env(tmp_path)), "start", "--no-browser"])
    assert r.exit_code == 0 and "stopped" in r.output and calls == [False]


def test_start_opens_the_desktop_window_by_default(tmp_path: Path, monkeypatch):
    import flackey.desktop as desktop

    opened = []
    monkeypatch.setattr(desktop, "run_in_window", lambda settings: opened.append(settings.web_port))
    r = runner.invoke(app, ["--env", str(_env(tmp_path)), "start"])
    assert r.exit_code == 0 and opened == [8765]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest -q tests/test_desktop.py tests/test_cli.py`
Expected: `ModuleNotFoundError: No module named 'flackey.desktop'` and the two CLI tests fail.

- [ ] **Step 3: Write `desktop.py`**

```python
"""The desktop window. `crate start` runs the server on a background thread and shows the UI in a
pywebview (WKWebView) window on the main thread, which is where macOS insists the GUI loop lives.
Closing the window stops the server; Ctrl-C in the terminal still stops everything (pywebview installs
a Mach interrupt handler so the GUI loop returns)."""
from __future__ import annotations

import asyncio
import logging
import subprocess
import sys
import threading

from .app import WEB_SERVER_START_TIMEOUT_S, ServerHandle, run
from .config import Settings

log = logging.getLogger(__name__)
MAIN_SIZE = (1100, 720)
MIN_SIZE = (720, 540)  # the Welcome and setup screens ask for 720x540 through WindowApi.resize
LIGHT_WINDOW = "#ECECEC"  # keep in sync with --window in web/src/theme.css
DARK_WINDOW = "#1E1E1E"
INSET_FLAG = "?titlebar=inset"
_NS_FULL_SIZE_CONTENT_VIEW = 1 << 15  # NSWindowStyleMaskFullSizeContentView
_NS_WINDOW_TITLE_HIDDEN = 1           # NSWindowTitleHidden


def system_is_dark() -> bool:
    """macOS stores the appearance in `AppleInterfaceStyle`; the key is absent in light mode."""
    if sys.platform != "darwin":
        return False
    try:
        out = subprocess.run(["defaults", "read", "-g", "AppleInterfaceStyle"],
                             capture_output=True, text=True, timeout=2)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return out.returncode == 0 and out.stdout.strip() == "Dark"


class WindowApi:
    """Exposed to the page as `window.pywebview.api`. Only public methods are exported, hence `_window`."""

    def __init__(self) -> None:
        self._window = None

    def attach(self, window) -> None:
        self._window = window

    def resize(self, width: float, height: float) -> None:
        if self._window is not None:
            self._window.resize(int(width), int(height))


def start_server_thread(settings: Settings) -> tuple[threading.Thread, ServerHandle]:
    handle = ServerHandle()

    def target() -> None:
        try:
            asyncio.run(run(settings, open_browser=False, handle=handle))
        except BaseException as e:  # noqa: BLE001 - the window thread reports it; nothing else would
            handle.error = handle.error or e
            log.exception("server thread stopped with an error")
        finally:
            handle.started.set()

    thread = threading.Thread(target=target, name="flackey-server", daemon=True)
    thread.start()
    return thread, handle


def wait_for_server(handle: ServerHandle, timeout_s: float = WEB_SERVER_START_TIMEOUT_S + 5) -> str:
    handle.started.wait(timeout_s)
    if handle.error is not None:
        raise handle.error
    if handle.url is None:
        raise RuntimeError("web server did not start")
    return handle.url


def inset_titlebar(window) -> bool:
    """Traffic lights over the sidebar: transparent title bar, hidden title, content under the title bar.
    The AppKit calls are queued on the main thread. False when there is no native handle (not macOS)."""
    native = getattr(window, "native", None)
    if native is None or sys.platform != "darwin":
        return False
    try:
        from PyObjCTools import AppHelper
    except ImportError:
        return False

    def apply() -> None:
        native.setStyleMask_(native.styleMask() | _NS_FULL_SIZE_CONTENT_VIEW)
        native.setTitlebarAppearsTransparent_(True)
        native.setTitleVisibility_(_NS_WINDOW_TITLE_HIDDEN)

    AppHelper.callAfter(apply)
    return True


def run_in_window(settings: Settings) -> None:
    import webview  # lazy: `crate start --no-browser` must work without pywebview installed

    thread, handle = start_server_thread(settings)
    url = wait_for_server(handle)
    inset = sys.platform == "darwin"
    api = WindowApi()
    window = webview.create_window(
        "Flackey", url + (INSET_FLAG if inset else ""), js_api=api,
        width=MAIN_SIZE[0], height=MAIN_SIZE[1], min_size=MIN_SIZE,
        background_color=DARK_WINDOW if system_is_dark() else LIGHT_WINDOW,
        transparent=inset, vibrancy=inset)
    api.attach(window)
    if inset:
        window.events.shown += lambda: inset_titlebar(window)
    window.events.closed += handle.stop
    try:
        webview.start()
    finally:
        handle.stop()
        thread.join(timeout=15)
```

`transparent` + `vibrancy` put an `NSVisualEffectView` behind a background-less WKWebView; the page paints its own opaque `--window` everywhere except the sidebar (Task 4), so the desktop shows through the sidebar only, like Finder. If the window shows black or flickers on first launch (Task 10 checks), set both to `False` and keep the rest.

- [ ] **Step 4: Update the CLI and import layers**

In `src/flackey/cli.py` replace the `start` command:

```python
@app.command()
def start(no_browser: bool = typer.Option(False, "--no-browser", help="Serve only; open http://localhost:8765 yourself"),
          browser: bool = typer.Option(False, "--browser", help="Open the system browser instead of the app window")) -> None:
    """Run the worker and open the Flackey window; stops when the window closes or on Ctrl-C."""
    from .app import run

    try:
        if no_browser or browser:
            asyncio.run(run(_settings(), open_browser=browser))
        else:
            from .desktop import run_in_window

            run_in_window(_settings())
    except KeyboardInterrupt:
        pass
    typer.echo("stopped")
```

In `pyproject.toml` change the contract layers to start `"cli", "desktop", "app", "web", ...` (insert `"desktop"` between `cli` and `app`).

- [ ] **Step 5: Run the tests**

Run: `uv run pytest -q --ignore=tests/live` → 258 passed. `uv run lint-imports` → `Contracts: 1 kept, 0 broken.` `uv run ruff check src tests` → clean.

- [ ] **Step 6: Smoke the window by hand (macOS only, 20 seconds)**

Write `/private/tmp/claude-501/-Users-delarea-Desktop-code-flackey/e7b1cfad-5520-4bc1-8db2-a843f9bda47c/scratchpad/smoke.env` containing `TELEGRAM_API_ID=1`, `TELEGRAM_API_HASH=h`, `LIBRARY_ROOT=<scratchpad>/smoke/lib`, `DATA_DIR=<scratchpad>/smoke/data`, `WEB_PORT=8766` (one per line, absolute paths). Run `npm --prefix web run build` once so `web/dist` exists, then `uv run crate --env <that file> start` in the background with a 25 s timeout. Expected: a 1100×720 window titled Flackey appears showing the app; closing it makes the command print `stopped` and exit. Kill it if it hangs (`pkill -f "crate --env"`) and note what happened in the report; do not spend more than one retry on this.

- [ ] **Step 7: Commit**

```
/usr/bin/git -C /Users/delarea/Desktop/code/flackey/.claude/worktrees/native-mac-ui add src/flackey/desktop.py src/flackey/cli.py pyproject.toml uv.lock tests/test_desktop.py tests/test_cli.py
/usr/bin/git -C /Users/delarea/Desktop/code/flackey/.claude/worktrees/native-mac-ui commit -m "feat(desktop): crate start opens a pywebview window; server runs on a thread"
```

---

### Task 3: Tokens, base styles, platform helpers and icons

**Files:**
- Modify: `web/index.html` (remove the three font `<link>` lines), `web/src/theme.css` (rewrite)
- Create: `web/src/platform.ts`, `web/src/platform.test.ts`, `web/src/components/Icon.tsx`

**Interfaces:**
- Produces: CSS variables listed below (every later task uses them); classes `.btn-primary` (+`.lg`), `.btn-secondary`, `.btn-link` (+`.muted`), `.input`, `.group`, `.status-dot` (+`.amber`), `.mono`, `.muted`, `.faint`, `.err`, keyframes `pulse`; `insetTitlebar(): boolean`, `requestWindowSize(width: number, height: number): void`; `<Icon name size? stroke? />` with `IconName = 'download' | 'library' | 'settings' | 'playlist' | 'search' | 'folder' | 'check' | 'x' | 'chevron' | 'phone'`.

- [ ] **Step 1: Write the failing platform tests**

Create `web/src/platform.test.ts`:

```ts
import { insetTitlebar, requestWindowSize } from './platform'

const setSearch = (search: string) => { window.history.replaceState(null, '', `/${search}`) }
type W = Window & { pywebview?: { api?: { resize: (w: number, h: number) => void } } }

afterEach(() => { setSearch(''); delete (window as W).pywebview })

it('reads the inset flag from the query string', () => {
  expect(insetTitlebar()).toBe(false)
  setSearch('?titlebar=inset')
  expect(insetTitlebar()).toBe(true)
})

it('asks the pywebview window to resize when the bridge is there', () => {
  const resize = vi.fn()
  ;(window as W).pywebview = { api: { resize } }
  requestWindowSize(720, 540)
  expect(resize).toHaveBeenCalledWith(720, 540)
})

it('waits for pywebviewready when the bridge is not there yet, and is a no-op in a plain browser', () => {
  requestWindowSize(720, 540)   // nothing to call, must not throw
  const resize = vi.fn()
  ;(window as W).pywebview = { api: { resize } }
  window.dispatchEvent(new Event('pywebviewready'))
  expect(resize).toHaveBeenCalledWith(720, 540)
})
```

- [ ] **Step 2: Run it to verify it fails**

Run: `npm --prefix web test -- platform` → fails: cannot find module `./platform`.

- [ ] **Step 3: Write `platform.ts` and `Icon.tsx`**

`web/src/platform.ts`:

```ts
// What the page knows about the window it lives in. Set by src/flackey/desktop.py: the launcher adds
// `?titlebar=inset` on macOS and exposes `window.pywebview.api.resize`. In a plain browser both are absent.
type Bridge = { api?: { resize: (width: number, height: number) => unknown } }
const bridge = () => (window as Window & { pywebview?: Bridge }).pywebview

export const insetTitlebar = (): boolean => new URLSearchParams(window.location.search).get('titlebar') === 'inset'

export function requestWindowSize(width: number, height: number): void {
  const api = bridge()?.api
  if (api) { api.resize(width, height); return }
  window.addEventListener('pywebviewready', () => bridge()?.api?.resize(width, height), { once: true })
}
```

`web/src/components/Icon.tsx` (paths are the mockup's 24-unit stroke icons):

```tsx
export type IconName = 'download' | 'library' | 'settings' | 'playlist' | 'search' | 'folder' | 'check' | 'x' | 'chevron' | 'phone'
const PATHS: Record<IconName, string> = {
  download: 'M12 3a9 9 0 1 0 0 18 9 9 0 0 0 0-18ZM12 7v9M8.5 12.5 12 16l3.5-3.5',
  library: 'M4 6h10M4 12h10M4 18h6M19.5 16V7l3-1M14.5 16a2.5 2.5 0 1 0 5 0 2.5 2.5 0 0 0-5 0',
  settings: 'M12 9a3 3 0 1 0 0 6 3 3 0 0 0 0-6ZM12 2v3M12 19v3M2 12h3M19 12h3M4.9 4.9l2.1 2.1M17 17l2.1 2.1M4.9 19.1 7 17M17 7l2.1-2.1',
  playlist: 'M4 7h12M4 12h12M4 17h7M17 17.5a2.5 2.5 0 1 0 2.5-2.5V8l3-1',
  search: 'M11 4.5a6.5 6.5 0 1 0 0 13 6.5 6.5 0 0 0 0-13Zm5 11.5 4.5 4.5',
  folder: 'M3 7.5A1.5 1.5 0 0 1 4.5 6h5l2 2h8A1.5 1.5 0 0 1 21 9.5v8A1.5 1.5 0 0 1 19.5 19h-15A1.5 1.5 0 0 1 3 17.5z',
  check: 'm5 12.5 4.5 4.5L19 7.5',
  x: 'M7 7l10 10M17 7 7 17',
  chevron: 'm9 6 6 6-6 6',
  phone: 'M7 2.5h10v19H7zM11 18h2',
}
export default function Icon({ name, size = 16, stroke = 1.6 }: { name: IconName; size?: number; stroke?: number }) {
  return (<svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={stroke}
    strokeLinecap="round" strokeLinejoin="round" aria-hidden style={{ flex: 'none' }}><path d={PATHS[name]} /></svg>)
}
```

- [ ] **Step 4: Rewrite `theme.css` and strip the fonts from `index.html`**

Delete the three `<link ... fonts.googleapis.com / fonts.gstatic.com ...>` lines from `web/index.html`. Replace `web/src/theme.css` with:

```css
:root {
  color-scheme: light dark;
  --window: #ECECEC; --content: #FFFFFF;
  --sidebar: rgba(233,233,235,.86);
  --group: #FFFFFF; --group-border: rgba(0,0,0,.10);
  --well: #FFFFFF; --well-border: rgba(0,0,0,.16);
  --text: #1D1D1F; --secondary: rgba(0,0,0,.55); --tertiary: rgba(0,0,0,.28);
  --sep: rgba(0,0,0,.09);
  --accent: #007AFF; --accent-text: #FFFFFF;
  --selection: rgba(0,0,0,.07); --alt: rgba(0,0,0,.03); --chip: rgba(0,0,0,.08);
  --button: #FFFFFF; --button-border: rgba(0,0,0,.14); --button-shadow: 0 .5px 1px rgba(0,0,0,.12);
  --orange: #FF9500; --green: #34C759; --red: #FF3B30;
  --orange-wash: rgba(255,149,0,.08); --red-wash: rgba(255,59,48,.07);
  --sans: -apple-system, BlinkMacSystemFont, 'SF Pro Text', 'Helvetica Neue', sans-serif;
  --mono: 'SF Mono', Menlo, Monaco, monospace;
}
@media (prefers-color-scheme: dark) {
  :root {
    --window: #1E1E1E; --content: #1E1E1E;
    --sidebar: rgba(43,43,43,.86);
    --group: #2A2A2A; --group-border: rgba(255,255,255,.09);
    --well: #1B1B1B; --well-border: rgba(255,255,255,.16);
    --text: rgba(255,255,255,.87); --secondary: rgba(255,255,255,.55); --tertiary: rgba(255,255,255,.28);
    --sep: rgba(255,255,255,.09);
    --accent: #0A84FF;
    --selection: rgba(255,255,255,.09); --alt: rgba(255,255,255,.03); --chip: rgba(255,255,255,.12);
    --button: rgba(255,255,255,.11); --button-border: rgba(255,255,255,.10); --button-shadow: 0 .5px 1px rgba(0,0,0,.3);
    --orange: #FF9F0A; --green: #30D158; --red: #FF453A;
    --orange-wash: rgba(255,159,10,.07); --red-wash: rgba(255,69,58,.09);
  }
}
* { box-sizing: border-box; }
html, body, #root { height: 100%; margin: 0; }
body { background: var(--window); color: var(--text); font: 13px/1.35 var(--sans); -webkit-font-smoothing: antialiased; }
/* Inside the native window the WKWebView is transparent and a vibrancy view sits behind it: only the
   sidebar (rgba) lets it through; every screen paints --window itself. */
html.native body { background: transparent; }
h1, h2, p { margin: 0; }
button, input { font: inherit; color: inherit; }
button { cursor: default; }
.mono { font-family: var(--mono); font-size: 11px; }
.muted { color: var(--secondary); } .faint { color: var(--tertiary); }
.err { color: var(--red); font-size: 12px; }
.btn-primary { display: inline-flex; align-items: center; justify-content: center; height: 24px; padding: 0 12px; border: 0; border-radius: 6px; background: var(--accent); color: var(--accent-text); font-size: 13px; font-weight: 500; box-shadow: var(--button-shadow); white-space: nowrap; }
.btn-primary.lg { height: 30px; padding: 0 22px; border-radius: 7px; font-size: 14px; }
.btn-secondary { display: inline-flex; align-items: center; justify-content: center; height: 22px; padding: 0 10px; border: .5px solid var(--button-border); border-radius: 5px; background: var(--button); color: var(--text); font-size: 12px; box-shadow: var(--button-shadow); white-space: nowrap; }
.btn-primary:active { filter: brightness(.92); } .btn-secondary:active { background: var(--selection); }
.btn-primary:disabled, .btn-secondary:disabled { opacity: .45; }
.btn-link { background: none; border: 0; padding: 0; color: var(--accent); font-size: 12px; }
.btn-link.muted { color: var(--secondary); }
.input { height: 28px; width: 100%; padding: 0 8px; border: .5px solid var(--well-border); border-radius: 7px; background: var(--well); color: var(--text); font-size: 13px; outline: none; }
.input::placeholder { color: var(--tertiary); }
.input:focus, .btn-primary:focus-visible, .btn-secondary:focus-visible { border-color: var(--accent); box-shadow: 0 0 0 3px color-mix(in srgb, var(--accent) 35%, transparent); }
.group { background: var(--group); border: .5px solid var(--group-border); border-radius: 10px; overflow: hidden; }
.status-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--green); flex: none; }
.status-dot.amber { background: var(--orange); }
@keyframes pulse { 0%, 100% { opacity: 1 } 50% { opacity: .35 } }
```

In `web/src/main.tsx` add, before `createRoot`: `import { insetTitlebar } from './platform'` and `if (insetTitlebar()) document.documentElement.classList.add('native')`.

- [ ] **Step 5: Run the tests and the type check**

Run: `npm --prefix web test` → 65 passed (62 + 3). `npm --prefix web run build` → succeeds (Icon is unused for now; that is fine, `tsc` does not flag unused modules).

- [ ] **Step 6: Commit**

```
/usr/bin/git -C /Users/delarea/Desktop/code/flackey/.claude/worktrees/native-mac-ui add web/index.html web/src/theme.css web/src/main.tsx web/src/platform.ts web/src/platform.test.ts web/src/components/Icon.tsx
/usr/bin/git -C /Users/delarea/Desktop/code/flackey/.claude/worktrees/native-mac-ui commit -m "feat(ui): system-font tokens for light and dark, platform bridge, stroke icons"
```

---

### Task 4: Native shell (sidebar, drag strip, toolbar, banner)

**Files:**
- Modify: `web/src/components/Shell.tsx`, `Sidebar.tsx`, `Banner.tsx`, `web/src/App.tsx`, `web/src/app.css` (full replacement; later tasks append)
- Test: `web/src/components/Shell.test.tsx` (new)

**Interfaces:**
- Consumes: `Icon`, `insetTitlebar`, `requestWindowSize` (Task 3).
- Produces: `Shell` props `{ tab, onTab, telegramAuthorized, banner?, sidebarExtra?, inset: boolean, children }`; `Sidebar` props gain `inset: boolean`; CSS classes `.app`, `.sidebar`, `.drag-strip`, `.nav`, `.nav-item`, `.sidebar-footer`, `.content`, `.toolbar`, `.scroll`, `.empty`, `.banner`, `.app-loading`. Tasks 5–7 place their top row inside `<div className="toolbar">` and their body inside `<div className="scroll">`.

- [ ] **Step 1: Write the failing test**

Create `web/src/components/Shell.test.tsx`:

```tsx
import { render, screen, fireEvent } from '@testing-library/react'
import Shell from './Shell'

const props = { tab: 'download' as const, telegramAuthorized: true, onTab: vi.fn() }

it('reserves the title-bar drag strip only inside the inset native window', () => {
  const { container, rerender } = render(<Shell {...props} inset={false}>x</Shell>)
  expect(container.querySelector('.pywebview-drag-region')).toBeNull()
  rerender(<Shell {...props} inset>x</Shell>)
  expect(container.querySelector('.pywebview-drag-region')).not.toBeNull()
})

it('switches tabs and shows the Telegram state in the footer', () => {
  render(<Shell {...props} inset={false} telegramAuthorized={false}>x</Shell>)
  expect(screen.getByText('Telegram signed out')).toBeInTheDocument()
  fireEvent.click(screen.getByText('Library'))
  expect(props.onTab).toHaveBeenCalledWith('library')
})
```

- [ ] **Step 2: Run it to verify it fails**

Run: `npm --prefix web test -- Shell` → fails (type error / drag strip always absent).

- [ ] **Step 3: Rewrite Shell, Sidebar, Banner**

`web/src/components/Sidebar.tsx`:

```tsx
import type { ReactNode } from 'react'
import Icon, { type IconName } from './Icon'
export type Tab = 'download' | 'library' | 'settings'
const ITEMS: { tab: Tab; label: string; icon: IconName }[] = [
  { tab: 'download', label: 'Download', icon: 'download' }, { tab: 'library', label: 'Library', icon: 'library' }, { tab: 'settings', label: 'Settings', icon: 'settings' }]
export default function Sidebar({ tab, onTab, telegramAuthorized, inset, extra }: { tab: Tab; onTab: (t: Tab) => void; telegramAuthorized: boolean; inset: boolean; extra?: ReactNode }) {
  return (
    <nav className={`sidebar${inset ? ' inset' : ''}`}>
      {inset && <div className="drag-strip pywebview-drag-region" />}
      <div className="nav">{ITEMS.map(i => <button key={i.tab} className={`nav-item${tab === i.tab ? ' active' : ''}`} onClick={() => onTab(i.tab)}><Icon name={i.icon} />{i.label}</button>)}</div>
      {extra}
      <div className="sidebar-footer"><span className={`status-dot${telegramAuthorized ? '' : ' amber'}`} />{telegramAuthorized ? 'Telegram connected' : 'Telegram signed out'}</div>
    </nav>
  )
}
```

`web/src/components/Shell.tsx`:

```tsx
import type { ReactNode } from 'react'
import Sidebar, { type Tab } from './Sidebar'
export default function Shell({ tab, onTab, telegramAuthorized, banner, sidebarExtra, inset, children }: { tab: Tab; onTab: (t: Tab) => void; telegramAuthorized: boolean; banner?: ReactNode; sidebarExtra?: ReactNode; inset: boolean; children: ReactNode }) {
  return (
    <div className="app">
      <Sidebar tab={tab} onTab={onTab} telegramAuthorized={telegramAuthorized} inset={inset} extra={sidebarExtra} />
      <main className="content">{banner}{children}</main>
    </div>
  )
}
```

`web/src/components/Banner.tsx`: change the action button classes to `tone === 'amber' ? 'btn-primary' : 'btn-secondary'` (drop `sm`).

In `web/src/App.tsx`: import `{ insetTitlebar, requestWindowSize } from './platform'`; add `const inset = insetTitlebar()` at module level (outside the component); pass `inset={inset}` to `<Shell>`. Add, inside `App` before the early returns:

```tsx
  const mainScreen = !!h && h.setup_done && !reconnecting
  useEffect(() => { if (h) requestWindowSize(mainScreen ? 1100 : 720, mainScreen ? 720 : 540) }, [h, mainScreen])
```

(`h` is already defined above; hooks must stay above the `if (!h)` return.)

- [ ] **Step 4: Replace `app.css` with the shell section**

Overwrite `web/src/app.css` with exactly this (later tasks append their sections below a comment header):

```css
/* ---- Shell ---- */
.app { height: 100%; display: flex; min-height: 0; }
.app-loading { height: 100%; display: flex; align-items: center; justify-content: center; padding: 20px; background: var(--window); color: var(--secondary); }
.sidebar { width: 220px; flex: none; display: flex; flex-direction: column; background: var(--sidebar); -webkit-backdrop-filter: saturate(180%) blur(20px); backdrop-filter: saturate(180%) blur(20px); border-right: .5px solid var(--sep); }
.drag-strip { height: 52px; flex: none; }
.nav { display: flex; flex-direction: column; gap: 2px; padding: 12px 10px 0; }
.sidebar.inset .nav { padding-top: 4px; }
.nav-item { display: flex; align-items: center; gap: 8px; height: 28px; padding: 0 8px; border: 0; border-radius: 6px; background: none; color: var(--text); font-size: 13px; text-align: left; transition: background 120ms; }
.nav-item svg { color: var(--secondary); }
.nav-item:hover { background: color-mix(in srgb, var(--selection) 50%, transparent); }
.nav-item.active { background: var(--selection); font-weight: 500; }
.nav-item.active svg { color: var(--accent); }
.sidebar-footer { margin-top: auto; display: flex; align-items: center; gap: 8px; padding: 12px 18px; font-size: 12px; color: var(--secondary); }
.content { flex: 1; min-width: 0; display: flex; flex-direction: column; overflow: hidden; background: var(--window); }
.toolbar { height: 52px; flex: none; display: flex; align-items: center; gap: 10px; padding: 0 20px; border-bottom: .5px solid var(--sep); }
.scroll { flex: 1; overflow-y: auto; padding: 18px 20px 24px; display: flex; flex-direction: column; gap: 22px; }
.empty { padding: 40px 0; text-align: center; color: var(--secondary); }
.banner { display: flex; align-items: center; gap: 12px; padding: 8px 20px; font-size: 12px; border-bottom: .5px solid var(--sep); }
.banner.amber { background: var(--orange-wash); }
.banner.red { border: .5px solid color-mix(in srgb, var(--red) 40%, transparent); border-radius: 8px; padding: 8px 12px; background: var(--red-wash); }
.banner .dot { width: 8px; height: 8px; border-radius: 50%; flex: none; }
.banner.amber .dot { background: var(--orange); } .banner.red .dot { background: var(--red); }
.banner .text { flex: 1; }
```

- [ ] **Step 5: Run the tests and the type check**

Run: `npm --prefix web test` → 67 passed. `npm --prefix web run build` → succeeds.

- [ ] **Step 6: Commit**

```
/usr/bin/git -C /Users/delarea/Desktop/code/flackey/.claude/worktrees/native-mac-ui add web/src/app.css web/src/App.tsx web/src/components/Shell.tsx web/src/components/Sidebar.tsx web/src/components/Banner.tsx web/src/components/Shell.test.tsx
/usr/bin/git -C /Users/delarea/Desktop/code/flackey/.claude/worktrees/native-mac-ui commit -m "feat(ui): native shell with vibrancy sidebar, drag strip behind the inset title bar, window sizing"
```

---

### Task 5: Download screen

**Files:**
- Modify: `web/src/presentation.ts` (`RowView` interface line 13–16, `done` case lines 103–108), `web/src/presentation.test.ts` (line 39), `web/src/components/download/{PasteBar,FilterBar,DownloadPage,Group,RequestRow,CandidateCard,ProgressDots,Artwork,SpectrogramWell}.tsx`, `RequestRow.test.tsx` (fixture), `web/src/app.css` (append)
- Test: `web/src/components/download/RequestRow.test.tsx`, `presentation.test.ts`

**Interfaces:**
- Consumes: `.toolbar`, `.scroll`, `.empty`, `.group` (Tasks 3–4), `Icon`.
- Produces: `RowView.verifiedKbps: number | null` (new field; `status` of a filed row is now the relative path alone). Test fixtures building a `RowView` must include `verifiedKbps: null`.

- [ ] **Step 1: Update the tests first**

In `web/src/presentation.test.ts` line 39 replace the assertion with:

```ts
    expect(v.status).toBe('Ace Ventura / Ace Ventura - Rezonate.mp3')
    expect(v.verifiedKbps).toBe(320)
    expect(v.statusMono).toBe(true)
```

In `web/src/components/download/RequestRow.test.tsx` add `verifiedKbps: null` to the `base` fixture (after `removable: false`), and append:

```ts
it('shows the green verified badge on a filed row', () => {
  const view: RowView = { ...base, status: 'Ace Ventura / Ace Ventura - Rezonate.mp3', statusMono: true, verifiedKbps: 320, dimmed: false, tag: null,
    action: { label: 'Show in Finder', kind: 'reveal', path: '/a.mp3' }, bucket: 'done', removable: true }
  render(<RequestRow view={view} onAction={() => {}} onChoose={() => {}} />)
  expect(screen.getByText('320 kbps verified')).toBeInTheDocument()
  expect(screen.getByText('Ace Ventura / Ace Ventura - Rezonate.mp3')).toHaveClass('mono')
})
```

Search the tests for other `RowView` literals that need the new field: `grep -rn "removable:" web/src --include=*.test.tsx --include=*.test.ts` and add `verifiedKbps: null` to each.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `npm --prefix web test -- presentation RequestRow` → the done-row assertion and the badge test fail; TypeScript complains about the unknown field.

- [ ] **Step 3: Presentation change**

In `web/src/presentation.ts` add `verifiedKbps: number | null` to the `RowView` interface (line 14 area) and `verifiedKbps: null` to the initial object in `presentRow`. In the `case 'done':` branch replace the `if (b.track)` body with:

```ts
        v.status = relPath(b.track.path, opts.libraryRoot)
        v.verifiedKbps = b.track.bitrate_kbps
        v.statusTone = 'muted'; v.statusMono = true
        v.action = { label: 'Show in Finder', kind: 'reveal', path: b.track.path }
        if (!v.version) v.version = b.track.mix_name
```

- [ ] **Step 4: Rewrite the download components**

`PasteBar.tsx`: render as the toolbar; keep all state and handlers:

```tsx
  return (
    <div className="toolbar">
      <form className="pastebar" onSubmit={submit} aria-label="add link" role="form">
        <input className="input" placeholder="Paste a YouTube or YouTube Music link" value={url} onChange={e => setUrl(e.target.value)} />
        <button className="btn-primary" type="submit" disabled={pending}>{pending ? 'Adding…' : 'Add'}</button>
      </form>
      {note && <div className={`note${note.error ? ' error' : ''}`}>{note.text}</div>}
    </div>
  )
```

`FilterBar.tsx`: keep markup; only change the button class `btn-secondary sm` → `btn-secondary`.

`DownloadPage.tsx`: replace the two `<p className="muted" style=...>` empty states with `<div className="empty">Paste a link above to start digging.</div>` and `<div className="empty">Nothing here.</div>` (same texts).

`Group.tsx`: `<section className="group-section">`; heading stays `<div className="group-head"><h2>…</h2><span className="summary">…</span></div>`; the rows container becomes `<div className="group">`.

`RequestRow.tsx`:

```tsx
import Artwork from './Artwork'
import CandidateCard from './CandidateCard'
import ProgressDots from './ProgressDots'
import SpectrogramWell from './SpectrogramWell'
import Icon from '../Icon'
import type { RowAction, RowView } from '../../presentation'

interface Props { view: RowView; whyOpen?: boolean; onAction: (kind: RowAction['kind'], rowId: number, path?: string) => void; onChoose: (rowId: number, candidateId: number) => void }

export default function RequestRow({ view: v, whyOpen, onAction, onChoose }: Props) {
  const cls = ['row', v.dimmed && 'dimmed', v.washed && 'washed', v.rejected && 'rejected'].filter(Boolean).join(' ')
  const showDots = v.dots && !v.rejected && v.verifiedKbps == null
  return (
    <div className={cls}>
      <div className="row-main">
        <Artwork url={v.artworkUrl} rejected={v.rejected} />
        <div className="row-text">
          <div className="title">{v.title}{v.version && <span className="version"> ({v.version})</span>}</div>
          <div className={`status ${v.statusTone}${v.statusMono ? ' mono' : ''}`}>{v.status}</div>
        </div>
        <div className="row-right">
          {v.verifiedKbps != null ? <span className="verified"><Icon name="check" size={13} stroke={2.2} />{v.verifiedKbps} kbps verified</span>
            : v.tag ? <span className="tag">{v.tag}</span> : showDots ? <ProgressDots dots={v.dots!} label={v.stepLabel} /> : null}
          {v.action && v.action.kind !== 'cancel' && (
            <button className="btn-secondary" onClick={() => onAction(v.action!.kind, v.id, v.action!.path)}>{v.action.label}</button>)}
          {v.removable && <button className="btn-link muted" onClick={() => onAction('remove', v.id, undefined)}>Remove</button>}
        </div>
      </div>
      {v.candidates && (<>
        <div className="candidates">{v.candidates.map(c => <CandidateCard key={c.id} c={c} onChoose={() => onChoose(v.id, c.id)} />)}</div>
        {v.action?.kind === 'cancel' && <div className="skip"><button className="btn-link" onClick={() => onAction('cancel', v.id, undefined)}>{v.action.label}</button></div>}
      </>)}
      {v.rejection && whyOpen && <SpectrogramWell r={v.rejection} />}
    </div>
  )
}
```

`CandidateCard.tsx`: button class `c.chosen ? 'btn-primary' : 'btn-secondary'` (drop `sm`). `ProgressDots.tsx` unchanged (keeps `.dots`, `.dot6`, `.steps`; the test counts `.dot6`). `Artwork.tsx`, `SpectrogramWell.tsx` unchanged.

- [ ] **Step 5: Append the Download CSS to `app.css`**

```css
/* ---- Download ---- */
.pastebar { flex: 1; display: flex; gap: 10px; min-width: 0; }
.toolbar .note { flex: none; max-width: 40%; font-size: 11px; color: var(--secondary); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.toolbar .note.error { color: var(--red); }
.filterbar { display: flex; align-items: center; gap: 6px; flex: none; height: 38px; padding: 0 20px; border-bottom: .5px solid var(--sep); }
.chip { display: flex; align-items: center; gap: 5px; height: 22px; padding: 0 10px; border: 0; border-radius: 11px; background: none; color: var(--secondary); font-size: 12px; transition: background 120ms; }
.chip:hover { background: color-mix(in srgb, var(--selection) 50%, transparent); }
.chip[aria-pressed="true"] { background: var(--selection); color: var(--text); font-weight: 500; }
.chip .count { font-size: 11px; color: var(--tertiary); }
.chip .count.amber { color: var(--orange); } .chip .count.red { color: var(--red); }
.filterbar .btn-secondary { margin-left: auto; }
.group-section { display: flex; flex-direction: column; gap: 8px; }
.group-head { display: flex; align-items: baseline; gap: 10px; padding: 0 2px; }
.group-head h2 { font-size: 15px; font-weight: 600; letter-spacing: -.01em; }
.group-head .summary { font-size: 12px; color: var(--secondary); }
.group-head .needs { color: var(--orange); } .group-head .rej { color: var(--red); }
.row { display: flex; flex-direction: column; border-top: .5px solid var(--sep); }
.row:first-child { border-top: 0; }
.row.washed { background: var(--orange-wash); } .row.dimmed { opacity: .6; }
.row-main { display: flex; align-items: center; gap: 12px; min-height: 54px; padding: 8px 12px; }
.row-text { flex: 1; min-width: 0; display: flex; flex-direction: column; gap: 3px; }
.row .title { font-size: 13px; font-weight: 500; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.row .title .version { font-weight: 400; color: var(--secondary); }
.row .status { font-size: 11px; color: var(--secondary); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.status.amber { color: var(--orange); } .status.red { color: var(--red); } .status.green { color: var(--green); }
.row-right { flex: none; display: flex; align-items: center; gap: 10px; }
.verified { display: inline-flex; align-items: center; gap: 5px; font-size: 12px; color: var(--green); white-space: nowrap; }
.tag { font-size: 11px; color: var(--tertiary); }
.dots { display: flex; align-items: center; gap: 4px; }
.dot6 { width: 6px; height: 6px; border-radius: 50%; background: var(--tertiary); }
.dot6.done { background: var(--green); } .dot6.current { background: var(--orange); animation: pulse 1.2s infinite; }
.steps { margin-left: 6px; font-size: 11px; color: var(--tertiary); white-space: nowrap; }
.artwork { width: 36px; height: 36px; flex: none; border-radius: 4px; object-fit: cover; background: linear-gradient(135deg, #47608A, #1C2536); box-shadow: inset 0 0 0 .5px rgba(0,0,0,.15); }
.artwork.sm { width: 28px; height: 28px; }
.artwork.rejected { display: grid; place-items: center; color: var(--red); background: var(--red-wash); font-size: 16px; }
.candidates { display: flex; flex-wrap: wrap; gap: 10px; padding: 0 12px 12px 60px; }
.candidate { flex: 1 1 260px; display: flex; flex-direction: column; gap: 6px; padding: 10px 12px; border-radius: 8px; background: var(--group); border: .5px solid var(--group-border); }
.candidate.chosen { border: 1.5px solid var(--accent); padding: 9px 11px; }
.candidate .head { display: flex; justify-content: space-between; align-items: baseline; gap: 8px; font-size: 13px; font-weight: 500; }
.candidate .head .version { font-weight: 400; color: var(--secondary); }
.candidate .score { font-size: 12px; font-weight: 500; color: var(--secondary); white-space: nowrap; }
.candidate .meta { font-size: 12px; color: var(--secondary); }
.skip { padding: 0 12px 12px 60px; }
.well { margin: 0 12px 12px 60px; padding: 12px 14px; border-radius: 8px; background: var(--well); border: .5px solid var(--well-border); }
.well img { display: block; width: 100%; height: 84px; object-fit: cover; border-radius: 5px; }
.well .cut { position: relative; }
.well .cutline { position: absolute; left: 0; right: 0; top: 30%; border-top: 1px dashed var(--red); }
.well .cutlabel { position: absolute; right: 8px; top: calc(30% - 18px); font-size: 11px; font-weight: 500; color: var(--red); }
.well .caption { margin-top: 10px; font-size: 12px; color: var(--secondary); }
```

- [ ] **Step 6: Run the tests and the type check**

Run: `npm --prefix web test` → 68 passed. `npm --prefix web run build` → succeeds.

- [ ] **Step 7: Commit**

```
/usr/bin/git -C /Users/delarea/Desktop/code/flackey/.claude/worktrees/native-mac-ui add web/src/presentation.ts web/src/presentation.test.ts web/src/components/download web/src/app.css
/usr/bin/git -C /Users/delarea/Desktop/code/flackey/.claude/worktrees/native-mac-ui commit -m "feat(ui): Download as toolbar plus inset grouped lists; verified badge on filed rows"
```

---

### Task 6: Library screen

**Files:**
- Modify: `web/src/components/library/{LibraryPage,TrackTable,PlaylistCard,PlaylistNav}.tsx`, `web/src/app.css` (append)
- Test: `web/src/components/library/TrackTable.test.tsx`

**Interfaces:**
- Consumes: `.toolbar`, `.scroll`, `.group`, `Icon`.
- Produces: `TrackTable` keeps `{ tracks, onReveal }`; rows get `.trow`, `.selected` on click, `onReveal(path)` on double-click and from the hover button.

- [ ] **Step 1: Update the tests**

Replace `web/src/components/library/TrackTable.test.tsx` assertions:

```tsx
it('renders a track row from catalog data', () => {
  const onReveal = vi.fn()
  const { container } = render(<TrackTable tracks={[t]} onReveal={onReveal} />)
  expect(screen.getByText('Astral Projection – Into the Void')).toBeInTheDocument()
  expect(screen.getByText('Original Mix')).toBeInTheDocument()
  expect(screen.getByText('Psy-Trance')).toBeInTheDocument()
  expect(screen.getByText('TIP Records')).toBeInTheDocument()
  expect(screen.getByText('2002')).toBeInTheDocument()
  expect(screen.getByText('320')).toBeInTheDocument()
  expect(container.querySelector('.kbps svg')).not.toBeNull()
  fireEvent.click(screen.getByText('Show in Finder'))
  expect(onReveal).toHaveBeenCalledWith('/lib/Astral Projection/x.mp3')
})

it('falls back to the track tags when there is no catalog record', () => {
  render(<TrackTable tracks={[{ ...t, catalog: null }]} onReveal={() => {}} />)
  expect(screen.getAllByText('Unknown').length).toBeGreaterThanOrEqual(2)
})

it('shows a bare kbps number with no checkmark when the track is not verified', () => {
  const { container } = render(<TrackTable tracks={[{ ...t, verified_at: null }]} onReveal={() => {}} />)
  expect(screen.getByText('320')).toBeInTheDocument()
  expect(container.querySelector('.kbps svg')).toBeNull()
})

it('selects a row on click and reveals it on double-click', () => {
  const onReveal = vi.fn()
  render(<TrackTable tracks={[t, { ...t, id: 2, title: 'Second' }]} onReveal={onReveal} />)
  const row = screen.getByText('Astral Projection – Into the Void').closest('.trow')!
  fireEvent.click(row)
  expect(row).toHaveClass('selected')
  fireEvent.click(screen.getByText('Astral Projection – Second').closest('.trow')!)
  expect(row).not.toHaveClass('selected')
  fireEvent.doubleClick(row)
  expect(onReveal).toHaveBeenCalledWith('/lib/Astral Projection/x.mp3')
})
```

- [ ] **Step 2: Run to verify failure**

Run: `npm --prefix web test -- TrackTable` → the first and last tests fail.

- [ ] **Step 3: Rewrite the library components**

`TrackTable.tsx`:

```tsx
import { useState } from 'react'
import Artwork from '../download/Artwork'
import Icon from '../Icon'
import type { Track } from '../../api'
export default function TrackTable({ tracks, onReveal }: { tracks: Track[]; onReveal: (path: string) => void }) {
  const [selected, setSelected] = useState<number | null>(null)
  return (<div className="group table">
    <div className="thead"><span /><span>Track</span><span>Genre</span><span>Label</span><span>Year</span><span className="num">Kbps</span><span /></div>
    {tracks.map(t => {
      const c = t.catalog
      return (<div className={`trow${selected === t.id ? ' selected' : ''}`} key={t.id} onClick={() => setSelected(t.id)} onDoubleClick={() => onReveal(t.path)}>
        <Artwork url={c?.artwork_url ?? null} small />
        <div className="cell"><div className="t">{t.artist} – {t.title}</div><div className="v">{t.mix_name}</div></div>
        <span>{c?.genre ?? 'Unknown'}</span><span className="ellipsis">{c?.label ?? 'Unknown'}</span><span>{c?.release_date?.slice(0, 4) ?? ''}</span>
        <span className="kbps">{t.bitrate_kbps}{t.verified_at && <Icon name="check" size={12} stroke={2.4} />}</span>
        <span className="reveal"><button className="btn-secondary" onClick={e => { e.stopPropagation(); onReveal(t.path) }}>Show in Finder</button></span>
      </div>)
    })}
    {tracks.length === 0 && <div className="empty">Nothing here yet.</div>}
  </div>)
}
```

`LibraryPage.tsx` return block:

```tsx
  return (<>
    <div className="toolbar">
      <div className="search"><Icon name="search" size={14} /><input className="input" placeholder="Search" value={q} onChange={e => setQ(e.target.value)} aria-label="search library" /></div>
      {s && <span className="counts">{s.tracks} tracks · {s.playlists} playlists · {gb(s.bytes)} on disk</span>}
      {s && <button className="btn-secondary" onClick={() => reveal(s.library_root)}>Open library folder</button>}
    </div>
    <div className="scroll">
      {queryError && <Banner tone="red" text={queryError} action={{ label: 'Dismiss', onClick: () => setQueryError(null) }} />}
      {actionError && <Banner tone="red" text={actionError} action={{ label: 'Dismiss', onClick: () => setActionError(null) }} />}
      {pl && <PlaylistCard playlist={pl} count={pl.track_ids.length} onShowFile={() => reveal(pl.file)} />}
      <TrackTable tracks={tracks} onReveal={reveal} />
    </div>
  </>)
```

(add `import Icon from '../Icon'`). Check `LibraryPage.test.tsx` for how it finds the search box: if it uses the old placeholder `'Search artist, title or label'`, keep that placeholder text instead of `'Search'`.

`PlaylistCard.tsx`:

```tsx
import Icon from '../Icon'
import type { Playlist } from '../../api'
export default function PlaylistCard({ playlist, count, onShowFile }: { playlist: Playlist; count: number; onShowFile: () => void }) {
  return (<div className="group pl-card">
    <span className="pl-icon"><Icon name="playlist" size={22} /></span>
    <div className="how"><h2>{playlist.name} <span>· {count} tracks</span></h2>In Rekordbox: File → Import → Playlist, then choose this file. Safe to re-import after new tracks are added.</div>
    <button className="btn-secondary" onClick={onShowFile}>Show playlist file</button>
  </div>)
}
```

`PlaylistNav.tsx`:

```tsx
import Icon from '../Icon'
import type { Playlist } from '../../api'
export default function PlaylistNav({ playlists, selected, onSelect }: { playlists: Playlist[]; selected: number | null; onSelect: (id: number | null) => void }) {
  return (<div className="pl-nav">
    <div className="pl-label">Playlists</div>
    <button className={`pl-item${selected == null ? ' active' : ''}`} onClick={() => onSelect(null)}><Icon name="playlist" size={15} />All tracks</button>
    {playlists.map(p => <button key={p.id} className={`pl-item${selected === p.id ? ' active' : ''}`} onClick={() => onSelect(p.id)} title={p.name}><Icon name="playlist" size={15} /><span className="ellipsis">{p.name}</span></button>)}
  </div>)
}
```

- [ ] **Step 4: Append the Library CSS**

```css
/* ---- Library ---- */
.ellipsis { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.pl-nav { display: flex; flex-direction: column; gap: 2px; padding: 18px 10px 0; }
.pl-label { padding: 0 8px 4px; font-size: 11px; font-weight: 600; color: var(--tertiary); }
.pl-item { display: flex; align-items: center; gap: 8px; height: 26px; padding: 0 8px; border: 0; border-radius: 6px; background: none; color: var(--text); font-size: 13px; text-align: left; min-width: 0; }
.pl-item svg { color: var(--secondary); }
.pl-item.active { background: var(--selection); }
.search { position: relative; width: 260px; flex: none; }
.search svg { position: absolute; left: 8px; top: 7px; color: var(--tertiary); pointer-events: none; }
.search .input { padding-left: 26px; }
.toolbar .counts { font-size: 12px; color: var(--secondary); white-space: nowrap; }
.toolbar .counts + .btn-secondary { margin-left: auto; }
.pl-card { display: flex; align-items: center; gap: 14px; padding: 14px 16px; }
.pl-icon { display: flex; color: var(--secondary); }
.pl-card .how { flex: 1; font-size: 12px; color: var(--secondary); line-height: 1.5; }
.pl-card h2 { margin-bottom: 4px; font-size: 15px; font-weight: 600; letter-spacing: -.01em; color: var(--text); }
.pl-card h2 span { font-weight: 400; color: var(--secondary); }
.table { background: var(--content); }
.thead, .trow { display: grid; grid-template-columns: 36px minmax(0, 2.4fr) 1fr 1.4fr 56px 64px 110px; gap: 12px; align-items: center; padding: 0 12px; }
.thead { height: 26px; font-size: 11px; font-weight: 500; color: var(--secondary); border-bottom: .5px solid var(--sep); }
.thead .num { text-align: right; }
.trow { height: 44px; font-size: 12px; color: var(--secondary); }
.trow:nth-child(odd) { background: var(--alt); }
.trow .cell { min-width: 0; }
.trow .t { color: var(--text); font-size: 13px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.trow .v { font-size: 11px; }
.trow .kbps { display: inline-flex; justify-content: flex-end; align-items: center; gap: 4px; color: var(--text); }
.trow .kbps svg { color: var(--green); }
.trow .reveal { display: flex; justify-content: flex-end; opacity: 0; transition: opacity 120ms; }
.trow:hover .reveal, .trow.selected .reveal, .trow .reveal:focus-within { opacity: 1; }
.trow.selected, .trow.selected:nth-child(odd) { background: var(--accent); color: rgba(255,255,255,.9); }
.trow.selected .t, .trow.selected .kbps { color: #fff; } .trow.selected .v { color: rgba(255,255,255,.8); } .trow.selected .kbps svg { color: #fff; }
```

- [ ] **Step 5: Run tests and type check**

Run: `npm --prefix web test` → 69 passed. `npm --prefix web run build` → succeeds.

- [ ] **Step 6: Commit**

```
/usr/bin/git -C /Users/delarea/Desktop/code/flackey/.claude/worktrees/native-mac-ui add web/src/components/library web/src/app.css
/usr/bin/git -C /Users/delarea/Desktop/code/flackey/.claude/worktrees/native-mac-ui commit -m "feat(ui): Library table with header, alternating rows, selection and hover reveal"
```

---

### Task 7: Settings screen

**Files:**
- Modify: `web/src/components/SettingsPage.tsx`, `web/src/app.css` (append)
- Test: `web/src/components/SettingsPage.test.tsx` (must pass unchanged: it finds `Change`, `Save`, `Cancel`, `Choose…`, `Show in Finder`, `Show logs`, `Sign out`, `Dismiss`, the path, `Flackey 0.1.0`, the Telegram line)

- [ ] **Step 1: Run the existing test to confirm the baseline passes**

Run: `npm --prefix web test -- SettingsPage` → passes.

- [ ] **Step 2: Rewrite the return block**

Keep every hook, handler and error state in `SettingsPage.tsx`; replace only the JSX from `return (` to the end:

```tsx
  return (
    <div className="scroll settings">
      {revealError && <Banner tone="red" text={revealError} action={{ label: 'Dismiss', onClick: () => setRevealError(null) }} />}
      {tgError && <Banner tone="red" text={tgError} action={{ label: 'Dismiss', onClick: () => setTgError(null) }} />}
      <h1>Settings</h1>
      <div className="group">
        <div className="srow"><div className="srow-body"><div className="k">Library folder</div>
          {editing ? <><input className="input" value={path} onChange={e => setPath(e.target.value)} />{err && <div className="err">{err}</div>}</> : <div className="v mono">{s.library_root}</div>}</div>
          <div className="actions">{editing
            ? <>{pickerAvailable && <button className="btn-secondary" onClick={chooseFolderClicked} disabled={busy}>Choose…</button>}<button className="btn-secondary" onClick={() => { setEditing(false); setErr(null) }} disabled={busy}>Cancel</button><button className="btn-primary" onClick={save} disabled={busy}>Save</button></>
            : <button className="btn-secondary" onClick={() => { setPath(s.library_root); setEditing(true); setErr(null) }}>Change</button>}</div></div>
      </div>
      <div className="group">
        <div className="srow"><div className="srow-body"><div className="k">Telegram</div>
          <div className="v"><span className={`status-dot${authorized ? '' : ' amber'}`} />{telegramLine}</div></div>
          <div className="actions">{authorized ? <button className="btn-secondary" onClick={signOut} disabled={signingOut}>Sign out</button>
            : <button className="btn-secondary" onClick={onReconnect}>Reconnect</button>}</div></div>
      </div>
      <div className="group">
        <div className="srow"><div className="srow-body"><div className="k">App version</div><div className="v">Flackey {s.version}</div></div></div>
        <div className="srow"><div className="srow-body"><div className="k">App data</div><div className="v mono">{s.data_dir}</div></div>
          <div className="actions"><button className="btn-secondary" onClick={reveal}>Show in Finder</button><button className="btn-secondary" onClick={showLogs}>Show logs</button></div></div>
      </div>
    </div>
  )
```

- [ ] **Step 3: Append the Settings CSS**

```css
/* ---- Settings ---- */
.settings { max-width: 640px; gap: 14px; padding: 22px 20px; }
.settings h1 { margin-bottom: 4px; font-size: 22px; font-weight: 700; letter-spacing: -.02em; }
.srow { display: flex; align-items: center; gap: 12px; min-height: 48px; padding: 10px 14px; border-top: .5px solid var(--sep); }
.srow:first-child { border-top: 0; }
.srow-body { flex: 1; min-width: 0; }
.srow .k { font-size: 13px; }
.srow .v { display: flex; align-items: center; gap: 8px; margin-top: 2px; font-size: 12px; color: var(--secondary); }
.srow .v.mono { display: block; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.srow .input { margin-top: 4px; }
.srow .actions { display: flex; gap: 8px; flex: none; }
```

- [ ] **Step 4: Run tests and type check**

Run: `npm --prefix web test` → 69 passed. `npm --prefix web run build` → succeeds.

- [ ] **Step 5: Commit**

```
/usr/bin/git -C /Users/delarea/Desktop/code/flackey/.claude/worktrees/native-mac-ui add web/src/components/SettingsPage.tsx web/src/app.css
/usr/bin/git -C /Users/delarea/Desktop/code/flackey/.claude/worktrees/native-mac-ui commit -m "feat(ui): Settings as System Settings style grouped rows"
```

---

### Task 8: Setup shell with stepper and bottom bar; steps restyled

**Files:**
- Modify: `web/src/components/setup/{SetupShell,FolderStep,TelegramStep,ReadyStep}.tsx`, `web/src/App.tsx` (setup branch), `web/src/app.css` (append)
- Test: `web/src/components/setup/SetupShell.test.tsx` (new); `FolderStep.test.tsx`, `TelegramStep.test.tsx`, `ReadyStep.test.tsx` must keep passing

**Interfaces:**
- Consumes: `Icon`, tokens.
- Produces: `SetupShell` props `{ step: 1 | 2 | 3; onBack?: () => void; hint?: string; children }`; CSS `.setup`, `.setup-title`, `.setup-body`, `.stepper`, `.step`, `.disc`, `.setup-content`, `.setup-bar`. Task 9 reuses `.setup` and `.setup-title` for the Welcome screen frame.

- [ ] **Step 1: Write the failing test**

Create `web/src/components/setup/SetupShell.test.tsx`:

```tsx
import { render, screen, fireEvent } from '@testing-library/react'
import SetupShell from './SetupShell'

it('marks steps done, current and next', () => {
  const { container } = render(<SetupShell step={2}>body</SetupShell>)
  const steps = container.querySelectorAll('.step')
  expect(steps[0]).toHaveClass('done')
  expect(steps[1]).toHaveClass('cur')
  expect(steps[2]).not.toHaveClass('done')
  expect(steps[2]).not.toHaveClass('cur')
  expect(screen.getByText('Welcome to Flackey')).toBeInTheDocument()
  expect(screen.getByText('body')).toBeInTheDocument()
})

it('shows Back and the hint in the bottom bar when given', () => {
  const onBack = vi.fn()
  render(<SetupShell step={2} onBack={onBack} hint="Waiting for Telegram…">body</SetupShell>)
  fireEvent.click(screen.getByText('Back'))
  expect(onBack).toHaveBeenCalled()
  expect(screen.getByText('Waiting for Telegram…')).toBeInTheDocument()
})
```

- [ ] **Step 2: Run it to verify it fails**

Run: `npm --prefix web test -- SetupShell` → fails (no `.step` elements / `hint` prop unknown).

- [ ] **Step 3: Rewrite `SetupShell.tsx`**

```tsx
import type { ReactNode } from 'react'
import Icon from '../Icon'
const NAMES = ['Folder', 'Telegram', 'Ready']
export default function SetupShell({ step, children, onBack, hint }: { step: 1 | 2 | 3; children: ReactNode; onBack?: () => void; hint?: string }) {
  return (<div className="setup">
    <div className="setup-title pywebview-drag-region">Welcome to Flackey</div>
    <div className="setup-body">
      <div className="stepper">{NAMES.map((n, i) => {
        const k = i + 1; const state = k < step ? 'done' : k === step ? 'cur' : 'next'
        return <div key={n} className={`step ${state}`}><span className="disc">{state === 'done' ? <Icon name="check" size={10} stroke={3} /> : k}</span>{n}</div>
      })}</div>
      <div className="setup-content">{children}</div>
      <div className="setup-bar">{onBack ? <button className="btn-secondary" onClick={onBack}>Back</button> : <span />}{hint && <span className="hint">{hint}</span>}</div>
    </div>
  </div>)
}
```

- [ ] **Step 4: Restyle the three steps (markup only; keep every hook, handler, message and test id)**

`FolderStep.tsx` return:

```tsx
  return (<>
    <h1>Where should your music live?</h1>
    <p className="lead">Every finished track is filed here, one folder per artist. Rekordbox reads straight from this folder.</p>
    <div className="folder">
      <div className="folder-well"><Icon name="folder" size={18} /><input value={path} onChange={e => setPath(e.target.value)} aria-label="library folder" /></div>
      <div className="hint-row">Type or paste the full folder path{pickerAvailable && <button className="btn-secondary" onClick={chooseFolderClicked} disabled={busy}>Choose…</button>}</div>
      {err && <div className="err">{err}</div>}
    </div>
    <button className="btn-primary lg" onClick={go} disabled={busy}>Continue</button>
  </>)
```

(add `import Icon from '../Icon'`).

`ReadyStep.tsx`: replace `<div className="ready-disc">✓</div>` with `<div className="ready-disc"><Icon name="check" size={26} stroke={2.4} /></div>`, `<div className="ready-disc bad">×</div>` with `<div className="ready-disc bad"><Icon name="x" size={24} stroke={2.4} /></div>` (both places), change the primary buttons to `className="btn-primary lg"`, and drop the `style={{ fontSize: 14 }}` / `style={{ marginTop: 8 }}` props (add `import Icon from '../Icon'`).

`TelegramStep.tsx`: change the QR colours to `color: { dark: '#111111', light: '#ffffff' }`; replace `<span className="qr-badge">✈</span>` with nothing (remove); the phone-mode placeholder card becomes `<div className="qr-card phone"><Icon name="phone" size={48} stroke={1.2} /></div>`; the three `<h1 style={{ fontSize: 20 }}>` lose their style prop; every `btn-primary sm` becomes `btn-primary`; remove inline `style={{ marginTop: ... }}` props and wrap the buttons that had them in `<div className="row-gap">`; the `pwBox` becomes:

```tsx
  const pwBox = needPw && (<div className="pw-box"><div className="k">Your account has a two-step password</div>
    <div className="row-gap"><input className="input" type="password" placeholder="Two-step password" value={pw} onChange={e => setPw(e.target.value)} />
    <button className="btn-primary" onClick={submitPw} disabled={busy}>Sign in</button></div></div>)
```

(add `import Icon from '../Icon'`). Texts stay identical so `TelegramStep.test.tsx` passes.

- [ ] **Step 5: Wire Back and the hint in `App.tsx`**

In the setup branch of `App`, replace the `<SetupShell ...>` opening tag with:

```tsx
    const back = reconnecting && current === 2 ? () => { setReconnecting(false); setStep(1) }
      : current === 2 ? () => setStep(1) : current === 3 ? () => setStep(2) : undefined
    return (<SetupShell step={current} onBack={back} hint={current === 2 ? 'Waiting for Telegram…' : undefined}>
```

(Task 9 adds the Welcome-aware `back` for step 1.)

- [ ] **Step 6: Append the Setup CSS**

```css
/* ---- Setup ---- */
.setup { height: 100%; display: flex; flex-direction: column; background: var(--window); }
.setup-title { height: 52px; flex: none; display: flex; align-items: center; justify-content: center; border-bottom: .5px solid var(--sep); font-size: 13px; font-weight: 600; color: var(--secondary); }
.setup-body { flex: 1; min-height: 0; display: flex; flex-direction: column; gap: 22px; padding: 22px 36px 20px; }
.stepper { display: flex; justify-content: center; gap: 22px; }
.step { display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--secondary); }
.step.cur { color: var(--text); font-weight: 500; }
.disc { display: flex; align-items: center; justify-content: center; width: 16px; height: 16px; border-radius: 8px; background: var(--chip); color: var(--secondary); font-size: 10px; }
.step.cur .disc { background: var(--accent); color: #fff; } .step.done .disc { background: var(--green); color: #fff; }
.setup-content { flex: 1; min-height: 0; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 16px; text-align: center; }
.setup h1 { font-size: 22px; font-weight: 700; letter-spacing: -.02em; line-height: 1.15; }
.setup .lead { max-width: 440px; font-size: 13px; color: var(--secondary); line-height: 1.5; }
.setup-bar { display: flex; justify-content: space-between; align-items: center; min-height: 22px; }
.setup-bar .hint { font-size: 12px; color: var(--tertiary); }
.folder { display: flex; flex-direction: column; gap: 8px; align-items: center; }
.folder-well { width: 440px; max-width: 100%; display: flex; align-items: center; gap: 10px; height: 34px; padding: 0 10px; border-radius: 8px; background: var(--well); border: .5px solid var(--well-border); }
.folder-well svg { color: var(--secondary); }
.folder-well input { flex: 1; min-width: 0; border: 0; outline: none; background: none; font: 12px var(--mono); color: var(--text); }
.hint-row { display: flex; align-items: center; gap: 8px; font-size: 11px; color: var(--tertiary); }
.row-gap { display: flex; align-items: center; gap: 8px; margin-top: 6px; }
.tg { display: grid; grid-template-columns: 220px minmax(0, 1fr); gap: 32px; align-items: center; width: 100%; text-align: left; }
.tg .lead { max-width: none; }
.qr-card { width: 220px; height: 220px; padding: 12px; border-radius: 8px; background: #fff; border: .5px solid rgba(0,0,0,.15); display: grid; place-items: center; }
.qr-card img { display: block; width: 196px; height: 196px; }
.qr-card.phone { background: var(--well); border-color: var(--well-border); color: var(--tertiary); }
.qr-hint { margin-top: 8px; font-size: 11px; color: var(--tertiary); text-align: center; }
.pw-box { display: flex; flex-direction: column; gap: 4px; margin-top: 12px; padding: 12px 14px; border-radius: 8px; background: var(--orange-wash); border: .5px solid var(--orange); }
.pw-box .k { font-size: 13px; font-weight: 500; }
.footnote { margin-top: 16px; padding-top: 10px; border-top: .5px solid var(--sep); font-size: 11px; color: var(--tertiary); line-height: 1.5; }
.ready-disc { display: grid; place-items: center; width: 56px; height: 56px; border-radius: 50%; background: color-mix(in srgb, var(--green) 15%, transparent); color: var(--green); }
.ready-disc.bad { background: var(--red-wash); color: var(--red); }
.path-well { padding: 8px 14px; border-radius: 8px; background: var(--well); border: .5px solid var(--well-border); font: 12px var(--mono); }
```

- [ ] **Step 7: Run tests and type check**

Run: `npm --prefix web test` → 71 passed. `npm --prefix web run build` → succeeds.

- [ ] **Step 8: Commit**

```
/usr/bin/git -C /Users/delarea/Desktop/code/flackey/.claude/worktrees/native-mac-ui add web/src/components/setup web/src/App.tsx web/src/app.css
/usr/bin/git -C /Users/delarea/Desktop/code/flackey/.claude/worktrees/native-mac-ui commit -m "feat(ui): setup window with numbered stepper, bottom bar and native fields"
```

---

### Task 9: Welcome screen with the animated DJ rig

**Files:**
- Create: `web/src/components/setup/WelcomeStep.tsx`, `web/src/components/setup/WelcomeStep.test.tsx`, `web/public/welcome-rig.jpg` (copy of `docs/superpowers/design/gui/welcome-rig-crop.jpg`, 1078×460)
- Modify: `web/src/App.tsx` (routing), `web/src/app.css` (append)

**Interfaces:**
- Consumes: `.setup`, `.setup-title` (Task 8), `SetupShell`.
- Produces: `WelcomeStep({ onStart: () => void })`.

Geometry below is in percentages of the illustration (so a better file drops in) and pixel sizes for the 560×239 slot (1078×460 scaled by 0.5195). Deck tilts are −20°, −8°, 8°, 20° from the outer left deck to the outer right deck. Estimated from the image; Task 10 nudges them after a screenshot.

- [ ] **Step 1: Write the failing test**

```tsx
import { render, screen, fireEvent } from '@testing-library/react'
import WelcomeStep from './WelcomeStep'

it('shows the pitch, the rig with four spinning decks, and Get started', () => {
  const onStart = vi.fn()
  const { container } = render(<WelcomeStep onStart={onStart} />)
  expect(screen.getByText('Flackey')).toBeInTheDocument()
  expect(screen.getByText('Paste a YouTube link. Get the real 320, tagged and filed where Rekordbox will find it.')).toBeInTheDocument()
  expect(container.querySelectorAll('.ring')).toHaveLength(4)
  expect(container.querySelectorAll('.screen')).toHaveLength(4)
  expect(container.querySelectorAll('.meter')).toHaveLength(4)
  expect(container.querySelector('img')?.getAttribute('src')).toBe('/welcome-rig.jpg')
  fireEvent.click(screen.getByText('Get started'))
  expect(onStart).toHaveBeenCalled()
})
```

- [ ] **Step 2: Run it to verify it fails**

Run: `npm --prefix web test -- WelcomeStep` → cannot find module.

- [ ] **Step 3: Copy the asset and write the component**

Copy `docs/superpowers/design/gui/welcome-rig-crop.jpg` to `web/public/welcome-rig.jpg` (create the `web/public` directory; Vite serves it at `/welcome-rig.jpg`).

`web/src/components/setup/WelcomeStep.tsx`:

```tsx
import type { CSSProperties } from 'react'

// Overlay geometry as percentages of the illustration (1078x460) so a higher-res file drops in.
// Jog wheels: [x%, y%, ring diameter px at the 560px slot]. Screens: [x%, y%, w, h, tilt deg].
const WHEELS: [number, number, number][] = [[16, 70, 78], [34, 58, 74], [68.5, 58, 74], [85.5, 70, 78]]
const SCREENS: [number, number, number, number, number][] = [[11, 41, 64, 36, -20], [31.3, 28.5, 64, 36, -8], [69, 28.5, 64, 36, 8], [89, 41, 64, 36, 20]]
const METERS = [46.5, 49.1, 51.7, 54.3]  // x% of the mixer's LED strips; top 27%

const bars = Array.from({ length: 40 }, (_, i) => {
  const h = 6 + Math.abs(Math.sin(i * 1.7) * 12 + Math.cos(i * 0.6) * 6)
  return `<rect x='${i * 5}' y='${(30 - h) / 2}' width='2.5' height='${h.toFixed(1)}' rx='1' fill='%2366ccff'/>`
}).join('')
const WAVE = `url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='200' height='30' viewBox='0 0 200 30'>${bars}</svg>")`

const ringStyle = ([x, y, d]: [number, number, number], i: number): CSSProperties => {
  const mask = `radial-gradient(circle, transparent 0 ${d / 2 - 9}px, #000 ${d / 2 - 8}px ${d / 2 - 2}px, transparent ${d / 2}px)`
  return { left: `${x}%`, top: `${y}%`, width: d, height: d, margin: `${-d / 2}px 0 0 ${-d / 2}px`, WebkitMaskImage: mask, maskImage: mask, animationDelay: `${(i * -0.45).toFixed(2)}s` }
}
const screenStyle = ([x, y, w, h, r]: [number, number, number, number, number]): CSSProperties =>
  ({ left: `${x}%`, top: `${y}%`, width: w, height: h, margin: `${-h / 2}px 0 0 ${-w / 2}px`, transform: `rotate(${r}deg)` })

export default function WelcomeStep({ onStart }: { onStart: () => void }) {
  return (<div className="setup welcome">
    <div className="setup-title pywebview-drag-region" />
    <div className="welcome-body">
      <div className="rig">
        <img src="/welcome-rig.jpg" alt="" />
        {SCREENS.map((s, i) => <div key={i} className="screen" style={screenStyle(s)}>
          <div className="wave" style={{ backgroundImage: WAVE, animationDelay: `${(i * -0.9).toFixed(1)}s` }} /><div className="playhead" /></div>)}
        {WHEELS.map((w, i) => <div key={i} className="ring" style={ringStyle(w, i)} />)}
        {METERS.map((x, i) => <div key={i} className="meter" style={{ left: `${x}%`, animationDelay: `${(i * -0.12).toFixed(2)}s` }} />)}
      </div>
      <div className="pitch">
        <h1>Flackey</h1>
        <p className="lead">Paste a YouTube link. Get the real 320, tagged and filed where Rekordbox will find it.</p>
      </div>
      <div className="cta">
        <button className="btn-primary lg" onClick={onStart}>Get started</button>
        <div className="note">Two minutes: pick a folder, connect Telegram.</div>
      </div>
    </div>
  </div>)
}
```

- [ ] **Step 4: Route Welcome in `App.tsx`**

Add `import WelcomeStep from './components/setup/WelcomeStep'` and the state `const [welcomeSeen, setWelcomeSeen] = useState(false)` next to `step`. In the setup branch, before `const current = ...`, add:

```tsx
    if (!reconnecting && !welcomeSeen) return <WelcomeStep onStart={() => setWelcomeSeen(true)} />
```

and extend `back` so step 1 returns to Welcome: `: current === 1 && !reconnecting ? () => setWelcomeSeen(false) : undefined` (replace the trailing `: undefined` of the chain from Task 8).

- [ ] **Step 5: Append the Welcome CSS**

```css
/* ---- Welcome ---- */
.welcome .setup-title { border-bottom: 0; }
.welcome-body { flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 12px; padding: 0 40px 34px; text-align: center; }
.welcome h1 { font-size: 34px; font-weight: 700; letter-spacing: -.03em; line-height: 1.05; }
.welcome .pitch { display: flex; flex-direction: column; align-items: center; gap: 8px; }
.welcome .lead { max-width: 380px; font-size: 14px; color: var(--secondary); line-height: 1.45; }
.welcome .cta { display: flex; flex-direction: column; align-items: center; gap: 10px; margin-top: 6px; }
.welcome .note { font-size: 11px; color: var(--tertiary); }
.rig { position: relative; width: 560px; height: 239px; background: var(--window); animation: rise .7s cubic-bezier(.2,.7,.2,1) both, float 5s ease-in-out .7s infinite alternate; }
.rig img { display: block; width: 100%; height: 100%; object-fit: cover; }
/* the placeholder crop has a light grey background; multiply melts it into the dark window */
@media (prefers-color-scheme: dark) { .rig img { mix-blend-mode: multiply; } }
.ring { position: absolute; border-radius: 50%; mix-blend-mode: screen; background: conic-gradient(from 0deg, rgba(90,200,255,0) 0deg, rgba(90,200,255,0) 215deg, rgba(120,220,255,.95) 330deg, rgba(90,200,255,0) 360deg); animation: spin 1.8s linear infinite; }
.screen { position: absolute; overflow: hidden; border-radius: 3px; mix-blend-mode: screen; -webkit-mask-image: linear-gradient(90deg, transparent, #000 15%, #000 85%, transparent); mask-image: linear-gradient(90deg, transparent, #000 15%, #000 85%, transparent); }
.wave { position: absolute; inset: 0; background-repeat: repeat-x; background-position: 0 50%; opacity: .75; animation: scroll 3.2s linear infinite; }
.playhead { position: absolute; left: 50%; top: 0; bottom: 0; width: 1.5px; background: rgba(255,255,255,.85); }
.meter { position: absolute; top: 27%; width: 4px; height: 40px; border-radius: 2px; opacity: .85; background: linear-gradient(0deg, rgba(90,230,120,.9) 0 55%, rgba(255,200,60,.9) 55% 80%, rgba(255,80,60,.95) 80%); mix-blend-mode: screen; transform-origin: bottom; animation: vu .47s ease-in-out infinite; }
@keyframes rise { from { opacity: 0; transform: translateY(14px) scale(.985); } to { opacity: 1; transform: translateY(0) scale(1); } }
@keyframes float { from { transform: translateY(0); } to { transform: translateY(-5px); } }
@keyframes spin { to { transform: rotate(360deg); } }
@keyframes scroll { from { background-position-x: 0; } to { background-position-x: -200px; } }
@keyframes vu { 0%, 100% { transform: scaleY(.35); } 30% { transform: scaleY(.95); } 60% { transform: scaleY(.55); } }
@media (prefers-reduced-motion: reduce) { .rig, .ring, .wave, .meter, .dot6.current { animation: none; } .rig { opacity: 1; } }
```

- [ ] **Step 6: Run tests, type check and the production build**

Run: `npm --prefix web test` → 72 passed. `npm --prefix web run build` → succeeds and `web/dist/welcome-rig.jpg` exists.

- [ ] **Step 7: Commit**

```
/usr/bin/git -C /Users/delarea/Desktop/code/flackey/.claude/worktrees/native-mac-ui add web/public/welcome-rig.jpg web/src/components/setup/WelcomeStep.tsx web/src/components/setup/WelcomeStep.test.tsx web/src/App.tsx web/src/app.css
/usr/bin/git -C /Users/delarea/Desktop/code/flackey/.claude/worktrees/native-mac-ui commit -m "feat(ui): Welcome screen with the animated DJ rig before setup"
```

---

### Task 10: Visual review in light and dark (done by the orchestrating session)

**Files:**
- Possibly modify: `web/src/app.css`, `WelcomeStep.tsx` geometry constants, `desktop.py` (transparent/vibrancy flags)
- Modify: `docs/superpowers/specs/2026-09-07-native-mac-ui-design.md` section 5.1 asset note (crop is now 1078×460)

- [ ] **Step 1: Serve the built UI against a scratch data dir**

`npm --prefix web run build`, then run `uv run crate --env <scratchpad>/smoke.env start --no-browser` in the background (env file from Task 2 step 6, port 8766).

- [ ] **Step 2: Screenshot every screen with Playwright, light and dark**

Open `http://localhost:8766/` at 720×540: Welcome, then Get started → Folder step, Continue → Telegram step. Then `POST /api/setup/done` (see `api.ts` for the exact path) and reload at 1100×720 with `?titlebar=inset`: Download (empty), Library (empty), Settings. Repeat with the colour scheme emulated dark (`page.emulateMedia({ colorScheme: 'dark' })` through `browser_run_code_unsafe`). Compare with the canvas artboards and the HIG checklist in `.claude/skills/macos-design-guidelines`.

- [ ] **Step 3: Nudge the rig overlays**

Check that each ring sits on its jog wheel and each waveform on its screen; adjust `WHEELS`, `SCREENS`, `METERS` in `WelcomeStep.tsx` until they do. Confirm the dark-mode multiply trick hides the crop's grey background.

- [ ] **Step 4: Launch the real window once**

`uv run crate --env <scratchpad>/smoke.env start` — confirm the Dock shows the crate-and-record icon and the menu bar says Flackey (Task 11), traffic lights sit over the sidebar with the 52px strip, the sidebar shows vibrancy, resizing to 720×540 happens on the Welcome/setup screens and back to 1100×720 after setup, and closing the window prints `stopped`. If the transparent window misbehaves, set `transparent=False, vibrancy=False` in `desktop.py`.

- [ ] **Step 5: Update the spec's asset note, run both suites, commit**

`uv run pytest -q --ignore=tests/live`, `npm --prefix web test`, `uv run lint-imports`, then commit `docs: spec asset note; visual review fixes`.

---

### Task 11: App icon in the Dock and a favicon (runs after Task 9, before Task 10)

An unbundled Python process shows a blank document in the Dock and "Python" in the menu bar. pywebview 6.2.1 already forwards `webview.start(icon=path)` to `NSApplication.setApplicationIconImage_` on macOS (`webview/platforms/cocoa.py` lines 628–630, despite the docstring saying GTK/QT only), so the Dock icon is one keyword argument plus a bundled PNG. The menu-bar name is a best-effort write of `CFBundleName` into the main bundle's info dictionary before AppKit finishes launching (pywebview mutates the same dictionary at `cocoa.py` line 35). The favicon covers the `--browser` path.

Design sources (committed): `docs/superpowers/design/gui/app-icon.png` (1024×1024 RGBA, transparent margins, rendered from `app-icon-source.html`), `docs/superpowers/design/gui/icon.svg` (64×64 favicon), `docs/superpowers/design/gui/icon.png` (256×256 tile).

**Files:**
- Create: `src/flackey/assets/app-icon.png` (copy of `docs/superpowers/design/gui/app-icon.png`), `web/public/icon.svg` (copy of `docs/superpowers/design/gui/icon.svg`), `web/public/icon.png` (copy of `docs/superpowers/design/gui/icon.png`), `web/src/favicon.test.ts`
- Modify: `src/flackey/desktop.py`, `web/index.html`
- Test: `tests/test_desktop.py` (append)

**Interfaces:**
- Consumes: `run_in_window`, `start_server_thread`, `ServerHandle` (Task 2).
- Produces: `APP_NAME = "Flackey"`, `APP_ICON: Path`, `app_icon() -> str | None`, `set_app_name(name: str = APP_NAME) -> bool` in `desktop.py`.

Hatch's wheel target packages the whole `src/flackey` directory, so the PNG ships with no `pyproject.toml` change.

- [ ] **Step 1: Copy the assets**

```
cp docs/superpowers/design/gui/app-icon.png src/flackey/assets/app-icon.png
cp docs/superpowers/design/gui/icon.svg web/public/icon.svg
cp docs/superpowers/design/gui/icon.png web/public/icon.png
```

(create `src/flackey/assets/` first; `web/public/` exists since Task 9.)

- [ ] **Step 2: Write the failing Python tests**

Append to `tests/test_desktop.py`:

```python
def test_app_icon_asset_is_a_1024_square_png():
    from flackey.desktop import APP_ICON

    data = APP_ICON.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    width, height = int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
    assert (width, height) == (1024, 1024)


def test_app_icon_is_none_when_the_asset_is_missing(monkeypatch, tmp_path):
    from flackey import desktop

    monkeypatch.setattr(desktop, "APP_ICON", tmp_path / "missing.png")
    assert desktop.app_icon() is None


def test_set_app_name_is_false_off_macos(monkeypatch):
    from flackey import desktop

    monkeypatch.setattr(sys, "platform", "linux")
    assert desktop.set_app_name() is False


def test_run_in_window_hands_the_dock_icon_to_pywebview(monkeypatch):
    import threading
    import types

    from flackey import desktop

    class Hook:
        def __iadd__(self, fn):
            return self

    class Window:
        def __init__(self):
            self.events = types.SimpleNamespace(shown=Hook(), closed=Hook())

    started = {}
    fake_webview = types.SimpleNamespace(
        create_window=lambda *a, **kw: Window(), start=lambda **kw: started.update(kw))
    monkeypatch.setitem(sys.modules, "webview", fake_webview)
    handle = ServerHandle(url="http://localhost:1")
    handle.started.set()
    thread = threading.Thread(target=lambda: None)
    thread.start()
    monkeypatch.setattr(desktop, "start_server_thread", lambda settings: (thread, handle))
    monkeypatch.setattr(desktop, "app_icon", lambda: "/icons/app-icon.png")
    monkeypatch.setattr(desktop, "set_app_name", lambda: True)
    desktop.run_in_window(settings=object())
    assert started == {"icon": "/icons/app-icon.png"}
```

- [ ] **Step 3: Run them to see them fail**

Run: `uv run pytest -q tests/test_desktop.py`
Expected: the four new tests fail (`ImportError`/`AttributeError` on `APP_ICON`, `app_icon`, `set_app_name`; the last one fails on `started == {}`).

- [ ] **Step 4: Add the icon and name to `desktop.py`**

Add `from pathlib import Path` to the imports. After `_NS_WINDOW_TITLE_HIDDEN = 1` add:

```python
APP_NAME = "Flackey"
APP_ICON = Path(__file__).with_name("assets") / "app-icon.png"


def app_icon() -> str | None:
    """Path of the Dock icon, or None when the asset is missing (pywebview then keeps the default)."""
    return str(APP_ICON) if APP_ICON.is_file() else None


def set_app_name(name: str = APP_NAME) -> bool:
    """An unbundled Python process is called "Python" in the menu bar. Best effort: write CFBundleName
    into the main bundle's info dictionary before AppKit finishes launching (pywebview reads the same
    dictionary for its Hide/Quit menu items). False off macOS, without AppKit, or when the write fails."""
    if sys.platform != "darwin":
        return False
    try:
        from AppKit import NSBundle
    except ImportError:
        return False
    try:
        bundle = NSBundle.mainBundle()
        info = bundle.localizedInfoDictionary() or bundle.infoDictionary()
        info["CFBundleName"] = name
    except Exception:  # noqa: BLE001 - cosmetic; the window still opens
        log.debug("could not set the app name", exc_info=True)
        return False
    return True
```

In `run_in_window`, insert `set_app_name()` as the first line after `import webview`, and change `webview.start()` to `webview.start(icon=app_icon())`. Nothing else changes.

- [ ] **Step 5: Run the Python tests**

Run: `uv run pytest -q tests/test_desktop.py`
Expected: all pass. Then `uv run pytest -q --ignore=tests/live` (expected: previous count + 4) and `uv run lint-imports` (1 kept, 0 broken).

- [ ] **Step 6: Write the failing favicon test**

Create `web/src/favicon.test.ts`:

```ts
import { existsSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const root = resolve(__dirname, '..')

it('index.html links the SVG and PNG favicons and both files exist', () => {
  const html = readFileSync(resolve(root, 'index.html'), 'utf8')
  expect(html).toContain('<link rel="icon" type="image/svg+xml" href="/icon.svg" />')
  expect(html).toContain('<link rel="icon" type="image/png" sizes="256x256" href="/icon.png" />')
  expect(existsSync(resolve(root, 'public/icon.svg'))).toBe(true)
  expect(existsSync(resolve(root, 'public/icon.png'))).toBe(true)
})
```

Run: `npm --prefix web test -- favicon`
Expected: FAIL on the first `toContain`.

- [ ] **Step 7: Link the favicons**

In `web/index.html`, directly after the `<title>Flackey</title>` line, add:

```html
    <link rel="icon" type="image/svg+xml" href="/icon.svg" />
    <link rel="icon" type="image/png" sizes="256x256" href="/icon.png" />
```

Run: `npm --prefix web test -- favicon` (PASS), then `npm --prefix web test` (previous count + 1) and `npm --prefix web run build` (the two files appear in `web/dist/`).

- [ ] **Step 8: Commit**

```
/usr/bin/git -C /Users/delarea/Desktop/code/flackey/.claude/worktrees/native-mac-ui add src/flackey/assets/app-icon.png src/flackey/desktop.py tests/test_desktop.py web/public/icon.svg web/public/icon.png web/index.html web/src/favicon.test.ts
/usr/bin/git -C /Users/delarea/Desktop/code/flackey/.claude/worktrees/native-mac-ui commit -m "feat(desktop): app icon in the Dock, app name in the menu bar, favicon"
```
