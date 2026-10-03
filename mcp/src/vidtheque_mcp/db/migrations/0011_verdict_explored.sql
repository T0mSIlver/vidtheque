-- vidtheque migration 0011 — the exploration flag on verdicts (companion.md §3.2).
--
-- Additive: one column, every existing verdict reads 0 (scored on the whole
-- profile). 1 marks a verdict re-scored without the negative entries that
-- reached 2, which the feed shows as outside the profile.

ALTER TABLE verdicts ADD COLUMN explored INTEGER NOT NULL DEFAULT 0 CHECK (explored IN (0,1));
