# Changelog

## 1.7.0

**Turn one paper into a lecture you can actually read.** The corpus tool could
summarise everything and answer questions, but both assume you already know
what to ask — which is exactly what you don't when you open an unfamiliar paper.
The new tab writes the paper out in eight sections: what the paper *assumes you
already know* (first, because unstated background is what stops readers, not
long sentences), the problem through a concrete instance, why the obvious
approach fails, **the mechanism walked through one real input step by step with
why each step is needed**, how it sits against the papers it cites, what the
numbers do and don't show, what to doubt, and questions to test yourself on.
Measured on one paper: 5,750 words, 175 seconds, **$0.0099**.

**Comparing against cited work costs nothing, because the authors already wrote
the comparison.** The obvious approach — fetch thirty referenced papers and have
the model read them — costs dozens of times more and is *worse*: reading the
cited paper leaves the model guessing which idea was borrowed. The sentence
around a citation says exactly that, in the author's own words. Semantic
Scholar serves those sentences free and without a key, along with a
ready-made one-line summary of each cited paper. On one paper: 63 references, 58
with citation sentences, retrieved in two HTTP calls for **$0**.

**The depth guard now drives a rewrite, not just a warning.** Sections flagged as
shallow are rewritten once with the specific sentence that failed attached —
telling a model to "go deeper" makes it write *longer*, naming the failing
sentence makes it write *deeper*. Fabricated numbers and bad passage ids are
deliberately excluded from that feedback: rewriting cannot fix them, and
including them only dilutes the complaint.

**Three failures found by running it rather than testing it.** Leaving reasoning
on made two batches out of four burn 76 seconds and return an *empty string* —
the token budget went to thinking before any writing happened. A whole paragraph
set in bold or in the heading colour leaves the eye nowhere to land, so emphasis
is now applied only to short leads. And the monospace font cannot compose
Vietnamese stacked diacritics — "số" rendered as "sô´" — so symbol notes are set
in the normal face.

## 1.6.3

**The label protocol no longer breaks when the model types a label slightly
differently.** Translated text comes back as flat prose with each block marked
`<<<b12>>>`, and the parser matched that syntax exactly. Two deviations seen in
real output — `<<<b4_g>>` (one bracket short) and `### b9_g` (a Markdown
heading) — matched nothing, so everything after them was appended to the
*previous* block. One explanation cell had grown to **20,052 characters** holding
a dozen blocks' worth of text, with 48 `###` labels showing as visible litter in
the reading column; sixteen more cells had a translation and its explanation
fused together.

The fix is to stop guessing the syntax. `stream_chunk` already knows which block
ids are in the batch, so the pattern is built from that set: a label is any line
that is *only* a known id, however the model chose to decorate it. Two
constraints hold it in place — a label must be alone on its line, or a sentence
mentioning `[b12]` would split the document, and a label found inside body text
marks the block dirty so it is never written to the translation memory. Existing
documents were repaired in place by re-parsing the damaged cells: 25 collapsed
blocks recovered, no model call.

## 1.6.2

**Fix a translation in place, while reading.** Every block gets a ✎ button that
opens the stored raw text — not the rendered HTML, which already carries
`<sup>`, `<sub>` and figure-reference anchors; editing that would nest a fresh
layer of tags on every save and destroy the `^{…}` markers. The correction is
written to the translation memory too, so it follows the paragraph rather than
the document: the same text in another paper, or in this one after a re-parse,
comes back corrected.

**A script-leak guard on the translation pass, which had none.** A translation
came back reading `띠ᥕᥕᥲᥕᥱ` where it should have said "preserved". `cjk_leak`
only knows CJK and Hangul; those characters are Limbu. Enumerating forbidden
scripts is an endless chase, so `script_leak()` enumerates the *permitted* ones
— Latin, Vietnamese diacritics, Greek, maths, sub/superscripts — and flags
anything else the source does not contain. The place this must be enforced is
the **translation memory**, not the document: garbage in the document is visible
and fixable, garbage in the memory returns forever, silently and for free.
Scanning real data found 4 poisoned memory entries (Cyrillic, Devanagari,
Armenian) and 2 affected blocks.

**Re-parse a paper without losing the translation.** `POST …/reparse` re-runs
the extractor and merges by **content, not position**: a new block whose text
matches an old one reclaims the old id, so translations, notes, highlights and
slide sources still point where they should. Matching by position would shift
every id after an inserted block and paste translations onto the wrong
paragraph — worse than losing them, because it still looks right. Blocks with
identical text match by order of appearance; the first version matched one-to-one
and re-minted 12 ids on every run.

**Adjust a crop while reading.** The ✂ editor was only reachable from the review
screen, so a badly cropped equation encountered mid-read meant leaving, finding
the block, fixing it and starting over. It now opens from the reader. Separately,
crop rectangles are widened so they never cut a glyph in half — an equation box
built from its own spans was rendering "ere at step t…" instead of "where…".

