import struct

import httpx
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
        return ["http://10.0.0.1:1900/desc.xml"]

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
        return ["http://10.0.0.1:1900/desc.xml"]

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
        return ["http://10.0.0.1:1900/desc.xml", "http://10.0.0.2:1900/desc.xml"]

    async with httpx.AsyncClient() as http:
        m = await upnp_map(50300, 3600, http, discover=discover, internal_ip=None)
    assert m is not None and m.gateway == "10.0.0.2"
    assert soap.calls[0].request.content.decode().count("<NewInternalClient>10.0.0.5</NewInternalClient>") == 1


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
