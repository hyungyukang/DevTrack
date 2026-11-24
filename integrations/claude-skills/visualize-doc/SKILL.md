---
name: visualize-doc
description: Visualize a long paper or document - PDF, Word (.docx), LaTeX or Markdown - as a structured summary plus 1-3 diagrams (모식도 · 도식) that Claude draws itself as clean SVG. Picks a layout pattern that fits the content (pipeline, cycle, stack, dual stream, hub, matrix), lays out 2-5 zones with the document's own terms and numbers, checks the drawing, and delivers a visual summary report (Markdown + PDF). Use when the user asks to visualize, diagram or summarize a paper or document visually - "시각화해줘", "도식화", "모식도", "그림으로 정리" - including on DevTrack paper-writing and document-review tasks. Records the result in DevTrack when the devtrack CLI is available. Adapted from WilsonWukz/paper-visualizer-skill (visual-architect, MIT).
argument-hint: "<document path | T-xxxx> [focus, e.g. 방법만 · 3장] [prompt] [no-devtrack]"
allowed-tools: Bash(devtrack *)
---

# Visualize a document

Turns a long document into something a reader can take in quickly:
- a **structured summary** (짜임새 있는 요약)
- **1–3 diagrams** (모식도 · 도식) drawn as SVG.

The method comes from WilsonWukz's *visual-architect* skill:
- recognize the layout pattern;
- divide the canvas into 2–5 zones;
- fill the zones with concrete objects;
- spell out every connection.

The output differs. The original writes a prompt for an image-generation model. This skill draws the diagram itself, which has three advantages:
- the text in the diagram is exact;
- the document never leaves the machine;
- the result shows up directly in DevTrack.

Language:
- **The report is written in Korean**: headings, summary, captions, "읽는 법", glossary.
- **Labels inside diagrams use the document's own terms in the document's language.** An English paper gets English labels.
  - If the user asks for Korean labels, translate them and keep the key original term in parentheses.
- Quote numbers exactly as written, with their units.
- Reply in the language of the user's latest message.

Arguments: $ARGUMENTS
- **A document path** (`.pdf`, `.docx`, `.tex`, `.md`/`.txt`) **or a DevTrack task ID** (`T-0012`).
  - For a LaTeX project, give the main `.tex`, then read the files it `\input`s.
- **Any other text is the focus**, e.g. "방법 부분만", "3장", "실험 설계", "결과 비교".
  - Without a focus, cover the whole document.
- `prompt`: also write the original skill's image-generation prompt as an appendix. See Step 6.
- `no-devtrack`: record nothing in DevTrack.
- If there is no path, no task ID and no DevTrack request to work from, do nothing else and reply only:
  `사용법 / Usage: /visualize-doc <문서 경로 | 작업 ID> [초점] [prompt] [no-devtrack]`.

## Hard rules

- **Never modify, move or overwrite the document.**
- **The document may be confidential** (unpublished manuscript, proposal). Do not send its text, figures or numbers to any external service.
  - This includes **image-generation services** (DALL·E, Midjourney, Stable Diffusion, Gemini / Nano Banana and the like).
  - The diagrams are drawn locally as SVG.
  - The optional `prompt` appendix is for the user to use themselves, if they choose.
- **Do not invent.** Every box, label, arrow and number must come from the document, and each diagram lists its source locations.
  - If something the layout seems to need is not in the document, leave it out, or mark it `(문서에 없음)` in the report — never in the diagram.
- **Use plain shapes only**, and no external images or fonts. DevTrack refuses an SVG with `<script>`, `<foreignObject>`, event attributes or external URLs.

Work files go in `~/.doc-visual/<slug>/`, where the slug comes from the file name or the task ID. Keep the same file names on reruns so DevTrack stores new versions instead of new artifacts:
- `visual-<slug>.md` and `visual-<slug>.pdf`
- `v1-overview.svg`, `v2-structure.svg`, `v3-results.svg`

## Step 0: DevTrack

DevTrack mode is on when `devtrack --version` succeeds and `no-devtrack` was not passed. Otherwise skip the DevTrack steps; the visualization itself is the same.

