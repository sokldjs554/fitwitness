# FitWitness

[![CI](https://github.com/sokldjs554/fitwitness/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/sokldjs554/fitwitness/actions/workflows/ci.yml)

**도면의 생김새보다, 맞는 근거.**

제조 부품의 도면을 검색하고 실제 치수·소재 근거로 검증하며, 개정판이 생기면 기존 판단을 무효화하는 연구 프로젝트입니다. 텔어스 채용 공고의 문제를 바탕으로 만든 독립 포트폴리오입니다.

**[공개 데모 열기](https://fitwitness.onrender.com/)** · **[보험 청구 심사 데모](https://fitwitness.onrender.com/#claims)** · **[스캔 읽기 예시](https://fitwitness.onrender.com/#claims-scan)** · [검증 기록](docs/verification/hosted-20261029-88a7484.json)

![도면 검토 작업대](docs/media/review-workbench.png)

![실제 LLM 평가와 오류 사례 탐색](docs/media/evaluation-lab.png)

실제 Claude·OpenAI·Qwen 모델과 E5·OpenCLIP·DINOv2 인코더의 실측, 실패→수정 기록은 [실측 기록](docs/EVIDENCE.md)(10-06 시점 원문)과 [전체 측정 표](docs/RESULTS.md)에 있습니다. 아래는 핵심만 추린 것입니다.

## 한눈에

- **무엇**: 같은 코어(LangGraph + PostgreSQL checkpoint + 근거 검증) 위의 두 워크플로입니다. (1) 제조 도면 검색·검증: 도번·키워드·의미·형상으로 후보를 모으고 PDF의 실제 치수 위치를 근거로 조건 일치·불일치·확인 필요를 판정하며, 개정판이 생기면 이전 판정을 무효화합니다. (2) 보험금 청구 심사: 합성 서류에서 값과 위치를 읽고 가상 지급 기준표의 규칙으로 지급·부지급·담당자 확인을 정하며, 담당자 확인·2인 승인·서명된 영수증을 남깁니다.
- **왜**: 텔어스 공고가 든 제조 도면 검색 문제와 의료 보험금 청구 문제를 한 프로젝트로 풀어 보기 위해서입니다.
- **모델의 역할**: 조건·금액을 최종으로 정하는 것은 결정적 검증기와 규칙이고, 모델은 질의 읽기·서류 추출 제안·도구 선택·지급 직전 감사에 씁니다. 모델이 정하지 않게 한 이유와 그 비용은 [JOB_FIT_AUDIT](docs/JOB_FIT_AUDIT.md)에 한계로 적었습니다.
- **어떻게 증명했나**: 실제 모델 호출 측정(비용과 실패 포함), 매 커밋 API 없는 평가 게이트, 호스팅 데모 검증 기록, 닫힌 한계 목록입니다.

## 핵심 결과

| 측정 | 결과 | 기록 |
|---|---|---|
| PDF 근거 판정, 같은 24사례 × 3회 | Claude Haiku 4.5 72/72 정답, 잘못된 일치 0/42, $0.11. OpenAI `gpt-5-mini`도 72/72 정답, 잘못된 일치 0/42, $0.0149(둘 다 천장이라 우열은 못 가림) | [evaluation/README](docs/evaluation/README.md) |
| 실제 모델이 고르는 경로, test 6개 family × 세 구조 | 네 번 실행: 1차 planner+challenger 4/6 완료 → 컨텍스트를 고쳐 4차 18/18 완료, 90/90. 판정은 검증기가 하고 모델은 도구 선택과 종료만. 합계 $0.40. 같은 설정을 세 번씩, 도면 서른 장·후보 12개로 어렵게 만들자 OpenAI `gpt-5-mini`는 모두 정답인 실행이 쉬운 설정 54/54에서 어려운 설정 0·6·7/36(단일·반복·challenger)으로 떨어졌고, 틀린 판정은 모두 조회하지 않은 도면이었음. 같은 두 설정을 Claude Haiku 4.5로 돌리면 쉬운 설정 54/54, 어려운 설정 0/24 · 22/24 · 23/23(상한 $0.9에서 반복 2회로 멈춤)으로 두 다단계 구조는 거의 다 맞혔고 단일 계획은 구조상 12개 중 8개만 열어 0이었음. 멈춘 셋째 반복만 따로 돌려 합치면 0/36 · 33/36 · 35/35(반복 계획이 놓친 세 번은 모두 같은 케이스). planner는 Claude로 두고 반례 역할만 gpt-5-mini에게 맡기면 모두 정답인 실행이 10/24로 떨어짐(반례 쪽이 일찍 멈춰 조회하지 않은 도면이 남음). 모델이 바뀌면 구조의 순서가 달라져 이 비교는 다른 모델에 옮길 수 없음 | [evaluation/README](docs/evaluation/README.md#세-번씩-그리고-더-어려운-설정에서-2026-10-30) |
| 검색 | 복합 질의 Recall@5 33.3%(95% 구간 17~49%), 조건 정렬 64.2%. NIST STEP 11쌍 CAD 거리 Top-1 11/11. 합성 도면 10만 장까지 규모 시험 | [RETRIEVAL.md](docs/RETRIEVAL.md) · [CAD.md](docs/CAD.md) |
| 보험 청구 자동 심사, 합성 120건 (API 없음) | 잘못 지급 0건, 결정 정확도 92.5%, 자동 처리 88.5%. 스캔·모델 추출·감사 경로는 전체 표 | [CLAIMS.md](docs/CLAIMS.md) |
| 모델이 판정까지 내리게 하면(제품에는 없는 비교 실험), 청구 60건 × 2회 | OpenAI `gpt-5-mini`는 잘못 지급 1/120(진단서가 없는 청구에 수술비까지 지급), 규칙 경로는 0/60. 모델이 틀린 나머지는 지급할 것을 담당자 확인으로 돌린 쪽이었음. 같은 프롬프트의 Claude Haiku 4.5(확장 사고 없음)는 49/120(40.8%), 잘못 나간 금액 합 156,060,000원(gpt-5-mini 300,000원)이었고 대부분 맞는 결과에 더 큰 금액을 적은 것. 모델 종류가 달라 우열이 아니라, 모델에게 판정을 맡기면 잘못된 지급의 빈도가 모델과 출력 형식에 따라 크게 갈린다는 것. 양식에서 근거를 금액 앞에 두고 같은 청구에 다시 돌리면 39/120(32.5%)로 줄었지만 줄어든 것은 금액 오류(30→20)뿐이고 담당자 확인·부지급 청구를 지급한 것은 그대로(19→19)였음. 변형 네 가지를 dev 60건에서 24·18·4·6으로 재고(미리 정한 규칙으로 4건인 담보별 작업 줄 먼저 쓰기를 선택), 한 번도 읽지 않은 새 청구 60건에 처음 양식과 함께 물으면 25/60 → 5/60(규칙 경로 0/60). 남은 5건은 모두 담당자 확인 대상을 지급한 것 | [CLAIMS.md](docs/CLAIMS.md#모델이-최종-판정까지-내리면-2026-10-30) |
| 자동 검증 | Python 591개, Playwright 80개, API 없는 평가 게이트, 알림 규칙의 promtool 테스트. 공개 데모는 배포 때마다 15항목 검증 | [TESTING.md](docs/TESTING.md) · [검증 기록](docs/verification/hosted-20261029-88a7484.json) |
| 한계 | 합성 데이터뿐이고 실제 고객 도면·병원 서류가 없음. 사람이 받은 알림 없음. 무료 단일 인스턴스 | [JOB_FIT_AUDIT](docs/JOB_FIT_AUDIT.md) |

나머지 30여 개 측정(스캔 읽기, 상용 OCR 비교, 표제란, 프롬프트 비교, 사용자 테스트, 부하, 비용 등)은 [전체 측정 표](docs/RESULTS.md)에 있습니다.

## 만든 방식과 역할

- **개발 도구**: 2026-10-04~06은 Codex(설계서와 초기 구현·리뷰 기록), 2026-10-06부터는 Claude Code입니다. 커밋 220개 중 155개의 작성자가 `Claude`이고 그 커밋에 세션 링크가 있습니다. 이력은 고치지 않았습니다. 한 줄 한 줄을 사람이 직접 썼다고 주장하지 않습니다.
- **지원자가 한 일(저장소 기록 기준)**: 공고를 고르고 범위를 정한 것, 유료 모델 호출의 승인과 상한(예: [OPERATOR.md](docs/OPERATOR.md)의 합계 1 USD, OpenAI 측정 $0.10)과 API 키 등록, Render 수동 배포와 배포 뒤 확인([DEPLOYMENT.md](docs/DEPLOYMENT.md)), 사용자 테스트 세 명의 모집·진행·인용 동의([USER-TEST.md](docs/USER-TEST.md)).
- **개발 도구가 한 일**: 구현, 테스트, 측정 실행의 설계와 기록, 문서. 측정마다 실행 id·코드 SHA·비용을 남기고, 독립 리뷰([REVIEW.md](docs/REVIEW.md))와 보안 점검([SECURITY.md](docs/SECURITY.md))의 발견과 수정을 같은 저장소에 기록했습니다.

## 구현된 기능

- CadQuery 기반 30개 family / 180개 합성 PDF·STEP·PNG·3D mesh(개정판 30개 포함, 활성 150)
- **보험금 청구 자동 심사**(두 번째 워크플로): 합성 진단서·입퇴원확인서·수술확인서·영수증에서 값과 위치를 읽고, 가상의 지급 기준표를 규칙 ID와 함께 적용해 지급·부지급·담당자 확인으로 나누며, 멱등 원장으로 한 번만 지급합니다. 120건 평가에서 잘못 지급 0건, 자동 처리율 88.5%. 텍스트 층이 없는 스캔은 Tesseract로 읽고, 읽기가 불확실하면 지급하지 않고 담당자에게 넘기며 스캔에 근거한 부지급은 자동 확정하지 않습니다(시뮬레이션 스캔이며 실제 문서가 아닙니다). 비고란의 지시문은 어느 필드에도 닿지 않고, 비고 안의 가짜 라벨은 값이 되지 못하며, 한 서류가 한 필드에 두 값을 말하면 담당자에게 갑니다. 같은 판을 실제 모델의 추출 경로로 돌리니 가짜 라벨이 모델의 제안은 바꿨지만 grounding이 그 값을 버렸고, 그런 경우도 담당자에게 가도록 했습니다. test 60건을 실제 모델의 추출 경로로 돌리니 텍스트 층에서는 규칙 판독기보다 3건 더 맞혔고 스캔에서는 1건 덜 맞히며 잘못 부지급 1건이 나와 규칙 경로의 보호를 옮겨 넣었습니다. 지급 직전에 모델이 결정을 한 번 더 읽고 담당자에게 보내는 감사를 선택 사항으로 두었고, 약해진 규칙이 통과시킨 잘못된 지급 19건을 모두 세우고 옳은 지급 46건은 그대로 두었습니다 ([docs/CLAIMS.md](docs/CLAIMS.md)). 같은 스캔 서식 98쪽을 상용 문서 AI(Upstage Information Extraction)에 보내 같은 기준으로 채점하니 필드 446개를 모두 맞혔고, 격자 판독기는 431개였습니다
- 체험 작업 공간은 브래킷·플랜지·샤프트·하우징 4종 20개 도면을 불러옵니다. 검색이 돌려준 후보만 판정하고 나머지는 "검색 제외"로 접어 보여 주며, 아무것도 맞지 않으면 그 사실을 알립니다
- 도번·BM25와 해시 고정 E5·OpenCLIP/pgvector 색인·검색·worker 연결. 도번은 공백·밑줄·O/0·시리즈 변형과 한 글자 오타까지 정규화해 찾습니다. 두 번째 이미지 인코더(DINOv2)를 같은 색인에 선택 채널로 둘 수 있고, 모델은 해시로 고정합니다. 질의의 치수 조건은 패턴이 읽고, 선택하면 모델이 질의를 읽어 종류·치수 구간·재질·제외 재질을 제안하되 질의에서 따온 인용이 없는 제안은 버립니다(test 60문장에서 패턴 12, 모델 60; 복합 검색 Recall@5 59.3% → 76.5%, 읽은 종류를 후보 풀과 정렬에 쓰면 89.0%)
- PDF 실제 위치를 근거로 조건 일치·불일치·확인 필요 판정
- LangGraph + PostgreSQL checkpoint, 실제 worker 종료 후 복구, 일시 오류의 지수 백오프 재시도와 보류함(dead-letter)
- 미리 데워 둔 worker 프로세스 풀: 실행마다 인터프리터와 LangGraph import를 다시 치르지 않아 큐 등록 → worker 시작이 로컬 1.65초 → 0.16초
- 근거가 부족하면 담당자에게 묻고 멈추는 `waiting_input`, 답변을 받아 checkpoint에서 이어가는 resume API. 청구는 금액에 따른 승인 구간을 두어, 상급 검토 건은 사유와 서로 다른 두 담당자의 승인이 있어야 지급하고, 응답 기한을 넘기면 대신 결정하지 않고 상급 검토로 올립니다 ([CLAIMS.md](docs/CLAIMS.md))
- 도면 40 → 42mm 개정 시 기존 결과 무효화 및 재검증
- 방문자별 세션·RLS, typed tools, OpenAI·Claude 어댑터, 실행 예산
- 에이전트가 스스로 정의해 재사용하는 저장 검색 도구(`define_search_tool` / `run_saved_search`), 테넌트별 격리·개수 제한
- 실행·이벤트 테이블에서 계산하는 Prometheus 지표, 실행별 span tree(`/api/runs/{id}/trace`), 환경 변수 하나로 켜는 OpenTelemetry(OTLP) 전송. Jaeger에서 중단 후 복구된 실행까지 한 trace로 확인했습니다 ([docs/OBSERVABILITY.md](docs/OBSERVABILITY.md)). 내보내는 span은 OTel GenAI 의미 규약의 이름(`gen_ai.operation.name`, `gen_ai.agent.name`, `gen_ai.tool.name`, `gen_ai.usage.*`)을 달고 schema URL을 고정합니다
- 공개 데모는 10분마다 `/health`를 확인하도록 예약한 GitHub Actions(`uptime.yml`)가 응답 코드·시간을 기록하고, 응답이 없으면 메일로 알립니다. GitHub가 예약을 열 시간에 두 번만 돌려 깨워 두기에는 외부 모니터를 함께 씁니다(두 번 모두 잠든 데모를 32.7초·36.0초에 깨움) ([docs/DEPLOYMENT.md](docs/DEPLOYMENT.md#깨워-두기-2026-10-27))
- Prometheus 알림 규칙 13개와 기록 규칙 14개, Grafana 대시보드(`deploy/prometheus`, `deploy/grafana`). SLO는 끝난 실행의 99%가 완료이고, 큐 정지·보류함·실패 소진·모델 오류·비용·지연·5xx를 봅니다. promtool 단위 테스트가 알림마다 울릴 때와 조용할 때를 확인하고, 실제 Prometheus 3.5.0이 로컬 앱을 긁어 큐 정지 알림이 울리는 것을 보았습니다. Alertmanager 0.28.1이 등급(page·ticket)에 따라 나누어 로컬 수신기까지 보내는 것(울림과 해소)을 확인했고 CI가 알림 13개의 경로를 검사합니다. 메일·Slack은 설정하지 않아 사람이 받은 알림도, 공개 데모를 긁는 Prometheus도 없습니다 ([docs/OBSERVABILITY.md](docs/OBSERVABILITY.md#알림-규칙과-slo))
- 바깥 연결 없이 도는 범위(규칙 경로, 로컬 Tesseract, 미리 옮긴 인코더 가중치, 같은 망의 Jaeger)와 켜는 순서는 [docs/ONPREM.md](docs/ONPREM.md)에 적었습니다. 매 커밋 CI가 이미지를 바깥으로 나갈 길이 없는 Docker 내부망에 PostgreSQL과 함께 띄워, 바깥 주소가 닿지 않는 것을 확인한 뒤 데모 검증 전체와 청구 8건을 돌립니다(실제 고객 폐쇄망은 아님)
- React·Three.js 도면 검토대: 후보 필터, 근거 위치, 실제 CAD 3D, 개정판·실행 기록 검사, 담당자 확인 입력
- LLM 실험실: 맨 위의 "최신 모델 측정"이 실제 모델로 잰 경로·청구 추출·지급 감사·질의 읽기·명령 검색·VLM 표제란과 사용자 테스트·실제 데이터의 범위를 커밋된 기록에서 보여 줍니다(`docs/evaluation/model-records.json`, 화면을 열 때 모델은 부르지 않음). 그 아래에 Qwen/Claude 모델 선택, 오류 사례·반복 실행별 원시 출력, 실제 Agent 호출 기록, 평가 프로토콜
- 도구로 조회한 사실만 판정에 반영하는 Agent, 별도 탐색/반례 검토 단계, 가용 도구·호출·토큰·비용 제한과 bounded retry
- [재현 가능한 LLM 평가](docs/evaluation/README.md)와 API 없이 매 커밋 베이스라인과 비교하는 [평가 게이트](docs/EVALUATION-GATE.md). 결과뿐 아니라 실행이 지난 경로(노드 순서, 지급 전 반례 검토, 담당자 응답 한 번 적용, 재실행 시 원장 불변)도 커밋된 기준 경로와 대조합니다. 실제 모델이 고르는 경로는 상한을 둔 수동 workflow로 재고, 틀린 판정을 조회 안 함·필드 누락·미완료로 나눠 고친 기록을 남깁니다

기본 체험은 명시적인 규칙 기반 엔진이며 API 호출을 흉내 내지 않습니다. 공개 익명 API는 키가 설정되어 있어도 유료 모델 실행을 거부합니다(운영자가 `FITWITNESS_LIVE_DEMO=on`과 상한을 설정한 라이브 모델 읽기만 예외이고 기본은 꺼짐, [DEPLOYMENT.md](docs/DEPLOYMENT.md)).

## 실행 흐름 한눈에

```
POST /api/runs ──▶ queued ──▶ running ──▶ completed ──(개정)──▶ stale
                                │  │
                 일시 오류 ◀────┘  └────▶ waiting_input ◀── 근거 부족 + review=on_unknown
                 retry_wait ─▶ running        │ POST /api/runs/{id}/resume
                 (5·10초, 3번째 실패에서)           ▼
                 소진 시 failed(dead_lettered)  running ─▶ completed
```

상태 전이, 재시도 규칙, 담당자 입력 계약, 지표·trace, 저장 도구의 경계는 [docs/WORKFLOW.md](docs/WORKFLOW.md)에 있습니다.

[공고 대조·미완료 항목](docs/JOB_FIT_AUDIT.md) · [독립 리뷰와 수정 기록](docs/REVIEW.md) · [보안 점검](docs/SECURITY.md) · [배포 상태](docs/DEPLOYMENT.md) · [운영자 모델 실행](docs/OPERATOR.md) · [검색 실험](docs/RETRIEVAL.md)

## 로컬 실행

Python 3.12 이상, Node.js, PostgreSQL 16 + pgvector가 필요합니다. 데이터베이스는 둘 중 하나로 띄웁니다. Python 3.12라면 `uv run python scripts/dev-db.py`가 개발 의존성 `pgserver`로 임시 PostgreSQL을 띄우고 `export FITWITNESS_DATABASE_URL=...` 줄을 출력합니다(다른 터미널에서 그 줄을 실행). 3.13에서는 `pgserver` wheel이 없으므로 `docker compose up -d postgres`로 띄우고 `.env.example`의 `FITWITNESS_DATABASE_URL`을 씁니다. 환경 변수가 없으면 통합 테스트는 연결 오류로 시작하지 못합니다.

```bash
uv sync --extra dev
export PYTHONPATH=src
export FITWITNESS_DATABASE_URL='postgresql://fitwitness:local-dev-only@localhost:5432/fitwitness'   # docker compose 쓸 때. dev-db.py면 그 출력
uv run python -m fitwitness.data.generate
uv run python -m fitwitness.claims.synth var/claims 120
uv run python scripts/setup.py
npm ci --prefix web
npm run build --prefix web
uv run uvicorn fitwitness.api.app:create_app --factory --host 127.0.0.1 --port 8787
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
