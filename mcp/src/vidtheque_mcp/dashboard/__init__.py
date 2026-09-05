"""`/dashboard` — the management surface, as a route group beside `public/`.

`docs/design/dashboard.md` is the contract. The decision it encodes: today the
management surface of vidtheque is the MCP server, and that is backwards. An
agent is the right consumer of the corpus and the wrong consumer of the
corpus's *plumbing* — nobody should have to ask a language model which model
transcribed a video or whether OCR quietly failed on forty of them.

**Python serves no page here as of 2026-09-06.** Every `GET /dashboard*` page
is Next's (`docs/design/frontend-migration.md` §1d), so what this group
registers is three things and nothing else: `/dashboard/api/*` — the JSON the
React pages read, plus the same handlers `/api/*` uses, which is the facade a
private deployment could not have before (demo-site.md §7.4) — the fourteen
`POST`s, and the `/dashboard/` → `/dashboard` redirect. There is no template,
no stylesheet and no asset route; the front end serves its own.

Those `/api/*` handlers were bounded by owner clamps *because of the prefix*
until phase 5 keyed them off the credential instead; see
`public/api.py:policy_for`.

Agents still use `/mcp`; this route group does not acquire a second query layer
or an agent-oriented endpoint.

It lives inside the mcp server because all state does (CLAUDE.md), and it never
speaks MCP: it calls `tools/*` and `db/queries.py` directly, exactly as `/api`
already does.
"""

from __future__ import annotations

from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.routing import Route

from ..public.api import api_routes
from . import api, writes
from .access import (
    WRITE_ROUTES,
    credential,
    origin_ok,
    peer_trusted,
    require_write,
    sign_in_hint,
    write_side_enabled,
)
from .settings import ROOT, DashboardSettings, refuse_proxy_origin_cidrs

__all__ = [
    "ROOT",
    "WRITE_ROUTES",
    "DashboardSettings",
    "credential",
    "dashboard_routes",
    "origin_ok",
    "require_write",
    "refuse_proxy_origin_cidrs",
    "write_side_enabled",
]


