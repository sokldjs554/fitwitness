# 평가 게이트 — API 없이 매 커밋 측정하는 것

유료 실측(Claude·Qwen·E5·OpenCLIP)은 별도 workflow에서 승인 후 실행합니다. 그 사이에 코드가 조용히 나빠지는 것을 막기 위해, 모델 가중치와 API 키 없이 실제 PostgreSQL만으로 돌 수 있는 부분을 CI에서 매 커밋 측정하고 **커밋된 베이스라인과 비교해 하락하면 실패**시킵니다.

```bash
uv run python -m fitwitness.evaluation.gate --output artifacts/gate --baseline docs/evaluation/gate-baseline.json
# 의도적으로 베이스라인을 갱신할 때만
uv run python -m fitwitness.evaluation.gate --output artifacts/gate --baseline docs/evaluation/gate-baseline.json --write-baseline
```

## 측정 항목

| 스위트 | 무엇을 | 지표 |
|---|---|---|
| `rules_graph` | FW-F000 도면 5개에 실제 LangGraph(rules provider) 실행, 생성기 gold와 대조 | 판정 정확도 (`== 1.0`) |
| `lexical` | 고정 test split 6 family × 질의 유형(exact / id_variant / id_typo / paraphrase), 도번+BM25 채널만 | 유형별 Recall@5, nDCG@5, p50 지연 |
| `geometry` | test+dev 12 family의 STEP 60개를 CadQuery로 읽어 특징 거리로 순위. 각 family의 기준 모델이 자기 family를 찾는지 | same-family@1, same-family@3 |
| `control` | `lexical`을 도번 정규화를 끈 상태(`id_matching="token"`)로 다시 실행 | 정상 − 열화 차이. **차이가 작으면 벤치마크가 열화를 감지하지 못하는 것이므로 실패** |

질의 유형 `id_variant`(공백·밑줄·소문자·O/0 변형)와 `id_typo`(한 자리가 틀려 존재하지 않는 도번)는 `evaluation/retrieval.py`의 `make_cases`에 추가됐고(protocol `retrieval-v3`), 유료 검색 실측에서도 같은 유형이 측정됩니다. 이전 v2 결과는 그대로 보존되며 v3와 직접 비교하지 않습니다.

## 임계치

`evaluation/gate.py`의 `THRESHOLDS`에 있습니다. 절대 하한(`min`), 베이스라인 대비 허용 하락폭(`max_drop`), 정확히 일치해야 하는 값(`equals`) 세 종류입니다. 리포트는 `artifacts/gate/report.json`, 판정은 `artifacts/gate/gate.json`, 질의별 원시 순위는 `artifacts/gate/rows.json`에 남습니다.

## 해석 범위

- 합성 도면 150개·고정 split 기준입니다. 산업 데이터 성능이 아닙니다.
- 의미·이미지 채널과 LLM Agent 품질은 이 게이트에 없습니다. 그것은 유료 실측 workflow의 몫입니다.
- `paraphrase` 유형은 lexical 채널로는 원래 낮습니다. 하한 없이 하락폭만 감시합니다.
