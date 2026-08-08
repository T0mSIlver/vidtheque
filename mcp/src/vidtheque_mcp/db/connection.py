"""Connections, PRAGMAs, the read pool, the single writer, and cancellation.

index-schema §5. Two rules do most of the work:

* **One writer.** The mcp process owns exactly one write connection, guarded by
  an ``asyncio.Lock``. Readers are a small pool of ``mode=ro`` connections used
  from a thread executor.
* **Cancellation uses both mechanisms.** ``interrupt()`` is a documented no-op
  when no statement is running, so a deadline that fires between "request
  cancelled" and "statement begins" is silently lost. The progress handler has
  no such window. Measured: both abort a 6.0 s query at 100 ms; ``interrupt()``
  called *before* the statement started let it run the full 6.0 s.
"""

from __future__ import annotations

import asyncio
import contextlib
import sqlite3
import struct
import time
from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path
from typing import Any, TypeVar

import sqlite_vec

T = TypeVar("T")

# Measured: n=10_000 is noise-level overhead from Python (each call re-acquires
# the GIL) and still gives ~17,000 checks per second of query time.
PROGRESS_HANDLER_OPS = 10_000

_READ_PRAGMAS = (
    "PRAGMA foreign_keys = ON",
    "PRAGMA busy_timeout = 10000",
    "PRAGMA cache_size = -65536",
    "PRAGMA temp_store = MEMORY",
    "PRAGMA query_only = ON",
)

_WRITE_PRAGMAS = (
    "PRAGMA journal_mode = WAL",
    "PRAGMA synchronous = NORMAL",
    "PRAGMA foreign_keys = ON",
    "PRAGMA busy_timeout = 10000",
    "PRAGMA cache_size = -65536",
    "PRAGMA temp_store = MEMORY",
    "PRAGMA wal_autocheckpoint = 2000",
)


class QueryInterrupted(Exception):
    """A statement was stopped by the deadline or by an explicit cancel."""

    def __init__(self, deadline_expired: bool) -> None:
        super().__init__("interrupted")
        self.deadline_expired = deadline_expired


def phash_to_signed(value: int) -> int:
    """SQLite integers are signed 64-bit; a raw getrandbits(64) overflows."""
    return struct.unpack("<q", struct.pack("<Q", value & 0xFFFF_FFFF_FFFF_FFFF))[0]


def phash_hamming(a: int, b: int) -> int:
    """Hamming distance over two signed 64-bit perceptual hashes.

    Registered as a deterministic UDF. Only ever applied to an already-capped
    candidate set — never as a table-scan predicate.
    """
    if a is None or b is None:  # pragma: no cover - defensive
        return 64
    return bin((a ^ b) & 0xFFFF_FFFF_FFFF_FFFF).count("1")


def _load_vec(conn: sqlite3.Connection) -> None:
    conn.enable_load_extension(True)
    try:
        sqlite_vec.load(conn)
    finally:
        conn.enable_load_extension(False)


def _configure(conn: sqlite3.Connection, pragmas: tuple[str, ...]) -> None:
    conn.row_factory = sqlite3.Row
    for pragma in pragmas:
        conn.execute(pragma)
    conn.create_function("phash_hamming", 2, phash_hamming, deterministic=True)


def open_write_connection(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False)
    _load_vec(conn)
    _configure(conn, _WRITE_PRAGMAS)
    return conn


def open_read_connection(path: Path) -> sqlite3.Connection:
    uri = f"file:{path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, isolation_level=None, check_same_thread=False)
    _load_vec(conn)
    _configure(conn, _READ_PRAGMAS)
    return conn


