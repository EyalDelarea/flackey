"""Who may talk to the API: the checks every request passes before it reaches a route.

The API has no login, but it is no longer open to anything that can reach the port. Four holes are
closed here:

* Other programs. Any process on the machine -- or another account on it, or a LAN host when Docker
  publishes the port -- can open a socket to the API. Every `/api/` request must carry the launch token
  (`TOKEN_HEADER`): random per launch, handed to the app's own window in the URL fragment it opens and
  printed to the terminal in browser mode, never served to a request that does not already have it.
  Elements that cannot send a header (`<audio>`, `<img>`, `EventSource`) ride on a session cookie that
  only a request with the token can set, and that opens reads: no state change, no saved secret.
* DNS rebinding. A page on evil.example re-points its own name at 127.0.0.1 and is then same-origin with
  Flackey, so CORS never applies. The browser still sends `Host: evil.example`, so a request whose Host
  is a *name* other than localhost (or one the owner allowed) is refused. An IP literal cannot be
  rebound, which is what lets a Docker owner reach the UI by the host's address without configuring
  anything.
* Blind cross-site requests. Any page can fire a body-less POST at localhost without a preflight; it
  cannot read the answer, but the route still runs. Every state-changing request must carry
  `APP_HEADER`, and a custom header is what forces the preflight no other origin passes.
* Framing. No other page may put the UI in a frame and steer the owner's clicks on it.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import secrets

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

APP_HEADER = "x-flackey-app"
TOKEN_HEADER = "x-flackey-token"
SESSION_COOKIE = "flackey_session"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
# What the cookie opens: reads only, and never OPTIONS, which no media element sends.
COOKIE_METHODS = frozenset({"GET", "HEAD"})
LOOPBACK_NAMES = frozenset({"localhost"})
TOKEN_BYTES = 32
# A fixed token from the environment (Docker) is held to roughly the strength of a generated one.
MIN_TOKEN_CHARS = 32
AUTH_STATE = "flackey_auth"
NEEDS_TOKEN = ("Flackey needs its access key. Open it from its own window, or from the link it printed "
               "when it started.")
SECURITY_HEADERS = ((b"content-security-policy", b"frame-ancestors 'none'"),
                    (b"x-frame-options", b"DENY"),
                    (b"x-content-type-options", b"nosniff"))


def new_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def same_secret(given: str | None, expected: str) -> bool:
    """Constant-time, and on bytes: `compare_digest` raises TypeError for a str with non-ASCII in it, and
    a header is whatever latin-1 the client chose to send."""
    if not given:
        return False
    return hmac.compare_digest(given.encode("utf-8", "surrogateescape"), expected.encode("utf-8"))


def session_cookie_value(token: str) -> str:
    """Derived from the token rather than the token itself, so the cookie jar never holds the key that
    opens state changes."""
    mac = hmac.new(token.encode(), b"flackey-session-cookie-v1", hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac).rstrip(b"=").decode()


def _cookie(header: str | None, name: str) -> str | None:
    for part in (header or "").split(";"):
        key, sep, value = part.strip().partition("=")
        if sep and key == name:
            return value
    return None


def from_the_app(request: Request) -> None:
    """Refuse a request some other page made the browser send.

    The middleware already demands the header on every unsafe method; routes that *read* a secret call
    this too, so a GET for one needs the header -- and the token itself, not the media cookie, which
    opens reads of nothing worse than a spectrogram. Not an `Origin` check: a WKWebView on http:// and a
    dev-server proxy make that unguessable here."""
    if not request.headers.get(APP_HEADER) or request.scope.get("state", {}).get(AUTH_STATE) != "token":
        raise HTTPException(403, "That request did not come from Flackey.")


def hostname(host: str) -> str:
    """The name part of a Host header: `[::1]:8765` -> `::1`, `localhost:8765` -> `localhost`."""
    host = host.strip().lower()
    if host.startswith("["):
        return host[1:host.find("]")] if "]" in host else ""
    name, _, _ = host.partition(":")
    return name.rstrip(".")


def host_allowed(host: str | None, extra: frozenset[str] = frozenset()) -> bool:
    if not host:
        return False
    name = hostname(host)
    if not name:
        return False
    if name in LOOPBACK_NAMES or name in extra:
        return True
    try:
        ipaddress.ip_address(name)
    except ValueError:
        return False
    return True


class GuardMiddleware:
    """Refuse a rebound Host on every path; on the API, a request without the launch token (or, for a
    read, the session cookie it set) and an unsafe method without `APP_HEADER`. Every answer, refusals
    included, says it may not be framed."""

    def __init__(self, app: ASGIApp, token: str, allowed_hosts: frozenset[str] = frozenset()) -> None:
        self.app = app
        self.token = token
        self.cookie = session_cookie_value(token)
        self.allowed_hosts = frozenset(h.lower() for h in allowed_hosts)

    def auth(self, scope: Scope, headers: dict[str, str]) -> str | None:
        if same_secret(headers.get(TOKEN_HEADER), self.token):
            return "token"
        if (scope["type"] == "http" and scope.get("method", "GET") in COOKIE_METHODS
                and same_secret(_cookie(headers.get("cookie"), SESSION_COOKIE), self.cookie)):
            return "cookie"
        return None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                names = {k.lower() for k, _ in message.get("headers", [])}
                message["headers"] = [*message.get("headers", []),
                                      *((k, v) for k, v in SECURITY_HEADERS if k not in names)]
            await send(message)

        headers = {k.decode("latin-1"): v.decode("latin-1") for k, v in scope.get("headers", [])}
        if not host_allowed(headers.get("host"), self.allowed_hosts):
            await JSONResponse(status_code=400, content={"detail": "Unknown host."})(
                scope, receive, send_with_headers)
            return
        if scope.get("path", "").startswith("/api/"):
            method = scope.get("method", "GET")
            if scope["type"] == "http" and method not in SAFE_METHODS and not headers.get(APP_HEADER):
                await JSONResponse(status_code=403, content={"detail": "That request did not come from Flackey."})(
                    scope, receive, send_with_headers)
                return
            how = self.auth(scope, headers)
            if how is None:
                await JSONResponse(status_code=401, content={"detail": NEEDS_TOKEN})(
                    scope, receive, send_with_headers)
                return
            scope.setdefault("state", {})[AUTH_STATE] = how
        await self.app(scope, receive, send_with_headers)
