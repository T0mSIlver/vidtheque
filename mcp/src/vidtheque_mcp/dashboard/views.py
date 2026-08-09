"""The three read-only pages — dashboard.md §5.1, §5.2, §5.3.

Every one of these calls the **same service layer the MCP tools call**
(`tools/library.*`), plus the raw `db/queries.py` reads the tool surface was
designed not to expose. It never speaks MCP, and it never imports anything from
`worker/`. That is CLAUDE.md's boundary and dashboard.md §2.1's whole argument
for a route group rather than a second service.

Bounds are the byte analogue of the token discipline the tools keep (§6):
every list here is double-capped, every pager probes one row past its limit
rather than counting, the per-video `COUNT(*)`s run on this page and nowhere
else, and the frame widths come from a fixed set of three so the derived cache
holds three variants per keyframe instead of one per browser window.
"""

from __future__ import annotations

import os
import sqlite3
from typing import Any

from starlette.requests import Request
from starlette.responses import HTMLResponse, Response

from .. import __version__
from ..db import queries
from ..errors import HTTP_STATUS
from ..public.api import OWNER_CLAMPS, _cover_frames, thumb_url
from ..text import clamp
from ..tools import library
from ..tools.base import Deps
from .render import build_environment
from .settings import ROOT

# The fixed width set (§6.4). Three variants per frame in the `derived/` cache,
# not one per browser window — and never inline base64, which is the byte
# analogue of the token blowup that invariant exists to prevent.
STRIP_WIDTH = 192
DETAIL_WIDTH = 512
LIGHTBOX_WIDTH = 1280
FRAME_QUALITY = 70

# Page sizes. Owner clamps, still server-side: `?frames=100000` is clamped.
FRAME_PAGE = 24
FRAME_PAGE_MAX = 96
CUE_PAGE = 50
CUE_PAGE_MAX = 200
SHOT_CAP = 2_000
CHANNEL_CAP = 12
TAG_CAP = 24
RECENT_CAP = 8
OCR_LINE_CAP = 600

_ENV = build_environment()


def _render(name: str, context: dict[str, Any], status: int = 200) -> HTMLResponse:
    return HTMLResponse(
        _ENV.get_template(name).render(**context),
        status_code=status,
        # A management page describes state that changes under the reader.
        # Nothing here is cacheable and a shared cache must never hold it.
        headers={"Cache-Control": "no-store"},
    )


def _chrome(request: Request, page: str) -> dict[str, Any]:
    """What every page needs: where it is, and what the deployment is."""
    assembled = request.app.state.assembled
    return {
        "root": ROOT,
        "page": page,
        "version": __version__,
        "auth_mode": assembled.settings.auth_mode,
        "readonly": assembled.public.enabled,
        "writes_allowed": assembled.db.writes_allowed,
    }


def sign_in_page(request: Request, mode: str) -> Response:
    """The 401 an unauthenticated browser gets — a page, not a JSON blob."""
    from .access import sign_in_hint

    return _render(
        "error.html",
        {
            **_chrome(request, "corpus"),
            "title": "Sign in",
            "error": {
                "code": "E_AUTH_REQUIRED",
                "message": "The dashboard needs the owner's token or session.",
                "next": sign_in_hint(mode),
            },
        },
        status=401,
    )


def _tool_error(result: Any) -> dict[str, Any] | None:
    if not result.is_error:
        return None
    payload = dict(result.structured_content or {})
    payload.setdefault("code", "E_INTERNAL")
    payload.setdefault("message", "the query layer refused this request.")
    payload.setdefault("next", None)
    return payload


# ---------------------------------------------------------------- §5.1 corpus


