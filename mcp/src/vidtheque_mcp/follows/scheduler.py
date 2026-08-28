"""Turning a due follow into a job. The whole clock, and it is nine lines of it.

There is no timer here and no second loop. `PipelineRunner` already wakes on a
poll interval to look for work; this runs on that same tick, just before the
claim, and its only job is to put a `follow_check` row on the queue when one is
owed. Everything after that — ordering, backoff, heartbeats, crash recovery,
the war-story page — is the queue's, already built and already tested.

`priority=10` is the one number worth arguing about. A check is a listing
request and finishes in about a second; an index item is minutes of GPU. At the
default priority a check enqueued behind an overnight batch would run when the
batch finished, which is to say hours after it was due, and the follow would
report a stale clock through no fault of its own. Ten is far enough ahead of
`high` (50) that a check never waits on indexing, and it costs the queue
nothing because there is nothing to wait *for*.
"""

from __future__ import annotations

import logging
import sqlite3

from ..jobs import store as jobs_store

logger = logging.getLogger(__name__)

# Lower runs first (`jobs.priority`); `high` is 50 and `normal` is 100.
CHECK_PRIORITY = 10

# How many follows may be made due in one tick. Not a cap on how many follows
# an operator may have: it bounds the burst after a long downtime, when every
# follow is overdue at once and each check is a request against a source that
# blocks boxes for asking too fast. The rest stay due and go on the next tick.
MAX_ENQUEUED_PER_TICK = 3


async def enqueue_due(db: object, *, enabled: bool = True, limit: int = MAX_ENQUEUED_PER_TICK) -> int:
    """Queue a `follow_check` for every follow whose clock has come round.

    Returns how many were enqueued. Safe to call on every tick: a follow with a
    check already queued or running is skipped, so a check that is deferred
    behind a ninety-minute rate-limit backoff is not joined by a fresh one
    every two seconds.
    """
    if not enabled:
        return 0
    return int(await db.write(lambda c: _enqueue(c, limit)))  # type: ignore[attr-defined]


def _enqueue(conn: sqlite3.Connection, limit: int) -> int:
    from . import store

    enqueued = 0
    for follow in store.due(conn, limit):
        collection_id = int(follow["collection_id"])
        if store.check_in_flight(conn, collection_id) is not None:
            # Already on the queue. Push the clock out by one interval anyway,
            # so a check wedged behind a long backoff cannot be re-examined on
            # every one of the thousands of ticks in between.
            store.schedule_next(conn, collection_id, int(follow["check_interval_s"]))
            continue
        jobs_store.create_job(
            conn,
            "follow_check",
            {"follow": str(follow["slug"]), "collection_id": collection_id},
            [jobs_store.NewItem(str(follow["source_url"]))],
            priority=CHECK_PRIORITY,
            collection_id=collection_id,
        )
        # The clock moves when the check is *queued*, not when it finishes. A
        # check that fails still owes its next one an interval — otherwise a
        # channel that 404s is re-checked on every tick forever.
        store.schedule_next(conn, collection_id, int(follow["check_interval_s"]))
        enqueued += 1
    if enqueued:
        # `follow_spend` retention, on the event that fills it rather than at
        # boot. Rows arrive only when a check queues something, so pruning on
        # the same event is self-balancing: a box with no active follows adds
        # nothing and has nothing to delete. It also costs a `DELETE` on an
        # indexed range at most once per check, which is hours apart.
        store.prune_spend(conn)
    return enqueued
