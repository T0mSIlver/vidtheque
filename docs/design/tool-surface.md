# vidtheque MCP tool surface — v1

Status: **design contract, not yet implemented.** This is the surface the `mcp/`
server implements; the HTTP API underneath it is an implementation detail and may
carry extra knobs, but anything reachable from a model is specified here.

Sources for the decisions below: `research/HANDOFF-2026-08-08.md`,
`research/screenpipe-tool-surface-deep-dive.md` (28-tool surface at 20k stars, with
receipts for every token-discipline rule), `research/landscape-survey-video-mcp.md`
(what the competition ships and where they break). Where a rule exists because
screenpipe learned it the hard way, the issue number is cited inline — those are
not preferences, they are paid-for lessons.

Example payloads are illustrative. Video IDs in them are real videos; the numbers
in them are made up.

---

## 1. Design principles

1. **One search tool, many content types.** Not one tool per modality. screenpipe
   has held this since Dec 2024 and never regretted it. Modality is a parameter,
   never a tool name.
2. **`all` means all.** screenpipe's `content_type=all` silently drops two of its
   five types (live bug). Ours queries every leg, every time. When a *filter* makes
   a leg meaningless, the leg is skipped **and the payload says so in a `note:`
   line**. Never a silent narrowing.
3. **Relevance first.** screenpipe orders everything by recency and uses BM25 only
   to pick which 5,000 candidates survive. That is right for a screen timeline and
   fatal for a corpus — the best answer about attention is a 2023 video. `order`
   is explicit, defaults to `relevance`.
4. **Two time axes, never overloaded.** `published_*` selects *which videos*;
   `offset_*` selects *where inside a video*. One `start_time`/`end_time` pair
   cannot mean both.
5. **The payload teaches the next call.** Pagination hints, truncation opt-outs,
   and `next:` lines are printed inside the text the model reads. The model should
   never have to guess a parameter name it wasn't shown.
6. **Every list tool is double-capped** — items *and* characters, whichever binds
   first — and every expensive path (image encoding, OCR joins, related-tag
   co-occurrence) is bounded **independently of `limit`**. screenpipe shipped
   `limit=500&include_frames=true` spawning 500 ffmpeg processes; we don't.
