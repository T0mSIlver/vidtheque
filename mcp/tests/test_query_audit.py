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


# ------------------------------------------------------------------------- Q9


async def _duplicate_slide(db, t_s: float = 60.0) -> None:
    """A second live keyframe with the same phash and lines as the fixture's slide."""

    def write(c):
        vid = c.execute("SELECT id FROM videos WHERE public_id='zduSFxRajkE'").fetchone()[0]
        k = c.execute("SELECT * FROM keyframes WHERE video_id=?", (vid,)).fetchone()
        cur = c.execute(
            "INSERT INTO keyframes (video_id, ord, t_s, shot_id, shot_start_s, shot_end_s, "
            "phash, sharpness, width, height, jpeg_path, jpeg_bytes, ocr_state) "
            "VALUES (?, 1, ?, 1, ?, ?, ?, 10.0, 1280, 720, ?, ?, 'done')",
            (vid, t_s, t_s, t_s + 5, k["phash"], k["jpeg_path"], k["jpeg_bytes"]),
        )
        lines = [
            r["text"]
            for r in c.execute(
                "SELECT text FROM ocr_lines WHERE keyframe_id=? ORDER BY line_no", (k["id"],)
            )
        ]
        for i, text in enumerate(lines):
            c.execute(
                "INSERT INTO ocr_lines (keyframe_id, video_id, t_s, line_no, text, conf, "
                "x0, y0, x1, y1) VALUES (?, ?, ?, ?, ?, 0.9, 0, 0, 1, 1)",
                (cur.lastrowid, vid, t_s, i, text),
            )

    await db.write(write)


async def test_ocr_highlights_collapse_frames_with_one_phash(assembled: Assembled) -> None:
    """Q9: the docstring promised phash dedup but the query grouped by
    keyframe, so a slide shown twice was highlighted twice."""
    from vidtheque_mcp.tools import library

    await _duplicate_slide(assembled.deps.db)
    r = await library.video_summary(assembled.deps, video_id="zduSFxRajkE")
    highlights = r.structured_content["ocr_highlights"]
    assert [h["t"] for h in highlights] == [12.0]


async def test_video_summary_span_scopes_every_section(assembled: Assembled) -> None:
    """Q9: with t_start/t_end the empty OCR line claimed the whole video had no
    on-screen text, and chapters and links ignored the span."""
    from vidtheque_mcp.tools import library

    def write(c):
        vid = c.execute("SELECT id FROM videos WHERE public_id='kCc8FmEb1nY'").fetchone()[0]
        c.execute("DELETE FROM chapters WHERE video_id=?", (vid,))
        c.execute(
            "INSERT INTO chapters (video_id, seq, start_s, end_s, title) VALUES "
            "(?, 0, 0, 100, 'intro'), (?, 1, 100, 300, 'middle'), (?, 2, 300, 7000, 'end')",
            (vid, vid, vid),
        )

    await assembled.deps.db.write(write)
    r = await library.video_summary(
        assembled.deps, video_id="kCc8FmEb1nY", t_start=150, t_end=200, include_links=True
    )
    text = r.content[0].text
    assert "no keyframe of this video" not in text
    assert "in the requested t_start/t_end span" in text.split("On-screen")[1]
    assert [c["title"] for c in r.structured_content["chapters"]] == ["middle"]
    # The fixture's one link sits at 5 s, outside the span.
    assert "example.com/paper" not in text


# ------------------------------------------------------------------------- Q4


async def _long_slide(db, t_s: float = 11.0, chars: int = 1400) -> None:
    def write(c):
        vid = c.execute("SELECT id FROM videos WHERE public_id='zduSFxRajkE'").fetchone()[0]
        cur = c.execute(
            "INSERT INTO keyframes (video_id, ord, t_s, shot_id, shot_start_s, shot_end_s, "
            "phash, sharpness, width, height, jpeg_path, jpeg_bytes, ocr_state) "
            "VALUES (?, 1, ?, 9, ?, ?, 999, 10.0, 1280, 720, 'x', 1, 'done')",
            (vid, t_s, t_s, t_s + 1),
        )
        c.execute(
            "INSERT INTO ocr_lines (keyframe_id, video_id, t_s, line_no, text, conf, "
            "x0, y0, x1, y1) VALUES (?, ?, ?, 0, ?, 0.9, 0, 0, 1, 1)",
            (cur.lastrowid, vid, t_s, ("def f(x): return x " * 100)[:chars]),
        )

    await db.write(write)