async def overview(request: Request) -> Response:
    assembled = request.app.state.assembled
    deps: Deps = assembled.deps
    db = assembled.db

    summary = await library.corpus_summary(
        deps,
        max_channels=CHANNEL_CAP,
        max_tags=TAG_CAP,
        include_recent=False,
        include_guidance=False,
    )
    error = _tool_error(summary)
    if error is not None:  # pragma: no cover - corpus_summary has no error path
        return _render(
            "error.html",
            {**_chrome(request, "corpus"), "error": error, "title": "Corpus"},
            status=HTTP_STATUS.get(error["code"], 500),
        )
    payload = summary.structured_content or {}

    # `corpus_summary` builds its rollup for the lines it prints; this reads it
    # again for the three fields the payload does not carry (OCR lines, the
    # published span, the last-indexed clock). One flat statement, twice, is
    # the price of not re-deriving `data_status` here — §4.5 is not negotiable.
    rollup = await db.read(queries.corpus_rollup)
    pool = await db.read(lambda c: queries.resolve_videos(c, queries.CorpusFilter()))
    recent = await db.read(lambda c: queries.recent_indexed(c, pool, RECENT_CAP))
    keyframe_bytes = await db.read(queries.keyframe_bytes_total)

    covers = await db.read(
        lambda c: _cover_frames(c, [str(r["public_id"]) for r in recent])
    )
    recent_rows = [
        {
            "video_id": str(row["public_id"]),
            "title": str(row["title"]),
            "channel": row["channel_name"] or "",
            "duration_s": row["duration_s"],
            "indexed_at": row["indexed_at"],
            "thumb": thumb_url(deps, covers.get(str(row["public_id"])), STRIP_WIDTH),
        }
        for row in recent
    ]

    return _render(
        "overview.html",
        {
            **_chrome(request, "corpus"),
            "title": "Corpus",
            "corpus": payload,
            "rollup": rollup,
            "recent": recent_rows,
            "channels": payload.get("channels", []),
            "tags": payload.get("tags", {}),
            "gaps": payload.get("gaps", {}),
            "config": _declared_models(assembled.db.config),
            "vectors": assembled.db.vectors,
            "storage": {
                "keyframes": keyframe_bytes,
                # os.stat, not a directory walk: the file knows its own size and
                # the keyframe bytes are a column (§5.1).
                "database": _file_size(assembled.settings.db_path),
            },
        },
    )


def _file_size(path: Any) -> int:
    try:
        return int(os.stat(path).st_size)
    except OSError:  # pragma: no cover - the db exists by the time a page loads
        return 0


# The rows of `config` a human wants next to the live vector state. Deliberately
# a fixed list rather than "everything in `config`": that table also carries
# dimensions and storage formats, which are the schema's business.
_MODEL_KEYS = (
    ("stt.model", "transcription"),
    ("text_embed.model", "transcript embeddings"),
    ("frame_embed.model", "frame embeddings"),
    ("ocr.model", "on-screen text"),
)


def _declared_models(config: dict[str, str]) -> list[dict[str, str]]:
    """What the corpus says it was built with (§4.1 caveat 2).

    `config` is written by migrations and read once at boot. It is the
    *declared* model, never the worker's reported one — the live answer is the
    vector state beside it, which is what `note_worker_drift` disables on a
    mismatch. Showing the pair is the point.
    """
    rows = []
    for key, label in _MODEL_KEYS:
        value = config.get(key)
        if not value:
            continue
        dim = config.get(key.replace(".model", ".dim"))
        rows.append({"label": label, "key": key, "value": value, "dim": dim or ""})
    return rows


# ---------------------------------------------------------------- §5.2 videos

_ORDERS = ("recency", "title", "duration", "indexed_at", "relevance")
_HAS = ("any", "transcript", "ocr", "frames", "all")


