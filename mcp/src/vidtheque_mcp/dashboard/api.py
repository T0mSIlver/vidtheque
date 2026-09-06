"""`/dashboard/api/*` — the JSON the React dashboard reads
(`docs/design/frontend-migration.md`).

Since 2026-09-06 this module is the whole of what `/dashboard` answers with,
apart from the writes: the Jinja pages are gone, `views.py` with them, and the
three handlers that were the scripts' poll targets — the jobs list, one job's
war story and the cue pager — moved here rather than being deleted, because
the React pages read all three.

Additive reads, and they add no query and no policy: `overview`, `ledger`,
`library`, `library/{video_id}`, `following` and `following/{slug}` are
`read_models`' assemblers — the same reads the Jinja pages make, in the same
order, under the same projection and the same server-side clamps — shaped into
typed JSON, and `session` is what a browser needs before it can decide whether
to render a dashboard or a sign-in link.

The two following routes are the one pair here that is **not always
registered**: they are declared with the write routes, because their pages are
(dashboard.md §18.6), so a deployment with no write side 404s them exactly as
it 404s the pages. Negotiating a medium must never be a way to reach a surface
the deployment decided not to serve.

`library` rather than `videos` because `/dashboard/api/videos` is taken: the
`/api/*` facade is registered under this prefix too (§2.5.1), and its listing
is the corpus's own shape — the records `/api/videos` serves the demo, with
`published` and `duration` already rendered. One path cannot be two contracts,
and the two are answering different questions: the facade's is "what is in the
corpus", this one's is "what does the management table show", which needs the
index state, the coverage booleans, the exact filtered count and the epochs the
tool spent on prose.

Three rules this module keeps, all of them settled:

* **Typed values, formatted at the edge.** Counts are integers, durations and
  clocks are seconds since the epoch, states are the store's own words, and
  nothing here renders "4m 12s" or "3 hours ago" — that is React's half now.
  What stays Python's is *policy text*: refusal codes, messages and their
  `next:` line, and the redaction itself.
* **The projection is what is absent.** In `VIDTHEQUE_PUBLIC_READONLY=1` the
  operator's box is not sent with a flag beside it; the reads behind it are
  never taken (`read_models.redacted`). A client cannot un-redact a field that
  is not in the payload.
* **Nothing is cacheable.** `no-store` on all three, exactly as the pages have
  always answered, because they describe state that changes under the reader.

`overview` and `ledger` sit behind the route group's read gate, like the pages
and like `/dashboard/api/*`. `session` deliberately does **not**: a signed-out
browser has to be able to ask what this deployment expects of it, and the 401
page has been telling an anonymous caller the auth mode and the sign-in hint
since phase 1 — so the endpoint publishes that same pair and nothing else. No
secret, no path, no model id, no URL of the operator's own infrastructure, and
`signed_in` is the *validated* session, never the presence of a cookie; the
cookie's mere presence is the separate `has_session_cookie`.
"""

from __future__ import annotations

import asyncio
import sqlite3
import time
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from .. import __version__
from ..auth.credential import credential, is_owner
from ..auth.login import SESSION_COOKIE
from ..db import queries
from ..errors import HTTP_STATUS
from ..public.api import OWNER_CLAMPS, PUBLIC_CLAMPS
from ..text import clamp
from .access import peer_trusted, sign_in_hint, write_side_enabled
from .read_models import (
    BUDGET_WINDOW_S,
    CHECK_CAP,
    CUE_PAGE,
    CUE_PAGE_MAX,
    FAILED_WINDOW_S,
    FRAME_PAGE,
    FRAME_PAGE_MAX,
    GAPS_FAILED_CAP,
    HELD_BAND_CAP,
    INDEX_JOB_CAP,
    NEAR_MISS_S,
    OCR_LINE_CAP,
    POLL_MS,
    SHOT_CAP,
    VIDEO_HISTORY_CAP,
    LedgerReads,
    OverviewReads,
    VideosReads,
    clamp_note,
    coverage_flags,
    declared_models,
    drift_reason,
    follow_detail_reads,
    follow_row_json,
    follow_row_json_with_error,
    following_reads,
    job_detail_reads,
    jobs_reads,
    ledger_reads,
    near_miss,
    overview_reads,
    pipeline_readiness,
    redacted,
    video_detail_reads,
    video_header,
    videos_reads,
)
from .settings import ROOT

# Every response on this surface. A management payload describes state that
# changes under the reader, and a shared cache must never hold it.
NO_STORE = {"Cache-Control": "no-store"}


def _json(payload: dict[str, Any], status: int = 200) -> JSONResponse:
    return JSONResponse(payload, status_code=status, headers=NO_STORE)


def _refusal(error: dict[str, Any], *, echo: dict[str, Any] | None = None) -> JSONResponse:
    """A tool's typed refusal, as the envelope `/api/*` already answers with.

    `echo` is what the server had already resolved when it refused, merged in
    beside the three envelope fields under the names the success payload uses
    — today only the videos table's `filters` (§20). A refusal that drops the
    resolved query leaves the controls that composed it with nothing to render
    but what was typed, which is the one reading the server has just said is
    not what it ran.
    """
    code = str(error.get("code") or "E_INTERNAL")
    payload: dict[str, Any] = {
        "error": code,
        "message": error.get("message") or "the query layer refused this request.",
        "next": error.get("next"),
    }
    if echo:
        payload.update(echo)
    return _json(payload, status=HTTP_STATUS.get(code, 500))


def _epoch(value: Any) -> int | None:
    """A stored unix stamp as an int, or `None` when the corpus has none."""
    return None if value is None else int(value)


def _seconds(value: Any) -> float | None:
    return None if value is None else float(value)


def _readiness(readiness: dict[str, Any]) -> dict[str, Any]:
    """The pipeline observation, field by field rather than passed through.

    Copied out instead of forwarded because of the shape: a field added to the
    assembler's dict would otherwise join this contract the day it is written,
    which is how an operator-only value reaches a payload nobody re-reviewed.
    `worker` stays `None` whole in the projection - the probe was never made.
    """
    worker = readiness["worker"]
    return {
        "mcp": readiness["mcp"],
        "database": readiness["database"],
        "vectors": {
            "enabled": bool(readiness["vectors"]["enabled"]),
            "reason": readiness["vectors"]["reason"],
        },
        "worker": None
        if worker is None
        else {
            "state": str(worker["state"]),
            # Policy text, and deliberately still Python's: it is the sentence
            # that says what the boundary did, not a rendering of a number.
            "detail": str(worker["detail"]),
            "models": [
                {
                    "task": str(model["task"]),
                    "model": str(model["model"]),
                    "loaded": bool(model["loaded"]),
                }
                for model in worker["models"]
            ],
        },
        "checked_at": readiness["checked_at"],
    }


