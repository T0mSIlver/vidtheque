"""`follow-channel`: the five verbs, the refusals, and what the payload promises.

Nothing here reaches the network, and that is the property under test as much
as a fixture constraint: `action="follow"` must be a database row and a name
read off the URL, so the channel below can be one that does not exist and the
call still has to succeed.
"""

from __future__ import annotations

import pytest
from mcp_types import TextContent

from vidtheque_mcp.app import Assembled
from vidtheque_mcp.public.readonly import WRITE_TOOLS, hidden_tools
from vidtheque_mcp.tools import follows as follows_tool
from vidtheque_mcp.tools import library

CHANNEL = "https://www.youtube.com/@karpathy"
PLAYLIST = "https://www.youtube.com/playlist?list=PLAqhIrjkxbuWI23v9cThsA9GvCAUhRvKZ"


def body(result) -> str:
    return "\n".join(b.text for b in result.content if isinstance(b, TextContent))


def structured(result) -> dict:
    assert result.structured_content is not None
    return result.structured_content


async def follow(deps, **kwargs):
    return await follows_tool.follow_channel(deps, **kwargs)


async def count_follows(deps) -> int:
    row = await deps.db.read(
        lambda c: c.execute("SELECT COUNT(*) AS n FROM follows").fetchone()
    )
    return int(row["n"])


# ------------------------------------------------------------- the round trip


async def test_follow_pause_resume_check_now_unfollow(assembled: Assembled) -> None:
    deps = assembled.deps
    created = await follow(deps, url=CHANNEL, min_duration="8:00", max_per_check=3)
    text = body(created)
    assert "Following: @karpathy (channel)" in text
    assert "taken from the URL" in text  # no probe ran, and the payload says so
    assert "take up to 3 new uploads from @karpathy on /videos" in text
    assert "longer than 0:08:00" in text  # `8:00` parsed on the offset axis
    assert "Budget:" in text
    assert text.rstrip().splitlines()[-1].startswith("next:")
    payload = structured(created)
    assert payload["follow"]["kind"] == "channel"
    assert payload["follow"]["state"] == "active"
    assert payload["already_following"] is False
    slug = payload["follow"]["slug"]

    paused = await follow(deps, url=slug, action="pause")
    assert "Paused: @karpathy" in body(paused)
    assert structured(paused)["follow"]["state"] == "paused"

    # A paused follow is not checked, and check_now must not claim otherwise.
    held = await follow(deps, url=slug, action="check_now")
    assert "Not scheduled" in body(held)
    assert structured(held)["scheduled"] is False

    resumed = await follow(deps, url="karpathy", action="resume")  # by name substring
    assert "Resumed: @karpathy" in body(resumed)
    assert structured(resumed)["follow"]["state"] == "active"

    due = await follow(deps, url=CHANNEL, action="check_now")  # by stored URL
    assert "Due now" in body(due)
    assert "has not run yet" in body(due)
    assert structured(due)["scheduled"] is True

    gone = await follow(deps, url=slug, action="unfollow")
    assert "Unfollowed: @karpathy" in body(gone)
    assert "stay in the corpus" in body(gone)
    assert structured(gone)["unfollowed"] is True
    assert await count_follows(deps) == 0


async def test_unfollowing_keeps_the_videos_it_brought_in(assembled: Assembled) -> None:
    """The one promise the payload makes about deletion, asserted on the corpus."""
    deps = assembled.deps
    await follow(deps, url=CHANNEL)
    before = await deps.db.read(
        lambda c: c.execute("SELECT COUNT(*) AS n FROM videos").fetchone()["n"]
    )
    await follow(deps, url=CHANNEL, action="unfollow")
    after = await deps.db.read(
        lambda c: c.execute("SELECT COUNT(*) AS n FROM videos").fetchone()["n"]
    )
    assert after == before


async def test_a_playlist_url_is_followed_as_a_playlist(assembled: Assembled) -> None:
    result = await follow(assembled.deps, url=PLAYLIST)
    assert structured(result)["follow"]["kind"] == "playlist"
    assert "playlist PLAqhIrjkxbuWI23v9cThsA9GvCAUhRvKZ" in body(result)


async def test_the_tab_suffix_is_not_a_second_channel(assembled: Assembled) -> None:
    """`@handle` and `@handle/videos` are one channel; which tabs is a rule."""
    deps = assembled.deps
    first = await follow(deps, url=CHANNEL)
    again = await follow(deps, url=f"{CHANNEL}/videos", tabs="videos,shorts")
    assert structured(again)["already_following"] is True
    assert structured(again)["follow"]["slug"] == structured(first)["follow"]["slug"]
    assert await count_follows(deps) == 1


# ----------------------------------------------------------------- idempotence


