"""Ask the router to open the Soulseek listen port.

Two protocols, tried in order: NAT-PMP (RFC 6886, one UDP round trip to the gateway; Apple routers
and many others) and UPnP IGD (SSDP discovery, then one SOAP call to the WANIPConnection service;
most consumer routers). That is what the official Soulseek client and every BitTorrent client do,
and it is why their users have an open port without ever logging in to the router.

Everything here fails soft: a router that answers neither, a VPN, a carrier-grade NAT, a double
NAT -- all come back as None, and `sharing.py` then verifies from outside and tells the user what
to do by hand. No new dependencies: the two protocols are a few dozen bytes each.
"""

from __future__ import annotations

import asyncio
import logging
import re
import socket
import struct
import subprocess
import sys
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit
from xml.etree import ElementTree

import httpx

log = logging.getLogger(__name__)

NATPMP_PORT = 5351
SSDP_ADDR = ("239.255.255.250", 1900)
WAN_SERVICES = ("urn:schemas-upnp-org:service:WANIPConnection:2",
                "urn:schemas-upnp-org:service:WANIPConnection:1",
                "urn:schemas-upnp-org:service:WANPPPConnection:1")
IGD_TYPES = ("urn:schemas-upnp-org:device:InternetGatewayDevice:2",
             "urn:schemas-upnp-org:device:InternetGatewayDevice:1")
DESCRIPTION = "Flackey Soulseek"


@dataclass(frozen=True)
class Mapping:
    protocol: str            # "natpmp" | "upnp"
    gateway: str
    internal_port: int
    external_port: int
    lease_s: int
    control_url: str | None = None
    service_type: str | None = None


# ---- where the router is -------------------------------------------------------------------------

def default_gateway(run=subprocess.run) -> str | None:
    """The default route's next hop. `route -n get default` on macOS, `ip route` elsewhere. None
    when there is no default route or the command is missing; a VPN's utun default reports no
    gateway."""
    if sys.platform == "darwin":
        cmd, pattern = ["route", "-n", "get", "default"], r"gateway:\s*([0-9.]+)"
    else:
        cmd, pattern = ["ip", "route", "show", "default"], r"default via ([0-9.]+)"
    try:
        out = run(cmd, capture_output=True, text=True, timeout=3, check=False)
    except (OSError, subprocess.TimeoutExpired) as e:
        log.info("default gateway: %s failed (%s)", cmd[0], e.__class__.__name__)
        return None
    m = re.search(pattern, out.stdout or "")
    if m is None:
        log.info("default gateway: no default route in %s output", cmd[0])
        return None
    return m.group(1)


def lan_ip(gateway: str | None = None) -> str | None:
    """This machine's address on the router's network: the source address a UDP socket picks to
    reach the gateway. No packet is sent."""
    target = gateway or "10.255.255.255"
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect((target, 9))
            return s.getsockname()[0]
    except OSError as e:
        log.info("lan_ip: could not determine (%s)", e.__class__.__name__)
        return None


# ---- NAT-PMP -------------------------------------------------------------------------------------

async def _udp_exchange(gateway: str, payload: bytes, timeout: float) -> bytes:
    """One request, one reply, on the calling thread's behalf via to_thread so the loop never
    blocks."""
    def go() -> bytes:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(timeout)
            s.sendto(payload, (gateway, NATPMP_PORT))
            data, _ = s.recvfrom(64)
            return data
    return await asyncio.to_thread(go)


async def natpmp_map(
    gateway: str, port: int, lease_s: int, *, transport=_udp_exchange
) -> Mapping | None:
    """RFC 6886 3.3: opcode 2 is a TCP mapping; a reply's opcode is the request's plus 128 and
    result 0 means granted. The router may hand back a different public port; the mapping records
    it."""
    request = struct.pack("!BBHHHI", 0, 2, 0, port, port, lease_s)
    try:
        reply = await transport(gateway, request, 2.0)
    except (OSError, TimeoutError) as e:
        log.info("NAT-PMP: no answer from %s (%s)", gateway, e.__class__.__name__)
        return None
    if len(reply) < 16:
        log.info("NAT-PMP: %s sent a short reply (%d bytes)", gateway, len(reply))
        return None
    _, opcode, result, _, private, public, lifetime = struct.unpack("!BBHIHHI", reply[:16])
    if opcode != 130 or result != 0 or private != port:
        log.info("NAT-PMP: %s refused (opcode %d, result %d)", gateway, opcode, result)
        return None
    return Mapping("natpmp", gateway, port, public, lifetime)


# ---- UPnP ----------------------------------------------------------------------------------------

async def _ssdp_discover(timeout: float) -> list[str]:
    """LOCATION URLs of every Internet Gateway Device that answers an M-SEARCH, deduplicated."""
    def go() -> list[str]:
        found: list[str] = []
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP) as s:
            s.settimeout(timeout)
            for st in IGD_TYPES:
                msg = (f"M-SEARCH * HTTP/1.1\r\nHOST: {SSDP_ADDR[0]}:{SSDP_ADDR[1]}\r\n"
                       f'MAN: "ssdp:discover"\r\nMX: 2\r\nST: {st}\r\n\r\n').encode()
                s.sendto(msg, SSDP_ADDR)
            try:
                while True:
                    data, _ = s.recvfrom(2048)
                    m = re.search(rb"(?im)^LOCATION:\s*(\S+)", data)
                    if m:
                        loc = m.group(1).decode(errors="replace")
                        if loc not in found:
                            found.append(loc)
            except (TimeoutError, OSError):
                pass
        return found
    return await asyncio.to_thread(go)


