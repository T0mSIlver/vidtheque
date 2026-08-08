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
"""

from __future__ import annotations

import re
from pathlib import Path

from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Route

from ..auth.login import SESSION_COOKIE
from ..auth.modes import AuthBundle
from ..config import Settings
from ..db import Database
from ..db import queries

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


def frames_routes(settings: Settings, db: Database, auth: AuthBundle) -> list[Route]:
    async def serve(request: Request) -> Response:
        frame_id = request.path_params["frame_id"]
        width = _clamp(request.query_params.get("w"), 128, 1280, 512)
        quality = _clamp(request.query_params.get("q"), 20, 95, 75)

        if not await _authorized(request, auth, frame_id, width, quality):
            return JSONResponse(
                {"error": "invalid_token", "error_description": "signature, bearer or session required"},
                status_code=401,
                headers={
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
        # mimeType is image/jpeg and the bytes are JPEG. screenpipe labels
        # ffmpeg-emitted MJPEG as image/png; a mislabelled image is a
        # client-side decode failure with no useful error.
        return FileResponse(
            path,
            media_type="image/jpeg",
            headers={"Cache-Control": "private, max-age=1800"},
        )

    return [Route("/frames/{frame_id}.jpg", serve, methods=["GET"])]


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
) -> bool:
    if auth.mode == "none":
        return True
    signer = auth.frame_signer
    if signer is not None and signer.verify(
        frame_id,
        width,
        quality,
        request.query_params.get("exp"),
        request.query_params.get("sig"),
    ):
        return True
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer ") and auth.token_verifier is not None:
        if await auth.token_verifier.verify_token(header[7:]) is not None:
            return True
    if auth.store is not None and auth.store.load_session(request.cookies.get(SESSION_COOKIE)):
        return True
    return False
