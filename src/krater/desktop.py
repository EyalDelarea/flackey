"""The desktop window. `crate start` runs the server on a background thread and shows the UI in a
pywebview (WKWebView) window on the main thread, which is where macOS insists the GUI loop lives.
Closing the window stops the server; Ctrl-C in the terminal still stops everything (pywebview installs
a Mach interrupt handler so the GUI loop returns)."""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

from .app import UI_DIR, WEB_SERVER_START_TIMEOUT_S, ServerHandle, run
from .config import Settings

log = logging.getLogger(__name__)
MAIN_SIZE = (1100, 720)
MIN_SIZE = (720, 540)  # the Welcome and setup screens ask for 720x540 through WindowApi.resize
LIGHT_WINDOW = "#ECECEC"  # keep in sync with --window in web/src/theme.css
DARK_WINDOW = "#1E1E1E"
INSET_FLAG = "?titlebar=inset"
_NS_FULL_SIZE_CONTENT_VIEW = 1 << 15  # NSWindowStyleMaskFullSizeContentView
_NS_WINDOW_TITLE_HIDDEN = 1           # NSWindowTitleHidden

APP_NAME = "Krater"
APP_ICON = Path(__file__).with_name("assets") / "app-icon.png"
BUNDLE_ID = "app.krater"
BUNDLED_ENV = "KRATER_BUNDLED"  # set on the re-exec'd process so it does not bundle itself again
_INFO_PLIST = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>{APP_NAME}</string>
  <key>CFBundleDisplayName</key><string>{APP_NAME}</string>
  <key>CFBundleExecutable</key><string>{APP_NAME}</string>
  <key>CFBundleIdentifier</key><string>{BUNDLE_ID}</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
"""


def build_bundle(settings: Settings) -> Path | None:
    """The Dock names an unbundled process after its executable ("python3.12"), whatever the info
    dictionary says, because LaunchServices reads the name from the bundle on disk. So build a minimal
    `Krater.app` in the data dir around a copy of the interpreter: `Contents/pyvenv.cfg` (copied
    from this venv) makes Python treat `Contents/` as the venv, and `Contents/lib` links to the venv's
    lib so the same site-packages load. Rebuilt on every launch so it follows the venv it was started
    from. Returns the bundle's executable, or None when this interpreter is not a venv or the copy
    fails (the window then opens unbundled)."""
    base = Path(getattr(sys, "_base_executable", sys.executable)).resolve()
    venv_cfg = Path(sys.prefix) / "pyvenv.cfg"
    if not venv_cfg.is_file() or not base.is_file():
        return None
    contents = settings.data_dir / f"{APP_NAME}.app" / "Contents"
    exe = contents / "MacOS" / APP_NAME
    try:
        (contents / "MacOS").mkdir(parents=True, exist_ok=True)
        (contents / "Info.plist").write_text(_INFO_PLIST)
        shutil.copyfile(venv_cfg, contents / "pyvenv.cfg")
        lib = contents / "lib"
        if lib.is_symlink():
            lib.unlink()
        elif lib.exists():
            shutil.rmtree(lib)
        lib.symlink_to(Path(sys.prefix) / "lib")
        src, dst = base.stat(), exe.stat() if exe.exists() else None
        if dst is None or (dst.st_size, int(dst.st_mtime)) != (src.st_size, int(src.st_mtime)):
            shutil.copy2(base, exe)  # keeps mtime, which is what the comparison above relies on
        exe.chmod(0o755)
    except OSError:
        log.warning("could not build %s.app in %s; the Dock will show the interpreter's name", APP_NAME,
                    settings.data_dir, exc_info=True)
        return None
    return exe


def relaunch_bundled(settings: Settings) -> bool:
    """Exec this process again through the app bundle so the Dock and menu bar say Krater. Never
    returns on success (the process is replaced). False off macOS, when already running bundled, or when
    the bundle cannot be built."""
    if sys.platform != "darwin" or os.environ.get(BUNDLED_ENV):
        return False
    exe = build_bundle(settings)
    if exe is None:
        return False
    env = {**os.environ, BUNDLED_ENV: "1"}
    os.execve(str(exe), [str(exe), "-c", "from krater.cli import app; app()", *sys.argv[1:]], env)
    return True  # pragma: no cover - execve does not return


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

    thread = threading.Thread(target=target, name="krater-server", daemon=True)
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
    """Traffic lights over the sidebar: transparent title bar, hidden title, content under the title bar,
    and the see-through background the vibrancy layer shows through. The AppKit calls are queued on the
    main thread. False when there is no native handle (not macOS)."""
    native = getattr(window, "native", None)
    if native is None or sys.platform != "darwin":
        return False
    try:
        import AppKit
        from PyObjCTools import AppHelper
    except ImportError:
        return False

    def apply() -> None:
        native.setStyleMask_(native.styleMask() | _NS_FULL_SIZE_CONTENT_VIEW)
        native.setTitlebarAppearsTransparent_(True)
        native.setTitleVisibility_(_NS_WINDOW_TITLE_HIDDEN)
        # The three lines pywebview's own `transparent=True` would run, minus its deprecated selector:
        # it sets the KVC key 'drawsTransparentBackground', which resolves to WKWebView's deprecated
        # -_setDrawsTransparentBackground: and makes AppKit log on every launch. 'drawsBackground' = NO
        # is the same effect through a selector AppKit does not complain about. Doing it here instead of
        # at window creation also means the window opens on the opaque `background_color` and only then
        # goes clear, so there is no white flash before the page paints.
        native.setOpaque_(False)
        native.setBackgroundColor_(AppKit.NSColor.clearColor())
        content = native.contentView()   # pywebview makes the WKWebView the window's content view
        if content is not None:
            content.setValue_forKey_(False, "drawsBackground")
        native.setHasShadow_(True)  # a non-opaque window loses its shadow, and a shadowless window is not native

    AppHelper.callAfter(apply)
    return True


def run_in_window(settings: Settings) -> None:
    if not UI_DIR.exists():
        raise SystemExit(f"UI not built: run `npm --prefix web run build` (expected {UI_DIR})")
    relaunch_bundled(settings)  # before AppKit loads: the Dock name is fixed at process start
    import webview  # lazy: `crate start --no-browser` must work without pywebview installed
    set_app_name()

    thread, handle = start_server_thread(settings)
    url = wait_for_server(handle)
    inset = sys.platform == "darwin"
    api = WindowApi()
    window = webview.create_window(
        "Krater", url + (INSET_FLAG if inset else ""), js_api=api,
        width=MAIN_SIZE[0], height=MAIN_SIZE[1], min_size=MIN_SIZE,
        # Opaque on purpose: this is what the window shows until `inset_titlebar` turns the background
        # clear on `shown`, and it is what stops a white/black flash before the page paints. Passing
        # pywebview's own transparent=True instead would zero this colour's alpha at creation *and*
        # trip the deprecated WKWebView selector -- see inset_titlebar.
        background_color=DARK_WINDOW if system_is_dark() else LIGHT_WINDOW,
        vibrancy=inset)
    api.attach(window)
    if inset:
        window.events.shown += lambda: inset_titlebar(window)
    window.events.closed += handle.stop
    try:
        webview.start(icon=app_icon())
    finally:
        handle.stop()
        thread.join(timeout=15)
