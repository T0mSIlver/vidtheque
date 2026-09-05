"""The read-only pages — dashboard.md §5.1–§5.4 and amendments §14–§15.

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

import asyncio
import re
import sqlite3
import time
from typing import Any
from urllib.parse import parse_qs, quote, urlencode, urlsplit

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response

from .. import __version__
from ..db import queries
from ..errors import HTTP_STATUS, ToolError
from ..jobs import store as jobs_store
# The *pages* keep the owner page size for every reader, and that is a
# decision, not the leftover of phase 5's credential-keyed clamp (dashboard.md
# §2.4). What phase 5 fixed is the full-transcript hatch, which is a `/api/*`
# parameter and reaches no page: no template renders untruncated transcript
# text, and no page takes `max_text_chars`. What is left between the two
# policies here is rows-per-page and how far an offset may walk, on a listing
# the demo publishes in full anyway — so keying it off the credential would
# paginate the browsable corpus at 24 rows to protect nothing.
from ..public.api import CONTENT_TYPES, OWNER_CLAMPS, _cover_frames, search_payload
from ..follows import rules as follow_rules
from ..follows import store as follows_store
from ..text import clamp, clock, duration_clock, iso_day, iso_minute, iso_z, split_csv
from ..timeparse import parse_corpus_time
from ..tools import library
from ..tools.base import Deps
# The shared half of the overview and the ledger — the reads, the width set,
# the projection predicate and the worker probe — moved to `read_models.py` on
# 2026-09-05 so `api.py` answers out of the same code rather than a second copy
# of the same SQL. Imported back under the names this module has always used:
# the pages call exactly what they called before.
from .read_models import DETAIL_WIDTH, FAILED_WINDOW_S, LIGHTBOX_WIDTH, STRIP_WIDTH
from .read_models import declared_models as _declared_models
from .read_models import ledger_reads, overview_reads
from .read_models import pipeline_readiness as _pipeline_readiness
from .read_models import redacted as _redacted
from .read_models import thumb as _thumb
from .read_models import tool_error as _tool_error
# The videos table's and the detail page's half, moved out the same way on
# 2026-09-05 (§20): the query assembly, the row facts, the stage table, the
# shot facts and the frame cards. Same reads, same order, same bounds — the
# page renders what the assembler read, and so does `/dashboard/api/videos`.
from .read_models import HAS_VALUES, VIDEO_ORDERS, video_detail_reads, videos_reads
from .read_models import video_header as _video_header

# Re-exported deliberately (the redundant alias is the marker): the bound is
# `read_models`' now, and a test reads it off this module.
from .read_models import WORKER_STATUS_TIMEOUT_S as WORKER_STATUS_TIMEOUT_S
from .read_models import (
    CUE_PAGE,
    CUE_PAGE_MAX,
    FRAME_PAGE,
    FRAME_PAGE_MAX,
    OCR_LINE_CAP,
    SHOT_CAP,
    VIDEO_HISTORY_CAP,
)
from .render import build_environment, elapsed, span
from .settings import ROOT

# `OCR_PREVIEW_LINES` is gone (Tom, 2026-08-10). It bounded a per-frame digest
# whose expander split the list in two, which in turn capped the box↔line
# linkage at the eight lines a stylesheet could enumerate. The panel is a
# scrollbox of every line now and the linkage is by index, so there is no
# preview length left to pick. `OCR_LINE_CAP` — the *page's* budget, the outer
# half of §5.3's double cap — is untouched and still printed when it binds.
# The same bound for the job event log, and the same reason to have one: an
# overnight batch writes sixty events and the panel printed all of them.
EVENT_PREVIEW = 8

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
    from ..auth.login import SESSION_COOKIE
    from .access import write_side_enabled

    assembled = request.app.state.assembled
    mode = assembled.settings.auth_mode
    readonly = assembled.public.enabled
    return {
        "root": ROOT,
        "page": page,
        # `rail_query` is gone with the rail's search box (Tom, 2026-08-13):
        # search is a page, and a page's own form reads its own query string.
        "version": __version__,
        "auth_mode": mode,
        "readonly": readonly,
        "writes_allowed": assembled.db.writes_allowed,
        # Whether this deployment registered a write side at all (§2.3, §3.2
        # rule 3). Every affordance that would POST is behind this, so the demo
        # projection and an `AUTH=none` instance render pages with no controls
        # rather than pages with controls that 404 — the same discipline
        # `hidden_tools` applies to the tool list.
        "write_side": write_side_enabled(mode, readonly),
        # Is there a cookie to sign out of? Deliberately the cookie's presence
        # rather than "is this request authenticated": a bearer holder has
        # nothing to sign out of, and a stale cookie is exactly the thing the
        # button should still be there to clear.
        "signed_in": SESSION_COOKIE in request.cookies,
    }


def sign_in_page(request: Request, mode: str, *, login: bool = True) -> Response:
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
                "next": sign_in_hint(mode, login=login),
            },
            # The refusal's own way back, when there is one: the page that can
            # actually resolve it.
            "back": {"href": f"{ROOT}/login", "label": "Sign in"} if login else None,
        },
        status=401,
    )


# ---------------------------------------------------------------- §5.1 corpus


async def overview(request: Request) -> Response:
    redact = _redacted(request)
    # Network and database work overlap. A down worker therefore costs at most
    # the remainder of this one-second budget, not one second after the corpus
    # page has already finished assembling itself.
    readiness_task = asyncio.create_task(_pipeline_readiness(request, redact=redact))
    try:
        return await _overview_page(request, readiness_task, redact=redact)
    finally:
        # Any unwind between here and the task's own await — a query budget
        # exceeded, a locked writer — must not orphan a task holding an open
        # HTTP request. Cancelling a completed task is a no-op.
        if not readiness_task.done():
            readiness_task.cancel()


async def _overview_page(
    request: Request, readiness_task: asyncio.Task[dict[str, Any]], *, redact: bool
) -> Response:
    assembled = request.app.state.assembled

    # The reads themselves are `read_models.overview_reads` — the same call
    # `/dashboard/api/overview` makes, so the page and the JSON can never
    # answer out of two different passes over the corpus.
    data = await overview_reads(request, readiness_task, redact=redact)
    if data.error is not None:  # pragma: no cover - corpus_summary has no error path
        return _render(
            "error.html",
            {**_chrome(request, "corpus"), "error": data.error, "title": "Corpus"},
            status=HTTP_STATUS.get(data.error["code"], 500),
        )
    payload = data.corpus or {}

    return _render(
        "overview.html",
        {
            **_chrome(request, "corpus"),
            "title": "Corpus",
            "corpus": payload,
            "rollup": data.rollup,
            "recent": data.recent,
            "channels": payload.get("channels", []),
            "tags": payload.get("tags", {}),
            "gaps": payload.get("gaps", {}),
            # §5.1's job line, and the two links it is made of. The window is
            # in the context rather than in the copy so the sentence and the
            # query behind it are the same number.
            "jobs": data.health,
            "failed_window_h": FAILED_WINDOW_S // 3600,
            # The three the projection drops (§2.4). `None` rather than absent:
            # `StrictUndefined` is on, so a template that forgets the guard is a
            # loud failure in a test rather than a blank panel on a public page.
            "config": None if redact else _declared_models(assembled.db.config),
            "vectors": assembled.db.vectors,
            # The reason is a config/dimension mismatch written for the
            # operator; the *state* it caused is the visitor's business, because
            # search answers differently without the vector legs.
            "drift_reason": None if redact else assembled.db.vectors.reason,
            "storage": data.storage,
            "readiness": data.readiness,
        },
    )


# ---------------------------------------------------------------- §17 ledger


async def ledger(request: Request) -> Response:
    """`GET /dashboard/ledger` — every key number this instance can count.

    The page exists because the numbers were scattered (Tom, 2026-08-13): the
    corpus counts are on the overview, the queue's are in two sentences beside
    them, the per-state video counts were only ever a *filter* on the videos
    table, and the byte totals are a panel three screens down. An operator who
    wants "what does this box hold, and what is it behind on" was reading four
    pages and doing arithmetic.

    It adds no read the surface did not already have the right to make, and no
    per-video work: every figure is a whole-table or index count
    (`corpus_rollup`, `corpus_ledger`, `gaps`, `embed_backlog`,
    `job_state_counts`, `job_health`), and the readiness observation is the
    overview's own, made concurrently with them for the same reason.
    """
    redact = _redacted(request)
    readiness_task = asyncio.create_task(_pipeline_readiness(request, redact=redact))
    try:
        return await _ledger_page(request, readiness_task, redact=redact)
    finally:
        if not readiness_task.done():
            readiness_task.cancel()


async def _ledger_page(
    request: Request, readiness_task: asyncio.Task[dict[str, Any]], *, redact: bool
) -> Response:
    # The same reads `/dashboard/api/ledger` makes, in the same order and under
    # the same projection rule — `read_models.ledger_reads`.
    data = await ledger_reads(request, readiness_task, redact=redact)
    rollup = data.rollup

    return _render(
        "ledger.html",
        {
            **_chrome(request, "ledger"),
            "title": "Ledger",
            "rollup": rollup,
            "ledger": data.ledger,
            # `corpus-summary`'s `videos` without the tool call: the rollup
            # already splits the corpus into ready and not-ready, and the two
            # add up to it by construction (`_CORPUS_SQL`'s `<> 'ready'`). This
            # page has no use for the channel and tag *lists* the tool would
            # also build, so it does not ask for them.
            "corpus_videos": int(rollup["videos_ready"]) + int(rollup["videos_pending"]),
            "gaps": data.gaps,
            "backlog": data.backlog,
            "jobs": data.jobs_by_state,
            "health": data.health,
            "failed_window_h": FAILED_WINDOW_S // 3600,
            "storage": data.storage,
            "readiness": data.readiness,
            # The clock of this reading. Every figure on the page was counted
            # inside this request, so the page carries one timestamp and not a
            # per-panel one — there is no cache and no sample behind any of them.
            "counted_at": iso_z(time.time()),
        },
    )


# --------------------------------------------------------------- search


def _search_receipt(hit: dict[str, Any]) -> dict[str, str] | None:
    """The tool's YouTube link, admitted as one exact-second receipt."""
    raw = hit.get("link")
    if not isinstance(raw, str):
        return None
    parsed = urlsplit(raw)
    query = parse_qs(parsed.query)
    seconds = query.get("t", [None])[0]
    if (
        parsed.scheme != "https"
        or parsed.hostname != "youtu.be"
        or not parsed.path.strip("/")
        or seconds is None
        or not seconds.isdigit()
    ):
        return None
    return {
        "href": raw,
        "label": f"youtu.be/{parsed.path.strip('/')}?t={seconds}",
    }


