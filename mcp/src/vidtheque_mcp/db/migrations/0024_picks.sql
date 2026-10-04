-- vidtheque migration 0024 — picks an agent adds on top of the verdicts
-- (companion.md §6.4, #200).
--
-- Additive: one new table. A pick is one video an agent recommends on one
-- local day, with its reason and moments that passed the verdicts' receipt
-- check (§3.1). `source` says which agent; only 'claude' writes today.

CREATE TABLE picks (
  id         INTEGER PRIMARY KEY,
  owner_id   INTEGER NOT NULL DEFAULT 1 REFERENCES owners(id),
  video_id   INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
  day        TEXT    NOT NULL,             -- the box's local date it was picked
  source     TEXT    NOT NULL DEFAULT 'claude' CHECK (source IN ('claude')),
  reason     TEXT    NOT NULL CHECK (length(reason) BETWEEN 1 AND 200),
  moments    TEXT    NOT NULL DEFAULT '[]', -- JSON [{cue_id, offset_s, end_cue_id, end_s, why}], ≤ 3
  client     TEXT,                          -- the OAuth client that picked it
  created_at INTEGER NOT NULL DEFAULT (unixepoch()),
  UNIQUE (owner_id, source, day, video_id)
) STRICT;
CREATE INDEX picks_day ON picks(owner_id, day);
