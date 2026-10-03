"""The feed's endpoints — companion.md §6, contract in dashboard.md §25.

`feed`, `verdicts/{video_id}`, `signals`, `feedback`, `profile`,
`profile/revert` and `devices`, for the web feed (#90) and the Android app (#91). They are owner
routes: registered with the write side, so a read-only projection and
`VIDTHEQUE_AUTH=none` 404 them, reads behind the read gate and writes behind
`require_write`.

JSON in, JSON out, unlike the form writes of §21: no form posts here, a
profile op is nested, and a cross-site form cannot send
`Content-Type: application/json` without a preflight this server never grants.
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from ..auth.credential import credential
from ..errors import HTTP_STATUS
from ..profile import feedback as feedback_store
from ..profile import signals as signals_store
from ..profile import store as profile_store
from ..text import clamp, deeplink
from ..verdicts import store as verdicts_store
from .access import require_write
from .api import NO_STORE

OWNER_ID = 1

FEED_PAGE = 20
FEED_PAGE_MAX = 50
OFFSET_MAX = 10_000
# "skipped (n)" is a count, so it is bounded like every other expensive path.
SKIPPED_COUNT_CAP = 1000
TITLE_CHARS = 200
# `top` is what the feed shows; `skipped` is the list behind "skipped (n)".
BANDS = {"top": (2, 3), "skipped": (0, 1)}

HISTORY_PAGE = 20
HISTORY_PAGE_MAX = 100

MAX_BODY_BYTES = 16_384
APP_KINDS = ("open", "watch", "ask_claude", "thumb_up", "thumb_down", "mute", "dismiss")
VIDEO_ID_CHARS = 64
# Two days: past any video's length, so a moment's offset is never refused.
OFFSET_S_MAX = 172_800.0

DEVICES_MAX = 20
# FCM registration tokens are URL-safe base64 with a `:` separator.
_TOKEN = re.compile(r"[A-Za-z0-9_:\-]{1,4096}")
_INTEGER = re.compile(r"-?[0-9]{1,18}")
# SQLite's INTEGER ceiling; a larger id would overflow the bind.
ID_MAX = 2**63 - 1


class _Refused(Exception):
    def __init__(self, code: str, message: str, next_hint: str) -> None:
        super().__init__(message)
        self.code = code
        self.next_hint = next_hint


def _json(payload: dict[str, Any]) -> JSONResponse:
    return JSONResponse(payload, headers=NO_STORE)


def _refusal(code: str, message: str, next_hint: str) -> JSONResponse:
    return JSONResponse(
        {"error": code, "message": message, "next": next_hint},
        status_code=HTTP_STATUS.get(code, 500),
        headers=NO_STORE,
    )


def _from(exc: _Refused | profile_store.ProfileRefused) -> JSONResponse:
    return _refusal(exc.code, str(exc), exc.next_hint)


async def _actor(request: Request) -> str:
    """`app` for the bearer the Android app holds, `owner` for a session or a trusted peer."""
    return "app" if await credential(request) == "bearer" else "owner"


def _int_param(request: Request, name: str, low: int, high: int, default: int) -> int:
    raw = request.query_params.get(name)
    if raw is not None and _INTEGER.fullmatch(raw.strip()) is None:
        raise _Refused("E_BAD_PARAM", f"{name}={raw!r} is not an integer.", f"pass {name} as a whole number.")
    return clamp(int(raw) if raw is not None else None, low, high, default)


async def _body(request: Request) -> dict[str, Any]:
    if request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
        raise _Refused(
            "E_BAD_PARAM",
            "this endpoint reads a JSON body.",
            "send Content-Type: application/json.",
        )
    raw = await request.body()
    if len(raw) > MAX_BODY_BYTES:
        raise _Refused(
            "E_TOO_LARGE",
            f"the body is {len(raw)} bytes; the limit is {MAX_BODY_BYTES}.",
            "send fewer operations per call.",
        )
    try:
        body = json.loads(raw, parse_constant=_no_constant)
    except ValueError:
        raise _Refused("E_BAD_PARAM", "the body is not valid JSON.", "send one JSON object.") from None
    if not isinstance(body, dict):
        raise _Refused("E_BAD_PARAM", "the body must be a JSON object.", "send one JSON object.")
    return body


def _no_constant(name: str) -> Any:
    raise ValueError(name)  # NaN and Infinity are not numbers a weight or offset can be


def _only(body: dict[str, Any], allowed: tuple[str, ...]) -> None:
    unknown = sorted(set(body) - set(allowed))
    if unknown:
        raise _Refused(
            "E_BAD_PARAM",
            f"unknown field {', '.join(map(repr, unknown))}.",
            f"this endpoint reads {', '.join(allowed)}.",
        )


def _number(value: Any, name: str) -> float:
    # An int past ±ID_MAX would overflow float(); no weight or offset is that large.
    if isinstance(value, int) and not isinstance(value, bool) and abs(value) > ID_MAX:
        raise _Refused("E_BAD_PARAM", f"{name} is out of range.", f"pass {name} as a JSON number.")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise _Refused("E_BAD_PARAM", f"{name} must be a number.", f"pass {name} as a JSON number.")
    return float(value)


def _whole(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= ID_MAX:
        raise _Refused("E_BAD_PARAM", f"{name} must be a non-negative integer.", f"pass {name} as a whole number.")
    return value


def _list(body: dict[str, Any], name: str) -> list[Any]:
    value = body.get(name)
    if value is None:
        return []
    if not isinstance(value, list):
        raise _Refused("E_BAD_PARAM", f"{name} must be a list.", f"pass {name} as a JSON array.")
    if len(value) > profile_store.MAX_LIVE:
        raise _Refused(
            "E_TOO_LARGE",
            f"{name} has {len(value)} items; one call takes at most {profile_store.MAX_LIVE}.",
            "split the change into several calls.",
        )
    return value


def _object(item: Any, name: str, keys: tuple[str, ...]) -> dict[str, Any]:
    if not isinstance(item, dict) or set(item) != set(keys):
        raise _Refused(
            "E_BAD_PARAM",
            f"each {name} item is an object with exactly {', '.join(keys)}.",
            f"for example {name}=[{{{', '.join(f'{k!r}: …' for k in keys)}}}].",
        )
    return item


def _video(conn: sqlite3.Connection, video_id: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT id, public_id, title, channel_name, duration_s, published_at FROM videos"
        " WHERE public_id = ? AND owner_id = ?",
        (video_id, OWNER_ID),
    ).fetchone()


def _unknown_video(video_id: str) -> _Refused:
    return _Refused(
        "E_UNKNOWN_VIDEO",
        f'Video "{video_id}" is not in the corpus.',
        "use a video_id from the feed.",
    )


def _video_json(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "video_id": row["public_id"],
        "title": (row["title"] or "")[:TITLE_CHARS],
        "channel": row["channel_name"],
        "duration_s": float(row["duration_s"] or 0),
        "published_at": row["published_at"],
    }


# --------------------------------------------------------------------- feed


async def feed(request: Request) -> Response:
    """`GET /dashboard/api/feed` — verdicts newest first, one score band per page."""
    try:
        band = request.query_params.get("band", "top")
        if band not in BANDS:
            raise _Refused("E_BAD_PARAM", f"band={band!r} is not a feed band.", "use band=top or band=skipped.")
        limit = _int_param(request, "limit", 1, FEED_PAGE_MAX, FEED_PAGE)
        offset = _int_param(request, "offset", 0, OFFSET_MAX, 0)
    except _Refused as refused:
        return _from(refused)
    low, high = BANDS[band]

    def read(conn: sqlite3.Connection) -> tuple[list[sqlite3.Row], list[Any], int]:
        rows = list(
            conn.execute(
                "SELECT v.public_id, v.title, v.channel_name, v.duration_s, v.published_at,"
                " d.score, d.reason, d.explored, d.matches, d.created_at FROM verdicts d"
                " JOIN videos v ON v.id = d.video_id"
                " WHERE v.owner_id = ? AND d.score BETWEEN ? AND ?"
                " ORDER BY d.created_at DESC, d.video_id DESC LIMIT ? OFFSET ?",
                (OWNER_ID, low, high, limit + 1, offset),
            )
        )
        skipped = conn.execute(
            "SELECT COUNT(*) FROM (SELECT 1 FROM verdicts d JOIN videos v ON v.id = d.video_id"
            " WHERE v.owner_id = ? AND d.score <= 1 LIMIT ?)",
            (OWNER_ID, SKIPPED_COUNT_CAP + 1),
        ).fetchone()[0]
        return rows, verdicts_store.matches_json(conn, rows[:limit]), int(skipped)

    rows, matches, skipped = await request.app.state.assembled.db.read(read)
    has_more = len(rows) > limit
    # Past the offset ceiling a next page would be clamped back onto this one.
    next_offset = offset + limit if has_more and offset + limit <= OFFSET_MAX else None
    return _json(
        {
            "band": band,
            "order": "newest",
            "items": [
                {
                    **_video_json(row),
                    "score": int(row["score"]),
                    "reason": row["reason"],
                    "explored": bool(row["explored"]),
                    "matches": row_matches,
                    "judged_at": int(row["created_at"]),
                }
                for row, row_matches in zip(rows[:limit], matches)
            ],
            "pagination": {
                "limit": limit,
                "offset": offset,
                "has_more": has_more,
                "next_offset": next_offset,
            },
            "skipped": {
                "count": min(skipped, SKIPPED_COUNT_CAP),
                "capped": skipped > SKIPPED_COUNT_CAP,
            },
        }
    )


async def verdict(request: Request) -> Response:
    """`GET /dashboard/api/verdicts/{video_id}` — the video screen."""
    video_id = str(request.path_params["video_id"])[:VIDEO_ID_CHARS]

    def read(conn: sqlite3.Connection) -> dict[str, Any]:
        video = _video(conn, video_id)
        if video is None:
            raise _unknown_video(video_id)
        row = verdicts_store.get(conn, int(video["id"]))
        if row is None:
            raise _Refused(
                "E_NO_VERDICT",
                f'Video "{video_id}" has no verdict yet.',
                "a verdict is written after the video is indexed; check again once its job is done.",
            )
        # A reindex can break a stored receipt before the rerun lands; show
        # only the moments whose cue still holds the offset.
        kept, dropped = verdicts_store.check_receipts(
            conn, int(video["id"]), verdicts_store.moments_of(row)
        )
        return {
            "video": _video_json(video),
            "score": int(row["score"]),
            "reason": row["reason"],
            "explored": bool(row["explored"]),
            "matches": verdicts_store.matches_json(conn, [row])[0],
            "feedback": feedback_store.state_of(conn, int(video["id"]), OWNER_ID),
            "summary": row["summary"],
            "moments": [
                {
                    "cue_id": m.cue_id,
                    "offset_s": m.offset_s,
                    "why": m.why,
                    "url": deeplink(video["public_id"], m.offset_s),
                }
                for m in kept
            ],
            "moments_dropped": len(dropped),
            "profile_rev": int(row["profile_rev"]),
            "model": row["model"],
            "judged_at": int(row["created_at"]),
        }

    try:
        payload = await request.app.state.assembled.db.read(read)
    except _Refused as refused:
        return _from(refused)
    return _json(payload)


# ------------------------------------------------------------------ signals


async def signal(request: Request) -> Response:
    """`POST /dashboard/api/signals` — one tap from the feed, through `record_signal`."""
    refusal = await require_write(request)
    if refusal is not None:
        return refusal
    try:
        body = await _body(request)
        _only(body, ("kind", "video_id", "offset_s"))
        kind = body.get("kind")
        if kind not in APP_KINDS:
            raise _Refused(
                "E_BAD_PARAM",
                f"kind={kind!r} is not a feed signal.",
                f"use one of {', '.join(APP_KINDS)}.",
            )
        video_id = body.get("video_id")
        if not isinstance(video_id, str) or not 0 < len(video_id) <= VIDEO_ID_CHARS:
            raise _Refused("E_BAD_PARAM", "video_id is required.", "pass the video_id the feed listed.")
        offset_s: float | None = None
        if kind == "watch":
            if body.get("offset_s") is None:
                raise _Refused("E_BAD_PARAM", "watch needs offset_s.", "pass the moment's offset in seconds.")
            offset_s = _number(body["offset_s"], "offset_s")
            if not 0 <= offset_s <= OFFSET_S_MAX:
                raise _Refused("E_BAD_PARAM", f"offset_s={offset_s} is out of range.", "pass seconds from the start of the video.")
        elif body.get("offset_s") is not None:
            raise _Refused("E_BAD_PARAM", "only watch takes offset_s.", f"drop offset_s for {kind}.")
    except _Refused as refused:
        return _from(refused)

    client = "app" if await _actor(request) == "app" else "web"

    def write(conn: sqlite3.Connection) -> int | None:
        # A thumb or mute sent here sets the video's state, as `feedback` does.
        if kind in feedback_store.STATE_OF:
            return feedback_store.set_state(
                conn, video_id, feedback_store.STATE_OF[kind], client=client, owner_id=OWNER_ID
            )
        return signals_store.record_signal(
            conn, kind, video_id=video_id, offset_s=offset_s, client=client, owner_id=OWNER_ID
        )

    signal_id = await request.app.state.assembled.db.write(write)
    if signal_id is None:
        return _from(_unknown_video(video_id))
    return _json({"recorded": True, "signal_id": signal_id, "kind": kind, "video_id": video_id})


async def feedback(request: Request) -> Response:
    """`POST /dashboard/api/feedback` — set or take back a video's thumb or mute."""
    refusal = await require_write(request)
    if refusal is not None:
        return refusal
    try:
        body = await _body(request)
        _only(body, ("video_id", "state"))
        video_id = body.get("video_id")
        if not isinstance(video_id, str) or not 0 < len(video_id) <= VIDEO_ID_CHARS:
            raise _Refused("E_BAD_PARAM", "video_id is required.", "pass the video_id the feed listed.")
        state = body.get("state")
        if state not in feedback_store.STATES:
            raise _Refused(
                "E_BAD_PARAM",
                f"state={state!r} is not a feedback state.",
                f"use one of {', '.join(feedback_store.STATES)}.",
            )
    except _Refused as refused:
        return _from(refused)

    client = "app" if await _actor(request) == "app" else "web"
    done = await request.app.state.assembled.db.write(
        lambda conn: feedback_store.set_state(conn, video_id, state, client=client, owner_id=OWNER_ID)
    )
    if done is None:
        return _from(_unknown_video(video_id))
    return _json({"video_id": video_id, "state": state})


