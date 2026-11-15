# DevTrack — AI 협업 작업 관리 (AI collaboration hub)

Claude 등 AI 에이전트가 수행하는 작업(코드 개발, 코드 리뷰, 논문/LaTeX, 데이터 분석, 시각화, AI 모델 개발)의
**진행 상황 · 문제점 · 결과물**을 웹에서 확인하고, **승인이 필요한 항목을 알림으로 받아 승인/수정 요청**하는 서비스입니다.
승인된 코드 변경은 에이전트가 대상 저장소에 commit · push 합니다.

```
 사용자 ── 작업 지시 ──▶ AI 에이전트 (Claude, Frontier)
   ▲                         │ devtrack CLI 로 기록
   │                         ▼   (단계 · 문제 · 결과물 · 승인 요청)
   │                 data/  (작업별 JSON + 결과물 파일)
   │                         │
   └── 웹 대시보드 ◀─────────┘   알림 → 승인 / 수정 요청 / 거절
            │
            └─ 승인 ─▶ 에이전트가 devtrack apply ─▶ git commit + push
```

> 처음이라면 → **[사용 방법 (단계별 매뉴얼)](web/guide.md)** — 웹 대시보드 왼쪽 아래 `사용 방법` 메뉴에서도 볼 수 있습니다.
> 웹은 처음 열면 영어 화면입니다. 오른쪽 위 `한` 단추를 누르면 한국어로 바뀌고, 고른 언어는 브라우저에 기억됩니다.
> English: **[DevTrack Guide](web/guide.en.md)** (the web `Guide` page shows it when the display language is English).

## 구성

| 구성 요소 | 설명 |
|---|---|
| `bin/devtrack` | 에이전트용 CLI. Python 3.7+ 표준 라이브러리만 사용 (설치 불필요) |
| `web/` | 대시보드. 빌드 없는 정적 HTML/JS — 그대로 정적 호스팅 가능 |
| `devtrack serve` | 로컬 웹 서버. 웹에서 바로 승인 · 피드백 · 작업 지시 저장 |
| `devtrack publish` | 웹 + 데이터를 정적 디렉토리(예: Frontier `www`)로 배포 |
| `docs/AGENT_PROTOCOL.md` | 에이전트가 따를 기록 규칙 + CLAUDE.md 에 넣을 문구 |
| `templates/review_report.md` | 코드 리뷰 리포트 양식 (일반 사용자도 읽기 쉬운 구성) |
| `integrations/claude-skills/` | DevTrack 에 기록하는 Claude Code 스킬 (`/review-pr`) |

## 빠른 시작 (로컬에서 예시 화면 보기)

```bash
bin/devtrack --data demo-data demo        # 예시 작업 8개 생성 (실제 git 저장소 포함)
bin/devtrack --data demo-data serve --open
# → http://localhost:8765
```

예시에는 승인 대기 중인 커밋 요청(실제 diff), PR 리뷰 리포트(md/pdf), 막힌 학습 작업, 논문 LaTeX 초안,
분석 노트북 등이 들어 있습니다. 웹에서 `T-0006` 커밋 요청을 승인한 뒤 아래를 실행하면 실제 커밋이 만들어집니다.

```bash
bin/devtrack --data demo-data inbox             # 승인 결정 확인
bin/devtrack --data demo-data apply T-0006 A-1  # 커밋 (데모 저장소는 원격이 없어 push 생략)
```

어디서든 쓰려면 PATH 에 연결하세요: `ln -s "$PWD/bin/devtrack" ~/bin/devtrack`

## 웹 화면

- **대시보드** — 진행 중 / 승인 대기 / 열린 문제 / 완료 요약, 확인이 필요한 항목, 진행 중인 작업 카드, 최근 활동
- **작업 상세** — 지시 내용, 현재 단계와 진행률, 작업 계획 체크리스트, 진행 기록 타임라인, 문제점, 결과물, 승인 요청.
  오른쪽 위에서 **완료로 처리**(끝나지 않았어도 완료로 넘김, 메모 가능) · **작업 취소** · **삭제**.
  아직 시작하지 않은 작업은 담당 AI 가 지금 웹 지시를 기다리는지(대기 모드 · `devtrack watch`)와, 아니면 깨우는 방법을 표시 (로컬 서버 모드)
