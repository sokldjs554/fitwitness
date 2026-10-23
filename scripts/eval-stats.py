"""How wide the headline numbers are, read from the committed records under docs/evaluation.

Writes docs/evaluation/statistics.json and prints the table the docs quote. No model is called and no
measurement is repeated: every count here already exists in a record; this adds the interval a count of
that size carries, pass^k where a case was tried more than once, and a case-level bootstrap where repeats
of the same query would otherwise be counted as independent."""
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
import gzip, json, subprocess, sys

sys.path.insert(0, "src")
from fitwitness.evaluation.stats import wilson, pass_power_k, case_bootstrap, sem  # noqa: E402

E = Path("docs/evaluation")
entries = []


def add(name, source, correct=None, total=None, **extra):
    row = {"name": name, "source": source}
    if total is not None:
        row.update(correct=correct, total=total, share=round(correct / total, 4), ci95=wilson(correct, total))
    row.update(extra)
    entries.append(row)
    return row


def load(path):
    return json.loads((E / path).read_text())


def rows_of(folder, fallback):
    gz = E / folder / "runs.jsonl.gz"
    if gz.exists():
        return [json.loads(l) for l in gzip.open(gz, "rt") if l.strip()], f"{folder}/runs.jsonl.gz (3 repeats)"
    return load(fallback)["rows"], f"{fallback} (repeat 1 only)"


# 1. PDF evidence verdicts, Claude Haiku 4.5: 24 cases x 3 repeats
claude = load("claude.json")
model = [r for r in claude["predictions"] if r["method"] == "anthropic"]
by_case = defaultdict(list)
for r in model:
    by_case[r["case_id"]].append(r["status"] == "ok" and r["predicted"] == r["expected"])
negative = [r for r in model if r["expected"] != "match"]
add("PDF 근거 판정, Claude Haiku 4.5, 정답 verdict", "claude.json", sum(sum(v) for v in by_case.values()), len(model),
    repeats=pass_power_k(by_case), note="72 verdict는 24 사례 x 3회라 독립 72건이 아님; pass^3가 사례 단위 신뢰도")
add("PDF 근거 판정, Claude Haiku 4.5, 잘못된 일치(false acceptance)", "claude.json",
    sum(r["predicted"] == "match" for r in negative), len(negative), note="match가 아닌 사례 중 match로 판정한 수")

# 2. The same cases with the local Qwen pilot
local = [json.loads(l) for l in (E / "run-20261004/predictions.jsonl").read_text().splitlines() if l.strip()]
local = [r for r in local if r["method"] == "local"]
by_case = defaultdict(list)
for r in local:
    by_case[r["case_id"]].append(r["status"] == "ok" and r["predicted"] == r["expected"])
add("PDF 근거 판정, Qwen3-1.7B 로컬 pilot, 정답 verdict", "run-20261004/predictions.jsonl",
    sum(sum(v) for v in by_case.values()), len(local), repeats=pass_power_k(by_case),
    per_repeat=[round(sum(by_case[c][i] for c in by_case) / len(by_case), 4) for i in range(3)],
    accuracy_case_bootstrap_ci95=case_bootstrap({c: [float(x) for x in v] for c, v in by_case.items()}))

# 3. Retrieval and reranking: case-level bootstrap of Recall@5 and nDCG@10 per method
for name, folder, fallback in (("복합 검색 retrieval-v2", "retrieval-37198685994", "retrieval.json"),
                               ("재정렬 reranking-v1", "reranking-37206750470", "reranking.json")):
    rows, src = rows_of(folder, fallback)
    for method in sorted({r["method"] for r in rows}, key=lambda m: [r["method"] for r in rows].index(m)):
        sub = [r for r in rows if r["method"] == method]
        per_case = defaultdict(lambda: {"recall_at_5": [], "ndcg_at_10": []})
        for r in sub:
            for k in ("recall_at_5", "ndcg_at_10"):
                per_case[r["case_id"]][k].append(float(r[k]))
        add(f"{name}, {method}", src, rows=len(sub), cases=len(per_case),
            recall_at_5=round(sum(x for c in per_case.values() for x in c["recall_at_5"]) / len(sub), 4),
            recall_at_5_ci95=case_bootstrap({c: v["recall_at_5"] for c, v in per_case.items()}),
            ndcg_at_10=round(sum(x for c in per_case.values() for x in c["ndcg_at_10"]) / len(sub), 4),
            ndcg_at_10_ci95=case_bootstrap({c: v["ndcg_at_10"] for c, v in per_case.items()}))

# 4. Real-model trajectory, run 4 and the runs before it
traj = load("agent-trajectory.json")
for run in traj["runs"]:
    for mode, m in run["by_mode"].items():
        if mode == "rules":
            continue
        add(f"실제 모델 경로 {run['run_id']} {mode}, 완료한 실행", "agent-trajectory.json", m["completed"], m["runs"],
            verdict_accuracy=m["accuracy"], note="verdict 30개는 실행 6개 안에서 상관됨; 구간은 실행 단위")

