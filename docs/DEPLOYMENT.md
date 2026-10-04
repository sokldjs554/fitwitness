# 배포와 검증 상태

새 GitHub 저장소: https://github.com/sokldjs554/fitwitness
개발 브랜치: `feat/fitwitness-foundation`
초안 PR: https://github.com/sokldjs554/fitwitness/pull/1

`render.yaml`은 웹 앱과 PostgreSQL을 연결합니다. 실제 리소스는 아직 만들지 않았습니다. Render 연결 도구가 작업 공간의 명시적 선택을 요구하므로 `My workspace` 사용 확인이 필요합니다. 무료 구성으로 작성했지만 사용 가능한 플랜과 실제 메모리 한도는 배포 시 확인합니다. 자동 유료 업그레이드는 하지 않습니다.

외부 Origin은 실제 배포 URL로 `FITWITNESS_ALLOWED_ORIGINS`에 설정합니다. 쿠키 서명키와 metrics token은 Render에서 생성하고 저장소에는 넣지 않습니다. 공개 데모에서 유료 모델을 활성화하기 전에는 전체 호출량 제한과 접근 통제를 검토해야 합니다.

## 완료 판단

- 단위 테스트: 현재 환경에서 재실행.
- 데이터베이스·브라우저: GitHub Actions의 서로 독립된 두 PostgreSQL 실행을 기준으로 확인.
- 실제 데모 화면·영상: CI가 API를 호출해 캡처한 artifact.
- 공개 서비스: URL이 만들어지고 `/ready`와 브라우저 흐름을 확인한 이후에만 완료.
- 라이브 OpenAI·Claude: 실제 키와 모델·단가 설정 후 별도 측정.

API·LLM·배포의 미측정 항목을 테스트 통과로 표시하지 않습니다.
