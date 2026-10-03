-- vidtheque migration 0015 — one row per model completion (companion.md §4.1).
--
-- Additive: one new table. The shared client (`llm.py`) writes a row for every
-- call, whatever its outcome. Token counts are what the backend reported, NULL
-- when it reported nothing. `cost_micro_usd` is fixed at write time from the
-- list price then configured (or the backend's own figure), so a later price
-- change never rewrites history; NULL means unknown, never free.
--
-- `outcome`: `ok`, `cancelled`, or the client's failure reason
-- (`invalid_output`, `upstream_unavailable`, `upstream_rejected`,
-- `upstream_rate_limited`, `not_configured`). `video_id` is cleared, not
-- cascaded, when its video is deleted: the money was spent either way.

CREATE TABLE llm_calls (
  id                INTEGER PRIMARY KEY,
  owner_id          INTEGER NOT NULL DEFAULT 1 REFERENCES owners(id),
  at                INTEGER NOT NULL,                -- unix seconds, when the call started
  purpose           TEXT    NOT NULL,                -- verdict, verdict_explore, nightly_update, unknown
  video_id          INTEGER REFERENCES videos(id) ON DELETE SET NULL,
  backend           TEXT    NOT NULL,                -- api, claude-code, codex
  model             TEXT,
  prompt_tokens     INTEGER,                         -- includes cached_tokens
  completion_tokens INTEGER,                         -- includes reasoning_tokens
  cached_tokens     INTEGER,
  reasoning_tokens  INTEGER,
  latency_ms        INTEGER NOT NULL,
  outcome           TEXT    NOT NULL,
  cost_micro_usd    INTEGER
) STRICT;
CREATE INDEX llm_calls_by_at ON llm_calls(owner_id, at);
