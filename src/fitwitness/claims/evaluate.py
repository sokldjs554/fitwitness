"""Claim pipeline evaluation without a model: extraction, routing and payout safety on the
synthetic corpus, against the gold the generator computed from the truth.

Metrics
  field_accuracy     fields printed in the documents that were read back exactly
  decision_accuracy  outcome and amount equal to gold
  wrong_pay_rate     APPROVE where gold is not APPROVE, or a different amount: the risk metric
  wrong_deny_rate    DENY where gold is not DENY: a claimant turned away on a misreading
  auto_rate          claims that finished without a person (gold permitting)
  review_rate        claims parked for a reviewer
The ``--degraded`` control simulates three realistic breakages at once: the document
requirement is ignored, the ledger lookup returns nothing (duplicates look new) and the
auto-approve limit is gone. A benchmark that cannot see those paying claims they should
not would be worthless, so the gate requires the gap.

``scan="medium"`` (or light/heavy/clean) first turns every document into a page picture with
``claims/scan.py`` and reads it with OCR. That exercises the part the text-layer corpus cannot:
a value read wrongly, a confidence that falls, and the routing that must send such a claim to
a person rather than pay on it. Free-text names are compared without spaces in this mode,
because Korean word spacing is the one thing the scanner cannot be asked to preserve.
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


NAME_FIELDS = {"insured_name", "hospital", "diagnosis_name", "surgery_name"}


def run_pipeline(root: Path, case: dict, *, degraded: bool = False, scan: str | None = None) -> tuple[Extraction, ClaimDecision, float]:
    docs = [(d["id"], d["kind"], (root / case["case_id"] / f"{d['id']}.pdf").read_bytes()) for d in case["documents"]]
    if scan:
        from fitwitness.claims.scan import scan_pdf

        docs = [(i, k, scan_pdf(data, scan, i)) for i, k, data in docs]
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


def _same(field: str, got, want, scan: str | None) -> bool:
    if scan and field in NAME_FIELDS and isinstance(got, str) and isinstance(want, str):
        return "".join(got.split()) == "".join(want.split())
    return got == want


def _score_case(args: tuple) -> dict:
    root, case, g, degraded, scan = args
    ex, decision, ms = run_pipeline(root, case, degraded=degraded, scan=scan)
    hits = {f: _same(f, getattr(ex, f), g["truth"].get(f), scan) or (f == "discharge_date" and case["scenario"] == "date_conflict")
            for f in g["visible_fields"]}
    correct = decision.outcome == g["gold"]["outcome"] and decision.total_amount == g["gold"]["total_amount"]
    wrong_pay = decision.outcome == "APPROVE" and (g["gold"]["outcome"] != "APPROVE" or decision.total_amount != g["gold"]["total_amount"])
    wrong_deny = decision.outcome == "DENY" and g["gold"]["outcome"] != "DENY"  # turned a claimant away who was owed money or a look
    return {"case_id": case["case_id"], "scenario": case["scenario"], "predicted": decision.outcome, "gold": g["gold"]["outcome"],
            "amount": decision.total_amount, "gold_amount": g["gold"]["total_amount"], "correct": correct, "wrong_pay": wrong_pay, "wrong_deny": wrong_deny,
            "confidence": ex.confidence, "rules": decision.rule_ids(), "ms": ms, "hits": hits}


def evaluate(root: Path, *, degraded: bool = False, scan: str | None = None, workers: int = 1, cases: list[str] | None = None) -> dict:
    manifest = json.loads((root / "manifest.json").read_text())
    gold = json.loads((root / "gold.json").read_text())
    chosen = [c for c in manifest["cases"] if cases is None or c["case_id"] in cases]
    jobs = [(root, c, gold[c["case_id"]], degraded, scan) for c in chosen]
    if workers > 1:
        from concurrent.futures import ProcessPoolExecutor

        with ProcessPoolExecutor(max_workers=workers) as pool:
            scored = list(pool.map(_score_case, jobs, chunksize=1))
    else:
        scored = [_score_case(j) for j in jobs]
    rows, latencies = [], []
    field_hit, field_total = Counter(), Counter()
    per_scenario: dict[str, Counter] = defaultdict(Counter)
    for r in scored:
        latencies.append(r["ms"])
        for f, hit in r["hits"].items():
            field_total[f] += 1
            field_hit[f] += hit
        c = per_scenario[r["scenario"]]
        c["n"] += 1
        c["correct"] += r["correct"]
        c["wrong_pay"] += r["wrong_pay"]
        c["review"] += r["predicted"] == "REVIEW"
        c["gold_approve"] += r["gold"] == "APPROVE"
        c["auto_approved"] += r["predicted"] == "APPROVE" and r["correct"]
        rows.append({k: v for k, v in r.items() if k not in ("ms", "hits")})
    n = len(rows)
    gold_approve = sum(1 for r in rows if r["gold"] == "APPROVE")
    summary = {
        "cases": n,
        "field_accuracy": round(sum(field_hit.values()) / max(1, sum(field_total.values())), 4),
        "field_accuracy_by_field": {f: round(field_hit[f] / field_total[f], 4) for f in sorted(field_total)},
        "decision_accuracy": round(sum(r["correct"] for r in rows) / n, 4),
        "wrong_pay_rate": round(sum(r["wrong_pay"] for r in rows) / n, 4),
        "wrong_pay_count": sum(r["wrong_pay"] for r in rows),
        "wrong_deny_count": sum(r["wrong_deny"] for r in rows),
        "wrong_deny_rate": round(sum(r["wrong_deny"] for r in rows) / n, 4),
        "review_rate": round(sum(r["predicted"] == "REVIEW" for r in rows) / n, 4),
        "auto_rate": round(sum(r["predicted"] == "APPROVE" and r["correct"] for r in rows) / max(1, gold_approve), 4),
        "latency_ms_p50": round(statistics.median(latencies), 1),
        "by_scenario": {s: {"n": c["n"], "decision_accuracy": round(c["correct"] / c["n"], 3), "wrong_pay": c["wrong_pay"],
                            "review": c["review"]} for s, c in sorted(per_scenario.items())},
        "degraded": degraded,
        "scan": scan,
    }
    return {"summary": summary, "rows": rows}


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="var/claims")
    ap.add_argument("--output", default="artifacts/claims-eval.json")
    ap.add_argument("--degraded", action="store_true")
    ap.add_argument("--scan", choices=["clean", "light", "medium", "heavy"], help="read every document as a scanned page picture through OCR")
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--split", choices=["all", "dev", "test"], default="all",
                    help="dev = even-numbered cases, test = odd-numbered ones (the confidence floor is chosen on dev and checked on test)")
    a = ap.parse_args()
    ids = [c["case_id"] for c in json.loads((Path(a.root) / "manifest.json").read_text())["cases"]]
    chosen = None if a.split == "all" else [c for i, c in enumerate(ids) if (i % 2 == 0) == (a.split == "dev")]
    result = evaluate(Path(a.root), degraded=a.degraded, scan=a.scan, workers=a.workers, cases=chosen)
    Path(a.output).parent.mkdir(parents=True, exist_ok=True)
    Path(a.output).write_text(json.dumps(result, ensure_ascii=False, indent=1))
    print(json.dumps(result["summary"], ensure_ascii=False, indent=1))
