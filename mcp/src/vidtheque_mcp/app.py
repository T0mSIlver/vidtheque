"""``build_app(settings) -> Starlette`` — the ASGI root.

The host app owns the root and MCP is a mount, for two reasons that are really
one: custom routes are *never* authenticated in either framework (deliberately
— health checks and OAuth callbacks must be reachable before any token exists),
and ``/frames/<id>.jpg`` must be authenticated. So the framework's job shrinks
to tool registration and the streamable-HTTP transport.

Three gotchas, all documented, all real:

* **A mounted sub-app's lifespan never runs.** The top-level app must enter
  ``mcp.session_manager.run()`` itself, or the first request dies with
  ``RuntimeError: Task group is not initialized.``
* ``mcp.session_manager`` only exists **after** ``streamable_http_app()`` has
  been called.
* ``Mount("/")`` matches everything, so our routes are listed **before** it.

And the fourth, from §3.3: the transport arms DNS-rebinding protection with a
localhost-only allowlist, so behind a real hostname every request is
``421 Misdirected Request`` until ``allowed_hosts`` names it. That is what
``VIDTHEQUE_PUBLIC_HOSTNAME`` feeds.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field, replace

import httpx2 as httpx
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.routing import Mount

from .auth.modes import AuthBundle, build_auth
from .config import Settings, _env
from .dashboard import (
    DashboardSettings,
    dashboard_routes,
    refuse_proxy_origin_cidrs,
    write_side_enabled,
)
from .db import Database
from .embeddings import EmbeddingClient
from .http import export_routes, frames_routes, health_routes
from .follows.scheduler import enqueue_due
from .jobs.runner import Pipeline, PipelineRunner
from .pipeline import PipelineSettings, WorkerAPI, build_pipeline, worker_client
from .public import (
    PublicSettings,
    SqliteBudgetStore,
    hidden_tools,
    public_middleware,
    public_routes,
)
from .public.ask import OpenRouter
from .server import build_mcp_server
from .tools import Deps

logger = logging.getLogger(__name__)


@dataclass
class Assembled:
    """The parts, exposed so tests can reach past the ASGI surface."""

    app: Starlette
    settings: Settings
    db: Database
    deps: Deps
    auth: AuthBundle
    runner: PipelineRunner
    worker_status_http: httpx.AsyncClient | None = None
    public: PublicSettings = field(default_factory=PublicSettings)
    dashboard: DashboardSettings = field(default_factory=DashboardSettings)


def build_app(
    settings: Settings,
    *,
    embeddings: EmbeddingClient | None = None,
    run_pipeline: bool | None = None,
    pipeline: Pipeline | None = None,
    public: PublicSettings | None = None,
    dashboard: DashboardSettings | None = None,
    public_http: httpx.AsyncClient | None = None,
    worker_status_http: httpx.AsyncClient | None = None,
) -> Starlette:
    return assemble(
        settings,
        embeddings=embeddings,
        run_pipeline=run_pipeline,
        pipeline=pipeline,
        public=public,
        dashboard=dashboard,
        public_http=public_http,
        worker_status_http=worker_status_http,
    ).app


def _tighten_for_public(settings: Settings, public: PublicSettings) -> Settings:
    """Defaults that are right for an owner's agent and wrong for the internet.

    ``get-frames return="image"`` ships at four images and 6 MiB of raw JPEG,
    which base64 turns into an ~8 MiB response with the raw bytes, the encoded
    string and the JSON copy all alive at once — and the byte cap is consulted
    *after* the file is read. On a private box that is a sensible convenience
    for an agent that cannot follow a URL. Pointed at the internet it is a
    memory and uplink amplifier on the surface with the loosest limit, so in
    public mode the default becomes zero and callers get URLs instead, which is
    what CLAUDE.md's frames-by-URL rule wants anyway.

    An operator who sets the variable explicitly still means it: this only
    moves the *default*. (2026-08-10 audit, F-13.)
    """
    if not public.enabled:
        return settings
    if _env("VIDTHEQUE_INLINE_FRAME_MAX") is not None:
        return settings
    return replace(settings, inline_frame_max=0)


def assemble(
    settings: Settings,
    *,
    embeddings: EmbeddingClient | None = None,
    run_pipeline: bool | None = None,
    pipeline: Pipeline | None = None,
    public: PublicSettings | None = None,
    dashboard: DashboardSettings | None = None,
    public_http: httpx.AsyncClient | None = None,
    worker_status_http: httpx.AsyncClient | None = None,
) -> Assembled:
    """``public`` / ``public_http`` are the demo seam: the mode, and the LLM
    client behind ``/api/ask`` (a ``MockTransport`` in tests, exactly as
    ``embeddings=`` fakes the worker). ``worker_status_http`` is the equivalent
    HTTP seam for the dashboard's bounded ``GET /status`` probe. ``dashboard``
    is the same seam for the management route group."""
    settings.validate()
    run_pipeline = settings.run_pipeline if run_pipeline is None else run_pipeline
    public = public if public is not None else PublicSettings.from_env()
    dashboard = dashboard if dashboard is not None else DashboardSettings.from_env()
    settings = _tighten_for_public(settings, public)
    # An allowlist that covers the proxy's own socket makes every visitor
    # through the proxy an owner. Refused at boot (gate G2, 2026-08-11): it was
    # a warning, and a warning is a control that depends on someone reading it.
    refuse_proxy_origin_cidrs(dashboard, public.trusted_ip_header, settings.public_hostnames)
    db = Database(
        path=settings.db_path,
        read_pool_size=settings.read_pool_size,
        query_budget_s=settings.query_timeout_s,
        stale_claim_s=settings.stale_claim_s,
    )
    auth = build_auth(settings)
    # No default model: each leg names the encoder it needs from `config`, so
    # the transcript and frame spaces cannot be confused for one another. The
    # indexing half needs three more endpoints than query time does, so the
    # default client is the superset — same object, handed to both.
    pipeline_settings = PipelineSettings.from_env()
    client = embeddings or worker_client(settings.worker_url, pipeline_settings)
    # A fake embedding client (tests) is not a worker: the pipeline gets a real
    # HTTP client of its own rather than an object missing half the surface.
    runner = PipelineRunner(
        db,
        pipeline
        or build_pipeline(
            settings,
            db,
            worker=client if isinstance(client, WorkerAPI) else None,
            pipeline_settings=pipeline_settings,
        ),
        stale_after_s=settings.stale_claim_s,
    )
    # The follow clock, on the loop's own tick rather than beside it. Nothing
    # is enqueued when `VIDTHEQUE_FOLLOW_CHECKS=0`, and nothing is enqueued in
    # a build with the pipeline off either — a queued check that no runner will
    # ever claim is worse than no check at all, because the dashboard would
    # show it waiting (follows/scheduler.py).
    if run_pipeline and pipeline_settings.follow_checks:
        runner.before_claim = lambda: enqueue_due(db, enabled=True)
    deps = Deps(
        settings=settings,
        db=db,
        embeddings=client,
        frame_signer=auth.frame_signer,
        runner=runner,
        search_semaphore=asyncio.Semaphore(settings.max_concurrent_searches),
    )

    # Read-only public mode: the write tools are never handed to `add_tool`, so
    # they are absent from `tools/list` rather than present-and-refusing
    # (demo-site.md §1.1).
    mcp = build_mcp_server(settings, deps, auth, hidden_tools(public.enabled))
    mcp_app = mcp.streamable_http_app(
        streamable_http_path="/mcp",
        # Stateless, and it has to be. The default keeps a transport and a
        # server task per session in the manager, with no idle timeout and no
        # cap — and `/mcp` is deliberately the one route the rate limiter never
        # sees. So an anonymous `initialize` in a loop, which needs no
        # credential and costs the caller nothing, accumulates sessions until
        # the box dies. Nothing here holds per-session state: the tools take
        # their dependencies from `deps`, and the resources are static.
        # (2026-08-10 audit, F-2.)
        stateless_http=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=settings.allowed_hosts,
            allowed_origins=[f"https://{h}" for h in settings.public_hostnames]
            + ["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"],
        ),
    )

    http: httpx.AsyncClient | None = None
    llm: OpenRouter | None = None
    if public.enabled and public.ask_enabled:
        http = public_http or httpx.AsyncClient(timeout=60.0)
        llm = OpenRouter(public, http)
    elif public_http is not None:  # an injected client with no key: still closed
        http = public_http

    status_http = worker_status_http
    if status_http is None and dashboard.enabled and settings.worker_url:
        status_http = httpx.AsyncClient()

    # The daily ask budget's durable half. Public mode only — it is the only
    # mode with a daily bucket to persist — and constructed here rather than
    # inside the limiter so its lifecycle hangs off the same lifespan the
    # database's does, in the right order (demo-site.md §4.2).
    budget = SqliteBudgetStore(db) if public.enabled else None

    @contextlib.asynccontextmanager
    async def lifespan(_: Starlette) -> AsyncIterator[None]:
        await db.open()
        if budget is not None:
            await budget.open()
        if not db.vectors.enabled:
            logger.warning("vector legs disabled: %s", db.vectors.reason)
        async with mcp.session_manager.run():
            if run_pipeline:
                await runner.start()
            try:
                yield
            finally:
                if run_pipeline:
                    await runner.stop()
                await client.aclose()
                if http is not None:
                    await http.aclose()
                if status_http is not None and status_http is not http:
                    await status_http.aclose()
                # Before the database: the drain has deltas to write and it
                # writes them through the connection closed on the next line.
                if budget is not None:
                    await budget.close()
                await db.close()
                auth.close()

    routes = [
        *health_routes(settings, db),
        *auth.routes,
        *frames_routes(settings, db, auth),
        *export_routes(settings, db),
        *(public_routes() if public.enabled else []),
        # The write side is resolved here, once, beside every other mode
        # decision: read-only mode and `AUTH=none` each register no write
        # routes and no login page at all (dashboard.md §2.3, §3.2 rule 3) —
        # the same discipline as `hidden_tools` above, for the same reason.
        *(
            dashboard_routes(
                write_side=write_side_enabled(settings.auth_mode, public.enabled)
            )
            if dashboard.enabled
            else []
        ),
        # Mount("/") matches everything — it must be last.
        Mount("/", app=mcp_app),
    ]
    # The limiter lives in the root app's middleware stack: `/api/*`,
    # `/frames/*` and `/dashboard/*` are charged per IP, everything else
    # (including the MCP mount's streaming transport) is passed straight
    # through. It is no longer public-mode-only — dashboard.md §2.5.3.
    app = Starlette(
        routes=routes,
        middleware=public_middleware(
            public,
            dashboard.rate_per_min if dashboard.enabled else None,
            budget=budget,
        ),
        lifespan=lifespan,
    )
    app.state.public_settings = public
    app.state.dashboard_settings = dashboard
    app.state.openrouter = llm
    app.state.assembled = Assembled(
        app=app,
        settings=settings,
        db=db,
        deps=deps,
        auth=auth,
        runner=runner,
        worker_status_http=status_http,
        public=public,
        dashboard=dashboard,
    )
    return app.state.assembled
