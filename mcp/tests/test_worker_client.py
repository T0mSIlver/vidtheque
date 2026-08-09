"""The indexing half of the worker seam: retries, uploads, and the one stub.

Everything runs against an in-process transport. CLAUDE.md's boundary rule is
why: the contract under test is ``worker/openapi.json``, not any worker code,
and no import crosses the line even in a test.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx2 as httpx
import pytest

from vidtheque_mcp.embeddings import EmbeddingUnavailable
from vidtheque_mcp.pipeline.worker_client import (
    FRAME_QUERY_PATH,
    HTTPWorkerClient,
    WorkerRejected,
    WorkerUnavailable,
)


def make_client(handler, **kwargs: Any) -> tuple[HTTPWorkerClient, list[float]]:
    client = HTTPWorkerClient("http://worker:8081", retries=kwargs.pop("retries", 3), **kwargs)
    client._client = httpx.AsyncClient(
        base_url="http://worker:8081", transport=httpx.MockTransport(handler)
    )
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    client._sleep = fake_sleep
    return client, slept


def embeddings_body(dim: int = 1152, count: int = 1) -> dict[str, Any]:
    return {
        "object": "list",
        "model": "siglip2-so400m-patch16-naflex",
        "dimensions": dim,
        "data": [{"index": i, "embedding": [0.1] * dim} for i in range(count)],
    }


# ------------------------------------------------------------------- retries


async def test_503_with_retry_after_is_waited_out_not_failed(tmp_path: Path) -> None:
    """The worker emits exactly this under VRAM pressure: a lease it cannot take
    yet. An indexing job has hours; it should wait the seconds it was given."""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if len(calls) == 1:
            return httpx.Response(503, headers={"Retry-After": "7"}, json={"detail": "loading"})
        return httpx.Response(200, json=embeddings_body())

    client, slept = make_client(handler)
    image = tmp_path / "frame.jpg"
    image.write_bytes(b"\xff\xd8\xff\xe0jpeg\xff\xd9")

    vectors, model, dims = await client.embed_images([image])
    assert len(calls) == 2 and slept == [7.0]
    assert dims == 1152 and len(vectors) == 1 and model


async def test_retry_after_is_ignored_when_it_is_not_a_number(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, headers={"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"})

    client, slept = make_client(handler, retries=1)
    with pytest.raises(WorkerUnavailable):
        await client.ocr([tmp_path / "missing.jpg"] and [_jpeg(tmp_path)])
    # Fell back to the exponential rather than trusting a clock we cannot read.
    assert slept and 2.0 <= slept[0] <= 2.5


async def test_retries_are_bounded_and_then_reported_as_unavailable(tmp_path: Path) -> None:
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        return httpx.Response(503)

    client, _ = make_client(handler, retries=2)
    with pytest.raises(WorkerUnavailable) as caught:
        await client.embed_images([_jpeg(tmp_path)])
    assert len(attempts) == 3  # the first go plus two retries
    assert "503" in str(caught.value)


async def test_a_4xx_is_not_retried(tmp_path: Path) -> None:
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        return httpx.Response(413, json={"detail": "batch too large"})

    client, _ = make_client(handler)
    with pytest.raises(WorkerRejected) as caught:
        await client.ocr([_jpeg(tmp_path)])
    assert len(attempts) == 1
    assert "batch too large" in str(caught.value)


async def test_a_connect_failure_becomes_unavailable_not_a_crash(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    client, _ = make_client(handler, retries=1)
    with pytest.raises(WorkerUnavailable):
        await client.transcribe(_wav(tmp_path))


async def test_healthy_is_a_boolean_never_an_exception() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("nothing there")

    client, _ = make_client(handler)
    assert await client.healthy() is False


# ------------------------------------------------------------------- uploads


async def test_a_retried_upload_resends_the_whole_file(tmp_path: Path) -> None:
    """Handles are opened per attempt: a spent file object uploads nothing, and
    an empty second attempt would look like a worker bug for a week."""
    bodies: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(request.content)
        if len(bodies) == 1:
            return httpx.Response(503, headers={"Retry-After": "0"})
        return httpx.Response(200, json={"segments": [], "model": "whisperx-large-v3"})

    client, _ = make_client(handler)
    audio = _wav(tmp_path, b"RIFF" + b"\x00" * 4096)
    await client.transcribe(audio, language="en", model="whisperx-large-v3")
    assert len(bodies) == 2
    # Same bytes both times (only the random multipart boundary differs), so
    # the second attempt really did start from byte zero.
    assert len(bodies[0]) == len(bodies[1])
    assert bodies[1].count(b"\x00" * 4096) == 1
    assert b"RIFF" in bodies[1]


async def test_transcription_asks_for_verbose_json_with_word_timestamps(tmp_path: Path) -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = request.content.decode("latin-1")
        seen["path"] = request.url.path
        return httpx.Response(200, json={"segments": []})

    client, _ = make_client(handler)
    await client.transcribe(_wav(tmp_path), language="en")
    assert seen["path"] == "/v1/audio/transcriptions"
    assert "verbose_json" in seen["body"]
    # OpenAI's field name keeps its brackets; the worker's schema spells it so.
    assert "timestamp_granularities[]" in seen["body"]
    assert "word" in seen["body"] and "segment" in seen["body"]


async def test_ocr_results_come_back_aligned_with_the_frames_sent(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "object": "list",
                "model": "rapidocr-v2",
                # Deliberately out of order: `index` is authoritative, not
                # position in the array.
                "data": [
                    {"index": 1, "items": [{"text": "second", "confidence": 0.8}]},
                    {
                        "index": 0,
                        "items": [
                            {"text": "first", "confidence": 0.9, "bbox": [1.0, 2.0, 3.0, 4.0]}
                        ],
                    },
                ],
            },
        )

    client, _ = make_client(handler)
    results, model = await client.ocr([_jpeg(tmp_path, "a"), _jpeg(tmp_path, "b")])
    assert model == "rapidocr-v2"
    assert [line.text for page in results for line in page] == ["first", "second"]
    assert results[0][0].bbox == (1.0, 2.0, 3.0, 4.0)
    assert results[1][0].bbox is None


async def test_an_empty_batch_never_reaches_the_wire() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("should not have been called")

    client, _ = make_client(handler)
    assert await client.ocr([]) == ([], None)
    assert await client.embed_images([]) == ([], None, None)


# --------------------------------------------------------------- cardinality


async def test_ocr_missing_a_page_is_a_failure_not_an_empty_page(tmp_path: Path) -> None:
    """Seven results for eight images used to write `ocr_state='empty'` on the
    eighth — a real answer, recorded as read, with the stage marked done. No
    resume would ever repair it."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": i, "items": [{"text": "hello", "confidence": 0.9}]}
                    for i in range(7)
                ],
                "model": "rapidocr-default",
            },
        )

    client, _ = make_client(handler, retries=0)
    images = [_jpeg(tmp_path, f"frame{i}") for i in range(8)]
    with pytest.raises(WorkerUnavailable) as caught:
        await client.ocr(images)
    assert "7 result(s) for 8 image(s)" in str(caught.value)


