"""SigLIP 2 NaFlex frame embeddings via transformers.

``google/siglip2-so400m-patch16-naflex``: 1152 dims, Apache-2.0. NaFlex is the
variant that preserves aspect ratio instead of square-resizing, which is the
whole argument for it here — a 16:9 screencast frame squashed to 384×384 mangles
exactly the glyphs a terminal or slide frame is worth indexing for. The paper
reports NaFlex ahead of the fixed-resolution checkpoints on Screen2Words,
SciCap, TextCaps and HierText for that reason.

**transformers is the only shipping path.** open_clip's NaFlex configs landed on
``main`` in May 2026 and are in no release, so ``open_clip_torch`` gets you
SigLIP 2 FixRes and not this checkpoint. NaFlex also needs ``Siglip2Model``
(``AutoModel`` resolves it); the older ``SiglipModel`` cannot load it.

``max_num_patches`` is the resolution knob and it is per-call: the model was
trained on a budget sampled from ``PATCH_BUDGETS``, so a talking head is worth
256 and a slide RapidOCR found sixty lines in is worth 1024 — same checkpoint,
no re-model. The processor resizes aspect-preserving to a multiple of the patch
size such that the patch count fits, then pads in the patch dimension.

Vectors come back L2-normalised, in the order the images were given: SigLIP
scores by sigmoid over a scaled cosine, and ranking only needs the cosine.
"""

from __future__ import annotations

import io
import logging
from typing import Any

from .base import BackendUnavailable, BaseBackend, Embeddings

log = logging.getLogger(__name__)

DEFAULT_MODEL = "google/siglip2-so400m-patch16-naflex"

PATCH_BUDGETS = (128, 256, 576, 784, 1024)
"""The budgets NaFlex was trained on. Others load, but are off-distribution."""


class SigLIP2Backend(BaseBackend):
    name = "siglip2"
    task = "image_embed"
    # 1.136B params in bfloat16 = 2.3 GB of weights; activations at batch 32-64
    # take it to roughly 5 GB. Co-resident with the text embedder on a 24 GB
    # card with room to spare.
    default_vram_mb = 5000

    def __init__(
        self,
        model_id: str = DEFAULT_MODEL,
        *,
        device: str = "cpu",
        max_num_patches: int = 256,
        batch_size: int = 32,
        vram_estimate_mb: int | None = None,
    ) -> None:
        super().__init__(model_id, vram_estimate_mb=vram_estimate_mb)
        self.device = device
        self.max_num_patches = max_num_patches
        self.batch_size = batch_size
        self._model: Any = None
        self._processor: Any = None
        self._torch: Any = None
        self._dims: int | None = None

    # -- lifecycle ---------------------------------------------------------
    def _load(self) -> None:
        try:
            import torch
            from transformers import AutoModel, AutoProcessor
        except ImportError as exc:  # pragma: no cover - depends on gpu extra
            raise BackendUnavailable(
                "transformers is not installed — install the worker's 'gpu' extra"
            ) from exc

        log.info("loading frame embedder model=%s device=%s", self.model_id, self.device)
        dtype = torch.bfloat16 if self.device == "cuda" else torch.float32
        model = AutoModel.from_pretrained(
            self.model_id, dtype=dtype, attn_implementation="sdpa"
        )
        self._torch = torch
        self._model = model.eval().to(self.device)
        self._processor = AutoProcessor.from_pretrained(self.model_id)
        self._dims = _projection_dims(self._model)

    def _unload(self) -> None:
        self._model = None
        self._processor = None
        self._torch = None
        self._empty_cuda_cache()

    # -- inference ---------------------------------------------------------
    def infer(
        self,
        images: list[bytes],
        *,
        max_num_patches: int | None = None,
        **kwargs: Any,
    ) -> Embeddings:
        if self._model is None or self._processor is None:
            raise BackendUnavailable("frame embedding model is not loaded")

        budget = int(max_num_patches or self.max_num_patches)
        if budget not in PATCH_BUDGETS:
            log.warning(
                "max_num_patches=%d is outside the trained set %s", budget, PATCH_BUDGETS
            )
        batch_size = int(kwargs.get("batch_size", self.batch_size))
        frames = [_open_rgb(blob) for blob in images]

        vectors: list[list[float]] = []
        for start in range(0, len(frames), max(1, batch_size)):
            vectors.extend(self._encode(frames[start : start + batch_size], budget))

        dims = self._dims or (len(vectors[0]) if vectors else 0)
        return Embeddings(vectors=vectors, dims=dims, model=self.model_id)

    def _encode(self, frames: list[Any], budget: int) -> list[list[float]]:
        torch = self._torch
        # NaFlex inputs are (B, N, patch**2 * 3) pixel_values plus
        # pixel_attention_mask and spatial_shapes — not the (B, C, H, W) the
        # stale model_doc page shows.
        inputs = self._processor(
            images=frames, max_num_patches=budget, return_tensors="pt"
        ).to(self._model.device)
        with torch.inference_mode():
            features = self._model.get_image_features(**inputs)
            features = features / features.norm(p=2, dim=-1, keepdim=True)
        return [[float(x) for x in row] for row in features.float().cpu()]

    @property
    def dims(self) -> int | None:
        return self._dims


def _open_rgb(blob: bytes) -> Any:
    try:
        from PIL import Image
    except ImportError as exc:  # pragma: no cover - depends on gpu extra
        raise BackendUnavailable(
            "pillow is not installed — install the worker's 'gpu' extra"
        ) from exc

    with Image.open(io.BytesIO(blob)) as image:
        return image.convert("RGB")


def _projection_dims(model: Any) -> int | None:
    config = getattr(model, "config", None)
    for holder, attr in (
        (config, "projection_dim"),
        (getattr(config, "vision_config", None), "projection_size"),
        (getattr(config, "vision_config", None), "hidden_size"),
    ):
        value = getattr(holder, attr, None)
        if isinstance(value, int) and value > 0:
            return value
    return None  # pragma: no cover - every shipped checkpoint declares one
