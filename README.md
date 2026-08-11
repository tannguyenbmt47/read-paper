# Loupe

**A local research environment for scientific papers. Two tools that share a
codebase but not a workflow: a *reader* that translates one paper English →
Vietnamese while preserving its argument, and a *corpus* that indexes dozens of
papers and answers questions about them with citations down to the passage.**

Version 1.6.0 · runs locally · models called through
[OpenRouter](https://openrouter.ai) · [CHANGELOG](CHANGELOG.md) ·
[Docker](DOCKER.md)

A loupe is the lens a jeweller holds to one stone at a time. Machine translation
gives you the words at a glance; this is for the reading where you need to see
exactly what every claim rests on.

| | Reader | Survey corpus |
|---|---|---|
| Scale | one paper, read closely | 20–50 papers, queried |
| Output | bilingual columns, explanations, slides | answers with per-passage citations |
| Translation | full, and it is the point | none — indexing instead |
| Cost | ~$0.30 per paper | ~$0.034 per paper, then ~$0.03 per question |

```bash
./run.sh          # creates .env on first run; add your key and run again
# open http://localhost:8010   (change the port with PORT=9000 ./run.sh)
```

---

## Why this exists

Existing PDF translators (Immersive Translate, PDFMathTranslate/BabelDOC)
preserve **layout**. This tool preserves **reasoning**, and explains what each
paragraph contributes to the paper's argument.

Machine translation of scientific papers loses the argument for four specific,
nameable reasons:

| Failure | How it shows up | How this tool addresses it |
|---|---|---|
| Paragraphs translated in isolation | Discourse connectives are lost; `while` and `since` are rendered with the wrong sense | The **full text** is held in context, with a mandatory connective mapping table |
| Terminology never fixed | One term rendered three different ways in the same paper | A separate pass **fixes the glossary before translation begins** |
| Claim strength drifts | `may` → "will", `suggests` → "proves" | A hedging table forbids strengthening or weakening claims |
| No way to check the translation | You cannot trace a Vietnamese sentence back to its source | Paragraph-aligned bilingual columns plus an explanation layer |

## How it works: two separate stages

```
╔═ STAGE 1 · PREPROCESSING ══════════ no model calls, no cost ══════╗
║  PDF / arXiv / pasted text                                        ║
║    ├─ classify text: body text  vs  text inside figures           ║
║    ├─ segment into blocks: paragraphs, headings, captions,        ║
║    │  equations, references                                       ║
║    ├─ crop figures and tables to images                           ║
║    └─ drop proceedings headers/footers, footnotes, figure labels  ║
║                          ↓                                        ║
║  Review screen: check the crops, drop junk blocks,                ║
║  and see the estimated cost before committing                     ║
╚═══════════════════════════════════════════════════════════════════╝
                           ↓  you confirm
╔═ STAGE 2 · TRANSLATION ═══════════════════════════════════════════╗
║  Pass 1   read the whole paper → summary + argument chain         ║
║           + FIXED GLOSSARY + diagrams                             ║
║  Pass 2   translate batch by batch, carrying the full text,       ║
║           summary, and glossary                                   ║
║  Pass 2b  (optional) review against the source                    ║
║  Pass 3   explain a paragraph on demand, with a diagram           ║
║  Pass 4   draft a talk outline, then render it into slides        ║
╚═══════════════════════════════════════════════════════════════════╝
```

The two stages are separate because they fail in different ways and are fixed in
different ways. If segmentation is wrong, no amount of translation quality can
save the result — and segmentation is **free**, so it is worth reviewing before
spending anything on stage 2.

### Figure and table extraction: two tiers

**Tier 1 — layout model (recommended).** With `docling` installed, the tool uses
a layout-detection model to obtain a bounding box for each table and figure. This
is the only approach that handles pages containing several tables and figures
packed together; caption-relative heuristics cannot separate them.

```bash
.venv/bin/pip install docling      # ~5GB, uses the GPU if one is available
```

Measured on an ACL 2023 paper (9 figures/tables, one page holding three objects):

| | Correctly extracted | Overlapping box pairs |
|---|---|---|
| Heuristic | 8/9 | 1 |
| Layout model | **9/9** | **0** |

The first run after installation takes a few minutes (downloading weights and
compiling). The server warms the model at startup, so subsequent imports take
about 5 seconds. Disable it with the *Layout model* checkbox on the import
screen, or `LAYOUT_BACKEND=off` in `.env`.

**Tier 2 — heuristic, used when docling is absent: after PDFFigures 2.0**

The intuitive approach — "crop from the caption up to the nearest paragraph" —
fails constantly, because text *inside* a figure is also a valid text block as
far as PyMuPDF is concerned: diagram labels, table cells, even complete sentences
sitting inside an illustration.

[PDFFigures 2.0](https://ai2-website.s3.amazonaws.com/publications/pdf2.0.pdf)
(Allen AI, in production for Semantic Scholar, 94% precision / 90% recall)
inverts the order: **classify the text first, then infer the figure region**.
Most text in a paper is body text set consistently, so anything that deviates
from that norm is likely to be inside a figure:

| Signal | Conclusion |
|---|---|
| Overlaps a graphics cluster | text inside a figure |
| Smaller than the paper's body font | text inside a figure |
| Many unusually wide inter-word gaps | table body |
| Multi-line and exactly one column wide | body text |
| Larger than body font, flush or centered | paper title or section heading |
| Aligned to the column edge | body text |

Only then: expand from the caption to the nearest **body-text** block, and shrink
back around the largest graphics cluster inside that region. The shrink step is
what stops a crop from swallowing the paper's title and author list — those are
body text, but separated from the figure by whitespace.

**On cost.** The system prompt (translation rules + full paper text + glossary)
is byte-identical across every request for a given paper and always precedes the
cache breakpoint. Measured in practice: **99% of the system prompt is served from
cache**, shared across all four tasks (translate, review, explain, ask). For
`anthropic/*` models the breakpoint is marked explicitly; OpenAI, Gemini, and
DeepSeek cache automatically. A `session_id` keeps every request on the same
provider endpoint — without it the cache almost never hits.

## Features

### Preprocessing and review

- **Review screen before any spending** — verify the figure crops, drop junk
  blocks, and see the estimated cost using live model prices from OpenRouter.
- **Adjustable crop boxes** — press ✂ on a bad crop and the PDF page appears with
  the current box; drag any of the eight handles and save. Re-cropping happens at
  a higher DPI, so the result is sharper than the automatic one. Captions the
  tool missed entirely can be cropped by hand.
- **Block editing** — delete junk blocks, merge a paragraph split across a column
  break, split two paragraphs that were glued together.
- **Realignment with a cheap model** — PDFs store neither spaces nor structure,
  so extracted text often runs words together (`=∅or`), breaks at hyphens, or
  scrambles equation fragments. A cheap model cleans this up (~$0.001 per paper).
  It may **only** change whitespace and ordering: alphanumeric character
  multisets must match before and after, and any mismatch is rejected in favour
  of the original.
- **List structure preserved** — each bullet becomes its own block, translated
  separately and displayed as a list, rather than being flattened into one long
  paragraph.
- **Sub- and superscripts preserved** — `D = {dᵢ}ᴺᵢ₌₁` is stored as
  `D = {d_{i}}^{N}_{i=1}` rather than collapsing to `D = {di}N i=1`, and `ˆa` is
  recombined into `â`. This structure is what the model needs to translate and
  explain the formula correctly.

### Reading

- **Paragraph-aligned bilingual columns** — the columns share grid rows, so they
  stay aligned while scrolling without any scroll synchronisation.
- **Original figures and tables**, cropped straight from the PDF and shown above
  their captions.
- **Mermaid diagrams** for the paper's argument chain, the proposed mechanism,
  and individual paragraphs on request.
- **Glossary** fixed before translation, searchable, with definitions.
- **Argument explanations** per paragraph: main point · role in the paper · how
  it connects to what came before · detailed explanation · a concrete image ·
  what the authors are *not* claiming · a self-check question.
- **A plain-language column for readers without background** — restates the
  passage in ordinary words, defines new concepts in place, and explains both the
  mechanism and its role in the argument. Metaphors are forbidden; clarification
  must come from concrete examples taken from the paper itself.
- **Question answering** about the paper, with the full text already in context.
- **Highlights and notes** — five colours, editable notes that behave like
  comments, and an option to have the model explain exactly the highlighted span.
- **Side-by-side original PDF** — opens in a right-hand pane, follows the
  paragraph you are reading, and jumps to a paragraph's page when you click it.
- **Search across all three columns** (Ctrl+F), resume position, adjustable font
  size and column width, light and dark themes.

### Cost control

- **Three independently toggleable columns** — Source · Vietnamese · Explanation.
  A disabled column is **never generated**, so it costs nothing. Turning off the
  Vietnamese column produces explanations only.
- **Section-level translation** — the ☑ button lists the paper's sections with
  the number of blocks already translated and the cost of each; tick a section to
  translate only that one. Long papers usually need Methods and Results only;
  leaving the appendix alone means not paying for what you will not read.
- **Per-request pricing** — every translation batch and every explanation shows
  its own cost, the session total, and the running total for the paper.
- **Stop mid-translation** — pressing Stop finishes the batch in flight (already
  paid for) and then halts. Reopening the paper resumes; completed work is never
  redone.

### Slides

- **Two-step generation.** *Draft content* produces an **outline**: what the talk
  argues, how it divides into sections, what each slide proves, and which
  evidence supports it. You review and edit at the level of ideas — rewrite an
  assertion, add or remove points, choose a different figure, reorder — and only
  then press *Build slides*. Drafting is far cheaper than rendering, so editing
  at that stage is effectively free. Slides you have edited by hand are never
  overwritten by a rebuild.
- **Assertion–evidence design**, following Garner & Alley: each headline is a
  complete sentence stating what the slide proves, and the body is the evidence.
- **Direct editing on the slide**, plus per-slide free layout with drag, resize,
  snapping, and alignment guides.
- **Presentation mode** and export to PDF, HTML, or PPTX. In the PPTX, Mermaid
  diagrams are redrawn as native PowerPoint shapes, so they remain editable.

### Export

**PDF, HTML, or Markdown**, bilingual or Vietnamese only. Images are embedded
directly, so the file keeps its figures wherever it is opened; the HTML export is
a single self-contained file that reads offline with working diagrams. PDF export
goes through the browser's print dialog — the only route that preserves both the
Mermaid diagrams and the two-column grid.

## The survey corpus

The reader puts one paper's full text into the system prompt. That design is
what makes close reading work, and it is exactly what cannot answer *"how do
these approaches differ?"* — that needs thirty papers, and thirty papers do not
fit in a prompt.

So the corpus is a **second mechanism**, sharing infrastructure but not a single
line of the reader's pipeline. Its governing constraint: translating fifty
papers is not financially viable, so it **does not translate**. It indexes, and
it compresses each paper into a structured ~600-token *card*.

```
PDF → passages → context sentence → vectors → RAPTOR tree → card → entity graph
                                            ↓
  question → plan → search → read → check gaps → search again → answer → verify
```

**Retrieval is hybrid and every stage is there for a measured reason.**

- **BM25 (SQLite FTS5) + dense (BGE-M3), fused with Reciprocal Rank Fusion.**
  RRF reads only ranks, so it mixes BM25's unbounded negative scores with
  cosine's narrow band without any normalisation — and lets the dense retriever
  be switched off entirely without branching the code.
- **Embeddings run on your machine.** OpenRouter serves no embedding endpoint,
  so the alternative was a second paid API key. BGE-M3 is multilingual by
  construction, which is the whole problem here: the corpus is English, the
  questions are Vietnamese. Measured on this repo's own papers, Vietnamese
  queries retrieve the right English passages in 9–71 ms; BM25 alone returns
  noise for the same queries.
- **Contextual retrieval.** Each passage gets a generated English sentence
  placed beside it before indexing. A passage reading *"we reach 62.3 EM"*
  contains no method name, no dataset, no word a person would search for; the
  context sentence supplies them.
- **query2doc.** The planner writes a short fake English paragraph in the voice
  of a paper answering the question, then searches with it. This is also how a
  Vietnamese question reaches English text without a translation step.
- **A RAPTOR tree per paper.** Passages are clustered and summarised
  recursively. Queries hit *every level at once* — the original paper found that
  this "collapsed tree" beats walking down from the root, because a question
  often needs a number from a leaf and framing from a higher level together.
- **An entity graph across papers.** Extracted once per paper, with no community
  summaries (the part that makes full GraphRAG prohibitively expensive). It is
  used to *expand* results after retrieval, not as a parallel search path:
  plain vector search wins on single-fact lookup, graphs win on multi-hop, and
  expansion gets both.
- **Two rerank stages.** A cross-encoder cuts 60 candidates to 20 for free on
  the GPU; a model call then cuts 20 to 10 while seeing the sub-questions, which
  a cross-encoder cannot. A cap of 3 passages per paper enforces coverage.

**The deep-dive loop is bounded and honest.** It plans a checklist of
sub-questions, then searches, reads, marks which items now have evidence, and
searches again for the ones that do not — at most five rounds, carrying the two
best passages forward each time. Every round streams to the screen, the budget
is checked *before* each model call, and if the loop stops early the answer says
so. Items that never found evidence are stated as not found rather than papered
over; that failure — fluent prose covering a gap — is the one that makes a
research tool actively harmful.

**Answers are verified mechanically before you see them.** Every number must
appear verbatim in a cited passage; every citation must be a passage that was
actually retrieved this run; citations are clickable and open the exact text.
An optional entailment pass catches the subtler failure where the citation is
real but does not support the claim. Warnings are shown, not enforced — you have
the screen to judge for yourself.

**Cost.** Parsing, chunking, indexing and embedding are free. Enrichment costs
about $0.034 per paper, once. A three-round question costs about $0.03, and
asking the same question again while the corpus is unchanged is free.

## Installation

Requires Python 3.10+.

```bash
./run.sh
```

The script creates `.venv`, installs dependencies, and generates `.env` from the
template. Open `.env`, add a key from <https://openrouter.ai/keys>, and run it
again.

Configuration in `.env`:

| Variable | Meaning |
|---|---|
| `OPENROUTER_API_KEY` | Required |
| `OR_MODEL` | Translation model. Defaults to `~deepseek/deepseek-v4-flash-latest` |
| `OR_MODEL_FAST` | Model for light tasks (reserved, not yet used) |
| `OPENROUTER_BASE_URL` | Change when routing through an internal proxy |
| `PAPER_DATA_DIR` | Where papers are stored. Defaults to `./data` |

The model can also be changed from the interface: at import, on the review screen
(the cost estimate recalculates), and from the toolbar while reading. Switching
mid-way does not re-translate finished work; only pending batches use the new
model.

| Model | Input/output per 1M tokens | Notes |
|---|---|---|
| `~deepseek/deepseek-v4-flash-latest` | $0.09 / $0.18 | Default. 1M context. Abstract + introduction costs about $0.005. Slower on the whole-paper pass (~60–75s). |
| `openai/gpt-5.6-luna` | $0.10 / $0.60 | Nearly as cheap as DeepSeek, 1M context |
| `deepseek/deepseek-v4-pro` | $0.43 / $0.87 | Better, still inexpensive |
| `openai/gpt-5.6-terra` | $1 / $6 | 1M context. The `-pro` variant costs the same with deeper reasoning |
| `anthropic/claude-sonnet-4.5` | $3 / $15 | Fluent Vietnamese, roughly 33× the cost of DeepSeek |
| `openai/gpt-5.6-sol` | $5 / $30 | Top of the 5.6 line |

The leading `~` is part of the model name — OpenRouter uses it for
self-updating aliases.

## Known limitations

- **Scanned PDFs are not readable** — run OCR first (`ocrmypdf`).
- Unusual layouts (three columns, magazine spreads, posters) segment less
  reliably than standard one- and two-column papers.
- The heuristics are tuned for computer-science conference papers (ACL, NeurIPS,
  and similar), matching the scope PDFFigures 2.0 targeted. Other fields may
  behave differently.
- Equations are kept as text and **not rendered as LaTeX**. Sub- and superscripts
  survive (`x^{2}`, `d_{i}`), but for multi-level fractions, sums, and integrals
  the original PDF pane remains the better view.
- **Reasoning models need to be held back.** DeepSeek V4 and the GPT-5.x line can
  spend their entire token budget on internal reasoning and return an empty
  response or truncated JSON. The tool disables reasoning for translation passes
  and keeps it low for the whole-paper pass.
- **Chinese-origin models may answer in Chinese** even when prompted in
  Vietnamese. The tool states the language rule explicitly, detects leaked Han
  characters, and retries once — but only relative to the source, so genuine
  Chinese quotations are preserved.
- Reference lists are deliberately **not** translated.

This is precisely why stage 1 exists: when a heuristic gets something wrong, you
see it and fix it immediately, instead of discovering it after paying to
translate the entire paper.

### Verified on

| Paper | Figures & tables extracted | Junk blocks remaining |
|---|---|---|
| Attention Is All You Need (NeurIPS 2017, 2 columns) | 6/6 | 0 |
| Precise Zero-Shot Dense Retrieval (ACL 2023, 2 columns) | 8/9, one overlapping box pair | 0 |

Two tables set close together can still share a single box. When that happens,
stage 1 shows two identical images, which you separate with ✂. The tool
deliberately does **not** discard one of them automatically: doing so would lose
a table without telling you.

## Repository layout

```
server/
  parser.py    PDF/text → structured blocks; crops figures and tables to images
  prompts.py   every prompt for the reader — where quality is decided
  llm.py       OpenRouter wrapper: streaming, cache breakpoints, sticky sessions
  pipeline.py  orchestrates the reader's passes and assembles shared context
  layout.py    layout-detection model (Docling), optional
  db.py        SQLite: documents, parse cache, translation memory
  store.py     facade over db.py; images and source PDFs stay on disk
  main.py      HTTP API, SSE, and PDF/HTML/Markdown/PPTX export
  survey/      the corpus tool — separate mechanism, shares only infrastructure
    db.py      its own tables + FTS5 index + vectors + entity graph
    ingest.py  PDF → passages → context → vectors → tree → card → graph
    tree.py    per-paper RAPTOR tree (recursive cluster and summarise)
    graph.py   entity and relation extraction, cross-paper edges
    embed.py   BGE-M3 embeddings and cross-encoder reranking, on your own GPU
    search.py  hybrid retrieval: BM25 + dense → RRF → two rerank stages
    agent.py   the budgeted deep-dive loop
    verify.py  citation and number grounding — the guard rail for answers
    prompts.py every prompt for the corpus tool
  survey_api.py  its routes, mounted into the same app
web/           front end, no framework (app.js = reader, survey.js = corpus)
```

To change translation quality, edit `server/prompts.py`; for the corpus tool,
`server/survey/prompts.py`. Everything else is plumbing.

## Development

```bash
.venv/bin/python -m pytest                          # 114 tests, ~3 minutes
.venv/bin/python -m pytest tests/test_unit.py -q    # pure logic, ~3 seconds
.venv/bin/python -m pytest tests/test_survey.py -q  # corpus tool, ~4 seconds
node --check web/app.js web/survey.js
```

`tests/test_api.py` and `tests/test_survey.py` exercise the real API against a
temporary `PAPER_DATA_DIR`, so they never touch your own `data/`, and they make
no model calls, so they cost nothing. The corpus tests run with
`EMBED_BACKEND=off`: the BM25-only path has to work on its own, because that is
the path a machine without a GPU takes.

Note that the source comments, prompts, and user-facing strings are written in
Vietnamese — that is the audience the tool is built for. This README and
[CHANGELOG](CHANGELOG.md) are the English-facing documentation.

Note on conventions: docstrings, comments, button labels, and user-facing error
messages are written **in Vietnamese**, since that is the audience the tool
serves. `CLAUDE.md` documents the architecture and the traps worth knowing about,
also in Vietnamese.
