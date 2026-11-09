# AI 에이전트 작업 기록 규칙 (DevTrack)

Claude · Codex 등 AI 에이전트가 사용자 대신 작업할 때 따르는 규칙입니다.
사용자는 웹 대시보드에서 진행 상황을 보고, 승인 요청에 답합니다.

> Claude 가 이 규칙을 항상 따르게 하려면 아래 [CLAUDE.md 에 넣을 내용](#claudemd-에-넣을-내용)을
> `~/.claude/CLAUDE.md`(모든 프로젝트) 또는 작업 대상 저장소의 `CLAUDE.md` 에 붙여 넣으세요.
> Codex 는 [AGENTS.md 에 넣을 내용](#agentsmd-에-넣을-내용-codex)을 `~/.codex/AGENTS.md` 에 넣습니다.

## 원칙

1. **모든 작업은 DevTrack 작업(T-xxxx)으로 기록한다.** 사용자가 보고 있다고 생각하고, 의미 있는 단계마다 남긴다.
2. **커밋/푸시는 승인 후에만 한다.** `git commit`/`git push` 를 직접 실행하지 말고
   `devtrack request --commit` → 사용자 승인 → `devtrack apply` 로만 반영한다.
3. **문제는 숨기지 않는다.** 막히거나 예상과 다르면 `issue` 로 기록하고, 결정이 필요하면 `status blocked` + 선택지를 남긴다.
4. **결과물은 웹에서 볼 수 있게 남긴다.** 그림, 리포트, 로그, 노트북, LaTeX, diff 등은 `artifact` 로 첨부한다.
5. **사용자 입력을 주기적으로 확인하고, 온 것은 묻지 않고 처리한다.** 작업 시작 시, 단계가 끝날 때마다 `devtrack inbox` 를 확인한다.
   웹에서 온 새 작업 지시 · 메시지는 그 자체가 사용자의 지시다 — [다른 작업 중에 온 입력](#다른-작업-중에-온-입력).
6. **내게 배정된 작업만 처리한다.** `devtrack inbox` 에는 나(`$DEVTRACK_AGENT`)에게 배정된 작업과 담당이 정해지지 않은 작업만 보인다.
   다른 AI 의 작업은 사용자가 맡으라고 할 때만 `devtrack start <TID> --take` 로 가져온다.

## 작업 흐름

```bash
# 0. 사용자 입력 확인 (웹에서 들어온 새 지시, 메시지, 승인 결정)
devtrack inbox

# 1. 작업 시작 (웹에서 지시된 작업이면 new 대신 start)
TID=$(devtrack new "압력 솔버 HIP 포팅" -t code --repo ~/cfd-solver \
      -i "사용자 지시 원문" --plan "구조 분석" "포팅" "검증" "커밋 승인 요청")
devtrack start T-0007 "지시 확인, 분석 시작"        # 대기(queued) 작업인 경우
devtrack link  T-0008 T-0005                         # 관련 작업 연결 (예: 이 분석 → 논문 작업)

# 2. 진행 기록 — 지금 무엇을 하는지
devtrack step $TID "hipify 변환 중 (커널 3개)" -p 30
devtrack check $TID 1                                 # 계획 1단계 완료
devtrack log $TID "빌드 성공, 경고 2개" -k success -d "상세 내용 (markdown)"

# 3. 문제
devtrack issue $TID "스트림 동기화 누락으로 NaN" -s high -d "재현 조건 ..."
devtrack resolve $TID I-1 -m "hipStreamSynchronize 추가"

# 4. 결과물
devtrack artifact $TID perf_report.md -n "성능 측정 리포트"
devtrack artifact $TID fig/speedup.png -n "가속비 그림"
devtrack artifact $TID --git-diff ~/cfd-solver -n "현재 변경사항"
devtrack artifact $TID --url https://github.com/org/repo/pull/12 -n "PR"
devtrack latex    $TID paper/main.tex                 # 논문: 컴파일 → PDF/오류 기록 (TeX 없으면 --pdf 로 기록)

# 5. 커밋 승인 요청 (diff 자동 첨부) → 웹 알림
AID=$(devtrack request $TID "HIP 포팅 커밋" --commit -m "Port solver to HIP" \
      -d "검증: CPU 대비 상대오차 3e-13" -a F-1)

# 6. 결정 대기 (승인 0, 수정/거절 2, 시간초과 3)
devtrack wait $TID $AID --timeout 1800
#   승인 → 커밋/푸시 실행
devtrack apply $TID $AID
#   수정 요청 → 코멘트대로 고친 뒤 새 승인 요청 (같은 요청은 재사용 불가)

# 7. 완료
devtrack done $TID -m "HIP 포팅 완료, 커밋 abc1234"

# (참고) 취소 · 삭제 — 사용자가 요청할 때만
devtrack cancel $TID -m "사유"        # 기록은 남기고 취소 (대기 중인 승인 요청은 철회)
devtrack delete $TID                  # 완전히 삭제 — 작업 ID 를 다시 입력해 확인 (사용자가 이미 확인했으면 --yes)
```

## 작업 유형별 요령

| 유형 (`-t`) | 남길 것 |
|---|---|
| `code` | 계획, 단계별 진행, 테스트 결과, `request --commit` |
| `review` | **일반 사용자도 읽을 수 있는** 리포트(`templates/review_report.md` 형식: 한 줄 결론 · 한눈에 보기 · 쉬운 설명 · 용어 풀이)와 검토 항목 R1, R2… 를 `devtrack review <TID> report.md [report.pdf] --findings findings.json` 으로 등록. 코멘트할 항목은 `request -k review --choices` 로 사용자가 고르게 하고, PR 에 게시하기 전 초안으로 다시 `request -k review`. 자세한 흐름은 [코드 리뷰](#코드-리뷰) |
| `paper` | `--plan` 에 원고 구성(절/장)을 넣는다 — 웹의 논문 카드에 체크리스트로 표시. 원고를 고칠 때마다 `.tex` 를 `artifact` 로 올리고 **`devtrack latex <TID> paper/main.tex` 로 컴파일 결과(PDF/오류)를 기록**한다. 그림은 `\includegraphics` 와 **같은 파일 이름(확장자 무관)** 으로 올려야 웹의 문서 보기에서 연결된다 (예: `figs/fig4_1.pdf` ↔ `fig4_1.png`). 원본 데이터와 다른 수치는 `issue` |
| `analysis` | 노트북(`.ipynb`), CSV 요약, 그림, 요약 리포트(md). 해석이 필요한 결과는 `request -k result`. **논문에 쓸 분석이면 `--related <논문 T-ID>`** 로 연결 |
| `visualization` | 그림/HTML(plotly 등) 결과물, 사용한 스크립트 |
| `model` | 학습 설정, 손실 곡선 그림, 로그, 평가 결과. 큰 자원 사용 전 `request -k plan` |

## 코드 리뷰

Claude Code 의 `/review-pr` 스킬을 쓰면 아래 흐름이 자동으로 진행됩니다
(DevTrack 연동판: [integrations/claude-skills](../integrations/claude-skills/README.md)). 직접 할 때도 같은 순서를 따릅니다.

```bash
TID=$(devtrack new "PR #42 코드 리뷰: <제목>" -t review --url https://github.com/org/repo/pull/42 --tag PR-42 \
      --plan "PR 정보 · 변경사항 수집" "코드 검토" "리뷰 리포트 작성" "코멘트할 항목 선택 (사용자)" "코멘트 초안 작성 · 게시 승인" "PR 에 게시")

# 1. 리포트 + 검토 항목 등록 → 웹의 '리뷰 결과 한눈에 보기'
devtrack review $TID review-PR-42.md --pdf -n "PR #42 리뷰 리포트" --findings review-PR-42.json   # --pdf: PDF 도 만들어 첨부
#    리포트를 고친 뒤 같은 명령을 다시 실행하면 같은 결과물(F-1, F-2)의 새 버전이 됨 — 파일 이름을 바꾸지 말 것

# 2. 코멘트할 항목을 사용자가 체크해서 고름 (값 없는 --choices = 열린 항목 전체, 반드시 수정 · 수정 권장은 미리 체크)
AID=$(devtrack request $TID "PR #42: 코멘트를 작성할 항목을 골라주세요" -k review --choices \
      --choices-label "PR 코멘트를 작성할 항목" -a F-1)
devtrack wait $TID $AID      # → approved / 선택: R1, R3 / 코멘트: ...

# 3. 고른 항목만 코멘트 초안 → 게시 승인 요청
F=$(devtrack artifact $TID review-PR-42-comments.md -n "PR #42 리뷰 코멘트 초안" --review)
AID=$(devtrack request $TID "PR #42 에 리뷰를 게시할까요? (COMMENT, 코멘트 2개)" -k review -a $F)

# 4. 승인되면 게시 → 상태 갱신 (같은 ID 는 합쳐지므로 바뀐 필드만 줘도 됨)
echo '[{"id":"R1","status":"commented"},{"id":"R3","status":"commented"}]' | devtrack review $TID --findings -
devtrack artifact $TID --url <게시된 리뷰 URL> -n "PR #42 게시된 리뷰" --review
devtrack done $TID -m "PR #42 리뷰 게시"
```

검토 항목 JSON (`--findings`):

```json
{
  "pr": {"url": "https://github.com/org/repo/pull/42", "number": 42, "title": "...", "author": "...",
         "base": "main", "head": "feature/x", "head_sha": "<리뷰한 커밋>", "files": 14, "additions": 612, "deletions": 430, "ci": "통과"},
  "verdict": "request_changes",
  "summary": "한 줄 결론 + 꼭 고칠 것 (쉬운 말 2~3문장)",
  "findings": [
    {"id": "R1", "severity": "blocker", "title": "기술적 한 줄 요약", "plain": "쉽게 말하면: 비전문가용 1~2문장",
     "file": "src/a.cpp", "line": 88, "category": "정확성", "confidence": "high", "status": "open"}
  ]
}
```

- `severity`: `blocker`(반드시 수정) · `major`(수정 권장) · `minor`(개선 제안) · `nit`(사소함) · `question`(질문)
- `status`: `open` · `fixed` · `commented`(PR 에 게시) · `dismissed`(제외) · `na`(해당 없음)
- `verdict`: `approve` · `comment` · `request_changes`
- **리포트 · 검토 항목은 모두 한국어로** 쓴다 (항목 제목, 개발자용 상세 포함). 코드 · 식별자 · 경로 · 인용문만 원문 그대로. GitHub 코멘트만 영어.
- **리포트는 일반 사용자도 이해할 수 있게** 쓴다: 맨 앞에 한 줄 결론, 비개발자용 "한눈에 보기", 항목마다 `plain`(어떤 상황에서
  무엇이 잘못되는지), 전문 용어는 풀어서. 개발자용 상세(코드 · 위치 · 근거)는 그 뒤에 그대로 둔다.

## 승인 요청을 올려야 하는 때

- 저장소에 **커밋/푸시** 할 때 (`--commit`, 필수)
- PR 에 리뷰 코멘트 게시, 외부에 공개되는 작업 (`-k review`)
- 많은 계산 자원을 쓰는 작업 시작 전 (`-k plan`)
- 결과 해석/데이터 제외 등 사용자의 판단이 필요한 경우 (`-k result`)

## 웹 지시를 바로 받기 (대기 모드)

웹에서 작업을 지시해도 Claude 는 채팅으로 말을 걸기 전까지 움직이지 않습니다. 열어 둔 Claude Code 세션이
웹 입력에 바로 반응하게 하려면, 에이전트가 `devtrack inbox --wait` 를 **백그라운드로** 실행해 둡니다.

```bash
devtrack inbox                 # 1. 밀린 입력 먼저 처리 (본 항목은 '본 것'으로 기록됨)
devtrack inbox --wait          # 2. 백그라운드로 실행 → 아직 보지 않은 새 지시 · 메시지 · 결정이 생기면 출력하고 종료
#                                 3. Claude Code 는 백그라운드 명령이 끝나면 에이전트를 다시 깨운다 → 처리 → 2 로
```

- 사용자는 Claude 채팅에서 한 번 "웹 지시 기다려줘" 라고 하면 됩니다. 이후에는 웹에서 지시 · 승인만 해도 진행됩니다.
- 기다리는 동안에는 파일 변경만 확인하므로 토큰을 쓰지 않습니다. VS Code 창(세션)은 열려 있어야 합니다.
- 이미 본 항목(예: 시작하지 않기로 한 대기 작업)으로는 다시 깨어나지 않습니다. 본 항목 기록: `<data>/.agent/inbox-seen-<에이전트>.json` (배포되지 않음).
- 기다리는 동안 `<data>/.agent/listeners/` 에 '기다리는 중' 기록을 남깁니다 (30초마다 갱신, 끝나면 삭제, `devtrack watch` 도 같음).
  웹(로컬 서버, `api/agents`)은 이것으로 대기 작업 화면에 '기다리는 중 / 기다리는 AI 없음 / 이미 확인함'을 보여 줍니다.
  이 프로세스를 띄운 대화가 끝나 부모 프로세스가 바뀌면 기록을 지웁니다 (깨울 대상이 없으므로).
- `--timeout 초` 를 주면 시간 초과 시 exit 3. 승인 결정에도 깨어나므로 이 모드에서는 `devtrack wait` 가 따로 필요 없습니다.
- 로컬 서버 모드(`devtrack serve`)에서만 웹 입력이 바로 저장됩니다. 정적 모드에서는 웹이 만든 메시지를 채팅에 붙여넣으세요.

## 다른 작업 중에 온 입력

웹에서 온 새 작업 지시와 메시지(추가 질문 · 조사 요청)는 사용자가 이미 지시한 것이다. 다른 작업을 하다가 inbox 에서 발견해도
"진행할까요?" 라고 묻지 않고 처리한다.

- **순서**: 메시지는 지금 단계를 마친 뒤 바로, 새 작업은 지금 작업이 끝나거나 승인 대기가 되면 시작한다. 여러 건이면 온 순서대로 하고,
  끝나면 원래 작업으로 돌아온다. 채팅에서 사용자가 직접 시킨 일이 있으면 그것을 먼저 한다.
- **새 작업**: `devtrack start <TID>` 로 시작해 지시대로 진행한다.
- **메시지**: 그 작업의 지시 · 이전 기록 · 결과물을 보고 답한다. 답은 웹에서 볼 수 있게 그 작업에 남기고
  (`devtrack log <TID> "답 요약" -d "상세"`, 길면 `devtrack artifact` 로 리포트) `devtrack inbox --task <TID> --ack` 로 처리 표시한다.
- **승인 결정**: 평소대로 — 승인이면 `devtrack apply`, 수정 요청이면 고친 뒤 새 승인 요청.
- 자동으로 진행해도 승인 규칙은 그대로다: 커밋 · 푸시, PR 게시 등은 `devtrack request` 로 승인을 받는다.
- 채팅 중이었다면 마지막 답에 받은 편지함에서 처리한 것을 한두 줄로 알린다.
- `devtrack inbox` 출력 끝의 `→ 새 작업 · 메시지는 …` 안내가 이 규칙을 다시 알려 준다.

## 여러 AI 함께 쓰기 (담당 AI)

작업마다 담당 AI(`agent`)가 있습니다. 웹의 "새 작업 지시" 에서 고르거나 `devtrack new --agent Codex` 로 정합니다.

- **내 이름**: `--as 이름` > `$DEVTRACK_AGENT` > `Claude`. Codex 는 `DEVTRACK_AGENT=Codex` 로 실행합니다
  (`~/.codex/config.toml` 의 `[shell_environment_policy.set]` 아래에 `DEVTRACK_AGENT = "Codex"`).
- **받은 편지함**: `devtrack inbox` · `inbox --wait` 는 내게 배정된 작업과 담당 미정(`누구나`) 작업의 입력만 보여 줍니다.
  `--all-agents` 는 전부, `--task T-0007` 은 그 작업만. `--ack` 는 보여 준 항목만 처리됨으로 표시합니다.
- **시작할 때 확인**: `devtrack start` (그리고 대기 작업에 대한 첫 `step`)는 담당이 다른 AI 면 거부합니다.
  담당 미정 작업은 먼저 시작한 AI 가 맡습니다 (동시에 시작하면 한쪽만 성공). 대신 맡기: `devtrack start <TID> --take`.
- **취소 · 삭제 알림**도 그 작업의 담당 AI 에게만 갑니다.

### Codex: `devtrack watch` 로 웹 지시 받기

Codex 에는 위의 백그라운드 대기 모드를 쓸 수 없으므로, 사용자가 터미널에 실행기를 켜 둡니다.

```bash
devtrack watch --codex                     # Codex 에게 온 입력이 생길 때마다 codex exec 로 처리
devtrack watch --agent Gemini -- <AI 명령> [옵션…]    # 다른 AI: 프롬프트를 마지막 인자로 받는 명령이면 됨
```

- 작업마다 한 번씩 실행합니다. 실행 폴더는 작업의 저장소 경로(없으면 `--cwd`, 기본 현재 폴더).
  Codex 는 `codex exec --sandbox workspace-write --skip-git-repo-check -C <폴더> --add-dir <기록 폴더>` 로 실행됩니다
  (허락을 물을 수 없으므로 인터넷 허용 `-c sandbox_workspace_write.network_access=true` — 막으려면 `--no-network`).
  Codex CLI 는 `$DEVTRACK_CODEX` > PATH 의 `codex` > 앱(ChatGPT · Codex · VS Code 확장)에 든 CLI 중 가장 새 버전.
- 실행되는 AI 에게는 `DEVTRACK_AGENT`, `DEVTRACK_DATA`, `DEVTRACK_TASK=<TID>`, `DEVTRACK_WATCH=1` 이 주어지고,
  그 안에서 `devtrack inbox` 는 그 작업의 입력만 보여 줍니다.
- **watch 가 실행한 세션의 규칙**: 그 작업만 처리하고, 사용자 결정이 필요하면 `devtrack request` 를 남기고 끝낸다.
  `devtrack wait` · `inbox --wait` 로 기다리지 않는다 — 결정이 오면 watch 가 다시 실행한다.
- 성공(exit 0)하면 넘긴 입력을 처리됨으로 표시하고, Codex 의 마지막 메시지를 "실행 요약" 으로 기록합니다.
  실패하면 경고를 남기고 입력은 그대로 둡니다 (watch 를 다시 켜면 다시 처리).
- 실행 중에 사용자가 그 작업을 취소 · 삭제하면 실행을 멈춥니다. 같은 AI 의 watch 는 하나만 실행됩니다.

## 사용자 결정이 도착하는 방법

- **로컬 서버 모드**: 사용자가 웹에서 누르면 즉시 기록됨 → `devtrack wait` / `devtrack inbox` 로 확인
- **정적 모드 (Frontier www)**: 사용자가 Claude 세션에 `[DevTrack 결정] T-0001 / A-1 ... → 승인` 형태의 메시지를 붙여넣음
  → 에이전트가 `devtrack decide T-0001 A-1 approve -m "코멘트"` 로 기록한 뒤 `apply` 진행
  (선택 항목이 있는 요청이면 메시지에 `선택: R1, R3` 이 있고, 명령에 `--select R1 R3` 가 붙어 있음)
- `[DevTrack 메시지]` → `devtrack comment <TID> "..."` 로 기록 후 반영
- `[DevTrack 새 작업 지시]` → `devtrack new ...` 로 등록 후 진행
- `[DevTrack 작업 취소]` → `devtrack cancel <TID> -m "사유"` 후 그 작업 중단
- `[DevTrack 작업 삭제]` → 사용자가 웹에서 작업 ID 를 입력해 확인한 것이므로 `devtrack delete <TID> --yes` 후 그 작업 중단
- 웹(로컬 서버)에서 취소 · 삭제하면 `devtrack inbox` 에 `[취소]` · `[삭제]` 로 표시된다 → 그 작업을 바로 멈춘다

### 작업 도중에 온 취소 · 삭제 (`devtrack hook`)

대화형 AI 는 단계 사이에만 inbox 를 보므로 작업 도중의 취소 · 삭제를 모른다. 이를 위해:

- 작업에 기록하는 명령(`new` · `start` · `step` · `artifact` · `request` · `done` 등)은 실행한 AI 세션을 그 작업에 적어 둔다
  (`CLAUDE_CODE_SESSION_ID` · `CODEX_THREAD_ID`, 다른 AI 는 `DEVTRACK_SESSION`).
- PreToolUse 훅으로 `bin/devtrack-hook`(= `devtrack hook`, 항상 종료 코드 0)을 걸어 두면(웹의 사용 방법 5-4 · 11단계 ⑤), 도구를 실행하기 직전마다 그 세션이 맡은 작업 중
  사용자가 취소 · 삭제했는데 아직 확인하지 않은 것이 있는지 본다. 있으면 그 도구 실행을 막고 `⛔ DevTrack: …` 안내를 AI 에게 준다.
  devtrack 명령(과 `echo` · `head` · `tail` · `grep` 같은 출력 정리)만 있는 셸 명령은 통과한다.
- 안내를 받으면: 그 작업을 하던 중이면 즉시 멈추고, 다른 명령을 붙이지 말고 `devtrack inbox --task <TID> --ack` 만 실행해
  확인을 기록한 뒤 사용자에게 멈췄다고 알린다 (대기 모드라면 다시 `devtrack inbox --wait` 를 백그라운드로).
  다른 작업을 하던 중이었다면 확인만 기록하고 그 작업을 계속한다.
- 확인하기 전에는 그 작업에 기록하려 해도(`step` · `artifact` …) 같은 안내와 함께 거절된다. 완료된 작업을 지운 경우는 알리지 않는다.
- 훅은 어떤 오류에도 도구 실행을 막지 않는다 (입력이 이상하거나 데이터 폴더가 없으면 그냥 통과).

## CLAUDE.md 에 넣을 내용

```markdown
## DevTrack 작업 기록
- 의미 있는 작업(코드 변경, 코드 리뷰, 데이터 분석, 논문, 시각화, 모델 개발)은 `devtrack` 으로 기록한다. 간단한 질문 · 설명은 기록하지 않는다.
  규칙: <DevTrack 저장소>/docs/AGENT_PROTOCOL.md
- 작업 시작 전과 각 단계 후 `devtrack inbox` 로 사용자 입력(새 지시, 메시지, 승인 결정)을 확인한다.
- inbox 의 새 작업 지시 · 메시지(추가 질문 · 조사 요청)는 사용자에게 다시 묻지 않고 진행한다. 다른 작업 중이면 메시지는 지금 단계를 마친 뒤,
  새 작업은 지금 작업이 끝나거나 승인 대기가 되면 온 순서대로 처리하고 원래 작업으로 돌아온다. 메시지의 답은 그 작업에 기록하고
  (`devtrack log` · `devtrack artifact`) `devtrack inbox --task <TID> --ack` 로 처리 표시한다. 커밋 · 게시 등은 그대로 승인을 받는다.
- 사용자가 "웹 지시 기다려줘" 라고 하면 `devtrack inbox --wait` 를 백그라운드로 실행해 두고, 끝나면(새 입력) 처리한 뒤 다시 실행한다.
- 웹에서 온 지시에 `/review-pr <URL>` 같은 스킬 호출이 있으면 매번 Skill 도구로 그 스킬을 새로 불러 최신 지침대로 진행한다
  (같은 대화에서 전에 불러온 내용을 다시 쓰지 않는다).
- 의미 있는 단계마다 `devtrack step <TID> "지금 하는 일" -p <진행률>` 을 남긴다.
- 문제는 `devtrack issue`, 결과물은 `devtrack artifact` / 리뷰 리포트는 `devtrack review` 로 남긴다.
- 코드 리뷰 리포트는 개발자가 아닌 사람도 이해할 수 있게 쓴다: 한 줄 결론, 비개발자용 "한눈에 보기", 항목별 "쉽게 말하면", 용어 풀이.
- DevTrack 작업의 결과를 저장소에 반영할 때는 git commit/push 를 직접 하지 않고 `devtrack request <TID> "..." --commit -m "..."` 로
  승인을 요청하고, 승인되면 `devtrack apply <TID> <AID>` 로 반영한다. 단, 사용자가 채팅에서 직접 커밋 · 푸시를 지시하면 따른다.
- 사용자가 `[DevTrack 결정]` / `[DevTrack 메시지]` / `[DevTrack 새 작업 지시]` / `[DevTrack 작업 취소]` / `[DevTrack 작업 삭제]` 메시지를 보내면
  각각 `devtrack decide` / `devtrack comment` / `devtrack new` / `devtrack cancel` / `devtrack delete --yes` 로 먼저 기록한 뒤 진행한다.
- inbox 에 `[취소]` · `[삭제]` 가 오거나 도구 실행이 `⛔ DevTrack` 메시지로 막히면 그 작업은 즉시 멈추고, 안내대로 확인만 기록한 뒤
  (`devtrack inbox --task <TID> --ack`) 더 기록하지 않는다. 작업 삭제는 사용자가 요청하고 확인했을 때만 한다.
```

## AGENTS.md 에 넣을 내용 (Codex)

`~/.codex/AGENTS.md` 에 붙여 넣습니다. 설정 파일(`~/.codex/config.toml`)과 스킬 설치는 웹의 사용 방법 11단계를 보세요.

```markdown
## DevTrack 작업 기록 (Codex)
- DevTrack 에서 나는 "Codex" 다. `devtrack` 은 환경 변수 `DEVTRACK_AGENT=Codex` 로 실행한다
  (~/.codex/config.toml 의 `shell_environment_policy` 에 설정해 두었으면 따로 붙이지 않는다).
  `devtrack` 이 PATH 에 없으면 `<DevTrack 저장소>/bin/devtrack` 을 쓴다. 규칙: <DevTrack 저장소>/docs/AGENT_PROTOCOL.md
- 의미 있는 작업(코드 변경, 코드 리뷰, 데이터 분석, 논문, 시각화, 모델 개발)은 `devtrack` 으로 기록한다. 간단한 질문 · 설명은 기록하지 않는다.
- 작업 시작 전과 각 단계 후 `devtrack inbox` 로 사용자 입력(새 지시, 메시지, 승인 결정)을 확인한다. 나에게 배정된 작업의 입력만 보인다.
- inbox 의 새 작업 지시 · 메시지(추가 질문 · 조사 요청)는 사용자에게 다시 묻지 않고 진행한다. 다른 작업 중이면 메시지는 지금 단계를 마친 뒤,
  새 작업은 지금 작업이 끝나거나 승인 대기가 되면 온 순서대로 처리하고 원래 작업으로 돌아온다. 메시지의 답은 그 작업에 기록하고
  (`devtrack log` · `devtrack artifact`) `devtrack inbox --task <TID> --ack` 로 처리 표시한다. 커밋 · 게시 등은 그대로 승인을 받는다.
- 사용자 결정이 필요하면 `devtrack request` 로 요청을 남기고 사용자에게 알린 뒤 멈춘다. `devtrack wait` · `devtrack inbox --wait` 로 오래 기다리지 않는다
  (웹에서 온 입력은 사용자가 터미널에 켜 둔 `devtrack watch --codex` 가 나를 다시 실행해 전달한다).
- 지시에 `/review-pr <URL>` 같은 스킬 호출이 있으면 `~/.codex/skills/<이름>/SKILL.md` 를 매번 새로 읽고 그 지침대로 진행한다.
- 의미 있는 단계마다 `devtrack step <TID> "지금 하는 일" -p <진행률>` 을 남긴다.
- 문제는 `devtrack issue`, 결과물은 `devtrack artifact` / 리뷰 리포트는 `devtrack review` 로 남긴다.
- 코드 리뷰 리포트는 개발자가 아닌 사람도 이해할 수 있게 쓴다: 한 줄 결론, 비개발자용 "한눈에 보기", 항목별 "쉽게 말하면", 용어 풀이.
- DevTrack 작업의 결과를 저장소에 반영할 때는 git commit/push 를 직접 하지 않고 `devtrack request <TID> "..." --commit -m "..."` 로
  승인을 요청하고, 승인되면 `devtrack apply <TID> <AID>` 로 반영한다. 단, 사용자가 채팅에서 직접 커밋 · 푸시를 지시하면 따른다.
- 사용자가 `[DevTrack 결정]` / `[DevTrack 메시지]` / `[DevTrack 새 작업 지시]` / `[DevTrack 작업 취소]` / `[DevTrack 작업 삭제]` 메시지를 보내면
  각각 `devtrack decide` / `devtrack comment` / `devtrack new` / `devtrack cancel` / `devtrack delete --yes` 로 먼저 기록한 뒤 진행한다.
- inbox 에 `[취소]` · `[삭제]` 가 오거나 도구 실행이 `⛔ DevTrack` 메시지로 막히면 그 작업은 즉시 멈추고, 안내대로 확인만 기록한 뒤
  (`devtrack inbox --task <TID> --ack`) 더 기록하지 않는다. 작업 삭제는 사용자가 요청하고 확인했을 때만 한다.
```