def _search_inside(hit: dict[str, Any]) -> str | None:
    """The same moment on this dashboard's own video page.

    The receipt beside it goes to YouTube, which is the product's argument; this
    goes to what the index actually *stored* about that second, which is what
    this surface is for. Both, on every hit: the operator who is checking
    whether OCR read a slide correctly does not want to be sent to the video to
    find out.

    A hit that names a keyframe lands **on that frame**. `ord` is dense per
    video and the strip pages by ordinal, so which page holds it is arithmetic
    rather than a second query — the same arithmetic the shot bars have always
    done (`_shot_bars`) — and `select=` plus `#frame-N` mark it whether or not
    the script runs.

    A transcript hit does not get the same treatment, and that is deliberate. It
    names its cues by **id**, the transcript panel pages by *offset*, and there
    is no honest arithmetic from one to the other; a link that landed on cue
    page 1 while claiming to point at 1:12:03 would be the page inventing a
    position. It links to the video plainly and lets the panel say where it is.
    """
    video_id = hit.get("video_id")
    if not isinstance(video_id, str) or not video_id:
        return None
    page = f"{ROOT}/videos/{quote(video_id, safe='')}"
    frame_id = hit.get("frame_id")
    prefix = f"{video_id}-"
    if not isinstance(frame_id, str) or not frame_id.startswith(prefix):
        return page
    tail = frame_id[len(prefix) :]
    if not tail.isdigit():
        return page
    ordinal = int(tail)
    params = urlencode(
        {"frame_offset": (ordinal // FRAME_PAGE) * FRAME_PAGE, "select": ordinal}
    )
    return f"{page}?{params}#frame-{ordinal}"


# The picker's own vocabulary. `content_type` is a *parameter* — the value in
# the URL stays `ocr`, and the tool never sees anything else — but the word in
# the list is the one the demo's chips use, because "ocr" is a filter an
# operator has to already know the meaning of.
_CONTENT_WORDS = {
    "all": "all three channels",
    "transcript": "transcript (spoken)",
    "ocr": "on-screen text (OCR)",
    "frame": "frames (visual)",
}


# Three sources are three kinds of evidence, and the page says which one it is
# holding rather than letting the snippet imply it. The words are the demo's own
# (`web/src/lib/group.ts`), so a badge means the same thing on both surfaces:
# `spoken` was said out loud, `on-screen` was read off the screen by OCR,
# `frame` matched on the picture with no text involved at all. `kind` is what
# the stylesheet colours from — and only `screen` is allowed lime, because
# only `screen` is evidence the machine read off a slide (The Lime Rule).
_SOURCE_WORDS: dict[str, tuple[tuple[str, str], ...]] = {
    "transcript": (("spoken", "spoken"),),
    "ocr": (("on-screen", "screen"),),
    "frame": (("frame", "frame"),),
    "transcript+ocr": (("spoken", "spoken"), ("on-screen", "screen")),
}


def _search_evidence(source: Any) -> dict[str, Any]:
    """The hit's `source` as words a human reads, with the key kept.

    The raw string travels with the words rather than being replaced by them:
    this is an instrument, `source=transcript+ocr` is what a bug report quotes,
    and a page that only prints `spoken · on-screen` has thrown away the thing
    the tool actually returned.

    A source this table does not know still gets a badge carrying its own name
    — a fourth leg one day must arrive as an unfamiliar word, never as a hit
    with no provenance on it at all.
    """
    key = source if isinstance(source, str) and source else ""
    words = _SOURCE_WORDS.get(key) or ((key, "other"),) if key else ()
    return {
        "key": key,
        "pills": [{"label": label, "kind": kind} for label, kind in words],
        # Which of the four ways the snippet under it should be set: a
        # quotation of speech, a mono lime line the machine read, mono muted
        # text that merely rode along with a picture, or neither.
        "kind": (
            "mixed" if key == "transcript+ocr" else (words[0][1] if words else "other")
        ),
    }


# The query planner's own leg names, in a human's words. The count line under a
# search is the one thing tool-surface.md tells callers to *read* — `fts 0` is
# how you learn the corpus does not contain your phrasing — so the dashboard
# prints it as a sentence rather than as eight identifiers. The raw key stays
# beside each label: an operator comparing this page against a `search` payload
# is comparing keys, not prose.
#
# The units are part of the labels because the numbers are three different units
# and are famously not summands (tool-surface.md §9.2, terra eval): the fused
# leg counts segments, `fts` counts cues, the vector legs count chunks and
# frames, and `…_knn` is what the nearest-neighbour search *considered* before
# the relevance band cut it down.
_LEG_LABELS: tuple[tuple[str, str, str], ...] = (
    ("transcript", "Transcript — ranked into these results", "segments"),
    ("transcript_fts", "Transcript — keyword match (FTS)", "cues"),
    ("transcript_vec", "Transcript — semantic match (embeddings)", "chunks kept"),
    ("transcript_vec_knn", "Transcript — semantic candidates considered", "chunks"),
    ("ocr", "On-screen text (OCR)", ""),
    ("frame", "Frames — visual match", ""),
    ("frame_vec", "Frames — semantic match (embeddings)", "frames kept"),
    ("frame_knn", "Frames — visual candidates considered", "frames"),
)


def _search_legs(leg_counts: Any) -> list[dict[str, Any]]:
    """`leg_counts` in reading order, each with its label, unit and raw key.

    Ordered by this table rather than by the mapping, so a sub-leg always sits
    under the leg it explains; a key the table does not know is appended with
    its own name for a label rather than dropped, for the same reason an
    unfamiliar `source` still gets a badge.
    """
    if not isinstance(leg_counts, dict):
        return []
    known = [key for key, _, _ in _LEG_LABELS]
    order = known + [key for key in leg_counts if key not in known]
    labels = {key: (label, unit) for key, label, unit in _LEG_LABELS}
    legs = []
    for key in order:
        if key not in leg_counts:
            continue
        label, unit = labels.get(key, (key, ""))
        legs.append(
            {
                "key": key,
                "label": label,
                "unit": unit,
                "count": leg_counts[key],
                # A sub-leg is a *candidate* count behind a fused one, so it is
                # set one step back rather than printed as a ninth peer.
                "sub": key not in ("transcript", "ocr", "frame"),
            }
        )
    return legs


# Enough terms to mark a real question, few enough that a query pasted out of a
# log cannot turn a snippet into a solid block of gold; and a ceiling on the
# marks themselves, because the text this runs over is capped by the tool but
# the number of occurrences in it is not.
HIGHLIGHT_TERMS = 8
HIGHLIGHT_MARKS = 40


def _highlighted(text: Any, query: str) -> list[dict[str, Any]]:
    """The snippet, split into the parts the query matched and the parts it did
    not — never into markup.

    The demo marks the matched words and it is the difference between reading a
    result and scanning one. What it does *not* do here is build HTML: this
    returns text runs with a flag, the template decides what a marked run looks
    like, and every one of them goes through autoescape on the way out. A slide
    that says `<script>` is a normal slide.

    Not the tool's own matching: the FTS leg stems and the vector legs do not
    match words at all, so a mark is "these are your words, here" and never a
    claim about *why* this hit ranked. A hit with nothing marked is ordinary —
    that is what a semantic match looks like.
    """
    if not isinstance(text, str) or not text:
        return []
    terms = sorted(
        {word for word in re.split(r"[^\w'-]+", query.lower()) if len(word) > 1},
        key=len,
        reverse=True,
    )[:HIGHLIGHT_TERMS]
    if not terms:
        return [{"text": text, "hit": False}]
    pattern = re.compile("|".join(re.escape(term) for term in terms), re.IGNORECASE)
    parts: list[dict[str, Any]] = []
    cursor = 0
    for n, match in enumerate(pattern.finditer(text)):
        if n >= HIGHLIGHT_MARKS:
            break
        if match.start() > cursor:
            parts.append({"text": text[cursor : match.start()], "hit": False})
        parts.append({"text": match.group(0), "hit": True})
        cursor = match.end()
    if cursor < len(text):
        parts.append({"text": text[cursor:], "hit": False})
    return parts


def _search_page_link(filters: dict[str, Any], offset: int) -> str:
    params = {key: value for key, value in filters.items() if value not in (None, "")}
    params["offset"] = max(0, offset)
    return f"{ROOT}/search?{urlencode(params)}"


async def search(request: Request) -> Response:
    """Human inspection over the exact handler used by both JSON facades."""
    deps: Deps = request.app.state.assembled.deps
    params = request.query_params
    filters = {
        "q": (params.get("q") or "")[:512],
        "content_type": (
            params.get("content_type")
            if params.get("content_type") in CONTENT_TYPES
            else "all"
        ),
        "channel": (params.get("channel") or "")[:128],
        "limit": params.get("limit") or "",
        "max_text_chars": params.get("max_text_chars") or "",
    }
    searched = "q" in params
    payload: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    status = 200
    if searched:
        payload, status = await search_payload(request, verbatim_notes=True)
        if "error" in payload:
            error = payload
            payload = None
        else:
            for hit in payload.get("results", []):
                hit["receipt"] = _search_receipt(hit)
                hit["inside"] = _search_inside(hit)
                hit["evidence"] = _search_evidence(hit.get("source"))
                hit["parts"] = _highlighted(hit.get("text"), filters["q"])
                # The facade decorated the hit for a JSON reader: absolute
                # thumbnail URLs built from `PUBLIC_URL`, at the widths the
                # public page asks for. Both are replaced rather than added to,
                # because a page knows its own host better than `PUBLIC_URL`
                # does (`_thumb`) and because the widths this surface may ask
                # for are the fixed three (§6.4) — a fourth is a fourth JPEG per
                # keyframe in a cache that is capped in bytes.
                frame_id = hit.get("frame_id")
                hit["thumb"] = _thumb(deps, frame_id, STRIP_WIDTH)
                hit["thumb_large"] = _thumb(deps, frame_id, LIGHTBOX_WIDTH)

    pagination = (payload or {}).get("pagination", {})
    limit = int(pagination.get("limit") or OWNER_CLAMPS.search_default_limit)
    offset = int(pagination.get("offset") or 0)
    return _render(
        "search.html",
        {
            **_chrome(request, "search"),
            "title": "Search",
            "filters": filters,
            "content_types": CONTENT_TYPES,
            "content_words": _CONTENT_WORDS,
            "searched": searched,
            "payload": payload,
            "legs": _search_legs((payload or {}).get("leg_counts")),
            "error": error,
            "previous": (
                _search_page_link(filters, max(0, offset - limit)) if offset else None
            ),
            "next": (
                _search_page_link(filters, offset + limit)
                if pagination.get("has_more")
                else None
            ),
        },
        status=status,
    )


# ---------------------------------------------------------------- §5.2 videos


async def videos(request: Request) -> Response:
    """`GET /dashboard/videos` — the table, its filters and its exact count.

    Every read, clamp and refusal is `read_models.videos_reads`', so this and
    `/dashboard/api/videos` answer out of one query assembly (§20). What is
    left here is the page: the template's context, and the strings the tool
    already rendered for it.
    """
    data = await videos_reads(request)

    def context(**rest: Any) -> dict[str, Any]:
        return {
            **_chrome(request, "videos"),
            "title": "Videos",
            **rest,
            # Every key the form and the link macros read, whatever went wrong:
            # a refused date must still render a band with the other seven
            # controls in it, so the reader can fix the one that broke instead
            # of losing the query.
            "filters": data.filters,
            "orders": VIDEO_ORDERS,
            "has_values": HAS_VALUES,
            "index_states": queries.INDEX_STATES,
        }

    if data.error is not None:
        return _render(
            "videos.html",
            context(
                error=data.error,
                rows=[],
                pagination={
                    "limit": data.limit,
                    "offset": data.offset,
                    "has_more": False,
                },
                # No count, rather than a zero: a refused date filtered nothing,
                # so there is no set to have counted.
                total=None,
            ),
            status=HTTP_STATUS.get(data.error["code"], 500),
        )
    return _render(
        "videos.html",
        context(
            error=None,
            rows=data.rows,
            pagination=data.pagination,
            total=data.total,
        ),
    )


# ---------------------------------------------------------------- §5.3 detail


async def video_detail(request: Request) -> Response:
    """`GET /dashboard/videos/{video_id}` — the five panels, one read each.

    The reads are `read_models.video_detail_reads`', in the order they have
    always run; what stays here is the page's own arithmetic — the shot band's
    percentages, the `?select=` ordinal it lands on, and the prefill link into
    the index form.
    """
    params = request.query_params
    video_id = request.path_params["video_id"]

    frame_page = clamp(params.get("frames"), 1, FRAME_PAGE_MAX, FRAME_PAGE)  # type: ignore[arg-type]
    frame_offset = clamp(params.get("frame_offset"), 0, 100_000, 0)  # type: ignore[arg-type]
    # The keyframe a shot bar pointed at, by `ord` (2026-08-10, review round 4).
    # A bar's link has always carried the `frame_offset` of the strip page that
    # holds its first frame; what it could not carry was *which* frame, because
    # `#frame-N` is a fragment and a fragment never reaches a server. So a bar
    # that pointed off the current page navigated and then marked nothing —
    # which on real data is most of the band, because a talk has one shot per
    # keyframe and a strip page holds twenty-four of them. `select` is the same
    # ordinal as the fragment, in the query string, so the landing page can put
    # the frame into evidence itself. `None` when absent: `0` is a real ordinal.
    selected_ord = _selected_ord(params.get("select"))
    cue_page_size = clamp(params.get("cues"), 1, CUE_PAGE_MAX, CUE_PAGE)  # type: ignore[arg-type]
    cue_offset = clamp(params.get("cue_offset"), 0, 500_000, 0)  # type: ignore[arg-type]

    data = await video_detail_reads(
        request,
        video_id,
        frame_page=frame_page,
        frame_offset=frame_offset,
        cue_page=cue_page_size,
        cue_offset=cue_offset,
        redact=_redacted(request),
    )
    if data is None:
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

    row = data.row
    duration = float(row["duration_s"] or 0.0)
    return _render(
        "video.html",
        {
            **_chrome(request, "videos"),
            "title": str(row["title"]),
            "video": _video_header(row, data.tags),
            "duration_s": duration,
            "data_status": data.summary.get("data_status"),
            "summary_error": data.summary_error,
            "chapters": data.summary.get("chapters", []),
            "stages": data.stages,
            "counts": data.counts,
            "origins": data.origins,
            "shots": _shot_bars(data.shots, duration, frame_page),
            "shots_capped": data.shots_capped,
            "frames": data.frames,
            # The honest half of the double cap: when the *page's* line budget
            # is spent the per-frame counts under-report by definition, so the
            # panel says so rather than printing a short list as if it were the
            # whole one. This is the only OCR bound left — the per-frame one
            # went with the digest.
            "ocr_line_cap": OCR_LINE_CAP,
            "ocr_lines_capped": data.ocr_lines_capped,
            "frame_page": frame_page,
            "frame_offset": frame_offset,
            "frames_more": data.frames_more,
            "selected_ord": selected_ord,
            "cues": _cue_rows(data.cues or [], data.chunks),
            "cue_totals": data.cue_totals,
            "cue_page": cue_page_size,
            "cue_offset": cue_offset,
            "cues_more": data.cues_more,
            # A GET prefill, not a write. The source URL is encoded into one
            # internal dashboard link; the index form remains the place where
            # the operator reviews it and POST remains the only state change.
            "queue_channel_url": f"{ROOT}/index?"
            + urlencode({"urls": str(row["url"]), "expand": "channel_recent"}),
            "job_history": data.history,
            "job_history_cap": VIDEO_HISTORY_CAP,
        },
    )


def _selected_ord(raw: str | None) -> int | None:
    """`?select=` as an ordinal, or ``None``.

    Not :func:`clamp`, because every other bound on this page has a sensible
    default and this one does not: `clamp(None, 0, …, 0)` would select frame 0
    on every page load, and a page that arrives with a keyframe already marked
    is a page reporting a click nobody made. An unparseable or negative value
    is the same as no value — the URL is an input.
    """
    if raw is None or not raw.strip().isdigit():
        return None
    return min(int(raw.strip()), 100_000)


def _shot_bars(
    shots: list[dict[str, Any]], duration: float, frame_page: int
) -> list[dict[str, Any]]:
    """The shot facts as percentages of the runtime, each pointing at a page.

    The link is a real `<a href>` carrying the `frame_offset` of the strip page
    that holds the shot's first keyframe — `ord` is dense per video, so the
    offset is arithmetic rather than another query. Clicking a shot works with
    JavaScript off, which is the difference between a timeline and a
    decoration. Both numbers are the page's own: a percentage is a rendering,
    and `read_models.shot_rows` is what the JSON answers with.
    """
    runtime = duration if duration > 0 else max(
        (float(shot["end_s"]) for shot in shots), default=1.0
    )
    runtime = runtime or 1.0
    bars = []
    for shot in shots:
        start, end = float(shot["start_s"]), float(shot["end_s"])
        bars.append(
            {
                **shot,
                "left": round(100.0 * min(start, runtime) / runtime, 4),
                "width": round(100.0 * max(end - start, 0.0) / runtime, 4) or 0.05,
                "frame_offset": (int(shot["first_ord"]) // frame_page) * frame_page,
            }
        )
    return bars


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

    The scrollbox replaced a "Next N cues" button (Tom, 2026-08-10), so the
    reader never leaves the page and the batch has to arrive as data. This is
    the *same* read the page made — `queries.cue_page` plus `chunk_spans` over
    the same cue-id window — with the same server-side clamps, and every string
    the script assigns is formatted here: the timecode, the chunk label, the
    confidence. The script carries no formatter of its own, which is the rule
    `jobs.js` already keeps.

    `has_more` and not a total: the page's own "of N" comes from
    `per_video_counts`, which it already read for the counts band, so nothing
    here duplicates a count query.
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
            headers={"Cache-Control": "no-store"},
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
                    "at": clock(cue["start_s"]),
                    "t": int(cue["start_s"]),
                    "text": cue["text"],
                    "speaker": cue["speaker"],
                    "conf": None
                    if cue["avg_logprob"] is None
                    else f"{cue['avg_logprob']:.2f}",
                    "in_chunk": bool(cue["chunk_opens"] or cue["chunk_closes"]),
                    "chunk": None
                    if cue["chunk_opens"] is None
                    else (
                        f"chunk {cue['chunk_opens']['seq']} · "
                        f"{clock(cue['chunk_opens']['start_s'])}–"
                        f"{clock(cue['chunk_opens']['end_s'])} · "
                        f"{cue['chunk_opens']['n_words']} words · "
                        f"{cue['chunk_opens']['n_chars']} chars"
                    ),
                }
                for cue in _cue_rows(cue_rows, chunks)
            ],
            "offset": offset,
            "limit": limit,
            "has_more": has_more,
        },
        headers={"Cache-Control": "no-store"},
    )


