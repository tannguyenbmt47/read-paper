# Changelog

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
