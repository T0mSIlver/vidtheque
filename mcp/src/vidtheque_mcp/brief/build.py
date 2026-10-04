"""What the weekly brief holds, and the reads that assemble it (companion.md §6.1).

Sync, connection-first. `freeze` picks what the brief keeps as it was on
Sunday (the three videos, the skip audit, what speakers said); `assemble`
adds what is read live each time the page opens (the channel report, this
week's profile changes, the check-in and the audit answers).
"""

from __future__ import annotations

import json
import random
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any

from ..discover import picks as discover_picks
from ..profile import ledger
from ..text import deeplink
from ..verdicts import store as verdicts_store

PICKS = 3
AUDIT = 3
# "What speakers said": the top entries by weight, the videos that hit each,
# and the cues around their moments, all bounded whatever the week held.
SAID_ENTRIES = 3
SAID_VIDEOS = 4
SAID_POINTS = 3
CUE_CHARS = 600
SUMMARY_CHARS = 600
# The channel report reads a month; a channel is flagged for pausing only when
# it had enough videos to judge and none of them counted.
REPORT_DAYS = 30
PAUSE_MIN_VIDEOS = 4
CHANNELS_MAX = 100
CHANGES_MAX = 20
# How far a proposed reweight moves a "less of this" entry toward 0, the
# nightly update's own step (§2.4).
PROPOSAL_STEP = 0.3
ENGAGED_KINDS = ("watch", "ask_claude")


@dataclass(frozen=True)
class Week:
    key: str  # the Monday, YYYY-MM-DD, as #156's week ranks key it
    since_at: int
    until_at: int  # the next Monday; the brief is built before it


def week_of(now: datetime) -> Week:
    """The calendar week (Monday to Sunday, local time) that holds ``now``.

    TODO(#156): use `verdicts/week.py`'s helper once it lands; #156 owns the week.
    """
    monday = now.date() - timedelta(days=now.weekday())
    start = datetime.combine(monday, time(), now.tzinfo)
    end = datetime.combine(monday + timedelta(days=7), time(), now.tzinfo)
    return Week(monday.isoformat(), int(start.timestamp()), int(end.timestamp()))


def parse_week(key: str) -> date | None:
    try:
        day = date.fromisoformat(key)
    except ValueError:
        return None
    return day if day.weekday() == 0 else None


# ------------------------------------------------------------ the frozen part


def picks(conn: sqlite3.Connection, week: Week, owner_id: int = 1) -> list[str]:
    """The week's three videos most worth the time, as public ids.

    Score first, then how strongly the profile's wanted entries hit it, then
    newest. #156's weekly ranks replace this order once they land.
    """
    rows = conn.execute(
        "SELECT v.public_id, d.score, d.matches FROM verdicts d JOIN videos v ON v.id = d.video_id"
        " WHERE v.owner_id = ? AND d.score >= 2 AND v.published_at >= ? AND v.published_at < ?"
        " ORDER BY d.score DESC, v.published_at DESC, v.id DESC LIMIT 200",
        (owner_id, week.since_at, week.until_at),
    ).fetchall()

    def pull(row: sqlite3.Row) -> int:
        return sum(
            int(m["strength"]) * (1 if m["direction"] == "up" else -1) for m in json.loads(row["matches"])
        )

    ranked = sorted(rows, key=lambda r: (-int(r["score"]), -pull(r)))  # stable: newest stays first
    return [str(r["public_id"]) for r in ranked[:PICKS]]


def audit_picks(conn: sqlite3.Connection, week: Week, rng: random.Random, owner_id: int = 1) -> list[str]:
    """Three random skipped videos of the week (score 0–1) the owner has not judged yet."""
    rows = [
        str(r[0])
        for r in conn.execute(
            "SELECT v.public_id FROM verdicts d JOIN videos v ON v.id = d.video_id"
            " WHERE v.owner_id = ? AND d.score <= 1 AND v.published_at >= ? AND v.published_at < ?"
            " AND NOT EXISTS (SELECT 1 FROM skip_verdicts s WHERE s.owner_id = v.owner_id AND s.video_id = v.id)"
            " ORDER BY v.id LIMIT 500",
            (owner_id, week.since_at, week.until_at),
        )
    ]
    return rng.sample(rows, min(AUDIT, len(rows)))


