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
    for d in [*case["documents"], *case.get("supplementary_documents", [])]:
        repo.save_claim_doc(
            scope, d, (root / case["case_id"] / f"{d['id']}.pdf").read_bytes(), (root / case["case_id"] / f"{d['id']}.png").read_bytes()
        )
    if case["prior_paid_keys"]:
        repo.seed_paid_keys(scope, case["policy"]["policy_id"], case["prior_paid_keys"])


def request_for(case, **extra):
    claim = ClaimRequest(
        case_id=case["case_id"],
        claim_id=case["claim_id"],
        policy=Policy.model_validate(case["policy"]),
        requested=case["requested"],
        document_ids=[d["id"] for d in case["documents"]],
        submitted_at=case["submitted_at"],
        **extra,
    )
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


def test_a_reviewer_can_hold_a_claim_for_documents_without_paying_or_refusing_it(env, claim_corpus):
    from fitwitness.agents.graph import execute_run
    from fitwitness.claims.review import ReviewRuleError

    r, j, s = env
    root, manifest, gold = claim_corpus
    case = by_scenario(manifest, "missing_doc")
    seed_case(r, s, root, case)
    run = j.enqueue(s, request_for(case), "claim-hold")
    execute_run(r, s, run.id)
    assert j.get(s, run.id).state == "waiting_input"
    with pytest.raises(ReviewRuleError):  # the request has to name what is missing
        j.resume(s, run.id, {"outcome": "REQUEST_INFO", "reviewer": "심사역 김", "note": " "})
    assert j.get(s, run.id).state == "waiting_input"
    j.resume(s, run.id, {"outcome": "REQUEST_INFO", "reviewer": "심사역 김", "note": "수술확인서 사본을 보내 달라고 안내"})
    execute_run(r, s, run.id)
    view = j.get(s, run.id)
    assert view.state == "completed"
    claim = view.claim
    assert claim["decision"]["outcome"] == "REVIEW" and claim["decision"]["total_amount"] == 0
    assert claim["human"]["outcome"] == "REQUEST_INFO" and claim["payout"]["status"] == "skipped" and claim["payout"]["amount"] == 0
    assert any(x["rule_id"] == "R-HUMAN-02" and "수술확인서" in x["message"] for x in claim["decision"]["reasons"])
    assert "보류" in claim["explanation"] and "지급합니다" not in claim["explanation"]
    assert [p for p in r.payouts(s) if p["claim_id"] == case["claim_id"]] == []  # nothing reached the ledger


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


def _held(env, claim_corpus, scenario, key):
    """A claim of ``scenario`` run, parked for a reviewer, and held for documents."""
    from fitwitness.agents.graph import execute_run

    r, j, s = env
    root, manifest, gold = claim_corpus
    case = by_scenario(manifest, scenario)
    seed_case(r, s, root, case)
    run = j.enqueue(s, request_for(case), key)
    execute_run(r, s, run.id)
    assert j.get(s, run.id).state == "waiting_input"
    j.resume(s, run.id, {"outcome": "REQUEST_INFO", "reviewer": "심사역 김", "note": "누락된 서류를 보내 달라고 안내"})
    execute_run(r, s, run.id)
    assert j.get(s, run.id).claim["human"]["outcome"] == "REQUEST_INFO"
    return case, run, gold[case["case_id"]]


def test_documents_sent_after_a_hold_continue_the_same_claim_and_pay_it_once(env, claim_corpus):
    from fitwitness.agents.graph import execute_run
    from fitwitness.claims import resubmit

    r, j, s = env
    case, held, g = _held(env, claim_corpus, "missing_doc", "resubmit-missing")
    waiting = [d["id"] for d in case["supplementary_documents"]]
    planned = resubmit.plan(case, j.raw(s, held.id), waiting)
    assert planned.claim_id == case["claim_id"] and planned.resubmission.of_run == held.id and planned.resubmission.attempt == 2
    assert planned.resubmission.held_by == "심사역 김" and "누락" in planned.resubmission.hold_note
    assert planned.resubmission.replaced == [] and set(planned.document_ids) == {d["id"] for d in case["documents"]} | set(waiting)
    body = RunRequest(kind="claim", claim=planned.model_dump(mode="json"))
    second = j.enqueue(s, body, "resubmit:" + held.id)
    assert j.enqueue(s, body, "resubmit:" + held.id).id == second.id  # a second click starts nothing new
    execute_run(r, s, second.id)
    view = j.get(s, second.id)
    after = g["gold_after_supplement"]
    assert after["outcome"] == "APPROVE"  # the corpus case chosen here pays once complete
    assert view.state == "completed", view.state
    claim = view.claim
    assert claim["resubmission"]["of_run"] == held.id and set(claim["document_ids"]) == set(planned.document_ids)
    assert claim["decision"]["outcome"] == "APPROVE" and claim["decision"]["total_amount"] == after["total_amount"]
    assert claim["payout"]["status"] == "paid" and claim["payout"]["amount"] == after["total_amount"]
    rows = [p for p in r.payouts(s) if p["claim_id"] == case["claim_id"]]
    assert len(rows) == 1 and rows[0]["amount"] == after["total_amount"]
    # the held run is still the record of the hold: nothing in it changed
    old = j.get(s, held.id).claim
    assert old["human"]["outcome"] == "REQUEST_INFO" and old["payout"]["status"] == "skipped" and old["resubmission"] is None
    # the documents asked for cannot be attached twice, and another set cannot be attached to the same hold
    other = body.model_copy(update={"claim": {**body.claim, "document_ids": body.claim["document_ids"][:-1]}})
    with pytest.raises(ValueError):
        j.enqueue(s, other, "resubmit:" + held.id)