# ------------------------------------------------------------------- overview


async def overview(request: Request) -> Response:
    """`GET /dashboard/api/overview` — what the corpus holds, and what it is doing.

    The overview page's own reads (`read_models.overview_reads`), typed. The
    lists are the page's lists and carry its caps: `CHANNEL_CAP` channels,
    `TAG_CAP` tags, `RECENT_CAP` arrivals, all applied in the assembler and none
    of them reachable from the query string — this endpoint takes no parameters
    at all, so there is nothing to clamp and nothing a caller can widen.

    The worker probe runs concurrently with the database reads, the way the
    page runs it: a down worker costs at most the remainder of
    `WORKER_STATUS_TIMEOUT_S`, and an unwind must not orphan a task holding an
    open HTTP request.
    """
    redact = redacted(request)
    task = asyncio.create_task(pipeline_readiness(request, redact=redact))
    try:
        data: OverviewReads = await overview_reads(request, task, redact=redact)
    finally:
        if not task.done():
            task.cancel()
    if data.error is not None:  # pragma: no cover - corpus_summary has no error path
        return _refusal(data.error)

    corpus = data.corpus or {}
    rollup = data.rollup
    gaps = corpus.get("gaps") or {}
    backlog = corpus.get("embed_backlog") or {}
    health = data.health or {}
    payload: dict[str, Any] = {
        "counted_at": int(time.time()),
        "redacted": redact,
        # Whether this instance may write at all — `/api/session`'s field, the
        # same boolean off the same `Database`, and it rides here for the
        # reason §19 gives below: the Indexing statepair and the drift banner
        # are on this payload's own page, and a rendering that waits on a
        # second request to learn a deployment fact is a rendering that flips
        # under the reader. Not redacted, because `/api/session` publishes it
        # to an anonymous browser already; the *reason* stays behind
        # `drift_reason`, which is the sentence about the operator's box.
        "writes_allowed": bool(request.app.state.assembled.db.writes_allowed),
        "corpus": {
            "videos": int(corpus.get("videos") or 0),
            "queryable_videos": int(corpus.get("queryable_videos") or 0),
            # Ready **only**, and it is not `queryable_videos` minus something:
            # `stale` answers a query and is not ready, so the two numbers are
            # different questions and the band that says "N ready" means this
            # one. It is `corpus_rollup`'s own column, the same field the
            # ledger's `videos_by_state.ready` is read from.
            "videos_ready": int(rollup["videos_ready"] or 0),
            # The store's own words as keys, so a state the schema grows later
            # arrives here the day it is added.
            "videos_by_index_state": {
                str(state): int(n)
                for state, n in (corpus.get("videos_by_index_state") or {}).items()
            },
            # `data_status` verbatim from `corpus-summary`, never re-derived
            # (index-schema §4.5).
            "data_status": str(corpus.get("data_status") or ""),
            "cues": int(rollup["cues"] or 0),
            "keyframes": int(rollup["keyframes"] or 0),
            "ocr_lines": int(rollup["ocr_lines"] or 0),
            # Seconds only. `corpus_rollup` also carries `hours`, and its own
            # comment calls that a display rounding - deriving seconds back out
            # of the 0.1-rounded figure once reported a 149 s corpus as 0
            # (research/e2e-smoke-2026-08-08.md 4.6). Sending both would put
            # that rounding on the wire for React to render.
            "duration_s": float(rollup["duration_s"] or 0.0),
            "published": {
                "oldest": _epoch(rollup["oldest_published"]),
                "newest": _epoch(rollup["newest_published"]),
            },
            "last_indexed": _epoch(rollup["last_indexed"]),
        },
        "channels": [
            {
                "channel": str(row.get("channel") or ""),
                "videos": int(row.get("videos") or 0),
                "seconds": float(row.get("seconds") or 0.0),
            }
            for row in corpus.get("channels") or []
        ],
        # A list, not the tool's object: a tag is a string a client must not
        # have to trust as a JSON key, and the order here is the rollup's
        # (most-used first), which an object would leave to the reader.
        "tags": [
            {"tag": str(tag), "videos": int(n)}
            for tag, n in (corpus.get("tags") or {}).items()
        ],
        "gaps": {
            "transcript_no_ocr": int(gaps.get("transcript_no_ocr") or 0),
            "indexing": int(gaps.get("indexing") or 0),
            # A *count* of failed videos. The rows behind it carry
            # `video_stages.error`, which is the pipeline's prose about the
            # operator's box, and they reach no surface from here.
            "failed": int(gaps.get("failed") or 0),
            # …and the count is the length of a list `queries.gaps` probes
            # with `LIMIT 5`, so at the ceiling it means "five or more". The
            # cap travels as a number and the reading of it as a boolean: a
            # client renders `5+` off the pair, and a `5` hard-coded in a
            # template is how a cap gets reported as an exact count the day
            # the `LIMIT` changes.
            "failed_cap": GAPS_FAILED_CAP,
            "failed_capped": int(gaps.get("failed") or 0) >= GAPS_FAILED_CAP,
        },
        "embed_backlog": {
            "text": int(backlog.get("text") or 0),
            "frame": int(backlog.get("frame") or 0),
        },
        "jobs": {
            "active": int(health.get("active") or 0),
            "running": int(health.get("running") or 0),
            "deferred": int(health.get("deferred") or 0),
            "failed_recent": int(health.get("failed_recent") or 0),
            # The window the count was taken over, so the client's sentence and
            # the query behind it cannot disagree.
            "failed_window_s": FAILED_WINDOW_S,
        },
        "recent": [
            {
                "video_id": str(row["video_id"]),
                "title": str(row["title"]),
                "channel": str(row["channel"]),
                "duration_s": _seconds(row["duration_s"]),
                "indexed_at": _epoch(row["indexed_at"]),
                "thumb": row["thumb"],
            }
            for row in data.recent or []
        ],
        # The vector state and the worker probe, exactly as the page's panel
        # gets them: `vectors.reason` and the whole worker block are already
        # `None` in the projection, because `pipeline_readiness` never asked.
        "readiness": _readiness(data.readiness),
        # Dropped by the projection, both of them, by not being read (§2.4).
        "declared_models": None if redact else declared_models(
            request.app.state.assembled.db.config
        ),
        "storage": None
        if data.storage is None
        else {
            "keyframe_bytes": int(data.storage["keyframes"]),
            "database_bytes": int(data.storage["database"]),
        },
    }
    return _json(payload)


# --------------------------------------------------------------------- ledger