# ----------------------------------------------------------------- §5.4 jobs

# Owner clamps again, server-side. A job page is cheap — two reads for the
# list, six for the detail, whatever the row count — but "cheap" is not
# "unbounded" and the URL is an input.
JOB_PAGE = 25
JOB_PAGE_MAX = 100
ITEM_CAP = 200  # `index-video` cannot create more: max_items clamps to 200
EVENT_CAP = 60
DEGRADED_CAP = 40

_JOB_STATES = ("all", "active", "failed", "done")
# `follow_check` is a `jobs.kind` since migration 0006, so it is a filter here
# the day it is a kind: a job the queue can hold and this view cannot select
# for is a job an operator triages by reading past it.
_JOB_KINDS = ("all", "index", "reindex", "delete", "follow_check")
_JOB_ORDERS = ("newest", "priority", "wall_clock")

# 2 s while anything is `queued|running`, stopped when nothing is (§5.4). Not
# an env var: a poll interval that is a deployment knob is a poll interval
# somebody sets to 100 ms, and the rate limiter would then be the only thing
# saying no. The page ships the number to its own script and to nothing else.
POLL_MS = 2_000

# The states that mean "this will change under the reader".
LIVE_STATES = ("queued", "running")


def _counts_line(card: dict[str, Any]) -> str:
    parts = [f"{card['n_done']}/{card['n_items']} done"]
    for key, word in (("n_failed", "failed"), ("n_skipped", "skipped"),
                      ("n_cancelled", "cancelled")):
        if card[key]:
            parts.append(f"{card[key]} {word}")
    return " · ".join(parts)


