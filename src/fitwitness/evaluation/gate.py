"""No-API evaluation gate: the parts of the system that can be measured on every commit.

Runs against real PostgreSQL but needs no model weights and no provider key, so CI can
block a regression before a paid experiment is ever scheduled. Four suites:

* ``rules_graph``     the deterministic LangGraph verdicts on family FW-F000 vs generator gold
* ``lexical``         exact + BM25 retrieval recall@5 per query category on the test split
* ``geometry``        STEP feature distance: does a family's base model find its own family?
* ``control``         the same lexical suite with identifier normalization switched off; it
                      must score lower on identifier variants, or the benchmark has lost its teeth

The report is compared with a committed baseline using absolute floors and a maximum
allowed drop; ``--write-baseline`` refreshes the baseline deliberately.

    python -m fitwitness.evaluation.gate --output artifacts/gate --baseline docs/evaluation/gate-baseline.json
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from uuid import uuid4

from fitwitness.contracts import Budget, DrawingRevision, RunRequest, SearchRequest, TenantScope
from fitwitness.evaluation.retrieval import make_cases, score_ranking

GATE_VERSION = "gate-v1"

# metric path -> rule. "min": absolute floor; "max_drop": allowed decrease vs. baseline; "equals": exact value.
THRESHOLDS: dict[str, dict] = {
    "rules_graph.accuracy": {"equals": 1.0},
    "lexical.exact.recall_at_5": {"min": 0.99},
    "lexical.id_variant.recall_at_5": {"min": 0.95, "max_drop": 0.0},
    "lexical.id_typo.recall_at_5": {"min": 0.80, "max_drop": 0.05},
    "lexical.paraphrase.recall_at_5": {"max_drop": 0.10},
    "geometry.same_family_at_3": {"min": 0.5, "max_drop": 0.10},
    "control.id_variant_gap": {"min": 0.3},
    "control.id_typo_gap": {"min": 0.3},
}


def _git_sha() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except Exception:  # noqa: BLE001 - not inside a checkout
        return os.getenv("GITHUB_SHA", "unknown")


def run_rules_graph(repo, root: Path) -> dict:
    """Execute the real graph with the rules provider on FW-F000 and score against gold."""
    from fitwitness.agents.graph import execute_run
    from fitwitness.ingest.pdf import extract_pdf
    from fitwitness.runtime.jobs import Jobs

    manifest = json.loads((root / "manifest.json").read_text())
    gold = json.loads((root / "gold/labels.json").read_text())
    scope = TenantScope(tenant_id=f"gate-rules-{uuid4()}", user_id="gate", role="operator")
    entries = [e for e in manifest["document_entries"] if e["family_id"] == "FW-F000" and not e["is_revision_update"]]
    expected = {}
    for e in entries:
        rev = DrawingRevision(tenant_id=scope.tenant_id, **{k: e[k] for k in ("id", "document_id", "drawing_number", "family_id", "revision_label", "supersedes", "kind", "title", "source_hash")})
        data = (root / e["pdf"]).read_bytes()
        repo.add_revision(scope, rev, data)
        repo.save_facts(scope, rev.id, extract_pdf(data, rev))
        truth = gold[rev.id]
        expected[rev.id] = "mismatch" if truth["hole_spacing"] != 40 or truth["material"] not in ("SUS304", None) else "unknown" if truth["material"] is None else "match"
    jobs = Jobs(repo)
    request = RunRequest(search=SearchRequest(text="브래킷 구멍 간격 40mm SUS304"), provider="rules", budget=Budget(max_tokens=64000))
    job = jobs.enqueue(scope, request, str(uuid4()))
    started = time.perf_counter()
    execute_run(repo, scope, job.id)
    view = jobs.get(scope, job.id)
    actual = {d.revision_id: d.verdict.value for d in view.decisions}
    correct = sum(actual.get(k) == v for k, v in expected.items())
    return {"state": view.state, "correct": correct, "total": len(expected), "accuracy": correct / len(expected),
            "latency_ms": (time.perf_counter() - started) * 1000, "expected": expected, "actual": actual}


def run_lexical(repo, scope, root: Path, *, id_matching: str) -> dict:
    """exact+BM25 recall@5 per category over the frozen test-split cases (no encoders)."""
    from fitwitness.retrieval.pipeline import search

    manifest = json.loads((root / "manifest.json").read_text())
    gold = json.loads((root / "gold/labels.json").read_text())
    cases = [c for c in make_cases(manifest, gold) if c["category"] in ("exact", "id_variant", "id_typo", "paraphrase")]
    snapshot = repo.snapshot(scope)
    rows = []
    for case in cases:
        started = time.perf_counter()
        ranked = search(scope, SearchRequest(text=case["text"], top_k=10), snapshot, repo, None, {"exact", "bm25"}, id_matching=id_matching)
        ids = [c.revision_id for c in ranked]
        rows.append({"case_id": case["id"], "category": case["category"], "text": case["text"], "latency_ms": (time.perf_counter() - started) * 1000,
                     "ranked": ids[:5], **{k + "_at_5": v for k, v in score_ranking(ids, case["relevance"], 5).items()}})
    by_cat: dict[str, list] = defaultdict(list)
    for r in rows:
        by_cat[r["category"]].append(r)
    out = {cat: {"n": len(rs), "recall_at_5": mean(r["recall_at_5"] for r in rs), "ndcg_at_5": mean(r["ndcg_at_5"] for r in rs),
                 "latency_p50_ms": sorted(r["latency_ms"] for r in rs)[len(rs) // 2]} for cat, rs in sorted(by_cat.items())}
    out["rows"] = rows
    return out


def run_geometry(root: Path, families: list[str]) -> dict:
    """For each family's base STEP, rank every other STEP in the pool by feature distance."""
    from fitwitness.ingest.cad import extract_step
    from fitwitness.retrieval.geometry import rank_geometry

    manifest = json.loads((root / "manifest.json").read_text())
    pool = [d for d in manifest["document_entries"] if d["family_id"] in families and not d["is_revision_update"]]
    features = {}
    parse_ms = []
    for d in pool:
        started = time.perf_counter()
        features[d["id"]] = extract_step((root / d["step"]).read_bytes(), include_mesh=False)
        parse_ms.append((time.perf_counter() - started) * 1000)
    rows = []
    for d in pool:
        if not d["drawing_number"].endswith("-0"):
            continue
        candidates = {k: v for k, v in features.items() if k != d["id"]}
        ranked = rank_geometry(features[d["id"]], candidates)
        fam = {x["id"]: x["family_id"] for x in pool}
        top = [fam[r["candidate_id"]] for r in ranked[:3]]
        rows.append({"query": d["drawing_number"], "family_id": d["family_id"], "top3_families": top, "same_family_at_1": top[0] == d["family_id"],
                     "same_family_at_3": d["family_id"] in top, "nearest_distance": ranked[0]["distance"]})
    return {"queries": len(rows), "pool": len(pool), "same_family_at_1": mean(r["same_family_at_1"] for r in rows),
            "same_family_at_3": mean(r["same_family_at_3"] for r in rows), "parse_latency_p50_ms": sorted(parse_ms)[len(parse_ms) // 2], "rows": rows}


def run_gate(root: Path, output: Path) -> dict:
    from fitwitness.data.bootstrap import seed
    from fitwitness.runtime.jobs import Jobs
    from fitwitness.storage.repository import Repository

    output.mkdir(parents=True, exist_ok=True)
    repo = Repository(os.environ["FITWITNESS_DATABASE_URL"])
    repo.migrate()
    Jobs(repo).migrate()
    manifest = json.loads((root / "manifest.json").read_text())
    scope = TenantScope(tenant_id=f"gate-{uuid4()}", user_id="gate", role="operator")
    started = time.perf_counter()
    seed(repo, scope, root)
    lexical = run_lexical(repo, scope, root, id_matching="normalized")
    degraded = run_lexical(repo, scope, root, id_matching="token")
    rules = run_rules_graph(repo, root)
    geometry = run_geometry(root, sorted(manifest["family_splits"]["test"]) + sorted(manifest["family_splits"]["dev"]))
    control = {
        "id_variant_gap": lexical["id_variant"]["recall_at_5"] - degraded["id_variant"]["recall_at_5"],
        "id_typo_gap": lexical["id_typo"]["recall_at_5"] - degraded["id_typo"]["recall_at_5"],
        "degraded": {k: v for k, v in degraded.items() if k != "rows"},
    }
    report = {
        "version": GATE_VERSION, "code_sha": _git_sha(), "created_at": datetime.now(timezone.utc).isoformat(),
        "wall_s": round(time.perf_counter() - started, 2), "corpus_documents": len(repo.snapshot(scope).revision_ids),
        "rules_graph": {k: v for k, v in rules.items() if k not in ("expected", "actual")},
        "lexical": {k: v for k, v in lexical.items() if k != "rows"},
        "geometry": {k: v for k, v in geometry.items() if k != "rows"},
        "control": control,
        "limitations": [
            "합성 도면 150개·고정 test split 6 family 기준의 회귀 게이트입니다. 산업 데이터 성능이 아닙니다.",
            "모델·API 호출이 없습니다. 의미·이미지 채널과 LLM Agent 품질은 별도 실측 workflow가 측정합니다.",
            "control 항목은 도번 정규화를 끈 열화 실행과의 차이이며, 벤치마크가 열화를 감지하는지 확인하는 용도입니다.",
        ],
    }
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    (output / "rows.json").write_text(json.dumps({"lexical": lexical["rows"], "degraded": degraded["rows"], "geometry": geometry["rows"],
                                                  "rules": {"expected": rules["expected"], "actual": rules["actual"]}}, ensure_ascii=False, indent=1))
    return report


def _get(d: dict, path: str):
    cur = d
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def compare(report: dict, baseline: dict | None, thresholds: dict[str, dict] = THRESHOLDS) -> dict:
    checks = []
    for path, rule in thresholds.items():
        value = _get(report, path)
        base = _get(baseline, path) if baseline else None
        why = []
        if value is None:
            why.append("missing")
        else:
            if "min" in rule and value < rule["min"]:
                why.append(f"{value:.3f} < min {rule['min']}")
            if "equals" in rule and value != rule["equals"]:
                why.append(f"{value} != {rule['equals']}")
            if "max_drop" in rule and base is not None and value < base - rule["max_drop"]:
                why.append(f"dropped {base:.3f} -> {value:.3f} (> {rule['max_drop']})")
        checks.append({"metric": path, "value": value, "baseline": base, "ok": not why, "why": "; ".join(why)})
    return {"passed": all(c["ok"] for c in checks), "checks": checks}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--root", type=Path, default=Path("var/corpus"))
    p.add_argument("--output", type=Path, default=Path("artifacts/gate"))
    p.add_argument("--baseline", type=Path, default=Path("docs/evaluation/gate-baseline.json"))
    p.add_argument("--write-baseline", action="store_true", help="store this report as the new baseline (deliberate act)")
    args = p.parse_args(argv)
    report = run_gate(args.root, args.output)
    baseline = json.loads(args.baseline.read_text()) if args.baseline.exists() else None
    result = compare(report, baseline)
    for c in result["checks"]:
        mark = "PASS" if c["ok"] else "FAIL"
        base = f" (baseline {c['baseline']:.3f})" if isinstance(c["baseline"], (int, float)) else ""
        value = f"{c['value']:.3f}" if isinstance(c["value"], (int, float)) else str(c["value"])
        print(f"{mark} {c['metric']}={value}{base}{' ' + c['why'] if c['why'] else ''}")
    (args.output / "gate.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    if args.write_baseline:
        args.baseline.parent.mkdir(parents=True, exist_ok=True)
        args.baseline.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        print(f"baseline written to {args.baseline}")
    print("RESULT:", "passed" if result["passed"] else "FAILED", f"({report['wall_s']}s)")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
