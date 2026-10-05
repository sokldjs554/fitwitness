# 텔어스 공고 대조 — 2026-10-05

사용자가 제공한 공고의 제조 도면 검색 문제에 맞춘 독립 포트폴리오입니다. 회사의 내부 평가나 합격 가능성을 추정한 문서가 아닙니다. [최초 감사](audits/2026-10-04-initial-and-followups.md)는 당시 상태로 보존합니다.

| 주요업무 | 실제 구현·측정 근거 | 남은 한계 |
|---|---|---|
| Agentic System Design | LangGraph 단일 계획·반복 계획·별도 planner/challenger, typed tools, Claude 실제 도구 호출, 조회한 사실만 검증 | 최초6회 중3회 실패 보존. 수정 후 각 구조1회 완료는 같은 사례의 진단이며 일반 성공률이 아님 |
| Advanced Retrieval | 도번·BM25·E5·OpenCLIP·pgvector·원자적 색인·snapshot 검증. RRF/BGE/조건 정렬216회 실제 비교 | NIST 외부 STEP 11쌍에서 AP203→AP242 CAD 거리 Top-1 11/11 실측. ANN·생산 공장 분포·대규모 산업 데이터는 미검증 |
| Prompt Engineering & Orchestration | LangChain 구조화 출력, LangGraph 반복·종료, 가용 도구, 누락/미조회 구분, 프롬프트·입력 해시 | 독립 프롬프트 최적화 비교 없음. trace는 도구·근거·결정 기록이며 비공개 사고과정이 아님 |
| Evaluation | Qwen/Claude PDF 비교, Agent 실패·수정 기록, 검색 Recall/nDCG/지연, VLM18회와 graph 실측, 동결 protocol·원시 기록 | 반복을 독립 표본으로 세지 않음. 실제 산업 도면·다양한 의도·전체 업무 성공률 평가 필요 |
| Monitoring | DB 이벤트·모델/도구 지연·역할·usage·오류·비용 예약, HTTP Prometheus, lease·취소·bounded retry | OpenTelemetry exporter·알림·장기 SLO 기록 없음 |
| Production Deployment | Render+Neon, tenant RLS·세션·quota, worker 복구, health/readiness, DB·브라우저·컨테이너 CI | 무료 연구 데모. enterprise SSO·부하·장기 운영 검증 미완료 |

## 자격요건·우대사항

- LangChain/LangGraph와 Claude는 실제 앱·API·도구 사용 기록이 있습니다. OpenAI 어댑터와 비용 제한 workflow는 준비했지만 2026-10-05 credential-readiness에서 OPENAI_API_KEY=false라 실제 호출은 미측정입니다.
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