async def videos(request: Request) -> Response:
    assembled = request.app.state.assembled
    deps: Deps = assembled.deps
    params = request.query_params

    q = (params.get("q") or "").strip() or None
    channel = (params.get("channel") or "").strip() or None
    tags = (params.get("tags") or "").strip() or None
    has = params.get("has") if params.get("has") in _HAS else "any"
    # `all` is this page's default and it means all: a management table that
    # cannot see the failed and the half-indexed is the one view nobody needs.
    index_state = params.get("index_state") or "all"
    if index_state not in (*queries.INDEX_STATES, "all"):
        index_state = "all"
    order = params.get("order") if params.get("order") in _ORDERS else None
    if order is None:
        order = "relevance" if q else "recency"
    limit = clamp(params.get("limit"), 1, OWNER_CLAMPS.videos_max_limit,  # type: ignore[arg-type]
                  OWNER_CLAMPS.videos_default_limit)
    offset = clamp(params.get("offset"), 0, OWNER_CLAMPS.offset_max, 0)  # type: ignore[arg-type]

    result = await library.list_videos(
        deps,
        q=q,
        channel=channel,
        tags=tags,
        has=has,
        index_state=index_state,
        order=order,
        limit=limit,
        offset=offset,
        fields="video_id,title,channel,published,duration,coverage,tags,indexed_at,index_state",
        max_text_chars=200,
    )
    filters = {
        "q": q or "",
        "channel": channel or "",
        "tags": tags or "",
        "has": has,
        "index_state": index_state,
        "order": order,
        "limit": limit,
    }
    error = _tool_error(result)
    if error is not None:
        return _render(
            "videos.html",
            {
                **_chrome(request, "videos"),
                "title": "Videos",
                "error": error,
                "rows": [],
                "pagination": {"limit": limit, "offset": offset, "has_more": False},
                "filters": filters,
                "orders": _ORDERS,
                "has_values": _HAS,
                "index_states": queries.INDEX_STATES,
            },
            status=HTTP_STATUS.get(error["code"], 500),
        )

    payload = result.structured_content or {}
    rows = [dict(v) for v in payload.get("videos", [])]
    covers = await assembled.db.read(
        lambda c: _cover_frames(c, [r["video_id"] for r in rows])
    )
    for row in rows:
        row["thumb"] = thumb_url(deps, covers.get(row["video_id"]), STRIP_WIDTH)
        row["coverage_pills"] = _coverage_pills(str(row.get("coverage") or "---"))
        row["tag_list"] = [t for t in str(row.get("tags") or "").split(",") if t]

    return _render(
        "videos.html",
        {
            **_chrome(request, "videos"),
            "title": "Videos",
            "error": None,
            "rows": rows,
            "pagination": payload.get("pagination", {}),
            "filters": filters,
            "orders": _ORDERS,
            "has_values": _HAS,
            "index_states": queries.INDEX_STATES,
        },
    )


_COVERAGE_LABELS = (
    ("t", "transcript"),
    ("o", "on-screen text"),
    ("f", "frame embeddings"),
)


def _coverage_pills(coverage: str) -> list[dict[str, Any]]:
    """The tool's own `t/o/f/-` string, rendered as three labelled pills.

    §4.2: this is what the videos table shows instead of per-row counts, and it
    costs nothing extra — `_LIST_SQL` already computes the three booleans.
    """
    return [
        {"letter": letter, "label": label, "present": letter in coverage}
        for letter, label in _COVERAGE_LABELS
    ]


# ---------------------------------------------------------------- §5.3 detail


