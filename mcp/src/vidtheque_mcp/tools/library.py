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
    duration_clock,
    iso_day,
    iso_minute,
    middle_truncate,
    pagination_line,
    split_csv,
    tsv,
    validate_tag,
)
from ..timeparse import parse_corpus_time, parse_offset
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

    limit = clamp(limit, 1, 100, 20)
    offset = clamp(offset, 0, 10_000, 0)
    max_text_chars = clamp_text_chars(max_text_chars, 40, 2000, 120)
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

    wanted = [f.strip() for f in (fields or DEFAULT_LIST_FIELDS).split(",") if f.strip()][:12]
    unknown = [f for f in wanted if f not in LIST_FIELDS]
    if unknown:
        raise bad_param(
            f"unknown field(s): {', '.join(unknown)}.",
            f"available fields: {', '.join(LIST_FIELDS)}.",
        )

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
    missing = [r for r in records if "-" in str(r["coverage"])]
    if missing:
        example = missing[0]
        footer.append(
            # "channels" here means transcript/OCR/frame coverage, but `channel`
            # is also a filter parameter on this very tool, so the old wording
            # read as a missing YouTube channel name (smoke §4.6).
            f"{len(missing)} video(s) have incomplete coverage. "
            f'next: index-video url="https://youtu.be/{example["video_id"]}" force_reindex=true'
        )
    if records:
        footer.append(f'next: video-summary video_id="{records[0]["video_id"]}" for chapters and key texts.')

    return text_result(
        "\n".join(header) + body + "\n".join(footer),
        {
            "videos": records,
            "pagination": {
                "limit": limit,
                "offset": offset,
                "has_more": has_more,
                "approx_total": probe_total,
            },
        },
    )


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

    total = int(rollup["videos_ready"]) + int(rollup["videos_pending"])
    if total == 0:
        status = "empty"
    elif gap_info["active_jobs"]:
        status = "indexing"
    elif gap_info["recent_failed_jobs"]:
        status = "degraded"
    elif gap_info["transcript_no_ocr"]:
        status = "partial"
    else:
        status = "ok"

    hours = float(rollup["hours"] or 0.0)
    lines = [
        f"Corpus: {total} videos · {hours:.1f}h · {int(rollup['cues']):,} transcript cues · "
        f"{int(rollup['keyframes']):,} keyframes",
        f"Published span: {iso_day(rollup['oldest_published'])} → "
        f"{iso_day(rollup['newest_published'])} · last indexed: "
        f"{iso_minute(rollup['last_indexed'])}",
        f"data_status: {status}",
    ]
    structured: dict[str, Any] = {
        "videos": total,
        "hours": hours,
        "cues": int(rollup["cues"]),
        "keyframes": int(rollup["keyframes"]),
        "data_status": status,
    }
    note = deps.db.vectors.note()
    if note:
        lines.append(note)

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
        all_tags, _ = await deps.db.read(queries.tag_count)
        lines.append("")
        lines.append(f"Tags (top {len(rows)} of {all_tags}):")
        if rows:
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

    if include_gaps:
        lines.append("")
        lines.append("Gaps:")
        lines.append(
            f"  {gap_info['transcript_no_ocr']} videos have transcript but no OCR"
        )
        for row in gap_info["failed"][:5]:
            lines.append(
                f"  failed: {row['public_id']} — \"{(row['error'] or 'unknown error')[:120]}\""
            )
        lines.append(f"  {gap_info['indexing']} videos currently indexing")
        structured["gaps"] = {
            "transcript_no_ocr": gap_info["transcript_no_ocr"],
            "indexing": gap_info["indexing"],
            "failed": len(gap_info["failed"]),
        }

    if include_guidance:
        lines.append("")
        if total == 0:
            lines.append(
                'next_best_query: index-video url="https://youtu.be/…" to add your first video.'
            )
        else:
            lines.append(
                'next_best_query: search q="<topic>" limit=5 — or list-videos '
                'channel="…" to browse one channel.'
            )

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
        raise unknown_video(video_id)
    vid = int(row["id"])

    if row["index_state"] == "pending":
        raise ToolError(
            "E_NOT_INDEXED",
            f'Video "{video_id}" is in the corpus but the pipeline never ran.',
            'index-video force_reindex=true to build it.',
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
    structured: dict[str, Any] = {"video_id": video_id, "data_status": status}

    if include_tags:
        tag_map = await deps.db.read(lambda c: queries.video_tags(c, [vid]))
        tag_list = tag_map.get(vid, [])
        if tag_list:
            lines.append("Tags: " + ", ".join(tag_list))
        structured["tags"] = tag_list

    if include_chapters:
        rows = await deps.db.read(lambda c: queries.chapters(c, vid, max_chapters))
        total = await deps.db.read(lambda c: queries.chapter_count(c, vid))
        lines.append("")
        lines.append(f"Chapters ({len(rows)} of {total}):")
        for chapter in rows:
            lines.append(
                f"  {clock(chapter['start_s']):>8}  "
                f"{middle_truncate(str(chapter['title']), max_chars):<48} "
                f"?t={int(chapter['start_s'])}"
            )
        structured["chapters"] = [
            {"start": float(c["start_s"]), "title": c["title"]} for c in rows
        ]

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
        lines.append(f"Key texts ({len(rows)}):")
        for cue in rows:
            lines.append(
                f"  {clock(cue['start_s']):>8}  "
                f'"{middle_truncate(str(cue["text"]), max_chars)}"  ?t={int(cue["start_s"])}'
            )

    if include_ocr_highlights:
        rows = await deps.db.read(
            lambda c: queries.ocr_highlights(c, vid, max_ocr_highlights, span_start, span_end)
        )
        lines.append("")
        lines.append(f"On-screen text highlights ({len(rows)}):")
        for frame in rows:
            lines.append(
                f"  {clock(frame['t_s']):>8}  "
                f"{middle_truncate(str(frame['screen_text'] or ''), max_chars):<52} "
                f"{video_id}-{int(frame['ord']):05d}  ?t={int(frame['t_s'])}"
            )

    if include_links:
        rows = await deps.db.read(lambda c: queries.video_links(c, vid, 10))
        if rows:
            lines.append("")
            lines.append("Links:")
            for link in rows:
                lines.append(f"  {link['url']}  {link['title'] or ''}")

    if include_guidance:
        lines.append("")
        lines.append(
            f'next: get-segment-context video_id="{video_id}" t=0 window=60 '
            "for the actual words."
        )

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
    require_known_videos(known, ids)  # partial batches do not apply
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
