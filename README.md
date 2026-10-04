# FitWitness

**도면의 생김새보다, 맞는 근거.**

제조 부품의 도면을 검색하고 실제 치수·소재 근거로 검증하며, 개정판이 생기면 기존 판단을 무효화하는 연구 프로젝트입니다. 텔어스 채용 공고의 문제를 바탕으로 만든 독립 포트폴리오입니다.

![실제 PostgreSQL·API에 연결된 데모](docs/media/workspace.png)

검증된 코드 `42851e8`: 독립 DB 환경 2회에서 Python 테스트 66개, 데스크톱·모바일 E2E 4개와 실제 시연 캡처 단계 통과. [CI 실행](https://github.com/sokldjs554/fitwitness/actions/runs/37178871171). 유료 모델 성능 측정이나 공개 호스팅 완료를 뜻하지 않습니다.

## 구현된 기능

- CadQuery 기반 30개 family / 180개 합성 PDF·STEP·PNG·3D mesh
- 도번·BM25 검색, e5·OpenCLIP 및 pgvector 검색 연결 코드
- PDF 실제 위치를 근거로 조건 일치·불일치·확인 필요 판정
- LangGraph + PostgreSQL checkpoint, 실제 worker 종료 후 복구
- 도면 40 → 42mm 개정 시 기존 결과 무효화 및 재검증
- 방문자별 세션·RLS, typed tools, OpenAI·Claude 어댑터, 실행 예산
- React·Three.js 워크스페이스, 근거 위치 표시, 실제 CAD 3D 뷰

현재 개발 브랜치입니다. 기본 체험은 명시적인 규칙 기반 엔진이며 API 호출을 흉내 내지 않습니다. 라이브 LLM, 공개 배포, 최종 평가의 완료를 의미하지 않습니다.

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

설계·구현 계획은 `docs/superpowers/`에 있습니다. 생산 준비 완료 상태는 아닙니다. 일반 스캔 도면 OCR, 최소 의존성 재검증, 실제 산업 데이터 성능, 호스팅 검증은 별도 과제입니다.

## 라이선스

직접 작성한 코드는 Apache-2.0입니다. 의존성·모델·글꼴에는 각 원저작자의 라이선스가 적용됩니다.
