---
name: vidtheque
description: The projection room — a dark, gold-on-black surface where mono means the machine said it, lime means the machine read it off the screen, and nothing moves unless the machine is working.
scheme: dark-only
colors:
  # ── the ground, deepest first. Names are v5's own, so CSS ports without translation.
  pitch: "#040405"
  void: "#08080a"
  console: "#0a0a0e"
  plate: "#0e0e12"
  plate2: "#141419"
  plate3: "#1a1a20"
  seam: "#242429"
  seam2: "#33333b"
  black: "#000000"
  over: "rgba(8,8,11,.92)"
  seam-over: "rgba(255,255,255,.07)"
  edge-in: "rgba(255,255,255,.06)"
  scrim: "rgb(0 0 0 / 0.66)"
  # ── the ink
  fg: "#f3f0ea"
  fg2: "#9d968c"
  fg3: "#6b665e"
  fg-max: "#ffffff"
  fg-over: "#c9c2b6"
  # ── the one accent: gold
  gold: "#e7b455"
  gold-hi: "#f6cd78"
  gold-ink: "#120c02"
  gold-6: "rgba(231,180,85,.06)"
  gold-12: "rgba(231,180,85,.12)"
  gold-24: "rgba(231,180,85,.24)"
  gold-44: "rgba(231,180,85,.44)"
  # ── the seen channel: reserved, semantic, never decoration (The Lime Rule)
  seen: "#c9f938"
  seen-ink: "#0a1400"
  seen-10: "rgba(201,249,56,.10)"
  seen-24: "rgba(201,249,56,.24)"
  seen-48: "rgba(201,249,56,.48)"
  # ── the state tones. Ink is a hex; ground and border are mixed from it.
  tone-ok: "#71d083"
  tone-ok-bg: "color-mix(in srgb, {colors.tone-ok} 10%, {colors.plate})"
  tone-ok-line: "color-mix(in srgb, {colors.tone-ok} 26%, {colors.plate})"
  tone-warn: "#ffa057"
  tone-warn-bg: "color-mix(in srgb, {colors.tone-warn} 10%, {colors.plate})"
  tone-warn-line: "color-mix(in srgb, {colors.tone-warn} 26%, {colors.plate})"
  tone-bad: "#ff9592"
  tone-bad-bg: "color-mix(in srgb, {colors.tone-bad} 10%, {colors.plate})"
  tone-bad-line: "color-mix(in srgb, {colors.tone-bad} 26%, {colors.plate})"
  tone-work: "#70b8ff"
  tone-work-bg: "color-mix(in srgb, {colors.tone-work} 10%, {colors.plate})"
  tone-work-line: "color-mix(in srgb, {colors.tone-work} 26%, {colors.plate})"
  tone-wait: "{colors.fg2}"
  tone-wait-bg: "color-mix(in srgb, {colors.fg2} 10%, {colors.plate})"
  tone-wait-line: "color-mix(in srgb, {colors.fg2} 26%, {colors.plate})"
  tone-neutral: "{colors.tone-wait}"
  tone-neutral-bg: "{colors.tone-wait-bg}"
  tone-neutral-line: "{colors.tone-wait-line}"
  # ── the six pinned role names. Aliases only: an alias never carries its own value.
  bg: "{colors.pitch}"
  muted: "{colors.fg2}"
  line: "{colors.seam}"
  accent: "{colors.gold}"
  raised: "{colors.plate}"
  panel: "{colors.plate2}"
  rule: "{colors.seam2}"
  mark: "{colors.fg3}"
film:
  rest: "brightness(.52) saturate(.72) contrast(1.05)"
  dim: "brightness(.13) saturate(.2)"
  lit: "brightness(1.34) saturate(1.04) contrast(1.02)"
  band: "saturate(.78) brightness(.7)"
  full: "none"
typography:
  brand:
    fontFamily: "Archivo VF, system-ui, -apple-system, sans-serif"
    fontSize: "16.5px"
    fontWeight: 500
    lineHeight: 1.1
    letterSpacing: "-0.035em"
  brand-lg:
    fontFamily: "{typography.brand.fontFamily}"
    fontSize: "clamp(2.2rem, 4.4vw, 4rem)"
    fontWeight: 250
    lineHeight: 0.9
    letterSpacing: "-0.045em"
  display:
    fontFamily: "{typography.brand.fontFamily}"
    fontSize: "clamp(2.7rem, 5.6vw, 5.6rem)"
    fontWeight: 200
    lineHeight: 0.96
    letterSpacing: "-0.04em"
  headline:
    fontFamily: "{typography.brand.fontFamily}"
    fontSize: "clamp(1.9rem, 3.5vw, 3.35rem)"
    fontWeight: 210
    lineHeight: 1.0
    letterSpacing: "-0.034em"
  question:
    fontFamily: "{typography.brand.fontFamily}"
    fontSize: "clamp(1.05rem, 1.6vw, 1.5rem)"
    fontWeight: 250
    lineHeight: 1.26
    letterSpacing: "-0.028em"
  quote:
    fontFamily: "{typography.brand.fontFamily}"
    fontSize: "clamp(1rem, 1.24vw, 1.24rem)"
    fontWeight: 330
    lineHeight: 1.36
    letterSpacing: "-0.022em"
  lede:
    fontFamily: "{typography.brand.fontFamily}"
    fontSize: "clamp(0.98rem, 1.12vw, 1.1rem)"
    fontWeight: 340
    lineHeight: 1.62
    letterSpacing: "-0.004em"
  body:
    fontFamily: "{typography.brand.fontFamily}"
    fontSize: "16px"
    fontWeight: 340
    lineHeight: 1.55
    letterSpacing: "-0.004em"
  prose:
    fontFamily: "{typography.brand.fontFamily}"
    fontSize: "15.5px"
    fontWeight: 340
    lineHeight: 1.68
    letterSpacing: "-0.004em"
  action:
    fontFamily: "{typography.brand.fontFamily}"
    fontSize: "15px"
    fontWeight: 600
    lineHeight: 1.1
    letterSpacing: "-0.012em"
  cell:
    fontFamily: "{typography.brand.fontFamily}"
    fontSize: "14px"
    fontWeight: 340
    lineHeight: 1.4
    letterSpacing: "-0.012em"
  cell-strong:
    fontFamily: "{typography.brand.fontFamily}"
    fontSize: "14px"
    fontWeight: 520
    lineHeight: 1.4
    letterSpacing: "-0.02em"
  machine:
    fontFamily: "JetBrains Mono VF, ui-monospace, SFMono-Regular, monospace"
    fontSize: "13px"
    fontWeight: 400
    lineHeight: 1.4
    letterSpacing: "-0.01em"
    fontFeature: "tabular-nums; ligatures none"
  query:
    fontFamily: "{typography.machine.fontFamily}"
    fontSize: "13.5px"
    fontWeight: 400
    lineHeight: 1.2
    letterSpacing: "normal"
    fontFeature: "ligatures none"
  log:
    fontFamily: "{typography.machine.fontFamily}"
    fontSize: "12.5px"
    fontWeight: 400
    lineHeight: 1.85
    letterSpacing: "normal"
    fontFeature: "tabular-nums; ligatures none"
  machine-sm:
    fontFamily: "{typography.machine.fontFamily}"
    fontSize: "11px"
    fontWeight: 400
    lineHeight: 1.55
    letterSpacing: "0.005em"
    fontFeature: "tabular-nums; ligatures none"
  label:
    fontFamily: "{typography.machine.fontFamily}"
    fontSize: "10px"
    fontWeight: 600
    lineHeight: 1.4
    letterSpacing: "0.19em"
    textTransform: "uppercase"
    fontFeature: "ligatures none"
  label-sm:
    fontFamily: "{typography.machine.fontFamily}"
    fontSize: "9.5px"
    fontWeight: 600
    lineHeight: 1.4
    letterSpacing: "0.17em"
    textTransform: "uppercase"
    fontFeature: "ligatures none"
  tag:
    fontFamily: "{typography.machine.fontFamily}"
    fontSize: "8.5px"
    fontWeight: 700
    lineHeight: 1.1
    letterSpacing: "0.14em"
    textTransform: "uppercase"
    fontFeature: "ligatures none"
  figure:
    fontFamily: "{typography.machine.fontFamily}"
    fontSize: "clamp(1.6rem, 2.5vw, 2.5rem)"
    fontWeight: 300
    lineHeight: 1.0
    letterSpacing: "-0.045em"
    fontFeature: "tabular-nums; ligatures none"
