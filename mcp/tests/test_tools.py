"""Tool behaviour: payload shape, caps, notes, and the typed error contract."""

from __future__ import annotations

import json

from mcp_types import ImageContent, TextContent

from vidtheque_mcp.app import Assembled
from vidtheque_mcp.text import TRUNCATION_MARKER
from vidtheque_mcp.tools import frames as frames_tool
from vidtheque_mcp.tools import indexing, library, resources, search, segment

def body(result) -> str:
    return "\n".join(b.text for b in result.content if isinstance(b, TextContent))


def structured(result) -> dict:
    assert result.structured_content is not None
    return result.structured_content


# ------------------------------------------------------------------- search


async def test_search_returns_transcript_hits_with_deep_links(assembled: Assembled) -> None:
    result = await search.run(assembled.deps, q="cache", limit=5)
    text = body(result)
    assert "[transcript]" in text
    assert "https://youtu.be/kCc8FmEb1nY?t=" in text
    assert "Legs: transcript" in text
    ids = {r["video_id"] for r in structured(result)["results"]}
    assert "kCc8FmEb1nY" in ids


async def test_search_needs_a_query_or_a_filter(assembled: Assembled) -> None:
    result = await search.run(assembled.deps)
    assert result.is_error
    assert structured(result)["code"] == "E_EMPTY_QUERY"


async def test_browse_mode_works_with_a_filter_alone(assembled: Assembled) -> None:
    result = await search.run(assembled.deps, q="*", channel="karpathy", limit=5)
    assert not result.is_error
    assert "note:" in body(result)  # browse mode announces the skipped legs


async def test_search_clamps_limit_server_side(assembled: Assembled) -> None:
    """Not prompt-only: screenpipe's advisory "max 20" is a live bug."""
    result = await search.run(assembled.deps, q="cache", limit=9999)
    assert structured(result)["pagination"]["limit"] == 50


async def test_all_means_all_and_a_skipped_leg_prints_a_note(assembled: Assembled) -> None:
    result = await search.run(assembled.deps, q="cache", min_chars=5, limit=5)
    assert "frame leg was not queried" in body(result)
    assert any("min_chars" in n for n in structured(result)["notes"])


async def test_speaker_filter_is_feature_disabled(assembled: Assembled) -> None:
    result = await search.run(assembled.deps, q="cache", speaker="karpathy")
    assert result.is_error
    assert structured(result)["code"] == "E_FEATURE_DISABLED"


async def test_video_time_order_needs_a_single_video(assembled: Assembled) -> None:
    result = await search.run(assembled.deps, q="cache", order="video_time")
    assert structured(result)["code"] == "E_ORDER_SCOPE"
    scoped = await search.run(
        assembled.deps, q="cache", order="video_time", video_id="kCc8FmEb1nY"
    )
    assert not scoped.is_error


async def test_unknown_video_names_the_id(assembled: Assembled) -> None:
    result = await search.run(assembled.deps, q="cache", video_id="notarealid1")
    payload = structured(result)
    assert payload["code"] == "E_UNKNOWN_VIDEO"
    assert "notarealid1" in payload["message"]
    assert "index-video" in payload["next"]


async def test_bad_time_format_echoes_the_accepted_formats(assembled: Assembled) -> None:
    result = await search.run(assembled.deps, q="cache", published_after="last tuesday-ish")
    payload = structured(result)
    assert payload["code"] == "E_BAD_TIME_FORMAT"
    assert "7d ago" in payload["next"]


async def test_empty_result_teaches_the_next_call(assembled: Assembled) -> None:
    result = await search.run(assembled.deps, q="cache", channel="nobody-by-that-name")
    text = body(result)
    assert "Results: 0/0" in text
    assert "data_status:" in text
    assert "next:" in text


async def test_truncation_marker_and_opt_out(assembled: Assembled) -> None:
    truncated = await search.run(assembled.deps, q="cache", max_text_chars=120, limit=5)
    full = await search.run(assembled.deps, q="cache", max_text_chars=0, limit=5)

    cut = {r["video_id"] + str(r["start"]): r["text"] for r in structured(truncated)["results"]}
    whole = {r["video_id"] + str(r["start"]): r["text"] for r in structured(full)["results"]}
    marked = [t for t in cut.values() if "chars truncated" in t]
    assert marked, "the fixture's clustered segment is longer than 120 chars"
    assert TRUNCATION_MARKER.split("{")[0] in marked[0]
    # Middle truncation: both ends survive.
    key = next(k for k, v in cut.items() if "chars truncated" in v)
    assert whole[key].startswith(cut[key].split("…[")[0])
    assert whole[key].endswith(cut[key].split("]…")[-1])
    # The 0 opt-out is tested because screenpipe once shipped a build where 0
    # returned only the marker.
    assert all("chars truncated" not in t for t in whole.values())
    assert len(whole[key]) > len(cut[key])
    assert "Text middle-truncated at 120 chars" in body(truncated)


