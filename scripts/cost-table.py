#!/usr/bin/env python3
"""Per-call cost and latency of every measured model call, recomputed from the committed records.

    python scripts/cost-table.py            # prints the rows of docs/COST.md

Nothing is called. Each figure is a mean (cost) or median / 95th percentile (latency) over the per-row
``cost_usd`` and ``latency_ms`` of a record in docs/evaluation; ``p95`` is the value at index
``int(0.95 * (n - 1))`` of the sorted latencies.
"""
import json
import statistics as st
from collections import defaultdict
from pathlib import Path

E = Path(__file__).resolve().parent.parent / "docs" / "evaluation"


def load(name):
    return json.loads((E / f"{name}.json").read_text(encoding="utf-8"))


def stats(rows):
    cost = [float(r["cost_usd"]) for r in rows]
    lat = sorted(r["latency_ms"] for r in rows)
    return {"n": len(rows), "cost": st.mean(cost), "p50": st.median(lat) / 1000, "p95": lat[int(0.95 * (len(lat) - 1))] / 1000}


def span(values, fmt):
    lo, hi = fmt % min(values), fmt % max(values)
    return lo if lo == hi else f"{lo}~{hi}"


def show(label, groups, unit, digits=1):
    s = [stats(g) for g in groups]
    fmt = f"%.{digits}f"
    print(f"| {label} | {unit} | ${span([x['cost'] for x in s], '%.4f')} | {span([x['p50'] for x in s], fmt)} / {span([x['p95'] for x in s], fmt)}초 |")


claude = load("claude")
show("PDF 근거 판정", [[x for x in claude["predictions"] if float(x["cost_usd"] or 0) > 0]], "72회", digits=2)

cm = {b["input"]: b["rows"] for b in load("claims-model")["batches"]}
show("청구 서류 읽기, 글자 층이 있는 서류", [cm[k] for k in cm if k.startswith("text layer")], "60건 × 3회")
show("청구 서류 읽기, 스캔처럼 흐리게 만든 서류", [cm["medium scan (OCR for both readers)"]], "60건 × 1회")

audit = load("claims-audit")
for label, runs in (("지급 직전 감사, 약해진 규칙의 결정", [r for r in audit["runs"] + audit["repeats"]["runs"] if r["mode"] == "degraded"]),
                    ("지급 직전 감사, 멀쩡한 규칙의 결정", [r for r in audit["runs"] if r["mode"] == "clean"]),
                    ("지급 직전 감사, 사실 절이 비어 있는 고장", [audit["subtle_control"]["run"]])):
    cost = [r["cost_usd"] / r["audited"] for r in runs]
    p50 = [r["latency_ms_median"] / 1000 for r in runs]
    print(f"| {label} | {runs[0]['audited']}건 × {len(runs)}회 | ${span(cost, '%.4f')} | 중앙값 {span(p50, '%.1f')}초 |")

qu = {b["name"]: b["rows"] for b in load("query-understanding")["batches"] if b["protocol"].get("provider") != "replay"}
show("검색 질의 읽기, 지금의 프롬프트 (dev·test 각 1회)", [qu["dev-after"], qu["test-after"]], "60문장 × 2회")
show("검색 질의 읽기, 프롬프트를 고치기 전 (test 3회)", [qu["test"], qu["test-repeat-2"], qu["test-repeat-3"]], "60문장 × 3회")
show("현업자 검색어 읽기 (고치기 전 · 후)", [qu["practitioner-before"], qu["practitioner-after"]], "10개 × 2회")

by = defaultdict(list)
for r in load("agent-trajectory")["runs"][3]["rows"]:
    if r["provider"] == "anthropic":
        by[r["mode"]].append(r)
for mode, label in (("fixed", "단일 도구 계획"), ("react", "반복 계획"), ("fitwitness", "planner+challenger")):
    s = stats(by[mode])
    calls = st.mean(r["model_calls"] for r in by[mode])
    print(f"| 에이전트 실행 한 번, {label} | {s['n']}회 | ${s['cost']:.4f} | 중앙값 {s['p50']:.1f}초 (모델 호출 평균 {calls:.0f}회) |")