async def test_a_duplicated_or_out_of_range_index_is_refused(tmp_path: Path) -> None:
    for data in (
        [{"index": 0, "items": []}, {"index": 0, "items": []}],  # duplicate
        [{"index": 0, "items": []}, {"index": 9, "items": []}],  # out of range
        [{"index": 0, "items": []}, {"items": []}],  # no index at all
    ):

        def handler(request: httpx.Request, data=data) -> httpx.Response:
            return httpx.Response(200, json={"data": data, "model": "rapidocr-default"})

        client, _ = make_client(handler, retries=0)
        with pytest.raises(WorkerUnavailable):
            await client.ocr([_jpeg(tmp_path, "a"), _jpeg(tmp_path, "b")])


async def test_a_page_with_no_text_is_still_a_valid_result(tmp_path: Path) -> None:
    """`items` is optional in the worker's schema — `index` is what is required."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"data": [{"index": 0}, {"index": 1, "items": []}], "model": "rapidocr-default"},
        )

    client, _ = make_client(handler)
    results, model = await client.ocr([_jpeg(tmp_path, "a"), _jpeg(tmp_path, "b")])
    assert results == [[], []]
    assert model == "rapidocr-default"


async def test_short_embedding_responses_are_refused_rather_than_written(
    tmp_path: Path,
) -> None:
    """Seven vectors for eight frames wrote seven rows through a non-strict zip
    and marked the stage done. Zero vectors wrote nothing, and still did."""
    for count in (0, 7):

        def handler(request: httpx.Request, count=count) -> httpx.Response:
            return httpx.Response(200, json=embeddings_body(dim=1152, count=count))

        client, _ = make_client(handler, retries=0)
        with pytest.raises(WorkerUnavailable) as caught:
            await client.embed_images([_jpeg(tmp_path, f"frame{i}") for i in range(8)])
        assert "8 image(s)" in str(caught.value)


async def test_a_batch_that_mixes_vector_widths_is_refused() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": "Qwen/Qwen3-Embedding-0.6B",
                "dimensions": 1024,
                "data": [
                    {"index": 0, "embedding": [0.1] * 1024},
                    {"index": 1, "embedding": [0.1] * 512},
                ],
            },
        )

    client, _ = make_client(handler, retries=0)
    with pytest.raises(WorkerUnavailable) as caught:
        await client.embed(["a", "b"], input_type="document")
    assert "mixes vector widths" in str(caught.value)


async def test_a_header_that_disagrees_with_the_bytes_is_refused() -> None:
    """`_dimension_mismatch` checks the header against the corpus. This checks
    the header against what actually arrived."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "model": "Qwen/Qwen3-Embedding-0.6B",
                "dimensions": 1024,
                "data": [{"index": 0, "embedding": [0.1] * 512}],
            },
        )

    client, _ = make_client(handler, retries=0)
    with pytest.raises(WorkerUnavailable) as caught:
        await client.embed(["a"], input_type="document")
    assert "1024-d but the vectors are 512-d" in str(caught.value)


