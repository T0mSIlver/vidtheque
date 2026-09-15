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

Since 2026-09-05 every handler here answers **two** ways from one URL (§21):
the 303 a form navigation expects, or — when the caller's `Accept` prefers it —
the typed outcome the React pages read. Same route, same guard, same Origin
rule, same rate bucket; :func:`_accepts_json` is the only thing that differs,
and it is one function so the thirteen writes cannot drift apart.

**A refusal is the same envelope in both media** (2026-09-05). It used to be a
rendered error page when the caller asked for HTML; there is no error page any
more, so `_refusal_json` serves both branches and the code, the message and the
`next:` line — the policy text this side keeps — leave in one shape. The 303 is
now a *success* branch only: the sign-in page and the sign-out button are real
`<form method="post">`s, a submit before React has hydrated is a navigation,
and a navigation needs somewhere to land. Those targets are paths Next serves.
"""

from __future__ import annotations

import hmac
import json
import re
import secrets
import time
from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response

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
from .access import auth_required, bad_origin, origin_ok, require_write
from .api import NO_STORE
from .read_models import follow_row_json_with_error
from .read_models import tool_error as _tool_error
from .settings import ROOT

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
# none) is the string `all` — the tool's own word, not a synonym. Public,
# because the follow form ticks the same three boxes for the same parameter and
# a second copy of the labels is how one thing gets described two ways.
CHANNEL_BOXES = (
    ("transcript", "Transcript", "what was said, from the audio or the captions"),
    ("ocr", "On-screen text", "what the frames read, per keyframe"),
    ("frames", "Frame embeddings", "visual search over the keyframes"),
)


# ---------------------------------------------- one URL, two answers (§21)
#
# The React dashboard writes with `fetch` and needs the outcome *inline* — a
# cancel whose only evidence is the next 2 s poll is a button that looks
# broken. A redirect cannot carry that, and a second `/dashboard/api/*` POST
# route per write would be two routes to guard, to bucket and to keep in step.
# So the medium is negotiated and everything else is shared.


def _quality(params: str) -> float:
    """The `q=` of one `Accept` entry, defaulting to 1.0 as the RFC does."""
    for param in params.split(";"):
        name, _, value = param.partition("=")
        if name.strip().lower() == "q":
            try:
                return float(value.strip())
            except ValueError:
                return 0.0
    return 1.0


def _accepts_json(request: Request) -> bool:
    """Does this caller *prefer* JSON to a page?

    Strict in both directions, because the redirect is the older contract and
    must not be lost by accident. `application/json` has to be **named** — a
    browser's `*/*`, and `fetch`'s own default, is a request for anything and
    not for a typed outcome — and it has to outrank `text/html`, which a
    top-level form navigation always sends first. So `application/json` alone
    is JSON, `application/json, text/html;q=0.9` is JSON, and Chrome's
    `text/html,...,*/*;q=0.8` is the page it has always been.
    """
    json_q: float | None = None
    html_q: float | None = None
    for part in request.headers.get("accept", "").split(","):
        media, _, params = part.strip().partition(";")
        media = media.strip().lower()
        if media not in ("application/json", "text/html"):
            continue
        q = _quality(params)
        if media == "application/json":
            json_q = q if json_q is None else max(json_q, q)
        else:
            html_q = q if html_q is None else max(html_q, q)
    if json_q is None or json_q <= 0:
        return False
    return html_q is None or json_q > html_q


def _json(payload: dict[str, Any], status: int = 200) -> JSONResponse:
    """A write's outcome, under the read side's own cache rule."""
    return JSONResponse(payload, status_code=status, headers=NO_STORE)


def _envelope(error: dict[str, Any]) -> dict[str, Any]:
    """A typed refusal as the JSON surfaces spell it: `code` is named `error`.

    `retry_after_s` rides along only when the refusal named a delay —
    `ToolError.structured()` always writes the key and mostly writes `None`,
    and a null on every envelope is a field a client learns to ignore.
    """
    payload: dict[str, Any] = {
        "error": str(error.get("code") or "E_INTERNAL"),
        "message": error.get("message"),
        "next": error.get("next"),
    }
    if error.get("retry_after_s") is not None:
        payload["retry_after_s"] = int(error["retry_after_s"])
    if error.get("urls"):  # which batch of the index form this one refused
        payload["urls"] = [str(url) for url in error["urls"]]
    return payload


def _refusal_json(
    error: dict[str, Any], *, echo: dict[str, Any] | None = None
) -> JSONResponse:
    """The envelope, at the status `errors.HTTP_STATUS` maps the code to.

    The header goes out beside `retry_after_s` because a client that reads the
    status and not the body still obeys `Retry-After`.

    `echo` is what the handler had already resolved when it refused, merged in
    under the names its success payload uses — today only the index form's
    `accepted` (§21). A refusal that drops it leaves the form echoing what was
    typed, which is the one reading the server has just contradicted.
    """
    payload = _envelope(error)
    if echo:
        payload.update(echo)
    headers = dict(NO_STORE)
    if "retry_after_s" in payload:
        headers["Retry-After"] = str(payload["retry_after_s"])
    return JSONResponse(
        payload, status_code=HTTP_STATUS.get(payload["error"], 500), headers=headers
    )


def _outcome(
    request: Request, payload: dict[str, Any], *, back: str, status: int = 200
) -> Response:
    """The typed outcome, or the 303 a form navigation expects. Never both."""
    if _accepts_json(request):
        return _json(payload, status)
    return _see(back)


def _safe_next(raw: str | None) -> str:
    """A redirect target that cannot leave this surface.

    Only a path under `/dashboard`, and never `//host` — a protocol-relative
    URL is an absolute one wearing a path's clothes, and an open redirect on
    the page that mints the session cookie is the worst place to have one.
    """
    if not raw or not raw.startswith(ROOT) or raw.startswith("//"):
        return ROOT
    return raw


def _see(path: str) -> RedirectResponse:
    """POST → 303 → GET. A write is never the thing a reload repeats.

    The 303 is the *success* branch's, and only its: a form navigation that
    landed here before React hydrated needs a page to arrive at, and every
    target below is a path Next serves. A refusal takes `_refusal_json`
    whichever medium asked (§21, 2026-09-05) — the 401 included, which is what
    sends a signed-out shell to the sign-in page.
    """
    return RedirectResponse(path, status_code=303)


# --------------------------------------------------------------------- login


# One sentence for both secrets. "Wrong password" against a deployment that
# accepts the token instead would be a hint about which secret exists. It was
# the rendered form's sentence first and it is the envelope's now, unchanged:
# which secret a deployment holds is `/dashboard/api/session`'s answer, and a
# refusal must not be a second one.
BAD_SECRET = "That secret does not match this instance."


def _bad_credential() -> JSONResponse:
    """A refused sign-in, typed — and deliberately **not** `E_AUTH_REQUIRED`.

    Every other 401 on this surface means "go and sign in", and the React shell
    acts on it by navigating to the sign-in page (§21, frontend-migration.md
    §1d). On the sign-in page's own POST that rule is a loop, so the refusal
    carries its own code and the shell can tell "your session went" from "that
    secret is wrong" without knowing which route it called.
    """
    return _refusal_json(
        {
            "code": "E_BAD_CREDENTIAL",
            "message": BAD_SECRET,
            # Neutral about which secret this deployment holds: the page that
            # asked already says, and the refusal must not be a second answer.
            "next": "the sign-in page names which secret this deployment accepts.",
        }
    )


async def login(request: Request) -> Response:
    """`POST /dashboard/login` — the secret, once, for the existing cookie.

    Registered only where the write side is (`write_side_enabled`): a sign-in
    that grants nothing is a probe magnet with a password field on it.

    The thirteenth write to answer two ways (§21): the 303 a form navigation
    expects, or `{"signed_in": true, "next": …}` carrying the same `Set-Cookie`
    — on the response either way, because a React shell cannot mint an
    `HttpOnly` cookie itself, which is why this write stays Python's for good.
    Both refusals are the envelope now, in either medium: there is no form left
    to carry a sentence back into.

    **The `GET` went with the Jinja page on 2026-09-06.** The sign-in page is
    Next's, "am I signed in" is `/dashboard/api/session`'s question, and which
    secret this deployment accepts is that endpoint's answer too — so nothing
    was left for a `GET` here to do but be probed.
    """
    assembled = request.app.state.assembled
    settings = assembled.settings
    store = assembled.auth.store

    form = await request.form()
    next_url = _safe_next(str(form.get("next") or ""))
    # The login is a state change (it mints a session), so it carries the same
    # Origin rule as every other write. It cannot carry the credential half —
    # not having one is the point of the page.
    if not origin_ok(request):
        # `access.bad_origin()` verbatim, so there is one origin refusal on this
        # surface and not two. It used to be one wording for a client reading a
        # code and a second, softer sentence on the rendered form; the form is
        # gone and so is the second sentence.
        refusal = bad_origin()
        refusal.headers.update(NO_STORE)
        return refusal
    if store is None:  # pragma: no cover - every write-side mode builds one
        return auth_required(settings.auth_mode)

    supplied = str(form.get("password") or "")
    if not _accepted(settings, supplied):
        return _bad_credential()

    sid = secrets.token_urlsafe(32)
    ttl = settings.login_session_ttl_s
    store.save_session(sid, OWNER_SUBJECT, int(time.time()) + ttl)
    # `next` rides in the payload rather than in a `Location`: the shell that
    # asked for JSON navigates itself, and it goes to the target `_safe_next`
    # already fenced — the same fence the 303 branch redirects to.
    response = _outcome(request, {"signed_in": True, "next": next_url}, back=next_url)
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

    JSON outcome: ``{"signed_out": true}``. The `Set-Cookie` is on both
    branches, because it is the *response* that ends the session and a React
    shell cannot clear an `HttpOnly` cookie itself (§19's `has_session_cookie`
    exists for the other half of that).
    """
    refusal = await require_write(request)
    if refusal is not None:
        return refusal
    store = request.app.state.assembled.auth.store
    if store is not None:
        store.delete_session(request.cookies.get(SESSION_COOKIE))
    response = _outcome(request, {"signed_out": True}, back=f"{ROOT}/login")
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


