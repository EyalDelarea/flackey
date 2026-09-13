import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from telethon.errors import (
    PasswordHashInvalidError,
    PhoneCodeInvalidError,
    SessionPasswordNeededError,
)

from flackey.telegram import LoginError, TelegramLogin, mask_phone


class FakeQR:
    def __init__(self, outcome):
        self.outcome, self.url = outcome, "tg://login?token=abc"
        self.expires = datetime.now(UTC) + timedelta(seconds=30)
        self.recreated = 0

    async def wait(self, timeout=None):
        if self.outcome == "password":
            raise SessionPasswordNeededError(request=None)
        if self.outcome == "expire":
            raise TimeoutError
        if self.outcome == "hang":
            await asyncio.sleep(3600)
        return object()

    async def recreate(self):
        self.recreated += 1


class FakeClient:
    def __init__(self, authorized=False, qr_outcome="ok", phone="31612345642"):
        self.authorized, self.qr_outcome, self.phone = authorized, qr_outcome, phone
        self.calls = []
        self.dead = False
        self.connected = False

    async def is_user_authorized(self):
        if self.dead:
            raise RuntimeError("client is dead")
        return self.authorized

    async def get_me(self):
        class Me:
            phone = self.phone
        return Me()

    async def qr_login(self):
        if self.dead:
            raise RuntimeError("client is dead")
        return FakeQR(self.qr_outcome)

    async def send_code_request(self, phone):
        if self.dead:
            raise RuntimeError("client is dead")
        self.calls.append(("code", phone))
        class R:
            phone_code_hash = "h"
        return R()

    async def sign_in(self, phone=None, code=None, phone_code_hash=None, password=None):
        if self.dead:
            raise RuntimeError("client is dead")
        self.calls.append(("sign_in", phone, code, phone_code_hash, password))
        if password == "bad":
            raise PasswordHashInvalidError(request=None)
        if code == "000":
            raise PhoneCodeInvalidError(request=None)
        if code == "2fa" and password is None:
            raise SessionPasswordNeededError(request=None)
        self.authorized = True

    async def log_out(self):
        self.authorized = False
        self.dead = True
        return True

    async def connect(self):
        self.connected = True

    def is_connected(self):
        return self.connected


def test_mask_phone():
    assert mask_phone("31612345642") == "+31 •••• ••42"
    assert mask_phone("+1 555 0100") == "+15 •••• ••00"
    assert mask_phone(None) is None


async def test_status():
    assert await TelegramLogin(FakeClient(authorized=True), True).status() == {
        "authorized": True, "configured": True, "phone_masked": "+31 •••• ••42"}
    assert await TelegramLogin(FakeClient(), False).status() == {"authorized": False, "configured": False, "phone_masked": None}


async def test_status_when_unconfigured_never_touches_the_client():
    # app.py's credential guard hands TelegramLogin a client that was never connected; status() must not
    # call it in the unconfigured case, since a real (Telethon) disconnected client raises rather than
    # returning a bool.
    class DisconnectedClient:
        async def is_user_authorized(self):
            raise ConnectionError("Cannot send requests while disconnected")

    assert await TelegramLogin(DisconnectedClient(), False).status() == {
        "authorized": False, "configured": False, "phone_masked": None}


async def test_qr_login_completes_and_notifies():
    fired = []
    login = TelegramLogin(FakeClient(), True, on_authorized=lambda: fired.append(1))
    qr = await login.start_qr()
    assert qr["url"].startswith("tg://login") and qr["expires_at"].endswith("+00:00")
    await asyncio.sleep(0.01)
    assert login.qr_state(qr["id"]) == "done" and fired == [1]
    assert login.qr_state("nope") == "unknown"


async def test_qr_login_needs_password_then_password_signs_in():
    fired = []
    client = FakeClient(qr_outcome="password")
    login = TelegramLogin(client, True, on_authorized=lambda: fired.append(1))
    qr = await login.start_qr()
    await asyncio.sleep(0.01)
    assert login.qr_state(qr["id"]) == "password_needed" and fired == []
    with pytest.raises(LoginError, match="Wrong password"):
        await login.password("bad")
    assert await login.password("secret") == "done" and fired == [1]
    assert client.calls[-1] == ("sign_in", None, None, None, "secret")


async def test_qr_expires():
    login = TelegramLogin(FakeClient(qr_outcome="expire"), True)
    qr = await login.start_qr()
    await asyncio.sleep(0.01)
    assert login.qr_state(qr["id"]) == "expired"


async def test_qr_requires_credentials():
    with pytest.raises(LoginError, match="isn't set up to connect"):
        await TelegramLogin(FakeClient(), False).start_qr()


async def test_phone_flow():
    fired = []
    client = FakeClient()
    login = TelegramLogin(client, True, on_authorized=lambda: fired.append(1))
    await login.send_code("+31612345642")
    with pytest.raises(LoginError, match="code"):
        await login.sign_in("+31612345642", "000")
    assert await login.sign_in("+31612345642", "2fa") == "password_needed" and fired == []
    assert await login.password("secret") == "done" and fired == [1]
    assert client.calls[0] == ("code", "+31612345642")
    assert client.calls[1] == ("sign_in", "+31612345642", "000", "h", None)


async def test_log_out():
    # FakeClient.log_out() now models Telethon's destructive behaviour: the client is marked dead and
    # raises on further calls. A make_client factory is passed so status() below has a live client to
    # call -- the rebuild itself is exercised in detail by the tests below.
    client = FakeClient(authorized=True)
    login = TelegramLogin(client, True, make_client=lambda: FakeClient())
    await login.log_out()
    assert (await login.status())["authorized"] is False


