from __future__ import annotations

import logging
import os
import subprocess
import sys
from dataclasses import dataclass
from typing import Protocol

from .tools import no_window


@dataclass
class Button:
    label: str
    data: str


class Notifier(Protocol):
    async def send(self, text: str, buttons: list[Button] | None = None) -> None: ...


class MemoryNotifier:
    def __init__(self) -> None:
        self.sent: list[tuple[str, list[Button] | None]] = []

    async def send(self, text: str, buttons: list[Button] | None = None) -> None:
        self.sent.append((text, buttons))


class LogNotifier:
    """The GUI learns everything from the event stream; the worker's messages only go to the log."""

    def __init__(self) -> None:
        self.log = logging.getLogger("flackey.worker")

    async def send(self, text: str, buttons: list[Button] | None = None) -> None:
        self.log.info(text.replace("\n", " | "))


# A Windows toast from PowerShell, with no module to install. The text comes in through the environment and
# is XML-escaped there, so a track title is never spliced into the script. The AUMID is PowerShell's own:
# Windows shows nothing for an app id nobody registered, and the packaged app does not register one.
_TOAST = r"""
$t = [Security.SecurityElement]::Escape($env:FLACKEY_NOTE_TITLE)
$b = [Security.SecurityElement]::Escape($env:FLACKEY_NOTE_BODY)
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] > $null
$x = New-Object Windows.Data.Xml.Dom.XmlDocument
$x.LoadXml("<toast><visual><binding template='ToastGeneric'><text>$t</text><text>$b</text></binding></visual></toast>")
$id = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($id).Show([Windows.UI.Notifications.ToastNotification]::new($x))
"""


def drive_missing_note(drive: str) -> tuple[str, str]:
    """The system notification for a library drive found unplugged: its title and its body. Shown once
    each time the drive goes; the banner in the app says the rest for as long as it stays gone."""
    return (f"Your music drive ({drive}) isn't connected",
            "Flackey keeps downloading. New tracks will wait and be filed when you plug it in.")


def desktop_notify(title: str, body: str) -> None:
    """One system notification, best effort: Notification Centre on macOS, a toast on Windows, `notify-send`
    elsewhere. Never raises -- a notification that cannot be shown is not worth failing a filing over.

    The text is passed as arguments or environment, never written into a script, because it is a track
    title and comes from the internet."""
    env = None
    if sys.platform == "darwin":
        cmd = ["osascript", "-e", "on run argv", "-e", "display notification (item 2 of argv) with title (item 1 of argv)",
               "-e", "end run", title, body]
    elif sys.platform == "win32":
        cmd = ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", _TOAST]
        env = {**os.environ, "FLACKEY_NOTE_TITLE": title, "FLACKEY_NOTE_BODY": body}
    else:
        cmd = ["notify-send", "--", title, body]   # "--": a title like "-Rezz" is not an option
    try:
        subprocess.Popen(cmd, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **no_window())
    except (OSError, ValueError) as e:     # ValueError: a NUL byte in the text
        logging.getLogger(__name__).debug("could not show a notification: %s", e)
