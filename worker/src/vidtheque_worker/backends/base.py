"""Backend protocols.

One protocol per task. A backend is a plain synchronous object — the lifecycle
manager is the only thing that knows about asyncio, threads or the GPU, so an
implementation is free to be as blocking as the underlying library is.

Adding a backend is: write a class satisfying one of these protocols, add one
line to ``registry.py``. Nothing else in the worker learns its name.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

TASKS = ("stt", "embed", "ocr")


class BackendError(RuntimeError):
    """Backend failed in a way the caller can be told about."""


class BackendUnavailable(BackendError):
    """Optional dependency (or model) missing — surfaced as HTTP 503."""


# --------------------------------------------------------------------------
# result shapes
# --------------------------------------------------------------------------


@dataclass(slots=True)
class Word:
    word: str
    start: float | None = None
    end: float | None = None
    score: float | None = None


@dataclass(slots=True)
class Segment:
    id: int
    start: float
    end: float
    text: str
    words: list[Word] = field(default_factory=list)


@dataclass(slots=True)
class Transcription:
    text: str
    language: str | None = None
    duration: float | None = None
    segments: list[Segment] = field(default_factory=list)


@dataclass(slots=True)
class Embeddings:
    vectors: list[list[float]]
    dims: int
    model: str


@dataclass(slots=True)
class OCRItem:
    text: str
    confidence: float | None = None
    bbox: list[float] | None = None
    """``[x0, y0, x1, y1]`` in pixels, axis-aligned around the detected polygon."""


# --------------------------------------------------------------------------
# protocols
# --------------------------------------------------------------------------


@runtime_checkable
class Backend(Protocol):
    """Common surface every backend has, whatever its task."""

    name: str
    task: str
    model_id: str

    @property
    def vram_estimate_mb(self) -> int:
        """Rough VRAM this backend needs once loaded. Used for admission control."""

    @property
    def loaded(self) -> bool: ...

    def load(self) -> None:
        """Pull weights into memory. Idempotent."""

    def unload(self) -> None:
        """Release weights and (for CUDA backends) empty the allocator cache. Idempotent."""


@runtime_checkable
class STTBackend(Backend, Protocol):
    def infer(
        self,
        audio_path: str,
        *,
        language: str | None = None,
        align: bool = True,
        **kwargs: Any,
    ) -> Transcription: ...


@runtime_checkable
class EmbedBackend(Backend, Protocol):
    def infer(self, texts: list[str], **kwargs: Any) -> Embeddings: ...


@runtime_checkable
class OCRBackend(Backend, Protocol):
    def infer(self, images: list[bytes], **kwargs: Any) -> list[list[OCRItem]]: ...


# --------------------------------------------------------------------------
# convenience base
# --------------------------------------------------------------------------


class BaseBackend:
    """Bookkeeping shared by the shipped backends. Implementing the protocol
    directly is equally fine — this is a convenience, not a requirement."""

    name: str = "base"
    task: str = "unknown"
    default_vram_mb: int = 0

    def __init__(self, model_id: str, *, vram_estimate_mb: int | None = None) -> None:
        self.model_id = model_id
        self._vram_estimate_mb = (
            vram_estimate_mb if vram_estimate_mb is not None else self.default_vram_mb
        )
        self._loaded = False

    @property
    def vram_estimate_mb(self) -> int:
        return self._vram_estimate_mb

    @property
    def loaded(self) -> bool:
        return self._loaded

    def load(self) -> None:
        if self._loaded:
            return
        self._load()
        self._loaded = True

    def unload(self) -> None:
        if not self._loaded:
            return
        try:
            self._unload()
        finally:
            self._loaded = False

    # -- subclass hooks ----------------------------------------------------
    def _load(self) -> None:  # pragma: no cover - overridden
        raise NotImplementedError

    def _unload(self) -> None:  # pragma: no cover - overridden
        raise NotImplementedError

    # -- helpers -----------------------------------------------------------
    @staticmethod
    def _empty_cuda_cache() -> None:
        try:  # pragma: no cover - requires torch
            import gc

            import torch

            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:
            pass

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        state = "loaded" if self._loaded else "unloaded"
        return f"<{type(self).__name__} {self.task}:{self.name} {self.model_id} {state}>"