weights:
  display: 200
  display-2: 250
  body: 340
  strong: 500
  action: 600
  mono-label: 600
  mono-tag: 700
rounded:
  none: "0"
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
layout:
  maxw: "1460px"
  gut: "clamp(20px, 4.2vw, 72px)"
  beat: "clamp(64px, 7.4vw, 124px)"
  gap-block: "clamp(34px, 4vw, 58px)"
  gap-head: "clamp(22px, 2.4vw, 34px)"
  gap-copy: "clamp(16px, 1.9vw, 26px)"
  prose: "70ch"
  lede: "60ch"
  quote: "58ch"
  note: "74ch"
  bp-stack: "1120px"
  bp-hand: "780px"
elevation:
  drop: "0 40px 90px -24px rgba(0,0,0,.94)"
  drop-sm: "0 22px 50px -18px rgba(0,0,0,.9)"
  edge: "inset 0 0 0 1px {colors.edge-in}"
  edge-hit: "inset 0 0 0 2px {colors.gold}"
motion:
  ease-lift: "cubic-bezier(.22,.72,.2,1)"
  ease-acquire: "cubic-bezier(.16,1,.3,1)"
  t-fast: "0.16s"
  t-state: "0.25s"
  t-film: "0.5s"
  t-veil: "0.55s"
  t-lift: "0.82s"
  t-acquire: "0.45s"
  blink: "1.05s steps(1,end) infinite"
  drift-gate: "150s ease-in-out infinite alternate"
  drift-band: "900s linear infinite"
