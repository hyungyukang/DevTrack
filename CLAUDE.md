# DevTrack 개발 가이드 (이 저장소에서 작업하는 에이전트용)

AI 에이전트의 작업 진행/문제/결과물을 기록하는 CLI(`devtrack/`)와, 이를 보여주고 승인받는 웹(`web/`)으로 구성된다.
사용자 문서는 README.md(영어 — GitHub 첫 화면) · README.ko.md(한국어판 — README.md 를 고치면 함께 고친다)와 web/guide.md(웹의 `사용 방법` 화면 — 기능을 바꾸면 함께 갱신) · web/guide.en.md(영어 화면의 같은 문서 —
guide.md 를 고치면 같은 절 번호로 함께 고친다), 에이전트 기록 규칙은 docs/AGENT_PROTOCOL.md.
README 맨 위 그림 `docs/images/dashboard.png`(영어 화면) · `dashboard.ko.png`(한국어 화면)는 데모 대시보드에 번호 표시를 얹은 캡처다 — 대시보드 모양이 크게 바뀌면 다시 찍는다.

## 반드시 지킬 제약
- **Python 표준 라이브러리만** (Python 3.7+). HPC(슈퍼컴퓨터) 같은 공유 서버에서 pip 설치 없이 돌아가야 한다. f-string 대신 `.format` 을 써 온 기존 스타일 유지.
- **웹은 빌드 단계 없음** — 순수 ES 모듈 + `web/vendor/` 의 고정 버전 라이브러리. npm/번들러 도입 금지.
- **정적 호스팅 호환** — 웹은 `data/` 의 JSON/파일을 읽기만 한다. URL 은 항상 상대경로(`data/...`, `api/...`)로 써서 하위 경로(`~/www/devtrack/`)에서도 동작해야 한다. 쓰기 기능은 `api/health` 가 live 일 때만, 정적 모드에서는 Claude 에게 보낼 메시지/명령을 보여준다.
- **보안** — 화면에 넣는 모든 동적 값은 `html`` ` 템플릿(자동 이스케이프)으로, markdown 은 `renderMarkdown()`(DOMPurify)으로만 넣는다. HTML 결과물은 sandbox iframe.
- **커밋은 승인 후에만** — `apply_approval` 의 fingerprint 검사, 브랜치 검사를 약화시키지 말 것.
- **삭제는 확인 후에만** — `devtrack delete` 의 ID 재입력 확인, 서버 `api/tasks/<T>/delete` 의 `confirm` 검사를 약화시키지 말 것. 지운 ID 는 `.deleted.json` 으로 재사용을 막는다.
- UI 문구는 한국어. 화면 틀 문구(메뉴 · 머리말 · 버튼 · 라벨 · 안내)는 `L('한국어')` 로 감싸고 `web/js/i18n.js` 의 EN 표에 영어를 넣는다
  (영어 화면 전환). 같은 한국어가 자리마다 다른 영어가 되면 `L('취소§상태')` 처럼 `§` 뒤에 쓰임새를 붙인다.
  사용 방법 문서는 영어판 `web/guide.en.md` 를 따로 둔다 (절 번호 = 목차 ID `#/guide/s5` 가 두 판에서 같아야 한다).
  작업 내용 · 결과물 · AI 에게 보내는 메시지(`[DevTrack 결정]` 등)는 번역하지 않는다.

## 구조
- `devtrack/store.py` 데이터 모델과 모든 쓰기 (잠금 + 원자적 쓰기, 저장 시 `index.json` 재생성, `DEVTRACK_PUBLISH_DIR` 동기화)
- `devtrack/cli.py` argparse CLI, `devtrack/server.py` 로컬 서버(정적 + POST API), `devtrack/gitops.py` diff/commit/push, `devtrack/demo.py` 예시 데이터
- 담당 AI: 작업의 `agent` (`누구나` = 미정). `Store.inbox(agent=, task=)` 는 그 AI 의 입력만, `update_task(claim=)` 는 시작할 때 담당 확인.
  `devtrack watch` (cli.py) 는 Codex 등 대기 모드가 없는 AI 의 실행기 — 입력마다 `codex exec`/명령을 작업별로 실행
- 취소 · 완료 처리 전달: 작업 기록 명령(`TASK_WORK_CMDS`)이 `.agent/sessions/<세션>.json` 에 세션 ↔ 작업을 적고 (`CLAUDE_CODE_SESSION_ID` ·
  `CODEX_THREAD_ID`), `devtrack hook`(PreToolUse)이 그 세션 작업의 미확인 취소 · 삭제 · 사용자 완료 처리(`Store.stop_notice`)를 보면 도구 실행을 막는다.
  훅은 어떤 오류에도 통과(fail-open)해야 한다 — 종료 코드 2 는 AI 의 모든 도구 실행을 막으므로 훅에는 항상 0 으로 끝나는
  `bin/devtrack-hook` 을 건다. 확인 전 기록 명령은 `_guard_task` 가 거절
