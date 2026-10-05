# FitWitness

**도면의 생김새보다, 맞는 근거.**

제조 부품의 도면을 검색하고 실제 치수·소재 근거로 검증하며, 개정판이 생기면 기존 판단을 무효화하는 연구 프로젝트입니다. 텔어스 채용 공고의 문제를 바탕으로 만든 독립 포트폴리오입니다.

**[공개 데모 열기](https://fitwitness.onrender.com/)** · [검증 기록](docs/verification/hosted-research-20261005.json)

![도면 검토 작업대](docs/media/review-workbench.png)

![실제 LLM 평가와 오류 사례 탐색](docs/media/evaluation-lab.png)

실제 Claude 실측과 실패→수정 기록을 포함합니다. [PDF 72회](https://github.com/sokldjs554/fitwitness/actions/runs/37191423446) · [Agent 최초 실패 포함](https://github.com/sokldjs554/fitwitness/actions/runs/37191644166) · [수정 후 재실행](https://github.com/sokldjs554/fitwitness/actions/runs/37191981503) · [최종 코드 Claude gate](https://github.com/sokldjs554/fitwitness/actions/runs/37321337436).

최종 제출 코드 `fbf3dbae` 검증: 독립 PostgreSQL 두 환경 각각 Python **145개**와 Playwright **58개**를 통과했고, 실제 API 데모 캡처와 Docker package도 성공했습니다. [CI](https://github.com/sokldjs554/fitwitness/actions/runs/37330447524) · [컨테이너](https://github.com/sokldjs554/fitwitness/actions/runs/37330447770).

## 구현된 기능

- CadQuery 기반 30개 family / 180개 합성 PDF·STEP·PNG·3D mesh
- 도번·BM25와 해시 고정 E5·OpenCLIP/pgvector 색인·검색·worker 연결
- PDF 실제 위치를 근거로 조건 일치·불일치·확인 필요 판정
- LangGraph + PostgreSQL checkpoint, 실제 worker 종료 후 복구
- 도면 40 → 42mm 개정 시 기존 결과 무효화 및 재검증
- 방문자별 세션·RLS, typed tools, OpenAI·Claude 어댑터, 실행 예산
- React·Three.js 도면 검토대: 후보 필터, 근거 위치, 실제 CAD 3D, 개정판·실행 기록 검사
- LLM 실험실: Qwen/Claude 모델 선택, 오류 사례·반복 실행별 원시 출력, 실제 Agent 호출 기록, 평가 프로토콜
- 도구로 조회한 사실만 판정에 반영하는 Agent, 별도 탐색/반례 검토 단계, 가용 도구·호출·토큰·비용 제한과 bounded retry
- [재현 가능한 LLM 평가](docs/evaluation/README.md): 고정 모델 revision, 입력·프롬프트·소스 SHA256, 호출별 JSONL

공개 데모는 Render Free + Neon Free로 배포했습니다. 제출 전 UI에서는 3분 체험 가이드, 실험실 빠른 이동, VLM 실패 사례 설명을 추가해 면접관이 핵심 흐름을 바로 확인할 수 있게 정리했습니다. 기본 체험은 명시적인 규칙 기반 엔진이며 API 호출을 흉내 내지 않습니다. 실험실에는 Qwen3-1.7B의 실제 측정 기록을 공개했습니다. 24개 PDF 근거 판정 사례를 3회 반복한 pilot에서 정확도 75%, 잘못된 일치 28.6%를 기록했습니다. 동일 입력의 Claude Haiku 4.5 실측 72회는 72/72 정답, 잘못된 일치 0/42, API 비용 $0.110455를 기록했습니다. 이는 좁은 PDF 근거 판정 과제이며 전체 Agent·검색·VLM 성능과 구분합니다. 전체 Agent 첫6회는3회 실패했고, 관측·종료 처리를 수정한 뒤 세 구조의 진단 재실행이 모두 완료됐습니다. 실험실에서 개선 전/후를 선택할 수 있습니다. VLM 후속까지 누적 API 계산 비용은 $0.238662였습니다. 현재 최종 코드에서 Claude Agent를 다시 실측한 $0.016586를 더해 **누적 $0.255248**이며 미확정 예약은 0입니다. 최종 측정 provider는 Claude로 고정했고 OpenAI는 선택적 어댑터만 유지하며 성능 비교 범위에서는 제외했습니다. 무료 서버는 첫 접속·실행이 느릴 수 있습니다.

복합 검색도 실제 모델과 PostgreSQL로288회 측정했습니다. 합성150개 도면·24개 질문에서 Recall@5는 도번+BM25 25.0%, 복합33.3%였지만 설명+이미지 질문에서는 복합9.7%로 의미 검색12.5%보다 낮았습니다. 원시 실패·한계를 포함한 [검색 실험과 재현 방법](docs/RETRIEVAL.md)을 공개했습니다. 무료 체험의 실시간 검색은 계속 도번·키워드 방식이며 실험실에는 별도 측정 기록을 표시합니다.

[공고 대조·미완료 항목](docs/JOB_FIT_AUDIT.md) · [독립 리뷰와 수정 기록](docs/REVIEW.md) · [배포 상태](docs/DEPLOYMENT.md) · [운영자 모델 실행](docs/OPERATOR.md)

![공개 실험실의 실제 검색 비교 기록](docs/media/retrieval-proof.jpg)

## 후속 연구 결과

신규24개 합성 질문에서 RRF/BGE/치수 조건 정렬216회를 비교했습니다. Recall@5는39.9/54.2/64.2%였고 BGE의 CPU 지연 p50은16.8초였습니다. 이미지 전용 검색은 개선되지 않았습니다. [실측과 한계](docs/RETRIEVAL.md).

Claude 이미지18회는15회 구조화 출력 완료, 오류 포함23/36필드 정답이었습니다. 실제 Agent가 이미지 도구를 호출하되 검증 전 관측으로 조건 일치를 확정하지 않는 것도 확인했습니다. [원시 실패·비용·graph](docs/VISION.md). 실험실에서 재정렬 비교와 이미지 입력·반복별 실제 응답을 확인할 수 있습니다.

[실제 API·복구·개정판·새 평가 화면 데모 영상](docs/media/research-demo.webm)

![실측 후보 재정렬 비교](docs/media/reranking-comparison.png)

![주석 없는 이미지와 실제 모델 관측](docs/media/vision-omission.png)

## 로컬 실행

Python 3.12+, Node.js, PostgreSQL 16 + pgvector가 필요합니다.

```bash
uv sync --extra dev
export PYTHONPATH=src
export FITWITNESS_DATABASE_URL='postgresql://USER:PASSWORD@localhost:5432/fitwitness'
uv run python -m fitwitness.data.generate
uv run python scripts/setup.py
npm ci --prefix web
npm run build --prefix web
uv run uvicorn fitwitness.api.app:create_app --factory --host 0.0.0.0 --port 8787
```

`http://localhost:8787`에서 조건 검증, 원본 근거, 개정판 적용, worker 복구를 체험합니다. DB 초기화 계정에는 role/extension 생성 권한이 필요합니다.

## 검증

```bash
uv run pytest -q
cd web
npx playwright install chromium
PLAYWRIGHT_BASE_URL=http://localhost:8787 npm run test:e2e
```

공개 익명 API에서는 키가 설정되어 있어도 유료 모델 실행을 거부합니다. 운영자 실험은 격리된 CLI 환경에 API 키, 정확한 모델 ID, 현재 단가를 설정해야 합니다. 라이브 실험 미측정을 성공으로 보고하지 않습니다.

설계·구현 계획은 `docs/superpowers/`에 있습니다. 생산 준비 완료 상태는 아닙니다. 일반 스캔 도면 OCR, 최소 의존성 재검증, 실제 산업 데이터 성능은 별도 과제입니다.

## 라이선스

직접 작성한 코드는 Apache-2.0입니다. 의존성·모델·글꼴에는 각 원저작자의 라이선스가 적용됩니다.


## 외부 CAD 형상 실측

NIST 공개 PMI STEP 검증 모델에서 AP203 geometry-only 11개를 질의, 대응 AP242 11개를 후보로 두고 실제 CadQuery 특징 거리를 측정했습니다. GitHub Actions `37313290927`에서 선택 STEP 22/22를 파싱했고 Top-1 11/11, Top-3 11/11, MRR 1.000을 기록했습니다. 이 결과는 외부 engineering benchmark의 **cross-format geometry retrieval** 실측이며 생산 공장의 산업 도면 성능으로 표현하지 않습니다. 세부 프로토콜은 [docs/CAD.md](docs/CAD.md), 게시 JSON은 [docs/evaluation/nist-cad.json](docs/evaluation/nist-cad.json)에 있습니다.

최종 provider gate는 Claude Haiku 4.5로 진행했습니다. 현재 코드 SHA `0347d5af`에서 단일 도구 계획·반복 계획·planner+challenger 세 구조가 모두 완료됐고 각각 5/5 판정을 기록했습니다. 모델/도구 호출은 1/6, 1/6, 2/7회였고 비용은 $0.004102 / $0.004102 / $0.008382였습니다. 원시 결과는 [Actions 37321337436](https://github.com/sokldjs554/fitwitness/actions/runs/37321337436)에 보존했고, 제출용 요약은 [claude-final-gate.json](docs/evaluation/claude-final-gate.json)에 정리했습니다.
