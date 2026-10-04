"""The week's ranking (companion.md §3.4): one comparison per week, at most five 3s.

The model is a fake; nothing calls a real one.
"""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime

import pytest

from vidtheque_mcp.app import Assembled
from vidtheque_mcp.llm import LLMUnavailable
from vidtheque_mcp.verdicts import store, week
from vidtheque_mcp.verdicts.stage import VerdictStage

from .test_verdicts import FakeModel, FixedRoll, rows, video_id, verdict

# A Wednesday noon, local time, and its Monday.
WED = int(datetime(2026, 9, 30, 12, 0).timestamp())
MONDAY = "2026-09-28"


class Logged:
    def __init__(self) -> None:
        self.lines: list[tuple[str, str]] = []

    async def __call__(self, message: str, level: str = "info") -> None:
        self.lines.append((message, level))


def seed_week(conn: sqlite3.Connection, scores: list[int], at: int = WED) -> list[int]:
    """One video per score, published an hour apart from `at`, each with a verdict."""
    ids = []
    for i, score in enumerate(scores):
        vid = conn.execute(
            "INSERT INTO videos (source_id, url, title, duration_s, published_at, index_state)"
            " VALUES (?, ?, ?, 600, ?, 'ready')",
            (f"wk{at}{i:04d}", f"https://youtu.be/wk{i}", f"talk {i}", at - i * 3600),
        ).lastrowid
        store.save(conn, vid, score=score, reason=f"r{i}", summary="s", moments=[], profile_rev=0, model="m")
        ids.append(int(vid))
    return ids


@pytest.mark.parametrize("tz", ["Europe/Paris", "America/Los_Angeles", "UTC"])
def test_the_week_is_monday_to_sunday_local_and_sqlite_agrees(monkeypatch, tz: str) -> None:
    monkeypatch.setenv("TZ", tz)
    time.tzset()
    try:
        conn = sqlite3.connect(":memory:")
        start, end = week.bounds("2026-10-26")  # the week Paris leaves summer time
        for ts in (start, start + 1, end - 1, int(datetime(2026, 10, 25, 23, 59).timestamp())):
            sql = conn.execute(f"SELECT {week.WEEK_SQL.format(col='?')}", (ts,)).fetchone()[0]
            assert week.week_of(ts) == sql
        assert week.week_of(start) == "2026-10-26"
        assert week.week_of(start - 1) == "2026-10-19"
        assert week.week_of(end) == "2026-11-02"
    finally:
        monkeypatch.undo()
        time.tzset()


async def test_without_a_ranking_only_the_first_five_threes_show_as_three(assembled: Assembled) -> None:
    ids = await assembled.db.write(lambda c: seed_week(c, [3] * 7 + [2, 1]))
    ranked = await assembled.db.read(lambda c: week.view(c, MONDAY))
    assert [r.candidate.video_id for r in ranked] == ids[:8]  # the 1 is no candidate
    assert [r.tier for r in ranked] == [3] * 5 + [2] * 3


async def test_the_ranking_is_held_to_the_candidates_and_five_on_top(assembled: Assembled) -> None:
    db = assembled.db
    ids = await db.write(lambda c: seed_week(c, [2] * 8))
    answer = {
        # An unknown id, a repeat, and two candidates left out.
        "order": [ids[5], 999_999, ids[5], ids[1], ids[0], ids[2], ids[3], ids[4]],
        "top": [ids[4], ids[1], 999_999, ids[0], ids[2], ids[3], ids[7]],
    }
    model = FakeModel(answer)
    log = Logged()
    await week.WeekRanker(db, model, "api:fake").rank(MONDAY, log)

    ranked = await db.read(lambda c: week.view(c, MONDAY))
    order = [r.candidate.video_id for r in ranked]
    # The top five first, in the model's order; then the rest of its order; the missing last.
    assert order[:5] == [ids[4], ids[1], ids[0], ids[2], ids[3]]
    assert order[5] == ids[5] and set(order[6:]) == {ids[6], ids[7]}
    assert [r.tier for r in ranked] == [3] * 5 + [2] * 3
    assert model.labels[0]["purpose"] == "week_rank"
    assert f"[{ids[0]}] talk 0" in model.prompts[0]


