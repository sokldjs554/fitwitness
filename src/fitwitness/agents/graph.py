"""LangGraph executes evidence steps with durable PostgreSQL checkpoints."""

from typing import TypedDict
import json, os, threading, time
from datetime import datetime, timezone
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.types import Command, interrupt
from fitwitness.contracts import (
    Candidate,
    Decision,
    Evidence,
    Fact,
    Requirement,
    ReviewInput,
    RunRequest,
    Usage,
)
from fitwitness.retrieval.pipeline import extract_requirements, search
from fitwitness.verification.conditions import verify
from fitwitness.agents.tools import EvidenceTools, ToolRequest, SearchPlan
from fitwitness.agents.budget import BudgetTracker, BudgetExhausted
from fitwitness.agents.providers import create_model, TransientProviderError
from fitwitness.agents.evidence import EvidenceSession
from fitwitness.runtime.jobs import Jobs


class State(TypedDict, total=False):
    requirements: list[dict]
    candidates: list[dict]
    decisions: list[dict]
    iterations: int
    observations: list[dict]
    usage: dict
    stop: bool
    human: dict


_CHECKPOINTER_READY: set[str] = set()


def ensure_checkpointer(dsn: str) -> None:
    """Create the LangGraph checkpoint tables once per process, outside any transaction.

    ``PostgresSaver.setup()`` runs ``CREATE INDEX CONCURRENTLY`` on a fresh database. That
    statement waits for every open transaction to finish, so calling it while this worker
    already holds the per-run advisory-lock transaction deadlocks the worker against itself
    (the first run on an un-migrated database never returned). Run it before taking the
    fence; later calls are cheap no-ops."""
    if dsn in _CHECKPOINTER_READY:
        return
    with PostgresSaver.from_conn_string(dsn) as cp:
        cp.setup()
    _CHECKPOINTER_READY.add(dsn)


def execute_run(
    repo, scope, run_id, *, encoders=None, fault_after_retrieval=False, lease_token=None
):
    ensure_checkpointer(repo.dsn)
    # A per-run DB session lock spans every model call and checkpoint write.
    # Another production worker cannot claim this run while the process is alive.
    with repo.connection(scope) as fence:
        held = fence.execute(
            "SELECT pg_try_advisory_lock(hashtextextended(%s,1)) AS held",
            (scope.tenant_id + ":" + run_id,),
        ).fetchone()["held"]
        if not held:
            return
        try:
            return _execute_run(
                repo,
                scope,
                run_id,
                encoders=encoders,
                fault_after_retrieval=fault_after_retrieval,
                lease_token=lease_token,
            )
        finally:
            fence.execute(
                "SELECT pg_advisory_unlock(hashtextextended(%s,1))",
                (scope.tenant_id + ":" + run_id,),
            )


