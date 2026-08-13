"""``VIDTHEQUE_AUTH=none|token|oauth`` — one branch, at app-construction time.

| mode | what runs | who it is for |
|---|---|---|
| `none` | no AuthSettings, no PRM, no 401; frames served unsigned | localhost / trusted LAN |
| `token` | a static bearer over `VIDTHEQUE_TOKEN`; frames need a signature or the bearer | tunnel without OAuth: Claude Code `--header`, Cursor, claude.ai `static_headers` |
| `oauth` | the full self-contained AS | claude.ai custom connector — the documented default for public exposure |

The important design rule is that the mode resolves **here**, into a small
bundle the app assembles, rather than becoming per-route conditionals.
"""

from __future__ import annotations

import hmac
import time
from dataclasses import dataclass, field

from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.routes import create_auth_routes
from mcp.server.auth.settings import (
    AuthSettings,
    ClientRegistrationOptions,
    RevocationOptions,
)
from pydantic import AnyHttpUrl
from starlette.routing import Route

from ..config import OFFLINE_SCOPE, READ_SCOPE, WRITE_SCOPE, Settings
from .cimd import CIMDFetcher
from .login import login_routes
from .metadata import metadata_routes
from .provider import VidthequeOAuthProvider
from .store import AuthStore
from .tokens import FrameUrlSigner, TokenIssuer


class StaticTokenVerifier(TokenVerifier):
    """`token` mode: one shared secret, compared in constant time."""

    def __init__(self, token: str, resource: str) -> None:
        self._token = token
        self._resource = resource

    async def verify_token(self, token: str) -> AccessToken | None:
        if not hmac.compare_digest(token, self._token):
            return None
        return AccessToken(
            token=token,
            client_id="static",
            scopes=[READ_SCOPE, WRITE_SCOPE],
            expires_at=int(time.time()) + 3600,
            resource=self._resource,
            subject="owner",
        )


class JWTTokenVerifier(TokenVerifier):
    """`oauth` mode: signature + `aud` + `exp`, no database read."""

    def __init__(self, provider: VidthequeOAuthProvider) -> None:
        self._provider = provider

    async def verify_token(self, token: str) -> AccessToken | None:
        return await self._provider.load_access_token(token)


@dataclass
class AuthBundle:
    """Everything the app needs to wire authentication for the chosen mode."""

    mode: str
    token_verifier: TokenVerifier | None = None
    auth_settings: AuthSettings | None = None
    routes: list[Route] = field(default_factory=list)
    frame_signer: FrameUrlSigner | None = None
    store: AuthStore | None = None
    provider: VidthequeOAuthProvider | None = None

    def close(self) -> None:
        if self.store is not None:
            self.store.close()


def build_auth(settings: Settings) -> AuthBundle:
    secret = settings.resolve_secret()
    signer = FrameUrlSigner(secret, settings.frame_url_ttl_s)

    if settings.auth_mode == "none":
        # Omitting both auth= and token_verifier= is what an authless connector
        # needs: no PRM route, no 401 ever, Claude probes the well-known paths,
        # gets 404, and connects anonymously.
        return AuthBundle(mode="none", frame_signer=None)

    resource = settings.resource_url

    if settings.auth_mode == "token":
        assert settings.static_token
        # `token` mode carries a session store too, from phase 3 of the
        # dashboard (dashboard.md §3.2 rule 2). The cookie is the *existing*
        # `vidtheque_session` in the *existing* `login_sessions` table — what
        # changed is that this mode now has somewhere to write one, so a human
        # can type the secret into a login page once instead of holding a
        # bearer header a browser has no way to send. Nothing else in this mode
        # moves: the bearer is still the verifier, and a cookie that is not in
        # the table is not a credential.
        token_store = AuthStore(settings.auth_db_path)
        token_store.purge_expired()
        return AuthBundle(
            mode="token",
            token_verifier=StaticTokenVerifier(settings.static_token, resource),
            # `resource_server_url=None` is the SDK's RS-off switch, and it is
            # the honest shape for this mode: with it set, the SDK serves
            # protected-resource metadata naming `issuer_url` as an
            # authorization server and stamps `resource_metadata=` into every
            # 401 — advertising an OAuth dance this mode does not host, so a
            # discovering client walked the pointer into a DCR 404 instead of
            # asking its human for the token (field report, CT 9002,
            # 2026-08-13). With it None the 401 is a plain Bearer challenge and
            # no well-known route exists: clients probe, 404, and fall back to
            # configured credentials. The SDK refuses a verifier with no
            # AuthSettings at all, which is why this is None-in-settings rather
            # than settings-less; `issuer_url` is required by the model and
            # served by nothing in this mode.
            auth_settings=AuthSettings(
                issuer_url=AnyHttpUrl(settings.issuer_url),
                resource_server_url=None,
                required_scopes=[READ_SCOPE],
            ),
            frame_signer=signer,
            store=token_store,
        )

    store = AuthStore(settings.auth_db_path)
    store.purge_expired()
    issuer = TokenIssuer(
        secret=secret,
        issuer=settings.issuer_url,
        audience=resource,
        ttl_s=settings.access_token_ttl_s,
    )
    provider = VidthequeOAuthProvider(
        settings,
        store,
        issuer,
        CIMDFetcher(allow_insecure=not settings.public_url.startswith("https://")),
    )
    auth_settings = AuthSettings(
        issuer_url=AnyHttpUrl(settings.issuer_url),
        resource_server_url=AnyHttpUrl(resource),
        required_scopes=[READ_SCOPE],
        client_registration_options=ClientRegistrationOptions(
            enabled=True,  # DCR retained: CIMD alone is not yet universal.
            valid_scopes=[READ_SCOPE, WRITE_SCOPE, OFFLINE_SCOPE],
            default_scopes=[READ_SCOPE, WRITE_SCOPE],
        ),
        revocation_options=RevocationOptions(enabled=True),
    )

    # The SDK's own /authorize, /token, /register and /revoke handlers — but
    # NOT its metadata route: build_metadata() omits
    # client_id_metadata_document_supported and never advertises "none", which
    # between them switch CIMD off for every client that checks.
    sdk_routes = [
        route
        for route in create_auth_routes(
            provider=provider,
            issuer_url=AnyHttpUrl(settings.issuer_url),
            client_registration_options=auth_settings.client_registration_options,
            revocation_options=auth_settings.revocation_options,
        )
        if route.path != "/.well-known/oauth-authorization-server"
    ]

    return AuthBundle(
        mode="oauth",
        token_verifier=JWTTokenVerifier(provider),
        auth_settings=auth_settings,
        routes=[*metadata_routes(settings), *login_routes(settings, provider), *sdk_routes],
        frame_signer=signer,
        store=store,
        provider=provider,
    )
