"""The road a real model takes through the drawing graph, family by family, under a hard cost ceiling.

The gate's trajectory suite runs the rules provider and scripted planners; it says nothing about the path a
model chooses. This module runs the same graph with a provider (Claude) on every family of the test split, one
query per family built from the family's base drawing, and records for each run

  * the verdicts against the generator's labels (the deterministic verifier owns verdicts; the model only chooses
    which tools to call and when to stop),
  * the path: which tools were called in what order, whether every retrieved candidate was looked up before the
    first verdict, whether the challenger took a turn, why the run stopped,
  * the drawing invariants of ``evaluation/trajectory.py`` (no verdict before a lookup, no lookup twice, ...),
  * cost, tokens and latency.

The rules provider runs first on every family as the reference path, free. A paid run is started only when its
whole budget still fits under the ceiling next to what the ledger reports as spent so far; the ceiling cannot be
overrun by a run that uses its full budget. Every wrong verdict is classified: the candidate was never looked up,
it was looked up and the verdict still differs, or the run did not complete.

    python -m fitwitness.evaluation.agent_trajectory --provider anthropic --model claude-haiku-4-5-20251001 \\
        --max-cost-usd 0.8 --output artifacts/agent-trajectory
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from statistics import mean, median
from uuid import uuid4

from fitwitness.contracts import Budget, RunRequest, SearchRequest
from fitwitness.evaluation.trajectory import _seed_family, drawing_invariants, signature

MODES = ("fixed", "react", "fitwitness")
KIND_WORD = {"bracket": "브래킷", "flange": "플랜지", "shaft": "샤프트", "housing": "하우징"}
DIMENSION_WORD = {"hole_spacing": "구멍 간격", "width": "너비"}


def family_cases(root: Path, split: str = "test") -> list[dict]:
    """One query per family: the base drawing's dimension and material, as a person would type them, and the
    verdict the labels give every revision of the family (the revision update is not seeded, as in the gate)."""
    manifest = json.loads((root / "manifest.json").read_text())
    gold = json.loads((root / "gold/labels.json").read_text())
    cases = []
    for family in sorted(manifest["family_splits"][split]):
        entries = [e for e in manifest["document_entries"] if e["family_id"] == family and not e["is_revision_update"]]
        base = entries[0]
        truth = gold[base["id"]]
        dimension = "hole_spacing" if truth.get("hole_spacing") is not None else "width"
        need = truth[dimension]
        expected = {}
        for e in entries:
            t = gold[e["id"]]
            if t.get(dimension) != need or t.get("material") not in ("SUS304", None):
                expected[e["id"]] = "mismatch"
            elif t.get("material") is None:
                expected[e["id"]] = "unknown"
            else:
                expected[e["id"]] = "match"
        cases.append({"family_id": family, "kind": base["kind"], "dimension": dimension, "need": need,
                      "query": f"{KIND_WORD[base['kind']]} {DIMENSION_WORD[dimension]} {need}mm SUS304",
                      "revision_ids": [e["id"] for e in entries], "expected": expected})
    return cases


class Charged:
    """What one finished run reports: what it paid, and what it still had reserved when it ended."""

    def __init__(self, cost_usd: Decimal, reserved_cost_usd: Decimal):
        self.cost_usd, self.reserved_cost_usd = cost_usd, reserved_cost_usd


class Ledger:
    """What the paid runs have cost, and whether one more fits under the ceiling."""

    def __init__(self, ceiling: Decimal):
        self.ceiling = ceiling
        self.spent = Decimal(0)
        self.reserved = Decimal(0)

    def fits(self, cap: Decimal) -> bool:
        return self.spent + self.reserved + cap <= self.ceiling

    def reserve(self, cap: Decimal) -> None:
        self.reserved += cap

    def settle(self, cap: Decimal, usage) -> Decimal:
        """A finished run is charged what the ledger reports, plus any reservation it left behind (a call that
        was cut off mid-flight keeps its reservation: counted, not forgiven)."""
        self.reserved -= cap
        charged = (usage.cost_usd or Decimal(0)) + usage.reserved_cost_usd
        self.spent += charged
        return charged


def _lookups(events: list[dict]) -> set[str]:
    """The revisions whose dimensions were looked up. The model's tool events carry the arguments; the rules
    provider's carry the revision id directly."""
    out = set()
    for e in events:
        p = e.get("payload") or {}
        if e["kind"] == "tool" and p.get("name") == "query_dimensions":
            out.add((p.get("arguments") or {}).get("revision_id") or p.get("revision_id"))
    return out


