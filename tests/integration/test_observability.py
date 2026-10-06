"""Metrics are derived from the run/event tables; a run can be read back as a span tree."""
from test_workflow import env, request
from fitwitness.runtime.metrics import collect, summarize


def test_metrics_and_trace_reflect_a_completed_run(env):
    from fitwitness.agents.graph import execute_run
    from fitwitness.runtime.trace import build_trace

    r, j, s = env
    run = j.enqueue(s, request(), "obs-1")
    execute_run(r, s, run.id)
    metrics = summarize(collect(r.dsn))
    assert metrics['fitwitness_runs_total{state="completed"}'] >= 1
    assert metrics['fitwitness_tool_calls_total{tool="query_dimensions"}'] >= 5
    assert metrics['fitwitness_tool_latency_ms_count{tool="query_dimensions"}'] >= 5
    assert "fitwitness_cost_usd_total" in metrics
    trace = build_trace(run.id, j.events(s, run.id))
    root = trace["spans"][0]
    assert root["name"] == "run" and trace["totals"]["events"] == len(j.events(s, run.id))
    tools = [c for c in root["children"] if c["kind"] == "tool"]
    assert len(tools) >= 5 and all(c["duration_ms"] >= 0 for c in tools)


def test_metrics_endpoint_requires_token_and_includes_db_metrics(env, monkeypatch):
    from fastapi.testclient import TestClient
    from fitwitness.api.app import create_app

    monkeypatch.setenv("FITWITNESS_METRICS_TOKEN", "t0k3n")
    client = TestClient(create_app())
    assert client.get("/metrics").status_code == 401
    text = client.get("/metrics", headers={"authorization": "Bearer t0k3n"}).text
    assert "fitwitness_requests_total" in text and "fitwitness_runs_total" in text and "fitwitness_model_latency_ms" in text


def test_trace_endpoint_is_tenant_scoped(env):
    from fastapi.testclient import TestClient
    from fitwitness.api.app import create_app

    a, b = TestClient(create_app()), TestClient(create_app())
    a.post("/api/demo-sessions")
    b.post("/api/demo-sessions")
    run = a.post("/api/runs", json={"search": {"text": "브래킷 구멍 간격 40mm SUS304"}}, headers={"Idempotency-Key": "trace"}).json()
    trace = a.get(f"/api/runs/{run['id']}/trace").json()
    assert trace["run_id"] == run["id"] and trace["spans"][0]["name"] == "run"
    assert b.get(f"/api/runs/{run['id']}/trace").status_code == 404
