---
name: make-poster
description: Build an editable conference poster as a single-slide PowerPoint (.pptx, A0/A1/48x36 in or a custom size) from a paper or DevTrack task, with no external service. Chooses 1-3 main messages and a 300-800 word budget, lays out a title band and 2-4 columns of blocks (text, bullets, figures, tables) with `devtrack pptx` (standard library only, works on HPC), checks overflow, column balance and figure resolution with a preview, and records it in DevTrack. A LaTeX poster (tikzposter/beamerposter) is an option when the user asks for LaTeX. Use for "포스터", "학회 포스터", "poster". Adapted from K-Dense latex-posters and pptx-posters (MIT) with image-generation APIs, pip dependencies and lookups removed.
argument-hint: "<T-xxxx | document path> [size and venue, e.g. A0 세로 · 48x36in 가로 · LaTeX]"
allowed-tools: Bash(devtrack *)
---

# Make a poster

The skill makes three files:
- `poster.pptx`: one editable slide at print size;
- `poster.pdf`: a preview;
- `poster.json`: the spec they are built from.

`devtrack pptx` writes the .pptx with the Python standard library, so it works on HPC without pip.

Language:
- **Poster text** is in the poster's language: the user's choice, otherwise the source document's.
- **Your summary to the user** is in Korean.

Arguments: $ARGUMENTS
- **A DevTrack task** (a paper, document review or analysis) or a document path.
- **The rest** gives the size, orientation and venue, e.g. "A0 세로", "48x36in 가로", "A1", and `LaTeX` for a LaTeX poster.

## Hard rules

- **No external services.**
  - No image-generation APIs.
  - No web search, no online templates, icons or fonts.
  - No QR-code services. A QR code goes on only as an image file the user provides.
- **Figures** come from the user's files only, or are diagrams you draw as SVG by the `/visualize-doc` rules. Never draw or "illustrate" data, plots, micrographs or spectra.
- **Logos** go on only from files the user provides.
- **No fabrication.**
  - Numbers come from the source exactly as written.
  - Names, affiliations, contact, funding and acknowledgments appear only if given. Otherwise write `[[MISSING: …]]`.
  - References come only from the source's own bibliography.
- **Size.** PowerPoint allows at most 56 in (1422 mm) per side. A bigger physical poster needs a proportional canvas that the printer scales; ask first.

Work files go in `~/.poster-work/<slug>/`: `poster.json`, `poster.pptx`, `poster.pdf`, `figures/`. Keep the same names on reruns.

## Step 0: DevTrack

DevTrack mode is on when `devtrack --version` succeeds.

- **Task.**
  - **A `poster` task** — the web's New task with type 포스터, or the Posters menu's New poster. This is the skill's own task.
    - `devtrack start <TID>`.
    - Set the plan, if it has none, with `devtrack plan <TID> "요구 사항 · 핵심 메시지" "내용 · 그림 고르기" "배치 설명 작성" "만들기 · 점검" "확인 (사용자)"`.
    - Record progress with `-p`.
    - The source is a related task or named in the instruction.
  - **The poster is about another existing task** (paper, document review). Work in that task.
    - Use `devtrack step <TID> "포스터: …"` **without `-p`**.
    - Do not change its plan or status.
  - **Otherwise**, create a `poster` task:
    ```
    TID=$(devtrack new "포스터: <제목>" -t poster -i "<the user's request, verbatim>" --related <source task> \
          --plan "요구 사항 · 핵심 메시지" "내용 · 그림 고르기" "배치 설명 작성" "만들기 · 점검" "확인 (사용자)")
    ```
- **Sources.**
  - `devtrack doc <TID> --all` lists the documents.
  - `devtrack doc <TID> --figures` lists the figures.
  - For each linked task, read `devtrack show <R> --brief`.

## Step 1: Requirements

Record, or ask in one message:
- **Paper size and orientation.** Presets are `A0`, `A0-landscape`, `A1`, `A1-landscape`, `48x36in` and `36x48in`; anything else is `{"width_mm": …, "height_mm": …}`.
- **Venue rules**: maximum size, required sections, logos.
- **Printer needs**: bleed, margins, file format.
- **Presenter and authors, affiliations, contact**, if they should appear.
- **Accent color**, if there is one.

## Step 2: Content budget

- **Messages.** Pick 1–3 main messages. Everything on the poster supports one of them.
- **Budget.** Aim for 300–800 words in total and 3–5 figures. A reader at 1.5–2 m should get each message from the headings and figures alone.
- **Typical blocks.** Each block heading should be a short claim where possible.
  - Motivation or question.
  - Approach or methods: a diagram rather than paragraphs.
  - Results: the largest share, figures first.
  - Conclusions or take-home: 2–4 points.
  - References: short, from the source's bibliography.
  - Acknowledgments and funding: only if given.
- **Fidelity.** Keep design, run counts, units, uncertainty and limitations. Do not round away the error bars.

## Step 3: Figures

