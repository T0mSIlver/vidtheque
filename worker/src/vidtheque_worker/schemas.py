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

from .backends.base import Embeddings, OCRItem, Transcription

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


class EmbeddingsRequest(BaseModel):
    input: str | list[str]
    model: str | None = None
    encoding_format: Literal["float"] = "float"
    user: str | None = None

    def texts(self) -> list[str]:
        return [self.input] if isinstance(self.input, str) else list(self.input)


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


def to_embeddings_response(result: Embeddings) -> EmbeddingsResponse:
    # Whitespace tokens are a stand-in: the backends do not expose a tokeniser
    # count and no billing depends on it. Present so OpenAI clients parse.
    return EmbeddingsResponse(
        data=[
            EmbeddingItem(index=i, embedding=vec) for i, vec in enumerate(result.vectors)
        ],
        model=result.model,
        dimensions=result.dims,
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


class OCRImageResult(BaseModel):
    index: int
    filename: str | None = None
    items: list[OCRItemOut] = Field(default_factory=list)


class OCRResponse(BaseModel):
    object: Literal["list"] = "list"
    model: str | None = None
    backend: str | None = None
    data: list[OCRImageResult]


def to_ocr_response(
    per_image: list[list[OCRItem]],
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
                    for it in items
                ],
            )
            for i, items in enumerate(per_image)
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
