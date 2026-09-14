# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the Mac app. Build it with `packaging/build_app.sh`, not by hand -- the icon has
to be converted first and this file expects to find it.

What is deliberately *not* in here: `.env`, and any settings file. Those carry the owner's own Telegram
API credentials, and this bundle is made to hand to someone else. The app already knows how to start
without them and show its setup screen, which is the correct experience for a second person anyway.
"""

import re
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH).parent  # noqa: F821 - SPECPATH is injected by PyInstaller
VERSION = re.search(
    r'^__version__ = "([^"]+)"',
    (ROOT / "src" / "flackey" / "__init__.py").read_text(),
    re.M,
).group(1)

datas = [
    # `resource_dir()` resolves to the unpacked bundle when frozen, so these two land exactly where
    # `UI_DIR` and `APP_ICON` look for them.
    (str(ROOT / "web" / "dist"), "web/dist"),
    (str(ROOT / "src" / "flackey" / "assets"), "flackey/assets"),
]
datas += collect_data_files("curl_cffi")   # the bundled CA bundle; without it every HTTPS call fails

# The helper programs `flackey.tools` looks for first: `bundled_bin_dir()` is `sys._MEIPASS / "bin"`, and
# a destination of "bin" here is exactly that folder. build_app.sh fetches them; a spec run without them
# would build an app that silently falls back to whatever Homebrew the machine has, so it refuses.
HELPERS = ROOT / "packaging" / "build" / "bin"
binaries = [(str(HELPERS / name), "bin") for name in ("ffmpeg", "ffprobe", "fpcalc") if (HELPERS / name).is_file()]
if len(binaries) != 3:
    raise SystemExit("helper binaries missing from packaging/build/bin: run packaging/build_app.sh, not pyinstaller directly")
datas.append((str(HELPERS / "licenses"), "bin/licenses"))

# uvicorn picks its loop and protocol implementations by string at run time, so the module graph cannot
# see them and they have to be named. This is the classic way a frozen server starts and then does
# nothing at all.
hiddenimports = collect_submodules("uvicorn") + [
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
]

a = Analysis(  # noqa: F821
    [str(ROOT / "packaging" / "launch.py")],
    pathex=[str(ROOT / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    # Nothing in the app draws a chart; matplotlib and tkinter arrive as transitive suggestions and cost
    # tens of megabytes each in a bundle that someone has to download.
    excludes=["tkinter", "matplotlib", "PIL", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="Flackey",
    debug=False,
    strip=False,
    upx=False,
    console=False,   # a windowed build: stdout goes nowhere, which is why launch.py logs to a file
    argv_emulation=False,
    target_arch=None,
)
coll = COLLECT(  # noqa: F821
    exe, a.binaries, a.datas,
    strip=False, upx=False, name="Flackey",
)
app = BUNDLE(  # noqa: F821
    coll,
    name="Flackey.app",
    icon=str(ROOT / "packaging" / "build" / "Flackey.icns"),
    bundle_identifier="com.flackey.app",
    info_plist={
        "CFBundleName": "Flackey",
        "CFBundleDisplayName": "Flackey",
        "CFBundleShortVersionString": VERSION,
        "CFBundleVersion": VERSION,
        "NSHighResolutionCapable": True,
        # The UI is served by a uvicorn on 127.0.0.1 and loaded over http. App Transport Security
        # blocks plain http by default, and this is the key that carves out the loopback case.
        "NSAppTransportSecurity": {"NSAllowsLocalNetworking": True},
        # One window, and closing it quits: without this the app stays in the Dock with nothing on
        # screen and no way back to it.
        "LSApplicationCategoryType": "public.app-category.music",
        "LSMinimumSystemVersion": "12.0",
    },
)
