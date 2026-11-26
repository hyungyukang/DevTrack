# DevTrack 짧은 규칙 (에이전트용)

상세 규칙은 docs/AGENT_PROTOCOL.md. 전문을 읽지 말고, 아래 '더 읽을 때' 의 상황이 오면 `devtrack rules <주제>` 로 그 절만 읽는다.

## 꼭 지킬 것
1. 의미 있는 작업은 DevTrack 작업(T-xxxx)으로 기록한다. 간단한 질문 · 설명은 기록하지 않는다.
2. 커밋 · 푸시는 승인 후에만 한다: `devtrack request <T> "…" --commit -m "…"` → 승인 → `devtrack apply <T> <A>`. 채팅에서 직접 지시하면 따른다.
3. 입력 확인: 작업을 시작할 때 `devtrack inbox`. 그 뒤로는 devtrack 명령 출력에 `📬 DevTrack 새 입력` 줄이 보일 때만 inbox (단계마다 따로 부르지 않는다).
   새 작업 · 메시지는 묻지 않고 처리한다 — 메시지는 지금 단계 뒤, 새 작업은 지금 작업이 끝나거나 승인 대기가 되면. 답은 그 작업에 남기고 `devtrack inbox --task <T> --ack`.
4. `[AI 메시지]` 는 다른 AI 의 요청이다. 처리하되 승인이 필요한 일은 여전히 사용자 승인.
5. 작업 지시의 언어로 기록한다 (`show` 의 `언어`, 영어 작업이면 `→ 작업 언어: 영어`).
6. 취소 · 삭제 · 완료 처리 알림이나 `⛔ DevTrack` 이 오면 그 작업은 즉시 멈추고 `devtrack inbox --task <T> --ack` 만 한다.
7. 담당이 다른 AI 이거나 모델이 다른 작업(`[다른 모델 지정]`)은 처리하지 않는다.
8. 몇 분 넘게 걸릴 명령(빌드 · 시뮬레이션 · 학습)은 `devtrack run <T> -- 명령` 으로 실행한다 (Claude Code 는 백그라운드로 실행해 두고 기다림).
   중간 확인이 진행 기록에 남고, 끝나거나 멈춘 듯하면 명령이 끝나며 알려 준다 — 그동안 `tail` · `runs` 를 되풀이해 부르지 않는다.

## 토큰 아끼기
- 기록 명령은 묶어서 한 번에 부른다. 기록만 하는 명령(step · check · log · artifact)을 혼자 부르지 않는다:
  `devtrack check <T> 2 && devtrack step <T> "테스트 중" -p 60 && pytest -q`
  (명령마다 AI 가 대화 전체를 다시 읽는다 — 따로 부른 기록 명령이 작업 대화 비용의 12–18% 였다)
- 다른 작업은 `devtrack show <T> --brief` (상태 · 주요 결과물 · 인계 메모) 부터 보고, 리포트 · 파일은 필요한 것만 연다.
- 긴 출력은 필요한 부분만 본다 (`| tail -20`, `--json` 대신 기본 출력).

## 기본 흐름
```bash
devtrack inbox                                   # 시작할 때
TID=$(devtrack new "제목" -t code --repo . --plan "분석" "구현" "테스트")   # 웹에서 온 작업이면 devtrack start <T>
devtrack step $TID "지금 하는 일" -p 30           # 의미 있는 단계마다 (다른 명령과 묶어서)
devtrack issue $TID "문제" -s high -d "상세"      # 막히거나 예상과 다를 때
devtrack artifact $TID out.png -n "결과 그림"     # 결과물 (리뷰 리포트는 devtrack review)
devtrack note $TID - < notes.md                  # 인계 메모: 결론 · 숫자 · 파일 · 명령 · 주의점 (틀: --template)
AID=$(devtrack request $TID "커밋" --commit -m "메시지")   # 결정 전에 변경을 더하면 devtrack withdraw 후 다시
devtrack apply $TID $AID                         # 승인되면
devtrack done $TID -m "요약"
```
- 사용자가 채팅으로 `[DevTrack 결정]` · `[DevTrack 메시지]` · `[DevTrack 새 작업 지시]` · `[DevTrack 작업 완료]` · `[DevTrack 작업 취소]` · `[DevTrack 작업 삭제]` 를 보내면
  각각 `decide` · `comment` · `new` · `done --by-user` · `cancel` · `delete --yes` 로 먼저 기록한다.
- "웹 지시 기다려줘": 밀린 inbox 를 처리하고 `devtrack inbox --wait` 를 백그라운드로 실행해 둔다 (한 번에 하나, 끝나면 처리 후 다시).
- 작업 유형마다 스킬이 있다 (inbox 의 `→ 유형 …: /스킬`): 논문 `/write-paper` · 발표 자료 `/make-slides` · 포스터 `/make-poster` ·
  문서 리뷰 `/review-doc` · 문서 시각화 `/visualize-doc` · 코드 리뷰 `/review-pr`. 지시에 스킬 호출이 있으면 매번 새로 불러 따른다.

## 더 읽을 때 (`devtrack rules <주제>`, 목록: `devtrack rules --list`)
| 이럴 때 | 주제 |
|---|---|
| 리뷰받기로 한 작업(inbox 의 `끝나면 … 리뷰` · `심사`), `[AI 메시지]` 로 리뷰 요청 · 판정이 옴 | `review-loop` |
| PR 리뷰 · 코드 리뷰 리포트를 쓸 때 | `code-review` |
| 문서(PDF · Word · LaTeX) 리뷰 | `doc-review` |
| 논문 · 발표 자료 · 포스터 · 원고 폴더 · 그림 | `paper` |
| 요약 · 도식 시각화 | `visual` |
| 아이디어 브레인스토밍 | `brainstorm` |
| 인계 메모를 처음 쓸 때 · 다른 작업 메모를 읽을 때 | `note` |
| 작업 중에 다른 입력이 와서 순서가 헷갈릴 때 | `inputs` |
| 대기 모드 · GitHub 우편함 · 보낸 파일 | `wait` |
| 담당 AI · 모델 지정 · `devtrack watch` 로 실행됨 | `agents` |
| 승인이 필요한지 모를 때 · 결정이 오는 방법 · 작업 도중 취소 | `approvals` · `decisions` |
| 분석 · 시뮬레이션 등 유형별 요령 | `types` |
| 오래 걸리는 실행이 멈춤 의심 · 실패, Slurm 작업 따라가기, `[실행 알림]` | `runs` |

DevTrack 명령도 필요한 때 안내를 출력한다 (inbox 의 `→ …` 줄, `start` 의 관련 작업 메모, 리뷰 요청 · 판정 메시지의 다음 할 일). 그 안내를 따른다.
