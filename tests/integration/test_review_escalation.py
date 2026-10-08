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
