"""The ``derived/`` cache — resized, re-encoded keyframe variants.

index-schema §6 reserves ``derived/<source_id>/<ord>-w512-q75.jpg`` for this and
calls it "pure cache": disposable, byte-capped, `rm -rf` safe. tool-surface §4.6
specifies the serving side — "a cached PIL resample keyed on ``(frame_id, w, q)``,
single-flight per key". This module is that cache; ``frames.py`` is its only
caller.

Three properties are the whole point, and each one is a bug that shows up under
load rather than in a unit test:

* **Single-flight per key.** A results page fires ten thumbnails at once and a
  cold cache would otherwise resample the same frame ten times, in ten threads.
  Requests for one key queue on one lock; the first encodes, the rest hit.
* **Atomic writes.** Variants are written to a temp file in the destination
  directory and ``os.replace``d into place, so a crashed or concurrent writer
  can never leave a half-JPEG that the next request happily serves.
* **A byte cap, enforced in bytes.** ``VIDTHEQUE_DERIVED_CACHE_MB`` (an entry
  count would drift by 20x between a 64 px and a 1280 px variant). Eviction is
  LRU over an in-memory order that starts, on first use, from what is on disk
  ordered by mtime — a restart loses recency, not correctness.

Reads deliberately do **not** touch the file's mtime: it costs a syscall per hit
on the hot path, and recency is already tracked in memory for as long as the
process lives.
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from collections import OrderedDict
from contextlib import asynccontextmanager
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import AsyncIterator

log = logging.getLogger(__name__)

# tool-surface §4.6 clamps `width` to 128..1280. The floor is 64 here: the demo
# grid renders 96x54 CSS pixels, and a route that silently doubles the width it
# was asked for is how a results page ends up shipping 886 KB of JPEG for
# thumbnails. The signature binds the *clamped* pair, so widening the floor only
# ever makes a URL that used to 401 work — no signed URL changes meaning.
MIN_WIDTH = 64
MAX_WIDTH = 1280
DEFAULT_WIDTH = 512

MIN_QUALITY = 20
MAX_QUALITY = 95
DEFAULT_QUALITY = 75


@dataclass(frozen=True)
class Derived:
    """A variant to serve: a cached file, freshly encoded bytes, or both."""

    path: Path | None = None
    data: bytes | None = None


def variant_key(video: str, ordinal: int, width: int | None, quality: int) -> str:
    """``<source_id>/<ord>-w512-q75.jpg`` — the path index-schema §6 reserves.

    ``width=None`` means "re-encode at this quality, leave the pixels alone";
    it spells itself ``worig`` so the two cases can never collide.
    """
    edge = f"w{width}" if width is not None else "worig"
    return f"{video}/{ordinal:05d}-{edge}-q{quality}.jpg"


def encode_variant(source: Path, width: int | None, quality: int | None) -> bytes | None:
    """Resample and re-encode ``source``. ``None`` means "serve the original".

    Returning ``None`` rather than a copy of the original is the honest answer
    to the two cases where a variant would be a lie:

    * **Never upscale.** ``w`` wider than the stored keyframe is a request for
      pixels that do not exist; the caller gets the original, which is exactly
      what tool-surface §4.6 promises ("callers see a larger image than they
      asked for, never a different one").
    * **Undecodable source.** A truncated or non-JPEG file still has a byte
      stream a client may be able to use, and a 500 here would take out a whole
      results page over one bad frame.
    """
    from PIL import Image

    try:
        with Image.open(source) as original:
            original.load()
            src_w, src_h = original.size
            resample = width is not None and 0 < width < src_w
            if not resample and quality is None:
                return None
            if resample:
                assert width is not None  # narrowed by `resample`
                height = max(1, round(src_h * width / src_w))
                image = original.resize((width, height), Image.LANCZOS)
            else:
                image = original
            if image.mode not in ("RGB", "L"):
                image = image.convert("RGB")
            buffer = BytesIO()
            image.save(
                buffer,
                format="JPEG",
                quality=quality if quality is not None else DEFAULT_QUALITY,
                optimize=True,
            )
    except Exception as exc:  # pragma: no cover - exercised via the fake-JPEG corpus
        log.warning("derived: cannot re-encode %s (%s); serving the original", source, exc)
        return None
    return buffer.getvalue()


class DerivedCache:
    """Byte-capped LRU over ``derived/``, single-flight per key."""

    def __init__(self, root: Path, max_bytes: int) -> None:
        self.root = root
        self.max_bytes = max(0, max_bytes)
        # Counters, for tests and for anyone wondering whether the cache works.
        self.encodes = 0
        self.hits = 0
        self.evictions = 0
        self._entries: OrderedDict[str, int] = OrderedDict()  # key -> size, LRU first
        self._total = 0
        self._scanned = False
        self._index_lock = asyncio.Lock()
        self._locks: dict[str, asyncio.Lock] = {}
        self._waiting: dict[str, int] = {}

    # ------------------------------------------------------------------ api

    async def variant(
        self,
        video: str,
        ordinal: int,
        source: Path,
        width: int | None,
        quality: int | None,
    ) -> Derived | None:
        """The cached variant for this frame, encoding it if nobody else has.

        ``None`` means there is no variant to serve and the caller should hand
        back the stored keyframe (see :func:`encode_variant`).
        """
        stored_quality = quality if quality is not None else DEFAULT_QUALITY
        key = variant_key(video, ordinal, width, stored_quality)
        async with self._single_flight(key):
            cached = await self._take(key)
            if cached is not None:
                self.hits += 1
                return Derived(path=cached)
            data = await asyncio.to_thread(encode_variant, source, width, quality)
            self.encodes += 1
            if data is None:
                return None
            if self.max_bytes <= 0 or len(data) > self.max_bytes:
                # A cache that evicts everything else to hold one file is not a
                # cache. Serve the bytes, store nothing.
                return Derived(data=data)
            path = self.root / key
            await asyncio.to_thread(_atomic_write, path, data)
            await self._insert(key, len(data))
            return Derived(path=path, data=data)

    # -------------------------------------------------------- single flight

    @asynccontextmanager
    async def _single_flight(self, key: str) -> AsyncIterator[None]:
        lock = self._locks.get(key)
        if lock is None:
            lock = self._locks[key] = asyncio.Lock()
        # Refcount, so the lock dict cannot grow with one entry per frame ever
        # served. Both halves run between awaits, so they are atomic.
        self._waiting[key] = self._waiting.get(key, 0) + 1
        try:
            async with lock:
                yield
        finally:
            remaining = self._waiting[key] - 1
            if remaining:
                self._waiting[key] = remaining
            else:
                del self._waiting[key]
                self._locks.pop(key, None)

    # --------------------------------------------------------------- index

    async def _take(self, key: str) -> Path | None:
        """A hit, promoted to most-recent — or ``None``, forgetting a lost file."""
        async with self._index_lock:
            await self._scan_once()
            if key not in self._entries:
                return None
            path = self.root / key
            if not path.is_file():  # someone ran `rm -rf derived/`, which is allowed
                self._total -= self._entries.pop(key)
                return None
            self._entries.move_to_end(key)
            return path

    async def _insert(self, key: str, size: int) -> None:
        async with self._index_lock:
            await self._scan_once()
            previous = self._entries.pop(key, None)
            if previous is not None:
                self._total -= previous
            self._entries[key] = size
            self._total += size
            self._evict(protect=key)

    def _evict(self, protect: str) -> None:
        while self._total > self.max_bytes and len(self._entries) > 1:
            oldest, size = next(iter(self._entries.items()))
            if oldest == protect:  # the entry just written is not the victim
                break
            self._entries.pop(oldest)
            self._total -= size
            self.evictions += 1
            try:
                (self.root / oldest).unlink()
            except OSError as exc:  # pragma: no cover - racing another evictor
                log.debug("derived: could not evict %s (%s)", oldest, exc)

    async def _scan_once(self) -> None:
        """Adopt whatever a previous process left in ``derived/``, oldest first."""
        if self._scanned:
            return
        self._scanned = True
        # Anything this process already wrote is newer than anything it finds.
        live = list(self._entries)
        for key, size in await asyncio.to_thread(_scan, self.root):
            if key not in self._entries:
                self._entries[key] = size
                self._total += size
        for key in live:
            self._entries.move_to_end(key)
        self._evict(protect="")


def _scan(root: Path) -> list[tuple[str, int]]:
    """``(key, size)`` for every variant on disk, oldest mtime first."""
    if not root.is_dir():
        return []
    rows: list[tuple[float, str, int]] = []
    for path in root.glob("*/*.jpg"):
        if path.name.startswith("."):  # a temp file from an interrupted write
            continue
        try:
            stat = path.stat()
        except OSError:  # pragma: no cover - vanished mid-scan
            continue
        rows.append((stat.st_mtime, f"{path.parent.name}/{path.name}", stat.st_size))
    rows.sort()
    return [(key, size) for _mtime, key, size in rows]


def _atomic_write(path: Path, data: bytes) -> None:
    """Temp file in the destination directory, then ``os.replace``.

    Same directory so the rename stays on one filesystem, dot-prefixed so
    :func:`_scan` never mistakes a partial write for a cache entry.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
