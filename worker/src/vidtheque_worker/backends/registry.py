"""Backend registry.

One table per task, mapping the env-selected name to a factory that turns
:class:`~vidtheque_worker.config.Settings` into a backend instance. Adding a
backend is a class plus one line here — the app, the lifecycle manager and the
HTTP layer never learn the name.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from ..config import Settings
from .base import Backend, EmbedBackend, ImageEmbedBackend, OCRBackend, STTBackend
from .bge_m3_embed import BGEM3Backend
from .qwen3_embed import Qwen3EmbedBackend
from .qwen3_vl_embed import Qwen3VLEmbedBackend
from .rapidocr_ocr import RapidOCRBackend
from .siglip2_image_embed import SigLIP2Backend
from .whisperx_stt import WhisperXBackend


class UnknownBackend(KeyError):
    def __init__(self, task: str, name: str, available: list[str]) -> None:
        super().__init__(
            f"unknown {task} backend {name!r}; available: {', '.join(sorted(available))}"
        )
        self.task = task
        self.name = name
        self.available = available


def _whisperx(settings: Settings) -> STTBackend:
    device = settings.resolved_device()
    return WhisperXBackend(
        settings.stt_model,
        device=device,
        compute_type=settings.resolved_compute_type(device),
        batch_size=settings.stt_batch_size,
        align=settings.stt_align,
        # A CPU fallback holds no VRAM, so it should not gate on it either.
        vram_estimate_mb=None if device == "cuda" else 0,
    )


def _qwen3_embedding(settings: Settings) -> EmbedBackend:
    device = settings.resolved_device()
    return Qwen3EmbedBackend(
        settings.embed_model,
        device=device,
        query_prompt=settings.embed_query_prompt,
        vram_estimate_mb=None if device == "cuda" else 0,
    )


def _bge_m3(settings: Settings) -> EmbedBackend:
    device = settings.resolved_device()
    return BGEM3Backend(
        settings.embed_model,
        device=device,
        vram_estimate_mb=None if device == "cuda" else 0,
    )


def _qwen3_vl_embedding(settings: Settings, *, task: str = "embed") -> Any:
    """The unified embedder, built from whichever task's model id applies.

    Registered under both ``embed`` and ``image_embed``. ``task`` picks which
    ``*_MODEL`` id names the checkpoint, so an operator who points only one of
    them at the unified model still gets a coherent worker — the *sharing*
    decision is :func:`build_backends`', and it needs the two ids to agree.
    """
    device = settings.resolved_device()
    model_id = settings.embed_model if task == "embed" else settings.image_embed_model
    return Qwen3VLEmbedBackend(
        model_id,
        device=device,
        batch_size=settings.embed_batch_size,
        image_batch_size=settings.image_embed_batch_size,
        truncate_dim=settings.embed_dim or None,
        query_prompt=settings.embed_query_prompt,
        frame_query_prompt=settings.frame_query_prompt,
        vram_estimate_mb=None if device == "cuda" else 0,
    )


def _siglip2(settings: Settings) -> ImageEmbedBackend:
    device = settings.resolved_device()
    return SigLIP2Backend(
        settings.image_embed_model,
        device=device,
        max_num_patches=settings.image_embed_max_patches,
        vram_estimate_mb=None if device == "cuda" else 0,
    )


def _rapidocr(settings: Settings) -> OCRBackend:
    # No device argument on purpose: RapidOCR is CPU-only here (see the module
    # docstring), so it holds no VRAM whatever DEVICE says.
    return RapidOCRBackend(
        settings.ocr_model, intra_op_num_threads=settings.ocr_threads
    )


STT_BACKENDS: Mapping[str, Callable[[Settings], STTBackend]] = {
    "whisperx": _whisperx,
}

EMBED_BACKENDS: Mapping[str, Callable[[Settings], EmbedBackend]] = {
    "qwen3-vl-embedding": _qwen3_vl_embedding,
    "qwen3-embedding": _qwen3_embedding,
    "bge-m3": _bge_m3,
}

IMAGE_EMBED_BACKENDS: Mapping[str, Callable[[Settings], ImageEmbedBackend]] = {
    "qwen3-vl-embedding": lambda s: _qwen3_vl_embedding(s, task="image_embed"),
    "siglip2": _siglip2,
}

OCR_BACKENDS: Mapping[str, Callable[[Settings], OCRBackend]] = {
    "rapidocr": _rapidocr,
}

_TABLES: Mapping[str, Mapping[str, Callable[[Settings], Backend]]] = {
    "stt": STT_BACKENDS,
    "embed": EMBED_BACKENDS,
    "image_embed": IMAGE_EMBED_BACKENDS,
    "ocr": OCR_BACKENDS,
}


def available(task: str) -> list[str]:
    return sorted(_TABLES[task])


def build_backend(task: str, name: str, settings: Settings) -> Backend:
    table = _TABLES[task]
    try:
        factory = table[name]
    except KeyError:
        raise UnknownBackend(task, name, list(table)) from None
    return factory(settings)


def shares_one_model(settings: Settings) -> bool:
    """Do ``embed`` and ``image_embed`` resolve to one checkpoint?

    Three things must line up, and all three are the operator's to get right:
    the same backend name, a backend that says it can serve both
    (``serves_tasks``), and the same model id. Anything else is two models and
    two slots, which is still a supported configuration — the hybrid fallback
    in ``research/multimodal-embedding-2026-08-09.md`` §7 is exactly that.

    The id comparison is casefolded and nothing else, deliberately: it is the
    same rule ``mcp/``'s drift checks use, so two genuinely different
    checkpoints are still caught.
    """
    if settings.embed_backend != settings.image_embed_backend:
        return False
    serves = getattr(_BACKEND_CLASSES.get(settings.embed_backend), "serves_tasks", ())
    if "embed" not in serves or "image_embed" not in serves:
        return False
    return settings.embed_model.lower() == settings.image_embed_model.lower()


_BACKEND_CLASSES: Mapping[str, type] = {
    "qwen3-vl-embedding": Qwen3VLEmbedBackend,
}
"""Backends that can serve more than one task, by the name the env selects.

Only consulted by :func:`shares_one_model` — the factories stay the registry's
one public shape, and a single-task backend simply is not in here."""


def build_backends(settings: Settings) -> dict[str, Backend]:
    """Instantiate the env-selected backend for every task. Cheap — no weights
    are touched until the lifecycle manager calls ``load()``.

    When ``embed`` and ``image_embed`` name one checkpoint the **same instance**
    is returned under both keys, and :class:`~vidtheque_worker.lifecycle.LifecycleManager`
    turns that into one shared Slot. Building two instances of the same model
    would load ~4.4 GB twice and charge admission control ~8.8 GB for it
    (``research/multimodal-embedding-2026-08-09.md`` §5.5); building one and
    aliasing it is the whole fix, and it is four lines here rather than a
    concept anywhere else.
    """
    embed = build_backend("embed", settings.embed_backend, settings)
    image_embed = (
        embed
        if shares_one_model(settings)
        else build_backend("image_embed", settings.image_embed_backend, settings)
    )
    return {
        "stt": build_backend("stt", settings.stt_backend, settings),
        "embed": embed,
        "image_embed": image_embed,
        "ocr": build_backend("ocr", settings.ocr_backend, settings),
    }
