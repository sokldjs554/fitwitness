"""Trajectory evaluation: does a run take the path it is supposed to take?

The accuracy metrics of the gate look at where a run ends up. A run can end in the right
place by the wrong road: a payout written before the challenge step, a claim that skips the
reviewer and still happens to be correct, a drawing run that looks the same field up twice.
This suite records the road, as the sequence of events a run leaves in its own log, and
checks it two ways.

* **Reference paths.** Every claim in the synthetic corpus (and a few pinned drawing runs) has
  a committed reference signature in ``docs/evaluation/trajectories.json``. A run must
  reproduce it token for token. A deliberate behaviour change shows up as a diff of that
  file in review, the way a golden file does.
* **Invariants.** Independent of the reference, properties that must hold on any path: the
  order of the claim nodes, no payout before the challenge step, nothing paid on a path that
  ended in a denial, nothing paid while a reviewer is still being asked, a reviewer's answer
  applied exactly once, a rerun never paying twice, a drawing tool query never repeated.
  Violations are counted; the gate requires zero.

It runs the real LangGraph graphs against PostgreSQL through the job runtime, with the rules
provider (no model, no key), plus one scripted planner/challenger pair so the multi-agent path
(``tool_skipped``, the challenger's turn) is covered too. The degraded control breaks the claim
pipeline the same way the accuracy gate's control does and must be caught by this suite as well.
"""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from fitwitness.contracts import DrawingRevision, RunRequest, SearchRequest, TenantScope

VERSION = "trajectory-v1"
QUIET = {"queued", "started"}  # infrastructure events: present on every run, say nothing about the path
FORBIDDEN = {"interrupted", "retry_scheduled", "failed", "dead_lettered", "budget_stop", "stale", "cancelled"}
CLAIM_ORDER = ["intake", "tool:extract_documents", "extracted", "validated", "adjudicated", "challenged"]


# --------------------------------------------------------------------------------------------
# signatures
# --------------------------------------------------------------------------------------------
def token(event: dict) -> str:
    """One event as a short, stable string. Detail that identifies the path (an outcome, a tool
    name) is kept; detail that changes between runs (ids, amounts, timestamps) is not."""
    kind, payload = event["kind"], event.get("payload") or {}
    if kind in ("tool", "model"):
        return f"{kind}:{payload.get('name') or payload.get('role') or ''}".rstrip(":")
    if kind in ("adjudicated", "challenged", "human_review", "completed"):
        outcome = payload.get("outcome")
        return f"{kind}:{outcome}" if outcome else kind
    if kind == "payout":
        return f"payout:{payload.get('status')}"
    if kind == "agent_plan":
        return f"agent_plan:{payload.get('role')}"
    return kind


def signature(events: list[dict]) -> list[str]:
    return [token(e) for e in events if e["kind"] not in QUIET]


def divergence(expected: list[str], actual: list[str]) -> dict:
    """Where two signatures first differ, for a failure message a person can act on."""
    for i, (a, b) in enumerate(zip(expected, actual)):
        if a != b:
            return {"at": i, "expected": a, "actual": b}
    if len(expected) != len(actual):
        i = min(len(expected), len(actual))
        return {"at": i, "expected": expected[i] if i < len(expected) else "(end)", "actual": actual[i] if i < len(actual) else "(end)"}
    return {}


# --------------------------------------------------------------------------------------------
# claim runs
# --------------------------------------------------------------------------------------------
def _seed_claim(repo, root: Path, case: dict) -> TenantScope:
    scope = TenantScope(tenant_id=f"gate-traj-{uuid4()}", user_id="gate", role="operator")
    docs = []
    for d in case["documents"]:
        png = root / case["case_id"] / f"{d['id']}.png"
        docs.append((d, (root / case["case_id"] / f"{d['id']}.pdf").read_bytes(), png.read_bytes() if png.exists() else None))
    repo.seed_claim_case(scope, case, docs, case["prior_paid_keys"])
    return scope


