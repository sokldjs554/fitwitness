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
the connection reports, as before this module), 1 is one proxy, 2 is an edge in front of the platform's balancer. Unset,
it is 2 on Render (which sets ``RENDER``) and 0 anywhere else, so a direct deployment, the tests and the load test behave
as they did.

Counting hops was not enough on Render. With 1 the per-address limit stopped counting at all (run 41224993810: 30
workspaces from one address, no refusal; the rightmost entry changes from request to request). With 2 it held for most
requests but not all (run 41226556755: refused at the 23rd, then two headers of the probe's own making and one plain
request let through), so the number of edge entries after the visitor's own changes from request to request too.
Render is fronted by Cloudflare (its responses carry ``server: cloudflare`` and ``cf-ray``), and Cloudflare writes the
address it saw in ``CF-Connecting-IP``, replacing a header of that name a client sent. So on Render the visitor is that
header (``FITWITNESS_CLIENT_IP_HEADER``, ``none`` turns it off), and the X-Forwarded-For reading above is the fallback
when it is missing. ``shape`` describes a request's headers without any address in it, for the probe to check this from
outside after a deployment.
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
    return 2 if os.getenv("RENDER") else 0


def edge_client_header() -> str:
    """The header an edge network in front writes with the visitor's address (replacing one a client sent), or "" for none.
    Unset, it is ``cf-connecting-ip`` on Render and none anywhere else."""
    raw = os.getenv("FITWITNESS_CLIENT_IP_HEADER")
    if raw is not None:
        name = raw.strip().lower()
        return "" if name in ("", "none") else name
    return "cf-connecting-ip" if os.getenv("RENDER") else ""


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


def count_address(headers, connection: str, hops: int, edge_header: str) -> str:
    """The address a visit is counted under: the edge network's header when it names a public address, else ``address``."""
    if edge_header:
        ip = _entry(headers.get(edge_header) or "")
        if ip is not None and ip.is_global:
            return str(ip)
    return address(headers.get("x-forwarded-for"), connection, hops)


def shape(headers, connection: str, hops: int, edge_header: str) -> dict:
    """What a request's address headers look like, with no address in the answer: how many X-Forwarded-For entries and
    of which kind, whether the edge header is there, and where the counted address came from."""
    chain = [e for e in (part.strip() for part in (headers.get("x-forwarded-for") or "").split(",")) if e]
    kinds = []
    for text in chain:
        ip = _entry(text)
        kinds.append("invalid" if ip is None else "public" if ip.is_global else "not public")
    edge = _entry(headers.get(edge_header) or "") if edge_header else None
    counted = count_address(headers, connection, hops, edge_header)
    from_right = None
    for i, text in enumerate(reversed(chain)):
        ip = _entry(text)
        if ip is not None and str(ip) == counted:
            from_right = i
            break
    source = "edge header" if edge is not None and edge.is_global else "connection" if counted == connection else "x-forwarded-for"
    return {
        "forwarded_for_entries": kinds,
        "edge_header": edge_header or None,
        "edge_header_present": bool(edge_header and headers.get(edge_header)),
        "edge_header_is_public": bool(edge is not None and edge.is_global),
        "edge_header_is_in_forwarded_for": bool(edge is not None and any(_entry(t) == edge for t in chain)),
        "counted_from": source,
        "counted_entry_from_the_right": from_right,
        "trusted_hops": hops,
    }
