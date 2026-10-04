"""Measuring the feed against YouTube — companion.md §3.3, dashboard.md §25.10–25.11.

The rules a wrong number would hide: watch time is capped twice, an open is
never a hit, a miss waits for its verdict, and a share is a miss only when the
feed did not offer the video.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

from vidtheque_mcp.db import migrations
from vidtheque_mcp.db.connection import open_write_connection
from vidtheque_mcp.profile import ledger
from vidtheque_mcp.profile.signals import record_signal

# Wednesday 2026-10-14 12:00 UTC: its week starts Monday 2026-10-12.
NOW = datetime(2026, 10, 14, 12, tzinfo=timezone.utc)
T = int(NOW.timestamp())
MONDAY = int(datetime(2026, 10, 12, tzinfo=timezone.utc).timestamp())


@pytest.fixture
def conn(tmp_path: Path) -> sqlite3.Connection:
    c = open_write_connection(tmp_path / "vidtheque.db")
    migrations.migrate(c)
    yield c
    c.close()


def _video(conn, source_id: str, *, duration=600.0, channel="UCa", published=T - 3600) -> int:
    return conn.execute(
        "INSERT INTO videos (source_id, url, title, duration_s, index_state, channel_id, published_at)"
        " VALUES (?, ?, 't', ?, 'ready', ?, ?)",
        (source_id, f"https://youtu.be/{source_id}", duration, channel, published),
    ).lastrowid


def _verdict(conn, vid: int, score: int, moments=()) -> None:
    conn.execute(
        "INSERT INTO verdicts (video_id, score, reason, summary, moments, profile_rev, model)"
        " VALUES (?, ?, 'r', 's', ?, 1, 'm')",
        (vid, score, json.dumps([{"cue_id": 1, "offset_s": m, "why": "w"} for m in moments])),
    )


def _follow(conn, channel: str, *, created=T - 86_400) -> None:
    cid = conn.execute(
        "INSERT INTO collections (slug, title, kind, created_at) VALUES (?, 'c', 'channel', ?)",
        (f"f-{channel}", created),
    ).lastrowid
    conn.execute("INSERT INTO follows (collection_id) VALUES (?)", (cid,))
    seen = _video(conn, f"seen-{channel}"[:11].ljust(11, "x"), channel=channel, published=None)
    conn.execute("INSERT INTO collection_videos (collection_id, video_id) VALUES (?, ?)", (cid, seen))


def _watch(conn, source_id: str, offset: float, seconds: float | None, *, at=T - 600) -> int:
    sid = record_signal(conn, "watch", video_id=source_id, offset_s=offset, now=at)
    if seconds is not None:
        assert ledger.record_watched(conn, sid, seconds, now=at + int(seconds)) is not None
    return sid


def test_watch_time_is_capped_by_the_video_and_the_clock(conn) -> None:
    _video(conn, "kCc8FmEb1nY", duration=600)
    first = record_signal(conn, "watch", video_id="kCc8FmEb1nY", offset_s=500, now=T - 7200)
    # Two hours away, 100 s of video left after the moment.
    assert ledger.record_watched(conn, first, 7200, now=T).watched_s == 100
    second = record_signal(conn, "watch", video_id="kCc8FmEb1nY", offset_s=0, now=T - 30)
    # The phone claims ten minutes; the server saw thirty seconds go by, plus slack.
    assert ledger.record_watched(conn, second, 600, now=T).watched_s == 30 + ledger.CLOCK_SLACK_S
    # Set once: a retry answers what was stored.
    again = ledger.record_watched(conn, second, 5, now=T)
    assert again.already and again.watched_s == 90


def test_watch_time_only_lands_on_a_watch(conn) -> None:
    _video(conn, "kCc8FmEb1nY")
    opened = record_signal(conn, "open", video_id="kCc8FmEb1nY", now=T)
    assert ledger.record_watched(conn, opened, 60, now=T) is None
    assert ledger.record_watched(conn, 12345, 60, now=T) is None


@pytest.mark.parametrize(
    ("moments", "spans", "kept"),
    [
        ([100.0, 400.0], [(95.0, 170.0)], True),  # one of two
        ([100.0, 400.0, 500.0], [(95.0, 170.0)], False),  # one of three
        ([100.0, 400.0, 500.0], [(95.0, 170.0), (400.0, 470.0)], True),
        ([100.0], [(0.0, 120.0)], False),  # left before a minute of it
        ([100.0], [(110.0, 200.0)], False),  # started past it
        ([590.0], [(590.0, 600.0)], True),  # to the end of the video
        ([], [(0.0, 290.0)], False),
        ([], [(0.0, 150.0), (300.0, 450.0)], True),  # half of 600 s
        ([100.0], [], False),
    ],
)
def test_kept_means_past_half_the_moments(moments, spans, kept) -> None:
    assert ledger.kept(moments, 600.0, spans) is kept


def test_weeks_count_hits_regret_and_misses(conn) -> None:
    _follow(conn, "UCa")
    up = _video(conn, "upupupupupu")
    watched = _video(conn, "watchedwatc")
    opened = _video(conn, "openedopene")
    regret = _video(conn, "regretregre")
    for vid in (up, watched, opened):
        _verdict(conn, vid, 2, moments=[100.0])
    _verdict(conn, regret, 1)
    conn.execute("INSERT INTO feedback (video_id, state, at) VALUES (?, 'up', ?)", (up, T))
    _watch(conn, "watchedwatc", 100, 120)
    # Opened and handed off, but back in twenty seconds: not a hit.
    record_signal(conn, "open", video_id="openedopene", now=T - 900)
    _watch(conn, "openedopene", 100, 20)
    _watch(conn, "regretregre", 0, 300)
    conn.execute("INSERT INTO feedback (video_id, state, at) VALUES (?, 'down', ?)", (regret, T))

    # A share of a 1, a 3, a video from an unfollowed channel, one not indexed yet.
    shared_low = _video(conn, "sharedlow01")
    _verdict(conn, shared_low, 1)
    shared_top = _video(conn, "sharedtop01")
    _verdict(conn, shared_top, 3)
    _video(conn, "elsewhere01", channel="UCb", published=T - 86_400 * 30)
    for source_id in ("sharedlow01", "sharedlow01", "sharedtop01", "elsewhere01", "notyet00001"):
        ledger.record_share(conn, source_id, now=T - 60)

    week = ledger.weeks(conn, NOW)["weeks"][0]
    assert week["start"] == MONDAY and week["current"]
    # The shared 3 was offered too, and never opened here.
    assert week["hits"] == {"kept": 2, "offered": 4, "rate": 0.5, "capped": False}
    # Two watched a minute or more; the 1 thumbed down after it is the regret.
    assert week["regret"] == {"down": 1, "watched": 2, "rate": 0.5, "capped": False}
    assert week["misses"] == {"count": 2, "pending": 1, "shared": 4, "skipped": 0, "capped": False}
    last = ledger.weeks(conn, NOW)["weeks"][1]
    assert last["hits"]["offered"] == 0 and last["hits"]["rate"] is None


def test_a_miss_waits_for_its_verdict_and_reads_the_follow_at_share_time(conn) -> None:
    _follow(conn, "UCa", created=T)
    vid = _video(conn, "laterfollow")
    _verdict(conn, vid, 3)
    # Followed only after the share: the feed would not have offered it then.
    assert ledger.miss_of(conn, "laterfollow", T - 10) == (True, "channel not followed")
    assert ledger.miss_of(conn, "laterfollow", T + 10) == (False, "scored 3")
    _video(conn, "unjudged001")
    assert ledger.miss_of(conn, "unjudged001", T + 10) == (None, "no verdict yet")
    assert ledger.miss_of(conn, "absent00001", T) == (None, "not indexed yet")


def test_a_skip_called_wrong_in_the_brief_is_a_miss(conn) -> None:
    # 0019 (#158) lands after this; its table as that branch defines it.
    conn.execute(
        "CREATE TABLE skip_verdicts (owner_id INTEGER NOT NULL DEFAULT 1, video_id INTEGER NOT NULL,"
        " answer TEXT NOT NULL, source TEXT NOT NULL, score INTEGER NOT NULL,"
        " at INTEGER NOT NULL, PRIMARY KEY (owner_id, video_id))"
    )
    wrong = _video(conn, "skippedwron")
    fair = _video(conn, "skippedfair")
    _verdict(conn, wrong, 1)
    _verdict(conn, fair, 0)
    conn.execute("INSERT INTO skip_verdicts VALUES (1, ?, 'wrong', 'audit', 1, ?)", (wrong, T - 60))
    conn.execute("INSERT INTO skip_verdicts VALUES (1, ?, 'right', 'audit', 0, ?)", (fair, T - 60))
    # Also shared: still one miss.
    ledger.record_share(conn, "skippedwron", now=T - 30)
    misses = ledger.weeks(conn, NOW)["weeks"][0]["misses"]
    assert misses == {"count": 1, "pending": 0, "shared": 1, "skipped": 1, "capped": False}


def test_week_starts_on_the_boxs_monday() -> None:
    starts = ledger.week_starts(NOW, 3)
    assert starts == [MONDAY, MONDAY - 7 * 86_400, MONDAY - 14 * 86_400]
