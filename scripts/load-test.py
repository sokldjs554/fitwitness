"""Virtual demo visitors against a running FitWitness API, step by step.

A visitor does what the browser client does (web/src/main.tsx): open a workspace (POST /api/demo-sessions,
which seeds 20 drawings and 8 claims into a new tenant), list the documents, load the selected drawing's PNG,
then run the default check a number of times (POST /api/runs with the client's default query, conditions and
an Idempotency-Key; poll GET /api/runs/{id} and GET /api/runs/{id}/events once a second until the run leaves
queued/running). Arrivals are spread evenly over the first ``--ramp`` seconds of a step, and each visitor sends
its own X-Forwarded-For address (uvicorn trusts it from 127.0.0.1), so the per-address session limit counts
visitors one by one as it would for people on different networks.

Every request is timed. 429 (admission: sessions or runs per hour) and 409 (a tenant's concurrent-run limit) are
the server saying no by design and are counted apart from 5xx and transport errors. With ``--dsn`` the script
reads, after each step, when each of its runs was queued, when a worker wrote its ``started`` event (the wait
for a worker) and when its last event was written (the work); with ``--reset-admission`` it also empties the hourly admission
buckets before each step so a step measures capacity rather than the quota left by the previous one.

    python scripts/load-test.py --url http://127.0.0.1:8787 --steps 1,10,25,50,100 --checks 2 \\
        --dsn "$FITWITNESS_DATABASE_URL" --reset-admission --output docs/evaluation/load-local.json

Never point this at the public demo: its admission buckets are shared with real visitors for an hour.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import random
import time
from collections import defaultdict
from pathlib import Path
from uuid import uuid4

import httpx

TERMINAL = {"completed", "failed", "cancelled", "stale", "waiting_input"}
# The client's defaults (web/src/main.tsx): the guided bracket story.
RUN_BODY = {
    "search": {
        "text": "장비에 고정할 브래킷을 찾아줘",
        "requirements": [
            {"field": "hole_spacing", "value": {"low": "40", "high": "40"}, "unit": "mm"},
            {"field": "material", "value": "SUS304"},
        ],
    },
    "provider": "rules",
    "review": "none",
}


def percentile(values: list[float], q: float) -> float | None:
    """Nearest-rank percentile (q in 0..100); None for no values."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(q / 100 * len(ordered)))
    return ordered[rank - 1]


def outcome(status: int | None) -> str:
    if status is None:
        return "transport_error"
    if status in (429, 409):
        return "rejected"
    if status >= 500:
        return "server_error"
    if status >= 400:
        return "client_error"
    return "ok"


def summarise(records: list[dict], journeys: list[dict]) -> dict:
    """Per endpoint: count, outcomes, latency percentiles of the successful requests (ms). Per journey: the same
    for the visitors that finished every step."""
    by_endpoint: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        by_endpoint[r["endpoint"]].append(r)
    endpoints = {}
    for name, rows in sorted(by_endpoint.items()):
        ok = [r["ms"] for r in rows if outcome(r["status"]) == "ok"]
        counts = defaultdict(int)
        for r in rows:
            counts[outcome(r["status"])] += 1
        statuses = defaultdict(int)
        for r in rows:
            statuses[str(r["status"])] += 1
        endpoints[name] = {
            "requests": len(rows),
            "outcomes": dict(counts),
            "statuses": dict(statuses),
            **{f"p{q}_ms": _round(percentile(ok, q)) for q in (50, 95, 99)},
        }
    done = [j for j in journeys if j["finished"]]
    total = [j["ms"] for j in done]
    check = [c for j in journeys for c in j["checks_ms"]]
    counts = defaultdict(int)
    for r in records:
        counts[outcome(r["status"])] += 1
    return {
        "visitors": len(journeys),
        "visitors_finished": len(done),
        "requests": len(records),
        "outcomes": dict(counts),
        "runs_started": sum(j["runs_started"] for j in journeys),
        "runs_completed": sum(j["runs_completed"] for j in journeys),
        "run_states": _tally(s for j in journeys for s in j["run_states"]),
        "journey_ms": {f"p{q}": _round(percentile(total, q)) for q in (50, 95, 99)} | {"max": _round(max(total) if total else None)},
        "check_ms": {f"p{q}": _round(percentile(check, q)) for q in (50, 95, 99)},
        "endpoints": endpoints,
        "transport_errors": _tally(f"{r['endpoint']}: {r['error']}" for r in records if r.get("error")),
        "failures": _tally(j["failure_kind"] for j in journeys if j["failure_kind"]),
        "failure_examples": sorted({j["failure"] for j in journeys if j["failure"]})[:5],
    }