- **승인 요청** — 커밋 diff · 리포트를 펼쳐 보고 **승인 / 수정 요청 / 거절** + 코멘트
- **코드 개발** — 코드 개발 작업(유형 `code`)별 카드(대상 저장소 · 브랜치, 지금 하는 일), 반영된 커밋(승인 후 커밋 · 푸시 기록, GitHub 링크), 코드 · diff 결과물. `승인 대기` 필터는 확인이 필요한 작업만 표시
- **코드 리뷰** — **리뷰 결과 한눈에 보기**(PR 정보, 리뷰 의견, 쉬운 요약, 중요도별 남은 항목, 검토 항목 R1, R2… 와 쉬운 설명 · GitHub 위치 링크),
  리뷰 리포트(Markdown · PDF) 뷰어, **PR 코멘트를 작성할 항목을 체크해서 진행**, 코멘트 초안 게시 승인, 의견 보내기.
  Claude Code `/review-pr` 스킬 연동은 [integrations/claude-skills](integrations/claude-skills/README.md)
- **논문 작성** — 논문(유형 `paper`)별 카드: 원고 구성(작성 계획), **컴파일된 PDF**(최신 빌드 · 경고 · 실패 알림), 원고 파일, 그림, 연결된 데이터 분석, 열린 문제. 논문 결과물(PDF · 원고 · 그림 · 표/데이터) 모아보기
- **LaTeX 문서 보기** — `.tex` 결과물을 코드 대신 읽기 쉬운 문서로 표시: 절 제목 · 수식(KaTeX) · 표 · 목록 · 각주, `\cite`/`\ref`/`\eqref` 번호 연결, `\includegraphics` 는 같은 이름으로 올라온 그림(같은 작업 또는 연결된 분석 작업)을 찾아 표시. `원본 코드` 로 전환 가능. 번호·레이아웃은 실제 PDF 와 다를 수 있는 미리보기
- **문서 리뷰** — 논문 · 보고서 · 제안서(PDF · Word · LaTeX) 리뷰 작업(유형 `docreview`) 목록과, 고른 작업의 **리뷰 결과 한눈에 보기**(리뷰 의견 · 한 줄 결론, 문서 요약, 영역별 평가, 잘된 점, 보완 · 수정할 부분 R1… 의 위치와 **원문 → 수정 제안**), 리뷰 리포트(PDF) · 원본 문서 보기. Word 원본은 글자 · 표를 뽑아 미리보기(표준 라이브러리만). 새 작업 지시에서 문서 경로 · 종류 · 목적 입력
- **데이터 분석** — 데이터 분석 · 시각화 작업(유형 `analysis`, `visualization`)과 그림 갤러리, 데이터 · 노트북 · 리포트. `논문용` 필터는 논문 작업에 연결된 분석만 표시
- **브레인스토밍** — 브레인스토밍 작업(유형 `brainstorm`) 목록과, 고른 작업의 주제 · 진행 계획, AI 가 정리한 아이디어 노트, **이어갈 아이디어를 체크해서 고르기**(`request --choices "I1=…"`), 지금까지 고른 아이디어, 의견 보내기, 고른 아이디어로 새 작업 지시(연결된 작업으로 표시)
- **결과물** — Markdown, PDF, 이미지/SVG, diff, 코드/LaTeX(하이라이트), CSV 표, Jupyter 노트북, HTML.
  결과물 화면과 작업의 결과물 탭은 오른쪽 위 단추로 **카드 · 목록** 보기를 바꿀 수 있습니다 (목록은 제목을 줄이지 않고 세로로 나열)
- **활동 기록** — 모든 작업의 기록을 날짜별로
- **새 작업 지시** — 제목 · 유형 · 저장소 · 지시 내용으로 작업 등록
- **사용 방법** — 처음 쓰는 사람이 차례로 따라 하는 매뉴얼 (설치 → Claude 설정 → 작업 지시 → 승인 → 분야별 사용법 → Frontier). 원문: [web/guide.md](web/guide.md), 영어판 [web/guide.en.md](web/guide.en.md)
- **알림** — 상단 종 아이콘(승인 대기 수), 새 승인 요청/문제/완료 시 토스트, 선택 시 데스크톱 알림. 10초마다 자동 갱신
- **화면 언어** — 처음 열면 영어 화면입니다. 상단의 `한` / `EN` 단추로 메뉴 · 머리말 · 버튼 · 상태 라벨 같은 화면 틀을 영어 ↔ 한국어로 전환.
  사용 방법 문서도 한국어판(`web/guide.md`) / 영어판(`web/guide.en.md`)으로 바뀝니다. 작업 내용(지시, 진행 기록, 결과물, 리뷰 리포트)은 한국어 그대로이고, 선택은 브라우저에 기억됩니다

### 두 가지 동작 모드

| | 로컬 서버 모드 (`devtrack serve`) | 정적 모드 (Frontier `www` 등) |
|---|---|---|
| 진행 상황 · 알림 보기 | ✅ 자동 갱신 | ✅ 자동 갱신 |
| 승인 · 피드백 · 작업 지시 | 웹에서 바로 저장 | Claude 세션에 붙여넣을 **메시지/명령이 생성됨** |