def _claim_run(repo, jobs, scope, case: dict):
    from fitwitness.agents.graph import execute_run
    from fitwitness.claims.models import ClaimRequest, Policy

    claim = ClaimRequest(case_id=case["case_id"], claim_id=case["claim_id"], policy=Policy.model_validate(case["policy"]),
                         requested=case["requested"], document_ids=[d["id"] for d in case["documents"]], submitted_at=case["submitted_at"])
    job = jobs.enqueue(scope, RunRequest(kind="claim", claim=claim.model_dump(mode="json")), str(uuid4()))
    execute_run(repo, scope, job.id)
    return job.id


def _answer(repo, jobs, scope, run_id: str, outcome: str):
    """Answer every question the run asks, with a different person each time and a written reason.

    A standard claim asks once. A senior claim asks a second person after an approval; a denial ends it."""
    from fitwitness.agents.graph import execute_run

    for person in ("gate-reviewer-a", "gate-reviewer-b", "gate-reviewer-c"):
        if jobs.get(scope, run_id).state != "waiting_input":
            break
        jobs.resume(scope, run_id, {"outcome": outcome, "reviewer": person, "note": "trajectory check: documents verified"})
        execute_run(repo, scope, run_id)


def _snapshot(repo, jobs, scope, run_id: str) -> dict:
    view = jobs.get(scope, run_id)
    events = jobs.events(scope, run_id)
    waiting = next((e["payload"] for e in reversed(events) if e["kind"] == "waiting_input"), None)
    return {"state": view.state, "signature": signature(events), "ledger": [(p["claim_id"], p["amount"]) for p in repo.payouts(scope)],
            "proposed": (waiting or {}).get("total_amount"), "approvals_required": (waiting or {}).get("approvals_required") or 1}


@contextmanager
def degraded_claim_graph():
    """Break the claim graph's adjudication the way the accuracy gate's control does: required
    documents are ignored, the ledger lookup finds nothing, nobody signs off on large amounts."""
    import fitwitness.claims.graph as graph

    real = graph.adjudicate

    def broken(policy, product, requested, ex, present, prior, flags):
        everything = set(present) | {"diagnosis", "admission", "surgery", "receipt"}
        return real(policy, product.model_copy(update={"auto_approve_limit": 10**12}), requested, ex, everything, set(), flags)

    graph.adjudicate = broken
    try:
        yield
    finally:
        graph.adjudicate = real


def claim_invariants(case: dict, gold: dict, first: dict, answers: dict, rerun: dict | None) -> list[str]:
    """Properties of one claim's runs that hold whatever the reference path says."""
    bad: list[str] = []
    sig = first["signature"]

    def idx(prefix: str) -> int:
        return next((i for i, t in enumerate(sig) if t == prefix or t.startswith(prefix + ":")), -1)

    positions = [idx(t) for t in CLAIM_ORDER]
    if any(p < 0 for p in positions) or positions != sorted(positions):
        bad.append(f"nodes out of order or missing: {sig}")
    stray = FORBIDDEN & {t.split(":")[0] for t in sig}
    if stray:
        bad.append(f"unexpected events on a clean run: {sorted(stray)}")
    if sum(t.startswith("payout:") for t in sig) > 1 or len(first["ledger"]) > 1:
        bad.append("more than one payout in a single run")
    pay_at, challenged_at = idx("payout"), idx("challenged")
    if pay_at >= 0 and challenged_at > pay_at:
        bad.append("payout before the challenge step")
    if first["state"] == "waiting_input":
        if pay_at >= 0 or first["ledger"]:
            bad.append("money moved while a reviewer was still being asked")
    elif first["state"] != "completed":
        bad.append(f"run ended in state {first['state']}")
    elif first["ledger"]:
        # The graph, not just the pure function, must agree with the generator's gold.
        paid = first["ledger"][0][1]
        if gold["gold"]["outcome"] != "APPROVE" or paid != gold["gold"]["total_amount"]:
            bad.append(f"paid {paid} where gold says {gold['gold']['outcome']} {gold['gold']['total_amount']}")
    if "approve" in answers:
        after = answers["approve"]
        owed = (first.get("proposed") or 0) > 0  # approving a claim whose proposed amount is zero pays nothing
        if owed and (len(after["ledger"]) != 1 or after["ledger"][0][1] != first["proposed"] or sum(t == "payout:paid" for t in after["signature"]) != 1):
            bad.append("approving a parked claim must pay the proposed amount exactly once")
        if not owed and after["ledger"]:
            bad.append("approving a claim with nothing owed must not pay")
        if "human_review:APPROVE" not in after["signature"]:
            bad.append("the reviewer's answer is not in the path")
        need = first.get("approvals_required") or 1
        asked = after["signature"].count("waiting_input")
        if asked != need:
            bad.append(f"approval needs {need} people, the path asked {asked} times")
        last_ask = max((i for i, t in enumerate(after["signature"]) if t == "waiting_input"), default=-1)
        pay_idx = next((i for i, t in enumerate(after["signature"]) if t.startswith("payout:")), -1)
        if owed and pay_idx < last_ask:
            bad.append("paid before the last required approval")
    if "deny" in answers:
        after = answers["deny"]
        if after["ledger"] or "payout:skipped" not in after["signature"]:
            bad.append("denying a parked claim must not pay")
        if after["signature"].count("waiting_input") != 1:
            bad.append("one refusal is final: a denial must not ask for a second opinion")
    if rerun is not None and len(rerun["ledger"]) != len(first["ledger"]):
        bad.append("running the same claim again changed the ledger")
    return bad


