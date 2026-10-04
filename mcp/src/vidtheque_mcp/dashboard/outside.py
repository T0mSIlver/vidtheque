"""Discovery's endpoints — companion.md §6.2, contract in dashboard.md §27.

Owner routes with the feed's access, JSON rules and refusals (§25.1). Nothing
here asks YouTube anything: the scout does that at night.
"""

from __future__ import annotations

import sqlite3
import time
from datetime import datetime
from typing import Any

from starlette.requests import Request
from starlette.responses import Response

from ..brief.build import parse_week, week_of
from ..discover import picks
from ..discover.scout import ScoutSettings
from ..llm import LLMSettings, is_configured
from ..tools import follows as follows_tool
from .access import require_write
from .feed import OWNER_ID, OFFSET_S_MAX, _body, _from, _json, _number, _only, _Refused, _whole
from .read_models import tool_error


def _unknown_pick(pick_id: int) -> _Refused:
    return _Refused(
        "E_UNKNOWN_PICK",
        f"{pick_id} is not a pick from outside on this instance.",
        "use an id the feed listed.",
    )


def _scouting() -> bool:
    return ScoutSettings.from_env().enabled and is_configured(LLMSettings.from_env())


async def outside(request: Request) -> Response:
    """`GET /dashboard/api/outside` — the week's picks from outside and its speaker suggestion."""
    raw = request.query_params.get("week")
    if raw is None:
        week = week_of(datetime.now().astimezone()).key
    elif parse_week(raw) is None:
        return _from(
            _Refused(
                "E_BAD_PARAM",
                f"week={raw!r} is not a Monday as YYYY-MM-DD.",
                "omit week for this week.",
            )
        )
    else:
        week = raw
    payload = await request.app.state.assembled.db.read(
        lambda c: picks.week_payload(c, week, OWNER_ID)
    )
    return _json({**payload, "scouting": _scouting()})


async def outside_pick(request: Request) -> Response:
    """`GET /dashboard/api/outside/{id}` — one shown pick, any week."""
    raw = request.path_params["pick_id"]
    pick_id = int(raw) if raw.isdigit() and len(raw) <= 18 else -1

    def read(conn: sqlite3.Connection) -> dict[str, Any] | None:
        row = picks.shown(conn, pick_id, OWNER_ID)
        return picks.pick_json(conn, row) if row is not None else None

    payload = await request.app.state.assembled.db.read(read)
    if payload is None:
        return _from(_unknown_pick(pick_id))
    return _json(payload)


async def feedback(request: Request) -> Response:
    """`POST /dashboard/api/outside/feedback` — thumbs on a pick; up offers a trial follow."""
    refusal = await require_write(request)
    if refusal is not None:
        return refusal
    try:
        body = await _body(request)
        _only(body, ("id", "state"))
        pick_id = _whole(body.get("id"), "id")
        state = body.get("state")
        if state not in picks.FEEDBACK:
            raise _Refused(
                "E_BAD_PARAM",
                f"state={state!r} is not a feedback state.",
                f"use one of {', '.join(picks.FEEDBACK)}.",
            )
    except _Refused as refused:
        return _from(refused)

    def write(conn: sqlite3.Connection) -> dict[str, Any] | None:
        row = picks.set_feedback(conn, pick_id, state, OWNER_ID)
        if row is None:
            return None
        offer = None
        if (
            state == "up"
            and row["channel_url"]
            and picks.follow_of(conn, row["channel_id"], row["channel_url"], OWNER_ID) is None
        ):
            offer = {"channel": row["channel_name"], "url": row["channel_url"], "days": 14}
        return {"id": pick_id, "state": state, "offer": offer}

    done = await request.app.state.assembled.db.write(write)
    if done is None:
        return _from(_unknown_pick(pick_id))
    return _json(done)


