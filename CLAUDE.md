# DevTrack 개발 가이드 (이 저장소에서 작업하는 에이전트용)

AI 에이전트의 작업 진행/문제/결과물을 기록하는 CLI(`devtrack/`)와, 이를 보여주고 승인받는 웹(`web/`)으로 구성된다.
사용자 문서는 README.md(영어 — GitHub 첫 화면) · README.ko.md(한국어판 — README.md 를 고치면 함께 고친다)와 web/guide.md(웹의 `사용 방법` 화면 — 기능을 바꾸면 함께 갱신) · web/guide.en.md(영어 화면의 같은 문서 —
guide.md 를 고치면 같은 절 번호로 함께 고친다), 에이전트 기록 규칙은 docs/AGENT_PROTOCOL.md.
README 맨 위 그림 `docs/images/dashboard.png`(영어 화면) · `dashboard.ko.png`(한국어 화면)는 데모 대시보드에 번호 표시를 얹은 캡처다 — 대시보드 모양이 크게 바뀌면 다시 찍는다.

## 반드시 지킬 제약
- **Python 표준 라이브러리만** (Python 3.7+). HPC(슈퍼컴퓨터) 같은 공유 서버에서 pip 설치 없이 돌아가야 한다. f-string 대신 `.format` 을 써 온 기존 스타일 유지.
- **웹은 빌드 단계 없음** — 순수 ES 모듈 + `web/vendor/` 의 고정 버전 라이브러리. npm/번들러 도입 금지.
- **정적 호스팅 호환** — 웹은 `data/` 의 JSON/파일을 읽기만 한다. URL 은 항상 상대경로(`data/...`, `api/...`)로 써서 하위 경로(`~/www/devtrack/`)에서도 동작해야 한다. 쓰기 기능은 `api/health` 가 live 일 때만, 정적 모드에서는 Claude 에게 보낼 메시지/명령을 보여준다.
  정적 웹 서버는 Cache-Control 을 못 정하는 곳이 많아(OLCF users.nccs.gov 는 `.htaccess` 의 `Header` · `Expires` 가 500 오류) `publish` 가
  index.html 의 화면 파일 주소에 내용 해시(`?v=`)와 js 모듈용 import map 을 넣는다(`store.write_versioned_index`). web/js 에 모듈을 더해도 저절로 들어간다.
- **보안** — 화면에 넣는 모든 동적 값은 `html`` ` 템플릿(자동 이스케이프)으로, markdown 은 `renderMarkdown()`(DOMPurify)으로만 넣는다. HTML 결과물은 sandbox iframe.
- **커밋은 승인 후에만** — `apply_approval` 의 fingerprint 검사, 브랜치 검사를 약화시키지 말 것.
- **삭제는 확인 후에만** — `devtrack delete` 의 ID 재입력 확인, 서버 `api/tasks/<T>/delete` 의 `confirm` 검사를 약화시키지 말 것. 지운 ID 는 `.deleted.json` 으로 재사용을 막는다.
- UI 문구는 한국어. 화면 틀 문구(메뉴 · 머리말 · 버튼 · 라벨 · 안내)는 `L('한국어')` 로 감싸고 `web/js/i18n.js` 의 EN 표에 영어를 넣는다
  (영어 화면 전환). 같은 한국어가 자리마다 다른 영어가 되면 `L('취소§상태')` 처럼 `§` 뒤에 쓰임새를 붙인다.
  사용 방법 문서는 영어판 `web/guide.en.md` 를 따로 둔다 (절 번호 = 목차 ID `#/guide/s5` 가 두 판에서 같아야 한다).
  작업 내용 · 결과물 · AI 에게 보내는 메시지(`[DevTrack 결정]` 등)는 번역하지 않는다.

## 구조
- 기록 위치: `cli.data_location` — `--data` > `$DEVTRACK_DATA` > 설정 파일(`config_path()` = `$XDG_CONFIG_HOME` 또는
  `~/.config/devtrack/config.json`, `devtrack config`) > `<저장소>/data`. 웹 공개 폴더도 같은 순서(`publish_location`, 설정 파일 값은
  설정한 기록 폴더를 쓸 때만). 환경 변수는 그 셸에서 켠 프로세스에만 있으므로(먼저 켠 serve, VS Code 의 AI · 훅) 설정 파일이 기본 수단.
  `_warn_other_data` 가 init · serve · watch · `inbox --wait` 에서 다른 곳의 기록을 알린다 (`--data` 일 때는 조용히)
