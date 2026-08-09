"""The write side — dashboard.md §5.5 and §2.4's row actions, phase 3.

Five POSTs and two forms, and every one of them goes through the **same service
call the MCP tool makes**: `tools/indexing.index_video` for the index form and
for re-index, `tools/library.tag_video` for tags. The form adds no policy. It
renders a signature that is already bounded — `max_items` clamped 1..200, tags
validated against the namespace rules, `channels` checked against the four sets
— which is the whole argument for building this on the service layer instead of
beside it (§5.5).

Three things this module deliberately does **not** have:

* **A delete button.** `jobs.kind` permits `'delete'` and index-schema §6.2
  designs the job, but nothing implements it: the runner's
  `NotImplementedPipeline` raises `E_NOT_IMPLEMENTED`. Shipping the button would
  queue a job that fails and call it a feature (§5.2).
* **A second session system.** The login page writes the *existing*
  `login_sessions` row and sets the *existing* `vidtheque_session` cookie, with
  the attributes `auth/login.py` already chose (§3.2 rule 2).
* **A state-changing GET.** `SameSite=Lax` sends the cookie on a top-level GET
  navigation; every handler here is POST and a test asserts the group has no
  other kind of write (§3.3).
"""

from __future__ import annotations

import hmac
import re
import secrets
import time
from typing import Any

from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

from ..auth.login import SESSION_COOKIE
from ..auth.provider import OWNER_SUBJECT
from ..config import Settings
from ..db import queries
from ..errors import HTTP_STATUS
from ..text import clamp
from ..tools import indexing, library
from ..tools.base import Deps
from .access import auth_required, credential, origin_ok, require_write
from .settings import ROOT
from .views import _chrome, _render, _tool_error

# §10.7, resolved: `index-video`'s ten-URL cap protects the *model* surface and
# does not move. A human pasting a straggler list is not that surface, so the
# form splits server-side into jobs of ten — which is what the 2026-08-09
# straggler run did by hand, 64 videos into 7 jobs.
URLS_PER_JOB = 10

# The form's own cap, because the paste box is an input and the URL bar is not
# the only place that rule applies. 200 is `max_items`' own ceiling in
# `tools/indexing.py`, so the form and the tool refuse at the same number
# rather than at two numbers a reader has to reconcile.
MAX_FORM_URLS = 200

# What a paste is split on: newlines, spaces, commas. Anything else is part of
# a URL, and `normalize_url` is the one that decides whether it is a good one.
_SEPARATORS = re.compile(r"[\s,]+")

# The three sets a human ticks, and the CSV `index-video` reads. All three (or
# none) is the string `all` — the tool's own word, not a synonym.
_CHANNEL_BOXES = (
    ("transcript", "Transcript", "what was said, from the audio or the captions"),
    ("ocr", "On-screen text", "what the frames read, per keyframe"),
    ("frames", "Frame embeddings", "visual search over the keyframes"),
)


# ------------------------------------------------------------------ plumbing


def _wants_html(request: Request) -> bool:
    return "text/html" in request.headers.get("accept", "")


def _safe_next(raw: str | None) -> str:
    """A redirect target that cannot leave this surface.

    Only a path under `/dashboard`, and never `//host` — a protocol-relative
    URL is an absolute one wearing a path's clothes, and an open redirect on
    the page that mints the session cookie is the worst place to have one.
    """
    if not raw or not raw.startswith(ROOT) or raw.startswith("//"):
        return ROOT
    return raw


async def _guard(request: Request) -> Response | None:
    """`require_write`, with the browser's half of the refusal.

    A script gets the typed JSON. A browser whose session expired between the
    page load and the button gets sent to the login page with somewhere to come
    back to — the refusal is the same one, rendered in the medium that asked.
    """
    refusal = await require_write(request)
    if refusal is None:
        return None
    if refusal.status_code == 401 and _wants_html(request):
        return _to_login(request)
    return refusal


def _to_login(request: Request) -> RedirectResponse:
    target = f"{ROOT}/login?next={request.url.path}"
    return RedirectResponse(target, status_code=303)