def _tally(items) -> dict:
    out: dict[str, int] = defaultdict(int)
    for i in items:
        out[i] += 1
    return dict(out)


def _round(v):
    return None if v is None else round(v, 1)


async def visitor(client_args: dict, checks: int, delay: float, records: list[dict], step: int) -> dict:
    await asyncio.sleep(delay)
    journey = {
        "finished": False,
        "ms": None,
        "checks_ms": [],
        "runs_started": 0,
        "runs_completed": 0,
        "run_states": [],
        "run_ids": [],
        "failure": None,
        "failure_kind": None,
    }
    started = time.perf_counter()
    async with httpx.AsyncClient(**client_args) as http:

        async def call(endpoint: str, method: str, path: str, **kw):
            t0 = time.perf_counter()
            status, error = None, None
            try:
                resp = await http.request(method, path, **kw)
                status = resp.status_code
                return resp
            except httpx.HTTPError as exc:
                error = type(exc).__name__
                journey["failure"] = journey["failure"] or f"{endpoint}: {error} {str(exc)[:120]}"
                journey["failure_kind"] = journey["failure_kind"] or f"{endpoint}: {error}"
                return None
            finally:
                records.append(
                    {"step": step, "endpoint": endpoint, "status": status, "error": error, "ms": (time.perf_counter() - t0) * 1000}
                )

        def failed(endpoint: str, resp) -> bool:
            if resp is not None and resp.status_code < 400:
                return False
            if resp is not None:
                journey["failure"] = journey["failure"] or f"{endpoint}: {resp.status_code} {resp.text[:120]}"
                journey["failure_kind"] = journey["failure_kind"] or f"{endpoint}: {resp.status_code}"
            return True

        resp = await call("POST /api/demo-sessions", "POST", "/api/demo-sessions")
        if failed("POST /api/demo-sessions", resp):
            return journey
        resp = await call("GET /api/documents", "GET", "/api/documents")
        if failed("GET /api/documents", resp):
            return journey
        docs = resp.json()
        active = next((d for d in docs if d.get("active")), docs[0] if docs else None)
        if active:
            resp = await call("GET /api/documents/{id}/assets/png", "GET", f"/api/documents/{active['id']}/assets/png")
            if failed("GET /api/documents/{id}/assets/png", resp):
                return journey
        for _ in range(checks):
            t_check = time.perf_counter()
            resp = await call("POST /api/runs", "POST", "/api/runs", json=RUN_BODY, headers={"Idempotency-Key": str(uuid4())})
            if failed("POST /api/runs", resp):
                return journey
            run = resp.json()
            journey["runs_started"] += 1
            journey["run_ids"].append(run["id"])
            while run["state"] not in TERMINAL:
                await asyncio.sleep(1.0)  # the client polls once a second
                r1, r2 = await asyncio.gather(
                    call("GET /api/runs/{id}", "GET", f"/api/runs/{run['id']}"),
                    call("GET /api/runs/{id}/events", "GET", f"/api/runs/{run['id']}/events"),
                )
                if failed("GET /api/runs/{id}", r1) or failed("GET /api/runs/{id}/events", r2):
                    return journey
                run = r1.json()
            journey["run_states"].append(run["state"])
            journey["runs_completed"] += run["state"] == "completed"
            journey["checks_ms"].append((time.perf_counter() - t_check) * 1000)
    journey["finished"] = journey["failure"] is None
    journey["ms"] = (time.perf_counter() - started) * 1000
    return journey


