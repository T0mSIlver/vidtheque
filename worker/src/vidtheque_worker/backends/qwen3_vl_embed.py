"""Qwen3-VL-Embedding: one checkpoint, both vector legs, one space.

The unified embedder (`research/multimodal-embedding-2026-08-09.md`, Tom's
decision of 2026-08-09): `Qwen/Qwen3-VL-Embedding-2B`, Apache-2.0, native
**2048** dims, bf16 weights (~4.4 GB), 32K text context, and an image tower
from the model class that measures 1.3-3.6x better than a CLIP-style dual
encoder on document-like imagery — which is what a conference-talk corpus of
slides, terminals and editors actually is.

It replaces two models with one: `Qwen/Qwen3-Embedding-0.6B` on the transcript
leg and `google/siglip2-so400m-patch16-naflex` on the frame leg. Both of those
are still selectable (`registry.py` keeps their entries); this is the shipped
default.

**This one object serves two lifecycle tasks.** `embed` and `image_embed` are
separate tasks because indexing needs both at once and one slot would force the
pipeline to choose — but when both tasks resolve to the *same* checkpoint,
`build_backends` hands the manager the same instance twice and
`LifecycleManager` gives it **one shared Slot**. That is not a detail: one slot
per task would load ~4.4 GB twice, charge admission control ~8.8 GB for a model
already on the card, and start evicting whisperX to make room for it
(`research/multimodal-embedding-2026-08-09.md` §5.5).

So `infer` is the entry point of *both* protocols and dispatches on the payload
type — `list[str]` is `EmbedBackend.infer`, `list[bytes]` is
`ImageEmbedBackend.infer`. That dispatch is total rather than a guess: the two
HTTP paths validate their payloads before they submit, `/v1/embeddings` rejects
anything that is not a string and `/v1/embeddings/image` rejects an empty
upload, so the type of the first item is the task. Nothing about the three
paths changes; only the routing behind them collapses.

Three instructions, three behaviours, and getting them wrong degrades recall
without erroring:

* **documents and images are embedded bare** — no instruction, the checkpoint's
  own system prompt. That is the document side of an asymmetric embedder.
* **`input_type=query` on `/v1/embeddings`** applies :data:`DEFAULT_QUERY_INSTRUCTION`
  (override: `EMBED_QUERY_PROMPT`).
* **`/v1/embeddings/frame-query`** applies :data:`DEFAULT_FRAME_INSTRUCTION`
  (override: `FRAME_QUERY_PROMPT`) — a *different* instruction, because the
  model is instruction-aware and "find the passage that answers this" and "find
  the frame that shows this" are different retrieval tasks over one space.

Both instructions are echoed back on the response (`instruction`) and on
`/status`, because the corpus records them in `config['text_embed.query_prefix']`
and `config['frame_embed.query_prefix']` and that record has drifted from
behaviour once already (`research/pipeline-perf-2026-08-09.md` §5: `'query: '`
recorded against a model that applies `Instruct: …`). A record nobody can check
is a record that lies.

The 64-token ceiling that `siglip2_image_embed.py` had to warn about is gone
with the text tower it belonged to: frame queries now go through a 32K-context
model, so a long descriptive frame query is embedded whole.
"""

from __future__ import annotations

import io
import logging
from typing import Any

from .base import (
    BackendError,
    BackendUnavailable,
    BaseBackend,
    Embeddings,
    InvalidImageError,
)

log = logging.getLogger(__name__)

DEFAULT_MODEL = "Qwen/Qwen3-VL-Embedding-2B"

NATIVE_DIMS = 2048
"""The checkpoint's native width. MRL truncation (64-2048) is a config knob —
`EMBED_DIM` — and is the documented fallback if query latency or storage ever
argues for it, not the starting point (Tom, 2026-08-09)."""

DEFAULT_QUERY_INSTRUCTION = (
    "Given a search query, retrieve the transcript passage that answers it"
)
"""Applied to `/v1/embeddings` with `input_type=query`, and to nothing else.

Explicit rather than the checkpoint's registered `query` prompt on purpose: the
corpus records this string in `config['text_embed.query_prefix']`, and a
default that moves with a card revision would silently invalidate that record.
"""

DEFAULT_FRAME_INSTRUCTION = (
    "Given a search query, retrieve the video frame that matches it"
)
"""Applied to `/v1/embeddings/frame-query`. Recorded as
`config['frame_embed.query_prefix']`."""