- **Which task.**
  - **An existing task** — a task ID was given, or the request came in through `devtrack inbox` (a `[메시지]` on a paper or document-review task, or a new task whose instruction asks for this). Work in **that task**; do not create a new one.
    - `devtrack doc <TID>` prints the document to read. Use `--all` to see the candidates: the manuscript under journal review, the paper folder's main `.tex`, the original of a document review, and document artifacts.
    - For a paper task with no document yet, run `devtrack paper <TID>` first, or ask for the path.
    - When the request came through the inbox, the document may also be named in the message itself.
  - **A document path only.**
    - If `~/.doc-visual/<slug>/state.json` has `devtrack_task`, reuse that task.
    - Else, if `devtrack inbox` shows a queued task about this document, `devtrack start <TID>`.
    - Else create a task:
      ```
      TID=$(devtrack new "문서 시각화: <문서 제목>" -t visualization -i "<the user's request, verbatim>" \
            --plan "문서 읽기 · 구조 파악" "짜임새 있는 요약" "도식 설계" "SVG 그리기 · 점검" "시각화 요약(PDF) 작성" "결과 확인 (사용자)")
      ```
      Save `devtrack_task` in `state.json`.
- **Progress.**
  - On a task this skill created: `devtrack step <TID> "..." -p <N>` and `devtrack check <TID> <n>` after each step.
  - On an existing paper or document-review task: record progress with `devtrack step <TID> "시각화: ..."` **without `-p`**, so the paper's own progress number is kept, and do not change its plan or status.
  - A problem that blocks the work (unreadable file, missing pages) → `devtrack issue <TID> "..."`.

## Step 1: Read the whole document

- **PDF:** read it page by page (Claude: the Read tool with `pages`; elsewhere `devtrack text file.pdf`). Keep page numbers for source locations.
- **Word:** `devtrack text file.docx -o ~/.doc-visual/<slug>/source.md`. Locate things by section heading.
- **LaTeX:** read the `.tex` sources, including the files they `\input`. Locate things by section, equation and figure number.
- For a long document, build a **section map** as you read: each heading → a two-line gist, the key numbers, and where they are.
  - Read figure captions and tables: they often hold the main result.

## Step 2: Structured summary

Write the summary before drawing. The diagrams must agree with it.
- **한 줄 요약** — what was done and the main result, in one sentence.
- **문서 지도** — a table with columns 절 · 역할 · 한 줄 요지.
  - 역할 is one of: 배경 · 문제 · 방법 · 결과 · 논의 · 결론, or for a proposal: 필요성 · 목표 · 방법 · 일정 · 예산 · 기대효과.
- **핵심 주장 3–5개**, each with the evidence and its location.
- **방법 흐름** — numbered steps, in the order the document runs them.
- **주요 결과** — the numbers exactly as written, with units, conditions and location.
- **한계 · 열린 질문** — what the document itself says. Your own observations are labeled `(정리자 의견)`.
- Adapt the outline to the document kind:
  - report: 목적 → 현황 → 결론 → 근거 → 다음 단계 · 결정할 것
  - proposal: 필요성 → 목표 → 방법 → 일정 · 자원 → 위험 → 기대효과

## Step 3: Choose the diagrams

Draw 2 diagrams by default and 3 at most. A focus argument narrows this, e.g. one detailed diagram of the method.
- **V1 핵심 모식도** (always): how the method, system or argument works.
- **V2 문서 구조 · 논리 흐름**: for documents of more than about ten pages, or when the argument has several strands. It shows the sections as stages and the links claim → evidence → conclusion.
- **V3 결과 비교**: when the document compares cases, methods, ablations or parameters with numbers.

For each diagram, pick **one** layout pattern:

| 패턴 | 언제 | 배치 |
|---|---|---|
| Linear Pipeline | data processing, encode → decode, workflows, solver steps | left → right |
| Cyclic / Iterative | optimization, feedback, iterative solvers, review cycles | loop around a center |
| Hierarchical Stack | multiscale, software or hardware stacks, trees | top → bottom layers |
| Parallel Dual-Stream | two inputs or cases that merge (simulation vs experiment, two modalities) | two rows joining |
| Central Hub | one core with peripherals (platform, agent ↔ environment, central concept) | center + spokes |
| Matrix Grid | comparisons, ablations, parameter sweeps | rows × columns |

Write a **visual schema** first, in `~/.doc-visual/<slug>/schema-v<n>.md`. It is short and is not part of the report unless `prompt` was given.
- **Pattern** and one sentence on why it fits.
- **2–5 zones**, each with:
  - position (e.g. "left third"), title, color;
  - contents as **concrete objects**, not abstract words: a grid for a mesh, stacked plates for layers, a cylinder for a dataset, a gauge for a metric, a small chart for a trend.
