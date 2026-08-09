"""The pipeline's failure modes, from the robustness review of 2026-08-09.

`test_pipeline_e2e` covers the happy path and an ordinary resume; these are the
shapes an unattended 116-video night actually produces — a transient worker
error mid-item, a video that is not a video *yet*, a caption URL that 403s, a
worker that answers seven of eight. Each test is one reviewed trigger.

Nothing here reaches the network or a GPU: same fakes as `test_pipeline_e2e`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from vidtheque_mcp.config import Settings
from vidtheque_mcp.pipeline.runner import LIFECYCLE_RETRY_S
from vidtheque_mcp.pipeline.settings import PipelineSettings

from .pipeline_fakes import INFO, VIDEO_URL, FakeWorker, canned_source
from .test_job_recovery import close
from .test_pipeline_e2e import FAST, Harness, harness


def media_files(parts: Harness, source_id: str = "aB3dEfG7hIj") -> list[Path]:
    return list((parts.parts.settings.data_dir / "media").glob(f"{source_id}.*"))


def audio_files(parts: Harness, source_id: str = "aB3dEfG7hIj") -> list[Path]:
    return list((parts.parts.settings.data_dir / "audio").glob(f"{source_id}.*"))


def keyframe_files(parts: Harness, source_id: str = "aB3dEfG7hIj") -> list[Path]:
    return list((parts.parts.settings.data_dir / "keyframes" / source_id).glob("*.jpg"))


# ==================================================================== retention


async def test_a_soft_failure_downstream_still_releases_the_source_video(
    settings: Settings, clip: Path
) -> None:
    """The disk leak: any soft failure pinned the mp4 forever.

    `_retention` returned early on a non-empty `failed_stages`, so a transient
    OCR or frame-embedding error kept a multi-gigabyte source video for good —
    the item finished `done`, the video went `ready`, and nothing was ever going
    to schedule the retry that the file was being kept for.
    """
    parts = await harness(settings, clip, worker=FakeWorker(fail={"ocr", "embed_images"}))
    try:
        await parts.index(url=VIDEO_URL)
        assert await parts.run() is True

        stages = await parts.stages()
        assert stages["keyframe"]["state"] == "done"
        assert stages["ocr"]["state"] == "failed"
        assert stages["frame_embed"]["state"] == "failed"

        # `keyframe` consumed the mp4 and is done with it. The stages that
        # failed read JPEGs, and the JPEGs are still there for their retry.
        assert media_files(parts) == []
        assert keyframe_files(parts)
        assert audio_files(parts)  # keep_source=audio, the default
    finally:
        await close(parts)


async def test_the_source_video_survives_a_failure_of_the_stage_that_needs_it(
    settings: Settings,
) -> None:
    """The other side of the rule: `keyframe` failed, so its input is kept.

    No clip, so the "downloaded" mp4 is bytes OpenCV cannot open — exactly the
    decode failure `_stage_keyframes` soft-fails on.
    """
    parts = await harness(settings, None)
    try:
        await parts.index(url=VIDEO_URL)
        assert await parts.run() is True

        stages = await parts.stages()
        assert stages["keyframe"]["state"] == "failed"
        assert media_files(parts), "the input to the keyframe retry was deleted"
    finally:
        await close(parts)


async def test_keep_source_none_releases_the_audio_once_stt_has_settled(
    settings: Settings, clip: Path
) -> None:
    parts = await harness(
        settings,
        clip,
        pipeline_settings=PipelineSettings(
            between_videos_s=0.0, sleep_subtitles_s=0.0, max_shot_seconds=2.0, keep_source="none"
        ),
    )
    try:
        await parts.index(url=VIDEO_URL)
        assert await parts.run() is True
        assert (await parts.stages())["stt"]["state"] == "done"
        assert audio_files(parts) == []
        assert media_files(parts) == []
    finally:
        await close(parts)


# ================================================================ 403 throttling


def test_a_download_403_is_classified_as_throttling_not_an_internal_error() -> None:
    """Measured twice in seven bench runs while a sibling agent shared the IP.

    The same URL works minutes later, so it is throttling wearing a different
    status code — but it arrived as `E_INTERNAL` and burned the generic retry
    budget instead of the rate-limit one.
    """
    from vidtheque_mcp.pipeline.sources import _is_rate_limit, _is_unavailable

    throttled = "ERROR: unable to download video data: HTTP Error 403: Forbidden"
    assert _is_rate_limit(throttled)
    assert _is_rate_limit("giving up after 3 fragment retries: HTTP Error 403")
    assert _is_rate_limit("HTTP Error 429: Too Many Requests")

    # Narrow on purpose: a 403 on an *extraction* is geo-blocking or
    # members-only, which no amount of waiting fixes.
    members = "ERROR: Join this channel to get access to members-only content"
    assert not _is_rate_limit(members)
    assert _is_unavailable(members)


async def test_a_403_on_the_media_download_defers_the_job(
    settings: Settings, clip: Path
) -> None:
    class Forbidden:
        def __init__(self, inner) -> None:  # type: ignore[no-untyped-def]
            self._inner = inner

        def __getattr__(self, name: str):  # type: ignore[no-untyped-def]
            return getattr(self._inner, name)

        def download_video(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            from vidtheque_mcp.pipeline.sources import RateLimited

            raise RateLimited(
                "unable to download video data: HTTP Error 403: Forbidden"
            )

    parts = await harness(settings, clip)
    parts.parts.runner.pipeline.source = Forbidden(canned_source(clip))
    parts.parts.runner.rate_limit_backoff_s = 240
    try:
        job_id = await parts.index(url=VIDEO_URL)
        assert await parts.run() is True

        job = await parts.one(
            "SELECT *, unixepoch() AS now FROM jobs WHERE public_id = ?", (job_id,)
        )
        assert (job["state"], job["error_code"]) == ("queued", "E_RATE_LIMIT")
        assert job["not_before"] - job["now"] == pytest.approx(240, abs=5)
    finally:
        await close(parts)


# ==================================================================== lifecycle


async def test_a_premiere_is_deferred_rather_than_permanently_failed(
    settings: Settings, clip: Path
) -> None:
    """`is_upcoming` settled the item `failed` for good, tonight.

    It is a lifecycle state — the video exists, it is just not a video yet — and
    treating it like a takedown means a channel backfill run at 9pm permanently
    loses whatever was premiering at 9pm.
    """
    import time as _time

    parts = await harness(settings, clip)
    source = parts.parts.runner.pipeline.source
    source.infos = dict(source.infos)
    source.infos[VIDEO_URL] = dict(INFO) | {
        "live_status": "is_upcoming",
        "release_timestamp": int(_time.time()) + 900,
    }
    try:
        job_id = await parts.index(url=VIDEO_URL)
        assert await parts.run() is True

        job = await parts.one(
            "SELECT *, unixepoch() AS now FROM jobs WHERE public_id = ?", (job_id,)
        )
        assert job["state"] == "queued"
        assert job["not_before"] - job["now"] == pytest.approx(900, abs=10)
        item = await parts.one("SELECT state, attempts FROM job_items")
        assert (item["state"], item["attempts"]) == ("queued", 1)
        assert any("E_NOT_READY_YET" in message for message in await parts.events())
    finally:
        await close(parts)


async def test_post_live_waits_the_default_window(settings: Settings, clip: Path) -> None:
    """No `release_timestamp`, so the pipeline's own window applies."""
    parts = await harness(settings, clip)
    source = parts.parts.runner.pipeline.source
    source.infos = dict(source.infos)
    source.infos[VIDEO_URL] = dict(INFO) | {"live_status": "post_live"}
    try:
        job_id = await parts.index(url=VIDEO_URL)
        assert await parts.run() is True
        job = await parts.one(
            "SELECT *, unixepoch() AS now FROM jobs WHERE public_id = ?", (job_id,)
        )
        assert job["not_before"] - job["now"] == pytest.approx(LIFECYCLE_RETRY_S, abs=10)
    finally:
        await close(parts)


