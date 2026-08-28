# vidtheque index schema — v1

The database contract. `tool-surface.md` says what the MCP server exposes; this
says what backs it. Implementation follows this document; if implementation needs
to diverge, this document changes in the same commit.

**Reconciled with the shipped schema and pipeline on 2026-08-08.** Where the two
disagreed, the reviewed implementation won and the text below was changed to match;
`docs/design/DECISIONS.md` still outranks both, and `mcp/README.md` keeps the
running list of deviations with the reasoning behind each.

**Everything here was executed.** The DDL, the triggers and every query in §4 were
run against SQLite 3.46.1 with `sqlite-vec` 0.1.9 and CPython 3.13, on a synthetic
fixture at the target scale — 500 videos × 20 min: 150,000 cues, 18,500 chunks,
40,000 keyframes, 200,000 OCR lines. Numbers quoted as "measured" come from that
fixture on Tom's box. Numbers quoted as "estimated" are arithmetic and say so.

---

## 0. Shape of the thing

**The corpus is one SQLite file plus one directory of JPEGs.** Nothing else is
state. That is the whole operational story: back up two paths, move two paths,
`rm -rf` two paths. The GPU worker holds no state and never opens the database
(`mcp/ ↔ worker/ is HTTP only` — CLAUDE.md invariant).

Four rules the rest of the document keeps falling out of:

1. **Internal ids are integers; wire ids are strings.** FTS5 external-content
   tables key on `content_rowid`, and `sqlite-vec` keys on `rowid`. Both want a
   dense INTEGER. So every table has an INTEGER surrogate key, and the API's
   `video_id` (`kCc8FmEb1nY`) / `frame_id` (`kCc8FmEb1nY-00412`) are translated at
   the HTTP edge, exactly once, in one function.
2. **One writer.** The `mcp` process owns the only write connection. Readers are a
   pool of `mode=ro` connections. This is not a scaling compromise, it is what
   makes WAL, `busy_timeout` and cancellation simple enough to reason about (§5).
3. **Derived data is derived.** Cues, chunks, keyframes, OCR, vectors and both FTS
   indexes are reproducible from the source media. Tags, collections and the
   `videos` rows themselves are not. That split drives the backup story (§5.5) and
   the migration story (§1.10): derived tables get rebuilt, never migrated.
4. **Filters resolve to a video-id set first.** `videos` is O(hundreds) — a full
   scan for a case-insensitive substring `channel`/`video_title` filter took
   **1.0 ms over 499 rows** (measured). Every heavy query in §4 starts by
   collapsing the corpus-axis filters into a small `video_id` list, then hands that
   to the FTS and vector legs. This is the cheap half of screenpipe's MATERIALIZED
   candidate-CTE lesson (deep-dive §3o, issue #4474): cap the candidate set before
   anything expensive joins to it.

---

## 1. Tables

Every table is `STRICT`. Timestamps are INTEGER unix seconds UTC; positions inside
a video are REAL seconds. `PRAGMA foreign_keys=ON` is set on **every** connection —
it is per-connection, not persisted in the file, and forgetting it on one
connection silently disables every `ON DELETE CASCADE` in §1.

### 1.1 `config` — the anti-drift table

The single most important table in the file. Index-time and query-time must use
the same models; if they drift, retrieval degrades silently and looks like a bad
corpus rather than a bug.

```sql
CREATE TABLE config (
  key        TEXT PRIMARY KEY,
  value      TEXT NOT NULL,
  updated_at INTEGER NOT NULL DEFAULT (unixepoch())
) STRICT;
```

Seeded at first migration; **read at boot, never at query time**:

| key | value (after 0004) | why it is pinned here and not in env |
|---|---|---|
| `text_embed.model` | `Qwen/Qwen3-VL-Embedding-2B` | The vectors in the file were produced by *this*. Env can change; the file cannot retroactively agree. |
| `text_embed.dim` | `2048` | Must equal the `vec_chunks` declared dimension. Asserted at boot. |
| `text_embed.normalized` | `1` | Written L2-normalized, so cosine ≡ dot. |
| `text_embed.query_prefix` | `Given a search query, retrieve the transcript passage that answers it` | The record of what indexing assumed. *Applied by the worker, not here* (note below). |
| `frame_embed.model` | `Qwen/Qwen3-VL-Embedding-2B` | **The same checkpoint.** Still its own key: two independently-staleable stages, two dimension assertions, and one of them may be pointed elsewhere. |
| `frame_embed.dim` | `2048` | Must equal `vec_frames`. |
| `frame_embed.query_prefix` | `Given a search query, retrieve the video frame that matches it` | Added by 0004. A *different* instruction from the text leg's: one model, one space, two retrieval tasks. |
| `frame_embed.storage` | `float32` | `float32` or `int8` (§3.4). |
| `stt.model` | `large-v3` | Drives `video_stages` staleness, not query correctness. |
| `ocr.model` | `rapidocr-default` | |
| `diarization.enabled` | `0` | Backs `E_FEATURE_DISABLED` for `search speaker=` (tool-surface §4.1). |
| `chunk.target_seconds` / `chunk.overlap_seconds` | `45` / `15` | Changing these invalidates every chunk vector. |
| `pipeline.version` | `2` | Bumped when pipeline *semantics* change; see §1.10. 0004 bumped it: a checkpoint swap changes no column and invalidates every vector. |

**One model, two records, and they stay two.** Tom's decision of 2026-08-09
(`research/multimodal-embedding-2026-08-09.md`, addendum) replaced
`Qwen/Qwen3-Embedding-0.6B` + `google/siglip2-so400m-patch16-naflex` with one
`Qwen/Qwen3-VL-Embedding-2B` at its native 2048 dims, bf16, non-resident. Both
legs are now one vector space served by one loaded model in one worker slot.
Nothing above collapses: the two legs remain two stages, two config records and
two boot assertions, because the pair is a *configuration* — the old models are
still selectable, and the memo's §7 hybrid (`Qwen3-Embedding-4B` for
transcripts + `Qwen3-VL-Embedding-2B` for frames) is two checkpoints again.
`text_embed.model == frame_embed.model` is what tells the drift check it may
treat a frame-space mismatch as a whole-index mismatch (§5.4 of the memo);
when they differ, a frame-space mismatch costs the frame leg alone.

**The model values are the exact identifiers the worker reports**, i.e. what
`deploy/.env.example` ships as `EMBED_MODEL` / `IMAGE_EMBED_MODEL` / `OCR_MODEL`
/ `STT_MODEL`. That is the whole point of the comparison: the drift check is an
exact match (casefold only, no prefix stripping), so two genuinely different
checkpoints are still caught, and `model_key` (§2.2) reads back as the thing
that produced the row. Migration 0001 seeded friendly short names
(`qwen3-embedding-0.6b`) and 0002 renames them — with the defaults on both
sides disagreeing, the drift check fired on a default install and disabled both
vector legs before anything was written (research/e2e-smoke-2026-08-08.md §4.1).

**The query prefix is the worker's to apply, not the query layer's.** `POST
/v1/embeddings` takes `input_type=document|query` and the worker applies the
checkpoint's own instruction itself, so the query layer sends the switch
(`input_type="query"`) and never prepends `config['text_embed.query_prefix']` —
doing both applies it twice, which is exactly the silent drift this table exists
to prevent. The keys stay because they record what *indexing* assumed; nothing
on the read path reads them to build a string.

**And a record nobody can check is a record that lies.** These keys shipped
wrong from 0001 — `query: ` recorded against a model that applies `Instruct:
…\nQuery: …` — and nothing noticed for the life of the project, because nothing
reads them (`research/pipeline-perf-2026-08-09.md` §5). An instruction-aware
model with *two* instructions over *one* space makes them more load-bearing,
not less: the same sentence embeds differently under each, so "which model" is
no longer the whole answer to "what space is this". Two things close it:

* 0004 writes the worker's actual shipped defaults into both keys
  (`DEFAULT_QUERY_INSTRUCTION` / `DEFAULT_FRAME_INSTRUCTION` in
  `worker/src/vidtheque_worker/backends/qwen3_vl_embed.py`, overridable with
  `EMBED_QUERY_PROMPT` / `FRAME_QUERY_PROMPT`);
* the worker now **says what it applied** — `instruction` on every
  `EmbeddingsResponse`, and `instructions` per backend on `GET /status`. So
  `curl $WORKER_URL/status` against these two rows is the whole reconciliation,
  and it is one command rather than a code read.

The remaining gap is honest and small: nothing *automatically* reconciles the
two. Wiring the echoed `instruction` into the drift check would mean widening
the `(vectors, model, dimensions)` tuple the entire vector path is built on, for
a check that a human can now run in one command. Recorded as a choice, not an
oversight.

**Boot assertion** (runs before the server accepts a request):

```sql
SELECT
  (SELECT value FROM config WHERE key='text_embed.dim')  AS want_text_dim,
  (SELECT value FROM config WHERE key='frame_embed.dim') AS want_frame_dim;
```

compared against the dimensions declared on the vec tables and against the model
ids the worker reports from `GET /v1/models`. A mismatch is **fatal at boot for
writes and degrades reads**: the server refuses to index (it would mix embedding
spaces) but still serves FTS-only search, with a `note:` on every response saying
the vector legs are disabled and why. Never silently continue — mixed embedding
spaces produce plausible-looking garbage that no test catches.

### 1.2 `videos`

```sql
CREATE TABLE videos (
  id            INTEGER PRIMARY KEY,
  source        TEXT    NOT NULL DEFAULT 'youtube',
  source_id     TEXT    NOT NULL,
  public_id     TEXT    GENERATED ALWAYS AS (
                  CASE WHEN source = 'youtube' THEN source_id
                       ELSE source || ':' || source_id END) VIRTUAL,
  url           TEXT    NOT NULL,
  title         TEXT    NOT NULL,
  title_lc      TEXT    GENERATED ALWAYS AS (lower(title)) VIRTUAL,
  description   TEXT,
  channel_id    TEXT,
  channel_name  TEXT,
  channel_lc    TEXT    GENERATED ALWAYS AS (lower(channel_name)) VIRTUAL,
  published_at  INTEGER,
  duration_s    REAL    NOT NULL DEFAULT 0,
  language      TEXT,
  chapters_json TEXT,
  thumb_path    TEXT,
  media_path    TEXT,
  audio_path    TEXT,
  index_state   TEXT    NOT NULL DEFAULT 'pending'
                CHECK (index_state IN ('pending','indexing','ready','failed','stale')),
  indexed_at    INTEGER,
  added_at      INTEGER NOT NULL DEFAULT (unixepoch()),
  updated_at    INTEGER NOT NULL DEFAULT (unixepoch()),
  UNIQUE (source, source_id)
) STRICT;

CREATE INDEX videos_published  ON videos(published_at DESC);
CREATE INDEX videos_channel_lc ON videos(channel_lc, published_at DESC);
CREATE INDEX videos_public_id  ON videos(public_id);
CREATE INDEX videos_state      ON videos(index_state) WHERE index_state <> 'ready';
```

Notes:

- `public_id` is the wire `video_id` from tool-surface §3.1, computed rather than
  stored so the two can never disagree. Generated columns are `VIRTUAL` (computed
  on read) because these are all cheap expressions over columns already on the
  page; `STORED` would cost bytes for no gain. Indexes on virtual generated
  columns work — `videos_channel_lc` is used by the filter resolution in §4.1.
- `channel`/`video_title` filters are case-insensitive **substring** (tool-surface
  §4.1). `LIKE '%x%'` cannot use the index; that is fine and deliberate — see
  rule 4 in §0, it is a 1 ms scan. Do not add trigram FTS for this until `videos`
  is in the tens of thousands.
- `index_state` is the coarse flag for filtering and `data_status`; per-stage truth
  lives in `video_stages`.
- `stale` means "indexed, but with a pipeline/model version we no longer consider
  current" — it stays fully searchable, it is just a candidate for reindex.
- `chapters_json` holds yt-dlp's chapter payload **verbatim** for provenance;
  `chapters` (§1.3) is the queryable projection. Store the raw thing you were
  given, query the thing you derived — reparsing is free, re-fetching is not.

### 1.3 `video_stages`, `chapters`, `video_links`

Per-stage state is what makes incremental reindex possible: swapping the STT model
should re-run STT, chunking and text embedding, and leave 40,000 keyframes and
their OCR alone.

```sql
CREATE TABLE video_stages (
  video_id      INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
  stage         TEXT    NOT NULL
                CHECK (stage IN ('fetch','stt','chunk','text_embed',
                                 'keyframe','ocr','frame_embed')),
  state         TEXT    NOT NULL DEFAULT 'pending'
                CHECK (state IN ('pending','running','done','failed','skipped')),
  model_key     TEXT,
  stage_version INTEGER NOT NULL DEFAULT 1,
  started_at    INTEGER,
  finished_at   INTEGER,
  error         TEXT,
  PRIMARY KEY (video_id, stage)
) WITHOUT ROWID, STRICT;
```

**`fetch` is one stage covering both halves of acquisition** — the metadata probe
(info dict, chapters, subtitle inventory, heatmap) *and* the media download (audio,
plus video at the height cap when frames are wanted). It was drafted as two,
`fetch_metadata` and `fetch_media`; splitting them buys a finer resume boundary for
the cheaper half — the probe is one request, the download is hundreds of megabytes —
so the CHECK keeps its seven values and the runner reports progress *inside* the
stage instead (0.0 → 0.25 once the info dict is in → 1.0).

**Both halves are bought per consumer, not per item.** The media download has
always been: the mp4 is fetched only when the `keyframe` stage it feeds is
actually going to run. The probe is now held to the same rule. An item whose URL
names a video `videos` already holds resolves **out of the index** — the id in
the URL against `videos.source_id`, the same resolution `index-video` does — and
neither probes nor waits out the politeness gap, provided all three of the
stages that consume the info dict are current:

| consumer | why it needs the probe |
|---|---|
| `fetch` | its own `model_key` is stale (a yt-dlp upgrade) or its row is not `done` — then this item *is* a fetch |
| `stt` | a transcript that will re-run needs the audio or the subtitle inventory, both of which only the info dict carries |
| `keyframe` | the media download itself |

The `stt` question is asked **with the worker assumed healthy**: its `_should_run`
clause is "only re-transcribe when we can do better", so a momentarily
unreachable worker would otherwise read as "stt will not run" and buy a fast path
that a worker returning mid-item then needs the probe for.

A container URL (`/playlist?list=…&v=<id>` names a video id inside a URL meaning
"fan me out") and any `force_reindex` are excluded outright. So is a URL whose
**host** is not one we index from: the identity is `(source, source_id)`, both
columns, because an eleven-character id in the query string of some other host
is not a claim about this corpus. Matching on `source_id` alone let
`https://evil.example/?v=<a known id>` resolve to that video, skip the probe,
and run the caller's stages and tags against it (2026-08-09 review); the same
pair now keys `index-video`'s already-indexed lookup. Anything unrecognised
falls to the slow path, which asks yt-dlp — the correct answer, just not the
free one. Everything else is
unchanged, and that is the point — the **claim is still taken** (`E_INDEXING`
across jobs, `E_DUPLICATE_ITEM` within one), the video still reads `indexing` for
the length of the pass, and the `fetch` row is left alone rather than re-stamped
with a `finished_at` for a stage that did not run. The job log says where the
metadata came from: `resolved locally; nothing to fetch`, never `fetched
metadata for …`. Measured on the 2026-08-10 re-embed, before the rule was
extended: 78 already-indexed videos, each paying ~90 s of politeness sleep and
one YouTube round trip in front of ~15 s of purely local work — 2.5 hours
instead of ~20 minutes, and 78 requests spent from a rate-limited box to learn
78 identities the file already held.

