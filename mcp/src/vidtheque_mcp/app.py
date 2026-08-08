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
from dataclasses import dataclass

from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.routing import Mount

from .auth.modes import AuthBundle, build_auth
from .config import Settings
from .db import Database
from .embeddings import EmbeddingClient, HTTPEmbeddingClient
from .http import frames_routes, health_routes
from .jobs.runner import PipelineRunner
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


def build_app(
    settings: Settings,
    *,
    embeddings: EmbeddingClient | None = None,
    run_pipeline: bool = True,
) -> Starlette:
    return assemble(settings, embeddings=embeddings, run_pipeline=run_pipeline).app


def assemble(
    settings: Settings,
    *,
    embeddings: EmbeddingClient | None = None,
    run_pipeline: bool = True,
) -> Assembled:
    settings.validate()
    db = Database(
        path=settings.db_path,
        read_pool_size=settings.read_pool_size,
        query_budget_s=settings.query_timeout_s,
    )
    auth = build_auth(settings)
    # No default model: each leg names the encoder it needs from `config`, so
    # the transcript and frame spaces cannot be confused for one another.
    client = embeddings or HTTPEmbeddingClient(settings.worker_url)
    runner = PipelineRunner(db)
    deps = Deps(
        settings=settings,
        db=db,
        embeddings=client,
        frame_signer=auth.frame_signer,
        runner=runner,
        search_semaphore=asyncio.Semaphore(settings.max_concurrent_searches),
    )

    mcp = build_mcp_server(settings, deps, auth)
    mcp_app = mcp.streamable_http_app(
        streamable_http_path="/mcp",
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=settings.allowed_hosts,
            allowed_origins=[f"https://{h}" for h in settings.public_hostnames]
            + ["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"],
        ),
    )

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
                await db.close()
                auth.close()

    routes = [
        *health_routes(settings, db),
        *auth.routes,
        *frames_routes(settings, db, auth),
        # Mount("/") matches everything — it must be last.
        Mount("/", app=mcp_app),
    ]
    app = Starlette(routes=routes, lifespan=lifespan)
    app.state.assembled = Assembled(
        app=app, settings=settings, db=db, deps=deps, auth=auth, runner=runner
    )
    return app.state.assembled
