"""Which address a visit is counted under, behind a proxy that writes X-Forwarded-For.

The demo lets one address open 20 workspaces an hour. Behind Render's proxy the address comes from ``X-Forwarded-For``,
and a client may write that header itself: the proxy adds the address it saw to the right of whatever the client sent,
so everything to the left of the proxy's entry is the client's own claim. uvicorn decides which entry it believes from
``FORWARDED_ALLOW_IPS``; with ``*`` it believes the first, and a visitor who sends ``X-Forwarded-For: <any address>`` is
then counted under that address, a new one for every request (observed on the public demo on 2026-10-31, run
41200322347: the 21st request from one address was refused, three more with a header of its own were let through).

So this reads the header itself and takes the rightmost *public* entry, the one a proxy added, and skips the entries an
infrastructure hop added after it (private and reserved addresses, and ``hops - 1`` more public ones when the operator
says the proxy chain is longer). An entry no proxy could have added, such as an invented documentation address with no
real one after it, falls back to the address the connection reports.

``FITWITNESS_TRUSTED_PROXY_HOPS`` sets how many trusted proxies stand in front: 0 reads no header at all (the address
the connection reports, as before this module), 1 is one proxy. Unset, it is 1 on Render (which sets ``RENDER``) and 0
anywhere else, so a direct deployment, the tests and the load test behave as they did.
"""

from __future__ import annotations

import ipaddress
import os


def trusted_proxy_hops() -> int:
    raw = os.getenv("FITWITNESS_TRUSTED_PROXY_HOPS", "").strip()
    if raw:
        hops = int(raw)  # a value that is not a number stops the start: a wrong guess here would silently change who is counted
        if hops < 0:
            raise ValueError("FITWITNESS_TRUSTED_PROXY_HOPS must be 0 or more")
        return hops
    return 1 if os.getenv("RENDER") else 0


def _entry(text: str):
    """The IP address an X-Forwarded-For entry names (an optional port or brackets removed), or None."""
    text = text.strip()
    if text.startswith("["):  # [2001:db8::1]:443
        text = text[1 : text.find("]")] if "]" in text else text[1:]
    elif text.count(":") == 1:  # 203.0.113.5:4430
        text = text.split(":")[0]
    try:
        return ipaddress.ip_address(text)
    except ValueError:
        return None


def address(forwarded_for: str | None, connection: str, hops: int) -> str:
    """The address to count: the rightmost public entry of the header after skipping the infrastructure hops, else ``connection``."""
    if hops <= 0 or not forwarded_for:
        return connection
    chain = [e for e in (part.strip() for part in forwarded_for.split(",")) if e]
    for text in reversed(chain[: max(0, len(chain) - (hops - 1))]):
        ip = _entry(text)
        if ip is not None and ip.is_global:
            return str(ip)
    return connection
