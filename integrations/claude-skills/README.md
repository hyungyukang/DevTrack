# Claude Code 스킬 연동

DevTrack 에 기록하도록 고친 Claude Code 스킬입니다. `devtrack` 명령이 PATH 에 있을 때만 DevTrack 단계를 실행하고,
없으면 원래 스킬과 똑같이 동작합니다 (`no-devtrack` 인자로 끌 수도 있습니다).

| 스킬 | 하는 일 | DevTrack 에서 보이는 것 |
|---|---|---|
| `review-pr` | 다른 사람의 PR 리뷰 → 쉬운 말 리포트 + 검토 항목 R1, R2… → 고른 항목만 코멘트 초안 → 승인 후 게시 | 코드 리뷰 화면: 리뷰 결과 한눈에 보기, 리포트(md/pdf), **코멘트할 항목 체크 → 진행**, 코멘트 초안 게시 승인 |
| `review-doc` | 논문 · 보고서 · 제안서(PDF · Word · LaTeX) 리뷰 → 문서 요약 · 목적 대비 미비한 부분 · 논리 · 문장/맞춤법 (과학 논문은 형식 · 선행 연구와의 차이 · 결론의 근거까지) → 보완 · 수정 항목 R1, R2… 를 **PDF 리포트**로 | 문서 리뷰 화면: 리뷰 의견 · 문서 요약 · 영역별 평가 · 원문 → 수정 제안, 리뷰 리포트(PDF) · 원본 보기, 리포트 확인 요청 |

### 외부 스킬을 DevTrack 에 맞게 고친 것 (MIT, [NOTICE](NOTICE.md))

