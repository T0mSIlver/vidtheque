"""The verdicts table and the `verdict` job's queue. Sync, connection-first.

companion.md §3.1–3.2, index-schema §1.13. Callers run these through
`Database.write` or `Database.read`.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any, Sequence

from ..db.queries import QUERYABLE_INDEX_STATES
from ..jobs import store as jobs_store
from .novelty import Span

# After every indexing job, `high` (50) and plain (100) alike: a verdict is
# never what an index waits behind (§3.2).
VERDICT_PRIORITY = 200

BACKFILL_MAX = 1000


@dataclass(frozen=True)
class Moment:
    """A span from `offset_s`, inside cue `cue_id`, to `end_s`, the end of cue
    `end_cue_id`. Verdicts written before spans have no end (§3.1)."""

    cue_id: int
    offset_s: float
    why: str
    end_cue_id: int | None = None
    end_s: float | None = None


@dataclass(frozen=True)
class Match:
    """A profile entry the verdict hit. `direction` follows the entry's weight
    sign when scored; `strength` 2 is central to the video, 1 comes up."""

    entry_id: int
    direction: str
    strength: int


def check_receipts(
    conn: sqlite3.Connection, video_id: int, moments: Sequence[Moment]
) -> tuple[list[Moment], list[Moment]]:
    """(kept, dropped). A moment is kept only when its cue is this video's and
    its offset lies inside that cue, and, when it has an end, the end cue is
    this video's, not before the first, and holds `end_s`. Nothing is moved to
    a nearby cue (§3.1)."""
    kept: list[Moment] = []
    dropped: list[Moment] = []
    for moment in moments:
        start = conn.execute(
            "SELECT seq FROM cues WHERE id = ? AND video_id = ? AND ? BETWEEN start_s AND end_s",
            (moment.cue_id, video_id, moment.offset_s),
        ).fetchone()
        ok = start is not None
        if ok and moment.end_cue_id is not None:
            end = conn.execute(
                "SELECT 1 FROM cues WHERE id = ? AND video_id = ? AND seq >= ?"
                " AND ? BETWEEN start_s AND end_s AND ? >= ?",
                (moment.end_cue_id, video_id, start["seq"], moment.end_s, moment.end_s, moment.offset_s),
            ).fetchone()
            ok = end is not None
        (kept if ok else dropped).append(moment)
    return kept, dropped


def spanned(
    conn: sqlite3.Connection, video_id: int, answered: Sequence[tuple[int, float, int, str]]
) -> list[Moment]:
    """The model's `(cue_id, offset_s, end_cue_id, why)` as moments, each ending
    at its end cue's end. An end cue that is not this video's keeps no `end_s`
    and fails `check_receipts`."""
    out: list[Moment] = []
    for cue_id, offset_s, end_cue_id, why in answered:
        row = conn.execute(
            "SELECT end_s FROM cues WHERE id = ? AND video_id = ?", (end_cue_id, video_id)
        ).fetchone()
        end_s = float(row[0]) if row is not None else -1.0
        out.append(Moment(cue_id, offset_s, why, end_cue_id, end_s))
    return out


def moments_s(moments: Sequence[Moment]) -> float | None:
    """The seconds the moments cover, overlaps counted once; None when any
    moment has no end (a verdict written before spans)."""
    if any(m.end_s is None for m in moments):
        return None
    total = 0.0
    reach = -1.0
    for m in sorted(moments, key=lambda m: m.offset_s):
        end = float(m.end_s)  # type: ignore[arg-type]
        start = max(m.offset_s, reach)
        if end > start:
            total += end - start
            reach = end
    return total


def save(
    conn: sqlite3.Connection,
    video_id: int,
    *,
    score: int,
    reason: str,
    summary: str,
    moments: Sequence[Moment],
    profile_rev: int,
    model: str,
    explored: bool = False,
    matches: Sequence[Match] = (),
    overlaps: Sequence[Span] = (),
) -> None:
    """Insert or replace the video's verdict. A rerun keeps `notified_at`."""
    conn.execute(
        "INSERT INTO verdicts (video_id, score, reason, summary, moments, profile_rev,"
        " model, explored, matches, overlaps) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
        " ON CONFLICT (video_id) DO UPDATE SET score = excluded.score,"
        " reason = excluded.reason, summary = excluded.summary, moments = excluded.moments,"
        " profile_rev = excluded.profile_rev, model = excluded.model,"
        " explored = excluded.explored, matches = excluded.matches,"
        " overlaps = excluded.overlaps, created_at = unixepoch()",
        (
            video_id,
            score,
            reason,
            summary,
            json.dumps([m.__dict__ for m in moments]),
            profile_rev,
            model,
            int(explored),
            json.dumps([m.__dict__ for m in matches]),
            json.dumps([o.__dict__ for o in overlaps]),
        ),
    )


