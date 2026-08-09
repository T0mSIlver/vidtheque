# Dashboard visual direction + agent tooling — research, 2026-08-09

**Scope.** Tom asked for the phase-1 dashboard (`/dashboard`, shipped functional
and visually plain) to become "way more polished and modern, production-ready",
and for an honest read on the tooling people are using for agent-driven frontend
work in 2026. This document is (1) three candidate visual directions with a pick,
(2) tool-by-tool verdicts with install steps, (3) a workflow that lets several
phase agents build UI in parallel without the surfaces diverging.

**Method and honesty note.** Every claim about an external tool carries a URL.
Every claim about this repo or this box carries a path or a command I ran.
Where I could not verify something — chiefly exact font byte sizes and a couple
of licence files I did not open individually — I say so in §5 rather than
guessing. Nothing outside this file was changed; nothing was installed.

**Binding constraints** (from `docs/design/dashboard.md` §10.2 and
`docs/design/demo-site.md` §6, both read in full): server-rendered Jinja2 with
autoescape + plain ES modules, **no build step**, **no external requests at
runtime**, light and dark schemes, strict XSS posture (no `| safe`, no HTML
sinks, `safeUrl()` on every URL), and it must work behind an SSH tunnel on an
arbitrary port (phase-2's relative-`/frames/…` lesson).

---

## 0. What is actually true today — the incumbent world, and what is on this box

### 0.1 The incumbent visual world is not "nothing"

`mcp/src/vidtheque_mcp/dashboard/static/dashboard.css` is 885 lines and opens
with a stated design position, not boilerplate:

> Same six custom properties as `public/static/style.css` … Same system fonts,
> same hairline rows, same single amber accent, no cards, no shadows.

The palette is warm, not neutral: `--bg: #fbfaf9`, `--fg: #1b1a19`,
`--accent: #b45309` (a burnt amber), dark scheme `#131313` / `#e9e6e3` /
`#f0a55a`. On top of the shared six the dashboard adds `--panel` and a
six-value state palette (`--tone-ok|warn|bad|work|wait|neutral`), plus
`--measure: 74rem` / `--prose: 46rem`.

Type is the system stack (`-apple-system, BlinkMacSystemFont, "Segoe UI",
Roboto, …`) at `400 16px/1.55`, mono is `ui-monospace, SFMono-Regular, Menlo,
Consolas`. There are already the right small primitives: `.num` sets
`font-variant-numeric: tabular-nums`, `.unit` is a `0.7em` muted suffix,
`.sr-only`, a single `:focus-visible` ring in the accent, and a
`prefers-reduced-motion` block.

**Read: this is a coherent, deliberately quiet, warm-paper typographic world
that has been under-built, not a wrong world badly executed.** That distinction
decides §1: the honest options are "expand it", "replace it with the achromatic
console lane", or "replace it with a domain-grounded world". Not "add polish to
something that has no position".

### 0.2 Three constraints in the test suite that any redesign must plan around

These are real and I verified each one:

1. **The six shared tokens are asserted equal across the two surfaces.**
   `mcp/tests/test_dashboard.py:561-577` parses both stylesheets and asserts the
   dashboard's `bg/fg/muted/line/accent/raised` (both schemes, 12 values) are
   the demo's, in order — *"the thing that stops the copy drifting into a second
   visual world"*. So a palette change is a **two-file change**, and any
   direction that gives the dashboard its own ground either moves the demo with
   it or renegotiates that test. This is a feature: it is the divergence guard
   §3 wants, already written.
2. **No inline `<script>`, asserted.** `mcp/tests/test_dashboard.py:550-555`
   strips the one module tag and asserts `"<script" not in body` — *"the page
   stays CSP-ready"*. This directly forbids the usual no-FOUC theme-toggle
   trick (a tiny blocking inline script that reads `localStorage` before paint).
   A three-state light/dark/system toggle is still shippable; §1.5 says how, and
   what it costs.
3. **Both schemes and a mobile viewport are asserted**, including
   `content="light dark"`, two `theme-color` tags, a `prefers-reduced-motion`
   block, and `max-width: 52rem` as the breakpoint where *"the table stops being
   a table rather than scrolling the page sideways"*
   (`test_dashboard.py:580-590`).

### 0.3 What is already installed on this box (read-only checks)

| thing | state | evidence |
|---|---|---|
| Node / npm | v24.18.0 / 11.18.0 | `node --version`, `npm --version` |
| `chromium` / `google-chrome` on PATH | **absent** | `which chromium chromium-browser google-chrome` → not found |
| Playwright browser binaries | **present** — `chromium-1223`, `chromium-1234`, `chromium_headless_shell-1223/1234`, `ffmpeg-1011` | `ls ~/.cache/ms-playwright` |
| Puppeteer browser binaries | **present** — `chrome/linux-151.0.7922.71` and `chrome-headless-shell/linux-151.0.7922.71` | `ls ~/.cache/puppeteer` |
| `playwright` npm package | present in the **npx cache** (two entries), not global | `ls ~/.npm/_npx/*/node_modules/playwright*` |
| `@playwright/cli` | **absent** | not in `~/.npm/_npx/*` nor `/usr/lib/node_modules` |
| Python `playwright` | **absent** from the repo venv | `.venv/bin/python -c "import playwright"` → ModuleNotFoundError |
| system fonts | 58 families, all libre defaults (DejaVu, Liberation, Noto Color Emoji, Unifont, IPAGothic) — **no Inter, no Geist, no commercial faces** | `fc-list : family` |
| vendored web fonts in repo | **none** — no `.woff2`/`.woff`/`.ttf` anywhere outside `.git` | `find . -name "*.woff2" …` |

The prior QA run confirms the browser story: `research/website-test-2026-08-09.md`
records the driver as *"Playwright 1.62.1 / Chromium, real page loads"*. So
**real-browser screenshotting on this box is already a solved problem with
zero new downloads of browser binaries** (~700 MB is the usual cost of a cold
Playwright install — [playwright.dev](https://playwright.dev/docs/browsers) —
and we have already paid it, twice).

One consequence for §1: because the box has no Inter and no Geist installed
*system-wide*, a headless screenshot pass will render whatever the page's own
`@font-face` serves. That is correct behaviour — the page must vendor its fonts
anyway — but it means "it looked fine in my screenshot" is only true after the
`.woff2` files are actually committed. Do not evaluate a type direction from a
screenshot taken before the files land.

### 0.4 What impeccable already gives us

Vendored at `.claude/skills/impeccable/` (v4.0.4, Apache 2.0), committed in
`354e8ab`. Read its `SKILL.md` and the `init` / `document` / `new-work`
references. The relevant capabilities:

- **Modes.** `Operate` (dashboard) vs `Persuade` (demo page) is exactly the
  split Tom has, and the skill treats mode as a per-surface property, not a
  per-product one: *"A tool's landing page is still Persuade"*.
- **`init` → PRODUCT.md**, product truth only; it explicitly refuses to record
  aesthetics. **Neither PRODUCT.md nor DESIGN.md exists in this repo today** —
  I checked.
- **`document` → DESIGN.md** at project root, in the
  [google-labs-code/design.md spec](https://raw.githubusercontent.com/google-labs-code/design.md/main/docs/spec.md):
  YAML frontmatter carrying *normative* tokens (colors, typography, rounded,
  spacing, components) plus up to eight fixed-order markdown sections. This is
  the shared contract §3 is built on.
- **`new-work`** — the direction-selection flow, including
  `scripts/concept-seed.mjs` which forcibly randomises which of seven derived
  directions gets built, precisely so every run does not converge on the
  category default. Worth knowing before §1: my three directions below are
  *research input for Tom*, not a substitute for that roll if he wants a genuine
  replacement world.
- **A detector that scans URLs with a real browser**:
  `node .claude/skills/impeccable/scripts/detect.mjs --viewport 390x844 <url>`,
  with `--scope type,layout`, `--json`, and DESIGN.md-awareness
  (`--no-design-system` to disable). The hook is already on for this project;
  `.impeccable/config.json` carries one waiver (the lightbox's src-less `<img>`,
  committed `9e2392a`).

So a meaningful part of the "agent-driven frontend polish" stack is already in
the tree and already commit-tracked. That materially changes the verdicts in §2.

---

## 1. Design direction

### 1.1 What best-in-class actually does in 2026 — the evidence

I went looking for what the reference-class control surfaces do, not for trend
listicles. The load-bearing findings:

**Linear** is the density benchmark. Its type system is *entirely* Inter
Variable with the OpenType features `cv01` and `ss03` enabled globally, run at
an unusually fine weight ladder — 300 body, **510** as the signature weight, 590
for emphasis — with aggressive negative tracking at display sizes (−1.0 to
−1.6px at 48–72px). Its colour system is *almost entirely achromatic*: near-black
grounds (`#0f1011`, `#08090a`, floor `#010102` — deliberately not `#000000`),
white/grey text, punctuated by a single indigo-violet accent (`#5e6ad2`
background, `#7170ff` interactive). Code is set in Berkeley Mono.
([DesignMD](https://designmd.cc/benchmarks/linear),
[opendesigner.io](https://opendesigner.io/design-systems/linear-app),
[designlang](https://www.designlang.app/gallery/linear-app))
Row height is reported at 36px with keyboard-first navigation and "almost no
chrome" ([saasui.design](https://www.saasui.design/blog/7-saas-ui-design-trends-2026)).

**Vercel/Geist** shipped its typeface as open source under the SIL Open Font
License — Geist Sans for text and Geist Mono for code, explicitly Swiss-geometric,
designed for screen legibility.
([vercel.com/font](https://vercel.com/font),
[LICENSE.txt](https://github.com/vercel/geist-font/blob/main/LICENSE.txt),
[vercel.com/geist/typography](https://vercel.com/geist/typography))
That matters here more than the aesthetic does: it is a first-tier product
typeface we are *allowed to vendor*.

**Grafana's Saga** uses Inter as its primary family, chosen for its
"well-crafted number forms" — the thing that matters in a dense table.
([grafana.com/developers/saga](https://grafana.com/developers/saga/about/overview/),
[Saga announcement](https://grafana.com/blog/saga-design-system-shaping-the-future-of-user-experiences-at-grafana-labs/))
The practical density number that keeps recurring for data-heavy cells is
**13px / 1.4 line-height**
([FontAlternatives](https://fontalternatives.com/blog/best-fonts-dense-dashboards/)) —
note that this is a blog, not a spec, so treat it as a starting point to
measure, not a law.

**GitHub Primer** is the mature open counter-example: colour/typography/spacing
primitives published as JSON, a CSS implementation, and a `DataTable` component
added comparatively recently. ([primer.style](https://primer.style/),
[primer/css](https://github.com/primer/css),
[DesignSystems.one breakdown](https://www.designsystems.one/design-systems/primer))

**Radix Colors** is the piece I would actually steal: MIT-licensed, 12-step
scales where each step has a declared job (1–2 app/component backgrounds, 11
low-contrast text, 12 high-contrast text), shipped with light, dark and *alpha*
variants, distributed as **plain CSS custom properties** — light scales bind to
`:root`/`.light`, dark scales to `.dark`. ([radix-ui.com/colors](https://www.radix-ui.com/colors),
[usage docs](https://www.radix-ui.com/colors/docs/overview/usage),
[understanding the scale](https://www.radix-ui.com/colors/docs/palette-composition/understanding-the-scale))
No framework, no build step, and it solves the exact problem the current
six-token palette is going to hit the moment we need hover/pressed/selected
states in two schemes.

**The 2026 ambient expectations** for a tool of this shape, from the trend
surveys (weaker evidence, cited as such): keyboard-first with a Cmd-K command
palette treated as a default in anything with more than ~10 features; dark-first
with *low-contrast* panel borders; density personalisation; "every element must
earn its place".
([saasui.design](https://www.saasui.design/blog/7-saas-ui-design-trends-2026),
[hashbyt enterprise UI](https://hashbyt.com/blog/enterprise-ui-design))
I would take exactly one of these — the hairline low-contrast panel border — and
leave the command palette until there are enough destinations to warrant it
(there are five routes).

**What I did not find** and will not fake: no public design-token documentation
for Datadog, Supabase Studio or the Tailscale admin console. Search kept
returning Grafana *dashboards for* those products rather than their own design
systems. Treat any claim about their internals in this document's absence as
unverified.

### 1.2 The three directions

All three assume the same non-negotiable chassis, because it is where the
"production-ready" feeling actually comes from and it is direction-independent:

> **The chassis.** 4px spacing base. Table rows 34–36px with 13px/1.4 cells.
> Tabular numerals on every number, every duration and every timecode. Sticky
> table head over a `--panel` ground. Hairline separation, never shadowed cards.
> One `:focus-visible` ring, visible on rows. Every state is a word *and* a
> colour (already the rule — `dashboard.css` header comment). Explicit
> `width`/`height` on every thumbnail. Zero layout shift on the steady state.

The directions differ in world, not in rigour.

---

#### Direction A — "The archive slip" (expand the incumbent)

*Amplify the warm-paper typographic world already in the tree instead of
replacing it.*

**Typography.** A real display face against a real text face, both OFL.
Display: **Instrument Serif** (or **Newsreader**, variable) for the masthead,
page titles and stat-panel numbers only — 28/34/44px, weight 400, tracking −1%.
Text: **Inter Variable**, 14px/1.55 body, 13px/1.4 table cells, weights 400 /
510 / 590, with `font-feature-settings: "cv01" 1, "ss03" 1` and `"tnum" 1` on
numerics. Mono: **IBM Plex Mono** 12.5px for ids, model keys and error codes —
Plex is warmer and more humanist than Geist Mono, which is the point of this
direction.

**Density.** The chassis, but with **generous vertical air between sections**
(48px) and a shorter measure for prose (`--prose: 46rem`, already present).
Documents that happen to contain tables, rather than a grid with text in it.
Panels are separated by rules and whitespace, never by boxes.

**Colour.** Keep the warm ground and the burnt amber, but rebuild both schemes
on a **Radix `sand` neutral scale + `amber`/`orange` accent + `grass`/`red`/
`amber`/`blue` for states**, vendored as ~28 custom properties. That gives the
hover/selected/disabled steps the current six tokens cannot express, without
changing the identity a visitor already saw. Dark scheme leans warm-charcoal,
not blue-black.

**Tables / stat panels / detail.** Stat panels are a small caps label, a large
serif number, a `0.7em` muted unit — the `.unit` primitive already exists.
Tables are hairline-ruled with no zebra; the hover state is a 2px accent bar in
the left gutter, not a fill. The detail page reads as a *record card*: header
block, then the seven provenance stages as a definition list, then the browsers.

**What this direction says about the product.** "This is a library, and I keep
the accession records." It frames vidtheque as an archive with a conscience —
provenance-first, citation-first, the thing that sends you back to the source.
It is the most honest match for the citation contract and the least likely of
the three to look dated in three years. Its risk is that "warm and quiet" reads
as *unfinished* to someone arriving from Linear, which is precisely the
complaint that started this work.

---

#### Direction B — "The instrument panel" (the console lane, played straight)

*The Linear/Vercel achromatic console, executed at full fidelity.*

**Typography.** One family: **Inter Variable** everywhere, `cv01`+`ss03` on
globally, weight ladder 400 / 510 / 590 exactly as Linear runs it. 13px/1.4
cells, 14px/1.5 body, 20/24/30px headings with −1.5% tracking above 24px. Mono:
**Geist Mono** (OFL) at 12.5px for every machine string — ids, `model_key`,
`error_code`, timecodes. No display face at all; hierarchy comes from weight,
size and colour, never from a second personality.

**Density.** Maximum defensible. 32px rows on the videos table, 28px on the cue
and OCR lists. Sections separated by 1px hairlines at `neutral-6`, not by
whitespace. A persistent left rail with the five routes, collapsing to a top bar
under 52rem. Page gutter 20px; content width stays `74rem`.

**Colour.** Achromatic by construction: Radix `slate` (or `gray`) 12-step, both
schemes, plus **one** cool accent — and here I would break with the incumbent
and go cool, because the amber is doing double duty as "brand" and "warning" and
in a console that ambiguity is a defect. States use `grass`/`amber`/`red`/`blue`
at steps 9 (solid) and 3 (background) with step 11 text, which is what the Radix
scale is for. Dark mode is the *primary* scheme, light is the correct
translation — the reverse of today.

**Tables / stat panels / detail.** Stat panels are a single row of large
tabular numbers with a muted label beneath, hairline-separated, no boxes at all.
The videos table gets a sticky head, column alignment discipline (text left,
numbers right, states centred as pills), and row selection on click. The detail
page is a two-column split at ≥64rem: persistent metadata/provenance rail on the
left, the four browsers as a tabbed panel on the right.

**What this direction says about the product.** "This is infrastructure, and it
is serious." It buys instant credibility with the audience that already lives in
Linear and Vercel, and it is the safest possible answer to "make it look
production-ready". Its cost is that it says nothing whatsoever about *video* —
it is a world any competent tool could wear, and it makes the demo page a
stranger to its own dashboard.

---

#### Direction C — "The cutting room" (domain-grounded, my pick)

*Derive the world from what this product actually handles: timecode, shot
boundaries, contact sheets, and the log a lab keeps of what it processed.*

This is the direction the Anthropic frontend-design skill's own doctrine points
at — *"identify the concrete subject… use the subject's own materials,
instruments and vernacular as sources"* — and it is the only one of the three
that could not be pasted onto another product.

**Typography.** A deliberate two-channel split that *encodes meaning*:

- **Human text** — **Inter Variable**, 14px/1.5 body, 13px/1.4 cells, weights
  400 / 510 / 590, `cv01`+`ss03`. Titles, channels, prose, labels.
- **Machine text** — **Geist Mono** (OFL) or **JetBrains Mono** (OFL), 12.5px,
  weight 400/500. And here the rule is absolute and legible from across the
  room: **every timecode, every duration, every id, every `model_key`, every
  `error_code`, every count is mono and tabular.** The eye learns in ten seconds
  that mono means "the machine said this" and sans means "a human wrote this".
  This is the same move `demo-site.md` §6.3 already makes for OCR text
  ("monospaced behind a dashed rule") — I am promoting an existing local rule to
  a system-wide one.
- Display: **none, or Geist Sans at 30–44px** for the masthead. The signature is
  not a display face; it is §"the signature" below.

**Density.** The chassis, hard, with one exception: the timeline and keyframe
strip get *more* room than a grid would give them, because they are the reason
the page exists. 34px table rows; 8px gutters inside the strip; sections at 32px.

**Colour.** Radix 12-step, **`slate` or `sage` neutral**, both schemes, dark
treated as a first-class peer rather than a `prefers-color-scheme` afterthought.
The accent is a single saturated signal used *only* for "this is the moment you
are pointing at" — the playhead, the selected shot, the focus ring, the active
nav item. I would keep it warm (the incumbent amber, moved onto the Radix
`amber`/`orange` scale so it has 12 steps) rather than go cool, because it
reads as tungsten/lamp rather than as a generic SaaS indigo, and because it
keeps continuity with the demo page and with test `test_dashboard.py:561`.
State colours come from `grass`/`amber`/`red`/`blue`/`iris` at 3/9/11.

**Tables / stat panels / detail.**

- **Overview** is a *ledger*, not a dashboard of cards: one hairline-ruled block
  of large tabular numbers (videos · hours · cues · keyframes · OCR lines), then
  the coverage gaps as a single honest sentence with a number, then channel and
  tag rollups as compact two-column lists, then declared models beside live
  vector state as a diff-shaped panel that goes red on drift. No boxes, no
  shadows, no charts (§1 non-goal 5 forbids time axes and I would not fight it).
- **Videos table** — 16:9 thumbnails at 64×36 with explicit dimensions,
  filmstrip-notched left gutter, coverage as three glyph+word pills (`t o f`),
  state as a word in its tone colour, dates and durations right-aligned mono.
- **Video detail is where the boldness is spent.** Two elements carry the whole
  direction and nothing else competes with them:
  1. **The shot timeline** as a full-width horizontal band under the header —
     one segment per shot across the true duration, kept keyframes solid,
     `dup_of` shots at 30% with a hatched fill, chapter marks as ticks above,
     and a hairline playhead that tracks whichever cue/frame is selected. This
     is the "signature element" the frontend-design skill asks for and it is
     *made of real data we already store* (`keyframes GROUP BY shot_id`).
  2. **The OCR box overlay** — the normalised 0–1 boxes drawn over the frame
     (they are already normalised at write time,
     `pipeline/store.py:375-379`). Boxes in the accent at 1px with a 12%
     accent-alpha fill; hovering a line in the list lights its box and vice
     versa. `docs/design/dashboard.md` §5.3 already calls this "the single most
     convincing thing on the page". Design should agree with it out loud.
  - The provenance panel is a **stage rail**: seven rows, pipeline order, each a
    state glyph + stage name + `model_key` in mono + elapsed in mono, `absent`
    stages rendered as a dimmed rule. It should read like a processing log,
    because it is one.
- **Jobs (phase 2)** inherits the same rail: `not_before` as a live mono
  countdown is the hero number, `attempts/max_attempts` beside it, and the
  `job_events` tail set as a log — mono, hairline-ruled, timestamps in the
  muted step. The 03:00 test in `docs/design/dashboard.md` §5.4 is a *typography*
  test as much as an information one.

**What this direction says about the product.** "I watched these videos frame by
frame, and I can show you exactly what I saw and when." It makes the dashboard
look like the instrument of a specific craft rather than the admin panel of a
generic service, and it turns the two things nobody else has — the shot timeline
and the OCR overlay — into the identity instead of burying them in panel four.
Its risk is thematic drift: "cutting room" must stay a *derivation* (mono for
machine truth, timeline as spine, contact-sheet gutters) and never become
decoration (no film sprockets, no clapperboard icons, no faux-CRT). If it starts
looking like a movie poster it has failed.

### 1.3 The pick

**Direction C for `/dashboard`. Direction A for `/`. Direction B is the standing
safe answer if Tom wants zero risk.**

Why C over B: B is genuinely excellent and genuinely borrowed. Every argument
for it is an argument about *credibility with an audience that has seen Linear*,
and none of it is an argument about vidtheque. C keeps every one of B's density
and rigour rules — they are the shared chassis, not B's property — and spends
the differentiation budget on the two artefacts that only this product has.
For a single-operator self-hosted video index, where the user is the person who
built it and the job is "explain the index to me at 03:00", the mono/sans truth
split is worth more than a fashionable accent, because it is *readable
information architecture wearing a visual identity*, which is the only kind of
identity an Operate surface should have.

Why not A for the dashboard: A is right about the product's soul and wrong about
this surface's job. A serif display face and 48px section gaps are the correct
answer for the page that sells; they are the wrong answer for a table you scan
sixty rows of. The incumbent world's real problem is not that it is warm — it is
that it is *system-font-default at 16px with six tokens*, which reads as
"nobody made a decision here". C keeps the warmth (the amber, the sage/sand-ish
neutral) and adds the decision.

Why B stays on the table: it is the honest category standard, and impeccable's
own doctrine reserves a permanent "play the canon straight" exit for exactly
this situation. If Tom's instinct is "I just want it to look like a 2026 dev
tool", B executed at full fidelity is a completely respectable ship, and it is
cheaper — one family, one accent, no signature element to get right.

### 1.4 What transfers to the demo page, and what stays dashboard-only

`demo-site.md` §6 is explicit: *"a search engine, not a dashboard"*, and phase 4
turns the demo into a read-only projection of the dashboard. So the two surfaces
must share a spine and diverge in temperature.

**Transfers (shared, and the six-token test should keep enforcing it):**

- The neutral scale and the accent — same hues, same steps. This is what
  `test_dashboard.py:561` already asserts; a 12-step Radix-derived scale should
  extend that test from six properties to the full vendored set.
- The mono-means-machine rule. It is already half-present on the demo page
  (OCR snippets are mono, §6.3) — promoting it is a *clarification* of an
  existing decision, and it makes the provenance badges legible in one glance
  on both surfaces.
- Inter Variable as the text face, and the vendored `.woff2` files themselves
  (one copy, served from one static route, so the second surface costs zero
  extra bytes).
- The state palette and the badge grammar — border style + font + colour, never
  colour alone (`demo-site.md` §6.3's rule, which is also an accessibility rule).
- The focus ring, the reduced-motion block, the skip link, the `.sr-only`.

**Stays dashboard-only:**

- The density (32–36px rows, 13px cells). The demo page's measure and 16px body
  are correct for reading a column of results and wrong for scanning a grid.
- The left rail / persistent navigation. The demo has one page.
- The stage rail, the shot timeline, the OCR overlay, the ledger overview.
- The `--panel` second ground and the sticky table head.

**Demo-only warmth (Direction A's inheritance):** the display serif, the wider
line-height, the generous section rhythm, the illustrative empty state with the
three example queries, and the drawn film-frame favicon that `demo-site.md`
already specifies. The demo is `Persuade`; it is allowed a hero and a voice.
The dashboard is `Operate`; its brand lives in the details.

### 1.5 Typography: exactly what to vendor, and the mechanics under no-build

**The shortlist, all self-hostable, all SIL OFL 1.1:**

| face | role | licence evidence | why |
|---|---|---|---|
| **Inter Variable** | UI text, all three directions | OFL; packaged at [fontsource.org/fonts/inter](https://fontsource.org/fonts/inter/install), [@fontsource-variable/inter](https://www.npmjs.com/package/@fontsource-variable/inter) | the default for UI work; variable wght 100–900 with optical-size behaviour; `cv01`/`ss03` give the geometric single-storey forms Linear uses; number forms designed for dense data ([Saga](https://grafana.com/developers/saga/about/overview/)) |
| **Geist Mono** | machine text (C, B) | [SIL OFL, vercel/geist-font/LICENSE.txt](https://github.com/vercel/geist-font/blob/main/LICENSE.txt), [vercel.com/font](https://vercel.com/font) | drawn for code/data on screen; pairs with Inter without arguing |
| **JetBrains Mono** | machine text (alternate) | OFL, via [fontsource.org](https://fontsource.org/) | taller x-height, more distinct `0/O` and `1/l`; better at 12px than Geist Mono if we go smaller |
| **IBM Plex Mono / Plex Sans** | machine + text (A) | OFL, via [fontsource.org](https://fontsource.org/) | warmer, humanist; the right mono if the world stays paper-ish |
| **Instrument Serif / Newsreader** | demo display only | OFL per Google Fonts / Fontsource — **verify the licence file at download**, I did not open it | editorial voice for `/`, never for `/dashboard` |
| **Geist Sans** | text alternative | same OFL as Geist Mono | if Tom prefers Swiss-geometric over Inter's neutrality |

**Concrete type scale to start from (Direction C, dashboard):**

```
--font-sans: "Inter Variable", -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
--font-mono: "Geist Mono", ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;

body            14px / 1.5   weight 400   feature-settings "cv01" 1, "ss03" 1
table cell      13px / 1.4   weight 400   .num → tabular-nums
table head      12px / 1.3   weight 510   letter-spacing 0.02em, uppercase optional
label / meta    12px / 1.4   weight 400   colour: neutral-11
h3 / panel      15px / 1.35  weight 590
h2 / page       20px / 1.3   weight 590   letter-spacing -0.01em
h1 / masthead   30px / 1.15  weight 590   letter-spacing -0.015em
stat number     30px / 1.0   weight 510   tabular-nums, mono or sans by direction
machine string  12.5px/1.4   mono 400     ids, timecodes, model_key, error_code
```

**Self-hosting mechanics that satisfy "no build step, no external requests":**

1. Download the `latin` (and `latin-ext` if Tom wants it) **variable** `.woff2`
   from Fontsource — either the npm tarball or the direct download; Fontsource
   exists precisely for this and offers per-subset files
   ([fontsource.org](https://fontsource.org/)). Do **not** `npm install` into
   the repo; extract the two or three `.woff2` files and delete the rest.
2. Commit them to `mcp/src/vidtheque_mcp/dashboard/static/fonts/` (hatchling
   already ships package data under the package directory —
   `demo-site.md` §6 relies on this for the existing three static files).
   Serve from the **existing** static mount; add nothing to the route list.
3. Hand-write the `@font-face` blocks at the top of `dashboard.css` with
   **relative** `src: url("fonts/InterVariable-latin.woff2") format("woff2")`,
   `font-weight: 100 900`, `font-display: swap`, and a `unicode-range` matching
   the subset. Relative is required, not stylistic: phase 2's SSH-tunnel finding
   (`docs/design/dashboard.md` §8) is that absolute URLs built from `PUBLIC_URL`
   break on an arbitrary port, and a dead font URL is a worse version of a dead
   thumbnail.
4. **Preload** the text face in `base.html`
   (`<link rel="preload" as="font" type="font/woff2" crossorigin>`), and only
   that one — preloading the mono too costs more than the swap it prevents.
5. This is a **deliberate binary commit**, exactly the shape of the Jinja2
   dependency commit in phase 1 (`docs/design/dashboard.md` §8 table, row 10.2).
   It should be its own commit with its own message, and `CLAUDE.md`'s
   deterministic-builds discipline says the provenance (source URL, version,
   subset, licence file) belongs in the commit message or a
   `static/fonts/LICENSE` file. **Ship the OFL text alongside the fonts** — OFL
   requires the licence to travel with the files.
6. **Budget.** A latin-subset variable Inter `.woff2` is on the order of
   ~100 KB and a mono around ~30–50 KB — **I did not measure these and will not
   pretend to**; measure at vendor time and record the real numbers. If the pair
   exceeds ~200 KB, subset harder (Latin basic + the punctuation the UI actually
   uses) rather than dropping the variable axis.

**The honest zero-cost alternative:** keep the system stack and fix its actual
defects — add `font-variant-numeric: tabular-nums` everywhere numbers appear
(partly done via `.num`), tighten to 14px/13px, and add the weight ladder. This
gets maybe 60% of the perceived polish for zero bytes and zero commits. It is
the right call *only* if Tom decides vendoring binaries into a public repo is a
line he does not want to cross. It is not the recommendation.

### 1.6 Light + dark: the toggle, and the test that blocks the usual trick

Today both surfaces are `prefers-color-scheme` only. 2026 expectation is a
three-state control (system / light / dark). The mechanics under our
constraints:

- Define the light palette on bare `:root`, redefine only the changed tokens
  under `@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) }`,
  and again under `:root[data-theme="dark"]`. This is the standard three-state
  pattern and it is pure CSS.
- The toggle writes `data-theme` on `<html>` and persists to `localStorage` from
  `dashboard.js` (~15 lines added to the existing 144).
- **The catch:** `test_dashboard.py:550-555` asserts there is no inline
  `<script>` beyond the one module tag, "so the page stays CSP-ready". The
  standard no-flash trick is a tiny *blocking inline* script in `<head>`. With a
  deferred module you get a one-frame flash of the system scheme when the user's
  choice differs from it. Three honest options, in my order of preference:
  1. **Accept the flash** and make it invisible by having the module run first
     thing and by keeping `<body>`'s background token the only painted ground.
     On a local/tunnelled instance this is genuinely hard to see.
  2. **Move the theme choice server-side** — a cookie read in the Jinja context,
     `<html data-theme="{{ theme }}">` rendered. Zero flash, zero inline script,
     costs one cookie and one POST (and phase 3 is already building the write
     side with Origin discipline).
  3. Relax the test to allow one tiny inline `<script>` with a documented CSP
     hash. I would not; option 2 is strictly better here and the test is a good
     test.
  Recommend **option 2**, deferred to phase 3 where the write side lands, with
  option 1 shipping in the meantime.

---

## 2. Tooling and skills — verdicts

### 2.1 Anthropic's `frontend-design` — **SKIP for `/dashboard`; consider for `/`**

**What it is.** An Anthropic-published, Anthropic-verified plugin wrapping a
skill of the same name. Source:
[`anthropics/claude-code/plugins/frontend-design/skills/frontend-design/SKILL.md`](https://github.com/anthropics/claude-code/blob/main/plugins/frontend-design/skills/frontend-design/SKILL.md)
(I confirmed via `gh api` that this directory contains exactly one file,
`SKILL.md` — it bundles no references, no scripts, no data). Listed at
[claude.com/plugins/frontend-design](https://claude.com/plugins/frontend-design)
with 1,134,112 installs at the time of writing.

**Install.**
```
/plugin marketplace add anthropics/claude-code
/plugin install frontend-design@claude-code-plugins
```
(per [Boris Cherny's launch post](https://www.threads.com/@boris_cherny/post/DRDDB19kUZ5/to-get-started-add-our-marketplace-in-claude-code-plugin-marketplace-add); the
official marketplace is auto-registered so
`/plugin install frontend-design@claude-plugins-official` may work in one line —
[Claude Code docs](https://code.claude.com/docs/en/discover-plugins)). Its
licence is "complete terms in LICENSE.txt" in-repo; the wider `anthropics/skills`
repo is Apache 2.0 for most skills
([anthropics/skills](https://github.com/anthropics/skills)).

**What it says.** Ground design in the subject matter; hero as thesis; typography
as personality with intentional display/body pairing; structure encodes
information; deliberate motion; a two-pass process (brainstorm a plan with 4–6
hex values and named typefaces, then review against the brief before coding);
spend boldness in one place; build quality floors (responsive, keyboard focus,
reduced motion); self-critique through screenshots.

**Overlap with impeccable.** Near-total on doctrine and much thinner on
machinery. Impeccable v4.0.4 contains the same "ground it in the subject,
spend boldness once, quality floor" position *plus* 25+ command playbooks, the
mode taxonomy, the PRODUCT.md/DESIGN.md artefact contract, `concept-seed.mjs`'s
anti-convergence roll, a URL-scanning detector, and a project hook — all of
which are already vendored and commit-tracked in this repo. `frontend-design`
is a single 1-file SKILL.md.

**Can both coexist in `.claude/skills`?** Mechanically yes — skills are
independent directories and Claude Code loads all of them. Practically it is a
routing problem: two skills whose descriptions both claim "design, redesign,
polish any frontend interface" means every UI request forces an arbitration, and
impeccable's own `SKILL.md` is built around *"load the one playbook that owns
the request"*. Two owners is worse than one.

**The deciding factor is bias.** `frontend-design`'s marketing copy is explicit
about what it optimises for: *"orchestrated animations and scroll-triggered
interactions, asymmetrical spatial composition, visual depth through gradients
and textures"*
([claude.com/plugins/frontend-design](https://claude.com/plugins/frontend-design)).
That is a `Persuade` skill. Pointing it at a 60-row table behind an SSH tunnel
with no build step will fight the brief. Impeccable's `Operate` mode is the
correct authority for `/dashboard`.

**Second deciding factor, specific to us:** a plugin installs into the user's
`~/.claude/plugins`, not into the repo. Impeccable was deliberately *vendored*
(`354e8ab`, "tooling: vendor the impeccable skill (v4.0.4) for frontend work")
so that any agent in any worktree has it. A phase agent spawned in a fresh
worktree would have impeccable and would not necessarily have the plugin. For a
multi-agent design contract, that asymmetry alone is disqualifying.

**Verdict: skip.** If Tom wants a second opinion during the `/` (demo page)
redesign — where `Persuade` is the right mode and animation is legitimately on
the table — install it then, use it for one direction round, and do not leave it
installed while phase agents are running against DESIGN.md.

### 2.2 21st.dev — **SKIP (not "later"; structurally wrong for us)**

**What it is.** *"A community catalog of UI built by design engineers: React
components, full templates, shadcn themes, shaders, and gradients."* Components
are published in the **shadcn registry format**, built on **React + Tailwind**,
composed with shadcn/ui primitives. Distribution is either an AI-ready prompt
pasted into an agent or a `shadcn` CLI install command. Free tier is 2 component
copies per day; membership unlocks unlimited. Component licensing is not stated
on the site. ([21st.dev](https://21st.dev/))

The MCP story: the old "Magic MCP" has been folded into a unified **21st MCP**
installed via the 21st CLI; the intelligence is a **hosted API** with an API key
and a generation quota.
([21st-dev/magic-mcp](https://mcpservers.org/servers/21st-dev/magic-mcp),
[MCP.Directory 2026 guide](https://mcp.directory/blog/21st-dev-magic-mcp-complete-guide-2026),
[startuphub coverage](https://www.startuphub.ai/ai-news/claudes-corner/2026/claudes-corner-21st-dev-yc-w2026))

**Fit against our constraints.** Three independent disqualifications:

1. **React + Tailwind + shadcn only.** There is no vanilla-CSS or HTML output.
   Our contract is Jinja2 templates and hand-written CSS custom properties;
   `docs/design/dashboard.md` §10.2 lists a real build toolchain as the *third*
   fallback, adopted only when a concrete page demands it. Nothing here converts.
2. **Runtime network dependency on a hosted API with a quota and a key.** Even
   for authoring, that is a subscription in the critical path of a self-hosted
   MIT project's design work.
3. **Unstated component licensing** on a public MIT repo is a risk not worth
   taking even for copied CSS.

**What could transfer** is honestly one thing: a human browsing the catalog for
*visual reference*. That does not require an install, an MCP server or a key.

**Verdict: skip.** Not "later" — "later" would require the project to adopt a
build step and a React runtime, which is a decision the design docs have already
deferred twice and which nothing in this redesign requires.

### 2.3 "UI UX Pro Max" — **SKIP as an authority; optional one-off offline consult**

**What it actually is.** `nextlevelbuilder/ui-ux-pro-max-skill` — a Claude Code
skill wrapping a **local, offline, searchable database** of design decisions.
Verified via `gh api`: **MIT licence, 114,907 stars, 12,310 forks, last pushed
2026-08-06** (so it is real and very actively used — I double-checked the star
count because the figure looked implausible). Its
[SKILL.md](https://github.com/nextlevelbuilder/ui-ux-pro-max-skill/blob/main/.claude/skills/ui-ux-pro-max/SKILL.md)
describes *"84 styles, 192 color palettes, 74 font pairings"* plus 98 UX
guidelines, 25 chart types, 104 icon entries, 16 GSAP motion presets, and 192
product-type templates, searched through a bundled Python script
(`scripts/search.py`, standard library only, **no network access at runtime**).
It claims stack-agnostic guidance across 22 frameworks and **defaults to
HTML + Tailwind**.

**Install.**
```
/plugin marketplace add nextlevelbuilder/ui-ux-pro-max-skill
/plugin install ui-ux-pro-max@ui-ux-pro-max-skill
```
or `npm install -g ui-ux-pro-max-cli && uipro init --ai claude`
([README](https://github.com/nextlevelbuilder/ui-ux-pro-max-skill)). Being MIT
and offline, it *could* be vendored into `.claude/skills/` the way impeccable was.

**Honest fit assessment.** The offline-Python-over-CSV design is genuinely
compatible with our constraints — better than 21st.dev by a wide margin — and
the accessibility/contrast/touch-target checks overlap usefully with impeccable's
detector. But:

- Its organising concept is a **catalogue of named styles** — Glassmorphism,
  Neumorphism, Claymorphism, Bento Grid. That is trend-lookup thinking, and it
  is the precise failure mode impeccable's `concept-seed.mjs` exists to prevent
  and that `frontend-design` warns against ("avoid generic AI aesthetics").
  Picking a world for vidtheque out of a list of 84 named styles would produce
  exactly the templated result Tom is complaining about.
- It is a **second design authority**. `CLAUDE.md` has a strict ground-truth
  ordering for a reason. Two skills both claiming to own palettes, fonts and UX
  rules, against a DESIGN.md that is supposed to be normative, is a divergence
  generator in a multi-agent session — the exact thing §3 is trying to prevent.
- Its font-pairing and palette tables are the one genuinely useful part, and
  Radix Colors + the Fontsource catalogue cover that need with better provenance
  and a licence I can point at per-font.

**Verdict: skip as an installed authority.** If Tom is curious, the zero-risk
use is a **one-off, offline consult during direction selection only**: clone it
to a scratch directory, run `search.py` for font pairings and palettes, take
notes, delete it. Do not leave it in `.claude/skills/` while phase agents run.

### 2.4 Playwright for screenshot-driven iteration — **ADOPT the CLI, not the MCP server**

**The 2026 state.** There are now two distinct products:

- **`@playwright/mcp`** — Microsoft's MCP server, browser control via structured
  accessibility snapshots. Install:
  `claude mcp add playwright npx @playwright/mcp@latest`. Tools include
  `browser_take_screenshot` (png/jpeg/webp), `browser_snapshot`,
  `browser_resize`, `browser_navigate`, `browser_evaluate`. Flags: `--headless`,
  `--isolated`, `--browser`, `--caps` (vision, pdf, devtools, network, storage,
  testing), `--output-dir`, `--device`.
  ([microsoft/playwright-mcp](https://github.com/microsoft/playwright-mcp),
  [playwright.dev/docs/getting-started-mcp](https://playwright.dev/docs/getting-started-mcp))
- **`@playwright/cli`** — a *separate* package built specifically for coding
  agents, which **saves browser state to disk instead of streaming it into
  context**. Install:
  ```
  npm install -g @playwright/cli@latest      # or: npm i -D @playwright/cli@latest
  playwright-cli install --skills            # installs agent skill definitions
  ```
  Commands include `screenshot` (with `[ref]` for an element, `--filename=`,
  `--hires` for device pixels), `click`, `type`, `eval`, navigation, tabs,
  storage, network routing, console, tracing, video. Playwright's own docs
  position CLI as *"best for coding agents (Claude Code, Copilot) with large
  codebases — lower token cost, concise output, skills on-demand"* against MCP's
  *"higher — tool schemas + snapshots in context"*.
  ([playwright.dev/agent-cli/introduction](https://playwright.dev/agent-cli/introduction),
  [playwright.dev/docs/getting-started-cli](https://playwright.dev/docs/getting-started-cli),
  [@playwright/cli on npm](https://www.npmjs.com/package/@playwright/cli))
  Third-party measurement puts the saving at roughly **4.6×** fewer tokens, with
  a 30-page screenshot sweep consuming ~12% of a 200K window
  ([TestDino, 2026](https://testdino.com/blog/playwright-cli)) — third-party, so
  treat the exact multiple as indicative.

**Install cost on this box: very low.** `~/.cache/ms-playwright` already holds
`chromium-1234` and `chromium_headless_shell-1234` (plus 1223 and ffmpeg), and
`~/.cache/puppeteer` holds Chrome 151 — so **the ~700 MB browser download is
already paid**. Node 24.18.0 and npm 11.18.0 are present. The only new fetch is
the `@playwright/cli` npm package itself (small). No system Chromium is on
PATH and none is needed. Phase 1's QA already drove real Chromium via Playwright
1.62.1 from the npx cache (`research/website-test-2026-08-09.md`), so the
pattern and the binaries are both proven here.

**Verdict: adopt `@playwright/cli`, skip `@playwright/mcp`.** For a design loop
the deliverable is a PNG on disk that the agent then `Read`s — which is exactly
the CLI's model, and exactly not the MCP server's. Concretely, the loop is:

```
playwright-cli open http://127.0.0.1:8100/dashboard
playwright-cli screenshot --filename=shots/overview-1280.png
playwright-cli resize 390 844
playwright-cli screenshot --filename=shots/overview-390.png
```
…then `Read` both, in **one batched round**, per impeccable's bounded-passes
rule (build fully → inspect once, desktop and mobile together → fix everything
in one batch → confirm with at most one more round → stop). Screenshots into the
scratchpad directory, never committed — `website-test-2026-08-09.md` already set
that precedent.

**And note we may not need it at all**, which is the cheapest outcome:
`node .claude/skills/impeccable/scripts/detect.mjs --viewport 390x844 <url>`
already scans live URLs with a browser, honours DESIGN.md, and emits `--json`.
If Tom wants exactly one install, the honest ordering is: **use the detector
first, add `@playwright/cli` only when an agent needs to look at pixels rather
than at findings.**

### 2.5 `chrome-devtools-mcp` — **SKIP (nothing here needs it)**

Google's Chrome DevTools team ships an official MCP exposing screenshots,
console, network, performance traces and Lighthouse over CDP; v0.21.0 as of
April 2026, Chrome / Chrome-for-Testing only.
([ChromeDevTools/chrome-devtools-mcp](https://github.com/ChromeDevTools/chrome-devtools-mcp/),
[npm](https://www.npmjs.com/package/chrome-devtools-mcp))
It is the right tool for runtime performance forensics on a heavy SPA. Our
dashboard is server-rendered documents with 144 lines of ES module and no
network chatter beyond thumbnails; the performance surface is "did we set
`width`/`height` on the images and did the font swap shift anything", which
impeccable's `audit` and the CLS discipline already in `demo-site.md` §6.1
cover. Revisit only if a page ever grows a real client-side runtime.

### 2.6 Genuinely load-bearing extras I would recommend

- **Radix Colors — ADOPT (vendored, as a data source).** MIT, 12-step scales
  with per-step declared jobs, light + dark + alpha, distributed as plain CSS
  custom properties bound to `:root`/`.light`/`.dark`.
  ([radix-ui.com/colors](https://www.radix-ui.com/colors),
  [usage](https://www.radix-ui.com/colors/docs/overview/usage))
  **Do not import the package.** Copy the ~28 custom properties we actually use
  (one neutral scale, one accent scale, four state scales at steps 3/9/11) into
  `dashboard.css` / `style.css`, with a comment naming the source scale and
  version. That keeps "no build step, no external requests" intact, keeps the
  diff reviewable, and extends `test_dashboard.py:561`'s equality assertion to
  the full shared set instead of six values.
- **Fontsource — ADOPT (as the download source, not a dependency).** 2,000+
  open-source families with per-subset variable `.woff2`, versioned.
  ([fontsource.org](https://fontsource.org/)) Take the files, commit them, ship
  the OFL text with them; do not add npm to the build story.
- **The DESIGN.md format spec — ADOPT as the contract.**
  [google-labs-code/design.md spec](https://raw.githubusercontent.com/google-labs-code/design.md/main/docs/spec.md),
  which is what impeccable's `document` command already writes to. §3 depends on
  it.
- **Considered and rejected:** GitHub Primer (excellent primitives, but adopting
  its CSS means adopting its build and its component vocabulary — take the ideas,
  not the dependency: [primer.style](https://primer.style/)); Open Props and
  Pico CSS (both would work under no-build, but a 900-line hand-written
  stylesheet with a documented position does not need a third-party token set
  it would immediately override).

---

## 3. Recommended workflow for the redesign

The goal is that three or four phase agents can build UI in parallel and produce
one surface, not four dialects. The mechanism is a **normative token file** plus
a **detector that reads it**, which is exactly what impeccable already ships.

### Step 0 — settle two decisions before any agent touches CSS

Both are Tom's, both are cheap to answer and expensive to reverse:

1. **Direction A, B or C** (§1.2). Everything downstream is a derivation.
2. **Do we vendor `.woff2` binaries into a public MIT repo?** If no, §1.5's
   zero-cost alternative is the ceiling and Direction A loses its display face.

If Tom wants a *genuine* replacement world rather than picking from my three,
this is where `new-work` runs its own flow instead —
`node .claude/skills/impeccable/scripts/concept-seed.mjs --scope direction
--mode operate` — and my three become inputs to that round, not a menu that
short-circuits it.

### Step 1 — `/impeccable init` → `PRODUCT.md` (one commit)

PRODUCT.md does not exist. It records product truth only — who the user is
(a single self-hosting operator), the jobs (`docs/design/dashboard.md` §1's five
questions), the durable constraints (no build step, no external requests, SSH
tunnels, XSS posture, single-user, the citation contract), and the platform
(`web`). It explicitly must **not** record aesthetics. Committed at repo root.

This is the file that stops every future agent re-deriving "who is this for"
from 1,900 lines of design doc.

### Step 2 — build the token layer and **one** reference page

One agent, not several. Deliverables:

- the vendored fonts + `@font-face` + `LICENSE` (its own commit, §1.5 step 5);
- the full token set in `dashboard.css` and the shared subset in `style.css`,
  both schemes (its own commit, alongside the extended `test_dashboard.py:561`
  assertion);
- **the overview page only**, rebuilt in the chosen direction — because it is
  the smallest page that exercises stat panels, rollup lists, the drift panel
  and the masthead.

Then one batched screenshot round at 1280×800 and 390×844 (§2.4), one fix batch,
stop. Tom reviews the overview page. **Do not build four pages before the first
review.**

### Step 3 — `/impeccable document` → `DESIGN.md` (one commit) — this is the contract

Run against the finished reference page. The output is the
[design.md spec](https://raw.githubusercontent.com/google-labs-code/design.md/main/docs/spec.md)
format: YAML frontmatter with `colors`, `typography`, `rounded`, `spacing` and
`components` (component sub-tokens limited to the 8 permitted props), then the
canonical sections in order — Overview, Colors, Typography, Layout, Elevation &
Depth, Shapes, Components, Do's and Don'ts.

**Three project-specific rules I would write into it by hand after generation,
because they are our invariants and not the generator's:**

- *Do's and Don'ts* must carry the mono-means-machine rule, the "every state is
  a word as well as a colour" rule, the "no shadowed cards, hairlines only" rule,
  the "no chart with a time axis" non-goal, and "frames by URL, never base64".
- *Layout* must carry the 52rem table breakpoint and the relative-URL rule for
  every `src` (fonts included).
- *Overview* must name the mode — `Operate` for `/dashboard`, `Persuade` for `/` —
  and point at `docs/design/dashboard.md` and `demo-site.md` as the functional
  contracts it does not override.

**The divergence guard, stated as a rule agents must follow** — deliberately
mirroring `CLAUDE.md`'s existing rule for design docs:

> DESIGN.md's frontmatter tokens are normative. A phase agent may **use** any
> token and may **add** a `components:` entry for a component it is the first to
> build. It may not introduce a raw colour, a font size outside the scale, or a
> spacing value off the 4px grid. If a page genuinely needs a new primitive, the
> agent edits DESIGN.md **in the same commit** as the CSS and says why — exactly
> the discipline `CLAUDE.md` already requires when implementation diverges from
> a design contract.

And, because `CLAUDE.md` is the conventions file: add DESIGN.md and PRODUCT.md
to its ground-truth list as a fourth entry ("visual contract; `docs/design/*.md`
still wins on function"). That is Tom's edit to make, not an agent's.

### Step 4 — fan out the phase agents

Now parallelism is safe. Each agent gets an identical preamble:

1. Read `PRODUCT.md`, `DESIGN.md`, and the section of `docs/design/dashboard.md`
   that owns its page.
2. Run `node .claude/skills/impeccable/scripts/context.mjs --target <template>`
   once (it loads PRODUCT.md, DESIGN.md and the surface brief and prints
   directives).
3. Build. Use only DESIGN.md tokens.
4. Run the detector on its own route:
   `node .claude/skills/impeccable/scripts/detect.mjs --viewport 1280x800 <url>`
   and again at `390x844`. The hook already fires it after UI edits
   (`.impeccable/config.json` is committed and carries the one existing waiver),
   so this is confirmation, not discovery.
5. One batched screenshot round, one fix batch, stop.
6. Commit with a pathspec covering only its own files (`CLAUDE.md`'s multi-agent
   rule — never `git add -A`).

Natural split, and it maps onto the existing phase plan:
**videos table** · **video detail** (the timeline + OCR overlay — the largest and
the one that carries the direction, so give it the strongest agent) ·
**jobs view** (phase 2, and it should be built *after* DESIGN.md exists so it is
born correct rather than retrofitted) · **demo page `/`** (mode `Persuade`,
inheriting the shared subset per §1.4).

### Step 5 — converge

- `/impeccable audit` on each route (a11y, responsive, perf) and
  `/impeccable polish` once across the set.
- Re-run `make test` — the palette-equality, no-inline-script, both-schemes and
  no-`| safe` assertions are the regression net and they are already written.
- If DESIGN.md accumulated edits during step 4, re-run `/impeccable document`
  to re-carbonize it, or run `/impeccable doctor` to report drift. Per the
  skill's own rule, drift is *reported*, not repaired as a side effect of a
  design task.

### Why this ordering and not another

The failure mode in a multi-agent redesign is four agents each inventing a grey.
Steps 2–3 exist to make one agent invent every value, once, under review, and
write it down in a machine-readable file that the detector then enforces on
everybody else. Building DESIGN.md *before* a reference page produces fiction;
building it *after* four parallel pages produces an average. One page, then the
contract, then the fan-out.

---

## 4. Summary table

| item | verdict | one line |
|---|---|---|
| **Direction C — "the cutting room"** | **build** | domain-grounded; mono=machine/sans=human, shot timeline and OCR overlay as the signature; keeps B's density rules and the incumbent warmth |
| Direction A — "the archive slip" | build **for `/`** | warm editorial world with a display serif; right for the page that sells, wrong for a 60-row table |
| Direction B — "the instrument panel" | standing safe answer | Linear/Vercel achromatic console, played straight; credible, cheaper, says nothing about video |
| Anthropic `frontend-design` | **skip** | one-file skill, `Persuade`-biased, near-total doctrine overlap with vendored impeccable, and it installs outside the repo so fresh worktrees lack it |
| 21st.dev | **skip** | React+Tailwind+shadcn only, hosted API with key and quota, unstated component licence — nothing converts to Jinja + vanilla CSS |
| UI UX Pro Max | **skip** (optional offline consult) | MIT, 114.9k★, offline Python, genuinely constraint-compatible — but it is a catalogue of 84 named styles and a second design authority |
| `@playwright/cli` | **adopt** | built for agents, state to disk not context (~4.6× fewer tokens); browsers already cached on this box, only the npm package is new |
| `@playwright/mcp` | skip | tool schemas + snapshots in context; wrong shape for a screenshot loop |
| `chrome-devtools-mcp` | skip | perf/Lighthouse forensics for heavy SPAs; our pages are documents with 144 lines of JS |
| Radix Colors | **adopt** (vendored values) | MIT 12-step light/dark/alpha scales as plain custom properties; copy ~28 values, do not add the package |
| Fontsource | **adopt** (as download source) | per-subset variable `.woff2` for Inter / Geist Mono / Plex; commit the files + the OFL text |
| DESIGN.md spec + impeccable `document` | **adopt** | the normative token file that lets phase agents run in parallel |
| impeccable detector + hook | already adopted | scans live URLs, honours DESIGN.md, hook already on with one committed waiver |

---

## 5. What I could not verify

Stated plainly so nobody inherits a guess as a fact.

1. **Exact `.woff2` byte sizes** for latin-subset variable Inter, Geist Mono,
   JetBrains Mono and IBM Plex Mono. My ~100 KB / ~30–50 KB figures are
   recollection, not measurement. Measure at vendor time and record the numbers
   in the commit message.
2. **Licence files for Instrument Serif and Newsreader** — reported as OFL via
   Google Fonts / Fontsource, but I did not open either `OFL.txt`. Verify before
   committing.
3. **Design-token documentation for Datadog, Supabase Studio and the Tailscale
   admin console.** I found none public. Every search returned Grafana dashboards
   *monitoring* those products. Any claim about their internals is unsupported.
4. **Linear's exact row height (36px)** and the **13px/1.4 dense-cell figure**
   come from third-party breakdowns and a font blog, not from Linear or Grafana
   directly. Good starting points; measure our own.
5. **`@playwright/cli`'s exact command surface.** I read the docs pages, not the
   binary — it is not installed here and I did not install it. `screenshot`,
   `resize`, `eval`, `click`, `type`, navigation, tabs, storage, network,
   console, tracing and video are documented; the precise flag spellings for
   `open`/`resize` in my §2.4 loop should be checked against
   `playwright-cli --help` on first use.
6. **Whether the official one-line install
   `/plugin install frontend-design@claude-plugins-official` works**, versus the
   two-line marketplace form. Both are documented in different places; I did not
   run either.
7. **Whether `@fontsource-variable/inter`'s current version ships the `cv01` /
   `ss03` axes** exposed the way Linear uses them. Inter supports the features;
   whether a given Fontsource subset build preserves them needs a check against
   the actual file.
