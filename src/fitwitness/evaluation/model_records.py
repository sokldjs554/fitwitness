"""The latest model measurements, summarised for the laboratory page.

Every number is read from a committed record under docs/evaluation; nothing is measured here. The summary is
written to docs/evaluation/model-records.json (``python -m fitwitness.evaluation.model_records``), served by
/api/evaluations/models, and scripts/verify-research.py checks that the committed file still equals what the
records give. The user-test card is the one exception: its numbers come from the written records in
docs/user-test, which have no machine-readable form."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
E = ROOT / "docs/evaluation"
OUT = E / "model-records.json"
REPO = "https://github.com/sokldjs554/fitwitness/blob/main/"
SOURCES = [
    "agent-trajectory",
    "claims-model",
    "claims-audit",
    "query-understanding",
    "shell-search-model",
    "title-block-vlm",
    "title-block-vlm-prompts",
    "openai",
    "agent-trajectory-repeats",
    "claims-model-decides",
]
MODES = {"fixed": "단일 계획", "react": "반복 계획", "fitwitness": "탐색 + 반례 검토"}


def _load(name):
    return json.loads((E / f"{name}.json").read_text())


def _pct(x):
    return f"{x * 100:.1f}%"


def _usd(x):
    return round(float(x), 6)


def _link(label, path):
    return {"label": label, "href": REPO + path}


def trajectory():
    t = _load("agent-trajectory")
    rows, last = [], t["runs"][-1]
    for i, run in enumerate(t["runs"], 1):
        cells = [f"{i}차 · {run['started_at'][:10]}", run["code_sha"][:7]]
        for mode in MODES:
            m = run["by_mode"].get(mode)
            cells.append(f"{m['completed']}/{m['runs']}" if m else "—")
        cells.append(f"${run['spent_usd']:.4f}")
        rows.append(cells)
    model_rows = [r for r in last["rows"] if r["provider"] != "rules"]
    completed = sum(last["by_mode"][m]["completed"] for m in MODES)
    runs = sum(last["by_mode"][m]["runs"] for m in MODES)
    sample = next(r for r in model_rows if r["mode"] == "fitwitness")
    return {
        "id": "trajectory",
        "title": "실제 모델이 고르는 경로",
        "posting": "주요업무 1 · Agentic System Design",
        "question": "모델이 도구를 고르고 멈추는 그래프가 실제 모델로 끝까지 도는가",
        "model": t["model"],
        "measured": t["runs"][0]["started_at"][:10]
        + ("" if last["started_at"][:10] == t["runs"][0]["started_at"][:10] else " ~ " + last["started_at"][:10]),
        "cost_usd": _usd(t["total_spent_usd"]),
        "headline": [
            {"label": f"{len(t['runs'])}차 세 구조 완료", "value": f"{completed}/{runs}"},
            {
                "label": f"{len(t['runs'])}차 판정이 검증기와 일치",
                "value": f"{sum(r['correct'] for r in model_rows)}/{sum(r['total'] for r in model_rows)}",
            },
            {"label": "1차 탐색 + 반례 검토 완료", "value": "{completed}/{runs}".format(**t["runs"][0]["by_mode"]["fitwitness"])},
        ],
        "table": {
            "caption": "회차별 완료한 실행 (test 6개 family, 구조마다 한 번)",
            "columns": ["회차", "코드", *MODES.values(), "비용"],
            "rows": rows,
        },
        "path": {
            "caption": f"{len(t['runs'])}차 탐색 + 반례 검토 한 건 ({sample['family_id']})",
            "tools": sorted(set(sample["tools"])),
            "planner_tools": len(sample["tools"]) - sample["challenger_tools"],
            "challenger_tools": sample["challenger_tools"],
            "model_calls": sample["model_calls"],
            "stop": sample["stop"],
            "verdicts": f"{sample['correct']}/{sample['total']}",
        },
        "fixes": [w for f in t["fixes"] for w in f["what"]],
        "caveats": t["limitations"][:4],
        "links": [_link("기록 JSON", "docs/evaluation/agent-trajectory.json"), _link("설명", "docs/evaluation/README.md")],
    }


def claims_model():
    c = _load("claims-model")
    rows, inputs = (
        [],
        {
            "text layer": "텍스트 층 1차",
            "medium scan (OCR for both readers)": "스캔 medium (두 판독기 모두 OCR)",
            "text layer, repeat 2": "텍스트 층 2차",
            "text layer, repeat 3": "텍스트 층 3차",
        },
    )
    for b in c["batches"]:
        m, r = b["headline"]["model"], b["headline"]["rules"]
        rows.append(
            [
                inputs.get(b["input"], b["input"]),
                _pct(m["decision_accuracy"]),
                _pct(r["decision_accuracy"]),
                str(m["wrong_pay_count"]),
                str(m["wrong_deny_count"]),
                f"${m['cost_usd']:.4f}",
            ]
        )
    rep = c["repeats"]
    scan = next(b for b in c["batches"] if "scan" in b["input"])["headline"]
    after = _load("claims-model-grounding")
    return {
        "id": "claims-model",
        "title": "청구 서류를 모델이 읽는 경로",
        "posting": "자격 3 · Claude로 실제 동작하는 앱",
        "question": "청구 test 60건을 규칙 판독기와 같은 바이트에서 모델이 읽게 하면 결정이 나아지는가",
        "model": c["model"],
        "measured": c["date"],
        "cost_usd": _usd(float(c["spent_usd_total"]) + float(after["spent_usd_total"])),
        "headline": [
            {
                "label": "텍스트 층 세 번, 결정 정확도",
                "value": " · ".join(_pct(x) for x in rep["decision_accuracy_per_run"]),
                "note": f"규칙 {_pct(rep['rules_per_run'][0])}",
            },
            {"label": "세 번 모두 맞은 결정", "value": f"{rep['correct_in_every_run']}/{rep['cases']}"},
            {
                "label": "잘못 지급 · 잘못 부지급 (텍스트 세 번)",
                "value": f"{sum(rep['wrong_pay_per_run'])} · {sum(rep['wrong_deny_per_run'])}",
            },
            {
                "label": "값을 인용한 줄과 대조하게 한 뒤 두 번(2026-10-30)",
                "value": " · ".join(_pct(r["decision_accuracy"]) for r in after["runs"]),
                "note": f"규칙 {_pct(after['runs'][0]['rules_decision_accuracy'])}, 잘못 지급 0. 앞선 세 번의 우위는 흐려진 줄을 고쳐 읽은 값을 받아들인 데서 났음",
            },
            {
                "label": "스캔 한 번, 결정 정확도",
                "value": _pct(scan["model"]["decision_accuracy"]),
                "note": f"규칙 {_pct(scan['rules']['decision_accuracy'])}, 잘못 부지급 {scan['model']['wrong_deny_count']}건",
            },
        ],
        "table": {
            "caption": "묶음별 (합성 청구 test 60건)",
            "columns": ["입력", "모델 결정 정확도", "규칙", "모델 잘못 지급", "모델 잘못 부지급", "비용"],
            "rows": rows,
        },
        "caveats": [
            "청구와 약관은 합성입니다.",
            "앞의 세 번(93.3·90.0·95.0%)은 근거 검사가 '인용한 줄이 서류에 있다'까지만 볼 때의 값입니다. 값이 그 줄과 맞는지까지 보게 한 뒤의 두 번은 대조 실행 없이 잰 것이라 원인을 코드 변경 하나로 못 박지 못합니다.",
            "스캔에서 나온 잘못 부지급 1건은 규칙 경로에 있던 보호를 모델 경로로 옮겨 고쳤습니다(고친 뒤 스캔은 다시 재지 않음).",
            "돈이 움직이는 결정은 결정적 규칙이 내리고, 모델은 서류에서 읽은 값을 인용과 함께 제안만 합니다.",
        ],
        "links": [_link("기록 JSON", "docs/evaluation/claims-model.json"), _link("설명", "docs/CLAIMS.md")],
    }


def claims_audit():
    a = _load("claims-audit")
    degraded = next(r for r in a["runs"] if r["mode"] == "degraded")
    clean = next(r for r in a["runs"] if r["mode"] == "clean")
    across, subtle = a["repeats"]["across_three_runs"], a["subtle_control"]["run"]
    flags = " · ".join(str(x) for x in across["false_flags_per_run"])
    return {
        "id": "claims-audit",
        "title": "지급 직전 모델 감사",
        "posting": "주요업무 1 · 도구로 Task 수행, 반례 검토",
        "question": "약해진 규칙이 지급으로 보낸 청구를 모델이 담당자에게 돌려보내는가",
        "model": a["model"],
        "measured": a["date"],
        "cost_usd": _usd(a["spent_usd_total"]),
        "headline": [
            {"label": "잘못된 지급을 세운 것 (세 번 모두)", "value": f"{across['wrong_payments_caught_in_every_run']}/{across['of']}"},
            {"label": "옳은 지급을 세운 것 (세 번)", "value": f"{flags} / {degraded['correct_payments']}"},
            {"label": "멀쩡한 규칙의 지급을 세운 것", "value": f"{clean['false_flags']}/{clean['correct_payments']}"},
            {"label": "사실 절이 말해 주지 않는 고장", "value": f"{subtle['caught']}/{subtle['wrong_payments']}"},
        ],
        "table": None,
        "caveats": [
            "청구와 약관, 약해진 규칙의 고장 세 가지는 합성이고 지원자가 고른 것입니다.",
            "감사자는 담당자 확인으로 보내는 이유만 더할 수 있고, 지급을 결정하지 않습니다.",
            "담당자가 그 이유에 동의하는지는 재지 않았습니다.",
        ],
        "links": [_link("기록 JSON", "docs/evaluation/claims-audit.json"), _link("설명", "docs/CLAIMS.md")],
    }


def query_understanding():
    q = _load("query-understanding")
    batches = {b["name"]: b for b in q["batches"]}
    test, kind = batches["test"], batches["test-kind"]
    u = test["understanding"]
    s, k = test["retrieval"]["summary"], kind["retrieval"]["summary"]
    rep, free, prac = q["repeats"], q["freeform"], q["practitioner"]
    return {
        "id": "query-understanding",
        "title": "검색 질의를 모델이 읽어 조건으로",
        "posting": "주요업무 2·3 · 의도 파악, 사내 검색",
        "question": "흐트러진 검색 문장에서 종류·치수 구간·재질을 패턴보다 잘 읽고, 그것이 검색을 낫게 하는가",
        "model": q["model"],
        "measured": q["date"],
        "cost_usd": _usd(q["spent_usd_total"]),
        "headline": [
            {
                "label": "test 60문장을 다 맞게 읽은 것",
                "value": " · ".join(f"{x}/60" for x in rep["all_correct_per_run"]),
                "note": f"패턴 {u['rules']['all_correct']}/{u['rules']['queries']}",
            },
            {"label": "틀 밖 30문장", "value": f"{free['model_all_correct']}/30", "note": f"패턴 {free['rules_all_correct']}/30"},
            {
                "label": "현업자 검색어 10개",
                "value": f"{prac['before']['model_all_correct']}/10 → {prac['after']['model_all_correct']}/10",
                "note": "프롬프트를 고친 뒤, 같은 세트라 held-out 아님",
            },
            {"label": "복합 검색 Recall@5", "value": f"{_pct(s['rrf']['recall_at_5'])} → {_pct(k['model']['recall_at_5'])}"},
        ],
        "table": {
            "caption": "test 60문장의 Recall@5",
            "columns": ["순위", "Recall@5"],
            "rows": [
                [s["rrf"]["label"], _pct(s["rrf"]["recall_at_5"])],
                [s["rules"]["label"], _pct(s["rules"]["recall_at_5"])],
                [s["model"]["label"], _pct(s["model"]["recall_at_5"])],
                ["모델이 읽은 종류로 후보 풀과 정렬까지", _pct(k["model"]["recall_at_5"])],
            ],
        },
        "caveats": [
            "test 60문장은 틀 10개 × 6 family로 만든 것이고, 틀 밖 30문장도 지원자가 썼습니다.",
            "모델의 제안은 질의에서 따온 인용이 있을 때만 받습니다.",
            "현업자 검색어 10개는 회사 정보 없이 적은 것이고, 그 세트로 고장을 찾아 고쳤으므로 고친 뒤의 10/10은 held-out이 아닙니다.",
        ],
        "links": [_link("기록 JSON", "docs/evaluation/query-understanding.json"), _link("설명", "docs/RETRIEVAL.md")],
    }


def shell_search():
    s = _load("shell-search-model")
    test = next(r for r in s["runs"] if r["label"].startswith("test"))
    before = next(r for r in s["runs"] if "before" in r["label"])
    after = next(r for r in s["runs"] if "after" in r["label"])
    arms = test["summary"]
    typed = [r for r in test["rows"] if r["arm"] == "shell"]
    used = sum(1 for r in typed if r["commands"])
    # The dev run after the change skipped one query at its ceiling; compare the two on the queries both ran.
    ran = {r["case"] for r in after["rows"] if r["arm"] == "shell"}

    def dev(run, key):
        return sum(r[key] for r in run["rows"] if r["arm"] == "shell" and r["case"] in ran)

    labels = {"rules": "규칙", "ranked": "모델, 셸 끔", "shell": "모델, 셸 켬"}
    return {
        "id": "shell-search",
        "title": "명령으로 검색하는 도구 search_shell",
        "posting": "주요업무 2 · 터미널 명령어로 검색",
        "question": "순위 검색이 다 못 돌려주는 집합 질의에서, 모델에게 셸 같은 도구를 주면 더 찾는가",
        "model": test["protocol"]["model"],
        "measured": s["measured_on"],
        "cost_usd": _usd(s["spent_usd_total"]),
        "headline": [
            {
                "label": "test 정답 중 찾은 것",
                "value": f"{arms['ranked']['found']} → {arms['shell']['found']} / {arms['shell']['answer']}",
                "note": f"셸 끔 → 켬, 규칙 {arms['rules']['found']}",
            },
            {"label": "틀린 일치", "value": str(sum(arms[a]["false_matches"] for a in labels))},
            {"label": "셸을 켜고 명령을 친 실행", "value": f"{used}/{len(typed)}"},
            {
                "label": f"dev 질의 {len(ran)}개, 문맥을 고친 전후",
                "value": f"{dev(before, 'found')} → {dev(after, 'found')} / {dev(after, 'answer')}",
            },
        ],
        "table": {
            "caption": "test 집합 질의 8개 × 합성 도면 30장",
            "columns": ["방식", "찾은 정답", "다 찾은 질의", "틀린 일치"],
            "rows": [
                [
                    labels[a],
                    f"{arms[a]['found']}/{arms[a]['answer']}",
                    f"{arms[a]['complete_sets']}/{arms[a]['runs']}",
                    str(arms[a]["false_matches"]),
                ]
                for a in labels
            ],
        },
        "caveats": s["limitations"],
        "links": [_link("기록 JSON", "docs/evaluation/shell-search-model.json"), _link("설명", "docs/SHELL-SEARCH.md")],
    }


def title_block_vlm():
    t = _load("title-block-vlm")
    prompts = _load("title-block-vlm-prompts")
    dev = prompts["dev_scores"]
    best_label = max(dev["label"].values(), key=lambda s: s["merit"])["merit"]
    recorded = prompts["recorded_baseline_on_dev_2026_10_25"]["crop"]["merit"]
    page, crop = t["summary"]["all"]["page"], t["summary"]["all"]["crop"]
    tess = [t["tesseract_records"]["holdout1"]["before the change"], t["tesseract_records"]["holdout2"]["after the change"]]
    fields = {"drawing_number": "도번", "material": "재질", "scale": "비례"}

    def tsum(key, field=None):
        return sum(x[key][field] if field else x[key] for x in tess)

    def wrong(r):
        return r["wrong_values_reported"] + r["readings_where_the_block_has_no_such_cell"]

    rows = [
        [
            label,
            f"{page['correct'][f]}/{page['expected'][f]}",
            f"{crop['correct'][f]}/{crop['expected'][f]}",
            f"{tsum('correct', f)}/{tsum('expected', f)}",
        ]
        for f, label in fields.items()
    ]
    rows.append(
        [
            "틀리거나 칸이 없는데 읽은 값",
            str(wrong(page)),
            str(wrong(crop)),
            str(tsum("wrong_values_reported") + tsum("readings_where_the_block_has_no_such_cell")),
        ]
    )
    return {
        "id": "title-block-vlm",
        "title": "실제 도면 표제란을 VLM으로",
        "posting": "우대 1 · VLM",
        "question": "사람이 라벨을 확인한 실제 도면 61장의 표제란을 Claude가 Tesseract보다 잘 읽는가",
        "model": t["runs"][0]["protocol"]["model"],
        "measured": t["measured_on"],
        "cost_usd": _usd(t["spent_usd_total"]),
        "headline": [
            {
                "label": "쪽 읽기 도번 · 재질 · 비례",
                "value": " · ".join(f"{page['correct'][f]}/{page['expected'][f]}" for f in fields),
                "note": "Tesseract " + " · ".join(f"{tsum('correct', f)}/{tsum('expected', f)}" for f in fields),
            },
            {
                "label": "틀리거나 칸이 없는데 읽은 값",
                "value": str(wrong(page)),
                "note": f"Tesseract {tsum('wrong_values_reported') + tsum('readings_where_the_block_has_no_such_cell')}",
            },
        ],
        "table": {
            "caption": f"실제 도면 {t['summary']['all']['drawings']}장 (공개 교재 쪽, 라벨은 사람이 하나씩 확인)",
            "columns": ["칸", "쪽 전체", "찾은 표제란을 잘라서", "Tesseract"],
            "rows": rows,
        },
        "caveats": [
            f"{prompts['measured_on']} 프롬프트 실험: 칸의 라벨을 값과 함께 인용하게 한 변형이 dev 27장에서 기준선을 못 이겨"
            f"(점수 {best_label} 대 {prompts['chosen']['merit']}) 규칙대로 test는 읽지 않았습니다. 같은 기준선 프롬프트의 자른 표제란"
            f" 점수가 이틀 사이 {recorded}에서 {dev['baseline']['crop']['merit']}로 흔들려, 한 번씩 돌려서는 프롬프트 순위를 매길 수 없습니다"
            f"(${prompts['spent_usd_total']:.2f}).",
            "한 공개 벤치마크(중국 기계 교재)의 쪽을 한 번씩 읽은 것입니다.",
            "더 많이 읽는 만큼 틀린 값도 늘어, 읽은 값은 확정된 사실이 아니라 '확인 필요'로만 씁니다.",
            "도면 이미지는 저장소에 넣지 않았고, 해시만 커밋했습니다.",
        ],
        "links": [
            _link("기록 JSON", "docs/evaluation/title-block-vlm.json"),
            _link("프롬프트 실험", "docs/evaluation/title-block-vlm-prompts.json"),
            _link("설명", "docs/TITLE-BLOCK.md"),
        ],
    }


def people_and_real_data():
    return {
        "id": "people",
        "title": "사람에게 쓰게 해 본 것과 실제 데이터의 범위",
        "posting": "실제 데이터 · 현업 검증의 범위",
        "question": "합성 데이터 밖에서 무엇을 확인했고, 무엇은 아직 없는가",
        "model": None,
        "measured": "2026-10-14 ~ 10-28",
        "cost_usd": None,
        "headline": [
            {"label": "사용자 테스트", "value": "3명", "note": "지인: 비전문가 1, 생산기술 현업 5년 1, 보험 현업 1"},
            {
                "label": "비전문가 과제",
                "value": "4/5",
                "note": "기록이 스스로 과제 3은 기준대로면 X에 가깝다고 적어 엄격히는 3/5. 막힌 곳은 모두 화면이 설명하지 않은 데서 왔고 고침",
            },
            {"label": "현업자 검색어 10개의 치수 조건", "value": "0개", "note": "제가 쓴 90문장이 못 찾은 고장을 드러냄"},
        ],
        "table": {
            "caption": "합성이 아닌 데이터",
            "columns": ["무엇", "규모", "쓴 곳"],
            "rows": [
                ["공개 기계 도면(교재 쪽), 라벨은 사람이 확인", "61장 + 처음 12장", "표제란 OCR·VLM"],
                ["현업자가 실제로 치는 검색어(회사 정보 없이)", "10개", "질의 읽기"],
                ["한국어 공공문서", "12쪽", "OCR"],
                ["실제 법정 서식(빈 양식)", "2종", "서식 칸 읽기"],
                ["NIST 공개 STEP 모델", "11쌍", "형상 검색"],
                ["보험사·제조사의 실제 고객 문서", "없음", "—"],
            ],
        },
        "quote": "근거를 바로 보여주는 건 좋은데, 이 결과만 보고 써도 되는 부품이라고 생각하면 곤란할 것 같습니다.",
        "caveats": [
            "사용자 테스트이지 고객 데이터도 현장 검증도 아닙니다.",
            "현업자의 말을 듣고 후보 카드에 품명과 승인 상태를 올리고, 조건 일치가 제작 가능을 뜻하지 않는다는 문구를 넣었습니다.",
        ],
        "links": [_link("과정", "docs/USER-TEST.md"), _link("기록", "docs/user-test")],
    }


def openai_evidence():
    o = _load("openai")
    s, m = o["summary"], o["model"]
    claude = o["comparison"]["claude_haiku_4_5"]
    qwen = o["comparison"]["qwen3_1_7b_local"]
    return {
        "id": "openai-evidence",
        "title": "같은 서류 판정 24사례를 OpenAI 모델로",
        "posting": "자격요건 · OpenAI API와 Claude로 만든 동작 앱",
        "question": "Claude로 잰 것과 같은 판정 사례를 OpenAI 모델도 같은 규칙(인용이 없으면 일치로 안 침)으로 푸는가",
        "model": m["id"],
        "measured": o["date"],
        "cost_usd": _usd(s["cost_usd"]),
        "headline": [
            {
                "label": "끝난 판정",
                "value": f"{s['completed']}/{s['calls']}",
                "note": "2026-10-29에는 잔액 소진(HTTP 429)으로 17회가 비었고, 충전 뒤 2026-10-30에 72회를 모두 다시 돌림",
            },
            {
                "label": "끝난 판정 중 정답",
                "value": f"{s['correct']}/{s['completed']}",
                "note": f"95% 구간 {_pct(s['wilson_95_lower_for_72_of_72'])}부터(한 사례의 세 번은 독립이 아니라 실제보다 좁음). 천장이라 모델 우열은 못 가림",
            },
            {
                "label": "틀린 일치",
                "value": f"{s['false_match']}/{s['completed_non_match_cases']}",
                "note": "비일치 사례 42건 중. 모든 인용은 서류의 사실 id",
            },
            {
                "label": "판정 한 번 지연 중앙값",
                "value": f"{s['latency_ms_median'] / 1000:.1f}초",
                "note": f"p95 {s['latency_ms_p95'] / 1000:.1f}초",
            },
        ],
        "table": {
            "caption": "같은 24사례 × 3회(test 분할)",
            "columns": ["모델", "결과"],
            "rows": [
                [
                    f"{m['id']} (OpenAI)",
                    f"{s['correct']}/{s['completed']} 정답, 틀린 일치 {s['false_match']}, ${s['cost_usd']:.4f}",
                ],
                ["Claude Haiku 4.5", claude],
                ["Qwen3-1.7B 로컬", qwen],
            ],
        },
        "caveats": o["limitations"],
        "links": [_link("기록 JSON", "docs/evaluation/openai.json"), _link("설명", "docs/evaluation/README.md")],
    }


def _repeat_counts(run, mode):
    """How many repeats a mode got: the same for every case, or a range when a ceiling stopped the run part-way."""
    per = (run.get("runs_per_repeat") or {}).get(mode)
    if not per:
        return f"{run['by_mode'][mode]['pass_k']['k']}회"
    n = len(per)
    return f"{n}회" if len(set(per.values())) == 1 else f"{n - 1}~{n}회"


def trajectory_repeats():
    t = _load("agent-trajectory-repeats")
    by_id = {r.get("run_id"): r for r in t["runs"]}
    runs = [
        ("gpt-5-mini", by_id[41032282374], by_id[41032292254]),
        ("Claude Haiku 4.5", by_id[41154974238], by_id[41155731372]),
    ]
    rows = []
    for name, easy, hard in runs:
        for mode, label in MODES.items():
            e, h = easy["by_mode"][mode], hard["by_mode"][mode]
            done = hard["completed_runs_only"][mode]
            rows.append(
                [
                    f"{name} · {label}",
                    f"{e['runs_fully_correct']}/{e['runs']}",
                    f"{h['runs_fully_correct']}/{h['runs']}",
                    _pct(h["accuracy"]),
                    _pct(done["verdicts_right"] / done["verdicts"]),
                    f"{h['runs'] - h['completed']}/{h['runs']}",
                    f"{h['pass_k']['cases_all_correct']}/{h['pass_k']['cases']} ({_repeat_counts(hard, mode)})",
                    f"${e['cost_usd'] + h['cost_usd']:.3f}",
                ]
            )

    def total(run, key):
        return sum(m["failure_classes"][key] for m in run["by_mode"].values() if "failure_classes" in m)

    gpt_hard, claude_hard = runs[0][2], runs[1][2]
    hard_right = lambda run: " · ".join(f"{run['by_mode'][m]['runs_fully_correct']}" for m in MODES)  # noqa: E731
    hard_runs = lambda run: "·".join(str(run["by_mode"][m]["runs"]) for m in MODES)  # noqa: E731
    return {
        "id": "trajectory-repeats",
        "title": "구조 비교를 반복해서, 더 어려운 설정에서, 두 모델로",
        "posting": "주요업무 1 · Agentic System Design",
        "question": "세 구조의 차이가 반복해도 남는가, 후보가 많아지면 모델이 모든 도면을 열어 보는가, 그 답이 모델이 바뀌어도 같은가",
        "model": f"{runs[0][1]['model']} · {runs[1][1]['model']}",
        "measured": f"{t['date']} ~ {t['date_claude'][5:]}",
        "cost_usd": _usd(t["total_spent_usd"]),
        "headline": [
            {
                "label": "쉬운 설정에서 모두 정답인 실행",
                "value": "108/108",
                "note": "두 모델 × 세 구조 × 6 family × 3회씩. 천장이라 구조를 가를 수 없음",
            },
            {
                "label": "어려운 설정, gpt-5-mini: 모두 정답인 실행",
                "value": f"{hard_right(gpt_hard)} / 36씩",
                "note": "단일 · 반복 · 탐색+반례 (도면 30장, 후보 12개, 재료 2종). 일찍 멈추거나 계획 형식을 못 지킨 실행이 많음",
            },
            {
                "label": "어려운 설정, Claude Haiku 4.5: 모두 정답인 실행",
                "value": f"{hard_right(claude_hard)} / {hard_runs(claude_hard)}",
                "note": "같은 설정. 단일 계획은 구조상 12개 중 8개만 열 수 있어 0. 상한 $0.9에서 멈춰 반복은 2회(탐색+반례는 1~2회)",
            },
            {
                "label": "조회하고도 틀린 판정",
                "value": str(total(gpt_hard, "inspected_wrong") + total(claude_hard, "inspected_wrong")),
                "note": f"두 모델 모두. 틀린 판정은 조회 안 한 도면(gpt-5-mini {total(gpt_hard, 'not_inspected')}개, Claude {total(claude_hard, 'not_inspected')}개)이거나 끝나지 못한 실행",
            },
        ],
        "table": {
            "caption": "케이스(family × 재료)마다 반복. 쉬운 설정은 6케이스 × 3회, 어려운 설정은 12케이스(gpt-5-mini 3회, Claude 2회 안팎). 어려운 설정의 실행 수는 모델마다 다름",
            "columns": [
                "모델 · 구조",
                "쉬운: 모두 정답",
                "어려운: 모두 정답",
                "어려운: 판정 정확도(전체)",
                "어려운: 끝난 실행만",
                "어려운: 실패한 실행",
                "어려운: 모든 반복에서 정답인 케이스",
                "비용(쉬운+어려운)",
            ],
            "rows": rows,
        },
        "caveats": [t["limitations"][i] for i in (0, 1, 2, 6)],
        "links": [
            _link("기록 JSON", "docs/evaluation/agent-trajectory-repeats.json"),
            _link("설명", "docs/evaluation/README.md"),
        ],
    }


def claims_decides():
    d = _load("claims-model-decides")
    gs, g = d["summary"], d["summary"]["model"]
    cs = d["claude"]["summary"]
    c, r = cs["model"], cs["rules_engine_on_the_same_claims"]
    assert r == gs["rules_engine_on_the_same_claims"], "the rules path is deterministic: both runs must have the same rules numbers"
    ci = cs["repeats"]["wrong_pay_wilson95_if_answers_were_independent"]
    gname, cname = d["model"]["id"], d["claude"]["model"]["id"]

    def line(label, m):
        return [
            label,
            _pct(m["decision_accuracy"]),
            _pct(m["auto_rate"]),
            f"{m['wrong_pay_count']}/{m['answers']}",
            _pct(m["review_rate"]),
            f"{m['overpaid_krw']:,}원",
        ]

    return {
        "id": "claims-decides",
        "title": "모델이 최종 판정까지 내리게 하면",
        "posting": "주요업무 · 에이전트가 돈이 움직이는 결정을 어디까지 맡는가",
        "question": "제품에서는 하지 않는 설계로, 모델이 지급 여부와 금액까지 내리면 잘못된 지급이 나오는가",
        "model": f"{gname} · {cname}",
        "measured": f"{d['date']} ~ {d['date_claude'][5:]}",
        "cost_usd": _usd(gs["spent_usd"] + cs["spent_usd"]),
        "headline": [
            {
                "label": "잘못된 지급, Claude Haiku 4.5",
                "value": f"{c['wrong_pay_count']}/{c['answers']}",
                "note": f"청구 60건을 두 번씩 물은 120번 중. 95% 구간 {_pct(ci[0])}~{_pct(ci[1])}(두 번의 답은 독립이 아님). 규칙 경로는 {r['wrong_pay_count']}/{r['answers']}",
            },
            {
                "label": "잘못된 지급, gpt-5-mini",
                "value": f"{g['wrong_pay_count']}/{g['answers']}",
                "note": "같은 청구, 같은 프롬프트. 추론 모델(low)과 확장 사고 없는 모델이라 두 모델의 우열을 가린 숫자는 아님",
            },
            {
                "label": "잘못 나간 금액",
                "value": f"{c['overpaid_krw']:,}원",
                "note": f"Claude 기준 120번의 합. gpt-5-mini {g['overpaid_krw']:,}원, 규칙 경로 0원",
            },
        ],
        "table": {
            "caption": "청구 test 60건. 정답은 claims/synth.py의 gold(참값에 규칙 엔진을 돌린 결과). 모델 경로는 청구당 두 번 물은 120번, 규칙 경로는 60번",
            "columns": ["경로", "결정 정확도", "자동 지급률", "잘못된 지급", "담당자 확인 비율", "잘못 나간 금액"],
            "rows": [
                line("규칙 판독기 + 엔진", r),
                line(f"모델이 판정까지 ({gname})", g),
                line(f"모델이 판정까지 ({cname})", c),
            ],
        },
        "caveats": [d["limitations"][0], d["claude"]["findings"][2], d["limitations"][1], d["limitations"][2]],
        "links": [_link("기록 JSON", "docs/evaluation/claims-model-decides.json"), _link("설명", "docs/CLAIMS.md")],
    }


def build():
    cards = [
        trajectory(),
        claims_model(),
        claims_audit(),
        query_understanding(),
        shell_search(),
        title_block_vlm(),
        openai_evidence(),
        trajectory_repeats(),
        claims_decides(),
        people_and_real_data(),
    ]
    paid = [c["cost_usd"] for c in cards if c["cost_usd"] is not None]
    # the prompt experiment is a source of the title-block card, not a card of its own; its spend belongs in the total
    paid.append(_usd(_load("title-block-vlm-prompts")["spent_usd_total"]))
    return {
        "status": "measured",
        "what": "2026-10-22 ~ 10-31에 실제 모델(Claude Haiku 4.5, OpenAI gpt-5-mini)로 잰 것과 사람에게 쓰게 해 본 것. 모두 커밋된 기록에서 옮겼고, 이 화면을 열 때 모델을 부르지 않습니다.",
        "sources": {name: hashlib.sha256((E / f"{name}.json").read_bytes()).hexdigest()[:16] for name in SOURCES},
        "spent_usd": round(sum(paid), 6),
        "cards": cards,
    }


def render():
    return json.dumps(build(), ensure_ascii=False, indent=1) + "\n"


if __name__ == "__main__":
    OUT.write_text(render())
    print("written", OUT.relative_to(ROOT))
