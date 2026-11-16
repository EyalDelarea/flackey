"""What was blocked or failed, as fixed codes only (owner, 2026-11-16: "if something is blocked, I want to
know what and why"). Codes come from exception types, HTTP statuses and outcome codes; never from a message,
a title, a URL or anything a peer or a site wrote."""
import errno
import json
import sys
import types

import httpx
import pytest

from flackey import catalog
from flackey.catalog import BeatportCatalog, CatalogUnavailable, network_reason, refusal_reason
from flackey.source.base import SourceError, SourceNotFound, SourceTimeout, SourceUnauthorized
from flackey.telemetry.schema import SCHEMA_PATH
from flackey.worker import usage

SCHEMA = json.loads(SCHEMA_PATH.read_text())


def test_every_code_the_app_can_send_is_in_the_schema_enum():
    for event, field in (("request_finished", "failure_reason"), ("request_finished", "catalog_reason"),
                         ("source_status_changed", "reason")):
        assert set(usage.REASONS) == set(SCHEMA["events"][event][field]["enum"]), (event, field)
    assert set(usage.OUTCOME_REASON.values()) <= set(usage.REASONS)
    assert usage.outcome_reason("no_pick", no_results=True) == "no_results"
    assert usage.outcome_reason("no_pick") == "no_pick"
    assert usage.outcome_reason("something new") == "unknown"


@pytest.mark.parametrize(("exc", "code"), [
    (SourceUnauthorized("bot wants a login for /Users/me"), "not_logged_in"),
    (SourceNotFound("Nothing for Astral Projection"), "no_results"),
    (SourceTimeout("x"), "timeout"),
    (SourceError("x"), "source_error"),
    (httpx.ConnectTimeout("x"), "timeout"),
    (httpx.ConnectError("x"), "network"),
    (OSError(errno.ENOSPC, "No space left on /Volumes/DJ"), "disk_full"),
    (PermissionError(errno.EACCES, "/Users/me/Music"), "filesystem"),
    (CatalogUnavailable("beatport http 403", "cloudflare_challenge"), "cloudflare_challenge"),
    (CatalogUnavailable("x", "made-up"), "unknown"),
    (KeyError("/Users/me/Music/secret.aiff"), "internal_error"),
    (RuntimeError("anything at all"), "internal_error"),
])
def test_an_exception_becomes_a_code_by_its_type_never_its_message(exc, code):
    assert usage.reason_of(exc) == code
    assert usage.reason_of(exc) in usage.REASONS


def test_a_status_error_is_coded_by_its_status():
    req = httpx.Request("GET", "https://example.invalid/secret-track")
    for status, code in ((403, "http_403"), (429, "http_429"), (502, "http_5xx"), (404, "http_other")):
        exc = httpx.HTTPStatusError("boom", request=req, response=httpx.Response(status, request=req))
        assert usage.reason_of(exc) == code


def test_cloudflare_is_told_apart_from_a_plain_403():
    assert refusal_reason(403, {"cf-mitigated": "challenge"}, "") == "cloudflare_challenge"
    assert refusal_reason(403, {}, "<script src='/cdn-cgi/challenge-platform/h/b'>") == "cloudflare_challenge"
    assert refusal_reason(403, {"Server": "nginx"}, "Forbidden") == "http_403"
    assert refusal_reason(429, {}, "") == "http_429"
    assert refusal_reason(503, {}, "") == "http_5xx"


class FakeResponse:
    def __init__(self, status, headers=None, text=""):
        self.status_code, self.headers, self.text = status, headers or {}, text


@pytest.mark.parametrize(("resp", "code"), [
    (FakeResponse(403, {"cf-mitigated": "challenge"}, "Just a moment..."), "cloudflare_challenge"),
    (FakeResponse(403, {}, "Forbidden"), "http_403"),
    (FakeResponse(503), "http_5xx"),
])
def test_beatport_refusals_carry_their_reason(monkeypatch, resp, code):
    fake = types.ModuleType("curl_cffi")
    fake.requests = types.SimpleNamespace(get=lambda *a, **k: resp)
    monkeypatch.setitem(sys.modules, "curl_cffi", fake)
    with pytest.raises(CatalogUnavailable) as e:
        BeatportCatalog()._get("https://www.beatport.com/search?q=x")
    assert e.value.reason == code


def test_a_network_failure_is_a_timeout_or_network_by_type():
    class ReadTimeout(Exception):
        pass

    assert network_reason(ReadTimeout("read timed out after 30 s")) == "timeout"
    assert network_reason(httpx.ConnectTimeout("x")) == "timeout"
    assert network_reason(ConnectionResetError("reset by peer 1.2.3.4")) == "network"
    assert catalog.CatalogUnavailable("x").reason == "network"