class Cancellable:
    """One per read connection: deadline + external cancel, both honoured."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.deadline: float | None = None
        self.cancelled = False
        self._deadline_expired = False
        conn.set_progress_handler(self._tick, PROGRESS_HANDLER_OPS)

    def _tick(self) -> int:
        # Runs on the query thread; reads flags set by the event-loop thread.
        if self.cancelled:
            return 1
        if self.deadline is not None and time.monotonic() > self.deadline:
            self.cancelled = True
            self._deadline_expired = True
            return 1
        return 0

    def arm(self, budget_s: float | None) -> None:
        self.cancelled = False
        self._deadline_expired = False
        self.deadline = None if budget_s is None else time.monotonic() + budget_s

    def disarm(self) -> None:
        self.deadline = None
        self.cancelled = False
        self._deadline_expired = False

    def cancel(self) -> None:
        self.cancelled = True  # closes the pre-statement race
        self.conn.interrupt()  # stops a statement already running, now

    @property
    def deadline_expired(self) -> bool:
        return self._deadline_expired


class ReadPool:
    """A small pool of read-only connections used from a thread executor.

    Read connections are never held across an ``await`` that is not the query
    itself (index-schema §5.4).
    """

    def __init__(self, path: Path, size: int = 4, budget_s: float = 30.0) -> None:
        self._path = path
        self._size = max(1, size)
        self._budget_s = budget_s
        self._free: asyncio.LifoQueue[tuple[sqlite3.Connection, Cancellable]] | None = None
        self._all: list[sqlite3.Connection] = []

    async def open(self) -> None:
        self._free = asyncio.LifoQueue()
        for _ in range(self._size):
            conn = await asyncio.to_thread(open_read_connection, self._path)
            self._all.append(conn)
            self._free.put_nowait((conn, Cancellable(conn)))

    async def close(self) -> None:
        for conn in self._all:
            with contextlib.suppress(Exception):
                conn.close()
        self._all.clear()
        self._free = None

    async def run(self, fn: Callable[[sqlite3.Connection], T], budget_s: float | None = None) -> T:
        """Run ``fn`` on a pooled connection with a real, interruptible deadline."""
        if self._free is None:  # pragma: no cover - misuse
            raise RuntimeError("ReadPool.open() was never awaited")
        conn, cancel = await self._free.get()
        cancel.arm(self._budget_s if budget_s is None else budget_s)
        try:
            try:
                return await asyncio.to_thread(fn, conn)
            except sqlite3.OperationalError as exc:
                if "interrupted" in str(exc).lower():
                    raise QueryInterrupted(cancel.deadline_expired) from exc
                raise
        except asyncio.CancelledError:
            # A dropped future must stop real work, not just stop waiting.
            cancel.cancel()
            raise
        finally:
            cancel.disarm()
            self._free.put_nowait((conn, cancel))


class Writer:
    """The single write connection, guarded by an asyncio.Lock.

    Every read-modify-write uses ``BEGIN IMMEDIATE``: a deferred transaction
    that reads first and writes later takes a read lock then tries to upgrade,
    and the upgrade failure is a SQLITE_BUSY that ``busy_timeout`` will *not*
    retry.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._conn: sqlite3.Connection | None = None
        self._lock = asyncio.Lock()

    async def open(self) -> None:
        self._conn = await asyncio.to_thread(open_write_connection, self._path)

    async def close(self) -> None:
        if self._conn is not None:
            conn, self._conn = self._conn, None
            await asyncio.to_thread(conn.close)

    @property
    def raw(self) -> sqlite3.Connection:
        if self._conn is None:  # pragma: no cover - misuse
            raise RuntimeError("Writer.open() was never awaited")
        return self._conn

    async def run(self, fn: Callable[[sqlite3.Connection], T]) -> T:
        """Run ``fn`` inside a BEGIN IMMEDIATE transaction on the writer."""
        async with self._lock:
            return await asyncio.to_thread(self._run_sync, fn)

    def _run_sync(self, fn: Callable[[sqlite3.Connection], T]) -> T:
        conn = self.raw
        conn.execute("BEGIN IMMEDIATE")
        try:
            result = fn(conn)
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")
        return result


@contextlib.contextmanager
def immediate(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Synchronous BEGIN IMMEDIATE helper, for migrations and tests."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


@contextlib.asynccontextmanager
async def admission(sem: asyncio.Semaphore, retry_after_s: int = 1) -> AsyncIterator[None]:
    """Admission control: a third concurrent search gets an immediate E_BUSY.

    Queueing converts a slow query into a slow *everything* — screenpipe's
    #4474 outage was exactly that.
    """
    from ..errors import busy

    # `locked()` is "cannot be acquired immediately". Nothing awaits between the
    # check and the acquire, so on a single-threaded loop this is atomic:
    # asyncio.Semaphore.acquire returns synchronously when the value is > 0.
    if sem.locked():
        raise busy(retry_after_s)
    await sem.acquire()
    try:
        yield
    finally:
        sem.release()


def scalar(conn: sqlite3.Connection, sql: str, params: Any = ()) -> Any:
    row = conn.execute(sql, params).fetchone()
    return None if row is None else row[0]
