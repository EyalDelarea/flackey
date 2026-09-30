"""Download, verify and extract the slskd sidecar binary.

Nothing here executes anything. `install()` streams the pinned release archive to a temp file,
verifies its size and SHA-256 against the constants below *before* touching the zip's contents,
extracts to a temp sibling directory, and only then moves the result into place. A verification
failure deletes what was downloaded and raises; it never leaves a half-installed tree behind.
"""
from __future__ import annotations

import hashlib
import os
import platform
import shutil
import sys
import tempfile
import zipfile
from collections.abc import Callable, Iterable
from pathlib import Path

SLSKD_VERSION = "0.26.0"

# (asset file name, pinned sha256, pinned size in bytes) — the arm64 digest was confirmed by
# downloading the release and hashing it locally; both come from the GitHub release metadata. The
# win-x64 digest and size were confirmed the same way on 2026-09-29 (and its zip holds `slskd.exe` at
# the top level, beside `wwwroot/`, like the Mac archives hold `slskd`).
ASSETS: dict[str, tuple[str, str, int]] = {
    "arm64": (
        "slskd-0.26.0-osx-arm64.zip",
        "53bd82e26224908abb30780f3e3a3ee58788d17379354b3138c85c6fe02cd5a0",
        58309223,
    ),
    "x86_64": (
        "slskd-0.26.0-osx-x64.zip",
        "3d624c53de73229caa090c395ee5eada9c7f54d59fd3a0e79a2597e8b467b448",
        60596634,
    ),
    "win-x64": (
        "slskd-0.26.0-win-x64.zip",
        "942299d8c97da6cc1f6cd82dcd4a3662b97b82fbd1742df4bec165b79357268a",
        60777709,
    ),
}
# The one Windows build Flackey ships is x64, and it runs on ARM Windows under emulation. The sidecar
# follows the app rather than the machine: an x64 slskd is what the x64 installer was tested with.
WINDOWS_ASSET = "win-x64"

_RELEASE_URL = f"https://github.com/slskd/slskd/releases/download/{SLSKD_VERSION}/{{asset}}"
_CHUNK_SIZE = 1024 * 1024

FetchFn = Callable[[str], Iterable[bytes]]


class SlskdBinaryError(Exception):
    """Raised when the slskd archive cannot be fetched, verified, or installed."""


def install_dir(data_dir: Path) -> Path:
    """Where the archive is extracted. A dedicated directory, never shared with anything else."""
    return data_dir / "slskd" / "bin"


def binary_name() -> str:
    """What the archive calls the executable: `slskd.exe` in the Windows zip, `slskd` in the Mac ones."""
    return "slskd.exe" if sys.platform == "win32" else "slskd"


def binary_path(data_dir: Path) -> Path:
    return install_dir(data_dir) / binary_name()


def is_installed(data_dir: Path) -> bool:
    """True when the binary exists and is executable. On Windows existence is the whole test:
    `os.access(X_OK)` there answers True for any file at all, and the `.exe` name is what makes it
    runnable -- so asking would look like a check without being one."""
    path = binary_path(data_dir)
    if sys.platform == "win32":
        return path.is_file()
    return path.is_file() and os.access(path, os.X_OK)


def _arch_key() -> str:
    # Not `platform.machine()` on Windows: an x64 Python running under emulation on an ARM64 machine can
    # report the machine's native "ARM64" there (it reads the processor, not the process), which is not
    # a key below and would refuse an install that the x64 asset serves perfectly well.
    if sys.platform == "win32":
        return WINDOWS_ASSET
    machine = platform.machine()
    if machine in ASSETS:
        return machine
    raise SlskdBinaryError(
        f"unsupported architecture {machine!r}; slskd is only fetched for arm64 or x86_64 macOS"
    )


def download_url() -> str:
    """The pinned release URL for this machine's architecture."""
    asset, _sha256, _size = ASSETS[_arch_key()]
    return _RELEASE_URL.format(asset=asset)


