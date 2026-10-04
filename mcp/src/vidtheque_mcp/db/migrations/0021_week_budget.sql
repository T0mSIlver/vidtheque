-- vidtheque migration 0021 — the owner's weekly time budget (companion.md §6, #156).
--
-- Additive: one column on `owners`. `week_budget_min` is the minutes a week
-- the feed fits, 210 by default, 30 a day.

ALTER TABLE owners ADD COLUMN week_budget_min INTEGER NOT NULL DEFAULT 210
  CHECK (week_budget_min BETWEEN 0 AND 10080);
