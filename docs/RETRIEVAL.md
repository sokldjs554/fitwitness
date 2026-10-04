# 도면 검색 실험과 실행 경로

기존 도번·BM25 검색에 실제 E5 의미 검색과 OpenCLIP 이미지 검색을 연결했습니다. 공개 무료 체험은 기존 규칙·도번·키워드 엔진이며, 실험실의 복합 검색 결과는 별도 PostgreSQL 작업 환경에서 측정한 기록입니다. 공개 서버에 모델 가중치를 포함하거나 방문자의 요청으로 유료 API를 실행하지 않습니다.

## 구현 범위

- `retrieval/indexing.py`는 원본에서 추출된 사실·제목·도번과 PNG만 읽습니다. 정답 디렉터리를 읽지 않습니다.
- 두 임베딩을 모두 생성한 뒤 한 transaction에서 저장합니다. 인코딩 실패·근거 변경·테넌트 불일치 때 일부 색인을 게시하지 않습니다.
- 모델 revision·파일 SHA-256을 `retrieval/models.json`에 고정했습니다. worker는 hash 검사 후 로컬 모델만 로드합니다. 임의 벡터를 실제 모델 결과로 대체하지 않습니다.
- 검색은 현재 tenant와 고정 snapshot의 승인된 개정만 대상으로 합니다. 입력 전후 snapshot을 확인하고, 사실 변경 시 벡터를 지웁니다. 모델 해시가 다르거나 일부 색인이 없으면 명시적으로 실패합니다.
- worker는 `FITWITNESS_ENCODERS=pretrained`일 때만 모델을 로드합니다. 같은 LangGraph 경로와 typed 검색 도구를 사용합니다. 일반 공개 환경의 기본값은 `off`입니다.
- 이 색인은 작은 코퍼스의 **정확한 cosine 검색**입니다. HNSW/IVFFlat ANN이나 대규모 검색 부하 검증을 했다는 뜻은 아닙니다.

## 작업 환경에서 재현

PostgreSQL+pgvector, 프로젝트 Python 의존성, 기존 tenant의 자료가 필요합니다. 텍스트·이미지 모델을 함께 메모리에 올리므로 무료 웹 인스턴스에서 켜지 않습니다.

```bash
uv sync --frozen --extra dev --extra models
export PYTHONPATH=src
# FITWITNESS_DATABASE_URL은 본인의 작업용 PostgreSQL 연결을 환경 변수로 설정
uv run python scripts/setup.py
uv run python scripts/download_encoders.py
uv run python -m fitwitness.retrieval.indexing --tenant YOUR_EXISTING_TENANT
FITWITNESS_ENCODERS=pretrained uv run python -m fitwitness.runtime.worker \
  --tenant YOUR_EXISTING_TENANT --run YOUR_QUEUED_RUN_ID
```

평가는 빈 결과 디렉터리와 별도 tenant를 생성하며 기존 결과에 덮어쓰지 않습니다. 유료 LLM 키가 필요하지 않습니다.

```bash
uv run python -m fitwitness.evaluation.retrieval --output artifacts/retrieval-new
```

## 평가 설계

150개 활성 합성 도면, 30개 family 중 고정 test6개 family의 24개 질문을 사용합니다. 도번·설명·이미지·설명+이미지 각각6개입니다. 같은 질문에서 도번+BM25, E5, OpenCLIP, 네 채널 RRF를 비교하고3회 반복합니다. 총288번 검색이며 독립288개 질문은 아닙니다. 모델·RRF 상수60·top10을 test 결과에 맞춰 선택하지 않았습니다.

텍스트 질문은 종류와 폭만 정답 기준으로 사용합니다. 이미지·복합 질문은 보이는 구멍 간격까지 포함합니다. 재질이나 조립 적합성 정답과 구분합니다. 도면에서 잘라 크기·회전을 바꾼 이미지이므로 독립 촬영·외부 자료 평가로 해석하지 않습니다. PDF·원본 PNG·변형 질문 PNG의 해시와 변형 방식이 protocol에 남습니다.

입력 모달리티가 없는 방식은 빈 결과를 반환합니다. 전체 평균만 보면 이미지 질문에서 키워드 방식이 불리하므로 질문 유형별 지표를 같이 표시합니다. 검색 지연은 모델 로드·색인 시간을 제외한 query encode+DB+fusion 전체 시간입니다. 모델 로딩과 색인 시간은 별도 필드에 기록합니다. 세 반복의 순위를 유지하고, 화면에는 반복1의 후보와 채널별 점수를 표시합니다.

