from __future__ import annotations

import asyncio
import logging
import re
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Protocol

from telethon.errors import (
    AuthKeyError,
    FloodWaitError,
    PasswordHashInvalidError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    PhoneNumberInvalidError,
    SessionPasswordNeededError,
    UnauthorizedError,
)

log = logging.getLogger(__name__)

# Telethon's connect() and qr_login() retry underneath us and have no deadline of their own, so on a
# network that cannot reach Telegram at all they simply never return -- and the setup screen, which
# has nothing to show until the POST answers, sits on an empty card indefinitely. Long enough to
# cover a slow first connection, short enough that a wedged one becomes a sentence the owner can act
# on. Overridden in tests.
QR_START_TIMEOUT_S = 20.0

# The probe below runs on the failure path of a request that has already gone wrong, and one of the
# reasons it can go wrong is a network that answers nothing at all. Without a deadline of its own it
# would hang the worker exactly when the worker most needs to get on with deciding what happened.
# Overridden in tests.
PROBE_TIMEOUT_S = 8.0


async def probe_authorized(client: LoginClient) -> bool | None:
    """Ask Telegram itself whether this session is still signed in.

    Returns True (signed in), False (the session is gone) or None (could not tell -- the network is
    down, Telegram is unreachable).

    `is_user_authorized()` cannot answer this. Telethon caches `_authorized` at sign-in and clears it
    nowhere except `log_out()`: neither `disconnect()` nor the update loop's own teardown touches it,
    so once this run has signed in successfully the cached answer stays True for the rest of the run
    even after Telegram has revoked the key underneath us. `get_me()` is a real request, which is the
    only thing that knows -- and when the session is gone it answers `None` rather than raising,
    because it catches `UnauthorizedError` itself (telethon/client/users.py).

    The three-valued result is the whole point. A revoked session and a wifi drop reach this code as
    the same builtin `ConnectionError`, and calling a wifi drop a revoked session would pause the
    worker and raise a "sign in again" banner over a session that is perfectly fine."""
    try:
        async with asyncio.timeout(PROBE_TIMEOUT_S):
            if not client.is_connected():
                # Telethon tears its own socket down when the update loop sees the key rejected, so the
                # client this is called on is usually already disconnected and could otherwise only
                # ever answer "cannot tell". `GetConfig` needs no auth, so connecting proves nothing on
                # its own -- the get_me() below is what decides.
                await client.connect()
            me = await client.get_me()
    except (UnauthorizedError, AuthKeyError):
        # Telegram itself saying no. `get_me()` swallows UnauthorizedError, so in the wild this clause
        # catches the key errors that sit beside it (AuthKeyError is a sibling under RPCError, not a
        # subclass, so nothing swallows those) -- and it keeps the answer right if a future telethon
        # stops swallowing, or if connect() is the call that gets the refusal.
        return False
    except (OSError, TimeoutError):
        # ConnectionError is an OSError; so is every socket failure underneath it. Telegram was not
        # reached, so nothing here says anything about the session.
        return None
    except Exception:
        # Anything else is a surprise, and a surprise is not evidence that the owner has been signed
        # out. Guessing "signed out" here would pause the worker and cover the app in a sign-in banner
        # for what may be a bug in this line; "cannot tell" costs a retry.
        log.warning("could not check whether the Telegram session is still signed in", exc_info=True)
        return None
    return me is not None


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
    async def disconnect(self) -> None: ...
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
        # Not `is_user_authorized()`: it answers from a cache that a revoked session never clears, so
        # the Settings page and the sidebar used to keep saying "connected" for the rest of the run
        # after Telegram had thrown this session out (issue #91). The probe asks Telegram.
        authorized = await probe_authorized(self.client)
        if authorized is None:
            # Telegram was not reached, so we know nothing new -- and putting a "sign in again" screen
            # over a working session because the wifi dropped is worse than saying nothing changed.
            # The client's own cached answer is the last thing that was actually true: it only ever
            # says True when this run signed in successfully. With nothing cached it makes a request
            # of its own, which fails on the same dead socket -- and a client that has never signed in
            # is, correctly, not signed in.
            try:
                authorized = await self.client.is_user_authorized()
            except Exception:
                # The page needs an answer and "no" is the safe one: it offers a sign-in.
                log.warning("could not reach Telegram to check the session", exc_info=True)
                authorized = False
        phone = None
        if authorized:
            try:
                me = await self.client.get_me()
                phone = me.phone if me is not None else None
            except Exception:
                # a failed lookup must not hide the connected state
                log.warning("could not read the account's phone number", exc_info=True)
        return {"authorized": authorized, "configured": self.configured, "phone_masked": mask_phone(phone)}

    async def reconfigure(self) -> None:
        """Keys arrived after startup (the setup screen or Settings saved them): build a client that
        carries them and connect it. `make_client` closes over the live Settings object, so the
        values it reads are the ones just saved. Without this the wizard would have to say
        "restart the app"."""
        if self.make_client is None:
            self.configured = True
            return
        self._cancel_qr()
        if self.client.is_connected():
            # The worker reads the bot's messages through this very object (app.py dereferences
            # `login.client` on every call), so an already-connected client has to be closed rather
            # than abandoned with its MTProto socket and receive task still live. disconnect(), not
            # log_out(): a re-save of the keys keeps the account signed in, only drops the socket.
            await self.client.disconnect()
        self.client = self.make_client()
        self.configured = True
        await self.client.connect()
        log.info("Telegram client rebuilt with the keys just saved")

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
            log.warning("Telegram sign-in requested without API keys")
            raise LoginError("This copy of Flackey isn't set up to connect to Telegram yet.")
        self._cancel_qr()  # one live QR at a time
        try:
            async with asyncio.timeout(QR_START_TIMEOUT_S):
                if not self.client.is_connected():
                    # Telethon disconnects on its own when Telegram drops the session
                    # (AuthKeyUnregistered); a QR on a disconnected client fails with "Cannot send
                    # requests while disconnected".
                    await self.client.connect()
                qr = await self.client.qr_login()
        except TimeoutError:
            # Nothing was recorded in `_qr` yet, so there is no half-started login to clean up --
            # whatever the timeout interrupted is dropped with the cancelled task.
            log.warning("Telegram did not answer a QR sign-in within %ss", QR_START_TIMEOUT_S)
            raise LoginError(
                "Telegram did not answer in time. Check this Mac's internet connection and try "
                "again, or sign in with your phone number instead.") from None
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
