"""The terminal-style search: what it accepts, what it refuses, and that nothing it is given is ever run by a shell."""
from pathlib import Path

import pytest

from fitwitness.agents import shell_search as S
from fitwitness.agents.evidence import EvidenceSession
from fitwitness.agents.tools import SCHEMAS, EvidenceTools, SearchPlan, ToolRequest
from fitwitness.contracts import Candidate, DrawingRevision, Fact, SourceRef

HASH = "a" * 64


def revision(rid, number, label="A", title="브래킷", family="F1"):
    return DrawingRevision(id=rid, tenant_id="t", document_id=f"d-{rid}", drawing_number=number, family_id=family,
                           revision_label=label, source_hash=HASH, title=title)


def fact(rid, field, value, unit="mm", certainty="verified"):
    return Fact(field=field, value=value, unit=unit, certainty=certainty,
                source=SourceRef(revision_id=rid, source_hash=HASH, page=1, bbox=(0.1, 0.1, 0.2, 0.2)))


@pytest.fixture
def workspace():
    revs = [revision("r1", "BS-1201", "A"), revision("r2", "BS-1202", "B"), revision("r3", "BS-1301", "A", "플레이트", "F2"),
            revision("r4", "BS 9/9", "C")]
    facts = {"r1": [fact("r1", "material", "SUS304", None), fact("r1", "width", "120")],
             "r2": [fact("r2", "material", "SUS316", None), fact("r2", "width", "95")],
             "r3": [fact("r3", "width", "130")],
             "r4": [fact("r4", "material", "SUS304", None, "uncertain")]}
    ws = S.Workspace(revs, facts)
    yield ws
    ws.close()


def ids(hits):
    return [h["revision_id"] for h in hits]


def test_a_search_returns_the_revisions_whose_files_the_output_names(workspace):
    assert sorted(ids(workspace.search("grep -rlw SUS304 ."))) == ["r1", "r4"]
    assert sorted(ids(workspace.search('grep -rliE "sus304|sus316" .'))) == ["r1", "r2", "r4"]
    assert ids(workspace.search("grep -rL 'fact material' .")) == ["r3"]  # a negation no ranking can express
    assert sorted(ids(workspace.search('find . -name "BS-12*"'))) == ["r1", "r2"]
    assert ids(workspace.search('find . -name "*.revB.*" -type f')) == ["r2"]


def test_pipelines_and_counts_work_and_the_score_counts_lines(workspace):
    hits = workspace.search("grep -rn fact . | sort | head -n 20")
    assert {h["revision_id"] for h in hits} == {"r1", "r2", "r3", "r4"}
    assert next(h for h in hits if h["revision_id"] == "r1")["scores"] == {"shell": 2.0}
    assert ids(workspace.search("grep -rc material . | head -5")) == ["r1", "r2", "r4"] or sorted(ids(workspace.search("grep -rc material ."))) == ["r1", "r2", "r4"]
    assert workspace.search("grep -rl no-such-text .") == []
    assert len(workspace.search("grep -rl width .", top_k=2)) == 2


def test_hits_are_candidates_the_rest_of_the_system_already_understands(workspace):
    hits = workspace.search("grep -rl width .")
    assert all(Candidate.model_validate(h).facts == [] for h in hits)
    session = EvidenceSession([], {"search_shell", "query_dimensions"})
    session.observe(ToolRequest(name="search_shell", arguments={"command": "grep -rl width ."}), hits)
    assert {c.revision_id for c in session.candidates} == {"r1", "r2", "r3"} and all(not c.facts for c in session.candidates)


HOSTILE = [
    "rm -rf /", "ls; rm -rf /", "ls && ls", "ls || ls", "ls &", "grep a . > out.txt", "grep a < /etc/passwd", "cat /etc/passwd", "cat ../../etc/passwd",
    "cat ~/.ssh/id_rsa", "head -n 3 $(ls)", "find . -exec cat {} +", "find . -delete", "find . -fprint /tmp/x", "find / -name x", "find . -newer x",
    "grep -f /etc/passwd .", "grep -P x .", "grep --file=/etc/passwd .", "grep -r a /", "grep -r a ..", "ls -l /", "sort -o out .", "sort --output=out x",
    "cut -f1 -d", "echo hi", "env", "python -c 1", "sh -c ls", "curl http://x", "nc -l 1", "xargs rm", "tee x", "grep", "grep -A x a .", "head -n abc x",
    "ls | ls | ls | ls", "", "   ", "grep 'unterminated .", "a" * 500, "grep -m 99999999 a .", "find . -maxdepth 99", "cat \x00x", "grep a .\nrm x",
]


@pytest.mark.parametrize("command", HOSTILE)
def test_everything_outside_the_closed_set_is_refused(command, workspace):
    with pytest.raises(S.ShellRefused):
        workspace.search(command)


def test_there_is_no_shell_so_expansions_are_only_text(tmp_path, workspace):
    marker = tmp_path / "touched"
    for pattern in (f"$(touch {marker})", f"`touch {marker}`", f"x;touch {marker}", "$HOME", "*"):
        workspace.search(f"grep -r '{pattern}' .")  # a literal pattern, matched against text and found nowhere
    assert not marker.exists()
    assert workspace.search("grep -rl '$HOME' .") == []  # and no variable was expanded


def test_a_command_that_runs_too_long_is_stopped(monkeypatch, workspace):
    def slow(*args, **kwargs):
        raise S.subprocess.TimeoutExpired("grep", 5)

    monkeypatch.setattr(S.subprocess, "run", slow)
    with pytest.raises(S.ShellRefused, match="longer than"):
        workspace.search("grep -r a .")


def test_file_names_cannot_escape_or_collide_and_the_directory_goes_away(workspace):
    names = list(workspace.names)
    assert all("/" not in n and n.endswith(".txt") for n in names) and len(set(names)) == len(names)
    assert "BS_9_9.revC.r4.txt" in names
    root = workspace.root
    assert root.exists()
    workspace.close()
    assert not root.exists()
    workspace.close()  # closing twice is harmless


def test_the_tool_is_opt_in_typed_and_bounded(monkeypatch):
    monkeypatch.delenv("FITWITNESS_SHELL_SEARCH", raising=False)
    assert not S.enabled() and "search_shell" in SCHEMAS
    monkeypatch.setenv("FITWITNESS_SHELL_SEARCH", "on")
    assert S.enabled()
    assert "search_shell" in EvidenceTools(None, None, None, None).available
    monkeypatch.setenv("FITWITNESS_SHELL_SEARCH", "off")
    assert "search_shell" not in EvidenceTools(None, None, None, None).available
    with pytest.raises(ValueError):
        ToolRequest(name="search_shell", arguments={"command": "grep a .", "tenant_id": "victim"})
    with pytest.raises(ValueError):
        ToolRequest(name="search_shell", arguments={"command": "x" * 401})
    with pytest.raises(ValueError):
        ToolRequest(name="shell", arguments={"command": "ls"})
    assert SearchPlan(operations=[{"name": "search_shell", "arguments": {"command": "grep -rl SUS304 ."}}]).operations
    assert "terminal" in SCHEMAS["search_shell"].model_json_schema()["properties"]["command"]["description"].lower()
