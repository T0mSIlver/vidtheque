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

Those bounds are a :class:`ClampPolicy` rather than module constants because
there are two answers to "how much may you ask for" (dashboard.md §2.5.1):
anonymous traffic gets the demo's, the owner gets a wider one. One set of
handlers, two policies, still no second query layer.

**The policy follows the credential, not the route group** (phase 5; the fix
`docs/deploy-public.md` opened as a policy question and phase 4 declined to
make). Phase 1 chose it by prefix — `/api/*` public, `/dashboard/api/*` owner —
which is correct exactly when the prefix implies the caller, and in the
intended public deployment (``VIDTHEQUE_PUBLIC_READONLY=1`` +
``VIDTHEQUE_AUTH=none``) it does not: nothing stands in front of
`/dashboard/api/*` there, so an anonymous visitor was handed the owner's
bounds, including ``search_text_chars=None`` — the full-transcript hatch this
module's second rule reserves for an owner's agent. At 120 requests a minute
that is not a wider page, it is the corpus.

So :func:`policy_for` asks ``auth.credential.is_owner``: a bearer, a login
session or a trusted peer gets :data:`OWNER_CLAMPS`; everyone else gets
:data:`PUBLIC_CLAMPS`, on **both** prefixes, in **every** mode. The prefix
still decides what is *registered* (`ask=False` on the dashboard, and
`/api/*` only in public mode); it no longer decides what a caller may take.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from .. import __version__
from ..auth.credential import is_owner
from ..db import queries
from ..errors import HTTP_STATUS
from ..text import clamp, clamp_text_chars, clock
from ..tools import library, search
from ..tools.base import Deps
from . import humanize
from .settings import REPO_URL, PublicSettings

# 160×90 CSS pixels at 2x DPR. **One width, every hit** (§5, amended
# 2026-08-11): there used to be two — 192 for a transcript or OCR hit and 320
# for a frame hit, because the frame is the evidence and deserved the room. It
# does, and it still gets it; what it may not do is make a Sources list where a
# `frame` citation and a `transcript+ocr` citation beside it are different
# sizes (Tom, 2026-08-11). A result row is a result row, so the frame's box is
# now every kind's box and the second width is gone.
THUMB_WIDTH = 320
# The click-to-enlarge view (§6.4). Wide enough to read a slide, well inside the
# route's 64..1280 clamp and the `derived/` byte cap, and fetched only when a
# visitor actually opens one — so it costs nothing on a results page.
LIGHTBOX_WIDTH = 960
THUMB_QUALITY = 70

CONTENT_TYPES = ("all", "transcript", "ocr", "frame")


# --------------------------------------------------------------------- policy


@dataclass(frozen=True)
class ClampPolicy:
    """How much one route group may ask the service layer for.

    Every number here is a *second* cap under the tool's own: the tool clamps
    `limit` to 1..50 whatever this says, so a policy can only ever be tighter,
    never a way around the service layer. That asymmetry is the point — it is
    why two surfaces can share one set of handlers without either of them
    becoming the loophole in the other's discipline.

    ``search_text_chars=None`` means "pass the caller's ``max_text_chars``
    through, clamped by the tool" — including the documented ``0`` opt-out.
    An integer means the surface forces that width and the opt-out does not
    exist, which is demo-site.md §2's rule for anonymous traffic.
    """

    name: str
    search_max_limit: int
    search_default_limit: int
    search_text_chars: int | None
    videos_max_limit: int
    videos_default_limit: int
    offset_max: int


# demo-site.md §2.1/§2.2, unchanged: these are the numbers the demo ships and
# `test_public.py` asserts.
PUBLIC_CLAMPS = ClampPolicy(
    name="public",
    search_max_limit=20,
    search_default_limit=10,
    search_text_chars=400,
    videos_max_limit=50,
    videos_default_limit=24,
    offset_max=1_000,
)

# dashboard.md §5.2: wider, still server-side. `?limit=100000` is clamped, not
# honoured — the owner gets a bigger page, not an unbounded one. The search
# leg's ceilings are the tool's own (50 items, and `max_text_chars` honoured
# so a full transcript is one request away), because the owner *is* the
# "owner's agent" the escape hatch was written for.
OWNER_CLAMPS = ClampPolicy(
    name="owner",
    search_max_limit=50,
    search_default_limit=20,
    search_text_chars=None,
    videos_max_limit=100,
    videos_default_limit=50,
    offset_max=10_000,
)


