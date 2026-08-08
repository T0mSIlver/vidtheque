# screenpipe query/tool surface — full deep-dive report

Researched 2026-08-08 by an Opus subagent from primary sources: `screenpipe/screenpipe@078d779` (shallow clone), npm `screenpipe-mcp@0.19.1` plus early tarballs (0.2.0), GitHub API for history/issues. Local API on `localhost:3030`.

Purpose: adapt screenpipe's proven query surface to our self-hosted YouTube-corpus MCP (transcript + OCR + frame embeddings, cross-video search, timestamped results).

---

## 1. MCP tool surface

`packages/screenpipe-mcp/src/index.ts` (2473 lines) — **28 base tools + 3 enterprise `team-*` tools**, registered only when `SCREENPIPE_ENTERPRISE_TOKEN` resolves. All names are **kebab-case**. Every read tool carries MCP `annotations` (`title`, `readOnlyHint`, `idempotentHint`, `openWorldHint`).

### 1.0 Naming history

`search_screen` / `get_recent_context` were **never** screenpipe tools. The original MCP was Python (2024-12-18), shipping exactly one tool, kebab-case `search-content`, with `q / content_type / limit / offset / start_time / end_time / app_name / window_name / min_length / max_length` — parameter names unchanged 20 months later. npm 0.2.0 (2026-01-22) added desktop-automation tools (`click-element`, `fill-element`, `find-elements`, `open-application`, `open-url`, `pixel-control`, `scroll-element`) — **all removed in 0.6.0** ("Remove operator API"). Automation later returned only as a *read projection* (`purpose: "automation"`), no-store, with ids explicitly declared not to be live handles.

### 1.1 `search-content` — the primary tool

Backs onto `GET /search`; forwards every supplied argument as a query param verbatim.

| param | type | default | notes |
|---|---|---|---|
| `q` | string | — | FTS5 query. Description warns: *"Avoid for audio — transcriptions are noisy, q filters too aggressively"* |
| `content_type` | enum `all\|ocr\|audio\|input\|accessibility\|memory\|parsed` | `all` | `ocr` is a legacy label meaning *all* screen text |
| `limit` | int | 10 (description says "max 20") | **advisory only — no server clamp** |
| `offset` | int | 0 | |
| `start_time` / `end_time` | string | — / now | ISO 8601, `Nh ago` / `Nd ago` / `Nw ago`, `now`, `yesterday`, `today`, bare `YYYY-MM-DD` |
| `app_name` | string | — | **case-sensitive** exact match (documented footgun) |
| `window_name` | string | — | substring |
| `frame_id` / `actor_id` | int | — | only valid with `content_type=parsed` (else 400) |
| `min_length` / `max_length` | int | — | filter by content character length |
| `include_frames` | bool | `false` | base64 screenshots, OCR results only. *"Warning: large response"* |
| `speaker_ids` / `speaker_name` | string | — | comma-separated ints / case-insensitive partial |
| `tags` | string | — | comma-separated, **AND** semantics; namespaced (`person:ada,project:atlas`) |
| `include_related` | bool | `false` | co-occurring-tags block |
| `max_content_length` | int | **1000** (client-side when absent) | middle-truncation; `0` = opt out |

