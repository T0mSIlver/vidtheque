"""Moment collections: the best moments across videos, by profile entry (#171).

companion.md §6.2. A positive entry collects the moments of the verdicts of 2
or more that match it, best first; each moment after the first says which
stretch repeats an earlier one, and its link starts after it. Every leg is
bounded: the verdicts scanned, the moments kept, the chunks compared.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from ..db.queries import QUERYABLE_INDEX_STATES
from ..profile import store as profile_store
from . import novelty, store

# The newest verdicts read for every collection at once.
SCAN = 2000
MAX_MOMENTS = 10
# A moment longer than this many chunks (about 10 minutes) is compared on its first ones.
MOMENT_CHUNKS = 20


@dataclass(frozen=True)
class Item:
    video: sqlite3.Row  # id, public_id, title, channel_name, duration_s, published_at
    moment: store.Moment
    score: int
    strength: int


@dataclass(frozen=True)
class Placed:
    """An item with where its link starts and the earlier item it repeats."""

    item: Item
    start_s: float
    repeats: int | None  # index of the earlier item
    whole: bool


def by_entry(conn: sqlite3.Connection, owner_id: int = 1) -> dict[int, tuple[list[Item], bool]]:
    """Each positive live entry's moments, best first, at most `MAX_MOMENTS`,
    and whether more matched. Best: the entry central to the video before
    merely coming up, then the higher score, then the newer video. A moment
    written before spans has no length and is left out, as is one whose
    receipt a reindex broke, and every moment of a video thumbed down or muted."""
    entries = [int(e["id"]) for e in profile_store.entries(conn, owner_id) if float(e["weight"]) > 0]
    states = ",".join("?" for _ in QUERYABLE_INDEX_STATES)
    rows = conn.execute(
        "SELECT v.id, v.public_id, v.title, v.channel_name, v.duration_s, v.published_at,"
        " d.score, d.matches, d.moments FROM verdicts d JOIN videos v ON v.id = d.video_id"
        " LEFT JOIN feedback f ON f.video_id = v.id AND f.owner_id = v.owner_id"
        f" WHERE v.owner_id = ? AND d.score >= 2 AND v.index_state IN ({states})"
        " AND COALESCE(f.state, 'none') NOT IN ('down', 'muted')"
        " ORDER BY d.created_at DESC LIMIT ?",
        (owner_id, *QUERYABLE_INDEX_STATES, SCAN),
    ).fetchall()
    found: dict[int, list[Item]] = {e: [] for e in entries}
    for row in rows:
        wanted = [
            m for m in json.loads(row["matches"])
            if int(m["entry_id"]) in found and m["direction"] == "up"
        ]
        if not wanted:
            continue
        # As on the video screen: a cue a reindex removed drops its moment.
        kept, _ = store.check_receipts(conn, int(row["id"]), store.moments_of(row))
        moments = sorted((m for m in kept if m.end_s is not None), key=lambda m: m.offset_s)
        for match in wanted:
            found[int(match["entry_id"])].extend(
                Item(row, m, int(row["score"]), int(match["strength"])) for m in moments
            )
    out: dict[int, tuple[list[Item], bool]] = {}
    for entry, items in found.items():
        # Stable: a video's moments stay together, in order.
        items.sort(key=lambda i: (-i.strength, -i.score, -(i.video["published_at"] or 0)))
        out[entry] = (items[:MAX_MOMENTS], len(items) > MAX_MOMENTS)
    return out


def place(conn: sqlite3.Connection, items: list[Item]) -> list[Placed]:
    """Walk the collection from its first moment: each later one is compared,
    chunk by chunk, with every earlier moment of another video. Moments are
    short, so one matched chunk is a repeat."""
    chunks = [_moment_chunks(conn, i) for i in items]
    out: list[Placed] = []
    for j, item in enumerate(items):
        spans: list[novelty.Span] = []
        for i in range(j):
            if items[i].video["id"] == item.video["id"]:
                continue
            # The earlier item's index rides in `video_id`.
            spans.extend(novelty.spans_between(conn, chunks[j], chunks[i], i, 1))
        start, touching, whole = store.start_after(item.moment.offset_s, item.moment.end_s, spans)
        out.append(Placed(item, start, touching[0].video_id if touching else None, whole))
    return out


def _moment_chunks(conn: sqlite3.Connection, item: Item) -> list[tuple[int, float, float]]:
    return [
        (int(r[0]), float(r[1]), float(r[2]))
        for r in conn.execute(
            "SELECT id, start_s, end_s FROM chunks WHERE video_id = ? AND end_s > ? AND start_s < ?"
            " ORDER BY seq LIMIT ?",
            (item.video["id"], item.moment.offset_s, item.moment.end_s, MOMENT_CHUNKS),
        )
    ]
