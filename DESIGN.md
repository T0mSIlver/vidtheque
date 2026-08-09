---
name: vidtheque dashboard
description: The cutting room — a warm-paper operator surface where mono means the machine said it and sans means a human wrote it.
colors:
  bg: "#fbfaf9"
  fg: "#1b1a19"
  muted: "#6b6764"
  line: "#e3dfdb"
  accent: "#b45309"
  raised: "#ffffff"
  panel: "#f1f0ef"
  row-hover: "#f9f9f8"
  rule: "#cfceca"
  mark: "#8d8d86"
  accent-bg: "#fff7c2"
  accent-line: "#e9c162"
  accent-solid: "#ffc53d"
  tone-ok: "#2a7e3b"
  tone-ok-bg: "#f5fbf5"
  tone-ok-line: "#b2ddb5"
  tone-warn: "#8a5a00"
  tone-warn-bg: "#fefbe9"
  tone-warn-line: "#f3d673"
  tone-bad: "#ce2c31"
  tone-bad-bg: "#fff7f7"
  tone-bad-line: "#fdbdbe"
  tone-work: "#0d74ce"
  tone-work-bg: "#f4faff"
  tone-work-line: "#acd8fc"
  tone-wait: "#63635e"
  tone-wait-bg: "#f9f9f8"
  tone-wait-line: "#dad9d6"
  scrim: "rgb(0 0 0 / 0.66)"
typography:
  brand:
    fontFamily: "Instrument Serif, Georgia, Times New Roman, serif"
    fontSize: "1.25rem"
    fontWeight: 400
    lineHeight: 1.2
    letterSpacing: "-0.01em"
  display:
    fontFamily: "Inter Variable, -apple-system, BlinkMacSystemFont, Segoe UI, Roboto, Helvetica Neue, Arial, sans-serif"
    fontSize: "1.875rem"
    fontWeight: 590
    lineHeight: 1.2
    letterSpacing: "-0.02em"
  headline:
    fontFamily: "{typography.display.fontFamily}"
    fontSize: "1.25rem"
    fontWeight: 590
    lineHeight: 1.2
    letterSpacing: "-0.01em"
  title:
    fontFamily: "{typography.display.fontFamily}"
    fontSize: "0.9375rem"
    fontWeight: 590
    lineHeight: 1.35
    letterSpacing: "-0.01em"
  body:
    fontFamily: "{typography.display.fontFamily}"
    fontSize: "0.875rem"
    fontWeight: 400
    lineHeight: 1.5
    letterSpacing: "normal"
  cell:
    fontFamily: "{typography.display.fontFamily}"
    fontSize: "0.8125rem"
    fontWeight: 400
    lineHeight: 1.4
    letterSpacing: "normal"
  label:
    fontFamily: "{typography.display.fontFamily}"
    fontSize: "0.6875rem"
    fontWeight: 510
    lineHeight: 1.4
    letterSpacing: "0.07em"
  machine:
    fontFamily: "JetBrains Mono Variable, ui-monospace, SFMono-Regular, Menlo, Consolas, monospace"
    fontSize: "0.78125rem"
    fontWeight: 400
    lineHeight: 1.4
    letterSpacing: "normal"
    fontFeature: "tabular-nums; ligatures off"
  figure:
    fontFamily: "{typography.machine.fontFamily}"
    fontSize: "1.875rem"
    fontWeight: 510
    lineHeight: 1.2
    letterSpacing: "-0.02em"
    fontFeature: "tabular-nums; ligatures off"
rounded:
  sm: "3px"
  md: "5px"
  lg: "8px"
spacing:
  s1: "0.25rem"
  s2: "0.5rem"
  s3: "0.75rem"
  s4: "1rem"
  s5: "1.25rem"
  s6: "1.5rem"
  s8: "2rem"
  s10: "2.5rem"
  s12: "3rem"
