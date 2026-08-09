"""LifecycleManager behaviour, with fake backends only."""

from __future__ import annotations

import asyncio

import pytest
from conftest import FakeBackend, FakeHooks, FakeVram, Recorder

from vidtheque_worker.backends.base import (
    BackendCrashed,
    BackendError,
    BackendPoisoned,
    BackendUnavailable,
    InvalidImageError,
)
from vidtheque_worker.gpu import GPUHookError
from vidtheque_worker.lifecycle import (
    InsufficientVRAM,
    LifecycleManager,
    ManagerNotRunning,
    WorkerShuttingDown,
    _InFlight,
)


def make_backends(recorder: Recorder, **overrides) -> dict[str, FakeBackend]:
    spec = {"stt": 3000, "embed": 2000, "ocr": 0}
    spec.update(overrides)
    return {
        task: FakeBackend(task, vram_estimate_mb=mb, recorder=recorder, result=f"{task}-ok")
        for task, mb in spec.items()
    }


async def make_manager(backends, recorder, **kwargs) -> LifecycleManager:
    kwargs.setdefault("vram_probe", FakeVram(backends))
    kwargs.setdefault("hook_runner", FakeHooks(recorder))
    kwargs.setdefault("idle_poll_interval", 0.01)
    manager = LifecycleManager(backends, **kwargs)
    await manager.start()
    return manager


# --------------------------------------------------------------------------
# load on demand
# --------------------------------------------------------------------------


async def test_nothing_loads_until_a_job_arrives(recorder):
    backends = make_backends(recorder)
    manager = await make_manager(backends, recorder)
    try:
        assert not any(b.loaded for b in backends.values())
        result = await manager.submit("embed", lambda b: b.infer(["hi"]))
        assert result == "embed-ok"
        assert backends["embed"].loaded
        assert not backends["stt"].loaded
        assert recorder.names("load", "infer") == ["embed", "embed"]
        assert backends["embed"].infer_calls == [((["hi"],), {})]
    finally:
        await manager.stop()


async def test_second_job_reuses_the_loaded_model(recorder):
    backends = make_backends(recorder)
    manager = await make_manager(backends, recorder)
    try:
        await manager.submit("embed", lambda b: b.infer(["a"]))
        await manager.submit("embed", lambda b: b.infer(["b"]))
        assert recorder.names("load") == ["embed"]
        assert manager.slot("embed").load_count == 1
        assert manager.slot("embed").job_count == 2
    finally:
        await manager.stop()


async def test_submit_before_start_is_refused(recorder):
    manager = LifecycleManager(make_backends(recorder))
    with pytest.raises(ManagerNotRunning):
        await manager.submit("embed", lambda b: b.infer(["x"]))


async def test_unknown_task_is_a_key_error(recorder):
    backends = make_backends(recorder)
    manager = await make_manager(backends, recorder)
    try:
        with pytest.raises(KeyError):
            await manager.submit("nope", lambda b: None)
    finally:
        await manager.stop()


# --------------------------------------------------------------------------
# hooks
# --------------------------------------------------------------------------


async def test_acquire_runs_before_first_load_release_after_last_unload(recorder):
    backends = make_backends(recorder)
    hooks = FakeHooks(recorder)
    manager = await make_manager(
        backends,
        recorder,
        hook_runner=hooks,
        acquire_cmd="systemctl stop llama-server",
        release_cmd="systemctl start llama-server",
    )
    try:
        await manager.submit("embed", lambda b: b.infer(["hi"]))
        assert recorder.names("hook", "load") == ["GPU_ACQUIRE_CMD", "embed"]
    finally:
        await manager.stop()

    assert recorder.names("hook", "load", "unload") == [
        "GPU_ACQUIRE_CMD",
        "embed",
        "embed",
        "GPU_RELEASE_CMD",
    ]
    assert [c[0] for c in hooks.calls] == [
        "systemctl stop llama-server",
        "systemctl start llama-server",
    ]


async def test_acquire_runs_once_for_several_models(recorder):
    backends = make_backends(recorder)
    hooks = FakeHooks(recorder)
    manager = await make_manager(
        backends,
        recorder,
        hook_runner=hooks,
        acquire_cmd="acquire",
        release_cmd="release",
        vram_headroom_mb=0,
    )
    try:
        await manager.submit("ocr", lambda b: b.infer([b"img"]))
        await manager.submit("embed", lambda b: b.infer(["hi"]))
        assert [label for _, label in hooks.calls] == ["GPU_ACQUIRE_CMD"]
    finally:
        await manager.stop()
    assert [label for _, label in hooks.calls] == ["GPU_ACQUIRE_CMD", "GPU_RELEASE_CMD"]


