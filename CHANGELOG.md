# Changelog

## 1.9.5

**Re-parsing now repairs a truncated title.** A title guessed from the top of
page one often loses its first line: one paper was stored as "Question
Answering", which is the tail of "Ground, Cover, and Refine: Evidence-Centric
Frame Selection for Long-Video Question Answering". Re-parsing extracts the
whole thing correctly but was throwing the new title away and replacing only the
blocks, so the paper kept the wrong name forever — and the name is not
cosmetic: it appears in the document list, at the head of every export, on the
title slide, and it is what Semantic Scholar is queried with in the corpus tool.

Only the truncation case is repaired: the stored title must be a strict
substring of the newly extracted one. A title you typed yourself is never
overwritten, since there is a rename button and clobbering a deliberate choice
would be worse than the bug being fixed.

## 1.9.4

**The figure preview window can be enlarged, not just the image inside it.**
Zooming reads one cell at a time, which is the wrong tool for a table: comparing
a row against a column is the reason for opening it, and that needs the whole
grid on screen at once. A ⤢ button expands the panel to 1200×880 and back, and
the panel is resizable by dragging its corner. It is anchored bottom-right, so
it grows left and up rather than off the screen.

## 1.9.3

**The figure preview can be panned and zoomed.** Clicking "Figure 3" in the body
opens a small window showing which image the reference points at — but figures
cut from a PDF are dense with small type (axis labels, legends, numbers inside
tables) and the window is only about 560px wide, so fitted to width they cannot
be read. Reading the number on the chart is the whole reason for clicking, so
the image now zooms on the scroll wheel, drags to pan, and double-clicks between
fit and 3×.

Position is driven by `transform` rather than scrollbars, because zooming has to
keep the point under the cursor stationary and that needs exact coordinates. The
image always keeps at least a quarter of itself inside the frame, so a hard drag
cannot fling it out of sight, and the zoom resets whenever a different figure is
opened.

## 1.9.2

**Highlights now cover the whole word and stand out from the page.** The mark
was drawn with `linear-gradient(transparent 55%, colour 55%)` — a thick
underline that tints only the lower 45% of the line, which is exactly what "the
highlight doesn't cover the text" and "the colour is too pale against the
background" describe. It is now a solid fill. The palette was measured as well:
the old colours reached only 1.13–1.38 contrast against white, close to
invisible; the new ones sit at 1.37–1.73 while text on top stays above 8.9,
where 4.5 is the threshold.

The colours had to be updated in all four theme blocks. Changing only `:root`
left the explicit light theme on the old values, so a browser check still
reported the original pale yellow after the "fix".

## 1.9.1

**A paragraph interrupted by a display equation now reads as one paragraph.**
The classic shape in a methods paper — "Let the timestamps be sorted as", then
the equation, then "where T_V is the video duration" — is one paragraph in
print. Here it was three blocks: three separate rows, the middle one with no
translation, the last starting with "where" and no visible link to anything.

They are still three blocks, deliberately. The equation is rendered as an image
that has to sit between the two halves, and each block has to stay its own unit
for translation, highlighting and notes. What changed is that the tail is marked
as a continuation, so the display layer drops the extra spacing and hides the
second "chưa dịch" placeholder — the first half already says it, and two grey
italic lines in a row make the passage look more broken, not less.

## 1.9.0

**Two filters that stop paying to translate rubbish.** Every block is one
translation call plus one explanation call, so a stray fragment costs twice for
something nobody reads.

*Paragraphs split mid-word by an intervening figure are now rejoined.* In a
two-column paper figures and tables float to the top of a column, so they land
in the middle of a sentence: one paper had six paragraphs ending `differ-`,
`compo-`, `sen-`, with the tail sitting after one or two captions. Each fragment
was translated on its own, and the model wrote into the explanation column that
*"the original sentence is cut off right after mentioning Table 3, so it does
not yet say which"* — paying twice for a translation that could not be right.
Joining requires both signals, a hyphen ending and a lower-case continuation,
and stops at a heading. Five of six joined; the sixth continues with a capital
and is deliberately left alone.

*Adjacent paragraphs split mid-sentence are joined too*, under a stricter rule
since there is no hyphen to go on: the two blocks must be adjacent, the first
must not end in punctuation, the second must start lower-case. Splits across a
display equation are deliberately left alone — the equation is rendered as an
image and has to stay between the two halves, so merging the text would push the
image below the whole paragraph and make the reading order worse, not better.

*The bibliography is no longer translated.* `parse_pdf` labels a reference
section when it finds the heading, but the layout-model path has no such step,
so on one paper the entire bibliography landed in the Conclusion section marked
for translation — **5,664 of 32,701 characters, 17% of the translation bill**,
spent on a list of citations. The detector is the one already tuned on real data
for the corpus tool: the required signal is a **publication venue**, not a
density of years or "et al.", since a sentence citing "(Lewis et al., 2020; Lin
et al., 2024; Ram et al., 2023)" has a higher year density than a real
bibliography. That paper now translates 26,382 characters instead of 32,701.

