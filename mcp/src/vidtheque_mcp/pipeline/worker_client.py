"""The worker seam for the indexing half.

``embeddings.py`` owns the query-time seam — one method, one shape. Indexing
needs three more endpoints and a much less forgiving failure model, so this
extends that client rather than forking it: ``HTTPWorkerClient`` *is* an
``EmbeddingClient``, so ``app.py`` can hand the same object to the tools and to
the pipeline.

Two behaviours the query path does not need:

* **Retry on 503 + Retry-After.** The worker emits exactly that under VRAM
  pressure — a lease it cannot take yet, or a model it must evict first. A
  query would rather answer FTS-only immediately; an indexing job has hours and
  should wait the number of seconds it was given.
* **Streaming uploads.** A 2-hour lecture's audio is tens of megabytes and a
  frame batch is tens of JPEGs. Handles are passed to httpx open, never
  ``read()`` into a bytes object — and re-opened per attempt, because a retried
  upload must rewind.

CLAUDE.md's boundary rule still holds: this file knows the worker's *HTTP*
contract (``worker/openapi.json``) and nothing else. No import ever crosses.
"""

from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol, Sequence, runtime_checkable

import httpx2 as httpx

from ..embeddings import EmbeddingUnavailable, HTTPEmbeddingClient

logger = logging.getLogger(__name__)

# Transport failures where the server provably never saw the request: nothing
# was executed, so replaying it costs a round trip. Everything else — a read
# timeout above all — happens *after* the body was accepted, and for an
# expensive non-idempotent call it may be running on the GPU right now.
CONNECT_FAILURES = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.PoolTimeout,
    httpx.UnsupportedProtocol,
    httpx.InvalidURL,
)

# The text tower of the frame model, exposed as its own path. See the note on
# `embed_frame_query` — this is the one assumption in the file.
FRAME_QUERY_PATH = "/v1/embeddings/frame-query"

RETRYABLE_STATUS = {429, 502, 503, 504}


class WorkerUnavailable(EmbeddingUnavailable):
    """The worker could not be reached, or gave up after its retries.

    Subclasses ``EmbeddingUnavailable`` so query-time callers, which already
    degrade to the lexical leg on that exception, need no new branch.
    """


class WorkerRejected(RuntimeError):
    """A 4xx that retrying cannot fix: a file the worker will not accept, a
    batch over its cap, a model it does not serve."""


@dataclass(frozen=True)
class OcrLine:
    text: str
    confidence: float | None
    bbox: tuple[float, float, float, float] | None


@runtime_checkable
class WorkerAPI(Protocol):
    """What the pipeline needs from the worker. Faked wholesale in tests."""

    async def healthy(self) -> bool: ...

    async def transcribe(
        self,
        audio: Path,
        *,
        language: str | None = None,
        model: str | None = None,
        duration_s: float | None = None,
    ) -> dict[str, Any]: ...

    async def ocr(
        self, images: Sequence[Path], *, min_confidence: float | None = None
    ) -> tuple[list[list[OcrLine]], str | None]: ...

    async def embed_images(
        self,
        images: Sequence[Path],
        *,
        model: str | None = None,
        max_num_patches: int | None = None,
    ) -> tuple[list[list[float]], str | None, int | None]: ...

    async def embed_frame_query(
        self, texts: Sequence[str], *, model: str | None = None
    ) -> tuple[list[list[float]], str | None, int | None]: ...


