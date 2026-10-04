"""`recommend`: an agent's own picks on top of the verdicts (companion.md §6.4, tool-surface §4.13).

Called bare it returns what the agent picks from: the last days' verdicts in
compact form, and what the owner did with its earlier picks. With a
`video_id` and a `reason` it stores today's pick of that video, its moments
held to the verdicts' receipt check.
"""

from __future__ import annotations

import sqlite3
import time
from typing import Any

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp_types import CallToolResult
from pydantic import BaseModel, ConfigDict

from ..db import queries
from ..db.queries import QUERYABLE_INDEX_STATES
from ..errors import ToolError, bad_param, unknown_video
from ..text import clock
from ..verdicts import picks, store
from .base import CALL_CONTEXT, Deps, handle_errors, text_result


class PickMoment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_s: float
    end_s: float
    why: str


@handle_errors
async def recommend(
    deps: Deps,
    video_id: str | None = None,
    reason: str | None = None,
    moments: list[PickMoment] | None = None,
    days: int = picks.DAYS_DEFAULT,
    limit: int = picks.LIMIT_DEFAULT,
    offset: int = 0,
) -> CallToolResult:
    if video_id is None:
        if reason is not None or moments:
            raise bad_param("reason and moments need a video_id.", "recommend(video_id=..., reason=...).")
        return await _read(deps, days, limit, offset)
    return await _pick(deps, video_id.strip(), reason, moments or [])


# ------------------------------------------------------------------ write


async def _pick(deps: Deps, video_id: str, reason: str | None, moments: list[PickMoment]) -> CallToolResult:
    if reason is None:
        raise bad_param("a pick needs a reason.", "reason= one line on why this owner should watch it.")
    if len(moments) > picks.MOMENTS_MAX:
        raise bad_param(f"{len(moments)} moments; at most {picks.MOMENTS_MAX}.", "keep the best three.")
    asked: list[picks.AskedMoment] = []
    try:
        line = picks.check_text("reason", reason, picks.REASON_CHARS)
        for m in moments:
            if m.start_s < 0 or m.end_s <= m.start_s:
                raise bad_param(
                    f"moment {m.start_s}–{m.end_s} s is not a span.",
                    "start_s and end_s are seconds into the video, start first.",
                )
            asked.append(picks.AskedMoment(m.start_s, m.end_s, picks.check_text("why", m.why, picks.WHY_CHARS)))
    except picks.PickRefused as refused:
        raise ToolError(refused.code, str(refused), refused.next_hint) from None

    row = await deps.db.read(lambda c: queries.lookup_video(c, video_id))
    if row is None:
        raise unknown_video(video_id, can_index=False)
    if str(row["index_state"]) not in QUERYABLE_INDEX_STATES:
        raise ToolError(
            "E_NOT_INDEXED",
            f'Video "{video_id}" is not indexed yet, so its moments cannot be checked.',
            "pick it once job-status says it is ready.",
        )
    client = _client()
    try:
        saved = await deps.db.write(lambda c: picks.save(c, int(row["id"]), line, asked, client=client))
    except picks.PickRefused as refused:
        raise ToolError(refused.code, str(refused), refused.next_hint) from None
    return text_result(_render_saved(video_id, saved), _saved_json(video_id, saved))


def _client() -> str | None:
    call = CALL_CONTEXT.get()
    if call is not None and call.client:
        return call.client
    token = get_access_token()
    return token.client_id if token is not None else None


def _render_saved(video_id: str, saved: picks.Saved) -> str:
    verb = "Replaced today's pick of" if saved.replaced else "Picked"
    lines = [f"{verb} {video_id} · {saved.today} of {picks.PER_DAY} today."]
    for m in saved.kept:
        lines.append(f"moment {clock(m.offset_s)}–{clock(m.end_s)} kept: {m.why}")
    for m, why in saved.dropped:
        lines.append(f"note: moment {m.start_s:.1f}–{m.end_s:.1f} s dropped: {why}. Moments are never moved.")
    if saved.dropped:
        lines.append("next: read the cues with get-transcript t_start= and pick the span again to replace it.")
    return "\n".join(lines)


def _saved_json(video_id: str, saved: picks.Saved) -> dict[str, Any]:
    return {
        "video_id": video_id,
        "pick_id": saved.pick_id,
        "replaced": saved.replaced,
        "today": saved.today,
        "per_day": picks.PER_DAY,
        "moments": [m.__dict__ for m in saved.kept],
        "dropped": [{"start_s": m.start_s, "end_s": m.end_s, "why": m.why, "reason": r} for m, r in saved.dropped],
    }


