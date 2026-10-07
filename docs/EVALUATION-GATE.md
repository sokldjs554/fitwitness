# 평가 게이트 — API 없이 매 커밋 측정하는 것

유료 실측(Claude·Qwen·E5·OpenCLIP)은 별도 workflow에서 승인 후 실행합니다. 그 사이에 코드가 조용히 나빠지는 것을 막기 위해, 모델 가중치와 API 키 없이 실제 PostgreSQL만으로 돌 수 있는 부분을 CI에서 매 커밋 측정하고 **커밋된 베이스라인과 비교해 하락하면 실패**시킵니다.

```bash
uv run python -m fitwitness.evaluation.gate --output artifacts/gate --baseline docs/evaluation/gate-baseline.json
# 의도적으로 베이스라인을 갱신할 때만
uv run python -m fitwitness.evaluation.gate --output artifacts/gate --baseline docs/evaluation/gate-baseline.json --write-baseline
# 실행 경로(trajectory)가 의도적으로 바뀌었을 때만: 새 기준 경로를 채택
uv run python -m fitwitness.evaluation.gate --output artifacts/gate --write-trajectories
```

## 측정 항목

| 스위트 | 무엇을 | 지표 |
|---|---|---|
| `rules_graph` | FW-F000 도면 5개에 실제 LangGraph(rules provider) 실행, 생성기 gold와 대조 | 판정 정확도 (`== 1.0`) |
| `lexical` | 고정 test split 6 family × 질의 유형(exact / id_variant / id_typo / paraphrase), 도번+BM25 채널만 | 유형별 Recall@5, nDCG@5, p50 지연 |
| `geometry` | test+dev 12 family의 STEP 60개를 CadQuery로 읽어 특징 거리로 순위. 각 family의 기준 모델이 자기 family를 찾는지 | same-family@1, same-family@3 |
| `control` | `lexical`을 도번 정규화를 끈 상태(`id_matching="token"`)로 다시 실행 | 정상 − 열화 차이. **차이가 작으면 벤치마크가 열화를 감지하지 못하는 것이므로 실패** |
| `claims` | 합성 청구 120건에 추출 → 정합성 → 기준표 → 반례 검토를 실행, 생성기 gold와 대조. 열화 대조군은 서류 요건·원장 조회·자동승인 한도를 끈 실행 | 잘못 지급 비율(`== 0`), 결정 정확도, 필드 정확도, 자동 처리율, 열화 대조군과의 잘못 지급 차이(`≥ 0.05`) |
| `claims_scan` | 위 청구 중 시나리오당 2건(22건)을 스캔처럼 열화시켜(`medium`) Tesseract로 읽고 같은 파이프라인에 통과. **Tesseract가 없으면 건너뜀**(CI는 `tesseract-ocr-kor`를 설치) | 잘못 지급 비율(`== 0`), 잘못 부지급 비율(`== 0`), 필드 정확도(`≥ 0.90`, 하락폭 0.03), 자동 처리율(`≥ 0.50`, 표본에서 지급 대상이 6건 안팎이라 하락폭은 보지 않음) |
| `trajectory` | 청구 120건과 도면 실행 4가지를 **실제 LangGraph 그래프로 작업 큐를 거쳐** 실행하고, 이벤트 로그에 남은 경로를 커밋된 기준 경로와 대조. 청구는 담당자 승인·부지급 분기와 같은 청구의 재실행까지 | 경로 일치율(`== 1.0`), 불변식 위반(`== 0`), 그래프 수준 잘못 지급(`== 0`), 열화 대조군의 경로 차이(`≥ 0.05`)와 위반 수(`≥ 1`) |

질의 유형 `id_variant`(공백·밑줄·소문자·O/0 변형)와 `id_typo`(한 자리가 틀려 존재하지 않는 도번)는 `evaluation/retrieval.py`의 `make_cases`에 추가됐고(protocol `retrieval-v3`), 유료 검색 실측에서도 같은 유형이 측정됩니다. 이전 v2 결과는 그대로 보존되며 v3와 직접 비교하지 않습니다.