def said_inputs(conn: sqlite3.Connection, week: Week, owner_id: int = 1) -> list[dict[str, Any]]:
    """For each top wanted entry, the week's videos that hit it and the cues behind their moments."""
    topics: list[dict[str, Any]] = []
    entries = conn.execute(
        "SELECT id, text, weight FROM profile_entries WHERE owner_id = ? AND retired_at IS NULL AND weight > 0"
        " ORDER BY weight DESC, id",
        (owner_id,),
    ).fetchall()
    for entry in entries:
        if len(topics) >= SAID_ENTRIES:
            break
        rows = conn.execute(
            "SELECT d.*, v.public_id, v.title, v.channel_name, json_extract(m.value, '$.strength') AS strength"
            " FROM verdicts d JOIN videos v ON v.id = d.video_id, json_each(d.matches) m"
            " WHERE v.owner_id = ? AND v.published_at >= ? AND v.published_at < ?"
            " AND json_extract(m.value, '$.entry_id') = ? AND json_extract(m.value, '$.direction') = 'up'"
            " ORDER BY strength DESC, d.score DESC, v.published_at DESC LIMIT ?",
            (owner_id, week.since_at, week.until_at, int(entry["id"]), SAID_VIDEOS),
        ).fetchall()
        videos = []
        for row in rows:
            kept, _ = verdicts_store.check_receipts(conn, int(row["video_id"]), verdicts_store.moments_of(row))
            cues = [_cue(conn, m.cue_id) for m in kept]
            cues = [c for c in cues if c is not None]
            if cues:
                videos.append(
                    {
                        "video_id": row["public_id"],
                        "title": row["title"] or "",
                        "channel": row["channel_name"] or "",
                        "summary": (row["summary"] or "")[:SUMMARY_CHARS],
                        "cues": cues,
                    }
                )
        if videos:
            topics.append({"entry_id": int(entry["id"]), "text": entry["text"], "videos": videos})
    return topics


def _cue(conn: sqlite3.Connection, cue_id: int) -> dict[str, Any] | None:
    """The moment's cue and the one after it, as one quotable passage."""
    row = conn.execute("SELECT id, video_id, seq, start_s, text FROM cues WHERE id = ?", (cue_id,)).fetchone()
    if row is None:
        return None
    after = conn.execute(
        "SELECT text FROM cues WHERE video_id = ? AND seq = ?", (row["video_id"], int(row["seq"]) + 1)
    ).fetchone()
    text = " ".join([row["text"], after["text"] if after else ""]).strip()[:CUE_CHARS]
    return {"cue_id": int(row["id"]), "offset_s": float(row["start_s"]), "text": text}


SAID_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["topics"],
    "properties": {
        "topics": {
            "type": "array",
            "maxItems": SAID_ENTRIES * 2,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["entry_id", "points"],
                "properties": {
                    "entry_id": {"type": "integer"},
                    "points": {
                        "type": "array",
                        "maxItems": SAID_POINTS * 2,
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["cue_id", "said"],
                            "properties": {
                                "cue_id": {"type": "integer"},
                                "said": {"type": "string", "minLength": 1, "maxLength": 300},
                            },
                        },
                    },
                    "disagreement": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["cue_ids", "about"],
                        "properties": {
                            "cue_ids": {"type": "array", "items": {"type": "integer"}, "minItems": 2, "maxItems": 2},
                            "about": {"type": "string", "minLength": 1, "maxLength": 300},
                        },
                    },
                },
            },
        }
    },
}