class HTTPWorkerClient(HTTPEmbeddingClient):
    """``EmbeddingClient`` plus transcription, OCR and image embeddings."""

    def __init__(
        self,
        base_url: str,
        model: str | None = None,
        timeout_s: float = 20.0,
        *,
        op_timeout_s: float = 120.0,
        stt_timeout_s: float = 1800.0,
        retries: int = 3,
        retry_max_wait_s: float = 120.0,
        retry_total_s: float = 1800.0,
        stt_realtime_factor: float = 2.0,
    ) -> None:
        # `timeout_s` stays the *query* budget it is on the parent — a search
        # would rather answer FTS-only than wait two minutes. Indexing calls
        # pass their own, much longer, per-operation budget.
        super().__init__(base_url, model=model, timeout_s=timeout_s)
        self._op_timeout = op_timeout_s
        self._stt_timeout = stt_timeout_s
        self._retries = max(0, retries)
        self._retry_max_wait = retry_max_wait_s
        self._retry_total = max(0.0, retry_total_s)
        self._stt_realtime_factor = max(0.0, stt_realtime_factor)
        self._sleep = asyncio.sleep  # swapped in tests

    # ------------------------------------------------------------- transport

    async def _send(
        self,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        files: Callable[[], list[tuple[str, tuple[str, Any, str]]]] | None = None,
        data: dict[str, Any] | None = None,
        timeout_s: float | None = None,
        replay_after_acceptance: bool = True,
    ) -> dict[str, Any]:
        """POST with the worker's backpressure honoured.

        ``files`` is a *factory*, not a list: each attempt opens its own
        handles, because a retried multipart body has to start from byte zero
        and a spent file object silently uploads nothing.

        ``replay_after_acceptance=False`` says this call is expensive and not
        idempotent: a connect failure is still replayed (the server never saw
        it), but a read timeout is not, because the worker may be running the
        request right now and a retry would enqueue the same 70 minutes of GPU
        work a second time.

        Two budgets, on purpose. ``retries`` bounds *requests* — a worker that
        keeps dropping the connection is not going to start answering. The 503
        wait is bounded by ``retry_total_s`` of wall clock instead, because
        `Retry-After: 30` four times is 90 seconds, and "an indexing job has
        hours" was written about a loop that gave up in a minute and a half.
        """
        client = await self._http()
        attempt = 0
        waited = 0.0
        last: Exception | None = None
        while True:
            handles: list[Any] = []
            try:
                kwargs: dict[str, Any] = {"timeout": timeout_s or self._op_timeout}
                if json_body is not None:
                    kwargs["json"] = json_body
                if files is not None:
                    parts = files()
                    handles = [part[1][1] for part in parts]
                    kwargs["files"] = parts
                if data is not None:
                    kwargs["data"] = data
                response = await client.post(path, **kwargs)
            except Exception as exc:  # connect/read/timeout
                last = exc
                if not replay_after_acceptance and not isinstance(exc, CONNECT_FAILURES):
                    raise WorkerUnavailable(
                        f"{path}: {exc}. The request was accepted before it failed, so it "
                        "may still be running on the worker; it is not replayed. Raise "
                        "VIDTHEQUE_STT_TIMEOUT_S if this is a long recording."
                    ) from exc
                if attempt >= self._retries:
                    raise WorkerUnavailable(f"{path}: {exc}") from exc
                await self._backoff(attempt, None, str(exc))
                attempt += 1
                continue
            finally:
                for handle in handles:
                    close = getattr(handle, "close", None)
                    if callable(close):
                        close()

            if response.status_code in RETRYABLE_STATUS:
                detail = _detail(response)
                if waited >= self._retry_total:
                    raise WorkerUnavailable(
                        f"{path}: {response.status_code} for the whole "
                        f"{self._retry_total:.0f}s backpressure budget ({detail})"
                    )
                # Backpressure is the worker saying "not yet", which is not the
                # same as failing: it costs the wait budget, not an attempt.
                waited += await self._backoff(
                    attempt, response.headers.get("retry-after"), detail, floor=0.5
                )
                attempt += 1
                continue
            if response.status_code >= 400:
                raise WorkerRejected(f"{path}: {response.status_code} {_detail(response)}")
            try:
                body = response.json()
            except Exception as exc:
                raise WorkerUnavailable(f"{path}: response was not JSON ({exc})") from exc
            if not isinstance(body, dict):
                raise WorkerUnavailable(
                    f"{path}: expected a JSON object, got {type(body).__name__}"
                )
            return body

    async def _backoff(
        self, attempt: int, retry_after: str | None, why: str, floor: float = 0.0
    ) -> float:
        """Retry-After when the worker names one, exponential when it does not.

        Returns the seconds spent, which is what the backpressure budget counts
        — a wall clock read here would make the budget untestable and would not
        measure anything the clock does not already say. ``floor`` keeps a
        `Retry-After: 0` from turning that budget into a hot loop.
        """
        delay = _parse_retry_after(retry_after)
        if delay is None:
            # Capped exponent: the budget can allow many more waits than an int
            # can shift, and `2 ** 1024` is an OverflowError, not a long sleep.
            delay = min(2.0 * (2 ** min(attempt, 16)), self._retry_max_wait)
            delay += random.uniform(0, 0.5)  # nothing else is racing, but still
        delay = max(min(delay, self._retry_max_wait), floor)
        logger.info("worker asked us to wait %.1fs (attempt %d): %s", delay, attempt + 1, why)
        await self._sleep(delay)
        return delay

    # --------------------------------------------------------------- surface

    async def healthy(self) -> bool:
        """Cheap reachability probe. Decides whether the audio download is even
        worth doing, and whether the STT policy falls back to captions."""
        try:
            client = await self._http()
            response = await client.get("/healthz", timeout=5.0)
            return response.status_code < 400
        except Exception:
            return False

    async def transcribe(
        self,
        audio: Path,
        *,
        language: str | None = None,
        model: str | None = None,
        duration_s: float | None = None,
    ) -> dict[str, Any]:
        """``POST /v1/audio/transcriptions``, verbose_json, word granularity.

        ``timestamp_granularities[]`` keeps the literal brackets: it is
        OpenAI's form-field name, and the worker's schema spells it that way.

        The budget is sized from the recording, not from a constant: a flat
        1,800 s covers a conference talk and not a three-hour stream, and the
        timeout it produced was the *worst* possible failure here — the whole
        multipart body was replayed, up to three more times, each one enqueuing
        the same transcription again on a worker that was probably still running
        the first. Read timeouts are no longer replayed at all (the request was
        accepted), so the budget has to be right rather than recoverable.
        """
        data: dict[str, Any] = {
            "response_format": "verbose_json",
            "temperature": "0",
            "timestamp_granularities[]": ["segment", "word"],
        }
        if language:
            data["language"] = language
        if model:
            data["model"] = model
        return await self._send(
            "/v1/audio/transcriptions",
            files=lambda: [("file", (audio.name, audio.open("rb"), "application/octet-stream"))],
            data=data,
            timeout_s=self.stt_budget(duration_s),
            replay_after_acceptance=False,
        )

    def stt_budget(self, duration_s: float | None) -> float:
        """`VIDTHEQUE_STT_TIMEOUT_S` as a floor, plus room for the recording."""
        if not duration_s or duration_s <= 0:
            return self._stt_timeout
        return max(self._stt_timeout, float(duration_s) * self._stt_realtime_factor)

    async def embed(
        self, texts: Sequence[str], model: str | None = None, input_type: str = "query"
    ) -> tuple[list[list[float]], str | None, int | None]:
        """``POST /v1/embeddings``, on the *indexing* budget when it is indexing.

        This was the one method the class did not override, so `text_embed` —
        the only caller that passes ``input_type="document"`` — inherited
        ``HTTPEmbeddingClient.embed``: the 20-second *query* timeout, no retry
        loop, and no ``Retry-After`` handling. A cold Qwen3-Embedding load is
        7.8-19.2 s on the reference box (bench 2026-08-09 §6.1), so load plus
        the first batch crossed 20 s, the read timeout became
        `EmbeddingUnavailable`, and the job runner burned all three item
        attempts re-running `fetch` and dying in the same place. The same gap
        also lost the worker's 503 + `Retry-After` contract, so an
        `InsufficientVRAM` refusal on a busy card failed the stage outright.

        The switch is `input_type` because it already names the caller exactly:
        documents are only ever embedded by the pipeline, queries only ever by
        `search`, and a search would still rather answer FTS-only in 20 seconds
        than wait two minutes for a model to load.
        """
        if input_type != "document":
            return await super().embed(texts, model=model, input_type=input_type)
        if not texts:
            return [], None, None
        payload: dict[str, Any] = {
            "input": list(texts),
            "encoding_format": "float",
            "input_type": input_type,
        }
        chosen = model or self._model
        if chosen:
            payload["model"] = chosen
        body = await self._send("/v1/embeddings", json_body=payload)
        return _vectors(body, len(texts), "/v1/embeddings", "input")

    async def ocr(
        self, images: Sequence[Path], *, min_confidence: float | None = None
    ) -> tuple[list[list[OcrLine]], str | None]:
        """``POST /v1/ocr``. CPU on the worker side — no GPU lease involved, so
        this never contends with a transcription in flight."""
        if not images:
            return [], None
        data: dict[str, Any] = {}
        if min_confidence is not None:
            data["min_confidence"] = str(min_confidence)
        body = await self._send(
            "/v1/ocr",
            files=lambda: [("file", (path.name, path.open("rb"), "image/jpeg")) for path in images],
            data=data or None,
        )
        pages = _by_index(body.get("data", []), len(images), "/v1/ocr", "image")
        # An image with no text legitimately omits `items` — the worker's schema
        # requires `index`, not `items`. A *missing index* is the failure.
        results = [
            [_ocr_line(item) for item in (page.get("items") or [])] for page in pages
        ]
        return results, body.get("model") or body.get("backend")

    async def embed_images(
        self,
        images: Sequence[Path],
        *,
        model: str | None = None,
        max_num_patches: int | None = None,
    ) -> tuple[list[list[float]], str | None, int | None]:
        """``POST /v1/embeddings/image``. Vectors come back in upload order."""
        if not images:
            return [], None, None
        data: dict[str, Any] = {}
        if model:
            data["model"] = model
        if max_num_patches:
            data["max_num_patches"] = str(max_num_patches)
        body = await self._send(
            "/v1/embeddings/image",
            files=lambda: [("file", (path.name, path.open("rb"), "image/jpeg")) for path in images],
            data=data or None,
        )
        return _vectors(body, len(images), "/v1/embeddings/image", "image")

    async def embed_frame_query(
        self, texts: Sequence[str], *, model: str | None = None
    ) -> tuple[list[list[float]], str | None, int | None]:
        """Text into the **frame** vector space, via the frame model's text tower.

        This is the one endpoint this file assumes rather than reads off
        ``worker/openapi.json``: at the time of writing the worker exposes no
        text->frame-space route, which is deviation #5 in ``mcp/README.md`` and
        the reason ``search content_type=frame`` cannot answer.

        The assumed shape is a sibling path — ``POST /v1/embeddings/frame-query``
        with ``{"input": [...], "model": …}`` answering with the usual
        ``EmbeddingsResponse``. A sibling *path* rather than a ``space=frame``
        field on ``/v1/embeddings`` is the safer assumption in exactly one way
        that matters: an unknown field is ignored by a permissive server and we
        would write text-space vectors into the frame index, while an unknown
        path 404s. If the worker lands a different shape, this method is the
        only thing that changes.

        A 404 is reported as unavailability, not a hard error: the frame leg
        already knows how to print a `note:` and carry on.
        """
        if not texts:
            return [], None, None
        payload: dict[str, Any] = {"input": list(texts), "encoding_format": "float"}
        if model:
            payload["model"] = model
        try:
            body = await self._send(FRAME_QUERY_PATH, json_body=payload)
        except WorkerRejected as exc:
            raise WorkerUnavailable(
                f"this worker serves no text->frame-space endpoint ({exc})"
            ) from exc
        return _vectors(body, len(texts), FRAME_QUERY_PATH, "input")


