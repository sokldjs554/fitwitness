"""The claim graph with a real model reading the documents, on the synthetic claims, beside the rules reader.

The claims evaluation (``claims/evaluate.py``) runs without a model and is what the gate checks on every commit.
The operator path is different: a model proposes the extraction and ``claims/extract.ground`` keeps only what it can
find in a row of the document; the rules after that are the same. Until 2026-10-23 that path had never run end
to end (the first paid batch of the injection harness found two bugs in it). This module measures it the way the
rules reader is measured: every claim of a split goes through the job runtime with the model, the rules reader
reads the same bytes, and both are scored against the generator's gold with the same rules, field by field and
decision by decision. ``--scan`` turns the documents into page pictures first, so the model reads OCR text and the
rules reader its own OCR pairs: the same comparison on the harder input.

Cost: one tenant per claim, each run reserves its cap on a ledger under a hard ceiling, a run that would not fit is
skipped and listed, and the first run the provider rejects before serving a token ends the batch.

    python -m fitwitness.evaluation.claims_model --provider anthropic --split test \\
        --max-cost-usd 0.6 --per-run-usd 0.04 --output artifacts/claims-model
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

from fitwitness.claims.evaluate import _same, decide
from fitwitness.claims.models import Extraction
from fitwitness.evaluation.agent_trajectory import Ledger, rejected
from fitwitness.evaluation.claims_injection_model import request_for, run_one, scope_for

SPLITS = {"test": lambda n: n % 2 == 1, "dev": lambda n: n % 2 == 0, "all": lambda n: True}


def load_docs(root: Path, case: dict, scan: str | None) -> list[tuple[str, str, bytes]]:
    docs = [(d["id"], d["kind"], (root / case["case_id"] / f"{d['id']}.pdf").read_bytes()) for d in case["documents"]]
    if scan:
        from fitwitness.claims.scan import scan_pdf

        docs = [(i, k, scan_pdf(data, scan, i)) for i, k, data in docs]
    return docs


def score(case: dict, g: dict, ex: Extraction | None, outcome: str | None, amount: int | None, scan: str | None) -> dict:
    """The claims evaluation's scoring, for one reader's answer; a reader that gave no answer scores nothing right."""
    gold = g["gold"]
    hits = (
        {
            f: _same(f, getattr(ex, f), g["truth"].get(f), scan) or (f == "discharge_date" and case["scenario"] == "date_conflict")
            for f in g["visible_fields"]
        }
        if ex is not None
        else {f: False for f in g["visible_fields"]}
    )
    return {
        "predicted": outcome,
        "amount": amount,
        "hits": hits,
        "correct": outcome == gold["outcome"] and amount == gold["total_amount"],
        "wrong_pay": outcome == "APPROVE" and (gold["outcome"] != "APPROVE" or amount != gold["total_amount"]),
        "wrong_deny": outcome == "DENY" and gold["outcome"] != "DENY",
        "auto_paid": outcome == "APPROVE" and gold["outcome"] == "APPROVE" and amount == gold["total_amount"],
    }


def summarise(rows: list[dict], path: str) -> dict:
    """One reader's numbers over the rows, with the claims evaluation's definitions."""
    n = len(rows)
    if not n:
        return {"cases": 0}
    gold_approve = sum(1 for r in rows if r["gold"] == "APPROVE")
    field_hit: Counter = Counter()
    field_total: Counter = Counter()
    for r in rows:
        for f, hit in r[path]["hits"].items():
            field_total[f] += 1
            field_hit[f] += bool(hit)
    by_scenario: dict[str, Counter] = defaultdict(Counter)
    for r in rows:
        c = by_scenario[r["scenario"]]
        c["cases"] += 1
        c["correct"] += r[path]["correct"]
        c["wrong_pay"] += r[path]["wrong_pay"]
        c["review"] += r[path]["predicted"] == "REVIEW"
    out = {
        "cases": n,
        "decision_accuracy": round(sum(r[path]["correct"] for r in rows) / n, 4),
        "wrong_pay_count": sum(r[path]["wrong_pay"] for r in rows),
        "wrong_deny_count": sum(r[path]["wrong_deny"] for r in rows),
        "review_rate": round(sum(r[path]["predicted"] == "REVIEW" for r in rows) / n, 4),
        "auto_rate": round(sum(r[path]["auto_paid"] for r in rows) / gold_approve, 4) if gold_approve else None,
        "field_accuracy": round(sum(field_hit.values()) / max(1, sum(field_total.values())), 4),
        "field_accuracy_by_field": {f: round(field_hit[f] / field_total[f], 4) for f in sorted(field_total)},
        "by_scenario": {s: dict(c) for s, c in sorted(by_scenario.items())},
    }
    if path == "model":
        runs = [r["run"] for r in rows]
        done = [u for u in runs if u["state"] in ("completed", "waiting_input")]
        out.update(
            {
                "completed_or_waiting": len(done),
                "failed": n - len(done),
                "ungrounded_reviews": sum("R-CHAL-02" in (r["model"].get("rules") or []) for r in rows),
                "fields_dropped_by_grounding": sum(len(u.get("dropped") or []) for u in runs),
                "model_errors": sum(len(u["model_errors"]) for u in runs),
                "repairs": sum(u["repairs"] for u in runs),
                "cost_usd": round(sum(u["cost_usd"] for u in runs), 6),
                "latency_ms_median": round(statistics.median(u["latency_ms"] for u in runs), 1),
                "input_tokens": sum(u["input_tokens"] for u in runs),
                "output_tokens": sum(u["output_tokens"] for u in runs),
            }
        )
    return out


def evaluate(
    repo,
    jobs,
    root: Path,
    *,
    provider: str,
    model: str,
    ceiling: Decimal,
    cap: Decimal,
    split: str = "test",
    scan: str | None = None,
    max_cases: int | None = None,
) -> dict:
    manifest = json.loads((root / "manifest.json").read_text())
    gold = json.loads((root / "gold.json").read_text())
    chosen = [c for c in manifest["cases"] if SPLITS[split](int(c["case_id"].split("-")[-1]))][: max_cases or None]
    ledger = Ledger(ceiling)
    rows: list[dict] = []
    skipped: list[dict] = []
    stopped: str | None = None
    for case in chosen:
        g = gold[case["case_id"]]
        docs = load_docs(root, case, scan)
        started = time.perf_counter()
        r_ex, r_dec, _ = decide(case, docs)
        rules = {
            **score(case, g, r_ex, r_dec.outcome, r_dec.total_amount, scan),
            "rules": r_dec.rule_ids(),
            "confidence": r_ex.confidence,
            "latency_ms": round((time.perf_counter() - started) * 1000, 1),
        }
        if stopped or not ledger.fits(cap):
            skipped.append({"case_id": case["case_id"], "reason": stopped or f"ceiling: spent {ledger.spent} + cap {cap} > {ceiling}"})
            continue
        scope = scope_for(case["case_id"], f"model-{scan or 'text'}")
        if case["prior_paid_keys"]:
            repo.seed_paid_keys(scope, case["policy"]["policy_id"], case["prior_paid_keys"])
        ledger.reserve(cap)
        run, got, view = run_one(repo, jobs, scope, case, docs, request_for(case, docs, provider, model, cap))
        run["charged_usd"] = float(ledger.settle(cap, view.usage))
        stopped = stopped or rejected(run)
        m_ex, m_dec = got if got else (None, None)
        model_row = {
            **score(case, g, m_ex, m_dec.outcome if m_dec else None, m_dec.total_amount if m_dec else None, scan),
            "rules": m_dec.rule_ids() if m_dec else [],
            "confidence": m_ex.confidence if m_ex else None,
        }
        rows.append(
            {
                "case_id": case["case_id"],
                "scenario": case["scenario"],
                "gold": g["gold"]["outcome"],
                "gold_amount": g["gold"]["total_amount"],
                "rules": rules,
                "model": model_row,
                "agree": (rules["predicted"], rules["amount"]) == (model_row["predicted"], model_row["amount"]),
                "run": {
                    k: run[k]
                    for k in (
                        "run_id",
                        "state",
                        "error",
                        "latency_ms",
                        "model_calls",
                        "input_tokens",
                        "output_tokens",
                        "cost_usd",
                        "charged_usd",
                        "model_errors",
                        "repairs",
                        "proposed",
                        "dropped",
                        "grounded_fields",
                    )
                },
            }
        )
    disagreements = [
        {
            "case_id": r["case_id"],
            "scenario": r["scenario"],
            "gold": [r["gold"], r["gold_amount"]],
            "rules": [r["rules"]["predicted"], r["rules"]["amount"]],
            "model": [r["model"]["predicted"], r["model"]["amount"]],
            "model_rules_added": sorted(set(r["model"]["rules"]) - set(r["rules"]["rules"])),
            "proposed": r["run"]["proposed"],
            "dropped": r["run"]["dropped"],
            "error": r["run"]["error"],
        }
        for r in rows
        if not r["agree"]
    ]
    return {
        "summary": {
            "model": summarise(rows, "model"),
            "rules": summarise(rows, "rules"),
            "agreement": round(sum(r["agree"] for r in rows) / len(rows), 4) if rows else None,
            "spent_usd": float(ledger.spent),
            "ceiling_usd": float(ceiling),
            "skipped": len(skipped),
        },
        "disagreements": disagreements,
        "rows": rows,
        "skipped": skipped,
        "stopped": stopped,
    }


def _sha() -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True, timeout=5).stdout.strip()
    except Exception:  # noqa: BLE001
        return os.getenv("GITHUB_SHA")


def main(argv: list[str] | None = None) -> int:
    import argparse

    from fitwitness.runtime.jobs import Jobs
    from fitwitness.storage.repository import Repository

    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument(
        "--provider",
        choices=["anthropic", "openai", "rules"],
        default="anthropic",
        help="rules runs the same harness without a model (free): both paths then read with the rules reader",
    )
    p.add_argument("--model", default=None)
    p.add_argument("--root", default="var/claims")
    p.add_argument("--split", choices=list(SPLITS), default="test")
    p.add_argument(
        "--scan",
        choices=["none", "clean", "light", "medium", "heavy"],
        default="none",
        help="page pictures at this damage level instead of the text layer",
    )
    p.add_argument("--max-cases", type=int, default=None)
    p.add_argument("--max-cost-usd", type=float, required=True, help="hard ceiling for the whole batch (at most 1.0)")
    p.add_argument("--per-run-usd", type=float, default=0.04, help="budget of one claim run, reserved before it starts (at most 0.1)")
    p.add_argument("--output", default="artifacts/claims-model")
    args = p.parse_args(argv)
    assert 0 < args.max_cost_usd <= 1.0, "the ceiling is at most 1 USD"
    assert 0 < args.per_run_usd <= 0.1
    if args.provider == "rules":
        args.model = args.model or ""
    else:
        args.model = args.model or (
            "claude-haiku-4-5-20251001" if args.provider == "anthropic" else os.environ.get("FITWITNESS_OPENAI_MODEL", "")
        )
        assert args.model, "no model id: pass --model or set FITWITNESS_OPENAI_MODEL"
    scan = None if args.scan == "none" else args.scan
    repo = Repository(os.environ["FITWITNESS_DATABASE_URL"])
    started = datetime.now(timezone.utc)
    report = evaluate(
        repo,
        Jobs(repo),
        Path(args.root),
        provider=args.provider,
        model=args.model,
        ceiling=Decimal(str(args.max_cost_usd)),
        cap=Decimal(str(args.per_run_usd)),
        split=args.split,
        scan=scan,
        max_cases=args.max_cases,
    )
    prefix = f"FITWITNESS_{args.provider.upper()}"
    report["protocol"] = {
        "code_sha": _sha(),
        "started_at": started.isoformat(),
        "provider": args.provider,
        "model": args.model,
        "split": args.split,
        "scan": scan,
        "max_cases": args.max_cases,
        "ceiling_usd": str(args.max_cost_usd),
        "per_run_cap_usd": str(args.per_run_usd),
        "pricing_usd_per_million": {
            "input": os.getenv(f"{prefix}_INPUT_USD_PER_MILLION"),
            "output": os.getenv(f"{prefix}_OUTPUT_USD_PER_MILLION"),
        },
        "scoring": "claims/evaluate.py rules against the generator's gold; the rules reader reads the same bytes as the model run",
    }
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str))
    s = report["summary"]
    print(
        "== claims-model spent",
        s["spent_usd"],
        "ceiling",
        s["ceiling_usd"],
        "cases",
        s["model"].get("cases"),
        "skipped",
        s["skipped"],
        "stopped",
        report["stopped"],
    )
    for path in ("model", "rules"):
        print(
            f"== {path}",
            json.dumps({k: v for k, v in s[path].items() if k not in ("by_scenario", "field_accuracy_by_field")}, ensure_ascii=False),
        )
    print("== agreement", s["agreement"], "disagreements", len(report["disagreements"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