async def test_following_twice_returns_the_first_follow(assembled: Assembled) -> None:
    deps = assembled.deps
    first = await follow(deps, url=CHANNEL, max_per_check=3)
    second = await follow(deps, url=CHANNEL, max_per_check=9)
    text = body(second)
    assert "Already following: @karpathy" in text
    assert "No second follow was created" in text
    # The stored rule, not the one this call passed — and it says so.
    assert "take up to 3 new uploads" in text
    assert structured(second)["already_following"] is True
    assert structured(second)["follow"]["slug"] == structured(first)["follow"]["slug"]
    assert await count_follows(deps) == 1


# ------------------------------------------------------------------- refusals


async def test_a_bare_video_url_is_sent_to_index_video(assembled: Assembled) -> None:
    result = await follow(assembled.deps, url="https://youtu.be/kCc8FmEb1nY")
    payload = structured(result)
    assert result.is_error
    assert payload["code"] == "E_BAD_PARAM"
    assert "single video" in payload["message"]
    assert "index-video" in payload["next"]
    assert await count_follows(assembled.deps) == 0


async def test_a_watch_url_with_a_list_is_still_a_video(assembled: Assembled) -> None:
    """`noplaylist` picks the video out of it, which is what the pasted link means."""
    result = await follow(
        assembled.deps, url="https://www.youtube.com/watch?v=kCc8FmEb1nY&list=PL123"
    )
    assert structured(result)["code"] == "E_BAD_PARAM"


async def test_a_non_youtube_host_is_refused_before_anything_is_stored(
    assembled: Assembled,
) -> None:
    result = await follow(assembled.deps, url="http://169.254.169.254/latest/meta-data/")
    assert structured(result)["code"] == "E_UNSUPPORTED_SOURCE"
    assert await count_follows(assembled.deps) == 0


async def test_a_url_too_long_to_be_a_channel_is_refused_before_it_is_stored(
    assembled: Assembled,
) -> None:
    """A follow's URL is stored forever and echoed in every payload that names it.

    `is_indexable_url` checks the host and `looks_like_container` looks for a
    marker, so a hundred kilobytes of padding after a real prefix passed both.
    Refused rather than truncated: a truncated URL is a different URL, and this
    one is the key the follow is found by.
    """
    padded = CHANNEL + "a" * 100_000
    result = await follow(assembled.deps, url=padded)
    payload = structured(result)
    assert payload["code"] == "E_BAD_PARAM"
    assert "2048" in payload["message"]
    assert len(body(result)) < 2_000  # and the refusal does not echo it back
    assert await count_follows(assembled.deps) == 0


async def test_a_playlist_cannot_be_given_tabs_it_does_not_have(
    assembled: Assembled,
) -> None:
    """A filter that cannot apply prints a `note:` — it never silently narrows.

    Tabs are a channel's /videos, /streams and /shorts. A playlist is listed
    whole and its candidates are tagged `videos`, so `tabs="streams"` on one
    produced a follow that rejected every candidate as `skipped_tab` forever —
    structurally dead, and discoverable only by reading the ledger.
    """
    result = await follow(assembled.deps, url=PLAYLIST, tabs="streams")
    text = body(result)
    assert "note: tabs are a channel's" in text
    assert "/streams was not applied" in text
    assert structured(result)["follow"]["tabs"] == "videos"


async def test_check_now_on_a_failing_follow_does_not_promise_a_check(
    assembled: Assembled,
) -> None:
    """The scheduler enqueues active follows only, so anything else must say so."""
    deps = assembled.deps
    created = await follow(deps, url=CHANNEL)
    slug = structured(created)["follow"]["slug"]
    await deps.db.write(
        lambda c: c.execute(
            "UPDATE follows SET state = 'failing', last_error_message = 'channel is gone' "
            "WHERE collection_id = (SELECT id FROM collections WHERE slug = ?)",
            (slug,),
        )
    )

    result = await follow(deps, url=CHANNEL, action="check_now")
    text = body(result)
    assert "Not scheduled" in text and "failing" in text
    assert "channel is gone" in text  # and it says what went wrong
    assert structured(result)["scheduled"] is False
    assert "resume" in text


async def test_an_unknown_follow_names_what_was_tried(assembled: Assembled) -> None:
    result = await follow(assembled.deps, url="nobody-follows-this", action="pause")
    payload = structured(result)
    assert payload["code"] == "E_UNKNOWN_FOLLOW"
    assert "slug" in payload["message"] and "name" in payload["message"]


async def test_url_is_required_for_every_action(assembled: Assembled) -> None:
    result = await follow(assembled.deps, action="check_now")
    assert structured(result)["code"] == "E_BAD_PARAM"


async def test_an_unknown_action_names_the_five(assembled: Assembled) -> None:
    result = await follow(assembled.deps, url=CHANNEL, action="subscribe")
    payload = structured(result)
    assert payload["code"] == "E_BAD_PARAM"
    assert "check_now" in payload["message"]


