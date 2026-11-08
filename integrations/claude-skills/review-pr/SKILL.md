---
name: review-pr
description: Review someone else's GitHub PR from its URL - overview, summary of code changes, and numbered findings (R1, R2…) that need fixing, written so non-specialists can follow it; then draft or post review comments only for the items the user approves. Records progress, the report and the user's choices in DevTrack when the devtrack CLI is available.
argument-hint: "<GitHub PR URL> [no-email] [no-devtrack]"
allowed-tools: Bash(python3 ${CLAUDE_SKILL_DIR}/../pr-review-report/scripts/send_report.py *), Bash(devtrack *)
---

# Review a PR

Produces a reviewer's report on another developer's pull request: what the PR does, what changed, and what needs fixing, each finding numbered (R1, R2…). Afterwards the user can say "R1, R3 코멘트 초안 써줘" / "Draft comments for R1 and R3" or "R1, R3 PR에 달아줘" / "Post R1 and R3" — or pick the items on the DevTrack dashboard.

This is the reviewer-side counterpart of `pr-review-report` (which handles comments on the user's own PRs). It reuses that skill's email script and config.

Language:
- **The whole report is written in Korean** — every heading, table, finding title, and the developer sections too (변경 사항, 상세: 문제 · 왜 중요한가 · 제안 · 확신도, 테스트 · 검증 제안, 종합 의견). The developer parts are technical, but still Korean sentences.
- The findings JSON is Korean as well: `title`, `plain`, `summary`, `verdict_reason`.
- Keep in their original form only: code, identifiers, file paths, commands, config keys, numbers with units, and text you quote from code, docs or comments (a quoted English sentence stays English, followed by a short Korean explanation). Technical terms may be written as 한국어(원어) at first use, e.g. "부분 셀(partial cell)".
- Instructions may come in Korean or English. Reply in the language of the user's latest message.
- Only the GitHub review comments (Phase 4–5) are drafted in English, unless the PR conversation is in another language.

Arguments: $ARGUMENTS
- **Required: a GitHub PR URL** `https://github.com/<owner>/<repo>/pull/<number>` (trailing `/files`, `/commits`, `#...` are fine). Parse owner, repo and number.
- **If no PR URL is given** (no arguments, only a number, or not a GitHub pull request URL), do nothing else: run no commands, read no files, make no API calls. Reply only with: `사용법 / Usage: /review-pr <GitHub PR URL> [no-email] [no-devtrack]` and stop.
- `no-email` (optional, HPC/SSH only): skip emailing the report for this run.
- `no-devtrack` (optional): do not record anything in DevTrack for this run.

## Hard rules

Until the user explicitly approves — in chat, or through a DevTrack decision as described under "DevTrack" below — do NOT:
- post any review, comment, suggestion, or reaction on GitHub
- approve, request changes, or change the PR's state (labels, reviewers, draft, merge)
- clone a repository, check out the PR branch, or touch any working copy (reading code through the GitHub API is fine)

Never, even with approval unless the user asks for it specifically: edit the author's code, commit, or push to the author's branch. This skill reviews; it does not fix.

On HPC/SSH sessions, emailing the report to the configured `PR_REPORT_TO` address is pre-approved and is the only thing sent without asking; never email any other address. Recording in DevTrack is local bookkeeping, not sending.

## Step 0: Where you are running

Run `python3 ${CLAUDE_SKILL_DIR}/../pr-review-report/scripts/send_report.py --detect`:
- `local` → deliver the full report in the chat. No email.
- `remote: ...` (SSH session or HPC login node) → save the report to a file and email it.
- If the script is missing (`pr-review-report` not installed), treat the session as remote when `$SSH_CONNECTION`, `$SSH_TTY`, `$SLURM_*` or `$LMOD_*` is set, and deliver in chat either way.

On HPC login nodes: never submit jobs (`sbatch`, `salloc`, `srun`) or run builds/tests; list the commands for the user instead.

Use `gh` with the user's login (`gh pr view`, `gh pr diff`, `gh api`). In a claude.ai cloud session, attach the repo with `add_repo` (read), use REST only, and publish the report as a document artifact instead of emailing.

State files live in `~/.pr-review/<owner>-<repo>/` (create it if needed); nothing is written into any repository.

## Step 0b: DevTrack

DevTrack is the user's progress dashboard (web): it shows what you are doing, the report, and asks the user to choose and approve. **DevTrack mode is on when `devtrack --version` succeeds and `no-devtrack` was not passed.** Otherwise skip every "DevTrack" step in this file; the rest of the skill works as before. Chat always remains a valid channel, also in DevTrack mode.

- **Task.** If the state file `~/.pr-review/<owner>-<repo>/review-PR-<n>.json` has `devtrack_task`, reuse that task (a rerun): `devtrack status <TID> in_progress -m "재검토: 새 커밋 <short sha>"`. Otherwise run `devtrack inbox`; if a queued review task's instruction contains this PR URL, `devtrack start <TID>`. Otherwise create one:
  ```
  TID=$(devtrack new "PR #<n> 코드 리뷰: <PR title>" -t review --url <PR URL> --tag PR-<n> \
        -i "<the user's request, verbatim>" \
        --plan "PR 정보 · 변경사항 수집" "코드 검토" "리뷰 리포트 작성" "코멘트할 항목 선택 (사용자)" "코멘트 초안 작성 · 게시 승인" "PR 에 게시")
  ```
  Store the TID as `devtrack_task` in the state file.
- **Progress.** At each phase: `devtrack step <TID> "<지금 하는 일, 쉬운 한국어>" -p <0-100>`; when plan step k is done: `devtrack check <TID> <k>`. Something that limits the review (no access to a file, parts of a huge PR skipped, CI logs unavailable) → `devtrack issue <TID> "<무엇이 문제인지>" -s medium`.
- `devtrack` commands that create something print only the new ID on stdout. Do not pass `--data`; the user's `$DEVTRACK_DATA` decides where records go.

## Phase 1: Collect

- PR metadata: `gh api repos/{o}/{r}/pulls/{n}` — title, author, body, base/head refs, head sha, draft state, additions/deletions, changed file count, linked issues (from the body: "Fixes #", "Closes #").
- Commits: `repos/{o}/{r}/pulls/{n}/commits` (messages give intent).
- Diff: `gh pr diff <url>`; per-file stats from `repos/{o}/{r}/pulls/{n}/files`. GitHub omits `patch` for very large files; for those, fetch base and head versions via `repos/{o}/{r}/contents/{path}?ref=<sha>` only if they matter.
- Context beyond the diff: for every non-trivial hunk, read enough of the surrounding head-sha file (callers, the function's other branches, related headers or interfaces) to judge correctness. A finding based only on a diff hunk must say so.
- Repo conventions: skim `CONTRIBUTING*`, `.clang-format`, linters/pre-commit config, and nearby code so style findings follow the project, not general taste.
- CI: `repos/{o}/{r}/commits/{head_sha}/check-runs` (name, conclusion). Summarize failures with the failing step if visible.
- Existing reviews and comments: `pulls/{n}/reviews`, `pulls/{n}/comments`, `issues/{n}/comments`. Do not repeat what another reviewer already raised; if you agree with it, mention it under that reviewer's name instead.

Large PRs (roughly > 50 files or > 3000 changed lines): say so, review the core logic first, and list what was skimmed or skipped (generated files, vendored code, lockfiles, test data, pure renames/moves).

DevTrack: `devtrack step <TID> "PR 정보 수집: <N>개 파일, +A −D, CI <상태>" -p 10`, then `devtrack check <TID> 1`.

## Phase 2: Review

Check each change for:
- **Correctness**: logic errors, off-by-one, wrong conditions, unhandled cases, error handling, resource cleanup.
- **Numerics and science** (when relevant): units, precision/kind mixing, division by zero, conservation, unit/dimension consistency, hard-coded constants that belong in a config or constants module.
- **Parallel and GPU code** (when relevant): host/device memory spaces, data races, missing fences or syncs, MPI correctness, unnecessary host-device copies, thread-safety.
- **Performance**: work inside hot loops, needless allocations or copies, algorithmic complexity.
- **API and compatibility**: changed interfaces, defaults, config/namelist options, file formats; backward compatibility; are all callers updated?
- **Tests**: is the new behavior tested? Are edge cases covered? Do existing tests still mean what they meant?
- **Readability and conventions**: naming, structure, dead code, comments and docs that no longer match the code — following the repo's own style.

Rules for findings:
- Only report what you can point to in the code. Each finding gets a **confidence**: high (verified in the code), medium (likely; explain the assumption), low (worth a question).
- Prefer fewer, solid findings over many speculative ones. Phrase low-confidence items as questions.
- Do not pad with praise or generic advice.
- For every finding, also write a **plain-language sentence** (`plain`): what goes wrong, in which situation, in words a non-programmer understands (see "Writing for non-specialists").

DevTrack: `devtrack step <TID> "코드 검토 중 (<핵심 파일/영역>)" -p 35`; when done, `devtrack check <TID> 2`.

### Number (stable across reruns)
- Number findings R1, R2… in order of severity, then file order.
- Save `~/.pr-review/<owner>-<repo>/review-PR-<n>.json` in the format below (DevTrack reads the same file).
- On a rerun (e.g. the author pushed fixes), load that file first: keep existing numbers, mark each old finding **fixed** / **still open** / **no longer applicable** against the new head sha, and give new findings the next numbers.

State / findings JSON:
```json
{
  "devtrack_task": "T-0012",
  "pr": {"url": "https://github.com/o/r/pull/42", "repo": "o/r", "number": 42, "title": "...", "author": "...",
         "base": "main", "head": "feature/x", "head_sha": "<full sha reviewed>", "previous_sha": null,
         "files": 14, "additions": 612, "deletions": 430, "ci": "통과"},
  "verdict": "request_changes",
  "summary": "<한 줄 결론 + 꼭 고칠 것을 쉬운 말 2~3문장으로>",
  "findings": [
    {"id": "R1", "severity": "blocker", "title": "<한국어 한 줄 요약 (기술적이어도 한국어로)>",
     "plain": "<쉽게 말하면: 비전문가용 1~2문장>", "file": "src/boundary.cpp", "line": 88,
     "category": "정확성", "confidence": "high", "status": "open", "head_sha": "<sha>"}
  ]
}
```
- `verdict`: `approve` | `comment` | `request_changes`.
- `severity`: `blocker` | `major` | `minor` | `nit` | `question`.
- `status`: `open` (also for "still open") | `fixed` | `commented` (posted to the PR) | `dismissed` (the user chose not to raise it) | `na` (no longer applicable).
- `line` may be a range string `"88-92"`; omit `file`/`line` for PR-wide findings. Add `comment_url` after posting.

## Writing for non-specialists

The report is read by the PR's developers **and by people who do not read this code** (project leads, scientists, managers). Someone who does not program should understand the header, 한눈에 보기 and 검토 결과 요약; a developer should still find full technical detail in 상세.

- Lead with the conclusion. Short sentences, one idea per sentence.
- In the plain parts, describe **consequences, not mechanisms**: in which situation what goes wrong and how badly ("가로·세로 칸 크기가 다른 격자에서 벽 근처 값이 틀립니다"), not "uses dx instead of dy".
- No unexplained jargon or abbreviations in the plain parts. If a technical term is unavoidable, add a short gloss in parentheses at first use, and list it in 용어 풀이.
- Severity is always shown in Korean with the English term: 🔴 반드시 수정 (Blocker), 🟠 수정 권장 (Major), 🟡 개선 제안 (Minor), ⚪ 사소함 (Nit), 🔵 질문 (Question). Confidence: 높음 / 중간 / 낮음.
- Do not overstate: medium/low-confidence findings say "~일 수 있습니다", and questions are written as questions.
- Keep the developer part precise: code, file:line, exact conditions, evidence. Plain language is added on top, never instead of it.
- Precise does not mean English: write the developer part in Korean too (see "Language"), keeping identifiers and quoted text as they are.

## Phase 3: Report

Title: `PR #<n> 코드 리뷰 – <owner>/<repo> – <date>`.

0. **헤더**: first a quote line `> **한 줄 결론:** …` (one plain sentence: is it OK to merge, and what must happen first). Then a one-row table `PR | 작성자 | 대상 | 리뷰한 커밋 | 규모 | CI` (PR title linked; `base` ← `head`; short sha; files, +/−). Below it, existing review states and linked issues if any. On reruns, the previous sha and what changed since.
1. **한눈에 보기** (비개발자용, no jargon):
   - **이 PR이 하는 일:** 1–2 sentences on what changes and why, in everyday words.
   - **리뷰 의견:** 승인해도 됨 / 의견만 전달 / 수정 후 다시 확인 — one-sentence reason.
   - **꼭 고쳐야 할 것:** count, then one plain line per Blocker/Major (`**R1** — …`), stating the consequence.
   - **영향 범위:** who or what is affected (which runs, configurations, users).
   - The severity legend table `중요도 | 뜻` (the five levels above, each with a one-line meaning).
2. **검토 결과 요약**: table `# | 중요도 | 쉬운 설명 | 위치 | 분류 | 확신도` (쉬운 설명 = the `plain` idea in one short line).
3. **변경 사항** (개발자용): 3–6 sentences on what the PR does and why (from the description, commits and code) and whether the implementation matches the stated intent; then changes grouped by component or purpose, not file by file — a table `영역 | 파일 | +/− | 변경 내용` — and a few sentences on the key design decisions.
4. **상세 (개발자용)**: one section per finding, headed `### R1. <한국어 제목> — 🔴 반드시 수정`, with bullets (all in Korean):
   - **쉽게 말하면:** 1–2 plain sentences (the `plain` text).
   - **위치:** file:line on the head side, with a link.
   - **문제:** what is wrong, with the code evidence.
   - **왜 중요한가:** which inputs or conditions produce which wrong result.
   - **제안:** a suggested fix (a short code snippet when it helps).
   - **확신도:** 높음/중간/낮음 — and its basis (verified in code / reproduced / diff only).
5. **테스트 · 검증 제안**: missing tests, and what to run to verify (on HPC, the exact commands with `module load` lines).
6. **종합 의견**: suggested verdict (Approve / Comment / Request changes) with a one-paragraph rationale in plain words. The decision is the user's.
7. **다른 리뷰어 의견**: one line per existing reviewer comment you agree or disagree with, if any.
8. **용어 풀이**: only the technical terms that appear in the report, one plain line each. Omit if there are none.

Do not write GitHub comment drafts in the report; those come in Phase 4 only for the items the user picks.

Write the report Markdown the way GitHub renders it (CommonMark + tables): pipe tables, 2-space nested lists, fenced code blocks with a language tag. Raw HTML is shown as text, not rendered, so `<placeholders>` are safe but HTML tags are not formatting. Keep code snippets short (a few lines) so they fit on a PDF page; keep table cells to one line.

### Deliver
- **Local**: write the full report as your chat response, ending with a suggested next instruction (e.g. "R1, R3 코멘트 초안 써줘" / "Draft comments for R1 and R3"). Do not also write it to a file (unless DevTrack mode is on, below); only save the findings JSON. If the user asks to save it, write `~/.pr-review/<owner>-<repo>/review-PR-<n>.md`.
- **Remote**: write `~/.pr-review/<owner>-<repo>/review-PR-<n>.md`. Unless `no-email` was passed, send it:
  `python3 ${CLAUDE_SKILL_DIR}/../pr-review-report/scripts/send_report.py ~/.pr-review/<owner>-<repo>/review-PR-<n>.md --subject "[Code review] <owner>/<repo>#<n> – <YYYY-MM-DD>"`
  - The script converts the report to PDF next to it (`review-PR-<n>.pdf`, A4, Korean font embedded) and attaches the PDF and the `.md`; with `smtp` the body is also HTML, with `mailx` it is plain text and the PDF carries the formatting. Do not write a separate email or PDF version yourself.
  - It reads the recipient and credentials from `~/.config/pr-review-report/email.env`. Never read, print, ask for, or write the password yourself.
  - On a setup error, point the user to `pr-review-report`'s one-time email setup. If SMTP is blocked, suggest `PR_REPORT_METHOD=mailx`. If email cannot be sent at all, show the full report in chat.
  - If it prints `WARNING: PDF not attached (...)`, the email was still sent without the PDF. Tell the user why and point to step 5 of `pr-review-report`'s one-time setup (it also printed the command). Do not install packages or fonts yourself unless asked.
  - In chat, give only the header line, the findings summary table, the report and PDF paths, whether the email was sent (and whether the PDF was attached), and the suggested next instruction.
- **Cloud**: publish as a document artifact; no email.
- **DevTrack** (in addition to the above, local or remote):
  1. Always write `~/.pr-review/<owner>-<repo>/review-PR-<n>.md` (also locally) and the findings JSON.
  2. Before registering, check the language: every heading and finding title in the report and the JSON must be Korean (see "Language"); fix anything that is not.
  3. Register the report, its PDF and the findings in one command:
     `devtrack review <TID> ~/.pr-review/<owner>-<repo>/review-PR-<n>.md --pdf -n "PR #<n> 리뷰 리포트" --findings ~/.pr-review/<owner>-<repo>/review-PR-<n>.json`
     `--pdf` renders `review-PR-<n>.pdf` next to the Markdown (A4, Korean fonts, same look as the web) with a headless Chrome/Chromium and attaches both. It prints the artifact IDs, first one = the Markdown report. If it warns that no browser was found, the Markdown is still registered; tell the user the PDF was skipped and why. Then `devtrack check <TID> 3`.
  4. Ask which findings to turn into PR comments:
     ```
     AID=$(devtrack request <TID> "PR #<n>: 코멘트를 작성할 항목을 골라주세요" -k review \
           --choices --choices-label "PR 코멘트를 작성할 항목" -a <report F-ID> \
           -d "<한 줄 결론> 제안: R1, R2. 고른 항목만 코멘트 초안을 만들어 다시 확인받습니다 — 아직 PR 에는 아무것도 게시하지 않았습니다.")
     ```
     `--choices` without IDs offers every open finding as a checkbox; Blocker and Major start checked. Then `devtrack step <TID> "사용자가 코멘트할 항목을 고르기를 기다리는 중" -p 70`.
  5. End the chat message with: "DevTrack 웹의 **코드 리뷰** 화면에서 항목을 골라 진행하거나, 여기서 'R1, R3 코멘트 초안 써줘' 라고 하셔도 됩니다."
  6. If the harness supports background commands, start `devtrack wait <TID> <AID> --timeout 3600` in the background so you resume when the user decides on the web — unless a `devtrack inbox --wait` is already running in the background (web-instruction waiting mode), which also wakes on decisions.

Then stop and wait.

## Phase 4: Draft review comments (on request)

Triggered by "R1, R3 코멘트 초안 써줘", "R2–R5 초안", "Draft comments for R1 and R3", "Draft all Blockers and Majors" — or, in DevTrack mode, by the user's decision on the selection request. Bare numbers mean R-numbers; if a number does not exist, ask.

DevTrack — reading the decision:
- `devtrack inbox` (or the finished background `wait`) shows `[결정] <TID> <AID> … → approved` with `선택: R1, R3` and maybe `코멘트: …`. The selected IDs are the items to draft; follow the comment if any. Mark it handled with `devtrack inbox --ack`.
- `changes_requested`: the comment says what to change in the review (e.g. "R2 는 틀린 지적"). Update the report and findings, re-register them (`devtrack review …`), and make a new selection request. `rejected`: draft nothing; set unpicked findings to `dismissed` if the user said so, and `devtrack done <TID> -m "리뷰 리포트만 전달, PR 코멘트 없음"`.
- If the user picks in chat instead, record it first: `devtrack decide <TID> <AID> approve --select R1 R3 -m "채팅에서 선택"`. In static mode the user may paste `[DevTrack 결정] T-… / A-… → 승인` with `선택: …`; run the `devtrack decide` command it contains, then continue.
- Findings the user did not pick stay `open` in the JSON unless the user says to drop them.

For each selected finding:
- An inline comment anchored to a line **in the diff** on the head side (`path`, `line`, `side: RIGHT`; `start_line` for multi-line). If the line is not part of the diff, GitHub cannot anchor it; put that item in the review body instead and say so.
- Tone: specific, polite, and short; say what and why, and ask rather than assert when confidence is not high. Prefix nits with `nit:`.
- When the fix is small and certain, include a GitHub suggestion block:
  ````
  ```suggestion
  <replacement lines>
  ```
  ````
- Also draft a short review summary body.

Show all drafts with their anchors, plus the proposed review event: **COMMENT** by default. Then ask (in the user's language): "이대로 PR에 리뷰를 게시할까요? (수정 가능)" / "Post this review to the PR? (you can edit it first)". Do not post.

DevTrack: write the drafts to `~/.pr-review/<owner>-<repo>/review-PR-<n>-comments.md` — for each item: R-number, anchor (`path:line` or "본문"), the exact comment text; then the summary body and the event. Before each comment add one plain Korean line saying what it asks the author to do, so the user can approve without reading code. Register and request approval:
```
F=$(devtrack artifact <TID> ~/.pr-review/<owner>-<repo>/review-PR-<n>-comments.md -n "PR #<n> 리뷰 코멘트 초안" --review)
AID=$(devtrack request <TID> "PR #<n> 에 리뷰를 게시할까요? (<EVENT>, 코멘트 <k>개)" -k review -a $F \
      -d "승인하면 이 초안 그대로 GitHub 에 게시합니다. 고칠 점은 '수정 요청' 에 적어주세요.")
devtrack check <TID> 4
```
Optionally wait in the background as in Phase 3.

## Phase 5: Post (only on explicit approval)

Approval means: the user says so in chat, or the DevTrack posting request from Phase 4 is `approved` with `decided_by: user` (decided on the web, or recorded by you only from the user's own chat message or pasted `[DevTrack 결정]`). Approval covers exactly the attached draft; if the user edits drafts in chat, post the edited text. A `changes_requested` decision means: revise the drafts as the comment says and make a new posting request. `rejected` means: post nothing.

- Post everything as **one** review: `POST repos/{o}/{r}/pulls/{n}/reviews` with `commit_id` = the reviewed head sha, `body`, `event`, and `comments[]`. If the head sha moved since the review, say so and ask before posting (anchors may be stale).
- `event` is `COMMENT` unless the user explicitly says approve (`APPROVE`) or request changes (`REQUEST_CHANGES`).
- Post exactly the approved text. After posting, give the review URL and update the findings JSON (status: commented, plus `comment_url`).
- DevTrack: `devtrack review <TID> --findings <json>` (updates statuses), `devtrack artifact <TID> --url <review URL> -n "PR #<n> 게시된 리뷰" --review`, `devtrack check <TID> 5 6`, `devtrack done <TID> -m "PR #<n> 에 리뷰 게시 (<EVENT>, 코멘트 <k>개): <URL>"`.
