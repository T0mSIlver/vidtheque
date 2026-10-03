-- vidtheque migration 0015 — thumbs and "less like this" as a state per video
-- (companion.md §2.3).
--
-- Additive: one new table, nothing else touched. `state` is what the owner
-- thinks of the video now ('none' once taken back); `seen` is the state the
-- last nightly update read, so a night reads only `state != seen` and a tap
-- taken back before then nets to nothing. A night deletes the rows it leaves
-- with both 'none'. The `signals` rows these taps also write stay as the event log.

CREATE TABLE feedback (
  owner_id INTEGER NOT NULL DEFAULT 1 REFERENCES owners(id),
  video_id INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
  state    TEXT    NOT NULL CHECK (state IN ('none','up','down','muted')),
  seen     TEXT    NOT NULL DEFAULT 'none' CHECK (seen IN ('none','up','down','muted')),
  at       INTEGER NOT NULL DEFAULT (unixepoch()),
  PRIMARY KEY (owner_id, video_id)
) STRICT;
