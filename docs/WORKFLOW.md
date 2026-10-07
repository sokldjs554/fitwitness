# 실행 워크플로 — 상태, 재시도, 담당자 확인, 관측, 저장 도구

이 문서는 2026-10-06 브랜치에서 추가된 실행 흐름을 설명합니다. 모든 항목은 실제 PostgreSQL 위에서 통합 테스트로 검증되며(`tests/integration/test_review.py`, `test_retry.py`, `test_observability.py`, `test_saved_tools.py`), 유료 모델 호출 없이 재현됩니다.

## 상태 전이

| 상태 | 뜻 | 들어오는 길 | 나가는 길 |
|---|---|---|---|
| `queued` | 접수됨, dispatch 대기 | `POST /api/runs`, resume | worker가 lease를 잡으면 `running` |
| `running` | worker가 lease를 보유 | claim | 완료·실패·대기·재시도 |
| `retry_wait` | 일시 오류 후 백오프 중 | `TransientProviderError` | `next_attempt_at` 이후 dispatcher가 다시 claim |
| `waiting_input` | 담당자 답변 대기. dispatch 큐에서 빠져 worker를 점유하지 않음 | 근거 부족 + `review="on_unknown"` | `POST /api/runs/{id}/resume` → `queued` |
| `completed` / `failed` / `cancelled` / `stale` | 종결 또는 개정판에 의해 무효화 | | `stale`은 재검증으로만 벗어남 |

대기(`retry_wait`, `waiting_input`)에 쓴 시간은 `waited_seconds`에 누적되어 실행 마감(`deadline_seconds`)에서 제외됩니다. 사람을 기다렸다는 이유로 실행이 시간 초과로 실패하지 않습니다.

## 일시 오류 재시도와 보류함

- provider 어댑터는 429·408·5xx·연결/시간 오류를 프로세스 안에서 1초 간격 두 번까지 시도합니다(기존 동작).
- 그래도 실패하면 `TransientProviderError`로 올라오고, worker는 `jobs.fail(retryable=True)`로 `retry_wait`를 기록합니다. 백오프는 `backoff_seconds(attempt) = min(5·2^attempt, 300)`초이며 이벤트 `retry_scheduled`에 다음 시각이 남습니다.
- dispatcher는 `next_attempt_at`이 지나기 전에는 그 실행을 건드리지 않습니다. 운영자는 `Jobs.make_due()`로 즉시 재시도할 수 있습니다.
- `MAX_ATTEMPTS = 3`을 소진하면 `failed`로 종결하고 이벤트 `dead_lettered`를 남기며 dispatch 큐에서 제거합니다. 스키마·권한 오류 같은 비일시 오류는 처음부터 한 번에 `failed`입니다.
- 재시도는 checkpoint에서 이어지므로 이미 끝난 검색·조회 노드를 다시 실행하지 않습니다.

## 담당자 확인(human-in-the-loop)

`RunRequest.review`가 `"on_unknown"`이면, 그래프가 `unknown` 판정을 남긴 채 끝나려 할 때 `review` 노드가 LangGraph `interrupt()`로 멈춥니다. interrupt payload는 그대로 이벤트 `waiting_input`에 기록됩니다.

```json
{"question": "근거가 부족한 도면 2건의 판정을 입력해 주세요.",
 "pending": [{"revision_id": "…", "drawing_number": "FW-F003-A", "unknown_fields": ["material"]}],
 "accepts": {"decisions": "revision_id → match|mismatch|unknown", "reviewer": "string", "note": "string"}}
```

답변은 `POST /api/runs/{id}/resume`에 `ReviewInput`으로 보냅니다. `verdict`는 세 값만 허용되고(`approve` 같은 값은 422), 대기 중이 아닌 실행에 보내면 409입니다. worker는 `Command(resume=…)`으로 그래프를 이어가며, 담당자 판정은 `verifier_version="human-v1"`인 `Evidence`로 결정에 붙고 `Decision.reviewed_by`, `review_note`에 남습니다. 모델이 사람의 답을 재해석하지 않으며, 답하지 않은 도면은 `unknown`으로 유지됩니다.

검토대에서는 "확인 필요 시 담당자에게 묻기"를 켜고 실행하면 대기 상태에서 도면별 판정 선택과 재개 버튼이 나타납니다.

## worker 시작 시간: 미리 띄워 둔 프로세스

실행은 별도 프로세스에서 돕니다(중단·강제 종료 데모와 서버 보호를 위해). 예전에는 실행마다 새 Python 프로세스를 띄웠고, 인터프리터 기동과 LangGraph·LangChain·psycopg import에 로컬 약 1초, 공유 CPU인 무료 서버에서는 10초 넘게 걸린 뒤에야 일이 시작됐습니다.

