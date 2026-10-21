"""A client that hangs up stops costing GPU time.

uvicorn does not cancel a handler when its client disconnects, so without
this the F-29 skip (`_execute` drops a job whose future is done) never fired
for a real HTTP caller: mcp's read timeout gave up, replayed, and every
abandoned request still ran its forward pass behind the live one.
Real uvicorn on an ephemeral port with a fake backend: CPU-only, about a second.
"""

from __future__ import annotations

import asyncio
import socket

import httpx2 as httpx
import pytest
import uvicorn
from conftest import FakeBackend, Recorder

from vidtheque_worker import api
from vidtheque_worker.app import create_app
from vidtheque_worker.backends.base import Embeddings
from vidtheque_worker.config import Settings
from vidtheque_worker.lifecycle import LifecycleManager


async def _wait_for(predicate, timeout: float = 5.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition never became true")
        await asyncio.sleep(0.01)


async def test_requests_whose_client_gave_up_never_reach_the_gpu(monkeypatch):
    monkeypatch.setattr(api, "DISCONNECT_POLL_SECONDS", 0.02)
    recorder = Recorder()
    embed = FakeBackend(
        "embed",
        vram_estimate_mb=0,
        recorder=recorder,
        result=Embeddings(vectors=[[0.1]], dims=1, model="m"),
        infer_seconds=0.4,
    )
    manager = LifecycleManager({"embed": embed}, vram_probe=lambda: None, idle_poll_interval=1)
    app = create_app(settings=Settings(_env_file=None), manager=manager)

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="warning", lifespan="on"))
    serving = asyncio.create_task(server.serve(sockets=[sock]))
    try:
        await _wait_for(lambda: server.started)
        async with httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{port}", timeout=0.15, trust_env=False
        ) as client:

            async def one() -> str:
                try:
                    await client.post("/v1/embeddings", json={"input": ["x"]})
                except httpx.TimeoutException as exc:
                    return type(exc).__name__
                return "answered"

            outcomes = await asyncio.gather(*[one() for _ in range(5)])
        assert outcomes == ["ReadTimeout"] * 5

        # Every handler has returned and the GPU is idle.
        await _wait_for(
            lambda: manager.snapshot()["queue"]["in_flight"] == 0
            and manager.snapshot()["queue"]["running"] is None
            and manager.snapshot()["queue"]["depth"] == 0
        )
        # The first job was already in its thread and cannot be stopped; the
        # other four were dropped before they started.
        assert len(embed.infer_calls) == 1
    finally:
        server.should_exit = True
        await asyncio.wait_for(serving, timeout=5.0)


async def test_a_job_abandoned_during_its_cold_load_skips_the_forward_pass():
    recorder = Recorder()
    stt = FakeBackend("stt", vram_estimate_mb=0, recorder=recorder, result="t", load_seconds=0.2)
    manager = LifecycleManager({"stt": stt}, vram_probe=lambda: None, idle_poll_interval=1)
    await manager.start()
    try:
        abandoned = asyncio.create_task(manager.submit("stt", lambda b: b.infer("a.wav")))
        await _wait_for(lambda: manager.snapshot()["queue"]["running"] == "stt")
        abandoned.cancel()
        with pytest.raises(asyncio.CancelledError):
            await abandoned
        assert await manager.submit("stt", lambda b: b.infer("b.wav")) == "t"
        assert [call[0] for call in stt.infer_calls] == [("b.wav",)]
    finally:
        await manager.stop()