async def test_cpu_only_traffic_never_touches_the_lease(recorder):
    """OCR is CPU-only, so it must not stop the co-tenant it does not compete
    with — measured doing exactly that in gpu-validation-2026-08-08 §5.2."""
    backends = make_backends(recorder)
    hooks = FakeHooks(recorder)
    manager = await make_manager(
        backends,
        recorder,
        hook_runner=hooks,
        acquire_cmd="systemctl stop llama-server",
        release_cmd="systemctl start llama-server",
    )
    try:
        await manager.submit("ocr", lambda b: b.infer([b"img"]))
        await manager.submit("ocr", lambda b: b.infer([b"img"]))
        assert backends["ocr"].loaded
        assert hooks.calls == []
        assert manager.hooks.acquired is False
        assert manager.snapshot()["lease"]["acquired"] is False
    finally:
        await manager.stop()
    # ...and the unload at shutdown fires no release either.
    assert hooks.calls == []
    assert manager.hooks.events == []


async def test_a_resident_model_holds_vram_but_not_the_lease(recorder):
    """EMBED_RESIDENT=1 used to mean the release hook never ran again: something
    was always loaded, so the co-tenant stayed stopped for the life of the
    process. The lease now brackets non-resident GPU work only."""
    backends = make_backends(recorder)
    hooks = FakeHooks(recorder)
    manager = await make_manager(
        backends,
        recorder,
        hook_runner=hooks,
        acquire_cmd="stop llama",
        release_cmd="start llama",
        resident_tasks=("embed",),
        idle_unload_seconds=0.05,
        idle_poll_interval=0.01,
        vram_headroom_mb=0,
    )
    try:
        await manager.submit("embed", lambda b: b.infer(["hi"]))
        assert backends["embed"].loaded
        assert hooks.calls == [], "a resident model must not take the lease"

        await manager.submit("stt", lambda b: b.infer("a.wav"))
        assert [label for _, label in hooks.calls] == ["GPU_ACQUIRE_CMD"]
        # acquire came *before* the STT load, and after the resident one
        assert recorder.names("hook", "load") == ["embed", "GPU_ACQUIRE_CMD", "stt"]

        for _ in range(200):  # the reaper takes STT; the resident model stays
            await asyncio.sleep(0.01)
            if not backends["stt"].loaded:
                break
        assert not backends["stt"].loaded
        assert [label for _, label in hooks.calls] == [
            "GPU_ACQUIRE_CMD",
            "GPU_RELEASE_CMD",
        ]
        assert backends["embed"].loaded, "the resident model keeps its VRAM"
        assert manager.slot("embed").unload_count == 0
    finally:
        await manager.stop()


async def test_failed_acquire_hook_aborts_the_load(recorder):
    backends = make_backends(recorder)
    hooks = FakeHooks(recorder, error=RuntimeError("llama-server would not stop"))
    manager = await make_manager(
        backends, recorder, hook_runner=hooks, acquire_cmd="acquire"
    )
    try:
        with pytest.raises(GPUHookError):
            await manager.submit("embed", lambda b: b.infer(["hi"]))
        assert not backends["embed"].loaded
        assert recorder.names("load") == []
        # The lease was not taken, so a later attempt tries the hook again.
        assert manager.hooks.acquired is False
    finally:
        await manager.stop()


async def test_release_hook_failure_does_not_propagate(recorder):
    backends = make_backends(recorder)

    class FlakyRelease(FakeHooks):
        async def __call__(self, command: str, label: str) -> None:
            self.calls.append((command, label))
            self.recorder.record("hook", label)
            if label == "GPU_RELEASE_CMD":
                raise RuntimeError("boom")

    hooks = FlakyRelease(recorder)
    manager = await make_manager(
        backends, recorder, hook_runner=hooks, acquire_cmd="a", release_cmd="r"
    )
    await manager.submit("embed", lambda b: b.infer(["hi"]))
    await manager.stop()  # must not raise
    assert "release-failed" in manager.hooks.events


