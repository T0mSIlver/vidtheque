"""Settings: env names, aliases, and the derived device/compute-type pairs."""

from __future__ import annotations

import os

import pytest

from vidtheque_worker.backends.registry import (
    UnknownBackend,
    build_backend,
    build_backends,
)
from vidtheque_worker.config import Settings


def settings(**env) -> Settings:
    return Settings(_env_file=None, **env)


def two_model_settings(**env) -> Settings:
    """The pre-2026-08-09 pair: Qwen3-Embedding for text, SigLIP 2 for frames.

    Still a supported configuration (it is the §7 hybrid's shape, and a
    self-hoster on a smaller card may prefer it), so it keeps its coverage —
    it is just no longer what an unset environment gets.
    """
    return settings(
        embed_backend="qwen3-embedding",
        embed_model="Qwen/Qwen3-Embedding-0.6B",
        image_embed_backend="siglip2",
        image_embed_model="google/siglip2-so400m-patch16-naflex",
        **env,
    )


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
        "qwen3-vl-embedding",
        "qwen3-vl-embedding",
        "rapidocr",
    )
    # Both legs, one checkpoint, native width: Tom's 2026-08-09 decision.
    assert s.embed_model == "Qwen/Qwen3-VL-Embedding-2B"
    assert s.image_embed_model == "Qwen/Qwen3-VL-Embedding-2B"
    assert s.embed_dim == 0, "0 = the checkpoint's native 2048; MRL is the fallback"
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
        "qwen3-vl-embedding",
        "qwen3-vl-embedding",
        "rapidocr",
    ]
    assert not any(b.loaded for b in backends.values())


# --------------------------------------------------------------------------
# the shared slot: one checkpoint under two task names
# --------------------------------------------------------------------------


def test_the_unified_embedder_is_one_instance_under_both_tasks():
    """The whole point, and it is identity rather than equality.

    Two instances of the same class would be two sets of weights: ~4.4 GB
    loaded twice and ~8.8 GB charged to admission control for one model, which
    starts evicting whisperX to make room for something already on the card
    (research/multimodal-embedding-2026-08-09.md §5.5).
    """
    backends = build_backends(settings(device="cuda"))
    assert backends["embed"] is backends["image_embed"]


def test_two_different_models_are_two_instances():
    backends = build_backends(two_model_settings(device="cuda"))
    assert backends["embed"] is not backends["image_embed"]
    assert backends["embed"].name == "qwen3-embedding"
    assert backends["image_embed"].name == "siglip2"


def test_the_same_backend_with_different_model_ids_is_not_shared():
    """A difference in the id is read as two checkpoints, because it is one.

    Sharing on the backend *name* alone would quietly serve frames from the
    text model's weights while `/status` reported two ids.
    """
    backends = build_backends(
        settings(device="cuda", image_embed_model="Qwen/Qwen3-VL-Embedding-8B")
    )
    assert backends["embed"] is not backends["image_embed"]


def test_the_unified_embedder_serves_both_tasks():
    backend = build_backends(settings(device="cpu"))["image_embed"]
    assert set(backend.serves_tasks) == {"embed", "image_embed"}


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
    backends = build_backends(two_model_settings(device="cuda"))
    assert (
        backends["image_embed"].vram_estimate_mb > backends["embed"].vram_estimate_mb
    )


def test_the_unified_embedder_still_fits_under_whisperx():
    """The line the 8B failed and the 2B passes: the worker's peak is
    whisperX's 7,941 MB and the embedder must not raise it, or the ~12 GB
    llama.cpp lease has to be resized (memo §4.1)."""
    backends = build_backends(settings(device="cuda"))
    assert backends["embed"].vram_estimate_mb <= backends["stt"].vram_estimate_mb


def test_the_frame_estimate_follows_the_configured_patch_budget():
    """The knob the SigLIP config invites you to turn: 1024 patches is ~4x the
    work per image. With a static estimate, admission still believed the
    256-patch figure, admitted the load, and the first real batch OOMed
    mid-inference — then the client retried the same input into the same OOM,
    30 s at a time, until the job died."""
    at_256 = build_backends(two_model_settings(device="cuda"))["image_embed"]
    at_1024 = build_backends(
        two_model_settings(device="cuda", image_embed_max_patches=1024)
    )["image_embed"]
    assert at_256.vram_estimate_mb == 3200, "the measured 256-patch figure"
    assert at_1024.vram_estimate_mb > at_256.vram_estimate_mb * 1.5


def test_a_smaller_patch_budget_does_not_shrink_the_estimate():
    """Scaling is upward only: nobody has measured the bottom of the range,
    and an under-estimate is an OOM while an over-estimate is an eviction."""
    small = build_backends(
        two_model_settings(device="cuda", image_embed_max_patches=128)
    )["image_embed"]
    assert small.vram_estimate_mb == 3200


def test_the_patch_budget_does_not_reach_the_unified_embedder():
    """It is a NaFlex knob. The unified model takes the frame whole, so a
    budget set for a SigLIP deployment must not silently mean something else
    after the swap."""
    at_256 = build_backends(settings(device="cuda"))["image_embed"]
    at_1024 = build_backends(
        settings(device="cuda", image_embed_max_patches=1024)
    )["image_embed"]
    assert at_256.vram_estimate_mb == at_1024.vram_estimate_mb


def test_the_stt_estimate_follows_the_configured_batch_size():
    at_16 = build_backends(settings(device="cuda"))["stt"]
    at_64 = build_backends(settings(device="cuda", stt_batch_size=64))["stt"]
    at_4 = build_backends(settings(device="cuda", stt_batch_size=4))["stt"]
    assert at_16.vram_estimate_mb == 8000, "measured peak at the default batch"
    assert at_64.vram_estimate_mb > at_16.vram_estimate_mb
    assert at_4.vram_estimate_mb == 8000, "upward only, same as the patch budget"


def test_hf_home_reaches_the_environment_the_libraries_read(monkeypatch):
    """pydantic-settings fills this object and stops there; transformers,
    huggingface_hub and ctranslate2 all read os.environ. Parsed-and-dropped
    meant weights landed in ~/.cache/huggingface however the operator set it,
    and every container rebuild re-downloaded several GB."""
    monkeypatch.delenv("HF_HOME", raising=False)
    settings(hf_home="/hf-cache").apply_process_env()
    assert os.environ["HF_HOME"] == "/hf-cache"


def test_an_unset_hf_home_leaves_the_environment_alone(monkeypatch):
    monkeypatch.setenv("HF_HOME", "/somewhere/else")
    Settings(_env_file=None, hf_home=None).apply_process_env()
    assert os.environ["HF_HOME"] == "/somewhere/else"


def test_unknown_backend_name_lists_the_alternatives():
    with pytest.raises(UnknownBackend) as excinfo:
        build_backend("stt", "wav2vec-vibes", settings())
    assert "whisperx" in str(excinfo.value)


def test_unknown_image_embed_backend_lists_the_alternatives():
    with pytest.raises(UnknownBackend) as excinfo:
        build_backend("image_embed", "open-clip", settings())
    assert "siglip2" in str(excinfo.value)