def _job_card(
    row: sqlite3.Row, now: int, *, degraded: int = 0, redact: bool = False
) -> dict[str, Any]:
    """One job, with the three durations it actually has.

    The semantics are the fixed ones (`jobs/store.claim_next`): `started_at` is
    the **first** claim, not the most recent, so `created_at → finished_at` is
    the honest wall clock and `started_at → finished_at` is time on the runner.
    A deferred job spends the difference waiting, which is the whole reason for
    printing both — a 92-minute overnight job that reported "started 40s ago"
    is what the fix was for.
    """
    state = str(row["state"])
    created = int(row["created_at"] or 0)
    started = row["started_at"]
    finished = row["finished_at"]
    started = int(started) if started is not None else None
    finished = int(finished) if finished is not None else None
    live = state in LIVE_STATES
    end = finished if finished is not None else (now if live else None)
    card = {
        "job_id": str(row["public_id"]),
        "state": state,
        "kind": str(row["kind"]),
        "priority": int(row["priority"]),
        "progress": int(round(float(row["progress"] or 0.0) * 100)),
        "n_items": int(row["n_items"] or 0),
        "n_done": int(row["n_done"] or 0),
        "n_failed": int(row["n_failed"] or 0),
        "n_skipped": int(row["n_skipped"] or 0),
        "n_cancelled": int(row["n_cancelled"] or 0),
        "cancel_requested": bool(row["cancel_requested"]),
        "created_at": created,
        "started_at": started,
        "finished_at": finished,
        # Queued and never claimed: it has waited, it has not run.
        "waited_s": None if started is None else max(0, started - created),
        "ran_s": None if started is None or end is None else max(0, end - started),
        "wall_s": None if end is None else max(0, end - created),
        "live": live,
        # The line that was missing. Only a *queued* job is actually being held
        # off — `not_before` on a running row is a stamp the last deferral left
        # behind, and a countdown against it would invent a wait that is not
        # happening.
        "defer_s": int(row["defer_s"] or 0) if state == "queued" else 0,
        "error_code": row["error_code"],
        "error_message": None if redact else row["error_message"],
        "degraded": int(degraded),
    }
    # Every changing value, formatted once, server-side. The page renders these
    # strings and the 2 s tick assigns the same strings to the same nodes, so
    # the poller needs no formatter of its own and cannot drift into a second
    # way of saying "4m 12s" (the one exception is the countdown between ticks,
    # which is arithmetic on a number this already sent).
    # What the percentage is made of, and what it is computed over (Tom,
    # 2026-08-10, round 4: "the progress % is unexplained"). All five buckets,
    # always, including the zeroes — the point of the line is that they add up
    # to `n_items`, and a tally with terms missing does not visibly add up.
    pending = max(
        0,
        card["n_items"]
        - card["n_done"]
        - card["n_failed"]
        - card["n_skipped"]
        - card["n_cancelled"],
    )
    card["text"] = {
        "progress": f"{card['progress']}%",
        "counts": _counts_line(card),
        "tally": " · ".join(
            (
                f"{card['n_done']} done",
                f"{card['n_failed']} failed",
                f"{card['n_skipped']} skipped",
                f"{card['n_cancelled']} cancelled",
                f"{pending} still to run",
            )
        ),
        # The rule, in the one place a reader can ask for it. `jobs_store.STAGES`
        # rather than a literal 7, because the fraction in `_ITEM_FRACTION` is
        # divided by that same tuple's length.
        "basis": (
            f"of {card['n_items']} item(s). An item still in the pipeline counts "
            f"the stages it has finished, out of {len(jobs_store.STAGES)}."
        ),
        "wall": span(card["wall_s"]),
        "ran": span(card["ran_s"]),
        "waited": span(card["waited_s"]),
        "defer": span(card["defer_s"]),
        # When it stopped, not only when it was asked for (Tom, 2026-08-13:
        # "created is not enough"). `created_at` answers "when did I queue
        # this"; the operator arriving at 03:00 is asking "when did the batch
        # actually end", and until now the only page that said so was the job's
        # own. A job that has not finished says so with the em dash this
        # surface already uses for "not recorded" (`render.dash`) rather than
        # with an empty cell — and because a running job *acquires* the value
        # under the reader, it is formatted here and patched by the tick, like
        # every other changing string on this row.
        "finished": iso_minute(finished) if finished else "—",
    }
    return card


