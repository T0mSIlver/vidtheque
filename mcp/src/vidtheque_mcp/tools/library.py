"""`list-videos`, `corpus-summary`, `video-summary`, `tag-video`."""

from __future__ import annotations

import sqlite3
from typing import Any

from mcp_types import CallToolResult

from ..db import queries
from ..db.connection import admission
from ..errors import ToolError, bad_param, unknown_video
from ..text import (
    clamp,
    clamp_text_chars,
    clock,
    deeplink,
    deeplink_t,
    duration_clock,
    iso_day,
    iso_minute,
    iso_z,
    last_page_offset,
    middle_truncate,
    pagination_line,
    split_csv,
    tsv,
    validate_tag,
)
from ..timeparse import parse_corpus_time, parse_offset
from . import corpus_state
from .base import Deps, handle_errors, normalize_video_ids, require_known_videos, text_result

LIST_FIELDS = ("video_id", "title", "channel", "published", "duration", "coverage",
               "tags", "indexed_at", "index_state", "cues", "frames", "link")
DEFAULT_LIST_FIELDS = "video_id,title,channel,published,duration,coverage"
HAS_VALUES = ("transcript", "ocr", "frames", "all", "any")
LIST_ORDERS = ("recency", "title", "duration", "indexed_at", "relevance")

# `index_state=` accepts one of the five states, or `all`. Omitting it keeps
# the query surface's own meaning of "in the corpus" — `ready` and `stale`, the
# videos that have data to answer with (queries.QUERYABLE_INDEX_STATES). The
# dashboard passes `all`, because a management view that cannot see the failed
# and the half-indexed is the one view nobody needs (dashboard.md §5.2).
INDEX_STATE_VALUES = (*queries.INDEX_STATES, "all")


# ---------------------------------------------------------------- list-videos


