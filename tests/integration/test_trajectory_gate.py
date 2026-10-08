"""The trajectory suite against the real graphs: clean on the real pipeline, loud on a broken one."""
import json
from pathlib import Path
import pytest
from test_workflow import env  # noqa: F401  (fixture: repository + jobs on the test database)
from fitwitness.evaluation import trajectory as T

ROOT = Path("var/corpus")
CLAIMS = Path("var/claims")
REFERENCE = Path("docs/evaluation/trajectories.json")

pytestmark = pytest.mark.skipif(not (CLAIMS / "manifest.json").exists(), reason="claim corpus not generated")


def test_real_claim_paths_hold_every_invariant_and_match_the_reference(env):
    repo, jobs, _ = env
    result = T.run_claim_trajectories(repo, jobs, CLAIMS, cases_per_scenario=1, branches_per_scenario=1)
    assert len(result["rows"]) == 11
    assert [r["violations"] for r in result["rows"] if r["violations"]] == []
    scored = T.score(result, None, json.loads(REFERENCE.read_text()))
    assert scored["compared"] >= 11 and scored["match_rate"] == 1.0, scored["mismatches"]


def test_the_reviewer_branches_were_exercised(env):
    repo, jobs, _ = env
    result = T.run_claim_trajectories(repo, jobs, CLAIMS, cases_per_scenario=2, branches_per_scenario=2)
    parked = [r for r in result["rows"] if r["first"]["state"] == "waiting_input"]
    assert parked and all({"approve", "deny"} <= set(r["answers"]) for r in parked)
    paid = [r for r in result["rows"] if r["first"]["ledger"]]
    assert paid and all(r["rerun"] is not None for r in paid)
    assert all(not r["violations"] for r in result["rows"])


def test_a_broken_claim_graph_is_seen_by_the_path_checks(env):
    repo, jobs, _ = env
    with T.degraded_claim_graph():
        result = T.run_claim_trajectories(repo, jobs, CLAIMS, branches_per_scenario=0, cases_per_scenario=4)
    scored = T.score({"rows": result["rows"]}, None, {"claims": {r["case_id"]: {"signature": ["never"]} for r in result["rows"]}})
    assert scored["invariant_violations"] >= 1 and scored["graph_wrong_pay"] >= 1
    # and the graph is back to normal afterwards
    import fitwitness.claims.graph as graph
    assert graph.adjudicate.__name__ == "adjudicate"


def test_drawing_paths_are_clean_and_match_the_reference(env):
    repo, jobs, _ = env
    drawing = T.run_drawing_trajectories(repo, jobs, ROOT)
    assert {n: d["violations"] for n, d in drawing.items() if d["violations"]} == {}
    ref = json.loads(REFERENCE.read_text())["drawing"]
    assert drawing["rules"]["signature"] == ref["rules"]
    assert drawing["multi_agent"]["signature"] == ref["multi_agent"]
    assert "tool_skipped" in drawing["multi_agent"]["signature"]
    assert drawing["shell_agent"]["signature"] == ref["shell_agent"]
    assert drawing["shell_agent"]["signature"].index("tool:search_shell") < drawing["shell_agent"]["signature"].index("tool:query_dimensions")
    assert drawing["review"]["before"]["signature"] == ref["review_before"]
    assert drawing["review"]["after"]["signature"] == ref["review_after"]
