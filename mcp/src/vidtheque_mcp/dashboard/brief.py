"""The weekly brief's endpoints — companion.md §6.1, contract in dashboard.md §26.

`brief` (GET), `brief/checkin` and `skips` (POST). Owner routes with the same
access, JSON rules and refusals as the feed's (§25.1), whose helpers they use.
"""

from __future__ import annotations

import sqlite3
import time
from typing import Any

from starlette.requests import Request
from starlette.responses import Response

from ..brief import build
from ..profile import feedback as feedback_store
from .access import require_write
from .feed import OWNER_ID, VIDEO_ID_CHARS, _actor, _body, _from, _json, _only, _Refused, _unknown_video

MISSING_CHARS = 500
ANSWERS = ("right", "wrong")
SOURCES = ("audit", "row")


def _no_brief(week: str | None) -> _Refused:
    if week is None:
        return _Refused("E_NO_BRIEF", "no weekly brief has been built yet.", "the first one is built on Sunday morning.")
    return _Refused("E_NO_BRIEF", f"there is no brief for the week of {week}.", "omit week for the latest brief.")


def _week_param(raw: Any) -> str:
    if not isinstance(raw, str) or build.parse_week(raw) is None:
        raise _Refused("E_BAD_PARAM", f"week={raw!r} is not a Monday as YYYY-MM-DD.", "pass the week the brief names.")
    return raw


async def brief(request: Request) -> Response:
    """`GET /dashboard/api/brief` — the latest week's brief, or `?week=` an older one."""
    try:
        raw = request.query_params.get("week")
        week = _week_param(raw) if raw is not None else None
    except _Refused as refused:
        return _from(refused)

    def read(conn: sqlite3.Connection) -> dict[str, Any]:
        if week is None:
            row = conn.execute(
                "SELECT * FROM briefs WHERE owner_id = ? ORDER BY week DESC LIMIT 1", (OWNER_ID,)
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM briefs WHERE owner_id = ? AND week = ?", (OWNER_ID, week)
            ).fetchone()
        if row is None:
            raise _no_brief(week)
        return build.assemble(conn, row, int(time.time()), OWNER_ID)

    try:
        payload = await request.app.state.assembled.db.read(read)
    except _Refused as refused:
        return _from(refused)
    return _json(payload)


async def checkin(request: Request) -> Response:
    """`POST /dashboard/api/brief/checkin` — "was last week's feed worth the time?", 1–5."""
    refusal = await require_write(request)
    if refusal is not None:
        return refusal
    try:
        body = await _body(request)
        _only(body, ("week", "rating", "missing"))
        week = _week_param(body.get("week"))
        rating = body.get("rating")
        if isinstance(rating, bool) or not isinstance(rating, int) or not 1 <= rating <= 5:
            raise _Refused("E_BAD_PARAM", "rating must be a whole number from 1 to 5.", "1 is not worth it, 5 is.")
        missing = body.get("missing")
        if missing is not None and not isinstance(missing, str):
            raise _Refused("E_BAD_PARAM", "missing must be a string.", "say what was missing in one line.")
        missing = " ".join((missing or "").split())[:MISSING_CHARS] or None
    except _Refused as refused:
        return _from(refused)

    def write(conn: sqlite3.Connection) -> bool:
        if conn.execute("SELECT 1 FROM briefs WHERE owner_id = ? AND week = ?", (OWNER_ID, week)).fetchone() is None:
            return False
        conn.execute(
            "INSERT INTO checkins (owner_id, week, rating, missing) VALUES (?, ?, ?, ?)"
            " ON CONFLICT (owner_id, week) DO UPDATE SET rating = excluded.rating,"
            " missing = excluded.missing, at = unixepoch()",
            (OWNER_ID, week, rating, missing),
        )
        return True

    if not await request.app.state.assembled.db.write(write):
        return _from(_no_brief(week))
    return _json({"week": week, "rating": rating, "missing": missing})


async def skip(request: Request) -> Response:
    """`POST /dashboard/api/skips` — the owner's word on a skipped video.

    `wrong` ("I'd watch this") sets the video's thumb up, the strong signal the
    nightly update reads, and answers a proposed reweight of the entry that
    sank it; the owner applies it, or not, through `POST profile`.
    """
    refusal = await require_write(request)
    if refusal is not None:
        return refusal
    try:
        body = await _body(request)
        _only(body, ("video_id", "answer", "source"))
        video_id = body.get("video_id")
        if not isinstance(video_id, str) or not 0 < len(video_id) <= VIDEO_ID_CHARS:
            raise _Refused("E_BAD_PARAM", "video_id is required.", "pass the video_id the feed listed.")
        answer = body.get("answer")
        if answer not in ANSWERS:
            raise _Refused("E_BAD_PARAM", f"answer={answer!r} is not right or wrong.", "wrong means you would watch it.")
        source = body.get("source", "row")
        if source not in SOURCES:
            raise _Refused("E_BAD_PARAM", f"source={source!r} is not audit or row.", "omit it from the feed.")
    except _Refused as refused:
        return _from(refused)
    client = "app" if await _actor(request) == "app" else "web"

    def write(conn: sqlite3.Connection) -> dict[str, Any]:
        row = conn.execute(
            "SELECT d.*, v.public_id FROM verdicts d JOIN videos v ON v.id = d.video_id"
            " WHERE v.public_id = ? AND v.owner_id = ?",
            (video_id, OWNER_ID),
        ).fetchone()
        if row is None:
            raise _unknown_video(video_id)
        if int(row["score"]) > 1:
            raise _Refused(
                "E_BAD_PARAM",
                f'Video "{video_id}" was not skipped: it scored {row["score"]}.',
                "only a video scored 0–1 takes a skip answer.",
            )
        vid = int(row["video_id"])
        before = conn.execute(
            "SELECT answer FROM skip_verdicts WHERE owner_id = ? AND video_id = ?", (OWNER_ID, vid)
        ).fetchone()
        conn.execute(
            "INSERT INTO skip_verdicts (owner_id, video_id, answer, source, score) VALUES (?, ?, ?, ?, ?)"
            " ON CONFLICT (owner_id, video_id) DO UPDATE SET answer = excluded.answer,"
            " source = excluded.source, score = excluded.score, at = unixepoch()",
            (OWNER_ID, vid, answer, source, int(row["score"])),
        )
        state = feedback_store.state_of(conn, vid, OWNER_ID)
        if answer == "wrong" and state != "up":
            feedback_store.set_state(conn, video_id, "up", client=client, owner_id=OWNER_ID)
        elif answer == "right" and before is not None and before[0] == "wrong" and state == "up":
            # Taking back "I'd watch this" takes back the thumb it set.
            feedback_store.set_state(conn, video_id, "none", client=client, owner_id=OWNER_ID)
        return {
            "video_id": video_id,
            "answer": answer,
            "feedback": feedback_store.state_of(conn, vid, OWNER_ID),
            "proposal": build.proposal(conn, row) if answer == "wrong" else None,
        }

    try:
        payload = await request.app.state.assembled.db.write(write)
    except _Refused as refused:
        return _from(refused)
    return _json(payload)
