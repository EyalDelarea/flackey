import importlib.util
import io
import os
import sys
from pathlib import Path

LAUNCH = Path(__file__).resolve().parents[1] / "packaging" / "launch.py"


def _launch_module():
    """packaging/ is not a package; load the entry point the way PyInstaller runs it, by path."""
    spec = importlib.util.spec_from_file_location("flackey_launch_under_test", LAUNCH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_windowed_build_gets_devnull_for_its_missing_streams(monkeypatch):
    """A `console=False` Windows exe starts with `sys.stdout`/`sys.stderr` set to None, and uvicorn's
    formatter asks `sys.stderr.isatty()` -- an AttributeError on the server thread before the window has a
    page. Both are pointed at the null device before anything reads them."""
    launch = _launch_module()
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)

    launch.ensure_std_streams()

    try:
        for stream in (sys.stdout, sys.stderr):
            assert stream is not None and stream.name == os.devnull
            assert stream.isatty() is False
            stream.write("goes nowhere, and does not raise")
    finally:
        for stream in (sys.stdout, sys.stderr):
            if stream is not None:
                stream.close()


def test_real_streams_are_left_alone(monkeypatch):
    """Every Mac launch and every terminal has streams; they must be the ones the process was given."""
    launch = _launch_module()
    out, err = io.StringIO(), io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)

    launch.ensure_std_streams()

    assert sys.stdout is out and sys.stderr is err


def test_the_streams_are_fixed_before_logging_is_configured(monkeypatch, tmp_path: Path):
    """`logging.StreamHandler()` captures `sys.stderr` when it is built, so the order is the fix."""
    launch = _launch_module()
    order: list[str] = []
    monkeypatch.setattr(launch, "ensure_std_streams", lambda: order.append("streams"))
    monkeypatch.setattr(launch, "load_settings",
                        lambda: order.append("settings") or type("S", (), {"data_dir": tmp_path})())
    monkeypatch.setattr(launch, "configure_logging", lambda d: order.append("logging"))
    import flackey.desktop
    monkeypatch.setattr(flackey.desktop, "run_in_window", lambda s: order.append("window"))
    import flackey.single_instance
    monkeypatch.setattr(flackey.single_instance, "claim_single_instance", lambda: order.append("claim") or True)

    launch.main()

    assert order == ["streams", "settings", "logging", "claim", "window"]


def test_a_second_copy_hands_over_before_loading_the_app(monkeypatch, tmp_path: Path):
    """On an Arm PC the desktop imports took three minutes before the old check ran. A second launch now
    brings the first copy forward and exits without ever reaching the window code."""
    launch = _launch_module()
    monkeypatch.setattr(launch, "load_settings", lambda: type("S", (), {"data_dir": tmp_path})())
    monkeypatch.setattr(launch, "configure_logging", lambda d: None)
    import flackey.desktop
    import flackey.single_instance
    focused: list[bool] = []
    monkeypatch.setattr(flackey.single_instance, "claim_single_instance", lambda: False)
    monkeypatch.setattr(flackey.single_instance, "focus_running_window", lambda: focused.append(True) or True)

    def no_window(settings):
        raise AssertionError("a second copy must not open a window")

    monkeypatch.setattr(flackey.desktop, "run_in_window", no_window)

    launch.main()

    assert focused == [True]
