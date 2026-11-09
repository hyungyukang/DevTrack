# DevTrack 사용 방법

처음 쓰는 분이 **위에서부터 차례로 따라 하면** 바로 쓸 수 있도록 정리한 매뉴얼입니다.
1~5단계는 처음 한 번만 하는 준비이고, 6단계부터가 매일 쓰는 방법입니다.

## 0. DevTrack 이란?

Claude 같은 AI 에게 일을 맡기고, **무엇을 하고 있는지 · 어디서 막혔는지 · 결과물이 무엇인지**를 웹에서 확인하는 도구입니다.
저장소에 커밋하거나 PR 에 코멘트를 다는 일처럼 되돌리기 어려운 일은 **웹에서 승인해야만** 진행됩니다.

```text
 ① 사용자: 작업 지시  ──▶  ② Claude: 작업하면서 DevTrack 에 기록
                               (지금 하는 일 · 문제 · 결과물 · 승인 요청)
 ④ 사용자: 승인 / 수정 요청  ◀──  ③ 웹 대시보드: 진행 상황 · 알림
          │
          └──▶ ⑤ Claude: 승인된 내용만 커밋 · 푸시 · 게시
```

자주 나오는 말:

| 용어 | 뜻 |
|---|---|
| 작업 (`T-0001`) | 맡긴 일 하나. 지시 내용, 계획, 진행 기록, 문제, 결과물, 승인 요청이 모두 여기에 모입니다 |
| 단계 · 진행률 | Claude 가 "지금 하는 일"을 적을 때마다 갱신됩니다 |
| 문제 (`I-1`) | 작업 중 발견한 문제나 막힌 점 |
| 결과물 (`F-1`) | 리포트, 그림, 코드 변경(diff), 논문 PDF, 노트북 등 — 웹에서 바로 열어 볼 수 있습니다 |
| 승인 요청 (`A-1`) | Claude 가 다음으로 넘어가기 전에 확인을 구하는 것. **승인 · 수정 요청 · 거절** 중 하나로 답합니다 |

## 1. 준비물 확인

터미널에서 아래 명령이 모두 실행되는지 확인합니다.

```bash
python3 --version      # 3.7 이상 (Frontier: module load cray-python)
git --version
```

- **Claude Code** — VS Code 확장 또는 터미널용 `claude` 명령. AI 가 일하는 곳입니다.
- **GitHub CLI (`gh`)** — 코드 리뷰를 맡길 때만 필요합니다. `gh auth status` 로 로그인되어 있는지 확인하세요.

DevTrack 은 Python 표준 라이브러리만 쓰므로 `pip install` 같은 설치 과정이 없습니다.

## 2. DevTrack 내려받기

```bash
git clone git@github.com:hyungyukang/AI_Dev_Progress_Checker.git ~/devtrack
```

이후 설명은 `~/devtrack` 에 받았다고 가정합니다. 다른 곳에 받았다면 경로만 바꿔 주세요.

## 3. `devtrack` 명령 등록 (PATH)

어느 폴더에서든, 그리고 **Claude 도** `devtrack` 명령을 쓸 수 있게 셸 설정 파일에 경로를 추가합니다.

```bash
echo 'export PATH="$HOME/devtrack/bin:$PATH"' >> ~/.zshrc     # bash 를 쓰면 ~/.bashrc
source ~/.zshrc
devtrack --version                                           # → devtrack 0.1.0
```

기록은 기본으로 `~/devtrack/data/` 에 저장됩니다. 다른 곳에 두려면 같은 파일에 `export DEVTRACK_DATA=~/devtrack-data` 를 추가하세요.

## 4. 웹 대시보드 열기

```bash
devtrack serve --open          # → http://localhost:8765
```

