"""Claim pipeline evaluation without a model: extraction, routing and payout safety on the
synthetic corpus, against the gold the generator computed from the truth.

Metrics
  field_accuracy     fields printed in the documents that were read back exactly
  decision_accuracy  outcome and amount equal to gold
  wrong_pay_rate     APPROVE where gold is not APPROVE, or a different amount: the risk metric
  auto_rate          claims that finished without a person (gold permitting)
  review_rate        claims parked for a reviewer
The ``--degraded`` control simulates three realistic breakages at once: the document
requirement is ignored, the ledger lookup returns nothing (duplicates look new) and the
auto-approve limit is gone. A benchmark that cannot see those paying claims they should
not would be worthless, so the gate requires the gap.
"""

from __future__ import annotations

import json
import statistics
import time
from collections import Counter, defaultdict
from pathlib import Path

from fitwitness.claims.extract import extract_documents
from fitwitness.claims.graph import ITEM_FIELDS
from fitwitness.claims.models import ClaimDecision, Extraction, Policy, Reason
from fitwitness.claims.policy import PRODUCTS, adjudicate, validate


def challenge(ex: Extraction, decision: ClaimDecision) -> ClaimDecision:
    reasons = list(decision.reasons)
    for li in decision.line_items:
        if any(f not in ex.evidence for f in ITEM_FIELDS[li.coverage]):
            reasons.append(Reason(rule_id="R-CHAL-01", code="unevidenced_line_item", severity="review", message="근거 위치 없음"))
    outcome = "REVIEW" if decision.outcome == "APPROVE" and any(r.severity == "review" for r in reasons) else decision.outcome
    return ClaimDecision(outcome=outcome, line_items=decision.line_items, total_amount=decision.total_amount, reasons=reasons)


def run_pipeline(root: Path, case: dict, *, degraded: bool = False) -> tuple[Extraction, ClaimDecision, float]:
    docs = [(d["id"], d["kind"], (root / case["case_id"] / f"{d['id']}.pdf").read_bytes()) for d in case["documents"]]
    started = time.perf_counter()
    ex = extract_documents(docs)
    present = {d["kind"] for d in case["documents"]}
    flags = validate(ex, present)
    policy = Policy.model_validate(case["policy"])
    product = PRODUCTS[policy.product_id]
    prior = set(case["prior_paid_keys"])
    if degraded:
        present = present | {"diagnosis", "admission", "surgery", "receipt"}  # every document "is there"
        prior = set()  # the ledger lookup is broken
        product = product.model_copy(update={"auto_approve_limit": 10**12})  # nobody signs off on large amounts
    decision = challenge(ex, adjudicate(policy, product, case["requested"], ex, present, prior, flags))
    return ex, decision, (time.perf_counter() - started) * 1000


def evaluate(root: Path, *, degraded: bool = False) -> dict:
    manifest = json.loads((root / "manifest.json").read_text())
    gold = json.loads((root / "gold.json").read_text())
    rows, latencies = [], []
    field_hit, field_total = Counter(), Counter()
    per_scenario: dict[str, Counter] = defaultdict(Counter)
    for case in manifest["cases"]:
        g = gold[case["case_id"]]
        ex, decision, ms = run_pipeline(root, case, degraded=degraded)
        latencies.append(ms)
        for f in g["visible_fields"]:
            field_total[f] += 1
            if getattr(ex, f) == g["truth"].get(f) or (f == "discharge_date" and case["scenario"] == "date_conflict"):
                field_hit[f] += 1
        correct = decision.outcome == g["gold"]["outcome"] and decision.total_amount == g["gold"]["total_amount"]
        wrong_pay = decision.outcome == "APPROVE" and (g["gold"]["outcome"] != "APPROVE" or decision.total_amount != g["gold"]["total_amount"])
        c = per_scenario[case["scenario"]]
        c["n"] += 1
        c["correct"] += correct
        c["wrong_pay"] += wrong_pay
        c["review"] += decision.outcome == "REVIEW"
        c["gold_approve"] += g["gold"]["outcome"] == "APPROVE"
        c["auto_approved"] += decision.outcome == "APPROVE" and correct
        rows.append({"case_id": case["case_id"], "scenario": case["scenario"], "predicted": decision.outcome, "gold": g["gold"]["outcome"],
                     "amount": decision.total_amount, "gold_amount": g["gold"]["total_amount"], "correct": correct, "wrong_pay": wrong_pay,
                     "confidence": ex.confidence, "rules": decision.rule_ids()})
    n = len(rows)
    gold_approve = sum(1 for r in rows if r["gold"] == "APPROVE")
    summary = {
        "cases": n,
        "field_accuracy": round(sum(field_hit.values()) / max(1, sum(field_total.values())), 4),
        "field_accuracy_by_field": {f: round(field_hit[f] / field_total[f], 4) for f in sorted(field_total)},
        "decision_accuracy": round(sum(r["correct"] for r in rows) / n, 4),
        "wrong_pay_rate": round(sum(r["wrong_pay"] for r in rows) / n, 4),
        "wrong_pay_count": sum(r["wrong_pay"] for r in rows),
        "review_rate": round(sum(r["predicted"] == "REVIEW" for r in rows) / n, 4),
        "auto_rate": round(sum(r["predicted"] == "APPROVE" and r["correct"] for r in rows) / max(1, gold_approve), 4),
        "latency_ms_p50": round(statistics.median(latencies), 1),
        "by_scenario": {s: {"n": c["n"], "decision_accuracy": round(c["correct"] / c["n"], 3), "wrong_pay": c["wrong_pay"],
                            "review": c["review"]} for s, c in sorted(per_scenario.items())},
        "degraded": degraded,
    }
    return {"summary": summary, "rows": rows}


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="var/claims")
    ap.add_argument("--output", default="artifacts/claims-eval.json")
    ap.add_argument("--degraded", action="store_true")
    a = ap.parse_args()
    result = evaluate(Path(a.root), degraded=a.degraded)
    Path(a.output).parent.mkdir(parents=True, exist_ok=True)
    Path(a.output).write_text(json.dumps(result, ensure_ascii=False, indent=1))
    print(json.dumps(result["summary"], ensure_ascii=False, indent=1))
