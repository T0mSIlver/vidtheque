"""Rate limiting for the public surface — demo-site.md §4.

Two shapes, because the two things being guarded are different:

* **Token buckets, in memory**, for the per-minute limits. Single-process
  deliberately: vidtheque is one uvicorn process holding one SQLite writer, so
  there is no second replica for a shared counter to be shared with. If a
  replica ever appears, this is the first thing that needs a shared store — and
  the writer would have to move first, which is the bigger change. A redeploy
  resets them, which costs nothing: a minute bucket guards against hammering,
  and nobody hammers across a restart they did not know happened.
* **A UTC-day counter, written to SQLite**, for the daily `ask` budget. That one
  guards *money*, and an in-memory 50/day resets on every deploy — which on a
  launch day is several times an hour. Tom's decision, 2026-08-09 evening. See
  :class:`SqliteBudgetStore` for the split between the cache and the record, and
  migration 0005 for why a counter rather than a serialised bucket.

Written as a plain ASGI middleware rather than Starlette's
``BaseHTTPMiddleware``: the app's root has ``Mount("/", mcp_app)`` under it, and
wrapping a streaming transport in a request/response middleware is how you break
SSE. Non-matching paths are passed straight through, untouched.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import math
import sqlite3
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Iterable, Protocol

from starlette.types import ASGIApp, Message, Receive, Scope, Send

if TYPE_CHECKING:  # pragma: no cover - typing only, and `db` must not import us
    from ..db import Database

logger = logging.getLogger(__name__)

# A limit whose window is at least this long is a *daily budget*, not a rate:
# it is counted per UTC day and it is written down. Nothing else about the
# limiter branches on the bucket's name.
DAILY_WINDOW_S = 86_400.0


def utc_day(now: float | None = None) -> str:
    """The UTC date a charge belongs to, as ``YYYY-MM-DD``.

    A text date rather than an epoch day: it is the key in the table, and a
    budget row somebody reads in ``sqlite3`` at 3am should say which day it is.
    """
    return time.strftime("%Y-%m-%d", time.gmtime(time.time() if now is None else now))


def until_utc_midnight(now: float | None = None) -> float:
    """Seconds until the day's budget resets. Unix time is UTC-midnight aligned."""
    now = time.time() if now is None else now
    return DAILY_WINDOW_S - (now % DAILY_WINDOW_S)


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


@dataclass
class DailyCounter:
    """One ``(bucket, client)`` pair's spend for one UTC day.

    A counter rather than a refilling bucket, because this is the half that has
    to be writable down. ``Bucket`` refills from ``time.monotonic()``, which has
    no meaning across a process boundary; "how much of today has been spent" has
    exactly one honest value and a restart can read it back.

    The counter never rolls its own day: :meth:`RateLimiter._counter` replaces it
    when the date changes, so a stale one can never be asked to decide anything.
    """

    capacity: int
    day: str
    spent: int = 0

    def take(self) -> bool:
        if self.spent >= self.capacity:
            return False
        self.spent += 1
        return True

    def give_back(self) -> None:
        """Floored at zero, so a refund can never mint budget — the same rule
        the bucket enforces by capping a refill at capacity."""
        self.spent = max(0, self.spent - 1)

    @property
    def remaining(self) -> int:
        return max(0, self.capacity - self.spent)


class BudgetStore(Protocol):
    """The durable record behind the daily counters.

    Deliberately two methods with two different rhythms: ``spent`` is a
    synchronous cache lookup on the decision path, ``record`` is fire-and-file.
    Neither may block a request on the database.
    """

    def spent(self, bucket: str, client: str, day: str) -> int:
        """What that day's row already says — 0 for a day with no row yet."""

    def record(self, bucket: str, client: str, day: str, delta: int) -> None:
        """Note a charge (`+1`) or a refund (`-1`) against that day."""


# A month of rows: enough for the operator to answer "what did the demo cost in
# July", small enough that nothing ever needs an index beyond the primary key.
BUDGET_KEEP_DAYS = 30

_BUDGET_UPSERT = """
INSERT INTO ask_budget (bucket, client, day, spent, updated_at)
VALUES (:bucket, :client, :day, max(0, :delta), unixepoch())
ON CONFLICT(bucket, client, day) DO UPDATE SET
  spent      = max(0, ask_budget.spent + :delta),
  updated_at = unixepoch()
"""


