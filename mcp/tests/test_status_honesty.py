"""The status/copy honesty cluster: one story per fact, on every surface.

`research/demo-queries-2026-08-09.md` §9 is the field test these come from, and
the tourist's summary is the standard: *"small internal contradictions erode
trust in every other number the server prints."* Four contradictions, one test
section each:

- §9.1.4 three answers to "is this corpus indexing?" in one session;
- §9.1.8 every dead end in the read-only demo pointing at a masked write tool;
- §9.1.9 tags advertised as columns and populated nowhere;
- §9.1.9 `format=tsv` accepting a field name that does not exist.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from mcp_types import TextContent

from vidtheque_mcp.app import Assembled, assemble
from vidtheque_mcp.config import Settings
from vidtheque_mcp.jobs.runner import NotImplementedPipeline
from vidtheque_mcp.public.settings import PublicSettings
from vidtheque_mcp.tools import corpus_state, indexing, library, resources, search

from .conftest import FakeEmbeddings


def body(result) -> str:
    return "\n".join(b.text for b in result.content if isinstance(b, TextContent))


def structured(result) -> dict:
    assert result.structured_content is not None
    return result.structured_content


@pytest.fixture
async def readonly(seeded: Settings, fake_embeddings: FakeEmbeddings):
    """The demo deployment: the write tools are never registered."""
    parts: Assembled = assemble(
        seeded,
        embeddings=fake_embeddings,
        run_pipeline=False,
        pipeline=NotImplementedPipeline(),
        public=PublicSettings(enabled=True),
    )
    await parts.db.open()
    try:
        yield parts
    finally:
        await parts.db.close()
        parts.auth.close()


async def queue_one_job(assembled: Assembled, defer_s: int | None = None) -> str:
    """Create a real job, optionally deferred into the future like the demo's five."""
    created = await indexing.index_video(assembled.deps, url="https://youtu.be/Qk7mF2xLp0A")
    job_id = structured(created)["job_id"]
    if defer_s is not None:
        await assembled.deps.db.write(
            lambda c: c.execute(
                "UPDATE jobs SET not_before = unixepoch() + ? WHERE public_id = ?",
                (defer_s, job_id),
            )
        )
    return job_id


async def three_surfaces(assembled: Assembled) -> tuple[str, dict, str]:
    """`corpus-summary`, `vidtheque://context` and `search`'s empty state."""
    summary = await library.corpus_summary(assembled.deps)
    context = json.loads(await resources.context_resource(assembled.deps))
    empty = await search.run(assembled.deps, q="cache", channel="nobody-by-that-name")
    return body(summary), context, body(empty)


# ------------------------------------------- §9.1.4 one story about indexing


async def test_a_deferred_queue_is_not_indexing(assembled: Assembled) -> None:
    """Five queued-but-deferred jobs made the first call of every session lie."""
    await queue_one_job(assembled, defer_s=3600)

    summary_text, context, empty_text = await three_surfaces(assembled)

    assert "data_status: deferred" in summary_text
    assert "nothing running" in summary_text
    assert context["corpus"]["data_status"] == "deferred"
    assert context["corpus"]["active_jobs"] == 1
    assert context["corpus"]["running_jobs"] == 0
    assert context["corpus"]["deferred_jobs"] == 1
    assert context["corpus"]["deferred_until"], "the time the queue resumes"
    assert "data_status: deferred" in empty_text
    assert "index fresh" not in empty_text, "the third contradicting answer"
    assert "deferred" in empty_text


async def test_a_ready_queue_is_indexing_on_all_three_surfaces(assembled: Assembled) -> None:
    await queue_one_job(assembled)

    summary_text, context, empty_text = await three_surfaces(assembled)

    assert "data_status: indexing" in summary_text
    assert context["corpus"]["data_status"] == "indexing"
    assert context["corpus"]["deferred_jobs"] == 0
    assert "data_status: indexing" in empty_text


async def test_an_idle_queue_says_so_everywhere(assembled: Assembled) -> None:
    """The fixture is `partial` (one video has no OCR) and *not* indexing."""
    summary_text, context, empty_text = await three_surfaces(assembled)
    assert "data_status: partial" in summary_text
    assert context["corpus"]["data_status"] == "partial"
    assert context["corpus"]["active_jobs"] == 0
    # `search`'s empty state answers the activity axis only, so a corpus with a
    # coverage gap and an idle queue reads `ok` there — never `indexing`.
    assert "data_status: ok" in empty_text
    assert "index fresh" in empty_text


