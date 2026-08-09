"""One story about indexing state, for every surface that prints one.

Three surfaces answered "is this corpus in flux?" three different ways in a
single session (`research/demo-queries-2026-08-09.md` §9.1.4): the context
resource said `active_jobs: 5, data_status: "indexing"`, `corpus-summary` said
`data_status: indexing` two lines above "0 videos currently indexing", and
`search`'s empty state said the index was fresh. All three were reading real
data — they were reading *different* real data, and none of them was reading the
one column that reconciles them.

That column is `jobs.not_before`. Five jobs were queued and **deferred**: the
backoff `claim_next` honours had put them beyond a future timestamp, so nothing
was running, nothing was about to run, and the queue was still correctly
non-empty. "Queued" was true; "indexing" was not. This module is the one place
that distinguishes the two, and the three surfaces derive their word from here
rather than each counting a different table.

Two counters, one name each — the other half of §9.1.4:

- **jobs** (`active` / `running` / `deferred`) count rows in `jobs`.
- **videos_indexing** counts videos in `index_state='indexing'`.

They answer different questions and are never again printed as if they were the
same number.

The deferral read is phase 2's, not a new one: `_JOB_SQL` already selects
`not_before` and `defer_s` (the remainder on the same clock the column was
written against), so `list_jobs` hands them over for free.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ..db import queries
from ..jobs import store as jobs_store
from ..text import iso_z
from .base import Deps

# How many active jobs we will read to split running from deferred. The split
# needs the rows themselves (`not_before` is per job), and this path runs on
# `corpus-summary`, on `vidtheque://context` and on an empty `search` — the
# three cheapest, most-called payloads in the surface. A queue deeper than this
# is unambiguously *working*, and the header says so without paying for the
# page: see `QueueState.truncated`.
QUEUE_PAGE_CAP = 25


@dataclass(frozen=True)
class QueueState:
    """What the job queue is doing, as opposed to what it contains."""

    active: int
    running: int
    deferred: int
    deferred_until: int | None
    videos_indexing: int
    truncated: bool = False

    @property
    def working(self) -> bool:
        """Is anything actually being indexed, or about to be?

        A queued job with no `not_before` in the future is work the runner picks
        up on its next tick — that counts. A deferred one does not.
        """
        if self.truncated:
            return bool(self.active)
        return bool(self.videos_indexing or self.running or (self.active - self.deferred))

    @property
    def deferred_only(self) -> bool:
        return bool(self.deferred) and not self.working

    def phrase(self) -> str | None:
        """The queue in one clause, or None when the queue is empty and idle.

        This is the sentence the field test asked for: "N jobs deferred until
        <time>, nothing running".
        """
        if self.truncated:
            return f"{self.active} indexing job(s) queued or running"
        parts: list[str] = []
        if self.running:
            parts.append(f"{self.running} job(s) indexing right now")
        ready = self.active - self.deferred - self.running
        if ready > 0:
            parts.append(f"{ready} job(s) queued and ready to run")
        if self.deferred:
            until = iso_z(self.deferred_until)
            deferred = f"{self.deferred} job(s) deferred"
            if until:
                deferred += f" until {until}"
            if not self.running and not ready:
                deferred += ", nothing running"
            parts.append(deferred)
        if self.videos_indexing:
            parts.append(f"{self.videos_indexing} video(s) mid-pipeline")
        return " · ".join(parts) if parts else None


EMPTY_QUEUE = QueueState(0, 0, 0, None, 0)


async def read_queue(deps: Deps, gap_info: Mapping[str, Any] | None = None) -> QueueState:
    """Split the active queue into running, ready-to-run and deferred.

    ``gap_info`` is `queries.gaps`, which every caller of this module has
    already read; it carries the *exact* active count, so the bounded page below
    only ever has to classify, never to count.
    """
    if gap_info is None:
        gap_info = await deps.db.read(queries.gaps)
    active = int(gap_info["active_jobs"])
    videos_indexing = int(gap_info["indexing"])
    if not active:
        return QueueState(0, 0, 0, None, videos_indexing)

    rows = await deps.db.read(
        lambda c: jobs_store.list_jobs(c, "active", QUEUE_PAGE_CAP)
    )
    if len(rows) < active:
        # More active jobs than we read: report the count, not a split we cannot
        # justify. `working` stays true, which is the honest reading of a queue
        # that deep.
        return QueueState(active, 0, 0, None, videos_indexing, truncated=True)

    running = sum(1 for r in rows if r["state"] == "running")
    horizons = [
        int(r["not_before"])
        for r in rows
        if r["state"] == "queued" and int(r["defer_s"] or 0) > 0
    ]
    return QueueState(
        active=active,
        running=running,
        deferred=len(horizons),
        deferred_until=min(horizons) if horizons else None,
        videos_indexing=videos_indexing,
    )


def status_word(
    total: int,
    gap_info: Mapping[str, Any],
    backlog: Mapping[str, Any],
    queue: QueueState,
) -> str:
    """`data_status`, derived once — `corpus-summary` §4.3's vocabulary.

    `deferred` is the value added 2026-08-09. It is an **extension** of the
    existing vocabulary, not a fifth one (dashboard.md §4.5 forbids that): the
    five other words are unchanged and keep their meanings, and this one names
    the state that used to be misreported as `indexing` — the queue is not
    empty, and nothing in it is running or about to.

    Precedence is worst-first, and `degraded` outranks `deferred` deliberately:
    a deferral is often the backoff *after* a failure, and "some data is missing
    now" beats "some work is scheduled for later".
    """
    if total == 0:
        return "empty"
    if queue.working:
        return "indexing"
    if gap_info["recent_failed_jobs"] or backlog["text"] or backlog["frame"]:
        return "degraded"
    if queue.deferred:
        return "deferred"
    if gap_info["transcript_no_ocr"]:
        return "partial"
    return "ok"


@dataclass(frozen=True)
class CorpusState:
    """`data_status` and the queue behind it, resolved together."""

    word: str
    queue: QueueState

    def activity_word(self) -> str:
        """`data_status` on the *activity* axis alone.

        `search`'s empty state answers "is the index settled?", not "is every
        video's coverage complete?" — `partial` and `degraded` are the coverage
        axis and belong to `corpus-summary`, which prints both. What the three
        surfaces must agree on, and did not, is whether anything is indexing.
        """
        return self.word if self.word in ("indexing", "deferred", "empty") else "ok"

    def freshness(self) -> str:
        """How a payload describes the index's currency in passing.

        `search`'s empty state used to hard-code "index fresh", which was the
        third of the three contradicting answers in §9.1.4.
        """
        return self.queue.phrase() or "index fresh"


async def read_corpus_state(
    deps: Deps,
    total: int,
    gap_info: Mapping[str, Any] | None = None,
    backlog: Mapping[str, Any] | None = None,
) -> CorpusState:
    """The whole story, for a caller that has not already read the parts."""
    if gap_info is None:
        gap_info = await deps.db.read(queries.gaps)
    if backlog is None:
        backlog = await deps.db.read(queries.embed_backlog)
    queue = await read_queue(deps, gap_info)
    return CorpusState(status_word(total, gap_info, backlog, queue), queue)