## 1.6.1

**Text the layout model missed is no longer discarded.** `assign_spans` places
each span in the smallest box containing its centre; spans outside every box
were silently dropped. When docling misses a text region — common for a
paragraph spanning a column break — the whole paragraph vanished from the
document with no error. Measured on one paper: **20.4% of spans fell outside
every box**, and a reader saw a paragraph stop mid-sentence at the word
"Current" and jump to an unrelated point. Leftover spans are now grouped into
paragraphs and fed back into the normal pipeline, filtered against figure
regions and body font size. Word retention: **82.5% → 89.7%**.

The fix carried its own trap: columns must be separated *before* lines are
built. `_rows()` groups by baseline across the whole page, so in a two-column
paper a left-hand line and a right-hand line at the same height became one line
and the two columns interleaved — *"…static evidence repre- summarized as
follows: sentation, failing…"*. That is what the first version produced.

## 1.6.0

**A synthesis view: read the corpus, not just query it.** Question answering
assumes you already know what to ask, which is exactly what you don't when
entering a field. The new first tab reads every paper's card and builds the
shared argument: the problem and the competing ways of framing it, the
approaches grouped **by mechanism**, what each one *bets on*, who builds on
whom, what is genuinely new in each paper versus assembled from existing parts,
and where papers contradict each other. Lineage is computed from the entity
graph rather than asked of the model, so every link carries the passage it was
read from.

**A depth guard, applied to synthesis, answers and slides alike.** The existing
guards catch fabrication; none caught the more common failure — a sentence that
is true, properly cited, and carries no information. *"CIRAG uses a
construction-integration mechanism to improve retrieval quality"* is not wrong,
and replacing the method name with a nonsense word leaves it just as "true".
`server/depth.py` encodes that test and checks four things mechanically: empty
stock phrases, "improves/enhances" with no stated mechanism, long sentences with
neither causality nor numbers, and circular definitions. Calibrated against
shallow and deep examples: 3/3 caught, 0/4 false positives.

**Slides must now walk one mechanism end to end.** Decks about a method
routinely tell you the problem and the results while the middle — how it
actually runs — collapses into a name and a three-box diagram. The outline step
now requires at least one slide that takes a concrete input from the paper,
steps through it, and says at each step why that step is needed;
`check_depth()` flags a deck that has none.

**Two real bugs, both found by running the thing.** Paper ids (`p50d58cb2d3`)
differ from passage ids (`p50d58cb2d3c14`) only by a suffix, and the model kept
conflating them and emitting ids that do not exist — six of nine warnings on one
real synthesis. Papers are now labelled `P1`, `P2` in prompts and mapped back
afterwards. Separately, the number check flagged *"100 million frames"* as
fabricated because the source wrote `100M` and the strict number pattern rejects
digits followed by a letter; source-side extraction is now deliberately more
permissive than answer-side.

**Model selection in the interface, per corpus**, with the active model shown
wherever money is spent — including which one, and whether it came from the
corpus setting or `.env`. Static assets are fingerprinted so a stale stylesheet
can no longer survive an update.

## 1.5.0

**A second mechanism: the survey corpus.** The reader puts one paper's full text
into the system prompt, which is what makes close reading work and what makes
*"how do these approaches differ?"* unanswerable — that needs thirty papers. The
corpus tool indexes many papers instead of translating them, and answers
questions with citations down to the passage. It shares `parser.py`, `llm.py` and
the SQLite file with the reader, and changes not one line of the reader's
pipeline.

**Retrieval is hybrid, and each stage earns its place.** BM25 (SQLite FTS5) and
BGE-M3 dense vectors are fused with Reciprocal Rank Fusion, which reads only
ranks and therefore needs no score normalisation — and lets the dense retriever
be disabled without branching the code. Embeddings and cross-encoder reranking
run on your own GPU, so retrieval quality costs nothing per query. Measured on
three real papers: Vietnamese questions retrieve the right English passages in
9–71 ms, where BM25 alone returns noise.

**Each paper gets a RAPTOR tree, and queries hit every level at once.** Passages
are clustered and summarised recursively; leaves and summaries share one index,
because a question often needs a number from a leaf and framing from a higher
level in the same breath.

**An entity graph links papers.** Extracted once per paper with no community
summaries — the part that makes full GraphRAG prohibitive. It expands results
after retrieval rather than replacing it, since plain vector search wins on
single-fact lookup and graphs win on multi-hop.

**The deep-dive loop is bounded and says what it could not find.** It plans a
checklist, searches, reads, and searches again only for the items still missing
evidence — at most five rounds, budget checked before every model call. Gaps are
stated as gaps. Fluent prose covering a gap is the failure that makes a research
tool actively harmful, so there is a check that catches it.