화면 우측 상단의 표시(`실시간 · 로컬 서버` / `정적 · 읽기 전용`)로 현재 모드를 알 수 있습니다.

## 에이전트 쪽 사용법

자세한 규칙은 [docs/AGENT_PROTOCOL.md](docs/AGENT_PROTOCOL.md). 핵심 명령:

```bash
TID=$(devtrack new "작업 제목" -t code --repo ~/project --plan "분석" "구현" "테스트")
devtrack step    $TID "지금 하는 일" -p 40
devtrack check   $TID 1
devtrack issue   $TID "문제 내용" -s high -d "상세"
devtrack artifact $TID result.png -n "결과 그림"
devtrack review  $TID review_report.md
AID=$(devtrack request $TID "변경사항 커밋" --commit -m "커밋 메시지")
devtrack wait    $TID $AID --timeout 1800   # 승인되면 exit 0
devtrack apply   $TID $AID                  # commit + push
devtrack done    $TID -m "완료 요약"
devtrack inbox                              # 웹에서 온 새 지시 · 메시지 · 결정
```

| 명령 | 설명 |
|---|---|
| `new` / `start` / `done` | 작업 생성 / 대기 작업 시작 / 완료 (`--by-user`: 사용자가 끝나지 않은 작업을 완료로 넘긴 것을 기록 — 웹의 **완료로 처리** 와 같음) |
| `cancel` / `delete` | 작업 취소 (기록 유지, 대기 중인 승인 요청 철회) / 작업 삭제 (되돌릴 수 없음, 작업 ID 를 다시 입력해 확인 — 웹에서도 가능) |
| `step` / `status` / `log` | 현재 단계 / 상태·진행률 변경 / 기록 남기기 |
| `plan` / `check` | 작업 계획 설정 / 단계 완료 표시 |
| `link` | 작업 연결 (예: 데이터 분석 → 논문). `new --related T-ID` 로 생성 시 연결도 가능 |
| `issue` / `resolve` | 문제 기록 / 해결 |
| `artifact` / `review` | 결과물 첨부 (파일, `--url`, `--git-diff`, `--source` = 문서 리뷰의 검토할 원본) / 코드 · 문서 리뷰 리포트 등록 + `--findings` 로 검토 항목(R1, R2…) 기록 (문서 리뷰는 `doc` · `checks` · `strengths` 도), `--pdf` 로 PDF 도 만들어 첨부. 같은 이름으로 다시 올리면 새 버전으로 교체(이전 버전 보관, `artifact --replace` 도 동일) |
| `text` | 문서의 글자 뽑기 — Word(`.docx`) → Markdown (표준 라이브러리), PDF → 글자 (`pdftotext` 가 있을 때). 문서 리뷰에서 AI 가 원본을 읽는 용도 |
| `pdf` | Markdown 문서를 PDF 로 변환 (A4, 한글 글꼴, 웹과 같은 모양 — 헤드리스 Chrome/Chromium 사용, `DEVTRACK_CHROME` 으로 경로 지정) |
| `latex` | LaTeX 컴파일 → 성공하면 PDF 를 빌드로 기록(쪽수, 정의 안 된 인용/참조, overfull 경고), 실패하면 오류 위치와 로그 기록. `--pdf` 로 다른 곳에서 만든 PDF 기록 |
| `request` / `wait` / `apply` | 승인 요청 (`--commit` 이면 diff 자동 첨부, `--choices` 면 사용자가 웹에서 항목을 체크해 고름) / 결정 대기 / 승인된 커밋 실행 |
| `decide` / `comment` | 사용자 결정(`--select R1 R3`) · 메시지 기록 (정적 모드에서 에이전트가 대신 기록) |
| `inbox` | 처리할 사용자 입력 — 내게 배정된 작업만 (`--ack` 로 처리 완료 표시, `--wait` 로 새 입력이 올 때까지 대기 — 에이전트가 백그라운드로 실행해 두면 웹 지시에 바로 반응, `--all-agents` 모든 AI, `--task` 한 작업만) |
| `hook` | Claude Code · Codex 의 PreToolUse 훅 — 그 세션이 맡은 작업을 사용자가 완료로 처리 · 취소 · 삭제했으면 다음 도구 실행을 막고 멈추게 함. 훅에는 항상 0 으로 끝나는 `bin/devtrack-hook` 을 건다 (설정: 웹의 사용 방법 5-4 · 11단계 ⑤) |
| `watch` | AI 실행기: 웹에서 그 AI 에게 온 입력마다 AI 를 실행해 처리 (`--codex` = `codex exec`, 또는 `-- 명령`) — Codex 처럼 대기 모드가 없는 AI 용 |
| `show` / `list` | 작업 상세 / 목록 |
| `serve` / `publish` / `demo` | 로컬 서버 / 정적 배포 / 예시 데이터 |

