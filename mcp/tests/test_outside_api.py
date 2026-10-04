"""Discovery's endpoints (dashboard.md §27): picks, thumbs, watches, trial follows."""

from __future__ import annotations

import json
import time
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from vidtheque_mcp.brief import build
from vidtheque_mcp.db.connection import open_write_connection

from .test_dashboard_feed import API, JSON_BEARER
from .test_dashboard_following import BEARER, _corpus, owner_client

CHANNEL = "https://www.youtube.com/@outsidechannel"


@pytest.fixture
def client(tmp_path: Path):
    data = _corpus(tmp_path)
    week = build.week_of(datetime.now().astimezone()).key
    conn = open_write_connection(data / "vidtheque.db")
    try:
        conn.execute("BEGIN IMMEDIATE")
        moments = json.dumps([{"offset_s": 600.0, "end_s": 900.0, "why": "the harness"}])
        conn.execute(
            "INSERT INTO outside_picks (id, source_id, week, because, title, channel_id, channel_name,"
            " channel_url, duration_s, state, score, reason, summary, moments)"
            " VALUES (1, 'outside0001', ?, 'Coding agent evals', 'An eval talk', 'UCoutside', 'Outside',"
            " ?, 2400, 'shown', 2, 'why', 'what', ?)",
            (week, CHANNEL, moments),
        )
        conn.execute(
            "INSERT INTO outside_picks (id, source_id, week, because, title, state, score)"
            " VALUES (2, 'outside0002', ?, 'Coding agent evals', 'Not shown', 'judged', 1)",
            (week,),
        )
        conn.execute(
            "INSERT INTO speaker_suggestions (id, week, name, name_key, talk_title, talks, reason)"
            " VALUES (1, ?, 'Ada Lovelace', 'adalovelace', 'A talk', ?, 'Spoke in “A talk”')",
            (
                week,
                json.dumps([{"video_id": "adatalk0001", "title": "Ada again", "channel": "Conf"}]),
            ),
        )
        conn.execute("COMMIT")
    finally:
        conn.close()
    with owner_client(tmp_path) as c:
        c.week = week  # type: ignore[attr-defined]
        yield c


def test_the_week_lists_shown_picks_and_the_speaker(client: TestClient) -> None:
    assert client.get(f"{API}/outside").status_code == 401
    week = client.get(f"{API}/outside", headers=BEARER).json()
    assert week["week"] == client.week  # type: ignore[attr-defined]
    [pick] = week["picks"]
    assert pick["id"] == 1 and pick["because"] == "Coding agent evals"
    assert pick["moments"][0]["url"] == "https://youtu.be/outside0001?t=600"
    assert pick["follow"] == {"state": "none", "until": None}
    assert week["speaker"]["name"] == "Ada Lovelace"
    assert week["speaker"]["channel"] is None
    assert week["speaker"]["talks"][0]["url"] == "https://youtu.be/adatalk0001"
    assert client.get(f"{API}/outside/1", headers=BEARER).json()["title"] == "An eval talk"
    assert client.get(f"{API}/outside/2", headers=BEARER).json()["error"] == "E_UNKNOWN_PICK"
    assert (
        client.get(f"{API}/outside?week=2026-10-07", headers=BEARER).json()["error"]
        == "E_BAD_PARAM"
    )
    earlier = (date.fromisoformat(client.week) - timedelta(days=7)).isoformat()  # type: ignore[attr-defined]
    assert client.get(f"{API}/outside?week={earlier}", headers=BEARER).json()["picks"] == []


def test_a_thumb_up_offers_a_trial_follow_that_ends_in_14_days(client: TestClient) -> None:
    up = client.post(
        f"{API}/outside/feedback", json={"id": 1, "state": "up"}, headers=JSON_BEARER
    ).json()
    assert up["offer"] == {"channel": "Outside", "url": CHANNEL, "days": 14}
    followed = client.post(f"{API}/outside/follow", json={"pick": 1}, headers=JSON_BEARER).json()
    assert followed["already"] is False
    assert abs(followed["trial_until"] - (time.time() + 14 * 86_400)) < 120
    pick = client.get(f"{API}/outside/1", headers=BEARER).json()
    assert pick["feedback"] == "up"
    assert pick["follow"]["state"] == "trial"
    # Followed now: no second offer, and a second follow changes nothing.
    again = client.post(
        f"{API}/outside/feedback", json={"id": 1, "state": "up"}, headers=JSON_BEARER
    ).json()
    assert again["offer"] is None
    assert (
        client.post(f"{API}/outside/follow", json={"pick": 1}, headers=JSON_BEARER).json()[
            "already"
        ]
        is True
    )
    # A speaker with no channel has nothing to follow.
    refused = client.post(f"{API}/outside/follow", json={"speaker": 1}, headers=JSON_BEARER)
    assert refused.status_code == 409 and refused.json()["error"] == "E_NO_CHANNEL"


def test_watches_are_capped_and_count_toward_the_outside_hit_rate(client: TestClient) -> None:
    long = client.post(
        f"{API}/outside/watched",
        json={"id": 1, "offset_s": 600, "watched_s": 99999},
        headers=JSON_BEARER,
    ).json()
    assert long["watched_s"] == 1800.0  # what is left of the video from 600 s
    ledger = client.get(f"{API}/valued-time", headers=BEARER).json()
    assert ledger["weeks"][0]["outside"] == {"shown": 1, "kept": 1, "rate": 1.0}
    refused = client.post(
        f"{API}/outside/watched",
        json={"id": 2, "offset_s": 0, "watched_s": 10},
        headers=JSON_BEARER,
    )
    assert refused.json()["error"] == "E_UNKNOWN_PICK"


def test_a_dismissed_speaker_is_gone_from_the_week(client: TestClient) -> None:
    done = client.post(
        f"{API}/outside/speaker", json={"id": 1, "state": "dismissed"}, headers=JSON_BEARER
    )
    assert done.json() == {"id": 1, "state": "dismissed"}
    assert client.get(f"{API}/outside", headers=BEARER).json()["speaker"] is None
    bad = client.post(
        f"{API}/outside/speaker", json={"id": 1, "state": "open"}, headers=JSON_BEARER
    )
    assert bad.json()["error"] == "E_BAD_PARAM"