def queue_waits(dsn: str, run_ids: list[str]) -> dict:
    """Milliseconds from a run's row being written (its ``queued`` event) to its ``started`` event, the wait for a
    worker, and from ``started`` to the run's last event, the work."""
    import psycopg

    if not run_ids:
        return {}
    with psycopg.connect(dsn) as c:
        rows = c.execute(
            "SELECT r.id, extract(epoch FROM min(e.timestamp) FILTER (WHERE e.kind = 'started') - r.created_at) AS wait, "
            "extract(epoch FROM max(e.timestamp) - min(e.timestamp) FILTER (WHERE e.kind = 'started')) AS work "
            "FROM fw_runs r LEFT JOIN fw_events e ON e.run_id = r.id WHERE r.id = ANY(%s) GROUP BY r.id, r.created_at",
            (run_ids,),
        ).fetchall()
    waits = [float(w) * 1000 for _, w, _ in rows if w is not None]
    works = [float(x) * 1000 for _, _, x in rows if x is not None]
    return {
        "runs": len(rows),
        "queue_wait_ms": {f"p{q}": _round(percentile(waits, q)) for q in (50, 95, 99)} | {"max": _round(max(waits) if waits else None)},
        "started_to_last_event_ms": {f"p{q}": _round(percentile(works, q)) for q in (50, 95, 99)},
    }


def reset_admission(dsn: str) -> None:
    import psycopg

    with psycopg.connect(dsn) as c:
        c.execute("DELETE FROM fw_admission WHERE bucket LIKE 'sessions:%' OR bucket LIKE 'runs:%'")


async def sample_server(pid: int, stop: asyncio.Event, out: dict) -> None:
    """Memory of the API process and its worker subprocesses, summed, and CPU of that tree, twice a second. PSS
    (proportional set size: shared library pages split between the processes that map them) is what the tree
    really occupies; summed RSS counts shared pages once per process and overstates it. The database is another
    process tree and is not counted: on Render it is a separate service (Neon)."""
    import psutil

    root = psutil.Process(pid)
    rss, pss, cpu, procs = [], [], [], []
    tree = {}
    while not stop.is_set():
        try:
            members = [root, *root.children(recursive=True)]
        except psutil.NoSuchProcess:
            break
        total_rss, total_pss, total_cpu = 0, 0, 0.0
        for proc in members:
            try:
                if proc.pid not in tree:
                    tree[proc.pid] = proc
                    proc.cpu_percent(None)  # the first reading only primes the counter
                mem = proc.memory_full_info()
                total_rss += mem.rss
                total_pss += getattr(mem, "pss", mem.rss)
                total_cpu += proc.cpu_percent(None)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        rss.append(total_rss / 2**20)
        pss.append(total_pss / 2**20)
        cpu.append(total_cpu)
        procs.append(len(members))
        try:
            await asyncio.wait_for(stop.wait(), 0.5)
        except asyncio.TimeoutError:
            pass
    out.update(
        {
            "samples": len(rss),
            "pss_mib_max": _round(max(pss) if pss else None),
            "pss_mib_p50": _round(percentile(pss, 50)),
            "rss_mib_max": _round(max(rss) if rss else None),
            "cpu_percent_p50": _round(percentile(cpu, 50)),
            "cpu_percent_p95": _round(percentile(cpu, 95)),
            "processes_max": max(procs) if procs else None,
        }
    )


async def run_step(args, visitors: int) -> dict:
    records: list[dict] = []
    spread = args.ramp if visitors > 1 else 0.0
    delays = [spread * i / visitors for i in range(visitors)]
    random.Random(visitors).shuffle(delays)

    def client_args(i: int) -> dict:
        headers = {"Origin": args.url}
        if args.distinct_peers:
            # uvicorn trusts X-Forwarded-For from 127.0.0.1 by default, so each visitor counts as its own address
            # for the per-address session limit, as visitors from different networks would.
            headers["X-Forwarded-For"] = f"10.{visitors % 256}.{i // 256}.{i % 256}"
        return {"base_url": args.url, "timeout": args.timeout, "headers": headers}

    resources: dict = {}
    stop = asyncio.Event()
    sampler = asyncio.create_task(sample_server(args.server_pid, stop, resources)) if args.server_pid else None
    t0 = time.perf_counter()
    journeys = await asyncio.gather(*(visitor(client_args(i), args.checks, d, records, visitors) for i, d in enumerate(delays)))
    wall = time.perf_counter() - t0
    if sampler:
        stop.set()
        await sampler
    out = {"visitors": visitors, "wall_seconds": round(wall, 1), "summary": summarise(records, journeys)}
    if sampler:
        out["server_resources"] = resources
    out["summary"]["requests_per_second"] = round(len(records) / wall, 2) if wall else None
    if args.dsn:
        out["server"] = queue_waits(args.dsn, [rid for j in journeys for rid in j["run_ids"]])
    return out