7. **Never an exact total.** `has_more` comes from `LIMIT n+1` and a bounded count
   probe over the *same* CTE. screenpipe's duplicated count query is its stated
   main correctness liability ("filter logic written twice — must agree or
   pagination breaks"). We write the filter once.
8. **Progressive disclosure.** Summary → search → drill-down-by-id. Every
   description carries USE WHEN / DO NOT USE and a starting `limit`.
9. **Ids are evidence, not guesses.** Every drill-down handle in this document is
   returned by some other call. The guide resource says, verbatim, never to
   fabricate one.

---

## 2. The surface at a glance

Nine tools. Kebab-case, following screenpipe (their original Python server shipped
`search-content` in 2024-12 and the name/params are unchanged 20 months later).

| # | Tool | One-line purpose | readOnly | idempotent |
|---|---|---|---|---|
| 1 | `search` | Cross-video search over transcripts, on-screen text and frame imagery, with timestamped deep links. | ✅ | ✅ |
| 2 | `list-videos` | Browse/filter the library without a query — what is in the corpus. | ✅ | ✅ |
| 3 | `corpus-summary` | Pre-aggregated rollup of the whole library: channels, tags, coverage, gaps. | ✅ | ✅ |
| 4 | `video-summary` | Pre-aggregated rollup of one video: chapters, key texts, speakers, tags. | ✅ | ✅ |
| 5 | `get-segment-context` | Full detail around one moment: transcript window, nearby OCR, chapter, frame refs. | ✅ | ✅ |
| 6 | `get-frames` | Fetch keyframe images as authenticated URLs (default) or inline base64. | ✅ | ✅ |
| 7 | `index-video` | Add a video/playlist to the corpus. Async — returns a job id. | ❌ | ✅ |
| 8 | `job-status` | Poll an indexing job. | ✅ | ❌ |
| 9 | `tag-video` | Add/remove namespaced tags on an indexed video. | ❌ | ✅ |

Three resources: `vidtheque://corpus`, `vidtheque://context`, `vidtheque://guide`.

Deliberately **not** in v1: subscriptions, `get-clip`, markdown export, speaker
identity management, per-channel permissions. See §6 for the sketches and why.

---

## 3. Shared conventions

Everything in this section is implemented once, in one place, and referenced by
every tool. Where a tool's parameter table says "see §3.x", the semantics are
identical — no per-tool drift.

### 3.1 Identifiers

| Handle | Shape | Example | Where it comes from |
|---|---|---|---|
| `video_id` | YouTube 11-char id; for future non-YouTube sources, `<source>:<id>` | `kCc8FmEb1nY` | every result line, `list-videos` |
| `cue_id` | integer, stable per transcript cue | `1841` | `search` (`cues 1841-1849`), `get-segment-context` |
| `frame_id` | `<video_id>-<NNNNN>`, keyframe ordinal within the video, URL-safe by construction | `kCc8FmEb1nY-00412` | `search` frame hits, `get-segment-context`, `video-summary` |
| `job_id` | `job_` + 12 hex | `job_7f3a29b1c04d` | `index-video` |

There is deliberately **no query-dependent `segment_id`**. A search segment is a
cluster computed for *that* query (§3.10); handing the model an id that only
exists for one result set invites fabrication and stale drill-downs. Segments are
addressed the durable way: `(video_id, t)`.

`frame_id` embeds the `video_id` so a fabricated one is almost always detectably
wrong (unknown ordinal for a known video → typed `E_UNKNOWN_FRAME` naming the
valid range), and so `/frames/<frame_id>.jpg` is a flat, cache-friendly route.

### 3.2 Time: two axes

**Corpus axis** — `published_after` / `published_before`. Selects videos by upload
date. Accepts, and normalizes server-side:

- ISO 8601 (`2026-03-01`, `2026-03-01T12:00:00Z`)
- relative (`7d ago`, `3w ago`, `6mo ago`, `2y ago`)
- keywords (`now`, `today`, `yesterday`)

Bare dates resolve to start-of-day **UTC**. screenpipe advertised these formats
while its server rejected them, returning silent empty results for months
(issue #3124); they patched it in the MCP client. We normalize in the HTTP layer
so there is exactly one implementation and the raw API behaves the same.

**Intra-video axis** — `offset_start` / `offset_end`. Seconds from the start of a
video. Accepts a number (`723`) or a clock string (`12:03`, `1:12:03`), normalized
to seconds. Only meaningful when the result set is scoped to few videos; harmless
otherwise (it filters *within* each video).

Also accepted anywhere a "when did we ingest this" filter makes sense:
`indexed_after` / `indexed_before`, same normalizer.

Unparseable input is a hard, typed error (`E_BAD_TIME_FORMAT`) that echoes the
accepted formats — never a silently ignored filter.

### 3.3 Text truncation

Every field that can carry unbounded text is middle-truncated. Head truncation
loses the payoff of a transcript sentence; both ends carry signal.

- Parameter: `max_text_chars`, default **1000**, `0` = **opt out entirely**.
- Clamp: `0`, or `120..20000`. Values in `1..119` are clamped up to 120 (a
  truncation window smaller than the marker is useless).
- Marker: `…[N chars truncated — pass max_text_chars=0 for full text]…`
- The `0` opt-out is **tested**. screenpipe once shipped a build where `0`
  returned *only* the truncation marker.

Truncation is the second cap, never the only one. Each tool also caps item counts
(§ per-tool "token discipline" blocks), and the **response as a whole** is capped
at `RESPONSE_MAX_CHARS` (default 60,000, env-tunable). If a response would exceed
it, items are dropped from the tail and the payload ends with:

```
Response truncated at 60000 chars — 4 results dropped. Re-run with a smaller limit or max_text_chars.
```

### 3.4 Pagination and `has_more`

No tool ever runs a second count query. The rule:

1. The ranked candidate CTE is cut at `CANDIDATE_CAP` (default **5000**,
   `ORDER BY rank`). This is the seam where the embedding reranker slots in.
2. The page is fetched with `LIMIT limit + 1` → `has_more`.
3. A **count probe** counts rows in the *same* CTE up to
   `ceiling = offset + limit + 30`. Cheap (bounded), and it cannot disagree with
   the page query because it is the same filter expression.

Rendering:

- probe finished below the ceiling → exact: `Results: 10/38 (use offset=10 for more)`
- probe hit the ceiling → approximate: `Results: 10/~40+ (use offset=10 for more)`
- last page: `Results: 8/8 (no more results)`

`~40+` reads as "at least 40, we stopped counting" — which is the truth, unlike an
unbounded `COUNT(*)` that screenpipe still runs (live bug: page capped at 5000
candidates, count uncapped, `Input` type counts with `LIKE '%q%'` full scan).

### 3.5 Output formats

List-shaped tools (`search`, `list-videos`) accept:

| param | values | default | note |
|---|---|---|---|
| `format` | `text` \| `tsv` | `text` | `text` = the model-readable block form shown per tool |
| `fields` | comma-separated column names | per-tool default set | `tsv` only |

`tsv` writes the keys once. screenpipe measured 25 elements at 2,410 tokens as
compact JSON, 3,008 as YAML (+25% — YAML is *worse*), and **644 as columnar TSV
with ids dropped (−73%)**. Their conclusion, verbatim: *"The win is not the syntax
… it is writing the keys once."* We never offer JSON or YAML as a model-facing
format; structured data goes in `structuredContent`, which conformant clients read
without spending prose tokens.

### 3.6 Deep links

**Every timestamped item in every payload carries `https://youtu.be/<id>?t=<int>`.**
Seconds are integers (YouTube ignores fractions); `t` is the clamped floor of the
item's start minus `DEEPLINK_LEAD` (default 2s, so the sentence isn't already
half-spoken when playback starts). This is free precision we get from whisperX
word-level alignment, and it sidesteps screenpipe's live `offset_index` unit bug
(ms vs fps) entirely — we store seconds, not decode-frame indices.

Non-YouTube sources (later) fall back to `link: null` plus `frame_id`/`cue_id`;
the field is always present so the model's rendering doesn't branch.

### 3.7 Tags

Namespaced, lowercase, `<namespace>:<value>`. Reserved namespaces: `topic:`,
`person:`, `project:`, `source:`, `lang:`, `series:`. Anything else is rejected by
`tag-video` with the list of valid namespaces (open namespaces produce
`topic:x` / `Topic:X` / `topics:x` triplicates within a week).

Validation regex: `^[a-z0-9]+:[a-z0-9][a-z0-9._-]{0,63}$`.

`tags` filters use **AND** semantics (comma-separated). `include_related=true` adds
a co-occurrence block computed from tags appearing on the same videos as the
result set — bounded at **30 tags / 800ms**, and it **degrades to omission**, never
to an error or a slow response.

### 3.8 Error contract

Errors are typed, actionable, and returned with `isError: true` so the model
retries differently instead of treating the error prose as data — screenpipe's
stated rationale, and the reason their 25/25 failing `keyword-search` calls were
noticed at all.

Shape: one text block, plus `structuredContent`:

```
error: E_UNKNOWN_VIDEO
Video "kCc8FmEb1nY" is not in the corpus.
next: index-video url="https://youtu.be/kCc8FmEb1nY" to add it (takes ~2-6 min), or list-videos to browse what is indexed.
```

```json
{"code":"E_UNKNOWN_VIDEO","message":"Video \"kCc8FmEb1nY\" is not in the corpus.",
 "next":"index-video url=…","retry_after_s":null}
```

| Code | HTTP | When | `next:` hint |
|---|---|---|---|
| `E_BAD_TIME_FORMAT` | 400 | unparseable time value | echoes accepted formats with an example |
| `E_BAD_PARAM` | 400 | wrong type / out-of-domain enum | names the parameter and its domain |
| `E_EMPTY_QUERY` | 400 | `search` with no `q` and no filters | "pass `q`, or use `list-videos` to browse" |
| `E_ORDER_SCOPE` | 400 | `order=video_time` without a single-video scope | "add `video_id=…`, or use `order=relevance`" |
| `E_UNKNOWN_VIDEO` | 404 | video not in corpus | `index-video` / `list-videos` |
| `E_UNKNOWN_FRAME` | 404 | bad `frame_id` | valid ordinal range for that video |
| `E_UNKNOWN_JOB` | 404 | bad `job_id` | "call `job-status` with no id for recent jobs" |
| `E_NOT_INDEXED` | 409 | video row exists, pipeline never ran | `index-video force_reindex=true` |
| `E_INDEXING` | 409 | video is mid-pipeline; partial data | `job-status job_id=…`, plus what *is* queryable now |
| `E_FEATURE_DISABLED` | 409 | filter needs a disabled feature (e.g. `speaker` with diarization off) | "omit `speaker=`" |
| `E_TIMEOUT` | 408 | 30s query budget exhausted | "narrow the range: add `channel=`, `video_id=`, or a tighter `published_after`" |
| `E_BUSY` | 503 | admission control full | `retry_after_s: 1` |
| `E_RATE_LIMIT` | 429 | per-client budget | `retry_after_s` |
| `E_TOO_LARGE` | 413 | request would exceed inline image budget | "use `return=url`, or lower `limit`" |
| `E_UNSUPPORTED_SOURCE` | 422 | `index-video` URL yt-dlp can't handle | lists supported sources |
| `E_INTERNAL` | 500 | anything else | "retry once; if it persists the server log has the trace id" (trace id included) |

408 + 503 + **real cancellation** ship together. screenpipe's outage (#4474) was a
30s timeout with *no* cancellation: the query kept running 153s holding its pooled
connection, concurrency drained the pool, and the whole app 500'd for minutes. The
fix is a SQLite progress handler firing every 1,000 VM ops against a 30s budget so
a dropped future actually interrupts `sqlite3_step`. Two admission layers: at most
2 concurrent uncached searches (`try_acquire` → immediate 503, **not** a queue),
and a heavier semaphore inside the frame/vector legs.

### 3.9 Annotations

Every tool carries MCP `annotations`: `title`, `readOnlyHint`, `idempotentHint`,
`openWorldHint`. `openWorldHint` is `false` for every query tool (the corpus is a
closed local index) and `true` for `index-video` (it fetches from the internet).

We use `idempotentHint` as a **cache-safety signal**: `true` when the same
arguments return the same answer until the corpus changes. That makes `job-status`
the one read tool with `idempotentHint: false`.

### 3.10 Search-side algorithms (shared by `search` and, in part, the summaries)

**Adjacent-cue clustering.** Transcript cues are ~1–3 seconds; ten of them are one
sentence. Emitting them as ten results is exactly the failure mode behind
screenpipe #2285 (81 near-duplicate rows in a 15-minute window, the top five
byte-identical). Matched cues in the same video within `cluster_gap` (default
**8s**) collapse into one segment; the segment text is the *contiguous* cue run
from the first to the last matched cue, so it reads as prose rather than keyword
confetti. Bounded by `cluster_max_seconds` (default 120) and by `max_text_chars`.
Result shape: `{video_id, start, end, text, cue_ids[]}`.

**Per-video diversity cap.** `ROW_NUMBER() OVER (PARTITION BY video_id ORDER BY
rank) <= max_per_video`, default **3**, applied *before* the page slice.
screenpipe's comment on the equivalent app-level cap: *"without this, a single
dominant app can fill the entire result set."* One 3-hour lecture would otherwise
bury fifty videos. When the cap actually bit, the payload footer says so and names
the parameter to widen.

**OCR-vs-transcript dedup.** A slide usually says what the speaker is saying. Two
hits, same second, same claim. Within one video, an OCR hit whose frame timestamp
falls inside (or within 5s of) a transcript segment's span, and whose normalized
text (casefold, strip punctuation/runs of whitespace) is contained in the other or
shares ≥0.8 trigram Jaccard, collapses into one result. **The longer text wins** —
screenpipe's `deduplicate_ocr_and_ui` upgrades OCR text when the accessibility
text is longer, same principle. Provenance becomes `[transcript+ocr]` so the model
still knows both channels fired without a second query.

**Cross-modal fusion.** BM25 scores and cosine similarities are not comparable, so
`content_type=all` fuses the three legs with **Reciprocal Rank Fusion**
(`score = Σ 1/(k + rank)`, k=60) rather than a hand-tuned weighted sum. RRF needs
no per-leg calibration and survives a leg returning nothing. The per-leg candidate
lists are each cut at `CANDIDATE_CAP` before fusion.

**Provenance prefixes** (so the model never needs a second, narrower query just to
learn where a hit came from): `[transcript]`, `[ocr]`, `[frame]`, `[transcript+ocr]`,
`[description]`.

---

## 4. Tools

### 4.1 `search`

**Purpose:** cross-video search over transcripts, on-screen text and frame imagery,
returning timestamped deep links.

**Description (ships verbatim):**

