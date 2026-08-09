"""RapidOCR's batch semantics, against a fake engine.

No onnxruntime, no models: what this level is about is which failures belong
to one image and which belong to the request. Getting that wrong is expensive
in one direction only — mcp/ requires one result per input and treats a 4xx/5xx
as a non-retryable failure of the whole stage, so a single unreadable keyframe
raising would cost the other sixty-three their OCR.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from vidtheque_worker.backends.base import BackendUnavailable
from vidtheque_worker.backends.rapidocr_ocr import RapidOCRBackend


class FakeEngine:
    """Answers per blob: an exception instance raises, anything else is a result."""

    def __init__(self, answers: list[object]) -> None:
        self.answers = list(answers)
        self.calls: list[bytes] = []

    def __call__(self, blob: bytes, text_score: float = 0.5) -> object:
        self.calls.append(blob)
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return answer


def result(*lines: str) -> SimpleNamespace:
    return SimpleNamespace(
        txts=list(lines),
        scores=[0.9] * len(lines),
        boxes=[[(0.0, 0.0), (10.0, 0.0), (10.0, 5.0), (0.0, 5.0)]] * len(lines),
    )


def make_backend(answers: list[object]) -> tuple[RapidOCRBackend, FakeEngine]:
    backend = RapidOCRBackend()
    engine = FakeEngine(answers)
    backend._engine = engine
    backend._loaded = True
    return backend, engine


def test_every_image_gets_a_page_in_order():
    backend, _ = make_backend([result("uv sync"), result(), result("$ make test")])
    pages = backend.infer([b"a", b"b", b"c"])
    assert [[item.text for item in page.items] for page in pages] == [
        ["uv sync"],
        [],
        ["$ make test"],
    ]
    assert [page.error for page in pages] == [None, None, None]


def test_an_undecodable_image_is_its_own_failure():
    backend, engine = make_backend(
        [result("uv sync"), ValueError("could not decode image bytes"), result("ok")]
    )
    pages = backend.infer([b"good", b"corrupt", b"good"])

    assert len(pages) == 3, "one page per input, even when one of them failed"
    assert pages[1].items == []
    assert pages[1].code == "invalid_image"
    assert "image 1" in pages[1].error
    # and the batch carried on rather than losing the third image with it
    assert [item.text for item in pages[2].items] == ["ok"]
    assert len(engine.calls) == 3


def test_a_device_failure_is_the_requests_failure_not_the_images():
    """The next image would hit the same wall, and the slot needs unloading —
    so this one propagates instead of becoming sixty-four page errors."""
    backend, engine = make_backend([RuntimeError("CUDA error: out of memory"), None])
    with pytest.raises(RuntimeError, match="CUDA"):
        backend.infer([b"a", b"b"])
    assert len(engine.calls) == 1


def test_inference_without_an_engine_is_unavailable():
    backend = RapidOCRBackend()
    with pytest.raises(BackendUnavailable):
        backend.infer([b"a"])
