"""The 116-video overnight run, and the six ways it could have lied about itself.

A correctness review of the job runner (2026-08-09, ahead of the first
unattended batch) found that a run could finish *looking* healthy while videos
or whole search channels were missing. Every test here is one of those
sequences, replayed:

1. a rate limit answered in the same millisecond, three times, then again
   against every URL behind it — and erased from the payload if any sibling
   succeeded (`E_RATE_LIMIT` §backoff);
2. a wave of ten URLs, nine of them already indexed, all ten queued and
   redownloaded (§mixed waves);
3. an OCR or embedding stage that failed while `job-status` printed every wire
   stage `done` (§degraded);
4. a `force_reindex` that crashed and redownloaded and retranscribed the whole
   video on every retry (§force);
5. an orderly `stop()` that left a fresh claim wedged for the full staleness
   window (§stop);
6. progress that walked backwards on a retry (§progress).

Nothing here reaches the network or a GPU: same fakes as `test_pipeline_e2e`.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path

import pytest

from vidtheque_mcp.app import Assembled
from vidtheque_mcp.config import Settings
from vidtheque_mcp.jobs import store as jobs_store
from vidtheque_mcp.jobs.runner import ItemFailed
from vidtheque_mcp.pipeline.sources import RateLimited
from vidtheque_mcp.tools import indexing

from .pipeline_fakes import SECOND_URL, VIDEO_URL, FakeWorker, canned_source
from .test_job_recovery import Killed, backdate, close, kill_at_keyframe
from .test_pipeline_e2e import Harness, body, harness, structured

URL_A = "https://youtu.be/Qk7mF2xLp0A"
URL_B = "https://youtu.be/aBcDeFgHiJk"


async def queue(parts, *urls: str) -> str:
    """A job straight through the store, so the pipeline is the only variable."""
    return await parts.db.write(
        lambda c: jobs_store.create_job(c, "index", {}, [(u, None) for u in urls])
    )


async def job_row(parts, public_id: str) -> sqlite3.Row:
    return await parts.db.read(
        lambda c: c.execute(
            "SELECT *, unixepoch() AS now FROM jobs WHERE public_id = ?", (public_id,)
        ).fetchone()
    )


async def items_of(parts, public_id: str) -> list[sqlite3.Row]:
    return await parts.db.read(
        lambda c: c.execute(
            "SELECT i.* FROM job_items i JOIN jobs j ON j.id = i.job_id "
            "WHERE j.public_id = ? ORDER BY i.seq",
            (public_id,),
        ).fetchall()
    )


async def open_the_gate(parts) -> None:
    """Fast-forward past the backoff without sleeping through it."""
    await parts.db.write(lambda c: c.execute("UPDATE jobs SET not_before = 0"))


# ======================================================================= backoff


class Throttled:
    """429s the first N attempts on the URLs named, lets everything else through.

    `attempts` is the whole point: it counts how many times the runner *asked*,
    and the bug was that it asked three times inside one millisecond.
    """

    def __init__(self, throttle: dict[str, int] | None = None) -> None:
        self.throttle = throttle or {}
        self.attempts: list[str] = []

    async def run_item(self, ctx) -> None:  # type: ignore[no-untyped-def]
        self.attempts.append(ctx.source_url)
        if self.attempts.count(ctx.source_url) <= self.throttle.get(ctx.source_url, 0):
            raise ItemFailed("E_RATE_LIMIT", "429 from YouTube", retryable=True)


async def test_a_rate_limited_item_is_not_handed_straight_back(
    assembled: Assembled,
) -> None:
    """The sequence: one 429 used to burn all three attempts instantly.

    `_fail_item` requeued the item while the job stayed `running`, so the very
    next line of `_drive` claimed the same lowest-seq item again.
    """
    runner = assembled.runner
    runner.pipeline = pipeline = Throttled({URL_A: 1})
    runner.rate_limit_backoff_s = 300
    job_id = await queue(assembled, URL_A, URL_B)

    assert await runner.run_once() is True
    assert pipeline.attempts == [URL_A]  # once. Not three times, not ten.

    job = await job_row(assembled, job_id)
    assert job["state"] == "queued"
    assert job["not_before"] - job["now"] == pytest.approx(300, abs=2)
    assert job["error_code"] == "E_RATE_LIMIT"
    items = await items_of(assembled, job_id)
    assert (items[0]["state"], items[0]["attempts"]) == ("queued", 1)
    assert items[1]["state"] == "queued"  # the siblings were not burned either

    # And the queue honours it: nothing is claimable until the window closes.
    assert await runner.run_once() is False
    assert pipeline.attempts == [URL_A]

    await open_the_gate(assembled)
    assert await runner.run_once() is True
    assert pipeline.attempts == [URL_A, URL_A, URL_B]
    assert (await job_row(assembled, job_id))["state"] == "done"


async def test_the_source_own_window_wins_over_the_default(
    assembled: Assembled,
) -> None:
    class SaysWhen:
        async def run_item(self, ctx) -> None:  # type: ignore[no-untyped-def]
            raise ItemFailed("E_RATE_LIMIT", "429", retryable=True, retry_after_s=42)

    assembled.runner.pipeline = SaysWhen()
    assembled.runner.rate_limit_backoff_s = 300
    job_id = await queue(assembled, URL_A)
    assert await assembled.runner.run_once() is True

    job = await job_row(assembled, job_id)
    assert job["not_before"] - job["now"] == pytest.approx(42, abs=2)


async def test_rate_limiting_survives_a_partially_successful_job(
    assembled: Assembled,
) -> None:
    """The erasure: nine successes and one 429 aggregated to `done`, code null.

    An unattended driver reads `error_code` to decide whether to cool off. It
    got the same answer as a clean run and started the next wave immediately.
    """
    runner = assembled.runner
    runner.pipeline = Throttled({URL_A: 99})
    runner.rate_limit_backoff_s = 60
    job_id = await queue(assembled, URL_A, URL_B)

    for _ in range(3):  # three attempts, each behind its own backoff
        await open_the_gate(assembled)
        assert await runner.run_once() is True

    job = await job_row(assembled, job_id)
    assert job["state"] == "done"  # a sibling did get indexed
    assert job["error_code"] == "E_RATE_LIMIT"  # and the job still says so

    status = await indexing.job_status(assembled.deps, job_id=job_id)
    payload = structured(status)
    assert (payload["n_done"], payload["n_failed"]) == (1, 1)
    assert payload["error_code"] == "E_RATE_LIMIT"
    assert payload["item_errors"] == {"E_RATE_LIMIT": 1}


async def test_a_recovered_rate_limit_is_still_reported(assembled: Assembled) -> None:
    """No item failed in the end. The box was still throttled, and says so."""
    runner = assembled.runner
    runner.pipeline = Throttled({URL_A: 1})
    runner.rate_limit_backoff_s = 30
    job_id = await queue(assembled, URL_A)

    assert await runner.run_once() is True
    await open_the_gate(assembled)
    assert await runner.run_once() is True

    status = await indexing.job_status(assembled.deps, job_id=job_id)
    payload = structured(status)
    assert (payload["state"], payload["n_done"], payload["n_failed"]) == ("done", 1, 0)
    assert payload["error_code"] == "E_RATE_LIMIT"
    assert payload["item_errors"] == {}  # nothing failed; it was still throttled
    assert "rate-limited:" in body(status)


async def test_a_local_hiccup_does_not_stick_to_the_finished_job(
    assembled: Assembled,
) -> None:
    """Only rate limiting outlives the retry that fixed it."""

    class Flaky:
        def __init__(self) -> None:
            self.seen = 0

        async def run_item(self, ctx) -> None:  # type: ignore[no-untyped-def]
            self.seen += 1
            if self.seen == 1:
                raise ItemFailed("E_INTERNAL", "the worker restarted", retryable=True)

    assembled.runner.pipeline = Flaky()
    job_id = await queue(assembled, URL_A)
    assert await assembled.runner.run_once() is True
    await open_the_gate(assembled)
    assert await assembled.runner.run_once() is True

    job = await job_row(assembled, job_id)
    assert (job["state"], job["error_code"]) == ("done", None)


# ------------------------------------------------------- 429 on the caption leg


class ThrottledCaptions:
    """`fetch_subtitle` is the third request of a video and where the 429 lands."""

    def __init__(self, inner) -> None:  # type: ignore[no-untyped-def]
        self._inner = inner

    def __getattr__(self, name: str):  # type: ignore[no-untyped-def]
        return getattr(self._inner, name)

    def fetch_subtitle(self, track):  # type: ignore[no-untyped-def]
        raise RateLimited(f"429 fetching the {track.lang} caption track")


async def test_a_caption_429_keeps_its_type_instead_of_becoming_unsupported(
    settings: Settings, clip: Path
) -> None:
    """It used to end the item as `E_UNSUPPORTED_SOURCE`, final.

    The caller was told the video had no transcript. The video had a transcript;
    the box had been asking too fast.
    """
    parts = await harness(settings, clip, worker=FakeWorker(healthy=False))
    parts.parts.runner.pipeline.source = ThrottledCaptions(canned_source(clip))
    parts.parts.runner.rate_limit_backoff_s = 120
    try:
        job_id = await parts.index(url=VIDEO_URL, channels="transcript")
        assert await parts.run() is True

        job = await job_row(parts, job_id)
        assert job["error_code"] == "E_RATE_LIMIT"
        assert job["state"] == "queued"  # deferred, not retired
        assert job["not_before"] - job["now"] == pytest.approx(120, abs=2)

        item = (await items_of(parts, job_id))[0]
        assert (item["state"], item["attempts"]) == ("queued", 1)
        assert item["error_code"] is None
        stages = await parts.stages()
        assert stages["stt"]["state"] == "failed"
        assert "429" in str(stages["stt"]["error"])
    finally:
        await close(parts)


# ===================================================================== degraded


@pytest.fixture
async def degraded(settings: Settings, clip: Path):
    """One video indexed with its OCR leg down. `ready`, and missing a channel.

    `_soft_fail` is deliberate — a video with no OCR is still a video you can
    find by what was said in it — but the job reported `n_done: 1, n_failed: 0`,
    no error code, and `job-status` printed every wire stage `done`.
    """
    parts = await harness(settings, clip, worker=FakeWorker(fail={"ocr"}))
    try:
        job_id = await parts.index(url=VIDEO_URL)
        assert await parts.run() is True
        yield parts, job_id
    finally:
        await close(parts)


async def test_a_soft_failed_stage_is_visible_in_the_payload(degraded) -> None:
    parts, job_id = degraded
    stages = await parts.stages()
    assert stages["ocr"]["state"] == "failed"  # the premise
    assert (await parts.one("SELECT index_state FROM videos"))["index_state"] == "ready"

    status = await indexing.job_status(parts.deps, job_id=job_id)
    payload = structured(status)
    assert (payload["state"], payload["n_done"], payload["n_failed"]) == ("done", 1, 0)
    assert payload["n_degraded"] == 1
    assert payload["degraded_stages"] == ["ocr"]


async def test_the_stage_table_reads_the_stage_rows_not_the_item(degraded) -> None:
    """It printed `ocr done` because the *item* was done. The rows said failed."""
    parts, job_id = degraded
    text = body(await indexing.job_status(parts.deps, job_id=job_id))
    rows = {
        line.split()[0]: line.split()[1]
        for line in text.splitlines()
        if line.startswith("  ") and len(line.split()) >= 2
    }
    assert rows["ocr"] == "failed"
    assert rows["download"] == "done"
    assert rows["transcribe"] == "done"
    assert rows["keyframes"] == "done"
    assert "degraded: 1 video(s) finished with a failed stage (ocr)" in text
    assert "(1 degraded)" in text


async def test_a_degraded_video_can_be_resubmitted_without_force(degraded) -> None:
    """It short-circuited as "already indexed", so the failed stage was
    unreachable without `force_reindex` — which redoes the six that were fine."""
    parts, _job_id = degraded
    parts.worker.fail = set()  # the worker is back
    downloads = len(parts.source.downloads)

    result = await indexing.index_video(parts.deps, url=VIDEO_URL)
    assert not result.is_error, body(result)
    payload = structured(result)
    assert payload["job_id"] is not None  # not a no-op any more
    assert payload["resuming"] == {VIDEO_URL: ["ocr"]}
    assert "resuming at the failed stage(s) only: ocr" in body(result)

    calls = len(parts.worker.calls)
    assert await parts.run() is True

    stages = await parts.stages()
    assert stages["ocr"]["state"] == "done"
    assert all(row["state"] == "done" for row in stages.values())
    # Only the failed stage ran: no transcription, no re-embedding of chunks.
    assert [c for c in parts.worker.calls[calls:] if c.startswith("ocr")]
    assert "transcribe" not in parts.worker.calls[calls:]
    assert not [c for c in parts.worker.calls[calls:] if c.startswith("embed:")]
    # And nothing was re-downloaded: `ocr` reads keyframes off disk.
    assert len(parts.source.downloads) == downloads

    payload = structured(await indexing.job_status(parts.deps, job_id=payload["job_id"]))
    assert payload["n_degraded"] == 0


async def test_a_clean_job_says_nothing_about_degradation(
    settings: Settings, clip: Path
) -> None:
    parts = await harness(settings, clip)
    try:
        job_id = await parts.index(url=VIDEO_URL)
        assert await parts.run() is True
        status = await indexing.job_status(parts.deps, job_id=job_id)
        assert structured(status)["n_degraded"] == 0
        assert structured(status)["degraded_stages"] == []
        assert "degraded" not in body(status)
    finally:
        await close(parts)


# =================================================================== mixed waves


async def test_a_mixed_wave_queues_only_the_new_video(
    settings: Settings, clip: Path
) -> None:
    """Nine ready and one new used to mean ten downloads.

    The no-op shortcut only fired when *every* URL was current, so a wave with
    one new entry in it queued the lot — and `fetch` probes and downloads before
    any later stage discovers the video is already indexed.
    """
    parts = await harness(settings, clip)
    try:
        await parts.index(url=VIDEO_URL)
        assert await parts.run() is True
        downloads = len(parts.source.downloads)

        result = await indexing.index_video(parts.deps, urls=[VIDEO_URL, SECOND_URL])
        assert not result.is_error, body(result)
        payload = structured(result)
        assert payload["items"] == 1  # one video of work
        assert payload["n_items"] == 2  # two rows, so the wave still adds up
        assert len(payload["already_indexed"]) == 1
        assert "already indexed and left alone" in body(result)

        items = await items_of(parts, payload["job_id"])
        assert [i["state"] for i in items] == ["skipped", "queued"]
        assert items[0]["error_code"] == "E_ALREADY_INDEXED"
        assert items[0]["started_at"] is None  # never claimed, never probed

        assert await parts.run() is True
        # Exactly one video's worth of media, not two.
        assert len(parts.source.downloads) - downloads == 2

        status = await indexing.job_status(parts.deps, job_id=payload["job_id"])
        counts = structured(status)
        assert (counts["n_done"], counts["n_skipped"], counts["n_items"]) == (1, 1, 2)
        assert counts["item_errors"] == {"E_ALREADY_INDEXED": 1}
    finally:
        await close(parts)


async def test_a_wave_of_only_ready_videos_still_creates_no_job(
    settings: Settings, clip: Path
) -> None:
    """The shortcut that was already right stays right."""
    parts = await harness(settings, clip)
    try:
        await parts.index(url=VIDEO_URL)
        assert await parts.run() is True
        result = await indexing.index_video(parts.deps, urls=[VIDEO_URL])
        assert structured(result)["job_id"] is None
        assert "Already indexed" in body(result)
    finally:
        await close(parts)


async def test_force_reindex_ignores_the_partition(settings: Settings, clip: Path) -> None:
    """`force_reindex` means "do it anyway", including for a current video."""
    parts = await harness(settings, clip)
    try:
        await parts.index(url=VIDEO_URL)
        assert await parts.run() is True

        result = await indexing.index_video(
            parts.deps, urls=[VIDEO_URL, SECOND_URL], force_reindex=True
        )
        payload = structured(result)
        assert payload["already_indexed"] == []
        items = await items_of(parts, payload["job_id"])
        assert [i["state"] for i in items] == ["queued", "queued"]
    finally:
        await close(parts)


# ====================================================================== progress


async def test_progress_does_not_fall_back_to_the_siblings_on_a_requeue(
    assembled: Assembled,
) -> None:
    """A retryable failure late in an item used to erase the item from the sum.

    `requeue_item` clears `stage`, and the fractional term only counted running
    items, so the job's percentage dropped to whatever the terminal siblings
    contributed and then climbed the same ground a second time. The floor is the
    stages this job actually finished for that video — durable work no retry
    undoes.
    """
    from vidtheque_mcp.pipeline import store as pipeline_store

    db = assembled.db
    video = await db.read(
        lambda c: c.execute("SELECT id FROM videos WHERE source_id = 'kCc8FmEb1nY'").fetchone()
    )
    video_id = int(video["id"])
    job_id = await db.write(
        lambda c: jobs_store.create_job(
            c, "index", {}, [(f"https://youtu.be/kCc8FmEb1nY", video_id), (URL_B, None)]
        )
    )
    job = await db.write(jobs_store.claim_next)
    item = await db.write(lambda c: jobs_store.claim_item(c, int(job["id"])))
    item_id = int(item["id"])

    for stage in ("fetch", "stt", "chunk"):
        await db.write(
            lambda c: pipeline_store.stage_finished(c, video_id, stage, "done", "m")
        )
    await db.write(lambda c: jobs_store.record_stage(c, item_id, "text_embed", 0.75))
    before = float((await db.read(lambda c: jobs_store.get_job(c, job_id)))["progress"])

    await db.write(lambda c: jobs_store.requeue_item(c, item_id))
    after = float((await db.read(lambda c: jobs_store.get_job(c, job_id)))["progress"])

    assert after > 0.0  # it used to be exactly 0.0: no terminal sibling, no credit
    assert after == pytest.approx(3 / 7 / 2, abs=0.001)  # three stages of two items
    assert after <= before

    # And starting the attempt over does not walk it back below that floor.
    await db.write(lambda c: jobs_store.claim_item(c, int(job["id"])))
    await db.write(lambda c: jobs_store.record_stage(c, item_id, "fetch", 0.0))
    retried = float((await db.read(lambda c: jobs_store.get_job(c, job_id)))["progress"])
    assert retried == pytest.approx(after, abs=0.001)


async def test_a_previous_jobs_stages_are_not_this_jobs_progress(
    assembled: Assembled,
) -> None:
    """The floor is keyed on the job, or a reindex would open at 100%."""
    from vidtheque_mcp.pipeline import store as pipeline_store

    db = assembled.db
    video = await db.read(
        lambda c: c.execute("SELECT id FROM videos WHERE source_id = 'kCc8FmEb1nY'").fetchone()
    )
    video_id = int(video["id"])
    for stage in ("fetch", "stt", "chunk", "text_embed", "keyframe", "ocr", "frame_embed"):
        await db.write(
            lambda c: pipeline_store.stage_finished(c, video_id, stage, "done", "m")
        )
    await db.write(
        lambda c: c.execute("UPDATE video_stages SET finished_at = unixepoch() - 3600")
    )

    job_id = await db.write(
        lambda c: jobs_store.create_job(
            c, "reindex", {}, [("https://youtu.be/kCc8FmEb1nY", video_id)]
        )
    )
    row = await db.read(lambda c: jobs_store.get_job(c, job_id))
    assert float(row["progress"]) == 0.0


# ========================================================================== stop


async def test_an_orderly_stop_gives_the_claim_back_immediately(
    settings: Settings, clip: Path
) -> None:
    """Shutdown mid-item used to wedge the job for the whole staleness window.

    `CancelledError` is not an `Exception`, so it bypassed the item handlers;
    `_settle` saw the item still `running` and returned. What was left was a
    `running` job with a heartbeat seconds old — exactly what a *live* job looks
    like, so the restart's sweep correctly refused to touch it for 300 s.
    """
    entered = asyncio.Event()

    async def block_forever() -> None:
        entered.set()
        await asyncio.Event().wait()

    worker = FakeWorker(on_transcribe=block_forever)
    parts = await harness(settings, clip, worker=worker)
    try:
        job_id = await parts.index(url=VIDEO_URL, channels="transcript")
        await parts.parts.runner.start()
        await asyncio.wait_for(entered.wait(), timeout=10)

        await parts.parts.runner.stop()

        job = await job_row(parts, job_id)
        assert job["state"] == "queued"
        assert job["heartbeat_at"] is None
        assert job["not_before"] <= job["now"]  # claimable now, not in 300 s
        item = (await items_of(parts, job_id))[0]
        assert item["state"] == "queued"
        # The video and its interrupted stage are back to a state a resume reads.
        assert (await parts.one("SELECT index_state FROM videos"))["index_state"] == "pending"
        stages = await parts.stages()
        assert stages["stt"]["state"] == "pending"
        assert "runner was stopped" in str(stages["stt"]["error"])

        # No sweep involved: nothing is stale, and the next claim still lands.
        assert await parts.parts.runner.reclaim_stale() == []
        worker.on_transcribe = None
        assert await parts.run() is True
        assert (await job_row(parts, job_id))["state"] == "done"
    finally:
        await close(parts)


async def test_a_stop_between_jobs_releases_nothing(
    settings: Settings, clip: Path
) -> None:
    """The guard: a job that finished before the stop landed stays finished."""
    parts = await harness(settings, clip)
    try:
        job_id = await parts.index(url=VIDEO_URL, channels="transcript")
        assert await parts.run() is True
        await parts.parts.runner.start()
        await parts.parts.runner.stop()
        assert (await job_row(parts, job_id))["state"] == "done"
    finally:
        await close(parts)


# ========================================================================= force


async def test_a_crashed_force_reindex_resumes_per_stage_like_any_other(
    settings: Settings, clip: Path
) -> None:
    """`force_reindex` used to be a mode, so every retry re-ran everything.

    A forced reindex that dies at keyframes came back with `force_reindex` still
    true in its args: `_should_run` returned true for fetch and stt however
    recently they had finished, and the media helpers ignored the files already
    on disk. On an hour-long video that is a redownload and a retranscription
    per crash — forever, if the crash is deterministic.
    """
    worker = FakeWorker()
    parts = await harness(settings, clip, worker=worker)
    try:
        await parts.index(url=VIDEO_URL)
        assert await parts.run() is True

        # The forced reindex, killed mid-keyframe.
        kill_at_keyframe(parts)
        job_id = await parts.index(url=VIDEO_URL, force_reindex=True)
        with pytest.raises(Killed):
            await parts.run()

        # It really did redo the work — that is what force means, once.
        stages = await parts.stages()
        assert stages["stt"]["state"] == "done"
        downloads = len(parts.source.downloads)
        calls = len(worker.calls)
        assert "transcribe" in worker.calls  # the force attempt transcribed

        # Now the crash recovery, with keyframes working again: `kill_at_keyframe`
        # shadowed the method on the instance, so deleting it uncovers the real one.
        del parts.parts.runner.pipeline._stage_keyframes
        await backdate(parts)
        assert await parts.parts.runner.reclaim_stale() == [job_id]
        assert await parts.run() is True

        job = await job_row(parts, job_id)
        assert (job["state"], job["n_done"]) == ("done", 1)
        stages = await parts.stages()
        assert all(row["state"] == "done" for row in stages.values()), {
            k: (v["state"], v["error"]) for k, v in stages.items()
        }
        # The second attempt is a resume, not a second force.
        assert len(parts.source.downloads) == downloads
        assert "transcribe" not in worker.calls[calls:]
    finally:
        await close(parts)


async def test_the_force_marker_is_per_video_not_per_job(
    settings: Settings, clip: Path
) -> None:
    """Two videos, one forced job: spending the force on the first must not
    let the second resume as though it had already been invalidated."""
    parts = await harness(settings, clip)
    try:
        await parts.index(urls=[VIDEO_URL, SECOND_URL], channels="transcript")
        assert await parts.run() is True
        calls = len(parts.worker.calls)

        await parts.index(
            urls=[VIDEO_URL, SECOND_URL], channels="transcript", force_reindex=True
        )
        assert await parts.run() is True

        # Both videos were re-transcribed, not just the first.
        assert parts.worker.calls[calls:].count("transcribe") == 2
        job = await parts.one("SELECT args_json FROM jobs ORDER BY id DESC LIMIT 1")
        assert len(json.loads(job["args_json"])["force_applied"]) == 2
    finally:
        await close(parts)


async def test_a_video_with_no_transcript_at_all_is_still_final(
    settings: Settings, clip: Path
) -> None:
    """The other half of the same branch: no 429, no retry, no pretending."""

    class NoCaptions:
        def __init__(self, inner) -> None:  # type: ignore[no-untyped-def]
            self._inner = inner

        def __getattr__(self, name: str):  # type: ignore[no-untyped-def]
            return getattr(self._inner, name)

        def fetch_subtitle(self, track):  # type: ignore[no-untyped-def]
            from vidtheque_mcp.pipeline.sources import SourceError

            raise SourceError("no such track")

    parts = await harness(settings, clip, worker=FakeWorker(healthy=False))
    parts.parts.runner.pipeline.source = NoCaptions(canned_source(clip))
    try:
        job_id = await parts.index(url=VIDEO_URL, channels="transcript")
        assert await parts.run() is True
        job = await job_row(parts, job_id)
        assert (job["state"], job["error_code"]) == ("failed", "E_UNSUPPORTED_SOURCE")
    finally:
        await close(parts)
