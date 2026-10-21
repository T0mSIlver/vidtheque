"""Language-tag handling in the whisperX backend.

Found live: YouTube metadata says ``en-US``, faster-whisper's tokenizer
raises ``ValueError`` on anything but its bare codes, and the whole STT
stage fell over to the caption fallback — silently, from the operator's
point of view. These tests run without whisperx installed: the module is
stubbed into ``sys.modules`` so ``infer`` can execute its import.
"""

from __future__ import annotations

import sys
import types

import pytest

from vidtheque_worker.backends.base import (
    BackendCrashed,
    BackendPoisoned,
    InvalidMediaError,
    looks_like_device_failure,
)
from vidtheque_worker.backends.whisperx_stt import (
    MAX_ALIGN_MODELS,
    WhisperXBackend,
    normalize_language,
)
from vidtheque_worker.lifecycle import LifecycleManager


@pytest.mark.parametrize(
    ("tag", "expected"),
    [
        ("en-US", "en"),
        ("en_GB", "en"),
        ("pt-BR", "pt"),
        ("yue", "yue"),  # 3-letter codes are real whisper languages
        ("EN", "en"),
        ("en", "en"),
        (None, None),
        ("", None),
        ("x", None),  # too short to be a language
        # A plausible-looking primary subtag passes normalization; whisper's
        # own rejection then triggers the auto-detect retry in infer().
        ("not-a-tag-at-all!", "not"),
        ("1234", None),
    ],
)
def test_normalize_language(tag: str | None, expected: str | None) -> None:
    assert normalize_language(tag) == expected


class _FakeModel:
    """Rejects the first language it sees, accepts auto-detect."""

    def __init__(self, reject: set[str]) -> None:
        self.reject = reject
        self.calls: list[str | None] = []

    def transcribe(self, audio, batch_size, language):  # noqa: ANN001
        self.calls.append(language)
        if language in self.reject:
            raise ValueError(f"'{language}' is not a valid language code")
        return {"language": language or "en", "segments": []}


@pytest.fixture()
def stub_whisperx(monkeypatch: pytest.MonkeyPatch, tmp_path):
    module = types.SimpleNamespace(load_audio=lambda path: [0.0] * 16000)
    monkeypatch.setitem(sys.modules, "whisperx", module)
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"\0")
    return audio


def test_infer_normalizes_bcp47_before_transcribing(stub_whisperx) -> None:
    backend = WhisperXBackend(align=False)
    backend._model = _FakeModel(reject={"en-US"})
    result = backend.infer(str(stub_whisperx), language="en-US", align=False)
    assert backend._model.calls == ["en"]  # normalized, never the raw tag
    assert result.language == "en"


def test_infer_retries_with_autodetect_when_whisper_rejects(stub_whisperx) -> None:
    backend = WhisperXBackend(align=False)
    backend._model = _FakeModel(reject={"xx"})
    result = backend.infer(str(stub_whisperx), language="xx-unknown", align=False)
    # "xx" survives normalization (2 alpha chars) but whisper rejects it:
    # one rejected call, then the auto-detect retry.
    assert backend._model.calls == ["xx", None]
    assert result.language == "en"


def test_infer_does_not_loop_when_autodetect_itself_raises(stub_whisperx) -> None:
    backend = WhisperXBackend(align=False)
    backend._model = _FakeModel(reject={None})  # pathological: reject auto too
    with pytest.raises(ValueError):
        backend.infer(str(stub_whisperx), language=None, align=False)
    assert backend._model.calls == [None]  # no retry storm


# --------------------------------------------------------------------------
# unreadable media
# --------------------------------------------------------------------------


def test_media_ffmpeg_cannot_read_is_a_typed_input_error(monkeypatch, tmp_path):
    """ffmpeg refusing a container raises a plain RuntimeError. Untyped, that
    unloaded the model and answered 503 — which mcp/ reads as backpressure, so
    it replayed the same unreadable file until the 1800 s budget was gone."""
    def explode(path):
        raise RuntimeError(f"Failed to load audio: ffmpeg: {path}: Invalid data")

    monkeypatch.setitem(
        sys.modules, "whisperx", types.SimpleNamespace(load_audio=explode)
    )
    audio = tmp_path / "a.webm"
    audio.write_bytes(b"\0")
    backend = WhisperXBackend(align=False)
    backend._model = _FakeModel(reject=set())

    with pytest.raises(InvalidMediaError):
        backend.infer(str(audio), align=False)
    assert not looks_like_device_failure(
        InvalidMediaError("the audio could not be decoded")
    )


