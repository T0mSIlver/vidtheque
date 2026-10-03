-- vidtheque migration 0013 — the nightly profile update's runs (companion.md §2.4).
--
-- Additive: one new table. A row per owner per local day is what keeps the
-- update to once a day across restarts: the run claims its day before the
-- model call, and the ops it applies commit in the same transaction that
-- marks it `done`, so a crash in between applies nothing and may retry.
--
-- `state`: `running` (claimed), `done` (the model answered; its ops were
-- applied or refused), `idle` (no signals since the last run; no model call),
-- `failed` (the model call failed; nothing applied; retried a few times that day).
-- `since_at`/`until_at` bound the signals it read, so the next run starts where
-- the last finished one stopped. `refused` is a JSON list of the proposed ops
-- the guards turned away, each with why.

CREATE TABLE nightly_runs (
  id          INTEGER PRIMARY KEY,
  owner_id    INTEGER NOT NULL DEFAULT 1 REFERENCES owners(id),
  day         TEXT    NOT NULL,                  -- the box's local date, YYYY-MM-DD
  state       TEXT    NOT NULL CHECK (state IN ('running','done','idle','failed')),
  attempts    INTEGER NOT NULL DEFAULT 1,
  started_at  INTEGER NOT NULL,
  finished_at INTEGER,
  since_at    INTEGER NOT NULL,
  until_at    INTEGER NOT NULL,
  n_signals   INTEGER NOT NULL DEFAULT 0,
  n_applied   INTEGER NOT NULL DEFAULT 0,
  refused     TEXT    NOT NULL DEFAULT '[]',
  model       TEXT,
  error       TEXT,
  UNIQUE (owner_id, day)
) STRICT;
