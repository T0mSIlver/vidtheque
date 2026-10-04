"""The weekly speaker suggestion — companion.md §6.2.

From the talks the owner liked in the last 30 days (thumbed up, or kept per
the ledger), one model call names who presented; a flat YouTube search per
name, at most two, looks for their own channel or other talks. The first name
with either, not followed and never suggested before, is the week's one
suggestion. A week that found none makes no second model call.
"""

from __future__ import annotations

import asyncio
import json
import re
import sqlite3
from typing import TYPE_CHECKING, Any

from ..pipeline.sources import RateLimited, SearchHit
from ..profile import ledger
from . import picks

if TYPE_CHECKING:
    from .scout import Run, Scout

LIKED_DAYS = 30
LIKED_MAX = 10
NAMES_MAX = 5
LOOKUPS = 2
RESULTS = 10
TALKS_MIN = 2
TALKS_SHOWN = 3
CHAPTERS_MAX = 20
OCR_UNTIL_S = 120.0
OCR_LINES = 30
OCR_CHARS = 1_500

NAMES_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["speakers"],
    "properties": {
        "speakers": {
            "type": "array",
            "maxItems": NAMES_MAX,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "video_id"],
                "properties": {
                    "name": {"type": "string", "minLength": 1, "maxLength": 80},
                    "video_id": {"type": "string", "minLength": 1, "maxLength": 64},
                },
            },
        }
    },
}

SYSTEM = f"""You read the titles, chapter titles and on-screen text of talks
one person liked, and name the people who presented them. Answer with one JSON
object, {{"speakers": [{{"name": "...", "video_id": "..."}}]}}, at most
{NAMES_MAX}, the clearest first. A speaker is a person who gave the talk or
was interviewed in it: a full name as written in the material, never a host,
an interviewer, a company, a channel or a product. Use only names the material
states; when no person is named, answer {{"speakers": []}}."""


def key(name: str) -> str:
    return "".join(ch for ch in name.casefold() if ch.isalnum())


async def suggest(scout: Scout, run: Run) -> None:
    db, owner_id = scout.db, scout.owner_id
    if await db.read(lambda c: _tried(c, run.week.key, run.week.since_at, owner_id)):
        return
    now = int(scout.clock().timestamp())
    talks = await db.read(lambda c: liked_talks(c, now, owner_id))
    if not talks:
        return  # nothing liked yet this month; tomorrow may have something
    try:
        await _suggest(scout, run, talks)
    except RateLimited:
        run.speaker = "blocked"
        raise


async def _suggest(scout: Scout, run: Run, talks: list[dict[str, Any]]) -> None:
    db, owner_id = scout.db, scout.owner_id
    shown = [{k: v for k, v in t.items() if not k.startswith("_")} for t in talks]
    answer = await scout.model.complete(
        json.dumps(shown, ensure_ascii=False),
        system=SYSTEM,
        schema=NAMES_SCHEMA,
        purpose="speaker_names",
    )
    by_id = {t["video_id"]: t for t in talks}
    names = await db.read(lambda c: _fresh_names(c, answer["speakers"], by_id, owner_id))
    for name, talk in names[:LOOKUPS]:
        run.requests += 1
        hits = await asyncio.to_thread(scout.source.search, f'"{name}"', RESULTS)
        found = await db.read(lambda c: _lookup(c, name, talk, hits, owner_id))
        if found is None:
            continue
        await db.write(lambda c: _store(c, run.week.key, name, talk, found, owner_id))
        run.speaker = "suggested"
        return
    run.speaker = "none"


def _tried(conn: sqlite3.Connection, week: str, since_at: int, owner_id: int) -> bool:
    if conn.execute(
        "SELECT 1 FROM speaker_suggestions WHERE owner_id = ? AND week = ?", (owner_id, week)
    ).fetchone():
        return True
    if conn.execute(
        "SELECT 1 FROM scout_runs WHERE owner_id = ? AND started_at >= ? AND speaker = 'none'",
        (owner_id, since_at),
    ).fetchone():
        return True
    spent = picks.week_counts(conn, week, since_at, owner_id)
    return spent["calls"] >= picks.CALLS_PER_WEEK


# ---------------------------------------------------------------- the talks


