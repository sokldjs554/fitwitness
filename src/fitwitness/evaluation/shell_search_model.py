"""Does a real model search better with the terminal-style tool than without it? Measured under a hard ceiling.

The trajectory evaluation seeds one family (five drawings) per run, where every drawing is retrieved anyway and a
search tool has nothing to add. This module seeds the whole test split (thirty drawings) into one workspace per
run and asks for sets that a ranked search cannot list: every drawing of a material when there are more of them
than the ranked channels return, every drawing whose material is not written, every drawing with one dimension
value. A flange query that the ranked channels can answer is the control.

The queries and their answers are built from the generator's labels before any call, by fixed rules (the most
common value, the kind with the most such drawings), and written to the protocol. Each query runs three times:

  * ``rules``: the free reference, ranked retrieval and a lookup of every candidate,
  * ``ranked``: the model in the ``react`` structure with the usual tools,
  * ``shell``: the same, with ``search_shell`` switched on (``FITWITNESS_SHELL_SEARCH=on``).

A run is scored on the drawings it ends with, since a candidate it never found can never get a verdict: how many
of the answer set it found, how many it found that are not in the set, and for queries the verifier can check
(material, dimension) how many of the answer set it confirmed as a match and whether anything else was. The
commands the model typed, the ones the tool refused, cost and latency go in the row.

    python -m fitwitness.evaluation.shell_search_model --max-cost-usd 0.3 --output artifacts/shell-search
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from statistics import median
from uuid import uuid4

from fitwitness.contracts import Budget, RunRequest, SearchRequest
from fitwitness.evaluation.agent_trajectory import KIND_WORD, Charged, Ledger, rejected

ARMS = ("ranked", "shell")
SUBJECT = {"width": "너비", "hole_spacing": "구멍 간격"}
BUDGET = {"max_model_calls": 4, "max_tool_calls": 24, "max_tokens": 64000, "deadline_seconds": 240}


def _entries(root: Path, split: str) -> list[dict]:
    manifest = json.loads((root / "manifest.json").read_text())
    families = set(manifest["family_splits"][split])
    return [e for e in manifest["document_entries"] if e["family_id"] in families and not e["is_revision_update"]]


def cases(root: Path, split: str = "test") -> list[dict]:
    """The queries and their answer sets, from the labels of the drawings the workspace holds.

    Written the way the pattern reader parses them ("AL6061 도면", "너비 101mm"), so the verifier can check the
    material and dimension queries; "재질이 AL6061인" is not parsed, and that is the model reading's job, not
    this measurement's. No condition says "material not written", so those two are scored on what was found."""
    gold = json.loads((root / "gold/labels.json").read_text())
    entries = _entries(root, split)
    label = {e["id"]: gold[e["id"]] for e in entries}
    kind = {e["id"]: e["kind"] for e in entries}

    def ids(pred) -> list[str]:
        return sorted(i for i in label if pred(i))

    def most(counter: Counter):
        return sorted(counter.items(), key=lambda kv: (-kv[1], str(kv[0])))[0][0]

    materials = Counter(label[i].get("material") for i in label if label[i].get("material"))
    common, other = most(materials), most(Counter({m: n for m, n in materials.items() if m != most(materials)}))
    no_material = most(Counter(kind[i] for i in label if not label[i].get("material")))
    other_kind = most(Counter(kind[i] for i in label if label[i].get("material") == other))
    out = [
        ("material_common", f"{common} 도면을 모두 찾아 주세요", ids(lambda i: label[i].get("material") == common), True),
        ("material_other", f"{other} 도면을 모두 찾아 주세요", ids(lambda i: label[i].get("material") == other), True),
        ("material_missing", "재질이 적혀 있지 않은 도면을 모두 찾아 주세요", ids(lambda i: not label[i].get("material")), False),
        ("kind_material_missing", f"재질이 적혀 있지 않은 {KIND_WORD[no_material]} 도면을 모두 찾아 주세요",
         ids(lambda i: kind[i] == no_material and not label[i].get("material")), False),
        ("kind_material_other", f"{other} {KIND_WORD[other_kind]} 도면을 모두 찾아 주세요",
         ids(lambda i: kind[i] == other_kind and label[i].get("material") == other), True),
    ]
    for field in ("width", "hole_spacing"):
        values = Counter(label[i].get(field) for i in label if label[i].get(field) is not None)
        value = most(values)
        out.append((f"{field}_value", f"{SUBJECT[field]} {value}mm인 도면을 모두 찾아 주세요",
                    ids(lambda i, f=field, v=value: label[i].get(f) == v), True))
    out.append(("kind_control", "플랜지 도면을 모두 찾아 주세요", ids(lambda i: kind[i] == "flange"), True))
    return [{"id": cid, "query": q, "answer": a, "checkable": c} for cid, q, a, c in out]


