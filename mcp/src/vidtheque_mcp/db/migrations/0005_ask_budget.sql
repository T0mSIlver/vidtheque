-- vidtheque migration 0005 — the daily ask budget survives a restart.
--
-- Tom's decision of 2026-08-09 (evening): the model behind `/api/ask` is paid,
-- and until now the 50/day cap lived in a Python dict. A redeploy reset it. So
-- the guard held money only until the next deploy — which, on a day when the
-- site is being deployed repeatedly, is no guard at all.
--
-- demo-site.md §4.2 always said the in-memory limiter was deliberate and named
-- this exact caveat ("for a budget guard on a free tier that is acceptable; for
-- anything where money is at stake it would not be"). Money is at stake now.
--
--
-- WHY A DAY-KEYED COUNTER AND NOT A SERIALISED TOKEN BUCKET
-- --------------------------------------------------------------------------
-- The minute buckets refill continuously from `time.monotonic()`, and a
-- monotonic clock has no meaning across a process boundary — there is no
-- honest way to write one down and read it back. Storing wall-clock instead
-- would make the *rate limiter* dependent on the system clock being sane,
-- which is a worse trade than the one made here.
--
-- A daily budget has a natural durable form that a refilling bucket does not:
-- how much of today has been spent. So `ask_global` becomes a UTC-day counter
-- — `spent` against a capacity, reset by the date changing rather than by
-- trickle. The minute buckets are untouched and stay in memory: they guard
-- against hammering, they are meaningless after a restart, and nothing about
-- them costs money.
--
-- The visible consequence, stated in demo-site.md §4.2: a spent day now
-- unblocks at UTC midnight instead of trickling back over the afternoon. That
-- is the cost of being able to state the budget at all, and for a *daily*
-- budget it is also the more legible rule.
--
--
-- WHY THE CORPUS DATABASE AND NOT A SECOND FILE
-- --------------------------------------------------------------------------
-- `auth/store.py` keeps its own file so a corpus rebuild never touches
-- credentials. The opposite argument applies here: a budget is not a secret,
-- it is one row a day, and the mcp process already owns exactly one writer on
-- this file (§5). A second writable file would be a second thing to back up
-- and a second thing to forget.
--
-- Rows are pruned to the last 30 days at boot, which leaves the operator a
-- month of "how much did the demo actually cost" and bounds the table at a
-- size no query will ever notice.

CREATE TABLE ask_budget (
  -- The limiter's own key, exactly: a bucket name and the client it was
  -- charged to. `ask_global` is charged to the literal '@global'; the column
  -- exists so a per-IP daily bucket would need no migration to persist.
  bucket     TEXT    NOT NULL,
  client     TEXT    NOT NULL,
  -- UTC 'YYYY-MM-DD'. A text date rather than an epoch day so the table reads
  -- correctly in `sqlite3` with no arithmetic, and so the rollover rule is the
  -- string changing rather than a division somebody has to get right twice.
  day        TEXT    NOT NULL,
  -- Charges minus refunds, floored at zero — a refund can never mint budget,
  -- which is the same rule the in-memory bucket enforces by capping at
  -- capacity.
  spent      INTEGER NOT NULL DEFAULT 0 CHECK (spent >= 0),
  updated_at INTEGER NOT NULL DEFAULT (unixepoch()),
  PRIMARY KEY (bucket, client, day)
) WITHOUT ROWID, STRICT;