# ------------------------------------------------------------------ profile


def _event_json(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "at": int(row["at"]),
        "actor": row["actor"],
        "op": row["op"],
        "entry_id": int(row["entry_id"]),
        "before": json.loads(row["before"]) if row["before"] else None,
        "after": json.loads(row["after"]) if row["after"] else None,
        "reason": row["reason"],
    }


def _profile_reads(conn: sqlite3.Connection, limit: int, before: int | None) -> dict[str, Any]:
    entries = [
        {
            "id": int(r["id"]),
            "text": r["text"],
            "weight": float(r["weight"]),
            "source": r["source"],
            "created_at": int(r["created_at"]),
            # §2.1: the evidence beside an entry is its `add` event's reason.
            "evidence": r["evidence"],
        }
        for r in conn.execute(
            "SELECT p.id, p.text, p.weight, p.source, p.created_at,"
            " (SELECT reason FROM profile_events e WHERE e.entry_id = p.id AND e.op = 'add'"
            "  ORDER BY e.id LIMIT 1) AS evidence"
            " FROM profile_entries p WHERE p.owner_id = ? AND p.retired_at IS NULL"
            " ORDER BY p.weight DESC, p.id",
            (OWNER_ID,),
        )
    ]
    events = list(
        conn.execute(
            "SELECT e.* FROM profile_events e JOIN profile_entries p ON p.id = e.entry_id"
            " WHERE p.owner_id = ? AND e.id < ? ORDER BY e.id DESC LIMIT ?",
            (OWNER_ID, before if before is not None else 2**62, limit + 1),
        )
    )
    has_more = len(events) > limit
    events = events[:limit]
    return {
        "revision": profile_store.revision(conn, OWNER_ID),
        "max_entries": profile_store.MAX_LIVE,
        "entries": entries,
        "history": {
            "events": [_event_json(e) for e in events],
            "limit": limit,
            "has_more": has_more,
            "next_before": int(events[-1]["id"]) if has_more else None,
        },
    }


