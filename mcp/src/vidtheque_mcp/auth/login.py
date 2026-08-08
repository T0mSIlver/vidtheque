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


def _render(heading: str, body: str, status: int = 200) -> HTMLResponse:
    return HTMLResponse(_PAGE.format(heading=heading, body=body), status_code=status)


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
        note = '<p class="err">Wrong password.</p>' if error else ""
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

        form = await request.form()
        request_key = str(form.get("rq", ""))
        supplied = str(form.get("password", ""))
        if not settings.password or not hmac.compare_digest(supplied, settings.password):
            return _render(
                "Sign in to vidtheque", _login_body(request_key, "bad"), status=401
            )
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
