"""`/api/*` — the JSON facade the demo page calls (demo-site.md §2).

A browser cannot do an MCP session handshake and should not have to. So this is
a **facade over the same tool implementations** — ``tools.search.run``,
``tools.library.list_videos`` — reading the ``structuredContent`` they already
build and re-shaping it for a page. Not a second query layer: every clamp, every
`note:`, every `has_more` is the one the MCP tool computed.

Two rules it exists to keep:

* **Token discipline carries over.** The facade passes its own, *tighter*
  bounds in: `limit` 1..20 against the tool's 50, `max_text_chars` 400 with no
  `0` opt-out. The full-transcript escape hatch is for an owner's agent, not
  for anonymous traffic.
* **Typed errors survive.** ``errors.HTTP_STATUS`` already maps every `E_*`
  code to a status; the facade returns that status with the same code, message
  and `next:` hint. One table, two consumers.
"""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from .. import __version__
from ..db import queries
from ..errors import HTTP_STATUS
from ..text import TRUNCATION_MARKER, clamp, clock
from ..tools import library, search
from ..tools.base import Deps
from .settings import REPO_URL, PublicSettings

# 96×54 CSS pixels at 2x DPR — now that /frames actually applies w, this is
# the honest size (was 320 when the param was decorative; ~2x page weight).
THUMB_WIDTH = 192
# A frame hit matched on its *image*, so the page renders it at 160×90 CSS
# pixels instead of 96×54; the width follows, at the same 2x.
FRAME_THUMB_WIDTH = 320
THUMB_QUALITY = 70

SEARCH_MAX_LIMIT = 20
SEARCH_DEFAULT_LIMIT = 10
SEARCH_TEXT_CHARS = 400
VIDEOS_MAX_LIMIT = 50
VIDEOS_DEFAULT_LIMIT = 24

CONTENT_TYPES = ("all", "transcript", "ocr", "frame")


# --------------------------------------------------------------------- shared


def thumb_url(deps: Deps, frame_id: str | None, width: int = THUMB_WIDTH) -> str | None:
    """A `/frames/…` URL for a keyframe, signed only when signing means anything.

    In ``AUTH=none`` — the intended public deployment — ``frame_signer`` is
    ``None`` and the route is open, so an unsigned URL is the honest one:
    signing a link to a file the server hands to anyone who asks buys nothing.
    What guards the keyframe directory in public mode is the rate limiter
    (demo-site.md §5). With ``token``/``oauth`` the signer exists and this uses
    the same ``FrameUrlSigner.url()`` ``get-frames`` does — one signing scheme,
    two callers.
    """
    if not frame_id:
        return None
    base = deps.settings.public_url.rstrip("/")
    signer = deps.frame_signer
    if signer is None:
        return f"{base}/frames/{frame_id}.jpg?w={width}&q={THUMB_QUALITY}"
    url, _expires = signer.url(base, frame_id, width, THUMB_QUALITY)
    return url


# The tool's truncation marker ends in advice only an MCP client can take
# ("pass max_text_chars=0"), and the facade deliberately has no such opt-out
# (§2). Built from the template rather than retyped, so a change in `text.py`
# cannot leave a stale pattern behind that silently matches nothing.
_TRUNCATED = re.compile(re.escape(TRUNCATION_MARKER).replace(re.escape("{n}"), r"(\d+)"))


def demo_text(text: str | None) -> str | None:
    """The tool's snippet, with the marker rewritten for a reader with no API."""
    if not text:
        return text
    return _TRUNCATED.sub(lambda m: f"…[{m.group(1)} chars cut]…", text)


def _error_response(structured: dict[str, Any] | None, fallback: str) -> JSONResponse:
    payload = structured or {}
    code = str(payload.get("code") or "E_INTERNAL")
    return JSONResponse(
        {
            "error": code,
            "message": payload.get("message") or fallback,
            "next": payload.get("next"),
        },
        status_code=HTTP_STATUS.get(code, 500),
    )


def _int_param(request: Request, name: str, low: int, high: int, default: int) -> int:
    return clamp(request.query_params.get(name), low, high, default)  # type: ignore[arg-type]


def _decorate_hit(deps: Deps, hit: dict[str, Any]) -> dict[str, Any]:
    """The only two fields the facade adds, both from data already returned."""
    row = dict(hit)
    row["timestamp"] = clock(hit.get("start"))
    width = FRAME_THUMB_WIDTH if hit.get("source") == "frame" else THUMB_WIDTH
    row["thumb"] = thumb_url(deps, hit.get("frame_id"), width)
    row["text"] = demo_text(hit.get("text"))
    return row


# ------------------------------------------------------------------ endpoints


