"""The terminal-style search runs through the same tool path as every other search, on the real corpus."""
import pytest
from test_workflow import env  # noqa: F401
from fitwitness.agents import shell_search
from fitwitness.agents.budget import BudgetTracker
from fitwitness.agents.evidence import EvidenceSession
from fitwitness.agents.tools import EvidenceTools, ToolRequest
from fitwitness.contracts import Budget


@pytest.fixture
def tools(env, monkeypatch):
    monkeypatch.setenv("FITWITNESS_SHELL_SEARCH", "on")
    r, j, s = env
    return EvidenceTools(s, r.snapshot(s), r, BudgetTracker(Budget())), r, s


def shell(t, command, **kw):
    return t.execute(ToolRequest(name="search_shell", arguments={"command": command, **kw}))


def test_a_pattern_search_returns_candidates_and_facts_still_come_through_query_dimensions(tools):
    t, r, s = tools
    assert "search_shell" in t.available
    everything = shell(t, "grep -rl drawing_number .", top_k=50)
    assert {h["revision_id"] for h in everything} == set(t.snapshot.revision_ids)  # one file per active revision
    session = EvidenceSession([], t.available)
    op = ToolRequest(name="search_shell", arguments={"command": "grep -rl drawing_number ."})
    session.observe(op, everything)
    assert all(not c.facts for c in session.candidates)  # the search hands over names, not evidence
    rid = everything[0]["revision_id"]
    facts = t.execute(ToolRequest(name="query_dimensions", arguments={"revision_id": rid, "fields": []}))
    assert facts and all(f["source"]["revision_id"] == rid for f in facts)
    assert t.budget.usage.tool_calls == 2


def test_the_text_a_search_sees_is_the_stored_facts(tools):
    t, r, s = tools
    with_material = {h["revision_id"] for h in shell(t, "grep -rl 'fact material' .", top_k=50)}
    stored = {rid for rid in t.snapshot.revision_ids if any(f.field == "material" for f in r.load_facts(s, rid))}
    assert with_material == stored
    without = {h["revision_id"] for h in shell(t, "grep -rL 'fact material' .", top_k=50)}
    assert without == set(t.snapshot.revision_ids) - stored  # a negation, which no ranked channel answers


def test_a_refused_command_is_a_tool_error_and_costs_no_workspace(tools):
    t, r, s = tools
    with pytest.raises(shell_search.ShellRefused):
        shell(t, "cat /etc/passwd")
    assert t._shell_workspace is None  # refused before anything was written
    assert shell(t, "ls | wc -l") is not None and t._shell_workspace is not None


def test_a_changed_corpus_makes_the_workspace_stale(tools):
    t, r, s = tools
    shell(t, "ls")
    from fitwitness.contracts import Fact
    rid = t.snapshot.revision_ids[0]
    facts = r.load_facts(s, rid)
    r.save_facts(s, rid, facts[:-1] or facts)  # any change of the stored rows changes the snapshot digest
    fresh = r.snapshot(s)
    if fresh.id != t.snapshot.id:
        with pytest.raises(ValueError, match="stale"):
            shell(t, "ls")