def run_claim_trajectories(repo, jobs, root: Path, *, degraded: bool = False, branches_per_scenario: int = 3,
                           cases_per_scenario: int | None = None) -> dict:
    """Every claim of the corpus once; the reviewer and rerun branches for the first few of each scenario."""
    manifest = json.loads((root / "manifest.json").read_text())
    gold = json.loads((root / "gold.json").read_text())
    seen: dict[str, int] = {}
    rows = []
    for case in manifest["cases"]:
        seen[case["scenario"]] = seen.get(case["scenario"], 0) + 1
        if cases_per_scenario is not None and seen[case["scenario"]] > cases_per_scenario:
            continue
        with_branches = seen[case["scenario"]] <= branches_per_scenario
        scope = _seed_claim(repo, root, case)
        run_id = _claim_run(repo, jobs, scope, case)
        first = _snapshot(repo, jobs, scope, run_id)
        answers: dict[str, dict] = {}
        rerun = None
        if with_branches and first["state"] == "waiting_input":
            _answer(repo, jobs, scope, run_id, "APPROVE")
            answers["approve"] = _snapshot(repo, jobs, scope, run_id)
            other = _seed_claim(repo, root, case)
            other_run = _claim_run(repo, jobs, other, case)
            _answer(repo, jobs, other, other_run, "DENY")
            answers["deny"] = _snapshot(repo, jobs, other, other_run)
        if with_branches and first["ledger"]:
            again = _claim_run(repo, jobs, scope, case)
            rerun = _snapshot(repo, jobs, scope, again)
        rows.append({"case_id": case["case_id"], "scenario": case["scenario"], "gold": gold[case["case_id"]]["gold"]["outcome"],
                     "first": first, "answers": answers, "rerun": rerun,
                     "violations": claim_invariants(case, gold[case["case_id"]], first, answers, rerun)})
    return {"rows": rows, "degraded": degraded}


# --------------------------------------------------------------------------------------------
# drawing runs
# --------------------------------------------------------------------------------------------
def _seed_family(repo, root: Path, family: str = "FW-F000") -> TenantScope:
    from fitwitness.ingest.pdf import extract_pdf

    manifest = json.loads((root / "manifest.json").read_text())
    scope = TenantScope(tenant_id=f"gate-traj-{uuid4()}", user_id="gate", role="operator")
    for e in manifest["document_entries"]:
        if e["family_id"] != family or e["is_revision_update"]:
            continue
        rev = DrawingRevision(tenant_id=scope.tenant_id, **{k: e[k] for k in ("id", "document_id", "drawing_number", "family_id", "revision_label", "supersedes", "kind", "title", "source_hash")})
        data = (root / e["pdf"]).read_bytes()
        repo.add_revision(scope, rev, data)
        repo.save_facts(scope, rev.id, extract_pdf(data, rev))
    return scope


