"""Scene detection, sharpest-frame selection and perceptual dedup, on a real file.

These are the two stages a mock cannot check: whether the detector's weights
find the cuts, and whether the hash width tells two screens apart. The fixture
is an 8-second clip ffmpeg synthesizes on the spot — four shots, the second and
fourth pixel-identical, so "these two frames are the same screen" is known
rather than eyeballed.

No network, no models, no GPU: PySceneDetect and ImageHash are pure CPU.
"""

from __future__ import annotations

from pathlib import Path

from vidtheque_mcp.pipeline import keyframes
from vidtheque_mcp.pipeline.paths import Layout


def _extract(clip: Path, out: Path, **kwargs) -> list[keyframes.KeyframeDraft]:
    layout = Layout(out)
    directory = layout.keyframes_dir("aB3dEfG7hIj")
    return keyframes.extract_keyframes(
        clip,
        directory,
        lambda ordinal, t_s: layout.keyframe_relpath("aB3dEfG7hIj", ordinal, t_s),
        **kwargs,
    )


def test_the_detector_finds_the_cuts(clip: Path) -> None:
    shots = keyframes.detect_shots(clip, kind="screencast", max_shot_seconds=25.0)
    # Four 2-second segments with hard cuts between them.
    assert len(shots) >= 4
    assert shots[0].start_s == 0.0
    assert shots[-1].end_s > 7.0


def test_extraction_writes_one_jpeg_per_shot_with_schema_paths(clip: Path, tmp_path: Path) -> None:
    drafts = _extract(clip, tmp_path)
    assert drafts
    for draft in drafts:
        assert draft.relpath.startswith("keyframes/aB3dEfG7hIj/")
        # <ord:05d>-<t_ms:09d>.jpg — sorts lexically into time order.
        name = Path(draft.relpath).name
        ordinal, rest = name.split("-", 1)
        assert ordinal == f"{draft.ordinal:05d}"
        assert rest == f"{int(round(draft.t_s * 1000)):09d}.jpg"
        assert draft.absolute is not None and draft.absolute.exists()
        assert draft.jpeg_bytes == draft.absolute.stat().st_size
        assert draft.width > 0 and draft.height > 0
        assert -(2**63) <= draft.phash < 2**63


def test_timestamps_are_inside_their_shot_and_strictly_increasing(
    clip: Path, tmp_path: Path
) -> None:
    drafts = _extract(clip, tmp_path)
    stamps = [d.t_s for d in drafts]
    assert stamps == sorted(stamps)
    assert len(set(stamps)) == len(stamps)  # UNIQUE(video_id, t_s)
    for draft in drafts:
        assert draft.shot.start_s - 0.5 <= draft.t_s <= draft.shot.end_s + 0.5


def test_identical_shots_are_marked_dup_of_the_earlier_one(clip: Path, tmp_path: Path) -> None:
    """First-wins in timeline order: the kept frame is the earliest occurrence,
    which is the timestamp a user actually wants to jump to."""
    drafts = _extract(clip, tmp_path)
    duplicates = [d for d in drafts if d.dup_of is not None]
    assert duplicates, "the repeated bars shot should have been caught"
    for duplicate in duplicates:
        assert duplicate.dup_of < duplicate.ordinal


def test_a_threshold_of_zero_keeps_everything_distinct(clip: Path, tmp_path: Path) -> None:
    drafts = _extract(clip, tmp_path, phash_threshold=0)
    # Only genuinely bit-identical hashes collapse at 0.
    assert sum(1 for d in drafts if d.dup_of is not None) <= len(drafts) - 2


def test_the_budget_is_a_hard_cap(clip: Path, tmp_path: Path) -> None:
    drafts = _extract(clip, tmp_path, budget=2, max_shot_seconds=1.0)
    assert len(drafts) <= 2


def test_frames_are_capped_at_the_configured_width(clip: Path, tmp_path: Path) -> None:
    drafts = _extract(clip, tmp_path, max_width=160)
    assert drafts and all(d.width <= 160 for d in drafts)


def test_sharpness_is_recorded_for_the_get_frames_tiebreak(clip: Path, tmp_path: Path) -> None:
    drafts = _extract(clip, tmp_path)
    assert all(d.sharpness >= 0.0 for d in drafts)
    assert any(d.sharpness > 0.0 for d in drafts)


# ---------------------------------------------------------------- decode cost


def test_detection_decodes_with_frame_threading(clip: Path, monkeypatch) -> None:
    """PyAV's default for H.264 is SLICE alone, and that is the whole stage.

    Measured on a 1080p50 talk: 406 frames/s default against 1211 with AUTO,
    138s -> 94s end to end (research/keyframe-decode-bench-2026-08-08.md). It is
    a one-word argument that is easy to lose in a refactor, so it is asserted.
    """
    import scenedetect

    seen: dict[str, object] = {}
    real = scenedetect.open_video

    def spy(path, *args, **kwargs):
        seen.update(kwargs)
        return real(path, *args, **kwargs)

    monkeypatch.setattr(scenedetect, "open_video", spy)
    keyframes.detect_spans(clip)
    assert seen["backend"] == "pyav"  # PTS-backed timestamps, not frame_num/fps
    assert seen["threading_mode"] == "AUTO"


