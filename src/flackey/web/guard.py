"""Who may talk to the API: the two checks every request passes before it reaches a route.

The API has no login -- it is the owner's own machine -- so what keeps a website the owner visits from
driving it is the browser's same-origin policy. Two holes in that policy are closed here:

* DNS rebinding. A page on evil.example re-points its own name at 127.0.0.1 and is then same-origin with
  Flackey, so CORS never applies. The browser still sends `Host: evil.example`, so a request whose Host
  is a *name* other than localhost (or one the owner allowed) is refused. An IP literal cannot be
  rebound, which is what lets a Docker owner reach the UI by the host's address without configuring
  anything.
* Blind cross-site requests. Any page can fire a body-less POST at localhost without a preflight; it
  cannot read the answer, but the route still runs. Every state-changing request must carry
  `APP_HEADER`, and a custom header is what forces the preflight no other origin passes.
"""
from __future__ import annotations

import ipaddress

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

APP_HEADER = "x-flackey-app"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
LOOPBACK_NAMES = frozenset({"localhost"})


def from_the_app(request: Request) -> None:
    """Refuse a request some other page made the browser send.

    The middleware already demands the header on every unsafe method; routes that *read* a secret call
    this too, so a GET for one needs the header as well. Not an `Origin` check: a WKWebView on http://
    and a dev-server proxy make that unguessable here."""
    if not request.headers.get(APP_HEADER):
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
    """Refuse a rebound Host on every path, and an unsafe method without `APP_HEADER` on the API."""

    def __init__(self, app: ASGIApp, allowed_hosts: frozenset[str] = frozenset()) -> None:
        self.app = app
        self.allowed_hosts = frozenset(h.lower() for h in allowed_hosts)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1"): v.decode("latin-1") for k, v in scope.get("headers", [])}
        if not host_allowed(headers.get("host"), self.allowed_hosts):
            await JSONResponse(status_code=400, content={"detail": "Unknown host."})(scope, receive, send)
            return
        method = scope.get("method", "GET")
        if (scope["type"] == "http" and method not in SAFE_METHODS and scope.get("path", "").startswith("/api/")
                and not headers.get(APP_HEADER)):
            await JSONResponse(status_code=403, content={"detail": "That request did not come from Flackey."})(
                scope, receive, send)
            return
        await self.app(scope, receive, send)