async def search_endpoint(request: Request) -> JSONResponse:
    deps: Deps = request.app.state.assembled.deps
    params = request.query_params
    content_type = params.get("content_type") or "all"
    if content_type not in CONTENT_TYPES:
        return JSONResponse(
            {
                "error": "E_BAD_PARAM",
                "message": f"content_type must be one of {', '.join(CONTENT_TYPES)}.",
                "next": "omit it for all three channels.",
            },
            status_code=400,
        )

    limit = _int_param(request, "limit", 1, SEARCH_MAX_LIMIT, SEARCH_DEFAULT_LIMIT)
    offset = _int_param(request, "offset", 0, 1_000, 0)
    result = await search.run(
        deps,
        q=params.get("q"),
        content_type=content_type,
        limit=limit,
        offset=offset,
        channel=params.get("channel"),
        video_id=params.get("video_id"),
        max_text_chars=SEARCH_TEXT_CHARS,
    )
    if result.is_error:
        return _error_response(result.structured_content, "search failed")

    payload = result.structured_content or {}
    return JSONResponse(
        {
            "query": params.get("q") or "",
            "content_type": content_type,
            "results": [_decorate_hit(deps, hit) for hit in payload.get("results", [])],
            "pagination": payload.get("pagination", {}),
            "notes": payload.get("notes", []),
            # Only the tool's empty path sets this, and it is the difference
            # between "nothing matched" and "nothing is indexed" — which a
            # `?q=` link to a fresh instance would otherwise report as a bad
            # query. Absent on a page of hits, where it would say nothing.
            "data_status": payload.get("data_status"),
        }
    )


async def videos_endpoint(request: Request) -> JSONResponse:
    deps: Deps = request.app.state.assembled.deps
    params = request.query_params
    limit = _int_param(request, "limit", 1, VIDEOS_MAX_LIMIT, VIDEOS_DEFAULT_LIMIT)
    offset = _int_param(request, "offset", 0, 1_000, 0)
    q = params.get("q")
    result = await library.list_videos(
        deps,
        q=q,
        channel=params.get("channel"),
        limit=limit,
        offset=offset,
        # `relevance` needs something to be relevant to; the tool raises
        # E_ORDER_SCOPE otherwise, so the facade picks the order that fits.
        order="relevance" if q else "recency",
        # `format`/`fields` are deliberately not passed: they shape the *text*
        # block, and this reads `structured_content`, which carries every field
        # whatever they say. Passing them read as though they mattered here.
    )
    if result.is_error:
        return _error_response(result.structured_content, "listing failed")

    payload = result.structured_content or {}
    videos = [dict(v) for v in payload.get("videos", [])]
    covers = await deps.db.read(lambda c: _cover_frames(c, [v["video_id"] for v in videos]))
    for video in videos:
        video["thumb"] = thumb_url(deps, covers.get(video["video_id"]))
    return JSONResponse({"videos": videos, "pagination": payload.get("pagination", {})})


def _cover_frames(conn: sqlite3.Connection, public_ids: list[str]) -> dict[str, str]:
    """The lowest non-duplicate keyframe ordinal per video — a cover image.

    One grouped query for the whole page rather than a per-video probe: the
    listing is already capped at 50 rows, and this is the only extra read the
    facade does beyond what the tool ran.
    """
    if not public_ids:
        return {}
    rows = conn.execute(
        """
        SELECT v.public_id AS public_id, MIN(k.ord) AS ord
        FROM videos v JOIN keyframes k ON k.video_id = v.id AND k.dup_of IS NULL
        WHERE v.public_id IN (SELECT value FROM json_each(?))
        GROUP BY v.public_id
        """,
        (json.dumps(public_ids),),
    ).fetchall()
    return {str(r["public_id"]): f"{r['public_id']}-{int(r['ord']):05d}" for r in rows}


async def meta_endpoint(request: Request) -> JSONResponse:
    assembled = request.app.state.assembled
    deps: Deps = assembled.deps
    public: PublicSettings = request.app.state.public_settings
    rollup = await deps.db.read(queries.corpus_rollup)
    return JSONResponse(
        {
            "name": "vidtheque",
            "version": __version__,
            # The same string config.resource_url builds, so the page's copy
            # button and the OAuth `resource` can never disagree.
            "mcp_url": deps.settings.resource_url,
            "auth": deps.settings.auth_mode,
            "ask_enabled": public.ask_enabled,
            "ask_model": public.openrouter_model if public.ask_enabled else None,
            "videos": int(rollup["videos_ready"] or 0),
            "limits": {
                "search_per_min": public.search_per_min,
                "ask_per_min": public.ask_per_min,
                "ask_per_day": public.ask_per_day,
            },
            "repo": REPO_URL,
        }
    )


def api_routes() -> list[Route]:
    from .ask import ask_endpoint

    return [
        Route("/api/search", search_endpoint, methods=["GET"]),
        Route("/api/videos", videos_endpoint, methods=["GET"]),
        Route("/api/meta", meta_endpoint, methods=["GET"]),
        Route("/api/ask", ask_endpoint, methods=["POST"]),
    ]
