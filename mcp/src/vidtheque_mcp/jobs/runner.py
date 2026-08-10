"""The pipeline seam.

Job *bookkeeping* lives here — rows are created, claimed, heartbeated, staged
and finished through the state machine in ``store.py`` — and the work itself
lives behind one method, ``Pipeline.run_item``. ``pipeline/runner.py`` is the
real implementation; ``NotImplementedPipeline`` is kept because a build that
cannot index should say so plainly rather than pretend, and because it is what
the tool-surface tests inject so no test can reach YouTube.

An item finishes in one of four ways, and each is a state the wire already has
a word for: it returns (``done``), raises ``ItemFailed`` with a typed code
(``failed``, or requeued when retryable), raises ``ItemCancelled`` at a stage
boundary (``cancelled``), or raises ``ItemSkipped`` because it was a container
that fanned out into other items (``skipped``).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import sqlite3
from dataclasses import dataclass
from typing import Protocol

from ..config import _int_env
from ..db import Database
from . import store

logger = logging.getLogger(__name__)

HEARTBEAT_INTERVAL_S = 30.0
POLL_INTERVAL_S = 2.0

# How long a job sits out after an item was rate-limited, when the source did
# not say. YouTube's 429s are measured in minutes, not seconds, and the point of
# a backoff is to stop *this* box asking — retrying in the same millisecond is
# how a soft block becomes a long one (research §5.5).
#
# 90 minutes, up from five (research/ytdlp-usage-audit-2026-08-10.md §1). The
# failure this catches is not really a 429: it is a bot-check, i.e. an IP-level
# block on logged-out access, and the waves measured on the reference box ran
# 60-90 minutes. At 300 s an item's whole retry budget fitted inside eleven
# minutes of that, so it retired without ever reaching YouTube while YouTube was
# answering. An estimate from observed waves, not an upstream constant.
DEFAULT_RATE_LIMIT_BACKOFF_S = 5400

# Every other retryable failure is a local hiccup (a worker restart, a disk
# blip); it gets a short pause so the loop cannot spin, not a cool-off.
DEFAULT_RETRY_BACKOFF_S = 5

# The most attempts a single item may be granted when — and only when — the
# ones it already spent went to `E_RATE_LIMIT`. See `_extend_for_rate_limit`:
# the schema ships three attempts, so this is three ordinary tries plus up to
# three the box's block paid for, and 6 x DEFAULT_RATE_LIMIT_BACKOFF_S is 7.5
# hours — comfortably longer than the 60-90 minute waves the audit measured,
# and still a number rather than "forever".
RATE_LIMIT_ATTEMPT_CEILING = 6

NOT_IMPLEMENTED_MESSAGE = (
    "the indexing pipeline is not implemented in this build: download, "
    "transcription, keyframes, OCR and embeddings are the next milestone. "
    "Nothing was fetched and nothing was written to the corpus."
)


class ItemFailed(Exception):
    """A per-item failure carrying the typed code job-status will show.

    ``retry_after_s`` is the source's own answer to "how long?" — a
    ``Retry-After`` header, or a 429 that named a window. When it is None the
    runner picks the default for the code.
    """

    def __init__(
        self,
        code: str,
        message: str,
        retryable: bool = False,
        retry_after_s: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.retry_after_s = retry_after_s


class ItemCancelled(Exception):
    """The pipeline saw ``cancel_requested`` at a stage boundary and stopped.

    Not a failure: the item is `cancelled`, the stages that finished stay
    finished, and the next loop iteration cancels whatever is left of the job.
    """


class ItemSkipped(Exception):
    """The item was not a video to index — it expanded into other items, or it
    duplicated one already in this job. `skipped` is a terminal state that
    counts towards neither `n_done` nor `n_failed`.

    It carries a code and lands on the row, because "skipped" with no reason is
    how a job that did nothing came to read as a job that did everything.
    """

    def __init__(self, message: str, code: str = "E_SKIPPED") -> None:
        super().__init__(message)
        self.code = code


class Pipeline(Protocol):
    """What a real pipeline must implement. One method, one item."""

    async def run_item(self, ctx: "ItemContext") -> None: ...


@dataclass
class ItemContext:
    """Everything a stage needs, plus the two things it must call.

    ``record(stage, pct)`` is what makes ``job-status`` show
    ``transcribe running 41%``; ``cancelled()`` is the cooperative cancellation
    check the contract promises is honoured at every stage boundary and inside
    the per-chunk loops.
    """

    db: Database
    job_id: int
    job_public_id: str
    item_id: int
    source_url: str
    video_id: int | None

    async def record(self, stage: str, pct: float) -> None:
        # The heartbeat rides along with progress, and `PipelineRunner._beat`
        # ticks it on a clock besides: a stage can legitimately run for half an
        # hour, and either alone would let a live job look crashed.
        await self.db.write(lambda c: _record(c, self.job_id, self.item_id, stage, pct))

    async def cancelled(self) -> bool:
        return bool(
            await self.db.read(
                lambda c: c.execute(
                    "SELECT cancel_requested FROM jobs WHERE id = ?", (self.job_id,)
                ).fetchone()[0]
            )
        )

    async def log(self, message: str, level: str = "info", stage: str | None = None) -> None:
        await self.db.write(
            lambda c: store.log(c, self.job_id, message, level, self.item_id, stage)
        )


class NotImplementedPipeline:
    """The placeholder. Fails every item, loudly and typed."""

    async def run_item(self, ctx: ItemContext) -> None:
        await ctx.record("fetch", 0.0)
        raise ItemFailed("E_NOT_IMPLEMENTED", NOT_IMPLEMENTED_MESSAGE, retryable=False)


class PipelineRunner:
    """Claims jobs, drives their items through a `Pipeline`, keeps the rollups."""

    def __init__(
        self,
        db: Database,
        pipeline: Pipeline | None = None,
        stale_after_s: int = store.DEFAULT_STALE_CLAIM_S,
        rate_limit_backoff_s: int | None = None,
    ) -> None:
        self.db = db
        self.pipeline: Pipeline = pipeline or NotImplementedPipeline()
        self.stale_after_s = stale_after_s
        self.rate_limit_backoff_s = (
            rate_limit_backoff_s
            if rate_limit_backoff_s is not None
            else _int_env("VIDTHEQUE_RATE_LIMIT_BACKOFF_S", DEFAULT_RATE_LIMIT_BACKOFF_S)
        )
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        # What this process is holding. A blocked event loop is alive, not
        # crashed: its own jobs are never candidates for its own sweep.
        self._active: set[int] = set()

    async def start(self) -> None:
        self._stop.clear()
        # Startup sweep: whatever the previous process was holding when it died
        # is nobody's work until it goes back on the queue.
        await self.reclaim_stale()
        self._task = asyncio.create_task(self._loop(), name="vidtheque-pipeline")

    async def stop(self) -> None:
        """Cancel the loop, then give back every claim this process was holding.

        Cancellation bypasses the item handlers — `CancelledError` is not an
        `Exception` — so `_settle` found the item still `running` and returned,
        leaving a `running` job with a heartbeat seconds old. A restart quicker
        than the staleness window correctly refused to touch it (a fresh
        heartbeat is what a live job looks like), so the work sat wedged for the
        full 300 s and the next submission got `E_INDEXING` the whole time.

        The snapshot is taken *before* the cancel: `run_once`'s `finally` clears
        `_active` on the way out, and by then this is the only record of what was
        held.
        """
        self._stop.set()
        held = tuple(self._active)
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        if held:
            released = await self.db.write(lambda c: store.release_claims(c, held))
            if released:
                logger.info("released on stop: %s", ", ".join(released))

    async def reclaim_stale(self) -> list[str]:
        """Requeue every claim whose heartbeat has gone quiet. Returns job ids.

        The read comes first on purpose: this runs before every claim, and an
        idle server should not take a write lock every poll to learn there is
        nothing to reclaim.
        """
        held = tuple(self._active)
        stale = await self.db.read(
            lambda c: store.stale_claims(c, self.stale_after_s, held)
        )
        if not stale:
            return []
        reclaimed = await self.db.write(
            lambda c: store.reclaim_stale(c, self.stale_after_s, held)
        )
        if reclaimed:
            logger.warning("reclaimed stale job(s): %s", ", ".join(reclaimed))
        return reclaimed

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                worked = await self.run_once()
            except Exception:  # pragma: no cover - the loop must not die
                logger.exception("pipeline runner iteration failed")
                worked = False
            if not worked:
                with contextlib.suppress(TimeoutError, asyncio.TimeoutError):
                    await asyncio.wait_for(self._stop.wait(), timeout=POLL_INTERVAL_S)

    async def run_once(self) -> bool:
        """Claim at most one job and run it to completion. Returns True if it did."""
        await self.reclaim_stale()
        job = await self.db.write(store.claim_next)
        if job is None:
            return False
        job_id = int(job["id"])
        self._active.add(job_id)
        await self.db.write(lambda c: store.log(c, job_id, "job claimed"))
        beat = asyncio.create_task(self._beat(job_id), name=f"vidtheque-heartbeat-{job_id}")
        try:
            await self._drive(job_id, str(job["public_id"]))
        finally:
            beat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await beat
            self._active.discard(job_id)
            await self.db.write(lambda c: _settle(c, job_id))
        return True

    async def _beat(self, job_id: int) -> None:
        """Say "still here" on a clock, not only when a stage reports progress.

        A single whisperX call on an hour of audio is minutes of silence
        between two ``record()`` calls, and a job that only heartbeats through
        progress would be reclaimed out from under itself.
        """
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL_S)
            await self.db.write(lambda c: store.heartbeat(c, job_id))

    async def _drive(self, job_id: int, job_public_id: str) -> None:
        while True:
            if await self._cancel_requested(job_id):
                await self.db.write(lambda c: _cancel_remaining(c, job_id))
                return
            item = await self.db.write(lambda c: store.claim_item(c, job_id))
            if item is None:
                return
            await self.db.write(lambda c: store.heartbeat(c, job_id))
            ctx = ItemContext(
                db=self.db,
                job_id=job_id,
                job_public_id=job_public_id,
                item_id=int(item["id"]),
                source_url=str(item["source_url"]),
                video_id=int(item["video_id"]) if item["video_id"] is not None else None,
            )
            try:
                await self.pipeline.run_item(ctx)
            except ItemFailed as failure:
                if await self._fail_item(item, failure):
                    return  # deferred: the job is queued again, not now
            except ItemCancelled:
                item_id = ctx.item_id
                await self.db.write(lambda c: store.finish_item(c, item_id, "cancelled"))
            except ItemSkipped as skipped:
                item_id, message, code = ctx.item_id, str(skipped), skipped.code
                await self.db.write(
                    lambda c: store.finish_item(c, item_id, "skipped", code, message)
                )
                await self.db.write(lambda c: store.log(c, job_id, message, "info", item_id))
            except Exception as exc:  # pragma: no cover - unexpected
                logger.exception("pipeline item crashed")
                if await self._fail_item(
                    item, ItemFailed("E_INTERNAL", str(exc), retryable=True)
                ):
                    return
            else:
                item_id = ctx.item_id
                await self.db.write(lambda c: store.finish_item(c, item_id, "done"))

    async def _fail_item(self, item: sqlite3.Row, failure: ItemFailed) -> bool:
        """Requeue-with-backoff or retire. True means the job was deferred.

        A retryable item goes back on the queue *and takes its job off the
        runnable set until the backoff expires*: requeueing alone left the item
        as the lowest-seq candidate of a still-running job, so the next line of
        `_drive` handed it straight back out. Three attempts against a 429 in
        under a second, then the same against every URL behind it.
        """
        item_id = int(item["id"])
        job_id = int(item["job_id"])
        attempts = int(item["attempts"])
        max_attempts = int(item["max_attempts"])
        if failure.code == "E_RATE_LIMIT" and attempts >= max_attempts:
            max_attempts = await self._extend_for_rate_limit(
                job_id, item_id, attempts, max_attempts
            )
        if failure.retryable and attempts < max_attempts:
            delay = self._backoff_for(failure)
            await self.db.write(lambda c: store.requeue_item(c, item_id))
            await self.db.write(
                lambda c: store.log(
                    c,
                    job_id,
                    f"retrying in {delay}s after {failure.code}: {failure.message}",
                    "warn",
                    item_id,
                )
            )
            code, message = self._sticky_error(failure, delay)
            await self.db.write(
                lambda c: store.defer_job(c, job_id, delay, code, message)
            )
            return True
        await self.db.write(
            lambda c: store.finish_item(c, item_id, "failed", failure.code, failure.message)
        )
        await self.db.write(
            lambda c: store.log(c, job_id, f"{failure.code}: {failure.message}", "error", item_id)
        )
        return False

    async def _extend_for_rate_limit(
        self, job_id: int, item_id: int, attempts: int, max_attempts: int
    ) -> int:
        """Grant an exhausted item one more attempt, *because* it was the box.

        The retry budget answers "is this video indexable?". A rate-limit
        deferral does not answer that question — every other item in the queue
        is failing the same way at the same moment — so spending the budget on
        it retires videos for something they did not do. That is exactly what
        the 2026-08-09 waves did: the old 300 s cool-off fitted three attempts
        into 11 minutes of a 60-90 minute block, and items came out `failed`
        having never once reached YouTube while it was answering
        (research/ytdlp-usage-audit-2026-08-10.md §1).

        An item must be able to outlive a wave, so the extension is granted
        lazily — only when the ordinary budget is spent, only for
        `E_RATE_LIMIT`, and only up to `RATE_LIMIT_ATTEMPT_CEILING`. Bounded,
        because "retry until it works" is how a soft block becomes a long one,
        and because the deferral is the job's countdown too: at the shipped
        5400 s cool-off, six attempts span 7.5 hours of wave.

        It moves `max_attempts` rather than refunding `attempts` on purpose.
        The counter the dashboard renders (§4.4) stays a truthful count of
        tries; what changes is the allowance, which is the thing that actually
        changed. A row that reads `4 / 4` says "this waited out three cool-offs
        and is still trying", which a refunded `3 / 3` would have hidden.
        """
        if max_attempts >= RATE_LIMIT_ATTEMPT_CEILING:
            return max_attempts
        granted = max_attempts + 1
        await self.db.write(lambda c: store.set_max_attempts(c, item_id, granted))
        await self.db.write(
            lambda c: store.log(
                c,
                job_id,
                f"the source rate-limited this box on attempt {attempts} of "
                f"{max_attempts}; that is the box's fault and not this video's, so "
                f"the item gets attempt {granted} of {granted} rather than being "
                f"retired mid-block (ceiling {RATE_LIMIT_ATTEMPT_CEILING})",
                "warn",
                item_id,
            )
        )
        return granted

    def _backoff_for(self, failure: ItemFailed) -> int:
        if failure.retry_after_s is not None:
            return max(0, int(failure.retry_after_s))
        if failure.code == "E_RATE_LIMIT":
            return self.rate_limit_backoff_s
        return DEFAULT_RETRY_BACKOFF_S

    def _sticky_error(self, failure: ItemFailed, delay: int) -> tuple[str | None, str | None]:
        """Only rate limiting outlives the retry that fixed it.

        A worker blip that the next attempt rides through is not something the
        finished job needs to carry; being rate-limited is, because the caller's
        *next* job is the one that pays for ignoring it.
        """
        if failure.code != "E_RATE_LIMIT":
            return None, None
        return "E_RATE_LIMIT", (
            f"the source rate-limited this box during the job; it backed off {delay}s "
            f"before retrying — {failure.message}"
        )

    async def _cancel_requested(self, job_id: int) -> bool:
        return bool(
            await self.db.read(
                lambda c: c.execute(
                    "SELECT cancel_requested FROM jobs WHERE id = ?", (job_id,)
                ).fetchone()[0]
            )
        )


def _record(conn: sqlite3.Connection, job_id: int, item_id: int, stage: str, pct: float) -> None:
    store.record_stage(conn, item_id, stage, pct)
    store.heartbeat(conn, job_id)


def _cancel_remaining(conn: sqlite3.Connection, job_id: int) -> None:
    conn.execute(
        "UPDATE job_items SET state = 'cancelled', finished_at = unixepoch() "
        "WHERE job_id = ? AND state IN ('queued','running')",
        (job_id,),
    )
    store.finish_job(conn, job_id, "cancelled")


def _settle(conn: sqlite3.Connection, job_id: int) -> None:
    """All items terminal -> done, all failed -> failed, none productive -> said so.

    The counts come off ``job_items``, not off the trigger rollup, and the
    third branch is the one this grew: a job whose every item was *skipped* is
    terminal with nothing indexed, and calling that plain `done` is how
    ``job-status`` came to promise a video that was never fetched.
    """
    row = conn.execute(
        "SELECT state, error_code, error_message FROM jobs WHERE id = ?", (job_id,)
    ).fetchone()
    if row is None or row["state"] not in {"running", "queued"}:
        return
    counts = store.item_counts(conn, job_id)
    if sum(n for state, n in counts.items() if state not in store.TERMINAL):
        return
    done, failed = counts.get("done", 0), counts.get("failed", 0)
    code, message = _job_error(conn, job_id, row)
    if failed and not done:
        store.finish_job(conn, job_id, "failed", code, message)
        return
    if done:
        # Partial success is still partial: the codes the items carry survive
        # into the job, because "nine of ten, and the tenth was rate-limited" is
        # a different instruction to an unattended driver than "done".
        store.finish_job(conn, job_id, "done", code, message)
        return
    reasons = store.nonproductive_reasons(conn, job_id)
    detail = "; ".join(reasons) or "the job had no items to run"
    store.finish_job(
        conn,
        job_id,
        "done",
        "E_NOTHING_INDEXED",
        f"no video was indexed: every item was skipped or superseded — {detail}",
    )


def _job_error(
    conn: sqlite3.Connection, job_id: int, row: sqlite3.Row
) -> tuple[str | None, str | None]:
    """Which of the job's failures speaks for it. `E_RATE_LIMIT` speaks first.

    It outranks a later failure *and* a sibling success: whether the item that
    hit the 429 eventually got through says nothing about whether the next job
    should start immediately. The sticky code written by `defer_job` is honoured
    even when no item ended up failing at all.
    """
    rate_limited = store.first_failure(conn, job_id, "E_RATE_LIMIT")
    if rate_limited is not None:
        return "E_RATE_LIMIT", str(rate_limited["error_message"] or "")
    if str(row["error_code"] or "") == "E_RATE_LIMIT":
        return "E_RATE_LIMIT", row["error_message"]
    first = store.first_failure(conn, job_id)
    if first is not None:
        return first["error_code"], first["error_message"]
    return None, None
