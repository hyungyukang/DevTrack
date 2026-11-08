# DevTrack — AI 개발 진행 현황 체크 서비스

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

## 구성

| 구성 요소 | 설명 |
|---|---|
| `bin/devtrack` | 에이전트용 CLI. Python 3.7+ 표준 라이브러리만 사용 (설치 불필요) |
| `web/` | 대시보드. 빌드 없는 정적 HTML/JS — 그대로 정적 호스팅 가능 |
| `devtrack serve` | 로컬 웹 서버. 웹에서 바로 승인 · 피드백 · 작업 지시 저장 |
| `devtrack publish` | 웹 + 데이터를 정적 디렉토리(예: Frontier `www`)로 배포 |
| `docs/AGENT_PROTOCOL.md` | 에이전트가 따를 기록 규칙 + CLAUDE.md 에 넣을 문구 |
| `templates/review_report.md` | 코드 리뷰 리포트 양식 |

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
- **작업 상세** — 지시 내용, 현재 단계와 진행률, 작업 계획 체크리스트, 진행 기록 타임라인, 문제점, 결과물, 승인 요청
- **승인 요청** — 커밋 diff · 리포트를 펼쳐 보고 **승인 / 수정 요청 / 거절** + 코멘트
- **코드 리뷰** — 리뷰 리포트(Markdown · PDF) 목록과 뷰어, 리뷰 관련 승인 요청과 의견 보내기
- **논문 작성** — 논문(유형 `paper`)별 카드: 원고 구성(작성 계획), **컴파일된 PDF**(최신 빌드 · 경고 · 실패 알림), 원고 파일, 그림, 연결된 데이터 분석, 열린 문제. 논문 결과물(PDF · 원고 · 그림 · 표/데이터) 모아보기
- **LaTeX 문서 보기** — `.tex` 결과물을 코드 대신 읽기 쉬운 문서로 표시: 절 제목 · 수식(KaTeX) · 표 · 목록 · 각주, `\cite`/`\ref`/`\eqref` 번호 연결, `\includegraphics` 는 같은 이름으로 올라온 그림(같은 작업 또는 연결된 분석 작업)을 찾아 표시. `원본 코드` 로 전환 가능. 번호·레이아웃은 실제 PDF 와 다를 수 있는 미리보기
- **데이터 분석** — 데이터 분석 · 시각화 작업(유형 `analysis`, `visualization`)과 그림 갤러리, 데이터 · 노트북 · 리포트. `논문용` 필터는 논문 작업에 연결된 분석만 표시
- **결과물** — Markdown, PDF, 이미지/SVG, diff, 코드/LaTeX(하이라이트), CSV 표, Jupyter 노트북, HTML
- **활동 기록** — 모든 작업의 기록을 날짜별로
- **새 작업 지시** — 제목 · 유형 · 저장소 · 지시 내용으로 작업 등록
- **알림** — 상단 종 아이콘(승인 대기 수), 새 승인 요청/문제/완료 시 토스트, 선택 시 데스크톱 알림. 10초마다 자동 갱신

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
| `new` / `start` / `done` | 작업 생성 / 대기 작업 시작 / 완료 |
| `step` / `status` / `log` | 현재 단계 / 상태·진행률 변경 / 기록 남기기 |
| `plan` / `check` | 작업 계획 설정 / 단계 완료 표시 |
| `link` | 작업 연결 (예: 데이터 분석 → 논문). `new --related T-ID` 로 생성 시 연결도 가능 |
| `issue` / `resolve` | 문제 기록 / 해결 |
| `artifact` / `review` | 결과물 첨부 (파일, `--url`, `--git-diff`) / 리뷰 리포트 등록 |
| `latex` | LaTeX 컴파일 → 성공하면 PDF 를 빌드로 기록(쪽수, 정의 안 된 인용/참조, overfull 경고), 실패하면 오류 위치와 로그 기록. `--pdf` 로 다른 곳에서 만든 PDF 기록 |
| `request` / `wait` / `apply` | 승인 요청 (`--commit` 이면 diff 자동 첨부) / 결정 대기 / 승인된 커밋 실행 |
| `decide` / `comment` | 사용자 결정 · 메시지 기록 (정적 모드에서 에이전트가 대신 기록) |
| `inbox` | 처리할 사용자 입력 (`--ack` 로 처리 완료 표시) |
| `show` / `list` | 작업 상세 / 목록 |
| `serve` / `publish` / `demo` | 로컬 서버 / 정적 배포 / 예시 데이터 |

공통 옵션: `--data DIR`(기본 `$DEVTRACK_DATA` 또는 `<저장소>/data`), `--as NAME`(기록자, 기본 `$DEVTRACK_AGENT` 또는 Claude), `--json`.
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
- 백엔드: `devtrack/store.py`(데이터), `devtrack/cli.py`, `devtrack/server.py`, `devtrack/gitops.py`, `devtrack/demo.py`
- 외부 라이브러리는 `web/vendor/` 에 고정 버전으로 포함 (marked 12.0.2, DOMPurify 3.1.6, highlight.js 11.9.0)
