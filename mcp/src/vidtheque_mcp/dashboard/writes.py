"""The write side — dashboard.md §5.5 and §2.4's row actions, phase 3.

Every POST here goes through the **same service call the MCP tool makes**:
`tools/indexing.index_video` for the index form, for re-index and for the
ledger's `Index anyway`; `tools/library.tag_video` for tags;
`tools/follows.follow_channel` for following, pausing, resuming, checking and
unfollowing; and `follows/params.build_rules` — the validator that tool shares —
for editing a rule. The form adds no policy. It renders a signature that is
already bounded — `max_items` clamped 1..200, tags validated against the
namespace rules, `channels` checked against the four sets, a follow's interval
floored at fifteen minutes — which is the whole argument for building this on
the service layer instead of beside it (§5.5, §18.5).

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
import json
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
from ..errors import HTTP_STATUS, ToolError
from ..follows import params as follow_params
from ..follows import store as follows_store
# By name, not by module: `follow_rules` is a handler in this file, and a module
# alias one letter away from it is the kind of shadowing that only shows up at
# the one call site that needed the other one.
from ..follows.rules import TABS as FOLLOW_TABS
from ..follows.rules import Rules as FollowRules
from ..jobs import store as jobs_store
from ..text import clamp
from ..tools import follows as follows_tool
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

# A GET prefill is still an input that becomes a response body. Bound both
# free-text fields before rendering so a deep link cannot turn the index form
# into an unbounded HTML payload. These are render bounds only: POST keeps the
# service layer's validation and the form's URL-count cap below.
MAX_PREFILL_URLS_CHARS = 16_384
MAX_PREFILL_TAGS_CHARS = 800

# What a paste is split on: newlines, spaces, commas. Anything else is part of
# a URL, and `normalize_url` is the one that decides whether it is a good one.
_SEPARATORS = re.compile(r"[\s,]+")

# The three sets a human ticks, and the CSV `index-video` reads. All three (or
# none) is the string `all` — the tool's own word, not a synonym. Public,
# because the follow form ticks the same three boxes for the same parameter and
# a second copy of the labels is how one thing gets described two ways.
CHANNEL_BOXES = (
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


def _index_form_values() -> dict[str, Any]:
    return {
        "urls": "",
        "expand": "playlist",
        "max_items": 25,
        "tags": "",
        "channels": [name for name, _label, _note in CHANNEL_BOXES],
        "priority": "normal",
        "force_reindex": False,
    }


def _index_context(request: Request, **extra: Any) -> dict[str, Any]:
    assembled = request.app.state.assembled
    return {
        **_chrome(request, "index"),
        "title": "Add to the index",
        "expansions": indexing.EXPANSIONS,
        "channel_boxes": CHANNEL_BOXES,
        "urls_per_job": URLS_PER_JOB,
        "max_form_urls": MAX_FORM_URLS,
        # §5.5: when the corpus config and the vector tables disagree, the form
        # renders disabled with the reason, rather than accepting a submission
        # that will come back `E_FEATURE_DISABLED`.
        "vectors": assembled.db.vectors,
        "form": _index_form_values(),
        "result": None,
        "error": None,
        **extra,
    }


def _prefilled_index_form(request: Request) -> dict[str, Any]:
    """Bounded GET parameters, copied into controls and nowhere else.

    This deliberately does not normalise URLs, validate tags, call a tool or
    touch the database. A prefill is a draft the operator may still edit; the
    existing POST remains the only path that interprets or persists it.
    """
    form = _index_form_values()
    params = request.query_params
    expand = str(params.get("expand") or "")
    form["urls"] = str(params.get("urls") or "")[:MAX_PREFILL_URLS_CHARS]
    form["tags"] = str(params.get("tags") or "")[:MAX_PREFILL_TAGS_CHARS]
    if expand in indexing.EXPANSIONS:
        form["expand"] = expand
    return form


async def index_form(request: Request) -> Response:
    """`GET /dashboard/index` — a bounded prefill, and no state change."""
    return _render(
        "index.html", _index_context(request, form=_prefilled_index_form(request))
    )


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
        name for name, _label, _note in CHANNEL_BOXES if form.get(f"channel_{name}")
    ]
    names = [name for name, _label, _note in CHANNEL_BOXES]
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


async def cancel_job(request: Request) -> Response:
    """`POST /dashboard/jobs/{job_id}/cancel` — stop only live work.

    Queued work settles in the store immediately. Running work remains
    `running` with `cancel_requested=1` until the real pipeline reaches its
    next cooperative stage boundary; the detail page therefore reports the
    request without claiming the worker has already stopped.
    """
    refusal = await _guard(request)
    if refusal is not None:
        return refusal

    job_id = request.path_params["job_id"]
    outcome = await request.app.state.assembled.db.write(
        lambda c: jobs_store.request_cancel(c, job_id)
    )
    if outcome is None:
        return _error_page(
            request,
            "jobs",
            {
                "code": "E_UNKNOWN_JOB",
                "message": f'"{job_id}" is not a job on this instance.',
                "next": "the jobs table lists every job this index has run.",
            },
        )
    accepted, state = outcome
    if not accepted:
        return _error_page(
            request,
            "jobs",
            {
                "code": "E_BAD_PARAM",
                "message": f'Job "{job_id}" is already {state}.',
                "next": "only queued or running jobs can be cancelled.",
            },
            back={"href": f"{ROOT}/jobs/{job_id}", "label": "Back to this job"},
        )
    return _see(f"{ROOT}/jobs/{job_id}")


async def retry_job(request: Request) -> Response:
    """Requeue only a finished job's failed or degraded items.

    Selection is one bounded store read. Creation deliberately goes back
    through ``tools.indexing.index_video`` in batches of at most ten, preserving
    the original channels, tags, expansion bound and priority while leaving
    successful items out of the call entirely.
    """
    refusal = await _guard(request)
    if refusal is not None:
        return refusal

    assembled = request.app.state.assembled
    old_job_id = request.path_params["job_id"]
    selected = await assembled.db.read(
        lambda c: jobs_store.retry_candidates(c, old_job_id, MAX_FORM_URLS + 1)
    )
    if selected is None:
        return _error_page(
            request,
            "jobs",
            {
                "code": "E_UNKNOWN_JOB",
                "message": f'"{old_job_id}" is not a job on this instance.',
                "next": "the jobs table lists every job this index has run.",
            },
        )
    job, candidates = selected
    state = str(job["state"])
    if state in ("queued", "running"):
        return _error_page(
            request,
            "jobs",
            {
                "code": "E_BAD_PARAM",
                "message": f'Job "{old_job_id}" is still {state}.',
                "next": "retry is available after the original job finishes.",
            },
            back={"href": f"{ROOT}/jobs/{old_job_id}", "label": "Back to this job"},
        )
    if str(job["kind"]) not in ("index", "reindex"):
        return _error_page(
            request,
            "jobs",
            {
                "code": "E_BAD_PARAM",
                "message": f'Job "{old_job_id}" is a {job["kind"]} job.',
                "next": "only indexing jobs can be repaired through index-video.",
            },
            back={"href": f"{ROOT}/jobs/{old_job_id}", "label": "Back to this job"},
        )
    if len(candidates) > MAX_FORM_URLS:
        return _error_page(
            request,
            "jobs",
            {
                "code": "E_TOO_LARGE",
                "message": f"More than {MAX_FORM_URLS} items need repair.",
                "next": "retry the affected videos in smaller batches from the index form.",
            },
        )
    if not candidates:
        return _error_page(
            request,
            "jobs",
            {
                "code": "E_BAD_PARAM",
                "message": f'Job "{old_job_id}" has no failed or degraded items.',
                "next": "successful items are deliberately not re-queued.",
            },
            back={"href": f"{ROOT}/jobs/{old_job_id}", "label": "Back to this job"},
        )

    try:
        args = json.loads(str(job["args_json"] or "{}"))
    except (TypeError, ValueError):
        args = {}
    # `create_job` always dumps a dict, but this row is data, not an
    # invariant: `"null"` and `"[]"` decode fine and would 500 on `.get`.
    if not isinstance(args, dict):
        args = {}
    max_items = clamp(args.get("max_items"), 1, MAX_FORM_URLS, 25)
    batch_size = max(1, min(URLS_PER_JOB, max_items))
    urls = [str(row["source_url"]) for row in candidates]
    batches = [urls[i : i + batch_size] for i in range(0, len(urls), batch_size)]
    tags = args.get("tags") or []
    tags_csv = ",".join(str(tag) for tag in tags) if isinstance(tags, list) else str(tags)
    channels = str(args.get("channels") or "all")
    priority = "high" if int(job["priority"]) <= 50 else "normal"

    jobs: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for batch in batches:
        result = await indexing.index_video(
            assembled.deps,
            urls=batch,
            expand=str(args.get("expand") or "none"),
            max_items=max_items,
            tags=tags_csv or None,
            channels=channels,
            priority=priority,
        )
        error = _tool_error(result)
        if error is not None:
            errors.append(error)
            continue
        payload = result.structured_content or {}
        if payload.get("job_id"):
            jobs.append(
                {"job_id": str(payload["job_id"]), "items": int(payload.get("items", 0))}
            )

    # `index_submit`'s rule, for the same reason: one job and nothing to
    # explain goes straight to the thing that is now happening, and the POST
    # is never left as the page a reload would repeat — a reloaded retry is
    # a duplicate repair job, the one write on this surface where that is
    # not merely noise.
    if len(jobs) == 1 and not errors:
        return _see(f"{ROOT}/jobs/{jobs[0]['job_id']}")
    return _render(
        "retry.html",
        {
            **_chrome(request, "jobs"),
            "title": f"Retry from {old_job_id}",
            "old_job_id": old_job_id,
            "selected": len(candidates),
            "jobs": jobs,
            "errors": errors,
            "preserved": {
                "channels": channels,
                "tags": tags_csv or "—",
                "priority": priority,
            },
        },
        status=200 if jobs else 409,
    )


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


# ------------------------------------------------------- §5.5's shape, applied
#                                                          to following (§2.2)

# Six POSTs, and not one of them decides anything. Creating, pausing, resuming,
# checking now and unfollowing all go through `tools/follows.follow_channel` —
# the same call the model makes — and editing the rules goes through
# `follows/params.build_rules`, which that tool also calls and whose module
# docstring names this file as its second caller by design. The URL
# normalisation, the "a follow watches a container, not one video" refusal, the
# duration parser, the tag namespace rules, the interval floor and the two
# server-side clamps are all *there*. Reimplementing any of them here would be
# the second policy dashboard.md §5.5 exists to argue against.
#
# `Index anyway` is the seventh and it is not a follow write at all: it is
# `index_video` on one URL with `expand=none`, carrying the follow's own
# channels and tags so a video rescued from the ledger is built the way the
# follow would have built it.


def _follow_error(
    request: Request, error: dict[str, Any], slug: str | None = None
) -> Response:
    back = (
        {"href": f"{ROOT}/following/{slug}", "label": "Back to this follow"}
        if slug
        else {"href": f"{ROOT}/following", "label": "Following"}
    )
    return _error_page(request, "following", error, back=back)


def _typed(exc: ToolError) -> dict[str, Any]:
    """A raised `ToolError` in the shape `_tool_error` returns.

    `build_rules` raises rather than returning a `CallToolResult`, because its
    other caller is a tool and a tool's decorator does the wrapping. One page
    renders both, so they arrive as one shape.
    """
    return {"code": exc.code, "message": exc.message, "next": exc.next_hint}


def _follow_rule_form(form: Any) -> dict[str, Any]:
    """The rule controls, as the shared validator's keyword arguments.

    Checkbox groups collapse the way `index-video`'s do: all three channels
    ticked, or none, is the tool's own word `all` rather than a three-item CSV
    that means the same thing. Nothing here is validated — that is
    `build_rules`' job, and doing half of it here is how two validators start
    disagreeing.
    """
    tabs = [tab for tab in FOLLOW_TABS if form.get(f"tab_{tab}")]
    names = [name for name, _label, _note in CHANNEL_BOXES]
    channels = [name for name in names if form.get(f"channel_{name}")]
    return {
        "tabs": ",".join(tabs) or "videos",
        "min_duration": str(form.get("min_duration") or "").strip() or None,
        "max_duration": str(form.get("max_duration") or "").strip() or None,
        "title_include": str(form.get("title_include") or "").strip() or None,
        "title_exclude": str(form.get("title_exclude") or "").strip() or None,
        "channels": "all" if len(channels) in (0, len(names)) else ",".join(channels),
        "tags": str(form.get("tags") or "").strip() or None,
        "backfill": str(form.get("backfill") or "0").strip() or "0",
        "max_per_check": str(form.get("max_per_check") or "5").strip() or "5",
        "mode": str(form.get("mode") or "auto"),
        "check_interval_s": str(form.get("check_interval_s") or "").strip() or None,
    }


async def follow_create(request: Request) -> Response:
    """`POST /dashboard/following` — the add form, through the tool.

    Redirect to the new follow's own page: the thing the operator wants to read
    next is the rule they just wrote, rendered as the sentence the check will
    obey. A URL that was already followed lands on the same page, because the
    tool returns the existing follow rather than making a second one.
    """
    refusal = await _guard(request)
    if refusal is not None:
        return refusal

    deps: Deps = request.app.state.assembled.deps
    form = await request.form()
    result = await follows_tool.follow_channel(
        deps,
        url=str(form.get("url") or "").strip(),
        action="follow",
        title=str(form.get("title") or "").strip() or None,
        **_follow_rule_form(form),
    )
    error = _tool_error(result)
    if error is not None:
        return _follow_error(request, error)
    payload = result.structured_content or {}
    slug = str((payload.get("follow") or {}).get("slug") or "")
    return _see(f"{ROOT}/following/{slug}" if slug else f"{ROOT}/following")


async def _follow_action(request: Request, action: str, back: str) -> Response:
    """`pause`, `resume`, `check_now` and `unfollow`, which differ only in a word.

    The follow is resolved by slug here and handed to the tool as its stored
    source URL, so the tool's own resolver sees the string it wrote rather than
    a path segment this surface invented.

    The caller has already run :func:`_guard`; this is the half after it, so a
    handler that reads its form first cannot end up checking the credential
    twice — or, worse, once too late.
    """
    assembled = request.app.state.assembled
    slug = str(request.path_params["slug"])
    row = await assembled.db.read(lambda c: follows_store.by_slug(c, slug))
    if row is None:
        return _follow_error(request, _no_such_follow(slug))
    result = await follows_tool.follow_channel(
        assembled.deps, url=str(row["source_url"]), action=action
    )
    error = _tool_error(result)
    if error is not None:
        return _follow_error(request, error, slug)
    return _see(back.format(slug=slug))


def _no_such_follow(slug: str) -> dict[str, Any]:
    return {
        "code": "E_UNKNOWN_FOLLOW",
        "message": f'"{slug}" is not a follow on this instance.',
        "next": "the Following page lists every channel this index watches.",
    }


async def follow_state(request: Request) -> Response:
    """`POST /dashboard/following/{slug}/state` — pause or resume.

    One route, and the verb is in the body rather than in the path: pause and
    resume are the two directions of one control, and a surface with two URLs
    for them is a surface where a page can offer the wrong one.
    """
    refusal = await _guard(request)
    if refusal is not None:
        return refusal
    form = await request.form()
    wanted = str(form.get("action") or "")
    if wanted not in ("pause", "resume"):
        return _follow_error(
            request,
            {
                "code": "E_BAD_PARAM",
                "message": f'"{wanted}" is not pause or resume.',
                "next": "the two buttons on the follow's page are the whole vocabulary.",
            },
            str(request.path_params["slug"]),
        )
    return await _follow_action(request, wanted, ROOT + "/following/{slug}")


async def follow_check_now(request: Request) -> Response:
    """`POST /dashboard/following/{slug}/check` — make the clock due now.

    It does not run a check: it moves `next_check_at`, and the queue claims a
    `follow_check` job on its next tick. A paused follow stays paused, which is
    the store's rule and not this handler's.
    """
    refusal = await _guard(request)
    if refusal is not None:
        return refusal
    return await _follow_action(request, "check_now", ROOT + "/following/{slug}")


async def follow_delete(request: Request) -> Response:
    """`POST /dashboard/following/{slug}/delete` — unfollow.

    The videos it brought in stay: they are corpus, not membership
    (`follows/store.delete`). So this lands on the list rather than on a page
    that no longer exists.
    """
    refusal = await _guard(request)
    if refusal is not None:
        return refusal
    return await _follow_action(request, "unfollow", ROOT + "/following")


async def follow_rules(request: Request) -> Response:
    """`POST /dashboard/following/{slug}/rules` — the edit disclosure.

    The one write here that is not a `follow_channel` action, because the tool
    has none: `action="follow"` on a URL already followed deliberately returns
    the existing follow and creates nothing, which is what makes a retried
    request safe. So the edit goes through the validator both callers share —
    `follows/params.build_rules`, whose module docstring names this file — and
    then through `store.update_rules`, which refuses a column that is not a
    rule rather than ignoring it.
    """
    refusal = await _guard(request)
    if refusal is not None:
        return refusal

    assembled = request.app.state.assembled
    slug = str(request.path_params["slug"])
    row = await assembled.db.read(lambda c: follows_store.by_slug(c, slug))
    if row is None:
        return _follow_error(request, _no_such_follow(slug))
    form = await request.form()
    try:
        rules = follow_params.build_rules(**_follow_rule_form(form))
    except ToolError as exc:
        return _follow_error(request, _typed(exc), slug)
    collection_id = int(row["collection_id"])
    columns = follow_params.rule_columns(rules)
    await assembled.db.write(
        lambda c: follows_store.update_rules(c, collection_id, columns)
    )
    return _see(f"{ROOT}/following/{slug}")


async def follow_queue(request: Request) -> Response:
    """`POST /dashboard/following/{slug}/queue` — "Index anyway", one row.

    The ledger's whole argument is that a rule which passed something over is
    reversible by the person who wrote it, so this is the button that reverses
    it. `expand=none` because the row is one video and a channel URL that
    expanded here would queue a surprise; the follow's own `channels` and `tags`
    because a video rescued from the ledger should be built the way the follow
    would have built it, and filed where the follow files things.
    """
    refusal = await _guard(request)
    if refusal is not None:
        return refusal

    assembled = request.app.state.assembled
    slug = str(request.path_params["slug"])
    row = await assembled.db.read(lambda c: follows_store.by_slug(c, slug))
    if row is None:
        return _follow_error(request, _no_such_follow(slug))
    form = await request.form()
    url = str(form.get("url") or "").strip()
    back = f"{ROOT}/following/{slug}#passed"
    if not url:
        return _see(back)

    rules = FollowRules.from_row(row)
    result = await indexing.index_video(
        assembled.deps,
        url=url,
        expand="none",
        channels=rules.channels or "all",
        tags=", ".join(rules.tags) or None,
    )
    error = _tool_error(result)
    if error is not None:
        return _follow_error(request, error, slug)
    job_id = (result.structured_content or {}).get("job_id")
    return _see(f"{ROOT}/jobs/{job_id}" if job_id else back)