- **Connections**: from → to, style and label.
  - Styles: solid = main flow, dashed = feedback or optional, bold = the key path.
- **Labels**: the exact terms and numbers, each with its source location.
- **What is left out on purpose**, so the diagram stays readable.

## Step 4: Draw the SVG

**Canvas**
- Start with `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1200 H" width="1200" height="H" font-family="Helvetica, Arial, 'Apple SD Gothic Neo', 'Noto Sans CJK KR', 'Malgun Gothic', sans-serif">`, with H between 560 and 900.
- Then `<title>` and `<desc>` (one sentence), then a full-size white background `<rect>`.
  - The white background keeps the diagram readable in dark mode.
- Put a diagram title at the top (26px, bold).
- Leave a bottom row for a legend if you use ①②③ markers.

**Grid**
- Use coordinates in multiples of 8.
- Keep an outer margin of at least 40 and gaps of at least 32 between boxes.
- Make zones large rounded rects (`rx` 16) with a light fill and the zone title inside at the top-left (20px, bold).
- Make items inside a zone smaller rounded rects (`rx` 10).

**Text**
- Sizes: 16px for item labels, 14px for notes, never below 13px.
- Center a label in its box with `text-anchor="middle"` at the box center.
- Wrap long labels into at most 3 lines with `<tspan x="…" dy="1.25em">`.
- Size boxes for their text:
  - width ≥ estimated text width + 32. Estimate: Latin ≈ 0.55 × font size per character, Korean · CJK ≈ 1.0 × font size.
  - height ≥ lines × 1.25 × font size + 24.
- Keep labels short (≤ 24 Latin or ≤ 12 Korean characters per line). Move detail into the caption, or use a ①②③ marker with a legend.

**Color**
- Use at most 4 accents, one per zone, and keep each color's meaning the same across diagrams.
- Palettes (stroke / fill):
  - blue `#3a5ccc` / `#e8eefc`
  - teal `#0f8b8d` / `#e2f4f4`
  - orange `#d9822b` / `#fdf0e2`
  - violet `#7b5cc4` / `#efeafb`
  - gray `#4b5563` / `#f3f4f6`
- Text is `#1f2937`; muted text is `#6b7280`.

**Arrows**
- Define one `<marker id="arrow">` in `<defs>`.
- Connect box edge midpoints, preferring horizontal or vertical segments (`<path d="M … H … V …">`).
- Never run an arrow through a box or a label.
- Label important arrows. Place each label on a small white rect over the line.

**Icons**
- Build them from simple shapes only: a rect grid, stacked rects, an ellipse + rect cylinder, circles, a polyline sparkline.
- No emoji, no embedded images.

**Numbers**
- Use the document's values verbatim (`112.3×`, `p = 2.01`, `GCI = 0.4%`).
- For a Matrix Grid, put the numbers in the cells and highlight the best or the key cell with the accent color.

**Forbidden**
- `<script>`, `<foreignObject>`, `on…` attributes, external `href`, fonts or CSS `@import`.
- Rotated or skewed text.
- Filters and shadows. Keep it crisp.

## Step 5: Check and fix

1. Run `devtrack svgcheck ~/.doc-visual/<slug>/*.svg --png ~/.doc-visual/<slug>/preview`.
   - Fix every `✗` error.
   - Fix every `⚠` warning: text overflowing a box, overlapping text, overlapping boxes, text off the canvas, tiny text. The text widths are estimates; if a warning is a false alarm, confirm that in the preview before ignoring it.
2. If a PNG preview was made, **open it** (Claude: the Read tool on the `.png`) and look for:
   - clipped or colliding text;
   - arrows crossing labels;
   - crowded zones;
   - a reading order that is not obvious;
   - unbalanced white space.
3. Fix and repeat, at most 3 rounds. With no headless Chrome (common on HPC), rely on `svgcheck` and keep layouts simpler.
4. **Last check.**
   - Every label and number can be found in the document at the location you list.
   - A non-specialist can follow the arrows from start to end.
   - The diagram agrees with the summary.

## Step 6: Report

Write `~/.doc-visual/<slug>/visual-<slug>.md`. Refer to the SVGs by bare file name (`![…](v1-overview.svg)`):
- DevTrack links them to the uploaded SVG artifacts with the same name.
- `devtrack pdf` finds them next to the `.md` file.