class Qwen3VLEmbedBackend(BaseBackend):
    """Text, images and text->image queries in one 2048-d space."""

    name = "qwen3-vl-embedding"
    task = "embed"
    """The *primary* task. The same instance is registered under `image_embed`
    too when both are configured to this backend; the lifecycle manager shares
    one Slot between them and reports the pair on `/status`."""

    serves_tasks = ("embed", "image_embed")
    """What this backend can answer. `build_backends` reads it to decide
    whether one instance may serve both slots."""

    # ESTIMATE, NOT A MEASUREMENT. ~4.4 GB of bf16 weights plus activation over
    # ~1,176 visual tokens per 1280x720 keyframe at the shipped
    # `VIDTHEQUE_FRAME_EMBED_BATCH=8`. The memo's envelope is 6,000-7,000 MB
    # (`research/multimodal-embedding-2026-08-09.md` §4.1) and this takes the
    # top of it: admission control refusing a load costs a 503 and a retry,
    # while under-estimating costs an OOM mid-batch that poisons the context.
    # **Bench item: replace with the measured peak.**
    default_vram_mb = 7000

    def __init__(
        self,
        model_id: str = DEFAULT_MODEL,
        *,
        device: str = "cpu",
        batch_size: int = 8,
        image_batch_size: int | None = None,
        truncate_dim: int | None = None,
        query_prompt: str | None = None,
        frame_query_prompt: str | None = None,
        max_seq_length: int | None = None,
        vram_estimate_mb: int | None = None,
    ) -> None:
        super().__init__(model_id, vram_estimate_mb=vram_estimate_mb)
        self.device = device
        self.batch_size = batch_size
        self.image_batch_size = image_batch_size or batch_size
        self.truncate_dim = truncate_dim
        """MRL width, or None for the checkpoint's native 2048."""
        self.query_prompt = query_prompt or DEFAULT_QUERY_INSTRUCTION
        self.frame_query_prompt = frame_query_prompt or DEFAULT_FRAME_INSTRUCTION
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

        log.info(
            "loading unified embedder model=%s device=%s dim=%s",
            self.model_id,
            self.device,
            self.truncate_dim or "native",
        )
        # bf16, not fp16: the card ships bf16 weights and the decision is full
        # precision until a bench says otherwise. fp16 on a 2B LLM-based
        # embedder is where the silent NaN lives.
        model_kwargs = {"dtype": "bfloat16"} if self.device == "cuda" else None
        kwargs: dict[str, Any] = {
            "device": self.device,
            "model_kwargs": model_kwargs,
            # Last-token pooling over a causal LM: padding on the right pools a
            # pad token. Same trap as Qwen3-Embedding, same fix.
            "tokenizer_kwargs": {"padding_side": "left"},
        }
        if self.truncate_dim:
            kwargs["truncate_dim"] = self.truncate_dim
        self._model = SentenceTransformer(self.model_id, **kwargs)
        if self.max_seq_length:
            self._model.max_seq_length = self.max_seq_length
        self._dims = _reported_dims(self._model, self.truncate_dim)

    def _unload(self) -> None:
        self._model = None
        self._empty_cuda_cache()

    # -- dispatch ----------------------------------------------------------
    def infer(
        self, items: list[Any], *, input_type: str = "document", **kwargs: Any
    ) -> Embeddings:
        """`EmbedBackend.infer` and `ImageEmbedBackend.infer` in one method.

        The payload type is the task, and it is decided by the endpoint that
        built the payload, not by this method guessing: `/v1/embeddings` has
        already refused anything that is not a string, `/v1/embeddings/image`
        has already refused an empty upload. See the module docstring.
        """
        if items and not isinstance(items[0], str):
            return self.embed_images(list(items), **kwargs)
        return self.embed_texts(list(items), input_type=input_type, **kwargs)

    # -- text --------------------------------------------------------------
    def embed_texts(
        self, texts: list[str], *, input_type: str = "document", **kwargs: Any
    ) -> Embeddings:
        """Transcript chunks (`document`) or a transcript query (`query`)."""
        instruction = self.query_prompt if input_type == "query" else None
        return self._encode(
            texts,
            instruction=instruction,
            batch_size=int(kwargs.get("batch_size", self.batch_size)),
        )

    def embed_text(self, texts: list[str], **kwargs: Any) -> Embeddings:
        """A **frame** query: text into the same space `embed_images` writes to.

        Named for the `ImageEmbedBackend` protocol it satisfies — this is what
        `/v1/embeddings/frame-query` calls, and with a unified model it is the
        same weights, the same slot and the same space as
        :meth:`embed_texts`, differing only in the instruction. It keeps its own
        endpoint anyway: an unknown *field* on `/v1/embeddings` is ignored by a
        hosted provider and an unknown *path* 404s, and that asymmetry is the
        whole reason the sibling path exists (`index-schema.md` §4.5).
        """
        return self._encode(
            texts,
            instruction=self.frame_query_prompt,
            batch_size=int(kwargs.get("batch_size", self.batch_size)),
        )

    # -- images ------------------------------------------------------------
    def embed_images(self, images: list[bytes], **kwargs: Any) -> Embeddings:
        """Keyframes, bare — the document side takes no instruction.

        No patch budget: `IMAGE_EMBED_MAX_PATCHES` was NaFlex's knob. This model
        takes the frame at its stored resolution (1280x720 -> ~1,176 visual
        tokens), which the paper's own sweep puts at the knee of its scaling
        curve — there is nothing to tune and nothing to under-feed.
        """
        frames = [_decode(blob, index) for index, blob in enumerate(images)]
        return self._encode(
            frames,
            instruction=None,
            batch_size=int(kwargs.get("batch_size", self.image_batch_size)),
        )

    # -- the one encode ----------------------------------------------------
    def _encode(
        self, items: list[Any], *, instruction: str | None, batch_size: int
    ) -> Embeddings:
        if self._model is None:
            raise BackendUnavailable("the unified embedding model is not loaded")
        encode_kwargs: dict[str, Any] = {}
        if instruction:
            encode_kwargs["prompt"] = instruction
        raw = self._model.encode(
            items,
            batch_size=max(1, batch_size),
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
            **encode_kwargs,
        )
        vectors = [[float(x) for x in row] for row in raw]
        dims = self._dims or (len(vectors[0]) if vectors else 0)
        return Embeddings(
            vectors=vectors, dims=dims, model=self.model_id, instruction=instruction
        )

    # -- introspection -----------------------------------------------------
    @property
    def dims(self) -> int | None:
        return self._dims

    def instructions(self) -> dict[str, str | None]:
        """What `/status` reports, so the config record can be checked.

        `config['text_embed.query_prefix']` and `config['frame_embed.query_prefix']`
        record what indexing assumed; these are what the worker actually
        applies. One `curl /status` is the whole reconciliation.
        """
        return {
            "document": None,
            "query": self.query_prompt,
            "frame_query": self.frame_query_prompt,
        }