async def policy_for(request: Request) -> ClampPolicy:
    """Which bounds this **caller** gets — see the module docstring.

    One predicate, both prefixes, every mode. The three consequences worth
    stating out loud, because each of them is a deliberate answer:

    * **``VIDTHEQUE_AUTH=none`` has no credential**, so every request in it is
      anonymous and gets :data:`PUBLIC_CLAMPS` — which is the point, since that
      is the mode the public demo ships in. The owner of a private
      ``AUTH=none`` box gets their bounds back one of two ways: through
      ``VIDTHEQUE_DASHBOARD_TRUSTED_CIDRS`` (the LAN answer, and see
      ``is_owner`` for why that counts), or through `/mcp` and the tools
      themselves, which are unmasked in that mode and were always the owner's
      agent's real surface — the JSON facade is a convenience over them, never
      the only way in.
    * **A read-only deployment that does have a credential no longer clamps its
      own owner**, which is the flaw in the mode-keyed version
      `docs/deploy-public.md` sketched and rejected.
    * **A bearer widens `/api/*` too.** The demo's own bounds are what an
      anonymous browser gets; a caller who can prove they are the owner is the
      caller the hatch was written for, whichever path they knocked on.
    """
    return OWNER_CLAMPS if await is_owner(request) else PUBLIC_CLAMPS


# --------------------------------------------------------------------- shared


def thumb_url(
    deps: Deps, frame_id: str | None, width: int = THUMB_WIDTH, *, absolute: bool = True
) -> str | None:
    """A `/frames/…` URL for a keyframe, signed only when signing means anything.

    In ``AUTH=none`` — the intended public deployment — ``frame_signer`` is
    ``None`` and the route is open, so an unsigned URL is the honest one:
    signing a link to a file the server hands to anyone who asks buys nothing.
    What guards the keyframe directory in public mode is the rate limiter
    (demo-site.md §5). With ``token``/``oauth`` the signer exists and this uses
    the same ``FrameUrlSigner.url()`` ``get-frames`` does — one signing scheme,
    two callers.

    ``absolute=False`` returns the same URL as a root-relative path, for a page
    that is already being read from this server and therefore knows its own
    host better than ``PUBLIC_URL`` does. That is not cosmetic: a dashboard
    previewed through an SSH tunnel on another port rendered every thumbnail
    against a ``PUBLIC_URL`` that resolved to nothing (2026-08-09), and a
    reverse proxy or a port map does the same. **Absolute stays the default and
    stays the MCP contract** — an agent gets a URL with no page around it to
    resolve against (dashboard.md §8, phase 2).

    The split lives here rather than in the signer because the signature covers
    ``frame_id``, ``width``, ``quality`` and the expiry and *not* the origin
    (``auth/tokens.py:_mac``), so a relative URL verifies exactly as an absolute
    one does. `/frames/*` also accepts the session cookie and a bearer, which is
    what a same-origin page carries anyway.
    """
    if not frame_id:
        return None
    base = deps.settings.public_url.rstrip("/") if absolute else ""
    signer = deps.frame_signer
    if signer is None:
        return f"{base}/frames/{frame_id}.jpg?w={width}&q={THUMB_QUALITY}"
    url, _expires = signer.url(base, frame_id, width, THUMB_QUALITY)
    return url


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
    """The fields the facade adds, all of them from data already returned."""
    row = dict(hit)
    row["timestamp"] = clock(hit.get("start"))
    row["thumb"] = thumb_url(deps, hit.get("frame_id"), THUMB_WIDTH)
    # The enlarged frame. A second *URL*, not a second query — and under
    # `token`/`oauth` it has to be signed here, because the page cannot sign a
    # width of its own (which is the point: the clamp is the server's).
    row["thumb_large"] = thumb_url(deps, hit.get("frame_id"), LIGHTBOX_WIDTH)
    row["text"] = humanize.snippet(hit.get("text"), hit.get("source"))
    return row


# ------------------------------------------------------------------ endpoints


