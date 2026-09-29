import os
import subprocess
import sys
from pathlib import Path

import pytest

from flackey import tools


def _fake_tool(directory: Path, name: str) -> Path:
    """A stand-in helper this platform would run: an executable-bit script on POSIX, a `.exe` name on
    Windows, where the name is what makes a file runnable and the mode bits mean nothing."""
    directory.mkdir(parents=True, exist_ok=True)
    exe = directory / (name + ".exe" if sys.platform == "win32" else name)
    exe.write_text("#!/bin/sh\n")
    exe.chmod(0o755)
    return exe


def _same(found: str | None, exe: Path) -> bool:
    """`shutil.which` on Windows builds the name from PATHEXT, whose case (`.EXE`) need not match the
    file's; the file system does not care, so neither does this comparison."""
    return found is not None and os.path.normcase(found) == os.path.normcase(str(exe))


def test_a_checkout_finds_its_helpers_on_the_path(monkeypatch, tmp_path: Path):
    """Running from a clone is the developer's case and must not change: whatever `brew install` put on
    the PATH is what runs."""
    monkeypatch.setattr(tools, "bundled_bin_dir", lambda: None)
    monkeypatch.setenv("PATH", str(tmp_path))
    exe = _fake_tool(tmp_path, "ffmpeg")

    assert _same(tools.tool_path("ffmpeg"), exe)


def test_the_copy_inside_the_app_wins_over_one_on_the_path(monkeypatch, tmp_path: Path):
    """A packaged app that carries its own ffmpeg has to use that one. The friend's machine may also have
    an older Homebrew copy on the PATH, and the bundle is the version this build was tested against."""
    bundled = _fake_tool(tmp_path / "bundle" / "bin", "ffmpeg")
    _fake_tool(tmp_path / "homebrew", "ffmpeg")
    monkeypatch.setattr(tools, "bundled_bin_dir", lambda: tmp_path / "bundle" / "bin")
    monkeypatch.setenv("PATH", str(tmp_path / "homebrew"))

    assert tools.tool_path("ffmpeg") == str(bundled)


def test_a_bundle_without_its_own_copy_still_falls_back_to_the_path(monkeypatch, tmp_path: Path):
    """The first packaged build ships no binaries and expects the friend to `brew install` them, so the
    bundled folder is missing entirely. That is a fallback, not a failure."""
    exe = _fake_tool(tmp_path / "homebrew", "ffmpeg")
    monkeypatch.setattr(tools, "bundled_bin_dir", lambda: tmp_path / "bundle" / "bin")
    monkeypatch.setenv("PATH", str(tmp_path / "homebrew"))

    assert _same(tools.tool_path("ffmpeg"), exe)


def test_a_missing_helper_reports_itself_rather_than_pretending(monkeypatch, tmp_path: Path):
    """`None` is what the health check and the fingerprint skip already key off, so a missing tool has to
    read as absent rather than as a bare name that would later fail as a confusing FileNotFoundError."""
    monkeypatch.setattr(tools, "bundled_bin_dir", lambda: None)
    monkeypatch.setattr(tools, "BREW_BINS", ())
    monkeypatch.setenv("PATH", str(tmp_path))

    assert tools.tool_path("ffmpeg") is None


@pytest.mark.skipif(sys.platform == "win32", reason="Homebrew prefixes are a macOS fallback only")
def test_a_helper_is_found_where_brew_put_it_even_with_finder_s_bare_path(monkeypatch, tmp_path: Path):
    """Finder launches a .app with a PATH of roughly /usr/bin:/bin:/usr/sbin:/sbin -- no Homebrew prefix.
    That is the whole reason a double-clicked app fails on a machine where the tools plainly work in a
    terminal, and it bites even when nothing is bundled."""
    exe = _fake_tool(tmp_path / "opt" / "homebrew" / "bin", "ffmpeg")
    monkeypatch.setattr(tools, "bundled_bin_dir", lambda: None)
    monkeypatch.setattr(tools, "BREW_BINS", (str(tmp_path / "opt" / "homebrew" / "bin"),))
    monkeypatch.setenv("PATH", "/nonexistent")

    assert tools.tool_path("ffmpeg") == str(exe)


def test_a_checkout_has_no_bundled_folder(monkeypatch):
    """`bundled_bin_dir` is the only part that inspects the freeze, so it is the part worth pinning."""
    monkeypatch.delattr("sys.frozen", raising=False)

    assert tools.bundled_bin_dir() is None


def test_a_frozen_app_looks_beside_its_own_bootstrap(monkeypatch, tmp_path: Path):
    """PyInstaller unpacks to `_MEIPASS`; inside a .app that is `Contents/Frameworks`, with the data
    folders symlinked in from `Contents/Resources`. Either way `bin` sits directly under it."""
    monkeypatch.setattr("sys.frozen", True, raising=False)
    monkeypatch.setattr("sys._MEIPASS", str(tmp_path), raising=False)

    assert tools.bundled_bin_dir() == tmp_path / "bin"