def test_a_corrected_document_replaces_the_one_it_corrects(env, claim_corpus):
    from fitwitness.agents.graph import execute_run
    from fitwitness.claims import resubmit

    r, j, s = env
    root, manifest, gold = claim_corpus
    case, held, g = _held(env, claim_corpus, "grade_missing", "resubmit-grade")
    old_surgery = next(d["id"] for d in case["documents"] if d["kind"] == "surgery")
    new_surgery = case["supplementary_documents"][0]["id"]
    planned = resubmit.plan(case, j.raw(s, held.id), [new_surgery])
    assert (
        planned.resubmission.replaced == [old_surgery] and old_surgery not in planned.document_ids and new_surgery in planned.document_ids
    )
    second = j.enqueue(s, RunRequest(kind="claim", claim=planned.model_dump(mode="json")), "resubmit:" + held.id)
    execute_run(r, s, second.id)
    view = j.get(s, second.id)
    after = g["gold_after_supplement"]
    # complete now: the run waits for a person exactly when the rules say REVIEW for the full set of documents
    assert (view.state == "waiting_input") == (after["outcome"] == "REVIEW"), (view.state, after["outcome"])
    if view.state == "completed":
        assert view.claim["decision"]["outcome"] == after["outcome"] and view.claim["decision"]["total_amount"] == after["total_amount"]
        assert view.claim["extraction"]["surgery_grade"] is not None


def test_documents_are_only_accepted_for_a_claim_held_for_documents(env, claim_corpus):
    from fitwitness.agents.graph import execute_run
    from fitwitness.claims import resubmit

    r, j, s = env
    root, manifest, gold = claim_corpus
    case, held, _ = _held(env, claim_corpus, "missing_doc", "resubmit-guards")
    waiting = [d["id"] for d in case["supplementary_documents"]]
    for bad, conflict in ((["nope"], False), ([waiting[0], waiting[0]], False), ([case["documents"][0]["id"]], False)):
        with pytest.raises(resubmit.ResubmitError) as e:
            resubmit.plan(case, j.raw(s, held.id), bad)
        assert e.value.conflict is conflict, bad
    clean = approvable(manifest, gold)  # a claim that was simply paid was not held
    seed_case(r, s, root, clean)
    paid = j.enqueue(s, request_for(clean), "resubmit-not-held")
    execute_run(r, s, paid.id)
    assert j.get(s, paid.id).state == "completed"
    with pytest.raises(resubmit.ResubmitError) as e:
        resubmit.plan({**clean, "supplementary_documents": [{"id": "x", "kind": "receipt"}]}, j.raw(s, paid.id), ["x"])
    assert e.value.conflict


def test_a_reviewer_cannot_raise_the_amount_the_sign_off_tier_was_chosen_on(env, claim_corpus):
    from fitwitness.agents.graph import execute_run
    from fitwitness.claims.review import ReviewRuleError

    r, j, s = env
    root, manifest, gold = claim_corpus
    case = by_scenario(manifest, "high_amount")
    seed_case(r, s, root, case)
    run = j.enqueue(s, request_for(case), "claim-raise")
    execute_run(r, s, run.id)
    pending = next(e for e in j.events(s, run.id) if e["kind"] == "waiting_input")["payload"]
    note = "진단서·영수증 확인 후 승인합니다"
    for amount in (pending["total_amount"] + 1, 9_000_000_000):
        with pytest.raises(ReviewRuleError):  # refused at the door: the run keeps waiting
            j.resume(s, run.id, {"outcome": "APPROVE", "reviewer": "심사역 김", "note": note, "total_amount": amount})
    assert j.get(s, run.id).state == "waiting_input"
    j.resume(s, run.id, {"outcome": "APPROVE", "reviewer": "심사역 김", "note": note, "total_amount": pending["total_amount"] - 1000})
    if pending["approvals_required"] > 1:
        execute_run(r, s, run.id)
        j.resume(s, run.id, {"outcome": "APPROVE", "reviewer": "심사역 이", "note": note})
    execute_run(r, s, run.id)
    view = j.get(s, run.id)
    assert view.state == "completed" and view.claim["payout"]["amount"] == pending["total_amount"] - 1000


def test_the_ledger_refuses_the_same_event_under_another_claim_number_and_a_cancelled_run(env):
    from fitwitness.storage.repository import LeaseLost

    r, j, s = env
    keys = ["POL-DUP|hosp|2026-01-01"]
    assert r.pay(s, "CLM-D1", "POL-DUP", 100_000, keys, [])["status"] == "paid"
    again = r.pay(s, "CLM-D2", "POL-DUP", 100_000, keys, [])  # the rules looked before CLM-D1 was paid
    assert again["status"] == "duplicate" and again["duplicate_of"] == "CLM-D1" and again["amount"] == 0
    assert [p["claim_id"] for p in r.payouts(s)] == ["CLM-D1"]
    assert r.pay(s, "CLM-D1", "POL-DUP", 100_000, keys, [])["status"] == "already_paid"  # a retry of the paid claim is still idempotent
    # a run that was cancelled pays nothing, even if its worker did not notice yet
    run = j.enqueue(s, RunRequest(), "fence-run")
    token = j.claim(s, run.id)
    j.cancel(s, run.id)
    with pytest.raises(LeaseLost):
        r.pay(s, "CLM-D3", "POL-D3", 50_000, ["POL-D3|diag|C16"], [], run.id, lease_token=token)
    assert [p["claim_id"] for p in r.payouts(s)] == ["CLM-D1"]
    live = j.enqueue(s, RunRequest(), "fence-live")
    assert r.pay(s, "CLM-D4", "POL-D4", 50_000, ["POL-D4|diag|C16"], [], live.id, lease_token=j.claim(s, live.id))["status"] == "paid"