- `devtrack/store.py` 데이터 모델과 모든 쓰기 (잠금 + 원자적 쓰기, 저장 시 `index.json` 재생성, `DEVTRACK_PUBLISH_DIR` 동기화 —
  기록 폴더가 곧 공개 폴더의 `data/` 이면(`publish_is_root`, 심볼릭 링크 · 다른 경로 이름도 `same_dir` 로 판단) 복사 · 정리를 건너뜀)
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
  차례마다 `.snapshots/round<n>.json` 을 남김. 판정은 `DOC_VERDICTS` (게재 가능이면 통과, `rework` = 게재 불가 → `rejected`), 커밋은 막지 않음.
  `\includegraphics` 그림은 `manuscript.find_graphic`(LaTeX 순서, 심사는 확장자 무관)으로 찾아 `_attach_figures` 가 심사 작업에 붙인다
  (결과물의 `figure` = 원고 폴더 기준 이름, 바뀐 그림은 새 버전, 지문 `digest` 에 그림 포함 — 그림이 없으면 예전 지문 그대로).
  심사 · 답변 기록: `review_done` 이 그 차례의 항목을 `review_rounds[].findings` 로 떠 두고, 답변서는 `response_sections` 가 항목 ID 제목
  (`## R1` · `**R1**`)으로 나눈다 (지난 차례의 열린 항목에 답이 없으면 `review-request` 거절). `_save_review_record` 가 논문 작업의
  `review_response_record.md` 결과물(`review_record`, `review_loop.record`)을 판정 · 답변서마다 새 버전으로 고친다
- 논문 원고 폴더: `tasks/<T>/paper/` (`Store.paper_folder`) — 최신 `.tex` · `.bib` 결과물 + 원고가 부르는 그림(같은 이름 결과물, 이 작업 > 연결된 작업)을
  LaTeX 가 찾는 폴더에. 원고 · 그림 결과물이 바뀌면 `_mark_paper` → `_save` 에서 그 작업과 연결된 논문 작업을 다시 만든다(`_refresh_papers`).
  DevTrack 이 관리하는 폴더라 없는 파일은 지우고, 공개 폴더도 `_mirror_paper` 로 맞춘다. `devtrack paper <T>` 는 다시 만들기 · 빠진 그림 확인
- diff 범위: `gitops.capture_changes` 가 빌드 결과물(CMake 빌드 폴더 · CMakeFiles · 오브젝트 · 실행 파일 등)을 빼고 `paths` · `excluded` 를 돌려준다.
  커밋(`commit_and_push(paths=)`)도 그 파일만. `snapshot_tree` 는 임시 index 로 트리를 만들어 차례 사이 diff · 리뷰 후 변경 확인에 쓴다 (사용자 index 는 건드리지 않음)
- 웹 비밀번호: `devtrack/access.py` (`devtrack password`) — 기록 폴더 `.auth.json`(salt · 반복 · 열쇠의 SHA-1, 비밀번호 · 열쇠는 저장 안 함),
  공개 폴더 `auth.json`(비밀번호 없으면 `password: false`) · `data/.htaccess`(Apache `Require expr` 가 쿠키 `devtrack_key` 의 sha1 확인 —
  OLCF users.nccs.gov 에서 동작 확인, `Header` · `Expires` 와 달리 `Require` 는 허용됨).
  `Store.sync_access` · `publish` 가 맞추고, `server.py` 가 같은 쿠키로 `data/` · `api/` 를 막는다(망가진 설정이면 닫힘). 웹 `lock.js` 가 PBKDF2(WebCrypto)로
  열쇠를 만들어 쿠키(https · localhost 는 `SameSite=None; Secure` — sandbox iframe 의 HTML 결과물이 옆 파일을 부를 때 필요)에 둔다.
  열쇠 계산(`derive_key` ↔ `deriveKey`, NFC)은 두 쪽을 함께 고칠 것. 쿠키 없이 data/ 가 열리면 '보호가 동작하지 않음' 경고