def _execute_run(
    repo, scope, run_id, *, encoders=None, fault_after_retrieval=False, lease_token=None
):
    jobs = Jobs(repo)
    raw = jobs.raw(scope, run_id)
    if not raw:
        return
    token = jobs.claim(scope, run_id, requested_token=lease_token)
    if not token:
        return
    request = RunRequest.model_validate(raw["request"])
    snapshot = repo.snapshot(scope)
    budget = BudgetTracker(request.budget)
    budget.usage = Usage.model_validate(raw["usage"])
    # Deadline counts from creation, minus the time the run spent parked for a retry or a reviewer.
    budget.start -= max(
        0, (datetime.now(timezone.utc) - raw["created_at"]).total_seconds() - float(raw.get("waited_seconds") or 0)
    )
    budget.persist = lambda usage: jobs.save_usage(scope, run_id, token, usage)
    stop = threading.Event()
    lost = threading.Event()

    def emit(kind, payload):
        return jobs.event(scope, run_id, kind, payload, token=token)

    def heartbeat():
        while not stop.wait(10):
            try:
                if not jobs.heartbeat(scope, run_id, token):
                    lost.set()
                    return
            except Exception:
                lost.set()
                return

    thread = threading.Thread(target=heartbeat, daemon=True)
    thread.start()
    try:
        if encoders is None:
            from fitwitness.retrieval.embeddings import configured_encoders
            encoders = configured_encoders()
        if snapshot.id != raw["snapshot_id"]:
            jobs.finalize(scope, run_id, raw["snapshot_id"], [], token)
            return
        tools = EvidenceTools(scope, snapshot, repo, budget, encoders, run_id=run_id)
        model = (
            create_model(request.provider, request.model_id, budget)
            if request.provider != "rules"
            else None
        )

        def guard(check_budget=True):
            budget.check(resources=check_budget)
            if lost.is_set() or not jobs.owns_lease(scope, run_id, token):
                raise RuntimeError("실행이 취소되거나 소유권이 만료되었습니다")

        if model:
            model.guard = guard
            model.emit = emit
            # Operator opt-in; anonymous rules demo cannot invoke vision.
            if os.getenv('FITWITNESS_VISION','off') == 'enabled':
                tools.vision = model

        def intent(state):
            guard()
            reqs = request.search.requirements + extract_requirements(
                request.search.text
            )
            emit("intent", {"requirements": [r.model_dump(mode="json") for r in reqs]})
            return {
                "requirements": [r.model_dump(mode="json") for r in reqs],
                "iterations": 0,
                "observations": [],
            }

        def retrieve(state):
            guard()
            budget.tool()
            channels = {"exact", "bm25"} | (
                {"semantic", "image"} if encoders else set()
            )
            candidates = search(
                scope, request.search, snapshot, repo, encoders, channels
            )
            if model:
                candidates = EvidenceSession(candidates, tools.available).candidates
            emit(
                "retrieved",
                {
                    "candidates": [
                        {"revision_id": c.revision_id, "scores": c.scores}
                        for c in candidates
                    ]
                },
            )
            guard()
            return {
                "candidates": [c.model_dump(mode="json") for c in candidates],
                "usage": budget.usage.model_dump(mode="json"),
            }

        def inspect(state, role="planner"):
            guard()
            # This node follows a persisted retrieval checkpoint. Fault injection is scoped to this worker process.
            if fault_after_retrieval and jobs.mark_fault_consumed(scope, run_id):
                emit(
                    "interrupted",
                    {"reason": "체험용 worker 종료; 검색 checkpoint 보존"},
                )
                os._exit(86)
            candidates = [
                Candidate.model_validate(c) for c in state.get("candidates", [])
            ]
            reqs = [Requirement.model_validate(r) for r in state["requirements"]]
            observations = list(state.get("observations", []))
            plan = None
            if model:
                session = EvidenceSession(candidates, tools.available, restored=True)
                context = session.context(request.search.text, state["requirements"], observations,
                                          state.get("decisions", []) if role == "challenger" else [],
                                          saved_tools=tools.saved_tools())
                try:
                    plan, metadata = model.plan(context, role=role)
                    emit("model", metadata)
                except BudgetExhausted as exc:
                    guard(False)
                    emit("budget_stop", {"role": role, "reason": str(exc)})
                    plan = SearchPlan(stop=True, stop_condition="resource budget exhausted")
                emit("agent_plan", {"role": role, **plan.model_dump(mode="json")})
                visited = {session.tool_key(ToolRequest.model_validate(x["tool"])) for x in observations}
                executed = 0
                for op in plan.operations:
                    guard(False)
                    key = session.tool_key(op)
                    if key in visited:
                        emit("tool_skipped", {"name": op.name, "reason": "identical query already observed"})
                        continue
                    if op.name not in tools.available:
                        raise ValueError("model selected unavailable tool")
                    started = time.monotonic()
                    try:
                        result = tools.execute(op)
                    except BudgetExhausted as exc:
                        emit("budget_stop", {"role": role, "reason": str(exc)})
                        plan.stop = True
                        break
                    session.observe(op, result)
                    visited.add(key)
                    executed += 1
                    observations.append({"tool": op.model_dump(mode="json"), "result": result})
                    emit("tool", {"role": role, "name": op.name, "arguments": op.arguments,
                                  "items": len(result), "latency_ms": (time.monotonic() - started) * 1000,
                                  "fact_ids": [f.id for c in session.candidates for f in c.facts]})
                if plan.operations and not executed:
                    plan.stop = True
                candidates = session.candidates
            else:
                # Visible deterministic baseline, not a fabricated model execution.
                for c in candidates:
                    guard()
                    if budget.usage.tool_calls >= budget.budget.max_tool_calls:
                        break
                    op = ToolRequest(
                        name="query_dimensions",
                        arguments={
                            "revision_id": c.revision_id,
                            "fields": [r.field for r in reqs],
                        },
                    )
                    started = time.monotonic()
                    result = tools.execute(op)
                    observations.append(
                        {"tool": op.model_dump(mode="json"), "result": result}
                    )
                    emit(
                        "tool",
                        {
                            "name": op.name,
                            "revision_id": c.revision_id,
                            "fields": [r.field for r in reqs],
                            "items": len(result),
                            "latency_ms": (time.monotonic() - started) * 1000,
                        },
                    )
            decisions = [verify(reqs, c, snapshot.id) for c in candidates]
            decisions.sort(
                key=lambda d: (
                    {"match": 0, "unknown": 1, "mismatch": 2}[d.verdict],
                    d.revision_id,
                )
            )
            emit(
                "verified",
                {
                    "match": sum(d.verdict == "match" for d in decisions),
                    "mismatch": sum(d.verdict == "mismatch" for d in decisions),
                    "unknown": sum(d.verdict == "unknown" for d in decisions),
                    "mode": "규칙 기반 검증" if not model else request.mode,
                },
            )
            guard(False)
            return {
                "candidates": [c.model_dump(mode="json") for c in candidates],
                "decisions": [d.model_dump(mode="json") for d in decisions],
                "iterations": state.get("iterations", 0) + 1,
                "observations": observations,
                "usage": budget.usage.model_dump(mode="json"),
                "stop": plan.stop if plan else True,
            }

        def needs_review(state):
            return (
                request.review == "on_unknown"
                and not state.get("human")
                and any(d["verdict"] == "unknown" for d in state.get("decisions", []))
            )

        def finish(state):
            return "review" if needs_review(state) else END

        def review(state):
            """Stop here until a reviewer answers; their verdicts become human evidence."""
            decisions = [Decision.model_validate(d) for d in state.get("decisions", [])]
            numbers = {rv.id: rv.drawing_number for rv in repo.list_revisions(scope)}
            pending = [
                {
                    "revision_id": d.revision_id,
                    "drawing_number": numbers.get(d.revision_id),
                    "unknown_fields": sorted({e.field for e in d.evidence if e.verdict == "unknown"}),
                }
                for d in decisions
                if d.verdict == "unknown"
            ]
            question = f"확인 필요 후보 {len(pending)}건: 담당자 결정을 기다립니다"
            answer = interrupt({
                "question": question,
                "pending": pending,
                "accepts": {"decisions": {"<revision_id>": "match | mismatch | unknown"}, "reviewer": "string", "note": "string"},
            })
            review_input = ReviewInput.model_validate(answer)
            changed = []
            for d in decisions:
                wanted = review_input.decisions.get(d.revision_id)
                if wanted is None or wanted == d.verdict:
                    continue
                d.evidence.append(Evidence(
                    requirement_id="review", candidate_revision_id=d.revision_id, field="review", verdict=wanted,
                    summary=f"담당자 {review_input.reviewer} 확인: {review_input.note or '추가 설명 없음'}", verifier_version="human-v1",
                ))
                d.verdict = wanted
                d.reviewed_by = review_input.reviewer
                d.review_note = review_input.note or None
                changed.append(d.revision_id)
            emit("human_review", {"reviewer": review_input.reviewer, "changed": changed, "note": review_input.note})
            return {"decisions": [d.model_dump(mode="json") for d in decisions], "human": review_input.model_dump(mode="json")}

        def route(state):
            evidence = EvidenceSession([Candidate.model_validate(c) for c in state.get("candidates", [])], tools.available, restored=True)
            if model and evidence.exhausted(state["requirements"], state.get("observations", [])):
                emit("evidence_exhausted", {"reason": "all required fields queried; source omissions remain unknown"})
                return finish(state)
            try:
                budget.check()
            except BudgetExhausted as exc:
                emit("budget_stop", {"reason": str(exc)})
                return finish(state)
            return (
                "inspect"
                if model and request.mode != "fixed"
                and EvidenceSession.needs_more(state.get("decisions", []), state["iterations"], state.get("stop", False))
                and budget.usage.tool_calls < budget.budget.max_tool_calls
                and budget.usage.model_calls < budget.budget.max_model_calls
                else finish(state)
            )

        builder = StateGraph(State)
        builder.add_node("intent", intent)
        builder.add_node("retrieve", retrieve)
        builder.add_node("inspect", inspect)
        builder.add_node("review", review)
        builder.add_edge("review", END)
        builder.add_edge(START, "intent")
        builder.add_edge("intent", "retrieve")
        builder.add_edge("retrieve", "inspect")
        if model and request.mode == "fitwitness":
            builder.add_node("challenge", lambda state: inspect(state, role="challenger"))
            def challenge_route(state):
                if budget.usage.model_calls >= budget.budget.max_model_calls or budget.usage.tool_calls >= budget.budget.max_tool_calls:
                    emit("budget_stop", {"role": "challenger", "reason": "call limit reached; inspected decisions retained"})
                    return finish(state)
                try:
                    budget.check()
                except BudgetExhausted as exc:
                    emit("budget_stop", {"role": "challenger", "reason": str(exc)})
                    return finish(state)
                return "challenge"
            builder.add_conditional_edges("inspect", challenge_route)
            builder.add_conditional_edges("challenge", route)
        else:
            builder.add_conditional_edges("inspect", route)
        with PostgresSaver.from_conn_string(repo.dsn) as cp:
            graph = builder.compile(checkpointer=cp)
            config = {"configurable": {"thread_id": scope.tenant_id + ":" + run_id}}

            def pending_interrupts():
                return [i.value for task in graph.get_state(config).tasks for i in task.interrupts]

            existing = graph.get_state(config)
            if existing.values:
                emit(
                    "resumed",
                    {
                        "checkpoint_id": existing.config.get("configurable", {}).get(
                            "checkpoint_id"
                        )
                    },
                )
                # DB reservation ledger is authoritative, including interrupted calls.
                budget.usage = Usage.model_validate(jobs.raw(scope, run_id)["usage"])
                if raw.get("human_input") and pending_interrupts():
                    out = graph.invoke(Command(resume=raw["human_input"]), config, durability="sync")
                elif existing.next:
                    out = graph.invoke(None, config, durability="sync")
                else:
                    out = existing.values
            else:
                out = graph.invoke({}, config, durability="sync")
            waiting = pending_interrupts()
            if waiting:
                # The graph stopped at the review node; park the job until a reviewer answers.
                jobs.wait_input(scope, run_id, token, waiting[0].get("question", "담당자 확인 필요"), waiting[0])
                return
            jobs.finalize(
                scope,
                run_id,
                snapshot.id,
                [Decision.model_validate(d) for d in out.get("decisions", [])],
                token,
                budget.usage,
            )
    except Exception as exc:
        jobs.fail(scope, run_id, token, str(exc), retryable=isinstance(exc, TransientProviderError))
        raise
    finally:
        stop.set()
        thread.join(timeout=1)