async def test_pagination_line_and_has_more(assembled: Assembled) -> None:
    first = await search.run(assembled.deps, q="cache OR attention OR memory", limit=1, cluster_gap=0)
    text = body(first)
    assert "Results: 1/" in text
    if structured(first)["pagination"]["has_more"]:
        assert "use offset=1 for more" in text


async def test_vector_leg_note_when_the_worker_is_down(assembled: Assembled) -> None:
    assembled.deps.embeddings.fail = True  # type: ignore[attr-defined]
    result = await search.run(assembled.deps, q="cache", limit=5)
    assert any("embedding worker is unreachable" in n for n in structured(result)["notes"])
    assert not result.is_error  # never a silent empty; the lexical leg answers


async def test_frame_leg_degrades_when_the_worker_has_no_text_tower(
    assembled: Assembled,
) -> None:
    """`all` still means all: the leg is skipped *and announced*.

    A worker that predates POST /v1/embeddings/frame-query answers 404, so a
    text query cannot reach the 1152-d frame vectors. Skipping quietly would
    look like "there were no visual matches", which is a different and false
    claim.
    """
    deps = assembled.deps
    deps.embeddings.serves_frame_text = False  # type: ignore[attr-defined]
    result = await search.run(deps, q="cache", limit=5)
    notes = structured(result)["notes"]
    assert any("no text->frame-space endpoint" in n for n in notes)
    assert not result.is_error
    assert structured(result)["results"], "the text legs still answer"

    # Probed once, then remembered: no wasted call per search.
    assert deps.frame_text_encoder is False
    before = len(deps.embeddings.calls)  # type: ignore[attr-defined]
    await search.run(deps, q="cache", limit=5)
    assert len(deps.embeddings.calls) - before == 1  # type: ignore[attr-defined]


async def test_query_prefix_is_the_workers_job(assembled: Assembled) -> None:
    """`input_type=query` is the worker's asymmetric-prefix switch; applying
    config['text_embed.query_prefix'] here as well would double it."""
    deps = assembled.deps
    await search.run(deps, q="cache", limit=5)
    _, texts = deps.embeddings.calls[0]  # type: ignore[attr-defined]
    assert texts == ["cache"]
    assert deps.db.query_prefix, "the config key still records what indexing assumed"


async def test_tsv_format_writes_the_keys_once(assembled: Assembled) -> None:
    result = await search.run(assembled.deps, q="cache", format="tsv", limit=5)
    text = body(result)
    assert "video_id\tstart\ttext\tlink\tsource" in text


async def test_search_never_returns_image_blocks(assembled: Assembled) -> None:
    result = await search.run(assembled.deps, q="cache", limit=10)
    assert all(not isinstance(b, ImageContent) for b in result.content)


# -------------------------------------------------------------- list-videos


async def test_list_videos_is_tsv_by_default(assembled: Assembled) -> None:
    result = await library.list_videos(assembled.deps)
    text = body(result)
    assert "video_id\ttitle\tchannel\tpublished\tduration\tcoverage" in text
    assert "coverage: t=transcript" in text
    assert "kCc8FmEb1nY" in text


async def test_list_videos_coverage_flags_the_gaps(assembled: Assembled) -> None:
    result = await library.list_videos(assembled.deps, fields="video_id,coverage")
    rows = {r["video_id"]: r["coverage"] for r in structured(result)["videos"]}
    assert rows["kCc8FmEb1nY"] == "tof"
    assert rows["eMlx5fFNoYc"] == "t--"


async def test_list_videos_relevance_needs_a_query(assembled: Assembled) -> None:
    result = await library.list_videos(assembled.deps, order="relevance")
    assert structured(result)["code"] == "E_ORDER_SCOPE"


async def test_list_videos_rejects_unknown_fields(assembled: Assembled) -> None:
    result = await library.list_videos(assembled.deps, fields="video_id,nonsense")
    assert structured(result)["code"] == "E_BAD_PARAM"


# ------------------------------------------------------------ corpus-summary


