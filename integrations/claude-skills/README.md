# Claude Code 스킬 연동

DevTrack 에 기록하도록 고친 Claude Code 스킬입니다. `devtrack` 명령이 PATH 에 있을 때만 DevTrack 단계를 실행하고,
없으면 원래 스킬과 똑같이 동작합니다 (`no-devtrack` 인자로 끌 수도 있습니다).

| 스킬 | 하는 일 | DevTrack 에서 보이는 것 |
|---|---|---|
| `review-pr` | 다른 사람의 PR 리뷰 → 쉬운 말 리포트 + 검토 항목 R1, R2… → 고른 항목만 코멘트 초안 → 승인 후 게시 | 코드 리뷰 화면: 리뷰 결과 한눈에 보기, 리포트(md/pdf), **코멘트할 항목 체크 → 진행**, 코멘트 초안 게시 승인 |
| `visualize-doc` | 긴 논문 · 문서(PDF · Word · LaTeX · Markdown)를 **짜임새 있는 요약 + 도식 1–3개**로 — 내용에 맞는 배치(흐름 · 순환 · 계층 · 두 갈래 · 중심 · 표)를 골라 Claude 가 SVG 로 직접 그리고 `devtrack svgcheck` 로 점검. [visual-architect](https://github.com/WilsonWukz/paper-visualizer-skill) (MIT) 를 고친 것 | 작업 화면의 **시각화** 칸 (도식 · 시각화 요약 md/PDF), 메시지 칸의 **시각화 요청** 버튼 |
| `write-paper` | DevTrack 논문 작업의 원고 쓰기 · 고치기 — 연결된 분석 작업의 결과물로 **근거 장부**를 만들고 그 근거로만 개요 → 초안, 숫자 · 방법-결과 대조, 내 `.bib` 로만 인용, 근거 없는 것은 `[[MISSING]]`, `devtrack latex` 빌드, 심사 답변서. [K-Dense scientific-writing](https://github.com/K-Dense-AI/scientific-agent-skills) (MIT) 을 고친 것 | 논문 작업의 원고 · 근거 장부 · PDF, "원고를 확인해 주세요" 요청 |
| `make-slides` | 논문 · 문서 · 작업으로 **PowerPoint 발표 자료** — 이야기 구성 · 시간 배분, 작업 · 연결된 작업의 그림, 발표자 노트, `devtrack pptx` (표준 라이브러리) + 미리보기 PDF 로 넘침 점검. K-Dense scientific-slides (MIT) 를 고친 것 (이미지 생성 API · 문헌 검색 없음) | 작업의 결과물: pptx + 같은 이름의 미리보기 PDF (웹에서 함께 보임) |
| `make-poster` | 학회 **포스터** (A0 · A1 · 48x36in …) — 핵심 메시지 1–3개 · 300–800단어, 제목 띠 + 2–4단 블록, 단 채움 · 그림 해상도 점검, LaTeX 포스터도 선택. K-Dense latex-posters · pptx-posters (MIT) 를 고친 것 | 작업의 결과물: pptx + 미리보기 PDF |
| `review-doc` | 논문 · 보고서 · 제안서(PDF · Word · LaTeX) 리뷰 → 문서 요약 · 목적 대비 미비한 부분 · 논리 · 문장/맞춤법 (과학 논문은 형식 · 선행 연구와의 차이 · 결론의 근거까지) → 보완 · 수정 항목 R1, R2… 를 **PDF 리포트**로 | 문서 리뷰 화면: 리뷰 의견 · 문서 요약 · 영역별 평가 · 원문 → 수정 제안, 리뷰 리포트(PDF) · 원본 보기, 리포트 확인 요청 |

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

`review-doc` 은 다른 스킬 없이 혼자 동작합니다 (메일 기능 없음).

```bash
for s in review-doc visualize-doc write-paper make-slides make-poster; do
  mkdir -p ~/.claude/skills/$s
  ln -sf <DevTrack 저장소>/integrations/claude-skills/$s/SKILL.md ~/.claude/skills/$s/SKILL.md
done
```

`write-paper` · `make-slides` · `make-poster` 는 K-Dense 의 공개 스킬(MIT, 각 폴더의 `LICENSE-k-dense.txt`)을 고친 것입니다. 원래 스킬의
외부 연결(이미지 생성 API · 문헌 검색 · arXiv 접속)과 "사용자의 원고 · 발표에 원작자 논문을 인용하라" 는 지시는 뺐고, pptx 는 pip 없이
DevTrack 의 `devtrack pptx` 로 만듭니다. `review-doc` 과 저널 심사위원 지시에는 K-Dense peer-review 의 심사 기준을 더했습니다.

`visualize-doc` 도 혼자 동작합니다. 도식 미리보기(`devtrack svgcheck --png`)와 PDF 는 헤드리스 Chrome 이 있을 때만 만들고, 없으면 점검 결과와 Markdown 만 냅니다.

**Codex 에서도** 같은 파일을 씁니다 (Codex 는 `allowed-tools` 를 무시하고, `${CLAUDE_SKILL_DIR}` 는 이 파일이 있는 폴더로 읽습니다).

```bash
for s in review-pr review-doc visualize-doc write-paper make-slides make-poster; do
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

## 사용 흐름 (`visualize-doc`)

```
(웹) 논문 · 문서 리뷰 작업의 메시지 칸 → '시각화 요청' (초점을 덧붙여도 됨) → 보내기
  → (AI 의 inbox) [메시지] … → 문서 시각화 요청: /visualize-doc T-0010
  → (Claude) devtrack doc T-0010 로 문서 찾기 → 읽기 → 짜임새 있는 요약 → 도식 설계(배치 · 구역 · 연결)
  → (Claude) SVG 그리기 → devtrack svgcheck --png 로 점검 · 미리보기 확인 → 시각화 요약(md → PDF)
  → (Claude) devtrack artifact … --visual --replace → 작업 화면 '시각화' 칸
```

채팅에서는 `/visualize-doc ~/papers/draft.pdf 방법 부분만` 이나 `/visualize-doc T-0010`. 원래 스킬(이미지 생성 AI 용 프롬프트)과 달리
도식을 직접 그려 문서가 밖으로 나가지 않습니다. 원래 형식의 프롬프트가 필요하면 `prompt` 인자를 붙이면 부록으로 씁니다.
