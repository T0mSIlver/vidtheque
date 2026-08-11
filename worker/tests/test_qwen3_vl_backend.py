"""The unified embedder, against a fake sentence-transformers.

No torch, no download, no GPU. What is worth asserting here is everything that
is silent when wrong: which payload goes to which task, which instruction is
applied to which leg, that documents and images get none, and that a bad
keyframe is still a typed 400 rather than a bare 500.
"""

from __future__ import annotations

import io
import logging
import sys
import threading
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from vidtheque_worker.backends import qwen3_vl_embed as mod
from vidtheque_worker.backends.base import (
    BackendUnavailable,
    InvalidImageError,
    watch_for_uninitialised_weights,
)
from vidtheque_worker.backends.qwen3_vl_embed import (
    DEFAULT_FRAME_INSTRUCTION,
    DEFAULT_QUERY_INSTRUCTION,
    NATIVE_DIMS,
    Qwen3VLEmbedBackend,
)


class FakeSentenceTransformer:
    """Records construction and every encode call."""

    last: "FakeSentenceTransformer | None" = None
    builds: list["FakeSentenceTransformer"] = []
    warn_uninitialised: "list[bool] | None" = None
    """Per-construction script for the transformers random-init warning: one
    bool per load attempt, consumed in order. ``None`` means a clean load."""

    def __init__(self, model_id: str, **kwargs: Any) -> None:
        self.model_id = model_id
        self.kwargs = kwargs
        self.calls: list[tuple[list[Any], dict[str, Any]]] = []
        self.max_seq_length: int | None = None
        FakeSentenceTransformer.last = self
        FakeSentenceTransformer.builds.append(self)
        script = FakeSentenceTransformer.warn_uninitialised
        if script and script.pop(0):
            logging.getLogger("transformers.modeling_utils").warning(
                "Some weights of Qwen3VLModel were not initialized from the model "
                "checkpoint at %s and are newly initialized: ['language_model."
                "embed_tokens.weight']",
                model_id,
            )

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
    FakeSentenceTransformer.builds = []
    FakeSentenceTransformer.warn_uninitialised = None
    yield module
    FakeSentenceTransformer.warn_uninitialised = None


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
    assert kwargs["model_kwargs"]["dtype"] == "bfloat16"
    assert kwargs["tokenizer_kwargs"] == {"padding_side": "left"}
    assert backend.dims == NATIVE_DIMS


def test_cpu_does_not_ask_for_bf16(st):
    loaded(st, device="cpu")
    assert "dtype" not in (FakeSentenceTransformer.last.kwargs["model_kwargs"] or {})


# --------------------------------------------------------------------------
# the load that must not be assumed
#
# `from_pretrained` answers a key mismatch with a warning and a random tensor,
# not an exception. The card is saved from `Qwen3VLForConditionalGeneration`
# (`model.language_model.…`) and loaded through `AutoModel` (`language_model.…`),
# and transformers 4.57.6 cannot bridge that because `Qwen3VLModel.base_model_prefix`
# is `""`. All 625 tensors came back random, twelve loads in a row, and the
# worker served correctly-shaped noise for 176 videos
# (`research/embedding-random-init-2026-08-10.md`).
# --------------------------------------------------------------------------


def test_the_checkpoint_key_mapping_reaches_from_pretrained(st):
    """`model_kwargs` is forwarded verbatim to `AutoModel.from_pretrained`, so
    this dict is the whole fix. Lose it and every vector is a random
    projection — with a 200 on the response and a unit norm on the vector."""
    loaded(st, device="cuda")
    assert (
        FakeSentenceTransformer.last.kwargs["model_kwargs"]["key_mapping"]
        == mod.CHECKPOINT_KEY_MAPPING
    )
    assert mod.CHECKPOINT_KEY_MAPPING == {r"^model\.": ""}


