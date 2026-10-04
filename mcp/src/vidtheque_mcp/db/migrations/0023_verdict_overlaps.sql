-- vidtheque migration 0023 — the stretches a video repeats from videos the
-- owner already saw (companion.md §3.2, #171).
--
-- Additive: one column, every existing verdict reads '[]' (none found) until
-- it is rescored. JSON [{video_id, start_s, end_s, seen_s}]: `start_s`–`end_s`
-- of this video says again what `video_id` says from `seen_s`.

ALTER TABLE verdicts ADD COLUMN overlaps TEXT NOT NULL DEFAULT '[]';