def test_a_checkout_finds_its_web_build_in_the_repo(monkeypatch):
    """`web/dist` sits at the top of the clone, two levels above this package."""
    monkeypatch.delattr("sys.frozen", raising=False)

    assert tools.resource_dir() == Path(tools.__file__).resolve().parents[2]


def test_a_frozen_app_carries_its_web_build_inside_itself(monkeypatch, tmp_path: Path):
    """The packaged app has no repository above it. `web/dist` is collected into the bundle, so the root
    that `web/dist` hangs off has to become the unpacked bundle rather than a path outside the .app --
    which is what `parents[2]` resolves to once the code lives in `Contents/Frameworks`."""
    monkeypatch.setattr("sys.frozen", True, raising=False)
    monkeypatch.setattr("sys._MEIPASS", str(tmp_path), raising=False)

    assert tools.resource_dir() == tmp_path


def test_a_missing_ffmpeg_is_reported_as_a_conversion_failure_not_a_stack_trace(monkeypatch, tmp_path: Path):
    """The friend this build goes to may not have run `brew install ffmpeg` yet. Without this the
    resolver hands `subprocess` a bare name that is nowhere on a Finder PATH, and the raw
    FileNotFoundError travels all the way to a failed row whose message is a filename."""
    from flackey import convert

    monkeypatch.setattr(convert, "tool_path", lambda _: None)
    src = tmp_path / "a.flac"
    src.write_bytes(b"not audio")

    with pytest.raises(convert.ConvertError, match="ffmpeg"):
        convert.to_format(src, "aiff", 16)


def test_a_missing_ffprobe_is_reported_as_a_verify_failure_not_a_stack_trace(monkeypatch, tmp_path: Path):
    """Same reason as ffmpeg: `verify` runs before anything is filed, so it is usually the first place
    a machine without the helpers notices."""
    from flackey import verify

    monkeypatch.setattr(verify, "tool_path", lambda _: None)
    src = tmp_path / "a.flac"
    src.write_bytes(b"not audio")

    with pytest.raises(verify.VerifyError, match="ffprobe"):
        verify.probe(src)


def test_the_helpers_a_machine_is_missing_are_named(monkeypatch):
    """What the startup banner logs. A friend sends back the log file, and this is the line that says
    which of the three they still need to install."""
    monkeypatch.setattr(tools, "BREW_BINS", ())
    monkeypatch.setattr(tools.shutil, "which", lambda _: None)

    assert tools.missing_helpers() == tools.HELPERS


def test_a_windows_bundle_finds_its_exe_helpers(monkeypatch, tmp_path: Path):
    """The Windows build carries `bin/ffmpeg.exe`, not `bin/ffmpeg`, and nothing sets an executable bit
    on it -- so the bundled lookup has to add the extension and settle for the file existing."""
    bundled = tmp_path / "bundle" / "bin"
    bundled.mkdir(parents=True)
    (bundled / "ffmpeg").write_text("not the Windows one")  # a bare name must not be taken for the tool
    exe = bundled / "ffmpeg.exe"
    exe.write_bytes(b"MZ")
    monkeypatch.setattr(tools, "bundled_bin_dir", lambda: bundled)
    monkeypatch.setattr(tools.shutil, "which", lambda _: None)
    monkeypatch.setattr(tools.os, "access", lambda *a: False)  # X_OK is meaningless there; never asked
    monkeypatch.setattr(sys, "platform", "win32")

    assert tools.tool_path("ffmpeg") == str(exe)


def test_windows_never_looks_in_the_homebrew_folders(monkeypatch, tmp_path: Path):
    """There is no Homebrew on Windows. `which` has already searched the PATH with every PATHEXT
    extension, so a miss there is a miss."""
    brew = tmp_path / "brew"
    brew.mkdir()
    (brew / "ffmpeg").write_bytes(b"x")
    monkeypatch.setattr(tools, "bundled_bin_dir", lambda: None)
    monkeypatch.setattr(tools, "BREW_BINS", (str(brew),))
    monkeypatch.setattr(tools.shutil, "which", lambda _: None)
    monkeypatch.setattr(sys, "platform", "win32")

    assert tools.tool_path("ffmpeg") is None


def test_no_window_hides_the_console_only_on_windows(monkeypatch):
    """A windowed Windows app flashes a console for every child it starts unless told not to; everywhere
    else the call must stay exactly what it was, because POSIX Popen refuses any creationflags."""
    monkeypatch.setattr(sys, "platform", "darwin")
    assert tools.no_window() == {}
    monkeypatch.setattr(sys, "platform", "linux")
    assert tools.no_window() == {}
    monkeypatch.setattr(sys, "platform", "win32")
    assert tools.no_window() == {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)}
    assert tools.no_window()["creationflags"] == 0x08000000