- `devtrack/latex.py` LaTeX 컴파일 · 로그 분석 (`Store.record_build` 로 기록)
- `devtrack/pdf.py` Markdown → PDF (web/vendor 렌더러 + 헤드리스 Chrome 인쇄, `review --pdf` · `devtrack pdf`)
- `devtrack/docx.py` Word(.docx) → Markdown (zipfile + ElementTree). `.docx` 결과물을 올리면 `preview`(웹 미리보기)를 만들고, `devtrack text` 가 쓴다
- 문서 리뷰(`docreview`): 원본은 결과물 분류 `source`, 리뷰 결과는 코드 리뷰와 같은 `t["review"]` 에 `doc` · `checks` · `strengths`, 항목의 `location` · `quote` · `suggestion`.
  `verdict` 는 `DOC_VERDICTS`. 웹 `viewDocReview` · `docReviewPanel`, 코드 리뷰 화면은 `codeReviews()` 로 문서 리뷰를 뺀다
- 인계 메모: `tasks/<T>/notes.md` (`Store.set_note` · `read_note` · `note_template`, 작업의 `note` = 쓴 때 · 쓴 사람 · 글자 수 · 판, 바로 전 판 `notes.prev.md`).
  `devtrack note` 보기 · 쓰기(`-` 표준입력) · `--template` · `--clear`, `show --brief` 는 상태 · 주요 결과물 · 메모만 (다른 작업이 먼저 읽는 것),
  `start` · inbox 새 작업 · `watch` 안내가 관련 작업의 메모를 가리키고, `request` · `done` 은 메모가 없거나 `NOTE_STALE` 건 넘게 지나면 알린다.
  웹 `noteCard`(개요, 실시간은 **고치기** → `api/tasks/<T>/note`, 고치는 글은 `state.noteEdit` 로 다시 그려도 남음)
- GitHub 이슈 우편함: `devtrack/mailbox.py` (`devtrack mailbox`) — 정적 웹 보내기 창의 'GitHub 이슈로 보내기'(app.js `ghIssueBlock`, 정적 모드 ·
  `index.json` 의 `mailbox` 가 있을 때만)가 붙여 넣기 메시지를 본문으로 새 이슈 주소를 연다 (7000자 넘으면 본문은 클립보드, `devtrack-paste-here`).
  `watch` · `inbox --wait` 의 `on_tick` 이 `Poller` 로 `.agent/mailbox-state.json` 의 시각을 보고 한 프로세스만 확인 → `parse_issue` · `parse_comment`
  (붙여 넣기 메시지 `[DevTrack …]` · 제목 `T-0041 …` · `이름: 값` 머리말 · 라벨) → `apply` 가 서버 POST 와 같은 Store 호출. **본인 계정의 글만**,
  삭제는 거절, 새 작업 이슈 = 그 작업의 대화(댓글 → 메시지 · `승인 A-1` …, 승인 요청 🔔, 끝나면 닫음). 우리 답글은 `BOT_MARK` 로 건너뜀(토큰이 사용자 것).
  설정은 기록 폴더 `.mailbox.json`(저장소 · 계정), 토큰은 `~/.config/devtrack/github-token`(0600) · `$DEVTRACK_GITHUB_TOKEN` — 기록 폴더에 두지 말 것.
  웹의 붙여 넣기 메시지 형식을 바꾸면 `mailbox._paste` 도 함께 고칠 것.
  보낸 파일: GitHub 끌어다 놓기 첨부(user-attachments)는 비공개 저장소에서 토큰으로 못 받는다(직접 · `body_html` 모두 404 · 서명 없음 확인).
  그래서 정적 웹 새 작업 · 메시지 창의 **파일 올리기**(`ghFolderId` → `https://github.com/<저장소>/upload/<브랜치>/inbox/<날짜>-<무작위>`)로 우편함 저장소에
  올리고 이슈 본문에 `<!-- devtrack-files: 폴더 -->`(`FOLDER_RE`, 웹 `data-marks`)를 남긴다. `fetch_folder`(Contents API 목록 · raw)가 받아
  `Store.add_sent_files`(분류 `source`, `sent`, 같은 이름은 새 버전)로 올린다. 상태 `folders` 가 새 작업 폴더(표시 없으면 `inbox/issue-<번호>`)는 작업이 끝날 때까지,
  메시지 폴더는 `FOLDER_WAIT` 동안 지켜보고(`_watch_folders` — 브랜치 머리 `branch_head` 가 바뀔 때만 폴더를 봄), 새 파일은 그 작업에 메시지(`meta.attachments`)로.
  글 속 저장소 파일 링크(`…/blob/<브랜치>/<경로>` · raw)도 `fetch_files` 가 받아 `📎 F-n` 으로 바꾼다 (새 작업은 같은 저장에서 지시를 고침 — `sent_files`).
  inbox 항목의 `attachments` · cli `_sent_lines` 가 파일 경로와 `devtrack text` 를 보여 준다. 토큰 권한 Contents: Read (`can_read_files`, 설정 `files` · `branch`,
  `index.json` 의 `mailbox.files` · `branch` — 웹은 `files` 가 false 면 버튼을 숨김)