def _by_index(
    data: Any, expected: int, path: str, noun: str
) -> list[dict[str, Any]]:
    """Exactly one response entry per input, indexed, in range, no duplicates.

    The old readers were forgiving in the one way that costs data: OCR
    initialised every page empty and dropped entries whose index it did not
    recognise, so seven results for eight images wrote a real `ocr_state` of
    `empty` on the eighth — and the stage still went `done`, so no resume would
    ever repair it. The embedding reader simply zipped whatever came back
    against the inputs, non-strict, so a short response wrote a prefix of the
    rows and called the stage done.

    A short, duplicated or out-of-range response is the worker misbehaving, and
    the honest answer is a retryable failure rather than a partial commit.
    """
    if not isinstance(data, list):
        raise WorkerUnavailable(f"{path}: expected a list of results, got {type(data).__name__}")
    found: dict[int, dict[str, Any]] = {}
    for entry in data:
        if not isinstance(entry, dict):
            raise WorkerUnavailable(f"{path}: a result entry was not an object")
        index = entry.get("index")
        if not isinstance(index, int) or isinstance(index, bool):
            raise WorkerUnavailable(f"{path}: a result carried no usable index ({index!r})")
        if not 0 <= index < expected:
            raise WorkerUnavailable(
                f"{path}: result index {index} is outside the {expected} {noun}(s) sent"
            )
        if index in found:
            raise WorkerUnavailable(f"{path}: {noun} {index} came back twice")
        found[index] = entry
    if len(found) != expected:
        missing = sorted(set(range(expected)) - set(found))[:5]
        raise WorkerUnavailable(
            f"{path}: {len(found)} result(s) for {expected} {noun}(s); "
            f"missing {noun}(s) {missing}"
        )
    return [found[i] for i in range(expected)]


