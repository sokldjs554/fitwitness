"""One model-mode run of the trajectory evaluation through the real graph, with a scripted planner in the model's place."""

from decimal import Decimal
from pathlib import Path

import pytest
from test_workflow import env  # noqa: F401  (fixture: repository + jobs on the test database)

import fitwitness.agents.graph as graph
from fitwitness.contracts import Budget, RunRequest, SearchRequest
from fitwitness.evaluation import agent_trajectory as A
from fitwitness.evaluation.trajectory import ScriptedPair

ROOT = Path("var/corpus")
pytestmark = pytest.mark.skipif(not (ROOT / "manifest.json").exists(), reason="corpus not generated")


def test_a_scripted_planner_run_is_scored_on_its_path_not_only_its_verdicts(env, monkeypatch):  # noqa: F811
    repo, jobs, _ = env
    case = A.family_cases(ROOT)[1]  # the bracket family: a hole-spacing query
    pair = ScriptedPair(sorted(case["revision_ids"]))
    monkeypatch.setattr(graph, "create_model", lambda *a: pair)
    request = RunRequest(
        search=SearchRequest(text=case["query"]),
        provider="anthropic",
        model_id="scripted",
        mode="fitwitness",
        budget=Budget(max_cost_usd=Decimal("0.04"), max_tokens=64000, max_model_calls=4),
    )
    row = A.run_one(repo, jobs, ROOT, case, request)
    assert row["state"] == "completed" and row["error"] is None
    assert row["roles"] == ["planner", "challenger"] and row["challenger_turn"] and row["skipped_repeats"] == 1
    # the script looks at two of the five drawings: three verdicts stay unknown, and the path checks say why
    # (one of the three is labelled unknown anyway, so two wrong verdicts, both for want of a lookup)
    assert row["coverage"] == pytest.approx(0.4, abs=1e-3) and len(row["uninspected"]) == 3
    assert [f["class"] for f in row["failures"].values()] == ["not_inspected", "not_inspected"]
    assert all(row["actual"][r] == "unknown" for r in row["uninspected"])
    assert row["violations"] == [] and row["stop"] == "plan_stop"
    # the free reference path looks at every drawing and agrees with the labels
    rules = A.run_one(
        repo, jobs, ROOT, case, RunRequest(search=SearchRequest(text=case["query"]), provider="rules", budget=Budget(max_tokens=64000))
    )
    assert rules["correct"] == rules["total"] == 5 and rules["coverage_before_verdict"] == 1.0 and rules["violations"] == []
    summary = A.summarise([rules, row], A.Ledger(Decimal("0.8")))
    assert summary["by_mode"]["rules"]["accuracy"] == 1.0 and summary["by_mode"]["fitwitness"]["failure_classes"]["not_inspected"] == 2
