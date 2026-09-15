# Decision record

Settled 2026-08-08 with Tom, consolidating the "Open questions" sections of
`tool-surface.md`, `index-schema.md`, `research/mcp-framework-oauth-research.md`,
and `research/pipeline-tooling-research.md`. Where a design doc disagrees with
this file, this file wins; fold changes back into the docs as implementation
touches them.

## AI Engineer Paris 2026 edition, set by the orchestrator (flag to Tom)

The hero line, the 50-term follow bound, and the organizer gate below are
orchestrator defaults flagged to Tom. They remain binding unless Tom overrides
them.

Set 2026-09-15. The edition is `GET /paris` in the existing Next.js app, under
the same document policy as `/demo`. It is not a new host or deployable. The
MCP tools stay unchanged; the public additions are one facade read and
optional tag filters on the existing search and Ask reads.

The existing tag validator requires namespaced tags, so the brief's three tag
labels are represented as `series:aie-paris-2026`,
`series:aie-paris-2026-stream`, and `series:aie-paris-2026-talk`. A day VOD
and a later talk upload remain distinct videos. The page prefers an explicitly
mapped talk upload and otherwise uses the mapped span in a day VOD.

The committed schedule is Python-owned JSON at
`mcp/src/vidtheque_mcp/editions/aie-paris-2026.json`. The additive
`GET /api/editions/{slug}` facade read is its browser boundary. Search and Ask
accept the existing namespaced `tags` filter, and Ask applies that scope to
every internal search and segment read.

Only the main stage is treated as streamed. The other schedule rows remain
visible with the exact label "not streamed; may arrive later as individual
uploads". Main-stage rows expose three honest states: `not_yet_indexed`,
`indexed_not_aligned`, and `aligned`. Unknown VOD ids and offsets stay null.

The second hero line is "Every main-stage talk from AI Engineer Paris, cited
to the second, slides included. Point your own agent at it." The
"main-stage" qualifier admits the 23 schedule sessions that were not streamed.

One auto follow watches the channel's streams and videos, applies the common
edition tag, uses `backfill=0` and `max_per_check=2`, and matches `Paris` plus
the schedule's speaker names. The global `MAX_TITLE_TERMS` bound is 50, while
the 80-character per-term limit stays unchanged. Operators add the stream or
talk subtype tag with the existing `tag-video` tool after classification.

Voxtral is an API-backed STT worker backend for this edition only. It receives
a bounded per-request `context_bias` list over the existing HTTP seam, uses
word timestamps without a language pin, and treats `align=True` as a no-op.
Calls cover at most 60 minutes, overlap by 30 seconds, run sequentially, and
deduplicate safe word matches at each seam. Diarization stays off because the
shared transcription response has no speaker field and the talk table already
provides attribution.

The bias list prioritizes speaker names, companies, the committed AI lexicon,
then distinctive title terms, with 100 terms at most. The private three-arm
2025 evaluation compares the existing whisperX transcript, Voxtral without
bias, and Voxtral with bias on proper-noun term accuracy. It prints current or
operator-supplied pricing before execution and does not publish private source
material, per-talk results, or aggregate results.

The edition indexing box raises the existing duration guard to 39,600 seconds
without using its `0` opt-out. The existing 600-keyframe cap, batch limits,
heartbeats, five-minute stale-claim recovery, and three-attempt queue limit
remain unchanged. The two 2025 videos may be used only in an isolated private
data directory for the dress rehearsal.

Transcript and slide OCR remain untrusted evidence, are rendered as text, and
are not rewritten by a prompt-injection filter that would alter receipts. The
pre-publication audit is a delta over the existing public baseline, covering
the money-bearing Mistral credential, edition facade, and untrusted content
handoff.

The organizer gate is satisfied when the request has been sent and no refusal
has arrived before the announcement. An explicit refusal blocks publication.
The public page keeps the existing source links and "Removal on request"
footer.

## Frontend replacement, decided by Tom, 2026-09-05

All three web surfaces move to Next.js and React: the landing at `/`, the
demo at `/demo`, and the management dashboard at `/dashboard`. Cutover waits
until all three replacements are ready, including the dashboard's existing
read and write workflows. Tested migration PRs may land before cutover.

