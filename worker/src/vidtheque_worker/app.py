"""App assembly.

``create_app()`` takes an optional pre-built :class:`LifecycleManager` so tests
can run the whole HTTP surface against fake backends — no CUDA, no downloads,
no network.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from . import __version__
from .api import router
from .backends.base import BackendCrashed, BackendError, BackendUnavailable
from .backends.registry import UnknownBackend, build_backends
from .config import Settings, get_settings
from .gpu import GPUHookError, NvmlProbe
from .lifecycle import InsufficientVRAM, LifecycleManager

log = logging.getLogger(__name__)

DESCRIPTION = """\
Stateless inference worker for vidtheque: speech-to-text with word timestamps,
text embeddings, frame embeddings, and on-screen-text OCR. One lifecycle manager
owns the GPU — requests queue rather than compete. The transcription and text
embedding endpoints follow OpenAI's shapes so a GPU-less deployment can swap in a
hosted provider. Text and frame embeddings are separate vector spaces with
separate models and separate endpoints; they are never interchangeable. A
natural-language query reaches the frame space through the frame model's own
text tower, on /v1/embeddings/frame-query — never through /v1/embeddings.
"""


def build_manager(settings: Settings) -> LifecycleManager:
    return LifecycleManager(
        build_backends(settings),
        idle_unload_seconds=settings.idle_unload_seconds,
        resident_tasks=("embed",) if settings.embed_resident else (),
        vram_headroom_mb=settings.vram_headroom_mb,
        acquire_cmd=settings.gpu_acquire_cmd,
        release_cmd=settings.gpu_release_cmd,
        hook_timeout_seconds=settings.gpu_hook_timeout_seconds,
        vram_probe=NvmlProbe(settings.gpu_index),
    )


def create_app(
    *, settings: Settings | None = None, manager: LifecycleManager | None = None
) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = settings
        app.state.manager = manager if manager is not None else build_manager(settings)
        await app.state.manager.start()
        try:
            yield
        finally:
            await app.state.manager.stop()

    app = FastAPI(
        title="vidtheque-worker",
        version=__version__,
        description=DESCRIPTION,
        lifespan=lifespan,
    )
    app.include_router(router)
    _install_error_handlers(app)
    return app


def _error(status_code: int, message: str, kind: str, headers: dict | None = None):
    return JSONResponse(
        status_code=status_code,
        headers=headers,
        content={"error": {"message": message, "type": kind}},
    )


def _install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(BackendUnavailable)
    async def _unavailable(_: Request, exc: BackendUnavailable):
        return _error(503, str(exc), "backend_unavailable", {"Retry-After": "60"})

    @app.exception_handler(BackendCrashed)
    async def _crashed(_: Request, exc: BackendCrashed):
        # Transient by construction: the manager already unloaded the slot, so
        # the retry mcp/ is about to make loads a clean model.
        return _error(503, str(exc), "backend_crashed", {"Retry-After": "30"})

    @app.exception_handler(InsufficientVRAM)
    async def _vram(_: Request, exc: InsufficientVRAM):
        return _error(503, str(exc), "insufficient_vram", {"Retry-After": "30"})

    @app.exception_handler(GPUHookError)
    async def _hook(_: Request, exc: GPUHookError):
        return _error(503, str(exc), "gpu_lease_failed", {"Retry-After": "30"})

    @app.exception_handler(UnknownBackend)
    async def _unknown(_: Request, exc: UnknownBackend):
        return _error(500, str(exc), "unknown_backend")

    @app.exception_handler(BackendError)
    async def _backend(_: Request, exc: BackendError):
        return _error(500, str(exc), "backend_error")


app = create_app()