- 이 터미널 창은 **열어 둔 채로** 두세요. 닫으면 웹이 꺼집니다.
- 화면 오른쪽 위에 **실시간 · 로컬 서버** 가 보이면 정상입니다. 이 모드에서는 웹에서 누른 승인 · 지시가 바로 저장됩니다.
- 처음에는 작업이 없어 빈 화면입니다. 예시를 먼저 보고 싶다면 → [12. 자주 묻는 질문](#/guide/s12)의 "예시 화면".

## 5. Claude 가 DevTrack 을 쓰도록 설정 (한 번만)

### 5-1. Claude 에게 기록 규칙 알려 주기

Claude Code 는 `~/.claude/CLAUDE.md` 에 적힌 내용을 모든 대화에서 따릅니다. 이 파일에 아래 내용을 붙여 넣으세요
(파일이 없으면 새로 만듭니다). `~/devtrack` 부분은 2단계에서 받은 위치로 바꿉니다.

```markdown
## DevTrack 작업 기록
- 의미 있는 작업(코드 변경, 코드 리뷰, 데이터 분석, 논문, 시각화, 모델 개발)은 `devtrack` 으로 기록한다. 간단한 질문 · 설명은 기록하지 않는다.
  규칙: ~/devtrack/docs/AGENT_PROTOCOL.md
- 작업 시작 전과 각 단계 후 `devtrack inbox` 로 사용자 입력(새 지시, 메시지, 승인 결정)을 확인한다.
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

> 설정은 **새로 연 Claude 대화부터** 적용됩니다. 이미 열려 있는 대화는 새로 시작하세요.

### 5-2. 코드 리뷰 스킬 설치 (코드 리뷰를 맡길 경우)

`/review-pr` 스킬이 DevTrack 에 리뷰 결과를 올리고, 웹에서 코멘트할 항목을 고를 수 있게 해 줍니다.

```bash
mkdir -p ~/.claude/skills/review-pr
ln -sf ~/devtrack/integrations/claude-skills/review-pr/SKILL.md ~/.claude/skills/review-pr/SKILL.md
```

복사 대신 링크로 걸어 두면 `git pull` 만으로 스킬도 함께 최신이 됩니다.
리뷰 리포트를 메일로 받는 기능은 `pr-review-report` 스킬이 함께 설치되어 있어야 합니다
([자세히](https://github.com/hyungyukang/AI_Dev_Progress_Checker/tree/main/integrations/claude-skills)).

### 5-3. 권한 확인 창 줄이기 (선택)

Claude 가 `devtrack` 명령을 실행할 때마다 확인 창이 뜨면, Claude Code 에서 `/permissions` 를 열어 허용 규칙
`Bash(devtrack:*)` 를 추가하세요. `devtrack` 은 기록만 하고, 커밋 · 푸시는 웹에서 승인된 요청만 실행합니다.

### 5-4. 작업 취소를 바로 전달하기 (권장)

Claude 는 작업 단계가 끝날 때만 DevTrack 을 확인합니다. 그래서 작업 도중에 웹에서 취소 · 삭제해도 그 단계가 끝날 때까지
계속 일할 수 있습니다. **훅**을 설치하면 Claude 가 명령을 실행하거나 파일을 고치기 **직전마다** DevTrack 이 "이 작업이
취소됐는지" 확인하고, 취소됐으면 그 동작을 막고 멈추게 합니다.

`~/.claude/settings.json` 에 추가합니다. 파일이 없으면 새로 만들고, 이미 `"hooks"` 가 있으면 그 안의 `"PreToolUse"` 목록에
항목만 더합니다. `/Users/me/devtrack` 은 2단계에서 받은 위치의 **전체 경로**로 바꿉니다.

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "hooks": [
          { "type": "command", "command": "/Users/me/devtrack/bin/devtrack-hook", "timeout": 10 }
        ]
      }
    ]
  }
}
```

- 훅은 **그 대화가 맡은 작업만** 확인합니다 (한 번에 0.05초 정도). 다른 대화 · 다른 작업에는 영향이 없습니다.
- `devtrack-hook` 은 `devtrack hook` 을 실행하되 문제가 생겨도 Claude 를 막지 않게 만든 실행 파일입니다.
- 막힌 Claude 는 `devtrack inbox --task T-0003 --ack` 로 확인을 기록하고 멈춥니다. 그 뒤로는 평소처럼 동작합니다.
- 설정은 **새로 연 Claude 대화부터** 적용됩니다. Codex 는 11단계 ⑤ 를 보세요.

## 6. 첫 작업 맡기기

방법은 두 가지입니다. 편한 쪽을 쓰면 되고, 둘 다 결과는 웹에 똑같이 나타납니다.

### 방법 A — Claude 채팅으로 지시

VS Code 의 Claude 채팅창에 평소처럼 지시합니다.

```
~/project 의 압력 솔버를 HIP 으로 포팅해줘. 검증까지 하고, 커밋은 승인받고 해줘.
```

Claude 가 DevTrack 에 작업(`T-0001`)을 만들고 진행 상황을 기록하기 시작합니다. 웹 대시보드에 바로 나타납니다.

### 방법 B — 웹에서 지시 (대기 모드)

1. Claude 채팅창에 한 번만 이렇게 말해 둡니다. 이 대화(VS Code 창)는 열어 두세요.
   ```
   웹 지시 기다려줘
   ```
2. 웹 왼쪽 위 **새 작업 지시** 를 누르고 입력합니다.

   | 칸 | 적을 내용 |
   |---|---|
   | 작업 제목 | 한 줄 요약 (예: `PR #42 코드 리뷰`) |
   | 작업 유형 | 코드 개발 · 코드 리뷰 · 논문 작성 · 데이터 분석 · 시각화 · AI 모델 · 기타 — 맞는 메뉴에 표시됩니다 |
   | 담당 AI | 이 작업을 처리할 AI — 그 AI 만 가져갑니다 (Codex 는 [11단계](#/guide/s11)) |
   | 대상 저장소 경로 · 브랜치 | 코드를 고칠 저장소 (선택) |
   | 관련 작업 | 예: 이 분석을 쓸 논문 작업 (선택) |
   | 지시 내용 | 목표, 참고 자료, 완료 기준, 승인받아야 할 시점 — 구체적일수록 좋습니다 |

3. **작업 지시 보내기** 를 누르면 몇 초 안에 Claude 가 자동으로 작업을 시작합니다.

> 대기 모드를 켜지 않았다면, 웹에서 지시한 뒤 Claude 채팅창에 "devtrack inbox 확인하고 진행해줘" 라고 한 번 말하면 됩니다.

## 7. 진행 상황 보기

- **대시보드** — 진행 중 · 승인 대기 · 열린 문제 · 완료 수, 확인이 필요한 항목, 진행 중인 작업 카드, 최근 활동.
- **작업** — 전체 작업 목록. 작업을 누르면 상세 화면으로 갑니다.
- **작업 상세** — 맨 위에 "지금 하는 일"과 진행률, 아래 탭으로 나뉩니다.
  - 개요: 지시 내용, 작업 계획 체크리스트, 최근 기록, 열린 문제, 결과물
  - 진행 기록 · 문제점 · 결과물 · 승인 (논문이면 빌드 탭도 생깁니다)
- **알림** — 오른쪽 위 종 아이콘에 승인 대기 수가 표시되고, 새 승인 요청 · 문제 · 완료가 생기면 화면 구석에 알림이 뜹니다.
  종 아이콘 메뉴에서 **데스크톱 알림 켜기** 를 누르면 다른 창을 보고 있어도 알려 줍니다.
- 화면은 **10초마다 자동으로** 새로 고쳐집니다. 바로 보고 싶으면 오른쪽 위 새로고침 버튼을 누르세요.

## 8. 승인 · 수정 요청 · 거절

Claude 가 확인이 필요한 지점에 오면 **승인 요청** 메뉴와 종 아이콘에 표시됩니다.

1. 요청을 열면 설명과 함께 검토할 내용(코드 변경 diff, 리포트, 그림 등)이 펼쳐집니다.
2. 필요하면 코멘트를 적고 버튼을 누릅니다.

   | 버튼 | 결과 |
   |---|---|
   | **승인** | Claude 가 다음 단계로 진행합니다. 커밋 요청이면 그 내용 그대로 커밋 · 푸시합니다 |
   | **수정 요청** | 코멘트(필수)대로 고친 뒤 새 승인 요청을 올립니다 |
   | **거절** | 그 내용은 진행하지 않습니다 |

3. 목록에서 고르는 요청(예: "코멘트를 작성할 항목을 골라주세요")은 체크한 항목만 진행됩니다.

안전장치: 커밋 요청은 **승인한 diff 와 실제 변경이 조금이라도 다르면 실행되지 않습니다.** 승인 이후 코드가 바뀌면 Claude 가 새 요청을 올립니다.

작업 중에 하고 싶은 말이 있으면 작업 상세 화면의 **에이전트에게 메시지** 칸에 적어 보내세요. Claude 가 다음 단계에서 확인합니다.

### 작업 취소 · 삭제

작업 상세 화면 오른쪽 위의 버튼을 씁니다.

| 버튼 | 결과 | 되돌리기 |
|---|---|---|
| **작업 취소** | 상태가 '취소'로 바뀌고 진행 중 목록에서 빠집니다. 기록 · 결과물은 남고, 결정을 기다리던 승인 요청은 철회됩니다 | 기록이 남아 있음 |
| **삭제** | 기록, 결과물 파일, 승인 요청까지 **모두 지웁니다.** 다른 작업과의 연결도 풀립니다 | 불가능 |

- 삭제 버튼을 누르면 **확인 창**이 뜨고, 작업 ID(예: `T-0003`)를 그대로 입력해야 **영구 삭제** 버튼이 눌립니다.
- 어느 쪽이든 담당 AI 에게 "이 작업을 멈추라"는 알림이 갑니다. 언제 멈추는지는 AI 가 일하는 방식에 따라 다릅니다.
  - `devtrack watch` 로 실행 중인 작업: **바로** 멈춥니다 (실행 중인 AI 를 종료).
  - 대화 중인 Claude · Codex: 훅(5-4, 11단계 ⑤)을 설치했으면 **다음 명령 · 파일 수정 직전에** 멈춥니다.
    훅이 없으면 지금 단계가 끝나고 inbox 를 확인할 때 멈춥니다.
  - AI 가 명령 없이 긴 답을 쓰는 동안에는 끼어들 수 없습니다. 급하면 대화 창의 중지 버튼을 누르세요.
- 보통은 **취소**를 쓰고, 잘못 만든 작업이나 테스트 작업만 **삭제**하세요. 지운 작업 번호는 다시 쓰이지 않습니다.
- 정적 모드에서는 두 버튼 모두 Claude 에게 보낼 메시지를 만들어 줍니다.
- 터미널에서는 `devtrack cancel T-0003 -m "사유"`, `devtrack delete T-0003` (작업 ID 를 다시 입력해 확인).

## 9. 분야별 사용법

### 코드 리뷰

1. Claude 채팅에 `/review-pr https://github.com/<조직>/<저장소>/pull/<번호>` — 또는 웹에서 유형 **코드 리뷰** 로 지시하고 지시 내용에 같은 명령을 적습니다.
2. **코드 리뷰** 메뉴에 리뷰가 올라옵니다.
   - **리뷰 결과 한눈에 보기**: 한 줄 결론, 리뷰 의견, 중요도별 남은 항목(반드시 수정 · 수정 권장 · 개선 제안 · 사소함 · 질문)
   - 항목마다 **쉬운 설명**과 GitHub 코드 위치 링크
   - 아래에 전체 리포트 — 한국어로, 개발자가 아니어도 읽을 수 있게 작성됩니다. 같은 내용의 **PDF** 도 함께 첨부되어 내려받거나 공유할 수 있습니다.
     리포트가 고쳐지면 같은 결과물이 **새 버전(v2, v3…)** 으로 바뀌고, 이전 버전은 결과물 화면의 "이전 버전" 링크로 볼 수 있습니다
3. **PR 코멘트를 작성할 항목**을 체크하고 **선택한 항목으로 진행** 을 누릅니다. 아직 PR 에는 아무것도 올라가지 않습니다.
4. Claude 가 고른 항목의 코멘트 초안을 올리면, 확인하고 **승인** 해야 PR 에 게시됩니다.

### 논문 작성

1. 유형 **논문 작성** 으로 지시합니다. 장 · 절 구성을 지시에 적으면 작업 계획 체크리스트로 표시됩니다.
2. **논문 작성** 메뉴에서 논문별로 확인합니다.
   - **컴파일된 PDF** (쪽수, 정의 안 된 인용 · 참조 경고, 컴파일 실패 시 오류 위치)
   - `.tex` 원고는 코드 대신 **문서 보기** 로 읽을 수 있습니다 (수식 · 표 · 그림 포함, 원본 코드로 전환 가능)
   - 그림, 연결된 데이터 분석, 열린 문제
3. 논문에 쓸 분석이 필요하면 논문 카드의 **이 논문용 분석 지시** 를 누르세요. 분석 작업이 논문과 연결됩니다.

### 데이터 분석 · 시각화

1. 유형 **데이터 분석** 또는 **시각화** 로 지시합니다. 데이터 위치와 무엇을 알고 싶은지 적어 주세요.
2. **데이터 분석** 메뉴에서 그림 갤러리, 표(CSV), 노트북, 요약 리포트를 봅니다. **논문용** 필터는 논문에 연결된 분석만 보여 줍니다.
3. 해석이 필요한 결과는 Claude 가 승인 요청(결과 확인)으로 올립니다.

### 코드 개발

1. 유형 **코드 개발**, 대상 저장소 경로를 적어 지시합니다.
2. 결과는 **승인 요청** 에서 diff 로 확인합니다. 승인하면 Claude 가 커밋하고 푸시합니다 (커밋 해시가 기록됩니다).

## 10. Frontier 에서 쓰기 (정적 웹)

Frontier 에서는 Claude 가 Frontier 에서 일하고, 기록을 `www` 디렉토리에 웹페이지로 공개합니다.

```bash
module load cray-python
git clone git@github.com:hyungyukang/AI_Dev_Progress_Checker.git ~/devtrack
echo 'export PATH="$HOME/devtrack/bin:$PATH"'          >> ~/.bashrc
echo 'export DEVTRACK_DATA=$HOME/devtrack-data'        >> ~/.bashrc
echo 'export DEVTRACK_PUBLISH_DIR=$HOME/www/devtrack'  >> ~/.bashrc   # 웹 공개 위치 (환경에 맞게)
source ~/.bashrc
devtrack publish               # 처음 한 번 (웹 화면을 업데이트했을 때도)
```

- 이후 Claude 가 기록할 때마다 공개 디렉토리에 자동으로 반영됩니다. 5단계(Claude 설정)도 Frontier 에서 똑같이 해 주세요.
- 이 웹은 **읽기 전용(정적 · 읽기 전용)** 입니다. 승인 · 메시지 · 새 작업 지시 버튼을 누르면 **Claude 에게 보낼 메시지**가 나오니,
  복사해서 Frontier 의 Claude 대화에 붙여 넣으세요.
- ⚠️ 공개 디렉토리의 내용(코드 diff, 리포트, 로그)은 주소를 아는 누구나 볼 수 있습니다. 민감한 작업은 접근 제한이 되는 위치를 쓰세요.

## 11. Codex 와 함께 쓰기 (선택)

Claude 와 Codex 를 함께 쓸 수 있습니다. 작업마다 **담당 AI** 를 정하면 그 AI 만 그 작업을 가져갑니다.

| 새 작업 지시의 "담당 AI" | 누가 처리하나 |
|---|---|
| Claude · Codex · Gemini | 고른 AI 만. 그 작업의 메시지 · 승인 결정 · 취소도 그 AI 에게만 갑니다 |
| 누구나 (먼저 시작한 AI) | 먼저 시작한 AI 가 맡고, 그때부터 그 AI 의 작업이 됩니다 |

다른 AI 의 작업을 대신 맡기려면 맡을 AI 에게 "T-0007 을 `--take` 로 가져와서 해줘" 라고 하세요
(`devtrack start T-0007 --take` — 담당 AI 가 바뀌고 기록에 남습니다).

### 11-1. Codex 설정 (한 번만)

**① 기록 규칙** — Codex 는 `~/.codex/AGENTS.md` 에 적힌 내용을 모든 대화에서 따릅니다. 아래 내용을 붙여 넣으세요
(5-1단계의 Claude 규칙과 거의 같고, Codex 에 맞게 몇 줄이 다릅니다). `~/devtrack` 부분은 2단계에서 받은 위치로 바꿉니다.

```markdown
## DevTrack 작업 기록 (Codex)
- DevTrack 에서 나는 "Codex" 다. `devtrack` 은 환경 변수 `DEVTRACK_AGENT=Codex` 로 실행한다
  (~/.codex/config.toml 의 `shell_environment_policy` 에 설정해 두었으면 따로 붙이지 않는다).
  `devtrack` 이 PATH 에 없으면 `~/devtrack/bin/devtrack` 을 쓴다. 규칙: ~/devtrack/docs/AGENT_PROTOCOL.md
- 의미 있는 작업(코드 변경, 코드 리뷰, 데이터 분석, 논문, 시각화, 모델 개발)은 `devtrack` 으로 기록한다. 간단한 질문 · 설명은 기록하지 않는다.
- 작업 시작 전과 각 단계 후 `devtrack inbox` 로 사용자 입력(새 지시, 메시지, 승인 결정)을 확인한다. 나에게 배정된 작업의 입력만 보인다.
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

**② Codex 설정 파일** — `~/.codex/config.toml` 맨 아래에 추가합니다. `/Users/me/devtrack/data` 는 기록 폴더
(`$DEVTRACK_DATA`, 기본 `~/devtrack/data`)의 **전체 경로**로 바꿉니다.

```toml
[shell_environment_policy.set]
DEVTRACK_AGENT = "Codex"                        # Codex 의 기록은 "Codex" 이름으로

[sandbox_workspace_write]
writable_roots = ["/Users/me/devtrack/data"]    # 작업 폴더 밖에 있는 DevTrack 기록 폴더에 쓰기 허용
```

- 파일에 `[shell_environment_policy.set]` 이나 `[sandbox_workspace_write]` 줄이 **이미 있으면** 새로 쓰지 말고 그 아래에 한 줄씩만
  추가하세요. 같은 이름의 줄이 두 번 있으면 Codex 가 설정 파일을 읽지 못합니다.

- Codex 는 안전을 위해 작업 폴더 밖에는 쓰지 못합니다. 위 설정은 DevTrack 기록 폴더 한 곳만 쓰기를 허용합니다.
- 인터넷(`gh` · `git push`)은 Codex 대화창에서는 필요할 때 허락을 묻고, 웹 지시를 처리하는 `devtrack watch` 는 대화창이 없어
  물을 수 없으므로 실행할 때 인터넷을 허용합니다 (막으려면 `devtrack watch --codex --no-network`).

**③ 코드 리뷰 스킬** (코드 리뷰를 맡길 경우) — Claude 와 같은 파일을 링크합니다.

```bash
mkdir -p ~/.codex/skills/review-pr
ln -sf ~/devtrack/integrations/claude-skills/review-pr/SKILL.md ~/.codex/skills/review-pr/SKILL.md
```

**④ `codex` 명령** — 웹 지시를 받는 `devtrack watch` 가 Codex CLI 를 씁니다. 터미널의 `codex` 가 없으면 ChatGPT · Codex 앱,
VS Code 확장에 들어 있는 CLI 중 **가장 새 버전**을 자동으로 찾습니다 (오래된 CLI 는 새 모델을 쓰지 못합니다).
다른 것을 쓰려면 `export DEVTRACK_CODEX=<codex 경로>`. 어떤 CLI 를 쓰는지는 `devtrack watch` 를 켤 때 첫 줄에 표시됩니다.

**⑤ 작업 취소 바로 전달 (훅, 권장)** — Claude 의 5-4 와 같습니다. Codex 앱 · VS Code 에서 대화하며 일하는 Codex 가 작업 도중에
취소 · 삭제를 알 수 있게 합니다. `~/.codex/hooks.json` 을 만듭니다 (이미 있으면 `"PreToolUse"` 목록에 항목만 더하고,
`/Users/me/devtrack` 은 DevTrack 의 전체 경로로 바꿉니다).

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "hooks": [
          { "type": "command", "command": "/Users/me/devtrack/bin/devtrack-hook", "timeout": 10 }
        ]
      }
    ]
  }
}
```

- Codex 는 처음 보는 훅을 **사용자가 확인한 뒤에만** 실행합니다. Codex 앱의 **설정 → Hooks**(터미널 `codex` 에서는 `/hooks`)에서
  이 훅을 확인하고 신뢰로 표시하세요. 훅 내용을 바꾸면 다시 확인해야 합니다.
- `devtrack watch` 로 실행되는 Codex 는 훅 없이도 취소하면 바로 멈춥니다.

> 설정은 **새로 연 Codex 대화부터** 적용됩니다.

### 11-2. Codex 에게 작업 맡기기

**방법 A — Codex 채팅으로 지시.** Codex 앱(또는 터미널의 `codex`)에 평소처럼 지시하면 Codex 가 DevTrack 에 작업을 만들고 기록합니다.

**방법 B — 웹에서 지시 (`devtrack watch`).** Claude 의 대기 모드("웹 지시 기다려줘")는 Claude Code 의 기능이라,
Codex 는 대신 터미널에서 **실행기**를 켜 둡니다.

```bash
devtrack watch --codex
```

1. 웹의 **새 작업 지시** 에서 담당 AI 를 **Codex** 로 고르고 보냅니다.
2. 몇 초 안에 실행기가 Codex 를 실행해 작업을 처리합니다. 터미널 창에 무엇을 처리하는지 표시되고, 끝나면 Codex 의 요약이 진행 기록에 남습니다.
3. Codex 가 승인을 요청하고 끝나면, 웹에서 승인 · 수정 요청 · 메시지를 보낼 때마다 실행기가 Codex 를 다시 실행해 이어서 처리합니다.

- 작업은 **대상 저장소 경로** 폴더에서 실행됩니다. 경로가 없는 작업은 `devtrack watch` 를 실행한 폴더에서 실행됩니다 (`--cwd 폴더` 로 바꿀 수 있음).
- 웹에서 작업을 취소 · 삭제하면 실행 중이던 Codex 를 바로 멈춥니다.
- 이 터미널 창은 열어 두세요 (멈추기: Ctrl+C). 다시 켜면 꺼져 있던 동안 들어온 지시부터 처리합니다.
- Codex 에 옵션을 더 주려면 `--` 뒤에 적습니다: `devtrack watch --codex -- -m <모델>`.

## 12. 자주 묻는 질문 · 문제 해결

**웹에서 지시했는데 아무 일도 일어나지 않아요.**
Claude 는 대화에서 말을 걸어야 움직입니다. 대기 모드("웹 지시 기다려줘")를 켜 두었는지, 그 VS Code 창이 열려 있는지 확인하세요.
아니면 Claude 채팅에 "devtrack inbox 확인하고 진행해줘" 라고 하세요. Codex 라면 `devtrack watch --codex` 가 켜져 있는지 확인하세요.

**Claude(또는 Codex)가 웹에서 지시한 작업을 가져가지 않아요.**
작업의 **담당 AI** 가 다른 AI 로 되어 있는지 보세요 (작업 화면 제목 아래 표시). AI 는 자기에게 배정된 작업만 가져갑니다.
`devtrack inbox --all-agents` 로 모든 AI 의 입력을 볼 수 있고, 대신 맡기려면 `devtrack start <작업 ID> --take` 입니다.

**웹에서 작업을 취소했는데 AI 가 계속 일해요.**
대화 중인 AI 는 작업 단계가 끝날 때만 DevTrack 을 확인합니다. 훅(Claude 5-4, Codex 11단계 ⑤)을 설치하면 다음 명령 · 파일 수정
직전에 멈춥니다. 훅을 설치했는데도 계속하면 ① 훅을 설치한 뒤 **새로 연 대화**인지, ② Codex 라면 훅을 **신뢰로 표시**했는지 확인하세요.
AI 가 명령 없이 긴 답을 쓰는 동안에는 끼어들 수 없으니, 급하면 대화 창에서 직접 중지하세요.

**버튼을 누르면 메시지 복사 창이 떠요.**
정적 모드(오른쪽 위 "정적 · 읽기 전용")입니다. 메시지를 Claude 대화에 붙여 넣거나, 로컬에서는 `devtrack serve` 로 연 주소를 쓰세요.

**DevTrack 이나 스킬을 업데이트했는데 예전처럼 동작해요.**
이미 열려 있던 Claude 대화(대기 모드 대화 포함)는 예전 지침을 기억하고 있을 수 있습니다.
`git pull` 로 업데이트한 뒤에는 Claude 대화를 **새로 열고**, 필요하면 "웹 지시 기다려줘" 를 다시 말해 주세요.

**`devtrack: command not found`**
3단계의 PATH 설정을 확인하고, 터미널과 VS Code 를 다시 여세요.

**웹 화면이 "불러오는 중"에서 멈추거나 이상해요.**
`devtrack serve` 를 실행한 터미널이 열려 있는지 확인하세요. DevTrack 을 업데이트(`git pull`)했다면 `devtrack serve` 를 껐다가 다시 켜세요.

**예시 화면을 먼저 보고 싶어요.**
실제 기록과 섞이지 않게 별도 폴더에 예시를 만들어 엽니다.
```bash
devtrack --data ~/devtrack-demo demo
devtrack --data ~/devtrack-demo serve --port 8766 --open
```

**리뷰 리포트 PDF 가 안 만들어져요.**
PDF 는 컴퓨터에 설치된 Chrome(또는 Chromium · Edge)으로 만듭니다. 브라우저가 없으면 Markdown 리포트만 올라가고 활동 기록에 경고가 남습니다.
다른 위치에 설치했다면 `export DEVTRACK_CHROME=<브라우저 실행 파일 경로>` 를 지정하세요. 이미 올라온 리포트는 `devtrack pdf <리포트.md>` 로 따로 만들 수 있습니다.

**기록은 어디에 있고, 백업은 어떻게 하나요?**
`$DEVTRACK_DATA` (기본 `~/devtrack/data/`) 폴더가 전부입니다. 이 폴더를 복사하면 백업이 됩니다.

**Claude 가 내 승인 없이 커밋할 수 있나요?**
DevTrack 작업은 승인된 요청(`devtrack apply`)으로만 커밋합니다. 승인한 diff 와 실제 변경이 다르면 실행을 거부합니다.
다만 채팅에서 직접 "커밋해줘" 라고 지시하면 그대로 따릅니다.

## 13. 명령 한눈에 보기

사용자가 직접 쓰는 명령:

| 명령 | 하는 일 |
|---|---|
| `devtrack serve --open` | 웹 대시보드 열기 (로컬) |
| `devtrack publish` | 정적 웹으로 배포 (Frontier `www`) |
| `devtrack list` · `devtrack show T-0001` | 터미널에서 작업 목록 · 상세 보기 |
| `devtrack watch --codex` | 웹에서 Codex 에게 온 지시를 Codex 로 처리 (11단계) |
| `devtrack --data <폴더> demo` | 예시 데이터 만들기 |

Claude 가 쓰는 명령 (참고): `new` 작업 생성 · `step` 지금 하는 일 · `issue` 문제 · `artifact` 결과물 · `review` 리뷰 리포트 ·
`request` 승인 요청 · `apply` 승인된 커밋 실행 · `inbox` 사용자 입력 확인 (자기에게 배정된 작업만) ·
`start --take` 다른 AI 의 작업 가져오기 · `done` 완료 · `hook` 취소 · 삭제를 일하는 중인 AI 에 전달 (5-4).
전체 설명은 `devtrack --help` 와 [에이전트 규칙](https://github.com/hyungyukang/AI_Dev_Progress_Checker/blob/main/docs/AGENT_PROTOCOL.md)을 보세요.