이제 서버는 기동 시 `FITWITNESS_WARM_WORKERS`(기본 1)개의 worker를 미리 띄워 import, 연결 풀, checkpointer 스키마 확인까지 마친 채 stdin에서 기다리게 합니다(`fitwitness.runtime.pool.WarmWorkers`, worker의 `--pool` 모드). 실행이 들어오면 JSON 한 줄로 작업을 건네고, worker는 실행이 정상적으로 끝나면 전용 파이프로 `{"done": true}` 한 줄만 돌려준 뒤 살아 있는 채로 다음 작업을 기다립니다. 실행이 예외로 끝나거나 데모용 강제 종료(86)를 만나거나 시간 초과로 죽으면 프로세스가 끝나고 종료 코드가 결과가 되며(0, 86, 그 외 실패 의미는 그대로), 그때 교체 worker가 뒤에서 데워집니다. 강제 종료가 예정된 작업은 실행 중에 미리 교체 worker를 데워 두어 복구가 기다리지 않게 합니다. 또 API가 실행을 큐에 넣으면 dispatcher를 바로 깨워 최대 1초의 폴링 대기도 없앱니다.

처음에는 실행이 끝날 때마다 worker가 종료되고 교체를 새로 띄웠습니다. 공개 서버(무료 CPU)에서 첫 실행은 빨랐지만, 바로 이어서 누른 두 번째 실행은 교체 worker가 아직 import 중이라 시작까지 17~21초를 기다렸습니다(실측). 교체를 띄우는 일이 다음 실행과 같은 CPU를 두고 경쟁하기도 했습니다. worker를 재사용하면 프로세스 기동 비용을 worker당 한 번만 냅니다.

| 측정(로컬, rules provider, 도면 20개) | 이전 | 이후 |
|---|---|---|
| 큐 등록 → worker 시작 (중앙값 4회) | 1.65초 | 0.16초 |
| 큐 등록 → 완료 (중앙값 4회) | 2.07초 | 0.51초 |

## 데이터베이스 왕복: 풀, 파이프라인, 묶음 시드

호스팅 환경에서는 데이터베이스가 다른 서버에 있어 질의 하나의 왕복이 10ms 안팎입니다. 실행 한 건이 수백 번 왕복하면 CPU와 무관하게 수 초가 걸리므로, 왕복 횟수 자체를 줄였습니다.

- **연결 풀**(`FITWITNESS_DB_POOL_MAX`, 기본 8): 예전에는 요청 단위 작업마다 새 TLS 연결을 열어 연결 비용이 질의 비용보다 컸습니다. 이제 프로세스당 풀 하나에서 빌려 쓰고, 30초 넘게 쉰 연결만 빌려주기 전에 살아 있는지 확인합니다(매번 확인하면 그 자체가 왕복입니다). LangGraph checkpointer도 실행마다 새로 접속하는 대신 전용 풀의 연결을 씁니다.
- **파이프라인 모드**: 테넌트 범위 연결 블록 하나는 libpq 파이프라인에서 `BEGIN`, 역할·테넌트 설정, 문장들, `COMMIT`을 한 번에 보내고 결과를 읽거나 블록이 끝날 때만 응답을 기다립니다. 쓰기만 하는 블록은 왕복 1회, 결과를 한 번 읽는 블록은 2회입니다(psycopg의 기본 트랜잭션 처리는 자체 `BEGIN` 뒤에 동기화를 끼워 넣어 블록당 4~5회였습니다). 오류는 같은 예외 형식으로 그 시점에 드러나고 트랜잭션은 되돌립니다.
- **묶음**: 실행 시작 시 스냅샷·개정·사실을 연결 한 번에 읽어 검색과 `query_dimensions` 도구가 같은 데이터를 재사용하고, 이벤트 기록은 리스 확인·순번 계산·삽입을 한 문장으로 합쳤습니다. 체험 공간 시드는 도면 20건과 청구 8건을 각각 한 트랜잭션으로 넣고, PDF 추출 결과는 프로세스 안에서 한 번만 계산해 둡니다. 만료 공간 정리도 테넌트마다 연결을 열지 않고 한 트랜잭션으로 처리합니다. 문서 목록(`/api/documents`)은 도면마다 질의하던 것을 연결 한 번으로 줄였습니다.

응답 시간은 왕복 지연을 양방향 6ms씩 넣은 로컬 프록시 뒤의 데이터베이스로 재서 호스팅 환경을 흉내 냈습니다(rules provider, 도면 20개, 청구 8건).

