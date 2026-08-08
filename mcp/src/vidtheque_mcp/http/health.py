"""``GET /healthz`` — always public, in every auth mode."""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from .. import __version__
from ..config import Settings
from ..db import Database


def health_routes(settings: Settings, db: Database) -> list[Route]:
    async def healthz(_: Request) -> JSONResponse:
        return JSONResponse(
            {
                "status": "ok",
                "version": __version__,
                "auth": settings.auth_mode,
                "vector_legs": db.vectors.enabled,
                "writes_allowed": db.writes_allowed,
            }
        )

    return [Route("/healthz", healthz, methods=["GET"])]
