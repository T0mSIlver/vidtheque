"""The weekly brief (companion.md §6.1): built once on Sunday, pushed once, and
its endpoints (dashboard.md §26). The model is a fake; nothing calls a real one.
"""

from __future__ import annotations

import json
import random
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from vidtheque_mcp.app import Assembled
from vidtheque_mcp.brief import build
from vidtheque_mcp.brief.weekly import Weekly
from vidtheque_mcp.db.connection import open_write_connection
from vidtheque_mcp.llm import LLMUnavailable

from .test_dashboard_feed import API, JSON_BEARER
from .test_dashboard_following import BEARER, _corpus, owner_client
from .test_nightly import Clock, FakeModel

SUNDAY = datetime(2026, 10, 4, 10, 0, tzinfo=timezone.utc)
WEEK = build.week_of(SUNDAY)


class Phones:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    async def brief(self, week: str, line: str) -> int:
        self.sent.append((week, line))
        return 1


def seed_week(conn: sqlite3.Connection, now: int) -> dict[str, int]:
    """Two worth-it videos and five skipped ones this week, one 3 from last week.

    The two worth-it ones hit the wanted entry; one skip was sunk by the unwanted one.
    """
    ids = dict(conn.execute("SELECT source_id, id FROM videos"))
    for n in range(4):
        ids[f"skip{n}"] = conn.execute(
            "INSERT INTO videos (source_id, url, title, duration_s, index_state, channel_name)"
            " VALUES (?, ?, 'plain', 60, 'ready', 'Noise')",
            (f"skip{n}", f"https://youtu.be/skip{n}"),
        ).lastrowid
    wanted = conn.execute("INSERT INTO profile_entries (text, weight, source) VALUES ('Evals', 0.9, 'owner')").lastrowid
    unwanted = conn.execute(
        "INSERT INTO profile_entries (text, weight, source) VALUES ('Launch hype', -0.7, 'owner')"
    ).lastrowid
    up = lambda s: json.dumps([{"entry_id": wanted, "direction": "up", "strength": s}])  # noqa: E731
    for source_id, score, age_days, matches in (
        ("kCc8FmEb1nY", 3, 1, up(2)),
        ("zduSFxRajkE", 2, 2, up(1)),
        ("eMlx5fFNoYc", 0, 1, json.dumps([{"entry_id": unwanted, "direction": "down", "strength": 2}])),
        *((f"skip{n}", n % 2, 3, "[]") for n in range(4)),
    ):
        cue = conn.execute(
            "SELECT id, start_s FROM cues WHERE video_id = ? ORDER BY seq LIMIT 1", (ids[source_id],)
        ).fetchone()
        moments = [{"cue_id": cue[0], "offset_s": cue[1], "why": "the point"}] if cue else []
        conn.execute("UPDATE videos SET published_at = ? WHERE id = ?", (now - age_days * 86_400, ids[source_id]))
        conn.execute(
            "INSERT INTO verdicts (video_id, score, reason, summary, moments, profile_rev, model, matches)"
            " VALUES (?, ?, 'why', 'summary', ?, 1, 'm', ?)",
            (ids[source_id], score, json.dumps(moments), matches),
        )
    ids["wanted"], ids["unwanted"] = wanted, unwanted
    return ids


def cue_of(conn: sqlite3.Connection, vid: int) -> int:
    return int(conn.execute("SELECT id FROM cues WHERE video_id = ? ORDER BY seq LIMIT 1", (vid,)).fetchone()[0])


