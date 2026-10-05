# 도면 이미지 관측 — 실제 Claude 진단

`read_image_region`은 현재 tenant·snapshot의 PNG 영역만 읽습니다. 호출 전후 원본 해시·snapshot을 확인하고 lease·취소·비용 예약을 검사합니다. 잘못된 이미지·10MB/16MP 초과 입력은 호출 전에 거부합니다. `FITWITNESS_VISION=enabled`인 유료 operator graph에서만 도구를 제공합니다. 공개 API는 유료 실행을 거부합니다.

## 실제 결과

[protocol](evaluation/vision-37207929539/protocol.json) · [원시18회](evaluation/vision-37207929539/runs.jsonl.gz) · [실제 graph](evaluation/vision-37207929539/graph.json) · [복구된 보고서](evaluation/vision.json)

이전 dev의 두 family에서 원본·재질 누락·주석 없는 crop을 각각3회 읽었습니다. Claude Haiku4.5의 구조화 출력은15/18 완료, 오류3회를 포함한 너비·재질 판정은23/36(63.9%)였습니다. p50/p95는1.55/1.94초입니다. 주석 없는 crop6회에서는 두 필드를 모두 누락으로 처리했지만, 재질 누락 도면에서 `SYNTHETIC`을 재질로 오인하고 숫자에 단위를 중복 출력한 사례가 있었습니다. 픽셀 bbox를0–1좌표 대신 출력한3회는 schema 오류로 남습니다. 결과를 좋게 만들기 위한 재호출·사후 점수 보정은 하지 않았습니다.

실제 LangGraph는 PDF 추출 사실을 의도적으로 지운 도면1개에서 이미지 모델을1회 호출하고 completed로 종료했습니다. 모델 총3회·도구2회이며 최종 판정은 `unknown`입니다. 관측은 항상 `vlm_observation`·`uncertain`으로 보관하며 단독으로 조건 일치를 확정하지 않습니다. 검증된 PDF 사실이 있으면 우선합니다.

이번 이미지+graph 계산 비용은 **$0.050821**, 이전 실험 포함 누적 **$0.238662**, 미확정 예약0입니다. 승인 누적$1 이내입니다. 계정 청구서 결산액이 아닌 usage×단가입니다.

## 실행·복구 기록

실행 코드3e266236, workflow37207929539의 호출과 graph 기록은 저장됐으나 최종 보고서 생성에서 datetime 직렬화가 실패했습니다. workflow 전체를 성공으로 표시하지 않습니다. 저장된 protocol·JSONL·graph·예산 장부만 읽는 `--summarize-only`로 보고서를 복구했으며 추가 API 호출은 없습니다. artifact ZIP SHA256: `d5f36ab04d18a0a1f3ba93215a7e267f41fa19727d7e9b47cc8589d5bf175a76`.

```bash
uv run python -m fitwitness.evaluation.vision --summarize-only \
  --output docs/evaluation/vision-37207929539 \
  --execution-sha 3e2662368438091463426a668b1acbe06ac9f690
uv run python scripts/verify-research.py
```

앞선37207557656은 실제 PNG를 넣기 전에 가짜 이미지를 저장한 test fixture 오류로 유료 단계 전에 중단됐습니다. 원본의 덮어쓰기 방지는 유지하고 fixture만 수정했습니다.

## 범위

합성 dev PNG6종 진단이며 산업 스캔 OCR·일반화·bbox 위치 정확도 평가가 아닙니다. fractional 영역과 정수 pixel crop 사이에 미세한 좌표 차이가 남습니다. 이미지 도구를 명시적으로 요청한 graph1개이므로 자율 도구 선택의 우위도 주장하지 않습니다. 이미지와 gold는 분리됐고 원본·입력 해시는 이전 보존 archive와 대조했습니다.
