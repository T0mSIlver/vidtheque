"""The unified embedder, against a fake sentence-transformers.

No torch, no download, no GPU. What is worth asserting here is everything that
is silent when wrong: which payload goes to which task, which instruction is
applied to which leg, that documents and images get none, and that a bad
keyframe is still a typed 400 rather than a bare 500.
"""

from __future__ import annotations

import io
import sys
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from vidtheque_worker.backends import qwen3_vl_embed as mod
from vidtheque_worker.backends.base import BackendUnavailable, InvalidImageError
from vidtheque_worker.backends.qwen3_vl_embed import (
    DEFAULT_FRAME_INSTRUCTION,
    DEFAULT_QUERY_INSTRUCTION,
    NATIVE_DIMS,
    Qwen3VLEmbedBackend,
)


class FakeSentenceTransformer:
    """Records construction and every encode call."""

    last: "FakeSentenceTransformer | None" = None

    def __init__(self, model_id: str, **kwargs: Any) -> None:
        self.model_id = model_id
        self.kwargs = kwargs
        self.calls: list[tuple[list[Any], dict[str, Any]]] = []
        self.max_seq_length: int | None = None
        FakeSentenceTransformer.last = self

    def get_sentence_embedding_dimension(self) -> int:
        return NATIVE_DIMS

    def encode(self, items, **kwargs: Any):
        self.calls.append((list(items), kwargs))
        return [[0.5, 0.5] for _ in items]


@pytest.fixture
def st(monkeypatch):
    module = ModuleType("sentence_transformers")
    module.SentenceTransformer = FakeSentenceTransformer  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sentence_transformers", module)
    FakeSentenceTransformer.last = None
    return module


def loaded(st, **kwargs) -> Qwen3VLEmbedBackend:
    backend = Qwen3VLEmbedBackend(**kwargs)
    backend.load()
    return backend


# --------------------------------------------------------------------------
# loading
# --------------------------------------------------------------------------


def test_cuda_loads_bf16_with_left_padding(st):
    """bf16 because the card ships bf16 and the decision is full precision;
    left padding because last-token pooling over a causal LM pools a pad token
    otherwise — the same trap Qwen3-Embedding has, silent in exactly the same
    way."""
    backend = loaded(st, device="cuda")
    kwargs = FakeSentenceTransformer.last.kwargs
    assert kwargs["model_kwargs"] == {"dtype": "bfloat16"}
    assert kwargs["tokenizer_kwargs"] == {"padding_side": "left"}
    assert backend.dims == NATIVE_DIMS


def test_cpu_does_not_ask_for_bf16(st):
    loaded(st, device="cpu")
    assert FakeSentenceTransformer.last.kwargs["model_kwargs"] is None


def test_native_width_asks_for_no_truncation(st):
    backend = loaded(st)
    assert "truncate_dim" not in FakeSentenceTransformer.last.kwargs
    assert backend.dims == NATIVE_DIMS


def test_mrl_truncation_is_passed_through_and_reported(st):
    """The fallback lever. It has to reach the model *and* be what `dims` says,
    because mcp/ compares that number against the corpus and disables both
    vector legs when they disagree."""
    backend = loaded(st, truncate_dim=1024)
    assert FakeSentenceTransformer.last.kwargs["truncate_dim"] == 1024
    assert backend.dims == 1024
    assert backend.embed_texts(["a"]).dims == 1024


def test_unloading_drops_the_model(st):
    backend = loaded(st)
    backend.unload()
    assert not backend.loaded
    with pytest.raises(BackendUnavailable):
        backend.embed_texts(["a"])


# --------------------------------------------------------------------------
# the instructions — three behaviours, all silent when wrong
# --------------------------------------------------------------------------


def test_documents_are_embedded_bare(st):
    backend = loaded(st)
    result = backend.embed_texts(["a transcript chunk"], input_type="document")
    _, kwargs = FakeSentenceTransformer.last.calls[0]
    assert "prompt" not in kwargs, "the document side of an asymmetric embedder"
    assert result.instruction is None


def test_a_transcript_query_gets_the_query_instruction(st):
    backend = loaded(st)
    result = backend.embed_texts(["kv cache"], input_type="query")
    _, kwargs = FakeSentenceTransformer.last.calls[0]
    assert kwargs["prompt"] == DEFAULT_QUERY_INSTRUCTION
    assert result.instruction == DEFAULT_QUERY_INSTRUCTION