`model_key` records the `config` value in force when the stage ran. The reindex
planner is one query:

```sql
-- which stages of which videos are out of date?
SELECT v.public_id, s.stage, s.model_key AS ran_with, c.value AS current
FROM video_stages s
JOIN videos v ON v.id = s.video_id
JOIN config  c ON c.key = CASE s.stage
                    WHEN 'stt'         THEN 'stt.model'
                    WHEN 'text_embed'  THEN 'text_embed.model'
                    WHEN 'ocr'         THEN 'ocr.model'
                    WHEN 'frame_embed' THEN 'frame_embed.model'
                  END
WHERE s.state = 'done' AND s.model_key IS NOT c.value;
```

**`+` splits contract from provenance.** Everything before the first `+` in a
`model_key` is the *contract* — change it and the stage is out of date.
Everything after it records *how* that answer was produced and is not, on its
own, grounds for re-running anything. Only `keyframe` uses the suffix today:

| written | means |
|---|---|
| `scenedetect-<detector>-w<max_width>` | detected before 2026-08-09: full-resolution decode, `cv2.resize` down to 256 px |
| `scenedetect-<detector>-w<max_width>+fused` | detected by the fused pass: one libswscale convert **at** 256 px |

The two produce shot boundaries that can differ by a frame or two (the detector
math is identical; the pixels reaching it are resampled by a different filter),
so they may not share a key — but a corpus indexed under the first one is not
stale, it is *older*. `_should_run` compares only the contract half for
`keyframe` (`pipeline/runner.py`, `_stage_contract`), which is what lets 75
already-indexed videos keep their keyframes, their OCR and their frame vectors
instead of re-downloading every source video that `keep_source=audio` deleted.
`force_reindex=true` is how a video is deliberately moved onto the new path.
The reindex-planner query above is unaffected: `keyframe` has no `config` row
and never appears in it.

`skipped` is how `index-video channels=transcript` (tool-surface §4.7) records that
keyframes were *deliberately* not built — distinguishable from `pending`, so
`coverage t,o,f` and `data_status` never report a deliberate choice as missing data.

**`error` is set on `done` rows too**, and only there does it mean "finished, but
not on all of it". The case that produces it is a frame the worker refuses as
undecodable (`invalid_image`, worker/openapi.json): one corrupt keyframe out of
six hundred is skipped and named, rather than costing the video its OCR or its
frame vectors — but a stage that finished having dropped work must say so
somewhere that outlives the process, and this column is that place. Every query
that reads a failure reads `state`, never the presence of `error` (`degraded_items`,
the resume plan, the `failed` rollup in §4), so a note here degrades nothing. A
stage where *every* frame was refused is `failed`, not `done`: `has_ocr` and
`has_frames` are "the stage is done", and a done stage that wrote nothing would
advertise a search channel that answers nothing.

```sql
CREATE TABLE chapters (
  id       INTEGER PRIMARY KEY,
  video_id INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
  seq      INTEGER NOT NULL,
  start_s  REAL    NOT NULL,
  end_s    REAL    NOT NULL,
  title    TEXT    NOT NULL,
  UNIQUE (video_id, seq)
) STRICT;
CREATE INDEX chapters_lookup ON chapters(video_id, start_s);

-- description links, for get-segment-context include_links (tool-surface §4.5)
CREATE TABLE video_links (
  id       INTEGER PRIMARY KEY,
  video_id INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
  t_s      REAL,               -- NULL = not timestamped
  url      TEXT    NOT NULL,
  title    TEXT,
  seq      INTEGER NOT NULL,
  UNIQUE (video_id, seq)
) STRICT;
CREATE INDEX video_links_time ON video_links(video_id, t_s) WHERE t_s IS NOT NULL;
```

### 1.4 `speakers` and `cues`

```sql
CREATE TABLE speakers (
  id           INTEGER PRIMARY KEY,
  label        TEXT NOT NULL UNIQUE,   -- diarizer output, e.g. 'SPEAKER_00@kCc8FmEb1nY'
  display_name TEXT,                   -- human-assigned, NULL until named
  merged_into  INTEGER REFERENCES speakers(id) ON DELETE SET NULL,
  created_at   INTEGER NOT NULL DEFAULT (unixepoch())
) STRICT;

CREATE TABLE cues (
  id          INTEGER PRIMARY KEY,
  video_id    INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
  seq         INTEGER NOT NULL,
  start_s     REAL    NOT NULL,
  end_s       REAL    NOT NULL,
  text        TEXT    NOT NULL,
  speaker_id  INTEGER REFERENCES speakers(id) ON DELETE SET NULL,
  origin      TEXT    NOT NULL DEFAULT 'whisperx'
              CHECK (origin IN ('whisperx','yt_manual','yt_auto')),
  avg_logprob REAL,
  words_json  TEXT,
  UNIQUE (video_id, seq)
) STRICT;
CREATE INDEX cues_time    ON cues(video_id, start_s);
CREATE INDEX cues_speaker ON cues(speaker_id) WHERE speaker_id IS NOT NULL;
```

**`cue_id` contiguity is an invariant, not an accident.** A video's cues are
inserted in one pass, in time order, in one transaction, so within a video: id
order == `seq` order == time order, and a time-contiguous run of cues is an
id-contiguous range. That is what lets `search` print `cues 1841-1849`
(tool-surface §3.1) instead of a list. Reindexing deletes and reallocates the
block; ids are stable between reindexes only. There is a test asserting this.

`origin` matters at query time: `yt_auto` captions are the noisy channel
screenpipe warns about (*"avoid `q` for audio — transcriptions are noisy"*). Keeping
provenance per cue means the ranking layer can down-weight auto-captions rather
than the whole transcript leg.

`speakers.merged_into` supports cross-video identity merging without rewriting
`cues.speaker_id` — resolution follows the chain at read time. The tools for it are
deferred (tool-surface §6); the column costs nothing now and retrofitting a merge
column after 150,000 cues reference the old ids is a migration.

**Word-level timing storage.** whisperX's whole reason for existing is word-level
forced alignment (handoff §"Model/tooling picks"), so throwing the alignment away
defeats the model choice. Three options, and the fixture settles it:

| option | measured / estimated | verdict |
|---|---|---|
| `cue_words` table (video_id, cue_id, idx, word, start, end) | ~1.5M rows at 500 videos; est. 100–150 MB with the rowid index | Pays a row-per-word tax for a query nobody issues. Rejected. |
| **`cues.words_json` — JSON array per cue** | **measured 274 B/cue, 41.1 MB total** | Chosen. |
| Packed binary blob (int16 centiseconds + joined words) | est. ~18 MB | 2.3× smaller, unreadable in `sqlite3`, needs a codec on both sides. Not worth it at 41 MB. |

Format is `[["word", start, end], ...]` with 2-decimal seconds. **The invariant that
makes it useful:** `cues.text` is exactly `' '.join(w[0] for w in words_json)`. So
FTS5 `highlight()` gives a character span in `cues.text`, and a character span maps
to a word index by walking the same list — O(words-in-one-cue), one row loaded. No
second index, no join. (FTS5 has no SQL-level `offsets()`; FTS3/4's is gone and
FTS5's instance API is C-only. Computing spans caller-side is the same move
screenpipe makes for `text_positions`, deep-dive §2.6.)

It is also *droppable*: `KEEP_WORD_TIMINGS=0` writes NULL and costs 10% of the
database. That fork is question 2 in §7.

### 1.5 `chunks` — the embedding unit

Cues are 1–3 seconds; embedding one is embedding a fragment. The vector leg runs
over overlapping windows of cues.

```sql
CREATE TABLE chunks (
  id           INTEGER PRIMARY KEY,
  video_id     INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
  seq          INTEGER NOT NULL,
  start_s      REAL    NOT NULL,
  end_s        REAL    NOT NULL,
  first_cue_id INTEGER NOT NULL REFERENCES cues(id) ON DELETE CASCADE,
  last_cue_id  INTEGER NOT NULL REFERENCES cues(id) ON DELETE CASCADE,
  text         TEXT    NOT NULL,
  n_chars      INTEGER NOT NULL,
  UNIQUE (video_id, seq)
) STRICT;
CREATE INDEX chunks_time ON chunks(video_id, start_s);
CREATE INDEX chunks_span ON chunks(first_cue_id, last_cue_id);
```

45 s target, 15 s overlap (both in `config`) → ~37 chunks per 20-minute video,
18,500 at 500 videos. `chunks_span` is what turns a vector hit back into cues:
`cues.id BETWEEN first_cue_id AND last_cue_id`, which is exact because of the
contiguity invariant in §1.4.

`chunks.text` is 15 MB of duplication (it is a concatenation of `cues.text`) and
could be reconstructed on demand. Kept, because it is the exact string that was
embedded — regenerating it after a chunker change would silently diverge from what
the vectors mean. Cheap provenance, same argument as `chapters_json`.

Chunks are **not** FTS-indexed. Cues are the text-search granularity; indexing both
would double-count every word in BM25 and put overlapping text in the index twice.

### 1.6 `keyframes`

```sql
CREATE TABLE keyframes (
  id           INTEGER PRIMARY KEY,
  video_id     INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
  ord          INTEGER NOT NULL,          -- 0-based within the video -> wire frame_id
  t_s          REAL    NOT NULL,
  shot_id      INTEGER NOT NULL,          -- PySceneDetect shot ordinal
  shot_start_s REAL    NOT NULL,
  shot_end_s   REAL    NOT NULL,
  phash        INTEGER NOT NULL,          -- 64-bit dct hash, stored signed
  sharpness    REAL    NOT NULL,          -- Laplacian variance
  width        INTEGER NOT NULL,
  height       INTEGER NOT NULL,
  jpeg_path    TEXT    NOT NULL,          -- relative to $VIDTHEQUE_DATA
  jpeg_bytes   INTEGER NOT NULL,
  dup_of       INTEGER REFERENCES keyframes(id) ON DELETE SET NULL,
  ocr_state    TEXT    NOT NULL DEFAULT 'pending'
               CHECK (ocr_state IN ('pending','done','empty','failed','skipped')),
  UNIQUE (video_id, ord),
  UNIQUE (video_id, t_s)
) STRICT;
CREATE INDEX keyframes_time ON keyframes(video_id, t_s);
CREATE INDEX keyframes_shot ON keyframes(video_id, shot_id);
CREATE INDEX keyframes_live ON keyframes(video_id, t_s) WHERE dup_of IS NULL;
```

- `ord` is the ordinal in `frame_id = <public_id>-<ord:05d>` (tool-surface §3.1).
  It is a column, not a computed rank, so `/frames/kCc8FmEb1nY-00412.jpg` is a
  single indexed lookup and a fabricated ordinal produces a clean `E_UNKNOWN_FRAME`
  naming the valid range (`SELECT MAX(ord)`).
- **`phash` is stored signed.** SQLite integers are signed 64-bit; a raw
  `random.getrandbits(64)` overflows. Convert on both sides:
  `struct.unpack('<q', struct.pack('<Q', h))[0]`. Getting this wrong is an
  `OverflowError` at insert, which is at least loud.
- **The column is 64-bit; the dedup that runs at index time is not.**
  `phash(hash_size=8)` is 64 bits off the top-left 8×8 of the DCT, and research
  §4.4 is emphatic that this is too narrow for slide decks — two distinct slides
  frequently hash identically at that width, so clustering there silently drops
  content. The pipeline therefore computes both: a `hash_size=16` (256-bit) hash
  clusters near-duplicates in memory during the keyframe stage
  (`VIDTHEQUE_PHASH_THRESHOLD`, default 24 bits of Hamming distance), and the
  64-bit one is what lands in this INTEGER column, for the "find frames that look
  like this one" query over an already-capped candidate set. Widening the column
  would be a schema change for a query nobody issues yet.
- Hamming distance has no SQLite builtin. Register a Python UDF
  (`conn.create_function("phash_hamming", 2, ..., deterministic=True)`) and only
  ever apply it to an already-capped candidate set — never as a table-scan
  predicate. Near-duplicate collapse at index time happens in the pipeline, in
  Python; `phash` is stored for "find frames that look like this one" later.
- `ocr_state` distinguishes the four ways a frame ends up without text: `empty` is
  a frame that was read and had none, `skipped` is a near-duplicate that was never
  sent (`dup_of`), `failed` is a frame that was sent and did not come back —
  either the whole batch failed, or the worker refused this one image as
  undecodable. Only `failed` is retried: the OCR stage claims `pending` and
  `failed` frames, so `empty` is never re-read and a corrupt frame is never
  recorded as a slide with nothing written on it.
- `dup_of` keeps near-duplicates rather than deleting them: the row is provenance
  (this shot really did appear again at 14:22) and `keyframes_live` is a partial
  index so every query path that wants distinct visuals gets one for free.
- `sharpness` is retained after the sharpest-per-shot selection because it is the
  tie-breaker when `get-frames` has to choose among frames in a span, and because
  it is the diagnostic when keyframe quality is bad (scene-cut frames are blurry —
  the Katna lesson, survey §3).

### 1.7 `ocr_lines`

```sql
CREATE TABLE ocr_lines (
  id          INTEGER PRIMARY KEY,
  keyframe_id INTEGER NOT NULL REFERENCES keyframes(id) ON DELETE CASCADE,
  video_id    INTEGER NOT NULL REFERENCES videos(id)    ON DELETE CASCADE,
  t_s         REAL    NOT NULL,
  line_no     INTEGER NOT NULL,
  text        TEXT    NOT NULL,
  conf        REAL,
  x0 REAL NOT NULL, y0 REAL NOT NULL, x1 REAL NOT NULL, y1 REAL NOT NULL,
  poly_json   TEXT,
  UNIQUE (keyframe_id, line_no)
) STRICT;
CREATE INDEX ocr_time  ON ocr_lines(video_id, t_s);
CREATE INDEX ocr_frame ON ocr_lines(keyframe_id);
```

`video_id` and `t_s` are **deliberately denormalized** from `keyframes`. Every
OCR query path filters by video, orders by time, or applies the per-video diversity
cap; carrying them here means the candidate CTE never joins `keyframes` at all.
Two REALs and an INTEGER per row (measured: `ocr_lines` 21.8 MB at 200,000 rows,
of which the denormalization is ~3 MB) to remove a join from the hot path. They are
maintained by the pipeline in the same transaction that writes the keyframe; they
are never updated afterwards because a keyframe's video and timestamp never change.

RapidOCR returns a 4-point polygon. The axis-aligned normalized box (`x0..y1`,
0–1) is what any consumer actually uses — layout reasoning, "is this the title
line", drawing a box on a thumbnail. Normalization to 0–1 happens on this side, at
insert, from the keyframe's own width and height — stored normalized, the row
survives a re-encode at another resolution.

`poly_json` holds the original quad for rotated text. **In v1 it is always NULL:**
the worker's `OCRItemOut` answers with an axis-aligned `bbox` only, so there is no
quad to carry across the HTTP seam. The column stays for the day the worker returns
one; nothing reads it meanwhile.

