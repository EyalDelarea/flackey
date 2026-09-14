"""Where the helper binaries are.

`ffmpeg`, `ffprobe` and `fpcalc` are separate programs this app shells out to. From a clone they come
from the PATH, which is what `brew install ffmpeg chromaprint` sets up. A packaged .app has no useful
PATH -- Finder launches it with a bare one that does not include /opt/homebrew/bin -- and carries its
own copies, so it looks inside itself first and only then at whatever the machine has.

Resolving to an absolute path rather than passing a bare name also fixes the Finder case on its own:
even with nothing bundled, this module can find Homebrew's copy where a bare `["ffmpeg", ...]` would
raise FileNotFoundError.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

# Where a packaged build would keep its own copies, and where `brew` puts them on the two Mac
# architectures. Finder hands a GUI app a PATH of roughly `/usr/bin:/bin:/usr/sbin:/sbin`, so the
# Homebrew prefixes have to be named explicitly or a bundle finds nothing on a machine that has
# everything installed.
HELPERS = ("ffmpeg", "ffprobe", "fpcalc")
BREW_BINS = ("/opt/homebrew/bin", "/usr/local/bin")


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
        candidate = bundled / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    found = shutil.which(name)
    if found:
        return found
    # Only reached in a GUI launch, where Finder's PATH omits the Homebrew prefixes.
    for prefix in BREW_BINS:
        candidate = Path(prefix) / name
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


def missing_helpers() -> tuple[str, ...]:
    """The helpers this machine cannot supply, in the order they are named to the user."""
    return tuple(name for name in HELPERS if tool_path(name) is None)
