"""Who may read the dashboard, and who may write to it — dashboard.md §3.

**Reuse, don't mint.** There is exactly one new idea here and it is a *refusal*:
in ``VIDTHEQUE_AUTH=none`` the write side is never registered. Everything else
is the credential check ``http/frames.py:_authorized`` already does, minus the
signed URL (a page has no signature to carry), applied to a route group instead
of to one route:

1. ``none`` mode — open for reading, because everything else in that mode is
   too, and closed for writing, because it is the mode with no credential to
   check;
2. ``Authorization: Bearer <VIDTHEQUE_TOKEN>``, through the existing
   ``StaticTokenVerifier`` — the curl/script path;
3. the existing ``vidtheque_session`` cookie, in the existing ``login_sessions``
   table. Phase 3 adds a second page that mints one (``writes.login``) and no
   second session system: same cookie name, same row, same TTL.

That check — :func:`~vidtheque_mcp.auth.credential.credential` — **moved to**
``auth/credential.py`` in phase 5, when `public/api.py` started keying its
clamp policy off the credential rather than off the route group (§2.5.1's
amendment). It is re-exported here so nothing in this package changed, and it
is in ``auth/`` because a route group is the wrong owner of an answer two route
groups need.

Two rules fall out of using a cookie, and both are cheap now and expensive
later (§3.3):

* **No state-changing GET, ever.** ``SameSite=Lax`` sends the session cookie on
  a top-level GET navigation, so ``<img src=".../videos/X/delete">`` in any page
  the owner opens would fire. Every write is a POST.
* **Origin check on every write.** Behind SameSite, and *ahead* of it when the
  credential is the cookie: see :func:`origin_evidence`.

**What is registered, and when.** ``WRITE_ROUTES`` is the declared list, the way
``public/readonly.py`` declares the masked tools (§2.5.4), and
:func:`write_side_enabled` is the one predicate that decides whether any of them
reach the router. Two deployments never see them at all:

* ``VIDTHEQUE_PUBLIC_READONLY=1`` (§2.3) — the demo projection. A route that
  exists and refuses is a route somebody probes, and a button that 403s is worse
  UI than a button that is not there.
* ``VIDTHEQUE_AUTH=none`` (§3.2 rule 3) — an unauthenticated instance behind a
  tunnel with a live "index this URL" button is remote-yt-dlp-as-a-service
  pointed at the operator's residential IP. **The login page is part of the
  write side** for the same reason and by the same predicate: a sign-in that
  grants nothing is a probe magnet with a password field on it.

:func:`require_write` still refuses in ``none`` mode even though no route can
reach it there. That is not dead code, it is the belt behind the braces — the
registration decision is made once, far from the handler, and a handler that
trusted it would be one refactor away from being wrong.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from ..auth.credential import credential
from ..auth.modes import AuthBundle
from .settings import ROOT

# Re-exported, not redefined. `credential()` lived here until `public/api.py`
# needed the same answer to pick a clamp policy (phase 5); it now lives in
# `auth/`, which imports neither route group. Every caller in this package —
# and the `dashboard.credential` name `__init__` re-exports — is unchanged.
__all__ = [
    "WRITE_ROUTES",
    "auth_required",
    "credential",
    "origin_evidence",
    "origin_ok",
    "require_write",
    "sign_in_hint",
    "write_side_enabled",
]

# One list, declared once (§2.5.4). Every path here is POST-only, every one is
# behind `require_write`, and a test asserts both — plus that this list is
# exactly the set of non-GET routes the group registers, so a tenth write route
# that forgets to declare itself fails the suite rather than shipping unguarded.
WRITE_ROUTES: tuple[str, ...] = (
    f"{ROOT}/login",
    f"{ROOT}/logout",
    f"{ROOT}/index",
    f"{ROOT}/jobs/{{job_id}}/cancel",
    f"{ROOT}/jobs/{{job_id}}/retry",
    f"{ROOT}/videos/{{video_id}}/reindex",
    f"{ROOT}/videos/{{video_id}}/tags",
)


def write_side_enabled(auth_mode: str, readonly: bool) -> bool:
    """Does this deployment register a write side at all? (§2.3, §3.2 rule 3.)"""
    return not readonly and auth_mode != "none"


def sign_in_hint(mode: str, *, login: bool = True) -> str:
    """How this deployment expects a human to authenticate, in one sentence.

    ``login`` is whether the sign-in page is registered here. It is not always:
    a read-only deployment with a credential (``VIDTHEQUE_PUBLIC_READONLY=1``
    plus `token`) still gates its read pages and still has no write side, so
    pointing a refused reader at a page that 404s would be the refusal telling
    them a second untruth.
    """
    header = (
        "Authorization: Bearer <token>"
        if mode == "oauth"
        else "Authorization: Bearer $VIDTHEQUE_TOKEN"
    )
    if login:
        return f"Sign in at {ROOT}/login, or send {header}."
    return f"Send {header}."


def origin_evidence(request: Request) -> str:
    """``same``, ``cross`` or ``absent`` — did a browser vouch for this write?

    ``Sec-Fetch-Site`` is the browser's own answer and is not forgeable from
    script, so it outranks a header the page could have chosen for itself.
    ``Origin`` is the fallback for clients that do not send fetch metadata, and
    is compared against ``PUBLIC_URL``'s origin, which the OAuth ``resource``
    already depends on.

    ``absent`` is a third answer rather than a failure because it is what a
    non-browser client looks like: curl and a script send neither header, carry
    a bearer token rather than an ambient cookie, and CSRF is not a thing that
    happens to a caller with no ambient credential. :func:`require_write` is
    where that distinction is spent.
    """
    fetch_site = request.headers.get("sec-fetch-site")
    if fetch_site is not None:
        return "same" if fetch_site in ("same-origin", "none") else "cross"
    origin = request.headers.get("origin")
    if origin is None:
        return "absent"
    settings = request.app.state.assembled.settings
    want = urlsplit(settings.public_url)
    got = urlsplit(origin)
    same = (got.scheme, got.hostname, got.port) == (want.scheme, want.hostname, want.port)
    return "same" if same else "cross"


def origin_ok(request: Request) -> bool:
    """Does this write come from a page this server served?

    The lenient reading of :func:`origin_evidence`, for a caller whose
    credential is *not* ambient. ``absent`` passes here.
    """
    return origin_evidence(request) != "cross"


def _refusal(code: str, message: str, hint: str, status: int) -> JSONResponse:
    return JSONResponse(
        {"error": code, "message": message, "next": hint}, status_code=status
    )


def auth_required(mode: str) -> JSONResponse:
    return _refusal(
        "E_AUTH_REQUIRED",
        "This action needs the owner's password, token or session.",
        sign_in_hint(mode),
        401,
    )


def peer_trusted(request: Request) -> bool:
    """Is the socket peer inside ``VIDTHEQUE_DASHBOARD_TRUSTED_CIDRS``?

    The one place the peer is read for access decisions, so the read gate and
    the write gate cannot drift apart again: until 2026-08-13 the write gate
    honored a trusted peer and the read pages refused the same peer — a network
    trusted to change the corpus but not to look at the dashboard it changed it
    from (dashboard.md §3.4's "boundary with no shape", lived). Socket peer
    only, never ``VIDTHEQUE_TRUSTED_IP_HEADER``: ``DashboardSettings.trusts``
    enforces that.
    """
    peer = request.client.host if request.client else None
    return bool(request.app.state.assembled.dashboard.trusts(peer))


async def require_write(request: Request) -> Response | None:
    """``None`` when the write may proceed, else the refusal to return.

    Order matters: the mode refusal is about the *deployment* and says how to
    fix it, so it must not be masked by a credential check that can never pass
    in that mode.

    The Origin rule is **strict for the cookie and lenient for everything
    else**, and that asymmetry is the whole CSRF posture (§3.3, amended by
    phase 3 — the write side is HTML forms, not JSON bodies, so "a cross-site
    form POST cannot reach the handler" is no longer true on its own):

    * ``session`` — an ambient credential a browser attaches by itself. It
      requires **positive** same-origin evidence; ``absent`` is refused,
      because a request with a cookie and no fetch metadata is exactly the
      shape a cross-site form POST would have if `SameSite=Lax` ever failed to
      hold it back.
    * ``bearer``, or a trusted peer — nothing ambient. ``absent`` passes, so
      ``curl -H 'Authorization: Bearer …'`` works with no ceremony.
    """
    assembled = request.app.state.assembled
    auth: AuthBundle = assembled.auth
    if auth.mode == "none":  # pragma: no cover - no such route is registered
        return _refusal(
            "E_AUTH_REQUIRED",
            "This instance runs with VIDTHEQUE_AUTH=none, so the dashboard "
            "serves the read-only views only.",
            "set VIDTHEQUE_AUTH=token and VIDTHEQUE_TOKEN, then restart.",
            403,
        )
    if request.method == "GET":  # pragma: no cover - no such route can exist
        raise AssertionError("no state-changing GET, ever (dashboard.md §3.3)")

    held = await credential(request)
    trusted_peer = held is None and peer_trusted(request)
    if held is None and not trusted_peer:
        return auth_required(auth.mode)

    evidence = origin_evidence(request)
    if evidence == "cross" or (evidence == "absent" and held == "session"):
        return _refusal(
            "E_BAD_ORIGIN",
            "That request came from another origin.",
            "use the dashboard on this server's own PUBLIC_URL.",
            403,
        )
    return None
