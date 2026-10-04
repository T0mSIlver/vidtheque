"""Measuring the feed against YouTube: watch time, shares, the weekly ledger
(companion.md §3.3, #157).

`record_watched` closes a `watch` hand-off with the time spent in the player,
`record_share` logs a link shared to the app, and `weeks` reads the four
weekly figures. Everything is read live from `signals`, `feedback`, `shares`
and `verdicts`, so a late thumb or watch still lands in its week.
"""

from __future__ import annotations

import json
import math
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from .signals import KEEP_DAYS

WEEKS = 8
# A moment counts as watched when one stretch in the player starts at most
# this far past it and runs this long after it (or to the video's end).
MOMENT_LEAD_S = 5.0
MOMENT_WATCH_S = 60.0
# Below this a hand-off is a bounce, not a watch, for regret's denominator.
WATCHED_MIN_S = 60.0
# The app's clock and the server's may disagree a little; never by more.
CLOCK_SLACK_S = 60.0
# Each week's cohort is read whole up to here, and says `capped` past it.
COHORT_CAP = 500
REGRET_TARGET = 0.10


@dataclass(frozen=True)
class Watched:
    signal_id: int
    watched_s: float
    already: bool


def record_watched(
    conn: sqlite3.Connection,
    signal_id: int,
    watched_s: float,
    *,
    owner_id: int = 1,
    now: int | None = None,
) -> Watched | None:
    """Set a `watch` signal's time in the player, once; None when it is not this owner's watch.

    Capped at what is left of the video from the hand-off's offset, and at the
    time since the hand-off, so a phone left in YouTube overnight counts one video.
    """
    row = conn.execute(
        "SELECT s.at, s.offset_s, s.watched_s, v.duration_s FROM signals s"
        " JOIN videos v ON v.id = s.video_id"
        " WHERE s.id = ? AND s.owner_id = ? AND s.kind = 'watch'",
        (signal_id, owner_id),
    ).fetchone()
    if row is None:
        return None
    if row["watched_s"] is not None:
        return Watched(signal_id, float(row["watched_s"]), True)
    at = int(time.time()) if now is None else now
    cap = max(0.0, at - int(row["at"]) + CLOCK_SLACK_S)
    duration = float(row["duration_s"] or 0)
    if duration > 0:
        cap = min(cap, max(0.0, duration - float(row["offset_s"] or 0)))
    kept = round(min(max(0.0, watched_s), cap), 1)
    conn.execute("UPDATE signals SET watched_s = ? WHERE id = ?", (kept, signal_id))
    return Watched(signal_id, kept, False)


def record_share(
    conn: sqlite3.Connection,
    source_id: str,
    *,
    client: str | None = None,
    owner_id: int = 1,
    now: int | None = None,
) -> int:
    """One link shared to the app. Kept as long as the signals are."""
    at = int(time.time()) if now is None else now
    share_id = conn.execute(
        "INSERT INTO shares (owner_id, at, source_id, client) VALUES (?, ?, ?, ?)",
        (owner_id, at, source_id, client),
    ).lastrowid
    conn.execute("DELETE FROM shares WHERE at < ?", (at - KEEP_DAYS * 86_400,))
    return int(share_id)


def miss_of(
    conn: sqlite3.Connection, source_id: str, at: int, *, owner_id: int = 1
) -> tuple[bool | None, str]:
    """Was a video shared at ``at`` a miss? None while it waits for its verdict.

    A miss is a video the feed scored 0–1, or one from a channel no follow
    covered when it was shared: either way the feed did not offer it.
    """
    video = conn.execute(
        "SELECT v.id, v.channel_id, d.score FROM videos v"
        " LEFT JOIN verdicts d ON d.video_id = v.id"
        " WHERE v.source = 'youtube' AND v.source_id = ? AND v.owner_id = ?",
        (source_id, owner_id),
    ).fetchone()
    if video is None:
        return None, "not indexed yet"
    if video["channel_id"] is not None and not _followed(conn, str(video["channel_id"]), at, owner_id):
        return True, "channel not followed"
    if video["score"] is None:
        return None, "no verdict yet"
    if int(video["score"]) <= 1:
        return True, f"scored {int(video['score'])}"
    return False, f"scored {int(video['score'])}"


def _followed(conn: sqlite3.Connection, channel_id: str, at: int, owner_id: int) -> bool:
    # A channel follow covers a channel once one of its videos is in the
    # follow's collection; an unfollow deletes the collection, and with it the claim.
    return (
        conn.execute(
            "SELECT 1 FROM follows f JOIN collections c ON c.id = f.collection_id"
            " JOIN collection_videos cv ON cv.collection_id = c.id"
            " JOIN videos v ON v.id = cv.video_id"
            " WHERE c.kind = 'channel' AND c.owner_id = ? AND c.created_at <= ?"
            " AND v.channel_id = ? LIMIT 1",
            (owner_id, at, channel_id),
        ).fetchone()
        is not None
    )


# ------------------------------------------------------------------ the weeks


def week_starts(now: datetime, count: int = WEEKS) -> list[int]:
    """Monday midnights on the box's clock, this week first."""
    monday = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    return [int((monday - timedelta(weeks=i)).timestamp()) for i in range(count)]


