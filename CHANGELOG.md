# Changelog

## 1.17.1

**Thirteen highlights on a 149-paragraph paper was too few, and the reasoning
behind that number was a misreading.** Dunlosky et al. (2013) rate highlighting
low utility because students highlight passively and highlight *too much*; the
level that measured as effective in that same work is one or two sentences per
paragraph, and painting whole pages is the failure. The first version collapsed
those two into "less is better" and set a flat cap of eighteen — about one mark
every eleven paragraphs, roughly ten times sparser than the effective end.

The budget now scales with the paper and the density is the reader's to choose:
sparse (one per ten paragraphs), medium (one per four, the default), or dense
(one per two). Across the six papers on disk that comes to 6–14, 15–37 and 30–74
marks. Measured on the 149-paragraph paper, medium returns 30 marks over 23
distinct blocks for $0.077, against 13 before.

Three things that only showed up once the marks got denser. Pressing the button
twice used to duplicate every mark; auto-placed highlights now carry a flag and a
second run replaces only those, leaving anything highlighted by hand untouched.
Two quotes overlapping inside one block would have nested `<mark>` elements and
rendered wrong — at medium density five blocks already carry two or more marks,
so the pass now drops the later of any overlapping pair. And the output ceiling
had to scale too: at roughly 90 tokens per mark, dense on a long paper needs
~6,700, where the old fixed 6,000 would have truncated the JSON and lost the
whole paid-for call.

A rounding bug the tests caught: per-kind caps summed to 73 against a total of 74,
so the budget was unreachable by one. The shortfall now goes to the largest share.

## 1.17.0

**The reader can now have the paper's key sentences marked for it.** A new button
reads back the finished translation and highlights the sentences worth
remembering, each with one line saying *why* it matters. The five kinds map onto
the five highlight colours that already existed: claim, mechanism, evidence,
limitation, key term.

The cap of eighteen per paper is deliberate and evidence-backed. Dunlosky et al.
(2013) rate highlighting *low utility*, but the reason is that students highlight
passively and highlight too much; those who mark only one or two sentences per
paragraph do substantially better than those who paint whole pages. The value is
in being sparse and in writing down why — so the `why` field is the point, and
the prompt requires it to say something the quoted sentence does not.

The pass deliberately skips `cached_prefix`. That prefix holds the *original* full
text, while the user message is the *translation* of the same paper — sending both
sends the paper twice, and the prefix is cold anyway because this button gets
pressed long after translating. Dropping it and keeping only the brief and
glossary took the call from 39,477 to 17,867 tokens and from $0.102 to $0.021,
with no loss in what came back.

The server does not write the highlights and does not compute character offsets.
Highlights anchor to the *rendered* text of a cell, and `sci()` turns `^{N}` into
`<sup>N</sup>` — 71 stored characters render as 54. Computing offsets server-side
would mean porting `sci()` to Python, a second implementation of the same
rendering. Instead the server returns the quote as stored and the browser runs the
same `sci()` over it to find the range, which holds even when a sentence spans a
superscript.

**A JSON bug this uncovered affects every pass that returns JSON.** When a model
quotes a sentence containing LaTeX verbatim, `\(`, `\tilde` and `\{` are not
valid JSON escapes, so `json.loads` raises `Invalid \escape` and the whole
paid-for call is lost. `extract_json` now repairs those backslashes and retries.
It failed per-paper — papers where the model happened not to quote a backslash
worked fine — which is exactly the kind of bug that looks fixed when it is not.

## 1.16.1

**The Docker image can now carry the layout model.** `WITH_LAYOUT` takes
`0 | 1|mineru | docling | all`, where `1` installs MinerU alone — the faster and
more accurate of the two — and `TORCH_CPU=1` drops the CUDA libraries for an
image about 2.7 GB smaller, at a measured cost of 14 seconds per paper. The
image goes from 387 MB to 6.8 GB with GPU support. Model weights live in a
mounted volume rather than baked into the image, so rebuilding does not
re-download them.