def get(conn: sqlite3.Connection, video_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM verdicts WHERE video_id = ?", (video_id,)).fetchone()


def moments_of(row: sqlite3.Row) -> list[Moment]:
    return [
        Moment(
            int(m["cue_id"]),
            float(m["offset_s"]),
            str(m["why"]),
            int(m["end_cue_id"]) if m.get("end_cue_id") is not None else None,
            float(m["end_s"]) if m.get("end_s") is not None else None,
        )
        for m in json.loads(row["moments"])
    ]


def overlaps_of(row: sqlite3.Row) -> list[Span]:
    return [
        Span(int(o["video_id"]), float(o["start_s"]), float(o["end_s"]), float(o["seen_s"]))
        for o in json.loads(row["overlaps"])
    ]


# A repeat that starts this soon after a moment's start still covers it: a
# chunk is 45 s at a 30 s stride, so a span's edges are that coarse.
SKIP_SLACK_S = 15.0


def start_after(
    offset_s: float, end_s: float | None, spans: Sequence[Span]
) -> tuple[float, list[Span], bool]:
    """Where a moment's link starts, the repeats it touches, and whether they
    cover all of it. The link starts past every repeat that covers its start;
    when they cover the whole moment, it starts where the moment does."""
    reach = end_s if end_s is not None else offset_s
    touching = [s for s in spans if s.start_s <= reach and s.end_s > offset_s]
    start = offset_s
    moved = True
    while moved:
        moved = False
        for s in touching:
            if s.start_s - SKIP_SLACK_S <= start < s.end_s:
                start, moved = s.end_s, True
    whole = end_s is not None and start >= end_s
    return (offset_s if whole else start), touching, whole


def matches_json(conn: sqlite3.Connection, rows: Sequence[sqlite3.Row]) -> list[list[dict[str, Any]]]:
    """Each row's matches as `{entry_id, text, direction, strength}`, the text
    read from `profile_entries` (a retired entry keeps its row and its text)."""
    stored = [json.loads(row["matches"]) for row in rows]
    ids = sorted({int(m["entry_id"]) for ms in stored for m in ms})
    texts = {
        int(r[0]): str(r[1])
        for r in conn.execute(
            "SELECT id, text FROM profile_entries WHERE id IN (SELECT value FROM json_each(?))",
            (json.dumps(ids),),
        )
    }
    return [
        [
            {
                "entry_id": int(m["entry_id"]),
                "text": texts[int(m["entry_id"])],
                "direction": m["direction"],
                "strength": int(m["strength"]),
            }
            for m in ms
            if int(m["entry_id"]) in texts
        ]
        for ms in stored
    ]


# ------------------------------------------------------------------ queue


def is_queued(conn: sqlite3.Connection, video_id: int) -> bool:
    """A `verdict` job for this video is waiting or running."""
    row = conn.execute(
        "SELECT 1 FROM jobs WHERE kind = 'verdict' AND state IN ('queued','running')"
        " AND json_extract(args_json, '$.video_id') = ? LIMIT 1",
        (video_id,),
    ).fetchone()
    return row is not None


def needs_verdict(conn: sqlite3.Connection, video_id: int) -> bool:
    """No verdict yet, or one whose receipts a reindex broke."""
    row = get(conn, video_id)
    if row is None:
        return True
    _, dropped = check_receipts(conn, video_id, moments_of(row))
    return bool(dropped)


def queue(conn: sqlite3.Connection, video_id: int) -> str | None:
    """Queue one `verdict` job for the video, unless one is already waiting.

    The item's `video_id` stays NULL on purpose: `job_items_one_inflight`
    would otherwise make a queued verdict refuse a reindex of its video, and
    the indexing item that called this is still `running` (index-schema §1.9).
    """
    if is_queued(conn, video_id):
        return None
    row = conn.execute("SELECT url FROM videos WHERE id = ?", (video_id,)).fetchone()
    if row is None:
        return None
    return jobs_store.create_job(
        conn,
        "verdict",
        {"video_id": video_id},
        [(str(row["url"]), None)],
        priority=VERDICT_PRIORITY,
    )


def queue_after_ready(conn: sqlite3.Connection, video_id: int) -> str | None:
    """The `_finalize` hook: queue a verdict when the video needs one."""
    if not needs_verdict(conn, video_id):
        return None
    return queue(conn, video_id)


def backfill(
    conn: sqlite3.Connection, limit: int, video_ids: Sequence[int] = (), *, rescore: bool = False
) -> tuple[list[str], int]:
    """Queue verdicts for indexed videos that have none, newest first.

    Returns (job ids, videos still without a verdict or a queued one). Rerun
    to continue: a video with a verdict or a waiting job is never picked
    twice. With `video_ids`, those videos are queued even when they already
    have a verdict — the explicit rerun. With `rescore`, the newest videos
    that have a verdict are queued instead, to rewrite them under the current
    prompt; it does not continue, a second run queues the same videos again.
    """
    limit = max(1, min(int(limit), BACKFILL_MAX))
    marks = ",".join("?" for _ in QUERYABLE_INDEX_STATES)
    if video_ids:
        candidates = [
            int(r[0])
            for r in conn.execute(
                f"SELECT id FROM videos WHERE index_state IN ({marks})"
                " AND id IN (SELECT value FROM json_each(?))",
                (*QUERYABLE_INDEX_STATES, json.dumps(list(video_ids))),
            )
        ]
    else:
        judged = "EXISTS" if rescore else "NOT EXISTS"
        candidates = [
            int(r[0])
            for r in conn.execute(
                f"SELECT id FROM videos v WHERE index_state IN ({marks})"
                f" AND {judged} (SELECT 1 FROM verdicts WHERE video_id = v.id)"
                " ORDER BY published_at DESC, id DESC",
                QUERYABLE_INDEX_STATES,
            )
        ]
    queued: list[str] = []
    waiting = 0
    for video_id in candidates:
        if is_queued(conn, video_id):
            continue
        if len(queued) >= limit:
            if rescore:
                break  # the rest of the judged corpus is not waiting for anything
            waiting += 1
            continue
        job = queue(conn, video_id)
        if job is not None:
            queued.append(job)
    return queued, waiting