@handle_errors
async def list_videos(
    deps: Deps,
    q: str | None = None,
    channel: str | None = None,
    tags: str | None = None,
    published_after: str | None = None,
    published_before: str | None = None,
    indexed_after: str | None = None,
    indexed_before: str | None = None,
    has: str = "any",
    index_state: str | None = None,
    order: str = "recency",
    limit: int = 20,
    offset: int = 0,
    format: str = "tsv",
    fields: str = DEFAULT_LIST_FIELDS,
    max_text_chars: int = 120,
) -> CallToolResult:
    if has not in HAS_VALUES:
        raise bad_param(f"has must be one of {', '.join(HAS_VALUES)}.")
    if index_state is not None and index_state not in INDEX_STATE_VALUES:
        raise bad_param(
            f"index_state must be one of {', '.join(INDEX_STATE_VALUES)}.",
            "omit it for the videos that have data to answer with.",
        )
    if order not in LIST_ORDERS:
        raise bad_param(f"order must be one of {', '.join(LIST_ORDERS)}.")
    if format not in ("text", "tsv"):
        raise bad_param("format must be text or tsv.")
    if order == "relevance" and queries.is_browse_query(q):
        raise ToolError(
            "E_ORDER_SCOPE",
            "order=relevance needs a query to be relevant to.",
            "pass q=…, or use order=recency.",
        )
    if q and len(q) > 256:
        raise bad_param("q is limited to 256 characters.")

    asked = (limit, offset)
    limit = clamp(limit, 1, 100, 20)
    offset = clamp(offset, 0, 10_000, 0)
    max_text_chars = clamp_text_chars(max_text_chars, 40, 2000, 120)
    # The same line `search` prints (§5.2's deferred half, taken 2026-08-10):
    # only when a clamp actually moved a number the caller sent, naming both
    # values. A rule honoured by one of the two paging tools is the shape §3.5
    # spent a paragraph removing.
    clamped = [
        f"{name}={was} → {now}"
        for name, was, now in zip(("limit", "offset"), asked, (limit, offset))
        if isinstance(was, int) and not isinstance(was, bool) and was != now
    ]
    tag_list = split_csv(tags, 10, "tags")
    for tag in tag_list:
        validate_tag(tag)

    flt = queries.CorpusFilter(
        channel=channel,
        published_after=parse_corpus_time(published_after, "published_after"),
        published_before=parse_corpus_time(published_before, "published_before"),
        indexed_after=parse_corpus_time(indexed_after, "indexed_after"),
        indexed_before=parse_corpus_time(indexed_before, "indexed_before"),
        tags=tag_list,
        index_states=_index_states(index_state),
    )

    async with admission(deps.search_semaphore):
        pool = await deps.db.read(lambda c: queries.resolve_videos(c, flt))
        rows = await deps.db.read(
            lambda c: queries.list_videos(
                c, pool, q, has, order, limit, offset, deps.settings.candidate_cap
            )
        )
        probe_total, probe_ceiling = await deps.db.read(
            lambda c: queries.probe_videos(
                c, pool, q, has, limit, offset, deps.settings.candidate_cap,
                deps.settings.count_probe_headroom,
            )
        )

    has_more = len(rows) > limit
    page = rows[:limit]
    tag_map = await deps.db.read(lambda c: queries.video_tags(c, [int(r["id"]) for r in page]))

    # Validate every name the caller wrote, *then* cap at twelve. The slice used
    # to run first, so a typo in the thirteenth position was dropped as silently
    # as the blank column §9.1.9 filed against `search`.
    asked = [f.strip() for f in (fields or DEFAULT_LIST_FIELDS).split(",") if f.strip()]
    unknown = [f for f in asked if f not in LIST_FIELDS]
    if unknown:
        raise bad_param(
            f"unknown field(s): {', '.join(unknown)}.",
            f"available fields: {', '.join(LIST_FIELDS)}.",
        )
    wanted = asked[:12]

    records = [_list_record(deps, r, tag_map, max_text_chars) for r in page]
    filter_line = " · ".join(
        part
        for part in [
            f'q="{q}"' if q else "",
            f'channel~"{channel}"' if channel else "",
            f"tags={','.join(tag_list)}" if tag_list else "",
            f"has={has}" if has != "any" else "",
            f"index_state={index_state}" if index_state else "",
            f"order={order}",
        ]
        if part
    )

    header = [
        pagination_line("Videos", len(page), offset, limit, has_more, probe_total, probe_ceiling),
        f"Filter: {filter_line}",
        "",
    ]
    if format == "tsv":
        body = tsv(records, wanted)
    else:
        body = "\n".join(
            f"{r['video_id']}  {r['title']}\n  {r['channel']} · {r['published']} · "
            f"{r['duration']} · coverage {r['coverage']}"
            for r in records
        )

    footer = ["", "coverage: t=transcript o=on-screen text f=frame embeddings -=missing"]
    # Every `note:` this payload prints is also collected, because a client that
    # reads `structuredContent` instead of the text block sees only what is
    # collected (§3.5, round-3 eval §14.1). The clamp note and the queryable
    # reconciliation below are the two round-2 repairs that were invisible to
    # such a client until this list existed.
    notes: list[str] = []
    if clamped:
        notes.append(
            f"note: clamped server-side: {', '.join(clamped)}. The caps are in "
            "vidtheque://context; page with offset instead of raising limit."
        )
    missing = [r for r in records if "-" in str(r["coverage"])]
    if missing:
        example = missing[0]
        notes.append(
            # "channels" here means transcript/OCR/frame coverage, but `channel`
            # is also a filter parameter on this very tool, so the old wording
            # read as a missing YouTube channel name (smoke §4.6).
            f"{len(missing)} video(s) have incomplete coverage. "
            + deps.hint(
                "index-video",
                f'next: index-video url="https://youtu.be/{example["video_id"]}" '
                "force_reindex=true",
                "The channels they do have are searchable; this server cannot "
                "re-index them.",
            )
        )
    # Two counters, one name — the §4.7 lesson, applied to videos. This tool
    # lists `QUERYABLE_INDEX_STATES` and `corpus-summary` counts every row; the
    # gap is now named on the payload that is missing rows, not left for the
    # caller to derive (it derived it wrong).
    if index_state is None:
        states = await corpus_state.read_video_states(deps)
        difference = states.difference()
        if difference:
            notes.append(
                f"note: {states.queryable} of the {states.total} videos in this corpus "
                f"are queryable and can appear here; {difference} cannot. "
                f"corpus-summary counts all {states.total}."
            )
    footer.extend(notes)
    nxt: str | None = None
    if records:
        nxt = f'next: video-summary video_id="{records[0]["video_id"]}" for chapters and key texts.'
        footer.append(nxt)

    pagination: dict[str, Any] = {
        "limit": limit,
        "offset": offset,
        "has_more": has_more,
        "approx_total": probe_total,
    }
    if not page and offset > 0:
        # Same key, same meaning as `search`'s past-the-end payload (§3.4): the
        # offset the last page starts at, so a client that pages structurally
        # does not have to parse the prose to get back.
        pagination["last_offset"] = last_page_offset(probe_total, limit)

    structured_list: dict[str, Any] = {
        "videos": records,
        "pagination": pagination,
        "notes": notes,
    }
    if nxt:
        structured_list["next"] = nxt
    return text_result("\n".join(header) + body + "\n".join(footer), structured_list)