Getting there turned up four traps, and every one of them failed silently: the
container returned HTTP 200 and "done" in a second or two, with the formulas gone
and nothing but a server log line to say why. MinerU pulls `opencv-python`, which
links against X libraries a headless image does not have; it also imports `six`
without declaring it, which only shows up in a slim container because the dev
machine gets `six` for free from docling. Snap's docker refuses the `--gpus`
flag outright and requires `runtime: nvidia`. And `~` in a compose volume does
not expand to your home directory under snap confinement — it becomes
`~/snap/docker/<rev>/...`, so the container mounts an empty directory while
`docker compose config` still looks correct.

A fifth trap was not Docker's: `parse_cache` is keyed on the file's SHA, so
re-uploading the same PDF to check the fix returned the cached heuristic parse.
Two consecutive measurements gave the same wrong answer for entirely different
reasons.

Because all of them fail quietly, the **ingest** path now pushes the fallback
reason through its progress stream instead of only logging it. `reparse` has
reported this since it was written; ingest had not.

## 1.16.0

**Display equations were being lost entirely, and now they are not.** Checking a
freshly loaded paper (arXiv:2602.15922, 36 pages) turned up six numbered display
equations in the PDF and **zero** of them extracted as images: the five blocks
labelled `equation` were mostly algorithm pseudocode, the formula between two
paragraphs on page 6 had vanished outright, and one paragraph began with the
shattered tail `[︃_{tk}]; C^{k}, c, q^{k}, t^{k}) − v^{k} 2 ]︃` glued onto the
prose. The cause was the layout model being off, which is the documented slim-
Docker default.

MinerU's PP-DocLayoutV2 is now a second layout backend, selected with
`LAYOUT_BACKEND=mineru`. On that paper it finds all seven display formulas and
crops every one, in **6.2 seconds** — about thirty times faster than docling,
because only the layout stage runs: no OCR, no table reconstruction, no LaTeX
recognition. It also separates `display_formula` from `inline_formula`, which
docling merges, and leads OmniDocBench v1.5 on formula recognition (CDM 88.46%).

Four things the integration has to get right, each found by measuring rather than
reading. Coordinates come back in pixels of the rendered page and must be scaled
to points. `inline_formula` must never become a block — it is a sub-region inside
a line of text, and admitting it would let `assign_spans` pull every mid-sentence
symbol out of its paragraph; page 6 alone has thirteen. Equation numbers are
merged into the formula on their row, since dropping them leaves the "(3)" glyphs
belonging to no box, to be recovered later as a text block containing only "(3)".
And sub-panels have to be merged: the model detects individual panels, not whole
figures, so the banner figure came back as twenty-eight regions and the caption
paired with one panel — the narrowest crop was 445px, and is now 1062px. Merging
stops at a caption, because a caption between two panels means two figures.

GPU is a minor factor here: 21.2s versus 35.2s on CPU, peaking at 669 MB. Worth
using, not worth chasing.

**A colon before a formula still introduces it.** `mark_continuations` skipped the
"…defined as:" → formula → "where…" pattern because `_SENT_END` counts a colon as
the end of a sentence. That is right for `_stitch_runon`, which actually joins the
text, but this function only sets a display flag. All three candidates in the
paper were being rejected for that reason.

## 1.15.0

**Inline LaTeX showed up raw in the middle of Vietnamese sentences.** The storage
form for maths in this tool is `^{…}` / `_{…}`, and `sci()` renders that, but the
model also writes `\(…\)` — and nothing rendered those, so a paragraph read
`\(Suf(a) \in \{0, 1\}\)` where it should have read `Suf(a) ∈ {0, 1}`.
Measured across the whole database: 13 cells, all of them `\(…\)` — no `$…$`, no
`\[`, no `\begin{}` — using `\in \tau \tilde \hat \cdot \theta \star
\rightarrow \xi \pi \mid`.

