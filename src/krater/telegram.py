from __future__ import annotations

import asyncio
import logging
import re
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Protocol

from telethon.errors import (
    FloodWaitError,
    PasswordHashInvalidError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    PhoneNumberInvalidError,
    SessionPasswordNeededError,
)

log = logging.getLogger(__name__)


class LoginError(Exception):
    """Plain-language message for the setup screen."""


class LoginClient(Protocol):
    async def is_user_authorized(self) -> bool: ...
    async def get_me(self) -> Any: ...
    async def qr_login(self) -> Any: ...
    async def send_code_request(self, phone: str) -> Any: ...
    async def sign_in(self, phone: str | None = None, code: str | None = None, phone_code_hash: str | None = None,
                      password: str | None = None) -> Any: ...
    async def log_out(self) -> bool: ...
    async def connect(self) -> None: ...
    def is_connected(self) -> bool: ...


def mask_phone(phone: str | None) -> str | None:
    if not phone:
        return None
    digits = re.sub(r"\D", "", phone)
    if len(digits) < 4:
        return "+" + digits
    return f"+{digits[:2]} •••• ••{digits[-2:]}"


class TelegramLogin:
    """Signs the owner's own Telegram account into the Telethon client the worker uses, the way
    Telegram Desktop does: QR first, phone + code as fallback, two-step password when the account has one."""

    def __init__(self, client: LoginClient, configured: bool, on_authorized: Callable[[], None] = lambda: None,
                 make_client: Callable[[], LoginClient] | None = None):
        self.client, self.configured, self.on_authorized = client, configured, on_authorized
        self.make_client = make_client
        self._qr: dict[str, dict] = {}          # id -> {"qr": QRLogin, "state": str, "task": Task}
        self._code_hash: dict[str, str] = {}    # phone -> phone_code_hash

    async def status(self) -> dict:
        if not self.configured:
            # a never-connected client exists only in the unconfigured case (app.py's credential guard);
            # calling it would raise rather than answer, so answer without touching it.
            return {"authorized": False, "configured": False, "phone_masked": None}
        authorized = await self.client.is_user_authorized()
        phone = None
        if authorized:
            try:
                phone = (await self.client.get_me()).phone
            except Exception:
                # a failed lookup must not hide the connected state
                log.warning("could not read the account's phone number", exc_info=True)
        return {"authorized": authorized, "configured": self.configured, "phone_masked": mask_phone(phone)}

    def _authorized(self) -> None:
        self.on_authorized()

    def _cancel_qr(self, *, mark: str | None = None) -> None:
        """Cancel all pending QR tasks. If mark is given, set state to mark without clearing.
        If mark is None, clear the _qr dict entirely."""
        for entry in self._qr.values():
            task = entry["task"]
            if task and not task.done():
                task.cancel()
            if mark is not None:
                entry["state"] = mark
        if mark is None:
            self._qr.clear()

    # ---- QR -------------------------------------------------------------
    async def start_qr(self) -> dict:
        if not self.configured:
            log.error("Telegram credentials missing: add telegram_api_id/telegram_api_hash to settings.json")
            raise LoginError("This copy of Krater isn't set up to connect to Telegram yet.")
        self._cancel_qr()  # one live QR at a time
        if not self.client.is_connected():
            # Telethon disconnects on its own when Telegram drops the session (AuthKeyUnregistered); a QR
            # on a disconnected client fails with "Cannot send requests while disconnected".
            await self.client.connect()
        qr = await self.client.qr_login()
        qr_id = uuid.uuid4().hex
        entry = {"qr": qr, "state": "waiting", "task": None}
        entry["task"] = asyncio.create_task(self._wait_qr(entry))
        self._qr[qr_id] = entry
        expires = qr.expires if qr.expires.tzinfo else qr.expires.replace(tzinfo=UTC)
        return {"id": qr_id, "url": qr.url, "expires_at": expires.isoformat(timespec="seconds")}

    async def _wait_qr(self, entry: dict) -> None:
        qr = entry["qr"]
        expires = qr.expires if qr.expires.tzinfo else qr.expires.replace(tzinfo=UTC)
        timeout = max(1.0, (expires - datetime.now(UTC)).total_seconds())
        try:
            await qr.wait(timeout=timeout)
        except SessionPasswordNeededError:
            entry["state"] = "password_needed"
            return
        except TimeoutError:
            entry["state"] = "expired"
            return
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            log.warning("QR login failed: %s", e)
            entry["state"] = "expired"
            return
        entry["state"] = "done"
        self._authorized()

    def qr_state(self, qr_id: str) -> str:
        entry = self._qr.get(qr_id)
        return entry["state"] if entry else "unknown"

    # ---- password / phone ----------------------------------------------
    async def password(self, password: str) -> str:
        try:
            await self.client.sign_in(password=password)
        except PasswordHashInvalidError:
            raise LoginError("Wrong password. Try again.") from None
        self._cancel_qr(mark="done")
        self._authorized()
        return "done"

    async def send_code(self, phone: str) -> None:
        try:
            result = await self.client.send_code_request(phone)
        except PhoneNumberInvalidError:
            raise LoginError("That does not look like a phone number Telegram knows. Include the country code.") from None
        except FloodWaitError as e:
            raise LoginError(f"Telegram asks you to wait {e.seconds} seconds before trying again.") from None
        self._code_hash[phone] = result.phone_code_hash

    async def sign_in(self, phone: str, code: str) -> str:
        try:
            await self.client.sign_in(phone=phone, code=code, phone_code_hash=self._code_hash.get(phone))
        except SessionPasswordNeededError:
            return "password_needed"
        except PhoneCodeInvalidError:
            raise LoginError("That code is not right. Check the message from Telegram and try again.") from None
        except PhoneCodeExpiredError:
            raise LoginError("That code has expired. Ask for a new one.") from None
        self._cancel_qr(mark="done")
        self._authorized()
        return "done"

    async def log_out(self) -> None:
        self._cancel_qr()
        await self.client.log_out()
        if self.make_client is not None:
            self.client = self.make_client()
            if self.configured:
                try:
                    await self.client.connect()
                except Exception as e:  # noqa: BLE001 - log_out() must still report success to the caller;
                    # a start_qr() on the still-disconnected client will surface through guard()'s 503.
                    log.warning("could not reconnect the rebuilt Telegram client: %s", e)
            log.info("Telegram client rebuilt after sign-out")
