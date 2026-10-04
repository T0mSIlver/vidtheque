"""Claude's picks (companion.md §6.4): the `recommend` tool, its receipts, its cap, its read."""

from __future__ import annotations

import json
import time
from datetime import datetime

from vidtheque_mcp.app import Assembled
from vidtheque_mcp.profile import ledger
from vidtheque_mcp.tools import recommend as recommend_tool
from vidtheque_mcp.tools.recommend import PickMoment
from vidtheque_mcp.verdicts import picks

VIDEO = "kCc8FmEb1nY"  # cues 0–2.8, 3–5.8, 6–8.8, 9–11.8, 12–14.8, then 420–423


async def recommend(deps, **kwargs):
    return await recommend_tool.recommend(deps, **kwargs)


def structured(result) -> dict:
    assert not result.is_error, result.content[0].text
    return result.structured_content


def error_code(result) -> str:
    assert result.is_error
    return result.structured_content["code"]


async def stored(deps) -> list[tuple]:
    return await deps.db.read(
        lambda c: [tuple(r) for r in c.execute("SELECT day, video_id, reason, moments FROM picks ORDER BY id")]
    )


async def judge_recently(deps, source_id: str, score: int = 2) -> int:
    """A fresh video with a verdict written just now."""

    def write(c) -> int:
        vid = int(c.execute("SELECT id FROM videos WHERE source_id = ?", (source_id,)).fetchone()[0])
        now = int(time.time())
        c.execute("UPDATE videos SET published_at = ? WHERE id = ?", (now - 3600, vid))
        c.execute(
            "INSERT INTO verdicts (video_id, score, reason, summary, moments, profile_rev, model)"
            " VALUES (?, ?, 'On caching.', 'Paged attention, in short.', '[]', 0, 'm:x')",
            (vid, score),
        )
        return vid

    return await deps.db.write(write)


# ------------------------------------------------------------------ the pick


async def test_a_moment_is_kept_only_on_this_videos_cues_and_ends_where_its_cue_does(
    assembled: Assembled,
) -> None:
    result = structured(
        await recommend(
            assembled.deps,
            video_id=VIDEO,
            reason="The clearest account of the KV cache you will find.",
            moments=[
                PickMoment(start_s=1.0, end_s=10.0, why="the cache, start to finish"),
                # 100 s falls between cues: dropped, not moved to the nearest one.
                PickMoment(start_s=100.0, end_s=421.0, why="the gap"),
            ],
        )
    )
    assert result["today"] == 1 and not result["replaced"]
    assert [(m["offset_s"], m["end_s"], m["why"]) for m in result["moments"]] == [
        (1.0, 11.8, "the cache, start to finish")
    ]
    assert [d["start_s"] for d in result["dropped"]] == [100.0]
    [(day, _, reason, moments)] = await stored(assembled.deps)
    assert day == picks.local_day(time.time())
    assert reason == "The clearest account of the KV cache you will find."
    assert [m["end_s"] for m in json.loads(moments)] == [11.8]


async def test_five_picks_a_day_and_a_repick_replaces_without_spending_one(
    assembled: Assembled,
) -> None:
    def add(c, vid: int, now: float) -> picks.Saved:
        return picks.save(c, vid, "worth it", [], client=None, now=now)

    vids = await assembled.deps.db.write(
        lambda c: [
            c.execute(
                "INSERT INTO videos (source_id, url, title, duration_s, index_state) VALUES (?, ?, 't', 60, 'ready')",
                (f"pick{i:07d}", f"https://youtu.be/pick{i:07d}"),
            ).lastrowid
            for i in range(6)
        ]
    )
    now = time.time()
    for vid in vids[:5]:
        await assembled.deps.db.write(lambda c, vid=vid: add(c, vid, now))
    again = await assembled.deps.db.write(lambda c: add(c, vids[0], now))
    assert again.replaced and again.today == 5

    sixth = await recommend(assembled.deps, video_id="pick0000005", reason="one more")
    assert error_code(sixth) == "E_PICK_LIMIT"
    assert len(await stored(assembled.deps)) == 5
    # A new day has its own five.
    tomorrow = await assembled.deps.db.write(lambda c: add(c, vids[5], now + 86_400))
    assert tomorrow.today == 1


async def test_a_reason_about_pay_or_a_job_search_is_refused_and_nothing_is_written(
    assembled: Assembled,
) -> None:
    for kwargs in (
        {"reason": "Good prep for the interview loop."},
        {"reason": "Fine.", "moments": [PickMoment(start_s=1.0, end_s=2.0, why="salary bands")]},
        {"reason": "two\n\nlines " + "x" * 300},
    ):
        assert error_code(await recommend(assembled.deps, video_id=VIDEO, **kwargs)) == "E_BAD_PARAM"
    assert error_code(await recommend(assembled.deps, video_id="nosuchvideo", reason="x")) == "E_UNKNOWN_VIDEO"
    assert await stored(assembled.deps) == []


# ------------------------------------------------------------------ the read


async def test_the_bare_read_lists_new_verdicts_and_what_became_of_earlier_picks(
    assembled: Assembled,
) -> None:
    fresh = await judge_recently(assembled.deps, "zduSFxRajkE", score=3)
    # A verdict written now for a talk published years ago (a rescore) stays out.
    await assembled.deps.db.write(
        lambda c: c.execute(
            "INSERT INTO verdicts (video_id, score, reason, summary, moments, profile_rev, model)"
            " SELECT id, 3, 'old', 'old', '[]', 0, 'm:x' FROM videos WHERE source_id = ?",
            (VIDEO,),
        )
    )
    structured(await recommend(assembled.deps, video_id="zduSFxRajkE", reason="Paged attention, finally clear."))
    await assembled.deps.db.write(
        lambda c: c.execute("INSERT INTO feedback (video_id, state) VALUES (?, 'up')", (fresh,))
    )

    body = structured(await recommend(assembled.deps))
    assert [v["video_id"] for v in body["verdicts"]] == ["zduSFxRajkE"]
    assert body["verdicts"][0]["summary"] == "Paged attention, in short."
    assert body["picked_today"] == 1
    [earlier] = body["earlier"]
    assert (earlier["public_id"], earlier["feedback"], earlier["kept"]) == ("zduSFxRajkE", "up", True)


# ------------------------------------------------------------------ the feed and the ledger


async def test_the_week_shows_the_picks_and_the_ledger_counts_them_apart(assembled: Assembled) -> None:
    await judge_recently(assembled.deps, "zduSFxRajkE", score=3)
    structured(
        await recommend(
            assembled.deps,
            video_id="zduSFxRajkE",
            reason="Paged attention, finally clear.",
            moments=[PickMoment(start_s=11.0, end_s=14.0, why="the block table")],
        )
    )
    now = time.time()

    def read(c):
        from vidtheque_mcp.verdicts import week as verdicts_week

        return picks.of_week(c, verdicts_week.week_of(now)), ledger.weeks(c, datetime.now())["weeks"][0]

    shown, current = await assembled.deps.db.read(read)
    [pick] = shown
    assert (pick["video_id"], pick["reason"], pick["score"]) == ("zduSFxRajkE", "Paged attention, finally clear.", 3)
    assert pick["moments"] == [{"offset_s": 11.0, "end_s": 16.0, "why": "the block table"}]
    assert current["picks"] == {"source": "claude", "picked": 1, "kept": 0, "rate": 0.0}
    # The week's lone 3 is the pipeline's top tier.
    assert current["top"] == {"kept": 0, "offered": 1, "rate": 0.0}
