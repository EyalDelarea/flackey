import asyncio
import sys
import threading
import types
from typing import ClassVar

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
    from flackey import desktop

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
        calls: ClassVar[list] = []

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


class Hook(list):
    def __iadd__(self, fn):
        self.append(fn)
        return self


class FakeWindow:
    def __init__(self, *args, **kwargs):
        self.args, self.kwargs = args, kwargs
        self.events = types.SimpleNamespace(shown=Hook(), loaded=Hook(), closed=Hook())


def _install_fake_webview(monkeypatch, tmp_path):
    """Wires a fake `webview` module into sys.modules and fakes every collaborator of
    `run_in_window` except the function under test. Returns (windows, started, handle):
    `windows["window"]` is the FakeWindow created by `create_window` (once run_in_window has
    run), `started` records the kwargs passed to `webview.start`, and `handle` is the
    ServerHandle the fake `start_server_thread` hands back."""
    from flackey import desktop

    windows: dict[str, FakeWindow] = {}
    started: dict[str, object] = {}

    def create_window(*args, **kwargs):
        window = FakeWindow(*args, **kwargs)
        windows["window"] = window
        return window

    fake_webview = types.SimpleNamespace(
        create_window=create_window, start=lambda **kw: started.update(kw))
    monkeypatch.setitem(sys.modules, "webview", fake_webview)
    monkeypatch.setattr(desktop, "UI_DIR", tmp_path)

    handle = ServerHandle(url="http://localhost:1")
    handle.started.set()
    thread = threading.Thread(target=lambda: None)
    thread.start()
    monkeypatch.setattr(desktop, "start_server_thread", lambda settings: (thread, handle))
    monkeypatch.setattr(desktop, "app_icon", lambda: "/icons/app-icon.png")
    monkeypatch.setattr(desktop, "set_app_name", lambda: True)
    monkeypatch.setattr(desktop, "relaunch_bundled", lambda settings: False)
    return windows, started, handle


def test_run_in_window_hands_the_dock_icon_to_pywebview(monkeypatch, tmp_path):
    from flackey import desktop

    _, started, _ = _install_fake_webview(monkeypatch, tmp_path)
    desktop.run_in_window(settings=object())
    assert started == {"icon": "/icons/app-icon.png"}


def test_run_in_window_opens_the_native_layout(monkeypatch, tmp_path):
    from flackey import desktop

    monkeypatch.setattr(sys, "platform", "darwin")
    windows, _, _ = _install_fake_webview(monkeypatch, tmp_path)
    desktop.run_in_window(settings=object())
    window = windows["window"]
    assert window.args[1].endswith("?titlebar=inset")
    assert window.kwargs["min_size"] == (720, 540)
    # transparent= is deliberately never passed: pywebview's implementation of it calls a deprecated
    # WKWebView selector. inset_titlebar does the same job on `loaded`. See its comment.
    assert "transparent" not in window.kwargs
    assert window.kwargs["vibrancy"] is True
    assert len(window.events.loaded) == 1


def test_run_in_window_plain_window_off_macos(monkeypatch, tmp_path):
    from flackey import desktop

    monkeypatch.setattr(sys, "platform", "linux")
    windows, _, _ = _install_fake_webview(monkeypatch, tmp_path)
    desktop.run_in_window(settings=object())
    window = windows["window"]
    assert "?titlebar=inset" not in window.args[1]
    assert "transparent" not in window.kwargs
    assert window.kwargs["vibrancy"] is False
    assert len(window.events.loaded) == 0


def test_closing_the_window_stops_the_server(monkeypatch, tmp_path):
    from flackey import desktop

    windows, _, handle = _install_fake_webview(monkeypatch, tmp_path)
    desktop.run_in_window(settings=object())
    # assigned only after run_in_window has returned, so its own `finally: handle.stop()`
    # cannot be what flips should_exit below -- only the closed handlers we call can.
    stub_server = types.SimpleNamespace(should_exit=False)
    handle.server = stub_server
    for fn in windows["window"].events.closed:
        fn()
    assert stub_server.should_exit is True


def test_run_in_window_refuses_without_a_ui_build(monkeypatch, tmp_path):
    from flackey import desktop

    _, started, _ = _install_fake_webview(monkeypatch, tmp_path)
    monkeypatch.setattr(desktop, "UI_DIR", tmp_path / "missing")
    with pytest.raises(SystemExit) as excinfo:
        desktop.run_in_window(settings=object())
    assert "npm --prefix web run build" in str(excinfo.value)
    assert started == {}


def _rect(x, y, w, h):
    """Stands in for an NSRect, which answers with `.origin` and `.size`."""
    return types.SimpleNamespace(origin=types.SimpleNamespace(x=x, y=y),
                                 size=types.SimpleNamespace(width=w, height=h))


