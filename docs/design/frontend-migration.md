# The dashboard's HTTP contract for the Next.js front end

**Status: the read slice is implemented (2026-09-05) — the overview, the
ledger, the session, and the videos table with its detail page.** This file
records the decisions Tom has settled and the endpoints that exist because of
them. It is not a plan — everything still open is in `docs/ROADMAP.md`, and
nothing is described here that is not in the tree. (The earlier speculative
draft of this file is gone; endpoints it sketched were never contracts.)

## 1. Settled decisions

From `DECISIONS.md` ("Frontend replacement", 2026-09-05) and Tom's review of
this slice:

1. **All three surfaces move to Next.js and React** — `/`, `/demo`,
   `/dashboard`. **Cutover happens only when all three are at parity**,
   dashboard reads and writes included; the Jinja pages keep serving until it.

   *Amended 2026-09-05:* `/` and `/demo` reached parity and their Python
   registrations are gone (§1a). "The Jinja pages keep serving until it" now
   means the dashboard's, which are the only pages Python still renders.
2. **Browser API requests go directly to Python.** No relay through Next, no
   generic proxy. Next may also read Python server-side for rendering.
3. **Python owns state, authorization, sessions and resource limits. Next owns
   the UI.** No new database layer, no second auth system, and the HTTP-only
   `mcp/` ↔ `worker/` boundary is untouched.
4. **Production is one origin**, so the existing routes and the existing
   `vidtheque_session` cookie keep working unchanged. Cross-origin support and
   the CORS policy it needs come later; nothing here adds them.
5. **Typed values on the wire, formatting in React.** What stays Python's is
   *policy text*: refusal codes, messages and their `next:` line, and the
   redaction itself.
6. **The public read-only projection is unchanged**, and it redacts by
   *omission*: the reads behind the operator's box are not taken, so there is
   no field for a client to un-hide.

## 1a. Route ownership

Decision 4 says one origin; this is how it splits. A reverse proxy sends the
**exact page GETs** to Next and **everything else** to Python. Exact, not by
prefix: `/demo` is Next's and `/demo`-anything is not a rule anyone has asked
for, whereas `/api/*` under a prefix rule is the whole facade in one line.