async def test_one_long_slide_does_not_hide_the_other_ocr(assembled: Assembled) -> None:
    """Q4: the OCR block cap charged the untruncated length and stopped at the
    first frame over it, so one >1200-char slide hid every slide."""
    from vidtheque_mcp.tools import segment

    await _long_slide(assembled.deps.db)
    for mtc in (4000, 0):
        r = await segment.run(
            assembled.deps, video_id="zduSFxRajkE", t=12, window=10, max_text_chars=mtc
        )
        ocr = r.content[0].text.split("ON-SCREEN TEXT")[1]
        assert "paged kv cache" in ocr, mtc
        if mtc:
            # the long slide is shown middle-truncated, charged at what is shown
            assert "def f(x)" in ocr
            assert "OCR capped" not in ocr
        else:
            # untruncated it does not fit: skipped, and the cap says so
            assert "OCR capped at 1200 chars" in ocr


# ------------------------------------------------------------------------- Q3


async def test_segment_budget_grows_outward_from_t(assembled: Assembled) -> None:
    """Q3: the char budget filled forward from the window's start, so with a
    wide window the printed text could end before t."""
    from vidtheque_mcp.tools import segment

    rows = [
        (1000 + i * 6, 1000 + i * 6 + 5.5, f"sentence number {i:03d} " + "lorem ipsum dolor sit amet " * 2)
        for i in range(117)
    ]
    await _add_cues(assembled.deps.db, "kCc8FmEb1nY", rows)
    r = await segment.run(assembled.deps, video_id="kCc8FmEb1nY", t=1500, window=300)
    sc = r.structured_content
    starts = [c["start"] for c in sc["cues"]]
    assert sc["binding_cap"] == "max_text_chars"
    assert any(c["start"] <= 1500 <= c["end"] for c in sc["cues"])
    assert min(starts) < 1450 and max(starts) > 1550
    assert starts == sorted(starts)


async def test_segment_always_prints_the_cue_at_t(assembled: Assembled) -> None:
    """Q3: a cue longer than the budget printed nothing, and the footer named
    the last cue of the window: `(cues 11-12 · 0 chars`."""
    from vidtheque_mcp.tools import segment

    await _add_cues(
        assembled.deps.db, "eMlx5fFNoYc", [(600.0, 610.0, "y" * 300), (611.0, 612.0, "short")]
    )
    r = await segment.run(
        assembled.deps, video_id="eMlx5fFNoYc", t=605, window=10, max_text_chars=200
    )
    cues = r.structured_content["cues"]
    assert [c["text"] for c in cues] == ["y" * 300]
    footer = next(ln for ln in r.content[0].text.splitlines() if ln.startswith("(cues"))
    assert footer.startswith(f"(cues {cues[0]['cue_id']}-{cues[0]['cue_id']} · 300 chars")


# ------------------------------------------------------------------------- Q7


async def _wide_island(db) -> None:
    """21 weak cues then one strong one, contiguous: one island whose anchor
    sits ~105 s after its start."""
    rows = []
    for i in range(22):
        t = 2160 + i * 5
        text = (
            "gadget " + " ".join(f"pad{j}" for j in range(25)) if i < 21 else "gadget gadget gadget"
        )
        rows.append((t, t + 4.5, text))
    await _add_cues(db, "kCc8FmEb1nY", rows)


