import os, importlib.util
import pytest
from fastapi.testclient import TestClient
from test_workflow import env  # noqa: F401


@pytest.fixture
def clients():
    assert importlib.util.find_spec("fitwitness.api.app"), "API not implemented"
    from fitwitness.api.app import create_app

    app = create_app()
    return TestClient(app), TestClient(app)


def test_session_scope_and_csrf(clients):
    a, b = clients
    assert a.get("/api/documents").status_code == 401
    import hashlib
    import psycopg

    def counts():  # per-address session counts by bucket
        with psycopg.connect(a.app.state.repo.dsn) as c:
            return dict(c.execute("SELECT bucket, used FROM fw_admission WHERE bucket LIKE 'sessions:%:%'").fetchall())

    before = counts()
    assert a.post("/api/demo-sessions").status_code == 200
    assert b.post("/api/demo-sessions").status_code == 200
    # Both clients share one address: its count grows by two, under a keyed hash of it rather than the bare SHA-256.
    grown = {k: v - before.get(k, 0) for k, v in counts().items() if v != before.get(k, 0)}
    peers = {k.split(":")[2] for k in grown}
    assert sum(grown.values()) == 2 and len(peers) == 1 and hashlib.sha256(b"testclient").hexdigest()[:16] not in peers
    docs = a.get("/api/documents").json()
    assert len(docs) >= 5
    rid = docs[0]["id"]
    # Same synthetic document IDs exist per workspace; a run ID must remain private.
    run = a.post(
        "/api/runs",
        json={"search": {"text": "브래킷 구멍 간격 40mm SUS304"}},
        headers={"Idempotency-Key": "api-test"},
    ).json()
    assert b.get("/api/runs/" + run["id"]).status_code == 404
    assert (
        a.post(
            "/api/runs",
            json={"search": {"text": "x"}},
            headers={"Origin": "https://evil.example"},
        ).status_code
        == 403
    )


def test_revision_marks_completed_result_stale(clients):
    a, _ = clients
    a.post("/api/demo-sessions")
    run = a.post(
        "/api/runs",
        json={"search": {"text": "브래킷 구멍 간격 40mm SUS304"}},
        headers={"Idempotency-Key": "r"},
    ).json()
    assert a.get("/api/runs/" + run["id"]).json()["state"] == "completed"
    change = a.post("/api/demo/revision").json()
    assert change["affected_runs"]
    assert a.get("/api/runs/" + run["id"]).json()["state"] == "stale"


def test_unconfigured_provider_is_not_fake_success(clients):
    a, _ = clients
    a.post("/api/demo-sessions")
    response = a.post(
        "/api/runs",
        json={"provider": "openai", "model_id": "missing", "search": {"text": "x"}},
    )
    assert response.status_code == 409


def test_unknown_document_is_404(clients):
    a, _ = clients
    a.post("/api/demo-sessions")
    assert a.get("/api/documents/nonexistent/assets/png").status_code == 404
    assert a.head("/health").status_code == 200  # the uptime monitor's probe, which may come as HEAD

def test_public_demo_cannot_spend_configured_provider_keys(clients,monkeypatch):
    a,_=clients
    monkeypatch.setenv('OPENAI_API_KEY','test-not-a-real-key')
    monkeypatch.setenv('FITWITNESS_OPENAI_MODEL','configured')
    a.post('/api/demo-sessions')
    assert a.get('/api/capabilities').json()['providers']['openai'] is False
    assert a.post('/api/runs',json={'provider':'openai','search':{'text':'브래킷'}}).status_code==409