**Answers are verified mechanically.** Every number must appear verbatim in a
cited passage, every citation must be a passage actually retrieved that run, and
citations are clickable. An optional entailment pass catches the subtler case
where the citation is real but does not support the claim.

**Two bugs found and fixed while building it, both worth naming.** The FTS5
external-content index corrupted the database when a paper's title differed
between write and delete — fixed by making the indexed content a pure function
of one table row, so divergence became impossible rather than merely unlikely.
And bibliographies from papers whose "References" heading went undetected were
ranking *first* for content questions; they are now filtered per block, before
grouping, and in the oversized-block path that bypassed the first filter.

**Cost.** Parsing, chunking, indexing and embedding are free. Enrichment is about
$0.034 per paper, once; a three-round question about $0.03; repeating a question
on an unchanged corpus is free.

## 1.4.0

**Slide generation split into two steps.** Previously a single model call had to
decide the narrative *and* handle layout, icons, diagrams, and word budgets at
the same time; most of its attention went to the format, so the content came out
thin. Now step ① drafts an **outline** — one assertion per slide, plus the
evidence that supports it and the points to make — which you review and edit;
step ② renders the approved outline into slides in batches of four, so each
slide gets several times the output budget. Measured on one paper: 129 words per
slide (was 90), and slides carrying warnings dropped from 7/16 to 0/16.

**Four layout bugs that only a real browser could reveal.** `.vis` had no height
constraint in normal flow, so `figure`'s `flex: 1` and the SVG's
`max-height: 100%` both resolved against an auto-height parent — images rendered
at full size and Mermaid diagrams grew to 4000px. Ten of twenty slides
overflowed. Also fixed: diagrams drawn at their intrinsic size (a postage stamp
in a 1280px frame), the equation box ignoring the autofit loop, and the export
toolbar sticking to the top of the page where it covered each slide's headline.

**Evidence and cards no longer compete for the same space.** A slide with a
figure or diagram now gets at most two cards; a slide that needs three or four
cards gets no decorative diagram, because tinted cards with icon chips are
already a visual structure. A diagram sitting beside cards must be horizontal
(`flowchart LR`) — the space left for it is a wide, short strip. `check_slides`
warns on all three cases.

**Removed a dead `slideLayout()`.** There were two copies in `app.js`; the later
one shadowed the earlier, and the live one was missing a rule the server had, so
the in-app preview disagreed with the exported file.

## 1.3.0

- **Translate selected sections only.** The ☑ button lists the paper's sections
  with the number of blocks already translated and the cost of each. Batches that
  contain no selected block are never sent.
- **Appendix detection.** Appendices sit after the reference list, so they were
  being swallowed into `reference` — headings appeared with no body text.

## 1.2.0

- **Free layout per slide.** Enabling it on one slide converts every part to
  absolute positioning, **captured from where the elements currently sit**, so
  you keep dragging rather than starting over. Drag to move, eight handles to
  resize, arrow keys to nudge 0.5% (Shift for 2%), with edge snapping and
  alignment guides. Double-click to edit text, as in Google Slides. Other slides
  keep the automatic layout.
- **Test suite** — `pytest`. `tests/test_unit.py` covers pure logic (~3s);
  `tests/test_api.py` exercises the real API against a temporary database and
  never calls a model, so it costs nothing.
- **Overflow prevention rewritten.** The hand-written flexbox simulation was
  replaced by an autofit loop that runs in the browser — measure `scrollHeight`,
  shrink the font, measure again — which is PowerPoint's own `normAutofit
  fontScale` algorithm. `max()` floors keep small text readable.
- **Numbered sections** on eyebrow labels, matching the numbers on the agenda.
- **Image placeholders appear only when there is real room** for one.

## 1.1.0

- **Import progress.** A progress bar, the current step, and an elapsed-time
  counter. The layout model takes tens of seconds; before this the button simply
  sat still, so a slow run was indistinguishable from a hang.
- **Hide junk blocks while reading.** Stray axis labels lifted out of figures,
  running footers, and so on. The ⊘ button appears on every block. Hiding is not
  deleting: the translation is kept, the block can be restored, and hidden blocks
  are excluded from translation batches so the remaining work costs less.
- **Lazy loading on the slide screen.** Opening it used to download all 22 images
  (589 KB) purely to read their dimensions for layout selection. The server now
  computes aspect ratios with PIL (header read only) and returns them in a single
  293-byte response; images load when they are actually displayed. Measured in
  the browser: **22 image requests → 1**.
- **The slide screen is no longer gated.** A paper missing even one block used to
  disable the button with no explanation. The screen is always reachable now; the
  warning moved to where the money is actually spent, the *Build slides* button.
