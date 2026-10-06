import os, importlib.util
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def clients():
    assert importlib.util.find_spec("fitwitness.api.app"), "API not implemented"
    from fitwitness.api.app import create_app

    app = create_app()
    return TestClient(app), TestClient(app)


def test_session_scope_and_csrf(clients):
    a, b = clients
    assert a.get("/api/documents").status_code == 401
    assert a.post("/api/demo-sessions").status_code == 200
    assert b.post("/api/demo-sessions").status_code == 200
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
