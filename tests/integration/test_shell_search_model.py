"""The shell-search evaluation through the real graph on the thirty test drawings, with a script in the model's place."""

from decimal import Decimal
from pathlib import Path

import pytest
from test_workflow import env  # noqa: F401  (fixture: repository + jobs on the test database)

import fitwitness.agents.graph as graph
from fitwitness.contracts import Budget, RunRequest, SearchRequest
from fitwitness.evaluation import shell_search_model as S

ROOT = Path("var/corpus")
pytestmark = pytest.mark.skipif(not (ROOT / "manifest.json").exists(), reason="corpus not generated")


class Typist:
    """Types one command, then looks up what it found; stops when nothing is left to look up."""

    def __init__(self, command: str):
        self.command = command

    def plan(self, context, role="planner"):
        from fitwitness.agents.tools import SearchPlan, ToolRequest

        if not context["observations"]:
            return SearchPlan(operations=[ToolRequest(name="search_shell", arguments={"command": self.command, "top_k": 50})]), {
                "role": role
            }
        todo = [c["revision_id"] for c in context["candidates"] if not c["examined_fields"]][:8]
        ops = [ToolRequest(name="query_dimensions", arguments={"revision_id": r, "fields": ["material"]}) for r in todo]
        return SearchPlan(operations=ops, stop=not ops), {"role": role}


def request(case):
    return RunRequest(
        search=SearchRequest(text=case["query"]),
        provider="anthropic",
        model_id="scripted",
        mode="react",
        budget=Budget(max_cost_usd=Decimal("0.03"), **S.BUDGET),
    )


def test_a_command_finds_the_whole_set_the_ranked_channels_cut_short_and_the_score_says_so(env, monkeypatch):  # noqa: F811
    repo, jobs, _ = env
    case = next(c for c in S.cases(ROOT) if c["id"] == "material_common")
    assert len(case["answer"]) == 18 and case["query"] == "SUS304 도면을 모두 찾아 주세요"
    rules = S.run_one(
        repo,
        jobs,
        ROOT,
        "test",
        case,
        RunRequest(search=SearchRequest(text=case["query"]), provider="rules", budget=Budget(max_tokens=64000)),
        "rules",
    )
    assert rules["found"] == 6 and rules["confirmed"] == 6  # the ranked channels return six of the eighteen
    monkeypatch.setattr(graph, "create_model", lambda *a: Typist("grep -rl 'fact material = SUS304' ."))
    row = S.run_one(repo, jobs, ROOT, "test", case, request(case), "shell")
    assert row["state"] == "completed" and row["error"] is None
    assert row["found"] == 18 and row["extra"] == 0 and row["commands"] == ["grep -rl 'fact material = SUS304' ."]
    # three plans at most: the search, then two of eight lookups, so two of the eighteen stay unconfirmed
    assert row["confirmed"] == 16 and row["false_matches"] == 0 and row["refused"] == []
    assert sorted(row["actual"].values()).count("unknown") == 2
    assert S.summarise([rules, row])["shell"]["complete_sets"] == 1


def test_a_refused_command_is_an_observation_and_the_switch_is_off_in_the_ranked_arm(env, monkeypatch):  # noqa: F811
    repo, jobs, _ = env
    case = next(c for c in S.cases(ROOT) if c["id"] == "material_missing")
    monkeypatch.setattr(graph, "create_model", lambda *a: Typist("cat /etc/passwd"))
    row = S.run_one(repo, jobs, ROOT, "test", case, request(case), "shell")
    assert row["state"] == "completed" and row["found"] == 0 and row["confirmed"] is None
    assert len(row["refused"]) == 1 and "/etc/passwd" in row["refused"][0]["command"]
    off = S.run_one(repo, jobs, ROOT, "test", case, request(case), "ranked")
    assert off["refused"] and "unavailable" in off["refused"][0]["error"]  # the ranked arm cannot reach the tool
