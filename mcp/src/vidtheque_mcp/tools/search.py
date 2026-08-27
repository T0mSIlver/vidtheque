"""`search` — one tool, many content types (tool-surface §4.1).

Modality is a parameter, never a tool name. `content_type=all` queries **all
three** legs, every time; when a filter makes a leg meaningless the leg is
skipped *and the payload says so in a `note:` line*.

Order of operations, which is load-bearing: fuse -> filter -> cluster ->
diversity cap -> page slice.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from typing import Any, Sequence

from mcp_types import CallToolResult

from ..db import queries
from ..db.connection import admission
from ..errors import ToolError, bad_param
from ..text import (
    TRUNCATION_MARKER,
    cap_response,
    clamp,
    clamp_text_chars,
    clock,
    deeplink,
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
from .params import axis_check

CONTENT_TYPES = ("all", "transcript", "ocr", "frame")
ORDERS = ("relevance", "recency", "video_time")
DEFAULT_FIELDS = "video_id,start,text,link,source"
# The default hit-text budget, named because a caller that renders hits of its
# own (`public/ask.py`) has to truncate to the same length this tool would, and
# a second literal is how the two drift apart.
DEFAULT_MAX_TEXT_CHARS = 1000
# Every column `format="tsv"` can emit — the keys `_as_dict` writes, and the
# list `E_BAD_PARAM` names. A field this does not contain used to print as a
# header with a blank cell under every row (demo-queries §9.1.9), in the one
# tool that answers `order="bogus"` with a clean typed error. `test_discipline`
# holds this tuple to `_as_dict`'s keys so the two cannot drift.
TSV_FIELDS = (
    "source",
    "video_id",
    "title",
    "channel",
    "start",
    "end",
    "match_start",
    "match_cue_id",
    "text",
    "link",
    "cue_ids",
    "frame_id",
    "score",
)

# The candidate POOL: how many rows each leg contributes to the fused ranking.
# It is a function of the query and nothing else — deliberately not of `offset`,
# `limit` or the page. Two things go wrong when the pool is `offset + limit`,
# and the field test hit both (research/demo-queries-2026-08-09.md §9.1.2,
# §9.1.3):
#
# * fusion bonuses fire only when both legs' copies of a hit land inside the
#   fetched prefix, so `limit=3` and `limit=50` produced different rank 1s for
#   the same query — the single largest score differentiator in the payload
#   appearing and disappearing with page size;
# * the vector leg's `k` grew with the page, so a bigger page pulled more raw
#   cues into the same cluster and the citation moved.
#
# Ranking is therefore computed once over the pool and *then* paged: same query
# ⇒ same order, same scores, same `?t=`, at any `limit`. Bounded, because
# "complete" over a corpus-sized candidate set is not a thing a search tool may
# promise — and bounded independently of `limit`, which is the invariant this
# constant exists to satisfy.
#
# It is also what `order=recency` / `order=video_time` need: sorting a
# relevance-truncated prefix by date returned the newest hit only when it also
# happened to be one of the most relevant. Those orders used to be the only
# callers of a pool (as ORDER_UNIVERSE, same 400); now every order gets one.
#
# What it costs, measured on a synthetic 75-video / 16,500-cue corpus with the
# distance floor disabled and a query matching most of it — the worst case, and
# not a realistic one: the transcript leg is roughly linear in the pool
# (100 -> 202 ms, 200 -> 355 ms, 400 -> 646 ms), because `clustered` joins the
# cue run back per island returned. Whole-search, same corpus, that is 737 ms
# against 567 ms for the old `offset + limit` fetch at `limit=10` — and 851 ms
# against 1,411 ms at `limit=50`, since `k` no longer grows with the page. A
# query that matches tens of cues rather than thousands never fills the pool and
# pays none of this. If a corpus ever needs it lower, this is the one number to
# turn: it trades paging depth (400 / 50 = 8 full pages) for candidate-set work.
CANDIDATE_POOL = 400

# How many title matches the `fts 0` note names before it stops. Three is a
# receipt; a list is a second results block nobody asked for.
TITLE_FOOTING_MAX = 3


@dataclass
class Hit:
    source: str  # transcript | ocr | frame | transcript+ocr | ocr+frame
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
    # Where the citation points: the best-scoring matched cue inside the
    # cluster, not the cluster's first second. `None` for the point-in-time
    # legs, where the hit *is* its own anchor.
    anchor_s: float | None = None
    anchor_cue_id: int | None = None
    anchor_text: str = ""
    # Tie-break evidence, filled in below: how many rankers found this hit, and
    # how much of the query its text actually contains.
    n_legs: int = 1
    exact: int = 0
    coverage: float = 0.0
    _norm: str | None = None

    @property
    def cite_s(self) -> float:
        return self.start_s if self.anchor_s is None else self.anchor_s


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
    max_text_chars: int = DEFAULT_MAX_TEXT_CHARS,
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
    unknown_fields = [
        f
        for f in (name.strip() for name in (fields or "").split(","))
        if f and f not in TSV_FIELDS
    ]
    if unknown_fields:
        raise bad_param(
            f"unknown field(s): {', '.join(unknown_fields)}.",
            f"available fields: {', '.join(TSV_FIELDS)}.",
        )

    asked = (limit, offset, max_per_video)
    limit = clamp(limit, 1, 50, 10)
    offset = clamp(offset, 0, 10_000, 0)
    max_per_video = clamp(max_per_video, 1, 20, 3)
    cluster_gap = float(clamp(int(cluster_gap), 0, 60, 8))
    max_text_chars = clamp_text_chars(max_text_chars, 120, 20_000, DEFAULT_MAX_TEXT_CHARS)
    # §5.2's deferred half, taken 2026-08-10: the caps are published ahead of the
    # call (`vidtheque://context`), but a caller who asked for `limit=500` and
    # got 50 had nothing in the payload to tell a clamp from a complete answer —
    # filed twice now, by two vendors' agents (mcp-design-bench §D6, terra eval
    # §4.12). One line, only when a clamp actually moved a number the caller
    # sent, naming both values.
    clamped = [
        f"{name}={was} → {now}"
        for name, was, now in zip(
            ("limit", "offset", "max_per_video"), asked, (limit, offset, max_per_video)
        )
        if isinstance(was, int) and not isinstance(was, bool) and was != now
    ]
    if clamped:
        notes.append(
            f"note: clamped server-side: {', '.join(clamped)}. The caps are in "
            "vidtheque://context; page with offset instead of raising limit."
        )

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
        # The remedy names the nearest working filter, not just the dead one.
        # "omit speaker=" is correct and was still a dead end: a round-3
        # consumer building a per-speaker briefing re-sent the same call with
        # six different names, one after another, because nothing in the error
        # offered a route to the thing it wanted (round-3 eval §14.4). Speaker
        # names are not indexed, but on a conference corpus they are usually in
        # the title, and `video_title=` is a real substring filter.
        raise ToolError(
            "E_FEATURE_DISABLED",
            "speaker= needs diarization, which is off for this corpus, so no "
            "speaker name will ever match — re-sending it with a different "
            "name is refused identically.",
            f'omit speaker=. To find one person anyway, try video_title="{speaker}" '
            "— titles often carry the speaker — or put the name in q=. See the "
            "deployment docs for DIARIZE=1.",
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
    # §3.2's two axes, guarded in the direction that was not: a year in the
    # in-video field used to return a small, tidy, entirely wrong answer with no
    # note (terra eval §4.8). axis_check returns the error, the note, or
    # nothing. (Imported by name: `params` is a local in this function.)
    axis = axis_check(t_start, t_end, scoped=bool(wanted_ids))
    if isinstance(axis, ToolError):
        raise axis
    if axis:
        notes.append(axis)

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
        await _note_untagged_corpus(deps, tag_list, notes)
        # limit/offset are echoed even here: the early return used to default
        # them to 0, so a filter that matched no video answered with
        # `pagination.limit: 0` while the same empty result from an unmatched
        # query echoed the real limit (§9.1.9).
        return await _empty_result(
            deps, q, content_type, flt, notes, limit=limit, offset=offset
        )

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

    # Admission opens *here*, not at the database call, and holds across both.
    # Embedding the query is GPU work on the worker, and it used to happen
    # before any admission control at all — so N concurrent anonymous searches
    # were up to 2N forward passes with nothing bounding N, on the one surface
    # (`/mcp`) that had no rate limit either. One acquisition spans the embeds
    # and the legs deliberately: two would let a request pay for its vectors
    # and then be turned away at the door. (2026-08-10 audit, F-1.)
    async with admission(deps.search_semaphore):
        qvec = None
        if legs["transcript"] and not browse and use_vector:
            qvec = await deps.embed_query(q or "", notes, space="text")
        qimg = None
        if legs["frame"]:
            # The frame leg runs the text query through the frame model itself,
            # into the same space as the stored frame vectors. Same weights as
            # the transcript leg's query with the unified embedder, under a
            # different instruction; SigLIP's text tower with the two-model
            # pair. Either way it is /v1/embeddings/frame-query, never
            # /v1/embeddings.
            qimg = await deps.embed_query(q or "", notes, space="frame")
            if qimg is None:
                legs["frame"] = False

        # The vectors these legs are about to search may not be current yet: an
        # embedder swap rebuilds both vec tables empty and sets the embed stages
        # back to `pending` (migration 0004). Nearest-neighbour search over a
        # half-filled index does not fail, it quietly returns less — so say so.
        # `all` means all, and that includes being honest about what `all` could
        # not reach.
        if legs["transcript"] or legs["frame"]:
            await _note_embed_backlog(deps, legs, notes)

        # Per-leg caps are an overfetch bound, not the user's `max_per_video`:
        # the cap the user asked for spans modalities and can only be applied
        # once the legs are fused and cross-modal duplicates collapsed (see
        # _cap_per_video).
        leg_per_video = min(50, max(max_per_video * 3, max_per_video + 2))
        fetch_n = CANDIDATE_POOL

        params = queries.SearchParams(
            q=q,
            video_ids=video_pool,
            qvec=qvec,
            limit=fetch_n,  # the pool, not the page: ranking must not move with limit
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
            vec_max_margin=settings.vec_max_margin,
            k_vec=_k_for(fetch_n),
        )

        hits, leg_counts, pool_full = await deps.db.read(
            lambda c: _run_legs(
                c,
                params,
                legs,
                qimg,
                fetch_n,
                settings.frame_max_distance,
                settings.frame_max_margin,
                max_text_chars,
            )
        )

    # The words are nowhere in the *text* the legs read — but they may be in a
    # title, and nothing else in the payload can say so (§9.8). The OCR leg is
    # purely lexical, so a non-zero `ocr` count is lexical footing too: the note
    # claims "no transcript or on-screen line", and that claim has to be true.
    lexical = leg_counts["transcript_fts"] or (legs["ocr"] and leg_counts["ocr"])
    if legs["transcript"] and not browse and not lexical:
        await _note_title_footing(deps, q, video_pool, notes)
    elif (
        legs["transcript"]
        and legs["ocr"]
        and not browse
        and not leg_counts["transcript_fts"]
        and leg_counts["ocr"]
    ):
        # The slides carry these words and the speech does not — the split the
        # guide teaches, said where it is actually observed rather than only in
        # a resource the caller may never have read (F6). Structurally
        # exclusive with the title-footing note above: that one needs *no*
        # lexical footing anywhere, this one needs the OCR leg to have supplied
        # it.
        notes.append(
            "note: 0 transcript hits, but the on-screen text matched. Slides "
            "write identifiers, speech spells them out — for what was said "
            "about it, try the spoken phrasing."
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
    # at the default cap. And every step runs over the whole POOL, so the cap
    # BACKFILLS: `limit=6, max_per_video=1` used to return 3 results and assert
    # "no more results" because the cap was applied to a page of 6 already
    # fetched (research/demo-queries-2026-08-09.md §7.7).
    hits = _dedup_ocr_against_transcript(hits)
    hits = _collapse_same_frame(hits)
    _score_matches(hits, q)
    hits = _sort(hits, order)
    hits, truncated_videos = _cap_per_video(hits, max_per_video)

    total = len(hits)
    page = hits[offset : offset + limit]
    has_more = total > offset + limit

    if pool_full and not has_more:
        # Said before the past-the-end branch, not after it: an offset beyond a
        # full pool is the *most* misleading place to leave this out, because
        # the payload's only number is then a bounded count printed as if it
        # were the whole answer (2026-08-09 review).
        notes.append(
            f"note: ranking ran over the first {CANDIDATE_POOL} candidates per leg "
            "(the pool is bounded independently of limit, so the order never moves "
            "when you page) and the pool was full — deeper matches exist. Narrow "
            "with channel=, video_id=, published_after= or a more specific query "
            "to reach them."
        )

    if not hits:
        return await _empty_result(
            deps,
            q,
            content_type,
            flt,
            notes,
            reason=_nothing_matched(legs, content_type),
            limit=limit,
            offset=offset,
        )
    if not page:
        # Past the last page. This used to print `Results: 0/<offset>` and three
        # blank lines — strictly less help than a genuinely empty search gets
        # (§7.9). Say where the end is and how to get back to it.
        return _past_the_end(
            q, content_type, order, max_per_video, total, limit, offset, notes, pool_full
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
        total=total,
        pool_full=pool_full,
        leg_counts=leg_counts,
        truncated_videos=truncated_videos,
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
            # The number of results this query can actually deliver, counted
            # after dedup and after the per-video cap — not a probe over the
            # raw candidate CTE, which counted rows paging could never reach
            # and moved with the page it was asked for (§9.1.6). `pool_full`
            # is what makes it "approx": the pool is bounded.
            "approx_total": total,
            "pool_exhausted": pool_full,
        },
        # The same split the `Legs:` line prints, for the client that reads
        # structured content instead of prose: a conformant client should not
        # have to parse a header to learn that the lexical sub-leg matched
        # nothing (§4.2 of the terra eval).
        "leg_counts": leg_counts,
        "notes": notes,
    }
    nxt = _next_line(page)
    if nxt:
        structured["next"] = nxt
    if related is not None:
        structured["related_tags"] = related
    return text_result(body, structured)


# ---------------------------------------------------------------------------
# the private half: the `note:` lines, the legs, dedup, and scoring


async def _note_untagged_corpus(
    deps: Deps, tag_list: Sequence[str], notes: list[str]
) -> None:
    """A `tags=` filter emptied the pool: was the tag wrong, or is nothing tagged?

    Both answers are a clean, error-free `Results: 0/0`, and a round-3
    integration consumer graded that ambiguity the single most dangerous case
    on the surface — *"`tags=` is a real, documented, schema-listed parameter
    that silently does nothing on this corpus"* (§14.3). §3.7 already says a
    corpus with no tags does not advertise tags; this is the same rule applied
    to the filter rather than to the display, and it costs one bounded
    existence probe on a branch that has nothing else to spend.
    """
    if not tag_list:
        return
    if await deps.db.read(queries.any_video_tagged):
        return
    notes.append(
        f"note: no video in this corpus carries any tag, so tags="
        f"{','.join(tag_list)} could not match — this is an empty feature, not "
        "a misspelt tag. Tags are attached with tag-video; every other filter "
        "on this call is unaffected."
    )


async def _note_title_footing(
    deps: Deps, q: str | None, video_pool: Sequence[int], notes: list[str]
) -> None:
    """`fts 0`, and the words are in a TITLE: say so, and name the videos.

    A phrase that exists only in a video's title has no lexical footing in any
    leg — titles, descriptions and channel names are not in `cues_fts` or
    `ocr_frames_fts` — so `fts 0` is truthful and the semantic leg decides
    alone. Live: `q="on-call tax"` over a corpus containing a talk *named*
    "…without the on-call tax" put an unrelated talk at rank 1 and the
    eponymous one at 2-4 (terra eval §9.8). **The one place `search` cannot
    find a phrase is the title bar.**

    Why a `note:` and not a leg or a boost (the design call, 2026-08-11):

    - A `search` result is a **moment with a receipt** (§3.6): a timestamp and
      a `?t=` a user can open. A title matches the *video*, not a moment in it,
      so promoting it to a result means either inventing `t=0` — a fabricated
      moment, which §1's first rule forbids — or a result with no deep link,
      which no consumer of this surface is shaped to read.
    - A title *boost* into the RRF fusion is the defensible larger fix, and it
      is a **tuned constant over a scored ranking**, exactly the class of
      change the vec floors are: measured on a bench, per encoder, with a
      before/after (`research/vec-floor-calibration-2026-08-10.md`). Shipping
      an uncalibrated one at 3am to fix a rank-1 is how round 1's "an unrelated
      talk takes rank 1" happened in the first place, in the other direction.
    - What is *not* defensible is the silence. §2's `all` means all rule says a
      filter or content type that cannot apply to a leg prints a `note:` and
      never narrows quietly; a modality the legs structurally do not cover is
      the same promise. The note names the videos, so the caller gets the
      receipt this leg cannot rank, and points at `video_title=` — the
      parameter that already does this job correctly.

    Cost: one bounded FTS lookup (`LIMIT 3`), and only on a query that already
    came back `fts 0`, which is where there is nothing else to spend on.
    """
    rows = await deps.db.read(
        lambda c: queries.title_matches(c, video_pool, q, TITLE_FOOTING_MAX)
    )
    if not rows:
        return
    named = " · ".join(
        f'"{middle_truncate(str(r["title"]), 90)}" ({r["public_id"]})' for r in rows
    )
    many = len(rows) != 1
    notes.append(
        f"note: no transcript or on-screen line contains these words (fts 0), but "
        f"{len(rows)} video title{'s' if many else ''} {'do' if many else 'does'}: "
        f"{named}. Titles are not in the searched index, so a title match cannot "
        f"rank a moment and did not rank one here. "
        f'search video_title="…" filters by title; video-summary video_id="'
        f'{rows[0]["public_id"]}" opens the first one.'
    )


async def _note_embed_backlog(
    deps: Deps, legs: dict[str, bool], notes: list[str]
) -> None:
    """One `note:` per degraded vector leg, naming how many videos are waiting.

    Only when a leg actually ran — a leg the caller did not ask for is not
    degraded, it is absent, and it already has its own note. Costs one query
    over `video_stages` (a few thousand rows in a `WITHOUT ROWID` table at the
    500-video projection), and only on searches that use a vector leg.
    """
    backlog = await deps.db.read(queries.embed_backlog)
    for leg, key, what in (
        ("transcript", "text", "transcript"),
        ("frame", "frame", "frame"),
    ):
        if not legs[leg] or not backlog[key]:
            continue
        n = backlog[key]
        notes.append(
            f"note: {n} video{'s' if n != 1 else ''} in scope {'are' if n != 1 else 'is'} "
            f"waiting to be re-embedded after an embedding-model change, so the "
            f"{what} leg's semantic half searched only the videos that are "
            "current — keyword matching covered the rest. "
            'index-video force_reindex=false url="…" (or the overnight batch) '
            "backfills them; no download or transcription is involved."
        )


def _k_for(pool: int) -> int:
    """The vector legs' `k`, bounded and independent of the page.

    It used to be `offset + limit` times a constant, which is the mechanism
    behind the moving citation: a larger page pulled more raw cues out of the
    KNN, they joined the same island, and the island grew earlier.
    """
    return min(1000, max(50, pool * 2))


def _run_legs(
    conn: sqlite3.Connection,
    params: queries.SearchParams,
    legs: dict[str, bool],
    qimg: bytes | None,
    fetch_n: int,
    frame_max_distance: float,
    frame_max_margin: float,
    max_text_chars: int,
) -> tuple[list[Hit], dict[str, int], bool]:
    hits: list[Hit] = []
    # `transcript` is the FUSED count — what the leg contributed to the ranking.
    # The `*_fts` / `*_vec` / `*_vec_knn` keys are the sub-leg candidate counts
    # behind it, and they are what the guide's "`transcript 0` means the
    # phrasing differs" rule actually needs: a fused count next to a KNN leg
    # never reads 0 (research/mcp-eval-terra-2026-08-10.md §4.2). Units are each
    # sub-leg's own: cues for FTS, chunks for the vector legs, frames for the
    # frame leg.
    counts = {
        "transcript": 0,
        "ocr": 0,
        "frame": 0,
        "transcript_fts": 0,
        "transcript_vec": 0,
        "transcript_vec_knn": 0,
        "frame_vec": 0,
        "frame_knn": 0,
    }
    # Every leg fetches `pool + 1` rows (SearchParams.bind adds the +1), so a
    # leg that came back with more than the pool is a leg that had more to give.
    # That extra row is a *sentinel*: it is read for the flag and then dropped
    # (`_pool`), because keeping it made the pool 401 deep while every sentence
    # in the payload — the leg counts, `approx_total`, "the first 400
    # candidates per leg" — said 400 (2026-08-09 review).
    pool_full = False

    def _pool(rows: list) -> list:
        nonlocal pool_full
        pool_full = pool_full or len(rows) > fetch_n
        return rows[:fetch_n]

    if legs["transcript"]:
        rows = _pool(queries.search_transcript(conn, params))
        counts["transcript"] = len(rows)
        if rows:
            counts["transcript_fts"] = int(rows[0]["n_fts"])
            counts["transcript_vec"] = int(rows[0]["n_vec"])
            counts["transcript_vec_knn"] = int(rows[0]["n_vec_knn"])
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
                    anchor_s=float(row["anchor_s"]),
                    anchor_cue_id=int(row["anchor_cue_id"]),
                    anchor_text=str(row["anchor_text"] or ""),
                    n_legs=int(row["n_legs"]),
                )
            )

    if legs["ocr"]:
        rows = _pool(queries.search_ocr(conn, params))
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
                    # The leg matches whole frames now (§2.5), so `text` is the
                    # snippet FTS5 picked around the terms rather than every
                    # line on the slide: middle-truncating a full slide to
                    # `max_text_chars` is as likely to cut the match out as to
                    # keep it, and the dedup below compares this text against
                    # transcript cues. `max_text_chars=0` is the documented
                    # "give me everything" opt-out, so it still means the whole
                    # frame — and the frame id is in the hit either way.
                    text=str((row["text"] if max_text_chars == 0 else row["matched_text"]) or ""),
                    score=float(row["score"]),
                    cue_ids=[],
                    frame_id=str(row["frame_id"]),
                )
            )

    if legs["frame"] and qimg is not None:
        rows = _pool(
            queries.search_frames(
                conn,
                params,
                qimg,
                _k_for(fetch_n),
                frame_max_distance,
                frame_max_margin,
            )
        )
        counts["frame"] = len(rows)
        if rows:
            counts["frame_vec"] = int(rows[0]["n_kept"])
            counts["frame_knn"] = int(rows[0]["n_knn"])
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

    return hits, counts, pool_full


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


# Punctuation (and `_`, which `\w` counts and `str.isalnum` does not) to spaces.
# The character-by-character generator this replaces cost 170 ms of a 700 ms
# search once the candidate pool made it run over ~1,200 rows instead of ~30:
# normalization is now on the hot path for three passes (dedup, trigrams, match
# scoring), so it is one C-level `re.sub` per text.
_PUNCT = re.compile(r"[^\w\s]|_")


def _normalize(text: str) -> str:
    return " ".join(_PUNCT.sub(" ", text.casefold()).split())


def _norm_of(hit: Hit) -> str:
    """`_normalize(hit.text)`, memoized — three passes want the same string."""
    if hit._norm is None:
        hit._norm = _normalize(hit.text)
    return hit._norm


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
    # Indexed by video and normalized ONCE. The pool is 400 rows per leg, so
    # the old shape — re-normalizing and re-trigramming every transcript hit
    # for every OCR hit, across videos that can never pair — was O(n·m) string
    # allocation over a set that is now two orders of magnitude bigger.
    by_video: dict[int, list[Hit]] = {}
    for hit in hits:
        if hit.source == "transcript":
            by_video.setdefault(hit.video_id, []).append(hit)
    grams_of: dict[int, set[str]] = {}

    def grams_for(hit: Hit) -> set[str]:
        cached = grams_of.get(id(hit))
        if cached is None:
            cached = _trigrams(_norm_of(hit))
            grams_of[id(hit)] = cached
        return cached

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
        norm = _norm_of(hit)
        grams = grams_for(hit)
        for other in by_video.get(hit.video_id, ()):
            if not (other.start_s - 5.0 <= hit.start_s <= (other.end_s or other.start_s) + 5.0):
                continue
            other_norm = _norm_of(other)
            other_grams = grams_for(other)
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
                    other.n_legs += 1
                    absorbed.add(id(other))
                other._norm = None  # the text may have grown
                merged = True
                break
        if not merged:
            survivors.append(hit)
    return survivors


def _collapse_same_frame(hits: list[Hit]) -> list[Hit]:
    """One frame, one result — even when two legs found it.

    The OCR leg and the frame leg index the same keyframes, so a slide whose
    text matches AND whose picture is near the query vector arrived twice: same
    `frame_id`, same timestamp, same text, two slots and two bites of
    `max_per_video` (research/demo-queries-2026-08-09.md §7.4). Byte-identical
    *different* frames are collapsed one layer down, in SQL, on the index-time
    `phash`; this is the cross-leg half, and like the OCR/transcript collapse it
    is a fusion event: the survivor keeps both legs' RRF contributions, because
    two channels agreeing is the corroboration RRF exists to reward.

    It runs AFTER the OCR/transcript collapse, so a slide already absorbed into
    a narrated passage claims its frame id too — otherwise the picture came back
    once as `[transcript+ocr]` and again as `[frame]`, which is the same bug
    wearing the fix for the neighbouring one.
    """
    survivors: list[Hit] = []
    by_frame: dict[str, Hit] = {}
    for hit in hits:
        if hit.frame_id is None or hit.source == "transcript":
            survivors.append(hit)
            continue
        first = by_frame.get(hit.frame_id)
        if first is None:
            by_frame[hit.frame_id] = hit
            survivors.append(hit)
            continue
        first.score += hit.score
        first.n_legs += 1
        first.source = f"{first.source}+{hit.source}"
        # The OCR leg's text is the matched window of the slide; the frame
        # leg's is "visual match, no text hit" or the whole slide. Keep the
        # one that says something.
        if len(hit.text) > len(first.text) and first.text == "visual match, no text hit":
            first.text = hit.text
            first._norm = None
    return survivors


def _score_matches(hits: list[Hit], q: str | None) -> None:
    """How much of the query each hit's text actually contains.

    This is the evidence the fusion seam was missing. Every leg's rank 1 scores
    exactly `1/(60+1)`, so at the top of the payload RRF ties are the rule, not
    the exception — and the tie used to be broken by `public_id`, i.e.
    alphabetically by video id, so a CVE number present in exactly two frames in
    the corpus came back at ranks 3 and 4 behind two hits that did not contain it
    (research/demo-queries-2026-08-09.md §7.1, §9.2).

    Two signals, both computed over the *normalized* text (casefold, punctuation
    to spaces) so `CVE-2026-22812` compares equal to `cve 2026 22812`:
    `exact` — the whole query appears as a phrase — and `coverage` — the share of
    the query's terms present as whole tokens. Bounded work: one pass over a pool
    that is bounded independently of `limit`.
    """
    norm_q = _normalize(q or "")
    terms = [t for t in dict.fromkeys(norm_q.split()) if t]
    if not terms:
        return
    phrase = f" {norm_q} "
    for hit in hits:
        text = _norm_of(hit)
        tokens = set(text.split())
        hit.exact = 1 if len(terms) > 1 and phrase in f" {text} " else 0
        hit.coverage = round(sum(1 for t in terms if t in tokens) / len(terms), 2)


def _relevance_key(hit: Hit) -> tuple[Any, ...]:
    """Relevance before identity — the documented tie-break order (§3.10).

    1. the fused RRF score;
    2. the query as an exact phrase in the hit's text;
    3. the share of the query's terms the hit's text contains;
    4. how many rankers found it (FTS + vector, or two modalities agreeing).

    Only then `_sort_key`, which is arbitrary and exists solely to make the
    order total.
    """
    return (-hit.score, -hit.exact, -hit.coverage, -hit.n_legs)


def _sort_key(hit: Hit) -> tuple[Any, ...]:
    """The last resort: arbitrary, but *total*.

    Equal BM25 ranks and equal vector distances are ordinary — cues expanded
    from one chunk all share a distance — and Python's sort is stable over an
    input whose order came from an unordered set of legs. Without a total order
    the membership of the rows at the page boundary can change between two
    identical calls, which is exactly the guarantee `offset` depends on. It is
    the LAST key, never the first: on its own it is alphabetical by video id.
    """
    return (hit.public_id, hit.start_s, hit.source, hit.frame_id or "", hit.text[:64])


def _sort(hits: list[Hit], order: str) -> list[Hit]:
    if order == "recency":
        return sorted(
            hits, key=lambda h: (-(h.published_at or 0), *_relevance_key(h), *_sort_key(h))
        )
    if order == "video_time":
        return sorted(hits, key=lambda h: (h.start_s, *_relevance_key(h), *_sort_key(h)))
    return sorted(hits, key=lambda h: (*_relevance_key(h), *_sort_key(h)))


def _cap_per_video(hits: list[Hit], max_per_video: int) -> tuple[list[Hit], set[int]]:
    """ONE per-video cap, over the fused and deduplicated list.

    Applied after `_sort`, so which hits a dominant video keeps follows the
    ordering the caller asked for: the highest-scoring under `relevance` and
    `recency` (all hits from one video share a publish date), the earliest
    under `video_time`.

    Returns the video ids that actually **lost** a hit alongside the kept list.
    Reaching the cap and being bound by it are different facts: a video with
    exactly `max_per_video` hits reaches it and loses nothing, and telling that
    caller to "raise max_per_video for more from it" points at nothing (F10 —
    on a small corpus every search said it).
    """
    kept: list[Hit] = []
    seen: dict[int, int] = {}
    truncated: set[int] = set()
    for hit in hits:
        n = seen.get(hit.video_id, 0)
        if n >= max_per_video:
            truncated.add(hit.video_id)
            continue
        seen[hit.video_id] = n + 1
        kept.append(hit)
    return kept, truncated


def _keep_the_match(text: str, max_chars: int, needle: str) -> str:
    """Truncate around the matched passage instead of through it.

    Middle-truncation assumes the signal is at both ends of a sentence, which is
    right for one cue and wrong for a clustered segment: at
    `max_text_chars=400` a two-minute cluster came back with the matched phrase
    cut out of the middle, so the result showed neither the words that matched
    nor a timestamp near them (research/demo-queries-2026-08-09.md §7.5). This
    is the transcript-side spelling of what the OCR leg gets from FTS5's
    `snippet()`: a window that is guaranteed to contain the match.

    Falls back to `middle_truncate` when there is no needle or it is not in the
    text — the marker, the budget and the `0` opt-out are the same either way.
    """
    if max_chars == 0 or len(text) <= max_chars:
        return text
    where = text.find(needle) if needle else -1
    if where < 0 or len(needle) >= max_chars:
        return middle_truncate(text, max_chars)
    # Centre the window on the needle, then slide it inside the text so a match
    # near either end still spends the whole budget on context.
    start = max(0, where - (max_chars - len(needle)) // 2)
    start = min(start, len(text) - max_chars)
    end = start + max_chars
    out = text[start:end]
    if start:
        out = TRUNCATION_MARKER.format(n=start) + out
    if end < len(text):
        out = out + TRUNCATION_MARKER.format(n=len(text) - end)
    return out


def _shown_text(hit: Hit, max_text_chars: int) -> str:
    return _keep_the_match(hit.text, max_text_chars, hit.anchor_text)


def _as_dict(deps: Deps, hit: Hit, max_text_chars: int) -> dict[str, Any]:
    return {
        "source": hit.source,
        "video_id": hit.public_id,
        "title": hit.title,
        "channel": hit.channel,
        "start": round(hit.start_s, 2),
        "end": round(hit.end_s, 2) if hit.end_s is not None else None,
        # The moment the link points at: inside the segment, at the cue that
        # actually matched. Equal to `start` for the point-in-time legs.
        "match_start": round(hit.cite_s, 2),
        "match_cue_id": hit.anchor_cue_id,
        "text": _shown_text(hit, max_text_chars),
        "link": deeplink(hit.public_id, hit.cite_s, deps.settings.deeplink_lead_s),
        "cue_ids": hit.cue_ids,
        "frame_id": hit.frame_id,
        "score": round(hit.score, 4),
    }


def _legs_line(counts: dict[str, int]) -> str:
    """`Legs:` — the fused count per leg, and the sub-legs behind it.

    The transcript leg is FTS ∪ KNN, and printing only the fused number killed
    the one diagnostic `vidtheque://guide` tells callers to use: with a KNN leg
    returning its k, `transcript` never reads 0, so "the phrasing differs" and
    "the topic is unspoken" look identical
    (research/mcp-eval-terra-2026-08-10.md §4.2). `fts 0` says it in one token.

    `a/b` is kept/considered: how many candidates survived the relevance band
    out of the k nearest the KNN returned (§4.1's floor). It is printed
    **always**, including `800/800`. Printing it only when the band bit made
    the one case a caller most needs to notice — a semantic pool that was not
    narrowed at all — read like the tidiest one, because `vec 800` and
    `vec 800/800` are the same four characters of difference in the opposite
    direction (terra eval §9.6).

    The three numbers are three units, and they are labelled, because nothing
    in the payload said so and the guide's example (`transcript 24 (fts 9 ·
    vec 15/800)`) added up by coincidence — so `fts 369` beside a fused `130`
    read as an arithmetic bug, or worse, as "369 talks say this" (§9.2). The
    fused count is segments (post-cluster, what the leg contributed to the
    ranking); `fts` counts cues; `vec` counts chunks. They are not summands.
    """
    transcript = ""
    if counts.get("transcript_fts") or counts.get("transcript_vec"):
        vec = str(counts["transcript_vec"])
        if counts.get("transcript_vec_knn", 0):
            vec += f"/{counts['transcript_vec_knn']}"
        transcript = (
            f" (fts {counts['transcript_fts']} {_unit(counts['transcript_fts'], 'cue')}"
            f" · vec {vec} chunks)"
        )
    frame = ""
    if counts.get("frame_knn", 0):
        frame = f" (vec {counts['frame_vec']}/{counts['frame_knn']})"
    fused = counts["transcript"]
    return (
        f"Legs: transcript {fused} {_unit(fused, 'segment')}{transcript} · "
        f"ocr {counts['ocr']} · frame {counts['frame']}{frame} (fused, RRF k=60)"
    )


def _unit(n: int, word: str) -> str:
    return word if n == 1 else word + "s"


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
    total: int,
    pool_full: bool,
    leg_counts: dict[str, int],
    notes: list[str],
    max_text_chars: int,
    related: dict[str, int] | None,
    truncated_videos: set[int],
    fmt: str,
    fields: str,
) -> str:
    if pool_full and not has_more:
        # "(no more results)" would be the §7.7 lie in a new place: the pool ran
        # out, the corpus did not. Name which one ended.
        first = f"Results: {len(page)}/{total} (end of the ranked pool)"
    else:
        first = pagination_line(
            "Results", len(page), offset, limit, has_more, total, pool_full
        )
    header = [
        first,
        f'Query: "{q or "*"}" · content_type={content_type} · order={order} · '
        f"max_per_video={max_per_video}",
        _legs_line(leg_counts),
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

    lead = deps.settings.deeplink_lead_s
    blocks: list[str] = []
    for hit in page:
        link = deeplink(hit.public_id, hit.cite_s, lead)
        when = clock(hit.start_s) + (f"–{clock(hit.end_s)}" if hit.end_s else "")
        # A clustered segment is a span; the link is a point inside it. Name the
        # point, or the payload looks inconsistent with its own link.
        if hit.anchor_s is not None and int(hit.anchor_s) != int(hit.start_s):
            when += f" · match at {clock(hit.anchor_s)}"
        lines = [
            f"[{hit.source}] {hit.title} — {hit.channel or 'unknown'} ({hit.public_id})",
            f"  {when} · {link}",
            "  " + _shown_text(hit, max_text_chars).replace("\n", " "),
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
    truncated_here: set[str] = set()
    for hit in page:
        per_video[hit.public_id] = per_video.get(hit.public_id, 0) + 1
        if hit.video_id in truncated_videos:
            truncated_here.add(hit.public_id)
    # Only when the cap actually dropped a hit from that video. `n >=
    # max_per_video` alone means "reached", and on a corpus small enough that
    # one video holds every match it is reached on every search — printing
    # "raise max_per_video for more from it" when there is no more.
    dominant = [
        v
        for v, n in per_video.items()
        if n >= max_per_video and v in truncated_here
    ]
    if dominant:
        footer.append(
            f"{max_per_video} of {len(page)} results came from {dominant[0]} "
            f"(max_per_video={max_per_video} bound). Raise max_per_video for more from it."
        )
    if max_text_chars and any(len(h.text) > max_text_chars for h in page):
        footer.append(
            f"Text truncated at {max_text_chars} chars, around the matched "
            "passage — pass max_text_chars=0 for full text."
        )
    if related:
        footer.append(
            "related tags: " + " · ".join(f"{t} {n}" for t, n in list(related.items())[:12])
        )
    nxt = _next_line(page)
    if nxt:
        footer.append(nxt)
    return "\n".join(header) + body + ("\n" + "\n".join(footer) if footer else "")


