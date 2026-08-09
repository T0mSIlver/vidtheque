"""One checkpoint, two tasks, one Slot.

The unified embedder (`research/multimodal-embedding-2026-08-09.md` §5.5 and
Tom's addendum) answers `embed` and `image_embed` from one set of weights.
`build_backends` returns the same instance under both names and
`LifecycleManager` has to notice: one slot per task would load ~4.4 GB twice,
charge admission control ~8.8 GB for a model already on the card, and evict
whisperX to make room for it.

Everything here runs against fakes — no torch, no download, no GPU.
"""

from __future__ import annotations

import pytest
from conftest import FakeBackend, FakeHooks, FakeVram, Recorder

from vidtheque_worker.lifecycle import LifecycleManager


def shared_backends(recorder: Recorder, *, vram: int = 7000) -> dict[str, FakeBackend]:
    """`stt`, `ocr`, and ONE embedder registered under both embedding tasks."""
    unified = FakeBackend(
        "embed",
        name="qwen3-vl-embedding",
        model_id="Qwen/Qwen3-VL-Embedding-2B",
        vram_estimate_mb=vram,
        recorder=recorder,
        result="embed-ok",
        text_result="frame-query-ok",
    )
    return {
        "stt": FakeBackend("stt", vram_estimate_mb=8000, recorder=recorder, result="stt-ok"),
        "embed": unified,
        "image_embed": unified,
        "ocr": FakeBackend("ocr", vram_estimate_mb=0, recorder=recorder, result="ocr-ok"),
    }


def distinct(backends: dict[str, FakeBackend]) -> dict[str, FakeBackend]:
    """De-duplicated for `FakeVram`, which sums what is loaded — the fake would
    otherwise reproduce the very double-count this file is about."""
    seen: dict[int, FakeBackend] = {}
    for name, backend in backends.items():
        seen.setdefault(id(backend), backend)
    return {f"b{i}": b for i, b in enumerate(seen.values())}


async def make_manager(backends, recorder, **kwargs) -> LifecycleManager:
    kwargs.setdefault("vram_probe", FakeVram(distinct(backends)))
    kwargs.setdefault("hook_runner", FakeHooks(recorder))
    kwargs.setdefault("idle_poll_interval", 0.01)
    manager = LifecycleManager(backends, **kwargs)
    await manager.start()
    return manager


# --------------------------------------------------------------------------
# one load
# --------------------------------------------------------------------------


async def test_both_tasks_resolve_to_one_slot(recorder):
    backends = shared_backends(recorder)
    manager = await make_manager(backends, recorder)
    try:
        assert manager.slot("embed") is manager.slot("image_embed")
        assert manager.slot("embed").tasks == ("embed", "image_embed")
        assert manager.slot("embed").label == "embed+image_embed"
        assert manager.slot("embed").shared is True
        # Four tasks, three models.
        assert len(manager.slots()) == 3
    finally:
        await manager.stop()


async def test_the_shared_model_loads_once_for_both_tasks(recorder):
    """A cold `content_type=all` search embeds the query twice — once per leg,
    because the two legs want different instructions. It must pay one load."""
    backends = shared_backends(recorder)
    manager = await make_manager(backends, recorder)
    try:
        await manager.submit("embed", lambda b: b.infer(["a query"], input_type="query"))
        await manager.submit("image_embed", lambda b: b.embed_text(["a query"]))
        await manager.submit("image_embed", lambda b: b.infer([b"\xff\xd8jpeg"]))

        assert recorder.names("load") == ["embed"], "one load, not one per task"
        assert manager.slot("image_embed").load_count == 1
        assert manager.slot("image_embed").job_count == 3
        assert recorder.names("unload") == []
    finally:
        await manager.stop()


async def test_the_shared_model_is_charged_once_to_admission(recorder):
    """Two slots would have needed 14,000 MB of headroom for 7,000 MB of
    weights, and evicted whisperX to find it."""
    backends = shared_backends(recorder, vram=7000)
    probe = FakeVram(distinct(backends), total_mb=16000)
    manager = await make_manager(
        backends, recorder, vram_probe=probe, vram_headroom_mb=512
    )
    try:
        await manager.submit("stt", lambda b: b.infer("audio.wav"))
        await manager.submit("embed", lambda b: b.infer(["a"]))
        await manager.submit("image_embed", lambda b: b.infer([b"jpeg"]))
        # 8,000 + 7,000 + 512 headroom fits in 16,000; nothing was evicted.
        assert recorder.names("unload") == []
        assert backends["stt"].loaded and backends["embed"].loaded
    finally:
        await manager.stop()


async def test_the_shared_slot_cannot_evict_itself(recorder):
    """`_eviction_candidate` excludes by slot, not by task name. Excluding by
    name let `image_embed` nominate `embed` — the same weights — as the victim
    that would make room for it, unloading the model it was about to use."""
    backends = shared_backends(recorder, vram=7000)
    probe = FakeVram(distinct(backends), total_mb=9000)
    manager = await make_manager(backends, recorder, vram_probe=probe)
    try:
        await manager.submit("embed", lambda b: b.infer(["a"]))
        await manager.submit("image_embed", lambda b: b.infer([b"jpeg"]))
        assert recorder.names("load", "unload") == ["embed"]
    finally:
        await manager.stop()


# --------------------------------------------------------------------------
# residency and the lease
# --------------------------------------------------------------------------


