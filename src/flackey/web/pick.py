"""The native "choose a folder" dialog behind the library-folder field.

Two dialogs, one per system, and neither lives entirely here. On macOS this module runs AppleScript's
`choose folder` itself, which works from any process -- the desktop window, or a browser tab on
`flackey start --browser`. Everywhere else the only dialog there is belongs to the pywebview window, and
this layer may not import pywebview (`web` sits below `desktop` in the import contract, and the server
must start without pywebview installed at all), so the window hands its dialog in at run time: see
`app.ServerHandle.pick_folder` and `desktop.folder_picker`.

That makes "is there a dialog?" a question for the moment of the request, not for import time. The
window only exists after the server has started, and a server started without one (`--no-browser` on
Windows) has no dialog to offer -- the page then keeps the plain text field, which is what
`/pick-folder/available` answering false tells it.
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from fastapi import APIRouter, HTTPException

logger = logging.getLogger(__name__)

Picker = Callable[[Path | None], Path | None]


def choose_folder(initial: Path | None) -> Path | None:
    """Open the macOS folder dialog and return the selected path or None if cancelled."""
    cmd = ["osascript", "-e", "tell application \"System Events\" to activate"]
    if initial:
        escaped = str(initial).replace('\\', '\\\\').replace('"', '\\"')
        default_part = f' default location POSIX file "{escaped}"'
    else:
        default_part = ""
    cmd.extend(["-e", f'POSIX path of (choose folder with prompt "Where should your music live?"{default_part})'])

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300, check=False)

    if result.returncode == 0:
        return Path(result.stdout.strip())
    elif result.returncode == 1 and "User canceled" in result.stderr:
        return None
    else:
        raise RuntimeError(result.stderr.strip())


def native_picker() -> Picker | None:
    """The dialog this process can open without a window's help: AppleScript on macOS, none elsewhere."""
    return choose_folder if sys.platform == "darwin" else None


def router(find_picker: Callable[[], Picker | None] = native_picker) -> APIRouter:
    """`find_picker` is asked on every request rather than once: the window that owns the Windows dialog
    registers it after this router already exists."""
    r = APIRouter(prefix="/api")

    @r.post("/pick-folder")
    async def pick_folder(body: dict) -> dict:
        picker = find_picker()
        if picker is None:
            raise HTTPException(501, "Choosing a folder needs the Flackey window. Type the folder's path instead.")

        initial_str = (body.get("initial") or "").strip()
        initial = Path(initial_str) if initial_str else None

        try:
            path = await asyncio.to_thread(picker, initial)
            return {"path": str(path) if path else None}
        except RuntimeError:
            logger.exception("Error opening folder chooser")
            raise HTTPException(500, "Couldn't open the folder chooser.") from None

    @r.get("/pick-folder/available")
    async def pick_folder_available() -> dict:
        return {"available": find_picker() is not None}

    return r