class SqliteBudgetStore:
    """The daily budget on disk (index-schema §1.11), behind the memory cache.

    The split is the whole design:

    * **Reads happen once, at boot.** :meth:`open` loads today's rows into a
      dict and prunes anything older than ``BUDGET_KEEP_DAYS``. After that the
      limiter never touches SQLite to *decide* anything — the in-memory counter
      is still the fast path, exactly as it was before this existed. SQLite is
      the record, not the gate.
    * **Writes happen behind the request.** ``record`` is called from a
      synchronous ASGI middleware, and the mcp process owns exactly one write
      connection guarded by an ``asyncio.Lock`` (index-schema §5). So a delta is
      queued and applied by one drain task. The visitor never waits on the
      writer, and — the case that made this a queue rather than a task per
      delta — a refund issued from the ``finally`` of a *cancelled* stream needs
      somewhere to put a delta that is not "await something".

    Deltas are `+1` and `-1` applied additively, so their order does not matter
    and a lost one costs at most a single ask. :meth:`close` drains before the
    database closes, so an orderly shutdown loses nothing at all; a `kill -9`
    can lose whatever was in flight, which is one ask, not the day.
    """

    def __init__(self, db: "Database") -> None:
        self._db = db
        self._cache: dict[tuple[str, str, str], int] = {}
        self._queue: asyncio.Queue[tuple[str, str, str, int]] = asyncio.Queue()
        self._task: asyncio.Task[None] | None = None

    # ------------------------------------------------------------ lifecycle

    async def open(self) -> None:
        """Load today, forget last month, and start the drain."""
        day = utc_day()
        cutoff = time.strftime(
            "%Y-%m-%d", time.gmtime(time.time() - BUDGET_KEEP_DAYS * DAILY_WINDOW_S)
        )

        def load(conn: sqlite3.Connection) -> list[sqlite3.Row]:
            conn.execute("DELETE FROM ask_budget WHERE day < ?", (cutoff,))
            return list(
                conn.execute(
                    "SELECT bucket, client, spent FROM ask_budget WHERE day = ?", (day,)
                )
            )

        # One BEGIN IMMEDIATE for both: a read-then-write on the writer is
        # exactly what that transaction shape exists for (index-schema §5).
        rows = await self._db.write(load)
        self._cache = {
            (str(row["bucket"]), str(row["client"]), day): int(row["spent"]) for row in rows
        }
        if self._cache:
            logger.info("ask budget: resumed %s for %s", dict(self._cache), day)
        self._task = asyncio.create_task(self._drain())

    async def close(self) -> None:
        if self._task is None:
            return
        task, self._task = self._task, None
        try:
            await asyncio.wait_for(self._queue.join(), timeout=5.0)
        except (asyncio.TimeoutError, TimeoutError):  # pragma: no cover - defensive
            logger.warning("ask budget: shutting down with deltas unwritten")
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    # ---------------------------------------------------------- BudgetStore

    def spent(self, bucket: str, client: str, day: str) -> int:
        return self._cache.get((bucket, client, day), 0)

    def record(self, bucket: str, client: str, day: str, delta: int) -> None:
        key = (bucket, client, day)
        # Kept in step with the row, so a counter rebuilt within the same day
        # (a sweep, a new process-wide capacity) re-seeds from the truth rather
        # than from zero.
        self._cache[key] = max(0, self._cache.get(key, 0) + delta)
        self._queue.put_nowait((bucket, client, day, delta))

    # --------------------------------------------------------------- drain

    async def _drain(self) -> None:
        while True:
            item = await self._queue.get()
            try:
                await self._apply(item)
            except asyncio.CancelledError:  # pragma: no cover - shutdown
                raise
            except Exception:
                # A budget that cannot be written is not a reason to refuse a
                # visitor: the in-memory counter is still enforcing the cap for
                # the life of this process, and it is the restart that degrades.
                logger.warning("ask budget: could not record %s", item, exc_info=True)
            finally:
                self._queue.task_done()

    async def _apply(self, item: tuple[str, str, str, int]) -> None:
        bucket, client, day, delta = item
        params = {"bucket": bucket, "client": client, "day": day, "delta": delta}
        await self._db.write(lambda conn: conn.execute(_BUDGET_UPSERT, params))