def _next_line(page: Sequence[Any]) -> str | None:
    """The closing `next:` line, so the text block and `structuredContent` agree.

    Two next steps, because the bench showed the single get-segment-context hint
    pulling models into window-walking when the question was "where does this
    video discuss X" — a chapter list answers that in one call.
    """
    if not page:
        return None
    first = page[0]
    return (
        f'next: video-summary video_id="{first.public_id}" for the chapter '
        "list (fastest way to name the moment), or "
        f'get-segment-context video_id="{first.public_id}" '
        f"t={int(first.start_s)} for the full surrounding transcript."
    )


def _and_list(names: list[str]) -> str:
    return " and ".join([", ".join(names[:-1]), names[-1]] if len(names) > 1 else names)


def _nothing_matched(legs: dict[str, bool], content_type: str) -> str:
    """The empty-state reason line, derived from the legs that actually ran.

    It was the constant "Every leg was queried and none of them matched." — a
    sentence that is false whenever the caller pinned `content_type`, and that
    could contradict a `note:` four lines above it in the same payload saying
    the semantic legs were skipped (research/demo-queries-2026-08-09.md §7.10,
    §9.1.5). `all` means all, and saying so when it did not is the same lie in
    the other direction. It also says *why* the others sat out, because the two
    reasons want different next steps: a pinned `content_type` is the caller's
    own doing and has no `note:` to read, everything else has one.
    """
    ran = [name for name, on in legs.items() if on]
    missing = [name for name, on in legs.items() if not on]
    if not ran:
        return "No leg was queried — every leg was ruled out by the filters above."
    if not missing:
        return "All three legs were queried and none of them matched."
    was = "leg was" if len(ran) == 1 else "legs were"
    why = (
        f"you pinned content_type={content_type}, so the "
        f"{_and_list(missing)} {'leg' if len(missing) == 1 else 'legs'} did not run — "
        "omit it to query all three"
        if content_type != "all"
        else f"the {_and_list(missing)} {'leg' if len(missing) == 1 else 'legs'} "
        "did not run, for the reason in the note above"
    )
    return f"The {_and_list(ran)} {was} queried and nothing matched; {why}."


