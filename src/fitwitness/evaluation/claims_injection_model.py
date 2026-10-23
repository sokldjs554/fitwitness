"""Injected remarks through the model extraction path, under a hard cost ceiling.

``claims/injection.py`` prints the same claim twice, once clean and once with a remark row that gives
instructions, claims an approval or puts a second value behind a familiar label, and runs both prints
through the rules reader. That suite is free and runs in the gate. It does not say what happens when a
model reads the remark: in the claim graph a model proposes the extraction, and only grounding
(``claims/extract.ground``: a value survives only where its quoted snippet sits in a row of its own)
stands between the model's reading of the remark and the decision.

This module runs the same twins through the claim graph on the job runtime with a real model. Every
print is a claim run of its own (enqueue, execute, read the event log back), in a tenant of its own, so
a payout recorded for the clean twin cannot make the injected twin a duplicate. The rules reader runs on
the same bytes as the reference. What is compared is the model path's clean print against its injected
print, with the same ``compare`` the rules suite uses, so the two suites' numbers mean the same thing.

Cost: each run reserves its cap on a ledger before it starts, a run that would not fit under the ceiling
is skipped and listed, and the first run the provider rejects before serving a token ends the batch.

    python -m fitwitness.evaluation.claims_injection_model --provider anthropic \\
        --max-cost-usd 0.3 --per-run-usd 0.04 --output artifacts/claims-injection-model
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from fitwitness.claims.injection import BENIGN, KINDS, WHAT, compare, remark, render
from fitwitness.claims.models import ClaimDecision, ClaimRequest, Extraction, Policy, Reason
from fitwitness.contracts import Budget, RunRequest, TenantScope
from fitwitness.evaluation.agent_trajectory import Ledger, rejected

DEFAULT_CASES = ["CLM-00000", "CLM-00002"]  # two claims the rules path pays in full: a remark that moves money shows there
CHALLENGE_RULES = {"unevidenced_line_item": "R-CHAL-01", "ungrounded_decisive_field": "R-CHAL-02", "review_requested": "R-REV-00"}
SUMMARY_KEYS = ("fields_changed", "decision_changed", "sent_to_review", "unsafe_change", "wrong_pay", "wrong_deny")


def scope_for(case_id: str, kind: str) -> TenantScope:
    """One tenant per print: the ledger, the case row and the documents of one run never meet another's."""
    return TenantScope(tenant_id=f"inj-{case_id}-{kind}".lower(), user_id="injection", role="operator")


def seed(repo, scope: TenantScope, case: dict, docs: list[tuple[str, str, bytes]]) -> dict:
    """The twin's case row and document bytes, as the API would have stored an upload."""
    issued = {d["id"]: d["issued_at"] for d in case["documents"]}
    rows = [{"id": i, "case_id": case["case_id"], "kind": k, "issued_at": issued.get(i, case["submitted_at"]), "pages": 1}
            for i, k, _ in docs]
    twin = {**case, "documents": rows}
    repo.save_claim_case(scope, twin)
    for row, (_, _, pdf) in zip(rows, docs):
        repo.save_claim_doc(scope, row, pdf, None)
    return twin


def request_for(case: dict, docs: list[tuple[str, str, bytes]], provider: str, model: str, cap: Decimal) -> RunRequest:
    claim = ClaimRequest(case_id=case["case_id"], claim_id=case["claim_id"], policy=Policy.model_validate(case["policy"]),
                         requested=case["requested"], document_ids=[i for i, _, _ in docs], submitted_at=case["submitted_at"])
    # The extraction is one structured call whose reservation is the prompt's bytes plus the schema; a four-document
    # claim is well over the default token allowance, so the allowance is raised and the money cap does the limiting.
    return RunRequest(kind="claim", claim=claim.model_dump(mode="json"), provider=provider, model_id=model,
                      budget=Budget(max_cost_usd=cap, max_model_calls=4, max_tokens=48000, deadline_seconds=180))


