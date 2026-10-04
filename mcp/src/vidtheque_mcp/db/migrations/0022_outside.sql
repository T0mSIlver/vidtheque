-- vidtheque migration 0022 — discovery outside the follows (companion.md §6.2, #170).
--
-- Additive: a nullable column on `follows` (a trial follow's end), the scout's
-- picks, the weekly speaker suggestion, and one row per scout night. The
-- shapes and their rules are index-schema §1.22.

ALTER TABLE follows ADD COLUMN trial_until INTEGER;  -- unix s; NULL = a lasting follow

CREATE TABLE outside_picks (
  id           INTEGER PRIMARY KEY,
  owner_id     INTEGER NOT NULL DEFAULT 1 REFERENCES owners(id),
  source_id    TEXT    NOT NULL CHECK (length(source_id) BETWEEN 1 AND 64),  -- the YouTube id
  week         TEXT    NOT NULL,          -- the Monday of the week it was scouted, local
  entry_id     INTEGER REFERENCES profile_entries(id) ON DELETE SET NULL,
  because      TEXT    NOT NULL,          -- the entry's text when it was scouted
  title        TEXT    NOT NULL,
  channel_id   TEXT, channel_name TEXT, channel_url TEXT,
  duration_s   REAL    NOT NULL DEFAULT 0,
  published_at INTEGER,
  state        TEXT    NOT NULL CHECK (state IN ('no_captions','judged','shown')),
  score        INTEGER CHECK (score BETWEEN 0 AND 3),
  reason       TEXT, summary TEXT,
  moments      TEXT    NOT NULL DEFAULT '[]',  -- JSON [{offset_s, end_s, why}], ≤ 3
  model        TEXT,
  feedback     TEXT    NOT NULL DEFAULT 'none' CHECK (feedback IN ('none','up','down')),
  watches      TEXT    NOT NULL DEFAULT '[]',  -- JSON [[from_s, to_s]], ≤ 20
  followed_at  INTEGER,                        -- a trial follow started from it
  created_at   INTEGER NOT NULL DEFAULT (unixepoch()),
  UNIQUE (owner_id, source_id)
) STRICT;
CREATE INDEX outside_picks_week ON outside_picks(owner_id, week, state);

CREATE TABLE speaker_suggestions (
  id           INTEGER PRIMARY KEY,
  owner_id     INTEGER NOT NULL DEFAULT 1 REFERENCES owners(id),
  week         TEXT    NOT NULL,
  name         TEXT    NOT NULL,
  name_key     TEXT    NOT NULL,          -- casefolded letters and digits
  video_id     INTEGER REFERENCES videos(id) ON DELETE SET NULL,  -- the liked talk
  talk_title   TEXT    NOT NULL,
  channel_id   TEXT, channel_name TEXT, channel_url TEXT,  -- their own channel, if any
  talks        TEXT    NOT NULL DEFAULT '[]',  -- JSON [{source_id, title, channel}], ≤ 3
  reason       TEXT    NOT NULL,
  state        TEXT    NOT NULL DEFAULT 'open' CHECK (state IN ('open','dismissed','followed')),
  created_at   INTEGER NOT NULL DEFAULT (unixepoch()),
  UNIQUE (owner_id, week),
  UNIQUE (owner_id, name_key)
) STRICT;

CREATE TABLE scout_runs (
  id          INTEGER PRIMARY KEY,
  owner_id    INTEGER NOT NULL DEFAULT 1 REFERENCES owners(id),
  day         TEXT    NOT NULL,           -- the box's local date
  state       TEXT    NOT NULL CHECK (state IN ('running','done','idle','blocked','failed')),
  started_at  INTEGER NOT NULL,
  finished_at INTEGER,
  requests    INTEGER NOT NULL DEFAULT 0,  -- YouTube requests the run made
  judged      INTEGER NOT NULL DEFAULT 0,
  shown       INTEGER NOT NULL DEFAULT 0,
  speaker     TEXT    CHECK (speaker IN ('suggested','none','blocked')),  -- NULL: not tried
  error       TEXT,
  UNIQUE (owner_id, day)
) STRICT;
