"""The owner login and consent pages.

One owner, one password (``VIDTHEQUE_PASSWORD``), no password reset, no user
management. That is the whole "user directory", and it is why writing our own
issuer is a materially smaller surface than the framework docs' warnings assume.

The consent screen displays the **host of the client_id URL**, not
``client_name`` — a CIMD document is self-asserted, so its display name is
whatever the client chose to claim.
"""

from __future__ import annotations

import html
import hmac
import secrets
import time

from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response
from starlette.routing import Route

from ..config import Settings
from .cimd import display_host
from .provider import OWNER_SUBJECT, VidthequeOAuthProvider

SESSION_COOKIE = "vidtheque_session"

_PAGE = """<!doctype html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>vidtheque — {heading}</title>
<style>
 body {{ font: 16px/1.5 system-ui, sans-serif; max-width: 26rem; margin: 12vh auto;
        padding: 0 1rem; color: #1a1a1a; background: #fff; }}
 h1 {{ font-size: 1.25rem; font-weight: 600; }}
 .host {{ font-weight: 600; }}
 input, button {{ font: inherit; padding: .5rem .6rem; width: 100%; box-sizing: border-box; }}
 button {{ margin-top: .75rem; cursor: pointer; }}
 .err {{ color: #b3261e; }}
 .muted {{ color: #666; font-size: .875rem; }}
 @media (prefers-color-scheme: dark) {{
   body {{ color: #eee; background: #141414; }}
   input, button {{ background: #222; color: #eee; border: 1px solid #444; }}
 }}
</style>
<h1>{heading}</h1>
{body}
"""


# An authorization server's own pages must not be framable: the consent screen
# is a decision made by looking at it, and a decision made by looking at it can
# be stolen by covering it. Both headers, because the old one is what actually
# gets honoured by some middleboxes and the CSP one is the current answer.
# RFC 9700 §4.16. (2026-08-10 audit, auth hardening.)
_FRAME_HEADERS = {
    "Content-Security-Policy": "frame-ancestors 'none'",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}


def _render(heading: str, body: str, status: int = 200) -> HTMLResponse:
    return HTMLResponse(
        _PAGE.format(heading=heading, body=body),
        status_code=status,
        headers=dict(_FRAME_HEADERS),
    )


def _matches(supplied: str, expected: str) -> bool:
    """Constant-time compare that a non-ASCII password cannot crash.

    `hmac.compare_digest` on `str` raises TypeError for anything outside
    ASCII, so `password=%C3%A9` was a 500 rather than a refusal — the same trap
    exists on the bearer and signature paths. Comparing UTF-8 bytes keeps the
    timing property and accepts every string. (2026-08-10 audit, auth hardening.)
    """
    return hmac.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8"))


class _LoginThrottle:
    """A global backoff on wrong passwords.

    The root limiter passes `/auth/*` through, and this endpoint mints an owner
    session, so it was unlimited guessing against a password with no strength
    requirement beyond non-empty. Keyed globally rather than per IP on purpose:
    there is exactly one password here, so spreading guesses across addresses
    must not buy anything, and locking out "everyone" means locking out one
    person who knows their own password and can wait ten seconds.
    """

    def __init__(self, threshold: int = 5, base_delay_s: float = 2.0) -> None:
        self._threshold = threshold
        self._base = base_delay_s
        self._failures = 0
        self._next_allowed = 0.0

    def retry_after(self) -> float:
        remaining = self._next_allowed - time.monotonic()
        return max(0.0, remaining)

    def record_failure(self) -> None:
        self._failures += 1
        if self._failures >= self._threshold:
            over = self._failures - self._threshold
            delay = min(self._base * (2**over), 60.0)
            self._next_allowed = time.monotonic() + delay

    def reset(self) -> None:
        self._failures = 0
        self._next_allowed = 0.0


_throttle = _LoginThrottle()


def _same_origin(request: Request, public_url: str) -> bool:
    """Refuse a form post that another site made the browser send.

    `SameSite=Lax` stops a cross-*site* POST and does not stop a sibling on the
    same registrable domain, so the session cookie alone is not proof the user
    meant this. `Origin` is sent on every cross-origin POST by every browser
    that matters; `Sec-Fetch-Site` is the modern belt to that braces. A request
    with neither is not a browser form post at all, and the only client that
    should be posting these forms is a browser.
    """
    site = request.headers.get("sec-fetch-site")
    if site in ("same-origin", "none"):
        return True
    if site is not None:
        return False
    origin = request.headers.get("origin")
    return bool(origin) and origin.rstrip("/") == public_url.rstrip("/")