- 기다리는 AI 표시: `inbox --wait` · `watch` 가 `_Listening` 으로 `.agent/listeners/<kind>-<agent>-<pid>.json` 을 남기고(30초 갱신),
  `Store.listeners()` 가 살아 있는 것만(같은 컴퓨터는 PID, 다른 컴퓨터는 120초) 돌려준다. 서버 `api/agents` → 웹 `waitingBox`.
  `seen_tasks` 는 그 프로세스가 이미 본 새 작업 — 다시 깨어나지 않으므로 '이미 확인함'으로 표시
- 작업별 AI 모델: `devtrack/models.py` — 작업의 `model` · `effort`, 이름 정리(`normalize_model`), 맞는지(`spec_matches`),
  고를 수 있는 목록(`catalog()` → `index.json` 의 `models`: Claude 목록 + Codex 의 `~/.codex/models_cache.json`, 웹은 `setModelCatalog`)과
  모델별 강도(`allowed_efforts` · 웹 `effortsFor`, 저장할 때 `check_effort`),
  지금 세션의 모델 · 강도(`session_model`: `$DEVTRACK_MODEL` > Claude Code · Codex 세션 기록 > `$CLAUDE_EFFORT`).
  `inbox` 는 다른 모델의 작업을 `[다른 모델 지정]` 으로 빼고 `start` 는 거절(`--any-model`), `watch --claude` · `--codex` 는 그 모델로 실행
  (`.agent/watch-sessions-<AI>.json` 으로 같은 작업을 같은 대화로 이어 감 — Claude `--resume`, Codex `exec resume` + `-c` 샌드박스 설정). 웹은 `util.js` 의 `specMatches` 로 기다리는 AI 를
  거른다 — 이름 · 맞는지 규칙은 두 쪽을 함께 고칠 것
- AI 끼리 리뷰 반복: 개발 작업의 `review_loop`(리뷰어 · 횟수 · 차례별 `history` — diff 결과물 · 트리 · 판정), 리뷰 작업의 `review_of`.
  `Store.review_request` (첫 차례에 리뷰 작업 생성, 다음 차례는 `[AI 메시지]`) · `review_done` (판정 → 개발 AI 에게 `[AI 메시지]`) ·
  `_commit_review_check` (통과한 트리와 같을 때만 커밋 요청, `--skip-review`). AI 메시지는 로그 kind `agent` + `ack: False` → inbox `agent_message`.
  같은 AI · 같은 모델의 리뷰도 되지만 개발한 대화는 못 맡는다: cli `_own_review` (세션 기록 `Store.task_sessions`) 가 inbox 에서 빼고 `_guard_task` 가 거절,
  `--same-session` 은 사용자 허락
- 논문 · 문서 심사 반복: `review_loop.kind == "doc"` (논문 작업 · `--doc`). `devtrack/manuscript.py` 가 원고를 떠 둠 (LaTeX `\input` · `.bib` ·
  Word → Markdown · PDF → pdftotext) → `Store._doc_review_request` 가 심사 작업(`docreview`)에 원고 · 지난 심사 이후 diff · 답변서를 붙이고
  차례마다 `.snapshots/round<n>.json` 을 남김. 판정은 `DOC_VERDICTS` (게재 가능이면 통과, `rework` = 게재 불가 → `rejected`), 커밋은 막지 않음
- diff 범위: `gitops.capture_changes` 가 빌드 결과물(CMake 빌드 폴더 · CMakeFiles · 오브젝트 · 실행 파일 등)을 빼고 `paths` · `excluded` 를 돌려준다.
  커밋(`commit_and_push(paths=)`)도 그 파일만. `snapshot_tree` 는 임시 index 로 트리를 만들어 차례 사이 diff · 리뷰 후 변경 확인에 쓴다 (사용자 index 는 건드리지 않음)
- `devtrack/latex.py` LaTeX 컴파일 · 로그 분석 (`Store.record_build` 로 기록)
- `devtrack/pdf.py` Markdown → PDF (web/vendor 렌더러 + 헤드리스 Chrome 인쇄, `review --pdf` · `devtrack pdf`)
- `devtrack/docx.py` Word(.docx) → Markdown (zipfile + ElementTree). `.docx` 결과물을 올리면 `preview`(웹 미리보기)를 만들고, `devtrack text` 가 쓴다
- 문서 리뷰(`docreview`): 원본은 결과물 분류 `source`, 리뷰 결과는 코드 리뷰와 같은 `t["review"]` 에 `doc` · `checks` · `strengths`, 항목의 `location` · `quote` · `suggestion`.
  `verdict` 는 `DOC_VERDICTS`. 웹 `viewDocReview` · `docReviewPanel`, 코드 리뷰 화면은 `codeReviews()` 로 문서 리뷰를 뺀다
- 코드 리뷰: `Store.set_review`(검토 항목 R1… 은 같은 ID 끼리 합침) · 승인 요청의 `choices`/`selected`(웹 체크박스 선택). `integrations/claude-skills/` 는 이를 쓰는 `/review-pr` · `/review-doc` 스킬 (사용자가 `~/.claude/skills` 로 복사해 설치)
- `web/js/app.js` 라우팅 · 폴링(10초) · 알림 · 모든 화면, `web/js/viewers.js` 결과물 뷰어, `web/js/latex.js` LaTeX → 문서 미리보기, `web/js/util.js` 템플릿/아이콘/라벨,
  `web/js/i18n.js` 화면 틀 한/영 전환(`L()`, 라벨 표는 `translated()`, `index.html` 은 `data-i18n`)

