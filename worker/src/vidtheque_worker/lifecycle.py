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
* **Lease hooks.** ``GPU_ACQUIRE_CMD`` runs before the first load of a
  *non-resident GPU* model and ``GPU_RELEASE_CMD`` once none is left loaded, so
  a co-tenant (llama.cpp, say) can be stopped and restarted around a burst of
  indexing work without this code knowing anything about it. CPU backends and
  resident models are outside that bracket — see :func:`_takes_lease`.

Backends are synchronous; jobs run in a worker thread so the event loop keeps
serving ``/status`` and ``/healthz`` while the GPU is busy.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, TypeVar

from .backends.base import (
    Backend,
    BackendCrashed,
    BackendError,
    BackendInputError,
    BackendUnavailable,
    looks_like_device_failure,
)
from .gpu import GPUHookError, NvmlProbe, VramInfo, run_shell_hook

log = logging.getLogger(__name__)

T = TypeVar("T")

VramProbe = Callable[[], VramInfo | None]
HookRunner = Callable[[str, str], Awaitable[None]]
"""``(command, label) -> awaitable``. Injectable so tests never spawn a shell."""


class InsufficientVRAM(RuntimeError):
    """Not enough free VRAM for a load, even after evicting what we may."""


class ManagerNotRunning(BackendUnavailable):
    """submit() before start(), or after stop().

    A ``BackendUnavailable`` rather than a bare ``RuntimeError`` so it answers
    503 + ``Retry-After`` like every other "not now" instead of a bare 500: the
    job never reached a model, so replaying it against the next process is
    exactly the right thing for the client to do.
    """

    code = "worker_not_ready"


