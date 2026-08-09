"""Which credential let a plain HTTP request through — one answer, three callers.

This started life inside ``dashboard/access.py``, where it decided who may read
the management pages. It moved here when a *second* route group needed the same
answer for a different question: `public/api.py` keys its clamp policy off the
credential rather than off the route group it arrived on (dashboard.md §2.5.1,
amended in phase 5). Two copies of an authorization check is how the two drift,
so there is one, in the package that owns the credentials, importing neither
route group.

It is deliberately *not* merged with ``http/frames.py:_authorized``, which
answers the same question plus one: a signed URL. That signature is the
credential a rendered ``<img>`` can carry and nothing else can, it is scoped to
one frame at one width, and it must never widen a clamp policy or unlock a
page — so it stays where the only route that honours it lives.

The return value names the credential rather than being a bool because the
callers treat the answers differently:

* ``"open"`` — ``VIDTHEQUE_AUTH=none``. **Not a credential**: it is the absence
  of a check, and every request in that mode gets it, including an anonymous
  one off the public internet. A caller asking "may this request read?" says
  yes to it (the corpus is already open through `/mcp` and `/frames` in that
  mode); a caller asking "is this the owner?" must say no.
* ``"bearer"`` — a token, presented deliberately, by a non-browser client.
* ``"session"`` — the login cookie: *ambient*, and therefore the only answer a
  cross-site page could ever cause to be sent. That is what the write side's
  Origin rule is asymmetric about (``dashboard/access.py:require_write``).
* ``None`` — anonymous, in a mode that has something to check.
"""

from __future__ import annotations

from starlette.requests import Request

from .login import SESSION_COOKIE
from .modes import AuthBundle


async def credential(request: Request) -> str | None:
    """``"open"``, ``"bearer"``, ``"session"`` or ``None`` — see the module docstring."""
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


async def is_owner(request: Request) -> bool:
    """Did the *caller* prove they are the owner? (Not: is this mode open.)

    The distinction ``"open"`` forces is the whole of the phase-5 clamp fix
    (`docs/deploy-public.md`, "Clamp policy on `/dashboard/api/*`"): in the
    intended public deployment — ``VIDTHEQUE_PUBLIC_READONLY=1`` with
    ``VIDTHEQUE_AUTH=none`` — every request is ``"open"``, so a policy that
    treated "the read gate let you in" as "you are the owner" handed the
    owner's bounds, and with them the full-transcript hatch, to the internet.

    A **trusted peer** counts. ``VIDTHEQUE_DASHBOARD_TRUSTED_CIDRS`` already
    grants that network the *write* side with no credential at all
    (dashboard.md §3.4) — indexing, re-indexing, tagging. A network trusted to
    change the corpus but not to read a transcript of it would be a boundary
    with no shape, and it is the one lever that gives an ``AUTH=none`` LAN
    deployment its owner back after this change. It is the socket peer only,
    never ``VIDTHEQUE_TRUSTED_IP_HEADER`` — ``DashboardSettings.trusts``
    enforces that, and a header-based allowlist is an allowlist of everybody.
    """
    if await credential(request) in ("bearer", "session"):
        return True
    peer = request.client.host if request.client else None
    return bool(request.app.state.assembled.dashboard.trusts(peer))