async def test_follow_ups_aim_at_the_match_not_the_segment_start(assembled: Assembled) -> None:
    """Q7: search's `next:` line, the ask evidence and the demo facade's
    timestamp and thumbnail used the segment start, while the link (§3.6)
    points at the matched cue."""
    import re

    from vidtheque_mcp.public import api, ask

    await _wide_island(assembled.deps.db)
    r = await search.run(assembled.deps, q="gadget", limit=1)
    hit = r.structured_content["results"][0]
    assert hit["match_start"] - hit["start"] > 60  # the case under test
    t = int(re.search(r"get-segment-context .* t=(\d+)", r.structured_content["next"]).group(1))
    assert t == int(hit["match_start"])
    assert f"t={int(hit['match_start'])}" in r.content[0].text.split("next:")[-1]

    evidence = ask.Evidence()
    evidence.record(hit)
    assert evidence.items[0].t == int(hit["match_start"])

    from vidtheque_mcp.text import clock

    row = api._decorate_hit(assembled.deps, hit)
    assert row["timestamp"] == clock(hit["match_start"])
    # a keyframe between the segment's start and its match
    await assembled.deps.db.write(
        lambda c: c.execute(
            "INSERT INTO keyframes (video_id, ord, t_s, shot_id, shot_start_s, shot_end_s, "
            "phash, sharpness, width, height, jpeg_path, jpeg_bytes) "
            "SELECT id, 2, 2200, 2, 2200, 2205, 77, 10.0, 1280, 720, 'x', 1 FROM videos "
            "WHERE public_id='kCc8FmEb1nY'"
        )
    )
    shown = await assembled.deps.db.read(lambda c: api._frames_on_screen(c, [hit]))
    assert shown == {0: "kCc8FmEb1nY-00002"}


# ------------------------------------------------------------------------ Q12


async def test_segment_structured_carries_ocr_and_notes(assembled: Assembled) -> None:
    """Q12: get-segment-context printed the on-screen text, the clamp note and
    the OCR-capped line, and structuredContent had none of them (§3.5)."""
    from vidtheque_mcp.tools import segment

    r = await segment.run(assembled.deps, video_id="zduSFxRajkE", t=99999, window=300)
    sc = r.structured_content
    assert sc["ocr"] == []  # clamped to 3600 s, the slide at 12 s is outside
    assert sc["notes"] and all(n in r.content[0].text for n in sc["notes"])
    assert any("clamped" in n for n in sc["notes"])

    await _long_slide(assembled.deps.db)
    r = await segment.run(
        assembled.deps, video_id="zduSFxRajkE", t=12, window=10, max_text_chars=0
    )
    sc = r.structured_content
    assert sc["ocr"] == [
        {
            "frame_id": "zduSFxRajkE-00000",
            "t": 12.0,
            "text": "paged kv cache | block table | 4% fragmentation",
        }
    ]
    assert sc["ocr_capped"] is True
    assert all(n in r.content[0].text for n in sc["notes"])


async def test_job_status_structured_carries_deferral_cancel_and_next(
    assembled: Assembled,
) -> None:
    """Q12: job-status printed the deferral, the cancel request and its
    `next:` line in prose only."""
    from vidtheque_mcp.tools import indexing

    created = await indexing.index_video(assembled.deps, url="https://youtu.be/Qk7mF2xLp0A")
    job_id = created.structured_content["job_id"]
    await assembled.deps.db.write(
        lambda c: c.execute(
            "UPDATE jobs SET not_before = unixepoch() + 600, cancel_requested = 1 "
            "WHERE public_id = ?",
            (job_id,),
        )
    )
    r = await indexing.job_status(assembled.deps, job_id=job_id)
    sc = r.structured_content
    text = r.content[0].text
    assert sc["deferred"] and sc["deferred_until"] and 0 < sc["defer_s"] <= 600
    assert sc["cancel_requested"] is True
    assert sc["notes"] and all(n in text for n in sc["notes"])
    assert sc["next"] == [ln for ln in text.splitlines() if ln.startswith("next:")][-1]


# ------------------------------------------------------------------------- Q5


@pytest.mark.parametrize("fmt", ["text", "tsv"])
async def test_response_cap_binds_every_format_and_says_so(assembled: Assembled, fmt: str) -> None:
    """Q5: the response cap applied to the text format only; TSV and
    structuredContent were uncapped, and a capped text block still said
    `(no more results)` over the dropped tail."""
    import json

    rows = [(1000 + i * 200, 1003 + i * 200, f"zebra item {i} " + "x" * 5000) for i in range(25)]
    await _add_cues(assembled.deps.db, "kCc8FmEb1nY", rows)
    cap = assembled.deps.settings.response_max_chars
    r = await search.run(
        assembled.deps,
        q="zebra",
        limit=50,
        max_per_video=20,
        max_text_chars=0,
        format=fmt,
        fields="video_id,start,text",
    )
    text = r.content[0].text
    sc = r.structured_content
    kept = len(sc["results"])
    assert len(text) <= cap
    assert 0 < kept < 20
    assert sc["truncated"]["dropped"] == 20 - kept
    assert sc["truncated"]["next_offset"] == kept
    assert sc["pagination"]["has_more"] is True
    assert "Response truncated" in text
    assert "no more results" not in text
    assert f"use offset={kept}" in text.splitlines()[0]
    if fmt == "tsv":
        assert len(text.split("\nvideo_id\tstart\ttext\n")[1].splitlines()) >= kept
    # structuredContent holds the same kept results, not the whole page
    assert len(json.dumps(sc["results"])) < 20 * 5000


