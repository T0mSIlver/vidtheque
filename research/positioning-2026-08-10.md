# Positioning — reference analysis, product truth, and three directions

**Status: WORKING DRAFT for joint refinement (Tom + orchestrator).** This is an
opinionated first pass, not a decision. It ends with a recommendation and a
list of calls only Tom can make. Nothing here is a contract: `docs/design/*.md`
still wins on function, data and copy, and `DESIGN.md` still wins on the visual
system for `/dashboard` and `/`.

Written 2026-08-10 by the positioning agent, session `peppy-wibbling-moler`,
from Tom's raw elements of the same date plus the repo's own docs and five
reference sites studied first-hand.

**Append-only, like the rest of `research/`.** Add clearly-headed sections;
don't rewrite what's here.

---

## 0. Tom's raw elements, restated

Verbatim in substance, 2026-08-10:

1. vidtheque must **not** be marketed as "just an MCP server."
2. It is **similar to screenpipe** — take inspiration from it.
3. It is for **heavy YouTube users who need a second brain**, and for
   **content creators who need to remember a video they made**.
4. The **demo corpus is AI Engineer talks**, and the demo site should sell
   vidtheque *through* that corpus.
5. Reference family: screenpipe's site, louis030195.com, Theo Browne's sites,
   impeccable.style, herdr. Tom's read: *"square/futuristic look, eye candy,
   real personality; some go overboard with AI features but we're an AI product
   so that's fine."*

Everything below serves those five. Where I disagree with one, I say so out
loud (§3.4, §5).

---

## 1. Reference analysis

**Method.** Two research agents fetched copy and structure; I took my own
1440×900 screenshots with Playwright because WebFetch reads markdown and cannot
see a palette — and it showed: one agent reported impeccable.style as
"minimalist neutral, whites/greys/beige," which is the opposite of what the page
actually renders (§1.4). **Trust the screenshots over the fetch summaries on
anything visual.**

Screenshots are in the session scratchpad, not the repo (this doc is the only
file the positioning agent writes):

| file | URL | taken |
|---|---|---|
| `ref-screenpipe.png` | https://screenpipe.com/ | 2026-08-10 |
| `ref-impeccable.png` | https://impeccable.style/ | 2026-08-10 |
| `ref-herdr.png` | https://herdr.dev/ | 2026-08-10 |
| `ref-uploadthing.png` | https://uploadthing.com/ | 2026-08-10 |
| `ref-t3chat.png` | https://t3.chat/ | 2026-08-10 |

Regenerate any of them with:

```bash
npx --yes playwright@latest screenshot \
  --viewport-size=1440,900 --wait-for-timeout=3000 <url> <out.png>
```

(`playwright@1.55.0` fails on this box — its pinned Chromium revision isn't in
`~/.cache/ms-playwright`; `@latest` matches `chromium-1234`, which is.)

### 1.1 screenpipe — https://screenpipe.com/ (screenpi.pe 301s here now)

Also: `github.com/mediar-ai/screenpipe`, 20.8k★, YC S26, Rust, source-available
(licence changed to stay "sustainable" — read: commercial).

**Positioning, one line.** *"AI powered by everything you've seen, said, or
heard"* — subline *"screenpipe gives Claude, Codex, and other agents the context
of what you've seen, said, and done, locally, on your device."* The README's
compressed version is better: *"screenpipe remembers how you actually work."*

