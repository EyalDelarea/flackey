"""Is the Soulseek listen port reachable from the internet?

Asked of the Soulseek project's own port test, the one the official client and Nicotine+ use: it
connects back to the caller's public address on the given port and says OPEN or CLOSED. Asking from
inside would not work -- most home routers do not hairpin -- and it has to be the address the Soulseek
server sees, which through a VPN is the VPN's exit. This is a plain HTML page, so the parse is pinned to
the two phrases it has carried for years and anything else is reported as "could not tell", never as
closed.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import httpx

log = logging.getLogger(__name__)
PORT_TEST_URL = "http://tools.slsknet.org/porttest.php"
# The page is a few hundred bytes. It comes over plain HTTP, so anything on the way can answer instead:
# only this much of it is read, and it is parsed in two linear steps -- the address, then the first
# verdict after it, which is what one backtracking pattern used to find.
MAX_BODY_BYTES = 64 * 1024
_TAG = re.compile(r"<[^<>]+>")
_ADDRESS = re.compile(r"IP:\s*([0-9.]+)", re.IGNORECASE)
_VERDICT = re.compile(r"Port:\s*(\d+)/tcp\s+(OPEN|CLOSED)", re.IGNORECASE)


@dataclass(frozen=True)
class PortCheck:
    reachable: bool | None
    public_ip: str | None
    error: str | None = None


def read_verdict(page: str) -> tuple[str, int, bool] | None:
    """The public address, the port and whether it is open, as the page says; None if it does not."""
    text = _TAG.sub(" ", page)
    address = _ADDRESS.search(text)
    verdict = address and _VERDICT.search(text, address.end())
    if not verdict:
        return None
    return address.group(1), int(verdict.group(1)), verdict.group(2).upper() == "OPEN"


async def _read_capped(http: httpx.AsyncClient, port: int) -> str:
    async with http.stream("GET", PORT_TEST_URL, params={"port": port}, timeout=15,
                           headers={"User-Agent": "Flackey (port check)"}) as r:
        r.raise_for_status()
        body = bytearray()
        async for chunk in r.aiter_bytes():
            body += chunk
            if len(body) >= MAX_BODY_BYTES:
                break
        return bytes(body[:MAX_BODY_BYTES]).decode(r.encoding or "utf-8", errors="replace")


async def check_port(port: int, http: httpx.AsyncClient) -> PortCheck:
    try:
        page = await _read_capped(http, port)
    except httpx.HTTPError as e:
        log.info("port check unavailable: %s", e.__class__.__name__)
        return PortCheck(None, None, "Could not reach the port test service.")
    found = read_verdict(page)
    if found is None or found[1] != port:
        log.warning("port check page had no verdict for port %d", port)
        return PortCheck(None, None, "The port test gave an answer Flackey could not read.")
    return PortCheck(found[2], found[0])
