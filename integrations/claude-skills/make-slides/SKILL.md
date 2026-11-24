---
name: make-slides
description: Build an editable PowerPoint (.pptx) talk - conference talk, seminar, group meeting, defense, journal club - from a paper, report or DevTrack task, with no external service. Plans the story and timing, picks figures from the task and its linked analyses (and draws any missing diagram as SVG), writes a slide spec, builds the .pptx with `devtrack pptx` (standard library only, works on HPC), checks text overflow with a preview PDF, adds speaker notes, and records it in DevTrack. Use for "발표 자료", "슬라이드", "PPT", "pptx", "학회 발표", "세미나 발표". Adapted from K-Dense scientific-slides (MIT) with image-generation APIs and literature lookup removed.
argument-hint: "<T-xxxx | document path> [talk type and length, e.g. 학회 15분 · 세미나 45분 · 그룹 미팅 10분]"
allowed-tools: Bash(devtrack *)
---

# Make slides (PowerPoint)

The skill makes three files:
- `deck.pptx`: editable PowerPoint with native text, tables, figures and speaker notes;
- `deck.pdf`: preview of the same deck;
- `deck.json`: the spec they are built from, kept so the deck can be rebuilt.

`devtrack pptx` writes the .pptx with the Python standard library, so it works on HPC without pip. The preview needs headless Chrome.

Language:
- **Slide text and speaker notes** are in the talk's language. Use what the user says; otherwise use the source document's language, so an English paper gives English slides.
- **Your summary to the user** is in Korean.
- Reply in the language of the user's latest message.

Arguments: $ARGUMENTS
- **A DevTrack task** (a paper, document review or analysis) or a document path.
- **The rest** gives the talk type, length, audience and focus, e.g. "학회 15분", "그룹 미팅 10분, 결과 위주", "박사 디펜스 45분".
- With neither a task nor a path, ask what the talk is about.

## Hard rules

- **No external services.**
  - No image-generation APIs (OpenRouter, Gemini or Nano Banana, DALL·E, Midjourney…).
  - No web search or literature lookup.
  - No online templates, icons or fonts.
- **Pictures come only from these sources:**
  - the user's files (figures already in the task, its linked analyses or the manuscript);
  - diagrams you draw yourself as SVG (Step 3).
- **No fabrication.**
  - Numbers on slides come from the source, exactly as written.
  - Names, affiliations, venue, date, funding and acknowledgments appear only if the user gave them. Otherwise write `[[MISSING: …]]`.
  - Cite only the works the source cites. Do not add references.
- Never modify the source document or its figures. Simplified versions are new files.

Work files go in `~/.slides-work/<slug>/`, where the slug comes from the task ID or file name: `deck.json`, `deck.pptx`, `deck.pdf`, `figures/`. Keep the same names on reruns so DevTrack stores new versions.

## Step 0: DevTrack

DevTrack mode is on when `devtrack --version` succeeds.

- **Task.**
  - **The talk is about an existing task** (a paper or document-review task given, or the request came through `devtrack inbox`). Work in that task. Record progress with `devtrack step <TID> "발표 자료: …"` **without `-p`**, so the task's own progress is kept, and do not change its plan or status.
  - **Otherwise**, create a task and link the source task if there is one:
    ```
    TID=$(devtrack new "발표 자료: <제목>" -t other -i "<the user's request, verbatim>" --related <source task> \
          --plan "내용 · 그림 모으기" "이야기 구성 · 시간 배분" "슬라이드 설명 작성" "만들기 · 점검" "확인 (사용자)")
    ```
- **Sources.**
  - `devtrack doc <TID> --all` lists the documents.
  - `devtrack doc <TID> --figures` lists the figure files of the task and its linked tasks.
  - For each linked task, read `devtrack show <R> --brief` (status, key results, handoff note).

## Step 1: Intake

Find out, or ask in one short message:
- talk type and length in minutes;
- audience (specialists in the field, a broad department, a general audience);
- language;
- aspect ratio: 16:9 by default, 4:3 if the venue requires it;
- presenter name and affiliation, venue and date, if they should appear;
- color, if the user or venue has one (theme `accent`).

`devtrack pptx` does not apply someone else's .pptx template. If the venue requires a template, build the deck, then apply the theme in PowerPoint (Design → Themes) and check the slides.

## Step 2: Story and timing

**Count.** Plan about one slide per minute: 10–15 for a 15-minute talk, 30–45 for 45 minutes. Put backup slides after the conclusion.

**Arc.**
1. Title.
2. Why it matters: the problem in the audience's terms.
3. The question or gap.
4. Approach: 1–3 slides.
5. Results: 40–50 % of the slides, one message per slide.
6. Take-home: 2–3 points.
7. Acknowledgments (only from the user).
8. Backup.

**Assertion-evidence.** The slide title is a full-sentence claim ("112.3× faster on 8 GCDs") and the body is its evidence, usually one figure. Avoid topic-label titles like "Results".

**By talk type.**
- **Conference**: one main result, few equations.
- **Seminar**: more background and methods.
- **Group meeting**: progress, open problems, next steps.
- **Defense**: chapters as acts, a contributions slide early and late.
- **Journal club**: someone else's paper; attribute every figure to it.

Write the plan in `outline.md`: for each slide, the title claim, the evidence (figure or numbers with source location), the speaker-note gist and the minutes.

## Step 3: Figures

