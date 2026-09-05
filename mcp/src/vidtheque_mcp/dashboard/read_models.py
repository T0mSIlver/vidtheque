"""The reads behind the dashboard's read pages — once, for two surfaces.

`views.py` renders them into Jinja and `api.py` returns them as JSON, and both
must be answering out of the *same* reads. Two copies of "what does this box
hold" is how the page and the API start disagreeing about the corpus, and the
projection rules (`redacted`) are the half where drift is a disclosure bug
rather than a cosmetic one: the demo drops the operator's disk, the declared
model ids and the drift reason by **not reading them**, and that decision has
to live in one place or it lives in neither.

What is here is exactly the shared half: the width set every frame URL on this
surface comes from, the projection predicate, the bounded worker probe, and the
assemblers — the overview, the ledger, the videos table and the video detail.
Nothing here formats a value for a human: display strings are `render.py`'s
(for the pages) and the browser's (for the JSON), which is the rule the
front-end migration settled — **typed values on the wire, formatting at the
edge**, and policy text (refusals, the clamp notes, the redaction itself) still
Python's.

Moved out of `views.py` unchanged: the overview and the ledger on 2026-09-05,
the videos table and the detail page the same day (dashboard.md §20). `views.py`
imports these back under their old private names, so the Jinja pages call the
same code, in the same order, under the same bounds they always did.
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import time
from dataclasses import dataclass, field, replace
from typing import Any

from starlette.requests import Request

from ..db import queries
from ..errors import ToolError
from ..jobs import store as jobs_store
from ..public.api import OWNER_CLAMPS, _cover_frames, thumb_url
from ..text import clamp, iso_day, iso_z, split_csv
from ..timeparse import parse_corpus_time
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

# The detail page's bounds (§5.3), and the JSON's: `?frames=100000` is clamped
# on both. The three that are not page sizes bound the *expensive* paths
# independently of `limit`, which is the invariant's other half — a video with
# nine thousand shots must not cost more than one with ninety.
FRAME_PAGE = 24
FRAME_PAGE_MAX = 96
CUE_PAGE = 50
CUE_PAGE_MAX = 200
SHOT_CAP = 2_000
VIDEO_HISTORY_CAP = 10
OCR_LINE_CAP = 600

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


# --------------------------------------------------------------------- videos

# The table's own vocabularies (§5.2), here rather than in `views.py` because
# the JSON has to advertise the same words the form offers: a client that
# renders a picker from one list while the server accepts another is exactly
# the drift this module exists to make impossible.
VIDEO_ORDERS = ("recency", "title", "duration", "indexed_at", "relevance")
HAS_VALUES = ("any", "transcript", "ocr", "frames", "all")

# The two ranges §5.2 lists, in the order they appear in the band: when the
# talk was published, and when this box indexed it. They are the corpus axis
# and the operations axis, and CLAUDE.md's invariant is that they are never
# overloaded — which is exactly why they are two controls and not one "date"
# filter with a mode.
DATE_FILTERS = (
    ("published_after", "published_before"),
    ("indexed_after", "indexed_before"),
)
DATE_PARAMS = tuple(name for pair in DATE_FILTERS for name in pair)

# A date is a position on a real timeline, so it gets real bounds. Both are
# *clamps*, not refusals, and the clamped value is echoed back into the form and
# into every link on the page — a filter the server quietly changed and did not
# show is the silent narrowing CLAUDE.md forbids.
#
# The floor is one second rather than zero: the column is unix seconds, and
# `iso_day` renders a falsy stamp as `—`, so a floor of 0 would not survive the
# round trip back into a date input. The ceiling is a year out, because nothing
# in a corpus was indexed after now and "next year" is already a generous
# reading of a clock skew.
DATE_FLOOR = 1
DATE_CEILING_S = 365 * 86_400
DAY_S = 86_400
# Long enough for every accepted spelling (`2026-08-09T12:00:00+00:00` is 25),
# short enough that the parser is never handed a kilobyte to think about.
DATE_MAX_CHARS = 32

# The fields the table asks `list-videos` for, and the title budget it asks
# them under. Both surfaces send the same request: a JSON row whose title was
# truncated at a different length than the page's is a second corpus.
LIST_FIELDS = "video_id,title,channel,published,duration,coverage,tags,indexed_at,index_state"
TITLE_CHARS = 200

_COVERAGE_LABELS = (
    ("t", "transcript"),
    ("o", "on-screen text"),
    ("f", "frame embeddings"),
)
_COVERAGE_KEYS = (("t", "transcript"), ("o", "ocr"), ("f", "frames"))


def coverage_pills(coverage: str) -> list[dict[str, Any]]:
    """The tool's own `t/o/f/-` string, as three labelled pills.

    §4.2: this is what the videos table shows instead of per-row counts, and it
    costs nothing extra — `_LIST_SQL` already computes the three booleans.
    """
    return [
        {"letter": letter, "label": label, "present": letter in coverage}
        for letter, label in _COVERAGE_LABELS
    ]


def coverage_flags(coverage: str) -> dict[str, bool]:
    """The same three booleans, keyed by leg, for a caller that draws its own.

    The letters are a *text* device — the `tsv` block a model reads — so they
    are the wrong thing to put on a wire that promises typed values. Both
    readings are built from the same letters, so the page and the payload
    cannot come to disagree about which one means on-screen text.
    """
    return {key: letter in coverage for letter, key in _COVERAGE_KEYS}


def video_facts(
    conn: sqlite3.Connection, public_ids: list[str]
) -> dict[str, dict[str, Any]]:
    """Per row: the cover keyframe, and the three columns `list-videos` rendered.

    One grouped query for the whole page, and deliberately the *same* one the
    table already ran. `public/api._cover_frames` is the cover half; this adds
    `published_at`, `duration_s` and `indexed_at` beside it rather than reading
    them in a second pass, because the tool's record carries those three as
    `iso_day`/`duration_clock` strings and a surface that promises typed values
    cannot use them (DECISIONS.md, 2026-09-05). Rendering is the edge's; the
    epoch has to survive the trip.

    The cover is a correlated `MIN(ord)` rather than a join, so a video with no
    keyframes keeps its row and loses only its thumbnail — the join form would
    drop the half-indexed video this table exists to show.
    """
    if not public_ids:
        return {}
    rows = conn.execute(
        """
        SELECT v.public_id    AS public_id,
               v.published_at AS published_at,
               v.duration_s   AS duration_s,
               v.indexed_at   AS indexed_at,
               (SELECT MIN(k.ord) FROM keyframes k
                 WHERE k.video_id = v.id AND k.dup_of IS NULL) AS cover_ord
        FROM videos v
        WHERE v.public_id IN (SELECT value FROM json_each(?))
        """,
        (json.dumps(public_ids),),
    ).fetchall()
    facts: dict[str, dict[str, Any]] = {}
    for row in rows:
        public_id = str(row["public_id"])
        ordinal = row["cover_ord"]
        facts[public_id] = {
            "published_at": row["published_at"],
            "duration_s": row["duration_s"],
            "indexed_at": row["indexed_at"],
            "cover": None if ordinal is None else f"{public_id}-{int(ordinal):05d}",
        }
    return facts


def date_filters(
    params: Any, now: int
) -> tuple[dict[str, int | None], dict[str, str], dict[str, Any] | None]:
    """Resolve the four date inputs to clamped epochs, plus what to echo back.

    Resolved **here** rather than passed through as strings, for one reason
    worth the extra call: `parse_corpus_time` accepts `30d`, `today` and a bare
    unix stamp as well as `2026-08-09`, and those are good things to be able to
    type into a URL and bad things to leave in a form field a browser renders
    as a date picker. So the entry point stays generous and the canonical form
    is the resolved UTC **day** — which is then what the picker shows, what the
    pager links carry, and what the query actually filtered on. The URL a
    visitor sends and the sentence the page prints are the same fact.

    Both ends are snapped to that day on purpose, and the two ends are snapped
    differently because the clause they feed is asymmetric
    (`db/queries.py:416-419`): `>= after` and `< before`. So `after` becomes the
    start of its day and `before` becomes the start of the *next* one, which is
    what makes `published_before=2026-08-09` include the ninth. The alternative
    — passing the instant through unrounded — is a control whose label says a
    day and whose filter means a moment, and a range that quietly drops
    everything published on its own end date reads as a bug because it is one.

    The third element is the tool's own typed refusal when a value will not
    parse, rendered rather than dropped: `timeparse` treats an unparseable
    filter as a hard error precisely because a silently ignored filter is a
    page reporting the wrong result set with total confidence.
    """
    resolved: dict[str, int | None] = {}
    echo: dict[str, str] = {}
    for name in DATE_PARAMS:
        raw = (params.get(name) or "").strip()[:DATE_MAX_CHARS]
        if not raw:
            resolved[name], echo[name] = None, ""
            continue
        try:
            value = int(parse_corpus_time(raw, name) or 0)
        except ToolError as error:
            return (
                resolved,
                echo,
                {"code": error.code, "message": error.message, "next": error.next_hint},
            )
        value = max(DATE_FLOOR, min(now + DATE_CEILING_S, value))
        day = value - value % DAY_S
        resolved[name] = day + (DAY_S if name.endswith("_before") else 0)
        echo[name] = iso_day(max(day, DATE_FLOOR))
    return resolved, echo, None


def _choice(raw: str | None, allowed: tuple[str, ...], default: str) -> str:
    return raw if raw in allowed else default


def _clamp_note(raw: str | None, value: int, name: str) -> str | None:
    """`limit=100000 → 100`, but only when a number in the URL actually moved.

    Only when it moved, and only when it was a number: the page has always
    clamped silently and re-printed the accepted value into its form, and a
    payload that announced a clamp on every request would be a line nobody
    reads by the second page.
    """
    if raw is None:
        return None
    try:
        asked = int(str(raw).strip())
    except ValueError:
        return None
    return None if asked == value else f"{name}={asked} → {value}"


@dataclass(frozen=True)
class VideosReads:
    """The videos table's whole read, before either surface shapes it.

    ``rows`` are `list-videos`' own records with three additions each: the
    cover thumbnail, the coverage pills the page draws, and ``typed`` — the
    epochs and the float the tool rendered to `2023-01-17` and `1:56:40` on its
    way out. ``error`` is a refusal (a date that will not parse, or the tool's
    own), in which case there are no rows and the caller answers with it.
    """

    filters: dict[str, Any]
    resolved: dict[str, int | None]
    order: str
    limit: int
    offset: int
    tags: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    error: dict[str, Any] | None = None
    rows: list[dict[str, Any]] = field(default_factory=list)
    pagination: dict[str, Any] = field(default_factory=dict)
    total: int | None = None


async def videos_reads(request: Request) -> VideosReads:
    """`GET /dashboard/videos`' reads, in the order and the bounds they have had.

    Seven reads whatever the page size: four inside `list-videos`, then the
    exact count of the filtered set (two), then one grouped row-facts query.
    Nothing here is per row — that is §6.3, and `test_no_page_issues_a_query_
    per_row` measures it as the shape "one row costs what a hundred do".
    """
    assembled = request.app.state.assembled
    deps: Deps = assembled.deps
    params = request.query_params

    q = (params.get("q") or "").strip() or None
    channel = (params.get("channel") or "").strip() or None
    tags = (params.get("tags") or "").strip() or None
    has = _choice(params.get("has"), HAS_VALUES, "any")
    # `all` is this page's default and it means all: a management table that
    # cannot see the failed and the half-indexed is the one view nobody needs.
    states = (*queries.INDEX_STATES, "all")
    index_state = _choice(params.get("index_state"), states, "all")
    order = params.get("order") if params.get("order") in VIDEO_ORDERS else None
    if order is None:
        order = "relevance" if q else "recency"
    limit = clamp(params.get("limit"), 1, OWNER_CLAMPS.videos_max_limit,  # type: ignore[arg-type]
                  OWNER_CLAMPS.videos_default_limit)
    offset = clamp(params.get("offset"), 0, OWNER_CLAMPS.offset_max, 0)  # type: ignore[arg-type]
    dates, date_echo, date_error = date_filters(params, int(time.time()))

    # Policy text, and Python's by the same rule as a refusal message: a value
    # the server did not honour has to say so, in words, on the payload that
    # answered with something else. The page echoes its clamps back into its
    # own form; a JSON caller has no form to read them out of.
    moved = [
        note
        for note in (
            _clamp_note(params.get("limit"), limit, "limit"),
            _clamp_note(params.get("offset"), offset, "offset"),
        )
        if note
    ]
    notes: list[str] = []
    if moved:
        notes.append(
            f"note: clamped server-side: {', '.join(moved)}. The bounds are this "
            "deployment's, not the URL's; page with offset instead of raising limit."
        )
    for name, raw, allowed, chosen in (
        ("has", params.get("has"), HAS_VALUES, has),
        ("index_state", params.get("index_state"), states, index_state),
        ("order", params.get("order"), VIDEO_ORDERS, order),
    ):
        if raw and raw not in allowed:
            notes.append(
                f"note: {name}={raw!r} is not one of {', '.join(allowed)}; "
                f"this listing used {name}={chosen}."
            )

    filters: dict[str, Any] = {
        "q": q or "",
        "channel": channel or "",
        "tags": tags or "",
        "has": has,
        "index_state": index_state,
        "order": order,
        "limit": limit,
        **date_echo,
    }
    # Every key the form and the link macros read, whatever went wrong: a
    # refused date must still render a band with the other seven controls in
    # it, so the reader can fix the one that broke instead of losing the query.
    for name in DATE_PARAMS:
        filters.setdefault(name, "")
    base = VideosReads(
        filters=filters,
        resolved=dict(dates),
        order=order,
        limit=limit,
        offset=offset,
        notes=notes,
    )
    if date_error is not None:
        return replace(base, error=date_error)

    result = await library.list_videos(
        deps,
        q=q,
        channel=channel,
        tags=tags,
        has=has,
        index_state=index_state,
        # Already resolved and clamped above, so the tool re-parses an integer
        # rather than a string — same parameters, same clauses, one parse.
        published_after=dates["published_after"],  # type: ignore[arg-type]
        published_before=dates["published_before"],  # type: ignore[arg-type]
        indexed_after=dates["indexed_after"],  # type: ignore[arg-type]
        indexed_before=dates["indexed_before"],  # type: ignore[arg-type]
        order=order,
        limit=limit,
        offset=offset,
        fields=LIST_FIELDS,
        max_text_chars=TITLE_CHARS,
    )
    error = tool_error(result)
    if error is not None:
        return replace(base, error=error)

    payload = result.structured_content or {}
    rows = [dict(v) for v in payload.get("videos", [])]

    # "50 shown of 473", with no tilde (Tom, 2026-08-13). The `~` was the tool's
    # and it was honest there: `list-videos` counts through a ceiling
    # (`COUNT_PROBE_FLOOR`) because an exact total is tokens an agent spends on
    # a number it will not page through. A reader with a pager under the table
    # is the other caller — the tilde is the one thing on the line they cannot
    # act on — so this counts the set itself, with the same filters, and the
    # tool's probe is untouched.
    #
    # Two reads for it, and they are the two the tool already made privately:
    # the corpus-axis filters collapse to a video-id pool, then one `COUNT(*)`
    # over the same CTE the rows came out of. Rebuilt here rather than returned
    # by the tool, because a tool that grows a parameter to please a page is how
    # the two surfaces stop sharing one query layer.
    filter_states = queries.INDEX_STATES if index_state == "all" else (index_state,)
    tag_list = split_csv(tags, 10, "tags")
    pool = await assembled.db.read(
        lambda c: queries.resolve_videos(
            c,
            queries.CorpusFilter(
                channel=channel,
                published_after=dates["published_after"],
                published_before=dates["published_before"],
                indexed_after=dates["indexed_after"],
                indexed_before=dates["indexed_before"],
                tags=tag_list,
                index_states=filter_states,
            ),
        )
    )
    total = await assembled.db.read(
        lambda c: queries.count_videos(c, pool, q, has, deps.settings.candidate_cap)
    )

    facts = await assembled.db.read(
        lambda c: video_facts(c, [r["video_id"] for r in rows])
    )
    for row in rows:
        fact = facts.get(row["video_id"], {})
        row["thumb"] = thumb(deps, fact.get("cover"), STRIP_WIDTH)
        row["coverage_pills"] = coverage_pills(str(row.get("coverage") or "---"))
        row["tag_list"] = [t for t in str(row.get("tags") or "").split(",") if t]
        # The columns the tool spent on prose, kept beside its record: the page
        # prints `published`, the payload sends `published_at`, and neither is
        # derived from the other.
        row["typed"] = {
            "published_at": fact.get("published_at"),
            "duration_s": fact.get("duration_s"),
            "indexed_at": fact.get("indexed_at"),
        }
    return replace(
        base,
        rows=rows,
        pagination=payload.get("pagination", {}),
        total=total,
        tags=tag_list,
    )


# --------------------------------------------------------------- video detail


def video_header(row: sqlite3.Row, tags: list[str]) -> dict[str, Any]:
    """The `videos` row a human wants — and none of the paths.

    `media_path`, `audio_path` and `jpeg_path` are operator detail that must not
    leak into a page that might be screenshotted, or into a payload a browser
    can read (§5.1). Presence, not location: the stage table already says
    whether a fetch succeeded. Every clock here is the stored epoch, and the
    surface that wants `2023-01-17` is the one that formats it.
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


