import asyncio
import sys
import threading
import types

import pytest

from krater.app import ServerHandle


def test_wait_for_server_returns_the_url():
    from krater.desktop import wait_for_server

    h = ServerHandle(url="http://localhost:8765")
    h.started.set()
    assert wait_for_server(h, timeout_s=0.1) == "http://localhost:8765"


def test_wait_for_server_raises_the_startup_error():
    from krater.desktop import wait_for_server

    h = ServerHandle(error=OSError("port taken"))
    h.started.set()
    with pytest.raises(OSError):
        wait_for_server(h, timeout_s=0.1)


def test_wait_for_server_times_out_as_a_runtime_error():
    from krater.desktop import wait_for_server

    with pytest.raises(RuntimeError):
        wait_for_server(ServerHandle(), timeout_s=0.05)


def test_server_thread_fills_the_handle_and_reports_a_crash(monkeypatch):
    import krater.desktop as desktop

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
    from krater.desktop import WindowApi

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
    from krater.desktop import inset_titlebar

    class NoNative:
        native = None

    assert inset_titlebar(NoNative()) is False


def test_system_is_dark_is_false_off_macos(monkeypatch):
    from krater import desktop

    monkeypatch.setattr(sys, "platform", "linux")
    assert desktop.system_is_dark() is False


def test_app_icon_asset_is_a_1024_square_png():
    from krater.desktop import APP_ICON

    data = APP_ICON.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    width, height = int.from_bytes(data[16:20], "big"), int.from_bytes(data[20:24], "big")
    assert (width, height) == (1024, 1024)


def test_app_icon_is_none_when_the_asset_is_missing(monkeypatch, tmp_path):
    from krater import desktop

    monkeypatch.setattr(desktop, "APP_ICON", tmp_path / "missing.png")
    assert desktop.app_icon() is None


def test_set_app_name_is_false_off_macos(monkeypatch):
    from krater import desktop

    monkeypatch.setattr(sys, "platform", "linux")
    assert desktop.set_app_name() is False


class Hook(list):
    def __iadd__(self, fn):
        self.append(fn)
        return self


class FakeWindow:
    def __init__(self, *args, **kwargs):
        self.args, self.kwargs = args, kwargs
        self.events = types.SimpleNamespace(shown=Hook(), closed=Hook())


def _install_fake_webview(monkeypatch, tmp_path):
    """Wires a fake `webview` module into sys.modules and fakes every collaborator of
    `run_in_window` except the function under test. Returns (windows, started, handle):
    `windows["window"]` is the FakeWindow created by `create_window` (once run_in_window has
    run), `started` records the kwargs passed to `webview.start`, and `handle` is the
    ServerHandle the fake `start_server_thread` hands back."""
    from krater import desktop

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
    from krater import desktop

    _, started, _ = _install_fake_webview(monkeypatch, tmp_path)
    desktop.run_in_window(settings=object())
    assert started == {"icon": "/icons/app-icon.png"}


def test_run_in_window_opens_the_native_layout(monkeypatch, tmp_path):
    from krater import desktop

    monkeypatch.setattr(sys, "platform", "darwin")
    windows, _, _ = _install_fake_webview(monkeypatch, tmp_path)
    desktop.run_in_window(settings=object())
    window = windows["window"]
    assert window.args[1].endswith("?titlebar=inset")
    assert window.kwargs["min_size"] == (720, 540)
    # transparent= is deliberately never passed: pywebview's implementation of it calls a deprecated
    # WKWebView selector. inset_titlebar does the same job on `shown`. See its comment.
    assert "transparent" not in window.kwargs
    assert window.kwargs["vibrancy"] is True
    assert len(window.events.shown) == 1


def test_run_in_window_plain_window_off_macos(monkeypatch, tmp_path):
    from krater import desktop

    monkeypatch.setattr(sys, "platform", "linux")
    windows, _, _ = _install_fake_webview(monkeypatch, tmp_path)
    desktop.run_in_window(settings=object())
    window = windows["window"]
    assert "?titlebar=inset" not in window.args[1]
    assert "transparent" not in window.kwargs
    assert window.kwargs["vibrancy"] is False
    assert len(window.events.shown) == 0


def test_closing_the_window_stops_the_server(monkeypatch, tmp_path):
    from krater import desktop

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
    from krater import desktop

    _, started, _ = _install_fake_webview(monkeypatch, tmp_path)
    monkeypatch.setattr(desktop, "UI_DIR", tmp_path / "missing")
    with pytest.raises(SystemExit) as excinfo:
        desktop.run_in_window(settings=object())
    assert "npm --prefix web run build" in str(excinfo.value)
    assert started == {}


