"""Qwen3-Embedding text embeddings via sentence-transformers.

The default text embedder (``docs/design/DECISIONS.md`` §4): Apache-2.0,
0.6B parameters, 1024 dims, 32k context — +4.7 MMTEB points over bge-m3 at the
same weight class, and long enough to swallow a whole transcript stage without
chunking around a 512-token window.

Two things this model needs that bge-m3 does not, both easy to get wrong:

* **Left padding.** The pooling is last-token, so the tokenizer has to pad on
  the left or every vector in a batch pools a pad token.
* **An instruction prefix on queries and not on documents.** The prefix text is
  a property of the *index*, not of the calling code, so it lives here (or in
  ``EMBED_QUERY_PROMPT``) rather than being pasted in by whoever is searching.
  Index and query with different prefixes and recall degrades quietly.

Vectors are L2-normalised at the source so the index can use a plain dot
product, and ``dims`` is reported back rather than hardcoded.
"""

from __future__ import annotations

import logging
from typing import Any

from .base import BackendUnavailable, BaseBackend, Embeddings

log = logging.getLogger(__name__)

DEFAULT_MODEL = "Qwen/Qwen3-Embedding-0.6B"


class Qwen3EmbedBackend(BaseBackend):
    name = "qwen3-embedding"
    task = "embed"
    # 0.6B params in float16 ≈ 1.2 GB of weights, plus activation headroom for a
    # modest batch of long transcript chunks.
    default_vram_mb = 1800

    #: Prompt registered by the model's own sentence-transformers config.
    query_prompt_name = "query"

    def __init__(
        self,
        model_id: str = DEFAULT_MODEL,
        *,
        device: str = "cpu",
        batch_size: int = 16,
        max_seq_length: int | None = None,
        query_prompt: str | None = None,
        vram_estimate_mb: int | None = None,
    ) -> None:
        super().__init__(model_id, vram_estimate_mb=vram_estimate_mb)
        self.device = device
        self.batch_size = batch_size
        self.max_seq_length = max_seq_length
        self.query_prompt = query_prompt
        """Overrides the checkpoint's own ``query`` prompt when set."""
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
        model_kwargs = {"dtype": "float16"} if self.device == "cuda" else None
        self._model = SentenceTransformer(
            self.model_id,
            device=self.device,
            model_kwargs=model_kwargs,
            # Last-token pooling: padding on the right would pool a pad token.
            tokenizer_kwargs={"padding_side": "left"},
        )
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
    def infer(
        self, texts: list[str], *, input_type: str = "document", **kwargs: Any
    ) -> Embeddings:
        if self._model is None:
            raise BackendUnavailable("embedding model is not loaded")

        encode_kwargs: dict[str, Any] = {}
        if input_type == "query":
            if self.query_prompt is not None:
                encode_kwargs["prompt"] = self.query_prompt
            else:
                encode_kwargs["prompt_name"] = self.query_prompt_name

        raw = self._model.encode(
            texts,
            batch_size=kwargs.get("batch_size", self.batch_size),
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
            **encode_kwargs,
        )
        vectors = [[float(x) for x in row] for row in raw]
        dims = self._dims or (len(vectors[0]) if vectors else 0)
        return Embeddings(vectors=vectors, dims=dims, model=self.model_id)

    @property
    def dims(self) -> int | None:
        return self._dims