Fixed at both ends, because either alone is insufficient. `TRANSLATE_TASK` and the
explanation-column prompt now forbid LaTeX outright and give the substitution
explicitly, which stops new translations producing it; and `mathTeX()` renders
what is already stored, which repairs the existing 13 cells for free rather than
charging to retranslate them. The prohibition lives in the `*_TASK` strings, not
in `TRANSLATION_RULES`, so `cached_prefix` stays byte-identical and no already-
translated paper loses its cache.

The macro table exists in two languages — `TEX` in `app.js` for the screen, `_TEX`
in `main.py` for exports — and
`test_bang_macro_tex_khop_nhau_giua_app_va_export` holds them to the same keys and
values, the same way the slide renderers are held together. A drift there would
only surface when someone opened a downloaded file.

Two traps inside the renderer itself. Nested indices need the brace rules applied
repeatedly, innermost first: `[^{}]*` only matches the innermost layer, so a
single pass over `a^{(g_{DOC})}` consumes `_{DOC}` and leaves the outer braces
sitting in the sentence. And the result is not wrapped in `<code>` — a grey
monospace block chops the sentence up, when the symbol is part of that sentence;
`.imath` italicises the letters the way maths is set, and returns `sub`/`sup` to
upright.

## 1.14.0

**A block whose translation came back as garbage could only be retyped by hand.**
The script-leak guard tells the reader to "fix it with ✎ or retranslate that
block", but the second option did not exist — the only real alternative was
retranslating the whole batch. Found on CIRAG: block `b41`, in the explanation
column, read `либо thiếu thông tin để suy luận, либо nhận quá nhiều nhiễu`, with
Cyrillic standing in for the Vietnamese "hoặc".

The new ↻ button on each paragraph retranslates just that block. It drops the
block's translation-memory entry *before* calling the model — memory answers
first, so without that step the user would pay for a turn that hands back exactly
the text they clicked to replace — and it runs hotter than the first pass (0.4,
then 0.6 on the retry), since the same prompt at the same temperature tends to
produce the same output. Highlights on the block are cleared, as `_forget` does,
because their character offsets no longer match. Verified on the real block: the
Cyrillic is gone, nothing flagged, $0.0266 for both columns with a cold prefix —
which is what the button now says, rather than a cheerier number.

**The guard was also crying wolf over real mathematics.** `_OK_SCRIPT` covered
math symbols only up to U+23FF, so `⟨⟩` — the angle brackets used for inner
products and sequences, present in SONIC — counted as a foreign script. A false
positive costs money here: a clean translation is withheld from the memory, so
every later paper containing that paragraph pays to translate it again. Three
math ranges are now allowed. Cyrillic and Limbu are still caught, because the
list names what is permitted rather than what is banned.

## 1.13.1

**The PDF pane is now drag-resizable.** It was fixed at 40% of the window, which
is the wrong width for a two-column paper — zooming inside the pane only shows a
fragment, and comparing against the original is the whole reason the pane exists.
The same lesson the figure-preview window already learned with its ⤢ button:
magnifying the picture does not substitute for enlarging the frame.

Drag the left edge, double-click it to return to the default, or move it with the
arrow keys when it has focus. The width lives in `localStorage` under
`docdoc:pdfw`, since it belongs to the machine you are sitting at rather than to
the paper.

Four things measured in the browser rather than guessed. The width travels through
a `--pdf-w` custom property instead of an inline style, because on narrow screens
the pane becomes `position: fixed; width: auto` to cover the viewport, and a media
query can outrank the base rule but not an inline style. The clamp subtracts the
left sidebar, not just the viewport: computing it from window width alone gave a
1,080px ceiling on a 1,440px window, and dragging that far left the text column at
51 pixels — one word per line. The stored width is re-clamped when the pane opens,
not only when the handler is wired, because at wiring time the pane is still
hidden and the row it measures does not include it: 720px slipped through where
only 698 fit. And shrinking the window re-clamps from the *stored* value rather
than the current width, or every shrink would shave a little off and widening the
window again would never bring the pane back.

