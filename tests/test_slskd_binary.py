import hashlib
import zipfile
from pathlib import Path

import pytest

from krater import slskd_binary
from krater.slskd_binary import (
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
        "slskd": b"#!/bin/sh\necho fake-slskd\n" + b"x" * 100,
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
    """Point ASSETS[arch] at the given (or correct) digest/size for this synthetic zip, and fix
    platform.machine() so the test behaves identically on arm64 and x86_64 hosts/CI."""
    data = zip_path.read_bytes()
    digest = sha256 if sha256 is not None else hashlib.sha256(data).hexdigest()
    total = size if size is not None else len(data)
    monkeypatch.setattr(slskd_binary.platform, "machine", lambda: arch)
    monkeypatch.setitem(slskd_binary.ASSETS, arch, (zip_path.name, digest, total))


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
