-- vidtheque migration 0002 — config model ids become the ids the worker serves.
--
-- 0001 seeded friendly short names (`qwen3-embedding-0.6b`); the worker reports
-- the identifier it was configured with, which `deploy/.env.example` ships as
-- the Hugging Face id (`Qwen/Qwen3-Embedding-0.6B`). The drift checks compare
-- those two strings — `db/database.py::note_worker_drift` at query time,
-- `pipeline/runner.py::_dimension_mismatch` at index time — so on a default
-- install the repo disagreed with itself and disabled both vector legs before
-- a single vector was written (research/e2e-smoke-2026-08-08.md §4.1).
--
-- The canonical form is therefore **the exact id the worker reports**: the
-- comparison stays an exact (casefold-only) string match, so two genuinely
-- different checkpoints are still caught, and `video_stages.model_key` reads
-- back as the thing that actually produced the row (index-schema §2.2).
--
-- Each UPDATE is guarded on the 0001 value, so an operator who already pointed
-- `config` at a different checkpoint keeps their value: this migration renames
-- the shipped defaults, it does not overwrite a deliberate choice. The matching
-- `video_stages.model_key` rewrite is part of the rename — the same weights
-- produced those rows, so the reindex planner must not read them as stale.

UPDATE config SET value = 'Qwen/Qwen3-Embedding-0.6B', updated_at = unixepoch()
 WHERE key = 'text_embed.model' AND value = 'qwen3-embedding-0.6b';
UPDATE video_stages SET model_key = 'Qwen/Qwen3-Embedding-0.6B'
 WHERE stage = 'text_embed' AND model_key = 'qwen3-embedding-0.6b';

UPDATE config SET value = 'google/siglip2-so400m-patch16-naflex', updated_at = unixepoch()
 WHERE key = 'frame_embed.model' AND value = 'siglip2-so400m-patch16-naflex';
UPDATE video_stages SET model_key = 'google/siglip2-so400m-patch16-naflex'
 WHERE stage = 'frame_embed' AND model_key = 'siglip2-so400m-patch16-naflex';

UPDATE config SET value = 'rapidocr-default', updated_at = unixepoch()
 WHERE key = 'ocr.model' AND value = 'rapidocr-v2';
UPDATE video_stages SET model_key = 'rapidocr-default'
 WHERE stage = 'ocr' AND model_key = 'rapidocr-v2';

-- `stt.model` never reaches a drift check (the worker logs `ignoring requested
-- model=…` and serves what it loaded), but it is sent on the transcription
-- request and recorded as `model_key`, so it gets the same treatment.
-- Auto-caption runs keep their own `youtube-asr-*` key; `_should_run` already
-- knows that key will never equal this one.
UPDATE config SET value = 'large-v3', updated_at = unixepoch()
 WHERE key = 'stt.model' AND value = 'whisperx-large-v3';
UPDATE video_stages SET model_key = 'large-v3'
 WHERE stage = 'stt' AND model_key = 'whisperx-large-v3';
