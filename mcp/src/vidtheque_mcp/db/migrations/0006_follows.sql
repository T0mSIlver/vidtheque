-- vidtheque migration 0006 — following channels.
--
-- The storage half of this feature has been here since 0001. `collections`
-- ships `kind IN ('manual','channel','playlist')`, `source_url`, `sync_cron`
-- and `last_sync_at`, and index-schema §1.8 says why in as many words:
-- "Subscriptions are deferred from the tool surface (§6) but the *storage* is
-- here now — `kind='channel' + source_url + sync_cron` is the whole feature
-- minus a cron entry, and adding it later to a populated database is a
-- migration for no reason." This is that migration, and it is small because
-- that call was right: a follow is a `collections` row that finally gets used,
-- not a new top-level concept. `collection_videos` therefore already answers
-- "what did this follow bring in", and it answers it with the same rows the
-- rest of the corpus is made of.
--
-- Three things are added:
--
--   1. `jobs.kind` learns `follow_check`, and `jobs` learns `collection_id`.
--   2. `follows` — the rules and the schedule, one row per followed collection.
--   3. `follow_seen` — the ledger: one row per candidate this follow has ever
--      looked at, carrying the decision and the number that made it.
--
--
-- WHY `follow_seen` IS NOT REDUNDANT WITH `collection_videos`
-- --------------------------------------------------------------------------
-- A candidate that was passed over never becomes a `videos` row, so there is
-- no id for `collection_videos` to hold and no way to ask what a rule cost
-- you. That is the whole difference between a filter and a stage with
-- provenance, and every other subsystem here is already the latter:
-- `video_stages` says which model transcribed what, `job_events` says why a
-- job waited ninety minutes, `data_status` admits the gap. A follow that
-- quietly drops a four-minute video because its floor is eight would be the
-- one place this index goes silent, and silence is the defect (PRODUCT.md,
-- principle 3).
--
--
-- WHY `jobs` IS REBUILT RATHER THAN ALTERED
-- --------------------------------------------------------------------------
-- SQLite cannot ALTER a CHECK constraint, and `kind` carries one. The
-- documented 12-step rebuild is what a new job kind costs, and the runner
-- already does the fiddly half of it: `migrations._apply_one` turns foreign
-- keys off for the connection, wraps the script in one transaction, and runs
-- `PRAGMA foreign_key_check` before it commits. Migrations run at boot before
-- `PipelineRunner.start`, so the queue is drained by construction and no claim
-- is in flight while the table is swapped.
--
-- Since the table is being rewritten anyway, it gains the column that makes
-- the feature legible from the jobs side: `collection_id`. Both the check
-- itself and the index job it enqueues carry it, so "which check found this
-- video" and "what has this follow queued" are plain queries rather than a
-- LIKE against `args_json`. It is nullable and NULL for every job that has
-- nothing to do with a follow, which is every job that exists today.