async def ledger(request: Request) -> Response:
    """`GET /dashboard/api/ledger` — every key number this instance can count.

    The ledger page's reads (`read_models.ledger_reads`), typed: a fixed number
    of whole-table and index counts, no per-video work, and no parameters.
    """
    redact = redacted(request)
    task = asyncio.create_task(pipeline_readiness(request, redact=redact))
    try:
        data: LedgerReads = await ledger_reads(request, task, redact=redact)
    finally:
        if not task.done():
            task.cancel()
    rollup = data.rollup
    row = data.ledger
    health = data.health
    payload: dict[str, Any] = {
        # Every figure was counted inside this request — there is no cache and
        # no sample behind any of them, so the payload carries one clock.
        "counted_at": int(time.time()),
        "redacted": redact,
        # The overview's field, same name and same source (§19): the ledger
        # draws the same drift banner, and one fact must not have two
        # spellings across two payloads that answer for one deployment.
        "writes_allowed": bool(request.app.state.assembled.db.writes_allowed),
        "corpus": {
            # ready + the four not-ready states, which add up to this by
            # construction (`_CORPUS_SQL`'s `<> 'ready'`).
            "videos": int(rollup["videos_ready"]) + int(rollup["videos_pending"]),
            # Seconds only, for the reason the overview gives above.
            "duration_s": float(rollup["duration_s"] or 0.0),
            "cues": int(rollup["cues"] or 0),
            "keyframes": int(rollup["keyframes"] or 0),
            "ocr_lines": int(rollup["ocr_lines"] or 0),
            "chunks": int(row["chunks"] or 0),
            "tags": int(row["tags"] or 0),
            "channels": int(row["channels"] or 0),
            # The band under the video count prints "published <oldest> –
            # <newest>", and this payload had no field for it — the same
            # `corpus_rollup` the counts above come from was already carrying
            # both stamps. Same name and same shape as the overview's, because
            # a client reading both must not have to learn two spellings of one
            # fact; `null` on both halves when the corpus is empty.
            "published": {
                "oldest": _epoch(rollup["oldest_published"]),
                "newest": _epoch(rollup["newest_published"]),
            },
            "last_indexed": _epoch(rollup["last_indexed"]),
        },
        "videos_by_state": {
            "ready": int(rollup["videos_ready"] or 0),
            "pending": int(row["videos_pending"] or 0),
            "indexing": int(row["videos_indexing"] or 0),
            "failed": int(row["videos_failed"] or 0),
            "stale": int(row["videos_stale"] or 0),
        },
        "jobs_by_state": {
            str(state): int(n) for state, n in data.jobs_by_state.items()
        },
        "queue": {
            "active": int(health["active"]),
            "running": int(health["running"]),
            "deferred": int(health["deferred"]),
            "failed_recent": int(health["failed_recent"]),
            "failed_window_s": FAILED_WINDOW_S,
        },
        "embed_backlog": {
            "text": int(data.backlog["text"]),
            "frame": int(data.backlog["frame"]),
        },
        # One figure out of `gaps()`, the same one the page takes: the rest of
        # that read is either counted more precisely above or is the failed-stage
        # rows, whose `error` text belongs to nobody but the operator.
        "gaps": {"transcript_no_ocr": int(data.gaps["transcript_no_ocr"])},
        "readiness": _readiness(data.readiness),
        "storage": None
        if data.storage is None
        else {
            "keyframe_bytes": int(data.storage["keyframes"]),
            "database_bytes": int(data.storage["database"]),
        },
    }
    return _json(payload)


# --------------------------------------------------------------------- videos


def _video_row(row: dict[str, Any]) -> dict[str, Any]:
    """One table row: the tool's record, with its two rendered columns undone.

    `list-videos` writes `published` as an `iso_day` string and `duration` as a
    `1:56:40` clock, because its reader is a model reading a `tsv` block. This
    surface sends the stamps `read_models.video_facts` read beside them, and
    React renders. `coverage` is the same three booleans the page draws as
    pills, keyed rather than lettered — the letters are the text block's device.
    """
    typed = row.get("typed") or {}
    return {
        "video_id": str(row["video_id"]),
        "title": str(row["title"]),
        "channel": str(row.get("channel") or ""),
        "published_at": _epoch(typed.get("published_at")),
        "duration_s": _seconds(typed.get("duration_s")),
        "indexed_at": _epoch(typed.get("indexed_at")),
        # The schema's own word, printed verbatim and never re-derived
        # (index-schema §4.5).
        "index_state": str(row["index_state"]),
        "coverage": coverage_flags(str(row.get("coverage") or "---")),
        "tags": list(row.get("tag_list") or []),
        # Relative, like every frame URL on this surface: a browser reading
        # this dashboard knows the host it fetched from, and `PUBLIC_URL` is
        # the thing that was wrong behind a tunnel (dashboard.md §8).
        "thumb": row.get("thumb"),
        "link": row.get("link"),
    }


def _filters_block(data: VideosReads) -> dict[str, Any]:
    """The query as the server resolved it — on the table and on its refusal.

    One function for both, so the payload that says "here are your rows" and
    the payload that says "that filter will not parse" describe the query the
    same way.
    """
    return {
        "q": data.filters["q"] or None,
        "channel": data.filters["channel"] or None,
        "tags": data.tags,
        "has": data.filters["has"],
        "index_state": data.filters["index_state"],
        # The two axes, never overloaded: `published_*` picks videos,
        # `offset_*` picks positions inside one and appears nowhere here.
        # These are the epochs the query actually filtered on, so each
        # `_after` is the start of its UTC day and each `_before` is the
        # start of the day *after* the one asked for — the bound is
        # exclusive, which is what makes `published_before` include its own
        # date.
        "published_after": _epoch(data.resolved.get("published_after")),
        "published_before": _epoch(data.resolved.get("published_before")),
        "indexed_after": _epoch(data.resolved.get("indexed_after")),
        "indexed_before": _epoch(data.resolved.get("indexed_before")),
    }