async def test_a_private_video_still_fails_immediately_and_finally(
    settings: Settings, clip: Path
) -> None:
    """Permanent unavailability must not inherit the lifecycle retry."""
    parts = await harness(settings, clip)
    source = parts.parts.runner.pipeline.source
    source.infos = dict(source.infos)
    source.infos[VIDEO_URL] = dict(INFO) | {"availability": "private"}
    try:
        job_id = await parts.index(url=VIDEO_URL)
        assert await parts.run() is True
        job = await parts.one("SELECT state, error_code FROM jobs WHERE public_id = ?", (job_id,))
        assert (job["state"], job["error_code"]) == ("failed", "E_UNSUPPORTED_SOURCE")
    finally:
        await close(parts)


# ================================================================= stt fallback


NO_CAPTIONS = {
    **{k: v for k, v in INFO.items() if k not in ("subtitles", "automatic_captions")},
}


def uncaption(parts: Harness) -> None:
    """Give the harness the same video with an empty caption inventory.

    The review's trigger: `prefer_whisperx`, no caption track, one failed
    health probe. Both the pipeline and the harness must see the same source
    object, or `downloads` counts the wrong one.
    """
    source = canned_source(parts.parts.runner.pipeline.source.clip)
    source.infos = dict(source.infos)
    source.infos[VIDEO_URL] = NO_CAPTIONS
    parts.parts.runner.pipeline.source = source
    parts.source = source


