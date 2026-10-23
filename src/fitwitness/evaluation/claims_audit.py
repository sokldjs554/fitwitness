"""The model auditor on decisions that are about to pay, measured where it matters.

``claims/audit.py`` shows a model the terms, the documents, what was read and the decision, and asks for reasons
not to pay. Two questions decide whether that is worth a call: on the weakened control (``--mode degraded``: the
document requirement ignored, the ledger blind to earlier payments, the approval limit gone, which lets claims
through that should not pay) how many of the wrong payments does it stop, and on the sound run (``--mode clean``)
how often does it stop a payment that was right. Every APPROVE decision of the split is audited once; the gold
says which of them were wrong. There is no model in the decision itself, so a verdict that is wrong costs a
person's look, never money.

Cost: each call reserves its cap on a ledger under a hard ceiling; a decision that would not fit is skipped and
listed; three failures in a row end the batch.

    python -m fitwitness.evaluation.claims_audit --mode degraded --max-cost-usd 0.5 --per-call-usd 0.03
"""

from __future__ import annotations

import json
import os
import statistics
import subprocess
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from fitwitness.claims.audit import AuditVerdict, audit
from fitwitness.claims.evaluate import decide
from fitwitness.claims.models import Policy
from fitwitness.claims.policy import PRODUCTS
from fitwitness.evaluation.agent_trajectory import Ledger
from fitwitness.evaluation.claims_model import SPLITS

MODES = {"clean": False, "degraded": True}


