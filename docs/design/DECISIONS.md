# Decision record

Settled 2026-08-08 with Tom, consolidating the "Open questions" sections of
`tool-surface.md`, `index-schema.md`, `research/mcp-framework-oauth-research.md`,
and `research/pipeline-tooling-research.md`. Where a design doc disagrees with
this file, this file wins; fold changes back into the docs as implementation
touches them.

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
