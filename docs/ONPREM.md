# 바깥 연결 없이 돌리기 (온프레미스·폐쇄망 메모)

제조·보험 고객의 문서는 밖으로 나갈 수 없는 경우가 많습니다. 이 문서는 FitWitness의 어느 부분이 바깥 연결 없이 도는지, 어느 부분이 밖을 부르는지, 그리고 막힌 망에서 켜려면 무엇을 미리 가져와야 하는지를 적습니다. 실제 폐쇄망에 설치해 본 기록은 없습니다. 아래의 "확인했다"는 모두 이 저장소의 테스트와 CI에서 바깥 호출 없이 돈 범위입니다.

## 바깥을 부르지 않는 것

| 구성 요소 | 어떻게 도는가 | 확인한 곳 |
|---|---|---|
| API 서버, worker 풀, 작업 큐, checkpoint | PostgreSQL(pgvector) 하나만 있으면 됨. Render·Neon은 공개 데모의 선택이지 요구가 아님 | 통합 테스트는 로컬 PostgreSQL로 돌고, CI의 게이트는 `services: postgres` 컨테이너로 돎 |
| 도면 검색의 도번·BM25 채널, 규칙 provider의 검증 | 순수 Python | 게이트의 `lexical`, `rules_graph` 항목 |
| 보험 청구의 규칙 경로(추출·규칙·반례·지급 원장) | 순수 Python + pdfplumber | 게이트의 `claims`, `claims_official`, `claims_injection` 항목 |
| 스캔 서류 읽기 | Tesseract(kor+eng)를 로컬에서 실행 | 게이트의 `claims_scan`, `claims_official_scan` 항목(Tesseract가 없으면 건너뜀) |
| 의미·이미지 검색(E5, OpenCLIP, DINOv2) | 해시 고정된 가중치를 `var/models`에서 읽음. `HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1`로 허브 접근을 막은 채 돎 | 검색 실측 workflow들이 그 두 변수를 켜고 돎 |
| 관측 | Prometheus 텍스트 지표, span tree API는 서버 안에서 계산. OTLP는 같은 망의 Tempo·Jaeger로 보냄 | [`deploy/observability`](../deploy/observability)의 compose로 로컬 Jaeger에 보낸 기록이 [OBSERVABILITY.md](OBSERVABILITY.md)에 있음 |
| 웹 작업대 | 빌드 결과물이 정적 파일 | CI의 Playwright 70개가 로컬 서버에서 돎 |

## 바깥을 부르는 것

| 구성 요소 | 호출 대상 | 막힌 망에서 |
|---|---|---|
| Claude·OpenAI provider | api.anthropic.com, api.openai.com | 키가 없으면 `rules` provider만 쓰임. 공개 익명 API는 키가 있어도 유료 모델을 거부함. 사내 게이트웨이를 쓰려면 `langchain_anthropic`의 base URL 설정이 필요하며 이 저장소에는 그 설정이 없음 |
| 인코더 가중치 내려받기 | huggingface.co | `scripts/download_encoders.py`는 연결된 기계에서 한 번 돌리고 `var/models`를 통째로 옮기면 됨. 해시가 `retrieval/models.json`과 맞지 않으면 서버가 거부함 |
| VLM 이미지 읽기 | provider와 같음 | `FITWITNESS_VISION=off`가 기본 |
| 패키지 설치 | PyPI, npm | `uv.lock`·`package-lock.json`으로 고정. 사내 미러가 있으면 그쪽으로 |

## 켜는 순서

1. 연결된 기계에서 `uv sync --frozen --extra models`, `scripts/download_encoders.py`, `cd web && npm ci && npm run build`를 돌리고, `.venv`, `var/models`, `web/dist`를 옮깁니다. 의미·이미지 검색을 쓰지 않으면 `--extra models`와 가중치는 필요 없습니다.
2. PostgreSQL 16 + pgvector를 띄우고 `FITWITNESS_DATABASE_URL`을 줍니다. `scripts/setup.py`가 스키마와 합성 자료를 만듭니다.
3. `.env`는 [`.env.example`](../.env.example)대로 두되 API 키 줄은 비워 둡니다. `FITWITNESS_ENCODERS=pretrained`는 가중치를 옮긴 뒤에만 켭니다. `HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1`을 함께 둡니다.
4. 스캔 서류를 읽으려면 Tesseract와 `kor` 데이터를 설치하고 `FITWITNESS_TESSERACT`로 경로를 줍니다.
5. 추적을 보려면 `deploy/observability`의 compose로 Jaeger를 같은 망에 띄우고 `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT`를 그쪽으로 둡니다.

## 바깥 연결 없이 실제로 돌린 것 (2026-10-27부터 매 커밋)

`container-evaluation` workflow가 빌드한 이미지를 Docker의 `--internal` 네트워크(바깥으로 가는 경로와 바깥 DNS가 없음)에 PostgreSQL(pgvector)과 함께 띄웁니다. 그다음 세 가지를 확인합니다.

- 앱 컨테이너 안에서 pypi.org·api.anthropic.com·api.openai.com·huggingface.co가 모두 풀리지 않고, 1.1.1.1:443은 "Network is unreachable"입니다.
- 같은 망의 다른 컨테이너에서 `scripts/verify_hosted.py --local`이 통과합니다. 체험 공간, 도면 20장의 자산과 도번 검색, 같은 요청의 반복, worker 중단 뒤 재개, 개정판 무효화와 재검증, 세션 격리, origin 거부, 유료 모델 거부, 지표 비공개를 봅니다.
- `scripts/check_claims.py`로 데모 청구 8건을 모두 돌립니다. 4건은 완료, 4건은 담당자 확인 대기로 정리됩니다.

첫 실행은 [40529491099](https://github.com/sokldjs554/fitwitness/actions/runs/40529491099)(`849ad92`)입니다.

## 하지 않은 것

- 이미지는 연결된 기계(GitHub 러너)에서 빌드했고, 끊긴 망에서는 실행만 했습니다. 실제 고객의 폐쇄망 기계에서 돌린 적은 없습니다.
- 이 이미지에는 Tesseract와 의미·이미지 인코더 가중치가 없어 스캔 서류 읽기와 E5·OpenCLIP·DINOv2 검색은 끊긴 망 시험에 들어가지 않았습니다. 위 표의 그 줄들은 여전히 코드와 workflow에서 읽은 것입니다.
- 사내 LLM 게이트웨이나 로컬 모델 서빙(vLLM 등)으로 Claude·OpenAI를 대신하는 어댑터는 없습니다. 판정 프롬프트 비교에서 로컬 Qwen3-1.7B를 쓴 기록은 [evaluation/README](evaluation/README.md)에 있지만, 그것은 평가 스크립트였지 서버의 provider가 아닙니다.
- 폐쇄망의 운영 요건(감사 로그 보존, 계정·권한, 백업)은 이 프로젝트의 범위 밖입니다.
