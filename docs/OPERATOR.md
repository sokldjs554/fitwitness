# 유료 모델 운영자 실행

공개 API는 유료 모델 호출을 거부합니다. 아래는 서버 접근 권한과 자기 API 키를 가진 운영자가 명시적으로 실행하는 연구 경로입니다. 라이브 실행은 아직 측정하지 않았습니다.

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

한도는 승인된 **실행 1회당** 1 USD입니다. 동일 workflow의 Re-run jobs는 `run_attempt == 1` 조건으로 차단합니다. 다시 측정하려면 비용 승인을 새로 받고 새 실행 커밋을 만들어야 합니다. 여러 실행의 누적 비용이나 다른 프로젝트의 사용량을 이 예산이 제한하지는 않습니다. API 반복에는 seed를 적용하지 않으므로 프로토콜의 seeds는 null이며, 로컬 모델의 seed 고정과 구분합니다.

사전 제안: Claude Haiku 4.5 고정 ID `claude-haiku-4-5-20251001`, 동일 24개 사례 × 3회. 입력/출력 단가는 2026-10-04 확인한 공식 기준 1/5 USD per million tokens이며, 실행 전 다시 확인합니다. 전체 실험은 보수적인 예약 합계 1 USD 한도로 차단합니다. 이 한도는 이 작업의 API 호출 예산이며 계정 전체 지출 한도는 아닙니다.

CI artifact에는 프로토콜·입력·호출별 출력·실사용 토큰·실패·집계가 저장됩니다. 기록 검토 전에는 공개 데모의 Qwen 측정을 덮어쓰지 않습니다. 이 경로는 PDF 근거 판정 평가이며, 실제 LangGraph 도구 사용 및 검색 평가와 다릅니다. 전체 Agent의 가용 도구·관측 반영·재시도 경로는 별도 보완 후 측정합니다.

참고: [GitHub Secrets](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/use-secrets) · [Claude Haiku 4.5 공식 사양](https://platform.claude.com/docs/en/models/haiku-4-5/overview)
