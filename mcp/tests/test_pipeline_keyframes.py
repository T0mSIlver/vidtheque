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
