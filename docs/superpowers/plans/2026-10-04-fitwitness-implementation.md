# FitWitness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 자연어·이미지로 도면을 찾고, 후보의 결정적 반례를 확인하며, 도면 변경과 실행 중단 이후에도 근거를 재검증하는 동작 가능한 Agent와 데모를 만든다.

**Architecture:** React·TypeScript 화면과 FastAPI API, LangGraph Python worker를 사용한다. PostgreSQL은 문서·증거·실행·checkpoint를 저장하고, pgvector와 BM25 및 CAD 파생 특징으로 검색한다. 결정적 조건 검증은 LLM과 분리하여 기준선과 Agent가 동일하게 사용한다.

**Tech Stack:** Python 3.12, FastAPI, Pydantic, SQLAlchemy·Alembic·psycopg, PostgreSQL·pgvector, LangChain·LangGraph, OpenAI·Anthropic adapters, CadQuery, pdfplumber·pypdfium2, sentence-transformers·OpenCLIP, React·TypeScript·Vite, OpenTelemetry·Prometheus, pytest·Playwright, Docker·GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-10-04-fitwitness-design.md` (사용자가 2026-10-04 01:08 KST에 설계 기준 진행을 승인).

**Status:** 구현 전 작업 계획. 체크되지 않은 항목은 완료되지 않았다. 예상 파일·함수·명령을 정의한 것이며 현재 구현의 존재를 뜻하지 않는다.

## Global Constraints

- 한글 UI·README를 기본으로 하며 자체 코드 라이선스는 Apache-2.0을 제안한다. 의존 라이선스는 별도 고지한다.
- 최초 지원 형식은 PDF·PNG·JPEG·STEP으로 제한하고 DWG 지원은 표기하지 않는다.
- 추출기 입력에는 정답 sidecar를 전달하지 않는다.
- test는 baseline과 budget을 정한 뒤 동결한다. threshold·prompt 선택은 검증 split에서 수행한다.
- 모델이 만든 임의 Python·shell 코드를 서버에서 실행하지 않는다.
- 모델 실험은 고정된 평가 표본에 최소 3회 반복한다.
- 최종 코드에서 전체 검증을 두 번 반복한다. 각 반복은 새 테스트 DB와 새 브라우저 세션을 사용한다.
- 키 부재로 skip된 테스트는 통과로 집계하지 않는다.
- Live 실행과 기록 재생을 화면에 명확하게 구분한다.
- 기존 저장소는 수정하지 않는다. 원격 저장소는 `sokldjs554/fitwitness` 이름 사용 가능 여부 확인 후 새로 만든다.
- 버전은 첫 설치 성공 시 `uv.lock`, `web/package-lock.json`, 모델 snapshot revision, Docker image digest에 고정한다. 설치 전 임의 버전을 검증 완료 값으로 쓰지 않는다.

## Review Focus

1. 단위 누락·소수점 쉼표·40mm/4cm·마이너스 공차: 임의 보정하지 않고 확정 가능한 값만 비교한다. Task 1·3.
2. 같은 도번의 두 승인 개정이 충돌하거나 실행 중 새 개정이 도착함: 임의 최신 선택·오래된 결과 확정을 금지한다. Task 2·7.
3. 회전·부분 잘림·저해상도 도면과 OCR의 0/O 혼동: 근거 위치를 보존하고 알 수 없는 필드는 보류한다. Task 3·10.
4. 재접속·중복 클릭·중복 재개·worker 종료: 동일 업무 결과는 한 번 저장되고 이벤트를 빠짐없이 재조회한다. Task 6·8·9.
5. 다른 방문자의 document/run ID, 악성 경로·도면 지시문: 자료·trace·비밀이 다른 tenant로 넘어가지 않는다. Task 2·5·8·9.

---

## 파일 경계

| 경로 | 책임 |
|---|---|
| `src/fitwitness/contracts.py`, `settings.py` | 검증 가능한 데이터 계약·실행 설정 |
| `src/fitwitness/data/` | 통제 도면 제작·gold·split manifest·외부 데이터 가져오기 |
| `src/fitwitness/storage/` | DB 테이블·권한·원본 파일·snapshot·lease·결과 transaction |
| `src/fitwitness/ingest/` | PDF·이미지·STEP를 근거와 파생 특징으로 변환 |
| `src/fitwitness/retrieval/` | exact·BM25·text/image vector·CAD 특징·fusion·rerank |
| `src/fitwitness/verification/` | 조건 비교·출처 검증·개정 충돌·변경 영향 계산 |
| `src/fitwitness/agents/` | 모델 adapter·typed tools·LangGraph·예산·프롬프트 |
| `src/fitwitness/runtime/` | worker·checkpoint·재시도·취소·관측 |
| `src/fitwitness/api/` | 인증 세션·파일·검색·실행·이벤트·결과·평가 API |
| `src/fitwitness/evaluation/` | 방법 비교·지표·반복 실행·bootstrap·보고서 |
| `web/src/features/` | 검색·근거·3D·workflow·방식 비교 UI |
| `tests/unit/`, `tests/integration/`, `tests/live/`, `web/e2e/` | 격리된 검증 층 |
| `scripts/`, `deploy/`, `.github/workflows/`, `docs/` | 재현·배포·최종 gate·문서 |

API·worker·평가 도구는 동일 core 모듈을 가져온다. API 내부 HTTP 호출로 unit test를 만들거나 UI에 별도 판정 로직을 복제하지 않는다. Task 간 인터페이스를 바꾸면 의존 task와 이 문서를 같은 변경에서 갱신한다.

## 공통 계약

`contracts.py`의 Pydantic 모델은 기본 `extra='forbid'`다. 모든 ID는 UUID 문자열이고 hash는 SHA-256 hex다. `TenantScope`는 인증 계층에서만 생성하며 API body의 tenant 문자열을 신뢰하지 않는다.

| 타입 | 필드 또는 값 |
|---|---|
| `TenantScope` | tenant_id, user_id, role: viewer/operator/admin |
| `SourceRef` | revision_id, source_hash, page(1-based), bbox(0..1 정규화) 또는 feature_id |
| `Requirement` | id, field, operator(eq/range), value, unit, required, source_text |
| `Fact` | id, field, value, unit, source: SourceRef, method, certainty(verified/uncertain) |
| `DrawingRevision` | id, tenant_id, document_id, drawing_number, family_id, revision_label, supersedes, approval, effective_from, source_hash |
| `Verdict` | match, mismatch, unknown |
| `Evidence` | requirement_id, candidate_revision_id, fact_ids, source_refs, verdict, summary, verifier_version |
| `Candidate` | revision_id, scores: dict[str,float], facts: list[Fact] |
| `Decision` | revision_id, verdict, evidence: list[Evidence], snapshot_id, stale |
| `SearchRequest` | text, image_id?, requirements: list[Requirement], top_k(1..50) |
| `SearchSnapshot` | id, revision_ids, index_hash, created_at |
| `SearchPlan` | operations: list[ToolRequest] (최대 8개), stop_condition |
| `ToolRequest` | name(등록 enum), arguments(도구별 Pydantic 모델) |
| `ToolObservation` | call_id, items, source_refs, error_code?, elapsed_ms, snapshot_id |
| `RunRequest` | search: SearchRequest, mode(fixed/react/fitwitness), provider(openai/anthropic/fixture), model_id, budget |
| `RunState` | queued/running/waiting_input/retry_wait/completed/failed/cancelled/stale |
| `RunView` | id, state, decisions, question?, usage, error?, input_hash, snapshot_id, provider, model_id |
| `RunEvent` | run_id, seq, kind, timestamp, payload, trace_id |
| `Usage` | input_tokens, output_tokens, model_calls, tool_calls, cost_usd? |
| `Budget` | max_model_calls, max_tool_calls, max_tokens, max_cost_usd, deadline_seconds |

`value`는 string 또는 Decimal 기반 숫자·구간 모델이며 NaN/Infinity를 금지한다. `cost_usd`는 가격표가 확인되지 않은 경우 null이다. 모델이 만든 금액을 사용량으로 저장하지 않는다.

## Task 1: 독립 평가가 가능한 데이터와 조건 검증

**Files:** `pyproject.toml`, `src/fitwitness/contracts.py`, `verification/conditions.py`, `data/generate.py`, `data/splits.py`, `data/manifest.py`, `tests/conftest.py`, `tests/unit/test_conditions.py`, `tests/unit/test_dataset.py`.

**Interfaces:**
- Produces: `verify(requirements: list[Requirement], candidate: Candidate, snapshot_id: str) -> Decision`.
- Produces: `generate_dataset(output: Path, *, families: int=30, variants: int=6, seed: int=1701) -> DatasetManifest`.
- Produces: `split_families(family_ids: list[str], seed: int=1701) -> dict[str,list[str]]` (18 train/6 dev/6 test).
- `DatasetManifest`: version, seed, document_entries, query_entries, family_splits, file_hashes, generator_commit. 각 entry는 파일 경로와 출처를 포함하며 gold 경로는 평가 전용이다.
- Fixtures: `bracket_40`, `bracket_42`, `bracket_missing_material`, `req_40mm`, `req_4cm`, `req_stainless`, `scope_a`, `scope_b`.

- [ ] Prepare isolated project environment and test runner, resolve dependency versions and save lockfiles. Initialize a new local Git repository. Verify `sokldjs554/fitwitness` name availability and create a private remote through a supported authorized path; browser fallback requires its tool-specific approval. Missing remote access does not block local implementation.
- [ ] Write `test_40mm_rejects_42mm`, `test_4cm_matches_40mm`, `test_missing_material_is_unknown`, `test_uncertain_ocr_is_unknown`, `test_invalid_number_rejected`, `test_tolerance_overlap_does_not_prove_containment`. Assert exact verdicts and cited source hashes.
- [ ] Run `uv run pytest tests/unit/test_conditions.py -q`; record failure from missing contracts/verifier, not a broken pytest invocation.
- [ ] Implement contracts and interval comparison. Numeric requirement ranges must contain candidate toleranced intervals to count as match. Unknown units, missing tolerance semantics, unknown materials stay unknown.
- [ ] Write `test_manifest_has_30_families_180_drawings_240_queries`, `test_family_splits_disjoint`, `test_hashes_match_assets`, `test_gold_unavailable_to_ingest`. Use family-aware fixtures and separate input/gold directories.
- [ ] Implement CadQuery generation for bracket/flange/shaft/housing classes: 30 parameterized base designs, 6 variants each. Describe these as 30 synthetic families, not 30 independently sourced industrial designs. Produce STEP, PDF, PNG and held-out gold; 8 query categories × 30 families = 240 queries.
- [ ] Run both test files; inspect one PDF/STEP pair per geometry class; commit `feat: 도면 데이터 계약과 조건 검증 추가`.

## Task 2: PostgreSQL·문서 버전·tenant 경계

**Files:** `storage/models.py`, `storage/repository.py`, `storage/blobs.py`, `storage/auth.py`, `migrations/`, `settings.py`, `tests/integration/test_storage.py`, `tests/integration/test_tenants.py`.

**Interfaces:**
- Consumes: common contracts.
- Produces: `Repository.add_revision(scope: TenantScope, revision: DrawingRevision, payload: bytes) -> str`; `snapshot(scope: TenantScope) -> SearchSnapshot`; `load_facts(scope, revision_id: str) -> list[Fact]`.
- Produces: `Repository.finalize(scope, run_id: str, snapshot_id: str, decisions: list[Decision], lease_token: str) -> bool` (false if snapshot/lease is stale).
- Produces: `BlobStore.put(scope, data: bytes, media_type: str) -> str`; `get(scope, blob_id: str) -> bytes`.
- Fixtures: disposable PostgreSQL DB, app role without BYPASSRLS, `repo`, `blob_store`. Required integration mode errors if DB unavailable; it cannot silently skip.

- [ ] Establish a disposable PostgreSQL execution path before integration tests: local user-owned PostgreSQL binaries when available, otherwise GitHub CI service container in the new repository. Add the minimal integration CI job here and extend it in Task 11. An unavailable execution path stays BLOCKED and does not become a SQLite substitute.
- [ ] Write cross-tenant tests for revision lookup, blob lookup and snapshot search; assert not found and zero foreign rows. Write `test_conflicting_approved_revisions_are_unresolved`, `test_cycle_rejected`, `test_finalize_rejects_changed_snapshot`.
- [ ] Run `uv run pytest tests/integration/test_storage.py tests/integration/test_tenants.py -q --require-postgres`; confirm behavioral failures with a reachable empty test DB.
- [ ] Implement Alembic migrations, pgvector extension, composite tenant foreign keys, RLS, explicit transaction-local tenant scope, immutable version rows and typed conflict responses. Store blob bytes in PostgreSQL initially to preserve original files across ephemeral web restarts; cap file size in Task 3.
- [ ] Test pooled connections A→B and rollback for tenant scope leakage, concurrent duplicate revision insertion, revision labels `Rev.2`/`Rev.10`, and ambiguous supersedes branches.
- [ ] Run integration suite with fresh DB; commit `feat: 도면 버전과 tenant 격리 저장소 추가`.

## Task 3: 실제 PDF·이미지·STEP에서 근거 추출

**Files:** `ingest/pdf.py`, `ingest/image.py`, `ingest/cad.py`, `ingest/pipeline.py`, `ingest/limits.py`, `tests/unit/test_ingest.py`, `tests/integration/test_ingestion.py`.

**Interfaces:**
- Produces: `extract_pdf(data: bytes, revision: DrawingRevision) -> list[Fact]`; `extract_image(data: bytes, revision, reader: VisionReader) -> list[Fact]`.
- Produces: `extract_step(data: bytes, revision) -> CadFeatures`; `ingest(scope, revision_id: str, repo: Repository, reader: VisionReader) -> IngestResult`.
- `CadFeatures`: source_hash, feature_hash, bbox_mm, volume_mm3, surface_mm2, circular_features, preview_mesh_blob_id. `IngestResult`: facts, cad_features?, warnings, status.
- `VisionReader.read_regions(image: bytes, request: str) -> list[Fact]` Protocol lives in `contracts.py`; implementation is Task 5. Unknown OCR/VLM values remain uncertain until validated.

- [ ] Write tests for rotated PDF bounding boxes, normalized coordinates, raster-only PDF, duplicate labels, absent unit, unsupported format, invalid STEP, hash mismatch. Assert every fact points to actual source content or is explicitly uncertain.
- [ ] Run `uv run pytest tests/unit/test_ingest.py -q`; record missing extraction failures.
- [ ] Implement pdfplumber vector text, pypdfium2 raster rendering, image normalization and CadQuery STEP importer. Derive preview mesh and CAD features from the same STEP bytes; do not fill facts from gold sidecars.
- [ ] Enforce 20 MiB input, 20 PDF pages, 16 megapixel rendered image, 30-second parser subprocess deadline. Reject extension/magic mismatches; use owned temporary paths without user-selected filesystem destinations.
- [ ] Run extraction on generated assets with gold available only to evaluator. Produce per-field error JSONL and inspect four drawings. Run `test_raster_rotation_citations`, `test_parser_timeout_leaves_no_partial_index`, `test_ingest_cannot_read_gold`.
- [ ] Commit `feat: PDF 이미지 STEP 근거 추출 연결`.

## Task 4: 멀티모달 검색과 강한 비 Agent 기준선

**Files:** `retrieval/exact.py`, `bm25.py`, `embeddings.py`, `geometry.py`, `fusion.py`, `pipeline.py`, `tests/unit/test_retrieval.py`, `tests/integration/test_retrieval.py`.

**Interfaces:**
- Produces: `index_revision(scope, revision_id: str, repo: Repository, encoders: Encoders) -> None`.
- Produces: `search(scope, request: SearchRequest, snapshot: SearchSnapshot, repo: Repository, encoders: Encoders, channels: set[str]) -> list[Candidate]`.
- Produces: `reciprocal_rank_fusion(rankings: dict[str,list[str]], k: int=60) -> list[tuple[str,float]]`.
- `Encoders` exposes `encode_text(texts: list[str])`, `encode_image(images: list[bytes])`, `rerank(query: str, passages: list[str])`. Model IDs: multilingual-e5-small, OpenCLIP ViT-B-32/laion2b_s34b_b79k, bge-reranker-v2-m3; resolve exact downloadable revisions and license notices before use. If inaccessible, document blocked channel rather than substitute fake vectors.

- [ ] Write exact drawing-number preservation (`BS-120` differs from `BS-1200`), Korean aliases, RRF expected ordering, deterministic tie-break, zero-result and stale-snapshot tests.
- [ ] Run `uv run pytest tests/unit/test_retrieval.py -q`; confirm expected failures.
- [ ] Implement lexical indexing with task-pinned tokenizer, dense pgvector retrieval, image vectors, normalized CAD feature distance and RRF. Tenant + snapshot restriction applies before ranking for every channel.
- [ ] Implement independent baseline modes: BM25; hybrid+rerank; hybrid+deterministic verifier. Preserve per-channel scores and top-k traces.
- [ ] Run each channel against real encoders and generated data; compare held-out family metrics, test unrelated image behavior and `test_ann_never_returns_foreign_tenant`.
- [ ] Commit `feat: 도번 키워드 의미 형상 검색과 기준선 추가`.

## Task 5: 두 모델 provider·typed tools·호출 예산

**Files:** `agents/providers.py`, `agents/tools.py`, `agents/budget.py`, `agents/prompts/`, `runtime/subprocess_tools.py`, `tests/unit/test_tools.py`, `tests/unit/test_providers.py`, `tests/live/test_providers.py`.

**Interfaces:**
- Produces: `create_model(provider: str, model_id: str, budget: Budget) -> ModelClient`; `ModelClient.plan(context: dict, tools: list) -> SearchPlan`; `ModelClient.read_regions(...)` implements `VisionReader`.
- Produces: `build_tools(scope, snapshot, repo, encoders) -> list[BaseTool]`; `execute_plan(plan: SearchPlan, context: ToolContext) -> list[ToolObservation]`.
- Tools: `search_exact(drawing_number)`, `search_keyword(query,top_k)`, `search_semantic(query,top_k)`, `search_image(image_id,top_k)`, `query_dimensions(revision_id,fields)`, `read_region(revision_id,page,bbox)`, `compare_revisions(old_id,new_id)`. No tool accepts shell source.
- `ToolContext`: authenticated scope, fixed snapshot, repo, encoders, budget tracker, run_id. `BudgetTracker.reserve(estimated_tokens:int, estimated_cost:Decimal) -> bool` and `record(usage:Usage) -> None`.

- [ ] Write tests for unregistered tools, additional schema properties, oversized plans, path traversal, injected shell tokens, cross-tenant region reads, exhausted budget, malformed output and provider-specific retry classification.
- [ ] Run unit files; confirm failures before implementation.
- [ ] Implement actual LangChain OpenAI and Anthropic adapters, structured output, model IDs from server config, bounded schema repair (1 retry), retry policy (3 total attempts with jitter), provider-change events. Fixture provider lives under tests and never appears as Live AI.
- [ ] Register CLI extraction using `subprocess` argv, `shell=False`, fixed executables, sanitized environment, resource/time/output caps. Any tool result retains evidence refs and snapshot ID.
- [ ] Add separate versioned extraction/planner/challenger prompts; treat source document text as data. Test injected “ignore previous instructions” and secret-fetch requests never change allowed tools or expose environment.
- [ ] Run live smoke with both provider secrets injected through deployment/CI secret settings. Assert provider/model/request ID, real tool invocation, image interpretation and token usage. If either secret is absent, emit BLOCKED and retain this task's live step unchecked.
- [ ] Commit `feat: 모델 provider와 제한된 검색 도구 연결`.

## Task 6: LangGraph 상태 저장·ReAct·반례 검토·복구

**Files:** `agents/graph.py`, `agents/nodes.py`, `runtime/worker.py`, `runtime/jobs.py`, `runtime/checkpoints.py`, `tests/integration/test_workflow.py`, `tests/integration/test_recovery.py`.

**Interfaces:**
- Consumes: Tasks 1–5 contracts, repo, tools and provider.
- Produces: `build_graph(services: AgentServices, checkpointer) -> CompiledStateGraph`; `enqueue(scope, request: RunRequest, idempotency_key: str) -> RunView`; `resume(scope, run_id: str, answer: dict, expected_event_seq: int) -> RunView`; `cancel(scope, run_id: str) -> RunView`.
- `AgentServices`: repo, encoders, model factory, clock, telemetry. Graph state: request, requirements, snapshot, candidates, evidence, visited tool arguments, budget, pending question, decisions.
- Produces: `worker_once(worker_id: str) -> bool`; `RunRepository.claim(worker_id: str, lease_seconds: int=30) -> Lease|None`; `heartbeat(lease_token: str) -> bool`. `Lease`: run_id, tenant_id, fencing_token, expires_at.

- [ ] Write controlled-observation test: missing material leads to region lookup while differing hole spacing leads to dimensional rejection; same query must not always execute a fixed trace.
- [ ] Write tests for fixed/react/fitwitness modes, separate challenger context, max-call termination, question interruption/resume, repeated tool prevention and cancel-before-finalize. Run workflow tests RED.
- [ ] Implement graph with PostgresSaver, event persistence and bounded loops. The verifier finalizes evidence; an LLM vote cannot override contradicted numeric facts. fixed uses predefined steps; react one context; fitwitness separates retrieval planner and challenger.
- [ ] Implement row-locked jobs, fencing lease, heartbeat every 10 seconds, capped retry, SIGTERM handling and transactional final result insert. Scope checkpointer thread IDs through server-owned tenant/run mapping; never accept arbitrary checkpoint keys from clients.
- [ ] Add subprocess crash tests after checkpoint, after model return and before final commit. Restart from real PostgreSQL with the same run ID. Assert one final result, no foreign tenant state, no stale worker write and honest duplicate model-attempt accounting.
- [ ] Run integration recovery tests on a new DB twice; commit `feat: 반례 Agent와 영속 실행 복구 추가`.

## Task 7: 도면 변경의 영향 계산과 선택적 재검증

**Files:** `verification/revisions.py`, `verification/impact.py`, `runtime/revalidation.py`, `tests/integration/test_revalidation.py`.

**Interfaces:**
- Produces: `revision_diff(old: list[Fact], new: list[Fact]) -> list[FactChange]`; `affected_decisions(scope, changes: list[FactChange], repo) -> list[str]`; `request_revalidation(scope, decision_ids: list[str], new_snapshot_id: str) -> list[str]`.
- `FactChange`: document_id, old_revision_id, new_revision_id, field, before_fact_id?, after_fact_id?, changed_content_hash.

- [ ] Write `test_revision_40_to_42_revokes_old_match`, `test_unrelated_document_does_not_revalidate`, `test_added_candidate_invalidates_candidate_set`, `test_approval_branch_conflict_holds_result`, `test_commit_races_revision_change`.
- [ ] Run tests RED before adding change handlers.
- [ ] Persist evidence dependency edges and transactional outbox events when revisions change. Immediately mark dependent results stale, then enqueue bounded revalidation. Candidate-set changes re-run retrieval even when old positive fact refs did not change.
- [ ] Preserve unchanged extraction observations only when source hash and all tool version inputs match. Recompute changed facts; verify final result against full recomputation on the same snapshot.
- [ ] Run integration tests, report saved tool calls without claiming saved external calls that actually retried; commit `feat: 도면 변경 시 판단 무효화와 재검증 추가`.

## Task 8: 실제 API·세션·이벤트·관측·격리된 장애 체험

**Files:** `api/app.py`, `api/sessions.py`, `api/documents.py`, `api/runs.py`, `api/events.py`, `api/evals.py`, `runtime/telemetry.py`, `runtime/demo_faults.py`, `tests/integration/test_api.py`, `tests/integration/test_observability.py`.

**Interfaces:**
- Produces: `create_app(settings: Settings) -> FastAPI`.
- Routes: `POST /api/demo-sessions`, `POST /api/documents`, `POST /api/search`, `POST /api/runs`, `GET /api/runs/{id}`, `GET /api/runs/{id}/events?after=SEQ`, `POST /api/runs/{id}/resume`, `POST /api/runs/{id}/cancel`, `POST /api/demo/runs/{id}/fault`, `GET /api/evaluations`, `GET /health`, `GET /ready`.
- `GET events`: server-sent events with monotonic stored seq and Last-Event-ID reconnect support. POST body schemas derive from contracts; responses include typed code/message/retryable errors.

- [ ] Write API tests for signed HttpOnly SameSite cookie scope, CSRF origin checks on writes, viewer/operator roles, idempotency conflict, session expiration, malformed upload, cancel/resume conflicts, missed-event replay and cross-tenant IDs.
- [ ] Run API tests RED with real test DB.
- [ ] Implement API, request validation, bounded SSE buffers and client backpressure behavior; store authoritative event history in PostgreSQL. Never put provider keys or unrestricted document text in logs.
- [ ] Instrument spans for API→graph→tool→provider→DB, counters for calls/errors/holds, histograms for latency/recovery, model usage. Keep run_id in traces/logs, not high-cardinality Prometheus labels. Protect `/metrics` as an operator endpoint.
- [ ] Implement demo fault by terminating only the per-run child process, never shared API/supervisor. Server injects one failure at a designated checkpoint; public users cannot select arbitrary PID, code or paths.
- [ ] Run two simultaneous sessions; kill A child and assert B completes, A resumes, both traces stay isolated. Health returns git SHA; readiness tests DB/migrations/index availability without making paid model calls.
- [ ] Commit `feat: 데모 API 이벤트 관측과 장애 체험 연결`.

## Task 9: 한눈에 이해되는 웹 데모

**Files:** `web/package.json`, `web/src/App.tsx`, `web/src/lib/api.ts`, `web/src/features/search/`, `evidence/`, `workflow/`, `comparison/`, `web/src/styles.css`, `web/e2e/fitwitness.spec.ts`.

**Interfaces:**
- Consumes: Task 8 HTTP contracts; generate TypeScript types from committed OpenAPI schema.
- Components: `SearchWorkbench`, `CandidateGrid`, `EvidencePanel`, `DrawingViewer`, `CadViewer`, `RunTimeline`, `MethodComparison`; shared `useRun(runId)` handles SSE reconnect and refetch.

- [ ] Write Playwright cases for query and condition changes, insufficient evidence, Rev.C arrival, fault recovery and method comparison. Assert server run IDs, verdict changes and actual source highlight coordinates; no UI-only fake state transitions.
- [ ] Run `npm --prefix web run test:e2e -- --project=chromium`; confirm absent UI failure against running API fixture server.
- [ ] Implement Korean screen with headline “닮은 도면 중에서 조건에 맞는 부품을 찾아보세요.” Use candidate image + condition chips + evidence panel; keep developer trace collapsed. Neutral ivory/slate with teal status, amber unknown, clear contrast, keyboard and focus support.
- [ ] Render PDF page regions and STEP-derived mesh with provenance hash; add touch-friendly controls, loading/error/empty states and retry. Desktop 1440×900; mobile 390×844 with no horizontal content loss.
- [ ] Show provider/mode, Live vs recorded playback, actual latency and unavailable cost as “미집계”. Replay includes immutable input/model/timestamp; changing query starts new run or exits replay.
- [ ] Run browser tests for mobile, keyboard-only input, repeated clicks, refresh during run and reconnect after dropped SSE. Capture screenshots only after functional assertions pass.
- [ ] Commit `feat: 도면 근거와 재검증을 체험하는 한글 데모 추가`.

## Task 10: 자동 평가·독립 데이터·연구 결과

**Files:** `evaluation/metrics.py`, `evaluation/baselines.py`, `evaluation/runner.py`, `evaluation/report.py`, `data/import_external.py`, `evals/protocol.json`, `evals/pricing.json`, `tests/unit/test_metrics.py`, `tests/integration/test_eval_runner.py`.

**Interfaces:**
- Produces: `evaluate(protocol_path: Path, output_dir: Path) -> EvaluationManifest`; `summarize(predictions: list[Prediction], family_bootstrap_seed: int=1701) -> MetricReport`.
- `Prediction`: query_id, family_id, split, relevant_revision_ids, ranked_ids, gold_verdicts, decisions, gold_facts, extracted_facts, source_validity, usage, timings, repeat, status.
- `EvaluationManifest`: commit, dependency/model/prompt hashes, data hash, methods, provider/models, budgets, split freeze hash, repeats, raw_result_paths, blocked_items.
- Methods: bm25, hybrid_rerank, hybrid_verify, fixed, react, fitwitness. Baselines use identical corpus/snapshots/condition verifier; graph methods receive equal model and tool budgets.

- [ ] Write known-list metric tests: Recall@5 0/1 boundaries, graded nDCG, tie handling, empty relevance excluded with explicit denominator, wrong match rate, coverage-risk, field/citation accuracy, clustered bootstrap and incomplete run denominators.
- [ ] Run metric tests RED; implement and run PASS before any experiment claims.
- [ ] Implement runner with train/dev/test separation; freeze `protocol.json` after dev selection and before held-out run. Compare 6 methods, two providers on model-using methods, 3 repetitions and family-based confidence intervals. Record skipped/failed calls rather than dropping failures.
- [ ] Inspect Fusion Gallery license and distribution terms before importing; record URL, hash and license. Select at least 30 usable external geometry samples by a fixed hash ordering. Record rejected files; manually verify geometry/query pairs. Do not label synthetic condition additions as real industrial ground truth.
- [ ] Run extraction perturbations (rotation/crop/resolution), held-out synthetic families and external retrieval as separate tables. Gold remains unavailable to the runtime. If external acquisition or model secrets are blocked, show the corresponding section as unmeasured.
- [ ] Generate JSONL plus human-readable report, raw-error gallery and cost/quality/latency chart. Main comparison: fitwitness vs fixed wrong-match rate under equal budget; nonpositive or inconclusive improvement stays visible.
- [ ] Commit `feat: 기준선 제거 실험과 재현 가능한 평가 추가`.

## Task 11: 새 저장소·Docker·CI·실제 배포

**Files:** `Dockerfile`, `compose.yaml`, `.dockerignore`, `.env.example`, `deploy/README_KO.md`, `deploy/render.yaml`, `.github/workflows/verify.yml`, `.github/workflows/live-evals.yml`, `scripts/verify_release.py`, `scripts/verify_deployed.py`.

**Interfaces:**
- CLI: `uv run python scripts/verify_release.py --rounds 2 --require-postgres --require-containers --require-live-providers --output artifacts/release`.
- CLI: `uv run python scripts/verify_deployed.py --base-url URL --expected-sha SHA --output artifacts/deployed`.
- Each script returns nonzero for required blocked gates and writes statuses PASS/FAIL/BLOCKED with exact command, timestamps, return code and artifact hash.

- [ ] Write release-gate contract tests: a passing unit suite plus missing DB/live provider/container must never return all-pass; mismatched deployment SHA must fail.
- [ ] Run contract tests RED; implement gate aggregator and command provenance storage.
- [ ] Build API/web and worker images; Compose includes PostgreSQL/pgvector, API, worker and optional local observability collector. Heavy encoders and CAD imports execute during indexing/worker paths, not every API health request. Never reuse downloaded model weights whose hashes differ from manifest.
- [ ] Reuse the new remote prepared in Task 1; if access was blocked, resolve that gate now. Verify the owner and new repository identity before pushing source and workflows. Do not reuse or modify an older repository.
- [ ] Configure CI unit·integration·E2E·container jobs, disposable databases and minimal token permissions. Provider eval workflow is manual and uses secret names only. GitHub usage blocks leave CI gate BLOCKED.
- [ ] Read actual hosting capabilities and cost before resource creation. Prefer existing authorized suitable infrastructure or a free plan that supports persistent DB and isolated workers. Render Blueprint is an optional Python deployment path, not permission to create paid resources. A static-only host is not a substitute for tested Python/PostgreSQL behavior.
- [ ] Deploy the web/API, worker and persistent DB; set secrets through hosting settings without exposing values. Run deployed health/SHA, search, evidence, revision and recovery checks. If only a recorded/static preview can be hosted, label that explicitly and leave full live deployment unchecked.
- [ ] Commit `chore: 배포 구성과 실제 실행 검증 gate 추가`.

## Task 12: 최종 두 차례 검증·영상·한글 제출 문서

**Files:** `README.md`, `LICENSE`, `THIRD_PARTY_NOTICES.md`, `docs/architecture.md`, `docs/decisions/`, `docs/tool-contracts.md`, `docs/development-log.md`, `docs/release-verification.md`, `docs/research-results.md`, `docs/media/`, `docs/submission/`.

**Interfaces:** consumes immutable run/eval/release manifests. README links to actual repository demo URL and versioned raw evidence, not invented metrics.

- [ ] Verify complete spec-to-task mapping and installed dependency/model licenses; document imported data separately from own Apache-2.0 code. Record actual Codex-assisted development steps without inventing external reviews or Claude Code use.
- [ ] Execute full release script for two clean DB/browser rounds on final code; inspect failures and required BLOCKED gates. Fix regressions, then repeat the affected tests and final release gate.
- [ ] Cross-check health SHA, deployment SHA, video/screenshots SHA and eval manifests. Documentation-only post-verification commits retain explicit tested-code SHA and current release mapping rather than falsely claiming old evidence tested new code.
- [ ] Record 2–3 minute real demo with readable pauses: query → dimensional mismatch → evidence → revision change → recovery → measured comparison. Export MP4 and short GIF from verified live execution; label replay footage as replay.
- [ ] Write concise Korean README, setup commands, architecture, Evals interpretation, failure analysis and submission PDF. The PDF must reflect actual outcomes, negative results and remaining limits; render and visually review every page.
- [ ] Return repository URL, working demo URL, verified test/experiment summaries, artifacts and any incomplete gate. No “완료” claim unless all mandatory gates passed. Commit `docs: 데모와 검증 결과 및 제출 자료 정리`.

## 실행 방식 제안

**Native (직접 구현)를 권장한다.** 검색·근거·snapshot·복구 상태가 같은 계약을 공유하므로 같은 실행자가 순차 구현하면 인터페이스 변경을 즉시 함께 반영하기 쉽다. 개별 Agent를 계속 교체하는 비용을 줄이고, 마지막에 별도 관점의 전체 코드 검토를 수행한다. 다른 선택은 task별 별도 구현·리뷰 Agent를 사용하는 Subagent-driven 방식이다.

## 현재 환경에서 확인한 사실

2026-10-04: Python 3.12.14, Node·npm·git·uv·ffmpeg 사용 가능. CadQuery·pypdfium2 설치 확인. FastAPI·SQLAlchemy·psycopg·LangGraph·LangChain·pytest·Playwright·sentence-transformers는 해당 Python 환경에서 미설치. Docker·psql·postgres 실행 파일 없음. 설치·접근 경로를 확보하기 전에는 해당 테스트를 통과했다고 기재하지 않는다.

이 계획 작성 단계에서는 제품 코드를 생성하거나 의존성을 설치하거나 원격 저장소·배포 서비스를 생성하지 않았다.

## 자체 검토

| 설계 요구 | 담당 Task |
|---|---|
| 통제 데이터·gold 분리·family split | 1·10 |
| 실제 PDF·이미지·STEP와 출처 | 3·4·9 |
| 조건 검증·도번·복합 검색·기준선 | 1·4·10 |
| LangChain·두 provider·VLM·도구 구성·프롬프트 | 5·6 |
| ReAct·Multi-Agent·반례·상태 저장·질문 | 6 |
| 개정 무효화·선택적 재검증 | 2·7 |
| tenant·재시도·worker crash·관측 | 2·5·6·8 |
| 다섯 데모·live/기록 구분·모바일 | 8·9 |
| 연구 실험·실패 보고·비용·지연 | 10 |
| 새 저장소·Docker·CI·배포 SHA | 11 |
| 두 차례 전체 검증·영상·한글 문서·PDF | 12 |

공통 타입명과 task 간 호출 이름을 대조했다. Review Focus의 다섯 입력·실패 범주를 각 task의 테스트에 배정했다. API 키·컨테이너·DB·원격 저장소 생성 접근은 실행 전에 해결할 환경 조건이며 성공한 것으로 전제하지 않았다.

## 구현 시 참고할 공식 문서

- LangGraph persistence: https://docs.langchain.com/oss/python/langgraph/persistence
- LangGraph interrupt/resume: https://docs.langchain.com/oss/python/langgraph/interrupts
- Sentence Transformers models: https://www.sbert.net/docs/sentence_transformer/pretrained_models.html
- Fusion Gallery: https://github.com/AutodeskAILab/Fusion360GalleryDataset

확인한 문서상 interrupt 재개 시 해당 node의 앞부분이 다시 실행될 수 있으므로 node 내 부수효과는 멱등 저장 계약을 따라야 한다. 설치 버전의 실제 API와 다시 대조한 후 구현한다.