### 1.7b `ocr_frames` — the searchable unit of on-screen text

```sql
CREATE TABLE ocr_frames (
  keyframe_id INTEGER PRIMARY KEY REFERENCES keyframes(id) ON DELETE CASCADE,
  video_id    INTEGER NOT NULL   REFERENCES videos(id)     ON DELETE CASCADE,
  t_s         REAL    NOT NULL,
  text        TEXT    NOT NULL
) STRICT;
CREATE INDEX ocr_frames_time ON ocr_frames(video_id, t_s);
```

One row per keyframe: that frame's `ocr_lines`, in `line_no` (reading) order,
joined with ` | ` — exactly `group_concat(text, ' | ' ORDER BY line_no)`, the same
string `get-frames`, `video-summary` and `get-segment-context` already print. It
exists because **OCR search is frame-granular** (§2.5): a line is too small a
document to hold a multi-term query. `ocr_lines` is unchanged and stays the truth
— boxes, confidences, per-line order, and every display path read it; this table
is its concatenation, materialized so FTS5 has a content table to point at.

`video_id`/`t_s` are denormalized for the same reason as §1.7's: the OCR
candidate CTE filters by video and bounds by time and must not join `keyframes`
to do it. Written by `pipeline/store.py::write_ocr` in the same transaction as
the lines (delete-then-insert, never `INSERT OR REPLACE` — REPLACE fires the
delete trigger only under `recursive_triggers`, which is off, and a missed
`'delete'` leaves postings for text that is no longer on screen).

### 1.8 Tags and collections

Two different things that get conflated. **Tags are labels** (`topic:attention`);
**collections are curated or synced sets** (a channel subscription, a playlist, "my
2026 reading list"). Same many-to-many shape, different lifecycle.

```sql
CREATE TABLE tags (
  id         INTEGER PRIMARY KEY,
  ns         TEXT NOT NULL
             CHECK (ns IN ('topic','person','project','source','lang','series')),
  name       TEXT NOT NULL
             CHECK (length(name) BETWEEN 1 AND 64
                    AND name GLOB '[a-z0-9]*'
                    AND NOT name GLOB '*[^a-z0-9._-]*'),
  full       TEXT GENERATED ALWAYS AS (ns || ':' || name) VIRTUAL,
  created_at INTEGER NOT NULL DEFAULT (unixepoch()),
  UNIQUE (ns, name)
) STRICT;
CREATE INDEX tags_full ON tags(full);

CREATE TABLE video_tags (
  video_id   INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
  tag_id     INTEGER NOT NULL REFERENCES tags(id)   ON DELETE CASCADE,
  origin     TEXT    NOT NULL DEFAULT 'manual'
             CHECK (origin IN ('manual','auto','import')),
  created_at INTEGER NOT NULL DEFAULT (unixepoch()),
  PRIMARY KEY (video_id, tag_id)
) WITHOUT ROWID, STRICT;
CREATE INDEX video_tags_by_tag ON video_tags(tag_id, video_id);
```

The `CHECK` constraints re-implement tool-surface §3.7's validation regex
(`^[a-z0-9]+:[a-z0-9][a-z0-9._-]{0,63}$`) in the schema. Duplicated validation is
usually a smell — screenpipe's live "filter logic written twice" bug is in this
document's research notes as a warning. It is justified here because the two copies
have different jobs: the API copy produces a *helpful typed error*, the schema copy
is the guarantee that no code path (a migration, a manual `sqlite3` session, a
future import tool) can create the `topic:x` / `Topic:X` / `topics:x` triplicates
that open namespaces produce within a week. The API validator is tested against the
schema constraint so they cannot diverge in meaning.

`video_tags` is `WITHOUT ROWID` (the PK *is* the row) with a reverse index, so both
"tags of this video" and "videos with this tag" are index-only scans.

**Tags apply to videos, not segments.** A segment-level tag would need a durable
segment identity, and tool-surface §3.1 deliberately refuses to mint one. Question 5
in §7.

```sql
CREATE TABLE collections (
  id           INTEGER PRIMARY KEY,
  slug         TEXT NOT NULL UNIQUE,
  title        TEXT NOT NULL,
  description  TEXT,
  kind         TEXT NOT NULL DEFAULT 'manual'
               CHECK (kind IN ('manual','channel','playlist')),
  source_url   TEXT,
  sync_cron    TEXT,
  last_sync_at INTEGER,
  created_at   INTEGER NOT NULL DEFAULT (unixepoch())
) STRICT;

CREATE TABLE collection_videos (
  collection_id INTEGER NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
  video_id      INTEGER NOT NULL REFERENCES videos(id)      ON DELETE CASCADE,
  position      INTEGER,
  added_at      INTEGER NOT NULL DEFAULT (unixepoch()),
  PRIMARY KEY (collection_id, video_id)
) WITHOUT ROWID, STRICT;
CREATE INDEX collection_videos_by_video ON collection_videos(video_id);
```

Subscriptions are deferred from the tool surface (§6) but the *storage* is here now
— `kind='channel' + source_url + sync_cron` is the whole feature minus a cron entry,
and adding it later to a populated database is a migration for no reason.

**Amended 2026-08-15 — following channels (migration 0006).** They are no longer
deferred (`following.md`; tool-surface §4.10), and the paragraph above held up:
0006 adds no new top-level concept, only the rules beside a `collections` row and
a ledger of what those rules decided.

```sql
CREATE TABLE follows (
  collection_id    INTEGER PRIMARY KEY REFERENCES collections(id) ON DELETE CASCADE,
  state            TEXT    NOT NULL DEFAULT 'active'
                   CHECK (state IN ('active','paused','failing')),
  tabs             TEXT    NOT NULL DEFAULT 'videos',   -- subset of videos,streams,shorts
  min_duration_s   INTEGER CHECK (min_duration_s IS NULL OR min_duration_s >= 0),
  max_duration_s   INTEGER CHECK (max_duration_s IS NULL OR max_duration_s >= 0),
  title_include    TEXT,                                -- comma-separated substrings
  title_exclude    TEXT,                                -- exclude wins
  channels         TEXT    NOT NULL DEFAULT 'all',      -- `index-video`'s vocabulary
  tags             TEXT,
  backfill         INTEGER NOT NULL DEFAULT 0  CHECK (backfill BETWEEN 0 AND 25),
  max_per_check    INTEGER NOT NULL DEFAULT 5  CHECK (max_per_check BETWEEN 1 AND 25),
  mode             TEXT    NOT NULL DEFAULT 'auto' CHECK (mode IN ('auto','review')),
  check_interval_s INTEGER NOT NULL DEFAULT 21600 CHECK (check_interval_s >= 900),
  next_check_at    INTEGER NOT NULL DEFAULT 0,
  last_new_at      INTEGER,
  last_error_code  TEXT,
  last_error_message TEXT,
  created_at       INTEGER NOT NULL DEFAULT (unixepoch()),
  updated_at       INTEGER NOT NULL DEFAULT (unixepoch())
) STRICT;
CREATE INDEX follows_due ON follows(next_check_at) WHERE state = 'active';

CREATE TABLE follow_seen (
  id            INTEGER PRIMARY KEY,
  collection_id INTEGER NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
  source_id     TEXT    NOT NULL,
  url           TEXT    NOT NULL,
  title         TEXT,
  duration_s    REAL,
  published_at  INTEGER,
  tab           TEXT,
  decision      TEXT    NOT NULL
                CHECK (decision IN ('queued','held_budget','held_review',
                                    'skipped_tab','skipped_title','skipped_duration',
                                    'skipped_horizon','already_indexed','failed')),
  reason        TEXT,
  judged_from   TEXT    NOT NULL DEFAULT 'listing'
                CHECK (judged_from IN ('listing','probe')),
  video_id      INTEGER REFERENCES videos(id) ON DELETE SET NULL,
  job_id        INTEGER REFERENCES jobs(id)   ON DELETE SET NULL,
  first_seen_at INTEGER NOT NULL DEFAULT (unixepoch()),
  decided_at    INTEGER NOT NULL DEFAULT (unixepoch()),
  UNIQUE (collection_id, source_id)
) STRICT;
CREATE INDEX follow_seen_recent ON follow_seen(collection_id, decided_at DESC);
CREATE INDEX follow_seen_budget ON follow_seen(decided_at) WHERE decision = 'queued';

CREATE TABLE follow_spend (
  id            INTEGER PRIMARY KEY,
  collection_id INTEGER REFERENCES collections(id) ON DELETE SET NULL,
  source_id     TEXT    NOT NULL,
  duration_s    REAL    NOT NULL,
  spent_at      INTEGER NOT NULL DEFAULT (unixepoch())
) STRICT;
CREATE INDEX follow_spend_window ON follow_spend(spent_at);
```

`follows` is **keyed by `collection_id`**, not by an id of its own: the follow *is*
the collection, and two names for one number is the drift the `CHECK` constraints
above spend their existence preventing. `owner_id` is not repeated either —
`collections` carries it and this row cannot outlive its parent. A `manual`
collection simply has no row here.

**`sync_cron` stays NULL and unused, and it is reserved rather than forgotten.**
The interval lives in `follows.check_interval_s`. A cron expression is a config
language and the dashboard is deliberately not a config editor (dashboard.md §1,
non-goal 4) — "every 6 hours" typed as `0 */6 * * *` is a worse control and a
parser nobody asked for. The column is left in place because a literal cron may
still earn its place later. `collections.last_sync_at` remains the last-check
stamp and is not duplicated into `follows`; `next_check_at` is new only because
the enqueue path needs something to sort on, and it is an absolute epoch rather
than an interval so a paused follow that resumes does not silently owe six hours.

**`follow_seen` is not redundant with `collection_videos`.** A candidate that was
passed over never becomes a `videos` row, so there is no id for
`collection_videos` to hold and no way to ask what a rule cost you. `decision` is
therefore a vocabulary about work that was *never accepted*, which is why it does
not collide with the four in §1.9 and §1.2 — the argument in full is
`following.md` §6.1. `reason` carries the number that made the call (`4:12,
shorter than your 8:00 floor`), `judged_from` says whether the duration came from
the flat listing or cost a probe, and `duration_s` NULL means neither ever said —
it is not zero, and a rule with a floor must not treat it as one.

`UNIQUE (collection_id, source_id)` is what stops a check reconsidering the same
upload every six hours for a year. Rows are upserted, and the one re-decision that
matters is `held_budget → queued` when the daily window frees; `first_seen_at`
survives that and `decided_at` does not. `follow_seen_budget` is partial because
`queued` is the only decision it counts — it served the daily budget until 0007
and now serves the *brought in* totals the Following page and `corpus-summary`
print.

**`follow_spend` exists so the daily budget cannot be refunded** (migration
0007, following.md §5). The budget was a `SUM(duration_s)` over `follow_seen`
rows decided inside a rolling day, and `collections` cascades to `follow_seen` —
so unfollowing returned hours the box had already spent on download and GPU to
the window, and the day could run to roughly twice its ceiling. One row is
written per accepted candidate, in the same transaction as the `queued` ledger
row, and `collection_id` is `ON DELETE SET NULL`: an orphan here is not a
decision nobody can explain, it is an hour that really was spent by a follow
that is gone.

Rows are kept 30 days, the clock `job_events` and `ask_budget` already use, so
this box has one retention story rather than three; the budget itself reads only
the last 24 hours and the rest is what following cost last week. The prune runs
when a check is enqueued (`follows/scheduler.py`) rather than at boot, because
rows arrive on that same event — a box with no active follows adds nothing and
has nothing to delete. `source_id` is not unique: a candidate re-queued after
its video row was deleted really did cost a second download.

### 1.9 `jobs` and `job_items`

One `index-video` call can cover up to 200 videos (playlist/channel expansion,
tool-surface §4.7), so a single `jobs.video_id` column does not fit the contract.
**`jobs` is the handle the model polls; `job_items` is the per-video work.**

```sql
CREATE TABLE jobs (
  id               INTEGER PRIMARY KEY,
  public_id        TEXT    NOT NULL UNIQUE,       -- 'job_' || 12 hex
  kind             TEXT    NOT NULL
                   CHECK (kind IN ('index','reindex','delete','export','follow_check')),
  state            TEXT    NOT NULL DEFAULT 'queued'
                   CHECK (state IN ('queued','running','done','failed','cancelled')),
  priority         INTEGER NOT NULL DEFAULT 100,  -- lower runs first; 'high' = 50
  args_json        TEXT    NOT NULL DEFAULT '{}',
  n_items          INTEGER NOT NULL DEFAULT 0,
  n_done           INTEGER NOT NULL DEFAULT 0,
  n_failed         INTEGER NOT NULL DEFAULT 0,
  cancel_requested INTEGER NOT NULL DEFAULT 0 CHECK (cancel_requested IN (0,1)),
  error_code       TEXT,
  error_message    TEXT,
  not_before       INTEGER NOT NULL DEFAULT 0,
  created_at       INTEGER NOT NULL DEFAULT (unixepoch()),
  started_at       INTEGER,
  heartbeat_at     INTEGER,
  finished_at      INTEGER,
  collection_id    INTEGER REFERENCES collections(id) ON DELETE SET NULL
) STRICT;
CREATE INDEX jobs_claim  ON jobs(priority, id) WHERE state = 'queued';
CREATE INDEX jobs_live   ON jobs(heartbeat_at) WHERE state = 'running';
CREATE INDEX jobs_recent ON jobs(created_at DESC);
CREATE INDEX jobs_by_collection ON jobs(collection_id, created_at DESC)
  WHERE collection_id IS NOT NULL;

CREATE TABLE job_items (
  id            INTEGER PRIMARY KEY,
  job_id        INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
  seq           INTEGER NOT NULL,
  source_url    TEXT    NOT NULL,
  video_id      INTEGER REFERENCES videos(id) ON DELETE CASCADE,  -- NULL until resolved
  state         TEXT    NOT NULL DEFAULT 'queued'
                CHECK (state IN ('queued','running','done','failed','skipped','cancelled')),
  stage         TEXT    CHECK (stage IN ('fetch','stt','chunk','text_embed',
                                         'keyframe','ocr','frame_embed')),
  stage_pct     REAL    NOT NULL DEFAULT 0.0 CHECK (stage_pct BETWEEN 0.0 AND 1.0),
  attempts      INTEGER NOT NULL DEFAULT 0,
  max_attempts  INTEGER NOT NULL DEFAULT 3,
  error_code    TEXT,
  error_message TEXT,
  started_at    INTEGER,
  finished_at   INTEGER,
  UNIQUE (job_id, seq)
) STRICT;
CREATE INDEX job_items_by_video ON job_items(video_id);
CREATE UNIQUE INDEX job_items_one_inflight ON job_items(video_id)
  WHERE video_id IS NOT NULL AND state IN ('queued','running');

CREATE TABLE job_events (
  id        INTEGER PRIMARY KEY,
  job_id    INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
  item_id   INTEGER REFERENCES job_items(id) ON DELETE CASCADE,
  at        INTEGER NOT NULL DEFAULT (unixepoch()),
  level     TEXT    NOT NULL DEFAULT 'info'
            CHECK (level IN ('debug','info','warn','error')),
  stage     TEXT,
  message   TEXT    NOT NULL,
  data_json TEXT
) STRICT;
CREATE INDEX job_events_by_job ON job_events(job_id, id);
```

**`follow_check` and `collection_id` arrived in migration 0006** (2026-08-15,
`following.md` §3). A follow check is a `jobs` row rather than a timer beside the
queue because a source that rate-limits is an ordinary operating condition here,
and this table already owns the classification, the 90-minute cool-off, the
attempt ceiling and crash recovery. It runs at `priority = 10` — ahead of `high`
(50) — because a one-second listing request must not wait behind hours of GPU,
and it enqueues an ordinary `index` job for whatever survived.

**Adding the kind cost a 12-step table rebuild.** SQLite cannot `ALTER` a `CHECK`
constraint and `kind` carries one, so 0006 is the documented rebuild:
`jobs_new`, copy, drop, rename, recreate all four indexes. Two things are worth
knowing before the next kind is added. `job_items_roll` lives on `job_items` and
only *references* `jobs`, but SQLite resolves every trigger body when a table is
dropped — leaving it in place makes `DROP TABLE jobs` fail with *"error in
trigger job_items_roll: no such table: main.jobs"*, so dropping and recreating it
verbatim is step 4 of the rebuild and not an optional tidy. And the runner
already does the fiddly half: `migrations._apply_one` turns foreign keys off for
the connection, wraps the script in one transaction, and runs `PRAGMA
foreign_key_check` before it commits. Migrations run at boot before
`PipelineRunner.start`, so the queue is drained by construction and no claim is
in flight while the table is swapped.

`collection_id` was taken while the table was being rewritten anyway. Both the
check and the `index` job it enqueues carry it, so "which check found this video"
and "what has this follow queued" are plain indexed queries rather than a `LIKE`
against `args_json`. It is nullable and NULL for every job that has nothing to do
with a follow — which is every job that existed before 0006 — and
`ON DELETE SET NULL` means an unfollow never takes a job's history with it.

**The state machine.**

```
                     ┌──────────── cancel_requested=1 ───────────┐
                     ▼                                           │
  queued ──claim──> running ──all items terminal──> done         │
     │                 │                                         │
     │                 ├── item fails, attempts<max ──> requeue item
     │                 ├── all items failed ──────────> failed   │
     │                 └── heartbeat stale ───────────> requeue (crash recovery)
     └──────────────── cancel ────────────────────────> cancelled ┘
```

Wire vocabulary is the same vocabulary: `job-status` (tool-surface §4.8) renders
`state` verbatim and `stage` for the current item. No translation table, so no
drift.

**Claiming** is one statement — `UPDATE … RETURNING` (SQLite 3.35+), verified:

```sql
UPDATE jobs
   SET state = 'running',
       started_at = COALESCE(started_at, unixepoch()),
       heartbeat_at = unixepoch()
 WHERE id = (SELECT id FROM jobs
              WHERE state = 'queued' AND not_before <= unixepoch()
              ORDER BY priority, id LIMIT 1)
RETURNING id, public_id, kind, args_json;
```

`started_at` is **first claim, not latest claim**, and the `COALESCE` is the
whole of that promise. Re-claiming is the normal path: `defer_job` returns a
rate-limited job to the queue behind a `not_before`, so a job that meets
`E_RATE_LIMIT` twice per item across ten items is claimed ~20 times. A plain
`unixepoch()` made the field mean "most recently claimed" — an overnight job
that had been grinding for 1h32m rendered as `started` 92 minutes late in
`job-status`, i.e. as freshly started, which reads as healthy. `heartbeat_at`
does move on every claim: it is the liveness stamp the staleness sweep reads.
`job_items.started_at` takes the same `COALESCE` for the same reason one level
down — `attempts` counts the tries, `started_at` spans them. Neither needs a
reset path: a job row is never reused, so a forced reindex is a new row with
both stamps NULL.

**`max_attempts` is a per-row allowance, not the constant 3 the DEFAULT makes
it look like.** One failure class may raise it: an item that has spent its
budget on `E_RATE_LIMIT` is granted one more attempt at a time, up to a ceiling
of 6 (`_extend_for_rate_limit`, `jobs/runner.py`; each grant writes a
`job_events` warn row). The reasoning is the same "attempts counts the tries"
rule read the other way — a rate-limit deferral is a statement about the box,
not about whether this video is indexable, so charging it to the video's budget
retires videos for something they did not do. Measured, 2026-08-09: a 60-90
minute bot-check wave against a five-minute cool-off exhausted three attempts
in eleven minutes and failed items that had never reached YouTube while it was
answering (research/ytdlp-usage-audit-2026-08-10.md §1). Readers of the column
should treat it as data — the retry budget is `attempts < max_attempts`, both
read from the row, as `job-status` and the dashboard already do.

**The in-flight guard is the partial unique index**, not application logic:
`job_items_one_inflight` makes a second `index-video` on an already-queued video an
`IntegrityError` the API turns into "already indexing, here is the job id"
(verified: the duplicate insert raises `UNIQUE constraint failed:
job_items.video_id`). `NULL video_id` rows are exempt, because SQL NULLs are
distinct — which is exactly right for playlist items not yet resolved to a video.

That exemption has a sharp edge, and it drew blood: an item whose video is not
`ready` yet resolves to `NULL` at insert, so the index cannot refuse it until the
pipeline attaches the video mid-run — and that refusal was read as "duplicate
inside this expansion" and *skipped*, which is how an `index-video` that fetched
nothing reported `done`. `index-video` now resolves the video row whatever its
`index_state`, so the guard fires at insert, and it resolves a claim before
creating the job:

| claim on the video | `force_reindex=false` | `force_reindex=true` |
|---|---|---|
| live (running, heartbeat fresh) | `E_INDEXING`, names the job | `E_INDEXING`, names the job — cancel it, then retry |
| queued, or just reclaimed from a dead process | `E_INDEXING`, points at `force_reindex` | **supersedes**: the old item goes `cancelled` with `E_SUPERSEDED`, the new job runs |

Mid-run, `attach_video` distinguishes the two refusals it always could have: the
same job (a duplicate inside one expansion — `skipped`, with the reason on the
row) from another job (`E_INDEXING`, a *failed* item, because an item that
indexed nothing must never read as work done).

**Progress** is a rollup, maintained by trigger so it cannot be forgotten:

```sql
CREATE TRIGGER job_items_roll AFTER UPDATE OF state ON job_items
WHEN new.state IN ('done','failed','skipped','cancelled')
 AND old.state NOT IN ('done','failed','skipped','cancelled')
BEGIN
  UPDATE jobs SET n_done   = n_done   + (new.state = 'done'),
                  n_failed = n_failed + (new.state = 'failed')
  WHERE id = new.job_id;
END;
```

and the fractional figure blends **finished items** with how far the running one
has come through the stage list. `stage_pct` alone was a bug, found live: it
restarts at 0 on every stage, so the overall figure printed 0.5 during `fetch`
and 0.05 a second later during `stt`. An item's share is the stages behind it
plus the one it is on, over `len(STAGES)`, and every terminal state counts as a
whole item — a failed or skipped item is *finished* work, and dropping it out of
the numerator made the figure fall as the job progressed:

```sql
SELECT j.public_id, j.state,
       ROUND(MIN(1.0,
             ((SELECT COUNT(*) FROM job_items i WHERE i.job_id = j.id
                AND i.state IN ('done','failed','skipped','cancelled'))
              + COALESCE((SELECT SUM(((CASE i.stage WHEN 'fetch' THEN 0 … 
                                       WHEN 'frame_embed' THEN 6 ELSE 0 END)
                                      + i.stage_pct) / 7.0)
                            FROM job_items i
                           WHERE i.job_id = j.id AND i.state = 'running'), 0.0)
             ) * 1.0 / MAX(j.n_items, 1)), 3) AS progress
FROM jobs j WHERE j.public_id = :job_id;
```

The CASE arm is generated from `STAGES` in `jobs/store.py`, so the stage list has
one definition. The figure only ever climbs for a given item set; expansion is
the one exception, because fan-out genuinely discovers work that did not exist
when the job started.

The same query reads `n_done`/`n_failed` — and `n_skipped`/`n_cancelled` — back
off `job_items` rather than off the trigger rollup, so the four terminal counts
and `n_items` **add up** in the payload. That is what makes "done with nothing
indexed" visible in the numbers.

**Aggregation is honest about doing nothing.** All items terminal → `done`, all
failed → `failed`, and — the third branch, added after a job whose only item was
skipped reported plain `done` and `job-status` promised "everything from this
job" — no item `done` at all → still `done` (the wire vocabulary does not grow a
sixth state), but with `error_code = 'E_NOTHING_INDEXED'` and an `error_message`
naming what was skipped and why. `job-status` prints that instead of a promise.

