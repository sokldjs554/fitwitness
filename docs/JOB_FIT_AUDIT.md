# 텔어스 공고 대조 — 2026-10-05

사용자가 제공한 공고의 제조 도면 검색 문제에 맞춘 독립 포트폴리오입니다. 회사의 내부 평가나 합격 가능성을 추정한 문서가 아닙니다. [최초 감사](audits/2026-10-04-initial-and-followups.md)는 당시 상태로 보존합니다.

| 주요업무 | 실제 구현·측정 근거 | 남은 한계 |
|---|---|---|
| Agentic System Design | LangGraph 단일 계획·반복 계획·별도 planner/challenger, typed tools, Claude 실제 도구 호출, 조회한 사실만 검증. 최종 코드 `0347d5af`에서도 세 구조 3/3 완료·각 5/5 확인 | 최초6회 중3회 실패 보존. 최종 gate도 같은 synthetic family의 연결 검증이며 일반 성공률이 아님 |
| Advanced Retrieval | 도번·BM25·E5·OpenCLIP·pgvector·원자적 색인·snapshot 검증. RRF/BGE/조건 정렬216회 실제 비교 | NIST 외부 STEP 11쌍에서 AP203→AP242 CAD 거리 Top-1 11/11 실측. ANN·생산 공장 분포·대규모 산업 데이터는 미검증 |
| Prompt Engineering & Orchestration | LangChain 구조화 출력, LangGraph 반복·종료, 가용 도구, 누락/미조회 구분, 프롬프트·입력 해시 | 독립 프롬프트 최적화 비교 없음. trace는 도구·근거·결정 기록이며 비공개 사고과정이 아님 |
| Evaluation | Qwen/Claude PDF 비교, Agent 실패·수정 기록, 검색 Recall/nDCG/지연, VLM18회와 graph 실측, 동결 protocol·원시 기록 | 반복을 독립 표본으로 세지 않음. 실제 산업 도면·다양한 의도·전체 업무 성공률 평가 필요 |
| Monitoring | DB 이벤트·모델/도구 지연·역할·usage·오류·비용 예약, HTTP Prometheus, lease·취소·bounded retry | OpenTelemetry exporter·알림·장기 SLO 기록 없음 |
| Production Deployment | Render+Neon, tenant RLS·세션·quota, worker 복구, health/readiness, DB·브라우저·컨테이너 CI | 무료 연구 데모. enterprise SSO·부하·장기 운영 검증 미완료 |

## 자격요건·우대사항

- LangChain/LangGraph와 Claude는 실제 앱·API·도구 사용 기록이 있습니다. 최종 provider gate `37321337436`에서 현재 코드로 Claude 세 구조 3/3 완료, 실제 model/tool usage와 비용까지 보존했습니다. OpenAI는 선택적 어댑터만 유지하며 최종 측정 범위에서 제외했습니다.
- VLM은 operator opt-in이며 관측은 항상 `uncertain`입니다. 이번 실측23/36은 추출·누락 판정이며 업무 적합성 정확도가 아닙니다.
- PostgreSQL checkpoint·lease·worker 종료 복구와 제한된 API 재시도를 구현했습니다. 수일간 작업·human resume·외부 청구 exactly-once는 미검증입니다.
- Codex 개발·리뷰·회귀 수정 기록이 있습니다. 사용하지 않은 Claude Code나 측정하지 않은 생산성 향상률을 주장하지 않습니다.
- 한국어 재현 문서와 원시 결과를 제공합니다. private 저장소이므로 공개 오픈소스 기여로 소개하지 않습니다.
- 학위·실무 기간은 프로젝트만으로 증명할 수 없으며 지원자의 실제 이력과 별도입니다.

## 지원 시 설명할 핵심

검색한 후보의 근거를 조회하고, 없는 값을 확정하지 않으며, 개정판·장애·비용 한도를 다루는 흐름이 차별점입니다. 실패를 숨기지 않고 재현·수정한 기록이 연구 엔지니어 업무와 연결됩니다. 공고 전체 충족이나 산업 현장 수준의 완성을 주장하지 않습니다.

[검색 실측](RETRIEVAL.md) · [VLM 실측](VISION.md) · [LLM/Agent 평가](evaluation/README.md) · [리뷰](REVIEW.md)


## 외부 CAD 후속 — 2026-10-05

NIST PMI STEP 원본 SHA를 고정한 Actions `37313290927`에서 AP203/AP242 11쌍을 측정했습니다. 22개 선택 파일 모두 파싱됐고 같은 설계가 Top-1 11/11, MRR 1.0이었습니다. 이 결과는 실제 외부 engineering benchmark에 대한 geometry extraction/거리 검색 근거이지만, 생산 산업 도면·PMI 의미·공차 승인 일반화로 소개하지 않습니다. 원시 runs SHA-256은 `9041974fa5b2f6343349553dce446a62b26c2fb57e58532304725ff312b740f1`입니다.


