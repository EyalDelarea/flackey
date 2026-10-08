import struct

import httpx
import pytest
import respx

from flackey import portmap
from flackey.portmap import Mapping, default_gateway, map_port, natpmp_map, unmap_port, upnp_map


def test_default_gateway_parses_route_output_on_mac(monkeypatch):
    monkeypatch.setattr(portmap.sys, "platform", "darwin")

    class R:
        stdout = "   route to: default\ndestination: default\n       mask: default\n    gateway: 10.0.0.1\n  interface: en0\n"
        returncode = 0

    assert default_gateway(run=lambda *a, **k: R()) == "10.0.0.1"


def test_default_gateway_is_none_when_the_command_fails():
    def boom(*a, **k):
        raise OSError("no route")
    assert default_gateway(run=boom) is None


# `route print -4 0.0.0.0` as a German Windows 11 prints it with a WireGuard tunnel up: two active default
# routes (the tunnel's at a *lower* metric, so it is the one in use), an on-link row, and a persistent
# route whose last column is a word. Every label is translated, which is why the parser keys on the shape
# of a row and never on a header.
WINDOWS_ROUTE_PRINT = """\
===========================================================================
Schnittstellenliste
 12...00 15 5d 01 02 03 ......Intel(R) Wi-Fi 6 AX201 160MHz
 23...........................WireGuard Tunnel
  1...........................Software Loopback Interface 1
===========================================================================

IPv4-Routentabelle
===========================================================================
Aktive Routen:
     Netzwerkziel    Netzwerkmaske          Gateway    Schnittstelle Metrik
          0.0.0.0          0.0.0.0      192.168.1.1     192.168.1.23     35
          0.0.0.0          0.0.0.0         10.8.0.1         10.8.0.6      5
          0.0.0.0          0.0.0.0   Auf Verbindung       10.66.66.2      0
===========================================================================
Ständige Routen:
  Netzwerkadresse          Netzmaske  Gatewayadresse  Metrik
          0.0.0.0          0.0.0.0    192.168.1.254  Standard
===========================================================================
"""


def test_default_gateway_on_windows_takes_the_lowest_metric_active_route(monkeypatch):
    """Only the five-column active rows with an address for a gateway count, and among those the lowest
    metric is the route Windows actually uses."""
    monkeypatch.setattr(portmap.sys, "platform", "win32")
    seen = {}

    class R:
        stdout = WINDOWS_ROUTE_PRINT
        returncode = 0

    def run(cmd, **kw):
        seen.update(cmd=cmd, **kw)
        return R()

    assert default_gateway(run=run) == "10.8.0.1"
    assert seen["cmd"] == ["route", "print", "-4", "0.0.0.0"]
    # No console flash from a windowed app, and a byte the OEM code page cannot decode is not an error.
    assert seen["creationflags"] == 0x08000000 and seen["errors"] == "replace"


def test_default_gateway_on_windows_ignores_persistent_and_on_link_rows(monkeypatch):
    monkeypatch.setattr(portmap.sys, "platform", "win32")
    only_those = "\n".join(line for line in WINDOWS_ROUTE_PRINT.splitlines()
                           if "192.168.1.1 " not in line and "10.8.0.1 " not in line)

    class R:
        stdout = only_those
        returncode = 0

    assert default_gateway(run=lambda *a, **k: R()) is None


def test_default_gateway_on_mac_passes_no_windows_flags(monkeypatch):
    """The Mac call is the call it always was: POSIX Popen would refuse `creationflags`."""
    monkeypatch.setattr(portmap.sys, "platform", "darwin")
    seen = {}

    class R:
        stdout = "gateway: 10.0.0.1\n"
        returncode = 0

    def run(cmd, **kw):
        seen.update(kw)
        return R()

    default_gateway(run=run)
    assert seen == {"capture_output": True, "text": True, "timeout": 3, "check": False}


async def test_natpmp_map_sends_a_tcp_mapping_request_and_reads_the_reply():
    sent = []

    async def transport(gateway, payload, timeout):
        sent.append((gateway, payload))
        # version 0, opcode 128+2, result 0, epoch, private port, mapped public port, lifetime
        return struct.pack("!BBHIHHI", 0, 130, 0, 1234, 50300, 50300, 3600)

    m = await natpmp_map("10.0.0.1", 50300, 3600, transport=transport)
    assert m == Mapping("natpmp", "10.0.0.1", 50300, 50300, 3600)
    gateway, payload = sent[0]
    assert gateway == "10.0.0.1"
    assert payload == struct.pack("!BBHHHI", 0, 2, 0, 50300, 50300, 3600)


async def test_natpmp_map_returns_none_on_error_result_or_no_reply():
    async def refused(gateway, payload, timeout):
        return struct.pack("!BBHIHHI", 0, 130, 2, 1234, 50300, 0, 0)   # result 2 = not authorized

    async def silent(gateway, payload, timeout):
        raise TimeoutError

    assert await natpmp_map("10.0.0.1", 50300, 3600, transport=refused) is None
    assert await natpmp_map("10.0.0.1", 50300, 3600, transport=silent) is None


