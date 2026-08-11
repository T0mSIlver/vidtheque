"""GPU facts and GPU policy hooks.

Two small things the lifecycle manager needs and neither of which it should
own: how much VRAM is free (NVML, optional dependency) and how to ask whatever
else is on the box to get out of the way (``GPU_ACQUIRE_CMD`` /
``GPU_RELEASE_CMD`` shell hooks). Keeping the lease policy in a shell command
is deliberate — the owner's llama.cpp arrangement stays out of shared code.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class VramInfo:
    total_mb: int
    used_mb: int
    free_mb: int
    source: str = "nvml"


class GPUHookError(RuntimeError):
    """A GPU acquire/release hook failed."""


class NvmlProbe:
    """Free-VRAM probe backed by NVML, degrading to ``None`` when unavailable.

    ``None`` means "no idea" and the caller should proceed rather than block:
    a worker on a box without NVML is a supported configuration, an unusable
    worker is not.

    **These numbers are not ``nvidia-smi``'s numbers.** See :meth:`__call__`.
    """

    def __init__(self, index: int = 0) -> None:
        self.index = index
        self._pynvml = None
        self._handle = None
        self._unavailable_logged = False

    def _ensure(self) -> bool:
        if self._handle is not None:
            return True
        try:
            import pynvml
        except ImportError:
            self._log_unavailable("pynvml/nvidia-ml-py not installed")
            return False
        try:
            pynvml.nvmlInit()
            self._handle = pynvml.nvmlDeviceGetHandleByIndex(self.index)
            self._pynvml = pynvml
            return True
        except Exception as exc:
            self._log_unavailable(f"NVML init failed: {exc}")
            return False

    def _log_unavailable(self, reason: str) -> None:
        if not self._unavailable_logged:
            log.info("VRAM accounting disabled (%s); loads will not be gated", reason)
            self._unavailable_logged = True

    def __call__(self) -> VramInfo | None:
        """Free/used/total VRAM, as NVML sees it — deliberately not as
        ``nvidia-smi`` sees it.

        ``nvmlDeviceGetMemoryInfo`` counts the driver's own reserve in ``used``
        and ``nvidia-smi memory.used`` does not: a constant **~322 MB** gap on
        the reference box (RTX 3090, driver 550.163.01 — measured in
        ``research/gpu-validation-2026-08-08.md`` §2). ``/status`` therefore
        reports ~322 MB more used, and admission control believes that much less
        is free, than the card appears to have. That is the conservative
        direction and is left alone on purpose: it is not a leak, and there is
        nothing here to "fix". Related: a process that has ever touched CUDA
        keeps a ~340 MB primary context until it exits, which no unload frees.
        """
        if not self._ensure():
            return None
        try:
            info = self._pynvml.nvmlDeviceGetMemoryInfo(self._handle)
        except Exception as exc:  # pragma: no cover - transient NVML failure
            log.warning("NVML query failed: %s", exc)
            return None
        mb = 1024 * 1024
        return VramInfo(
            total_mb=int(info.total // mb),
            used_mb=int(info.used // mb),
            free_mb=int(info.free // mb),
        )


async def run_shell_hook(command: str, *, timeout: float = 60.0, label: str = "hook") -> None:
    """Run a shell hook, raising :class:`GPUHookError` on failure or timeout.

    Nothing request-derived reaches ``command`` — it comes from the operator's
    environment and only from there — so this is not an injection surface. Two
    other things about it were real:

    * **The error named the command.** ``GPUHookError`` is returned to the
      caller as a 503 body, so a lease hook holding an inline bearer token for
      the llama.cpp host published it to whoever triggered a cold load. The
      label identifies which hook failed and is all a caller can act on; the
      command goes to the log, where the operator already has it.
    * **The timeout killed the shell, not its children.** ``/bin/sh -c`` exits
      and whatever it spawned carries on holding the VRAM the timeout was
      about. Killing the process group is what the timeout meant.

    (2026-08-10 audit, F-31.)
    """
    log.info("running %s: %s", label, command)
    proc = await asyncio.create_subprocess_shell(
        command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        start_new_session=True,
    )
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        _kill_group(proc)
        await proc.wait()
        log.warning("%s timed out after %ss: %s", label, timeout, command)
        raise GPUHookError(f"{label} timed out after {timeout}s") from None

    output = (stdout or b"").decode(errors="replace").strip()
    if output:
        log.info("%s output: %s", label, output)
    if proc.returncode != 0:
        log.warning("%s exited %s: %s", label, proc.returncode, command)
        raise GPUHookError(f"{label} exited {proc.returncode}")


def _kill_group(proc: "asyncio.subprocess.Process") -> None:
    """SIGKILL the hook's whole process group, falling back to the shell."""
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):  # pragma: no cover
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
