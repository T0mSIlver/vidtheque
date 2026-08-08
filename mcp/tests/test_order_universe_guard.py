"""The non-relevance orderings must sort a UNIVERSE, not a relevance prefix.

`test_tools.py` asserts the user-visible outcome. This file asserts the
mechanism that produces it, by shrinking `ORDER_UNIVERSE` back to the old
behaviour (fetch exactly the page) and showing the outcome breaks — otherwise a
future change could quietly restore the prefix bug while the outcome tests keep
passing by luck of the fixture's scores.
"""

from __future__ import annotations

import pytest

from vidtheque_mcp.app import Assembled
from vidtheque_mcp.tools import search


def _rows(result) -> list[dict]:
    assert result.structured_content is not None
    return result.structured_content["results"]


@pytest.mark.parametrize("universe", [1, search.ORDER_UNIVERSE])
async def test_recency_depends_on_the_candidate_universe(
    assembled: Assembled, monkeypatch: pytest.MonkeyPatch, universe: int
) -> None:
    monkeypatch.setattr(search, "ORDER_UNIVERSE", universe)
    result = await search.run(
        assembled.deps,
        q="attention OR cache OR memory",
        order="recency",
        limit=1,
        cluster_gap=0,
        max_per_video=20,
    )
    rows = _rows(result)
    assert rows
    newest_first = rows[0]["video_id"] == "eMlx5fFNoYc"
    if universe == 1:
        # The old shape: one row fetched by score, then "sorted" by date.
        assert not newest_first, "fixture no longer reproduces the prefix bug"
    else:
        assert newest_first, rows


@pytest.mark.parametrize("universe", [1, search.ORDER_UNIVERSE])
async def test_video_time_depends_on_the_candidate_universe(
    assembled: Assembled, monkeypatch: pytest.MonkeyPatch, universe: int
) -> None:
    monkeypatch.setattr(search, "ORDER_UNIVERSE", universe)
    result = await search.run(
        assembled.deps,
        # The fixture's best-scoring hit in this video is at 12.0 s, not the
        # earliest at 0.0 s — so a relevance prefix of one cannot be sorted
        # into the right chronological answer.
        q="tokenization OR memory",
        order="video_time",
        video_id="kCc8FmEb1nY",
        limit=1,
        cluster_gap=0,
        max_per_video=20,
    )
    rows = _rows(result)
    assert rows
    if universe == 1:
        assert rows[0]["start"] != 0.0, "fixture no longer reproduces the prefix bug"
    else:
        assert rows[0]["start"] == 0.0, rows