async def test_the_gaps_block_gives_each_counter_its_own_name(assembled: Assembled) -> None:
    """Jobs count rows in `jobs`; `indexing` counts videos. Never the same line."""
    await queue_one_job(assembled, defer_s=600)
    text = body(await library.corpus_summary(assembled.deps))

    assert "0 video(s) mid-pipeline" in text
    assert "1 indexing job(s)" in text
    payload = structured(await library.corpus_summary(assembled.deps))
    assert payload["gaps"]["indexing"] == 0
    assert payload["gaps"]["jobs_active"] == 1
    assert payload["gaps"]["jobs_deferred"] == 1
    assert payload["gaps"]["jobs_running"] == 0


async def make_one_video_unqueryable(assembled: Assembled, state: str = "indexing") -> None:
    await assembled.db.write(
        lambda c: c.execute(
            "UPDATE videos SET index_state = ? WHERE source_id = 'eMlx5fFNoYc'", (state,)
        )
    )


async def test_the_two_video_counters_reconcile_themselves(assembled: Assembled) -> None:
    """terra eval §4.7: 154 from corpus-summary, 152 from list-videos, in one minute.

    `corpus-summary` counts every row, `list-videos` counts the queryable ones
    (§4.2, deliberately) — and neither payload mentioned the other, so the
    consumer that was asked for the exact count invented the reconciliation and
    got it wrong.
    """
    await make_one_video_unqueryable(assembled)

    summary = await library.corpus_summary(assembled.deps)
    text = body(summary)
    assert "Corpus: 3 videos (2 queryable · 1 still being indexed)" in text
    payload = structured(summary)
    assert payload["videos"] == 3
    assert payload["queryable_videos"] == 2
    assert payload["videos_by_index_state"]["indexing"] == 1

    listed = body(await library.list_videos(assembled.deps, limit=50))
    assert "Videos: 2/2" in listed
    assert (
        "note: 2 of the 3 videos in this corpus are queryable and can appear here; "
        "1 still being indexed (index_state=indexing) cannot. corpus-summary counts all 3."
    ) in listed


async def test_the_counters_say_nothing_when_they_agree(assembled: Assembled) -> None:
    """No note, no parenthetical, on the corpus where the two counts are one."""
    text = body(await library.corpus_summary(assembled.deps))
    assert "Corpus: 3 videos · " in text
    assert "queryable" not in text
    listed = body(await library.list_videos(assembled.deps, limit=50))
    assert "queryable" not in listed


async def test_the_reconciling_note_is_for_the_default_view_only(
    assembled: Assembled,
) -> None:
    """`index_state=all` is the dashboard's view: nothing is being withheld."""
    await make_one_video_unqueryable(assembled, "failed")
    listed = body(await library.list_videos(assembled.deps, index_state="all", limit=50))
    assert "queryable" not in listed
    default = body(await library.list_videos(assembled.deps, limit=50))
    assert "1 failed to index (index_state=failed) cannot" in default


async def test_job_status_says_a_deferred_job_is_waiting_not_working(
    assembled: Assembled,
) -> None:
    job_id = await queue_one_job(assembled, defer_s=1800)
    text = body(await indexing.job_status(assembled.deps, job_id=job_id))
    assert "deferred until" in text
    listed = body(await indexing.job_status(assembled.deps))
    assert "deferred" in listed


async def test_queue_state_stops_splitting_a_queue_deeper_than_the_page(
    assembled: Assembled,
) -> None:
    """Beyond the cap we print the count, never a split we did not read."""
    state = corpus_state.QueueState(
        active=corpus_state.QUEUE_PAGE_CAP + 1,
        running=0,
        deferred=0,
        deferred_until=None,
        videos_indexing=0,
        truncated=True,
    )
    assert state.working
    assert "queued or running" in (state.phrase() or "")


# ------------------------------- §9.1.8 hints that name a tool you cannot call


async def test_the_guide_does_not_teach_index_video_when_it_is_masked(
    readonly: Assembled, assembled: Assembled
) -> None:
    demo = resources.guide(readonly.deps)
    assert "index-video" not in demo
    assert "read-only" in demo
    assert "index-video" in resources.guide(assembled.deps)


async def test_unknown_video_stops_recommending_a_masked_index_video(
    readonly: Assembled,
) -> None:
    result = await library.video_summary(readonly.deps, video_id="notarealid1")
    payload = structured(result)
    assert payload["code"] == "E_UNKNOWN_VIDEO"
    assert "index-video" not in payload["next"]
    assert "list-videos" in payload["next"]


async def test_job_status_stops_recommending_a_masked_index_video(
    readonly: Assembled,
) -> None:
    listed = body(await indexing.job_status(readonly.deps))
    assert "index-video" not in listed


async def test_list_videos_coverage_footer_degrades_when_masked(
    readonly: Assembled,
) -> None:
    text = body(await library.list_videos(readonly.deps, limit=20))
    assert "incomplete coverage" in text, "the fixture has a video with no frames"
    assert "index-video" not in text


async def test_the_empty_search_does_not_offer_to_index_when_masked(
    readonly: Assembled,
) -> None:
    text = body(await search.run(readonly.deps, q="cache", channel="nobody-by-that-name"))
    assert "index-video" not in text