def _coverage(conn: sqlite3.Connection, video_ids: list[int], owner_id: int) -> dict[int, list[tuple[float, float]]]:
    """Each video's stretches in the player, `(from, to)` in video seconds."""
    spans: dict[int, list[tuple[float, float]]] = {}
    for r in conn.execute(
        "SELECT video_id, offset_s, watched_s FROM signals"
        " WHERE owner_id = ? AND kind = 'watch' AND watched_s IS NOT NULL"
        " AND video_id IN (SELECT value FROM json_each(?))",
        (owner_id, json.dumps(video_ids)),
    ):
        start = float(r["offset_s"] or 0)
        spans.setdefault(int(r["video_id"]), []).append((start, start + float(r["watched_s"])))
    return spans


def kept(moments: list[float], duration: float, spans: list[tuple[float, float]]) -> bool:
    """Watched past half the moments; with no moment, past half the video."""
    if not spans:
        return False
    if not moments:
        return duration > 0 and sum(b - a for a, b in spans) >= duration / 2
    seen = 0
    for m in moments:
        until = min(m + MOMENT_WATCH_S, duration) if duration > m else m
        if any(a <= m + MOMENT_LEAD_S and b >= until for a, b in spans):
            seen += 1
    return seen >= math.ceil(len(moments) / 2)


def _hits(conn: sqlite3.Connection, start: int, end: int, owner_id: int) -> dict[str, Any]:
    # The week's 2–3 verdicts are those of the videos published in it, as the
    # feed orders them; a backfilled old video lands in its own old week.
    rows = conn.execute(
        "SELECT v.id, v.duration_s, d.moments, f.state FROM verdicts d"
        " JOIN videos v ON v.id = d.video_id"
        " LEFT JOIN feedback f ON f.video_id = v.id AND f.owner_id = v.owner_id"
        " WHERE v.owner_id = ? AND d.score >= 2"
        " AND COALESCE(v.published_at, d.created_at) >= ? AND COALESCE(v.published_at, d.created_at) < ?"
        " ORDER BY v.id LIMIT ?",
        (owner_id, start, end, COHORT_CAP + 1),
    ).fetchall()
    capped = len(rows) > COHORT_CAP
    rows = rows[:COHORT_CAP]
    spans = _coverage(conn, [int(r["id"]) for r in rows], owner_id)
    hits = 0
    for r in rows:
        moments = [float(m["offset_s"]) for m in json.loads(r["moments"])]
        if r["state"] == "up" or kept(moments, float(r["duration_s"] or 0), spans.get(int(r["id"]), [])):
            hits += 1
    return {"kept": hits, "offered": len(rows), "rate": _rate(hits, len(rows)), "capped": capped}


def _regret(conn: sqlite3.Connection, start: int, end: int, owner_id: int) -> dict[str, Any]:
    # A video watched this week (a minute or more in the player) and thumbed
    # down after the first such watch.
    rows = conn.execute(
        "SELECT w.video_id, w.first_at, f.state, f.at AS feedback_at FROM ("
        "  SELECT video_id, MIN(at) AS first_at FROM signals"
        "  WHERE owner_id = ? AND kind = 'watch' AND watched_s >= ? AND at >= ? AND at < ?"
        "  GROUP BY video_id LIMIT ?"
        ") w LEFT JOIN feedback f ON f.video_id = w.video_id AND f.owner_id = ?",
        (owner_id, WATCHED_MIN_S, start, end, COHORT_CAP + 1, owner_id),
    ).fetchall()
    capped = len(rows) > COHORT_CAP
    rows = rows[:COHORT_CAP]
    down = sum(1 for r in rows if r["state"] == "down" and int(r["feedback_at"]) >= int(r["first_at"]))
    return {"down": down, "watched": len(rows), "rate": _rate(down, len(rows)), "capped": capped}


def _misses(conn: sqlite3.Connection, start: int, end: int, owner_id: int) -> dict[str, Any]:
    rows = conn.execute(
        "SELECT source_id, MIN(at) AS at FROM shares WHERE owner_id = ? AND at >= ? AND at < ?"
        " GROUP BY source_id LIMIT ?",
        (owner_id, start, end, COHORT_CAP + 1),
    ).fetchall()
    capped = len(rows) > COHORT_CAP
    count = pending = 0
    for r in rows[:COHORT_CAP]:
        miss, _ = miss_of(conn, str(r["source_id"]), int(r["at"]), owner_id=owner_id)
        if miss is None:
            pending += 1
        elif miss:
            count += 1
    return {"count": count, "pending": pending, "shared": min(len(rows), COHORT_CAP), "capped": capped}


def _rate(part: int, whole: int) -> float | None:
    return round(part / whole, 3) if whole else None


def weeks(conn: sqlite3.Connection, now: datetime, *, owner_id: int = 1) -> dict[str, Any]:
    starts = week_starts(now)
    ends = [int(now.timestamp()) + 1, *starts[:-1]]
    return {
        "regret_target": REGRET_TARGET,
        "weeks": [
            {
                "start": start,
                "current": i == 0,
                "hits": _hits(conn, start, end, owner_id),
                "regret": _regret(conn, start, end, owner_id),
                "misses": _misses(conn, start, end, owner_id),
            }
            for i, (start, end) in enumerate(zip(starts, ends))
        ],
    }
