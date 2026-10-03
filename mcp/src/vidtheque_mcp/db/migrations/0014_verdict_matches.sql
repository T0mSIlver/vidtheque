-- vidtheque migration 0014 — the profile entries a verdict matched (companion.md §3.1).
--
-- Additive: one column, every existing verdict reads '[]' (scored before
-- matches were structured; its `reason` still names them in prose). A match
-- is `{entry_id, direction, strength}`; the entry's text is read from
-- `profile_entries` at read time, retired or not.

ALTER TABLE verdicts ADD COLUMN matches TEXT NOT NULL DEFAULT '[]';