- 마크다운 리포트 안의 그림 · 링크: 결과물은 `artifacts/F-<n>_<이름>` 으로 저장되므로 `![](fig.png)` 같은 상대 경로는 그대로면 깨진다.
  `renderMarkdown(…, { resolveFile })` (viewers.js `linkLocalFiles`) 가 app.js `resolveFile` 로 같은 이름의 결과물(index.json `filename` = 올릴 때 이름,
  없던 기록은 `_safe_name` 과 같은 `safeName`)에 잇는다 — 이름 정리 규칙은 두 쪽을 함께 고칠 것. `devtrack pdf` 는 `<base>` 를 원본 .md 폴더로 둔다
- 결과물에 대한 메시지: 결과물 화면 맨 아래 `feedbackBox(t, …, { artifact })` → `add_user_comment(artifact=)` 가 로그 `ref` · `meta.artifact`(보낼 때 버전)로 남긴다.
  결과물은 새 버전도 같은 ID 라 `ref` 로 그 결과물의 요청 · 버전 기록(`artifactThread`)이 이어지고, inbox 항목에는 `_comment_artifact` 가 지금 파일과
  새 버전으로 올리는 명령(`artifact --replace` · `review`)을 붙인다. 사용자 메시지의 `ack` 는 웹에 'AI 확인함 / 확인 전' 으로 표시
- 코드 리뷰: `Store.set_review`(검토 항목 R1… 은 같은 ID 끼리 합침) · 승인 요청의 `choices`/`selected`(웹 체크박스 선택). `integrations/claude-skills/` 는 이를 쓰는 `/review-pr` · `/review-doc` 스킬 (사용자가 `~/.claude/skills` 로 복사해 설치)
- 자동 갱신: `poll`(10초 · 탭으로 돌아올 때)이 바뀐 화면을 `render({auto})` 로 다시 그린다. 입력 중(`isBusy` — 입력 칸 포커스 · 누르는 중 ·
  `api` 쓰기 중 · 승인 선택지 · 새 작업 폼)이면 `deferRender` 로 미루고 입력 칸에서 나오면 `applyPending`. 써 두기만 한 글은 같은 화면이면
  `saveDrafts` 로 남기므로, 보낸 뒤에는 칸을 비우고 다시 그릴 것. 결과물 주소는 `artifactUrl` 이 `?v=<버전>` 을 붙인다 (새 버전도 같은 파일 이름)
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
  그림: `\graphicspath{{figures/}}` 와 PNG 그림을 두고 1차에 `그림: …` 결과물이 붙는지, 그림 하나만 고친 2차가 받아지고 메시지에 그 이름이 나오는지,
  아무것도 안 바꾼 3차는 거절되는지 본다. 원고 폴더는 논문 작업에 그림 · `.tex` 를 올리고(연결된 분석 작업의 그림 포함) `devtrack paper` 의
  `확장자를 빼야 할 그림` · `그림 결과물이 없는 그림`, 연결 해제 · 다시 공개 뒤 빠진 그림이 지워지는지 본다.
  심사 · 답변 기록: `severity: question` 이 섞인 항목으로 1차 판정 → 한 항목이 빠진 답변서는 거절 · `**R2.**` 로 더하면 2차 → accept 판정
  메시지에 `-a <기록 ID>` 가 나오는지, 기록의 표(답변 수 · 해결 수)와 항목별 저자 답변을 본다. 데모 T-0005 의 기록도 확인.
  같은 AI 리뷰는 `DEVTRACK_SESSION=s1` 로 개발 · 요청한 뒤 s1 의 inbox (`[다른 대화가 리뷰]`) · `start` 거절 · `inbox --wait` 안 깨어남, s2 는 처리되는지 본다.
- 기록 위치 변경 후: `XDG_CONFIG_HOME=<임시>` 로 실제 `~/.config` 를 건드리지 않고 `config data` · `config publish` →
  환경 변수 없이 `new` 가 그곳에 쓰고 공개 폴더로 반영되는지, 훅(`bin/devtrack-hook`)이 그곳의 취소를 보는지, `$DEVTRACK_DATA` 가 다르면
  `config` 가 경고하는지, `--data` 로 연 폴더에서는 공개 · 경고가 꺼지는지 본다.