def test_a_random_init_under_the_mapping_retries_without_it(st, caplog):
    """The mapping is a workaround for a transformers *version*. If a later one
    fixes `base_model_prefix`, the mapping strips a prefix the loader also wants
    to strip and the load goes random the other way — so the guard retries
    plain rather than pinning the worker to one release."""
    FakeSentenceTransformer.warn_uninitialised = [True, False]
    with caplog.at_level(logging.WARNING, logger=mod.__name__):
        backend = loaded(st)
    assert len(FakeSentenceTransformer.builds) == 2
    assert "key_mapping" in FakeSentenceTransformer.builds[0].kwargs["model_kwargs"]
    # The retry drops key_mapping and keeps everything else — including the
    # safety kwargs, which a repository-controlled config must never be able to
    # weaken (2026-08-10 audit, F-27).
    retried = FakeSentenceTransformer.builds[1].kwargs["model_kwargs"]
    assert "key_mapping" not in retried
    assert retried["use_safetensors"] is True and retried["weights_only"] is True
    assert backend.loaded
    assert "randomly initialised" in "\n".join(caplog.messages)


def test_a_random_init_both_ways_refuses_to_serve(st):
    """503, not a vector. A worker that is down costs the batch a retry; a
    worker serving random projections costs the corpus — silently, because the
    vectors are the right width, unit-norm and perfectly stable within one
    process."""
    FakeSentenceTransformer.warn_uninitialised = [True, True]
    backend = Qwen3VLEmbedBackend()
    with pytest.raises(BackendUnavailable) as raised:
        backend.load()
    assert "randomly initialised" in str(raised.value)
    assert "newly initialized" in str(raised.value), "quotes what transformers said"
    assert not backend.loaded


def test_a_clean_load_costs_exactly_one_attempt(st):
    loaded(st)
    assert len(FakeSentenceTransformer.builds) == 1


def test_the_watcher_sees_a_child_loggers_warning():
    """transformers logs this from `transformers.modeling_utils`; the handler
    hangs off `transformers`, and propagation is what connects them."""
    with watch_for_uninitialised_weights() as hits:
        logging.getLogger("transformers.modeling_utils").warning(
            "Some weights of X were not initialized from the model checkpoint at Y "
            "and are newly initialized: ['a.b']"
        )
        logging.getLogger("transformers.modeling_utils").warning("some other warning")
    assert len(hits) == 1 and "newly initialized" in hits[0]


def test_the_watcher_cannot_be_silenced_by_verbosity_and_restores_it():
    """`TRANSFORMERS_VERBOSITY=error` must not be a way to turn the guard off,
    and the guard must not be a way to turn an operator's verbosity back on."""
    logger = logging.getLogger("transformers")
    logger.setLevel(logging.ERROR)
    try:
        with watch_for_uninitialised_weights() as hits:
            logging.getLogger("transformers.modeling_utils").warning(
                "and are newly initialized: ['a.b']"
            )
        assert hits
        assert logger.level == logging.ERROR
    finally:
        logger.setLevel(logging.NOTSET)


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


# --------------------------------------------------------------------------
# decoding a *batch*: fanned out, but with the serial contract intact
#
# The fan-out exists because this whole call runs inside the lifecycle
# manager's GPU critical section, so decode time is time the card is idle
# (`research/multimodal-embedding-2026-08-09.md`, appendix 2026-08-09). None of
# these tests time anything — a timing assertion on a shared CI box is a
# flake — they assert the *structure* that makes the overlap possible and the
# three behaviours that must survive it: order, which failure is reported, and
# the 400/503 split.
# --------------------------------------------------------------------------


def test_a_batch_decodes_concurrently(monkeypatch):
    """A barrier is the deterministic version of "these ran at the same time".

    Every decode waits for all four to arrive before any returns, so a serial
    implementation deadlocks into the timeout and fails; only genuine overlap
    passes. No sleeps, no wall-clock thresholds.
    """
    barrier = threading.Barrier(4, timeout=5)

    def decode(blob: bytes, index: int) -> str:
        barrier.wait()
        return f"image-{index}"

    monkeypatch.setattr(mod, "_decode", decode)
    assert mod._decode_all([b"a", b"b", b"c", b"d"]) == [
        "image-0",
        "image-1",
        "image-2",
        "image-3",
    ]


def test_one_image_is_decoded_on_the_calling_thread(monkeypatch):
    """The pool loses at n=1 (measured 1.8 ms serial against 2.6 ms pooled:
    starting a thread costs more than the decode), and single-frame requests
    are a real shape on `/v1/embeddings/image`."""
    seen: list[str] = []

    monkeypatch.setattr(
        mod,
        "_decode",
        lambda blob, index: seen.append(threading.current_thread().name) or "frame",
    )
    mod._decode_all([b"a"])
    assert seen == [threading.current_thread().name]


