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
    counts towards neither `n_done` nor `n_failed`."""


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
        # The heartbeat rides along with progress. A stage can legitimately run
        # for half an hour, and a job that reports progress but no heartbeat
        # would be requeued as crashed at the next boot.
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

    def __init__(self, db: Database, pipeline: Pipeline | None = None) -> None:
        self.db = db
        self.pipeline: Pipeline = pipeline or NotImplementedPipeline()
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    async def start(self) -> None:
        self._stop.clear()
        self._task = asyncio.create_task(self._loop(), name="vidtheque-pipeline")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

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
        job = await self.db.write(store.claim_next)
        if job is None:
            return False
        job_id = int(job["id"])
        await self.db.write(lambda c: store.log(c, job_id, "job claimed"))
        try:
            await self._drive(job_id, str(job["public_id"]))
        finally:
            await self.db.write(lambda c: _settle(c, job_id))
        return True

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
                item_id, message = ctx.item_id, str(skipped)
                await self.db.write(lambda c: store.finish_item(c, item_id, "skipped"))
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
    """All items terminal -> done, all failed -> failed."""
    row = conn.execute("SELECT state, n_items, n_done, n_failed FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if row is None or row["state"] not in {"running", "queued"}:
        return
    outstanding = int(
        conn.execute(
            "SELECT COUNT(*) FROM job_items WHERE job_id = ? AND state NOT IN "
            "('done','failed','skipped','cancelled')",
            (job_id,),
        ).fetchone()[0]
    )
    if outstanding:
        return
    if int(row["n_failed"]) and not int(row["n_done"]):
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
    else:
        store.finish_job(conn, job_id, "done")
