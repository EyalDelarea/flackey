from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from ..config import Settings, save_settings
from ..telegram import LoginError, TelegramLogin

log = logging.getLogger(__name__)
NO_LOGIN = {"authorized": False, "configured": False, "phone_masked": None}


def router(login: TelegramLogin | None, status: dict,
           settings: Settings | None = None) -> APIRouter:
    r = APIRouter(prefix="/api/telegram")

    def need() -> TelegramLogin:
        if login is None:
            raise HTTPException(503, "Telegram is not available in this process")
        return login

    async def guard(coro):
        try:
            return await coro
        except LoginError as e:
            raise HTTPException(400, str(e))
        except Exception:
            log.exception("Telegram request failed")
            raise HTTPException(503, "Telegram isn't reachable right now. Try again in a moment.")

    @r.get("/status")
    async def tg_status() -> dict:
        return await login.status() if login else dict(NO_LOGIN)

    @r.post("/qr")
    async def qr() -> dict:
        return await guard(need().start_qr())

    @r.get("/qr/{qr_id}")
    async def qr_state(qr_id: str) -> dict:
        return {"state": need().qr_state(qr_id)}

    @r.post("/password")
    async def password(body: dict) -> dict:
        return {"state": await guard(need().password(body.get("password") or ""))}

    @r.post("/phone")
    async def phone(body: dict) -> dict:
        await guard(need().send_code((body.get("phone") or "").strip()))
        return {"ok": True}

    @r.post("/code")
    async def code(body: dict) -> dict:
        return {"state": await guard(need().sign_in((body.get("phone") or "").strip(), (body.get("code") or "").strip()))}

    @r.post("/logout")
    async def logout() -> dict:
        await need().log_out()
        status["telegram_authorized"] = False
        return {"ok": True}

    @r.post("/keys")
    async def keys(body: dict) -> dict:
        """Save an api_id and api_hash the owner made at my.telegram.org and bring Telegram up under
        them. Most copies never see this: a packaged build carries keys already
        (config.BUILD_DEFAULTS)."""
        if settings is None:
            raise HTTPException(503, "Settings are not available in this process")
        raw_id = str(body.get("api_id") or "").strip()
        api_hash = str(body.get("api_hash") or "").strip()
        if not raw_id.isdigit() or int(raw_id) <= 0:
            raise HTTPException(400, "The API id is a number, for example 1234567.")
        if len(api_hash) != 32:   # my.telegram.org always hands out 32 hex characters
            raise HTTPException(
                400, "The API hash is the long string next to the id at my.telegram.org.")
        save_settings(settings, telegram_api_id=int(raw_id), telegram_api_hash=api_hash)
        await guard(need().reconfigure())
        return {"configured": True}

    @r.post("/skip")
    async def skip() -> dict:
        """The owner chose not to connect Telegram. The bot is one of two sources, so turning it
        off is a real mode (worker.py checks `source_enabled`), not a missing step. Turning it back
        on is the sign-in path's job, see app.on_authorized."""
        if settings is None:
            raise HTTPException(503, "Settings are not available in this process")
        save_settings(settings, source_enabled=False)
        status["source_enabled"] = False   # health reads it from here, so the sidebar hears about it
        return {"source_enabled": False}

    return r
