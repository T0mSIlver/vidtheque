"""The scout's picks and the speaker suggestion: storage and the shapes the
feed reads (companion.md §6.2, dashboard.md §27, index-schema §1.22)."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from ..profile import ledger

# Per calendar week, server-side (companion.md §6.2).
SHOWN_PER_WEEK = 3
JUDGED_PER_WEEK = 12
CALLS_PER_WEEK = 15
PURPOSES = ("scout_verdict", "speaker_names")
WATCHES_MAX = 20
FEEDBACK = ("none", "up", "down")


def youtu_be(source_id: str, offset_s: float | None = None) -> str:
    return f"https://youtu.be/{source_id}" + (f"?t={int(offset_s)}" if offset_s is not None else "")


# ------------------------------------------------------------------ the caps


def week_counts(
    conn: sqlite3.Connection, week: str, since_at: int, owner_id: int = 1
) -> dict[str, int]:
    """What this week has spent: picks shown, candidates judged, model calls."""
    row = conn.execute(
        "SELECT COALESCE(SUM(state = 'shown'), 0) AS shown,"
        " COALESCE(SUM(state IN ('shown','judged')), 0) AS judged"
        " FROM outside_picks WHERE owner_id = ? AND week = ?",
        (owner_id, week),
    ).fetchone()
    calls = conn.execute(
        "SELECT COUNT(*) FROM llm_calls WHERE owner_id = ? AND at >= ?"
        " AND purpose IN (SELECT value FROM json_each(?))",
        (owner_id, since_at, json.dumps(PURPOSES)),
    ).fetchone()[0]
    return {"shown": int(row["shown"]), "judged": int(row["judged"]), "calls": int(calls)}


# ------------------------------------------------------------- what is known


def known(conn: sqlite3.Connection, source_id: str, owner_id: int = 1) -> bool:
    """In the corpus already, or asked about by the scout before."""
    return (
        conn.execute(
            "SELECT 1 FROM videos WHERE source = 'youtube' AND source_id = ? AND owner_id = ?"
            " UNION ALL SELECT 1 FROM outside_picks WHERE source_id = ? AND owner_id = ? LIMIT 1",
            (source_id, owner_id, source_id, owner_id),
        ).fetchone()
        is not None
    )


def _url_key(url: str | None) -> str | None:
    return url.rstrip("/").lower() if url else None


def follow_of(
    conn: sqlite3.Connection, channel_id: str | None, channel_url: str | None, owner_id: int = 1
) -> sqlite3.Row | None:
    """The follow that covers this channel, by its URL or by its channel id."""
    keys = [k for k in (_url_key(channel_url),) if k]
    if channel_id:
        keys.append(f"https://www.youtube.com/channel/{channel_id}".lower())
    for key in keys:
        row = conn.execute(
            "SELECT c.id AS collection_id, f.trial_until FROM collections c"
            " JOIN follows f ON f.collection_id = c.id"
            " WHERE c.owner_id = ? AND lower(rtrim(c.source_url, '/')) = ?",
            (owner_id, key),
        ).fetchone()
        if row is not None:
            return row
    if channel_id:
        # A follow by @handle covers the channel once one of its videos landed.
        return conn.execute(
            "SELECT c.id AS collection_id, f.trial_until FROM follows f"
            " JOIN collections c ON c.id = f.collection_id"
            " JOIN collection_videos cv ON cv.collection_id = c.id"
            " JOIN videos v ON v.id = cv.video_id"
            " WHERE c.kind = 'channel' AND c.owner_id = ? AND v.channel_id = ? LIMIT 1",
            (owner_id, channel_id),
        ).fetchone()
    return None


def follow_json(
    conn: sqlite3.Connection,
    channel_id: str | None,
    channel_url: str | None,
    followed: bool,
    owner_id: int = 1,
) -> dict[str, Any]:
    """`none`, `trial`, `lasting`, or `ended` for a trial started here that is gone."""
    row = follow_of(conn, channel_id, channel_url, owner_id)
    if row is not None:
        until = row["trial_until"]
        return {"state": "trial" if until is not None else "lasting", "until": until}
    return {"state": "ended" if followed else "none", "until": None}


# ------------------------------------------------------------------- writing


def insert(conn: sqlite3.Connection, owner_id: int = 1, **fields: Any) -> int:
    fields = {**fields, "owner_id": owner_id}
    if "moments" in fields:
        fields["moments"] = json.dumps(fields["moments"])
    names = ", ".join(fields)
    marks = ", ".join("?" for _ in fields)
    return int(
        conn.execute(
            f"INSERT INTO outside_picks ({names}) VALUES ({marks})", tuple(fields.values())
        ).lastrowid
    )


def shown(conn: sqlite3.Connection, pick_id: int, owner_id: int = 1) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM outside_picks WHERE id = ? AND owner_id = ? AND state = 'shown'",
        (pick_id, owner_id),
    ).fetchone()


def set_feedback(
    conn: sqlite3.Connection, pick_id: int, state: str, owner_id: int = 1
) -> sqlite3.Row | None:
    if shown(conn, pick_id, owner_id) is None:
        return None
    conn.execute("UPDATE outside_picks SET feedback = ? WHERE id = ?", (state, pick_id))
    return shown(conn, pick_id, owner_id)


def record_watched(
    conn: sqlite3.Connection, pick_id: int, offset_s: float, watched_s: float, owner_id: int = 1
) -> float | None:
    """One hand-off's time in YouTube, capped at what is left of the video."""
    row = shown(conn, pick_id, owner_id)
    if row is None:
        return None
    duration = float(row["duration_s"] or 0)
    kept = max(0.0, watched_s)
    if duration > 0:
        offset_s = min(offset_s, duration)
        kept = min(kept, duration - offset_s)
    kept = round(kept, 1)
    watches = json.loads(row["watches"])
    interval = [offset_s, offset_s + kept]
    # The app resends a return the network may have lost; the same one twice is one watch.
    if watches and watches[-1] == interval:
        return kept
    watches = watches[-(WATCHES_MAX - 1) :] + [interval]
    conn.execute(
        "UPDATE outside_picks SET watches = ? WHERE id = ?", (json.dumps(watches), pick_id)
    )
    return kept


