"""Human-in-the-loop: a run that cannot decide stops, waits, and resumes with the reviewer's answer."""
from test_workflow import env
from fitwitness.contracts import RunRequest, SearchRequest


def request():
    return RunRequest(search=SearchRequest(text="브래킷 구멍 간격 40mm SUS304"), review="on_unknown")


def test_unknown_verdicts_pause_the_run_until_a_reviewer_answers(env):
    from fitwitness.agents.graph import execute_run

    r, j, s = env
    run = j.enqueue(s, request(), "review-1")
    execute_run(r, s, run.id)
    view = j.get(s, run.id)
    assert view.state == "waiting_input" and view.question and "확인" in view.question
    assert (s.tenant_id, run.id) not in j.pending()                      # not re-dispatched while waiting
    kinds = [e["kind"] for e in j.events(s, run.id)]
    assert "waiting_input" in kinds and "completed" not in kinds
    pending = next(e for e in j.events(s, run.id) if e["kind"] == "waiting_input")["payload"]["pending"]
    assert pending and all(p["unknown_fields"] for p in pending)

    execute_run(r, s, run.id)                                           # a worker without an answer changes nothing
    assert j.get(s, run.id).state == "waiting_input"

    rid = pending[0]["revision_id"]
    resumed = j.resume(s, run.id, {"decisions": {rid: "match"}, "reviewer": "qa", "note": "현장 실측 확인"})
    assert resumed.state == "queued" and (s.tenant_id, run.id) in j.pending()
    execute_run(r, s, run.id)
    done = j.get(s, run.id)
    assert done.state == "completed"
    decision = next(d for d in done.decisions if d.revision_id == rid)
    assert decision.verdict == "match" and decision.reviewed_by == "qa"
    assert any(e.verifier_version == "human-v1" and "qa" in e.summary for e in decision.evidence)
    untouched = [d for d in done.decisions if d.revision_id != rid]
    assert all(d.reviewed_by is None for d in untouched)
    kinds = [e["kind"] for e in j.events(s, run.id)]
    assert kinds.index("resumed_by_human") < kinds.index("human_review") < kinds.index("completed")
    assert kinds.count("retrieved") == 1                               # resumed from the checkpoint, not restarted


def test_resume_is_refused_unless_the_run_is_waiting(env):
    import pytest
    from fitwitness.agents.graph import execute_run

    r, j, s = env
    run = j.enqueue(s, RunRequest(search=SearchRequest(text="브래킷 구멍 간격 40mm SUS304")), "review-2")
    execute_run(r, s, run.id)
    assert j.get(s, run.id).state == "completed"
    with pytest.raises(ValueError):
        j.resume(s, run.id, {"decisions": {}, "reviewer": "qa", "note": ""})


def test_review_resume_through_the_api(env):
    from fastapi.testclient import TestClient
    from fitwitness.api.app import create_app

    client = TestClient(create_app())
    assert client.post("/api/demo-sessions").status_code == 200
    run = client.post("/api/runs", json={"search": {"text": "브래킷 구멍 간격 40mm SUS304"}, "review": "on_unknown"},
                      headers={"Idempotency-Key": "api-review"}).json()
    view = client.get("/api/runs/" + run["id"]).json()
    assert view["state"] == "waiting_input"
    pending = next(e for e in client.get(f"/api/runs/{run['id']}/events").json() if e["kind"] == "waiting_input")["payload"]["pending"]
    rid = pending[0]["revision_id"]
    bad = client.post(f"/api/runs/{run['id']}/resume", json={"decisions": {rid: "approve"}, "reviewer": "qa"})
    assert bad.status_code == 422
    ok = client.post(f"/api/runs/{run['id']}/resume", json={"decisions": {rid: "mismatch"}, "reviewer": "qa", "note": "도면 재질 표기 오류"})
    assert ok.status_code == 200
    final = client.get("/api/runs/" + run["id"]).json()
    assert final["state"] == "completed"
    assert next(d for d in final["decisions"] if d["revision_id"] == rid)["verdict"] == "mismatch"
    again = client.post(f"/api/runs/{run['id']}/resume", json={"decisions": {}, "reviewer": "qa"})
    assert again.status_code == 409


def test_parked_runs_do_not_keep_the_worker_lease(env):
    """The dispatcher skips any run whose lease is still in the future, so parking must release it."""
    from fitwitness.agents.graph import execute_run

    r, j, s = env
    run = j.enqueue(s, RunRequest(search=SearchRequest(text="브래킷 구멍 간격 40mm SUS304"), review="on_unknown"), "review-lease")
    execute_run(r, s, run.id)
    raw = j.raw(s, run.id)
    assert raw["state"] == "waiting_input" and raw["lease_token"] is None and raw["lease_until"] is None
    j.resume(s, run.id, {"decisions": {}, "reviewer": "qa", "note": ""})
    raw = j.raw(s, run.id)
    assert raw["state"] == "queued" and raw["lease_until"] is None
    assert j.claim(s, run.id)  # immediately claimable, no lease to wait out
