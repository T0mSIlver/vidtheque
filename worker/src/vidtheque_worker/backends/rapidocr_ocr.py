"""RapidOCR (ONNX Runtime) on-screen-text backend.

ONNX means no CUDA requirement and a container that builds anywhere; on-screen
text in a 1080p keyframe is cheap enough on CPU that this rarely deserves GPU
time. Its VRAM estimate is therefore 0 by default — it does not participate in
the eviction game unless an operator overrides it.
"""

from __future__ import annotations

import logging
from typing import Any

from .base import BackendUnavailable, BaseBackend, OCRItem

log = logging.getLogger(__name__)


class RapidOCRBackend(BaseBackend):
    name = "rapidocr"
    task = "ocr"
    # CPU ONNX Runtime by default: costs no VRAM, so it never triggers eviction.
    default_vram_mb = 0

    def __init__(
        self,
        model_id: str = "rapidocr-default",
        *,
        min_confidence: float = 0.0,
        vram_estimate_mb: int | None = None,
    ) -> None:
        super().__init__(model_id, vram_estimate_mb=vram_estimate_mb)
        self.min_confidence = min_confidence
        self._engine: Any = None

    # -- lifecycle ---------------------------------------------------------
    def _load(self) -> None:
        try:
            from rapidocr_onnxruntime import RapidOCR
        except ImportError as exc:  # pragma: no cover - depends on gpu extra
            raise BackendUnavailable(
                "rapidocr-onnxruntime is not installed — install the worker's 'gpu' extra"
            ) from exc

        log.info("loading rapidocr engine (%s)", self.model_id)
        self._engine = RapidOCR()

    def _unload(self) -> None:
        self._engine = None

    # -- inference ---------------------------------------------------------
    def infer(self, images: list[bytes], **kwargs: Any) -> list[list[OCRItem]]:
        if self._engine is None:
            raise BackendUnavailable("rapidocr engine is not loaded")

        min_confidence = float(kwargs.get("min_confidence", self.min_confidence))
        out: list[list[OCRItem]] = []
        for blob in images:
            result, _elapsed = self._engine(_decode(blob))
            items: list[OCRItem] = []
            for entry in result or []:
                box, text, score = entry[0], entry[1], entry[2]
                confidence = float(score) if score is not None else None
                if confidence is not None and confidence < min_confidence:
                    continue
                items.append(
                    OCRItem(text=str(text), confidence=confidence, bbox=_bbox(box))
                )
            out.append(items)
        return out


def _decode(blob: bytes) -> Any:
    """RapidOCR takes bytes directly; decode via PIL only if that path fails."""
    try:
        import numpy as np
        from PIL import Image
        import io

        with Image.open(io.BytesIO(blob)) as im:
            return np.asarray(im.convert("RGB"))
    except Exception:  # pragma: no cover - PIL absent or exotic format
        return blob


def _bbox(box: Any) -> list[float] | None:
    """Collapse RapidOCR's 4-point polygon into an axis-aligned ``[x0,y0,x1,y1]``."""
    try:
        xs = [float(p[0]) for p in box]
        ys = [float(p[1]) for p in box]
    except Exception:  # pragma: no cover - defensive
        return None
    if not xs or not ys:
        return None
    return [min(xs), min(ys), max(xs), max(ys)]