async def videos(request: Request) -> Response:
    """`GET /dashboard/api/library` — the videos table (§5.2), typed.

    **Not `/dashboard/api/videos`**, and the name is the only thing about this
    endpoint that is not the page's: that path is already the *facade's*
    listing at this prefix (`public/api.videos_endpoint`), and one path cannot
    carry two contracts. The facade answers a question about the corpus in the
    corpus's own shape — the same records `/api/videos` serves the demo, with
    `published` and `duration` rendered for a reader of the tool's text block.
    This answers "what does the management table show", which needs six things
    the facade has no reason to carry: `index_state` (the facade lists only
    what is queryable and never says which), the coverage booleans, the exact
    count of the filtered set, the `has`/`tags`/`index_state`/date filters, an
    explicit `order`, and epochs instead of rendered days.

    Every parameter the page takes, under the same server-side clamps, because
    they are the same call: `read_models.videos_reads`. A `limit` above the cap
    is clamped and the payload says so in `notes` — the page echoes its clamps
    back into its own form, and a JSON caller has no form to read them out of.
    """
    data: VideosReads = await videos_reads(request)
    if data.error is not None:
        # The resolved query rides on the refusal too (§20, 2026-09-06). Both
        # refusals this route can answer with — a date `parse_corpus_time`
        # will not take, and `list-videos`' own, `E_ORDER_SCOPE` among them —
        # are raised after `videos_reads` has resolved every filter, so the
        # block is the same block under the same name. A date picker showing
        # the day the server ran on has nothing else to read it out of, and
        # the bound that *failed* is the one that reads `null`, which is the
        # honest answer to "which day did this filter become".
        return _refusal(data.error, echo={"filters": _filters_block(data)})
    pagination = data.pagination
    payload: dict[str, Any] = {
        "counted_at": int(time.time()),
        # The videos table is *not* redacted — §2.4 gives it to the demo whole,
        # because everything on it is corpus rather than deployment. The flag
        # is here so a client can tell which projection answered it.
        "redacted": redacted(request),
        # Explicit, always, and never inferred from the presence of `q`: the
        # default is `relevance` with a query and `recency` without one.
        "order": data.order,
        "filters": _filters_block(data),
        "videos": [_video_row(row) for row in data.rows],
        "pagination": {
            "limit": data.limit,
            "offset": data.offset,
            "has_more": bool(pagination.get("has_more")),
        },
        # The exact count of the filtered set, which is this page's deliberate
        # divergence from `has_more` alone (§5.2): a tilde above a table with a
        # Next button is the one thing on the line a reader cannot act on. The
        # tool's own bounded probe (`approx_total`) is not forwarded — two
        # totals with different rules is how a client picks the wrong one.
        "total": data.total,
        "notes": data.notes,
    }
    # Where the last page starts, when a caller has walked past the end. The
    # tool computes it; forwarded rather than recomputed.
    if pagination.get("last_offset") is not None:
        payload["pagination"]["last_offset"] = int(pagination["last_offset"])
    return _json(payload)


async def video(request: Request) -> Response:
    """`GET /dashboard/api/library/{video_id}` — the detail page (§5.3), typed.

    The panels the page shows, minus the transcript: cues are
    `/dashboard/api/videos/{video_id}/cues`, which already pages them under the
    same clamps, and a detail payload that carried a page of them too would be
    two contracts for one list. The keyframe strip *is* here, paged by `frames`
    and `frame_offset`, because nothing else serves it and it is the panel §5.3
    calls the most convincing thing on the page.

    Why the facade is not enough (`/api/videos/{video_id}`, demo-site.md
    §2.2.1): it is `video-summary`'s payload — chapters, key texts, on-screen
    highlights, a cover thumbnail — which is the corpus's answer about a video.
    This is the *pipeline's*: the seven `video_stages` rows with their state,
    declared model and clocks, the per-video counts, where the cues came from,
    the shot timeline, every keyframe with its OCR boxes, and the jobs that
    have touched this video. None of it has an equivalent anywhere in the MCP
    surface, which is §5.3's whole argument for the page.

    The projection drops two of those fields by not sending them: `model_key`
    is a declared model id and `error` is the pipeline's prose about the
    operator's box (§2.4). Everything else on this payload is corpus.
    """
    redact = redacted(request)
    params = request.query_params
    video_id = str(request.path_params["video_id"])
    frame_page = clamp(params.get("frames"), 1, FRAME_PAGE_MAX, FRAME_PAGE)  # type: ignore[arg-type]
    frame_offset = clamp(params.get("frame_offset"), 0, 100_000, 0)  # type: ignore[arg-type]

    data = await video_detail_reads(
        request,
        video_id,
        frame_page=frame_page,
        frame_offset=frame_offset,
        redact=redact,
    )
    if data is None:
        return _json(
            {
                "error": "E_UNKNOWN_VIDEO",
                "message": f'"{video_id}" is not in the corpus.',
                "next": "browse the videos table for what is indexed.",
            },
            status=404,
        )

    notes = [
        f"note: clamped server-side: {note}."
        for note in (
            clamp_note(params.get("frames"), frame_page, "frames"),
            clamp_note(params.get("frame_offset"), frame_offset, "frame_offset"),
        )
        if note
    ]
    counts = data.counts
    totals = data.cue_totals
    payload: dict[str, Any] = {
        "fetched_at": int(time.time()),
        "redacted": redact,
        # The `videos` row a human wants, and none of the paths: `media_path`,
        # `audio_path` and `jpeg_path` are operator detail on a page that might
        # be screenshotted (§5.1), and the stage table already says whether a
        # fetch succeeded.
        "video": video_header(data.row, data.tags),
        # `video-summary`'s own word for the state of this video, verbatim and
        # never re-derived here (§4.5) — and its refusal when it has one, which
        # for a video that never finished the pipeline is the honest answer
        # beside panels that still have something to say.
        "data_status": data.summary.get("data_status"),
        "summary_error": data.summary_error,
        "chapters": [
            {
                "start_s": float(chapter["start"]),
                "title": str(chapter["title"]),
                "link": chapter.get("link"),
            }
            for chapter in data.summary.get("chapters") or []
        ],
        # All seven, with the ones that never ran present as `absent` rather
        # than missing from the list.
        "stages": [
            {
                "stage": str(stage["stage"]),
                "state": str(stage["state"]),
                "model_key": stage["model_key"],
                "stage_version": stage["stage_version"],
                "started_at": _epoch(stage["started_at"]),
                "finished_at": _epoch(stage["finished_at"]),
                "error": stage["error"],
            }
            for stage in data.stages
        ],
        # The counts the schema does not denormalize (§4.2) — the one page in
        # this surface allowed a per-video read at all.
        "counts": {
            "cues": int(counts["cues"] or 0),
            "cues_with_words": int(counts["cues_with_words"] or 0),
            "chunks": int(counts["chunks"] or 0),
            "chapters": int(counts["chapters"] or 0),
            "keyframes": int(counts["keyframes"] or 0),
            "keyframes_kept": int(counts["keyframes_kept"] or 0),
            "ocr_frames": int(counts["ocr_frames"] or 0),
            "ocr_lines": int(counts["ocr_lines"] or 0),
            # This video's own keyframe bytes, which is corpus: the figure the
            # projection drops is the *disk*, on the overview and the ledger.
            "jpeg_bytes": int(counts["jpeg_bytes"] or 0),
        },
        # `whisperx | yt_manual | yt_auto` → how many cues came in that way.
        "cue_origins": {str(k): int(v) for k, v in data.origins.items()},
        "transcript": {
            "cues": int(counts["cues"] or 0),
            "words": int(totals["words"]),
            "chars": int(totals["chars"]),
            # Totals, and then where to read the cues themselves. This payload
            # does not serve them.
            "endpoint": f"{ROOT}/api/videos/{video_id}/cues",
            "default_limit": CUE_PAGE,
            "max_limit": CUE_PAGE_MAX,
        },
        "shots": {
            "shots": [
                {
                    "shot_id": int(shot["shot_id"]),
                    "start_s": float(shot["start_s"]),
                    "end_s": float(shot["end_s"]),
                    "frames": int(shot["frames"]),
                    "kept": int(shot["kept"]),
                    "ocr_done": int(shot["ocr_done"]),
                    "first_ord": int(shot["first_ord"]),
                    "preview": shot["preview"],
                }
                for shot in data.shots
            ],
            # Positions on the runtime, not percentages of it: the runtime is
            # `video.duration_s` and the arithmetic is the renderer's.
            "capped": data.shots_capped,
            "cap": SHOT_CAP,
        },
        "frames": {
            "frames": data.frames,
            "limit": frame_page,
            "offset": frame_offset,
            "has_more": data.frames_more,
            # The outer half of §5.3's double cap: when the page's line budget
            # is spent the per-frame counts under-report by definition, and a
            # short list that does not say so reads as the whole one.
            "ocr_line_cap": OCR_LINE_CAP,
            "ocr_lines_capped": data.ocr_lines_capped,
        },
        "job_history": {
            "jobs": [
                {
                    "job_id": str(job["job_id"]),
                    "state": str(job["state"]),
                    "kind": str(job["kind"]),
                    "created_at": _epoch(job["created_at"]),
                    "finished_at": _epoch(job["finished_at"]),
                    # The code, never the message: `jobs.error_message` is
                    # yt-dlp's prose and the jobs view has redacted it since
                    # phase 4. This list never carried it on either surface.
                    "error_code": job["error_code"],
                    "degraded_stages": list(job["degraded_stages"]),
                }
                for job in data.history
            ],
            "cap": VIDEO_HISTORY_CAP,
        },
        "notes": notes,
    }
    return _json(payload)


