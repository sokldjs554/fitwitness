# 지나간 측정 workflow (보존용)

여기 있는 다섯 파일은 2026-10-04~06에 유료 모델 측정을 한 번씩 돌린 GitHub Actions workflow입니다. 당시에는 작업 브랜치 `feat/fitwitness-foundation`에 표식 문구가 든 커밋을 푸시하면 돌게 되어 있었습니다. 그 브랜치는 `main`에 합쳐지고 사라졌으므로, `.github/workflows/`에 두면 Actions 탭에 영영 돌지 않는 workflow로 보여 이 폴더로 옮겼습니다.

- 실행 기록(실행 id, 코드 SHA, 비용, 원시 출력)은 [../README.md](../README.md)와 [../EVIDENCE.md](../../EVIDENCE.md)가 가리키는 `docs/evaluation/` 아래 JSON에 있고, 이 파일들은 "그때 무엇을 어떤 상한으로 돌렸나"를 보여 주는 설정 기록입니다.
- 다시 쓰려면 `.github/workflows/`로 되돌리고 `on:`을 `workflow_dispatch`로 바꾼 뒤 상한 입력을 확인하세요. 지금 쓰는 수동 workflow는 [운영자 문서](../../OPERATOR.md)의 표에 있습니다.
