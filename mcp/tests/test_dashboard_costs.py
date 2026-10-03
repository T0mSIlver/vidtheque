"""`GET /dashboard/api/costs` — dashboard.md §25.8, companion.md §4.1.

The windows, the cost per verdict and the top calls, from `llm_calls` rows
written here; access is the feed's and is tested with it.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from vidtheque_mcp.dashboard import costs
from vidtheque_mcp.db import migrations
from vidtheque_mcp.db.connection import open_write_connection

from .test_dashboard_feed import API, BEARER, client  # noqa: F401 — the fixture

# The 15th at noon: today, this month, 7 and 30 days all differ.
NOW = datetime(2026, 10, 15, 12, 0, tzinfo=timezone(timedelta(hours=2)))
HOUR = 3600
DAY = 86_400


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    c = open_write_connection(tmp_path / "v.db")
    migrations.migrate(c)
    yield c
    c.close()


def _call(
    c: sqlite3.Connection,
    ago_s: int,
    purpose: str,
    cost: int | None,
    outcome: str = "ok",
    video_id: int | None = None,
) -> None:
    c.execute(
        "INSERT INTO llm_calls (at, purpose, video_id, backend, model, prompt_tokens,"
        " completion_tokens, latency_ms, outcome, cost_micro_usd)"
        " VALUES (?, ?, ?, 'api', 'zai-glm-5-3', 1000, 100, 900, ?, ?)",
        (int(NOW.timestamp()) - ago_s, purpose, video_id, outcome, cost),
    )


def test_the_windows_and_the_cost_per_verdict(conn: sqlite3.Connection) -> None:
    _call(conn, HOUR, "verdict", 3000)
    _call(conn, HOUR, "verdict_explore", 1000)  # the rescore counts toward its verdict
    _call(conn, 2 * HOUR, "verdict", 2000, outcome="invalid_output")  # paid, no verdict
    _call(conn, 3 * DAY, "nightly_update", 5000)
    _call(conn, 3 * DAY, "verdict", None)  # unpriced: counted, never summed as 0
    _call(conn, 20 * DAY, "verdict", 4000)  # last month, inside 30 days
    _call(conn, 40 * DAY, "verdict", 9000)  # outside every window

    w = costs.summary(conn, NOW)["windows"]

    assert (w["today"]["calls"], w["today"]["cost_micro_usd"]) == (3, 6000)
    # Two verdict calls answered, but only one was a verdict; both were paid for.
    assert (w["today"]["verdicts"], w["today"]["per_verdict_micro_usd"]) == (1, 6000)
    assert (w["7d"]["calls"], w["7d"]["unpriced_calls"], w["7d"]["cost_micro_usd"]) == (5, 1, 11000)
    assert w["month"]["calls"] == 5
    assert (w["30d"]["calls"], w["30d"]["cost_micro_usd"]) == (6, 15000)
    assert w["30d"]["per_verdict_micro_usd"] == round(10000 / 3)


def test_an_empty_or_unpriced_window_is_null_not_zero(conn: sqlite3.Connection) -> None:
    empty = costs.summary(conn, NOW)["windows"]["today"]
    assert (empty["calls"], empty["cost_micro_usd"], empty["per_verdict_micro_usd"]) == (
        0, None, None,
    )
    _call(conn, HOUR, "verdict", None)
    unpriced = costs.summary(conn, NOW)["windows"]["today"]
    assert (unpriced["unpriced_calls"], unpriced["cost_micro_usd"]) == (1, None)
    assert unpriced["per_verdict_micro_usd"] is None


def test_by_purpose_and_the_most_expensive_calls(conn: sqlite3.Connection) -> None:
    for n in range(12):
        _call(conn, HOUR + n, "verdict", 100 * (n + 1))
    _call(conn, HOUR, "nightly_update", 50_000)
    _call(conn, HOUR, "unknown", None)

    summary = costs.summary(conn, NOW)

    purposes = summary["by_purpose"]["items"]
    assert [p["purpose"] for p in purposes] == ["nightly_update", "verdict", "unknown"]
    assert purposes[1]["calls"] == 12 and purposes[1]["prompt_tokens"] == 12_000
    top = summary["top"]["items"]
    assert len(top) == costs.TOP_CALLS
    assert [t["cost_micro_usd"] for t in top[:3]] == [50_000, 1200, 1100]


def test_the_route_answers_the_summary(client) -> None:  # noqa: F811
    body = client.get(f"{API}/costs", headers=BEARER).json()
    assert (body["currency"], body["pricing"]) == ("USD", "list")
    assert set(body["windows"]) == {"today", "month", "7d", "30d"}
    assert body["windows"]["30d"]["calls"] == 0