def dashboard_routes(*, write_side: bool = False) -> list[Route]:
    """The route group. Order matters only against ``Mount("/")`` in app.py.

    ``write_side`` is :func:`~.access.write_side_enabled`, resolved once by the
    caller from the auth mode and ``VIDTHEQUE_PUBLIC_READONLY``. When it is
    false **none of the write routes and no login page are in this list** —
    they 404 through the ``Mount("/")`` fallback like any other path this
    server does not serve, which is §2.3's rule and demo-site.md §1.1's
    argument: a route that exists and refuses is a route somebody probes.
    """

    def guarded(handler):
        """Read access: whatever `/frames/*` accepts, minus the signed URL.

        In `none` mode this is open, because the corpus is already open through
        `/mcp` and `/frames` in that mode — a dashboard that demanded a
        credential there would be theatre. In `token`/`oauth` it takes the
        bearer, the existing session cookie, **or a socket peer in
        `VIDTHEQUE_DASHBOARD_TRUSTED_CIDRS`** — the same peer the write gate
        already admits (dashboard.md §3.4: trusted CIDRs count as a
        credential; a network trusted to change the corpus but not to read it
        would be a boundary with no shape). Until 2026-08-13 only the write
        gate honored it, so a trusted LAN peer could submit an index job and
        not view the page it posted from. On a public deployment this clause
        is inert: G2a requires the CIDR list empty there, and the settings
        refuse to boot when a CIDR covers the proxy path. The refusal names
        the accepted credentials, rather than making the owner guess.

        The refusal is the envelope, and there is no second branch as of
        2026-09-06: it used to render a sign-in page when the caller asked for
        HTML, because the caller could be a browser navigating to a page. A
        browser never navigates to `/dashboard/api/*` — the shell fetches it —
        so the 401 is the signal and the shell decides what to do with it
        (dashboard.md §21, DECISIONS.md 2026-09-05).
        """

        async def wrapper(request: Request) -> Response:
            if await credential(request) is not None or peer_trusted(request):
                return await handler(request)
            mode = request.app.state.assembled.settings.auth_mode
            return JSONResponse(
                {
                    "error": "E_AUTH_REQUIRED",
                    "message": "The dashboard needs the owner's token or session.",
                    # `write_side` rather than a constant: the sign-in page is
                    # not registered in every deployment that refuses.
                    "next": sign_in_hint(mode, login=write_side),
                },
                status_code=401,
                # A refused read of the owner surface describes nothing stable
                # enough to cache; `api.NO_STORE` is the same header every
                # other `/dashboard/api/*` response carries.
                headers=api.NO_STORE,
            )

        wrapper.__name__ = getattr(handler, "__name__", "guarded")
        return wrapper

    async def trailing_slash(request: Request) -> Response:
        """`/dashboard/` is `/dashboard`, not a 404.

        Starlette's own `redirect_slashes` never fires here: `Mount("/")` is
        the last route and it matches everything, so the router finds a handler
        for `/dashboard/` before it ever considers a redirect. Typing the slash
        is not a mistake worth a 404 — and an unguarded redirect leaks nothing,
        so it sits outside the credential check. The page it redirects to is
        Next's; this server only refuses to 404 the slash.
        """
        query = request.url.query
        return RedirectResponse(f"{ROOT}?{query}" if query else ROOT, status_code=308)

    # The write side, or nothing at all. Declared here rather than inline so
    # the list and `WRITE_ROUTES` can be asserted against each other: a write
    # route that forgets to declare itself fails the suite (§2.5.4).
    write_routes: list[Route] = (
        [
            # The sign-in write is a write route by the same predicate as the
            # rest, and for the same reason (§3.2 rule 3): a sign-in that
            # grants nothing is a probe magnet with a password field on it.
            # Its `GET` went with the Jinja page on 2026-09-06 — the page is
            # Next's, and "am I signed in" is `/dashboard/api/session`'s
            # question — so this path is a `POST` like every other write.
            Route(f"{ROOT}/login", writes.login, methods=["POST"]),
            Route(f"{ROOT}/logout", writes.logout, methods=["POST"]),
            Route(f"{ROOT}/index", writes.index_submit, methods=["POST"]),
            Route(
                f"{ROOT}/jobs/{{job_id}}/cancel",
                writes.cancel_job,
                methods=["POST"],
            ),
            Route(
                f"{ROOT}/jobs/{{job_id}}/retry",
                writes.retry_job,
                methods=["POST"],
            ),
            Route(
                f"{ROOT}/videos/{{video_id}}/reindex",
                writes.reindex,
                methods=["POST"],
            ),
            Route(
                f"{ROOT}/videos/{{video_id}}/tags",
                writes.set_tags,
                methods=["POST"],
            ),
            Route(f"{ROOT}/following", writes.follow_create, methods=["POST"]),
            # The following reads are in *this* list rather than beside the
            # other reads, and deliberately: §18.6 puts the reads of a
            # write-only surface under the write-side predicate, so a
            # deployment that registers no writes has no following endpoint
            # either. A JSON route that answered where its page 404s would be
            # a way back into a surface the deployment decided not to serve,
            # which is the probe §2.3 exists to refuse. The two pages this
            # rule was written for are Next's now and the rule outlived them:
            # a shell that can read a follow is a shell that can offer to
            # change one.
            Route(f"{ROOT}/api/following", guarded(api.following), methods=["GET"]),
            Route(
                f"{ROOT}/api/following/{{slug}}",
                guarded(api.follow),
                methods=["GET"],
            ),
            Route(
                f"{ROOT}/following/{{slug}}/state",
                writes.follow_state,
                methods=["POST"],
            ),
            Route(
                f"{ROOT}/following/{{slug}}/check",
                writes.follow_check_now,
                methods=["POST"],
            ),
            Route(
                f"{ROOT}/following/{{slug}}/rules",
                writes.follow_rules,
                methods=["POST"],
            ),
            Route(
                f"{ROOT}/following/{{slug}}/delete",
                writes.follow_delete,
                methods=["POST"],
            ),
            Route(
                f"{ROOT}/following/{{slug}}/queue",
                writes.follow_queue,
                methods=["POST"],
            ),
        ]
        if write_side
        else []
    )

    return [
        Route(f"{ROOT}/", trailing_slash, methods=["GET"]),
        # Ahead of the reads. `/api/following/{slug}` and
        # `/following/{slug}/state` differ in segment count so neither can
        # shadow the other today, but the ordering means a future read route
        # with a greedier converter cannot quietly swallow a POST either.
        *write_routes,
        # The same handlers `/api/*` uses — behind the same gate as everything
        # else here, because JSON that skips the credential check is the hole
        # this prefix was guarded against, and under whatever clamps the
        # *caller* earns.
        #
        # The clamps used to be pinned to `OWNER_CLAMPS` here, which was right
        # only while the prefix implied the caller. It does not in `AUTH=none`,
        # where the gate above is open by design: `public/api.py:policy_for`
        # now resolves the policy per request, so this prefix grants a wider
        # bound to a bearer or a session and the demo's bound to everyone else
        # (phase 5; `docs/deploy-public.md`'s clamp audit item).
        *[
            Route(route.path, guarded(route.endpoint), methods=["GET"])
            for route in api_routes(ROOT, ask=False)
        ],
        # The React dashboard's own reads (`docs/design/frontend-migration.md`,
        # 2026-09-05). `/api/*` answers questions about the *corpus* in the
        # corpus's own shape; these answer "what does this box hold" and "what
        # is it behind on" — the overview's and the ledger's own reads
        # (`read_models`), typed. Same gate, and no clamp to state: they take
        # no parameter, so the assemblers' caps are the only bounds there are.
        Route(f"{ROOT}/api/overview", guarded(api.overview), methods=["GET"]),
        Route(f"{ROOT}/api/ledger", guarded(api.ledger), methods=["GET"]),
        # The videos table and the video detail, same argument and same gate
        # (§20). **Not** `/api/videos`: that path at this prefix is the
        # facade's listing, two routes up, and its records are the corpus's own
        # shape with `published` and `duration` already rendered for a reader of
        # the tool's text block. These answer what the *management surface*
        # shows — index state, coverage, the exact filtered count, the stage
        # table, the keyframe strip — so they are a second question, not a
        # second copy, and they get a name of their own rather than shadowing
        # an answer somebody already depends on.
        Route(f"{ROOT}/api/library", guarded(api.videos), methods=["GET"]),
        Route(
            f"{ROOT}/api/library/{{video_id}}",
            guarded(api.video),
            methods=["GET"],
        ),
        # **Outside the gate, deliberately.** It carries no corpus and no
        # secret, and a signed-out browser has to be able to ask whether this
        # deployment has a sign-in page at all — `guarded` would make the answer
        # to "am I signed in?" require being signed in.
        Route(f"{ROOT}/api/session", api.session, methods=["GET"]),
        # The jobs view's own poll target. Not one of `api_routes`' handlers
        # because `/api/*` answers questions about the *corpus* and this one
        # answers a question about the machine — but the same prefix, the same
        # gate and the same clamps, because a JSON route that skips either is
        # the hole this prefix was guarded against.
        Route(f"{ROOT}/api/jobs", guarded(api.jobs_json), methods=["GET"]),
        Route(
            f"{ROOT}/api/jobs/{{job_id}}",
            guarded(api.job_json),
            methods=["GET"],
        ),
        # The transcript pane's own source (2026-08-10). Same argument as the
        # two above: `/api/*` answers questions about the corpus in the corpus's
        # own shape, and this answers "the next batch of this video's cue list".
        # Same prefix, same gate, same clamps — `CUE_PAGE_MAX` and the offset
        # ceiling are the server's, not the URL's.
        Route(
            f"{ROOT}/api/videos/{{video_id}}/cues",
            guarded(api.cues_json),
            methods=["GET"],
        ),
    ]