def seed(repo, root: Path, split: str):
    from fitwitness.contracts import DrawingRevision, TenantScope
    from fitwitness.ingest.pdf import extract_pdf

    scope = TenantScope(tenant_id=f"shell-eval-{uuid4()}", user_id="eval", role="operator")
    for e in _entries(root, split):
        rev = DrawingRevision(tenant_id=scope.tenant_id, **{k: e[k] for k in ("id", "document_id", "drawing_number", "family_id", "revision_label", "supersedes", "kind", "title", "source_hash")})
        data = (root / e["pdf"]).read_bytes()
        repo.add_revision(scope, rev, data)
        repo.save_facts(scope, rev.id, extract_pdf(data, rev))
    return scope


def score(case: dict, actual: dict[str, str], events: list[dict]) -> dict:
    """What a run found and confirmed against the answer set, and what it typed."""
    answer, found = set(case["answer"]), set(actual)
    matched = {r for r, v in actual.items() if v == "match"}
    tools = [(e.get("payload") or {}) for e in events if e["kind"] == "tool"]
    errors = [(e.get("payload") or {}) for e in events if e["kind"] == "tool_error"]
    return {
        "answer": len(answer), "found": len(found & answer), "extra": len(found - answer),
        "found_recall": round(len(found & answer) / len(answer), 4) if answer else None,
        "confirmed": len(matched & answer) if case["checkable"] else None,
        "false_matches": len(matched - answer) if case["checkable"] else None,
        "missed": sorted(answer - found),
        "commands": [t.get("arguments", {}).get("command") for t in tools if t.get("name") == "search_shell"],
        "command_items": [t.get("items") for t in tools if t.get("name") == "search_shell"],
        "refused": [{"command": (e.get("arguments") or {}).get("command"), "error": e.get("error")} for e in errors if e.get("name") == "search_shell"],
        "tools": [t.get("name") for t in tools],
        "tool_errors": len(errors),
    }


def run_one(repo, jobs, root: Path, split: str, case: dict, request: RunRequest, arm: str) -> dict:
    from fitwitness.agents.graph import execute_run

    previous = os.environ.get("FITWITNESS_SHELL_SEARCH")
    os.environ["FITWITNESS_SHELL_SEARCH"] = "on" if arm == "shell" else "off"
    try:
        scope = seed(repo, root, split)
        job = jobs.enqueue(scope, request, str(uuid4()))
        started, error = time.perf_counter(), None
        try:
            execute_run(repo, scope, job.id)
        except Exception as exc:  # noqa: BLE001 - the failure is the finding; it goes in the row
            error = f"{type(exc).__name__}: {str(exc)[:300]}"
        latency = (time.perf_counter() - started) * 1000
    finally:
        if previous is None:
            os.environ.pop("FITWITNESS_SHELL_SEARCH", None)
        else:
            os.environ["FITWITNESS_SHELL_SEARCH"] = previous
    view, events = jobs.get(scope, job.id), [dict(e) for e in jobs.events(scope, job.id)]
    for e in events:
        e["timestamp"] = str(e["timestamp"])
    actual = {d.revision_id: d.verdict.value if hasattr(d.verdict, "value") else d.verdict for d in view.decisions}
    requirements = next(((e["payload"] or {}).get("requirements", []) for e in events if e["kind"] == "intent"), [])
    usage = view.usage
    return {"case": case["id"], "query": case["query"], "arm": arm, "provider": request.provider, "mode": request.mode,
            "run_id": view.id, "state": view.state, "error": error, "latency_ms": round(latency, 1),
            "requirements": [{k: r.get(k) for k in ("field", "operator", "value", "unit")} for r in requirements],
            "actual": actual, **score(case, actual, events),
            "model_calls": usage.model_calls, "tool_calls": usage.tool_calls, "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens, "cost_usd": float(usage.cost_usd or 0), "reserved_left_usd": float(usage.reserved_cost_usd),
            "model_errors": [e["payload"] for e in events if e["kind"] in ("model_error", "model_schema_error")],
            "events": events}


def summarise(rows: list[dict]) -> dict:
    out = {}
    for arm in ("rules", *ARMS):
        group = [r for r in rows if r["arm"] == arm]
        if not group:
            continue
        checkable = [r for r in group if r["confirmed"] is not None]
        out[arm] = {
            "runs": len(group), "completed": sum(r["state"] == "completed" for r in group),
            "found": sum(r["found"] for r in group), "answer": sum(r["answer"] for r in group),
            "extra": sum(r["extra"] for r in group),
            "complete_sets": sum(r["found"] == r["answer"] for r in group),
            "confirmed": sum(r["confirmed"] for r in checkable), "confirmable": sum(r["answer"] for r in checkable),
            "false_matches": sum(r["false_matches"] for r in checkable),
            "shell_commands": sum(len(r["commands"]) for r in group), "refused": sum(len(r["refused"]) for r in group),
            "runs_using_shell": sum(bool(r["commands"]) for r in group),
            "model_calls": sum(r["model_calls"] for r in group), "tool_calls": sum(r["tool_calls"] for r in group),
            "cost_usd": round(sum(r["cost_usd"] for r in group), 6),
            "latency_ms_p50": round(median(r["latency_ms"] for r in group), 1),
        }
    return out


