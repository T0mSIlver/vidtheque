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
| `GET /paris` | **Next** (`aie-paris-2026.md` §4) |
| Next's own build output (`/_next/*`, `/landing/*`) | **Next** |
| `/api/*` — including `POST /api/ask` | Python |
| `/frames/*` | Python |
| `/mcp` | Python |
| `/auth/*`, `/.well-known/*` | Python |
| `/authorize`, `/token`, `/register`, `/revoke` — registered at the root under `VIDTHEQUE_AUTH=oauth` (§10, *added 2026-09-06*) | Python |
| `/healthz` | Python |
| `/videos/{id}/export.md` | Python |
| `/dashboard` page GETs | **Next** (*landed 2026-09-06* — §1d) |
| `/dashboard/api/*`, the thirteen `POST`s, `/dashboard/` | Python — §1d |
| anything else | **Next** — its designed 404, with the document headers (*amended 2026-09-16*; §11.3. The edge sends every unmatched path to Next, and Python's `Mount("/", mcp_app)` 404 answers only a request that reaches Python directly) |

*Dropped 2026-09-07 by Tom:* `GET /videos` and `GET /videos/{id}` — the reader's
library — were Next's from 2026-09-01 until that day. They duplicated
`/dashboard/videos` and `/dashboard/videos/{id}`, which read the same corpus
with the owner's fields on top, so the public pair went entirely rather than
being maintained as a second reader. Both paths now fall through to Next's 404,
which is the "anything else" row. `/videos/{id}/export.md` is unaffected and
stays Python's; it is the only thing left under the prefix.

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
two faces and `test_web_assets.py` diffs `web/src/fonts/` against it. Nothing
routes it — the dashboard aliased `/dashboard/static/fonts/` onto it until
2026-09-06, and that route went with the dashboard's own pages.

## 1b. The document policy the pages carry

*Recorded 2026-09-05; extended 2026-09-15.* `_DOCUMENT_HEADERS` left Python
with the pages it was written for (§1a), and `web/src/proxy.ts` is what sends
it now on every document this front end serves: `/`, `/demo`, `/paris`, and
the `/dashboard` pages named in `proxy.ts`'s matcher. In production the policy
is, verbatim:

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

*Amended 2026-09-16:* `library.ts` and its cached reads went with the library
pages on 2026-09-07; no server data cache remains in `web/`. The dashboard's
reads are held in the browser tab instead (`lib/dashboard/resource.ts`,
DECISIONS.md 2026-09-16), which Next never sees.

**Cache headers** *(recorded 2026-09-16)*. `next.config.ts`'s `headers()` sends
`Cache-Control: no-store` on `/dashboard/*`, because a management page must
never sit in a shared cache, and `public, max-age=31536000, immutable` on
`/landing/*`, because a still is added or removed and never edited under its
own name. The wire copy on a document is set there, not in `proxy.ts`: a
header the proxy sets on a rendered document is overwritten by the render
(`docs/LESSONS.md`). `proxy.ts` still sets `no-store` on `/dashboard/*`, and
that mirror is the copy the RSC payloads of client navigations carry.

## 1c. One origin in development, too

*Recorded 2026-09-05.* Production is one origin because a reverse proxy makes
it one (§1a). Development runs Next on `:3000` and Python on `:8080`, and the
browser still has to see one origin: Python's CSRF origin check reads a request
from `localhost:3000` against its own `localhost:8080` as cross-site and
refuses the write, which is a refusal no CORS header should be asked to lift.
So `web/next.config.ts` forwards Python's prefixes — `/api/*`, `/frames/*`,
`/mcp`, `/auth/*`, `/.well-known/*`, the root OAuth endpoints (`/authorize`,
`/token`, `/register`, `/revoke`), `/healthz`, `/dashboard/*` and
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

| Path | Served by | Today |
| --- | --- | --- |
| `GET /dashboard` | **Next** | *landed 2026-09-05* |
| `GET /dashboard/ledger` | **Next** | *landed 2026-09-05* |
| `GET /dashboard/videos`, `GET /dashboard/videos/{id}` | **Next** | *landed 2026-09-05* |
| `GET /dashboard/search` | **Next** | *landed 2026-09-05* |
| `GET /dashboard/jobs`, `GET /dashboard/jobs/{id}` | **Next** | *landed 2026-09-05* |
| `GET /dashboard/following`, `GET /dashboard/following/{slug}` | **Next** | *landed 2026-09-05* |
| `POST /dashboard/following` | Python | **the first path split by method** |
| `GET /dashboard/index` | **Next** | *landed 2026-09-05* |
| `POST /dashboard/index` | Python | **the second path split by method** |
| `GET /dashboard/login` | **Next** | *landed 2026-09-05* |
| `POST /dashboard/login`, `/dashboard/logout` | Python | for good |
| `/dashboard/api/*` | Python | for good |
| every other `POST /dashboard/*` | Python | for good |
| `GET /dashboard/` | Python | a `308` to `/dashboard`, which is Next's |

*Amended 2026-09-06: three rows left this table rather than changing.*
`/dashboard/static/*` is gone — the stylesheet, the two scripts and the
`fonts/` alias went with the pages, and the web app serves its own assets. So
did `GET /dashboard/login` and `GET /dashboard/index` **on the Python side**:
both were Jinja pages and both are now `POST`-only paths here, which is what
makes the method split below a split rather than a shadowing. Python registers
no `GET` under this prefix that answers with a document.

Exact page GETs again, not a prefix: `/dashboard`-anything is Python's unless
it is one of the paths above. **Until a page is ported, Python keeps serving
its HTML** — the list is the destination, and a row becomes true the day that
React page lands, page by page as `docs/ROADMAP.md` tracks it.

*Tally, 2026-09-05:* all eleven `GET /dashboard/*` pages this table names are
Next's now — the sign-in page's landing was the last of them, and every row
above reads *landed*.

*Closed 2026-09-06:* the sentence above has no cases left, and the Python HTML
it described is deleted — `views.py`, `templates/`, `static/`, the asset route
and `jinja2` (dashboard.md §23). A misrouted edge no longer finds an older page
under a ported path; it finds the `Mount("/")` 404, exactly as `/` and `/demo`
do since §1a.

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

  *Two entries are dead as of 2026-09-06 and are `web/`'s to remove:*
  `/dashboard/static/:path*` forwards to a route that no longer exists, and the
  `afterFiles` catch-all now falls through to a 404 rather than to Python's
  HTML — which is correct, and is no longer the thing it was written for.

  *Removed 2026-09-06:* both are out of `next.config.ts`, and the catch-all had
  a second reason to go — `afterFiles` is consulted after the static routes but
  *before* the dynamic ones, so `/dashboard/:path*` matched
  `/dashboard/videos/{video_id}`, `/dashboard/jobs/{job_id}` and
  `/dashboard/following/{slug}` ahead of the pages that own them, and every
  detail page in development came back a 404 off Python
  (`web/src/next.config.test.ts`).
- **The document-policy matcher names each ported page literally.** The
  page-wide entry excludes the whole `/dashboard` prefix — the JSON under it is
  not a document and the unported pages carry Python's own policy — so a ported
  page is named back in, one entry per path. That list is therefore *the record
  of what is ported*, and a port that forgets to add its path ships a document
  with no CSP on it. Deliberately not a prefix, for exactly that reason.

  *Superseded 2026-09-16:* every path under `/dashboard` is now a document this
  app serves (a page, or the surface's not-found refusal), so the matcher is a
  single lookahead that excludes only Python's paths: the public prefixes of
  §1a, `/dashboard/api/` and exactly `/dashboard/logout`. The record of what is
  a page is `ported.ts`, and `proxy.test.ts` asserts the two lists agree.
  Prefetch requests (`next-router-prefetch`, `purpose: prefetch`) fetch a
  payload, not a document, and are skipped; the chunks a navigation then loads
  are covered by `'strict-dynamic'`.

**The two videos pages, and the one segment that keeps the writes Python's**
*(landed 2026-09-05)*. `GET /dashboard/videos` and
`GET /dashboard/videos/{video_id}` are Next's now, both named in the matcher.
The detail's entry is `/dashboard/videos/([^/]+)` — one segment, written as a
group rather than as `:id` so `proxy.test.ts` can run the matcher instead of
reimplementing it. Three segments therefore do not match, which is what leaves
`POST /dashboard/videos/{id}/reindex` and `POST /dashboard/videos/{id}/tags`
with Python: the table's last row, "every other `POST /dashboard/*`", holds
without an exception written for it. The development rewrites needed no change
at all — the `/dashboard` catch-all is in `afterFiles`, so the router finds
these two pages first.

**The following and index pages, and the two paths routed by method**
*(landed 2026-09-05, following first, index second)*. `GET /dashboard/following`,
`GET /dashboard/following/{slug}` and `GET /dashboard/index` are Next's, in the
matcher and in `ported.ts` like the rest. What is different about this trio is
the two rows above, and it is not an exception of one: **`POST
/dashboard/following` and `POST /dashboard/index` are each a form's route and
each shares its path with a page this app now serves** — every other write on
this surface has a segment its page does not (`…/{slug}/state`,
`…/{video_id}/tags`), so these are the only two collisions under `/dashboard`,
and the table's "every other `POST /dashboard/*`" cannot resolve either on path
alone.

**The sign-in page is the third, and the sharpest of the three** *(landed
2026-09-05)*. `GET /dashboard/login` is Next's now, named in the matcher and
in `ported.ts` beside the rest. `POST /dashboard/login` stays Python's **for
good** rather than until some later cutover — the other two collisions are
Python's only because nobody has ported their write yet, but this one cannot
move: the response's `Set-Cookie` is the whole write, and an `HttpOnly` cookie
is not a thing a React shell could mint or clear. `/dashboard/logout` stays
Python's too, and for a plainer reason — it has no page of its own to collide
with, only the rail's Sign out button, so it was never a candidate for this
list.

New behaviour worth naming: a deployment with no write side
(`VIDTHEQUE_PUBLIC_READONLY=1`, or `VIDTHEQUE_AUTH=none`) never registers
`/dashboard/login` on the Python side, `GET` and `POST` alike, so Python 404s
both (dashboard.md §21). But the edge routes a `GET` to this path by method,
not by asking the deployment first, so that `GET` reaches Next regardless —
and Next's page reads `login_url: null` off the session and renders the
absent state it was built for (§6, §9), rather than the reader ever seeing
Python's 404. Only a `POST` still means that 404.

**Production routes all three paths by method**: `GET` → Next, `POST` →
Python, on `/dashboard/following`, `/dashboard/index` and `/dashboard/login`
alike. That is the reverse proxy's rule and it is written here because nothing
in this repo can express it — a Next rewrite and a middleware matcher both
match paths, not methods. It is a cutover check (`docs/ROADMAP.md`): a `POST`
to any of the three through the edge must reach Python, or the form answers
with a document from a page that never saw the write.

**Development uses a shim, and it is not the rule.** `web/next.config.ts`'s
`PYTHON_FORM_POSTS` keys a `beforeFiles` rewrite for each of the three paths on
`content-type: application/x-www-form-urlencoded` — the header every write on
this surface sends (§9) and no document navigation ever does — because
`beforeFiles` is the only stage that runs before the router finds the page, and
`has` reads headers, cookies, the query and the host, and nothing else. It is a
development-only entry, deleted the day development runs both processes behind
one proxy, and it must not be read as the production arrangement.

A third list joined those two with these pages, and it is the one a component
asks: `web/src/app/dashboard/ported.ts` holds the ported page paths — `ROOT`,
`ROOT/ledger`, `ROOT/videos`, plus the same one-segment pattern for the detail
— behind `isPorted(href)`, which every link into this surface asks so a page
this app serves is reached with `Link` and a page Python still renders stays a
plain anchor. Porting a page adds its path here, names it in the matcher, and
changes nothing else.

**The index page reads no `/dashboard/api/*` route.** A form needs only what
this deployment is, and the chassis has already asked that — so the page waits
on the session read before drawing rather than rendering early and finding out
afterward that indexing is refused. The seeding parameters it accepts —
`urls`, `expand` and `tags` — are `writes._prefilled_index_form`'s, bounded by
`MAX_PREFILL_URLS_CHARS` and `MAX_PREFILL_TAGS_CHARS`, with `expand` taken only
when it is one of `indexing.EXPANSIONS`; nothing here validates a URL, a tag or
an expansion, because a prefill is a draft the operator may still edit and the
POST remains the only thing that interprets it. The video detail's "Queue more
from this channel" link is the one caller: it sends `urls` and
`expand=channel_recent`.

## 2. What landed

Seven additive `GET` endpoints and the shared read assembly behind them. No
writes, no CORS, no new env var, no dependency or lockfile change, no auth
policy change, and no import from `worker/`.

| Route | Gate | Contract |
| --- | --- | --- |
| `/dashboard/api/overview` | read gate | §4 |
| `/dashboard/api/ledger` | read gate | §5 |
| `/dashboard/api/session` | **none** | §6 |
| `/dashboard/api/library` | read gate | §6a, dashboard.md §20 |
| `/dashboard/api/library/{video_id}` | read gate | §6a, dashboard.md §20 |
| `/dashboard/api/following` | read gate, **write side only** | §6b, dashboard.md §22 |
| `/dashboard/api/following/{slug}` | read gate, **write side only** | §6b, dashboard.md §22 |

In `mcp/src/vidtheque_mcp/dashboard/`: `api.py` (new) is the handlers,
`read_models.py` (new) is the shared assembly, `__init__.py` registers the
routes, and `views.py` now calls the assemblers instead of holding them.
`mcp/tests/test_dashboard_api.py` (new) covers the slice.

`read_models.py` holds what the pages and the JSON must not answer twice:
`overview_reads`, `ledger_reads`, `videos_reads`, `video_detail_reads`,
`pipeline_readiness`, `redacted`, `declared_models`, `video_header`,
`stage_rows`, `shot_rows`, `frame_cards`, `coverage_pills`/`coverage_flags`,
`video_facts`, `date_filters`, `file_size`, `tool_error`, `thumb`,
`following_reads`, `follow_detail_reads`, `follow_row_json`,
`follow_row_json_with_error`, `follow_settings`, `near_miss`, and the caps
below. `views.py` imports them back under the names
it always used, so the Jinja pages run the same code and the same number of
database reads as before — the videos table's cover-frame query grew three
columns rather than gaining a second read, and the two following pages gained
nothing at all.

`follow_row_json` is the one entry there that a *write* also answers with:
`writes.py` imports its with-the-failure form, `follow_row_json_with_error`,
back as `_follow_payload`, so §21's outcome and §6b's detail read describe a
follow identically rather than by two functions agreeing.

## 3. Common behaviour

- **Auth.** Every route in §2's table but `/api/session` sits behind the route
  group's existing read gate (`dashboard/__init__.py:guarded`): a bearer token,
  a valid `vidtheque_session` cookie, a socket peer in
  `VIDTHEQUE_DASHBOARD_TRUSTED_CIDRS`, or `VIDTHEQUE_AUTH=none` (open by
  design). Refusal is `401` with `{"error": "E_AUTH_REQUIRED", "message",
  "next"}`. `/api/session` is outside the gate — see §6.
- **Registration.** The two `following` routes are the exception to "additive":
  they are declared with the write routes, because their pages are, so a
  deployment with no write side (`VIDTHEQUE_PUBLIC_READONLY=1`, or
  `VIDTHEQUE_AUTH=none`) answers `404` on both exactly as it does on the pages
  — dashboard.md §18.6 and §22. A client asks `/api/session` first and renders
  the Following surface only when `write_side` is true.
- **Caching.** Every response carries `Cache-Control: no-store`. *Amended
  2026-09-16:* the browser keeps each read in memory for the life of the
  document, keyed by the request (`lib/dashboard/resource.ts`), and paints it
  on a revisit, Back or Forward while it re-reads (DECISIONS.md, 2026-09-16).
  A page shows data only for its current request: a filter change shows the
  page's reserved placeholder until the new listing answers, never the last
  listing's rows. The one exception is a video's keyframe strip, whose next
  page replaces a panel rather than a listing: the strip on screen stays,
  dimmed and `aria-busy`, until it lands (dashboard.md §5.3).
- **Parameters.** `overview` and `ledger` read no query string at all, so there
  is nothing to clamp; their bounds are the constants in §4. The two `library`
  routes take the pages' parameters under the pages' clamps, and say in `notes`
  when a bound moved — §6a.
- **Rate limit.** The existing per-IP `/dashboard/*` bucket
  (`VIDTHEQUE_RATE_DASHBOARD_PER_MIN`, default 120) covers all of them.
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

  *Landed 2026-09-06:* both `text` blocks and `at_text` are gone, and `basis`
  is **beside** — a field on the card. `notes` is the list of bounds that moved
  on this request, and a sentence true of every card is not one of them.
- **The cues route was the one with a missing half**, not a duplicated one.
  *Landed 2026-09-05:* it carries `start_s` and `end_s` as floats,
  `avg_logprob` as a float or `null`, `chunk_opens` — the chunk's `seq`,
  `start_s`, `end_s`, `n_words`, `n_chars` — and `chunk_closes`, all under
  `views._cue_rows`' own names, because those are the values `at`, `conf` and
  `chunk` were renderings of. `in_chunk` is the two markers collapsed and
  stays; both are sent, because a chunk's last cue is not its first. The
  strings are untouched, and dashboard.md §5.3 is the contract entry.

**What the reader of both halves looks like, and what the cut will take**
*(recorded 2026-09-05, with the video detail page)*. The transcript panel
renders from the typed fields and falls back to the strings, so it works
against an instance that predates the addition: the timecode is
`clock(cue.start_s)` falling back to `cue.at`, the confidence is
`cue.avg_logprob.toFixed(2)` falling back to `cue.conf`, and the chunk mark is
composed here from `chunk_opens`' own five fields —
`chunk {seq} · {start_s}–{end_s} · {n_words} words · {n_chars} chars` — falling
back to `cue.chunk`. That composition is this side's by decision 5: it is a
value, not policy text. `in_chunk` is what styles a cue as inside its chunk;
`chunk_closes` and the cue's `end_s` are declared in `schemas.ts` and read by
nothing yet.

That reader is how the panel read while both halves were on the wire: the typed
fields were **optional** in the schema and the three strings required, which is
the shape of "the strings are the fallback".

*Landed 2026-09-06, both sides — the strings are cut.* Python's half went with
the video detail page: `static/dashboard.js` was the only reader of `at`, `conf`
and `chunk`, and they are off the wire. `web/`'s half is d7c1cd9 — the three
string fields are out of the `Cue` schema, `CueRow`'s three fallbacks are out
with them, and `start_s`, `end_s`, `avg_logprob`, `chunk_opens` and
`chunk_closes` are **required**. What made them optional was the fallback
itself — an instance predating `start_s` had only the strings — and there is no
such instance left to render against. `t` stays — it is the whole-second
start a `?t=` deeplink takes, a value and not a rendering. A payload that still
carries the three strings parses either way, because Zod strips what the object
does not name. Nothing else on either page moved.

## 4. `GET /dashboard/api/overview`

The corpus overview page's reads (`views.overview`), typed.

```jsonc
{
  "counted_at": 1757030400,          // int, epoch seconds
  "redacted": false,                 // bool: is this the public projection
  "writes_allowed": true,            // db.writes_allowed, the session's own flag
  "corpus": {
    "videos": 4, "queryable_videos": 3,
    "videos_ready": 3,               // ready only; queryable is ready + stale
    "videos_by_index_state": {"ready": 3, "indexing": 1},  // present states only
    "data_status": "ok",             // verbatim from corpus-summary
    "cues": 0, "keyframes": 0, "ocr_lines": 0,
    "duration_s": 13500.0,
    "published": {"oldest": 1740000000, "newest": 1740000000},  // int|null
    "last_indexed": 1740000000       // int|null
  },
  "channels": [{"channel": "…", "videos": 3, "seconds": 8100.0}],  // ≤ 12
  "tags":     [{"tag": "topic:attention", "videos": 2}],           // ≤ 24
  "gaps":     {"transcript_no_ocr": 0, "indexing": 1, "failed": 0,
               "failed_cap": 5, "failed_capped": false},  // failed is a probe
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

*Amended 2026-09-06: four fields, all additive over reads already taken.*
`corpus.videos_ready` is the band's "N ready" — `corpus_rollup`'s own column,
`ready` and nothing else, where `queryable_videos` is ready **plus stale**, so a
page saying "N ready" off that one counts a stale video as ready and its "not
ready" goes short by the same one. `gaps.failed_cap` and `gaps.failed_capped`
say that `gaps.failed` is a probe: `queries.gaps` looks for failed videos with
`LIMIT 5`, so `failed: 5` means "five or more", and the ceiling
(`read_models.GAPS_FAILED_CAP`) and the reading of it ride beside the count so
the client's `+` and the SQL behind it cannot disagree. `writes_allowed` is
`/dashboard/api/session`'s field (§6) on this payload as well, because the two
things drawn from it — the Indexing state in the readiness strip and the drift
banner's second half — are on this page: a rendering that waits on a second
request for a deployment fact is one that flips under the reader. Not redacted,
because the session publishes it to an anonymous browser already; the *reason*
stays there. All four are `dashboard.md` §19's.

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
  "writes_allowed": true,                       // as §4, and for the same page furniture
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
  "writes_refused_reason": null,  // string|null: why not, and null in the projection
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

*Added 2026-09-06: `writes_refused_reason`.* The flag above says a form must be
disabled; this is the sentence that says why, and without it the index page
refuses a reader without telling them the one thing they can act on — the Jinja
form printed it verbatim under its disabled controls.
`Database._assert_dimensions` turns `writes_allowed` off and writes
`VectorState.reason` in the same breath, so the two are one fact: a config key
and a table's declared width disagree, and indexing is refused so embedding
spaces cannot be mixed. It is `null` when writes are allowed and `null` in
`VIDTHEQUE_PUBLIC_READONLY=1` — one absence, so a client renders "indexing is
refused" with no explanation in both cases rather than learning to tell them
apart. Policy text under `DECISIONS.md`'s split: it
carries a number inside a sentence about this box, and Python composes it.
`read_models.drift_reason` is the only reader of that string on this surface,
shared with `/api/following`'s `vectors_reason` (§6b), so the index form and
the follow form cannot be refused with two different explanations.

**Never in this payload:** the token, the password, which of the two matched,
`PUBLIC_URL`, the worker URL, the database path, the trusted CIDRs, the
declared model ids. The drift reason was on this list until 2026-09-06 and is
now the exception directly above — to the **owner** only, and only where writes
are actually refused; the projection still never sees it.

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

**The sign-in page's own reading of this payload** *(landed 2026-09-05)*. It
waits on this read like the index form and unlike every other page, because
which secret this deployment accepts is the difference between a field
labelled with the right environment variable and one labelled with a guess.

- **The already-signed-in bounce keys on `signed_in`**, the validated session
  row — not on `credential()` returning non-null the way the Jinja `GET`
  bounced. `credential()` also answers a bearer, so a caller who only ever
  proves ownership with `Authorization: Bearer` used to be sent straight on by
  the old page; this one still shows them the form, because `signed_in` is
  narrower on purpose — it is the one question this page needs answered.
- **`sign_in_hint` is not rendered here.** Every other refusal on this surface
  names it as the way to a page it is not; this page *is* that page, and a
  sentence pointing a reader at where they already are is not a hint. What the
  page reads instead is `login_url` being `null` — the deployment's other
  fact, that there is no sign-in page at all — and it says so rather than
  drawing a form around a route that is not registered.
- **The note under the secret field is not a fourth field.** It is derived
  from `accepts_password` and `accepts_token`, the same two booleans the
  label above it switches on: both true names either secret, `accepts_token`
  alone names `VIDTHEQUE_TOKEN`, and `accepts_password` alone (the ordinary
  case) names `VIDTHEQUE_PASSWORD`.

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

## 6b. `GET /dashboard/api/following` and `/dashboard/api/following/{slug}`

*Landed 2026-09-05.* §18's two pages, typed. **The full schema, both payload
examples and the registration rule are `dashboard.md` §22** — it is the route
group's contract and this is the front end's index of it.

**These two can be absent, and no other read endpoint can.** They are declared
with the write routes because their pages are: in
`VIDTHEQUE_PUBLIC_READONLY=1` and in `VIDTHEQUE_AUTH=none` both are `404`, page
and JSON together. A client therefore reads `write_side` off `/api/session`
(§6) before it renders a Following link at all — the same field the rail's
**Manage** group already keys off — and treats a `404` here the way §9 says to
treat one on a write path: the affordance should not have been rendered.

| Route | Parameters | Bounds |
| --- | --- | --- |
| `/api/following` | `limit`, `offset` | `limit` 1..100 (default 25), `offset` 0..10 000 |
| `/api/following/{slug}` | `limit`, `offset` | the same, over the passed-over ledger |

What a page gets, in one sentence each: the list sends `order` explicitly
(`failing_first`, the store's one order), the band's seven totals, the rolling
budget as `spent_s`/`ceiling_h`/`window_s`, the two deployment booleans
`checks_enabled` and `vectors`, one row per follow, and the held band capped
independently of the pager. The detail sends the same follow row plus its error
code and message, `checks_enabled` beside it *(added 2026-09-05 — this page is
about one follow's clock, and with checks off that clock is a schedule nothing
will run)*, the decision counts, the two job lists as job ids, the in-flight
check, and the passed-over ledger with `reason` verbatim.

*Amended 2026-09-15:* every follow row — list, detail and write outcome, one
shape — carries `fail_count`, the derived `retrying`, and `max_tries` beside
them, because `failing` has covered two situations since migration 0008 and
`state` alone cannot say which one a row is in. The pages compose `retry 2 of
7` / `gave up after 7 tries` off those three (the near-miss line's split, and
its coupling: the bound rides along so the count and the sentence cannot
disagree), tone them `warn` while the check is still coming back on its own
and error once it has stopped, and print `—` for a next check nothing will
act on — a paused or a gave-up row keeps a `next_check_at` that is a promise
nobody will keep. One field on the row is **not the client's to compose**:
`not_schedulable_reason` is Python's own refusal sentence (the same string the
`409` answers with; `null` on a row that will be scheduled), and the disabled
Check-now's help renders it verbatim rather than re-wording it. Two more
things arrive with the same change: an unfollow's outcome carries `spent_s`,
the rolling day the delete did **not** refund (migration 0007 — print the
tool's "not a refund" line off it when there is a number to print, and nothing
at `0.0`), and `check_now` on a follow nothing will schedule is refused `409
E_NOT_SCHEDULABLE`, with the tool's own sentence as the message — so the
control is drawn disabled on a gave-up follow with that refusal as its help,
and a refusal that still arrives (the page a beat behind the row) renders
inline as the other refusals do.

Three things worth knowing before writing the pages:

- **The follow row is the same object a write answers with** (§9,
  `dashboard.md` §21). One schema in `schemas.ts` reads both; pausing a follow
  and re-listing it must not produce two shapes.
- **Nothing here is rendered.** The rule's compressed facts
  (`8:00 floor · every 6h`), the rule as an English sentence
  (`follows.rules.describe`) and the near-miss line are all the client's to
  compose from the columns beside them. `near_miss` is
  `{"count", "of", "within_s", "edge"}` or **`null`**, and `null` means *print
  nothing* — a zero rendered as "0 of the last 25" is the one thing that band
  may not say.
- **`reason` is the exception and is not a rendering.** It is the receipt the
  check wrote and it already carries the number that made the decision; print
  it, do not re-derive it.

**The form's vocabulary does not travel** *(recorded 2026-09-05, with the
pages)*. These payloads carry a follow's *rules*; they carry none of the
*options* the add-and-edit form offers, because the Jinja form reads those out
of a `views._follow_choices` context neither payload has and putting them on
the wire would be Python owning the controls of a React form. So the React
pages **hard-code them**, in `web/src/app/dashboard/following/parts.tsx`, under
a comment naming the module each one comes from:

| Copied into `parts.tsx` | Owned by |
| --- | --- |
| `TABS`, `MODES`, `MAX_BACKFILL`, `MAX_PER_CHECK`, `MIN_CHECK_INTERVAL_S`, `DEFAULT_CHECK_INTERVAL_S` | `mcp/src/vidtheque_mcp/follows/rules.py` |
| `CHANNEL_BOXES` (the three boxes, their labels and their notes) | `mcp/src/vidtheque_mcp/dashboard/writes.py` — public there because the index form ticks the same three for the same parameter |

**The rule that comes with it: a vocabulary Python grows has to be added there
too.** A fourth tab or a third mode is a control the React form will not offer
until somebody edits that file, and nothing fails loudly when they do not — the
copy is the price of not shipping a `choices` payload, and this line is the
only thing that collects it.

They are **options and ceilings on a control, never a bound**: nothing in
`parts.tsx` validates, every value goes to the server as typed, and
`follows/params.build_rules` is the only thing that clamps or refuses — its
floor is that same `MIN_CHECK_INTERVAL_S`, and a value under it comes back
`400 E_BAD_PARAM` in Python's own sentence, on both branches.

## 6c. `GET /dashboard/api/jobs` and `/dashboard/api/jobs/{job_id}`

*Landed 2026-08-10; nine fields added 2026-09-05.* The two oldest JSON routes
on this surface — `static/jobs.js` has polled them since phase 2 — and the two
the React port found short. **The full schema and the redaction rule are
`dashboard.md` §5.4.**

| Route | Parameters | Bounds |
| --- | --- | --- |
| `/api/jobs` | `state`, `kind`, `error_code`, `degraded`, `order`, `limit`, `offset` | `limit` 1..100 (default 25), `offset` 0..10 000, `error_code` ≤ 64 chars |
| `/api/jobs/{job_id}` | none | 200 items, 60 events, 40 degraded rows |

What the port added, because the templates rendered it and the payloads did
not: per card `contents` — `{title, more, channel, note}`, the first video's
title with the rest counted after it and the channel a batch was expanded from
— plus `filters` and `notes` on the list, and `counts`, `error_counts`,
`items_capped`, `degraded`, `focus` and `stages` on one job. Nothing was
removed and no bound moved.

Four things worth knowing before writing the pages:

- **Read the numbers, not the `text` block.** Every job, item and event carries
  a server-rendered `text` object for a script with no formatter, and every
  string in it renders a number sent beside it. It is deleted with
  `static/jobs.js` (§3). `basis` is the exception — the sentence saying what
  the percentage is computed over is policy text and survives, in `notes` or
  beside it.
- **`live` is the stop condition.** When nothing is `queued|running` there is
  nothing to poll for. `poll_ms` is the server's cadence; clamp it again in the
  browser, because a page that took its interval from a payload alone would
  poll as fast as a payload said.
- **`notes` is where a filter that fell back says so**, on the same rule as
  §6a's: an unrecognised `state`, `kind`, `order` or `degraded` names the value
  that answered, and a clamped `limit`/`offset` names both numbers. It is
  policy text; render it, do not compose it.
- **`focus` is nullable and it is the key to the stage panel.** A job whose
  items never resolved to a video has nothing in focus, and `stages` is then
  the seven pipeline rows with every state `absent` — the pipeline's shape, not
  a claim about the job. Render the panel on `focus`, not on `stages.length`.

**The React list reads `filters` and `notes` back, 2026-09-05.** It had been
reading its own URL for both — a `state=nonsense` that fell back to `all` was
printed over a table of every job, with the sentence saying so nowhere on the
page. The narrowing strip now names the filters the payload's own `filters`
says ran, not the ones the query string asked for, and the empty state chooses
its wording from that same echo: a filter that took every row out reads
differently from an instance that has never queued anything.

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

The two `following` routes have no projection to state: the projection is the
deployment they are absent from (§6b). If that ever changes, `dashboard.md` §22
names the field that goes first — `follow.last_error_message`, which is
`str(exc)` from a source that could not be read, the same category as
`stages[].error`.

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

For §6b's two, the same file over `test_dashboard_following.py`'s follow
fixture: the disappearance with the write side — the decision this pair had to
make — asserted against a deployment whose other read pages still answer, the
gate and GET-only registration, both payloads' shapes against the fixture's
exact tallies, the explicit orders, the clamps with their `note:`, a `404` for
an unknown slug, the write outcome and the read agreeing key for key on one
follow, and a scan of both payloads for a rendered clock, a spoken duration and
the two sentence renderers the pages have.

CORS and cross-origin sessions, the remaining read endpoints, the React pages
and the cutover are `docs/ROADMAP.md`'s. No schema for them is stated here until
it exists. The writes have one now — §9.

## 9. The write side, for the client (2026-09-05)

*Recorded 2026-09-05, from `DECISIONS.md` ("Dashboard writes answer by content
negotiation"). The contract is `dashboard.md` §21; this is what the browser
half of it does.*

Python's existing `POST /dashboard/*` routes are the write API. There is no
`/dashboard/api/*` twin for a write and there will not be one: one URL answers
both the form and the `fetch`, so there is one route to guard, one bucket to
charge and one contract to keep.

**What the client sends**, and all three are required together:

- the **session cookie, same-origin** — `credentials: "same-origin"`, which is
  `fetch`'s default only for same-origin requests, so say it. Next never sees
  this cookie (§1d) and the browser is the only thing that holds it;
- **`Accept: application/json`**. Nothing else switches the branch: no query
  parameter, no header of ours. It must outrank `text/html` if that is named at
  all, and `*/*` is not enough — a request for anything is not a request for a
  typed outcome;
- a **form-encoded body** —
  `Content-Type: application/x-www-form-urlencoded` — with the fields the Jinja
  form posts today. The handlers read a form on both branches and none of them
  parses a JSON body.

The browser must also be *on* this origin for the write to pass §3.3: the
session cookie is ambient, so the handler requires `Sec-Fetch-Site:
same-origin` (which the browser sets itself and script cannot forge) or an
`Origin` matching `PUBLIC_URL`. A same-origin `fetch` from a ported page sends
both without being asked; a page served from a second origin would be refused
with `E_BAD_ORIGIN`, which is the cross-origin work `docs/ROADMAP.md` still
tracks.

**What it gets back.** On success, `200` and the typed outcome for that route —
the table in `dashboard.md` §21, which is where the payloads are specified so
they are specified once. On refusal, the envelope this document's §3 already
describes: `{"error", "message", "next"}` at the code's own status, plus
`retry_after_s` and a `Retry-After` header when the refusal named a delay. Both
carry `Cache-Control: no-store`.

**What a receipt echoes, and what a refusal echoes with it** *(2026-09-06)*.
`POST /dashboard/index` answers with an `accepted` block —
`{"expand", "max_items", "priority"}`, the three values `_submitted` resolved —
because a form that keeps its own state has nothing else to read a clamp back
out of: `max_items` is held to the tool's 1..200 and both vocabularies fall
back to their defaults rather than being refused, and the Jinja page re-rendered
the form from exactly this. The same block rides on the form's own two refusals
(`E_BAD_PARAM`, `E_TOO_LARGE`), which are raised after those three resolve, so
a submission told that its list is too long is not also left showing the 9000 it
typed. The guard's refusals carry none: `require_write`, the Origin rule and the
rate bucket answer before a field is read. `GET /dashboard/api/library` refuses
the same way — its resolved `filters` block, under the name the `200` uses, so
a date picker redraws from the day the server took rather than from the string
the URL carried. The shapes are `dashboard.md` §20 and §21's; the rule for a
client is that **a refusal may carry more than the envelope**, and the fields
beside `error`, `message` and `next` are the route's own, read by the page that
asked and by nothing generic.

Three responses a client has to handle by shape rather than by route:

- **`401`** is the signal to send the reader to the sign-in page. It is the
  same rule as for a read (§3, §1d): authorization is decided in one place and
  the shell has no rule of its own to keep in step.
- **`403 E_BAD_ORIGIN`** is a bug in the client, not in the reader's session —
  a write that left the origin, or one made from a page Next served from
  somewhere else.
- **`404`** on a write path means this deployment registers no write side
  (`VIDTHEQUE_PUBLIC_READONLY=1`, or `VIDTHEQUE_AUTH=none`), so the affordance
  should not have been rendered. `/dashboard/api/session` is what the shell
  asks first (§6).

**Formatting is still the client's.** The outcomes carry ints, epoch seconds,
booleans and lists — a job's state as the store's own word, a follow's
`next_check_at` as an epoch where the MCP tool sends an `iso_minute` string.
The refusal text is the exception and is deliberate: `message` and `next` are
policy and stay Python's (decision 5).

**The client's write calls, as of the index and videos ports (2026-09-05).**
`web/src/lib/dashboard/client.ts` now makes all of them through the one
`postForm` helper above: the corpus three — `indexUrls`, `reindexVideo`,
`setVideoTags` — beside the jobs pair and the six follow writes. The page-side
rule that goes with every one of them is that **a write's outcome replaces the
row it acted on, and the page does not read again to find out what changed.**
The follow detail page is where that rule tightened: it used to re-read the
follow after every write to learn whether a failure line had cleared, and
dropped that read once the outcome itself carried `last_error_code` and
`last_error_message` (`dashboard.md` §21, commit `2fed72c`). The videos row's
re-index and the manage panel's tags write never had the read to drop — they
paint the job id and the tags the outcome sent from the day they landed.

**Signing in is the same shape** *(landed 2026-09-05)*. `POST
/dashboard/login` negotiates like the other twelve: the session cookie is not
sent (there is none yet) but `Accept: application/json`, the form-encoded body
— `password`, and `next` if the reader was going somewhere — and the
same-origin requirement are as above. On success it answers `200 {"signed_in":
true, "next": "<a path under /dashboard>"}` and sets the session cookie on that
response. A refused secret is `401 E_BAD_CREDENTIAL` — deliberately not
`E_AUTH_REQUIRED`, because the rule above sends a `401` reader to the sign-in
page and this *is* the sign-in page: `dashboard.signIn` is the one call that
passes `gated: false`, so its own `401` does not run the navigation every
other write's does. Its `message` is the same sentence for both secrets, so a
client must render it rather than infer which field was wrong. Cross-origin is
the same `403 E_BAD_ORIGIN`, and `429` (the sign-in's own tight bucket) is
JSON on both branches with `retry_after_s` and a `Retry-After` header.

**`next` is fenced twice, and the second fence does not trust the first**
*(landed 2026-09-05)*. Python's `_safe_next` fences the outcome's `next`
before it ever reaches the client, and that is not a reason for the client to
skip its own check: a redirect target that arrived over the wire is an input
like any other, and the page that mints the session cookie is the worst place
on this surface to have an open redirect. Both fences judge the value after
a browser's normalisation *(amended 2026-09-16)*: only `/dashboard` or a path
under `/dashboard/` survives, so `//host`, `/\host`, `/dashboard/../admin`,
`/dashboard/%2e%2e/admin` and `/dashboard.evil` all become `/dashboard`. The session's `login_url` must be
a same-origin path too. The page applies `safeNext` twice: to its own `?next=` before that
value ever enters the hidden field the form posts, and to the outcome's `next`
before the shell navigates anywhere.

**The write rides under a real `<form>`** *(landed 2026-09-05)*. `submit`
prevents the default and does the write as a `fetch`, but the element
underneath is `<form method="post" action="/dashboard/login">`, the same
shape the rail's Sign out button has — not only an `onSubmit` handler with
nothing under it. That is what keeps `form-action 'self'` a policy that holds
rather than one that only holds while the JavaScript has loaded: a click that
lands before this tree has hydrated still reaches Python and still gets the
`303` the Jinja page always sent.

**A write keeps the keyboard where the reader is** *(recorded 2026-09-16)*.
While a write is out its control is `aria-disabled`, not `disabled`, so focus
does not fall to `<body>`; a second press is ignored. What the route answers —
the outcome or the refusal, in the API's words — takes focus when it replaces
the control or appears beside it, and a form's refusal renders next to its
actions rather than above the form, so nothing the reader is looking at moves.

## 10. The cutover: the edge, and what has to be true before it (2026-09-06)

*Recorded 2026-09-06 with the deployment files. §1a and §1d say which process
owns which path and say plainly that nothing in this repo can express it. This
section names the file that does, and the checks that prove it did.*

**`deploy/Caddyfile` is the implementation.** One rule table, both topologies:
`cloudflared → caddy → { web, mcp }` on the public box, and `caddy` alone in
front of both on a private one. Its `@python` matcher is §1a's list plus
`/dashboard/api/*`, `/dashboard/logout` and `/dashboard/`; its
`@dashboard_writes` matcher is `method POST` over the whole `/dashboard`
prefix, which is §1d's last table row and the three method-split paths in one
rule rather than four; and everything else reaches Next. Four values that are a
machine's shape rather than a contract — the port, the interface and the two
upstreams — are environment placeholders whose defaults are the compose
answers, so the table is never copied. Nothing else is configured there: no
CORS, no security headers (they are `proxy.ts`'s, per request, and an edge
cannot see a nonce), no buffering (measured: SSE and NDJSON both arrive as
produced).

**One correction to §1a's table, found while writing that file.** Under
`VIDTHEQUE_AUTH=oauth` the MCP SDK registers its own handlers at the **root** —
`/authorize`, `/token`, `/register`, `/revoke` (`auth/modes.py`, via
`create_auth_routes`; the metadata advertises `issuer_url + "/token"`) — not
under `/auth/`, which holds only the login and consent pages. An edge that
routed `/auth/*` and stopped there would hand a client's token exchange to the
front end. The public deployment is `AUTH=none` and registers none of them,
which is why nothing has noticed. They are Python's, and the Caddyfile names
them.

**The checklist, through the edge, before the URL points at it.** Each line is
a row of §1a or §1d, and content type is the assertion — a page is `text/html`
from Next, an API is JSON from Python:

1. `GET /` and `GET /demo` render, and each carries the four document headers
   (§1b), with a **different** CSP nonce on two consecutive requests — a
   repeated nonce means a prerendered shell and a policy that protects nothing.
2. `GET /videos/{id}/export.md` is Python's, and `GET /videos` is a `404`
   from Next — the library pages that stood at those two paths were removed on
   2026-09-07 (§1a) and nothing may answer there but the export.
3. `POST /dashboard/following`, `POST /dashboard/index` and
   `POST /dashboard/login` reach **Python**, form-encoded, and answer JSON or a
   `303` — never `text/html`. This is the one the whole method split exists for
   and the one nothing else catches: a `200 text/html` here is the page
   swallowing its own write.
4. Sign-in end to end in a browser: the form posts, the `Set-Cookie` lands, the
   shell navigates to the fenced `next`, and the reader's next
   `/dashboard/api/*` read is authorized.
5. Frames are same-origin: every thumbnail loads under `img-src 'self'`
   (§1b) — a broken frame under this policy is a CSP refusal, not a 404.
6. `/mcp` and `POST /api/ask` stream *through* the edge — the timestamps
   spread, not one burst at the end (`docs/deploy-public.md` §7.3).
7. `GET /healthz` answers through the edge, which is now how the deploy scripts
   check it: it proves the routing and the process in one request.
8. Rate-limit buckets key on the tunnel's header, not on the edge: two devices
   on two networks get two buckets (deploy-public.md §7.4). The edge forwards
   `CF-Connecting-IP` unchanged and validates nothing — what makes that sound
   is that it is published on loopback and mcp has no published port at all.

**Rollback.** The tunnel is still the whole exposure, so stopping the connector
is still the rollback and is still seconds. Under it, the two paths differ:

- **The image box** (`deploy/vidtheque-update.sh`): `vidtheque-update
  <previous tag>` sets `IMAGE_TAG` and re-runs compose. What changed is that
  the script fetches `deploy/Caddyfile` beside the two compose files, pinned to
  the same tag, so a rollback restores the *route table* the old images expect
  along with the images. The previous images are still on disk, which is what
  makes it a restart rather than a pull.
- **The git box** (`deploy/staging/`): a deployment of an earlier ref, which
  now re-runs `pnpm install --frozen-lockfile && pnpm build` and re-installs
  the Caddyfile before restarting. A ref from *before* this cutover has
  neither, so rolling back past it is a hand-run job — stop `vidtheque-caddy`
  and `vidtheque-web`, point the tunnel back at `127.0.0.1:8100` — and
  `deploy/staging/install.md` §11 says so.

**What is deleted after cutover, and it is not deleted here.**
`next.config.ts`'s `PYTHON_FORM_POSTS` shim keys three rewrites on
`content-type: application/x-www-form-urlencoded` because a rewrite cannot key
on a method (§1d). It is development-only and it goes **the day development
runs behind the edge too** — not the day production does.
`deploy/compose.local.example.yml` carries the guidance for that: run the stack
with caddy in front and point the `web` service at a dev server, and the thing
under test is the thing that will be deployed. Until someone does that and the
shim is removed in a commit of its own, it stays, because deleting it first
would break every local write.

## 11. The parity round: what stands, and what was decided (2026-09-16)

*Recorded 2026-09-16 while closing the front-end parity audit against the Jinja
surface at `6785195`. The audit's own artefacts are
`.agent-runs/parity/REPORT.md`; this section is only the part of it that ends in
a decision rather than in a commit, so nobody re-derives it from the screenshots
a year from now. Everything not named here was fixed.*

### 11.1 Four differences that stand

**A refusal is a `200` document (audit X1).** Python answered `401` on a
signed-out page and `404` on an unknown video, job or follow, and the refusal
*was* the document. Here the document is the shell, the read happens in the
browser, and the status the refusal carries is on the XHR — `DashboardError`
holds it, `ReadFailure` renders from it, and the page the reader sees is the
same refusal it always was. Making the document itself `404` would mean the
shell knowing the answer before it has asked, which is the one thing §1's first
decision says it must not do: the pages fetch client-side and Next never sees
the session cookie. **The status is not lost, it moved** — a client wanting it
reads `/dashboard/api/*` directly, which is where it was always authoritative.
The two that *can* be documents are, and now are: an unmatched path under
`/dashboard` and an unmatched path at the root both answer `404` with a page in
this system's own type (`app/dashboard/not-found.tsx`, `app/not-found.tsx`).

**The deployment's facts arrive after mount (audit S1).** `write_side`,
`readonly`, `auth_mode` and the version were inline in `base.html` and are read
from `/dashboard/api/session` here, so the Manage group, the demo line, Sign out
and the version are absent for one round trip and permanently if that read
fails. This is the same decision as the one above and is not separable from it:
the shell is served without the cookie. What the chassis does do is render
everything that does *not* depend on the session immediately — the wordmark
and the five sections — rather than waiting for the read to draw a rail.

**Dashboard search builds its thumbnail URLs unsigned (audit V6).** `thumb_url`
signed them with `?exp=&sig=`; `SearchView` composes
`/frames/{id}.jpg?w=192&q=70` from `frame_id`, which is the rendering choice
§14.2 of `dashboard.md` already records — the page wants the dashboard's two
widths and the facade sends the demo's. `/frames/*` takes the session cookie
and the bearer beside the signature, so a cookie session and an `AUTH=none`
instance are both fine. **The case that would break is a bearer-token dashboard
with no cookie**, which is not a deployment this surface has: every page here
reads `/dashboard/api/*` with `credentials: same-origin`. If one is ever wanted,
the fix is the payload carrying signed URLs at the dashboard's widths, not the
page learning to sign.

**Four option vocabularies are hardcoded client-side (audit V11, J8).**
`index_state`, `has` and `order` on the videos band, and the follow-rule choices
(`TABS`, `MODES`, `MAX_BACKFILL`, `MAX_PER_CHECK`, `MIN_CHECK_INTERVAL_S`), came
from `queries.py` and `follows/rules.py` through the view; they are written into
`VideosView.tsx` and `following/parts.tsx` now, and none of them is on a
payload. Nothing is wrong today and nothing tells anybody the day it is: a
vocabulary Python grows desyncs silently, and the follow rules are the worse
half, because the number in a label and the number the validator clamps to stop
being one set. **This is a contract gap, not a rendering one** — the fix is
fields on `/dashboard/api/session` or on the two listings, and it belongs in a
commit that changes the payload. Until then, a change to either vocabulary is a
change in two files.

### 11.2 Seven calls, and what outranks the audit

**Gold does not follow the pointer (audit X7).** `dashboard.css` kept the
browser's underline on every link and sent `a:hover` to `--accent`. The
underline is back, scoped to a link inside a sentence (`.col a:not([class])` —
the classed families draw their own affordance, exactly as the old stylesheet
turned it off per component). The hover colour is not: `dashboard.md` §12.2
item 3 settles what gold means on this surface and lists five things, a
hovered link is not one of them, and DESIGN.md's One Signal Rule is the reason
the list is a list. Hover takes the `--fg` step, which is what the old
stylesheet's own named link rules took.

**The rail's group heading keeps `label` (audit X9).** The audit is right that
`Manage` was sentence case at the nav items' size and is uppercase tracked mono
now. DESIGN.md's `nav-group` token says `typography: {typography.label}` and
`padding: 0.75rem 1rem 0.25rem`, which is what the port implements — the old
surface was the deviation. What *was* a gap is ported: the heading is gone
entirely in the ≤1120px strip, where it labels a column that no longer exists.

**Dashboard search is a flat ranking again (audit V4).** The port grouped a page
of hits by video. Nothing asks for that: `dashboard.md` §14.1 is written per
hit throughout — per-hit badges, the frame on the row, two links per hit —
§14.2's field table is per hit, and §20 is about the videos table.
`search.html` drew `<ol start="{{ offset + 1 }}">`, one row per moment, each
with its own title and channel, and the ranking is the answer an operator came
for. Grouping printed the title twice for a single-hit video and hid whether
the second-ranked moment was in the same talk as the first.

**The final-record note keeps the port's wording (audit J3).** The condition was
the gap and is fixed: the note appears only on a live→terminal transition,
which is what `job.html` kept it hidden in the markup for. The sentence is not
restored. `job.html` said "so reload for the final record" because its ticker
patched a handful of fields and left the rest of the document at the reading it
was rendered from; here the poll replaces the whole payload, so the page the
reader is looking at *is* the final record and "reload" would send them to
re-fetch what they already have. `useJobsPoll` gained `wasLive` for the
condition, which is the honest place for it — a ref read during a render is a
value React is free not to have re-rendered for.

**`Rows` keeps no `max` (audit V9, J9).** `videos.html` and `jobs.html` had
`max="100"`. The ceiling is `OWNER_CLAMPS.videos_max_limit` and
`views.JOB_PAGE_MAX`, neither is on a payload, and a hundred written into the
page is a second bound that is wrong on the deployment that moved its own —
which is the prompt-only limit `CLAUDE.md`'s token-discipline invariant names.
The server clamps and says so in `notes`, in its own words, and the box echoes
the page size that was *accepted*. If the boxes are ever to carry a ceiling, the
payload has to carry it first, the way `transcript.max_limit` does.

**The refused sign-in keeps its typographic apostrophe (audit S5).**
`login.html` wrote `deployment's` with an ASCII quote. Eleven other sentences
on this surface use `&rsquo;` and this one would be the odd one out; the
audit's own severity is cosmetic. What was a real loss and is restored is the
refusal's `next:` line —
"the sign-in page names which secret this deployment accepts." — which
`writes.py` sends beside the message and the page dropped, leaving the refusal
here whose recovery sentence the reader never saw.

**A null clock keeps the em dash (audit X8).** `text.iso_day` and
`text.iso_minute` printed an ASCII `-` for a timestamp that does not exist, and
`format.at`/`format.day` print `—`. The port is the one this surface already
asked for: `render.dash` is named "the one rendering for *this is not
recorded*", `render.span` and `render.elapsed` printed `—` for the same fact,
and `dashboard.md` §5.4 says the jobs listing's `finished` column is "em-dashed
while a job is queued or running" — which is exactly an `iso_minute` stamp. The
old surface printed two glyphs for one meaning depending on which module the
value came through, and a timestamp is not a different kind of absence from a
duration. DESIGN.md prescribes neither glyph; `dashboard.md` prescribes this
one, and `DASH` is the single constant both halves of `format.ts` read.

### 11.3 §1d's two lists stop being one list

`app/dashboard/[...rest]` is new, and it is what makes the designed `404`
reachable at all: Next renders a segment's `not-found.tsx` when `notFound()` is
thrown *inside* that segment, and an unmatched URL is inside nothing — it gets
the **root** `app/not-found.tsx`, which is the front door's page and knows
nothing of this surface. The catch-all is the last thing the router tries, so it
shadows no page, and what Python owns under the prefix never reaches the router
(`next.config.ts` in development, `deploy/Caddyfile` in production).

**`proxy.ts`'s matcher gains one entry and loses an invariant.** It was a
whitelist of the ported pages, one entry each, on the argument that "every page
not yet ported is Python's HTML, which carries its own policy" — an argument
that retired on 2026-09-06 with the Jinja surface. What it left behind was a
hole: a mistyped `/dashboard/…` is a document this app renders, and it was the
one document on this surface shipped with **no CSP, no `X-Frame-Options` and no
`Referrer-Policy`** on it. The new entry is
`/dashboard/((?!api/|logout$).*)` — everything under the prefix that is not one
of Python's two, which are named rather than left to luck. Each exclusion ends
where the path it names ends: `api/` carries its slash and `logout` its `$`.
Written without the anchor, as it shipped on 2026-09-16 and was corrected the
same day, the second one excused every path *beginning* with the word — and
`/dashboard/logout-now` is this app's own refusal, so the hole this entry
closes was still open on it. *Amended 2026-09-16:* the matcher is one pattern now, and every
exclusion ends at a boundary: a prefix at `/` or the end of the path
(`dashboard/api` included, so `/dashboard/api` itself), an exact path at `$`.
The root OAuth endpoints are excluded with the rest of §1a's Python paths.

So `proxy.test.ts`'s "agrees with the list every link asks" no longer reads
`isPorted(path) === matches(path)`. The two answer different questions and now
differ by exactly one thing: **`ported.ts` says whether a path is a page**,
which is what a link has to know, and **the matcher says whether it is a
document**, which since the catch-all includes the refusal every other path
renders. A page is in both, Python's two are in neither, and a non-page under
`/dashboard` is a document and not a link target. The per-entry list above the
new one stays, because it is still the record of which paths are pages.

### 11.4 Left for the branches that own them

The body type role's second half — **16px, 15px below `--bp-hand`** (DESIGN.md,
the ladder and the breakpoint list), the last line of audit D17 — is not in the
sheet, and it is not a media query anybody can add here: `styles/type.css` is
generated by `web/scripts/tokens.mjs` from DESIGN.md's frontmatter, where a role
carries one `fontSize` and no breakpoint. Nor would the old rule's own form
reach it: `body { font-size: 15px }` at ≤780px moves only text that inherits the
body size, and on `/demo` at 390px exactly one string does — a visually hidden
label. Every visible line composes a type role, so the step belongs to the role
and to the generator that emits it, which is a change to the visual contract's
machinery rather than a parity fix. `docs/ROADMAP.md` carries it.

The follow pages' budget note, `next check` column, post-follow navigation and
button weights (J6, J7, J12, J13), and the `seen` table's stacking breakpoint
(J11's second half), are another branch's — `dashboard/following/**`. The jobs
half of J11 and J13 landed here, so the two tables that stack together do again:
jobs, items and videos at 52rem, with follows, and `seen` still at 780px until
that branch moves it.

The public pages left the parity frame on the same day. `/demo` and `/paris`
no longer port the old search box and ask pane one-for-one: one client console
replaces both, with its own URL shape and no router navigation (DECISIONS.md
2026-09-16; `demo-site.md` §6.1–§6.2). A parity finding about `?ask=0`, the
post-hydration mode swap or the results skeleton is now superseded, not open.