async def search_payload(
    request: Request, *, demo: bool = False, verbatim_notes: bool = False
) -> tuple[dict[str, Any], int]:
    """Run the shared search leg once and shape it for a browser surface.

    Both JSON route groups and the dashboard search page enter here. The page
    therefore cannot grow its own interpretation of a query, a clamp or a leg:
    all three are still :func:`tools.search.run`'s, behind the caller-keyed
    policy this module already owns.

    ``verbatim_notes`` is the dashboard inspection view's one presentation
    difference. It keeps the tool's exact ``note:`` lines; the public JSON
    facade continues to remove that agent-facing prefix for its reader.
    """
    deps: Deps = request.app.state.assembled.deps
    policy = await policy_for(request)
    params = request.query_params
    content_type = params.get("content_type") or "all"
    if content_type not in CONTENT_TYPES:
        return (
            {
                "error": "E_BAD_PARAM",
                "message": f"content_type must be one of {', '.join(CONTENT_TYPES)}.",
                "next": "omit it for all three channels.",
            },
            400,
        )

    limit = _int_param(
        request, "limit", 1, policy.search_max_limit, policy.search_default_limit
    )
    offset = _int_param(request, "offset", 0, policy.offset_max, 0)
    if policy.search_text_chars is None:
        # The tool's own clamp, `0` opt-out included (text.clamp_text_chars).
        text_chars = clamp_text_chars(
            params.get("max_text_chars"), 120, 20_000, 1_000  # type: ignore[arg-type]
        )
    else:
        text_chars = policy.search_text_chars
    result = await search.run(
        deps,
        q=params.get("q"),
        content_type=content_type,
        limit=limit,
        offset=offset,
        channel=params.get("channel"),
        video_id=params.get("video_id"),
        max_text_chars=text_chars,
    )
    if result.is_error:
        payload = result.structured_content or {}
        code = str(payload.get("code") or "E_INTERNAL")
        return (
            {
                "error": code,
                "message": payload.get("message") or "search failed",
                "next": payload.get("next"),
            },
            HTTP_STATUS.get(code, 500),
        )

    payload = result.structured_content or {}
    return (
        {
            "query": params.get("q") or "",
            "content_type": content_type,
            "results": [_decorate_hit(deps, hit) for hit in payload.get("results", [])],
            "pagination": payload.get("pagination", {}),
            "leg_counts": payload.get("leg_counts", {}),
            # The `note:` prefix marks a line as machinery for a model reading
            # the text block. The page renders notes in their own muted line,
            # which says the same thing without the prefix. On the demo, a note
            # whose whole audience is a model is dropped as well (§6.1).
            "notes": (
                list(payload.get("notes") or [])
                if verbatim_notes
                else humanize.notes(payload.get("notes"), demo=demo)
            ),
            # Only the tool's empty path sets this, and it is the difference
            # between "nothing matched" and "nothing is indexed" — which a
            # `?q=` link to a fresh instance would otherwise report as a bad
            # query. Absent on a page of hits, where it would say nothing.
            "data_status": payload.get("data_status"),
        },
        200,
    )


async def search_endpoint(request: Request, *, demo: bool = False) -> JSONResponse:
    payload, status = await search_payload(request, demo=demo)
    return JSONResponse(payload, status_code=status)


async def videos_endpoint(request: Request) -> JSONResponse:
    deps: Deps = request.app.state.assembled.deps
    policy = await policy_for(request)
    params = request.query_params
    limit = _int_param(
        request, "limit", 1, policy.videos_max_limit, policy.videos_default_limit
    )
    offset = _int_param(request, "offset", 0, policy.offset_max, 0)
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


