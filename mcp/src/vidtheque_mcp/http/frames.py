"""``GET /frames/{keyframe_id}.jpg`` — the authenticated non-MCP route.

This cannot be an ``@mcp.custom_route``: custom routes are *never*
authenticated in either framework, deliberately, because health checks and
OAuth callbacks must be reachable before any token exists. So it lives on our
own Starlette root app with our own auth.

Three credentials are accepted, in order:

1. the HMAC signature on the URL (``?exp=&sig=``) — the only thing a browser
   can present, since a rendered image fetch carries no Authorization header;
2. an ``Authorization: Bearer`` token, for programmatic clients;
3. the login-session cookie, for a human already signed in on this origin.

In ``none`` mode the route is open, because everything else is too.

``?w=`` and ``?q=`` are applied here, through the ``derived/`` cache of
index-schema §6 — they used to be accepted, bound into the signature, and then
ignored, which is how a ten-thumbnail results page came to ship 886 KB of
full-resolution JPEG for images rendered at 96x54.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Route

from ..auth.login import SESSION_COOKIE
from ..auth.modes import AuthBundle
from ..config import Settings
from ..db import Database
from ..db import queries
from .derived import (
    ALLOWED_QUALITIES,
    ALLOWED_WIDTHS,
    DEFAULT_QUALITY,
    DEFAULT_WIDTH,
    MAX_QUALITY,
    MAX_WIDTH,
    MIN_QUALITY,
    MIN_WIDTH,
    DerivedCache,
    snap,
)

# `<video_id>-<NNNNN>`: the ordinal is fixed-width, so a fabricated id is
# almost always detectably wrong.
FRAME_ID = re.compile(r"^(?P<video>[A-Za-z0-9_:-]{1,64})-(?P<ord>\d{5})$")


def parse_frame_id(frame_id: str) -> tuple[str, int] | None:
    match = FRAME_ID.match(frame_id)
    if not match:
        return None
    return match.group("video"), int(match.group("ord"))


def _clamp(value: str | None, low: int, high: int, default: int) -> int:
    try:
        return max(low, min(high, int(value))) if value is not None else default
    except (TypeError, ValueError):
        return default


def frames_routes(
    settings: Settings, db: Database, auth: AuthBundle, cache: DerivedCache | None = None
) -> list[Route]:
    cache = cache or DerivedCache(
        settings.derived_dir, settings.derived_cache_mb * 1024 * 1024
    )

    async def serve(request: Request) -> Response:
        frame_id = request.path_params["frame_id"]
        asked_width = request.query_params.get("w")
        asked_quality = request.query_params.get("q")
        # The signature covers the *effective* pair, and it covers it whether or
        # not the caller sent the parameters — so the defaults below are part of
        # the signing contract and cannot move without invalidating live URLs.
        # Snapping to the product's real width and quality sets keeps the cache
        # key space finite; the values the server itself generates are all in
        # those sets, so no URL it ever signed changes meaning (F-5).
        width = snap(_clamp(asked_width, MIN_WIDTH, MAX_WIDTH, DEFAULT_WIDTH), ALLOWED_WIDTHS)
        quality = snap(
            _clamp(asked_quality, MIN_QUALITY, MAX_QUALITY, DEFAULT_QUALITY),
            ALLOWED_QUALITIES,
        )

        credential = await _authorized(request, auth, frame_id, width, quality)
        if credential is None:
            return JSONResponse(
                {"error": "invalid_token", "error_description": "signature, bearer or session required"},
                status_code=401,
                headers={
                    "Cache-Control": "no-store",
                    "WWW-Authenticate": 'Bearer error="invalid_token", '
                    f'resource_metadata="{settings.issuer_url}'
                    '/.well-known/oauth-protected-resource/mcp", '
                    'scope="vidtheque:read"'
                },
            )

        parsed = parse_frame_id(frame_id)
        if parsed is None:
            return JSONResponse({"error": "E_UNKNOWN_FRAME"}, status_code=404)
        public_id, ordinal = parsed
        row = await db.read(lambda c: queries.keyframe_path(c, public_id, ordinal))
        if row is None:
            return JSONResponse(
                {"error": "E_UNKNOWN_FRAME", "frame_id": frame_id}, status_code=404
            )

        path = _resolve(settings.data_dir, str(row["jpeg_path"]))
        if path is None or not path.is_file():
            return JSONResponse(
                {"error": "E_UNKNOWN_FRAME", "message": "keyframe file is missing"},
                status_code=404,
            )
        # mimeType is image/jpeg and the bytes are JPEG whichever branch answers.
        # screenpipe labels ffmpeg-emitted MJPEG as image/png; a mislabelled
        # image is a client-side decode failure with no useful error.
        headers = {
            "Cache-Control": _cache_control(
                settings, credential, request.query_params.get("exp")
            )
        }

        if asked_width is None and asked_quality is None:
            # No parameters, no variant: the stored keyframe, byte for byte.
            return FileResponse(path, media_type="image/jpeg", headers=headers)

        derived = await cache.variant(
            video=public_id,
            ordinal=ordinal,
            source=path,
            width=width if asked_width is not None else None,
            quality=quality if asked_quality is not None else None,
        )
        if derived is None:
            # Nothing to serve but the original: `w` was wider than the stored
            # frame (never upscale), or the file would not decode.
            return FileResponse(path, media_type="image/jpeg", headers=headers)
        if derived.path is not None:
            # From the file either way, hit or fresh write, so a variant carries
            # the same ETag/Last-Modified whichever request happened to make it.
            return FileResponse(derived.path, media_type="image/jpeg", headers=headers)
        # Too big to cache at all: serve the bytes, store nothing.
        return Response(derived.data, media_type="image/jpeg", headers=headers)

    return [Route("/frames/{frame_id}.jpg", serve, methods=["GET"])]


def _cache_control(settings: Settings, credential: str, expires_at: str | None) -> str:
    """How long, and for whom, this JPEG may be cached.

    ``public`` only when the URL itself is the credential — an open route or a
    signed URL. Under a bearer token or a session cookie the same URL means
    different things to different callers, and a shared cache that stored the
    response would hand it to the next one; those get ``private``.

    The max-age default is a day, not the 300 s the static assets use: the bytes
    for a given ``(frame_id, w, q)`` are written once at index time and never
    edited. A day rather than a year because *re-indexing* a video reuses the
    ordinals, so the URL can outlive its pixels; a day also matches the default
    signed-URL TTL, and a signed response is capped at whatever is left of its
    own signature so a cache can never outlive the capability that fetched it.
    """
    max_age = max(0, settings.frame_cache_max_age_s)
    if credential == "signature":
        try:
            remaining = int(expires_at or 0) - int(time.time())
        except (TypeError, ValueError):  # pragma: no cover - verify() already parsed it
            remaining = 0
        max_age = max(0, min(max_age, remaining))
    if credential in ("open", "signature"):
        return f"public, max-age={max_age}"
    return f"private, max-age={max_age}"


def _resolve(data_dir: Path, relative: str) -> Path | None:
    """Refuse anything that escapes the data directory."""
    candidate = (data_dir / relative).resolve()
    root = data_dir.resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


async def _authorized(
    request: Request, auth: AuthBundle, frame_id: str, width: int, quality: int
) -> str | None:
    """Which credential let this request through — the cache headers depend on it."""
    if auth.mode == "none":
        return "open"
    signer = auth.frame_signer
    if signer is not None and signer.verify(
        frame_id,
        width,
        quality,
        request.query_params.get("exp"),
        request.query_params.get("sig"),
    ):
        return "signature"
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer ") and auth.token_verifier is not None:
        if await auth.token_verifier.verify_token(header[7:]) is not None:
            return "bearer"
    if auth.store is not None and auth.store.load_session(request.cookies.get(SESSION_COOKIE)):
        return "session"
    return None