# ---------------------------------------------------------------- §5.5 index


async def index_submit(request: Request) -> Response:
    """`POST /dashboard/index` → `index_video` → the jobs view.

    The one thing the form handles that the tool does not: a real batch. §10.7
    keeps the ten-URL cap on the MCP surface and splits here instead, server
    side, and says so on the page — a split the operator cannot see is a job
    count they cannot explain.

    The outcome is the accepted queue entries, the ids already in the corpus
    and a refusal per batch that failed. The one-job shortcut is a *redirect*,
    so it belongs to the form navigation alone: a client that always reads
    `jobs` is a client with no special case.
    """
    refusal = await require_write(request)
    if refusal is not None:
        return refusal

    deps: Deps = request.app.state.assembled.deps
    form = await request.form()
    submitted = _submitted(form)
    tokens = [t for t in _SEPARATORS.split(str(form.get("urls") or "")) if t]

    if not tokens:
        return _index_refusal(
            {
                "code": "E_BAD_PARAM",
                "message": "Paste at least one video, playlist or channel URL.",
                "next": "a bare 11-character YouTube id works too.",
            },
            submitted,
        )
    if len(tokens) > MAX_FORM_URLS:
        return _index_refusal(
            {
                "code": "E_TOO_LARGE",
                "message": f"{len(tokens)} URLs is past this form's cap of "
                f"{MAX_FORM_URLS}.",
                "next": "submit it in parts, or point one job at the playlist.",
            },
            submitted,
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

    # One job and nothing to explain: a form navigation goes straight to the
    # thing that is now happening (§5.5). Anything else used to render a
    # receipt page with the form still under it for the next batch, and there
    # is no form — so the receipt is the answer in either medium, which is also
    # the only shape that can carry a refusal *per batch*.
    if not _accepts_json(request) and len(jobs) == 1 and not errors and not already:
        return _see(f"{ROOT}/jobs/{jobs[0]['job_id']}")
    return _json(
        {
            "jobs": jobs,
            "already_indexed": already,
            "errors": [_envelope(e) for e in errors],
            "batches": len(batches),
            "urls": len(tokens),
            # What the server actually ran on, which is not always what was
            # typed: `max_items` is clamped to the tool's own 1..200 and the
            # two vocabularies fall back to their defaults rather than being
            # refused. The Jinja form re-rendered these three, so a reader who
            # typed `max_items=9000` saw the 200 that was used; a form that
            # keeps its own state has nothing to read them back out of, and a
            # clamp nobody is shown is a clamp that looks like a bug.
            "accepted": _accepted_block(submitted),
        },
        200 if jobs or already else 409,
    )


def _accepted_block(submitted: dict[str, Any]) -> dict[str, Any]:
    """The three values the server resolved, whatever the outcome was.

    The resolved half of `_submitted` and nothing else: what was *typed* is the
    client's own state, and only the values the server changed are the
    server's to report.
    """
    return {
        "expand": submitted["expand"],
        "max_items": submitted["max_items"],
        "priority": submitted["priority"],
    }


def _index_refusal(error: dict[str, Any], submitted: dict[str, Any]) -> JSONResponse:
    """A refused submission, carrying the same `accepted` block as a receipt.

    Both of this form's own refusals are raised *after* `_submitted` has
    resolved the three, so the block is the same block — an over-limit
    `max_items` still resolves to the tool's 200, and a form told "that URL
    list is too long" must not also be left showing the 9000 it typed. The
    guard's refusals above (`require_write`, the Origin rule, the bucket) carry
    the bare envelope: nothing was parsed when they answered.
    """
    return _refusal_json(error, echo={"accepted": _accepted_block(submitted)})


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

    JSON outcome: ``{"job_id", "state", "cancel_requested"}`` — the store's own
    word for the state the job is in *now*, so a client can tell the queued job
    that is already `cancelled` from the running one that is still `running`
    with the request recorded. That distinction is the reason this route
    answers inline at all.
    """
    refusal = await require_write(request)
    if refusal is not None:
        return refusal

    job_id = request.path_params["job_id"]
    outcome = await request.app.state.assembled.db.write(
        lambda c: jobs_store.request_cancel(c, job_id)
    )
    if outcome is None:
        return _refusal_json(
            {
                "code": "E_UNKNOWN_JOB",
                "message": f'"{job_id}" is not a job on this instance.',
                "next": "the jobs table lists every job this index has run.",
            }
        )
    accepted, state = outcome
    if not accepted:
        return _refusal_json(
            {
                "code": "E_BAD_PARAM",
                "message": f'Job "{job_id}" is already {state}.',
                "next": "only queued or running jobs can be cancelled.",
            }
        )
    return _outcome(
        request,
        {"job_id": job_id, "state": state, "cancel_requested": True},
        back=f"{ROOT}/jobs/{job_id}",
    )


async def retry_job(request: Request) -> Response:
    """Requeue only a finished job's failed or degraded items.

    Selection is one bounded store read. Creation deliberately goes back
    through ``tools.indexing.index_video`` in batches of at most ten, preserving
    the original channels, tags, expansion bound and priority while leaving
    successful items out of the call entirely.

    The outcome is the jobs this made, what it selected them from, and what it
    preserved. Like the index form it takes the one-job redirect shortcut only
    on the branch a browser navigated in on.
    """
    refusal = await require_write(request)
    if refusal is not None:
        return refusal

    assembled = request.app.state.assembled
    old_job_id = request.path_params["job_id"]
    selected = await assembled.db.read(
        lambda c: jobs_store.retry_candidates(c, old_job_id, MAX_FORM_URLS + 1)
    )
    if selected is None:
        return _refusal_json(
            {
                "code": "E_UNKNOWN_JOB",
                "message": f'"{old_job_id}" is not a job on this instance.',
                "next": "the jobs table lists every job this index has run.",
            }
        )
    job, candidates = selected
    state = str(job["state"])
    if state in ("queued", "running"):
        return _refusal_json(
            {
                "code": "E_BAD_PARAM",
                "message": f'Job "{old_job_id}" is still {state}.',
                "next": "retry is available after the original job finishes.",
            }
        )
    if str(job["kind"]) not in ("index", "reindex"):
        return _refusal_json(
            {
                "code": "E_BAD_PARAM",
                "message": f'Job "{old_job_id}" is a {job["kind"]} job.',
                "next": "only indexing jobs can be repaired through index-video.",
            }
        )
    if len(candidates) > MAX_FORM_URLS:
        return _refusal_json(
            {
                "code": "E_TOO_LARGE",
                "message": f"More than {MAX_FORM_URLS} items need repair.",
                "next": "retry the affected videos in smaller batches from the index form.",
            }
        )
    if not candidates:
        return _refusal_json(
            {
                "code": "E_BAD_PARAM",
                "message": f'Job "{old_job_id}" has no failed or degraded items.',
                "next": "successful items are deliberately not re-queued.",
            }
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

    preserved = {
        "channels": channels,
        "tags": [t for t in tags_csv.split(",") if t],
        "priority": priority,
    }
    # `index_submit`'s rule, for the same reason: one job and nothing to
    # explain sends a form navigation straight to the thing that is now
    # happening, and the POST is never left as the page a reload would repeat
    # — a reloaded retry is a duplicate repair job, the one write on this
    # surface where that is not merely noise.
    if not _accepts_json(request) and len(jobs) == 1 and not errors:
        return _see(f"{ROOT}/jobs/{jobs[0]['job_id']}")
    return _json(
        {
            "from_job_id": old_job_id,
            "selected": len(candidates),
            "jobs": jobs,
            "errors": [_envelope(e) for e in errors],
            "preserved": preserved,
        },
        200 if jobs else 409,
    )


async def reindex(request: Request) -> Response:
    """`POST /dashboard/videos/{video_id}/reindex` — force, one video.

    `force_reindex=true` on the video's own URL with `expand=none`: this button
    is about *this* row, and a playlist URL that expanded here would queue a
    surprise.

    JSON outcome: ``{"video_id", "job_id"}``.
    """
    refusal = await require_write(request)
    if refusal is not None:
        return refusal

    assembled = request.app.state.assembled
    video_id = request.path_params["video_id"]
    row = await assembled.db.read(lambda c: queries.lookup_video(c, video_id))
    if row is None:
        return _refusal_json(
            {
                "code": "E_UNKNOWN_VIDEO",
                "message": f'"{video_id}" is not in the corpus.',
                "next": "browse the videos table for what is indexed.",
            }
        )

    result = await indexing.index_video(
        assembled.deps, url=str(row["url"]), expand="none", force_reindex=True
    )
    error = _tool_error(result)
    if error is not None:
        return _refusal_json(error)
    job_id = (result.structured_content or {}).get("job_id")
    if not job_id:  # pragma: no cover - force_reindex always creates a job
        return _outcome(
            request,
            {"video_id": video_id, "job_id": None},
            back=f"{ROOT}/videos/{video_id}",
        )
    return _outcome(
        request,
        {"video_id": video_id, "job_id": str(job_id)},
        back=f"{ROOT}/jobs/{job_id}",
    )


async def set_tags(request: Request) -> Response:
    """`POST /dashboard/videos/{video_id}/tags` — the same `tag_video` the tool calls.

    Namespace rules, the ten-tag cap and the `<ns>:<value>` shape are the
    tool's, verbatim, including its error text: a tag this refuses here is a
    tag it would refuse there, and the page says so in the same words.

    JSON outcome: ``{"video_id", "tags"}`` — the video's tags *after* the
    write, read back rather than derived from what was asked for. `tag_video`
    reports what it added and removed across a batch, which is not the same
    question as "what does this row carry now", and the panel that made the
    call is showing exactly the second one.
    """
    refusal = await require_write(request)
    if refusal is not None:
        return refusal

    deps: Deps = request.app.state.assembled.deps
    video_id = request.path_params["video_id"]
    form = await request.form()
    add = _tags(str(form.get("add") or ""))
    remove = _tags(str(form.get("remove") or ""))
    back = f"{ROOT}/videos/{video_id}#manage"

    # Nothing asked for is nothing done, on both branches: the form's policy,
    # not a second one invented for the JSON caller.
    if not add and not remove:
        return await _tags_outcome(request, video_id, back)
    result = await library.tag_video(deps, video_id=video_id, add=add, remove=remove)
    error = _tool_error(result)
    if error is not None:
        return _refusal_json(error)
    return await _tags_outcome(request, video_id, back)


async def _tags_outcome(request: Request, video_id: str, back: str) -> Response:
    """The row's tags now, for a JSON caller — and one read fewer for the page."""
    if not _accepts_json(request):
        return _see(back)

    def read(conn: Any) -> list[str]:
        internal = queries.lookup_video_ids(conn, [video_id]).get(video_id)
        if internal is None:  # pragma: no cover - tag_video refused it already
            return []
        return queries.video_tags(conn, [internal]).get(internal, [])

    tags = await request.app.state.assembled.db.read(read)
    return _json({"video_id": video_id, "tags": tags})


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

    JSON outcome: ``{"follow", "already_following"}``. The second field is the
    tool's own, and it is the difference between "made" and "you had this
    already" that a redirect cannot express.
    """
    refusal = await require_write(request)
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
        return _refusal_json(error)
    payload = result.structured_content or {}
    slug = str((payload.get("follow") or {}).get("slug") or "")
    if _accepts_json(request):
        row = None
        if slug:
            row = await request.app.state.assembled.db.read(
                lambda c: follows_store.by_slug(c, slug)
            )
        return _json(
            {
                "follow": _follow_payload(row) if row is not None else None,
                "already_following": bool(payload.get("already_following")),
            }
        )
    return _see(f"{ROOT}/following/{slug}" if slug else f"{ROOT}/following")


async def _follow_action(request: Request, action: str, back: str) -> Response:
    """`pause`, `resume`, `check_now` and `unfollow`, which differ only in a word.

    The follow is resolved by slug here and handed to the tool as its stored
    source URL, so the tool's own resolver sees the string it wrote rather than
    a path segment this surface invented.

    The caller has already run :func:`_guard`; this is the half after it, so a
    handler that reads its form first cannot end up checking the credential
    twice — or, worse, once too late.

    JSON outcome: the follow row as it stands after the action, except for
    `unfollow`, where there is no row left to send — that one answers
    ``{"slug", "deleted", "videos_kept"}``, the count being the tool's own
    receipt for the videos an unfollow deliberately leaves in the corpus.
    """
    assembled = request.app.state.assembled
    slug = str(request.path_params["slug"])
    row = await assembled.db.read(lambda c: follows_store.by_slug(c, slug))
    if row is None:
        return _refusal_json(_no_such_follow(slug))
    result = await follows_tool.follow_channel(
        assembled.deps, url=str(row["source_url"]), action=action
    )
    error = _tool_error(result)
    if error is not None:
        return _refusal_json(error)
    # A `check_now` the scheduler will not act on is refused rather than
    # answered with an unchanged row. The tool's own medium says it with
    # `scheduled: false` beside the row; a `200` here would print "clock
    # moved" about a clock nothing will read. The line and the way out are
    # the tool's own (`follows_tool.check_now_refusal`), so both media refuse
    # with one code and one message (§21, 2026-09-15).
    if action == "check_now" and (result.structured_content or {}).get("scheduled") is False:
        return _refusal_json(follows_tool.check_now_refusal(row))
    if _accepts_json(request):
        if action == "unfollow":
            payload = result.structured_content or {}
            return _json(
                {
                    "slug": slug,
                    "deleted": True,
                    "videos_kept": int(payload.get("videos_kept") or 0),
                }
            )
        return await _follow_json(request, slug)
    return _see(back.format(slug=slug))


# One follow row, typed, with the last failure on it — the outcome every follow
# write here answers with, and the block `/dashboard/api/following/{slug}`
# answers with as well. It lives in `read_models.py` since 2026-09-05, for that
# second caller, and is imported back under the name this module has always
# used: a read and a write outcome must describe a follow identically, and they
# do so by being one function.
#
# The failure is on it because `set_state` clears `last_error_code` and
# `last_error_message` when it resumes a follow. An outcome that carried
# neither left the page showing an error the write had just cleared, so the
# page re-read after every write; with the two columns here the row a write
# answers with is complete (§21, 2026-09-05).
_follow_payload = follow_row_json_with_error


async def _follow_json(request: Request, slug: str) -> Response:
    """The follow as it stands after the write, re-read rather than assumed.

    `set_state` re-arms the clock when it resumes, so a payload built from the
    row this handler read *before* the tool ran would name the new state and
    the old `next_check_at` in one breath. The tool re-reads for its own
    payload for exactly this reason; so does this.
    """
    row = await request.app.state.assembled.db.read(
        lambda c: follows_store.by_slug(c, slug)
    )
    if row is None:  # pragma: no cover - the write above just wrote it
        return _refusal_json(_no_such_follow(slug))
    return _json({"follow": _follow_payload(row)})


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
    refusal = await require_write(request)
    if refusal is not None:
        return refusal
    form = await request.form()
    wanted = str(form.get("action") or "")
    if wanted not in ("pause", "resume"):
        return _refusal_json(
            {
                "code": "E_BAD_PARAM",
                "message": f'"{wanted}" is not pause or resume.',
                "next": "the two buttons on the follow's page are the whole vocabulary.",
            }
        )
    return await _follow_action(request, wanted, ROOT + "/following/{slug}")


async def follow_check_now(request: Request) -> Response:
    """`POST /dashboard/following/{slug}/check` — make the clock due now.

    It does not run a check: it moves `next_check_at`, and the queue claims a
    `follow_check` job on its next tick. A paused follow stays paused, which is
    the store's rule and not this handler's.
    """
    refusal = await require_write(request)
    if refusal is not None:
        return refusal
    return await _follow_action(request, "check_now", ROOT + "/following/{slug}")


async def follow_delete(request: Request) -> Response:
    """`POST /dashboard/following/{slug}/delete` — unfollow.

    The videos it brought in stay: they are corpus, not membership
    (`follows/store.delete`). So this lands on the list rather than on a page
    that no longer exists.
    """
    refusal = await require_write(request)
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

    JSON outcome: the follow row, which carries every rule column — so a client
    that just wrote a rule reads back the rule the store kept, not the one it
    sent.
    """
    refusal = await require_write(request)
    if refusal is not None:
        return refusal

    assembled = request.app.state.assembled
    slug = str(request.path_params["slug"])
    row = await assembled.db.read(lambda c: follows_store.by_slug(c, slug))
    if row is None:
        return _refusal_json(_no_such_follow(slug))
    form = await request.form()
    try:
        rules = follow_params.build_rules(**_follow_rule_form(form))
    except ToolError as exc:
        return _refusal_json(_typed(exc))
    collection_id = int(row["collection_id"])
    columns = follow_params.rule_columns(rules)
    await assembled.db.write(
        lambda c: follows_store.update_rules(c, collection_id, columns)
    )
    if _accepts_json(request):
        return await _follow_json(request, slug)
    return _see(f"{ROOT}/following/{slug}")


async def follow_queue(request: Request) -> Response:
    """`POST /dashboard/following/{slug}/queue` — "Index anyway", one row.

    The ledger's whole argument is that a rule which passed something over is
    reversible by the person who wrote it, so this is the button that reverses
    it. `expand=none` because the row is one video and a channel URL that
    expanded here would queue a surprise; the follow's own `channels` and `tags`
    because a video rescued from the ledger should be built the way the follow
    would have built it, and filed where the follow files things.

    JSON outcome: ``{"slug", "url", "job_id"}``. An empty `url` is nothing to
    do on both branches — the form's policy, not a second one written for the
    JSON caller — and answers with a null `job_id`.
    """
    refusal = await require_write(request)
    if refusal is not None:
        return refusal

    assembled = request.app.state.assembled
    slug = str(request.path_params["slug"])
    row = await assembled.db.read(lambda c: follows_store.by_slug(c, slug))
    if row is None:
        return _refusal_json(_no_such_follow(slug))
    form = await request.form()
    url = str(form.get("url") or "").strip()
    back = f"{ROOT}/following/{slug}#passed"
    if not url:
        return _outcome(
            request, {"slug": slug, "url": None, "job_id": None}, back=back
        )

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
        return _refusal_json(error)
    job_id = (result.structured_content or {}).get("job_id")
    return _outcome(
        request,
        {"slug": slug, "url": url, "job_id": str(job_id) if job_id else None},
        back=f"{ROOT}/jobs/{job_id}" if job_id else back,
    )
