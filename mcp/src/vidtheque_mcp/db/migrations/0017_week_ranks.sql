-- vidtheque migration 0017 — the week's ranking (companion.md §3.4, #156).
--
-- Additive: two new tables.
--
-- `week_ranks` is the latest ranking of a week's 2+ verdicts: one row per
-- ranked video, `rank` 1 the best, `top` the at most five that show as 3.
-- `week` is the Monday of the video's publication week in the box's local
-- time, as YYYY-MM-DD. A rerank replaces the week's rows.
--
-- `week_rank_runs` is one row per week: the candidate set it last ranked
-- (`candidates`, a JSON list of [video_id, verdict created_at]), so a week
-- whose set has moved since is ranked again, and how that went.

CREATE TABLE week_ranks (
  video_id  INTEGER PRIMARY KEY REFERENCES videos(id) ON DELETE CASCADE,
  week      TEXT    NOT NULL,
  rank      INTEGER NOT NULL CHECK (rank >= 1),
  top       INTEGER NOT NULL DEFAULT 0 CHECK (top IN (0,1)),
  ranked_at INTEGER NOT NULL DEFAULT (unixepoch())
) STRICT;

CREATE INDEX week_ranks_by_week ON week_ranks(week, rank);

CREATE TABLE week_rank_runs (
  week       TEXT    PRIMARY KEY,
  candidates TEXT    NOT NULL DEFAULT '[]',
  outcome    TEXT    NOT NULL CHECK (outcome IN ('ok','failed')),
  model      TEXT,
  ran_at     INTEGER NOT NULL DEFAULT (unixepoch())
) STRICT;