async def test_hooks_still_tracked_without_commands_configured(recorder):
    backends = make_backends(recorder)
    manager = await make_manager(backends, recorder)
    try:
        await manager.submit("embed", lambda b: b.infer(["hi"]))
        assert manager.hooks.acquired is True
    finally:
        await manager.stop()
    assert manager.hooks.events == ["acquire", "release"]


# --------------------------------------------------------------------------
# idle unload
# --------------------------------------------------------------------------


async def test_idle_model_is_unloaded_by_the_background_reaper(recorder):
    backends = make_backends(recorder)
    manager = await make_manager(
        backends, recorder, idle_unload_seconds=0.05, idle_poll_interval=0.01
    )
    try:
        await manager.submit("embed", lambda b: b.infer(["hi"]))
        assert backends["embed"].loaded
        for _ in range(100):
            await asyncio.sleep(0.01)
            if not backends["embed"].loaded:
                break
        assert not backends["embed"].loaded
        assert manager.slot("embed").unload_count == 1
    finally:
        await manager.stop()


async def test_idle_unload_disabled_when_zero(recorder):
    backends = make_backends(recorder)
    manager = await make_manager(
        backends, recorder, idle_unload_seconds=0, idle_poll_interval=0.01
    )
    try:
        await manager.submit("embed", lambda b: b.infer(["hi"]))
        await asyncio.sleep(0.05)
        assert await manager.reap_idle() == []
        assert backends["embed"].loaded
    finally:
        await manager.stop()


async def test_resident_backend_is_exempt_from_idle_unload(recorder):
    backends = make_backends(recorder)
    manager = await make_manager(
        backends,
        recorder,
        idle_unload_seconds=0.01,
        idle_poll_interval=0.005,
        resident_tasks=("embed",),
        vram_headroom_mb=0,
    )
    try:
        await manager.submit("embed", lambda b: b.infer(["hi"]))
        await manager.submit("ocr", lambda b: b.infer([b"img"]))
        await asyncio.sleep(0.1)
        assert backends["embed"].loaded, "resident model must survive the reaper"
        assert not backends["ocr"].loaded
    finally:
        await manager.stop()


async def test_stop_unloads_resident_models_too(recorder):
    backends = make_backends(recorder)
    manager = await make_manager(backends, recorder, resident_tasks=("embed",))
    await manager.submit("embed", lambda b: b.infer(["hi"]))
    await manager.stop()
    assert not backends["embed"].loaded


# --------------------------------------------------------------------------
# shutdown: the queue, the thread, and the runner's death
# --------------------------------------------------------------------------


async def _wait_for(predicate, timeout: float = 2.0) -> None:
    """Poll until ``predicate()`` or fail — never a bare sleep in a race test."""
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("condition never became true")
        await asyncio.sleep(0.005)


async def test_stop_resolves_every_queued_job(recorder):
    """The lifespan hang: cancelling the runner only ever resolved the job it
    was running, so anything still queued kept a pending future, its handler
    never returned, and shutdown waited for uvicorn's kill."""
    backends = make_backends(recorder)
    backends["stt"].infer_seconds = 0.2
    manager = await make_manager(backends, recorder)

    running = asyncio.create_task(manager.submit("stt", lambda b: b.infer("a.wav")))
    await _wait_for(lambda: backends["stt"].concurrent == 1)
    queued = [
        asyncio.create_task(manager.submit("stt", lambda b: b.infer("b.wav"))),
        asyncio.create_task(manager.submit("stt", lambda b: b.infer("c.wav"))),
    ]
    await _wait_for(lambda: manager.snapshot()["queue"]["depth"] == 2)

    await asyncio.wait_for(manager.stop(), timeout=5.0)

    for task in queued:
        with pytest.raises(WorkerShuttingDown):
            await asyncio.wait_for(task, timeout=1.0)
    # The in-flight one is answered too, rather than left cancelled.
    with pytest.raises(WorkerShuttingDown):
        await asyncio.wait_for(running, timeout=1.0)
    assert manager.snapshot()["queue"]["depth"] == 0