def _find_wan_service(xml_text: str, base: str) -> tuple[str, str] | None:
    """(service_type, absolute controlURL) of the first WAN*Connection service in a device
    description."""
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError:
        return None
    ns = {"d": "urn:schemas-upnp-org:device-1-0"}
    for st in WAN_SERVICES:
        for svc in root.iter("{urn:schemas-upnp-org:device-1-0}service"):
            if (svc.findtext("d:serviceType", namespaces=ns) or "").strip() == st:
                url = (svc.findtext("d:controlURL", namespaces=ns) or "").strip()
                if url:
                    return st, urljoin(base, url)
    return None


def _soap(action: str, service_type: str, args: dict[str, str]) -> tuple[dict, str]:
    body = "".join(f"<{k}>{v}</{k}>" for k, v in args.items())
    envelope = (
        '<?xml version="1.0"?><s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
        's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
        f'<u:{action} xmlns:u="{service_type}">{body}</u:{action}></s:Body></s:Envelope>'
    )
    headers = {
        "Content-Type": 'text/xml; charset="utf-8"',
        "SOAPAction": f'"{service_type}#{action}"',
    }
    return headers, envelope


async def upnp_map(port: int, lease_s: int, http: httpx.AsyncClient, *, discover=_ssdp_discover,
                    internal_ip: str | None = None) -> Mapping | None:
    try:
        locations = await discover(3.0)
    except OSError as e:
        log.info("UPnP: discovery failed (%s)", e)
        return None
    if not locations:
        log.info("UPnP: no gateway answered")
        return None
    for loc in locations:
        gateway = urlsplit(loc).hostname or ""
        try:
            desc = await http.get(loc, timeout=5)
            desc.raise_for_status()
        except httpx.HTTPError as e:
            log.info("UPnP: could not read %s (%s)", loc, e.__class__.__name__)
            continue
        found = _find_wan_service(desc.text, loc)
        if found is None:
            log.info("UPnP: %s has no WAN*Connection service", gateway)
            continue
        service_type, control = found
        client_ip = internal_ip or lan_ip(gateway)
        if client_ip is None:
            log.info("UPnP: could not determine this machine's address on %s", gateway)
            continue
        headers, body = _soap("AddPortMapping", service_type, {
            "NewRemoteHost": "", "NewExternalPort": str(port), "NewProtocol": "TCP",
            "NewInternalPort": str(port), "NewInternalClient": client_ip, "NewEnabled": "1",
            "NewPortMappingDescription": DESCRIPTION, "NewLeaseDuration": str(lease_s)})
        try:
            r = await http.post(control, content=body, headers=headers, timeout=5)
        except httpx.HTTPError as e:
            log.info("UPnP: %s did not answer AddPortMapping (%s)", gateway, e.__class__.__name__)
            continue
        if r.status_code != 200:
            log.info("UPnP: %s refused AddPortMapping (%d)", gateway, r.status_code)
            continue
        return Mapping(
            "upnp", gateway, port, port, lease_s, control_url=control, service_type=service_type
        )
    return None


# ---- the two together ----------------------------------------------------------------------------

async def map_port(port: int, lease_s: int = 3600, http: httpx.AsyncClient | None = None, *,
                    gateway: str | None = None, natpmp=natpmp_map, upnp=upnp_map) -> Mapping | None:
    """NAT-PMP first (one packet, no discovery), UPnP second. `gateway` defaults to the default
    route's next hop; without one there is nobody to ask NAT-PMP, but SSDP is multicast and is
    still tried."""
    if gateway is None:
        gateway = await asyncio.to_thread(default_gateway)
    if gateway is not None:
        m = await natpmp(gateway, port, lease_s)
        if m is not None:
            log.info("port %d opened on %s via NAT-PMP for %ds", port, gateway, m.lease_s)
            return m
    own = http is None
    http = http or httpx.AsyncClient()
    try:
        m = await upnp(port, lease_s, http)
    finally:
        if own:
            await http.aclose()
    if m is not None:
        log.info("port %d opened on %s via UPnP for %ds", port, m.gateway, lease_s)
    return m


async def unmap_port(
    mapping: Mapping, http: httpx.AsyncClient | None = None, *, transport=_udp_exchange
) -> None:
    """Release on quit. Failure is silent by design: the lease expires on its own."""
    try:
        if mapping.protocol == "natpmp":
            request = struct.pack("!BBHHHI", 0, 2, 0, mapping.internal_port, 0, 0)
            await transport(mapping.gateway, request, 2.0)
        elif mapping.protocol == "upnp" and mapping.control_url and mapping.service_type:
            headers, body = _soap("DeletePortMapping", mapping.service_type, {
                "NewRemoteHost": "",
                "NewExternalPort": str(mapping.external_port),
                "NewProtocol": "TCP",
            })
            own = http is None
            http = http or httpx.AsyncClient()
            try:
                await http.post(mapping.control_url, content=body, headers=headers, timeout=5)
            finally:
                if own:
                    await http.aclose()
    except (OSError, TimeoutError, httpx.HTTPError) as e:
        log.info("could not release the port mapping (%s)", e.__class__.__name__)
