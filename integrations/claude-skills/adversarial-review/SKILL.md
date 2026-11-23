---
name: adversarial-review
description: Tough review of the changed code only (a DevTrack review round, local changes, or a PR diff) through three hostile personas tuned for scientific / HPC code - the Saboteur (runtime breaks, parallel and GPU bugs), the Numerics Auditor (units, conservation, precision, reproducibility) and the New Hire (maintainability) - then numbered findings R1… recorded in DevTrack with a PDF report. Reviews only; never edits, commits or posts. Use for a harsh second opinion, before a commit request, or as the reviewer in a DevTrack review loop.
argument-hint: "[DevTrack review task T-ID | PR URL | git ref range | (none = local changes)] [no-devtrack]"
allowed-tools: Bash(devtrack *), Bash(gh pr diff *), Bash(gh pr view *)
---

# Adversarial review of the changed code

When an AI reviews code it wrote or just read, it shares the author's assumptions and says "looks good". This review forces three
different hostile viewpoints onto **the changed code only**.
Adapted for DevTrack and scientific / HPC code from `adversarial-reviewer` (see the end of this file).

Language: the report and the findings JSON (`title`, `plain`, `summary`, `suggestion`) are **Korean**; keep code, identifiers, paths,
commands, numbers with units and quoted text as they are. Reply in the language of the user's latest message.

## Hard rules

- **Review only.** Do not edit files, commit, push, stage, check out branches or post anything on GitHub. For posting PR comments use
  `/review-pr`, which asks for approval first.
- On HPC login nodes do not build, run tests or submit jobs; list the commands that would settle a question instead.

## 1. What to review — only what changed

Pick the first that applies:
1. **A DevTrack review task** (argument is its T-ID, or inbox gave you one; its instruction says which development task it reviews).
   The change is that task's artifact `n차 리뷰할 변경` (diff), from round 2 also `지난 리뷰 이후 바뀐 부분`. The development task's
   request: `devtrack show <development task>`. Previous findings: `devtrack show <review task>`.
2. **A PR URL** → `gh pr view <url>` and `gh pr diff <url>` (read-only).
3. **A git ref range** → `git diff <range>`.
4. **Nothing** → local changes of the current repo: `git diff HEAD` plus untracked source files; if empty, `git diff HEAD~1`.

Leave out build outputs (CMake build folders, `CMakeFiles/`, `*.o` `*.mod` `*.so` `*.pyc`, executables) — DevTrack diffs already do.
If nothing is left: report "리뷰할 변경 없음" and stop.

Read the **whole file around each hunk** to understand context, but every finding must be about a changed line or about how the change
interacts with the code around it. Problems that existed before and are not made worse go to a separate list "변경 전부터 있던 문제",
not into the findings.

## 2. Three personas, one after another

Do not soften: say what breaks and when. But label confidence honestly — **high** (verified in the code), **medium** (likely; state
the assumption), **low** (ask it as a question).

### 파괴자 (Saboteur) — "I am trying to break this in a real run"
- Array bounds, off-by-one in loop limits (first / last level, `nVertLevels`, halo widths), uninitialized or `intent(out)` variables
  read before set, integer overflow in index products.
- Division by zero or near-zero (zero-thickness layers, land / masked cells, empty columns), NaN or Inf propagation, `sqrt`/`log` of
  negative values.
- Parallel: missing halo update after a write, different results for different decompositions, MPI collectives inside branches,
  OpenMP / OpenACC / Kokkos races and missing private clauses, host ↔ device data not copied.
- Restart: new state not written to or read from restart files; behaviour different after restart.
- Error paths that continue silently or return a misleading value.
For each changed routine ask: worst input? run twice? on one task vs many? on GPU? after restart?

### 수치 감사관 (Numerics Auditor) — "the numbers will be published"
- Units and dimensions on both sides of every assignment; sign conventions (up / down, into / out of the ocean).
- Conservation of mass, heat, salt, momentum where the scheme promises it; flux consistency between neighbours.
- Precision: real kinds, integer ↔ real conversion and integer division (e.g. masks stored as integers), mixed precision, literal
  constants without kind (`1.0` vs `1.0_RKIND`).
- Reproducibility: bit-for-bit across decompositions, thread counts and restarts where the project expects it; order of reductions.
- Defaults: a new namelist / config option whose default silently changes existing results.
- Cost of hot loops (memory access order, work inside the innermost loop, extra halo exchanges).
- Security only where the change touches it: secrets in files, shell commands built from input, network calls.

