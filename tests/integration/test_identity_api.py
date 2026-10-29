"""With an OIDC issuer configured, a reviewer's answer is whom the token proves: the second approval must come from a
different account, and the receipt signs the account. Without one (the public demo) nothing changes."""

import pytest
from fastapi.testclient import TestClient

from identity_helpers import FakeIssuer


@pytest.fixture
def desk(tmp_path, monkeypatch):
    from fitwitness.api.app import create_app
    from fitwitness.claims.synth import generate_cases

    fake = FakeIssuer()
    monkeypatch.setenv("FITWITNESS_OIDC_ISSUER", fake.issuer)
    monkeypatch.setenv("FITWITNESS_OIDC_AUDIENCE", fake.audience)
    monkeypatch.setenv("FITWITNESS_OIDC_JWKS_FILE", fake.write_jwks(tmp_path / "jwks.json"))
    generate_cases(tmp_path / "claims", n=11, seed=5)
    monkeypatch.setenv("FITWITNESS_CLAIMS_DIR", str(tmp_path / "claims"))
    client = TestClient(create_app())
    assert client.post("/api/demo-sessions").status_code == 200
    return fake, client


def park(client, *, senior: bool):
    """A claim parked for a person with something to pay. ``senior``: its deadline is made to pass and the escalation
    sweep moves it to senior handling (a reason and two different people), as a claim nobody answered in time."""
    import os
    import psycopg

    for i, case in enumerate(client.get("/api/claims/cases").json()):
        run = client.post(f"/api/claims/cases/{case['case_id']}/run", headers={"Idempotency-Key": f"id-{senior}-{i}"}).json()
        view = client.get(f"/api/runs/{run['id']}").json()
        if view["state"] != "waiting_input":
            continue
        asked = [e for e in client.get(f"/api/runs/{run['id']}/events").json() if e["kind"] == "waiting_input"][-1]["payload"]
        if asked["total_amount"] <= 0:
            continue
        if senior:
            with psycopg.connect(os.getenv("FITWITNESS_DATABASE_URL", "postgresql://fwadmin@/postgres?host=/tmp&port=55439"), autocommit=True) as c:
                c.execute("UPDATE fw_runs SET review_due_at=now()-interval '1 second' WHERE id=%s", (run["id"],))
                c.execute("UPDATE fw_review_due SET due_at=now()-interval '1 second' WHERE run_id=%s", (run["id"],))
            assert client.app.state.jobs.escalate_overdue(limit=10_000) >= 1
            assert client.get(f"/api/runs/{run['id']}").json()["escalated"]
        return run["id"], asked["total_amount"]
    pytest.skip("no suitable claim in this corpus")


def test_the_demo_stays_anonymous_when_no_issuer_is_configured(monkeypatch):
    from fitwitness.api.app import create_app

    for name in ("FITWITNESS_OIDC_ISSUER", "FITWITNESS_OIDC_AUDIENCE", "FITWITNESS_OIDC_JWKS_FILE"):
        monkeypatch.delenv(name, raising=False)
    client = TestClient(create_app())
    assert client.get("/api/capabilities").json()["identity"] == {"mode": "anonymous"}


def test_an_answer_needs_a_token_and_the_reviewer_is_whom_it_proves(desk):
    fake, client = desk
    assert client.get("/api/capabilities").json()["identity"] == {"mode": "oidc", "issuer": fake.issuer}
    run_id, amount = park(client, senior=False)
    body = {"outcome": "APPROVE", "reviewer": "타이핑한 이름", "note": "", "account": "forged#account"}
    assert client.post(f"/api/runs/{run_id}/resume", json=body).status_code == 401
    for bad in (fake.token(aud="someone-else"), fake.token(exp=1), fake.token(key=fake.other), "not.a.token"):
        assert client.post(f"/api/runs/{run_id}/resume", json=body, headers={"Authorization": f"Bearer {bad}"}).status_code == 401
    assert client.get(f"/api/runs/{run_id}").json()["state"] == "waiting_input", "a refused answer leaves the run waiting"
    ok = client.post(f"/api/runs/{run_id}/resume", json=body, headers={"Authorization": f"Bearer {fake.token('u1', '김심사')}"})
    assert ok.status_code == 200
    view = client.get(f"/api/runs/{run_id}").json()
    assert view["state"] == "completed" and view["claim"]["payout"]["amount"] == amount
    human = view["claim"]["human"]
    assert human["reviewer"] == "김심사" and human["account"] == f"{fake.issuer}#u1", "the typed name and the forged account were replaced"
    rows = client.get(f"/api/runs/{run_id}/approvals").json()["approvals"]
    assert [(r["status"], r["reviewer"], r["account"]) for r in rows] == [("valid", "김심사", f"{fake.issuer}#u1")]


def test_the_second_approval_must_come_from_another_account_whatever_the_name_says(desk):
    fake, client = desk
    run_id, amount = park(client, senior=True)
    note = {"note": "서류와 금액을 직접 확인했습니다"}
    first = client.post(f"/api/runs/{run_id}/resume", json={"outcome": "APPROVE", "reviewer": "x", **note},
                        headers={"Authorization": f"Bearer {fake.token('u1', '김심사')}"})
    assert first.status_code == 200 and client.get(f"/api/runs/{run_id}").json()["state"] == "waiting_input"
    asked = [e for e in client.get(f"/api/runs/{run_id}/events").json() if e["kind"] == "waiting_input"][-1]["payload"]
    assert asked["step"] == 2 and asked["first_account"] == f"{fake.issuer}#u1" and asked["approvals_required"] == 2
    same_account_other_name = client.post(f"/api/runs/{run_id}/resume", json={"outcome": "APPROVE", "reviewer": "x", **note},
                                          headers={"Authorization": f"Bearer {fake.token('u1', '박팀장')}"})
    assert same_account_other_name.status_code == 422
    second = client.post(f"/api/runs/{run_id}/resume", json={"outcome": "APPROVE", "reviewer": "x", **note},
                         headers={"Authorization": f"Bearer {fake.token('u2', '김심사', alg='ES256')}"})  # the same name, another account
    assert second.status_code == 200
    view = client.get(f"/api/runs/{run_id}").json()
    assert view["state"] == "completed" and view["claim"]["payout"]["amount"] == amount
    rows = client.get(f"/api/runs/{run_id}/approvals").json()["approvals"]
    assert [r["status"] for r in rows] == ["valid", "valid"]
    assert [r["account"] for r in rows] == [f"{fake.issuer}#u1", f"{fake.issuer}#u2"] and [r["reviewer"] for r in rows] == ["김심사", "김심사"]
    assert [e for e in client.get(f"/api/runs/{run_id}/events").json() if e["kind"] == "human_review"][-1]["payload"]["accounts"] == [f"{fake.issuer}#u1", f"{fake.issuer}#u2"]
