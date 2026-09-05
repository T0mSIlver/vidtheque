"""The reads behind the corpus overview and the ledger — once, for two surfaces.

`views.py` renders them into Jinja and `api.py` returns them as JSON, and both
must be answering out of the *same* reads. Two copies of "what does this box
hold" is how the page and the API start disagreeing about the corpus, and the
projection rules (`redacted`) are the half where drift is a disclosure bug
rather than a cosmetic one: the demo drops the operator's disk, the declared
model ids and the drift reason by **not reading them**, and that decision has
to live in one place or it lives in neither.

What is here is exactly the shared half: the width set every frame URL on this
surface comes from, the projection predicate, the bounded worker probe, and the
two assemblers. Nothing here formats a value for a human — display strings are
`render.py`'s (for the pages) and the browser's (for the JSON), which is the
rule the front-end migration settled: **typed values on the wire, formatting at
the edge**, and policy text — refusals, the redaction itself — still Python's.

Moved out of `views.py` unchanged (2026-09-05). `views.py` imports these back
under their old private names, so the Jinja pages call the same code they
always did.
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import time
from dataclasses import dataclass
from typing import Any

from starlette.requests import Request

from ..db import queries
from ..jobs import store as jobs_store
from ..public.api import _cover_frames, thumb_url
from ..text import iso_z
from ..tools import library
from ..tools.base import Deps

# The fixed width set (dashboard.md §6.4). Three variants per frame in the
# `derived/` cache, not one per browser window — and never inline base64, which
# is the byte analogue of the token blowup that invariant exists to prevent.
STRIP_WIDTH = 192
DETAIL_WIDTH = 512
LIGHTBOX_WIDTH = 1280
FRAME_QUALITY = 70

# The overview's list bounds (§5.1). Server-side, like every other list here.
CHANNEL_CAP = 12
TAG_CAP = 24
RECENT_CAP = 8

# How far back "recently failed" reaches (§5.1). A day, because the question the
# line answers is "what failed while I was asleep" — a corpus that had a bad
# week six months ago must not light this up forever. The page prints the window
# from this constant and the JSON sends it, so the number and the words it sits
# in can never disagree.
FAILED_WINDOW_S = 86_400

# A health panel must never become the slowest dependency of the page that
# reports it. `/status` is deliberately lock-free on the worker; this is the
# corresponding client-side wall-clock bound. The response cap is defensive —
# the shipped worker returns a few kilobytes — and stops a mispointed URL from
# turning an overview request into an unbounded JSON parse.
WORKER_STATUS_TIMEOUT_S = 1.0
WORKER_STATUS_MAX_BYTES = 64 * 1024
WORKER_BACKEND_CAP = 12


def thumb(deps: Deps, frame_id: str | None, width: int) -> str | None:
    """Every frame on a dashboard surface, as a **relative** `/frames/…` path.

    The one place this surface differs from the MCP one on URLs, and the
    difference is which of the two has a page around it. An agent gets an
    absolute, self-contained, authenticated URL because nothing on its side
    resolves a path; a browser reading this dashboard already knows the host it
    fetched from, and it is more likely to be right than ``PUBLIC_URL`` is — a
    preview on a tunnelled port rendered every thumbnail against a dead origin
    (dashboard.md §8, phase 2). The signature is unaffected: it covers the
    frame, the width, the quality and the expiry, never the origin.
    """
    return thumb_url(deps, frame_id, width, absolute=False)


def redacted(request: Request) -> bool:
    """Is this the demo's read-only projection rather than the owner's page?

    The same flag that has always decided which half of vidtheque you get, and
    the whole of §2.4's right-hand column. It is deliberately a property of the
    **deployment**, not of the reader: a read-only instance that also has a
    credential configured still serves the projection, because the projection
    is what that mode *is*.

    Two rules follow it:

    * the **jobs** view keeps its states, its codes, its counts and **all of its
      clocks** — showing a visitor what indexing a video costs in time is the
      view's stated purpose (§10.4) — and drops source URLs, because
      `jobs.args_json` carries whatever was submitted, and error text, because
      yt-dlp's failure strings carry cookiefile paths, player clients and the
      operator's politeness settings;
    * the **corpus overview** and the **ledger** keep the corpus — counts,
      channels, tags, coverage, arrivals — and drop the operator's box: the
      declared model ids and their dimensions, the drift reason, the byte
      totals, and the `VIDTHEQUE_AUTH` line.

    The videos table and the video detail page are **not** redacted: §2.4 gives
    them to the demo whole, and everything on them is corpus, not deployment.
    """
    return bool(request.app.state.assembled.public.enabled)


def tool_error(result: Any) -> dict[str, Any] | None:
    """A `CallToolResult`'s refusal, in the shape both surfaces render."""
    if not result.is_error:
        return None
    payload = dict(result.structured_content or {})
    payload.setdefault("code", "E_INTERNAL")
    payload.setdefault("message", "the query layer refused this request.")
    payload.setdefault("next", None)
    return payload


