# Decision record

Settled 2026-08-08 with Tom, consolidating the "Open questions" sections of
`tool-surface.md`, `index-schema.md`, `research/mcp-framework-oauth-research.md`,
and `research/pipeline-tooling-research.md`. Where a design doc disagrees with
this file, this file wins; fold changes back into the docs as implementation
touches them.

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
