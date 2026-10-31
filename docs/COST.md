# 건당 모델 비용과 지연

모델 호출이 한 건에 얼마나 들고 얼마나 걸리는지를, 이미 커밋된 측정 기록에서 다시 계산한 표입니다. 새로 호출하지 않았고, 아래 값은 모두 `docs/evaluation/*.json`의 행별 기록(`cost_usd`, `latency_ms`)을 평균·중앙값·95번째 백분위로 줄인 것입니다. 표의 줄은 `python scripts/cost-table.py`가 그대로 찍습니다(2026-10-24 계산, 2026-10-25에 세 줄, 2026-10-30·31에 OpenAI와 Claude 줄을 더함).

모델은 `[OpenAI gpt-5-mini]`로 표시한 네 줄(입력 $0.25/M·출력 $2.00/M 토큰, 실행할 때 입력한 값)을 빼고 모두 Claude Haiku 4.5(`claude-haiku-4-5-20251001`)이고, 단가는 기록에 적힌 입력 $1/M·출력 $5/M 토큰입니다. 두 모델의 줄은 같은 일의 비교가 아닙니다(추론 설정, 입력 길이가 다름). 응답의 토큰 수로 계산한 값이라 청구서 결산액이 아닙니다. 캐시 할인과 세금은 넣지 않았습니다.

| 한 건 | 표본 | 비용 평균 | 지연 p50 / p95 | 기록 |
|---|---|---|---|---|
| PDF 근거 판정, 모델 호출 한 번 | 72회 | $0.0015 | 0.66 / 0.90초 | [claude.json](evaluation/claude.json) |
| 청구 한 건의 실행, 글자 층이 있는 서류 | 60건 × 3회 | $0.0043 | 3.2~3.3 / 4.4~4.9초 | [claims-model.json](evaluation/claims-model.json) |
| 같은 청구, 스캔처럼 흐리게 만든 서류(OCR 글에서 읽음) | 60건 × 1회 | $0.0041 | 25.5 / 34.4초 | [claims-model.json](evaluation/claims-model.json) |
| 지급 직전 모델 감사, 약해진 규칙의 결정 | 65건 × 3회 | $0.0030 | 중앙값 0.8초 | [claims-audit.json](evaluation/claims-audit.json) |
| 지급 직전 모델 감사, 멀쩡한 규칙의 결정 | 46건 × 1회 | $0.0027 | 중앙값 0.8초 | [claims-audit.json](evaluation/claims-audit.json) |
| 지급 직전 모델 감사, 사실 절이 비어 있는 고장 | 49건 × 1회 | $0.0038 | 중앙값 2.4초 | [claims-audit.json](evaluation/claims-audit.json) |
| 검색 질의 읽기, 지금의 프롬프트 | 60문장 × 2회(dev·test 각 1회) | $0.0021 | 1.5 / 1.9초 | [query-understanding.json](evaluation/query-understanding.json) |
| 검색 질의 읽기, 프롬프트를 고치기 전 | 60문장 × 3회(test) | $0.0020 | 1.3~1.4 / 1.8초 | [query-understanding.json](evaluation/query-understanding.json) |
| 현업자 검색어 읽기(고치기 전·후) | 10개 × 2회 | $0.0017 | 0.9 / 1.0~1.1초 | [query-understanding.json](evaluation/query-understanding.json) |
| 에이전트 실행 한 번, 단일 도구 계획 | 6회 | $0.0050 | 중앙값 5.1초 | [agent-trajectory.json](evaluation/agent-trajectory.json) |
| 에이전트 실행 한 번, 반복 계획 | 6회 | $0.0050 | 중앙값 4.9초 | [agent-trajectory.json](evaluation/agent-trajectory.json) |
| 에이전트 실행 한 번, planner+challenger | 6회 | $0.0097 | 중앙값 6.4초 | [agent-trajectory.json](evaluation/agent-trajectory.json) |
| 명령 검색 도구를 켠 에이전트 실행 한 번, 30장에서 집합 질의 | 질의 8·8·7개(세 묶음) | $0.0063~0.0111 | 5.6~8.7 / 5.8~13.4초 | [shell-search-model.json](evaluation/shell-search-model.json) |
| 같은 질의, 명령 검색 도구를 끈 실행 | 질의 8개 | $0.0060 | 5.4 / 7.8초 | [shell-search-model.json](evaluation/shell-search-model.json) |
| 표제란 읽기 호출 한 번(쪽 전체 또는 자른 표제란, 이미지 한 장) | 133회 | $0.0032 | 1.6 / 2.0초 | [title-block-vlm.json](evaluation/title-block-vlm.json) |
| [OpenAI gpt-5-mini] PDF 근거 판정, 모델 호출 한 번 | 72회 | $0.0002 | 0.88 / 1.32초 | [openai.json](evaluation/openai.json) |
| [OpenAI gpt-5-mini] 청구 한 건을 모델이 판정까지(추론 low, 모델 호출 한 번) | 60건 × 2회 | $0.0018 | 8.5 / 14.9초 | [claims-model-decides.json](evaluation/claims-model-decides.json) |
| [OpenAI gpt-5-mini] 에이전트 실행 한 번, 쉬운 설정(다섯 장), 단일 · 반복 · planner+challenger | 18회씩 | $0.0011 · $0.0012 · $0.0022 | 중앙값 3.4 · 3.8 · 5.7초 | [agent-trajectory-repeats.json](evaluation/agent-trajectory-repeats.json) |
| [OpenAI gpt-5-mini] 에이전트 실행 한 번, 어려운 설정(서른 장, 후보 12개), 단일 · 반복 · planner+challenger | 36회씩 | $0.0024 · $0.0037 · $0.0047 | 중앙값 5.8 · 9.2 · 9.8초 | [agent-trajectory-repeats.json](evaluation/agent-trajectory-repeats.json) |
| [Claude Haiku 4.5] 청구 한 건을 모델이 판정까지(확장 사고 없음, 모델 호출 한 번) | 60건 × 2회 | $0.0045 | 3.2 / 11.5초 | [claims-model-decides.json](evaluation/claims-model-decides.json) |
| [Claude Haiku 4.5] 같은 실험, 양식에서 근거를 금액 앞에 | 60건 × 2회 | $0.0042 | 3.2 / 6.1초 | [claims-model-decides.json](evaluation/claims-model-decides.json) |
| [Claude Haiku 4.5] 에이전트 실행 한 번, 쉬운 설정(다섯 장), 단일 · 반복 · planner+challenger | 18회씩 | $0.0052 · $0.0051 · $0.0100 | 중앙값 5.2 · 5.1 · 7.0초 | [agent-trajectory-repeats.json](evaluation/agent-trajectory-repeats.json) |
| [Claude Haiku 4.5] 에이전트 실행 한 번, 어려운 설정(서른 장, 후보 12개), 단일 · 반복 · planner+challenger | 24 · 24 · 23회 | $0.0067 · $0.0150 · $0.0148 | 중앙값 7.0 · 12.4 · 12.3초 | [agent-trajectory-repeats.json](evaluation/agent-trajectory-repeats.json) |