def _job_item(row: sqlite3.Row, now: int, *, redact: bool = False) -> dict[str, Any]:
    state = str(row["state"])
    started = row["started_at"]
    finished = row["finished_at"]
    started = int(started) if started is not None else None
    finished = int(finished) if finished is not None else None
    end = finished if finished is not None else (now if state == "running" else None)
    attempts = int(row["attempts"] or 0)
    max_attempts = int(row["max_attempts"] or 0)
    item = {
        "item_id": int(row["id"]),
        "seq": int(row["seq"]),
        "state": state,
        "stage": row["stage"],
        "stage_pct": int(round(float(row["stage_pct"] or 0.0) * 100)),
        "attempts": attempts,
        "max_attempts": max_attempts,
        # `ItemFailed.retryable` is not persisted (§4.4), so no row can say
        # "this will retry". This is the half of the inference the item carries;
        # the other half is the job's countdown.
        "retries_left": max(0, max_attempts - attempts) if state == "queued" else 0,
        "video_id": row["public_id"],
        "title": row["title"],
        "channel": row["channel_name"],
        "duration_s": row["duration_s"],
        # The submitted URL is the redacted field: it is `args_json`'s content
        # by another name. The *video* it resolved to is not — the demo lists
        # that video, by id and title, on two other pages.
        "source_url": None if redact else str(row["source_url"]),
        "error_code": row["error_code"],
        "error_message": None if redact else row["error_message"],
        "started_at": started,
        "finished_at": finished,
        "took_s": None if started is None or end is None else max(0, end - started),
    }
    item["text"] = {
        "attempts": f"{attempts}/{max_attempts}",
        "took": span(item["took_s"]),
        "stage": (
            f"{item['stage']} {item['stage_pct']}%" if item["stage"] else "—"
        ),
    }
    return item


def _job_event(row: sqlite3.Row, *, redact: bool = False) -> dict[str, Any]:
    """One `job_events` row, with its message dropped in the demo projection.

    The message is the one field on this surface that is *both* redacted things
    at once: the runner writes `"retrying in {delay}s after {code}: {message}"`
    with yt-dlp's string inside it, and a reclaim writes the item's URL. There
    is no structured half to keep, so demo mode keeps the shape of the log —
    when, how loud, which stage — and none of the prose. The clocks survive,
    which is what §10.4 asked the demo to keep.
    """
    return {
        "id": int(row["id"]),
        "at": int(row["at"]),
        # Formatted here so an event that arrives on a tick is stamped the same
        # way as one that arrived with the page, by the same function.
        "at_text": iso_minute(int(row["at"])),
        "level": str(row["level"]),
        "stage": row["stage"],
        "item_id": row["item_id"],
        "message": None if redact else str(row["message"]),
    }


async def jobs(request: Request) -> Response:
    """`GET /dashboard/jobs` — bounded triage with explicit ordering."""
    db = request.app.state.assembled.db
    params = request.query_params
    state = params.get("state") if params.get("state") in _JOB_STATES else "all"
    kind = params.get("kind") if params.get("kind") in _JOB_KINDS else "all"
    order = params.get("order") if params.get("order") in _JOB_ORDERS else "newest"
    error_code = str(params.get("error_code") or "").strip()[:64]
    degraded_only = params.get("degraded") == "1"
    limit = clamp(params.get("limit"), 1, JOB_PAGE_MAX, JOB_PAGE)  # type: ignore[arg-type]
    offset = clamp(params.get("offset"), 0, OWNER_CLAMPS.offset_max, 0)  # type: ignore[arg-type]
    redact = _redacted(request)

    cards, has_more, now = await _job_page(
        db, state, limit, offset, redact, error_code, kind, degraded_only, order
    )
    # One extra grouped read for the page, not one per row (§6.3). Deliberately
    # here and not in `_job_page`: what a job contains does not change between
    # two ticks of the poller, so the JSON the tick reads does not carry it.
    contents = await db.read(
        lambda c: queries.job_contents(c, [card["job_id"] for card in cards])
    )
    for card in cards:
        card["contents"] = _job_contents(card, contents.get(card["job_id"]))
    return _render(
        "jobs.html",
        {
            **_chrome(request, "jobs"),
            "title": "Jobs",
            "jobs": cards,
            "states": _JOB_STATES,
            "filters": {
                "state": state,
                "kind": kind,
                "error_code": error_code,
                "degraded": degraded_only,
                "order": order,
                "limit": limit,
            },
            "kinds": _JOB_KINDS,
            "orders": _JOB_ORDERS,
            "pagination": {"limit": limit, "offset": offset, "has_more": has_more},
            "live": any(card["live"] for card in cards),
            "now": now,
            "poll_ms": POLL_MS,
            "redacted": redact,
        },
    )


