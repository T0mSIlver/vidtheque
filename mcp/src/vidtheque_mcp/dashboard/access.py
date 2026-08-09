"""Who may read the dashboard, and who may write to it — dashboard.md §3.

**Reuse, don't mint.** There is exactly one new idea here and it is a *refusal*:
in ``VIDTHEQUE_AUTH=none`` the write side is never registered. Everything else
is the credential check ``http/frames.py:_authorized`` already does, minus the
signed URL (a page has no signature to carry), applied to a route group instead
of to one route:

1. ``none`` mode — open, because everything else in that mode is too;
2. ``Authorization: Bearer <VIDTHEQUE_TOKEN>``, through the existing
   ``StaticTokenVerifier`` — the curl/script path;
3. the existing ``vidtheque_session`` cookie, from the owner login the OAuth
   flow already mints (``auth/login.py``). No new credential, no new table.

Two rules fall out of using a cookie, and both are cheap now and expensive
later (§3.3):

* **No state-changing GET, ever.** ``SameSite=Lax`` sends the session cookie on
  a top-level GET navigation, so ``<img src=".../videos/X/delete">`` in any page
  the owner opens would fire. Every write is a POST with a JSON body.
* **Origin check on every write.** Belt-and-braces behind SameSite: the handler
  requires ``Origin`` (or ``Sec-Fetch-Site: same-origin``) to agree with
  ``PUBLIC_URL``'s origin, which the config already builds.

Phase 1 registers no write routes — ``WRITE_ROUTES`` is empty and the guard is
here, tested, waiting for phase 3. It is written now rather than then because
the two rules above are the kind that get retrofitted badly.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from ..auth.login import SESSION_COOKIE
from ..auth.modes import AuthBundle

# One list, declared once, the way `public/readonly.py` declares the masked
# tools (dashboard.md §2.5.4). Empty in phase 1: the dashboard is read-only.
WRITE_ROUTES: tuple[str, ...] = ()


async def credential(request: Request) -> str | None:
    """Which credential let this request through, or ``None``.

    Modelled on ``http/frames.py:_authorized``. The name is returned rather
    than a bool because the write guard treats ``open`` differently from the
    other two: an unauthenticated instance is exactly the one that must not
    have a live "index this URL" button.
    """
    auth: AuthBundle = request.app.state.assembled.auth
    if auth.mode == "none":
        return "open"
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer ") and auth.token_verifier is not None:
        if await auth.token_verifier.verify_token(header[7:]) is not None:
            return "bearer"
    if auth.store is not None and auth.store.load_session(request.cookies.get(SESSION_COOKIE)):
        return "session"
    return None


def sign_in_hint(mode: str) -> str:
    """How this deployment expects a human to authenticate, in one sentence."""
    if mode == "oauth":
        return "Sign in at /auth/login, or send Authorization: Bearer <token>."
    return "Send Authorization: Bearer $VIDTHEQUE_TOKEN."


def origin_ok(request: Request) -> bool:
    """Does this write come from a page this server served?

    ``Sec-Fetch-Site: same-origin`` is the browser's own answer and is not
    forgeable from script. ``Origin`` is the fallback for clients that do not
    send fetch metadata; it is compared against ``PUBLIC_URL``'s origin, which
    the OAuth ``resource`` already depends on. A request with **neither** is a
    non-browser client (curl, a script) and is allowed through — it carries a
    bearer token or it never got here, and CSRF is not a thing that happens to
    a caller with no ambient credential.
    """
    fetch_site = request.headers.get("sec-fetch-site")
    if fetch_site is not None:
        return fetch_site in ("same-origin", "none")
    origin = request.headers.get("origin")
    if origin is None:
        return True
    settings = request.app.state.assembled.settings
    want = urlsplit(settings.public_url)
    got = urlsplit(origin)
    return (got.scheme, got.hostname, got.port) == (want.scheme, want.hostname, want.port)


async def require_write(request: Request) -> Response | None:
    """``None`` when the write may proceed, else the refusal to return.

    Order matters: the mode refusal is about the *deployment* and says how to
    fix it, so it must not be masked by a credential check that can never pass
    in that mode.
    """
    auth: AuthBundle = request.app.state.assembled.auth
    if auth.mode == "none":
        return JSONResponse(
            {
                "error": "E_AUTH_REQUIRED",
                "message": (
                    "This instance runs with VIDTHEQUE_AUTH=none, so the dashboard "
                    "serves the read-only views only."
                ),
                "next": "set VIDTHEQUE_AUTH=token and VIDTHEQUE_TOKEN, then restart.",
            },
            status_code=403,
        )
    if request.method == "GET":  # pragma: no cover - no such route can exist
        raise AssertionError("no state-changing GET, ever (dashboard.md §3.3)")
    if await credential(request) is None:
        return JSONResponse(
            {
                "error": "E_AUTH_REQUIRED",
                "message": "This action needs the owner's token or session.",
                "next": sign_in_hint(auth.mode),
            },
            status_code=401,
        )
    if not origin_ok(request):
        return JSONResponse(
            {
                "error": "E_BAD_ORIGIN",
                "message": "That request came from another origin.",
                "next": "use the dashboard on this server's own PUBLIC_URL.",
            },
            status_code=403,
        )
    return None