**Voice.** Third person for the product, present tense, deadpan. No jokes, no
exclamation marks, no profanity. The signature device is **staccato triads**:
*"record. search. automate. all local, all private, all yours."* Feature bullets
are lowercase, verb-first, and answer an objection in the same breath:
*"100% local — your data lives on your machine only"*, *"source-available —
inspect, modify, audit"*. Swagger is factual rather than boastful — the flex is
a number (*"9ms on consumer device"*, *"the leading source-available alternative
to Rewind.ai, Microsoft Recall, Granola, and Otter.ai"*). Register: systems
changelog, not pitch deck.

**Visual identity (from the screenshot, and this is the important part).** Not
dark. **Off-white paper ground with a tiled isometric-cube watermark** at very
low contrast. The hero headline is set in a **monospace at display size**, ~64px,
centred, with one word (*heard*) in a deep olive-green — mono as a display face
is exactly the "square/futuristic" grammar Tom is pointing at, and it costs
nothing. **One acid accent**: chartreuse `#c8f000`-ish on the primary button,
which sits on a **hard-offset black shadow** with no blur and inner corner
brackets — brutalist, square, zero radius. Secondary CTA is an outlined
black-on-white peer of the same size. Section edges are **tick rules** (a ruler,
not a hairline), sections are numbered (`00`), and kickers are mono uppercase
with wide tracking (`FILM · 44 SECONDS`, `OVERVIEW`). Crop-mark `+` glyphs sit
in the margins. Density is airy; motion is embedded product video, not CSS
flourish. The imagery is the product's own dark UI, dropped into the light
shell — the contrast does the work.

**CTA.** `Download for free` (repeated down the page), with
`Star us on GitHub ★20,835` as an equal-weight peer immediately below, and
`Backed by Y Combinator` under both. Two funnels run in parallel in the nav
(free download vs `Enterprise` / `Pricing` / `Trust center`), and a
`npx screenpipe record` one-liner for developers.

**Product framing worth noting.** It says **24/7 recording** in marketing even
though the engine is event-driven — the honest mechanism is in the README, the
legible claim is on the page. Privacy is framed as *configurable on-device
policy* ("Cloud upload off", "Exclude apps, windows, and URLs, then redact
sensitive fields on your device"), not as a slogan. The developer surface is
framed as **infrastructure, not a feature**: *"Your memory is a local database
with an API, an SDK, and source code you can inspect."* Plugins are **"pipes"**
— they gave the extension point a noun. MCP is mentioned as one of several
access paths and never as the identity. **That last sentence is the whole
answer to Tom's element #1, proven by a 20k-star neighbour.**

**The one thing to steal:** *mono at display size on a light paper ground with
one acid accent.* It reads futuristic and square without a single gradient, and
it is one CSS change away from the faces `DESIGN.md` already vendors.

### 1.2 louis030195.com — Louis Beaumont, screenpipe's founder

Fully JS-rendered; direct fetch returned a spinner, so this read is from a
reader proxy and is thin on CSS. No screenshot taken (nothing to see until JS
runs, and it isn't a design reference anyway — it's a voice reference).

**Positioning, one line.** *"Louis Beaumont | Founder & Poetic Technologist"* —
elsewhere on the same page, *"screen protocol architect building post-scarcity
civilization."*

**Voice.** First person, aphoristic, unhedged, high swagger. *"I am fanatical
about building tools that augment humans."* *"I could never play small."* His
company is named **Negentropy, Inc.** because *"the purpose of life is to create
more life."* Personal stakes are load-bearing copy, not resume filler: *"At 13,
I was diagnosed with leukemia. I almost died several times"*; *$30 a day for
five years*. A section is literally headed **CONSCIOUSNESS INTERFACE**. No
humour, no CTA, no funnel — the site's product is an intellectual identity, and
it is surrounded by a constellation of subdomains (`art.`, `brain.` as a public
digital garden, `store.`).

**The one thing to steal:** *the founder is a character, and the character is
free distribution.* Tom notes they look alike; the transferable part is not the
mysticism, it's that **screenpipe's reach is partly a person with a stated
belief.** vidtheque's equivalent belief already exists in the repo and is much
better than "post-scarcity civilization" because it is falsifiable:
**an answer you cannot check is not an answer.** Put a name and a face behind
that sentence. Do not copy the grandiosity — it would fight the product's own
"admit the gap" voice and lose.

### 1.3 The Theo Browne family — t3.gg, ping.gg, uploadthing.com, t3.chat

**Positioning.** `t3.gg` is two words: *"theo"* / *"builds things."* `ping.gg`
is *"Theo's company. We build things."* over a card wall of shipped products.
`uploadthing.com` is *"Better file uploads for developers"* / *"Developers
deserve better than S3."* `t3.chat` is *"AI chat with GPT-5, Claude, Gemini, and
more"* (bot-blocked to fetch; the app itself renders as a pink-magenta,
rounded, IndexedDB-fast chat shell — see `ref-t3chat.png`).

**Voice.** A house voice across four properties: **lowercase, verb-first,
zero throat-clearing.** Product blurbs are one clipped fragment each
(*"picthing: Background removal for content creators."*). Swagger is
**comparative and named** — the competitor is in the copy (*S3*), and the
founder quotes himself in his own testimonial slot: *"I asked for a better S3
for years. UploadThing exists because we got tired of waiting."* Superlative
stacking plus an emoji sign-off is a real tic: *"the cheapest, fastest, AND best
looking AI chat app ever made. So pumped 🫡"*.

**Visual (uploadthing, screenshot).** Near-black ground washed with a deep
oxblood radial, one **hot red** accent used three ways: the second line of the
headline, the filled primary button, and the file-type chips in the hero
illustration. Headline is a tight, heavy grotesk at ~56px with a two-tone split
across two lines. Left column is text-only and generously empty; the right is a
**staged product collage** — floating `.MP4` / `.PNG` / `.PDF` cards drifting
over a dark app window with real code inside it. Dual CTA: filled `Get Started
for Free` + bare `Documentation →`. The proof is a code snippet or a UI, never
an abstract illustration.

**The one thing to steal:** *name the incumbent in the subline.* "Developers
deserve better than S3" does more positioning work in six words than a
paragraph of benefits, because it hands the reader a slot to put you in. For
vidtheque the slot is **YouTube's own search box** (§3, Direction B) — the
thing every visitor has already failed with.

### 1.4 impeccable.style

The most visually ambitious of the five, and the one the fetch summary got
wrong. **Correction on the record: it is not neutral/beige. It is gold on
black.**

**Positioning, one line.** *"The missing design vocabulary for agents."* Subline:
*"Impeccable strips the slop from AI-generated interfaces, gives you precise
commands to steer, and iterates visual variants live in your product. Then the
dice: pushed out of distribution, your model designs ambitious visual worlds it
could never reach alone."*

**Voice.** Clinical verbs in the feature copy (*strips*, *steer*, *iterates*),
countable claims (*"58 checks"*, *"23 commands"*), noun-phrase section headers
(*The Language*, *Worlds on the table*, *Ready to drop in*), one wink
(*"Psst… the deck grows every week in Pro →"*). Then it **outsources all the
swagger to a testimonial wall** and leaves it raw, profanity and typos intact:
*"It fucking works beautifully."* / *"Impeccable > Claude design"* / *"Uninstall
whatever frontend skill you're using. Replace it with Impeccable."* / *"I will
fight anyone who say anything bad against impeccable."* Restraint in the
first-party voice, chaos in the third-party voice. That split is the technique.

**Visual (screenshot).** Ground is **near-black with a kintsugi texture** —
photographic gold-and-verdigris veining bleeding in from the top-right and
bottom-left corners, plus a faint gold cross-hatch grid. Display face is a
**condensed geometric sans, light weight, very large** (~64px, "The missing
design vocabulary for agents." on two lines) — light-weight display type is the
single least-copied move in dev-tool design and it reads instantly as taste.
One accent: **gold**, used for the filled CTA, the underline on the active nav
item, and — the signature device — **annotation tabs**: small gold rectangles
with black mono uppercase text (`AI kicker`, `Italic serif`, `Side-tab border`,
`AI beige`) attached by leader lines to the *live before/after specimen* in the
hero. The product is literally shown critiquing a UI, with the critique labelled
in the margin. Below: a version band (`V4  Version 4 is here. …  WHAT CHANGED →`)
and an edge-to-edge marquee of tweet-shaped testimonial cards. Slash commands
(`/polish  /distill  /clarify`) sit in the specimen's footer as mono chips.

**The one thing to steal:** *the annotated specimen.* A single frame from the
corpus, shown at size, with **gold callout tabs pointing at the evidence** —
`on-screen text`, `never spoken`, `18:56`, `?t=1134` — turns vidtheque's whole
argument into one picture that needs no paragraph. This is the highest-value
steal in the document (§4.3, and §3 Direction B's hero).

### 1.5 herdr — https://herdr.dev/ (found; `github.com/herdrdev/herdr`, 26.4k★, Apache-2.0)

**Positioning, one line.** *"Run them anywhere. Leave them running."* Subline:
*"Herdr is the runtime your coding agents live on — laptop, desktop, or a box
you rent. It holds real terminals open so the work survives the lid closing, and
gets you back in from anything with a keyboard."*

**Voice.** No first person anywhere. Pure infrastructure-speak, lowercase,
em-dash lists, present tense: *"always running — herdr is a background server;
the terminals live inside it. close the lid, drop the network, restart the
machine — agents keep working and sessions come back."* *"runs what you already
run — claude code, codex, cursor, opencode, grok and the rest."* The only
swagger is a competitor jab disguised as a spec: *"one rust binary, no
electron."*

**Visual (screenshot).** Near-black `#0d0d0f`-ish ground. Display is a **tight
ultra-bold grotesk with negative tracking**, two lines, with **one word in
lavender** (*anywhere.*) — the accent is a word, not a shape. Kicker is a short
rule followed by mono uppercase (`——— THE AGENT RUNTIME`). The install
one-liner **is the CTA**: a bordered mono box, `$ curl -fsSL
https://herdr.dev/install.sh | sh`, with a `COPY` button welded to its right
edge, and beneath it a mono meta-line (`macOS · Linux · Windows beta ·
Apache 2.0`). Then — and this matters for vidtheque — a **four-cell stat ledger
band** across the full width, hairline-divided, each cell a huge numeral over a
mono uppercase label with a tiny glyph: `26,356 ★ GITHUB STARS` /
`371,107 ⤓ INSTALLS TO DATE` / `539 ⊞ COMMUNITY PLUGINS` / `19 >_ AGENT CLIS
DETECTED`. Below that, a **live interactive terminal mockup** — the product
demoing itself in its own medium — with status dots (● working, ○ idle,
◉ blocked). The theme toggle is labelled **INK / PAPER**. A giant watermark of
the logo glyph sits behind the hero at ~4% opacity.

**The one thing to steal:** *the stat ledger band, fed live from the index.*
`DESIGN.md` already has a "ledger band" signature on `/dashboard` overview, and
`/api/meta` already returns real counts. Four cells — **talks · hours ·
moments · frames read** — hairline-divided, mono uppercase labels, tabular
figures, rendered from the database at request time. It satisfies PRODUCT.md's
hardest brand rule (*"nothing may present a number that is not read out of the
index at render time"*) and it makes the corpus feel like a *quantity* in three
seconds. Second steal: **the install one-liner as the secondary CTA**, in the
same mono box with a COPY button — the panel `demo-site.md` §6.6 already
specifies (`claude mcp add --transport http vidtheque <mcp_url>`) is exactly
this device, currently buried at the bottom of the page.

### 1.6 What the family has in common — the grammar Tom is pointing at

Five sites, one recognisable grammar. Named, so an art director can act on it:

1. **A monospace or ultra-tight grotesk at display size.** Nobody uses a
   friendly humanist sans for the headline. Mono-as-display (screenpipe) or
   heavy-condensed (herdr, uploadthing) or light-condensed (impeccable).
2. **Exactly one accent, and it is loud.** Chartreuse, gold, lavender, hot red.
   Never two. It appears in ≤4 places: one word in the headline, the primary
   button, the active nav item, one rule.
3. **Square. Zero or near-zero radius, hairlines and tick rules instead of
   cards, hard offset shadows instead of blur.** The only rounded thing in the
   whole family is t3.chat, which is an app, not a pitch.
4. **Mono uppercase micro-labels with wide tracking**, used as kickers, stat
   labels and annotation tabs. This one device carries most of the "futuristic."
5. **A textured ground, never flat.** Isometric cubes, kintsugi gold, oxblood
   radial, a 4%-opacity logo watermark. Texture is where the "eye candy" lives;
   it is always *behind* the type, never competing.
6. **The product demos itself in its own medium** — a live terminal, an
   annotated before/after, a real code block, a real UI. Zero abstract
   illustration, zero stock 3D.
7. **A dual CTA with a copyable one-liner as the developer path**, plus a
   credibility chip (★ count, YC badge) sitting at equal weight.
8. **Personality is quarantined.** First-party copy is clipped and factual;
   the swagger lives in a testimonial wall, a self-quote, or a competitor jab.

**Where vidtheque already agrees:** hairlines-not-cards, one accent (burnt
amber), mono-means-machine, tabular figures, both schemes as peers, a drawn
mark, `INK/PAPER`-style scheme thinking. **`DESIGN.md`'s "cutting room" north
star is already 80% of this grammar.** The marketing surface does not need a
new design system — it needs `DESIGN.md`'s system with the volume up: the
display face bigger, the texture allowed, the accent used louder.

**Where vidtheque should refuse the family:** no gradients, no 3D, no faux-CRT
(already banned), no "AI-powered" chrome, and **no claim that isn't read out of
the index.** The family's swagger is mostly numbers; vidtheque's numbers are
real and live, which is a better version of the same move.

---

## 2. Product truth inventory

Sources: `README.md`, `PRODUCT.md`, `docs/design/{tool-surface,index-schema,demo-site,dashboard,DECISIONS}.md`,
`research/{HANDOFF-2026-08-08,landscape-survey-video-mcp,screenpipe-tool-surface-deep-dive,demo-queries-2026-08-09}.md`.

### 2.1 What it genuinely does today

- **Ingests published video by URL** — a video, a playlist, or a channel
  (`index-video`, with `expand=channel_recent`). yt-dlp fetch; source file
  deleted after indexing, audio kept for STT re-runs (`DECISIONS` #3).
- **Three channels indexed against one timeline**: spoken transcript with
  word-level timestamps (whisperX, or YouTube `json3` auto-captions as the
  zero-GPU fast path), **on-screen text via OCR with box coordinates kept**
  (RapidOCR, CPU), and **keyframe embeddings** from shot detection +
  perceptual-hash dedup + sharpness pick.
- **Hybrid search across the whole corpus** — FTS5 + vector, RRF fusion,
  relevance-first, per-video diversity cap, adjacent-cue clustering, one
  `search` tool with `content_type: all | transcript | ocr | frame` where
  **`all` means all** and a leg that can't apply prints a `note:`.
- **Answers with a citation that lands on the second** —
  `https://youtu.be/ID?t=123`, derived from real word timings, not estimated.
- **Nine MCP tools + three resources** (`search`, `list-videos`,
  `corpus-summary`, `video-summary`, `get-segment-context`, `get-frames`,
  `index-video`, `job-status`, `tag-video`; `vidtheque://corpus`,
  `://context`, `://guide`), over **remote HTTP MCP with its own OAuth**
  (CIMD + DCR, claude.ai-compatible) or bearer token or none.
- **A public read-only demo at `/`** — search, filter chips, and an `ask` mode
  that runs the model over the same tools and streams its work, with rate
  limits, degradation paths and a shareable `?q=` URL.
- **A management dashboard at `/dashboard`** — corpus overview, videos table,
  video detail with a shot timeline and OCR overlays, jobs, job detail.
- **Runs on two boxes or one**: `mcp/` is CPU and multi-arch (a Pi will do);
  `worker/` is an optional GPU service, and if you don't have a GPU you point
  `WORKER_URL` at any OpenAI-compatible provider and delete the service.
- **Provenance per stage per video** — seven stages, each with its own state,
  `model_key` and clock. The index can say what it is made of and what it is
  missing.

### 2.2 What is genuinely distinctive

Ranked by how hard it would be for a neighbour to copy truthfully:

1. **It finds things that were never said out loud.** `owl:FunctionalProperty`
   returns exactly one result in the whole corpus, from a slide, at 18:56, and
   no transcript-based tool on earth can answer that query.
   `guardrail_safety_check` exists only as a span name inside a screenshot of a
   trace tree while the speaker is saying something else entirely. A CVE number
   read off an NVD page on screen. `$ annotations_to_evals.py --weeks 2
   --priority-themes`, a CLI invocation nobody read aloud. **This is the
   product's single best argument and it is currently invisible in the pitch.**
2. **The moment comes with a receipt.** Not "this video discusses reward
   hacking" but a single cue at 5:57 with a link that opens mid-sentence.
   `research/demo-queries-2026-08-09.md` §1.1. Success is defined in PRODUCT.md
   as *"a question answered with a link that proves it"* — an answer that can't
   be checked has failed even when it's right.
3. **Three channels, one second, honest about which one answered.** The badges
   are `spoken` / `on-screen` / `frame`, styled differently on purpose, and
   `transcript+ocr` shows when two legs agreed on one moment. A snippet that
   doesn't say which channel it came from is quietly claiming to be speech.
4. **Frame search that actually works on pixels.** *"a photo of a person on
   stage at a podium"* returns podium shots with no text in them at all —
   proof the frame leg isn't secretly reading OCR. *"architecture diagram with
   boxes and arrows"* returns a labelled sharded-MongoDB topology.
5. **Self-hosted, single owner, one SQLite file, no runtime network
   dependency.** Your corpus is on your box. No build step, works behind a
   tunnel on a port nobody predicted.
6. **Agent-pluggable, and the demo proves it** — the public instance is a real
   MCP server a stranger can add to their own client in one line.
7. **The landscape gap is documented, not asserted.** `landscape-survey-video-mcp.md`
   checked 30+ repos: nobody combines a persistent cross-video multimodal index
   behind MCP + visual embeddings + fully local GPU + remote OAuth deployment.
   *Nobody embeds frames and does vector retrieval over pixels in an MCP
   server.* That is a citable claim with a date on it.

### 2.3 What it does **not** do — and the screenpipe difference

This is the paragraph that keeps the positioning honest, so it is the longest.

- **It does not record. Anything.** screenpipe watches *your machine* — it
  captures your screen and your microphone continuously and involuntarily, and
  its entire value proposition is that you did nothing to create the corpus.
  **vidtheque ingests published video you point it at.** You paste a URL, or a
  playlist, or a channel. The corpus is *deliberate*.
  - Consequence for positioning: **the "second brain" comparison is real but
    the acquisition story is inverted.** screenpipe's pitch can be "you already
    have the data, you just can't search it." vidtheque's pitch must be "you
    already *watched* it" — the effort was the watching, not the capturing, and
    the loss is the same loss. Do not imply passive capture. Do not say
    "everything you've seen." Say **"everything you've watched."**
  - Consequence for privacy copy: screenpipe has to spend half its homepage on
    privacy because it is recording you. **vidtheque has no such debt** —
    there is no surveillance surface to defend, because the input is public
    video. Spend that homepage space on the receipt instead. (Self-hosting is
    still a feature here; it is just not an *apology*.)
  - Consequence for the enemy: screenpipe's enemy is your own forgetting.
    vidtheque can pick a bigger one (§3).
- **It is not a video player.** Source media is deleted after indexing and the
  citation contract is to send the human back to YouTube. There is no scrubber,
  no embed, no clip export (`get-clip` is deferred).
- **It is not multi-user SaaS.** `owner_id` is a constant `1`. No roles, no
  invitations, no teams. A "team memory" positioning (§3, Direction B's third
  persona) is a *story the current code can only tell as a shared read-only
  instance*, not as accounts.
- **It has no releases and no published images yet**, and schemas can still
  change. README says so in a callout. Any homepage that implies a stable
  product is lying today.
- **It has no benchmarks, no user counts and no testimonials** — the three
  things every site in §1 leans on. The only numbers vidtheque may print are
  the ones the index computes at render time (PRODUCT.md, Evidence on Hand).
  **This is a constraint and also the best idea in the document** (§1.5 steal).
- **YouTube-shaped today.** yt-dlp makes "any site it supports, plus local
  files" reachable, but nothing else is exercised. Don't promise it.
- **Diarization is off by default** (pyannote VRAM regression); `speaker=`
  returns a typed feature-disabled error.
- **A real, current recall hole in OCR search.** At migration 0002 the OCR FTS
  is one document per *line*, so a multi-term OCR query only matches when every
  term lands on the same physical line — `search "vuln strcpy" content_type=ocr`
  returns 0 against a slide that plainly contains both. `0003_ocr_frame_fts.sql`
  fixes it and is not applied to the demo DB. **Every OCR example on the demo
  site must be single-line-matchable until 0003 lands**
  (`demo-queries-2026-08-09.md` §0).
- **Ask mode costs money and can take ~90s.** It is rate-limited (30 searches
  and 5 asks per minute, 50 asks per day) and degrades. A homepage that leads
  with "ask it anything" is writing a cheque the free tier cashes.

### 2.4 The demo corpus, precisely

`research/demo-queries-2026-08-09.md`, measured against the live stack:
**75 videos · 26.1 hours · 16,777 transcript cues · 3,060 keyframes · 59,817
OCR lines over 2,870 OCR'd frames · one channel (AI Engineer) · published
2026-04-16 → 2026-08-08.**

Tom's brief says "90 talks." **Do not hardcode either number.** The corpus grows
as he indexes more, and PRODUCT.md forbids a printed number that isn't read out
of the index at render time. The homepage says what `/api/meta` says. This is
not pedantry — it is the reason the stat band (§1.5) is the right device: it is
the only way to look as confident as herdr while staying inside the rule.

---

## 3. Three positioning directions

Genuinely different: different buyer, different enemy, different first screen.
All three are true; they are not equally strong. My pick is §3.2, with §3.1's
emotional register borrowed for the headline and §3.3 demoted to the second
scroll — reasoning in §3.4.

### 3.1 Direction A — "Second brain for what you watch" (personal memory)

**The position.** *vidtheque is the memory of everything you've watched — every
talk, tutorial and stream you got value from once, searchable to the second and
answering with the link that proves it.*

**Who it's for.**
- *The heavy YouTube learner.* Watches 5–15 hours a week of technical talks,
  conference recordings, long-form interviews. Has a Watch Later with 400 items
  and a memory that says "someone explained this really well, I think it was
  that Karpathy one." Currently loses ~100% of it.
- *The creator with a back catalogue.* Two hundred of their own videos.
  Someone asks "didn't you cover this?" and the honest answer is "probably,
  somewhere." Needs to cite their own past self, and needs the exact second to
  link to.

**The enemy: forgetting** — and its physical manifestation, **the bookmark
graveyard**. Watch Later, saved playlists, a Notes file of timestamps you never
went back to. The emotional beat is *"you did the work of watching and got
nothing durable for it."*

**Homepage hero.**
> ### Watch once. Find it forever.
> You already watched the talk. vidtheque remembers what was said, what was on
> the slide, and what second it happened — and hands you back the link that
> proves it.
>
> `[ Search 75 conference talks → ]`  `[ Run it on your own library ]`

**Feature hierarchy.**
1. Every moment has a link that lands on the second.
2. It reads the screen, not just the audio.
3. Point it at a video, a playlist, or a whole channel.
4. Your library, your box, one SQLite file.
5. Your agent can search it too *(one line: `claude mcp add …`)*.
6. *(Later, small, in the "how it works" section: "It speaks MCP.")*

**Demo-site framing.** The AI Engineer corpus is *a stand-in for your library* —
"this is one person's month of conference watching; here's what it looks like
when you can still search it." Honest, but it argues by analogy, and analogy is
the weakest form of demo.

**Why it might be the pick.** It is Tom's stated instinct, it speaks to both
his personas, and "second brain" is a category the market already understands.

**Why I don't lead with it.** Three reasons. (a) screenpipe, Rewind/Limitless
and Recall have saturated "second brain," and they all win the acquisition
argument because they capture automatically — vidtheque asks you to build the
corpus first, so a head-to-head on the same promise starts one step behind
(§2.3). (b) The demo corpus is not the visitor's memory, so the hero screen and
the proof screen are pulling in different directions. (c) "Memory" hides the
distinctive mechanism: nothing in this framing tells you it can find a string
that was never spoken.

### 3.2 Direction B — "The searchable archive" (corpus-first) ★ RECOMMENDED

**The position.** *vidtheque turns a body of video — a conference, a channel,
your own back catalogue, your own watch history — into a searchable archive that
answers in moments and cites every one to the second, including the things
nobody said out loud.*

**Who it's for.** Same two personas as A, plus a third that only this framing
can hold:
- *The heavy learner*, whose "body of video" is their own watch history.
- *The creator*, whose body of video is their own channel — and for whom this
  is the sharpest fit of all three directions, because a channel **is** a
  corpus, `index-video expand=channel_recent` already ingests one, and the
  question "which of my 200 videos covered this, and at what second" is
  answered with a link they can paste into a comment.
- *Team / community memory* — an org's internal talks, a conference's archive,
  a Discord's recorded office hours. Today this ships as **one shared read-only
  instance**, not as accounts (§2.3), which is enough for the story and must be
  said plainly.

**The enemy: YouTube's own search box.** Not forgetting — *searching and
failing*. Everyone in the audience has typed into YouTube's search, or
Ctrl-F'd a transcript panel, and found nothing, because YouTube searches titles
and descriptions, not moments, and never the screen. This enemy is better than
forgetting because it is (a) concrete, (b) something the visitor failed at this
week, and (c) a comparison vidtheque wins on the demo, live, in one click. It
is the uploadthing move from §1.3: name the incumbent.

**Homepage hero.**
> ### Search what the video said. And what it showed.
> vidtheque indexes a channel, a playlist or a conference into an archive you
> can actually search — spoken words, on-screen text and the frames themselves —
> and every answer comes back as a moment with a link that lands on the second.
>
> `[ Try it on the AI Engineer archive → ]`
> `$ docker compose up -d`  `[COPY]`

Alternate headline, if the "never said out loud" beat should carry the top of
the page instead — this is the one I'd A/B first:

> ### It read the slides, too.
> Search 75 conference talks by what was said, what was on screen, and what was
> in the frame. Every answer is a moment, with a link that lands on the second.

**Feature hierarchy.**
1. **Three ways in, one timeline** — `spoken` · `on-screen` · `frame`, and
   the answer tells you which one found it.
2. **Every answer is a moment with a receipt** (`youtu.be/ID?t=123`).
3. **Found what was never said** — the annotated-specimen block (§4.3).
4. **Point it at anything** — a video, a playlist, a channel, on a schedule.
5. **It's yours** — self-hosted, one compose file, one SQLite file, works on a
   Pi, no runtime network dependency, MIT.
6. **Any agent can search it** — the copy-paste MCP line, presented as an
   *access path* alongside the browser and the API, exactly as screenpipe
   presents its own (§1.1). This is where MCP lives: **position 6, as a
   feature, never in the headline and never in the `<h1>`.**

**Demo-site framing — the strongest part of this direction.** The AI Engineer
corpus stops being a sample and becomes **the product on display**: *the AI
Engineer World's Fair 2026, fully searchable — 75 talks, 26 hours, every claim
citable.* The demo site is a real, useful, linkable artefact that people will
share **for the corpus's sake**, and every share is a product demo. Concretely:
- The hero search box is pre-loaded with the five verified example chips from
  `demo-queries-2026-08-09.md` — `reward hacking` (fused `[transcript+ocr]`
  badge), `owl:FunctionalProperty` (pinned `ocr`, 1/1, never spoken),
  `architecture diagram with boxes and arrows` (pinned `frame`),
  `small towns in Bavaria` (paraphrase, not keyword), `slop`.
- A live stat band under the hero (§1.5), fed by `/api/meta`.
- One curated "the search that proves it" section: the ontology flow
  (§5.1 of the demo-queries doc) — search finds the slide, segment-context
  shows the speaker *telling the room to look at the slides*, and
  `get-frames` shows the slide. Three steps, one screenshot each.
- A "what's in the archive" grid — the 75 talks, from `/api/videos`, which
  doubles as SEO surface and as the thing conference-goers actually want.
- Credit and the ethics line up front, not just in the footer: results link to
  the original talks on YouTube; vidtheque indexes what it watched and sends
  you back to the source.

**Why it's my pick.** It is the only direction where **the demo is the proof
rather than an analogy**; it holds all three personas in one sentence without
contorting; its enemy is concrete and beatable live; it puts the genuinely
unique mechanism (found by reading the screen) in the hero instead of hiding it
behind "memory"; and it survives contact with §2.3's honesty constraints
without a single soft claim.

### 3.3 Direction C — "Your agent watched the talk" (agent-native)

**The position.** *Your agent can't watch video. vidtheque watches it for them
— and gives back cited moments, not summaries.*

**Who it's for.** Agent builders, Claude Code / Codex users, people wiring
context into assistants. Overlaps Tom's personas only at the edges.

**The enemy: the hallucinated summary** — the assistant that confidently tells
you what a talk said and cannot show you where. Secondary enemy: the context
window (you cannot paste 26 hours of transcript).

**Homepage hero.**
> ### Give your agent a memory of everything you've watched.
> vidtheque indexes your videos — speech, on-screen text and frames — and serves
> them to Claude, Codex or anything that speaks MCP as cited moments, never as
> a summary it made up.
>
> `claude mcp add --transport http vidtheque https://…/mcp`  `[COPY]`
> `[ Or try it in the browser → ]`

**Feature hierarchy.** 1. Cited moments, never summaries. 2. Nine tools, token
disciplined (the honest developer flex: middle-truncation with a `0` opt-out,
`has_more` not counts, server-side clamps, frames by URL because Claude Code
mangles `ImageContent`). 3. Three channels. 4. Remote MCP with OAuth — add it
from claude.ai, not just from a laptop. 5. Self-hosted. 6. The browser demo, as
the thing you show someone who doesn't run agents.

**Demo-site framing.** The AI Engineer corpus is a **public MCP endpoint** —
"add this corpus to your agent in one line, then ask it what the speakers
disagree about." The `ask` mode with its visible activity log is the whole
pitch, live.

**Why not the homepage.** It is the smallest audience of the three and the most
crowded (every MCP directory is full of YouTube servers — 30+ repos per the
landscape survey), it is closest to the "just an MCP server" framing Tom
explicitly rejected, and it puts the plumbing in the hero. **But it is the best
second scroll on the page, and it should be a full section under Direction B** —
because it is also the most *differentiated on execution*: the URL-frame
contribution and the token discipline are publishable work nobody else has done
(HANDOFF §"Tool surface direction"; landscape survey §4).

### 3.4 Where I disagree with the brief, briefly

Tom's framing (#3) is two consumer personas and Direction A. I'm recommending
Direction B, which contains both of them but leads with the corpus instead of
the person. The disagreement is narrow and worth having on the record: **A is
the better promise, B is the better proof, and vidtheque today has proof and no
users.** With 75 indexed talks, no releases, no testimonials and a brand rule
against unearned numbers, a proof-led homepage is the honest one — and it can
keep A's emotional headline register ("Watch once. Find it forever.") as a
tagline in the footer, in the README, and as the story in a launch post. If
vidtheque later ships hosted personal libraries, A becomes correct and the
site swings to it.

---

## 4. Naming / voice kit — for Direction B

### 4.1 Five taglines

1. **Watch once. Find it forever.** *(the memory promise; best for README, X bio, launch post)*
2. **It read the slides, too.** *(the mechanism, in four words; best as an H2 or an OG title)*
3. **Every answer lands on a second.** *(the receipt; best under a search box)*
4. **Your video library, with a search engine inside it.** *(the literal name, explained — `vidéothèque` = video library)*
5. **The talks you watched, still answering questions.** *(the corpus promise; best on the demo site)*

Rejected on purpose: anything with "AI-powered", anything with "unlock", and
anything that starts "The open-source alternative to …" — there is no incumbent
to be the alternative *to*, which is a strength, not a gap to paper over.

### 4.2 Vocabulary

**Use.**

| word | why |
|---|---|
| **archive**, **library**, **corpus** | the name means video library; "corpus" is already the repo's word and stays for the technical register |
| **moment** | the unit of an answer — better than "result", "hit", "chunk" or "segment" |
| **a link that lands on the second** | the receipt, in plain words |
| **on-screen text** | never "OCR" in marketing copy; "OCR" is a method, "on-screen text" is a thing a person understands |
| **spoken** / **on-screen** / **frame** | the three channels, and they are already the badge labels — one vocabulary across product and pitch |
| **it read the screen** | the differentiator as a verb |
| **watched** | not "seen", not "captured" — the input is deliberate (§2.3) |
| **point it at** | the ingest verb: "point it at a channel" |
| **self-hosted**, **your box**, **one file** | the ownership register, stated once, not defended |
| **cite / citation / receipt / proof** | the product's own success criterion |

**Avoid.**

| word | replace with |
|---|---|
| **"MCP server"** *(in any headline, hero, `<h1>`, OG title or tagline)* | *"searchable archive"* / *"search engine for video"*. It stays in the `<meta name="description">`, the README's first paragraph, the nav, and its own homepage section — screenpipe's exact treatment (§1.1). |
| "multimodal" | "spoken words, on-screen text and frames" — name the three |
| "RAG", "vector search", "embeddings", "hybrid retrieval", "RRF" | "search that understands paraphrase"; keep the machinery for `docs/` and the bench posts |
| "second brain" *(as the headline)* | fine in body copy and in a launch post; too crowded and too screenpipe-adjacent to own |
| "everything you've seen" | **"everything you've watched"** — the one-word difference between a recorder and a library |
| "AI-powered", "supercharge", "unlock", "revolutionise" | delete; state the mechanism (PRODUCT.md's voice rule) |
| "transcripts" *(alone)* | it is the least differentiated third of the product; never let it stand for the whole |
| "index" *(as a marketing verb)* | "reads", "watches", "remembers" for people; "index" stays exact in docs |
| any number not from `/api/meta` | the live stat band |

**Two voice rules carried over from PRODUCT.md, unchanged and binding on
marketing copy too:** state the mechanism rather than the benefit; and admit the
gap — a homepage that mentions the early-development callout and the "no
releases yet" fact will read *more* credible in this family, not less
(screenpipe ships a public "Trust center"; herdr prints its licence in the hero
meta line).

### 4.3 The one visual device to build first

The **annotated specimen** (§1.4 steal, §1.5 ledger as its companion): the
`Sir59K8ZDPU-00044` frame at 18:56 — the four-column ontology table — shown
large, on the textured ground, with amber annotation tabs in mono uppercase
pointing at it:

```
ON-SCREEN TEXT ──▸  owl:FunctionalProperty
NEVER SPOKEN   ──▸  (the speaker says "you can look at the slides")
FOUND AT       ──▸  18:56
THE RECEIPT    ──▸  youtu.be/Sir59K8ZDPU?t=1134
```

One image; the entire argument; zero adjectives. Note the caveat from
`demo-queries` §2.2 — pick specimens whose text is legible at the size you'll
render (§2.1 and §2.5 qualify; the Arize trace-tree screenshot does not).

### 4.4 How screenpipe's framing maps — and where vidtheque must diverge

| screenpipe | vidtheque | note |
|---|---|---|
| *"AI powered by everything you've seen, said, or heard"* | *"Everything you've **watched**, searchable to the second"* | one word, and it is the whole difference: capture vs. curation (§2.3) |
| *"remembers how you actually work"* | *"remembers what you actually watched"* | same sentence shape, honest substitution |
| *"record. search. automate."* | **"watch. search. cite."** | copy the staccato triad; the third verb is the divergence — screenpipe's payoff is *action*, vidtheque's is *proof* |
| *"all local, all private, all yours"* | *"your box, your corpus, your file"* | keep the rule of three; drop "private" — there is nothing to be private *about* (§2.3), and claiming it invites a question the product doesn't need to answer |
| Privacy as half the homepage | **Receipts as half the homepage** | this is the space trade that defines the two products |
| Plugins named **"pipes"** | *(open — see §5)* | giving the extension point a noun was smart; vidtheque's candidate nouns are `collections` (tags) and `subscriptions` (channel auto-index), both deferred features |
| `Download for free` + `Star us on GitHub` | `Try the archive →` + `$ docker compose up -d` `[COPY]` | there is nothing to download; the demo *is* the download |
| MCP as one access path among API/SDK/CLI | **identical** | this is the proof that element #1 is achievable, from a 20k-star neighbour in the same category |
| 24/7 capture as the legible claim | **do not have an equivalent, and don't invent one** | the honest legible claim is "it read the slides" |
| Enterprise / Pricing / Trust center | **none of it** | MIT, self-hosted, one owner. Do not build a pricing page for a product that has no price. |

---

## 5. Open questions for Tom

Calls only he can make, roughly in the order they block work.

1. **Direction: B, or A, or a merge?** My recommendation is B with A's headline
   register (§3.4). If you want A, the biggest consequence is that the AI
   Engineer corpus becomes an analogy rather than the product, and the demo
   site needs a different first screen.
2. **Is the creator persona real or aspirational?** It is the sharpest fit for
   B and it needs *zero* new code (a channel is already an ingest unit) — but it
   needs one worked example: your own channel, or a friend's, indexed and
   searched. Without that, it's a claim.
3. **The AI Engineer corpus: what is the ethical and social posture?** The
   footer sentence exists and is good. But: do we ask the organisers first? Do
   we credit them in the hero? Do we accept a takedown gracefully in public?
   The demo site's value depends on this corpus, so the answer is
   load-bearing. (My view: credit prominently, link every result to source,
   tell the organisers before launch, and treat a "yes" as a distribution
   channel.)
4. **One corpus or two on the demo?** A second channel (even a small one) turns
   "the searchable conference" into "the searchable *anything*" and kills the
   "this is just a conference site" read. It costs GPU hours and nothing else.
5. **Domain and name presentation.** `vidtheque.dev` was unregistered as of
   2026-08-08 (HANDOFF). Is the marketing site `vidtheque.dev`, or does the
   demo instance at `vidtheque.<something>.com` *become* the marketing site?
   Directions A and C want a marketing site; **B is the direction where the
   demo instance and the homepage can be the same page**, which is one less
   thing to maintain and one more reason to pick it.
6. **Is "second brain" allowed in body copy?** I've kept it out of the headline
   (§4.2) but it is your phrase and it tests well. Yes/no.
7. **How much of the early-development truth goes on the homepage?** My vote:
   a single quiet line, in mono, near the install box — "early development, no
   releases yet, schemas can change." In this reference family that reads as
   confidence.
8. **Do we want a face?** §1.2's real lesson. A named author with a stated
   belief is free distribution, and yours is already written
   (*"an answer you cannot check is not an answer"*). Byline on the site, or
   keep it a project?
9. **`ask` mode on the public demo — who pays, and does it survive a front
   page?** 50 asks/day is a rounding error against a Hacker News spike. Either
   the budget goes up for launch week or ask mode is visibly capped and the
   page says so.
10. **Naming the extension point** (§4.4). Do `collections` / `subscriptions`
    become nouns we market, or do they stay features? screenpipe's "pipes"
    suggests there's value in owning a word here.
11. **Migration 0003.** Every OCR example on the demo is currently constrained
    to single-line matches (§2.3). Do we land 0003 before the site copy is
    frozen, so the flagship examples can be the multi-term ones?

---

## 6. One-paragraph summary, if the rest of this is too long

vidtheque is not an MCP server; it is a **searchable archive of video you chose
to keep**, and its unique move is that **it reads the screen** — it answers
questions from text that was never spoken, and every answer is a moment with a
link that lands on the exact second. screenpipe is the right neighbour and the
wrong mirror: it records your machine involuntarily and must spend its homepage
on privacy; vidtheque ingests published video deliberately and should spend that
same space on receipts. Lead with the AI Engineer archive because the demo is
the proof, name YouTube's search box as the enemy, put MCP sixth on the feature
list where screenpipe already proved it belongs, and build two things first: a
live stat band read out of the index (herdr), and one annotated slide specimen
with amber callout tabs (impeccable) that makes the whole argument in a single
image.

---

## Refinement round 1 (Tom + orchestrator, 2026-08-10, ~01:00)

**Tom's critique of Direction B as recommended:** "searchable archive" sounds
like a 2025 product — RAG over YouTube. That may be what the mechanism is, but
the goal is something dynamic: *ask your agent to implement an AI feature, and
the agent — armed with vidtheque — answers with the SOTA according to recent
AI Engineer talks.* vidtheque provides knowledge **derived** from video; it
**solidifies** things that were only ever spoken or shown on screen. And the
screenpipe acquisition asymmetry (§A's weakness) is not permanent: automatic
capture — follow a channel, it keeps watching — is an easy future feature.

**The fused direction (B′ — "working knowledge from video"):**
- Position: talks are where the state of the art appears first and evaporates
  fastest; vidtheque solidifies them into verbatim, timestamped, quotable
  knowledge on tap for you *and your agents mid-task*.
- Enemy: the gap between what the field just said and what you can act on —
  knowledge trapped in video. (B's YouTube-search-box enemy survives as a
  mid-page demo beat, not the headline.)
- The demo corpus reframes from "an archive of AI Engineer 2026" to "the SOTA
  according to the people shipping it — ask it something."
- Roadmap line that makes the position true by construction: **follow
  channels; vidtheque keeps watching** (channel indexing + a refresh schedule
  — mostly exists). Deliberate subscription, not involuntary capture: keeps
  the no-surveillance-debt advantage.
- Proof obligation: the "agents use it mid-task" claim needs a shown artifact,
  not a sentence — a real captured agent exchange (the 2026-08-09 field test's
  synthesist transcripts are exactly this) as the demo page's third beat.

Candidate hero copy, this register (draft, unchosen):
> **The field talks. Your stack listens.**
> vidtheque turns the videos that matter into solid, timestamped knowledge you
> and your agents can actually use. Ask what the state of the art is; get the
> sentence, the slide, and the second it happened.

Alternate headline: "Knowledge is announced in videos. vidtheque makes it
usable." — with B's "It read the slides, too." demoted to the annotated
specimen's caption.

**Status: awaiting Tom's confirmation of B′ before landing-page versions are
built against it.**
