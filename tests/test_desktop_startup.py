"""Startup failures and second copies (audit SHL-1, SHL-2, SHL-11).

No real window, no real server and no real dialog: the window, the server thread and the message box
are all fakes. The data-folder lock is real, held by a second Python process where a test needs
"another copy" to own it.
"""
from __future__ import annotations

import socket
import subprocess
import sys
import textwrap
import threading
import types
from pathlib import Path

import pytest

from flackey import single_instance, startup_alert
from flackey.app import ServerHandle


@pytest.fixture(autouse=True)
def _release_locks(tmp_path):
    yield
    for path in list(single_instance._dir_locks):
        single_instance.release_data_dir(Path(path))


def _hold_lock_elsewhere(data_dir: Path) -> subprocess.Popen:
    """Another process that claims `data_dir` and keeps it until its stdin closes."""
    code = textwrap.dedent(f"""
        import sys
        from pathlib import Path
        from flackey.single_instance import claim_data_dir
        assert claim_data_dir(Path({str(data_dir)!r}))
        print("held", flush=True)
        sys.stdin.read()
    """)
    proc = subprocess.Popen([sys.executable, "-c", code], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    assert proc.stdout.readline().strip() == "held"
    return proc


def _release(proc: subprocess.Popen) -> None:
    proc.stdin.close()
    proc.wait(timeout=10)


# --- the data-folder lock -----------------------------------------------------------------------------

def test_the_first_claim_on_a_data_folder_wins_and_a_second_process_is_refused(tmp_path):
    other = _hold_lock_elsewhere(tmp_path)
    try:
        assert single_instance.claim_data_dir(tmp_path) is False
    finally:
        _release(other)
    # The kernel let go when the holder exited: no stale lock to clean up.
    assert single_instance.claim_data_dir(tmp_path) is True


def test_claiming_again_from_the_same_process_is_not_a_refusal(tmp_path):
    assert single_instance.claim_data_dir(tmp_path) is True
    assert single_instance.claim_data_dir(tmp_path) is True


@pytest.mark.skipif(sys.platform == "win32", reason="the pid is only written off Windows")
def test_the_holder_leaves_its_pid_for_a_second_copy_to_bring_forward(tmp_path):
    import os

    assert single_instance.claim_data_dir(tmp_path)
    assert single_instance.holder_pid(tmp_path) == os.getpid()


def test_a_folder_the_lock_cannot_be_made_in_does_not_stop_the_app(tmp_path, monkeypatch):
    """Starting is better than refusing to start."""
    def no_open(*a, **kw):
        raise PermissionError("read-only")

    monkeypatch.setattr("builtins.open", no_open)
    assert single_instance.claim_data_dir(tmp_path / "ro") is True


def test_hand_over_brings_the_lock_holder_forward_on_macos(monkeypatch, tmp_path):
    activated = []

    class App:
        def activateWithOptions_(self, options):
            activated.append(options)
            return True

    fake_appkit = types.SimpleNamespace(NSRunningApplication=types.SimpleNamespace(
        runningApplicationWithProcessIdentifier_=lambda pid: App() if pid == 4321 else None))
    monkeypatch.setitem(sys.modules, "AppKit", fake_appkit)
    monkeypatch.setattr(single_instance.sys, "platform", "darwin")
    (tmp_path / single_instance.LOCK_FILE_NAME).write_text("4321")

    assert single_instance.hand_over(tmp_path) is True
    assert activated


# --- run_in_window ------------------------------------------------------------------------------------

def _fake_window_env(monkeypatch, tmp_path, *, handle: ServerHandle):
    """Every collaborator of `run_in_window` faked, the server thread answering with `handle`. Returns
    a dict that records what happened."""
    from flackey import desktop

    seen: dict[str, object] = {"windows": [], "guarded": False, "alerts": []}
    fake_webview = types.SimpleNamespace(
        create_window=lambda *a, **kw: seen["windows"].append(kw) or types.SimpleNamespace(
            events=types.SimpleNamespace(**{n: _Hook() for n in ("shown", "loaded", "closing", "closed",
                                                                  "resized")}), destroy=lambda: None),
        start=lambda **kw: None, FileDialog=types.SimpleNamespace(FOLDER=20))
    monkeypatch.setitem(sys.modules, "webview", fake_webview)
    monkeypatch.setattr(desktop, "UI_DIR", tmp_path)
    monkeypatch.setattr(desktop, "claim_single_instance", lambda: True)
    monkeypatch.setattr(desktop, "webview2_version", lambda: "1")
    monkeypatch.setattr(desktop, "relaunch_bundled", lambda s: False)
    monkeypatch.setattr(desktop, "set_app_name", lambda: True)
    monkeypatch.setattr(desktop, "screen_size", lambda: (6000, 4000))
    monkeypatch.setattr(desktop, "window_origin", lambda size: None)
    monkeypatch.setattr(desktop, "stop_slskd", lambda s: None)
    monkeypatch.setattr(desktop, "guard_slskd", lambda s: seen.__setitem__("guarded", True))
    monkeypatch.setattr(desktop, "show_alert", lambda headline, detail: seen["alerts"].append((headline, detail)))
    thread = threading.Thread(target=lambda: None)
    thread.start()

    def start_server_thread(settings):
        seen["server"] = True
        return thread, handle

    monkeypatch.setattr(desktop, "start_server_thread", start_server_thread)
    return seen


class _Hook(list):
    def __iadd__(self, fn):
        self.append(fn)
        return self


def _settings(tmp_path, port=8765):
    return types.SimpleNamespace(data_dir=tmp_path, window_size=None, web_host="127.0.0.1", web_port=port)


def _failed(error: BaseException | None) -> ServerHandle:
    handle = ServerHandle()
    handle.error = error
    handle.started.set()
    return handle


def test_a_taken_port_is_explained_instead_of_closing_silently(monkeypatch, tmp_path):
    """uvicorn answers a taken port with sys.exit, which reaches the window as a CancelledError -- a
    BaseException that used to slip past every handler and close the app with no word."""
    import asyncio

    from flackey import desktop

    busy = socket.socket()
    busy.bind(("127.0.0.1", 0))
    busy.listen()
    port = busy.getsockname()[1]
    try:
        seen = _fake_window_env(monkeypatch, tmp_path, handle=_failed(asyncio.CancelledError()))
        desktop.run_in_window(_settings(tmp_path, port))
    finally:
        busy.close()

    assert seen["windows"] == []
    [(headline, detail)] = seen["alerts"]
    assert "port" in headline and str(port) in detail
    assert "flackey.log" in detail


def test_any_other_startup_failure_points_at_the_log(monkeypatch, tmp_path):
    from flackey import desktop

    seen = _fake_window_env(monkeypatch, tmp_path, handle=_failed(RuntimeError("database is locked")))
    monkeypatch.setattr(desktop, "port_in_use", lambda host, port: False)
    desktop.run_in_window(_settings(tmp_path))

    [(headline, detail)] = seen["alerts"]
    assert "problem while starting" in headline
    assert str(tmp_path / "flackey.log") in detail


def test_a_start_that_never_answers_is_told_as_too_slow(monkeypatch, tmp_path):
    from flackey import desktop

    seen = _fake_window_env(monkeypatch, tmp_path, handle=ServerHandle())  # never started, no error
    monkeypatch.setattr(desktop, "port_in_use", lambda host, port: False)
    real_wait = desktop.wait_for_server
    monkeypatch.setattr(desktop, "wait_for_server", lambda handle: real_wait(handle, timeout_s=0.01))
    desktop.run_in_window(_settings(tmp_path))

    [(headline, _)] = seen["alerts"]
    assert "too long" in headline


def test_the_window_waits_long_enough_for_a_slow_prelude():
    """SHL-11: the thread signals the moment it listens or fails, so the outer wait only bounds a hang,
    and it must cover the prelude plus `serve`'s own 30 s rather than give up at 35."""
    import inspect

    from flackey import app, desktop

    default = inspect.signature(desktop.wait_for_server).parameters["timeout_s"].default
    assert default >= app.WEB_SERVER_START_TIMEOUT_S + 60


def test_a_second_copy_on_the_same_data_folder_never_reaches_the_sidecar_guard(monkeypatch, tmp_path):
    """SHL-2: the second copy's exit guard used to stop the first copy's slskd. It now hands over
    before the guard is installed or a server started."""
    from flackey import desktop

    seen = _fake_window_env(monkeypatch, tmp_path, handle=_failed(None))
    handed = []
    monkeypatch.setattr(desktop, "hand_over", lambda d: handed.append(d) or True)
    other = _hold_lock_elsewhere(tmp_path)
    try:
        desktop.run_in_window(_settings(tmp_path))
    finally:
        _release(other)

    assert handed == [tmp_path]
    assert seen["guarded"] is False and "server" not in seen and seen["alerts"] == []


async def test_the_server_refuses_a_data_folder_another_copy_holds(tmp_path):
    """`flackey start` in a terminal beside the app: refused before it installs its exit guard."""
    from flackey.app import _run
    from flackey.config import Settings
    from flackey.single_instance import DataDirInUse

    settings = Settings(_env_file=None, telegram_api_id=1, telegram_api_hash="h",
                        library_root=tmp_path / "lib", data_dir=tmp_path / "data")
    other = _hold_lock_elsewhere(settings.data_dir)
    try:
        with pytest.raises(DataDirInUse):
            await _run(settings, ServerHandle())
    finally:
        _release(other)


# --- the alert itself ---------------------------------------------------------------------------------

def test_port_in_use_sees_a_listening_socket_and_not_a_free_port():
    busy = socket.socket()
    busy.bind(("127.0.0.1", 0))
    busy.listen()
    try:
        assert startup_alert.port_in_use("127.0.0.1", busy.getsockname()[1]) is True
    finally:
        busy.close()
    free = socket.socket()
    free.bind(("127.0.0.1", 0))
    port = free.getsockname()[1]
    free.close()
    assert startup_alert.port_in_use("127.0.0.1", port) is False


def test_the_mac_alert_passes_its_text_as_arguments_not_inside_the_script(monkeypatch):
    calls = []
    monkeypatch.setattr(startup_alert.sys, "platform", "darwin")
    monkeypatch.setattr(startup_alert.subprocess, "run",
                        lambda argv, **kw: calls.append(argv) or types.SimpleNamespace(returncode=0))
    tricky = 'log at /Users/a"b/flackey.log'
    assert startup_alert.show_alert("Headline", tricky) is True
    [argv] = calls
    assert argv[0] == "osascript" and argv[-2:] == ["Headline", tricky]
    assert tricky not in argv[2]


def test_the_windows_alert_is_a_message_box(monkeypatch):
    boxes = []
    fake_ctypes = types.SimpleNamespace(windll=types.SimpleNamespace(user32=types.SimpleNamespace(
        MessageBoxW=lambda hwnd, text, title, flags: boxes.append((text, title)) or 1)))
    monkeypatch.setitem(sys.modules, "ctypes", fake_ctypes)
    monkeypatch.setattr(startup_alert.sys, "platform", "win32")
    assert startup_alert.show_alert("Headline", "Detail") is True
    assert boxes == [("Headline\n\nDetail", "Flackey")]


def test_an_alert_that_cannot_be_shown_never_raises(monkeypatch):
    def boom(*a, **kw):
        raise OSError("no osascript")

    monkeypatch.setattr(startup_alert.sys, "platform", "darwin")
    monkeypatch.setattr(startup_alert.subprocess, "run", boom)
    assert startup_alert.show_alert("Headline", "Detail") is False


# --- the packaged entry point -------------------------------------------------------------------------

def _launch_module():
    import importlib.util

    path = Path(__file__).resolve().parents[1] / "packaging" / "launch.py"
    spec = importlib.util.spec_from_file_location("flackey_launch_startup_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_an_unexpected_startup_error_in_the_packaged_app_is_shown_and_logged(monkeypatch, tmp_path):
    launch = _launch_module()
    monkeypatch.setattr(launch, "load_settings", lambda: _settings(tmp_path))
    monkeypatch.setattr(launch, "configure_logging", lambda d: None)
    import flackey.desktop

    def broken(settings):
        raise SystemExit("UI not built")

    monkeypatch.setattr(flackey.desktop, "run_in_window", broken)
    shown = []
    monkeypatch.setattr(startup_alert, "show_alert", lambda h, d: shown.append((h, d)))

    with pytest.raises(SystemExit):
        launch.main()
    [(headline, detail)] = shown
    assert "problem while starting" in headline and "flackey.log" in detail


def test_the_packaged_app_hands_over_when_another_copy_holds_the_data_folder(monkeypatch, tmp_path):
    launch = _launch_module()
    monkeypatch.setattr(launch, "load_settings", lambda: _settings(tmp_path))
    monkeypatch.setattr(launch, "configure_logging", lambda d: None)
    import flackey.desktop

    monkeypatch.setattr(flackey.desktop, "run_in_window",
                        lambda s: (_ for _ in ()).throw(AssertionError("must not start")))
    handed = []
    monkeypatch.setattr(single_instance, "hand_over", lambda d: handed.append(d) or False)
    other = _hold_lock_elsewhere(tmp_path)
    try:
        launch.main()
    finally:
        _release(other)
    assert handed == [tmp_path]
