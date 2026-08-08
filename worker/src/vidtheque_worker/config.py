"""Worker configuration.

Every setting is read from the environment. Names are prefixed ``VIDTHEQUE_``;
the ones that read naturally bare (``STT_BACKEND``, ``GPU_ACQUIRE_CMD``, …) also
accept the unprefixed spelling, because they are what an operator types into a
compose file. All of them are documented in ``deploy/.env.example``.
"""

from __future__ import annotations

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
    host: str = Field(default="0.0.0.0", validation_alias=_either("HOST"))
    port: int = Field(default=8081, validation_alias=_either("PORT"))
    log_level: str = Field(default="info", validation_alias=_either("LOG_LEVEL"))

    # --- backend selection ------------------------------------------------
    stt_backend: str = Field(default="whisperx", validation_alias=_either("STT_BACKEND"))
    embed_backend: str = Field(default="bge-m3", validation_alias=_either("EMBED_BACKEND"))
    ocr_backend: str = Field(default="rapidocr", validation_alias=_either("OCR_BACKEND"))

    # --- model identifiers (backend-interpreted) --------------------------
    stt_model: str = Field(default="large-v3", validation_alias=_either("STT_MODEL"))
    embed_model: str = Field(default="BAAI/bge-m3", validation_alias=_either("EMBED_MODEL"))
    ocr_model: str = Field(default="rapidocr-default", validation_alias=_either("OCR_MODEL"))

    # --- device -----------------------------------------------------------
    device: str = Field(default="auto", validation_alias=_either("DEVICE"))
    """``auto`` | ``cuda`` | ``cpu``. ``auto`` uses CUDA when torch reports it."""

    compute_type: str = Field(default="auto", validation_alias=_either("COMPUTE_TYPE"))
    """ctranslate2 compute type. ``auto`` → ``float16`` on CUDA, ``int8`` on CPU."""

    stt_batch_size: int = Field(default=16, validation_alias=_either("STT_BATCH_SIZE"))
    stt_align: bool = Field(default=True, validation_alias=_either("STT_ALIGN"))
    """Word-level forced alignment. Off = coarser timestamps, faster runs."""

    # --- lifecycle --------------------------------------------------------
    idle_unload_seconds: float = Field(
        default=300.0, validation_alias=_either("IDLE_UNLOAD_SECONDS")
    )
    """Unload a model after this many seconds without a job. ``0`` disables."""

    embed_resident: bool = Field(default=False, validation_alias=_either("EMBED_RESIDENT"))
    """Keep the embedding model loaded permanently and exempt from eviction."""

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

    # --- caches -----------------------------------------------------------
    hf_home: str | None = Field(default=None, validation_alias=AliasChoices("HF_HOME"))

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