class WorkerShuttingDown(ManagerNotRunning):
    """The manager stopped before this job ran. Nothing was inferred."""

    code = "worker_shutting_down"


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
        shutdown_grace_seconds: float = 30.0,
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
        self.shutdown_grace_seconds = shutdown_grace_seconds
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
        self._stopping = False
        self._inference_done: threading.Event | None = None
        """Set by the worker thread when the last dispatched job function
        returned. ``to_thread`` cannot be cancelled, so this is the only handle
        :meth:`stop` has on a thread that is still inside the model."""
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
        self._stopping = False
        self._runner = asyncio.create_task(self._run_queue(), name="vidtheque-gpu-queue")
        self._reaper = asyncio.create_task(self._run_reaper(), name="vidtheque-idle-reaper")
        log.info(
            "lifecycle manager started: tasks=%s idle_unload=%ss resident=%s",
            ",".join(self._slots),
            self.idle_unload_seconds,
            ",".join(t for t, s in self._slots.items() if s.resident) or "none",
        )

    async def stop(self) -> None:
        """Tear down: refuse new work, resolve every queued job, then unload.

        The order matters and each step is one of the two ways this used to
        wedge a shutdown.

        ``_stopping`` goes up first so a request racing the teardown is
        refused rather than queued behind a runner that is already gone —
        :meth:`submit` has no await between its check and its ``put``, so the
        flag closes the window completely.

        Then the queue is *drained*. Cancelling the runner only ever resolved
        the job it happened to be running: everything already queued kept a
        pending future, the handlers awaiting those futures never returned,
        and the lifespan hung until uvicorn's timeout killed the process.

        Then the in-flight worker thread is given a bounded grace period.
        ``asyncio.to_thread`` cannot be cancelled — the awaiting coroutine is
        abandoned, the thread runs on — so unloading straight after the cancel
        raced ``_model = None`` and ``empty_cache()`` against a thread still
        inside the model. **Residual, deliberately:** if the grace expires the
        unload proceeds anyway, because a shutdown that never finishes is
        worse than the race it is avoiding. Size ``SHUTDOWN_GRACE_SECONDS``
        for the longest job you are willing to wait on.
        """
        self._stopping = True
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
        self._drain_queue("the worker is shutting down; the job never ran")
        await self._await_inference()
        async with self._gpu_lock:
            await self._unload_all(reason="shutdown", include_resident=True)

    def _drain_queue(self, reason: str) -> int:
        """Resolve every queued job with :class:`WorkerShuttingDown`.

        Nothing else consumes ``_queue``, so a job left in it is a caller
        awaiting a future that no longer has anyone to complete it.
        """
        failed = 0
        while True:
            try:
                job = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            self._queue.task_done()
            if not job.future.done():
                job.future.set_exception(WorkerShuttingDown(reason))
                failed += 1
        if failed:
            log.warning("failed %d queued job(s): %s", failed, reason)
        return failed

    async def _await_inference(self) -> None:
        """Wait, bounded, for the worker thread of the last dispatched job."""
        done = self._inference_done
        if done is None or done.is_set() or self.shutdown_grace_seconds <= 0:
            return
        log.info(
            "waiting up to %.1fs for the in-flight job before unloading",
            self.shutdown_grace_seconds,
        )
        finished = await asyncio.to_thread(done.wait, self.shutdown_grace_seconds)
        if not finished:
            log.warning(
                "in-flight job still running after %.1fs; unloading anyway",
                self.shutdown_grace_seconds,
            )

    # -- public API --------------------------------------------------------
    async def submit(self, task: str, fn: Callable[[Backend], T], *, label: str = "") -> T:
        """Queue GPU work for ``task`` and await its result.

        ``fn`` receives the loaded backend and runs in a thread. It must not
        assume anything is loaded before it is called, and must not unload.
        """
        if task not in self._slots:
            raise KeyError(f"no backend registered for task {task!r}")
        if self._stopping:
            raise WorkerShuttingDown("the worker is shutting down; the job never ran")
        if self._runner is None or self._runner.done():
            raise ManagerNotRunning("lifecycle manager is not running")

        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        job = _Job(task=task, fn=fn, future=future, enqueued_at=self._clock(), label=label)
        self._in_flight += 1
        try:
            # Unbounded queue: `put` never awaits, so nothing can run between
            # the liveness check above and the job landing in the queue.
            await self._queue.put(job)
            return await future
        finally:
            self._in_flight -= 1

    def snapshot(self) -> dict[str, Any]:
        """Everything ``GET /status`` reports. Cheap enough to call per request.

        Read *without* ``_gpu_lock``, deliberately. Taking it would make
        ``/status`` queue behind whatever the GPU is doing, and a status
        endpoint that blocks for the length of a transcription is worse than
        one that can catch a load mid-eviction and report the transitional
        picture. Every field is a plain attribute read, so nothing here can
        tear; only the cross-slot composition can be a moment out of date.
        """
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
                # The one field that distinguishes "busy" from "wedged": with a
                # dead consumer, depth and in_flight climb and nothing else on
                # this page changes.
                "consumer_alive": self._runner is not None and not self._runner.done(),
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
        """The single consumer. Its death is the queue's death, so it says so.

        Whatever ends this task — the cancel in :meth:`stop`, or a bug nobody
        has written yet — every job left in the queue has a caller awaiting a
        future that will now never be completed by anyone. Draining on the way
        out turns "the request hangs until the client's timeout" into a 503
        that says what happened.
        """
        try:
            while True:
                job = await self._queue.get()
                try:
                    await self._execute(job)
                except asyncio.CancelledError:
                    if not job.future.done():
                        job.future.set_exception(
                            WorkerShuttingDown(
                                f"{job.task} was interrupted by shutdown; "
                                "it may or may not have run"
                            )
                        )
                    raise
                finally:
                    self._queue.task_done()
        except BaseException:
            self._drain_queue("the GPU queue runner stopped")
            raise

    async def _execute(self, job: _Job) -> None:
        slot = self._slots[job.task]
        try:
            async with self._gpu_lock:
                self._running_task = job.task
                try:
                    await self._ensure_loaded(job.task)
                    try:
                        result = await self._dispatch(job, slot)
                    except Exception as exc:
                        await self._job_raised(slot, exc)
                        raise
                finally:
                    self._running_task = None
                    slot.last_used = self._clock()
            slot.job_count += 1
            if not job.future.done():
                job.future.set_result(result)
        except asyncio.CancelledError:
            raise
        except BaseException as exc:  # noqa: BLE001 - the caller gets the exception
            log.warning(
                "job failed task=%s label=%s: %s",
                job.task,
                job.label,
                exc,
                # A rejected upload is not worth a traceback; anything else is.
                exc_info=not isinstance(exc, BackendInputError),
            )
            if not job.future.done():
                job.future.set_exception(exc)

    async def _dispatch(self, job: _Job, slot: Slot) -> Any:
        """Run the job function in a thread, leaving a completion flag behind.

        The flag outlives the await on purpose: cancelling this coroutine
        abandons the thread rather than stopping it, and :meth:`stop` needs
        something to wait on before it starts freeing weights out from under
        that thread.
        """
        done = threading.Event()
        self._inference_done = done
        return await asyncio.to_thread(_call_and_signal, job.fn, slot.backend, done)

    async def _job_raised(self, slot: Slot, exc: Exception) -> None:
        """Decide what a failed *inference* did to the model, under the lock.

        Unloading is for the device failing, not for the job failing. A CUDA
        OOM leaves the model's context poisoned, and a slot left ``loaded``
        then answers every later job with ``invalid device ordinal`` until the
        process restarts (measured in ``research/gpu-validation-2026-08-08.md``
        §5.1 — with ``IDLE_UNLOAD_SECONDS=0`` it never recovers at all). So a
        :class:`BackendPoisoned` — or an untyped exception that
        :func:`looks_like_device_failure` recognises — unloads the slot, which
        also frees its VRAM and, if it was the last non-resident GPU model,
        gives the lease back.

        The discriminator used to be ``isinstance(exc, RuntimeError)``, which
        is what whisperX raises for a malformed media stream, torch for a dtype
        mismatch and onnxruntime for a shape error. Each of those cost a full
        reload (5.8 s for the frame embedder) *and* came back as 503, which the
        mcp client reads as backpressure and answers by replaying the same
        input until the 1800 s budget is gone. None of them are the device
        talking, so none of them unload anything now.

        What is left keeps its type when it has one, and gets wrapped in a
        typed :class:`BackendError` when it does not — an untyped exception
        would otherwise reach FastAPI's bare-500 path, stack trace and all,
        with no ``error.type`` for the caller to classify.
        """
        if not _unloads_the_slot(exc):
            if isinstance(exc, BackendError):
                return  # already typed; the HTTP layer knows how to answer it
            raise BackendError(
                f"{slot.task} backend raised {type(exc).__name__}: {exc}"
            ) from exc
        log.warning("unloading %s after a device failure: %s", slot.task, exc)
        try:
            await self._unload(slot.task, reason="job failed")
        except Exception:  # pragma: no cover - a backend whose unload also dies
            log.exception("unload of %s failed after a failed job", slot.task)
        if isinstance(exc, BackendError):
            return  # already typed; the HTTP layer knows how to answer it
        raise BackendCrashed(
            f"{slot.task} backend failed and was unloaded, retry: {exc}"
        ) from exc

    # -- loading / unloading (always under _gpu_lock) ----------------------
    async def _ensure_loaded(self, task: str) -> None:
        slot = self._slots[task]
        if slot.loaded:
            return

        await self._admit(slot)
        acquired_here = await self._acquire_lease() if _takes_lease(slot) else False
        try:
            started = self._clock()
            await asyncio.to_thread(slot.backend.load)
        except BaseException:
            if acquired_here and not self._any_lease_holder():
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
        if not self._any_lease_holder():
            await self._release_lease()

    async def _unload_all(self, *, reason: str, include_resident: bool = False) -> None:
        for task, slot in self._slots.items():
            if slot.loaded and (include_resident or not slot.resident):
                await self._unload(task, reason=reason)

    def _any_lease_holder(self) -> bool:
        """Is any model that the lease is *for* still loaded?"""
        return any(slot.loaded and _takes_lease(slot) for slot in self._slots.values())

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


