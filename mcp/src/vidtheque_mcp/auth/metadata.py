"""Our own RFC 8414 and RFC 9728 documents.

We serve ``/.well-known/oauth-authorization-server`` rather than the SDK's,
because the SDK's ``build_metadata()`` has two gaps that between them switch
CIMD off for every client that checks:

* ``token_endpoint_auth_methods_supported`` is hard-coded to
  ``["client_secret_post", "client_secret_basic"]`` — ``"none"`` is never
  advertised;
* ``client_id_metadata_document_supported`` exists on the model but is never set.

Claude picks CIMD only when **both** are right; missing either, it falls back
to hunting for a ``registration_endpoint``.

The ``offline_access`` placement is the other subtlety worth stating out loud:
Claude appends ``offline_access`` to the requested scopes *only if the
authorization server* metadata lists it in ``scopes_supported``, while the MCP
spec says the **protected resource** SHOULD NOT list it. So it goes in the AS
metadata and stays out of the PRM. Get it wrong and you get no refresh token.
"""

from __future__ import annotations

from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from ..config import OFFLINE_SCOPE, Settings


def authorization_server_metadata(settings: Settings) -> dict[str, Any]:
    issuer = settings.issuer_url
    return {
        "issuer": issuer,
        "authorization_endpoint": f"{issuer}/authorize",
        "token_endpoint": f"{issuer}/token",
        "registration_endpoint": f"{issuer}/register",
        "revocation_endpoint": f"{issuer}/revoke",
        # offline_access here and ONLY here.
        "scopes_supported": [*settings.scopes_supported, OFFLINE_SCOPE],
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "token_endpoint_auth_methods_supported": ["none"],
        "revocation_endpoint_auth_methods_supported": ["none"],
        "code_challenge_methods_supported": ["S256"],
        "client_id_metadata_document_supported": True,
        "authorization_response_iss_parameter_supported": True,
    }


def protected_resource_metadata(settings: Settings) -> dict[str, Any]:
    return {
        # Must match the MCP server URL exactly as the user types it into Claude.
        "resource": settings.resource_url,
        # Claude uses the FIRST entry only and does not fall back.
        "authorization_servers": [settings.issuer_url],
        # Deliberately no offline_access: the PRM SHOULD NOT list it.
        "scopes_supported": list(settings.scopes_supported),
        "bearer_methods_supported": ["header"],
        "resource_name": "vidtheque",
    }


def _json(payload: dict[str, Any]) -> JSONResponse:
    return JSONResponse(payload, headers={"Cache-Control": "public, max-age=300"})


def metadata_routes(settings: Settings) -> list[Route]:
    """Both PRM paths: Claude probes the path-suffixed variant first."""

    async def as_metadata(_: Request) -> JSONResponse:
        return _json(authorization_server_metadata(settings))

    async def prm(_: Request) -> JSONResponse:
        return _json(protected_resource_metadata(settings))

    return [
        Route("/.well-known/oauth-authorization-server", as_metadata, methods=["GET"]),
        Route("/.well-known/oauth-protected-resource/mcp", prm, methods=["GET"]),
        Route("/.well-known/oauth-protected-resource", prm, methods=["GET"]),
    ]