async def test_residency_covers_both_tasks_or_neither(recorder):
    """One set of weights cannot be pinned for one leg and evictable for the
    other. `EMBED_RESIDENT=1` names the `embed` task; with a unified model that
    is the frame slot too."""
    backends = shared_backends(recorder)
    manager = await make_manager(
        backends, recorder, resident_tasks=("embed",), idle_unload_seconds=0.01
    )
    try:
        await manager.submit("image_embed", lambda b: b.infer([b"jpeg"]))
        assert manager.slot("image_embed").resident is True
        assert await manager.reap_idle() == []
        assert backends["embed"].loaded
    finally:
        await manager.stop()


async def test_a_resident_shared_model_never_takes_the_lease(recorder):
    """The `EMBED_RESIDENT` trap, restated for a bigger model: a resident
    backend keeps `_any_lease_holder()` true forever, so `GPU_RELEASE_CMD`
    never fires and the co-tenant is stopped for the life of the process. It
    costs ~4.4 GB standing with a unified 2B instead of 1.5 GB."""
    backends = shared_backends(recorder)
    hooks = FakeHooks(recorder)
    manager = await make_manager(
        backends,
        recorder,
        resident_tasks=("embed",),
        hook_runner=hooks,
        acquire_cmd="stop-llama",
        release_cmd="start-llama",
    )
    try:
        await manager.submit("image_embed", lambda b: b.infer([b"jpeg"]))
        assert hooks.calls == [], "a resident model holds VRAM, not the lease"
    finally:
        await manager.stop()


async def test_the_lease_is_released_once_the_shared_model_unloads(recorder):
    """Release fires when no task needs the model — which, with one slot for
    two tasks, is the same statement as "the slot unloaded"."""
    backends = shared_backends(recorder)
    hooks = FakeHooks(recorder)
    manager = await make_manager(
        backends,
        recorder,
        hook_runner=hooks,
        acquire_cmd="stop-llama",
        release_cmd="start-llama",
        idle_unload_seconds=0.01,
        # The background reaper would otherwise win the race and this would be
        # asserting on an empty list rather than on the eviction.
        idle_poll_interval=60,
    )
    try:
        await manager.submit("embed", lambda b: b.infer(["a"]))
        await manager.submit("image_embed", lambda b: b.infer([b"jpeg"]))
        assert [label for _, label in hooks.calls] == ["GPU_ACQUIRE_CMD"]

        evicted = await _reap_until(manager)
        assert evicted == ["embed"], "one eviction for one model, not one per task"
        assert [label for _, label in hooks.calls] == [
            "GPU_ACQUIRE_CMD",
            "GPU_RELEASE_CMD",
        ]
    finally:
        await manager.stop()


async def test_the_lease_is_held_while_the_other_task_still_needs_it(recorder):
    """Two *different* embedders are two slots and two lease holders: releasing
    when the first unloads would hand the card back under a model that is still
    on it. The shared case collapses to one holder; this is the control."""
    backends = shared_backends(recorder)
    backends["image_embed"] = FakeBackend(
        "image_embed", vram_estimate_mb=3200, recorder=recorder, result="siglip-ok"
    )
    hooks = FakeHooks(recorder)
    manager = await make_manager(
        backends, recorder, hook_runner=hooks, acquire_cmd="a", release_cmd="r"
    )
    try:
        await manager.submit("embed", lambda b: b.infer(["a"]))
        await manager.submit("image_embed", lambda b: b.infer([b"jpeg"]))
        await manager._unload("embed", reason="test")
        assert [label for _, label in hooks.calls] == ["GPU_ACQUIRE_CMD"]
        await manager._unload("image_embed", reason="test")
        assert [label for _, label in hooks.calls] == [
            "GPU_ACQUIRE_CMD",
            "GPU_RELEASE_CMD",
        ]
    finally:
        await manager.stop()


# --------------------------------------------------------------------------
# what /status says about it
# --------------------------------------------------------------------------


async def test_status_names_the_shared_slot_on_both_entries(recorder):
    """Still one entry per task — that is what callers look up by — but each
    says which slot it belongs to, so nobody sums `vram_estimate_mb` across two
    entries describing one model."""
    backends = shared_backends(recorder)
    manager = await make_manager(backends, recorder)
    try:
        snap = manager.snapshot()
        by_task = {b["task"]: b for b in snap["backends"]}
        assert by_task["embed"]["slot"] == "embed+image_embed"
        assert by_task["image_embed"]["slot"] == "embed+image_embed"
        assert by_task["embed"]["shared_with"] == ["image_embed"]
        assert by_task["image_embed"]["shared_with"] == ["embed"]
        assert by_task["stt"]["shared_with"] == []
        assert by_task["stt"]["slot"] == "stt"
    finally:
        await manager.stop()


async def test_status_reports_the_instructions_a_backend_applies(recorder):
    """`config['*_embed.query_prefix']` records what indexing assumed and has
    been wrong before. This is the behaviour, so the two can be compared."""

    class Instructed(FakeBackend):
        def instructions(self):
            return {"document": None, "query": "q-instr", "frame_query": "f-instr"}

    backends = shared_backends(recorder)
    backends["embed"] = backends["image_embed"] = Instructed(
        "embed", vram_estimate_mb=7000, recorder=recorder
    )
    manager = await make_manager(backends, recorder)
    try:
        by_task = {b["task"]: b for b in manager.snapshot()["backends"]}
        assert by_task["embed"]["instructions"]["query"] == "q-instr"
        assert by_task["image_embed"]["instructions"]["frame_query"] == "f-instr"
        assert by_task["stt"]["instructions"] is None
    finally:
        await manager.stop()


async def _reap_until(manager: LifecycleManager, tries: int = 50) -> list[str]:
    import asyncio

    for _ in range(tries):
        evicted = await manager.reap_idle()
        if evicted:
            return evicted
        await asyncio.sleep(0.01)
    return []


@pytest.fixture
def recorder() -> Recorder:
    return Recorder()
