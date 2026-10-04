# 배포와 검증 상태

새 GitHub 저장소: https://github.com/sokldjs554/fitwitness
개발 브랜치: `feat/fitwitness-foundation`
초안 PR: https://github.com/sokldjs554/fitwitness/pull/1

`render.yaml`은 Docker 웹 앱과 PostgreSQL을 연결합니다. 2026-10-04 사용자가 `My workspace`의 무료 배포를 승인했습니다. 실제 생성 요청은 Render API에서 `400: cannot have more than one active free tier database`로 거절되었습니다. 기존 무료 DB를 변경하거나 유료 리소스를 만들지 않았으며, FitWitness의 공개 서비스와 DB는 아직 없습니다.

배포 재개에는 pgvector와 애플리케이션 역할 생성이 가능한 별도 PostgreSQL 연결 또는 승인된 유료 DB 구성이 필요합니다. Render 연결 도구는 Docker 서비스/Blueprint 생성도 지원하지 않아, 현재 Docker 구성을 적용하려면 Dashboard 경로가 필요합니다. 무료 슬롯 제한: https://render.com/docs/free

CI run 37178871171은 두 환경 모두 pytest와 E2E, 영상 캡처를 통과했으나, background `uv run`이 캐시 잠금을 유지하여 `setup-uv` 종료 정리가 실패했습니다. 서비스 실행을 `.venv/bin/uvicorn`으로 변경해 uv 캐시 잠금을 보유하지 않도록 수정했습니다. 후속 CI run 37180224738에서 두 환경 모두 테스트·브라우저·캡처·종료 정리까지 전체 성공했습니다. 검증 링크: https://github.com/sokldjs554/fitwitness/actions/runs/37180224738

외부 Origin은 실제 배포 URL로 `FITWITNESS_ALLOWED_ORIGINS`에 설정합니다. 쿠키 서명키와 metrics token은 Render에서 생성하고 저장소에는 넣지 않습니다. 공개 데모에서 유료 모델을 활성화하기 전에는 전체 호출량 제한과 접근 통제를 검토해야 합니다.

## 완료 판단

- 단위 테스트: 현재 환경에서 재실행.
- 데이터베이스·브라우저: GitHub Actions의 서로 독립된 두 PostgreSQL 실행을 기준으로 확인.
- 실제 데모 화면·영상: CI가 API를 호출해 캡처한 artifact.
- 공개 서비스: URL이 만들어지고 `/ready`와 브라우저 흐름을 확인한 이후에만 완료.
- 라이브 OpenAI·Claude: 실제 키와 모델·단가 설정 후 별도 측정.

API·LLM·배포의 미측정 항목을 테스트 통과로 표시하지 않습니다.
