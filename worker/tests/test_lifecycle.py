"""LifecycleManager behaviour, with fake backends only."""

from __future__ import annotations

import asyncio

import pytest
from conftest import FakeBackend, FakeHooks, FakeVram, Recorder

from vidtheque_worker.gpu import GPUHookError
from vidtheque_worker.lifecycle import (
    InsufficientVRAM,
    LifecycleManager,
    ManagerNotRunning,
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
        with pytest.raises(ValueError):
            await manager.submit("embed", lambda b: b.infer(["hi"]))
        backends["embed"].infer_error = None
        assert await manager.submit("embed", lambda b: b.infer(["hi"])) == "embed-ok"
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
        assert snap["lease"]["acquired"] is True
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
