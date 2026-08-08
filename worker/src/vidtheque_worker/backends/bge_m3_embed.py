"""bge-m3 text embeddings via sentence-transformers.

Vectors are L2-normalised at the source so the index can use a plain dot
product, and ``dims`` is reported back rather than hardcoded — the same model
value has to be pinned at index time and query time, and the caller is the one
holding that config.
"""

from __future__ import annotations

import logging
from typing import Any

from .base import BackendUnavailable, BaseBackend, Embeddings

log = logging.getLogger(__name__)


class BGEM3Backend(BaseBackend):
    name = "bge-m3"
    task = "embed"
    # ~2.2 GB of weights in fp16 plus activation headroom for a modest batch.
    default_vram_mb = 2600

    def __init__(
        self,
        model_id: str = "BAAI/bge-m3",
        *,
        device: str = "cpu",
        batch_size: int = 16,
        max_seq_length: int | None = None,
        vram_estimate_mb: int | None = None,
    ) -> None:
        super().__init__(model_id, vram_estimate_mb=vram_estimate_mb)
        self.device = device
        self.batch_size = batch_size
        self.max_seq_length = max_seq_length
        self._model: Any = None
        self._dims: int | None = None

    # -- lifecycle ---------------------------------------------------------
    def _load(self) -> None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:  # pragma: no cover - depends on gpu extra
            raise BackendUnavailable(
                "sentence-transformers is not installed — install the worker's 'gpu' extra"
            ) from exc

        log.info("loading embedding model=%s device=%s", self.model_id, self.device)
        self._model = SentenceTransformer(self.model_id, device=self.device)
        if self.max_seq_length:
            self._model.max_seq_length = self.max_seq_length
        try:
            self._dims = int(self._model.get_sentence_embedding_dimension())
        except Exception:  # pragma: no cover - defensive
            self._dims = None

    def _unload(self) -> None:
        self._model = None
        self._empty_cuda_cache()

    # -- inference ---------------------------------------------------------
    def infer(self, texts: list[str], **kwargs: Any) -> Embeddings:
        if self._model is None:
            raise BackendUnavailable("embedding model is not loaded")

        raw = self._model.encode(
            texts,
            batch_size=kwargs.get("batch_size", self.batch_size),
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        vectors = [[float(x) for x in row] for row in raw]
        dims = self._dims or (len(vectors[0]) if vectors else 0)
        return Embeddings(vectors=vectors, dims=dims, model=self.model_id)

    @property
    def dims(self) -> int | None:
        return self._dims
