"""A claim that waited days for a person while the service was deployed again: the record names the code that queued it
and the code that answered it, the deadline that passed moved it to senior handling, and the money moved once."""

import pytest
from test_workflow import env  # noqa: F401
from test_review_escalation import CLAIMS, answer, parked_claim, waiting

from fitwitness.claims.review import ReviewRuleError

pytestmark = pytest.mark.skipif(not (CLAIMS / "manifest.json").exists(), reason="claim corpus not generated")

THREE_DAYS = 3 * 86400


def test_a_claim_parked_for_three_days_is_answered_under_the_next_deployment(env, monkeypatch):
    repo, jobs, _ = env
    monkeypatch.setenv("FITWITNESS_GIT_SHA", "deploy-a")
    scope, run_id, amount = parked_claim(repo, jobs, senior=False)
    assert jobs.raw(scope, run_id)["code_version"] == "deploy-a"
    assert [e for e in jobs.events(scope, run_id) if e["kind"] == "queued"][0]["payload"]["code_version"] == "deploy-a"
    assert waiting(jobs, scope, run_id)["approvals_required"] == 1

    # Three days pass: every clock the run keeps moves back, and the sweep that runs on each dispatcher pass sees the deadline gone.
    with repo.connection(scope) as c:
        c.execute("UPDATE fw_runs SET created_at=created_at-interval '3 days', paused_since=paused_since-interval '3 days', "
                  "review_due_at=review_due_at-interval '3 days' WHERE id=%s", (run_id,))
        c.execute("UPDATE fw_review_due SET due_at=due_at-interval '3 days' WHERE run_id=%s", (run_id,))
    assert jobs.escalate_overdue(limit=10_000) >= 1
    escalated = [e["payload"] for e in jobs.events(scope, run_id) if e["kind"] == "review_escalated"]
    assert len(escalated) == 1 and escalated[0]["waited_seconds"] >= THREE_DAYS - 60

    # The service is deployed again while the claim waits. The checkpoint written under deploy-a is read under deploy-b.
    monkeypatch.setenv("FITWITNESS_GIT_SHA", "deploy-b")
    with pytest.raises(ReviewRuleError):  # late means senior: a reason is needed now
        jobs.resume(scope, run_id, {"outcome": "APPROVE", "reviewer": "김심사", "note": ""})
    answer(repo, jobs, scope, run_id, "APPROVE", "김심사")
    assert jobs.get(scope, run_id).state == "waiting_input" and repo.payouts(scope) == []
    answer(repo, jobs, scope, run_id, "APPROVE", "박팀장")
    assert jobs.get(scope, run_id).state == "completed"
    assert [p["amount"] for p in repo.payouts(scope)] == [amount]

    events = jobs.events(scope, run_id)
    by_human = [e["payload"] for e in events if e["kind"] == "resumed_by_human"]
    assert [(p["started_with"], p["resumed_with"]) for p in by_human] == [("deploy-a", "deploy-b")] * 2
    resumed = [e["payload"]["code_version"] for e in events if e["kind"] == "resumed"]
    assert resumed and set(resumed) == {"deploy-b"}, "every resume of the graph names the code that did it"
    row = jobs.raw(scope, run_id)
    assert row["code_version"] == "deploy-a" and row["waited_seconds"] >= THREE_DAYS - 60
