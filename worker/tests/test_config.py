"""Settings: env names, aliases, and the derived device/compute-type pairs."""

from __future__ import annotations

import pytest

from vidtheque_worker.backends.registry import (
    UnknownBackend,
    build_backend,
    build_backends,
)
from vidtheque_worker.config import Settings


def settings(**env) -> Settings:
    return Settings(_env_file=None, **env)


DOCUMENTED_ENV = (
    "STT_BACKEND",
    "EMBED_BACKEND",
    "IMAGE_EMBED_BACKEND",
    "OCR_BACKEND",
    "IDLE_UNLOAD_SECONDS",
    "EMBED_RESIDENT",
    "IMAGE_EMBED_MAX_PATCHES",
    "OCR_THREADS",
    "PORT",
    "SHUTDOWN_GRACE_SECONDS",
)


@pytest.fixture
def clean_env(monkeypatch):
    for name in DOCUMENTED_ENV:
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(f"VIDTHEQUE_{name}", raising=False)


def test_defaults_match_the_documented_ones(clean_env):
    s = settings()
    assert (s.stt_backend, s.embed_backend, s.image_embed_backend, s.ocr_backend) == (
        "whisperx",
        "qwen3-embedding",
        "siglip2",
        "rapidocr",
    )
    assert s.embed_model == "Qwen/Qwen3-Embedding-0.6B"
    assert s.image_embed_model == "google/siglip2-so400m-patch16-naflex"
    assert s.idle_unload_seconds == 300.0
    assert s.embed_resident is False
    assert s.image_embed_max_patches == 256
    assert s.ocr_threads == 4
    assert s.port == 8081
    assert s.shutdown_grace_seconds == 30.0


def test_bare_env_name_is_accepted(monkeypatch):
    monkeypatch.setenv("STT_BACKEND", "somethingelse")
    assert Settings(_env_file=None).stt_backend == "somethingelse"


def test_prefixed_env_name_wins(monkeypatch):
    monkeypatch.setenv("STT_BACKEND", "bare")
    monkeypatch.setenv("VIDTHEQUE_STT_BACKEND", "prefixed")
    assert Settings(_env_file=None).stt_backend == "prefixed"


def test_bool_and_float_envs_parse(monkeypatch):
    monkeypatch.setenv("EMBED_RESIDENT", "1")
    monkeypatch.setenv("IDLE_UNLOAD_SECONDS", "42.5")
    s = Settings(_env_file=None)
    assert s.embed_resident is True
    assert s.idle_unload_seconds == 42.5


@pytest.mark.parametrize(
    ("device", "expected"),
    [("cpu", "int8"), ("cuda", "float16")],
)
def test_compute_type_follows_the_device(device, expected):
    assert settings(compute_type="auto").resolved_compute_type(device) == expected


def test_explicit_compute_type_is_respected():
    assert settings(compute_type="float32").resolved_compute_type("cpu") == "float32"


def test_explicit_device_is_respected():
    assert settings(device="cuda").resolved_device() == "cuda"


# --------------------------------------------------------------------------
# registry
# --------------------------------------------------------------------------


def test_build_backends_instantiates_without_loading_weights():
    backends = build_backends(settings(device="cpu"))
    assert set(backends) == {"stt", "embed", "image_embed", "ocr"}
    assert [b.name for b in backends.values()] == [
        "whisperx",
        "qwen3-embedding",
        "siglip2",
        "rapidocr",
    ]
    assert not any(b.loaded for b in backends.values())


def test_bge_m3_stays_selectable():
    backend = build_backend("embed", "bge-m3", settings(device="cpu", embed_model="BAAI/bge-m3"))
    assert backend.name == "bge-m3"
    assert backend.model_id == "BAAI/bge-m3"
    assert backend.task == "embed"


def test_cpu_backends_claim_no_vram():
    backends = build_backends(settings(device="cpu"))
    assert backends["stt"].vram_estimate_mb == 0
    assert backends["embed"].vram_estimate_mb == 0
    assert backends["image_embed"].vram_estimate_mb == 0
    assert backends["ocr"].vram_estimate_mb == 0


def test_cuda_backends_declare_an_estimate():
    backends = build_backends(settings(device="cuda"))
    assert backends["stt"].vram_estimate_mb > 0
    assert backends["embed"].vram_estimate_mb > 0
    assert backends["image_embed"].vram_estimate_mb > 0
    # OCR is CPU-only whatever DEVICE says, so it never joins the eviction game.
    assert backends["ocr"].vram_estimate_mb == 0


def test_the_frame_embedder_is_the_biggest_model():
    backends = build_backends(settings(device="cuda"))
    assert (
        backends["image_embed"].vram_estimate_mb > backends["embed"].vram_estimate_mb
    )


def test_unknown_backend_name_lists_the_alternatives():
    with pytest.raises(UnknownBackend) as excinfo:
        build_backend("stt", "wav2vec-vibes", settings())
    assert "whisperx" in str(excinfo.value)


def test_unknown_image_embed_backend_lists_the_alternatives():
    with pytest.raises(UnknownBackend) as excinfo:
        build_backend("image_embed", "open-clip", settings())
    assert "siglip2" in str(excinfo.value)
