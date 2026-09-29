"""A test client that asks the way the page does: a loopback Host and the app header (see `web.guard`)."""
from __future__ import annotations

from fastapi.testclient import TestClient

LOOPBACK = "http://127.0.0.1"


class AppClient(TestClient):
    def __init__(self, app, **kwargs):
        kwargs.setdefault("base_url", LOOPBACK)
        kwargs["headers"] = {"x-flackey-app": "1", **(kwargs.get("headers") or {})}
        super().__init__(app, **kwargs)
