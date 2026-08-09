"""Token-bucket rate limiting for the public surface — demo-site.md §4.

In-memory and single-process, deliberately: vidtheque is one uvicorn process
holding one SQLite writer, so there is no second replica for a shared counter to
be shared with. If a replica ever appears, this is the first thing that needs a
shared store — and the writer would have to move first, which is the bigger
change. The visible consequence is that a redeploy resets the daily `ask`
budget; for a guard on a free tier that is acceptable, for money it would not
be.

Written as a plain ASGI middleware rather than Starlette's
``BaseHTTPMiddleware``: the app's root has ``Mount("/", mcp_app)`` under it, and
wrapping a streaming transport in a request/response middleware is how you break
SSE. Non-matching paths are passed straight through, untouched.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Callable, Iterable

from starlette.types import ASGIApp, Message, Receive, Scope, Send


@dataclass
class Bucket:
    """Capacity `n`, refilled continuously at `n / window` tokens per second.

    Continuous refill rather than a fixed window for two reasons: a fixed
    window lets a client spend `2n` across a boundary, and a limited visitor
    should get their next request back in seconds rather than at the top of the
    minute. Capacity doubles as the burst allowance.
    """

    capacity: int
    window_s: float
    tokens: float = field(init=False)
    updated: float = field(init=False)

    def __post_init__(self) -> None:
        self.tokens = float(self.capacity)
        self.updated = time.monotonic()

    @property
    def rate(self) -> float:
        return self.capacity / self.window_s if self.window_s else 0.0

    def _refill(self, now: float) -> None:
        elapsed = max(0.0, now - self.updated)
        self.updated = now
        self.tokens = min(float(self.capacity), self.tokens + elapsed * self.rate)

    def take(self, now: float | None = None) -> float:
        """Spend one token. Returns 0.0 on success, else seconds until it works."""
        self._refill(time.monotonic() if now is None else now)
        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return 0.0
        rate = self.rate
        if rate <= 0:  # capacity 0 — the bucket is a hard "no"
            return float(self.window_s or 1.0)
        return (1.0 - self.tokens) / rate

    def give_back(self) -> None:
        """Return one token — a charge for something that never happened.

        Refilled first, so the credit lands on the current level rather than on
        a stale one, and capped like every other refill: a bucket can never
        hold more than its capacity, however many refunds arrive.
        """
        self._refill(time.monotonic())
        self.tokens = min(float(self.capacity), self.tokens + 1.0)

    @property
    def remaining(self) -> int:
        return int(self.tokens)

    @property
    def full(self) -> bool:
        return self.tokens >= self.capacity


class RateLimiter:
    """Named buckets, keyed by ``(bucket, client)``."""

    def __init__(self, limits: dict[str, tuple[int, float]], max_keys: int = 10_000) -> None:
        self._limits = limits
        self._max_keys = max_keys
        self._buckets: dict[tuple[str, str], Bucket] = {}

    def limit(self, name: str) -> tuple[int, float] | None:
        return self._limits.get(name)

    def check(self, name: str, client: str) -> tuple[bool, float, int, int]:
        """``(allowed, retry_after_s, limit, remaining)`` for one charge."""
        limit = self._limits.get(name)
        if limit is None:
            return True, 0.0, 0, 0
        capacity, window = limit
        key = (name, client)
        bucket = self._buckets.get(key)
        if bucket is None:
            self._sweep()
            bucket = self._buckets[key] = Bucket(capacity, window)
        wait = bucket.take()
        return wait == 0.0, wait, capacity, bucket.remaining

    def refund(self, name: str, client: str) -> None:
        """Give back one charge, if that bucket still exists."""
        bucket = self._buckets.get((name, client))
        if bucket is not None:
            bucket.give_back()

    def _sweep(self) -> None:
        """Drop full buckets first: a full bucket is a client we can forget.

        A bucket at capacity is indistinguishable from one that was never
        created, so evicting it costs a client nothing. Only if that is not
        enough do we drop partially-spent ones, oldest-touched first.
        """
        if len(self._buckets) < self._max_keys:
            return
        for key in [k for k, b in self._buckets.items() if b.full]:
            del self._buckets[key]
        if len(self._buckets) < self._max_keys:
            return
        stale = sorted(self._buckets.items(), key=lambda kv: kv[1].updated)
        for key, _ in stale[: len(stale) // 2]:
            del self._buckets[key]


def client_key(scope: Scope, trusted_header: str) -> str:
    """The client's identity for limiting purposes — demo-site.md §4.3."""
    if trusted_header:
        wanted = trusted_header.lower().encode("latin-1")
        for name, value in scope.get("headers") or ():
            if name == wanted:
                # A comma-joined chain (some proxies) — the first entry is the
                # client; the rest are hops we did not put there.
                candidate = value.decode("latin-1").split(",")[0].strip()
                if candidate:
                    return candidate
    client = scope.get("client")
    return client[0] if client else "unknown"


