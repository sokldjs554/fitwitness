# 유료 모델 운영자 실행

공개 API는 유료 모델 호출을 거부합니다. 아래는 서버 접근 권한과 자기 API 키를 가진 운영자가 명시적으로 실행하는 연구 경로입니다. 라이브 실행은 아직 측정하지 않았습니다.

1. `scripts/setup.py`로 초기화합니다.
2. `.env.example`의 API 키·정확한 모델 ID·현재 USD/million token 단가를 비밀 환경변수로 설정합니다. `.env`는 저장소에 올리지 않습니다.
3. 다음 예제는 최대 비용 예약 0.25 USD, 토큰 한도 16,000으로 한 번 실행합니다. 입력의 보수적인 토큰 상한이 예산을 넘으면 요청 전에 실패합니다.

```python
import os
from uuid import uuid4
from fitwitness.contracts import TenantScope, RunRequest, SearchRequest
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
)
job = Jobs(repo).enqueue(scope, request, str(uuid4()))
execute_run(repo, scope, job.id)
print(Jobs(repo).get(scope, job.id).model_dump_json(indent=2))
```

응답을 받지 못한 호출은 `reserved_cost_usd`와 `reserved_tokens`에 보수적 예약이 남습니다. 이는 실제 청구액을 확정한 값이 아닙니다. `cost_usd`는 확인된 사용량과 운영자가 입력한 단가를 곱한 값입니다. 외부 API 청구의 exactly-once나 실제 청구서 일치를 보장하지 않습니다.