def test_demo_workspace_holds_other_part_kinds_and_search_excludes_them(clients):
    a, _ = clients
    assert a.post("/api/demo-sessions").status_code == 200
    docs = a.get("/api/documents").json()
    kind = {d["id"]: d["kind"] for d in docs}
    assert len(docs) == 20 and set(kind.values()) == {"bracket", "flange", "shaft", "housing"}
    brackets = a.post("/api/search", json={"text": "장비에 고정할 브래킷을 찾아줘", "top_k": 10}).json()
    assert len(brackets) == 5 and {kind[c["revision_id"]] for c in brackets} == {"bracket"}
    flanges = a.post("/api/search", json={"text": "플랜지 연결판", "top_k": 10}).json()
    assert len(flanges) == 5 and {kind[c["revision_id"]] for c in flanges} == {"flange"}
    assert a.post("/api/search", json={"text": "존재하지 않는 부품 이름"}).json() == []
    # The guided revision story is untouched: FW-000-0 Rev B is still held back.
    assert not any(d["drawing_number"] == "FW-000-0" and d["revision_label"] == "B" for d in docs)


def test_claim_cases_are_seeded_runnable_and_reviewable(tmp_path, monkeypatch):
    from fitwitness.claims.synth import generate_cases
    from fitwitness.api.app import create_app

    generate_cases(tmp_path, n=11, seed=5)  # one case per scenario
    monkeypatch.setenv("FITWITNESS_CLAIMS_DIR", str(tmp_path))
    a = TestClient(create_app())
    assert a.post("/api/demo-sessions").status_code == 200
    cases = a.get("/api/claims/cases").json()
    assert {c["scenario"] for c in cases} >= {"clean", "duplicate", "high_amount"} and all(c["latest_run"] is None for c in cases)
    clean = next(c for c in cases if c["scenario"] == "clean")
    png = a.get(f"/api/claims/cases/{clean['case_id']}/documents/{clean['documents'][0]['id']}/png")
    assert png.status_code == 200 and png.headers["content-type"] == "image/png"
    run = a.post(f"/api/claims/cases/{clean['case_id']}/run", headers={"Idempotency-Key": "api-claim-1"}).json()
    view = a.get("/api/runs/" + run["id"]).json()
    assert view["kind"] == "claim" and view["state"] == "completed"
    assert view["claim"]["decision"]["outcome"] == "APPROVE" and view["claim"]["payout"]["status"] == "paid"
    assert a.get("/api/claims/ledger").json()[0]["claim_id"] == clean["claim_id"]
    assert next(c for c in a.get("/api/claims/cases").json() if c["case_id"] == clean["case_id"])["latest_run"]["state"] == "completed"
    high = next(c for c in cases if c["scenario"] == "high_amount")
    run = a.post(f"/api/claims/cases/{high['case_id']}/run", headers={"Idempotency-Key": "api-claim-2"}).json()
    assert a.get("/api/runs/" + run["id"]).json()["state"] == "waiting_input"
    assert a.post(f"/api/runs/{run['id']}/resume", json={"outcome": "MAYBE", "reviewer": "qa"}).status_code == 422
    assert a.post(f"/api/runs/{run['id']}/resume", json={"outcome": "DENY", "reviewer": "qa", "note": "서류 재요청"}).status_code == 200
    final = a.get("/api/runs/" + run["id"]).json()
    assert final["state"] == "completed" and final["claim"]["decision"]["outcome"] == "DENY" and final["claim"]["payout"]["status"] == "skipped"
    # a drawing revision must not invalidate claim runs
    a.post("/api/demo/revision")
    assert a.get("/api/runs/" + run["id"]).json()["state"] == "completed"


