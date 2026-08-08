"""HTTP surface.

Handlers do three things and no more: parse, hand a closure to the lifecycle
manager, shape the result. Nothing here touches a model, a device or a lock —
that is the manager's job, and keeping the split clean is what lets a backend
be swapped by an env var.
"""

from __future__ import annotations

import logging
import os
import tempfile
from typing import Annotated, Any

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import PlainTextResponse

from . import __version__
from .backends.base import Backend
from .lifecycle import LifecycleManager
from .schemas import (
    EmbeddingsRequest,
    EmbeddingsResponse,
    HealthResponse,
    OCRResponse,
    StatusResponse,
    TranscriptionOut,
    VerboseTranscriptionOut,
    to_embeddings_response,
    to_ocr_response,
    to_verbose,
)

log = logging.getLogger(__name__)

router = APIRouter()

MAX_EMBED_BATCH = 512
MAX_OCR_IMAGES = 64


def get_manager(request: Request) -> LifecycleManager:
    manager: LifecycleManager | None = getattr(request.app.state, "manager", None)
    if manager is None:  # pragma: no cover - only if lifespan was skipped
        raise HTTPException(status_code=503, detail="worker is still starting")
    return manager


def _backend_name(manager: LifecycleManager, task: str) -> str | None:
    return getattr(manager.slot(task).backend, "name", None)


def _model_id(manager: LifecycleManager, task: str) -> str | None:
    return getattr(manager.slot(task).backend, "model_id", None)


# --------------------------------------------------------------------------
# POST /v1/audio/transcriptions
# --------------------------------------------------------------------------


@router.post(
    "/v1/audio/transcriptions",
    response_model=None,
    summary="Transcribe audio (OpenAI-compatible)",
)
async def transcriptions(
    request: Request,
    file: Annotated[UploadFile, File(description="Audio or video file")],
    model: Annotated[str | None, Form()] = None,
    language: Annotated[str | None, Form()] = None,
    response_format: Annotated[str, Form()] = "json",
    temperature: Annotated[float, Form()] = 0.0,
    timestamp_granularities: Annotated[
        list[str] | None, Form(alias="timestamp_granularities[]")
    ] = None,
) -> Any:
    if response_format not in {"json", "verbose_json", "text"}:
        raise HTTPException(
            status_code=400,
            detail=f"unsupported response_format {response_format!r}; "
            "use json, verbose_json or text",
        )

    manager = get_manager(request)
    configured = _model_id(manager, "stt")
    if model and configured and model != configured:
        log.info("ignoring requested model=%s; worker serves %s", model, configured)

    # Word timestamps are the point of this worker, so alignment is on unless
    # the caller explicitly asks for segment granularity only.
    align = True
    if timestamp_granularities:
        align = "word" in {g.strip().lower() for g in timestamp_granularities}

    path = await _spool(file)
    try:
        def job(backend: Backend) -> Any:
            return backend.infer(path, language=language, align=align)

        result = await manager.submit("stt", job, label=file.filename or "audio")
    finally:
        _cleanup(path)

    if response_format == "text":
        return PlainTextResponse(result.text)
    if response_format == "json":
        return TranscriptionOut(text=result.text)
    return to_verbose(result, model=configured, backend=_backend_name(manager, "stt"))


async def _spool(upload: UploadFile) -> str:
    """Persist the upload: STT backends want a path, not a stream."""
    suffix = os.path.splitext(upload.filename or "")[1] or ".bin"
    fd, path = tempfile.mkstemp(prefix="vidtheque-", suffix=suffix)
    try:
        with os.fdopen(fd, "wb") as out:
            while chunk := await upload.read(1024 * 1024):
                out.write(chunk)
    except BaseException:
        _cleanup(path)
        raise
    return path


def _cleanup(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:  # pragma: no cover - already gone
        pass


# --------------------------------------------------------------------------
# POST /v1/embeddings
# --------------------------------------------------------------------------


@router.post(
    "/v1/embeddings",
    response_model=EmbeddingsResponse,
    summary="Embed text (OpenAI-compatible)",
)
async def embeddings(request: Request, body: EmbeddingsRequest) -> EmbeddingsResponse:
    texts = body.texts()
    if not texts:
        raise HTTPException(status_code=400, detail="input must not be empty")
    if len(texts) > MAX_EMBED_BATCH:
        raise HTTPException(
            status_code=413,
            detail=f"input has {len(texts)} items; max {MAX_EMBED_BATCH} per request",
        )
    if any(not isinstance(t, str) for t in texts):
        raise HTTPException(status_code=400, detail="input items must be strings")

    manager = get_manager(request)
    configured = _model_id(manager, "embed")
    if body.model and configured and body.model != configured:
        log.info("ignoring requested model=%s; worker serves %s", body.model, configured)

    def job(backend: Backend) -> Any:
        return backend.infer(texts)

    result = await manager.submit("embed", job, label=f"{len(texts)} texts")
    return to_embeddings_response(result)


# --------------------------------------------------------------------------
# POST /v1/ocr
# --------------------------------------------------------------------------


@router.post("/v1/ocr", response_model=OCRResponse, summary="OCR one or more images")
async def ocr(
    request: Request,
    file: Annotated[list[UploadFile], File(description="One or more image files")],
    min_confidence: Annotated[float | None, Form()] = None,
) -> OCRResponse:
    if not file:
        raise HTTPException(status_code=400, detail="at least one file is required")
    if len(file) > MAX_OCR_IMAGES:
        raise HTTPException(
            status_code=413,
            detail=f"{len(file)} images; max {MAX_OCR_IMAGES} per request",
        )

    manager = get_manager(request)
    blobs = [await upload.read() for upload in file]
    filenames = [upload.filename for upload in file]
    if any(not blob for blob in blobs):
        raise HTTPException(status_code=400, detail="one or more uploads were empty")

    kwargs = {} if min_confidence is None else {"min_confidence": min_confidence}

    def job(backend: Backend) -> Any:
        return backend.infer(blobs, **kwargs)

    result = await manager.submit("ocr", job, label=f"{len(blobs)} images")
    return to_ocr_response(
        result,
        filenames,
        model=_model_id(manager, "ocr"),
        backend=_backend_name(manager, "ocr"),
    )


# --------------------------------------------------------------------------
# GET /status, GET /healthz
# --------------------------------------------------------------------------


@router.get("/status", response_model=StatusResponse, summary="Models, VRAM, queue depth")
async def status(request: Request) -> StatusResponse:
    manager = get_manager(request)
    return StatusResponse(version=__version__, **manager.snapshot())


@router.get("/healthz", response_model=HealthResponse, summary="Liveness")
async def healthz() -> HealthResponse:
    return HealthResponse(version=__version__)