## 임계치

`evaluation/gate.py`의 `THRESHOLDS`에 있습니다. 절대 하한(`min`), 베이스라인 대비 허용 하락폭(`max_drop`), 정확히 일치해야 하는 값(`equals`) 세 종류입니다. 리포트는 `artifacts/gate/report.json`, 판정은 `artifacts/gate/gate.json`, 질의별 원시 순위는 `artifacts/gate/rows.json`에 남습니다.

## 해석 범위

- 합성 도면 150개·고정 split 기준입니다. 산업 데이터 성능이 아닙니다.
- 의미·이미지 채널과 LLM Agent 품질은 이 게이트에 없습니다. 그것은 유료 실측 workflow의 몫입니다.
- `paraphrase` 유형은 lexical 채널로는 원래 낮습니다. 하한 없이 하락폭만 감시합니다.

## 경로 게이트: 어디에 도착했는지가 아니라 어떤 길로 갔는지

정확도 지표는 결과만 봅니다. 결과가 우연히 맞더라도 길이 틀릴 수 있습니다. 반례 검토보다 지급 기록이 먼저 나가거나, 담당자에게 묻지 않고 지급이 끝나거나, 같은 조회를 두 번 하는 경우입니다. `evaluation/trajectory.py`는 각 실행이 남기는 이벤트(`intake → tool:extract_documents → extracted → validated → adjudicated:X → challenged:X → payout:… → explained`)를 짧은 토큰열로 바꿔 두 가지로 검사합니다.

1. **기준 경로.** `docs/evaluation/trajectories.json`에 청구 120건(담당자 대기 건은 승인·부지급 후의 경로, 지급된 건은 재실행 경로 포함)과 도면 실행 4가지의 경로가 커밋되어 있습니다. 실행이 이를 토큰 단위로 재현해야 합니다. 동작을 의도적으로 바꾸면 이 파일의 diff가 리뷰에 그대로 나타납니다. 어긋나면 게이트가 처음 갈라진 단계를 알려 줍니다(`path differs: claims/CLM-00000 at step 5: expected 'payout:paid', got 'challenged:APPROVE'`).
2. **불변식.** 기준 경로와 별개로, 어떤 경로에서든 지켜져야 하는 성질입니다. 노드 순서, 반례 검토 전 지급 금지, 한 실행의 지급은 최대 1건, 담당자에게 묻는 동안 원장 변화 없음, 원장 금액은 생성기 gold와 일치, 담당자 승인은 제안 금액을 정확히 한 번만 지급하고 부지급은 지급하지 않음, 같은 청구를 다시 실행해도 원장 불변, 한 도면 조회를 두 번 실행하지 않음, 정상 실행에 재시도·실패·예산 중단 이벤트가 없음.

도면 쪽은 규칙 provider(후보마다 한 번 조회), 담당자 대기와 재개, 그리고 대본으로 고정한 planner+challenger 한 쌍을 돌립니다. 대본의 planner는 같은 조회를 두 번 요청하고, 두 번째는 `tool_skipped`로 건너뛰어야 하며, challenger는 다른 도면을 조회하고 멈춥니다. 모델 호출 없이 다중 에이전트 경로가 결정적으로 검사됩니다.

**열화 대조군.** 정확도 게이트의 대조군과 같은 방식(서류 요건 무시, 원장 조회 실패, 자동승인 한도 해제)으로 청구 그래프의 판정 함수를 실제로 망가뜨려 실행합니다. 이때 경로 불일치와 잘못 지급(그래프 수준)이 잡혀야 합니다. 현재 40건 중 10건에서 잘못 지급이 잡히고 경로 일치율이 0.25 떨어집니다. 이 게이트가 아무것도 못 잡게 되면 `control.trajectory_degraded_violations`가 실패합니다.

**범위.** 규칙 엔진과 대본의 planner/challenger가 지나는 경로입니다. 실제 모델이 고르는 경로의 품질은 이 게이트 밖이며 유료 실측 workflow가 다룹니다. 실행 시간은 CI에서 약 1분입니다.