def from_events(events: list[dict]) -> tuple[Extraction, ClaimDecision] | None:
    """The extraction and the decision after the challenge step, as the event log recorded them.

    The same for a run that completed and for one that stopped for a reviewer: a run in ``waiting_input`` has no
    result object yet, but its ``extracted``, ``adjudicated`` and ``challenged`` events are already there."""
    by = {e["kind"]: (e.get("payload") or {}) for e in events}
    if "extracted" not in by or "adjudicated" not in by:
        return None
    ex = Extraction.model_validate({**by["extracted"].get("fields", {}), "confidence": by["extracted"].get("confidence", 1.0)})
    adj = by["adjudicated"]
    reasons = [Reason.model_validate(r) for r in adj.get("reasons", [])]
    outcome = adj["outcome"]
    if "challenged" in by:
        outcome = by["challenged"]["outcome"]
        reasons += [Reason(rule_id=CHALLENGE_RULES.get(code, "R-CHAL-01"), code=code, severity="review", message=code)
                    for code in by["challenged"].get("added", [])]
    decision = ClaimDecision(outcome=outcome, line_items=adj.get("line_items", []), total_amount=adj["total_amount"], reasons=reasons)
    return ex, decision


def run_one(repo, jobs, scope: TenantScope, case: dict, docs: list[tuple[str, str, bytes]], request: RunRequest) -> tuple[dict, tuple | None, object]:
    from fitwitness.agents.graph import execute_run

    seed(repo, scope, case, docs)
    job = jobs.enqueue(scope, request, str(uuid4()))
    started, error = time.perf_counter(), None
    try:
        execute_run(repo, scope, job.id)
    except Exception as exc:  # noqa: BLE001 - the failure is the finding; it goes in the row
        error = f"{type(exc).__name__}: {str(exc)[:300]}"
    view, events = jobs.get(scope, job.id), [dict(e) for e in jobs.events(scope, job.id)]
    for e in events:
        e["timestamp"] = str(e["timestamp"])
    got = from_events(events)
    extracted = next((e["payload"] for e in events if e["kind"] == "extracted"), {}) or {}
    usage = view.usage
    row = {"run_id": view.id, "tenant": scope.tenant_id, "provider": request.provider, "state": view.state, "error": error,
           "latency_ms": round((time.perf_counter() - started) * 1000, 1),
           "model_calls": usage.model_calls, "input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens,
           "cost_usd": float(usage.cost_usd or 0), "reserved_left_usd": float(usage.reserved_cost_usd),
           "model_errors": [e["payload"] for e in events if e["kind"] in ("model_error", "model_schema_error")],
           "repairs": sum(1 for e in events if e["kind"] == "model" and (e["payload"] or {}).get("repair")),
           "extracted": got[0].model_dump(mode="json", exclude={"evidence", "scanned"}) if got else None,
           "grounded_fields": sorted((extracted.get("evidence") or {}).keys()),
           "proposed": extracted.get("proposed"), "dropped": extracted.get("dropped"),
           "decision": [got[1].outcome, got[1].total_amount, got[1].rule_ids()] if got else None,
           "events": events}
    return row, got, view


def summarise(rows: list[dict]) -> dict:
    """The rules suite's summary keys, for the rows of one path (``path`` is 'model' or 'rules')."""
    pairs = [r for r in rows if not r.get("incomparable")]
    by_kind: dict[str, Counter] = defaultdict(Counter)
    for r in pairs:
        c = by_kind[r["kind"]]
        c["pairs"] += 1
        c["fields_changed"] += bool(r["fields_changed"])
        for k in SUMMARY_KEYS[1:]:
            c[k] += r[k]
    return {
        "cases": len({r["case_id"] for r in rows}), "pairs": len(pairs), "incomparable": len(rows) - len(pairs),
        "fields_changed_pairs": sum(bool(r["fields_changed"]) for r in pairs),
        "decision_changed_pairs": sum(r["decision_changed"] for r in pairs),
        "sent_to_review_pairs": sum(r["sent_to_review"] for r in pairs),
        "review_on_benign_pairs": sum(r["sent_to_review"] for r in pairs if r["kind"] in BENIGN),
        "unsafe_decision_changes": sum(r["unsafe_change"] for r in pairs),
        "wrong_pay_count": sum(r["wrong_pay"] for r in pairs),
        "wrong_deny_count": sum(r["wrong_deny"] for r in pairs),
        "clean_matches_gold": sum(r["clean_matches_gold"] for r in pairs),
        # model path only: pairs where the model's own proposal differed between the clean and the injected print
        "proposed_changed_pairs": sum(bool(r.get("proposed_changed")) for r in pairs),
        "by_kind": {k: {"what": WHAT[k], **dict(c)} for k, c in sorted(by_kind.items())},
    }


