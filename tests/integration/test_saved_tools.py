"""The agent can define bounded, tenant-scoped search tools and reuse them."""
import pytest
from uuid import uuid4
from pydantic import ValidationError
from test_workflow import env, request
from fitwitness.contracts import Budget, TenantScope
from fitwitness.agents.budget import BudgetTracker
from fitwitness.agents.evidence import EvidenceSession
from fitwitness.agents.tools import EvidenceTools, SearchPlan, ToolRequest

DEFINITION = {
    "name": "bracket_lookup",
    "description": "브래킷 도면을 찾고 구멍 간격과 소재를 함께 읽는다",
    "channels": ["exact", "bm25"],
    "query_template": "{query} 브래킷",
    "fields": ["hole_spacing", "material"],
    "top_k": 5,
}
BODY = {k: v for k, v in DEFINITION.items() if k != "name"}


def tools_for(r, s):
    return EvidenceTools(s, r.snapshot(s), r, BudgetTracker(Budget()))


def test_define_then_run_saved_search_returns_inspected_candidates(env):
    r, j, s = env
    t = tools_for(r, s)
    assert {"define_search_tool", "run_saved_search"} <= t.available
    out = t.execute(ToolRequest(name="define_search_tool", arguments=DEFINITION))
    assert out["defined"] == "bracket_lookup"
    assert [x["name"] for x in r.saved_tools(s)] == ["bracket_lookup"]
    hits = t.execute(ToolRequest(name="run_saved_search", arguments={"name": "bracket_lookup", "query": "구멍 간격 40mm SUS304"}))
    assert hits and any(h["facts"] for h in hits)
    assert all({f["field"] for f in h["facts"]} <= {"hole_spacing", "material"} for h in hits)
    session = EvidenceSession([], t.available)
    op = ToolRequest(name="run_saved_search", arguments={"name": "bracket_lookup", "query": "x"})
    session.observe(op, hits)
    assert {c.revision_id for c in session.candidates} == {h["revision_id"] for h in hits}
    assert any(c.facts for c in session.candidates)  # facts arrived with their source check
    rid = hits[0]["revision_id"]
    examined = EvidenceSession.examined(rid, [{"field": "hole_spacing"}], [{"tool": op.model_dump(mode="json"), "result": hits}])
    assert examined == {f["field"] for f in hits[0]["facts"]}
    assert t.budget.usage.tool_calls == 2


def test_saved_search_rejects_facts_that_cite_another_revision(env):
    r, j, s = env
    t = tools_for(r, s)
    t.execute(ToolRequest(name="define_search_tool", arguments=DEFINITION))
    hits = t.execute(ToolRequest(name="run_saved_search", arguments={"name": "bracket_lookup", "query": "SUS304"}))
    with_facts = next(h for h in hits if h["facts"])
    forged = {**with_facts, "revision_id": "someone-else"}
    with pytest.raises(ValueError):
        EvidenceSession([], t.available).observe(ToolRequest(name="run_saved_search", arguments={"name": "bracket_lookup", "query": "x"}), [forged])


def test_saved_tools_are_tenant_scoped_and_bounded(env):
    r, j, s = env
    tools_for(r, s).execute(ToolRequest(name="define_search_tool", arguments=DEFINITION))
    other = TenantScope(tenant_id=str(uuid4()), user_id=str(uuid4()))
    assert r.saved_tools(other) == [] and r.saved_tool(other, "bracket_lookup") is None
    with pytest.raises(ValueError):
        tools_for(r, other).execute(ToolRequest(name="run_saved_search", arguments={"name": "bracket_lookup", "query": "x"}))
    for i in range(r.MAX_SAVED_TOOLS - 1):
        r.save_tool(s, f"tool_{i}", BODY)
    with pytest.raises(ValueError):
        r.save_tool(s, "one_too_many", BODY)
    r.save_tool(s, "bracket_lookup", {**BODY, "top_k": 3})  # redefining an existing name replaces it
    assert r.saved_tool(s, "bracket_lookup")["top_k"] == 3


@pytest.mark.parametrize(
    "bad",
    [
        {"name": "Bracket Lookup"},  # a tool name is an identifier
        {"query_template": "브래킷 도면"},  # the caller's text needs its slot
        {"channels": ["image"]},  # image search takes an image id, not a template
        {"channels": ["semantic"]},  # not connected in this workspace
        {"channels": ["exact", "exact"]},
        {"name": "search_exact"},  # cannot shadow a built-in tool
        {"fields": ["tenant_id"]},
        {"top_k": 500},
    ],
)
def test_definitions_are_validated(env, bad):
    r, j, s = env
    with pytest.raises((ValueError, ValidationError)):
        tools_for(r, s).execute(ToolRequest(name="define_search_tool", arguments={**DEFINITION, **bad}))
    assert r.saved_tools(s) == []


def test_graph_run_can_define_and_reuse_a_tool(env, monkeypatch):
    import fitwitness.agents.graph as mod

    r, j, s = env
    seen = []

    class Planner:
        def plan(self, context, role="planner"):
            seen.append((role, [t["name"] for t in context["saved_tools"]]))
            if role == "planner" and not context["saved_tools"]:
                return SearchPlan(operations=[
                    ToolRequest(name="define_search_tool", arguments=DEFINITION),
                    ToolRequest(name="run_saved_search", arguments={"name": "bracket_lookup", "query": "구멍 간격 40mm SUS304"}),
                ]), {"role": role}
            return SearchPlan(stop=True), {"role": role}

    monkeypatch.setattr(mod, "create_model", lambda *a: Planner())
    run = j.enqueue(s, request().model_copy(update={"provider": "anthropic", "model_id": "test"}), "saved-1")
    mod.execute_run(r, s, run.id)
    out = j.get(s, run.id)
    assert out.state == "completed"
    assert {d.verdict for d in out.decisions} & {"match", "mismatch"}  # evidence came through the saved tool
    names = [e["payload"]["name"] for e in j.events(s, run.id) if e["kind"] == "tool"]
    assert names[:2] == ["define_search_tool", "run_saved_search"]
    assert seen[0] == ("planner", []) and all(tools == ["bracket_lookup"] for _, tools in seen[1:])
    assert r.saved_tools(s)[0]["created_by_run"] == run.id
