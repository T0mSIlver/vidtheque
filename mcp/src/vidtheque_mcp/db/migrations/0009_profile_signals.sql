-- vidtheque migration 0009 — the interest profile and the signals it learns from.
--
-- companion.md §2.1 and §2.3. Additive only: three new tables and their
-- indexes, nothing existing is touched, so a populated corpus upgrades with
-- every `videos`/`cues` row exactly as it was.
--
-- The profile is a list of plain-word entries rather than a vector, so it can
-- be read, edited and reverted. Every write goes through `profile_events`,
-- which is why a revert is possible at all: an event carries the entry's
-- state before and after, and undoing it is writing `before` back.


-- --------------------------------------------------------- profile_entries
--
-- `source` is the actor that created the entry, in the same vocabulary as
-- `profile_events.actor`. The evidence for an entry ("4 asks this week") is the
-- `reason` of its `add` event, not a second free-text column here.
-- A retired entry keeps its row: the history and a revert both need it.

CREATE TABLE profile_entries (
  id         INTEGER PRIMARY KEY,
  owner_id   INTEGER NOT NULL DEFAULT 1 REFERENCES owners(id),
  text       TEXT    NOT NULL CHECK (length(text) BETWEEN 1 AND 200),
  weight     REAL    NOT NULL CHECK (weight BETWEEN -1 AND 1),
  source     TEXT    NOT NULL CHECK (source IN ('owner','agent','nightly','app')),
  created_at INTEGER NOT NULL DEFAULT (unixepoch()),
  retired_at INTEGER
) STRICT;

-- The live list, which is what every read and the 40-entry cap count.
CREATE INDEX profile_entries_live ON profile_entries(owner_id) WHERE retired_at IS NULL;


-- ---------------------------------------------------------- profile_events
--
-- `before` / `after` are JSON snapshots of the entry, `{"text", "weight",
-- "live"}`, or NULL where the entry did not exist yet. The highest event id
-- touching an owner's entries is that profile's revision.

CREATE TABLE profile_events (
  id       INTEGER PRIMARY KEY,
  at       INTEGER NOT NULL DEFAULT (unixepoch()),
  actor    TEXT    NOT NULL CHECK (actor IN ('owner','agent','nightly','app')),
  op       TEXT    NOT NULL CHECK (op IN ('add','drop','reweight','revert')),
  entry_id INTEGER NOT NULL REFERENCES profile_entries(id),
  before   TEXT,
  after    TEXT,
  reason   TEXT
) STRICT;

CREATE INDEX profile_events_by_entry ON profile_events(entry_id, id);


-- ----------------------------------------------------------------- signals
--
-- `owner_id` is not in §2.3's column list; it is here because the signals are
-- owner-scoped and every other owned table carries it. `client` is the MCP
-- client id or `app`. A video that is deleted takes its signals with it: a read
-- of something no longer in the corpus says nothing the profile can use.

CREATE TABLE signals (
  id       INTEGER PRIMARY KEY,
  owner_id INTEGER NOT NULL DEFAULT 1 REFERENCES owners(id),
  at       INTEGER NOT NULL DEFAULT (unixepoch()),
  kind     TEXT    NOT NULL
           CHECK (kind IN ('mcp_search','mcp_read','ask_claude','thumb_up',
                           'thumb_down','mute','open','watch','dismiss')),
  video_id INTEGER REFERENCES videos(id) ON DELETE CASCADE,
  offset_s REAL,
  text     TEXT,
  client   TEXT
) STRICT;

-- The nightly update reads one day of them; retention deletes by the same key.
CREATE INDEX signals_at ON signals(owner_id, at);
CREATE INDEX signals_retention ON signals(at);
