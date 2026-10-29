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


def test_a_hold_is_told_to_the_claimant_and_a_reviewers_answers_carry_the_servers_signature(tmp_path, monkeypatch):
    import os
    import psycopg
    from datetime import date, timedelta
    from fitwitness.claims.synth import generate_cases
    from fitwitness.api.app import create_app

    generate_cases(tmp_path, n=11, seed=5)
    monkeypatch.setenv("FITWITNESS_CLAIMS_DIR", str(tmp_path))
    a = TestClient(create_app())
    assert a.post("/api/demo-sessions").status_code == 200
    cases = a.get("/api/claims/cases").json()
    missing = next(c for c in cases if c["scenario"] == "missing_doc")
    waiting = [d["id"] for d in missing["supplementary_documents"]]
    held = a.post(f"/api/claims/cases/{missing['case_id']}/run", headers={"Idempotency-Key": "api-notice-1"}).json()
    forged = {"step": 1, "decided_at": "2020-01-01T00:00:00+00:00", "prev": "", "signature": "0" * 64, "key_id": "deadbeef"}
    answered = a.post(f"/api/runs/{held['id']}/resume", json={"outcome": "REQUEST_INFO", "reviewer": "심사자A", "note": "진단서를 보내 주세요", "receipt": forged})
    assert answered.status_code == 200
    view = a.get("/api/runs/" + held["id"]).json()["claim"]
    notice = next(n for n in view["notices"] if n["kind"] == "request_info")
    assert notice["respond_by"] == str(date.today() + timedelta(days=14)) and "진단서를 보내 주세요" in notice["body"] and "실제로" not in notice["subject"]
    assert view["human"]["receipt"]["signature"] != forged["signature"] and view["human"]["receipt"]["key_id"] != "deadbeef"  # a receipt sent by the client is replaced
    rows = a.get(f"/api/runs/{held['id']}/approvals").json()["approvals"]
    assert [r["status"] for r in rows] == ["valid"] and rows[0]["reviewer"] == "심사자A" and rows[0]["note"] == "진단서를 보내 주세요"
    assert a.get("/api/runs/does-not-exist/approvals").status_code == 404

    # An answer edited in the database afterwards no longer verifies.
    dsn = os.getenv("FITWITNESS_DATABASE_URL", "postgresql://fwadmin@/postgres?host=/tmp&port=55439")
    with psycopg.connect(dsn, autocommit=True) as c:
        c.execute("UPDATE fw_runs SET result=jsonb_set(result,'{approvals,0,note}','\"서류 불필요\"') WHERE id=%s", (held["id"],))
    assert [r["status"] for r in a.get(f"/api/runs/{held['id']}/approvals").json()["approvals"]] == ["altered"]

    # After the last day the documents are refused, with the date in the answer; a hold with no deadline stays open.
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    with psycopg.connect(dsn, autocommit=True) as c:
        c.execute("UPDATE fw_runs SET result=jsonb_set(result,'{notices,0,respond_by}',to_jsonb(%s::text)) WHERE id=%s", (yesterday, held["id"]))
    late = a.post(f"/api/runs/{held['id']}/supplement", json={"document_ids": waiting})
    assert late.status_code == 409 and yesterday in late.json()["detail"]
    with psycopg.connect(dsn, autocommit=True) as c:
        c.execute("UPDATE fw_runs SET result=jsonb_set(result,'{notices,0,respond_by}',to_jsonb(%s::text)) WHERE id=%s",
                  (str(date.today()), held["id"]))
    assert a.post(f"/api/runs/{held['id']}/supplement", json={"document_ids": waiting}).status_code == 200  # the last day still counts

    # A finished claim is told its decision, and the second approval of a senior claim is signed after the first.
    clean = next(c for c in cases if c["scenario"] == "clean")
    paid = a.post(f"/api/claims/cases/{clean['case_id']}/run", headers={"Idempotency-Key": "api-notice-2"}).json()
    told = a.get("/api/runs/" + paid["id"]).json()["claim"]["notices"]
    assert [n["kind"] for n in told] == ["decision"] and "지급" in told[0]["subject"] and told[0]["respond_by"] is None
    assert a.get(f"/api/runs/{paid['id']}/approvals").json()["approvals"] == []


