"""What the worker talks to when it asks a network for a lossless file (spec §5.1). One implementation today
(slskd.SoulseekProvider); the gate, caps, attempt record and conversion never see anything but this."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ..lossless import LosslessFile


class LosslessError(Exception):
    """A miss with an attempt outcome attached (spec §11)."""

    def __init__(self, msg: str, outcome: str = "transfer_failed"):
        super().__init__(msg)
        self.outcome = outcome


class LosslessUnavailable(LosslessError):
    """The sidecar is down or not logged in: nothing to do with this request."""

    def __init__(self, msg: str):
        super().__init__(msg, "unavailable")


@dataclass
class TransferProgress:
    state: str
    bytes: int
    size: int
    speed_bps: float
    first_byte_ms: int | None


RawSink = Callable[[str, object], None]


class LosslessProvider(Protocol):
    name: str

    async def health(self) -> dict: ...

    async def search(self, text: str, *, wait_s: float, on_raw: RawSink | None = None) -> list[LosslessFile]: ...

    async def download(self, file: LosslessFile, *, first_byte_s: float, total_s: float, poll_s: float,
                       queue_wait_s: float | None = None,
                       on_progress: Callable[[TransferProgress], None] | None = None,
                       on_raw: RawSink | None = None) -> Path: ...

    async def cancel_all(self) -> int: ...

    async def rescan_shares(self) -> None: ...
