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

from ..db import Database
from . import store

logger = logging.getLogger(__name__)

HEARTBEAT_INTERVAL_S = 30.0
POLL_INTERVAL_S = 2.0

NOT_IMPLEMENTED_MESSAGE = (
    "the indexing pipeline is not implemented in this build: download, "
    "transcription, keyframes, OCR and embeddings are the next milestone. "
    "Nothing was fetched and nothing was written to the corpus."
)


class ItemFailed(Exception):
    """A per-item failure carrying the typed code job-status will show."""

    def __init__(self, code: str, message: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


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
    ) -> None:
        self.db = db
        self.pipeline: Pipeline = pipeline or NotImplementedPipeline()
        self.stale_after_s = stale_after_s
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
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

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
                await self._fail_item(item, failure)
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
                await self._fail_item(item, ItemFailed("E_INTERNAL", str(exc), retryable=True))
            else:
                item_id = ctx.item_id
                await self.db.write(lambda c: store.finish_item(c, item_id, "done"))

    async def _fail_item(self, item: sqlite3.Row, failure: ItemFailed) -> None:
        item_id = int(item["id"])
        job_id = int(item["job_id"])
        attempts = int(item["attempts"])
        max_attempts = int(item["max_attempts"])
        if failure.retryable and attempts < max_attempts:
            await self.db.write(lambda c: store.requeue_item(c, item_id))
            await self.db.write(
                lambda c: store.log(
                    c, job_id, f"retrying after {failure.code}: {failure.message}", "warn", item_id
                )
            )
            return
        await self.db.write(
            lambda c: store.finish_item(c, item_id, "failed", failure.code, failure.message)
        )
        await self.db.write(
            lambda c: store.log(c, job_id, f"{failure.code}: {failure.message}", "error", item_id)
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
    row = conn.execute("SELECT state FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None or row["state"] not in {"running", "queued"}:
        return
    counts = store.item_counts(conn, job_id)
    if sum(n for state, n in counts.items() if state not in store.TERMINAL):
        return
    done, failed = counts.get("done", 0), counts.get("failed", 0)
    if failed and not done:
        first = conn.execute(
            "SELECT error_code, error_message FROM job_items WHERE job_id = ? "
            "AND state = 'failed' ORDER BY seq LIMIT 1",
            (job_id,),
        ).fetchone()
        store.finish_job(
            conn,
            job_id,
            "failed",
            first["error_code"] if first else None,
            first["error_message"] if first else None,
        )
        return
    if done:
        store.finish_job(conn, job_id, "done")
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
