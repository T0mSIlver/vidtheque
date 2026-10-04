"""Picks an agent adds on top of the verdicts (companion.md §6.4, #200).

The `recommend` tool writes them; the week's feed shows them above its ranked
verdicts, and the ledger counts their hit rate apart from the pipeline's.
Sync, connection-first, like `store`.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Sequence

from ..db.queries import QUERYABLE_INDEX_STATES
from ..profile import ledger, topics
from . import store, week as verdicts_week

OWNER_ID = 1
SOURCE = "claude"
# Server-side, whatever the agent is told: a pick is scarce or it is a second feed.
PER_DAY = 5
MOMENTS_MAX = 3
REASON_CHARS = 200
WHY_CHARS = 120
# The read the routine starts from: verdicts written in the last `days`, of
# videos published in the last month, so a rescore of an old talk stays out.
DAYS_DEFAULT = 2
DAYS_MAX = 7
RECENT_PUBLISHED_S = 30 * 86_400
LIMIT_DEFAULT = 30
LIMIT_MAX = 60
CHARS_MAX = 24_000
# How far back the read reports what the owner did with earlier picks.
FEEDBACK_DAYS = 14


class PickRefused(Exception):
    def __init__(self, code: str, message: str, next_hint: str) -> None:
        super().__init__(message)
        self.code = code
        self.next_hint = next_hint


@dataclass(frozen=True)
class AskedMoment:
    start_s: float
    end_s: float
    why: str


@dataclass
class Saved:
    pick_id: int
    replaced: bool
    today: int
    kept: list[store.Moment] = field(default_factory=list)
    dropped: list[tuple[AskedMoment, str]] = field(default_factory=list)


def local_day(ts: float) -> str:
    return datetime.fromtimestamp(ts).date().isoformat()


def check_text(name: str, text: str, chars: int) -> str:
    """One line, at most `chars`, and no job-search or pay words (§2.1's rule)."""
    line = " ".join(text.split())
    if not line:
        raise PickRefused("E_BAD_PARAM", f"{name} is empty.", f"give {name} as one plain line.")
    if len(line) > chars:
        raise PickRefused(
            "E_BAD_PARAM", f"{name} is {len(line)} characters; at most {chars}.", f"shorten {name} to one line."
        )
    hit = topics.denied(line, topics.DENY_TERMS)
    if hit is not None:
        raise PickRefused(
            "E_BAD_PARAM",
            f"{name} hits {hit!r}: a pick carries no job-search, pay or employer details.",
            f"say why the video is worth watching without {hit!r}.",
        )
    return line


def receipts(
    conn: sqlite3.Connection, video_id: int, asked: Sequence[AskedMoment]
) -> tuple[list[store.Moment], list[tuple[AskedMoment, str]]]:
    """Each asked span as a moment on the video's cues, then the verdicts' receipt check.

    The start must fall inside a cue, the end inside the same cue or a later
    one, and the moment ends where that cue ends, as a verdict's does. Nothing
    is moved to a nearby cue: a span that misses is dropped and named.
    """
    built: list[tuple[AskedMoment, store.Moment]] = []
    dropped: list[tuple[AskedMoment, str]] = []
    for m in asked:
        start = conn.execute(
            "SELECT id, seq FROM cues WHERE video_id = ? AND ? BETWEEN start_s AND end_s"
            " ORDER BY seq LIMIT 1",
            (video_id, m.start_s),
        ).fetchone()
        if start is None:
            dropped.append((m, f"no cue holds {m.start_s:.1f} s"))
            continue
        end = conn.execute(
            "SELECT id, end_s FROM cues WHERE video_id = ? AND seq >= ? AND ? BETWEEN start_s AND end_s"
            " ORDER BY seq LIMIT 1",
            (video_id, start["seq"], m.end_s),
        ).fetchone()
        if end is None:
            dropped.append((m, f"no cue at or after the start holds {m.end_s:.1f} s"))
            continue
        built.append((m, store.Moment(int(start["id"]), m.start_s, m.why, int(end["id"]), float(end["end_s"]))))
    kept, failed = store.check_receipts(conn, video_id, [b for _, b in built])
    lost = {id(b) for b in failed}
    dropped.extend((m, "failed the receipt check") for m, b in built if id(b) in lost)
    return kept, dropped


def save(
    conn: sqlite3.Connection,
    video_id: int,
    reason: str,
    asked: Sequence[AskedMoment],
    *,
    client: str | None,
    now: float | None = None,
    source: str = SOURCE,
) -> Saved:
    """Store today's pick of the video, or replace it: one row per video a day.

    A replacement does not count against the day's `PER_DAY`; a new pick past
    it is refused, so nothing is written.
    """
    at = time.time() if now is None else now
    day = local_day(at)
    existing = conn.execute(
        "SELECT id FROM picks WHERE owner_id = ? AND source = ? AND day = ? AND video_id = ?",
        (OWNER_ID, source, day, video_id),
    ).fetchone()
    today = int(
        conn.execute(
            "SELECT count(*) FROM picks WHERE owner_id = ? AND source = ? AND day = ?",
            (OWNER_ID, source, day),
        ).fetchone()[0]
    )
    if existing is None and today >= PER_DAY:
        raise PickRefused(
            "E_PICK_LIMIT",
            f"{today} picks already today ({day}); at most {PER_DAY} a day.",
            "keep the best: call recommend bare to see today's picks; picking one again replaces it.",
        )
    kept, dropped = receipts(conn, video_id, asked)
    moments = json.dumps([m.__dict__ for m in kept])
    if existing is None:
        pick_id = conn.execute(
            "INSERT INTO picks (owner_id, video_id, day, source, reason, moments, client, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (OWNER_ID, video_id, day, source, reason, moments, client, int(at)),
        ).lastrowid
        today += 1
    else:
        pick_id = int(existing["id"])
        conn.execute(
            "UPDATE picks SET reason = ?, moments = ?, client = ?, created_at = ? WHERE id = ?",
            (reason, moments, client, int(at), pick_id),
        )
    return Saved(int(pick_id), existing is not None, today, kept, dropped)


# ------------------------------------------------------------------ the read


def recent_verdicts(
    conn: sqlite3.Connection, days: int, limit: int, offset: int, now: float
) -> tuple[list[sqlite3.Row], bool]:
    """Verdicts written in the last `days` of videos published in the last
    month, best first: score, then newest. (rows, has_more)."""
    marks = ",".join("?" for _ in QUERYABLE_INDEX_STATES)
    rows = conn.execute(
        "SELECT v.id, v.public_id, v.title, v.channel_name, v.duration_s, v.published_at,"
        " d.score, d.reason, d.summary, d.moments, d.created_at, f.state AS feedback FROM verdicts d"
        " JOIN videos v ON v.id = d.video_id"
        " LEFT JOIN feedback f ON f.video_id = v.id AND f.owner_id = v.owner_id"
        f" WHERE v.owner_id = ? AND v.index_state IN ({marks}) AND d.created_at >= ?"
        " AND COALESCE(v.published_at, d.created_at) >= ?"
        " ORDER BY d.score DESC, COALESCE(v.published_at, d.created_at) DESC, v.id DESC"
        " LIMIT ? OFFSET ?",
        (OWNER_ID, *QUERYABLE_INDEX_STATES, int(now - days * 86_400), int(now - RECENT_PUBLISHED_S), limit + 1, offset),
    ).fetchall()
    return rows[:limit], len(rows) > limit


@dataclass(frozen=True)
class Earlier:
    day: str
    public_id: str
    title: str
    channel: str | None
    reason: str
    feedback: str
    kept: bool
    opened: bool


def _kept_and_opened(
    conn: sqlite3.Connection, rows: Sequence[sqlite3.Row]
) -> tuple[dict[int, bool], set[int]]:
    """Per picked video: kept as §3.3 counts a hit, and opened since it was picked."""
    ids = sorted({int(r["video_id"]) for r in rows})
    spans: dict[int, list[tuple[float, float]]] = {}
    for r in conn.execute(
        "SELECT video_id, offset_s, watched_s FROM signals WHERE owner_id = ? AND kind = 'watch'"
        " AND watched_s IS NOT NULL AND video_id IN (SELECT value FROM json_each(?))",
        (OWNER_ID, json.dumps(ids)),
    ):
        start = float(r["offset_s"] or 0)
        spans.setdefault(int(r["video_id"]), []).append((start, start + float(r["watched_s"])))
    opened = {
        int(r["video_id"])
        for r in rows
        if conn.execute(
            "SELECT 1 FROM signals WHERE owner_id = ? AND video_id = ? AND at >= ?"
            " AND kind IN ('open','watch','ask_claude') LIMIT 1",
            (OWNER_ID, int(r["video_id"]), int(r["created_at"])),
        ).fetchone()
        is not None
    }
    kept: dict[int, bool] = {}
    for r in rows:
        vid = int(r["video_id"])
        moments = [(m.offset_s, m.end_s) for m in _moments(r)]
        kept[vid] = r["feedback"] == "up" or ledger.kept(moments, float(r["duration_s"] or 0), spans.get(vid, []))
    return kept, opened


def _moments(row: sqlite3.Row) -> list[store.Moment]:
    """The pick's own moments, or its verdict's when it named none."""
    own = store.moments_of({"moments": row["moments"]})  # type: ignore[arg-type]
    if own or row["verdict_moments"] is None:
        return own
    return store.moments_of({"moments": row["verdict_moments"]})  # type: ignore[arg-type]


_PICK_ROWS = (
    "SELECT p.id, p.day, p.reason, p.moments, p.created_at, p.video_id, v.public_id, v.title,"
    " v.channel_name, v.duration_s, v.published_at, d.moments AS verdict_moments, d.score,"
    " COALESCE(f.state, 'none') AS feedback FROM picks p JOIN videos v ON v.id = p.video_id"
    " LEFT JOIN verdicts d ON d.video_id = p.video_id"
    " LEFT JOIN feedback f ON f.video_id = p.video_id AND f.owner_id = p.owner_id"
    " WHERE p.owner_id = ? AND p.source = ?"
)


def earlier(conn: sqlite3.Connection, now: float, days: int = FEEDBACK_DAYS) -> list[Earlier]:
    """The picks of the last `days`, newest first, with what the owner did with each."""
    since = (date.fromisoformat(local_day(now)) - timedelta(days=days)).isoformat()
    rows = conn.execute(
        _PICK_ROWS + " AND p.day >= ? ORDER BY p.day DESC, p.id DESC LIMIT ?",
        (OWNER_ID, SOURCE, since, PER_DAY * (days + 1)),
    ).fetchall()
    kept, opened = _kept_and_opened(conn, rows)
    return [
        Earlier(
            day=str(r["day"]),
            public_id=str(r["public_id"]),
            title=str(r["title"] or ""),
            channel=r["channel_name"],
            reason=str(r["reason"]),
            feedback=str(r["feedback"]),
            kept=kept[int(r["video_id"])],
            opened=int(r["video_id"]) in opened,
        )
        for r in rows
    ]


# ------------------------------------------------------------------ the feed and the ledger


def of_week(conn: sqlite3.Connection, week: str) -> list[dict[str, Any]]:
    """The week's picks, newest first, a video picked twice shown once (its newest)."""
    first, last = verdicts_week.days(week)[0], verdicts_week.days(week)[-1]
    rows = conn.execute(
        _PICK_ROWS + " AND p.day BETWEEN ? AND ? ORDER BY p.day DESC, p.created_at DESC, p.id DESC",
        (OWNER_ID, SOURCE, first, last),
    ).fetchall()
    out: list[dict[str, Any]] = []
    seen: set[int] = set()
    for r in rows:
        if int(r["video_id"]) in seen:
            continue
        seen.add(int(r["video_id"]))
        moments = _moments(r)
        out.append(
            {
                "video_id": str(r["public_id"]),
                "title": str(r["title"] or ""),
                "channel": r["channel_name"],
                "duration_s": float(r["duration_s"] or 0),
                "published_at": r["published_at"],
                "source": SOURCE,
                "day": str(r["day"]),
                "reason": str(r["reason"]),
                "score": None if r["score"] is None else int(r["score"]),
                "moments": [
                    {"offset_s": m.offset_s, "end_s": m.end_s, "why": m.why} for m in moments
                ],
                "moments_s": store.moments_s(moments) if moments else None,
            }
        )
    return out


def week_rate(conn: sqlite3.Connection, week: str) -> dict[str, Any]:
    """The week's picked videos and the share kept, as §3.3 counts a hit."""
    first, last = verdicts_week.days(week)[0], verdicts_week.days(week)[-1]
    rows = conn.execute(
        _PICK_ROWS + " AND p.day BETWEEN ? AND ? ORDER BY p.day DESC, p.id DESC",
        (OWNER_ID, SOURCE, first, last),
    ).fetchall()
    newest: dict[int, sqlite3.Row] = {}
    for r in rows:
        newest.setdefault(int(r["video_id"]), r)
    kept, _ = _kept_and_opened(conn, list(newest.values()))
    n = sum(1 for v in kept.values() if v)
    return {"source": SOURCE, "picked": len(newest), "kept": n, "rate": round(n / len(newest), 3) if newest else None}
