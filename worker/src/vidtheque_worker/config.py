"""Worker configuration.

Every setting is read from the environment. Names are prefixed ``VIDTHEQUE_``;
the ones that read naturally bare (``STT_BACKEND``, ``GPU_ACQUIRE_CMD``, …) also
accept the unprefixed spelling, because they are what an operator types into a
compose file. All of them are documented in ``deploy/.env.example``.
"""

from __future__ import annotations

import os
from functools import lru_cache

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _either(name: str) -> AliasChoices:
    """Accept both ``VIDTHEQUE_FOO`` and bare ``FOO``, prefixed winning."""
    return AliasChoices(f"VIDTHEQUE_{name}", name)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="VIDTHEQUE_",
        env_file=".env",
        extra="ignore",
        populate_by_name=True,
    )

    # --- server -----------------------------------------------------------
    host: str = Field(
        default="0.0.0.0",
        validation_alias=AliasChoices("VIDTHEQUE_WORKER_HOST", "VIDTHEQUE_HOST", "HOST"),
    )
    # VIDTHEQUE_WORKER_PORT wins over VIDTHEQUE_PORT: when mcp and the worker
    # share one env file (single-box deployment, 2026-08-11), VIDTHEQUE_PORT
    # belongs to mcp and the worker taking it binds mcp's port. dev_stack.sh
    # and deploy/staging always set the WORKER_ form; the bare forms remain for
    # a worker deployed alone.
    port: int = Field(
        default=8081,
        validation_alias=AliasChoices("VIDTHEQUE_WORKER_PORT", "VIDTHEQUE_PORT", "PORT"),
    )
    log_level: str = Field(default="info", validation_alias=_either("LOG_LEVEL"))

    docs_enabled: bool = Field(default=False, validation_alias=_either("WORKER_DOCS"))
    """FastAPI's /docs, /redoc and /openapi.json. Off by default.

    This service has no authentication, so anything it publishes it publishes to
    whoever can reach the port. Handy while developing, a map while deployed
    (2026-08-10 audit, F-32)."""

    # --- backend selection ------------------------------------------------
    stt_backend: str = Field(default="whisperx", validation_alias=_either("STT_BACKEND"))
    embed_backend: str = Field(
        default="qwen3-vl-embedding", validation_alias=_either("EMBED_BACKEND")
    )
    image_embed_backend: str = Field(
        default="qwen3-vl-embedding", validation_alias=_either("IMAGE_EMBED_BACKEND")
    )
    """Defaulted to the *same* backend as ``embed_backend``, which is what makes
    the shipped configuration one model in one shared slot. Point the two at
    different backends (``qwen3-embedding`` + ``siglip2``, the pre-2026-08-09
    pair, or the §7 hybrid) and they go back to two models in two slots — still
    supported, and still two loads on a cold ``content_type=all`` search."""

    ocr_backend: str = Field(default="rapidocr", validation_alias=_either("OCR_BACKEND"))

    # --- model identifiers (backend-interpreted) --------------------------
    stt_model: str = Field(default="large-v3", validation_alias=_either("STT_MODEL"))
    embed_model: str = Field(
        default="Qwen/Qwen3-VL-Embedding-2B", validation_alias=_either("EMBED_MODEL")
    )
    image_embed_model: str = Field(
        default="Qwen/Qwen3-VL-Embedding-2B",
        validation_alias=_either("IMAGE_EMBED_MODEL"),
    )
    """Must equal ``embed_model`` for the shared slot to engage — a difference,
    even one that resolves to the same weights, is read as two checkpoints and
    loads two."""

    ocr_model: str = Field(default="rapidocr-default", validation_alias=_either("OCR_MODEL"))

    model_revision: str | None = Field(
        default=None, validation_alias=_either("MODEL_REVISION")
    )
    """Pin every hub download to one commit, instead of tracking a moving head.

    Empty by default because there is no revision that is right for whatever
    `EMBED_MODEL` an operator sets, and inventing one would break the moment
    they change the model. But an unpinned id is a repository we re-download
    whatever it says today: SentenceTransformers reads a repository-controlled
    `sentence_bert_config.json` whose `model_kwargs` can set
    `weights_only: false`, and Transformers then calls unrestricted
    `torch.load` — unpickling as root, on a box with a GPU attached.
    :func:`safe_model_kwargs` closes that specific door; this closes the door
    of the artifact changing under you at all. (2026-08-10 audit, F-27.)
    """

    # --- embedding behaviour ----------------------------------------------
    embed_query_prompt: str | None = Field(
        default=None, validation_alias=_either("EMBED_QUERY_PROMPT")
    )
    """Instruction for ``/v1/embeddings`` with ``input_type=query``. Unset uses
    the backend's documented default, which the corpus records as
    ``config['text_embed.query_prefix']``."""

    frame_query_prompt: str | None = Field(
        default=None, validation_alias=_either("FRAME_QUERY_PROMPT")
    )
    """Instruction for ``/v1/embeddings/frame-query``. A *different* instruction
    from ``embed_query_prompt`` on purpose: one unified model, one space, two
    retrieval tasks. Ignored by a dual-encoder frame backend (SigLIP 2's text
    tower takes no instruction). Recorded as
    ``config['frame_embed.query_prefix']``."""

    embed_dim: int = Field(default=0, validation_alias=_either("EMBED_DIM"))
    """MRL truncation width for the unified embedder; ``0`` = the checkpoint's
    native 2048.

    The fallback lever, not the starting point (Tom, 2026-08-09): Qwen publish a
    sweep showing 1024 -> 512 costing 1.4% of retrieval, so narrowing later is a
    config change plus a ~12-minute re-embed. It must match
    ``config['text_embed.dim']`` / ``config['frame_embed.dim']`` in the corpus
    or `mcp/` disables both vector legs rather than mix widths."""

    embed_batch_size: int = Field(
        default=16, validation_alias=_either("EMBED_BATCH_SIZE")
    )
    """Texts per forward pass inside the worker. `mcp/` batches per *request*
    (``VIDTHEQUE_EMBED_BATCH``); this bounds what one request costs in VRAM."""

    image_embed_batch_size: int = Field(
        default=8, validation_alias=_either("IMAGE_EMBED_BATCH_SIZE")
    )
    """Frames per forward pass. Lower than the text batch because a 1280x720
    keyframe is ~1,176 visual tokens against a chunk's few hundred, and the
    activation is what the VRAM estimate is mostly made of."""

    image_embed_max_patches: int = Field(
        default=256, validation_alias=_either("IMAGE_EMBED_MAX_PATCHES")
    )
    """NaFlex patch budget: the frame-embedder's resolution knob, per request.

    A **SigLIP-2-only** knob. The unified embedder ignores it — it takes the
    frame at its stored resolution, which lands on the knee of its own scaling
    curve with nothing to tune."""

    # --- device -----------------------------------------------------------
    device: str = Field(default="auto", validation_alias=_either("DEVICE"))
    """``auto`` | ``cuda`` | ``cpu``. ``auto`` uses CUDA when torch reports it."""

    compute_type: str = Field(default="auto", validation_alias=_either("COMPUTE_TYPE"))
    """ctranslate2 compute type. ``auto`` → ``float16`` on CUDA, ``int8`` on CPU."""

    stt_batch_size: int = Field(default=16, validation_alias=_either("STT_BATCH_SIZE"))
    stt_align: bool = Field(default=True, validation_alias=_either("STT_ALIGN"))
    """Word-level forced alignment. Off = coarser timestamps, faster runs."""

    ocr_threads: int = Field(default=4, validation_alias=_either("OCR_THREADS"))
    """ONNX Runtime intra-op threads. Left to itself it reads the *host* core
    count rather than the container's and oversubscribes."""

    # --- lifecycle --------------------------------------------------------
    idle_unload_seconds: float = Field(
        default=300.0, validation_alias=_either("IDLE_UNLOAD_SECONDS")
    )
    """Unload a model after this many seconds without a job. ``0`` disables."""

    embed_resident: bool = Field(default=False, validation_alias=_either("EMBED_RESIDENT"))
    """Keep the embedding model loaded permanently and exempt from eviction.

    It applies to the ``embed`` slot. With the shipped unified configuration
    that *is* the frame slot — one shared Slot carries one ``resident`` flag,
    so the model is resident for both legs or for neither, and there is one
    decision to make instead of two. (Before the unification this key covered
    the text embedder only, because the frame embedder was a second, larger
    checkpoint that was never worth pinning.)

    Leave it **off**, and more firmly than before. A resident backend keeps
    ``_any_lease_holder()`` true forever, so ``GPU_RELEASE_CMD`` never fires
    while the worker is up — measured in
    ``research/gpu-validation-2026-08-08.md`` §5.3, where llama.cpp is stopped
    at the first embedding request and never restarted. That trap cost 1,483 MB
    standing with the 0.6B text embedder; with a unified 2B it costs ~4.4 GB
    standing *and* the co-tenant still never comes back."""

    vram_headroom_mb: int = Field(default=512, validation_alias=_either("VRAM_HEADROOM_MB"))
    """Slack required on top of a backend's estimate before a load is allowed."""

    gpu_index: int = Field(default=0, validation_alias=_either("GPU_INDEX"))
    """Which NVML device to account against."""

    gpu_acquire_cmd: str | None = Field(
        default=None, validation_alias=_either("GPU_ACQUIRE_CMD")
    )
    """Shell command run *before the first* model load (e.g. stop llama-server)."""

    gpu_release_cmd: str | None = Field(
        default=None, validation_alias=_either("GPU_RELEASE_CMD")
    )
    """Shell command run *after the last* unload (e.g. restart llama-server)."""

    gpu_hook_timeout_seconds: float = Field(
        default=60.0, validation_alias=_either("GPU_HOOK_TIMEOUT_SECONDS")
    )

    shutdown_grace_seconds: float = Field(
        default=30.0, validation_alias=_either("SHUTDOWN_GRACE_SECONDS")
    )
    """How long shutdown waits for a job that is already inside a model.

    ``asyncio.to_thread`` cannot be cancelled, so the alternative to waiting is
    freeing weights out from under a live thread. ``0`` disables the wait."""

    # --- caches -----------------------------------------------------------
    hf_home: str | None = Field(default=None, validation_alias=AliasChoices("HF_HOME"))
    """Weight cache directory. Applied by :func:`apply_process_env`, because
    the libraries that honour it read ``os.environ``, not this object."""

    def apply_process_env(self) -> None:
        """Push the settings that libraries read from the environment back into it.

        pydantic-settings reads ``.env`` into *this object* and stops there,
        but ``transformers``, ``huggingface_hub`` and ``ctranslate2`` all look
        at ``os.environ["HF_HOME"]``. So the documented local-dev path — put
        ``HF_HOME`` in ``.env`` — silently cached several GB of weights in
        ``~/.cache/huggingface`` instead of where the operator asked for them
        (in the container, the mounted volume), and a rebuild re-downloaded
        them. Called before any backend is constructed.
        """
        if self.hf_home:
            os.environ["HF_HOME"] = self.hf_home

    def resolved_device(self) -> str:
        """Resolve ``auto`` against whatever torch says, without importing it eagerly."""
        if self.device != "auto":
            return self.device
        try:  # pragma: no cover - depends on the optional gpu extra
            import torch

            return "cuda" if torch.cuda.is_available() else "cpu"
        except Exception:
            return "cpu"

    def resolved_compute_type(self, device: str) -> str:
        if self.compute_type != "auto":
            return self.compute_type
        return "float16" if device == "cuda" else "int8"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
