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
from .ratelimit import BudgetStore, RateLimiter, RateLimitMiddleware, SqliteBudgetStore
from .readonly import WRITE_TOOLS, hidden_tools
from .settings import PublicSettings

__all__ = [
    "PublicSettings",
    "SqliteBudgetStore",
    "WRITE_TOOLS",
    "hidden_tools",
    "public_middleware",
    "public_routes",
]

STATIC_DIR = Path(__file__).parent / "static"

# Long enough that a reload is cheap, short enough that a redeploy is visible.
_STATIC_CACHE = "public, max-age=300"

# The kinds of file in `static/`. This route used to type everything that was
# not a stylesheet as `text/javascript`, which loads a font in today's browsers
# — they sniff woff2 — and stops loading it the moment anything in front of this
# app sets `X-Content-Type-Options: nosniff`. That is not a bet worth taking on
# the two faces the whole type system rests on (DESIGN.md, Fonts rule 5), so the
# suffix decides, the way `dashboard/__init__.py` already does it. A suffix that
# is not here — the OFL licence texts beside the fonts — is not an asset and
# 404s rather than being served as something it is not.
_MEDIA = {
    ".css": "text/css",
    ".js": "text/javascript",
    ".html": "text/html",
    ".svg": "image/svg+xml",
    ".woff2": "font/woff2",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
}
_TEXTUAL = frozenset({".css", ".js", ".html", ".svg"})

# Fonts and stills are content-stable binaries: they outlive a deploy in a way a
# stylesheet does not, so they get the cache the page cannot have.
_ASSET_CACHE = "public, max-age=31536000, immutable"

# `/dashboard/login`, per IP, per minute. A constant rather than a knob, for the
# same reason the jobs view's poll interval is one: a number somebody can raise
# is a number somebody raises, and this one is the difference between a password
# form and an oracle.
LOGIN_PER_MIN = 10


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
        media = _MEDIA.get(path.suffix)
        if media is None:
            return Response(status_code=404)
        textual = path.suffix in _TEXTUAL
        return FileResponse(
            path,
            media_type=f"{media}; charset=utf-8" if textual else media,
            headers={"Cache-Control": _STATIC_CACHE if textual else _ASSET_CACHE},
        )

    return [
        *api_routes(),
        Route("/", page, methods=["GET"]),
        Route("/static/{asset:path}", asset, methods=["GET"]),
    ]


def public_middleware(
    public: PublicSettings,
    dashboard_per_min: int | None = None,
    budget: BudgetStore | None = None,
) -> list[Middleware]:
    """The limiter, as a Starlette middleware spec.

    It sits in the root app's stack rather than wrapping the returned object, so
    ``build_app`` still hands back a real ``Starlette``. Non-matching paths —
    including the MCP mount's streaming transport — are passed through
    untouched by the middleware itself.

    It is no longer public-mode-only (dashboard.md §2.5.3): a management surface
    a bot can hammer with ``/dashboard/api/videos`` is the same denial of
    service as a public one, so the ``dashboard`` bucket is installed whenever
    that route group is. ``dashboard_per_min`` arrives as a plain number rather
    than as ``DashboardSettings`` because ``dashboard/`` imports ``public.api``
    and the reverse edge would be a cycle.

    In a private deployment the *only* bucket is ``dashboard``: `/frames/*`
    behaves exactly as it does today, because charging the owner 48 thumbnails
    for one detail page against a 120/min bucket would refuse the second page
    load, and the owner is not the threat model that bucket was written for.
    """
    limits: dict[str, tuple[int, float]] = {}
    if public.enabled:
        limits.update(
            {
                "search": (public.search_per_min, 60.0),
                "ask": (public.ask_per_min, 60.0),
                # A window of a day or more makes this a *daily budget* rather
                # than a rate: `RateLimiter` counts it per UTC day and writes it
                # down, because it is the one bucket that guards money
                # (demo-site.md §4.2). The others reset on a redeploy, and
                # should — a minute bucket means nothing across a restart.
                "ask_global": (public.ask_per_day, 86_400.0),
                "frames": (public.frames_per_min, 60.0),
            }
        )
    if dashboard_per_min is not None:
        limits["dashboard"] = (dashboard_per_min, 60.0)
        # The sign-in page is the one path on this surface where a request is a
        # guess at a secret, so it gets its own, much tighter bucket. Not an env
        # var and deliberately not derived from the loose one: a login rate a
        # deployment can raise is a login rate somebody raises. Ten a minute is
        # more than a human typing a password ever needs and is four orders of
        # magnitude short of useful against even a weak one.
        limits["dashboard_login"] = (LOGIN_PER_MIN, 60.0)
    if not limits:
        return []

    # The store is handed in already-constructed (and opened in the app's
    # lifespan, after the database is): the middleware stack is built before
    # anything is connected, and a limiter that opened its own connection would
    # be a second writer on a file that documents having exactly one.
    limiter = RateLimiter(limits, max_keys=public.rate_max_keys, budget=budget)

    def bucket_for(path: str) -> str | None:
        # `/dashboard/api/*` is matched before `/api/*`, so the owner's JSON is
        # charged to the loose bucket rather than to the anonymous one — and
        # the sign-in page is matched before both, because it is the only path
        # here where a request is a guess.
        if "dashboard" in limits and path.startswith("/dashboard"):
            return "dashboard_login" if path == "/dashboard/login" else "dashboard"
        if "search" not in limits:
            return None
        if path == "/api/ask":
            return "ask"
        if path.startswith("/api/"):
            return "search"
        if path.startswith("/frames/"):
            return "frames"
        return None

    def extra_buckets(path: str) -> tuple[str, ...]:
        return ("ask_global",) if path == "/api/ask" and "ask_global" in limits else ()

    return [
        Middleware(
            RateLimitMiddleware,
            limiter=limiter,
            bucket_for=bucket_for,
            trusted_header=public.trusted_ip_header,
            extra_buckets=extra_buckets,
        )
    ]
