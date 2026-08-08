"""RapidOCR (ONNX Runtime) on-screen-text backend.

The package is ``rapidocr`` 3.9.2, not the frozen ``rapidocr-onnxruntime``: the
three backend-split distributions stopped at 1.4.4 in January 2025 and the
merged one is where PP-OCRv6 lives. It ships **no inference engine** — the
worker's ``gpu`` extra pins ``onnxruntime`` itself — but it does ship the
default det/cls/rec ONNX models inside the wheel, so a default-config engine
makes zero network calls. That is the property that makes an offline container
possible, and it is the opposite of what most write-ups still claim.

CPU on purpose. PP-OCR det/rec are small dynamic-shape convnets: per frame you
pay dozens of tiny kernel launches and two host↔device copies for a few ms of
compute, and the maintainer's own position is that the CUDA path was never
worth it. So this backend's VRAM estimate is 0 and it never contends for the
GPU lease — only whisperX and the two embedders do.

Three defaults below are deliberate and each is worth real time:

* ``Det.limit_type="max"`` — the shipped default is ``min``, which only ever
  *upscales*, so a 1920×1080 keyframe reaches the detector at full size. Capping
  the long side at 1280 roughly halves detector cost with no recall loss on
  screen text.
* ``Rec.rec_batch_num=16`` — the default 6 turns a 60-line slide into ten
  sequential recognition batches.
* explicit ``intra_op_num_threads`` — ONNX Runtime reads the *host* core count,
  not the cgroup limit, and oversubscribes badly in a container.

``use_cls`` is off because screen text is never 180°-rotated; note that the
classifier model is constructed regardless, so an offline image still has to
carry all three files.
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
        min_confidence: float = 0.5,
        use_cls: bool = False,
        det_side_len: int = 1280,
        rec_batch_num: int = 16,
        intra_op_num_threads: int = 4,
        vram_estimate_mb: int | None = None,
    ) -> None:
        super().__init__(model_id, vram_estimate_mb=vram_estimate_mb)
        self.min_confidence = min_confidence
        self.use_cls = use_cls
        self.det_side_len = det_side_len
        self.rec_batch_num = rec_batch_num
        self.intra_op_num_threads = intra_op_num_threads
        self._engine: Any = None

    # -- lifecycle ---------------------------------------------------------
    def _load(self) -> None:
        try:
            from rapidocr import RapidOCR
        except ImportError as exc:  # pragma: no cover - depends on gpu extra
            raise BackendUnavailable(
                "rapidocr is not installed — install the worker's 'gpu' extra"
            ) from exc

        log.info("loading rapidocr engine (%s)", self.model_id)
        # ``params`` is a flat dotted-key dict merged over the packaged config.
        self._engine = RapidOCR(
            params={
                "Global.log_level": "warning",
                "Global.text_score": self.min_confidence,
                "Global.use_cls": self.use_cls,
                "Det.limit_type": "max",
                "Det.limit_side_len": self.det_side_len,
                "Rec.rec_batch_num": self.rec_batch_num,
                "EngineConfig.onnxruntime.intra_op_num_threads": self.intra_op_num_threads,
            }
        )

    def _unload(self) -> None:
        self._engine = None

    # -- inference ---------------------------------------------------------
    def infer(self, images: list[bytes], **kwargs: Any) -> list[list[OCRItem]]:
        if self._engine is None:
            raise BackendUnavailable("rapidocr engine is not loaded")

        min_confidence = float(kwargs.get("min_confidence", self.min_confidence))
        out: list[list[OCRItem]] = []
        for blob in images:
            # RapidOCR decodes bytes itself (path/bytes/ndarray/URL all accepted).
            result = self._engine(blob, text_score=min_confidence)
            out.append(_items(result, min_confidence))
        return out


def _items(result: Any, min_confidence: float) -> list[OCRItem]:
    """Unpack a ``RapidOCROutput``.

    ``boxes``/``txts``/``scores`` are index-aligned — lines whose recognised text
    came back empty are dropped from all three together — and are ``None`` rather
    than empty when nothing was found. Boxes are already mapped back to original
    image coordinates, so no un-resizing is needed here.
    """
    texts = getattr(result, "txts", None)
    if not texts:
        return []
    boxes = getattr(result, "boxes", None)
    scores = getattr(result, "scores", None)
    count = len(texts)
    if boxes is None:
        boxes = [None] * count
    if scores is None:
        scores = [None] * count

    items: list[OCRItem] = []
    for box, text, score in zip(boxes, texts, scores):
        confidence = None if score is None else float(score)
        if confidence is not None and confidence < min_confidence:
            continue
        items.append(OCRItem(text=str(text), confidence=confidence, bbox=_bbox(box)))
    return items


def _bbox(box: Any) -> list[float] | None:
    """Collapse RapidOCR's 4-point polygon into an axis-aligned ``[x0,y0,x1,y1]``."""
    if box is None:
        return None
    try:
        xs = [float(p[0]) for p in box]
        ys = [float(p[1]) for p in box]
    except Exception:  # pragma: no cover - defensive
        return None
    if not xs or not ys:
        return None
    return [min(xs), min(ys), max(xs), max(ys)]