def file_size(path: Any) -> int:
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


def declared_models(config: dict[str, str]) -> list[dict[str, str]]:
    """What the corpus says it was built with (§4.1 caveat 2).

    `config` is written by migrations and read once at boot. It is the
    *declared* model, never the worker's reported one — the live answer is the
    vector state beside it, which is what `note_worker_drift` disables on a
    mismatch. Showing the pair is the point, and the projection shows neither.
    """
    rows = []
    for key, label in _MODEL_KEYS:
        value = config.get(key)
        if not value:
            continue
        dim = config.get(key.replace(".model", ".dim"))
        rows.append({"label": label, "key": key, "value": value, "dim": dim or ""})
    return rows


def _stamped(readiness: dict[str, Any]) -> dict[str, Any]:
    """One reading of the clock, in the two shapes this observation is read in.

    The page prints `checked_at` into a `<time datetime=…>` attribute, which
    wants ISO-8601 UTC. The JSON sends `checked_at_s`, because React owns date
    formatting (DECISIONS.md, 2026-09-05) and an API that ships an already
    rendered day is the one field where that split silently stops holding. Both
    come off the same `time.time()` call here rather than from two, so the page
    and the payload can never name different seconds.
    """
    now = time.time()
    readiness["checked_at"] = iso_z(now)
    readiness["checked_at_s"] = int(now)
    return readiness


async def pipeline_readiness(request: Request, *, redact: bool) -> dict[str, Any]:
    """One bounded current-state observation of the local pipeline boundary.

    The projection does not make the worker request at all: worker reachability
    and checkpoint ids are operator infrastructure, while MCP/database
    readiness and the vector-search *effect* are already observable through
    the page and its search results. There is no cache and no history; the
    timestamp is the clock of this observation, stamped by :func:`_stamped` in
    both of the representations this observation has readers for.
    """
    assembled = request.app.state.assembled
    readiness: dict[str, Any] = {
        "mcp": "ready",
        "database": "ready",
        "vectors": {
            "enabled": assembled.db.vectors.enabled,
            "reason": None if redact else assembled.db.vectors.reason,
        },
        "worker": None,
        "checked_at": None,
        "checked_at_s": None,
    }
    if redact:
        return _stamped(readiness)

    worker_url = assembled.settings.worker_url.rstrip("/")
    http = assembled.worker_status_http
    if not worker_url or http is None:
        readiness["worker"] = {
            "state": "unconfigured",
            "detail": "No worker URL is configured.",
            "models": [],
        }
        return _stamped(readiness)

    worker: dict[str, Any] = {
        "state": "unavailable",
        "detail": "The worker did not answer its status check.",
        "models": [],
    }
    try:
        body: Any = None
        parsed = False
        too_large = False
        # `asyncio.timeout` is the wall-clock bound §15 promises. httpx's
        # `timeout=` alone is per-operation and its read leg resets on every
        # chunk, so a peer trickling one byte per 900 ms would stay under it
        # for as long as it cared to — with the overview awaiting the whole
        # time.
        async with asyncio.timeout(WORKER_STATUS_TIMEOUT_S):
            async with http.stream(
                "GET", f"{worker_url}/status", timeout=WORKER_STATUS_TIMEOUT_S
            ) as response:
                if response.status_code >= 400:
                    worker["detail"] = f"The worker answered HTTP {response.status_code}."
                else:
                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(content) + len(chunk) > WORKER_STATUS_MAX_BYTES:
                            too_large = True
                            break
                        content.extend(chunk)
                    if too_large:
                        worker["detail"] = "The worker status response exceeded 64 kB."
                    else:
                        body = json.loads(content)
                        parsed = True
        if parsed:
            backends = body.get("backends") if isinstance(body, dict) else None
            if not isinstance(backends, list):
                worker["detail"] = "The worker returned an invalid status response."
            else:
                models = []
                for backend in backends[:WORKER_BACKEND_CAP]:
                    if not isinstance(backend, dict) or not backend.get("model"):
                        continue
                    models.append(
                        {
                            "task": str(backend.get("task") or "unknown"),
                            "model": str(backend["model"]),
                            "loaded": bool(backend.get("loaded")),
                        }
                    )
                worker = {
                    "state": "ready",
                    "detail": "Reachable over HTTP.",
                    "models": models,
                }
    except Exception:
        # Transport, timeout, status JSON and protocol errors are all the same
        # current fact to the operator. Exception text can contain the worker
        # hostname and is not useful enough to put into HTML.
        pass
    readiness["worker"] = worker
    return _stamped(readiness)


# ------------------------------------------------------------------- overview