Browser API requests go directly to Python. Tom chose this over relaying
browser requests through Next to keep the implementation simple. Python
retains authorization, sessions, and resource limits. Next renders pages and
may read Python over HTTP for server rendering. Cross-origin browser access
requires an explicit CORS policy; same-origin deployment avoids that need.

Dashboard HTTP responses use typed values and explicit state fields. React
owns display formatting, such as seconds to `4m 12s`, dates, counts, and
badges. Python retains policy text, including refusal messages and truncation
notes. Tom chose this split over carrying the current preformatted strings
into the new frontend.

This supersedes the Jinja2 and no-build-step frontend choice in
`dashboard.md` and `PRODUCT.md`. Existing authorization, public read-only
behavior, payload bounds, and the HTTP-only `mcp/` to `worker/` boundary
still apply. Migration work and remaining design choices live in
`docs/ROADMAP.md`.

## Dashboard pages fetch client-side, decided by Tom, 2026-09-05

Next serves a data-free shell for every `/dashboard*` page. The browser then
calls `/dashboard/api/*` same-origin, carrying its own `vidtheque_session`
cookie, and React renders from that response. Every write is a browser call to
Python's existing `POST` routes under `/dashboard/*`, under the cookie and
Origin rules of `dashboard.md` §3.3. Next never sees the cookie, forwards
nothing on a visitor's behalf, and caches nothing per user.

Two consequences that fall out rather than needing rules of their own. The
public read-only projection works unchanged, because the API already answers
anonymous reads there. And a `401` from the API is what sends the browser to
the login page — the refusal is the signal, so there is no second place where
authorization is decided.

Tom rejected server rendering with cookie forwarding: it makes Next a
credential relay that has to forward the session cookie and the visitor's
address on every read and must never cache a response across users, which is
three ways to leak one reader's dashboard to another. A hybrid — some pages
rendered on the server, some fetched — was rejected for the same reason, since
one forwarded cookie is the whole cost.

Route ownership under `/dashboard` follows in
`docs/design/frontend-migration.md`; this record is the choice, not its
expression in configuration.

## Dashboard writes answer by content negotiation, decided by Tom, 2026-09-05

Every write route under `/dashboard` keeps one URL and answers in the medium
the caller asked for. With `Accept: application/json` the handler answers JSON:
on success a typed outcome — the job id and its new state for cancel and
retry, the accepted queue entries for the index form, the resulting tag list
for tags, the follow row's new state for each follow action — and on refusal
the existing `{error, message, next}` envelope at the status the code maps to,
plus `retry_after_s` and a `Retry-After` header when the refusal names a delay.
Otherwise the handler answers the `303` redirect the Jinja pages expect,
unchanged. The redirect branch is deleted with the last Jinja page.

Same handler, same guard, same Origin evidence rule, same rate-limit bucket.
The fetch caller sends the session cookie same-origin, so `dashboard.md` §3.3
holds unchanged: `Sec-Fetch-Site: same-origin` or a matching `Origin` header is
required of it exactly as of the form. Form-encoded bodies stay the input for
both branches.

Tom rejected separate `/dashboard/api/*` POST routes: two routes per write to
guard, to bucket and to keep in step, for one contract. He also rejected plain
form navigation from React, which shows no inline outcome — the jobs view's 2 s
poll cannot say what a cancel decided.

The contract is `docs/design/dashboard.md` §21; what the React client sends and
receives is `docs/design/frontend-migration.md` §9.

## The Python dashboard HTML is gone, decided by Tom, 2026-09-06

Every `GET /dashboard*` page is served by the Next.js app, so the Python HTML
surface under that prefix is deleted rather than kept as a fallback: the nine
page handlers in `views.py` and the two the write side served as `GET`s (the
index form, the sign-in page), the fifteen Jinja templates, `dashboard.css` and
the two ES modules, the `/dashboard/static/*` asset route, the Jinja
environment, and `jinja2` as a dependency of `mcp/`. Python keeps
`/dashboard/api/*`, the thirteen `POST`s and the `/dashboard/` redirect. This
completes the cutover the first entry above made conditional on parity.

