"""Fakes shared by the worker tests.

Nothing here imports torch, whisperx or onnxruntime, and nothing downloads a
model: the point of the backend protocols is that the manager and the HTTP
layer can be tested against stand-ins that take microseconds.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any

import pytest

from vidtheque_worker.backends.base import (
    Embeddings,
    OCRItem,
    Segment,
    Transcription,
    Word,
)
from vidtheque_worker.gpu import VramInfo


@dataclass
class Recorder:
    """Ordered log of everything the fakes did, across all backends."""

    events: list[tuple[str, str]] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def record(self, kind: str, what: str) -> None:
        with self._lock:
            self.events.append((kind, what))

    def kinds(self, *kinds: str) -> list[tuple[str, str]]:
        return [e for e in self.events if e[0] in kinds]

    def names(self, *kinds: str) -> list[str]:
        return [e[1] for e in self.kinds(*kinds)]


class FakeBackend:
    """Satisfies the Backend protocol; records load/unload/infer with timing."""

    def __init__(
        self,
        task: str,
        *,
        name: str = "fake",
        model_id: str = "fake-model",
        vram_estimate_mb: int = 1000,
        recorder: Recorder | None = None,
        result: Any = None,
        text_result: Any = None,
        infer_seconds: float = 0.0,
        load_seconds: float = 0.0,
        load_error: Exception | None = None,
        infer_error: Exception | None = None,
    ) -> None:
        self.task = task
        self.name = name
        self.model_id = model_id
        self._vram = vram_estimate_mb
        self.recorder = recorder or Recorder()
        self.result = result
        self.text_result = text_result
        self.infer_seconds = infer_seconds
        self.load_seconds = load_seconds
        self.load_error = load_error
        self.infer_error = infer_error
        self._loaded = False
        self.infer_calls: list[tuple[tuple, dict]] = []
        self.embed_text_calls: list[tuple[tuple, dict]] = []
        self.concurrent = 0
        self.max_concurrent = 0
        self._lock = threading.Lock()

    # -- protocol ----------------------------------------------------------
    @property
    def vram_estimate_mb(self) -> int:
        return self._vram

    @property
    def loaded(self) -> bool:
        return self._loaded

    def load(self) -> None:
        if self._loaded:
            return
        if self.load_error is not None:
            self.recorder.record("load-failed", self.task)
            raise self.load_error
        if self.load_seconds:
            time.sleep(self.load_seconds)
        self._loaded = True
        self.recorder.record("load", self.task)

    def unload(self) -> None:
        if not self._loaded:
            return
        self._loaded = False
        self.recorder.record("unload", self.task)

    def infer(self, *args: Any, **kwargs: Any) -> Any:
        assert self._loaded, f"{self.task} inferred while unloaded"
        with self._lock:
            self.concurrent += 1
            self.max_concurrent = max(self.max_concurrent, self.concurrent)
        try:
            self.recorder.record("infer", self.task)
            self.infer_calls.append((args, kwargs))
            if self.infer_error is not None:
                raise self.infer_error
            if self.infer_seconds:
                time.sleep(self.infer_seconds)
            return self.result
        finally:
            with self._lock:
                self.concurrent -= 1

    def embed_text(self, *args: Any, **kwargs: Any) -> Any:
        """The second tower of an image-embedding backend: same object, same
        slot. Recorded separately so a test can tell the towers apart."""
        assert self._loaded, f"{self.task} embedded text while unloaded"
        self.recorder.record("embed_text", self.task)
        self.embed_text_calls.append((args, kwargs))
        if self.infer_error is not None:
            raise self.infer_error
        return self.text_result if self.text_result is not None else self.result


class FakeVram:
    """Free VRAM derived from what is actually loaded — so eviction really frees."""

    def __init__(self, backends: dict[str, FakeBackend], total_mb: int = 24000) -> None:
        self.backends = backends
        self.total_mb = total_mb
        self.calls = 0

    def __call__(self) -> VramInfo:
        self.calls += 1
        used = sum(b.vram_estimate_mb for b in self.backends.values() if b.loaded)
        return VramInfo(
            total_mb=self.total_mb, used_mb=used, free_mb=self.total_mb - used
        )


class FakeHooks:
    """Stand-in for the shell hook runner: records instead of forking."""

    def __init__(self, recorder: Recorder, error: Exception | None = None) -> None:
        self.recorder = recorder
        self.error = error
        self.calls: list[tuple[str, str]] = []

    async def __call__(self, command: str, label: str) -> None:
        self.calls.append((command, label))
        self.recorder.record("hook", label)
        if self.error is not None:
            raise self.error


# --------------------------------------------------------------------------
# fixtures
# --------------------------------------------------------------------------


@pytest.fixture
def recorder() -> Recorder:
    return Recorder()


@pytest.fixture
def transcription() -> Transcription:
    return Transcription(
        text="hello there general kenobi",
        language="en",
        duration=4.2,
        segments=[
            Segment(
                id=0,
                start=0.0,
                end=2.0,
                text="hello there",
                words=[
                    Word(word="hello", start=0.0, end=0.8, score=0.99),
                    Word(word="there", start=0.9, end=2.0, score=0.97),
                ],
            ),
            Segment(id=1, start=2.1, end=4.2, text="general kenobi", words=[]),
        ],
    )


@pytest.fixture
def embeddings() -> Embeddings:
    return Embeddings(
        vectors=[[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]], dims=3, model="fake-embed"
    )


@pytest.fixture
def image_embeddings() -> Embeddings:
    """A different width from the text fixture on purpose: the two vector
    spaces are separate, and a test that passes with both at 3 dims proves
    less than one that would notice them being crossed."""
    return Embeddings(
        vectors=[[0.1, 0.2, 0.3, 0.4], [0.5, 0.6, 0.7, 0.8]],
        dims=4,
        model="fake-frame-embed",
    )


@pytest.fixture
def frame_query_embeddings() -> Embeddings:
    """Same width as the image fixture — same space, that is the point — but
    different values, so a test can prove which tower answered."""
    return Embeddings(
        vectors=[[0.9, 0.8, 0.7, 0.6]], dims=4, model="fake-frame-embed"
    )


@pytest.fixture
def ocr_items() -> list[list[OCRItem]]:
    return [
        [
            OCRItem(text="uv sync", confidence=0.93, bbox=[10.0, 20.0, 110.0, 44.0]),
            OCRItem(text="$ make test", confidence=0.88, bbox=[10.0, 50.0, 150.0, 74.0]),
        ]
    ]