# ------------------------------------------------------- the indexing budget


async def test_document_embeddings_get_the_retry_loop_and_the_long_budget() -> None:
    """`embed` was the one method not overridden, so `text_embed` inherited the
    20 s query timeout with no retry and no `Retry-After`. A cold Qwen3 load is
    7.8-19.2 s on the reference box, so the model loading failed the job."""
    calls: list[str] = []
    timeouts: list[Any] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        timeouts.append(request.extensions.get("timeout"))
        if len(calls) == 1:
            return httpx.Response(503, headers={"Retry-After": "9"}, json={"detail": "loading"})
        return httpx.Response(200, json=embeddings_body(dim=1024))

    client, slept = make_client(handler, op_timeout_s=120.0)
    vectors, model, dims = await client.embed(["a chunk of transcript"], input_type="document")

    assert calls == ["/v1/embeddings", "/v1/embeddings"]  # it waited and retried
    assert slept == [9.0]  # exactly what the worker asked for
    assert (len(vectors), dims) == (1, 1024)
    read = (timeouts[0] or {}).get("read")
    assert read is None or read > 20.0, "still on the query budget"


async def test_a_query_embedding_still_fails_fast_instead_of_waiting() -> None:
    """The other half: a search would rather answer FTS-only than wait out a
    model load, and the two callers share one client object."""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(503, headers={"Retry-After": "30"}, json={"detail": "loading"})

    client, slept = make_client(handler)
    with pytest.raises(EmbeddingUnavailable):
        await client.embed(["a query"], input_type="query")
    assert len(calls) == 1
    assert slept == []


async def test_an_empty_document_batch_never_reaches_the_wire() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("should not have been called")

    client, _ = make_client(handler)
    assert await client.embed([], input_type="document") == ([], None, None)


# --------------------------------------------------------------- frame query


async def test_frame_query_posts_to_the_assumed_sibling_endpoint() -> None:
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["json"] = request.content.decode()
        return httpx.Response(200, json=embeddings_body(count=1))

    client, _ = make_client(handler)
    vectors, model, dims = await client.embed_frame_query(
        ["a terminal with nvidia-smi"], model="siglip2-so400m-patch16-naflex"
    )
    assert seen["path"] == FRAME_QUERY_PATH
    assert "nvidia-smi" in seen["json"]
    assert dims == 1152 and len(vectors[0]) == 1152 and model


async def test_a_worker_without_the_endpoint_degrades_instead_of_erroring() -> None:
    """A 404 is the *point* of assuming a path rather than a field: an unknown
    field would be ignored and we would write text vectors into the frame index."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "Not Found"})

    client, _ = make_client(handler)
    with pytest.raises(WorkerUnavailable) as caught:
        await client.embed_frame_query(["anything"])
    assert "text->frame-space" in str(caught.value)


# ----------------------------------------------------------------- utilities


def _jpeg(tmp_path: Path, name: str = "frame") -> Path:
    path = tmp_path / f"{name}.jpg"
    path.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 32 + b"\xff\xd9")
    return path


def _wav(tmp_path: Path, payload: bytes = b"RIFF0000WAVE") -> Path:
    path = tmp_path / "audio.wav"
    path.write_bytes(payload)
    return path
