# 게시용 글

세 편입니다. 모두 저장소의 문서와 커밋에서 숫자를 가져왔고, 각 글 끝의 주석에 출처가 있습니다. 게시는 작성자 본인의 계정이 필요하며, 아직 어디에도 올리지 않았습니다.

| 파일 | 제목 | 한 줄 요약 | 권하는 태그 |
|---|---|---|---|
| [latency-round-trips.md](latency-round-trips.md) | FitWitness 공개 서버의 대기 시간: 데이터베이스 왕복과 worker 기동 | 체험 공간 생성 14.4초가 1.1초가 되기까지, 왕복을 세고 줄인 방법 | PostgreSQL, psycopg, 성능, LangGraph |
| [measuring-my-own-reader.md](measuring-my-own-reader.md) | 청구 서류 읽기에서 제 측정과 리더가 틀렸던 세 곳 | D05.1이 O05.1이 된 사연, 빈 서식이 읽힌 사연, 흔들어 보고 나온 세 오류 | OCR, Tesseract, 문서 AI, 평가 |
| [trajectory-four-runs.md](trajectory-four-runs.md) | 실제 모델이 고르는 경로를 네 번 잰 기록: 실패 2건에서 18/18까지 | 0.40달러로 네 번 돌려 두 번 고친 과정과 그 숫자가 보여 주지 않는 것 | LLM 에이전트, LangGraph, 평가, Claude |

## 올리기 전에

1. 글의 숫자가 지금 문서와 같은지 한 번 더 봅니다. `docs/evaluation/statistics.json`에 구간이 있으니 "72/72"처럼 천장인 수치 옆에는 "94.9%부터"를 붙이는 편이 좋습니다.
2. "코드는 대부분 Claude Code가 썼습니다" 문장은 세 글에 다 있습니다. 본인이 한 일과 맞게 고치세요. 그래프의 노드 구성, 지급 기준표의 규칙 구조, 평가 지표와 임계값, 무엇을 잴지 정한 것이 본인 몫이라면 그렇게 적습니다.
3. velog는 마크다운을 그대로 붙여 넣으면 되고 표와 코드 블록이 그대로 보입니다. 끝의 `<!-- sources -->` 주석은 보이지 않지만, 출처를 본문 끝에 "출처" 절로 풀어 쓰는 편이 읽는 사람에게 낫습니다.
4. 공개 저장소 링크(https://github.com/sokldjs554/fitwitness)와 공개 데모(https://fitwitness.onrender.com/)를 첫 문단 아래에 한 줄로 둡니다.
5. 순서는 세 번째 글(궤적 네 번)부터가 공고와 가장 가깝습니다. 그다음 둘째, 첫째 순입니다.
