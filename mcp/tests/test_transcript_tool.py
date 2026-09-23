"""`get-transcript` — the paged bulk reader (tool-surface §4.11).

The behaviour worth pinning is the pager, because the tool's whole reason to
exist is that a caller can walk a talk to its end without guessing: every page
has to advance by what it printed, say whether more is coming, and never hand
back an offset that skips a cue.
"""

from __future__ import annotations

import pytest

from vidtheque_mcp.tools import transcript

VIDEO = "kCc8FmEb1nY"  # six cues, the last 400 s after the fifth
SHORT = "zduSFxRajkE"  # three cues


@pytest.fixture
async def deps(assembled):
    return assembled.deps


async def read(deps, **kwargs):
    result = await transcript.run(deps, **kwargs)
    return result.content[0].text, result.structured_content


async def test_it_returns_every_cue_in_order_with_a_citable_stamp(deps) -> None:
    text, payload = await read(deps, video_id=VIDEO)

    assert [c["text"] for c in payload["cues"]] == [
        "we cache the keys and the values at every new token",
        "otherwise you would recompute attention over the entire prefix",
        "which is quadratic in the sequence length",
        "the cache makes it linear in the number of new tokens",
        "and the price you pay for that is memory",
        "much later we talk about tokenization instead",
    ]
    # The base URL once, a `?t=` per line: §3.6's compact form, so the stamp
    # costs ~2% of the line rather than a third of the page.
    assert f"https://youtu.be/{VIDEO}" in text
    assert "[7:00 ?t=418] much later we talk about tokenization instead" in text
    assert text.count("https://youtu.be/") == 2  # the header link and the cite line
    assert payload["pagination"]["has_more"] is False
    assert payload["pagination"]["next_offset"] is None
    assert "that is the end of the transcript" in payload["next"]


async def test_the_pager_walks_to_the_end_without_skipping_a_cue(deps) -> None:
    seen: list[str] = []
    offset, pages = 0, 0
    while True:
        _, payload = await read(deps, video_id=VIDEO, limit=2, offset=offset)
        seen.extend(c["text"] for c in payload["cues"])
        pages += 1
        if not payload["pagination"]["has_more"]:
            break
        offset = payload["pagination"]["next_offset"]
        assert pages < 10, "the pager did not terminate"

    _, whole = await read(deps, video_id=VIDEO)
    assert seen == [c["text"] for c in whole["cues"]]
    assert pages == 3


async def test_the_char_budget_binds_before_limit_and_the_offset_follows_it(deps) -> None:
    """The bug this exists to prevent: `offset + limit` past a short page.

    With `limit=6` and a budget that affords two cues, advancing by the limit
    would resume at cue 7 — four lines the caller never saw and cannot know
    are missing.
    """
    _, first = await read(deps, video_id=VIDEO, limit=6, max_text_chars=200)
    shown = first["pagination"]["shown"]

    assert first["binding_cap"] == "max_text_chars"
    assert shown < 6
    assert first["pagination"]["has_more"] is True
    assert first["pagination"]["next_offset"] == shown

    _, second = await read(
        deps, video_id=VIDEO, limit=6, offset=first["pagination"]["next_offset"]
    )
    _, whole = await read(deps, video_id=VIDEO)
    walked = [c["text"] for c in first["cues"]] + [c["text"] for c in second["cues"]]
    assert walked == [c["text"] for c in whole["cues"]]


async def test_a_budget_too_small_for_one_cue_still_advances(deps) -> None:
    """Otherwise the next offset equals this one and the caller loops forever."""
    _, payload = await read(deps, video_id=VIDEO, max_text_chars=1)
    # 1 clamps *up* to the floor, and the floor still affords several cues here,
    # so force the degenerate case with a real one-cue budget.
    assert payload["pagination"]["shown"] >= 1

    fitted = transcript._fit([{"text": "x" * 400}], 20)
    assert fitted == (1, 400, "max_text_chars")


async def test_the_page_footer_says_where_the_next_one_starts(deps) -> None:
    text, payload = await read(deps, video_id=VIDEO, limit=2)
    assert "Cues: 2/6 (use offset=2 for more)" in text
    assert "offset=2 continues this transcript" in payload["next"]


async def test_the_next_hint_repeats_the_span_its_offset_counts_in(deps) -> None:
    _, payload = await read(deps, video_id=VIDEO, t_start=5, t_end=10, limit=2)
    assert "offset=2 t_start=5 t_end=10 continues this transcript" in payload["next"]
    # Truncating 5.9 to 5 would pull in the cue ending at 5.8 and shift the offset.
    _, payload = await read(deps, video_id=VIDEO, t_start=5.9, t_end=10, limit=1)
    assert "offset=1 t_start=5.9 t_end=10 continues" in payload["next"]


async def test_the_span_selects_by_overlap_not_by_start(deps) -> None:
    """A cue running 3.0-5.8 is part of `t_start=5`, not a line to page back for."""
    _, payload = await read(deps, video_id=VIDEO, t_start=5, t_end=10)
    starts = [c["start"] for c in payload["cues"]]
    assert starts == [3.0, 6.0, 9.0]


async def test_the_span_accepts_a_clock_and_rejects_a_reversed_one(deps) -> None:
    _, payload = await read(deps, video_id=VIDEO, t_start="0:00", t_end="0:09")
    assert [c["start"] for c in payload["cues"]] == [0.0, 3.0, 6.0, 9.0]

    result = await transcript.run(deps, video_id=VIDEO, t_start=100, t_end=10)
    assert result.structured_content["code"] == "E_BAD_PARAM"
    assert "before t_start" in result.structured_content["message"]


async def test_a_span_with_no_cues_says_so_rather_than_printing_a_heading(deps) -> None:
    text, payload = await read(deps, video_id=VIDEO, t_start=6000, t_end=6100)
    assert payload["cues"] == []
    assert "No transcript cues" in text
    assert "TRANSCRIPT" not in text


async def test_tsv_carries_the_cite_column_and_costs_less(deps) -> None:
    tsv_text, _ = await read(deps, video_id=VIDEO, format="tsv")
    plain, _ = await read(deps, video_id=VIDEO)
    assert tsv_text.splitlines()[4].startswith("t\t")
    assert "?t=<the t column>" in tsv_text
    assert len(tsv_text) < len(plain)


async def test_an_unknown_format_is_a_typed_error(deps) -> None:
    result = await transcript.run(deps, video_id=VIDEO, format="json")
    assert result.structured_content["code"] == "E_BAD_PARAM"
    assert "text, tsv" in result.structured_content["message"]


async def test_an_unknown_video_names_itself(deps) -> None:
    result = await transcript.run(deps, video_id="nope")
    assert result.structured_content["code"] == "E_UNKNOWN_VIDEO"


async def test_the_caps_are_clamped_server_side_not_honoured(deps) -> None:
    _, payload = await read(deps, video_id=VIDEO, limit=99_999, max_text_chars=10_000_000)
    assert payload["pagination"]["limit"] == transcript.MAX_CUES

    _, floor = await read(deps, video_id=SHORT, limit=0)
    assert floor["pagination"]["limit"] == 1


async def test_the_approximate_total_does_not_move_with_the_page_size(deps) -> None:
    """terra eval §4.12, in the shape that fits a cue pager."""
    _, small = await read(deps, video_id=VIDEO, limit=1)
    _, big = await read(deps, video_id=VIDEO, limit=500)
    assert small["pagination"]["approx_total"] == big["pagination"]["approx_total"] == 6
    assert small["pagination"]["approx_total_is_floor"] is False