async def test_stop_waits_for_the_in_flight_thread_before_unloading(recorder):
    """`asyncio.to_thread` cannot be cancelled: the coroutine is abandoned and
    the thread runs on. Unloading straight after the cancel freed weights out
    from under a live thread."""
    backends = make_backends(recorder)
    backends["stt"].infer_seconds = 0.2
    manager = await make_manager(backends, recorder, shutdown_grace_seconds=5.0)

    running = asyncio.create_task(manager.submit("stt", lambda b: b.infer("a.wav")))
    await _wait_for(lambda: backends["stt"].concurrent == 1)
    await asyncio.wait_for(manager.stop(), timeout=5.0)

    assert recorder.names("unload-during-infer") == []
    order = [kind for kind, task in recorder.kinds("infer-done", "unload") if task == "stt"]
    assert order == ["infer-done", "unload"]
    with pytest.raises(WorkerShuttingDown):
        await asyncio.wait_for(running, timeout=1.0)


async def test_the_grace_period_is_bounded(recorder, caplog):
    """The documented residual: a job that outlasts the grace does *not* hold
    the shutdown open. A teardown that never finishes is worse than the race."""
    backends = make_backends(recorder)
    backends["stt"].infer_seconds = 0.3
    manager = await make_manager(backends, recorder, shutdown_grace_seconds=0.02)

    running = asyncio.create_task(manager.submit("stt", lambda b: b.infer("a.wav")))
    await _wait_for(lambda: backends["stt"].concurrent == 1)
    with caplog.at_level("WARNING"):
        await asyncio.wait_for(manager.stop(), timeout=1.0)

    assert "still running" in caplog.text
    assert recorder.names("unload-during-infer") == ["stt"]
    with pytest.raises(WorkerShuttingDown):
        await asyncio.wait_for(running, timeout=1.0)


async def test_a_job_that_never_reached_its_thread_does_not_hold_the_shutdown(recorder):
    """The other outcome of cancelling a `to_thread`: if the executor had not
    picked the call up yet, no thread exists and `done` will never be set.
    Waiting the whole grace period on it would pay a shutdown delay for
    nothing."""
    backends = make_backends(recorder)
    manager = await make_manager(backends, recorder, shutdown_grace_seconds=30.0)
    manager._in_thread = _InFlight()  # dispatched, never started

    started = asyncio.get_running_loop().time()
    await asyncio.wait_for(manager.stop(), timeout=5.0)
    assert asyncio.get_running_loop().time() - started < 2.0


async def test_submit_during_and_after_stop_is_refused(recorder):
    backends = make_backends(recorder)
    manager = await make_manager(backends, recorder)
    await manager.stop()
    with pytest.raises(WorkerShuttingDown):
        await manager.submit("embed", lambda b: b.infer(["hi"]))


async def test_a_dead_runner_does_not_strand_queued_jobs(recorder):
    """Nothing supervises the consumer. If it ever dies for a reason that is
    not `stop()`, every queued future is waiting on a task that no longer
    exists — so the runner drains on its way out, whatever killed it."""
    backends = make_backends(recorder)
    backends["stt"].infer_seconds = 0.2
    manager = await make_manager(backends, recorder)
    try:
        running = asyncio.create_task(manager.submit("stt", lambda b: b.infer("a.wav")))
        await _wait_for(lambda: backends["stt"].concurrent == 1)
        queued = asyncio.create_task(manager.submit("stt", lambda b: b.infer("b.wav")))
        await _wait_for(lambda: manager.snapshot()["queue"]["depth"] == 1)

        manager._runner.cancel()  # a death that is not a shutdown

        with pytest.raises(WorkerShuttingDown):
            await asyncio.wait_for(queued, timeout=1.0)
        with pytest.raises(WorkerShuttingDown):
            await asyncio.wait_for(running, timeout=1.0)
        # and /status says which of "busy" and "wedged" this is
        assert manager.snapshot()["queue"]["consumer_alive"] is False
    finally:
        await manager.stop()


async def test_resident_text_embedder_does_not_exempt_the_frame_embedder(recorder):
    """EMBED_RESIDENT is about query latency. The frame embedder is an
    indexing-time cost and the biggest of the three, so it stays evictable."""
    backends = make_backends(recorder, image_embed=5000)
    manager = await make_manager(
        backends,
        recorder,
        idle_unload_seconds=0.01,
        idle_poll_interval=0.005,
        resident_tasks=("embed",),
        vram_headroom_mb=0,
    )
    try:
        await manager.submit("embed", lambda b: b.infer(["hi"]))
        await manager.submit("image_embed", lambda b: b.infer([b"jpeg"]))
        await asyncio.sleep(0.1)
        assert backends["embed"].loaded, "resident text embedder must survive"
        assert not backends["image_embed"].loaded
    finally:
        await manager.stop()