async def video_detail(request: Request) -> Response:
    assembled = request.app.state.assembled
    deps: Deps = assembled.deps
    db = assembled.db
    video_id = request.path_params["video_id"]
    params = request.query_params

    row = await db.read(lambda c: queries.lookup_video(c, video_id))
    if row is None:
        return _render(
            "error.html",
            {
                **_chrome(request, "videos"),
                "title": "Unknown video",
                "error": {
                    "code": "E_UNKNOWN_VIDEO",
                    "message": f'"{video_id}" is not in the corpus.',
                    "next": "browse the videos table for what is indexed.",
                },
            },
            status=404,
        )
    vid = int(row["id"])

    frame_page = clamp(params.get("frames"), 1, FRAME_PAGE_MAX, FRAME_PAGE)  # type: ignore[arg-type]
    frame_offset = clamp(params.get("frame_offset"), 0, 100_000, 0)  # type: ignore[arg-type]
    cue_page_size = clamp(params.get("cues"), 1, CUE_PAGE_MAX, CUE_PAGE)  # type: ignore[arg-type]
    cue_offset = clamp(params.get("cue_offset"), 0, 500_000, 0)  # type: ignore[arg-type]

    # `video-summary` refuses a video that never finished the pipeline — which
    # is exactly the video this page exists for. So the refusal is *rendered*,
    # verbatim, next to the panels that still have something to say (the stage
    # table always does), instead of becoming this page's own error.
    summary = await library.video_summary(
        deps,
        video_id=video_id,
        include_key_texts=False,
        include_ocr_highlights=False,
        include_speakers=False,
        include_guidance=False,
        max_chapters=50,
    )
    summary_payload = summary.structured_content or {}
    summary_error = _tool_error(summary)

    stages = await db.read(lambda c: queries.video_stages(c, vid))
    counts = await db.read(lambda c: queries.per_video_counts(c, vid))
    origins = await db.read(lambda c: queries.cue_origins(c, vid))
    shots = await db.read(lambda c: queries.shot_timeline(c, vid, SHOT_CAP))
    frame_rows = await db.read(
        lambda c: queries.keyframe_page(c, vid, frame_offset, frame_page)
    )
    cue_rows = await db.read(lambda c: queries.cue_page(c, vid, cue_offset, cue_page_size))
    tag_map = await db.read(lambda c: queries.video_tags(c, [vid]))

    frames_more = len(frame_rows) > frame_page
    frame_rows = frame_rows[:frame_page]
    cues_more = len(cue_rows) > cue_page_size
    cue_rows = cue_rows[:cue_page_size]

    ocr_lines = await db.read(
        lambda c: queries.ocr_for_frames(
            c, [int(f["id"]) for f in frame_rows], OCR_LINE_CAP
        )
    )
    chunks: list[sqlite3.Row] = []
    if cue_rows:
        chunks = await db.read(
            lambda c: queries.chunk_spans(
                c, vid, int(cue_rows[0]["id"]), int(cue_rows[-1]["id"])
            )
        )

    duration = float(row["duration_s"] or 0.0)
    return _render(
        "video.html",
        {
            **_chrome(request, "videos"),
            "title": str(row["title"]),
            "video": _video_header(row, tag_map.get(vid, [])),
            "duration_s": duration,
            "data_status": summary_payload.get("data_status"),
            "summary_error": summary_error,
            "chapters": summary_payload.get("chapters", []),
            "stages": _stage_rows(stages),
            "counts": counts,
            "origins": origins,
            "shots": _shot_bars(shots, duration, frame_page),
            "shots_capped": len(shots) >= SHOT_CAP,
            "frames": _frame_cards(deps, video_id, frame_rows, ocr_lines),
            "frame_page": frame_page,
            "frame_offset": frame_offset,
            "frames_more": frames_more,
            "cues": _cue_rows(cue_rows, chunks),
            "cue_page": cue_page_size,
            "cue_offset": cue_offset,
            "cues_more": cues_more,
        },
    )


def _video_header(row: sqlite3.Row, tags: list[str]) -> dict[str, Any]:
    """The `videos` row a human wants — and none of the paths.

    `media_path`, `audio_path` and `jpeg_path` are operator detail that must not
    leak into a page that might be screenshotted (§5.1). Presence, not location:
    the stage table already says whether a fetch succeeded.
    """
    return {
        "video_id": str(row["public_id"]),
        "title": str(row["title"]),
        "channel": row["channel_name"] or "",
        "published_at": row["published_at"],
        "duration_s": row["duration_s"],
        "language": row["language"] or "",
        "index_state": str(row["index_state"]),
        "indexed_at": row["indexed_at"],
        "added_at": row["added_at"],
        "url": str(row["url"]),
        "description": (row["description"] or "")[:400],
        "tags": tags,
    }