async def profile(request: Request) -> Response:
    """`GET /dashboard/api/profile` — entries, revision, and one page of history."""
    try:
        limit = _int_param(request, "limit", 1, HISTORY_PAGE_MAX, HISTORY_PAGE)
        before = (
            _int_param(request, "before", 1, 2**62, 1)
            if request.query_params.get("before") is not None
            else None
        )
    except _Refused as refused:
        return _from(refused)
    payload = await request.app.state.assembled.db.read(
        lambda conn: _profile_reads(conn, limit, before)
    )
    return _json(payload)


def _ops(body: dict[str, Any]) -> profile_store.Ops:
    _only(body, ("add", "drop", "reweight", "reason"))
    add = []
    for item in _list(body, "add"):
        entry = _object(item, "add", ("text", "weight"))
        if not isinstance(entry["text"], str):
            raise _Refused("E_BAD_PARAM", "an add's text must be a string.", "say the interest in a few words.")
        add.append((entry["text"], _number(entry["weight"], "weight")))
    drop = [_whole(item, "drop id") for item in _list(body, "drop")]
    reweight = []
    for item in _list(body, "reweight"):
        entry = _object(item, "reweight", ("id", "weight"))
        reweight.append((_whole(entry["id"], "id"), _number(entry["weight"], "weight")))
    reason = body.get("reason")
    if reason is not None and not isinstance(reason, str):
        raise _Refused("E_BAD_PARAM", "reason must be a string.", "say why in one line.")
    if not (add or drop or reweight):
        raise _Refused(
            "E_BAD_PARAM",
            "the call names no operation.",
            "pass add=[{text, weight}], drop=[id] or reweight=[{id, weight}].",
        )
    return profile_store.Ops(add=add, drop=drop, reweight=reweight, reason=reason)


