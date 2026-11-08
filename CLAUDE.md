# DevTrack 개발 가이드 (이 저장소에서 작업하는 에이전트용)

AI 에이전트의 작업 진행/문제/결과물을 기록하는 CLI(`devtrack/`)와, 이를 보여주고 승인받는 웹(`web/`)으로 구성된다.
사용자 문서는 README.md 와 web/guide.md(웹의 `사용 방법` 화면 — 기능을 바꾸면 함께 갱신), 에이전트 기록 규칙은 docs/AGENT_PROTOCOL.md.

## 반드시 지킬 제약
- **Python 표준 라이브러리만** (Python 3.7+). Frontier 등 HPC 에서 pip 설치 없이 돌아가야 한다. f-string 대신 `.format` 을 써 온 기존 스타일 유지.
- **웹은 빌드 단계 없음** — 순수 ES 모듈 + `web/vendor/` 의 고정 버전 라이브러리. npm/번들러 도입 금지.
- **정적 호스팅 호환** — 웹은 `data/` 의 JSON/파일을 읽기만 한다. URL 은 항상 상대경로(`data/...`, `api/...`)로 써서 하위 경로(`~/www/devtrack/`)에서도 동작해야 한다. 쓰기 기능은 `api/health` 가 live 일 때만, 정적 모드에서는 Claude 에게 보낼 메시지/명령을 보여준다.
- **보안** — 화면에 넣는 모든 동적 값은 `html`` ` 템플릿(자동 이스케이프)으로, markdown 은 `renderMarkdown()`(DOMPurify)으로만 넣는다. HTML 결과물은 sandbox iframe.
- **커밋은 승인 후에만** — `apply_approval` 의 fingerprint 검사, 브랜치 검사를 약화시키지 말 것.
- **삭제는 확인 후에만** — `devtrack delete` 의 ID 재입력 확인, 서버 `api/tasks/<T>/delete` 의 `confirm` 검사를 약화시키지 말 것. 지운 ID 는 `.deleted.json` 으로 재사용을 막는다.
- UI 문구는 한국어.

## 구조
- `devtrack/store.py` 데이터 모델과 모든 쓰기 (잠금 + 원자적 쓰기, 저장 시 `index.json` 재생성, `DEVTRACK_PUBLISH_DIR` 동기화)
- `devtrack/cli.py` argparse CLI, `devtrack/server.py` 로컬 서버(정적 + POST API), `devtrack/gitops.py` diff/commit/push, `devtrack/demo.py` 예시 데이터
- `devtrack/latex.py` LaTeX 컴파일 · 로그 분석 (`Store.record_build` 로 기록)
- `devtrack/pdf.py` Markdown → PDF (web/vendor 렌더러 + 헤드리스 Chrome 인쇄, `review --pdf` · `devtrack pdf`)
- 코드 리뷰: `Store.set_review`(검토 항목 R1… 은 같은 ID 끼리 합침) · 승인 요청의 `choices`/`selected`(웹 체크박스 선택). `integrations/claude-skills/` 는 이를 쓰는 `/review-pr` 스킬 (사용자가 `~/.claude/skills` 로 복사해 설치)
- `web/js/app.js` 라우팅 · 폴링(10초) · 알림 · 모든 화면, `web/js/viewers.js` 결과물 뷰어, `web/js/latex.js` LaTeX → 문서 미리보기, `web/js/util.js` 템플릿/아이콘/라벨

## 확인 방법
```bash
bin/devtrack --data demo-data demo --force && bin/devtrack --data demo-data serve   # http://localhost:8765
```
- Python 코드를 바꾸면 실행 중인 `serve` 를 재시작해야 반영된다 (웹 파일은 새로고침만).
- 화면 변경 후: 주요 경로(`#/`, `#/tasks`, `#/task/T-0006/approvals`, `#/reviews`, `#/task/T-0004`, `#/papers`, `#/analysis`, `#/artifacts`, `#/activity`, `#/new`, `#/guide`)를 라이트/다크, 390px 폭에서 확인하고 콘솔 오류가 없는지 본다.
- 정적 모드 확인: `bin/devtrack --data demo-data publish --to /tmp/x/devtrack` 후 `python3 -m http.server` 로 열기.
- CLI 변경 후: 임시 git 저장소로 `new → request --commit → decide → apply` 흐름을 돌려 본다.
