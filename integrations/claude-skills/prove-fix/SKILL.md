---
name: prove-fix
description: Fix a bug with proof - verify the cause against real evidence, write a test or small reproducer that FAILS on the current code, apply the smallest fix, watch it pass, then put the old code back and watch it fail again (a test that passes both ways proves nothing). Never commits; asks for commit approval through DevTrack. Use when fixing a bug, a wrong number, a crash or NaN, or when asked to "prove the bug", "make the test fail first", "red-green".
argument-hint: "[the bug, e.g. 'mask is integer so the flux is truncated'] [no-devtrack]"
allowed-tools: Bash(devtrack *)
---

# Prove the fix (red → green → red → green)

A bug is fixed when a check **fails without your fix and passes with it**, and you have watched it do both.
Adapted for DevTrack and scientific / HPC code from `red-green-proof` (see the end of this file).

Arguments: $ARGUMENTS
- With a bug description, that is the target. Without one, take it from the conversation, in this order: a defect just found but not
  fixed → a fix applied without a failing check → a check never seen red → something called "still open". State the pick in one line
  and start. Ask only when two candidates are equally likely; if nothing qualifies, ask for a target instead of inventing one.
- `no-devtrack`: do not record in DevTrack for this run.

Language: everything you write for the user (chat, DevTrack logs, the evidence record) is **Korean**. Keep code, identifiers,
paths, commands, numbers with units and quoted output as they are.

## Hard rules

- **Never commit, push, amend, rebase, `git stash`, `git reset`, `git checkout -- <path>` or `git restore`.** The working tree holds
  the user's uncommitted work; reverting the fix is done only by copying the files you changed (step 4).
- The commit happens only after the user approves it: in DevTrack mode `devtrack request <TID> … --commit` (then `devtrack apply`
  after approval); otherwise ask in chat. If the user tells you in chat to commit, follow that.
- Touch only the files the fix and its check need. Do not reformat or "improve" nearby code.
- HPC: compiling where the project normally compiles is fine. Runs that need a compute node (`srun`, `sbatch`, `salloc`, a model run
  under a batch system) need the user's OK first — in DevTrack mode `devtrack request <TID> "테스트 실행 허락: <명령>" -k plan` and wait
  for the decision; otherwise ask in chat. If a phase cannot be run, label it **모름(Unknown)** and list the exact command; never report
  a result you did not observe.

## DevTrack

DevTrack mode is on when `devtrack --version` succeeds and `no-devtrack` was not passed; otherwise skip the DevTrack lines.

- Task: use the DevTrack task this conversation is already working on. If there is none, run `devtrack inbox` and `devtrack start`
  a queued code task whose instruction matches; otherwise create one:
  `TID=$(devtrack new "버그 수정: <한 줄>" -t code --repo <repo> -i "<the user's request, verbatim>" --plan "원인 확인" "실패하는 검사 작성" "수정" "수정 되돌려 실패 확인" "전체 검사")`.
- Progress: `devtrack step $TID "<지금 하는 일>" -p <진행률>` at each phase, and one `devtrack log` per phase with the command and the
  last lines of its output in `-d` (step 2 · 4: `-k warning` "실패 확인", step 3 · 5: `-k success`).
- If the cause turns out to be something you cannot fix (wrong input data, a decision the user must make), `devtrack issue` it.

## The loop

Do not skip step 1, never skip step 4.

### 1. Verify the cause — do not infer it

Establish what actually happens with evidence you can point at: the actual value in the output file or log, the line of source you
are blaming, a value printed from a small run. Label the cause:
- **확인됨(Proven)** — I read the value / ran the code.
- **추정(Inferred)** — consistent with the evidence, not confirmed.
- **모름(Unknown)** — cannot tell from what is available; name the one file or run that would settle it.

Traps: a plausible mechanism is not the mechanism — rank candidates and rule them out by reading code. Check whether the "bug" was
deliberate: `git log -S '<the exact line>'` and read that commit; an existing test may assert the current behaviour on purpose.

