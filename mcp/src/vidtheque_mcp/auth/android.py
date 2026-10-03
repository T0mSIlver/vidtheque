"""The Android app as an OAuth client of its own instance (companion.md §5).

The app is a public client with PKCE whose ``client_id`` is a CIMD URL on this
instance. CIMD requires the redirect to be same-origin with that URL, so the
redirect is an https App Link here too, and Android only hands it to the app
once ``/.well-known/assetlinks.json`` names the app's signing key. All three
documents exist only when ``VIDTHEQUE_ANDROID_CERT_SHA256`` is set.

The provider answers the app's ``client_id`` from :func:`client_document`
directly rather than fetching its own public URL: the fetch would leave the box
through the tunnel and come back, and the SSRF guard refuses a loopback hop.
"""

from __future__ import annotations

import re
from typing import Any

from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route

from ..config import OFFLINE_SCOPE, READ_SCOPE, WRITE_SCOPE, ConfigError, Settings

PACKAGE = "dev.vidtheque.app"
# Under /auth/ because the deploy proxies already send that prefix to Python
# (deploy/Caddyfile, web/src/proxy.ts); a new prefix would reach Next and 404.
CLIENT_PATH = "/auth/android/client.json"
CALLBACK_PATH = "/auth/android/callback"

_FINGERPRINT = re.compile(r"^[0-9A-F]{2}(:[0-9A-F]{2}){31}$")


def parse_fingerprints(raw: str | None) -> tuple[str, ...]:
    """Comma-separated SHA-256 signing-cert fingerprints, as keytool prints them."""
    prints = tuple(p.strip().upper() for p in (raw or "").split(",") if p.strip())
    for p in prints:
        if not _FINGERPRINT.match(p):
            raise ConfigError(
                f"VIDTHEQUE_ANDROID_CERT_SHA256 entry {p!r} is not a SHA-256 "
                "fingerprint (32 colon-separated hex bytes, as `keytool -list -v` prints it)"
            )
    return prints


def enabled(settings: Settings) -> bool:
    return settings.auth_mode == "oauth" and bool(settings.android_cert_sha256)


def client_id(settings: Settings) -> str:
    return f"{settings.issuer_url}{CLIENT_PATH}"


def client_document(settings: Settings) -> dict[str, Any]:
    return {
        "client_id": client_id(settings),
        "client_name": "vidtheque for Android",
        "redirect_uris": [f"{settings.issuer_url}{CALLBACK_PATH}"],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
        "scope": " ".join((READ_SCOPE, WRITE_SCOPE, OFFLINE_SCOPE)),
    }


def asset_links(settings: Settings) -> list[dict[str, Any]]:
    return [
        {
            "relation": ["delegate_permission/common.handle_all_urls"],
            "target": {
                "namespace": "android_app",
                "package_name": PACKAGE,
                "sha256_cert_fingerprints": list(settings.android_cert_sha256),
            },
        }
    ]


# Shown only when the App Link did not open the app: an unverified link, or a
# browser without Auth Tab. The code in the URL is single-use and PKCE-bound,
# so a page that leaves it there gives nothing away.
_CALLBACK_PAGE = """<!doctype html><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>vidtheque</title>
<body style="background:#040405;color:#f3f0ea;font:16px system-ui;padding:24px">
<p>The vidtheque app did not pick up this sign-in.</p>
<p style="color:#9d968c">Open the app and sign in again. If this page comes back,
the instance's assetlinks.json does not list the app's signing key
(VIDTHEQUE_ANDROID_CERT_SHA256).</p>
</body>"""


def android_routes(settings: Settings) -> list[Route]:
    if not enabled(settings):
        return []

    async def client(_: Request) -> JSONResponse:
        return JSONResponse(client_document(settings), headers={"Cache-Control": "public, max-age=300"})

    async def links(_: Request) -> JSONResponse:
        return JSONResponse(asset_links(settings), headers={"Cache-Control": "public, max-age=300"})

    async def callback(_: Request) -> HTMLResponse:
        return HTMLResponse(_CALLBACK_PAGE, headers={"Cache-Control": "no-store"})

    return [
        Route(CLIENT_PATH, client, methods=["GET"]),
        Route("/.well-known/assetlinks.json", links, methods=["GET"]),
        Route(CALLBACK_PATH, callback, methods=["GET"]),
    ]
