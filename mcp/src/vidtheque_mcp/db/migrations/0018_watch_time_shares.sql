-- vidtheque migration 0018 — measuring the feed against YouTube (#157,
-- companion.md §3.3).
--
-- Additive: one column and one table. `signals.watched_s` is how long the
-- owner stayed in the player after a `watch` hand-off, set once when the app
-- comes back; NULL on every other kind and on a watch whose return was never
-- reported. An added column needs no rebuild of `signals`, unlike a new kind.
--
-- `shares` is one row per YouTube link shared to the app ("found it
-- elsewhere"). It keys on the YouTube id rather than `videos.id` because the
-- video is usually not in the corpus yet when the share lands; whether it was
-- a miss is read later, once its verdict exists (dashboard.md §25.11).

ALTER TABLE signals ADD COLUMN watched_s REAL CHECK (watched_s IS NULL OR watched_s >= 0);

CREATE TABLE shares (
  id        INTEGER PRIMARY KEY,
  owner_id  INTEGER NOT NULL DEFAULT 1 REFERENCES owners(id),
  at        INTEGER NOT NULL DEFAULT (unixepoch()),
  source_id TEXT    NOT NULL CHECK (length(source_id) BETWEEN 1 AND 64),
  client    TEXT
) STRICT;

CREATE INDEX shares_at ON shares(owner_id, at);
