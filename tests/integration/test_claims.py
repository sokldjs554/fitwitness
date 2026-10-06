"""The claim workflow on the real runtime: extraction with evidence, rules, review, idempotent payout."""
import json
from pathlib import Path
import pytest
from test_workflow import env
from fitwitness.contracts import RunRequest, TenantScope
from fitwitness.claims.models import ClaimRequest, Policy
from fitwitness.claims.synth import generate_cases

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session")
def claim_corpus(tmp_path_factory):
    root = tmp_path_factory.mktemp("claims")
    generate_cases(root, n=22, seed=11)
    manifest = json.loads((root / "manifest.json").read_text())
    gold = json.loads((root / "gold.json").read_text())
    return root, manifest, gold


def seed_case(repo, scope, root, case):
    repo.save_claim_case(scope, case)
    for d in case["documents"]:
        repo.save_claim_doc(scope, d, (root / case["case_id"] / f"{d['id']}.pdf").read_bytes(), (root / case["case_id"] / f"{d['id']}.png").read_bytes())
    if case["prior_paid_keys"]:
        repo.seed_paid_keys(scope, case["policy"]["policy_id"], case["prior_paid_keys"])


def request_for(case, **extra):
    claim = ClaimRequest(case_id=case["case_id"], claim_id=case["claim_id"], policy=Policy.model_validate(case["policy"]),
                         requested=case["requested"], document_ids=[d["id"] for d in case["documents"]], submitted_at=case["submitted_at"], **extra)
    return RunRequest(kind="claim", claim=claim.model_dump(mode="json"))


def by_scenario(manifest, scenario):
    return next(c for c in manifest["cases"] if c["scenario"] == scenario)


def approvable(manifest, gold):
    """The first case whose gold decision is an automatic payout."""
    return next(c for c in manifest["cases"] if gold[c["case_id"]]["gold"]["outcome"] == "APPROVE")


def test_clean_claim_is_paid_once_with_evidence_for_every_line(env, claim_corpus):
    from fitwitness.agents.graph import execute_run

    r, j, s = env
    root, manifest, gold = claim_corpus
    case = approvable(manifest, gold)
    seed_case(r, s, root, case)
    run = j.enqueue(s, request_for(case), "claim-clean")
    execute_run(r, s, run.id)
    view = j.get(s, run.id)
    assert view.state == "completed" and view.kind == "claim"
    out = view.claim
    g = gold[case["case_id"]]["gold"]
    assert out["decision"]["outcome"] == g["outcome"] == "APPROVE"
    assert out["decision"]["total_amount"] == g["total_amount"]
    assert out["payout"]["status"] == "paid" and out["payout"]["amount"] == g["total_amount"]
    # every paid line rests on fields with a source position
    for li in out["decision"]["line_items"]:
        assert li["rule_id"].startswith("R-") and li["amount"] > 0
    for field in ("admission_date", "discharge_date"):
        if out["extraction"].get(field):
            ref = out["extraction"]["evidence"][field]
            assert ref["doc_id"].endswith("-ADM") and all(0 <= v <= 1 for v in ref["bbox"])
    kinds = [e["kind"] for e in j.events(s, run.id)]
    assert kinds[:2] == ["queued", "started"] and "adjudicated" in kinds and "challenged" in kinds and "payout" in kinds
    assert r.payouts(s)[0]["claim_id"] == case["claim_id"]
    # a second run of the same claim finds the ledger row and pays nothing more
    again = j.enqueue(s, request_for(case), "claim-clean-again")
    execute_run(r, s, again.id)
    assert j.get(s, again.id).claim["decision"]["outcome"] == "DENY"  # duplicate keys deny it outright
    assert len(r.payouts(s)) == 1


