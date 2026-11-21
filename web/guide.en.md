# DevTrack Guide

A step-by-step manual for first-time users: **follow it from the top** and you are ready to go.
Steps 1–5 are one-time setup; from step 6 on is how you use DevTrack every day.

## 0. What is DevTrack?

You give work to an AI such as Claude, and DevTrack shows on the web **what it is doing, where it is stuck, and what it has produced**.
Things that are hard to undo, such as committing to a repository or commenting on a PR, go ahead **only after you approve them on the web**.

```text
 ① You: give a task  ──▶  ② Claude: works and records in DevTrack
                              (current step · issues · artifacts · approval requests)
 ④ You: approve / request changes  ◀──  ③ Web dashboard: progress · notifications
          │
          └──▶ ⑤ Claude: commits · pushes · posts only what you approved
```

Common terms:

| Term | Meaning |
|---|---|
| Task (`T-0001`) | One piece of work. The instruction, plan, progress log, issues, artifacts and approval requests all live here |
| Step · progress | Updated each time Claude records "what I am doing now" |
| Issue (`I-1`) | A problem or blocker found during the work |
| Artifact (`F-1`) | A report, figure, code change (diff), paper PDF, notebook, etc. — you can open it right on the web |
| Approval request (`A-1`) | Claude asks you to check before moving on. You answer with **Approve · Request changes · Reject** |

## 1. Check the prerequisites

Make sure these commands run in a terminal.

```bash
python3 --version      # 3.7 or later (on HPC systems: module load python or similar)
git --version
```

- **Claude Code** — the VS Code extension or the `claude` command for the terminal. This is where the AI works.
- **GitHub CLI (`gh`)** — needed only for code reviews. Check that you are logged in with `gh auth status`.

DevTrack uses only the Python standard library, so there is nothing to install with `pip`.

## 2. Download DevTrack

```bash
git clone git@github.com:hyungyukang/DevTrack.git ~/devtrack
```

The rest of this guide assumes you cloned it to `~/devtrack`. If you used another location, change the path accordingly.

## 3. Add the `devtrack` command to PATH

Add the path to your shell startup file so that you — **and Claude** — can run `devtrack` from any folder.

```bash
echo 'export PATH="$HOME/devtrack/bin:$PATH"' >> ~/.zshrc     # for bash, use ~/.bashrc
source ~/.zshrc
devtrack --version                                           # → devtrack 0.1.0
```

Records are saved in `~/devtrack/data/` by default. To keep them elsewhere, run `devtrack config data ~/devtrack-data` once.
It is saved in a settings file (`~/.config/devtrack/config.json`), so devtrack writes to the same place whichever terminal or program runs it.
`devtrack config` shows where records go now.
`export DEVTRACK_DATA=…` also works, but devtrack run where that variable is missing writes to the default folder.
Examples are a terminal opened before you set it, and Claude and its hooks in VS Code.

## 4. Open the web dashboard

```bash
devtrack serve --open          # → http://localhost:8765
```