# 5. Claims, 120 synthetic cases (gate baseline) and the scanned forms on the test split
gate = load("gate-baseline.json")
c = gate["claims"]
k = round(c["decision_accuracy"] * c["cases"]); assert abs(k / c["cases"] - c["decision_accuracy"]) < 1e-6
add("보험 청구 120건, 잘못 지급", "gate-baseline.json claims", c["wrong_pay_count"], c["cases"])
add("보험 청구 120건, 잘못 부지급", "gate-baseline.json claims", c["wrong_deny_count"], c["cases"])
add("보험 청구 120건, 결정 정확도", "gate-baseline.json claims", k, c["cases"])
scan = load("official-scan.json")
for level, v in scan["redrawn_layout"]["test"].items():
    f, p = v["field_level"], v["pipeline"]
    add(f"스캔 공식 서식 test {level}, 필드 정답", "official-scan.json redrawn_layout.test", f["correct"], f["fields"])
    add(f"스캔 공식 서식 test {level}, 잘못 지급", "official-scan.json redrawn_layout.test", p["wrong_pay_count"], p["cases"])
for level, v in scan["real_blank_forms"]["test"].items():
    add(f"실제 빈 서식 test {level}, 필드 정답", "official-scan.json real_blank_forms.test", v["correct"], v["fields"])

# 6. External CAD, VLM, title blocks
nist = load("nist-cad.json")["metrics"]
add("NIST STEP 11쌍, Top-1", "nist-cad.json", round(nist["top1_accuracy"] * nist["paired_cases"]), nist["paired_cases"])
vis = load("vision.json")["metrics"]
add("VLM 이미지 읽기, 구조화 완료", "vision.json", vis["completed"], vis["attempts"])
add("VLM 이미지 읽기, 필드 정답", "vision.json", round(vis["field_accuracy"] * 36), 36)
tb = load("title-block-real.json")["summary"]
for field in ("drawing_number", "material"):
    add(f"실제 도면 표제란 OCR, {field}", "title-block-real.json", tb["correct"][field], tb["expected"][field])

# 7. Prompt comparison: the family bootstrap intervals already in the record, and what one item is worth
pl, pc = load("prompts-local.json"), load("prompts-claude.json")
add("판정 프롬프트 비교, Qwen3-1.7B dev 36문항", "prompts-local.json", resolution_per_item=round(1 / 36, 4),
    accuracy={v: pl["dev"][v]["accuracy"] for v in pl["dev"]}, accuracy_ci95={v: pl["dev"][v].get("accuracy_ci95") for v in pl["dev"]},
    versus_baseline=pl["versus_baseline"], note="family 단위 bootstrap 구간(기록에 있던 값). 36문항에서 한 문항은 2.8%p")
add("판정 프롬프트 비교, Claude Haiku 4.5 dev 36문항", "prompts-claude.json", resolution_per_item=round(1 / 36, 4),
    accuracy={v: pc["dev"][v]["accuracy"] for v in pc["dev"]}, note="네 프롬프트 모두 36/36으로 천장: 차이를 잴 수 없음")

record = {
    "created_at": datetime.now(timezone.utc).isoformat(),
    "code_sha": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
    "method": {"ci95": "Wilson score interval; 0/n과 n/n에서도 유한한 폭", "repeats": "pass^k = 모든 반복에서 맞은 사례의 비율",
               "bootstrap": "사례를 복원 추출(반복은 사례와 함께 이동), 1000회, 고정 seed 1701",
               "caveat": "같은 질의의 반복과 같은 실행 안의 verdict는 독립 표본이 아니므로 사례·실행 단위로 셈"},
    "entries": entries,
}
(E / "statistics.json").write_text(json.dumps(record, ensure_ascii=False, indent=1))
for e in entries:
    if "total" in e:
        print(f"| {e['name']} | {e['correct']}/{e['total']} ({e['share']:.1%}) | {e['ci95'][0]:.1%} ~ {e['ci95'][1]:.1%} |"
              + (f" pass^{e['repeats']['k']} {e['repeats']['pass_power_k']:.1%} ({e['repeats']['cases_all_correct']}/{e['repeats']['cases']}) |" if "repeats" in e else ""))
    elif "recall_at_5" in e:
        print(f"| {e['name']} | Recall@5 {e['recall_at_5']:.1%} ({e['recall_at_5_ci95'][0]:.1%} ~ {e['recall_at_5_ci95'][1]:.1%}) | nDCG@10 {e['ndcg_at_10']:.1%} ({e['ndcg_at_10_ci95'][0]:.1%} ~ {e['ndcg_at_10_ci95'][1]:.1%}) | {e['rows']}행 {e['cases']}사례 |")
print("written", E / "statistics.json", len(entries), "entries")