| Path | Served by |
| --- | --- |
| `GET /` | **Next** |
| `GET /demo` | **Next** |
| `GET /videos`, `GET /videos/{id}` | **Next** (the pages; `/videos/{id}/export.md` is Python's) |
| Next's own build output (`/_next/*`, `/landing/*`) | **Next** |
| `/api/*` — including `POST /api/ask` | Python |
| `/frames/*` | Python |
| `/mcp` | Python |
| `/auth/*`, `/.well-known/*` | Python |
| `/healthz` | Python |
| `/videos/{id}/export.md` | Python |
| `/dashboard/*` — pages, `/dashboard/api/*`, `/dashboard/static/*` | Python, until its own port lands — §1d |
| anything else | Python (`Mount("/", mcp_app)`, which 404s) |

**`POST /api/ask` is Python's**, and the Next route handler that shadowed it is
being removed in a sibling change: browsers call Python directly (decision 2),
and an ask relayed through Next would put a second process on the path of the
one request that spends money and is charged to a per-IP and a per-day bucket
keyed on the caller's address. *Landed 2026-09-05: the handler is deleted, so
that endpoint has one implementation again — §1c.*

*Landed 2026-09-05:* `public_routes()` no longer registers `GET /`, `GET /demo`
or `GET /static/{asset:path}` — it returns the facade and nothing else, and the
two page bundles are out of the Python package. Two things went with them and
are worth naming here rather than being found later: the `_DOCUMENT_HEADERS`
policy (CSP, `X-Frame-Options`, `nosniff`, `Referrer-Policy`), which is now
whatever serves the pages sends and which no test in `mcp/` can see —
demo-site.md §7 item 0 is the handover and the check on it — and the
`static/lab/` denylist, which was a property of the asset route.
`public/static/fonts/` stays: DESIGN.md makes it the document of record for the
two faces, `dashboard/__init__.py` aliases `/dashboard/static/fonts/` onto it,
and `test_web_assets.py` diffs `web/src/fonts/` against it.

## 1b. The document policy the pages carry

*Recorded 2026-09-05.* `_DOCUMENT_HEADERS` left Python with the pages it was
written for (§1a), and `web/src/proxy.ts` is what sends it now — on every
document this front end serves, which is `/`, `/demo`, `/videos` and
`/videos/{id}`. In production the policy is, verbatim:

```
default-src 'self'; script-src 'self' 'nonce-<per request>' 'strict-dynamic';
style-src 'self' 'unsafe-inline'; img-src 'self'; font-src 'self';
connect-src 'self'; frame-ancestors 'none'; form-action 'self';
base-uri 'none'; object-src 'none'
```

with `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff` and
`Referrer-Policy: no-referrer` beside it — the same four headers Python sent,
which is what demo-site.md §7 item 0's check is a check on.

Development, and only development, widens three directives: `'unsafe-eval'` on
`script-src`, because React rebuilds server stacks in the browser with it;
`data:`, `blob:` and the `VIDTHEQUE_API_URL` origin on `img-src`, because the
two processes are on two ports and the frame URLs the API hands back name its
host rather than this one; `ws:` and that same origin on `connect-src`, for the
HMR socket and the dev overlay. `NODE_ENV=production` gets none of it.

Two directives diverge from the policy Python sent, deliberately:

- **`style-src` gains `'unsafe-inline'`.** `EvidenceFrame` positions each OCR
  box with a `style=` attribute computed from that box's own coordinates, and
  the hero's lift and the 16:9 frame box do the same. React renders those as
  inline style attributes, which `style-src 'self'` refuses. CSSOM writes from
  a script (`el.style.transform`) were never governed by CSP and are not what
  this buys.
- **`img-src` loses `data:`.** Python's landing carried its favicon as a
  `data:image/svg+xml` URL in a `<link rel="icon">`; the mark is
  `web/src/app/icon.svg` now, a file on this origin, so the scheme has nothing
  left to allow. `img-src 'self'` therefore also depends on `/frames/*` being
  same-origin — true in production because §1a's split makes it true, and true
  in development because of the rewrite in §1c.

`script-src` is the shape change rather than a divergence. A React page cannot
say `script-src 'self'` and mean it: the framework ships an inline bootstrap
and streams its payload as more inline scripts, so the policy is the nonce
form, with `'strict-dynamic'` covering the chunks those scripts load and
`'self'` left beside it for the browsers that ignore `'strict-dynamic'`.

**What the nonce costs.** It is worth something only if it is new every
request, so every document renders per request: `app/layout.tsx` calls
`connection()`, and Cache Components stays off in `next.config.ts`, because a
partial prerender serves a shell built at build time and a build cannot carry a
per-request token. Data caching survives that move in `web/src/lib/library.ts`,
where the two library reads are `unstable_cache` with the periods the named
lifetimes had — `listVideos` revalidates at 60 s, `getVideo` at 3600 s, both
tagged (`library`, and `video-{id}` for the second), both serving the stale
copy while the fresh one is fetched. Two things are honestly gone: the `expire`
component of a named lifetime, the age at which a stale copy stops being served
at all, has no `unstable_cache` equivalent (the `stale` half went with the
prerender either way), and nothing in the tree calls `revalidateTag` — the tags
are written for an invalidation that does not exist yet.

## 1c. One origin in development, too

*Recorded 2026-09-05.* Production is one origin because a reverse proxy makes
it one (§1a). Development runs Next on `:3000` and Python on `:8080`, and the
browser still has to see one origin: Python's CSRF origin check reads a request
from `localhost:3000` against its own `localhost:8080` as cross-site and
refuses the write, which is a refusal no CORS header should be asked to lift.
So `web/next.config.ts` forwards Python's prefixes — `/api/*`, `/frames/*`,
`/mcp`, `/auth/*`, `/.well-known/*`, `/healthz`, `/dashboard/*` and
`/videos/{id}/export.md` — to `VIDTHEQUE_API_URL` with `rewrites()` whenever
`NODE_ENV` is not `production`. In production the list is empty, because the
proxy is doing it. **No CORS anywhere**, by decision 4, and this is the one
place it was tempting to reach for.

`POST /api/ask` therefore has one implementation, Python's:
`web/src/app/api/ask/route.ts` is deleted rather than disabled, so the event
vocabulary demo-site.md §3.5 defines has one owner, and the request that spends
money crosses one process fewer.

`VIDTHEQUE_CLIENT_IP_HEADER` narrows to the same seam. It applies only to the
reads this server makes for itself — search, videos and meta, in
`web/src/lib/api/client.ts` — which are the only requests that leave Next for
Python. Browser traffic reaches Python directly and carries the visitor's
address without help. The header must still equal the instance's
`VIDTHEQUE_TRUSTED_IP_HEADER`, or every visitor this server reads for shares
one rate-limit bucket.

## 1d. Route ownership under `/dashboard`

*Recorded 2026-09-05, from `DECISIONS.md` ("Dashboard pages fetch
client-side").* §1a splits the public surface; this is the same split inside
the dashboard, and it is decided by how the pages get their data.

**The pages fetch client-side.** Next serves a **data-free shell** for every
`/dashboard*` page. The browser calls `/dashboard/api/*` same-origin with its
own `vidtheque_session` cookie, and React renders from that response. Every
write is a browser call to Python's existing `POST` routes under `/dashboard/*`
under the cookie and Origin rules of dashboard.md §3.3. **Next never sees the
cookie**, forwards nothing on a visitor's behalf, and caches nothing per user.

Two things this arrangement gets for free rather than legislating. The public
read-only projection keeps working, because the API already answers anonymous
reads there (§7) — the shell is the same shell either way. And a **`401` from
the API is what sends the browser to the login page**: the refusal is the
signal, so authorization is decided in one place and the shell has no rule of
its own to keep in step.

Rejected, and why, so nobody re-derives it: **server rendering with cookie
forwarding**, which makes Next a credential relay — it must forward the session
cookie and the visitor's address on every read, and must never cache a response
across users. A **hybrid** was rejected on the same grounds, because one
forwarded cookie costs the whole property.

What each side owns, once a page is ported:

| Path | Served by | |
| --- | --- | --- |
| `GET /dashboard` | **Next** | *landed 2026-09-05* |
| `GET /dashboard/ledger` | **Next** | *landed 2026-09-05* |
| `GET /dashboard/videos`, `GET /dashboard/videos/{id}` | **Next** | pending — Python's HTML |
| `GET /dashboard/search` | **Next** | pending — Python's HTML |
| `GET /dashboard/jobs`, `GET /dashboard/jobs/{id}` | **Next** | pending — Python's HTML |
| `GET /dashboard/following`, `GET /dashboard/following/{slug}` | **Next** | pending — Python's HTML |
| `/dashboard/api/*` | Python | for good |
| `/dashboard/static/*` | Python | for good |
| `/dashboard/login`, `/dashboard/logout` | Python | for good |
| `/dashboard/index` (the form and its POST) | Python | for good |
| every other `POST /dashboard/*` | Python | for good |

Exact page GETs again, not a prefix: `/dashboard`-anything is Python's unless
it is one of the paths above. **Until a page is ported, Python keeps serving
its HTML** — the list is the destination, and a row becomes true the day that
React page lands, page by page as `docs/ROADMAP.md` tracks it.

The `web/` side expresses this split in its development rewrites and in the
matcher of the middleware that sends the document policy (§1b). That is
configuration catching up with the rule; the rule is the table.

**How the two configurations say it** *(landed 2026-09-05, with the first two
pages)*. Both are ordered so that the rule above is what a request meets, and
both are lists a port has to add itself to:

- **The development rewrites** are two lists, not one. Python's non-pages under
  `/dashboard` are named **explicitly** — `/dashboard/api/:path*`,
  `/dashboard/static/:path*`, `/dashboard/login`, `/dashboard/logout`,
  `/dashboard/index` — and forwarded in `beforeFiles`, so the router never
  looks for a page that could shadow one of them. The catch-all for the rest of
  `/dashboard` is in `afterFiles`, which is consulted *after* the router has
  looked: a ported page wins its own path, and every path with no page yet
  falls through to Python's HTML exactly as before. Porting a page adds a page
  and deletes nothing here.
- **The document-policy matcher names each ported page literally.** The
  page-wide entry excludes the whole `/dashboard` prefix — the JSON under it is
  not a document and the unported pages carry Python's own policy — so a ported
  page is named back in, one entry per path. That list is therefore *the record
  of what is ported*, and a port that forgets to add its path ships a document
  with no CSP on it. Deliberately not a prefix, for exactly that reason.

## 2. What landed

Five additive `GET` endpoints and the shared read assembly behind them. No
writes, no CORS, no new env var, no dependency or lockfile change, no auth
policy change, and no import from `worker/`.

In `mcp/src/vidtheque_mcp/dashboard/`: `api.py` (new) is the handlers,
`read_models.py` (new) is the shared assembly, `__init__.py` registers the
routes, and `views.py` now calls the assemblers instead of holding them.
`mcp/tests/test_dashboard_api.py` (new) covers the slice.

`read_models.py` holds what the pages and the JSON must not answer twice:
`overview_reads`, `ledger_reads`, `videos_reads`, `video_detail_reads`,
`pipeline_readiness`, `redacted`, `declared_models`, `video_header`,
`stage_rows`, `shot_rows`, `frame_cards`, `coverage_pills`/`coverage_flags`,
`video_facts`, `date_filters`, `file_size`, `tool_error`, `thumb`, and the caps
below. `views.py` imports them back under the names it always used, so the
Jinja pages run the same code and the same number of database reads as before —
the videos table's cover-frame query grew three columns rather than gaining a
second read.

## 3. Common behaviour

- **Auth.** `/api/overview`, `/api/ledger`, `/api/library` and
  `/api/library/{video_id}` sit behind the route group's existing read gate
  (`dashboard/__init__.py:guarded`): a bearer token, a valid
  `vidtheque_session` cookie, a socket peer in
  `VIDTHEQUE_DASHBOARD_TRUSTED_CIDRS`, or `VIDTHEQUE_AUTH=none` (open by
  design). Refusal is `401` with `{"error": "E_AUTH_REQUIRED", "message",
  "next"}`. `/api/session` is outside the gate — see §6.
- **Caching.** Every response carries `Cache-Control: no-store`.
- **Parameters.** `overview` and `ledger` read no query string at all, so there
  is nothing to clamp; their bounds are the constants in §4. The two `library`
  routes take the pages' parameters under the pages' clamps, and say in `notes`
  when a bound moved — §6a.
- **Rate limit.** The existing per-IP `/dashboard/*` bucket
  (`VIDTHEQUE_RATE_DASHBOARD_PER_MIN`, default 120) covers all three.
- **Errors.** A tool refusal passes through as `{"error", "message", "next"}`
  at the status `errors.HTTP_STATUS` maps the code to.
- **Timestamps.** Epoch seconds, every one of them, `readiness.checked_at`
  included. The pages format that observation with `iso_z` because a
  `<time datetime=…>` attribute wants ISO-8601; `read_models._stamped` takes one
  reading of the clock and carries both shapes, so the page and the payload can
  never name different seconds. `api.py:_readiness` copies the block out field
  by field rather than forwarding the page's dict, so a value added for the
  templates does not join this contract by default.
- **No display roundings either.** `corpus_rollup` carries `hours` beside
  `duration_s` and its own SQL comment calls it "a display rounding"; the JSON
  sends the seconds only. *Settled 2026-09-05 (Tom): `hours` stays dropped from
  the overview payload — `duration_s` is the figure, and React divides.*

**The three older endpoints, and the rule for them** *(settled 2026-09-05 by
Tom)*. `/dashboard/api/jobs`, `/dashboard/api/jobs/{job_id}` and
`/dashboard/api/videos/{video_id}/cues` predate decision 5: they are the Jinja
scripts' own poll targets and they answer in rendered strings, which is what a
React page cannot read. The rule is **typed fields beside the strings, strings
cut at the port**: add the typed half now, additively, and delete a rendered
string in the same commit that deletes the Jinja page or script that reads it.
Changing them any earlier changes what `static/jobs.js` and
`static/dashboard.js` receive, and those pages are still serving.

- **The two jobs routes need no change today.** The typed half is already
  beside the `text` block — `progress`, `wall_s`, `ran_s`, `waited_s`,
  `defer_s`, `created_at`/`started_at`/`finished_at`, per item
  `attempts`/`max_attempts`, per event `at`. What goes at the port is `text`
  itself (and the items' `text`, and each event's `at_text`); `basis`, the
  sentence saying what the percentage is computed over, is policy text and
  survives, in `notes` or beside it.
- **The cues route was the one with a missing half**, not a duplicated one.
  *Landed 2026-09-05:* it carries `start_s` and `end_s` as floats,
  `avg_logprob` as a float or `null`, `chunk_opens` — the chunk's `seq`,
  `start_s`, `end_s`, `n_words`, `n_chars` — and `chunk_closes`, all under
  `views._cue_rows`' own names, because those are the values `at`, `conf` and
  `chunk` were renderings of. `in_chunk` is the two markers collapsed and
  stays; both are sent, because a chunk's last cue is not its first. The
  strings are untouched, and dashboard.md §5.3 is the contract entry.

## 4. `GET /dashboard/api/overview`

The corpus overview page's reads (`views.overview`), typed.

```jsonc
{
  "counted_at": 1757030400,          // int, epoch seconds
  "redacted": false,                 // bool: is this the public projection
  "corpus": {
    "videos": 4, "queryable_videos": 3,
    "videos_by_index_state": {"ready": 3, "indexing": 1},  // present states only
    "data_status": "ok",             // verbatim from corpus-summary
    "cues": 0, "keyframes": 0, "ocr_lines": 0,
    "duration_s": 13500.0,
    "published": {"oldest": 1740000000, "newest": 1740000000},  // int|null
    "last_indexed": 1740000000       // int|null
  },
  "channels": [{"channel": "…", "videos": 3, "seconds": 8100.0}],  // ≤ 12
  "tags":     [{"tag": "topic:attention", "videos": 2}],           // ≤ 24
  "gaps":     {"transcript_no_ocr": 0, "indexing": 1, "failed": 0},
  "embed_backlog": {"text": 0, "frame": 0},
  "jobs": {"active": 2, "running": 1, "deferred": 1,
           "failed_recent": 1, "failed_window_s": 86400},
  "recent": [{"video_id": "…", "title": "…", "channel": "…",
              "duration_s": 5400.0, "indexed_at": 1740000000,
              "thumb": "/frames/….jpg?w=192&q=70"}],               // ≤ 8
  "readiness": {"mcp": "ready", "database": "ready",
                "vectors": {"enabled": true, "reason": null},
                "worker": {"state": "ready|unavailable|unconfigured",
                           "detail": "…",
                           "models": [{"task": "stt", "model": "…",
                                       "loaded": true}]},           // ≤ 12
                "checked_at": 1757030400},                          // int, epoch
  "declared_models": [{"label": "…", "key": "…", "value": "…", "dim": "…"}],
  "storage": {"keyframe_bytes": 0, "database_bytes": 0}
}
```

Caps, from `read_models`: `CHANNEL_CAP=12`, `TAG_CAP=24`, `RECENT_CAP=8`,
`FAILED_WINDOW_S=86_400`, `WORKER_BACKEND_CAP=12`; the worker probe is bounded
by `WORKER_STATUS_TIMEOUT_S=1.0` wall-clock and `WORKER_STATUS_MAX_BYTES=64 kB`
and runs concurrently with the database reads. `gaps.failed` is a **count** —
the rows behind it carry `video_stages.error`, the pipeline's prose about the
operator's box, and reach no surface from here.

## 5. `GET /dashboard/api/ledger`

The ledger page's reads: a fixed number of whole-table and index counts, no
per-video work.

```jsonc
{
  "counted_at": 1757030400, "redacted": false,
  "corpus": {"videos": 4, "duration_s": 13500.0,
             "cues": 0, "keyframes": 0, "ocr_lines": 0,
             "chunks": 0, "tags": 0, "channels": 2,
             "published": {"oldest": 1740000000, "newest": 1740000000},  // int|null
             "last_indexed": 1740000000},
  "videos_by_state": {"ready": 3, "pending": 0, "indexing": 1,
                      "failed": 0, "stale": 0},   // sums to corpus.videos
  "jobs_by_state": {"queued": 1, "running": 1, "done": 0,
                    "failed": 1, "cancelled": 0},
  "queue": {"active": 2, "running": 1, "deferred": 1,
            "failed_recent": 1, "failed_window_s": 86400},
  "embed_backlog": {"text": 0, "frame": 0},
  "gaps": {"transcript_no_ocr": 0},
  "readiness": { … as above … },
  "storage": {"keyframe_bytes": 0, "database_bytes": 0}
}
```

*Added 2026-09-05 (Tom): `corpus.published`.* The ledger band prints
"published *oldest* – *newest*" under the video count and this payload had no
field for it, so a React ledger could only drop the line. It is the **same
name and the same shape** as the overview's (§4) — epoch seconds, `null` on
both halves when the corpus is empty — because one fact with two spellings is
how two pages start disagreeing about the corpus. It costs no read: the
assembler's `corpus_rollup` was already carrying `oldest_published` and
`newest_published` for the counts beside it.

## 6. `GET /dashboard/api/session`

**Readable signed out, deliberately** — a React shell that cannot ask this can
only guess whether to render a dashboard or a sign-in link, or probe a data
endpoint and read the 401. It is no new disclosure: `GET /dashboard` has
answered an anonymous browser with the auth mode and this exact sign-in hint
since phase 1, and the endpoint is on the same rate-limit bucket.

```jsonc
{
  "version": "0.0.6",
  "auth_mode": "token",        // none | token | oauth
  "readonly": false,           // VIDTHEQUE_PUBLIC_READONLY
  "write_side": true,          // does this deployment register writes at all
  "writes_allowed": true,      // db.writes_allowed
  "authenticated": false,      // may this caller read the data endpoints
  "is_owner": false,           // did they *prove* it ("open" is not a credential)
  "signed_in": false,          // a validated session row, never cookie presence
  "has_session_cookie": false, // did the browser send one at all, valid or not
  "policy": "public",          // public | owner — the clamp policy they earn
  "login_url": "/dashboard/login",   // null when there is no write side
  "sign_in_hint": "Sign in at /dashboard/login, or send Authorization: …",
  "accepts_password": true, "accepts_token": true
}
```

`signed_in` is `auth/credential.py:credential()` returning `"session"` — the
cookie looked up in `login_sessions` and found unexpired. A cookie the browser
still holds after its row is gone reads `false`, which is the whole point.

*Added 2026-09-05 (Tom): `has_session_cookie`.* The payload carries **both**
facts, because they answer different questions and the shell needs both.
`signed_in` is authorization: will the next request be served.
`has_session_cookie` is `vidtheque_session in request.cookies` — the same
lookup `views._chrome` makes, from the same constant, so the two cannot drift —
and it authorizes nothing. It is "is there a cookie to clear", which is why the
HTML rail's own `signed_in` has always been cookie presence: a stale cookie
must still get a **Sign out** button rather than silence. The React shell
therefore shows sign-out when **either** is true, and renders the dashboard on
`signed_in` alone. The cookie is `HttpOnly`, so a shell cannot read it itself;
without this field the stale-cookie case is invisible to React.

Three fields whose reading is easy to get wrong, and each one is a page's
existing behaviour rather than a new rule:

- `signed_in` here is **not** `base.html`'s `signed_in`, which is the cookie's
  mere presence. That is what `has_session_cookie` is, side by side with it —
  the rail's field and the gate's answer, named apart so neither has to stand
  in for the other.
- `sign_in_hint` is `null` in `VIDTHEQUE_AUTH=none`. The string
  `access.sign_in_hint` builds names a bearer unconditionally, which is correct
  where it is used — a 401 page, in a mode that takes one — and untrue as a
  standing description of a deployment that refuses nobody and registers no
  login page.
- `writes_allowed` is `db.writes_allowed`, the database's own flag, exactly as
  the rail reads it. It is not the write gate: a deployment can have a writable
  database and no write side (`readonly`, or `AUTH=none`), and a client renders
  a control only when `write_side` is true.

**Never in this payload:** the token, the password, which of the two matched,
`PUBLIC_URL`, the worker URL, the database path, the trusted CIDRs, the
declared model ids, the drift reason.

**What the shell does with it** *(landed 2026-09-05, with the first two
pages)*. §1d says a `401` from the API is what sends the browser to the login
page; these are the two rules that follow from it, and they belong here because
both are readings of this payload rather than of a page's own state.

- **The 401, in one place, and only where there is somewhere to go.** The
  browser client answers a refused read by asking this endpoint — outside the
  gate, so that second request cannot itself be refused — and navigates to
  `login_url` with a `?next=` return path, the parameter `writes.login` already
  reads and `writes._safe_next` already fences to this surface. It navigates
  **only when `login_url` is non-null**: a token-gated read-only instance gates
  its reads and registers no login page, so it can refuse a reader while having
  nowhere to send them, and a redirect there would be a 404. When it is null the
  page renders its signed-out state instead. **One navigation per page load** —
  a page has several reads in flight and three simultaneous 401s must not mean
  three navigations.
- **The rail reads three fields and each answers a different question.**
  Sign-out shows on `signed_in || has_session_cookie`, because the reader who
  most needs the button is the one whose row expired under a cookie the browser
  still holds. The **Manage** group shows on `write_side`, which is whether
  this deployment registers writes at all — not `writes_allowed`, the
  database's own flag, which can be true where there is no write side. The
  demo's line shows on `readonly`, and it says only that nothing here writes:
  `auth=` names an environment variable and "indexing refused" is a sentence
  about a worker nobody visiting the demo can reach (§2.4).

## 6a. `GET /dashboard/api/library` and `/dashboard/api/library/{video_id}`

*Landed 2026-09-05.* The videos table (`dashboard.md` §5.2) and the video
detail page (§5.3), typed. **The full schema, both payload examples and the
redaction table are `dashboard.md` §20** — it is the route group's contract and
this is the front end's index of it.

**The name is `library`, not `videos`, and that is not cosmetic.**
`/dashboard/api/videos` and `/dashboard/api/videos/{video_id}` already exist at
this prefix: they are the `/api/*` facade's handlers, registered under
`/dashboard` since phase 1, and `/dashboard/api/videos/{id}/cues` hangs off
them. One path cannot be two contracts. A React videos page therefore reads
`/dashboard/api/library`, and the facade's listing keeps answering exactly what
it answered before.

| Route | Parameters | Bounds |
| --- | --- | --- |
| `/api/library` | `q`, `channel`, `tags`, `has`, `index_state`, `order`, `limit`, `offset`, `published_after/before`, `indexed_after/before` | `limit` 1..100 (default 50), `offset` 0..10 000, dates snapped to the UTC day |
| `/api/library/{video_id}` | `frames`, `frame_offset` | `frames` 1..96 (default 24), `frame_offset` 0..100 000 |

What a page gets, in one sentence each: the table sends `order` explicitly,
`total` as the exact count of the filtered set, `pagination.has_more`, and per
row `published_at`/`indexed_at` as epoch seconds, `duration_s` as seconds,
`index_state` as the schema's word, `coverage` as three booleans, `tags` as a
list and `thumb` as a root-relative frame URL. The detail sends the video
header, `data_status`, `summary_error`, chapters, the seven `video_stages`
rows, the per-video counts, the cue origins, the shot timeline, the keyframe
strip with its OCR lines and normalised boxes, the job history — and the
transcript as **totals plus the name of the cues endpoint**, never as cues.

Three things worth knowing before writing the page:

- **`notes` is where a clamp is disclosed.** The Jinja page echoes an accepted
  `limit` back into the form field the reader typed it into; a JSON caller has
  no form, so `notes` carries the sentence (`limit=100000 → 100`). Unknown
  `has`/`index_state`/`order` values fall back and say which value answered.
  It is policy text and it stays Python's; render it, do not compose it.
- **The date echo is exclusive at the top.** `filters.published_before` is the
  start of the day *after* the one asked for, because that is the bound the
  query used (`>= after`, `< before`). A date input wants the day itself back:
  subtract 86 400, or keep the string the reader typed.
- **`order=relevance` without `q` is a `400`** (`E_ORDER_SCOPE`), and an
  unparseable date is a `400` (`E_BAD_TIME_FORMAT`). Both are the tool's typed
  refusals in the §3 envelope.

## 7. The projection, per field

In `VIDTHEQUE_PUBLIC_READONLY=1` (`read_models.redacted`), on both corpus
endpoints:

| Field | Public projection |
| --- | --- |
| `declared_models` | `null` — not read |
| `storage` | `null` — the byte totals are not read |
| `readiness.worker` | `null` — the probe is not made at all |
| `readiness.vectors.reason` | `null`; `enabled` stays, because search answers differently without the vector legs |
| everything else | unchanged — counts, channels, tags, gaps, queue, arrivals are corpus, not deployment |

On the two `library` routes: the table is **not** redacted at all (§2.4 gives
the demo the browsable corpus whole), and the detail drops exactly two fields
by not sending them — `stages[].model_key`, a declared model id, and
`stages[].error`, the pipeline quoting yt-dlp. `dashboard.md` §20 has the
per-field table.

## 8. Tests and what is not here

`mcp/tests/test_dashboard_api.py`, over `test_dashboard.py`'s fixture corpus and
client builders: the gate (anonymous, bearer, live and stale session cookie),
`no-store`, GET-only registration and disappearance with `VIDTHEQUE_DASHBOARD=0`,
typed values against the fixture's exact tallies, a scan of both payloads for a
rendered clock (the readiness stamp is the one field where the split nearly
leaked back), the caps and the absence of
parameters, the worker probe dropping `/status`'s operator-only fields, and the
projection both ways — the demo losing the box and never probing the worker,
against the owner's instance still seeing both. The Jinja pages are unchanged
and `test_dashboard.py` plus `test_dashboard_following.py` still cover them.

For §6a's two routes, the same file: the gate and GET-only registration, the
table's shape and the fixture's exact tallies, every clamp with the `note:` it
produces, ordering and each filter, the two contracts staying two paths (the
facade still answering its own shape at `/dashboard/api/videos`), the detail's
stage table and strip pagination, a `404` for an unknown id, the projection
losing the model ids and the yt-dlp prose while keeping the states and the
clocks, and a scan of both payloads for a rendered clock — including a bare
`1:56:40`, which is the shape `list-videos` would have travelled with had the
records been forwarded.

Writes, CORS and cross-origin sessions, the remaining read endpoints, the React
pages and the cutover are `docs/ROADMAP.md`'s. No schema for them is stated here
until it exists.