# -------------------------------------------------------------------- session


async def session(request: Request) -> Response:
    """`GET /dashboard/api/session` — what this deployment expects of this caller.

    **Readable signed out, by design.** A React shell that cannot ask this has
    only two ways to find out whether to render a dashboard or a sign-in link:
    guess, or probe a data endpoint and read the 401 — and the second is the
    one that puts a refusal in the console on every cold load. Nothing here is
    new disclosure: `GET /dashboard` has answered an anonymous browser with the
    auth mode and this exact sign-in hint since phase 1.

    Every field is a boolean, a mode word or a path this server serves. What is
    deliberately absent: the token, the password, whether a *specific* secret
    matched, `PUBLIC_URL`, the worker URL, the database path, the trusted CIDRs,
    the declared model ids and the drift reason.

    `signed_in` is `credential()` returning ``"session"`` — the cookie looked up
    in `login_sessions` and found unexpired. A cookie the browser still holds
    after its row has gone is exactly the case that must read `false`, because
    it is the one that would otherwise render a dashboard shell for a caller
    every subsequent request refuses.

    `has_session_cookie` is the other fact, and both are needed (Tom,
    2026-09-05). It is `SESSION_COOKIE in request.cookies`, from the same
    constant `auth/login.py` sets, and it authorizes nothing. It answers "is
    there a cookie to clear", which is why the old HTML rail's `signed_in` was
    cookie presence: a stale cookie must still get a **Sign out** button. The
    React shell renders that button when either field is true, and renders the
    dashboard on `signed_in` alone.
    """
    assembled = request.app.state.assembled
    settings = assembled.settings
    mode = str(settings.auth_mode)
    readonly = bool(assembled.public.enabled)
    write_side = write_side_enabled(mode, readonly)

    held = await credential(request)
    trusted = peer_trusted(request)
    owner = await is_owner(request)
    return _json(
        {
            "version": __version__,
            "auth_mode": mode,
            # The projection, and whether this deployment registered a write
            # side at all (§2.3, §3.2 rule 3). A client renders no control the
            # server would not accept — the same discipline the templates keep.
            "readonly": readonly,
            "write_side": write_side,
            "writes_allowed": bool(assembled.db.writes_allowed),
            # …and why not, when it is not. `Database._assert_dimensions` turns
            # `writes_allowed` off and writes the sentence in the same breath,
            # and the index form printed that sentence under its disabled
            # controls: a form refused with no reason is a form an operator
            # retypes. Policy text, `None` where writes are allowed and `None`
            # in the projection, which is the readiness block's rule for the
            # same string — the demo is told indexing is refused, never what
            # this box is serving.
            "writes_refused_reason": (
                None
                if assembled.db.writes_allowed
                else drift_reason(assembled.db, redact=readonly)
            ),
            # May this caller read the dashboard's data endpoints? The read
            # gate's own predicate: a credential, or a trusted socket peer.
            # In `AUTH=none` every request is `"open"` and this is true.
            "authenticated": held is not None or trusted,
            # Did they *prove* they are the owner? `"open"` is not a credential,
            # which is the distinction the clamp policy turns on.
            "is_owner": owner,
            "signed_in": held == "session",
            # The cookie's mere presence, which is not authorization: a stale
            # cookie reads `true` here and `false` above, and that pair is what
            # lets a shell offer sign-out to a browser the server refuses.
            "has_session_cookie": SESSION_COOKIE in request.cookies,
            "policy": (OWNER_CLAMPS if owner else PUBLIC_CLAMPS).name,
            # Where a human signs in, when this deployment has anywhere.
            "login_url": f"{ROOT}/login" if write_side else None,
            # The sentence a refused caller is given, and `None` where nobody
            # is ever refused. `sign_in_hint` is written for the 401 and names
            # a bearer unconditionally, which is right on the page — it is only
            # ever rendered on a refusal — and wrong here: in `AUTH=none` the
            # gate admits everyone, `/dashboard/login` is not registered, and
            # this field would be the one place on the deployment telling a
            # reader to send a token it does not accept.
            "sign_in_hint": None if mode == "none" else sign_in_hint(mode, login=write_side),
            # Which secret the login page will accept — the booleans that page
            # already renders to an anonymous visitor, and never their values.
            "accepts_password": bool(write_side and settings.password),
            "accepts_token": bool(
                write_side and mode == "token" and settings.static_token
            ),
        }
    )