Two consequences Tom settled with it. **A refusal answers the same envelope in
both media** — there is no page left to render one into, so `_error_page` goes
and `_refusal_json` serves both branches, the `401` included; a signed-out form
`POST` is told `E_AUTH_REQUIRED` rather than redirected, and the shell decides
where to go. **The `303` stays on the success branches**, because the sign-in
page and the sign-out button are real `<form method="post">`s and a submit
before React has hydrated is a navigation that needs somewhere to land; the
targets are paths Next serves, so they stay as paths.

The rendered strings the two jobs routes and the cue pager carried are cut in
the same round, which is the rule the 2026-09-05 typed-values decision set:
delete a rendering in the commit that deletes its reader. `basis` — the
sentence saying what a job's progress percentage is computed over — survives as
policy text, which is the same split that keeps refusal messages Python's.

`public/static/fonts/` is not part of this: DESIGN.md makes it the document of
record for the two faces and the web app is diffed against it. Only the
`/dashboard/static/fonts/` alias onto it went.

The removal is `docs/design/dashboard.md` §23; the route ownership it settles
is `docs/design/frontend-migration.md` §1d.

## The public library is gone, decided by Tom, 2026-09-07

`GET /videos` and `GET /videos/{id}` — the reader's library, live since
2026-09-01 — duplicated `/dashboard/videos` and `/dashboard/videos/{id}`, which
read the same corpus and carry the owner's fields on top of it. Two readers of
one table is one reader too many, so the public pair goes entirely rather than
being kept as a lesser copy of the management surface. The owner's pages and
`/dashboard/api/library*` are untouched.

What went with them: the pages and their boundaries, the `unstable_cache` reads
and the `library` cache tags in `web/src/lib/library.ts`, the `VideoCard` the
grid was made of, and the facade's `GET /api/videos/{video_id}` — added on
2026-09-01 for that detail page and called by nothing else, so it went with its
only caller (demo-site.md §2.2.1). `GET /api/videos`, the listing, stays: the
demo's cold page lists the corpus from it. So does
`GET /videos/{id}/export.md`, which was always Python's and is now the only
thing under that prefix.

The links that pointed into the library go back to where the Python demo sent
them before it existed: a result card's title to the talk itself — the moment's
own link with the `?t=` taken off — and an ask citation's title to the moment it
cites. That is `app.js`'s own rule, and it keeps demo-site.md §6 item 4's
promise that the page returns people to the original talks.

The route table is `docs/design/frontend-migration.md` §1a.

## Decided by Tom (2026-08-08)

1. **MCP stack: official `mcp` SDK 2.0** (2026-07-28 spec). No fastmcp
   dependency. Vidtheque is its own OAuth authorization server: CIMD with DCR
   fallback, HS256 JWT access tokens, hashed rotating refresh tokens in
   `auth.db`. Auth modes: `none` | `token` | `oauth`.
2. **User model: single-user behavior, multi-user-ready schema.** Every
   user-owned table carries `owner_id` (constant `1` for now, FK to a
   single-row owners table). No per-user filtering in v1 queries beyond the
   column existing. Multi-user is explicitly out of scope for v1.
3. **Retention: delete original video files after indexing.** Keep: extracted
   audio (enables STT re-runs), keyframe JPEGs, index. `get-clip` (future)
   re-downloads on demand. Env override `VIDTHEQUE_KEEP_SOURCE=none|audio|originals`
   (default `audio`).
4. **Text embeddings: `Qwen3-Embedding-0.6B` default, 1024 dims.** Model and
   dims pinned in the `config` table; upgrading to Qwen3 4B/8B later = config
   change + per-stage re-embed job (Matryoshka lets dims stay 1024). bge-m3
   remains an available worker backend, not the default.

## Defaults set by the orchestrator (flag to Tom if they bite)

- **STT policy** (`VIDTHEQUE_STT_POLICY`): `prefer_whisperx` default; YouTube
  auto-caption `json3` (per-word timestamps, verified) as the zero-GPU
  fallback and fast path; per-stage versioning makes "index with auto-caps
  now, upgrade to whisperX later" a supported flow.