async def test_corpus_summary_rolls_up(assembled: Assembled) -> None:
    result = await library.corpus_summary(assembled.deps)
    text = body(result)
    assert "Corpus: 3 videos" in text
    assert "data_status:" in text
    assert "Channels (top" in text
    assert "next_best_query:" in text
    assert structured(result)["videos"] == 3


async def test_corpus_summary_sections_can_be_switched_off(assembled: Assembled) -> None:
    result = await library.corpus_summary(
        assembled.deps,
        include_channels=False,
        include_tags=False,
        include_recent=False,
        include_gaps=False,
        include_guidance=False,
    )
    text = body(result)
    assert "Channels" not in text
    assert "Gaps" not in text


# ------------------------------------------------------------- video-summary


async def test_video_summary_never_dumps_a_transcript(assembled: Assembled) -> None:
    result = await library.video_summary(assembled.deps, video_id="kCc8FmEb1nY")
    text = body(result)
    assert "Chapters (" in text
    assert "Key texts (" in text
    assert "On-screen text highlights (" in text
    assert "data_status: ok" in text


async def test_video_summary_reports_missing_channels(assembled: Assembled) -> None:
    result = await library.video_summary(assembled.deps, video_id="eMlx5fFNoYc")
    assert structured(result)["data_status"] == "no_ocr"


async def test_video_summary_unknown_video(assembled: Assembled) -> None:
    result = await library.video_summary(assembled.deps, video_id="nope")
    assert structured(result)["code"] == "E_UNKNOWN_VIDEO"


# ------------------------------------------------------- get-segment-context


async def test_segment_context_returns_the_words(assembled: Assembled) -> None:
    result = await segment.run(assembled.deps, video_id="kCc8FmEb1nY", t=6, window=45)
    text = body(result)
    assert "TRANSCRIPT" in text
    assert "we cache the keys" in text
    assert "FRAMES: kCc8FmEb1nY-00000" in text
    assert structured(result)["frame_ids"] == ["kCc8FmEb1nY-00000"]


async def test_segment_context_double_cap_names_the_binding_one(assembled: Assembled) -> None:
    tight = await segment.run(
        assembled.deps, video_id="kCc8FmEb1nY", t=6, window=300, max_text_chars=200
    )
    assert structured(tight)["binding_cap"] == "max_text_chars"
    loose = await segment.run(
        assembled.deps, video_id="kCc8FmEb1nY", t=6, window=45, max_text_chars=20000
    )
    assert structured(loose)["binding_cap"] == "window"


async def test_segment_context_clamps_a_late_timestamp(assembled: Assembled) -> None:
    result = await segment.run(assembled.deps, video_id="kCc8FmEb1nY", t=99999)
    assert "clamped" in body(result)
    assert not result.is_error  # no error for a slightly-late timestamp


async def test_segment_context_cue_from_another_video(assembled: Assembled) -> None:
    result = await segment.run(assembled.deps, video_id="kCc8FmEb1nY", cue_id=7)
    payload = structured(result)
    assert payload["code"] == "E_BAD_PARAM"
    assert "zduSFxRajkE" in payload["message"]  # names the right video


async def test_segment_context_returns_no_images(assembled: Assembled) -> None:
    result = await segment.run(assembled.deps, video_id="kCc8FmEb1nY", t=6)
    assert all(not isinstance(b, ImageContent) for b in result.content)


# ---------------------------------------------------------------- get-frames


async def test_get_frames_url_mode_is_the_default(assembled: Assembled) -> None:
    result = await frames_tool.run(assembled.deps, frame_ids=["kCc8FmEb1nY-00000"])
    text = body(result)
    assert "image: http://localhost:8080/frames/kCc8FmEb1nY-00000.jpg" in text
    assert "ocr:" in text
    assert all(not isinstance(b, ImageContent) for b in result.content)


async def test_get_frames_image_mode_is_jpeg(assembled: Assembled) -> None:
    result = await frames_tool.run(
        assembled.deps, frame_ids=["kCc8FmEb1nY-00000"], return_="image"
    )
    images = [b for b in result.content if isinstance(b, ImageContent)]
    assert len(images) == 1
    # mimeType is image/jpeg and the bytes ARE JPEG.
    assert images[0].mime_type == "image/jpeg"
    import base64

    assert base64.b64decode(images[0].data).startswith(b"\xff\xd8\xff")


