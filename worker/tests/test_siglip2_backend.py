"""SigLIP 2's two towers, against a fake processor and a fake model.

No torch, no transformers, no download: the backend's job at this level is
*what it hands the processor*, and on transformers 4.x that is the whole
correctness story for the text tower — lowercased text padded to the trained
64-token window. Get it wrong and nothing raises; retrieval just degrades. So
it gets an assertion rather than a comment.
"""

from __future__ import annotations

import contextlib
import io
import logging
import math
from types import SimpleNamespace
from typing import Any

import pytest

from vidtheque_worker.backends import siglip2_image_embed as mod
from vidtheque_worker.backends.base import (
    BackendUnavailable,
    InvalidImageError,
    looks_like_device_failure,
)
from vidtheque_worker.backends.siglip2_image_embed import (
    TEXT_CONTEXT_TOKENS,
    SigLIP2Backend,
)


# --------------------------------------------------------------------------
# the smallest torch-shaped surface the backend actually uses
# --------------------------------------------------------------------------


class FakeTensor:
    """Rows of floats with the four operations ``_encode*`` performs."""

    def __init__(self, rows: list[list[float]]) -> None:
        self.rows = [list(row) for row in rows]

    def norm(self, p: int = 2, dim: int = -1, keepdim: bool = True) -> FakeTensor:
        assert (p, dim, keepdim) == (2, -1, True)
        return FakeTensor([[math.sqrt(sum(x * x for x in row))] for row in self.rows])

    def __truediv__(self, other: FakeTensor) -> FakeTensor:
        norms = [row[0] for row in other.rows]
        return FakeTensor(
            [[x / norms[i] for x in row] for i, row in enumerate(self.rows)]
        )

    def float(self) -> FakeTensor:
        return self

    def cpu(self) -> FakeTensor:
        return self

    def __iter__(self):
        return iter(self.rows)


class FakeMask:
    """Only ``sum(dim=-1).tolist()`` is ever asked of an attention mask."""

    def __init__(self, lengths: list[int]) -> None:
        self.lengths = lengths

    def sum(self, dim: int = -1) -> Any:
        assert dim == -1
        return SimpleNamespace(tolist=lambda: list(self.lengths))


class FakeInputs(dict):
    """``BatchFeature`` stand-in: a mapping that can be ``.to(device)``'d."""

    def to(self, device: str) -> FakeInputs:
        self.moved_to = device
        return self