async def watched(request: Request) -> Response:
    """`POST /dashboard/api/outside/watched` — the time in YouTube after a moment's hand-off."""
    refusal = await require_write(request)
    if refusal is not None:
        return refusal
    try:
        body = await _body(request)
        _only(body, ("id", "offset_s", "watched_s"))
        pick_id = _whole(body.get("id"), "id")
        offset = _number(body.get("offset_s"), "offset_s")
        seconds = _number(body.get("watched_s"), "watched_s")
        for name, value in (("offset_s", offset), ("watched_s", seconds)):
            if not 0 <= value <= OFFSET_S_MAX:
                raise _Refused(
                    "E_BAD_PARAM", f"{name}={value} is out of range.", f"pass {name} in seconds."
                )
    except _Refused as refused:
        return _from(refused)
    kept = await request.app.state.assembled.db.write(
        lambda conn: picks.record_watched(conn, pick_id, offset, seconds, OWNER_ID)
    )
    if kept is None:
        return _from(_unknown_pick(pick_id))
    return _json({"id": pick_id, "watched_s": kept})


async def follow(request: Request) -> Response:
    """`POST /dashboard/api/outside/follow` — a 14-day trial follow of a pick's or speaker's channel."""
    refusal = await require_write(request)
    if refusal is not None:
        return refusal
    try:
        body = await _body(request)
        _only(body, ("pick", "speaker"))
        if len(body) != 1:
            raise _Refused(
                "E_BAD_PARAM", "pass one of pick or speaker.", 'for example {"pick": 4}.'
            )
        kind = next(iter(body))
        ref_id = _whole(body[kind], kind)
    except _Refused as refused:
        return _from(refused)

    db = request.app.state.assembled.db

    def read(conn: sqlite3.Connection) -> sqlite3.Row | None:
        if kind == "pick":
            return picks.shown(conn, ref_id, OWNER_ID)
        return conn.execute(
            "SELECT * FROM speaker_suggestions WHERE id = ? AND owner_id = ?", (ref_id, OWNER_ID)
        ).fetchone()

    row = await db.read(read)
    if row is None:
        return _from(_unknown_pick(ref_id))
    if not row["channel_url"]:
        return _from(
            _Refused(
                "E_NO_CHANNEL",
                "this has no channel to follow.",
                "open its talks on YouTube instead.",
            )
        )
    url, title = str(row["channel_url"]), str(row["channel_name"] or "")
    already = (
        await db.read(lambda c: picks.follow_of(c, row["channel_id"], url, OWNER_ID)) is not None
    )
    if not already:
        result = await follows_tool.follow_channel(
            request.app.state.assembled.deps, url=url, action="trial", title=title or None
        )
        error = tool_error(result)
        if error is not None:
            return _from(
                _Refused(error["code"], error["message"], error["next"] or "try again later.")
            )

    def mark(conn: sqlite3.Connection) -> int | None:
        if kind == "pick":
            picks.mark_followed(conn, ref_id, int(time.time()))
        else:
            conn.execute(
                "UPDATE speaker_suggestions SET state = 'followed' WHERE id = ?", (ref_id,)
            )
        covering = picks.follow_of(conn, row["channel_id"], url, OWNER_ID)
        return covering["trial_until"] if covering is not None else None

    until = await db.write(mark)
    return _json({"url": url, "title": title, "trial_until": until, "already": already})


async def speaker(request: Request) -> Response:
    """`POST /dashboard/api/outside/speaker` — the week's speaker is not wanted."""
    refusal = await require_write(request)
    if refusal is not None:
        return refusal
    try:
        body = await _body(request)
        _only(body, ("id", "state"))
        speaker_id = _whole(body.get("id"), "id")
        if body.get("state") != "dismissed":
            raise _Refused(
                "E_BAD_PARAM", "state must be dismissed.", 'send {"id": …, "state": "dismissed"}.'
            )
    except _Refused as refused:
        return _from(refused)
    changed = await request.app.state.assembled.db.write(
        lambda conn: (
            conn.execute(
                "UPDATE speaker_suggestions SET state = 'dismissed' WHERE id = ? AND owner_id = ?",
                (speaker_id, OWNER_ID),
            ).rowcount
        )
    )
    if not changed:
        return _from(_unknown_pick(speaker_id))
    return _json({"id": speaker_id, "state": "dismissed"})