async def test_get_frames_inline_cap_downgrades_to_urls(assembled: Assembled) -> None:
    """Extras downgrade to URLs rather than failing."""
    deps = assembled.deps
    object.__setattr__(deps.settings, "inline_frame_max", 1)
    result = await frames_tool.run(
        deps,
        frame_ids=["kCc8FmEb1nY-00000", "kCc8FmEb1nY-00001"],
        return_="image",
        limit=2,
    )
    text = body(result)
    assert "1 inline, 1 as URLs" in text
    assert len([b for b in result.content if isinstance(b, ImageContent)]) == 1


async def test_get_frames_collects_per_frame_failures(assembled: Assembled) -> None:
    result = await frames_tool.run(
        assembled.deps, frame_ids=["kCc8FmEb1nY-00000", "kCc8FmEb1nY-09999"], limit=5
    )
    assert "failed:" in body(result)
    assert structured(result)["failed"]


async def test_get_frames_unknown_frame_names_the_range(assembled: Assembled) -> None:
    result = await frames_tool.run(assembled.deps, frame_ids=["kCc8FmEb1nY-09999"])
    payload = structured(result)
    assert payload["code"] == "E_UNKNOWN_FRAME"
    assert "00000-00001" in payload["message"]


async def test_get_frames_needs_ids_or_a_video(assembled: Assembled) -> None:
    result = await frames_tool.run(assembled.deps)
    assert structured(result)["code"] == "E_BAD_PARAM"


async def test_get_frames_span_is_bounded(assembled: Assembled) -> None:
    result = await frames_tool.run(
        assembled.deps, video_id="kCc8FmEb1nY", t_start=0, t_end=5000
    )
    payload = structured(result)
    assert payload["code"] == "E_BAD_PARAM"
    assert "narrow" in payload["next"]


# ------------------------------------------------------------------ tagging


async def test_tag_video_is_idempotent(assembled: Assembled) -> None:
    first = await library.tag_video(
        assembled.deps, video_id="eMlx5fFNoYc", add=["topic:transformers"]
    )
    assert structured(first)["added"]["topic:transformers"] == {"new": 1, "existing": 0}
    again = await library.tag_video(
        assembled.deps, video_id="eMlx5fFNoYc", add=["topic:transformers"]
    )
    assert structured(again)["added"]["topic:transformers"] == {"new": 0, "existing": 1}


async def test_tag_video_dry_run_changes_nothing(assembled: Assembled) -> None:
    await library.tag_video(
        assembled.deps, video_id="eMlx5fFNoYc", add=["topic:cuda"], dry_run=True
    )
    listed = await library.list_videos(assembled.deps, fields="video_id,tags")
    rows = {r["video_id"]: r["tags"] for r in structured(listed)["videos"]}
    assert "topic:cuda" not in rows["eMlx5fFNoYc"]


async def test_tag_video_rejects_a_bad_namespace(assembled: Assembled) -> None:
    result = await library.tag_video(
        assembled.deps, video_id="eMlx5fFNoYc", add=["nonsense:thing"]
    )
    payload = structured(result)
    assert payload["code"] == "E_BAD_PARAM"
    assert "topic" in payload["next"]


async def test_tag_video_partial_batches_do_not_apply(assembled: Assembled) -> None:
    result = await library.tag_video(
        assembled.deps, video_id=["eMlx5fFNoYc", "notarealid1"], add=["topic:cuda"]
    )
    assert structured(result)["code"] == "E_UNKNOWN_VIDEO"
    listed = await library.list_videos(assembled.deps, fields="video_id,tags")
    rows = {r["video_id"]: r["tags"] for r in structured(listed)["videos"]}
    assert "topic:cuda" not in rows["eMlx5fFNoYc"]


async def test_tag_filter_then_search(assembled: Assembled) -> None:
    result = await search.run(assembled.deps, q="cache", tags="series:gpu-mode", limit=5)
    ids = {r["video_id"] for r in structured(result)["results"]}
    assert ids <= {"zduSFxRajkE"}


# --------------------------------------------------------------- index-video


async def test_index_video_creates_a_job(assembled: Assembled) -> None:
    result = await indexing.index_video(assembled.deps, url="https://youtu.be/Qk7mF2xLp0A")
    text = body(result)
    assert "Job queued: job_" in text
    assert "Nothing from this video is searchable until the job reports done." in text
    assert structured(result)["job_id"].startswith("job_")


async def test_index_video_accepts_a_bare_id(assembled: Assembled) -> None:
    result = await indexing.index_video(assembled.deps, url="Qk7mF2xLp0A")
    assert structured(result)["job_id"] is not None