def test_inset_titlebar_applies_the_native_style(monkeypatch):
    from krater import desktop

    monkeypatch.setattr(sys, "platform", "darwin")
    calls = []

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

    class FakeWebView:
        def setValue_forKey_(self, value, key):
            calls.append(("setValue_forKey_", value, key))

    class Window:
        native = FakeNative()

    fake_app_helper = types.SimpleNamespace(callAfter=lambda fn: fn())
    monkeypatch.setitem(sys.modules, "PyObjCTools.AppHelper", fake_app_helper)
    monkeypatch.setitem(sys.modules, "PyObjCTools", types.SimpleNamespace(AppHelper=fake_app_helper))
    monkeypatch.setitem(sys.modules, "AppKit",
                        types.SimpleNamespace(NSColor=types.SimpleNamespace(clearColor=lambda: "clear")))

    assert desktop.inset_titlebar(Window()) is True
    assert calls == [
        ("setStyleMask_", 1 << 15),
        ("setTitlebarAppearsTransparent_", True),
        ("setTitleVisibility_", 1),
        ("setOpaque_", False),
        ("setBackgroundColor_", "clear"),
        ("setValue_forKey_", False, "drawsBackground"),
        ("setHasShadow_", True),
    ]
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
    from krater import desktop

    venv, base = _fake_venv(monkeypatch, tmp_path)
    settings = types.SimpleNamespace(data_dir=tmp_path / "data")
    exe = desktop.build_bundle(settings)
    contents = tmp_path / "data" / "Krater.app" / "Contents"
    assert exe == contents / "MacOS" / "Krater"
    assert exe.read_bytes() == base.read_bytes()
    assert exe.stat().st_mode & 0o111
    assert "<string>Krater</string>" in (contents / "Info.plist").read_text()
    assert "<string>app.krater</string>" in (contents / "Info.plist").read_text()
    assert (contents / "pyvenv.cfg").read_text() == "home = /nowhere/bin\n"
    assert (contents / "lib").resolve() == (venv / "lib").resolve()


def test_build_bundle_refreshes_a_stale_interpreter_copy(monkeypatch, tmp_path):
    from krater import desktop

    _, base = _fake_venv(monkeypatch, tmp_path)
    settings = types.SimpleNamespace(data_dir=tmp_path / "data")
    exe = desktop.build_bundle(settings)
    base.write_bytes(b"#!/bin/sh\necho newer interpreter\n")
    assert desktop.build_bundle(settings) == exe
    assert exe.read_bytes() == base.read_bytes()


def test_build_bundle_is_none_outside_a_venv(monkeypatch, tmp_path):
    from krater import desktop

    monkeypatch.setattr(sys, "prefix", str(tmp_path))  # no pyvenv.cfg here
    assert desktop.build_bundle(types.SimpleNamespace(data_dir=tmp_path / "data")) is None


def test_relaunch_bundled_execs_through_the_bundle(monkeypatch, tmp_path):
    from krater import desktop

    _fake_venv(monkeypatch, tmp_path)
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(sys, "argv", ["crate", "--env", "x.env", "start"])
    monkeypatch.delenv(desktop.BUNDLED_ENV, raising=False)
    calls = []
    monkeypatch.setattr(desktop.os, "execve", lambda path, argv, env: calls.append((path, argv, env)))
    desktop.relaunch_bundled(types.SimpleNamespace(data_dir=tmp_path / "data"))
    (path, argv, env), = calls
    assert path.endswith("Krater.app/Contents/MacOS/Krater")
    assert argv == [path, "-c", "from krater.cli import app; app()", "--env", "x.env", "start"]
    assert env[desktop.BUNDLED_ENV] == "1"


def test_relaunch_bundled_is_false_when_already_bundled_or_off_macos(monkeypatch, tmp_path):
    from krater import desktop

    _fake_venv(monkeypatch, tmp_path)
    monkeypatch.setattr(desktop.os, "execve", lambda *a: pytest.fail("must not exec"))
    settings = types.SimpleNamespace(data_dir=tmp_path / "data")
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setenv(desktop.BUNDLED_ENV, "1")
    assert desktop.relaunch_bundled(settings) is False
    monkeypatch.delenv(desktop.BUNDLED_ENV)
    monkeypatch.setattr(sys, "platform", "linux")
    assert desktop.relaunch_bundled(settings) is False
