from pathlib import Path

from flackey import tools


def _fake_tool(directory: Path, name: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    exe = directory / name
    exe.write_text("#!/bin/sh\n")
    exe.chmod(0o755)
    return exe


def test_a_checkout_finds_its_helpers_on_the_path(monkeypatch, tmp_path: Path):
    """Running from a clone is the developer's case and must not change: whatever `brew install` put on
    the PATH is what runs."""
    monkeypatch.setattr(tools, "bundled_bin_dir", lambda: None)
    monkeypatch.setenv("PATH", str(tmp_path))
    exe = _fake_tool(tmp_path, "ffmpeg")

    assert tools.tool_path("ffmpeg") == str(exe)


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

    assert tools.tool_path("ffmpeg") == str(exe)


def test_a_missing_helper_reports_itself_rather_than_pretending(monkeypatch, tmp_path: Path):
    """`None` is what the health check and the fingerprint skip already key off, so a missing tool has to
    read as absent rather than as a bare name that would later fail as a confusing FileNotFoundError."""
    monkeypatch.setattr(tools, "bundled_bin_dir", lambda: None)
    monkeypatch.setattr(tools, "BREW_BINS", ())
    monkeypatch.setenv("PATH", str(tmp_path))

    assert tools.tool_path("ffmpeg") is None


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