def stage_rows(stages: list[sqlite3.Row], redact: bool = False) -> list[dict[str, Any]]:
    """All seven stages, with the ones that never ran said out loud.

    `job-status` collapses these into five *wire* stages for a model's benefit
    (`jobs/store.WIRE_STAGES`). A human wants the seven, and wants the absent
    ones present as `absent` rather than silently missing from the list.

    ``redact`` drops the two fields that are the operator's console rather than
    the corpus: `model_key`, which is a declared model id and therefore a
    setting by §2.4's own argument, and `error`, which is the pipeline's raw
    prose. The states, the versions and the clocks stay — they are what a
    reader can act on, and dropping them would leave an empty shell. The jobs
    view has redacted since phase 4; this page had not (2026-08-10 audit, F-4).
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
                "model_key": None if redact else row["model_key"],
                "stage_version": row["stage_version"],
                "started_at": row["started_at"],
                "finished_at": row["finished_at"],
                "error": None if redact else row["error"],
            }
        )
    return rows


def shot_rows(
    deps: Deps, video_id: str, shots: list[sqlite3.Row]
) -> list[dict[str, Any]]:
    """One shot, as facts: where it runs, what it holds, and its first frame.

    The percentages the band draws are not here — a percentage of a runtime is
    a rendering, and the runtime is already on the payload. What is here is the
    scrub preview's URL, and three things about that are decisions:

    * it is **`STRIP_WIDTH`**, not a fourth entry in the width set (§6.4). A
      new width is a new JPEG per keyframe in a cache that is capped in bytes,
      and 192x108 is the scale a scrub preview is read at anyway — YouTube's
      own storyboard tiles are 158x90. For a shot whose first frame is on the
      strip below, the preview is the *same* file the page already fetched.
    * it is emitted for every shot rather than fetched on demand, because the
      alternative is a request per hover against the process that also holds
      the only SQLite writer. Nothing is fetched until a pointer asks: the
      markup carries a URL, the browser carries the bytes.
    * it is derived from `first_ord` with no extra query — the frame id is
      `<public_id>-<ord:05d>` (`http/frames.py`), the same string
      :func:`frame_cards` builds.
    """
    rows = []
    for shot in shots:
        start = max(0.0, float(shot["start_s"] or 0.0))
        end = max(start, float(shot["end_s"] or start))
        first_ord = int(shot["first_ord"])
        rows.append(
            {
                "shot_id": int(shot["shot_id"]),
                "start_s": start,
                "end_s": end,
                "frames": int(shot["frames"]),
                "kept": int(shot["kept"]),
                "ocr_done": int(shot["ocr_done"]),
                "first_ord": first_ord,
                "preview": thumb(deps, f"{video_id}-{first_ord:05d}", STRIP_WIDTH),
            }
        )
    return rows


def frame_cards(
    deps: Deps,
    video_id: str,
    rows: list[sqlite3.Row],
    ocr_lines: dict[int, list[sqlite3.Row]],
) -> list[dict[str, Any]]:
    """One keyframe: its measurements, its three URLs, and the text read off it.

    Never inline base64 — CLAUDE.md's invariant, and a strip of forty base64
    JPEGs is the byte analogue of the token blowup it exists to prevent. The
    box coordinates are normalised 0–1 at write time (`pipeline/store.py`), so
    whoever draws them over the frame does not need to know its pixels.
    """
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
                "thumb": thumb(deps, frame_id, STRIP_WIDTH),
                "detail": thumb(deps, frame_id, DETAIL_WIDTH),
                "large": thumb(deps, frame_id, LIGHTBOX_WIDTH),
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


@dataclass(frozen=True)
class VideoDetailReads:
    """§5.3's panels, read once.

    ``summary_error`` is `video-summary`'s own refusal when it has one — a
    video that never finished the pipeline is exactly the video this page
    exists for, so the refusal travels *beside* the panels that still have
    something to say rather than becoming the page's error.

    ``cues`` is ``None`` when the caller did not ask for a cue page: the JSON
    surface points at `/dashboard/api/videos/{id}/cues` instead of re-serving a
    transcript, and a read nobody renders is a read not taken.
    """

    row: sqlite3.Row
    vid: int
    summary: dict[str, Any]
    summary_error: dict[str, Any] | None
    stages: list[dict[str, Any]]
    counts: sqlite3.Row
    origins: dict[str, int]
    shots: list[dict[str, Any]]
    shots_capped: bool
    frames: list[dict[str, Any]]
    frames_more: bool
    frame_page: int
    frame_offset: int
    ocr_lines_capped: bool
    cue_totals: dict[str, int]
    tags: list[str]
    history: list[dict[str, Any]]
    cues: list[sqlite3.Row] | None = None
    cues_more: bool = False
    cue_page: int = 0
    cue_offset: int = 0
    chunks: list[sqlite3.Row] = field(default_factory=list)


async def video_detail_reads(
    request: Request,
    video_id: str,
    *,
    frame_page: int,
    frame_offset: int,
    cue_page: int | None = None,
    cue_offset: int = 0,
    redact: bool = False,
) -> VideoDetailReads | None:
    """Everything `GET /dashboard/videos/{id}` reads. ``None`` for an unknown id.

    The order is the page's, unchanged, and so is the count: every read here is
    scoped to one video and bounded by a cap that is not `limit` — `SHOT_CAP`
    on the timeline, `OCR_LINE_CAP` on the text, `VIDEO_HISTORY_CAP` on the
    runs — which is the discipline that lets one frame and ninety-six cost the
    same number of queries.
    """
    assembled = request.app.state.assembled
    deps: Deps = assembled.deps
    db = assembled.db

    row = await db.read(lambda c: queries.lookup_video(c, video_id))
    if row is None:
        return None
    vid = int(row["id"])

    # `video-summary` refuses a video that never finished the pipeline — which
    # is exactly the video this page exists for. So the refusal is carried
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
    summary_error = tool_error(summary)

    stages = await db.read(lambda c: queries.video_stages(c, vid))
    counts = await db.read(lambda c: queries.per_video_counts(c, vid))
    origins = await db.read(lambda c: queries.cue_origins(c, vid))
    shots = await db.read(lambda c: queries.shot_timeline(c, vid, SHOT_CAP))
    frame_rows = await db.read(
        lambda c: queries.keyframe_page(c, vid, frame_offset, frame_page)
    )
    cue_rows: list[sqlite3.Row] | None = None
    if cue_page is not None:
        cue_rows = await db.read(
            lambda c: queries.cue_page(c, vid, cue_offset, cue_page)
        )
    # The transcript header is totals, not a position (Tom, 2026-08-10, round
    # 4). Read beside the counts because it answers the same question — how
    # much of this video is there — and never on a listing page.
    cue_totals = await db.read(lambda c: queries.cue_text_totals(c, vid))
    tag_map = await db.read(lambda c: queries.video_tags(c, [vid]))
    history_rows = await db.read(
        lambda c: jobs_store.recent_jobs_for_video(c, vid, VIDEO_HISTORY_CAP)
    )

    frames_more = len(frame_rows) > frame_page
    frame_rows = frame_rows[:frame_page]
    cues_more = False
    if cue_rows is not None and cue_page is not None:
        cues_more = len(cue_rows) > cue_page
        cue_rows = cue_rows[:cue_page]

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

    return VideoDetailReads(
        row=row,
        vid=vid,
        summary=summary_payload,
        summary_error=summary_error,
        stages=stage_rows(stages, redact),
        counts=counts,
        origins=origins,
        shots=shot_rows(deps, video_id, shots),
        shots_capped=len(shots) >= SHOT_CAP,
        frames=frame_cards(deps, video_id, frame_rows, ocr_lines),
        frames_more=frames_more,
        frame_page=frame_page,
        frame_offset=frame_offset,
        # The honest half of the double cap: when the *page's* line budget is
        # spent the per-frame counts under-report by definition, so the surface
        # says so rather than showing a short list as if it were the whole one.
        ocr_lines_capped=sum(len(v) for v in ocr_lines.values()) >= OCR_LINE_CAP,
        cue_totals=cue_totals,
        tags=tag_map.get(vid, []),
        history=[
            {
                "job_id": str(job["public_id"]),
                "state": str(job["state"]),
                "kind": str(job["kind"]),
                "created_at": job["created_at"],
                "finished_at": job["finished_at"],
                "error_code": job["error_code"],
                "degraded_stages": (
                    str(job["degraded_stages"]).split(",")
                    if job["degraded_stages"]
                    else []
                ),
            }
            for job in history_rows
        ],
        cues=cue_rows,
        cues_more=cues_more,
        cue_page=cue_page or 0,
        cue_offset=cue_offset,
        chunks=chunks,
    )
