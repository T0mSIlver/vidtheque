"""Crash recovery, in-flight collisions and honest aggregation.

Every test here comes from one live incident (2026-08-08): an mcp process was
killed mid-`keyframe`, its job stayed `running` forever, and the `force_reindex`
that followed produced `job_48ac80da64c3` — `done` in seconds, `n_items: 1,
n_done: 0`, no download, no log line. Three bugs in a row:

1. crash recovery ran **at boot only**, and the restart happened inside the
   staleness window, so nothing ever looked at the claim again;
2. the new item deduplicated against the zombie's claim on the video and was
   *silently skipped*;
3. a job whose only item was skipped aggregated to plain `done`, and
   `job-status` printed "Queryable now: everything from this job."

Nothing here reaches the network or a GPU: the pipeline is the same fake-driven
one `test_pipeline_e2e` uses.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from vidtheque_mcp.app import Assembled
from vidtheque_mcp.config import Settings
from vidtheque_mcp.jobs import store as jobs_store
from vidtheque_mcp.jobs.runner import ItemSkipped
from vidtheque_mcp.tools import indexing

from .pipeline_fakes import VIDEO_URL, FakeWorker
from .test_pipeline_e2e import Harness, body, harness, structured


class Killed(BaseException):
    """SIGKILL, as close as a test can get.

    Deliberately **not** an `Exception`: `_drive` catches those and fails the
    item cleanly, which is the one thing a killed process does not do.
    """


def kill_at_keyframe(parts: Harness) -> None:
    """Leave the item exactly where the live incident left it: mid-`keyframe`."""
    pipeline = parts.parts.runner.pipeline

    async def die(run) -> None:  # type: ignore[no-untyped-def]
        await pipeline._stage_running(run, "keyframe")
        await run.ctx.record("keyframe", 0.3)
        raise Killed("the process was killed mid-stage")

    pipeline._stage_keyframes = die  # type: ignore[method-assign]


async def backdate(parts: Harness, seconds: int = 9_999) -> None:
    await parts.db.write(
        lambda c: c.execute(
            "UPDATE jobs SET heartbeat_at = unixepoch() - ? WHERE state = 'running'",
            (seconds,),
        )
    )


async def close(parts: Harness) -> None:
    await parts.db.close()
    parts.parts.auth.close()


# --------------------------------------------------------------- the zombie


@pytest.fixture
async def zombie(settings: Settings, clip: Path):
    """A job left `running` by a process that died mid-`keyframe`."""
    worker = FakeWorker()
    parts = await harness(settings, clip, worker=worker)
    kill_at_keyframe(parts)
    job_id = await parts.index(url=VIDEO_URL)
    with pytest.raises(Killed):
        await parts.run()
    yield parts, worker, job_id
    await close(parts)


async def test_a_killed_process_leaves_a_claim_nobody_is_holding(zombie) -> None:
    parts, _worker, job_id = zombie
    job = await parts.one("SELECT state FROM jobs WHERE public_id = ?", (job_id,))
    assert job["state"] == "running"
    item = await parts.one("SELECT state, stage FROM job_items")
    assert (item["state"], item["stage"]) == ("running", "keyframe")
    stages = await parts.stages()
    assert stages["keyframe"]["state"] == "running"
    assert (await parts.one("SELECT index_state FROM videos"))["index_state"] == "indexing"


async def test_a_restart_inside_the_window_still_recovers(
    zombie, settings: Settings, clip: Path
) -> None:
    """The incident's exact shape: boot was too early to see the stale claim.

    Recovery used to run at boot and never again, so a restart faster than the
    staleness window left the claim standing for good. The runner sweeps before
    every claim now, so the next poll after the window closes picks it up.
    """
    parts, worker, job_id = zombie
    await close(parts)

    restarted = await harness(settings, clip, worker=worker)
    try:
        # Boot recovery sees a heartbeat from seconds ago and leaves it alone.
        job = await restarted.one("SELECT state FROM jobs WHERE public_id = ?", (job_id,))
        assert job["state"] == "running"
        assert await restarted.parts.runner.reclaim_stale() == []

        await backdate(restarted)
        assert await restarted.parts.runner.reclaim_stale() == [job_id]

        job = await restarted.one("SELECT state FROM jobs WHERE public_id = ?", (job_id,))
        assert job["state"] == "queued"
        item = await restarted.one("SELECT state, stage, attempts FROM job_items")
        assert (item["state"], item["stage"]) == ("queued", None)
        # `claim_item` already counted the attempt that died; counting it twice
        # would retire the item a whole attempt early.
        assert item["attempts"] == 1
        stages = await restarted.stages()
        assert stages["keyframe"]["state"] == "pending"
        assert "killed mid-stage" in str(stages["keyframe"]["error"])
        assert (await restarted.one("SELECT index_state FROM videos"))["index_state"] == "pending"
    finally:
        await close(restarted)


async def test_the_job_completes_after_the_restart(
    zombie, settings: Settings, clip: Path
) -> None:
    """The headline: a job killed mid-stage finishes correctly on a restart."""
    parts, worker, job_id = zombie
    await close(parts)
    first_pass = len(worker.calls)

    restarted = await harness(settings, clip, worker=worker)
    try:
        await backdate(restarted)
        assert await restarted.run() is True  # sweeps, then claims and resumes

        job = await restarted.one("SELECT * FROM jobs WHERE public_id = ?", (job_id,))
        assert job["state"] == "done"
        assert (job["n_done"], job["n_failed"]) == (1, 0)
        item = await restarted.one("SELECT state FROM job_items")
        assert item["state"] == "done"

        stages = await restarted.stages()
        assert all(row["state"] == "done" for row in stages.values()), {
            k: (v["state"], v["error"]) for k, v in stages.items()
        }
        assert (await restarted.one("SELECT index_state FROM videos"))["index_state"] == "ready"
        assert await restarted.rows("SELECT id FROM keyframes")
        # Per-stage resume, not a restart: the transcript was already done.
        assert "transcribe" not in worker.calls[first_pass:]
    finally:
        await close(restarted)


async def test_force_reindex_over_a_zombie_actually_indexes(
    zombie, settings: Settings, clip: Path
) -> None:
    """`job_48ac80da64c3`, replayed: it must not be `done` with nothing done."""
    parts, worker, _job_id = zombie
    await close(parts)

    restarted = await harness(settings, clip, worker=worker)
    try:
        await backdate(restarted)
        result = await indexing.index_video(
            restarted.deps, url=VIDEO_URL, force_reindex=True
        )
        assert not result.is_error, body(result)
        new_job = structured(result)["job_id"]

        # The reclaimed job runs first and honestly reports that it was
        # superseded; the new one does the work.
        while await restarted.run():
            pass

        job = await restarted.one("SELECT * FROM jobs WHERE public_id = ?", (new_job,))
        assert job["state"] == "done"
        assert (job["n_items"], job["n_done"], job["n_failed"]) == (1, 1, 0)
        assert (await restarted.one("SELECT index_state FROM videos"))["index_state"] == "ready"
        assert "transcribe" in worker.calls  # the pipeline actually ran

        superseded = await restarted.one(
            "SELECT state, error_code FROM job_items WHERE state = 'cancelled'"
        )
        assert superseded["error_code"] == "E_SUPERSEDED"
    finally:
        await close(restarted)


async def test_an_item_that_keeps_killing_the_process_retires(
    zombie, settings: Settings, clip: Path
) -> None:
    parts, worker, job_id = zombie
    await parts.db.write(
        lambda c: c.execute("UPDATE job_items SET attempts = max_attempts")
    )
    await backdate(parts)
    assert await parts.parts.runner.reclaim_stale() == [job_id]

    item = await parts.one("SELECT state, error_code FROM job_items")
    assert (item["state"], item["error_code"]) == ("failed", "E_CRASHED")
    # Requeued, but with nothing left to claim: the next pass settles it as the
    # failure it is instead of handing the item out a fourth time.
    assert await parts.run() is True
    job = await parts.one("SELECT state, error_code FROM jobs WHERE public_id = ?", (job_id,))
    assert (job["state"], job["error_code"]) == ("failed", "E_CRASHED")


# ------------------------------------------------------- the sweep's limits


async def test_the_sweep_never_reclaims_what_this_process_is_driving(
    assembled: Assembled,
) -> None:
    """A blocked event loop is alive. A 30-minute transcription is not a crash."""
    db, runner = assembled.db, assembled.runner
    job_id = await db.write(
        lambda c: jobs_store.create_job(c, "index", {}, [("https://youtu.be/Qk7mF2xLp0A", None)])
    )
    claimed = await db.write(jobs_store.claim_next)
    await db.write(
        lambda c: c.execute("UPDATE jobs SET heartbeat_at = unixepoch() - 9999")
    )

    runner._active.add(int(claimed["id"]))
    assert await runner.reclaim_stale() == []
    runner._active.discard(int(claimed["id"]))
    assert await runner.reclaim_stale() == [job_id]


async def test_a_fresh_claim_is_left_alone(assembled: Assembled) -> None:
    db, runner = assembled.db, assembled.runner
    await db.write(
        lambda c: jobs_store.create_job(c, "index", {}, [("https://youtu.be/Qk7mF2xLp0A", None)])
    )
    await db.write(jobs_store.claim_next)
    assert await runner.reclaim_stale() == []
    row = await db.read(lambda c: c.execute("SELECT state FROM jobs").fetchone())
    assert row["state"] == "running"


# -------------------------------------------------- force_reindex semantics


async def _live_claim(assembled: Assembled, source_id: str = "kCc8FmEb1nY") -> str:
    """A job holding `source_id`, heartbeating right now."""
    db = assembled.db
    video = await db.read(
        lambda c: c.execute("SELECT id FROM videos WHERE source_id = ?", (source_id,)).fetchone()
    )
    job_id = await db.write(
        lambda c: jobs_store.create_job(
            c, "index", {}, [(f"https://youtu.be/{source_id}", int(video["id"]))]
        )
    )
    await db.write(jobs_store.claim_next)
    await db.write(lambda c: jobs_store.claim_item(c, _job_row_id(c, job_id)))
    return job_id


def _job_row_id(conn: sqlite3.Connection, public_id: str) -> int:
    row = conn.execute("SELECT id FROM jobs WHERE public_id = ?", (public_id,)).fetchone()
    return int(row["id"])


async def test_force_reindex_of_a_live_job_is_a_typed_refusal(
    assembled: Assembled,
) -> None:
    """Never a silent skip, and never a job id that will do no work."""
    job_id = await _live_claim(assembled)
    result = await indexing.index_video(
        assembled.deps, url="https://youtu.be/kCc8FmEb1nY", force_reindex=True
    )
    assert result.is_error
    payload = structured(result)
    assert payload["code"] == "E_INDEXING"
    assert payload["job_id"] == job_id
    assert "being indexed right now" in body(result)
    jobs = await assembled.db.read(lambda c: c.execute("SELECT COUNT(*) AS n FROM jobs").fetchone())
    assert jobs["n"] == 1  # nothing was queued


async def test_a_job_this_process_is_driving_is_never_superseded(
    assembled: Assembled,
) -> None:
    """The sweep skips this process's own jobs, so force_reindex must too.

    A quiet heartbeat on a job the local runner is driving means a slow stage,
    not a dead process — cancelling its item underneath it would be the same
    silent no-op bug wearing a different hat.
    """
    job_id = await _live_claim(assembled)
    row = await assembled.db.read(lambda c: jobs_store.get_job(c, job_id))
    assembled.runner._active.add(int(row["id"]))
    await assembled.db.write(
        lambda c: c.execute("UPDATE jobs SET heartbeat_at = unixepoch() - 9999")
    )
    try:
        result = await indexing.index_video(
            assembled.deps, url="https://youtu.be/kCc8FmEb1nY", force_reindex=True
        )
    finally:
        assembled.runner._active.discard(int(row["id"]))
    assert result.is_error
    assert structured(result)["code"] == "E_INDEXING"
    item = await assembled.db.read(
        lambda c: c.execute("SELECT state FROM job_items").fetchone()
    )
    assert item["state"] == "running"  # untouched


async def test_force_reindex_supersedes_a_claim_nobody_holds(
    assembled: Assembled,
) -> None:
    db = assembled.db
    video = await db.read(
        lambda c: c.execute("SELECT id FROM videos WHERE source_id = 'kCc8FmEb1nY'").fetchone()
    )
    old = await db.write(
        lambda c: jobs_store.create_job(
            c, "index", {}, [("https://youtu.be/kCc8FmEb1nY", int(video["id"]))]
        )
    )
    result = await indexing.index_video(
        assembled.deps, url="https://youtu.be/kCc8FmEb1nY", force_reindex=True
    )
    assert not result.is_error, body(result)
    assert structured(result)["job_id"] != old

    item = await db.read(
        lambda c: c.execute(
            "SELECT i.state, i.error_code FROM job_items i JOIN jobs j ON j.id = i.job_id "
            "WHERE j.public_id = ?",
            (old,),
        ).fetchone()
    )
    assert (item["state"], item["error_code"]) == ("cancelled", "E_SUPERSEDED")


async def test_a_queued_claim_refuses_without_force_and_says_how(
    assembled: Assembled,
) -> None:
    db = assembled.db
    video = await db.read(
        lambda c: c.execute("SELECT id FROM videos WHERE source_id = 'kCc8FmEb1nY'").fetchone()
    )
    # A video mid-index is not `ready`, so the "already indexed" short-circuit
    # does not fire and the guard has to.
    await db.write(
        lambda c: c.execute(
            "UPDATE videos SET index_state = 'indexing' WHERE id = ?", (int(video["id"]),)
        )
    )
    old = await db.write(
        lambda c: jobs_store.create_job(
            c, "index", {}, [("https://youtu.be/kCc8FmEb1nY", int(video["id"]))]
        )
    )
    result = await indexing.index_video(assembled.deps, url="https://youtu.be/kCc8FmEb1nY")
    assert result.is_error
    assert structured(result)["code"] == "E_INDEXING"
    assert old in body(result)
    assert "force_reindex" in body(result)


# ------------------------------------------------ collisions inside the run


async def test_another_jobs_claim_fails_the_item_instead_of_skipping_it(
    settings: Settings, clip: Path
) -> None:
    """The runtime half of the same bug: a cross-job clash is not bookkeeping."""
    parts = await harness(settings, clip)
    try:
        await parts.index(url=VIDEO_URL, channels="transcript")
        assert await parts.run() is True
        video = await parts.one("SELECT id FROM videos")

        # A live sibling job holding the same video.
        holder = await parts.db.write(
            lambda c: jobs_store.create_job(c, "index", {}, [(VIDEO_URL, int(video["id"]))])
        )
        await parts.db.write(
            lambda c: c.execute(
                "UPDATE jobs SET state = 'running', started_at = unixepoch(), "
                "heartbeat_at = unixepoch() WHERE public_id = ?",
                (holder,),
            )
        )
        await parts.db.write(
            lambda c: c.execute(
                "UPDATE job_items SET state = 'running' WHERE job_id = "
                "(SELECT id FROM jobs WHERE public_id = ?)",
                (holder,),
            )
        )
        # A second job that only resolves the video mid-pipeline.
        blocked = await parts.db.write(
            lambda c: jobs_store.create_job(c, "index", {}, [(VIDEO_URL, None)])
        )
        assert await parts.run() is True

        job = await parts.one("SELECT * FROM jobs WHERE public_id = ?", (blocked,))
        assert job["state"] == "failed"
        assert job["error_code"] == "E_INDEXING"
        assert holder in str(job["error_message"])
    finally:
        await close(parts)


async def test_a_duplicate_inside_one_job_is_still_a_skip(
    settings: Settings, clip: Path
) -> None:
    parts = await harness(settings, clip)
    try:
        await parts.index(url=VIDEO_URL, channels="transcript")
        assert await parts.run() is True
        video = await parts.one("SELECT id FROM videos")

        # Two items, one job, same video: the first resolves it mid-pipeline
        # and finds the second already holding the claim.
        job_id = await parts.db.write(
            lambda c: jobs_store.create_job(
                c, "index", {}, [(VIDEO_URL, None), (VIDEO_URL, int(video["id"]))]
            )
        )
        assert await parts.run() is True

        job = await parts.one("SELECT * FROM jobs WHERE public_id = ?", (job_id,))
        assert job["state"] == "done"
        items = await parts.rows(
            "SELECT i.state, i.error_code FROM job_items i JOIN jobs j ON j.id = i.job_id "
            "WHERE j.public_id = ? ORDER BY i.seq",
            (job_id,),
        )
        assert [i["state"] for i in items] == ["skipped", "done"]
        assert items[0]["error_code"] == "E_DUPLICATE_ITEM"
    finally:
        await close(parts)


# ------------------------------------------------------------- aggregation


class SkippingPipeline:
    """Every item skips. The shape a job with no work has."""

    async def run_item(self, ctx) -> None:  # type: ignore[no-untyped-def]
        raise ItemSkipped("kCc8FmEb1nY is already claimed elsewhere", code="E_SKIPPED")


async def test_a_job_that_skipped_everything_does_not_read_as_success(
    assembled: Assembled,
) -> None:
    assembled.runner.pipeline = SkippingPipeline()
    result = await indexing.index_video(assembled.deps, url="https://youtu.be/Qk7mF2xLp0A")
    job_id = structured(result)["job_id"]
    assert await assembled.runner.run_once() is True

    row = await assembled.db.read(lambda c: jobs_store.get_job(c, job_id))
    assert row["state"] == "done"
    assert row["error_code"] == "E_NOTHING_INDEXED"
    assert "already claimed elsewhere" in str(row["error_message"])

    status = await indexing.job_status(assembled.deps, job_id=job_id)
    text = body(status)
    assert "Queryable now: nothing — this job indexed no video." in text
    assert "already claimed elsewhere" in text
    assert "everything from this job" not in text

    payload = structured(status)
    assert (payload["n_done"], payload["n_failed"], payload["n_skipped"]) == (0, 0, 1)
    assert payload["n_items"] == 1
    assert (
        payload["n_done"] + payload["n_failed"] + payload["n_skipped"] + payload["n_cancelled"]
        == payload["n_items"]
    )
    assert payload["progress"] == 1.0


async def test_a_job_that_indexed_something_says_how_much(
    settings: Settings, clip: Path
) -> None:
    parts = await harness(settings, clip)
    try:
        job_id = await parts.index(url=VIDEO_URL, channels="transcript")
        assert await parts.run() is True
        status = await indexing.job_status(parts.deps, job_id=job_id)
        assert "Queryable now: the 1 video(s) this job indexed." in body(status)
        payload = structured(status)
        assert (payload["n_done"], payload["n_skipped"]) == (1, 0)
    finally:
        await close(parts)


# ---------------------------------------------------------------- progress


async def test_progress_never_goes_backwards_as_stages_advance(
    assembled: Assembled,
) -> None:
    """The live symptom: 0.5 → 0.05 → 0.75 → 0.197, because the overall figure
    was the *current stage's* percentage. Stage order now carries it."""
    db = assembled.db
    job_id = await db.write(
        lambda c: jobs_store.create_job(c, "index", {}, [("https://youtu.be/Qk7mF2xLp0A", None)])
    )
    job = await db.write(jobs_store.claim_next)
    item = await db.write(lambda c: jobs_store.claim_item(c, int(job["id"])))
    item_id = int(item["id"])

    seen: list[float] = []
    for stage, pct in (
        ("fetch", 0.0),
        ("fetch", 0.5),
        ("fetch", 1.0),
        ("stt", 0.05),
        ("stt", 1.0),
        ("chunk", 1.0),
        ("text_embed", 0.75),
        ("keyframe", 0.197),
        ("ocr", 0.4),
        ("frame_embed", 1.0),
    ):
        await db.write(lambda c: jobs_store.record_stage(c, item_id, stage, pct))
        row = await db.read(lambda c: jobs_store.get_job(c, job_id))
        seen.append(float(row["progress"]))

    assert seen == sorted(seen), seen
    assert seen[1] == pytest.approx(0.5 / 7, abs=0.001)  # not 0.5
    assert seen[3] > seen[2] - 1e-9  # `stt` at 5% is still past `fetch` at 100%
    assert 0.0 <= seen[-1] <= 1.0

    await db.write(lambda c: jobs_store.finish_item(c, item_id, "done"))
    row = await db.read(lambda c: jobs_store.get_job(c, job_id))
    assert float(row["progress"]) == 1.0


async def test_a_terminal_item_counts_whatever_its_state(assembled: Assembled) -> None:
    """A failed or skipped item is finished work: progress must not drop."""
    db = assembled.db
    job_id = await db.write(
        lambda c: jobs_store.create_job(
            c,
            "index",
            {},
            [("https://youtu.be/Qk7mF2xLp0A", None), ("https://youtu.be/aBcDeFgHiJk", None)],
        )
    )
    job = await db.write(jobs_store.claim_next)
    item = await db.write(lambda c: jobs_store.claim_item(c, int(job["id"])))
    item_id = int(item["id"])
    await db.write(lambda c: jobs_store.record_stage(c, item_id, "ocr", 0.5))
    before = float((await db.read(lambda c: jobs_store.get_job(c, job_id)))["progress"])

    await db.write(lambda c: jobs_store.finish_item(c, item_id, "failed", "E_X", "boom"))
    after = float((await db.read(lambda c: jobs_store.get_job(c, job_id)))["progress"])
    assert after >= before
    assert after == 0.5  # one of two items is over
