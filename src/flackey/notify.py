from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol


@dataclass
class Button:
    label: str
    data: str


class Notifier(Protocol):
    async def send(self, text: str, buttons: list[Button] | None = None) -> None: ...


class NullNotifier:
    async def send(self, text: str, buttons: list[Button] | None = None) -> None:
        return None


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