components:
  wordmark:
    textColor: "{colors.fg}"
    typography: "{typography.brand}"
    accentGlyph: "{colors.gold}"
  wordmark-lg:
    textColor: "{colors.fg}"
    typography: "{typography.brand-lg}"
    accentGlyph: "{colors.gold}"
  kicker:
    textColor: "{colors.gold}"
    typography: "{typography.label}"
    rule: "34px x 1px {colors.gold}, 12px gap"
  micro-label:
    textColor: "{colors.fg2}"
    typography: "{typography.label}"
  button-primary:
    backgroundColor: "{colors.gold}"
    textColor: "{colors.gold-ink}"
    borderColor: "{colors.gold}"
    rounded: "{rounded.none}"
    padding: "14px 20px 14px 22px"
    typography: "{typography.action}"
  button-primary-hover:
    backgroundColor: "{colors.gold-hi}"
  button-ghost:
    backgroundColor: "transparent"
    textColor: "{colors.fg2}"
    borderColor: "{colors.seam2}"
    rounded: "{rounded.none}"
    padding: "0 12px"
    height: "2.125rem"
    typography: "{typography.label}"
  button-ghost-hover:
    textColor: "{colors.gold}"
    borderColor: "{colors.gold-44}"
  chip:
    backgroundColor: "transparent"
    textColor: "{colors.fg3}"
    borderColor: "{colors.seam}"
    rounded: "{rounded.none}"
    padding: "0 12px"
    height: "2.75rem"
    typography: "{typography.label-sm}"
  chip-hover:
    textColor: "{colors.fg}"
  chip-on:
    backgroundColor: "{colors.gold-12}"
    textColor: "{colors.gold}"
  input-text:
    backgroundColor: "{colors.console}"
    textColor: "{colors.fg}"
    borderColor: "{colors.seam2}"
    rounded: "{rounded.none}"
    padding: "0 13px"
    height: "2.75rem"
    typography: "{typography.query}"
  picker:
    backgroundColor: "{colors.console}"
    textColor: "{colors.fg}"
    borderColor: "{colors.seam2}"
    rounded: "{rounded.none}"
    padding: "0 24px 0 13px"
    height: "2.125rem"
    typography: "{typography.query}"
    appearance: "none — the platform draws none of it"
    mark: "6px square, right + bottom 1px {colors.fg2}, rotated 45deg, 12px from the right edge"
    markHover: "{colors.gold}"
  hint:
    backgroundColor: "{colors.plate}"
    textColor: "{colors.fg2}"
    borderColor: "{colors.seam2}"
    rounded: "{rounded.none}"
    padding: "8px 12px"
    maxWidth: "24rem"
    shadow: "none — it is one plate above the page, not lifted off the wall"
    typography: "{typography.machine-sm}"
  input-textarea:
    backgroundColor: "{colors.console}"
    textColor: "{colors.fg}"
    borderColor: "{colors.seam2}"
    rounded: "{rounded.none}"
    padding: "0.75rem"
    minHeight: "8.5rem"
    typography: "{typography.machine}"
  query-bar:
    backgroundColor: "{colors.over}"
    borderColor: "{colors.seam2}"
    height: "52px"
    shadow: "{elevation.drop-sm}"
    typography: "{typography.query}"
  query-caret:
    backgroundColor: "{colors.gold}"
    width: "8px"
    height: "17px"
    animation: "{motion.blink}"
  status-cell:
    backgroundColor: "{colors.console}"
    textColor: "{colors.fg3}"
    borderColor: "{colors.seam}"
    padding: "0 14px"
    typography: "{typography.tag}"
  status-cell-active:
    backgroundColor: "{colors.gold}"
    textColor: "{colors.gold-ink}"
  panel:
    backgroundColor: "{colors.plate}"
    borderColor: "{colors.seam2}"
    rounded: "{rounded.none}"
    cornerTick: "12px x 1px + 1px x 12px {colors.gold} at the top-left corner"
  panel-lifted:
    backgroundColor: "{colors.plate}"
    borderColor: "{colors.seam2}"
    shadow: "{elevation.drop}"
  panel-head:
    backgroundColor: "{colors.plate2}"
    borderColor: "{colors.seam}"
    padding: "9px 14px"
    minHeight: "32px"
    typography: "{typography.label}"
  panel-foot:
    backgroundColor: "{colors.plate2}"
    borderColor: "{colors.seam}"
    padding: "11px 16px"
    typography: "{typography.label}"
  panel-note:
    textColor: "{colors.gold}"
    typography: "{typography.machine-sm}"
    prefix: "note: in {colors.fg3}"
  frame:
    backgroundColor: "{colors.black}"
    aspectRatio: "16/9"
    rounded: "{rounded.none}"
    shadow: "{elevation.edge}"
  det-box:
    borderColor: "{colors.seen-24}"
    borderWidth: "1px"
    rounded: "{rounded.none}"
  det-box-on:
    borderColor: "{colors.seen}"
    borderWidth: "1.5px"
  det-tag:
    backgroundColor: "{colors.seen}"
    textColor: "{colors.seen-ink}"
    padding: "2.5px 6px"
    typography: "{typography.tag}"
  receipt:
    backgroundColor: "{colors.gold-6}"
    textColor: "{colors.gold}"
    borderColor: "{colors.gold}"
    rounded: "{rounded.none}"
    padding: "9px 0 9px 11px"
    typography: "{typography.machine}"
    fontWeight: 600
  receipt-action:
    backgroundColor: "{colors.gold}"
    textColor: "{colors.gold-ink}"
    padding: "9px 11px"
    fontWeight: 700
  receipt-hover:
    backgroundColor: "{colors.gold-12}"
  quote-block:
    textColor: "{colors.fg}"
    borderLeft: "1px solid {colors.gold}"
    padding: "0 0 0 14px"
    typography: "{typography.quote}"
    maxWidth: "{layout.quote}"
  ledger:
    backgroundColor: "{colors.plate}"
    borderColor: "{colors.seam2}"
    dividerColor: "{colors.seam}"
    padding: "20px 22px 22px"
  ledger-label:
    textColor: "{colors.fg2}"
    typography: "{typography.label-sm}"
  ledger-figure:
    textColor: "{colors.fg}"
    typography: "{typography.figure}"
    unitColor: "{colors.gold}"
  log:
    backgroundColor: "{colors.console}"
    textColor: "{colors.fg2}"
    borderColor: "{colors.seam2}"
    padding: "clamp(22px,2.4vw,36px) clamp(18px,2.4vw,36px)"
    typography: "{typography.log}"
  log-call:
    textColor: "{colors.fg}"
    keyColor: "{colors.gold}"
    argColor: "{colors.fg2}"
    typography: "{typography.log}"
  wall-tile:
    backgroundColor: "{colors.black}"
    filter: "{film.band}"
    shadow: "{elevation.edge}"
  wall-tile-slug:
    backgroundColor: "rgba(3,5,8,.72)"
    textColor: "{colors.fg2}"
    padding: "2.5px 5px 3px"
    typography: "{typography.tag}"
  wall-tile-hit:
    filter: "{film.lit}"
    shadow: "{elevation.edge-hit}"
  pill:
    backgroundColor: "{colors.tone-neutral-bg}"
    textColor: "{colors.tone-neutral}"
    borderColor: "{colors.tone-neutral-line}"
    rounded: "{rounded.none}"
    padding: "1px 0.5rem"
    typography: "{typography.label-sm}"
  table-head:
    backgroundColor: "{colors.plate2}"
    textColor: "{colors.fg2}"
    borderColor: "{colors.seam2}"
    typography: "{typography.label}"
    padding: "0.5rem"
  table-head-sorted:
    textColor: "{colors.fg}"
    borderColor: "{colors.gold}"
  table-row:
    backgroundColor: "transparent"
    textColor: "{colors.fg}"
    borderColor: "{colors.seam}"
    typography: "{typography.cell}"
    padding: "0.5rem"
    height: "2.125rem"
  table-row-hover:
    backgroundColor: "{colors.plate}"
    shadow: "inset 1px 0 0 {colors.gold}"
  nav-rail:
    backgroundColor: "{colors.void}"
    borderColor: "{colors.seam}"
    width: "15rem"
  nav-group:
    textColor: "{colors.fg3}"
    typography: "{typography.label}"
    padding: "0.75rem 1rem 0.25rem"
  nav-link:
    backgroundColor: "transparent"
    textColor: "{colors.fg2}"
    typography: "{typography.cell}"
    padding: "0.5rem 1rem"
    height: "2.125rem"
  nav-link-hover:
    backgroundColor: "{colors.plate}"
    textColor: "{colors.fg}"
  nav-link-active:
    backgroundColor: "{colors.pitch}"
    textColor: "{colors.gold}"
    shadow: "inset 2px 0 0 {colors.gold}"
  field-help:
    textColor: "{colors.fg2}"
    typography: "{typography.machine-sm}"
  empty-lead:
    textColor: "{colors.fg}"
    typography: "{typography.cell-strong}"
  empty-note:
    textColor: "{colors.fg2}"
    typography: "{typography.cell}"
  next-affordance:
    backgroundColor: "transparent"
    textColor: "{colors.gold}"
    borderColor: "{colors.gold-44}"
    borderStyle: "1px dashed, offset -4px"
    typography: "{typography.label}"
  next-affordance-hover:
    backgroundColor: "{colors.gold-6}"
  focus-ring:
    outline: "2px solid {colors.gold}"
    outlineOffset: "2px"
  selection:
    backgroundColor: "{colors.gold}"
    textColor: "{colors.gold-ink}"