def live_env(monkeypatch, **changes):
    values = {"FITWITNESS_LIVE_DEMO": "on", "ANTHROPIC_API_KEY": "test-not-a-real-key", "FITWITNESS_ANTHROPIC_MODEL": "stub-model",
              "FITWITNESS_ANTHROPIC_INPUT_USD_PER_MILLION": "1", "FITWITNESS_ANTHROPIC_OUTPUT_USD_PER_MILLION": "5",
              "FITWITNESS_LIVE_DAILY_USD": "0.10", "FITWITNESS_LIVE_RUN_USD": "0.04", "FITWITNESS_LIVE_PER_SESSION": "5",
              # A run started here is executed by a worker process, where nothing in this process can stand in for the model.
              # Its provider client is pointed at a port nothing listens on, so no test request ever leaves the machine.
              "ANTHROPIC_API_URL": "http://127.0.0.1:9", "ANTHROPIC_BASE_URL": "http://127.0.0.1:9", **changes}
    for key, value in values.items():
        monkeypatch.setenv(key, value)


def live_client(tmp_path, monkeypatch, **changes):
    from uuid import uuid4
    from fitwitness.api import live
    from fitwitness.api.app import create_app
    from fitwitness.claims.synth import generate_cases

    generate_cases(tmp_path, n=11, seed=5)
    monkeypatch.setenv("FITWITNESS_CLAIMS_DIR", str(tmp_path))
    live_env(monkeypatch, **changes)
    day = f"test-{uuid4().hex[:10]}"
    monkeypatch.setattr(live, "today", lambda: day)  # a day of its own: the ceiling is counted from zero
    a = TestClient(create_app())
    assert a.post("/api/demo-sessions").status_code == 200
    return a, a.get("/api/claims/cases").json(), day


