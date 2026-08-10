"""Backend protocols.

One protocol per task. A backend is a plain synchronous object — the lifecycle
manager is the only thing that knows about asyncio, threads or the GPU, so an
implementation is free to be as blocking as the underlying library is.

Adding a backend is: write a class satisfying one of these protocols, add one
line to ``registry.py``. Nothing else in the worker learns its name.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

log = logging.getLogger(__name__)

TASKS = ("stt", "embed", "image_embed", "ocr")
"""``embed`` is the text leg; ``image_embed`` is the frame leg. They are separate
tasks rather than two backends for one task because indexing needs both at once —
one slot would force the pipeline to choose.

They are separate *tasks*, not necessarily separate *models*. A unified
multimodal embedder (``qwen3_vl_embed.py``) answers both, and then
:func:`~vidtheque_worker.backends.registry.build_backends` hands the lifecycle
manager the same instance under both names and the manager gives it **one
shared Slot**: one load, one VRAM charge, one eviction clock, one lease. Two
slots over one checkpoint would charge ~8.8 GB of admission for 4.4 GB of
weights and evict whisperX to make room for a model already on the card
(``research/multimodal-embedding-2026-08-09.md`` §5.5)."""


class BackendError(RuntimeError):
    """Backend failed in a way the caller can be told about.

    ``code`` is the ``error.type`` the HTTP layer puts on the wire. It lives on
    the class so a new error is one subclass rather than one subclass plus a
    handler plus a mapping table — and so ``mcp/`` can classify on a string
    that is declared in exactly one place.
    """

    code = "backend_error"


class BackendUnavailable(BackendError):
    """Optional dependency (or model) missing — surfaced as HTTP 503."""

    code = "backend_unavailable"


class BackendCrashed(BackendError):
    """Inference failed in a way that leaves the loaded model unusable.

    A CUDA OOM is the case this exists for: the allocation fails, but the
    model's CUDA context is poisoned and every later call to that instance
    returns ``invalid device ordinal`` (measured:
    ``research/gpu-validation-2026-08-08.md`` §5.1). The lifecycle manager
    unloads the slot before raising this, so the failure is transient — HTTP
    503 + ``Retry-After``, and the next request loads a clean model.
    """

    code = "backend_crashed"


class BackendPoisoned(BackendCrashed):
    """*The device* failed, not the input: unload the slot before anything else.

    This is the type a backend raises when it recognises a genuine device
    condition — a CUDA OOM, a lost context, a cuBLAS failure — and it is the
    only signal the lifecycle manager treats as "the loaded model is now
    rubbish". It carries ``BackendCrashed``'s wire shape deliberately (503 +
    ``Retry-After: 30``, ``error.type: backend_crashed``) so narrowing the
    *unload* rule changed no contract ``mcp/`` codes against.

    Everything the manager used to unload for and no longer does — a
    ``RuntimeError`` from a malformed media stream, a dtype mismatch, a shape
    error — costs a 5.8 s reload per hit and, because 503 is backpressure to
    the mcp client, replays the same doomed input until the 1800 s retry budget
    is gone. That is the failure this type exists to stop.
    """


class BackendInputError(BackendError):
    """The payload is the problem, and re-sending it will fail the same way.

    HTTP 400 with ``code`` as ``error.type``: outside ``mcp/``'s
    ``RETRYABLE_STATUS``, so the client fails the item instead of spending its
    backpressure budget on it. The model stays loaded — a bad JPEG says nothing
    about the weights.
    """

    code = "invalid_input"


class InvalidImageError(BackendInputError):
    """Bytes that no decoder would take: not an image, truncated, zero-pixel."""

    code = "invalid_image"

    def __init__(self, message: str, *, index: int | None = None) -> None:
        super().__init__(message)
        self.index = index
        """Position in the request's file list, when the batch knows it."""


class InvalidMediaError(BackendInputError):
    """Audio/video the decoder could not read — the STT-side twin of the above."""

    code = "invalid_media"


DEVICE_FAILURE_MARKERS = (
    "cuda",
    "out of memory",
    "cublas",
    "cudnn",
    "device-side assert",
    "no kernel image",
    "invalid device ordinal",
    "cudaerror",
    "nvrtc",
)
"""Substrings that mean the card, in the exceptions the shipped stacks raise."""

