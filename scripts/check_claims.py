"""Run every demo claim case of one workspace through the API and wait for each to settle (stdlib only).

    python scripts/check_claims.py --url http://app:8787

Used by the offline container check: the claims path (reading, rules, counterexample review, payout ledger) must
finish without any outside connection. A case either completes or waits for a person (amount above the auto limit,
a missing grade); anything else fails the check."""
import argparse
import json
import time
from http.cookiejar import CookieJar
from urllib.request import HTTPCookieProcessor, Request, build_opener


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    base = ap.parse_args().url.rstrip("/")
    client = build_opener(HTTPCookieProcessor(CookieJar()))

    def call(path, data=None):
        body = None if data is None else json.dumps(data).encode()
        req = Request(base + path, data=body, method="GET" if data is None else "POST",
                      headers={"Origin": base, "Content-Type": "application/json"})
        with client.open(req, timeout=60) as r:
            return json.loads(r.read())

    call("/api/demo-sessions", {})
    settled = {}
    for case in call("/api/claims/cases"):
        run = call(f"/api/claims/cases/{case['case_id']}/run", {})
        for _ in range(180):
            run = call(f"/api/runs/{run['id']}")
            if run["state"] not in ("queued", "running", "retry_wait"):
                break
            time.sleep(1)
        settled[case["case_id"]] = {"scenario": case["scenario"], "state": run["state"]}
    print(json.dumps(settled, ensure_ascii=False))
    assert settled and all(v["state"] in ("completed", "waiting_input") for v in settled.values()), settled
    print(f"{len(settled)} claim cases settled without an outside connection")


if __name__ == "__main__":
    main()