def _see(path: str) -> RedirectResponse:
    """POST → 303 → GET. A write is never the thing a reload repeats."""
    return RedirectResponse(path, status_code=303)


def _error_page(
    request: Request, page: str, error: dict[str, Any], back: dict[str, str] | None = None
) -> Response:
    return _render(
        "error.html",
        {**_chrome(request, page), "title": error["message"], "error": error, "back": back},
        status=HTTP_STATUS.get(error["code"], 500),
    )


# --------------------------------------------------------------------- login


def _login_context(
    request: Request, *, error: str | None, next_url: str
) -> dict[str, Any]:
    settings = request.app.state.assembled.settings
    return {
        **_chrome(request, "login"),
        "title": "Sign in",
        "error": error,
        "next_url": next_url,
        # Which secret this deployment will accept, so the field's label is the
        # truth rather than a generic "password". In `token` mode with no
        # password set there is exactly one answer and the page says it.
        "accepts_password": bool(settings.password),
        "accepts_token": settings.auth_mode == "token" and bool(settings.static_token),
    }


async def login(request: Request) -> Response:
    """`GET|POST /dashboard/login` — the secret, once, for the existing cookie.

    Registered only where the write side is (`write_side_enabled`): a sign-in
    that grants nothing is a probe magnet with a password field on it.
    """
    assembled = request.app.state.assembled
    settings = assembled.settings
    store = assembled.auth.store

    if request.method == "GET":
        next_url = _safe_next(request.query_params.get("next"))
        # Already holding a credential: the page has nothing to offer, so it
        # sends them where they were going instead of asking again.
        if await credential(request) is not None:
            return _see(next_url)
        return _render("login.html", _login_context(request, error=None, next_url=next_url))

    form = await request.form()
    next_url = _safe_next(str(form.get("next") or ""))
    # The login is a state change (it mints a session), so it carries the same
    # Origin rule as every other write. It cannot carry the credential half —
    # not having one is the point of the page.
    if not origin_ok(request):
        return _render(
            "login.html",
            _login_context(
                request, error="That sign-in came from another origin.", next_url=next_url
            ),
            status=403,
        )
    if store is None:  # pragma: no cover - every write-side mode builds one
        return auth_required(settings.auth_mode)

    supplied = str(form.get("password") or "")
    if not _accepted(settings, supplied):
        # One message for both secrets and both failure shapes. "Wrong
        # password" against a deployment that accepts the token instead would
        # be a hint about which secret exists.
        return _render(
            "login.html",
            _login_context(
                request, error="That secret does not match this instance.", next_url=next_url
            ),
            status=401,
        )

    sid = secrets.token_urlsafe(32)
    ttl = settings.login_session_ttl_s
    store.save_session(sid, OWNER_SUBJECT, int(time.time()) + ttl)
    response = _see(next_url)
    response.set_cookie(
        SESSION_COOKIE,
        sid,
        max_age=ttl,
        httponly=True,
        samesite="lax",
        # Exactly the attributes `auth/login.py` chose, read from the same
        # place: one cookie means one set of flags, not two that drift.
        secure=settings.public_url.startswith("https://"),
        path="/",
    )
    return response


def _accepted(settings: Settings, supplied: str) -> bool:
    """Is this the password, or (in `token` mode) the bearer token?

    Both comparisons always run: a short-circuit would leak, by timing, which
    of the two secrets a deployment has configured.
    """
    ok = False
    if settings.password:
        ok |= hmac.compare_digest(supplied, settings.password)
    if settings.auth_mode == "token" and settings.static_token:
        ok |= hmac.compare_digest(supplied, settings.static_token)
    return ok


async def logout(request: Request) -> Response:
    """`POST /dashboard/logout` — the row goes, not just the cookie.

    Clearing the cookie alone leaves a live `login_sessions` row that anything
    holding a copy of the value could still present.
    """
    refusal = await _guard(request)
    if refusal is not None:
        return refusal
    store = request.app.state.assembled.auth.store
    if store is not None:
        store.delete_session(request.cookies.get(SESSION_COOKIE))
    response = _see(f"{ROOT}/login")
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


