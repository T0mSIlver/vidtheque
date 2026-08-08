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

from ..embeddings import EmbeddingUnavailable, HTTPEmbeddingClient

logger = logging.getLogger(__name__)

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
        self, audio: Path, *, language: str | None = None, model: str | None = None
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
    ) -> None:
        # `timeout_s` stays the *query* budget it is on the parent — a search
        # would rather answer FTS-only than wait two minutes. Indexing calls
        # pass their own, much longer, per-operation budget.
        super().__init__(base_url, model=model, timeout_s=timeout_s)
        self._op_timeout = op_timeout_s
        self._stt_timeout = stt_timeout_s
        self._retries = max(0, retries)
        self._retry_max_wait = retry_max_wait_s
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
    ) -> dict[str, Any]:
        """POST with the worker's backpressure honoured.

        ``files`` is a *factory*, not a list: each attempt opens its own
        handles, because a retried multipart body has to start from byte zero
        and a spent file object silently uploads nothing.
        """
        client = await self._http()
        attempt = 0
        last: Exception | None = None
        while attempt <= self._retries:
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
                if attempt >= self._retries:
                    raise WorkerUnavailable(
                        f"{path}: {response.status_code} after {attempt + 1} attempts ({detail})"
                    )
                await self._backoff(attempt, response.headers.get("retry-after"), detail)
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
        raise WorkerUnavailable(f"{path}: {last}")  # pragma: no cover - loop always returns

    async def _backoff(self, attempt: int, retry_after: str | None, why: str) -> None:
        """Retry-After when the worker names one, exponential when it does not."""
        delay = _parse_retry_after(retry_after)
        if delay is None:
            delay = min(2.0 * (2**attempt), self._retry_max_wait)
            delay += random.uniform(0, 0.5)  # nothing else is racing, but still
        delay = min(delay, self._retry_max_wait)
        logger.info("worker asked us to wait %.1fs (attempt %d): %s", delay, attempt + 1, why)
        await self._sleep(delay)

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
        self, audio: Path, *, language: str | None = None, model: str | None = None
    ) -> dict[str, Any]:
        """``POST /v1/audio/transcriptions``, verbose_json, word granularity.

        ``timestamp_granularities[]`` keeps the literal brackets: it is
        OpenAI's form-field name, and the worker's schema spells it that way.
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
            timeout_s=self._stt_timeout,
        )

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
        results: list[list[OcrLine]] = [[] for _ in images]
        for entry in body.get("data", []):
            index = int(entry.get("index", 0))
            if not 0 <= index < len(results):
                continue
            results[index] = [_ocr_line(item) for item in entry.get("items", [])]
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
        return _vectors(body)

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
        return _vectors(body)


def _vectors(body: dict[str, Any]) -> tuple[list[list[float]], str | None, int | None]:
    items = sorted(body.get("data", []), key=lambda d: d.get("index", 0))
    vectors = [list(item["embedding"]) for item in items]
    return vectors, body.get("model"), body.get("dimensions")


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
