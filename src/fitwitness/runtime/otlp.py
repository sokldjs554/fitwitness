"""Export a run's trace as OTLP/HTTP JSON to any OpenTelemetry backend (Jaeger, Tempo, a collector).

The run's event log is already the source of truth (``runtime/trace.py`` turns it into a span
tree for the API). This module only re-shapes that tree into the OTLP wire format and posts it,
so there is no second instrumentation path to keep in step with the first, and a run that
crashed half-way still exports exactly what it recorded.

Switched on by the standard variables:

    OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318        (posts to <endpoint>/v1/traces)
    OTEL_EXPORTER_OTLP_TRACES_ENDPOINT=http://host/v1/traces (the full URL, wins over the above)
    OTEL_EXPORTER_OTLP_HEADERS=authorization=Bearer x,key=value
    FITWITNESS_ENV=production                                 (deployment.environment, default "local")

Unset, nothing happens and nothing is imported. Export is best effort: a backend that is down
costs a short timeout and a log line, never a failed run.

A run is exported once, when it reaches an end state (completed, failed, cancelled, stale). A run
that is only paused (waiting for a reviewer, backing off before a retry) is not exported yet:
OTLP has no "update this span", and a backend that receives the same span id twice keeps both
(Jaeger's memory store showed two roots for a resumed claim). The finished export covers the
whole run, including the time it spent paused. Ids are still derived from the run id and the
event sequence number, so an operator who backfills a run twice gets the same spans.
"""

from __future__ import annotations

import hashlib
import logging
import os
import sys
from typing import Any

log = logging.getLogger(__name__)

SERVICE = "fitwitness"
ERROR_KINDS = {"failed", "dead_lettered", "model_error", "model_schema_error"}
ATTR_LIMIT = 500


def _hex(seed: str, nbytes: int) -> str:
    return hashlib.sha256(seed.encode()).hexdigest()[: nbytes * 2]


def trace_id(run_id: str) -> str:
    return _hex(f"trace:{run_id}", 16)


def _value(v: Any) -> dict | None:
    if isinstance(v, bool):
        return {"boolValue": v}
    if isinstance(v, int):
        return {"intValue": str(v)}  # OTLP JSON carries 64-bit integers as strings
    if isinstance(v, float):
        return {"doubleValue": v}
    if isinstance(v, str):
        return {"stringValue": v[:ATTR_LIMIT]}
    return None  # nested structures stay in the event log; spans carry scalars


def _attrs(d: dict, prefix: str = "fw.") -> list[dict]:
    out = []
    for k, v in sorted(d.items()):
        val = _value(v)
        if val is not None:
            out.append({"key": f"{prefix}{k}", "value": val})
    return out


def trace_to_otlp(trace: dict, *, kind: str | None = None, state: str | None = None,
                  environment: str | None = None, version: str | None = None) -> dict:
    """The span tree of ``build_trace`` as an OTLP ``ExportTraceServiceRequest`` (JSON mapping)."""
    run_id = trace["run_id"]
    tid = trace_id(run_id)
    # Integer microseconds: epoch seconds as a float lose nanosecond precision, which made point
    # events (zero duration) come out with end < start in the backend.
    base_us = round(float(trace.get("started_at_unix") or 0.0) * 1_000_000)
    spans: list[dict] = []

    def ids(node: dict, path: str) -> str:
        key = f"seq:{node['seq']}" if node.get("seq") is not None else path
        return _hex(f"span:{run_id}:{key}", 8)

    def visit(node: dict, parent: str | None, path: str) -> tuple[int, int]:
        sid = ids(node, path)
        begin_us = base_us + round(node["start_ms"] * 1000)
        end_us = begin_us + max(1, round(node["duration_ms"] * 1000))  # a point event lasts one microsecond
        attrs = _attrs(node.get("attrs") or {})
        attrs.append({"key": "fw.event.kind", "value": {"stringValue": node["kind"]}})
        if node.get("seq") is not None:
            attrs.append({"key": "fw.event.seq", "value": {"intValue": str(node["seq"])}})
        span = {"traceId": tid, "spanId": sid, "name": node["name"], "kind": 1,  # SPAN_KIND_INTERNAL
                "attributes": attrs}
        if parent:
            span["parentSpanId"] = parent
        if node["kind"] in ERROR_KINDS:
            span["status"] = {"code": 2, "message": str((node.get("attrs") or {}).get("error", node["kind"]))[:200]}
        spans.append(span)
        for i, child in enumerate(node.get("children") or []):
            c_begin, c_end = visit(child, sid, f"{path}/{i}:{child['name']}")
            # A parent covers its children: a tool span is recorded at its end with its latency, so it can
            # start before the group that holds it, and a closing point event ends after the run's last tick.
            begin_us, end_us = min(begin_us, c_begin), max(end_us, c_end)
        span["startTimeUnixNano"], span["endTimeUnixNano"] = str(begin_us * 1000), str(end_us * 1000)
        return begin_us, end_us

    for root in trace.get("spans") or []:
        visit(root, None, "root")
    if spans:
        root_span = spans[0]
        extra = [{"key": "fw.run.id", "value": {"stringValue": run_id}}]
        if kind:
            extra.append({"key": "fw.run.kind", "value": {"stringValue": kind}})
        if state:
            extra.append({"key": "fw.run.state", "value": {"stringValue": state}})
        for k, v in (trace.get("totals") or {}).items():
            val = _value(v)
            if val is not None:
                extra.append({"key": f"fw.total.{k}", "value": val})
        root_span["attributes"] = extra + root_span["attributes"]
        if state == "failed":
            root_span["status"] = {"code": 2, "message": "run failed"}
        elif state == "completed":
            root_span["status"] = {"code": 1}
    resource = [{"key": "service.name", "value": {"stringValue": SERVICE}},
                {"key": "deployment.environment", "value": {"stringValue": environment or os.getenv("FITWITNESS_ENV", "local")}}]
    if version:
        resource.append({"key": "service.version", "value": {"stringValue": version}})
    return {"resourceSpans": [{"resource": {"attributes": resource},
                               "scopeSpans": [{"scope": {"name": "fitwitness.runtime.trace"}, "spans": spans}]}]}