# ------------------------------------------------------------ following (§22)


def _follow_list_row(row: Any) -> dict[str, Any]:
    """A table line: the shared row block, plus the code the column prints.

    The block is `read_models.follow_row_json` and nothing else — the base a
    write outcome and the detail read both answer with (§21, §22), so pausing a
    follow and listing it cannot describe it two ways. What the list adds is
    the last error's *code*, which is what the table's own column shows. The
    *message* is what it leaves off, and this is now the only payload on the
    surface that does: a list of sixty rows is a column to compare, and a fetch
    failure's prose is read on the follow's own page.
    """
    return {**follow_row_json(row), "last_error_code": row["last_error_code"]}


async def following(request: Request) -> Response:
    """`GET /dashboard/api/following` — every follow, and what they are costing.

    Registered **with the write routes**, not beside the other reads, and that
    is dashboard.md §18.6's rule rather than a new one: the Following pages sit
    inside the write-route list, so in `VIDTHEQUE_PUBLIC_READONLY=1` and in
    `VIDTHEQUE_AUTH=none` this endpoint is absent exactly as they are. A route
    that exists and refuses is a route somebody probes, and a JSON twin that
    answered where its page 404s would be a way back into a surface the
    deployment decided not to register. There is therefore no projection to
    apply here and no `redacted` flag to send: the only deployment that answers
    is the one whose reader is the owner.

    Four reads, whatever the row count — `read_models.following_reads`, the
    page's own — and the same server-side clamps, with `notes` carrying what a
    clamp moved because a JSON caller has no form to read the accepted value
    back out of.
    """
    data = await following_reads(request)
    params = request.query_params
    notes = [
        note
        for note in (
            clamp_note(params.get("limit"), data.limit, "limit"),
            clamp_note(params.get("offset"), data.offset, "offset"),
        )
        if note
    ]
    return _json(
        {
            "counted_at": int(time.time()),
            # Explicit, and not a parameter: `store.list_follows` has one order
            # — whatever is failing, then whatever was checked most recently —
            # because a table read at 03:00 is read to find the follow that
            # broke. Named rather than implied, like every other list here.
            "order": "failing_first",
            "totals": {key: int(value) for key, value in data.totals.items()},
            "budget": {
                # Hours of *video*, not GPU-minutes: the check knows a
                # candidate's length before it knows what indexing will cost.
                # Seconds on the wire and hours for the ceiling, because those
                # are the units each is stored and configured in; `0.0` on the
                # ceiling means the operator turned it off, which is a state
                # and not "no budget left".
                "spent_s": float(data.spent_s),
                "ceiling_h": float(data.settings["daily_hours"]),
                "window_s": BUDGET_WINDOW_S,
            },
            # Two facts about the deployment that decide what the clocks below
            # mean. `checks` is `VIDTHEQUE_FOLLOW_CHECKS`: with it off, every
            # `next_check_at` on this payload is a time nothing will happen at,
            # and a page that could not say so would be confidently wrong.
            # `vectors` is §5.5's honest refusal — `follow_channel` raises
            # `E_FEATURE_DISABLED` on the same condition `index_video` does, so
            # the surface says so above the controls rather than after a
            # submission. Neither names an environment variable.
            "checks_enabled": bool(data.settings["checks"]),
            "vectors": data.vectors,
            # The reason under that refusal, as the list's note printed it.
            # `None` where the legs are on, so the field is the sentence or
            # nothing rather than a sentence about nothing.
            "vectors_reason": data.vectors_reason,
            "follows": [_follow_list_row(row) for row in data.rows],
            # The band that is addressed to a person rather than describing the
            # instance: something matched a rule and is waiting on a human. It
            # is capped independently of `limit`, because it is not what the
            # pager pages, and it says so with `held_more` rather than a total.
            "held": [
                {
                    "title": str(row["title"] or row["url"]),
                    "url": str(row["url"]),
                    "slug": str(row["follow_slug"]),
                    "follow": str(row["follow_title"] or row["follow_slug"]),
                    "published_at": _epoch(row["published_at"]),
                    "first_seen_at": _epoch(row["first_seen_at"]),
                }
                for row in data.held
            ],
            "held_more": data.held_more,
            "held_cap": HELD_BAND_CAP,
            "pagination": {
                "limit": data.limit,
                "offset": data.offset,
                "has_more": data.has_more,
            },
            "notes": notes,
        }
    )