DESC = """<?xml version="1.0"?><root xmlns="urn:schemas-upnp-org:device-1-0">
<device><friendlyName>Test Router</friendlyName><deviceList><device><deviceList><device>
<serviceList><service><serviceType>urn:schemas-upnp-org:service:WANIPConnection:1</serviceType>
<controlURL>/ctl/IPConn</controlURL></service></serviceList>
</device></deviceList></device></deviceList></device></root>"""


@respx.mock
async def test_upnp_map_finds_the_wan_service_and_posts_add_port_mapping():
    respx.get("http://10.0.0.1:1900/desc.xml").mock(return_value=httpx.Response(200, text=DESC))
    soap = respx.post("http://10.0.0.1:1900/ctl/IPConn").mock(return_value=httpx.Response(200, text="<ok/>"))

    async def discover(timeout):
        return [("http://10.0.0.1:1900/desc.xml", "10.0.0.1")]

    async with httpx.AsyncClient() as http:
        m = await upnp_map(50300, 3600, http, discover=discover, internal_ip="10.0.0.5")
    assert m == Mapping("upnp", "10.0.0.1", 50300, 50300, 3600,
                        control_url="http://10.0.0.1:1900/ctl/IPConn",
                        service_type="urn:schemas-upnp-org:service:WANIPConnection:1")
    body = soap.calls[0].request.content.decode()
    assert "<NewExternalPort>50300</NewExternalPort>" in body and "<NewInternalClient>10.0.0.5</NewInternalClient>" in body
    assert "<NewProtocol>TCP</NewProtocol>" in body and "<NewLeaseDuration>3600</NewLeaseDuration>" in body
    assert soap.calls[0].request.headers["SOAPAction"] == '"urn:schemas-upnp-org:service:WANIPConnection:1#AddPortMapping"'


@respx.mock
async def test_upnp_map_returns_none_when_the_router_refuses():
    respx.get("http://10.0.0.1:1900/desc.xml").mock(return_value=httpx.Response(200, text=DESC))
    respx.post("http://10.0.0.1:1900/ctl/IPConn").mock(return_value=httpx.Response(500, text="<fault/>"))

    async def discover(timeout):
        return [("http://10.0.0.1:1900/desc.xml", "10.0.0.1")]

    async with httpx.AsyncClient() as http:
        assert await upnp_map(50300, 3600, http, discover=discover, internal_ip="10.0.0.5") is None


async def test_upnp_map_returns_none_when_nothing_answers_ssdp():
    async def discover(timeout):
        return []

    async with httpx.AsyncClient() as http:
        assert await upnp_map(50300, 3600, http, discover=discover, internal_ip="10.0.0.5") is None


@respx.mock
async def test_upnp_map_tries_the_next_gateway_when_lan_ip_fails_for_the_first(monkeypatch):
    respx.get("http://10.0.0.1:1900/desc.xml").mock(return_value=httpx.Response(200, text=DESC))
    respx.get("http://10.0.0.2:1900/desc.xml").mock(return_value=httpx.Response(200, text=DESC))
    soap = respx.post("http://10.0.0.2:1900/ctl/IPConn").mock(return_value=httpx.Response(200, text="<ok/>"))

    def fake_lan_ip(gateway=None):
        return None if gateway == "10.0.0.1" else "10.0.0.5"

    monkeypatch.setattr(portmap, "lan_ip", fake_lan_ip)

    async def discover(timeout):
        return [("http://10.0.0.1:1900/desc.xml", "10.0.0.1"), ("http://10.0.0.2:1900/desc.xml", "10.0.0.2")]

    async with httpx.AsyncClient() as http:
        m = await upnp_map(50300, 3600, http, discover=discover, internal_ip=None)
    assert m is not None and m.gateway == "10.0.0.2"
    assert soap.calls[0].request.content.decode().count("<NewInternalClient>10.0.0.5</NewInternalClient>") == 1


def _discover(*pairs):
    async def discover(timeout):
        return list(pairs)
    return discover


@respx.mock
async def test_upnp_map_only_talks_to_the_default_gateway_when_it_is_known():
    """Any machine on the network can answer an SSDP search. With the gateway known, only an answer
    describing the gateway itself is used -- a second responder is never contacted."""
    respx.get("http://10.0.0.1:1900/desc.xml").mock(return_value=httpx.Response(200, text=DESC))
    soap = respx.post("http://10.0.0.1:1900/ctl/IPConn").mock(return_value=httpx.Response(200, text="<ok/>"))
    other = respx.get("http://10.0.0.77:1900/desc.xml").mock(return_value=httpx.Response(200, text=DESC))
    discover = _discover(("http://10.0.0.77:1900/desc.xml", "10.0.0.77"),
                         ("http://10.0.0.1:1900/desc.xml", "10.0.0.1"))

    async with httpx.AsyncClient() as http:
        m = await upnp_map(50300, 3600, http, discover=discover, internal_ip="10.0.0.5", gateway="10.0.0.1")
    assert m is not None and m.gateway == "10.0.0.1" and soap.call_count == 1
    assert other.call_count == 0