@pytest.mark.parametrize(
    ("kwargs", "fragment"),
    [
        ({"tabs": "podcasts"}, "tabs must be a subset"),
        ({"min_duration": "20:00", "max_duration": "8:00"}, "nothing could ever match"),
        ({"tags": "notanamespace"}, "namespace"),
        ({"check_interval_s": 60}, "at least 900 seconds"),
        ({"mode": "whenever"}, "mode must be one of"),
        ({"channels": "subtitles"}, "channels must be"),
    ],
)
async def test_the_rule_is_validated_before_a_row_exists(
    assembled: Assembled, kwargs: dict, fragment: str
) -> None:
    result = await follow(assembled.deps, url=CHANNEL, **kwargs)
    assert result.is_error
    assert fragment in structured(result)["message"]
    assert await count_follows(assembled.deps) == 0


async def test_state_actions_never_silently_drop_a_rule_argument(
    assembled: Assembled,
) -> None:
    deps = assembled.deps
    await follow(deps, url=CHANNEL)
    result = await follow(deps, url=CHANNEL, action="pause", max_per_check=20)
    assert "note: max_per_check was ignored" in body(result)


async def test_following_is_refused_when_writes_are_disabled(
    assembled: Assembled,
) -> None:
    deps = assembled.deps
    deps.db.writes_allowed = False
    try:
        result = await follow(deps, url=CHANNEL)
        assert structured(result)["code"] == "E_FEATURE_DISABLED"
    finally:
        deps.db.writes_allowed = True


# ------------------------------------------------------ budget and discipline


async def test_the_budget_line_reads_the_pipeline_ceiling(
    assembled: Assembled, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("VIDTHEQUE_FOLLOW_DAILY_HOURS", "3")
    result = await follow(assembled.deps, url=CHANNEL)
    assert "of 3.0h accepted in the last 24h" in body(result)
    assert structured(result)["budget"]["daily_hours"] == 3.0


async def test_the_payload_is_the_same_size_whatever_the_input(
    assembled: Assembled,
) -> None:
    """Token discipline: no argument the caller controls can grow the response."""
    small = await follow(assembled.deps, url=CHANNEL)
    await follow(assembled.deps, url=CHANNEL, action="unfollow")
    big = await follow(
        assembled.deps,
        url=CHANNEL,
        title_include=",".join(f"term{i}" for i in range(30)),
        title_exclude=",".join(f"nope{i}" for i in range(30)),
        tags=",".join(f"topic:t{i}" for i in range(30)),
    )
    # `split_csv` caps every list at ten before it is ever stored or echoed.
    assert structured(big)["code"] == "E_BAD_PARAM"
    assert len(body(small)) < 2000


# --------------------------------------------------------- corpus-summary view


async def test_corpus_summary_says_nothing_about_follows_by_default(
    assembled: Assembled,
) -> None:
    await follow(assembled.deps, url=CHANNEL)
    result = await library.corpus_summary(assembled.deps)
    assert "Following" not in body(result)
    assert "follows" not in structured(result)


async def test_corpus_summary_include_follows_answers_is_this_covered(
    assembled: Assembled,
) -> None:
    deps = assembled.deps
    await follow(deps, url=CHANNEL, min_duration="8:00", tags="topic:llm")
    result = await library.corpus_summary(deps, include_follows=True)
    text = body(result)
    assert "Following (1: 1 active) · 0 video(s) brought in:" in text
    assert "@karpathy" in text
    assert "every 6h · /videos · ≤5/check" in text
    assert "last check never" in text
    payload = structured(result)["follows"]
    assert payload["total"] == 1
    assert payload["items"][0]["brought_in"] == 0
    assert payload["has_more"] is False


async def test_corpus_summary_caps_the_following_section_at_ten(
    assembled: Assembled,
) -> None:
    deps = assembled.deps
    for i in range(12):
        await follow(deps, url=f"https://www.youtube.com/@channel{i}")
    text = body(await library.corpus_summary(deps, include_follows=True))
    assert "… and 2 more" in text
    assert text.count("@channel") == 10
    assert structured(await library.corpus_summary(deps, include_follows=True))["follows"][
        "has_more"
    ]


async def test_corpus_summary_says_so_when_nothing_is_followed(
    assembled: Assembled,
) -> None:
    text = body(await library.corpus_summary(assembled.deps, include_follows=True))
    assert "Following: none" in text


# ------------------------------------------------------------ the demo surface


def test_follow_channel_is_masked_on_a_read_only_deployment() -> None:
    """It writes, so `readOnlyHint: False` puts it in the derived mask."""
    assert "follow-channel" in WRITE_TOOLS
    assert "follow-channel" in hidden_tools(True)
    assert "follow-channel" not in hidden_tools(False)


async def test_a_read_only_server_does_not_say_what_it_follows(
    assembled: Assembled,
) -> None:
    deps = assembled.deps
    await follow(deps, url=CHANNEL)
    deps.hidden_tools = hidden_tools(True)
    try:
        result = await library.corpus_summary(deps, include_follows=True)
        assert "include_follows was not applied" in body(result)
        assert "@karpathy" not in body(result)
        assert "follows" not in structured(result)
    finally:
        deps.hidden_tools = frozenset()