def login_routes(settings: Settings, provider: VidthequeOAuthProvider) -> list[Route]:
    store = provider.store

    def _consent_body(request_key: str, client_id: str, scopes: list[str]) -> str:
        host = html.escape(display_host(client_id))
        scope_text = html.escape(", ".join(scopes))
        return (
            f'<p><span class="host">{host}</span> is asking to connect to your '
            "vidtheque corpus.</p>"
            f'<p class="muted">Scopes: {scope_text}</p>'
            f'<form method="post" action="/auth/consent">'
            f'<input type="hidden" name="rq" value="{html.escape(request_key)}">'
            '<button type="submit" name="decision" value="allow">Allow</button>'
            '<button type="submit" name="decision" value="deny">Deny</button>'
            "</form>"
        )

    def _login_body(request_key: str, error: str | None) -> str:
        messages = {
            "bad": "Wrong password.",
            "slow": "Too many attempts. Wait a moment and try again.",
        }
        note = (
            f'<p class="err">{messages.get(error or "", "Wrong password.")}</p>'
            if error
            else ""
        )
        return (
            f"{note}"
            '<form method="post" action="/auth/login">'
            f'<input type="hidden" name="rq" value="{html.escape(request_key)}">'
            '<input type="password" name="password" autocomplete="current-password" '
            'placeholder="Owner password" autofocus>'
            '<button type="submit">Sign in</button>'
            "</form>"
        )

    async def login(request: Request) -> Response:
        request_key = request.query_params.get("rq", "")
        if request.method == "GET":
            if store.load_session(request.cookies.get(SESSION_COOKIE)):
                return _consent_page(request_key, request)
            return _render("Sign in to vidtheque", _login_body(request_key, None))

        if not _same_origin(request, settings.public_url):
            return _render("Sign in to vidtheque", _login_body("", "bad"), status=403)
        form = await request.form()
        request_key = str(form.get("rq", ""))
        supplied = str(form.get("password", ""))
        delay = _throttle.retry_after()
        if delay:
            return _render(
                "Sign in to vidtheque",
                _login_body(request_key, "slow"),
                status=429,
            )
        if not settings.password or not _matches(supplied, settings.password):
            _throttle.record_failure()
            return _render(
                "Sign in to vidtheque", _login_body(request_key, "bad"), status=401
            )
        _throttle.reset()
        sid = secrets.token_urlsafe(32)
        store.save_session(sid, OWNER_SUBJECT, int(time.time()) + settings.login_session_ttl_s)
        response = _consent_page(request_key, request)
        response.set_cookie(
            SESSION_COOKIE,
            sid,
            max_age=settings.login_session_ttl_s,
            httponly=True,
            samesite="lax",
            secure=settings.public_url.startswith("https://"),
            path="/",
        )
        return response

    def _consent_page(request_key: str, request: Request) -> Response:
        pending = store.take_pending(request_key) if request_key else None
        if pending is None:
            return _render(
                "Signed in",
                '<p>You are signed in. There is no pending authorization request — '
                "start the connection again from your client.</p>",
            )
        # Put it back: consent has not happened yet.
        store.save_pending(request_key, pending)
        return _render(
            "Authorize access",
            _consent_body(request_key, pending["client_id"], pending["scopes"]),
        )

    async def consent(request: Request) -> Response:
        if not _same_origin(request, settings.public_url):
            return _render(
                "Refused",
                "<p>That request did not come from this site.</p>",
                status=403,
            )
        form = await request.form()
        request_key = str(form.get("rq", ""))
        if not store.load_session(request.cookies.get(SESSION_COOKIE)):
            return RedirectResponse(f"/auth/login?rq={request_key}", status_code=303)
        pending = store.take_pending(request_key)
        if pending is None:
            return _render("Expired", "<p>That authorization request expired.</p>", status=400)
        if str(form.get("decision")) != "allow":
            redirect = pending["redirect_uri"]
            sep = "&" if "?" in redirect else "?"
            state = f"&state={pending['state']}" if pending.get("state") else ""
            return RedirectResponse(f"{redirect}{sep}error=access_denied{state}", status_code=302)
        return RedirectResponse(provider.complete_authorization(pending), status_code=302)

    return [
        Route("/auth/login", login, methods=["GET", "POST"]),
        Route("/auth/consent", consent, methods=["POST"]),
    ]