def _job_contents(card: dict[str, Any], row: sqlite3.Row | None) -> dict[str, Any]:
    """The line that says what a job holds, from its own items.

    A jobs table whose rows print only `job_uid` is a list of opaque handles
    (Tom, 2026-08-10, round 4). What the reader wants is what went in: the first
    video's title with the rest counted after it, and — when every item that has
    resolved so far came from one channel — that channel's name, which is the
    playlist or channel a batch was expanded from by another route.

    The submitted URL is **not** here and must not be: §2.4's redaction table
    drops it in the demo projection, and the title and channel it resolved to
    are corpus, published on two other pages. Keeping one rule for both modes
    is what stops the two drifting apart.

    A job whose items have not been fetched yet has no title to print, and says
    so with the count it does have rather than borrowing the id as a name.
    """
    n_items = int(card["n_items"])
    if row is None or not row["first_title"]:
        return {
            "title": None,
            "more": 0,
            "channel": None,
            "note": f"{n_items} item(s), none fetched yet",
        }
    return {
        "title": str(row["first_title"]),
        "more": max(0, n_items - 1),
        # One channel across every resolved item, or none named at all: "two of
        # these came from somewhere else" is not a fact a row can print in three
        # words, and naming only the first would be a claim about the rest.
        "channel": str(row["channel"]) if int(row["channels"]) == 1 and row["channel"] else None,
        "note": None,
    }


async def _job_page(
    db: Any,
    state: str,
    limit: int,
    offset: int,
    redact: bool,
    error_code: str = "",
    kind: str = "all",
    degraded_only: bool = False,
    order: str = "newest",
) -> tuple[list[dict[str, Any]], bool, int]:
    """Two reads for the whole page, whatever the row count (§6.3).

    One probe row past the limit rather than a count, exactly as the videos
    table pages, and one grouped `degraded_counts` for every row on the page
    rather than a probe per row.
    """
    rows = await db.read(
        lambda c: jobs_store.list_jobs(
            c,
            state,
            limit + 1,
            offset,
            error_code=error_code or None,
            kind=None if kind == "all" else kind,
            degraded_only=degraded_only,
            order=order,
        )
    )
    has_more = len(rows) > limit
    rows = rows[:limit]
    degraded = await db.read(
        lambda c: jobs_store.degraded_counts(c, [int(r["id"]) for r in rows])
    )
    now = int(time.time())
    cards = [
        _job_card(row, now, degraded=degraded.get(int(row["id"]), 0), redact=redact)
        for row in rows
    ]
    return cards, has_more, now


async def job_detail(request: Request) -> Response:
    """`GET /dashboard/jobs/{job_id}` — the war story of one job."""
    db = request.app.state.assembled.db
    job_id = request.path_params["job_id"]
    redact = _redacted(request)

    detail = await _job_detail(db, job_id, redact)
    if detail is None:
        return _render(
            "error.html",
            {
                **_chrome(request, "jobs"),
                "title": "Unknown job",
                "error": {
                    "code": "E_UNKNOWN_JOB",
                    "message": f'"{job_id}" is not a job on this instance.',
                    "next": "the jobs table lists every job this index has run.",
                },
            },
            status=404,
        )
    return _render(
        "job.html",
        {
            **_chrome(request, "jobs"),
            "title": f"Job {detail['job']['job_id']}",
            "poll_ms": POLL_MS,
            "redacted": redact,
            # The event log's digest bound (DESIGN.md, The digest).
            "event_preview": EVENT_PREVIEW,
            **detail,
        },
    )


async def _job_detail(db: Any, job_id: str, redact: bool) -> dict[str, Any] | None:
    """One job, its items, the stage table of the item in focus, and the tail.

    Six reads, and six however many items the job has. The stage table is read
    for the **one** item the job is actually on — running, else the last one to
    finish — because seven stage rows per item is precisely the fan-out §6.3
    forbids, and every other item's stages are one click away on its own video
    page.
    """
    row = await db.read(lambda c: jobs_store.get_job(c, job_id))
    if row is None:
        return None
    internal_id = int(row["id"])
    now = int(time.time())

    item_rows = await db.read(lambda c: jobs_store.job_items(c, internal_id, ITEM_CAP))
    counts = await db.read(lambda c: jobs_store.item_counts(c, internal_id))
    error_counts = await db.read(lambda c: jobs_store.item_error_counts(c, internal_id))
    degraded_rows = await db.read(
        lambda c: jobs_store.degraded_items(c, internal_id, DEGRADED_CAP)
    )
    events = await db.read(
        lambda c: jobs_store.job_event_page(c, internal_id, None, EVENT_CAP)
    )

    items = [_job_item(item, now, redact=redact) for item in item_rows]
    # The item the stage table is about. It has to have a video: `video_stages`
    # is keyed on one, and an item that never resolved to a video (a bad URL, a
    # bot-check on the fetch) has no stages to show — seven `absent` rows under
    # a heading with no name is a panel pretending to have an answer.
    resolved = [i for i in item_rows if i["video_id"] is not None]
    focus = next((i for i in resolved if str(i["state"]) == "running"), None)
    if focus is None:
        finished = [i for i in resolved if i["finished_at"] is not None]
        focus = max(finished, key=lambda i: int(i["finished_at"])) if finished else None
    stages: dict[str, sqlite3.Row] = {}
    if focus is not None:
        video_id = int(focus["video_id"])
        stages = await db.read(lambda c: jobs_store.item_stages(c, video_id))

    degraded = [
        {
            "seq": int(entry["seq"]),
            "video_id": entry["public_id"],
            "stage": str(entry["stage"]),
            "error": None if redact else entry["error"],
        }
        for entry in degraded_rows
    ]
    return {
        "job": _job_card(
            row, now, degraded=len({d["seq"] for d in degraded}), redact=redact
        ),
        "items": items,
        "items_capped": len(item_rows) >= ITEM_CAP,
        "counts": counts,
        "error_counts": error_counts,
        "degraded": degraded,
        "events": [_job_event(event, redact=redact) for event in events],
        "focus": None if focus is None else _job_item(focus, now, redact=redact),
        "stages": _focus_stages(stages),
        "now": now,
        "live": str(row["state"]) in LIVE_STATES,
    }


def _focus_stages(stages: dict[str, sqlite3.Row]) -> list[dict[str, Any]]:
    """The seven `video_stages` rows for the item in focus, in pipeline order.

    Durations included, and they are the answer to "what does indexing a video
    cost" — which is why they survive the demo projection whole (§10.4). A
    stage with no row yet is `absent`, the same word the provenance panel uses,
    rather than a blank the reader has to interpret.
    """
    rows = []
    for stage in queries.STAGE_ORDER:
        row = stages.get(stage)
        if row is None:
            rows.append({"stage": stage, "state": "absent", "started_at": None,
                         "finished_at": None, "took_s": None})
            continue
        started = row["started_at"]
        finished = row["finished_at"]
        rows.append(
            {
                "stage": stage,
                "state": str(row["state"]),
                "started_at": started,
                "finished_at": finished,
                "took_s": (
                    None
                    if started is None or finished is None or finished < started
                    else int(finished) - int(started)
                ),
            }
        )
    return rows


# ------------------------------------------------------- §5.4 the poll target


async def jobs_json(request: Request) -> Response:
    """`GET /dashboard/api/jobs` — what the 2 s tick reads.

    The same projection the page rendered, so the script patches values it
    could not have computed differently, and the demo's redaction is the one
    the page already applied rather than a second rule that can drift.

    `live` is the script's stop condition: when nothing is `queued|running`
    there is nothing to poll for, and the tab stops being a load generator
    against the process that also holds the only SQLite writer.
    """
    db = request.app.state.assembled.db
    params = request.query_params
    state = params.get("state") if params.get("state") in _JOB_STATES else "all"
    kind = params.get("kind") if params.get("kind") in _JOB_KINDS else "all"
    order = params.get("order") if params.get("order") in _JOB_ORDERS else "newest"
    error_code = str(params.get("error_code") or "").strip()[:64]
    degraded_only = params.get("degraded") == "1"
    limit = clamp(params.get("limit"), 1, JOB_PAGE_MAX, JOB_PAGE)  # type: ignore[arg-type]
    offset = clamp(params.get("offset"), 0, OWNER_CLAMPS.offset_max, 0)  # type: ignore[arg-type]
    cards, has_more, now = await _job_page(
        db,
        state,
        limit,
        offset,
        _redacted(request),
        error_code,
        kind,
        degraded_only,
        order,
    )
    return JSONResponse(
        {
            "now": now,
            "poll_ms": POLL_MS,
            "live": any(card["live"] for card in cards),
            "jobs": cards,
            "pagination": {"limit": limit, "offset": offset, "has_more": has_more},
        },
        headers={"Cache-Control": "no-store"},
    )


