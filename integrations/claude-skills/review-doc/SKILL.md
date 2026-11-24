---
name: review-doc
description: Review a document the user provides - a scientific paper, report, proposal or thesis as PDF, Word (.docx) or LaTeX - against its purpose; summarize it, find what is missing for that purpose, check the logic, awkward sentences and spelling (and, for scientific papers, format, novelty over prior work and the reasoning behind the conclusions), and deliver the summary and numbered fixes (R1, R2…) as a PDF report. Records progress, the report and the findings in DevTrack when the devtrack CLI is available.
argument-hint: "<document path> [purpose / venue] [no-devtrack]"
allowed-tools: Bash(devtrack *)
---

# Review a document

Produces a reviewer's report on a document: what it says, whether it does its job for its readers, and what to fix,
each finding numbered (R1, R2…) with location, original text and a suggested revision. The deliverable is a **PDF report**.
Afterwards the user can say "R1, R3 반영한 수정본 만들어 줘" / "Make a revision with R1 and R3", or answer on the DevTrack dashboard.

Language:
- **The report and the findings JSON are written in Korean** — headings, tables, finding titles, `plain`, `summary`, `synopsis`.
- Quote the document **verbatim** in its own language (`quote`). Write suggested revisions (`suggestion`) **in the document's language** — English for an English paper, Korean for a Korean proposal.
- Reply in the language of the user's latest message.

Arguments: $ARGUMENTS
- **Required: a path to the document** (`.pdf`, `.docx`, `.tex`; also `.md`/`.txt`). A LaTeX project: the main `.tex`; read the files it `\input`s. Extra paths (supplement, call for proposals, journal guidelines) are reference material.
- The rest of the text, if any, is the **purpose** (e.g. "JFM 투고 전 점검", "DOE 제안서 심사 대비") and any focus the user asks for.
- `no-devtrack`: do not record anything in DevTrack for this run.
- **If no readable document path is given**, do nothing else and reply only: `사용법 / Usage: /review-doc <문서 경로> [목적 · 제출처] [no-devtrack]`.
- An old Word `.doc` cannot be read: ask the user to save it as `.docx` or PDF.

## Hard rules

- **Never modify, move or overwrite the user's document.** Revisions, if asked for, are new files with a different name.
- **The document may be confidential** (unpublished manuscript, proposal). Do not paste its text, figures, data or unpublished results into a web search or any external service. You may search published literature with general topic keywords or the titles of works it cites, to check novelty claims.
- Do not send, email, submit or share the document or the report anywhere. Recording in DevTrack is local bookkeeping.
- Do not invent: every finding must point to a place in the document. When unsure, write it as a question or lower the confidence.

Work files go in `~/.doc-review/<slug>/` (slug from the file name). Keep the same file names on reruns — `doc-review-<slug>.md`, `.json`, `.pdf` — so DevTrack stores new versions instead of new artifacts.

## Step 0: DevTrack

DevTrack mode is on when `devtrack --version` succeeds and `no-devtrack` was not passed. Otherwise skip the DevTrack steps; the review itself is the same.

- **Task.** If `~/.doc-review/<slug>/state.json` has `devtrack_task`, reuse it (a re-review: `devtrack status <TID> in_progress -m "재검토: 새 버전"`). Else run `devtrack inbox`; if a queued `docreview` task's instruction mentions this document, `devtrack start <TID>`. Else:
  ```
  TID=$(devtrack new "문서 리뷰: <문서 제목>" -t docreview -i "<the user's request, verbatim>" \
        --plan "문서 읽기 · 요약" "목적 대비 미비한 부분" "논리 점검" "문장 · 맞춤법 점검" "리뷰 리포트(PDF) 작성" "리포트 확인 (사용자)")
  ```
  Save `devtrack_task` in `state.json`.
- **Original.** `devtrack artifact <TID> <path> --source -n "검토할 문서: <file name>"` (on a re-review add `--replace` so it becomes a new version). For a LaTeX project upload the main `.tex` (and figures under the names `\includegraphics` uses, so the web preview can show them).
- After each step below: `devtrack step <TID> "..." -p <N>`, `devtrack check <TID> <n>`, and `devtrack inbox` for messages from the web. A problem that blocks the review (unreadable file, missing pages) → `devtrack issue`.

