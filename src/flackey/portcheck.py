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
_VERDICT = re.compile(r"IP:\s*([0-9.]+).*?Port:\s*(\d+)/tcp\s+(OPEN|CLOSED)",
                       re.DOTALL | re.IGNORECASE)


@dataclass(frozen=True)
class PortCheck:
    reachable: bool | None
    public_ip: str | None
    error: str | None = None


async def check_port(port: int, http: httpx.AsyncClient) -> PortCheck:
    try:
        r = await http.get(PORT_TEST_URL, params={"port": port}, timeout=15,
                           headers={"User-Agent": "Flackey (port check)"})
        r.raise_for_status()
    except httpx.HTTPError as e:
        log.info("port check unavailable: %s", e.__class__.__name__)
        return PortCheck(None, None, "Could not reach the port test service.")
    text = re.sub(r"<[^>]+>", " ", r.text)
    m = _VERDICT.search(text)
    if m is None or int(m.group(2)) != port:
        log.warning("port check page had no verdict for port %d", port)
        return PortCheck(None, None, "The port test gave an answer Flackey could not read.")
    return PortCheck(m.group(3).upper() == "OPEN", m.group(1))