def _past_the_end(
    q: str | None,
    content_type: str,
    order: str,
    max_per_video: int,
    total: int,
    limit: int,
    offset: int,
    notes: list[str],
    pool_full: bool,
) -> CallToolResult:
    """`offset` past the last result: say where the end is, do not go quiet.

    Over-paging used to print `Results: 0/<offset>` — a "total" equal to the
    offset — the query echo, the leg counts, and then two blank lines: strictly
    less help than a genuinely empty search gets
    (research/demo-queries-2026-08-09.md §7.9, §9.1.6).

    ``pool_full`` travels with the count for the reason it does everywhere
    else: `total` is the length of the **bounded ranked pool**, not of the
    corpus. This path used to drop the flag, so an offset past a full pool was
    the one response that printed a bounded count as a complete one, with no
    `pool_exhausted` and no narrowing note to correct it (2026-08-09 review) —
    the exact case where a caller is most likely to read "there is nothing
    past here" as a fact about the corpus.
    """
    last = last_page_offset(total, limit)
    end = "past the last page of the ranked pool" if pool_full else "past the last page"
    holds = (
        f"The ranked pool holds {total} result{'s' if total != 1 else ''} and it "
        f"filled, so deeper matches exist; the last page of the pool starts at "
        f"offset={last}."
        if pool_full
        else (
            f"This query has {total} result{'s' if total != 1 else ''}; the last "
            f"page starts at offset={last}."
        )
    )
    nxt = f"next: re-run with offset={last}, or offset=0 for the top of the ranking."
    lines = [
        f"Results: 0/{total} ({end})",
        f'Query: "{q or "*"}" · content_type={content_type} · order={order} · '
        f"max_per_video={max_per_video}",
        *notes,
        "",
        holds,
        nxt,
    ]
    return text_result(
        "\n".join(lines),
        {
            "results": [],
            "pagination": {
                "limit": limit,
                "offset": offset,
                "has_more": False,
                "approx_total": total,
                "pool_exhausted": pool_full,
                "last_offset": last,
            },
            "notes": notes,
            "reason": holds,
            "next": nxt,
        },
    )


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
        hint = deps.hint(
            "index-video",
            'index-video url="https://youtu.be/…" to add your first video.',
            "nothing is indexed on this read-only server, and it exposes no tool "
            "that can add a video.",
        )
    else:
        # "index fresh" was hard-coded, and was the third of three contradicting
        # answers about the queue in one session (demo-queries §9.1.4). Both the
        # word and the clause now come from the derivation `corpus-summary` and
        # `vidtheque://context` read — restricted to the *activity* axis, since
        # this line is about whether the index is settled, not about coverage.
        state = await corpus_state.read_corpus_state(deps, total)
        status = (
            f"{state.activity_word()} (corpus has {total} videos, newest published "
            f"{_day(rollup['newest_published'])}, {state.freshness()})"
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
            # The empty state's whole value is *which* empty state it is: no
            # video survived the filters, or the legs ran and nothing matched,
            # or no word of the query is in the corpus at all. That distinction
            # lived only in the text block, and a structured-only consumer
            # called the resulting ambiguity "the single most dangerous case in
            # the whole surface" (round-3 eval §14.2) — four different causes
            # returning one indistinguishable payload.
            "reason": reason,
            "next": f"next: {hint}",
            "data_status": status.split()[0],
        },
    )


def _day(ts: Any) -> str:
    from ..text import iso_day

    return iso_day(int(ts) if ts else None)