- **Reuse first.** Use the existing figures (PNG, JPEG, GIF, SVG) from `devtrack doc <TID> --figures`, the manuscript folder or the user's files.
  - SVG is best: sharp at any size. `devtrack pptx` also embeds a PNG fallback for older PowerPoint, using headless Chrome or a same-name `.png` next to the SVG.
  - A PDF figure cannot be embedded. Convert it with `pdftoppm -png -r 200 fig.pdf fig` if poppler is installed; otherwise ask for a PNG or SVG.
- **Simplify for the room.** A paper figure is often too dense for a slide. You may make a simplified copy from *the same data*: fewer curves, bigger labels, the key comparison highlighted. Never change values.
- **Diagrams** (method pipeline, setup, concept) are drawn as SVG by the `/visualize-doc` rules:
  - layout pattern, 2–5 zones, the document's exact terms;
  - safe SVG with `xmlns` and a `viewBox`, no scripts or external links;
  - checked with `devtrack svgcheck <svg> --png`.
- One main figure per slide, as large as the slide allows. Captions name the source (paper figure number or task) and the conditions.

## Step 4: Write the slide spec

Start from `devtrack pptx --example slides` and write `deck.json`.

**Layouts**

| layout | use | keys |
|---|---|---|
| `title` | title slide | `title`, `subtitle`, `lines` |
| `section` | divider between parts | `title`, `subtitle` |
| `bullets` | short list | `title`, `bullets` (nested list = sub-points) |
| `figure` | one figure | `title`, `image`, `caption`, `alt` |
| `figure-bullets` | figure + 2–4 points | `image`, `bullets`, `image_side` (`left`/`right`), `image_width` (0.4–0.7) |
| `two-figures` | side-by-side comparison | `images`: two `{path, caption}` |
| `table` | key numbers | `table`: `{header, rows, highlight: [[row, col]]}`, `caption` |
| `statement` | one big message (take-home) | `title`, `text`, `sub` |
| `free` | custom arrangement | `items`: text · image · rect · round · ellipse · line · arrow · table, positions in % of the slide |

**Text**
- At most 6 bullets per slide, at most about 10 words each.
- Body text 22–24 pt. Never below 18 pt (the default sizes are fine).
- Slide titles fit in one line, two at most.
- Inline markup: `**bold**`, `*italic*`, `^{sup}`, `_{sub}`, as in `Re_{τ}` and `y^{+}`.

**Notes and placeholders**
- Every content slide gets `note`: 2–5 sentences of what to say, plus a timing cue ("~1 min").
- Unknown facts stay `[[MISSING: …]]`, visible on the slide, until the user fills them in.

**Deck options**
- `footer`: short; the talk title or venue.
- `numbers: true` adds slide numbers.
- `theme`: `{"accent": "#…"}`, plus `font` (default Arial) and `font_ko` (default 맑은 고딕).

## Step 5: Build and check

```
devtrack pptx ~/.slides-work/<slug>/deck.json --pdf          # add --png <dir> for one PNG per slide
```
- **Warnings.** Fix every `⚠` before showing the user:
  - text that may overflow: shorten it, or split the slide;
  - a low-resolution figure: find a better source or use the SVG;
  - more than 6 bullets.
- **Look at the preview.** Claude: the Read tool on the PDF with `pages`, or on the PNGs. Check:
  - every slide is readable from the back of the room;
  - each slide carries one message;
  - figures are large and their labels legible;
  - colors are consistent;
  - nothing collides;
  - no placeholder is left by mistake.
- Repeat at most 3 rounds.
- The preview is drawn with the same layout as the .pptx, but line breaks can differ slightly from PowerPoint, so leave some room.
- With no headless Chrome (common on HPC), rely on the warnings and keep the text lighter.
- Timing check: about one minute per content slide, and the number of slides fits the length.

## Step 6: Deliver

1. **Upload** (DevTrack mode). The PDF has the same base name as the .pptx, so DevTrack shows it as the preview of the .pptx.
   ```
   devtrack artifact <TID> ~/.slides-work/<slug>/deck.pptx --replace -n "발표 자료 (PowerPoint)"
   devtrack artifact <TID> ~/.slides-work/<slug>/deck.pdf  --replace -n "발표 자료 미리보기 (PDF)"
   ```
2. **Close out.**
   - Record `devtrack log <TID> "발표 자료: 슬라이드 n장 · 약 m분 · 남은 [[MISSING]] k개"`.
   - If the request came from an inbox message, also run `devtrack inbox --task <TID> --ack`.
   - On a task this skill created, run `devtrack request <TID> "발표 자료를 확인해 주세요" -k result -a <pptx F-ID> -d "<구성 · 남은 것>"` and stop.
   - On an existing paper or review task, do not open a request.
3. **In chat:**
   - the slide count and minutes;
   - the arc, one line per part;
   - the placeholders the user must fill;
   - the file paths or F-IDs.

## Follow-ups

- **Changes.** Edit `deck.json`, rebuild, and upload with the same names (`--replace` keeps the old version).
- **Manual edits are not merged back.** Changes the user made in PowerPoint do not flow back into `deck.json`. If the user has edited the .pptx by hand, ask before rebuilding, because a rebuild replaces those edits; the old version stays in DevTrack.

## Credits

The talk structure, timing and design principles are adapted from K-Dense *scientific-slides*: <https://github.com/K-Dense-AI/scientific-agent-skills>, `skills/scientific-slides`, MIT License, © 2025 K-Dense Inc.; see `LICENSE-k-dense.txt` next to this file. This adaptation removes:
- image generation through external APIs;
- literature lookup;
- the PptxGenJS / Node workflow;
- the original's request to cite its authors' paper.

The .pptx files are written by DevTrack's own `devtrack pptx`.