# ---------------------------------------------------------------- §5.5 index


def _index_context(request: Request, **extra: Any) -> dict[str, Any]:
    assembled = request.app.state.assembled
    return {
        **_chrome(request, "index"),
        "title": "Add to the index",
        "expansions": indexing.EXPANSIONS,
        "channel_boxes": _CHANNEL_BOXES,
        "urls_per_job": URLS_PER_JOB,
        "max_form_urls": MAX_FORM_URLS,
        # §5.5: when the corpus config and the vector tables disagree, the form
        # renders disabled with the reason, rather than accepting a submission
        # that will come back `E_FEATURE_DISABLED`.
        "vectors": assembled.db.vectors,
        "form": {
            "urls": "",
            "expand": "playlist",
            "max_items": 25,
            "tags": "",
            "channels": [name for name, _label, _note in _CHANNEL_BOXES],
            "priority": "normal",
            "force_reindex": False,
        },
        "result": None,
        "error": None,
        **extra,
    }


async def index_form(request: Request) -> Response:
    """`GET /dashboard/index` — the form. A read, so it takes the read gate."""
    return _render("index.html", _index_context(request))


async def index_submit(request: Request) -> Response:
    """`POST /dashboard/index` → `index_video` → the jobs view.

    The one thing the form handles that the tool does not: a real batch. §10.7
    keeps the ten-URL cap on the MCP surface and splits here instead, server
    side, and says so on the page — a split the operator cannot see is a job
    count they cannot explain.
    """
    refusal = await _guard(request)
    if refusal is not None:
        return refusal

    deps: Deps = request.app.state.assembled.deps
    form = await request.form()
    submitted = _submitted(form)
    tokens = [t for t in _SEPARATORS.split(str(form.get("urls") or "")) if t]

    if not tokens:
        return _render(
            "index.html",
            _index_context(
                request,
                form=submitted,
                error={
                    "code": "E_BAD_PARAM",
                    "message": "Paste at least one video, playlist or channel URL.",
                    "next": "a bare 11-character YouTube id works too.",
                },
            ),
            status=400,
        )
    if len(tokens) > MAX_FORM_URLS:
        return _render(
            "index.html",
            _index_context(
                request,
                form=submitted,
                error={
                    "code": "E_TOO_LARGE",
                    "message": f"{len(tokens)} URLs is past this form's cap of "
                    f"{MAX_FORM_URLS}.",
                    "next": "submit it in parts, or point one job at the playlist.",
                },
            ),
            status=413,
        )

    # Batch at ten, or at `max_items` when the operator set it lower —
    # `index_video` truncates its URL list to `max_items`, and a batch bigger
    # than that would drop the tail silently.
    size = max(1, min(URLS_PER_JOB, submitted["max_items"]))
    batches = [tokens[i : i + size] for i in range(0, len(tokens), size)]

    jobs: list[dict[str, Any]] = []
    already: list[str] = []
    errors: list[dict[str, Any]] = []
    for batch in batches:
        result = await indexing.index_video(
            deps,
            urls=batch,
            expand=submitted["expand"],
            max_items=submitted["max_items"],
            tags=submitted["tags"] or None,
            force_reindex=submitted["force_reindex"],
            channels=",".join(submitted["channels"]) or "all",
            priority=submitted["priority"],
        )
        error = _tool_error(result)
        if error is not None:
            errors.append({**error, "urls": batch})
            continue
        payload = result.structured_content or {}
        already.extend(str(v) for v in payload.get("already_indexed", []))
        if payload.get("job_id"):
            jobs.append(
                {
                    "job_id": str(payload["job_id"]),
                    "items": int(payload.get("items", 0)),
                    "urls": batch,
                }
            )

    # One job and nothing to explain: go straight to the thing that is now
    # happening (§5.5). Anything else has a receipt worth reading, and a
    # receipt is not something a reload should re-submit — so it renders in
    # place of the form, with the form still under it for the next batch.
    if len(jobs) == 1 and not errors and not already:
        return _see(f"{ROOT}/jobs/{jobs[0]['job_id']}")
    return _render(
        "index.html",
        _index_context(
            request,
            form=submitted,
            result={
                "jobs": jobs,
                "already": already,
                "errors": errors,
                "batches": len(batches),
                "urls": len(tokens),
                "split": len(batches) > 1,
            },
        ),
        status=200 if jobs or already else 409,
    )