SAID_SYSTEM = f"""You write one section of a person's weekly video brief: what
speakers said this week about the topics they care about most. For each topic
you get the videos that touched it, each with passages, every passage under a
cue_id. Answer with one JSON object, {{"topics": [...]}}, one item per topic
worth a line:
- entry_id: the topic's id, as given.
- points: at most {SAID_POINTS}, each {{"cue_id", "said"}}: one claim, number or
  technique a speaker stated, in at most 25 words, naming the speaker or
  channel. cue_id is the passage that says it; use only cue_ids given for that
  topic, never invent one.
- disagreement, only when two passages from different videos contradict each
  other: {{"cue_ids": [a, b], "about": "what they disagree on, at most 20 words"}}.
Plain words. No "the speaker discusses", no hedging, no closing sentence. A topic
with nothing concrete said gets no item."""


def said_prompt(topics: list[dict[str, Any]]) -> str:
    return json.dumps({"topics": topics}, ensure_ascii=False)


def check_said(topics: list[dict[str, Any]], answer: dict[str, Any]) -> list[dict[str, Any]]:
    """The model's answer, kept only where each receipt is a cue it was given for that topic.

    A point citing any other cue is dropped, never repaired (§3.1's rule); a
    disagreement needs two given cues from two different videos.
    """
    given = {t["entry_id"]: t for t in topics}
    kept: list[dict[str, Any]] = []
    seen: set[int] = set()
    for item in answer.get("topics", []):
        topic = given.get(item["entry_id"])
        if topic is None or item["entry_id"] in seen:
            continue
        cues = {
            c["cue_id"]: (v, c) for v in topic["videos"] for c in v["cues"]
        }
        points = []
        for point in item["points"]:
            if point["cue_id"] in cues and len(points) < SAID_POINTS:
                video, cue = cues[point["cue_id"]]
                points.append({**_receipt(video, cue), "said": point["said"].strip()})
        disagreement = None
        pair = (item.get("disagreement") or {}).get("cue_ids") or []
        if len(pair) == 2 and all(c in cues for c in pair):
            sides = [cues[c] for c in pair]
            if sides[0][0]["video_id"] != sides[1][0]["video_id"]:
                disagreement = {
                    "about": item["disagreement"]["about"].strip(),
                    "sides": [_receipt(v, c) for v, c in sides],
                }
        if points or disagreement:
            seen.add(item["entry_id"])
            kept.append({"entry_id": topic["entry_id"], "text": topic["text"], "points": points, "disagreement": disagreement})
    return kept


def _receipt(video: dict[str, Any], cue: dict[str, Any]) -> dict[str, Any]:
    return {
        "video_id": video["video_id"],
        "title": video["title"],
        "channel": video["channel"],
        "cue_id": cue["cue_id"],
        "offset_s": cue["offset_s"],
        "url": deeplink(video["video_id"], cue["offset_s"]),
    }


def store(
    conn: sqlite3.Connection, week: Week, body: dict[str, Any], model: str | None, owner_id: int = 1
) -> bool:
    """Keep the week's brief. False when one already exists: a week is built once."""
    return (
        conn.execute(
            "INSERT INTO briefs (owner_id, week, since_at, until_at, body, model) VALUES (?, ?, ?, ?, ?, ?)"
            " ON CONFLICT (owner_id, week) DO NOTHING",
            (owner_id, week.key, week.since_at, week.until_at, json.dumps(body), model),
        ).rowcount
        == 1
    )


# --------------------------------------------------------------- the live part


