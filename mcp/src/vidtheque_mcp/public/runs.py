"""A visitor's asks, kept past a dropped connection (demo-site.md §3.6).

A phone that switches apps drops the stream, and the loop used to stop with it:
the answer was lost and the completions it had run were paid for. With a
visitor id the loop runs as its own task and every event it yields is kept, so
the same visitor asking the same question, or coming back to it, gets the
run's events from the start and then the rest live. A finished answer is kept
for `KEEP_S`. Per visitor, never shared (Tom, 2026-09-19): another visitor
asking the same words gets a run of their own.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Callable
from contextlib import aclosing
from typing import Any

logger = logging.getLogger(__name__)

# Long enough to switch apps, answer a message and come back; short enough
# that a restart is the only thing the kept answers ever cost.
KEEP_S = 1800.0
# Finished runs kept at once. Running ones are never evicted: the rate limits
# already bound how many can be in flight.
MAX_KEPT = 256

Key = tuple[str, str, str]


def run_key(visitor: str, question: str, tags: str | None) -> Key:
    """The same visitor's same question, however it was spaced or cased."""
    return (visitor, " ".join(question.split()).casefold(), tags or "")


class Run:
    """One loop's events, as they came, for every reader that asks for them."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self.done = False
        self.finished_at: float | None = None
        self._tick = asyncio.Event()
        self.task: asyncio.Task[None] | None = None

    @property
    def answered(self) -> bool:
        return bool(self.events) and self.events[-1].get("event") == "answer"

    def _emit(self, event: dict[str, Any]) -> None:
        self.events.append(event)
        self._wake()

    def _wake(self) -> None:
        tick, self._tick = self._tick, asyncio.Event()
        tick.set()

    async def follow(self) -> AsyncIterator[dict[str, Any]]:
        """Every event from the first, then each new one until the run ends."""
        i = 0
        while True:
            # Taken before the length is read, so an event emitted between the
            # read and the wait sets this very tick instead of being missed.
            tick = self._tick
            while i < len(self.events):
                yield self.events[i]
                i += 1
            if self.done:
                return
            await tick.wait()


class Runs:
    """The runs of one process. Lost on restart, which is the documented cost."""

    def __init__(self, keep_s: float = KEEP_S, max_kept: int = MAX_KEPT) -> None:
        self._keep_s = keep_s
        self._max_kept = max_kept
        self._runs: dict[Key, Run] = {}

    def get(self, key: Key) -> Run | None:
        """A run still going, or one that ended in an answer not yet expired."""
        self._sweep()
        return self._runs.get(key)

    def start(
        self,
        key: Key,
        source: AsyncIterator[dict[str, Any]],
        on_end: Callable[[], None],
    ) -> Run:
        """Run `source` to its end whoever is reading; `on_end` settles the bill.

        A run that ends in anything but an answer is dropped as it ends, so
        asking again starts afresh instead of replaying a refusal.
        """
        self._sweep()
        existing = self._runs.get(key)
        if existing is not None:
            return existing
        run = Run()
        self._runs[key] = run
        run.task = asyncio.create_task(self._drive(key, run, source, on_end))
        return run

    async def _drive(
        self,
        key: Key,
        run: Run,
        source: AsyncIterator[dict[str, Any]],
        on_end: Callable[[], None],
    ) -> None:
        try:
            async with aclosing(source) as events:  # type: ignore[type-var]
                async for event in events:
                    run._emit(event)
        except asyncio.CancelledError:
            raise
        except Exception:  # pragma: no cover - the source frames its own failures
            logger.exception("ask run: the loop failed outside its own handling")
        finally:
            run.done = True
            run.finished_at = time.monotonic()
            if not run.answered and self._runs.get(key) is run:
                del self._runs[key]
            try:
                on_end()
            finally:
                run._wake()

    def _sweep(self) -> None:
        now = time.monotonic()
        for key, run in list(self._runs.items()):
            if run.finished_at is not None and now - run.finished_at > self._keep_s:
                del self._runs[key]
        finished = sorted(
            (run.finished_at, key)
            for key, run in self._runs.items()
            if run.finished_at is not None
        )
        for _, key in finished[: max(0, len(finished) - self._max_kept)]:
            del self._runs[key]

    async def close(self) -> None:
        """Cancel what is still running, at shutdown, before its clients close."""
        tasks = [run.task for run in self._runs.values() if run.task and not run.task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
