"""The exported trace must be a valid OTLP request, not something that merely looks like one."""
import json
import pytest
from fitwitness.runtime.otlp import END_STATES, endpoint_from_env, headers_from_env, trace_id, trace_to_otlp
from fitwitness.runtime.trace import build_trace

proto = pytest.importorskip("opentelemetry.proto.collector.trace.v1.trace_service_pb2")
from google.protobuf import json_format  # noqa: E402


def ev(seq, kind, at, payload=None):
    from datetime import datetime, timedelta, timezone
    base = datetime(2026, 10, 7, 9, 0, 0, tzinfo=timezone.utc)
    return {"seq": seq, "kind": kind, "timestamp": base + timedelta(milliseconds=at), "payload": payload or {}}


EVENTS = [
    ev(1, "queued", 0), ev(2, "started", 5), ev(3, "intent", 10),
    ev(4, "model", 1500, {"role": "planner", "model_id": "m", "latency_ms": 1400.0, "input_tokens": 120}),
    ev(5, "tool", 1600, {"role": "planner", "name": "query_dimensions", "latency_ms": 50.0, "items": 3, "arguments": {"a": 1}}),
    ev(6, "tool", 1700, {"role": "challenger", "name": "query_dimensions", "latency_ms": 40.0}),
    ev(7, "verified", 1800, {"match": 1}), ev(8, "completed", 1900, {"candidates": 1}),
]


def parsed(payload):
    """Parse with the official protobuf definition.

    OTLP/JSON differs from the generic protobuf JSON mapping in one place: trace and span ids are hex
    strings, not base64 (spec: "OTLP/JSON Protobuf Encoding"). The generic parser expects base64, so
    the ids are converted first; everything else is parsed exactly as the receiver would."""
    import base64
    import copy

    wire = copy.deepcopy(payload)
    for rs in wire["resourceSpans"]:
        for ss in rs["scopeSpans"]:
            for sp in ss["spans"]:
                for key in ("traceId", "spanId", "parentSpanId"):
                    if key in sp:
                        assert len(sp[key]) == {"traceId": 32}.get(key, 16) and int(sp[key], 16) >= 0, f"{key} must be hex of the right length"
                        sp[key] = base64.b64encode(bytes.fromhex(sp[key])).decode()
    return json_format.Parse(json.dumps(wire), proto.ExportTraceServiceRequest())


def test_the_request_parses_with_the_official_protobuf_definition():
    request = parsed(trace_to_otlp(build_trace("run-1", EVENTS), kind="drawing", state="completed"))
    spans = request.resource_spans[0].scope_spans[0].spans
    assert len(spans) == 11  # run, 5 plain events, 2 role groups, the 3 calls inside them
    ids = {s.span_id for s in spans}
    assert len(ids) == len(spans), "span ids are unique"
    root = [s for s in spans if not s.parent_span_id]
    assert len(root) == 1 and root[0].name == "run"
    assert {s.trace_id for s in spans} == {bytes.fromhex(trace_id("run-1"))}
    for s in spans:
        assert s.end_time_unix_nano > s.start_time_unix_nano, s.name  # a point event still lasts a microsecond
        if s.parent_span_id:
            assert s.parent_span_id in ids


def test_parents_cover_their_children_and_roles_group_their_calls():
    request = parsed(trace_to_otlp(build_trace("run-1", EVENTS), state="completed"))
    spans = {s.name: s for s in request.resource_spans[0].scope_spans[0].spans}
    by_id = {s.span_id: s for s in request.resource_spans[0].scope_spans[0].spans}
    for s in by_id.values():
        if s.parent_span_id:
            parent = by_id[s.parent_span_id]
            assert parent.start_time_unix_nano <= s.start_time_unix_nano and s.end_time_unix_nano <= parent.end_time_unix_nano, s.name
    planner = spans["role:planner"]
    assert {by_id[k].name for k in by_id if by_id[k].parent_span_id == planner.span_id} == {"model:m", "tool:query_dimensions"}


def test_attributes_are_typed_and_only_scalars():
    request = parsed(trace_to_otlp(build_trace("run-1", EVENTS), kind="drawing", state="completed"))
    spans = request.resource_spans[0].scope_spans[0].spans
    root = next(s for s in spans if not s.parent_span_id)
    attrs = {a.key: a.value for a in root.attributes}
    assert attrs["fw.run.id"].string_value == "run-1" and attrs["fw.run.kind"].string_value == "drawing"
    assert attrs["fw.run.state"].string_value == "completed" and attrs["fw.total.events"].int_value == 8
    tool = next(s for s in spans if s.name == "tool:query_dimensions")
    tool_attrs = {a.key: a.value for a in tool.attributes}
    assert tool_attrs["fw.items"].int_value == 3 and tool_attrs["fw.latency_ms"].double_value == 50.0
    assert "fw.arguments" not in tool_attrs, "nested payloads stay in the event log"
    service = {a.key: a.value.string_value for a in request.resource_spans[0].resource.attributes}
    assert service["service.name"] == "fitwitness"


def test_failed_runs_carry_an_error_status():
    events = [ev(1, "queued", 0), ev(2, "started", 5), ev(3, "failed", 50, {"error": "boom", "attempts": 1})]
    request = parsed(trace_to_otlp(build_trace("run-2", events), state="failed"))
    spans = request.resource_spans[0].scope_spans[0].spans
    root = next(s for s in spans if not s.parent_span_id)
    failed = next(s for s in spans if s.name == "failed")
    assert root.status.code == 2 and failed.status.code == 2 and "boom" in failed.status.message


def test_ids_are_stable_so_a_backfill_of_the_same_run_matches():
    a = trace_to_otlp(build_trace("run-1", EVENTS), state="completed")
    b = trace_to_otlp(build_trace("run-1", EVENTS), state="completed")
    assert a == b
    other = trace_to_otlp(build_trace("run-9", EVENTS), state="completed")
    assert trace_id("run-1") != trace_id("run-9")
    assert a["resourceSpans"][0]["scopeSpans"][0]["spans"][1]["spanId"] != other["resourceSpans"][0]["scopeSpans"][0]["spans"][1]["spanId"]


def test_configuration_follows_the_standard_variables(monkeypatch):
    for k in ("OTEL_EXPORTER_OTLP_ENDPOINT", "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "OTEL_EXPORTER_OTLP_HEADERS"):
        monkeypatch.delenv(k, raising=False)
    assert endpoint_from_env() is None
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector:4318/")
    assert endpoint_from_env() == "http://collector:4318/v1/traces"
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "http://other/x/traces")
    assert endpoint_from_env() == "http://other/x/traces"
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_HEADERS", "authorization=Bearer t, x-team=fw")
    assert headers_from_env() == {"Content-Type": "application/json", "authorization": "Bearer t", "x-team": "fw"}
    assert END_STATES == {"completed", "failed", "cancelled", "stale"}
