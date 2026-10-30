# 테스트가 무엇을 막는가

테스트 개수는 자랑거리가 아니라서 숫자를 늘리려고 쓰지 않았습니다. 이 문서는 지금 있는 테스트가 **어떤 고장이 나면 실패하는지**를 묶어 보여 주는 표입니다.

## 개수

| 구분 | 개수 | 확인 방법 |
|---|---|---|
| Python(pytest) | **574**: 단위 430 + 통합 144 | `uv run pytest --collect-only -q \| tail -1` |
| 그 가운데 서로 다른 테스트 함수 | 480 | 같은 목록에서 `[...]`를 뗀 이름 |
| 파라미터를 바꿔 늘어난 사례 | 94(함수 22개가 116사례) | 한 함수가 45사례(허용 목록 밖 명령 거부)를 차지합니다 |
| Playwright(브라우저) | **80**: 시나리오 40 × 데스크톱·모바일 두 화면 | `cd web && npx playwright test --list` |
| 평가 게이트(pytest 밖) | 매 커밋 API 없이 실행해 커밋된 기준선과 비교 | [EVALUATION-GATE.md](EVALUATION-GATE.md) |
| 알림 규칙 테스트(pytest 밖, promtool) | 알림 검사 26개(알림 13개가 울릴 때와 조용할 때) + 기록 규칙 값 검사 2개 | `promtool test rules deploy/prometheus/tests/*.test.yml`, [OBSERVABILITY.md](OBSERVABILITY.md#알림-규칙과-slo) |

통합 테스트는 실제 PostgreSQL(권한이 제한된 역할, 행 단위 보안 포함)에서 돌고, CI는 푸시마다 전부 실행합니다(같은 브랜치에 연달아 푸시하면 앞 실행은 취소됩니다). 개수는 수집된 사례 수이고 날짜별로 이렇게 늘었습니다.

| 날짜 | 더한 것 | 누계 |
|---|---|---|
| 2026-10-24 | 표제란 읽기 테스트 셋 | 437 |
| 2026-10-25 | 알림 규칙과 함께 지표 테스트 둘 → 439. 명령 검색 실측 하네스 둘, 플래너 문맥 하나, 도구가 모두 실패한 계획 둘, 셸이 있어야 되는 명령의 거부 셋 → 447. 끝난 실행이 디스패처의 빈 칸을 바로 넘기는지 보는 테스트 하나 → 448 | 448 |
| 2026-10-28 | 스캔 읽기 강화의 테스트 넷 → 452. 스캔 읽기 예시 화면의 단위 테스트 셋과 브라우저 시나리오 하나 → 455(브라우저 72). 보험 현업자 테스트 뒤 서류 보완 요청의 규칙 테스트 하나와 서버 시험 하나, 브라우저 시나리오 하나 → 457(브라우저 74) | 457 |
| 2026-10-29 | 서류 재접수(계획·측정·API 시험 열둘)와 서류가 판단되지 않은 담보 옆의 부지급 규칙 하나, 보안 점검에서 나온 시험(청구 위조·승인 금액·이중 지급·취소된 실행·한도·본문 크기·NUL·정규식)과 하트비트·코퍼스 재현성·모델 어댑터 계약·게이트 시험 → 488 | 488 |
| 2026-10-29 오후 | 약점을 다시 따져 더한 시험 51개: 검색 상태를 한 번 읽어 두는 것(스냅숏 id가 예전과 같음, 모든 종류의 쓰기가 카운터를 올림, 롤백된 쓰기는 기억되지 않음, 옛 경로와 순위가 같음)과 도번 색인, 겹치는·이어진 입원과 수술분류표 규칙, 서명된 승인 영수증과 청구인 안내문·접수 기한, 라이브 모델 읽기의 설정·예약·상한, 벡터 색인 확인을 코퍼스 상태마다 한 번만 하는 것 | 539 |
| 2026-10-29 밤 | 벡터 색인 7사례(함수 6), 담당자 신원 9, 사흘 대기 뒤 재개 1 | 556 |
| 2026-10-30 | 감사에서 재현한 결함마다 먼저 실패하는 시험을 둠: 청구 규칙(보험기간 날짜, 흐린 날짜의 부지급, 코드 없는 수술, 조정 승인의 키) 5, 값과 인용의 일치 2, 손으로 계산한 정답 26건을 도는 시험 1, 도번 정규화 4, 같은 키 재시도로 예약을 건너뛰는 것·죽는 worker의 시도 횟수·준비 구간 실패·500건 넘는 이벤트 5, `/docs` 끄기 1. 브라우저 시나리오 셋(접근성 검사)과 호출 트리 확인 | 574(브라우저 80) |

## 묶음별로 무엇을 막는가

한 테스트가 둘 이상의 위험에 걸칠 수 있어서 가장 가까운 묶음에 한 번만 넣었습니다. 사례 수 합계가 574입니다.

| 막으려는 고장 | 사례(함수) | 파일 | 실패로 바뀌는 사례의 예 |
|---|---|---|---|
| **돈이 틀리게 나가거나 두 번 나가는 것** | 76(76) | `test_claims`, `test_claims_policy`, `test_claims_independent_gold`, `test_review_escalation`, `test_review_rules`, `test_claims_model_path`, `test_claims_audit`, `test_resubmission`, `test_receipts_and_notices`, `test_long_wait_resume`| `crash_after_ledger_write_resumes_without_paying_twice`(지급 기록 직후 일시 오류가 나도 다시 쓰지 않음), `a_senior_claim_is_paid_only_after_two_different_people_approve`, `a_remarks_amount_the_model_quoted_is_dropped_and_the_claim_goes_to_a_person`, `deductible_days_are_not_paid` |
| **중간에 죽거나 실패한 뒤 다시 시작하는 것, 큐가 멈춰 서는 것** | 55(48) | `test_recovery`, `test_review_recovery`, `test_retry`, `test_provider_retry`, `test_review`, `test_pool`, `test_pool_worker`, `test_workflow`, `test_dispatcher`, `test_provider_contract`, `test_heartbeat` | `real_worker_crash_resumes_without_repeating_retrieval`, `old_worker_fencing_token_cannot_complete`, `exhausted_retries_are_dead_lettered`, `timeout_kills_the_worker_and_the_next_job_still_runs`, `cancelled_run_cannot_finalize`, `a_finished_run_starts_the_next_queued_one_without_waiting_for_the_poll` |
| **서류와 질의에 든 지시문, 모델이 지어낸 값을 받아들이는 것** | 27(27) | `test_claims_extract`, `test_claims_injection_model`, `test_query_understanding` | `a_field_label_inside_a_remark_never_becomes_the_value_and_cannot_ground_a_model`, `grounding_keeps_only_values_whose_snippet_is_in_the_document`, `a_proposal_is_kept_only_when_its_quote_is_in_the_query`, `an_exclusion_removes_the_material_the_pattern_took_as_wanted` |
| **모르는 것을 맞다고 하는 것** | 35(29) | `test_conditions`, `test_review_guards`, `test_agent_evidence`, `test_graph_evidence` | `40mm_rejects_42mm`, `missing_material_is_unknown`, `uncertain_ocr_is_unknown`, `search_does_not_supply_inspection_evidence`, `graph_does_not_approve_uninspected_search_results`, `a_plan_whose_every_tool_failed_gets_one_more_turn_and_only_one` |
| **에이전트의 도구, 예산, 경로가 정해진 범위를 벗어나는 것** | 100(47) | `test_tools`, `test_shell_search`, `test_shell_search_tool`, `test_saved_tools`, `test_agent_pilot`, `test_agent_trajectory`, `test_agent_trajectory_run`, `test_trajectory`, `test_trajectory_gate` | `arbitrary_code_cannot_be_a_tool`, `budget_never_allows_an_extra_call`, `everything_outside_the_closed_set_is_refused`(45사례), `a_command_that_would_fail_for_want_of_a_shell_is_refused_with_how_to_type_it`, `each_wrong_road_is_caught`, `a_broken_claim_graph_is_seen_by_the_path_checks` |
| **검색이 엉뚱한 도면을 올리거나 낡은 색인을 읽는 것** | 80(70) | `test_drawing_number`, `test_retrieval_ids`, `test_retrieval`, `test_korean_search`, `test_reranking`, `test_indexing`, `test_dense_index`, `test_geometry`, `test_corpus_state`, `test_number_index`, `test_vector_index`| `one_edit_typo_falls_back_to_fuzzy_only_when_nothing_matches_exactly`, `material_code_is_not_mistaken_for_an_identifier`, `constraint_order_never_displaces_an_explicit_drawing_number`, `search_rejects_old_snapshot_before_reading_vectors` |
| **스캔·OCR·표제란·이미지 읽기가 틀린 값을 사실로 올리는 것** | 90(74) | `test_scan_examples`, `test_ocr`, `test_ocr_scan`, `test_official`, `test_official_scan`, `test_title_block`, `test_vision`, `test_ingest` | `a_badly_degraded_scan_never_ends_in_a_wrong_payment`, `a_field_the_clerk_left_empty_is_not_invented`, `a_material_grade_is_not_taken_for_a_drawing_number_and_a_page_footer_is_not_either`, `unknown_or_invalid_vision_numbers_are_not_promoted_to_facts` |
| **측정이 스스로를 속이는 것** | 53(52) | `test_evaluation`, `test_evaluation_api`, `test_evaluation_runner`, `test_gate`, `test_stats`, `test_retrieval_metrics`, `test_retrieval_ablation`, `test_dataset`, `test_prompt_experiment`, `test_claims_model_eval`, `test_vendor_ocr`, `test_shell_search_model`, `test_synth_determinism` | `model_input_never_contains_gold_or_prediction`, `gate_never_tolerates_a_wrong_payout`, `gate_requires_the_degraded_control_to_be_worse`, `editing_a_prompt_after_the_dev_run_voids_the_dev_report`, `family_splits_disjoint` |
| **다른 고객의 데이터가 보이거나 지난 판이 바뀌는 것, 답한 사람이 누구인지 모르는 것** | 41(40) | `test_storage`, `test_api`, `test_managed_role`, `test_live_config`, `test_identity`, `test_identity_api`| `foreign_tenant_cannot_read_revision_or_asset`, `immutable_revision`, `public_demo_cannot_spend_configured_provider_keys`, `migration_grants_set_to_non_superuser_owner`, `every_other_token_is_refused`, `the_second_approval_must_come_from_another_account_whatever_the_name_says` |
| **관측이 비거나, 관측 때문에 실행이 깨지는 것** | 17(17) | `test_observability`, `test_otlp_export`, `test_otlp`, `test_trace` | `a_paused_run_is_not_exported_until_it_ends`, `an_unreachable_or_refusing_backend_never_fails_the_caller`, `counters_do_not_go_down_when_a_workspace_expires_and_waits_are_exported`, `an_unhandled_error_is_counted_as_a_500` |

## 이 표가 말하지 않는 것

- 테스트가 통과한다는 것은 이 저장소의 합성 데이터와 스텁 모델 위에서 위 고장이 나지 않는다는 뜻입니다. 실제 서류, 실제 부하, 실제 장애에서의 동작은 보장하지 않습니다.
- 모델을 실제로 부르는 측정은 pytest가 아니라 상한을 둔 수동 workflow에서 하고, 그 숫자는 [README의 측정표](../README.md)와 [evaluation/](evaluation/)에 있습니다. pytest는 모델을 부르지 않고 스텁을 씁니다(키 이름이 나오는 테스트는 가짜 키를 넣고 공개 데모가 그 키를 쓰지 못하는지만 봅니다).
- 스캔 읽기와 합성 코퍼스에 기대는 테스트는 Tesseract(한국어)나 코퍼스가 없으면 건너뛰도록 되어 있습니다. CI는 둘을 설치하고 생성한 뒤 돌립니다([ci.yml](../.github/workflows/ci.yml)). 이 문서의 556은 수집된 사례 수이고, 환경에 따라 실제로 실행되는 수는 더 적을 수 있습니다.
- 묶음은 파일 이름과 테스트 이름을 읽고 제가 나눈 것입니다. 테스트 하나가 한 묶음에만 속하는 것은 아닙니다.
