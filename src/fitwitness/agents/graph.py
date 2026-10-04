"""LangGraph executes evidence steps with durable PostgreSQL checkpoints."""

from typing import TypedDict
import json, os, threading, time
from datetime import datetime, timezone
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.postgres import PostgresSaver
from fitwitness.contracts import (
    Candidate,
    Decision,
    Fact,
    Requirement,
    RunRequest,
    Usage,
)
from fitwitness.retrieval.pipeline import extract_requirements, search
from fitwitness.verification.conditions import verify
from fitwitness.agents.tools import EvidenceTools, ToolRequest, SCHEMAS
from fitwitness.agents.budget import BudgetTracker
from fitwitness.agents.providers import create_model
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


def execute_run(
    repo, scope, run_id, *, encoders=None, fault_after_retrieval=False, lease_token=None
):
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
    budget.start -= max(
        0, (datetime.now(timezone.utc) - raw["created_at"]).total_seconds()
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
        if snapshot.id != raw["snapshot_id"]:
            jobs.finalize(scope, run_id, raw["snapshot_id"], [], token)
            return
        tools = EvidenceTools(scope, snapshot, repo, budget, encoders)
        model = (
            create_model(request.provider, request.model_id, budget)
            if request.provider != "rules"
            else None
        )

        def guard():
            budget.check()
            if lost.is_set() or not jobs.owns_lease(scope, run_id, token):
                raise RuntimeError("실행이 취소되거나 소유권이 만료되었습니다")

        if model:
            model.guard = guard
            model.emit = emit

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
                                          state.get("decisions", []) if role == "challenger" else [])
                plan, metadata = model.plan(context, role=role)
                emit("model", metadata)
                emit("agent_plan", {"role": role, **plan.model_dump(mode="json")})
                for op in plan.operations:
                    guard()
                    if op.name not in tools.available:
                        raise ValueError("model selected unavailable tool")
                    started = time.monotonic()
                    result = tools.execute(op)
                    session.observe(op, result)
                    observations.append({"tool": op.model_dump(mode="json"), "result": result})
                    emit("tool", {"role": role, "name": op.name, "arguments": op.arguments,
                                  "items": len(result), "latency_ms": (time.monotonic() - started) * 1000,
                                  "fact_ids": [f.id for c in session.candidates for f in c.facts]})
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
            guard()
            return {
                "candidates": [c.model_dump(mode="json") for c in candidates],
                "decisions": [d.model_dump(mode="json") for d in decisions],
                "iterations": state.get("iterations", 0) + 1,
                "observations": observations,
                "usage": budget.usage.model_dump(mode="json"),
                "stop": plan.stop if plan else True,
            }

        def route(state):
            return (
                "inspect"
                if model and request.mode != "fixed"
                and EvidenceSession.needs_more(state.get("decisions", []), state["iterations"], state.get("stop", False))
                and budget.usage.tool_calls < budget.budget.max_tool_calls
                and budget.usage.model_calls < budget.budget.max_model_calls
                else END
            )

        builder = StateGraph(State)
        builder.add_node("intent", intent)
        builder.add_node("retrieve", retrieve)
        builder.add_node("inspect", inspect)
        builder.add_edge(START, "intent")
        builder.add_edge("intent", "retrieve")
        builder.add_edge("retrieve", "inspect")
        if model and request.mode == "fitwitness":
            builder.add_node("challenge", lambda state: inspect(state, role="challenger"))
            builder.add_edge("inspect", "challenge")
            builder.add_conditional_edges("challenge", route)
        else:
            builder.add_conditional_edges("inspect", route)
        with PostgresSaver.from_conn_string(repo.dsn) as cp:
            cp.setup()
            graph = builder.compile(checkpointer=cp)
            config = {"configurable": {"thread_id": scope.tenant_id + ":" + run_id}}
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
                out = (
                    graph.invoke(None, config, durability="sync")
                    if existing.next
                    else existing.values
                )
            else:
                out = graph.invoke({}, config, durability="sync")
            jobs.finalize(
                scope,
                run_id,
                snapshot.id,
                [Decision.model_validate(d) for d in out.get("decisions", [])],
                token,
                budget.usage,
            )
    except Exception as exc:
        jobs.fail(scope, run_id, token, str(exc))
        raise
    finally:
        stop.set()
        thread.join(timeout=1)