def _index_states(index_state: str | None) -> tuple[str, ...]:
    if index_state is None:
        return queries.QUERYABLE_INDEX_STATES
    if index_state == "all":
        return queries.INDEX_STATES
    return (index_state,)


def _coverage(row: sqlite3.Row) -> str:
    return (
        ("t" if row["has_transcript"] else "-")
        + ("o" if row["has_ocr"] else "-")
        + ("f" if row["has_frames"] else "-")
    )


def _list_record(
    deps: Deps, row: sqlite3.Row, tag_map: dict[int, list[str]], max_text_chars: int
) -> dict[str, Any]:
    public_id = str(row["public_id"])
    return {
        "video_id": public_id,
        "title": middle_truncate(str(row["title"]), max_text_chars),
        "channel": row["channel_name"] or "",
        "published": iso_day(row["published_at"]),
        "duration": duration_clock(row["duration_s"]),
        "coverage": _coverage(row),
        "tags": ",".join(tag_map.get(int(row["id"]), [])),
        "indexed_at": iso_day(row["indexed_at"]),
        # Printed verbatim, never re-derived: `index_state` is the schema's own
        # word and the dashboard colours from the string (dashboard.md §4.5).
        "index_state": str(row["index_state"]),
        "cues": "",
        "frames": "",
        "link": f"https://youtu.be/{public_id}",
    }


# ------------------------------------------------------------- corpus-summary


