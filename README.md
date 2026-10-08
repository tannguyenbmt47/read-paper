# Loupe

**Read scientific papers in Vietnamese without losing the argument.**

Loupe is a local web app with two tools that share one codebase:

- **Reader** — translates one paper English → Vietnamese, paragraph by paragraph,
  next to the original, with a plain-language explanation of what each paragraph
  does in the paper's argument.
- **Survey corpus** — indexes dozens of papers and answers questions across them,
  with every claim cited down to the passage.

Version 1.18.0 · runs on your machine · models via [OpenRouter](https://openrouter.ai)
· [Changelog](CHANGELOG.md) · [Docker](DOCKER.md)
· [Giới thiệu (tiếng Việt)](docs/index.html) · [Hướng dẫn sử dụng](docs/huong-dan.html)

```bash
./run.sh                      # first run creates .env — add your key, run again
# → http://localhost:8010     (PORT=9000 ./run.sh to change the port)
```

---

## Contents

1. [Why Loupe](#why-loupe)
2. [The reader](#the-reader)
3. [The survey corpus](#the-survey-corpus)
4. [What it costs](#what-it-costs)
5. [Installation and configuration](#installation-and-configuration)
6. [Limitations](#limitations)
7. [Project layout](#project-layout)
8. [Development](#development)

---

## Why Loupe

PDF translators such as Immersive Translate or PDFMathTranslate preserve the
**layout** of a paper. Loupe preserves its **reasoning**. Machine translation of
research papers fails in four predictable ways, and each one has a specific
counter-measure here:

| Failure | What you see | What Loupe does |
|---|---|---|
| Paragraphs translated in isolation | Connectives like *while* and *since* take the wrong sense | The **full paper** stays in context for every request |
| Terminology drifts | One term rendered three ways in one paper | A **glossary is fixed** before translation starts |
| Claims change strength | *may* → "will", *suggests* → "proves" | Explicit rules forbid strengthening or weakening a claim |
| Nothing to check against | A Vietnamese sentence cannot be traced to its source | **Paragraph-aligned columns**: source · translation · explanation |

| | Reader | Survey corpus |
|---|---|---|
| Scale | one paper, read closely | 20–50 papers, queried |
| Produces | bilingual columns, explanations, slides | cited answers, a synthesis, per-paper lectures |
| Translates? | yes — that is the point | no — it indexes instead |

---

## The reader

### Two stages, deliberately separate

```
 STAGE 1 · PREPARE                                     free, no model calls
 ─────────────────────────────────────────────────────────────────────────
 PDF · arXiv link · Markdown · pasted text
   → blocks: paragraphs, headings, lists, captions, equations, tables
   → figures, tables and display equations cropped to images
   → headers, footers, reference list and stray fragments set aside
 Review screen: fix crops, merge or split blocks, see the price
                                    │ you confirm
                                    ▼
 STAGE 2 · TRANSLATE                                   paid, priced per step
 ─────────────────────────────────────────────────────────────────────────
 Pass 1   read the whole paper → summary, argument chain, fixed glossary
 Pass 2   translate in batches (three in parallel)
 Pass 2b  optional review against the source
 Pass 3   explain any paragraph on demand
 Pass 4   draft a talk outline, then build slides from it
 Pass 5   highlight the sentences worth remembering, each with a reason
```

Segmentation errors cannot be fixed by better translation, and segmentation is
free — so you review it before spending anything.

### Reading

- **Three aligned columns** — source, Vietnamese, and a plain-language
  explanation. Each column can be switched off, and a column that is off is
  **never generated**, so it costs nothing.
- **Figures, tables and equations** cropped from the PDF; click *Figure 3* in the
  text to preview it, zoom and pan.
- **Glossary, summary and argument chain** in the side panel, with Mermaid
  diagrams.
- **Highlights and notes** in five colours; ask the model about exactly the span
  you highlighted.
- **Ask about this paper** — a chat with the full text already in context.
- **Original PDF side by side**, following the paragraph you are reading.
- **Edit or re-translate** a single paragraph; your edits are remembered across
  papers.
- Search across all columns, resume where you stopped, light and dark themes.

### Cost control

- **Translate by section** — tick Methods and Results, skip the appendix.
- **Price on every action** — each batch, explanation and slide build shows its
  own cost and the running total for the paper.
- **Stop at any time** — finished work is kept; resuming never redoes it.
- **Translation memory** — a paragraph translated once is reused for free,
  in any paper.

### Slides

Two steps: first an **outline** you edit at the level of ideas (what each slide
claims and which evidence proves it), then the **slides** themselves. Slides
follow the assertion–evidence design (Garner & Alley): every headline is a full
sentence, every body is evidence. Edit directly on the slide, present in the
browser, or export to **PDF, HTML or PPTX** — in the PPTX, diagrams are native
shapes you can still edit.

### Library

Folders, multi-select to move or delete, search and sort. Importing a paper you
already have asks first, and offers to open it, re-parse it, overwrite it, or
keep the new file as **a new version** (v2, v3 …) of the same paper.

### Export

Bilingual or Vietnamese-only **PDF, HTML or Markdown**. Images are embedded, so
the HTML file is self-contained and works offline with diagrams intact.

---

## The survey corpus

The reader works because one paper fits in the prompt. Thirty papers do not, and
translating fifty is not affordable — so the corpus **does not translate**. It
indexes each paper and compresses it into a structured ~600-token *card*.

```
 PDF → passages → context sentence → vectors → summary tree → card → entity graph
                                        │
 question → plan → search → read → check gaps → search again → answer → verify
```

**Retrieval is hybrid**, and each part is there for a measured reason:

| Component | Why |
|---|---|
| BM25 (SQLite FTS5) + BGE-M3 vectors, fused with RRF | Rank fusion needs no score normalisation, and the vector side can be switched off without changing code |
| Embeddings on your own machine | No second API key; BGE-M3 puts Vietnamese questions and English papers in one vector space |
| A generated context sentence per passage | *"we reach 62.3 EM"* alone contains nothing anyone would search for |
| query2doc | A short fake English answer is searched instead of the raw Vietnamese question |
| RAPTOR summary tree, searched as one flat index | A question often needs a number from a leaf and framing from a summary at once |
| Entity graph, used to expand results | Vectors win on single facts, graphs on multi-hop questions; expansion gets both |
| Cross-encoder, then a model rerank | 60 → 20 for free on the GPU, then 20 → 10 with the sub-questions in view |

**The search loop is bounded and honest.** It plans a checklist of
sub-questions, then searches, reads and re-searches for the gaps — at most five
rounds, with the budget checked before every call. Anything never found is
**stated as not found** in the answer instead of being papered over.

**Answers are checked before you see them.** Every number must appear verbatim
in a cited passage, and every citation must be a passage actually retrieved for
this question. Citations open the exact text. Problems are shown as warnings,
not hidden.

Three views per corpus: **Q&A**, a **synthesis** of the whole corpus (approaches,
tensions, gaps, a reading order), and a **lecture** that teaches one paper —
including how its own authors describe each work they cite, pulled free from
Semantic Scholar.

---

## What it costs

Measured on real papers with the default model (DeepSeek V4 Flash).

| Action | Typical cost |
|---|---|
| Import, segmentation, figure crops, review | free |
| Translate a full paper (translation + explanation) | ~$0.04–0.10 (DeepSeek V4 Pro: ~$1–3) |
| Explain one paragraph | ~$0.003 |
| Add a paper to a corpus | ~$0.034, once |
| A three-round corpus question | ~$0.03 (asking it again is free) |

The prompt prefix (rules + summary + glossary + full paper) is byte-identical for
every request on the same paper, so up to 99% of input tokens are read from the
provider's cache. The first batch runs alone to warm that cache before the
others start in parallel.

---

## Installation and configuration

Requires Python 3.10+.

```bash
./run.sh
```

This creates `.venv`, installs dependencies and generates `.env`. Add a key from
<https://openrouter.ai/keys> and run it again. For Docker, see
[DOCKER.md](DOCKER.md).

### Layout models (optional, recommended)

Without a layout model, Loupe uses PyMuPDF heuristics. With one, figures,
tables and display equations are detected far more reliably. Measured on a
36-page paper with 7 numbered equations:

| Backend | Equations cropped as images | Time |
|---|---|---|
| Heuristic only | 0 / 7 | 2.8 s |
| Docling | 7 / 7 | ~196 s |
| **MinerU** (layout stage only) | **7 / 7** | **6.2 s** |

```bash
.venv/bin/pip install "mineru[pipeline]"    # or: pip install docling
```

Loupe picks MinerU first when both are present. Force a choice with
`LAYOUT_BACKEND=mineru | docling | off`. A GPU helps but is not required.

### `.env`

| Variable | Purpose |
|---|---|
| `OPENROUTER_API_KEY` | **Required** |
| `OR_MODEL` | Translation model (default `~deepseek/deepseek-v4-flash-latest`) |
| `OR_MODEL_FAST` | Cheap model for text clean-up and light tasks (default `qwen/qwen3.7-flash`) |
| `SURVEY_MODEL`, `SURVEY_FAST_MODEL` | Corpus models; empty means use the two above |
| `SURVEY_BUDGET` | Spending cap per corpus question, in USD (default `0.50`) |
| `EMBED_BACKEND`, `RERANK_BACKEND` | `auto` or `off`; `off` falls back to BM25 only |
| `LAYOUT_BACKEND` | `mineru`, `docling` or `off` |
| `PAPER_DATA_DIR` | Where papers are stored (default `./data`) |

The model can also be changed in the interface — at import, on the review screen
(the estimate updates) and while reading. Switching mid-paper never re-translates
finished work.

| Model | Input / output per 1M tokens | Notes |
|---|---|---|
| `~deepseek/deepseek-v4-flash-latest` | $0.09 / $0.18 | Default, 1M context |
| `openai/gpt-5.6-luna` | $0.10 / $0.60 | Similar price, 1M context |
| `deepseek/deepseek-v4-pro` | $0.43 / $0.87 | Better, still cheap |
| `openai/gpt-5.6-terra` | $1 / $6 | 1M context |
| `anthropic/claude-sonnet-4.5` | $3 / $15 | Very fluent Vietnamese |

The leading `~` is part of the name: OpenRouter's self-updating alias.

---

## Limitations

- **Scanned PDFs need OCR first** (for example `ocrmypdf`).
- Tuned for one- and two-column computer-science papers. Posters, magazines and
  three-column layouts segment less reliably.
- Equations stay as text with sub- and superscripts (`x^{2}`, `d_{i}`); display
  equations are shown as images only when a layout model is installed. Inline
  formulas with fractions or sums read best in the original-PDF pane.
- Reference lists are deliberately **not** translated.
- Reasoning models (DeepSeek V4, GPT-5.x) are run with reasoning off or low;
  otherwise they can spend the whole budget thinking and return nothing.

When segmentation gets something wrong, the review screen is where you see it —
before paying to translate it.

---

## Project layout

```
server/
  main.py        HTTP API, server-sent events, exports (PDF/HTML/Markdown/PPTX)
  parser.py      PDF/text → blocks; reading order; figure and table crops
  layout.py      optional layout models (MinerU, Docling)
  pipeline.py    the reader's passes and the shared, cache-friendly context
  prompts.py     every reader prompt — where translation quality is decided
  llm.py         OpenRouter client: streaming, cache breakpoints, sticky sessions
  depth.py       checks that catch empty, generic or circular explanations
  pptx_out.py    PowerPoint export with native, editable diagrams
  db.py, store.py  SQLite storage; images and source PDFs stay on disk
  survey/        the corpus tool — separate pipeline, shared infrastructure
    ingest.py  search.py  agent.py  verify.py  synth.py  lecture.py  …
  survey_api.py  corpus routes, mounted into the same app
web/             front end, no framework (app.js = reader, survey.js = corpus)
docs/            landing page + Vietnamese user guide (GitHub Pages; the app serves it at /gioi-thieu/)
tests/           268 tests; no network, no model calls, never touch ./data
```

To change translation quality, edit `server/prompts.py` (reader) or
`server/survey/prompts.py` (corpus). The rest is plumbing.

---

## Development

```bash
.venv/bin/python -m pytest                           # full suite, ~3 minutes
.venv/bin/python -m pytest tests/test_unit.py -q     # pure logic, ~2 seconds
.venv/bin/python -m pytest tests/test_survey.py -q   # corpus, ~2 seconds
node --check web/app.js web/survey.js
```

Tests run against a temporary `PAPER_DATA_DIR`, replace every model call with a
fake, and therefore cost nothing. The corpus tests run with `EMBED_BACKEND=off`
because the BM25-only path is what a machine without a GPU uses.

Source comments, prompts and interface text are in **Vietnamese** — that is who
the tool is for. [`CLAUDE.md`](CLAUDE.md) documents the architecture and the
traps already found, also in Vietnamese; read it before changing the parser,
the prompt prefix or the translation protocol.