- **Keep this terminal window open.** Closing it stops the web server.
- If the top right of the page shows **Live · local server**, everything is working. In this mode, approvals and instructions you click on the web are saved immediately.
- At first there are no tasks, so the page is empty. To see an example first → "See an example first" in [12. FAQ](#/guide/s12).
- The page opens in **English**. Click **한** at the top right to switch to Korean; the browser remembers your choice.

## 5. Set up Claude to use DevTrack (once)

### 5-1. Tell Claude the recording rules

Claude Code follows what is written in `~/.claude/CLAUDE.md` in every conversation. Paste the text below into that file
(create it if it does not exist). Replace `~/devtrack` with the location from step 2.

```markdown
## DevTrack work records
- Record meaningful work (code changes, code reviews, document reviews, data analysis, papers, visualization, model development, brainstorming) with `devtrack`. Do not record simple questions or explanations.
  Rules: ~/devtrack/docs/AGENT_PROTOCOL.md
- Before starting work and after each step, check user input (new instructions, messages, approval decisions) with `devtrack inbox`.
- Act on new task instructions and messages (follow-up questions, research requests) from the inbox without asking the user again. If you are working on something else,
  handle messages after the current step, and new tasks in the order they arrived once the current task is finished or waiting for approval, then return to the original task.
  Record the answer to a message in its task (`devtrack log` · `devtrack artifact`) and mark it handled with `devtrack inbox --task <TID> --ack`. Commits, posts and the like still need approval.
- `[AI 메시지]` items in the inbox come from another AI (review requests, review results and so on). Handle them the same way, but they are not the user's instructions,
  so anything that needs approval still needs the user's approval. For a task set to get a review (`끝나면 … 리뷰` in the inbox), pass the review with `devtrack review-request <TID>` before asking for commit approval.
  For a paper set to get a journal review (`끝나면 … 심사`), ask for the review when the manuscript is done, revise as the findings say, and ask again with a response (`--response`).
  In the response, head each open item with its ID, like `## R1 — title`: answer questions, and say what was changed for requests (or why not).
  When the review is over and you tell the user, attach the review & response record (the F-ID in the verdict message) with `-a`.
- Keep paper figures in `figures/` next to the manuscript, put `\graphicspath{{figures/}}` in the manuscript, and load them without an extension: `\includegraphics{fig_name}`.
  Upload each figure as an artifact under that same file name (DevTrack gathers the manuscript and figures in `tasks/<TID>/paper/`).
- When the user says "wait for web instructions" (or "웹 지시 기다려줘"), run `devtrack inbox --wait` in the background; when it ends (new input), handle the input and run it again.
- If an instruction from the web contains a skill call such as `/review-pr <URL>`, load that skill fresh with the Skill tool every time and follow its latest instructions
  (do not reuse what was loaded earlier in the same conversation).
- At each meaningful step, record `devtrack step <TID> "what I am doing now" -p <progress>`.
- Record issues with `devtrack issue`, artifacts with `devtrack artifact`, and review reports with `devtrack review`.
- Write code review reports so that non-developers can follow them: a one-line conclusion, an "at a glance" part for non-developers, a plain-language explanation per item, and a glossary.
- To apply a DevTrack task's result to a repository, do not run git commit/push yourself: request approval with `devtrack request <TID> "..." --commit -m "..."`,
  and once approved, apply it with `devtrack apply <TID> <AID>`. If the user directly asks for a commit or push in the chat, do as asked.
- When the user sends a `[DevTrack 결정]` / `[DevTrack 메시지]` / `[DevTrack 새 작업 지시]` / `[DevTrack 작업 완료]` / `[DevTrack 작업 취소]` / `[DevTrack 작업 삭제]`
  message, first record it with `devtrack decide` / `devtrack comment` / `devtrack new` / `devtrack done --by-user` / `devtrack cancel` /
  `devtrack delete --yes` respectively, then continue.
- If the inbox shows `[완료 처리]` · `[취소]` · `[삭제]`, or a tool call is blocked with a `⛔ DevTrack` message, stop that task immediately, record only the acknowledgement as instructed
  (`devtrack inbox --task <TID> --ack`), and record nothing more for it. Delete a task only when the user asked for it and confirmed.
```

> The settings take effect **from the next new Claude conversation**. Restart any conversation that is already open.
>
> The bracketed tags (`[DevTrack 결정]`, `[완료 처리]` and so on) stay in Korean: they are exactly what DevTrack prints, and Claude understands them either way.

### 5-2. Install the review skills (if you will ask for code or document reviews)

The `/review-pr` skill posts code review results to DevTrack and lets you pick on the web which items to comment on.
The `/review-doc` skill reviews a paper, report or proposal (PDF, Word, LaTeX) and posts the summary and what to fix as a PDF report ([Document review](#/guide/s9)).

```bash
mkdir -p ~/.claude/skills/review-pr ~/.claude/skills/review-doc
ln -sf ~/devtrack/integrations/claude-skills/review-pr/SKILL.md ~/.claude/skills/review-pr/SKILL.md
ln -sf ~/devtrack/integrations/claude-skills/review-doc/SKILL.md ~/.claude/skills/review-doc/SKILL.md
```

Linking instead of copying means `git pull` keeps the skill up to date too.
To receive review reports by email, the `pr-review-report` skill must also be installed
([details](https://github.com/hyungyukang/DevTrack/tree/main/integrations/claude-skills)).

### 5-3. Fewer permission prompts (optional)

If a confirmation prompt appears every time Claude runs `devtrack`, open `/permissions` in Claude Code and add the allow rule
`Bash(devtrack:*)`. `devtrack` only records; commits and pushes run only for requests you approved on the web.

### 5-4. Deliver cancellations right away (recommended)

Claude checks DevTrack only at the end of each work step. So if you cancel or delete a task on the web mid-step, Claude may keep working
until that step ends. With the **hook** installed, DevTrack checks "has this task been cancelled?" **right before** every command Claude runs
or file it edits, and if so, blocks that action and makes Claude stop.

Add this to `~/.claude/settings.json`. Create the file if it does not exist; if it already has `"hooks"`, just add the entry to its
`"PreToolUse"` list. Replace `/Users/me/devtrack` with the **full path** of the location from step 2.

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

- The hook checks **only the tasks of that conversation** (about 0.05 s each time). Other conversations and tasks are not affected.
- `devtrack-hook` runs `devtrack hook`, but is built so that it never blocks Claude when something goes wrong.
- A blocked Claude records the acknowledgement with `devtrack inbox --task T-0003 --ack` and stops. After that it works as usual.
- The settings take effect **from the next new Claude conversation**. For Codex, see step 11 ⑤.

## 6. Give your first task

There are three ways. Use whichever is convenient; the result appears on the web the same way.

### Option A — Instruct in the Claude chat

Give the instruction in the Claude chat in VS Code (or in `claude` started in a terminal) as usual.

```
Port the pressure solver in ~/project to HIP. Verify it too, and get my approval before committing.
```

Claude creates a task (`T-0001`) in DevTrack and starts recording its progress. It shows up on the web dashboard right away.

### Option B — Instruct from the web (waiting mode)

1. Say this once in the Claude chat, and keep that conversation (VS Code window) open. To do this in a terminal without VS Code, see Option C.
   ```
   wait for web instructions
   ```
2. Click **New task** at the top left of the web page and fill it in.

   | Field | What to enter |
   |---|---|
   | Title | A one-line summary (e.g. `Code review of PR #42`) |
   | Type | Code development · Code review · Paper writing · Document review · Data analysis · Visualization · AI model · Brainstorming · Other — decides which menu (work area) shows it. Document review switches to fields for the document path, kind and purpose ([step 9](#/guide/s9)) |
   | Assigned AI · Priority | The AI that will handle this task — only that AI picks it up (for Codex, see [step 11](#/guide/s11)). Right below, the page shows which AI is waiting for web instructions now |
   | Model · Effort | The AI model and effort for this task (optional, e.g. Opus 5.5 · Extra high) — only that model handles it ([6-1](#/guide/s6-1)) |
   | Repository path · Branch | The repository to change (optional) |
   | Get a review · Get a journal review | Only for Code development and Paper writing (optional). Another AI reviews the work when it is done ([6-2](#/guide/s6-2), [6-3](#/guide/s6-3)) |
   | Related tasks | e.g. the paper task that will use this analysis (optional). Pick several from the list one after another, or type task numbers in the box next to it (`T-0001, T-0002` or `1 2`) |
   | Instruction | Goal, references, definition of done, when approval is needed — the more specific, the better |

3. Click **Submit task**, and Claude starts the task automatically within a few seconds.
   The top of the task page shows whether the assigned AI is currently waiting for web instructions (local server mode).
   - **Claude is waiting for web instructions** (green): Claude in waiting mode or `devtrack watch` will pick it up shortly.
   - **No Claude is waiting for web instructions** (yellow): wake it up as shown. Copy the text shown and paste it into the chat.
   - **…has already seen this task and did not start it** (yellow): the waiting AI has already seen this instruction, so it will not wake up for it again.
     Ask directly in the chat, e.g. "go ahead with T-0007", or restart the runner.
   - **…on a different model, so it won't take this task** (yellow): the model set for the task differs from the waiting chat's model — see [6-1](#/guide/s6-1).

> If waiting mode is not on, give the instruction on the web and then say once in the Claude chat: "check devtrack inbox and go ahead".
>
> If the AI is busy with another task, it continues without asking — messages and follow-up questions sent from a task page are handled after the current step,
> and new tasks start once the current task is finished or waiting for approval.

### Option C — Keep Claude running in a terminal and instruct from the web (without VS Code)

You can also run `claude` in a terminal, without opening VS Code, and give all instructions from the web. This is Option B in a terminal instead of VS Code.
If you do not have the `claude` command for the terminal, install it as in [10-3](#/guide/s10-3) ① (the same steps work on your own computer).

1. Open a terminal and start Claude in the folder where it will work.
   ```bash
   cd ~/project        # the folder Claude mainly works in
   claude
   ```
2. Once Claude is up, say this once.
   ```
   wait for web instructions
   ```
   Claude starts `devtrack inbox --wait` in the background and waits. **Leave this terminal window open.**
3. Give instructions from the web with **New task** (steps 2–3 of Option B). Within a few seconds the Claude in this terminal starts the task, and it waits again when it is done.
   Follow progress and approval requests on the web, and approve there too.

Good to know:
- **Permission prompts appear in this terminal.** While Claude waits for permission to run a command or edit a file, the task looks stalled on the web.
  Check the terminal now and then, or allow frequently used commands in advance as in [5-3](#/guide/s5-3).
- **Working folder:** Claude mainly works with files in the folder where it was started. If you often give it repositories in other folders, start it in a parent folder or add a folder with `/add-dir <folder>` in Claude.
- **Closing the window stops it:** closing the terminal window ends Claude too. To keep it running after you close the window, run it inside `tmux`, as in [10-4](#/guide/s10-4)
  (`tmux new -s claude` → `claude` → "wait for web instructions" → press and release `Ctrl+b`, then `d`). While the computer is asleep, Claude does not work.
- **Waiting mode in one place only:** if both Claude in VS Code and Claude in a terminal are in waiting mode, both can receive the same instruction and start it together.
- **To stop waiting:** tell the Claude in the terminal "stop waiting for web instructions", or end Claude with `/exit`.

### 6-1. Choose the AI model for each task (optional)

You can set the AI model and effort for each task. For example, a hard porting task can get **Opus 5.5 · Extra high**,
and a quick cleanup **Sonnet 5.5**. A task with a model set is handled only by that model.

- **Where to set it:** the **Model (optional)** and **Effort** fields on **New task**. For an existing task, click the model shown
  near the top of the task page (e.g. `Opus 5.5 · Extra high Change`). In a terminal, use `devtrack model T-0001 opus-5.5 --effort xhigh`, and `--clear` to remove it.
- **The model list** follows the assigned AI. Claude shows the Claude models, Codex shows the models this computer's Codex has fetched
  (`~/.codex/models_cache.json`, created the first time Codex runs; a built-in list is used without it), and **Anyone** shows both.
  For a model that is not in the list, choose **Type a name…** and enter its name.
- **Effort** lists only the levels the chosen model takes. Claude takes Low – Max. For Codex it depends on the model, and the current
  models take Low – Ultra. A level the model does not take is refused on save. In a terminal, `devtrack model --list` shows the models and their levels.
- Leave it empty, and any model handles the task, as before.

**Use the runner (the surest way).** Each time the runner runs a task, it starts the AI with that task's model and effort.

```bash
devtrack watch --claude      # Claude: runs claude -p --model … --effort …
devtrack watch --codex       # Codex: runs codex exec -m … (step 11)
```

- Later messages and approval decisions on the same task **continue the same conversation** (both Claude and Codex), so the task keeps its context to the end.
  If the conversation record is gone and it cannot continue, it runs in a new conversation, which picks up from the DevTrack records.
- The runner's Claude has no chat window to ask for permissions, so it runs in the `auto` permission mode. To change it, put the option after `--`
  (`devtrack watch --claude -- --permission-mode <mode>`).
- Keep this terminal window open (stop with Ctrl+C). If both the runner and a chat in waiting mode are on, both can receive the same instruction, so keep only one on.

**Use a chat in waiting mode (Options B and C).** A running chat cannot change its own model, so it skips tasks set to a different model or effort.
DevTrack reads the chat's model and effort from the Claude Code session record to check.

- If the task page says **"Claude is waiting, but on a different model, so it won't take this task"**, do one of three things:
  start the runner; switch that chat with `/model claude-opus-5-5` · `/effort xhigh` and then say "go ahead with T-0012"; or change the task's model.
- If you change the model of a task already under way, it applies from the next message.

### 6-2. Let AIs review each other's work (optional)

A code task can be set to get a review from another AI once the work is done. For example, Claude develops and Codex reviews.
If the review asks for changes, the AI that did the work fixes them and asks again; once the review passes, it asks for commit approval. No one has to pass the results along in between.

1. On **New task**, keep the type at **Code development** and, under **Get a review**, choose the reviewer AI and the **max review rounds** (3 by default).
   You can also set the reviewer's model and effort ([6-1](#/guide/s6-1)).
2. When the work is done, the assigned AI asks for a review. DevTrack makes a **review task** (e.g. `T-0012`) with the diff of the code changed so far and gives it to the reviewer.
3. The reviewer records findings (R1, R2…) and a verdict (**Approved** / **Changes requested**). The result goes to the AI that did the work as an **[AI 메시지]** (AI message).
4. If changes are requested, that AI fixes them and asks again. From the second round on, a diff of **what changed since the last review** goes along too.
   The reviewer continues the same conversation, so it checks that its earlier findings were fixed.
5. Once the review passes, the AI asks for commit approval, and the request shows "This code passed review round 2 by Codex".
   **Nothing is committed or pushed until you approve, as before.** If all rounds are used and changes are still requested, the AI asks you to decide, with the open findings (commit as is, fix more, or allow more reviews).

The **AI review** box on the task page shows each round's result (e.g. round 1 changes requested → round 2 approved), the open items and the reviewed changes. Cancelling the task stops the loop.

- **Both AIs must be running for this to go on by itself.** For example, run `devtrack watch --claude` in one terminal and `devtrack watch --codex` in another.
  Claude can also be a chat in waiting mode.
- **The same AI works too** (e.g. Claude develops and Claude reviews, even with the same model). The review is still done by a **new conversation, not the one
  that wrote the code**, so the code is not re-read in the same context. With just `devtrack watch --claude` on, development and review run in separate conversations for each task.
  The waiting-mode chat that did the work does not take that review task (`[다른 대화가 리뷰]`).
- **Rules that are enforced**
  - A task set to get a review cannot ask for commit approval before the review passes. If the code changes after it passes, the change has to be reviewed again.
    To commit without a review, say so in the chat.
  - The conversation that did the work cannot take its review. To have the same conversation review it anyway, say so in the chat.
  - The reviewer only reviews and does not edit files. Once a commit is applied, later changes on the task are reviewed again and the rounds are counted afresh.
- **The diff holds only changed code.** CMake build folders (folders with a `CMakeCache.txt`), `CMakeFiles/`, objects, libraries and executables (`.o` · `.a` · `.so` and so on), build folders such as `build/` · `cmake-build-*/`, and `__pycache__/` are left out of the review diff and the commit even when they are not in `.gitignore`, and what was left out is shown separately.
  If a left-out file really belongs in, name it exactly in the request (`--files`).
- **AIs asking each other directly:** `devtrack send <task> "message"` reaches the inbox of the AI that has the task, as an [AI 메시지].
  It is not the user's instruction, so anything that needs approval still goes to the user.
- In a terminal, set it with `devtrack new … --review-by Codex --review-rounds 3`, or for an existing task ask with `devtrack review-request T-0001 --reviewer Codex`.

### 6-3. Journal review loop for papers (optional)

When the manuscript of a paper task is done, another AI can review it **as a referee for the target journal**. The author AI then revises it and asks for another review.
As in a real journal review, a verdict (**accept · minor revision · major revision · reject**), findings (R1, R2…) and the author's **response to reviewers** go back and forth.

1. On **New task**, keep the type at **Paper writing** and choose the referee AI and the maximum rounds under **Get a journal review**.
   Enter the **manuscript to review** (e.g. `paper/main.tex`, a Word `.docx`, a PDF or Markdown) and the **target journal** (e.g. Journal of Fluid Mechanics).
   If you leave the manuscript empty, the AI sets it when it asks for the review. You can also set the referee's model and effort ([6-1](#/guide/s6-1)).
2. When the manuscript is done, the author AI asks for a review. A **review task** (a document review) is made with the manuscript as it is at that point.
   - For LaTeX, the main file comes with the files it pulls in with `\input` · `\include`, the bibliography (`.bib`) and the PDF of the same name.
     The **figures** the manuscript loads with `\includegraphics` come along too, so the referee looks at the figures themselves.
     A round where only figures changed can be reviewed again, and the review request names the changed figures.
   - For Word, the original and its text come along.
3. The referee AI looks at novelty and contribution over prior work, soundness of the method, whether the results support the conclusions, figures and tables, and the writing.
   Each finding has a location, the original text and a fix (in the manuscript's language), and the referee sends a referee report and a verdict.
4. If revisions are needed, the author AI revises the manuscript, writes a **response to reviewers** and asks again. From round 2 on the response is required.
   - The response is split by item, `R1`, `R2` …. Questions get an answer; requests get how they were handled (or why not).
   - If any item left open in the last round has no answer, the request is refused.
   - The referee looks at **what changed since the last review** and the response, continuing the same conversation, and checks whether the earlier points were resolved.
5. On **accept**, the author AI reports the result to you. On reject, or once all rounds are used, it asks you what to do next.
   Either way it attaches the **review & response record** below.

**Review & response record** — a report that gathers the referee's questions and requests and the author's answers, round by round. It is an artifact of the paper task.
- The table at the top shows each round's verdict, how many questions and requests there were, how many were answered, and how many were resolved in the next review.
- Then each item shows its location, the quoted text, the referee's question or request, the author's answer, and whether the next round resolved it.
- DevTrack updates it to a new version whenever a verdict or a response arrives. Earlier versions are kept.

The **AI journal review** box on the task page shows each round's verdict, the open items, the manuscript, changes and response, and a link to the **review & response record**. The review task also appears on the **Document review** page.

- The same rules as for code review apply. Both AIs must be running for this to go on by itself, the conversation that wrote the manuscript does not take its review, and the referee does not edit the manuscript.
- A journal review does not block commits (drafts can be committed to the repository as often as you like).
- In a terminal: `devtrack new … -t paper --review-by Codex --review-doc paper/main.tex --review-venue "J. Fluid Mech."`; to ask again,
  `devtrack review-request T-0005 --response response.md`.

## 7. Follow the progress

- **Dashboard** — counts of tasks in progress, awaiting approval, open issues and done; items that need your attention; cards for tasks in progress; recent activity.
- **Tasks** — the full task list. Click a task to open its detail page.
- **Task detail** — under the title, the assigned AI, model, repository and related tasks; below that, "what I am doing now" and the progress bar, with tabs below.
  - Overview: instruction, plan checklist, recent records, open issues, artifacts. A task set to get a review has an **AI review** or **AI journal review** box at the top ([6-2](#/guide/s6-2), [6-3](#/guide/s6-3))
  - Progress log · Issues · Artifacts · Approvals (paper tasks also get a Builds tab)
- **Activity** — the records of all tasks, by date (the latest 300).
- **Notifications** — the bell icon at the top right shows how many approvals are waiting, and a notice pops up in a corner of the page for new approval requests, issues and completions.
  In the bell menu, turn on **Desktop notifications** to be notified even while you are looking at another window.
- The page **refreshes automatically every 10 seconds**. With the local server or on the static web, the records and artifacts the AI leaves show up without reloading the browser,
  and the page checks right away when you come back from another tab. To see changes right away, click the refresh button at the top right.
  (On the static web the page changes once the AI's records are copied to the public web folder — [step 10](#/guide/s10))
  - Text you have typed into a box but not sent stays there when the page refreshes.
  - While the cursor is in a text box (you are typing) or you have ticked approval choices, the page does not change; it shows **Show new updates** and applies them by itself once you leave the box.
    The **New task** page is not redrawn once you start filling it in, so what you wrote is not lost.
  - When an artifact gets a new version, figures and PDFs load the new file.
- **How artifacts are shown** — the button at the top right of the **Artifacts** page and of a task's **Artifacts** tab switches between **cards** (a grid) and a **list** (one row each).
  The list shows full titles together with the task number, kind, time and size, and figures get a small preview. Your choice is remembered in the browser.
- **Artifact order** — switch it with the **Newest** · **By task** buttons next to it. It starts as **Newest**: the artifact added or updated to a new version most recently is at the top.
  **By task** groups artifacts under a heading for each task, highest task number first, and lists each task's artifacts in the order they were made (F-1, F-2 …).
  A task's **Artifacts** tab offers **Newest** · **Created order**. The browser remembers this choice too.
- **Display language (English / 한국어)** — the page opens in English. Click **한** at the top right to switch the page frame — menus, headings, buttons, status labels — and this guide to Korean;
  click **EN** to switch back to English. The browser remembers your choice. Task titles, instructions, progress logs, artifacts and review reports stay in the language they were written in (usually Korean).
  Messages for the AI (the paste-in text in static mode) are also generated in Korean.

## 8. Approve · request changes · reject

When Claude reaches a point that needs your check, it shows up in the **Approvals** menu and on the bell icon.

1. Open a request to see its description and what to review (a code diff, report, figures, etc.).
2. Add a comment if needed and click a button.

   | Button | Result |
   |---|---|
   | **Approve** | Claude moves on to the next step. For a commit request, it commits and pushes exactly that content |
   | **Request changes** | Claude makes the changes in your comment (required) and posts a new approval request |
   | **Reject** | That content does not go ahead |

3. For requests where you pick from a list (e.g. "pick the items to comment on"), only the checked items go ahead.

Safeguard: a commit request **does not run if the actual change differs even slightly from the diff you approved.** If the code changes after your approval, Claude posts a new request.
The commit holds only the files in the diff. Build outputs (CMake build folders, `.o` files, executables and so on) are left out even when they are not in `.gitignore`, and the request lists them under **Build outputs left out** ([6-2](#/guide/s6-2)).

If you want to tell Claude something during the work, write it in the **Message to Claude** box (named after the assigned AI) on the **Progress log** tab of the task detail page and send it. Claude picks it up at its next step.
User messages in the progress log show whether the AI has picked them up (**Seen by AI** / **Not yet seen by AI**).

**Request changes · further instructions** — open an artifact (a report, figure, table, code and so on), and this box is at the bottom.
- A message sent here reaches the AI as **a message about that artifact** (e.g. "show column 2 as percentages", "make a 3D figure the same way").
- The AI uploads the revised artifact as a **new version (v2, v3…)** of the same artifact. You see the new version on the same page, and earlier ones through the "Earlier versions" link.
- Once you send one, **requests sent about this artifact, and new versions** pile up above the box in order (request → v2 → next request …).
- In static mode it creates a message to paste to Claude. In a terminal: `devtrack comment T-0003 --artifact F-2 "…"`.

### Mark as done · Cancel task · Delete

Use the buttons at the top right of the task detail page.

| Button | Result | Undo |
|---|---|---|
| **Mark as done** | Sets the status to 'Done' (100 %) even if the work is unfinished. Use it when you finished the rest yourself or no more work is needed. Records and artifacts stay, and approval requests still waiting for a decision are withdrawn (a commit request is not committed). You can leave a note | Records are kept |
| **Cancel task** | Sets the status to 'Cancelled' and removes it from the in-progress list. Records and artifacts stay, and approval requests still waiting for a decision are withdrawn | Records are kept |
| **Delete** | Erases **everything** — records, artifact files and approval requests. Links from other tasks are removed too | Not possible |

- Clicking Delete opens a **confirmation dialog**; the **Delete permanently** button works only after you type the task ID (e.g. `T-0003`) exactly.
- Each of them sends the assigned AI a notice to stop this task. When it stops depends on how the AI is working.
  - A task run by `devtrack watch`: stops **right away** (the running AI is terminated).
  - Claude or Codex in a conversation: with the hook installed (5-4, step 11 ⑤), stops **right before its next command or file edit**.
    Without the hook, it stops when the current step ends and it checks the inbox.
  - While the AI is writing a long answer without running commands, it cannot be interrupted. If it is urgent, click the stop button in the conversation window.
- Normally use **Cancel**, and **Delete** only tasks created by mistake or for testing. Deleted task numbers are never reused.
- In static mode, all three buttons create a message to send to Claude instead.
- In a terminal: `devtrack done T-0003 --by-user -m "note"`, `devtrack cancel T-0003 -m "reason"`, `devtrack delete T-0003` (confirm by typing the task ID again).

## 9. Use by field

### Code development

1. Give the instruction with type **Code development** and the repository path.
2. The **Code development** menu collects the code tasks.
   - Each task card shows the repository · branch and what is being done now
   - **Applied commits**: what was actually committed and pushed after approval (click a commit hash to open it on GitHub)
   - **Code and changes**: code files and diffs posted by the AI
   - The **Awaiting approval** filter shows only the tasks that need your check
3. Check the result as a diff in **Approvals**. When you approve, Claude commits and pushes (the commit hash is recorded).

### Code review

1. In the Claude chat, `/review-pr https://github.com/<org>/<repo>/pull/<number>` — or instruct on the web with type **Code review** and put the same command in the instruction.
2. The review appears in the **Code review** menu.
   - **Review at a glance**: one-line conclusion, review verdict, remaining items by severity (Blocker · Major · Minor · Nit · Question)
   - A **plain-language explanation** for each item and a link to the code location on GitHub
   - The full report below — written (in Korean) so that non-developers can read it too. A **PDF** with the same content is attached for download or sharing.
     When the report is revised, the same artifact gets a **new version (v2, v3…)**, and earlier versions are available through the "Earlier versions" link on the artifact page
3. Check the **items to write PR comments for** and click **Proceed with selected**. Nothing is posted to the PR yet.
4. Claude posts draft comments for the chosen items; they are posted to the PR only after you check them and click **Approve**.

### Paper writing

1. Give the instruction with type **Paper writing**. If you list the chapters and sections in the instruction, they appear as a plan checklist.
2. Check each paper in the **Paper writing** menu.
   - The **compiled PDF** (page count, undefined citation and reference warnings, error locations if compilation failed)
   - `.tex` manuscripts can be read in **Document view** instead of as code (with equations, tables and figures; you can switch back to the source)
   - Figures, linked data analyses, open issues
3. If the paper needs an analysis, click **New analysis for this paper** on the paper card. The analysis task is linked to the paper.
4. **Manuscript folder** — each paper task keeps a manuscript you can compile as is or upload to Overleaf in `tasks/<task ID>/paper/` in the records folder.
   - It holds the latest `.tex` and `.bib` the AI uploaded, under their original file names. Figures the manuscript loads with
     `\includegraphics` are gathered into `figures/` from figure artifacts of the same name (from this task, or else from a linked analysis task).
     Figures the manuscript does not load are left out.
   - If the manuscript uses `\graphicspath{{figures/}}` and loads figures without an extension (`\includegraphics{fig_kappa}`),
     it also compiles with PNG figures when there is no PDF. This is part of the AI's working rules.
   - The folder is rebuilt whenever the artifacts change, so do not edit files in it.
     `devtrack paper <task ID>` shows its state and any missing figures (an extension that does not match, or no figure artifact).
   - Moving just the records folder to another computer brings the manuscript along. For example, a paper started on a Mac can be continued on an HPC system.

### Document review

Have Claude read a paper, report or proposal and give you a **summary** and **what to improve or fix**. The result comes as a PDF report.

1. Give the instruction with type **Document review**. Choosing this type on the New task page shows its own fields.
   - **Path to the document**: a PDF, Word (`.docx`) or LaTeX (`.tex`) file, as a path on the computer Claude works on (save an old `.doc` file as `.docx` or PDF first)
   - **Document type**: scientific paper, report, proposal, thesis or other
   - **Purpose and destination**: e.g. check before journal submission, prepare a grant proposal for review. The review judges how well the document serves this purpose
   - In the chat you can also say `/review-doc ~/papers/draft.pdf check before journal submission` (install the skill in [step 5-2](#/guide/s5-2))
2. Claude goes in this order: summary of the document → what is missing for its purpose → overall logic → awkward sentences and spelling.
   For a **scientific paper** it looks more closely at the format, how the work differs from prior studies, and the reasoning that leads to the conclusions.
3. See the results in the **Document review** menu.
   - **Review at a glance**: the verdict (ready to submit, minor revision, major revision, rewrite) with a one-line conclusion, the **document summary**,
     a rating per area (good, needs work, poor) and the strengths
   - **What to improve or fix**: each item (R1, R2…) has a plain explanation, its location (page, section) and, where it applies, **original → suggested revision**.
     Minor items such as spelling are folded
   - Below, switch between the **review report (PDF)** and the **original document**. For a Word original only the text and tables are shown (download it to see figures and formatting)
4. When Claude posts "please check the report", look at it and click **Approve**. Write what should change in the comment or in **Send feedback on the review**.
   If you want a revised copy of the document itself, send "make a revision with R1 and R3": the original file stays as it is and the revision is uploaded separately.

### Data analysis · visualization

1. Give the instruction with type **Data analysis** or **Visualization**. Say where the data is and what you want to know.
2. The **Data analysis** menu shows a figure gallery, tables (CSV), notebooks and summary reports. The **For paper** filter shows only analyses linked to a paper.
3. Results that need interpretation are posted by Claude as approval requests (result checks).

### Brainstorming

1. Give the instruction with type **Brainstorming**. Describe the topic and constraints (time frame · data and computing resources you can use · scope),
   and what you want to know about each idea (what it needs · risks · expected benefit, etc.). You can also say "brainstorm ○○ with me" in the chat.
2. Look at it in the **Brainstorming** menu.
   - Pick a brainstorm in the list on the left to see its topic, plan and the **idea notes** Claude wrote. If there are several notes, switch with the buttons above the note.
   - When Claude posts a request such as "pick the ideas to work out further", check them right on this page and click **Proceed with selected**.
     The ideas you pick collect under **Chosen ideas**, and Claude works out only those.
   - Send new thoughts or changed constraints with the **Send ideas or comments** box below.
3. Once you know which idea to try, click **New task from these ideas**. The new task (code development · data analysis · paper writing, etc.)
   is linked to this brainstorm and shows up under **Linked tasks**.

## 10. Using DevTrack on an HPC system

On a remote server such as an HPC system (supercomputer), Claude works on that system and the records stay there. There are two ways to see them on the web.
The module names, paths and login addresses below are examples; adjust them for the system you use.

| | Static web ([10-6](#/guide/s10-6)) | Live · SSH tunnel ([10-5](#/guide/s10-5)) |
|---|---|---|
| Where you look | The address of the HPC system's public web folder (e.g. `~/www`) | `localhost` in your own browser (through a tunnel) |
| Approvals · instructions | Paste the message the web creates into the Claude conversation on the HPC system | Saved directly from the web — the waiting Claude starts right away |
| How long it runs | Always (updated every time something is recorded) | Only while you use the web |
| Caution | Anyone who knows the address can see it | While it runs, other users on the same login node can connect too |

We recommend following progress on the static web and turning on the live mode briefly only when you give instructions or approve.
If this is your first time, follow 10-1 → 10-3 → 10-4 → 10-5 in order (10-2 only when you move records from your own computer).

### 10-1. Install DevTrack (once)

```bash
module load python               # Python 3.7 or later (the module name differs by system)
git clone git@github.com:hyungyukang/DevTrack.git ~/devtrack
cat >> ~/.bashrc <<'EOF'
module load python
export PATH="$HOME/devtrack/bin:$PATH"
EOF
source ~/.bashrc
devtrack --version
devtrack config data $HOME/devtrack-data
devtrack config publish $HOME/www/devtrack     # where the web page is published (adjust for your site)
devtrack publish               # once at first (and again after updating the web pages)
```

- `module load python`: needed when the system's default `python3` is older than 3.7. The module name differs by system, so check with `module avail python`
  (if the default `python3` is 3.7 or later, leave out both lines). It is important to put it in `~/.bashrc` as well.
  Claude and the cancellation hook (5-4) must use this Python too ([10-3](#/guide/s10-3)).
- `git clone` needs a GitHub SSH key registered on the HPC system.
- From then on, every record Claude makes is copied to the published directory automatically.
  If your center does not offer a public web folder, leave out `devtrack config publish` and `devtrack publish`, and use the live mode ([10-5](#/guide/s10-5)).
- **After updating DevTrack (`git pull`), run `devtrack publish` again.** Records are copied automatically, but the web page files are copied only by this command.
  Publishing adds a version to each page file's address, so one browser refresh shows the new page even when the web server does not let you set caching.
- The records and publish folders are saved in a settings file (`~/.config/devtrack/config.json`). Programs started without reading your shell settings use the same place.
  Examples are Claude and its hooks in VS Code, tmux, and a `serve` you started earlier. Check with `devtrack config`.
  If you put `export DEVTRACK_DATA` or `DEVTRACK_PUBLISH_DIR` in `~/.bashrc` before, remove those lines.
  An environment variable takes precedence, so a different value splits the records between two places.
- **Keep the records folder in your home directory.** Work areas on parallel file systems such as scratch often delete (purge) files that have not been read or modified for a while, so old task records can disappear.
  Project-shared areas are also open to other members of the same project.
  Home directories are usually not purged and are backed up. Check your center's documentation for its policies.

### 10-2. Move records from your own computer (optional)

To continue on the HPC system with the task records you used locally (on a Mac, etc.), move them in the order below. The records are a single folder, so moving them is simple.

**① Before moving (on your computer)**
- **Clear pending approvals:** if possible, decide pending approval requests first. **Commit approval requests** in particular refer to repository paths on your computer and cannot be applied on the HPC system.
- **Files only on your computer:** finish tasks that depend on such files before moving, or move those files too. Examples: code review comment drafts (`~/.pr-review/`), paper manuscripts in local folders, data.
- **Turn off the local side:** stop `devtrack serve` and waiting mode ("wait for web instructions"). **Recording in two places at once makes the records diverge.** After the move, use only the HPC system.

**② Copy the records (on your computer)** — run this after finishing the 10-1 installation.

```bash
rsync -av --exclude '.agent/' --exclude '.lock' \
  ~/devtrack/data/ <user ID>@<HPC login address>:~/devtrack-data/
```

- Copies the records folder on your computer (see `devtrack config`; default `~/devtrack/data/`) to the records folder on the HPC system.
- `.agent/` holds that computer's conversation and waiting state, so it is not moved. `.lock` is a lock file.
- Whether instructions and decisions were already handled is stored in each task file, so handled instructions do not show up again on the HPC system.
  The list of deleted task numbers (`.deleted.json`) is moved too, so those numbers are not reused.
- Artifact files are copied inside the records folder, so they show up as they are (except artifacts recorded by path only with `--no-copy`).
  The manuscript folders of paper tasks (`tasks/<task ID>/paper/`, [9. Paper writing](#/guide/s9)) are inside the records folder too, so they move along.
  Local repository paths written in finished tasks stay as they are, but that does not affect viewing.
- To continue code reviews, copy the `~/.pr-review/` folder to your home on the HPC system the same way.

**③ Refresh the web page (on the HPC system)** — run `devtrack publish` once.

**④ Claude setup** — [10-3](#/guide/s10-3).

**⑤ Check (on the HPC system)**
- See that the moved tasks appear in `devtrack list` and that `devtrack inbox` runs without errors.
- Open the web address and see that the tasks and artifacts are there.

### 10-3. Install and set up Claude Code (once)

**① Install** — run this on the HPC system. It installs to `~/.local/bin/claude` in your home directory and needs no administrator rights.

```bash
curl -fsSL https://claude.ai/install.sh | bash      # shows no progress while downloading
echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
source ~/.bashrc
claude --version                                     # a version number means success
```

- If you get `claude: command not found`, check with `ls ~/.local/bin/claude` that it is installed; if it is, check the PATH line above.

**② Log in** — the first time you run `claude`, it shows login instructions. HPC systems usually have no browser, so open the address shown on the screen in **the browser on your own computer**, log in, and paste the code it gives you into the terminal on the HPC system.

**③ Turn off automatic updates (recommended)** — your home on an HPC system is usually a network disk shared by all login nodes.
If Claude Code updates itself on one node and deletes the old version files, a Claude running in tmux (10-4) on another node can stop with a `Bus error`.
Put the following in `~/.claude/settings.json` (if the file already exists, add just the one line inside `env`), and update yourself with `claude update` when no Claude is running.

```json
{
  "env": { "DISABLE_AUTOUPDATER": "1" }
}
```

**④ DevTrack setup** — do step 5 the same way on the HPC system, with the points below.

- **Recording rules (5-1):** write the location on the HPC system (`~/devtrack/...`) as the path in `~/.claude/CLAUDE.md`. If you copied the file from your computer, the paths point to your computer, so be sure to change them.
- **Code review skill (5-2):** re-create the link so that it points to `~/devtrack` on the HPC system. Code reviews also need `gh auth login`.
  For the report email feature, set up email once again on the HPC system. Do not copy configuration files that contain passwords.
- **Cancellation hook (5-4):** put the full path on the HPC system in `"command"`. Check it with `echo $HOME/devtrack/bin/devtrack-hook`.
- **⚠️ Run Claude in a shell where Python 3.7 or later is active (where the `module load` from 10-1 has been done).** Before starting Claude, check that `python3 --version` is 3.7 or later.
  If the hook runs with Python older than 3.7, it **does nothing** and lets the call through, so that it never blocks Claude.
  Then cancelling a task on the web does not stop Claude right away.
- If doing this by hand is tedious, you can leave it to Claude on the HPC system — **"Read sections 5-1, 5-4 and 10-3 of `~/devtrack/web/guide.md` and set up DevTrack for this HPC environment"**.
  After the settings change, restart Claude so they take effect.

### 10-4. Keep Claude running inside tmux

tmux is a program that keeps a terminal screen open on a remote computer. Even if your ssh connection drops or you close your laptop, Claude inside it keeps working, and you can connect again and return to the same screen.

**① Start it the first time** (on the HPC system)

```bash
hostname                  # e.g. login05 — remember the login node where you opened tmux
tmux new -s claude        # open a tmux screen named 'claude'
python3 --version         # 3.7 or later? (otherwise module load python)
cd <working directory>
claude
```

When Claude is up, say **"wait for web instructions"** ("웹 지시 기다려줘"). How to give instructions from the web is in [10-5](#/guide/s10-5).

**② Step out** — press and release `Ctrl+b`, then `d`. Claude keeps running in the background, so you can now disconnect ssh.

**③ Go back in**

```bash
ssh <user ID>@<HPC login address>
hostname                  # if this is a different node from before
ssh login05               # move to the node where you opened tmux
tmux attach -t claude
```

A tmux screen exists only on the login node that opened it. If the login address covers several login nodes, each connection may put you on a different node, so move over as above.
`tmux ls` lists the open screens.

| Key (after pressing and releasing `Ctrl+b`) | What it does |
|---|---|
| `d` | Step out (Claude keeps running) |
| `[` | Scroll up (arrows · PgUp, `q` to finish) |
| `c` | New window |
| `n` / `p` | Next / previous window |

**④ Finish** — inside tmux, exit Claude (`/exit`) and type `exit`. To close it from outside, use `tmux kill-session -t claude`.

- If `tmux` is not found, use `screen` — start with `screen -S claude`, step out with `Ctrl+a` then `d`, go back in with `screen -r claude`.
- If the login node restarts (for maintenance, for example), the tmux screen is gone too. Start again from ①.
- **Heavy computation on compute nodes:** Claude works on a login node. Login nodes are shared by many people, so work that uses a lot of computation or memory is not allowed there (the policy at most HPC centers).
  For things like large data processing, say in the instruction that it should run on a compute node. An interactive allocation (`salloc` with Slurm) or batch jobs work for this.
  This uses allocation hours, so if you want to approve before submission, add "get my approval before submitting jobs" to the instruction.

### 10-5. Instruct and approve directly from the web (live mode, SSH tunnel)

If you start `devtrack serve` on the HPC system and open it from your computer through an SSH tunnel, you can give new instructions and save approvals directly on the web, just as you do locally.
The Claude waiting in tmux (10-4) picks them up within a few seconds and works on the HPC system.

> **⚠️ Turn it on only while you use the web, and turn it off right after.**
> A login node is a computer shared by many people, so while the server runs, **other users on the same node** can connect to it too.
> The DevTrack server has no login (authentication) yet, so someone else could view the records or click approve, commit or delete.
> Do not leave the server running inside tmux.

**① Make Claude wait** — as in 10-4, tell Claude inside tmux "wait for web instructions". Once you do, it keeps waiting.

**② Open the tunnel and start the server** — connect from a terminal on your computer,

```bash
ssh -L 8766:localhost:8765 <user ID>@<HPC login address>
```

and start the server **in that same window**.

```bash
devtrack serve            # do not use --open (HPC systems usually have no browser)
```

- The tunnel leads to the login node this ssh connected to, so the server must be started in this window. Other windows may be on other nodes.
- Claude may be in tmux on a different node. Both read the same records folder on the shared disk.
- The `8766` at the front is the port on your computer. If DevTrack also runs on your computer, it already uses 8765, so use a different number.

**③ Open the web** — open `http://localhost:8766/` in the browser on your computer. If the top right shows "Live · local server", the web saves directly.

**④ Instruct · approve** — register an instruction under **New task** in the menu. If the line below the Assigned AI field shows "AI waiting for web instructions: Claude (waiting mode)", Claude on the HPC system starts within a few seconds.
Progress records and approval requests show up on this web too, and clicking approve delivers it to Claude right away.

**⑤ Finish** — press `Ctrl+C` in the server window. If the ssh connection drops, the server stops with it.
The Claude in tmux keeps waiting, so next time start again from ②. While the server is off, look at the static web ([10-6](#/guide/s10-6)) and give instructions and approvals by pasting.

**Good to know**
- **Do not mix up the two DevTracks:** if you also use DevTrack on your computer, `localhost:8765` is your computer's DevTrack and `localhost:8766` is the HPC system's.
  Both may show "Claude (waiting mode)", so tell them apart by the task list. Give instructions on the web of the place where the work should happen.
- **`Address already in use`:** if someone else uses 8765 on the same node, the server does not start. Start it on another port, such as `devtrack serve --port 8790`, and match the tunnel with `-L 8766:localhost:8790`.
  If your computer shows the same message, change the `8766` at the front to another number.
- **If you added `--open`:** you get a long list of messages such as `xdg-open: no method available …`. It only means no browser could be opened; the server is running.

### 10-6. What is different on the static web

- **Approvals and instructions by pasting:** the Approve, Message, New task, Mark as done, Cancel and Delete buttons show a **message for Claude**.
  Copy it and paste it into the Claude conversation on the HPC system (the tmux screen, 10-4).
- **Web instructions cannot wake Claude:** the static web cannot write records, so an instruction sent from the web cannot wake a waiting Claude. To instruct directly from the web, turn on the live mode ([10-5](#/guide/s10-5)).
- **⚠️ Who can see it:** anyone who knows the address can see the contents of the published directory. This includes code diffs, reports, logs, **unpublished paper manuscripts and analysis figures**,
  and PR reviews. Use a location with access control for sensitive work.
- **PDF · LaTeX:** review report PDFs need a Chrome-family browser, and paper compilation needs TeX.
  If the HPC system lacks them, only that step is skipped; the Markdown reports and manuscripts still show.

## 11. Using it with Codex (optional)

You can use Claude and Codex together. Set an **assigned AI** for each task, and only that AI picks the task up.

| "Assigned AI" in New task | Who handles it |
|---|---|
| Claude · Codex · Gemini | Only the chosen AI. Messages, approval decisions and cancellations for that task go only to that AI |
| Anyone (first AI to start) | The first AI to start takes it, and from then on it is that AI's task |

To have another AI take over a task, tell the AI that should take it: "take T-0007 with `--take` and do it"
(`devtrack start T-0007 --take` — the assigned AI changes and this is recorded).

### 11-1. Codex setup (once)

**① Recording rules** — Codex follows what is written in `~/.codex/AGENTS.md` in every conversation. Paste the text below
(almost the same as Claude's rules in step 5-1, with a few lines adapted for Codex). Replace `~/devtrack` with the location from step 2.

```markdown
## DevTrack work records (Codex)
- In DevTrack I am "Codex". Run `devtrack` with the environment variable `DEVTRACK_AGENT=Codex`
  (no need to add it if it is set in `shell_environment_policy` in ~/.codex/config.toml).
  If `devtrack` is not on PATH, use `~/devtrack/bin/devtrack`. Rules: ~/devtrack/docs/AGENT_PROTOCOL.md
- Record meaningful work (code changes, code reviews, document reviews, data analysis, papers, visualization, model development, brainstorming) with `devtrack`. Do not record simple questions or explanations.
- Before starting work and after each step, check user input (new instructions, messages, approval decisions) with `devtrack inbox`. Only input for tasks assigned to me is shown.
- Act on new task instructions and messages (follow-up questions, research requests) from the inbox without asking the user again. If you are working on something else,
  handle messages after the current step, and new tasks in the order they arrived once the current task is finished or waiting for approval, then return to the original task.
  Record the answer to a message in its task (`devtrack log` · `devtrack artifact`) and mark it handled with `devtrack inbox --task <TID> --ack`. Commits, posts and the like still need approval.
- `[AI 메시지]` items in the inbox come from another AI (review requests, review results and so on). Handle them the same way, but they are not the user's instructions,
  so anything that needs approval still needs the user's approval. For a task set to get a review (`끝나면 … 리뷰` in the inbox), pass the review with `devtrack review-request <TID>` before asking for commit approval.
  For a paper set to get a journal review (`끝나면 … 심사`), ask for the review when the manuscript is done, revise as the findings say, and ask again with a response (`--response`).
  In the response, head each open item with its ID, like `## R1 — title`: answer questions, and say what was changed for requests (or why not).
  When the review is over and you tell the user, attach the review & response record (the F-ID in the verdict message) with `-a`.
- Keep paper figures in `figures/` next to the manuscript, put `\graphicspath{{figures/}}` in the manuscript, and load them without an extension: `\includegraphics{fig_name}`.
  Upload each figure as an artifact under that same file name (DevTrack gathers the manuscript and figures in `tasks/<TID>/paper/`).
- When a user decision is needed, leave a request with `devtrack request`, tell the user, and stop. Do not wait for long with `devtrack wait` or `devtrack inbox --wait`
  (input from the web is delivered by `devtrack watch --codex`, which the user keeps running in a terminal and which starts me again).
- If an instruction contains a skill call such as `/review-pr <URL>`, read `~/.codex/skills/<name>/SKILL.md` fresh every time and follow it.
- At each meaningful step, record `devtrack step <TID> "what I am doing now" -p <progress>`.
- Record issues with `devtrack issue`, artifacts with `devtrack artifact`, and review reports with `devtrack review`.
- Write code review reports so that non-developers can follow them: a one-line conclusion, an "at a glance" part for non-developers, a plain-language explanation per item, and a glossary.
- To apply a DevTrack task's result to a repository, do not run git commit/push yourself: request approval with `devtrack request <TID> "..." --commit -m "..."`,
  and once approved, apply it with `devtrack apply <TID> <AID>`. If the user directly asks for a commit or push in the chat, do as asked.
- When the user sends a `[DevTrack 결정]` / `[DevTrack 메시지]` / `[DevTrack 새 작업 지시]` / `[DevTrack 작업 완료]` / `[DevTrack 작업 취소]` / `[DevTrack 작업 삭제]`
  message, first record it with `devtrack decide` / `devtrack comment` / `devtrack new` / `devtrack done --by-user` / `devtrack cancel` /
  `devtrack delete --yes` respectively, then continue.
- If the inbox shows `[완료 처리]` · `[취소]` · `[삭제]`, or a tool call is blocked with a `⛔ DevTrack` message, stop that task immediately, record only the acknowledgement as instructed
  (`devtrack inbox --task <TID> --ack`), and record nothing more for it. Delete a task only when the user asked for it and confirmed.
```

**② Codex configuration file** — add this at the end of `~/.codex/config.toml`. Replace `/Users/me/devtrack/data` with the **full path**
of the records folder (shown by `devtrack config`; default `~/devtrack/data`).

```toml
[shell_environment_policy.set]
DEVTRACK_AGENT = "Codex"                        # Codex records under the name "Codex"

[sandbox_workspace_write]
writable_roots = ["/Users/me/devtrack/data"]    # allow writing to the DevTrack records folder outside the work folder
```

- If the file **already has** a `[shell_environment_policy.set]` or `[sandbox_workspace_write]` line, do not add it again; add only the single lines
  under the existing one. If the same section appears twice, Codex cannot read the configuration file.

- For safety, Codex cannot write outside the work folder. The setting above allows writing to the DevTrack records folder only.
- For network access (`gh` · `git push`), Codex asks for permission in its chat window when needed. `devtrack watch`, which handles web instructions, has no chat window
  to ask in, so it allows network access when it runs Codex (to block it: `devtrack watch --codex --no-network`).

**③ Review skills** (if you will ask for code or document reviews) — link the same files as for Claude.

```bash
mkdir -p ~/.codex/skills/review-pr ~/.codex/skills/review-doc
ln -sf ~/devtrack/integrations/claude-skills/review-pr/SKILL.md ~/.codex/skills/review-pr/SKILL.md
ln -sf ~/devtrack/integrations/claude-skills/review-doc/SKILL.md ~/.codex/skills/review-doc/SKILL.md
```

**④ The `codex` command** — `devtrack watch`, which receives web instructions, uses the Codex CLI. If there is no `codex` in the terminal, it automatically finds
the **newest** of the CLIs bundled with the ChatGPT · Codex app and the VS Code extension (including `~/.vscode-server` on an HPC system opened over Remote SSH; older CLIs cannot use newer models).
To use a different one, `export DEVTRACK_CODEX=<path to codex>`. The CLI in use is shown on the first line when `devtrack watch` starts.

**⑤ Deliver cancellations right away (hook, recommended)** — the same as Claude's 5-4. It lets Codex, working in a conversation in the Codex app or VS Code,
learn about cancellations and deletions mid-task. Create `~/.codex/hooks.json` (if it already exists, just add the entry to its `"PreToolUse"` list,
and replace `/Users/me/devtrack` with the full path of DevTrack).

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

- Codex runs a new hook **only after the user has reviewed it**. In the Codex app, go to **Settings → Hooks** (in the terminal `codex`, `/hooks`),
  review this hook and mark it as trusted. If you change the hook, you need to review it again.
- Codex run by `devtrack watch` stops right away on cancellation even without the hook.

> The settings take effect **from the next new Codex conversation**.

### 11-2. Give tasks to Codex

**Option A — Instruct in the Codex chat.** Give the instruction in the Codex app (or `codex` in a terminal) as usual; Codex creates a task in DevTrack and records it.

**Option B — Instruct from the web (`devtrack watch`).** Claude's waiting mode ("wait for web instructions") is a Claude Code feature,
so for Codex you keep a **runner** going in a terminal instead.

```bash
devtrack watch --codex
```

1. In **New task** on the web, choose **Codex** as the assigned AI and submit.
2. Within a few seconds the runner starts Codex to handle the task. The terminal window shows what is being handled, and when it finishes, Codex's summary is added to the progress log.
3. Once Codex asks for approval and finishes, the runner starts Codex again to continue every time you approve, request changes or send a message on the web.
   The same task continues the same Codex conversation, so Codex remembers what it read and did before.

- Tasks run in their **repository path** folder. Tasks without a path run in the folder where you started `devtrack watch` (change it with `--cwd <folder>`).
- Cancelling or deleting a task on the web stops the running Codex right away.
- Keep this terminal window open (to stop: Ctrl+C). When you start it again, it begins with the instructions that arrived while it was off.
- To pass more options to Codex, put them after `--`: `devtrack watch --codex -- -m <default model>`.
  If a task has a model and effort set, it runs with that model ([6-1](#/guide/s6-1)).

## 12. FAQ · troubleshooting

**I gave an instruction on the web, but nothing happens.**
First look at the notice at the top of the task page (step 6): it tells you whether the assigned AI is waiting, or whether it needs to be woken up and how.
Claude moves only when it is addressed in a conversation. Check that waiting mode ("wait for web instructions") is on and that its VS Code window or terminal is open.
If it runs in a terminal, also check that it is not waiting at a permission prompt ([Option C](#/guide/s6)).
Otherwise, say "check devtrack inbox and go ahead" in the Claude chat. For Codex, check that `devtrack watch --codex` is running.

**Claude (or Codex) does not pick up the task I gave on the web.**
Check whether the task's **assigned AI** is a different AI (shown under the title on the task page). An AI picks up only the tasks assigned to it.
`devtrack inbox --all-agents` shows the input for all AIs, and to take a task over, use `devtrack start <task ID> --take`.

**I cancelled a task (or marked it as done) on the web, but the AI keeps working.**
An AI in a conversation checks DevTrack only at the end of each work step. With the hook installed (Claude 5-4, Codex step 11 ⑤), it stops right before its next command or file
edit. If it keeps going with the hook installed, check ① that it is a **new conversation** started after installing the hook, and ② for Codex, that you **marked the hook as trusted**.
While the AI is writing a long answer without running commands, it cannot be interrupted; if it is urgent, stop it directly in the conversation window.

**Clicking a button opens a dialog with a message to copy.**
This is static mode ("Static · read-only" at the top right). Paste the message into the Claude conversation, or locally, use the address opened by `devtrack serve`.

**I updated DevTrack or a skill, but it behaves as before.**
A Claude conversation that was already open (including one in waiting mode) may still remember the old instructions.
After updating with `git pull`, **open a new** Claude conversation and, if needed, say "wait for web instructions" again.

**`devtrack: command not found`**
Check the PATH setting from step 3, then reopen the terminal and VS Code.

**The `claude` command does not work on the HPC system.**
Claude Code is not installed or not on your PATH. Install it as in [10-3](#/guide/s10-3) ① and add `~/.local/bin` to your PATH.

**`devtrack serve --open` on the HPC system prints a long list of `xdg-open` errors.**
There is no browser on the HPC system, so none could be opened; the server is running. Open the web from your computer through an SSH tunnel ([10-5](#/guide/s10-5)).

**The tunnel address to the HPC system (`localhost:8766`) does not open.**
Check ① that the ssh connection with `-L` is still open and ② that you started `devtrack serve` **in that ssh window**.
A server started in another window may be on a different login node and is not connected to the tunnel ([10-5](#/guide/s10-5)).

**The web page is stuck on "Loading…" or looks wrong.**
Check that the terminal running `devtrack serve` is still open. If you updated DevTrack (`git pull`), stop `devtrack serve` and start it again.
On the static web (such as the HPC system's public web folder), run `devtrack publish` again and refresh the browser ([10-1](#/guide/s10-1)).

**See an example first.**
Create the example in a separate folder so that it does not mix with your real records, and open it.
```bash
devtrack --data ~/devtrack-demo demo
devtrack --data ~/devtrack-demo serve --port 8766 --open
```

**The review report PDF is not created.**
The PDF is made with Chrome (or Chromium · Edge) installed on the computer. Without a browser, only the Markdown report is posted and a warning is left in the activity log.
If the browser is installed somewhere else, set `export DEVTRACK_CHROME=<path to the browser executable>`. For a report already posted, run `devtrack pdf <report.md>` separately.

**Records pile up in two folders (I set `DEVTRACK_DATA`, but `~/devtrack/data/` fills up too).**
An environment variable such as `DEVTRACK_DATA` only reaches programs started from the shell where it was set. In these cases devtrack does not see it and writes to the default folder:
- `devtrack serve`, `watch` or waiting mode started before you set the variable
- Other terminals and tmux
- Claude and its hooks run from VS Code

Set the folder in the settings file with `devtrack config data <records folder>`, and devtrack uses the same place wherever it runs. Then restart any `serve`, `watch` or waiting mode you left running.
`devtrack config` shows where records go now and whether other folders also have tasks. To see the tasks there, run `devtrack --data <that folder> list`.

**Where are the records, and how do I back them up?**
Everything is in the records folder (see `devtrack config`; default `~/devtrack/data/`). Copying this folder is a backup.

**Can Claude commit without my approval?**
For DevTrack tasks, Claude commits only through approved requests (`devtrack apply`). If the actual change differs from the diff you approved, it refuses to run.
However, if you directly say "commit it" in the chat, Claude does as asked.

## 13. Command reference

Commands you use yourself:

| Command | What it does |
|---|---|
| `devtrack serve --open` | Open the web dashboard (local) |
| `devtrack config` | Show the records folder and the publish folder. Set them with `config data <folder>` · `config publish <folder>`, remove a setting with `--unset` (step 3, 10-1) |
| `devtrack publish` | Publish as a static website (e.g. the HPC system's public web folder) |
| `devtrack list` · `devtrack show T-0001` | View the task list · a task's details in the terminal |
| `devtrack paper T-0005` | Rebuild a paper task's manuscript folder (`tasks/T-0005/paper`) and check for missing figures (9. Paper writing) |
| `devtrack watch --claude` | Handle instructions sent to Claude on the web, with each task's model (6-1) |
| `devtrack watch --codex` | Handle instructions sent to Codex on the web with Codex (step 11) |
| `devtrack model T-0001 opus-5.5 --effort xhigh` | Set the AI model and effort for a task (`--clear` removes it, `--list` shows the models and levels, 6-1) |
| `devtrack review-request T-0001 --reviewer Codex` | Have another AI review the code changed so far (build outputs left out). The result comes as an [AI 메시지] (6-2) |
| `devtrack review-done T-0012 --verdict changes -m "…"` | (The reviewing AI) send this round's result: `approve` or `changes`; for a journal review `accept` · `minor` · `major` · `reject` (6-3) |
| `devtrack review-request T-0005 --response response.md` | (Paper) ask for another journal review of the revised manuscript with the response; the first time, `--doc <manuscript> --venue <journal>` (6-3) |
| `devtrack send T-0012 "…"` | Send a message to a task another AI has ([AI 메시지]) |
| `devtrack --data <folder> demo` | Create example data |

Commands Claude uses (for reference): `new` create a task · `step` what it is doing now · `issue` issue · `artifact` artifact · `review` review report · `text` text from a Word/PDF file ·
`link` link tasks · `latex` compile LaTeX (records the PDF as a build) ·
`request` approval request · `apply` run an approved commit · `inbox` check user input (only for tasks assigned to it) ·
`start --take` take over another AI's task · `done` done · `hook` deliver cancellations and deletions to an AI at work (5-4).
For full details, see `devtrack --help` and the [agent rules](https://github.com/hyungyukang/DevTrack/blob/main/docs/AGENT_PROTOCOL.md).