## 최종 Claude provider gate — 2026-10-05

현재 코드 트리에서 Actions `37321337436`으로 Claude Haiku 4.5를 다시 실측했습니다. 단일 도구 계획·반복 계획·planner+challenger가 모두 completed였고 각 5/5 판정이었습니다. 실제 모델/도구 호출은 1/6, 1/6, 2/7회, 추가 비용은 총 $0.016586, 예약 잔액은 0입니다. 이는 단일 synthetic family의 연결 검증이며 일반 성공률이나 구조 우위를 주장하지 않습니다.


## 워크플로 보강 후속 — 2026-10-06

공고의 "agents that define their own tools", "human-in-the-loop", "모니터링" 항목에 대응해 다음을 추가하고 실제 PostgreSQL 통합 테스트로 확인했습니다. 유료 모델 호출은 없었습니다.

| 항목 | 추가된 것 | 남은 한계 |
|---|---|---|
| Agentic System Design | 에이전트가 정의·재사용하는 저장 검색 도구(`define_search_tool`/`run_saved_search`), 닫힌 채널 집합·템플릿 자리·RLS·개수 제한 | 저장 도구는 텍스트 검색 조합에 한정. 실제 모델이 정의한 도구의 품질은 미측정 |
| Human-in-the-loop | `waiting_input` 상태, LangGraph `interrupt()`, `POST /api/runs/{id}/resume`, 담당자 판정의 Evidence 기록, 검토대 입력 UI | 승인 권한 모델·감사 로그 보존 기간 없음. 장기 대기 미측정 |
| 내결함성 | 일시 오류의 지수 백오프 `retry_wait`, 3회 소진 시 `dead_lettered`, checkpoint 재개, 대기 시간 마감 제외 | 외부 청구 exactly-once는 여전히 미보장 |
| Monitoring | DB에서 계산하는 Prometheus 지표(상태·비용·토큰·도구·이벤트·지연 히스토그램), 실행별 span tree API | OpenTelemetry exporter·알림·SLO 기록은 없음 |
| Evaluation | API 없이 매 커밋 베이스라인과 비교하는 게이트, 도번 변형·오타 질의 유형, 열화 대조군 | 산업 데이터·의미 채널은 게이트 밖 |
| 검색 | 도번 정규화(공백·밑줄·O/0·시리즈·한 글자 오타) | 산업 도번 체계는 합성 규칙과 다를 수 있음 |

이전 절의 "human resume 미검증"은 이번 후속으로 통합 테스트 수준에서 검증됐습니다. 세부는 [WORKFLOW.md](WORKFLOW.md), 자동 측정은 [EVALUATION-GATE.md](EVALUATION-GATE.md)에 있습니다.


## 보험 청구 시나리오 추가 — 2026-10-06

공고가 제조 도면과 나란히 든 두 번째 예시(진단서·수술확인서에서 필요한 내용만 추출, 사내 기준 적용, 사람이 개입하지 않아도 지급되는 파이프라인)를 같은 런타임 위에 구현했습니다. 세부는 [CLAIMS.md](CLAIMS.md)에 있습니다.

| 공고 문장 | 구현 | 측정 (합성 120건, API 없음) | 한계 |
|---|---|---|---|
| 서류에서 보험금에 필요한 내용만 추출 | 라벨 기반 추출기 + 위치(bbox) 근거, 모델 제안은 인용 스니펫이 서류에 있을 때만 채택 | 필드 정확도 97.7% | 벡터 PDF 합성본. 실제 스캔·손글씨 미검증 |
| 사내 지급 기준 적용 | 상품별 기준표를 데이터로, 모든 금액·거절·보류에 규칙 ID | 결정 정확도 92.5%, **잘못 지급 0건** | 기준표는 가상 값 |
| 여러 Agent 조합, 사람 개입 없이 지급 | intake→extract→validate→adjudicate→challenge→(review)→payout, 멱등 원장, 중단 후 재개 시 중복 지급 없음 | 자동 처리율 88.5%, 열화 대조군 잘못 지급 15.8% 감지 | 모델 추출 경로는 유료 실측 안 함 |
| 의사결정 포인트 | 신뢰도·정합성·서류 누락·한도·근거 없는 지급 항목에서 `waiting_input`, 심사자 답변으로 재개 | 통합·브라우저 테스트로 확인 | 심사 권한 모델 없음 |
