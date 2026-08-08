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

from vidtheque_worker.backends.whisperx_stt import WhisperXBackend, normalize_language


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
