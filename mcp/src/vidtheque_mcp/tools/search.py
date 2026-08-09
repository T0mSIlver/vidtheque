"""`search` — one tool, many content types (tool-surface §4.1).

Modality is a parameter, never a tool name. `content_type=all` queries **all
three** legs, every time; when a filter makes a leg meaningless the leg is
skipped *and the payload says so in a `note:` line*.

Order of operations, which is load-bearing: fuse -> filter -> cluster ->
diversity cap -> page slice.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any, Sequence

from mcp_types import CallToolResult

from ..db import queries
from ..db.connection import admission
from ..errors import ToolError, bad_param
from ..text import (
    cap_response,
    clamp,
    clamp_text_chars,
    clock,
    deeplink,
    middle_truncate,
    pagination_line,
    split_csv,
    tsv,
    validate_tag,
)
from ..timeparse import parse_corpus_time, parse_offset
from .base import Deps, handle_errors, normalize_video_ids, require_known_videos, text_result

CONTENT_TYPES = ("all", "transcript", "ocr", "frame")
ORDERS = ("relevance", "recency", "video_time")
DEFAULT_FIELDS = "video_id,start,text,link,source"

# `order=recency` and `order=video_time` are not relevance, so a relevance
# prefix is the wrong candidate set to sort. Fetching only `offset+limit` rows
# by score and *then* sorting them by date returned the newest hit only when it
# also happened to be one of the most relevant: an older video supplying the
# first `offset+limit+1` hits hid a newer video's hit ranked just below the
# prefix, and `order=recency` never saw it. Non-relevance orders therefore sort
# a bounded candidate UNIVERSE, not a prefix. Bounded, because "complete" over
# a corpus-sized candidate set is not a thing a search tool may promise.
ORDER_UNIVERSE = 400


@dataclass
class Hit:
    source: str  # transcript | ocr | frame | transcript+ocr
    video_id: int
    public_id: str
    title: str
    channel: str | None
    published_at: int | None
    start_s: float
    end_s: float | None
    text: str
    score: float
    cue_ids: list[int]
    frame_id: str | None = None
    n_cues: int = 0


@handle_errors
async def run(
    deps: Deps,
    q: str | None = None,
    content_type: str = "all",
    limit: int = 10,
    offset: int = 0,
    order: str = "relevance",
    video_id: str | list[str] | None = None,
    channel: str | None = None,
    video_title: str | None = None,
    tags: str | None = None,
    include_related: bool = False,
    published_after: str | None = None,
    published_before: str | None = None,
    t_start: float | str | None = None,
    t_end: float | str | None = None,
    speaker: str | None = None,
    min_chars: int | None = None,
    max_chars: int | None = None,
    max_per_video: int = 3,
    cluster_gap: float = 8.0,
    max_text_chars: int = 1000,
    format: str = "text",
    fields: str = DEFAULT_FIELDS,
) -> CallToolResult:
    settings = deps.settings
    notes: list[str] = []

    if content_type not in CONTENT_TYPES:
        raise bad_param(
            f"content_type must be one of {', '.join(CONTENT_TYPES)}.",
            "omit it for all three channels.",
        )
    if order not in ORDERS:
        raise bad_param(f"order must be one of {', '.join(ORDERS)}.", "omit it for relevance.")
    if format not in ("text", "tsv"):
        raise bad_param("format must be text or tsv.", 'omit it for "text".')

    limit = clamp(limit, 1, 50, 10)
    offset = clamp(offset, 0, 10_000, 0)
    max_per_video = clamp(max_per_video, 1, 20, 3)
    cluster_gap = float(clamp(int(cluster_gap), 0, 60, 8))
    max_text_chars = clamp_text_chars(max_text_chars, 120, 20_000, 1000)

    if q is not None and len(q) > 512:
        raise bad_param("q is limited to 512 characters.", "shorten the query.")
    if channel and len(channel) > 128:
        raise bad_param("channel is limited to 128 characters.")
    if video_title and len(video_title) > 256:
        raise bad_param("video_title is limited to 256 characters.")

    wanted_ids = normalize_video_ids(video_id, 20)
    tag_list = split_csv(tags, 10, "tags")
    for tag in tag_list:
        validate_tag(tag)

    if order == "video_time" and len(wanted_ids) != 1:
        raise ToolError(
            "E_ORDER_SCOPE",
            "order=video_time needs a single-video scope — chronological across "
            "the whole corpus is meaningless.",
            'add video_id="…", or use order=relevance.',
        )

    if speaker and not deps.db.diarization_enabled:
        raise ToolError(
            "E_FEATURE_DISABLED",
            "speaker= needs diarization, which is off for this corpus.",
            "omit speaker=. See the deployment docs for DIARIZE=1.",
        )

    flt = queries.CorpusFilter(
        channel=channel,
        video_title=video_title,
        published_after=parse_corpus_time(published_after, "published_after"),
        published_before=parse_corpus_time(published_before, "published_before"),
        video_ids=wanted_ids,
        tags=tag_list,
    )
    span_start = parse_offset(t_start, "t_start")
    span_end = parse_offset(t_end, "t_end")

    browse = queries.is_browse_query(q)
    if browse and not any(
        [channel, video_title, wanted_ids, tag_list, published_after, published_before]
    ):
        raise ToolError(
            "E_EMPTY_QUERY",
            "search needs either a query or at least one filter.",
            "pass q, or use list-videos to browse the library.",
        )

    # Which legs run, and why the others do not.
    legs = {"transcript": True, "ocr": True, "frame": True}
    if content_type != "all":
        for leg in legs:
            legs[leg] = leg == content_type
    if speaker:
        legs["ocr"] = legs["frame"] = False
        notes.append(
            "note: speaker= applies to the transcript leg only — ocr and frame "
            "legs were not queried for this call."
        )
    if (min_chars is not None or max_chars is not None) and legs["frame"]:
        legs["frame"] = False
        notes.append(
            "note: min_chars/max_chars are text filters — the frame leg was not "
            "queried for this call."
        )
    if browse:
        if legs["ocr"] or legs["frame"]:
            notes.append(
                "note: browse mode (no query) lists transcript positions only — "
                "the ocr and frame legs need a query."
            )
        legs["ocr"] = legs["frame"] = False

    if wanted_ids:
        known = await deps.db.read(lambda c: queries.lookup_video_ids(c, wanted_ids))
        require_known_videos(known, wanted_ids)

    video_pool = await deps.db.read(lambda c: queries.resolve_videos(c, flt))
    if not video_pool:
        return await _empty_result(deps, q, content_type, flt, notes)

    # A speaker that resolves to nothing means "filter to nothing", never "no
    # filter" — `speaker_ids=[]` and `speaker_ids=None` are different bindings.
    speaker_ids: list[int] | None = None
    if speaker:
        speaker_ids = await deps.db.read(lambda c: queries.resolve_speakers(c, speaker))
        if not speaker_ids:
            notes.append(
                f'note: no speaker matches "{speaker}" in this corpus — the '
                "transcript leg was filtered to nothing rather than ignoring the "
                "filter. video-summary lists the speakers a video has."
            )

    # The vector legs cannot report "nothing here matched": nearest-neighbour
    # search returns its k nearest whatever it is asked. If not one word of the
    # query occurs anywhere in the corpus, they are not queried at all — which
    # is what makes a genuinely empty answer reachable.
    use_vector = True
    if not browse and (legs["transcript"] or legs["frame"]):
        use_vector = await deps.db.read(lambda c: queries.has_lexical_footing(c, q))
        if not use_vector:
            legs["frame"] = False
            notes.append(
                "note: no word of this query occurs anywhere in the corpus, so the "
                "semantic (nearest-neighbour) legs were not queried — they would "
                "have returned their k nearest vectors regardless."
            )

    qvec = None
    if legs["transcript"] and not browse and use_vector:
        qvec = await deps.embed_query(q or "", notes, space="text")
    qimg = None
    if legs["frame"]:
        # The frame leg runs the *text* query through SigLIP's text tower —
        # same shared embedding space as the stored frame vectors.
        qimg = await deps.embed_query(q or "", notes, space="frame")
        if qimg is None:
            legs["frame"] = False

    # Per-leg caps are an overfetch bound, not the user's `max_per_video`: the
    # cap the user asked for spans modalities and can only be applied once the
    # legs are fused and cross-modal duplicates collapsed (see _cap_per_video).
    leg_per_video = min(50, max(max_per_video * 3, max_per_video + 2))
    fetch_n = offset + limit
    if order != "relevance":
        fetch_n = max(fetch_n, ORDER_UNIVERSE)

    params = queries.SearchParams(
        q=q,
        video_ids=video_pool,
        qvec=qvec,
        limit=fetch_n,  # legs are merged, so each fetches the whole prefix
        offset=0,
        max_per_video=leg_per_video,
        cluster_gap=cluster_gap,
        candidate_cap=settings.candidate_cap,
        t_start=span_start,
        t_end=span_end,
        min_chars=min_chars,
        max_chars=max_chars,
        speaker_ids=speaker_ids,
        vec_max_distance=settings.vec_max_distance,
        k_vec=min(1000, max(50, fetch_n * leg_per_video * 4)),
    )

    async with admission(deps.search_semaphore):
        hits, leg_counts, probe_total, probe_ceiling = await deps.db.read(
            lambda c: _run_legs(
                c,
                params,
                legs,
                qimg,
                leg_per_video,
                fetch_n,
                settings.count_probe_headroom,
                settings.frame_max_distance,
            )
        )

    meta = await deps.db.read(lambda c: _video_meta(c, [h.video_id for h in hits]))
    for hit in hits:
        info = meta.get(hit.video_id)
        if info:
            hit.public_id = info["public_id"]
            hit.title = info["title"]
            hit.channel = info["channel_name"]
            hit.published_at = info["published_at"]

    # fuse -> collapse cross-modal duplicates -> order -> ONE global per-video
    # cap -> page slice. The cap has to sit here, after the collapse: applied
    # per modality it let one video contribute a transcript hit, an OCR hit and
    # a frame hit — three results — before any other video appeared, and nine
    # at the default cap.
    hits = _dedup_ocr_against_transcript(hits)
    hits = _sort(hits, order)
    hits = _cap_per_video(hits, max_per_video)

    page = hits[offset : offset + limit]
    has_more = len(hits) > offset + limit

    if not hits:
        return await _empty_result(
            deps,
            q,
            content_type,
            flt,
            notes,
            reason="Every leg was queried and none of them matched.",
            limit=limit,
            offset=offset,
        )

    related: dict[str, int] | None = None
    if include_related and page:
        rows = await deps.db.read(
            lambda c: queries.related_tags(c, [h.video_id for h in page], tag_list)
        )
        related = {str(r["full"]): int(r["n"]) for r in rows}

    body = _render(
        deps,
        page,
        q=q,
        content_type=content_type,
        order=order,
        max_per_video=max_per_video,
        offset=offset,
        limit=limit,
        has_more=has_more,
        probe_total=probe_total,
        probe_ceiling=probe_ceiling,
        leg_counts=leg_counts,
        notes=notes,
        max_text_chars=max_text_chars,
        related=related,
        fmt=format,
        fields=fields,
    )
    structured: dict[str, Any] = {
        "results": [_as_dict(deps, h, max_text_chars) for h in page],
        "pagination": {
            "limit": limit,
            "offset": offset,
            "has_more": has_more,
            "approx_total": probe_total,
        },
        "notes": notes,
    }
    if related is not None:
        structured["related_tags"] = related
    return text_result(body, structured)


# ---------------------------------------------------------------------------


def _run_legs(
    conn: sqlite3.Connection,
    params: queries.SearchParams,
    legs: dict[str, bool],
    qimg: bytes | None,
    leg_per_video: int,
    fetch_n: int,
    headroom: int,
    frame_max_distance: float,
) -> tuple[list[Hit], dict[str, int], int, bool]:
    hits: list[Hit] = []
    counts = {"transcript": 0, "ocr": 0, "frame": 0}
    probe_total = 0
    probe_ceiling = False

    if legs["transcript"]:
        rows = queries.search_transcript(conn, params)
        counts["transcript"] = len(rows)
        for row in rows:
            hits.append(
                Hit(
                    source="transcript",
                    video_id=int(row["video_id"]),
                    public_id="",
                    title="",
                    channel=None,
                    published_at=None,
                    start_s=float(row["start_s"]),
                    end_s=float(row["end_s"]),
                    text=str(row["text"] or ""),
                    score=float(row["score"]),
                    cue_ids=json.loads(row["cue_ids"]),
                    n_cues=int(row["n_cues"]),
                )
            )
        total, ceiling = queries.probe_transcript(conn, params, headroom)
        probe_total += total
        probe_ceiling = probe_ceiling or ceiling

    if legs["ocr"]:
        rows = queries.search_ocr(conn, params)
        counts["ocr"] = len(rows)
        for row in rows:
            hits.append(
                Hit(
                    source="ocr",
                    video_id=int(row["video_id"]),
                    public_id="",
                    title="",
                    channel=None,
                    published_at=None,
                    start_s=float(row["t_s"]),
                    end_s=None,
                    text=str(row["text"] or ""),
                    score=float(row["score"]),
                    cue_ids=[],
                    frame_id=str(row["frame_id"]),
                )
            )
        total, ceiling = queries.probe_ocr(conn, params, headroom)
        probe_total += total
        probe_ceiling = probe_ceiling or ceiling

    if legs["frame"] and qimg is not None:
        k_frames = min(2000, max(20, fetch_n * leg_per_video * 4))
        rows = queries.search_frames(conn, params, qimg, k_frames, frame_max_distance)
        counts["frame"] = len(rows)
        for row in rows:
            hits.append(
                Hit(
                    source="frame",
                    video_id=int(row["video_row_id"]),
                    public_id=str(row["video_id"]),
                    title=str(row["title"]),
                    channel=row["channel"],
                    published_at=None,
                    start_s=float(row["t_s"]),
                    end_s=None,
                    text=str(row["ocr_text"] or "visual match, no text hit"),
                    score=float(row["score"]),
                    cue_ids=[],
                    frame_id=str(row["frame_id"]),
                )
            )
        probe_total += len(rows)

    return hits, counts, probe_total, probe_ceiling


def _video_meta(conn: sqlite3.Connection, ids: Sequence[int]) -> dict[int, sqlite3.Row]:
    ids = [i for i in ids if i]
    if not ids:
        return {}
    rows = conn.execute(
        "SELECT id, public_id, title, channel_name, published_at FROM videos "
        "WHERE id IN (SELECT value FROM json_each(?))",
        (json.dumps(list(set(ids))),),
    ).fetchall()
    return {int(r["id"]): r for r in rows}


def _normalize(text: str) -> str:
    return " ".join("".join(c if c.isalnum() or c.isspace() else " " for c in text.casefold()).split())


def _trigrams(text: str) -> set[str]:
    padded = f"  {text} "
    return {padded[i : i + 3] for i in range(max(0, len(padded) - 2))}


def _dedup_ocr_against_transcript(hits: list[Hit]) -> list[Hit]:
    """OCR-vs-transcript dedup (§3.10), the whole rule, in one place.

    Same video, within 5s, and *similar text* — containment or trigram-Jaccard
    >= 0.8. Similar collapses into one result and the longer text wins;
    different text keeps both, because a shell command that happens to sit
    under a sentence about it is a second fact, not a duplicate.

    It runs caller-side because it is O(n.m) string work over a set already
    capped at limit x a small constant, and because "which one survived"
    becomes the `[transcript+ocr]` provenance prefix — a rendering decision,
    not a storage one. SQL used to pre-drop on time-overlap and length alone,
    which failed both halves of the rule at once (smoke §4.4).
    """
    transcripts = [h for h in hits if h.source == "transcript"]
    survivors: list[Hit] = []
    # RRF is a sum over LEGS. A survivor that swallowed three OCR lines still
    # saw the OCR leg once, so it earns one OCR contribution — the best one.
    # Adding all three would let a repeated slide out-rank a better result;
    # adding none (the old behaviour) threw away the corroboration entirely, so
    # a passage both channels agreed on scored the same as one only the
    # transcript found.
    absorbed: set[int] = set()
    for hit in hits:
        if hit.source != "ocr":
            survivors.append(hit)
            continue
        merged = False
        norm = _normalize(hit.text)
        grams = _trigrams(norm)
        for other in transcripts:
            if other.video_id != hit.video_id:
                continue
            if not (other.start_s - 5.0 <= hit.start_s <= (other.end_s or other.start_s) + 5.0):
                continue
            other_norm = _normalize(other.text)
            other_grams = _trigrams(other_norm)
            union = grams | other_grams
            jaccard = len(grams & other_grams) / len(union) if union else 0.0
            # "contained in the other" runs both ways: with the SQL prefilter
            # gone, a slide that spells out a clipped cue is the common shape,
            # and it is the case `the longer text wins` was written for.
            contained = (norm and norm in other_norm) or (other_norm and other_norm in norm)
            if norm and (contained or jaccard >= 0.8):
                other.source = "transcript+ocr"
                if hit.frame_id and not other.frame_id:
                    other.frame_id = hit.frame_id
                if len(hit.text) > len(other.text):
                    other.text = hit.text  # the longer text wins
                if id(other) not in absorbed:
                    # OCR rows arrive in rank order, so the first to collapse
                    # into this survivor is the leg's best contribution.
                    other.score += hit.score
                    absorbed.add(id(other))
                merged = True
                break
        if not merged:
            survivors.append(hit)
    return survivors


def _sort_key(hit: Hit) -> tuple[Any, ...]:
    """The tie-break every ordering ends with.

    Equal BM25 ranks and equal vector distances are ordinary — cues expanded
    from one chunk all share a distance — and Python's sort is stable over an
    input whose order came from an unordered set of legs. Without a total order
    the membership of the rows at the page boundary can change between two
    identical calls, which is exactly the guarantee `offset` depends on.
    """
    return (hit.public_id, hit.start_s, hit.source, hit.frame_id or "", hit.text[:64])


def _sort(hits: list[Hit], order: str) -> list[Hit]:
    if order == "recency":
        return sorted(hits, key=lambda h: (-(h.published_at or 0), -h.score, *_sort_key(h)))
    if order == "video_time":
        return sorted(hits, key=lambda h: (h.start_s, -h.score, *_sort_key(h)))
    return sorted(hits, key=lambda h: (-h.score, *_sort_key(h)))


def _cap_per_video(hits: list[Hit], max_per_video: int) -> list[Hit]:
    """ONE per-video cap, over the fused and deduplicated list.

    Applied after `_sort`, so which hits a dominant video keeps follows the
    ordering the caller asked for: the highest-scoring under `relevance` and
    `recency` (all hits from one video share a publish date), the earliest
    under `video_time`.
    """
    kept: list[Hit] = []
    seen: dict[int, int] = {}
    for hit in hits:
        n = seen.get(hit.video_id, 0)
        if n >= max_per_video:
            continue
        seen[hit.video_id] = n + 1
        kept.append(hit)
    return kept


def _as_dict(deps: Deps, hit: Hit, max_text_chars: int) -> dict[str, Any]:
    return {
        "source": hit.source,
        "video_id": hit.public_id,
        "title": hit.title,
        "channel": hit.channel,
        "start": round(hit.start_s, 2),
        "end": round(hit.end_s, 2) if hit.end_s is not None else None,
        "text": middle_truncate(hit.text, max_text_chars),
        "link": deeplink(hit.public_id, hit.start_s, deps.settings.deeplink_lead_s),
        "cue_ids": hit.cue_ids,
        "frame_id": hit.frame_id,
        "score": round(hit.score, 4),
    }


def _render(
    deps: Deps,
    page: list[Hit],
    *,
    q: str | None,
    content_type: str,
    order: str,
    max_per_video: int,
    offset: int,
    limit: int,
    has_more: bool,
    probe_total: int,
    probe_ceiling: bool,
    leg_counts: dict[str, int],
    notes: list[str],
    max_text_chars: int,
    related: dict[str, int] | None,
    fmt: str,
    fields: str,
) -> str:
    header = [
        pagination_line("Results", len(page), offset, limit, has_more, probe_total, probe_ceiling),
        f'Query: "{q or "*"}" · content_type={content_type} · order={order} · '
        f"max_per_video={max_per_video}",
        f"Legs: transcript {leg_counts['transcript']} · ocr {leg_counts['ocr']} · "
        f"frame {leg_counts['frame']} (fused, RRF k=60)",
        *notes,
        "",
    ]

    if fmt == "tsv":
        wanted = [f.strip() for f in fields.split(",") if f.strip()][:12]
        rows = [_as_dict(deps, h, max_text_chars) for h in page]
        for row in rows:
            row["start"] = clock(row["start"])
            row["source"] = row["source"]
        body = tsv(rows, wanted or DEFAULT_FIELDS.split(","))
        return "\n".join(header) + body

    blocks: list[str] = []
    for hit in page:
        link = deeplink(hit.public_id, hit.start_s, deps.settings.deeplink_lead_s)
        when = clock(hit.start_s) + (f"–{clock(hit.end_s)}" if hit.end_s else "")
        lines = [
            f"[{hit.source}] {hit.title} — {hit.channel or 'unknown'} ({hit.public_id})",
            f"  {when} · {link}",
            "  " + middle_truncate(hit.text, max_text_chars).replace("\n", " "),
        ]
        trailer: list[str] = []
        if hit.cue_ids:
            trailer.append(
                f"cues {hit.cue_ids[0]}-{hit.cue_ids[-1]}"
                if len(hit.cue_ids) > 1
                else f"cue {hit.cue_ids[0]}"
            )
        if hit.frame_id:
            trailer.append(f"frame {hit.frame_id}")
        trailer.append(f"score {hit.score:.4f}")
        lines.append("  " + " · ".join(trailer))
        blocks.append("\n".join(lines))

    body, _ = cap_response(blocks, deps.settings.response_max_chars - 600, "results")

    footer: list[str] = []
    per_video: dict[str, int] = {}
    for hit in page:
        per_video[hit.public_id] = per_video.get(hit.public_id, 0) + 1
    dominant = [v for v, n in per_video.items() if n >= max_per_video]
    if dominant:
        footer.append(
            f"{max_per_video} of {len(page)} results came from {dominant[0]} "
            f"(max_per_video={max_per_video} bound). Raise max_per_video for more from it."
        )
    if max_text_chars and any(len(h.text) > max_text_chars for h in page):
        footer.append(
            f"Text middle-truncated at {max_text_chars} chars — pass "
            "max_text_chars=0 for full text."
        )
    if related:
        footer.append(
            "related tags: " + " · ".join(f"{t} {n}" for t, n in list(related.items())[:12])
        )
    if page:
        first = page[0]
        # Two next steps, because the bench showed the single get-segment-context
        # hint pulling models into window-walking when the question was "where
        # does this video discuss X" — a chapter list answers that in one call.
        footer.append(
            f'next: video-summary video_id="{first.public_id}" for the chapter '
            "list (fastest way to name the moment), or "
            f'get-segment-context video_id="{first.public_id}" '
            f"t={int(first.start_s)} for the full surrounding transcript."
        )
    return "\n".join(header) + body + ("\n" + "\n".join(footer) if footer else "")


async def _empty_result(
    deps: Deps,
    q: str | None,
    content_type: str,
    flt: queries.CorpusFilter,
    notes: list[str],
    reason: str = "No indexed video matched the filters, so no leg was queried.",
    limit: int = 0,
    offset: int = 0,
) -> CallToolResult:
    """No bare "no results": say what the corpus *does* have and what to try."""
    rollup = await deps.db.read(queries.corpus_rollup)
    total = int(rollup["videos_ready"]) + int(rollup["videos_pending"])
    if total == 0:
        status = "empty (nothing has been indexed yet)"
        hint = 'index-video url="https://youtu.be/…" to add your first video.'
    else:
        status = (
            f"ok (corpus has {total} videos, newest published "
            f"{_day(rollup['newest_published'])}, index fresh)"
        )
        hint = "retry with fewer filters, or list-videos to see what is indexed."
    lines = [
        "Results: 0/0",
        f'Query: "{q or "*"}" · content_type={content_type}',
        *notes,
        "",
        f"data_status: {status}",
        reason,
        f"next: {hint}",
    ]
    return text_result(
        "\n".join(lines),
        {
            "results": [],
            "pagination": {
                "limit": limit,
                "offset": offset,
                "has_more": False,
                "approx_total": 0,
            },
            "notes": notes,
            "data_status": status.split()[0],
        },
    )


def _day(ts: Any) -> str:
    from ..text import iso_day

    return iso_day(int(ts) if ts else None)
