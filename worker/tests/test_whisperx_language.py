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

from vidtheque_worker.backends.base import InvalidMediaError, looks_like_device_failure
from vidtheque_worker.backends.whisperx_stt import (
    MAX_ALIGN_MODELS,
    WhisperXBackend,
    normalize_language,
)


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