async def test_built_once_on_sunday_with_receipts_checked_and_pushed_once(assembled: Assembled) -> None:
    ids = await assembled.db.write(lambda c: seed_week(c, int(SUNDAY.timestamp())))
    first, second = await assembled.db.read(
        lambda c: (cue_of(c, ids["kCc8FmEb1nY"]), cue_of(c, ids["zduSFxRajkE"]))
    )
    model = FakeModel(
        {
            "topics": [
                {
                    "entry_id": ids["wanted"],
                    "points": [{"cue_id": first, "said": "Karpathy grades with unit tests."}, {"cue_id": 999_999, "said": "invented"}],
                    "disagreement": {"cue_ids": [first, second], "about": "whether LLM judges are enough"},
                },
                {"entry_id": 424242, "points": [{"cue_id": first, "said": "not a topic it was given"}]},
            ]
        }
    )
    clock, phones = Clock(SUNDAY - timedelta(days=1)), Phones()
    weekly = Weekly(assembled.db, model, "api:fake", phones, hour=9, clock=clock, rng=random.Random(1))

    await weekly.tick()  # Saturday: nothing
    assert weekly._task is None
    clock.now = SUNDAY
    assert await weekly.run_once() == WEEK.key
    assert await weekly.run_once() == WEEK.key  # the next tick finds it built and pushed

    assert phones.sent == [(WEEK.key, "Top pick: " + await _title(assembled, ids["kCc8FmEb1nY"]) + " and 1 more.")]
    assert len(model.prompts) == 1 and model.labels[0]["purpose"] == "weekly_brief"
    body = json.loads((await assembled.db.read(lambda c: c.execute("SELECT body FROM briefs").fetchone()))[0])
    assert body["picks"] == ["kCc8FmEb1nY", "zduSFxRajkE"]
    # Three of the five skipped videos, never a worth-it one.
    assert len(body["audit"]) == 3 and set(body["audit"]) <= {"eMlx5fFNoYc", "skip0", "skip1", "skip2", "skip3"}
    [topic] = body["said"]
    assert [p["said"] for p in topic["points"]] == ["Karpathy grades with unit tests."]
    assert topic["points"][0]["url"].startswith("https://youtu.be/kCc8FmEb1nY?t=")
    assert [s["video_id"] for s in topic["disagreement"]["sides"]] == ["kCc8FmEb1nY", "zduSFxRajkE"]


async def test_a_failed_model_call_costs_only_its_section(assembled: Assembled) -> None:
    await assembled.db.write(lambda c: seed_week(c, int(SUNDAY.timestamp())))
    phones = Phones()
    weekly = Weekly(assembled.db, FakeModel(LLMUnavailable("timeout")), "api:fake", phones, clock=Clock(SUNDAY))
    await weekly.run_once()
    body = json.loads((await assembled.db.read(lambda c: c.execute("SELECT body FROM briefs").fetchone()))[0])
    assert body["said"] == [] and body["said_note"] == "the model call failed"
    assert body["picks"] and len(phones.sent) == 1


async def _title(parts: Assembled, vid: int) -> str:
    return (await parts.db.read(lambda c: c.execute("SELECT title FROM videos WHERE id = ?", (vid,)).fetchone()))[0]


# ------------------------------------------------------------------ endpoints


@pytest.fixture
def client(tmp_path: Path):
    data = _corpus(tmp_path)
    conn = open_write_connection(data / "vidtheque.db")
    try:
        conn.execute("BEGIN IMMEDIATE")
        now = int(time.time())
        ids = seed_week(conn, now)
        week = build.week_of(datetime.now().astimezone())
        # Karpathy's follow holds the skipped videos: four judged, none worth it, none watched.
        karpathy = conn.execute("SELECT id FROM collections WHERE title = 'Andrej Karpathy'").fetchone()[0]
        conn.executemany(
            "INSERT INTO collection_videos (collection_id, video_id) VALUES (?, ?)",
            [(karpathy, ids[f"skip{n}"]) for n in range(4)],
        )
        # A nightly change this week, which the brief offers to revert.
        conn.execute(
            "INSERT INTO profile_events (at, actor, op, entry_id, before, after, reason)"
            " VALUES (?, 'nightly', 'reweight', ?, ?, ?, '3 searches on evals')",
            (week.since_at + 60, ids["wanted"], json.dumps({"text": "Evals", "weight": 0.6, "live": True}),
             json.dumps({"text": "Evals", "weight": 0.9, "live": True})),
        )
        conn.execute(
            "INSERT INTO briefs (week, since_at, until_at, body) VALUES (?, ?, ?, ?)",
            (week.key, week.since_at, week.until_at,
             json.dumps({"picks": ["kCc8FmEb1nY"], "audit": ["eMlx5fFNoYc", "skip0"], "said": [], "said_note": None})),
        )
        conn.execute("COMMIT")
    finally:
        conn.close()
    with owner_client(tmp_path) as c:
        c.week = week.key  # type: ignore[attr-defined]
        yield c


