"""Two `note:`/footer honesty fixes from the design bench (F6, F10).

Both are the same defect in different clothes: the payload said something that
was not true of *this* search. The cap footer named a video the cap had not
truncated, and a search the slides answered and the speech did not said nothing
about the difference.
"""

from __future__ import annotations

from mcp_types import TextContent

from vidtheque_mcp.app import Assembled
from vidtheque_mcp.tools import search


def body(result) -> str:
    return "\n".join(b.text for b in result.content if isinstance(b, TextContent))


# ------------------------------------------------------- F10: the cap footer


async def test_the_cap_footer_names_the_video_the_cap_actually_truncated(
    assembled: Assembled,
) -> None:
    """`n >= max_per_video` is "reached"; the footer's claim is "bound".

    At `max_per_video=2` the fixture gives kCc8FmEb1nY exactly two hits (it
    lost nothing) and zduSFxRajkE three (it lost one). The footer used to take
    the first video that *reached* the cap — kCc8FmEb1nY, in page order — and
    tell the caller to raise the cap "for more from it", of which there was
    none.
    """
    text = body(await search.run(assembled.deps, q="cache", limit=10, max_per_video=2))
    assert "max_per_video=2 bound" in text
    assert "came from zduSFxRajkE" in text
    assert "came from kCc8FmEb1nY" not in text


async def test_a_cap_that_dropped_nothing_says_nothing(assembled: Assembled) -> None:
    """The F10 case: on a corpus this small the cap is reached on every search."""
    text = body(await search.run(assembled.deps, q="cache", limit=10, max_per_video=20))
    assert "Raise max_per_video" not in text
    assert "bound)" not in text


async def test_the_footer_still_fires_when_the_cap_really_binds(
    assembled: Assembled,
) -> None:
    text = body(await search.run(assembled.deps, q="cache", limit=10, max_per_video=1))
    assert "Raise max_per_video for more from it." in text


# ------------------------------------------- F6: the slides answered, not the speech


async def test_a_term_only_on_a_slide_says_the_speech_did_not_carry_it(
    assembled: Assembled,
) -> None:
    """`nvidia-smi` is on a keyframe and in no cue — the identifier/speech split."""
    text = body(await search.run(assembled.deps, q="nvidia-smi", limit=10))
    assert "transcript 0 segments" in text
    assert "note: 0 transcript hits, but the on-screen text matched." in text
    assert "try the spoken phrasing" in text


async def test_the_note_is_absent_when_the_transcript_did_match(
    assembled: Assembled,
) -> None:
    text = body(await search.run(assembled.deps, q="cache", limit=10))
    assert "0 transcript hits" not in text


async def test_the_two_zero_hit_notes_are_mutually_exclusive(
    assembled: Assembled,
) -> None:
    """Title-footing needs *no* lexical footing; F6 needs OCR to have supplied it.

    A word in no cue, no slide and no title gets neither.
    """
    slide_only = body(await search.run(assembled.deps, q="nvidia-smi", limit=10))
    assert "note: 0 transcript hits" in slide_only
    assert "video title" not in slide_only

    nowhere = body(await search.run(assembled.deps, q="zzzznotacorpusword", limit=10))
    assert "note: 0 transcript hits, but the on-screen text matched." not in nowhere
