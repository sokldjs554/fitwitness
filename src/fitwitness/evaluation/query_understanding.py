"""Does a model reading the query find conditions the patterns miss, and does that reach the ranking?

``retrieval/understanding.py`` lets a model propose the kind, dimension intervals, material and exclusions a
search query states, keeps only what it quotes from the query, and merges that with the pattern reading. This
measures it on queries written the way a workshop writes them: a range ("폭 60에서 70mm 사이"), a unit in cm, a
bound ("두께 4mm 이상"), a number without a unit, English, a particle between the word and the number
("폭은 65mm"), a material to avoid ("AL6061 말고"), the unit spelled out ("밀리미터"), and no spaces at all; plus
one plain phrasing the patterns already read, as the control. Ten phrasings per family, built from the generator's
gold of the family's base drawing, on the dev split first (6 families) and the test split once.

Two stages. Understanding: the pattern reading and the model-merged reading are scored against the expected
conditions (field, bounds in mm, kind, material, exclusions). Retrieval (``--retrieval``, needs PostgreSQL and the
pinned encoders): the same queries through the hybrid search, ordered by conditions from each reading, scored
against the drawings whose gold satisfies the query; the RRF top-20 pool's recall is the bound either ordering
can reach.

Cost: one call per query under a per-call cap and a hard ceiling; a query that would not fit is skipped and listed.

    python -m fitwitness.evaluation.query_understanding --split dev --max-cost-usd 0.15 --per-call-usd 0.02
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

from fitwitness.contracts import Interval, Requirement, SearchRequest
from fitwitness.evaluation.agent_trajectory import Ledger
from fitwitness.evaluation.stats import wilson
from fitwitness.retrieval.pipeline import extract_requirements
from fitwitness.retrieval.understanding import DIMENSIONS, OPEN_HIGH, Condition, UnderstoodQuery, merge, understand
from fitwitness.verification.conditions import UNITS

KIND_KO = {"bracket": "브래킷", "flange": "플랜지", "shaft": "샤프트", "housing": "하우징"}
DESCRIPTIONS = {"bracket": "장비를 바닥에 붙잡아 두는 납작한 금속 부속", "flange": "관 끝에 맞대어 체결하는 둥근 금속 부속",
                "shaft": "동력을 전달하며 돌아가는 긴 원통형 부속", "housing": "기계 내부 부속을 둘러싸는 속이 빈 외피"}
PHRASINGS = ["between", "cm", "at_least", "no_unit", "english", "particle", "plain", "exclude", "unit_word", "squeezed"]
PHRASING_KO = {"between": "범위 (에서 ... 사이)", "cm": "cm 단위", "at_least": "하한 (이상)", "no_unit": "단위 없는 숫자", "english": "영어",
               "particle": "조사 (폭은)", "plain": "정형 (대조군)", "exclude": "재질 제외 (말고)", "unit_word": "단위를 말로 (밀리미터)", "squeezed": "띄어쓰기 없음·미리"}


def _plain(n) -> str:
    d = Decimal(str(n)).normalize()
    return format(d, "f")


def phrasings(source: dict, truth: dict, materials: list[str]) -> list[dict]:
    """Ten queries for one base drawing, each with the conditions it states and the spans they rest on."""
    kind, k = source["kind"], KIND_KO[source["kind"]]
    p = DESCRIPTIONS[kind]
    w, t, hs, m = truth["width"], truth["thickness"], truth.get("hole_spacing"), truth["material"]
    other = next((x for x in sorted(materials) if x != m), None)
    out = []

    def add(pid, text, conditions, kind_quote, material=None, material_quote="", excluded=()):
        out.append({"id": f"{source['family_id']}-{pid}", "family_id": source["family_id"], "phrasing": pid, "text": text, "source_id": source["id"],
                    "expected": {"kind": kind, "kind_quote": kind_quote, "conditions": conditions, "material": material, "material_quote": material_quote,
                                 "excluded": [{"material": x, "quote": q} for x, q in excluded]}})

    add("between", f"{p} 폭 {w - 5}에서 {w + 5}mm 사이", {"width": {"low": _plain(w - 5), "high": _plain(w + 5), "quote": f"폭 {w - 5}에서 {w + 5}mm 사이"}}, p)
    add("cm", f"{k} 너비 {_plain(Decimal(w) / 10)}cm", {"width": {"low": _plain(w), "high": _plain(w), "quote": f"너비 {_plain(Decimal(w) / 10)}cm"}}, k)
    add("at_least", f"{k} 두께 {t}mm 이상", {"thickness": {"low": _plain(t), "high": None, "quote": f"두께 {t}mm 이상"}}, k)
    if hs:
        add("no_unit", f"{p} 구멍 사이 거리 {hs}", {"hole_spacing": {"low": _plain(hs), "high": _plain(hs), "quote": f"구멍 사이 거리 {hs}"}}, p)
    else:
        add("no_unit", f"{p} 두께 {t}", {"thickness": {"low": _plain(t), "high": _plain(t), "quote": f"두께 {t}"}}, p)
    add("english", f"{kind} about {w} millimetres wide and {t} mm thick",
        {"width": {"low": _plain(w), "high": _plain(w), "quote": f"{w} millimetres wide"}, "thickness": {"low": _plain(t), "high": _plain(t), "quote": f"{t} mm thick"}}, kind)
    add("particle", f"{k}인데 폭은 {w}mm", {"width": {"low": _plain(w), "high": _plain(w), "quote": f"폭은 {w}mm"}}, k)
    add("plain", f"{k} 폭 {w}mm 두께 {t}mm 재질 {m}", {"width": {"low": _plain(w), "high": _plain(w), "quote": f"폭 {w}mm"}, "thickness": {"low": _plain(t), "high": _plain(t), "quote": f"두께 {t}mm"}},
        k, material=m, material_quote=f"재질 {m}")
    if other:
        add("exclude", f"{k} 폭 {w}mm, {other} 말고", {"width": {"low": _plain(w), "high": _plain(w), "quote": f"폭 {w}mm"}}, k, excluded=[(other, f"{other} 말고")])
    add("unit_word", f"폭 {w} 밀리미터짜리 {p}", {"width": {"low": _plain(w), "high": _plain(w), "quote": f"폭 {w} 밀리미터"}}, p)
    add("squeezed", f"{k}폭{w}미리 두께{t}미리", {"width": {"low": _plain(w), "high": _plain(w), "quote": f"폭{w}미리"}, "thickness": {"low": _plain(t), "high": _plain(t), "quote": f"두께{t}미리"}}, k)
    return out


def make_queries(manifest: dict, gold: dict, split: str) -> list[dict]:
    active = [d for d in manifest["document_entries"] if not d["is_revision_update"]]
    materials = sorted({m for d in active if (m := gold[d["id"]].get("material"))})
    out = []
    for family in sorted(manifest["family_splits"][split]):
        source = next(d for d in active if d["family_id"] == family)
        out.extend(phrasings(source, gold[source["id"]], materials))
    return out


def oracle(query: dict) -> UnderstoodQuery:
    """The expected conditions as a model answer: what a perfect reading returns."""
    e = query["expected"]
    return UnderstoodQuery(kind=e["kind"], kind_quote=e["kind_quote"],
                           conditions=[Condition(field=f, low=c["low"], high=c["high"], unit="mm", quote=c["quote"]) for f, c in e["conditions"].items()],
                           material=e["material"], material_quote=e["material_quote"],
                           excluded_materials=[x["material"] for x in e["excluded"]], excluded_quotes=[x["quote"] for x in e["excluded"]])


def bounds_mm(r: Requirement) -> tuple[Decimal, Decimal | None] | None:
    if not isinstance(r.value, Interval) or r.unit not in UNITS:
        return None
    f = UNITS[r.unit]
    return r.value.low * f, (None if r.value.high >= OPEN_HIGH else r.value.high * f)


def score(expected: dict, reqs: list[Requirement]) -> dict:
    """Field by field against the expected conditions; everything in mm."""
    want = {f: (Decimal(c["low"]), None if c["high"] is None else Decimal(c["high"])) for f, c in expected["conditions"].items()}
    got: dict[str, list] = defaultdict(list)
    for r in reqs:
        if r.field in DIMENSIONS and (b := bounds_mm(r)) is not None:
            got[r.field].append(b)
    correct = sum(1 for f, b in want.items() if got.get(f) and all(x == b for x in got[f]))
    wrong = sum(1 for f, b in want.items() if got.get(f) and any(x != b for x in got[f]))
    missing = sum(1 for f in want if not got.get(f))
    extra = sum(1 for f in got if f not in want)
    kinds = {str(r.value) for r in reqs if r.field == "kind"}
    materials = {str(r.value) for r in reqs if r.field == "material"}
    excluded = {str(r.value) for r in reqs if r.field == "excluded_material"}
    want_excluded = {x["material"] for x in expected["excluded"]}
    kind_ok = kinds == {expected["kind"]}
    material_ok = materials == ({expected["material"]} if expected["material"] else set())
    excluded_ok = excluded == want_excluded
    return {"expected": len(want), "correct": correct, "wrong": wrong, "missing": missing, "extra": extra, "kind_ok": kind_ok,
            "kind_found": bool(kinds), "material_ok": material_ok, "material_wrong": bool(materials) and not material_ok, "excluded_ok": excluded_ok,
            "all_correct": correct == len(want) and wrong == 0 and extra == 0 and kind_ok and material_ok and excluded_ok}


def relevant(query: dict, active: list[dict], gold: dict) -> dict[str, int]:
    """The drawings whose gold satisfies every condition the query states."""
    e = query["expected"]
    out = {}
    for d in active:
        g = gold[d["id"]]
        if d["kind"] != e["kind"]:
            continue
        if e["material"] and g["material"] != e["material"]:
            continue
        if any(g["material"] == x["material"] for x in e["excluded"]):
            continue
        ok = True
        for f, c in e["conditions"].items():
            v = g.get(f)
            if v is None or Decimal(v) < Decimal(c["low"]) or (c["high"] is not None and Decimal(v) > Decimal(c["high"])):
                ok = False
        if ok:
            out[d["id"]] = 1
    return out


class StubModel:
    """Returns a fixed understanding (empty by default): exercises the harness without a credential."""

    def __init__(self, answer: UnderstoodQuery | None = None):
        self.answer = answer or UnderstoodQuery()
        self.emit = None

    def structured(self, schema, prompt, payload, role="query"):
        assert schema is UnderstoodQuery
        return self.answer, {"role": role, "attempt": 1, "repair": 0, "latency_ms": 0.0, "provider": "stub", "model_id": "stub",
                             "usage": {"input_tokens": len(payload) // 4, "output_tokens": 20}}


def _client(provider: str, model: str, cap: Decimal):
    if provider == "stub":
        return StubModel(), None
    if provider in ("oracle", "replay"):
        return None, None
    from fitwitness.agents.budget import BudgetTracker
    from fitwitness.agents.providers import create_model
    from fitwitness.contracts import Budget

    budget = BudgetTracker(Budget(max_cost_usd=cap, max_model_calls=4, max_tokens=24000, deadline_seconds=120))
    return create_model(provider, model, budget), budget


class _Zero:
    cost_usd = Decimal(0)
    reserved_cost_usd = Decimal(0)


def replayable(record: Path, split: str) -> dict[str, dict]:
    """The model readings a committed record holds for a split, by query id: the same reading, measured again
    through a changed ranking, without a call."""
    rec = json.loads(record.read_text())
    return {r["id"]: {**r, "workflow_run": b["workflow_run"]} for b in rec["batches"] if b["split"] == split and b.get("replay_of") is None for r in b["rows"]}


def evaluate(root: Path, *, provider: str, model: str, ceiling: Decimal, cap: Decimal, split: str = "dev", retrieval: bool = False,
             encoders: str = "pinned", max_queries: int | None = None, replay: Path | None = None) -> dict:
    manifest = json.loads((root / "manifest.json").read_text())
    gold = json.loads((root / "gold" / "labels.json").read_text())
    active = [d for d in manifest["document_entries"] if not d["is_revision_update"]]
    queries = make_queries(manifest, gold, split)[: max_queries or None]
    readings = replayable(replay, split) if provider == "replay" else None
    ledger = Ledger(ceiling)
    rows, skipped = [], []
    failures_in_a_row, stopped = 0, None
    for q in queries:
        rules = extract_requirements(q["text"])
        row = {"id": q["id"], "family_id": q["family_id"], "phrasing": q["phrasing"], "text": q["text"], "expected": q["expected"],
               "rules": {"requirements": [r.model_dump(mode="json") for r in rules], **score(q["expected"], rules)}, "model": None, "error": None,
               "latency_ms": None, "cost_usd": 0.0, "charged_usd": 0.0, "input_tokens": 0, "output_tokens": 0, "model_errors": [], "repairs": 0}
        if stopped or not ledger.fits(cap):
            skipped.append({"id": q["id"], "reason": stopped or f"ceiling: spent {ledger.spent} + cap {cap} > {ceiling}"})
            rows.append(row)
            continue
        client, budget = _client(provider, model, cap)
        events: list[dict] = []
        if client is not None:
            client.emit = lambda kind, payload, _e=events: _e.append({"kind": kind, "payload": payload})
        ledger.reserve(cap)
        started, error, merged, changes, dropped, meta = time.perf_counter(), None, rules, {"added": [], "replaced": [], "disagreements": []}, [], {}
        try:
            if provider == "replay":
                # The stored proposal is the model's part; grounding and the merge are deterministic and run again here.
                from fitwitness.retrieval.understanding import UnderstoodQuery, to_requirements
                prior = (readings or {}).get(q["id"])
                if not prior or not (prior.get("model") or {}).get("proposed") or prior["text"] != q["text"]:
                    raise LookupError(f"no stored reading for {q['id']} with this text")
                proposed, dropped = to_requirements(UnderstoodQuery.model_validate(prior["model"]["proposed"]), q["text"])
                merged, changes = merge(rules, proposed)
                meta = {"proposed": prior["model"]["proposed"], "replayed_from": prior.get("workflow_run")}
            else:
                if provider == "oracle":
                    from fitwitness.retrieval.understanding import to_requirements
                    proposed, dropped = to_requirements(oracle(q), q["text"])
                    meta = {"proposed": oracle(q).model_dump(mode="json")}
                else:
                    proposed, dropped, meta = understand(client, q["text"])
                merged, changes = merge(rules, proposed)
        except Exception as exc:  # noqa: BLE001 - the failure is the finding
            error = f"{type(exc).__name__}: {str(exc)[:300]}"
        usage = budget.usage if budget is not None else _Zero()
        charged = float(ledger.settle(cap, usage))
        failures_in_a_row = failures_in_a_row + 1 if error else 0
        if failures_in_a_row >= 3:
            stopped = f"three failures in a row; last: {error}"
        row.update({"model": {"requirements": [r.model_dump(mode="json") for r in merged], "proposed": meta.get("proposed"), "dropped": dropped, **changes,
                              **({"replayed_from": meta["replayed_from"]} if "replayed_from" in meta else {}),
                              **score(q["expected"], merged)}, "error": error,
                    "latency_ms": round((time.perf_counter() - started) * 1000, 1), "cost_usd": float(getattr(usage, "cost_usd", 0) or 0), "charged_usd": charged,
                    "input_tokens": getattr(usage, "input_tokens", 0), "output_tokens": getattr(usage, "output_tokens", 0),
                    "model_errors": [e["payload"] for e in events if e["kind"] in ("model_error", "model_schema_error")],
                    "repairs": sum(1 for e in events if e["kind"] == "model" and (e["payload"] or {}).get("repair"))})
        rows.append(row)
    report = {"summary": {"rules": summarise(rows, "rules"), "model": summarise(rows, "model", ledger)}, "rows": rows, "skipped": skipped, "stopped": stopped}
    if retrieval:
        report["retrieval"] = retrieve(root, manifest, gold, active, rows, encoders)
    return report


def summarise(rows: list[dict], path: str, ledger: Ledger | None = None) -> dict:
    scored = [r for r in rows if r.get(path)]
    by_phrasing: dict[str, Counter] = defaultdict(Counter)
    for r in scored:
        s = r[path]
        c = by_phrasing[r["phrasing"]]
        c["queries"] += 1
        c["all_correct"] += s["all_correct"]
        c["correct"] += s["correct"]
        c["expected"] += s["expected"]
        c["wrong"] += s["wrong"]
        c["extra"] += s["extra"]
        c["kind_ok"] += s["kind_ok"]
        c["material_wrong"] += s["material_wrong"]
    expected = sum(r[path]["expected"] for r in scored)
    correct = sum(r[path]["correct"] for r in scored)
    wrong = sum(r[path]["wrong"] for r in scored)
    extra = sum(r[path]["extra"] for r in scored)
    all_correct = sum(r[path]["all_correct"] for r in scored)
    out = {"path": path, "queries": len(scored), "all_correct": all_correct, "all_correct_rate": round(all_correct / len(scored), 4) if scored else None,
           "all_correct_wilson95": wilson(all_correct, len(scored)) if scored else None,
           "conditions_expected": expected, "conditions_correct": correct, "conditions_wrong": wrong, "conditions_extra": extra,
           "condition_recall": round(correct / expected, 4) if expected else None,
           "condition_precision": round(correct / (correct + wrong + extra), 4) if correct + wrong + extra else None,
           "kind_accuracy": round(sum(r[path]["kind_ok"] for r in scored) / len(scored), 4) if scored else None,
           "material_wrong": sum(r[path]["material_wrong"] for r in scored),
           "by_phrasing": {p: dict(c) for p, c in sorted(by_phrasing.items())}}
    if path == "model":
        out.update({"failed_calls": sum(1 for r in rows if r["error"]), "dropped_ungrounded": sum(len(r["model"]["dropped"]) for r in scored),
                    "replaced": sum(len(r["model"]["replaced"]) for r in scored), "disagreements": sum(len(r["model"]["disagreements"]) for r in scored),
                    "cost_usd": round(sum(r["cost_usd"] for r in rows), 6), "spent_usd": float(ledger.spent) if ledger else None,
                    "latency_ms_median": round(statistics.median(r["latency_ms"] for r in scored), 1) if scored else None,
                    "input_tokens": sum(r["input_tokens"] for r in rows), "output_tokens": sum(r["output_tokens"] for r in rows),
                    "model_errors": sum(len(r["model_errors"]) for r in rows), "repairs": sum(r["repairs"] for r in rows)})
    return out


def retrieve(root: Path, manifest: dict, gold: dict, active: list[dict], rows: list[dict], encoders: str) -> dict:
    """The same queries through the hybrid search, ordered by the conditions of each reading."""
    from uuid import uuid4

    from fitwitness.contracts import TenantScope
    from fitwitness.data.bootstrap import seed
    from fitwitness.evaluation.retrieval import score_ranking
    from fitwitness.retrieval.indexing import index_revision
    from fitwitness.retrieval.pipeline import search
    from fitwitness.runtime.jobs import Jobs
    from fitwitness.storage.repository import Repository

    repo = Repository(os.environ["FITWITNESS_DATABASE_URL"])
    repo.migrate()
    Jobs(repo).migrate()
    scope = TenantScope(tenant_id="query-understanding-" + str(uuid4()), user_id="evaluation", role="operator")
    seed(repo, scope, root)
    enc, channels, load_ms, index_ms = None, {"exact", "bm25"}, 0.0, 0.0
    if encoders == "pinned":
        from fitwitness.retrieval.embeddings import Encoders

        began = time.perf_counter()
        enc = Encoders()
        load_ms = (time.perf_counter() - began) * 1000
        channels = {"exact", "bm25", "semantic"}
        began = time.perf_counter()
        for rid in repo.snapshot(scope).revision_ids:
            index_revision(scope, rid, repo, enc)
        index_ms = (time.perf_counter() - began) * 1000
    snapshot = repo.snapshot(scope)
    methods = {"rrf": "복합 RRF (조건 정렬 없음)", "rules": "RRF + 패턴 읽기의 조건 정렬", "model": "RRF + 모델 읽기(병합)의 조건 정렬"}
    out_rows = []
    for r in rows:
        q = {"expected": r["expected"]}
        rel = relevant(q, active, gold)
        if not rel:
            out_rows.append({"id": r["id"], "phrasing": r["phrasing"], "relevant": 0, "methods": {}, "pool_recall": None})
            continue
        results = {}
        pool = search(scope, SearchRequest(text=r["text"], top_k=20), snapshot, repo, enc, channels)
        pool_ids = [c.revision_id for c in pool]
        pools = {}
        for method in methods:
            reqs = [] if method != "model" or not r["model"] else [Requirement.model_validate(x) for x in r["model"]["requirements"]]
            request = SearchRequest(text=r["text"], top_k=10, ranking="rrf" if method == "rrf" else "constraints", requirements=reqs)
            started = time.perf_counter()
            ranked = [c.revision_id for c in search(scope, request, snapshot, repo, enc, channels)]
            results[method] = {"recall_at_5": score_ranking(ranked, rel, 5)["recall"], "ndcg_at_10": score_ranking(ranked, rel, 10)["ndcg"],
                               "latency_ms": round((time.perf_counter() - started) * 1000, 1), "ranked": ranked}
            if method != "rrf":
                # the whole pool this ordering worked on (the RRF top-20 plus candidates of the stated kind): its recall is the bound
                whole = [c.revision_id for c in search(scope, request, snapshot, repo, enc, channels, return_pool=True)]
                pools[method] = score_ranking(whole, rel, len(whole) or 1)["recall"]
        out_rows.append({"id": r["id"], "phrasing": r["phrasing"], "relevant": len(rel), "methods": results,
                         "pool_recall": score_ranking(pool_ids, rel, 20)["recall"], "pool_recall_widened": pools})
    scored = [x for x in out_rows if x["methods"]]
    summary = {}
    for method, label in methods.items():
        vals = [x["methods"][method] for x in scored]
        by_phrasing = defaultdict(list)
        for x in scored:
            by_phrasing[x["phrasing"]].append(x["methods"][method]["recall_at_5"])
        summary[method] = {"label": label, "queries": len(vals), "recall_at_5": round(statistics.mean(v["recall_at_5"] for v in vals), 4) if vals else None,
                           "ndcg_at_10": round(statistics.mean(v["ndcg_at_10"] for v in vals), 4) if vals else None,
                           "latency_ms_median": round(statistics.median(v["latency_ms"] for v in vals), 1) if vals else None,
                           "recall_at_5_by_phrasing": {p: round(statistics.mean(v), 4) for p, v in sorted(by_phrasing.items())}}
    return {"channels": sorted(channels), "corpus_size": len(snapshot.revision_ids), "model_load_ms": round(load_ms), "index_ms": round(index_ms),
            "queries_with_relevant": len(scored), "queries_without_relevant": len(out_rows) - len(scored),
            "pool_recall_at_20": round(statistics.mean(x["pool_recall"] for x in scored), 4) if scored else None,
            "pool_recall_widened": {m: round(statistics.mean(x["pool_recall_widened"][m] for x in scored), 4) for m in ("rules", "model")} if scored else None,
            "summary": summary, "rows": out_rows, "snapshot_id": snapshot.id,
            "encoder_fingerprint": getattr(enc, "fingerprint", None), "models": getattr(enc, "manifest", None)}


def _sha() -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True, timeout=5).stdout.strip()
    except Exception:  # noqa: BLE001
        return os.getenv("GITHUB_SHA")


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--provider", choices=["anthropic", "openai", "stub", "oracle", "replay"], default="anthropic",
                   help="stub proposes nothing and oracle proposes the expected answer (both free): they check the harness, not a model; "
                        "replay takes the model's stored readings from --replay and measures them again through the current ranking")
    p.add_argument("--replay", default="docs/evaluation/query-understanding.json", help="the committed record whose readings --provider replay reuses")
    p.add_argument("--model", default=None)
    p.add_argument("--root", default="var/corpus")
    p.add_argument("--split", choices=["dev", "test"], default="dev")
    p.add_argument("--max-queries", type=int, default=None)
    p.add_argument("--retrieval", action="store_true", help="also run the queries through the search (needs FITWITNESS_DATABASE_URL)")
    p.add_argument("--encoders", choices=["pinned", "none"], default="pinned", help="none = exact + BM25 only, for a check without the encoder files")
    p.add_argument("--max-cost-usd", type=float, required=True, help="hard ceiling for the whole batch (at most 0.3)")
    p.add_argument("--per-call-usd", type=float, default=0.02, help="budget of one call, reserved before it starts (at most 0.05)")
    p.add_argument("--output", default="artifacts/query-understanding")
    args = p.parse_args(argv)
    assert 0 < args.max_cost_usd <= 0.3, "the ceiling is at most 0.3 USD"
    assert 0 < args.per_call_usd <= 0.05
    if args.provider in ("stub", "oracle", "replay"):
        args.model = args.model or ""
    else:
        args.model = args.model or ("claude-haiku-4-5-20251001" if args.provider == "anthropic" else os.environ.get("FITWITNESS_OPENAI_MODEL", ""))
        assert args.model, "no model id: pass --model or set FITWITNESS_OPENAI_MODEL"
    started = datetime.now(timezone.utc)
    report = evaluate(Path(args.root), provider=args.provider, model=args.model, ceiling=Decimal(str(args.max_cost_usd)), cap=Decimal(str(args.per_call_usd)),
                      split=args.split, retrieval=args.retrieval, encoders=args.encoders, max_queries=args.max_queries,
                      replay=Path(args.replay) if args.provider == "replay" else None)
    prefix = f"FITWITNESS_{args.provider.upper()}"
    report["protocol"] = {"version": "query-understanding-v2", "ordering": "RRF top-20 pool widened with candidates of the stated kind; conditions and kind order it",
                          "replay": args.replay if args.provider == "replay" else None,
                          "code_sha": _sha(), "started_at": started.isoformat(), "provider": args.provider, "model": args.model,
                          "split": args.split, "phrasings": PHRASINGS, "max_queries": args.max_queries, "ceiling_usd": str(args.max_cost_usd),
                          "per_call_cap_usd": str(args.per_call_usd), "retrieval": args.retrieval, "encoders": args.encoders,
                          "pricing_usd_per_million": {"input": os.getenv(f"{prefix}_INPUT_USD_PER_MILLION"), "output": os.getenv(f"{prefix}_OUTPUT_USD_PER_MILLION")},
                          "tuning": "phrasings and prompt written before any call; dev split first, test once; no change after a test score",
                          "relevance": "retrieval: same kind, gold inside every stated interval, material as asked, excluded material absent"}
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str))
    s = report["summary"]
    print("== query-understanding", json.dumps({k: {x: v[x] for x in ("queries", "all_correct", "condition_recall", "condition_precision", "kind_accuracy", "material_wrong")} for k, v in s.items()},
                                                ensure_ascii=False), "cost", s["model"].get("cost_usd"), "skipped", len(report["skipped"]), "stopped", report["stopped"])
    if "retrieval" in report:
        print("== retrieval", json.dumps({m: v["recall_at_5"] for m, v in report["retrieval"]["summary"].items()}), "pool", report["retrieval"]["pool_recall_at_20"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