def test_the_brief_reads_live_parts_and_refuses_what_it_should(client: TestClient) -> None:
    assert client.get(f"{API}/brief").status_code == 401
    brief = client.get(f"{API}/brief", headers=BEARER).json()
    assert brief["week"] == client.week  # type: ignore[attr-defined]
    # The brief's own week of the ledger, in valued-time's shape.
    [week] = brief["ledger"]["weeks"]
    assert week["current"] is True and week["start"] == brief["since"]
    assert brief["picks"][0]["moments"][0]["url"].startswith("https://youtu.be/kCc8FmEb1nY?t=")
    sunk = {a["video_id"]: a["sunk_by"] for a in brief["audit"]}
    assert sunk["eMlx5fFNoYc"]["text"] == "Launch hype" and sunk["skip0"] is None
    [change] = brief["profile_changes"]
    assert change["reason"] == "3 searches on evals" and change["reverted"] is False
    flagged = [c["title"] for c in brief["channels"] if c["suggest_pause"]]
    assert flagged == ["Andrej Karpathy"]  # the paused one is never flagged

    # Revert goes through the profile's own route, and the brief shows it.
    client.post(f"{API}/profile/revert", json={"event_id": change["event_id"]}, headers=JSON_BEARER)
    assert client.get(f"{API}/brief", headers=BEARER).json()["profile_changes"][0]["reverted"] is True

    for query, code in (("?week=2026-10-06", "E_BAD_PARAM"), ("?week=2020-01-06", "E_NO_BRIEF")):
        assert client.get(f"{API}/brief{query}", headers=BEARER).json()["error"] == code


def test_checkin_is_one_answer_per_week(client: TestClient) -> None:
    week = client.week  # type: ignore[attr-defined]
    for rating in (2, 4):
        answer = client.post(f"{API}/brief/checkin", json={"week": week, "rating": rating, "missing": "  more  GPU  "}, headers=JSON_BEARER)
        assert answer.json() == {"week": week, "rating": rating, "missing": "more GPU"}
    assert client.get(f"{API}/brief", headers=BEARER).json()["checkin"]["rating"] == 4
    for body, code in (({"week": week, "rating": 6}, "E_BAD_PARAM"), ({"week": "2020-01-06", "rating": 3}, "E_NO_BRIEF")):
        assert client.post(f"{API}/brief/checkin", json=body, headers=JSON_BEARER).json()["error"] == code


def test_id_watch_this_is_a_thumb_and_a_proposal_never_a_reweight(client: TestClient) -> None:
    wrong = client.post(f"{API}/skips", json={"video_id": "eMlx5fFNoYc", "answer": "wrong", "source": "audit"}, headers=JSON_BEARER).json()
    assert wrong["feedback"] == "up"
    assert wrong["proposal"]["text"] == "Launch hype" and wrong["proposal"]["to"] == pytest.approx(-0.4)
    entries = {e["text"]: e["weight"] for e in client.get(f"{API}/profile", headers=BEARER).json()["entries"]}
    assert entries["Launch hype"] == pytest.approx(-0.7)
    brief = client.get(f"{API}/brief", headers=BEARER).json()
    assert {a["video_id"]: a["answer"] for a in brief["audit"]}["eMlx5fFNoYc"] == "wrong"
    # #157's ledger counts it as a miss.
    assert brief["ledger"]["weeks"][0]["misses"]["count"] == 1

    # Taking it back takes back the thumb it set.
    right = client.post(f"{API}/skips", json={"video_id": "eMlx5fFNoYc", "answer": "right"}, headers=JSON_BEARER).json()
    assert right == {"video_id": "eMlx5fFNoYc", "answer": "right", "feedback": "none", "proposal": None}

    refused = client.post(f"{API}/skips", json={"video_id": "kCc8FmEb1nY", "answer": "wrong"}, headers=JSON_BEARER)
    assert refused.status_code == 400