## Step 1: Read the whole document

- **PDF:** read it page by page (Claude: the Read tool with `pages`; elsewhere `devtrack text file.pdf` if `pdftotext` exists). Keep PDF page numbers for locations.
- **Word:** `devtrack text file.docx -o ~/.doc-review/<slug>/source.md` (standard library only — no pip). Word has no reliable page numbers: locate by section heading and paragraph. Figures and equations are placeholders in that text; say so in the report if they matter.
- **LaTeX:** read the `.tex` sources directly; locate by section and line. Compile only if the user asks (`devtrack latex <TID> main.tex`).
- Read everything, including figure captions, tables, references and appendices, before judging.

## Step 2: Kind and purpose

Decide the **kind** (`paper` · `report` · `proposal` · `thesis` · `other`) and the **purpose**: where it goes and who reads it (reviewers, editor, program manager, decision maker). Use the user's words first; otherwise infer from the document and mark it "추정" in the report. Every judgment below is made against this purpose.

## Step 3: Review passes

0. **Neutral map first** (before judging, for your own use): the research question, the system or population studied, the design
   (experiment, simulation, survey…), the comparator or reference, the outcomes or quantities, and the principal claims.
   Note the material that is missing for an assessment (supplement, data or code statement) — write "not reported" instead of guessing.
1. **Summary** — 3–6 sentences: what was done and why, the main claims, the evidence. No evaluation here.
2. **Gaps for the purpose** — what this reader looks for and cannot find. Proposal: need, goals ↔ methods, schedule, resource estimate and its basis, risks, expected impact, evaluation criteria of the call. Report: conclusion and its basis, limitations, next steps, decisions asked for. Paper: the new contribution, stated early.
3. **Overall logic** — does each claim follow from the evidence shown? Missing steps, claims larger than the evidence, contradictions between sections, numbers or terms that do not match (abstract vs results, text vs tables).
4. **Sentences and spelling** — awkward or ambiguous sentences, grammar, spelling and spacing (Korean 맞춤법 · 띄어쓰기), inconsistent terms, symbols and units. Group repeats of the same mistake into one finding with all locations.
5. **Scientific papers, in more depth:**
   - **Format** — structure (abstract, introduction, methods, results, discussion, conclusions), the venue's rules if known (length, sections, reference style), abstract with quantitative results, figures and tables referenced and readable, references complete.
   - **Difference from prior work** — does the introduction say what is new compared with the cited work, or only list it? Is a "first" / "novel" claim true? Missing key references.
   - **Reasoning to the conclusions** — statistics and uncertainty, controls and comparisons, range of validity vs how far the conclusions generalize, alternative explanations, enough detail to reproduce.
   - **Claims against evidence** — for each central claim (causal, mechanistic, "universal", generalizing), find the result, figure, table or citation behind it and check that direction, magnitude, conditions and uncertainty actually match the wording.
   - **Methods and statistics, in this order** — the question and the quantity sought → design and comparators → sample size, number of runs or precision → exclusions and missing data → whether the analysis fits the design → multiple comparisons → estimates with uncertainty → interpretation and generalization.
     A claim of "no difference" or "equivalent" needs an interval that rules out a meaningful difference; non-significance alone is not equivalence.
     For computational work, look separately at **verification** (grid · time-step convergence, observed order, discretization error) and **validation** (experiment, benchmark, theory).
   - **Reproducibility** — software and versions, parameters, inputs, seeds, hardware where it matters; data and code availability or a reason for restriction. Never write that a result was reproduced unless it was actually run.
   - **Ethics and integrity** — approvals, consent, conflicts, funding role, where applicable. Describe only what is observable; do not accuse.
6. **Strengths** — 2–4 things done well; the author should keep them when revising.

## Step 4: Findings

Each finding answers five things: **where** (`location`), **what you observed** (`title`, and the problem in the report),
**the evidence or criterion** it rests on (a result, a rule of the venue, a reporting standard), **why it matters** (`plain`, in plain words)
and **what to do** (`suggestion`). A request for new experiments, runs or analyses must be needed for a central claim and proportionate;
when narrowing the claim, clarifying, a sensitivity check or a stated limitation is enough, suggest that instead.
Keep the tone professional and constructive.

