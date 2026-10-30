"""Two-person approval and missed deadlines, through the real graph and job runner."""

import json
from pathlib import Path
from uuid import uuid4
import pytest
from test_workflow import env  # noqa: F401
from fitwitness.agents.graph import execute_run
from fitwitness.claims.review import ReviewRuleError
from fitwitness.evaluation import trajectory as T

CLAIMS = Path("var/claims")
pytestmark = pytest.mark.skipif(not (CLAIMS / "manifest.json").exists(), reason="claim corpus not generated")


def parked_claim(repo, jobs, *, senior: bool):
    """A claim that stops for a person with something to pay: a big one (senior) or a small one (standard).

    Chosen by what the pipeline proposed, not by the generator's gold, since the proposal is what gets paid."""
    manifest = json.loads((CLAIMS / "manifest.json").read_text())
    for case in manifest["cases"]:
        scope = T._seed_claim(repo, CLAIMS, case)
        run_id = T._claim_run(repo, jobs, scope, case)
        if jobs.get(scope, run_id).state != "waiting_input":
            continue
        asked = waiting(jobs, scope, run_id)
        if asked["total_amount"] > 0 and (asked["tier"] == "senior") == senior:
            return scope, run_id, asked["total_amount"]
    pytest.skip("no suitable claim in this corpus")


def waiting(jobs, scope, run_id):
    return [e for e in jobs.events(scope, run_id) if e["kind"] == "waiting_input"][-1]["payload"]


def answer(repo, jobs, scope, run_id, outcome, reviewer, note="서류와 금액을 직접 확인했습니다"):
    jobs.resume(scope, run_id, {"outcome": outcome, "reviewer": reviewer, "note": note})
    execute_run(repo, scope, run_id)


def test_a_standard_claim_needs_one_approval(env):
    repo, jobs, _ = env
    scope, run_id, amount = parked_claim(repo, jobs, senior=False)
    assert waiting(jobs, scope, run_id)["approvals_required"] == 1
    answer(repo, jobs, scope, run_id, "APPROVE", "김심사", note="")
    assert jobs.get(scope, run_id).state == "completed"
    assert [p["amount"] for p in repo.payouts(scope)] == [amount]


def test_a_senior_claim_is_paid_only_after_two_different_people_approve(env):
    repo, jobs, _ = env
    scope, run_id, amount = parked_claim(repo, jobs, senior=True)
    q = waiting(jobs, scope, run_id)
    assert (q["tier"], q["approvals_required"], q["step"], q["note_required"]) == ("senior", 2, 1, True)
    with pytest.raises(ReviewRuleError):  # no reason
        jobs.resume(scope, run_id, {"outcome": "APPROVE", "reviewer": "김심사", "note": ""})
    assert jobs.get(scope, run_id).state == "waiting_input"  # the refused answer was not stored
    answer(repo, jobs, scope, run_id, "APPROVE", "김심사")
    assert jobs.get(scope, run_id).state == "waiting_input" and repo.payouts(scope) == []  # nothing paid on one signature
    q2 = waiting(jobs, scope, run_id)
    assert q2["step"] == 2 and q2["first_reviewer"] == "김심사"
    with pytest.raises(ReviewRuleError):
        jobs.resume(scope, run_id, {"outcome": "APPROVE", "reviewer": " 김심사", "note": "혼자서 두 번 승인"})
    answer(repo, jobs, scope, run_id, "APPROVE", "박팀장")
    assert jobs.get(scope, run_id).state == "completed"
    assert [p["amount"] for p in repo.payouts(scope)] == [amount]
    review = [e["payload"] for e in jobs.events(scope, run_id) if e["kind"] == "human_review"][-1]
    assert review["approvers"] == ["김심사", "박팀장"] and review["tier"] == "senior"


def test_the_second_approval_is_signed_after_the_first_and_the_chain_verifies(env):
    from fitwitness.claims import receipt
    from fitwitness.claims.models import ClaimReview

    repo, jobs, _ = env
    key = receipt.signing_key(b"k" * 48)

    def signer(run, answer, step, prev):
        return receipt.sign(
            key, run["tenant_id"], run["id"], ((run["request"] or {}).get("claim") or {}).get("claim_id", ""), answer, step=step, prev=prev
        )

    scope, run_id, amount = parked_claim(repo, jobs, senior=True)
    note = "서류와 금액을 직접 확인했습니다"
    jobs.resume(
        scope,
        run_id,
        {
            "outcome": "APPROVE",
            "reviewer": "김심사",
            "note": note,
            "receipt": {"step": 1, "decided_at": "x", "signature": "0" * 64, "key_id": "forged"},
        },
        signer=signer,
    )
    execute_run(repo, scope, run_id)
    assert waiting(jobs, scope, run_id)["first_signature"] not in (
        "",
        "0" * 64,
    )  # the question for the second approver carries the first signature
    jobs.resume(scope, run_id, {"outcome": "APPROVE", "reviewer": "박팀장", "note": note}, signer=signer)
    execute_run(repo, scope, run_id)
    result = jobs.raw(scope, run_id)["result"]
    approvals = [ClaimReview.model_validate(a) for a in result["approvals"]]
    assert [a.reviewer for a in approvals] == ["김심사", "박팀장"] and [a.receipt.step for a in approvals] == [1, 2]
    assert approvals[1].receipt.prev == approvals[0].receipt.signature and approvals[0].receipt.key_id != "forged"
    claim_id = result["claim_id"]
    rows = receipt.verify(key, scope.tenant_id, run_id, claim_id, approvals)
    assert [r["status"] for r in rows] == ["valid", "valid"]
    swapped = [approvals[0].model_copy(update={"total_amount": 1}), approvals[1]]
    assert [r["status"] for r in receipt.verify(key, scope.tenant_id, run_id, claim_id, swapped)] == ["altered", "valid"]
    assert [r["status"] for r in receipt.verify(key, scope.tenant_id, run_id, claim_id, approvals[::-1])] == [
        "out_of_order",
        "out_of_order",
    ]
    assert [p["amount"] for p in repo.payouts(scope)] == [amount]  # signing changed nothing about the money


