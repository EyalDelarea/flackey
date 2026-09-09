from __future__ import annotations

import asyncio
import logging
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from fastapi import APIRouter, HTTPException

logger = logging.getLogger(__name__)


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


def router(picker: Callable[[Path | None], Path | None] = choose_folder) -> APIRouter:
    r = APIRouter(prefix="/api")

    @r.post("/pick-folder")
    async def pick_folder(body: dict) -> dict:
        if sys.platform != "darwin":
            raise HTTPException(501, "Choosing a folder in a window only works on macOS.")

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
        return {"available": sys.platform == "darwin"}

    return r
