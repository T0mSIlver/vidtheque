"""Wire shapes.

The transcription and embedding responses copy OpenAI's field names on purpose:
an operator without a GPU should be able to delete the worker service and point
``WORKER_URL`` at a hosted endpoint without the ``mcp`` service noticing. ``/v1/ocr``
has no OpenAI equivalent, so it follows the same conventions rather than inventing
new ones.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from .backends.base import Embeddings, OCRPage, Transcription

# --------------------------------------------------------------------------
# transcriptions
# --------------------------------------------------------------------------


class WordOut(BaseModel):
    word: str
    start: float | None = None
    end: float | None = None
    score: float | None = None


class SegmentOut(BaseModel):
    id: int
    seek: int = 0
    start: float
    end: float
    text: str
    words: list[WordOut] = Field(default_factory=list)


class TranscriptionOut(BaseModel):
    text: str


class VerboseTranscriptionOut(BaseModel):
    task: Literal["transcribe"] = "transcribe"
    language: str | None = None
    duration: float | None = None
    text: str
    segments: list[SegmentOut] = Field(default_factory=list)
    model: str | None = None
    backend: str | None = None


def to_verbose(
    result: Transcription, *, model: str | None = None, backend: str | None = None
) -> VerboseTranscriptionOut:
    return VerboseTranscriptionOut(
        language=result.language,
        duration=result.duration,
        text=result.text,
        segments=[
            SegmentOut(
                id=seg.id,
                start=seg.start,
                end=seg.end,
                text=seg.text,
                words=[
                    WordOut(word=w.word, start=w.start, end=w.end, score=w.score)
                    for w in seg.words
                ],
            )
            for seg in result.segments
        ],
        model=model,
        backend=backend,
    )


# --------------------------------------------------------------------------
# embeddings
# --------------------------------------------------------------------------


class _TextInput(BaseModel):
    """OpenAI's ``input`` polymorphism, shared by the two text-in endpoints."""

    input: str | list[str]
    model: str | None = None
    encoding_format: Literal["float"] = "float"

    def texts(self) -> list[str]:
        return [self.input] if isinstance(self.input, str) else list(self.input)


class EmbeddingsRequest(_TextInput):
    user: str | None = None
    input_type: Literal["document", "query"] = "document"
    """Non-OpenAI extra, and optional: an instruction-tuned embedder prefixes
    queries and not documents. Defaults to ``document`` so a caller that never
    sends it indexes correctly, and a symmetric model ignores it entirely."""


class FrameQueryRequest(_TextInput):
    """A query for the frame vector space, run through the frame model itself.

    No ``input_type``: this endpoint is the query side by construction — the
    document side is images, and it has its own endpoint. The frame model
    applies its own frame-retrieval instruction, which the response echoes.

    How long a query may be depends on the configured frame backend, and the
    difference is silent either way. SigLIP 2's text tower is trained to **64
    tokens** and drops the rest without an error (it logs a warning). The
    unified `Qwen3-VL-Embedding` backend has the model's full 32K context, so
    a long descriptive frame query is embedded whole — which is the shape the
    client model actually writes.
    """


class EmbeddingItem(BaseModel):
    object: Literal["embedding"] = "embedding"
    index: int
    embedding: list[float]


class Usage(BaseModel):
    prompt_tokens: int = 0
    total_tokens: int = 0


class EmbeddingsResponse(BaseModel):
    object: Literal["list"] = "list"
    data: list[EmbeddingItem]
    model: str
    usage: Usage = Field(default_factory=Usage)
    dimensions: int
    """Non-OpenAI extra: the index pins this alongside the model name."""

    instruction: str | None = None
    """Non-OpenAI extra: the instruction these vectors were embedded under, or
    null for a bare document-side encode.

    An instruction-aware embedder gives the same text different vectors under
    different instructions, so "which model" is no longer the whole answer to
    "what space is this". The corpus records the assumption
    (`config['text_embed.query_prefix']`, `config['frame_embed.query_prefix']`);
    this is the behaviour, so the two can be compared instead of trusted."""


def to_embeddings_response(result: Embeddings) -> EmbeddingsResponse:
    # Whitespace tokens are a stand-in: the backends do not expose a tokeniser
    # count and no billing depends on it. Present so OpenAI clients parse.
    return EmbeddingsResponse(
        data=[
            EmbeddingItem(index=i, embedding=vec) for i, vec in enumerate(result.vectors)
        ],
        model=result.model,
        dimensions=result.dims,
        instruction=result.instruction,
    )


# --------------------------------------------------------------------------
# ocr
# --------------------------------------------------------------------------


class OCRItemOut(BaseModel):
    text: str
    confidence: float | None = None
    bbox: list[float] | None = Field(
        default=None, description="[x0, y0, x1, y1] in source pixels"
    )


class OCRErrorOut(BaseModel):
    """Why one image produced no text. Absent means it was read successfully."""

    type: str = Field(description="Stable code, e.g. invalid_image")
    message: str


class OCRImageResult(BaseModel):
    index: int
    filename: str | None = None
    items: list[OCRItemOut]
    """Required, and empty rather than absent when an image carried no text or
    could not be read — the worker has always emitted it, and a schema that
    permits its absence invites a reader that treats missing as `[]` for real
    text it simply failed to parse."""
    error: OCRErrorOut | None = Field(
        default=None,
        description=(
            "Set when this image could not be read. `items` is then empty, and "
            "the entry still counts as this image's one result — a per-file "
            "failure never shortens the response."
        ),
    )


class OCRResponse(BaseModel):
    object: Literal["list"] = "list"
    model: str | None = None
    backend: str | None = None
    data: list[OCRImageResult]


def to_ocr_response(
    pages: list[OCRPage],
    filenames: list[str | None],
    *,
    model: str | None = None,
    backend: str | None = None,
) -> OCRResponse:
    return OCRResponse(
        model=model,
        backend=backend,
        data=[
            OCRImageResult(
                index=i,
                filename=filenames[i] if i < len(filenames) else None,
                items=[
                    OCRItemOut(text=it.text, confidence=it.confidence, bbox=it.bbox)
                    for it in page.items
                ],
                error=(
                    None
                    if page.error is None
                    else OCRErrorOut(
                        type=page.code or "invalid_image", message=page.error
                    )
                ),
            )
            for i, page in enumerate(pages)
        ],
    )


# --------------------------------------------------------------------------
# status
# --------------------------------------------------------------------------


class StatusResponse(BaseModel):
    version: str
    backends: list[dict[str, Any]]
    vram: dict[str, Any]
    queue: dict[str, Any]
    lease: dict[str, Any]
    idle_unload_seconds: float


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    version: str


# --------------------------------------------------------------------------
# errors
# --------------------------------------------------------------------------


class ErrorBody(BaseModel):
    message: str
    type: str = Field(
        description=(
            "Stable code for the failure. `invalid_input`/`invalid_image`/"
            "`invalid_media` (400) mean the payload is the problem and "
            "re-sending it will fail identically; `backend_crashed`, "
            "`insufficient_vram`, `gpu_lease_failed`, `backend_unavailable`, "
            "`worker_shutting_down` and `worker_not_ready` (503, with "
            "`Retry-After`) all mean not-now rather than never."
        )
    )


class ErrorResponse(BaseModel):
    """The envelope every handled failure uses.

    Documented because it is the shape a client branches on, and because it is
    *not* FastAPI's `{"detail": ...}` — a caller that reads only `detail` gets
    nothing useful out of a 503.
    """

    error: ErrorBody
