import time

import httpx
import pytest
import respx

from flackey.portcheck import PORT_TEST_URL, PortCheck, check_port

PAGE = ("<html><body><div>IP: <b>82.166.148.116</b></div><div>Port: 50300/tcp <b>{verdict}</b>. "
        "Your router and/or Soulseek client needs to be configured correctly.</div></body></html>")


@respx.mock
async def test_closed_port_is_reported_with_the_public_address():
    route = respx.get(PORT_TEST_URL).mock(return_value=httpx.Response(200, text=PAGE.format(verdict="CLOSED")))
    async with httpx.AsyncClient() as http:
        assert await check_port(50300, http) == PortCheck(False, "82.166.148.116")
    assert route.calls[0].request.url.params["port"] == "50300"


@respx.mock
async def test_open_port():
    respx.get(PORT_TEST_URL).mock(return_value=httpx.Response(200, text=PAGE.format(verdict="OPEN")))
    async with httpx.AsyncClient() as http:
        assert await check_port(50300, http) == PortCheck(True, "82.166.148.116")


@respx.mock
async def test_unreadable_page_and_network_errors_are_unknown_not_closed():
    respx.get(PORT_TEST_URL).mock(return_value=httpx.Response(200, text="<html>maintenance</html>"))
    async with httpx.AsyncClient() as http:
        r = await check_port(50300, http)
    assert r.reachable is None and r.public_ip is None and "could not read" in r.error
    respx.get(PORT_TEST_URL).mock(side_effect=httpx.ConnectError("down"))
    async with httpx.AsyncClient() as http:
        r = await check_port(50300, http)
    assert r.reachable is None and "Could not reach" in r.error


@respx.mock
async def test_only_the_start_of_a_huge_answer_is_read():
    """The answer comes over plain HTTP, so anyone on the same network can rewrite it: the page is read
    only as far as MAX_BODY_BYTES, never whole."""
    from flackey.portcheck import MAX_BODY_BYTES
    sent = []

    async def body():
        yield PAGE.format(verdict="OPEN").encode()
        for _ in range(1000):
            sent.append(1)
            yield b"<div>" + b"x" * 8192 + b"</div>"

    respx.get(PORT_TEST_URL).mock(return_value=httpx.Response(200, content=body()))
    async with httpx.AsyncClient() as http:
        assert await check_port(50300, http) == PortCheck(True, "82.166.148.116")
    assert len(sent) * 8192 <= MAX_BODY_BYTES + 16_384


# Named ids: the strings themselves would make test ids too long for an environment variable on Windows
JUNK = {"opens": "<", "addresses": "IP: 1", "open-tags": "<b ", "ports": "Port: 1", "spaces": " "}


@pytest.mark.parametrize("junk", JUNK.values(), ids=JUNK.keys())
@respx.mock
async def test_a_hostile_page_is_read_in_linear_time(junk):
    """A page as long as the cap, and one far past it, built to make a backtracking parse take seconds."""
    from flackey.portcheck import MAX_BODY_BYTES, read_verdict
    page = ("IP:" if junk == " " else "") + junk * (MAX_BODY_BYTES // len(junk))
    start = time.perf_counter()
    assert read_verdict(page) is None
    assert time.perf_counter() - start < 0.5
    respx.get(PORT_TEST_URL).mock(return_value=httpx.Response(200, text=page * 4))
    start = time.perf_counter()
    async with httpx.AsyncClient() as http:
        r = await check_port(50300, http)
    assert time.perf_counter() - start < 2
    assert r.reachable is None


@respx.mock
async def test_the_verdict_after_noise_and_tags_still_reads():
    page = "<p>IP:</p> <b>1.2.3.4</b> noise <i>Port:</i> 50300/tcp <b>CLOSED</b> " + PAGE.format(verdict="OPEN")
    respx.get(PORT_TEST_URL).mock(return_value=httpx.Response(200, text=page))
    async with httpx.AsyncClient() as http:
        # the first address on the page and the first verdict after it, as it always read
        assert await check_port(50300, http) == PortCheck(False, "1.2.3.4")