def test_documents_sent_after_a_hold_are_attached_to_the_same_claim_over_the_api(tmp_path, monkeypatch):
    from fitwitness.claims.synth import generate_cases
    from fitwitness.api.app import create_app

    generate_cases(tmp_path, n=11, seed=5)
    monkeypatch.setenv("FITWITNESS_CLAIMS_DIR", str(tmp_path))
    a = TestClient(create_app())
    assert a.post("/api/demo-sessions").status_code == 200
    cases = a.get("/api/claims/cases").json()
    missing = next(c for c in cases if c["scenario"] == "missing_doc")
    waiting = [d["id"] for d in missing["supplementary_documents"]]
    assert waiting and not set(waiting) & {d["id"] for d in missing["documents"]}
    png = a.get(f"/api/claims/cases/{missing['case_id']}/documents/{waiting[0]}/png")  # the waiting document can be looked at
    assert png.status_code == 200 and png.headers["content-type"] == "image/png"
    held = a.post(f"/api/claims/cases/{missing['case_id']}/run", headers={"Idempotency-Key": "api-supp-1"}).json()
    assert a.post(f"/api/runs/{held['id']}/supplement", json={"document_ids": waiting}).status_code == 409  # not held yet: it is waiting for a person
    assert a.post(f"/api/runs/{held['id']}/resume", json={"outcome": "REQUEST_INFO", "reviewer": "qa", "note": "서류를 보내 주세요"}).status_code == 200
    assert a.post(f"/api/runs/{held['id']}/supplement", json={"document_ids": ["nope"]}).status_code == 422
    assert a.post(f"/api/runs/{held['id']}/supplement", json={"document_ids": []}).status_code == 422
    assert a.post("/api/runs/does-not-exist/supplement", json={"document_ids": waiting}).status_code == 404
    second = a.post(f"/api/runs/{held['id']}/supplement", json={"document_ids": waiting})
    assert second.status_code == 200
    view = a.get("/api/runs/" + second.json()["id"]).json()
    assert view["id"] != held["id"] and view["state"] == "completed"
    assert view["claim"]["resubmission"]["of_run"] == held["id"] and view["claim"]["payout"]["status"] == "paid"
    assert a.post(f"/api/runs/{held['id']}/supplement", json={"document_ids": waiting}).json()["id"] == view["id"]  # a second click is the same run
    assert [p["claim_id"] for p in a.get("/api/claims/ledger").json()] == [missing["claim_id"]]
    latest = next(c for c in a.get("/api/claims/cases").json() if c["case_id"] == missing["case_id"])["latest_run"]
    assert latest["run_id"] == view["id"]
    clean = next(c for c in cases if c["scenario"] == "clean")
    paid = a.post(f"/api/claims/cases/{clean['case_id']}/run", headers={"Idempotency-Key": "api-supp-2"}).json()
    assert a.post(f"/api/runs/{paid['id']}/supplement", json={"document_ids": waiting}).status_code == 409


def test_the_api_refuses_a_client_built_claim_and_an_oversized_body_and_sends_its_headers(clients):
    a, _ = clients
    assert a.post("/api/demo-sessions").status_code == 200
    forged = {"kind": "claim", "claim": {"case_id": "CLM-00007", "claim_id": "MINE-1", "policy": {"policy_id": "POL-OTHER"}}}
    assert a.post("/api/runs", json=forged).status_code == 422  # a claim run is built from the stored case, never from the client
    assert a.post("/api/runs", json={"claim": {"claim_id": "MINE-2"}}).status_code == 422
    big = a.post("/api/runs", content=b'{"search":{"text":"' + b"a" * 70_000 + b'"}}', headers={"content-type": "application/json"})
    assert big.status_code == 413
    r = a.get("/api/documents")
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"] and "script-src 'self'" in r.headers["content-security-policy"]
    assert r.headers["x-frame-options"] == "DENY" and r.headers["x-content-type-options"] == "nosniff"
    assert a.post("/api/runs", json={"search": {"text": "x" * 10}, "model_id": "m" * 101}).status_code == 422
    assert a.post("/api/search", json={"requirements": [{"field": "thickness", "value": {"low": "1E+999999", "high": "1E+999999"}, "unit": "in"}]}).status_code == 422


def test_a_repeated_idempotency_key_does_not_spend_the_hourly_run_quota(env):
    from fitwitness.contracts import RunRequest
    from fitwitness.runtime.jobs import QuotaExceeded
    from uuid import uuid4

    r, j, s = env
    quota = ("test-runs:" + uuid4().hex, 1)
    first = j.enqueue(s, RunRequest(), "quota-1", quota=quota)
    assert j.enqueue(s, RunRequest(), "quota-1", quota=quota).id == first.id  # the same key returns the run and spends nothing
    with pytest.raises(QuotaExceeded):
        j.enqueue(s, RunRequest(), "quota-2", quota=quota)