def path_checks(events: list[dict], expected: dict[str, str], actual: dict[str, str], mode: str, state: str = "completed") -> dict:
    """What the path says beyond the verdicts: coverage of the retrieved candidates before the first verdict,
    the roles that took a turn, why the run stopped, and a class for every wrong verdict."""
    sig = signature(events)
    retrieved = next((e["payload"]["candidates"] for e in events if e["kind"] == "retrieved"), [])
    retrieved_ids = [c["revision_id"] for c in retrieved]
    first_verified = next((i for i, e in enumerate(events) if e["kind"] == "verified"), len(events))
    looked_up_before, looked_up = _lookups(events[:first_verified]), _lookups(events)
    plans = [e["payload"] for e in events if e["kind"] == "agent_plan"]
    roles = [p.get("role") for p in plans]
    tools = [(e["payload"] or {}).get("name") for e in events if e["kind"] == "tool"]
    stop = next((k for k in ("evidence_exhausted", "budget_stop", "waiting_input") if k in sig), None)
    if stop is None and plans and plans[-1].get("stop"):
        stop = "plan_stop"
    failures = {}
    for rid, want in expected.items():
        got = actual.get(rid)
        if got == want:
            continue
        failures[rid] = {"expected": want, "actual": got,
                         "class": "run_incomplete" if got is None else "not_inspected" if rid not in looked_up else "inspected_wrong"}
    return {
        "retrieved": len(retrieved_ids),
        "coverage_before_verdict": round(len(looked_up_before & set(retrieved_ids)) / len(retrieved_ids), 3) if retrieved_ids else None,
        "coverage": round(len(looked_up & set(retrieved_ids)) / len(retrieved_ids), 3) if retrieved_ids else None,
        "uninspected": sorted(set(retrieved_ids) - looked_up),
        "roles": roles,
        "challenger_turn": "challenger" in roles,
        "challenger_tools": sum(1 for p in plans if p.get("role") == "challenger" for _ in p.get("operations", [])),
        "tools": tools,
        "skipped_repeats": sig.count("tool_skipped"),
        "model_errors": [e["payload"] for e in events if e["kind"] in ("model_error", "model_schema_error")],
        "stop": stop,
        "signature": sig,
        "violations": drawing_invariants(mode, events, state),
        "failures": failures,
    }


def _row(case: dict, mode: str, provider: str, view, events: list[dict], latency_ms: float, error: str | None) -> dict:
    actual = {d.revision_id: d.verdict.value if hasattr(d.verdict, "value") else d.verdict for d in view.decisions}
    checks = path_checks(events, case["expected"], actual, mode if provider != "rules" else "rules", view.state)
    usage = view.usage
    return {"family_id": case["family_id"], "kind": case["kind"], "query": case["query"], "mode": mode, "provider": provider,
            "run_id": view.id, "state": view.state, "error": error, "latency_ms": round(latency_ms, 1),
            "correct": sum(actual.get(r) == v for r, v in case["expected"].items()), "total": len(case["expected"]),
            "expected": case["expected"], "actual": actual,
            "model_calls": usage.model_calls, "tool_calls": usage.tool_calls, "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens, "cost_usd": float(usage.cost_usd or 0), "reserved_left_usd": float(usage.reserved_cost_usd),
            **checks, "events": events}


def run_one(repo, jobs, root: Path, case: dict, request: RunRequest) -> dict:
    from fitwitness.agents.graph import execute_run

    scope = _seed_family(repo, root, case["family_id"])
    job = jobs.enqueue(scope, request, str(uuid4()))
    started, error = time.perf_counter(), None
    try:
        execute_run(repo, scope, job.id)
    except Exception as exc:  # noqa: BLE001 - the failure is the finding; it goes in the row
        error = f"{type(exc).__name__}: {str(exc)[:300]}"
    view, events = jobs.get(scope, job.id), [dict(e) for e in jobs.events(scope, job.id)]
    for e in events:
        e["timestamp"] = str(e["timestamp"])
    return _row(case, request.mode, request.provider, view, events, (time.perf_counter() - started) * 1000, error)


