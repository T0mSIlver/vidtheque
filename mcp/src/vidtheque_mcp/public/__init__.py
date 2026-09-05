"""The public surface: read-only masking, the `/api` facade, rate limits.

``VIDTHEQUE_PUBLIC_READONLY=1`` turns them on together — one mode resolved at
app-construction time, like ``VIDTHEQUE_AUTH``, never a per-route conditional.
`docs/design/demo-site.md` is the contract.

**The pages left this package on 2026-09-05.** `/` and `/demo` are the Next.js
front end's in `web/`, and `GET /static/{path}` went with them — nothing this
app serves under this flag is a document any more. Production is one origin
split by path: the exact page GETs go to Next, everything else — `/api/*`,
`/frames/*`, `/mcp`, `/auth/*`, `/.well-known/*`, `/healthz`,
`/videos/{id}/export.md` and `/dashboard/*` — comes here. So the document
policy that used to live here (CSP, `X-Frame-Options`, `Referrer-Policy`) is
the front end's to send; the one document Python still serves of its own, the
OAuth consent screen, carries its own pair in `auth/login.py`.

`static/fonts/` stays and is not dead: DESIGN.md makes it the document of
record for the two faces, and `dashboard/__init__.py` aliases its own asset
route onto it rather than keeping a second copy.
"""

from __future__ import annotations

from starlette.middleware import Middleware
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

# `/dashboard/login`, per IP, per minute. A constant rather than a knob, for the
# same reason the jobs view's poll interval is one: a number somebody can raise
# is a number somebody raises, and this one is the difference between a password
# form and an oracle.
LOGIN_PER_MIN = 10


def public_routes() -> list[Route]:
    """`/api/*`, and nothing else.

    Order matters only against ``Mount("/", mcp_app)`` in ``app.py``, which
    matches everything and must stay last.

    Until 2026-09-05 this also registered the landing at `/`, the demo at
    `/demo`, and the asset route under `/static/`. All three moved to the
    Next.js app, so they are gone rather than kept as redirects: a redirect on
    `/` here would be a second answer to a question the edge has already
    routed elsewhere. A private deployment is unaffected — this whole list is
    registered only under `VIDTHEQUE_PUBLIC_READONLY=1`.
    """
    return api_routes(demo=True)


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
                # `/mcp` used to be the one route with no bucket at all, on the
                # argument that an agent's traffic is not a browser's. True, and
                # it still let an anonymous caller loop `tools/call` at machine
                # speed on the surface that reaches the GPU: a `search` embeds
                # its query before any admission control, so N concurrent calls
                # are 2N forward passes with nothing bounding N. The bucket is
                # deliberately loose — an agent legitimately makes a burst of
                # calls to answer one question — but a ceiling that exists is
                # the difference from one that does not.
                # (2026-08-10 audit, F-1.)
                "mcp": (public.mcp_per_min, 60.0),
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
        if path == "/mcp" or path.startswith("/mcp/"):
            return "mcp"
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