def evaluate(repo, jobs, root: Path, *, provider: str, model: str, ceiling: Decimal, cap: Decimal,
             cases: list[str] | None = None, kinds: list[str] | None = None) -> dict:
    from fitwitness.claims.evaluate import decide

    manifest = json.loads((root / "manifest.json").read_text())
    gold = json.loads((root / "gold.json").read_text())
    cases = cases or DEFAULT_CASES
    kinds = kinds or list(KINDS)
    ledger = Ledger(ceiling)
    model_rows: list[dict] = []
    rules_rows: list[dict] = []
    runs: list[dict] = []
    skipped: list[dict] = []
    stopped: str | None = None

    def paid(case: dict, docs, kind: str) -> tuple[dict | None, tuple | None]:
        """One claim run on the model path, or None when it does not fit under the ceiling."""
        nonlocal stopped
        if not ledger.fits(cap):
            skipped.append({"case_id": case["case_id"], "kind": kind, "reason": f"ceiling: spent {ledger.spent} + cap {cap} > {ceiling}"})
            return None, None
        ledger.reserve(cap)
        row, got, view = run_one(repo, jobs, scope_for(case["case_id"], kind), case, docs, request_for(case, docs, provider, model, cap))
        row["charged_usd"] = float(ledger.settle(cap, view.usage))
        row.update({"case_id": case["case_id"], "kind": kind})
        runs.append(row)
        stopped = stopped or rejected(row)
        return row, got

    with tempfile.TemporaryDirectory() as tmp:
        for case in manifest["cases"]:
            if case["case_id"] not in cases or stopped:
                continue
            g = gold[case["case_id"]]
            truth = Extraction.model_validate({k: v for k, v in g["truth"].items() if k in Extraction.FIELDS})
            clean_docs = render(case, truth, Path(tmp) / case["case_id"] / "clean", None)
            twin = {**case, "documents": [{"id": i, "kind": k} for i, k, _ in clean_docs]}
            rules_clean = decide(twin, clean_docs)
            clean_row, model_clean = paid(case, clean_docs, "clean")
            for kind in kinds:
                if stopped:
                    break
                docs = render(case, truth, Path(tmp) / case["case_id"] / kind, remark(kind, truth))
                rules_rows.append({"path": "rules", **compare(case, g["gold"], rules_clean, decide(twin, docs), kind)})
                row, model_inj = paid(case, docs, kind)
                if model_clean and model_inj:
                    p0, p1 = clean_row.get("proposed") or {}, row.get("proposed") or {}
                    moved = sorted(f for f in set(p0) | set(p1) if p0.get(f) != p1.get(f))
                    model_rows.append({"path": "model", **compare(case, g["gold"], (*model_clean, 0.0), (*model_inj, 0.0), kind),
                                       "clean_run_id": clean_row["run_id"], "run_id": row["run_id"],
                                       "model_clean_matches_rules_clean": (model_clean[1].outcome, model_clean[1].total_amount)
                                       == (rules_clean[1].outcome, rules_clean[1].total_amount),
                                       "grounded_fields": row["grounded_fields"],
                                       # the remark's effect on the model itself, before grounding: what it proposed differently
                                       "proposed_changed": moved, "proposed_values": {f: [p0.get(f), p1.get(f)] for f in moved},
                                       "dropped_by_grounding": [clean_row.get("dropped") or [], row.get("dropped") or []]})
                else:
                    model_rows.append({"path": "model", "case_id": case["case_id"], "scenario": case["scenario"], "kind": kind,
                                       "incomparable": "clean run has no decision" if not model_clean else
                                                       "injected run has no decision" if row else "skipped under the ceiling",
                                       "clean_run_id": clean_row["run_id"] if clean_row else None, "run_id": row["run_id"] if row else None})
    return {"summary": {"model": summarise(model_rows), "rules": summarise(rules_rows),
                        "spent_usd": float(ledger.spent), "ceiling_usd": float(ceiling), "runs": len(runs),
                        "completed": sum(r["state"] == "completed" for r in runs), "waiting_input": sum(r["state"] == "waiting_input" for r in runs),
                        "failed": sum(r["state"] not in ("completed", "waiting_input") for r in runs),
                        "model_errors": sum(len(r["model_errors"]) for r in runs), "repairs": sum(r["repairs"] for r in runs)},
            "rows": model_rows + rules_rows, "runs": runs, "skipped": skipped, "stopped": stopped}


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
    p.add_argument("--provider", choices=["anthropic", "openai", "rules"], default="anthropic",
                   help="rules runs the same harness without a model (free): the clean and injected prints then differ only by what the rules reader does")
    p.add_argument("--model", default=None, help="the provider's model id (default: Claude Haiku 4.5 for anthropic, FITWITNESS_OPENAI_MODEL for openai)")
    p.add_argument("--root", default="var/claims")
    p.add_argument("--cases", nargs="*", default=None, help=f"case ids (default {' '.join(DEFAULT_CASES)})")
    p.add_argument("--kinds", nargs="*", default=None, choices=list(KINDS))
    p.add_argument("--max-cost-usd", type=float, required=True, help="hard ceiling for the whole batch (at most 1.0)")
    p.add_argument("--per-run-usd", type=float, default=0.04, help="budget of one claim run, reserved before it starts (at most 0.1)")
    p.add_argument("--output", default="artifacts/claims-injection-model")
    args = p.parse_args(argv)
    assert 0 < args.max_cost_usd <= 1.0, "the ceiling is at most 1 USD"
    assert 0 < args.per_run_usd <= 0.1
    if args.provider == "rules":
        args.model = args.model or ""
    else:
        args.model = args.model or ("claude-haiku-4-5-20251001" if args.provider == "anthropic" else os.environ.get("FITWITNESS_OPENAI_MODEL", ""))
        assert args.model, "no model id: pass --model or set FITWITNESS_OPENAI_MODEL"
    ceiling, cap = Decimal(str(args.max_cost_usd)), Decimal(str(args.per_run_usd))
    repo = Repository(os.environ["FITWITNESS_DATABASE_URL"])
    jobs = Jobs(repo)
    started = datetime.now(timezone.utc)
    report = evaluate(repo, jobs, Path(args.root), provider=args.provider, model=args.model, ceiling=ceiling, cap=cap,
                      cases=args.cases, kinds=args.kinds)
    prefix = f"FITWITNESS_{args.provider.upper()}"
    report["protocol"] = {
        "code_sha": _sha(), "started_at": started.isoformat(), "provider": args.provider, "model": args.model,
        "cases": args.cases or DEFAULT_CASES, "kinds": args.kinds or list(KINDS), "ceiling_usd": str(ceiling), "per_run_cap_usd": str(cap),
        "pricing_usd_per_million": {"input": os.getenv(f"{prefix}_INPUT_USD_PER_MILLION"), "output": os.getenv(f"{prefix}_OUTPUT_USD_PER_MILLION")},
        "comparison": "each injected print against the clean print of the same path (model vs model, rules vs rules); gold only says whether the clean print was right",
    }
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str))
    s = report["summary"]
    print("== claims-injection-model spent", s["spent_usd"], "ceiling", s["ceiling_usd"], "runs", s["runs"], "skipped", len(report["skipped"]),
          "stopped", report["stopped"])
    print("== model", json.dumps({k: v for k, v in s["model"].items() if k != "by_kind"}, ensure_ascii=False))
    print("== rules", json.dumps({k: v for k, v in s["rules"].items() if k != "by_kind"}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