## 확인 방법
```bash
bin/devtrack --data demo-data demo --force && bin/devtrack --data demo-data serve   # http://localhost:8765
```
- Python 코드를 바꾸면 실행 중인 `serve` 를 재시작해야 반영된다 (웹 파일은 새로고침만).
- 화면 변경 후: 주요 경로(`#/`, `#/tasks`, `#/task/T-0006/approvals`, `#/code`, `#/reviews`, `#/task/T-0004`, `#/papers`, `#/docreview`, `#/analysis`, `#/brainstorm`, `#/artifacts`, `#/activity`, `#/new`, `#/guide`)를 라이트/다크, 390px 폭에서 확인하고 콘솔 오류가 없는지 본다.
  화면은 처음 열면 영어다 (`i18n.js` — 고른 적이 없으면 en). 문구를 바꿨으면 영어 화면과 한국어 화면(상단 `한`)을 모두 보고,
  EN 표에 빠진 문구가 없는지 확인한다.
- 정적 모드 확인: `bin/devtrack --data demo-data publish --to /tmp/x/devtrack` 후 `python3 -m http.server` 로 열기.
- CLI 변경 후: 임시 git 저장소로 `new → request --commit → decide → apply` 흐름을 돌려 본다.
- 담당 AI · watch 변경 후: 임시 데이터에 담당이 다른 작업을 만들고 `--as Codex inbox` · `start` 를 확인, 가짜 AI 스크립트로
  `devtrack watch --agent Codex --once -- ./fake.sh` 를 돌려 본다 (실제 codex 호출은 필요할 때만).
- 훅 변경 후: 임시 데이터에서 `DEVTRACK_SESSION=s1 devtrack start` → 사용자 취소(`Store.cancel_task(author=USER)`) →
  `echo '{"session_id":"s1","tool_input":{"command":"ls"}}' | devtrack hook` 이 deny, devtrack 단독 명령은 통과하는지 본다
  (실제 Claude 는 `claude -p --settings <훅 설정>` 으로 필요할 때만).
- 모델 지정 변경 후: 임시 데이터에 모델을 정한 작업을 만들고 `DEVTRACK_MODEL=… DEVTRACK_EFFORT=… devtrack inbox` · `start` 를 확인,
  가짜 claude 스크립트(`DEVTRACK_CLAUDE=…`, `CLAUDE_CONFIG_DIR=…`)로 `watch --claude --once` 의 `--model` · `--effort`,
  `--session-id` → 다음 입력 `--resume` 을 본다. Codex 는 가짜 codex(`DEVTRACK_CODEX=…`, `CODEX_HOME=…` 에 rollout 파일,
  `--json` 출력에 `thread.started`)로 `exec --json` → 다음 입력 `exec resume <ID>` + `-c` 샌드박스 설정, 실패 시 새 대화를 본다
  (실제 claude · codex 호출은 필요할 때만).
- 리뷰 반복 · diff 범위 변경 후: 임시 git 저장소에 CMake 빌드 폴더(`build/CMakeCache.txt` · `CMakeFiles/` · 실행 파일)와 소스 변경을 만들고
  `new --review-by Codex` → `--as Claude review-request` → `--as Codex review --findings` · `review-done --verdict changes` → `--as Claude inbox` 의 `[AI 메시지]` →
  수정 후 2차 → 통과 → `request --commit` (리뷰 전 · 통과 후 변경은 거절) → `apply` 의 커밋에 소스만 들어가는지 본다.
  가짜 AI 두 개를 `watch --agent Claude -- dev.sh` · `watch --agent Codex -- rev.sh` 로 켜 두면 반복이 저절로 도는지 볼 수 있다.
  논문 심사는 임시 폴더에 `\input` 이 있는 LaTeX 원고를 두고 `new -t paper --review-by Codex --review-doc main.tex` → `review-request` →
  `review --findings` · `review-done --verdict major` → 원고 수정 → `--response` 없이 거절 · 함께 2차 → `지난 심사 이후 바뀐 부분` diff 를 본다.
  같은 AI 리뷰는 `DEVTRACK_SESSION=s1` 로 개발 · 요청한 뒤 s1 의 inbox (`[다른 대화가 리뷰]`) · `start` 거절 · `inbox --wait` 안 깨어남, s2 는 처리되는지 본다.
- 기다리는 AI 표시 변경 후: 데모 데이터에 담당별 대기 작업을 만들고 `inbox --wait` · `watch --agent Codex -- ./fake.sh` 를
  백그라운드로 켠 채 `api/agents` 와 대기 작업 화면(기다리는 중 / 없음 / 이미 확인함)을 보고, 끈 뒤 다음 폴링에 바뀌는지 본다.