ZOOM_BUTTON = (53.0, 692.0, 14.0, 14.0)  # the third traffic light, as AppKit lays them out from the left


def _fake_window(calls, width=1100.0, height=720.0):
    """A window shaped like the real one: a WKWebView content view inside a frame view, with the
    traffic lights in the top left."""

    class FakeFrameView:
        def __init__(self):
            self.views = []

        def frame(self):
            return _rect(0.0, 0.0, width, height)

        def subviews(self):
            return list(self.views)

        def addSubview_positioned_relativeTo_(self, view, place, other):
            self.views.append(view)
            calls.append(("addSubview", view.frame, view.mask, place, other))

    frame_view = FakeFrameView()

    class FakeWebView:
        def setValue_forKey_(self, value, key):
            calls.append(("setValue_forKey_", value, key))

        def superview(self):
            return frame_view

    class FakeNative:
        def styleMask(self):
            return 0

        def setStyleMask_(self, v):
            calls.append(("setStyleMask_", v))

        def setTitlebarAppearsTransparent_(self, v):
            calls.append(("setTitlebarAppearsTransparent_", v))

        def setTitleVisibility_(self, v):
            calls.append(("setTitleVisibility_", v))

        def setHasShadow_(self, v):
            calls.append(("setHasShadow_", v))

        def setOpaque_(self, v):
            calls.append(("setOpaque_", v))

        def setBackgroundColor_(self, v):
            calls.append(("setBackgroundColor_", v))

        def contentView(self):
            return FakeWebView()

        def standardWindowButton_(self, which):
            return types.SimpleNamespace(frame=lambda: _rect(*ZOOM_BUTTON))

    return FakeNative(), frame_view


def _appkit_module():
    """The AppKit surface inset_titlebar and add_titlebar_drag_view touch."""
    return types.SimpleNamespace(
        NSColor=types.SimpleNamespace(clearColor=lambda: "clear"),
        NSWindowZoomButton=2, NSWindowAbove=1, NSViewWidthSizable=2, NSViewMinYMargin=32,
        NSMakeRect=lambda x, y, w, h: (x, y, w, h))


def _fake_drag_view(monkeypatch):
    """Stands in for the Objective-C view class, so the test never registers one with the runtime."""
    from flackey import desktop

    class FakeView:
        def __init__(self, frame):
            self.frame, self.mask = frame, None

        def setAutoresizingMask_(self, mask):
            self.mask = mask

    class FakeViewClass:
        @staticmethod
        def alloc():
            return types.SimpleNamespace(initWithFrame_=FakeView)

    monkeypatch.setattr(desktop, "titlebar_drag_view_class", lambda _appkit: FakeViewClass)


def test_titlebar_drag_view_spans_the_title_bar_clear_of_the_traffic_lights(monkeypatch):
    """The strip is what moves the window, so it has to cover the title bar and nothing below it: a
    taller one would swallow clicks meant for the toolbar, and one starting further left would take the
    traffic lights' own hit area with it."""
    from flackey import desktop

    _fake_drag_view(monkeypatch)
    calls = []
    native, frame_view = _fake_window(calls)

    assert desktop.add_titlebar_drag_view(native, _appkit_module()) is True
    added = [c for c in calls if c[0] == "addSubview"]
    assert len(added) == 1
    _, frame, mask, place, _relative = added[0]
    # Unflipped: y is measured from the bottom, so the strip's 28 points sit at the very top.
    assert frame == (53.0 + 14.0 + 12.0, 720.0 - 28.0, 1100.0 - 79.0, 28.0)
    assert mask == 2 | 32  # width follows the window, bottom margin absorbs a height change
    assert place == 1  # above the web view, or the web view keeps every press for itself
    assert len(frame_view.subviews()) == 1


def test_titlebar_drag_view_is_added_only_once(monkeypatch):
    """`loaded` fires again on every navigation, and a stack of strips would each drag the window."""
    from flackey import desktop

    _fake_drag_view(monkeypatch)
    calls = []
    native, frame_view = _fake_window(calls)
    frame_view.views.append(types.SimpleNamespace(className=lambda: "FlackeyTitlebarDragView"))

    assert desktop.add_titlebar_drag_view(native, _appkit_module()) is False
    assert len(frame_view.subviews()) == 1