async def test_a_week_is_ranked_again_only_when_its_candidates_moved(assembled: Assembled) -> None:
    db = assembled.db
    ids = await db.write(lambda c: seed_week(c, [2, 3]))
    model = FakeModel({"order": ids, "top": []})
    ranker = week.WeekRanker(db, model, "api:fake", clock=lambda: WED + 86_400)
    log = Logged()
    await ranker.rank_pending(log)
    await ranker.rank_pending(log)
    assert len(model.prompts) == 1
    # Its 3 was not picked for the top, so it shows as 2.
    assert [r.tier for r in await db.read(lambda c: week.view(c, MONDAY))] == [2, 2]
    # A rescore moves the set; a new verdict in the week shows after the ranked ones until reranked.
    new = await db.write(lambda c: seed_week(c, [2], at=WED + 7200))
    assert (await db.read(lambda c: week.view(c, MONDAY)))[-1].candidate.video_id == new[0]
    await ranker.rank_pending(log)
    assert len(model.prompts) == 2


async def test_a_failed_ranking_keeps_the_last_one_and_is_tried_again(assembled: Assembled) -> None:
    db = assembled.db
    ids = await db.write(lambda c: seed_week(c, [2, 2]))
    model = FakeModel(answers=[LLMUnavailable("http_500"), {"order": ids[::-1], "top": [ids[1]]}])
    ranker = week.WeekRanker(db, model, "api:fake", clock=lambda: WED)
    log = Logged()
    await ranker.rank_pending(log)
    assert any(level == "warn" and "not ranked" in msg for msg, level in log.lines)
    await ranker.rank_pending(log)
    ranked = await db.read(lambda c: week.view(c, MONDAY))
    assert [(r.candidate.video_id, r.tier) for r in ranked] == [(ids[1], 3), (ids[0], 2)]


async def test_a_lone_candidate_needs_no_model_call(assembled: Assembled) -> None:
    db = assembled.db
    [only] = await db.write(lambda c: seed_week(c, [3]))
    model = FakeModel(AssertionError("no call"))
    await week.WeekRanker(db, model, "api:fake", clock=lambda: WED).rank_pending(Logged())
    assert model.prompts == []
    [r] = await db.read(lambda c: week.view(c, MONDAY))
    assert (r.candidate.video_id, r.tier) == (only, 3)


async def test_the_verdict_job_ranks_once_no_other_verdict_waits(assembled: Assembled) -> None:
    db = assembled.db
    vid = await video_id(db, "kCc8FmEb1nY")
    other = await video_id(db, "zduSFxRajkE")
    now = int(time.time())
    await db.write(lambda c: c.execute("UPDATE videos SET published_at = ? WHERE id IN (?, ?)", (now, vid, other)))
    ranker_model = FakeModel({"order": [], "top": []})
    stage = VerdictStage(
        assembled.deps,
        FakeModel(verdict(score=2)),
        "api:fake",
        rng=FixedRoll(1.0),
        ranker=week.WeekRanker(db, ranker_model, "api:fake"),
    )
    assembled.runner.handlers["verdict"] = stage
    await db.write(lambda c: (store.queue(c, vid), store.queue(c, other)))
    assert await assembled.runner.run_once() is True
    assert ranker_model.prompts == []  # the second verdict still waits
    assert await assembled.runner.run_once() is True
    assert len(ranker_model.prompts) == 1
    feed_rows = await rows(db, "SELECT video_id, rank FROM week_ranks ORDER BY rank")
    assert {r["video_id"] for r in feed_rows} == {vid, other}
    run = (await rows(db, "SELECT candidates, outcome FROM week_rank_runs"))[0]
    assert run["outcome"] == "ok" and len(json.loads(run["candidates"])) == 2