*Noise blocks no longer get translated.* Fragments under twelve characters,
blocks that are only digits and punctuation (`57.3%`, `(4) ...`), author emails,
ORCIDs, and affiliation or footnote lines are flagged as not-to-translate — 8 to
10 blocks per paper across three real papers. Flagged, not deleted: the boundary
of "rubbish" is never certain, a short numeric line can be a paper's headline
result, and the reader can switch any of them back on.

## 1.8.3

**A box edge clipping the first line lost the whole line.** Layout-model boxes
hug the text closely, so the top edge often falls inside the first line rather
than above it. On one paper the abstract's box began at y=249.4 while its first
line spanned 244.5–253.5 — a centre of 249.0, four tenths of a point too high —
so "Long-video question answering requires identifying sparse yet" fell outside
every box and vanished, even though the layout model had read it correctly.

Span assignment now takes a second pass: anything no box contains by centre is
assigned to the box it overlaps most, requiring at least a third of the span to
be inside. The first pass is untouched, so no correct assignment changes, and
spans that genuinely touch nothing still fall through to the existing recovery
path. Word retention on that paper: **68.1% → 79.2%**, 674 words recovered; two
other papers unchanged.

## 1.8.2

**Choosing a model did nothing.** The corpus PATCH route kept its own copy of
the editable-field list, and that copy was missing `model` and `fast_model`, so
the choice was dropped silently — no error, no warning, the update function
never saw it. The screen then reloaded, read back the old value, and the picker
snapped to "Theo .env (mặc định)". From the outside this is indistinguishable
from a dropdown that closes before you can pick, which is where the first
attempt at this bug went looking. There is now one field list, used by both, and
a test that fails if the route ever hand-copies it again.

## 1.8.1

**Model dropdowns opened and closed again before you could pick anything.** A
`<select>` nested inside a `<label>` gets the click twice: once directly, and
once forwarded by the label to its labelled control. Chromium opens the popup on
the first and closes it on the second. All eight selects in the app were built
that way.

What makes this one worth naming is that automated checking could not see it.
Dispatching synthetic `mousedown`/`click` at the select never travels through
the label, so a MutationObserver saw no rebuild, focus stayed put, and every
measurement said the control was fine. Only a real mouse press reproduces it.
Labels are now siblings linked by `for=`, which also gives every select a proper
accessible name, and a structural test keeps them unnested.

## 1.8.0

**Everything you create can now be edited and deleted, not just created and
read.** Most screens had only the first two: fine while testing, where
everything is new and correct, and a wall the moment something goes in wrong
and the only remedy is delete-and-redo — which costs money.

The worst case was a paper whose title had been extracted as just *"Question
Answering"*. A title is not only a label here: it goes into the full-text
index, into the corpus digest sent to the model, and it is what Semantic
Scholar is queried with. A wrong title broke all three, and nothing in the
interface could fix it. Papers now have an inline editor for title, year,
venue, authors and link; documents in the reader can be renamed.

Also added: delete a question from the history (which also drops the cache
entry pointing at it — otherwise asking again hits the cache, resolves a run
id that no longer exists, and shows a blank screen), discard a lecture or a
synthesis, and edit the comparison table's columns. Deletions that cost money
to rebuild state the price in the confirmation, because "are you sure?"
without a number gives you nothing to be sure with.

The line held throughout: **what you typed is editable, what a guarded pass
produced is not.** The paper editor silently ignores `card`, `status` and
`lecture` — hand-editing those would make the number binding and the depth
checks meaningless, the same reason `PATCH …/slides` refuses
`source_block_ids`.

## 1.7.2

**Move a paper to another corpus.** Loading a PDF into the wrong corpus is easy,
and the obvious remedy — delete it and load it again — throws away the expensive
part: the extracted card, the per-passage context sentences, the summary tree,
the vectors, the lecture. Re-enriching costs about $0.034 and several minutes;
moving keeps all of it and costs nothing.

Passages, vectors and the full-text index follow the paper on their own, because
they are keyed by paper id rather than by corpus. The entity graph does not:
`entity.id` is a hash of *(corpus, normalised name)*, so the same entity in two
corpora is two different ids. Skipping that would leave the paper in its new
corpus while its entities stayed behind — the new corpus's graph missing the
paper, the old one full of orphan nodes pointing at a paper no longer there. The
move re-keys entities, mentions and edges, and recounts both corpora. It refuses
when the destination already holds the same file, which caught a real duplicate
during testing.

## 1.7.1

**The number check was crying wolf.** One lecture produced 33 warnings, 32 of
them from the worked-example section — "suppose the video runs 600 seconds, take
frames 750 to 755" is not a claim about anyone's results, and a timestamp like
`[00:12:30-00:12:35]` was being split into six meaningless numbers. The
verbatim-number constraint now applies only to sections that assert something
about the paper, timestamps are stripped before extraction, and the interface
folds repeated warnings of the same kind into one expandable line. On the same
lecture: 33 warnings → 1, and that one is real. A guard that cries wolf gets
ignored, and the real warning goes with it.

**"No comparison dossier" now says which of the three reasons it was**: the
title was too mangled to look up (one paper had been reduced to "Question
Answering"), nothing matched, or — the common case for a fresh preprint —
Semantic Scholar has the paper but has not finished extracting its references
yet, so waiting is the answer rather than editing anything.

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
