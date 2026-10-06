"""Prometheus metrics derived from the database.

Workers run in separate processes, so process-local counters in the API would miss
every model and tool call. The run/event tables are already the system of record for
those; this module aggregates them at scrape time into Prometheus text exposition.
Latencies come from the ``latency_ms`` recorded on ``model`` and ``tool`` events, cost
from the usage ledger saved on each run.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Iterable

import psycopg

LATENCY_BUCKETS_MS = (50, 100, 250, 500, 1000, 2500, 5000, 10000, 30000, float("inf"))
EVENT_COUNTERS = {
    "budget_stop": "fitwitness_budget_stops_total",
    "retry_wait": "fitwitness_provider_retries_total",
    "model_error": "fitwitness_model_errors_total",
    "model_schema_error": "fitwitness_model_schema_errors_total",
    "retry_scheduled": "fitwitness_job_retries_total",
    "dead_lettered": "fitwitness_dead_letters_total",
    "waiting_input": "fitwitness_review_requests_total",
    "resumed_by_human": "fitwitness_review_resumes_total",
    "interrupted": "fitwitness_worker_interruptions_total",
    "resumed": "fitwitness_checkpoint_resumes_total",
    "stale": "fitwitness_stale_results_total",
}


def _labels(**labels: str) -> str:
    items = ",".join(f'{k}="{v}"' for k, v in sorted(labels.items()) if v is not None)
    return "{" + items + "}" if items else ""


def _histogram(name: str, help_text: str, samples: dict[str, list[float]], label: str) -> list[str]:
    lines = [f"# HELP {name} {help_text}", f"# TYPE {name} histogram"]
    for key, values in sorted(samples.items()):
        for bucket in LATENCY_BUCKETS_MS:
            le = "+Inf" if bucket == float("inf") else f"{bucket:g}"
            lines.append(f"{name}_bucket{_labels(**{label: key, 'le': le})} {sum(1 for v in values if v <= bucket)}")
        lines.append(f"{name}_sum{_labels(**{label: key})} {sum(values):g}")
        lines.append(f"{name}_count{_labels(**{label: key})} {len(values)}")
    return lines


def collect(dsn: str) -> str:
    """Render database-derived metrics. Uses an admin connection: metrics are global, not per tenant."""
    with psycopg.connect(dsn) as c:
        states = c.execute("SELECT state, count(*) FROM fw_runs GROUP BY state").fetchall()
        cost = c.execute("SELECT COALESCE(SUM((usage->>'cost_usd')::numeric),0), COALESCE(SUM((usage->>'input_tokens')::bigint),0), "
                         "COALESCE(SUM((usage->>'output_tokens')::bigint),0), COALESCE(SUM((usage->>'model_calls')::bigint),0), "
                         "COALESCE(SUM((usage->>'tool_calls')::bigint),0) FROM fw_runs").fetchone()
        kinds = c.execute("SELECT kind, count(*) FROM fw_events GROUP BY kind").fetchall()
        models = c.execute("SELECT COALESCE(payload->>'role','planner'), (payload->>'latency_ms')::float FROM fw_events "
                           "WHERE kind='model' AND payload ? 'latency_ms'").fetchall()
        tools = c.execute("SELECT payload->>'name', (payload->>'latency_ms')::float FROM fw_events WHERE kind='tool' AND payload ? 'latency_ms'").fetchall()
        tool_counts = c.execute("SELECT COALESCE(payload->>'name','unknown'), count(*) FROM fw_events WHERE kind='tool' GROUP BY 1").fetchall()
    lines: list[str] = ["# HELP fitwitness_runs_total Runs by current state", "# TYPE fitwitness_runs_total gauge"]
    lines += [f"fitwitness_runs_total{_labels(state=state)} {n}" for state, n in sorted(states)]
    lines += ["# HELP fitwitness_cost_usd_total Measured provider cost (usage x configured price), all runs", "# TYPE fitwitness_cost_usd_total counter",
              f"fitwitness_cost_usd_total {float(cost[0]):g}",
              "# HELP fitwitness_tokens_total Provider tokens by direction", "# TYPE fitwitness_tokens_total counter",
              f"fitwitness_tokens_total{_labels(direction='input')} {cost[1]}", f"fitwitness_tokens_total{_labels(direction='output')} {cost[2]}",
              "# HELP fitwitness_model_calls_total Model calls recorded in run usage", "# TYPE fitwitness_model_calls_total counter",
              f"fitwitness_model_calls_total {cost[3]}",
              "# HELP fitwitness_tool_calls_total Tool calls by tool name", "# TYPE fitwitness_tool_calls_total counter"]
    lines += [f"fitwitness_tool_calls_total{_labels(tool=name)} {n}" for name, n in sorted(tool_counts)]
    kind_counts = Counter({k: n for k, n in kinds})
    for kind, metric in EVENT_COUNTERS.items():
        lines += [f"# HELP {metric} Count of '{kind}' run events", f"# TYPE {metric} counter", f"{metric} {kind_counts.get(kind, 0)}"]
    model_samples: dict[str, list[float]] = defaultdict(list)
    for role, latency in models:
        model_samples[role].append(latency)
    tool_samples: dict[str, list[float]] = defaultdict(list)
    for name, latency in tools:
        tool_samples[name or "unknown"].append(latency)
    lines += _histogram("fitwitness_model_latency_ms", "Model call latency by role (ms)", model_samples, "role")
    lines += _histogram("fitwitness_tool_latency_ms", "Tool call latency by tool (ms)", tool_samples, "tool")
    return "\n".join(lines) + "\n"


def summarize(text: str) -> dict[str, float]:
    """Parse the exposition back into a flat dict (used by tests and the UI)."""
    out: dict[str, float] = {}
    for line in text.splitlines():
        if line.startswith("#") or not line.strip():
            continue
        name, value = line.rsplit(" ", 1)
        out[name] = float(value)
    return out
