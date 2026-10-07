"""Span tree for one run, reconstructed from its event log.

Events are points in time; model and tool events carry their own latency, so each
becomes a span ending at the event timestamp. Everything is grouped under the run and,
for paid runs, under the planner/challenger role that produced it. The shape follows
what a tracing backend would show, so the same data can later be exported as OTLP
without changing how it is recorded.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

ROLE_KINDS = {"model", "tool", "agent_plan", "tool_skipped", "budget_stop", "model_error", "model_schema_error", "retry_wait"}


def _ts(value: Any) -> float:
    if isinstance(value, datetime):
        return value.timestamp()
    return datetime.fromisoformat(str(value)).timestamp()


def build_trace(run_id: str, events: list[dict]) -> dict:
    if not events:
        return {"run_id": run_id, "duration_ms": 0, "spans": []}
    start = _ts(events[0]["timestamp"])
    end = _ts(events[-1]["timestamp"])
    root = {"name": "run", "kind": "run", "start_ms": 0.0, "duration_ms": (end - start) * 1000, "attrs": {"first": events[0]["kind"], "last": events[-1]["kind"]}, "children": []}
    roles: dict[str, dict] = {}
    for e in events:
        at = (_ts(e["timestamp"]) - start) * 1000
        payload = e.get("payload") or {}
        latency = float(payload.get("latency_ms") or 0)
        span = {"name": e["kind"] if e["kind"] not in ("model", "tool") else f"{e['kind']}:{payload.get('name') or payload.get('model_id') or ''}".rstrip(":"),
                "kind": e["kind"], "seq": e["seq"], "start_ms": max(0.0, at - latency), "duration_ms": latency,
                "attrs": {k: v for k, v in payload.items() if k not in ("fact_ids",) and not isinstance(v, (list, dict))}, "children": []}
        role = payload.get("role")
        if e["kind"] in ROLE_KINDS and role:
            if role not in roles:
                roles[role] = {"name": f"role:{role}", "kind": "role", "start_ms": at, "duration_ms": 0.0, "attrs": {}, "children": []}
                root["children"].append(roles[role])
            parent = roles[role]
            parent["duration_ms"] = max(parent["duration_ms"], at - parent["start_ms"])
            parent["children"].append(span)
        else:
            root["children"].append(span)
    totals = {"model_ms": sum(s["duration_ms"] for r in root["children"] for s in r.get("children", []) + [r] if s["kind"] == "model"),
              "tool_ms": sum(s["duration_ms"] for r in root["children"] for s in r.get("children", []) + [r] if s["kind"] == "tool"),
              "events": len(events)}
    return {"run_id": run_id, "started_at_unix": start, "duration_ms": root["duration_ms"], "totals": totals, "spans": [root]}