def run(args) -> dict:
    from fitwitness.runtime.jobs import Jobs
    from fitwitness.storage.repository import Repository

    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=False)
    root = Path(args.corpus)
    todo = cases(root, args.split)
    if args.cases:
        todo = [c for c in todo if c["id"] in args.cases]
    arms = [a for a in args.arms.split(",") if a]
    assert set(arms) <= set(ARMS), arms
    repo = Repository(os.environ["FITWITNESS_DATABASE_URL"])
    repo.migrate()
    jobs = Jobs(repo)
    jobs.migrate()
    ceiling, cap = Decimal(str(args.max_cost_usd)), Decimal(str(args.per_run_usd))
    protocol = {
        "split": args.split, "drawings": len(_entries(root, args.split)), "cases": todo, "arms": arms, "mode": args.mode,
        "provider": "anthropic", "model": args.model, "ceiling_usd": str(ceiling), "per_run_cap_usd": str(cap), "budget": BUDGET,
        "pricing_usd_per_million": {"input": os.getenv("FITWITNESS_ANTHROPIC_INPUT_USD_PER_MILLION"),
                                    "output": os.getenv("FITWITNESS_ANTHROPIC_OUTPUT_USD_PER_MILLION")},
        "code_sha": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "module_hash": sha256(Path(__file__).read_bytes()).hexdigest(),
        "started_at": datetime.now(timezone.utc).isoformat(),
    }
    (out / "protocol.json").write_text(json.dumps(protocol, ensure_ascii=False, indent=2))
    rows, skipped, stop_reason = [], [], None
    ledger = Ledger(ceiling)

    def record(row):
        rows.append(row)
        with (out / "runs.jsonl").open("a") as f:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
        print(row["arm"], row["case"], row["state"], f"found {row['found']}/{row['answer']} extra {row['extra']}",
              "confirmed", row["confirmed"], "commands", row["commands"], "refused", len(row["refused"]), "cost", row["cost_usd"], flush=True)

    for case in todo:  # the reference path, free
        record(run_one(repo, jobs, root, args.split, case, RunRequest(search=SearchRequest(text=case["query"]), provider="rules", budget=Budget(max_tokens=64000)), "rules"))
    for arm in arms:
        for case in todo:
            if stop_reason or not ledger.fits(cap):
                skipped.append({"case": case["id"], "arm": arm, "reason": stop_reason or f"ceiling: spent {ledger.spent} + cap {cap} > {ceiling}"})
                continue
            ledger.reserve(cap)
            request = RunRequest(search=SearchRequest(text=case["query"]), provider="anthropic", model_id=args.model, mode=args.mode,
                                 budget=Budget(max_cost_usd=cap, **BUDGET))
            row = run_one(repo, jobs, root, args.split, case, request, arm)
            row["charged_usd"] = float(ledger.settle(cap, Charged(Decimal(str(row["cost_usd"])), Decimal(str(row["reserved_left_usd"])))))
            record(row)
            stop_reason = rejected({**row, "provider": "anthropic"})
    report = {"status": "measured" if any(r["arm"] != "rules" and r["state"] == "completed" for r in rows) else "not measured",
              "stopped": stop_reason, "protocol": protocol, "summary": summarise(rows),
              "spent_usd": float(ledger.spent), "skipped": skipped,
              "rows": [{k: v for k, v in r.items() if k != "events"} for r in rows],
              "limitations": [
                  "합성 도면 test split 30장 한 작업 공간, 질의 8개, 질의마다 한 번 실행입니다. 반복이 없어 모델의 변동은 보지 못합니다.",
                  "질의와 정답 집합은 생성기의 라벨에서 정해진 규칙으로 만들었고, 셸 도구가 유리한 질의(열거, 부정)를 일부러 골랐습니다. 순위 검색이 답할 수 있는 플랜지 질의 하나가 대조군입니다.",
                  "셸 도구가 읽는 글은 도번·제목·추출된 사실뿐이고, 사실 추출은 합성 PDF의 벡터 글자에서 나옵니다.",
              ]}
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return report


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--model", default="claude-haiku-4-5-20251001")
    p.add_argument("--arms", default=",".join(ARMS))
    p.add_argument("--mode", default="react", choices=["fixed", "react", "fitwitness"])
    p.add_argument("--max-cost-usd", type=float, required=True, help="hard ceiling for the whole run")
    p.add_argument("--per-run-usd", type=float, default=0.03, help="budget of one graph run; reserved before it starts")
    p.add_argument("--corpus", default="var/corpus")
    p.add_argument("--split", default="test")
    p.add_argument("--cases", nargs="*")
    p.add_argument("--output", required=True)
    args = p.parse_args(argv)
    assert 0 < args.max_cost_usd <= 0.5, "the ceiling is at most 0.5 USD"
    assert 0 < args.per_run_usd <= 0.05
    report = run(args)
    for arm, m in report["summary"].items():
        print("== shell-search", arm, json.dumps(m, ensure_ascii=False))
    print("== shell-search spent", report["spent_usd"], "skipped", len(report["skipped"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
