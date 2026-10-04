-- vidtheque migration 0020 — what a profile entry is, and when it lapses (companion.md §2.1, #159).
--
-- Additive: two columns, every existing entry reads as a `topic` that never
-- expires. A `project` is something the owner is building now, written from
-- Claude's memory or an interview; it lapses 30 days after it was last
-- written unless written again. Reads skip an entry past `expires_at`; the
-- nightly update retires it with a `drop` event.

ALTER TABLE profile_entries ADD COLUMN kind TEXT NOT NULL DEFAULT 'topic'
  CHECK (kind IN ('topic','project'));
ALTER TABLE profile_entries ADD COLUMN expires_at INTEGER;