## 1.13.0

**Figures extracted before the resolution fix stayed blurry, and there was no
way to redo them short of reparsing the whole paper.** `dpi_for` aims for 1,600
pixels across, but papers loaded before it cropped at a fixed DPI, so a narrow
region came out a few hundred pixels wide — and reading a number off a chart is
the whole reason anyone zooms. `parse_cache` is keyed on the PDF's SHA, so
reloading the same file skips the crop step entirely.

`POST /api/doc/{id}/recrop` re-renders every figure from the stored
`figure_page` and `figure_rect`. The block list is not rebuilt, so translations,
notes, highlights and slide provenance are untouched; this matters because
`reparse` carries the risk of falling back to the heuristic parser, which on
SONIC took eight formula images down to three and then to none. Measured across
five papers: CIRAG 514 → 1,120 px average width, Theia 796 → 1,504, SONIC
1,091 → 1,601, every image sharper.

It also repairs figures whose file had gone missing. Block ids drift across
reparses while the PNGs on disk keep their old names, leaving a block pointing at
a file that does not exist and the reader showing an empty box — seven such
blocks on CIRAG. Re-rendering from the stored rect makes the files agree with the
blocks again, and `figure` is reset to the block id.

The button sends the images' URLs a fresh `?v=` stamp afterwards. Without it the
browser keeps serving the old picture from cache under the same URL while every
server-side measurement reports success — the same trap already documented for
CSS and JS.

## 1.12.3

**The corpus digest threw away 18% of what the cards had paid to extract.**
`corpus_digest()` never carried `problem`, `novelty`, `contribution_type` or
`domain`, and truncated `results` to four. Measured on the real corpus: 1,238
characters dropped out of 6,855, and three of four cards lost a result to the
cut. The dropped fields are exactly the ones the synthesis prompt needs — it
asks the model to sort the papers into directions and say what is genuinely new,
while `novelty`, the field that asks that question outright, and
`contribution_type`, the label that sorts them, were never shown to it. The
synthesis read as generic because it was working from less than the tool had
already bought, not because the prompt was weak. Only `code_url` stays out, on
purpose: a synthesis has no use for a repository link and will copy it into the
answer if given one.

`prompts.DIGEST_V` now feeds `corpus_fingerprint`, so a change to the digest's
shape marks existing syntheses stale and misses `qcache` once, instead of
letting work built from the thinner digest keep looking current.

`test_digest_mang_du_moi_truong_phieu_da_boc` reads the JSON template out of
`CARD_SYSTEM` itself and checks every top-level key reaches the digest, so
adding a field to the card and forgetting the digest fails the suite rather
than quietly repeating this.

## 1.12.2

**Answers containing a table showed the raw pipes.** The reader has its own
Markdown renderer, `renderMd()` in `app.js`, and it never had a table branch —
so an answer comparing three papers arrived as `| Thí nghiệm | Input | Output |`
followed by `|---|---|---|`, exactly the shape the prompt asks the model to use
whenever it compares three or more sources. The corpus side, `svMd()` in
`survey.js`, had rendered tables correctly since it was written; the reader was
the copy that fell behind. Tables now scroll horizontally inside their own box
rather than widening the chat bubble, and the header row is set off by a heavier
rule instead of a fill — the bubble is already `--surface-2`, so filling the
header with the same token made it vanish into the background.

**Subscripts written without braces stayed as underscores.** The parser stores
`x_{t}`, and both renderers handled that; but the model writes `x_t`, `z_t`,
`X_t+H` in prose, which matched nothing. The new rule is deliberately narrow —
a single-letter base and a subscript of one or two characters — because
`snake_case` looks identical: widening it would turn `source_block_ids`,
`paper_id` and `t_max` into formulas. `svMd()` did not render the stored
`^{…}` / `_{…}` form at all, so quoted passages showed `d_{i}`; it does now.

