# FitWitness

**도면의 생김새보다, 맞는 근거.**

제조 부품의 도면을 검색하고 실제 치수·소재 근거로 검증하며, 개정판이 생기면 기존 판단을 무효화하는 연구 프로젝트입니다. 텔어스 채용 공고의 문제를 바탕으로 만든 독립 포트폴리오입니다.

**[공개 데모 열기](https://fitwitness.onrender.com/)** · [검증 기록](docs/verification/hosted-20261004.json)

![Neon PostgreSQL에 연결된 공개 데모에서 개정판 재검증](docs/media/hosted-revision.jpg)

검증된 코드 `c1508f2`: 독립 PostgreSQL 환경 2회에서 각각 Python 테스트 67개, 데스크톱·모바일 E2E 4개, 실제 시연 캡처까지 전체 통과. [CI 실행](https://github.com/sokldjs554/fitwitness/actions/runs/37183531736). 유료 모델 성능 측정은 별도입니다.

## 구현된 기능

- CadQuery 기반 30개 family / 180개 합성 PDF·STEP·PNG·3D mesh
- 도번·BM25 검색, e5·OpenCLIP 및 pgvector 검색 연결 코드
- PDF 실제 위치를 근거로 조건 일치·불일치·확인 필요 판정
- LangGraph + PostgreSQL checkpoint, 실제 worker 종료 후 복구
- 도면 40 → 42mm 개정 시 기존 결과 무효화 및 재검증
- 방문자별 세션·RLS, typed tools, OpenAI·Claude 어댑터, 실행 예산
- React·Three.js 도면 검토대: 후보 필터, 근거 위치, 실제 CAD 3D, 개정판·실행 기록 검사
- LLM 실험실: 검증기와 모델 비교, 오류 사례·반복 실행별 원시 출력, 평가 프로토콜
- [재현 가능한 LLM 평가](docs/evaluation/README.md): 고정 모델 revision, 입력·프롬프트·소스 SHA256, 호출별 JSONL

공개 데모는 Render Free + Neon Free로 배포했습니다. 기본 체험은 명시적인 규칙 기반 엔진이며 API 호출을 흉내 내지 않습니다. 실험실에는 Qwen3-1.7B의 실제 측정 기록을 공개했습니다. 24개 PDF 근거 판정 사례를 3회 반복한 pilot에서 정확도 75%, 잘못된 일치 28.6%를 기록했습니다. 전체 Agent·검색·VLM 평가와 OpenAI·Claude 측정은 별도 과제입니다. 무료 서버는 첫 접속·실행이 느릴 수 있습니다.

[독립 리뷰와 수정 기록](docs/REVIEW.md) · [배포 상태](docs/DEPLOYMENT.md) · [운영자 모델 실행](docs/OPERATOR.md)

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
