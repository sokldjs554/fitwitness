# 텔어스 공고 대조 — 2026-10-04

검토 기준은 사용자가 제공한 AI Research Engineer - Agents & Workflows 공고 전문입니다. 채용 담당자의 내부 평가나 합격 가능성을 추정한 점수가 아닙니다. 코드·문서·실측 결과와 독립 코드 리뷰를 대조했습니다.

## 결론

프로젝트 주제는 회사의 제조 도면 검색 문제와 직접 맞습니다. 조건별 PDF 근거, 개정판으로 인한 판단 무효화, 장애 후 재개, 비용과 테넌트 경계는 보여줄 만한 엔지니어링 증거입니다. 그러나 현재를 공고 전체 구현 완료 또는 완성된 LLM Agent 연구 성과라고 소개하면 과장입니다. 앞선 완료 보고의 범위는 v0.2 UI와 좁은 LLM 판정 평가였습니다.

## 주요업무

| 공고 항목 | 실제 근거 | 현재 판단·보완점 |
| --- | --- | --- |
| Agentic System Design | `agents/graph.py`: LangGraph, 도구 계획, 검사 반복, `agents/tools.py`: 7종 typed tool | 부분 구현. 실제 provider 도구 호출 실측 없음. intent는 정규식이며 모델 의도 추출이 아님. `fitwitness`는 challenger 프롬프트 전환이며 독립된 Multi-Agent가 아님. |
| Advanced Retrieval | `retrieval/pipeline.py`: 도번·BM25·RRF, `embeddings.py`: e5/OpenCLIP, `repository.py`: pgvector | 도번·키워드 경로는 사용 중. 운영 worker에는 encoder가 전달되지 않고 seed도 vector 색인을 만들지 않음. 의미·이미지 통합 검색 성능 미측정. |
| Prompt Engineering & Orchestration | LangGraph + LangChain provider의 구조화 출력, 모델·도구 예산 | 프롬프트 버전 간 비교나 최적화 실험 없음. 도구 선택/실행 결과와 후속 판단 개선을 실제로 검증해야 함. 공개할 것은 도구·근거·결정 기록이며 모델의 비공개 사고과정이 아님. |
| Evaluation | 실제 Qwen3-1.7B 24개 사례 × 3회, 원시 JSONL·프로토콜·오류·신뢰구간, CI 회귀 | PDF 근거 판정 pilot은 실측. 검색 recall/nDCG, 도구 선택, 전체 업무 성공률, Agent 방식 비교, 산업 도면 평가는 미완료. |
| Monitoring | DB 이벤트·usage·lease·run 상태, HTTP Prometheus counter/histogram | 앱 수준 기록은 구현. OpenTelemetry는 의존성만 있고 span/exporter 구현 없음. 노드/모델/도구별 지연·오류·비용 대시보드 및 알림은 추가 필요. |
| Production Deployment | Render Free + Neon PostgreSQL, RLS·세션·실행 quota·health·CI | 공개 체험 배포·격리는 검증. SaaS 운영 능력의 일부 증거이며 enterprise SSO, 부하/장기 운영 검증, 운영 SLO 완료를 뜻하지 않음. Docker 별도 경로의 평가 데이터 누락을 발견해 보완 대상에 포함. |

공고의 ReAct/Multi-Agent, LangChain/LangGraph, 제조/의료 사례는 예시를 포함합니다. 모든 프레임워크를 동시에 사용하거나 보험금 지급까지 구현해야 적합한 것은 아닙니다. 제조 문제 하나에서 실제 Agent의 품질 개선을 입증하는 편이 지금의 우선순위입니다.

## 자격요건·우대사항