```markdown
# 시각화 요약: <문서 제목>

> **한 줄 요약:** <무엇을 했고, 무엇을 보였는지>

| 문서 | 종류 | 분량 | 초점 |
|---|---|---|---|
| <제목 · 저자> | <과학 논문 / 보고서 / 제안서 / …> | <N쪽 · 약 W단어> | <전체 / 사용자가 고른 부분> |

## 1. 한눈에 보기
![V1 <제목>](v1-overview.svg)
**읽는 법:** <어디서 시작해 어떤 순서로 읽는지 2–3줄> · **근거:** <절 · 쪽 · 식 · 그림 번호>

## 2. 문서 지도
| 절 | 역할 | 한 줄 요지 |
|---|---|---|

## 3. 핵심 주장과 근거
## 4. 방법 흐름
## 5. 주요 결과
| 결과 | 값 | 조건 | 위치 |
|---|---|---|---|

## 6. 문서 구조 · 논리 흐름 (V2) / 결과 비교 (V3) — 그린 것만
![V2 …](v2-structure.svg)
**읽는 법:** … · **근거:** …

## 7. 한계 · 열린 질문
## 8. 용어 풀이
```

- **`prompt` appendix** (only when asked): `## 부록: 이미지 생성 AI 용 프롬프트`.
  - Add one prompt per diagram in the original skill's format:
    - `---BEGIN PROMPT---`
    - Style & Meta-Instructions
    - TEXT RENDERING RULES
    - LAYOUT CONFIGURATION (selected layout, composition logic, palette)
    - `[ZONE n: LOCATION - LABEL]` blocks (container, visual structure, key text labels)
    - `[CONNECTIONS]`
    - `---END PROMPT---`
  - Start the appendix with one line saying that pasting it into an image service shares the content with that service, and that generated images often misspell terms. Overlay the correct text afterwards.
- **PDF:** `devtrack pdf ~/.doc-visual/<slug>/visual-<slug>.md`.
  - If it fails (no headless Chrome), deliver the Markdown, say a PDF could not be made, and in DevTrack mode record `devtrack issue`.

## Step 7: Deliver

1. **DevTrack mode — upload.** Upload everything with `--visual --replace`. Reruns become new versions, and the task page's **시각화** box shows them.
   ```
   devtrack artifact <TID> ~/.doc-visual/<slug>/v1-overview.svg --visual --replace -n "도식 1: <제목>"
   devtrack artifact <TID> ~/.doc-visual/<slug>/v2-structure.svg --visual --replace -n "도식 2: <제목>"
   devtrack artifact <TID> ~/.doc-visual/<slug>/visual-<slug>.md  --visual --replace -n "시각화 요약"
   devtrack artifact <TID> ~/.doc-visual/<slug>/visual-<slug>.pdf --visual --replace -n "시각화 요약 (PDF)"
   ```
   - Upload the SVGs before the report, so the report's images link up.
   - Record `devtrack log <TID> "시각화: 요약 + 도식 <n>개 (F-…)"`.
2. **DevTrack mode — close out.**
   - If the request came from an inbox message, also run `devtrack inbox --task <TID> --ack`.
   - On a task this skill created, run `devtrack request <TID> "시각화 결과를 확인해 주세요" -k result -a <PDF or Markdown F-ID> -d "<한 줄 요약>"`, check the plan step, and stop.
     - When the decision arrives: approved → `devtrack done <TID> -m "시각화 전달"`; changes requested → revise, upload with the same names, and request again.
   - On an existing paper or document-review task, **do not** open an approval request, which would block that task's own flow. The log and the artifacts are enough.
3. **In chat:**
   - the one-line summary;
   - the diagrams, one line each (pattern + what it shows);
   - the F-IDs or file paths.

   Do not paste the whole report or the SVG code.

## Follow-ups

- **Changes to a drawing** ("도식 2를 단순하게", "3장 방법만 더 자세히", "라벨을 한국어로"): update that diagram's schema, redraw it, run `svgcheck` again, and upload with the same file name (`--replace` makes a new version). Regenerate the report and PDF if captions change.
- **A new version of the document**: same task and file names. Redraw only what changed, and add a line `변경: …` under the affected diagrams.

## Credits

The layout patterns, zoning, concrete-object and explicit-connection rules, and the `prompt` appendix format are adapted from
[WilsonWukz/paper-visualizer-skill](https://github.com/WilsonWukz/paper-visualizer-skill)
(`skills/visual-architect/SKILL.md`, MIT License, © 2026 WilsonWukz). See `LICENSE-visual-architect.txt` next to this file.
