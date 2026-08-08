"""HS256 access tokens and HMAC-signed frame URLs.

Access tokens are **stateless JWTs**: no access-token table, no DB read on the
hot path, and the token verifier is a signature + `aud` + `exp` check.

Frame URLs are a different secret in a different place, so a leaked image link
is not an access token: the signing key is derived from ``VIDTHEQUE_SECRET``
with a distinct salt.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from base64 import urlsafe_b64encode
from dataclasses import dataclass

import jwt

ALGORITHM = "HS256"
_ACCESS_SALT = b"vidtheque/access-token/v1"
_FRAME_SALT = b"vidtheque/frame-url/v1"
_REFRESH_SALT = b"vidtheque/refresh-token/v1"


def derive_key(secret: str, salt: bytes) -> bytes:
    """One configured secret, several independent keys."""
    return hmac.new(salt, secret.encode("utf-8"), hashlib.sha256).digest()


def hash_refresh_token(secret: str, token: str) -> str:
    """Refresh tokens are stored by hash only — never in the clear."""
    return hmac.new(derive_key(secret, _REFRESH_SALT), token.encode("utf-8"), hashlib.sha256).hexdigest()


@dataclass(frozen=True)
class TokenIssuer:
    """Issues and verifies the server's own access tokens."""

    secret: str
    issuer: str
    audience: str
    ttl_s: int = 3600

    @property
    def _key(self) -> bytes:
        return derive_key(self.secret, _ACCESS_SALT)

    def issue(
        self,
        subject: str,
        client_id: str,
        scopes: list[str],
        resource: str | None = None,
        ttl_s: int | None = None,
    ) -> tuple[str, int]:
        """Return (jwt, expires_at)."""
        now = int(time.time())
        expires_at = now + (ttl_s if ttl_s is not None else self.ttl_s)
        claims = {
            "iss": self.issuer,
            # RFC 8707: the token was issued *for us*, and the verifier checks it.
            "aud": resource or self.audience,
            "sub": subject,
            "client_id": client_id,
            "scope": " ".join(scopes),
            "iat": now,
            "exp": expires_at,
            "jti": secrets.token_hex(12),
        }
        return jwt.encode(claims, self._key, algorithm=ALGORITHM), expires_at

    def verify(self, token: str) -> dict[str, object] | None:
        try:
            return jwt.decode(
                token,
                self._key,
                algorithms=[ALGORITHM],
                audience=self.audience,
                issuer=self.issuer,
                options={"require": ["exp", "sub", "aud", "iss"]},
            )
        except jwt.PyJWTError:
            return None


@dataclass(frozen=True)
class FrameUrlSigner:
    """Capability URLs for ``/frames/<frame_id>.jpg``.

    A browser has no Authorization header: when the human clicks the link in a
    tool result, nothing attaches the OAuth bearer. Signed URLs are the only
    thing a browser-side renderer can actually fetch — so keep the TTL tight,
    sign the *whole* request (id, width, quality, expiry), and compare in
    constant time.
    """

    secret: str
    ttl_s: int = 86_400

    @property
    def _key(self) -> bytes:
        return derive_key(self.secret, _FRAME_SALT)

    def _mac(self, frame_id: str, width: int, quality: int, expires_at: int) -> str:
        message = f"{frame_id}\n{width}\n{quality}\n{expires_at}".encode("utf-8")
        digest = hmac.new(self._key, message, hashlib.sha256).digest()[:18]
        return urlsafe_b64encode(digest).decode("ascii").rstrip("=")

    def sign(
        self, frame_id: str, width: int, quality: int, now: int | None = None
    ) -> tuple[int, str]:
        expires_at = (now if now is not None else int(time.time())) + self.ttl_s
        return expires_at, self._mac(frame_id, width, quality, expires_at)

    def url(self, base_url: str, frame_id: str, width: int, quality: int) -> tuple[str, int]:
        expires_at, signature = self.sign(frame_id, width, quality)
        url = (
            f"{base_url.rstrip('/')}/frames/{frame_id}.jpg"
            f"?w={width}&q={quality}&exp={expires_at}&sig={signature}"
        )
        return url, expires_at

    def verify(
        self,
        frame_id: str,
        width: int,
        quality: int,
        expires_at: int | str | None,
        signature: str | None,
        now: int | None = None,
    ) -> bool:
        if not signature or expires_at is None:
            return False
        try:
            expiry = int(expires_at)
        except (TypeError, ValueError):
            return False
        if expiry < (now if now is not None else int(time.time())):
            return False
        return hmac.compare_digest(self._mac(frame_id, width, quality, expiry), signature)