`test_hai_bo_dung_markdown_deu_dung_bang_va_chi_so` holds both renderers to the
same floor, and guards the fact that `survey.js` borrows `_SUBSCRIPTISH` from
`app.js`: deleting the declaration would throw at render time and leave every
corpus answer blank with no error on screen.

## 1.12.1

**Asking a question about the paper was completely broken.** `paint()` was
defined outside the `try` block while `answer` was declared with `let` inside
it — different scopes — so every animation frame threw
`ReferenceError: answer is not defined` and the panel showed only
"Lỗi: answer is not defined". The variable now lives in the same scope as the
function that reads it.

**Explaining a block took 145 seconds; it now takes 14.** Measured on one call:
`{"effort":"low"}` spent almost all of that time thinking rather than writing,
for 1,383 tokens of output. Turning reasoning off gives the same cost and the
same note — all eight fields, a concrete example drawn from the paper, a valid
diagram — in a tenth of the time. The depth comes from the required structure of
the prompt, where each field asks one specific question, not from thinking
tokens. This is the trap already documented for the lecture pass, where the same
setting produced empty strings after 76 seconds. The output ceiling also came
down from 8,000 to 2,500, since real output is under 1,400.

## 1.12.0

Two more audits — how well the tool actually teaches, and the interface as a
design rather than as a list of bugs. What each turned up, and what is fixed:

**The cost estimate was wrong by five to nine times, always cheap.** The whole
reason for splitting review from translation is knowing the price before
spending anything, and that number rested on two bad assumptions: output was
counted for the Vietnamese column only, though the reader generates an
explanation column by default and that column runs longer; and input assumed
each batch re-sends a tenth of the context when in fact every batch re-sends the
whole cached prefix. Both are fixed, the estimate now follows the column
checkboxes, and it reports a range with its scope stated rather than a single
number implying precision it does not have.

**The "walks a mechanism" check was scoring the opposite of what it meant.** It
counted any word from the broad causal list, which includes `khi`, `nếu` and
`trong khi` — function words present in nearly every Vietnamese sentence. On a
real deck the two slides that genuinely describe a mechanism failed while an
ablation slide with no mechanism passed. There is now a strict list for this
one purpose, keeping real connectives like `nên` and dropping the temporal and
conditional ones.

**`falsify` was generated, guarded, and never shown.** "What observation would
prove this approach wrong" is the most Feynman field in the whole synthesis: the
model wrote it, `check` complained when it was missing, and it appeared in
neither the interface nor the Markdown export — paying for the tokens and hiding
the result, while the warning pointed at a field nobody could see to fix.

**One lecture carried 109 warnings, 106 of them the same kind.** Grouping now
happens in the data rather than only in the interface, since those entries were
written to the database and travelled with the lecture. That paper drops to 11,
and the three real warnings are visible instead of buried.

**Exported lectures printed each answer directly under its question**, undoing
the one place in the tool with genuine retrieval practice — the in-app view
folds answers away deliberately. Answers now sit at the end.

Interface: the reading toolbar wrapped nothing, so at 1024px the export button
left the screen and at 768px the translate button — the tool's primary action —
sat outside the viewport entirely; the muted text colour measured 3.51:1 against
the page, below the 4.5:1 threshold, and it is the colour of nearly every
explanatory line in the app; per-block buttons were 20px and invisible without
hover, so on a touch device the explain and edit buttons could not be reached at
all. And **Move to another corpus** was hidden whenever only one corpus existed
— exactly when a paper has just been loaded into the wrong one; it is always
offered now, and the destination picker can create the corpus.

## 1.11.1

**Dragging to select one column no longer picks up the others.** The bilingual
grid is laid out by rows, so the DOM order is `en, vi, gl, en, vi, gl…` and the
browser sweeps a selection in that order rather than down the visible column.
Selecting three rows of the translation therefore swept in the English and the
explanation of every row in between, and copying produced three languages
interleaved.

