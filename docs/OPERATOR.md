# 유료 모델 운영자 실행

공개 API는 유료 모델 호출을 거부합니다. 아래는 서버 접근 권한과 자기 API 키를 가진 운영자가 명시적으로 실행하는 연구 경로입니다. Claude 실제 실행 기록은 `docs/evaluation/README.md`에 있습니다. OpenAI 실제 호출은 아직 측정하지 않았습니다.

1. `scripts/setup.py`로 초기화합니다.
2. `.env.example`의 API 키·정확한 모델 ID·현재 USD/million token 단가를 비밀 환경변수로 설정합니다. `.env`는 저장소에 올리지 않습니다.
3. 다음 예제는 최대 비용 예약 0.25 USD, 토큰 한도 64,000으로 한 번 실행합니다. 기본 16,000토큰 예산은 전체 PDF 사실·도구 스키마를 포함한 요청을 호출 전에 차단할 수 있어 연구 실행에서는 한도를 명시합니다. 이는 성공을 보장하는 설정이 아니며 입력이나 누적 호출이 예산을 넘으면 요청 전에 실패합니다.

```python
import os
from uuid import uuid4
from fitwitness.contracts import TenantScope, RunRequest, SearchRequest, Budget
from fitwitness.storage.repository import Repository
from fitwitness.runtime.jobs import Jobs
from fitwitness.data.bootstrap import seed
from fitwitness.agents.graph import execute_run

repo = Repository(os.environ['FITWITNESS_DATABASE_URL'])
scope = TenantScope(tenant_id=str(uuid4()), user_id='operator')
seed(repo, scope)
request = RunRequest(
    provider='openai',  # anthropic도 지원하는 어댑터가 있습니다.
    model_id=os.environ['FITWITNESS_OPENAI_MODEL'],
    search=SearchRequest(text='브래킷 구멍 간격 40mm 소재 SUS304'),
    budget=Budget(max_cost_usd='0.25', max_tokens=64000),
)
job = Jobs(repo).enqueue(scope, request, str(uuid4()))
execute_run(repo, scope, job.id)
print(Jobs(repo).get(scope, job.id).model_dump_json(indent=2))
```

응답을 받지 못한 호출은 `reserved_cost_usd`와 `reserved_tokens`에 보수적 예약이 남습니다. 이는 실제 청구액을 확정한 값이 아닙니다. `cost_usd`는 확인된 사용량과 운영자가 입력한 단가를 곱한 값입니다. 외부 API 청구의 exactly-once나 실제 청구서 일치를 보장하지 않습니다.

## Claude 키를 공개하지 않고 측정하기

키는 채팅·이슈·소스·커밋에 넣지 않습니다. 이 저장소의 Settings → Secrets and variables → Actions → New repository secret에서 `ANTHROPIC_API_KEY`라는 이름으로 저장합니다. 키를 저장하는 것만으로는 API가 호출되지 않습니다.

`.github/workflows/claude-evaluation.yml`은 `feat/fitwitness-foundation`에 아래 문구로 시작하는 커밋이 들어올 때만 유료 실행을 시작합니다. 이 전용 실행 커밋은 사용자의 비용 승인을 받은 뒤 생성합니다. 승인 없는 일반 수정에는 이 문구를 사용하지 않습니다.

`eval: claude evidence pilot [max-usd=1]`

이 프로젝트 승인 한도는 후속 진단을 포함한 **누적1 USD**입니다. 각 workflow의 한도 외에도 확인된 비용과 미확정 예약을 합산합니다. 동일 workflow 재실행은 run_attempt==1로 차단합니다. 현재 누적은$0.238662이며 자동 추가 실행은 없습니다. API 반복에는 seed를 적용하지 않으므로 프로토콜의 seeds는 null이며, 로컬 모델의 seed 고정과 구분합니다.

사전 제안: Claude Haiku 4.5 고정 ID `claude-haiku-4-5-20251001`, 동일 24개 사례 × 3회. 입력/출력 단가는 2026-10-04 확인한 공식 기준 1/5 USD per million tokens이며, 실행 전 다시 확인합니다. 전체 실험은 보수적인 예약 합계 1 USD 한도로 차단합니다. 이 한도는 이 작업의 API 호출 예산이며 계정 전체 지출 한도는 아닙니다.

CI artifact에는 프로토콜·입력·호출별 출력·실사용 토큰·실패·집계가 저장됩니다. 기록 검토 전에는 공개 데모의 Qwen 측정을 덮어쓰지 않습니다. 이 경로는 PDF 근거 판정 평가이며, 실제 LangGraph 도구 사용 및 검색 평가와 다릅니다. 이후 전체 Agent의 가용 도구·관측 반영·재시도 경로를 보완하고 별도 실측했습니다. 아래는 당시 준비 절차를 포함하며 최신 결과는 평가 기록을 참조합니다.

참고: [GitHub Secrets](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/use-secrets) · [Claude Haiku 4.5 공식 사양](https://platform.claude.com/docs/en/models/haiku-4-5/overview)

### 등록된 Claude 키로 한 번의 검증 실행

2026-10-04 승인된 실행 한도는 합계 1 USD입니다. 전용 workflow는 동일 PDF 72회에 0.60 USD, 실제 PostgreSQL LangGraph 6개 실행에 각각 0.066 USD(합계 0.396 USD)를 따로 예약합니다. SDK 자동 재시도는 끄며, Agent transient retry도 동일 실행 한도에 포함합니다. 실패한 호출의 불명확한 비용은 예약 장부에 남습니다. 공개 서버에는 키를 전달하지 않습니다.

Agent pilot은 FW-F000 합성 도면 5개의 단일 질의를 fixed/ReAct/별도 planner+challenger에 두 번씩 실행합니다. gold는 평가기만 읽으며 실제 판정·도구 인자·request ID·토큰·지연·오류·최종 근거를 원시 기록으로 보관합니다. 작은 연결 검증이며 일반 성능 우위를 뜻하지 않습니다. `stop`은 명시적 종료 플래그, `stop_condition`은 종료 설명입니다. 누락 근거는 unknown으로 남습니다. 429·일부 5xx·연결/시간 오류만 최대 두 번 시도하고, schema/권한 오류는 재시도하지 않습니다. 프로세스 내 시도가 모두 실패하면 실행은 `retry_wait`로 백오프한 뒤 checkpoint에서 다시 이어지고, 3회 소진 시 보류함으로 갑니다([WORKFLOW.md](WORKFLOW.md)).

## 유료 API 없는 의미·이미지 검색

E5/OpenCLIP 모델 준비, 원본 색인, worker 연결과 검색 비교 절차는 [검색 실행 문서](RETRIEVAL.md)에 있습니다. 공개 무료 서버에서는 모델을 켜지 않습니다.

이미지 도구는 `FITWITNESS_VISION=enabled`인 operator만 사용합니다. [실측·관측 경계](VISION.md)를 참조하세요.

## 담당자 확인이 필요한 실행

`RunRequest(review="on_unknown")`으로 실행하면 근거가 부족한 도면이 남을 때 `waiting_input`에서 멈춥니다. `Jobs.resume(scope, run_id, {"decisions": {...}, "reviewer": "...", "note": "..."})` 또는 `POST /api/runs/{id}/resume`으로 답하면 저장된 지점부터 이어집니다. 담당자 판정은 `verifier_version="human-v1"` 근거로 결정에 남습니다.