| 항목 | 이 프로젝트가 보여주는 것 / 남은 근거 |
| --- | --- |
| 관련 학사·동등 역량, ML/AI 실무 1년 또는 동등 역량 | 프로젝트만으로 학위·근무기간을 증명할 수 없음. 지원자의 실제 학력·경험과 별도로 제출해야 함. |
| LangChain/LangGraph·OpenAI/Claude 앱 구축 | 실제 LangGraph 앱과 두 provider 어댑터 존재. OpenAI·Claude 실호출은 아직 확인하지 못했으므로 두 API 활용 성과로 쓰지 않음. |
| trace/log/metric 설계·운영 | 실제 실행 이벤트·비용 장부·HTTP 지표·배포 경험. 분산 추적·운영 기간·장애 대응 경험은 별도 증거 필요. |
| Codex/Claude Code 적극 활용 | Codex로 개발·수정·회귀 검증한 변경 기록이 존재. 사용하지 않은 Claude Code나 측정하지 않은 생산성 향상률을 주장하지 않음. |
| LLM/VLM/Agent 개발·최적화 | Qwen 실측 있음. `ModelClient.read_image`가 그래프에서 호출되지 않아 VLM 업무 적용·최적화 완료는 아님. |
| 기술 내재화·공유 | 한글 README·원시 평가·재현 문서·독립 리뷰 기록. 현재 비공개 저장소이므로 공개 오픈소스 활동이라고 부르지 않음. |
| Evals·회귀·실험 설계 | 고정된 입력·family 분리·반복·실패 포함 지표·CI 존재. 모델·검색·프롬프트별 ablation 및 별도 실데이터 검증 추가 필요. |
| 상태 저장·재시도·장애 복구 장기 workflow | 실제 PostgreSQL checkpoint와 worker 종료 복구 검증. API 429/timeout/schema 오류는 terminal failed이며 backoff 재시도·human resume·장기 실행은 미구현. |

## 독립 리뷰에서 확인한 구체적 한계

1. 기본 live 예제는 16,000토큰 제한인데 PDF 사실과 도구 스키마를 모두 넣는 보수적 예약은 표준 후보 5개만으로 최소 20,639토큰입니다. 예제에 명시적 64,000토큰 예산을 넣되, 실제 성공으로 간주하지 않습니다. 프롬프트 축소·호출별 사전 점검도 필요합니다.
2. encoder가 없는 실행에도 모델 컨텍스트에는 의미·이미지 도구 스키마가 노출됩니다. 가용 도구만 제공하는 경로를 검증해야 합니다.
3. inspection 도구 결과는 observations에 쌓이지만 기존 candidate의 facts에 반영되지 않습니다. 현재 최종 판정은 검색 때 이미 가져온 전체 facts를 사용합니다. 따라서 누락 근거를 도구로 보충해 판정을 개선했다는 주장은 불가합니다.
4. `stop_condition`은 반환 타입에는 있지만 routing에 쓰이지 않습니다. 후보가 없으면 unknown 여부도 false라 추가 탐색 없이 끝납니다. 최대 두 번 반복을 일반적인 자율 Agent 완성으로 볼 수 없습니다.
5. `retry_wait`·`waiting_input` 이름은 있어도 실제 해당 상태 전이가 구현되지 않았습니다. 프로세스 복구와 외부 API 재시도는 구분해야 합니다.
6. 전체 snapshot 기준 재검증이며 변경된 근거만의 최소 재검증·호출 절감 효과는 입증되지 않았습니다.
7. 현재 실험실 UI의 CPU·LOCAL LLM 표시와 seed 표시는 게시된 Qwen 보고서에 맞춰져 있습니다. Claude artifact를 직접 latest.json으로 덮어쓰면 provider 표시와 null seed 처리가 맞지 않습니다. API 결과를 공개할 때 provider별 메타데이터·복수 실험 로딩을 먼저 구현·검증해야 합니다. 준비한 CI는 결과를 자동 게시하지 않습니다.

## 보완 순서와 통과 기준