def _submitted(form: Any) -> dict[str, Any]:
    """The form, back as the form — so a refusal re-renders what was typed."""
    expand = str(form.get("expand") or "playlist")
    if expand not in indexing.EXPANSIONS:
        expand = "playlist"
    priority = str(form.get("priority") or "normal")
    if priority not in ("normal", "high"):
        priority = "normal"
    channels = [
        name for name, _label, _note in _CHANNEL_BOXES if form.get(f"channel_{name}")
    ]
    names = [name for name, _label, _note in _CHANNEL_BOXES]
    return {
        "urls": str(form.get("urls") or ""),
        "expand": expand,
        # The tool's own clamp, applied here too so the *re-rendered* form shows
        # the number that would actually be used.
        "max_items": clamp(form.get("max_items"), 1, 200, 25),  # type: ignore[arg-type]
        "tags": str(form.get("tags") or "").strip(),
        # All three, or none ticked, is the tool's word `all` — not a synonym
        # for it and not a three-item CSV that means the same thing.
        "channels": [] if len(channels) in (0, len(names)) else channels,
        "priority": priority,
        "force_reindex": bool(form.get("force_reindex")),
    }


# ------------------------------------------------------------ §2.4 row actions


async def reindex(request: Request) -> Response:
    """`POST /dashboard/videos/{video_id}/reindex` — force, one video.

    `force_reindex=true` on the video's own URL with `expand=none`: this button
    is about *this* row, and a playlist URL that expanded here would queue a
    surprise.
    """
    refusal = await _guard(request)
    if refusal is not None:
        return refusal

    assembled = request.app.state.assembled
    video_id = request.path_params["video_id"]
    row = await assembled.db.read(lambda c: queries.lookup_video(c, video_id))
    if row is None:
        return _error_page(
            request,
            "videos",
            {
                "code": "E_UNKNOWN_VIDEO",
                "message": f'"{video_id}" is not in the corpus.',
                "next": "browse the videos table for what is indexed.",
            },
        )

    result = await indexing.index_video(
        assembled.deps, url=str(row["url"]), expand="none", force_reindex=True
    )
    error = _tool_error(result)
    if error is not None:
        return _error_page(
            request,
            "videos",
            error,
            back={"href": f"{ROOT}/videos/{video_id}", "label": "Back to this video"},
        )
    job_id = (result.structured_content or {}).get("job_id")
    if not job_id:  # pragma: no cover - force_reindex always creates a job
        return _see(f"{ROOT}/videos/{video_id}")
    return _see(f"{ROOT}/jobs/{job_id}")


async def set_tags(request: Request) -> Response:
    """`POST /dashboard/videos/{video_id}/tags` — the same `tag_video` the tool calls.

    Namespace rules, the ten-tag cap and the `<ns>:<value>` shape are the
    tool's, verbatim, including its error text: a tag this refuses here is a
    tag it would refuse there, and the page says so in the same words.
    """
    refusal = await _guard(request)
    if refusal is not None:
        return refusal

    deps: Deps = request.app.state.assembled.deps
    video_id = request.path_params["video_id"]
    form = await request.form()
    add = _tags(str(form.get("add") or ""))
    remove = _tags(str(form.get("remove") or ""))
    back = f"{ROOT}/videos/{video_id}#manage"

    if not add and not remove:
        return _see(back)
    result = await library.tag_video(deps, video_id=video_id, add=add, remove=remove)
    error = _tool_error(result)
    if error is not None:
        return _error_page(
            request,
            "videos",
            error,
            back={"href": back, "label": "Back to this video"},
        )
    return _see(back)


def _tags(raw: str) -> list[str]:
    return [t.strip() for t in _SEPARATORS.split(raw) if t.strip()]
