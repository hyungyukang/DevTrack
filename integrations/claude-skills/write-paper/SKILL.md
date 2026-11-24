---
name: write-paper
description: Draft, revise and check a scientific paper - LaTeX first, also Markdown or Word text - inside a DevTrack paper task without inventing anything. Gathers the evidence from the task and its linked analysis tasks into an evidence ledger, outlines from it, drafts section by section, reconciles every number and method with its source, checks citations against the user's own .bib, builds the PDF with devtrack latex, and answers referees with a response letter for DevTrack's journal review loop. Use when the user asks to write, draft, revise or polish a paper, a section, an abstract or a response to reviewers - "논문 써줘", "초안", "4장 작성", "수정본", "답변서". Adapted from K-Dense scientific-writing (MIT) with every network and external-service step removed.
argument-hint: "<T-xxxx | manuscript path> [section or request, e.g. 4장 결과 · 초록 · 심사 답변서]"
allowed-tools: Bash(devtrack *)
---

# Write a paper

Write clear scientific prose without inventing evidence or hiding uncertainty. Drafting, checking the evidence and the
user's approval are separate stages. The user (the authors) decide the science and approve the result; the AI is not
an author, and fluent text is never evidence.

Language:
- **The manuscript stays in its own language** (usually English).
- **Notes to the user are in Korean**: evidence ledger, change summary, DevTrack logs and requests.
- Reply in the language of the user's latest message.

Arguments: $ARGUMENTS
- **A DevTrack paper task** (`T-0005`) or a manuscript path (main `.tex`, `.md`, `.docx`).
- **The rest is the request**, e.g. "4장 결과", "초록 다시", "Fig 4.2 설명 보강", "심사 답변서".
- If there is neither a task nor a path and nothing in `devtrack inbox` to work from, ask what to write.

## Hard rules

### Confidentiality (local only)

- Unpublished manuscripts, data, referee reports and editor letters stay on this machine.
- No external services: no web search or online lookup for the manuscript, no DOI or citation resolvers, no grammar, plagiarism or AI-detection services, no translation services, no image generation.
- Use only what the user provided: the task, its linked tasks and their artifacts, files in the repository or the manuscript folder, the user's `.bib`, and papers the user gave.

### No fabrication

Never invent or complete any of these:
- **Citations and sources**: citations, references, DOIs, page numbers, URLs, quotations.
- **Results and statistics**: data values, sample sizes, units, error bars, uncertainties, statistical tests, significance.
- **Methods**: methods, parameters, grid or mesh sizes, time steps, software versions, hardware, run settings.
- **People and statements**: authors, author order, affiliations, CRediT roles, acknowledgments, funding, conflicts of interest, ethics statements, data or code availability, AI-use disclosure.

Write an explicit placeholder instead, never plausible boilerplate:
- `[[MISSING: …]]` — the information does not exist yet.
- `[[CHECK: …]]` — two sources disagree, or something is unverified.
- `[[CITE: what needs a source]]` — a statement needs a reference that is not in the user's `.bib`.

LaTeX prints these visibly, and Step 6 lists every one left.

### Evidence binding

- Every factual or numeric statement in the manuscript maps to an entry in the **evidence ledger** (Step 2).
- In LaTeX, end such a line with a comment, `% E3` or `% E3,E7`. It is invisible in the PDF and lets anyone trace the number.
- Memory, plausible-looking values and another paper's bibliography do not count as evidence.

### Scientific fidelity

- Keep uncertainty and alternative explanations; do not drop error bars or caveats to make prose smoother.
- Keep methods and results consistent. Reconcile units, precision, sample or run counts, conditions and labels between the abstract, the text, figures, tables and captions.
- **Computational work**: state numerical verification (grid or time-step convergence, observed order, discretization error) apart from validation (comparison with experiment, benchmarks or theory). Give parameter ranges, and do not generalize beyond them.
- Report null, negative or unexpected results that belong to the study.
- Never turn association into causation, non-significance into equivalence, or one case into "universal".
- Avoid "novel", "first", "unprecedented" unless the user's sources support the claim; otherwise write `[[CHECK: novelty claim]]`.

### Human accountability

The user decides scientific ambiguities, author order, declarations, and when the paper is ready to submit. Never mark a manuscript "submission-ready" yourself.

Work files go in `~/.paper-work/<TID or slug>/`:
- `evidence.md` (the evidence ledger)
- `outline.md`
- `changes.md`
- response letters `response_round<n>.md`

## Step 0: DevTrack

DevTrack mode is on when `devtrack --version` succeeds.