def assemble(conn: sqlite3.Connection, row: sqlite3.Row, now: int, owner_id: int = 1) -> dict[str, Any]:
    body = json.loads(row["body"])
    week = row["week"]
    previous = conn.execute(
        "SELECT week FROM briefs WHERE owner_id = ? AND week < ? ORDER BY week DESC LIMIT 1", (owner_id, week)
    ).fetchone()
    checkin = conn.execute(
        "SELECT rating, missing, at FROM checkins WHERE owner_id = ? AND week = ?", (owner_id, week)
    ).fetchone()
    return {
        "week": week,
        "since": int(row["since_at"]),
        "until": int(row["until_at"]),
        "built_at": int(row["created_at"]),
        "previous_week": previous[0] if previous else None,
        "picks": [p for p in (_pick(conn, vid, owner_id) for vid in body.get("picks", [])) if p is not None],
        "said": body.get("said"),
        "said_note": body.get("said_note"),
        "channels": channel_report(conn, now, owner_id),
        "profile_changes": profile_changes(conn, int(row["since_at"]), int(row["until_at"]), owner_id),
        "audit": [a for a in (_audit(conn, vid, owner_id) for vid in body.get("audit", [])) if a is not None],
        "checkin": dict(checkin) if checkin else None,
        "ledger": _ledger(conn, row, now, owner_id),
        # Discovery's week (companion.md §6.2), read when asked.
        "outside": discover_picks.week_payload(conn, week, owner_id),
    }


def _ledger(conn: sqlite3.Connection, row: sqlite3.Row, now: int, owner_id: int) -> dict[str, Any] | None:
    """The brief's week in the valued-time ledger (#157), in `GET valued-time`'s shape.

    None for a week older than the ledger reads.
    """
    read = ledger.weeks(conn, datetime.fromtimestamp(now).astimezone(), owner_id=owner_id)
    week = next((w for w in read["weeks"] if w["start"] == int(row["since_at"])), None)
    return {"regret_target": read["regret_target"], "weeks": [week]} if week else None


def _verdict_row(conn: sqlite3.Connection, public_id: str, owner_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT d.*, v.public_id, v.title, v.channel_name, v.duration_s, v.published_at"
        " FROM verdicts d JOIN videos v ON v.id = d.video_id WHERE v.public_id = ? AND v.owner_id = ?",
        (public_id, owner_id),
    ).fetchone()


def _video(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "video_id": row["public_id"],
        "title": (row["title"] or "")[:200],
        "channel": row["channel_name"],
        "duration_s": float(row["duration_s"] or 0),
        "published_at": row["published_at"],
        "score": int(row["score"]),
        "reason": row["reason"],
    }


def _pick(conn: sqlite3.Connection, public_id: str, owner_id: int) -> dict[str, Any] | None:
    row = _verdict_row(conn, public_id, owner_id)
    if row is None:  # deleted since Sunday
        return None
    kept, _ = verdicts_store.check_receipts(conn, int(row["video_id"]), verdicts_store.moments_of(row))
    return {
        **_video(row),
        "moments": [
            {"cue_id": m.cue_id, "offset_s": m.offset_s, "why": m.why, "url": deeplink(row["public_id"], m.offset_s)}
            for m in kept
        ],
    }


def _audit(conn: sqlite3.Connection, public_id: str, owner_id: int) -> dict[str, Any] | None:
    row = _verdict_row(conn, public_id, owner_id)
    if row is None:
        return None
    answer = conn.execute(
        "SELECT answer FROM skip_verdicts WHERE owner_id = ? AND video_id = ?", (owner_id, int(row["video_id"]))
    ).fetchone()
    return {
        **_video(row),
        "sunk_by": sunk_by(conn, row),
        "answer": answer[0] if answer else None,
    }


def sunk_by(conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any] | None:
    """The entry that sank a skipped verdict: its strongest "less of this" match."""
    down = [m for m in verdicts_store.matches_json(conn, [row])[0] if m["direction"] == "down"]
    return max(down, key=lambda m: m["strength"]) if down else None


