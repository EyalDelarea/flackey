from __future__ import annotations

from pathlib import Path
from typing import Protocol

from ..models import Candidate, Query


class SourceError(Exception):
    pass


class SourceNotFound(SourceError):
    pass


class SourceTimeout(SourceError):
    pass


class SourceUnauthorized(SourceError):
    """The owner's Telegram session was revoked or expired; nothing will work until `crate login`."""


class Source(Protocol):
    name: str

    async def search(self, query: Query) -> list[Candidate]: ...

    async def fetch(self, cand: Candidate, dest_dir: Path) -> Path: ...