def _vectors(
    body: dict[str, Any], expected: int, path: str, noun: str
) -> tuple[list[list[float]], str | None, int | None]:
    items = _by_index(body.get("data", []), expected, path, noun)
    vectors: list[list[float]] = []
    for position, item in enumerate(items):
        raw = item.get("embedding")
        if not isinstance(raw, (list, tuple)) or not raw:
            raise WorkerUnavailable(f"{path}: {noun} {position} came back with no embedding")
        vectors.append([float(value) for value in raw])
    widths = {len(vector) for vector in vectors}
    if len(widths) > 1:
        raise WorkerUnavailable(f"{path}: the batch mixes vector widths {sorted(widths)}")
    dimensions = body.get("dimensions")
    if isinstance(dimensions, int) and widths and int(dimensions) not in widths:
        # The header and the payload disagree about the space these live in.
        # `_dimension_mismatch` compares the header against the corpus; this
        # compares it against the bytes that arrived.
        raise WorkerUnavailable(
            f"{path}: the response says {dimensions}-d but the vectors are {widths.pop()}-d"
        )
    return vectors, body.get("model"), dimensions


def _ocr_line(item: dict[str, Any]) -> OcrLine:
    bbox = item.get("bbox")
    box: tuple[float, float, float, float] | None = None
    if isinstance(bbox, (list, tuple)) and len(bbox) == 4:
        box = (float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3]))
    confidence = item.get("confidence")
    return OcrLine(
        text=str(item.get("text") or ""),
        confidence=float(confidence) if isinstance(confidence, (int, float)) else None,
        bbox=box,
    )


def _detail(response: Any) -> str:
    try:
        body = response.json()
    except Exception:
        return (getattr(response, "text", "") or "")[:200]
    if isinstance(body, dict):
        return str(body.get("detail") or body)[:200]
    return str(body)[:200]


def _parse_retry_after(value: str | None) -> float | None:
    """Seconds form only. The HTTP-date form is legal and the worker never
    sends it; guessing at clock skew would be worse than the exponential."""
    if not value:
        return None
    try:
        seconds = float(value.strip())
    except ValueError:
        return None
    return max(0.0, seconds)