async def follow(request: Request) -> Response:
    """`GET /dashboard/api/following/{slug}` — the rule, the checks, the cost.

    §18.4's three bands, typed. Two things it deliberately does not send, both
    because they are sentences a page composed and §21's outcomes already ruled
    those out of this surface:

    * **the rule as English.** `follows.rules.describe` is still the only
      renderer of a policy as a sentence and the MCP tools still call it; what
      travels here is the rule as columns, out of the same
      `read_models.follow_row_json` a write outcome answers with, so a client
      composing that sentence is reading the row the check obeys.
    * **the near-miss line.** The arithmetic is the contract — how many of
      *these* rows the length rule turned away by a whisker, `null` rather than
      zero when there is nothing to report — and the words around it are the
      client's. The threshold rides along, so the count and the number in the
      sentence cannot disagree.
    """
    slug = str(request.path_params["slug"])
    data = await follow_detail_reads(request, slug)
    if data is None:
        return _refusal(
            {
                "code": "E_UNKNOWN_FOLLOW",
                "message": f'"{slug}" is not a follow on this instance.',
                "next": "the Following page lists every channel this index watches.",
            }
        )

    row = data.row
    params = request.query_params
    notes = [
        note
        for note in (
            clamp_note(params.get("limit"), data.limit, "limit"),
            clamp_note(params.get("offset"), data.offset, "offset"),
        )
        if note
    ]
    found = near_miss(data.seen, data.rules)
    return _json(
        {
            "fetched_at": int(time.time()),
            # The row with its last failure on it — code and message — which is
            # `read_models.follow_row_json_with_error`, the block every write
            # outcome on this follow answers with too. The message is the one
            # string here that is not the operator's own words:
            # `follows/check.py` records `str(exc)[:400]` when a source cannot
            # be read at all, and it is the field that would have to go first
            # if this surface ever answered a projection.
            "follow": follow_row_json_with_error(row),
            # The same deployment fact the list sends, for the same reason:
            # with `VIDTHEQUE_FOLLOW_CHECKS` off, the `next_check_at` above is
            # a time at which nothing will happen, and a page that could not
            # say so would be confidently wrong. It names no environment
            # variable, like `/api/session`'s `write_side`.
            "checks_enabled": bool(data.settings["checks"]),
            # What the follow has brought in, and every decision it has made.
            # One grouped query, already read for the page's own figure.
            "brought_in": int(data.counts.get("queued", 0)),
            "counts": {key: int(value) for key, value in data.counts.items()},
            "near_miss": None
            if found is None
            else {
                "count": found[0],
                "of": len(data.seen),
                "within_s": NEAR_MISS_S,
                "edge": found[1],
            },
            # This follow's own checks and the index jobs they enqueued. Both
            # carry the job id rather than a copy of the job: the war story is
            # already written at `/dashboard/jobs/{job_id}` and this band does
            # not fork it.
            "checks": [
                {
                    "job_id": str(check["public_id"]),
                    "state": str(check["state"]),
                    "error_code": check["error_code"],
                    "created_at": _epoch(check["created_at"]),
                    "started_at": _epoch(check["started_at"]),
                    "finished_at": _epoch(check["finished_at"]),
                }
                for check in data.checks
            ],
            "index_jobs": [
                {
                    "job_id": str(job["public_id"]),
                    "state": str(job["state"]),
                    "n_items": int(job["n_items"] or 0),
                    "n_done": int(job["n_done"] or 0),
                    "n_failed": int(job["n_failed"] or 0),
                    "created_at": _epoch(job["created_at"]),
                }
                for job in data.index_jobs
            ],
            # A check already queued or running is named, so `Check now` cannot
            # look like it did nothing.
            "in_flight": (
                None if data.in_flight is None else str(data.in_flight["public_id"])
            ),
            # The point of the page: every candidate that did *not* become a
            # video, newest decision first. `reason` travels **verbatim** — it
            # already carries the number that made the call, and re-deriving it
            # on the client is how a receipt stops being one. It is policy
            # text, which is Python's half of the split.
            "order": "newest",
            "seen": [
                {
                    "title": str(item["title"] or item["source_id"]),
                    "url": str(item["url"]),
                    "decision": str(item["decision"]),
                    "reason": item["reason"],
                    "judged_from": str(item["judged_from"]),
                    "duration_s": _seconds(item["duration_s"]),
                    "published_at": _epoch(item["published_at"]),
                    "decided_at": _epoch(item["decided_at"]),
                }
                for item in data.seen
            ],
            # The two job lists are bounded independently of `limit`, because
            # neither of them is what the pager pages — the caps ride along so
            # a client can say "ten most recent" without hard-coding ten.
            "caps": {"checks": CHECK_CAP, "index_jobs": INDEX_JOB_CAP},
            "pagination": {
                "limit": data.limit,
                "offset": data.offset,
                "has_more": data.has_more,
            },
            "notes": notes,
        }
    )


# ------------------------------------------------- §5.4 the two jobs payloads
#
# These two and the cue pager below predate the rest of this module: they were
# the Jinja pages' own poll targets and lived beside those pages in `views.py`
# until 2026-09-06, when the pages went and JSON was the only thing left in
# this route group. Nothing about them moved but the file.


def _cue_rows(
    cues: list[sqlite3.Row], chunks: list[sqlite3.Row]
) -> list[dict[str, Any]]:
    """Cues with the chunk boundaries overlaid.

    "What exactly is the embedding unit" is one of the questions this page
    exists to answer, and `chunks.first_cue_id` / `last_cue_id` is the answer:
    a cue that opens a chunk carries the chunk's label, one that closes it
    carries the rule that ends it.
    """
    opens: dict[int, sqlite3.Row] = {int(c["first_cue_id"]): c for c in chunks}
    closes = {int(c["last_cue_id"]) for c in chunks}
    rows = []
    for cue in cues:
        cue_id = int(cue["id"])
        chunk = opens.get(cue_id)
        rows.append(
            {
                "id": cue_id,
                "seq": int(cue["seq"]),
                "start_s": float(cue["start_s"]),
                "end_s": float(cue["end_s"]),
                "text": str(cue["text"]),
                "origin": str(cue["origin"]),
                "avg_logprob": cue["avg_logprob"],
                "has_words": bool(cue["has_words"]),
                "speaker": cue["speaker"],
                "chunk_opens": None
                if chunk is None
                else {
                    "seq": int(chunk["seq"]),
                    "start_s": float(chunk["start_s"]),
                    "end_s": float(chunk["end_s"]),
                    "n_chars": int(chunk["n_chars"]),
                    # Characters are what the chunker clamps on; words are what
                    # a human has an intuition for. Counted here, from the
                    # chunk's own text, and the text itself never reaches the
                    # template — `words_json` is not the only thing this page
                    # declines to dump.
                    "n_words": len(str(chunk["text"]).split()),
                },
                "chunk_closes": cue_id in closes,
            }
        )
    return rows


async def cues_json(request: Request) -> Response:
    """`GET /dashboard/api/videos/{video_id}/cues` — the next batch, for the
    transcript scrollbox.

    The transcript pane pages through the whole cue list without leaving the
    page, so the batch arrives as data: `queries.cue_page` plus `chunk_spans`
    over the same cue-id window, under the same server-side clamps
    `/dashboard/api/library/{video_id}` names in `transcript`.

    `has_more` and not a total: the reader's own "of N" comes from
    `per_video_counts`, which the detail read already made for its counts band,
    so nothing here duplicates a count query.

    **Typed fields, and the strings they replaced** (frontend-migration.md §3).
    This endpoint predates DECISIONS.md's typed-values rule and was the one
    place where the typed half was *missing* rather than merely duplicated: it
    sent `at`, `conf` and `chunk` — a `clock()`, a two-decimal log-probability
    and a composed sentence — and the numbers behind them were added beside
    them on 2026-09-05. The strings went on 2026-09-06 with the script that
    read them; `start_s`, `end_s`, `avg_logprob`, `chunk_opens` and
    `chunk_closes` are what is left, under `_cue_rows`' own names.
    """
    db = request.app.state.assembled.db
    video_id = str(request.path_params["video_id"])
    row = await db.read(lambda c: queries.lookup_video(c, video_id))
    if row is None:
        return JSONResponse(
            {
                "error": "E_UNKNOWN_VIDEO",
                "message": f'"{video_id}" is not in the corpus.',
                "next": "browse the videos table for what is indexed.",
            },
            status_code=404,
            headers=NO_STORE,
        )
    vid = int(row["id"])
    params = request.query_params
    limit = clamp(params.get("limit"), 1, CUE_PAGE_MAX, CUE_PAGE)  # type: ignore[arg-type]
    offset = clamp(params.get("offset"), 0, 500_000, 0)  # type: ignore[arg-type]

    cue_rows = await db.read(lambda c: queries.cue_page(c, vid, offset, limit))
    has_more = len(cue_rows) > limit
    cue_rows = cue_rows[:limit]
    chunks: list[sqlite3.Row] = []
    if cue_rows:
        chunks = await db.read(
            lambda c: queries.chunk_spans(
                c, vid, int(cue_rows[0]["id"]), int(cue_rows[-1]["id"])
            )
        )
    return JSONResponse(
        {
            "cues": [
                {
                    # Seconds as floats, because a cue boundary is not a whole
                    # second and `t` has always rounded it down; the
                    # log-probability as the number it is; and the chunk as the
                    # five fields a label is composed from, so a client can say
                    # "chunk 3" without parsing " · ".
                    "start_s": float(cue["start_s"]),
                    "end_s": float(cue["end_s"]),
                    "avg_logprob": None
                    if cue["avg_logprob"] is None
                    else float(cue["avg_logprob"]),
                    "chunk_opens": cue["chunk_opens"],
                    # `in_chunk` is these two facts collapsed into one bool, and
                    # a marker at the end of a chunk is not a marker at the
                    # start of one — a reader draws them differently.
                    "chunk_closes": cue["chunk_closes"],
                    "t": int(cue["start_s"]),
                    "text": cue["text"],
                    "speaker": cue["speaker"],
                    "in_chunk": bool(cue["chunk_opens"] or cue["chunk_closes"]),
                }
                for cue in _cue_rows(cue_rows, chunks)
            ],
            "offset": offset,
            "limit": limit,
            "has_more": has_more,
        },
        headers=NO_STORE,
    )


