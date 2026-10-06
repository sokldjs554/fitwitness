"""Transient provider failures back off and retry; anything else fails once and stays failed."""
import pytest
from test_workflow import env, request
from fitwitness.agents.providers import TransientProviderError
from fitwitness.agents.tools import SearchPlan


class FlakyPlanner:
    def __init__(self, failures):
        self.failures = failures
        self.calls = 0
        self.roles = []

    def plan(self, context, role="planner"):
        self.calls += 1
        self.roles.append(role)
        if self.calls <= self.failures:
            raise TransientProviderError("APITimeoutError: provider did not answer")
        return SearchPlan(operations=[], stop=True), {"role": role}


def paid():
    return request().model_copy(update={"provider": "anthropic", "model_id": "test"})


def test_transient_failure_schedules_a_retry_and_later_succeeds(env, monkeypatch):
    import fitwitness.agents.graph as mod

    r, j, s = env
    planner = FlakyPlanner(failures=1)
    monkeypatch.setattr(mod, "create_model", lambda *a: planner)
    run = j.enqueue(s, paid(), "retry-1")
    with pytest.raises(TransientProviderError):                       # the worker process exits non-zero…
        mod.execute_run(r, s, run.id)
    view = j.get(s, run.id)                                            # …but the job has already been rescheduled
    assert view.state == "retry_wait" and view.attempts == 1 and view.next_attempt_at
    assert (s.tenant_id, run.id) in j.pending()
    assert j.claim(s, run.id) is None                                   # not due yet
    kinds = [e["kind"] for e in j.events(s, run.id)]
    assert "retry_scheduled" in kinds and "failed" not in kinds
    j.make_due(s, run.id)
    mod.execute_run(r, s, run.id)
    assert j.get(s, run.id).state == "completed"
    # one failed planner call, then the retried planner call and its challenger
    assert planner.roles == ["planner", "planner", "challenger"]


def test_exhausted_retries_are_dead_lettered(env, monkeypatch):
    import fitwitness.agents.graph as mod

    r, j, s = env
    monkeypatch.setattr(mod, "create_model", lambda *a: FlakyPlanner(failures=99))
    run = j.enqueue(s, paid(), "retry-2")
    for _ in range(3):
        j.make_due(s, run.id)
        with pytest.raises(TransientProviderError):
            mod.execute_run(r, s, run.id)
    view = j.get(s, run.id)
    assert view.state == "failed" and view.attempts == 3
    assert [e["kind"] for e in j.events(s, run.id)][-1] == "dead_lettered"
    assert (s.tenant_id, run.id) not in j.pending()


def test_non_transient_error_fails_immediately(env, monkeypatch):
    import fitwitness.agents.graph as mod

    class Broken:
        def plan(self, context, role="planner"):
            raise ValueError("model output failed schema validation")

    r, j, s = env
    monkeypatch.setattr(mod, "create_model", lambda *a: Broken())
    run = j.enqueue(s, paid(), "retry-3")
    with pytest.raises(ValueError):
        mod.execute_run(r, s, run.id)
    view = j.get(s, run.id)
    assert view.state == "failed" and view.attempts == 1
    assert [e["kind"] for e in j.events(s, run.id)][-1] == "failed"


def test_backoff_schedule_is_bounded():
    from fitwitness.runtime.jobs import backoff_seconds

    assert [backoff_seconds(a) for a in range(4)] == [5, 10, 20, 40]
    assert backoff_seconds(20) == 300
