---
name: surgical-check
description: Before asking for commit approval, check that the change contains only what the request needs - every changed line traces to the task, no drive-by reformatting, renames, debug prints, stray or build files - and that assumptions and verification are stated. Removes your own noise, then asks for the commit through DevTrack (never commits directly). Use when development work is done, before "request --commit" or "review-request", or when asked "did I overdo this", "pre-commit check".
argument-hint: "[repo path] [no-devtrack]"
allowed-tools: Bash(devtrack *)
---

# Surgical check before the commit request

Every changed line should trace directly to the user's request. This check runs after development and before the commit approval
request, so the user approves a small, readable diff of changed source only.
Adapted for DevTrack from `karpathy-coder` (see the end of this file).

Arguments: $ARGUMENTS — a repo path (default: the repo of the current DevTrack task, else the current directory); `no-devtrack` turns
DevTrack recording off for this run.

Language: the check report, DevTrack logs and the request text are **Korean**. Keep code, paths, commands and commit messages as the
project uses them.

## Hard rules

- **Never `git commit`, `git push`, `git add -A`, `git stash`, `git reset`, `git checkout -- <path>` or `git restore`.** Commits go only
  through the user's approval: `devtrack request … --commit` (DevTrack) or asking in chat. If the user tells you in chat to commit, follow that.
- Remove only noise **you introduced in this task**. Pre-existing dead code, odd formatting or old bugs are listed, not touched.
  When you cannot tell whether a hunk is yours, leave it and list it as a question.
- Undo noise by editing the hunk back, never by checking out a whole file (that would also drop the wanted change in it).

## Steps

### 1. What was asked

Read the request: the DevTrack task (`devtrack show <TID>` — instruction, plan, user messages) and this conversation. Write it down as
one to three sentences of success criteria. Everything below is measured against these.

### 2. Collect only the changed source

```bash
git -C <repo> status --porcelain
git -C <repo> diff --stat ; git -C <repo> diff --cached --stat
```
Sort every changed or new path into:
- **소스 변경** — source, tests, docs, config the request needs.
- **빌드 결과물** — CMake build folders (`CMakeCache.txt`, `CMakeFiles/`, `cmake_install.cmake`), `build/`, `*.o` `*.a` `*.so` `*.mod`
  `*.pyc` `__pycache__/`, executables. DevTrack's diff and commit leave these out automatically; never add them with `--files`.
- **딸려 온 파일** — files that are neither: run outputs (`*.nc`, `log.*`, `*.out`), editor backups (`*~`, `*.orig`, `*.rej`, `*.swp`),
  scratch scripts, copies. Delete only the ones you created in this task; list the rest.

### 3. Trace every hunk

Read `git -C <repo> diff -- <source files>` hunk by hunk and tag each one:
- **요청** — directly needed for the success criteria.
- **따라온 변경** — a required consequence: callers updated, a test for the change, docs or namelist defaults that describe it,
  imports or variables your change made unused (remove those).
- **잡음** — not needed: reformatting or whitespace churn, reordered imports, renamed variables, rewritten comments, changed
  indentation style, debug prints / `write(*,*)` / `print`, commented-out code, "while I was here" refactors, speculative options or
  abstractions nobody asked for.

Edit the 잡음 hunks back to the original text (use `git -C <repo> diff` to see the original). Re-run the diff until only 요청 and
따라온 변경 remain.

### 4. The four principles, briefly

For the remaining change, answer each in one or two lines (they go into the report):
1. **가정** — what did the change assume that the request did not say (units, array sizes, which configuration, default values)?
   Anything unresolved becomes a question to the user, not a silent choice.
2. **단순함** — is there a clearly smaller way? (Single-use helper layers, unused configurability, error handling for impossible cases
   are signs.) Simplify if it is your code and the gain is clear.
3. **최소 변경** — confirmed by step 3.
4. **검증** — which build / test / run shows the success criteria are met, and its actual result. Not run → say so and give the command.

### 5. Report and request

Write `$W/surgical-check-<TID or slug>.md` (Korean), with `W=$(mktemp -d)` — outside the repository, so the report never shows up in the diff:
- 요청 (성공 기준)
- 변경 파일 표: `파일 | +/− 줄 | 분류 | 메모`
- 되돌린 잡음 (what and why), 남긴 의문 (hunks you were unsure about), 손대지 않은 기존 문제
- 가정 · 단순함 · 검증 (step 4)

DevTrack mode (`devtrack --version` succeeds, `no-devtrack` not given); use the task this conversation works on:
```bash
F=$(devtrack artifact $TID $W/surgical-check-<TID>.md -n "커밋 전 변경 점검")
```
- If the task has a reviewer (`끝나면 … 리뷰` in inbox / `리뷰:` in `devtrack show`): `devtrack review-request $TID -m "<봐 줄 부분>"`.
  After it passes, run this check again only if you changed code since.
- Otherwise: `devtrack request $TID "<요약>" --commit -m "<commit message>" --files <소스 변경 files> -a $F`
  (the diff is attached automatically, build outputs excluded). Then wait for the decision as usual; `devtrack apply` only after approval.

Without DevTrack: show the report and the final `git diff --stat` in chat and ask whether to commit.

## When to relax

For a one-line or typo fix, do steps 2–3 quickly and skip the report file; still never commit without approval.

---
Adapted from `karpathy-coder` (claude-code-skills, MIT) in [alirezarezvani/claude-skills](https://github.com/alirezarezvani/claude-skills)
(MIT, © 2025 Alireza Rezvani), which follows Andrej Karpathy's notes on LLM coding pitfalls. Changes: prompt-only (no scripts or hooks),
build-output and stray-file sorting for compiled scientific code, DevTrack commit approval, Korean output. See `../NOTICE.md`.