async def test_an_uncaptioned_video_keeps_its_audio_when_the_probe_fails(
    settings: Settings, clip: Path
) -> None:
    """One flaky `/healthz` used to permanently fail an uncaptioned video.

    Fetch suppressed the audio download because captions were *allowed*, never
    checking one existed. whisperX then had nothing to transcribe and returned
    None, captions returned None, and an empty error list made the STT failure
    non-retryable. The video settled `failed` for good — with its mp4 left
    behind, since `channels=all` had already downloaded it.
    """
    worker = FakeWorker(healthy=False)
    parts = await harness(settings, clip, worker=worker)
    uncaption(parts)
    try:
        await parts.index(url=VIDEO_URL, channels="transcript")
        # The probe says down, but there are no captions to fall back to, so the
        # audio is fetched anyway and whisperX is given its chance.
        worker._healthy = True
        assert await parts.run() is True

        assert "audio" in parts.source.downloads
        stages = await parts.stages()
        assert stages["stt"]["state"] == "done"
        assert stages["stt"]["model_key"] == "large-v3"  # whisperX, not captions
        assert (await parts.one("SELECT index_state FROM videos"))["index_state"] == "ready"
    finally:
        await close(parts)


async def test_a_captioned_video_still_skips_the_audio_when_the_worker_is_down(
    settings: Settings, clip: Path
) -> None:
    """The zero-GPU shortcut is unchanged where it was always right."""
    parts = await harness(settings, clip, worker=FakeWorker(healthy=False))
    try:
        await parts.index(url=VIDEO_URL, channels="transcript")
        assert await parts.run() is True
        assert "audio" not in parts.source.downloads
        stages = await parts.stages()
        assert stages["stt"]["state"] == "done"
        assert stages["stt"]["model_key"] == "youtube-asr-en"
    finally:
        await close(parts)


async def test_a_worker_outage_is_retryable_not_an_unsupported_source(
    settings: Settings, clip: Path
) -> None:
    """The item must not conclude "this video has no transcript" from an outage."""
    worker = FakeWorker(healthy=False, fail={"transcribe"})
    parts = await harness(settings, clip, worker=worker)
    uncaption(parts)
    try:
        job_id = await parts.index(url=VIDEO_URL, channels="transcript")
        assert await parts.run() is True  # the worker stays down all run

        job = await parts.one("SELECT * FROM jobs WHERE public_id = ?", (job_id,))
        assert job["state"] == "queued"  # deferred for another attempt
        item = await parts.one("SELECT state, error_code FROM job_items")
        assert item["state"] == "queued"
        events = await parts.events()
        assert any("E_WORKER_UNAVAILABLE" in message for message in events)
    finally:
        await close(parts)


async def test_the_worker_is_probed_again_at_stt_time(
    settings: Settings, clip: Path
) -> None:
    """The fetch-time probe is minutes old and decided whether audio exists."""
    worker = FakeWorker(healthy=False)
    parts = await harness(settings, clip, worker=worker)
    pipeline = parts.parts.runner.pipeline
    probes: list[bool] = []
    original = pipeline._worker_healthy

    async def counted() -> bool:
        result = await original()
        probes.append(result)
        return result

    pipeline._worker_healthy = counted  # type: ignore[method-assign]
    try:
        await parts.index(url=VIDEO_URL, channels="transcript")
        assert await parts.run() is True
        assert len(probes) == 2, "the stt stage trusted a stale fetch-time probe"
    finally:
        await close(parts)


async def test_keep_source_originals_keeps_everything(
    settings: Settings, clip: Path
) -> None:
    parts = await harness(
        settings,
        clip,
        pipeline_settings=PipelineSettings(
            between_videos_s=0.0,
            sleep_subtitles_s=0.0,
            max_shot_seconds=2.0,
            keep_source="originals",
        ),
    )
    try:
        await parts.index(url=VIDEO_URL)
        assert await parts.run() is True
        assert media_files(parts)
        assert audio_files(parts)
    finally:
        await close(parts)
