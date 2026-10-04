# 독립 코드 리뷰와 수정 기록

리뷰 대상: `d2625b1` 기준 현재 파일. 별도 fresh-context reviewer가 검토했습니다. 릴리스 완료 판정은 하지 않았습니다.

| Important finding | 수정 | 검증 근거 |
|---|---|---|
| 자유 입력 숫자 조건 누락 | 두께·너비·높이 추출, 구조화 조건과 병합, 미지원 수치/부정 표현은 unknown 조건 | `test_review_guards.py` |
| 익명 사용자의 무제한 자원·유료 호출 | 공유 DB quota, 시간당 생성/실행 제한, 2개 dispatcher slot, 만료 세션 정리, 공개 API 유료 모델 비활성화 | `test_shared_admission_limit_is_enforced`, `test_public_demo_cannot_spend_configured_provider_keys` |
| 만료 worker의 부작용·checkpoint | per-run DB session lock, token/expiry guard, token 소유 usage/event 저장, supervisor 자기 token만 사용 | `test_expired_worker_cannot_commit_or_renew`, `test_lease_guards_usage_and_events` |
| 일반 종료/API 재시작 복구 누락 | DB dispatch 큐와 expired lease 재수집, 비정상 종료 시 token 한정 lease 해제 | `test_queued_job_is_discoverable_after_restart`, `test_dispatcher_recovers_abnormal_process_exit` |
| 미래 개정판 즉시 활성화 | 시간대가 있는 날짜 검증, effective_from 적용, active set snapshot 반영 | `test_future_revision_not_active_and_withdrawn_does_not_restore_parent` |
| 개정 도중 실패 후 구 결과 유효 표시 | 추출 선행, 결과 읽기 시 현재 snapshot 재확인 및 stale 표시 | `test_completed_read_detects_missing_invalidation` |
| 실패·복구에서 청구 사용량 유실 | 호출 전 보수적 예약을 DB 저장, 응답 후 실사용 저장, 실패 시 예약 보존, 기존 생성 시점 기준 deadline | `test_reservation_survives_unanswered_provider_call`, `test_lease_guards_usage_and_events` |

회귀 재현: CI `37178482633`에서 추가한 10개 테스트가 실패하는 것을 먼저 확인했습니다. 수정 후 `42851e8` 기준 CI `37178871171`에서 서로 독립된 두 DB 환경 모두 Python 테스트 66개와 브라우저 E2E 4개가 통과했고 실제 시연 영상이 생성되었습니다. 테스트가 존재한다는 사실을 통과로 대신하지 않습니다.

## 판단과 한계

- 원본 ingestion의 여러 저장 단계를 단일 activation transaction으로 묶는 대신, 결과 읽기에서 snapshot을 재검증하는 보수적 방식을 적용했습니다. 추가 DB 읽기가 필요하고 불완전 ingestion은 unknown으로 남을 수 있습니다.
- session lock은 정상 production worker의 중복 실행을 막습니다. 외부 제공자의 exactly-once 청구는 보장하지 않습니다. 응답을 잃은 호출은 예약 금액을 남겨 비용을 낙관적으로 줄이지 않습니다.
- 공개 데모는 규칙 엔진입니다. 실제 키를 가진 운영자 실험은 별도 CLI/서버 환경에서만 수행해야 합니다.
- 일반 자연어의 모든 의미를 이해한다고 주장하지 않습니다. 지원되는 숫자/단위와 명시적 필드를 다루며, 미지원 수치 조건은 확인 필요로 남깁니다.
- 일반 OCR, live VLM/agent 비교, 전체 hybrid 색인 연결·평가, 외부 산업 데이터, 최소 의존성 재검증과 SSE는 완료되지 않았습니다.

## 2026-10-04 LLM 평가·UI 재리뷰

도면 작업대와 실험실, 평가 runner·지표·원시 결과를 별도 fresh-context reviewer가 검토했습니다. 실제 PDF 추출로 데이터셋을 재생성해 해시가 같음을 확인했고, 원시 JSONL과 게시 JSON이 일치하며 모든 점수가 다시 계산됨을 확인했습니다.

- 잘못된 인용이 예외 처리 중 버려져 유효성 비율이 낙관적으로 계산될 수 있는 경로를 수정했습니다. 검증을 통과하지 못한 인용도 기록하고 지표 분모에 포함합니다.
- API 출력 형식 오류 시 이미 받은 토큰·비용·원문·요청 ID가 사라지는 경로를 수정했습니다. 응답 오류가 측정 정보를 함께 전달합니다.
- 두 오류를 재현한 테스트가 먼저 실패했고, 수정 후 단위 테스트 48개가 통과했습니다. 추가 focused review도 통과했습니다.
- 실험실에서 Ctrl/Cmd+K로 검토대로 돌아올 때 검색 입력에 초점이 가도록 수정했습니다. 필터 버튼의 접근성 이름도 명시했습니다.

Qwen 실측 72회는 모두 형식·인용 검증에 통과하여 위 오류의 영향을 받지 않았습니다. 원시 측정과 당시 실행 소스는 변경하지 않았으며, 개선된 runner는 이후 실험부터 적용됩니다.