- 웹 비밀번호 변경 후: 임시 데이터에 `echo '<비밀번호>' | devtrack --data <임시> password --stdin` → `serve` 에서 curl 로 쿠키 없이 403 ·
  `access.derive_key` 로 만든 쿠키로 200 · POST 403, 웹은 잠금 화면 → 틀린 비밀번호 · 맞는 비밀번호 → 다시 열 때 안 묻는지 · **잠그기**.
  `publish --to` 한 폴더를 `python3 -m http.server`(.htaccess 무시)로 열면 경고가 나오는지. 실제 Apache 는 `~/www` 아래 임시 폴더에 데모를 공개해 보고 지운다.
- 인계 메모 변경 후: 임시 데이터에서 `note --template` → `note <T> -` (표준입력) → `show --brief` · `show` → `--related` 로 만든 대기 작업의 inbox 안내 ·
  `start` 안내 → 메모 없이 `request` · `done` 의 `💡`, 기록 15건 뒤 오래된 메모 알림 → `serve` 의 `api/tasks/<T>/note` 로 사용자가 고친 뒤 AI 가 쓰면 경고 ·
  `notes.prev.md` → `--clear`. 웹은 실시간에서 고치기 → 저장, 정적 화면은 보기만 (390px · 영어 포함).
- GitHub 우편함 변경 후: 가짜 GitHub 서버(`$DEVTRACK_GITHUB_API`, `/user` · `/repos/o/r` · issues · comments · labels · reactions 흉내)와
  `XDG_CONFIG_HOME=<임시>` 로 `echo <토큰> | devtrack --data <임시> mailbox o/r --stdin` → 손으로 쓴 이슈 · 붙여 넣기 형식 · `T-0001 메시지` ·
  `T-0001 A-1 승인` · 삭제(거절) · 다른 계정(건너뜀) · 대화 이슈 댓글(메시지 · `승인 A-1` · `완료`) → `mailbox --check` 와 답글 · 닫힘을 본다.
  웹은 `publish --to` 한 정적 폴더에서 보내기 창의 버튼이 연 주소를 그대로 가짜 서버에 이슈로 넣어 폼 값이 그대로 돌아오는지(왕복), 긴 지시의
  붙여 넣을 자리, 실시간(`serve`)에는 버튼이 없는지 본다. `inbox --wait` 두 개를 함께 켜 이슈가 한 번만 들어오는지도.
  파일 올리기 폴더: 가짜 서버에 폴더 목록(JSON) · raw · `branches/<브랜치>` 머리(파일을 올릴 때마다 바뀜)와 호출 수를 두고, 먼저 올린 폴더 + 표시 있는 새 작업 이슈 →
  나중에 올린 파일(메시지) → 그대로면 폴더를 안 봄 → 같은 이름 다시 올림(새 버전) → 표시 없는 이슈(`inbox/issue-N`, 첫 답글의 올리기 링크) → 메시지 이슈 폴더의
  기한 지남 · 작업 완료 뒤 그만 봄 → 권한 없는 토큰(버튼 · 링크 숨김)을 본다. 웹은 보내기 창의 두 버튼 주소 · 이슈 본문의 폴더 표시(긴 본문일 때도)를 본다.
  보낸 파일: 가짜 서버에 `contents/<경로>?ref=`(raw) 응답과 Contents 권한이 없는 토큰(403)을 두고, 새 작업 이슈에 Word · 슬래시 브랜치 파일 ·
  끌어다 놓기 첨부 · 없는 파일 · 다른 사이트 링크, 붙여 넣기 형식 끝의 링크, 대화 이슈 댓글의 raw 주소, `T-0041 완료` 에 붙인 링크(받지 않음),
  너무 큰 파일(`ATTACH_MAX` 를 낮춰서) → 지시의 `📎 F-n` · 답글 · `--as Claude inbox` 의 `→ 보낸 파일` 줄 · `devtrack text` · 웹 '보낸 파일' 표시를 본다.
- 기다리는 AI 표시 변경 후: 데모 데이터에 담당별 대기 작업을 만들고 `inbox --wait` · `watch --agent Codex -- ./fake.sh` 를
  백그라운드로 켠 채 `api/agents` 와 대기 작업 화면(기다리는 중 / 없음 / 이미 확인함)을 보고, 끈 뒤 다음 폴링에 바뀌는지 본다.