def test_one_refusal_is_final_at_either_step(env):
    repo, jobs, _ = env
    scope, run_id, _ = parked_claim(repo, jobs, senior=True)
    answer(repo, jobs, scope, run_id, "DENY", "김심사", note="")  # a refusal needs no second opinion
    assert jobs.get(scope, run_id).state == "completed" and repo.payouts(scope) == []
    scope, run_id, _ = parked_claim(repo, jobs, senior=True)
    answer(repo, jobs, scope, run_id, "APPROVE", "김심사")
    answer(repo, jobs, scope, run_id, "DENY", "박팀장", note="")  # the second person says no
    assert jobs.get(scope, run_id).state == "completed" and repo.payouts(scope) == []


def test_an_unanswered_review_is_escalated_not_decided(env, monkeypatch):
    repo, jobs, _ = env
    scope, run_id, amount = parked_claim(repo, jobs, senior=False)
    assert jobs.get(scope, run_id).review_due_at is not None and jobs.escalate_overdue() >= 0
    assert not jobs.get(scope, run_id).escalated
    with repo.connection(scope) as c:  # the deadline passes
        c.execute("UPDATE fw_runs SET review_due_at=now()-interval '1 second' WHERE id=%s", (run_id,))
        c.execute("UPDATE fw_review_due SET due_at=now()-interval '1 second' WHERE run_id=%s", (run_id,))
    assert jobs.escalate_overdue(limit=10_000) >= 1
    view = jobs.get(scope, run_id)
    assert view.state == "waiting_input" and view.escalated, "late is not the same as approved or denied"
    assert repo.payouts(scope) == []
    event = [e for e in jobs.events(scope, run_id) if e["kind"] == "review_escalated"]
    assert len(event) == 1 and event[0]["payload"]["level"] == "senior"
    assert jobs.escalate_overdue(limit=10_000) == 0, "an escalated run is escalated once"
    # an escalated claim follows the senior rules, whatever its amount
    with pytest.raises(ReviewRuleError):
        jobs.resume(scope, run_id, {"outcome": "APPROVE", "reviewer": "김심사", "note": ""})
    answer(repo, jobs, scope, run_id, "APPROVE", "김심사")
    assert jobs.get(scope, run_id).state == "waiting_input" and repo.payouts(scope) == []
    answer(repo, jobs, scope, run_id, "APPROVE", "박팀장")
    assert [p["amount"] for p in repo.payouts(scope)] == [amount]


def test_answering_clears_the_deadline_and_the_ttl_is_configurable(env, monkeypatch):
    repo, jobs, _ = env
    monkeypatch.setenv("FITWITNESS_REVIEW_TTL_SECONDS", "7200")
    assert jobs.review_ttl_seconds() == 7200
    monkeypatch.setenv("FITWITNESS_REVIEW_TTL_SECONDS", "nonsense")
    assert jobs.review_ttl_seconds() == 86400
    scope, run_id, _ = parked_claim(repo, jobs, senior=False)
    answer(repo, jobs, scope, run_id, "DENY", "김심사", note="")
    with repo.plain() as c:
        assert c.execute("SELECT count(*) AS n FROM fw_review_due WHERE run_id=%s", (run_id,)).fetchone()["n"] == 0
    assert jobs.get(scope, run_id).review_due_at is None


def test_the_sign_off_rules_still_hold_when_the_question_is_past_the_first_page_of_events(env):
    """resume() looked for the question in the first 500 events; in a run whose question came later it found none and
    skipped the rules (a reason for a senior claim, a second person, the amount ceiling)."""
    repo, jobs, _ = env
    scope, run_id, _ = parked_claim(repo, jobs, senior=True)
    with repo.connection(scope) as c:  # as if 600 events had come before the question
        for i in range(600):
            jobs._event(c, scope, run_id, "tool_call", {"filler": i})
        c.execute("UPDATE fw_events SET seq=seq+1000 WHERE run_id=%s AND kind='waiting_input'", (run_id,))
    assert len(jobs.events(scope, run_id)) == 500 and not [e for e in jobs.events(scope, run_id) if e["kind"] == "waiting_input"]
    with pytest.raises(ReviewRuleError):  # a senior claim's approval needs a reason
        jobs.resume(scope, run_id, {"outcome": "APPROVE", "reviewer": "김심사", "note": ""})
    assert jobs.get(scope, run_id).state == "waiting_input"
