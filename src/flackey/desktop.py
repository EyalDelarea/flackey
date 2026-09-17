"""The desktop window. `flackey start` runs the server on a background thread and shows the UI in a
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
from .config import Settings, save_settings

log = logging.getLogger(__name__)
MAIN_SIZE = (1100, 720)  # a first launch only: after that the window opens where `window_size` left it
MIN_SIZE = (560, 420)    # the floor the narrow layout at the end of web/src/app.css is written for
LIGHT_WINDOW = "#ECECEC"  # keep in sync with --window in web/src/theme.css
DARK_WINDOW = "#1E1E1E"
INSET_FLAG = "?titlebar=inset"
TITLEBAR_H = 28.0  # keep in sync with .titlebar-spacer in web/src/app.css
_TRAFFIC_LIGHT_GAP = 12.0  # air between the zoom button and where the drag strip starts
_NS_FULL_SIZE_CONTENT_VIEW = 1 << 15  # NSWindowStyleMaskFullSizeContentView
_NS_WINDOW_TITLE_HIDDEN = 1           # NSWindowTitleHidden

APP_NAME = "Flackey"
APP_ICON = Path(__file__).with_name("assets") / "app-icon.png"
BUNDLE_ID = "app.flackey"
BUNDLED_ENV = "FLACKEY_BUNDLED"  # set on the re-exec'd process so it does not bundle itself again
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
    `Flackey.app` in the data dir around a copy of the interpreter: `Contents/pyvenv.cfg` (copied
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
    """Exec this process again through the app bundle so the Dock and menu bar say Flackey. Never
    returns on success (the process is replaced). False off macOS, when already running bundled, or when
    the bundle cannot be built."""
    # `sys.frozen` is the packaged .app, which already is a bundle: there is no venv to wrap and the
    # Dock name is its own. Left to reach `build_bundle` it would decline only because a frozen app has
    # no pyvenv.cfg -- true today, and an accident to rely on when the cost of it changing is `execve`
    # onto the wrong path, which in a windowed build is a hang with nothing on screen to explain it.
    if sys.platform != "darwin" or os.environ.get(BUNDLED_ENV) or getattr(sys, "frozen", False):
        return False
    exe = build_bundle(settings)
    if exe is None:
        return False
    env = {**os.environ, BUNDLED_ENV: "1"}
    os.execve(str(exe), [str(exe), "-c", "from flackey.cli import app; app()", *sys.argv[1:]], env)
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
    except Exception:
        log.debug("could not set the app name", exc_info=True)
        return False
    return True


def system_is_dark() -> bool:
    """macOS stores the appearance in `AppleInterfaceStyle`; the key is absent in light mode."""
    if sys.platform != "darwin":
        return False
    try:
        out = subprocess.run(["defaults", "read", "-g", "AppleInterfaceStyle"],
                             capture_output=True, text=True, timeout=2, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return out.returncode == 0 and out.stdout.strip() == "Dark"


# There is deliberately no `js_api`. The one method the page ever had through the bridge was `resize`,
# and the Welcome and setup screens calling it is what made the window snap to a size of its own choosing
# mid-session, throwing away whatever the owner had dragged out. How big the window is, is the owner's
# answer -- `startup_size` remembers it and AppKit's own resize handles the rest -- so the bridge is gone
# rather than left in place unused, since the page cannot misuse what it is not given.


def startup_size(settings: Settings) -> tuple[int, int]:
    """How big to open the window: the size the owner last closed it at, or `MAIN_SIZE` if they never
    resized it. Clamped up to `MIN_SIZE`, because the minimum has come down before and a size saved under
    an older, larger one would otherwise open a window smaller than the layout has rules for. Anything
    unreadable in the file -- a hand-edited string, a single number -- is treated as never having been
    set, since a window that will not open is a worse answer than a window of the wrong size."""
    saved = getattr(settings, "window_size", None) or ()
    try:
        width, height = (int(saved[0]), int(saved[1])) if len(saved) == 2 else MAIN_SIZE
    except (TypeError, ValueError):
        log.warning("ignoring an unreadable saved window size %r", saved)
        return MAIN_SIZE
    return max(width, MIN_SIZE[0]), max(height, MIN_SIZE[1])


def remember_window_size(settings: Settings, size: tuple[int, int]) -> bool:
    """Write the size back so the next launch opens there. Best effort and never raised: this runs from
    the window's `closed` handler, where the only thing left to do is stop the server, and a data dir that
    has gone read-only must not turn quitting the app into a traceback."""
    try:
        save_settings(settings, window_size=[int(size[0]), int(size[1])])
    except Exception:
        log.warning("could not save the window size", exc_info=True)
        return False
    return True


def start_server_thread(settings: Settings) -> tuple[threading.Thread, ServerHandle]:
    handle = ServerHandle()

    def target() -> None:
        try:
            asyncio.run(run(settings, open_browser=False, handle=handle))
        except BaseException as e:
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


_drag_view_class = None
_DRAG_VIEW_NAME = "FlackeyTitlebarDragView"


def titlebar_drag_view_class(AppKit):  # the module, passed in so this file still imports off macOS
    """The view that moves the window. Registered with the Objective-C runtime the first time it is
    asked for and reused after that, because a second class of the same name is an error, and defined in
    here rather than at module scope because AppKit does not import off macOS."""
    global _drag_view_class
    if _drag_view_class is None:
        class FlackeyTitlebarDragView(AppKit.NSView):
            def mouseDown_(self, event):  # an Objective-C selector, hence the name
                self.window().performWindowDragWithEvent_(event)

            def acceptsFirstMouse_(self, event):
                # Otherwise the first click on a background window only raises it, and the owner has to
                # click twice to start moving a window they can see but have not focused.
                return True

        _drag_view_class = FlackeyTitlebarDragView
    return _drag_view_class


def add_titlebar_drag_view(native, AppKit) -> bool:
    """Give the window back its title bar as a drag handle.

    With `NSWindowStyleMaskFullSizeContentView` the WKWebView is the content view and covers the title
    bar, and it answers `mouseDownCanMoveWindow` with NO, so neither the title bar nor
    `movableByWindowBackground` moves the window any more: a press anywhere lands in the web view. The
    page used to paper over that by marking strips as pywebview drag regions, and that is what threw the
    window across the screen -- pywebview answers such a drag by posting an absolute screen position back
    to Python, where the Cocoa backend re-adds the origin of an `NSScreen.mainScreen()` frame snapshotted
    at window creation. On one display at (0, 0) the addition is a no-op; with a second display attached
    the window teleports by that screen's origin on the first pixel of movement.

    So the drag goes back to AppKit: a transparent view across the title bar whose `mouseDown:` hands the
    event to `performWindowDragWithEvent:`, which is the real thing -- snapping, spaces, double-click to
    zoom, and coordinates AppKit works out for itself. It starts to the right of the zoom button so the
    traffic lights keep their own hit area, and it is the height of the title bar and no more, so every
    control the page draws below it still receives its clicks.

    Returns False when the window has no frame view to hang it off, or when it already has one."""
    frame_view = native.contentView().superview() if native.contentView() is not None else None
    if frame_view is None:
        return False
    if any(v.className() == _DRAG_VIEW_NAME for v in frame_view.subviews()):
        return False  # `loaded` fires again on every navigation; one strip is enough
    zoom = native.standardWindowButton_(AppKit.NSWindowZoomButton)
    left = (zoom.frame().origin.x + zoom.frame().size.width + _TRAFFIC_LIGHT_GAP) if zoom is not None else 78.0
    size = frame_view.frame().size
    strip = titlebar_drag_view_class(AppKit).alloc().initWithFrame_(
        AppKit.NSMakeRect(left, size.height - TITLEBAR_H, max(size.width - left, 0.0), TITLEBAR_H))
    # Unflipped coordinates: pinned to the top of the window and stretched with it as it is resized.
    strip.setAutoresizingMask_(AppKit.NSViewWidthSizable | AppKit.NSViewMinYMargin)
    frame_view.addSubview_positioned_relativeTo_(strip, AppKit.NSWindowAbove, None)
    return True


_VIBRANCY_VIEW_NAME = "NSVisualEffectView"


def stretch_web_view(native, AppKit) -> bool:
    """Make the web view, and the blur behind it, follow the window frame.

    Two things have to be true for the window to redraw cleanly when it is resized, and pywebview leaves
    neither of them true here. It builds the WKWebView with `initWithFrame:` and never gives it an
    autoresizing mask, and it hangs its `NSVisualEffectView` off the web view as a child rather than
    beside it, so the blur inherits whatever frame the web view happens to be holding.

    In an opaque window that is survivable: AppKit erases the background to the window colour, so a view
    that arrives at its new frame a moment late costs a flicker. This window is not opaque -- the call
    above clears its background so the vibrancy can show through -- and a non-opaque window never erases.
    Every point no view currently covers keeps the pixels that were last drawn there, for as long as
    nothing else draws over them. A web view whose frame lags the window therefore does not merely draw
    in the wrong place; it leaves the previous drawing stranded beside it, which is the dead strip with a
    stale "Connected" pill still painted in it that the bug report shows.

    So pin the web view to all four edges and set its frame from the frame view now -- the style-mask
    change just above moves the content rect up by the height of the title bar, and the web view is not
    told -- then lift the vibrancy view out from under it to sit as its sibling, below it, stretched the
    same way. Sibling rather than child because a blur that is a child of the view it exists to back
    cannot cover for that view turning up late, which is the whole failure being fixed.

    Returns False when there is no frame view to measure against (the content view is not installed yet)."""
    content = native.contentView()
    frame_view = content.superview() if content is not None else None
    if frame_view is None:
        return False
    bounds = frame_view.bounds()
    both = AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable
    content.setAutoresizingMask_(both)
    content.setFrame_(bounds)
    for view in list(content.subviews()):
        if view.className() != _VIBRANCY_VIEW_NAME:
            continue
        view.setFrame_(bounds)
        view.setAutoresizingMask_(both)
        # No `removeFromSuperview` first: adding a view to a new superview takes it out of its old one,
        # and doing it by hand would leave the view with no owner but this loop variable across the two
        # calls in between -- which is a released view in PyObjC, and a window that has stopped blurring.
        frame_view.addSubview_positioned_relativeTo_(view, AppKit.NSWindowBelow, content)
    return True


def inset_titlebar(window) -> bool:
    """Traffic lights over the sidebar: transparent title bar, hidden title, content under the title bar,
    and the see-through background the vibrancy layer shows through. The AppKit calls are queued on the
    main thread. False when there is no native handle (not macOS).

    This is also what makes the window draggable -- see `add_titlebar_drag_view`.

    Call this on `loaded`, not `shown`. pywebview installs the WKWebView as the window's content view
    from its own didFinishNavigation handler, roughly 70ms after `shown` fires; until then the content
    view is a plain NSView that does not answer 'drawsBackground' at all."""
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
        # What pywebview's own `transparent=True` would do, minus its deprecated selector: it sets the
        # KVC key 'drawsTransparentBackground', which resolves to WKWebView's deprecated
        # -_setDrawsTransparentBackground: and makes AppKit log on every launch. 'drawsBackground' = NO
        # is the same effect through a selector AppKit does not complain about. Doing it here instead of
        # at window creation also means the window opens on the opaque `background_color` and only then
        # goes clear, so there is no white flash before the page paints.
        #
        # The web view first, the window second, and the window only if the web view agreed. Clearing
        # the window's background is what makes it see-through; the web view drawing the page is what
        # fills it back in. Done the other way round -- as this did until the content view turned out
        # not to be the WKWebView yet -- a refused key leaves a transparent window with nothing
        # painting it, which is a window you can see the desktop through.
        content = native.contentView()
        try:
            content.setValue_forKey_(False, "drawsBackground")
        except Exception:
            log.warning("the window's content view would not take 'drawsBackground'; leaving the "
                        "window opaque with its standard title bar", exc_info=True)
            return
        native.setOpaque_(False)
        native.setBackgroundColor_(AppKit.NSColor.clearColor())
        native.setHasShadow_(True)  # a non-opaque window loses its shadow, and a shadowless window is not native
        # After the style mask, not before: the content rect it stretches the web view to is the one
        # `NSWindowStyleMaskFullSizeContentView` has just redefined.
        stretch_web_view(native, AppKit)
        add_titlebar_drag_view(native, AppKit)

    AppHelper.callAfter(apply)
    return True


def run_in_window(settings: Settings) -> None:
    if not UI_DIR.exists():
        raise SystemExit(f"UI not built: run `npm --prefix web run build` (expected {UI_DIR})")
    relaunch_bundled(settings)  # before AppKit loads: the Dock name is fixed at process start
    import webview  # lazy: `flackey start --no-browser` must work without pywebview installed
    set_app_name()

    thread, handle = start_server_thread(settings)
    url = wait_for_server(handle)
    inset = sys.platform == "darwin"
    size = startup_size(settings)
    window = webview.create_window(
        "Flackey", url + (INSET_FLAG if inset else ""),
        width=size[0], height=size[1], min_size=MIN_SIZE,
        # Opaque on purpose: this is what the window shows until `inset_titlebar` turns the background
        # clear on `shown`, and it is what stops a white/black flash before the page paints. Passing
        # pywebview's own transparent=True instead would zero this colour's alpha at creation *and*
        # trip the deprecated WKWebView selector -- see inset_titlebar.
        background_color=DARK_WINDOW if system_is_dark() else LIGHT_WINDOW,
        vibrancy=inset)
    # Tracked as it changes rather than read back on `closed`, because by then pywebview has already let
    # go of the native window and there is no frame left to measure. pywebview reports the frame size,
    # which is the same number `create_window` was given, so what is saved is what reopens.
    last = list(size)

    def remember(width, height) -> None:
        last[:] = [int(width), int(height)]

    window.events.resized += remember
    if inset:
        # `loaded`, not `shown`: see inset_titlebar. `shown` fires while the content view is still
        # pywebview's placeholder NSView, and the call that makes the web view transparent is refused.
        window.events.loaded += lambda: inset_titlebar(window)
    window.events.closed += lambda: remember_window_size(settings, (last[0], last[1]))
    window.events.closed += handle.stop
    try:
        webview.start(icon=app_icon())
    finally:
        handle.stop()
        thread.join(timeout=15)