def test_a_batch_keeps_upload_order_when_decodes_finish_out_of_order(monkeypatch):
    """Vectors come back in upload order or `mcp/` writes the wrong frame's
    vector against the wrong timestamp — silently, since both are floats."""
    release = [threading.Event() for _ in range(3)]

    def decode(blob: bytes, index: int) -> str:
        # Finish last-to-first: index 2 frees 1, which frees 0.
        if index < 2:
            release[index].wait(timeout=5)
        result = f"image-{index}"
        if index:
            release[index - 1].set()
        return result

    monkeypatch.setattr(mod, "_decode", decode)
    assert mod._decode_all([b"a", b"b", b"c"]) == ["image-0", "image-1", "image-2"]


def test_the_lowest_index_failure_is_the_one_reported(monkeypatch):
    """`mcp/` halves a refused batch to find the offender
    (`runner._call_per_frame`). Reporting whichever thread failed first would
    send that search down the wrong half, so the index has to be the same one a
    serial decode would have raised on."""

    def decode(blob: bytes, index: int) -> str:
        if index in (1, 3):
            raise InvalidImageError(f"image {index} could not be decoded", index=index)
        return f"image-{index}"

    monkeypatch.setattr(mod, "_decode", decode)
    with pytest.raises(InvalidImageError) as raised:
        mod._decode_all([b"a", b"b", b"c", b"d"])
    assert raised.value.index == 1


def test_a_broken_worker_still_beats_a_broken_upload(monkeypatch):
    """A `BackendUnavailable` (503) from a *later* position must not be
    downgraded to the earlier position's 400: the two answers send `mcp/` in
    opposite directions — retry the batch, or discard the frame."""

    def decode(blob: bytes, index: int) -> str:
        raise (
            InvalidImageError("bad upload", index=index)
            if index
            else BackendUnavailable("pillow is not installed")
        )

    monkeypatch.setattr(mod, "_decode", decode)
    with pytest.raises(BackendUnavailable):
        mod._decode_all([b"a", b"b"])


def test_the_pool_never_outgrows_the_batch_or_the_box(monkeypatch):
    """Sized per call, so a 64-image request does not start 64 threads and a
    two-image one does not start eight. There is no env var here on purpose."""
    captured: dict[str, Any] = {}
    real = mod.ThreadPoolExecutor

    def spy(*args: Any, **kwargs: Any):
        captured.update(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(mod, "ThreadPoolExecutor", spy)
    monkeypatch.setattr(mod, "_decode", lambda blob, index: index)
    monkeypatch.setattr(mod.os, "cpu_count", lambda: 4)

    mod._decode_all([b"x"] * 64)
    assert captured["max_workers"] == 4, "capped by the box"

    mod._decode_all([b"x"] * 2)
    assert captured["max_workers"] == 2, "capped by the batch"

    monkeypatch.setattr(mod.os, "cpu_count", lambda: 64)
    mod._decode_all([b"x"] * 64)
    assert captured["max_workers"] == mod.MAX_DECODE_WORKERS, "capped by the ceiling"


def test_the_frame_leg_logs_the_decode_encode_split(st, monkeypatch, caplog):
    """The whole point of the fan-out is that decode is dead time on the card,
    and the only way tomorrow's restart can be validated against tonight's
    numbers is if the worker says which half went where."""
    backend = loaded(st)
    monkeypatch.setattr(mod, "_decode", lambda blob, index: f"image-{index}")
    with caplog.at_level(logging.INFO, logger=mod.__name__):
        backend.embed_images([b"a", b"b"])
    line = "\n".join(caplog.messages)
    assert "image embed: 2 frames" in line
    assert "decode=" in line and "encode=" in line and "img/s" in line


def test_the_query_legs_do_not_log_at_info(st, caplog):
    """One line per search would drown the indexing throughput this log exists
    to explain."""
    backend = loaded(st)
    with caplog.at_level(logging.INFO, logger=mod.__name__):
        backend.embed_texts(["kv cache"], input_type="query")
        backend.embed_text(["a slide showing a kv-cache diagram"])
    assert caplog.messages == []
