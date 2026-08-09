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
typography:
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
  nav-link:
    textColor: "{colors.muted}"
    typography: "{typography.cell}"
  nav-link-active:
    textColor: "{colors.accent}"
  ledger-figure:
    backgroundColor: "{colors.bg}"
    textColor: "{colors.fg}"
    typography: "{typography.figure}"
    padding: "1rem"
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
- Two faces, one meaning each: Inter for human text, JetBrains Mono for machine text.
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
- **Bench** (`--panel`, Radix `sand-3`): the second ground — sticky table heads
  and the filter bar. What separates a dense grid from the page it sits on.
- **Pointed-At** (`--row-hover`, Radix `sand-2`): the ground of the row under
  the pointer. Deliberately lighter than the bench.
- **Score** (`--rule`, Radix `sand-7`): the one rule heavier than a hairline —
  the top of the ledger band and the underline of a sticky head.
- **Graphite** (`--mark`, Radix `sand-9`): non-text marks only. Hatching on a
  deduplicated shot, the `·` separators. **Never type.** It measures 3.20:1.

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
**Display Font:** none. Hierarchy comes from weight, size and colour, never from
a second personality.

Both are vendored, latin subset, variable, SIL OFL 1.1, at
`dashboard/static/fonts/` with their licence texts and a `PROVENANCE.md`. Only
the text face is preloaded.

**Character:** Inter is the neutral workhorse whose number forms survive a dense
table. JetBrains Mono is chosen for one measurable property: it draws `0`/`O`
and `1`/`l`/`I` apart, which stops being cosmetic when the machine strings on
this surface include YouTube ids like `kCc8FmEb1nY`.

### Hierarchy

- **Display / h1** (590, 30px, 1.2, −0.02em): the page title. One per page.
- **Headline / h2** (590, 20px, 1.2, −0.01em): a section heading that is not a
  panel title. Rare.
- **Title** (590, 15px, 1.35): panel headings that carry a sentence, and notice
  titles.
- **Body** (400, 14px, 1.5): prose, list rows, notes. Measure capped at
  `--prose` (46rem); the scanning grid gets `--measure` (74rem).
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

**The No-Display-Face Rule.** `/dashboard` gets no serif, no display cut, no
second personality. The editorial voice belongs to `/`, which is Persuade.

**The Dead-Feature Rule.** Do not set `font-feature-settings: "cv01" 1, "ss03" 1`.
The vendored Inter subset's GSUB is `calt ccmp dnom frac locl numr pnum tnum` —
those two features are not in the file and setting them is a silent no-op.
`tabular-nums` (`tnum`) *is* there and is the one that matters.

## Layout

One centred column, two widths: `--measure` (74rem) for anything you scan and
`--prose` (46rem) for anything you read. The masthead, `main` and the footer all
sit on `--measure`; every paragraph of explanation is capped at `--prose`.

**Spacing is a 4px grid** (`--s1` 4px through `--s12` 48px) and a value off it
is a new primitive. Sections are separated by `--s8` (32px) plus a hairline;
tight groups use `--s1`/`--s2`. More space above a heading than below it.

**Density.** Table rows are 34px (`--row`) with 13px cells and 8px padding.
Table heads are sticky, on `--panel`, underlined with an inset shadow rather
than a border (`border-collapse: collapse` drops a sticky cell's own border).

**Breakpoints**, and what each one is for:

- **76rem** — the five-column ledger becomes three columns, still one band.
- **60rem** — the two-column `.split` becomes one column.
- **52rem** — **pinned by test** (`test_both_schemes_and_a_mobile_viewport_are_declared`).
  The videos and jobs tables stop being tables: each row becomes a stacked block
  with the column name in front of the value. Every *other* grid stays a table
  and scrolls inside its own `.tablewrap`; the page body never scrolls sideways.
- **40rem** — the page gutter drops to 16px, h1 drops to 20px, the ledger goes
  to two columns with a full-width last cell, the keyframe strip and OCR grid
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

Three shadows exist and none of them is decoration:

- `inset 0 -1px 0 var(--rule)` — the sticky table head's underline, an inset
  because a collapsed border vanishes when the cell detaches.
- `inset 1px 0 0 var(--accent)` — the 1px edge on a hovered row.
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

### Navigation
- **Style:** a horizontal row in the masthead, 13px at weight 510, `--muted`,
  2px transparent bottom border. Hover goes to `--fg`; the current page goes to
  `--accent` with the bottom border in `--accent` and `aria-current="page"`.
- **Mobile:** it stays a row and wraps; the deployment strip drops to its own
  full-width line under it.

### The ledger band (signature, `/dashboard` overview)
A single ruled band across the full measure: a 2px `--rule` on top, a hairline
under, and N equal columns divided by hairlines. Each column is an 11px
uppercase label, a 30px mono tabular figure, and a 12px sans note. It reads as
one line in a log, which is what it is. It is **not** a row of stat cards, and
turning it into cards — separate boxes, shadows, individual borders — destroys
the only thing it is for.

### The shot timeline (signature, video detail)
A full-width horizontal band under the header, one absolutely-positioned bar per
shot across the true duration, on a `--panel` ground inside a hairline. Kept
keyframes solid `--accent` at 0.75 opacity; a shot whose every frame was
deduplicated is hatched in `--mark` at 0.4, so it reads as "captured, then
dropped" rather than as empty video. Minimum bar width 3px — the bar's
*position* is the fact, and a mark you cannot see lies about the cut structure.
Hovering either the timeline or the keyframe strip lights the other.

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
  above 1px.
- **Don't** reach for film sprockets, clapperboards, reel icons or faux-CRT.
  "Cutting room" is a derivation, not a decoration; if it starts looking like a
  movie poster it has failed.
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
