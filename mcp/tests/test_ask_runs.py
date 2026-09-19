"""The registry behind a visitor's ask (demo-site.md §3.6), without HTTP."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from vidtheque_mcp.public.runs import Runs, run_key

ANSWER = {"event": "answer", "payload": {"answer": "yes", "citations": [], "model": None}}


def key(q: str) -> tuple[str, str, str]:
    return run_key("v-0123456789abcdef", q, None)


async def events(*items: dict[str, Any], gate: asyncio.Event | None = None) -> AsyncIterator[dict]:
    for item in items:
        if gate is not None:
            await gate.wait()
        yield item


async def test_a_kept_answer_expires_and_the_oldest_goes_first() -> None:
    runs = Runs(keep_s=0.05, max_kept=2)
    for q in ("q0", "q1", "q2"):
        run = runs.start(key(q), events(ANSWER), lambda: None)
        assert run.task is not None
        await run.task
    assert runs.get(key("q0")) is None, "three kept, room for two: the oldest goes"
    assert runs.get(key("q2")) is not None
    await asyncio.sleep(0.08)
    assert runs.get(key("q2")) is None, "and nothing outlives keep_s"


async def test_spacing_and_case_are_the_same_question() -> None:
    runs = Runs()
    run = runs.start(key("What  is it?"), events(ANSWER), lambda: None)
    assert runs.get(key("what is IT?")) is run


async def test_shutdown_cancels_a_running_run_settles_it_and_ends_its_readers() -> None:
    runs = Runs()
    settled: list[bool] = []
    gate = asyncio.Event()  # never set: the loop is mid-completion at shutdown
    run = runs.start(key("q"), events(ANSWER, gate=gate), lambda: settled.append(True))
    await asyncio.sleep(0)
    reader = asyncio.create_task(_drain(run))
    await runs.close()
    assert run.done and settled == [True]
    assert await asyncio.wait_for(reader, 1) == []
    assert runs.get(key("q")) is None, "a run that never answered is not kept"


async def _drain(run: Any) -> list[dict]:
    return [event async for event in run.follow()]