@dataclass(frozen=True)
class OverviewReads:
    """Everything the corpus overview is made of, before anyone renders it.

    ``error`` is `corpus-summary`'s own refusal when it has one; every other
    field is unset in that case and the caller answers with the refusal.
    ``storage`` is ``None`` in the projection because the reads behind it were
    never taken, not because a template declined to print them.
    """

    error: dict[str, Any] | None = None
    corpus: dict[str, Any] | None = None
    rollup: sqlite3.Row | None = None
    recent: list[dict[str, Any]] | None = None
    health: dict[str, int] | None = None
    storage: dict[str, int] | None = None
    readiness: dict[str, Any] | None = None


async def overview_reads(
    request: Request, readiness_task: asyncio.Task[dict[str, Any]], *, redact: bool
) -> OverviewReads:
    """The overview's reads, in the order and the bounds they have always had."""
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
    error = tool_error(summary)
    if error is not None:  # pragma: no cover - corpus_summary has no error path
        # The readiness task is the caller's to cancel: a refusal does not wait
        # a second for a health probe nobody will render.
        return OverviewReads(error=error)
    payload = summary.structured_content or {}

    # `corpus_summary` builds its rollup for the lines it prints; this reads it
    # again for the three fields the payload does not carry (OCR lines, the
    # published span, the last-indexed clock). One flat statement, twice, is
    # the price of not re-deriving `data_status` here — §4.5 is not negotiable.
    rollup = await db.read(queries.corpus_rollup)
    pool = await db.read(lambda c: queries.resolve_videos(c, queries.CorpusFilter()))
    recent = await db.read(lambda c: queries.recent_indexed(c, pool, RECENT_CAP))
    # The queue, in one row (§5.1). Read in both modes: what the machine is
    # doing is corpus-shaped, not operator-shaped, and the jobs view the
    # numbers link into is already part of the demo projection (§10.4).
    health = await db.read(
        lambda c: jobs_store.job_health(c, int(time.time()) - FAILED_WINDOW_S)
    )
    # The one read the projection skips rather than redacts: a byte total of
    # the operator's disk is not a fact about the corpus, and not asking for it
    # is cheaper and more honest than asking and then not printing it.
    storage = (
        None
        if redact
        else {
            "keyframes": await db.read(queries.keyframe_bytes_total),
            # os.stat, not a directory walk: the file knows its own size and
            # the keyframe bytes are a column (§5.1).
            "database": file_size(assembled.settings.db_path),
        }
    )

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
            "thumb": thumb(deps, covers.get(str(row["public_id"])), STRIP_WIDTH),
        }
        for row in recent
    ]
    return OverviewReads(
        corpus=payload,
        rollup=rollup,
        recent=recent_rows,
        health=health,
        storage=storage,
        readiness=await readiness_task,
    )


# --------------------------------------------------------------------- ledger


@dataclass(frozen=True)
class LedgerReads:
    """Every key number this instance can count, in one bounded pass (§17).

    Every figure is a whole-table or index count — nothing here walks a video,
    a job or a keyframe, which is what makes the read count a constant.
    """

    rollup: sqlite3.Row
    ledger: sqlite3.Row
    gaps: dict[str, Any]
    backlog: dict[str, int]
    jobs_by_state: dict[str, int]
    health: dict[str, int]
    storage: dict[str, int] | None
    readiness: dict[str, Any]


async def ledger_reads(
    request: Request, readiness_task: asyncio.Task[dict[str, Any]], *, redact: bool
) -> LedgerReads:
    assembled = request.app.state.assembled
    db = assembled.db

    rollup = await db.read(queries.corpus_rollup)
    ledger = await db.read(queries.corpus_ledger)
    # `gaps` for one of its five numbers, and that is deliberate: the
    # "transcript but no on-screen text" set is the one figure here that is a
    # judgement about coverage rather than a column, and a second copy of that
    # SQL is how the overview and this page start disagreeing about what a gap
    # is. The other four terms it computes are cheap counts this page reads
    # more precisely elsewhere — and its `failed` rows carry `video_stages.error`,
    # which is the pipeline's prose about the operator's box and reaches
    # neither surface from here.
    gaps = await db.read(queries.gaps)
    backlog = await db.read(queries.embed_backlog)
    jobs_by_state = await db.read(jobs_store.job_state_counts)
    health = await db.read(
        lambda c: jobs_store.job_health(c, int(time.time()) - FAILED_WINDOW_S)
    )
    # The same read the overview skips rather than redacts, for the same reason:
    # a byte total of the operator's disk is not a fact about the corpus, and
    # not asking is cheaper and more honest than asking and not printing (§2.4).
    storage = (
        None
        if redact
        else {
            "keyframes": await db.read(queries.keyframe_bytes_total),
            "database": file_size(assembled.settings.db_path),
        }
    )
    return LedgerReads(
        rollup=rollup,
        ledger=ledger,
        gaps=gaps,
        backlog=backlog,
        jobs_by_state=jobs_by_state,
        health=health,
        storage=storage,
        readiness=await readiness_task,
    )