No CSS property constrains a selection to a column, but text inside
`user-select: none` is skipped by the sweep — so the other two columns are
disabled the moment a drag starts, and re-enabled when the next drag starts
somewhere else. Measured on a real paper: 2,733 characters selected before,
2,703 after, with the other columns gone and the chosen column intact.

## 1.11.0

Four parallel audits — synthesis, cost, frontend, data integrity — each asked
for evidence rather than suspicion. What they found, and what is fixed:

**Two features were simply dead.** Clicking a past question in the corpus
history did nothing: the click handler was assigned inside a render function
that runs on every reload, silently overwriting the one bound at startup. And
the delete button beside each question never worked, because the attribute was
written `data-dropRun` while HTML lower-cases attribute names, so the lookup
read `undefined` and the request went to `/run/undefined`. The selector
`[data-dropRun]` still matched — which is why the confirmation dialog appeared
and the failure looked like a server problem.

**Editing a translation twice overwrote the second edit with the first.** The
save handler was attached to the cell, which survives the whole reading session,
so every re-open stacked another handler holding a stale textarea. The second
save fired both and the older value won the race. Two related bugs in the same
place: the list marker vanished from an edited item, and closing the find bar
restored its saved copy of the cell over the edit.

**Stale content stayed on screen after switching.** Changing corpus left the
previous corpus's synthesis, answers, lecture and graph in place — everything
that *changes* changed correctly, so the wrong thing was the thing that did not.
Opening a different paper left the previous paper's chat log visible.

**Four routes never checked that the paper belongs to the corpus in the URL.**
Deleting worked across corpora and returned the current corpus's stats, so
nothing looked wrong; `enrich` was worse, writing a paper's entity graph into a
corpus that does not contain it. All six paper routes now share one check.

**Changing a title updated the index in a second transaction.** If that second
statement failed — a concurrent SSE stream holding the write lock is exactly the
case already seen — the text would be new while the index still held the old
tokens, which is the silent corruption path that damaged the database once
before. Both statements are now one transaction.

**Synthesis checks had three holes.** Paper labels in `framings` and
`tensions[].sides` were never validated, so red chips appeared while the warning
count said zero; a label-mapping helper read `x if x in real else x`, two
identical branches pretending to be a check; and citations were resolved without
filtering by corpus, so a citation could point into another corpus and then be
used as the source of truth for number checking.

**Money.** The alias prefix used throughout the app (`~anthropic/…`) failed a
`startswith` test, so those models silently lost their cache markers. The
context pass re-sent every passage in the user message although the full text
was already in the cached prefix — about 17k wasted tokens per paper. Batches
were sized at 4,500 characters while using only 7–15% of the output ceiling;
at 12,000 a paper drops from 15–16 batches to 6–7, cutting input by a quarter
with a warm cache and far more without one.

Also: figure crops are rendered to a pixel target rather than a fixed dpi, so
zooming has real detail to show; the lecture prompt no longer contains a real
passage id as its example, which the model had been copying verbatim into every
section; the lecture's source-side number extraction now matches the deliberate
asymmetry documented for the answer checker; deleting a corpus clears its cached
questions; and the three-column reading layout no longer overflows on a long
URL.

## 1.10.0

**Re-parsing says when it fell back to the weaker extractor.** Without the
layout model the route quietly used the rule-based path and still reported
success, while the result was much worse — on one paper 8 equations with images
became 3 with none, and an equation without its image renders as broken
mathematical text. Nothing in the interface hinted at why. The response now
carries `layout_used` and `fallback_why`, and the reader shows a dialog naming
the reason and the fix.

**Source is mounted into the container, so UI changes no longer need a
rebuild.** The image bakes `web/` and `server/`, which meant every frontend
change required `docker compose build` — and forgetting it showed the old
version, which cost most of a day. Editing `web/` now needs only a refresh, and
`server/` only a restart. The trade-off is that on-disk code shadows the image's
copy, so moving the image to a machine without the source runs the baked
version; drop the two lines if that ever matters.

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