def rejected(row: dict) -> str | None:
    """Why the batch must stop after this run: the provider refused before any token was served (a bad key, an
    empty balance, a wrong model id). The first such run is enough; the others would fail the same way."""
    if row["provider"] == "rules" or row["state"] == "completed":
        return None
    if row["model_calls"] >= 1 and row["input_tokens"] == 0 and row["output_tokens"] == 0 and row["error"]:
        return f"provider rejected the first call: {row['error'][:160]}"
    return None


def summarise(rows: list[dict], ledger: Ledger | None) -> dict:
    by_mode: dict[str, dict] = {}
    rules = {r["family_id"]: r for r in rows if r["provider"] == "rules"}
    for mode in ["rules", *MODES]:
        group = [r for r in rows if (r["provider"] == "rules") == (mode == "rules") and (mode == "rules" or r["mode"] == mode)]
        if not group:
            continue
        done = [r for r in group if r["state"] == "completed"]
        verdicts = sum(r["total"] for r in group)
        by_mode[mode] = {
            "runs": len(group), "completed": len(done),
            "accuracy": round(sum(r["correct"] for r in group) / verdicts, 4) if verdicts else None,
            "agreement_with_rules": round(mean(sum(r["actual"].get(k) == rules[r["family_id"]]["actual"].get(k) for k in r["expected"]) / r["total"]
                                              for r in group if r["family_id"] in rules), 4) if rules and group else None,
            "coverage_before_verdict": round(mean(r["coverage_before_verdict"] for r in group if r["coverage_before_verdict"] is not None), 3) if any(r["coverage_before_verdict"] is not None for r in group) else None,
            "challenger_turns": sum(r["challenger_turn"] for r in group),
            "invariant_violations": sum(len(r["violations"]) for r in group),
            "failure_classes": {c: sum(1 for r in group for f in r["failures"].values() if f["class"] == c) for c in ("not_inspected", "inspected_wrong", "run_incomplete")},
            "stops": {s: sum(1 for r in group if r["stop"] == s) for s in sorted({r["stop"] for r in group}, key=str)},
            "model_calls_mean": round(mean(r["model_calls"] for r in group), 2),
            "tool_calls_mean": round(mean(r["tool_calls"] for r in group), 2),
            "model_errors": sum(len(r["model_errors"]) for r in group),
            "cost_usd": round(sum(r["cost_usd"] for r in group), 6),
            "latency_ms_p50": round(median(r["latency_ms"] for r in group), 1),
        }
    return {"by_mode": by_mode, "spent_usd": float(ledger.spent) if ledger else 0.0, "ceiling_usd": float(ledger.ceiling) if ledger else 0.0}


