"""``VidthequeOAuthProvider`` — the ten methods, SQLite-backed.

vidtheque **is** its own authorization server. No Auth0, no WorkOS, no Keycloak
container in the compose file. The spec permits it ("the AS may be hosted with
the resource server"), and the alternative is not self-hostable by someone
running a Pi.

We reuse the SDK's handlers for ``/authorize``, ``/token``, ``/register`` and
``/revoke`` — battle-tested PKCE, form parsing, error shapes, RFC 9207 ``iss``
— and bypass ``auth_server_provider=`` on ``MCPServer`` so we do not inherit
its metadata defaults (see ``metadata.py``).
"""

from __future__ import annotations

import hashlib
import secrets
import time
from typing import Any
from urllib.parse import urlencode

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    RegistrationError,
    TokenError,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from ..config import OFFLINE_SCOPE, READ_SCOPE, WRITE_SCOPE, Settings
from .cimd import (
    CIMDError,
    CIMDFetcher,
    LoopbackRedirectClient,
    looks_like_cimd,
    matches_registered_redirect,
)
from .store import AuthStore
from .tokens import TokenIssuer, hash_refresh_token

OWNER_SUBJECT = "owner"
AUTH_CODE_TTL_S = 300


class VidthequeOAuthProvider(OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken]):
    """Single-owner issuer: CIMD first, DCR retained for clients that need it."""

    def __init__(
        self,
        settings: Settings,
        store: AuthStore,
        issuer: TokenIssuer,
        cimd: CIMDFetcher | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.issuer = issuer
        self.cimd = cimd or CIMDFetcher(
            allow_insecure=not settings.public_url.startswith("https://")
        )

    # ------------------------------------------------------------ clients

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        """CIMD lives here: URL client_id -> fetch -> validate -> synthesize.

        Nothing is persisted for a CIMD client. A non-URL client_id falls
        through to the DCR table.
        """
        if looks_like_cimd(client_id):
            try:
                return await self.cimd.get(client_id)
            except CIMDError:
                return None
        stored = self.store.load_client(client_id)
        if stored is None:
            return None
        # The subclass, not the SDK model: its validate_redirect_uri carries
        # the RFC 8252 loopback-port rule the handler enforces pre-authorize.
        return LoopbackRedirectClient.model_validate(stored)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        """DCR fallback, persisted. Deprecated by the spec, kept for reach."""
        if not client_info.redirect_uris:
            raise RegistrationError(
                error="invalid_redirect_uri",
                error_description="at least one redirect_uri is required",
            )
        secret_hash = (
            hashlib.sha256(client_info.client_secret.encode()).hexdigest()
            if client_info.client_secret
            else None
        )
        # The hash is the stored credential, so the plaintext beside it was a
        # second copy with no job — `docker inspect` on auth.db, a backup, or a
        # stray read all handed out a working client secret that the hash exists
        # precisely to avoid storing. (2026-08-10 audit, auth hardening.)
        metadata = client_info.model_dump(mode="json", exclude_none=True)
        metadata.pop("client_secret", None)
        self.store.save_client(client_info.client_id, metadata, secret_hash)

    # ---------------------------------------------------------- authorize

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        """Stash the request and send the browser to our login/consent page.

        The SDK's handler redirects the user agent wherever this returns; we
        return an internal URL so the owner can authenticate before a code is
        minted.
        """
        registered = [str(u) for u in (client.redirect_uris or [])]
        if registered and not matches_registered_redirect(registered, str(params.redirect_uri)):
            raise TokenError(  # pragma: no cover - the handler pre-validates
                error="invalid_request", error_description="redirect_uri is not registered"
            )
        key = secrets.token_urlsafe(24)
        self.store.save_pending(
            key,
            {
                "client_id": client.client_id,
                "redirect_uri": str(params.redirect_uri),
                "redirect_explicit": params.redirect_uri_provided_explicitly,
                "code_challenge": params.code_challenge,
                "scopes": params.scopes or [READ_SCOPE, WRITE_SCOPE],
                "state": params.state,
                "resource": params.resource,
            },
            ttl_s=AUTH_CODE_TTL_S,
        )
        return f"{self.settings.issuer_url}/auth/login?{urlencode({'rq': key})}"

    def complete_authorization(self, pending: dict[str, Any]) -> str:
        """Mint the code and build the redirect back to the client.

        RFC 9207: the authorization response carries ``iss`` so the client can
        detect a mix-up attack, and the metadata advertises that we send it.
        """
        code = secrets.token_urlsafe(32)
        # `offline_access` is kept on the code and stripped when the access
        # token is minted: it is a request for a refresh token, not a scope the
        # resource server ever sees.
        self.store.save_code(
            code,
            {
                "client_id": pending["client_id"],
                "redirect_uri": pending["redirect_uri"],
                "redirect_explicit": 1 if pending.get("redirect_explicit", True) else 0,
                "code_challenge": pending["code_challenge"],
                "scopes": " ".join(pending["scopes"]),
                "resource": pending.get("resource"),
                "subject": OWNER_SUBJECT,
                "expires_at": int(time.time()) + AUTH_CODE_TTL_S,
            },
        )
        query = {"code": code, "iss": self.settings.issuer_url}
        if pending.get("state"):
            query["state"] = pending["state"]
        separator = "&" if "?" in pending["redirect_uri"] else "?"
        return f"{pending['redirect_uri']}{separator}{urlencode(query)}"

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        row = self.store.peek_code(authorization_code)
        if row is None or row["client_id"] != client.client_id:
            return None
        return AuthorizationCode(
            code=authorization_code,
            scopes=str(row["scopes"]).split(),
            expires_at=float(row["expires_at"]),
            client_id=str(row["client_id"]),
            code_challenge=str(row["code_challenge"]),
            redirect_uri=row["redirect_uri"],  # type: ignore[arg-type]
            redirect_uri_provided_explicitly=bool(row["redirect_explicit"]),
            resource=row["resource"],
            subject=str(row["subject"]),
        )

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        row = self.store.take_code(authorization_code.code)  # single use
        if row is None:
            raise TokenError(error="invalid_grant", error_description="authorization code expired")
        return self._issue(
            client_id=client.client_id,
            subject=authorization_code.subject or OWNER_SUBJECT,
            scopes=authorization_code.scopes,
            resource=authorization_code.resource,
        )

    # ---------------------------------------------------------- refreshing

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        row = self.store.load_refresh(hash_refresh_token(self.issuer.secret, refresh_token))
        if row is None or row["client_id"] != client.client_id:
            return None
        return RefreshToken(
            token=refresh_token,
            client_id=str(row["client_id"]),
            scopes=str(row["scopes"]).split(),
            expires_at=int(row["expires_at"]) if row["expires_at"] is not None else None,
            subject=str(row["subject"]),
        )

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        old_hash = hash_refresh_token(self.issuer.secret, refresh_token.token)
        row = self.store.load_refresh(old_hash)
        if row is None:
            # `invalid_grant`, not a custom code: Claude keys its re-auth on it.
            raise TokenError(error="invalid_grant", error_description="refresh token is not valid")
        granted = scopes or refresh_token.scopes
        if not set(granted).issubset(set(refresh_token.scopes)):
            raise TokenError(error="invalid_scope", error_description="scope escalation refused")
        token = self._issue(
            client_id=client.client_id,
            subject=refresh_token.subject or OWNER_SUBJECT,
            scopes=granted,
            resource=row["resource"],
        )
        assert token.refresh_token is not None
        self.store.rotate_refresh(old_hash, hash_refresh_token(self.issuer.secret, token.refresh_token))
        return token

    # -------------------------------------------------------- token issue

    def _issue(
        self, client_id: str, subject: str, scopes: list[str], resource: str | None
    ) -> OAuthToken:
        granted = [s for s in scopes if s != OFFLINE_SCOPE]
        if not granted:
            granted = [READ_SCOPE]
        access, expires_at = self.issuer.issue(
            subject=subject,
            client_id=client_id,
            scopes=granted,
            resource=resource or self.settings.resource_url,
        )
        refresh = secrets.token_urlsafe(40)
        self.store.save_refresh(
            hash_refresh_token(self.issuer.secret, refresh),
            client_id,
            subject,
            granted,
            resource,
            int(time.time()) + self.settings.refresh_token_ttl_s,
        )
        return OAuthToken(
            access_token=access,
            token_type="Bearer",
            expires_in=max(1, expires_at - int(time.time())),
            scope=" ".join(granted),
            refresh_token=refresh,
        )

    # ------------------------------------------------------- verification

    async def load_access_token(self, token: str) -> AccessToken | None:
        claims = self.issuer.verify(token)
        if claims is None:
            return None
        return AccessToken(
            token=token,
            client_id=str(claims.get("client_id", "")),
            scopes=str(claims.get("scope", "")).split(),
            expires_at=int(claims["exp"]),  # type: ignore[arg-type]
            resource=str(claims.get("aud")) if claims.get("aud") else None,
            subject=str(claims.get("sub")) if claims.get("sub") else None,
            claims={"iss": claims.get("iss")},
        )

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        """Revoke both sides where we can.

        Access tokens are stateless JWTs, so an individual one cannot be
        withdrawn before it expires; revoking the client's refresh tokens is
        what actually ends the session, and the short access TTL closes the gap.
        """
        if isinstance(token, RefreshToken):
            self.store.revoke_refresh(hash_refresh_token(self.issuer.secret, token.token))
        else:
            self.store.revoke_client_refresh(token.client_id)