def _call_and_signal(
    fn: Callable[[Backend], Any], backend: Backend, done: threading.Event
) -> Any:
    try:
        return fn(backend)
    finally:
        done.set()


def _unloads_the_slot(exc: BaseException) -> bool:
    """Is the loaded model worth keeping after this exception?

    Two cases say no. A device failure (:func:`looks_like_device_failure`) —
    the context is poisoned and every later job on that instance fails. And a
    :class:`BackendUnavailable` raised *from inference*, which means the
    backend says it cannot serve while its slot says ``loaded``: the two have
    drifted, and unloading costs one reload of something that was not working
    anyway. Everything else leaves a perfectly good model where it is.
    """
    return looks_like_device_failure(exc) or isinstance(exc, BackendUnavailable)


def _takes_lease(slot: Slot) -> bool:
    """Does loading this backend belong inside the GPU lease?

    Two exclusions, both measured on hardware
    (``research/gpu-validation-2026-08-08.md`` §5.2, §5.3):

    * **A 0 MB backend** — OCR runs on CPU. Taking the lease for it stops a
      co-tenant that OCR was never going to compete with, and holds it stopped
      for the whole idle TTL in exchange for nothing.
    * **A resident backend** — ``EMBED_RESIDENT=1`` keeps the text embedder
      loaded for the life of the process (1.5 GB measured). Bracketing it would
      acquire the lease at the first embedding request and never release it, so
      the co-tenant would be stopped forever. A resident model holds VRAM; it
      does not hold the lease.
    """
    return slot.backend.vram_estimate_mb > 0 and not slot.resident


def _default_poll(idle_unload_seconds: float) -> float:
    if idle_unload_seconds <= 0:
        return 30.0
    return max(0.05, min(30.0, idle_unload_seconds / 4))
