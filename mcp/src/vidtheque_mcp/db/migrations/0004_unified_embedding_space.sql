-- vidtheque migration 0004 — one embedding space, 2048 dims, both legs.
--
-- Tom's decision of 2026-08-09 (research/multimodal-embedding-2026-08-09.md,
-- addendum): `Qwen/Qwen3-VL-Embedding-2B` replaces the pair of
-- `Qwen/Qwen3-Embedding-0.6B` (transcripts) and
-- `google/siglip2-so400m-patch16-naflex` (frames), at the checkpoint's native
-- 2048 dims rather than the memo's MRL@1024. So *both* vec tables rebuild:
-- `vec_chunks` 1024 -> 2048 and `vec_frames` 1152 -> 2048.
--
-- Storage stays trivial — ~5.5k vectors x 8 KB ~= 45 MB fp32 on the live
-- corpus, and §3.4's 500-video projection roughly doubles from its vector
-- line, not from its total.
--
--
-- WHAT THIS INVALIDATES, AND WHAT IT DELIBERATELY DOES NOT
-- --------------------------------------------------------------------------
-- Exactly two stages: `text_embed` and `frame_embed`. `fetch`, `stt`, `chunk`,
-- `keyframe` and `ocr` keep their `done` rows and their `model_key`s, so:
--
--   * **nothing is re-downloaded.** `want_media` is gated on the *keyframe*
--     stage being stale (`pipeline/runner.py`), which it is not — so the mp4s
--     that `keep_source=audio` already deleted are never missed. The inputs
--     are the keyframe JPEGs already on disk and the chunk text already in
--     this file.
--   * **nothing is re-transcribed and nothing is re-OCR'd**, for the same
--     reason via `need_audio`.
--
-- Re-embed cost on the live corpus, estimated: ~11 min of GPU for 3,060
-- frames and ~50 s for 2,691 chunks (§5.3 of the memo). Minutes, not hours.
--
--
-- WHY THE STAGES GO TO `pending` RATHER THAN KEEPING A STALE `model_key`
-- --------------------------------------------------------------------------
-- Either would re-run the stage: `_should_run` compares the recorded
-- `model_key` against `config`'s for every stage but `stt` and `keyframe`, and
-- for an EMBEDDING stage that comparison is exactly right — a different
-- checkpoint really is a different vector space, and there is no
-- `PROVENANCE_SEP` forgiveness to give (contrast the `keyframe` stage, whose
-- `+fused` half is provenance and must NOT invalidate: §1.3).
--
-- But a `done` row with a stale key is a *lie to the reader*. `coverage`
-- derives `has_frames` from `frame_embed` being `done`, `data_status` and
-- `video-summary` print it, and the vec table is empty — so for the whole
-- window between this migration and the re-embed finishing, the corpus would
-- claim frame search it cannot answer. `pending` is the same re-run trigger
-- and an honest one: `data_status` reads `no_frames` per video and `degraded`
-- corpus-wide until the backfill lands, and `search` prints a `note:` naming
-- how many videos are waiting. "All means all" applies to what we claim to
-- have, not only to what we query.
--
-- `skipped` rows are left alone: they record a deliberate choice (a video
-- indexed `channels=transcript` never wanted frame vectors) and re-running
-- them was never the plan.
--
--
-- WHY NOT KEEP THE OLD TABLES SERVING UNTIL THE NEW ONES FILL
-- --------------------------------------------------------------------------
-- Considered and rejected. It means two vec tables per leg at two widths and a
-- query layer that unions them — i.e. fusing ranks from two embedding spaces
-- into one RRF, which is precisely the "plausible-looking garbage that no test
-- catches" §1.1 exists to prevent. The degraded window is minutes and it is
-- *reported*; a mixed space is permanent and silent.
--
--
-- THE ORDERING TRAP (memo §5.4), FOR WHOEVER RUNS THIS ON A LIVE BOX
-- --------------------------------------------------------------------------
-- **Config first, worker second.** `note_worker_drift` disables both vector
-- legs the moment the worker reports a model that differs from `config`, and
-- both embed stages skip themselves when `db.vectors.enabled` is false — so a
-- worker switched to the unified model *before* this migration runs latches
-- the legs off and the re-embed silently never happens. Migrate, then restart
-- the worker with the new EMBED_BACKEND/EMBED_MODEL.
--
--
-- IF YOUR CORPUS IS NOT ON THE SHIPPED DEFAULTS
-- --------------------------------------------------------------------------
-- The `config` rewrites below are guarded on the 0002 values, so a deliberate
-- choice (`bge-m3`, say) is not overwritten — 0002's precedent. The vec tables
-- are NOT guardable that way: a `vec0` dimension is a literal in DDL and there
-- is no conditional CREATE, so they are rebuilt at 2048 unconditionally.
--
-- A corpus on a non-default embedder therefore lands in the §1.1 boot
-- assertion's mismatch state on purpose: writes refused, reads FTS-only, and a
-- `note:` on every response naming both numbers. That is loud and recoverable,
-- and it is the same state a hand-edited dimension has always produced. To
-- stay on your own embedder, set `config['*_embed.dim']` to its width and
-- re-create the matching table (`DROP TABLE vec_chunks;` + the CREATE below at
-- your width) — the vectors were invalidated by the width change either way.
-- Both old backends remain selectable in the worker; only the shipped default
-- moved.