DEVICE_FAILURE_TYPES = frozenset(
    {"OutOfMemoryError", "AcceleratorError", "CudaError", "CUDAOutOfMemoryError"}
)
"""``torch.cuda.OutOfMemoryError`` and friends, matched by name so this module
imports without torch."""


def looks_like_device_failure(exc: BaseException) -> bool:
    """Defense in depth behind :class:`BackendPoisoned`.

    A backend that recognises its own device failures raises the type and this
    is never consulted. It exists because three of the four shipped stacks
    (ctranslate2, onnxruntime, transformers) report a dead device as an
    untyped exception whose *only* distinguishing feature is the message —
    onnxruntime's is not even a ``RuntimeError`` — and getting a real OOM
    wrong leaves a poisoned model answering every later job.

    Matched on the message rather than the type for that reason, and never
    consulted for a :class:`BackendInputError`: a filename with ``cuda`` in it
    must not cost a reload.
    """
    if isinstance(exc, BackendInputError):
        return False
    if isinstance(exc, BackendPoisoned):
        return True
    if type(exc).__name__ in DEVICE_FAILURE_TYPES:
        return True
    text = str(exc).lower()
    return any(marker in text for marker in DEVICE_FAILURE_MARKERS)


# --------------------------------------------------------------------------
# "did the weights actually load?"
# --------------------------------------------------------------------------


UNINITIALISED_WEIGHT_MARKERS = (
    "newly initialized",
    "were not initialized from the model checkpoint",
)
"""Substrings of the ``transformers`` warning that says a tensor is *random*.

The stable half of a warning that has read
``Some weights of {model} were not initialized from the model checkpoint at
{path} and are newly initialized: [...]`` since transformers 2.x.
"""


class _UninitialisedWeightWatcher(logging.Handler):
    """Collects that warning while a checkpoint is being loaded."""

    def __init__(self) -> None:
        super().__init__(level=logging.NOTSET)
        self.hits: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = record.getMessage()
        except Exception:  # pragma: no cover - a malformed record is not our bug
            return
        if any(marker in message for marker in UNINITIALISED_WEIGHT_MARKERS):
            self.hits.append(message)


@contextlib.contextmanager
def watch_for_uninitialised_weights(
    logger_name: str = "transformers",
) -> Iterator[list[str]]:
    """Yield a list that fills with ``transformers``' random-init warnings.

    **Why a backend cannot skip this.** ``from_pretrained`` does not fail when
    it cannot match the checkpoint's keys to the model's — it *warns*, fills the
    unmatched tensors with fresh random numbers, and returns a model that
    answers every request. An embedder built that way is a random projection:
    it produces stable, unit-norm, correctly-shaped 2048-d vectors that are
    mutually orthogonal to the ones the previous process produced, and nothing
    downstream can tell. That is not hypothetical — it is what
    ``Qwen/Qwen3-VL-Embedding-2B`` did on this stack for 22 hours and 176
    videos (``research/embedding-random-init-2026-08-10.md``): 625 of 625
    tensors newly initialised, twelve times, once per model load, with the
    warning sitting in ``worker.log`` the whole time.

    So the rule this enforces: **a published checkpoint that loads with any
    newly-initialised weight is a failed load**, and a failed load is a 503
    (:class:`BackendUnavailable`) rather than a corpus of noise.

    The level is forced to ``WARNING`` for the duration so an operator's
    ``TRANSFORMERS_VERBOSITY=error`` cannot turn the guard off by accident.
    """
    watcher = _UninitialisedWeightWatcher()
    logger = logging.getLogger(logger_name)
    previous_level = logger.level
    if not logger.isEnabledFor(logging.WARNING):
        logger.setLevel(logging.WARNING)
    logger.addHandler(watcher)
    try:
        yield watcher.hits
    finally:
        logger.removeHandler(watcher)
        logger.setLevel(previous_level)


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
    instruction: str | None = None
    """The instruction the model was asked to embed *under*, or None for a bare
    (document-side) encode.

    Reported back on the response and on ``/status`` because the corpus records
    what indexing assumed — ``config['text_embed.query_prefix']`` and
    ``config['frame_embed.query_prefix']`` — and that record drifted out of
    sync with behaviour once already, silently
    (``research/pipeline-perf-2026-08-09.md`` §5). An instruction-aware model
    with two instructions makes it more load-bearing, not less, so the worker
    says what it applied instead of leaving it to be inferred."""


