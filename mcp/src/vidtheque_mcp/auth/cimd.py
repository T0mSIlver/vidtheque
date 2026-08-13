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
import json
import socket
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

import httpx2 as httpx

from pydantic import AnyUrl

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
        if _is_special_use(ip):
            return True
    return False


# Ranges Python's own predicates do not cover, and which are still not the
# public internet. 100.64.0.0/10 is carrier-grade NAT — routable-looking,
# reachable, and on a home connection frequently the ISP's own equipment.
# RFC 6890. (2026-08-10 audit, auth hardening.)
_EXTRA_SPECIAL_USE = (
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("192.0.0.0/24"),
    ipaddress.ip_network("198.18.0.0/15"),
    ipaddress.ip_network("64:ff9b::/96"),
)


def _is_special_use(ip: "ipaddress.IPv4Address | ipaddress.IPv6Address") -> bool:
    if (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    ):
        return True
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None and _is_special_use(mapped):
        return True
    return any(ip in network for network in _EXTRA_SPECIAL_USE if ip.version == network.version)


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


class LoopbackRedirectClient(OAuthClientInformationFull):
    """The SDK's client model, with RFC 8252 §7.3 loopback semantics.

    The SDK's ``validate_redirect_uri`` is exact membership, and its handler
    runs it BEFORE the provider's ``authorize`` — so the loopback rule
    :func:`matches_registered_redirect` implements was dead code for the case
    it exists for: a native client (Claude Code, MCP Inspector) registers
    ``http://localhost:<the port it held that day>/callback``, binds a fresh
    random port at the next sign-in, and the handler refuses the mismatch
    before this module is consulted (field report, CT 9002, 2026-08-13). The
    RFC is explicit that the server MUST allow any port at request time for
    loopback redirects. Every client this provider materializes — stored DCR
    rows and synthesized CIMD documents both — is this subclass, so the
    handler's own validation applies the rule; non-loopback URIs keep the
    SDK's exact matching.
    """

    def validate_redirect_uri(self, redirect_uri: AnyUrl | None) -> AnyUrl:
        if redirect_uri is not None and matches_registered_redirect(
            [str(u) for u in (self.redirect_uris or [])], str(redirect_uri)
        ):
            return redirect_uri
        return super().validate_redirect_uri(redirect_uri)


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
    return LoopbackRedirectClient(
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
            # Streamed, and stopped at the cap. `response.content` buffered and
            # decompressed the *whole* body before the length was looked at, so
            # the limit described what we would accept and not what we would
            # read — a compression bomb landed in memory first and was rejected
            # afterwards. (2026-08-10 audit, auth hardening.)
            async with client.stream(
                "GET", client_id, headers={"Accept": "application/json"}
            ) as response:
                if response.status_code != 200:
                    raise CIMDError(
                        f"client metadata document returned HTTP {response.status_code}"
                    )
                chunks: list[bytes] = []
                total = 0
                async for chunk in response.aiter_bytes():
                    total += len(chunk)
                    if total > MAX_DOCUMENT_BYTES:
                        raise CIMDError("client metadata document is too large")
                    chunks.append(chunk)
            document = json.loads(b"".join(chunks))
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
