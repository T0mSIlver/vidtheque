"""The `signals` table: what the owner reads, searches and taps (companion.md §2.3).

`record_signal` is the one insert; the MCP tool layer calls it for `search` and
the three read tools, and the app endpoints will call it for the rest.
"""

from __future__ import annotations

import sqlite3
import time

KINDS = frozenset(
    {
        "mcp_search",
        "mcp_read",
        "ask_claude",
        "thumb_up",
        "thumb_down",
        "mute",
        "open",
        "watch",
        "dismiss",
    }
)
KEEP_DAYS = 180
MAX_TEXT_CHARS = 500


def record_signal(
    conn: sqlite3.Connection,
    kind: str,
    *,
    video_id: str | None = None,
    offset_s: float | None = None,
    text: str | None = None,
    client: str | None = None,
    owner_id: int = 1,
    now: int | None = None,
) -> int | None:
    """Insert one signal. ``video_id`` is the public id; one not in the corpus records nothing."""
    if kind not in KINDS:
        raise ValueError(f"unknown signal kind {kind!r}")
    at = int(time.time()) if now is None else now
    row_video: int | None = None
    if video_id is not None:
        found = conn.execute(
            "SELECT id FROM videos WHERE public_id = ? AND owner_id = ?", (video_id, owner_id)
        ).fetchone()
        if found is None:
            return None
        row_video = int(found["id"])
    if text is not None:
        text = text.strip()[:MAX_TEXT_CHARS] or None
    signal_id = conn.execute(
        "INSERT INTO signals (owner_id, at, kind, video_id, offset_s, text, client)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (owner_id, at, kind, row_video, offset_s, text, client),
    ).lastrowid
    # Retention on the event that fills the table, like `follow_spend`: an
    # indexed range delete that finds nothing on almost every call.
    prune(conn, now=at)
    return int(signal_id)


def prune(conn: sqlite3.Connection, *, now: int | None = None) -> int:
    """Delete signals older than ``KEEP_DAYS``. Returns how many went."""
    cutoff = (int(time.time()) if now is None else now) - KEEP_DAYS * 86_400
    return conn.execute("DELETE FROM signals WHERE at < ?", (cutoff,)).rowcount
