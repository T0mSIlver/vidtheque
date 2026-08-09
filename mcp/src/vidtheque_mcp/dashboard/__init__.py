"""`/dashboard` — the management surface, as a route group beside `public/`.

`docs/design/dashboard.md` is the contract. The decision it encodes: today the
management surface of vidtheque is the MCP server, and that is backwards. An
agent is the right consumer of the corpus and the wrong consumer of the
corpus's *plumbing* — nobody should have to ask a language model which model
transcribed a video or whether OCR quietly failed on forty of them.

Phase 1 is read-only: the corpus overview, the videos table and the video
detail page, plus `/dashboard/api/*` — the same handlers `/api/*` uses, under
owner clamps, which is also the JSON facade a private deployment could not have
before (demo-site.md §7.4).

It lives inside the mcp server because all state does (CLAUDE.md), and it never
speaks MCP: it calls `tools/*` and `db/queries.py` directly, exactly as `/api`
already does.
"""

from __future__ import annotations

from pathlib import Path

from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Route

from ..public.api import OWNER_CLAMPS, api_routes
from . import views
from .access import WRITE_ROUTES, credential, origin_ok, require_write, sign_in_hint
from .settings import ROOT, DashboardSettings

__all__ = [
    "ROOT",
    "WRITE_ROUTES",
    "DashboardSettings",
    "credential",
    "dashboard_routes",
    "origin_ok",
    "require_write",
]

STATIC_DIR = Path(__file__).parent / "static"

# Its own asset route rather than `public/static`: that one is registered only
# in public mode, and a private deployment must not need the demo turned on to
# get a stylesheet.
_STATIC_CACHE = "public, max-age=300"


def dashboard_routes() -> list[Route]:
    """The route group. Order matters only against ``Mount("/")`` in app.py."""

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
                        "next": sign_in_hint(mode),
                    },
                    status_code=401,
                )
            return views.sign_in_page(request, mode)

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
        media = "text/css" if path.suffix == ".css" else "text/javascript"
        return FileResponse(
            path,
            media_type=f"{media}; charset=utf-8",
            headers={"Cache-Control": _STATIC_CACHE},
        )

    return [
        # Static first: it is the one path under the prefix that carries no
        # corpus data, and it must load even on the 401 page.
        Route(f"{ROOT}/static/{{asset:path}}", asset, methods=["GET"]),
        # The same handlers `/api/*` uses, under owner clamps — and behind the
        # same gate as the pages, because JSON that skips the credential check
        # is the hole the pages were guarded against.
        *[
            Route(route.path, guarded(route.endpoint, json=True), methods=["GET"])
            for route in api_routes(OWNER_CLAMPS, ROOT, ask=False)
        ],
        Route(ROOT, guarded(views.overview), methods=["GET"]),
        Route(f"{ROOT}/videos", guarded(views.videos), methods=["GET"]),
        Route(f"{ROOT}/videos/{{video_id}}", guarded(views.video_detail), methods=["GET"]),
    ]