def endpoint_from_env() -> str | None:
    full = os.getenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT")
    if full:
        return full
    base = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
    return (base.rstrip("/") + "/v1/traces") if base else None


def headers_from_env() -> dict[str, str]:
    out = {"Content-Type": "application/json"}
    for pair in filter(None, (os.getenv("OTEL_EXPORTER_OTLP_HEADERS") or "").split(",")):
        k, _, v = pair.partition("=")
        if k.strip():
            out[k.strip()] = v.strip()
    return out


def post(payload: dict, url: str, *, timeout: float = 3.0) -> bool:
    import httpx

    try:
        r = httpx.post(url, json=payload, headers=headers_from_env(), timeout=timeout)
        if r.status_code >= 300:
            log.warning("OTLP export to %s answered %s: %s", url, r.status_code, r.text[:200])
            return False
        return True
    except Exception as exc:  # noqa: BLE001 - tracing must never break a run
        log.warning("OTLP export to %s failed: %s", url, type(exc).__name__)
        return False


END_STATES = {"completed", "failed", "cancelled", "stale"}


def export_run(jobs, scope, run_id: str, url: str | None = None, *, include_open: bool = False) -> bool:
    """Read the run's events, build its trace and post it.

    False when disabled, when the run is only paused (unless ``include_open``), empty or unreachable."""
    url = url or endpoint_from_env()
    if not url:
        return False
    from fitwitness.runtime.trace import build_trace

    view = jobs.get(scope, run_id)
    if view is None or (view.state not in END_STATES and not include_open):
        return False
    trace = build_trace(run_id, jobs.events(scope, run_id))
    if not trace.get("spans"):
        return False
    return post(trace_to_otlp(trace, kind=view.kind, state=view.state, version=os.getenv("FITWITNESS_VERSION")), url)


def main(argv: list[str] | None = None) -> int:
    """Backfill: export one run that already finished (or is waiting)."""
    import argparse

    from fitwitness.contracts import TenantScope
    from fitwitness.runtime.jobs import Jobs
    from fitwitness.storage.repository import Repository

    p = argparse.ArgumentParser(description=main.__doc__)
    p.add_argument("--tenant", required=True)
    p.add_argument("--run", required=True)
    p.add_argument("--endpoint", help="full traces URL; defaults to the OTEL_EXPORTER_OTLP_* variables")
    p.add_argument("--include-open", action="store_true", help="also export a run that is waiting or backing off (it will be exported again when it ends)")
    args = p.parse_args(argv)
    jobs = Jobs(Repository(os.environ["FITWITNESS_DATABASE_URL"]))
    ok = export_run(jobs, TenantScope(tenant_id=args.tenant, user_id="otlp", role="operator"), args.run, args.endpoint, include_open=args.include_open)
    print("exported" if ok else "nothing exported (no endpoint, the run has not ended, no events, or the backend did not accept it)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