class StubModel:
    """Agrees with everything: exercises the harness without a credential."""

    def __init__(self):
        self.emit = None

    def structured(self, schema, prompt, payload, role="auditor"):
        assert schema is AuditVerdict
        return AuditVerdict(agree=True, recommendation="pay"), {"role": role, "attempt": 1, "repair": 0, "latency_ms": 0.0, "provider": "stub",
                                                                  "model_id": "stub", "usage": {"input_tokens": len(payload) // 4, "output_tokens": 20}}


def judge(gold: dict, decision, verdict: AuditVerdict | None) -> dict:
    """What the verdict did to a decision the gold can judge."""
    should_flag = decision.outcome != gold["outcome"] or decision.total_amount != gold["total_amount"]
    flagged = verdict is not None and verdict.recommendation != "pay"
    return {"should_flag": should_flag, "flagged": flagged, "caught": should_flag and flagged, "false_flag": flagged and not should_flag,
            "missed": should_flag and not flagged}


def _client(provider: str, model: str, cap: Decimal):
    if provider == "stub":
        return StubModel(), None
    from fitwitness.agents.budget import BudgetTracker
    from fitwitness.agents.providers import create_model
    from fitwitness.contracts import Budget

    budget = BudgetTracker(Budget(max_cost_usd=cap, max_model_calls=4, max_tokens=48000, deadline_seconds=180))
    client = create_model(provider, model, budget)
    return client, budget


def evaluate(root: Path, *, provider: str, model: str, ceiling: Decimal, cap: Decimal, split: str = "all", mode: str = "degraded",
             max_cases: int | None = None) -> dict:
    manifest = json.loads((root / "manifest.json").read_text())
    gold = json.loads((root / "gold.json").read_text())
    chosen = [c for c in manifest["cases"] if SPLITS[split](int(c["case_id"].split("-")[-1]))][: max_cases or None]
    ledger = Ledger(ceiling)
    rows: list[dict] = []
    skipped: list[dict] = []
    failures_in_a_row, stopped = 0, None
    for case in chosen:
        g = gold[case["case_id"]]
        docs = [(d["id"], d["kind"], (root / case["case_id"] / f"{d['id']}.pdf").read_bytes()) for d in case["documents"]]
        ex, decision, _ = decide(case, docs, degraded=MODES[mode])
        if decision.outcome != "APPROVE":
            continue  # nothing is about to be paid
        if stopped or not ledger.fits(cap):
            skipped.append({"case_id": case["case_id"], "reason": stopped or f"ceiling: spent {ledger.spent} + cap {cap} > {ceiling}"})
            continue
        client, budget = _client(provider, model, cap)
        events: list[dict] = []
        client.emit = lambda kind, payload, _e=events: _e.append({"kind": kind, "payload": payload})
        ledger.reserve(cap)
        started, verdict, meta, error = time.perf_counter(), None, {}, None
        try:
            verdict, meta = audit(client, Policy.model_validate(case["policy"]), PRODUCTS[case["policy"]["product_id"]], list(case["requested"]), docs, ex,
                                  decision, list(case["prior_paid_keys"]))
        except Exception as exc:  # noqa: BLE001 - the failure is the finding
            error = f"{type(exc).__name__}: {str(exc)[:300]}"
        usage = budget.usage if budget is not None else None
        charged = float(ledger.settle(cap, usage)) if usage is not None else float(ledger.settle(cap, _Zero()))
        failures_in_a_row = failures_in_a_row + 1 if error else 0
        if failures_in_a_row >= 3:
            stopped = f"three failures in a row; last: {error}"
        verdict_dump = verdict.model_dump() if verdict else None
        rows.append({"case_id": case["case_id"], "scenario": case["scenario"], "gold": [g["gold"]["outcome"], g["gold"]["total_amount"]],
                     "decision": [decision.outcome, decision.total_amount, decision.rule_ids()],
                     **judge(g["gold"], decision, verdict), "verdict": verdict_dump, "error": error,
                     "latency_ms": round((time.perf_counter() - started) * 1000, 1),
                     "cost_usd": float(usage.cost_usd or 0) if usage is not None else 0.0, "charged_usd": charged,
                     "input_tokens": usage.input_tokens if usage is not None else 0, "output_tokens": usage.output_tokens if usage is not None else 0,
                     "model_errors": [e["payload"] for e in events if e["kind"] in ("model_error", "model_schema_error")],
                     "repairs": sum(1 for e in events if e["kind"] == "model" and (e["payload"] or {}).get("repair"))})
    return {"summary": summarise(rows, mode, ledger), "rows": rows, "skipped": skipped, "stopped": stopped}


class _Zero:
    cost_usd = Decimal(0)
    reserved_cost_usd = Decimal(0)


def summarise(rows: list[dict], mode: str, ledger: Ledger | None = None) -> dict:
    positives = [r for r in rows if r["should_flag"]]
    negatives = [r for r in rows if not r["should_flag"]]
    by_scenario: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        c = by_scenario[r["scenario"]]
        c["audited"] += 1
        c["should_flag"] += r["should_flag"]
        c["caught"] += r["caught"]
        c["false_flag"] += r["false_flag"]
    codes_caught: Counter = Counter(c["code"] for r in rows if r["caught"] for c in (r["verdict"] or {}).get("concerns", []))
    codes_false: Counter = Counter(c["code"] for r in rows if r["false_flag"] for c in (r["verdict"] or {}).get("concerns", []))
    return {
        "mode": mode, "audited": len(rows), "failed_calls": sum(1 for r in rows if r["error"]),
        "wrong_payments": len(positives), "caught": sum(r["caught"] for r in positives),
        "catch_rate": round(sum(r["caught"] for r in positives) / len(positives), 4) if positives else None,
        "correct_payments": len(negatives), "false_flags": sum(r["false_flag"] for r in negatives),
        "false_flag_rate": round(sum(r["false_flag"] for r in negatives) / len(negatives), 4) if negatives else None,
        "by_scenario": {s: dict(c) for s, c in sorted(by_scenario.items())},
        "concern_codes_on_caught": dict(codes_caught.most_common()), "concern_codes_on_false_flags": dict(codes_false.most_common()),
        "cost_usd": round(sum(r["cost_usd"] for r in rows), 6), "spent_usd": float(ledger.spent) if ledger else None,
        "latency_ms_median": round(statistics.median(r["latency_ms"] for r in rows), 1) if rows else None,
        "input_tokens": sum(r["input_tokens"] for r in rows), "output_tokens": sum(r["output_tokens"] for r in rows),
        "model_errors": sum(len(r["model_errors"]) for r in rows), "repairs": sum(r["repairs"] for r in rows),
    }


def _sha() -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True, timeout=5).stdout.strip()
    except Exception:  # noqa: BLE001
        return os.getenv("GITHUB_SHA")


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--provider", choices=["anthropic", "openai", "stub"], default="anthropic", help="stub agrees with every decision (free): checks the harness, not the model")
    p.add_argument("--model", default=None)
    p.add_argument("--root", default="var/claims")
    p.add_argument("--split", choices=list(SPLITS), default="all")
    p.add_argument("--mode", choices=list(MODES), default="degraded")
    p.add_argument("--max-cases", type=int, default=None)
    p.add_argument("--max-cost-usd", type=float, required=True, help="hard ceiling for the whole batch (at most 1.0)")
    p.add_argument("--per-call-usd", type=float, default=0.03, help="budget of one audit call, reserved before it starts (at most 0.1)")
    p.add_argument("--output", default="artifacts/claims-audit")
    args = p.parse_args(argv)
    assert 0 < args.max_cost_usd <= 1.0, "the ceiling is at most 1 USD"
    assert 0 < args.per_call_usd <= 0.1
    if args.provider == "stub":
        args.model = args.model or ""
    else:
        args.model = args.model or ("claude-haiku-4-5-20251001" if args.provider == "anthropic" else os.environ.get("FITWITNESS_OPENAI_MODEL", ""))
        assert args.model, "no model id: pass --model or set FITWITNESS_OPENAI_MODEL"
    started = datetime.now(timezone.utc)
    report = evaluate(Path(args.root), provider=args.provider, model=args.model, ceiling=Decimal(str(args.max_cost_usd)), cap=Decimal(str(args.per_call_usd)),
                      split=args.split, mode=args.mode, max_cases=args.max_cases)
    prefix = f"FITWITNESS_{args.provider.upper()}"
    report["protocol"] = {"code_sha": _sha(), "started_at": started.isoformat(), "provider": args.provider, "model": args.model, "split": args.split, "mode": args.mode,
                          "max_cases": args.max_cases, "ceiling_usd": str(args.max_cost_usd), "per_call_cap_usd": str(args.per_call_usd),
                          "pricing_usd_per_million": {"input": os.getenv(f"{prefix}_INPUT_USD_PER_MILLION"), "output": os.getenv(f"{prefix}_OUTPUT_USD_PER_MILLION")},
                          "what_is_audited": "every APPROVE decision of the split in this mode; a wrong payment is one whose outcome or amount differs from the gold"}
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str))
    s = report["summary"]
    print("== claims-audit", json.dumps({k: v for k, v in s.items() if k not in ("by_scenario", "concern_codes_on_caught", "concern_codes_on_false_flags")}, ensure_ascii=False),
          "skipped", len(report["skipped"]), "stopped", report["stopped"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
