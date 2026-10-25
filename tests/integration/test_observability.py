"""Metrics are derived from the run/event tables; a run can be read back as a span tree."""
from test_workflow import env, request
from fitwitness.runtime.metrics import collect, summarize
from fitwitness.contracts import RunRequest, SearchRequest


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


def test_counters_do_not_go_down_when_a_workspace_expires_and_waits_are_exported(env):
    """The session sweep deletes an expired workspace's runs and events. The counters are computed from those
    rows, so the sweep keeps what they counted; a counter that went down would read to Prometheus as a restart
    and every rate over it would jump. The two gauges say how long a run has waited for a worker and how many
    reviews are past their deadline without having been escalated."""
    import psycopg
    from psycopg.types.json import Jsonb
    from fitwitness.agents.graph import execute_run

    r, j, s = env
    j.register_session(s)
    run = j.enqueue(s, request(), "obs-sweep")
    execute_run(r, s, run.id)
    waiting = j.enqueue(s, RunRequest(search=SearchRequest(text="플랜지 너비 60mm SUS304")), "obs-wait")
    with psycopg.connect(r.dsn, autocommit=True) as c:
        # A priced run, so the usage totals have something to keep, and a run no worker has taken for ten minutes
        # whose review deadline has passed.
        c.execute("UPDATE fw_runs SET usage=usage||%s WHERE id=%s", (Jsonb({"cost_usd": "0.25", "input_tokens": 1000, "output_tokens": 200, "model_calls": 2}), run.id))
        c.execute("UPDATE fw_runs SET created_at=now()-interval '10 minutes' WHERE id=%s", (waiting.id,))
        c.execute("INSERT INTO fw_review_due(tenant_id,run_id,due_at) VALUES(%s,%s,now()-interval '1 minute')", (s.tenant_id, waiting.id))
    before = summarize(collect(r.dsn))
    assert before["fitwitness_queue_oldest_seconds"] >= 600
    assert before["fitwitness_reviews_overdue"] >= 1
    assert before["fitwitness_runs_completed_total"] >= 1

    with psycopg.connect(r.dsn, autocommit=True) as c:
        c.execute("UPDATE fw_sessions SET expires_at=now()-interval '1 minute' WHERE tenant_id=%s", (s.tenant_id,))
    for _ in range(10):  # the sweep takes 20 expired workspaces at a time
        j.cleanup_sessions()
        with psycopg.connect(r.dsn) as c:
            if not c.execute("SELECT 1 FROM fw_sessions WHERE tenant_id=%s", (s.tenant_id,)).fetchone():
                break
    with psycopg.connect(r.dsn) as c:
        assert c.execute("SELECT count(*) FROM fw_runs WHERE tenant_id=%s", (s.tenant_id,)).fetchone()[0] == 0
    after = summarize(collect(r.dsn))
    counters = [k for k in before if k.split("{")[0].endswith("_total") and not k.startswith(("fitwitness_runs_total", "fitwitness_requests_total"))
                and "_latency_" not in k]
    assert "fitwitness_cost_usd_total" in counters and 'fitwitness_tool_calls_total{tool="query_dimensions"}' in counters
    assert {k: after.get(k) for k in counters} == {k: before[k] for k in counters}


def test_an_unhandled_error_is_counted_as_a_500(env, monkeypatch):
    """The 5xx alert reads fitwitness_requests_total. An exception a route does not handle becomes a 500 outside
    the middleware that counts requests, so the middleware counts it on the way out."""
    from fastapi.testclient import TestClient
    from fitwitness.api.app import create_app
    from fitwitness.runtime.jobs import Jobs

    monkeypatch.setenv("FITWITNESS_METRICS_TOKEN", "t0k3n")
    monkeypatch.setattr(Jobs, "cleanup_sessions", lambda self: 1 / 0)
    client = TestClient(create_app(), raise_server_exceptions=False)
    assert client.post("/api/demo-sessions").status_code == 500
    text = client.get("/metrics", headers={"authorization": "Bearer t0k3n"}).text
    assert 'fitwitness_requests_total{method="POST",status="500"} 1.0' in text
