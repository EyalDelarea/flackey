"""Where the helper binaries are.

`ffmpeg`, `ffprobe` and `fpcalc` are separate programs this app shells out to. From a clone they come
from the PATH, which is what `brew install ffmpeg chromaprint` sets up. A packaged .app has no useful
PATH -- Finder launches it with a bare one that does not include /opt/homebrew/bin -- and carries its
own copies, so it looks inside itself first and only then at whatever the machine has.

Resolving to an absolute path rather than passing a bare name also fixes the Finder case on its own:
even with nothing bundled, this module can find Homebrew's copy where a bare `["ffmpeg", ...]` would
raise FileNotFoundError.

On Windows the same three helpers ship as `.exe` files in the same `bin` folder, there is no Homebrew,
and "executable" is a property of the file name rather than a mode bit, so each of those three
assumptions is branched on below rather than papered over.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

# Where a packaged build would keep its own copies, and where `brew` puts them on the two Mac
# architectures. Finder hands a GUI app a PATH of roughly `/usr/bin:/bin:/usr/sbin:/sbin`, so the
# Homebrew prefixes have to be named explicitly or a bundle finds nothing on a machine that has
# everything installed.
HELPERS = ("ffmpeg", "ffprobe", "fpcalc")
BREW_BINS = ("/opt/homebrew/bin", "/usr/local/bin")
# `subprocess.CREATE_NO_WINDOW`, spelled out because the constant only exists in a Windows build of
# the `subprocess` module and the tests exercise the Windows branch on a Mac.
_CREATE_NO_WINDOW = 0x08000000


def no_window() -> dict:
    """Extra `subprocess` keyword arguments that stop a child process from opening a console.

    The packaged Windows app is a windowed (GUI-subsystem) executable, so it has no console of its own,
    and Windows answers every console program it starts -- ffmpeg, fpcalc, slskd, `route` -- by opening
    a fresh black console window for it: a flash per fingerprint, dozens a minute while a playlist
    downloads. CREATE_NO_WINDOW runs the child with no console at all. It changes nothing else (stdout
    and stderr are still captured through the pipes the caller asked for).

    Empty off Windows, so a Mac or Linux call is exactly the call it was before -- and has to be, since
    POSIX `Popen` rejects any nonzero `creationflags` outright. Spread into the call as `**no_window()`.

    `slskd_process` and `portmap` keep their own copy of these few lines: they sit below this module in
    the import-linter layers and may not import it."""
    if sys.platform == "win32":
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", _CREATE_NO_WINDOW)}
    return {}


def _executable_name(name: str) -> str:
    """`ffmpeg` is `ffmpeg.exe` on Windows. Only for the paths this module builds itself: `shutil.which`
    already tries every extension in PATHEXT."""
    return name + ".exe" if sys.platform == "win32" else name


def _is_executable(candidate: Path) -> bool:
    """A file that can be run. On Windows `os.access(X_OK)` says nothing -- it answers True for any file
    that exists, a README included -- so the `.exe` name built by `_executable_name` is what carries
    that meaning there and existence is the whole check."""
    if sys.platform == "win32":
        return candidate.is_file()
    return candidate.is_file() and os.access(candidate, os.X_OK)


def bundled_bin_dir() -> Path | None:
    """The `bin` folder this app carries, or None when running from a checkout. PyInstaller unpacks to
    `sys._MEIPASS`; inside a .app that is `Contents/Frameworks`, with data folders symlinked in from
    `Contents/Resources`, so `bin` sits directly under it either way."""
    if not getattr(sys, "frozen", False):
        return None
    return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / "bin"


def resource_dir() -> Path:
    """The folder that `web/dist` hangs off: the unpacked bundle when frozen, the top of the clone
    otherwise. From a checkout that is two levels above this package, which is where the repository root
    is; inside a .app the code lives in `Contents/Frameworks` and the repository is not there at all."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parents[2]


def tool_path(name: str) -> str | None:
    """Absolute path to a helper binary, or None when it is nowhere to be found.

    None rather than the bare name: `fingerprint.fpcalc_available` and the library health check already
    treat absence as a state to report, and a bare name would defer the failure to a FileNotFoundError
    from deep inside a subprocess call, which says much less about what is wrong."""
    bundled = bundled_bin_dir()
    if bundled is not None:
        candidate = bundled / _executable_name(name)
        if _is_executable(candidate):
            return str(candidate)
    found = shutil.which(name)
    if found:
        return found
    if sys.platform == "win32":
        return None  # no Homebrew there, and `which` has already searched the PATH
    # Only reached in a GUI launch, where Finder's PATH omits the Homebrew prefixes.
    for prefix in BREW_BINS:
        candidate = Path(prefix) / name
        if _is_executable(candidate):
            return str(candidate)
    return None


def missing_helpers() -> tuple[str, ...]:
    """The helpers this machine cannot supply, in the order they are named to the user."""
    return tuple(name for name in HELPERS if tool_path(name) is None)
