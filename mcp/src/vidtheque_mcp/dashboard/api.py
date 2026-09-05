"""`/dashboard/api/{overview,ledger,library,session}` — the JSON the React
dashboard reads (`docs/design/frontend-migration.md`).

Additive reads, and they add no query and no policy: `overview`, `ledger`,
`library` and `library/{video_id}` are `read_models`' assemblers — the same
reads the Jinja pages make, in the same order, under the same projection and
the same server-side clamps — shaped into typed JSON, and `session` is what a
browser needs before it can decide whether to render a dashboard or a sign-in
link.

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
import time
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from .. import __version__
from ..auth.credential import credential, is_owner
from ..auth.login import SESSION_COOKIE
from ..errors import HTTP_STATUS
from ..public.api import OWNER_CLAMPS, PUBLIC_CLAMPS
from ..text import clamp
from .access import peer_trusted, sign_in_hint, write_side_enabled
from .read_models import (
    CUE_PAGE,
    CUE_PAGE_MAX,
    FAILED_WINDOW_S,
    FRAME_PAGE,
    FRAME_PAGE_MAX,
    OCR_LINE_CAP,
    SHOT_CAP,
    VIDEO_HISTORY_CAP,
    LedgerReads,
    OverviewReads,
    VideosReads,
    clamp_note,
    coverage_flags,
    declared_models,
    ledger_reads,
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


def _refusal(error: dict[str, Any]) -> JSONResponse:
    """A tool's typed refusal, as the envelope `/api/*` already answers with."""
    code = str(error.get("code") or "E_INTERNAL")
    return _json(
        {
            "error": code,
            "message": error.get("message") or "the query layer refused this request.",
            "next": error.get("next"),
        },
        status=HTTP_STATUS.get(code, 500),
    )


def _epoch(value: Any) -> int | None:
    """A stored unix stamp as an int, or `None` when the corpus has none."""
    return None if value is None else int(value)


def _seconds(value: Any) -> float | None:
    return None if value is None else float(value)


def _readiness(readiness: dict[str, Any]) -> dict[str, Any]:
    """The pipeline observation, field by field rather than passed through.

    Two reasons it is copied out instead of forwarded. The clock: the page's
    `checked_at` is an ISO-8601 string because a `<time datetime=...>` attribute
    wants one, and this surface sends `read_models`' epoch seconds off the same
    reading, because React formats dates. And the shape: a field added to the
    dict for the templates would otherwise join this contract the day it is
    written, which is how an operator-only value reaches a payload nobody
    re-reviewed. `worker` stays `None` whole in the projection - the probe was
    never made.
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
        "checked_at": readiness["checked_at_s"],
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
        "corpus": {
            "videos": int(corpus.get("videos") or 0),
            "queryable_videos": int(corpus.get("queryable_videos") or 0),
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
        return _refusal(data.error)
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
        "filters": {
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
        },
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
        # The one read this surface does not take. `cue_page=None` is not a
        # filter on the answer, it is the absence of a query.
        cue_page=None,
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
    2026-09-05). It is `SESSION_COOKIE in request.cookies` — the same lookup
    `views._chrome` makes, from the same constant, so the two cannot drift —
    and it authorizes nothing. It answers "is there a cookie to clear", which
    is why the HTML rail's own `signed_in` has always been cookie presence: a
    stale cookie must still get a **Sign out** button. The React shell renders
    that button when either field is true, and renders the dashboard on
    `signed_in` alone.
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