@respx.mock
@pytest.mark.parametrize("location, source", [
    ("http://10.0.0.9:1900/desc.xml", "10.0.0.1"),         # points somewhere other than who answered
    ("http://93.184.216.34:1900/desc.xml", "93.184.216.34"),   # a public address
    ("https://10.0.0.1:1900/desc.xml", "10.0.0.1"),        # not plain http
    ("http://router.local:1900/desc.xml", "10.0.0.1"),     # a name, not an address
    ("http://127.0.0.1:1900/desc.xml", "127.0.0.1"),       # this machine, not a router
])
async def test_upnp_map_ignores_a_location_that_is_not_the_responding_router(location, source):
    route = respx.get(location).mock(return_value=httpx.Response(200, text=DESC))
    async with httpx.AsyncClient() as http:
        assert await upnp_map(50300, 3600, http, discover=_discover((location, source)),
                              internal_ip="10.0.0.5") is None
    assert route.call_count == 0


@respx.mock
@pytest.mark.parametrize("control", ["http://10.0.0.9:1900/ctl/IPConn", "https://10.0.0.1/ctl/IPConn",
                                     "//evil.example/ctl"])
async def test_upnp_map_refuses_a_control_url_on_another_host(control):
    desc = DESC.replace("<controlURL>/ctl/IPConn</controlURL>", f"<controlURL>{control}</controlURL>")
    respx.get("http://10.0.0.1:1900/desc.xml").mock(return_value=httpx.Response(200, text=desc))
    posts = respx.post(url__regex=r".*").mock(return_value=httpx.Response(200, text="<ok/>"))
    async with httpx.AsyncClient() as http:
        assert await upnp_map(50300, 3600, http, discover=_discover(("http://10.0.0.1:1900/desc.xml", "10.0.0.1")),
                              internal_ip="10.0.0.5") is None
    assert posts.call_count == 0


@respx.mock
async def test_upnp_map_does_not_follow_a_redirect(monkeypatch):
    respx.get("http://10.0.0.1:1900/desc.xml").mock(
        return_value=httpx.Response(302, headers={"Location": "http://10.0.0.9/desc.xml"}))
    elsewhere = respx.get("http://10.0.0.9/desc.xml").mock(return_value=httpx.Response(200, text=DESC))
    async with httpx.AsyncClient(follow_redirects=True) as http:
        assert await upnp_map(50300, 3600, http, discover=_discover(("http://10.0.0.1:1900/desc.xml", "10.0.0.1")),
                              internal_ip="10.0.0.5") is None
    assert elsewhere.call_count == 0


async def test_map_port_hands_the_gateway_to_upnp():
    seen = {}

    async def natpmp(gateway, port, lease_s, **kw):
        return None

    async def upnp(port, lease_s, http, **kw):
        seen.update(kw)

    await map_port(50300, 3600, gateway="10.0.0.1", natpmp=natpmp, upnp=upnp)
    assert seen.get("gateway") == "10.0.0.1"


async def test_map_port_tries_natpmp_first_then_upnp():
    calls = []

    async def natpmp(gateway, port, lease_s, **kw):
        calls.append("natpmp")

    async def upnp(port, lease_s, http, **kw):
        calls.append("upnp")
        return Mapping("upnp", "10.0.0.1", port, port, lease_s, control_url="u", service_type="s")

    m = await map_port(50300, 3600, gateway="10.0.0.1", natpmp=natpmp, upnp=upnp)
    assert m is not None and m.protocol == "upnp" and calls == ["natpmp", "upnp"]


async def test_map_port_skips_natpmp_without_a_gateway(monkeypatch):
    monkeypatch.setattr(portmap, "default_gateway", lambda: None)

    async def natpmp(*a, **k):
        raise AssertionError("must not be called")

    async def upnp(port, lease_s, http, **kw):
        return None

    assert await map_port(50300, 3600, gateway=None, natpmp=natpmp, upnp=upnp) is None


async def test_unmap_sends_a_zero_lifetime_natpmp_request():
    sent = []

    async def transport(gateway, payload, timeout):
        sent.append(payload)
        return struct.pack("!BBHIHHI", 0, 130, 0, 1, 50300, 0, 0)

    await unmap_port(Mapping("natpmp", "10.0.0.1", 50300, 50300, 3600), transport=transport)
    assert sent[0] == struct.pack("!BBHHHI", 0, 2, 0, 50300, 0, 0)


@respx.mock
async def test_unmap_posts_delete_port_mapping_for_upnp():
    soap = respx.post("http://10.0.0.1:1900/ctl/IPConn").mock(return_value=httpx.Response(200, text="<ok/>"))
    m = Mapping("upnp", "10.0.0.1", 50300, 50300, 3600, control_url="http://10.0.0.1:1900/ctl/IPConn",
                service_type="urn:schemas-upnp-org:service:WANIPConnection:1")
    async with httpx.AsyncClient() as http:
        await unmap_port(m, http)
    assert "DeletePortMapping" in soap.calls[0].request.headers["SOAPAction"]
