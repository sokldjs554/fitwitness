# PDF 근거 판정 평가

이 실험은 고정된 PDF 근거로 조건 충족 여부를 판단하는 소규모 pilot입니다. 전체 Agent, 검색, OCR, VLM 평가와 구분합니다. 공개 데모에서는 모델을 실행하지 않고 실제로 측정한 결과를 읽습니다.

## 실험 계약

- 모델: `Qwen/Qwen3-1.7B`, revision `70d244cc86ccca08cf5af4e1e306ecf908b1ad5e`, Apache-2.0.
- 입력: 실제 합성 PDF에서 `extract_pdf`로 추출한 치수·재질, 요구 조건. 정답은 모델에 전달하지 않습니다.
- 표본: 기존 test split 중 사전 지정한 4개 family, 각 6개 유형, 총 24개. 동일 사례를 seed 1701–1703으로 3회 반복합니다.
- 유형: 조건 일치, 치수 불일치, 소재 불일치, 소재 누락, cm/mm 변환, 개정판. 개정으로 치수가 바뀌지 않은 family도 포함합니다.
- 비교: 운영 코드의 결정적 검증기와 동일 근거를 받는 LLM. 모델이 JSON 형식이나 인용 검증에 실패하면 오류로 기록하며 정확도 분모에서 빼지 않습니다.
- 지표: 판정 정확도, 비일치 정답 중 잘못된 일치 판정 비율, 보류율, 유효 인용 비율, 실제 토큰, 지연 p50/p95. 인용 유효성은 ID 존재 확인이며 의미적 지지도를 뜻하지 않습니다.
- 신뢰구간: family 단위 1,000회 bootstrap. 4개 family의 좁은 pilot이므로 일반화 근거로 쓰지 않습니다.
- 실행: CPU bfloat16, thinking 비활성화, temperature 0.7, top-p 0.8, top-k 20, 출력 최대 128토큰. 지연에는 생성·토큰화가 포함되고 모델 최초 로드는 포함되지 않습니다.

## 재현

```bash
uv sync --extra dev --extra models
uv run python - <<'PYMODEL'
from huggingface_hub import snapshot_download
from pathlib import Path
revision = '70d244cc86ccca08cf5af4e1e306ecf908b1ad5e'
folder = 'var/models/Qwen3-1.7B'
snapshot_download('Qwen/Qwen3-1.7B', revision=revision, local_dir=folder)
Path(folder, 'REVISION').write_text(revision)
PYMODEL
# 처음 실행한다면 합성 PDF 생성
PYTHONPATH=src uv run python -m fitwitness.data.generate
PYTHONPATH=src uv run python -m fitwitness.evaluation.runner \
  --provider local --model-path var/models/Qwen3-1.7B \
  --output artifacts/my-evaluation --repeats 3
```

프로토콜·입력·프롬프트는 추론 전에 저장됩니다. JSONL은 호출마다 추가되어 실패나 중단도 추적할 수 있습니다. 기존 출력 디렉터리에 덮어쓰지 않습니다. `report.json`을 검토한 후 `docs/evaluation/latest.json`으로 게시하면 공개 비교 화면에 반영됩니다.

OpenAI 또는 Claude는 비밀 환경변수의 API 키, 정확한 모델 ID와 현재 단가가 필요합니다. `--provider openai` 또는 `--provider anthropic`, `--model`, `--max-cost-usd`를 명시해야 합니다. 현재 두 서비스는 미측정입니다. 익명 웹 요청으로 유료 평가를 시작할 수 없습니다.

## 해석 범위

API 비용 0 USD는 로컬 모델의 API 청구가 없다는 뜻이며 컴퓨트·전력 비용 0을 뜻하지 않습니다. 이 결과로 frontier 모델, 전체 Agent 구조, 실제 제조 적합성을 평가했다고 주장하지 않습니다. 프롬프트는 이 test 결과에 맞춰 수정하지 않습니다. 후속 개선은 별도 dev split에서 선택하고 새 실험으로 기록해야 합니다.

## UI 참고

- [Braintrust 실험 비교](https://www.braintrust.dev/docs/evaluate/compare-experiments): 동일 입력별 비교, 회귀 사례부터 검사, 실행 반복별 차이 확인.
- [LangSmith Studio](https://docs.langchain.com/langsmith/studio): 실행 단계와 상태를 연결해 탐색.
- [Langfuse Datasets](https://langfuse.com/docs/evaluation/experiments/datasets): 데이터셋과 기대 출력의 버전 관리.

화면 자산이나 소스는 복제하지 않고, 비교·검사 상호작용만 FitWitness의 도면 검토 흐름에 맞춰 구현합니다.

## 2026-10-04 실측 결과

24개 사례 × 3회, 모델 추론 72회를 완료했습니다. 결정적 검증기는 72/72, Qwen3-1.7B는 54/72(75%)의 판정 정확도를 기록했습니다. 모델의 family-bootstrap 95% 구간은 66.7–83.3%입니다.

- 잘못된 일치 판정: 12/42 비일치 정답(28.6%). 소재 누락 12회 모두 잘못된 일치로 확정했습니다.
- 단위 변환: 12회 중 6회 정답. 치수·소재 불일치 및 개정판 사례는 모두 정답이었습니다.
- 모델 지연 p50 6.18초, p95 7.12초. 입력 49,677 / 출력 1,680 토큰.
- API 청구 0 USD. OpenAI·Claude는 미측정입니다.

[전체 결과](latest.json) · [호출별 원시 JSONL](run-20261004/predictions.jsonl) · [동결 프로토콜](run-20261004/protocol.json) · [입력/정답](run-20261004/cases.json)

실행은 코드 커밋 전 작업 트리에서 수행했습니다. 프로토콜의 code_sha는 당시 base commit이며, 실제 평가 소스는 [source](run-20261004/source)와 [SHA256 명세](run-20261004/source-manifest.json)에 동결했습니다. 재현할 때 이 스냅샷을 사용합니다. 단순한 비교 과제의 검증기 100%는 실제 산업 도면 전반의 정확도 100%를 뜻하지 않습니다.

평가 후 코드 리뷰에서 향후 오류 호출의 인용·사용량 보존을 강화했습니다. 게시된 측정과 동결 소스는 변경하지 않았으며, 현재 runner에는 두 실패 경로의 회귀 테스트가 추가되어 있습니다.
