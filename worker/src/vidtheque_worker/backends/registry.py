"""Backend registry.

One table per task, mapping the env-selected name to a factory that turns
:class:`~vidtheque_worker.config.Settings` into a backend instance. Adding a
backend is a class plus one line here — the app, the lifecycle manager and the
HTTP layer never learn the name.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from ..config import Settings
from .base import Backend, EmbedBackend, ImageEmbedBackend, OCRBackend, STTBackend
from .bge_m3_embed import BGEM3Backend
from .qwen3_embed import Qwen3EmbedBackend
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
    "qwen3-embedding": _qwen3_embedding,
    "bge-m3": _bge_m3,
}

IMAGE_EMBED_BACKENDS: Mapping[str, Callable[[Settings], ImageEmbedBackend]] = {
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


def build_backends(settings: Settings) -> dict[str, Backend]:
    """Instantiate the env-selected backend for every task. Cheap — no weights
    are touched until the lifecycle manager calls ``load()``."""
    return {
        "stt": build_backend("stt", settings.stt_backend, settings),
        "embed": build_backend("embed", settings.embed_backend, settings),
        "image_embed": build_backend(
            "image_embed", settings.image_embed_backend, settings
        ),
        "ocr": build_backend("ocr", settings.ocr_backend, settings),
    }