공통 옵션: `--data DIR`(기본 `$DEVTRACK_DATA` 또는 `<저장소>/data`), `--as NAME`(기록자 · 나의 AI 이름, 기본 `$DEVTRACK_AGENT` 또는 Claude), `--json`.

**여러 AI 함께 쓰기**: 작업마다 담당 AI(Claude · Codex · Gemini · 누구나)가 있고, 각 AI 는 자기에게 배정된 작업만 가져갑니다
(`start` 가 담당을 확인, 다른 AI 의 작업은 `start --take`). Codex 설정은 웹의 사용 방법 11단계와
[AGENT_PROTOCOL.md](docs/AGENT_PROTOCOL.md#agentsmd-에-넣을-내용-codex)를 보세요.
작업/항목 ID 는 짧게 써도 됩니다: `devtrack step 3 "..."` = `T-0003`, `resolve 3 1` = `I-1`.

### 커밋 안전장치

- `request --commit` 시점의 변경사항 지문(fingerprint)을 저장하고, `apply` 할 때 다시 계산해 **승인한 diff 와 다르면 실행을 거부**합니다 (`--force` 로 무시 가능).
- 승인되지 않은 요청, 이미 적용된 요청, 승인된 브랜치와 현재 브랜치가 다른 경우에도 실행하지 않습니다.
- 커밋은 성공하고 푸시만 실패하면 그 사실이 기록되고 웹에 오류로 표시됩니다.

## Frontier 에 배포

```bash
module load cray-python                 # 시스템 python3 가 구버전이면
git clone git@github.com:hyungyukang/AI_Dev_Progress_Checker.git ~/devtrack
ln -s ~/devtrack/bin/devtrack ~/bin/devtrack

export DEVTRACK_DATA=~/devtrack-data            # 작업 기록 위치
export DEVTRACK_PUBLISH_DIR=~/www/devtrack      # 웹 공개 디렉토리 (환경에 맞게)
devtrack publish                                # 웹 + 데이터 최초 배포
```

`DEVTRACK_PUBLISH_DIR` 이 설정되어 있으면 이후 모든 기록이 자동으로 공개 디렉토리에 동기화됩니다
(파일 권한은 웹에서 읽을 수 있게 644/755 로 설정). 웹 화면(`web/`)을 수정했을 때만 `devtrack publish` 를 다시 실행하면 됩니다.
위 환경 변수는 `~/.bashrc` 에, 에이전트 규칙은 `~/.claude/CLAUDE.md` 에 넣어 두세요 ([문구](docs/AGENT_PROTOCOL.md#claudemd-에-넣을-내용)).
로컬에서 쓰던 기록을 Frontier 로 옮기는 방법, Claude Code 설치 · 설정(훅이 쓰는 파이썬 포함), tmux 로 Claude 띄워 두기,
SSH 터널로 웹에서 바로 지시 · 승인하는 방법과 그 주의점(켜 있는 동안 같은 로그인 노드의 다른 사용자도 접속 가능), 정적 웹의 주의점은
[사용 방법 10단계](web/guide.md#10-frontier-에서-쓰기)에 있습니다.

> ⚠️ 공개 디렉토리에 올라간 내용(코드 diff, 리포트, 로그)은 URL 을 아는 누구나 볼 수 있습니다.
> 민감한 내용이 있다면 접근 제한이 가능한 위치를 사용하세요.

## 데이터 구조

```
data/
  index.json                      대시보드용 요약 (기록할 때마다 재생성)
  tasks/T-0001/task.json          지시, 계획, 기록(log), 문제(issues), 결과물(artifacts), 승인(approvals)
  tasks/T-0001/artifacts/F-1_...  결과물 파일
```

웹은 이 파일들을 읽기만 하므로 정적 호스팅에서도 동작합니다. 쓰기는 CLI 와 로컬 서버만 합니다 (파일 잠금 + 원자적 쓰기).

## 개발

- 프런트엔드: `web/js/app.js`(화면 · 라우팅 · 폴링), `web/js/viewers.js`(결과물 뷰어), `web/js/util.js`, `web/css/app.css`
- 백엔드: `devtrack/store.py`(데이터), `devtrack/cli.py`, `devtrack/server.py`, `devtrack/gitops.py`, `devtrack/demo.py`, `devtrack/latex.py`, `devtrack/pdf.py`(Markdown → PDF), `devtrack/docx.py`(Word → Markdown)
- 외부 라이브러리는 `web/vendor/` 에 고정 버전으로 포함 (marked 12.0.2, DOMPurify 3.1.6, highlight.js 11.9.0)