def _default_fetch(url: str) -> Iterable[bytes]:
    import httpx

    with httpx.stream("GET", url, follow_redirects=True, timeout=60.0) as resp:
        resp.raise_for_status()
        yield from resp.iter_bytes(_CHUNK_SIZE)


def _download_and_verify(
    url: str,
    *,
    fetch: FetchFn,
    expected_sha256: str,
    expected_size: int,
    dest: Path,
    on_progress: Callable[[int, int], None] | None,
) -> None:
    """Stream `url` to `dest`, hashing as it goes. Raises SlskdBinaryError and deletes `dest` on any
    size or hash mismatch. Never reports a total larger than the pinned size (the server's claimed
    Content-Length, if any, is not trusted)."""
    digest = hashlib.sha256()
    done = 0
    try:
        with dest.open("wb") as fh:
            for chunk in fetch(url):
                fh.write(chunk)
                digest.update(chunk)
                done += len(chunk)
                if on_progress is not None:
                    on_progress(done, expected_size)
    except SlskdBinaryError:
        raise
    except Exception as exc:  # network/IO failure while streaming
        dest.unlink(missing_ok=True)
        raise SlskdBinaryError(f"failed to download slskd: {exc}") from exc

    if done != expected_size:
        dest.unlink(missing_ok=True)
        raise SlskdBinaryError(
            f"downloaded size mismatch: expected {expected_size} bytes, got {done}"
        )

    actual = digest.hexdigest()
    if actual != expected_sha256:
        dest.unlink(missing_ok=True)
        raise SlskdBinaryError("downloaded archive failed SHA-256 verification")


def _safe_extract(archive: Path, dest: Path) -> None:
    """Extract `archive` into `dest`. Every entry's resolved destination is checked against `dest`
    before anything is extracted — a single entry that would land outside `dest` (path traversal)
    aborts the whole install with nothing extracted. Mirrors the containment check in
    `source/slskd.py`'s `local_path_for`."""
    dest.mkdir(parents=True, exist_ok=True)
    root = dest.resolve()
    with zipfile.ZipFile(archive) as zf:
        for info in zf.infolist():
            candidate = (dest / info.filename).resolve()
            if candidate != root and not candidate.is_relative_to(root):
                raise SlskdBinaryError(
                    f"archive entry escapes the install directory: {info.filename!r}"
                )
        zf.extractall(dest)


def install(
    data_dir: Path,
    *,
    fetch: FetchFn = _default_fetch,
    on_progress: Callable[[int, int], None] | None = None,
) -> Path:
    """Download the pinned slskd archive, verify its SHA-256, extract it, and return the path to the
    executable. Raises SlskdBinaryError on an unsupported architecture, a network failure, a size or
    hash mismatch, or an unwritable path. Never executes anything.

    If already installed, returns the existing path without downloading."""
    if is_installed(data_dir):
        return binary_path(data_dir)

    arch = _arch_key()
    asset, expected_sha256, expected_size = ASSETS[arch]
    url = download_url()

    target = install_dir(data_dir)
    target.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(dir=target.parent) as tmp_name:
        tmp_dir = Path(tmp_name)
        archive_path = tmp_dir / asset
        _download_and_verify(
            url,
            fetch=fetch,
            expected_sha256=expected_sha256,
            expected_size=expected_size,
            dest=archive_path,
            on_progress=on_progress,
        )

        extract_dir = tmp_dir / "extracted"
        _safe_extract(archive_path, extract_dir)

        extracted_binary = extract_dir / binary_name()
        if not extracted_binary.is_file():
            raise SlskdBinaryError(f"archive did not contain an {binary_name()!r} executable")
        if sys.platform != "win32":
            # Mode bits mean nothing to Windows, and `chmod` there can only toggle read-only.
            os.chmod(extracted_binary, 0o755)

        if target.exists():
            shutil.rmtree(target)
        shutil.move(str(extract_dir), str(target))

    return binary_path(data_dir)
