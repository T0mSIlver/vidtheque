"""`/dashboard` — the management surface, as a route group beside `public/`.

`docs/design/dashboard.md` is the contract. The decision it encodes: today the
management surface of vidtheque is the MCP server, and that is backwards. An
agent is the right consumer of the corpus and the wrong consumer of the
corpus's *plumbing* — nobody should have to ask a language model which model
transcribed a video or whether OCR quietly failed on forty of them.

Phase 1 is read-only: the corpus overview, the videos table and the video
detail page, plus `/dashboard/api/*` — the same handlers `/api/*` uses, which is
also the JSON facade a private deployment could not have before
(demo-site.md §7.4). Those handlers were bounded by owner clamps *because of the
prefix* until phase 5 keyed them off the credential instead; see
`public/api.py:policy_for`.

Phase 2 adds the jobs view and its poll target, still read-only: `not_before`
as a live countdown, `attempts`, the degraded list and the `job_events` tail
(§5.4), redacted to codes, counts and clocks in demo mode (§2.4).

It lives inside the mcp server because all state does (CLAUDE.md), and it never
speaks MCP: it calls `tools/*` and `db/queries.py` directly, exactly as `/api`
already does.
"""

from __future__ import annotations

from pathlib import Path

from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Route

from ..public.api import api_routes
from . import views, writes
from .access import (
    WRITE_ROUTES,
    credential,
    origin_ok,
    require_write,
    sign_in_hint,
    write_side_enabled,
)
from .settings import ROOT, DashboardSettings

__all__ = [
    "ROOT",
    "WRITE_ROUTES",
    "DashboardSettings",
    "credential",
    "dashboard_routes",
    "origin_ok",
    "require_write",
    "write_side_enabled",
]

STATIC_DIR = Path(__file__).parent / "static"

# Its own asset route rather than `public/static`: that one is registered only
# in public mode, and a private deployment must not need the demo turned on to
# get a stylesheet.
_STATIC_CACHE = "public, max-age=300"

# The three kinds of file in `static/`. A font served as `text/javascript` does
# load in today's browsers — they sniff woff2 — but it stops loading the moment
# anything in front of this app sets `X-Content-Type-Options: nosniff`, which is
# not a bet worth taking on the one asset the whole type system rests on. The
# fonts also outlive a deploy in a way a stylesheet does not: they are
# content-stable binaries, so they get the long immutable cache the CSS cannot.
_MEDIA = {".css": "text/css", ".js": "text/javascript", ".woff2": "font/woff2"}
_FONT_CACHE = "public, max-age=31536000, immutable"