## 읽는 법

- **"한 건"의 단위가 줄마다 다릅니다.** PDF 근거 판정·감사·질의 읽기는 모델 호출 한 번입니다. 청구 한 건은 서류 읽기(추출) 호출 한 번과 검증, 기록을 거쳐 끝나거나 담당자 확인을 기다리는 데까지의 실행 전체이고(`claims/graph.py`의 추출 노드에 모델 호출이 하나이며, 네 묶음 모두 재요청 0건·모델 오류 0건), 에이전트 실행은 모델 호출이 평균 1회(planner+challenger는 2회)인 실행 전체입니다.
- **청구 한 건의 지연은 모델 호출만의 시간이 아닙니다.** 같은 실행 안의 검증과 데이터베이스 기록이 들어 있고, 모델 시간만 따로 재지 않았습니다. 스캔 줄은 글자 인식(Tesseract) 시간이 들어 있어 25초대입니다. 글자 층이 있는 줄(3초대)이 모델 호출에 더 가깝지만 같은 값은 아닙니다.
- 청구·에이전트 줄은 GitHub Actions 실행기와 그 안의 PostgreSQL 컨테이너에서 잰 값입니다. 공개 서버의 지연이 아닙니다.
- 지급 감사의 지연에는 코드가 계산하는 사실(날짜 차이, 서류 유무, 한도 대조)을 만드는 시간이 같은 구간에 들어 있지만 모델 호출에 비하면 무시할 만합니다. 사실 절이 비어 있는 고장 줄은 모델이 서류의 줄을 직접 읽느라 출력이 길어(결정당 출력 약 270토큰, 약해진 규칙 줄은 약 90토큰) 비용이 약 27%, 지연이 약 3배입니다.
- 질의 읽기는 프롬프트에 "이 보관소에 없는 부품은 종류를 비워 두라"는 문장을 더한 뒤(`955a90e`) 입력이 늘어 비용이 $0.0020에서 $0.0021로, 지연이 약 0.1초 늘었습니다. 지금의 줄은 이 프롬프트로 잰 두 묶음이고, 고치기 전 줄은 비교를 위해 남겼습니다.
- 표본이 60건·72회로 작아 비용은 소수 넷째 자리까지 의미가 없고, 지연은 호출 시점의 API 부하에 따라 달라집니다. 같은 기록 안에서도 묶음마다 p50이 0.1초 안팎으로 흔들립니다.
- 공개 서버 지연(13.6초 → 0.5초)은 모델 호출이 아니라 데이터베이스 왕복의 문제였고, [WORKFLOW.md](WORKFLOW.md)에 따로 있습니다.

## 다시 계산하기

```bash
python scripts/cost-table.py
```

`claude.json`은 `predictions`에서 규칙 판독기 행(`cost_usd` 0)을 뺀 모델 행 72개를, `claims-model.json`은 묶음마다 60행을, `claims-audit.json`은 실행마다 `cost_usd`를 `audited`로 나눈 값과 기록된 `latency_ms_median`을, `query-understanding.json`은 모델이 읽은 묶음(`dev-after`, `test-after`와 `test`, `test-repeat-2`, `test-repeat-3`, `practitioner-before`, `practitioner-after`)을 씁니다. `dev-kind`·`test-kind`는 저장된 읽기를 다시 정렬한 재생이라 비용이 0이어서 뺐습니다. `agent-trajectory.json`은 네 번째 실행의 모델 행입니다. p95는 정렬한 지연의 `int(0.95 * (n - 1))`번째 값입니다.