components:
  button-primary:
    backgroundColor: "{colors.fg}"
    textColor: "{colors.bg}"
    rounded: "{rounded.sm}"
    padding: "0.25rem 0.75rem"
    typography: "{typography.cell}"
  button-primary-hover:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.bg}"
  button-ghost:
    backgroundColor: "transparent"
    textColor: "{colors.fg}"
    rounded: "{rounded.sm}"
    padding: "0.25rem 0.75rem"
  button-ghost-hover:
    textColor: "{colors.accent}"
  input-text:
    backgroundColor: "{colors.raised}"
    textColor: "{colors.fg}"
    rounded: "{rounded.sm}"
    padding: "0.25rem 0.5rem"
    typography: "{typography.cell}"
  pill:
    backgroundColor: "{colors.tone-neutral-bg}"
    textColor: "{colors.tone-wait}"
    rounded: "{rounded.sm}"
    padding: "1px 0.5rem"
    typography: "{typography.label}"
  chip:
    backgroundColor: "transparent"
    textColor: "{colors.muted}"
    rounded: "{rounded.sm}"
    padding: "2px 0.5rem"
  chip-hover:
    backgroundColor: "{colors.row-hover}"
    textColor: "{colors.fg}"
  table-head:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.muted}"
    typography: "{typography.label}"
    padding: "0.5rem"
  table-row:
    backgroundColor: "{colors.bg}"
    textColor: "{colors.fg}"
    typography: "{typography.cell}"
    padding: "0.5rem"
    height: "2.125rem"
  table-row-hover:
    backgroundColor: "{colors.row-hover}"
  nav-rail:
    backgroundColor: "{colors.panel}"
    width: "15rem"
  wordmark:
    textColor: "{colors.fg}"
    typography: "{typography.brand}"
  brandmark:
    backgroundColor: "{colors.accent}"
    textColor: "{colors.bg}"
    rounded: "{rounded.lg}"
  nav-group:
    textColor: "{colors.muted}"
    typography: "{typography.label}"
    padding: "0.75rem 1rem 0.25rem"
  nav-link:
    backgroundColor: "transparent"
    textColor: "{colors.muted}"
    typography: "{typography.cell}"
    padding: "0.5rem 1rem"
    height: "2.125rem"
  nav-link-hover:
    backgroundColor: "{colors.row-hover}"
    textColor: "{colors.fg}"
  nav-link-active:
    backgroundColor: "{colors.bg}"
    textColor: "{colors.accent}"
  ledger-figure:
    backgroundColor: "{colors.bg}"
    textColor: "{colors.fg}"
    typography: "{typography.figure}"
    padding: "1rem"
  empty-lead:
    textColor: "{colors.fg}"
    typography: "{typography.cell}"
  empty-note:
    textColor: "{colors.muted}"
    typography: "{typography.cell}"
  table-head-sorted:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.fg}"
    borderColor: "{colors.accent}"
    typography: "{typography.label}"
  live-badge:
    textColor: "{colors.tone-work}"
    typography: "{typography.label}"
  countdown:
    backgroundColor: "{colors.tone-warn-bg}"
    textColor: "{colors.tone-warn}"
    rounded: "{rounded.sm}"
    padding: "0.25rem 0.5rem"
    typography: "{typography.machine}"
---

# Design System: vidtheque dashboard

Normative for `/dashboard` (impeccable mode **Operate**). The public demo at `/`
(mode **Persuade**) shares only the six pinned colours, the type faces and the
mono-means-machine rule; everything else here is dashboard-only.

This file does **not** override `docs/design/dashboard.md` or
`docs/design/demo-site.md` on function, data, clamps or copy. It owns how the
surface looks, and nothing else.

**The amendment rule.** The frontmatter tokens above are normative. Any agent
may **use** any token and may **add** a `components:` entry for a component it
is the first to build. No agent may introduce a raw colour, a font size outside
the ladder, or a spacing value off the 4px grid. If a page genuinely needs a new
primitive, the agent edits this file **in the same commit** as the CSS and says
why — the same discipline `CLAUDE.md` already requires when an implementation
diverges from a design contract.

Values live in `mcp/src/vidtheque_mcp/dashboard/static/dashboard.css`. Write
`var(--token)`, never a hex. The frontmatter carries the **light** value; the
dark scheme redefines the six pinned properties and `--tone-warn` under
`@media (prefers-color-scheme: dark)` and every other token follows through the
Radix scale it points at.

## Overview

**Creative North Star: "The cutting room"**

The world is derived from what this product actually handles: timecode, shot
boundaries, contact sheets, and the log a lab keeps of what it processed. It is
warm paper under a single tungsten-amber signal — not the achromatic console
every developer tool ships, and not a movie poster either. "Cutting room" is a
derivation, never a decoration: it shows up as the mono/sans truth split, the
ruled ledger band, the shot timeline and the contact-sheet gutters. It must
never show up as film sprockets, clapperboard icons or faux-CRT.

The surface is dense because the operator is scanning sixty rows at 03:00 over
an SSH tunnel, and quiet because everything on it is either a fact from the
index or a state the index is in. There are no cards, no shadows and no charts.
Separation is done with hairlines and whitespace; emphasis is done with weight,
size and one accent. The page is a document that happens to contain tables, and
it should read like a lab's processing log — because that is what it is.

The single most important decision is not a colour. It is that the surface has
**two typographic channels**, and which channel a string is in tells you where
it came from.

**Key Characteristics:**
- Three faces, one meaning each: Inter for human text, JetBrains Mono for
  machine text, Instrument Serif for the product's own name and nothing else.
