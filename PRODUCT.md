# Product

<!-- impeccable:product-schema 1 -->

> **How this file was written.** Derived from `CLAUDE.md`, `README.md`,
> `docs/design/dashboard.md`, `docs/design/demo-site.md`,
> `docs/design/tool-surface.md`, `docs/design/index-schema.md` and
> `docs/design/DECISIONS.md` — all of them records of decisions Tom has already
> made and signed off. No interview was run: the agent that wrote it had no
> question tool in its surface, and the brief directed it to derive product
> truth from those files rather than ask. Facts here are cited to a file where
> the source is not obvious. Anything genuinely undecided is marked
> **[undecided]** rather than invented.
>
> **Aligned 2026-08-10:** `docs/design/positioning.md` is LOCKED and
> authoritative for framing, voice and vocabulary. Where this file and the
> positioning contract touch, the contract wins; this revision folds it in
> (position, word law, surface roles, the wordmark decision).

## Platform

web

## Users

**One person: the operator who self-hosts this instance and owns the corpus.**
Today that is Tom Vaucourt on his own box. `owner_id` is a constant `1`
(`DECISIONS.md` #2 — single-user behaviour, multi-user-ready schema), so there
is no second audience with different permissions, and there is no user
management, no roles, no invitations.

Two situations, and they are not the same job:

1. **At the terminal, mid-batch.** Something has been running for twenty
   minutes and the operator wants to know whether it is working, waiting, or
   quietly broken — at 03:00, on a laptop, over an SSH tunnel.
2. **After the fact, answering a question about the index itself.** Which model
   transcribed this? Why does this video have no on-screen text? What did OCR
   actually read off that frame, and where on the frame did it read it?

A third consumer exists and is not a person: **an MCP client** (Claude and
friends) talking to `/mcp`. Agents are the right consumer of the corpus and the
wrong consumer of the corpus's plumbing — that inversion is the stated reason
the dashboard exists (`docs/design/dashboard.md`, opening decision).

## Product Purpose

**vidtheque empowers AI with the knowledge of the builders and creators**
(`positioning.md`, the position). It turns the channels you follow — the
videos you *didn't have time to watch* — into solid, timestamped knowledge:
every sentence spoken, every line that crossed the screen, every frame, and it
answers with **receipts that land on the exact second**
(`https://youtu.be/ID?t=123`) — the sentence, the slide, and the second. Your
agent watched it so you didn't have to. It is a self-hosted MCP server: two
services (`mcp/` on CPU, `worker/` on GPU), one SQLite index, HTTP between
them.

Success is a question answered with a link that proves it. A vidtheque answer
that cannot be checked against the source video has failed even when it is
correct.

## Positioning

**The contract is `docs/design/positioning.md` (LOCKED, Tom, 2026-08-10)** —
the position, the three pillars (follow the builders · your agent watched it ·
receipts, always), the twin line, the personas, the blessed and banned
vocabulary, and the word law (ownership attaches to the *choice*, never the
viewing; the agent is the one who watched). Every public string on every
surface answers to it. What belongs here is the substantiation — the mechanism
a neighbouring product could not truthfully copy:

- **Three channels over one timeline.** Spoken transcript, on-screen text (OCR,
  with the box coordinates kept), and frame embeddings all indexed against the
  same second of the same video, so a query can be answered from whichever
  channel actually holds the evidence and the citation still lands.
- **Provenance is a first-class column, not a log line.** Every stage of the
  pipeline (`fetch · stt · chunk · text_embed · keyframe · ocr · frame_embed`)
  records its own state, its own `model_key`, and its own clock, per video.
  The index can therefore explain what it is made of, and admit what it is
  missing.
- **`all` means all.** A leg or a filter that cannot apply prints a `note:`; it
  never silently narrows the result (`CLAUDE.md` invariant).
- **Self-hosted and single-operator by construction.** Your corpus stays on
  your box, on one SQLite file, with one writer.

## Operating Context

- **Deployment:** `docker compose up -d` in `deploy/`; one image, one process
  for `mcp/`, an optional GPU `worker/`, an optional `cloudflared` tunnel
  profile. `deploy/.env.example` is the document of record for every knob.
- **Access:** most often over an **SSH tunnel on an arbitrary local port**, so
  every asset URL the surfaces emit must survive being served from a prefix
  nobody predicted. This is a shipped lesson, not a hypothetical
  (`docs/design/dashboard.md` §8).
- **Auth modes:** `none` | `token` | `oauth` (`DECISIONS.md` #1). A browser that
  is not signed in gets a page, not a JSON blob.
- **Three web surfaces, one product** (aligned 2026-08-11: `/` is the landing,
  the demo moved to `/demo` — commit `4ddd45d`):
  - `/` — the landing, static by design; the argument before the product.
  - `/demo` — the public demo, a read-only projection of the same corpus,
    gated by `VIDTHEQUE_PUBLIC_READONLY`. Its role per the positioning
    contract: **the proof** — the corpus on tap, ask it something; it searches
    and answers, it does not manage (`docs/design/demo-site.md` §6,
    `positioning.md` surface implications).
  - `/dashboard` — the management surface. Five routes: corpus overview,
    videos table, video detail, jobs, job detail.
- **The GPU is leased.** On Tom's box the worker shares a 3090 with a llama.cpp
  LXC through `GPU_ACQUIRE_CMD`/`GPU_RELEASE_CMD` hooks; public defaults leave
  them unset. Jobs therefore wait for reasons that are real and need to be
  visible.
- **Sources push back.** YouTube bot-checks and rate-limits are ordinary
  operating conditions (commit `a5b7cd4`), so "deferred, retrying in 300s" is a
  normal state the surfaces must be able to state plainly.

## Capabilities and Constraints

**Capabilities.** Index a video, playlist or channel; hybrid search (FTS +
vector, RRF) across transcript, on-screen text and frames; per-video summary;
segment context; frame retrieval by signed URL; tagging; a job queue with
retries, deferral and an event log; a management dashboard; a public demo
projection.

**Durable technical constraints — these bind every future surface:**

- **`mcp/` ↔ `worker/` is HTTP only.** No Python imports across the boundary,
  not even in tests. All state (SQLite, keyframes, jobs) lives in `mcp/`.
- **One SQLite file, one writer.**
- **Server-rendered Jinja2 with autoescape on, plus plain ES modules.**
  **No build step. No external requests at runtime.** No `| safe`, no HTML
  sinks, `safeUrl()` on every URL.
- **Every asset path is relative or root-relative**, never built from
  `PUBLIC_URL` — see the SSH tunnel above.
- **Light and dark schemes are both first-class**, driven by
  `prefers-color-scheme`. A persisted three-state toggle is deferred to phase 3
  and must arrive without an inline `<script>`; the pages stay CSP-ready.
- **Token discipline** on every payload: middle truncation with a documented `0`
  opt-out, pagination hints printed in the payload, double caps (items *and*
  chars), expensive paths bounded independently of `limit`, and **server-side
  clamps — never prompt-only limits**. The URL is an input, not an instruction.
- **`has_more` over exact totals.** No duplicated count queries.
- **Two time axes, never overloaded:** `published_*` selects videos, `offset_*`
  selects positions inside one.
- **Frames by authenticated URL by default**; inline base64 is opt-in, with the
  correct mimeType.
- Python 3.12, `uv` workspace, committed `uv.lock`, digest-pinned CUDA base
  image. `make test` is CPU-only and must never download a model or need a GPU.
- No self-hosted CI runners on this public repo.

**Terminology that must not be renamed by a designer.** `cue` (the transcript
unit a hit points at) · `chunk` (the span of cues that became one embedding) ·
`shot` and `keyframe`, with `dup_of` for a deduplicated frame · `stage` (one of
the seven, each with a `state` and a `model_key`) · `job`, `job item`,
`not_before`, `attempts` · `index_state` · `data_status` (`corpus-summary`'s
own word, printed verbatim) · `owner`. Four state vocabularies exist and are
deliberately **not** unified; no surface may invent a fifth.

**Non-goals** (`docs/design/dashboard.md` §1): not a video player — source media
is deleted after indexing and the citation contract is to send the human back to
YouTube; not multi-user SaaS; not a replacement for the MCP surface; not a
config editor — the dashboard displays resolved settings and never writes them;
not an analytics product — there is no time-series table, so no charts with a
time axis.

## Brand Commitments

- **Name:** `vidtheque`, lower case, always. Wordmark is the word.
- **Licence:** MIT, public repo. Owner: Tom Vaucourt.
- **Voice:** plain, specific, and willing to admit what is missing. The existing
  copy states the mechanism rather than the benefit ("the index, explaining
  itself"; "summed from the column, not walked on disk"). Every state is a
  **word** as well as a colour — that is an accessibility rule and a voice rule
  at the same time.
- **The favicon is the v.** (chosen 2026-08-11, replacing the drawn film
  frame): the wordmark's `v` with its gold full stop, floating transparent
  with a dark keyline so one SVG reads on both tab-strip themes. An inline
  `data:` SVG shared verbatim by both surfaces so they read as one product in
  a tab strip. It is the word abbreviated — the tab says what the rail lockup
  says — and it never appears inside a page beside the word.
- **The logo is the wordmark: `vidtheque.` — lowercase, with the period.**
  Decided 2026-08-10 with the landing-page identity: the gold full stop is the
  receipt ending the sentence, and it recurs as the timestamp dot. A font
  logo, nothing drawn. The exact face, weight and tracking are `DESIGN.md`'s
  business (re-specced in the 2026-08-10 amendment that made the landing's
  gold-on-black system normative); the *commitment* belongs here: the
  product's name gets a voice of its own, and no other string on a product
  surface borrows it.
- **A landing page exists** (2026-08-10, "projection room" — the maximal
  expression of the identity; `DESIGN.md` per-surface guidance). It is the
  only marketing surface; nothing else external exists — do not invent brand
  assets beyond the wordmark and the shared favicon.

## Evidence on Hand

- A real corpus lives on Tom's box; the test suite runs against a seeded
  fixture corpus (`mcp/tests/conftest.py::seed`) that carries deliberately
  hostile strings, a half-indexed video, a failed stage, a deduplicated
  keyframe and three job shapes.
- `research/` holds the receipts behind the contracts and is append-only.
- **No benchmarks, no user counts, no testimonials, no published images, no
  releases exist.** README says so in a callout. Nothing may present a number
  that is not read out of the index at render time.

## Product Principles

1. **The index explains itself.** Every surface answers a question a human
   actually has about the plumbing, and answers it without an agent in the
   middle.
2. **Citation is the contract.** Anything that cannot be traced back to a
   second of a real video is not an answer.
3. **Admit the gap.** Missing on-screen text, a failed stage, a job that is
   deferred, a vector leg that is off — these are printed, named, and counted.
   Silence is the defect.
4. **Bounded by construction.** Every list is clamped server-side; every page
   loads a predictable amount whatever the URL asks for.
5. **It stays self-hostable.** One compose file, no build step, no runtime
   network dependency, works behind a tunnel on a port nobody predicted.

## Accessibility & Inclusion

No formal standard has been declared **[undecided]**, but two rules are already
shipped and binding: **state is always a word as well as a colour**, and there
is **one visible `:focus-visible` ring on everything focusable**, including
table rows and result rows. A `prefers-reduced-motion` block exists on both
surfaces. Layout shift is treated as a defect: every image ships explicit
`width`/`height` or lives in a fixed-aspect box.

## Surfaces and modes

Mode is a property of the surface, not of the product. All three surfaces
share one identity (the 2026-08-10 `DESIGN.md` system: the landing's world)
and differ in register, not in world.

- **The landing — `Sell`.** The maximal expression: the wall, the lift, the
  receipts performed. Static by design; the trailer, not the movie.
- **`/demo` (the public demo) — `Persuade`, in continuation of the landing.** The
  proof for someone who arrived from a link: live search, ask-with-citations,
  the same aesthetic carrying into a working product
  (`positioning.md`: the demo is the proof).
- **`/dashboard` — `Operate`.** The minimalist end of the same system: a true,
  informative control surface. Scanability, density and consistency outrank
  expression; it sells nothing and narrates nothing (`positioning.md`: the
  instrument — its charisma is receipts rendered perfectly).