class ScriptedPair:
    """A planner and a challenger that say fixed things, so the multi-agent path is deterministic.

    The planner asks for the same lookup twice (the second must be skipped, not repeated); the
    challenger looks at a different drawing and stops."""

    def __init__(self, revision_ids: list[str]):
        self.revision_ids = revision_ids
        self.calls: list[str] = []

    def plan(self, context, role="planner"):
        from fitwitness.agents.tools import SearchPlan, ToolRequest

        self.calls.append(role)

        def lookup(rid):
            return ToolRequest(name="query_dimensions", arguments={"revision_id": rid, "fields": ["hole_spacing", "material"]})

        if role == "planner":
            return SearchPlan(operations=[lookup(self.revision_ids[0]), lookup(self.revision_ids[0])], stop=False), {"role": role}
        return SearchPlan(operations=[lookup(self.revision_ids[1])], stop=True), {"role": role}


class ScriptedShell:
    """A planner that searches by typing a command and a challenger that inspects what the command found.

    The command is a negation no ranked channel can answer ("every revision with no material fact"). The
    search returns names, never text, so the challenger looks up two candidates nobody has inspected yet,
    which is the only way a fact reaches a verdict."""

    def __init__(self, command: str = "grep -rL 'fact material' ."):
        self.command = command
        self.calls: list[str] = []
        self.looked_up: list[str] = []

    def plan(self, context, role="planner"):
        from fitwitness.agents.tools import SearchPlan, ToolRequest

        self.calls.append(role)
        if role == "planner":
            return SearchPlan(operations=[ToolRequest(name="search_shell", arguments={"command": self.command})], stop=False), {"role": role}
        self.looked_up = [c["revision_id"] for c in context["candidates"] if not c["inspected"]][:2]
        ops = [ToolRequest(name="query_dimensions", arguments={"revision_id": rid, "fields": ["hole_spacing", "material"]}) for rid in self.looked_up]
        return SearchPlan(operations=ops, stop=True), {"role": role}


def drawing_invariants(name: str, events: list[dict], state: str) -> list[str]:
    bad: list[str] = []
    sig = signature(events)
    stray = FORBIDDEN & {t.split(":")[0] for t in sig}
    if stray:
        bad.append(f"{name}: unexpected events {sorted(stray)}")
    if name != "review" and state != "completed":
        bad.append(f"{name}: ended in {state}")
    retrieved = next((e["payload"] for e in events if e["kind"] == "retrieved"), None)
    if retrieved is None:
        bad.append(f"{name}: no retrieval event")
    first_verified = next((i for i, t in enumerate(sig) if t == "verified"), len(sig))
    first_tool = next((i for i, t in enumerate(sig) if t.startswith("tool")), -1)
    if "intent" not in sig or sig.index("intent") > sig.index("retrieved"):
        bad.append(f"{name}: intent must come before retrieval")
    if first_tool >= 0 and first_tool > first_verified:
        bad.append(f"{name}: verdict before the first lookup")
    # one lookup per (revision, fields): never the same query twice
    queries = [json.dumps(e["payload"].get("arguments") or {"revision_id": e["payload"].get("revision_id"), "fields": e["payload"].get("fields")}, sort_keys=True)
               for e in events if e["kind"] == "tool" and (e["payload"] or {}).get("name") == "query_dimensions"]
    if len(queries) != len(set(queries)):
        bad.append(f"{name}: the same lookup ran twice")
    if name == "rules" and retrieved is not None and sum(t == "tool:query_dimensions" for t in sig) != len(retrieved["candidates"]):
        bad.append("rules: one lookup per retrieved candidate expected")
    return bad


