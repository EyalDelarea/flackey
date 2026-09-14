from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..sharing import empty_state


def router(sharing=None) -> APIRouter:
    """The port the page shows. Without a service -- tests, and any process that does not run the
    sharing loop -- the state is the empty one and there is nothing to check."""
    r = APIRouter(prefix="/api/sharing")

    @r.get("")
    async def get_sharing() -> dict:
        return dict(sharing.state) if sharing is not None else empty_state()

    @r.post("/check")
    async def check() -> dict:
        if sharing is None or not sharing.can_check:
            raise HTTPException(409, "Soulseek isn't set up yet, so there is no port to check.")
        return sharing.start_refresh()

    return r