BucketFor = Callable[[str], str | None]

# Where the middleware leaves what it charged, so a handler downstream can hand
# a charge back. A scope key rather than an app attribute: the charge belongs to
# the request, and the handler must never be able to refund somebody else's.
CHARGES_SCOPE_KEY = "vidtheque.rate_charges"


def refund(scope: Scope, *names: str) -> None:
    """Give back this request's charge against `names`, if it was charged.

    The limiter charges before the handler runs — it has to, that is what makes
    it cheap — so a request that turns out to have cost nothing (an upstream
    that never answered, a body that was never valid) is refunded here rather
    than counted a second time somewhere else.
    """
    charged = scope.get(CHARGES_SCOPE_KEY)
    if not charged:
        return
    limiter, entries = charged
    wanted = set(names)
    for name, key in entries:
        if name in wanted:
            limiter.refund(name, key)


class RateLimitMiddleware:
    """Charge matching requests against a bucket, or answer 429."""

    def __init__(
        self,
        app: ASGIApp,
        limiter: RateLimiter,
        bucket_for: BucketFor,
        trusted_header: str = "CF-Connecting-IP",
        extra_buckets: Callable[[str], Iterable[str]] | None = None,
    ) -> None:
        self.app = app
        self.limiter = limiter
        self.bucket_for = bucket_for
        self.trusted_header = trusted_header
        # `/api/ask` is charged against its per-IP bucket *and* the global daily
        # one. Per-IP runs first, so one visitor cannot spend the day's budget
        # before being told to slow down.
        self.extra_buckets = extra_buckets or (lambda _path: ())

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        bucket = self.bucket_for(path)
        if bucket is None:
            await self.app(scope, receive, send)
            return

        client = client_key(scope, self.trusted_header)
        charged: list[tuple[str, str]] = []
        for name, key in [(bucket, client), *((b, "@global") for b in self.extra_buckets(path))]:
            allowed, wait, limit, remaining = self.limiter.check(name, key)
            if not allowed:
                # A refused request costs nothing anywhere: the buckets already
                # charged for it are handed back before the 429 goes out.
                for done_name, done_key in charged:
                    self.limiter.refund(done_name, done_key)
                await _refused(send, name, limit, wait)
                return
            charged.append((name, key))
        scope[CHARGES_SCOPE_KEY] = (self.limiter, tuple(charged))
        await self.app(scope, receive, send)


async def _refused(send: Send, bucket: str, limit: int, wait: float) -> None:
    retry_after = max(1, math.ceil(wait))
    window = "minute" if bucket != "ask_global" else "day"
    body = (
        '{"error":"E_RATE_LIMIT","message":"Too many requests — '
        f'{limit} per {window}.","retry_after_s":{retry_after},"bucket":"{bucket}"}}'
    ).encode("utf-8")
    message: Message = {
        "type": "http.response.start",
        "status": 429,
        "headers": [
            (b"content-type", b"application/json"),
            (b"retry-after", str(retry_after).encode("ascii")),
            (b"x-ratelimit-limit", str(limit).encode("ascii")),
            (b"x-ratelimit-remaining", b"0"),
            (b"content-length", str(len(body)).encode("ascii")),
        ],
    }
    await send(message)
    await send({"type": "http.response.body", "body": body})