CREATE TABLE jobs_new (
  id               INTEGER PRIMARY KEY,
  owner_id         INTEGER NOT NULL DEFAULT 1 REFERENCES owners(id),
  public_id        TEXT    NOT NULL UNIQUE,       -- 'job_' || 12 hex
  kind             TEXT    NOT NULL
                   CHECK (kind IN ('index','reindex','delete','export','follow_check')),
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
       heartbeat_at, finished_at, NULL
  FROM jobs;

-- `job_items_roll` lives on `job_items` and only *references* `jobs` — but
-- SQLite resolves every trigger body when a table is dropped, so leaving it in
-- place makes `DROP TABLE jobs` fail with "error in trigger job_items_roll: no
-- such table: main.jobs". It comes off first and goes back verbatim below,
-- which is step 4 of the documented rebuild and not an optional tidy.
DROP TRIGGER job_items_roll;

DROP TABLE jobs;
ALTER TABLE jobs_new RENAME TO jobs;

-- Verbatim from 0001, plus the one this migration adds.
CREATE INDEX jobs_claim  ON jobs(priority, id) WHERE state = 'queued';
CREATE INDEX jobs_live   ON jobs(heartbeat_at) WHERE state = 'running';
CREATE INDEX jobs_recent ON jobs(created_at DESC);
CREATE INDEX jobs_by_collection ON jobs(collection_id, created_at DESC)
  WHERE collection_id IS NOT NULL;

-- Verbatim from 0001. Progress is a rollup maintained by trigger so it cannot
-- be forgotten; the rebuild above is the only reason it was ever off.
CREATE TRIGGER job_items_roll AFTER UPDATE OF state ON job_items
WHEN new.state IN ('done','failed','skipped','cancelled')
 AND old.state NOT IN ('done','failed','skipped','cancelled')
BEGIN
  UPDATE jobs SET n_done   = n_done   + (new.state = 'done'),
                  n_failed = n_failed + (new.state = 'failed')
  WHERE id = new.job_id;
END;


-- ----------------------------------------------------------------- follows
--
-- Keyed by `collection_id` rather than carrying an id of its own: the follow
-- *is* the collection, and two names for one number is the kind of drift this
-- schema spends its CHECK constraints preventing. A `manual` collection simply
-- has no row here.
--
-- `owner_id` is not repeated for the same reason — `collections` carries it,
-- and this row cannot outlive its parent.
--
-- THE CLOCKS. `collections.last_sync_at` is the last-check stamp; it is not
-- duplicated here. `next_check_at` is new because the queue needs something to
-- sort on, and it is an absolute epoch rather than an interval so a paused
-- follow that resumes does not silently owe six hours.
--
-- `collections.sync_cron` STAYS NULL AND UNUSED. A cron expression is a config
-- language, and the dashboard is explicitly not a config editor
-- (dashboard.md §1, non-goal 4) — "every 6 hours" typed as `0 */6 * * *` is a
-- worse control and a parser nobody asked for. The interval lives in
-- `check_interval_s`. The column is left in place rather than dropped because
-- a literal cron may still earn its place later, and index-schema §1.8 now
-- records that it is reserved rather than forgotten.

CREATE TABLE follows (
  collection_id    INTEGER PRIMARY KEY REFERENCES collections(id) ON DELETE CASCADE,
  state            TEXT    NOT NULL DEFAULT 'active'
                   CHECK (state IN ('active','paused','failing')),
  -- Which channel tabs the follow watches. A comma-separated subset of
  -- videos,streams,shorts — one flat extraction each, against a source that
  -- rate-limits, which is why the default is one.
  tabs             TEXT    NOT NULL DEFAULT 'videos',
  -- NULL means no floor / no ceiling. Seconds, matching `videos.duration_s`.
  min_duration_s   INTEGER CHECK (min_duration_s IS NULL OR min_duration_s >= 0),
  max_duration_s   INTEGER CHECK (max_duration_s IS NULL OR max_duration_s >= 0),
  -- Comma-separated plain substrings, case-insensitive; exclude wins. Not
  -- regex: a regex in a config field is unbounded compute from a stored
  -- string, and every rule here has to be cheap enough to run on a listing.
  title_include    TEXT,
  title_exclude    TEXT,
  -- `index-video`'s own vocabulary, verbatim: 'all' or a subset of
  -- transcript,ocr,frames. A podcast follow that only wants transcripts is
  -- where this parameter finally pays for itself.
  channels         TEXT    NOT NULL DEFAULT 'all',
  -- Applied to every video the follow brings in, same validation as `tag-video`.
  tags             TEXT,
  -- Uploads to reach back for at the moment of following. Capped at 25 here as
  -- well as in the tool: a bigger sweep is a deliberate `index-video
  -- expand=channel_recent`, not something a follow does while you are asleep.
  backfill         INTEGER NOT NULL DEFAULT 0 CHECK (backfill BETWEEN 0 AND 25),
  max_per_check    INTEGER NOT NULL DEFAULT 5 CHECK (max_per_check BETWEEN 1 AND 25),
  -- Held candidates wait for a human instead of queueing themselves.
  mode             TEXT    NOT NULL DEFAULT 'auto' CHECK (mode IN ('auto','review')),
  check_interval_s INTEGER NOT NULL DEFAULT 21600 CHECK (check_interval_s >= 900),
  next_check_at    INTEGER NOT NULL DEFAULT 0,
  last_new_at      INTEGER,
  last_error_code  TEXT,
  last_error_message TEXT,
  created_at       INTEGER NOT NULL DEFAULT (unixepoch()),
  updated_at       INTEGER NOT NULL DEFAULT (unixepoch())
) STRICT;

-- The due query, and the only one on the enqueue path.
CREATE INDEX follows_due ON follows(next_check_at) WHERE state = 'active';


-- ------------------------------------------------------------- follow_seen
--
-- One row per candidate per follow, ever — `UNIQUE (collection_id, source_id)`
-- is what stops a check reconsidering the same upload every six hours for a
-- year. A row is updated in place when a decision changes, which happens in
-- exactly one direction that matters: `held_budget` becomes `queued` when the
-- window frees.
--
-- `decision` is a fifth state vocabulary and PRODUCT.md says no surface may
-- invent one. It is not a fifth reading of an existing state: nothing else in
-- this schema records what happened to a video that was never indexed, which
-- is precisely what these rows are. The four existing vocabularies
-- (`index_state`, job `state`, item `state`, stage `state`) all describe work
-- that was accepted; this one describes the decision to accept it.
--
-- `duration_s` NULL means neither the listing nor a probe ever said — it is
-- not zero, and a rule with a floor must not treat it as zero.

CREATE TABLE follow_seen (
  id            INTEGER PRIMARY KEY,
  collection_id INTEGER NOT NULL REFERENCES collections(id) ON DELETE CASCADE,
  source_id     TEXT    NOT NULL,
  url           TEXT    NOT NULL,
  title         TEXT,
  duration_s    REAL,
  published_at  INTEGER,
  tab           TEXT,
  decision      TEXT    NOT NULL
                CHECK (decision IN ('queued','held_budget','held_review',
                                    'skipped_tab','skipped_title','skipped_duration',
                                    'skipped_horizon','already_indexed','failed')),
  -- The sentence a human reads on the "what it passed over" band. It carries
  -- the number that made the decision, because "shorter than your floor" is an
  -- opinion and "4:12, shorter than your 8:00 floor" is a receipt.
  reason        TEXT,
  -- Where the duration came from. The flat listing carries it for most
  -- entries and not all; the rest cost one probe, which is still before any
  -- download. The surface prints which, so a check that spent requests says so.
  judged_from   TEXT    NOT NULL DEFAULT 'listing'
                CHECK (judged_from IN ('listing','probe')),
  video_id      INTEGER REFERENCES videos(id) ON DELETE SET NULL,
  job_id        INTEGER REFERENCES jobs(id)   ON DELETE SET NULL,
  first_seen_at INTEGER NOT NULL DEFAULT (unixepoch()),
  decided_at    INTEGER NOT NULL DEFAULT (unixepoch()),
  UNIQUE (collection_id, source_id)
) STRICT;

-- The follow detail page reads the ledger newest-first, per follow.
CREATE INDEX follow_seen_recent ON follow_seen(collection_id, decided_at DESC);
-- The daily budget is a sum over accepted rows in a rolling window, across
-- every follow. Partial, because it is the only decision the sum counts.
CREATE INDEX follow_seen_budget ON follow_seen(decided_at) WHERE decision = 'queued';