**Cancellation is cooperative and its own column.** `cancel_requested` is set by the
API; the pipeline checks it at every stage boundary and inside the per-chunk loops.
It is not a state, because a running job that has been asked to stop is still
running until it stops, and `job-status` should say so.

**Crash recovery runs at boot _and_ before every claim.** Boot-only was the bug
behind zombie `job_5ac6f2ee2b29` (2026-08-08): the process was killed
mid-`keyframe` and restarted *inside* the staleness window, so boot saw a fresh
heartbeat, and nothing ever looked again — the job stayed `running` for good and
its claim on the video blocked every later `index-video`. `PipelineRunner`
sweeps before it claims (a cheap indexed read first; the write only when there
is something to reclaim), excluding the jobs *this* process is driving, because
an event loop blocked on a 30-minute transcription is alive, not crashed.

A stale claim is one whose `heartbeat_at` is older than `VIDTHEQUE_STALE_CLAIM_S`
(default 300 s). The runner heartbeats on a 30 s tick as well as on every
progress report, so a live job is never that quiet. Reclaiming resets all three
lies a killed process leaves behind:

- the job → `queued`, `heartbeat_at = NULL`;
- its `running` items → `queued`, stage cleared. `attempts` is **not**
  incremented — `claim_item` already counted the attempt when it handed the item
  out — and an item already at `max_attempts` is failed with `E_CRASHED` rather
  than handed out again;
- the video it was working on: `video_stages` rows still `running` → `pending`
  (the finished ones stay finished, which is what makes resume per-stage), and
  `videos.index_state` `indexing` → `stale` if it was indexed before, `pending`
  if it was not.

`job_events` is the append-only log behind `job-status`'s error display. It is the
one table with no retention pressure and no query on the hot path — capped by a
nightly `DELETE FROM job_events WHERE at < unixepoch() - 2592000`.

### 1.10 Schema version and migrations

**`PRAGMA user_version` is the authority.** It is a 32-bit integer in the database
header, read and written transactionally with the migration itself, and it needs no
table to exist before it can be read — which matters when the migration you are
about to run is "create the first table".

```sql
PRAGMA user_version;        -- current schema version
PRAGMA user_version = 7;    -- set inside the migration transaction
```