async def test_a_writable_server_still_recommends_index_video(assembled: Assembled) -> None:
    payload = structured(await library.video_summary(assembled.deps, video_id="notarealid1"))
    assert "index-video" in payload["next"]


# --------------------------------------- §9.1.9 tags advertised but populated


async def untag(assembled: Assembled) -> None:
    await assembled.deps.db.write(lambda c: c.execute("DELETE FROM video_tags"))


async def test_an_untagged_corpus_stops_printing_tag_columns(readonly: Assembled) -> None:
    await untag(readonly)

    summary = body(await library.corpus_summary(readonly.deps))
    assert "Tags (top" not in summary, "there is nothing to show and it said so in a header"

    corpus = await resources.corpus_resource(readonly.deps)
    assert "\ttags" not in corpus.splitlines()[1]

    context = json.loads(await resources.context_resource(readonly.deps))
    assert "tag_namespaces" not in context, "a read-only corpus with no tags cannot use them"


async def test_the_tag_surfaces_return_the_moment_a_video_is_tagged(
    assembled: Assembled,
) -> None:
    summary = body(await library.corpus_summary(assembled.deps))
    assert "Tags (top" in summary
    corpus = await resources.corpus_resource(assembled.deps)
    assert "\ttags" in corpus.splitlines()[1]
    context = json.loads(await resources.context_resource(assembled.deps))
    assert "topic" in context["tag_namespaces"]


async def test_a_writable_server_keeps_the_namespaces_for_tag_video(
    assembled: Assembled,
) -> None:
    """`tag-video` is registered here, so the namespaces are how you write one."""
    await untag(assembled)
    context = json.loads(await resources.context_resource(assembled.deps))
    assert context["tag_namespaces"], "you can still create tags on this server"


async def test_an_explicitly_requested_tags_column_is_never_dropped(
    assembled: Assembled,
) -> None:
    await untag(assembled)
    text = body(
        await library.list_videos(assembled.deps, fields="video_id,tags", limit=5)
    )
    assert "video_id\ttags" in text


# ------------------------------------------- §9.1.9 tsv with a bogus field name


async def test_search_rejects_an_unknown_tsv_field(assembled: Assembled) -> None:
    result = await search.run(
        assembled.deps, q="cache", format="tsv", fields="nonexistent_field,video_id"
    )
    payload = structured(result)
    assert payload["code"] == "E_BAD_PARAM"
    assert "nonexistent_field" in payload["message"]
    assert "video_id" in payload["next"], "the error names the valid fields"


def test_the_advertised_tsv_fields_are_the_ones_search_can_emit() -> None:
    """The error names valid fields, so the list has to *be* the valid fields."""
    from vidtheque_mcp.tools.search import Hit, _as_dict

    hit = Hit(
        source="transcript",
        video_id=1,
        public_id="kCc8FmEb1nY",
        title="t",
        channel=None,
        published_at=None,
        start_s=0.0,
        end_s=1.0,
        text="",
        score=0.0,
        cue_ids=[],
    )

    class _Settings:
        deeplink_lead_s = 2

    class _Deps:
        settings = _Settings()

    assert set(_as_dict(_Deps(), hit, 0)) == set(search.TSV_FIELDS)


async def test_list_videos_rejects_an_unknown_field_past_the_twelfth(
    assembled: Assembled,
) -> None:
    """The `[:12]` slice used to swallow the field before it could be checked."""
    fields = ",".join(library.LIST_FIELDS) + ",nonexistent_field"
    payload = structured(await library.list_videos(assembled.deps, fields=fields))
    assert payload["code"] == "E_BAD_PARAM"
    assert "nonexistent_field" in payload["message"]


# ------------------------------------------------------------- §9.3 copy nits


async def test_job_status_only_offers_a_retry_when_something_failed(
    assembled: Assembled,
) -> None:
    await queue_one_job(assembled)
    listed = body(await indexing.job_status(assembled.deps))
    assert "force_reindex" not in listed, "nothing in this list failed"


def test_the_guide_documents_the_deep_link_lead_and_the_tags_parameter(
    assembled: Assembled,
) -> None:
    guide = resources.guide(assembled.deps)
    assert "2 s" in guide or "2s" in guide
    assert "tags=" in guide


def test_the_contract_quotes_the_guide_that_actually_ships() -> None:
    """tool-surface §5.3 embeds the guide verbatim; drift there is invisible."""
    doc = (
        Path(__file__).resolve().parents[2] / "docs" / "design" / "tool-surface.md"
    ).read_text()
    block = doc[doc.index("### 5.3 `vidtheque://guide`") :]
    start = block.index("```markdown") + len("```markdown\n")
    quoted = block[start : block.index("\n```", start) + 1]
    assert quoted.strip() == resources.GUIDE.strip()
