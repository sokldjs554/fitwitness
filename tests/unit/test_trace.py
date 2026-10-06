from fitwitness.runtime.trace import build_trace


def test_trace_groups_role_events_and_measures_latency():
    t0 = "2026-10-06T00:00:00+00:00"
    events = [
        {"seq": 1, "kind": "queued", "timestamp": t0, "payload": {}},
        {"seq": 2, "kind": "retrieved", "timestamp": "2026-10-06T00:00:01+00:00", "payload": {"candidates": []}},
        {"seq": 3, "kind": "model", "timestamp": "2026-10-06T00:00:03+00:00", "payload": {"role": "planner", "latency_ms": 1500, "model_id": "m"}},
        {"seq": 4, "kind": "tool", "timestamp": "2026-10-06T00:00:03.2+00:00", "payload": {"role": "planner", "name": "query_dimensions", "latency_ms": 50, "fact_ids": ["a"]}},
        {"seq": 5, "kind": "completed", "timestamp": "2026-10-06T00:00:04+00:00", "payload": {"candidates": 1}},
    ]
    trace = build_trace("r1", events)
    assert trace["duration_ms"] == 4000 and trace["totals"] == {"model_ms": 1500, "tool_ms": 50, "events": 5}
    root = trace["spans"][0]
    names = [s["name"] for s in root["children"]]
    assert names == ["queued", "retrieved", "role:planner", "completed"]
    planner = root["children"][2]
    assert [s["name"] for s in planner["children"]] == ["model:m", "tool:query_dimensions"]
    assert planner["children"][0]["start_ms"] == 1500 and "fact_ids" not in planner["children"][1]["attrs"]


def test_empty_run_has_no_spans():
    assert build_trace("x", []) == {"run_id": "x", "duration_ms": 0, "spans": []}
