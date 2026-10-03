"""Thumbs and "less like this" as one state per video (companion.md §2.3).

A tap sets the state, a second tap takes it back; the app and the web feed show
what is stored. The nightly update reads only the videos whose state moved
since it last read them (`state != seen`), so a tap taken back the same day
never reaches the profile, and one taken back after a night reads as taken back.
"""

from __future__ import annotations

import sqlite3
import time

from . import signals

STATES = ("none", "up", "down", "muted")
# The event a state writes to `signals`; taking one back writes none.
KIND_OF = {"up": "thumb_up", "down": "thumb_down", "muted": "mute"}
STATE_OF = {kind: state for state, kind in KIND_OF.items()}


def set_state(
    conn: sqlite3.Connection,
    video_id: str,
    state: str,
    *,
    client: str | None = None,
    owner_id: int = 1,
    now: int | None = None,
) -> int | None:
    """Store ``state`` for the video (public id). None when the video is not in the corpus.

    Returns the signal id the tap wrote, or 0 for a take-back, which writes none.
    """
    if state not in STATES:
        raise ValueError(f"unknown feedback state {state!r}")
    at = int(time.time()) if now is None else now
    found = conn.execute(
        "SELECT id FROM videos WHERE public_id = ? AND owner_id = ?", (video_id, owner_id)
    ).fetchone()
    if found is None:
        return None
    row_video = int(found["id"])
    conn.execute(
        "INSERT INTO feedback (owner_id, video_id, state, at) VALUES (?, ?, ?, ?)"
        " ON CONFLICT (owner_id, video_id) DO UPDATE SET state = excluded.state, at = excluded.at",
        (owner_id, row_video, state, at),
    )
    if state == "none":
        return 0
    signal_id = signals.record_signal(
        conn, KIND_OF[state], video_id=video_id, client=client, owner_id=owner_id, now=at
    )
    return signal_id or 0


def state_of(conn: sqlite3.Connection, video_row_id: int, owner_id: int = 1) -> str:
    row = conn.execute(
        "SELECT state FROM feedback WHERE owner_id = ? AND video_id = ?", (owner_id, video_row_id)
    ).fetchone()
    return str(row["state"]) if row is not None else "none"


def unread(
    conn: sqlite3.Connection, until: int, owner_id: int = 1, limit: int = 200
) -> list[sqlite3.Row]:
    """The videos whose state moved since the nightly update last read them, oldest first."""
    return conn.execute(
        "SELECT f.video_id, f.state, f.seen, f.at, v.title, v.channel_name FROM feedback f"
        " JOIN videos v ON v.id = f.video_id"
        " WHERE f.owner_id = ? AND f.state != f.seen AND f.at <= ?"
        " ORDER BY f.at, f.video_id LIMIT ?",
        (owner_id, until, limit),
    ).fetchall()


def mark_read(conn: sqlite3.Connection, read: list[tuple[int, str]], owner_id: int = 1) -> None:
    """After a night: each (video row id, state it read) becomes `seen`.

    The state the night read, not the current one, so a tap made while the
    model was answering is still unread tomorrow. Rows are only deleted here,
    never by a take-back, or a first tap taken back during that window would
    lose the row the night must mark.
    """
    conn.executemany(
        "UPDATE feedback SET seen = ? WHERE owner_id = ? AND video_id = ?",
        [(state, owner_id, vid) for vid, state in read],
    )
    conn.execute("DELETE FROM feedback WHERE owner_id = ? AND state = 'none' AND seen = 'none'", (owner_id,))