def run_drawing_trajectories(repo, jobs, root: Path) -> dict:
    import fitwitness.agents.graph as graph
    from fitwitness.agents.graph import execute_run
    from fitwitness.contracts import Budget

    text = "브래킷 구멍 간격 40mm SUS304"
    out: dict[str, dict] = {}

    # 1. the rules provider, straight through
    scope = _seed_family(repo, root)
    req = RunRequest(search=SearchRequest(text=text), provider="rules", budget=Budget(max_tokens=64000))
    run = jobs.enqueue(scope, req, str(uuid4()))
    execute_run(repo, scope, run.id)
    events = jobs.events(scope, run.id)
    out["rules"] = {"state": jobs.get(scope, run.id).state, "signature": signature(events), "violations": drawing_invariants("rules", events, jobs.get(scope, run.id).state)}

    # 2. the rules provider with a reviewer: stops at unknown evidence, takes the answer, finishes
    scope = _seed_family(repo, root)
    req = RunRequest(search=SearchRequest(text=text), provider="rules", review="on_unknown", budget=Budget(max_tokens=64000))
    run = jobs.enqueue(scope, req, str(uuid4()))
    execute_run(repo, scope, run.id)
    events = jobs.events(scope, run.id)
    before = {"state": jobs.get(scope, run.id).state, "signature": signature(events)}
    violations = drawing_invariants("review", events, before["state"])
    after = None
    if before["state"] == "waiting_input":
        pending = next((e["payload"]["pending"] for e in events if e["kind"] == "waiting_input"), [])
        decisions = {p["revision_id"]: "mismatch" for p in pending}
        jobs.resume(scope, run.id, {"decisions": decisions, "reviewer": "gate", "note": "trajectory check"})
        execute_run(repo, scope, run.id)
        events = jobs.events(scope, run.id)
        after = {"state": jobs.get(scope, run.id).state, "signature": signature(events)}
        if after["state"] != "completed" or "human_review" not in after["signature"] or after["signature"].count("waiting_input") != 1:
            violations.append("review: the reviewer's answer must be applied once and finish the run")
    else:
        violations.append("review: a run with unknown evidence must stop for a reviewer")
    out["review"] = {"before": before, "after": after, "violations": violations}

    # 3. planner + challenger (scripted): a repeated lookup is skipped, the challenger takes its own turn
    scope = _seed_family(repo, root)
    ids = sorted(r.id for r in repo.list_revisions(scope))
    pair = ScriptedPair(ids)
    real = graph.create_model
    graph.create_model = lambda *a: pair
    try:
        req = RunRequest(search=SearchRequest(text=text), provider="anthropic", model_id="scripted", budget=Budget(max_tokens=64000))
        run = jobs.enqueue(scope, req, str(uuid4()))
        execute_run(repo, scope, run.id)
    finally:
        graph.create_model = real
    events = jobs.events(scope, run.id)
    state = jobs.get(scope, run.id).state
    violations = drawing_invariants("multi_agent", events, state)
    sig = signature(events)
    if sig.count("tool_skipped") != 1:
        violations.append("multi_agent: the repeated lookup must be skipped exactly once")
    if pair.calls[:2] != ["planner", "challenger"]:
        violations.append(f"multi_agent: roles ran as {pair.calls}")
    out["multi_agent"] = {"state": state, "signature": sig, "violations": violations}

    # 4. a planner that searches by typing a command, a challenger that inspects what it found
    scope = _seed_family(repo, root)
    script = ScriptedShell()
    real, previous = graph.create_model, os.environ.get("FITWITNESS_SHELL_SEARCH")
    graph.create_model = lambda *a: script
    os.environ["FITWITNESS_SHELL_SEARCH"] = "on"
    try:
        req = RunRequest(search=SearchRequest(text=text), provider="anthropic", model_id="scripted", budget=Budget(max_tokens=64000))
        run = jobs.enqueue(scope, req, str(uuid4()))
        execute_run(repo, scope, run.id)
    finally:
        graph.create_model = real
        if previous is None:
            os.environ.pop("FITWITNESS_SHELL_SEARCH", None)
        else:
            os.environ["FITWITNESS_SHELL_SEARCH"] = previous
    events = jobs.events(scope, run.id)
    state = jobs.get(scope, run.id).state
    violations = drawing_invariants("shell_agent", events, state)
    sig = signature(events)
    searched_first = "tool:search_shell" in sig and "tool:query_dimensions" in sig and sig.index("tool:search_shell") < sig.index("tool:query_dimensions")
    if not searched_first:
        violations.append("shell_agent: the command search must come before the lookups")
    searched = next((e["payload"] for e in events if e["kind"] == "tool" and (e["payload"] or {}).get("name") == "search_shell"), None)
    if searched is None or not searched["items"]:
        violations.append("shell_agent: the command found no revision")
    if not script.looked_up or sig.count("tool:query_dimensions") != len(script.looked_up):
        violations.append("shell_agent: the challenger must look up what the search left uninspected")
    out["shell_agent"] = {"state": state, "signature": sig, "violations": violations}
    return out


