-- vidtheque migration 0019 — the weekly brief, its check-in and the skip audit
-- (companion.md §6.1).
--
-- Additive: three new tables, nothing existing is touched.


-- ------------------------------------------------------------------ briefs
--
-- One per owner per calendar week (Monday to Sunday, the box's local time),
-- keyed by its Monday and built on its Sunday. `body` is the part frozen when
-- it was built: the three picks, the skip-audit picks and what speakers said
-- about the top entries. The channel report and the profile changes are read
-- live, so a revert or a pause made since shows as it stands. `pushed_at`
-- makes the Sunday push happen once.

CREATE TABLE briefs (
  id         INTEGER PRIMARY KEY,
  owner_id   INTEGER NOT NULL DEFAULT 1 REFERENCES owners(id),
  week       TEXT    NOT NULL,                  -- YYYY-MM-DD, the Monday the week starts
  since_at   INTEGER NOT NULL,
  until_at   INTEGER NOT NULL,
  body       TEXT    NOT NULL DEFAULT '{}',
  model      TEXT,                              -- NULL when no model wrote a part of it
  created_at INTEGER NOT NULL DEFAULT (unixepoch()),
  pushed_at  INTEGER,
  UNIQUE (owner_id, week)
) STRICT;


-- ---------------------------------------------------------------- checkins
--
-- "Was last week's feed worth the time? 1–5", one answer per brief week; a
-- second answer replaces the first.

CREATE TABLE checkins (
  owner_id INTEGER NOT NULL DEFAULT 1 REFERENCES owners(id),
  week     TEXT    NOT NULL,
  rating   INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
  missing  TEXT    CHECK (missing IS NULL OR length(missing) <= 500),
  at       INTEGER NOT NULL DEFAULT (unixepoch()),
  PRIMARY KEY (owner_id, week)
) STRICT;


-- ----------------------------------------------------------- skip_verdicts
--
-- The owner's word on a skipped video: `wrong` ("I'd watch this") or `right`
-- (the skip was fair). `source` says where it was asked: the brief's audit of
-- three random skips, or the feed's skipped row. One per video; a second
-- answer replaces the first.

CREATE TABLE skip_verdicts (
  owner_id INTEGER NOT NULL DEFAULT 1 REFERENCES owners(id),
  video_id INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
  answer   TEXT    NOT NULL CHECK (answer IN ('right','wrong')),
  source   TEXT    NOT NULL CHECK (source IN ('audit','row')),
  score    INTEGER NOT NULL CHECK (score BETWEEN 0 AND 3),  -- the verdict's score when answered
  at       INTEGER NOT NULL DEFAULT (unixepoch()),
  PRIMARY KEY (owner_id, video_id)
) STRICT;

CREATE INDEX skip_verdicts_at ON skip_verdicts(owner_id, at);
