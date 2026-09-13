import httpx
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
