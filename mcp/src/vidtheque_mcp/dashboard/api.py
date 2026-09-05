"""`/dashboard/api/{overview,ledger,session}` — the first slice of the JSON the
React dashboard reads (`docs/design/frontend-migration.md`).

Three additive reads. They add no query, no clamp and no policy: `overview` and
`ledger` are `read_models`' assemblers — the same reads the Jinja pages make,
under the same projection — shaped into typed JSON, and `session` is what a
browser needs before it can decide whether to render a dashboard or a sign-in
link.

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
from .access import peer_trusted, sign_in_hint, write_side_enabled
from .read_models import (
    FAILED_WINDOW_S,
    LedgerReads,
    OverviewReads,
    declared_models,
    ledger_reads,
    overview_reads,
    pipeline_readiness,
    redacted,
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