- **Param naming:** intra-video time axis is `t_start`/`t_end` (resolves the
  collision with pagination `offset`). Corpus axis stays `published_after/before`.
- `order=video_time` without a single-video scope → typed error.
- **v1 tool cut:** `tag-video` stays (9 tools). Subscriptions deferred
  (`index-video expand=channel_recent` covers on-demand); revisit post-v1.
  `corpus-summary` and `list-videos` stay separate.
- **Frame URLs:** HMAC-signed, default TTL 24h (`VIDTHEQUE_FRAME_URL_TTL`),
  bearer token also accepted on `/frames/*`.
- **Retrieval:** hybrid (FTS + vector, RRF k=60) is the default for text
  legs; zero FTS hits → semantic-only leg with a `note:` line, never a
  silent empty.
- **Tokenizers (measured):** `porter` for transcript/metadata FTS,
  `unicode61 tokenchars '_-./'` for OCR FTS. Query layer quote-wraps terms.
- **Tool description budget:** ≤ ~120 words each; shared rules live in the
  `guide` resource, not repeated per tool.
- **Diarization: off by default** (pyannote 4.x ~12GB VRAM regression + HF
  gating). `speaker=` filters return `E_FEATURE_DISABLED` with a docs
  pointer. Lease sizing documented at ~12GB when enabled.
- **Word timings:** JSON-per-cue in v1 (~10% of DB, acceptable); packed
  binary noted as a later optimization.
- **Segment-level tags:** deferred (needs durable segment identity).
- **Owner credential UX:** `VIDTHEQUE_PASSWORD` env for the OAuth login page
  in v1; pairing-code flow is a later nicety.
- **Frame embeddings:** SigLIP 2 `google/siglip2-so400m-patch16-naflex`
  (1152 dims) via transformers ≥5.14 (open_clip cannot load NaFlex).
  Both towers of that one checkpoint are served from one lifecycle slot:
  `POST /v1/embeddings/image` indexes keyframes, `POST
  /v1/embeddings/frame-query` runs the *text* tower so `:q_img_vec`
  (index-schema §4.5) has an encoder. A sibling path, not a `space=` flag on
  `/v1/embeddings`: after the hosted-provider `WORKER_URL` swap an unknown
  field is ignored and answers with the wrong space, an unknown path 404s.
  The text tower is trained to 64 tokens — queries only, never prose. The
  transformers pin is in fact ≥4.56 (whisperX caps `huggingface-hub`), so the
  worker applies the lowercase + pad-to-64 the 5.x processor would.
- **Worker fixes from research:** OCR dependency is `rapidocr` 3.9.2 (not
  the frozen `rapidocr-onnxruntime`); OCR is CPU-only (no GPU lease
  involvement); PySceneDetect `ContentDetector` needs an explicit weights
  override for near-greyscale screencasts.
- **yt-dlp heatmap** ("most replayed") captured at index time into the
  videos table for future ranking use — cheap now, nobody else has it.
- **Lease semantics: resident models hold VRAM but never the lease;
  acquire/release bracket non-resident GPU work only.** `EMBED_RESIDENT=1`
  costs a measured 1.5 GB for the life of the process, so bracketing it would
  fire `GPU_ACQUIRE_CMD` at the first embedding request and never fire
  `GPU_RELEASE_CMD` again — the co-tenant stopped forever. CPU backends (OCR,
  0 MB) are outside the bracket for the same reason in reverse: they never
  contend, so they must never stop a co-tenant. Measured in
  `research/gpu-validation-2026-08-08.md` §5.2–5.3.

## Amended 2026-08-11 (repo cleanup; flagged in the cleanup PR for Tom)

- **Embedding default superseded: `Qwen3-VL-Embedding-2B`, both legs.**
  Decision 4 (text: `Qwen3-Embedding-0.6B`) and the SigLIP 2 frame-embeddings
  default above describe the 2026-08-08 stack. Since 2026-08-10 the shipped
  default is `EMBED_BACKEND=qwen3-vl-embedding` / `IMAGE_EMBED_BACKEND=
  qwen3-vl-embedding` with `Qwen/Qwen3-VL-Embedding-2B` on both legs — one
  model, one lifecycle slot, one vector space (`deploy/.env.example`, the env
  document of record; evidence `research/multimodal-embedding-2026-08-09.md`,
  load-contract fix `research/embedding-random-init-2026-08-10.md` §4).
  `Qwen3-Embedding-0.6B`, SigLIP 2 and BGE-M3 remain selectable worker
  backends, not defaults.