async def test_index_video_rejects_an_unsupported_source(assembled: Assembled) -> None:
    result = await indexing.index_video(assembled.deps, url="not a url at all")
    payload = structured(result)
    assert payload["code"] == "E_UNSUPPORTED_SOURCE"
    assert "supported" in payload["next"]


async def test_index_video_is_a_noop_for_an_indexed_video(assembled: Assembled) -> None:
    result = await indexing.index_video(assembled.deps, url="https://youtu.be/kCc8FmEb1nY")
    assert structured(result)["job_id"] is None
    assert "Already indexed" in body(result)


async def test_index_video_echoes_at_most_ten_titles(assembled: Assembled) -> None:
    urls = [f"https://youtu.be/vid{i:08d}" for i in range(10)]
    result = await indexing.index_video(assembled.deps, urls=urls)
    assert body(result).count("https://youtu.be/vid") <= 10


# ---------------------------------------------------------------- job-status


async def test_job_status_reports_the_new_job(assembled: Assembled) -> None:
    created = await indexing.index_video(assembled.deps, url="https://youtu.be/Qk7mF2xLp0A")
    job_id = structured(created)["job_id"]
    result = await indexing.job_status(assembled.deps, job_id=job_id)
    text = body(result)
    assert job_id in text
    assert "state: queued" in text
    for wire in ("download", "transcribe", "keyframes", "ocr", "embed"):
        assert wire in text


async def test_job_status_lists_recent_jobs(assembled: Assembled) -> None:
    await indexing.index_video(assembled.deps, url="https://youtu.be/Qk7mF2xLp0A")
    result = await indexing.job_status(assembled.deps)
    assert "Jobs: 1 active" in body(result)


async def test_job_status_unknown_job(assembled: Assembled) -> None:
    result = await indexing.job_status(assembled.deps, job_id="job_deadbeef0000")
    payload = structured(result)
    assert payload["code"] == "E_UNKNOWN_JOB"
    assert "no arguments" in payload["next"]


async def test_pipeline_fails_the_item_with_not_implemented(assembled: Assembled) -> None:
    """The pipeline seam: bookkeeping is real, execution is the next milestone."""
    created = await indexing.index_video(assembled.deps, url="https://youtu.be/Qk7mF2xLp0A")
    job_id = structured(created)["job_id"]

    assert await assembled.runner.run_once() is True

    result = await indexing.job_status(assembled.deps, job_id=job_id)
    payload = structured(result)
    assert payload["state"] == "failed"
    assert payload["error_code"] == "E_NOT_IMPLEMENTED"
    assert "not implemented" in body(result)
    assert "Nothing was fetched" in body(result)


async def test_runner_is_a_noop_with_an_empty_queue(assembled: Assembled) -> None:
    assert await assembled.runner.run_once() is False


async def test_job_cancellation_is_cooperative(assembled: Assembled) -> None:
    from vidtheque_mcp.jobs import store as jobs_store

    created = await indexing.index_video(assembled.deps, url="https://youtu.be/Qk7mF2xLp0A")
    job_id = structured(created)["job_id"]
    await assembled.db.write(lambda c: jobs_store.request_cancel(c, job_id))
    await assembled.runner.run_once()
    result = await indexing.job_status(assembled.deps, job_id=job_id, state="all")
    assert structured(result)["state"] == "cancelled"


# ----------------------------------------------------------------- resources


async def test_corpus_resource_is_tsv_with_a_footer(assembled: Assembled) -> None:
    text = await resources.corpus_resource(assembled.deps)
    assert text.startswith("# vidtheque corpus · 3 videos")
    assert "video_id\ttitle\tchannel" in text
    assert "narrow with the list-videos tool" in text


async def test_context_resource_is_json_with_timestamps(assembled: Assembled) -> None:
    payload = json.loads(await resources.context_resource(assembled.deps))
    assert payload["corpus"]["videos"] == 3
    assert payload["timestamps"]["today_start"].endswith("Z")
    assert payload["id_formats"]["frame_id"].startswith("<video_id>-")


async def test_guide_carries_the_shared_rules(assembled: Assembled) -> None:
    """DECISIONS.md lifts these out of the nine tool descriptions."""
    guide = resources.GUIDE
    assert "Never fabricate ids" in guide
    assert "t_start`/`t_end" in guide
    assert "case-insensitive substrings" in guide
    assert "max_text_chars=0" in guide
