"""The lifecycle manager: one object owns the GPU.

Everything GPU-touching in this worker goes through :meth:`LifecycleManager.submit`.
That buys four properties that are painful to retrofit:

* **Serialization.** A single consumer task drains one job queue, so ten
  concurrent HTTP requests become ten sequential GPU jobs instead of an OOM.
* **Load on demand.** Weights are pulled only when a job needs them, and an
  idle model is evicted after ``IDLE_UNLOAD_SECONDS`` — except a resident
  embedding model, which is exempt because query latency beats its ~2 GB.
* **Admission control.** Before a load, NVML is asked how much VRAM is free;
  if the estimate does not fit, the least-recently-used non-resident backend is
  evicted first. No NVML installed means "no idea", and no idea means proceed.
* **Lease hooks.** ``GPU_ACQUIRE_CMD`` runs before the *first* load and
  ``GPU_RELEASE_CMD`` after the *last* unload, so a co-tenant (llama.cpp, say)
  can be stopped and restarted around a burst of indexing work without this
  code knowing anything about it.

Backends are synchronous; jobs run in a worker thread so the event loop keeps
serving ``/status`` and ``/healthz`` while the GPU is busy.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, TypeVar

from .backends.base import Backend
from .gpu import GPUHookError, NvmlProbe, VramInfo, run_shell_hook

log = logging.getLogger(__name__)

T = TypeVar("T")

VramProbe = Callable[[], VramInfo | None]
HookRunner = Callable[[str, str], Awaitable[None]]
"""``(command, label) -> awaitable``. Injectable so tests never spawn a shell."""


class InsufficientVRAM(RuntimeError):
    """Not enough free VRAM for a load, even after evicting what we may."""


class ManagerNotRunning(RuntimeError):
    """submit() before start(), or after stop()."""


@dataclass(slots=True)
class Slot:
    task: str
    backend: Backend
    resident: bool = False
    last_used: float | None = None
    loaded_at: float | None = None
    load_count: int = 0
    unload_count: int = 0
    job_count: int = 0

    @property
    def loaded(self) -> bool:
        return self.backend.loaded


@dataclass(slots=True)
class _Job:
    task: str
    fn: Callable[[Backend], Any]
    future: asyncio.Future
    enqueued_at: float
    label: str = ""


@dataclass(slots=True)
class HookLog:
    """What the manager has done to the lease, for /status and for tests."""

    acquired: bool = False
    events: list[str] = field(default_factory=list)


class LifecycleManager:
    def __init__(
        self,
        backends: Mapping[str, Backend],
        *,
        idle_unload_seconds: float = 300.0,
        resident_tasks: Iterable[str] = (),
        vram_headroom_mb: int = 512,
        acquire_cmd: str | None = None,
        release_cmd: str | None = None,
        hook_timeout_seconds: float = 60.0,
        vram_probe: VramProbe | None = None,
        hook_runner: HookRunner | None = None,
        idle_poll_interval: float | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        resident = set(resident_tasks)
        self._slots: dict[str, Slot] = {
            task: Slot(task=task, backend=backend, resident=task in resident)
            for task, backend in backends.items()
        }
        self.idle_unload_seconds = idle_unload_seconds
        self.vram_headroom_mb = vram_headroom_mb
        self.acquire_cmd = acquire_cmd
        self.release_cmd = release_cmd
        self.hook_timeout_seconds = hook_timeout_seconds
        self._vram_probe: VramProbe = vram_probe or NvmlProbe()
        self._hook_runner: HookRunner = hook_runner or self._default_hook_runner
        self._clock = clock
        self._idle_poll_interval = idle_poll_interval or _default_poll(idle_unload_seconds)

        self._queue: asyncio.Queue[_Job] = asyncio.Queue()
        self._gpu_lock = asyncio.Lock()
        self._runner: asyncio.Task | None = None
        self._reaper: asyncio.Task | None = None
        self._running_task: str | None = None
        self._in_flight = 0
        self.hooks = HookLog()

    # -- plumbing ----------------------------------------------------------
    async def _default_hook_runner(self, command: str, label: str) -> None:
        await run_shell_hook(command, timeout=self.hook_timeout_seconds, label=label)

    @property
    def tasks(self) -> list[str]:
        return list(self._slots)

    def slot(self, task: str) -> Slot:
        return self._slots[task]

    # -- start / stop ------------------------------------------------------
    async def start(self) -> None:
        if self._runner is not None:
            return
        self._runner = asyncio.create_task(self._run_queue(), name="vidtheque-gpu-queue")
        self._reaper = asyncio.create_task(self._run_reaper(), name="vidtheque-idle-reaper")
        log.info(
            "lifecycle manager started: tasks=%s idle_unload=%ss resident=%s",
            ",".join(self._slots),
            self.idle_unload_seconds,
            ",".join(t for t, s in self._slots.items() if s.resident) or "none",
        )

    async def stop(self) -> None:
        for task in (self._reaper, self._runner):
            if task is not None:
                task.cancel()
        for task in (self._reaper, self._runner):
            if task is not None:
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._reaper = self._runner = None
        async with self._gpu_lock:
            await self._unload_all(reason="shutdown", include_resident=True)

    # -- public API --------------------------------------------------------
    async def submit(self, task: str, fn: Callable[[Backend], T], *, label: str = "") -> T:
        """Queue GPU work for ``task`` and await its result.

        ``fn`` receives the loaded backend and runs in a thread. It must not
        assume anything is loaded before it is called, and must not unload.
        """
        if task not in self._slots:
            raise KeyError(f"no backend registered for task {task!r}")
        if self._runner is None or self._runner.done():
            raise ManagerNotRunning("lifecycle manager is not running")

        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        self._in_flight += 1
        await self._queue.put(
            _Job(task=task, fn=fn, future=future, enqueued_at=self._clock(), label=label)
        )
        try:
            return await future
        finally:
            self._in_flight -= 1

    def snapshot(self) -> dict[str, Any]:
        """Everything ``GET /status`` reports. Cheap enough to call per request."""
        now = self._clock()
        vram = self._vram_probe()
        backends = []
        for task, slot in self._slots.items():
            backends.append(
                {
                    "task": task,
                    "backend": getattr(slot.backend, "name", type(slot.backend).__name__),
                    "model": getattr(slot.backend, "model_id", None),
                    "loaded": slot.loaded,
                    "resident": slot.resident,
                    "vram_estimate_mb": slot.backend.vram_estimate_mb,
                    "idle_seconds": (
                        None if slot.last_used is None else round(now - slot.last_used, 3)
                    ),
                    "load_count": slot.load_count,
                    "unload_count": slot.unload_count,
                    "job_count": slot.job_count,
                }
            )
        return {
            "backends": backends,
            "vram": (
                {
                    "available": True,
                    "total_mb": vram.total_mb,
                    "used_mb": vram.used_mb,
                    "free_mb": vram.free_mb,
                    "source": vram.source,
                }
                if vram is not None
                else {"available": False, "reason": "nvml unavailable"}
            ),
            "queue": {
                "depth": self._queue.qsize(),
                "in_flight": self._in_flight,
                "running": self._running_task,
            },
            "lease": {
                "acquired": self.hooks.acquired,
                "acquire_cmd_configured": bool(self.acquire_cmd),
                "release_cmd_configured": bool(self.release_cmd),
            },
            "idle_unload_seconds": self.idle_unload_seconds,
        }

    # -- queue consumer ----------------------------------------------------
    async def _run_queue(self) -> None:
        while True:
            job = await self._queue.get()
            try:
                await self._execute(job)
            except asyncio.CancelledError:
                if not job.future.done():
                    job.future.cancel()
                raise
            finally:
                self._queue.task_done()

    async def _execute(self, job: _Job) -> None:
        slot = self._slots[job.task]
        try:
            async with self._gpu_lock:
                self._running_task = job.task
                try:
                    await self._ensure_loaded(job.task)
                    result = await asyncio.to_thread(job.fn, slot.backend)
                finally:
                    self._running_task = None
                    slot.last_used = self._clock()
            slot.job_count += 1
            if not job.future.done():
                job.future.set_result(result)
        except asyncio.CancelledError:
            raise
        except BaseException as exc:  # noqa: BLE001 - the caller gets the exception
            log.warning("job failed task=%s label=%s: %s", job.task, job.label, exc)
            if not job.future.done():
                job.future.set_exception(exc)

    # -- loading / unloading (always under _gpu_lock) ----------------------
    async def _ensure_loaded(self, task: str) -> None:
        slot = self._slots[task]
        if slot.loaded:
            return

        await self._admit(slot)
        acquired_here = await self._acquire_lease()
        try:
            started = self._clock()
            await asyncio.to_thread(slot.backend.load)
        except BaseException:
            if acquired_here and not self._any_loaded():
                await self._release_lease()
            raise
        slot.loaded_at = self._clock()
        slot.last_used = slot.loaded_at
        slot.load_count += 1
        log.info(
            "loaded %s backend=%s in %.1fs",
            task,
            getattr(slot.backend, "name", "?"),
            slot.loaded_at - started,
        )

    async def _admit(self, slot: Slot) -> None:
        """Make room for ``slot``, or explain why we cannot."""
        needed = slot.backend.vram_estimate_mb + self.vram_headroom_mb
        if slot.backend.vram_estimate_mb <= 0:
            return  # CPU backend: nothing to account for

        info = self._vram_probe()
        if info is None:
            return  # no NVML: proceed rather than block

        while info.free_mb < needed:
            victim = self._eviction_candidate(exclude=slot.task)
            if victim is None:
                raise InsufficientVRAM(
                    f"{slot.task} needs ~{needed} MB (estimate "
                    f"{slot.backend.vram_estimate_mb} + headroom {self.vram_headroom_mb}), "
                    f"{info.free_mb} MB free and nothing evictable"
                )
            await self._unload(victim, reason="vram pressure")
            probed = self._vram_probe()
            if probed is None:
                return
            info = probed

    def _eviction_candidate(self, *, exclude: str) -> str | None:
        candidates = [
            slot
            for task, slot in self._slots.items()
            if task != exclude
            and slot.loaded
            and not slot.resident
            and slot.backend.vram_estimate_mb > 0
        ]
        if not candidates:
            return None
        candidates.sort(key=lambda s: (s.last_used if s.last_used is not None else 0.0))
        return candidates[0].task

    async def _unload(self, task: str, *, reason: str) -> None:
        slot = self._slots[task]
        if not slot.loaded:
            return
        log.info("unloading %s (%s)", task, reason)
        await asyncio.to_thread(slot.backend.unload)
        slot.unload_count += 1
        slot.loaded_at = None
        if not self._any_loaded():
            await self._release_lease()

    async def _unload_all(self, *, reason: str, include_resident: bool = False) -> None:
        for task, slot in self._slots.items():
            if slot.loaded and (include_resident or not slot.resident):
                await self._unload(task, reason=reason)

    def _any_loaded(self) -> bool:
        return any(slot.loaded for slot in self._slots.values())

    # -- lease hooks -------------------------------------------------------
    async def _acquire_lease(self) -> bool:
        """Run the acquire hook if this is the first load. Returns whether it ran."""
        if self.hooks.acquired:
            return False
        self.hooks.acquired = True
        self.hooks.events.append("acquire")
        if not self.acquire_cmd:
            return True
        try:
            await self._hook_runner(self.acquire_cmd, "GPU_ACQUIRE_CMD")
        except Exception as exc:
            self.hooks.acquired = False
            self.hooks.events.append("acquire-failed")
            raise GPUHookError(f"GPU_ACQUIRE_CMD failed: {exc}") from exc
        return True

    async def _release_lease(self) -> None:
        if not self.hooks.acquired:
            return
        self.hooks.acquired = False
        self.hooks.events.append("release")
        if not self.release_cmd:
            return
        try:
            await self._hook_runner(self.release_cmd, "GPU_RELEASE_CMD")
        except Exception as exc:  # releasing is best-effort: never fail a request on it
            log.error("GPU_RELEASE_CMD failed: %s", exc)
            self.hooks.events.append("release-failed")

    # -- idle reaper -------------------------------------------------------
    async def _run_reaper(self) -> None:
        while True:
            await asyncio.sleep(self._idle_poll_interval)
            if self.idle_unload_seconds <= 0:
                continue
            await self.reap_idle()

    async def reap_idle(self) -> list[str]:
        """Unload every non-resident backend idle past the TTL. Returns their tasks."""
        if self.idle_unload_seconds <= 0:
            return []
        evicted: list[str] = []
        async with self._gpu_lock:
            now = self._clock()
            for task, slot in self._slots.items():
                if not slot.loaded or slot.resident:
                    continue
                reference = slot.last_used if slot.last_used is not None else slot.loaded_at
                if reference is None:
                    continue
                if now - reference >= self.idle_unload_seconds:
                    await self._unload(task, reason="idle")
                    evicted.append(task)
        return evicted


def _default_poll(idle_unload_seconds: float) -> float:
    if idle_unload_seconds <= 0:
        return 30.0
    return max(0.05, min(30.0, idle_unload_seconds / 4))