def channel_report(conn: sqlite3.Connection, now: int, owner_id: int = 1) -> list[dict[str, Any]]:
    """Per followed channel, the last 30 days: videos, share scored 2+, share watched or liked.

    `suggest_pause` flags a channel that had enough videos and none scored 2+
    or was watched or liked. It is a suggestion: nothing here pauses a follow.
    """
    since = now - REPORT_DAYS * 86_400
    marks = ",".join("?" for _ in ENGAGED_KINDS)
    rows = conn.execute(
        "SELECT c.slug, c.title, f.state,"
        " COUNT(v.id) AS videos,"
        " COUNT(d.video_id) AS judged,"
        " SUM(d.score >= 2) AS worth,"
        " SUM(d.video_id IS NOT NULL AND ("
        "   EXISTS (SELECT 1 FROM feedback b WHERE b.owner_id = c.owner_id AND b.video_id = v.id AND b.state = 'up')"
        f"   OR EXISTS (SELECT 1 FROM signals s WHERE s.video_id = v.id AND s.kind IN ({marks})))) AS engaged"
        " FROM follows f JOIN collections c ON c.id = f.collection_id"
        " LEFT JOIN collection_videos cv ON cv.collection_id = c.id"
        " LEFT JOIN videos v ON v.id = cv.video_id AND v.published_at >= ?"
        " LEFT JOIN verdicts d ON d.video_id = v.id"
        " WHERE c.owner_id = ? GROUP BY c.id ORDER BY videos DESC, c.title LIMIT ?",
        (*ENGAGED_KINDS, since, owner_id, CHANNELS_MAX),
    ).fetchall()
    report = []
    for r in rows:
        judged, worth, engaged = int(r["judged"]), int(r["worth"] or 0), int(r["engaged"] or 0)
        report.append(
            {
                "slug": r["slug"],
                "title": r["title"],
                "state": r["state"],
                "videos": int(r["videos"]),
                "judged": judged,
                "worth_share": round(worth / judged, 2) if judged else None,
                "engaged_share": round(engaged / judged, 2) if judged else None,
                "suggest_pause": r["state"] != "paused" and judged >= PAUSE_MIN_VIDEOS and worth == 0 and engaged == 0,
            }
        )
    report.sort(key=lambda c: not c["suggest_pause"])  # stable: flagged first, then by videos
    return report


def profile_changes(conn: sqlite3.Connection, since: int, until: int, owner_id: int = 1) -> list[dict[str, Any]]:
    """What the nightly update changed this week, with its reasons, newest first.

    `reverted` is true once a revert undid this event: by its id, or by rolling
    back to a revision before it.
    """
    rows = conn.execute(
        # The store's own revert reasons name what they undid (`profile/store.py`).
        "SELECT e.*, (SELECT 1 FROM profile_events r WHERE r.entry_id = e.entry_id AND r.op = 'revert'"
        "  AND r.id > e.id AND (r.reason = 'revert of event ' || e.id"
        "   OR (r.reason LIKE 'revert to revision %' AND CAST(substr(r.reason, 20) AS INTEGER) < e.id))"
        "  LIMIT 1) AS reverted"
        " FROM profile_events e JOIN profile_entries p ON p.id = e.entry_id"
        " WHERE p.owner_id = ? AND e.actor = 'nightly' AND e.at >= ? AND e.at < ?"
        " ORDER BY e.id DESC LIMIT ?",
        (owner_id, since, until, CHANGES_MAX),
    ).fetchall()
    return [
        {
            "event_id": int(r["id"]),
            "at": int(r["at"]),
            "op": r["op"],
            "entry_id": int(r["entry_id"]),
            "before": json.loads(r["before"]) if r["before"] else None,
            "after": json.loads(r["after"]) if r["after"] else None,
            "reason": r["reason"],
            "reverted": bool(r["reverted"]),
        }
        for r in rows
    ]


def proposal(conn: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any] | None:
    """For a skip the owner says was wrong: ease the entry that sank it by one nightly step.

    Only proposed; the owner applies it with an ordinary profile reweight.
    """
    sunk = sunk_by(conn, row)
    if sunk is None:
        return None
    entry = conn.execute(
        "SELECT weight FROM profile_entries WHERE id = ? AND retired_at IS NULL", (sunk["entry_id"],)
    ).fetchone()
    if entry is None or float(entry["weight"]) >= 0:
        return None
    weight = float(entry["weight"])
    return {
        "entry_id": sunk["entry_id"],
        "text": sunk["text"],
        "weight": weight,
        "to": round(min(0.0, weight + PROPOSAL_STEP), 2),
    }
