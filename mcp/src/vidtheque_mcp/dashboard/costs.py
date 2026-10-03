"""`GET /dashboard/api/costs` — what the model calls cost, dashboard.md §25.8.

Read from `llm_calls` (companion.md §4.1) for the console's Health page and
the app's profile screen. Every query reads at most the last 31 days, and the
lists are capped, whatever the table holds.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from typing import Any, Callable

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from .api import NO_STORE

OWNER_ID = 1
TOP_CALLS = 10
PURPOSES_MAX = 20
TITLE_CHARS = 200
# The rescore belongs to the verdict it rescored.
VERDICT_PURPOSES = ("verdict", "verdict_explore")


def windows(now: datetime) -> dict[str, int]:
    """Each window's start, in unix seconds; `today` and `month` on the box's clock."""
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return {
        "today": int(midnight.timestamp()),
        "month": int(midnight.replace(day=1).timestamp()),
        "7d": int((now - timedelta(days=7)).timestamp()),
        "30d": int((now - timedelta(days=30)).timestamp()),
    }


def _window(conn: sqlite3.Connection, since: int) -> dict[str, Any]:
    row = conn.execute(
        "SELECT COUNT(*) AS calls,"
        " COUNT(*) - COUNT(cost_micro_usd) AS unpriced,"
        " SUM(cost_micro_usd) AS cost,"
        " SUM(purpose = 'verdict' AND outcome = 'ok') AS verdicts,"
        f" SUM(CASE WHEN purpose IN {VERDICT_PURPOSES} THEN cost_micro_usd END) AS verdict_cost"
        " FROM llm_calls WHERE owner_id = ? AND at >= ?",
        (OWNER_ID, since),
    ).fetchone()
    verdicts = int(row["verdicts"] or 0)
    verdict_cost = row["verdict_cost"]
    return {
        "since": since,
        "calls": int(row["calls"]),
        "unpriced_calls": int(row["unpriced"]),
        "cost_micro_usd": row["cost"],
        "verdicts": verdicts,
        "per_verdict_micro_usd": (
            round(verdict_cost / verdicts) if verdicts and verdict_cost is not None else None
        ),
    }


def _by_purpose(conn: sqlite3.Connection, since: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT purpose, COUNT(*) AS calls, COUNT(*) - COUNT(cost_micro_usd) AS unpriced,"
        " SUM(cost_micro_usd) AS cost, SUM(prompt_tokens) AS prompt,"
        " SUM(completion_tokens) AS completion"
        " FROM llm_calls WHERE owner_id = ? AND at >= ?"
        " GROUP BY purpose ORDER BY cost IS NULL, cost DESC, calls DESC LIMIT ?",
        (OWNER_ID, since, PURPOSES_MAX),
    )
    return [
        {
            "purpose": r["purpose"],
            "calls": int(r["calls"]),
            "unpriced_calls": int(r["unpriced"]),
            "cost_micro_usd": r["cost"],
            "prompt_tokens": r["prompt"],
            "completion_tokens": r["completion"],
        }
        for r in rows
    ]


def _top(conn: sqlite3.Connection, since: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT c.at, c.purpose, c.model, c.prompt_tokens, c.cached_tokens,"
        " c.completion_tokens, c.latency_ms, c.outcome, c.cost_micro_usd,"
        " v.public_id, v.title FROM llm_calls c LEFT JOIN videos v ON v.id = c.video_id"
        " WHERE c.owner_id = ? AND c.at >= ? AND c.cost_micro_usd IS NOT NULL"
        " ORDER BY c.cost_micro_usd DESC, c.at DESC LIMIT ?",
        (OWNER_ID, since, TOP_CALLS),
    )
    return [
        {
            "at": int(r["at"]),
            "purpose": r["purpose"],
            "video_id": r["public_id"],
            "title": (r["title"] or "")[:TITLE_CHARS] or None,
            "model": r["model"],
            "prompt_tokens": r["prompt_tokens"],
            "cached_tokens": r["cached_tokens"],
            "completion_tokens": r["completion_tokens"],
            "latency_ms": int(r["latency_ms"]),
            "outcome": r["outcome"],
            "cost_micro_usd": int(r["cost_micro_usd"]),
        }
        for r in rows
    ]


def summary(conn: sqlite3.Connection, now: datetime) -> dict[str, Any]:
    starts = windows(now)
    return {
        "currency": "USD",
        "pricing": "list",
        "windows": {key: _window(conn, since) for key, since in starts.items()},
        "by_purpose": {"window": "30d", "items": _by_purpose(conn, starts["30d"])},
        "top": {"window": "30d", "items": _top(conn, starts["30d"])},
    }


def _now() -> datetime:
    return datetime.now().astimezone()


async def costs(request: Request, clock: Callable[[], datetime] = _now) -> Response:
    """`GET /dashboard/api/costs` — today, this month, 7 and 30 days, by purpose, the top calls."""
    now = clock()
    payload = await request.app.state.assembled.db.read(lambda c: summary(c, now))
    return JSONResponse(payload, headers=NO_STORE)