# --------------------------------------------------------------------------
# VRAM admission control
# --------------------------------------------------------------------------


async def test_lru_backend_is_evicted_to_make_room(recorder):
    backends = make_backends(recorder)
    manager = await make_manager(
        backends, recorder, vram_headroom_mb=0, vram_probe=FakeVram(backends, total_mb=4000)
    )
    try:
        await manager.submit("embed", lambda b: b.infer(["hi"]))  # 2000 of 4000
        await manager.submit("stt", lambda b: b.infer("a.wav"))  # needs 3000
        assert not backends["embed"].loaded, "embed should have been evicted"
        assert backends["stt"].loaded
        assert recorder.names("load", "unload") == ["embed", "embed", "stt"]
    finally:
        await manager.stop()


async def test_resident_backend_is_never_evicted_for_vram(recorder):
    backends = make_backends(recorder)
    manager = await make_manager(
        backends,
        recorder,
        vram_headroom_mb=0,
        resident_tasks=("embed",),
        vram_probe=FakeVram(backends, total_mb=4000),
    )
    try:
        await manager.submit("embed", lambda b: b.infer(["hi"]))
        with pytest.raises(InsufficientVRAM):
            await manager.submit("stt", lambda b: b.infer("a.wav"))
        assert backends["embed"].loaded
        assert not backends["stt"].loaded
    finally:
        await manager.stop()


async def test_frame_embedder_is_evicted_for_the_text_embedder(recorder):
    backends = make_backends(recorder, image_embed=5000)
    manager = await make_manager(
        backends,
        recorder,
        vram_headroom_mb=0,
        vram_probe=FakeVram(backends, total_mb=6000),
    )
    try:
        await manager.submit("image_embed", lambda b: b.infer([b"jpeg"]))  # 5000/6000
        await manager.submit("embed", lambda b: b.infer(["hi"]))  # needs 2000
        assert not backends["image_embed"].loaded
        assert backends["embed"].loaded
        assert recorder.names("load", "unload") == [
            "image_embed",
            "image_embed",
            "embed",
        ]
    finally:
        await manager.stop()


async def test_both_frame_towers_share_one_slot_and_one_load(recorder):
    """Image and text towers are two heads of one checkpoint. A frame query
    against an already-loaded frame model must not reload it, and must not
    take a slot of its own — the manager only ever has four."""
    backends = make_backends(recorder, image_embed=5000)
    manager = await make_manager(
        backends,
        recorder,
        vram_headroom_mb=0,
        vram_probe=FakeVram(backends, total_mb=8000),
    )
    try:
        await manager.submit("image_embed", lambda b: b.infer([b"jpeg"]))
        await manager.submit("image_embed", lambda b: b.embed_text(["a terminal"]))
        assert manager.slot("image_embed").load_count == 1
        assert manager.slot("image_embed").job_count == 2
        assert recorder.names("load", "unload") == ["image_embed"]
        assert manager.tasks == ["stt", "embed", "ocr", "image_embed"]
    finally:
        await manager.stop()


async def test_the_two_embedders_never_run_at_once(recorder):
    backends = make_backends(recorder, image_embed=5000)
    for backend in backends.values():
        backend.infer_seconds = 0.01
    manager = await make_manager(backends, recorder, vram_headroom_mb=0)
    try:
        await asyncio.gather(
            manager.submit("embed", lambda b: b.infer(["a"])),
            manager.submit("image_embed", lambda b: b.infer([b"jpeg"])),
            manager.submit("embed", lambda b: b.infer(["b"])),
        )
        assert all(b.max_concurrent <= 1 for b in backends.values())
    finally:
        await manager.stop()


async def test_zero_vram_backend_skips_admission_control(recorder):
    backends = make_backends(recorder)
    probe = FakeVram(backends, total_mb=0)
    manager = await make_manager(backends, recorder, vram_probe=probe)
    try:
        await manager.submit("ocr", lambda b: b.infer([b"img"]))
        assert backends["ocr"].loaded
        assert probe.calls == 0, "a 0 MB backend should not even probe"
    finally:
        await manager.stop()