## Amended 2026-08-15 (following channels; decided by Tom)

- **Subscriptions are no longer deferred.** The v1 cut above reads
  *"Subscriptions deferred (`index-video expand=channel_recent` covers
  on-demand); revisit post-v1"* — this is that revisit, and the answer is yes.
  `positioning.md` (LOCKED) makes "follow the builders" the first pillar and
  names *follow channels* as the roadmap line that makes the position true by
  construction; on-demand expansion does not keep watching. The full contract is
  `docs/design/following.md`; the storage was already in the schema
  (index-schema §1.8) and migration 0006 adds `follows`, `follow_seen`, the
  `follow_check` job kind and `jobs.collection_id`.
- **Arrival is automatic, bounded by a daily budget.** Matching uploads index
  themselves until the day's hours-of-video budget is spent
  (`VIDTHEQUE_FOLLOW_DAILY_HOURS`, **default 16** — proposed at 8, set to 16 by
  Tom on 2026-08-15 against his own box); the rest are `held_budget` and
  reconsidered on the next check,
  **never dropped**. The budget is counted in hours of *video* because a check
  knows a candidate's duration before it knows what indexing will cost, and it
  is global across every follow because five follows would otherwise spend five
  budgets. Per-follow `mode=review` overrides to hold everything for a human.
  Rejected: review-by-default, which makes the product a queue of chores and
  contradicts the pillar it serves.
- **The v1 tool cut becomes 10 tools.** `follow-channel` (tool-surface §4.10) is
  the write side, dispatching on `action` (follow | unfollow | pause | resume |
  check_now); reading stays on `corpus-summary include_follows=true` rather than
  becoming an eleventh tool. **The tool budget was the whole deferral argument
  and the cost is recorded rather than waved away** — it is +1 where the §6
  sketch priced +3. Tom's stated intent is that *"we might merge all the
  dashboard management tools later on into one single tool"*, so the design
  constraint follows from it: the tool dispatches on `action`, and no parameter
  is named in a way that would not survive that merge.

## Amended 2026-08-28 (consolidation; lifted from `research/`)

- **Reranker: deferred, not rejected.** `Qwen/Qwen3-VL-Reranker-2B` is the only
  viable candidate; the 8B is operationally disqualified (17.6 GB of BF16
  weights, an estimated 18–23 GB working set, against a 24 GB card that already
  carries the ~12 GB llama.cpp lease and the 4.3 GB embedder). Evidence
  `research/reranker-research-2026-08-10.md`.

  Revisit at roughly **500 videos** or when telemetry yields a stable hard-query
  set — **corpus size alone must not trigger adoption**. Ship only if *all four*
  hold: ≥10% of representative queries show an **ordering** error (a relevant
  result inside fused top-20 but missing top-5 — a missing candidate is a recall
  failure a reranker cannot fix); ≥0.05 absolute gain in nDCG@5 or MRR@5 with no
  regression on exact identifiers, natural-visual queries or per-video
  diversity; added warm p95 ≤500 ms per search and ≤2 s per ask-mode run; and a
  3090 test showing safe peak VRAM across 20 acquire/release cycles with the
  llama.cpp lease reliably restored. Otherwise it is at most an explicit
  high-precision path, never the default. Budget 2–4 days plus the evaluation.

  *Note (2026-08-28): the private corpus is at 479 videos, so the first half of
  the revisit trigger is about to fire. The four conditions are what decide it.*

- **Vendored fonts stay under a 200 KB total budget**, OFL-licensed,
  latin-subset, no CDN and no runtime network request. The rule outlives the
  faces it was written against — the retired Inter + Instrument Serif pair and
  the current Archivo + JetBrains Mono pair (75 KB) both answer to it.