---

# Design System: vidtheque — the projection room

Normative for every **product** web surface: the landing page, the public demo
at `/`, and the dashboard at `/dashboard`. One world, three densities.

This file does **not** override `docs/design/demo-site.md` or
`docs/design/dashboard.md` on function, data, clamps or copy, and it does not
override `docs/design/positioning.md` on voice. It owns how the surfaces look,
and nothing else.

**Reference implementation:** `mcp/src/vidtheque_mcp/public/static/lab/versions/v5.html`
("projection room"). Tom picked it on 2026-08-10 as the product's visual
identity. Every token below was read out of v5 as shipped; where a value is an
extension rather than a readout it says so in the prose. When this file and v5
disagree, **this file wins** — v5 is a lab piece and may drift; but say why in
the amending commit, because v5 is the thing Tom actually approved.

**The amendment rule.** The frontmatter tokens above are normative. Any agent
may **use** any token and may **add** a `components:` entry for a component it
is the first to build. No agent may introduce a raw colour, a font size outside
the ladder, a font weight off the weight ladder, a non-zero border radius, or a
layout spacing value off the 4px grid. If a page genuinely needs a new
primitive, the agent edits this file **in the same commit** as the CSS and says
why — the same discipline `CLAUDE.md` already requires when an implementation
diverges from a design contract.

Write `var(--token)`, never a hex. The system is **dark only**: there is one
scheme, `color-scheme: dark` is declared, and there is no light palette, no
`prefers-color-scheme` block and no toggle. A projection room does not have a
day mode.

**What happened to the warm-paper system.** Removed, 2026-08-10, on Tom's
order. The previous contract — Radix `sand`/`amber` on paper `#fbfaf9`, Inter +
Instrument Serif, 3/5/8px radii, light and dark as peers — is replaced in full
by the palette, faces and rules below. Git history keeps it (`git show
HEAD~1:DESIGN.md`); nothing here is a deprecated appendix, because a dead
palette left in the contract is a palette somebody uses. Three ideas survived
the change and are re-stated below in their new clothes: the Two-Channel Rule,
the Word-and-Colour Rule and the One Signal Rule.

## Overview

**Creative North Star: "the projection room."**

The room is dark because the corpus is footage and the only lit things in a
projection room are the ones being looked at. The page ground is near-black,
the frames are the light, and one gold accent marks the moment you are pointing
at — the receipt, the CTA, the found frame. A second hue exists and is spent on
exactly one meaning: lime is what the machine **read off the screen**.

The derivation is the product's own machinery — the gate, the light table, the
wall of watched frames, the booth log — and it shows up as behaviour, not as
decoration: the wall drifts because the projector is running, the query types
itself because the agent is querying, the frame lifts off the wall onto the
table because that is what "found it" looks like. It must never show up as film
sprockets, clapperboards, faux-CRT, scan lines or a glow.

**Key characteristics:**

- Two faces, one meaning each: Archivo for human text, JetBrains Mono for
  machine text. No third face — the wordmark is the sans, not a serif.
- Gold on black, one accent; lime reserved for on-screen-text evidence.
- **Zero radius everywhere.** `*{border-radius:0}` is in the reset.
- Hairlines and plates, not cards. A shadow means *lifted off the wall*.
- Every state is a word as well as a colour.
- Movement only where the machine is working; everything freezes under
  `prefers-reduced-motion`.
- Zero layout shift: every image has explicit dimensions or a fixed-aspect box.

## Colors

### The ground

Six plates and two seams, deepest first. They are a depth scale, not a set of
options: a thing sits one plate above what contains it, and no further.

