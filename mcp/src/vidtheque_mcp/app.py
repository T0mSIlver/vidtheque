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
from dataclasses import dataclass, field

import httpx2 as httpx
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.routing import Mount

from .auth.modes import AuthBundle, build_auth
from .config import Settings
from .db import Database
from .embeddings import EmbeddingClient
from .http import frames_routes, health_routes
from .jobs.runner import Pipeline, PipelineRunner
from .pipeline import PipelineSettings, WorkerAPI, build_pipeline, worker_client
from .public import PublicSettings, hidden_tools, public_middleware, public_routes
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
    public: PublicSettings = field(default_factory=PublicSettings)


def build_app(
    settings: Settings,
    *,
    embeddings: EmbeddingClient | None = None,
    run_pipeline: bool = True,
    pipeline: Pipeline | None = None,
    public: PublicSettings | None = None,
    public_http: httpx.AsyncClient | None = None,
) -> Starlette:
    return assemble(
        settings,
        embeddings=embeddings,
        run_pipeline=run_pipeline,
        pipeline=pipeline,
        public=public,
        public_http=public_http,
    ).app


def assemble(
    settings: Settings,
    *,
    embeddings: EmbeddingClient | None = None,
    run_pipeline: bool = True,
    pipeline: Pipeline | None = None,
    public: PublicSettings | None = None,
    public_http: httpx.AsyncClient | None = None,
) -> Assembled:
    """``public`` / ``public_http`` are the demo seam: the mode, and the LLM
    client behind ``/api/ask`` (a ``MockTransport`` in tests, exactly as
    ``embeddings=`` fakes the worker)."""
    settings.validate()
    public = public if public is not None else PublicSettings.from_env()
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

    @contextlib.asynccontextmanager
    async def lifespan(_: Starlette) -> AsyncIterator[None]:
        await db.open()
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
                await db.close()
                auth.close()

    routes = [
        *health_routes(settings, db),
        *auth.routes,
        *frames_routes(settings, db, auth),
        *(public_routes() if public.enabled else []),
        # Mount("/") matches everything — it must be last.
        Mount("/", app=mcp_app),
    ]
    # The limiter lives in the root app's middleware stack: `/api/*` and
    # `/frames/*` are charged per IP, everything else (including the MCP
    # mount's streaming transport) is passed straight through.
    app = Starlette(
        routes=routes,
        middleware=public_middleware(public) if public.enabled else [],
        lifespan=lifespan,
    )
    app.state.public_settings = public
    app.state.openrouter = llm
    app.state.assembled = Assembled(
        app=app,
        settings=settings,
        db=db,
        deps=deps,
        auth=auth,
        runner=runner,
        public=public,
    )
    return app.state.assembled