# ------------------------------------------------------------------ read


async def _read(deps: Deps, days: int, limit: int, offset: int) -> CallToolResult:
    if not 1 <= days <= picks.DAYS_MAX:
        raise bad_param(f"days={days}; 1 to {picks.DAYS_MAX}.", f"days={picks.DAYS_DEFAULT} reads the last two.")
    if not 1 <= limit <= picks.LIMIT_MAX:
        raise bad_param(f"limit={limit}; 1 to {picks.LIMIT_MAX}.", f"limit={picks.LIMIT_DEFAULT}.")
    if offset < 0:
        raise bad_param(f"offset={offset} is negative.", "offset=0 starts at the best verdict.")
    now = time.time()

    def read(conn: sqlite3.Connection) -> tuple[list[sqlite3.Row], bool, list[picks.Earlier]]:
        rows, more = picks.recent_verdicts(conn, days, limit, offset, now)
        return rows, more, picks.earlier(conn, now)

    rows, more, earlier = await deps.db.read(read)
    today = picks.local_day(now)
    today_n = sum(1 for e in earlier if e.day == today)

    blocks: list[str] = []
    items: list[dict[str, Any]] = []
    used = 0
    for r in rows:
        item = _verdict_json(r)
        block = _verdict_block(item)
        if items and used + len(block) > picks.CHARS_MAX:
            more = True
            break
        used += len(block)
        blocks.append(block)
        items.append(item)
    next_offset = offset + len(items) if more else None

    lines = [
        f"Picks today ({today}): {today_n} of {picks.PER_DAY}.",
        f"Verdicts written in the last {days} day(s), best first: {len(items)}"
        + (f" from offset {offset}" if offset else "")
        + (f"; more at offset={next_offset}" if more else "")
        + ".",
        "",
        *blocks,
    ]
    if not items:
        lines.append("No new verdicts in that window.")
    lines.append(f"Earlier picks, last {picks.FEEDBACK_DAYS} days, with what the owner did:")
    if earlier:
        lines.append("day\tvideo_id\tthumb\tkept\topened\ttitle")
        for e in earlier:
            lines.append(
                f"{e.day}\t{e.public_id}\t{e.feedback}\t{'yes' if e.kept else 'no'}\t"
                f"{'yes' if e.opened else 'no'}\t{e.title}"
            )
            lines.append(f"  reason: {e.reason}")
    else:
        lines.append("none yet.")
    lines.append("")
    lines.append(
        "next: get-transcript video_id= for the few most promising; then "
        "recommend(video_id, reason, moments=[{start_s, end_s, why}]) at most "
        f"{picks.PER_DAY} a day. kept = thumbed up or watched past half the moments."
    )
    structured = {
        "today": today,
        "picked_today": today_n,
        "per_day": picks.PER_DAY,
        "verdicts": items,
        "pagination": {"offset": offset, "limit": limit, "has_more": more, "next_offset": next_offset},
        "earlier": [e.__dict__ for e in earlier],
    }
    return text_result("\n".join(lines), structured)


def _verdict_json(r: sqlite3.Row) -> dict[str, Any]:
    moments = store.moments_of(r)
    return {
        "video_id": str(r["public_id"]),
        "title": str(r["title"] or ""),
        "channel": r["channel_name"],
        "duration_s": float(r["duration_s"] or 0),
        "published_at": r["published_at"],
        "score": int(r["score"]),
        "reason": str(r["reason"]),
        "summary": str(r["summary"]),
        "moments": [{"offset_s": m.offset_s, "end_s": m.end_s, "why": m.why} for m in moments],
        "feedback": r["feedback"] or "none",
    }


def _verdict_block(item: dict[str, Any]) -> str:
    published = (
        time.strftime("%Y-%m-%d", time.localtime(int(item["published_at"])))
        if item["published_at"] is not None
        else "?"
    )
    head = (
        f"{item['video_id']}\tscore {item['score']}\t{published}\t{clock(item['duration_s'])}\t"
        f"{item['channel'] or '?'}\t{item['title']}"
    )
    lines = [head, f"  reason: {item['reason']}", f"  summary: {item['summary']}"]
    for m in item["moments"]:
        # Seconds, as recommend takes them; the clock for a reader.
        end = f"–{m['end_s']:.0f}" if m["end_s"] is not None else ""
        lines.append(f"  moment {m['offset_s']:.0f}{end} s ({clock(m['offset_s'])}): {m['why']}")
    if item["feedback"] != "none":
        lines.append(f"  thumb: {item['feedback']}")
    return "\n".join(lines) + "\n"