async def job_json(request: Request) -> Response:
    """`GET /dashboard/api/jobs/{job_id}` — one job, for the detail page's tick."""
    db = request.app.state.assembled.db
    detail = await _job_detail(db, request.path_params["job_id"], _redacted(request))
    if detail is None:
        return JSONResponse(
            {
                "error": "E_UNKNOWN_JOB",
                "message": "no such job.",
                "next": "the jobs table lists every job this index has run.",
            },
            status_code=404,
            headers={"Cache-Control": "no-store"},
        )
    return JSONResponse(
        {
            "now": detail["now"],
            "poll_ms": POLL_MS,
            "live": detail["live"],
            "job": detail["job"],
            "items": detail["items"],
            "events": detail["events"],
        },
        headers={"Cache-Control": "no-store"},
    )


# ----------------------------------------------------------------- following

# Page sizes, server-side and double-capped like every other list on this
# surface. The two lists are bounded independently of one another because they
# are different costs: the follow list is a join of two small tables, and the
# ledger is the one that grows for the life of the deployment.
FOLLOW_PAGE = 25
FOLLOW_PAGE_MAX = 100
SEEN_PAGE = 25
SEEN_PAGE_MAX = 100

# The two job lists on a follow's page. Bounded independently of `limit`,
# because neither of them is what the pager pages.
CHECK_CAP = 10
INDEX_JOB_CAP = 10

# How many held candidates the list page names above the table. The band's job
# is to say *that* something is waiting and give it a door, not to be a second
# ledger — the follow's own page is where the rows are read.
HELD_BAND_CAP = 5

# What counts as "nearly" for the one derived sentence above the ledger. Sixty
# seconds because a length rule is typed in minutes, and a minute is the
# smallest gap an operator would call a near miss. The page prints the number
# in the sentence, from this constant, so the two cannot disagree.
NEAR_MISS_S = 60

# Every decision except `queued` — which is to say, every candidate that did
# **not** become a video. This band is the point of the page: a follow that
# quietly drops a four-minute talk because its floor is eight would be the one
# place this index goes silent (migration 0006's own argument).
PASSED_OVER = (
    "held_budget",
    "held_review",
    "skipped_tab",
    "skipped_title",
    "skipped_duration",
    "skipped_horizon",
    "already_indexed",
    "failed",
)


def _follow_daily_hours(assembled: Any) -> float:
    """The ceiling the check enforces, read from where the check reads it.

    ``PipelineSettings.follow_daily_hours`` is resolved once at boot and handed
    to the runner; a build with the pipeline off (every test, and any deployment
    running the queue elsewhere) has no runner settings to ask, so the
    environment is re-read rather than guessed at. Zero means the operator
    turned the ceiling off, and the page says so in words rather than printing
    "of 0h", which reads as "no budget left" and means the opposite.
    """
    from ..pipeline.settings import PipelineSettings

    settings = getattr(getattr(assembled.runner, "pipeline", None), "settings", None)
    hours = getattr(settings, "follow_daily_hours", None)
    if hours is None:
        hours = PipelineSettings.from_env().follow_daily_hours
    return float(hours)


def _rule_facts(rules: follow_rules.Rules) -> list[str]:
    """The rule compressed to the facts that fit in a table cell.

    Not a second :func:`~vidtheque_mcp.follows.rules.describe`. That function
    renders the *policy* as one English sentence and it is the only thing that
    does; this is the same rule as a row of machine facts, because sixty follows
    scanned at 03:00 are a column to compare and not sixty sentences to read.
    The sentence is one click away, at the top of the follow's own page.
    """
    facts = [", ".join(f"/{tab}" for tab in rules.tabs)]
    low, high = rules.min_duration_s, rules.max_duration_s
    if low is not None and high is not None:
        facts.append(f"{duration_clock(low)}–{duration_clock(high)}")
    elif low is not None:
        facts.append(f"{duration_clock(low)} floor")
    elif high is not None:
        facts.append(f"{duration_clock(high)} ceiling")
    terms = len(rules.title_include) + len(rules.title_exclude)
    if terms:
        facts.append(f"{terms} title term(s)")
    if rules.channels != "all":
        facts.append(f"{rules.channels} only")
    facts.append(f"{rules.max_per_check}/check")
    facts.append(f"every {span(rules.check_interval_s)}")
    if rules.mode == "review":
        facts.append("held for review")
    return facts


def _follow_row(row: sqlite3.Row) -> dict[str, Any]:
    """One line of the table. Everything on it came off the row itself."""
    rules = follow_rules.Rules.from_row(row)
    return {
        "slug": str(row["slug"]),
        "name": str(row["title"] or row["slug"]),
        "state": str(row["state"]),
        "facts": _rule_facts(rules),
        "last_check": row["last_sync_at"],
        "last_new": row["last_new_at"],
        "next_check": row["next_check_at"],
        "error_code": row["last_error_code"],
    }


def _follow_choices() -> dict[str, Any]:
    """The vocabularies the form offers, from the modules that own them.

    Copied nowhere: ``tabs``, the channel sets and the two modes are
    ``follows/rules.py``'s constants, and the floors and ceilings are the ones
    ``follows/params.py`` clamps to. A form that repeated them would be a second
    policy, which §5.5 is explicit the form does not add.
    """
    from .writes import CHANNEL_BOXES

    return {
        "tabs": follow_rules.TABS,
        # The index form's own three boxes, verbatim: `index-video`'s `channels`
        # and a follow's `channels` are the same parameter, so they are the same
        # three labels and the same three notes, and a second copy of them here
        # is how the two forms start describing one thing two ways. Imported
        # inside the function because `writes` imports this module.
        "channels": CHANNEL_BOXES,
        "modes": follow_rules.MODES,
        "max_backfill": follow_rules.MAX_BACKFILL,
        "max_per_check": follow_rules.MAX_PER_CHECK,
        "min_interval_s": follow_rules.MIN_CHECK_INTERVAL_S,
        "default_interval_s": follow_rules.DEFAULT_CHECK_INTERVAL_S,
    }


def _follow_form_values(row: sqlite3.Row | None = None) -> dict[str, Any]:
    """The form's controls, either empty or filled from a follow's own row."""
    if row is None:
        return {
            "url": "",
            "title": "",
            "tabs": ["videos"],
            "min_duration": "",
            "max_duration": "",
            "title_include": "",
            "title_exclude": "",
            "channels": ["transcript", "ocr", "frames"],
            "tags": "",
            "backfill": 0,
            "max_per_check": 5,
            "mode": "auto",
            "check_interval_s": follow_rules.DEFAULT_CHECK_INTERVAL_S,
        }
    rules = follow_rules.Rules.from_row(row)
    return {
        "url": str(row["source_url"] or ""),
        "title": str(row["title"] or ""),
        "tabs": list(rules.tabs),
        "min_duration": (
            "" if rules.min_duration_s is None else duration_clock(rules.min_duration_s)
        ),
        "max_duration": (
            "" if rules.max_duration_s is None else duration_clock(rules.max_duration_s)
        ),
        "title_include": ", ".join(rules.title_include),
        "title_exclude": ", ".join(rules.title_exclude),
        "channels": (
            ["transcript", "ocr", "frames"]
            if rules.channels == "all"
            else follow_rules.split_terms(rules.channels)
        ),
        "tags": ", ".join(rules.tags),
        "backfill": rules.backfill,
        "max_per_check": rules.max_per_check,
        "mode": rules.mode,
        "check_interval_s": rules.check_interval_s,
    }