Use the same rules as the slides:
- **Reuse.** Use the existing figures from `devtrack doc <TID> --figures`. SVG is best for print.
- **Simplify.** You may make simplified copies from the same data, never with changed values.
- **Diagrams.** Draw them as SVG and check them with `devtrack svgcheck --png`.
- **Resolution.** Raster figures need about 150 dpi at print size. `devtrack pptx` warns when a figure is stretched too far.
- **PDF figures.** Convert them with `pdftoppm -png -r 300` if available; otherwise ask for a PNG or SVG.

## Step 4: Write the poster spec

Start from `devtrack pptx --example poster`. One slide with `"layout": "poster"`:
- **Title band**:
  - `title`, `authors`, `affiliation`;
  - `logos`: image files given by the user;
  - `band_mm`: band height, default 150 mm at A0.
- **`columns`**: 2–4 columns for portrait, 3–5 for landscape. Each column is a list of blocks, top to bottom.
- **Block keys**:
  - `heading`;
  - `text` (a string or a list of paragraphs), `bullets`;
  - `image` with `caption` and `alt`;
  - `table`: `{header, rows, highlight}`;
  - `size`: text scale, default 1.0, which is 30 pt at A0;
  - `h`: a fixed height in % of the column; otherwise the block takes the height of its content.
- **Text sizes at A0.** Title 84 pt, headings 48 pt, body 30 pt; scaled by paper size. Never put body text below 24 pt at A0.
- **Markup**: `**bold**`, `*italic*`, `^{sup}`, `_{sub}`.
- **Free placement.** For a layout that blocks cannot express, use `"layout": "free"` with items positioned in % of the page.

## Step 5: Build and check

```
devtrack pptx ~/.poster-work/<slug>/poster.json --pdf --png
```
- **Warnings.** Fix every `⚠`:
  - **text overflow**: cut words; posters are for skimming;
  - **a column overflowing**: move a block, or trim it;
  - **a column with a large empty bottom**: rebalance the blocks between columns, or enlarge a figure (`h`);
  - **a low-resolution figure**: use the SVG or a larger export.
- **Look at the PNG preview.** Claude: the Read tool. Check:
  - the reading order is obvious (columns left to right);
  - the messages are visible at a glance;
  - figures are large and their labels legible;
  - the columns end at similar heights;
  - no placeholder is left by mistake.
- Repeat at most 3 rounds. The preview line breaks can differ slightly from PowerPoint, so leave some room.
- **For printing**: tell the user to open the .pptx in PowerPoint, check it, then export the print PDF (File → Export → PDF). The preview PDF is for checking, not for the printer.

## LaTeX poster (when the user asks for LaTeX)

1. **Write it.** Write `poster.tex` with `tikzposter`, or `beamerposter` if the user's institution has a Beamer theme. Use the same content and budget as above.
2. **Figures.** Use vector PDF figures (pdfLaTeX cannot include SVG directly), with `\graphicspath{{figures/}}` and `\includegraphics` without the extension.
3. **Compile** with `devtrack latex <TID> poster.tex`. This needs a LaTeX installation with the poster class. If it is missing, say so and deliver the .tex.
4. **Check.** Read the compiled PDF and check it with the same list as Step 5.

## Step 6: Deliver

1. **Upload** (DevTrack mode). The PDF shares the .pptx's base name, so DevTrack shows it as the preview of the .pptx.
   ```
   devtrack artifact <TID> ~/.poster-work/<slug>/poster.pptx --replace -n "포스터 (PowerPoint)"
   devtrack artifact <TID> ~/.poster-work/<slug>/poster.pdf  --replace -n "포스터 미리보기 (PDF)"
   ```
2. **Close out.**
   - Record `devtrack log <TID> "포스터: <크기> · 단 n개 · 약 w단어 · 그림 k개 · 남은 [[MISSING]] m개"`.
   - If the request came from the inbox, run `devtrack inbox --task <TID> --ack`.
   - On a `poster` task, run `devtrack request <TID> "포스터를 확인해 주세요" -k result -a <pptx F-ID>` and stop.
3. **In chat:**
   - the size;
   - the main messages;
   - the column plan;
   - the placeholders to fill;
   - a note on exporting the print PDF from PowerPoint.

## Follow-ups

- **Changes.** Edit `poster.json`, rebuild, and upload with the same names. A message on a `poster` task is such a follow-up; the inbox marks it `→ 유형 포스터`.
- **Manual edits are not merged back.** Edits made in PowerPoint do not flow back into the spec. Ask before rebuilding over them; the old version stays in DevTrack.

## Credits

The content budget, poster structure, figure and print rules are adapted from K-Dense *latex-posters* and *pptx-posters*: <https://github.com/K-Dense-AI/scientific-agent-skills>, MIT License, © 2025 K-Dense Inc.; see `LICENSE-k-dense.txt` next to this file. This adaptation removes:
- image generation through external APIs;
- the python-pptx and pip dependencies;
- the original's request to cite its authors' paper.

The .pptx files are written by DevTrack's own `devtrack pptx`.