- **Task.** Use the paper task given, or the one the request came from (`devtrack inbox`). For a new paper:
  ```
  TID=$(devtrack new "논문: <제목>" -t paper -i "<the user's request, verbatim>" \
        --plan "자료 · 근거 정리" "개요 (근거 기반)" "초안" "숫자 · 방법-결과 대조" "인용 점검" "빌드 · 점검" "원고 확인 (사용자)")
  ```
  When the user names the sections ("2장 방법, 3장 결과…"), use those as the plan instead.
- **Context.**
  - `devtrack show <TID>` gives the instruction, plan, artifacts and messages.
  - For each linked task (`related`), read `devtrack show <R> --brief` first: status, key artifacts and the handoff note. Open reports and files only as needed.
  - Run `devtrack paper <TID>` to see the manuscript folder (`tasks/<TID>/paper/`) and any missing figures.
- **Progress.** After each step: `devtrack step <TID> "..." -p <N>`, `devtrack check <TID> <n>`, then `devtrack inbox` for messages from the web.
  - Problems go to `devtrack issue`. Examples: a number in two sources that disagree, or a figure with no data.
  - Keep the handoff note current: `devtrack note <TID> -`.
- **No DevTrack** (no `devtrack`): do the same steps on the files, and report in chat.

## Step 1: Intake

