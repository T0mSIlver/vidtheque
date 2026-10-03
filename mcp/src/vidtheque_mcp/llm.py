"""The model client every LLM caller shares — `docs/design/companion.md` §4.

One OpenAI-compatible chat client, moved here from the demo's ask so the
companion can reuse it. The demo keeps its own key, URL and budget
(``public/ask.py``'s ``OpenRouter``); nothing in this module reads the demo's
settings.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx2 as httpx

logger = logging.getLogger(__name__)


class LLMUnavailable(Exception):
    """The model cannot serve this request. Carries a reason, never a body."""

    def __init__(self, reason: str, retry_after_s: int = 60) -> None:
        super().__init__(reason)
        self.reason = reason
        self.retry_after_s = retry_after_s


@dataclass
class Billing:
    """Did this ask already buy upstream work? One flag, per request (§4.4).

    The daily budget is refunded for an ask that cost nothing, and *cost* is
    the only honest test. "Did the visitor get an answer" is not the same
    question, and using it was a way to get paid completions for free: the
    first `activity` event is emitted only after a completion came back with a
    tool call in it, so a client that waits for that line, disconnects, and
    repeats spends the model's tokens while the ``finally`` hands the day's
    token back every time. Reported by the 2026-08-09 review.

    So the flag is set by :meth:`ChatClient.chat` **before it dispatches**,
    not after the provider answers. That ordering is the whole point, and the
    2026-08-10 audit (B-3) is why it changed: setting it afterwards left a
    window the length of a completion in which the flag was still false. A
    visitor who disconnects inside that window cancels the task; `CancelledError`
    derives from `BaseException`, so the `except Exception` below never sees it,
    the assignment never runs, and the ``finally`` refunds the day's token —
    while the provider has already generated and billed the answer. Repeat and
    the daily cap never moves.

    The honest question is not "did the provider answer" but "might this have
    been billed", and once the request is on the wire the answer is yes. So the
    flag goes up first and comes back down only where nothing can have been
    generated: a failure to connect at all, and an explicit refusal
    (401/403/429) which the provider answers without running a model. A read
    timeout, a 5xx and a cancellation all stay paid, because any of them can sit
    on top of a real generation.

    The cost of this direction is that a genuinely dead upstream can charge a
    visitor a token they did not get an answer from. That is the right way round
    for a budget that guards money: the alternative charged Tom instead.
    """

    paid: bool = False


class ChatClient:
    """The thinnest possible OpenAI-compatible chat client.

    Takes its ``httpx`` client from the caller so tests can hand it a
    ``MockTransport`` instead of the internet. ``key`` may be None for a local
    endpoint (llama.cpp) that wants no ``Authorization`` header.
    """

    def __init__(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        key: str | None,
        *,
        headers: dict[str, str] | None = None,
        request_cap_s: float = 60.0,
    ) -> None:
        self._client = client
        self._base_url = base_url.rstrip("/")
        self._key = key
        self._headers = headers or {}
        self._request_cap_s = request_cap_s

    async def chat(
        self, body: dict[str, Any], deadline: float, billing: Billing | None = None
    ) -> dict[str, Any]:
        """POST ``/chat/completions`` and return the parsed response."""
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise LLMUnavailable("upstream_unavailable")
        headers = dict(self._headers)
        if self._key:
            headers["Authorization"] = f"Bearer {self._key}"
        # Whatever this call was worth, it was already worth it before this
        # round: a later round must never be able to hand back an earlier one.
        was_paid = billing.paid if billing is not None else False
        if billing is not None:
            billing.paid = True
        try:
            response = await self._client.post(
                f"{self._base_url}/chat/completions",
                json=body,
                headers=headers,
                timeout=min(remaining, self._request_cap_s),
            )
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            # Never reached the provider, so nothing can have been generated.
            # This is the narrow case the flag may be handed back for — it is
            # also the flapping-upstream case §4.4 exists to forgive.
            if billing is not None:
                billing.paid = was_paid
            logger.warning("llm: upstream unreachable: %s", type(exc).__name__)
            raise LLMUnavailable("upstream_unavailable") from None
        except Exception as exc:  # read timeout, TLS mid-stream, protocol error
            # The request was on the wire. It may have been generated and
            # billed, so the flag stays up. Cancellation does not land here at
            # all — CancelledError is a BaseException — which is precisely why
            # the flag had to go up before the await.
            logger.warning("llm: upstream request failed: %s", type(exc).__name__)
            raise LLMUnavailable("upstream_unavailable") from None

        if response.status_code >= 400:
            # A status line is proof the provider answered *instead of*
            # generating — a refusal (401/403/429), a rejected body (4xx), or a
            # flap (5xx). None of those ran a model, so the day's token goes
            # back. This is §4.4's original reason for existing: without it one
            # visitor retrying through an upstream flap burns the whole day for
            # everybody. It is also the line between this and a cancellation,
            # which produces no status at all and therefore stays charged.
            if billing is not None:
                billing.paid = was_paid

        if response.status_code in (401, 403):
            logger.warning("llm: upstream rejected the key (%s)", response.status_code)
            raise LLMUnavailable("upstream_rejected", retry_after_s=300)
        if response.status_code == 429:
            logger.warning("llm: upstream rate limited")
            raise LLMUnavailable("upstream_rate_limited")
        if response.status_code >= 400:
            # Status only. An upstream body is attacker-influenced text, and a
            # provider that echoes the request would copy the prepaid key
            # straight into this log line (2026-08-10 audit, F-8).
            logger.warning("llm: upstream returned %s", response.status_code)
            raise LLMUnavailable("upstream_unavailable")
        try:
            return response.json()
        except ValueError:
            logger.warning("llm: upstream returned a non-JSON body")
            raise LLMUnavailable("upstream_unavailable") from None