- **Pitch** (`--pitch` #040405) — the page ground.
- **Void** (`--void` #08080a) — a section band and the footer; the one step off
  the page ground that separates a beat without a rule.
- **Console** (`--console` #0a0a0e) — the ground of a log, a terminal, a query
  field: a surface the machine writes into.
- **Plate** (`--plate` #0e0e12) — a real box's ground (a panel, the ledger, a
  copy box).
- **Plate 2** (`--plate2` #141419) — the head or foot band *inside* a box.
- **Plate 3** (`--plate3` #1a1a20) — the topmost plate; swatches and inert
  fills. Nothing sits above it.
- **Seam** (`--seam` #242429) — the hairline. Every rule and border that
  separates without announcing.
- **Seam 2** (`--seam2` #33333b) — the edge of a real box, one step louder.
- **Black** (`--black` #000) — the ground *behind a frame*. Image boxes only, so
  a still that has not decoded yet leaves black rather than a coloured plate.

Three values exist for chrome that floats over imagery, where a plate would
read as a hole: `--over` (a control's ground), `--seam-over` (a rule), and
`--edge-in` (the 1px inset hairline that keeps an image from bleeding into the
page). `--scrim` is the modal `::backdrop` and is deliberately a literal black
alpha — a scrim has to darken whatever is behind it.

### The ink

- **Fg** (`--fg` #f3f0ea) — body text. 18.02:1 on `--pitch`, 16.94:1 on `--plate`.
- **Fg2** (`--fg2` / `--muted` #9d968c) — secondary prose, ledes, notes, labels
  that carry a fact. 7.00:1 on `--pitch`, 6.58:1 on `--plate`.
- **Fg3** (`--fg3` / `--mark` #6b665e) — **3.60:1 on `--pitch`, 3.38:1 on
  `--plate`.** Over the 3:1 non-text floor, under the 4.5:1 text floor. It is
  therefore *atmosphere*: the landing may set decorative micro-labels and
  non-load-bearing captions in it, and **no label that is the only carrier of a
  fact may use it on any surface**. On the demo and the dashboard, label ink is
  `--fg2`. This is the one measured exception in the system; if you find you
  need a second, measure it and write it here.
- **Fg-max** (`--fg-max` #fff) — the display headline only. Pure white is the
  top of the ladder and it is spent on one element per page.
- **Fg-over** (`--fg-over` #c9c2b6) — body set over imagery, with the text
  shadow the hero uses. Not for use on a plate.

### Gold — the one accent

**Gold** (`--gold` #e7b455, 10.79:1 on `--pitch`, 10.14:1 on `--plate`) means
exactly one thing: *this is the moment you are pointing at*. The receipt slab,
the CTA, the focus ring, the found tile's outline, one word of the H1, the
active nav item, the timecode worth clicking, the ledger's unit. If everything
is gold, nothing is.

`--gold-hi` (#f6cd78) is the hover step of a gold fill. `--gold-ink` (#120c02,
10.25:1 on gold) is the text on it — never `--pitch` and never black. The alpha
ladder (`--gold-6` / `-12` / `-24` / `-44`) is the accent as a *ground*: a wash
under a receipt, an active chip, a landing pad, a dashed "next" outline.

### Lime — the seen channel

**Seen** (`--seen` #c9f938, 15.68:1 on `--plate`) with `--seen-ink` (#0a1400,
15.36:1 on it) and the alpha ladder `--seen-10` / `-24` / `-48`.

### The state tones

Five tones, each with three tokens: `--tone-X` is the ink, `--tone-X-bg` its
ground, `--tone-X-line` its border. Ground and border are **mixed from the ink**
— `color-mix(in srgb, var(--tone-X) 10%, var(--plate))` and the same at 26% —
which is the pattern the gold and seen alpha ladders already use, so a tone
needs one hex and not three.

- **Grass** (`--tone-ok` #71d083): done, ready, present, allowed. 10.14:1 on
  `--plate`, 8.67:1 on its own ground.
- **Orange** (`--tone-warn` #ffa057): deferred, cancel requested, held. 9.56:1 /
  8.19:1. **This is the one hand-held colour in the system and the one place the
  palette is extended rather than read out of v5**, which has no state tones:
  the obvious pick, the vendored dark `amber-11` #ffca16, sits 7° of hue from
  `--gold` and would read as the accent, which the One Signal Rule forbids.
- **Signal Red** (`--tone-bad` #ff9592): failed, refused, drifted, degraded.
  9.14:1 / 7.86:1. The dark `red-11` already vendored in `dashboard.css`.
- **Instrument Blue** (`--tone-work` #70b8ff): running, indexing, mid-pipeline.
  9.16:1 / 7.88:1. The dark `blue-11` already vendored.
- **Fg2** (`--tone-wait` / `--tone-neutral`): queued, skipped, absent,
  unrecognised. It is `--fg2` itself — a waiting state is the absence of a
  colour, not a fifth one.

Provenance, so nobody re-derives it: ok / bad / work are the dark Radix steps
already vendored in `dashboard/static/dashboard.css` (`grass-11`, `red-11`,
`blue-11`), carried over unchanged because they were measured for a dark ground
and this ground is darker still. warn is new and hand-held. wait is ours.

### The film filters

The four looks a still can have are tokens, because "how bright is the wall" is
a system decision and not a per-page one: `--film-rest` (the wall at rest, the
projector idling), `--film-dim` (the wall while a query is running — everything
that is not the answer), `--film-lit` (the found frame, lit *past* the scrim,
not merely back to normal), `--film-band` (a still in an evidence wall you can
read ids off). Full brightness (`none`) is the hover of a band tile and the
resting state of a frame on the light table: a frame you are being shown is
never dimmed.

### Named Rules

**The Lime Rule** (hard, like the Two-Channel Rule). `--seen` and its alpha
ladder mark exactly one thing: **evidence the machine read off the screen** —
an OCR detection box, its tag, an on-screen-text line, the legend swatch that
names those, and the `seen` label of the on-screen-text channel. It is never a
second accent, never a highlight, never a hover, never a link, never a chart
series, never a "success" green, never a decoration, and never the ground of
anything larger than a detection box. If lime appears on a surface with no
on-screen-text evidence on it, that is a bug — delete it, do not re-tint it.
The whole point is that a viewer learns in one screen that lime = *the machine
read this off the slide*, which is the product's single hardest claim to make
in a picture. v3 leaked lime onto a rail dot and a video id; v4 fixed it; the
fix is now the contract.

**The One Signal Rule.** `--accent` (gold) means *this is the moment you are
pointing at*. See above. Gold and lime never touch the same object: gold is
what you asked for, lime is what the machine saw.

**The Word-and-Colour Rule.** Every state prints its own word. Colour is
reinforcement, never the message. A state you can only read by seeing hue is a
bug, and the four state vocabularies in this product are deliberately not
unified — no surface may invent a fifth.

**The Plate Rule.** A thing sits exactly one plate above what contains it:
`--pitch` page → `--plate` box → `--plate2` band inside the box. Three plates
of nesting is a card in a card and the answer is a hairline.

## Typography

**Text / display face:** Archivo VF (variable, `wght` 100–900), with
`system-ui` as fallback.
**Machine face:** JetBrains Mono VF (variable, `wght` 100–800), with
`ui-monospace` as fallback.
**There is no third face.** The serif is gone: the wordmark is set in Archivo
(see **The Font-Logo Rule**), so `--font-display` no longer exists as a
distinct family and Instrument Serif is retired from the product surfaces.

Both faces are vendored, latin subset, SIL OFL 1.1 — see **Fonts** below.

**Character.** Archivo is a grotesque with a wide weight axis that stays sturdy
at 200 across a 90px headline, which is what makes the display voice possible
without a second family. JetBrains Mono is chosen for one measurable property:
it draws `0`/`O` and `1`/`l`/`I` apart, which stops being cosmetic when the
machine strings on these surfaces include YouTube ids like `kCc8FmEb1nY`.

### The ladder

Sans, largest first: **display** (200) → **headline** (210) → **question** (250)
→ **quote** (330) → **lede** (340) → **body** (340, 16px; 15px below
`--bp-hand`) → **prose** (340, 15.5px) → **action** (600, 15px) →
**cell** / **cell-strong** (340 / 520, 14px).

Mono: **query** (13.5px) → **machine** (13px) → **log** (12.5px) →
**machine-sm** (11px) → **label** (10px, 0.19em, uppercase) → **label-sm**
(9.5px, 0.17em) → **tag** (8.5px, 0.14em) → **figure** (300, clamp to 2.5rem,
tabular).

**The weight law.** Five sans rungs and no sixth: **200** display, **250**
secondary display, **340** body, **500** strong, **600** action. Mono has
three: **400** text, **600** label, **700** tag. A variable face makes optical
adjustment free, so a value **within ±20 of a rung** is an optical tweak and
needs no amendment — that is what v5's 210 on the h2, 330 on the pulled quote
and 520/560 inside a line are. Anything further from a rung is a new primitive
and gets written here.

**The size floor.** 8.5px `tag` and 9.5px `label-sm` exist for marks drawn
**on top of an image** and for a ledger's column name — strings of two or three
tracked words that the reader is not asked to parse in sequence. Running text
never goes below `machine-sm` (11px), and on the dashboard the smallest text
that carries a fact is `label` (10px) in `--fg2`.

### Named Rules

**The Two-Channel Rule** (unchanged, and it is still the system). Mono means
the machine said it; sans means a human wrote it. Every timecode, duration, id,
`model_key`, `error_code`, count, clock, confidence, byte figure, query string
and tool call is mono and tabular. Every title, channel name, description,
sentence and label a human wrote is Archivo. The eye learns this in ten seconds
and then the page reads itself.

Note the one thing that changed shape: micro-labels (`label`, `label-sm`,
`tag`) are **mono**, because a column name, a channel name like `SEEN`, and a
status word like `SCANNING` are the machine's own vocabulary. Sentence-length
human text is never uppercased and never tracked.

**The Font-Logo Rule** (replaces the Wordmark-Only Rule, 2026-08-10). The
wordmark is set in the text face; there is no separate brand family and no
drawn mark in the lockup. See **The logo** below. Because the identity is now
carried by the *word*, the old fence — "exactly two selectors may name
`--font-display`" — is retired with the serif it fenced.

**The Dead-Feature Rule.** Do not set `font-feature-settings` for stylistic
sets you have not opened the file to confirm. `tabular-nums` (`tnum`) is the
one that matters and both vendored faces carry it. Every mono context sets
`font-variant-ligatures: none`, because JetBrains Mono ships `calt` and the
literal characters of an id are the information.

## The logo

The logo is **the word, and nothing else**: `vidtheque` in lower case, in the
text face, with the full stop in gold. The period is the receipt's full stop —
the product's whole argument is that an answer ends in a citation, and the mark
ends in a dot. It is one string, so it is a `<b>` with an `<i>` around the
period, not an image and not an SVG.

The two sizes in the system, both read out of v5 as shipped:

| slot | face | weight | size | tracking | line-height |
|---|---|---|---|---|---|
| **rail / masthead** (`.mark b`) | Archivo VF | **500** | **16.5px** (15px below `--bp-hand`) | **−0.035em** | 1.1 |
| **footer signature** (`.fmark b`) | Archivo VF | **250** | **clamp(2.2rem, 4.4vw, 4rem)** | **−0.045em** | **0.9** |

The period is `--gold`, `font-style: normal` on the `<i>`, and it inherits
everything else. The lockup is `display:flex; align-items:baseline; gap:12px` —
the gap is for what sits *beside* the word (a corpus count, a state), never for
a drawing.

**The refinement, so it is not re-relitigated.** v4 shipped the rail mark at
**460 / 17.5px / −0.028em** with the note "the font logo: the period is the
receipt's full stop". v5 tightened it to **500 / 16.5px / −0.035em**: heavier
so the word holds its own against a lit wall behind it, smaller and tighter so
it reads as a mark rather than as a heading. **v5's numbers are the contract.**
The footer signature is unchanged between v4 and v5 and is the one place the
word is allowed to be large — a signature, at the end, after the argument.

The favicon is **the v.** (Tom's pick, 2026-08-11): the wordmark's `v` with its
gold full stop, floating on a transparent ground — gold core, a 1-unit
`--gold-ink` keyline so the same bytes read on light and dark tab strips (the
keyline melts into dark chrome, and becomes the silhouette on white). It is the
word abbreviated, not a drawing beside it, so the tab and the rail lockup say
the same thing. It is a favicon and not a brandmark: it never appears inside a
page beside the word. The drawn film frame it replaced lives in git history.

## Motion — the law

**Movement only where the machine is working.** (Tom, locked 2026-08-10.)
Motion on these surfaces is an honest signal that something is running; it is
never atmosphere. If you cannot name the machine work a moving thing is
reporting, it does not move.

**Sanctioned motion, and what each one reports:**

| motion | what it means | token |
|---|---|---|
| the projector gate's slow drift on a wall of stills | the projector is running / the corpus is being watched | `--drift-gate` (150s, alternate) |
| an evidence wall tracking sideways at a constant few px/s | this corpus was watched, and there is more of it than fits | `--drift-band` (900s linear) |
| a query typing itself, with a caret that blinks **only while typing** | the agent is asking | `--blink` |
| the wall dimming and one tile lighting | the search ran and this is the hit | `--t-film`, `--t-state` |
| a frame lifting off the wall onto the light table | found it — this frame is the evidence | `--t-lift`, `--ease-lift` |
| detection boxes acquiring, staggered | the machine is reading the screen | `--t-acquire`, `--ease-acquire` |
| a counter ticking up | counts are counts | — |
| a panel un-veiling as its data arrives | the answer landed | `--t-veil` |

**Banned outright, with no exception and no "but it's subtle":**

- pulsing or breathing dots, badges or rings;
- scan lines, CRT flicker, film grain, vignettes that move;
- ambient glow, halos, glimmer, shimmer, aurora, gradient animation;
- decorative hover motion — no lift, no scale, no translate, no wiggle, no
  parallax, no tilt, on anything, ever;
- entrance animation on scroll (fade-up, stagger-in) for content that is simply
  there;
- spinners as decoration. A spinner is allowed only while a real request is in
  flight, and it is replaced by the word for the state as soon as one is known.

**Hover may change colour, not geometry.** A hovered control may move its
ground, ink, border or image filter, at `--t-fast`. It may not move, scale or
shadow-lift. `--film-band` → `none` on a wall tile is the sanctioned example:
pointing at a still un-dims it, and nothing shifts.

**Everything freezes under `prefers-reduced-motion: reduce`,** and freezing
means **painting the end state**, never dropping the content: drifts stop,
carets stop blinking, transitions go to `none`, veiled things are simply
visible, `scroll-behavior` goes to `auto`. A surface that hides information
when motion is reduced has failed the rule twice. The lab's `?still=1` switch
does the same thing for screenshots; a product surface with a demo animation
must offer the same escape.

## Layout, shape and depth

**Chassis.** A page is a stack of **beats**: full-bleed bands on `--void` or
`--pitch`, separated by a `--seam` hairline, each padded `--beat` vertically,
with their content in a `--maxw` (1460px) column at `--gut` gutters. Inside a
beat: `--gap-head` under a heading block, `--gap-block` before a major object,
`--gap-copy` between the lines of a copy stack.

**Measures.** Prose is capped and grids are not: `--prose` 70ch for running
text, `--lede` 60ch for a lede (46ch when it is set over imagery), `--quote`
58ch for a pulled sentence, `--note` 74ch for a machine note.

**Spacing.** Layout spacing — gaps, tracks, section rhythm — is on the 4px grid
(`--s1` 4px … `--s12` 48px) or one of the fluid clamps above. **Component
padding is optical** and lives in the component's own entry (a 52px query bar
is `0 13px`, a panel head is `9px 14px`): those are read out of v5 and are not
required to land on the grid, but a *new* component's padding is a new
primitive and gets written here.

**Breakpoints**, and what each one is for:

- **`--bp-stack` 1120px** — two-column hero and split zones become one column;
  side-by-side plates stack with the still above its text.
- **`--bp-hand` 780px** — body drops to 15px, the wordmark to 15px, chrome
  tightens, wall tiles shrink, on-image tags (`typography.tag`) are dropped
  rather than shrunk, and multi-column grids go to one column.

A surface may add **one** breakpoint of its own for a real structural change
(the dashboard's tables stop being tables somewhere around 52rem) and documents
it in its own section here. It may not add a third for taste.

**The page body never scrolls horizontally at any width.** A grid too wide to
stack scrolls inside its own wrapper, which is `position: relative` so an
absolutely-positioned child cannot escape the clip. Every slot that prints a
corpus string — a title, an error, an id — carries `overflow-wrap: anywhere`
(`anywhere`, not `break-word`: only `anywhere` also reports a zero min-content
width, which is what stops a flex or grid parent being *widened* by an
80-character token).

**Shape: there is no radius.** The reset is `*{border-radius:0}` and a rounded
corner anywhere is a bug. Nothing is a circle. This is not a style preference —
a frame has square corners, and every box on these surfaces is either a frame,
a plate, or a rule.

**Borders.** 1px `--seam` is the hairline that separates; 1px `--seam2` is the
edge of a real box. 2px exists in exactly two places and both are gold: the
found tile's inset outline and the focus ring. A dashed 1px `--gold-44` at
`-4px` offset is the one honest-about-being-next affordance (a roadmap slot);
dashed means nothing else.

**Depth: a shadow means lifted off the wall.** Four things in v5 cast one and
all four are objects that have been taken *out* of the room and put in front of
you: the light table, a frame in flight, the booth log, the query bar.
`--drop` is that gesture; `--drop-sm` is its smaller sibling for a control.
Everything else — panels, ledgers, table rows, nav, pills, chips — has **no
shadow at all** and is separated by plate and hairline. `--edge` (a 1px inset
white hairline) is not elevation; it is the glass in front of a still.

The signature detail on a panel is the **corner tick**: a 12×1px and a 1×12px
gold rule meeting at the top-left corner, drawn with `::before`/`::after`.
It is the mark of a plate on a light table, it costs no box, and it is the
system's one ornament. One per panel; never on all four corners.

## Per-surface guidance

The same world at three densities. Function, data and copy still come from
`docs/design/*.md`; this section is about how much of the world each surface
spends.

### The landing page — the maximal expression

**v5.html is the reference.** Full-bleed imagery, the display ladder at its top
rungs, the drifting wall, the lift, the light table, the evidence wall, the
booth log. This is the only surface where a beat may exist purely to make an
argument, and the only one that may spend a whole viewport on one idea. Motion
inventory as v5 ships it; nothing is added to it without Tom.

### The demo page — the same world, selling, functional-first

The demo is the landing's continuation, not a second product and not a
stripped-down clone. It keeps the ground, the gold, the lime, the faces, the
zero radius, the wordmark, the receipt slab, the frame-and-detection treatment
and the motion law — a visitor arriving from the landing must not feel a seam.

What changes is the ratio: **the demo's job is to be used.** The search input,
the results, the receipts and the frames are the page; imagery serves a result
rather than a mood. So:

- The hero is at most one screen and it contains the working input. No beat
  exists that a visitor cannot act on.
- The display ladder is used one rung down from the landing: `headline` for the
  page's one big line, not `display` at 5.6rem.
- Motion is only the motion that reports *this visitor's* query running —
  typing, dimming, lifting, acquiring, counting. The ambient gate drift is a
  landing move; on the demo, an idle wall is idle.
- Every result carries its receipt (`youtu.be/ID?t=`), every OCR line carries
  its lime box, every machine string is mono. The demo's whole persuasion is
  that the receipts are real.
- `docs/design/demo-site.md` wins on function, data, clamps and copy —
  including token discipline: caps, `has_more`, and server-side clamps are not
  design decisions.

### The dashboard — the minimalist end of the same system

A true minimalist, informative control surface. Same world, no spectacle: the
operator is scanning sixty rows at 03:00 over an SSH tunnel.

- **Density first.** 34px table rows, 14px cells, 8px padding, hairline
  separation, sticky heads on `--plate2`. Information per screen is the metric.
- **The tones do the talking.** This is the surface the five state tones exist
  for. Every state is a word in its tone; the pill is the primitive.
- **Gold stays scarce.** The active nav item, the focus ring, the sorted
  column's underline, the timecode worth clicking, the hovered row's 1px inset
  edge. Nothing else.
- **Lime only where there is on-screen-text evidence** — the OCR overlay and
  its line list on video detail. On every other dashboard page there is no
  lime at all. (The Lime Rule.)
- **No imagery as decoration.** Keyframes appear because they are the data.
  There is no hero, no wall, no drift, no lift, no scrim. The dashboard's only
  motion is a live countdown ticking and a real request in flight.
- **No display type.** The ladder starts at `headline` for a page title and
  drops immediately to `label`/`cell`/`machine`. The wordmark appears twice —
  rail and footer — and is the only brand gesture on the surface.
- **No shadow at all**, corner ticks used at most once per page (the signature
  panel), and no card ever.
- `docs/design/dashboard.md` wins on function, data, clamps and copy.

## Fonts — one canonical location

The two faces are vendored `.woff2`, latin subset, SIL OFL 1.1, with their
licence texts beside them. **The canonical location is
`mcp/src/vidtheque_mcp/public/static/fonts/`:**

| file | family | axis | bytes |
|---|---|---|---|
| `archivo-latin-wght-normal.woff2` | Archivo | `wght` 100–900 | 34,928 |
| `jetbrains-mono-latin-wght-normal.woff2` | JetBrains Mono | `wght` 100–800 | 40,404 |

with `Archivo-OFL.txt` and `JetBrainsMono-OFL.txt`. Both files are byte-
identical to the lab's copies under `lab/versions/v5-assets/fonts/`, and the
JetBrains Mono file is byte-identical to the one the dashboard already shipped
(`md5 b058178d…`). Provenance: the fontsource variable packages, filenames
verbatim; the JetBrains Mono entry in
`dashboard/static/fonts/PROVENANCE.md` covers that file, and Archivo arrived
with lab v4 (commit `1251269`).

**Rules for the builders:**

1. `public/static/fonts/` is the **document of record**. New face, new subset,
   new version: it lands there first.
2. The dashboard keeps a **byte-identical copy** at
   `dashboard/static/fonts/` — copied, never diverged — because
   `public/static/*` is only routed under `VIDTHEQUE_PUBLIC_READONLY=1`
   (`public/__init__.py`) while the dashboard's own asset route is always
   registered. A dashboard font served from `/static/fonts/…` 404s in a private
   deployment. Archivo is already copied there; Inter and Instrument Serif are
   retired and their files and licence texts come out in the rebuild commit.
3. **`@font-face src` is relative** (`fonts/…woff2`), never built from
   `PUBLIC_URL`. These surfaces are served through an SSH tunnel on a port
   nobody predicted.
4. `font-display: block` for both, matching v5: these faces carry the display
   voice and a FOUT on a 5.6rem headline is worse than 100ms of nothing. Only
   the text face is preloaded.
5. **`public/__init__.py`'s asset route currently types every non-`.css` file as
   `text/javascript`.** Whoever ships fonts under `/static/fonts/` adds the
   `.woff2` media type there in the same commit, the way
   `dashboard/__init__.py` already did.

## Migration notes for the rebuild

The old system is pinned by tests. Whoever gets there first updates them, in
the same commit as their CSS, and says so:

- `test_dashboard.py::test_the_dashboard_palette_matches_the_demos` asserts
  twelve `--bg/--fg/--muted/--line/--accent/--raised` declarations (six per
  scheme, two schemes) and that the two files agree. The system is now
  single-scheme, so the expected count is **six**. Keep the assertion that the
  two files agree — that is what stops the surfaces becoming two visual worlds,
  and it is the reason the six role aliases exist in the frontmatter above.
- `test_dashboard.py::test_both_schemes_and_a_mobile_viewport_are_declared`
  expects two `theme-color` metas and `content="light dark"`. Dark only now:
  one `theme-color` (`#040405`) and `content="dark"`.
- Any test asserting a radius, a serif wordmark, `--font-display`, or a
  warm-paper hex.
- `.impeccable/design.json` is a generated mirror of this file's frontmatter and
  is stale until regenerated; it is not a second source of truth.

## Do's and Don'ts

### Do:

- **Do** put every timecode, duration, id, `model_key`, `error_code`, count,
  clock and byte figure in the mono face with `tabular-nums` and ligatures off.
  **The Two-Channel Rule** is the system.
- **Do** print the word for every state, in its tone. Colour reinforces.
- **Do** keep gold for "this is the moment you are pointing at", and lime for
  "the machine read this off the screen". Nothing else gets either.
- **Do** separate with plates and hairlines. A panel is a plate, an edge and a
  heading.
- **Do** name the machine work behind anything that moves — and freeze it all
  under `prefers-reduced-motion`, painting end states.
- **Do** give every image explicit `width`/`height` or a fixed-aspect box on
  `--black`.
- **Do** write `var(--token)`. If the token does not exist, amend this file in
  the same commit.
- **Do** keep the contrast sweep green: text ≥4.5:1, non-text marks ≥3:1, with
  `--fg3` the single measured exception and fenced as above.
- **Do** clamp lists server-side and print `has_more`, never a total.

### Don't:

- **Don't** add a border radius. Anywhere. The reset is zero.
- **Don't** spend lime on anything that is not on-screen-text evidence. **The
  Lime Rule** is as hard as the type rule.
- **Don't** add a second accent, a chart palette, or a "success green" beside
  the tones.
- **Don't** animate anything you cannot name the machine work for — and never
  a pulse, a scan line, an ambient glow, or a hover transform.
- **Don't** ship a shadow on anything that has not been lifted off the wall.
  No cards, no glass, no gradient used as a tonal ramp for looks. (*Gradient*
  means a decorative ramp, not the CSS function: the hero scrim is two
  layered gradients doing a legibility job, and a hatch encoding a fact is
  fine. A surface that fades because fading looks expensive is not.)
- **Don't** reach for film sprockets, clapperboards, reel icons or faux-CRT.
  "Projection room" is a derivation, not a decoration. (The old film-frame
  favicon was the one sanctioned exception; it retired 2026-08-11 for the v.)
- **Don't** introduce a third font family, or a serif for the wordmark.
- **Don't** put a raw colour, an off-ladder font size, an off-ladder weight, or
  an off-4px layout spacing value in a stylesheet.
- **Don't** build an absolute asset URL from `PUBLIC_URL`. Relative or
  root-relative, fonts included.
- **Don't** add an inline `<script>`. The pages stay CSP-ready.
- **Don't** inline a frame as base64 by default. Frames go by authenticated URL;
  base64 is the opt-in, with the correct mimeType.
- **Don't** rename the product's words. `cue`, `chunk`, `shot`, `keyframe`,
  `dup_of`, `stage`, `model_key`, `job item`, `not_before`, `index_state`,
  `data_status` are the vocabulary and a designer does not get to tidy them.
- **Don't** import a colour package, add a build step, or make a runtime request
  to anything off this box.