def test_a_live_claim_run_is_off_by_default_and_never_reads_a_key_the_operator_did_not_enable(tmp_path, monkeypatch):
    from fitwitness.api.app import create_app
    from fitwitness.claims.synth import generate_cases

    generate_cases(tmp_path, n=11, seed=5)
    monkeypatch.setenv("FITWITNESS_CLAIMS_DIR", str(tmp_path))
    monkeypatch.delenv("FITWITNESS_LIVE_DEMO", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-not-a-real-key")
    a = TestClient(create_app())
    assert a.post("/api/demo-sessions").status_code == 200
    assert a.get("/api/capabilities").json()["live"] == {"enabled": False}
    clean = next(c for c in a.get("/api/claims/cases").json() if c["scenario"] == "clean")
    refused = a.post(f"/api/claims/cases/{clean['case_id']}/run", headers={"x-claim-live": "1", "Idempotency-Key": "live-off-1"})
    assert refused.status_code == 409 and "꺼져" in refused.json()["detail"]
    plain = a.post(f"/api/claims/cases/{clean['case_id']}/run", headers={"Idempotency-Key": "live-off-2"}).json()
    assert a.get("/api/runs/" + plain["id"]).json()["state"] == "completed"  # the rules path is untouched


def test_live_runs_reserve_the_days_ceiling_atomically_and_a_repeated_key_reserves_nothing(tmp_path, monkeypatch):
    from decimal import Decimal
    import os

    a, cases, day = live_client(tmp_path, monkeypatch)
    caps = a.get("/api/capabilities").json()["live"]
    assert caps == {"enabled": True, "model": "stub-model", "per_session": 5, "remaining_today": 2}
    from fitwitness.storage.repository import Repository
    repo = Repository(os.getenv("FITWITNESS_DATABASE_URL", "postgresql://fwadmin@/postgres?host=/tmp&port=55439"))
    by_scenario = {f"case{i}": c for i, c in enumerate(cases)}  # any distinct cases will do: the worker's model call goes nowhere
    first = a.post(f"/api/claims/cases/{by_scenario['case0']['case_id']}/run", headers={"x-claim-live": "1", "Idempotency-Key": "live-1"})
    assert first.status_code == 200
    # The worker's model call goes nowhere (see live_env): what is checked is what was reserved and what was queued.
    assert Decimal(str(repo.live_reserved(day))) == Decimal("0.04")
    again = a.post(f"/api/claims/cases/{by_scenario['case0']['case_id']}/run", headers={"x-claim-live": "1", "Idempotency-Key": "live-1"})
    assert again.status_code == 200 and again.json()["id"] == first.json()["id"] and Decimal(str(repo.live_reserved(day))) == Decimal("0.04")
    second = a.post(f"/api/claims/cases/{by_scenario['case1']['case_id']}/run", headers={"x-claim-live": "1", "Idempotency-Key": "live-2"})
    assert second.status_code == 200 and Decimal(str(repo.live_reserved(day))) == Decimal("0.08")
    third = a.post(f"/api/claims/cases/{by_scenario['case2']['case_id']}/run", headers={"x-claim-live": "1", "Idempotency-Key": "live-3"})
    assert third.status_code == 429 and "한도" in third.json()["detail"] and Decimal(str(repo.live_reserved(day))) == Decimal("0.08")
    assert a.get("/api/capabilities").json()["live"]["remaining_today"] == 0
    # What the worker will run: the operator's model, and a budget that cannot exceed the per-run ceiling.
    import psycopg
    with psycopg.connect(os.getenv("FITWITNESS_DATABASE_URL", "postgresql://fwadmin@/postgres?host=/tmp&port=55439")) as c:
        request = c.execute("SELECT request FROM fw_runs WHERE id=%s", (first.json()["id"],)).fetchone()[0]
    assert request["provider"] == "anthropic" and request["model_id"] == "stub-model" and Decimal(str(request["budget"]["max_cost_usd"])) == Decimal("0.04")
    assert request["budget"]["max_model_calls"] == 3
    # The rules path still works after the ceiling is gone.
    ok = a.post(f"/api/claims/cases/{by_scenario['case2']['case_id']}/run", headers={"Idempotency-Key": "live-4"})
    assert ok.status_code == 200


def test_each_workspace_gets_a_few_live_runs(tmp_path, monkeypatch):
    a, cases, day = live_client(tmp_path, monkeypatch, FITWITNESS_LIVE_PER_SESSION="1", FITWITNESS_LIVE_DAILY_USD="1.00")
    first = a.post(f"/api/claims/cases/{cases[0]['case_id']}/run", headers={"x-claim-live": "1", "Idempotency-Key": "share-1"})
    assert first.status_code == 200
    second = a.post(f"/api/claims/cases/{cases[1]['case_id']}/run", headers={"x-claim-live": "1", "Idempotency-Key": "share-2"})
    assert second.status_code == 429 and "1번까지" in second.json()["detail"]


def test_concurrent_reservations_cannot_pass_the_ceiling(tmp_path, monkeypatch):
    import os
    from concurrent.futures import ThreadPoolExecutor
    from decimal import Decimal
    from uuid import uuid4
    from fitwitness.storage.repository import Repository

    repo = Repository(os.getenv("FITWITNESS_DATABASE_URL", "postgresql://fwadmin@/postgres?host=/tmp&port=55439"))
    repo.migrate()
    day = f"race-{uuid4().hex[:8]}"
    with ThreadPoolExecutor(16) as pool:
        won = list(pool.map(lambda _: repo.live_reserve(day, Decimal("0.04"), Decimal("0.10")), range(16)))
    assert sum(won) == 2 and Decimal(str(repo.live_reserved(day))) == Decimal("0.08")
    assert not repo.live_reserve(day, Decimal("0.20"), Decimal("0.10")) and not repo.live_reserve(day, Decimal(0), Decimal("1"))


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