def test_inset_titlebar_applies_the_native_style(monkeypatch):
    from flackey import desktop

    monkeypatch.setattr(sys, "platform", "darwin")
    calls = []
    native, _ = _fake_window(calls)

    class Window:
        pass

    Window.native = native

    _install_fake_appkit(monkeypatch)

    assert desktop.inset_titlebar(Window()) is True
    assert [c[0] for c in calls] == [
        "setStyleMask_",
        "setTitlebarAppearsTransparent_",
        "setTitleVisibility_",
        "setValue_forKey_",
        "setOpaque_",
        "setBackgroundColor_",
        "setHasShadow_",
        "addSubview",  # the drag strip, or the window cannot be moved at all
    ]
    assert calls[:3] == [("setStyleMask_", 1 << 15), ("setTitlebarAppearsTransparent_", True),
                         ("setTitleVisibility_", 1)]
    assert calls[3:7] == [("setValue_forKey_", False, "drawsBackground"), ("setOpaque_", False),
                          ("setBackgroundColor_", "clear"), ("setHasShadow_", True)]
    # The whole point of doing this ourselves: pywebview reaches transparency through
    # 'drawsTransparentBackground', which is WKWebView's deprecated -_setDrawsTransparentBackground:
    # and logs on every launch. If this key ever comes back, the warning comes back with it.
    assert not [c for c in calls if "drawsTransparentBackground" in repr(c)]


def _fake_venv(monkeypatch, tmp_path):
    """A venv-shaped prefix and a stand-in interpreter for build_bundle."""
    venv = tmp_path / "venv"
    (venv / "lib" / "python3.12" / "site-packages").mkdir(parents=True)
    (venv / "pyvenv.cfg").write_text("home = /nowhere/bin\n")
    base = tmp_path / "python3.12"
    base.write_bytes(b"#!/bin/sh\necho interpreter\n")
    monkeypatch.setattr(sys, "prefix", str(venv))
    monkeypatch.setattr(sys, "_base_executable", str(base), raising=False)
    return venv, base


def test_build_bundle_lays_out_an_app_around_a_copy_of_the_interpreter(monkeypatch, tmp_path):
    from flackey import desktop

    venv, base = _fake_venv(monkeypatch, tmp_path)
    settings = types.SimpleNamespace(data_dir=tmp_path / "data")
    exe = desktop.build_bundle(settings)
    contents = tmp_path / "data" / "Flackey.app" / "Contents"
    assert exe == contents / "MacOS" / "Flackey"
    assert exe.read_bytes() == base.read_bytes()
    assert exe.stat().st_mode & 0o111
    assert "<string>Flackey</string>" in (contents / "Info.plist").read_text()
    assert "<string>app.flackey</string>" in (contents / "Info.plist").read_text()
    assert (contents / "pyvenv.cfg").read_text() == "home = /nowhere/bin\n"
    assert (contents / "lib").resolve() == (venv / "lib").resolve()


def test_build_bundle_refreshes_a_stale_interpreter_copy(monkeypatch, tmp_path):
    from flackey import desktop

    _, base = _fake_venv(monkeypatch, tmp_path)
    settings = types.SimpleNamespace(data_dir=tmp_path / "data")
    exe = desktop.build_bundle(settings)
    base.write_bytes(b"#!/bin/sh\necho newer interpreter\n")
    assert desktop.build_bundle(settings) == exe
    assert exe.read_bytes() == base.read_bytes()


def test_build_bundle_is_none_outside_a_venv(monkeypatch, tmp_path):
    from flackey import desktop

    monkeypatch.setattr(sys, "prefix", str(tmp_path))  # no pyvenv.cfg here
    assert desktop.build_bundle(types.SimpleNamespace(data_dir=tmp_path / "data")) is None


def test_relaunch_bundled_execs_through_the_bundle(monkeypatch, tmp_path):
    from flackey import desktop

    _fake_venv(monkeypatch, tmp_path)
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(sys, "argv", ["flackey", "--env", "x.env", "start"])
    monkeypatch.delenv(desktop.BUNDLED_ENV, raising=False)
    calls = []
    monkeypatch.setattr(desktop.os, "execve", lambda path, argv, env: calls.append((path, argv, env)))
    desktop.relaunch_bundled(types.SimpleNamespace(data_dir=tmp_path / "data"))
    (path, argv, env), = calls
    assert path.endswith("Flackey.app/Contents/MacOS/Flackey")
    assert argv == [path, "-c", "from flackey.cli import app; app()", "--env", "x.env", "start"]
    assert env[desktop.BUNDLED_ENV] == "1"


def test_relaunch_bundled_is_false_when_already_bundled_or_off_macos(monkeypatch, tmp_path):
    from flackey import desktop

    _fake_venv(monkeypatch, tmp_path)
    monkeypatch.setattr(desktop.os, "execve", lambda *a: pytest.fail("must not exec"))
    settings = types.SimpleNamespace(data_dir=tmp_path / "data")
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setenv(desktop.BUNDLED_ENV, "1")
    assert desktop.relaunch_bundled(settings) is False
    monkeypatch.delenv(desktop.BUNDLED_ENV)
    monkeypatch.setattr(sys, "platform", "linux")
    assert desktop.relaunch_bundled(settings) is False