# ------------------------------------------------------------------------- Q6


async def test_ranking_does_not_move_with_max_text_chars(assembled: Assembled) -> None:
    """Q6: an OCR hit's text was the FTS snippet, or the whole frame at
    max_text_chars=0, and that text fed the OCR/transcript dedup and the match
    scores — so the same query ranked differently at a different display
    budget (§3.6: same query, same order, same scores)."""
    P = " ".join(f"word{i}" for i in range(60)) + " quokka " + " ".join(
        f"word{i}" for i in range(60, 120)
    )
    A = " ".join(f"intro{i}" for i in range(80))
    B = " | ".join(
        f"extra line {i} " + " ".join(f"tail{i}x{j}" for j in range(8)) for i in range(10)
    )
    await _add_cues(assembled.deps.db, "kCc8FmEb1nY", [(498.0, 503.0, A + " " + P)])

    def write(c):
        vid = c.execute("SELECT id FROM videos WHERE public_id='kCc8FmEb1nY'").fetchone()[0]
        cur = c.execute(
            "INSERT INTO keyframes (video_id, ord, t_s, shot_id, shot_start_s, shot_end_s, "
            "phash, sharpness, width, height, jpeg_path, jpeg_bytes, ocr_state) "
            "VALUES (?, 2, 500.0, 7, 500.0, 505.0, 4242, 10.0, 1280, 720, 'x', 1, 'done')",
            (vid,),
        )
        c.execute(
            "INSERT INTO ocr_frames (keyframe_id, video_id, t_s, text) VALUES (?, ?, 500.0, ?)",
            (cur.lastrowid, vid, P + " | " + B),
        )

    await assembled.deps.db.write(write)
    seen = []
    for mtc in (1000, 0):
        sc = (await search.run(assembled.deps, q="quokka", max_text_chars=mtc, limit=10)).structured_content
        seen.append(
            (
                sc["pagination"]["approx_total"],
                [(x["source"], x["start"], x["frame_id"], x["score"]) for x in sc["results"]],
            )
        )
    assert seen[0] == seen[1]
    # and the opt-out still shows an OCR hit's whole frame
    ocr = [
        (await search.run(assembled.deps, q="quokka", content_type="ocr", max_text_chars=mtc))
        .structured_content["results"][0]["text"]
        for mtc in (1000, 0)
    ]
    assert "tail9x7" not in ocr[0] and "tail9x7" in ocr[1]


# ------------------------------------------------------------------------- Q8


async def test_corpus_summary_filters_scope_the_whole_rollup(assembled: Assembled) -> None:
    """Q8: channel=/tags=/published_* scoped the channel, tag and recent
    sections only; the headline, the published span and the gaps stayed
    corpus-wide, and a filter that matched nothing said nothing."""
    from vidtheque_mcp.tools import library

    whole = (await library.corpus_summary(assembled.deps)).structured_content
    assert whole["videos"] == 3 and whole["gaps"]["transcript_no_ocr"] == 1

    r = await library.corpus_summary(assembled.deps, channel="GPU MODE")
    sc = r.structured_content
    assert sc["videos"] == 1
    assert sc["published_span"]["oldest"] == sc["published_span"]["newest"] == "2024-02-20"
    assert sc["gaps"]["transcript_no_ocr"] == 0
    assert r.content[0].text.startswith("Corpus: 1 videos")
    assert any("channel=GPU MODE" in n for n in sc["notes"])

    r = await library.corpus_summary(assembled.deps, channel="no-such-channel")
    sc = r.structured_content
    assert sc["videos"] == 0
    assert any("no video matches" in n for n in sc["notes"])
    assert "index-video" not in sc["next"]
    assert all(n in r.content[0].text for n in sc["notes"])