def mark_followed(conn: sqlite3.Connection, pick_id: int, at: int) -> None:
    conn.execute(
        "UPDATE outside_picks SET followed_at = COALESCE(followed_at, ?) WHERE id = ?",
        (at, pick_id),
    )


# ------------------------------------------------------------------- reading


def pick_json(conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    source_id = str(row["source_id"])
    return {
        "id": int(row["id"]),
        "video_id": source_id,
        "url": youtu_be(source_id),
        "title": row["title"],
        "channel": row["channel_name"],
        "channel_url": row["channel_url"],
        "duration_s": float(row["duration_s"] or 0),
        "published_at": row["published_at"],
        "because": row["because"],
        "score": row["score"],
        "reason": row["reason"],
        "summary": row["summary"],
        "moments": [
            {**m, "url": youtu_be(source_id, m["offset_s"])} for m in json.loads(row["moments"])
        ],
        "feedback": row["feedback"],
        "follow": follow_json(
            conn,
            row["channel_id"],
            row["channel_url"],
            row["followed_at"] is not None,
            int(row["owner_id"]),
        ),
    }


def speaker_json(conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    talk = None
    if row["video_id"] is not None:
        video = conn.execute(
            "SELECT public_id FROM videos WHERE id = ?", (row["video_id"],)
        ).fetchone()
        talk = video["public_id"] if video else None
    channel = (
        {"name": row["channel_name"], "url": row["channel_url"]} if row["channel_url"] else None
    )
    return {
        "id": int(row["id"]),
        "name": row["name"],
        "reason": row["reason"],
        "state": row["state"],
        "talk": {"video_id": talk, "title": row["talk_title"]},
        "channel": channel,
        "talks": [{**t, "url": youtu_be(t["video_id"])} for t in json.loads(row["talks"])],
        "follow": follow_json(
            conn,
            row["channel_id"],
            row["channel_url"],
            row["state"] == "followed",
            int(row["owner_id"]),
        ),
    }


def week_payload(conn: sqlite3.Connection, week: str, owner_id: int = 1) -> dict[str, Any]:
    rows = conn.execute(
        "SELECT * FROM outside_picks WHERE owner_id = ? AND week = ? AND state = 'shown'"
        " ORDER BY id LIMIT ?",
        (owner_id, week, SHOWN_PER_WEEK),
    ).fetchall()
    speaker = conn.execute(
        "SELECT * FROM speaker_suggestions WHERE owner_id = ? AND week = ? AND state <> 'dismissed'",
        (owner_id, week),
    ).fetchone()
    return {
        "week": week,
        "picks": [pick_json(conn, r) for r in rows],
        "speaker": speaker_json(conn, speaker) if speaker is not None else None,
    }


def week_rate(conn: sqlite3.Connection, week: str, owner_id: int = 1) -> dict[str, Any]:
    """The week's shown picks, and the share kept as §3.3 counts a hit."""
    rows = conn.execute(
        "SELECT duration_s, moments, feedback, watches FROM outside_picks"
        " WHERE owner_id = ? AND week = ? AND state = 'shown'",
        (owner_id, week),
    ).fetchall()
    n_kept = 0
    for r in rows:
        moments = [(float(m["offset_s"]), m.get("end_s")) for m in json.loads(r["moments"])]
        spans = [(float(a), float(b)) for a, b in json.loads(r["watches"])]
        if r["feedback"] == "up" or ledger.kept(moments, float(r["duration_s"] or 0), spans):
            n_kept += 1
    return {
        "shown": len(rows),
        "kept": n_kept,
        "rate": round(n_kept / len(rows), 3) if rows else None,
    }