| 측정(왕복 지연 12ms 흉내, 로컬) | 이전 | 이후 |
|---|---|---|
| 체험 공간 생성 `POST /api/demo-sessions` | 4.58초 | 0.63초 |
| 문서 목록 `GET /api/documents` | 1.62초 | 0.07초 |
| 실행 한 건(`execute_run`, 프로세스 내) | 3.94초 | 1.38초 |
| 실행 조회 `GET /api/runs/{id}` | 0.18초 | 0.07초 |
| 실행 한 건의 서버 응답 대기 횟수 | 138회 | 97회* |

\* 남은 응답 대기의 상당수는 큰 결과(사실 목록, checkpoint)가 여러 조각으로 도착한 것이어서 실제 왕복은 더 적습니다. 데이터베이스가 같은 호스트에 있으면(로컬 소켓) 체험 공간 생성은 0.3초, 실행은 0.25초 안팎입니다.

대기 중인 worker 하나는 약 90MB RSS를 차지합니다. 512MB 인스턴스에서는 기본값 1을 권하고, 메모리가 넉넉하면 2로 올리면 동시 실행 둘 다 즉시 시작합니다. 테스트처럼 lifespan 없이 앱을 만들면 미리 띄우지 않고 예전처럼 실행마다 cold로 띄웁니다.

## 보험 청구 워크플로

같은 상태 전이·재시도·`waiting_input`·resume 위에서 돌아가는 두 번째 워크플로입니다. 노드 구성, 지급 기준표, 멱등 원장, 측정은 [CLAIMS.md](CLAIMS.md)에 있습니다. 담당자 답변 계약은 도면 실행의 `ReviewInput` 대신 `ClaimReview{outcome, reviewer, note, total_amount?}`이며 같은 `POST /api/runs/{id}/resume`으로 들어옵니다.

## 관측

- `GET /metrics`(토큰 필요)는 프로세스 내 HTTP 지표에 더해 **DB에서 계산한** 지표를 붙입니다: `fitwitness_runs_total{state}`, `fitwitness_cost_usd_total`, `fitwitness_tokens_total{kind}`, `fitwitness_model_calls_total`, `fitwitness_tool_calls_total{tool}`, 이벤트별 카운터(`retry_scheduled`, `dead_lettered`, `waiting_input`, `resumed_by_human` …), 히스토그램 `fitwitness_model_latency_ms{role}`, `fitwitness_tool_latency_ms{tool}`. worker 프로세스가 여럿이어도 한 곳에서 맞는 값을 냅니다. DB가 내려가면 HTTP 지표만 내려가고 주석으로 사유를 남깁니다.
- `GET /api/runs/{id}/trace`는 이벤트 로그를 span tree로 바꿔 줍니다: 루트 `run`, planner/challenger 그룹, 도구·모델 span과 각 duration. 외부 tracing backend 없이 한 실행을 디버깅하는 용도입니다. 같은 trace는 `?format=otlp`로 OTLP/HTTP 본문으로 받거나, `OTEL_EXPORTER_OTLP_ENDPOINT`를 주면 worker가 실행이 끝날 때 Jaeger·Tempo 같은 백엔드로 보냅니다([OBSERVABILITY.md](OBSERVABILITY.md)).

## 에이전트가 정의하는 저장 검색 도구

planner는 `define_search_tool`로 자기 작업 공간에 재사용 가능한 검색을 저장하고 `run_saved_search`로 이름으로 실행합니다. 정의는 **코드가 아니라 데이터**입니다.

| 필드 | 제약 |
|---|---|
| `name` | `^[a-z][a-z0-9_]{2,40}$`, 내장 도구 이름과 충돌 불가 |
| `channels` | `exact`·`bm25`·`semantic` 중 1–3개, 작업 공간에 연결되지 않은 채널은 거부 |
| `query_template` | `{query}` 자리 필수, 500자 이하. 호출자의 텍스트만 치환 |
| `fields` | 치수 필드 0–5개. 각 결과에 `query_dimensions`와 같은 경로로 읽어 붙임 |
| `top_k` | 1–20 |

저장 도구는 `fw_saved_tools`에 도면과 같은 RLS로 격리되고 테넌트당 20개로 제한됩니다. 가져온 사실은 관측 시 반환 도면과 출처가 일치하는지 검사받으며, 검색 결과만으로는 여전히 판정하지 않습니다. 모델 컨텍스트의 `saved_tools`에 정의 목록이 보이고 `GET /api/tools`로 운영자가 확인합니다.

## 범위와 한계

- 재시도·대기·재개는 통합 테스트와 로컬 서버에서 확인했고, 수일 단위 대기나 운영 환경의 장기 실행은 측정하지 않았습니다.
- 담당자 인증은 세션 범위(테넌트)에 의존하며 별도의 승인 권한 모델은 없습니다.
- 저장 도구는 텍스트 검색만 묶습니다. 이미지 검색·영역 읽기는 포함하지 않습니다.