# --------------------------------------------------------------------------------------------
# reference file and summary
# --------------------------------------------------------------------------------------------
def build_reference(claims: dict, drawing: dict) -> dict:
    ref_claims = {}
    for row in claims["rows"]:
        entry = {"signature": row["first"]["signature"]}
        for name, snap in row["answers"].items():
            entry[name] = snap["signature"]
        if row["rerun"] is not None:
            entry["rerun"] = row["rerun"]["signature"]
        ref_claims[row["case_id"]] = entry
    ref_drawing = {"rules": drawing["rules"]["signature"], "review_before": drawing["review"]["before"]["signature"],
                   "review_after": (drawing["review"]["after"] or {}).get("signature"), "multi_agent": drawing["multi_agent"]["signature"],
                   "shell_agent": drawing["shell_agent"]["signature"]}
    return {"version": VERSION, "claims": ref_claims, "drawing": ref_drawing}


def _compare(expected, actual, label: str, mismatches: list) -> bool:
    if expected == actual:
        return True
    mismatches.append({"path": label, **(divergence(expected or [], actual or []))})
    return False


def score(claims: dict, drawing: dict | None, reference: dict | None) -> dict:
    """Match rate against the reference, and the invariant violations, for one set of runs."""
    mismatches: list[dict] = []
    compared = matched = 0
    violations = []
    for row in claims["rows"]:
        violations += [f"{row['case_id']}: {v}" for v in row["violations"]]
        ref = (reference or {}).get("claims", {}).get(row["case_id"])
        if reference is None:
            continue
        pairs = [("", ref and ref.get("signature"), row["first"]["signature"])]
        pairs += [(f"/{name}", ref and ref.get(name), snap["signature"]) for name, snap in row["answers"].items()]
        if row["rerun"] is not None:
            pairs.append(("/rerun", ref and ref.get("rerun"), row["rerun"]["signature"]))
        for suffix, expected, actual in pairs:
            compared += 1
            matched += _compare(expected, actual, f"claims/{row['case_id']}{suffix}", mismatches)
    if drawing is not None:
        for name, snap in drawing.items():
            violations += [f"drawing/{name}: {v}" for v in snap["violations"]]
        if reference is not None:
            ref = reference.get("drawing", {})
            for label, expected, actual in [("rules", ref.get("rules"), drawing["rules"]["signature"]),
                                            ("review_before", ref.get("review_before"), drawing["review"]["before"]["signature"]),
                                            ("review_after", ref.get("review_after"), (drawing["review"]["after"] or {}).get("signature")),
                                            ("multi_agent", ref.get("multi_agent"), drawing["multi_agent"]["signature"]),
                                            ("shell_agent", ref.get("shell_agent"), drawing["shell_agent"]["signature"])]:
                compared += 1
                matched += _compare(expected, actual, f"drawing/{label}", mismatches)
    graph_wrong_pay = sum(1 for v in violations if "paid" in v and "gold says" in v)
    return {"compared": compared, "matched": matched, "match_rate": (matched / compared) if (reference is not None and compared) else None,
            "invariant_violations": len(violations), "graph_wrong_pay": graph_wrong_pay,
            "mismatches": mismatches[:20], "violations": violations[:20]}


def run_trajectories(repo, jobs, root: Path, claims_root: Path, reference_path: Path | None = None, *, adopt: bool = False) -> dict:
    """The suite the gate calls: normal runs scored against the reference, degraded claim runs as the control.

    ``adopt`` scores the runs against their own paths (used when the reference is being written on purpose)."""
    reference = json.loads(reference_path.read_text()) if reference_path and reference_path.exists() else None
    claims = run_claim_trajectories(repo, jobs, claims_root)
    drawing = run_drawing_trajectories(repo, jobs, root)
    observed = build_reference(claims, drawing)
    if adopt:
        reference = observed
    normal = score(claims, drawing, reference)
    with degraded_claim_graph():
        broken = run_claim_trajectories(repo, jobs, claims_root, branches_per_scenario=0, cases_per_scenario=4)
    # The control is compared on the first path of every claim only: the branches were not run.
    first_paths = {"claims": {k: {"signature": v["signature"]} for k, v in reference["claims"].items()}} if reference else None
    degraded = score({"rows": broken["rows"]}, None, first_paths)
    return {"version": VERSION, "claims_runs": len(claims["rows"]), "normal": normal, "degraded": degraded, "observed": observed}
