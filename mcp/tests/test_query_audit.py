"""Regression tests for the read-side audit (2026-10-21).

Each test is one audited failure, built on the seeded fixture plus the few rows
it needs. The finding number (Q2, Q3, ...) is in each test's docstring.
"""

from __future__ import annotations

import pytest

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


# ------------------------------------------------------------------------ Q10


def _readonly_calls(deps):
    from vidtheque_mcp.tools import frames, indexing, library, segment

    unknown = "abcdefghijk"
    return {
        "search": lambda: search.run(deps, q="cache", video_id=unknown),
        "search-batch": lambda: search.run(deps, q="cache", video_id=[unknown, "abcdefghijl"]),
        "video-summary": lambda: library.video_summary(deps, video_id=unknown),
        "get-segment-context": lambda: segment.run(deps, video_id=unknown, t=3),
        "get-frames": lambda: frames.run(deps, video_id=unknown),
        "job-status": lambda: indexing.job_status(deps, video_id=unknown),
    }


@pytest.mark.parametrize(
    "tool",
    ["search", "search-batch", "video-summary", "get-segment-context", "get-frames", "job-status"],
)
async def test_unknown_video_never_recommends_a_masked_index_video(
    assembled: Assembled, tool: str
) -> None:
    """Q10: on the read-only surface the unknown-video remedy named
    `index-video`, a tool absent from that deployment's tools/list."""
    from vidtheque_mcp.public.readonly import hidden_tools

    deps = assembled.deps
    deps.hidden_tools = hidden_tools(True)
    r = await _readonly_calls(deps)[tool]()
    assert r.is_error
    assert r.structured_content["code"] == "E_UNKNOWN_VIDEO"
    assert "index-video" not in r.content[0].text
    assert "read-only" in r.content[0].text


async def test_segment_refuses_a_video_that_is_not_indexed(assembled: Assembled) -> None:
    """Q10: get-segment-context never checked index_state, though §4.5 lists
    E_NOT_INDEXED and E_INDEXING for it."""
    from vidtheque_mcp.tools import segment

    for state, code in (("pending", "E_NOT_INDEXED"), ("indexing", "E_INDEXING")):
        await assembled.deps.db.write(
            lambda c, s=state: c.execute(
                "UPDATE videos SET index_state=? WHERE public_id='eMlx5fFNoYc'", (s,)
            )
        )
        r = await segment.run(assembled.deps, video_id="eMlx5fFNoYc", t=30)
        assert r.is_error and r.structured_content["code"] == code


# ------------------------------------------------------------------------ Q13


async def test_like_wildcards_in_name_filters_are_literal(assembled: Assembled) -> None:
    """Q13: `channel="_"` matched every channel (LIKE wildcard), `%` and
    `video_title=` too."""
    from vidtheque_mcp.tools import library

    for wildcard in ("_", "%"):
        r = await library.list_videos(assembled.deps, channel=wildcard)
        assert r.structured_content.get("videos", []) == []
        r = await search.run(assembled.deps, q="cache", video_title=wildcard)
        assert r.structured_content.get("results", []) == []
    # A literal underscore still matches where it occurs.
    await assembled.deps.db.write(
        lambda c: c.execute("UPDATE videos SET channel_name='gpu_mode' WHERE public_id='zduSFxRajkE'")
    )
    r = await library.list_videos(assembled.deps, channel="gpu_mode")
    assert [v["video_id"] for v in r.structured_content["videos"]] == ["zduSFxRajkE"]


async def test_speaker_wildcard_is_literal(assembled: Assembled) -> None:
    """Q13: the speaker resolver had the same unescaped LIKE."""
    from vidtheque_mcp.db import queries

    def write(c):
        c.execute("INSERT INTO speakers (label) VALUES ('SPEAKER_00')")

    await assembled.deps.db.write(write)
    assert await assembled.deps.db.read(lambda c: queries.resolve_speakers(c, "%")) == []
    assert await assembled.deps.db.read(lambda c: queries.resolve_speakers(c, "speaker_0"))


# ------------------------------------------------------------------------ Q14


async def test_gaps_counts_every_failed_video_once(assembled: Assembled) -> None:
    """Q14: gaps.failed was len() of a LIMIT 5 stage-row list, so seven failed
    videos with two failed stages each counted 5, and the listing repeated
    a video once per failed stage."""
    from vidtheque_mcp.tools import library

    def write(c):
        for i in range(7):
            cur = c.execute(
                "INSERT INTO videos (owner_id, source_id, url, title, index_state) "
                "VALUES (1, ?, 'u', ?, 'failed')",
                (f"failvid{i:04d}", f"t{i}"),
            )
            for stage in ("fetch", "stt"):
                c.execute(
                    "INSERT INTO video_stages (video_id, stage, state, error) "
                    "VALUES (?, ?, 'failed', 'boom')",
                    (cur.lastrowid, stage),
                )

    await assembled.deps.db.write(write)
    r = await library.corpus_summary(assembled.deps)
    assert r.structured_content["gaps"]["failed"] == 7
    listed = [ln.split()[1] for ln in r.content[0].text.splitlines() if ln.strip().startswith("failed:")]
    assert len(listed) == len(set(listed)) == 5
    assert r.structured_content["gaps"]["failed_videos"] == 7
    assert "2 more video(s) with a failed stage" in r.content[0].text