def test_a_packaged_app_does_not_relaunch_itself_through_the_venv_shim(monkeypatch, tmp_path):
    """`build_bundle` exists to give an unbundled interpreter a Dock name, by wrapping the venv it was
    started from. A packaged .app already is a bundle and has no venv, so there is nothing to wrap and
    nothing to fix. Declining explicitly rather than by way of a missing pyvenv.cfg matters because the
    failure mode here is `os.execve` on a path that is not what the caller thinks it is -- a hang with no
    window and no message, in the one build where there is no console to see it happen."""
    from flackey import desktop
    from flackey.config import Settings

    monkeypatch.setattr("sys.frozen", True, raising=False)
    monkeypatch.setattr(desktop.sys, "platform", "darwin")
    monkeypatch.delenv(desktop.BUNDLED_ENV, raising=False)
    monkeypatch.setattr(desktop, "build_bundle", lambda settings: (_ for _ in ()).throw(
        AssertionError("a frozen app must not try to build a venv shim")))

    assert desktop.relaunch_bundled(Settings(data_dir=tmp_path)) is False


def _install_fake_appkit(monkeypatch):
    """The AppKit/PyObjC surface inset_titlebar touches, with callAfter running inline."""
    fake_app_helper = types.SimpleNamespace(callAfter=lambda fn: fn())
    monkeypatch.setitem(sys.modules, "PyObjCTools.AppHelper", fake_app_helper)
    monkeypatch.setitem(sys.modules, "PyObjCTools", types.SimpleNamespace(AppHelper=fake_app_helper))
    monkeypatch.setitem(sys.modules, "AppKit", _appkit_module())
    _fake_drag_view(monkeypatch)


def test_inset_titlebar_clears_the_background_only_after_the_web_view_accepts_it(monkeypatch):
    """Order matters, not just the set of calls. Clearing the window's background is what makes it
    see-through; the web view drawing its own is what fills it back in. Do them the other way round
    and there is a window of time -- and, when the key is refused, forever -- where the window is
    transparent with nothing painting it."""
    from flackey import desktop

    monkeypatch.setattr(sys, "platform", "darwin")
    calls = []

    class FakeWebView:
        def setValue_forKey_(self, value, key):
            calls.append(("setValue_forKey_", value, key))

        def superview(self):
            return types.SimpleNamespace(
                frame=lambda: _rect(0.0, 0.0, 1100.0, 720.0), subviews=list,
                addSubview_positioned_relativeTo_=lambda *a: calls.append(("addSubview",)))

    class FakeNative:
        def styleMask(self):
            return 0

        def __getattr__(self, name):
            return lambda *a: calls.append((name, *a))

        def contentView(self):
            return FakeWebView()

    class Window:
        native = FakeNative()

    _install_fake_appkit(monkeypatch)
    assert desktop.inset_titlebar(Window()) is True
    names = [c[0] for c in calls]
    assert names.index("setValue_forKey_") < names.index("setOpaque_")
    assert ("setValue_forKey_", False, "drawsBackground") in calls
    assert ("setOpaque_", False) in calls
    assert ("setHasShadow_", True) in calls


def test_inset_titlebar_leaves_the_window_opaque_when_the_content_view_is_not_the_web_view(monkeypatch):
    """pywebview only makes the WKWebView the content view once the page has finished loading. Run
    against the plain NSView that stands there until then, 'drawsBackground' raises -- and a window
    that has already been told it is not opaque then has nothing at all to paint it. Better to stay
    opaque and keep the standard title bar than to go see-through."""
    from flackey import desktop

    monkeypatch.setattr(sys, "platform", "darwin")
    calls = []

    class PlainNSView:
        def setValue_forKey_(self, value, key):
            raise KeyError("NSUnknownKeyException - not key value coding-compliant for "
                           f"the key {key}")

    class FakeNative:
        def styleMask(self):
            return 0

        def __getattr__(self, name):
            return lambda *a: calls.append((name, *a))

        def contentView(self):
            return PlainNSView()

    class Window:
        native = FakeNative()

    _install_fake_appkit(monkeypatch)
    desktop.inset_titlebar(Window())
    assert ("setOpaque_", False) not in calls
    assert ("setBackgroundColor_", "clear") not in calls


def test_run_in_window_insets_the_titlebar_once_the_page_is_loaded(monkeypatch, tmp_path):
    """`shown` fires while the content view is still a placeholder NSView; `loaded` fires from
    pywebview's didFinishNavigation handler, which is where the WKWebView is installed."""
    from flackey import desktop

    monkeypatch.setattr(sys, "platform", "darwin")
    windows, _, _ = _install_fake_webview(monkeypatch, tmp_path)
    desktop.run_in_window(settings=object())
    window = windows["window"]
    assert len(window.events.loaded) == 1
    assert len(window.events.shown) == 0
