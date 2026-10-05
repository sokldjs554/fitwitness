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

외부 API provider는 비밀 환경변수의 API 키, 정확한 모델 ID와 현재 단가가 필요합니다. 최종 측정 provider는 Claude Haiku 4.5로 고정했습니다. OpenAI 어댑터 코드는 유지하지만 최종 성능 비교 범위에는 포함하지 않습니다. 익명 웹 요청으로 유료 평가를 시작할 수 없습니다.

비교 UI는 catalog에서 모델을 선택합니다. Qwen 원본 latest.json과 Claude의 claude.json을 분리하고, API provider 표시 및 null seed를 지원합니다. API 반복은 로컬 모델과 달리 seed 고정을 주장하지 않습니다.

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

## Claude Haiku 4.5: 동일 입력 실측

[원시 workflow](https://github.com/sokldjs554/fitwitness/actions/runs/37191423446)의 PDF 단계에서 72회를 완료했습니다. Agent 단계는 초기 rules 요청의 model_id 타입 오류로 API 호출 전에 실패했습니다. 이 workflow 전체를 성공으로 표기하지 않습니다. PDF 측정은 그대로 보존하고 Agent 미사용 예산만 별도 단계에서 이어갑니다.

| 지표 | Qwen3-1.7B CPU | Claude Haiku 4.5 API |
|---|---:|---:|
| 판정 정답 | 54/72 (75%) | 72/72 (100%) |
| 잘못된 일치 | 12/42 (28.6%) | 0/42 (0%) |
| p50 / p95 | 6.18s / 7.12s | 0.658s / 0.924s |
| 입력 / 출력 토큰 | 49,677 / 1,680 | 89,895 / 4,112 |
| API 계산 비용 | $0 (로컬 연산 별도) | $0.110455 |

Claude model ID는 `claude-haiku-4-5-20251001`입니다. 공식 기본 단가 입력 $1/M, 출력 $5/M으로 응답 usage에서 계산했으며 청구서 결산액은 아닙니다. [공식 가격](https://platform.claude.com/docs/en/about-claude/pricing). 캐시 할인·세금·컴퓨트는 포함하지 않습니다.

두 실험의 dataset_hash와 prompt_hash는 동일합니다. 그러나 로컬 completion과 API structured output의 생성 계약·토크나이저·실행 환경은 다릅니다. 이 표는 작은 과제의 관측 결과이지 모델의 일반적인 우열이나 처리속도 순위가 아닙니다. 4개 family bootstrap의 Claude 구간 [100%,100%]도 모집단 정확도 100%의 증거가 아닙니다.

[Claude 전체 보고서](claude.json) · [동결 원본](claude-37191423446/report.json) · [JSONL](claude-37191423446/predictions.jsonl) · [protocol](claude-37191423446/protocol.json). 공개 화면에는 측정 기록을 표시하며 방문자의 API 호출은 발생하지 않습니다.

## 실제 Agent 첫 실행 — 실패 포함

[Agent workflow 37191644166](https://github.com/sokldjs554/fitwitness/actions/runs/37191644166)는 5개 도면에 대해 한 질문을 세 구조로 두 번씩 실행했습니다. 규칙 기준선은 5/5, 단일 도구 계획은 2/2 실행 완료, 반복 계획은 1/2, 탐색+반례 검토는 0/2였습니다. 세 실패는 출력 스키마 오류였고, 오류 발생 전 없는 소재를 다시 조회하는 경로가 관측되었습니다. 최초 측정에서 raw 오류 응답을 남기지 않아 구체적인 schema 위반 필드는 확인할 수 없습니다.

[첫 Agent 원본](agent-37191644166/agent/report.json)과 JSONL은 수정하지 않습니다. 첫 결과의 `request_id`는 LangChain 실행 ID이며 provider 응답 ID로 해석하면 안 됩니다. 후속 어댑터는 `provider_response_id`와 `langchain_run_id`를 분리하고 실패 응답·usage도 trace에 기록합니다.

개선은 조회한 필드와 원문에 없는 필드를 구분하고, 동일 인자의 조회를 반복하지 않으며, 필요한 모든 필드를 조회했다면 남은 누락을 unknown으로 확정하는 것입니다. 수정 후 동일 사례를 재실행하는 것은 오류 수정 검증이며, 독립 held-out 품질 평가가 아닙니다.

### 수정 후 진단 재실행

[workflow 37191981503](https://github.com/sokldjs554/fitwitness/actions/runs/37191981503), 코드 `85a4af3`: 동일 질문으로 각 구조를 한 번씩 다시 실행했습니다. 3/3 실행이 완료되었고 각 실행의 도면 판정은 5/5였습니다. 원래 실패를 분모에서 삭제하거나 v1 점수를 수정하지 않습니다.

| 구조 | 최종 판정 | 모델 / 도구 호출 | 전체 지연 | API 계산 비용 |
|---|---:|---:|---:|---:|
| 단일 도구 계획 | 5/5 | 1 / 6 | 4.793s | $0.004165 |
| 반복 계획 | 5/5 | 1 / 6 | 3.077s | $0.004205 |
| 탐색 + 별도 반례 검토 | 5/5 | 2 / 7 | 5.040s | $0.008453 |

반복 계획은 모든 요구 필드를 첫 단계에서 조회해 추가 모델 호출 없이 종료했습니다. 반례 구조가 이 사례에서 정확도를 높인 것은 아니며 모델 호출과 비용이 더 들었습니다. 단일 family·단일 질문·수정 후 각1회 결과로 구조의 일반 우위를 주장하지 않습니다.

[수정 후 보고서](agent.json) · [원시 실행](agent-37191981503/agent/runs.jsonl) · [이전 실패 포함 보고서](agent-v1.json). 모든 도구의 실제 인자, 역할, 근거 ID, 지연, provider 응답 ID, 토큰과 비용을 조회할 수 있습니다. v2의 `provider_response_id`는 실제 `msg_...`이며 별도로 LangChain 실행 ID를 보관합니다.

VLM 실험 이전 사용량 기준 계산 비용: PDF $0.110455 + 최초 Agent $0.060563 + 진단 재실행 $0.016823 = **$0.187841**. 남은 불명확한 예약은 0입니다. 최초1 USD 한도 안이며 후속 유료 실행을 자동 예약하지 않습니다.

게시 정정: v2 원본의 고정 안내 문구에 남은 “두 번”은 v1 설명입니다. 실제 v2는 protocol.runs 및 원시 기록대로 구조별1회입니다. 공개 agent.json의 안내 문구만 정정했고 원본·측정값은 보존했으며 publication_note에 원본 해시를 기록했습니다.

## 실제 의미·이미지 검색 평가

[검색 평가·재현 문서](../RETRIEVAL.md)에 v2의288회 실제 PostgreSQL 검색 결과를 공개했습니다. PDF 판정·LLM Agent 점수와 다른 과제입니다. 수정 전 정답 기준의 [v1원본](retrieval-v1-superseded/README.md)도 보존하며 성과 지표에서는 제외합니다.

## 후속 재정렬·VLM

[재정렬216회](../RETRIEVAL.md): RRF/BGE/조건 정렬 Recall@5 39.9/54.2/64.2%. [VLM18회+실제graph](../VISION.md):15회 구조화 출력 완료, 오류 포함23/36필드 정답, 이미지 근거 단독 판정은 unknown. 원시 실패도 보존했습니다. VLM까지 누적 계산 비용은 **$0.238662**, 미확정 예약0이었습니다. 아래 최종 Claude provider gate $0.016586를 포함하면 누적 **$0.255248**입니다. 공개 실험실은 저장된 기록을 읽으며 방문자 요청으로 새 API 호출을 하지 않습니다.


## NIST 외부 CAD

- Actions run: `37313290927`
- code: `e084c28ba2f1aceb4bee17a56705a207dc0576ca`
- source ZIP SHA-256: `1fb91bb8ff0fe02032b948fda0775bc74591cd0bebc0988347d32574e5884f90`
- 22/22 STEP parse, AP203→AP242 Top-1 11/11, Top-3 11/11, MRR 1.0
- raw `runs.jsonl` SHA-256: `9041974fa5b2f6343349553dce446a62b26c2fb57e58532304725ff312b740f1`
- 게시 요약: `nist-cad.json`; 전체 parse/protocol/report/raw는 run artifact `nist-cad-37313290927`.

생산 산업 도면 성능이 아니라 NIST의 외부 engineering benchmark에서 서로 다른 STEP 표현의 geometry retrieval을 측정한 결과입니다.

## 최종 Claude provider gate — 2026-10-05

최종 앱 코드와 같은 트리에서 marker SHA `0347d5af08b81a9f5182dd18144e314a339007f9`를 만들고 [Actions 37321337436](https://github.com/sokldjs554/fitwitness/actions/runs/37321337436)에서 Claude Haiku 4.5를 실제 호출했습니다. 전용 workflow는 구조별 최대 $0.066만 예약했고 자동 재실행을 금지했습니다.

| 구조 | 최종 판정 | 모델 / 도구 호출 | 전체 지연 | 입력 / 출력 토큰 | API 계산 비용 |
|---|---:|---:|---:|---:|---:|
| 단일 도구 계획 | 5/5 | 1 / 6 | 5.658s | 2,012 / 418 | $0.004102 |
| 반복 계획 | 5/5 | 1 / 6 | 3.624s | 2,012 / 418 | $0.004102 |
| 탐색 + 별도 반례 검토 | 5/5 | 2 / 7 | 5.674s | 5,452 / 586 | $0.008382 |

이번 추가 실측 비용은 **$0.016586**, 미확정 예약은 0입니다. artifact `11350975678`의 ZIP digest는 `deeb2014e3a59cd172f01d9c8cef09f66bc1214d7a41d54fadc9c51f74e05e0a`입니다. raw report SHA-256은 `b378a1930f89d9296a42d74b641f4b43978a87ce18cefbea2ba131c45e3f3859`, JSONL SHA-256은 `5eec450116a2f792166b766d334647eab0ed9c4993b5a07697bbb83b68d77645`입니다.

같은 합성 family의 단일 질문을 구조별1회 다시 실행한 연결 검증이므로 일반적인 Agent 성공률이나 구조 우위를 뜻하지 않습니다. OpenAI는 최종 측정 provider에서 제외했고, 필요할 때 사용할 수 있는 어댑터 코드만 유지합니다.
