"""Regression tests for the read-side audit (2026-10-21).

Each test is one audited failure, built on the seeded fixture plus the few rows
it needs. The finding number (Q2, Q3, ...) is in each test's docstring.
"""

from __future__ import annotations

from vidtheque_mcp.tools import search

from .conftest import Assembled


async def _add_cues(db, public_id: str, rows: list[tuple[float, float, str]]) -> None:
    def write(c):
        vid = c.execute("SELECT id FROM videos WHERE public_id=?", (public_id,)).fetchone()[0]
        seq0 = c.execute(
            "SELECT COALESCE(MAX(seq), 0) + 1 FROM cues WHERE video_id=?", (vid,)
        ).fetchone()[0]
        for i, (start, end, text) in enumerate(rows):
            c.execute(
                "INSERT INTO cues (video_id, seq, start_s, end_s, text) VALUES (?, ?, ?, ?, ?)",
                (vid, seq0 + i, start, end, text),
            )

    await db.write(write)


# ------------------------------------------------------------------------- Q2


async def test_video_time_returns_the_earliest_matches_not_the_strongest(
    assembled: Assembled,
) -> None:
    """Q2: the per-video leg cap kept the top islands by score, so a
    chronological read skipped the weaker early matches."""
    rows = []
    for i in range(20):
        t = 1000 + i * 200
        weak = "widget " + " ".join(f"filler{j}" for j in range(30))
        rows.append((t, t + 3, weak if i < 10 else "widget widget widget"))
    await _add_cues(assembled.deps.db, "kCc8FmEb1nY", rows)
    r = await search.run(
        assembled.deps,
        q="widget",
        order="video_time",
        video_id="kCc8FmEb1nY",
        content_type="transcript",
    )
    assert [x["start"] for x in r.structured_content["results"]] == [1000, 1200, 1400]