-- --------------------------------------------------------------------- config
--
-- Models first, then everything that must follow the model. The dims and the
-- instructions are guarded on the model *having become* the unified one rather
-- than on their own old values, so a corpus that kept `bge-m3` (also 1024-d)
-- does not get a 2048 dim written under a 1024-d model.

UPDATE config SET value = 'Qwen/Qwen3-VL-Embedding-2B', updated_at = unixepoch()
 WHERE key = 'text_embed.model' AND value = 'Qwen/Qwen3-Embedding-0.6B';

UPDATE config SET value = 'Qwen/Qwen3-VL-Embedding-2B', updated_at = unixepoch()
 WHERE key = 'frame_embed.model' AND value = 'google/siglip2-so400m-patch16-naflex';

UPDATE config SET value = '2048', updated_at = unixepoch()
 WHERE key = 'text_embed.dim'
   AND EXISTS (SELECT 1 FROM config
                WHERE key = 'text_embed.model' AND value = 'Qwen/Qwen3-VL-Embedding-2B');

UPDATE config SET value = '2048', updated_at = unixepoch()
 WHERE key = 'frame_embed.dim'
   AND EXISTS (SELECT 1 FROM config
                WHERE key = 'frame_embed.model' AND value = 'Qwen/Qwen3-VL-Embedding-2B');

-- The instruction record, and the reason it is worth fixing rather than
-- dropping. 0001 seeded `text_embed.query_prefix = 'query: '` against a model
-- that applies `Instruct: …\nQuery: …`; the column has therefore been wrong
-- since the first commit and nobody noticed, because nothing reads it
-- (pipeline-perf-2026-08-09.md §5). An instruction-aware model with *two*
-- instructions over *one* space makes it more load-bearing, not less: the same
-- sentence embeds differently under each, so "which model" is no longer the
-- whole answer to "what space is this".
--
-- These two strings are the worker's shipped defaults
-- (`worker/.../qwen3_vl_embed.py`: DEFAULT_QUERY_INSTRUCTION and
-- DEFAULT_FRAME_INSTRUCTION, overridable with EMBED_QUERY_PROMPT /
-- FRAME_QUERY_PROMPT). They are still applied by the worker and never
-- prepended here — doing both applies them twice — but the worker now echoes
-- what it applied on every embeddings response and on `GET /status`, so the
-- record can be *checked* instead of trusted. One curl is the reconciliation.
UPDATE config SET value = 'Given a search query, retrieve the transcript passage that answers it',
       updated_at = unixepoch()
 WHERE key = 'text_embed.query_prefix'
   AND EXISTS (SELECT 1 FROM config
                WHERE key = 'text_embed.model' AND value = 'Qwen/Qwen3-VL-Embedding-2B');

-- New key: the frame leg never had one, because SigLIP's text tower takes no
-- instruction. It does now.
INSERT INTO config (key, value)
SELECT 'frame_embed.query_prefix', ''
 WHERE NOT EXISTS (SELECT 1 FROM config WHERE key = 'frame_embed.query_prefix');

UPDATE config SET value = 'Given a search query, retrieve the video frame that matches it',
       updated_at = unixepoch()
 WHERE key = 'frame_embed.query_prefix'
   AND EXISTS (SELECT 1 FROM config
                WHERE key = 'frame_embed.model' AND value = 'Qwen/Qwen3-VL-Embedding-2B');

-- `pipeline.version` describes the semantics of the contents, not the shape of
-- the file (§1.10). Switching embedders changes no column and invalidates
-- every vector, which is exactly what this counter is for.
UPDATE config SET value = '2', updated_at = unixepoch()
 WHERE key = 'pipeline.version' AND value = '1';

-- --------------------------------------------------------------------- stages
--
-- Only `done` rows, and only the two embedding stages. `skipped` is a
-- deliberate choice and stays one.

UPDATE video_stages
   SET state = 'pending', model_key = NULL, started_at = NULL,
       finished_at = NULL, error = NULL
 WHERE stage IN ('text_embed', 'frame_embed') AND state = 'done';

-- -------------------------------------------------------------------- vectors
--
-- Derived tables are rebuilt, never migrated (§1.10 rule 3). Both are dropped
-- and re-created at 2048 with the same metadata columns, the same
-- `distance_metric=cosine` and the same `chunk_size`; the only change is the
-- width. Still a plain `video_id` metadata column and NOT a `PARTITION KEY` —
-- §3.2's measured trap (a full chunk allocated per partition, and `k` applied
-- per partition) has nothing to do with the width and is unchanged.
--
-- `chunks_ad` / `keyframes_ad` are triggers ON `chunks` / `keyframes`, not on
-- the vec tables, so dropping a vec table does not drop them and they keep
-- deleting vectors for a deleted video. Asserted in the migration tests,
-- because the failure mode — a deleted video silently leaving its frame
-- vectors behind — is invisible until a search returns a frame id that no
-- longer resolves.

DROP TABLE IF EXISTS vec_chunks;
CREATE VIRTUAL TABLE vec_chunks USING vec0(
  chunk_id  INTEGER PRIMARY KEY,
  video_id  INTEGER,
  start_s   FLOAT,
  embedding FLOAT[2048] distance_metric=cosine,
  chunk_size=256
);

DROP TABLE IF EXISTS vec_frames;
CREATE VIRTUAL TABLE vec_frames USING vec0(
  keyframe_id INTEGER PRIMARY KEY,
  video_id    INTEGER,
  t_s         FLOAT,
  embedding   FLOAT[2048] distance_metric=cosine,
  chunk_size=256
);