- Warm paper and one burnt amber, on vendored 12-step Radix `sand` and `amber`.
- Hairlines, never cards. No shadow anywhere except the lightbox backdrop.
- Every state is a word as well as a colour.
- 4px grid, 34px table rows, 13px cells, tabular numerals everywhere a number appears.
- Both schemes are peers; the dark scheme is not an afterthought.
- Zero layout shift: every image has explicit dimensions or a fixed-aspect box.

## Colors

Warm neutrals from Radix `sand` with one burnt-amber signal and four state hues,
all vendored as plain custom properties — the values are copied in, the package
is not a dependency.

### Primary

- **Burnt Amber** (`--accent`, #b45309 light / #f0a55a dark): the one signal.
  Links, the focus ring, timecodes, the gap counts you are meant to click, the
  active nav item, the shot bars, the OCR box outlines, the meter fill. It is
  pinned: `test_dashboard.py::test_the_dashboard_palette_matches_the_demos`
  asserts it equal to the demo page's.
- **Lamp Amber** (`--accent-solid`, #ffc53d): the solid fill of the same signal,
  for a mark that must be seen rather than read. Radix `amber-9`.
- **Amber Wash / Amber Edge** (`--accent-bg` #fff7c2, `--accent-line` #e9c162):
  the ground and border a selected or active thing sits on.

### Neutral

- **Paper** (`--bg`, #fbfaf9 light / #131313 dark): the page ground. Pinned.
- **Ink** (`--fg`, #1b1a19 light / #e9e6e3 dark): body text. Pinned.
- **Pencil** (`--muted`, #6b6764 light / #97918c dark): row meta, notes, column
  labels, the footer. Pinned. Measured 5.37:1 on paper, 4.92:1 on panel.
- **Hairline** (`--line`, #e3dfdb light / #2b2927 dark): every rule and border
  that separates without announcing. Pinned.
- **Card Stock** (`--raised`, #ffffff light / #1a1a19 dark): inputs, the
  lightbox. Pinned.
- **Bench** (`--panel`, Radix `sand-3`): the second ground — sticky table heads,
  the filter bar, and (layout v2) the nav rail. What separates a dense grid, or
  the chassis, from the page it sits on.
- **Pointed-At** (`--row-hover`, Radix `sand-2`): the ground of the row under
  the pointer. Deliberately lighter than the bench.
- **Score** (`--rule`, Radix `sand-7`): the one rule heavier than a hairline —
  the top of the ledger band and the underline of a sticky head.
- **Graphite** (`--mark`, Radix `sand-9`): non-text marks only. Hatching on a
  deduplicated shot, the `·` separators. **Never type.** It measures 3.20:1 on
  the page ground — and **2.93:1 on `--panel`**, under the 3:1 floor, which is
  why the rail foot lists one fact per line instead of separating them with
  dots. A `·` is only allowed on `--bg`.
- **Scrim** (`scrim`): the lightbox `::backdrop` only. Deliberately a literal
  black alpha rather than a scheme token — a modal scrim has to darken whatever
  is behind it, in both schemes, and a token that flips with the scheme would
  brighten the page it is meant to suppress.

### Tertiary — the state hues

Five tones, each with three tokens: `--tone-X` is the ink, `--tone-X-bg` its
ground, `--tone-X-line` its border. Radix steps 11 / 2 / 6.

- **Grass** (`--tone-ok`): done, ready, present, allowed.
- **Burnt Gold** (`--tone-warn`): deferred, cancel requested, held.
- **Signal Red** (`--tone-bad`): failed, refused, drifted, degraded.
- **Instrument Blue** (`--tone-work`): running, indexing, mid-pipeline.
- **Slate** (`--tone-wait` / `--tone-neutral`): queued, skipped, absent,
  unrecognised. Both point at Radix `sand-11`.

### Named Rules

**The Word-and-Colour Rule.** Every state prints its own word. Colour is
reinforcement, never the message. A state you can only read by seeing hue is a
bug, and the four state vocabularies in this product are deliberately not
unified — no surface may invent a fifth.

**The One Signal Rule.** `--accent` means exactly one thing: *this is the moment
you are pointing at*. The playhead, the selected shot, the focus ring, the
active nav item, the number worth clicking. If everything is amber, nothing is.

**The Step-2 Rule.** A tone's subtle ground is its scale's step **2**, not
step 3. Radix's own contract is that step 11 reads ≥4.5:1 on steps 1 and 2;
measured against step 3 the work pill lands at 4.25:1.

**The Measured Exception.** `--tone-warn` is the one hand-held colour in the
system: Radix `amber-11` measures 4.42:1 on this page ground and 4.43:1 on its
own step 2 — under the floor in both places, in the light scheme only. Light
keeps the incumbent #8a5a00 (5.69:1); dark stays on the scale. If you find a
second exception, measure it and write it here.

## Typography

**Body Font:** Inter Variable (with the platform sans as fallback)
**Label/Mono Font:** JetBrains Mono Variable (with `ui-monospace` as fallback)
**Brand Font:** Instrument Serif, 400, for the word `vidtheque` and nothing
else. Inside the page, hierarchy still comes from weight, size and colour and
never from a second personality — see **The Wordmark-Only Rule**.

All three are vendored, latin subset, SIL OFL 1.1, at `dashboard/static/fonts/`
with their licence texts and a `PROVENANCE.md`. Only the text face is preloaded.

**Character:** Inter is the neutral workhorse whose number forms survive a dense
table. JetBrains Mono is chosen for one measurable property: it draws `0`/`O`
and `1`/`l`/`I` apart, which stops being cosmetic when the machine strings on
this surface include YouTube ids like `kCc8FmEb1nY`.

### Hierarchy

- **Display / h1** (590, 30px, 1.2, −0.02em): the page title. One per page. It
  steps down to **24px** below 40rem (`--t-h1-sm`) and no further: dropping it
  all the way to the 20px headline size put a phone's largest type 1.8× its
  smallest and flattened the whole ladder, which the type detector reads as a
  page with no top and which it is right about.
- **Headline / h2** (590, 20px, 1.2, −0.01em): a section heading that is not a
  panel title. Rare.
- **Title** (590, 15px, 1.35): panel headings that carry a sentence, and notice
  titles.
- **Body** (400, 14px, 1.5): prose, list rows, notes. Measure capped at
  `--prose` (46rem); the scanning grid takes the full content column.
- **Cell** (400, 13px, 1.4): table cells, minirows, chips, buttons, inputs.
- **Label** (510, 11px, 1.4, 0.07em, uppercase): panel titles, ledger labels,
  table heads, field labels, the stacked-cell prefixes on mobile. One idiom, so
  a column name reads the same whether it is above the column or in front of it.
- **Machine** (mono 400, 12.5px, 1.4, tabular, ligatures off): ids, model keys,
  error codes, timecodes, durations, clocks, counts, byte figures.
- **Figure** (mono 510, 30px, 1.2, −0.02em, tabular): a ledger number.

Three weights exist and there is no fourth: 400 regular, **510** working, 590
strong. 510 is the density weight — heavier than body, lighter than a heading —
and only a variable font can hold it.

### Named Rules

**The Two-Channel Rule.** Mono means the machine said it; sans means a human
wrote it. Every timecode, duration, id, `model_key`, `error_code`, count, clock,
confidence and byte figure is mono and tabular. Every title, channel name,
description, label and sentence is Inter. The eye learns this in ten seconds and
then the page reads itself. This is not mono-as-costume-for-technical — it is
information architecture wearing a typeface, and it is a promotion of the rule
`demo-site.md` §6.3 already applied to OCR text.

**The Wordmark-Only Rule.** (Amended 2026-08-09; it replaces the
No-Display-Face Rule, which said `/dashboard` gets no serif at all.) The serif
sets the string `vidtheque` and nothing else, ever. Exactly two selectors may
name `--font-display` — `.wordmark` in the rail and `.footmark` in the footer —
and a third is a bug, not a variation. No heading, no notice title, no empty
state, no figure, no label and no cell may reach for it, because the moment the
serif touches a data surface it stops being an identity and becomes a costume.

The reasoning, so the next agent does not relitigate it. Direction A ("the
archive slip") was rejected *for the surface* and it was right to be: a display
serif and 48px section gaps are the wrong answer for a table you scan sixty rows
of, and the research memo says so in §1.3. But the product's own name is not a
data surface. It is the one string on this page that the index did not produce —
not a fact, not a state, not a machine string — and setting it in the voice of a
library's accession record is exactly what the mono/sans split already does for
everything else: **the typeface says where the string came from.** Machine text
is mono, human text is sans, and the one thing that is neither is the name of
the thing itself. Tom authorised the mix on 2026-08-09; this rule is the fence
around it.

The mark beside it is the favicon's own drawing at rail scale — the same film
frame, path for path, with its two colours taken from `--accent` and `--bg` so
it flips with the scheme. This is the one sanctioned exception to *don't reach
for film sprockets*: that ban is about decoration on data surfaces, and this is
one 26px mark that appears once per page in the chassis. A second drawn object
anywhere on this surface needs a new argument.

**The Dead-Feature Rule.** Do not set `font-feature-settings: "cv01" 1, "ss03" 1`.
The vendored Inter subset's GSUB is `calt ccmp dnom frac locl numr pnum tnum` —
those two features are not in the file and setting them is a silent no-op.
`tabular-nums` (`tnum`) *is* there and is the one that matters.

## Layout

**The app shell** (layout v2, 2026-08-09). A persistent rail on the left and a
fluid content column taking everything else:

```
.shell   grid: [--rail 15rem] [minmax(0, 1fr)]
  .rail  sticky, 100dvh, --panel ground, hairline on its right edge
  .col   the page: main + footer, padded --s6, capped at --wide
```

Phase 1 centred everything on a 74rem `--measure`, which is the demo page's
chassis: right for a document, wrong for a surface you operate. At 1920 it left
a third of the screen empty either side of a table that wanted the width, and
the nav was three links in a masthead you scrolled away from. So `--measure` is
gone. What replaced it:

- **The rail is `position: sticky`, not `fixed`** — sticky stays in flow, so the
  grid track reserves its width and the content needs no matching margin. It is
  exactly `100dvh` tall, because a stretched full-height element has nothing to
  stick to and its hairline would stop where the viewport did.
- **The content column is fluid** and capped only by `--wide` (110rem / 1760px),
  which is an ultra-wide backstop rather than a measure: past it the eye starts
  losing the row it is reading across. Every zone track is `minmax(0, …)` so a
  too-wide table scrolls inside its own wrapper instead of widening the grid.
- **`--prose` (46rem) survives unchanged** and is the only measure left. Prose
  is capped; grids are not. A paragraph of explanation next to a full-width
  table is still 46rem wide.

**Zones.** A full-bleed column needs panels side by side, because a three-line
list stretched across 1600px is a sentence with 1400px of nothing after it.
There are two shapes and no more: `.split` (equal halves) and `.split-main`
(2:1, the dense thing and the short answers beside it). Both collapse to one
column at 60rem.

**The page header band** (`.pagehead`) is the content column's own top edge:
full width, hairline under, title at the left. `.pagehead-line` puts the states
a page is in on the right of that same baseline. It is deliberately **not
sticky** — a 30px title band pinned to the top of an operator surface spends the
scarce dimension on something you read once.

**Spacing is a 4px grid** (`--s1` 4px through `--s12` 48px) and a value off it
is a new primitive. Sections are separated by `--s8` (32px) plus a hairline;
tight groups use `--s1`/`--s2`. More space above a heading than below it.

**Density.** Table rows are 34px (`--row`) with 13px cells and 8px padding.
Table heads are sticky, on `--panel`, underlined with an inset shadow rather
than a border (`border-collapse: collapse` drops a sticky cell's own border).

**Breakpoints**, and what each one is for:

- **76rem** — the five-column ledger becomes three columns, still one band.
- **60rem** — **the rail reflows.** Below this there is no room for 240px of
  navigation beside the content, so the rail stops being a column and becomes
  the strip across the top that phase 1 shipped: brand, a wrapping row of links
  with the current one underlined in `--accent`, deployment state on its own
  line. No drawer, no scrim, no checkbox — a three-item nav that fits on one
  line has nothing to gain from being hidden behind a control, and the control
  is the part of that pattern that costs the keyboard and the screen reader.
  The zones (`.split`, `.split-main`) collapse to one column at the same width.
- **52rem** — **pinned by test** (`test_both_schemes_and_a_mobile_viewport_are_declared`).
  The videos and jobs tables stop being tables: each row becomes a stacked block
  with the column name in front of the value, and the head is `display: none`
  rather than visually hidden — once `display: block` has taken the table apart
  there is no table for a `<th scope="col">` to be associated with, and a
  clipped-to-1px header row is three sticky cells and four sort links stacked
  inside a box the size of a full stop. `order` stays reachable in the filter
  bar. Every *other* grid stays a table and scrolls inside its own
  `.tablewrap`; the page body never scrolls sideways.
- **40rem** — the page gutter drops to 16px, h1 drops to 24px, the ledger goes
  to two columns with a full-width last cell, a row's far-end fact
  (`.row-when`) wraps onto its own line, and the keyframe strip and OCR grid
  reflow.

**Every URL is relative or root-relative.** `@font-face src` is
`fonts/…woff2`; assets in templates are `{{ root }}/static/…`. Nothing is ever
built from `PUBLIC_URL` — this surface is served through an SSH tunnel on a port
nobody predicted, and a dead font URL is a worse version of the dead thumbnail
phase 2 already shipped and fixed.

### Named Rules

**The No-Sideways Rule.** The page body never scrolls horizontally at any
width. A table too wide to stack scrolls inside its own wrapper.

**The Fixed-Box Rule.** Every image ships explicit `width`/`height` or lives in
a fixed `aspect-ratio` box that owns the geometry. CLS 0 is a shipped property
of this project and a strip of forty keyframes is the easiest place to lose it.

## Elevation & Depth

**There is no elevation.** No `box-shadow` on any surface, no cards, no lifted
containers. Depth is tonal and one step deep: the page ground (`--bg`), a second
ground for heads and the filter bar (`--panel`), and a card stock for inputs and
the lightbox (`--raised`). Everything else is separated by a 1px hairline.

Four shadows exist and none of them is decoration:

- `inset 0 -1px 0 var(--rule)` — the sticky table head's underline, an inset
  because a collapsed border vanishes when the cell detaches.
- `inset 1px 0 0 var(--accent)` — the 1px edge on a hovered row.
- `inset 2px 0 0 var(--accent)` — the active nav item's edge (layout v2). An
  inset rather than a `border-left` because the item is full-bleed and a border
  would move its text by 2px between states; 2px rather than 1px because it is
  read across a rail, not inside a row you are already pointing at. It is not a
  fifth **2px rule** in the alarm sense — nothing here is bordered.
- `rgb(0 0 0 / 0.66)` on the lightbox `::backdrop` — a real modal scrim.

### Named Rules

**The No-Card Rule.** A panel is a heading and a hairline. Nested boxes are how
a dashboard stops being scannable. If something needs to stand out, give it a
2px rule in a tone — not a border, a fill and a radius.

## Shapes

Radii are tight and there are three: `--r-sm` 3px (pills, chips, buttons,
inputs, thumbnails, frames, the focus ring), `--r-md` 5px (the filter bar, the
lightbox stage), `--r-lg` 8px (the lightbox itself). Nothing is a circle except
the live dot and the countdown's pill in the jobs view.

Borders are 1px and `--line` by default. Exactly three things are 2px, and each
one means something: the ledger's top rule, a panel that has gone `is-drift`,
and a notice's top rule in its tone. A 2px rule is an alarm; do not spend it on
decoration.

## Components

### Buttons
- **Shape:** slightly softened corners (3px), 1px border.
- **Primary:** ink ground, paper text (`--fg` on `--bg`), 4px/12px padding,
  13px at weight 510.
- **Hover:** ground and border go to `--accent`.
- **Ghost:** transparent ground, `--fg` text, `--sand-7` border; hover moves
  text and border to `--accent` and keeps the ground transparent.
- **Disabled:** 50% opacity, default cursor. Nothing moves.

### Pills (the state primitive)
- **Style:** tone ink on the tone's step-2 ground with a step-6 hairline, 3px
  radius, 11px at weight 510 with 0.04em tracking.
- **Always carries its word.** `.tone-ok|warn|bad|work|wait|neutral` set the
  three custom properties; the pill reads them. A pill with no word is invalid.

### Chips (tags)
- **Style:** transparent ground, `--muted` text, `--line` hairline, 3px radius.
  The count inside sits in mono at 11px in `--fg`.
- **Hover:** `--row-hover` ground, `--fg` text, `--accent-line` border.

### Tables — the sorted column, and the strip above
The head of the column the rows are actually ordered by takes `aria-sort` and
redraws its own inset underline in `--accent`, with the label in `--fg`. **No
caret glyph**: this system has no icon language and a sort arrow is not where
one should start (Appendix B.2 refused an icon rail for the same reason). The
One Signal Rule holds — the sorted column *is* the moment you are pointing at,
because you asked for it in the URL. The direction printed is the direction the
query uses, not a guess: titles are ascending, everything else descending, and
there is no opposite variant to toggle to, so a head is a statement rather than
a switch.

Above every grid, `.tablecount` is the table's caption in everything but
markup: how many rows arrived and whether there are more, on the left, with the
figure in mono because it came from a count; and on jobs a `.live` badge at the
far right — a `--tone-work` dot **and the word "live"**, because a pulse alone
is a state told in colour only.

### Containers
There are none. See **The No-Card Rule**. The two exceptions with a real box are
the filter bar (`--panel` ground, `--line` border, 5px) and the lightbox
(`--raised`, `--line`, 8px).

### Inputs / Fields
- **Style:** `--raised` ground, `--sand-7` border, 3px radius, 13px, 4px/8px
  padding. The label above is the 11px uppercase Label style.
- **Focus:** the one global ring — `2px solid var(--accent)` with 2px offset.
  There is exactly one focus treatment on this surface and it is visible on
  table rows and result rows, not only on form controls.
- **Placeholder:** `--muted` at full opacity.

### Navigation (the rail)
- **Shape:** a 15rem (`--rail`) sticky column on `--panel`, hairline on its
  right edge, holding three things in order — the brand lockup, one or more
  `.nav-group` label + `.navlist` pairs, and a `.rail-foot` pinned to the bottom
  carrying what this deployment is allowed to do (`auth=…`, read-only, indexing
  refused). Environment state belongs to the chassis, not to a page header.
- **Item:** full-bleed to the rail's edges (padding on the item, not the rail),
  `--row` tall — a nav item and a table row are the same 34px, which is what
  stops the rail feeling like a different product from the grid beside it. 13px
  at weight 510 in `--muted`.
- **Hover:** ground `--row-hover`, text `--fg`. Deliberately *lighter* than the
  rail in light and *darker* in dark; the same pair the table rows use.
- **Active:** the item takes the **content** ground (`--bg`) with its text in
  `--accent` and a `inset 2px 0 0 var(--accent)` edge, so it reads as a tab
  notched into the page rather than a pill floating on the rail — and the accent
  lands on type that measures ≥4.5:1, which it would not on `--panel` (4.41:1).
  Always with `aria-current="page"`.
- **Groups:** `.nav-group` is the one label idiom — 11px tracked uppercase, the
  same as a ledger label. A second group (search, settings) costs one more of
  these and nothing else. A dead link is not room, so nothing is listed before
  it exists.
- **Mobile (≤60rem):** the whole rail becomes a horizontal strip. Items lose the
  ground and the edge and take a 2px `--accent` bottom border instead —
  phase 1's masthead nav, unchanged.

### The brand lockup (chassis, every page)
The mark and the word on one baseline, 8px apart, at the top of the rail, with
the rubric under it in `--muted` at 12px. The mark is a 24px drawn film frame in
`--accent` with a `--bg` gate; the word is `vidtheque`, always lower case, in
`--font-display` at 20px/400. It is a link to the overview and it is the only
link on the surface whose hover does **not** go amber — the mark beside it is
already amber, and a wordmark that changes colour under the pointer reads as a
control. It underlines instead. The same lockup repeats once in the footer at
15px (`.footmark`) with the version beside it in mono, so the page is signed
rather than merely credited, and so the serif reads as a decision made twice
rather than as one odd word in a rail.

### The ledger band (signature, `/dashboard` overview)
A single ruled band across the full content column: a 2px `--rule` on top, a hairline
under, and N equal columns divided by hairlines. Each column is an 11px
uppercase label, a 30px mono tabular figure, and a 12px sans note. It reads as
one line in a log, which is what it is. It is **not** a row of stat cards, and
turning it into cards — separate boxes, shadows, individual borders — destroys
the only thing it is for.

### The shot timeline (signature, video detail)
A full-width horizontal band **directly under the header** (`.timeband`, and
"directly" is the point: it spent phase 2 as the third panel down, under a
counts grid and a seven-row table, which put the one artefact no neighbouring
product has below the fold on a laptop). One absolutely-positioned bar per shot
across the true duration, on a `--panel` ground inside a hairline. Kept
keyframes solid `--accent` at **0.85** opacity (0.75 measured 2.98:1 on
`--panel`, two hundredths under the floor for a non-text mark, and this mark is
the page's whole argument; 0.85 measures 3.48:1 light / 5.98:1 dark and still
leaves the step to full opacity that hover and focus use). A shot whose every
frame was deduplicated paints **its own `--bg` ground** and hatches `--mark` on
it at full strength, so it reads as "captured, then dropped" rather than as
empty video — and as paper with a scratch on it rather than as a gap between
bars. It is not hatched over `--panel`: `--mark` on `--panel` is 2.93:1 at full
strength, so no opacity of it could ever clear 3:1, while on `--bg` the same
graphite measures 3.20:1 light / 3.59:1 dark. Minimum bar width 3px — the bar's
*position* is the fact, and a mark you cannot see lies about the cut structure.
Hovering either the timeline or the keyframe strip lights the other.

Like the ledger band it carries only an `.sr-only` heading: a band of bars over
a counter running 0:00 to the runtime does not need a caption saying it is a
timeline. Under it sit two things. **The footage counter** (`.timeline-scale`)
is five quarter marks, each a 4px `--rule` tick and its clock, positioned at its
true percentage rather than spaced by flexbox — a band whose whole argument is
that position is a fact cannot carry a scale that is only approximately right.
The ends label the ends: `0:00` is left-aligned on the band's own edge and the
runtime is right-aligned on the other. **The key** (`.timeband-legend`) names
the two fills in 12px muted, with `.swatch` marks that are literally the bars'
own backgrounds at 24×12 — the hatch is the only mark on the page whose meaning
is not written beside it, and a key drawn any other way would be a claim rather
than a sample.

### The empty state
Three sentences' worth of contract in two elements: `.empty-lead` states the
absence in `--fg` at cell size and weight 510, `.empty-note` says in `--muted`
which row of which panel explains it. **Name the absence, name the cause, name
the move.** An operator opening a panel with nothing in it is asking one
question — is this broken, or is this correct — and "No keyframes." does not
answer it. It is the pill contract applied to a whole panel: never an absence on
its own, always a word.

### The OCR overlay (signature, video detail)
Normalised 0–1 boxes drawn over the keyframe inside a fixed 16:9 stage with
`overflow: hidden`, so a box can never escape onto the caption. 1px `--accent`
border with a 14% accent fill via `color-mix`. Hovering a line in the list
lights its box and vice versa. This is the single most convincing thing on the
page and it gets the room it needs.

### The stage rail (video detail) and the event log (jobs)
Both read as processing logs, because both are. Seven stages in pipeline order,
each a state word in its tone, a `model_key` in mono and an elapsed clock in
mono; an `absent` stage is a dimmed rule, not a missing row. The job event tail
is hairline-ruled rows of mono, with `not_before` as a live mono countdown —
the highest-value line on that page.

## Do's and Don'ts

### Do:
- **Do** put every timecode, duration, id, `model_key`, `error_code`, count,
  clock and byte figure in `var(--font-mono)` with `tabular-nums` and ligatures
  off. **The Two-Channel Rule** is the system.
- **Do** print the word for every state, in its tone. Colour reinforces.
- **Do** separate with hairlines and whitespace. **The No-Card Rule.**
- **Do** keep `--accent` for "this is the moment you are pointing at", and let a
  zero be muted — the accent means *go and look*, and there is nothing to look at.
- **Do** give every image explicit `width`/`height` or a fixed-aspect box.
- **Do** write `var(--token)`. If the token does not exist, amend this file in
  the same commit.
- **Do** keep both schemes green in the contrast sweep: body and label text
  ≥4.5:1, non-text marks ≥3:1, in light **and** dark.
- **Do** clamp lists server-side and print `has_more`, never a total. The URL is
  an input, not an instruction.

### Don't:
- **Don't** change `--bg`, `--fg`, `--muted`, `--line`, `--accent` or `--raised`
  without changing `public/static/style.css` in the same commit and widening
  `test_dashboard.py::test_the_dashboard_palette_matches_the_demos`. Those six
  are what stop the two surfaces becoming two visual worlds.
- **Don't** add an inline `<script>`. The pages stay CSP-ready, and that forbids
  the usual no-flash theme-toggle trick; the three-state toggle is a phase-3
  server-side cookie, not an inline script.
- **Don't** build a chart with a time axis. There is no time-series table and
  `indexed_at` at day resolution would only ever graph when Tom last ran a batch.
  No sparklines, no progress rings, no soft-shadowed rectangles standing in for
  content.
- **Don't** ship a card, a shadow, a gradient, glass, or a coloured `border-left`
  above 1px. *Gradient* here means a **tonal ramp used as decoration** — a
  surface that fades because fading looks expensive. It does not mean the CSS
  function: the deduplicated shot's hatch is a `repeating-linear-gradient`, and
  it is two flat colours at a fixed 2px/6px pitch encoding a fact the index
  stores. (An external review, 2026-08-09, read the rule literally and was
  right to ask; the answer is that the rule is about ramps, not about syntax. A
  hatch drawn as an SVG mask would satisfy the letter and change nothing about
  the pixels, which is how you can tell the letter was not the point.) A second
  pattern needs the same argument this one has: it must be encoding something.
- **Don't** reach for film sprockets, clapperboards, reel icons or faux-CRT.
  "Cutting room" is a derivation, not a decoration; if it starts looking like a
  movie poster it has failed. The single 24px `.brandmark` in the rail (and its
  identical twin in the tab strip) is the one sanctioned exception, and **The
  Wordmark-Only Rule** is the fence around it.
- **Don't** put `--font-display` on a second element. Two selectors, both named
  in that rule, and a third is a bug.
- **Don't** put a raw colour, an off-ladder font size or an off-4px spacing value
  in a stylesheet.
- **Don't** build an absolute asset URL from `PUBLIC_URL`. Relative or
  root-relative, fonts included.
- **Don't** inline a frame as base64 by default. Frames go by authenticated URL;
  base64 is the opt-in, with the correct mimeType.
- **Don't** rename the product's words. `cue`, `chunk`, `shot`, `keyframe`,
  `dup_of`, `stage`, `model_key`, `job item`, `not_before`, `index_state`,
  `data_status` are the vocabulary and a designer does not get to tidy them.
- **Don't** import a colour package, add a build step, or make a runtime request
  to anything off this box.
