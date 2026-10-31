"""A bounded probe of the public demo's per-address session limit.

    python scripts/probe_address_limit.py --url https://fitwitness.onrender.com

The demo lets one address open 20 workspaces an hour (``FITWITNESS_SESSION_LIMIT_PER_PEER``) on top of 100 an hour for
everyone. Behind Render's proxy the address the app counts comes from ``X-Forwarded-For``, and which entry of that header
uvicorn believes depends on a setting nobody here has seen (``FORWARDED_ALLOW_IPS``): if it believes the first one, a
visitor who writes the header themselves chooses the address that is counted and can step around the 20.

The probe opens workspaces from this one address until the limit refuses (at most 30 requests), then sends three more with
a header of its own making and one without. If the first refusal never comes, it says so and stops: the limit is higher
than 30 here, or the requests do not arrive from one address, and neither can be told apart from outside.

It uses a few of the shared 100 an hour and nothing else; it is not a load test.
"""

import argparse
import json
import time
import urllib.error
import urllib.request

MAX_PLAIN = 30


def create(base: str, xff: str | None = None) -> int:
    request = urllib.request.Request(base + "/api/demo-sessions", data=b"", method="POST", headers={"Origin": base})
    if xff:
        request.add_header("X-Forwarded-For", xff)
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            response.read()
            return response.status
    except urllib.error.HTTPError as error:
        error.read()
        return error.code


def probe(base: str) -> dict:
    out: dict = {"url": base, "started": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    statuses: list[int] = []
    for _ in range(MAX_PLAIN):
        statuses.append(create(base))
        if statuses[-1] == 429:
            break
    out["plain"] = {"sent": len(statuses), "first_refusal_at_request": statuses.index(429) + 1 if 429 in statuses else None}
    if 429 not in statuses:
        out["verdict"] = (
            "no refusal in %d requests: the limit is higher than that here, or the requests came from several addresses" % MAX_PLAIN
        )
        return out
    spoofed = {"one invented address": "198.51.100.7", "two invented addresses": "198.51.100.8, 203.0.113.9", "a third": "203.0.113.77"}
    out["with_a_header_of_my_own"] = {label: create(base, value) for label, value in spoofed.items()}
    out["plain_again"] = create(base)
    escaped = [label for label, status in out["with_a_header_of_my_own"].items() if status == 200]
    out["verdict"] = (
        "the limit holds: a header of my own making did not change the counted address"
        if not escaped and out["plain_again"] == 429
        else "the counted address follows the header I wrote: " + ", ".join(escaped or ["(plain request let through)"])
    )
    return out


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", required=True)
    args = parser.parse_args()
    print(json.dumps(probe(args.url.rstrip("/")), ensure_ascii=False, indent=1))