async def profile_ops(request: Request) -> Response:
    """`POST /dashboard/api/profile` — add, drop, reweight; one batch, through `store.apply`."""
    refusal = await require_write(request)
    if refusal is not None:
        return refusal
    try:
        ops = _ops(await _body(request))
    except _Refused as refused:
        return _from(refused)
    actor = await _actor(request)
    db = request.app.state.assembled.db
    try:
        applied = await db.write(lambda conn: profile_store.apply(conn, ops, actor, OWNER_ID))
    except profile_store.ProfileRefused as refused:
        return _from(refused)
    payload = await db.read(lambda conn: _profile_reads(conn, HISTORY_PAGE, None))
    payload["applied"] = {"events": applied.event_ids, "duplicates": applied.duplicates}
    return _json(payload)


async def profile_revert(request: Request) -> Response:
    """`POST /dashboard/api/profile/revert` — undo one event, or roll back to a revision."""
    refusal = await require_write(request)
    if refusal is not None:
        return refusal
    try:
        body = await _body(request)
        _only(body, ("event_id", "revision"))
        if ("event_id" in body) == ("revision" in body):
            raise _Refused(
                "E_BAD_PARAM",
                "pass exactly one of event_id and revision.",
                "event_id undoes one event; revision rolls back every later one.",
            )
        target = _whole(body.get("event_id", body.get("revision")), next(iter(body)))
    except _Refused as refused:
        return _from(refused)
    actor = await _actor(request)

    def write(conn: sqlite3.Connection) -> list[int]:
        if "event_id" in body:
            return [profile_store.revert_event(conn, target, actor, OWNER_ID)]
        return profile_store.revert_to(conn, target, actor, OWNER_ID)

    db = request.app.state.assembled.db
    try:
        reverted = await db.write(write)
    except profile_store.ProfileRefused as refused:
        return _from(refused)
    payload = await db.read(lambda conn: _profile_reads(conn, HISTORY_PAGE, None))
    payload["reverted"] = {"events": reverted}
    return _json(payload)