async def test_log_out_rebuilds_the_client_so_reconnect_works():
    login = TelegramLogin(FakeClient(authorized=True), True, make_client=lambda: FakeClient())
    await login.log_out()
    qr = await login.start_qr()
    assert qr["url"].startswith("tg://login")
    assert login.client.connected is True


async def test_log_out_survives_a_reconnect_failure():
    # If the network is down, connect() on the rebuilt client can raise. log_out() must still finish
    # (the caller's route already deleted the session and must report success), leaving the new client
    # assigned but unconnected; a later start_qr() then surfaces through guard()'s plain-words 503.
    class UnreachableClient(FakeClient):
        async def connect(self):
            raise ConnectionError("no route to host")

    unreachable = UnreachableClient()
    login = TelegramLogin(FakeClient(authorized=True), True, make_client=lambda: unreachable)
    await login.log_out()  # must not raise
    assert login.client is unreachable
    assert login.client.connected is False


async def test_log_out_cancels_a_pending_qr():
    fired = []
    client = FakeClient(authorized=True, qr_outcome="hang")
    login = TelegramLogin(client, True, on_authorized=lambda: fired.append(1))
    qr = await login.start_qr()
    qr_id = qr["id"]
    await asyncio.sleep(0.01)
    # Verify the task is still running before logout
    task = login._qr[qr_id]["task"]
    assert not task.done()
    await login.log_out()
    await asyncio.sleep(0.01)
    # After logout, QR dict is cleared and task is cancelled
    assert login._qr == {}
    assert task.cancelled()
    assert login.qr_state(qr_id) == "unknown"
    assert fired == []


async def test_phone_sign_in_cancels_a_pending_qr():
    fired = []
    client = FakeClient(qr_outcome="hang")
    login = TelegramLogin(client, True, on_authorized=lambda: fired.append(1))
    qr = await login.start_qr()
    qr_id = qr["id"]
    await login.send_code("+31612345642")
    result = await login.sign_in("+31612345642", "123")
    await asyncio.sleep(0.01)
    assert result == "done"
    # on_authorized fires exactly once
    assert fired == [1]
    # QR state marked "done", entries kept for qr_state() polling
    assert login.qr_state(qr_id) == "done"


async def test_password_after_qr_cancels_the_wait():
    fired = []
    client = FakeClient(qr_outcome="hang")
    login = TelegramLogin(client, True, on_authorized=lambda: fired.append(1))
    qr = await login.start_qr()
    qr_id = qr["id"]
    await asyncio.sleep(0.01)
    task = login._qr[qr_id]["task"]
    assert not task.done()
    # Switch to password flow
    await login.password("secret")
    await asyncio.sleep(0.01)
    # on_authorized fires exactly once
    assert fired == [1]
    # QR task is cancelled
    assert task.cancelled()
    # QR state marked "done"
    assert login.qr_state(qr_id) == "done"


async def test_start_qr_reconnects_a_dropped_client():
    """Telethon disconnects itself when Telegram drops the session (AuthKeyUnregistered); the next QR must
    connect again instead of failing with 'Cannot send requests while disconnected'."""
    client = FakeClient()
    client.connected = False
    login = TelegramLogin(client, True)
    await login.start_qr()
    assert client.connected is True


async def test_reconfigure_rebuilds_and_connects_the_client():
    from flackey.telegram import TelegramLogin

    class Client:
        def __init__(self):
            self.connected = False

        async def connect(self):
            self.connected = True

        def is_connected(self):
            return self.connected

        async def is_user_authorized(self):
            return False

    made = []

    def make():
        c = Client()
        made.append(c)
        return c

    login = TelegramLogin(Client(), False, make_client=make)
    assert (await login.status())["configured"] is False
    await login.reconfigure()
    assert login.configured is True and login.client is made[-1] and made[-1].connected
    assert (await login.status()) == {"authorized": False, "configured": True, "phone_masked": None}


async def test_reconfigure_closes_a_connected_client_before_opening_the_new_one():
    """The worker reads the bot's messages through login.client (app.py dereferences it on every
    call), so a reconfigure while already connected must close that socket rather than abandon it --
    and close it before the replacement dials in, so the two never share the session file."""
    events = []

    class Client:
        def __init__(self, name, connected=False):
            self.name, self.connected = name, connected

        async def connect(self):
            events.append(f"connect {self.name}")
            self.connected = True

        async def disconnect(self):
            events.append(f"disconnect {self.name}")
            self.connected = False

        def is_connected(self):
            return self.connected

        async def is_user_authorized(self):
            return False

    old = Client("old", connected=True)
    new = Client("new")
    login = TelegramLogin(old, True, make_client=lambda: new)
    await login.reconfigure()
    assert events == ["disconnect old", "connect new"]
    assert login.client is new and old.connected is False


async def test_reconfigure_leaves_an_unconnected_client_alone():
    """The first run's placeholder client was never connected (app.py skips connect() when the keys
    are missing); calling disconnect() on it would be a pointless round trip."""
    class Client:
        def __init__(self):
            self.connected = False
            self.disconnects = 0

        async def connect(self):
            self.connected = True

        async def disconnect(self):
            self.disconnects += 1

        def is_connected(self):
            return self.connected

        async def is_user_authorized(self):
            return False

    old = Client()
    login = TelegramLogin(old, False, make_client=Client)
    await login.reconfigure()
    assert old.disconnects == 0 and login.client is not old