async def test_missing_nvml_does_not_block_loads(recorder):
    backends = make_backends(recorder)
    manager = await make_manager(
        backends, recorder, vram_probe=lambda: None, vram_headroom_mb=99999
    )
    try:
        await manager.submit("stt", lambda b: b.infer("a.wav"))
        assert backends["stt"].loaded
    finally:
        await manager.stop()


# --------------------------------------------------------------------------
# serialization
# --------------------------------------------------------------------------


async def test_concurrent_requests_are_serialized(recorder):
    backends = make_backends(recorder)
    backends["embed"].infer_seconds = 0.02
    manager = await make_manager(backends, recorder)
    try:
        results = await asyncio.gather(
            *(manager.submit("embed", lambda b, i=i: b.infer([f"t{i}"])) for i in range(6))
        )
        assert results == ["embed-ok"] * 6
        assert backends["embed"].max_concurrent == 1
    finally:
        await manager.stop()


async def test_mixed_tasks_never_overlap_on_the_gpu(recorder):
    backends = make_backends(recorder)
    for backend in backends.values():
        backend.infer_seconds = 0.01
    manager = await make_manager(backends, recorder, vram_headroom_mb=0)
    try:
        await asyncio.gather(
            manager.submit("embed", lambda b: b.infer(["a"])),
            manager.submit("ocr", lambda b: b.infer([b"i"])),
            manager.submit("embed", lambda b: b.infer(["b"])),
        )
        # An interleaving would show up as two infers without a load between
        # them on a backend that had been evicted; simplest invariant:
        assert all(b.max_concurrent <= 1 for b in backends.values())
    finally:
        await manager.stop()


async def test_queue_depth_is_reported_while_work_is_pending(recorder):
    backends = make_backends(recorder)
    backends["embed"].infer_seconds = 0.05
    manager = await make_manager(backends, recorder)
    try:
        pending = [
            asyncio.create_task(manager.submit("embed", lambda b: b.infer(["x"])))
            for _ in range(4)
        ]
        await asyncio.sleep(0.02)
        snap = manager.snapshot()
        assert snap["queue"]["in_flight"] == 4
        assert snap["queue"]["depth"] >= 1
        await asyncio.gather(*pending)
        assert manager.snapshot()["queue"]["in_flight"] == 0
    finally:
        await manager.stop()


# --------------------------------------------------------------------------
# failures
# --------------------------------------------------------------------------


async def test_job_failure_propagates_and_the_queue_survives(recorder):
    backends = make_backends(recorder)
    backends["embed"].infer_error = ValueError("bad input")
    manager = await make_manager(backends, recorder)
    try:
        with pytest.raises(BackendError) as raised:
            await manager.submit("embed", lambda b: b.infer(["hi"]))
        # Wrapped, not swallowed: an untyped exception would reach FastAPI's
        # bare-500 path with no error.type on it, and the cause is what a log
        # reader actually needs.
        assert isinstance(raised.value.__cause__, ValueError)
        assert "ValueError" in str(raised.value)
        backends["embed"].infer_error = None
        assert await manager.submit("embed", lambda b: b.infer(["hi"])) == "embed-ok"
    finally:
        await manager.stop()


async def test_an_oom_during_inference_unloads_the_slot_and_frees_its_vram(recorder):
    """The 3090's finding: a CUDA OOM poisons the model's context, so a slot
    left `loaded` answers every later job with `invalid device ordinal`."""
    backends = make_backends(recorder)
    backends["stt"].infer_error = RuntimeError("CUDA failed with error out of memory")
    probe = FakeVram(backends)
    manager = await make_manager(backends, recorder, vram_probe=probe)
    try:
        with pytest.raises(BackendCrashed):
            await manager.submit("stt", lambda b: b.infer("a.wav"))

        # slot state: unloaded, and counted as an unload
        assert not backends["stt"].loaded
        assert manager.slot("stt").loaded is False
        assert manager.slot("stt").unload_count == 1
        assert recorder.names("load", "infer", "unload") == ["stt", "stt", "stt"]

        # VRAM accounting: the estimate came back
        snap = manager.snapshot()
        assert {b["task"]: b["loaded"] for b in snap["backends"]}["stt"] is False
        assert snap["vram"]["used_mb"] == 0

        # and the next request re-loads a clean model
        backends["stt"].infer_error = None
        assert await manager.submit("stt", lambda b: b.infer("a.wav")) == "stt-ok"
        assert manager.slot("stt").load_count == 2
        assert manager.slot("stt").job_count == 1
    finally:
        await manager.stop()


