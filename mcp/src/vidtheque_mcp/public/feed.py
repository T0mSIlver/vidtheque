"""`GET /api/feed` — the sample feed's read, demo-site.md §8.

The verdicts the public box wrote against the sample profile, best first. A
moment carries a short attributed excerpt and a link to its second on
YouTube; nothing longer leaves this route (the copyright posture, §8.3).
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse

from ..db.queries import QUERYABLE_INDEX_STATES
from ..errors import HTTP_STATUS, ToolError
from ..profile import store as profile_store
from ..text import clamp, deeplink, split_csv, validate_tag
from ..tools.base import Deps
from ..verdicts import store as verdicts_store
from . import sample
from .api import NO_STORE, THUMB_WIDTH, _cover_frames, thumb_url

MAX_LIMIT = 20
DEFAULT_LIMIT = 10
MAX_OFFSET = 1_000
# A recommendation is a score of 2 ("worth your time") or 3; `min_score=0` shows every verdict.
DEFAULT_MIN_SCORE = 2
TITLE_CHARS = 200
SUMMARY_CHARS = 600
REASON_CHARS = 300
WHY_CHARS = 200
# A quote, not a passage: about 25 words of the cue the moment starts in.
EXCERPT_CHARS = 160
ORDERS = ("score", "newest")


def _clip(text: str | None, chars: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= chars else text[: chars - 1].rstrip() + "…"


def _refusal(exc: ToolError) -> JSONResponse:
    return JSONResponse(
        {"error": exc.code, "message": exc.message, "next": exc.next_hint},
        status_code=HTTP_STATUS.get(exc.code, 400),
        headers=NO_STORE,
    )


def _excerpts(conn: sqlite3.Connection, cue_ids: list[int]) -> dict[int, dict[str, Any]]:
    rows = conn.execute(
        "SELECT c.id, c.text, COALESCE(s.display_name, '') AS speaker FROM cues c"
        " LEFT JOIN speakers s ON s.id = c.speaker_id"
        " WHERE c.id IN (SELECT value FROM json_each(?))",
        (json.dumps(cue_ids),),
    )
    return {
        int(r["id"]): {"text": _clip(r["text"], EXCERPT_CHARS), "speaker": r["speaker"] or None}
        for r in rows
    }


def _read(
    conn: sqlite3.Connection, tags: list[str], min_score: int, order: str, limit: int, offset: int
) -> dict[str, Any]:
    marks = ",".join("?" for _ in QUERYABLE_INDEX_STATES)
    where = [f"v.index_state IN ({marks})", "d.score >= ?"]
    args: list[Any] = [*QUERYABLE_INDEX_STATES, min_score]
    for tag in tags:
        where.append(
            "EXISTS (SELECT 1 FROM video_tags vt JOIN tags t ON t.id = vt.tag_id"
            " WHERE vt.video_id = v.id AND t.full = ?)"
        )
        args.append(tag)
    by = (
        "d.score DESC, v.published_at DESC, v.id DESC"
        if order == "score"
        else "v.published_at DESC, v.id DESC"
    )
    rows = conn.execute(
        "SELECT v.public_id, v.title, v.channel_name, v.duration_s, v.published_at,"
        " d.score, d.reason, d.summary, d.moments, d.matches"
        " FROM verdicts d JOIN videos v ON v.id = d.video_id"
        f" WHERE {' AND '.join(where)} ORDER BY {by} LIMIT ? OFFSET ?",
        (*args, limit + 1, offset),
    ).fetchall()
    has_more = len(rows) > limit
    rows = rows[:limit]
    matches = verdicts_store.matches_json(conn, rows)
    moments = [verdicts_store.moments_of(r) for r in rows]
    excerpts = _excerpts(conn, [m.cue_id for ms in moments for m in ms])
    covers = _cover_frames(conn, [str(r["public_id"]) for r in rows])
    items = []
    for row, ms, mt in zip(rows, moments, matches):
        public_id = str(row["public_id"])
        items.append(
            {
                "video_id": public_id,
                "title": _clip(row["title"], TITLE_CHARS),
                "channel": row["channel_name"],
                "duration_s": float(row["duration_s"] or 0),
                "published_at": row["published_at"],
                "url": deeplink(public_id, 0),
                "cover_frame": covers.get(public_id),
                "score": int(row["score"]),
                "reason": _clip(row["reason"], REASON_CHARS),
                "summary": _clip(row["summary"], SUMMARY_CHARS),
                "matches": [{"text": m["text"], "direction": m["direction"]} for m in mt],
                "moments": [
                    {
                        "offset_s": m.offset_s,
                        "url": deeplink(public_id, m.offset_s),
                        "why": _clip(m.why, WHY_CHARS),
                        "excerpt": excerpts.get(m.cue_id, {}).get("text"),
                        "speaker": excerpts.get(m.cue_id, {}).get("speaker"),
                    }
                    for m in ms
                ],
            }
        )
    entries = [
        {"text": str(e["text"]), "weight": float(e["weight"])}
        for e in profile_store.entries(conn)
    ]
    return {
        "profile": {"name": sample.NAME, "entries": entries} if entries else None,
        "items": items,
        "has_more": has_more,
        "next_offset": offset + limit if has_more else None,
    }


async def feed_endpoint(request: Request) -> JSONResponse:
    deps: Deps = request.app.state.assembled.deps
    params = request.query_params
    try:
        tags = split_csv(params.get("tags"), 10, "tags")
        for tag in tags:
            validate_tag(tag)
    except ToolError as exc:
        return _refusal(exc)
    order = params.get("order") or "score"
    if order not in ORDERS:
        return _refusal(
            ToolError("E_BAD_PARAM", f'Unknown order "{order[:20]}".', "use score or newest.")
        )
    limit = clamp(params.get("limit"), 1, MAX_LIMIT, DEFAULT_LIMIT)  # type: ignore[arg-type]
    offset = clamp(params.get("offset"), 0, MAX_OFFSET, 0)  # type: ignore[arg-type]
    min_score = clamp(params.get("min_score"), 0, 3, DEFAULT_MIN_SCORE)  # type: ignore[arg-type]
    payload = await deps.db.read(lambda c: _read(c, tags, min_score, order, limit, offset))
    for item in payload["items"]:
        item["thumb"] = thumb_url(deps, item.pop("cover_frame"), THUMB_WIDTH)
    return JSONResponse(payload, headers=NO_STORE)
