"""Client ID Metadata Documents (CIMD).

The 2026-07-28 spec deprecates DCR in favour of CIMD: the ``client_id`` *is* an
https URL, the document at that URL is the client's metadata, and nothing is
persisted server-side. Claude selects CIMD only when the AS metadata advertises
**both** ``client_id_metadata_document_supported: true`` and ``"none"`` in
``token_endpoint_auth_methods_supported`` — see ``metadata.py``.

Validation rules implemented here:

* the document must be **self-referential** (``doc.client_id == fetched URL``);
* ``token_endpoint_auth_method`` must be ``none`` or ``private_key_jwt``;
* ``redirect_uris`` must be same-origin with the ``client_id`` URL — **except**
  loopback, where the port component is ignored for ``127.0.0.1``, ``[::1]``
  *and* ``localhost``. Claude Code declares ``http://localhost/callback`` and
  ``http://127.0.0.1/callback`` and then listens on an ephemeral port (RFC
  8252), so an exact-match check locks it out.
* SSRF guard: https only, no credentials, no private/loopback/link-local host.
"""

from __future__ import annotations

import ipaddress
import socket
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import httpx2 as httpx

from mcp.shared.auth import OAuthClientInformationFull

LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]"}
MAX_DOCUMENT_BYTES = 64 * 1024
CACHE_TTL_S = 300


class CIMDError(ValueError):
    """The document is missing, malformed, or does not authorise the request."""


def looks_like_cimd(client_id: str) -> bool:
    return client_id.startswith("https://") or client_id.startswith("http://localhost")


def _resolves_to_private(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return True  # unresolvable: refuse rather than retry blindly
    for info in infos:
        address = info[4][0]
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:  # pragma: no cover - defensive
            return True
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            return True
    return False


def guard_ssrf(url: str, *, allow_insecure: bool = False) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" and not (allow_insecure and parsed.scheme == "http"):
        raise CIMDError("client_id metadata documents must be served over https")
    if parsed.username or parsed.password:
        raise CIMDError("client_id URL must not carry credentials")
    if parsed.fragment:
        raise CIMDError("client_id URL must not carry a fragment")
    host = parsed.hostname or ""
    if not host:
        raise CIMDError("client_id URL has no host")
    if allow_insecure:
        return
    if host in LOOPBACK_HOSTS or _resolves_to_private(host):
        raise CIMDError("client_id URL must not resolve to a private address")


def _origin(url: str) -> tuple[str, str, int | None]:
    parsed = urlparse(url)
    return parsed.scheme, (parsed.hostname or "").lower(), parsed.port


def _is_loopback_redirect(url: str) -> bool:
    scheme, host, _ = _origin(url)
    return scheme in {"http", "https"} and host in {h.strip("[]") for h in LOOPBACK_HOSTS}


def validate_redirect_uris(client_id: str, redirect_uris: list[str]) -> list[str]:
    """Same-origin with the client_id URL; loopback exempt, port ignored."""
    if not redirect_uris:
        raise CIMDError("the client metadata document declares no redirect_uris")
    want_scheme, want_host, want_port = _origin(client_id)
    for uri in redirect_uris:
        if _is_loopback_redirect(uri):
            continue
        scheme, host, port = _origin(uri)
        if (scheme, host, port) != (want_scheme, want_host, want_port):
            raise CIMDError(
                f"redirect_uri {uri!r} is not same-origin with the client_id URL"
            )
    return redirect_uris


def matches_registered_redirect(registered: list[str], candidate: str) -> bool:
    """Redirect comparison used at /authorize: loopback ignores the port."""
    for uri in registered:
        if uri == candidate:
            return True
        if _is_loopback_redirect(uri) and _is_loopback_redirect(candidate):
            reg = urlparse(uri)
            got = urlparse(candidate)
            if reg.path == got.path:
                return True
    return False


def synthesize(client_id: str, document: dict[str, Any]) -> OAuthClientInformationFull:
    """Turn a validated document into an in-memory client record."""
    if document.get("client_id") != client_id:
        raise CIMDError(
            "the client metadata document is not self-referential "
            "(its client_id does not equal the URL it was fetched from)"
        )
    method = document.get("token_endpoint_auth_method", "none")
    if method not in {"none", "private_key_jwt"}:
        raise CIMDError(f"unsupported token_endpoint_auth_method {method!r} for a CIMD client")
    redirect_uris = validate_redirect_uris(client_id, list(document.get("redirect_uris") or []))
    return OAuthClientInformationFull(
        client_id=client_id,
        client_secret=None,
        redirect_uris=redirect_uris,  # type: ignore[arg-type]
        token_endpoint_auth_method=method,
        grant_types=list(document.get("grant_types") or ["authorization_code", "refresh_token"]),
        response_types=list(document.get("response_types") or ["code"]),
        scope=document.get("scope"),
        client_name=document.get("client_name"),
        client_uri=document.get("client_uri"),
        logo_uri=document.get("logo_uri"),
        jwks_uri=document.get("jwks_uri"),
        jwks=document.get("jwks"),
    )


def display_host(client_id: str) -> str:
    """What the consent screen shows.

    The **host of the client_id URL**, never `client_name`: the document is
    self-asserted, so its display name is whatever the client wanted to claim.
    """
    return urlparse(client_id).hostname or client_id


@dataclass
class CIMDFetcher:
    """Fetch + validate + cache. Nothing is written to the database."""

    allow_insecure: bool = False
    timeout_s: float = 8.0
    client: httpx.AsyncClient | None = None
    _cache: dict[str, tuple[float, OAuthClientInformationFull]] = field(default_factory=dict)

    async def get(self, client_id: str) -> OAuthClientInformationFull:
        cached = self._cache.get(client_id)
        if cached and cached[0] > time.monotonic():
            return cached[1]
        guard_ssrf(client_id, allow_insecure=self.allow_insecure)
        document = await self._fetch(client_id)
        info = synthesize(client_id, document)
        self._cache[client_id] = (time.monotonic() + CACHE_TTL_S, info)
        return info

    async def _fetch(self, client_id: str) -> dict[str, Any]:
        client = self.client or httpx.AsyncClient(timeout=self.timeout_s, follow_redirects=False)
        owned = self.client is None
        try:
            response = await client.get(client_id, headers={"Accept": "application/json"})
            if response.status_code != 200:
                raise CIMDError(
                    f"client metadata document returned HTTP {response.status_code}"
                )
            if len(response.content) > MAX_DOCUMENT_BYTES:
                raise CIMDError("client metadata document is too large")
            document = response.json()
        except CIMDError:
            raise
        except Exception as exc:
            raise CIMDError(f"could not fetch the client metadata document: {exc}") from exc
        finally:
            if owned:
                await client.aclose()
        if not isinstance(document, dict):
            raise CIMDError("client metadata document is not a JSON object")
        return document