## 검토에서 발견한 실패

첫 실행은 모델 목록에 다운로드 임시 파일이 들어가404가 발생해 추론 전 종료됐습니다. 공식 파일명만 고정하고 회귀 검증했습니다. 두 번째 실행 중, 점수를 보기 전에 설명 질문의 정답 기준에 질문에 없는 구멍 간격이 포함된 문제를 확인했습니다. 실패 재현 후 v2에서 유형별 정답 기준으로 수정했습니다. 이전 protocol은 유효한 비교 성과로 사용하지 않습니다.

이 작업은 VLM/OCR, 3D 파생 거리, reranker, 외부 산업 자료, 대규모 색인, 모델 의도 파서의 일반화까지 완료한 것이 아닙니다.

## 실제 측정 — v2

[원시 보고서](evaluation/retrieval-37198685994/report.json) · [동결 protocol](evaluation/retrieval-37198685994/protocol.json) · [288개 원시 실행 JSONL.gz](evaluation/retrieval-37198685994/runs.jsonl.gz) · [실제 graph 기록](evaluation/retrieval-37198685994/graph.json)

GitHub Actions37198685994, 코드5fbc5feec8f58827aed5028a2d8d8c2be0c8b093에서288/288 검색과 실제 rules LangGraph 실행이 완료됐습니다. 모델 로드21.25초,150개 도면 색인44.91초였고 추가 LLM API 호출 비용은 없습니다. 원시 JSONL에서 개별/집계 지표를 다시 계산해 보고서와 대조했습니다.

| 방식 | 전체 Recall@5 | 전체 nDCG@10 | p50 / p95 |
|---|---:|---:|---:|
| 도번+BM25 | 25.0% | 0.250 | 149 /172 ms |
| E5 의미 | 22.9% | 0.209 | 201 /253 ms |
| OpenCLIP 이미지 | 5.6% | 0.088 | 331 /599 ms |
| 복합 RRF | 33.3% | 0.368 | 334 /530 ms |

| 질문 유형 | 도번+BM25 Recall@5 | E5 | OpenCLIP | 복합 RRF |
|---|---:|---:|---:|---:|
| 도번 | 100% | 66.7% | 입력 없음0% | 100% |
| 설명 | 0% | 12.5% | 입력 없음0% | 12.5% |
| 이미지 | 입력 없음0% | 입력 없음0% | 11.1% | 11.1% |
| 설명+이미지 | 0% | 12.5% | 11.1% | 9.7% |

**해석:** 네 채널의 연결·실행은 검증됐지만 검색 품질은 낮습니다. 복합 방식의 전체 평균 증가는 모달리티를 추가한 효과를 포함하며, 복합 질문에서는 오히려 각 dense 방식보다 낮았습니다. 범용 E5/OpenCLIP과 동일 가중치 RRF가 기술 도면의 세밀한 치수 차이에 충분하다는 근거는 없습니다. 과거 Claude PDF72/72와 이 검색 지표는 서로 다른 과제입니다. 이 결과를 산업 검색 정확도나 일반적 모델 우위라고 소개하지 않습니다.

다음 연구 단계는 별도 dev 자료에서 치수 조건을 사용한 후보 재정렬·3D 특징을 비교하고, 이후 새로운 독립 test자료로 검증하는 것입니다. 이번 test점수에 맞춰 가중치나 정답 범위를 바꾸지 않았습니다.

## 원본 자료 묶음

[실험과 바이트가 일치하는 PDF·PNG·manifest·gold](evaluation/retrieval-37198685994/sources.zip.xz)를 보존했습니다. `xz -d sources.zip.xz` 후 ZIP을 풀면 됩니다. 로컬에 예전부터 있던 PNG는 실험 PNG와 바이트가 달랐으므로 이를 실험 원본이라고 부르지 않았습니다. 동일한 CI 환경에서 원본을 다시 만들고, 동결 protocol의 PDF150개·PNG150개 해시가 모두 일치하는 것을 확인한 자료입니다. 화면에 표시되는 변형 질문 이미지12개도 동결 해시와 일치합니다.

자료 재생성 workflow37199579387, 보존 archive SHA-256 `7481215aa5a6844588d3fb7e93970258349a1af54d8be40c0011bb79547eebb3`. 이 작업에서 모델 추론·점수 계산을 다시 수행하지 않았습니다.
