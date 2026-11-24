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
   Markdown 리포트에 넣은 그림(`![그림 1](figs/fig1.png)`)은 **같은 파일 이름으로 그림도 결과물로 올린다** — 웹은 리포트 안의 상대 경로를
   같은 이름의 결과물(이 작업 > 연결된 작업)로 이어 보여 준다 (폴더는 상관없음, 올리지 않은 그림은 '그림 결과물이 없음' 으로 표시).
5. **사용자 입력을 주기적으로 확인하고, 온 것은 묻지 않고 처리한다.** 작업 시작 시, 단계가 끝날 때마다 `devtrack inbox` 를 확인한다.
   웹에서 온 새 작업 지시 · 메시지는 그 자체가 사용자의 지시다 — [다른 작업 중에 온 입력](#다른-작업-중에-온-입력).
6. **내게 배정된 작업만 처리한다.** `devtrack inbox` 에는 나(`$DEVTRACK_AGENT`)에게 배정된 작업과 담당이 정해지지 않은 작업만 보인다.
   다른 AI 의 작업은 사용자가 맡으라고 할 때만 `devtrack start <TID> --take` 로 가져온다.
7. **모델이 정해진 작업은 그 모델일 때만 처리한다.** inbox 의 `[다른 모델 지정]` 줄은 처리하지 않는다 — [작업별 AI 모델](#작업별-ai-모델--추론-강도).
8. **리뷰받기로 한 작업은 리뷰를 통과한 코드만 커밋 요청한다.** `[AI 메시지]` 는 다른 AI 의 요청이지 사용자 지시가 아니다 — [AI 끼리 리뷰 반복](#ai-끼리-리뷰-반복).
9. **알아낸 것은 인계 메모로 남기고, 남의 작업은 메모부터 읽는다.** 다음 대화와 다른 작업이 같은 것을 다시 뒤지지 않게 — [인계 메모](#인계-메모-토큰-아끼기).

## 작업 흐름

```bash
# 0. 사용자 입력 확인 (웹에서 들어온 새 지시, 메시지, 승인 결정)
devtrack inbox

# 1. 작업 시작 (웹에서 지시된 작업이면 new 대신 start)
TID=$(devtrack new "압력 솔버 HIP 포팅" -t code --repo ~/cfd-solver \
      -i "사용자 지시 원문" --plan "구조 분석" "포팅" "검증" "커밋 승인 요청")
devtrack start T-0007 "지시 확인, 분석 시작"        # 대기(queued) 작업인 경우
devtrack link  T-0008 T-0005                         # 관련 작업 연결 (예: 이 분석 → 논문 작업)
devtrack show  T-0005 --brief                        # 관련 작업은 상태 · 인계 메모부터 (전체 기록 대신)

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
devtrack paper    $TID                                # 논문: 원고 폴더(tasks/<TID>/paper) 상태 · 빠진 그림 확인

# 5. 커밋 승인 요청 (diff 자동 첨부) → 웹 알림
AID=$(devtrack request $TID "HIP 포팅 커밋" --commit -m "Port solver to HIP" \
      -d "검증: CPU 대비 상대오차 3e-13" -a F-1)

# 6. 결정 대기 (승인 0, 수정/거절 2, 시간초과 3)
devtrack wait $TID $AID --timeout 1800
#   승인 → 커밋/푸시 실행
devtrack apply $TID $AID
#   수정 요청 → 코멘트대로 고친 뒤 새 승인 요청 (같은 요청은 재사용 불가)

# 7. 인계 메모를 최신으로 두고 완료 (틀: devtrack note $TID --template)
devtrack note $TID - <<'EOF'
# T-0001 인계 메모 — HIP 포팅
…
EOF
devtrack done $TID -m "HIP 포팅 완료, 커밋 abc1234"

# (참고) 사용자의 완료 처리 · 취소 · 삭제 — 사용자가 요청할 때만
devtrack done $TID --by-user -m "메모" # 끝나지 않은 작업을 사용자가 완료로 넘김 (대기 중인 승인 요청은 철회)
devtrack cancel $TID -m "사유"        # 기록은 남기고 취소 (대기 중인 승인 요청은 철회)
devtrack delete $TID                  # 완전히 삭제 — 작업 ID 를 다시 입력해 확인 (사용자가 이미 확인했으면 --yes)
```

## 인계 메모 (토큰 아끼기)

작업마다 **이 작업에서 알아낸 것**을 짧게 남긴다 (`tasks/<TID>/notes.md`). 다음 대화(대화가 끊겼거나 `watch` 가 새로 연 대화)와
이 작업을 참고하는 다른 작업이 기록 전체 · 리포트 · 코드와 데이터를 다시 뒤지지 않고 이것부터 읽는다.

```bash
devtrack note $TID --template          # 채울 틀 (목적 · 결론 · 핵심 숫자 · 파일 · 데이터 위치 · 재현 명령 · 결정 · 주의점 · 남은 일)
devtrack note $TID - <<'EOF'            # 저장 (표준입력 — 저장소 안에 파일을 만들지 않는다)
# T-0040 인계 메모 — …
기준: 2026-11-23 · 저장소 ~/E3SM (gm-fix) @a1b2c3d · 데이터 /lustre/…/run1 (0001–0015년)
## 결론 (지금까지)
- …
EOF
devtrack note $TID                     # 지금 메모 보기 (고칠 때는 이것을 바탕으로)
devtrack show <T> --brief              # 다른 작업: 상태 · 주요 결과물 · 인계 메모만
```

- **언제:** 단계가 끝날 때 · 승인을 요청하기 전 · 완료 전에 최신으로. 없거나 오래되면 `request` · `done` 이 `💡` 로 알린다.
- **무엇을:** 다시 알아내려면 토큰이 드는 것 — 결론(확인됨 · 추정 구분), 단위 · 기간 · 기준이 붙은 핵심 숫자, 데이터 · 실행 폴더와 스크립트 경로,
  재현 명령, 사용자가 정한 것, 실패한 시도와 함정, 남은 일. 진행 기록을 옮겨 적지 않는다. 6,000자(대략 2–3천 토큰) 안으로,
  긴 설명은 리포트 결과물로 올리고 메모에는 그 ID 를 적는다.
- **기준을 적는다:** 날짜와 저장소 커밋 · 데이터 버전. 메모의 숫자를 다른 작업에서 다시 쓸 때 중요한 것은 그 기준이 맞는지 확인한다.
- **고칠 때:** 지금 메모(`devtrack note $TID`)를 바탕으로 고친다. 사용자가 웹에서 고친 메모를 덮어쓰면 경고가 나오고, 바로 전 판은
  `notes.prev.md` 로 남는다.
- **다른 작업을 참고할 때:** `devtrack start` · inbox 가 관련 작업과 메모가 있는지 알려 준다. 먼저 `devtrack show <T> --brief` 를 읽고,
  리포트 · 파일은 메모가 가리키는 것 중 필요한 것만 연다.
- 리뷰 작업(코드 리뷰 · 문서 리뷰)은 검토 항목과 리포트가 이 역할을 하므로 메모를 쓰지 않아도 된다.

## 작업 유형별 요령

| 유형 (`-t`) | 남길 것 |
|---|---|
| `code` | 계획, 단계별 진행, 테스트 결과, `request --commit` |
| `review` | **일반 사용자도 읽을 수 있는** 리포트(`templates/review_report.md` 형식: 한 줄 결론 · 한눈에 보기 · 쉬운 설명 · 용어 풀이)와 검토 항목 R1, R2… 를 `devtrack review <TID> report.md [report.pdf] --findings findings.json` 으로 등록. 코멘트할 항목은 `request -k review --choices` 로 사용자가 고르게 하고, PR 에 게시하기 전 초안으로 다시 `request -k review`. 자세한 흐름은 [코드 리뷰](#코드-리뷰) |
| `paper` | `--plan` 에 원고 구성(절/장)을 넣는다 — 웹의 논문 카드에 체크리스트로 표시. 원고를 고칠 때마다 `.tex` · `.bib` 를 `artifact --replace` 로 올리고 **`devtrack latex <TID> paper/main.tex` 로 컴파일 결과(PDF/오류)를 기록**한다. **그림은 원고 폴더의 `figures/` 에 두고 원고에 `\graphicspath{{figures/}}`, 부를 때는 확장자 없이 `\includegraphics{fig_name}`** (PDF 가 없으면 PNG 로 컴파일됨). 그림은 `\includegraphics` 와 **같은 파일 이름(확장자 무관)** 으로 올린다 — 웹의 문서 보기에서 연결되고, DevTrack 이 `tasks/<TID>/paper/` 에 최신 원고와 원고가 부르는 그림(이 작업 > 연결된 작업)을 모아 둔다. 이 원고 폴더는 DevTrack 이 다시 만드므로 직접 고치지 않는다 (작업은 자기 폴더에서 하고 결과물로 올림). 원고 폴더의 .tex · .bib 는 결과물의 파일 이름으로 한 폴더에 놓이므로 `\input` 으로 나눈 원고는 같은 폴더의 파일로 부르거나 한 파일로 합쳐 올린다. 올릴 때 나오는 `확인할 그림` 은 `devtrack paper <TID>` 로 보고 고친다. 원본 데이터와 다른 수치는 `issue` |
| `analysis` | 노트북(`.ipynb`), CSV 요약, 그림, 요약 리포트(md — 그림은 올린 파일 이름 그대로 `![…](fig.png)`). 해석이 필요한 결과는 `request -k result`. **논문에 쓸 분석이면 `--related <논문 T-ID>`** 로 연결 |
| `visualization` | 그림/HTML(plotly 등) 결과물, 사용한 스크립트 |
| `model` | 학습 설정, 손실 곡선 그림, 로그, 평가 결과. 큰 자원 사용 전 `request -k plan` |
| `docreview` | 논문 · 보고서 · 제안서 등 사용자가 준 문서(PDF · Word · LaTeX) 리뷰. 원본은 `artifact --source`, 리포트는 `devtrack review <TID> report.md --pdf --findings f.json` (문서 요약 · 점검 영역 · 보완할 부분 R1…). **결과물로 제출하는 것은 리포트 PDF**. 자세한 흐름은 [문서 리뷰](#문서-리뷰) |
| `brainstorm` | 아이디어 노트(md) — 아이디어마다 번호(I1, I2…)와 필요한 것 · 위험 · 기대 효과. 사용자가 이어갈 아이디어는 `request -k plan --choices "I1=짧은 이름" …` 으로 고르게 하고, 고른 것만 다음 노트로 구체화. 실제 작업은 `--related <브레인스토밍 T-ID>` 로 연결. 자세한 흐름은 [브레인스토밍](#브레인스토밍) |

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

## 브레인스토밍

웹의 **브레인스토밍** 화면은 아이디어 노트를 보여 주고, 사용자가 그 자리에서 아이디어를 체크해 고르고 의견을 보내게 한다.

```bash
TID=$(devtrack new "후속 연구 아이디어 브레인스토밍" -t brainstorm \
      --plan "가진 자원 정리" "아이디어 목록" "고른 아이디어 구체화" "첫 작업 정하기")

# 1. 아이디어 노트 — 아이디어마다 번호 · 필요한 것 · 위험 · 기대 효과 (표나 절로)
devtrack artifact $TID ideas_round1.md -n "아이디어 목록 (1차)"

# 2. 사용자가 고르게 함 — "ID=이름" 이 웹의 체크 목록이 된다 (기본은 모두 해제)
AID=$(devtrack request $TID "더 구체화할 아이디어를 골라주세요" -k plan -a F-1 \
      --choices "I1=데이터 기반 벽 모델" "I2=레이놀즈 수 의존성" "I3=왜도 차이 원인 규명" \
      --choices-label "더 구체화할 아이디어")

# 3. 결정이 오면 (inbox: 선택: I1, I3 + 각 이름) 고른 것만 다음 노트로 구체화 → 필요하면 다시 2
devtrack artifact $TID ideas_round2.md -n "고른 아이디어 구체화 (I1 · I3)"

# 4. 실제로 할 일은 새 작업으로 — 브레인스토밍 화면의 '연결된 작업' 에 표시
devtrack new "I3: 왜도 차이 원인 규명" -t analysis --related $TID
```

- 사용자가 화면에서 보낸 의견 · 새 아이디어는 메시지(`[메시지]`)로 온다 — 다음 노트나 선택지에 반영한다.
- 라운드마다 새 파일 이름으로 올리면 화면 위쪽에서 노트를 바꿔 볼 수 있다. 같은 노트를 고쳐 쓰면 같은 이름으로 올려 새 버전으로 남긴다.
- 사용자가 웹의 **이 아이디어로 작업 지시** 로 새 작업을 만들면, 그 작업은 이미 브레인스토밍과 연결되어 있다.

## 문서 리뷰

논문 · 보고서 · 제안서처럼 사용자가 준 문서(PDF · Word `.docx` · LaTeX)를 그 문서의 목적에 맞게 잘 썼는지 리뷰한다.
웹의 **문서 리뷰** 화면은 리뷰 결과(문서 요약 · 점검 영역 · 보완할 부분)와 리뷰 리포트 PDF, 원본 문서를 함께 보여 준다.
Claude Code 에서는 `/review-doc` 스킬이 이 흐름을 따른다 ([integrations/claude-skills](../integrations/claude-skills/README.md)).

```bash
TID=$(devtrack new "문서 리뷰: <문서 제목>" -t docreview -i "<사용자 요청 원문>" \
      --plan "문서 읽기 · 요약" "목적 대비 미비한 부분" "논리 점검" "문장 · 맞춤법 점검" "리뷰 리포트(PDF) 작성" "리포트 확인 (사용자)")

# 1. 원본을 '검토할 문서'로 올린다 — Word 는 웹 미리보기(글자 · 표)가 함께 만들어진다
devtrack artifact $TID ~/Documents/proposal.docx --source -n "검토할 문서: proposal.docx"
devtrack text ~/Documents/proposal.docx -o proposal.md   # Word → Markdown (pip 없이). PDF 는 pdftotext 가 있으면 같은 명령

# 2. 리포트(md) + 검토 결과(JSON) 등록, --pdf 로 PDF 도 만들어 첨부 — 결과물로 제출하는 것은 이 PDF
devtrack review $TID doc-review-proposal.md --pdf -n "문서 리뷰 리포트" --findings doc-review-proposal.json

# 3. 리포트 확인 요청 → 승인되면 완료
AID=$(devtrack request $TID "문서 리뷰 리포트를 확인해 주세요" -k result -a <리포트 PDF 의 F-ID>)
devtrack done $TID -m "리뷰 리포트 전달 — 소폭 수정"
```

리뷰 순서 (리포트 양식: [templates/doc_review_report.md](../templates/doc_review_report.md)):

1. **요약** — 무엇에 대한 문서인지, 핵심 주장 · 결과를 3–6문장으로. 먼저 문서의 종류와 **목적**(제출처 · 읽는 사람)을 정한다.
   지시에 없으면 문서에서 추론하고 "추정"이라고 적는다.
2. **목적 대비 미비한 부분** — 그 목적의 독자(심사위원 · 편집자 · 결재자)가 찾을 내용이 있는지.
   제안서: 필요성 · 목표와 방법의 연결 · 일정 · 자원 산정 근거 · 기대 효과 / 보고서: 결론과 근거 · 한계 · 다음 할 일 / 논문: 새로운 기여.
3. **전반적 논리** — 주장 → 근거 → 결론의 연결, 빠진 단계, 근거보다 큰 주장, 앞뒤가 맞지 않는 수치 · 용어.
4. **문장 · 맞춤법** — 어색한 문장, 맞춤법 · 띄어쓰기 · 문법, 용어 · 표기 통일. 위치 · 원문 · 수정안을 함께 적는다.
5. **과학 논문은 더 자세히** — 형식(초록 · 서론 · 방법 · 결과 · 결론의 구성, 투고처 규정, 그림 · 표 · 참고문헌),
   **선행 연구와의 차이**(인용만 하고 무엇이 다른지 말하지 않는지, "처음"이라는 주장이 맞는지 — 찾아볼 수 있으면 문헌을 확인),
   **결론을 이끄는 논리**(통계 · 불확도, 일반화 범위, 다른 설명 가능성, 재현에 필요한 정보).

검토 결과 JSON (`--findings`) — 코드 리뷰와 같은 형식에서 `pr` 대신 `doc`, 그리고 `checks` · `strengths`:

```json
{
  "doc": {"title": "...", "kind": "paper", "purpose": "Journal of Fluid Mechanics 투고 전 점검", "audience": "심사위원 · 편집자",
          "authors": "...", "pages": 12, "words": 6500, "format": "PDF", "source": "F-1",
          "synopsis": "문서 요약 (markdown, 3–6문장)"},
  "verdict": "major_revision",
  "summary": "한 줄 결론 + 꼭 고칠 것 (쉬운 말 2–3문장)",
  "checks": [{"area": "목적 부합", "rating": "fair", "note": "한 줄 메모"}],
  "strengths": ["잘된 점 한 문장"],
  "findings": [
    {"id": "R1", "severity": "blocker", "title": "한 줄 요약", "plain": "쉽게 말하면: 비전문가용 1–2문장",
     "location": "3쪽 · 2.1절 둘째 문단", "quote": "문서 원문 그대로", "suggestion": "수정안",
     "category": "선행 연구", "confidence": "high", "status": "open"}
  ]
}
```

- `doc.kind`: `paper`(과학 논문) · `report`(보고서) · `proposal`(제안서) · `thesis`(학위 논문) · `other`. `doc.source` 는 원본 결과물 ID
- `verdict`: `ready`(제출 가능) · `minor_revision`(소폭 수정) · `major_revision`(대폭 보완) · `rework`(다시 작성)
- `checks[].rating`: `good`(양호) · `fair`(보완 필요) · `poor`(미흡) · `na`(해당 없음). 영역은 문서에 맞게 —
  기본은 목적 부합 · 구성 · 형식 · 논리 전개 · 문장 · 표현 · 맞춤법 · 표기, 과학 논문은 선행 연구와의 차이 · 결론의 근거를 더한다
- `severity` 는 코드 리뷰와 같다. 문장 · 맞춤법 같은 작은 항목은 `nit` — 웹에서는 접어서 보여 준다. 같은 실수가 여러 곳이면 한 항목에 위치를 모은다
- `category`(권장): 목적 · 내용 / 구성 · 형식 / 논리 / 선행 연구 / 결론 · 근거 / 문장 · 표현 / 맞춤법 · 표기
- 리포트 · 항목 설명은 한국어. `quote` 는 문서 원문 그대로, `suggestion` 은 **문서의 언어로** (영어 논문이면 영어 수정안)
- 리포트를 고치면 같은 파일 이름으로 다시 `devtrack review` → 같은 결과물의 새 버전
- 원본을 고쳐 달라는 요청이 오면(예: "R1, R3 반영해 줘") 고칠 항목을 `request -k review --choices` 로 고르게 하고,
  수정본은 다른 이름으로 결과물에 올린다. 사용자의 원본 파일은 덮어쓰지 않는다

## 문서 시각화 (요약 + 도식)

논문 · 문서 리뷰 작업에서 사용자가 시각화(요약 · 도식 · 모식도)를 부탁하면 `/visualize-doc` 스킬로 진행한다.
웹 메시지 칸의 **시각화 요청** 으로 온 메시지는 inbox 에 `→ 문서 시각화 요청` 이 붙는다.
Codex 등 스킬 도구가 없으면 [integrations/claude-skills/visualize-doc/SKILL.md](../integrations/claude-skills/visualize-doc/SKILL.md) 를 읽고 따른다.

```bash
devtrack doc <TID>                                   # 읽을 문서 (심사 원고 · 원고 폴더 · 검토할 문서 순, --all 은 후보 모두)
devtrack svgcheck v1-overview.svg --png preview/     # 도식 점검 (실행 코드 · 넘치거나 겹친 글자 · 캔버스 밖) + PNG 미리보기
devtrack artifact <TID> v1-overview.svg --visual --replace -n "도식 1: …"   # 작업 화면 '시각화' 칸
devtrack artifact <TID> visual-<slug>.md --visual --replace -n "시각화 요약"
```

- 도식은 SVG 로 직접 그린다. 문서 내용을 이미지 생성 서비스(DALL·E · Midjourney · Gemini 등)에 보내지 않는다.
  `<script>` · `<foreignObject>` · `on…` 속성 · 외부 주소가 있는 SVG 는 DevTrack 이 받지 않는다.
- 그림 속 용어 · 숫자는 문서 그대로, 도식마다 근거 위치를 리포트에 적는다.
- 이미 있는 논문 · 문서 리뷰 작업에서 할 때는 `devtrack step <TID> "시각화: …"` 를 `-p` 없이 쓰고(그 작업의 진행률 · 계획은 그대로),
  승인 요청을 따로 올리지 않는다. 결과물과 기록(`devtrack log`)만 남기고 메시지는 `devtrack inbox --task <TID> --ack`.

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
- GitHub 이슈 우편함(`devtrack mailbox`)을 켜 두면 `inbox --wait` · `watch` 가 기다리는 동안 그 저장소의 이슈도 확인해, 정적 웹에서 보낸
  지시 · 메시지 · 결정을 웹 입력과 똑같이 기록한다 (AI 가 따로 할 일은 없음 — 평소처럼 inbox 로 받는다). 출력의 `[devtrack] GitHub 우편함: …` 줄은 알림일 뿐이다.
  사용자가 함께 보낸 파일은 그 작업의 '보낸 파일' 결과물로 올라와 있고(나중에 온 파일은 `보낸 파일: 📎 …` 메시지로), inbox 의 `→ 보낸 파일 F-3 …: <경로>` 줄이 가리킨다 — 그 파일을 바로 열고,
  Word · PDF 는 `devtrack text <경로>` 로 읽는다. `📎 … (받지 못함)` 은 받지 못한 파일이니 필요하면 사용자에게 다시 보내 달라고 남긴다.

## 다른 작업 중에 온 입력

웹에서 온 새 작업 지시와 메시지(추가 질문 · 조사 요청)는 사용자가 이미 지시한 것이다. 다른 작업을 하다가 inbox 에서 발견해도
"진행할까요?" 라고 묻지 않고 처리한다.

- **순서**: 메시지는 지금 단계를 마친 뒤 바로, 새 작업은 지금 작업이 끝나거나 승인 대기가 되면 시작한다. 여러 건이면 온 순서대로 하고,
  끝나면 원래 작업으로 돌아온다. 채팅에서 사용자가 직접 시킨 일이 있으면 그것을 먼저 한다.
- **새 작업**: `devtrack start <TID>` 로 시작해 지시대로 진행한다.
- **메시지**: 그 작업의 지시 · 이전 기록 · 결과물을 보고 답한다. 답은 웹에서 볼 수 있게 그 작업에 남기고
  (`devtrack log <TID> "답 요약" -d "상세"`, 길면 `devtrack artifact` 로 리포트) `devtrack inbox --task <TID> --ack` 로 처리 표시한다.
- **결과물에 대한 메시지**: 사용자가 결과물 화면의 '결과물 수정 요청 · 추가 지시' 칸에서 보낸 것은
  `[메시지] <TID> <L-ID> — 결과물 F-3 '제목' (v2) 에 대해: …` 로 오고, 아래에 그 결과물의 지금 파일과 새 버전으로 올리는 명령이 붙는다.
  고친 결과물은 **같은 파일 이름으로 새 버전**을 올린다 — `devtrack artifact <TID> <파일> --replace` (리뷰 리포트는 `devtrack review`).
  새 결과물을 따로 만들지 않아야 사용자가 그 결과물 화면에서 v1 → v2 로 이어 본다. 추가 지시(다른 분석 · 그림 더 만들기 등)면 평소처럼 진행하고,
  한 일을 `devtrack log` 로 남긴 뒤 `--ack`.
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
- **완료 처리 · 취소 · 삭제 알림**도 그 작업의 담당 AI 에게만 갑니다.

### 작업별 AI 모델 · 추론 강도

작업에 `model` · `effort` 를 정해 둘 수 있습니다 (웹의 "새 작업 지시" · 작업 화면의 모델 표시, 또는
`devtrack new … --model claude-opus-5-5 --effort xhigh` · `devtrack model <TID> opus-5.5 --effort xhigh` · `--clear`).
비어 있으면 어떤 모델이든 처리합니다.

- **고를 수 있는 모델 · 강도**: `devtrack model --list` — Claude 모델과, 이 컴퓨터의 Codex 가 받아 둔 Codex 모델
  (`~/.codex/models_cache.json`)과 그 모델이 받는 강도. 목록에 없는 이름도 적을 수 있지만, 목록에 있는 모델(Claude 담당은 Claude 의 값)이
  받지 않는 강도는 거부된다.
- **이 세션의 모델 · 강도**: `$DEVTRACK_MODEL` · `$DEVTRACK_EFFORT`(watch 가 넣어 줌) > Claude Code · Codex 의 세션 기록
  (`CLAUDE_CODE_SESSION_ID` · `CODEX_THREAD_ID` 로 찾은 마지막 응답의 모델 · 강도) > `$CLAUDE_EFFORT`. 모르면 확인하지 않는다.
- **받은 편지함**: 모델 · 강도가 이 세션과 다른 작업의 새 작업 · 메시지 · 결정은 따로 `[다른 모델 지정]` 으로 알리고 처리할 목록에서 뺀다.
  `inbox --wait` 는 그런 작업으로는 깨어나지 않고, `--ack` 도 표시하지 않는다. 취소 · 삭제 · 완료 처리 알림은 그대로 온다.
- **시작할 때 확인**: `devtrack start` (그리고 대기 작업에 대한 첫 `step`)는 모델이 다르면 거부한다. 세션 모델을 모르면 알리기만 한다.
  사용자가 이 세션에서 하라고 하면 `devtrack start <TID> --any-model`, 또는 사용자가 `/model` · `/effort` 로 바꾼 뒤 다시 시작한다.
- 세션은 자기 모델을 바꿀 수 없다 — 모델을 지켜야 하는 작업은 사용자가 `devtrack watch --claude` · `--codex` 로 받는다 (아래).
- `[DevTrack 모델 지정]` 메시지(정적 모드의 웹)를 받으면 안의 `devtrack model … --by-user` 명령으로 기록한다.

### `devtrack watch` 로 웹 지시 받기 (Codex · 작업마다 지정 모델로 실행)

Codex 에는 위의 백그라운드 대기 모드를 쓸 수 없으므로, 사용자가 터미널에 실행기를 켜 둡니다.
Claude 도 작업마다 정한 모델로 실행하려면 실행기를 씁니다.

```bash
devtrack watch --codex                     # Codex 에게 온 입력이 생길 때마다 codex exec 로 처리
devtrack watch --claude                    # Claude 에게 온 입력을 claude -p 로 처리 (작업마다 지정 모델 · 같은 작업은 같은 대화)
devtrack watch --agent Gemini -- <AI 명령> [옵션…]    # 다른 AI: 프롬프트를 마지막 인자로 받는 명령이면 됨
```

- 작업마다 한 번씩 실행합니다. 실행 폴더는 작업의 저장소 경로(없으면 `--cwd`, 기본 현재 폴더).
  Codex 는 `codex exec --json --sandbox workspace-write --skip-git-repo-check -C <폴더> --add-dir <기록 폴더>` 로 실행됩니다
  (허락을 물을 수 없으므로 인터넷 허용 `-c sandbox_workspace_write.network_access=true` — 막으려면 `--no-network`).
  Codex CLI 는 `$DEVTRACK_CODEX` > PATH 의 `codex` > 앱(ChatGPT · Codex · VS Code 확장)에 든 CLI 중 가장 새 버전.
  같은 작업의 다음 입력은 `codex exec resume <대화 ID>` 로 같은 대화를 이어 갑니다. resume 은 `--sandbox` · `-C` · `--add-dir` 를 받지 않으므로
  실행 폴더는 프로세스의 폴더로, 샌드박스는 `-c sandbox_mode="workspace-write"` · `-c sandbox_workspace_write.writable_roots=[…]`
  (기록 폴더 + 앞 차례에 쓰던 폴더 — config.toml 의 목록을 덮어쓰므로 함께 넘김)로 넘깁니다. 대화 ID 는 `--json` 출력의 `thread.started`
  에서 읽고, 터미널에는 Codex 의 말과 실행한 명령만 보여 줍니다. `--` 뒤에 `--sandbox` · `-C` 처럼 resume 에 없는 옵션을 적으면 매번 새 대화.
- Claude 는 `claude -p <프롬프트> --output-format json --permission-mode auto --add-dir <기록 폴더>` 로 실행됩니다
  (`--` 뒤에 `--permission-mode` 를 주면 그것을 씀). 작업의 첫 실행은 `--session-id <새 ID>`, 같은 작업의 다음 입력은
  `--resume <그 ID>` 로 같은 대화를 이어 갑니다 (실행 폴더가 같고 대화 기록이 남아 있을 때 — 열지 못하면 새 대화로 다시 실행. Codex 도 같음).
  세션 기록: `<data>/.agent/watch-sessions-<에이전트>.json` (배포되지 않음). Claude CLI 는 `$DEVTRACK_CLAUDE` > PATH 의 `claude`.
- **작업의 모델 · 강도**: Claude 는 `--model` · `--effort`, Codex 는 `-m` · `-c model_reasoning_effort="…"` 로 넘깁니다
  (`--` 뒤의 옵션보다 나중에 붙으므로 작업 지정이 이깁니다). 실행된 AI 에게는 `DEVTRACK_MODEL` · `DEVTRACK_EFFORT` 도 주어집니다.
  `-- <명령>` 으로 직접 실행하는 AI 에게는 `DEVTRACK_TASK_MODEL` · `DEVTRACK_TASK_EFFORT` 로 알려 줍니다 (그 명령이 써야 지정 모델로 실행됨).
- 실행되는 AI 에게는 `DEVTRACK_AGENT`, `DEVTRACK_DATA`, `DEVTRACK_TASK=<TID>`, `DEVTRACK_WATCH=1` 이 주어지고,
  그 안에서 `devtrack inbox` 는 그 작업의 입력만 보여 줍니다.
- **watch 가 실행한 세션의 규칙**: 그 작업만 처리하고, 사용자 결정이 필요하면 `devtrack request` 를 남기고 끝낸다.
  `devtrack wait` · `inbox --wait` 로 기다리지 않는다 — 결정이 오면 watch 가 다시 실행한다.
- 성공(exit 0)하면 넘긴 입력을 처리됨으로 표시하고, AI 의 마지막 메시지를 "실행 요약" 으로 기록합니다.
  실패하면 경고를 남기고 입력은 그대로 둡니다 (watch 를 다시 켜면 다시 처리).
- 실행 중에 사용자가 그 작업을 완료로 처리 · 취소 · 삭제하면 실행을 멈춥니다. 같은 AI 의 watch 는 하나만 실행됩니다.

## AI 끼리 리뷰 반복

코드 작업에 리뷰어가 정해져 있으면(inbox 의 새 작업 줄에 `· 끝나면 Codex 리뷰 (최대 3번)`, `devtrack show` 의 `리뷰:` 줄) 개발한 AI 와 리뷰어가 번갈아 일한다.

**개발한 AI**
1. 개발이 끝나고 커밋 승인을 요청하기 전에 `devtrack review-request <TID> [-m "봐 줄 부분"]` 을 실행한다. 리뷰어를 정해 두지 않았으면 `--reviewer Codex` 처럼 정한다
   (리뷰어 모델은 `--model` · `--effort`). 첫 요청이면 리뷰 작업이 만들어져 리뷰어에게 맡겨지고, 다음부터는 같은 리뷰 작업에 다시 요청이 간다.
2. 결과는 `[AI 메시지] <TID> …: n차 리뷰 결과: 수정 필요 — 열린 항목 R1 …` 로 온다. `devtrack show <리뷰 작업>` 으로 항목을 보고 고친 뒤 다시 `review-request`.
   같은 코드로 다시 요청하면 거절된다.
3. `통과` 가 오면 평소처럼 `devtrack request <TID> "…" --commit -m "…"`. 리뷰를 통과하지 않았거나 통과한 뒤 코드가 바뀌었으면 거절된다.
   사용자가 리뷰 없이 커밋하라고 하면 `--skip-review`.
4. 정한 횟수를 다 써도 `수정 필요` 면 더 요청할 수 없다 — 남은 항목을 정리해 `devtrack request <TID> "남은 리뷰 지적 처리"` 로 사용자에게 결정을 받는다
   (이대로 커밋 · 더 고치기 · 리뷰 더 받기). 사용자가 리뷰를 더 받으라고 하면 `review-request --rounds <N>` 으로 늘린다.

**리뷰하는 AI** (리뷰 작업의 지시에 할 일이 적혀 있다)
1. 리뷰할 변경은 리뷰 작업의 결과물 `n차 리뷰할 변경` (두 번째부터 `지난 리뷰 이후 바뀐 부분` 도). 개발 작업의 지시는 `devtrack show <개발 작업>`.
2. **파일을 고치지 않고 리뷰만 한다.** 커밋 · 푸시도 하지 않는다.
3. 항목은 `devtrack review <리뷰 작업> --findings f.json` — [코드 리뷰](#코드-리뷰) 의 JSON 형식(severity · file · line · title · plain · suggestion).
   다음 차례에는 같은 ID 의 `status` 를 `fixed` · `open` 으로 갱신하고 새 문제는 새 ID 로 더한다.
4. 판정은 `devtrack review-done <리뷰 작업> --verdict approve|changes -m "한두 줄 요약"`. `changes` 는 열린 항목이 있어야 한다.

**규칙**
- 리뷰어는 같은 AI · 같은 모델이어도 된다. 다만 **개발한 대화는 그 리뷰를 맡지 않는다**: 이 세션이 기록한 개발 작업의 리뷰 작업은 inbox 에서
  `[다른 대화가 리뷰]` 로 빠지고 (`inbox --wait` 도 깨어나지 않음), 그 리뷰 작업에 기록하는 명령은 거절된다. 실행기(`devtrack watch`)는 작업마다 다른 대화로
  실행하므로 하나로 개발과 리뷰를 함께 돌릴 수 있다. 사용자가 이 대화에서 리뷰하라고 하면 `devtrack start <리뷰 작업> --same-session`
  (다음 차례부터 미리 허락하려면 `review-request --same-session`).
- diff 는 늘 바뀐 코드만: 빌드 결과물(CMake 빌드 폴더 = `CMakeCache.txt` 가 있고 커밋된 파일이 없는 폴더, `CMakeFiles/`, `cmake_install.cmake` 등 CMake 가 만든 파일,
  `build/` · `cmake-build-*/` 같은 빌드 폴더의 새 파일, `.o` · `.a` · `.so` · `.dylib` · `.mod` · `.pyc` 등, 실행 파일 · 라이브러리 바이너리, `__pycache__/`)는
  `.gitignore` 에 없어도 리뷰 diff · 커밋 승인 diff · 커밋에서 빠지고, 뺀 것은 출력과 승인 요청에 표시된다. 꼭 넣어야 하면 `--files <파일 이름>` 으로 이름을 그대로 적는다.
- 커밋은 diff 에 넣은 파일만 스테이징해서 만든다 — 그 밖에 스테이징되어 있던 파일은 스테이징만 풀린다 (파일 내용은 그대로).
- 커밋이 반영되면 그 작업의 다음 변경은 다시 리뷰받고, 횟수도 새로 센다.
- 두 AI 가 모두 켜져 있어야 저절로 이어진다 (`devtrack watch --claude` · `--codex`, 또는 대기 모드 대화). 같은 AI 의 다른 모델이 리뷰한다면 리뷰 쪽은 실행기로.

### 논문 · 문서 심사 반복 (저널 심사위원 관점)

논문 작업(`-t paper`)이나 원고 파일(`--doc`)을 정한 작업은 코드 diff 대신 **원고를 저널 심사처럼** 심사받는다.
inbox 의 새 작업 줄에 `· 끝나면 Codex 심사 (최대 3번)` 가 보이고, `devtrack show` 의 `심사:` · `원고:` 줄에 원고 파일과 투고처가 있다.

**저자 AI**
1. 원고를 다 쓰면 `devtrack review-request <TID>` (원고 파일을 정해 두지 않았으면 `--doc paper/main.tex`, 투고처는 `--venue "…"`).
   원고는 LaTeX 본문(+ `\input` · `\include` 파일 · `.bib` · 같은 이름의 PDF · `\includegraphics` 그림) · Word(.docx) · PDF · Markdown · 텍스트.
   `찾지 못한 그림` 이 나오면 그 그림은 심사에 빠지니 경로 · 이름을 고친다. 그림만 고쳐도 다시 요청할 수 있다.
2. 판정은 `[AI 메시지] … n차 심사: 대폭 수정 (major revision) — 열린 항목 R1 …` 로 온다. `devtrack show <심사 작업>` 으로 심사평과 항목을 보고 원고를 고친다.
3. **답변서**(response to reviewers, 원고의 언어로)를 만들어 `devtrack review-request <TID> --response response.md` 로 다시 요청한다.
   열린 항목마다 **항목 ID 로 제목을 단다** — `## R1 — <항목 제목>` (또는 `**R1.** …` 로 시작하는 문단). 질문(`question`)에는 답을,
   요청에는 어떻게 고쳤는지(위치 포함, 고치지 않았다면 그 이유)를 쓴다. 총평은 첫 항목 앞에.
   2차부터는 답변서가 없거나, 지난 차례의 열린 항목 중 답이 없는 것이 있거나, 원고가 그대로면 거절된다.
   DevTrack 이 심사위원의 질문 · 요청과 이 답을 차례별로 모아 논문 작업에 **심사 · 답변 기록**(`review_response_record.md`)을 만든다 —
   심사 판정 · 답변서가 올 때마다 새 버전. 직접 고치지 않는다.
4. `게재 가능` 이면 `devtrack request <TID> "심사 통과: …" -k result -a <심사 · 답변 기록 ID>` 로 사용자에게 알린다 (ID 는 판정 메시지에 있다).
   `게재 불가` 거나 횟수를 다 쓰면 같은 기록을 첨부해 사용자에게 결정을 받는다. 논문 심사는 커밋을 막지 않는다.

**심사위원 AI** (심사 작업 = 문서 리뷰, 지시에 할 일이 적혀 있다)
1. 원고는 심사 작업의 결과물 `n차 원고` (PDF · Word) · `n차 원고 원문`, 두 번째부터 `지난 심사 이후 바뀐 부분` · `n차 답변서`.
   LaTeX 원고의 그림은 결과물 `그림: …` (지시에 적힌 폴더의 파일 — 직접 열어 본다). 바뀐 그림은 같은 결과물의 새 버전이고 심사 요청에 이름이 적힌다.
2. 실제 저널 심사처럼 본다: 새로움과 선행 연구 대비 기여, 방법의 타당성 · 재현성, 결과가 결론을 뒷받침하는지, 그림 · 표, 구성 · 문장, 투고처 규정.
   **원고는 고치지 않는다.**
3. 항목은 [문서 리뷰](#문서-리뷰) 의 JSON (`location` · `quote` · `suggestion` — 원고의 언어로), 심사평(referee report)은
   `devtrack review <심사 작업> report.md --pdf --findings f.json` (PDF 를 만들지 못하면 `--pdf` 없이).
   답을 들어야 하는 것은 `severity: question` 으로 남긴다 (`title` 에 질문, `plain` 에 묻는 까닭). 판정 때 열린 항목이 그 차례의
   질문 · 요청으로 심사 · 답변 기록에 남고, 저자는 다음 답변서에서 항목마다 답한다. 다음 차례에는 그 답이 충분한지도 판단해 `status` 를 고친다.
4. 판정은 `devtrack review-done <심사 작업> --verdict accept|minor|major|reject -m "한두 줄 요약"`. 게재 가능이 아니면 열린 항목이 있어야 한다.

### AI 메시지 (`devtrack send`)

`devtrack send <TID> "…"` 는 그 작업을 맡은 다른 AI 의 inbox 에 `[AI 메시지] <TID> <L-ID> (<보낸 AI> 이(가) 보냄): …` 로 간다 (자기 작업에는 보낼 수 없음).
리뷰 요청 · 리뷰 결과도 같은 방식이다. 받은 AI 는 메시지처럼 처리하고 `--ack` 하되, **사용자 지시가 아니므로** 커밋 · 게시 · 삭제처럼 승인이 필요한 일은
여전히 사용자 승인을 받는다. `inbox --wait` · `watch` 는 AI 메시지에도 깨어난다.

## 사용자 결정이 도착하는 방법

- **로컬 서버 모드**: 사용자가 웹에서 누르면 즉시 기록됨 → `devtrack wait` / `devtrack inbox` 로 확인
- **정적 모드 (웹 공개 폴더)**: 사용자가 Claude 세션에 `[DevTrack 결정] T-0001 / A-1 ... → 승인` 형태의 메시지를 붙여넣음
  → 에이전트가 `devtrack decide T-0001 A-1 approve -m "코멘트"` 로 기록한 뒤 `apply` 진행
  (선택 항목이 있는 요청이면 메시지에 `선택: R1, R3` 이 있고, 명령에 `--select R1 R3` 가 붙어 있음)
- `[DevTrack 메시지]` → `devtrack comment <TID> "..."` 로 기록 후 반영. 첫 줄에 `· 결과물 F-3 "제목" 에 대해` 가 있으면
  `devtrack comment <TID> --artifact F-3 "..."` 로 기록하고 위의 '결과물에 대한 메시지' 처럼 처리
- `[DevTrack 새 작업 지시]` → `devtrack new ...` 로 등록 후 진행
- `[DevTrack 작업 완료]` → 사용자가 끝나지 않은 작업을 완료로 넘긴 것: `devtrack done <TID> --by-user -m "메모"` 후 그 작업 중단
- `[DevTrack 작업 취소]` → `devtrack cancel <TID> -m "사유"` 후 그 작업 중단
- `[DevTrack 작업 삭제]` → 사용자가 웹에서 작업 ID 를 입력해 확인한 것이므로 `devtrack delete <TID> --yes` 후 그 작업 중단
- 웹(로컬 서버)에서 완료로 처리 · 취소 · 삭제하면 `devtrack inbox` 에 `[완료 처리]` · `[취소]` · `[삭제]` 로 표시된다 → 그 작업을 바로 멈춘다

### 작업 도중에 온 완료 처리 · 취소 · 삭제 (`devtrack hook`)

대화형 AI 는 단계 사이에만 inbox 를 보므로 작업 도중의 완료 처리 · 취소 · 삭제를 모른다. 이를 위해:

- 작업에 기록하는 명령(`new` · `start` · `step` · `artifact` · `request` · `done` 등)은 실행한 AI 세션을 그 작업에 적어 둔다
  (`CLAUDE_CODE_SESSION_ID` · `CODEX_THREAD_ID`, 다른 AI 는 `DEVTRACK_SESSION`).
- PreToolUse 훅으로 `bin/devtrack-hook`(= `devtrack hook`, 항상 종료 코드 0)을 걸어 두면(웹의 사용 방법 5-4 · 11단계 ⑤), 도구를 실행하기 직전마다 그 세션이 맡은 작업 중
  사용자가 완료로 처리 · 취소 · 삭제했는데 아직 확인하지 않은 것이 있는지 본다. 있으면 그 도구 실행을 막고 `⛔ DevTrack: …` 안내를 AI 에게 준다.
  devtrack 명령(과 `echo` · `head` · `tail` · `grep` 같은 출력 정리)만 있는 셸 명령은 통과한다.
- 안내를 받으면: 그 작업을 하던 중이면 즉시 멈추고, 다른 명령을 붙이지 말고 `devtrack inbox --task <TID> --ack` 만 실행해
  확인을 기록한 뒤 사용자에게 멈췄다고 알린다 (대기 모드라면 다시 `devtrack inbox --wait` 를 백그라운드로).
  다른 작업을 하던 중이었다면 확인만 기록하고 그 작업을 계속한다.
- 확인하기 전에는 그 작업에 기록하려 해도(`step` · `artifact` …) 같은 안내와 함께 거절된다. 완료된 작업을 지운 경우는 알리지 않는다.
- 훅은 어떤 오류에도 도구 실행을 막지 않는다 (입력이 이상하거나 데이터 폴더가 없으면 그냥 통과).

## CLAUDE.md 에 넣을 내용

```markdown
## DevTrack 작업 기록
- 의미 있는 작업(코드 변경, 코드 리뷰, 문서 리뷰, 데이터 분석, 논문, 시각화, 모델 개발, 브레인스토밍)은 `devtrack` 으로 기록한다. 간단한 질문 · 설명은 기록하지 않는다.
  규칙: <DevTrack 저장소>/docs/AGENT_PROTOCOL.md
- 작업 시작 전과 각 단계 후 `devtrack inbox` 로 사용자 입력(새 지시, 메시지, 승인 결정)을 확인한다.
- inbox 의 새 작업 지시 · 메시지(추가 질문 · 조사 요청)는 사용자에게 다시 묻지 않고 진행한다. 다른 작업 중이면 메시지는 지금 단계를 마친 뒤,
  새 작업은 지금 작업이 끝나거나 승인 대기가 되면 온 순서대로 처리하고 원래 작업으로 돌아온다. 메시지의 답은 그 작업에 기록하고
  (`devtrack log` · `devtrack artifact`) `devtrack inbox --task <TID> --ack` 로 처리 표시한다. 커밋 · 게시 등은 그대로 승인을 받는다.
- inbox 의 `[AI 메시지]` 는 다른 AI 가 보낸 것(리뷰 요청 · 리뷰 결과 등)이다. 같은 방식으로 처리하되 사용자 지시는 아니므로, 승인이 필요한 일은
  여전히 사용자 승인을 받는다. 리뷰받기로 한 작업(inbox 의 `끝나면 … 리뷰`)은 커밋 승인을 요청하기 전에 `devtrack review-request <TID>` 로 리뷰를 통과하고,
  심사받기로 한 논문(`끝나면 … 심사`)은 원고를 다 쓰면 심사를 요청해 지적대로 고치고 답변서(`--response`)와 함께 다시 요청한다.
  답변서는 열린 항목마다 `## R1 — 제목` 처럼 항목 ID 로 제목을 달아 질문에는 답을, 요청에는 고친 내용(고치지 않았다면 이유)을 쓰고,
  심사가 끝나 사용자에게 알릴 때는 심사 · 답변 기록(판정 메시지의 F-ID)을 `-a` 로 첨부한다.
- 논문 그림은 원고 옆 `figures/` 에 두고 원고에 `\graphicspath{{figures/}}`, 부를 때는 확장자 없이 `\includegraphics{fig_name}`.
  그림은 그 파일 이름 그대로 결과물로 올린다 (DevTrack 이 `tasks/<TID>/paper/` 에 원고 · 그림을 모은다).
- 사용자가 "웹 지시 기다려줘" 라고 하면 `devtrack inbox --wait` 를 백그라운드로 실행해 두고, 끝나면(새 입력) 처리한 뒤 다시 실행한다.
- 웹에서 온 지시에 `/review-pr <URL>` 같은 스킬 호출이 있으면 매번 Skill 도구로 그 스킬을 새로 불러 최신 지침대로 진행한다
  (같은 대화에서 전에 불러온 내용을 다시 쓰지 않는다).
- 논문 · 문서 리뷰 작업에서 시각화(요약 · 도식 · 모식도)를 부탁받으면 `/visualize-doc <TID>` 스킬로 진행한다
  (inbox 에 `→ 문서 시각화 요청` 으로 표시된다).
- 의미 있는 단계마다 `devtrack step <TID> "지금 하는 일" -p <진행률>` 을 남긴다.
- 작업마다 인계 메모(`devtrack note <TID>`)를 단계가 끝날 때 · 승인 요청 전 · 완료 전에 최신으로 둔다 — 결론 · 숫자 · 파일 위치 · 명령 ·
  결정 · 주의점만 짧게 (틀: `--template`, 저장: `devtrack note <TID> -`). 다른 작업을 참고할 때는 먼저 `devtrack show <T> --brief` 를 읽고
  리포트 · 파일은 필요한 것만 연다.
- 문제는 `devtrack issue`, 결과물은 `devtrack artifact` / 리뷰 리포트는 `devtrack review` 로 남긴다.
- 코드 리뷰 리포트는 개발자가 아닌 사람도 이해할 수 있게 쓴다: 한 줄 결론, 비개발자용 "한눈에 보기", 항목별 "쉽게 말하면", 용어 풀이.
- DevTrack 작업의 결과를 저장소에 반영할 때는 git commit/push 를 직접 하지 않고 `devtrack request <TID> "..." --commit -m "..."` 로
  승인을 요청하고, 승인되면 `devtrack apply <TID> <AID>` 로 반영한다. 단, 사용자가 채팅에서 직접 커밋 · 푸시를 지시하면 따른다.
- 사용자가 `[DevTrack 결정]` / `[DevTrack 메시지]` / `[DevTrack 새 작업 지시]` / `[DevTrack 작업 완료]` / `[DevTrack 작업 취소]` / `[DevTrack 작업 삭제]`
  메시지를 보내면 각각 `devtrack decide` / `devtrack comment` / `devtrack new` / `devtrack done --by-user` / `devtrack cancel` /
  `devtrack delete --yes` 로 먼저 기록한 뒤 진행한다.
- inbox 에 `[완료 처리]` · `[취소]` · `[삭제]` 가 오거나 도구 실행이 `⛔ DevTrack` 메시지로 막히면 그 작업은 즉시 멈추고, 안내대로 확인만 기록한 뒤
  (`devtrack inbox --task <TID> --ack`) 더 기록하지 않는다. 작업 삭제는 사용자가 요청하고 확인했을 때만 한다.
```

## AGENTS.md 에 넣을 내용 (Codex)

`~/.codex/AGENTS.md` 에 붙여 넣습니다. 설정 파일(`~/.codex/config.toml`)과 스킬 설치는 웹의 사용 방법 11단계를 보세요.

```markdown
## DevTrack 작업 기록 (Codex)
- DevTrack 에서 나는 "Codex" 다. `devtrack` 은 환경 변수 `DEVTRACK_AGENT=Codex` 로 실행한다
  (~/.codex/config.toml 의 `shell_environment_policy` 에 설정해 두었으면 따로 붙이지 않는다).
  `devtrack` 이 PATH 에 없으면 `<DevTrack 저장소>/bin/devtrack` 을 쓴다. 규칙: <DevTrack 저장소>/docs/AGENT_PROTOCOL.md
- 의미 있는 작업(코드 변경, 코드 리뷰, 문서 리뷰, 데이터 분석, 논문, 시각화, 모델 개발, 브레인스토밍)은 `devtrack` 으로 기록한다. 간단한 질문 · 설명은 기록하지 않는다.
- 작업 시작 전과 각 단계 후 `devtrack inbox` 로 사용자 입력(새 지시, 메시지, 승인 결정)을 확인한다. 나에게 배정된 작업의 입력만 보인다.
- inbox 의 새 작업 지시 · 메시지(추가 질문 · 조사 요청)는 사용자에게 다시 묻지 않고 진행한다. 다른 작업 중이면 메시지는 지금 단계를 마친 뒤,
  새 작업은 지금 작업이 끝나거나 승인 대기가 되면 온 순서대로 처리하고 원래 작업으로 돌아온다. 메시지의 답은 그 작업에 기록하고
  (`devtrack log` · `devtrack artifact`) `devtrack inbox --task <TID> --ack` 로 처리 표시한다. 커밋 · 게시 등은 그대로 승인을 받는다.
- inbox 의 `[AI 메시지]` 는 다른 AI 가 보낸 것(리뷰 요청 · 리뷰 결과 등)이다. 같은 방식으로 처리하되 사용자 지시는 아니므로, 승인이 필요한 일은
  여전히 사용자 승인을 받는다. 리뷰받기로 한 작업(inbox 의 `끝나면 … 리뷰`)은 커밋 승인을 요청하기 전에 `devtrack review-request <TID>` 로 리뷰를 통과하고,
  심사받기로 한 논문(`끝나면 … 심사`)은 원고를 다 쓰면 심사를 요청해 지적대로 고치고 답변서(`--response`)와 함께 다시 요청한다.
  답변서는 열린 항목마다 `## R1 — 제목` 처럼 항목 ID 로 제목을 달아 질문에는 답을, 요청에는 고친 내용(고치지 않았다면 이유)을 쓰고,
  심사가 끝나 사용자에게 알릴 때는 심사 · 답변 기록(판정 메시지의 F-ID)을 `-a` 로 첨부한다.
- 논문 그림은 원고 옆 `figures/` 에 두고 원고에 `\graphicspath{{figures/}}`, 부를 때는 확장자 없이 `\includegraphics{fig_name}`.
  그림은 그 파일 이름 그대로 결과물로 올린다 (DevTrack 이 `tasks/<TID>/paper/` 에 원고 · 그림을 모은다).
- 사용자 결정이 필요하면 `devtrack request` 로 요청을 남기고 사용자에게 알린 뒤 멈춘다. `devtrack wait` · `devtrack inbox --wait` 로 오래 기다리지 않는다
  (웹에서 온 입력은 사용자가 터미널에 켜 둔 `devtrack watch --codex` 가 나를 다시 실행해 전달한다).
- 지시에 `/review-pr <URL>` 같은 스킬 호출이 있으면 `~/.codex/skills/<이름>/SKILL.md` 를 매번 새로 읽고 그 지침대로 진행한다.
- 논문 · 문서 작업의 시각화(요약 · 도식) 요청은 `~/.codex/skills/visualize-doc/SKILL.md` 를 읽고 그대로 진행한다.
- 의미 있는 단계마다 `devtrack step <TID> "지금 하는 일" -p <진행률>` 을 남긴다.
- 작업마다 인계 메모(`devtrack note <TID>`)를 단계가 끝날 때 · 승인 요청 전 · 완료 전에 최신으로 둔다 — 결론 · 숫자 · 파일 위치 · 명령 ·
  결정 · 주의점만 짧게 (틀: `--template`, 저장: `devtrack note <TID> -`). 다른 작업을 참고할 때는 먼저 `devtrack show <T> --brief` 를 읽고
  리포트 · 파일은 필요한 것만 연다.
- 문제는 `devtrack issue`, 결과물은 `devtrack artifact` / 리뷰 리포트는 `devtrack review` 로 남긴다.
- 코드 리뷰 리포트는 개발자가 아닌 사람도 이해할 수 있게 쓴다: 한 줄 결론, 비개발자용 "한눈에 보기", 항목별 "쉽게 말하면", 용어 풀이.
- DevTrack 작업의 결과를 저장소에 반영할 때는 git commit/push 를 직접 하지 않고 `devtrack request <TID> "..." --commit -m "..."` 로
  승인을 요청하고, 승인되면 `devtrack apply <TID> <AID>` 로 반영한다. 단, 사용자가 채팅에서 직접 커밋 · 푸시를 지시하면 따른다.
- 사용자가 `[DevTrack 결정]` / `[DevTrack 메시지]` / `[DevTrack 새 작업 지시]` / `[DevTrack 작업 완료]` / `[DevTrack 작업 취소]` / `[DevTrack 작업 삭제]`
  메시지를 보내면 각각 `devtrack decide` / `devtrack comment` / `devtrack new` / `devtrack done --by-user` / `devtrack cancel` /
  `devtrack delete --yes` 로 먼저 기록한 뒤 진행한다.
- inbox 에 `[완료 처리]` · `[취소]` · `[삭제]` 가 오거나 도구 실행이 `⛔ DevTrack` 메시지로 막히면 그 작업은 즉시 멈추고, 안내대로 확인만 기록한 뒤
  (`devtrack inbox --task <TID> --ack`) 더 기록하지 않는다. 작업 삭제는 사용자가 요청하고 확인했을 때만 한다.
```
