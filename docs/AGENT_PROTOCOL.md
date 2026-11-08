# AI 에이전트 작업 기록 규칙 (DevTrack)

Claude 등 AI 에이전트가 사용자 대신 작업할 때 따르는 규칙입니다.
사용자는 웹 대시보드에서 진행 상황을 보고, 승인 요청에 답합니다.

> Frontier 에서 Claude 가 이 규칙을 항상 따르게 하려면 아래 [CLAUDE.md 에 넣을 내용](#claudemd-에-넣을-내용)을
> `~/.claude/CLAUDE.md`(모든 프로젝트) 또는 작업 대상 저장소의 `CLAUDE.md` 에 붙여 넣으세요.

## 원칙

1. **모든 작업은 DevTrack 작업(T-xxxx)으로 기록한다.** 사용자가 보고 있다고 생각하고, 의미 있는 단계마다 남긴다.
2. **커밋/푸시는 승인 후에만 한다.** `git commit`/`git push` 를 직접 실행하지 말고
   `devtrack request --commit` → 사용자 승인 → `devtrack apply` 로만 반영한다.
3. **문제는 숨기지 않는다.** 막히거나 예상과 다르면 `issue` 로 기록하고, 결정이 필요하면 `status blocked` + 선택지를 남긴다.
4. **결과물은 웹에서 볼 수 있게 남긴다.** 그림, 리포트, 로그, 노트북, LaTeX, diff 등은 `artifact` 로 첨부한다.
5. **사용자 입력을 주기적으로 확인한다.** 작업 시작 시, 단계가 끝날 때마다 `devtrack inbox` 를 확인한다.

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
```

## 작업 유형별 요령

| 유형 (`-t`) | 남길 것 |
|---|---|
| `code` | 계획, 단계별 진행, 테스트 결과, `request --commit` |
| `review` | `templates/review_report.md` 형식의 리포트를 `devtrack review` 로 등록 (md, 필요하면 pdf 도). PR 에 코멘트를 게시하기 전 `request -k review` |
| `paper` | `--plan` 에 원고 구성(절/장)을 넣는다 — 웹의 논문 카드에 체크리스트로 표시. 원고를 고칠 때마다 `.tex` 를 `artifact` 로 올리고 **`devtrack latex <TID> paper/main.tex` 로 컴파일 결과(PDF/오류)를 기록**한다. 그림은 `\includegraphics` 와 **같은 파일 이름(확장자 무관)** 으로 올려야 웹의 문서 보기에서 연결된다 (예: `figs/fig4_1.pdf` ↔ `fig4_1.png`). 원본 데이터와 다른 수치는 `issue` |
| `analysis` | 노트북(`.ipynb`), CSV 요약, 그림, 요약 리포트(md). 해석이 필요한 결과는 `request -k result`. **논문에 쓸 분석이면 `--related <논문 T-ID>`** 로 연결 |
| `visualization` | 그림/HTML(plotly 등) 결과물, 사용한 스크립트 |
| `model` | 학습 설정, 손실 곡선 그림, 로그, 평가 결과. 큰 자원 사용 전 `request -k plan` |

## 승인 요청을 올려야 하는 때

- 저장소에 **커밋/푸시** 할 때 (`--commit`, 필수)
- PR 에 리뷰 코멘트 게시, 외부에 공개되는 작업 (`-k review`)
- 많은 계산 자원을 쓰는 작업 시작 전 (`-k plan`)
- 결과 해석/데이터 제외 등 사용자의 판단이 필요한 경우 (`-k result`)

## 사용자 결정이 도착하는 방법

- **로컬 서버 모드**: 사용자가 웹에서 누르면 즉시 기록됨 → `devtrack wait` / `devtrack inbox` 로 확인
- **정적 모드 (Frontier www)**: 사용자가 Claude 세션에 `[DevTrack 결정] T-0001 / A-1 ... → 승인` 형태의 메시지를 붙여넣음
  → 에이전트가 `devtrack decide T-0001 A-1 approve -m "코멘트"` 로 기록한 뒤 `apply` 진행
- `[DevTrack 메시지]` → `devtrack comment <TID> "..."` 로 기록 후 반영
- `[DevTrack 새 작업 지시]` → `devtrack new ...` 로 등록 후 진행

## CLAUDE.md 에 넣을 내용

```markdown
## DevTrack 작업 기록 (필수)
- 모든 작업은 `devtrack` 으로 기록한다. 규칙: <DevTrack 저장소>/docs/AGENT_PROTOCOL.md
- 작업 시작 전과 각 단계 후 `devtrack inbox` 로 사용자 입력(새 지시, 메시지, 승인 결정)을 확인한다.
- 의미 있는 단계마다 `devtrack step <TID> "지금 하는 일" -p <진행률>` 을 남긴다.
- 문제는 `devtrack issue`, 결과물은 `devtrack artifact` / 리뷰 리포트는 `devtrack review` 로 남긴다.
- git commit/push 를 직접 하지 않는다. `devtrack request <TID> "..." --commit -m "..."` 로 승인을 요청하고,
  승인되면 `devtrack apply <TID> <AID>` 로 반영한다.
- 사용자가 `[DevTrack 결정]` / `[DevTrack 메시지]` / `[DevTrack 새 작업 지시]` 메시지를 보내면
  각각 `devtrack decide` / `devtrack comment` / `devtrack new` 로 먼저 기록한 뒤 진행한다.
```