# ------------------------------------------------------------------ devices


async def _device_token(request: Request) -> str:
    body = await _body(request)
    _only(body, ("token",))
    token = body.get("token")
    if not isinstance(token, str) or _TOKEN.fullmatch(token) is None:
        raise _Refused(
            "E_BAD_PARAM",
            "token is not an FCM registration token.",
            "pass the token FirebaseMessaging.getToken() returned.",
        )
    return token


async def devices(request: Request) -> Response:
    """`POST|DELETE /dashboard/api/devices` — register or remove a push token."""
    refusal = await require_write(request)
    if refusal is not None:
        return refusal
    try:
        token = await _device_token(request)
    except _Refused as refused:
        return _from(refused)
    db = request.app.state.assembled.db

    if request.method == "DELETE":
        removed = await db.write(
            lambda conn: conn.execute(
                "DELETE FROM devices WHERE token = ? AND owner_id = ?", (token, OWNER_ID)
            ).rowcount
        )
        return _json({"removed": bool(removed)})

    def register(conn: sqlite3.Connection) -> tuple[int, int]:
        conn.execute(
            "INSERT INTO devices (owner_id, token) VALUES (?, ?)"
            " ON CONFLICT (token) DO UPDATE SET last_seen = unixepoch()",
            (OWNER_ID, token),
        )
        evicted = conn.execute(
            "DELETE FROM devices WHERE owner_id = ? AND id NOT IN"
            " (SELECT id FROM devices WHERE owner_id = ? ORDER BY last_seen DESC, id DESC LIMIT ?)",
            (OWNER_ID, OWNER_ID, DEVICES_MAX),
        ).rowcount
        count = conn.execute("SELECT COUNT(*) FROM devices WHERE owner_id = ?", (OWNER_ID,)).fetchone()[0]
        return int(count), int(evicted)

    count, evicted = await db.write(register)
    return _json({"registered": True, "devices": count, "evicted": evicted})