def dashboard_routes(*, write_side: bool = False) -> list[Route]:
    """The route group. Order matters only against ``Mount("/")`` in app.py.

    ``write_side`` is :func:`~.access.write_side_enabled`, resolved once by the
    caller from the auth mode and ``VIDTHEQUE_PUBLIC_READONLY``. When it is
    false **none of the write routes and no login page are in this list** —
    they 404 through the ``Mount("/")`` fallback like any other path this
    server does not serve, which is §2.3's rule and demo-site.md §1.1's
    argument: a route that exists and refuses is a route somebody probes.
    """

    def guarded(handler, json: bool = False):
        """Read access: whatever `/frames/*` accepts, minus the signed URL.

        In `none` mode this is open, because the corpus is already open through
        `/mcp` and `/frames` in that mode — a dashboard that demanded a
        credential there would be theatre. In `token`/`oauth` it takes the
        bearer or the existing session cookie, and the refusal names which,
        rather than making the owner guess.
        """

        async def wrapper(request: Request) -> Response:
            if await credential(request) is not None:
                return await handler(request)
            mode = request.app.state.assembled.settings.auth_mode
            if json:
                return JSONResponse(
                    {
                        "error": "E_AUTH_REQUIRED",
                        "message": "The dashboard needs the owner's token or session.",
                        # `write_side` rather than a constant: the sign-in page
                        # is not registered in every deployment that refuses.
                        "next": sign_in_hint(mode, login=write_side),
                    },
                    status_code=401,
                )
            return views.sign_in_page(request, mode, login=write_side)

        wrapper.__name__ = getattr(handler, "__name__", "guarded")
        return wrapper

    async def asset(request: Request) -> Response:
        name = request.path_params["asset"]
        path = (STATIC_DIR / name).resolve()
        try:  # never serve anything outside the packaged static directory
            path.relative_to(STATIC_DIR.resolve())
        except ValueError:
            return Response(status_code=404)
        if not path.is_file():
            return Response(status_code=404)
        media = _MEDIA.get(path.suffix)
        if media is None:  # the OFL texts and the provenance note are not assets
            return Response(status_code=404)
        binary = path.suffix == ".woff2"
        return FileResponse(
            path,
            media_type=media if binary else f"{media}; charset=utf-8",
            headers={"Cache-Control": _FONT_CACHE if binary else _STATIC_CACHE},
        )

    async def trailing_slash(request: Request) -> Response:
        """`/dashboard/` is `/dashboard`, not a 404.

        Starlette's own `redirect_slashes` never fires here: `Mount("/")` is
        the last route and it matches everything, so the router finds a handler
        for `/dashboard/` before it ever considers a redirect. Typing the slash
        is not a mistake worth a 404 — and an unguarded redirect leaks nothing,
        so it sits outside the credential check with the stylesheet.
        """
        query = request.url.query
        return RedirectResponse(f"{ROOT}?{query}" if query else ROOT, status_code=308)

    # The write side, or nothing at all. Declared here rather than inline so
    # the list and `WRITE_ROUTES` can be asserted against each other: a write
    # route that forgets to declare itself fails the suite (§2.5.4).
    write_routes: list[Route] = (
        [
            # The login page is a write route by the same predicate as the
            # rest, and for the same reason (§3.2 rule 3): it is the only GET
            # in the group that would exist purely to be probed.
            Route(f"{ROOT}/login", writes.login, methods=["GET", "POST"]),
            Route(f"{ROOT}/logout", writes.logout, methods=["POST"]),
            # The form itself is a read and takes the read gate, so an
            # unauthenticated browser gets the sign-in page rather than a page
            # of controls it cannot use.
            Route(f"{ROOT}/index", guarded(writes.index_form), methods=["GET"]),
            Route(f"{ROOT}/index", writes.index_submit, methods=["POST"]),
            Route(
                f"{ROOT}/videos/{{video_id}}/reindex",
                writes.reindex,
                methods=["POST"],
            ),
            Route(
                f"{ROOT}/videos/{{video_id}}/tags",
                writes.set_tags,
                methods=["POST"],
            ),
        ]
        if write_side
        else []
    )

    return [
        # Static first: it is the one path under the prefix that carries no
        # corpus data, and it must load even on the 401 page.
        Route(f"{ROOT}/static/{{asset:path}}", asset, methods=["GET"]),
        Route(f"{ROOT}/", trailing_slash, methods=["GET"]),
        # Ahead of the read pages. `/videos/{video_id}` and
        # `/videos/{video_id}/reindex` differ in segment count so neither can
        # shadow the other today, but the ordering means a future read route
        # with a greedier converter cannot quietly swallow a POST either.
        *write_routes,
        # The same handlers `/api/*` uses — behind the same gate as the pages,
        # because JSON that skips the credential check is the hole the pages
        # were guarded against, and under whatever clamps the *caller* earns.
        #
        # The clamps used to be pinned to `OWNER_CLAMPS` here, which was right
        # only while the prefix implied the caller. It does not in `AUTH=none`,
        # where the gate above is open by design: `public/api.py:policy_for`
        # now resolves the policy per request, so this prefix grants a wider
        # bound to a bearer or a session and the demo's bound to everyone else
        # (phase 5; `docs/deploy-public.md`'s clamp audit item).
        *[
            Route(route.path, guarded(route.endpoint, json=True), methods=["GET"])
            for route in api_routes(ROOT, ask=False)
        ],
        # The jobs view's own poll target. Not one of `api_routes`' handlers
        # because `/api/*` answers questions about the *corpus* and this one
        # answers a question about the machine — but the same prefix, the same
        # gate and the same clamps, because a JSON route that skips either is
        # the hole the pages were guarded against.
        Route(f"{ROOT}/api/jobs", guarded(views.jobs_json, json=True), methods=["GET"]),
        Route(
            f"{ROOT}/api/jobs/{{job_id}}",
            guarded(views.job_json, json=True),
            methods=["GET"],
        ),
        Route(ROOT, guarded(views.overview), methods=["GET"]),
        Route(f"{ROOT}/videos", guarded(views.videos), methods=["GET"]),
        Route(f"{ROOT}/videos/{{video_id}}", guarded(views.video_detail), methods=["GET"]),
        Route(f"{ROOT}/jobs", guarded(views.jobs), methods=["GET"]),
        Route(f"{ROOT}/jobs/{{job_id}}", guarded(views.job_detail), methods=["GET"]),
    ]