```
Search the indexed video corpus. Covers spoken content (transcripts), on-screen
text (OCR of keyframes), and frame imagery (visual similarity), with a timestamped
youtu.be deep link on every result.

USE WHEN: you need specific words, claims, numbers, code, or visuals from videos
the user has indexed — "where does he explain KV caching", "which video shows the
nvidia-smi output", "find the slide with the loss curve".

DO NOT USE: to find out what is in the corpus at all (use corpus-summary, or the
vidtheque://corpus resource); to understand one video end to end (use
video-summary); to read the full transcript around a moment you already found (use
get-segment-context with the video_id and t from a result here). Do not use this
to search the public YouTube catalogue — it only searches videos already indexed.

START WITH limit=5 and add filters before raising it. content_type=all means all
three channels, always. Ordering defaults to relevance, not recency — pass
order=recency explicitly if the user asked for "latest".

Two independent time axes, do not confuse them: published_after/published_before
select WHICH VIDEOS by upload date; offset_start/offset_end select WHERE INSIDE a
video, in seconds. channel and video_title matching is case-insensitive and
substring-based.
```

**Parameters:**

| name | type | default | server-side constraint | notes |
|---|---|---|---|---|
| `q` | string | — | ≤ 512 chars | FTS5 query for text legs; encoded by the text encoder for the frame leg. Required unless ≥1 filter is set. Bare `*`/empty with filters = browse mode (skips FTS entirely, fast path). |
| `content_type` | enum `all\|transcript\|ocr\|frame` | `all` | — | `all` queries **all three**. Never silently narrowed; leg skips are printed as `note:`. |
| `limit` | int | `10` | **clamped 1..50** | Not prompt-only. screenpipe's "max 20" is advisory with no clamp (live bug). |
| `offset` | int | `0` | clamped 0..10000 | |
| `order` | enum `relevance\|recency\|video_time` | `relevance` | — | `recency` = video publish date desc, ties by relevance. `video_time` requires a single-video scope → else `E_ORDER_SCOPE`. |
| `video_id` | string \| string[] | — | ≤ 20 ids | Scope to specific videos. |
| `channel` | string | — | ≤ 128 chars | **Case-insensitive substring.** (screenpipe's `app_name` is case-sensitive exact — a footgun they repeat in every description.) |
| `video_title` | string | — | ≤ 256 chars | Case-insensitive substring. |
| `tags` | string | — | ≤ 10 tags | Comma-separated, AND semantics, namespaced (§3.7). |
| `include_related` | bool | `false` | 30 tags / 800ms, degrades to omission | Co-occurring-tag block. |
| `published_after` / `published_before` | string | — | §3.2 normalizer | Corpus axis. |
| `offset_start` / `offset_end` | number \| string | — | §3.2; seconds ≥ 0 | Intra-video axis. |
| `speaker` | string | — | ≤ 128 chars | Case-insensitive partial. Transcript leg only → other legs skipped with a `note:`. `E_FEATURE_DISABLED` if diarization is off corpus-wide. |
| `min_chars` / `max_chars` | int | — | 0..100000 | Filter by matched-segment text length. Text legs only. |
| `max_per_video` | int | `3` | clamped 1..20 | Diversity cap (§3.10). |
| `cluster_gap` | number | `8` | clamped 0..60 | Seconds; `0` disables clustering (returns raw cues). |
| `max_text_chars` | int | `1000` | `0` or 120..20000 | §3.3. |
| `format` | enum `text\|tsv` | `text` | — | §3.5. |
| `fields` | string | `video_id,start,text,link,source` | ≤ 12 fields | `tsv` only. |

**Return shape.** One `text` block; `structuredContent` mirrors it as
`{results: [...], pagination: {limit, offset, has_more, approx_total}, notes: [...],
related_tags?: {...}}`. **No image blocks — ever.** Frame hits carry `frame_id`;
images come from `get-frames` (§4.6), which is the whole point of that tool.

```
Results: 10/~40+ (use offset=10 for more)
Query: "kv cache" · content_type=all · order=relevance · max_per_video=3
Legs: transcript 24 · ocr 9 · frame 7 (fused, RRF k=60; 5000-candidate cap not reached)

[transcript] Let's build GPT: from scratch — Andrej Karpathy (kCc8FmEb1nY)
  1:12:03–1:12:47 · https://youtu.be/kCc8FmEb1nY?t=4321
  so the reason we cache the keys and the values is that at every new token you
  would otherwise recompute attention over the entire prefix, which is quadratic.
  the cache makes it linear in the number of new tokens, and the price you pay is
  memory — …[612 chars truncated — pass max_text_chars=0 for full text]… which is
  why long-context inference is a memory-bandwidth problem, not a compute problem.
  cues 1841-1849 · score 0.0312

[transcript+ocr] Fast LLM inference from scratch — Kevin Chen (BpXHvyqZ0Fk)
  22:41–23:10 · https://youtu.be/BpXHvyqZ0Fk?t=1359
  KV cache size = 2 · n_layers · n_heads · d_head · seq_len · dtype_bytes
  (slide text and narration matched within 3s; kept the longer text: ocr)
  cues 604-609 · frame BpXHvyqZ0Fk-00188 · score 0.0298

[frame] Visualizing transformers — 3Blue1Brown (eMlx5fFNoYc)
  09:14 · https://youtu.be/eMlx5fFNoYc?t=552
  visual match, no text hit · frame eMlx5fFNoYc-00073
  → get-frames frame_ids=["eMlx5fFNoYc-00073"] to look at it
  score 0.0241

[ocr] Making LLMs go brrr — GPU MODE (zduSFxRajkE)
  41:02 · https://youtu.be/zduSFxRajkE?t=2460
  paged kv cache · block table · 4% fragmentation vs 60-80% baseline
  frame zduSFxRajkE-00390 · score 0.0195

--- (6 more results elided in this example) ---

3 of 10 results came from kCc8FmEb1nY (max_per_video=3 bound). Raise max_per_video for more from it.
Text middle-truncated at 1000 chars — pass max_text_chars=0 for full text.
next: get-segment-context video_id="kCc8FmEb1nY" t=4321 for the full surrounding transcript.
```

A leg-skip note looks like this, on the line under `Legs:`:

```
note: speaker= applies to the transcript leg only — ocr and frame legs were not queried for this call.
```

Empty result set (no bare "no results"; screenpipe's `guidance.next_best_query`
principle applied to search):

```
Results: 0/0
Query: "flash attention 4" · content_type=all · published_after=2026-07-01

data_status: ok (corpus has 312 videos, newest published 2026-08-02, index fresh)
The corpus does contain 41 hits for "flash attention" with no date filter.
next: retry with published_after omitted, or search q="flash attention" limit=5.
```

**Token discipline.**
- `limit` clamped 1..50 server-side; `max_text_chars` default 1000 with a tested
  `0` opt-out; whole-response cap 60,000 chars (§3.3).
- Double cap: at most `limit` items **and** at most `limit × (max_text_chars + 220)`
  chars of body before the response cap trims the tail.
- Bounded independently of `limit`: the FTS/vector candidate window
  (`CANDIDATE_CAP=5000` per leg), the count probe (`offset+limit+30`), the
  related-tags co-occurrence (30 tags / 800ms), the dedup comparison window (5s),
  and the cluster span (120s). None of these grow when `limit` grows.
- No images, ever. That path is `get-frames`, where its cost is visible.

**Errors:** `E_EMPTY_QUERY`, `E_BAD_TIME_FORMAT`, `E_BAD_PARAM`, `E_ORDER_SCOPE`,
`E_UNKNOWN_VIDEO` (any id in `video_id[]` unknown — names which), `E_FEATURE_DISABLED`,
`E_TIMEOUT`, `E_BUSY`.

**Annotations:** `{title: "Search video corpus", readOnlyHint: true,
idempotentHint: true, openWorldHint: false}`.

---

### 4.2 `list-videos`

**Purpose:** browse and filter the library itself — titles, channels, durations,
coverage — without a search query.

Resources can't take parameters, so the `vidtheque://corpus` resource is the cheap
unfiltered view and this is the filtered one. Both exist on purpose.

**Description (ships verbatim):**

```
List videos in the corpus, with optional filters. This is the browsable library:
title, channel, publish date, duration, tags, and which channels of data each
video has (transcript / OCR / frame embeddings).

USE WHEN: the user asks what is indexed, wants everything from one channel or tag,
or you need a video_id before calling video-summary or a scoped search. Also use
it after an empty search to check whether the video is even in the corpus.

DO NOT USE: to find content inside videos (use search); to get a picture of the
whole corpus at once (use corpus-summary — it is one call instead of paging).

START WITH limit=20, format="tsv". Filters are case-insensitive substrings.
Ordering defaults to most-recently-published.
```

**Parameters:**

| name | type | default | constraint | notes |
|---|---|---|---|---|
| `q` | string | — | ≤ 256 chars | Matches title + channel + description only. Not transcripts. |
| `channel` | string | — | ≤ 128 | Case-insensitive substring. |
| `tags` | string | — | ≤ 10 | AND semantics. |
| `published_after` / `published_before` | string | — | §3.2 | |
| `indexed_after` / `indexed_before` | string | — | §3.2 | |
| `has` | enum `transcript\|ocr\|frames\|all\|any` | `any` | — | Coverage filter — finds the half-indexed ones. |
| `order` | enum `recency\|title\|duration\|indexed_at\|relevance` | `recency` | — | `relevance` requires `q`. |
| `limit` | int | `20` | clamped 1..100 | |
| `offset` | int | `0` | 0..10000 | |
| `format` | enum `text\|tsv` | `tsv` | — | TSV by default: this output is inherently columnar. |
| `fields` | string | `video_id,title,channel,published,duration,coverage` | ≤ 12 | adds: `tags`, `indexed_at`, `cues`, `frames`, `link` |
| `max_text_chars` | int | `120` | `0` or 40..2000 | Per-cell (titles), not per-response. |

**Return shape:**

```
Videos: 20/~50+ (use offset=20 for more)
Filter: channel~"karpathy" · order=recency

video_id	title	channel	published	duration	coverage
kCc8FmEb1nY	Let's build GPT: from scratch, in code, spelled out.	Andrej Karpathy	2023-01-17	1:56:20	t,o,f
zduSFxRajkE	Let's build the GPT Tokenizer	Andrej Karpathy	2024-02-20	2:13:35	t,o,f
l8pRSuU81PU	Let's reproduce GPT-2 (124M)	Andrej Karpathy	2024-06-09	4:01:26	t,o,-
…

coverage: t=transcript o=on-screen text f=frame embeddings -=missing
1 video is missing frame embeddings. next: index-video url="https://youtu.be/l8pRSuU81PU" force_reindex=true
next: video-summary video_id="kCc8FmEb1nY" for chapters and key texts.
```

**Token discipline.** `limit` clamped 1..100; TSV default (−73% vs JSON on
columnar shapes); titles truncated at 120 chars per cell; response cap applies.
Bounded independently of `limit`: the coverage rollup is read from denormalized
per-video counters, not computed by joining cue/frame tables per row.

**Errors:** `E_BAD_TIME_FORMAT`, `E_BAD_PARAM`, `E_ORDER_SCOPE` (`order=relevance`
without `q`), `E_TIMEOUT`, `E_BUSY`.

**Annotations:** `{title: "List indexed videos", readOnlyHint: true,
idempotentHint: true, openWorldHint: false}`.

---

### 4.3 `corpus-summary`

**Purpose:** one pre-aggregated call that answers "what is in this library, and is
it healthy?" — the entry point of the progressive-disclosure chain.

This is the port of screenpipe's `activity-summary`, which exists because of
issue #2285 (row dumps burning context). Their own conclusion from #4294 is the
design rule: *"the LLM doesn't need a bespoke 'workflow' endpoint — it just
queries concise primitives across multiple time ranges and stitches the picture
itself."* So: rollups, toggles, caps — not a workflow engine.

**Description (ships verbatim):**

```
Pre-aggregated overview of the whole video corpus: how many videos, which
channels, which topics/tags, date span, coverage gaps, and what was indexed most
recently. One call instead of paging list-videos.

USE WHEN: this is your FIRST call in a session, or the user asks what is in the
library, what topics are covered, or whether something is indexed yet. Also call
it after an empty search — it tells you whether the corpus is empty, still
indexing, or simply does not contain that topic.

DO NOT USE: to find content (use search); for detail on one video (use
video-summary).

Turn off what you do not need: include_channels, include_tags, include_recent,
include_gaps, include_guidance (all default true). Every section is capped; raise
max_channels / max_tags only when the user is explicitly enumerating.
```

**Parameters:**

| name | type | default | constraint | notes |
|---|---|---|---|---|
| `channel` | string | — | ≤ 128 | Scope the whole rollup to one channel. |
| `tags` | string | — | ≤ 10 | Scope to a collection. |
| `published_after` / `published_before` | string | — | §3.2 | |
| `include_channels` | bool | `true` | | |
| `include_tags` | bool | `true` | | |
| `include_recent` | bool | `true` | | Most recently indexed videos. |
| `include_gaps` | bool | `true` | | Coverage/failure diagnostics. |
| `include_guidance` | bool | `true` | | `next_best_query`. |
| `max_channels` | int | `10` | clamped 1..50 | |
| `max_tags` | int | `30` | clamped 1..100 | |
| `max_recent` | int | `8` | clamped 1..25 | |
| `max_text_chars` | int | `120` | `0` or 40..2000 | Per title/label. |

**Return shape:**

```
Corpus: 312 videos · 486h 12m · 1,240,331 transcript cues · 58,904 keyframes
Published span: 2019-04-02 → 2026-08-02 · last indexed: 2026-08-07 19:41 (2 videos)
data_status: ok

Channels (top 10 of 34):
  Andrej Karpathy          11 videos   24h 06m
  3Blue1Brown              28 videos   12h 41m
  GPU MODE                 41 videos   61h 55m
  Yannic Kilcher           37 videos   38h 12m
  …

Tags (top 30 of 112):
  topic:transformers 58 · topic:cuda 41 · topic:inference 39 · person:karpathy 11
  topic:quantization 22 · series:gpu-mode 41 · topic:rag 18 · lang:en 305 …

Recently indexed:
  2026-08-07  Flash Attention 3 walkthrough — GPU MODE (Qk7mF2xLp0A)  1:04:11
  2026-08-07  Speculative decoding, explained — Trelis (9dRk2XcVbNw)  38:20
  …

Gaps:
  4 videos have transcript but no OCR (indexed before OCR was enabled)
  1 video failed: HcQ8pL3vN1s — "yt-dlp: video unavailable (private)" on 2026-08-05
  0 videos currently indexing

next_best_query: search q="<topic>" limit=5 — or list-videos channel="GPU MODE" to browse that channel.
```

`data_status` is the endpoint diagnosing itself, so an empty answer never sends the
model guessing:

| value | meaning |
|---|---|
| `ok` | corpus populated, no active jobs |
| `empty` | nothing indexed yet → `next:` is `index-video` |
| `indexing` | ≥1 job in flight; counts are a moving target → `next:` is `job-status` |
| `partial` | ≥1 video indexed with missing channels (transcript-only, no frames) |
| `degraded` | ≥1 job failed in the last 24h; results may be incomplete |

**Token discipline.** Hard caps on every section (10 channels / 30 tags / 8 recent
by default; 50/100/25 ceilings). Repeated sibling rows collapse with `×N` in the
gaps section. Bounded independently of any `limit`: the channel and tag rollups
read materialized counter tables refreshed at index time, never `GROUP BY` over
the cue table. Worst case ≈ 3,500 chars with everything on at ceiling values.

**Errors:** `E_BAD_TIME_FORMAT`, `E_BAD_PARAM`, `E_TIMEOUT`, `E_BUSY`.

**Annotations:** `{title: "Summarize the corpus", readOnlyHint: true,
idempotentHint: true, openWorldHint: false}`.

---

### 4.4 `video-summary`

**Purpose:** pre-aggregated rollup of one video — chapters with timestamps,
speakers, key on-screen texts, tags, links — instead of dumping a two-hour
transcript.

**Description (ships verbatim):**

```
Structured overview of one indexed video: chapters with timestamps and deep links,
speakers, the most informative on-screen texts, tags, and links from the
description. Use it instead of reading the whole transcript.

USE WHEN: the user asks what a video covers, wants its structure, or you need to
pick a timestamp to drill into. Good second call after search returns a video you
have not seen before.

DO NOT USE: to search across videos (use search); to read the actual words around
a moment (use get-segment-context — this tool samples, it does not transcribe).

Everything heavy has an off switch: include_chapters, include_speakers,
include_key_texts, include_ocr_highlights, include_links, include_tags,
include_guidance. Caps: max_chapters=20, max_key_texts=12, max_chars=300 per item.
Full transcripts are never returned by this tool at any setting.
```

**Parameters:**

| name | type | default | constraint | notes |
|---|---|---|---|---|
| `video_id` | string | — | **required** | From search / list-videos. |
| `offset_start` / `offset_end` | number \| string | — | §3.2 | Summarize only part of a long video. |
| `include_chapters` | bool | `true` | | YouTube chapters if present, else derived from scene+topic segmentation. |
| `include_speakers` | bool | `true` | | Omitted silently when diarization is off (this one *is* a silent omission — it's a presence question, not a filter). |
| `include_key_texts` | bool | `true` | | Sampled transcript lines with highest TF-IDF weight. |
| `include_ocr_highlights` | bool | `true` | | Distinct on-screen texts (slide titles, code, command lines). |
| `include_links` | bool | `false` | | URLs from the description. Off by default — link lists are pure noise until asked for. |
| `include_tags` | bool | `true` | | |
| `include_guidance` | bool | `true` | | |
| `max_chapters` | int | `20` | clamped 1..50 | |
| `max_key_texts` | int | `12` | clamped 1..30 | |
| `max_ocr_highlights` | int | `10` | clamped 1..30 | |
| `max_chars` | int | `300` | `0` or 80..1200 | Per item, middle-truncated. |
| `format` | enum `text\|outline` | `text` | — | `outline` = indented, `×N`-collapsed, ≤200 lines. |

**Return shape:**

```
Let's build GPT: from scratch, in code, spelled out.
Andrej Karpathy (kCc8FmEb1nY) · published 2023-01-17 · 1:56:20 · indexed 2026-06-02
https://youtu.be/kCc8FmEb1nY
data_status: ok (transcript ✓ · ocr ✓ 1,204 keyframes · frame embeddings ✓)
Tags: topic:transformers, topic:training, person:karpathy, lang:en

Chapters (14 of 14):
  00:00  intro: ChatGPT, Transformers, nanoGPT           ?t=0
  07:52  reading and exploring the data                  ?t=470
  09:23  tokenization, train/val split                   ?t=563
  …
  1:11:32 kv cache and inference-time cost               ?t=4290
  1:47:58 conclusions                                    ?t=6476

Speakers: Andrej Karpathy (99% of speech), unnamed_2 (0.4%, 00:03-00:11)

Key texts (12):
  09:41  "we're going to take the tiny shakespeare dataset and train a character-level model"  ?t=581
  33:12  "the crux of self-attention is that every token emits a query and a key"              ?t=1992
  …

On-screen text highlights (10):
  12:04  import torch; torch.manual_seed(1337)                       [code]      ?t=724
  35:50  wei = q @ k.transpose(-2,-1) * C**-0.5                      [code]      ?t=2150
  41:18  "attention is a communication mechanism"                    [slide]     ?t=2478
  ×3 near-identical terminal frames collapsed (48:02-48:40)
  …

next: get-segment-context video_id="kCc8FmEb1nY" t=4290 window=60 for the KV-cache passage.
```

`data_status`: `ok` | `transcript_only` | `no_ocr` | `no_frames` | `indexing`
(with the `job_id`) | `failed` (with the error and a `force_reindex` hint).

**Token discipline.** Caps above, all clamped server-side; `×N` collapsing for
runs of near-identical OCR (perceptual-hash buckets, computed at index time);
`include_links` off by default. Bounded independently of everything else: key-text
and OCR-highlight selection reads a precomputed per-video "salience" table built
during indexing, so this tool never scans the cue table — it is O(caps), not
O(video length). A 4-hour video and a 4-minute video cost the same tokens and
roughly the same milliseconds. Worst case ≈ 6,000 chars at ceiling settings.

**Errors:** `E_UNKNOWN_VIDEO`, `E_NOT_INDEXED`, `E_INDEXING` (returns whatever is
already queryable plus the job id), `E_BAD_PARAM`.

**Annotations:** `{title: "Summarize one video", readOnlyHint: true,
idempotentHint: true, openWorldHint: false}`.

---

### 4.5 `get-segment-context`

**Purpose:** full detail around one moment — the drill-down leaf of the guide.

**Description (ships verbatim):**

```
Everything around one moment in one video: the verbatim transcript window, the
on-screen text of nearby keyframes, the enclosing chapter, and frame ids you can
pass to get-frames.

USE WHEN: search or video-summary gave you a video_id and a timestamp and you need
the actual words — to quote accurately, to check context before/after, or to
decide whether a hit is relevant.

DO NOT USE: as a transcript dump (window is capped at 300s and 4000 chars — for
broad coverage call it two or three times at different t, or use video-summary);
to find the moment in the first place (use search).

Pass video_id and t exactly as they appeared in a previous result. Never invent
them. START WITH window=45.
```

**Parameters:**

| name | type | default | constraint | notes |
|---|---|---|---|---|
| `video_id` | string | — | **required** | |
| `t` | number \| string | — | **required**; clamped to `[0, duration]` | Seconds or `hh:mm:ss`. Out-of-range clamps and says so — no error for a slightly-late timestamp. |
| `window` | number | `45` | clamped 5..300 | Seconds each side of `t`. |
| `cue_id` | int | — | | Alternative to `t`; centres on that cue exactly. |
| `include_ocr` | bool | `true` | ≤ 8 frames, ≤ 1200 chars | On-screen text of keyframes in the window. |
| `include_frame_refs` | bool | `true` | ≤ 12 ids | Frame ids for `get-frames`. Ids only, never images. |
| `include_chapter` | bool | `true` | | |
| `include_links` | bool | `false` | ≤ 10 | Description links whose timestamp falls in the window. |
| `max_text_chars` | int | `4000` | `0` or 200..20000 | Transcript window budget. `0` still respects `window`. |

**Return shape:**

```
kCc8FmEb1nY · Let's build GPT: from scratch — Andrej Karpathy
Chapter: "kv cache and inference-time cost" (1:11:32-1:19:04)
Window: 1:11:36-1:13:06 (t=4321 ±45s) · https://youtu.be/kCc8FmEb1nY?t=4276

TRANSCRIPT
[1:11:36] and now if you think about what happens at generation time, you have a
[1:11:41] prompt, and then you sample one token, and then you feed the whole thing
[1:11:47] back in. so you are recomputing all of the keys and values for every
[1:11:52] token you already processed, every single step.
[1:12:03] so the reason we cache the keys and the values is that at every new token
[1:12:09] you would otherwise recompute attention over the entire prefix, which is
[1:12:15] quadratic. the cache makes it linear in the number of new tokens, and the
[1:12:22] price you pay is memory.
…
[1:13:01] which is why long-context inference is a memory-bandwidth problem.
(cues 1836-1861 · 1,910 chars, under the 4000 budget)

ON-SCREEN TEXT (3 keyframes)
[1:11:58] kCc8FmEb1nY-00701  cache = {} ; for k,v in layers: cache[k].append(v)   [code]
[1:12:26] kCc8FmEb1nY-00703  KV cache: O(n) memory, O(1) recompute per token      [slide]
[1:12:54] kCc8FmEb1nY-00705  (terminal) nvidia-smi — 18,304MiB / 24,564MiB        [terminal]

FRAMES: kCc8FmEb1nY-00701, kCc8FmEb1nY-00703, kCc8FmEb1nY-00705
  → get-frames frame_ids=["kCc8FmEb1nY-00703"] to see the slide

next: search q="memory bandwidth" video_id="kCc8FmEb1nY" to find where else he says this.
```

**Token discipline.** Double-capped: `window` seconds **and** `max_text_chars`,
whichever binds first, with the binding one named in the payload. OCR capped at
8 frames / 1200 chars, frame refs at 12 ids — all independent of `window`, so
`window=300` does not multiply the image-adjacent cost (screenpipe caps the
analogous `frame-context` at 50 nodes / 2000 chars for the same reason). Never
returns image content.

**Errors:** `E_UNKNOWN_VIDEO`, `E_NOT_INDEXED`, `E_INDEXING`, `E_BAD_PARAM`
(`cue_id` belonging to a different video names the right video).

**Annotations:** `{title: "Get context around a moment", readOnlyHint: true,
idempotentHint: true, openWorldHint: false}`.

---

### 4.6 `get-frames`

**Purpose:** deliver keyframe images — as authenticated URLs by default, or inline
base64 when the client actually renders it.

**This is the publishable contribution.** MCP's `ImageContent` is the correct
implementation and it is badly broken in real clients: Claude Code passes it
through as raw base64 *text*, ~15,000–25,000 tokens per image instead of ~1,600
(10–20× blowup), and the model cannot see it —
[claude-code#31208](https://github.com/anthropics/claude-code/issues/31208), closed
not-planned, with #14150 / #9152 / #4002 closed unfixed alongside it. The two
mitigations in the wild are `media-mcp`'s local file paths (useless for a remote
server — no shared filesystem) and serving a URL from the MCP server itself.
Nobody in the landscape survey ships the URL variant. We default to it.

**Description (ships verbatim):**

```
Fetch keyframe images from indexed videos. Returns image URLs by default; pass
return="image" only if you can render inline images.

USE WHEN: a result mentions a slide, diagram, chart, terminal or UI and the text
alone is not enough — you have frame ids from search, video-summary or
get-segment-context.

DO NOT USE: to browse a video visually (frames are keyframes, not a filmstrip —
use video-summary for structure); to read text that is already in the payload
(OCR text is returned with the search result, no image needed); with more than a
handful of ids at once.

START WITH limit=3 and return="url". URLs are signed and expire in 1 hour;
fetching one costs no context. return="image" inlines base64 JPEG and is capped at
4 images per call regardless of limit — on some clients inline images cost 10-20x
their nominal token price, so prefer URLs unless you know yours renders them.
```

**Parameters:**

| name | type | default | constraint | notes |
|---|---|---|---|---|
| `frame_ids` | string[] | — | ≤ 12 ids | Either this **or** `video_id` (+ optional time range). |
| `video_id` | string | — | | With `offset_start`/`offset_end`, returns the keyframes in that span. |
| `offset_start` / `offset_end` | number \| string | — | §3.2 | Span within `video_id`; span > 600s → `E_BAD_PARAM` with a narrowing hint. |
| `return` | enum `url\|image` | `url` | — | No `path` mode: this is a remote server. |
| `limit` | int | `3` | clamped 1..12 | |
| `width` | int | `512` | clamped 128..1280 | Longest edge; aspect preserved. |
| `quality` | int | `75` | clamped 20..95 | JPEG quality. |
| `include_ocr` | bool | `true` | ≤ 300 chars/frame | The frame's OCR text alongside — often removes the need to look at all. |

**Return shape — `return="url"` (default):** one text block, no image blocks.

```
Frames: 3/3
kCc8FmEb1nY-00703 · 1:12:26 · https://youtu.be/kCc8FmEb1nY?t=4344
  image: https://vidtheque.example.com/frames/kCc8FmEb1nY-00703.jpg?w=512&q=75&exp=1786348800&sig=8f3a…
  ocr: "KV cache: O(n) memory, O(1) recompute per token"
kCc8FmEb1nY-00705 · 1:12:54 · https://youtu.be/kCc8FmEb1nY?t=4372
  image: https://vidtheque.example.com/frames/kCc8FmEb1nY-00705.jpg?w=512&q=75&exp=1786348800&sig=1c02…
  ocr: "(terminal) nvidia-smi — 18,304MiB / 24,564MiB"
…
URLs expire 2026-08-08T18:00:00Z. They are signed — no auth header needed to fetch them.
```

Signed URLs, not bearer-protected paths: the renderer that fetches the image is
usually a browser context that will not attach the OAuth token. HMAC over
`(frame_id, w, q, exp)` with the server key, 1h TTL, constant-time compare. The
`/frames/<id>.jpg` route also accepts the OAuth bearer for programmatic clients.

**Return shape — `return="image"`:** a text block per frame followed by its
`ImageContent`:

```
📷 kCc8FmEb1nY-00703 · Let's build GPT · 1:12:26 · https://youtu.be/kCc8FmEb1nY?t=4344
```
```json
{"type":"image","data":"/9j/4AAQSkZJRgABAQ…","mimeType":"image/jpeg"}
```

**`mimeType` is `image/jpeg` and the bytes are JPEG.** screenpipe labels
ffmpeg-emitted MJPEG bytes as `image/png` (live bug); a mislabelled image is a
client-side decode failure with no useful error.

When more images are requested than the inline budget allows, the extras
**downgrade to URLs rather than failing**:

```
Frames: 6/6 (4 inline, 2 as URLs — inline cap is 4 images / 6MB per call)
```

**Token discipline.**
- `limit` clamped 1..12. Inline images capped at **4 per call and 6MB total,
  independent of `limit`** — this is the "bound the expensive path independently"
  rule (screenpipe's `MAX_INLINE_FRAMES_PER_SEARCH=20` + concurrency 4 + global
  semaphore 3, after `limit=500&include_frames=true` spawned 500 ffmpeg processes).
- OCR text capped at 300 chars/frame.
- Keyframe JPEGs are written at index time and served from disk. There is **no
  ffmpeg on the query path** — resizing is a cached ImageMagick/PIL resample keyed
  on `(frame_id, w, q)`, single-flight per key, 30-minute cache.
- Per-frame failures are collected, not fail-fast: the payload lists successes and
  a `failed:` line per bad id.

**Errors:** `E_UNKNOWN_FRAME` (names the valid ordinal range for that video),
`E_UNKNOWN_VIDEO`, `E_BAD_PARAM` (neither `frame_ids` nor `video_id`; span too
wide), `E_TOO_LARGE` (single frame > inline budget even alone → "use return=url"),
`E_BUSY`.

**Annotations:** `{title: "Get keyframe images", readOnlyHint: true,
idempotentHint: true, openWorldHint: false}`.

---

### 4.7 `index-video`

**Purpose:** add a video, playlist, or channel page to the corpus. Asynchronous —
returns a job id immediately.

Indexing is the only GPU path (download → whisperX → keyframes → OCR → embeddings)
and takes minutes. A synchronous tool call would blow every client's timeout, so
this returns a handle and `job-status` (§4.8) does the waiting.

**Description (ships verbatim):**

```
Add a video, playlist, or channel to the corpus. Returns immediately with a job
id — indexing runs in the background and takes roughly 1-3 minutes per hour of
video, longer if the GPU is busy.

USE WHEN: the user gives you a video URL that search says is not indexed, or asks
to add something to the library.

DO NOT USE: to fetch a transcript for immediate reading (nothing is queryable
until the job reaches "done" — poll job-status); on a URL the user did not ask
for. Do not call this repeatedly for the same URL: a video already in the corpus
returns its existing video_id without re-indexing unless force_reindex=true.

After calling, tell the user it is queued and give the estimate. Poll job-status
at most every 15 seconds.
```

**Parameters:**

| name | type | default | constraint | notes |
|---|---|---|---|---|
| `url` | string | — | one of `url` / `urls` required | Video, playlist, or channel URL. Bare 11-char ids accepted. |
| `urls` | string[] | — | ≤ 10 | Batch → one job id covering all of them. |
| `expand` | enum `none\|playlist\|channel_recent` | `playlist` | ≤ `max_items` | What a non-video URL expands to. `channel_recent` = newest N uploads. |
| `max_items` | int | `25` | clamped 1..200 | Hard ceiling on expansion. Exceeded → indexes the first `max_items` and says so. |
| `tags` | string | — | ≤ 10, §3.7 validation | Applied to every video in the job. |
| `force_reindex` | bool | `false` | | Re-runs the pipeline over an existing video. |
| `channels` | string | `all` | `all` \| subset of `transcript,ocr,frames` | Skip stages (e.g. transcript-only for a podcast). |
| `priority` | enum `normal\|high` | `normal` | | `high` jumps the queue; rate-limited per client. |

**Return shape:**

```
Job queued: job_7f3a29b1c04d
1 video: "Flash Attention 3 walkthrough" — GPU MODE (Qk7mF2xLp0A, 1:04:11)
Stages: download → transcribe → keyframes → ocr → embed
Queue position 2 · estimated 4-7 minutes (GPU currently busy with job_2b81c4)
Tags to apply: topic:attention, series:gpu-mode

Nothing from this video is searchable until the job reports done.
next: job-status job_id="job_7f3a29b1c04d" (poll no more than every 15s).
```

Already indexed, no `force_reindex`:

```
Already indexed: Qk7mF2xLp0A — "Flash Attention 3 walkthrough" (indexed 2026-08-07, coverage t,o,f)
No job created. next: video-summary video_id="Qk7mF2xLp0A", or index-video force_reindex=true to rebuild.
```

**Token discipline.** Playlist expansion capped at `max_items` (ceiling 200) and
the payload lists at most **10 titles**, then `… and 43 more`. Never echoes the
full expansion. Fixed-size response regardless of batch size.

**Errors:** `E_UNSUPPORTED_SOURCE` (yt-dlp can't handle it — lists what is
supported), `E_BAD_PARAM` (malformed URL, bad tag namespace — lists valid
namespaces), `E_RATE_LIMIT` (`retry_after_s`), `E_BUSY` (queue at capacity —
`retry_after_s`), `E_INTERNAL`.

**Annotations:** `{title: "Index a video", readOnlyHint: false,
idempotentHint: true, openWorldHint: true}`. Idempotent because the same URL
without `force_reindex` is a no-op returning the existing id; `openWorldHint: true`
because this is the one tool that reaches the internet.

---

### 4.8 `job-status`

**Purpose:** poll an indexing job, or list recent jobs.

**Description (ships verbatim):**

```
Check the status of an indexing job started by index-video. Call with no arguments
to list recent jobs.

USE WHEN: you started an index-video job and need to know whether the video is
searchable yet, or a tool told you a video is still indexing.

DO NOT USE: in a tight loop. Indexing takes minutes; poll at most every 15
seconds, and prefer telling the user "it is running, ask me again in a minute"
over polling repeatedly inside one turn.

A job is only searchable at state "done". States "transcribing" onward make the
transcript partially queryable — the response says exactly what is available.
```

**Parameters:**

| name | type | default | constraint | notes |
|---|---|---|---|---|
| `job_id` | string | — | | Omit to list recent jobs. |
| `video_id` | string | — | | Alternative lookup: the most recent job for that video. |
| `state` | enum `all\|active\|failed\|done` | `active` | | List mode filter. |
| `limit` | int | `5` | clamped 1..20 | List mode. |

**Return shape (single job):**

```
job_7f3a29b1c04d · state: transcribing · 41% · started 2026-08-08T16:58:12Z (3m 04s ago)
Video: Qk7mF2xLp0A "Flash Attention 3 walkthrough" — GPU MODE (1:04:11)
  download   done   18s
  transcribe running  41%  (whisperX large-v3, ~2m 10s remaining)
  keyframes  pending
  ocr        pending
  embed      pending
Queryable now: nothing (transcript is written on stage completion, not streamed).
next: job-status job_id="job_7f3a29b1c04d" again in ~60s.
```

**Return shape (list mode):**

```
Jobs: 3 active, 1 failed in the last 24h
job_7f3a29b1c04d  transcribing  41%  Qk7mF2xLp0A  Flash Attention 3 walkthrough
job_2b81c40dd7e9  embedding     88%  9dRk2XcVbNw  Speculative decoding, explained
job_c19aa4e2b530  queued         0%  (playlist, 12 videos)
job_5d0e77af1cc2  failed         —   HcQ8pL3vN1s  yt-dlp: video unavailable (private)
next: index-video url="…" force_reindex=true to retry a failed job.
```

Failure detail carries the actionable cause, never a stack trace:

```
job_5d0e77af1cc2 · state: failed at stage "download" · 2026-08-05T09:12:44Z
error: yt-dlp reported "Private video. Sign in if you've been granted access."
This is not retryable without credentials. next: pick a different URL, or configure
YTDLP_COOKIES on the server.
```

**Token discipline.** List mode capped at 20 jobs; stage table is fixed-size (5
rows); error text capped at 400 chars with the yt-dlp/worker tail preserved (the
useful end); no logs are ever returned through MCP.

**Errors:** `E_UNKNOWN_JOB` ("call job-status with no arguments to list recent
jobs"), `E_BAD_PARAM`.

**Annotations:** `{title: "Check indexing job", readOnlyHint: true,
idempotentHint: false, openWorldHint: false}` — the one read tool where repeated
calls legitimately return different answers, so it must not be cached (§3.9).

---

### 4.9 `tag-video`

**Purpose:** curate the corpus — add or remove namespaced tags that then scope
`search`, `list-videos` and `corpus-summary`.

Tags are the corpus product's spine: they are how a library becomes collections,
and how `include_related` co-occurrence has anything to co-occur with. Something
has to write them, and making that a model-callable tool means the user can say
"tag everything from this playlist as `series:gpu-mode`" in the same conversation
where they discovered the playlist.

**Description (ships verbatim):**

```
Add or remove tags on an indexed video. Tags are namespaced — topic:, person:,
project:, source:, lang:, series: — and are used as filters by search,
list-videos and corpus-summary.

USE WHEN: the user asks to organise, label, or collect videos, or explicitly
approves a tag you proposed.

DO NOT USE: to tag speculatively. Never invent tags the user did not ask for —
a corpus with 400 machine-generated topic tags is worse than one with none.
Check existing tags first (corpus-summary include_tags=true) and reuse them rather
than coining near-duplicates.

Both add and remove are idempotent: adding an existing tag or removing an absent
one succeeds and reports no change.
```

**Parameters:**

| name | type | default | constraint | notes |
|---|---|---|---|---|
| `video_id` | string \| string[] | — | **required**, ≤ 50 ids | Batch tagging in one call. |
| `add` | string[] | — | ≤ 10 tags, §3.7 regex | |
| `remove` | string[] | — | ≤ 10 tags | |
| `dry_run` | bool | `false` | | Reports what would change. Useful before a 50-video batch. |

At least one of `add`/`remove` required.

**Return shape:**

```
Tagged 12 videos.
  added:   series:gpu-mode (12 videos, 11 new / 1 already present)
           topic:cuda (12 videos, 9 new / 3 already present)
  removed: topic:misc (4 videos)
Corpus now has 114 distinct tags across 6 namespaces.
next: search q="warp divergence" tags="series:gpu-mode" limit=5
```

**Token discipline.** Fixed-size response regardless of batch size — counts and at
most 10 tag lines, never a per-video echo. `dry_run` output is the same shape.

**Errors:** `E_UNKNOWN_VIDEO` (names which ids; **partial batches do not apply** —
the whole call is one transaction), `E_BAD_PARAM` (invalid namespace or format,
lists the valid namespaces and the regex).

**Annotations:** `{title: "Tag a video", readOnlyHint: false, idempotentHint: true,
openWorldHint: false}`.

---

## 5. Resources

Three, deliberately — the same count screenpipe settled on. Reads stay Tools:
their proposal to migrate reads to Resources (#4863) died as stale, and client
support for resources is still far behind tools.

### 5.1 `vidtheque://corpus` — the browsable library

`mimeType: text/tab-separated-values`. The cheap unfiltered view of the library, so
a client that surfaces resources lets the user *see* what is indexed without
spending a tool call. Capped at 200 rows, most recently published first.

```
# vidtheque corpus · 312 videos · 486h 12m · generated 2026-08-08T17:02:11Z
video_id	title	channel	published	duration	coverage	tags
Qk7mF2xLp0A	Flash Attention 3 walkthrough	GPU MODE	2026-08-02	1:04:11	tof	topic:attention,series:gpu-mode
9dRk2XcVbNw	Speculative decoding, explained	Trelis Research	2026-07-29	0:38:20	tof	topic:inference
…
# showing 200 of 312 — narrow with the list-videos tool (channel=, tags=, q=, published_after=)
```

The footer is the `format=outline` lesson applied to a resource: say what you
truncated and name the tool that narrows it.

### 5.2 `vidtheque://context` — precomputed timestamps and corpus facts

`mimeType: application/json`. Exists so the model never does date arithmetic —
screenpipe's version is the direct ancestor, and their bug #3124 (advertised
relative dates silently returning nothing) is what date-math-by-LLM looks like
when it fails.

```json
{
  "current_time": "2026-08-08T17:02:11Z",
  "timezone": "Europe/Paris",
  "timestamps": {
    "today_start": "2026-08-08T00:00:00Z",
    "yesterday_start": "2026-08-07T00:00:00Z",
    "one_week_ago": "2026-08-01T17:02:11Z",
    "one_month_ago": "2026-07-08T17:02:11Z",
    "one_year_ago": "2025-08-08T17:02:11Z"
  },
  "corpus": {
    "videos": 312,
    "total_duration_seconds": 1750320,
    "first_published": "2019-04-02",
    "last_published": "2026-08-02",
    "last_indexed": "2026-08-07T19:41:03Z",
    "active_jobs": 1,
    "data_status": "indexing"
  },
  "channels_top": ["GPU MODE", "Yannic Kilcher", "3Blue1Brown", "Andrej Karpathy"],
  "tag_namespaces": ["topic", "person", "project", "source", "lang", "series"],
  "id_formats": {
    "video_id": "11-char YouTube id, e.g. kCc8FmEb1nY",
    "frame_id": "<video_id>-<5-digit keyframe ordinal>, e.g. kCc8FmEb1nY-00703",
    "job_id": "job_ + 12 hex"
  },
  "deep_link_format": "https://youtu.be/<video_id>?t=<seconds>",
  "features": {"diarization": true, "ocr": true, "frame_embeddings": true},
  "server_version": "0.1.0"
}
```

### 5.3 `vidtheque://guide` — progressive disclosure

`mimeType: text/markdown`. Copied nearly verbatim in *shape* from
`screenpipe://guide`, which is the single most transferable artefact in that repo.

```markdown
# Using vidtheque

A persistent, searchable index of videos the user has chosen to keep. Work from
the top down — each step narrows what the next one has to read.

| Step | Tool | When |
|---|---|---|
| 1 | corpus-summary | "what's in the library?", "do I have anything on X?", and after any empty search |
| 2 | search | you need specific words, claims or visuals. START limit=5 |
| 3 | video-summary | you have a video_id and need its structure or a timestamp to aim at |
| 4 | get-segment-context | you have (video_id, t) and need the actual words |
| 5 | get-frames | text is not enough and you have frame ids. return="url" unless you render images |

Adding to the library: index-video → job-status. Nothing is searchable until the
job reports "done".

## Rules

- **Never fabricate ids or timestamps.** Only use video_id, frame_id, cue_id and
  t values that appeared in an actual result. A plausible-looking YouTube id that
  came from your memory is not in this corpus.
- **This searches only what is indexed.** It is not the YouTube catalogue. If
  something is missing, the answer is index-video, not a guess.
- Two time axes: `published_after/before` chooses videos by upload date;
  `offset_start/end` chooses seconds inside a video. They are not interchangeable.
- `channel` and `video_title` are case-insensitive substrings.
- Ordering defaults to relevance. Pass `order=recency` only if the user asked for
  "latest" or "newest".
- Start with `limit=5` and `max_text_chars=500`. Raise them when the first page
  proves the query is right.
- Auto-generated captions are noisy: unusual spellings, no punctuation, wrong
  proper nouns. Prefer two or three words over an exact long phrase, and check
  `get-segment-context` before quoting anything verbatim.
- Every timestamped result carries a `https://youtu.be/<id>?t=<s>` link. Give the
  user the link, not just the timestamp.
- `search` never returns images. Frame ids do; `get-frames` turns them into URLs.
- Read the pagination line: `Results: 10/~40+ (use offset=10 for more)` tells you
  your next call.
```

---

## 6. Deliberately deferred

| Thing | Why not v1 | Sketch if it lands |
|---|---|---|
| **Channel/playlist subscriptions** | The tool budget is the point (screenpipe's 28 tools are a warning, not a model). Subscriptions are *configuration* with a cron behind them, not something a model needs mid-conversation — and they are three tools (`subscribe`, `list-subscriptions`, `unsubscribe`) for one workflow, pushing the surface to 12. `index-video expand=channel_recent` already covers "catch me up on this channel" on demand. | `subscribe(url, expand, tags, max_items, check_interval)` → subscription id; cron enqueues new uploads as normal jobs; `list-subscriptions` folds into `corpus-summary include_subscriptions=true` rather than being its own tool. |
| **`get-clip`** | An ffmpeg cut is a slow, storage-producing, mostly-redundant operation: `youtu.be/<id>?t=<s>` already sends a human to the exact moment, and a model cannot watch an MP4. It also drags in retention policy and a second signed-URL kind. | `get-clip(video_id, start, end, max 120s)` → async job → signed URL, same job machinery as indexing. |
| **Markdown export** | Real product value (Obsidian, "data never hostage") but it is a *user* workflow, not a model one, and it is the biggest single payload the server could emit. Belongs on the HTTP API and any future web UI. | `GET /videos/<id>/export.md` — no MCP tool. |
| **Speaker identity management** (`list-unnamed-speakers`, `update-speaker`, `merge-speakers`) | Three tools for a curation workflow that only pays off once diarization is on across a large corpus. `search speaker=` and `video-summary include_speakers` cover reading; writing can wait. | Straight port of screenpipe's trio if it earns its place. |
| **Permissions / row-level filtering** | Single-owner corpus. The transferable parts are noted for later: server-authoritative policy from the credential (never a model-settable field), enforcement at both middleware and post-query row level, and *no total leakage* when restrictions are active. | — |
| **`format=json`** | Model-facing JSON is strictly worse than TSV (−73%) and worse than text for prose. Structured consumers read `structuredContent`. | — |

---

## 7. Worst-case response budget

Every tool has a bounded worst case. This table is the thing to re-check when a
parameter gets added.

| Tool | Worst case at ceiling settings | What bounds it |
|---|---|---|
| `search` | ~50 items × (20,000 + 220) chars → **capped at 60,000 by the response cap** | `limit≤50`, `max_text_chars`, response cap, candidate cap 5000/leg |
| `list-videos` | 100 rows × ~180 chars ≈ 18,000 | `limit≤100`, TSV, 120-char cells |
| `corpus-summary` | ~3,500 | fixed section caps (50/100/25) |
| `video-summary` | ~6,000 | 50 chapters + 30 key texts + 30 OCR × 1,200 chars, response cap |
| `get-segment-context` | ~22,000 (`max_text_chars=20000` + 1,200 OCR) | window ≤300s **and** char budget |
| `get-frames` (`url`) | 12 × ~400 ≈ 5,000 | `limit≤12`, OCR 300/frame |
| `get-frames` (`image`) | 4 images / 6MB + ~1,600 chars | inline cap independent of `limit` |
| `index-video` | ~1,200 | ≤10 titles echoed |
| `job-status` | ~2,500 | ≤20 jobs, fixed stage table, 400-char errors |
| `tag-video` | ~800 | counts only, no per-video echo |

---

## 8. Open questions for Tom

These are the forks where I could argue either side. Everything else in this
document is a decision, not a proposal.

1. **`offset_start`/`offset_end` collides with `offset` (pagination).** Same
   prefix, completely different meaning, in the same parameter list — and models
   confuse near-identical names more reliably than they confuse different ones.
   The handoff fixed these names, so I used them, but the alternatives are
   `t_start`/`t_end` (short, matches `get-segment-context`'s `t`), or
   `video_time_start`/`video_time_end` (verbose, matches `order=video_time`), or
   renaming the pagination one to `page_offset`. Worth settling before the guide
   and every description hard-code it.

2. **`order=video_time` without a single-video scope: error or group-by-video?**
   I chose a typed `E_ORDER_SCOPE` error because "chronological across 300 videos"
   is meaningless. The alternative is to accept it and sort by
   `(video published desc, offset asc)`, which is defensible for a two- or
   three-video scope and never errors. Erroring teaches, permissiveness flows.

3. **Does `tag-video` make the v1 cut, or do tags arrive only via
   `index-video tags=`?** Tags are the corpus product's spine and `include_related`
   needs them, but it is the only pure-write tool and "never invent tags" is a
   guardrail I do not fully trust a model to hold. Dropping it takes v1 to 8 tools
   and makes tagging an `index-video`/HTTP-API concern.

4. **Subscriptions in v1 after all?** I deferred them (§6) on tool-budget grounds,
   and `index-video expand=channel_recent` covers the on-demand case. If the
   "watch-later replacement" framing is the product story you want to *lead* with
   rather than a v1.1 addition, they should be in — the cost is +1 to +3 tools and
   a cron surface.

5. **`corpus-summary` and `list-videos` overlap at the edges.** Two tools, both
   answering "what's in the library", separated by aggregate-vs-rows. A single
   `corpus` tool with `view=summary|list` would be 8 tools instead of 9 — but it
   would also merge two very different parameter sets, and screenpipe's split
   (`activity-summary` vs `search-content`) has held for 20 months. I kept them
   split; say if you want the merge.

6. **Signed frame URLs vs bearer-only.** Signed URLs (HMAC + 1h expiry) are the
   only thing a browser-side renderer can actually fetch, but they are a capability
   URL: anyone holding one reads that frame for an hour, no OAuth. Alternatives:
   shorter TTL (5 min, likelier to expire mid-conversation), or bearer-only
   (correct, but images never render in any client that does not proxy the fetch).
   I chose 1h signed. This is the one security-shaped decision in the surface.

7. **Should `search` fall back to the vector leg when FTS returns nothing?** A
   silent semantic fallback rescues bad keyword queries ("that video about the
   memory wall") but makes results non-explainable and hides a genuinely empty
   corpus. Options: never (current — the empty-result payload suggests a next
   query instead), automatic-with-a-`note:`, or an explicit `mode=keyword|semantic|hybrid`
   parameter. Related: whether `content_type=all` should *always* run hybrid text
   retrieval (BM25 + text embeddings fused by RRF) rather than BM25-only, which is
   what §3.10 currently specifies.

8. **FTS5 tokenizer: `unicode61` or `porter`?** screenpipe uses `unicode61`
   everywhere (no stemming) because screen text is full of compound identifiers.
   Our corpus is mostly spoken prose, where stemming ("cache"/"caching",
   "quantize"/"quantization") is a real recall win — but it hurts on code and CLI
   text in the OCR leg. Plausible answer: `porter` on the transcript FTS table,
   `unicode61` on the OCR one. That means two tokenizers and two ranking profiles
   to reason about in the fusion step.

9. **Description length.** The USE WHEN / DO NOT USE blocks above run 120–190
   words each; nine of them plus schemas is roughly 3.5–4.5k tokens of permanent
   context in every session. screenpipe spends far more (28 tools), but they are
   not competing with an agent's other MCP servers. If that is too much, the lever
   is moving the shared rules (two time axes, case-insensitivity, never fabricate
   ids) out of every description and into the `guide` resource alone — cheaper, but
   only works on clients that actually read resources.

10. **Diarization on by default?** `search speaker=`, `video-summary
    include_speakers` and the `E_FEATURE_DISABLED` path all exist for it, and
    whisperX gives it to us — but pyannote adds a model to the GPU lease dance and
    a licence-acceptance step to the setup docs. Ship v1 with `DIARIZE=0` and the
    parameters present-but-disabled, or `DIARIZE=1` and pay the setup friction?