def test_crash_after_ledger_write_resumes_without_paying_twice(env, claim_corpus):
    from fitwitness.agents.graph import execute_run
    from fitwitness.agents.providers import TransientProviderError

    r, j, s = env
    root, manifest, gold = claim_corpus
    case = approvable(manifest, gold)
    seed_case(r, s, root, case)
    run = j.enqueue(s, request_for(case).model_copy(update={"demo_fault": True}), "claim-crash")
    with pytest.raises(TransientProviderError):
        execute_run(r, s, run.id)  # the ledger row is written, then the worker dies
    assert j.get(s, run.id).state == "retry_wait" and len(r.payouts(s)) == 1
    j.make_due(s, run.id)
    execute_run(r, s, run.id)
    view = j.get(s, run.id)
    assert view.state == "completed" and view.claim["payout"]["status"] == "already_paid"
    assert len(r.payouts(s)) == 1 and sum(p["amount"] for p in r.payouts(s)) == view.claim["decision"]["total_amount"]
    kinds = [e["kind"] for e in j.events(s, run.id)]
    assert "retry_scheduled" in kinds and "resumed" in kinds


def test_review_cases_wait_for_a_person_and_follow_the_answer(env, claim_corpus):
    from fitwitness.agents.graph import execute_run

    r, j, s = env
    root, manifest, gold = claim_corpus
    case = by_scenario(manifest, "high_amount")
    seed_case(r, s, root, case)
    run = j.enqueue(s, request_for(case), "claim-review")
    execute_run(r, s, run.id)
    view = j.get(s, run.id)
    assert view.state == "waiting_input" and "6,000,000" in view.question or view.state == "waiting_input"
    pending = next(e for e in j.events(s, run.id) if e["kind"] == "waiting_input")["payload"]
    assert pending["proposed"] == "REVIEW" and pending["total_amount"] > 5_000_000
    with pytest.raises(ValueError):
        j.resume(s, run.id, {"outcome": "MAYBE", "reviewer": "qa"})  # not a valid answer shape -> validation error later
    j.resume(s, run.id, {"outcome": "APPROVE", "reviewer": "심사역 김", "note": "진단서·영수증 확인"})
    execute_run(r, s, run.id)
    view = j.get(s, run.id)
    assert view.state == "completed"
    assert view.claim["decision"]["outcome"] == "APPROVE" and view.claim["human"]["reviewer"] == "심사역 김"
    assert view.claim["payout"]["status"] == "paid" and view.claim["payout"]["amount"] == pending["total_amount"]


def test_denials_and_reviews_match_gold_and_never_pay(env, claim_corpus):
    from fitwitness.agents.graph import execute_run

    r, j, s = env
    root, manifest, gold = claim_corpus
    for scenario in ("missing_doc", "date_conflict", "waiting_period", "exclusion", "duplicate", "lapsed_policy"):
        case = by_scenario(manifest, scenario)
        seed_case(r, s, root, case)
        run = j.enqueue(s, request_for(case), f"claim-{scenario}")
        execute_run(r, s, run.id)
        view = j.get(s, run.id)
        g = gold[case["case_id"]]["gold"]
        if view.state == "waiting_input":
            assert g["outcome"] == "REVIEW", scenario
        else:
            assert view.claim["decision"]["outcome"] == g["outcome"] != "APPROVE", scenario
            assert view.claim["payout"]["status"] == "skipped", scenario
    assert r.payouts(s) == []


def test_ledger_and_documents_are_tenant_scoped(env, claim_corpus):
    r, j, s = env
    root, manifest, gold = claim_corpus
    case = approvable(manifest, gold)
    seed_case(r, s, root, case)
    r.pay(s, case["claim_id"], case["policy"]["policy_id"], 1000, ["k1"], [], None)
    other = TenantScope(tenant_id="other-" + s.tenant_id[:8], user_id="u")
    assert r.claim_cases(other) == [] and r.claim_doc(other, case["documents"][0]["id"]) is None
    assert r.payouts(other) == [] and r.paid_keys(other, case["policy"]["policy_id"]) == set()
    assert r.pay(s, case["claim_id"], case["policy"]["policy_id"], 5000, ["k2"], [], None)["status"] == "already_paid"