async def jobs_json(request: Request) -> Response:
    """`GET /dashboard/api/jobs` — the table, and what the 2 s tick reads.

    `read_models.jobs_reads` is the assembly, so the poll and any other reader
    of this route answer out of one pass over the queue, and the demo's
    redaction is that assembly's rather than a second rule that can drift.

    `live` is the poll's stop condition: when nothing is `queued|running` there
    is nothing to poll for, and the tab stops being a load generator against
    the process that also holds the only SQLite writer.

    **The row headline is here as of 2026-09-05, and it costs the tick
    nothing.** `contents` used to be the Jinja page's alone, a third read taken
    after the cards were built — so a React table had no title for any row,
    only a count. It rides in the grouped row-facts read now, which leaves the
    tick on the two reads §5.4 budgets it. `filters` and `notes` are the other
    half: a `state=nonsense` fell back to `all` and said nothing, which is the
    `all` invariant's exact failure case on a payload with no form to echo
    into.
    """
    data = await jobs_reads(request)
    return JSONResponse(
        {
            "now": data.now,
            "poll_ms": POLL_MS,
            "live": data.live,
            # The projection this listing ran under, and the flag the page's
            # own footnote was gated on: with it true, `error_message` is
            # `null` on every row because this deployment does not publish
            # source URLs or error text, which is a different statement from a
            # job that failed without one.
            "redacted": data.redacted,
            "jobs": data.cards,
            "pagination": {
                "limit": data.limit,
                "offset": data.offset,
                "has_more": data.has_more,
            },
            # Copied out field by field rather than forwarded: the assembly's
            # dict was a template's context and still carries its shape, so a
            # key added there must not join this contract by default (§19).
            # `error_code` is `None` rather than the form's empty string, like
            # every other absent filter here.
            "filters": {
                "state": data.filters["state"],
                "kind": data.filters["kind"],
                "error_code": data.filters["error_code"] or None,
                "degraded": data.filters["degraded"],
                "order": data.filters["order"],
            },
            "notes": data.notes,
        },
        headers=NO_STORE,
    )


async def job_json(request: Request) -> Response:
    """`GET /dashboard/api/jobs/{job_id}` — one job's war story, typed.

    Six of these fields arrived on 2026-09-05. `degraded`, `focus`, `stages`,
    `error_counts`, `counts` and `items_capped` were assembled for the Jinja
    template and dropped on the way to the payload, so a React page could
    render the item table and nothing under it — including the degraded list,
    which is the silent loss this view was built for. `job_detail_reads` has
    always made all six for every caller, so this adds no read, no bound and no
    branch.

    Field by field rather than `**detail`: `now` and `live` are this payload's
    and the rest is the assembly's dict, so a value added there must not join
    this contract by default (§19).
    """
    db = request.app.state.assembled.db
    job_id = str(request.path_params["job_id"])
    detail = await job_detail_reads(db, job_id, redacted(request))
    if detail is None:
        return JSONResponse(
            {
                "error": "E_UNKNOWN_JOB",
                # The id, in the message. Policy text is Python's, and the
                # sentence the page printed named the thing it looked for —
                # which is the same sentence `writes.cancel_job` and
                # `writes.retry_job` refuse with, and the shape the unknown
                # video and the unknown slug already had. A client that shows
                # a refusal beside a list of ids must not be the one that has
                # to say which of them it asked about.
                "message": f'"{job_id}" is not a job on this instance.',
                "next": "the jobs table lists every job this index has run.",
            },
            status_code=404,
            headers=NO_STORE,
        )
    return JSONResponse(
        {
            "now": detail["now"],
            "poll_ms": POLL_MS,
            "live": detail["live"],
            # `_job_detail`'s own rule, said out loud. Every `error_message`
            # and every stage `error` below is `null` under it, and "not
            # published on this instance" is only an honest thing for a page to
            # print when the payload says which of the two it is looking at.
            "redacted": detail["redacted"],
            "job": detail["job"],
            "items": detail["items"],
            # The item list's own bound, said out loud: 200 is what
            # `index-video` can create, and a list that stopped there without
            # saying so is a list pretending to be complete.
            "items_capped": detail["items_capped"],
            # Items by state, and typed error codes counted. The job's own
            # five counts are one summary of it; these are the tally under
            # them, which is what an unattended driver can act on.
            "counts": detail["counts"],
            "error_counts": detail["error_counts"],
            # `done` + `n_failed=0` + a failed stage underneath — the loss that
            # takes no video down and shows up as a missing search channel.
            # `error` is `None` in the projection by `_job_detail`'s own rule,
            # not a second one here.
            "degraded": detail["degraded"],
            "focus": detail["focus"],
            # The seven `video_stages` rows of the item in focus, in pipeline
            # order. No `model_key` and no stage `error`: never read, which is
            # the projection §20's stage table states as two nulls.
            "stages": detail["stages"],
            "events": detail["events"],
        },
        headers=NO_STORE,
    )
