"""No-API evaluation gate: the parts of the system that can be measured on every commit.

Runs against real PostgreSQL but needs no model weights and no provider key, so CI can
block a regression before a paid experiment is ever scheduled. Four suites:

* ``rules_graph``     the deterministic LangGraph verdicts on family FW-F000 vs generator gold
* ``lexical``         exact + BM25 retrieval recall@5 per query category on the test split
* ``geometry``        STEP feature distance: does a family's base model find its own family?
* ``control``         the same lexical suite with identifier normalization switched off; it
                      must score lower on identifier variants, or the benchmark has lost its teeth
* ``claims``          the insurance claim pipeline (extraction, rules, challenge) on the synthetic
                      claim corpus: decision accuracy, the wrong-pay rate (must be zero) and the
                      automation rate, plus a degraded control that must pay claims it should not
* ``trajectory``      the road each run takes, not only where it ends: the real claim and drawing
                      graphs are run through the job runtime and their event paths are compared with
                      committed reference paths and checked against ordering, ledger and
                      reviewer invariants (see ``evaluation/trajectory.py``); a degraded claim
                      graph must be caught by it too

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
    "claims.wrong_pay_rate": {"equals": 0.0},
    "claims.wrong_deny_rate": {"equals": 0.0},
    "claims.decision_accuracy": {"min": 0.85, "max_drop": 0.02},
    "claims.field_accuracy": {"min": 0.90, "max_drop": 0.02},
    "claims.auto_rate": {"min": 0.60, "max_drop": 0.05},
    "control.claims_wrong_pay_gap": {"min": 0.05},
    # The statutory diagnosis certificate and fee statement, redrawn (no downloaded form is needed), read by their grid.
    "claims_official.wrong_pay_rate": {"equals": 0.0},
    "claims_official.wrong_deny_rate": {"equals": 0.0},
    "claims_official.decision_accuracy": {"min": 0.85, "max_drop": 0.02},
    "claims_official.field_accuracy": {"min": 0.95, "max_drop": 0.02},
    "control.official_reader_gap": {"min": 0.05},
    # Scanned-page reading needs the tesseract binary; without it these are skipped, not failed.
    "claims_scan.wrong_pay_rate": {"equals": 0.0, "optional": True},
    "claims_scan.wrong_deny_rate": {"equals": 0.0, "optional": True},
    "claims_scan.field_accuracy": {"min": 0.90, "max_drop": 0.03, "optional": True},
    # Only about six of the 22 sampled claims are payable, so a drop is not read from this rate: one claim is 17 points.
    "claims_scan.auto_rate": {"min": 0.50, "optional": True},
    "trajectory.match_rate": {"equals": 1.0},
    "trajectory.invariant_violations": {"equals": 0},
    "trajectory.graph_wrong_pay": {"equals": 0},
    "control.trajectory_gap": {"min": 0.05},
    "control.trajectory_degraded_violations": {"min": 1},
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


def run_claims(root: Path) -> dict:
    """Extraction + rules + challenge over the synthetic claim corpus, normal and degraded."""
    from fitwitness.claims.evaluate import evaluate

    normal = evaluate(root)
    degraded = evaluate(root, degraded=True)
    return {"normal": normal, "degraded": degraded}


def run_claims_official(root: Path) -> dict:
    """The claims on the two statutory forms, read by their grid, and again by the "label: value" reader alone."""
    from fitwitness.claims import official
    from fitwitness.claims.evaluate import evaluate

    forms = {"diagnosis": None, "receipt": None}
    grid = evaluate(root, forms=forms)
    read_page = official.read_page
    official.read_page = lambda *args, **kwargs: None  # as if the forms were not recognised
    try:
        text_only = evaluate(root, forms=forms)
    finally:
        official.read_page = read_page
    return {"normal": grid, "text_only": text_only}


SCAN_LEVEL = "medium"
SCAN_CASES_PER_SCENARIO = 2


def run_claims_scan(root: Path) -> dict | None:
    """The claim pipeline reading scanned pages (OCR), on a fixed sample, when Tesseract is installed."""
    from fitwitness.claims import ocr
    from fitwitness.claims.evaluate import evaluate

    if not ocr.available():
        return None
    manifest = json.loads((root / "manifest.json").read_text())
    seen: dict[str, int] = {}
    sample = []
    for case in manifest["cases"]:
        seen[case["scenario"]] = seen.get(case["scenario"], 0) + 1
        if seen[case["scenario"]] <= SCAN_CASES_PER_SCENARIO:
            sample.append(case["case_id"])
    return evaluate(root, scan=SCAN_LEVEL, workers=max(1, min(4, os.cpu_count() or 1)), cases=sample)


def run_gate(root: Path, output: Path, claims_root: Path | None = None, trajectories: Path | None = None,
             adopt_trajectories: bool = False) -> dict:
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
    claims_root = claims_root or Path("var/claims")
    claims = run_claims(claims_root) if (claims_root / "manifest.json").exists() else None
    claims_scan = run_claims_scan(claims_root) if claims else None
    claims_official = run_claims_official(claims_root) if claims else None
    trajectory = None
    if claims:
        from fitwitness.evaluation.trajectory import run_trajectories

        trajectory = run_trajectories(repo, Jobs(repo), root, claims_root, trajectories, adopt=adopt_trajectories)
        (output / "trajectories.observed.json").write_text(json.dumps(trajectory["observed"], ensure_ascii=False, indent=1))
    control = {
        "id_variant_gap": lexical["id_variant"]["recall_at_5"] - degraded["id_variant"]["recall_at_5"],
        "id_typo_gap": lexical["id_typo"]["recall_at_5"] - degraded["id_typo"]["recall_at_5"],
        "degraded": {k: v for k, v in degraded.items() if k != "rows"},
    }
    if claims:
        control["claims_wrong_pay_gap"] = claims["degraded"]["summary"]["wrong_pay_rate"] - claims["normal"]["summary"]["wrong_pay_rate"]
        control["claims_degraded"] = claims["degraded"]["summary"]
        control["official_reader_gap"] = claims_official["normal"]["summary"]["field_accuracy"] - claims_official["text_only"]["summary"]["field_accuracy"]
        control["official_text_only"] = claims_official["text_only"]["summary"]
    if trajectory:
        normal_rate, broken_rate = trajectory["normal"]["match_rate"], trajectory["degraded"]["match_rate"]
        control["trajectory_gap"] = (normal_rate - broken_rate) if normal_rate is not None and broken_rate is not None else None
        control["trajectory_degraded_violations"] = trajectory["degraded"]["invariant_violations"]
    report = {
        "version": GATE_VERSION, "code_sha": _git_sha(), "created_at": datetime.now(timezone.utc).isoformat(),
        "wall_s": round(time.perf_counter() - started, 2), "corpus_documents": len(repo.snapshot(scope).revision_ids),
        "rules_graph": {k: v for k, v in rules.items() if k not in ("expected", "actual")},
        "lexical": {k: v for k, v in lexical.items() if k != "rows"},
        "geometry": {k: v for k, v in geometry.items() if k != "rows"},
        "control": control,
        "claims": claims["normal"]["summary"] if claims else None,
        "trajectory": trajectory["normal"] if trajectory else None,
        "claims_scan": ({**claims_scan["summary"], "level": SCAN_LEVEL} if claims_scan else None),
        "claims_official": claims_official["normal"]["summary"] if claims_official else None,
        "limitations": [
            "합성 도면 150개·고정 test split 6 family 기준의 회귀 게이트입니다. 산업 데이터 성능이 아닙니다.",
            "모델·API 호출이 없습니다. 의미·이미지 채널과 LLM Agent 품질은 별도 실측 workflow가 측정합니다.",
            "control 항목은 도번 정규화를 끈 열화 실행과의 차이이며, 벤치마크가 열화를 감지하는지 확인하는 용도입니다.",
            "claims 항목은 합성 청구 서류와 가상의 지급 기준표 기준입니다. 실제 약관·실제 서류의 성능이 아니며, 모델 추출 경로는 포함하지 않습니다.",
            "claims_scan 항목은 합성 서류를 스캔처럼 열화시켜(medium) OCR로 읽은 결과이며 시나리오마다 2건만 잰 표본입니다. Tesseract가 없으면 건너뜁니다. 실제 스캔 문서의 성능이 아닙니다.",
            "claims_official 항목은 진단서와 진료비 계산서·영수증을 공식 서식의 라벨·선을 다시 그린 판에 생성기의 값으로 채워 읽은 결과입니다. 값의 위치는 서식에서 잰 것이고 병원 소프트웨어가 실제로 찍는 위치가 아닙니다. 두 서류 외에는 합성 문서 그대로입니다.",
            "trajectory 항목은 규칙 엔진과 고정 대본의 planner/challenger가 지나는 경로입니다. 실제 모델이 고르는 경로의 품질은 측정하지 않습니다.",
        ],
    }
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    (output / "rows.json").write_text(json.dumps({"lexical": lexical["rows"], "degraded": degraded["rows"], "geometry": geometry["rows"],
                                                  "rules": {"expected": rules["expected"], "actual": rules["actual"]},
                                                  "claims": claims["normal"]["rows"] if claims else []}, ensure_ascii=False, indent=1))
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
        if value is None and rule.get("optional"):
            checks.append({"metric": path, "value": None, "baseline": base, "ok": True, "why": "skipped (not measured here)"})
            continue
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
    p.add_argument("--claims-root", type=Path, default=Path("var/claims"))
    p.add_argument("--trajectories", type=Path, default=Path("docs/evaluation/trajectories.json"),
                   help="committed reference paths each claim and drawing run must reproduce")
    p.add_argument("--write-trajectories", action="store_true", help="adopt the observed paths as the new reference (deliberate act)")
    p.add_argument("--write-baseline", action="store_true", help="store this report as the new baseline (deliberate act)")
    args = p.parse_args(argv)
    report = run_gate(args.root, args.output, args.claims_root, args.trajectories, adopt_trajectories=args.write_trajectories)
    baseline = json.loads(args.baseline.read_text()) if args.baseline.exists() else None
    result = compare(report, baseline)
    for c in result["checks"]:
        mark = "PASS" if c["ok"] else "FAIL"
        base = f" (baseline {c['baseline']:.3f})" if isinstance(c["baseline"], (int, float)) else ""
        value = f"{c['value']:.3f}" if isinstance(c["value"], (int, float)) else str(c["value"])
        print(f"{mark} {c['metric']}={value}{base}{' ' + c['why'] if c['why'] else ''}")
    traj = report.get("trajectory") or {}
    for m in traj.get("mismatches", [])[:5]:
        print(f"  path differs: {m['path']} at step {m.get('at')}: expected {m.get('expected')!r}, got {m.get('actual')!r}")
    for v in traj.get("violations", [])[:5]:
        print(f"  invariant: {v}")
    (args.output / "gate.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    if args.write_trajectories:
        args.trajectories.parent.mkdir(parents=True, exist_ok=True)
        args.trajectories.write_text((args.output / "trajectories.observed.json").read_text() + "\n")
        print(f"trajectory reference written to {args.trajectories}")
    if args.write_baseline:
        args.baseline.parent.mkdir(parents=True, exist_ok=True)
        args.baseline.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        print(f"baseline written to {args.baseline}")
    print("RESULT:", "passed" if result["passed"] else "FAILED", f"({report['wall_s']}s)")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
