"""Trial follows (following.md §10.7): the verb, and how a trial ends or stays."""

from __future__ import annotations

import time

from mcp_types import TextContent
from vidtheque_mcp.app import Assembled
from vidtheque_mcp.follows import store as follows_store
from vidtheque_mcp.follows import trials
from vidtheque_mcp.tools import follows as follows_tool

CHANNEL = "https://www.youtube.com/@karpathy"


def body(result) -> str:
    return "\n".join(b.text for b in result.content if isinstance(b, TextContent))


async def trial_until(deps) -> int | None:
    row = await deps.db.read(lambda c: c.execute("SELECT trial_until FROM follows").fetchone())
    return row["trial_until"]


async def test_trial_then_follow_makes_it_lasting(assembled: Assembled) -> None:
    deps = assembled.deps
    started = await follows_tool.follow_channel(deps, url=CHANNEL, action="trial")
    assert "On trial: @karpathy (channel)" in body(started)
    assert "Trial: ends" in body(started)
    assert started.structured_content["action"] == "trial"
    assert started.structured_content["follow"]["trial_until"] is not None
    until = await trial_until(deps)
    assert until is not None and abs(until - (time.time() + trials.TRIAL_S)) < 60

    # A second trial changes nothing; `follow` keeps it for good.
    again = await follows_tool.follow_channel(deps, url=CHANNEL, action="trial")
    assert "Already following" in body(again)
    assert await trial_until(deps) == until
    kept = await follows_tool.follow_channel(deps, url=CHANNEL)
    assert "now a lasting follow" in body(kept)
    assert await trial_until(deps) is None


async def test_trial_on_a_lasting_follow_leaves_it_lasting(assembled: Assembled) -> None:
    deps = assembled.deps
    await follows_tool.follow_channel(deps, url=CHANNEL)
    again = await follows_tool.follow_channel(deps, url=CHANNEL, action="trial")
    assert "Already following" in body(again)
    assert await trial_until(deps) is None


async def _expired_trial_with(deps, liked: str | None) -> int:
    """A trial that ended a minute ago, with one video it brought in, liked or not."""
    await follows_tool.follow_channel(deps, url=CHANNEL, action="trial")
    now = int(time.time())
    began = now - trials.TRIAL_S - 60

    def write(c) -> int:
        collection_id = int(c.execute("SELECT collection_id FROM follows").fetchone()[0])
        c.execute("UPDATE follows SET trial_until = ?", (began + trials.TRIAL_S,))
        video = c.execute(
            "SELECT id, duration_s FROM videos WHERE duration_s > 0 LIMIT 1"
        ).fetchone()
        c.execute(
            "INSERT INTO collection_videos (collection_id, video_id) VALUES (?, ?)",
            (collection_id, video["id"]),
        )
        duration = float(video["duration_s"])
        if liked == "up":
            c.execute(
                "INSERT INTO feedback (owner_id, video_id, state, at) VALUES (1, ?, 'up', ?)",
                (video["id"], began + 3600),
            )
        elif liked in ("full", "half"):
            share = 0.9 if liked == "full" else 0.5
            c.execute(
                "INSERT INTO signals (owner_id, at, kind, video_id, offset_s, watched_s, client)"
                " VALUES (1, ?, 'watch', ?, 0, ?, 'app')",
                (began + 3600, video["id"], duration * share),
            )
        elif liked == "before":
            # A thumb from before the trial began is not this trial's evidence.
            c.execute(
                "INSERT INTO feedback (owner_id, video_id, state, at) VALUES (1, ?, 'up', ?)",
                (video["id"], began - 3600),
            )
        return collection_id

    return await deps.db.write(write)


async def _settle(deps) -> list[trials.Settled]:
    return await deps.db.write(lambda c: trials.settle(c))


async def test_an_unliked_trial_ends_and_keeps_its_videos(assembled: Assembled) -> None:
    deps = assembled.deps
    n_videos = await deps.db.read(lambda c: c.execute("SELECT COUNT(*) FROM videos").fetchone()[0])
    collection_id = await _expired_trial_with(deps, None)
    settled = await _settle(deps)
    assert [(s.collection_id, s.kept) for s in settled] == [(collection_id, False)]
    assert await deps.db.read(lambda c: follows_store.get(c, collection_id)) is None
    assert (
        await deps.db.read(lambda c: c.execute("SELECT COUNT(*) FROM videos").fetchone()[0])
        == n_videos
    )


async def test_a_thumb_or_a_full_watch_keeps_a_trial(assembled: Assembled) -> None:
    for evidence, kept in (("up", True), ("full", True), ("half", False), ("before", False)):
        deps = assembled.deps

        def clear(c) -> None:
            for table in ("collections WHERE kind = 'channel'", "feedback", "signals"):
                c.execute(f"DELETE FROM {table}")

        await deps.db.write(clear)
        collection_id = await _expired_trial_with(deps, evidence)
        settled = await _settle(deps)
        assert [s.kept for s in settled] == [kept], evidence
        row = await deps.db.read(lambda c: follows_store.get(c, collection_id))
        assert (row is not None and row["trial_until"] is None) is kept, evidence


async def test_a_trial_not_yet_due_is_left_alone(assembled: Assembled) -> None:
    deps = assembled.deps
    await follows_tool.follow_channel(deps, url=CHANNEL, action="trial")
    assert await _settle(deps) == []
    assert await trial_until(deps) is not None