@handle_errors
async def corpus_summary(
    deps: Deps,
    channel: str | None = None,
    tags: str | None = None,
    published_after: str | None = None,
    published_before: str | None = None,
    include_channels: bool = True,
    include_tags: bool = True,
    include_recent: bool = True,
    include_gaps: bool = True,
    include_guidance: bool = True,
    max_channels: int = 10,
    max_tags: int = 30,
    max_recent: int = 8,
    max_text_chars: int = 120,
) -> CallToolResult:
    max_channels = clamp(max_channels, 1, 50, 10)
    max_tags = clamp(max_tags, 1, 100, 30)
    max_recent = clamp(max_recent, 1, 25, 8)
    max_text_chars = clamp_text_chars(max_text_chars, 40, 2000, 120)
    tag_list = split_csv(tags, 10, "tags")
    for tag in tag_list:
        validate_tag(tag)

    flt = queries.CorpusFilter(
        channel=channel,
        published_after=parse_corpus_time(published_after, "published_after"),
        published_before=parse_corpus_time(published_before, "published_before"),
        tags=tag_list,
    )

    rollup = await deps.db.read(queries.corpus_rollup)
    pool = await deps.db.read(lambda c: queries.resolve_videos(c, flt))
    gap_info = await deps.db.read(queries.gaps)
    backlog = await deps.db.read(queries.embed_backlog)
    states = await corpus_state.read_video_states(deps)

    total = int(rollup["videos_ready"]) + int(rollup["videos_pending"])
    # `data_status` is derived in exactly one place for all three surfaces that
    # print it (tools/corpus_state.py). `indexing` used to be "the jobs table is
    # not empty", which is how five deferred jobs made this line contradict the
    # Gaps block eight lines below it (demo-queries §9.1.4).
    state = await corpus_state.read_corpus_state(deps, total, gap_info, backlog)
    status = state.word

    hours = float(rollup["hours"] or 0.0)
    # This headline counts every row; `list-videos` and `search` answer from
    # `QUERYABLE_INDEX_STATES` only. When the two numbers differ, the difference
    # is printed here rather than left for a caller to reconstruct — one did,
    # and got it wrong in a deliverable (terra eval §4.7).
    short = states.short()
    queryable = f" ({states.queryable} queryable · {short})" if short else ""
    lines = [
        f"Corpus: {total} videos{queryable} · {hours:.1f}h · "
        f"{int(rollup['cues']):,} transcript cues · "
        f"{int(rollup['keyframes']):,} keyframes",
        f"Published span: {iso_day(rollup['oldest_published'])} → "
        f"{iso_day(rollup['newest_published'])} · last indexed: "
        f"{iso_minute(rollup['last_indexed'])}",
        f"data_status: {status}",
    ]
    queue_phrase = state.queue.phrase()
    if queue_phrase:
        lines.append(f"queue: {queue_phrase}")
    structured: dict[str, Any] = {
        "videos": total,
        "queryable_videos": states.queryable,
        "videos_by_index_state": dict(states.counts),
        "hours": hours,
        "cues": int(rollup["cues"]),
        "keyframes": int(rollup["keyframes"]),
        "data_status": status,
        # The `Published span:` line, structured. A client that reads only
        # `structuredContent` had no way to answer "is there anything here from
        # before 2020?" — the question round-1 p5 refused correctly *off this
        # line* (§2.4), and which round 3's structured-only fleet could not
        # reach at all (§14.1).
        "published_span": {
            "oldest": iso_day(rollup["oldest_published"]),
            "newest": iso_day(rollup["newest_published"]),
            "last_indexed": iso_minute(rollup["last_indexed"]),
        },
    }
    notes: list[str] = []
    if backlog["text"] or backlog["frame"]:
        waiting = " and ".join(
            f"{backlog[key]} {what}"
            for key, what in (("text", "transcript"), ("frame", "frame"))
            if backlog[key]
        )
        notes.append(
            f"note: {waiting} vector set(s) are waiting to be re-embedded after "
            "an embedding-model change — semantic search covers only the videos "
            "already re-embedded; keyword search is unaffected. Nothing is "
            "re-downloaded or re-transcribed by the backfill."
        )
        structured["embed_backlog"] = dict(backlog)

    note = deps.db.vectors.note()
    if note:
        notes.append(note)
    lines.extend(notes)

    if include_channels:
        rows = await deps.db.read(lambda c: queries.channel_rollup(c, pool, max_channels))
        total_channels = await deps.db.read(lambda c: queries.channel_count(c, pool))
        lines.append("")
        lines.append(f"Channels (top {len(rows)} of {total_channels}):")
        for row in rows:
            name = middle_truncate(str(row["channel"]), max_text_chars)
            lines.append(f"  {name:<28} {int(row['n']):>4} videos   {float(row['seconds'])/3600:.1f}h")
        structured["channels"] = [
            {"channel": r["channel"], "videos": int(r["n"]), "seconds": float(r["seconds"])}
            for r in rows
        ]

    if include_tags:
        rows = await deps.db.read(lambda c: queries.tag_rollup(c, pool, max_tags))
        # `Tags (top 0 of 1)` was the corpus advertising a feature nothing in it
        # used: `tag_count` counts the `tags` table, the rollup counts tags
        # *attached to videos*, and a tag attached to nothing made the two
        # disagree in one line (demo-queries §9.1.9). The section is printed
        # when the pool has tags — which is also the cheapest possible probe,
        # since the rollup is the answer — and it comes back the moment anyone
        # tags a video.
        if rows:
            all_tags, _ = await deps.db.read(queries.tag_count)
            lines.append("")
            lines.append(f"Tags (top {len(rows)} of {all_tags}):")
            lines.append("  " + " · ".join(f"{r['full']} {int(r['n'])}" for r in rows))
        structured["tags"] = {str(r["full"]): int(r["n"]) for r in rows}

    if include_recent:
        rows = await deps.db.read(lambda c: queries.recent_indexed(c, pool, max_recent))
        lines.append("")
        lines.append("Recently indexed:")
        for row in rows:
            title = middle_truncate(str(row["title"]), max_text_chars)
            lines.append(
                f"  {iso_day(row['indexed_at'])}  {title} — {row['channel_name']} "
                f"({row['public_id']})  {duration_clock(row['duration_s'])}"
            )
        # The only place this tool names a video_id at all; without it a
        # structured-only client has to call `list-videos` to get its first id.
        structured["recent"] = [
            {
                "video_id": str(r["public_id"]),
                "title": str(r["title"]),
                "channel": str(r["channel_name"]),
                "indexed_at": iso_day(r["indexed_at"]),
                "duration": duration_clock(r["duration_s"]),
            }
            for r in rows
        ]

    if include_gaps:
        lines.append("")
        lines.append("Gaps:")
        lines.append(
            f"  {gap_info['transcript_no_ocr']} videos have transcript but no OCR"
        )
        for row in gap_info["failed"][:5]:
            if deps.offers("index-video"):
                lines.append(
                    f"  failed: {row['public_id']} — "
                    f"\"{(row['error'] or 'unknown error')[:120]}\""
                )
            else:
                # `video_stages.error` is the pipeline's own prose — yt-dlp
                # output, worker URLs, operator paths. A reader of a read-only
                # deployment can act on *which* video is incomplete, and there
                # is nothing they can do with the reason. (2026-08-10 audit,
                # F-4: the same redaction the dashboard applies, applied here.)
                lines.append(f"  failed: {row['public_id']}")
        # Two counters, each with one name. `indexing` counts *videos* whose
        # index_state says mid-pipeline; the job line counts *rows in `jobs`*.
        # Printing both as "indexing" is what let the headline and this block
        # disagree without either of them being wrong (§9.1.4).
        lines.append(f"  {state.queue.videos_indexing} video(s) mid-pipeline (index_state=indexing)")
        if state.queue.active:
            lines.append(f"  {state.queue.active} indexing job(s) queued or running: {queue_phrase}")
        structured["gaps"] = {
            "transcript_no_ocr": gap_info["transcript_no_ocr"],
            "indexing": state.queue.videos_indexing,
            "failed": len(gap_info["failed"]),
            "jobs_active": state.queue.active,
            "jobs_running": state.queue.running,
            "jobs_deferred": state.queue.deferred,
            "jobs_deferred_until": iso_z(state.queue.deferred_until),
        }

    if include_guidance:
        lines.append("")
        if total == 0:
            guidance = deps.hint(
                "index-video",
                'next_best_query: index-video url="https://youtu.be/…" to add '
                "your first video.",
                "next_best_query: none — this read-only server has no videos "
                "indexed and no tool that can add one.",
            )
        else:
            guidance = (
                'next_best_query: search q="<topic>" limit=5 — or list-videos '
                'channel="…" to browse one channel.'
            )
        lines.append(guidance)
        structured["next"] = guidance

    structured["notes"] = notes
    return text_result("\n".join(lines), structured)