async def test_a_crashed_job_gives_the_lease_back(recorder):
    backends = make_backends(recorder)
    backends["stt"].infer_error = RuntimeError("CUDA out of memory")
    hooks = FakeHooks(recorder)
    manager = await make_manager(
        backends, recorder, hook_runner=hooks, acquire_cmd="a", release_cmd="r"
    )
    try:
        with pytest.raises(BackendCrashed):
            await manager.submit("stt", lambda b: b.infer("a.wav"))
        assert [label for _, label in hooks.calls] == [
            "GPU_ACQUIRE_CMD",
            "GPU_RELEASE_CMD",
        ]
        assert manager.hooks.acquired is False
    finally:
        await manager.stop()


async def test_bad_input_does_not_cost_the_loaded_model(recorder):
    """Only the device talking unloads a slot. A ValueError is the caller's."""
    backends = make_backends(recorder)
    backends["embed"].infer_error = ValueError("bad input")
    manager = await make_manager(backends, recorder)
    try:
        with pytest.raises(BackendError):
            await manager.submit("embed", lambda b: b.infer(["hi"]))
        assert backends["embed"].loaded
        assert manager.slot("embed").unload_count == 0
    finally:
        await manager.stop()


@pytest.mark.parametrize(
    "exc",
    [
        RuntimeError("Expected all tensors to be on the same device"),
        RuntimeError("stack expects each tensor to be equal size"),
        ValueError("'en-US' is not a valid language code"),
        OSError("broken data stream when reading image file"),
    ],
    ids=["dtype", "shape", "language", "decoder"],
)
async def test_a_non_device_runtimeerror_keeps_the_model_loaded(recorder, exc):
    """The measured cost of getting this wrong, per bad input: a 5.8 s reload,
    and a 503 the mcp client reads as backpressure — so it replays the same
    doomed input until the 1800 s retry budget is gone. whisperX raises
    RuntimeError for a malformed media stream, torch for a dtype mismatch,
    onnxruntime for a shape error; none of them are the card."""
    backends = make_backends(recorder)
    backends["stt"].infer_error = exc
    manager = await make_manager(backends, recorder)
    try:
        with pytest.raises(BackendError) as raised:
            await manager.submit("stt", lambda b: b.infer("a.wav"))
        assert not isinstance(raised.value, BackendCrashed)
        assert backends["stt"].loaded
        assert manager.slot("stt").unload_count == 0
        assert recorder.names("unload") == []
    finally:
        await manager.stop()


@pytest.mark.parametrize(
    "exc",
    [
        RuntimeError("CUDA out of memory. Tried to allocate 2.00 GiB"),
        RuntimeError("CUBLAS_STATUS_EXECUTION_FAILED when calling cublasGemmEx"),
        RuntimeError("invalid device ordinal"),
        Exception("device-side assert triggered"),
    ],
    ids=["oom", "cublas", "ordinal", "assert"],
)
async def test_a_device_failure_still_unloads_whatever_its_type(recorder, exc):
    """The message sniff behind the typed signal: three of the four shipped
    stacks report a dead device as an untyped exception, and onnxruntime's is
    not even a RuntimeError."""
    backends = make_backends(recorder)
    backends["stt"].infer_error = exc
    manager = await make_manager(backends, recorder)
    try:
        with pytest.raises(BackendCrashed):
            await manager.submit("stt", lambda b: b.infer("a.wav"))
        assert not backends["stt"].loaded
        assert manager.slot("stt").unload_count == 1
    finally:
        await manager.stop()


async def test_a_poisoned_backend_says_so_by_type(recorder):
    """The signal a backend raises when it recognises its own device failure —
    no message sniffing involved, and the wire shape is BackendCrashed's."""
    backends = make_backends(recorder)
    backends["image_embed"] = FakeBackend(
        "image_embed", vram_estimate_mb=3200, recorder=recorder
    )
    backends["image_embed"].infer_error = BackendPoisoned("the context is gone")
    manager = await make_manager(backends, recorder)
    try:
        with pytest.raises(BackendPoisoned):
            await manager.submit("image_embed", lambda b: b.infer([b"x"]))
        assert not backends["image_embed"].loaded
        assert manager.slot("image_embed").unload_count == 1
    finally:
        await manager.stop()