def test_a_device_failure_while_loading_audio_still_propagates(monkeypatch, tmp_path):
    def explode(path):
        raise RuntimeError("CUDA error: out of memory")

    monkeypatch.setitem(
        sys.modules, "whisperx", types.SimpleNamespace(load_audio=explode)
    )
    audio = tmp_path / "a.webm"
    audio.write_bytes(b"\0")
    backend = WhisperXBackend(align=False)
    backend._model = _FakeModel(reject=set())

    with pytest.raises(RuntimeError) as raised:
        backend.infer(str(audio), align=False)
    assert not isinstance(raised.value, InvalidMediaError)


# --------------------------------------------------------------------------
# the alignment-model cache
# --------------------------------------------------------------------------


def test_the_alignment_cache_is_bounded_and_evicts_the_oldest(monkeypatch):
    """Each entry is a few hundred MB of device that admission control never
    sees — the 8000 MB estimate is measured with the *default* alignment model
    warm. Unbounded, a polyglot corpus ate the headroom until a transcription
    OOMed with nothing evictable on the card."""
    loaded: list[str] = []

    def load_align_model(language_code, device):  # noqa: ANN001
        loaded.append(language_code)
        return (f"model-{language_code}", {"language": language_code})

    monkeypatch.setitem(
        sys.modules,
        "whisperx",
        types.SimpleNamespace(load_align_model=load_align_model),
    )
    backend = WhisperXBackend()

    for language in ("en", "fr", "de"):
        backend._alignment_model(language)
    assert list(backend._align_cache) == ["en", "fr", "de"]
    assert loaded == ["en", "fr", "de"]

    # A cache hit reloads nothing and moves that language to the back...
    assert backend._alignment_model("en")[0] == "model-en"
    assert loaded == ["en", "fr", "de"]
    assert list(backend._align_cache) == ["fr", "de", "en"]

    # ...so the fourth language evicts `fr`, the least recently used.
    backend._alignment_model("es")
    assert list(backend._align_cache) == ["de", "en", "es"]
    assert len(backend._align_cache) == MAX_ALIGN_MODELS


def test_unloading_still_drops_every_alignment_model(monkeypatch):
    monkeypatch.setitem(
        sys.modules,
        "whisperx",
        types.SimpleNamespace(load_align_model=lambda language_code, device: (1, 2)),
    )
    backend = WhisperXBackend()
    backend._loaded = True
    backend._alignment_model("en")
    backend.unload()
    assert backend._align_cache == {}


# --------------------------------------------------------------------------
# alignment failures
# --------------------------------------------------------------------------


class _OneSegment:
    def transcribe(self, audio, batch_size, language):  # noqa: ANN001
        return {"language": "en", "segments": [{"start": 0.0, "end": 1.0, "text": "hi"}]}


def _stub_aligner(monkeypatch, tmp_path, align_error: Exception):
    def align(*args, **kwargs):
        raise align_error

    monkeypatch.setitem(
        sys.modules,
        "whisperx",
        types.SimpleNamespace(
            load_model=lambda *a, **k: _OneSegment(),
            load_audio=lambda path: [0.0] * 16000,
            load_align_model=lambda language_code, device: ("model", {}),
            align=align,
        ),
    )
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"\0")
    return audio


def test_an_alignment_oom_poisons_the_backend_instead_of_dropping_words(
    monkeypatch, tmp_path
):
    """Alignment is a bonus, a dead device is not: swallowing the OOM kept the
    poisoned model loaded (gpu-validation §5.1) and shipped a transcript with
    no word timestamps as a success."""
    audio = _stub_aligner(
        monkeypatch, tmp_path, RuntimeError("CUDA out of memory. Tried to allocate 2.00 GiB")
    )
    backend = WhisperXBackend(device="cuda")
    backend._model = _OneSegment()

    with pytest.raises(BackendPoisoned):
        backend.infer(str(audio))


def test_a_non_device_alignment_failure_is_still_a_bonus(monkeypatch, tmp_path):
    audio = _stub_aligner(monkeypatch, tmp_path, ValueError("no phonemes for this script"))
    backend = WhisperXBackend(device="cuda")
    backend._model = _OneSegment()

    result = backend.infer(str(audio))
    assert [s.text for s in result.segments] == ["hi"]


async def test_an_alignment_oom_unloads_the_stt_slot(monkeypatch, tmp_path):
    audio = _stub_aligner(monkeypatch, tmp_path, RuntimeError("CUDA out of memory"))
    backend = WhisperXBackend(device="cuda")
    manager = LifecycleManager({"stt": backend}, vram_probe=lambda: None)
    await manager.start()
    try:
        with pytest.raises(BackendCrashed):
            await manager.submit("stt", lambda b: b.infer(str(audio)))
        assert not backend.loaded
    finally:
        await manager.stop()
