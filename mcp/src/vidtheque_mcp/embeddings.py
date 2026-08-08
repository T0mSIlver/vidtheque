"""The worker-facing embedding client.

CLAUDE.md invariant: **mcp/ <-> worker/ is HTTP only.** No Python import ever
crosses the boundary, not even in tests — which is why this is a tiny interface
with a fake in the test suite rather than a shared module.

The contract is the worker's OpenAPI document: ``POST /v1/embeddings`` takes
``{input, model?, encoding_format}`` and returns ``{data: [{index, embedding}],
model, dimensions}``. ``model`` and ``dimensions`` on the response are the
authoritative anti-drift check (index-schema §1.1) — they describe the vector we
are about to compare against stored ones.
"""

from __future__ import annotations

import asyncio
from typing import Protocol, Sequence

import httpx2 as httpx


class EmbeddingUnavailable(RuntimeError):
    """The worker could not be reached or refused. Vector legs are skipped."""


class EmbeddingClient(Protocol):
    """The seam. Faked in tests; never imported from the worker package."""

    async def embed(
        self, texts: Sequence[str], model: str | None = None
    ) -> tuple[list[list[float]], str | None, int | None]:
        """Return (vectors, model_id, dimensions).

        ``model`` selects the encoder: the transcript leg asks for the text
        model from ``config``, the frame leg asks for the SigLIP tower so the
        query lands in the *same* embedding space as the stored frame vectors.
        """
        ...

    async def aclose(self) -> None: ...


class HTTPEmbeddingClient:
    """OpenAI-compatible client against ``WORKER_URL``."""

    def __init__(self, base_url: str, model: str | None = None, timeout_s: float = 20.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout_s
        self._client: httpx.AsyncClient | None = None
        self._lock = asyncio.Lock()

    async def _http(self) -> httpx.AsyncClient:
        async with self._lock:
            if self._client is None:
                self._client = httpx.AsyncClient(base_url=self._base_url, timeout=self._timeout)
            return self._client

    async def embed(
        self, texts: Sequence[str], model: str | None = None
    ) -> tuple[list[list[float]], str | None, int | None]:
        payload: dict[str, object] = {"input": list(texts), "encoding_format": "float"}
        chosen = model or self._model
        if chosen:
            payload["model"] = chosen
        try:
            client = await self._http()
            response = await client.post("/v1/embeddings", json=payload)
            response.raise_for_status()
            body = response.json()
        except Exception as exc:  # httpx errors, JSON errors, non-2xx
            raise EmbeddingUnavailable(str(exc)) from exc
        items = sorted(body.get("data", []), key=lambda d: d.get("index", 0))
        vectors = [list(item["embedding"]) for item in items]
        return vectors, body.get("model"), body.get("dimensions")

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None


class NullEmbeddingClient:
    """Used when the corpus has no vectors or the operator disabled the worker."""

    async def embed(
        self, texts: Sequence[str], model: str | None = None
    ) -> tuple[list[list[float]], str | None, int | None]:
        raise EmbeddingUnavailable("no embedding worker is configured")

    async def aclose(self) -> None:  # pragma: no cover - trivial
        return None
