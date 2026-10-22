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


class TooSlow(LosslessError):
    """A transfer that was moving, but so slowly that another peer is the better bet (see `SlowRule`). Its
    numbers ride along so the attempt's timeline can say how slow, not just that it was."""

    def __init__(self, msg: str, *, pct: int, speed_bps: float, left_s: float):
        super().__init__(msg, "too_slow")
        self.pct, self.speed_bps, self.left_s = pct, speed_bps, left_s


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


@dataclass(frozen=True)
class SlowRule:
    """When a transfer that is still moving may be given up for the next pick. The worker passes one only
    while there is a next pick worth going to (see `LosslessMixin._attempt`); without it a slow transfer
    runs to its ceiling as before, because a slow copy beats no copy.

    Judged on the average rate since the first byte, once `grace_s` of data has flowed (the first seconds
    of a transfer say little). Given up only when both hold: the rate is below `max_rate_bps`, and the
    remaining bytes would still take longer than `finish_s` at it. Never past `spare_from` of the file --
    that close to the end, finishing is cheaper than any new peer's queue and first byte. The worker
    folds what the next pick advertises into both numbers, so this side needs to know nothing about it."""
    grace_s: float
    finish_s: float
    spare_from: float
    max_rate_bps: float


RawSink = Callable[[str, object], None]


class LosslessProvider(Protocol):
    name: str

    async def health(self) -> dict: ...

    async def search(self, text: str, *, wait_s: float, on_raw: RawSink | None = None) -> list[LosslessFile]: ...

    async def download(self, file: LosslessFile, *, first_byte_s: float, total_s: float, poll_s: float,
                       queue_wait_s: float | None = None, stall_s: float | None = None,
                       slow: SlowRule | None = None, on_progress: Callable[[TransferProgress], None] | None = None,
                       on_raw: RawSink | None = None) -> Path: ...

    async def cancel_all(self) -> int: ...

    async def rescan_shares(self) -> None: ...