async def video_endpoint(request: Request) -> JSONResponse:
    """One video, straight from `video-summary` (demo-site.md §2.2.1).

    Same posture as the listing: the tool's structured payload verbatim plus
    the URLs only the facade can mint — a cover `thumb`, and `thumb` /
    `thumb_large` on every on-screen-text highlight, since a page cannot build
    a frame URL of its own (§5). The `next:` guidance is left out: it is
    written for an MCP client and names tools a browser cannot call.
    """
    deps: Deps = request.app.state.assembled.deps
    video_id = request.path_params["video_id"]
    result = await library.video_summary(
        deps,
        video_id,
        include_speakers=False,
        include_links=False,
        include_guidance=False,
        # Public bounds, fixed: the page shows what fits, not what a caller asks
        # for. The owner's agent has the tool itself for the wider cut.
        max_chapters=50,
        max_key_texts=12,
        max_ocr_highlights=12,
        max_chars=400,
    )
    if result.is_error:
        return _error_response(result.structured_content, "video lookup failed")

    payload = dict(result.structured_content or {})
    covers = await deps.db.read(lambda c: _cover_frames(c, [video_id]))
    payload["thumb"] = thumb_url(deps, covers.get(video_id))
    # The humanising layer (§2.4): the tool's truncation marker tells an agent
    # to pass `max_text_chars=0`, which a page cannot; a reader gets an ellipsis.
    payload["key_texts"] = [
        {**cue, "text": humanize.snippet(cue.get("text"))}
        for cue in payload.get("key_texts", [])
    ]
    payload["ocr_highlights"] = [
        {
            **frame,
            "screen_text": humanize.snippet(frame.get("screen_text")),
            "thumb": thumb_url(deps, frame.get("frame_id"), THUMB_WIDTH),
            "thumb_large": thumb_url(deps, frame.get("frame_id"), LIGHTBOX_WIDTH),
        }
        for frame in payload.get("ocr_highlights", [])
    ]
    return JSONResponse(payload)


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
    policy = await policy_for(request)
    deps: Deps = assembled.deps
    public: PublicSettings = request.app.state.public_settings
    rollup = await deps.db.read(queries.corpus_rollup)
    # Where the browsable corpus is, when there is one (dashboard.md §2.4). The
    # welcome page's one link out is unhidden by this and by nothing else: with
    # `VIDTHEQUE_DASHBOARD=0`, or with the edge rule that 404s `^/dashboard`
    # (`deploy/cloudflared.example.yml`), the route group is not there and a
    # link to it would be an invitation to a 404. The server knows; the page
    # asks rather than assuming.
    #
    # Imported here rather than at module scope because the dependency runs the
    # other way: `dashboard/` imports this module for its clamps and its
    # handlers, so a top-level edge back would be a cycle. By the time a request
    # reaches this line the package is long since loaded — `app.py` imports it
    # at boot whatever the mode — and one source of truth for the path beats a
    # second copy of the literal.
    from ..dashboard.settings import ROOT

    dashboard = request.app.state.dashboard_settings
    return JSONResponse(
        {
            "name": "vidtheque",
            "version": __version__,
            "browse": ROOT if dashboard.enabled else None,
            # The same string config.resource_url builds, so the page's copy
            # button and the OAuth `resource` can never disagree.
            "mcp_url": deps.settings.resource_url,
            "auth": deps.settings.auth_mode,
            "ask_enabled": public.ask_enabled,
            "ask_model": public.openrouter_model if public.ask_enabled else None,
            "videos": int(rollup["videos_ready"] or 0),
            # Which policy answered, so a caller can see what it is allowed to
            # ask for rather than discovering it by being clamped.
            "clamps": {
                "policy": policy.name,
                "search_max_limit": policy.search_max_limit,
                "videos_max_limit": policy.videos_max_limit,
            },
            "limits": {
                "search_per_min": public.search_per_min,
                "ask_per_min": public.ask_per_min,
                "ask_per_day": public.ask_per_day,
            },
            "repo": REPO_URL,
        }
    )


async def _demo_search_endpoint(request: Request) -> JSONResponse:
    """The demo page's own `/api/search` — the same handler, one note fewer.

    A closure and not a ``functools.partial``: Starlette's ``Route`` routes a
    plain function through ``request_response`` and treats anything else as an
    ASGI app, and a partial is not a function.
    """
    return await search_endpoint(request, demo=True)


def api_routes(prefix: str = "", *, ask: bool = True, demo: bool = False) -> list[Route]:
    """The read facade, under ``{prefix}/api/*``, bounded by :func:`policy_for`.

    ``prefix`` is what makes the dashboard's JSON the same handlers rather than
    a second implementation: ``api_routes("/dashboard")`` is the whole of
    dashboard.md §2.5.1. ``ask=False`` leaves the LLM route out, which is how
    the dashboard gets JSON without also getting a spend surface.

    ``demo=True`` is the demo page's own group, and the one thing it changes is
    which `note:` lines reach a reader (§6.1). It defaults to *off* so that
    every other caller — the dashboard, anything added later — keeps the query
    layer's full commentary unless it asks not to.

    **There is no ``policy`` parameter any more** (phase 5). It was the shape
    of the phase-1 bug: a bound chosen where the route is registered is a bound
    that cannot see who called, and `/dashboard/api/*` is registered in every
    mode — including the one with nothing in front of it. The handlers resolve
    it per request instead, which is also why these three wrappers are now the
    endpoints themselves.
    """
    routes = [
        Route(
            f"{prefix}/api/search",
            _demo_search_endpoint if demo else search_endpoint,
            methods=["GET"],
        ),
        Route(f"{prefix}/api/videos", videos_endpoint, methods=["GET"]),
        Route(f"{prefix}/api/videos/{{video_id}}", video_endpoint, methods=["GET"]),
        Route(f"{prefix}/api/meta", meta_endpoint, methods=["GET"]),
    ]
    if ask:
        from .ask import ask_endpoint

        routes.append(Route(f"{prefix}/api/ask", ask_endpoint, methods=["POST"]))
    return routes