class FakeProcessor:
    def __init__(self, *, mask_lengths: list[int] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.mask_lengths = mask_lengths

    def __call__(self, **kwargs: Any) -> FakeInputs:
        self.calls.append(kwargs)
        payload: dict[str, Any] = {"input_ids": "tokens"}
        if self.mask_lengths is not None:
            payload["attention_mask"] = FakeMask(self.mask_lengths)
        return FakeInputs(payload)


class FakeModel:
    device = "cpu"

    def __init__(self, rows: list[list[float]]) -> None:
        self.rows = rows
        self.text_calls: list[dict[str, Any]] = []
        self.image_calls: list[dict[str, Any]] = []

    def get_text_features(self, **kwargs: Any) -> FakeTensor:
        self.text_calls.append(kwargs)
        return FakeTensor(self.rows[: len(self.rows)])

    def get_image_features(self, **kwargs: Any) -> FakeTensor:
        self.image_calls.append(kwargs)
        return FakeTensor(self.rows[: len(self.rows)])


class FakePooledOutput:
    """transformers 5.x's ``BaseModelOutputWithPooling``, minus everything.

    It deliberately has no ``.norm``: that is exactly why 5.x turned both towers
    into 500s.
    """

    def __init__(self, pooled: FakeTensor) -> None:
        self.pooler_output = pooled
        self.last_hidden_state = "a sequence tensor, not what we want"


class WrappingFakeModel(FakeModel):
    """Same fake, transformers 5.x return shape."""

    def get_text_features(self, **kwargs: Any) -> FakePooledOutput:
        return FakePooledOutput(super().get_text_features(**kwargs))

    def get_image_features(self, **kwargs: Any) -> FakePooledOutput:
        return FakePooledOutput(super().get_image_features(**kwargs))


FAKE_TORCH = SimpleNamespace(inference_mode=contextlib.nullcontext)


def make_backend(
    *,
    rows: list[list[float]] | None = None,
    mask_lengths: list[int] | None = None,
    model_cls: type[FakeModel] = FakeModel,
    **kwargs: Any,
) -> tuple[SigLIP2Backend, FakeProcessor, FakeModel]:
    backend = SigLIP2Backend("google/siglip2-so400m-patch16-naflex", **kwargs)
    processor = FakeProcessor(mask_lengths=mask_lengths)
    model = model_cls(rows if rows is not None else [[3.0, 4.0]])
    backend._processor = processor
    backend._model = model
    backend._torch = FAKE_TORCH
    backend._dims = 1152
    backend._loaded = True
    return backend, processor, model


# --------------------------------------------------------------------------
# text tower
# --------------------------------------------------------------------------


def test_text_tower_lowercases_and_pads_to_the_trained_window():
    backend, processor, _ = make_backend()
    backend.embed_text(["A Terminal Showing A Stack Trace"])
    assert processor.calls == [
        {
            # Lowercased by us: the checkpoint was trained that way and the
            # 4.x processor does not do it.
            "text": ["a terminal showing a stack trace"],
            "padding": "max_length",
            "max_length": 64,
            "truncation": True,
            "return_tensors": "pt",
        }
    ]
    assert TEXT_CONTEXT_TOKENS == 64


def test_text_tower_returns_l2_normalised_vectors_in_the_frame_space():
    backend, _, _ = make_backend(rows=[[3.0, 4.0]])
    result = backend.embed_text(["a terminal"])
    assert result.vectors == [[0.6, 0.8]]
    assert math.isclose(sum(x * x for x in result.vectors[0]), 1.0)
    # The declared projection width, not the fake's row length.
    assert result.dims == 1152
    assert result.model == "google/siglip2-so400m-patch16-naflex"


def test_text_tower_batches_by_batch_size():
    backend, processor, _ = make_backend(batch_size=2)
    backend.embed_text(["one", "two", "three"])
    assert [call["text"] for call in processor.calls] == [["one", "two"], ["three"]]


def test_a_query_that_fills_the_window_is_logged(caplog):
    backend, _, _ = make_backend(mask_lengths=[TEXT_CONTEXT_TOKENS])
    with caplog.at_level(logging.WARNING, logger=mod.__name__):
        backend.embed_text(["a very long query " * 20])
    assert "truncated" in caplog.text


def test_a_short_query_is_not_logged(caplog):
    backend, _, _ = make_backend(mask_lengths=[4])
    with caplog.at_level(logging.WARNING, logger=mod.__name__):
        backend.embed_text(["a terminal"])
    assert caplog.text == ""


def test_a_processor_without_an_attention_mask_is_fine():
    backend, _, _ = make_backend(mask_lengths=None)
    assert backend.embed_text(["a terminal"]).vectors == [[0.6, 0.8]]


def test_text_tower_without_a_loaded_model_is_unavailable():
    backend = SigLIP2Backend("google/siglip2-so400m-patch16-naflex")
    with pytest.raises(BackendUnavailable):
        backend.embed_text(["a terminal"])


# --------------------------------------------------------------------------
# one checkpoint, two towers
# --------------------------------------------------------------------------


def test_both_towers_run_on_one_model_instance(monkeypatch):
    """The reason the frame query shares the ``image_embed`` slot: it is the
    same object, so serving a query loads nothing extra."""
    backend, processor, model = make_backend()
    monkeypatch.setattr(mod, "_decode", lambda blob, index: blob)

    backend.infer([b"\xff\xd8jpeg"], max_num_patches=1024)
    backend.embed_text(["a terminal"])

    assert len(model.image_calls) == 1
    assert len(model.text_calls) == 1
    assert processor.calls[0]["max_num_patches"] == 1024
    assert "images" in processor.calls[0] and "text" not in processor.calls[0]
    assert "text" in processor.calls[1] and "images" not in processor.calls[1]


def test_image_path_still_defaults_to_the_configured_budget(monkeypatch):
    backend, processor, _ = make_backend(max_num_patches=576)
    monkeypatch.setattr(mod, "_decode", lambda blob, index: blob)
    backend.infer([b"\xff\xd8jpeg"])
    assert processor.calls[0]["max_num_patches"] == 576


# --------------------------------------------------------------------------
# the two return shapes of get_*_features
# --------------------------------------------------------------------------


@pytest.mark.parametrize("model_cls", [FakeModel, WrappingFakeModel], ids=["4.x", "5.x"])
def test_both_towers_survive_either_transformers_return_shape(
    model_cls: type[FakeModel], monkeypatch
) -> None:
    """4.x returns the pooled tensor, 5.x a ``BaseModelOutputWithPooling``.

    Only 4.x was handled, so on 5.x ``features.norm(...)`` was an
    AttributeError and both ``/v1/embeddings/image`` and
    ``/v1/embeddings/frame-query`` answered 500 — frame indexing and frame
    search dying together the day the whisperX pin frees
    (research/e2e-smoke-2026-08-08.md §4.3).
    """
    backend, _, _ = make_backend(rows=[[3.0, 4.0]], model_cls=model_cls)
    monkeypatch.setattr(mod, "_decode", lambda blob, index: blob)

    assert backend.embed_text(["a terminal"]).vectors == [[0.6, 0.8]]
    assert backend.infer([b"\xff\xd8jpeg"]).vectors == [[0.6, 0.8]]


# --------------------------------------------------------------------------
# decoding: the one part of this file that needs a real PIL
# --------------------------------------------------------------------------


def _png(size: tuple[int, int] = (8, 6)) -> bytes:
    Image = pytest.importorskip("PIL.Image")
    buffer = io.BytesIO()
    Image.new("RGB", size, (1, 2, 3)).save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.mark.parametrize(
    ("blob", "why"),
    [
        (b"this is not an image at all", "garbage"),
        (_png()[:40], "truncated"),
        (b"<svg xmlns='http://www.w3.org/2000/svg'><rect/></svg>", "svg"),
    ],
    ids=["garbage", "truncated", "svg"],
)
def test_undecodable_bytes_are_a_typed_input_error(blob, why):
    """PIL raises `UnidentifiedImageError`/`OSError`, which matched no handler
    in the app and so came back as a bare 500 — read by mcp/ as a
    non-retryable failure of the whole indexing job, for one bad keyframe."""
    pytest.importorskip("PIL")
    with pytest.raises(InvalidImageError) as raised:
        mod._decode(blob, 7)
    assert raised.value.index == 7
    assert "image 7" in str(raised.value)
    # Never mistaken for the card, whatever the message says.
    assert not looks_like_device_failure(raised.value)


def test_a_real_image_decodes_to_rgb():
    pytest.importorskip("PIL")
    image = mod._decode(_png((8, 6)), 0)
    assert (image.width, image.height, image.mode) == (8, 6, "RGB")


def test_an_image_with_no_pixels_is_refused(monkeypatch):
    """A decoder that says yes to zero pixels still has nothing to embed, and
    the processor's failure on it would be a 500 rather than a 400."""
    monkeypatch.setattr(
        mod, "_open_rgb", lambda blob: SimpleNamespace(width=0, height=0)
    )
    with pytest.raises(InvalidImageError, match="no pixels"):
        mod._decode(b"whatever", 3)


def test_a_missing_pillow_is_the_workers_problem_not_the_uploads(monkeypatch):
    """BackendUnavailable is a 503 the operator must fix; it must not be
    relabelled as "your bytes are bad" on the way through the decoder."""
    def explode(blob):
        raise BackendUnavailable("pillow is not installed")

    monkeypatch.setattr(mod, "_open_rgb", explode)
    with pytest.raises(BackendUnavailable):
        mod._decode(b"whatever", 0)


def test_an_unrecognised_return_shape_is_not_silently_embedded() -> None:
    """A wrong-shaped tensor written next to real vectors is unfindable later,
    so an unknown shape raises rather than guessing at an attribute."""
    backend, _, model = make_backend()
    model.get_text_features = lambda **kwargs: SimpleNamespace(logits="?")
    with pytest.raises(TypeError, match="pooler_output"):
        backend.embed_text(["a terminal"])
