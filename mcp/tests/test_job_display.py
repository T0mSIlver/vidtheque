"""job-status stage display: completed stages must not print "pending".

Found live: with the item on `keyframe`, the display showed download and
transcribe as pending — every stage except the running one printed pending,
because the renderer only knew the current stage. The fix infers order from
WIRE_STAGES (the runner advances strictly in order)."""

from __future__ import annotations

from vidtheque_mcp.jobs import store as jobs_store
from vidtheque_mcp.tools.indexing import _wire_state


class Row(dict):
    """sqlite3.Row stand-in: mapping access by key."""


POSITIONS = {
    stage: i
    for i, (_wire, internals) in enumerate(jobs_store.WIRE_STAGES)
    for stage in internals
}


def render(item: Row) -> list[str]:
    current = POSITIONS.get(item["stage"])
    return [
        f"{wire}={_wire_state(item, i, current)}"
        for i, (wire, _internals) in enumerate(jobs_store.WIRE_STAGES)
    ]


def test_mid_job_shows_earlier_stages_done() -> None:
    item = Row(state="running", stage="keyframe", stage_pct=0.0)
    assert render(item) == [
        "download=done",
        "transcribe=done",
        "keyframes=running  0%",
        "ocr=pending",
        "embed=pending",
    ]


def test_second_internal_stage_of_a_wire_group_still_counts_as_that_group() -> None:
    # chunk is the second internal stage of the "transcribe" wire stage.
    item = Row(state="running", stage="chunk", stage_pct=0.4)
    assert render(item) == [
        "download=done",
        "transcribe=running  40%",
        "keyframes=pending",
        "ocr=pending",
        "embed=pending",
    ]


def test_failed_marks_the_failing_stage_and_keeps_earlier_done() -> None:
    item = Row(state="failed", stage="ocr", stage_pct=0.2)
    assert render(item) == [
        "download=done",
        "transcribe=done",
        "keyframes=done",
        "ocr=failed",
        "embed=pending",
    ]


def test_done_item_is_done_everywhere() -> None:
    item = Row(state="done", stage="frame_embed", stage_pct=1.0)
    assert all(line.endswith("=done") for line in render(item))


def test_unknown_stage_degrades_to_pending() -> None:
    item = Row(state="running", stage="mystery", stage_pct=0.0)
    assert all(line.endswith("=pending") for line in render(item))