### 새 팀원 (New Hire) — "I must change this in six months without the author"
- Names that do not say what the value is (and its units); magic numbers; comments that say *what* instead of *why*.
- A routine doing more than its name says; logic that needs three other files to follow.
- Inconsistency with the project's style and conventions (read CLAUDE.md / AGENTS.md / contributing docs if present).
- New behaviour without a test or a documented check case.

Each persona must report at least its **most fragile assumption** in the change. If it found nothing wrong, record that assumption as a
`question` or `nit` with low confidence — never inflate it.

## 3. Merge and rank

Merge duplicates. A finding raised by two or more personas moves up one severity level. Severities (DevTrack):
- `blocker` — wrong results, crash, data loss, broken restart or reproducibility. Must fix.
- `major` — likely wrong in edge cases or other configurations, or a clear performance regression. Should fix.
- `minor` · `nit` — maintainability, naming, docs, style.
- `question` — needs the author's answer.
Number them R1, R2… by severity, then file order. In a later round keep the same IDs: set `status` to `fixed` or `open`, new problems
get new IDs.

Verdict: any open `blocker` → `request_changes`; no blocker but two or more open `major` → `request_changes`; otherwise `approve`
(or `comment` if open questions remain).

## 4. Report (Korean) and findings

Write the files **outside the repository** (they would otherwise show up in the diff): `W=$(mktemp -d)`, then `$W/adversarial-review-<slug>.md`:
1. **한 줄 결론** and **판정** (승인 / 의견 / 수정 요청) with one line why.
2. **범위** — files, +/− lines, kind of change; what was not reviewable.
3. **항목 표** — `ID | 중요도 | 확신도 | 위치 | 한 줄 요약 | 지적한 관점`.
4. **항목별 상세** — 문제 (with `file:line` and the quoted code), 왜 중요한가 (which run, configuration or result it affects),
   제안, 확인 방법 (test or run that would settle it).
5. **변경 전부터 있던 문제** (if any), **가장 먼저 고칠 것** (one item).

Findings JSON `$W/adversarial-review-<slug>.json`:
```json
{"pr": "<PR URL or repo + range, or the development task ID>", "verdict": "request_changes",
 "summary": "<한두 문장>",
 "findings": [
   {"id": "R1", "severity": "blocker", "title": "<한국어 한 줄>", "plain": "<쉬운 설명 한두 문장>",
    "file": "src/ocn_gm.F", "line": 812, "category": "수치", "confidence": "high", "status": "open",
    "suggestion": "<제안>"}
 ]}
```
`category` is one of 정확성 · 병렬 · 수치 · 재현성 · 성능 · 유지보수 · 보안 · 테스트.

## 5. DevTrack

DevTrack mode is on when `devtrack --version` succeeds and `no-devtrack` was not passed. Otherwise give the report in chat and tell the
user where the two files are. The commands below run in `$W`.

- **In a DevTrack review loop** (case 1):
  ```bash
  devtrack review <review task> adversarial-review-<slug>.md --pdf --findings adversarial-review-<slug>.json -n "<n>차 리뷰 리포트"
  devtrack review-done <review task> --verdict approve|changes -m "<한두 줄 요약>"     # changes needs open findings
  ```
- **Otherwise** use the DevTrack task this conversation works on; if none, create a review task
  `TID=$(devtrack new "변경 리뷰: <한 줄>" -t review -i "<the user's request, verbatim>" --repo <repo>)` (add `--url <PR URL>` for a PR), then
  `devtrack review $TID adversarial-review-<slug>.md --pdf --findings adversarial-review-<slug>.json -n "적대적 리뷰 리포트"`.
  If the user should pick what to fix next: `devtrack request $TID "고칠 항목 선택" -k review --choices`.

`--pdf` makes the PDF from the Markdown report (headless Chrome). If it fails, the Markdown report is still recorded; say so.

---
Adapted from `adversarial-reviewer` (ekreloff, MIT) in [alirezarezvani/claude-skills](https://github.com/alirezarezvani/claude-skills)
(MIT, © 2025 Alireza Rezvani). Changes: changed-code-only scope, personas rewritten for scientific / HPC code (the Security Auditor
became the Numerics Auditor), honest confidence instead of forced severity, DevTrack findings and PDF report, Korean output.
See `../NOTICE.md`.