def environment(args) -> dict:
    """What the numbers depend on: the server's code and its settings, the machine, the database."""
    import os
    import platform

    import psutil

    out = {
        "date_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "cpus": os.cpu_count(),
        "memory_gib": round(psutil.virtual_memory().total / 2**30, 1),
        "python": platform.python_version(),
        "platform": platform.platform(),
    }
    try:
        out["server_health"] = httpx.get(args.url + "/health", timeout=10).json()
    except httpx.HTTPError as exc:
        out["server_health"] = f"unavailable: {type(exc).__name__}"
    if args.server_pid:
        env = psutil.Process(args.server_pid).environ()
        # Only the knobs that change capacity; secrets and connection strings are not recorded.
        knobs = (
            "FITWITNESS_WARM_WORKERS",
            "FITWITNESS_DB_POOL_MAX",
            "FITWITNESS_SESSION_LIMIT_PER_PEER",
            "OPENBLAS_NUM_THREADS",
            "OMP_NUM_THREADS",
        )
        out["server_settings"] = {k: env.get(k, "default") for k in knobs}
        out["server_command"] = " ".join(psutil.Process(args.server_pid).cmdline()[1:])
    if args.dsn:
        import psycopg

        with psycopg.connect(args.dsn) as c:
            out["postgres"] = c.execute("SHOW server_version").fetchone()[0]
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--url", default="http://127.0.0.1:8787")
    p.add_argument("--steps", default="1,10,25,50,100", help="comma separated visitor counts, run one after another")
    p.add_argument("--checks", type=int, default=2, help="checks (runs) per visitor after opening the workspace")
    p.add_argument("--ramp", type=float, default=10.0, help="arrivals are spread over this many seconds")
    p.add_argument("--timeout", type=float, default=120.0, help="per request, seconds")
    p.add_argument("--pause", type=float, default=5.0, help="seconds between steps")
    p.add_argument("--dsn", help="the server's database, to read queue waits (and to reset admission buckets)")
    p.add_argument("--reset-admission", action="store_true", help="empty the hourly session/run buckets before each step")
    p.add_argument(
        "--same-peer",
        dest="distinct_peers",
        action="store_false",
        help="send every visitor from one address (by default each gets its own X-Forwarded-For)",
    )
    p.add_argument("--server-pid", type=int, help="the API's process id, to sample its memory and CPU (same machine)")
    p.add_argument("--label", default="")
    p.add_argument("--output", required=True)
    args = p.parse_args(argv)
    assert "onrender.com" not in args.url, "not against the public demo"
    steps = [int(s) for s in args.steps.split(",") if s]
    env = environment(args)
    results = []
    for n in steps:
        if args.reset_admission:
            assert args.dsn, "--reset-admission needs --dsn"
            reset_admission(args.dsn)
        result = asyncio.run(run_step(args, n))
        results.append(result)
        s = result["summary"]
        print(
            json.dumps(
                {
                    "visitors": n,
                    "wall_s": result["wall_seconds"],
                    "finished": s["visitors_finished"],
                    "outcomes": s["outcomes"],
                    "journey_ms": s["journey_ms"],
                    "check_ms": s["check_ms"],
                    "queue": (result.get("server") or {}).get("queue_wait_ms"),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        time.sleep(args.pause)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    existing = json.loads(out.read_text()) if out.exists() else {"profiles": []}
    existing["profiles"] = [x for x in existing.get("profiles", []) if x.get("label") != args.label]
    existing["profiles"].append(
        {
            "label": args.label,
            "environment": env,
            "settings": {k: v for k, v in vars(args).items() if k not in ("output", "dsn", "server_pid")},
            "steps": results,
        }
    )
    out.write_text(json.dumps(existing, ensure_ascii=False, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