[alirezarezvani/claude-skills](https://github.com/alirezarezvani/claude-skills) 에서 코드 개발 · 코드 리뷰 · 논문 · 분석에 쓸 만한 것만 골라
다시 썼습니다. 공통으로: **사용자 승인 없이는 커밋하지 않고** (`devtrack request --commit` → 승인 → `apply`), diff · 리뷰는 **바뀐 소스만**
(빌드 결과물 제외), 리포트는 DevTrack 에 결과물로 (리포트는 **PDF** 도), 과학 계산 · HPC 코드에 맞춘 점검, 결과는 한국어.
원본의 스크립트 · 훅 · 에이전트는 넣지 않았습니다 (지시문만).

| 스킬 | 언제 | 하는 일 | DevTrack 에서 보이는 것 |
|---|---|---|---|
| `prove-fix` | 버그 · 틀린 값 · NaN · 충돌을 고칠 때 | 원인을 증거로 확인(확인됨 · 추정 · 모름) → 지금 코드에서 **실패하는** 검사(단위 테스트 · 작은 재현 실행) → 최소 수정 → **수정을 되돌려 다시 실패 확인** → 전체 검사. 되돌리기는 바꾼 파일만 (`git stash` · `reset` 안 씀), 계산 노드 실행은 먼저 허락 | 단계별 기록(실패 · 통과 출력), `버그 증명 기록`, `수정 diff`, 커밋 승인 요청 |
| `surgical-check` | 개발을 마치고 커밋 승인을 요청하기 전 | 바뀐 줄이 모두 요청에서 나왔는지 hunk 마다 점검 → 내가 만든 잡음(서식 · 이름 바꾸기 · 디버그 출력 · 딸려 온 파일) 되돌림 → 가정 · 단순함 · 검증 정리 | `커밋 전 변경 점검` 이 붙은 커밋 승인 요청 (리뷰어가 정해진 작업은 `review-request`) |
| `adversarial-review` | 거친 두 번째 의견이 필요할 때 · AI 끼리 리뷰의 리뷰어 | **바뀐 코드만** 세 관점으로: 파괴자(경계 · 초기화 · halo · MPI · GPU · 재시작) · 수치 감사관(단위 · 보존 · 정밀도 · 재현성 · 기본값) · 새 팀원(읽기 쉬움 · 테스트) → R1… | 코드 리뷰 화면: 검토 항목 · 리포트(md · **PDF**), 리뷰 반복이면 `review-done` 판정 |
| `deep-read` | 논문을 꼼꼼히 읽을 때 · 내 원고 · 분석 리포트를 내기 전 (`check`) | 중심 주장 → 논증 구조 → **근거 대조표**(주장 ↔ 그림 · 표 · 수치 · 쪽, 근거 강도) → 가정 · 반론 · 한계. `check` 는 근거가 부족한 주장을 C1… 로 | `정독 리포트 (PDF)` · 원문 결과물, (check) 보완할 항목 고르기 |

## 설치

`review-pr` 는 `pr-review-report` 스킬의 메일 스크립트(`../pr-review-report/scripts/send_report.py`)를 함께 씁니다.
같은 `~/.claude/skills/` 아래에 두세요.

```bash
# 기존 스킬 백업 후 교체
cp ~/.claude/skills/review-pr/SKILL.md ~/.claude/skills/review-pr/SKILL.md.bak
cp integrations/claude-skills/review-pr/SKILL.md ~/.claude/skills/review-pr/SKILL.md

# HPC 등 원격 서버에서도 같은 방법으로 (DevTrack 저장소를 clone 한 위치 기준)
cp ~/devtrack/integrations/claude-skills/review-pr/SKILL.md ~/.claude/skills/review-pr/SKILL.md
```

`review-doc` 과 외부 스킬 네 개는 다른 스킬 없이 혼자 동작합니다 (메일 기능 없음). 필요한 것만 링크하세요.

```bash
mkdir -p ~/.claude/skills/review-doc
ln -sf <DevTrack 저장소>/integrations/claude-skills/review-doc/SKILL.md ~/.claude/skills/review-doc/SKILL.md

for s in prove-fix surgical-check adversarial-review deep-read; do
  mkdir -p ~/.claude/skills/$s
  ln -sf <DevTrack 저장소>/integrations/claude-skills/$s/SKILL.md ~/.claude/skills/$s/SKILL.md
done
```

**Codex 에서도** 같은 파일을 씁니다 (Codex 는 `allowed-tools` 를 무시하고, `${CLAUDE_SKILL_DIR}` 는 이 파일이 있는 폴더로 읽습니다).

```bash
for s in review-pr review-doc prove-fix surgical-check adversarial-review deep-read; do
  mkdir -p ~/.codex/skills/$s
  ln -sf <DevTrack 저장소>/integrations/claude-skills/$s/SKILL.md ~/.codex/skills/$s/SKILL.md
done
```

`devtrack` 이 PATH 에 있어야 DevTrack 모드가 켜집니다: `ln -s <DevTrack 저장소>/bin/devtrack ~/bin/devtrack`
(`~/bin` 이 PATH 에 있어야 함). 기록 위치는 `devtrack config data <폴더>`(또는 `$DEVTRACK_DATA`)로 정합니다.

스킬의 `allowed-tools` 에 `Bash(devtrack *)` 가 들어 있어 스킬 실행 중 `devtrack` 명령은 매번 묻지 않고 실행됩니다.
원하지 않으면 그 항목을 지우세요.

## 원래 스킬과 달라진 점 (`review-pr`)

- **DevTrack 기록** (Step 0b): 리뷰 작업 생성(같은 PR 을 다시 리뷰하면 같은 작업 재사용), 단계마다 `step`/`check`,
  리포트와 검토 항목 등록(`devtrack review --findings`), 코멘트할 항목 선택 요청(`request --choices`),
  코멘트 초안 게시 승인 요청, 게시 후 상태 갱신과 완료.
- **웹 승인도 명시적 승인으로 인정**: 사용자가 웹에서 결정한 경우(`decided_by: user`)만. 채팅 지시는 그대로 유효.
- **일반 사용자도 읽기 쉬운 리포트**: 맨 앞 `한 줄 결론`, 비개발자용 `한눈에 보기`, 요약표의 `쉬운 설명` 열,
  항목마다 `쉽게 말하면`, 중요도 한국어 표기(반드시 수정 / 수정 권장 / 개선 제안 / 사소함 / 질문), `용어 풀이`.
  개발자용 상세는 그대로 유지.
- **검토 항목 JSON 형식 정리**: `~/.pr-review/<owner>-<repo>/review-PR-<n>.json` 을 DevTrack 이 그대로 읽습니다
  (`pr`, `verdict`, `summary`, `findings[]` — `id`, `severity`, `title`, `plain`, `file`, `line`, `confidence`, `status`).

## 사용 흐름

```
/review-pr https://github.com/org/repo/pull/42
  → (Claude) PR 수집 · 검토 · 리포트 작성 → DevTrack 에 리포트 + R1…R5 등록
  → (웹 · 코드 리뷰) 리뷰 결과 한눈에 보기 확인 → 코멘트할 항목 체크 → "선택한 항목으로 진행"
  → (Claude) 고른 항목만 GitHub 코멘트 초안 → 게시 승인 요청
  → (웹) 초안 확인 → 승인 / 수정 요청 / 거절
  → (Claude) 승인되면 PR 에 리뷰 1건으로 게시 → 상태 갱신 · 완료
```

웹 대신 채팅으로 "R1, R3 코멘트 초안 써줘", "이대로 게시해줘" 라고 해도 됩니다 (DevTrack 에 결정으로 기록됨).
정적 모드(웹 공개 폴더)에서는 웹 버튼이 Claude 에게 붙여넣을 `[DevTrack 결정] … 선택: R1, R3` 메시지를 만들어 줍니다.

## 사용 흐름 (`review-doc`)

```
/review-doc ~/papers/draft.pdf JFM 투고 전 점검
  → (Claude) 원본을 '검토할 문서'로 등록 → 읽기 · 요약 → 목적 대비 미비한 부분 · 논리 · 문장/맞춤법 점검
  → (Claude) 리포트(md → PDF) + 검토 항목 R1… 등록 → 리포트 확인 요청
  → (웹 · 문서 리뷰) 리뷰 결과 한눈에 보기 · PDF 확인 → 승인 / 수정 요청
  → (선택) "R1, R3 반영한 수정본 만들어 줘" → 원본은 그대로 두고 수정본을 따로 올림
```

웹에서 유형 **문서 리뷰** 로 지시해도 됩니다 (문서 경로 · 종류 · 목적 칸). 원고는 기밀일 수 있어, 스킬은 문서 내용을 검색이나 외부 서비스에 넣지 않습니다.

## 사용 흐름 (외부 스킬)

```
(코드 개발 작업 중) /prove-fix 마스크가 정수라 부분 셀 플럭스가 잘림
  → 원인 확인 → 실패하는 검사 → 수정 → 되돌려 실패 확인 → 전체 검사 → '버그 증명 기록' · '수정 diff'
/surgical-check
  → 잡음 되돌림 → '커밋 전 변경 점검' 을 붙여 커밋 승인 요청 (웹에서 승인해야 커밋 · 푸시)
(리뷰 작업 · 리뷰어 AI) /adversarial-review T-0042
  → 바뀐 코드만 세 관점 리뷰 → R1… · PDF 리포트 → review-done approve | changes
(논문 · 분석 작업 중) /deep-read paper/main.tex check
  → 근거 대조표 · 보완할 항목 C1… → '정독 리포트 (PDF)' → 고칠 항목 선택
```

웹의 새 작업 지시에 `/prove-fix …` 처럼 적어도 됩니다 — DevTrack 기록 규칙대로 AI 가 그 스킬을 불러 진행합니다.
AI 끼리 리뷰에서 리뷰어가 이 스킬을 쓰게 하려면 개발 AI 가 `devtrack review-request <T> -m "/adversarial-review 로 리뷰"` 처럼
메모로 남깁니다 — 메모는 리뷰 작업의 지시에 그대로 들어갑니다. 개발 작업 지시에 "리뷰는 /adversarial-review 로" 라고 적어 두면 개발 AI 가 그렇게 요청합니다.
