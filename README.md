# FitWitness

**도면의 생김새보다, 맞는 근거.**

제조 부품의 도면을 검색하고 실제 치수·소재 근거로 검증하며, 개정판이 생기면 기존 판단을 무효화하는 연구 프로젝트입니다. 텔어스 채용 공고의 문제를 바탕으로 만든 독립 포트폴리오입니다.

**[공개 데모 열기](https://fitwitness.onrender.com/)** · **[보험 청구 심사 데모](https://fitwitness.onrender.com/#claims)** · [검증 기록](docs/verification/hosted-research-20261005.json)

![도면 검토 작업대](docs/media/review-workbench.png)

![실제 LLM 평가와 오류 사례 탐색](docs/media/evaluation-lab.png)

실제 Claude·Qwen·E5·OpenCLIP 실측과 실패→수정 기록은 [실측 기록](docs/EVIDENCE.md)에 원문 그대로 보존했습니다. 아래는 현재 브랜치 기준 요약입니다.

| 측정 | 결과 | 기록 |
|---|---|---|
| PDF 근거 판정, Claude Haiku 4.5 72회 | 72/72 정답, 잘못된 일치 0/42, $0.110455 | [evaluation/README](docs/evaluation/README.md) |
| 전체 Agent 세 구조, 최종 코드 Claude gate | 3/3 완료, 각 5/5 판정, $0.016586 | [claude-final-gate.json](docs/evaluation/claude-final-gate.json) |
| 복합 검색 288회 + 재정렬 216회 | Recall@5 복합 33.3%, 조건 정렬 64.2% | [RETRIEVAL.md](docs/RETRIEVAL.md) |
| 외부 NIST STEP 11쌍 CAD 거리 검색 | Top-1 11/11, MRR 1.000 | [CAD.md](docs/CAD.md) |
| 보험 청구 자동 심사, 합성 120건 (API 없음) | 잘못 지급 0건, 결정 정확도 92.5%, 자동 처리 88.5% | [CLAIMS.md](docs/CLAIMS.md) |
| VLM 이미지 18회 | 15회 구조화 완료, 23/36 필드 정답 | [VISION.md](docs/VISION.md) |
| 누적 API 비용 | $0.255248, 미확정 예약 0 | [EVIDENCE.md](docs/EVIDENCE.md) |
| 현재 브랜치 자동 검증 | Python 183개, Playwright 58개, API 없는 평가 게이트 통과 | [CI](.github/workflows/ci.yml) |

## 구현된 기능

- CadQuery 기반 30개 family / 180개 합성 PDF·STEP·PNG·3D mesh
- **보험금 청구 자동 심사**(두 번째 워크플로): 합성 진단서·입퇴원확인서·수술확인서·영수증에서 값과 위치를 읽고, 가상의 지급 기준표를 규칙 ID와 함께 적용해 지급·부지급·담당자 확인으로 나누며, 멱등 원장으로 한 번만 지급합니다. 120건 평가에서 잘못 지급 0건, 자동 처리율 88.5% ([docs/CLAIMS.md](docs/CLAIMS.md))
- 체험 작업 공간은 브래킷·플랜지·샤프트·하우징 4종 20개 도면을 불러옵니다. 검색이 돌려준 후보만 판정하고 나머지는 "검색 제외"로 접어 보여 주며, 아무것도 맞지 않으면 그 사실을 알립니다
- 도번·BM25와 해시 고정 E5·OpenCLIP/pgvector 색인·검색·worker 연결. 도번은 공백·밑줄·O/0·시리즈 변형과 한 글자 오타까지 정규화해 찾습니다
- PDF 실제 위치를 근거로 조건 일치·불일치·확인 필요 판정
- LangGraph + PostgreSQL checkpoint, 실제 worker 종료 후 복구, 일시 오류의 지수 백오프 재시도와 보류함(dead-letter)
- 미리 데워 둔 worker 프로세스 풀: 실행마다 인터프리터와 LangGraph import를 다시 치르지 않아 큐 등록 → worker 시작이 로컬 1.65초 → 0.16초
- 근거가 부족하면 담당자에게 묻고 멈추는 `waiting_input`, 답변을 받아 checkpoint에서 이어가는 resume API. 청구는 금액에 따른 승인 구간을 두어, 상급 검토 건은 사유와 서로 다른 두 담당자의 승인이 있어야 지급하고, 응답 기한을 넘기면 대신 결정하지 않고 상급 검토로 올립니다 ([CLAIMS.md](docs/CLAIMS.md))
- 도면 40 → 42mm 개정 시 기존 결과 무효화 및 재검증
- 방문자별 세션·RLS, typed tools, OpenAI·Claude 어댑터, 실행 예산
- 에이전트가 스스로 정의해 재사용하는 저장 검색 도구(`define_search_tool` / `run_saved_search`), 테넌트별 격리·개수 제한
- 실행·이벤트 테이블에서 계산하는 Prometheus 지표, 실행별 span tree(`/api/runs/{id}/trace`), 환경 변수 하나로 켜는 OpenTelemetry(OTLP) 전송. Jaeger에서 중단 후 복구된 실행까지 한 trace로 확인했습니다 ([docs/OBSERVABILITY.md](docs/OBSERVABILITY.md))
- React·Three.js 도면 검토대: 후보 필터, 근거 위치, 실제 CAD 3D, 개정판·실행 기록 검사, 담당자 확인 입력
- LLM 실험실: Qwen/Claude 모델 선택, 오류 사례·반복 실행별 원시 출력, 실제 Agent 호출 기록, 평가 프로토콜
- 도구로 조회한 사실만 판정에 반영하는 Agent, 별도 탐색/반례 검토 단계, 가용 도구·호출·토큰·비용 제한과 bounded retry
- [재현 가능한 LLM 평가](docs/evaluation/README.md)와 API 없이 매 커밋 베이스라인과 비교하는 [평가 게이트](docs/EVALUATION-GATE.md). 결과뿐 아니라 실행이 지난 경로(노드 순서, 지급 전 반례 검토, 담당자 응답 한 번 적용, 재실행 시 원장 불변)도 커밋된 기준 경로와 대조합니다

기본 체험은 명시적인 규칙 기반 엔진이며 API 호출을 흉내 내지 않습니다. 공개 익명 API는 키가 설정되어 있어도 유료 모델 실행을 거부합니다.

## 실행 흐름 한눈에

```
POST /api/runs ──▶ queued ──▶ running ──▶ completed ──(개정)──▶ stale
                                │  │
                 일시 오류 ◀────┘  └────▶ waiting_input ◀── 근거 부족 + review=on_unknown
                 retry_wait ─▶ running        │ POST /api/runs/{id}/resume
                 (5·10·20…초, 3회)             ▼
                 소진 시 failed(dead_lettered)  running ─▶ completed
```

상태 전이, 재시도 규칙, 담당자 입력 계약, 지표·trace, 저장 도구의 경계는 [docs/WORKFLOW.md](docs/WORKFLOW.md)에 있습니다.

[공고 대조·미완료 항목](docs/JOB_FIT_AUDIT.md) · [독립 리뷰와 수정 기록](docs/REVIEW.md) · [배포 상태](docs/DEPLOYMENT.md) · [운영자 모델 실행](docs/OPERATOR.md) · [검색 실험](docs/RETRIEVAL.md)

## 로컬 실행

Python 3.12 이상, Node.js, PostgreSQL 16 + pgvector가 필요합니다. 개발 의존성 `pgserver`(테스트용 내장 PostgreSQL)는 Python 3.12까지만 wheel이 있어 3.13에서는 설치되지 않으며, 그 경우 `FITWITNESS_DATABASE_URL`로 외부 PostgreSQL을 지정합니다.

```bash
uv sync --extra dev
export PYTHONPATH=src
export FITWITNESS_DATABASE_URL='postgresql://USER:PASSWORD@localhost:5432/fitwitness'
uv run python -m fitwitness.data.generate
uv run python -m fitwitness.claims.synth var/claims 120
uv run python scripts/setup.py
npm ci --prefix web
npm run build --prefix web
uv run uvicorn fitwitness.api.app:create_app --factory --host 0.0.0.0 --port 8787
```

`http://localhost:8787`에서 조건 검증, 원본 근거, 개정판 적용, worker 복구, 담당자 확인 흐름과 "청구 심사" 탭의 보험금 자동 심사를 체험합니다. DB 초기화 계정에는 role/extension 생성 권한이 필요합니다.

## 검증

```bash
uv run pytest -q
uv run python -m fitwitness.evaluation.gate --output artifacts/gate --baseline docs/evaluation/gate-baseline.json
cd web
npx playwright install chromium
PLAYWRIGHT_BASE_URL=http://localhost:8787 npm run test:e2e
```

운영자 실험은 격리된 CLI 환경에 API 키, 정확한 모델 ID, 현재 단가를 설정해야 합니다. 라이브 실험 미측정을 성공으로 보고하지 않습니다.

설계·구현 계획은 `docs/superpowers/`에 있습니다. 생산 준비 완료 상태는 아닙니다. 일반 스캔 도면 OCR, 최소 의존성 재검증, 실제 산업 데이터 성능은 별도 과제입니다.

## 라이선스

직접 작성한 코드는 Apache-2.0입니다. 의존성·모델·글꼴에는 각 원저작자의 라이선스가 적용됩니다.
