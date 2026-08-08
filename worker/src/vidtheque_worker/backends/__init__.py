"""Backend protocols, shipped implementations, and the selection registry."""

from .base import (
    TASKS,
    Backend,
    BackendError,
    BackendUnavailable,
    Embeddings,
    EmbedBackend,
    OCRBackend,
    OCRItem,
    STTBackend,
    Segment,
    Transcription,
    Word,
)
from .registry import (
    EMBED_BACKENDS,
    OCR_BACKENDS,
    STT_BACKENDS,
    UnknownBackend,
    available,
    build_backend,
    build_backends,
)

__all__ = [
    "TASKS",
    "Backend",
    "BackendError",
    "BackendUnavailable",
    "EmbedBackend",
    "Embeddings",
    "OCRBackend",
    "OCRItem",
    "STTBackend",
    "Segment",
    "Transcription",
    "Word",
    "EMBED_BACKENDS",
    "OCR_BACKENDS",
    "STT_BACKENDS",
    "UnknownBackend",
    "available",
    "build_backend",
    "build_backends",
]