`normalizeTime()` rewrites model-favoured formats (`yesterday` → `1d ago`, bare dates → start-of-day UTC) client-side because the server rejects them (root cause of issue #3124).

**Return shape** — one `text` content block, then optional image blocks:

```
Results: 10/247 (use offset=10 for more)

[Screen·a11y] Google Chrome | GitHub — screenpipe
2026-08-07T09:03:44+02:00
<text, middle-truncated to 1000 chars>
Tags: project:atlas
---
[Audio] MacBook Pro Microphone
...
Related tags: people: ada, noah | projects: atlas
```

Per-type prefixes: `[Screen·a11y]` / `[Screen·ocr]` / `[Screen]` (from `content.text_source`), `[Audio]`, `[Accessibility]`, `[Memory #id]`, `[Parsed]`.

Truncation markers — client: `…[N chars truncated — pass max_content_length=0 for full text]…`; server: `...(truncated N chars)...`. Comment on the default records why: *"one logged call returned 131k chars from a limit:10 search."*

**Image delivery:** `{ type: "image", data: <base64>, mimeType: "image/png" }` preceded by a `📷 <app> at <timestamp>` text block. **The mimeType is wrong** — `extract_frame` shells to ffmpeg emitting MJPEG (raw base64 JPEG labelled PNG).

### 1.2 `activity-summary` — the "what was recently on screen" tool

A **pre-aggregated rollup**, not a row dump. MCP params: `start_time`*, `end_time`*, `app_name`. Renders apps (minutes + frame counts + first/last seen), windows/tabs by time, audio segments by speaker, sampled transcriptions and key screen texts — sliced to fixed caps (20 windows, 15 transcriptions, 20 key texts).

### 1.3 Remaining tools (abridged)

`keyword-search` (positional-match search, `fuzzy_match`), `search-elements` (+`role`, `source`, `purpose`), `get-frame-elements`, `frame-context`, `export-video`, `list/get/update/start/stop-meeting`, `update-memory`, `add-tags`, speaker tools (`search-speakers`, `list-unnamed-speakers`, `update-speaker`, `merge-speakers`), `get-feedback`, `send-notification`, `health-check`, `list-audio-devices`, `list-monitors`, `control-recording`, pipes tools, `team-*`.

### 1.4 HTTP transport — deliberately smaller

Streamable HTTP `/mcp`, flags `--http --port 3031 --listen-on-lan` (requires `--api-key`). Exposes **one tool, `search_content`** (snake_case), reduced schema. `"Call with no parameters to get recent activity."`

### 1.5 Resources — only 3, deliberately

- `screenpipe://context` — JSON with `current_time`, timezone, and **pre-computed** timestamps (`one_hour_ago`, `today_start`, `one_week_ago`, …) so the model never does date math.
- `screenpipe://guide` — the progressive-disclosure spine:

| Step | Tool | When |
|---|---|---|
| 1 | `activity-summary` | "what was I doing?", "which apps?" |
| 2 | `search-content` | need specific text |
| 3 | `search-elements` | need UI structure |
| 4 | `frame-context` | full detail for one moment (id from step 2) |

Plus: always pass `start_time`; start with `limit=5`; use `max_content_length=500`; don't use `q` for audio; `app_name` is case-sensitive; deep-link formats; *"Never fabricate IDs or timestamps — only use values from actual results."*
- `screenpipe://guide/pipes`.

**No read tool was ever migrated to Resources**; the proposal (#4863) was closed as stale.

---

## 2. Underlying HTTP API

~130 routes (`server.rs:881-1010`), OpenAPI at `/openapi.json`. Bearer auth on everything except `/health`. CORS localhost-only.

### 2.1 `GET /search`

Beyond MCP params: `order` (`descending` default), `input_context_only`, `frame_name`, `focused`, `on_screen`, `browser_url`, `include_cloud`, `device_name`, `machine_id`, `filter_pii`, `format` (`json|csv|tsv|table`), `fields` (dotted paths). Response `{data: [{type, content}...], pagination: {limit, offset, total}, related: {...}}` with per-type content shapes (OCR, Audio, UI, Input, Memory, Parsed).

### 2.2 How the modalities merge

**No `UNION ALL`.** `ContentType::All` runs up to **three queries in parallel** (`tokio::try_join!`) — OCR, audio, accessibility — concatenates, sorts in Rust by timestamp, slices. Lightweight projection (`'' as text_json`) so SQLite never reads the multi-MB blob.

**`all` is not all.** Five variants exist but `All` merges only OCR + Audio + UI; `Input` and `Memory` require an explicit `content_type`. Second narrowing: with `app_name`/`window_name` set, the audio leg is dropped entirely.

**Pagination — per-leg over-fetch, then global slice:** `fetch_limit = limit + offset` per leg, offset 0, then `skip(offset).take(limit)` on the merged set. Correct for early pages; deep offsets can truncate a leg early.

**Dedup:** OCR and Accessibility both read `frames_fts`, so `All` can emit the same frame twice; `deduplicate_ocr_and_ui` collapses on `(timestamp rounded to 1s, app_name)`, drops the UI row, **upgrades the OCR text if the UI text is longer**.

**One MATCH:** `app_name`/`window_name`/`browser_url` fold *into* the FTS expression as column-scoped terms, joined with the sanitized query into a single `frames_fts MATCH ?1`.

### 2.3 Ranking — recency, with BM25 as a truncation heuristic

Every final `ORDER BY` is `frames.timestamp {DESC|ASC}, frames.id`. `bm25(` appears exactly once in the crate (unrelated feature). `ORDER BY rank` appears three times — in every case ordering a candidate subquery cut by `LIMIT 5000`, never the returned rows. **Relevance decides which 5000 candidates survive; time decides the page.** No `snippet()`/`highlight()` anywhere.

### 2.4 FTS schema

**Every FTS5 table uses `tokenize='unicode61'`** — no stemming, no substring; recall tricks are query-side. All six live FTS tables are **external content** (`content=`, `content_rowid=`); only one standalone remains.

Two design moves:
- *Consolidation* (migration comment): *"Before: 6 FTS tables, 3 places for accessibility text, 2 for OCR text. After: `frames.full_text` is the single searchable text per frame."*
- *External content* receipt: deleting 951 frames took **~38s standalone vs ~0.09s external (~420×)**, plus ~175MB saved on a 14-day DB. Deletes use FTS5 `'delete'` with OLD values; triggers guard on non-empty text.

No FTS5 `'optimize'`/`'merge'` is ever called; only `PRAGMA optimize` on the 60s WAL-checkpoint tick.

### 2.5 `GET /activity-summary`

Eight token-control knobs: `include_apps/windows/key_texts/recording/memories/snippets/guidance` (all default true), `max_snippets` 8 (cap 12), `max_snippet_chars` 500 (clamped 160–1200), `max_memories` 5 (cap 20). Response includes `total_active_minutes` (idle gaps >300s excluded — "endpoint owns time math, never the prompt: the model drifts"), **`data_status`** (`ok|empty_but_recording|no_capture_in_range|not_recording`) — the endpoint returns its own failure diagnosis — and **`guidance.next_best_query`** — a next-query hint so empty results don't send the model guessing.

### 2.6 `GET /search/keyword`

Returns a **bare array** of `SearchMatch { frame_id, timestamp, text_positions[], app_name, window_name, confidence, text, url, text_source }` with normalized 0–1 bounding boxes. **Text positions computed in Rust**, not FTS: lowercase `contains`, proportional bbox narrowing (bailing on multi-line), fallback to a11y per-line char spans. **`fuzzy_match` is not typo tolerance** — it swaps the query builder to camelCase/digit-boundary splitting + `*` prefix OR-join (compensation for no stemmer; catches OCR-concatenated words, not misspellings). **Grouping** is the fast path: skips text blobs (~10× faster), `ROW_NUMBER() OVER (PARTITION BY app_name ...) <= 30` diversity cap (*"without this, a single dominant app can fill the entire result set"*), then clusters matches within 120s same-app/same-window into one group.

### 2.7 `GET /elements` — the token-efficiency showcase

`format` accepts `json|csv|tsv|table|outline|tree|automation|computer-use|preferred`. `outline` = indented text, runs of identical siblings collapsed to `×N`, caps 200 lines / 120 chars, footer `… showing 200 of 1043 — narrow with ?q=, ?role=, ?limit=`. Measured in-source (tiktoken o200k_base): **91% aggregate token saving vs JSON** (85–99% by tree shape; flat OCR text is the floor at 67%). `format=automation` emits response-local `ref=eN` handles, `cache-control: no-store`, "database ids are historical evidence, not a safe live UI handle."

### 2.8 Generic `format` / `fields`

Measured on 25 elements: compact JSON 2410 tok, **YAML 3008 (+25%)**, **columnar TSV with ids dropped 644 (−73%)**. Verbatim conclusion: *"The win is not the syntax (YAML is worse than compact JSON, because it still repeats every key per row); it is writing the keys once."*

### 2.9 Frames

`GET /frames/:id` (raw bytes, 30-min cache), `/thumbnail?width=384&quality=75` (clamped 64–1920 / 20–95, 64MiB 5-min cache, per-key single-flight), `/context` (a11y tree + URLs; MCP caps 50 nodes / 2000 chars), `/metadata`, `/next-valid?direction=forward&limit=50` (skip corrupt frames in one call), `/text` + POST (OCR on demand).

Frame→video model: `frames.video_chunk_id` → `video_chunks.file_path`, `offset_index` = 0-based decode frame index within the chunk mp4; `COALESCE(snapshot_path, file_path)`.

---

## 3. Design decisions worth copying

a. **One search tool with a `content_type` filter**, not one tool per modality. Held since Dec 2024.
b. **Two truncation layers with a documented `0` opt-out** (they once shipped a bug where `0` returned only the truncation marker).
c. **Middle-truncation, not head-truncation** — both ends of transcript-shaped text carry signal.
d. **Pagination affordance printed inside the result text**: `Results: 10/247 (use offset=10 for more)`; `… 45 more segments — call again with transcript_offset=155.` The model reads its next call out of the payload.
e. **Every list tool caps its own worst case, usually two ways** (segments AND chars, whichever first).
f. **Bound the expensive path independently of `limit`**: `MAX_INLINE_FRAMES_PER_SEARCH=20`, extraction concurrency 4, global semaphore 3 — because `limit=500&include_frames=true` used to spawn 500 ffmpeg processes. Frame failures collected, not fail-fast.
g. **Tool descriptions carry USE WHEN / DO NOT USE and a starting limit.** Repo principle: "always use progressive disclosure when designing agentic systems."
h. **Client-side time normalization** — repair model-favoured formats rather than 400.
i. **Errors typed, actionable, `isError: true`** "so the model retries with a different approach instead of treating the error text as data"; HTTP codes mapped to specific fix hints.
j. **Permission model**: presets reader/writer/admin; `Type(specifier)` rules across `Api()`, `App()`, `Window()`, `Content()` + `time`/`days`; deny → allow → default → reject. **Two-layer enforcement**: endpoint middleware + **row-level post-query filter** using each item's own timestamp. When restrictions are active, `pagination.total` is downgraded to page size ("the unrestricted DB count can reveal denied rows"). `privacy_filter` forced server-side from the credential, never from a request field; fails closed (503).
k. **Resilience as API contract**: 30s timeout → 408 "try a narrower time range"; admission full → 503 + `Retry-After: 1`; retryable vs non-retryable DB errors distinguished.
l. **Real statement cancellation**: SQLite progress handler every 1,000 VM ops with a 30s budget — a dropped future interrupts the running `sqlite3_step`. The real fix for outage #4474.
m. **Two admission layers**: at most 2 concurrent uncached `/search` (try_acquire → immediate 503, not a queue) + `heavy_read_semaphore(2)` inside the OCR leg.
n. **Byte-weighted response cache**: Moka, 64MiB by weight, 30s TTL, entries rejected >200 items or >2MiB, body stored pre-serialized; hits bypass admission.
o. **The MATERIALIZED candidate-CTE pattern** (verbatim rationale): *"Select and limit frame ids before joining tag tables. The previous query grouped the entire matching history and only then applied LIMIT... This CTE is bounded by the timestamp index and caps all downstream join/group work to one requested page."* Plus a fast path skipping FTS for unfiltered browsing.
p. **Tags as a namespaced cross-store join key** (`person:`, `project:`) + `include_related` co-occurrence block (bounded 30 tags / 5s, degrades to omission).
q. **They instrument whether tool calls produce value** — telemetry classifying each call's outcome + client identity; measuring usefulness, not call volume.

---

## 4. Known pain points and course corrections

| ref | complaint | outcome |
|---|---|---|
| #2285 | Near-duplicate OCR results wasting LLM context (81 rows / 15-min window, top 5 byte-identical) | **Origin of `/activity-summary`** + `deduplicate_ocr_and_ui()` |
| #4474 | Search wedged the app: 30s timeout but query ran 153s holding its pooled connection; concurrency drained pool → app-wide 500s for minutes. Also SELECT pulled multi-MB blobs for `LIMIT 3` | Produced the MATERIALIZED CTE, lightweight projection, both semaphores, progress-handler cancellation, 408/503 contract |
| #4294 | activity-summary's heavy fields had no toggle | `include_*` knobs. Quote: *"the LLM doesn't need a bespoke 'workflow' endpoint — it just queries concise primitives across multiple time ranges and stitches the picture itself"* |
| #2436 | AX tree captures off-screen text (scrollback) polluting hits | `on_screen` filter + inline flag |
| #3124 | Advertised relative dates silently returned nothing | Fixed client-side (`normalizeTime`) |
| in-code | keyword-search param mismatch: **25/25 calls failed** before a translation layer | Fixed in MCP |
| #5074 | Claude Desktop attach failure: API-key discovery ran synchronously at module load, blowing the host's startup timeout | Transport connects first; discovery lazy + memoized + background-warmed |
| #4863 | Proposal to migrate reads to MCP Resources | Closed stale; reads remain Tools |
| npm 0.2→0.6 | Shipped desktop-automation tools | Removed; returned only as read projection |
| live bug | **offset_index unit mismatch** (ms vs fps): inline search frames effectively always decode frame 0 of the chunk | Live |
| live | **Unbounded count query** — page query capped at 5000 candidates, count has no LIMIT; `Input` counts with `LIKE '%q%'` full scan | Live |
| live | **Filter logic written twice** — count re-implements search filters in a second SQL string ("must agree or pagination breaks") | Live; main correctness liability |
| live | Keyword search bypasses cancellation (raw pool, no 30s deadline) | Live |
| live | SQL string interpolation in relevance CASE (only `'`-doubling) | Live |
| live | `content_type=all` silently excludes Input and Memory | Live |
| live | `mimeType: "image/png"` on JPEG bytes | Live |
| live | `limit` "max 20" is prompt-only, no server clamp | Live |

### On embeddings — the precise claim

**No embedding contributes to any text retrieval result.** But sqlite-vec 0.1.3 is a live dependency used in ~10 production queries — scoped entirely to **speaker identity** (diarization centroids, brute-force cosine). A text-embeddings table (`ocr_text_embeddings`) was built and deliberately dropped: *"created for an embedding pipeline that was never implemented."* All user-facing text search is FTS5 + recency.

---

## 5. Translation notes → self-hosted YouTube-corpus MCP

Structural difference: screenpipe has **one global timeline**; a YouTube corpus has **N independent time axes** plus a corpus-level dimension (channel, upload date, topic). Most of the design survives if you split "which video" from "where in the video."

| screenpipe element | maps to | note |
|---|---|---|
| one `search-content` w/ `content_type` | **keep** — one `search`, `content_type: all\|transcript\|ocr\|frame` | Most copyable decision |
| `text_source` provenance field + `[Screen·a11y]` prefixes | keep so the model tells provenance without a second filter | |
| "don't use `q` for audio" warning | transfers directly to auto-captions | |
| `all` silently dropping types | **don't copy** — if `all` doesn't mean all, say so in the enum | |
| `start_time`/`end_time` | **splits in two**: `published_after/before` (corpus axis) AND `offset_start/end` seconds (intra-video) | Don't overload one pair |
| `app_name` case-sensitive | `channel`/`video_title` — **case-insensitive** | Their footgun, repeated in every description |
| `frame_id` universal drill-down handle | `(video_id, t_seconds)` or synthetic `segment_id` | Search returns it, other tools consume it, guide forbids fabricating |
| `offset_index` + file_path | seconds + video_id → `https://youtu.be/ID?t=123` free deep links; avoids their ms-vs-fps bug entirely | |
| `activity-summary` | **`video-summary`** (chapters, per-chapter seconds, speakers, sampled key text) and/or **`corpus-summary`** | Most valuable port. Include `data_status` + `guidance.next_best_query` in spirit |
| `include_*` toggles + `max_*` caps | same knobs | Heaviest field needs an off switch |
| recency-only ordering | **do not copy** — fatal for a corpus. bm25/embeddings primary; `order=relevance\|recency\|video_time` explicit | |
| `ORDER BY rank LIMIT 5000` candidate cap | **copy verbatim** — the seam where frame embeddings slot in as reranker | |
| `max_per_app=30` diversity via ROW_NUMBER | **per-video diversity cap** — critical; one 3-hour lecture would bury 50 videos | |
| `cluster_search_matches(120s)` | merge adjacent transcript cues into `{video_id, start, end, text, cue_ids[]}` | #1 defence against #2285's failure mode — worse for transcripts (10 cues ≈ one sentence) |
| `deduplicate_ocr_and_ui` | dedup OCR frame text vs transcript in same window (slide says what speaker says); keep the longer text | |
| FTS5 external content | **copy** (420× delete receipt); guard triggers on non-empty text | |
| `unicode61` + query-side expansion | consider `porter`/snowball for prose transcripts; keep prefix-`*`, drop compound-splitting (screen-text problem) | |
| exact `total` via duplicated count query | **don't copy** — `LIMIT n+1` → `has_more` | |
| text_positions in Rust | analogue: char spans into a cue or `[start,end]` seconds; compute yourself | |
| `include_frames` base64 inline | **don't inline by default** — return `thumbnail_url`/`frame_ref`; separate `get-frame` call. If inlining: JPEG ~384px, correct mimeType | |
| `frame-context` | `get-segment-context`: transcript window around t, OCR of nearby frames, chapter title, description links; keep 50-node/2000-char caps | |
| `format=outline` (91% cut) | chapter/segment outline per video: indented, deduped, ×N collapsing, ~200-line cap + "narrow with ?q=" footer | |
| `format=csv\|tsv` + `fields=` | **copy verbatim** for list endpoints (−73% on columnar shapes: "writing the keys once") | |
| `max_content_length` middle-truncation, `0`=off | copy verbatim incl. the marker text | |
| `Results: N/total (use offset=N)` | copy verbatim | |
| `screenpipe://context` precomputed timestamps | copy — stops model date arithmetic | |
| progressive-disclosure guide resource | copy the 4-step shape: summary → search → structural → drill-down-by-id; USE WHEN / DO NOT USE per tool | |
| namespaced tags + `include_related` | topics/entities across videos (`topic:transformers`, `person:karpathy`); co-occurrence for exploration; bounded + degrade-to-omit | |
| speaker workflow (list-unnamed → update → merge) | diarized speakers + cross-video identity merge, if we diarize | |
| permissions | mostly N/A; transferable: row-level post-query filter if gating by channel/playlist/licence; restricted queries must not leak `total`; **server-authoritative policy from the credential, never a model-settable field** | |
| `export-video` slow-tool contract | `get-clip` (ffmpeg cut) or timestamped URL | |
| 408 "narrow your range" / 503 + Retry-After / progress-handler cancellation | **copy all three together** — timeout without cancellation caused their outage | |
| 30s byte-weighted cache | copy byte-weighting, lengthen TTL (corpus is static; hours or until reindex) | |
| capture-side tools (~15 of 28) | **drop** — our surface should land around **8–10 tools** | |

**Two things not to inherit:** recency-only ranking (fatal for a corpus) and inlined base64 images by default.

**Two things to copy nearly verbatim:** the progressive-disclosure resource (summary → search → drill-down-by-id, USE WHEN / DO NOT USE in every description), and layered truncation with a documented `0` opt-out plus next-offset hints printed in the payload.
