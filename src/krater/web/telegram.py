from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from ..telegram import LoginError, TelegramLogin

log = logging.getLogger(__name__)
NO_LOGIN = {"authorized": False, "configured": False, "phone_masked": None}


def router(login: TelegramLogin | None, status: dict) -> APIRouter:
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

    return r
