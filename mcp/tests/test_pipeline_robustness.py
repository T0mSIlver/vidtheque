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
from vidtheque_mcp.pipeline.settings import PipelineSettings

from .pipeline_fakes import VIDEO_URL, FakeWorker, canned_source
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