async def following(request: Request) -> Response:
    """`GET /dashboard/following` — every follow, and what they are costing.

    Four reads for the whole page whatever the row count: the totals band, the
    rolling budget, one page of follows probed one row past its limit, and the
    held band. No per-follow round trip, because a table that costs a query per
    line is a table that stops being loadable at the size it exists for (§6.3).
    """
    assembled = request.app.state.assembled
    db = assembled.db
    params = request.query_params
    limit = clamp(params.get("limit"), 1, FOLLOW_PAGE_MAX, FOLLOW_PAGE)  # type: ignore[arg-type]
    offset = clamp(params.get("offset"), 0, OWNER_CLAMPS.offset_max, 0)  # type: ignore[arg-type]

    totals = await db.read(follows_store.totals)
    spent_s = await db.read(follows_store.budget_spent_s)
    rows = await db.read(
        lambda c: follows_store.list_follows(c, limit=limit, offset=offset)
    )
    has_more = len(rows) > limit
    rows = rows[:limit]
    held_rows = await db.read(lambda c: follows_store.held(c, HELD_BAND_CAP))
    held_more = len(held_rows) > HELD_BAND_CAP
    held_rows = held_rows[:HELD_BAND_CAP]

    ceiling_h = _follow_daily_hours(assembled)
    return _render(
        "following.html",
        {
            **_chrome(request, "following"),
            "title": "Following",
            "follows": [_follow_row(row) for row in rows],
            "totals": totals,
            "budget": {
                # Hours of *video*, not GPU-minutes: the check knows a
                # candidate's length before it knows what indexing it will
                # cost, and hours-of-video is the number an operator reasons
                # about (`follows/store.budget_spent_s`).
                "spent": span(int(spent_s)),
                "ceiling": f"{ceiling_h:g}h" if ceiling_h else None,
            },
            "held": [
                {
                    "title": str(row["title"] or row["url"]),
                    "slug": str(row["follow_slug"]),
                    "follow": str(row["follow_title"] or row["follow_slug"]),
                }
                for row in held_rows
            ],
            "held_more": held_more,
            "form": _follow_form_values(),
            "choices": _follow_choices(),
            # §5.5's honest refusal, for the follow form too: `follow_channel`
            # raises `E_FEATURE_DISABLED` on the same condition `index_video`
            # does, so the page says so above the controls rather than after a
            # submission.
            "vectors": assembled.db.vectors,
            "pagination": {"limit": limit, "offset": offset, "has_more": has_more},
        },
    )


def _seen_row(row: sqlite3.Row) -> dict[str, Any]:
    """One ledger line. The ``reason`` is printed verbatim — it is the receipt.

    No thumbnail, and there cannot be one: an un-indexed video has no keyframe
    on this disk, and a YouTube thumbnail URL would be a runtime request off
    this box. The ledger is text.
    """
    return {
        "title": str(row["title"] or row["source_id"]),
        "url": str(row["url"]),
        "decision": str(row["decision"]),
        "reason": row["reason"],
        "judged_from": str(row["judged_from"]),
        "duration_s": row["duration_s"],
        "published_at": row["published_at"],
        "decided_at": row["decided_at"],
    }


def _near_miss(rows: list[sqlite3.Row], rules: follow_rules.Rules) -> str | None:
    """The one derived line above the ledger, or nothing at all.

    Read out of the rows this page already fetched, at render time — no second
    query, no stored aggregate, and no sentence when the number is zero. A
    "0 of the last 20" line is a fact about nothing dressed as a finding, and
    this band is the one place on the surface that must stay believable.
    """
    low, high = rules.min_duration_s, rules.max_duration_s
    if low is None and high is None:
        return None
    near = 0
    for row in rows:
        if str(row["decision"]) != "skipped_duration" or row["duration_s"] is None:
            continue
        seconds = float(row["duration_s"])
        if low is not None and 0 <= low - seconds <= NEAR_MISS_S:
            near += 1
        elif high is not None and 0 <= seconds - high <= NEAR_MISS_S:
            near += 1
    if not near:
        return None
    if low is not None and high is not None:
        edge = "length rule"
    else:
        edge = "floor" if low is not None else "ceiling"
    verb = "was" if near == 1 else "were"
    return (
        f"{near} of the last {len(rows)} passed over {verb} within "
        f"{NEAR_MISS_S} seconds of your {edge}."
    )


async def follow_detail(request: Request) -> Response:
    """`GET /dashboard/following/{slug}` — the rule, the checks, the cost.

    Three bands in one order, and the third is the point: what this follow
    passed over, with the sentence carrying the number that made each decision.
    The ledger probes one row past its limit rather than counting, and the two
    job lists are bounded independently of that limit.
    """
    db = request.app.state.assembled.db
    slug = str(request.path_params["slug"])
    row = await db.read(lambda c: follows_store.by_slug(c, slug))
    if row is None:
        return _render(
            "error.html",
            {
                **_chrome(request, "following"),
                "title": "No such follow",
                "error": {
                    "code": "E_UNKNOWN_FOLLOW",
                    "message": f'"{slug}" is not a follow on this instance.',
                    "next": "the Following page lists every channel this index watches.",
                },
                "back": {"href": f"{ROOT}/following", "label": "Following"},
            },
            status=404,
        )

    collection_id = int(row["collection_id"])
    params = request.query_params
    limit = clamp(params.get("limit"), 1, SEEN_PAGE_MAX, SEEN_PAGE)  # type: ignore[arg-type]
    offset = clamp(params.get("offset"), 0, OWNER_CLAMPS.offset_max, 0)  # type: ignore[arg-type]

    rules = follow_rules.Rules.from_row(row)
    name = str(row["title"] or row["slug"])
    seen = await db.read(
        lambda c: follows_store.seen_page(
            c, collection_id, decisions=PASSED_OVER, limit=limit, offset=offset
        )
    )
    has_more = len(seen) > limit
    seen = seen[:limit]
    checks = await db.read(
        lambda c: follows_store.recent_checks(c, collection_id, CHECK_CAP)
    )
    index_jobs = await db.read(
        lambda c: follows_store.index_jobs(c, collection_id, INDEX_JOB_CAP)
    )
    in_flight = await db.read(lambda c: follows_store.check_in_flight(c, collection_id))
    counts = await db.read(lambda c: follows_store.counts(c, collection_id))

    return _render(
        "follow.html",
        {
            **_chrome(request, "following"),
            "title": name,
            "follow": {
                "slug": str(row["slug"]),
                "name": name,
                "source_url": str(row["source_url"] or ""),
                "kind": str(row["kind"]),
                "state": str(row["state"]),
                "last_check": row["last_sync_at"],
                "next_check": row["next_check_at"],
                "last_new": row["last_new_at"],
                "error_code": row["last_error_code"],
                "error_message": row["last_error_message"],
            },
            # The rule as one sentence, from the module that owns the sentence.
            # There is no second renderer of it anywhere on this surface.
            "sentence": follow_rules.describe(rules, name=name),
            "brought_in": int(counts.get("queued", 0)),
            "checks": [
                {
                    "job_id": str(check["public_id"]),
                    "state": str(check["state"]),
                    "error_code": check["error_code"],
                    "created_at": check["created_at"],
                    "took": elapsed(check["started_at"], check["finished_at"]),
                }
                for check in checks
            ],
            "index_jobs": [
                {
                    "job_id": str(job["public_id"]),
                    "state": str(job["state"]),
                    "n_items": int(job["n_items"] or 0),
                    "n_done": int(job["n_done"] or 0),
                    "n_failed": int(job["n_failed"] or 0),
                    "created_at": job["created_at"],
                }
                for job in index_jobs
            ],
            "in_flight": str(in_flight["public_id"]) if in_flight is not None else None,
            "seen": [_seen_row(item) for item in seen],
            "near_miss": _near_miss(seen, rules),
            "form": _follow_form_values(row),
            "choices": _follow_choices(),
            "pagination": {"limit": limit, "offset": offset, "has_more": has_more},
        },
    )