`schema_migrations` (§1.9's neighbour, DDL above) is the **audit trail**, not the
source of truth: version, name, checksum of the applied SQL, timestamp. If
`user_version` and the max applied row disagree, that is a hard boot error — someone
edited the file by hand.

The runner:

1. Migrations are numbered files,
   `mcp/src/vidtheque_mcp/db/migrations/0007_add_video_links.sql`, applied in order,
   **one transaction each**, no runtime branching.

   **`BEGIN IMMEDIATE` has to live inside the script.** A migration is multi-statement,
   so it runs through `sqlite3.Connection.executescript()` — and `executescript()`
   *commits any pending transaction before it starts*. A `BEGIN` issued from Python
   around the call is therefore silently closed, every statement in the file
   autocommits, and a migration that fails halfway leaves the file half-migrated with
   `user_version` still on the old number. The runner prepends the statement to the
   script text (`conn.executescript("BEGIN IMMEDIATE;\n" + sql)`) and issues the
   matching `COMMIT`/`ROLLBACK` afterwards, which keeps "one transaction each" true.
   Every migration file is written on that assumption: none of them opens or closes a
   transaction of its own.
2. `PRAGMA foreign_keys=OFF` for the duration of any migration that rebuilds a table
   (SQLite's documented 12-step ALTER procedure), then `PRAGMA foreign_key_check`
   before commit. FK enforcement is per-connection, so this is scoped to the
   migration connection.
3. **Derived tables are rebuilt, never migrated.** Changing the FTS tokenizer or the
   vec dimension is `DROP` + `CREATE` + `INSERT INTO cues_fts(cues_fts)
   VALUES('rebuild')` — measured at **0.8 s for 149,700 cues**. Vectors cannot be
   rebuilt from SQLite alone (they need the GPU worker), so a dimension change
   leaves the two vec tables empty and the corpus mid-backfill.

   **Corrected by 0004: the vector legs stay *enabled* through that window, and
   the window is reported instead.** The original clause said the §1.1 boot
   assertion "keeps the vector legs disabled until it finishes", and that is
   unimplementable as written — `_stage_text_embed` and `_stage_frame_embed`
   both skip themselves when `db.vectors.enabled` is false, so disabling the
   legs latches the backfill off forever and "until it finishes" never arrives
   (`research/multimodal-embedding-2026-08-09.md` §5.4). The two states are
   different and only one of them is dangerous:

   * **Incoherent** — `config` and the DDL disagree, or the worker answers in
     another model or width. Mixing spaces is *possible*, so writes are refused
     and reads go FTS-only with a `note:`. Unchanged.
   * **Coherent but not yet filled** — everything agrees, the vectors simply do
     not exist yet. Nothing can be mixed; the legs are safe to run and the
     backfill *must* be able to run. A migration that changes DDL and `config`
     in one transaction only ever produces this state.

   So the migration sets the two embed stages from `done` to `pending` — which
   is both the re-run trigger `_should_run` already honours and the honest thing
   to read, since `coverage`/`has_frames`/`data_status` derive from `done` and a
   stale `done` row would claim search the corpus cannot answer — and the
   degraded window is *named*: `queries.embed_backlog` counts videos whose
   chunks or keyframes are `done` while their embed stage is `pending`,
   `corpus-summary` reports `data_status: degraded` with the reason, and every
   search that uses a vector leg prints a `note:` saying how many videos are
   waiting and that keyword matching covered them. `skipped` is untouched: it
   records a deliberate choice, not an omission.

   Keeping the old tables serving until the new ones fill was considered and
   rejected. It means two vec tables per leg at two widths and a query layer
   that unions them — fusing ranks from two embedding spaces into one RRF, which
   is exactly the "plausible-looking garbage that no test catches" §1.1 exists
   to prevent. The degraded window is minutes and it is reported; a mixed space
   is permanent and silent.

   One consequence for the tooling: an invalidated video has to look like a
   *resume*, not a rebuild. `index-video` short-circuits anything `ready` with
   no outstanding stage, and `force_reindex=true` re-downloads the mp4 and
   re-transcribes the audio to redo two stages that need neither — so `pending`
   counts as outstanding alongside `failed` (`jobs/store.failed_stages`), and
   `index-video url="…"` with no force is the backfill command.
4. Checksums are recorded so an edited-after-the-fact migration is detected, not
   silently re-run.

**Schema version ≠ pipeline version.** `user_version` describes the *shape* of the
file; `config['pipeline.version']` describes the *semantics of its contents*.
Switching SigLIP checkpoints does not change a single column — it invalidates
40,000 vectors. Conflating them means either pointless migrations or silent staleness.

### 1.11 `ask_budget` — the one table that is not about the corpus

Added by 0005. The public demo's daily `ask` cap, on disk, because the model
behind it is paid and an in-memory counter resets on every redeploy
(demo-site.md §4.2 is the decision and the mechanism).

```sql
CREATE TABLE ask_budget (
  bucket     TEXT    NOT NULL,      -- the limiter's bucket name, e.g. 'ask_global'
  client     TEXT    NOT NULL,      -- the limiter's client key; '@global' for a server-wide one
  day        TEXT    NOT NULL,      -- UTC 'YYYY-MM-DD'
  spent      INTEGER NOT NULL DEFAULT 0 CHECK (spent >= 0),
  updated_at INTEGER NOT NULL DEFAULT (unixepoch()),
  PRIMARY KEY (bucket, client, day)
) WITHOUT ROWID, STRICT;
```

`(bucket, client)` is the rate limiter's own key, so a per-IP daily bucket would
need no migration to persist; today only `ask_global` writes here. One row per
day per bucket, pruned to the last 30 days at boot — a month of "what did the
demo cost" for a table nothing ever scans.

**It does not belong to the corpus, and it is here anyway.** The credentials
store gets its own file so a corpus rebuild never touches it (§7.2 of the auth
research); the opposite argument applies to a budget. It is not a secret, it is
one row a day, and this process already owns exactly one write connection to
this file (§5) — a second writable file would be a second thing to back up and a
second thing to forget. It does mean a `DROP` of the corpus hands the day's
budget back, which is correct: that is a new deployment.

**Access is deliberately lopsided.** Reads happen once, at boot, to seed the
in-memory counters; writes are `+1`/`-1` deltas queued from the ASGI middleware
and applied by one drain task through the single writer. Nothing on the request
path waits on this table, and nothing about a request's *outcome* depends on it —
the in-memory counter is still the gate. That is why a lost delta is survivable
and a lost row is not a correctness bug, only a cheaper day.

---

## 2. FTS5

Three external-content tables: `cues_fts`, `ocr_frames_fts`, `videos_fts`. The
OCR one indexes **frames, not lines** — §2.5 is why, and it is the one place
where the indexed document is not simply a base-table row.

### 2.1 Why external content, not standalone

Standalone FTS5 stores a second copy of every indexed string. screenpipe measured
the delete path on a 14-day database: **~38 s standalone vs ~0.09 s external, ~420×**,
plus ~175 MB saved (deep-dive §2.4). vidtheque deletes whole videos routinely
(`delete_video` jobs, reindex, retention), so the delete path is a first-class
concern, not a corner case.

The second reason is the one that keeps mattering: **external content makes the
tokenizer a reversible decision.** The base table is the truth; the index is a
projection that can be thrown away and rebuilt in under a second.

### 2.2 Tokenizer: `porter` for prose, `unicode61` for screen text

Different tokenizers per table, because transcripts and OCR are different
languages.

```sql
CREATE VIRTUAL TABLE cues_fts USING fts5(
  text,
  content='cues',
  content_rowid='id',
  tokenize="porter unicode61 remove_diacritics 2",
  prefix='2 3'
);

CREATE VIRTUAL TABLE ocr_frames_fts USING fts5(
  text,
  content='ocr_frames',
  content_rowid='keyframe_id',
  tokenize="unicode61 remove_diacritics 2 tokenchars '_-./'",
  prefix='2 3'
);

CREATE VIRTUAL TABLE videos_fts USING fts5(
  title, description, channel_name,
  content='videos',
  content_rowid='id',
  tokenize="porter unicode61 remove_diacritics 2",
  prefix='2 3'
);
```

**Transcripts get `porter`.** Measured on a fixture where three documents contain
`cache`, `caching` and `cached`:

| query | `porter unicode61` | `unicode61` |
|---|---|---|
| `cache` | **3** | 1 |
| `caching` | **3** | 1 |
| `cached` | **3** | 1 |

Spoken prose inflects, and a user asking "where does he explain caching" wants the
sentence that says "we cache the keys". screenpipe uses `unicode61` everywhere and
compensates query-side with camelCase/digit-boundary splitting and `*` prefix
OR-joins (deep-dive §2.6) — but their corpus is *screen text*, where stemming is
actively wrong. Ours is both, in two tables, so each gets the right answer instead
of one compromise.

**OCR gets `unicode61` with `tokenchars '_-./'`.** On-screen text is
`nvidia-smi`, `torch.compile`, `--tensor-parallel-size`, `n_kv_heads`,
`kv_cache.py`. Porter would stem identifiers into nonsense, and unicode61's default
separators would shred `nvidia-smi` into two tokens. Adding `_-./` to `tokenchars`
keeps them whole and searchable as written.

Caveats, stated so nobody rediscovers them:

- **`porter` is English-only** and destructive on inflected non-English text. If the
  corpus goes multilingual, this is the first thing to revisit — and revisiting it
  costs 0.8 s (§1.10). SQLite ships `unicode61`, `ascii`, `porter` and `trigram`
  only; a real snowball stemmer needs a C tokenizer registered through the FTS5 API,
  which `sqlite3` does not expose. `porter` or `unicode61` is the actual choice.
- `prefix='2 3'` prebuilds 2- and 3-character prefix indexes so `attn*` is cheap.
  It costs index size; measured `cues_fts_data` is 16.8 MB against 150,000 cues,
  which is affordable.
- **FTS5 query syntax is a syntax, and user text is not.** Verified: bare
  `nvidia-smi` and `torch.compile` are `OperationalError` at parse time (`-` is NOT,
  `.` is a column qualifier). The HTTP layer must quote-wrap user terms —
  `"nvidia-smi"` matches correctly in all three tokenizers — while preserving the
  operators we document (`AND`/`OR`/`NOT`/`"phrase"`/`prefix*`). One sanitizer,
  tested against a corpus of hostile queries, in the same place as the time
  normalizer.

### 2.3 The guarded triggers

Six triggers, one shape. The guard is `WHEN new.text <> ''`: an empty document adds
nothing to the index but still costs a row, and an unguarded delete of a row that
was never indexed corrupts the index's internal counts.

```sql
CREATE TRIGGER cues_ai AFTER INSERT ON cues WHEN new.text <> '' BEGIN
  INSERT INTO cues_fts(rowid, text) VALUES (new.id, new.text);
END;

CREATE TRIGGER cues_ad AFTER DELETE ON cues WHEN old.text <> '' BEGIN
  INSERT INTO cues_fts(cues_fts, rowid, text) VALUES ('delete', old.id, old.text);
END;

CREATE TRIGGER cues_au AFTER UPDATE OF text ON cues BEGIN
  INSERT INTO cues_fts(cues_fts, rowid, text)
    SELECT 'delete', old.id, old.text WHERE old.text <> '';
  INSERT INTO cues_fts(rowid, text)
    SELECT new.id, new.text WHERE new.text <> '';
END;
```

`ocr_frames`/`ocr_frames_fts` and `videos`/`videos_fts` follow identically
(`ocr_frames`'s rowid is `keyframe_id`; `videos_fts` carries three columns and
`coalesce(…,'')` on the nullable ones).

Three things that are easy to get wrong:

- **The `'delete'` command must be given the OLD values, exactly.** External-content
  FTS5 does not store the document; it re-tokenizes what you hand it to find the
  postings to remove. Pass the new text, or a `SELECT` that reads the already-deleted
  row, and the index silently rots. This is why the delete trigger reads `old.`
  columns and nothing else.
- **The UPDATE trigger cannot use the `WHEN` guard**, because the two branches need
  different conditions (old non-empty for the delete, new non-empty for the insert).
  Hence `INSERT … SELECT … WHERE`.
- **`ON DELETE CASCADE` does fire these triggers.** Verified on the fixture:
  deleting one `videos` row cascaded to 300 cues and left `cues` and
  `cues_fts_docsize` at identical counts. This is the load-bearing fact behind the
  whole delete story — if it were false, every cascade would leave orphaned postings.
  It is also what makes `ocr_frames` safe: the delete path is `videos` →
  `keyframes` → `ocr_frames` → trigger, and nothing in the pipeline has to
  remember to clean the frame index. **`INSERT OR REPLACE` does *not* fire the
  delete trigger** unless `PRAGMA recursive_triggers` is on, which it is not —
  so every rewrite of a frame's OCR is an explicit `DELETE` then `INSERT`.

**Count the shadow table, not the index.** `SELECT COUNT(*) FROM cues_fts` reads
straight through to `cues` — that is what `content='cues'` *means*, and it is the
same number whether the index is in sync, empty, or rotted, so it asserts nothing.
The index's own row count lives in `cues_fts_docsize`, and that is the one to
compare against the content table. It is also the count that shows the `WHEN
new.text <> ''` guard working: a corpus with one empty cue has one more row in
`cues` than in `cues_fts_docsize`, on purpose. (`ocr_frames_fts_docsize` and
`videos_fts_docsize` likewise.)

`INSERT INTO cues_fts(cues_fts) VALUES('integrity-check')` — which does inspect the
index proper — runs in the test suite after every destructive-path test.

### 2.4 Maintenance

`'optimize'` measured at 0.1 s over the fixture — cheap enough to run after each
video's indexing transaction rather than on a schedule. Follow screenpipe's other
half too: `PRAGMA optimize` on the periodic WAL checkpoint tick (§5.1).

### 2.5 The OCR document is a frame, not a line

*(Migration 0003. Until then the OCR index was `ocr_fts`, `content='ocr_lines'`.)*

**A line is too small a document to hold a query.** The OCR leg ANDs its terms
(§4.6, `expand_prefix_fts`), and a keyframe is about a dozen lines — measured on
the live corpus, **41,138 OCR lines across 3,460 keyframes**. So a slide titled
"Vector databases" with "…for retrieval augmented generation" in a bullet
answered `vector retrieval` with **silence**: the terms were both on screen,
both indexed, and no document contained both. No `note:`, no way to ask for it
differently — a pure recall hole, in exactly the slide-shaped content OCR is
best at. (Backlog #20; the fixture had hidden it, because its "multi-line" frame
was a single line containing ` | `.)

The unit of OCR search is therefore the **frame**: `ocr_frames` (§1.7b) holds one
document per keyframe and `ocr_frames_fts` indexes it, same tokenizer as the line
index it replaced. The terms only have to share a frame — which is also the thing
the result cites (`frame_id` + `t_s`), so the granularity of matching and the
granularity of citation finally agree.

**Why not keep both indexes.** Two indexes over the same text means two write
paths to keep honest, ~14 MB (§3.4) for the smaller one, and one of them still
answering `vector retrieval` with nothing. Derived tables are rebuilt, never
migrated (§1.10): going back is a `DROP`, a `CREATE` and a sub-second rebuild.

**Why a materialized table rather than standalone FTS5 over the concatenation.**
§2.1's delete-path measurement (~420×) is the whole reason the other indexes are
external-content, and it applies here more strongly, not less — one document per
frame is ~12× fewer `'delete'` commands per cascaded video than the line index
cost. The duplicated text is paid for by dropping `ocr_fts`.

Three consequences, all deliberate:

- **A phrase wrapped across two lines now matches.** ` | ` tokenizes to nothing
  under `unicode61`, so the last token of one line is positionally adjacent to
  the first of the next: `"retrieval augmented"` finds the slide that breaks the
  title across two lines. The same mechanism means a phrase query *can* match a
  coincidental line boundary; on slide text that trade is strongly positive.
- **A term repeated down a slide is one result, not five.** The line index spent
  five of `max_per_video` on one frame at one timestamp.
- **`min_chars`/`max_chars` on the OCR leg measure the frame's whole on-screen
  text**, because that is what the document is. "Short" now means a sparse
  slide, not a short line.

**Citations keep the line-level detail available.** A hit shows FTS5's own
`snippet()` over the frame document — the window that holds the terms, ` | `
separators intact — rather than the whole slide, because middle-truncating a
full slide to `max_text_chars` is as likely to cut the match out as to keep it.
`max_text_chars=0` (the documented opt-out) still returns the whole frame, and
`ocr_lines` still has every line with its box for anything that wants to draw
one. The snippet window is 64 tokens (FTS5's own maximum) and is computed for
the page's rows only — one rowid-restricted lookup each, never for the candidate
set: measured **145 ms vs 1,068 ms** on a 200,000-frame fixture where every frame
matched.

---

## 3. Vectors — `sqlite-vec`

`sqlite-vec` is at `0.1.9`, alpha, brute-force KNN, no ANN index (survey §3). At
personal scale that is a fine trade and the measurements below support it. The
escape hatch is **Vec1**, the SQLite project's own ANN extension; §3.5 says what
"switch to it" would actually cost.

### 3.1 The tables

```sql
CREATE VIRTUAL TABLE vec_chunks USING vec0(
  chunk_id  INTEGER PRIMARY KEY,
  video_id  INTEGER,                 -- metadata column, NOT a partition key (§3.2)
  start_s   FLOAT,
  embedding FLOAT[2048] distance_metric=cosine,
  chunk_size=256
);

CREATE VIRTUAL TABLE vec_frames USING vec0(
  keyframe_id INTEGER PRIMARY KEY,
  video_id    INTEGER,
  t_s         FLOAT,
  embedding   FLOAT[2048] distance_metric=cosine,
  chunk_size=256
);
```

2048 = `Qwen/Qwen3-VL-Embedding-2B`, native, on **both** tables — the two legs
share one space (§1.1). Before 0004 they were 1024 (`Qwen3-Embedding-0.6B`) and
1152 (SigLIP 2 SO400M NaFlex). Both dimensions are still duplicated in `config`
and asserted equal at boot (§1.1) — the declared dimension lives in the DDL
where the query planner needs it, and in `config` where the *indexer* needs it,
and a schema that can only be read by parsing `sqlite_master` is not a schema
the pipeline should be parsing.

Native width, not the memo's MRL@1024: truncation is the fallback if query
latency or storage argues for it, not the starting point, and MRL makes it a
config change plus a ~12-minute re-embed later (`EMBED_DIM` on the worker,
`*_embed.dim` here, and a rebuild of these two tables).

**A `vec0` dimension is a literal in DDL, and there is no conditional CREATE.**
That is why 0004 rebuilds both tables unconditionally while guarding its
`config` rewrites on the shipped values: a corpus that deliberately kept another
embedder lands in the boot assertion's mismatch state — writes refused, reads
FTS-only, both numbers named — which is loud and recoverable. Staying on your
own embedder means setting `*_embed.dim` to its width and re-creating the
matching table at that width; the vectors were invalidated by the width change
either way. Both old worker backends remain selectable; only the default moved.

Vectors are written **L2-normalized**, so cosine distance is a dot product and
`config['text_embed.normalized']=1` is a promise the pipeline keeps.

**`INSERT OR REPLACE` does not work here.** It parses, and it appeared to work
for the life of the project because every write the pipeline made was the
*first* write for that id. The second is `OperationalError: UNIQUE constraint
failed on vec_chunks primary key`: the conflict resolution never reaches vec0's
shadow tables. Nothing hit it until an embedding-model change made re-embedding
an already-embedded video a normal operation — it would have failed the
`text_embed` stage of every video on the second pass, with a message that reads
like corruption. `pipeline/store.py` deletes by id and then inserts, in the same
transaction, so a batch is still all-or-nothing.

### 3.2 `PARTITION KEY` is a trap at this scale — measured

The obvious modelling choice is `video_id INTEGER PARTITION KEY`, so a per-video
search scans only that video. Two measurements say no.

**Storage.** vec0 allocates vectors in chunks of `chunk_size` rows (default 1024)
and **allocates a full chunk per partition**. 50 partitions × 20 rows × 128-d:

| table shape | raw vectors | file size | ratio |
|---|---|---|---|
| `PARTITION KEY`, default `chunk_size` | 0.51 MB | **26.73 MB** | **52.2×** |
| `PARTITION KEY`, `chunk_size=8` | 0.51 MB | 0.75 MB | 1.46× |
| plain metadata column, default | 0.51 MB | 0.59 MB | 1.16× |
| plain metadata column, `chunk_size=8` | 0.51 MB | 0.66 MB | 1.28× |

At vidtheque's real shape — 500 partitions, 1152-d frames — a partitioned table
with the default chunk size would preallocate 500 × 1024 × 1152 × 4 B ≈ **2.4 GB**
to hold 184 MB of vectors.

**Semantics.** `k` is applied **per partition**, not globally. Verified: `k=10`
with no filter returns 10 rows; `k=10 AND video_id IN (1,2)` returns **20**;
`IN (1,2,3)` returns **30**. Every caller would have to re-sort and re-cap in an
outer query, and any that forgot would quietly return a mixed-scale result set.
With a plain metadata column, `k=5` returns 5 rows filtered or not.

So: **plain metadata column, `chunk_size=256`.** The cost is that a video-scoped
frame search still scans everything — measured 41.3 ms scoped vs 72.0 ms global,
so the filter prunes results, not work. That is acceptable while global KNN is
72 ms. If per-video frame search becomes a hot path, the fix is `PARTITION KEY`
**with an explicit `chunk_size`**, plus outer-query re-capping, and it is a rebuild
of a derived table (§1.10), not a migration.

### 3.3 Keeping vec tables in sync

**vec0 virtual tables are not reachable by foreign keys.** Deleting a video
cascades through `chunks` and `keyframes` and leaves the vectors behind — verified
on the fixture, where `vec_chunks` sat unchanged at 185 rows after a cascade that
removed 37 chunks. Two triggers close it:

```sql
CREATE TRIGGER chunks_ad AFTER DELETE ON chunks BEGIN
  DELETE FROM vec_chunks WHERE chunk_id = old.id;
END;

CREATE TRIGGER keyframes_ad AFTER DELETE ON keyframes BEGIN
  DELETE FROM vec_frames WHERE keyframe_id = old.id;
END;
```

With them, the same cascade took `vec_chunks` 185 → 148 and `vec_frames` 300 → 240.
A `PRAGMA integrity_check`-equivalent for this is a cheap consistency test that
belongs in CI:

```sql
SELECT (SELECT count(*) FROM chunks)    - (SELECT count(*) FROM vec_chunks) AS chunk_drift,
       (SELECT count(*) FROM keyframes) - (SELECT count(*) FROM vec_frames) AS frame_drift;
```

(Non-zero is legitimate only while a `text_embed`/`frame_embed` stage is mid-run.)

**Correction, 2026-08-11 — `frame_drift` as written is non-zero on a healthy,
idle corpus, and always will be.** Duplicate keyframes (`dup_of IS NOT NULL`)
are stored but never embedded, so the live corpus sits at **12,225 keyframes
against 8,429 `vec_frames`** with every `frame_embed` stage `done` — a standing
drift of ~3,800 that means nothing. The equivalence that actually holds is

```sql
SELECT (SELECT count(*) FROM chunks) - (SELECT count(*) FROM vec_chunks) AS chunk_drift,
       (SELECT count(*) FROM keyframes WHERE dup_of IS NULL)
         - (SELECT count(*) FROM vec_frames)                             AS frame_drift;
```

— measured exact (8,429 = 8,429). And for checking a *delete* rather than an
embed, the sharper question is whether any vector outlived its row, which the
subtraction cannot see at all:

```sql
SELECT (SELECT COUNT(*) FROM vec_chunks WHERE chunk_id    NOT IN (SELECT id FROM chunks)),
       (SELECT COUNT(*) FROM vec_frames WHERE keyframe_id NOT IN (SELECT id FROM keyframes));
```

Both 0 after a rehearsed video deletion on a copy of the live database.
`docs/takedown.md` §2.6 uses the second form for exactly that reason.

### 3.4 Storage and latency at 500 videos — measured

Fixture: 500 videos × 20 min, 150,000 cues, 18,500 chunks, 40,000 keyframes,
200,000 OCR lines. **Total database file: 420.5 MB.**

| object | MB | share |
|---|---|---|
| `vec_frames` vectors (40,000 × 1152 × f32) | 185.4 | 44% |
| `vec_chunks` vectors (18,500 × 1024 × f32) | 76.7 | 18% |
| *(after 0004: 40,000 × 2048 → **327.7 MB**, 18,500 × 2048 → **151.6 MB**)* | | |
| `cues` (of which `words_json` 41.1) | 63.2 | 15% |
| `ocr_lines` | 21.8 | 5% |
| `cues_fts` (data + docsize) | 18.3 | 4% |
| `chunks` | 15.0 | 4% |
| `ocr_fts` (data + docsize) | 14.0 | 3% |
| `keyframes` | 3.8 | 1% |
| `videos_fts`, `chapters`, indexes, rest | ~22 | 5% |

The `ocr_fts` row is the **pre-0003** measurement, kept because it is the number
the frame index is judged against. 0003 replaces it with `ocr_frames` (the same
text again, one row per keyframe rather than per line) plus `ocr_frames_fts`
(the same tokens, ~17× fewer documents, so a much smaller docsize shadow). The
fixture has not been regenerated since, so the honest statement is that the OCR
half of the file grows by roughly the size of the text itself — call it +10 MB
on a 420 MB file — and no re-measurement is claimed here. The FTS half of a
`delete_video` gets *cheaper*: 400 line documents become ~80 frame documents.

**Vectors are two-thirds of the database, and 0004 widens both.** At the
500-video projection the two vector rows go from 262 MB to **479 MB**, taking
the file from ~420 MB to roughly 640 MB — vectors from 62% to 75% of it. On the
live corpus it is nothing: ~5,750 vectors × 8 KB ≈ **45 MB**, against 556 MB of
keyframe JPEGs that nobody proposes to shrink. The projection is where it would
start to matter, and it has two levers rather than one now, because Qwen
publish measurements for both on this checkpoint (memo §4.4):

* **MRL** — 1024 → 512 costs 1.4% of retrieval on their sweep, so 2048 → 1024
  should cost less. Halves both rows.
* **int8** — the paper's §7.1 reports int8 preserving retrieval "with
  negligible degradation" and binary significantly impairing it. Unlike the
  table below, that is a *quantization-aware-trained* claim about this model
  rather than a generic one, which makes `config['frame_embed.storage']` a much
  safer switch than it was.

The lever measured below is quantization:

| frame vector storage | file | KNN k=50 |
|---|---|---|
| `FLOAT[1152]` | 185.4 MB | 72.0 ms |
| `INT8[1152]` (`vec_quantize_int8(?, 'unit')`) | **48.2 MB** | 65.7 ms |

3.85× smaller, ~9% faster — the KNN is not memory-bandwidth-bound at this size, so
quantization buys space, not speed, and costs some recall. v1 ships `float32`
(`config['frame_embed.storage']`) because 185 MB is not a problem at 500 videos and
recall is the product. `int8` is the documented switch for anyone indexing
thousands of videos; `vec_quantize_binary` also exists for the truly space-bound.

Query latency, median of 7 after warm-up:

| query | measured |
|---|---|
| `vec_chunks` KNN k=50 (18,500 × 1024-d) | 29.5 ms |
| `vec_frames` KNN k=50 (40,000 × 1152-d) | 72.0 ms |
| `vec_frames` KNN k=20, `video_id` filtered | 41.3 ms |
| `cues_fts` MATCH, common term, `LIMIT 5000` | 27.8 ms |
| `cues_fts` MATCH, two-term AND | 8.8 ms |
| cascade delete of one video (300 cues, 80 frames, 400 OCR lines, 117 vectors) | 1.48 s |

The delete is the slow one — hundreds of trigger-driven FTS `'delete'` commands and
vec deletes. It is a background `delete_video` job, never an interactive path, and
it is still the fast version: standalone FTS5 would be ~420× worse on the FTS half.

### 3.5 The Vec1 escape hatch

Brute-force KNN is linear. At 500 videos frame KNN is 72 ms; at 2,000 videos it is
~290 ms, which is where it stops being free. The migration path is deliberately
short because of §1.10's derived-table rule:

1. `vec_frames` and `vec_chunks` are pure projections of `keyframes` and `chunks`.
2. Switching extensions is a new `CREATE VIRTUAL TABLE`, a backfill job, and a
   changed `MATCH` clause in exactly the two query shapes in §4.2 and §4.5.
3. Nothing in `videos`, `cues`, `chunks`, `keyframes` or `ocr_lines` changes.

The thing that would make this painful — burying `vec_chunks` in joins throughout
the codebase — is avoided by the CTE discipline in §4: every vector access is a
single MATERIALIZED CTE with a fixed output shape (`id, distance`).

---

## 4. Query shapes

All of these ran. `:name` are bound parameters — no string interpolation anywhere
near SQL (screenpipe's live `CASE`-interpolation bug is in the notes as the reason).

### 4.1 Step 0: resolve corpus filters to a video-id set

Every heavy query starts here. `videos` is small; do the substring matching and
date filtering once, cheaply, and hand the FTS and vector legs a bounded id list.

```sql
SELECT id FROM videos
WHERE (:channel        IS NULL OR channel_lc LIKE '%' || lower(:channel) || '%')
  AND (:video_title    IS NULL OR title_lc   LIKE '%' || lower(:video_title) || '%')
  AND (:published_after  IS NULL OR published_at >= :published_after)
  AND (:published_before IS NULL OR published_at <  :published_before)
  AND (:indexed_after    IS NULL OR indexed_at   >= :indexed_after)
  AND index_state IN ('ready','stale');
```

Measured: **1.02 ms over 499 videos.** If the result is empty, the whole search
short-circuits to the `data_status` empty-result payload (tool-surface §4.1) without
touching FTS or vectors at all.

### 4.2 The transcript leg: FTS candidate cap, then vector rerank

The candidate cap is the seam. `ORDER BY rank LIMIT 5000` decides which candidates
survive; everything after it is bounded work.

```sql
WITH cand AS MATERIALIZED (
  SELECT c.id AS cue_id, c.video_id, c.start_s, c.end_s, c.text, f.rank AS bm25
  FROM cues_fts f
  JOIN cues c ON c.id = f.rowid
  WHERE cues_fts MATCH :q
  ORDER BY f.rank
  LIMIT :candidate_cap                       -- 5000
)
SELECT cand.cue_id, cand.video_id, cand.start_s, cand.bm25,
       vec_distance_cosine(v.embedding, :qvec) AS vdist
FROM cand
JOIN chunks     ch ON ch.video_id = cand.video_id
                  AND cand.cue_id BETWEEN ch.first_cue_id AND ch.last_cue_id
JOIN vec_chunks v  ON v.chunk_id = ch.id
ORDER BY vdist
LIMIT :rerank_out;
```

Notes:

- FTS5 `rank` is bm25 and is **negative — more negative is better**. Verified:
  `rank` and `bm25(cues_fts)` return the identical value (`-0.4749`), so `ORDER BY
  f.rank` ascending is "best first". Per-column weighting is available where it
  helps: `videos_fts MATCH :q AND rank MATCH 'bm25(10.0, 1.0, 3.0)'` weights title
  over description over channel.
- `AS MATERIALIZED` is not decoration. Without it SQLite may flatten the CTE into
  the join and re-evaluate the FTS scan per outer row, and — worse — the `LIMIT`
  stops meaning "cap the candidates". This is screenpipe's #4474 lesson verbatim:
  *"select and limit ids before joining … caps all downstream join work to one
  requested page."*
- `vec_distance_cosine` on a stored vector reranks a *known candidate set*. That is
  a different operation from `MATCH … k=`, which finds unknown neighbours. Both are
  needed; §4.3 uses both.

### 4.3 Full hybrid: two legs, RRF fusion, clustering, diversity, `has_more`

This is `search content_type=all` for the text channels (the frame leg, §4.5, joins
the same fusion). Ran end-to-end on the fixture.

```sql
WITH
params AS (SELECT 60.0 AS rrf_k, :cluster_gap AS gap_s,
                  :cluster_max AS max_span, :max_per_video AS per_video),

-- leg 1: lexical
fts_hits AS MATERIALIZED (
  SELECT c.id AS cue_id, c.video_id, c.start_s, c.end_s, c.text,
         ROW_NUMBER() OVER (ORDER BY f.rank) AS r
  FROM cues_fts f
  JOIN cues c ON c.id = f.rowid
  WHERE cues_fts MATCH :q
  ORDER BY f.rank
  LIMIT :candidate_cap
),

-- leg 2: semantic (finds what FTS missed; not merely a reranker)
vec_hits AS MATERIALIZED (
  SELECT chunk_id, distance
  FROM vec_chunks
  WHERE embedding MATCH :qvec AND k = :k_vec
),
vec_cues AS MATERIALIZED (
  SELECT c.id AS cue_id, c.video_id, c.start_s, c.end_s, c.text,
         ROW_NUMBER() OVER (ORDER BY vh.distance) AS r
  FROM vec_hits vh
  JOIN chunks ch ON ch.id = vh.chunk_id
  JOIN cues   c  ON c.id BETWEEN ch.first_cue_id AND ch.last_cue_id
),

-- reciprocal rank fusion: bm25 and cosine are not commensurable
scored AS (
  SELECT cue_id, video_id, start_s, end_s, text, SUM(s) AS score FROM (
    SELECT cue_id, video_id, start_s, end_s, text,
           1.0 / ((SELECT rrf_k FROM params) + r) AS s FROM fts_hits
    UNION ALL
    SELECT cue_id, video_id, start_s, end_s, text,
           1.0 / ((SELECT rrf_k FROM params) + r) AS s FROM vec_cues
  ) GROUP BY cue_id
),

filtered AS (
  SELECT s.* FROM scored s
  WHERE s.video_id IN (SELECT value FROM json_each(:video_ids))   -- from §4.1
    AND (:offset_start IS NULL OR s.end_s   >= :offset_start)
    AND (:offset_end   IS NULL OR s.start_s <= :offset_end)
    AND (:min_chars    IS NULL OR length(s.text) >= :min_chars)
    AND (:max_chars    IS NULL OR length(s.text) <= :max_chars)
),

-- adjacent-cue clustering: gaps-and-islands, bounded on both axes
marked AS (
  SELECT *,
    CASE WHEN start_s - LAG(end_s) OVER w <= (SELECT gap_s FROM params)
          AND CAST(start_s / (SELECT max_span FROM params) AS INTEGER)
            = CAST(LAG(start_s) OVER w / (SELECT max_span FROM params) AS INTEGER)
         THEN 0 ELSE 1 END AS is_new
  FROM filtered
  WINDOW w AS (PARTITION BY video_id ORDER BY start_s)
),
islands AS (
  SELECT *, SUM(is_new) OVER (PARTITION BY video_id ORDER BY start_s
                              ROWS UNBOUNDED PRECEDING) AS island
  FROM marked
),
clustered AS (
  SELECT video_id, island,
         MIN(start_s) AS start_s, MAX(end_s) AS end_s,
         group_concat(text, ' ' ORDER BY start_s) AS text,
         json_group_array(cue_id ORDER BY start_s) AS cue_ids,
         MAX(score)  AS score,
         COUNT(*)    AS n_cues
  FROM islands
  GROUP BY video_id, island
),

-- per-video diversity, applied BEFORE the page slice
capped AS (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY video_id
                               ORDER BY score DESC, start_s) AS rn
  FROM clustered
)
SELECT video_id, start_s, end_s, text, cue_ids, score, n_cues
FROM capped
WHERE rn <= (SELECT per_video FROM params)
ORDER BY score DESC, video_id, start_s
LIMIT :limit + 1 OFFSET :offset;          -- the +1 is has_more
```

**Clustering must be bounded on span, not just gap — measured.** With
gap-only clustering (`cluster_gap=12s`) over a dense query, the fixture produced
**4 clusters, the largest spanning 1199.8 s and 268 cues**: the entire video
collapsed into one "result". Adding the span bound:

| `cluster_max_seconds` | clusters | max cues in a cluster | max span |
|---|---|---|---|
| 60 | 61 | 15 | 59.8 s |
| **120** (default) | 31 | 30 | 119.8 s |
| unbounded | 4 | **268** | **1199.8 s** |

The bound is implemented as a fixed grid (`CAST(start_s / max_span AS INTEGER)`
changing forces a break), which keeps it pure SQL and guarantees a hard ceiling.
It is slightly arbitrary about *where* it splits a long run; that is strictly
better than emitting a 20-minute "segment". tool-surface §3.10's `cluster_max_seconds=120`
default is this measurement.

**Order of operations is load-bearing**: fuse → filter → cluster → diversity cap →
page slice. Clustering before the cap means a ten-cue sentence counts once against
`max_per_video=3`; capping first would spend the whole budget on one sentence.

**`has_more` via `LIMIT :limit + 1`.** Fetch one extra row, report
`has_more = len(rows) > limit`, return `rows[:limit]`. No second count query — the
`total` from a duplicated count query is screenpipe's standing correctness
liability, because the filters get written twice and *"must agree or pagination
breaks"* (deep-dive §4). Where tool-surface promises `approx_total`, it comes from
the same query with `LIMIT :offset + :limit + 31` and is reported as `~40+`, never
as a number pretending to be exact.

### 4.4 Anything joining tags: MATERIALIZED candidate CTE

The tag join is the shape that bit screenpipe hardest. Cap first, join second.

```sql
WITH cand AS MATERIALIZED (
  SELECT c.id AS cue_id, c.video_id, c.start_s, f.rank AS bm25
  FROM cues_fts f
  JOIN cues c ON c.id = f.rowid
  WHERE cues_fts MATCH :q
  ORDER BY f.rank
  LIMIT :candidate_cap
),
tagged AS MATERIALIZED (
  SELECT vt.video_id
  FROM video_tags vt
  JOIN tags t ON t.id = vt.tag_id
  WHERE t.full IN (SELECT value FROM json_each(:tags))
  GROUP BY vt.video_id
  HAVING COUNT(DISTINCT t.id) = :n_tags        -- AND semantics
)
SELECT cand.*
FROM cand JOIN tagged USING (video_id)
ORDER BY cand.bm25
LIMIT :limit + 1;
```

`HAVING COUNT(DISTINCT t.id) = :n_tags` is AND semantics without N self-joins.
Both CTEs are MATERIALIZED so neither is re-evaluated per outer row.

The co-occurrence block for `include_related` is a separate, independently bounded
query (30 tags / 800 ms budget, degrades to omission — tool-surface §3.7):

```sql
SELECT t.full, COUNT(*) AS n
FROM video_tags vt
JOIN tags t ON t.id = vt.tag_id
WHERE vt.video_id IN (SELECT value FROM json_each(:result_video_ids))
  AND t.full NOT IN (SELECT value FROM json_each(:query_tags))
GROUP BY t.full
ORDER BY n DESC
LIMIT 30;
```

### 4.5 The frame leg

```sql
WITH frame_hits AS MATERIALIZED (
  SELECT keyframe_id, video_id, t_s, distance
  FROM vec_frames
  WHERE embedding MATCH :q_img_vec AND k = :k_frames
),
ranked AS (
  SELECT fh.*, ROW_NUMBER() OVER (ORDER BY fh.distance) AS r
  FROM frame_hits fh
  WHERE fh.video_id IN (SELECT value FROM json_each(:video_ids))
),
capped AS (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY video_id ORDER BY distance) AS rn
  FROM ranked
)
SELECT v.public_id || '-' || printf('%05d', k.ord) AS frame_id,
       v.public_id AS video_id, c.t_s, c.distance,
       1.0 / (60.0 + c.r) AS rrf_score,
       (SELECT group_concat(o.text, ' | ' ORDER BY o.line_no)
          FROM ocr_lines o WHERE o.keyframe_id = c.keyframe_id) AS ocr_text
FROM capped c
JOIN keyframes k ON k.id = c.keyframe_id
JOIN videos    v ON v.id = c.video_id
WHERE c.rn <= :max_per_video
ORDER BY c.distance
LIMIT :limit + 1;
```

`:q_img_vec` is the *text* query embedded into the frame space — the entire
point of embedding frames rather than captioning them. `frame_id` is assembled
here so no caller ever constructs it by hand.

**Where `:q_img_vec` comes from: `POST /v1/embeddings/frame-query`.** The frame
model serves both directions from one worker lifecycle slot — `POST
/v1/embeddings/image` indexes keyframes, and this sibling path puts a query into
the same space. What runs behind it depends on the configured backend, and the
difference is worth stating because it changes what a query may *be*:

* **`Qwen/Qwen3-VL-Embedding-2B` (shipped).** The same weights that answer
  `/v1/embeddings`, under a different instruction —
  `config['frame_embed.query_prefix']` rather than
  `config['text_embed.query_prefix']`. One model, one 2048-d space, two
  retrieval tasks. Full 32K context, so a long descriptive frame query is
  embedded whole. And the slot is shared with `embed`, so a cold
  `content_type=all` search pays **one** model load instead of two (it used to
  be Qwen3-0.6B's 3.7 s *and* SigLIP's 5.8 s, possibly with an eviction between
  them).
* **`google/siglip2-so400m-patch16-naflex`.** The checkpoint's *text tower*,
  into its own 1152-d space, a second model in a second slot. Trained to **64
  tokens**: queries only, never prose, and everything past it is dropped with a
  log line and no error.

Either way `content_type=all` embeds the query **twice**, once per leg, and that
does not change with a unified model: an instruction-aware embedder gives the
same sentence different vectors under different instructions, so collapsing the
two calls would be one embedding answering two questions. Both calls hit one
loaded model, so the cost is a forward pass, not a load.

A sibling **path**, not a `space=frame` **field** on `/v1/embeddings`, and that
shape is load-bearing whichever backend is configured: point `WORKER_URL` at a
hosted OpenAI-compatible provider and an unknown *field* is ignored — you ask
for frame space, get text space at some other width, and compare it against the
frame index. An unknown *path* 404s, which is a failure you can detect. It takes
no `input_type`: this path *is* the query side, and the document side is images
with its own endpoint.

**The relevance ceiling on this leg is uncalibrated as shipped.**
`VIDTHEQUE_FRAME_MAX_DISTANCE` was measured at 0.96 in SigLIP's 1152-d space
(real best-hit 0.877–0.919, junk 0.877–0.966), and an absolute cosine distance
does not survive a change of embedder — a model that packs its corpus at a
different radius turns a ceiling that sat safely above one real range into one
that cuts through another, which is the failure mode `db/queries.py` calls the
worse one. So both floors ship open (1.0, i.e. drop only anti-correlated hits)
until the GPU bench re-measures them, with the SigLIP-era values documented in
`deploy/.env.example` for anyone still on that pair. The gate that actually
rejects nonsense is unchanged and is not a magic number: `has_lexical_footing`.

Degradation is explicit, per the "`all` means all, and a skipped leg says so" rule.
A worker that predates the endpoint 404s; the query layer remembers that (it is a
property of the worker build, not weather), prints a `note:` naming the missing
text→frame encoder, and stops asking until restart. Same latch if the vectors come
back at the wrong dimension. A *transient* failure — worker unreachable, timeout —
prints its own `note:` and is deliberately **not** cached, so the leg comes back on
its own.

Note the `k` inflation to feed the diversity cap: with `max_per_video=3`, asking for
`k = limit` returns too few distinct videos, so `k_frames = limit × max_per_video × 4`,
clamped, and bounded independently of `limit` (tool-surface §4.1 token discipline).

### 4.6 OCR leg and OCR-vs-transcript dedup

```sql
WITH ocr_cand AS MATERIALIZED (
  SELECT o.keyframe_id, o.video_id, o.t_s, o.text, f.rank AS bm25
  FROM ocr_frames_fts f
  JOIN ocr_frames o ON o.keyframe_id = f.rowid
  WHERE ocr_frames_fts MATCH :q
  ORDER BY f.rank
  LIMIT :candidate_cap
)
-- …scope/time/length predicates, rank, per-video cap, page slice…
SELECT p.*, (SELECT snippet(x.ocr_frames_fts, 0, '', '', ' … ', 64)
               FROM ocr_frames_fts x
              WHERE x.ocr_frames_fts MATCH :q AND x.rowid = p.keyframe_id) AS matched_text
FROM page p;
```

**The document is the frame** (§2.5), which is what makes `vector retrieval`
match a slide that says "vector" in the title and "retrieval" in a bullet. The
snippet is computed *outside* the page CTE, one rowid-restricted lookup per
returned row: inside the `MATERIALIZED` candidate CTE it would run
`candidate_cap` times instead of `limit` times (1,068 ms vs 145 ms on a
200,000-frame fixture).

**Dedup is not in this query.** It was: a `NOT EXISTS` against a `txt_cand` CTE
dropped any OCR line a longer transcript cue matching the same query overlapped
within ±5 s. That clause had no text-similarity test, so it was strictly more
aggressive than the rule it was prefiltering for (tool-surface §3.10: similar text
collapses, *different* text keeps both) — the Python half never saw the rows it was
supposed to judge. And it *dropped* rather than collapsing, so the surviving
transcript hit never picked up its `[transcript+ocr]` provenance. On a screencast,
where the presenter narrates what is on screen, `search content_type=ocr` for a
narrated word returned nothing, with no `note:` — "never silently narrows", broken
in the quietest possible way (research/e2e-smoke-2026-08-08.md §4.4).

So the rule lives in exactly one place, caller-side: containment or trigram-Jaccard
≥ 0.8 within ±5 s of the same video, longer text wins, provenance becomes
`[transcript+ocr]`. It is O(n·m) string work on a set already capped at `limit ×
a small constant`, which is the bound the SQL clause was there to provide, and
"which one survived" is a rendering decision rather than a storage one.

### 4.7 `get-segment-context`

```sql
-- transcript window
SELECT c.id AS cue_id, c.start_s, c.end_s, c.text,
       COALESCE(s.display_name, s.label) AS speaker
FROM cues c
LEFT JOIN speakers s ON s.id = c.speaker_id
WHERE c.video_id = :vid
  AND c.end_s >= :t - :window
  AND c.start_s <= :t + :window
ORDER BY c.start_s;

-- enclosing chapter
SELECT title, start_s, end_s FROM chapters
WHERE video_id = :vid AND :t >= start_s AND :t < end_s;

-- nearby on-screen text and the frame ids to look at
SELECT k.ord, k.t_s,
       group_concat(o.text, ' | ' ORDER BY o.line_no) AS screen_text
FROM keyframes k
LEFT JOIN ocr_lines o ON o.keyframe_id = k.id
WHERE k.video_id = :vid
  AND k.t_s BETWEEN :t - :window AND :t + :window
  AND k.dup_of IS NULL
GROUP BY k.id
ORDER BY k.t_s
LIMIT 8;

-- description links inside the window
SELECT url, title, t_s FROM video_links
WHERE video_id = :vid AND t_s BETWEEN :t - :window AND :t + :window
ORDER BY t_s LIMIT 10;
```

Four small indexed queries beat one clever join: each is independently bounded, each
maps to one `include_*` toggle, and switching a toggle off does not just discard
work — it never issues the query.

### 4.8 Rollups

```sql
-- corpus-summary
SELECT (SELECT COUNT(*) FROM videos WHERE index_state = 'ready')      AS videos_ready,
       (SELECT COUNT(*) FROM videos WHERE index_state <> 'ready')     AS videos_pending,
       (SELECT ROUND(SUM(duration_s)/3600.0, 1) FROM videos)          AS hours,
       (SELECT COUNT(*) FROM cues)                                    AS cues,
       (SELECT COUNT(*) FROM keyframes WHERE dup_of IS NULL)          AS keyframes,
       (SELECT COUNT(*) FROM ocr_lines)                               AS ocr_lines,
       (SELECT MAX(published_at) FROM videos)                         AS newest_published,
       (SELECT MAX(indexed_at)   FROM videos)                         AS last_indexed;

-- per-video coverage, for `coverage t,o,f` and data_status
SELECT v.public_id,
       MAX(s.stage = 'stt'         AND s.state = 'done') AS has_transcript,
       MAX(s.stage = 'ocr'         AND s.state = 'done') AS has_ocr,
       MAX(s.stage = 'frame_embed' AND s.state = 'done') AS has_frames
FROM videos v LEFT JOIN video_stages s ON s.video_id = v.id
WHERE v.id = :vid GROUP BY v.id;
```

These are the only unbounded `COUNT(*)`s in the system, they run on a summary tool
whose whole job is counting, and they are served from the response cache
(tool-surface §3) because the corpus is static between reindexes.

---

## 5. Operational

### 5.1 PRAGMAs

Set once per connection, at open. WAL and `page_size` persist in the file; the rest
do not.

```python
# writer
PRAGMA journal_mode = WAL;          # persisted in the file; set at creation
PRAGMA synchronous  = NORMAL;       # WAL + NORMAL: safe against process crash,
                                    # a power cut can lose the last transaction
PRAGMA foreign_keys = ON;           # PER CONNECTION. Not persisted. Easy to forget.
PRAGMA busy_timeout = 10000;
PRAGMA cache_size   = -65536;       # 64 MiB, negative = KiB not pages
PRAGMA temp_store   = MEMORY;       # the window functions in §4.3 spill to temp
PRAGMA wal_autocheckpoint = 2000;   # ~8 MB at the 4 KiB default page size

# readers: identical, plus
#   opened as file:…/vidtheque.db?mode=ro   and  PRAGMA query_only = ON
```

`PRAGMA optimize` runs on the periodic checkpoint tick and at clean shutdown, with
`PRAGMA analysis_limit = 400` set first so it cannot turn into a full scan of a
420 MB file at an inconvenient moment.

### 5.2 One writer, a pool of readers

The `mcp` process owns exactly one write connection, guarded by an `asyncio.Lock`.
The pipeline, tag writes, collection writes and job bookkeeping all go through it.
Reads come from a small pool (4–8) of `mode=ro` connections used from a thread
executor.

**Every read-modify-write uses `BEGIN IMMEDIATE`.** This is the WAL footgun worth
stating explicitly: a `BEGIN` (deferred) transaction that reads first and writes
later takes a read lock, then tries to upgrade — and if another connection wrote in
between, the upgrade fails with `SQLITE_BUSY` **that `busy_timeout` will not retry**,
because retrying would break the snapshot the transaction already read.
`BEGIN IMMEDIATE` takes the write lock up front, where `busy_timeout` does apply.
With a single writer this should never fire; it will fire the first time someone
opens `sqlite3` on the live file, and the failure mode should be a wait, not an
error.

Write batching: each pipeline stage commits in transactions of ~2,000 rows so the
WAL does not balloon during a 150,000-cue indexing run, with a
`PRAGMA wal_checkpoint(TRUNCATE)` after each video completes.

### 5.3 Cancellation

tool-surface's `E_TIMEOUT` (408-shaped, "narrow your range") and `E_BUSY`
(503 + `Retry-After`) are only honest if the query actually stops. screenpipe's
outage #4474 was precisely this: a 30 s timeout on a query that ran for 153 s while
holding its pooled connection, until concurrency drained the pool and the whole app
returned 500s (deep-dive §4). The timeout without the cancellation is worse than
neither, because it hides the problem until the pool is gone.

**Python offers two mechanisms; use both.** Measured on a 6.0 s query:

| mechanism | result |
|---|---|
| `Connection.set_progress_handler(fn, n)`, `fn` returns non-zero | aborted at **100 ms** → `OperationalError('interrupted')` |
| `Connection.interrupt()` called from another thread | aborted at **100 ms** → `OperationalError('interrupted')` |
| `interrupt()` called *before* the statement starts | **no-op — the query ran the full 6.0 s** |

That last row is the whole reason for "both". `sqlite3_interrupt()` is documented as
a no-op when no statement is running and as not affecting statements started
afterwards; a deadline that fires in the window between "request cancelled" and
"statement begins executing" is silently lost. The progress handler has no such
window — it is installed on the connection, so the very first opcode of the next
statement sees the flag.

Progress-handler overhead, same 6.0 s query:

| `n` (VM ops between calls) | wall time | handler calls |
|---|---|---|
| 1,000 | 6.05 s (+1.0%) | 1,020,000 |
| 10,000 | 5.98 s (−0.2%) | 102,000 |
| 100,000 | 5.99 s (−0.1%) | 10,200 |

screenpipe uses 1,000 from Rust; from Python each call re-acquires the GIL, so
**`n = 10_000`** — noise-level overhead, and still ~17,000 checks per second of
query time, far finer than a 30 s budget needs.

The shape:

```python
class Cancellable:
    """One per read connection. Deadline + external cancel, both honoured."""
    def __init__(self, conn):
        self.conn, self.deadline, self.cancelled = conn, None, False
        conn.set_progress_handler(self._tick, 10_000)

    def _tick(self):
        # runs on the query thread; reads flags set by the event loop thread
        if self.cancelled:
            return 1
        if self.deadline and time.monotonic() > self.deadline:
            self.cancelled = True
            return 1
        return 0

    def cancel(self):
        self.cancelled = True     # closes the pre-statement race
        self.conn.interrupt()     # stops a statement already running, now
```

Queries run via `asyncio.to_thread`; the MCP request handler wraps them in
`asyncio.timeout` and calls `cancel()` in the `finally`, so both a client
disconnect and a deadline stop real work. Both mechanisms raise the identical
`OperationalError('interrupted')`, so the *reason* comes from the flags —
`self.cancelled` plus whether the deadline passed — mapping to `E_TIMEOUT` or a
silent abort on disconnect.

**Not recommended: `apsw`.** It offers the same two primitives plus a bundled recent
SQLite and better diagnostics, which is a real argument if the deployment target's
SQLite is too old for `RETURNING` (3.35) or `group_concat(… ORDER BY …)` (3.44).
The Docker image pins its own Python, stdlib `sqlite3` on 3.46.1 has everything used
here, and `enable_load_extension` (required for sqlite-vec, and disabled in some
distro builds — verified present in ours) works. One dependency fewer. Revisit only
if extension loading or a SQLite version turns out to be a problem on some
self-hoster's platform.

### 5.4 Admission control

Two layers, from the deep-dive (§3m), scaled down to a single-owner server:

- At most **2 concurrent uncached searches**; a third gets an immediate `E_BUSY`
  with `retry_after_s`, not a queue slot. Queueing converts a slow query into a slow
  *everything*.
- Read connections are never held across an `await` that is not the query itself.

### 5.5 Backup

**What to back up, in priority order:**

1. **`vidtheque.db`** — 420 MB at 500 videos, and the only place anything
   irreplaceable lives: the video list, tags, collections, job history. Everything
   else in it is derived, but re-deriving needs the source media *and* a GPU *and*
   the videos to still exist on YouTube. Back this up.
2. **`keyframes/`** — ~4 GB at 500 videos. Derived, but only from source media that
   the default retention policy deletes (§6). In practice: back it up, or accept
   that a restore loses `get-frames` for anything taken down upstream.
3. **`audio/`** — ~1.8 GB. The cheapest insurance against a video disappearing: it
   is the input to STT, so a better model can be re-run without a re-download.
4. `media/`, `tmp/` — never.

**How.** Never copy `vidtheque.db` on its own — with WAL, the recent transactions
are in `-wal` and a bare file copy is a torn database. Two correct ways, both
verified:

```sql
VACUUM INTO '/backups/vidtheque-2026-08-08.db';   -- 32 ms; compacted, consistent
```

`VACUUM INTO` (3.27+) is one statement, takes a read transaction so writers are not
blocked in WAL mode, and produces a compacted file that opens cleanly with vec and
FTS intact (verified: the snapshot's `vec_chunks` and `cues_fts_docsize` counts
matched the source — `cues_fts` itself would have matched either way, see §2.3). It
is the scheduled-snapshot path.

`Connection.backup(target, pages=200, sleep=0.1)` — the online backup API, copying
incrementally and restarting if the source is written mid-copy. Use it when a
snapshot must be taken under sustained write load, e.g. during indexing.

Then `restic`/`rsync` the snapshot plus `keyframes/` and `audio/`. Restore is: put
the two directories back, `PRAGMA integrity_check`, `INSERT INTO
cues_fts(cues_fts) VALUES('integrity-check')`, a `cues` vs `cues_fts_docsize` count
comparison, and the §3.3 vector drift query.

**The disaster-recovery escape hatch** is `GET /videos/<id>/export.md` (tool-surface
§6): human-readable Markdown per video, transcript and OCR included. Not a backup
format — a guarantee that the corpus is never hostage to this schema.

---

## 6. Storage layout on disk

Everything under one root, `$VIDTHEQUE_DATA` (default `/data` in the container).

```
$VIDTHEQUE_DATA/
├── vidtheque.db              # + -wal, -shm. THE index.
├── keyframes/
│   └── <source_id>/          # one dir per video, e.g. kCc8FmEb1nY/
│       ├── 00000-000000000.jpg     # <ord:05d>-<t_ms:09d>.jpg
│       ├── 00001-000012480.jpg
│       └── …
├── derived/                  # resized/re-encoded get-frames variants, pure cache
│   └── <source_id>/<ord>-w512-q75.jpg
├── audio/
│   └── <source_id>.opus      # 24 kbps mono, the STT input
├── media/
│   └── <source_id>.<ext>     # source video — absent by default (§6.2)
├── tmp/
│   └── <job_public_id>/      # yt-dlp partials, ffmpeg scratch. Wiped at boot.
├── exports/                  # export.md output
└── backups/                  # VACUUM INTO targets, if backing up locally
```

Filename choices that matter:

- `<ord:05d>-<t_ms:09d>.jpg` sorts lexically into time order, so `ls` is a filmstrip
  and the ordinal in the name is the `frame_id` ordinal — a mis-scaled timestamp
  (screenpipe's live ms-vs-fps `offset_index` bug) is visible by eye.
- **Zero-padded, fixed-width, no user text in any path.** Titles change; ids do not.
- One directory per video: 500 dirs × ~80 files. If the corpus ever reaches tens of
  thousands of videos, shard on the first two characters of `source_id` — a change
  to one path-building function, since `keyframes.jpeg_path` is stored per row and
  is authoritative.
- `derived/` is disposable by definition; `rm -rf derived/` is always safe and is
  the first thing to delete when a disk fills.

Two media choices the pipeline makes, both env-overridable:

- **The extracted audio is opus, not the 16 kHz WAV whisperX nominally wants**
  (`VIDTHEQUE_AUDIO_CODEC=opus|wav|flac`, default `opus` at 24 kbps mono). The two
  halves of this system talk over HTTP, and that WAV is a ~256 MB upload per
  two-hour lecture against ~20 MB of opus — the worker re-decodes with ffmpeg
  either way, so it is a retention-and-transfer choice, not a quality one. §6.1
  sizes `audio/` on opus. `VIDTHEQUE_AUDIO_CODEC=wav` gets the uncompressed
  16 kHz PCM back.
- **Video for frame extraction is capped at 1080p, not 720p**
  (`VIDTHEQUE_INDEX_MAX_HEIGHT`, default `1080`). The OCR leg exists to read code
  and terminal text in screencasts, and 720p is where a 14 px editor font falls
  under 10 px and PP-OCR recall goes with it (research §5.3). Above 1080p the
  keyframe pipeline downsamples to `KEYFRAME_MAX_WIDTH` anyway, so the extra pixels
  are paid for and discarded; drop the cap to 720 for a talking-head corpus and
  halve the transfer.

### 6.1 Space at 500 videos (measured DB, estimated media)

| path | size | basis |
|---|---|---|
| `vidtheque.db` | **420 MB** | measured (§3.4) |
| `keyframes/` | **~3.9 GB** | 40,000 × ~98 KB (1280×720, q78) |
| `derived/` | ≤ 256 MB | byte-capped LRU cache, ~15 KB per 512 px variant, ~4 KB per 96 px one |
| `audio/` | **~1.8 GB** | 24 kbps opus × 1,200 s × 500 |
| `media/` (if kept) | **150–250 GB** | estimated: 300–500 MB per 20 min at the 1080p cap, ~2× the 720p arithmetic this row used to carry |

**Without source media: ~6 GB. With it: ~250 GB.** That ratio is the retention
argument, and question 1 in §7 — and it got worse, not better, when the download cap
moved to 1080p for OCR legibility (§6). It is also why the default is to delete:
this row is the only line in the table that is not the product.

Levers, all env-overridable: `KEYFRAME_MAX_WIDTH=960` roughly halves `keyframes/`;
`frame_embed.storage=int8` takes the DB from 420 MB to 283 MB; `KEEP_WORD_TIMINGS=0`
takes another 41 MB.

### 6.2 Retention defaults

Every one of these is an env var with an entry in `deploy/.env.example` (CLAUDE.md:
an env var without an entry there is a bug).

| what | default | rationale |
|---|---|---|
| `KEEP_SOURCE_MEDIA` | **`0`** — delete after all stages succeed | 150–250 GB at the 1080p cap (§6.1) to keep 6 GB of index. The corpus is the index; the MP4 is scaffolding. Deleted only after the last stage reports `done`, so a failure never destroys the input to a retry. |
| `KEEP_AUDIO` | **`1`** — keep opus | 1.8 GB, and it is the expensive-to-reproduce input: re-running STT with a better model needs no re-download, no re-extract, and works for videos since taken down. Best value per byte in the system. |
| `KEEP_KEYFRAMES` | `1` | They are a query product served over HTTP, not a cache. |
| `DERIVED_CACHE_MB` | `256` | Byte-capped LRU; `derived/` is disposable. Halved from the 512 this row used to carry: thumbnails are what the cache actually holds (~4 KB at 96 px, ~15 KB at 512 px), so 256 MB is already tens of thousands of variants, and the default should be a cache you never notice on a small disk. |
| `TMP_TTL_HOURS` | `24` | `tmp/` also wiped wholesale at boot — anything there belongs to a job that did not survive. |
| `JOB_EVENT_RETENTION_DAYS` | `30` | Nightly `DELETE FROM job_events`. |
| `BACKUP_KEEP` | `7` | Snapshots in `backups/`, if backing up locally. |

Deletion is a **`delete_video` job**, not an inline unlink: it drops the row (the
cascade and triggers of §1 and §3.3 clear cues, chunks, keyframes, OCR, both FTS
indexes and both vector tables — measured 1.48 s per video), then removes
`keyframes/<id>/`, `derived/<id>/`, `audio/<id>.opus` and `media/<id>.*`. Database
first, files second, so a crash mid-delete leaves orphaned *files* — detectable by a
sweep, and harmless — rather than rows pointing at files that are gone.

---

## 7. Open questions for Tom

Real forks. Everything else above is a decision.

1. **Delete source video files after indexing?** Default `KEEP_SOURCE_MEDIA=0`
   above: 150–250 GB versus 6 GB at 500 videos (§6.1, at the 1080p cap), and the
   index is the product. But
   it makes `get-clip` (deferred, tool-surface §6) require a re-download, which
   fails for anything taken down — and it makes vidtheque explicitly *not* an
   archival tool. Keeping originals makes it one, at ~20× the disk. There is a
   middle option: keep a 480 p re-encode (~30 MB/video, ~15 GB) which is enough for
   clips and re-extraction but not a real archive. Which of the three?

2. **Word-level timing granularity.** Default: JSON per cue, measured at 41.1 MB
   (10% of the database) for 500 videos, enough to trim a clip or a quote to word
   boundaries. Alternatives: drop it entirely (`KEEP_WORD_TIMINGS=0`, −41 MB, and
   whisperX's alignment stops earning its cost), or a packed binary blob (~18 MB,
   unreadable in a `sqlite3` shell). Is word-precision a thing you actually want, or
   is cue-precision (1–3 s) enough for deep links?

3. **Diarization on or off by default?** The schema carries `speakers` and
   `cues.speaker_id` either way — that part is decided. The question is
   `config['diarization.enabled']`, currently `0`: pyannote costs GPU time per video
   and a Hugging Face licence click-through that every self-hoster has to do
   manually, and `search speaker=` returns `E_FEATURE_DISABLED` without it. Worth
   the friction on by default, or opt-in?

4. **Text embedding model, and therefore 1024 dimensions.** I picked
   `qwen3-embedding-0.6b` at 1024-d as a concrete default; it is pinned in `config`
   so nothing drifts, but changing it later means a full re-embed of every chunk.
   If you have a preference from the worker side — something already resident,
   something with Matryoshka truncation so 512-d halves the 77 MB — now is the
   cheap moment.

5. **Video-level tags only, or segment-level too?** Currently video-level.
   Segment-level tags ("this 40 s is the flash-attention explanation") would be the
   strongest corpus feature in the design, and they need a durable segment identity
   — which tool-surface §3.1 deliberately refuses to mint, because query-dependent
   ids invite fabrication. A `segments` table of *user-created, durable* spans is a
   different thing from a search cluster and could coexist. Worth a v2 slot, or is
   that the beginning of a note-taking app?

6. **Is single-owner a permanent assumption?** There is no `owner_id` anywhere.
   That is right for your box, and it is the cheapest thing in this document to get
   wrong: the server is remote and speaks OAuth with dynamic client registration, so
   "a second person points their claude.ai at it" is one config change away. Adding
   tenancy later means a column on `videos`, `tags`, `collections` and `jobs`, a
   rewrite of every query in §4, and a filter on the frame-serving route. Confirm
   single-owner is the actual product, and I will stop mentioning it.
