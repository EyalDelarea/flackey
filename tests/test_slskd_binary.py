import hashlib
import os
import sys
import zipfile
from pathlib import Path

import pytest

from flackey import slskd_binary
from flackey.slskd_binary import (
    SlskdBinaryError,
    binary_path,
    download_url,
    install,
    install_dir,
    is_installed,
)

ARCH = "arm64"


def _write_zip(path: Path, entries: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        for name, content in entries.items():
            zf.writestr(name, content)


def _good_entries() -> dict[str, bytes]:
    # Mirrors the real archive's flat layout (no top-level directory): the executable plus the
    # non-optional etc/ and wwwroot/ trees.
    return {
        slskd_binary.binary_name(): b"#!/bin/sh\necho fake-slskd\n" + b"x" * 100,
        "slskd.staticwebassets.endpoints.json": b"{}",
        "etc/slskd.example.yml": b"# example config\n",
        "wwwroot/index.html": b"<html></html>",
    }


def _make_zip(tmp_path: Path, entries: dict[str, bytes] | None = None) -> Path:
    zip_path = tmp_path / "src" / "slskd-0.26.0-osx-arm64.zip"
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    _write_zip(zip_path, entries if entries is not None else _good_entries())
    return zip_path


def _pin(monkeypatch, zip_path: Path, *, sha256: str | None = None, size: int | None = None, arch: str = ARCH) -> None:
    """Point the asset this host would fetch at the given (or correct) digest/size for this synthetic
    zip, and fix platform.machine() so the test behaves identically on arm64 and x86_64 hosts/CI. The
    key is whatever `_arch_key` answers rather than `arch` itself: on Windows that is always win-x64,
    whatever the machine claims to be."""
    data = zip_path.read_bytes()
    digest = sha256 if sha256 is not None else hashlib.sha256(data).hexdigest()
    total = size if size is not None else len(data)
    monkeypatch.setattr(slskd_binary.platform, "machine", lambda: arch)
    monkeypatch.setitem(slskd_binary.ASSETS, slskd_binary._arch_key(), (zip_path.name, digest, total))


def _fetch_from(zip_path: Path, chunk_size: int = 7):
    """A fetch double that never touches the network: it just replays a local file in odd-sized
    chunks, so chunked hashing is actually exercised."""
    data = zip_path.read_bytes()

    def fetch(url: str):
        for i in range(0, len(data), chunk_size):
            yield data[i : i + chunk_size]

    return fetch


def test_install_verifies_extracts_and_sets_executable_bit(tmp_path: Path, monkeypatch):
    data_dir = tmp_path / "data"
    zip_path = _make_zip(tmp_path)
    _pin(monkeypatch, zip_path)
    progress: list[tuple[int, int]] = []

    result = install(data_dir, fetch=_fetch_from(zip_path), on_progress=lambda done, total: progress.append((done, total)))

    assert result == binary_path(data_dir)
    assert is_installed(data_dir)
    if sys.platform != "win32":  # no mode bits to set there; the .exe name is what makes it runnable
        assert result.stat().st_mode & 0o777 == 0o755
    assert (install_dir(data_dir) / "wwwroot" / "index.html").read_bytes() == b"<html></html>"
    assert (install_dir(data_dir) / "etc" / "slskd.example.yml").exists()
    size = zip_path.stat().st_size
    assert progress[-1] == (size, size)


def test_second_install_is_a_no_op(tmp_path: Path, monkeypatch):
    data_dir = tmp_path / "data"
    zip_path = _make_zip(tmp_path)
    _pin(monkeypatch, zip_path)
    install(data_dir, fetch=_fetch_from(zip_path))

    calls: list[str] = []

    def unexpected_fetch(url: str):
        calls.append(url)
        return iter([])

    result = install(data_dir, fetch=unexpected_fetch)

    assert result == binary_path(data_dir)
    assert calls == []  # already installed: no network fetch at all


def test_hash_mismatch_aborts_and_leaves_nothing_installed(tmp_path: Path, monkeypatch):
    data_dir = tmp_path / "data"
    zip_path = _make_zip(tmp_path)
    _pin(monkeypatch, zip_path, sha256="0" * 64)  # size correct, digest wrong

    with pytest.raises(SlskdBinaryError, match="SHA-256"):
        install(data_dir, fetch=_fetch_from(zip_path))

    assert not is_installed(data_dir)
    assert not install_dir(data_dir).exists()
    # no stray temp directory left behind under data_dir/slskd either
    assert list((data_dir / "slskd").iterdir()) == []


def test_size_mismatch_aborts(tmp_path: Path, monkeypatch):
    data_dir = tmp_path / "data"
    zip_path = _make_zip(tmp_path)
    data = zip_path.read_bytes()
    _pin(monkeypatch, zip_path, sha256=hashlib.sha256(data).hexdigest(), size=len(data) + 1)

    with pytest.raises(SlskdBinaryError, match="size"):
        install(data_dir, fetch=_fetch_from(zip_path))

    assert not is_installed(data_dir)
    assert list((data_dir / "slskd").iterdir()) == []


def test_zip_entry_escaping_install_dir_aborts_whole_install(tmp_path: Path, monkeypatch):
    data_dir = tmp_path / "data"
    entries = _good_entries()
    entries["../evil.sh"] = b"pwned"
    zip_path = _make_zip(tmp_path, entries)
    _pin(monkeypatch, zip_path)

    with pytest.raises(SlskdBinaryError, match="escapes"):
        install(data_dir, fetch=_fetch_from(zip_path))

    assert not is_installed(data_dir)
    assert not install_dir(data_dir).exists()
    assert not (tmp_path / "evil.sh").exists()
    assert not (data_dir / "evil.sh").exists()


def test_unsupported_architecture_raises_without_fetching(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(slskd_binary.sys, "platform", "darwin")  # the Mac asks the machine; Windows never does
    monkeypatch.setattr(slskd_binary.platform, "machine", lambda: "sparc64")
    calls: list[str] = []

    def unexpected_fetch(url: str):
        calls.append(url)
        return iter([])

    with pytest.raises(SlskdBinaryError, match="unsupported architecture"):
        install(tmp_path / "data", fetch=unexpected_fetch)
    assert calls == []

    with pytest.raises(SlskdBinaryError, match="unsupported architecture"):
        download_url()


def test_windows_always_fetches_the_x64_build_whatever_the_machine_reports(monkeypatch):
    """x64 is the only Windows build, and an emulated x64 Python on ARM64 Windows can report "ARM64" --
    which must not refuse the install the x64 slskd serves perfectly well under the same emulation."""
    monkeypatch.setattr(slskd_binary.sys, "platform", "win32")
    for machine in ("AMD64", "ARM64", "x86_64", ""):
        monkeypatch.setattr(slskd_binary.platform, "machine", lambda m=machine: m)
        assert download_url() == (
            "https://github.com/slskd/slskd/releases/download/0.26.0/slskd-0.26.0-win-x64.zip")


def test_the_windows_pin_is_the_verified_release_asset():
    """Checked by hand against the 0.26.0 release (download + sha256) on 2026-09-29."""
    assert slskd_binary.ASSETS["win-x64"] == (
        "slskd-0.26.0-win-x64.zip",
        "942299d8c97da6cc1f6cd82dcd4a3662b97b82fbd1742df4bec165b79357268a",
        60777709,
    )


def test_windows_installs_and_finds_slskd_exe(tmp_path: Path, monkeypatch):
    """The Windows archive's executable is `slskd.exe`, it gets no chmod (Windows has no mode bits to
    set), and "installed" means the file is there -- `os.access(X_OK)` would say yes to anything."""
    monkeypatch.setattr(slskd_binary.sys, "platform", "win32")
    chmods: list = []
    monkeypatch.setattr(slskd_binary.os, "chmod", lambda *a: chmods.append(a))
    data_dir = tmp_path / "data"
    zip_path = _make_zip(tmp_path)
    assert "slskd.exe" in zipfile.ZipFile(zip_path).namelist()
    _pin(monkeypatch, zip_path)

    result = install(data_dir, fetch=_fetch_from(zip_path))

    assert result == install_dir(data_dir) / "slskd.exe" == binary_path(data_dir)
    assert chmods == []
    monkeypatch.setattr(slskd_binary.os, "access", lambda *a: False)
    assert is_installed(data_dir)


def test_a_windows_zip_without_slskd_exe_is_refused(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(slskd_binary.sys, "platform", "win32")
    entries = _good_entries()
    entries["slskd"] = entries.pop("slskd.exe")  # the Mac layout, fetched on Windows by mistake
    zip_path = _make_zip(tmp_path, entries)
    _pin(monkeypatch, zip_path)

    with pytest.raises(SlskdBinaryError, match="slskd.exe"):
        install(tmp_path / "data", fetch=_fetch_from(zip_path))
    assert not is_installed(tmp_path / "data")


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX executable bit")
def test_on_the_mac_a_file_without_the_executable_bit_is_not_installed(tmp_path: Path):
    path = binary_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"x")
    os.chmod(path, 0o644)
    assert not is_installed(tmp_path)