# -------------------------------------------------------------- video-summary


@handle_errors
async def video_summary(
    deps: Deps,
    video_id: str,
    t_start: float | str | None = None,
    t_end: float | str | None = None,
    include_chapters: bool = True,
    include_speakers: bool = True,
    include_key_texts: bool = True,
    include_ocr_highlights: bool = True,
    include_links: bool = False,
    include_tags: bool = True,
    include_guidance: bool = True,
    max_chapters: int = 20,
    max_key_texts: int = 12,
    max_ocr_highlights: int = 10,
    max_chars: int = 300,
    format: str = "text",
) -> CallToolResult:
    if format not in ("text", "outline"):
        raise bad_param("format must be text or outline.")
    max_chapters = clamp(max_chapters, 1, 50, 20)
    max_key_texts = clamp(max_key_texts, 1, 30, 12)
    max_ocr_highlights = clamp(max_ocr_highlights, 1, 30, 10)
    max_chars = clamp_text_chars(max_chars, 80, 1200, 300)
    span_start = parse_offset(t_start, "t_start")
    span_end = parse_offset(t_end, "t_end")

    row = await deps.db.read(lambda c: queries.lookup_video(c, video_id))
    if row is None:
        raise unknown_video(video_id, can_index=deps.offers("index-video"))
    vid = int(row["id"])

    if row["index_state"] == "pending":
        raise ToolError(
            "E_NOT_INDEXED",
            f'Video "{video_id}" is in the corpus but the pipeline never ran.',
            deps.hint(
                "index-video",
                "index-video force_reindex=true to build it.",
                "nothing in it is queryable, and this read-only server cannot "
                "build it. list-videos has=all shows what is complete.",
            ),
        )
    if row["index_state"] == "indexing":
        from ..jobs import store as jobs_store

        job = await deps.db.read(lambda c: jobs_store.latest_job_for_video(c, vid))
        raise ToolError(
            "E_INDEXING",
            f'Video "{video_id}" is mid-pipeline; only partial data is queryable.',
            f'job-status job_id="{job["public_id"]}"' if job else "job-status to see progress.",
        )

    cov = await deps.db.read(lambda c: queries.coverage(c, vid))
    keyframes = await deps.db.read(lambda c: queries.keyframe_count(c, vid))
    status = _video_status(cov, row["index_state"])

    lines = [
        str(row["title"]),
        f"{row['channel_name']} ({video_id}) · published {iso_day(row['published_at'])} · "
        f"{duration_clock(row['duration_s'])} · indexed {iso_day(row['indexed_at'])}",
        f"https://youtu.be/{video_id}",
        f"data_status: {status} (transcript {_tick(cov, 'has_transcript')} · "
        f"ocr {_tick(cov, 'has_ocr')} {keyframes} keyframes · "
        f"frame embeddings {_tick(cov, 'has_frames')})",
    ]
    # Identity and body, not just `video_id` + `data_status`. This tool used to
    # put its entire substance — title, link, key texts, on-screen highlights —
    # in the text block alone, so a client that reads `structuredContent`
    # instead of prose got `{"video_id": …, "data_status": "ok", "tags": [],
    # "chapters": []}` back from a 27-minute talk: a payload with no content in
    # it (round-3 eval §14.1). §3.5's rule is parity, per tool.
    structured: dict[str, Any] = {
        "video_id": video_id,
        "title": str(row["title"]),
        "channel": str(row["channel_name"]),
        "published": iso_day(row["published_at"]),
        "duration": duration_clock(row["duration_s"]),
        "indexed_at": iso_day(row["indexed_at"]),
        "link": f"https://youtu.be/{video_id}",
        "keyframes": keyframes,
        "data_status": status,
    }

    if include_tags:
        tag_map = await deps.db.read(lambda c: queries.video_tags(c, [vid]))
        tag_list = tag_map.get(vid, [])
        if tag_list:
            lines.append("Tags: " + ", ".join(tag_list))
        structured["tags"] = tag_list

    # §3.6: every `?t=` in this payload is the item's start minus DEEPLINK_LEAD,
    # exactly as `deeplink()` computes it for the tools that print whole URLs.
    lead = deps.settings.deeplink_lead_s
    # Where the closing `next:` line points. It used to say `t=0`
    # unconditionally — the first second of a 27-minute talk, printed directly
    # under three timestamped key texts it could have aimed at (terra eval
    # §4.10). A key text is a spoken moment; a chapter start is a boundary and
    # the first one is usually the intro, so it is the fallback, not the pick.
    aim_key: float | None = None
    aim_chapter: float | None = None

    if include_chapters:
        rows = await deps.db.read(lambda c: queries.chapters(c, vid, max_chapters))
        total = await deps.db.read(lambda c: queries.chapter_count(c, vid))
        lines.append("")
        if rows:
            lines.append(f"Chapters ({len(rows)} of {total}):")
        else:
            # A bare `Chapters (0 of 0):` heading over nothing is the shape §3.7
            # forbids for tags, and it left a caller unable to tell "this video
            # has none" from "this server did not compute them" (§4.10).
            lines.append(
                "Chapters: none — the publisher marked none in the description, "
                "and this corpus does not derive them."
            )
        for chapter in rows:
            lines.append(
                f"  {clock(chapter['start_s']):>8}  "
                f"{middle_truncate(str(chapter['title']), max_chars):<48} "
                f"?t={deeplink_t(chapter['start_s'], lead)}"
            )
        structured["chapters"] = [
            {
                "start": float(c["start_s"]),
                "title": c["title"],
                "link": deeplink(video_id, c["start_s"], lead),
            }
            for c in rows
        ]
        aim_chapter = next((float(c["start_s"]) for c in rows if float(c["start_s"]) > 0), None)

    if include_speakers and deps.db.diarization_enabled:
        rows = await deps.db.read(lambda c: queries.speakers_for(c, vid))
        if rows:
            lines.append("")
            lines.append(
                "Speakers: "
                + ", ".join(f"{r['speaker']} ({float(r['seconds']):.0f}s)" for r in rows)
            )

    if include_key_texts:
        rows = await deps.db.read(
            lambda c: queries.key_texts(c, vid, max_key_texts, span_start, span_end)
        )
        lines.append("")
        if rows:
            lines.append(f"Key texts ({len(rows)}):")
            aim_key = float(rows[0]["start_s"])
        else:
            lines.append(
                "Key texts: none — this video has no transcript cues"
                + (" in the requested t_start/t_end span." if (span_start or span_end) else ".")
            )
        for cue in rows:
            lines.append(
                f"  {clock(cue['start_s']):>8}  "
                f'"{middle_truncate(str(cue["text"]), max_chars)}"  '
                f'?t={deeplink_t(cue["start_s"], lead)}'
            )
        structured["key_texts"] = [
            {
                "start": float(c["start_s"]),
                "text": middle_truncate(str(c["text"]), max_chars),
                "link": deeplink(video_id, c["start_s"], lead),
            }
            for c in rows
        ]

    if include_ocr_highlights:
        rows = await deps.db.read(
            lambda c: queries.ocr_highlights(c, vid, max_ocr_highlights, span_start, span_end)
        )
        lines.append("")
        if rows:
            lines.append(f"On-screen text highlights ({len(rows)}):")
        else:
            lines.append(
                "On-screen text highlights: none — no keyframe of this video "
                "carries readable on-screen text."
            )
        for frame in rows:
            lines.append(
                f"  {clock(frame['t_s']):>8}  "
                f"{middle_truncate(str(frame['screen_text'] or ''), max_chars):<52} "
                f"{video_id}-{int(frame['ord']):05d}  ?t={deeplink_t(frame['t_s'], lead)}"
            )
        # `frame_id` included: it is the only id `get-frames` accepts, and the
        # guide forbids constructing one.
        structured["ocr_highlights"] = [
            {
                "t": float(f["t_s"]),
                "frame_id": f"{video_id}-{int(f['ord']):05d}",
                "screen_text": middle_truncate(str(f["screen_text"] or ""), max_chars),
                "link": deeplink(video_id, f["t_s"], lead),
            }
            for f in rows
        ]

    if include_links:
        rows = await deps.db.read(lambda c: queries.video_links(c, vid, 10))
        if rows:
            lines.append("")
            lines.append("Links:")
            for link in rows:
                lines.append(f"  {link['url']}  {link['title'] or ''}")

    if include_guidance:
        aim, what = (
            (aim_key, " around the first key text above")
            if aim_key is not None
            else (aim_chapter, " around the first chapter above")
            if aim_chapter is not None
            else (None, "")
        )
        nxt = (
            f'next: get-segment-context video_id="{video_id}" t={int(aim or 0)} '
            f"window=60 for the actual words{what}."
        )
        lines.append("")
        lines.append(nxt)
        structured["next"] = nxt

    return text_result("\n".join(lines), structured)