Find out, or mark as unresolved:
- **The paper**: document type (article, letter, proceedings, thesis chapter), stage (first draft, revision, response), target venue, audience, and the scope of this request.
- **Venue rules**: the author instructions *only if the user has given them* (a file, or the venue's LaTeX class). Do not look them up online.
- **Sources available**: data, logs, analysis reports, figures, the `.bib`, and the papers the user gave.
- **Declarations**: whatever the user has stated (funding, availability, contributions).

## Step 2: Evidence ledger

Write `evidence.md`, one row per fact the manuscript will state:

| ID | 내용 (값 · 단위 · 조건) | 출처 (작업 · F-ID · 파일:줄 · 그림) | 상태 | 쓰는 곳 |
|---|---|---|---|---|
| E1 | 8-GCD speedup 112.3× (mean of 5 runs, ±1.2 %) | T-0006 F-3 perf.csv:12 | 확인 | §4.2, Table 4.1, abstract |

- **상태**:
  - `확인` — you opened the source and the value matches;
  - `미확인` — mentioned, but the source is not available;
  - `없음` — needed but missing, which becomes `[[MISSING]]` in the text.
- **Sources** are DevTrack artifacts (data, logs, notebooks, figures, reports), handoff notes, the user's own words in the instruction or messages, and the user's papers and `.bib`. Record exactly where each value is.
- In DevTrack mode, upload the ledger as `devtrack artifact <TID> evidence.md --replace -n "근거 장부"`.

## Step 3: Outline from the evidence

For each section, write:
- its purpose;
- the claims it makes, with their E-IDs;
- the figures and tables it uses;
- what is still missing.

Keep anything unsupported in the outline's "열린 문제" list, not in prose.

For a new paper or a whole chapter, upload the outline (`devtrack artifact <TID> outline.md --replace -n "개요"`) and ask for a check before drafting: `devtrack request <TID> "개요를 확인해 주세요" -k result -a <F-ID>`. Stop until the decision comes. For an edit of one section, go on.

## Step 4: Draft without adding facts

**Structure**
- Fit the venue and the content. IMRaD where it applies; letters and proceedings are shorter.
- The title and abstract must match the finished body. Put numbers in the abstract only when they are in the ledger.

**Methods and results**
- Describe methods as performed, with enough detail to reproduce: equations, discretization, parameters, grids, software and hardware, run counts.
- Present results in the order of the questions. Keep the result apart from its interpretation.
- Compare with prior work only through references in the user's `.bib` or papers the user gave.
- Keep conclusions within the cases, parameter ranges and uncertainty actually studied.

**Prose**
- Use one idea per paragraph, with its topic sentence first.
- Keep terms, symbols, units and abbreviations consistent, and define each at first use.
- Write methods and results in the past tense; established facts take the present tense.
- Prefer the active voice where it is natural.
- Avoid vague intensifiers.

**LaTeX** (DevTrack conventions)
- Keep the venue's class and preamble. Add packages only when needed, and say so.
- Use labels `sec:` `fig:` `tab:` `eq:` and refer with `\ref`/`\eqref`.
- Put figures in `figures/` with `\graphicspath{{figures/}}` and `\includegraphics{name}` *without* the extension. Upload the figure files under those names, so DevTrack's manuscript folder (`tasks/<TID>/paper/`) can assemble and compile them.
- Every figure and table needs a caption that stands alone, with what is shown, units, conditions and error-bar meaning.

## Step 5: Reconcile numbers, methods and results

- **Numbers.** List every number in the abstract, results, conclusions, figures, tables and captions. Check each one:
  - against the ledger;
  - against the other places the same number appears (abstract ↔ text ↔ table ↔ caption);
  - for units, rounding and significant digits.
- **Methods and results.** Every result has a method that produced it, and every described method leads to a result. Otherwise cut it or explain it.
- **Mismatches.** Fix a mismatch only when the ledger shows which side is right. Otherwise mark it `[[CHECK: …]]` and record a `devtrack issue`.
  - A legitimate difference must be named in the text, never silently normalized. Example: a different run set or condition.

## Step 6: Citations, build and lint

**Citations**
- Every `\cite` key must exist in the `.bib`.
- List `.bib` entries never cited, and leave them in.
- Do not add references from memory. A statement that needs one gets `[[CITE: …]]`.
- Follow the venue's citation style through its class or `.bst`. Do not hand-edit reference formatting.

**Build**
- Run `devtrack latex <TID> <main.tex>`, adding `--engine xe` or `--engine lua` if the class requires it. It records the PDF and the warnings.
- Fix undefined references and citations, and overfull boxes that show in the PDF.
- If no LaTeX is installed, say so and skip the build.

**Lint.** List what is left:
```
grep -n -E "\[\[(MISSING|CHECK|CITE)|TODO|TBD|\?\?" <manuscript files>
```
Never clear a placeholder by inventing content.

## Step 7: Declarations

These sections come only from what the user stated. Otherwise leave `[[MISSING: … — 저자가 정함]]`:
- data and code availability;
- funding;
- conflicts of interest;
- author contributions (CRediT);
- acknowledgments;
- ethics;
- AI-use disclosure, if the venue requires one.

## Step 8: Deliver

1. **Upload** (DevTrack mode). Upload the changed `.tex` and `.bib` files and any new figures with `--replace` (new versions, same names); the manuscript folder rebuilds itself. Upload the ledger too.
2. **Change summary.** Write `changes.md` (Korean): what changed where, the placeholders left with their count and locations, and the open questions for the user. Record it with `devtrack log`.
3. **Ask for a check.** `devtrack request <TID> "원고를 확인해 주세요" -k result -a <PDF F-ID or .tex F-ID> -d "<바뀐 것 · 남은 [[MISSING]] n개 · 결정할 것>"`, then stop.
   - When the decision arrives: approved → continue with the plan, or `devtrack done`; changes requested → revise and request again.
4. **Journal review loop.** If the task is set to get a journal review (`devtrack show` lists 심사), request it once the manuscript is complete: `devtrack review-request <TID>`, following DevTrack's paper rules.
5. **In chat**: a few lines on what changed, how many placeholders are left, and the decisions the user needs to make. Do not paste the manuscript.

## Revision and response to referees

1. **Read the referee report.** For DevTrack's journal review loop, read the review task's findings: `devtrack show <review task>` lists R1, R2… and the report.
2. **Classify each finding** as editorial, scientific, numerical or statistical, request for new work, question, or not addressed (with reason).
3. **Ledger first.** When facts change, update the ledger before the prose. Then revise, and re-run Steps 5–6 for what changed.
4. **Response letter.** Write `response_round<n>.md` with one heading per finding ID: `## R1 — <short title>`. Under each heading:
   - for a request, what changed and where (section, figure, equation);
   - for a question, the answer;
   - for anything not done, the reason, with evidence.
   DevTrack refuses a response that skips an open finding.
5. **No fabrication.** Never satisfy a request by inventing data, runs or citations. If new work is needed, say so in the letter, and raise it with the user through `devtrack request`.
6. **Request the next round**: `devtrack review-request <TID> --response ~/.paper-work/<TID>/response_round<n>.md`.

## Credits

The principles are adapted from K-Dense *scientific-writing*:
- confidentiality;
- no fabrication;
- evidence binding;
- scientific fidelity;
- the workflow from intake to response.

The source is <https://github.com/K-Dense-AI/scientific-agent-skills>, `skills/scientific-writing`, MIT License, © 2025 K-Dense Inc.; see `LICENSE-k-dense.txt` next to this file. This adaptation removes:
- all network lookups and external services;
- the original's request to cite its authors' paper in the user's work;
- the clinical reporting manifests and scripts.