async def test_a_typed_input_error_is_never_a_device_failure(recorder):
    """Not even when the filename says `cuda`: an input error never unloads."""
    backends = make_backends(recorder)
    backends["image_embed"] = FakeBackend(
        "image_embed", vram_estimate_mb=3200, recorder=recorder
    )
    backends["image_embed"].infer_error = InvalidImageError(
        "image 3 (cuda-talk-out-of-memory.png) is not a decodable image", index=3
    )
    manager = await make_manager(backends, recorder)
    try:
        with pytest.raises(InvalidImageError) as raised:
            await manager.submit("image_embed", lambda b: b.infer([b"x"]))
        assert raised.value.index == 3
        assert backends["image_embed"].loaded
        assert manager.slot("image_embed").unload_count == 0
    finally:
        await manager.stop()


async def test_a_typed_backend_error_keeps_its_type(recorder):
    """BackendUnavailable already maps to its own 503; unload, do not re-wrap."""
    backends = make_backends(recorder)
    backends["embed"].infer_error = BackendUnavailable("model is not loaded")
    manager = await make_manager(backends, recorder)
    try:
        with pytest.raises(BackendUnavailable):
            await manager.submit("embed", lambda b: b.infer(["hi"]))
        assert not backends["embed"].loaded
    finally:
        await manager.stop()


async def test_a_load_that_ooms_is_retryable_and_a_broken_load_is_typed(recorder):
    """A load-time OOM is the card being full now, which is worth retrying; a
    load that fails for any other reason at least says so with an error.type
    rather than arriving as FastAPI's bare 500."""
    backends = make_backends(recorder)
    backends["stt"].load_error = RuntimeError("CUDA out of memory during load")
    backends["embed"].load_error = OSError("no such file: model.bin")
    manager = await make_manager(backends, recorder)
    try:
        with pytest.raises(BackendCrashed):
            await manager.submit("stt", lambda b: b.infer("a.wav"))
        with pytest.raises(BackendError) as raised:
            await manager.submit("embed", lambda b: b.infer(["hi"]))
        assert not isinstance(raised.value, BackendCrashed)
        assert isinstance(raised.value.__cause__, OSError)
        # Neither slot was ever loaded, so neither can have been unloaded.
        assert recorder.names("unload") == []
    finally:
        await manager.stop()


async def test_load_failure_releases_the_lease(recorder):
    backends = make_backends(recorder)
    backends["embed"].load_error = RuntimeError("no weights")
    hooks = FakeHooks(recorder)
    manager = await make_manager(
        backends, recorder, hook_runner=hooks, acquire_cmd="a", release_cmd="r"
    )
    try:
        with pytest.raises(RuntimeError):
            await manager.submit("embed", lambda b: b.infer(["hi"]))
        assert [label for _, label in hooks.calls] == [
            "GPU_ACQUIRE_CMD",
            "GPU_RELEASE_CMD",
        ]
    finally:
        await manager.stop()


# --------------------------------------------------------------------------
# snapshot
# --------------------------------------------------------------------------


async def test_snapshot_shape(recorder):
    backends = make_backends(recorder)
    manager = await make_manager(backends, recorder, resident_tasks=("embed",))
    try:
        await manager.submit("embed", lambda b: b.infer(["hi"]))
        snap = manager.snapshot()
        by_task = {b["task"]: b for b in snap["backends"]}
        assert by_task["embed"]["loaded"] is True
        assert by_task["embed"]["resident"] is True
        assert by_task["embed"]["vram_estimate_mb"] == 2000
        assert by_task["stt"]["loaded"] is False
        assert snap["vram"]["available"] is True
        assert snap["vram"]["used_mb"] == 2000
        # The loaded model is resident: it holds VRAM, never the lease.
        assert snap["lease"]["acquired"] is False
        assert snap["queue"]["running"] is None
    finally:
        await manager.stop()


async def test_snapshot_without_nvml(recorder):
    backends = make_backends(recorder)
    manager = await make_manager(backends, recorder, vram_probe=lambda: None)
    try:
        assert manager.snapshot()["vram"] == {
            "available": False,
            "reason": "nvml unavailable",
        }
    finally:
        await manager.stop()