def _stage_rows(stages: list[sqlite3.Row]) -> list[dict[str, Any]]:
    """All seven stages, with the ones that never ran said out loud.

    `job-status` collapses these into five *wire* stages for a model's benefit
    (`jobs/store.WIRE_STAGES`). A human wants the seven, and wants the absent
    ones present as `absent` rather than silently missing from the list.
    """
    by_stage = {str(s["stage"]): s for s in stages}
    rows = []
    for stage in queries.STAGE_ORDER:
        row = by_stage.get(stage)
        if row is None:
            rows.append({"stage": stage, "state": "absent", "model_key": None,
                         "started_at": None, "finished_at": None, "error": None,
                         "stage_version": None})
            continue
        rows.append(
            {
                "stage": stage,
                "state": str(row["state"]),
                "model_key": row["model_key"],
                "stage_version": row["stage_version"],
                "started_at": row["started_at"],
                "finished_at": row["finished_at"],
                "error": row["error"],
            }
        )
    return rows


def _shot_bars(
    shots: list[sqlite3.Row], duration: float, frame_page: int
) -> list[dict[str, Any]]:
    """Shots as percentages of the runtime, each pointing at its first frame.

    The link is a real `<a href>` carrying the `frame_offset` that page holds
    the shot's first keyframe — `ord` is dense per video, so the offset is
    arithmetic rather than another query. Clicking a shot works with JavaScript
    off, which is the difference between a timeline and a decoration.
    """
    span = duration if duration > 0 else max(
        (float(s["end_s"] or 0.0) for s in shots), default=1.0
    )
    span = span or 1.0
    bars = []
    for shot in shots:
        start = max(0.0, float(shot["start_s"] or 0.0))
        end = max(start, float(shot["end_s"] or start))
        first_ord = int(shot["first_ord"])
        bars.append(
            {
                "shot_id": int(shot["shot_id"]),
                "start_s": start,
                "end_s": end,
                "left": round(100.0 * min(start, span) / span, 4),
                "width": round(100.0 * max(end - start, 0.0) / span, 4) or 0.05,
                "frames": int(shot["frames"]),
                "kept": int(shot["kept"]),
                "ocr_done": int(shot["ocr_done"]),
                "first_ord": first_ord,
                "frame_offset": (first_ord // frame_page) * frame_page,
            }
        )
    return bars


def _frame_cards(
    deps: Deps,
    video_id: str,
    rows: list[sqlite3.Row],
    ocr_lines: dict[int, list[sqlite3.Row]],
) -> list[dict[str, Any]]:
    cards = []
    for row in rows:
        ordinal = int(row["ord"])
        frame_id = f"{video_id}-{ordinal:05d}"
        lines = ocr_lines.get(int(row["id"]), [])
        cards.append(
            {
                "frame_id": frame_id,
                "ord": ordinal,
                "t_s": float(row["t_s"]),
                "shot_id": int(row["shot_id"]),
                "sharpness": float(row["sharpness"]),
                "width": int(row["width"]),
                "height": int(row["height"]),
                "jpeg_bytes": int(row["jpeg_bytes"]),
                "ocr_state": str(row["ocr_state"]),
                "dup_of_ord": None if row["dup_of"] is None else int(row["dup_of_ord"]),
                "thumb": thumb_url(deps, frame_id, STRIP_WIDTH),
                "detail": thumb_url(deps, frame_id, DETAIL_WIDTH),
                "large": thumb_url(deps, frame_id, LIGHTBOX_WIDTH),
                "lines": [
                    {
                        "line_no": int(line["line_no"]),
                        "text": str(line["text"]),
                        "conf": line["conf"],
                        "box": (
                            float(line["x0"]),
                            float(line["y0"]),
                            float(line["x1"]),
                            float(line["y1"]),
                        ),
                    }
                    for line in lines
                ],
            }
        )
    return cards


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
                },
                "chunk_closes": cue_id in closes,
            }
        )
    return rows
