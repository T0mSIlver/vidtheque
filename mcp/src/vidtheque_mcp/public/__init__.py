"""The public demo surface: read-only masking, `/api`, the page, rate limits.

``VIDTHEQUE_PUBLIC_READONLY=1`` turns all four on together — one mode resolved
at app-construction time, like ``VIDTHEQUE_AUTH``, never a per-route
conditional. `docs/design/demo-site.md` is the contract.
"""

from __future__ import annotations

from pathlib import Path

from starlette.middleware import Middleware
from starlette.responses import FileResponse, Response
from starlette.routing import Route

from .api import api_routes
from .ratelimit import RateLimiter, RateLimitMiddleware
from .readonly import WRITE_TOOLS, hidden_tools
from .settings import PublicSettings

__all__ = [
    "PublicSettings",
    "WRITE_TOOLS",
    "hidden_tools",
    "public_middleware",
    "public_routes",
]

STATIC_DIR = Path(__file__).parent / "static"

# Long enough that a reload is cheap, short enough that a redeploy is visible.
_STATIC_CACHE = "public, max-age=300"


def public_routes() -> list[Route]:
    """`/api/*`, the demo page at `/`, and its two static files.

    Order matters only against ``Mount("/", mcp_app)`` in ``app.py``, which
    matches everything and must stay last.
    """

    async def page(_request) -> Response:
        return FileResponse(
            STATIC_DIR / "index.html",
            media_type="text/html; charset=utf-8",
            headers={"Cache-Control": _STATIC_CACHE},
        )

    async def asset(request) -> Response:
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
        *api_routes(),
        Route("/", page, methods=["GET"]),
        Route("/static/{asset:path}", asset, methods=["GET"]),
    ]


def public_middleware(public: PublicSettings) -> list[Middleware]:
    """The limiter, as a Starlette middleware spec.

    It sits in the root app's stack rather than wrapping the returned object, so
    ``build_app`` still hands back a real ``Starlette``. Non-matching paths —
    including the MCP mount's streaming transport — are passed through
    untouched by the middleware itself.
    """
    limiter = RateLimiter(
        {
            "search": (public.search_per_min, 60.0),
            "ask": (public.ask_per_min, 60.0),
            # The same maths with a 24h window, so the budget trickles back
            # through the day instead of unblocking at UTC midnight.
            "ask_global": (public.ask_per_day, 86_400.0),
            "frames": (public.frames_per_min, 60.0),
        },
        max_keys=public.rate_max_keys,
    )

    def bucket_for(path: str) -> str | None:
        if path == "/api/ask":
            return "ask"
        if path.startswith("/api/"):
            return "search"
        if path.startswith("/frames/"):
            return "frames"
        return None

    def extra_buckets(path: str) -> tuple[str, ...]:
        return ("ask_global",) if path == "/api/ask" else ()

    return [
        Middleware(
            RateLimitMiddleware,
            limiter=limiter,
            bucket_for=bucket_for,
            trusted_header=public.trusted_ip_header,
            extra_buckets=extra_buckets,
        )
    ]