### 2. Write the check — watch it fail on the current code

The check must fail **before any fix exists**. In scientific code it can be:
- a unit test in the project's framework (pytest, ctest / Catch2 / GoogleTest, pFUnit …), or
- a small reproducer: one routine on a tiny input, a single-column or few-cell case, a short run compared with a known value,
  an analytic solution, a conservation total, or a bit-for-bit baseline (with a stated tolerance).

Name it after the defect (`reveals bug: integer mask truncates partial-cell flux`), not the routine. Assert what a user would see —
the wrong number, the NaN, the lost mass, the crash — not that a function was called. If it does not fail, you have not reproduced
the bug: go back to step 1.

### 3. Fix it — smallest change

Before editing, save the original of every source file you will change (paths relative to the repo root; `tar` keeps them):
```bash
PF=$(mktemp -d)
tar -cf $PF/before.tar <source files …>      # run from the repo root
```
Make the smallest change that makes the check pass. A large fix usually means the check is too broad: narrow it and split.
Afterwards save the fixed versions: `tar -cf $PF/after.tar <same source files …>`.

### 4. Put the old code back — confirm red  ← the whole point

```bash
tar -xf $PF/before.tar      # buggy code back (only the source files; the new check stays)
<run the same check>        # MUST fail
tar -xf $PF/after.tar       # fix back
<run the same check>        # green again
```
If it stayed green, the check is fake — it exercises a path next to the bug. Rewrite it and repeat. Keep the actual failure line.

### 5. Run everything

The project's wider tests (or the agreed regression cases). A fix that contradicts an existing test is a finding: read that test and
its history (step 1) and decide deliberately — never weaken a test to get green.

## When a true red is impossible

If the buggy code cannot be called in isolation (deep inside a model driver, needs the full model), pick one and say so in the
check's header comment:

| Kind | When | Honesty requirement |
|---|---|---|
| 떼어 내서 검사 | the logic can move to a small routine | preferred if the move is small |
| 구조 검사 | the call cannot be reached but the rule is visible in source | scan the source for the rule; say it is structural |
| 모형 검사 | an interaction you cannot instantiate | mirror the real code, cite `file:line`; step 4 matters most here |
| 현재 동작 기록 | documenting behaviour you are not fixing yet | call it that, not a regression test |

## Report and record

Write the evidence record (Korean) **outside the repository** so it does not join the diff — e.g. `$PF/prove-fix-<slug>.md`:

1. **대상과 원인** — with the label 확인됨 / 추정 / 모름.
2. **검사** — what it asserts and its kind (behavioural / structural / model / current behaviour).
3. **실패 (수정 전)** — command and actual failure output.
4. **통과 (수정 후)** and **다시 실패 (수정 되돌림)** — commands and actual output.
5. **전체 검사** — what ran, result, what could not run (with the command).
6. **남은 불확실성** — and anything you got wrong earlier, corrected explicitly.

DevTrack mode:
```bash
F=$(devtrack artifact $TID $PF/prove-fix-<slug>.md -n "버그 증명 기록: <한 줄>")
devtrack artifact $TID --git-diff <repo> --files <changed files and the new check> -n "수정 diff"
```
Then, if the task has a reviewer (`끝나면 … 리뷰` in inbox / `devtrack show`), `devtrack review-request $TID`; otherwise ask for the commit:
`devtrack request $TID "<요약>" --commit -m "<commit message>" --files <same files> -a $F`
(running `/surgical-check` first is a good habit — it ends with the same request).
Without DevTrack: show the record in chat and ask before committing.

Never describe a check as "revealing the bug" unless you saw it red. "All green" is not success if nothing was ever red.

---
Adapted from `red-green-proof` (© 2026 RooAGI, MIT) in [alirezarezvani/claude-skills](https://github.com/alirezarezvani/claude-skills)
(MIT, © 2025 Alireza Rezvani). Changes: DevTrack recording, no git commands that touch the user's work, HPC run approval, checks for
numerical code, Korean output. See `../NOTICE.md`.