def test_a_frame_query_gets_a_different_instruction(st):
    """One model, one space, two retrieval tasks. Sharing the instruction
    between the legs would be the quiet way to give up the frame leg's whole
    reason for going unified."""
    backend = loaded(st)
    result = backend.embed_text(["a slide showing a kv-cache diagram"])
    _, kwargs = FakeSentenceTransformer.last.calls[0]
    assert kwargs["prompt"] == DEFAULT_FRAME_INSTRUCTION
    assert result.instruction == DEFAULT_FRAME_INSTRUCTION
    assert DEFAULT_FRAME_INSTRUCTION != DEFAULT_QUERY_INSTRUCTION


def test_images_are_embedded_bare(st, monkeypatch):
    backend = loaded(st)
    monkeypatch.setattr(mod, "_decode", lambda blob, index: f"image-{index}")
    result = backend.embed_images([b"\xff\xd8jpeg", b"\xff\xd8jpeg2"])
    items, kwargs = FakeSentenceTransformer.last.calls[0]
    assert items == ["image-0", "image-1"]
    assert "prompt" not in kwargs
    assert result.instruction is None
    assert len(result.vectors) == 2


def test_the_instructions_are_reportable(st):
    """`/status` reads this so the corpus's recorded `*_embed.query_prefix` can
    be checked against behaviour instead of trusted."""
    backend = loaded(st, query_prompt="Q", frame_query_prompt="F")
    assert backend.instructions() == {
        "document": None,
        "query": "Q",
        "frame_query": "F",
    }


def test_an_operator_override_wins(st):
    backend = loaded(st, query_prompt="mine", frame_query_prompt="also mine")
    backend.embed_texts(["x"], input_type="query")
    assert FakeSentenceTransformer.last.calls[0][1]["prompt"] == "mine"


# --------------------------------------------------------------------------
# the dispatch: one `infer` for two protocols
# --------------------------------------------------------------------------


def test_infer_routes_strings_to_the_text_leg(st):
    backend = loaded(st)
    backend.infer(["a chunk"], input_type="document")
    items, kwargs = FakeSentenceTransformer.last.calls[0]
    assert items == ["a chunk"] and "prompt" not in kwargs


def test_infer_routes_bytes_to_the_image_leg(st, monkeypatch):
    """`/v1/embeddings` has already refused anything that is not a string and
    `/v1/embeddings/image` has already refused an empty upload, so the type of
    the first item is the task — not a guess."""
    backend = loaded(st)
    monkeypatch.setattr(mod, "_decode", lambda blob, index: f"image-{index}")
    backend.infer([b"\xff\xd8jpeg"])
    assert FakeSentenceTransformer.last.calls[0][0] == ["image-0"]


def test_infer_on_an_empty_list_is_an_empty_text_encode(st):
    backend = loaded(st)
    assert backend.infer([]).vectors == []


def test_the_batch_size_is_per_leg(st, monkeypatch):
    """A 1280x720 keyframe is ~1,176 visual tokens against a chunk's few
    hundred, and activation is most of the VRAM estimate."""
    backend = loaded(st, batch_size=16, image_batch_size=4)
    monkeypatch.setattr(mod, "_decode", lambda blob, index: blob)
    backend.embed_texts(["a"])
    backend.embed_images([b"jpeg"])
    assert FakeSentenceTransformer.last.calls[0][1]["batch_size"] == 16
    assert FakeSentenceTransformer.last.calls[1][1]["batch_size"] == 4


def test_vectors_come_back_normalised_by_the_model(st):
    backend = loaded(st)
    backend.embed_texts(["a"])
    assert FakeSentenceTransformer.last.calls[0][1]["normalize_embeddings"] is True


# --------------------------------------------------------------------------
# decoding: a bad keyframe is a typed 400, not a 500
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
    """400 is outside mcp/'s retryable set: the caller drops the frame instead
    of replaying 64 uploads until its backpressure budget is gone."""
    pytest.importorskip("PIL")
    with pytest.raises(InvalidImageError) as raised:
        mod._decode(blob, 7)
    assert raised.value.index == 7
    assert "image 7" in str(raised.value)


def test_a_zero_pixel_image_is_refused(monkeypatch):
    monkeypatch.setattr(
        mod, "_open_rgb", lambda blob: SimpleNamespace(width=0, height=0)
    )
    with pytest.raises(InvalidImageError):
        mod._decode(b"anything", 3)


def test_a_missing_pillow_is_the_worker_being_broken_not_the_upload(monkeypatch):
    """`BackendUnavailable` -> 503, because re-sending the same bytes to a
    fixed worker will work. An `InvalidImageError` here would tell mcp/ to
    discard every keyframe in the corpus."""

    def explode(blob):
        raise BackendUnavailable("pillow is not installed")

    monkeypatch.setattr(mod, "_open_rgb", explode)
    with pytest.raises(BackendUnavailable):
        mod._decode(b"anything", 0)