def test_frame_threading_does_not_move_the_cuts(clip: Path) -> None:
    """The reason threaded decoding is the *only* free win here: the detector
    sees the same frames in the same order, so the answer is bit-identical.
    Anything else that makes the decode cheaper (a smaller companion stream,
    frame skipping) changes what it sees — measured, and rejected, in the bench."""
    from scenedetect import SceneManager, open_video

    def cuts(mode: str) -> list[float]:
        video = open_video(str(clip), backend="pyav", threading_mode=mode)
        manager = SceneManager()
        manager.add_detector(keyframes.make_detector("screencast"))
        manager.auto_downscale = True
        manager.detect_scenes(video=video, show_progress=False)
        return [
            round(float(start.seconds), 3)
            for start, _ in manager.get_scene_list(start_in_scene=True)
        ]

    assert cuts("AUTO") == cuts("NONE")


# ------------------------------------------------------- pass 2 parallelism


def _fingerprint(drafts: list[keyframes.KeyframeDraft]) -> list[tuple]:
    """Everything that reaches the database, in the order it reaches it."""
    return [
        (
            d.ordinal,
            d.t_s,
            d.shot.index,
            round(d.shot.start_s, 3),
            round(d.shot.end_s, 3),
            d.sharpness,
            d.width,
            d.height,
            d.relpath,
            d.jpeg_bytes,
            d.phash,
            d.dup_of,
        )
        for d in drafts
    ]


def test_pooled_extraction_returns_exactly_what_the_serial_pass_returns(
    clip: Path, tmp_path: Path
) -> None:
    """The claim that lets `VIDTHEQUE_KEYFRAME_EXTRACT_WORKERS` stay out of the
    stage's model_key: threads change when frames are *found*, never which
    frames are found or what order they are committed in.

    `max_shot_seconds=1.0` is what makes this worth running — it puts ~8 shots
    through a chunk size of 2x2, so the pool refills and the committer has to
    keep the ordering across chunk boundaries rather than inside one batch.
    """
    serial = _extract(clip, tmp_path / "serial", max_shot_seconds=1.0, workers=1)
    pooled = _extract(clip, tmp_path / "pooled", max_shot_seconds=1.0, workers=2)

    assert len(serial) > 4, "the fixture should produce enough shots to span chunks"
    assert _fingerprint(pooled) == _fingerprint(serial)
    # Same bytes on disk too, not merely the same recorded size.
    for one, other in zip(serial, pooled):
        assert one.absolute is not None and other.absolute is not None
        assert one.absolute.read_bytes() == other.absolute.read_bytes()


def test_the_pool_still_commits_in_shot_order(clip: Path, tmp_path: Path) -> None:
    """Ordinals are assigned by the committer, so out-of-order completion must
    not leak into them: `<ord>-<t_ms>.jpg` has to stay sortable into time order
    and `UNIQUE(video_id, t_s)` has to keep holding."""
    drafts = _extract(clip, tmp_path, max_shot_seconds=1.0, workers=4)
    assert [d.ordinal for d in drafts] == list(range(len(drafts)))
    stamps = [d.t_s for d in drafts]
    assert stamps == sorted(stamps)
    assert len(set(stamps)) == len(stamps)
    for draft in drafts:
        assert Path(draft.relpath).name.startswith(f"{draft.ordinal:05d}-")


def test_a_worker_pool_of_one_never_builds_a_pool(clip: Path, tmp_path: Path, monkeypatch) -> None:
    """The default must be the old code path, not a one-thread pool wearing it."""
    import concurrent.futures

    def refuse(*args, **kwargs):  # pragma: no cover - the assertion is that it is not hit
        raise AssertionError("workers=1 should not construct a ThreadPoolExecutor")

    monkeypatch.setattr(concurrent.futures, "ThreadPoolExecutor", refuse)
    assert _extract(clip, tmp_path, workers=1)


def test_decode_threads_do_not_move_the_frames(clip: Path, tmp_path: Path) -> None:
    """`CAP_PROP_N_THREADS` is a scheduling knob; H.264 decode is deterministic
    and `CAP_PROP_POS_MSEC` is read back from the frame that actually arrived,
    so frame threading must not shift a single timestamp. Asserted because a
    drifting POS_MSEC would silently produce wrong deep links, which is the
    product."""
    plain = _extract(clip, tmp_path / "plain", max_shot_seconds=1.0)
    threaded = _extract(clip, tmp_path / "threaded", max_shot_seconds=1.0, decode_threads=4)
    assert _fingerprint(threaded) == _fingerprint(plain)


def test_extract_worker_counts_are_validated_at_boot() -> None:
    """A pool size of 0 raises inside ThreadPoolExecutor minutes into a job,
    which is the worst possible place to find a typo."""
    import pytest

    from vidtheque_mcp.config import ConfigError
    from vidtheque_mcp.pipeline.settings import PipelineSettings

    assert PipelineSettings().extract_workers == 1
    assert PipelineSettings().extract_decode_threads == 0
    with pytest.raises(ConfigError, match="EXTRACT_WORKERS"):
        PipelineSettings(extract_workers=0).validate()
    with pytest.raises(ConfigError, match="DECODE_THREADS"):
        PipelineSettings(extract_decode_threads=-1).validate()


def test_the_stage_model_key_ignores_the_thread_knobs() -> None:
    """Both knobs promise the same output, so neither may invalidate a stage —
    a corpus-wide reindex for a thread count would be the opposite of a win."""
    import inspect

    from vidtheque_mcp.pipeline import runner

    source = inspect.getsource(runner.IndexingPipeline._keyframe_model_key)
    assert "extract_workers" not in source
    assert "extract_decode_threads" not in source