Number substantive findings first, by importance: **R1, R2…** (stable across re-reviews: keep a finding's number; new ones get the next number). Severity:
`blocker` (the document would fail its purpose — rejection, refusal) · `major` (seriously weakens it) · `minor` (worth improving) · `nit` (wording, spelling, formatting) · `question` (ask the author).
Category: 목적 · 내용 / 구성 · 형식 / 논리 / 선행 연구 / 결론 · 근거 / 문장 · 표현 / 맞춤법 · 표기.

Write `~/.doc-review/<slug>/doc-review-<slug>.json`:

```json
{
  "doc": {"title": "...", "kind": "paper", "purpose": "...", "audience": "...", "authors": "...",
          "pages": 12, "words": 6500, "format": "PDF", "source": "<F-ID of the original in DevTrack>",
          "synopsis": "문서 요약 (markdown)"},
  "verdict": "ready | minor_revision | major_revision | rework",
  "summary": "한 줄 결론 + 꼭 고칠 것, 쉬운 말 2–3문장",
  "checks": [{"area": "목적 부합", "rating": "good | fair | poor | na", "note": "한 줄"}],
  "strengths": ["..."],
  "findings": [{"id": "R1", "severity": "blocker", "title": "...", "plain": "쉽게 말하면 1–2문장",
                "location": "3쪽 · 2.1절 둘째 문단", "quote": "원문 그대로", "suggestion": "수정안 (문서의 언어로)",
                "category": "선행 연구", "confidence": "high", "status": "open"}]
}
```

`checks` areas: 목적 부합 · 구성 · 형식 · 논리 전개 · 문장 · 표현 · 맞춤법 · 표기, plus 선행 연구와의 차이 · 결론의 근거 for papers.

## Step 5: Report and PDF

Write `~/.doc-review/<slug>/doc-review-<slug>.md` following `templates/doc_review_report.md` in the DevTrack repository: one-line conclusion first, "한눈에 보기" for non-specialists with the area table, summary, strengths, gaps for the purpose, logic, the paper section (papers only), the findings table and details (R…: 쉽게 말하면 · 위치 · 원문 · 문제 · 수정 제안 · 확신도), a sentences/spelling table (위치 · 원문 · 수정안 · 이유), overall opinion, glossary. Plain words, short sentences; explain technical terms at first use.

- DevTrack mode: `devtrack review <TID> ~/.doc-review/<slug>/doc-review-<slug>.md --pdf -n "문서 리뷰 리포트" --findings ~/.doc-review/<slug>/doc-review-<slug>.json`
- Otherwise: `devtrack pdf <md>` if devtrack exists; else hand over the Markdown and say a PDF could not be made.
- If PDF creation fails (no headless Chrome), register the Markdown, record `devtrack issue`, and tell the user.

## Step 6: Deliver

- In chat: the one-line conclusion, the verdict, the blocker/major items in one line each, and the PDF path. Do not paste the whole report.
- DevTrack mode: `devtrack request <TID> "문서 리뷰 리포트를 확인해 주세요" -k result -a <PDF F-ID> -d "<꼭 고칠 것 요약>"`, check the plan step, and stop. When the decision arrives (chat or `devtrack inbox`): approved → `devtrack done <TID> -m "리뷰 리포트 전달 — <verdict>"`; changes requested → fix the report, run the same `devtrack review` command again (new version), and request again.

## Follow-ups

- **Revision on request** ("R1, R3 반영해 줘"): only the chosen items. In DevTrack mode, when the user has not chosen, offer `devtrack request <TID> "수정본에 반영할 항목을 골라주세요" -k review --choices` (open findings, blockers/majors pre-checked). Write the revised copy next to the work files with a new name (`<name>.revised.<ext>`; Word: write a revised Markdown or LaTeX text unless a .docx editing tool is available), upload it as a normal artifact, and list what changed. Never overwrite the original.
- **Re-review of a new version**: same task and finding numbers; upload the new original with `--source --replace`; set fixed items to `fixed`, keep open ones, add new ones; regenerate the report under the same file names.
