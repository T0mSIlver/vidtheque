"""Trial follows — following.md §10.7, companion.md §6.2.

A trial is a follow with `trial_until` set. The follow clock settles it on its
tick: past `trial_until`, it becomes lasting when a video it brought in got a
thumbs up or a full watch after the trial began, and is unfollowed otherwise.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from dataclasses import dataclass

from . import store

logger = logging.getLogger(__name__)

TRIAL_DAYS = 14
TRIAL_S = TRIAL_DAYS * 86_400
# A full watch: the `watch` hand-offs cover this share of the video.
FULL_WATCH = 0.8
# Trials settled per tick; the rest wait for the next one.
SETTLE_PER_TICK = 10


@dataclass(frozen=True)
class Settled:
    collection_id: int
    title: str
    kept: bool


def start(conn: sqlite3.Connection, collection_id: int, now: int | None = None) -> int:
    """Make a follow a trial that ends `TRIAL_DAYS` from now. Returns the end."""
    until = (int(time.time()) if now is None else now) + TRIAL_S
    conn.execute(
        "UPDATE follows SET trial_until = ?, updated_at = unixepoch() WHERE collection_id = ?",
        (until, collection_id),
    )
    return until


def make_lasting(conn: sqlite3.Connection, collection_id: int) -> None:
    conn.execute(
        "UPDATE follows SET trial_until = NULL, updated_at = unixepoch() WHERE collection_id = ?",
        (collection_id,),
    )


def liked_since(conn: sqlite3.Connection, collection_id: int, since: int, owner_id: int = 1) -> bool:
    """Did a video this follow brought in get a thumbs up or a full watch since `since`?"""
    if conn.execute(
        "SELECT 1 FROM collection_videos cv JOIN feedback f ON f.video_id = cv.video_id"
        " WHERE cv.collection_id = ? AND f.owner_id = ? AND f.state = 'up' AND f.at >= ? LIMIT 1",
        (collection_id, owner_id, since),
    ).fetchone():
        return True
    spans: dict[int, list[tuple[float, float]]] = {}
    durations: dict[int, float] = {}
    for r in conn.execute(
        "SELECT s.video_id, s.offset_s, s.watched_s, v.duration_s FROM signals s"
        " JOIN collection_videos cv ON cv.video_id = s.video_id"
        " JOIN videos v ON v.id = s.video_id"
        " WHERE cv.collection_id = ? AND s.owner_id = ? AND s.kind = 'watch'"
        " AND s.watched_s IS NOT NULL AND s.at >= ?",
        (collection_id, owner_id, since),
    ):
        start_s = float(r["offset_s"] or 0)
        spans.setdefault(int(r["video_id"]), []).append((start_s, start_s + float(r["watched_s"])))
        durations[int(r["video_id"])] = float(r["duration_s"] or 0)
    return any(
        durations[v] > 0 and covered(s) >= FULL_WATCH * durations[v] for v, s in spans.items()
    )


def covered(spans: list[tuple[float, float]]) -> float:
    """Seconds inside at least one stretch; overlapping watches count once."""
    total, reach = 0.0, float("-inf")
    for a, b in sorted(spans):
        if b > reach:
            total += b - max(a, reach)
            reach = b
    return total


def settle(conn: sqlite3.Connection, now: int | None = None, owner_id: int = 1) -> list[Settled]:
    """Keep or end every trial past its end. Cheap: an indexed scan of a few rows."""
    at = int(time.time()) if now is None else now
    done: list[Settled] = []
    for row in conn.execute(
        "SELECT f.collection_id, f.trial_until, c.title FROM follows f"
        " JOIN collections c ON c.id = f.collection_id"
        " WHERE f.trial_until IS NOT NULL AND f.trial_until <= ? AND c.owner_id = ?"
        " ORDER BY f.trial_until LIMIT ?",
        (at, owner_id, SETTLE_PER_TICK),
    ).fetchall():
        collection_id = int(row["collection_id"])
        kept = liked_since(conn, collection_id, int(row["trial_until"]) - TRIAL_S, owner_id)
        if kept:
            make_lasting(conn, collection_id)
        else:
            store.delete(conn, collection_id)
        logger.info(
            "trial follow %s %s", row["title"], "kept: a video it brought in was liked" if kept else "ended"
        )
        done.append(Settled(collection_id, str(row["title"]), kept))
    return done
