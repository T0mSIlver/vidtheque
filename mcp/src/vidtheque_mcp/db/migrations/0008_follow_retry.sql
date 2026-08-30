-- vidtheque migration 0008 — a `failing` follow retries for a week, then stops.
--
-- Tom's decision of 2026-08-28, answering following.md §11.4. A follow whose
-- channel listing raised `SourceError` was set `failing` and stayed there until
-- a human pressed Resume. `due()` selects active follows, so nothing scheduled
-- it again and nothing ever would.
--
-- That is right for the case it was written for — a renamed or deleted channel,
-- where no amount of waiting helps — and wrong for the two cases that raise the
-- same exception: a channel that went private for an afternoon, and a broken
-- yt-dlp build. This repo has already paid for the second one; the exact nightly
-- pin in docs/LESSONS.md is what it cost. Both recover on their own, and both
-- left a follow that quietly indexed nothing until somebody happened to open the
-- Following page.
--
--
-- WHY BOUNDED, AND NOT SIMPLY "RETRY DAILY"
-- --------------------------------------------------------------------------
-- §11.4's own objection to the daily retry is the right one: it is also how a
-- follow keeps making requests forever against a channel that will never work
-- again, on a box that gets blocked for asking too fast. So the retry is
-- counted. `fail_count` is consecutive failed checks, reset by the first check
-- that completes; at `FAILING_MAX_TRIES` (7, a week at one a day) the follow
-- stops being due and waits for a human, which is the old behaviour arrived at
-- by evidence rather than by the first failure.
--
--
-- WHY NO NEW STATE WORD
-- --------------------------------------------------------------------------
-- `failing` now covers two situations — still trying, and gave up — and the
-- obvious move is a fourth value in the `state` CHECK. It is not taken.
-- PRODUCT.md's rule is that no surface invents a state vocabulary, and the
-- distinction here is not a state: it is a number, and the number is the
-- receipt. The surfaces print `retry 2 of 7` or `gave up after 7 tries` from
-- `fail_count`, the same way a skip prints `4:12, shorter than your 8:00
-- floor` instead of the word "short".
--
--
-- THE INDEX
-- --------------------------------------------------------------------------
-- `follows_due` was partial on `state = 'active'`, which is exactly the
-- predicate that is changing. It is dropped and rebuilt rather than joined by a
-- second index, so there is still one answer to "which follows are due".

ALTER TABLE follows ADD COLUMN fail_count INTEGER NOT NULL DEFAULT 0
  CHECK (fail_count >= 0);

DROP INDEX follows_due;
CREATE INDEX follows_due ON follows(next_check_at)
  WHERE state IN ('active', 'failing');

-- Existing `failing` rows arrive at `fail_count = 0` and are therefore due
-- again, most of them immediately: their `next_check_at` is in the past. That
-- is deliberate — the upgrade gives every follow that was parked by the old
-- rule one week of retries, which is the whole point — and it is bounded twice:
-- `MAX_ENQUEUED_PER_TICK` caps the burst, and a channel that is really gone
-- settles back into `failing` after seven days without anyone watching it.