def _reported_dims(model: Any, truncate_dim: int | None) -> int | None:
    if truncate_dim:
        return int(truncate_dim)
    try:
        return int(model.get_sentence_embedding_dimension())
    except Exception:  # pragma: no cover - defensive
        return None


# --------------------------------------------------------------------------
# decoding
#
# Deliberately this module's own copy rather than a shared helper: each image
# backend owns the exact failure it turns into a typed 400, and each one's
# tests patch its own module's `_open_rgb` to force the decoder's failure modes
# without a real PIL. A shared function would make one of those two suites lie.
# --------------------------------------------------------------------------


def _decode(blob: bytes, index: int) -> Any:
    """Decode one upload, or say which one was not an image.

    Same contract as the SigLIP backend's: this endpoint cannot answer per
    file — there is no vector to put in the failed slot and `mcp/` rejects a
    response short by one — so a bad frame fails the batch, as a 400 naming the
    position. 400 is outside `mcp/`'s retryable set, so the caller drops the
    frame instead of replaying 64 uploads until its budget is gone.
    """
    try:
        image = _open_rgb(blob)
    except BackendError:
        raise  # BackendUnavailable: the *worker* is broken, not the upload
    except Exception as exc:
        raise InvalidImageError(
            f"image {index} could not be decoded: {type(exc).__name__}: {exc}",
            index=index,
        ) from exc
    if not image.width or not image.height:
        raise InvalidImageError(f"image {index} has no pixels", index=index)
    return image


def _open_rgb(blob: bytes) -> Any:
    try:
        from PIL import Image
    except ImportError as exc:  # pragma: no cover - depends on gpu extra
        raise BackendUnavailable(
            "pillow is not installed — install the worker's 'gpu' extra"
        ) from exc

    with Image.open(io.BytesIO(blob)) as image:
        return image.convert("RGB")
