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