@dataclass(slots=True)
class OCRItem:
    text: str
    confidence: float | None = None
    bbox: list[float] | None = None
    """``[x0, y0, x1, y1]`` in pixels, axis-aligned around the detected polygon."""


@dataclass(slots=True)
class OCRPage:
    """What one input image produced — text, or the reason there is none.

    OCR is the one batch endpoint that can answer *per file*, and it should:
    ``mcp/`` requires exactly one indexed result per input, so failing the
    whole request over one unreadable frame loses the OCR stage for the other
    63 images in the batch, non-retryably. An entry with ``error`` set keeps
    the cardinality contract and still says what happened.

    A device failure is never a page error — it propagates, unloads the slot
    and fails the request, because the next image would fail the same way.
    """

    items: list[OCRItem] = field(default_factory=list)
    error: str | None = None
    code: str | None = None
    """``error.type``-style tag for the failure, mirroring the HTTP errors."""


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
    def infer(
        self, texts: list[str], *, input_type: str = "document", **kwargs: Any
    ) -> Embeddings:
        """``input_type`` is ``document`` or ``query``.

        Instruction-tuned embedders (Qwen3-Embedding) prefix queries and not
        documents; getting that asymmetry wrong degrades recall silently. A
        symmetric model (bge-m3) accepts the argument and ignores it.
        """


@runtime_checkable
class ImageEmbedBackend(Backend, Protocol):
    """Images into the frame vector space.

    Encoded JPEG/PNG bytes in, one L2-normalised vector per image out, in the
    order they were given.

    Whether that space is *different* from :class:`EmbedBackend`'s depends on
    the configuration and on nothing else: with two checkpoints (SigLIP 2 +
    Qwen3-Embedding) they are two spaces that must never be compared, and with
    one unified checkpoint they are the same space served by the same shared
    slot. Neither the HTTP paths nor `mcp/`'s two legs change between those two
    worlds — see ``qwen3_vl_embed.py``.
    """

    def infer(self, images: list[bytes], **kwargs: Any) -> Embeddings: ...

    def embed_text(self, texts: list[str], **kwargs: Any) -> Embeddings:
        """Text queries into the *same* space :meth:`infer` writes to.

        Always the same instance and the same lifecycle slot — never a second
        load. On a dual encoder this is the checkpoint's text tower, with a
        short trained context (64 tokens for SigLIP 2); on a unified embedder
        it is the same weights under the frame-retrieval instruction, at the
        model's full context. It is how a query reaches the frame index.
        """


@runtime_checkable
class OCRBackend(Backend, Protocol):
    def infer(self, images: list[bytes], **kwargs: Any) -> list[OCRPage]: ...
    """One page per image, in order, always — an unreadable image comes back as
    a page carrying its error, never as a missing entry or a raised exception."""


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
    def _empty_cuda_cache() -> bool:
        """Hand the allocator's cached blocks back. ``False`` if that failed.

        Swallowed on purpose — an unload that raises is worse than an unload
        that frees less than it hoped — but *logged*, because the silent
        version had a nasty tail: if this ever stops working, every unload
        becomes a no-op for VRAM, admission control sees no room after
        evicting, and the worker answers 503 forever while ``/healthz`` says
        200. That is a line in the log or an invisible wedge.
        """
        try:
            import gc

            import torch
        except Exception as exc:  # no torch: nothing cached, nothing to free
            log.debug("no torch to empty a CUDA cache with: %s", exc)
            return False
        try:  # pragma: no cover - requires a torch install
            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
            return True
        except Exception:  # pragma: no cover - a broken/absent driver
            log.error("torch.cuda.empty_cache() failed; VRAM may not come back",
                      exc_info=True)
            return False

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        state = "loaded" if self._loaded else "unloaded"
        return f"<{type(self).__name__} {self.task}:{self.name} {self.model_id} {state}>"
