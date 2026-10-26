# FitWitness

**도면의 생김새보다, 맞는 근거.**

제조 부품의 도면을 검색하고 실제 치수·소재 근거로 검증하며, 개정판이 생기면 기존 판단을 무효화하는 연구 프로젝트입니다. 텔어스 채용 공고의 문제를 바탕으로 만든 독립 포트폴리오입니다.

**[공개 데모 열기](https://fitwitness.onrender.com/)** · **[보험 청구 심사 데모](https://fitwitness.onrender.com/#claims)** · [검증 기록](docs/verification/hosted-20261026-adec980.json)

![도면 검토 작업대](docs/media/review-workbench.png)

![실제 LLM 평가와 오류 사례 탐색](docs/media/evaluation-lab.png)

실제 Claude·Qwen·E5·OpenCLIP 실측과 실패→수정 기록은 [실측 기록](docs/EVIDENCE.md)에 원문 그대로 보존했습니다. 아래는 현재 브랜치 기준 요약입니다.

| 측정 | 결과 | 기록 |
|---|---|---|
| PDF 근거 판정, Claude Haiku 4.5 72회 | 72/72 정답, 잘못된 일치 0/42, $0.110455 | [evaluation/README](docs/evaluation/README.md) |
| 전체 Agent 세 구조, 최종 코드 Claude gate | 3/3 완료, 각 5/5 판정, $0.016586 | [claude-final-gate.json](docs/evaluation/claude-final-gate.json) |
| 실제 모델이 고르는 경로, test 6개 family × 세 구조, 네 번 실행 (판정은 결정적 검증기, 모델은 도구 선택과 종료만) | 1차 planner+challenger 4/6 완료 → 스키마 실패·없는 저장 검색 호출을 고침 → 3차 단일·반복 계획 24/30·26/30 → 남은 계획 횟수·미조회 필드·돌릴 수 있는 채널을 컨텍스트에 넣음 → 4차 18/18 완료, 90/90, 조회율 1.0. 합계 $0.402162 | [evaluation/README](docs/evaluation/README.md#실제-모델이-고르는-경로--2026-10-22) · [agent-trajectory.json](docs/evaluation/agent-trajectory.json) |
| 복합 검색 288회 + 재정렬 216회 | Recall@5 복합 33.3%, 조건 정렬 64.2% | [RETRIEVAL.md](docs/RETRIEVAL.md) |
| 외부 NIST STEP 11쌍 CAD 거리 검색 | Top-1 11/11, MRR 1.000 | [CAD.md](docs/CAD.md) |
| 이미지 채널 비교, 같은 질의 그림 36개 × 3회 | DINOv2(CLS)가 OpenCLIP보다 이미지 질의 Recall@5 11.1% → 37.5%, 복합 검색에 넣으면 설명+이미지 9.7% → 26.4%. 두 채널을 함께 넣으면 오히려 하락. 기본 채널은 아직 바꾸지 않음 | [RETRIEVAL.md](docs/RETRIEVAL.md#이미지-채널-비교--dinov2-vs-openclip-2026-10-23) |
| 검색 질의를 모델(Claude Haiku 4.5)이 읽어 조건으로 (family마다 쓰기 방식 10가지, dev 60문장 뒤 test 60문장 세 번, 틀 밖 30문장 한 번, 현업자의 실제 검색어 10개 두 번, 열한 묶음 $0.84) | 패턴 읽기는 12/60, 모델 읽기는 dev와 test 세 번 모두 60/60이고 질의에 없는 조건은 인용이 없어 버려짐 0·불일치 0. 틀 밖 30문장(조건 없는 숫자 10개 포함)은 패턴 12/30, 모델 28/30. 복합 검색 Recall@5 test 42.9% → 패턴 조건 정렬 59.3% → 모델 조건 정렬 76.5%, 읽은 부품 종류를 후보 풀과 정렬에 쓰니 89.0% (풀 상한 79.2% → 95.3%). 현업자가 실제로 치는 검색어 10개(치수 조건 0)에서는 모델이 코퍼스에 없는 부품 셋(롤러·플레이트·고정판)을 네 종류 중 하나로 억지로 읽어 7/10, 패턴은 10/10. 네 종류가 전부라고 프롬프트에 적은 뒤 10/10(같은 세트라 held-out 아님), dev·test 60문장은 60/60·60/60 그대로. 90문장은 제가 쓴 것이고 현장 질의는 이 10개뿐 | [RETRIEVAL.md](docs/RETRIEVAL.md#질의를-모델이-읽어-조건으로-2026-10-23) · [query-understanding.json](docs/evaluation/query-understanding.json) |
| 명령으로 검색하는 도구(`search_shell`)를 실제 모델(Claude Haiku 4.5)에게 켜고 끄고, 순위 검색이 다 못 돌려주는 집합 질의 8개 × 합성 도면 30장 (세 묶음 $0.228) | test: 정답 47장 중 셸 끔 29, 켬 35, 규칙 21, 틀린 일치 0. 켠 쪽도 명령은 8번 중 2번(재질 없는 도면 6/6을 `grep -rL` 한 줄로). SUS304 18장은 두 쪽 다 순위 검색의 6장에서 멈춤 → 문맥에 작업 공간 크기와 순위 검색 상한을 넣고 dev에서 전후로 재니 정답 42장 중 24 → 36, SUS304 6 → 18. 모델이 친 명령 7개 중 3개 실패(`-r` 없음, `xargs`, `*.txt`) | [SHELL-SEARCH.md](docs/SHELL-SEARCH.md#실제-모델로-잰-결과-2026-10-25) |
| 보험 청구 자동 심사, 합성 120건 (API 없음) | 잘못 지급 0건, 결정 정확도 92.5%, 자동 처리 88.5% | [CLAIMS.md](docs/CLAIMS.md) |
| 같은 서류를 스캔처럼 열화시켜 OCR로 읽기 (4단계 × 2분할, 480건) | 잘못 지급 0건, 잘못 부지급 0건, 필드 정확도 90.6~95.8%, 자동 처리 67.9~91.7% | [CLAIMS.md](docs/CLAIMS.md#스캔-문서-읽기-ocr) |
| 실제 한국어 공공문서 12쪽에 OCR 엔진 호출(의료 서식 아님) | 기본 설정에서 숫자 재현율 0.66, 표가 많은 쪽에서 무너지고 sparse 모드는 0.90 | [CLAIMS.md](docs/CLAIMS.md#실제-한국어-공공문서로-잰-ocr) |
| 공식 서식 두 가지(진단서·진료비 계산서)를 칸 구조로 읽기, 생성기의 값을 채운 서식 1200필드 × 위치 흔들림 5단계 | 실제 빈 서식과 다시 그린 판 모두 전부 정답, 빈 서식에서 읽은 값 0개 (병원이 실제로 찍는 위치는 아님) | [CLAIMS.md](docs/CLAIMS.md#공식-서식-읽기) |
| 스캔한 공식 서식 두 가지를 격자 등록으로 읽기 (test 60건 × 열화 4단계, 코드를 고정한 뒤 한 번) | 필드 91~93% (OCR 경로만으로는 1% 안팎), 잘못 지급·부지급 0건. 청구 결정 정확도는 3~15%p 올랐고 자동 처리율은 거의 그대로 | [CLAIMS.md](docs/CLAIMS.md#스캔한-공식-서식-읽기) |
| 서류 비고란의 지시문·가짜 라벨 (11건 × 7문구 = 77쌍, 깨끗한 판과 비교) | 고치기 전: 가짜 라벨이 합계·진단코드·입원일을 덮어써 33쌍에서 값이 바뀌고 2쌍이 부지급. 고친 뒤: 값 변화 0, 지급·부지급 0, 가짜 라벨 14쌍은 담당자 확인으로. 매 커밋 게이트 | [CLAIMS.md](docs/CLAIMS.md#서류-본문의-지시문과-가짜-라벨-2026-10-23) |
| 같은 주입 판을 실제 모델(Claude Haiku 4.5)의 추출 경로로 (2건 × 7문구 = 14쌍, 세 묶음 $0.17) | 1차 16건 전부 실패(모델 경로가 한 번도 끝까지 돈 적 없었음) → 2차 14건 실패(인쇄된 금액 문자열) → 3차 16건 완료: 지시문·승인 주장은 모델을 못 움직임, 가짜 합계·가짜 진단코드는 2/2 모델이 제안했고 grounding이 버려 결정 변화 0. 다만 규칙 경로와 달리 담당자에게 가지 않아, 버려진 지급 필드는 담당자 확인으로 보내는 R-CHAL-02를 더하고 스텁 모델 통합 테스트로 확인 | [CLAIMS.md](docs/CLAIMS.md#모델-추출-경로에서-2026-10-23) · [claims-injection-model.json](docs/evaluation/claims-injection-model.json) |
| 스캔한 공식 서식 test 98쪽을 상용 문서 AI(Upstage information-extract)와 격자 판독기에 같이 (light·medium·heavy 세 단계, 같은 채점, 단계마다 어림 $3.92) | 벤더 light 446/446, medium 446/446(구간 99.2%부터), heavy 444/446(틀린 값 둘: 진단코드의 I를 1로, 병명 칸에 부 질병 미끼). 격자 판독기 light 413/446(92.6%), medium 431/446(96.6%), heavy 399/446(89.5%)이고 틀린 값은 9~27개, 빈 값은 6~20개. 이 비교는 필드 단위 채점이고 벤더의 값을 청구 파이프라인에 넣어 보지는 않았음. 합성 서식의 그림이지 실제 서류가 아니고, 벤더는 그림과 스키마만 받았음 | [CLAIMS.md](docs/CLAIMS.md#스캔-문서-읽기-ocr) · [vendor-ocr.json](docs/evaluation/vendor-ocr.json) |
| 청구 test 60건을 실제 모델(Claude Haiku 4.5)의 추출 경로로, 규칙 판독기와 같은 바이트에서 (텍스트 층 세 번과 medium 스캔 한 번, 네 묶음 $1.01) | 텍스트: 모델 결정 정확도 93.3%, 규칙 88.3%, 잘못 지급·부지급 0, 자동 처리 92.9% 대 85.7%. 스캔: 모델 75.0%, 규칙 76.7%, 모델이 1건 잘못 부지급(OCR이 D05.1을 005.1로 읽은 것을 그대로 제안). 규칙 경로에만 있던 "스캔에 근거한 부지급은 담당자에게" 보호를 모델 경로에도 넣음. 텍스트 묶음을 세 번 돌리니 93.3·90.0·95.0%, 세 번 모두 gold와 같은 결정 52/60, 잘못 지급·부지급은 세 번 모두 0 | [CLAIMS.md](docs/CLAIMS.md#test-60건에서-규칙-판독기와-나란히-2026-10-23) · [claims-model.json](docs/evaluation/claims-model.json) |
| 지급 직전에 모델(Claude Haiku 4.5)이 결정을 감사해 담당자에게만 보낼 수 있게 하고, 약해진 규칙이 지급으로 보낸 120건과 멀쩡한 규칙이 지급으로 보낸 120건에서 (일곱 묶음 $0.92) | 계산된 사실 없이 보낸 1차 확인은 옳은 지급 4/4를 세웠음(날짜 셈·정액 오해). 날짜 차이와 대조를 코드가 계산해 넘긴 뒤: 약해진 규칙이 통과시킨 잘못된 지급 19/19를 세움(한도 초과 12, 이미 지급 7, 서류 없음 2; 구간 83.2%부터), 옳은 지급은 1/46과 0/46. 사실 절이 답을 거의 말해 주므로 모델이 스스로 찾은 것은 1건. 결정 하나에 $0.003, 0.8 s. 세 번 돌려 19/19 세 번, 옳은 지급을 세운 것 1·0·0. 사실 절이 아무것도 말해 주지 않는 고장(등급·퇴원일 하나씩 어긋남, 면책 미적용) 49건에서는 서류의 줄을 읽어 48건을 세움 | [CLAIMS.md](docs/CLAIMS.md#지급-직전-모델-감사-2026-10-23) · [claims-audit.json](docs/evaluation/claims-audit.json) |
| 사용자 테스트 2명(지인: 비전문가 1, 생산기술 현업 1), 과제 5개와 30분 대본 (2026-10-24) | 비전문가는 과제 4/5 완료. 막힌 곳은 남은 조건이 도번 찾기 결과에 붙은 것, 검색 제외 이유를 치수로 오해한 것, 도면 패널이 무엇인지 모른 것 셋이고 모두 화면을 고침. 현업자가 실제로 치는 검색어 10개에 치수 조건은 0개였고 "브라켓" 표기를 패턴이 못 읽던 것을 찾아 고침. 공차·승인 이력·BOM 같은 요구는 합성 도면에 없어 한계로 남김 | [USER-TEST.md](docs/USER-TEST.md#결과--2026-10-24-첫-두-사람) · [user-test/](docs/user-test/) |
| 판정 프롬프트 네 가지 비교 (dev 6개 family 36건에서 고르고 test 4개 family에서 한 번 확인) | Claude Haiku 4.5는 모두 36/36으로 천장, Qwen3-1.7B는 기준 27/36이 최고이고 나머지는 21, 21, 10/36. 정교한 프롬프트의 이득은 어느 쪽에서도 확인하지 못함 | [JOB_FIT_AUDIT.md](docs/JOB_FIT_AUDIT.md#프롬프트-비교-결과) |
| 실제 기계 도면 12장(이미지)의 표제란 OCR | 도번 10/12, 재질 4/8, 보고한 15건 모두 정답, 읽은 값은 "확인 필요"로만 사용 | [TITLE-BLOCK.md](docs/TITLE-BLOCK.md) |
| 표제란 OCR을 처음 보는 도면으로: 첫 홀드아웃 27장(읽기 코드를 고치기 전), 고친 뒤 새로 뽑은 34장(시드 20261025). 정답 라벨은 Claude 하위 에이전트가 적었고, **두 세트 모두 사람이 하나씩 확인해 모두 맞음** | 첫 27장은 도번 4/14, 재질 3/16, 틀린 값 1건. 코드를 고친 뒤 새 34장에서 도번 4/16 → 8/16, 재질 3/17 → 8/17로 맞게 읽은 것은 두 배가 됐지만 **틀린 값은 1건 → 4건**(이탤릭 글꼴의 J/I 혼동 등)이고, 개선은 대부분 한 교재의 한 양식(합비공대)에서 나왔고 다른 양식은 그대로. 표본이 작아 구간이 넓음(도번 8/16은 95% 구간 28~72%) | [TITLE-BLOCK.md](docs/TITLE-BLOCK.md#읽기-코드를-고친-뒤-새-도면으로-한-번-더-2026-10-24) · [title-block-holdout2.json](docs/evaluation/title-block-holdout2.json) |
| 같은 61장(사람이 확인한 라벨)을 Claude Haiku 4.5가 읽기: 쪽 전체, 그리고 모델이 찾은 표제란을 원본에서 잘라 한 번 더 ($0.478) | 쪽 읽기 도번 22/30, 재질 23/33, 비례 32/38(Tesseract 12/30, 11/33, 0/38). 틀린 값 9와 칸이 없는데 읽은 값 7(Tesseract 5와 1): 점 빠진 도번, 비어 있는 비례 칸에 뷰 배율. 두 읽기가 같은 값을 낸 17개는 모두 정답(결과를 본 뒤 센 것). 읽은 값은 그대로 "확인 필요" | [TITLE-BLOCK.md](docs/TITLE-BLOCK.md#같은-61장을-claude가-읽으면-2026-10-25) |
| VLM 이미지 18회 | 15회 구조화 완료, 23/36 필드 정답 | [VISION.md](docs/VISION.md) |
| 건당 모델 비용과 지연 (Claude Haiku 4.5, 커밋된 측정 기록에서 `scripts/cost-table.py`로 다시 계산, 새 호출 없음) | 청구 한 건의 실행 $0.0043·중앙값 3.3초(스캔 25.5초, 모델 시간만이 아니라 검증·기록 포함), 지급 직전 감사 한 건 $0.003·0.8초(사실 절이 빈 고장 $0.0038·2.4초), 검색 질의 읽기 한 문장 $0.0021·1.5초, 에이전트 실행 한 번 $0.005~0.0097 | [docs/COST.md](docs/COST.md) |
| 누적 API 비용 | $0.255248, 미확정 예약 0. 프롬프트 비교 실험의 Claude 호출 $0.34355와 실제 모델 경로 측정 네 번의 $0.402162, 청구 주입 판의 모델 경로 세 묶음 $0.169556, 지급 직전 모델 감사 일곱 묶음 $0.92333, test 60건 모델 추출 네 묶음 $1.014931, 검색 질의 모델 읽기 열한 묶음 $0.837106, 명령 검색 도구 세 묶음 $0.228231, 표제란 VLM 네 번 $0.478174, Upstage 정보 추출 세 단계 298쪽의 어림 $11.92(벤더 콘솔 청구액 미확인)은 이 합계에 넣지 않았고 [prompts-claude.json](docs/evaluation/prompts-claude.json)·[agent-trajectory.json](docs/evaluation/agent-trajectory.json)·[claims-injection-model.json](docs/evaluation/claims-injection-model.json)·[claims-audit.json](docs/evaluation/claims-audit.json)·[claims-model.json](docs/evaluation/claims-model.json)·[query-understanding.json](docs/evaluation/query-understanding.json)·[shell-search-model.json](docs/evaluation/shell-search-model.json)·[title-block-vlm.json](docs/evaluation/title-block-vlm.json)에 따로 있음 | [EVIDENCE.md](docs/EVIDENCE.md) |
| 위 수치들의 95% 구간과 pass^k | 72/72는 94.9%부터, 0/120 잘못 지급은 3.1% 아래, 복합 검색 Recall@5 33.3%는 17~49%, 18/18 실행은 61%부터. 측정을 다시 하지 않고 커밋된 기록에서 계산 | [evaluation/README](docs/evaluation/README.md#표본-크기와-95-구간--2026-10-23) · [statistics.json](docs/evaluation/statistics.json) |
| 현재 브랜치 자동 검증 | Python 448개(서로 다른 테스트 함수 371개, 파라미터로 늘어난 사례 77개), Playwright 70개(시나리오 35개 × 데스크톱·모바일), API 없는 평가 게이트, 알림 규칙의 promtool 테스트 통과. 무엇을 막는 테스트인지는 묶음별 표로 정리 | [CI](.github/workflows/ci.yml) · [TESTING.md](docs/TESTING.md) |
| 동시 방문자 부하 시험 (로컬 한 기계, 규칙 provider, 방문자마다 체험 공간 열기와 기본 검사 두 번, 2026-10-25) | 150명까지 5xx 0건, 150명 단계의 50명은 시간당 공간 한도로 429. 검사 한 번 p50은 50명 2.1초, 100명 12.4초이고 대부분 큐 대기. 처음에는 50명 7.5초·100명 44.6초였고, 끝난 실행이 디스패처를 깨우지 않던 것을 고쳐 줄임. 100명에서는 API 프로세스 하나가 CPU 한 코어를 다 씀. 공개 서버(512 MB)에서는 재지 않음 | [docs/LOAD.md](docs/LOAD.md) |

## 구현된 기능

- CadQuery 기반 30개 family / 180개 합성 PDF·STEP·PNG·3D mesh
- **보험금 청구 자동 심사**(두 번째 워크플로): 합성 진단서·입퇴원확인서·수술확인서·영수증에서 값과 위치를 읽고, 가상의 지급 기준표를 규칙 ID와 함께 적용해 지급·부지급·담당자 확인으로 나누며, 멱등 원장으로 한 번만 지급합니다. 120건 평가에서 잘못 지급 0건, 자동 처리율 88.5%. 텍스트 층이 없는 스캔은 Tesseract로 읽고, 읽기가 불확실하면 지급하지 않고 담당자에게 넘기며 스캔에 근거한 부지급은 자동 확정하지 않습니다(시뮬레이션 스캔이며 실제 문서가 아닙니다). 비고란의 지시문은 어느 필드에도 닿지 않고, 비고 안의 가짜 라벨은 값이 되지 못하며, 한 서류가 한 필드에 두 값을 말하면 담당자에게 갑니다. 같은 판을 실제 모델의 추출 경로로 돌리니 가짜 라벨이 모델의 제안은 바꿨지만 grounding이 그 값을 버렸고, 그런 경우도 담당자에게 가도록 했습니다. test 60건을 실제 모델의 추출 경로로 돌리니 텍스트 층에서는 규칙 판독기보다 3건 더 맞혔고 스캔에서는 1건 덜 맞히며 잘못 부지급 1건이 나와 규칙 경로의 보호를 옮겨 넣었습니다. 지급 직전에 모델이 결정을 한 번 더 읽고 담당자에게 보내는 감사를 선택 사항으로 두었고, 약해진 규칙이 통과시킨 잘못된 지급 19건을 모두 세우고 옳은 지급 46건은 그대로 두었습니다 ([docs/CLAIMS.md](docs/CLAIMS.md)). 같은 스캔 서식 98쪽을 상용 문서 AI(Upstage Information Extraction)에 보내 같은 기준으로 채점하니 필드 446개를 모두 맞혔고, 격자 판독기는 431개였습니다
- 체험 작업 공간은 브래킷·플랜지·샤프트·하우징 4종 20개 도면을 불러옵니다. 검색이 돌려준 후보만 판정하고 나머지는 "검색 제외"로 접어 보여 주며, 아무것도 맞지 않으면 그 사실을 알립니다
- 도번·BM25와 해시 고정 E5·OpenCLIP/pgvector 색인·검색·worker 연결. 도번은 공백·밑줄·O/0·시리즈 변형과 한 글자 오타까지 정규화해 찾습니다. 두 번째 이미지 인코더(DINOv2)를 같은 색인에 선택 채널로 둘 수 있고, 모델은 해시로 고정합니다. 질의의 치수 조건은 패턴이 읽고, 선택하면 모델이 질의를 읽어 종류·치수 구간·재질·제외 재질을 제안하되 질의에서 따온 인용이 없는 제안은 버립니다(test 60문장에서 패턴 12, 모델 60; 복합 검색 Recall@5 59.3% → 76.5%, 읽은 종류를 후보 풀과 정렬에 쓰면 89.0%)
- PDF 실제 위치를 근거로 조건 일치·불일치·확인 필요 판정
- LangGraph + PostgreSQL checkpoint, 실제 worker 종료 후 복구, 일시 오류의 지수 백오프 재시도와 보류함(dead-letter)
- 미리 데워 둔 worker 프로세스 풀: 실행마다 인터프리터와 LangGraph import를 다시 치르지 않아 큐 등록 → worker 시작이 로컬 1.65초 → 0.16초
- 근거가 부족하면 담당자에게 묻고 멈추는 `waiting_input`, 답변을 받아 checkpoint에서 이어가는 resume API. 청구는 금액에 따른 승인 구간을 두어, 상급 검토 건은 사유와 서로 다른 두 담당자의 승인이 있어야 지급하고, 응답 기한을 넘기면 대신 결정하지 않고 상급 검토로 올립니다 ([CLAIMS.md](docs/CLAIMS.md))
- 도면 40 → 42mm 개정 시 기존 결과 무효화 및 재검증
- 방문자별 세션·RLS, typed tools, OpenAI·Claude 어댑터, 실행 예산
- 에이전트가 스스로 정의해 재사용하는 저장 검색 도구(`define_search_tool` / `run_saved_search`), 테넌트별 격리·개수 제한
- 실행·이벤트 테이블에서 계산하는 Prometheus 지표, 실행별 span tree(`/api/runs/{id}/trace`), 환경 변수 하나로 켜는 OpenTelemetry(OTLP) 전송. Jaeger에서 중단 후 복구된 실행까지 한 trace로 확인했습니다 ([docs/OBSERVABILITY.md](docs/OBSERVABILITY.md)). 내보내는 span은 OTel GenAI 의미 규약의 이름(`gen_ai.operation.name`, `gen_ai.agent.name`, `gen_ai.tool.name`, `gen_ai.usage.*`)을 달고 schema URL을 고정합니다
- Prometheus 알림 규칙 13개와 기록 규칙 14개, Grafana 대시보드(`deploy/prometheus`, `deploy/grafana`). SLO는 끝난 실행의 99%가 완료이고, 큐 정지·보류함·실패 소진·모델 오류·비용·지연·5xx를 봅니다. promtool 단위 테스트가 알림마다 울릴 때와 조용할 때를 확인하고, 실제 Prometheus 3.5.0이 로컬 앱을 긁어 큐 정지 알림이 울리는 것을 보았습니다. 받는 곳(Alertmanager)은 없고 실제로 받은 알림도, 공개 데모를 긁는 Prometheus도 없습니다 ([docs/OBSERVABILITY.md](docs/OBSERVABILITY.md#알림-규칙과-slo))
- 바깥 연결 없이 도는 범위(규칙 경로, 로컬 Tesseract, 미리 옮긴 인코더 가중치, 같은 망의 Jaeger)와 켜는 순서는 [docs/ONPREM.md](docs/ONPREM.md)에 적었습니다. 실제 폐쇄망에서 돌려 본 기록은 없습니다
- React·Three.js 도면 검토대: 후보 필터, 근거 위치, 실제 CAD 3D, 개정판·실행 기록 검사, 담당자 확인 입력
- LLM 실험실: Qwen/Claude 모델 선택, 오류 사례·반복 실행별 원시 출력, 실제 Agent 호출 기록, 평가 프로토콜
- 도구로 조회한 사실만 판정에 반영하는 Agent, 별도 탐색/반례 검토 단계, 가용 도구·호출·토큰·비용 제한과 bounded retry
- [재현 가능한 LLM 평가](docs/evaluation/README.md)와 API 없이 매 커밋 베이스라인과 비교하는 [평가 게이트](docs/EVALUATION-GATE.md). 결과뿐 아니라 실행이 지난 경로(노드 순서, 지급 전 반례 검토, 담당자 응답 한 번 적용, 재실행 시 원장 불변)도 커밋된 기준 경로와 대조합니다. 실제 모델이 고르는 경로는 상한을 둔 수동 workflow로 재고, 틀린 판정을 조회 안 함·필드 누락·미완료로 나눠 고친 기록을 남깁니다

기본 체험은 명시적인 규칙 기반 엔진이며 API 호출을 흉내 내지 않습니다. 공개 익명 API는 키가 설정되어 있어도 유료 모델 실행을 거부합니다.

## 실행 흐름 한눈에

```
POST /api/runs ──▶ queued ──▶ running ──▶ completed ──(개정)──▶ stale
                                │  │
                 일시 오류 ◀────┘  └────▶ waiting_input ◀── 근거 부족 + review=on_unknown
                 retry_wait ─▶ running        │ POST /api/runs/{id}/resume
                 (5·10·20…초, 3회)             ▼
                 소진 시 failed(dead_lettered)  running ─▶ completed
```

상태 전이, 재시도 규칙, 담당자 입력 계약, 지표·trace, 저장 도구의 경계는 [docs/WORKFLOW.md](docs/WORKFLOW.md)에 있습니다.

[공고 대조·미완료 항목](docs/JOB_FIT_AUDIT.md) · [독립 리뷰와 수정 기록](docs/REVIEW.md) · [배포 상태](docs/DEPLOYMENT.md) · [운영자 모델 실행](docs/OPERATOR.md) · [검색 실험](docs/RETRIEVAL.md)

## 로컬 실행

Python 3.12 이상, Node.js, PostgreSQL 16 + pgvector가 필요합니다. 개발 의존성 `pgserver`(테스트용 내장 PostgreSQL)는 Python 3.12까지만 wheel이 있어 3.13에서는 설치되지 않으며, 그 경우 `FITWITNESS_DATABASE_URL`로 외부 PostgreSQL을 지정합니다.

```bash
uv sync --extra dev
export PYTHONPATH=src
export FITWITNESS_DATABASE_URL='postgresql://USER:PASSWORD@localhost:5432/fitwitness'
uv run python -m fitwitness.data.generate
uv run python -m fitwitness.claims.synth var/claims 120
uv run python scripts/setup.py
npm ci --prefix web
npm run build --prefix web
uv run uvicorn fitwitness.api.app:create_app --factory --host 0.0.0.0 --port 8787
```

`http://localhost:8787`에서 조건 검증, 원본 근거, 개정판 적용, worker 복구, 담당자 확인 흐름과 "청구 심사" 탭의 보험금 자동 심사를 체험합니다. DB 초기화 계정에는 role/extension 생성 권한이 필요합니다.

## 검증

```bash
uv run pytest -q
uv run python -m fitwitness.evaluation.gate --output artifacts/gate --baseline docs/evaluation/gate-baseline.json
cd web
npx playwright install chromium
PLAYWRIGHT_BASE_URL=http://localhost:8787 npm run test:e2e
```

운영자 실험은 격리된 CLI 환경에 API 키, 정확한 모델 ID, 현재 단가를 설정해야 합니다. 라이브 실험 미측정을 성공으로 보고하지 않습니다.

설계·구현 계획은 `docs/superpowers/`에 있습니다. 생산 준비 완료 상태는 아닙니다. 일반 스캔 도면 OCR, 최소 의존성 재검증, 실제 산업 데이터 성능은 별도 과제입니다.

## 라이선스

직접 작성한 코드는 Apache-2.0입니다. 의존성·모델·글꼴에는 각 원저작자의 라이선스가 적용됩니다.
