-- vidtheque migration 0007 — the daily follow budget stops being refundable.
--
-- Tom's decision of 2026-08-28, answering following.md §11.7. The budget was a
-- sum over the ledger, `follow_seen`, and `collections` cascades to that table.
-- So an unfollow deleted the record of what the day had already spent: a follow
-- that accepted fifteen of the sixteen hours this morning and is deleted this
-- afternoon hands those hours back, and the box can run to roughly twice its
-- ceiling inside one rolling day. On a machine whose GPU is leased from a
-- co-tenant and whose yt-dlp requests are the thing that gets it blocked, that
-- ceiling is the whole point of the feature.
--
-- What made it reachable rather than theoretical: `follow-channel` has five
-- actions and none of them is `edit` (tools/follows.py). Rules can only be
-- changed on the dashboard, so a caller driving this box through MCP alone
-- changes a rule by unfollowing and following again. The refund is not on a
-- rare path for them. It is on the only path they have.
--
--
-- WHY A SECOND TABLE AND NOT A NULLABLE OWNER ON THE LEDGER
-- --------------------------------------------------------------------------
-- The cheaper fix was `follow_seen.collection_id` nullable with
-- `ON DELETE SET NULL` and a "belongs to a live follow" clause on every read.
-- It is a smaller diff and it was rejected: `follow_seen` is *rendered*. The
-- follow detail page reads it newest-first as the band of what a follow passed
-- over, and nulling the owner leaves rows on a ledger that no surface has an
-- honest sentence for — a decision about a follow that is not there. This
-- codebase's posture is that a surface never shows something it cannot explain.
--
-- The other reason is smaller and outlasts this decision: the budget stopped
-- being a property of a table that exists for something else. `follow_seen` is
-- there to stop a check reconsidering the same upload every six hours for a
-- year (0006). That it also happened to be summable was a coincidence, and the
-- next change to the ledger would have had to remember it.
--
--
-- WHY THE ROWS SURVIVE THEIR FOLLOW, AND WHY THAT IS NOT THE SAME PROBLEM
-- --------------------------------------------------------------------------
-- `collection_id` is `ON DELETE SET NULL` here, which is the same mechanism
-- rejected above. The difference is that nothing renders this table as a list.
-- It is the budget's own arithmetic, read only as a SUM over a window, and an
-- orphan row is not a decision nobody can explain — it is an hour that really
-- was spent, by a follow that is gone. That is exactly what the ceiling needs
-- to keep believing.
--
--
-- RETENTION
-- --------------------------------------------------------------------------
-- The budget window is 24 hours; the rows are kept for 30 days, which is the
-- clock `job_events` and `ask_budget` already use, so there is one retention
-- story on this box rather than three. The extra 29 days are not waste: they
-- are the only record of what following actually cost last week once the
-- ledger row has been re-decided or the follow deleted.
--
-- The prune runs when a check is enqueued (follows/scheduler.py) rather than at
-- boot. Rows arrive only when a check queues something, so pruning on the same
-- event is self-balancing: a box with no active follows adds nothing and needs
-- to delete nothing.

CREATE TABLE follow_spend (
  id            INTEGER PRIMARY KEY,
  -- NULL means "the follow that spent this is gone". See above: the hour is
  -- still spent.
  collection_id INTEGER REFERENCES collections(id) ON DELETE SET NULL,
  -- Kept for reading the table by hand when a day's arithmetic is disputed.
  -- Not unique: a candidate re-queued after its video row was deleted really
  -- did cost a second download, and the sum should say so.
  source_id     TEXT    NOT NULL,
  -- Seconds of video accepted, exactly the number the check added to its
  -- running total. An unknown duration is written as 0.0 rather than skipped,
  -- so the row count stays "candidates accepted" while the sum stays honest
  -- about what it can measure (0006 on `duration_s`; following.md §5).
  duration_s    REAL    NOT NULL,
  spent_at      INTEGER NOT NULL DEFAULT (unixepoch())
) STRICT;

-- The only query: SUM(duration_s) over a rolling window. The prune uses it too.
CREATE INDEX follow_spend_window ON follow_spend(spent_at);

-- Carry today's spend across the upgrade. Without this, the first check after a
-- deploy reads an empty table and grants a fresh sixteen hours to a day that
-- may already have spent them — the same refund this migration exists to close,
-- handed out once by the fix itself. Thirty days rather than one so the new
-- table starts with the history its retention window promises.
INSERT INTO follow_spend (collection_id, source_id, duration_s, spent_at)
SELECT collection_id, source_id, COALESCE(duration_s, 0.0), decided_at
  FROM follow_seen
 WHERE decision = 'queued' AND decided_at > unixepoch() - 2592000;
