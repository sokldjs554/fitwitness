# 외부 CAD 형상 실측 — NIST AP203 → AP242

2026-10-05 GitHub Actions run `37313290927`에서 NIST 공개 PMI STEP 검증 모델을 고정 SHA로 내려받아 측정했습니다.

## 프로토콜
- 원본 ZIP SHA-256: `1fb91bb8ff0fe02032b948fda0775bc74591cd0bebc0988347d32574e5884f90`
- CTC01~05 + FTC06~11, 총 11개 설계 쌍을 사전 고정했습니다.
- AP203 geometry-only를 질의로, 같은 ID의 canonical AP242를 정답 후보로 사용했습니다.
- ranking에는 ID 정답을 전달하지 않고 실제 STEP에서 읽은 bbox·volume·surface만 사용했습니다.
- bbox 축은 정렬하고 특징 log-ratio 거리를 고정 가중치 0.30/0.35/0.35로 합쳤습니다.
- AP242가 annotation/void compound를 첫 객체로 노출하는 경우 실제 measurable body를 선택합니다.

## 실측 결과
| 항목 | 결과 |
|---|---:|
| 선택 STEP 파싱 | 22 / 22 |
| Top-1 | 11 / 11 (100%) |
| Top-3 | 11 / 11 (100%) |
| MRR | 1.000 |
| 대응쌍 거리 p50 | 0.01848 |
| 대응쌍 거리 p95 | 0.05291 |
| 파싱 지연 p50 | 768 ms |
| 파싱 지연 p95 | 2,272 ms |

실행 코드 SHA는 `e084c28ba2f1aceb4bee17a56705a207dc0576ca`, 원시 `runs.jsonl` SHA-256은 `9041974fa5b2f6343349553dce446a62b26c2fb57e58532304725ff312b740f1`입니다. Actions artifact SHA-256은 `928cc750811524c91637e43e5ceab5bf03ae4ba3b2ffcea07377112e3e55e6f0`입니다.

## 해석 범위
이 결과는 **외부 engineering CAD benchmark의 서로 다른 STEP 표현에서 같은 geometry를 다시 찾는 실측**입니다. 생산 공장의 실제 도면 분포, PMI 의미 정확도, 공차 해석, 조립 적합성, 전문가 승인 성능으로 확대 해석하지 않습니다. 전체 parse/protocol/report/raw는 Actions run 37313290927의 `nist-cad-37313290927` artifact에 보존했습니다.