def run(args) -> dict:
    from fitwitness.runtime.jobs import Jobs
    from fitwitness.storage.repository import Repository

    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=False)
    root = Path(args.corpus)
    cases = family_cases(root, args.split)
    if args.families:
        cases = [c for c in cases if c["family_id"] in args.families]
    repo = Repository(os.environ["FITWITNESS_DATABASE_URL"])
    repo.migrate()
    jobs = Jobs(repo)
    jobs.migrate()
    ceiling, cap = Decimal(str(args.max_cost_usd)), Decimal(str(args.per_run_usd))
    modes = [m for m in args.modes.split(",") if m]
    assert set(modes) <= set(MODES), modes
    protocol = {
        "split": args.split, "families": [c["family_id"] for c in cases], "queries": {c["family_id"]: c["query"] for c in cases},
        "provider": args.provider, "model": args.model, "modes": modes, "ceiling_usd": str(ceiling), "per_run_cap_usd": str(cap),
        "budget": {"max_model_calls": 4, "max_tool_calls": 16, "max_tokens": 64000, "deadline_seconds": 240},
        "pricing_usd_per_million": {"input": os.getenv(f"FITWITNESS_{args.provider.upper()}_INPUT_USD_PER_MILLION"),
                                    "output": os.getenv(f"FITWITNESS_{args.provider.upper()}_OUTPUT_USD_PER_MILLION")},
        "code_sha": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "module_hash": sha256(Path(__file__).read_bytes()).hexdigest(),
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    (out / "protocol.json").write_text(json.dumps(protocol, ensure_ascii=False, indent=2))
    rows, skipped = [], []
    ledger = Ledger(ceiling)
    stop_reason = None

    def record(row):
        rows.append(row)
        with (out / "runs.jsonl").open("a") as f:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        print(row["provider"], row["mode"], row["family_id"], row["state"], f"{row['correct']}/{row['total']}",
              "coverage", row["coverage_before_verdict"], "stop", row["stop"], "cost", row["cost_usd"], flush=True)

    for case in cases:  # the reference path, free
        record(run_one(repo, jobs, root, case, RunRequest(search=SearchRequest(text=case["query"]), provider="rules", budget=Budget(max_tokens=64000))))
    for mode in modes:
        for case in cases:
            if stop_reason:
                skipped.append({"family_id": case["family_id"], "mode": mode, "reason": stop_reason})
                continue
            if not ledger.fits(cap):
                skipped.append({"family_id": case["family_id"], "mode": mode, "reason": f"ceiling: spent {ledger.spent} + cap {cap} > {ceiling}"})
                continue
            ledger.reserve(cap)
            request = RunRequest(search=SearchRequest(text=case["query"]), provider=args.provider, model_id=args.model, mode=mode,
                                 budget=Budget(max_cost_usd=cap, max_tokens=64000, max_model_calls=4, max_tool_calls=16, deadline_seconds=240))
            row = run_one(repo, jobs, root, case, request)
            row["charged_usd"] = float(ledger.settle(cap, Charged(Decimal(str(row["cost_usd"])), Decimal(str(row["reserved_left_usd"])))))
            record(row)
            stop_reason = rejected(row)
    report = {"status": "measured" if any(r["provider"] != "rules" and r["state"] == "completed" for r in rows) else "not measured",
              "stopped": stop_reason, "protocol": protocol, "summary": summarise(rows, ledger), "skipped": skipped,
              "rows": [{k: v for k, v in r.items() if k != "events"} for r in rows],
              "limitations": [
                  "합성 도면 test split 6개 family, family마다 질의 하나, 모드마다 한 번 실행입니다. 반복이 없어 모델의 변동은 보지 못합니다.",
                  "판정은 결정적 검증기가 내리고 모델은 어떤 도구를 언제 부를지와 멈출 때만 고릅니다. 정확도는 모델의 판단력이 아니라 경로의 충분함을 잽니다.",
                  "검색은 도번·BM25 채널만 쓰고, 한 family의 도면 다섯 장만 색인된 작업 공간입니다.",
              ]}
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return report


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--provider", choices=["anthropic", "openai"], default="anthropic")
    p.add_argument("--model", default=None, help="the provider's model id (default: Claude Haiku 4.5 for anthropic, FITWITNESS_OPENAI_MODEL for openai)")
    p.add_argument("--modes", default=",".join(MODES))
    p.add_argument("--max-cost-usd", type=float, required=True, help="hard ceiling for the whole run")
    p.add_argument("--per-run-usd", type=float, default=0.04, help="budget of one graph run; reserved before it starts")
    p.add_argument("--corpus", default="var/corpus")
    p.add_argument("--split", default="test")
    p.add_argument("--families", nargs="*")
    p.add_argument("--output", required=True)
    args = p.parse_args(argv)
    args.model = args.model or ("claude-haiku-4-5-20251001" if args.provider == "anthropic" else os.environ.get("FITWITNESS_OPENAI_MODEL", ""))
    assert args.model, "no model id: pass --model or set FITWITNESS_OPENAI_MODEL"
    assert 0 < args.max_cost_usd <= 1.0, "the ceiling is at most 1 USD"
    assert 0 < args.per_run_usd <= 0.1
    report = run(args)
    s = report["summary"]
    for mode, m in s["by_mode"].items():
        print("== agent-trajectory", mode, json.dumps(m, ensure_ascii=False))
    print("== agent-trajectory spent", s["spent_usd"], "ceiling", s["ceiling_usd"], "skipped", len(report["skipped"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