class RateLimiter:
    """Named buckets, keyed by ``(bucket, client)``.

    A limit with a window of a day or more is a :class:`DailyCounter` instead of
    a :class:`Bucket` — same key, same four-tuple answer, different maths and a
    durable record behind it.
    """

    def __init__(
        self,
        limits: dict[str, tuple[int, float]],
        max_keys: int = 10_000,
        budget: BudgetStore | None = None,
    ) -> None:
        self._limits = limits
        self._max_keys = max_keys
        self._budget = budget
        self._buckets: dict[tuple[str, str], Bucket] = {}
        self._counters: dict[tuple[str, str], DailyCounter] = {}

    def limit(self, name: str) -> tuple[int, float] | None:
        return self._limits.get(name)

    def is_daily(self, name: str) -> bool:
        limit = self._limits.get(name)
        return limit is not None and limit[1] >= DAILY_WINDOW_S

    def check(self, name: str, client: str, day: str | None = None) -> tuple[bool, float, int, int]:
        """``(allowed, retry_after_s, limit, remaining)`` for one charge.

        ``day`` is passed in by the middleware so that the charge and its
        eventual refund name the same UTC day even if the request straddles
        midnight; on its own it defaults to now.
        """
        limit = self._limits.get(name)
        if limit is None:
            return True, 0.0, 0, 0
        capacity, window = limit
        if window >= DAILY_WINDOW_S:
            return self._check_daily(name, client, capacity, day or utc_day())
        key = (name, client)
        bucket = self._buckets.get(key)
        if bucket is None:
            self._sweep()
            bucket = self._buckets[key] = Bucket(capacity, window)
        wait = bucket.take()
        return wait == 0.0, wait, capacity, bucket.remaining

    def refund(self, name: str, client: str, day: str | None = None) -> None:
        """Give back one charge, if that bucket still exists."""
        if self.is_daily(name):
            self._refund_daily(name, client, day or utc_day())
            return
        bucket = self._buckets.get((name, client))
        if bucket is not None:
            bucket.give_back()

    # ---------------------------------------------------------- daily half

    def _check_daily(
        self, name: str, client: str, capacity: int, day: str
    ) -> tuple[bool, float, int, int]:
        counter = self._counter(name, client, capacity, day)
        if not counter.take():
            # The honest answer to "when will this work" for a day-keyed budget
            # is the rollover, not a trickle. It can be hours; saying less would
            # be inviting the retry the 429 exists to stop.
            return False, until_utc_midnight(), capacity, 0
        if self._budget is not None:
            self._budget.record(name, client, day, 1)
        return True, 0.0, capacity, counter.remaining

    def _refund_daily(self, name: str, client: str, day: str) -> None:
        counter = self._counters.get((name, client))
        if counter is not None and counter.day == day:
            counter.give_back()
        # If the day rolled over while the request was in flight, the live
        # counter is a cache of *today* and must not absorb yesterday's refund —
        # only yesterday's row is credited, and it is already spent, so nobody
        # gains a token that never existed.
        if self._budget is not None:
            self._budget.record(name, client, day, -1)

    def _counter(self, name: str, client: str, capacity: int, day: str) -> DailyCounter:
        key = (name, client)
        counter = self._counters.get(key)
        if counter is not None and counter.day == day:
            return counter
        # A restart resumes the day from the durable row; a new day starts at
        # zero, which is exactly what an absent row already says.
        spent = self._budget.spent(name, client, day) if self._budget is not None else 0
        counter = self._counters[key] = DailyCounter(capacity, day, spent)
        for stale, other in list(self._counters.items()):
            if other.day != day:
                del self._counters[stale]
        return counter

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
    limiter, entries, day = charged
    wanted = set(names)
    for name, key in entries:
        if name in wanted:
            # The day the charge was made on, carried rather than recomputed: a
            # ninety-second ask that starts at 23:59:30 must give its token back
            # to the day it took it from.
            limiter.refund(name, key, day)


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
        # One reading of the clock for the whole request, so every charge and
        # every refund it ever produces name the same UTC day.
        day = utc_day()
        charged: list[tuple[str, str]] = []
        for name, key in [(bucket, client), *((b, "@global") for b in self.extra_buckets(path))]:
            allowed, wait, limit, remaining = self.limiter.check(name, key, day)
            if not allowed:
                # A refused request costs nothing anywhere: the buckets already
                # charged for it are handed back before the 429 goes out.
                for done_name, done_key in charged:
                    self.limiter.refund(done_name, done_key, day)
                await _refused(send, name, limit, wait)
                return
            charged.append((name, key))
        scope[CHARGES_SCOPE_KEY] = (self.limiter, tuple(charged), day)
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