1. **실제 Claude 도구 실행:** 사용 가능한 도구만 노출하고, 짧은 typed context와 호출 전 예산 점검을 적용합니다. 도구 관측이 다음 판단에 반영되도록 검증합니다. 고정 입력에 대해 실제 request ID·tool arguments/result·최종 근거·토큰·지연·실패를 남깁니다. 키 연결만으로 완료되지 않습니다.
2. **검색 파이프라인 연결:** 재현 가능한 encoder/index 빌드와 실제 graph 연결 후 도번/BM25/semantic/image/hybrid를 동일한 held-out 쿼리에서 비교합니다. recall@k·nDCG·근거 적합성과 지연을 측정합니다. 최종 모델 크기는 무료 호스트의 제약과 분리해 연구 환경에서 먼저 정합니다.
3. **구조 비교:** rules/fixed/ReAct/별도 challenger 구조를 같은 데이터와 예산에서 비교합니다. 모델을 늘리기 전에 기존 방식보다 어떤 실패가 줄었는지·비용이 얼마 늘었는지 측정합니다. 결과가 나쁘면 그대로 남깁니다.
4. **운영 보완:** 재시도 가능한 오류만 bounded backoff, 취소/lease/예산과의 상호작용 테스트, 노드별 관측·알림, 컨테이너 회귀를 추가합니다.
5. **제출 정리:** 공고 근거표, 실제 실패→수정 사례, 측정 결과, 영상·PDF를 코드 버전에 맞춰 정리합니다. 산업 데이터 일반화·프론티어 우위·모든 자격 충족을 추정으로 쓰지 않습니다.

## Claude 키와 비용

사용자는 키를 보유한다고 했으나 이 실행 환경의 `ANTHROPIC_API_KEY`는 비어 있었습니다. 키 값은 요청하거나 출력하지 않았습니다. 먼저 동일 PDF 판정 실험용 실행 경로를 준비합니다. 키는 GitHub Actions secret에 저장하고, 최대 1 USD의 유료 실행 승인을 받은 뒤 전용 marker commit으로 시작합니다. [실행 경로](OPERATOR.md). 이 pilot을 끝내도 위 전체 Agent·검색 항목은 별도 검증해야 합니다.

## 현재 검증 근거

- 배포 코드 `743026f`: 기존 CI `37186711069`, 독립 DB 두 환경 각각 Python 76개·E2E 6개 통과. 공개 서버 검증 기록은 `verification/ui-eval-20261004.json`.
- 이번 재검토에서 기존 단위 테스트 48개를 재실행했고, API seed 기록 회귀 2개를 추가한 최종 단위 테스트 50개가 통과했습니다. 전체 Agent·유료 provider 통과로 확대 해석하지 않습니다.
- Docker 누락은 실제 이미지 CI `37188314830`에서 not_measured 응답으로 재현했습니다. 평가 자료 COPY 후 `37188493629`에서 UI와 평가 API, 원시 기록으로 지표 재계산이 통과했습니다. 이는 컨테이너 패키징 검사이며 전체 Docker Compose 통합을 검증했다는 뜻은 아닙니다.
- Claude 유료 workflow는 일반 push에서 호출되지 않고 전용 marker와 run_attempt == 1을 요구합니다. API seed 미적용을 null로 기록하며, 새 실패 재현 테스트와 독립 focused review가 통과했습니다. 실제 API 호출·점수는 아직 없습니다.

최종 검증: `ba27bd2a98a1671980bfb6b49a2a313d6dce0fd2` 기준 독립 DB 두 환경 각각 Python 78개·E2E 6개가 통과한 CI `37188643062`와 실제 컨테이너 CI `37188643079`가 성공했습니다. 유료 평가 workflow `37188640274`의 evaluate job은 skipped이며 유료 실행을 시작하지 않았습니다. 공개 `/health`는 기존 배포 `743026f`를, `/ready`는 ready를 반환했습니다. 이번 보완은 Docker 패키징·평가 CLI·실행 준비에 관한 것이며 공개 UI 교체를 요구하지 않습니다.