def liked_talks(conn: sqlite3.Connection, now: int, owner_id: int = 1) -> list[dict[str, Any]]:
    """The owner's liked talks of the last 30 days, newest first, as the model reads them."""
    since = now - LIKED_DAYS * 86_400
    liked: dict[int, str] = {}
    for r in conn.execute(
        "SELECT video_id FROM feedback WHERE owner_id = ? AND state = 'up' AND at >= ?"
        " ORDER BY at DESC LIMIT ?",
        (owner_id, since, LIKED_MAX),
    ):
        liked[int(r["video_id"])] = "thumbed up"
    watched = [
        int(r["video_id"])
        for r in conn.execute(
            "SELECT video_id, MAX(at) AS last FROM signals WHERE owner_id = ? AND kind = 'watch'"
            " AND watched_s IS NOT NULL AND at >= ? GROUP BY video_id ORDER BY last DESC LIMIT ?",
            (owner_id, since, LIKED_MAX),
        )
    ]
    spans = ledger._coverage(conn, watched, owner_id)
    for r in conn.execute(
        "SELECT v.id, v.duration_s, d.moments FROM videos v LEFT JOIN verdicts d ON d.video_id = v.id"
        " WHERE v.id IN (SELECT value FROM json_each(?))",
        (json.dumps(watched),),
    ):
        moments = [(float(m["offset_s"]), m.get("end_s")) for m in json.loads(r["moments"] or "[]")]
        if int(r["id"]) not in liked and ledger.kept(
            moments, float(r["duration_s"] or 0), spans.get(int(r["id"]), [])
        ):
            liked[int(r["id"])] = "watched"
    talks: list[dict[str, Any]] = []
    for video_id, how in list(liked.items())[:LIKED_MAX]:
        v = conn.execute(
            "SELECT id, public_id, source_id, title, channel_name FROM videos WHERE id = ?",
            (video_id,),
        ).fetchone()
        if v is None:
            continue
        chapters = [
            r["title"]
            for r in conn.execute(
                "SELECT title FROM chapters WHERE video_id = ? ORDER BY seq LIMIT ?",
                (video_id, CHAPTERS_MAX),
            )
        ]
        talks.append(
            {
                "video_id": v["public_id"],
                "title": v["title"],
                "channel": v["channel_name"],
                "chapters": chapters,
                "on_screen": _on_screen(conn, video_id),
                # Not for the model: what the stored suggestion needs.
                "_row": int(v["id"]),
                "_source_id": v["source_id"],
                "_how": how,
            }
        )
    return talks


def _on_screen(conn: sqlite3.Connection, video_id: int) -> list[str]:
    """Distinct on-screen lines from the first two minutes, where title slides name speakers."""
    seen: list[str] = []
    used = 0
    for r in conn.execute(
        "SELECT text FROM ocr_lines WHERE video_id = ? AND t_s <= ? ORDER BY t_s, line_no",
        (video_id, OCR_UNTIL_S),
    ):
        text = " ".join(str(r["text"]).split())
        if not text or text in seen:
            continue
        if used + len(text) > OCR_CHARS or len(seen) >= OCR_LINES:
            break
        seen.append(text)
        used += len(text)
    return seen


# ---------------------------------------------------------------- the names


_NAME = re.compile(r"[^\W\d_][\w.'\-]*(?:\s+[^\W\d_][\w.'\-]*){1,3}")


def _fresh_names(
    conn: sqlite3.Connection,
    answered: list[dict[str, Any]],
    talks: dict[str, dict[str, Any]],
    owner_id: int,
) -> list[tuple[str, dict[str, Any]]]:
    """Names worth a lookup: a full name, from a liked talk, never suggested, not its channel."""
    out: list[tuple[str, dict[str, Any]]] = []
    for item in answered:
        name = " ".join(str(item["name"]).split())
        talk = talks.get(str(item["video_id"]))
        if talk is None or _NAME.fullmatch(name) is None:
            continue
        k = key(name)
        if k == key(talk["channel"] or "") or any(k == key(n) for n, _ in out):
            continue
        if conn.execute(
            "SELECT 1 FROM speaker_suggestions WHERE owner_id = ? AND name_key = ?", (owner_id, k)
        ).fetchone():
            continue
        out.append((name, talk))
    return out


def _lookup(
    conn: sqlite3.Connection, name: str, talk: dict[str, Any], hits: list[SearchHit], owner_id: int
) -> dict[str, Any] | None:
    """Their own channel, or at least two other talks; None when neither, or followed."""
    k = key(name)
    own = next((h for h in hits if h.channel_name and key(h.channel_name) == k), None)
    if (
        own is not None
        and picks.follow_of(conn, own.channel_id, own.channel_url, owner_id) is not None
    ):
        return None
    others = [
        h
        for h in hits
        if name.casefold() in h.title.casefold()
        and h.source_id != talk["_source_id"]
        and (own is None or h.channel_id != own.channel_id)
    ]
    if own is None and len(others) < TALKS_MIN:
        return None
    return {"own": own, "talks": others[:TALKS_SHOWN]}


def _store(
    conn: sqlite3.Connection,
    week: str,
    name: str,
    talk: dict[str, Any],
    found: dict[str, Any],
    owner_id: int,
) -> None:
    own: SearchHit | None = found["own"]
    talks = [
        {"video_id": h.source_id, "title": h.title, "channel": h.channel_name}
        for h in found["talks"]
    ]
    reason = f"Spoke in “{talk['title']}”, which you {talk['_how']}; " + (
        f"has a channel of their own, {own.channel_name}."
        if own is not None
        else f"gave {len(talks)} other talks on YouTube."
    )
    conn.execute(
        "INSERT INTO speaker_suggestions (owner_id, week, name, name_key, video_id, talk_title,"
        " channel_id, channel_name, channel_url, talks, reason) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            owner_id,
            week,
            name,
            key(name),
            talk["_row"],
            talk["title"],
            own.channel_id if own else None,
            own.channel_name if own else None,
            own.channel_url if own else None,
            json.dumps(talks, ensure_ascii=False),
            reason,
        ),
    )
