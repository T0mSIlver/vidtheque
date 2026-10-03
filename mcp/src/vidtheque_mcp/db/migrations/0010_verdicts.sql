-- vidtheque migration 0010 — verdicts (companion.md §3.1–3.2).
--
-- Two things: `jobs.kind` learns `verdict`, and the `verdicts` table.
--
-- `jobs` is rebuilt the way 0006 rebuilt it, for the same reason: SQLite
-- cannot ALTER a CHECK constraint. The new table is 0006's verbatim but for
-- the one word; every row is copied as it is, `job_items_roll` comes off
-- before the drop and goes back verbatim after it (index-schema §1.9), and the
-- runner turns foreign keys off and runs `foreign_key_check` before commit.

CREATE TABLE jobs_new (
  id               INTEGER PRIMARY KEY,
  owner_id         INTEGER NOT NULL DEFAULT 1 REFERENCES owners(id),
  public_id        TEXT    NOT NULL UNIQUE,       -- 'job_' || 12 hex
  kind             TEXT    NOT NULL
                   CHECK (kind IN ('index','reindex','delete','export','follow_check',
                                   'verdict')),
  state            TEXT    NOT NULL DEFAULT 'queued'
                   CHECK (state IN ('queued','running','done','failed','cancelled')),
  priority         INTEGER NOT NULL DEFAULT 100,  -- lower runs first; 'high' = 50
  args_json        TEXT    NOT NULL DEFAULT '{}',
  n_items          INTEGER NOT NULL DEFAULT 0,
  n_done           INTEGER NOT NULL DEFAULT 0,
  n_failed         INTEGER NOT NULL DEFAULT 0,
  cancel_requested INTEGER NOT NULL DEFAULT 0 CHECK (cancel_requested IN (0,1)),
  error_code       TEXT,
  error_message    TEXT,
  not_before       INTEGER NOT NULL DEFAULT 0,
  created_at       INTEGER NOT NULL DEFAULT (unixepoch()),
  started_at       INTEGER,
  heartbeat_at     INTEGER,
  finished_at      INTEGER,
  -- The follow this job belongs to, when it belongs to one: a `follow_check`
  -- is one, and so is the `index` job that check enqueues.
  collection_id    INTEGER REFERENCES collections(id) ON DELETE SET NULL
) STRICT;

INSERT INTO jobs_new (id, owner_id, public_id, kind, state, priority, args_json,
                      n_items, n_done, n_failed, cancel_requested, error_code,
                      error_message, not_before, created_at, started_at,
                      heartbeat_at, finished_at, collection_id)
SELECT id, owner_id, public_id, kind, state, priority, args_json,
       n_items, n_done, n_failed, cancel_requested, error_code,
       error_message, not_before, created_at, started_at,
       heartbeat_at, finished_at, collection_id
  FROM jobs;

-- SQLite resolves every trigger body when a table is dropped (0006).
DROP TRIGGER job_items_roll;

DROP TABLE jobs;
ALTER TABLE jobs_new RENAME TO jobs;

-- Verbatim from 0006.
CREATE INDEX jobs_claim  ON jobs(priority, id) WHERE state = 'queued';
CREATE INDEX jobs_live   ON jobs(heartbeat_at) WHERE state = 'running';
CREATE INDEX jobs_recent ON jobs(created_at DESC);
CREATE INDEX jobs_by_collection ON jobs(collection_id, created_at DESC)
  WHERE collection_id IS NOT NULL;

CREATE TRIGGER job_items_roll AFTER UPDATE OF state ON job_items
WHEN new.state IN ('done','failed','skipped','cancelled')
 AND old.state NOT IN ('done','failed','skipped','cancelled')
BEGIN
  UPDATE jobs SET n_done   = n_done   + (new.state = 'done'),
                  n_failed = n_failed + (new.state = 'failed')
  WHERE id = new.job_id;
END;


-- ---------------------------------------------------------------- verdicts
--
-- One per video; a rerun replaces it. `owner_id` is not repeated: the video
-- carries it, and this row cannot outlive the video. `moments` is a JSON list
-- of `{cue_id, offset_s, why}` that passed the receipt check (§3.1) — at most
-- three, possibly none. `notified_at` belongs to the push (§3.1, #88+) and a
-- rerun leaves it alone, so a video is never notified twice.

CREATE TABLE verdicts (
  video_id    INTEGER PRIMARY KEY REFERENCES videos(id) ON DELETE CASCADE,
  score       INTEGER NOT NULL CHECK (score BETWEEN 0 AND 3),
  reason      TEXT    NOT NULL,
  summary     TEXT    NOT NULL,
  moments     TEXT    NOT NULL DEFAULT '[]',
  profile_rev INTEGER NOT NULL,
  model       TEXT    NOT NULL,
  created_at  INTEGER NOT NULL DEFAULT (unixepoch()),
  notified_at INTEGER
) STRICT;

-- The feed reads newest first.
CREATE INDEX verdicts_recent ON verdicts(created_at DESC);