def _tick(cov: sqlite3.Row | None, key: str) -> str:
    return "✓" if cov is not None and cov[key] else "✗"


def _video_status(cov: sqlite3.Row | None, index_state: str) -> str:
    if index_state == "failed":
        return "failed"
    if cov is None or not cov["has_transcript"]:
        return "no_transcript"
    if not cov["has_ocr"]:
        return "no_ocr"
    if not cov["has_frames"]:
        return "no_frames"
    return "ok"


# ------------------------------------------------------------------ tag-video


@handle_errors
async def tag_video(
    deps: Deps,
    video_id: str | list[str],
    add: list[str] | None = None,
    remove: list[str] | None = None,
    dry_run: bool = False,
) -> CallToolResult:
    ids = normalize_video_ids(video_id, 50)
    if not ids:
        raise bad_param("video_id is required.", "pass one id or a list of ids.")
    add_tags = [validate_tag(t) for t in (add or [])]
    remove_tags = [validate_tag(t) for t in (remove or [])]
    if len(add_tags) > 10 or len(remove_tags) > 10:
        raise bad_param("add and remove accept at most 10 tags each.")
    if not add_tags and not remove_tags:
        raise bad_param(
            "at least one of add or remove is required.",
            'e.g. add=["topic:attention"].',
        )

    known = await deps.db.read(lambda c: queries.lookup_video_ids(c, ids))
    require_known_videos(known, ids, deps)  # partial batches do not apply
    internal = [known[i] for i in ids]

    result = await deps.db.write(
        lambda c: queries.apply_tags(c, internal, add_tags, remove_tags, dry_run)
    )

    verb = "Would tag" if dry_run else "Tagged"
    lines = [f"{verb} {len(ids)} video(s)."]
    if result["added"]:
        first = True
        for full, (new, present) in list(result["added"].items())[:10]:
            label = "  added:  " if first else "           "
            first = False
            lines.append(f"{label}{full} ({len(ids)} videos, {new} new / {present} already present)")
    if result["removed"]:
        first = True
        for full, count in list(result["removed"].items())[:10]:
            label = "  removed:" if first else "           "
            first = False
            lines.append(f"{label} {full} ({count} videos)")
    lines.append(
        f"Corpus now has {result['total_tags']} distinct tags across "
        f"{result['namespaces']} namespaces."
    )
    if add_tags:
        lines.append(
            f'next: search q="…" tags="{add_tags[0][0]}:{add_tags[0][1]}" limit=5'
        )

    return text_result(
        "\n".join(lines),
        {
            "videos": len(ids),
            "dry_run": dry_run,
            "added": {k: {"new": v[0], "existing": v[1]} for k, v in result["added"].items()},
            "removed": result["removed"],
            "total_tags": result["total_tags"],
        },
    )
