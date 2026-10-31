#!/usr/bin/env python3
"""A bounded load test of the PUBLIC demo (one free Render instance), from one address, with stop rules.

    python scripts/render-load.py --url https://fitwitness.onrender.com --pool 20 --steps 1,5,10,20 --checks 3,3,3,2 --output load.json

scripts/load-test.py measures capacity on a local server and says never to point it at the public demo, because the demo's
admission buckets are shared with real visitors for an hour: 100 workspaces and 200 runs an hour for everyone, 20
workspaces an hour per address. This script is the version that respects those buckets:

* it opens ``--pool`` workspaces once (at most 20, the per-address limit) and reuses them for every step, so a step of 20
  visitors costs no new workspaces;
* each visitor does what the browser does after opening a workspace, only the checks: POST /api/runs with the client's
  default query, then GET /api/runs/{id} and GET /api/runs/{id}/events once a second until the run ends;
* the steps raise the number of visitors working at once (``--steps``), each doing ``--checks`` checks (one number per step);
* ``--max-runs`` is a ceiling on the runs the whole test starts, well under the 200 an hour the demo allows;
* the test stops raising the load when a step has more than ``--max-error-rate`` of its requests failing (5xx, timeouts,
  dropped connections) or the slowest tenth of runs takes longer than ``--max-p95-seconds``, and says which rule stopped it;
* before the first step it waits for the server to wake (a free instance sleeps after 15 minutes) and times /health; it
  times /health again after the last step, so a server that does not come back shows.

It sends no header that changes the address it is counted under, and it is not a stress test: the largest step is 20
visitors from one address, which is as many as the per-address limit allows. What it shows is how a free instance
(0.1 CPU, 512 MB, a shared database) answers that, request by request. A 429 is the server refusing by design and is
counted apart from errors.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import random
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import httpx

_spec = importlib.util.spec_from_file_location("load_test", Path(__file__).with_name("load-test.py"))
load_test = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(load_test)
RUN_BODY, TERMINAL, percentile, outcome = load_test.RUN_BODY, load_test.TERMINAL, load_test.percentile, load_test.outcome


def ms(v):
    return None if v is None else round(v, 1)


async def wake(http: httpx.AsyncClient, seconds: int = 180) -> dict:
    """Wait until /health answers (a free instance sleeps); returns how long that took and the deployed commit."""
    started, last = time.perf_counter(), None
    while time.perf_counter() - started < seconds:
        try:
            r = await http.get("/health", timeout=30)
            if r.status_code == 200:
                return {"awake_after_seconds": round(time.perf_counter() - started, 1), "health": r.json()}
            last = r.status_code
        except httpx.HTTPError as exc:
            last = type(exc).__name__
        await asyncio.sleep(3)
    raise SystemExit(f"the server did not wake within {seconds} s (last answer: {last})")


async def health_times(http: httpx.AsyncClient, n: int = 5) -> dict:
    out = []
    for _ in range(n):
        t0 = time.perf_counter()
        try:
            r = await http.get("/health", timeout=30)
            out.append((r.status_code, (time.perf_counter() - t0) * 1000))
        except httpx.HTTPError as exc:
            out.append((type(exc).__name__, (time.perf_counter() - t0) * 1000))
        await asyncio.sleep(1)
    ok = [t for s, t in out if s == 200]
    return {"answers": [s for s, _ in out], "ms_median": ms(percentile(ok, 50)), "ms_max": ms(max(ok) if ok else None)}


async def open_pool(base: str, n: int) -> list[httpx.AsyncClient]:
    pool = []
    for _ in range(n):
        http = httpx.AsyncClient(base_url=base, headers={"Origin": base}, timeout=60)
        r = await http.post("/api/demo-sessions")
        if r.status_code != 200:
            await http.aclose()
            print(f"workspace {len(pool) + 1}: {r.status_code}; the pool stops at {len(pool)}", flush=True)
            break
        pool.append(http)
    return pool


async def check(http: httpx.AsyncClient, records: list[dict], step: int, budget: dict) -> dict:
    """One check as the browser makes it. Returns how it ended."""
    if budget["runs"] >= budget["max"]:
        return {"state": "not_started_budget"}

    async def call(endpoint: str, method: str, path: str, **kw):
        t0, status, error = time.perf_counter(), None, None
        try:
            resp = await http.request(method, path, **kw)
            status = resp.status_code
            return resp
        except httpx.HTTPError as exc:
            error = type(exc).__name__
            return None
        finally:
            records.append({"step": step, "endpoint": endpoint, "status": status, "error": error, "ms": (time.perf_counter() - t0) * 1000})

    t_check = time.perf_counter()
    budget["runs"] += 1
    resp = await call("POST /api/runs", "POST", "/api/runs", json=RUN_BODY, headers={"Idempotency-Key": str(uuid4())})
    if resp is None or resp.status_code >= 400:
        return {"state": "refused" if resp is not None and resp.status_code in (409, 429) else "error"}
    run = resp.json()
    deadline = time.perf_counter() + 120
    while run["state"] not in TERMINAL and time.perf_counter() < deadline:
        await asyncio.sleep(1.0)
        r1, r2 = await asyncio.gather(
            call("GET /api/runs/{id}", "GET", f"/api/runs/{run['id']}"),
            call("GET /api/runs/{id}/events", "GET", f"/api/runs/{run['id']}/events"),
        )
        if r1 is None or r1.status_code >= 400 or r2 is None or r2.status_code >= 400:
            return {"state": "error"}
        run = r1.json()
    return {"state": run["state"] if run["state"] in TERMINAL else "timeout", "seconds": time.perf_counter() - t_check}


async def visitor(http, checks: int, delay: float, records, step, budget) -> list[dict]:
    await asyncio.sleep(delay)
    done = []
    for _ in range(checks):
        done.append(await check(http, records, step, budget))
        await asyncio.sleep(random.uniform(0.5, 2.0))  # a person looks at the answer before asking again
    return done


def summarise(records: list[dict], outcomes: list[dict]) -> dict:
    by = {}
    for r in records:
        by.setdefault(r["endpoint"], []).append(r)
    kinds = {
        k: sum(1 for r in records if outcome(r["status"]) == k)
        for k in ("ok", "rejected", "client_error", "server_error", "transport_error")
    }
    seconds = [o["seconds"] for o in outcomes if o.get("state") == "completed"]
    return {
        "requests": len(records),
        "outcomes": kinds,
        "error_rate": round((kinds["server_error"] + kinds["transport_error"]) / max(1, len(records)), 4),
        "checks": {"started": sum(1 for o in outcomes if o["state"] != "not_started_budget"), "completed": len(seconds)},
        "check_states": {s: sum(1 for o in outcomes if o["state"] == s) for s in sorted({o["state"] for o in outcomes})},
        "check_seconds": {
            "p50": ms(percentile(seconds, 50)),
            "p95": ms(percentile(seconds, 95)),
            "max": ms(max(seconds) if seconds else None),
        },
        "endpoints": {
            e: {
                "n": len(v),
                "ms_p50": ms(percentile([r["ms"] for r in v if outcome(r["status"]) == "ok"], 50)),
                "ms_p95": ms(percentile([r["ms"] for r in v if outcome(r["status"]) == "ok"], 95)),
                "statuses": {str(s): sum(1 for r in v if r["status"] == s) for s in sorted({r["status"] for r in v if r["status"]})},
                "transport_errors": sum(1 for r in v if r["error"]),
            }
            for e, v in sorted(by.items())
        },
    }


async def main_async(args) -> dict:
    base = args.url.rstrip("/")
    async with httpx.AsyncClient(base_url=base, headers={"Origin": base}) as probe:
        report = {"url": base, "started": datetime.now(timezone.utc).isoformat(), "wake": await wake(probe)}
        report["health_before"] = await health_times(probe)
        pool = await open_pool(base, args.pool)
        report["pool"] = len(pool)
        steps, stopped = [], None
        budget = {"runs": 0, "max": args.max_runs}
        counts = [int(c) for c in args.checks.split(",")]
        for n, checks in zip((int(s) for s in args.steps.split(",")), counts):
            if n > len(pool):
                stopped = f"step {n}: only {len(pool)} workspaces could be opened"
                break
            records: list[dict] = []
            started = time.perf_counter()
            results = await asyncio.gather(*(visitor(pool[i], checks, 2.0 * i / n, records, n, budget) for i in range(n)))
            outcomes = [o for r in results for o in r]
            summary = summarise(records, outcomes)
            summary.update(visitors=n, checks_each=checks, wall_seconds=round(time.perf_counter() - started, 1))
            steps.append(summary)
            print(
                "== step",
                json.dumps(
                    {k: summary[k] for k in ("visitors", "checks_each", "wall_seconds", "error_rate", "check_seconds", "check_states")}
                ),
                flush=True,
            )
            if summary["error_rate"] > args.max_error_rate:
                stopped = f"after {n} visitors: {summary['error_rate']:.1%} of requests failed (rule: more than {args.max_error_rate:.0%})"
                break
            if (summary["check_seconds"]["p95"] or 0) > args.max_p95_seconds:  # check_seconds are seconds (the endpoint figures are ms)
                stopped = f"after {n} visitors: the slowest tenth of checks took {summary['check_seconds']['p95']:.1f} s (rule: over {args.max_p95_seconds} s)"
                break
            if budget["runs"] >= budget["max"]:
                stopped = f"the run ceiling of {budget['max']} was reached"
                break
        report.update(steps=steps, stopped_by=stopped, runs_started=budget["runs"], run_ceiling=budget["max"])
        await asyncio.sleep(5)
        report["health_after"] = await health_times(probe)
        for http in pool:
            await http.aclose()
    report["finished"] = datetime.now(timezone.utc).isoformat()
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", required=True)
    ap.add_argument("--pool", type=int, default=20)
    ap.add_argument("--steps", default="1,5,10,20")
    ap.add_argument("--checks", default="3,3,3,2")
    ap.add_argument("--max-runs", type=int, default=90)
    ap.add_argument("--max-error-rate", type=float, default=0.05)
    ap.add_argument("--max-p95-seconds", type=float, default=30.0)
    ap.add_argument("--allow-local-http", action="store_true", help="for a dry run against 127.0.0.1")
    ap.add_argument("--output", type=Path, required=True)
    args = ap.parse_args()
    if not args.url.startswith("https://") and not (args.allow_local_http and args.url.startswith("http://127.0.0.1")):
        raise SystemExit("https only (a dry run on 127.0.0.1 needs --allow-local-http)")
    assert 1 <= args.pool <= 20, "the per-address limit is 20 workspaces an hour"
    assert args.max_runs <= 100, "the demo allows 200 runs an hour to everyone; this test takes at most half"
    assert len(args.steps.split(",")) == len(args.checks.split(",")), "one number of checks per step"
    report = asyncio.run(main_async(args))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n")
    print("== done", json.dumps({k: report[k] for k in ("pool", "runs_started", "stopped_by")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
