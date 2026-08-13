# The management dashboard — the primary surface for the index

**Status: REVIEWED (Tom, 2026-08-09). Phases 1, 2, 3 and 4 are implemented;
phase 5 is not.** Written 2026-08-09 against the tree at `7dc8226`; every fact about the
code was checked and is cited with a path. The open questions in §10 were real
forks and are now mostly resolved inline. §8 records what each phase actually
delivered as it lands.

The decision this encodes (Tom, 2026-08-09): **today the management surface of
vidtheque is the MCP server, and that is backwards.** An agent is the right
consumer of the corpus and the wrong consumer of the corpus's *plumbing*. Nobody
should have to ask a language model which model transcribed a video, why a job
has been sitting still for five minutes, or whether OCR quietly failed on
forty videos. The dashboard is where the index explains itself to a human.

It absorbs the demo site rather than forking it. `mcp/src/vidtheque_mcp/public/`
is not a thing the dashboard competes with — it is the dashboard's read-only
projection, written first, and `VIDTHEQUE_PUBLIC_READONLY` is already the gate
that decides which half you get.

Sources it must not contradict: `demo-site.md` (the facade rules, the XSS
posture, and the rate limiter are reused verbatim), `tool-surface.md` (the
dashboard calls the same implementations the tools call), `index-schema.md`
(§4 below is an audit against it), `DECISIONS.md` (auth modes, single owner).

---

## 1. Purpose and non-goals

**Purpose.** One browser page per question a human has about the index:

- What is in the corpus, and what state is each video in?
- How is one video *stored* — how many shots, which frames were kept and which
  were deduped, what the chunk boundaries are, what OCR read off the screen and
  where on the screen it read it?
- Which model produced each of those, when, and does it still match what the
  worker is serving?
- What are the jobs doing right now, what are they waiting on, and what failed
  quietly?
- Add a video, a playlist or a channel to the corpus without a terminal.

**Self-hostable, single-user, one process.** Same compose file, same image, same
SQLite file. It is a route group, not a product.

**Non-goals**, stated so nobody has to guess:

1. **Not a video player.** vidtheque deletes source media after indexing
   (`VIDTHEQUE_KEEP_SOURCE=audio` by default, DECISIONS.md #3) and the whole
   citation contract is "send the human back to YouTube at `?t=`". A player
   would be an archive tool wearing a dashboard's clothes.
2. **Not multi-user SaaS.** DECISIONS.md #2 is single-user behaviour with a
   multi-user-ready schema. There is no user management page, no roles, no
   invitations. `owner_id` stays constant `1`.
3. **Not a replacement for the MCP surface.** Agents keep talking to `/mcp`.
   The dashboard never proxies MCP and never becomes the thing an agent scrapes
   because the tools were harder to use.
4. **Not a config editor.** Every pipeline knob is an env var
   (`mcp/src/vidtheque_mcp/pipeline/settings.py:97-142`) and
   `deploy/.env.example` is the document of record (CLAUDE.md). The dashboard
   *displays* resolved settings; it never writes them. A dashboard that edits
   env is a dashboard that fights compose and loses on the next `up -d`.
5. **Not an analytics product.** No time-series charts, no "indexing velocity".
   There is no time-series table and `videos.indexed_at` at day resolution would
   only ever graph when Tom last ran a batch.

---

## 2. The shape

### 2.1 One process, one route group

The dashboard lives **inside the mcp server**, as a route group beside
`public/`. The current route list is literal and order-sensitive
(`mcp/src/vidtheque_mcp/app.py:174-181`):

```python
routes = [ *health_routes(...), *auth.routes, *frames_routes(...),
           *(public_routes() if public.enabled else []),
           Mount("/", app=mcp_app) ]          # must stay last
```

One line is added before the mount:

```python
           *(dashboard_routes(...) if dashboard.enabled else []),
```

New module `mcp/src/vidtheque_mcp/dashboard/`, sibling of `public/`.

Why not a separate service — three reasons, in the order they bind:

1. **All state lives in `mcp/` and SQLite wants one writer.** That is a CLAUDE.md
   invariant and index-schema §5.2. A second service could not open the database
   for writing without breaking it, so it would have to call back into `mcp/`
   over HTTP — which is this route group, plus a container, plus a second port,
   plus a second auth story.
2. **The job runner is an asyncio task in this process.** `PipelineRunner`
   (`mcp/src/vidtheque_mcp/jobs/runner.py:148`) is started by this app and holds
   the claim. "Index this URL" from here is the same `index_video` call the MCP
   tool makes; from a second service it is a row insert and a hope.
3. **The self-host story stays one compose service.** `docker compose up` gets
   you an MCP server, a demo page and a dashboard, or it gets you a README with
   a networking section.

**It never speaks MCP.** There is no in-process MCP client. The dashboard calls
`tools/*.run(deps, …)` directly, exactly as `/api` already does
(`public/api.py:136`, `:172`; `public/ask.py:394`, `:428`). And it never imports
anything from `worker/` — everything GPU-shaped goes through the pipeline, over
HTTP, as it does today (CLAUDE.md invariant).

### 2.2 The service layer it calls

There is already a shared layer and `/api` is already documented as a facade
over it, not a second query implementation (`public/api.py:1-18`). The dashboard
joins the same queue at the same two levels:

- **Tool implementations** — `tools/search.run` (`tools/search.py:71`),
  `tools/library.list_videos` (`:40`), `corpus_summary` (`:200`),
  `video_summary` (`:336`), `tag_video` (`:493`), `tools/segment.run`
  (`tools/segment.py:29`), `tools/indexing.index_video`
  (`tools/indexing.py:67`), `job_status` (`:359`). Every clamp, every `note:`,
  every `has_more` is the one the tool computed.
- **Raw queries** — `db/queries.py` and `jobs/store.py`, through
  `Database.read(fn)` / `Database.write(fn)` (`db/database.py:146`, `:151`), for
  the views the tool surface deliberately does not expose (the seven
  `video_stages` rows, the shot timeline, the cue pager). `/api` already does
  this once for cover frames (`public/api.py:190`), so the pattern exists.

The rule: **if a tool already answers the question, call the tool.** New SQL is
for questions the tool surface was designed *not* to answer, because a model
does not need them (§7 lists all of them).

### 2.3 Read-only is the same flag, and it is not the auth story

`VIDTHEQUE_PUBLIC_READONLY` is read in exactly one place
(`public/settings.py:61`) and resolved once at construction
(`app.py:97`). It gates four things today: write-tool masking (`app.py:136`),
the `/api` + `/` + `/static` routes (`:178`), the rate-limit middleware
(`:187`), and the ask client (`:149-153`).

The dashboard adds a fifth: **when the flag is on, the dashboard's write routes
are not registered.** Same discipline as tool masking, for the same reason
demo-site.md §1.1 gives — a route that exists and refuses is a route somebody
probes, and a button that 403s is worse UI than a button that is not there.

What the flag is **not** is an authentication mechanism. A private instance runs
with the flag off and still needs its write side gated by a credential. Those
are two different questions and §3 answers the second one.

### 2.4 Demo mode = welcome page + read-only projection

Today `/demo` serves `public/static/demo/index.html` (amended 2026-08-11:
the landing owns `/` since commit `4ddd45d`'s topology swap, and the demo's
files moved under `static/demo/` in the repo cleanup — the welcome page's
route is `/demo`, its role is unchanged): search, ask, and a six-video "in
this corpus" list. **That page is the welcome page.**
It stays, it keeps its aesthetic (demo-site.md §6: a search engine, not a
dashboard), and it gains one link — into the browsable corpus, which is the
dashboard's read-only projection.

The projection is not a second implementation. It is the same views with the
write affordances absent and two fields redacted:

| view | private (write side) | demo (`VIDTHEQUE_PUBLIC_READONLY=1`) |
|---|---|---|
| corpus overview | full, incl. resolved settings and drift banner | counts, channels, tags, coverage — no settings, no paths |
| videos table | full, with row actions | full, no actions |
| video detail | full | full |
| jobs | full, with source URLs and error text | states, codes, counts and durations — the "what does a video cost" view (§5.4) |
| index form | yes | **absent** |
| re-index / delete / tag | yes | **absent** |
| `/dashboard/api/*` | owner clamps | **public clamps, unless the caller holds a credential** |

*Amended in phase 4 (2026-08-09): "no settings" is four fields, named.* The
overview row above was written before the page existed and turned out to be
one word short of a specification — `docs/deploy-public.md` §1.1 audited the
built page and found the declared checkpoint ids, `vectors.reason`, two byte
totals and `auth={{ auth_mode }}` on it, all of which are settings by any
reading and none of which the row's own wording obviously forbade. The
projection now drops, precisely: the **declared-models panel** (the `config`
rows and their dimensions), the **drift reason** (a dimension/model mismatch
addressed to whoever set the environment), the **storage figures** (a
measurement of the operator's disk), and the **`auth=` line** in the rail foot.
It keeps the *effect* of drift — "vector search is off on this instance", which
changes what a visitor should believe about the results — and it gains one rail
item back to the welcome page, so the two halves of the public site link to each
other in both directions.

*Amended in phase 5 (2026-08-09): the last row, and it is a column heading that
was wrong rather than a projection that was missing.* Every other row here
splits on `VIDTHEQUE_PUBLIC_READONLY`, because every other row is about what a
**page** renders and a page is served to whoever the read gate let in. The JSON
facade is not: it takes parameters, and one of them —`max_text_chars=0`— is the
full-transcript hatch demo-site.md §2 reserves for an owner's agent. Phase 1
bounded it by prefix (§2.5.1), which reads as "the owner's JSON" and is only
true when the prefix implies the caller. In the deployment this document is
about it does not: `AUTH=none` has no credential to check, so the read gate is
open by design, and an anonymous visitor was collecting the corpus's transcripts
at 120 requests a minute. The bound is now **keyed off the credential**
(`public/api.py:policy_for`, `auth/credential.py:is_owner`):

| the caller holds | `/api/*` | `/dashboard/api/*` |
|---|---|---|
| nothing (incl. every request in `AUTH=none`) | public | public |
| `Authorization: Bearer <token>` | owner | owner |
| a `vidtheque_session` cookie | owner | owner |
| a socket peer in `VIDTHEQUE_DASHBOARD_TRUSTED_CIDRS` | owner | owner |

— in **every** mode, with `VIDTHEQUE_PUBLIC_READONLY` on or off. The flag is not
in the table and that is the point: a read-only deployment that *does* have a
token configured is Tom's own, and a mode-keyed clamp would have clamped its
owner.

**Trusted CIDRs count as a credential, deliberately.** The setting already
grants that network the whole write side with nothing presented at all (§3.4) —
indexing, re-indexing, tagging. A network trusted to *change* the corpus but not
to read a transcript of it would be a boundary with no shape. (Since 2026-08-13
the dashboard's read gate honors it too, not just these clamps and the write
gate — the §3.2 audit row carries the change and the field report that forced
it.) It is also the
answer to the private-LAN owner story this change would otherwise break: in
`AUTH=none` there is no credential, so the owner of a LAN instance is anonymous
by this rule and gets the demo's bounds on the JSON. Two things give them back
their reach — set `VIDTHEQUE_DASHBOARD_TRUSTED_CIDRS` to the LAN, or use `/mcp`,
which is unmasked in that mode and was always the owner's agent's real surface.
The JSON facade is a convenience over the tools, never the only way in.

**The pages keep the owner page size for every reader**, and that is a decision.
The hatch is an `/api/*` parameter that reaches no page: no template renders
untruncated transcript text and no page takes `max_text_chars`. What is left
between the two policies on a page is rows-per-page and how far an offset may
walk, on a listing the demo publishes in full anyway — so keying *that* off the
credential would paginate the browsable corpus at 24 rows to protect nothing.

Redacting job source URLs and `error_message` in demo mode is a recommendation,
not a given (open question §10.4). The argument for it: yt-dlp's failure strings
carry cookiefile paths, player-client names and the operator's politeness
settings, and `jobs.args_json` carries whatever was submitted. The argument
against: demo-site.md deliberately kept `job-status` in the public tool set
because "it is how a curious visitor sees that indexing is a real pipeline". I
think both hold — keep the view, drop the two fields.

### 2.5 Migration path from today's `public/`

Nothing is deleted and nothing is forked.

1. **`public/api.py` becomes the read half of both surfaces.** Its handlers
   already take their clamps from module constants (`public/api.py:41-48`).
   Those constants become a small policy object — public clamps (`limit` 1..20,
   `max_text_chars` forced to 400) or owner clamps (wider, still server-side) —
   chosen by which route group the request arrived through. One set of handlers,
   two policies, no second query layer. *Amended in phase 5: chosen by the
   **credential**, not the route group — see §2.4's table. The prefix still
   decides what is registered; it never decided who was asking.* This also
   settles an open question
   already logged in demo-site.md §7.4: `/api` is public-mode-only today, and a
   private deployment that wants JSON has to enable the public flag. It stops
   having to, because `/dashboard/api/*` exists in private mode.
2. **The static bundle grows a sibling.** `public/static/` keeps
   `index.html` + `app.js` + `style.css` for the welcome page; the dashboard
   ships its own files in the same tree with the same no-build discipline (or
   does not — §10.2 is the honest fork).
3. **The rate limiter moves to always-on.** It is mounted only in public mode
   today (`app.py:187`). A management surface a bot can hammer with
   `/dashboard/api/videos` is the same denial of service as a public one, and
   the limiter is already a plain ASGI middleware with a path→bucket map
   (`public/ratelimit.py:175-218`, `public/__init__.py:92-99`). It gains a
   `dashboard` bucket and loses its mode conditional.
4. **`WRITE_TOOLS` keeps deriving itself.** `public/readonly.py:16` computes the
   masked set from `readOnlyHint` in the annotations, so a tenth write tool is
   masked the day it is added. The dashboard's write routes get an equivalent:
   one list, declared once, in `dashboard/__init__.py`.

### 2.6 URL structure

Recommendation: **`/dashboard`**, with JSON under `/dashboard/api/*`.

- `/` is taken, and it is what a shared link points at. Moving the welcome page
  would break every link that has ever been copied out of the demo.
- `/admin` overclaims. There is one owner and nothing to administer *about
  users* (DECISIONS.md #2); "admin" invites a permissions model the schema
  deliberately does not have.
- One prefix is what the middleware and the auth check match on, exactly as
  `/api/*` and `/frames/*` are matched today.

Recommendation against a configurable prefix: a movable route root is a config
knob that breaks every relative link, doubles the test matrix, and buys a
security property (obscurity) that is not one.

---

## 3. Auth for the write side

### 3.1 What exists today, exactly

- Modes `none | token | oauth` (`config.py:19`, `:184-189`), resolved once into
  an `AuthBundle` by `auth/modes.py:85`.
- **`none`**: no verifier, `frame_signer=None`, no 401 anywhere in the app.
- **`token`**: `StaticTokenVerifier` (`auth/modes.py:38-55`) doing a
  `hmac.compare_digest` against `VIDTHEQUE_TOKEN` (`config.py:202`); boot fails
  without it (`config.py:232-233`).
- **`oauth`**: a full authorization server — HS256 JWTs, CIMD with DCR
  fallback, rotating hashed refresh tokens (`auth/provider.py`, `auth/tokens.py`,
  `auth/store.py`), an owner login page at `/auth/login` gated by
  `VIDTHEQUE_PASSWORD` (`auth/login.py:95`), and **a real browser session
  cookie**: `vidtheque_session`, `httponly`, `samesite=lax`, `secure` iff
  `PUBLIC_URL` is https, TTL 12 h (`auth/login.py:27`, `:102-110`;
  `config.py:92` — hard-coded, no env reader).
- The verifier is attached **only to the `/mcp` mount**, by the SDK's
  `RequireAuthMiddleware` with scope `vidtheque:read`.
- `/frames/{id}.jpg` does its own per-route check, `_authorized`
  (`http/frames.py:186-207`), accepting **all three shapes**: open in `none`
  mode, HMAC `?exp=&sig=`, `Authorization: Bearer`, or the session cookie.
- **`/api/*` has no authentication of any kind.** The rate limiter is the only
  guard.
- **There is no CSRF machinery anywhere**, and no session middleware.

### 3.2 The recommendation: reuse, don't mint

One dependency, modelled on `http/frames.py:_authorized`, applied to the
dashboard's write routes. Accepted credentials:

1. **`Authorization: Bearer <VIDTHEQUE_TOKEN>` in `token` mode**, verified
   through the existing `StaticTokenVerifier`. This is the curl/script path.
2. **The `vidtheque_session` cookie**, in `token` mode as well as `oauth`.
   Today the cookie is minted only by the OAuth login page; the dashboard adds a
   second minting path — a login form that takes the bearer token once and
   exchanges it for the same cookie, writing the same `login_sessions` row
   (`auth/store.py:174-190`). No new credential, no new table, no new hashing
   scheme. A human types the token once instead of pasting a header.
3. **`VIDTHEQUE_AUTH=none` → the write side is refused, not open.** This is the
   one place I break symmetry with `/frames/*`. `none` is the documented
   public-demo mode; an unauthenticated instance behind a Cloudflare tunnel with
   a live "index this URL" button is remote-yt-dlp-as-a-service pointed at
   Tom's residential IP. In `none` mode the dashboard serves the read-only
   projection and says why, with the one-line fix (`VIDTHEQUE_AUTH=token`).

   *Amended in phase 3 (2026-08-09): **absent, not refused.*** `none` mode
   registers no write routes **and no login page**, exactly as
   `VIDTHEQUE_PUBLIC_READONLY=1` does, so the whole write side 404s rather than
   403s. §2.3's argument — a route that exists and refuses is a route somebody
   probes — applies with more force to a sign-in form that could never grant
   anything. The "says why" survives, in the rail foot, which DESIGN.md already
   gives the job of carrying what a deployment is allowed to do. §8's phase 3
   table has the rest.

The escape hatch for the home-lab case: `VIDTHEQUE_DASHBOARD_TRUSTED_CIDRS`,
**default empty**, matched against the **socket peer address only — never
`VIDTHEQUE_TRUSTED_IP_HEADER`**. That header is documented as
trust-on-configuration (demo-site.md §4.3) and is correct for rate-limit
bucketing and disqualifying for authorization; any client can send it. A
loopback-only default is not enough, because under compose the peer is the
bridge gateway, not `127.0.0.1` — so the var exists, ships empty, and a
`10.0.0.0/8` in `.env` is one line.

### 3.3 Two rules that fall out of using a cookie

Both are cheap now and expensive later:

- **No state-changing GET, ever.** `SameSite=Lax` sends the session cookie on a
  top-level GET navigation. `<img src="…/dashboard/videos/X/delete">` in any
  page Tom opens would fire. Every write is POST with a JSON body.
- **Origin check on every write.** `SameSite=Lax` already withholds the cookie
  from cross-site `fetch`, so this is belt-and-braces: the handler requires
  `Origin` (or `Sec-Fetch-Site: same-origin`) to match `PUBLIC_URL`'s origin,
  which the config already builds and the OAuth `resource` already depends on
  (`config.resource_url`). JSON-only bodies mean no cross-site form POST can
  reach a handler in the first place.

*Amended in phase 3 (2026-08-09): **the bodies are HTML forms, so the Origin
check carries the weight.*** §10.2 resolved this surface as server-rendered
documents that work with JavaScript off, and a write side reachable only by
`fetch` would not be that. So the check became asymmetric: an **ambient**
credential (the session cookie) requires *positive* same-origin evidence, and a
request carrying neither `Sec-Fetch-Site` nor `Origin` is refused; a bearer
token, or a trusted peer, is not ambient, so absent headers still pass and
`curl` needs no ceremony. The no-state-changing-GET rule is unchanged and is
asserted. **There is no CSRF token and none is planned** — it would need
server-side state or a signed cookie pair to guard a surface that already
refuses every request a cross-site page can generate.

### 3.4 Alternatives I did not pick

- **Reverse proxy only** — ship nothing, document Caddy basic-auth or Authelia.
  Honest, zero code, and wrong for the same reason OAuth is in the server rather
  than behind Cloudflare Access (HANDOFF-2026-08-08): self-hosters do not have
  Tom's proxy, and "the dashboard is secure if you configure something else
  correctly" is a footgun with a CVE-shaped shadow.
- **OAuth only.** Correct, already built, and it makes `VIDTHEQUE_PASSWORD` plus
  a CIMD flow mandatory for someone who just wants a table of their videos on
  a LAN.
- **Trusted CIDRs on by default (RFC1918).** Zero friction, and it silently
  grants indexing to everyone on the network the instance is on. Not a default I
  will set on someone else's behalf.

**Amendment, 2026-08-09 review.** The allowlist is decided on the socket peer,
which behind a reverse proxy or a tunnel is the *proxy's* address — cloudflared
speaks from loopback, or from a docker bridge under compose. So a CIDR covering
that network makes every anonymous visitor arriving through the proxy a trusted
peer: owner clamps, the full-transcript hatch, and the credential-free write
side. `settings.warn_on_proxy_origin_cidrs` logs a WARNING at boot when the
allowlist overlaps loopback/RFC1918/ULA **and** a trusted-IP header is
configured, since that header exists precisely because the socket peer is not
the client. A warning rather than a refusal: a LAN box on 192.168/16 with no
proxy is indistinguishable at boot, and taking someone's owner access away on a
heuristic is the worse failure. Rejecting proxy-origin CIDRs outright, or
demanding a credential for anything that arrived through the configured proxy,
is the deeper fix and belongs to the security audit (`deploy-public.md` §1.1).

---

## 4. What the index actually records — an audit

Every page in §5 was checked against the migrations before it was designed. Four
findings change the design; they are here rather than buried in a page.

### 4.1 Provenance exists, per video and per stage — no schema change needed

`video_stages` (`db/migrations/0001_initial.sql:86-99`), PK `(video_id, stage)`,
`WITHOUT ROWID`:

`video_id`, `stage`, `state`, `model_key`, `stage_version`, `started_at`,
`finished_at`, `error`.

- `stage ∈ (fetch, stt, chunk, text_embed, keyframe, ocr, frame_embed)`
- `state ∈ (pending, running, done, failed, skipped)`
- the completion timestamp is **`finished_at`**, not `completed_at`

What each stage writes into `model_key`:

| stage | `model_key` | written at |
|---|---|---|
| `fetch` | `yt-dlp-<version>` | `pipeline/sources.py:443-446`, `pipeline/runner.py:400` |
| `stt` | `config['stt.model']` (`large-v3`) or `youtube-{asr\|subs}-{lang}` | `pipeline/runner.py:563`, `:581`, `:624` |
| `chunk` | `chunk-<target>-<overlap>` | `pipeline/runner.py:632-634` |
| `text_embed` | `config['text_embed.model']` | `pipeline/runner.py:653`, `:702` |
| `keyframe` | `scenedetect-<detector>-w<max_width>+<decode-path>` | `pipeline/runner.py`, `_keyframe_model_key` |
| `ocr` | `config['ocr.model']` | `pipeline/runner.py:793`, `:855` |
| `frame_embed` | `config['frame_embed.model']` | `pipeline/runner.py:862`, `:916` |

So the provenance panel (§5.3) needs **no schema addition**. Five caveats the
page must be honest about rather than paper over:

0. **`+` separates contract from provenance** (index-schema §1.3). A corpus
   indexed across the 2026-08-09 fused-decode change carries both
   `scenedetect-screencast-w1280` and `scenedetect-screencast-w1280+fused`, and
   the older one is **not** out of date — it is the same detector reading
   slightly differently resampled pixels. A panel that flags "stale" by string
   inequality would light up the whole corpus; compare the part before `+`.

1. **A failed or skipped stage has no `model_key`** — it is set to `NULL` on
   failure (`pipeline/runner.py:1152`), on skip (`:1174`), and on force-reindex
   (`_invalidate_stages`, `:1204-1216`). Provenance records what *succeeded*,
   never what was attempted. The page renders `—` and does not guess.
2. **`model_key` is the corpus's declared model, not the worker's reported one.**
   `config` is written only by migrations — the sole application access is a
   single read at boot (`db/database.py:92`). The worker's actual answer is
   checked live by `Database.note_worker_drift` (`db/database.py:181-199`),
   which *disables vectors* on a mismatch rather than recording it. So the
   overview page shows the declared model **and** `db.vectors` / `writes_allowed`
   state side by side — that pair is currently visible only on `/healthz`
   (`http/health.py:22`), and "the corpus says `Qwen/Qwen3-Embedding-0.6B`, the
   worker is serving something else" is exactly the failure a dashboard should
   catch on the first screen.
3. **`stage_version` is never incremented.** Declared `DEFAULT 1`
   (`0001_initial.sql:94`), read once (`pipeline/store.py:140`), never written to
   anything but 1. It is a reserved field. Either wire it or leave it off the
   panel — §10.5.
4. **No build version is recorded anywhere except `fetch`.** `stt` records
   `large-v3`, not the whisperX release; `text_embed` records the checkpoint id,
   not the worker image digest. `fetch`'s `yt-dlp-<version>` is the only
   genuinely versioned provenance in the table — which, given the overnight
   bot-check saga, is also the one that mattered most. **If Tom wants "which
   build produced this", that is a prerequisite contract change**: one nullable
   `video_stages.worker_version TEXT`, populated in `stage_finished`
   (`pipeline/store.py:158-184`) from the worker's reply, and `index-schema.md`
   §1.3 updated in the same commit. I have not assumed it; §10.5.

### 4.2 There are no per-video counts, and `list-videos` has two fields that lie

`videos` carries **no** `n_cues` / `n_chunks` / `n_keyframes` / `n_ocr_lines`.
Corpus-wide counts are one flat query (`_CORPUS_SQL`, `db/queries.py:1123-1137`).
Per-video, only `keyframe_count` (`:1349`) and `chapter_count` (`:1262`) exist as
functions at all.

Worse: `list-videos` declares `cues` and `frames` in `LIST_FIELDS`
(`tools/library.py:29-30`) and `_list_record` sets both to the **empty string**
(`:190-191`). They have never been computed.

Design consequence, and it is the token-discipline rule applied to a web table:
**the videos table shows no per-row counts.** It shows what `_LIST_SQL` already
computes — the three coverage booleans (`db/queries.py:1028-1049`) — plus
`index_state`. Counts appear only on the *detail* page, one video at a time,
where four `COUNT(*)`s ride covering indexes (`cues_time`, `chunks_time`,
`ocr_time`, `keyframes_live`). A 50-row page must never fan out into 200 counts.

Separately: the two empty fields should be computed or dropped from
`LIST_FIELDS`. The dashboard makes a placeholder that a model tolerates into a
blank column a human reads as a bug. Backlog item, not this contract.

### 4.3 There is no `scenes` table — shots live on the keyframes

`keyframes` (`0001_initial.sql:169-191`): `id`, `video_id`, `ord`, `t_s`,
**`shot_id`, `shot_start_s`, `shot_end_s`**, `phash` (signed 64-bit),
`sharpness`, `width`, `height`, `jpeg_path`, `jpeg_bytes`, **`dup_of`**,
`ocr_state ∈ (pending, done, empty, failed, skipped)`.

So the "scene timeline" is a `GROUP BY shot_id` over `keyframes`, on the existing
`keyframes_shot(video_id, shot_id)` index — not a table read. `dup_of IS NOT
NULL` marks phash-deduped frames, which are also set `ocr_state='skipped'`
(`pipeline/store.py:272-306`); the timeline must show them dimmed, because "why
does this shot have no OCR" is answered entirely by that column.

Wire frame ids are `<public_id>-<ord:05d>` (`http/frames.py:50-57`), **not**
`keyframes.id`; the file is `jpeg_path`, relative to `VIDTHEQUE_DATA_DIR`
(`pipeline/paths.py:42-44`).

### 4.4 The jobs tables have everything the war story needs, and one thing is invisible

From `jobs`, `job_items`, `job_events` (`0001_initial.sql:260-320`):

- **`jobs.not_before`** is the backoff column. `defer_job`
  (`jobs/store.py:215-239`) sets `state='queued'`, `not_before = unixepoch() + n`;
  `claim_next` (`:126-156`) honours it. **`job-status` never selects or prints
  it** (`_JOB_SQL`, `jobs/store.py:497-512`). So during the overnight batch,
  "this job is deferred for another 240 seconds" was true and unobservable from
  every surface vidtheque has. Surfacing it is a read change, not a schema
  change, and it is the single highest-value line on the jobs page.
- `job_items.attempts` / `max_attempts` (default 3) is the retry counter,
  incremented in `claim_item` (`:159-179`). **`max_attempts` is not constant per
  item**: an item whose budget was spent on `E_RATE_LIMIT` is granted one more,
  lazily, up to `RATE_LIMIT_ATTEMPT_CEILING` (6) — `_extend_for_rate_limit`,
  `jobs/runner.py`. A block is a fact about the box, not about the video, so
  spending the retry budget on it retires videos for something they did not do
  (research/ytdlp-usage-audit-2026-08-10.md §1). The page needs no change — it
  renders both numbers — but "4 / 4" is now a state a row can be in, and each
  grant writes its own `job_events` warn row saying why.
- The deferral **reason** is sticky on `jobs.error_code` **only for
  `E_RATE_LIMIT`** (`_sticky_error`, `jobs/runner.py:353-365`); every other
  deferral writes nothing to `jobs` and exists only as a `job_events` warn row,
  `"retrying in {delay}s after {code}: {message}"` (`jobs/runner.py:324-332`).
  So the per-item history panel reads `job_events`
  (`job_events_by_job(job_id, id)`), which is the only place a deferral timeline
  exists at all.
- `ItemFailed.retryable` is **not persisted** (`jobs/runner.py:52-71`). The page
  cannot say "this will retry" from a row; it infers "deferred" from
  `state='queued'` with the job's `not_before` in the future, and "retrying"
  from `attempts < max_attempts`.
- Backoff values: source-supplied `retry_after_s`, else
  `VIDTHEQUE_RATE_LIMIT_BACKOFF_S` (default 5400 — 90 minutes, the measured
  bot-check wave; it was 300 until the 2026-08-10 audit) for `E_RATE_LIMIT`,
  else 5 s (`jobs/runner.py:346-351`). The countdown line matters more at this
  scale, not less: a deferral is now an hour and a half of a job looking idle.
- **`degraded_items`** (`jobs/store.py:553-571`) — items that finished `done` on
  a video with a `failed` stage. Its own docstring names the silent loss: the
  item is `done`, the job is `done`, `n_failed` is 0, and a search channel is
  simply missing. This is the most valuable thing the jobs page can show and it
  already has a query.

### 4.5 `data_status` is derived four different ways, with four vocabularies

Not a column anywhere. Computed at:

| scope | values | where |
|---|---|---|
| per video | `failed`, `no_transcript`, `no_ocr`, `no_frames`, `ok` | `tools/library.py:477-486` over `queries.coverage` (`:1144`) |
| corpus | `empty`, `indexing`, `degraded`, `partial`, `ok` | `tools/library.py:235-245` over `queries.gaps` (`:1210`) |
| empty search | `empty`, `ok` | `tools/search.py:696-725` |
| `vidtheque://context` | `indexing`, `empty`, `ok` | `tools/resources.py:196` |

**The dashboard prints the tool's word verbatim and never invents a fifth
vocabulary.** Where it needs a colour, it maps from the string and falls back to
neutral on anything unrecognised. Whether the four should be unified is a
question the dashboard raises rather than answers — §10.6.

---

## 5. The pages

Five routes. For each: what it reads, and what it deliberately does not show.

### 5.1 `GET /dashboard` — corpus overview

**Reads.** `tools/library.corpus_summary` (`tools/library.py:200`), which is
already `queries.corpus_rollup` (`:1140`) + `coverage` (`:1144`) +
`channel_rollup` (`:1158`) + `tag_rollup` (`:1181`) + `recent_indexed` (`:1198`)
+ `gaps` (`:1210`). Plus `Database.config` (the four declared model rows),
`db.writes_allowed` / `db.vectors` (today only on `/healthz`,
`http/health.py:22`), and `os.stat` on the database file.

**Shows.** The flat counts (videos, hours, cues, keyframes, OCR lines);
coverage gaps (`transcript_no_ocr` — the "indexed but no OCR" set); active and
recently-failed job counts; channel and tag rollups; the declared models next to
the live vector state; disk.

For disk, use `SUM(jpeg_bytes)` over `keyframes` rather than walking the
filesystem — the schema already stores the number, and a `du` over
`keyframes/` is a directory walk that grows with the corpus while the page is
loading. Database size from `os.stat`; both are cheap and honest.

**Does not show.** Any chart with a time axis (§1 non-goal 5). Any exact total
that would need a second count query (`has_more` over totals, CLAUDE.md). Any
filesystem path — `media_path`, `audio_path`, `jpeg_path` and the data dir are
operator detail that must not leak into a page that might be screenshotted.

### 5.2 `GET /dashboard/videos` — the table

**Reads.** `tools/library.list_videos` (`tools/library.py:40`) → `_LIST_SQL`
(`db/queries.py:1028-1049`) + `probe_videos` (`:1088`) for `has_more` +
`video_tags` (`:1106`). Cover thumbnails from `_cover_frames`
(`public/api.py:196`) — one grouped query for the whole page, already written.

**Columns.** thumbnail · title / channel · published · duration ·
`index_state` (`pending|indexing|ready|failed|stale`) · coverage strip (the
`t/o/f/-` string from `_coverage`, `tools/library.py:169-174`, rendered as three
pills) · tags · indexed date.

**Filters.** `q`, `channel`, `published_after/before`, `indexed_after/before`,
`tags`, `has`, `order` — all already parameters of `list_videos`. Plus one it
does not have: **filter by `index_state`**. The partial index
`videos_state(index_state) WHERE index_state <> 'ready'`
(`0001_initial.sql:81`) exists for exactly this query and nothing uses it. New
service-layer parameter, §7.

**Clamps.** Owner policy: `limit` 1..100, default 50 (public policy is 1..50 /
24, `public/api.py:47-48`). Server-side; `?limit=100000` is clamped, not
honoured.

**Row actions** (write side only): re-index (force), tag, delete.

**Delete is blocked on the pipeline, not the dashboard.** `jobs.kind` permits
`'delete'` (`0001_initial.sql:260-284`) and index-schema §6.2 designs it as a
`delete_video` job — but nothing implements it: the runner's
`NotImplementedPipeline` raises `E_NOT_IMPLEMENTED` (`jobs/runner.py:145`) and
the real pipeline handles index/reindex only. **Do not ship a button that queues
a job that fails.** Delete arrives in the phase that implements the job (§8).

**Does not show.** Per-row counts (§4.2). Per-row job history — that is one
click away and one query per row is the fan-out the rule exists to prevent.

### 5.3 `GET /dashboard/videos/{video_id}` — the detail page

The reason the dashboard exists. Five panels.

**Header.** The `videos` row: title, channel, published, duration,
`index_state`, `indexed_at`, source URL, language, and `data_status` from
`video_summary` (`tools/library.py:336`) printed verbatim (§4.5).

**Provenance panel.** The seven `video_stages` rows as they are: stage, state,
`model_key`, `started_at` → `finished_at` (and the elapsed time that falls out of
them), `error`. **This has no equivalent anywhere in the MCP surface** —
`job-status` deliberately collapses the seven stages into five *wire* stages for
the model's benefit (`WIRE_STAGES`, `jobs/store.py:32-38`). A human wants the
seven, with the model that produced each and the caveats of §4.1 rendered as
`—` rather than as a guess.

**Scene timeline.** One bar per shot across the video's duration, from
`keyframes GROUP BY shot_id` (`shot_start_s`, `shot_end_s`), marked where a
keyframe was kept and dimmed where `dup_of IS NOT NULL`. Click a shot, jump the
keyframe strip. New query, §7 — one grouped query for the whole video, never one
per shot.

**Keyframe strip.** `queries.keyframes_by_ord` (`:1411`) /
`keyframes_in_span` (`:1432`), paged. Each frame is
`/frames/{public_id}-{ord:05d}.jpg?w=192&q=70` through the existing derived cache
(§6.4). Per frame: `t_s`, `ord`, `sharpness`, `ocr_state`, and whether it is a
`dup_of` another. **Never inline base64** — CLAUDE.md invariant, and a page of
forty base64 JPEGs is the byte analogue of the token blowup that invariant
exists to prevent.

**Transcript browser.** `cues` paged by `seq` on `cues_time(video_id, start_s)`:
`start_s`, text, `origin` (`whisperx | yt_manual | yt_auto`), `avg_logprob` when
present — the last two are how a human sees that three of the fifty-seven videos
came in via captions (HANDOFF-2026-08-09). Chunk boundaries overlaid from
`chunks` (`first_cue_id` / `last_cue_id`), because "what exactly is the
embedding unit" is one of the questions this page exists to answer.

**OCR browser.** `ocr_frames` (the searchable unit, `0003_ocr_frame_fts.sql:40`)
per keyframe, with the `ocr_lines` behind it: `line_no`, `text`, `conf`, and the
box `x0,y0,x1,y1`. Those coordinates are **already normalised 0–1 at write time**
(`pipeline/store.py:375-379`), so drawing them over the frame costs nothing and
is the single most convincing thing on the page — it is the difference between
"OCR ran" and "here is what it read, and where".

**Does not show.**

- `words_json` — ~10% of the database (DECISIONS.md) and not a thing a human
  reads. The panel says word timings are present; it does not dump them.
- Raw embedding vectors. `vec_chunks` is 1024 floats and `vec_frames` is 1152;
  the page shows the dimension and the model, never the numbers.
- `chapters_json` / `heatmap_json` raw — chapters render from the `chapters`
  table, which is what everything else reads.
- `media_path`, `audio_path`, `jpeg_path` — presence, not location.

### 5.4 `GET /dashboard/jobs` and `/dashboard/jobs/{job_id}` — the war-story page

The motivating incident is in HANDOFF-2026-08-09: YouTube's bot-check was
misclassified as a permanent failure, then correctly reclassified as throttling
with defer/backoff — after which the honest state of the system was "sixteen
videos are in a pool, waiting, and coming back". Nothing rendered that. The test
of this page is whether 03:00 Tom sees it in one glance.

**Reads.** `jobs_store.list_jobs` (`:528`, already filters
`all|active|failed|done`), `job_items` (`:540`), `item_counts` (`:584`),
`item_error_counts` (`:593`), `degraded_items` (`:553`),
`nonproductive_reasons` (`:628`), `item_stages` (`:645`), plus `job_events`
(no accessor exists yet — §7) and two columns `_JOB_SQL` does not currently
select, `not_before` and `priority`.

**Per job.** state · kind · priority · `n_done` / `n_failed` / `n_skipped` /
`n_cancelled` (recomputed, as `_JOB_SQL` already does — never the trigger
rollup) · created / started / finished · **`not_before` as a live countdown
whenever it is in the future**, with `error_code` beside it. That row is the one
that was missing.

*Amended 2026-08-13 (Tom: "created is not enough").* The **listing** printed
only `created`, so "when did the overnight batch actually end" was a question
you had to open a job to answer. It carries a `finished` column now, formatted
by the same `iso_minute` as every other stamp on the surface, em-dashed while a
job is queued or running — the value arrives while the page is open, so the
string is formatted server-side and patched by the same 2 s tick that patches
the state pill.

**Per item.** `state` · `stage` · `stage_pct` · `attempts` / `max_attempts` ·
`error_code` · `error_message` · the tail of its `job_events`, which is where a
non-rate-limit deferral is the *only* record (§4.4).

**And the degraded list.** `degraded_items` for every recent `done` job, because
`done` + `n_failed=0` + missing OCR is a failure mode this project has already
shipped twice.

**Live progress: polling, not SSE.** 2 s while any job is `queued|running`,
stopped when none are, one `list_jobs` + one `item_counts` per tick. A
long-lived connection per open tab, against a single-process server that also
holds the only SQLite writer, for a page watched for minutes a week, is a
lifecycle problem bought with nothing. The tick rate is also clamped
server-side by the rate limiter, so a stuck tab cannot become a load generator.

**Does not show.** `args_json` verbatim — it can carry cookiefile paths,
politeness overrides and raw URLs; render the parsed fields. Stack traces (there
are none: `error` is truncated to 500 chars at `pipeline/store.py:183`). And in
demo mode, source URLs and `error_message` — codes and counts only (§2.4).

### 5.5 `GET|POST /dashboard/index` — the index form

Write side. Fields: URL or list of URLs, `expand`
(`none | playlist | channel_recent`, `tools/indexing.py:36`), `max_items`,
`tags`, `channels` (`all | transcript | ocr | frames`, `:37`), `priority`,
`force_reindex` — **every one of them already a parameter of
`tools/indexing.index_video` (`tools/indexing.py:67-79`) with its clamp already
written**: `max_items` clamped 1..200 default 25, `urls` capped at 10 entries,
tags validated against the namespace rules. The form adds no policy; it renders
a signature that is already bounded, which is the whole argument for building
the dashboard on the service layer instead of beside it.

POST → `index_video` → job id → redirect to `/dashboard/jobs/{job_id}`.

**Refuses honestly.** When `db.writes_allowed` is false — the config/dimension
mismatch path (`tools/indexing.py:99`) — the form renders disabled with the
reason shown, rather than accepting a submission that will `E_FEATURE_DISABLED`.

**Does not show.** Anything editable about the pipeline (§1 non-goal 4). A
read-only "resolved settings" panel is useful and in scope; the write half is
compose's job.

One thing the form must handle that the tool does not: **the ten-URL cap is
lower than a real batch.** HANDOFF-2026-08-09 tells Tom to retry sixteen ids in
one `index-video` call — which `tools/indexing.py` would reject at
`urls accepts at most 10 entries`. The form should either split a paste into
jobs of ten client-side and say so, or the cap should move. §10.7.

---

## 6. Discipline carried over — the byte analogues

Token discipline is a rule about what a payload costs its consumer. A browser has
the same problem with different units, and the same answers.

1. **Every list view is double-capped** — rows *and* bytes — like every list
   tool (CLAUDE.md). Same `clamp` helper, same server-side enforcement. A limit
   in the URL bar is an input, not an instruction.
2. **`has_more`, never a total.** `probe_videos` (`db/queries.py:1088`) is
   already the bounded count probe over the same CTE; the table prints
   "50 shown, more available" and a next link. No `COUNT(*) FROM videos` in any
   page. The overview is the one exception and it is a single flat query written
   for it (`_CORPUS_SQL`).
3. **Expensive paths bounded independently of `limit`.** Per-video counts only
   on the detail page (§4.2). OCR boxes only for the frame in view. The scene
   timeline is one grouped query. No page issues a query per row, ever.
4. **Images go through the cache that exists.** Every thumbnail is
   `/frames/*?w=&q=` and lands in `DerivedCache` (`http/derived.py:119`):
   clamped 64..1280 and 20..95 (`:48-54`), byte-capped by `DERIVED_CACHE_MB`
   (256 MB default), single-flight per key, LRU. The dashboard picks widths from
   a **fixed set** — 192 (strip), 512 (detail), 1280 (lightbox) — so the cache
   holds three variants per frame instead of one per browser window. Never
   inline base64.
5. **`loading="lazy"` and explicit `width`/`height` on every frame image.** CLS 0
   is a shipped property of the site (HANDOFF-2026-08-09) and a keyframe strip is
   the easiest place in the project to lose it.
6. **XSS posture identical to demo-site.md §6.2, for a stronger reason.** The
   dashboard renders *more* adversarial text than the demo does: OCR lines, video
   titles and descriptions, channel names, tag names, yt-dlp's own error strings.
   Two rules, both testable: every string becomes a DOM text node — no
   `innerHTML`, `insertAdjacentHTML`, `document.write` or `eval` anywhere — and
   every URL reaching an `href` or `src` passes `safeUrl()`, which returns only
   `http(s)`. The existing assertion in `mcp/tests/test_public.py:681,699`
   **extends to the dashboard bundle**; it is not copied into a second test that
   can drift.
7. **One writer, one read pool, shared limits.** The dashboard shares
   `VIDTHEQUE_QUERY_TIMEOUT_S`, `VIDTHEQUE_MAX_CONCURRENT_SEARCHES` and the
   `search_semaphore` on `Deps` (`tools/base.py:21`). It does not get its own
   pool; index-schema §5.2 is not negotiable per-surface.
8. **Rate limited in every mode** (§2.5), with a `dashboard` bucket generous
   enough for a human clicking and tight enough that a stuck polling tab is
   bounded.

---

## 7. What has to be built underneath

All read-only additions to modules that already exist. **No new table.**

**Shipped in phase 1**, except `worker_version`; the two `jobs/store.py` items
**shipped in phase 2**, along with three this list did not anticipate —
`job_items` had to start selecting `attempts`/`max_attempts` (incremented since
the retry loop landed, read by nobody), `list_jobs` gained an `offset` so the
table pages with `has_more`, and `degraded_counts(job_ids)` is the grouped
version of `degraded_items` a table of rows needs. One thing this list got
wrong: `list_videos` did not just
need a new filter, it needed the *existing* clause to become one. `_RESOLVE_SQL`
hard-codes `index_state IN ('ready','stale')`, which is the right meaning of "in
the corpus" for search and for a model browsing the library, and the wrong one
for a page whose whole job is "what state is each video in" — a `pending` video
was invisible to every surface vidtheque has. It is now
`CorpusFilter.index_states`, defaulting to that same pair
(`queries.QUERYABLE_INDEX_STATES`), and the dashboard passes `all`.

In `db/queries.py`:

- `list_videos` gains an `index_state` filter (the partial index exists, unused).
- `video_stages(video_id)` — the seven rows. Nothing reads them today except the
  pipeline's own planner (`pipeline/store.py:140`) and `item_stages`
  (`jobs/store.py:645`), which collapses to wire stages.
- `shot_timeline(video_id)` — `GROUP BY shot_id` over `keyframes`.
- `cue_page(video_id, offset, limit)` and `chunk_spans(video_id, …)`.
- `ocr_for_frames(keyframe_ids)` — lines plus boxes, bounded by frame count.
- `per_video_counts(video_id)` — the four `COUNT(*)`s, detail page only.
- `keyframe_bytes_total()` — for the overview's disk figure.

In `jobs/store.py` (all shipped in phase 2):

- `job_event_page(job_id, item_id, limit)` — newest first, on `job_events_by_job`.
- `_JOB_SQL` selects `j.not_before` and `j.priority` (§4.4), plus `defer_s`, the
  remainder of the backoff computed on the clock the column was written against.
- `job_items` selects `attempts` / `max_attempts`; `list_jobs` takes an `offset`;
  `degraded_counts(job_ids)` answers the table's badge in one grouped query.

In `public/api.py`: the clamp constants become a policy object (§2.5.1).

**The one possible schema change** is `video_stages.worker_version TEXT` (§4.1
caveat 4) and it is Tom's call. If it lands, `index-schema.md` §1.3 changes in
the same commit — CLAUDE.md's contract rule.

**Two pre-existing bugs this work makes visible**, both backlog rather than
scope: `list-videos`'s `cues`/`frames` fields are declared and always empty
(`tools/library.py:190-191`), and `login_session_ttl_s` is hard-coded with no env
reader (`config.py:92`) while every other tunable has one. **The second is
fixed in phase 3** — it is `VIDTHEQUE_DASHBOARD_SESSION_TTL_S`, and the OAuth
login's session honours the same number, because it is the same cookie and the
same row.

---

## 8. Rollout — five shippable phases

**Phase 0 — today.** `public/` is the embryo. Nothing to do.

**Phase 1 — read-only dashboard, private mode. ✅ SHIPPED 2026-08-09.** The
`/dashboard` route group, `/dashboard/api/*` with owner clamps, three pages:
overview, videos table, video detail. No writes, no new auth (read views follow
whatever `/frames/*` already accepts). This is the highest value per line in the
whole plan — the provenance panel and the four browsers — and it incidentally
delivers demo-site.md §7.4, a JSON facade in private mode.

What landed, against what this document specified:

| §  | promised | shipped |
|---|---|---|
| 2.1 | route group in this process, before the `Mount("/")` | `dashboard/`, wired at `app.py:191` |
| 2.5.1 | `public/api.py`'s clamps become a policy object | `ClampPolicy`, `PUBLIC_CLAMPS` / `OWNER_CLAMPS`; `api_routes(policy, prefix, ask=)` serves both surfaces from one set of handlers |
| 2.5.3 | the rate limiter loses its mode conditional | a `dashboard` bucket, installed in every mode. **Diverged:** in a *private* deployment it is the only bucket — `/frames/*` is not charged. One detail page asks for ~48 frames, and 120/min would refuse the second page load. The owner is not that bucket's threat model; a public deployment is unchanged |
| 2.5.4 | one declared list of write routes | `dashboard.WRITE_ROUTES`, empty, asserted empty |
| 2.6 | `/dashboard`, JSON under `/dashboard/api/*` | as specified, not configurable |
| 3.2 | bearer or the existing session cookie; `none` serves read-only | as specified, plus one credential this row did not list: **a socket peer in `VIDTHEQUE_DASHBOARD_TRUSTED_CIDRS` passes the read gate too** (2026-08-13, Tom's call). §3.4 already granted that peer the whole write side and §4's table calls it an owner, but `guarded()` checked only the bearer and the session — so a trusted LAN peer could submit an index job and get a sign-in page for the dashboard it posted from, the "boundary with no shape" lived in the other direction. `peer_trusted()` in `dashboard/access.py` is now the one place the peer is read, shared by both gates. Inert on a public deployment: G2a requires the list empty there and the settings refuse to boot when a CIDR covers the proxy. Read views are open in `none` (the corpus already is, through `/mcp` and `/frames`) and need a credential in `token`/`oauth` — pages get an HTML 401 naming the fix, `/dashboard/api/*` gets the typed JSON one |
| 3.3 | no state-changing GET; Origin on every write | `require_write()` ships now, refusing in `none` mode and checking `Sec-Fetch-Site`/`Origin`, with tests. A test also asserts every registered route is GET-only |
| 4.1 | provenance from `video_stages`, four caveats | all seven stages in pipeline order, a stage with no row rendered `absent`, `model_key` NULL as `—`, and the caveats as a panel note. `stage_version` is read and **not shown** (§10.5's open half) |
| 4.2 | no per-row counts; counts on the detail page | coverage pills in the table, `per_video_counts()` on the detail page only. A test asserts the read count does not grow with the page size, on both pages |
| 4.5 | print the tool's word, never a fifth vocabulary | `data_status` and every state string printed verbatim; colour maps *from* the string and falls back to neutral |
| 5.1 | overview: counts, gaps, rollups, declared models beside live vector state, disk | as specified. `SUM(jpeg_bytes)` and `os.stat`, no directory walk, no filesystem path on the page |
| 5.2 | the table, with an `index_state` filter | as specified, defaulting to `all`. The filter needed a query-layer change this document did not anticipate: `resolve_videos` hard-coded `index_state IN ('ready','stale')`, so *no* surface could see a `pending` or `failed` video. That clause is now `CorpusFilter.index_states` with the same pair as its default, and `list-videos` gained the filter plus an `index_state` field (tool-surface.md §4.2, same commit) |
| 5.3 | header, provenance, scene timeline, keyframe strip, transcript and OCR browsers | all six, plus chapters. The strip and the OCR browser share one pager, so the boxes drawn are always for the frames in view |
| 6 | the byte analogues | double caps, `has_more` over totals, three fixed widths through the derived cache, `loading="lazy"` and explicit `width`/`height`, never base64 |
| 6.6 | XSS posture identical to demo-site.md §6.2 | Jinja2 autoescape, no `| safe` in any template (asserted), no HTML sink in the module (asserted, same list as `app.js`), `safeUrl()` on every URL reaching an `href`/`src` |
| 9 | new env vars in `deploy/.env.example` | `VIDTHEQUE_DASHBOARD`, `VIDTHEQUE_RATE_DASHBOARD_PER_MIN`. The other two are phase 3's and are not shipped yet |
| 10.2 | Jinja2 with autoescape, as a deliberate dependency commit | its own commit, `mcp/pyproject.toml` + `uv.lock` only |

**Deferred out of phase 1, deliberately:** the jobs view (phase 2, and it is
what `not_before` is waiting for), everything in §5.5 and every write route
(phase 3), `VIDTHEQUE_DASHBOARD_TRUSTED_CIDRS` and
`VIDTHEQUE_DASHBOARD_SESSION_TTL_S` (phase 3, when there is a write side and a
login to mint), search and ask on this surface (phase 5), and
`video_stages.worker_version` (§10.5 — a schema change, Tom's, and no read
depends on it yet).

**Phase 2 — the jobs view. ✅ SHIPPED 2026-08-09.** Read-only. `not_before` as a
countdown, `attempts`, `degraded_items`, the `job_events` tail, 2 s polling.
Closes the blind spot the overnight batch found. Still no writes. Also in this
phase: dashboard HTML switches its frame `src`es to **relative** `/frames/…`
paths so pages survive SSH tunnels, reverse proxies and port maps unconfigured
(found 2026-08-09: a preview on a non-default port rendered every thumbnail
against a dead `PUBLIC_URL`). Absolute URLs remain the MCP contract — agents
need self-contained authenticated URLs — so the split lives in the `thumb_url`
helper, not in the signer. And `/dashboard/` (trailing slash) redirects to
`/dashboard` instead of 404ing.

What landed, against what this document specified:

| §  | promised | shipped |
|---|---|---|
| 4.4 | `not_before` surfaced — "the single highest-value line" | `_JOB_SQL` selects `not_before` **and** `defer_s`, the remainder computed in SQL on the clock the column was written against. Rendered as a countdown that the poller resets and the page ticks down between polls. **Only on a `queued` job**: `not_before` left on a `running` row is a stamp the last deferral dropped, and a countdown against it would invent a wait that is not happening |
| 4.4 | `attempts` / `max_attempts` | shipped — and `job_items` had to start *selecting* them, which this document did not anticipate. The page states the inference out loud rather than implying it: the counter is one half of "will this come back", the job's countdown is the other, and `retryable` is still not a column |
| 5.4 | per job: state · kind · priority · the four recomputed counts · created / started / finished | as specified, plus **two durations, never one** — `created → finished` is what the job cost the queue and `started → finished` is time on the runner. Both are only honest because `started_at` is now the first claim; the gap between them *is* the deferral |
| 5.4 | per item: state · stage · stage_pct · attempts · error_code · error_message · its `job_events` tail | shipped, except the tail is **per job, not per item** — one `job_event_page(job_id)` read for the page instead of one per row (§6.3). The events carry their `item_id`, so per-item filtering is a parameter that already exists when a page wants it |
| 5.4 | `degraded_items` for every recent `done` job | on the detail page as the full list with stages and reasons; on the *table* as a badge from a new `degraded_counts(job_ids)` — one grouped query for the page, because a badge per row must never become a query per row |
| 5.4 | live progress: polling, 2 s, stopped when nothing is live | `dashboard/static/jobs.js` against `/dashboard/api/jobs[/{job_id}]`. Not an env var — a poll interval that is a deployment knob is one somebody sets to 100 ms. Every changing value is formatted **server-side** and assigned as `textContent`, so the poller carries no formatter of its own; the one exception is the countdown between ticks, which is arithmetic on a number the server sent. New events are appended as elements, never as markup |
| 5.4 | `args_json` never verbatim; no stack traces | shipped. Nothing renders `args_json` at all in this phase: `kind`, `priority` and the item rows say everything the page needed |
| 5.4 | `item_stages` | rendered for **one** item — the running one, or the last to finish, and only if it resolved to a video. Seven stage rows per item is the fan-out §6.3 forbids; every other item's stages are one click away on its own video page. This is also what makes the demo's stated purpose land: the per-stage durations are on the page |
| 2.4 / 10.4 | demo keeps the view, drops source URLs and `error_message`, keeps the clocks | as specified, and asserted **both ways** — the owner's page is the contrast in the same test, so a redaction that quietly grows fails. One field needed a ruling this document did not cover: a `job_events` message is *both* redacted things at once (the runner writes `"retrying in {delay}s after {code}: {message}"`, and a reclaim writes the item's URL) with no structured half to keep. Demo mode keeps the shape of the log — when, how loud, which stage — and none of the prose |
| 5.4 | reads listed | `nonproductive_reasons` was **not** used: the items table already shows every `skipped`/`cancelled` item with its reason, and a second read of the same rows is a second way to say it |
| 6.3 | no page issues a query per row | two reads for the table whatever the row count, six for the detail page whatever the item count, asserted as a *shape* (one row and a hundred cost the same) exactly as phase 1 asserts it |
| 8 | relative frame paths in dashboard HTML | `thumb_url(…, absolute=False)`, used by every dashboard page. `/api/*` on both prefixes and the MCP tools are asserted still absolute, in the same test. Signing is untouched and needed to be: the MAC covers the frame, the width, the quality and the expiry, **never the origin**, so a relative signed URL verifies as an absolute one does — asserted, including a forged one |
| 8 | `/dashboard/` redirects | 308, query string preserved, and unguarded like the stylesheet because a redirect leaks nothing. It has to be a real route: `Mount("/")` matches everything, so Starlette's own `redirect_slashes` never gets to run |
| 9 | new env vars in `deploy/.env.example` | **none added.** The poll interval is a constant, the clamps are the owner policy phase 1 shipped, and the 2 s tick fits inside `VIDTHEQUE_RATE_DASHBOARD_PER_MIN` at 30 requests a minute per open tab |

**Deferred out of phase 2, deliberately:** per-item event filtering (the read
takes the parameter; no page passes it), `args_json`'s parsed fields, and the
"degraded across all recent jobs" roll-up — the badge is per job, and the
corpus overview already answers the corpus-wide version.

**Phase 3 — the write side. ✅ SHIPPED 2026-08-09.** Session login (password or
bearer → the existing `vidtheque_session` cookie), POST-only + `Origin`
discipline, the index form with server-side batching, re-index and tag actions,
`VIDTHEQUE_DASHBOARD_TRUSTED_CIDRS` and `VIDTHEQUE_DASHBOARD_SESSION_TTL_S`.

What landed, against what this document specified:

| §  | promised | shipped |
|---|---|---|
| 2.3 | in read-only mode the write routes are not registered | as specified, and asserted as **404 rather than 403** on every one of them, including the login page. The predicate is one function, `access.write_side_enabled(auth_mode, readonly)`, resolved once in `app.py` beside every other mode decision |
| 3.2 rule 3 | `none` mode serves the read-only projection and says why | same predicate, so `none` mode registers no write routes *and no login page* — **this document said "refused"; the implementation says "absent"**. A sign-in that grants nothing is the same probe magnet as a button that 403s, and the argument §2.3 makes for one makes it for the other. `require_write()` still refuses in `none` mode and is still tested: the registration decision is made far from the handler, and a handler that trusted it would be one refactor away from being wrong. The *why* lives in the rail foot, which DESIGN.md already gives the job of carrying what a deployment is allowed to do |
| 3.2 rule 2 | a login form that takes the credential once and mints the existing cookie | `GET|POST /dashboard/login`. Same cookie name, same `login_sessions` row, same flags read from the same place as `auth/login.py`. **One thing this document did not anticipate:** `token` mode built no `AuthStore` at all, so the cookie it promised could not have been minted or read there. `build_auth` now creates one in `token` mode as well — no new table, no new hashing, one more file open at boot. The secret is `VIDTHEQUE_PASSWORD` when set and, in `token` mode, `VIDTHEQUE_TOKEN` as well, so a token-only deployment still has a browser path in; both comparisons always run, because a short-circuit would leak by timing which secret exists |
| 3.2 | `VIDTHEQUE_DASHBOARD_TRUSTED_CIDRS`, socket peer only | as specified, empty by default, parsed with `ipaddress` and **never** `VIDTHEQUE_TRUSTED_IP_HEADER` — asserted with a request that forges both `CF-Connecting-IP` and `X-Forwarded-For` from outside the network and is refused. An unparseable entry is dropped with a warning rather than failing the boot: this is an authorization *widening*, so a typo must not fail open, and a silent drop is a write side the operator thinks is reachable and is not |
| 3.3 | no state-changing GET | as specified. Every write is POST, and a GET to one is a 404 (`Mount("/")` is a full match, so the router never reaches the POST-only route's 405) |
| 3.3 | Origin on every write; **JSON-only bodies** | **Diverged, deliberately.** The write side is HTML forms, not JSON — §10.2 resolved this surface as server-rendered documents, and phase 1 already shipped a timeline that navigates with JavaScript off. So "no cross-site form POST can reach a handler in the first place" stops being true on its own, and the Origin rule carries the weight instead: it is now **asymmetric**. A `session` credential is ambient, so it needs *positive* same-origin evidence and a request with neither `Sec-Fetch-Site` nor `Origin` is refused; a bearer, or a trusted peer, is not ambient, so absent headers still pass and `curl -H 'Authorization: Bearer …'` needs no ceremony. That plus `SameSite=Lax` is the whole CSRF posture — **no token, and none is planned**: a per-form token needs server-side state or a signed cookie pair, and it would guard a surface that already refuses every request a cross-site page can generate |
| 5.5 | the index form: URL(s), `expand`, `max_items`, `tags`, `channels`, `priority`, `force_reindex`, all clamped by the tool | as specified. `channels` is three checkboxes that submit the tool's own word `all` when they are all ticked, rather than a three-item CSV meaning the same thing. `force_reindex` is its own `<fieldset>` — it is not a channel, and a checkbox under the wrong legend is a wrong answer to a screen reader as well as to a reader |
| 5.5 | refuses honestly when `db.writes_allowed` is false | the controls render `disabled` with the vector-state reason above them, and a submission that gets through anyway comes back as the tool's `E_FEATURE_DISABLED`, verbatim |
| 5.5 / 10.7 | the form splits a paste into jobs of ten server-side | as specified, with **two bounds this document did not name**: the paste is capped at 200 URLs (`max_items`' own ceiling, so the form and the tool refuse at the same number), and the batch size is `min(10, max_items)` — `index_video` truncates its URL list to `max_items`, so a batch bigger than that would have dropped the tail silently. The split is printed on the receipt: a split the operator cannot see is a job count they cannot explain |
| 5.5 | POST → job id → redirect to the job | one job and nothing to explain redirects (303) to `/dashboard/jobs/{job_id}`. Anything else — a split, an already-indexed video, a refusal — renders a receipt above the form, because that outcome is worth reading and a reload must not re-submit it |
| 5.2 / 2.4 | row actions: re-index and tag, present in private, absent in demo | re-index is a one-click POST on both the table and the detail page; tagging is a form on the detail page, reached from the table by a link, because two text fields do not fit a 34px row and the namespace rules want room beside them. Both call the same `index_video` / `tag_video` the tools call, and both render the tool's refusal verbatim with a link back to the video |
| 5.2 | **no delete button** | none, and asserted: no `/delete` string on any write page, and the path 404s. `jobs.kind='delete'` is still schema with no pipeline behind it |
| 2.5.4 | one declared list of write routes | `WRITE_ROUTES`, now five paths, asserted **equal** to the set of registered non-GET routes — so a sixth that forgets to declare itself fails the suite rather than shipping unguarded |
| 9 | `VIDTHEQUE_DASHBOARD_TRUSTED_CIDRS`, `VIDTHEQUE_DASHBOARD_SESSION_TTL_S` in `deploy/.env.example` | both, in the same commit, and the TTL closes the gap §7 logged: `login_session_ttl_s` was hard-coded at `config.py:92` while every other tunable had a reader. One cookie, one table, one lifetime — the OAuth consent screen's session honours the same number rather than a second one nobody would think to set |
| — | *(not in this document)* | **A dedicated `dashboard_login` rate bucket, 10/min per IP, fixed.** The loose `dashboard` bucket is written for a human clicking beside a polling tab; charging a password form against it leaves 120 guesses a minute on a box reachable through a tunnel. It is a constant for the same reason the poll interval is one |
| — | *(not in this document)* | **`POST /dashboard/logout`**, which deletes the `login_sessions` row rather than only clearing the cookie — a cleared cookie leaves a live row anything holding a copy could replay |
| DESIGN.md | forms as full members of the system | four new `components:` entries (textarea, checkbox row, field help, row action) added to `DESIGN.md` in the same commit as the CSS, per its amendment rule. No new colour, no off-ladder size, no off-grid spacing. The paste box is set in the machine face — the Two-Channel Rule applied where it pays most, since JetBrains Mono was chosen for drawing `0`/`O` and `1`/`l`/`I` apart and a pasted column of ids is exactly that string |

**Deferred out of phase 3, deliberately:** `delete_video` (the pipeline job
first — §5.2, and phase 5 owns it), service-layer batching that bypasses the
tool for 100+-item runs (§10.7's roadmap half; the form's 200-URL cap is where
that becomes the answer), a CSRF token (see the §3.3 row — the posture is
deliberate, not pending), and per-video job history on the table.

**Phase 4 — the demo becomes the subset. ✅ SHIPPED 2026-08-09.**
`VIDTHEQUE_PUBLIC_READONLY=1` serves the welcome page plus the read-only
projection; the demo page keeps its search and ask and gains a link into the
browsable corpus. This is the phase where `public/` stops being a separate
frontend and becomes a policy on the dashboard's. It also closes the two
read-side gaps the second opinion assigned here — the queue on the first
screen, and the date filters §5.2 listed and phase 1 did not wire.

What landed, against what this document specified:

| §  | promised | shipped |
|---|---|---|
| 2.4 | the demo serves the read-only projection of every read page | as specified, and it needed **no new gate**: the flag already unregistered the write side (phase 3) and already redacted the jobs view (phase 2), so this phase is the composition plus the overview's own redaction. The composed behaviour is now asserted as one test rather than inferred from three |
| 2.4 | "no settings, no paths" on the overview | **four fields, named** — see the amendment under that table. The row was one word short of a specification and `docs/deploy-public.md` §1.1 found the difference on the live page. The split is a template conditional on the mode the view already resolves, per §2.4's "not a second implementation": one `_redacted(request)`, the same one phase 2 wrote for the jobs view, promoted out of the jobs section and given the whole surface's docstring |
| 2.4 | the drift banner is private | **halved rather than hidden.** The *reason* is a dimension/model mismatch written for an operator; the *effect* — search is answering from full-text — changes what a visitor should believe about every result on the next page. A demo that quietly reported health it did not have would be the redaction lying, which is a worse failure than the leak it was fixing |
| 2.4 | the welcome page gains one link | `Browse the corpus →` in the masthead, one link and not a nav, **hidden until `/api/meta` says the route group is registered**. `VIDTHEQUE_DASHBOARD=0` and the edge rule in `deploy/cloudflared.example.yml` both make `/dashboard` a 404, and a masthead invitation to a 404 is the same mistake as a button that 403s (§2.3). `/api/meta` gains `browse` (demo-site.md §2.3, same commit) |
| — | *(not in this document)* | **The rail links back.** In read-only mode the rail gains one item, `Search the corpus` → `/`. The public site is two pages and a visitor who followed the link in should be able to follow one back out; the condition is exact rather than approximate, because `/` is registered by `public_routes()` under the same flag that makes this the projection |
| 5.1 | active and recently-failed job counts | `jobs_store.job_health(conn, since)` — one grouped statement, four conditional sums, no fan-out on the page that answers in flat aggregates. Rendered as **three** numbers rather than two: `deferred` is the subset of active that is being *held off*, which is the overnight batch's own lesson (§4.4) applied to the first screen. Each figure is a link into the jobs view already filtered to what it counted. `failed_recent` is windowed at 24 h, and the window is a constant the sentence prints, so the number and the words cannot disagree |
| 5.2 | date filters: `published_after/before`, `indexed_after/before` | as specified, as two `<fieldset>` members of the existing GET band. Three things this document did not settle: (a) the values are **resolved server-side to a UTC day** and echoed back canonically, so `?indexed_after=30d` is a working entry point that immediately becomes `2026-07-10` in the picker, in the URL and in the page's own description of itself; (b) `before` resolves to the start of the *next* day, because the clause is `< before` and a range that dropped everything published on its own end date reads as a bug; (c) an absurd year is **clamped** to now + 365 d and the clamped value is printed — a filter the server changed and did not show is the silent narrowing CLAUDE.md forbids |
| 5.2 | clamped server-side | plus a 32-character cap on the raw value before the parser sees it, and a value that will not parse is the tool's own `E_BAD_TIME_FORMAT`, rendered with the rest of the band still populated. `timeparse` refuses rather than ignoring, and the page keeps that: a silently dropped filter is a table reporting the wrong result set with total confidence |
| 6.8 | rate limited in every mode | asserted for the *composed* mode: with the public buckets installed beside it, `/dashboard/*` is still charged to `dashboard`, and the projection's frames still go through the byte-capped derived cache at the same three fixed widths |
| DESIGN.md | new primitives declared in the same commit | three `components:` entries (`field-range`, `field-range-input`, `field-range-separator`, plus `text-tone`), a phase-4 section, and an amendment to the rail-foot paragraph. No new colour, no off-ladder size, no off-grid spacing |

**Found in phase 4 and deliberately not fixed in it:** `/dashboard/api/*` is
registered with `OWNER_CLAMPS` in **every** mode (phase 1, §2.5.1), and in the
intended public combination there is no credential in front of it — so an
anonymous visitor gets the owner's bounds on the JSON facade, including
`max_text_chars=0`, which is the full-transcript hatch demo-site.md §2 reserves
for an owner's agent. It is not a *page* the §2.4 table covers and it is not a
leak of the operator's box; it is the "how much of the corpus is public"
question, which `docs/deploy-public.md` §1 names as a policy call for Tom and
the audit rather than one an agent makes. Written up there with the three
answers and the one-line version of each; if the answer is "clamp it", the
honest fix keys off the *credential* rather than the flag, which is a design
change and belongs in phase 5.

**Resolved 2026-08-09, as the honest version.** The answer was "clamp it", and
the credential-keyed fix landed ahead of the rest of phase 5 because it is
ship-blocking for the public launch. §2.4 carries the matrix, the CIDR ruling
and the reason the *pages* were left alone; `docs/deploy-public.md`'s audit item
carries the verification. The one-line mode-keyed version was **not** taken, for
the reason that section already gave: it would clamp the owner of a read-only
deployment that has a credential configured, which is the deployment Tom runs.

**Deferred out of phase 4, deliberately:** a `robots.txt` and the `noindex` the
dashboard already sends are not the same decision, and whether the public
projection *wants* to be crawlable is a content question for the audit
(`docs/deploy-public.md` §1) rather than a code one; an `order` for the jobs
view; and per-channel coverage counts on the overview, which would be the
fifth flat aggregate on a page that already answers the question with links.

**Phase 5 — search and ask move in.** The dashboard grows the search box and the
ask pane, sharing `public/api.py`'s handlers; the welcome page becomes purely an
entry point. `delete_video` — the pipeline job, then the button — belongs here or
later.

**Shipped early out of phase 5: credential-keyed clamps (2026-08-09).** Not the
phase's main body — it is the phase-4 escalation above, pulled forward because
the public launch cannot ship without it. What landed:

| §  | promised | shipped |
|---|---|---|
| 2.4 | the clamp policy keys off the credential, not the flag or the prefix | `public/api.py:policy_for`, one predicate, both prefixes, every mode. `api_routes()` **lost its `policy` parameter** — a bound chosen where a route is registered is a bound that cannot see who called |
| 2.4 | trusted CIDRs are authenticated-equivalent | as specified, and asserted in `none` mode, where it is the only credential there is. Socket peer only; the forged-header client is outside, asserted with `CF-Connecting-IP` and `X-Forwarded-For` both set to an address inside the network |
| — | *(not in this document)* | **`credential()` moved to `auth/credential.py`** and is re-exported from `dashboard/access.py`. Two route groups now need the same answer, and a route group is the wrong owner of it. `is_owner()` is the new half, and the whole of it is the distinction `"open"` forces: `AUTH=none` means *no check*, never *the owner* |
| — | *(not in this document)* | **Two phase-1 assertions were rewritten**, exactly as `docs/deploy-public.md` predicted they would have to be. `test_one_set_of_handlers_serves_both_prefixes` used the two prefixes' *clamps* as its proof they shared handlers — which is what the bug looked like from inside the suite, since both of those requests are anonymous. It now proves it on the payload shape |
| — | *(not in this document)* | The matrix is asserted as a matrix: {anonymous, session, bearer, trusted peer} × {`none`, `token`} × {readonly on, off}, plus the hatch specifically (`max_text_chars=0` → 400 for anonymous on both prefixes, `0` for a bearer on both), plus a no-regression test for the demo's own numbers |

**Deliberately not in it:** the *pages* (§2.4's last paragraph — the hatch
reaches no page, and the rest is rows-per-page on a listing the demo publishes
in full), and no new environment variable, because the lever this needed
already exists and is already documented.

3 → 4 → 5 is ordered; 1 and 2 are independent of everything else.

---

## 9. New environment variables

CLAUDE.md: an env var without an entry in `deploy/.env.example` is a bug. These
must land there in the commit that implements them.

| var | default | what it does |
|---|---|---|
| `VIDTHEQUE_DASHBOARD` | `1` | Route group on/off. Default-on is safe **because** of §3.2 rule 3: in `AUTH=none` the write side is never registered. |
| `VIDTHEQUE_DASHBOARD_TRUSTED_CIDRS` | *(empty)* | Peer networks allowed to write without a credential. **Socket address only, never `VIDTHEQUE_TRUSTED_IP_HEADER`.** Empty = off. |
| `VIDTHEQUE_DASHBOARD_SESSION_TTL_S` | `43200` | Browser session lifetime. Also closes the existing gap: `login_session_ttl_s` is hard-coded at `config.py:92`. |
| `VIDTHEQUE_RATE_DASHBOARD_PER_MIN` | `120` | The new bucket. Loose — a human clicking plus one polling tab. |

No new var for the URL prefix (§2.6), and none for the pipeline settings the
overview displays: they are read from where they already live.

---

## 10. Open questions for Tom

Real forks. Everything above is a recommendation I will defend.

1. **Resolved (Tom, 2026-08-09): token-or-session as specified in §3.** The
   existing owner login sets the existing session cookie; bearer
   `VIDTHEQUE_TOKEN` for scripts; `none` mode serves read-only only;
   trusted CIDRs stay empty by default.

2. **Resolved (Tom, 2026-08-09): server-rendered HTML per page, plain ES
   modules for the interactive bits.** The dashboard is mostly documents; real
   `<a>` links, a page per route, ~100 lines of JS for polling and the
   lightbox. This phase introduces the repo's HTML templating story (today the
   only server-rendered HTML is the f-string OAuth login page,
   `auth/login.py:29-53`) — Jinja2, with autoescape on, as a deliberate
   dependency commit. Kept in reserve, in order, if a page outgrows this:
   vendored Preact+htm islands (no build step, committed to the tree), and only
   then a real build toolchain — each adopted per-page when a concrete page
   demands it, not by default.

3. **Resolved (Tom, 2026-08-09): `/dashboard`.** The welcome page stays at `/`
   and every already-shared link keeps working.

4. **Resolved (Tom, 2026-08-09): the demo keeps the jobs view.** Its stated
   purpose is to show a visitor what indexing a video actually costs in time, so
   per-item and per-stage durations are part of the redacted projection, not a
   private extra — the redaction drops source URLs and `error_message` (codes
   and counts only, §2.4), never the clocks. Submitting to the index stays
   absent from the demo in every form.

5. **Half-resolved (Tom, 2026-08-09): add `video_stages.worker_version`** — one
   nullable column, one write (§4.1 caveat 4). Still open: `stage_version` is
   declared and never incremented — wire it, or drop it from the schema and the
   panel.

6. **The four `data_status` vocabularies (§4.5).** A fifth consumer is the moment
   to decide whether one enum was ever wanted. Unifying touches four call sites
   and the tool payloads that models already read; leaving it means the dashboard
   renders four vocabularies verbatim and never gets to colour them consistently.

7. **Resolved (Tom, 2026-08-09): the cap stays; batching bypasses the MCP tool
   — roadmap.** `index-video`'s ten-URL cap protects the model surface and does
   not move. Real batch indexing (playlist, channel, straggler retries) becomes
   a dashboard capability that calls the job store through the service layer
   directly — no MCP tool in the path, so no cap and no payload rendered for a
   model that isn't there. Until that ships, the index form splits submissions
   into jobs of ten server-side (the 2026-08-09 straggler run did this split by
   hand: 64 videos → 7 jobs).

8. **Resolved (Tom, 2026-08-09): the owner bypasses `ask_global`.** The daily
   guard exists to stop anonymous visitors burning the OpenRouter budget; the
   owner can already spend their own key by other means, so metering them there
   protects nothing. Owner asks are still written to the same spend ledger —
   the corpus-stats page reports total spend honestly, it just never *refuses*
   the owner. The per-IP limiter keeps not applying to authenticated owner
   requests for the same reason.

9. **Resolved (Tom, 2026-08-09): yes, eventually — via §10.7's service-layer
   batching, and not as a phase-1/phase-3 requirement.** The phase-3 index form
   stays simple (URL/playlist/channel, split into jobs of ten); replacing
   `scripts/mcp_call.py` for 100+-item batch work is the roadmap item above.

---

## 11. The copy cull (Tom, 2026-08-10)

**The order.** The dashboard "shouldn't try to sell you anything, or have
leftover comment-like content". An earlier external review proposed this and was
told it needed Tom's call; this is the call. Every template was swept for prose
where the page narrates or justifies itself — vocabulary lessons, hedges,
editorial asides, and paragraphs arguing for a decision the reader did not ask
about. What stays: labels, values, states, and the empty-state contract
(DESIGN.md, **The empty state** — name the absence, name the cause, name the
move, one line each).

**Nothing was destroyed.** Every removed sentence carried either no fact at all
or a fact this document already records. The table is the audit; the right-hand
column is where the fact lives now.

| page | removed | the fact, and where it lives |
|---|---|---|
| every page (footer) | "This is the management surface. The corpus itself is served at `/mcp`." and the `· a self-hosted video-corpus MCP server` tagline | §2.6 has the URL structure. The tagline goes on the same argument that took the rubric out of the rail (DESIGN.md, **The brand lockup**): a tagline is a persuasion move |
| overview | ledger note "the unit a search hit points at" | §5.1. Replaced by a value — the video count the cues are spread across |
| video detail | ledger note "the embedding unit" | §5.3, and §4.3's chunk definition. Replaced by a value: how many cues the chunks were built from |
| overview | storage notes "summed from the column, not walked on disk" / "one SQLite file, one writer" | §5.1 already specifies the byte total as a column read, not a directory walk |
| overview | declared-models note, four sentences on `config` vs. the worker | §4.1 caveat 2. The panel is already a diff: declared on the left, the live pills on the right |
| overview | drift banner's "Meaning is ranked by words here, not by embeddings." | restates the sentence before it |
| videos | header "Every row is one video and what the pipeline made of it. Counts live on the detail page…" | §4.2 and §5.2: coverage booleans instead of per-row counts |
| videos | the empty notice's advice paragraphs | §5.2. The notice now names the filter that narrowed it, in one line, and links out |
| video detail | "There is no scenes table: a shot is a group of keyframes sharing a `shot_id`…" | §4.3, in full, with the schema citation |
| video detail | the Provenance note — a dash means "not recorded", the names are declared and not a worker report, only `fetch` records a build version, `stage_version` is never incremented | §4.1, all four, as caveats 1–4. The dash in the `model_key` cell is now the whole statement, and `test_the_detail_page_is_honest_about_a_failed_stage` asserts the cell rather than the gloss |
| video detail | the OCR note — boxes normalised 0–1 at write time, hover lights the pair, the searchable unit is the whole frame | §5.3, both facts, with citations. A hover that works needs no instructions |
| video detail | the transcript note — what a chunk is, why `words_json` is not printed | §5.3 and its "Does not show" list. The `.chunkmark` row labels the chunk; "What was stored" already counts the cues with word timings |
| video detail | re-index advice ("better served by submitting it on the index form without forcing…") and the tag note's "the refusals are `tag-video`'s own" | §5.5. The control says what it does; it does not coach |
| jobs | header "What the pipeline is doing, what it is waiting on…" and the wall-clock paragraph | §5.4, and the job page's own figure-notes say `created → finished` / `first claim → finished` |
| job detail | the "What it cost" paragraph on `started_at` being the first claim | §5.4 and §8's phase-2 row. The item error tally survives as data, under a label |
| job detail | "Whether a failure will retry is not a column…" | §4.4: `ItemFailed.retryable` is not persisted |
| job detail | the backoff note ("the source's own `retry_after`, otherwise `VIDTHEQUE_RATE_LIMIT_BACKOFF_S`, otherwise 5 s") | §4.4, with the defaults |
| job detail | "a deferral that is not `E_RATE_LIMIT` is recorded here and nowhere else" | §4.4. The log is printed; printing it and describing it are not both needed |
| job detail | the degraded panel's four sentences | trimmed to one: only `fetch` and `stt` are essential, re-index restores the rest (§4.4) |
| index form | "This runs the same `index-video` the MCP surface runs, with the same clamps…", the split justification, and the closing paragraph on why there is no delete | §2.2 (one service layer), §10.7 (the split), §5.2 (`jobs.kind='delete'` has no pipeline) |
| login | the header paragraph on cookies vs. headers, and "A script does not need this page at all" | §3.1–§3.3. The page names the env var that holds the secret and stops |
| error | "That code is the same string the MCP surface answers with…" | §2.2 |

**The fence.** Two tests replace the one that pinned "There is no scenes table"
(`test_the_scene_timeline_is_positions_not_a_query_per_shot`, which now asserts
the band's caption is a count):

- `test_no_dashboard_page_narrates_itself` renders every template — anonymous,
  owner and demo — and asserts each removed phrase is absent. A regression pin.
- `test_every_prose_slot_stays_inside_its_ceiling` bounds each prose slot by
  sentences and characters. The failure mode is not any one sentence; it is a
  paragraph growing back one clause at a time, and only a measure catches that.
  Page copy gets two sentences and 170 characters; `field-help` and
  `check-note`, which DESIGN.md sanctions as control documentation, get 240; a
  ledger `figure-note` gets one sentence and 56 characters.

---

## 12. The identity rebuild (2026-08-10)

**The order (Tom).** "Let the dashboard be a true minimalist and informative
control surface. Keep the global same aesthetic, the logo." `DESIGN.md` was
rewritten the same night — the warm-paper system is gone and the projection
room is the contract — and the per-surface section there makes this surface the
**minimalist end** of that system: density, restraint, information first, the
state tones doing the talking, no spectacle. `positioning.md` says the same
thing shorter: the dashboard is the instrument, and its charisma is receipts
rendered perfectly.

**Nothing about function moved.** Five routes, the same reads, the same clamps,
the same `has_more`, the same redactions, the same write side. Every class name
in the templates survived except the three the lockup retired (below). What
changed is the skin and the discipline: fewer rules, fewer boxes, hairline
seams, mono for everything the machine said and sans for what a human wrote.

### 12.1 What was stripped, and what was sharpened

| | |
|---|---|
| **stripped** | the pulsing `.livedot` (the Motion Law bans a breathing dot outright — the word `live` in the work tone was always the thing carrying it, and the square beside it now just sits there); the `.skip` link's slide-in; every border radius (`* { border-radius: 0 }` is the reset); the drawn `.brandmark` in the rail; the third font face; the light scheme and the whole vendored Radix light/dark pair; the `--r-sm/-md/-lg` scale; two of the four breakpoints; the row-hover fill's second job (a plate step now, not a lighter paper) |
| **sharpened** | the ledger became a real plate with the system's one corner tick, and it is the only ornament on the surface; the shot bars stopped being accent-coloured and are neutral until you point at one; the OCR overlay moved from gold to `--seen`, which is the Lime Rule's whole point and the one place lime appears; the state pills went to the five dark tones with grounds mixed from the ink; every control in the filter bar is one 34px `button-ghost`/`input-text` family; frames sit on `--black` behind a 1px inset hairline instead of a grey plate |

### 12.2 Six judgement calls, so nobody re-derives them

1. **The page title is `headline` at its floor (1.9rem), not its clamp.**
   DESIGN.md's per-surface note says the dashboard's ladder "starts at
   `headline`"; the rung's clamp reaches 50px at 1440, and the video detail
   page's `<h1>` is a corpus string — a 78-character conference title at 50px
   is four lines before the operator has read anything. 1.9rem is the rung's own
   floor, so it is on the ladder, and it is one step above everything under it,
   which is what a page top is for. Below `--bp-hand` it drops to `question`'s
   ceiling (1.5rem), the same step the old surface took.
2. **The footer signature stays at rail scale**, not the system's
   `clamp(2.2rem, 4.4vw, 4rem)`. That size is described in DESIGN.md as "a
   signature, at the end, after the argument" — a landing-page gesture. This
   surface makes no argument, so the word appears twice at the same 16.5px and
   the brand is a whisper on both ends.
3. **No gold fill on any control.** DESIGN.md lists what gold means here and it
   is five things: the active nav item, the focus ring, the sorted column's
   underline, a timecode worth clicking, and the hovered row's inset edge. A
   gold *Apply* button would quietly add "a button" to that list, so the whole
   button family is `button-ghost` — 34px, `label` typography, `--plate3` or
   transparent, gold only on hover. The one exception is `accent-color` on a
   native checkbox, where the alternative is the browser's blue, which would be
   a second accent for real.
4. **Every control in the filter bar is mono.** Its values are the machine's own
   vocabulary — `ready`, `E_RATE_LIMIT`, `2026-08-09`, `kCc8FmEb1nY`, a row
   count — and the Two-Channel Rule does not stop at the edge of an `<input>`.
   They sit on `--console` inside a `--plate` band: pitch → plate → console is
   the Plate Rule, and a field is a surface the machine writes into.
5. **The corner tick is used once per page and only on the ledger.** Panels on
   this surface are a label and a hairline, not plates, and a tick belongs to a
   plate. The overview's ledger is the only real box on the surface, so it is
   the only thing that gets one.
6. **Four breakpoints became three**, which is the allowance DESIGN.md gives:
   `--bp-stack` (70rem — the rail becomes a strip, the zones stack, the ledger
   goes to three columns), the dashboard's own **52rem** (a table stops being a
   table — unchanged, and still the one structural break this surface is allowed
   to invent), and `--bp-hand` (48.75rem — chrome tightens, the ledger goes to
   two columns). The old 76rem and 60rem were taste breaks for the same two
   changes and are folded into the system's own.

### 12.3 The lockup, the favicon and the fonts

The rail and footer marks are the **word and nothing else** — `vidtheque` with
the full stop in gold, one `<b>` with an `<i>` around the period (DESIGN.md,
The Font-Logo Rule). `.brandmark` / `.wordmark` / `.footmark` are retired and
replaced by `.mark` and `.fmark`, the two selectors the contract names. The
drawn film frame stays as the **favicon only**, redrawn gold on pitch with
square corners; it never appears inside a page.

`dashboard/static/fonts/` lost the two faces it no longer serves — Inter and
Instrument Serif, with their licence texts; `PROVENANCE.md` records what went
and why. Amended 2026-08-12: the byte-identical copy of the two remaining
faces is gone too. The dashboard's asset route aliases its `fonts/` prefix
onto `public/static/fonts/`, the document of record (DESIGN.md, Rules for the
builders #2, `dashboard/__init__.py` `_FONTS_DIR`), so the dashboard's font
URLs still serve in a private deployment and there is no second copy to
drift. The `src` stays relative for the SSH-tunnel reason §8 already gives.

### 12.4 The two pinned tests

DESIGN.md's migration notes named them, and this rebuild is where they moved:

- `test_the_dashboard_palette_matches_the_demos` — **six** role properties, not
  twelve, because there is one scheme. The two files must still agree, which is
  what stops the surfaces becoming two visual worlds; what changed is that each
  role is now resolved through its `var(--…)` alias chain and the two are
  compared as **mappings**. The six are aliases (`--bg: var(--pitch)`), and two
  stylesheets written independently group and order their tokens differently
  without either being wrong. The colour is the contract; the spelling is not.
- `test_both_schemes_and_a_mobile_viewport_are_declared` — one `theme-color`
  (`#040405`), `content="dark"`, `color-scheme: dark` in the stylesheet, and an
  assertion that **no** `@media (prefers-color-scheme` block exists. The name is
  left as DESIGN.md's migration note cites it; the docstring carries the truth.

### 12.5 Tom's review of the rebuild — the second pass (2026-08-10)

Fifteen items, reviewed on the built pages. §12.1–§12.4 above stay as the
record of the first pass; where this pass overturned something there, it says
so. The visuals and this section landed in one commit.

**The three that changed a decision §12 had already written down.**

1. **The state cluster on video detail is a `statepair`, not a pill / label /
   pill.** §12.1 shipped the header states as a bare pill, a floating 10px
   `data_status` and a second bare pill; Tom's verdict was that it "doesn't look
   good", and the diagnosis is that nothing said which label owned which pill.
   The new primitive is one hairline box per fact: the key on `--plate2`, the
   state's own pill flush against it. §4.5 is why both are labelled at all —
   the four `data_status` vocabularies are deliberately not unified, so a bare
   `ready` beside a bare `no_frames` reads as the page contradicting itself.
   Reused on the overview for the one fact left after item 14.

2. **The deferral countdown is the same height as the pills beside it.**
   §12's stylesheet argued the countdown "is a notch taller than the state pill
   beside it, which is correct". It was 26px against 17px, and Tom read the cell
   as two kinds of object rather than as one strip. Overturned: `--chip-h`
   (20px) is now the height of every marker that can share a state cell — pill,
   error code, `statepair` key, countdown. What still makes the countdown
   outrank its neighbours is what should: it is the only dashed border in the
   row, it is the warn tone, and it is the only one set in readable machine text
   rather than in the tracked label idiom.

3. **The `live` badge is gone entirely.** §12.1 recorded replacing the pulsing
   dot with a static square and the word — the right fix for the Motion Law, and
   still the wrong object. On a dashboard served by the process that also holds
   the job runner, "this page is live" is always true and therefore says
   nothing. Liveness belongs to the jobs, and the rows now report it themselves.

**How the jobs page feels alive without decorative motion** (Tom: "don't add
useless animation but it gotta feel alive"). Three real signals and no fourth,
each one nameable as machine work, which is the Motion Law's own test:

- the meter's width transitions over `--poll-ms`, **the server's own poll
  interval**, written onto the root element by `jobs.js`. A stage that moved
  from 42% to 47% between two measurements is drawn advancing across the gap
  instead of snapping. The duration is a data cadence, not a design token, and
  it is deliberately not in DESIGN.md's `motion:` block for that reason;
- `.is-working` is set **only while `state == running`** — the runner has
  claimed the job and a stage is executing — and it draws a bright cap on the
  fill's leading edge. Static geometry, no animation, and simply absent on a
  queued, deferred, finished or failed job;
- the wall clock ticks up once a second while the job is live. That is the
  Motion Law's own entry, "a counter ticking up: counts are counts", and it is
  the thing a reader actually watches. `data-wall` is written only for a job the
  server called live, so a finished job's clock is a measurement and stays one.

There is no indeterminate stripe, no pulse and no sweep, and a job that is not
running draws a bar that does not move — which is the honest picture of a
machine that is not working. **SSE was considered and not taken**: §5.4 decided
polling for reasons that have not changed (a long-lived connection per tab
against the single-process holder of the only SQLite writer), and none of the
three signals above needs it. Smoothness came from drawing the interval, not
from shortening it.

**The evidence panels.**

4. **Clicking a shot bar selects into evidence; it does not open a modal.**
   The strip and the OCR panel scroll to that moment and the frame is marked in
   both, in gold, because that is exactly "the moment you are pointing at". The
   second click — on the frame the reader can now see — is the one that opens
   it. A modal on the first click would answer a question that has not been
   asked yet. With JavaScript off the bar is still a real link to `#frame-N`,
   still carries its own offset when the frame is on another page of the strip,
   and `:target` still marks what it lands on; the interception only happens
   when the frame is already on the page.

5. **The OCR panel is a scrollbox of every line, and the digest is gone.**
   §12 shipped `OCR_PREVIEW_LINES = 8` and a `+ N more` expander, chosen to
   match the eight `:has()` pairs a stylesheet could enumerate. That number was
   always the tail wagging the dog: it meant a dense slide had twenty-six lines
   that lit nothing, and an opened expander then held lines that pointed at the
   wrong box or at none. Both are replaced by one continuous `<ol>` in a
   `--ocrbox-h` scroller, with the pairing carried by a `data-line` **index** on
   the line and on its box — which holds for every line, however many there are.
   `OCR_PREVIEW_LINES` is deleted; `OCR_LINE_CAP` (the *page's* budget, §5.3's
   outer cap) is untouched and still printed when it binds.

6. **The frames in the OCR panel open in the same overlay as the strip's**, by
   carrying the strip's own `data-*` — one delegated click handler serves both
   panels, because a second opener is a second place for the lightbox contract
   to drift. In the grid the boxes are `pointer-events: none`: at 512px a
   detection box is a few millimetres of screen, and a pointer aimed at one
   would only be stealing the click that opens the frame. **The full-size
   two-way interaction lives in the overlay**, which now lists the frame's lines
   beside it: hover a box, its line lights and scrolls into view; hover a line,
   its box lights.

   One thing this costs, stated plainly: the box↔line highlight used to be pure
   CSS and worked with JavaScript off. Pointing is now an enhancement. What is
   *not* an enhancement is the receipt — every line, its text, its confidence
   and its box are server-rendered and complete either way. Being able to aim at
   one end of it is what needs the script, and "all lines" is not purchasable in
   CSS at any length.

**The transcript.**

7. **A bounded scrollbox that appends, with the position always printed.**
   The "Next 50 cues →" button is no longer the control: a click that reloaded
   the page to move fifty rows threw the strip, the OCR panel and the reader's
   scroll position away with it. Nearing the end of the box fetches the next
   batch from `GET /dashboard/api/videos/{video_id}/cues` and appends it. The
   sticky line above prints `cues 1–150 of 1,203` — and the second number is the
   count `per_video_counts` already read for the "What was stored" band, so no
   page issues a second count query for a position line. **`has_more`, never a
   total, on the endpoint itself**; the same `CUE_PAGE_MAX` and offset ceiling
   as the page; every string formatted server-side, so the script carries no
   clock, no chunk label and no rounding of its own. The pager stays in the
   markup as the no-JavaScript path and as the appender's own fallback when a
   fetch is refused, hidden only once the script has taken over.

8. **The per-cue `origin` badge is gone.** It printed `whisperx` on every one
   of a thousand rows to say what the "What was stored" band already says once,
   per origin, with a count — and what the `stt` row in Provenance says with the
   model that produced it. A label repeated on every row is not a label.

9. **The chunk marker counts words as well as characters.** Characters are what
   the chunker clamps on; words are what a human has an intuition for.
   `chunk_spans` selects `chunks.text` for this and the count is
   `len(text.split())` — the definition — rather than a SQL space-counting
   expression that miscounts every newline in the joined cue text. The text
   itself never reaches the template.

**The rest.**

10. **The overview does not draw "What is missing" when all three counts are
    zero.** Three zeros is not a panel: the block answers "what should I go and
    look at", and when the answer is nothing it was a third of the column saying
    so. Nothing is hidden by it — every figure in it is a link into a filter of
    the videos table. The queue panel deliberately does *not* do this: an empty
    queue is a fact about this second, and a panel that vanished when the batch
    finished would take the operator's place-marker with it.

11. **An untagged corpus says `no tags` and stops.** The `<namespace>:<name>`
    lesson that followed it belongs beside the field that enforces it — where
    the video page's tag form already states it, and where `tag-video`'s own
    refusal states it again.

12. **The vector pill is gone from the models panel.** It said "vector legs on"
    — a green badge for the ordinary case, which is the badge nobody reads —
    beside a table that already names both embedding models and their
    dimensions. The one case that changes what a reader should believe is the
    *off* case, and that has its own banner at the top of the page in words a
    visitor can act on. `indexing allowed` survives as a `statepair`, since one
    fact left over from a removed pair had no business still wearing a row
    label.

13. **The rail's first group loses its heading.** "The index" named three
    routes and none of them needed naming. `Manage` and `This demo` keep theirs:
    those are the two groups whose *absence* is a fact about the deployment.

14. **The version is `0.0.1` everywhere.** `mcp/pyproject.toml` said `0.1.0`
    while the workspace root and the worker both said `0.0.1`, so `/healthz`,
    `vidtheque://context`, `/api/meta` and the dashboard footer all published a
    version this project does not ship. Fixed at the two sources
    (`mcp/pyproject.toml`, `vidtheque_mcp.__version__`), with `uv.lock`
    refreshed in the same commit because it records the member's version, and
    the example payloads in `demo-site.md` §2.3 and `tool-surface.md` corrected
    to match. Asserted against the packaging metadata rather than against a
    literal, so the next bump cannot leave a surface behind.

**Two bugs this review surfaced that were not on the list**, both from the first
pass and both invisible to the suite:

- **`.framebtn` was taking the 34px control height.** The chassis gives every
  `button` that height, a definite height beats an aspect ratio, and every
  keyframe in the strip was a 34px letterbox. The fixture's JPEGs do not decode,
  so the box was empty either way and nothing showed it. `height: auto` on
  `.framebtn`, plus a reset of the control's type so a broken frame's alt text
  is a sentence rather than a tracked label.
- **An `inline-flex` marker ate the spaces around its own clock.** A flex
  container makes an item of every text run inside it and drops the whitespace
  between them, so `held <span>1m 28s</span> more` printed as
  `held1m 28smore`. Every chip-height marker is `inline-block` with a
  `line-height` instead.

Both are pinned by tests, because a geometry bug a fixture can hide is exactly
the kind that comes back.

### 12.6 Tom's review of the rebuild — the third pass (2026-08-10)

Seven items, reviewed on the built pages against the **live corpus** rather
than the fixture, which is where five of the seven came from. §12.1–§12.5 stay
as the record of the earlier passes; where this pass overturned something, it
says so. Item 2 — the merged frames view — is deliberately **not** in this
section: it landed alone, last, in a commit that is only itself, and is written
up in §12.7 so that reverting it takes its own documentation with it.

**1. A shot bar puts its keyframe into evidence, and now off-page too.** §12.5
item 4 wrote down the behaviour and the second pass shipped it; Tom's verdict
on real data was that clicking a bar "visibly does nothing". Two causes, both
real, both invisible to the suite.

The first is a **fixture-vs-corpus difference**. The interception is a click
handler that marks a card already in the document, and `#frame-N` is a
fragment, which never reaches a server. A seeded video has three keyframes and
a page of the strip holds twenty-four, so in the suite every bar's frame was
always on the page and the handler always fired. A real talk has one shot per
keyframe — 164 of them on the video this was reproduced against — so five bars
in six navigated instead, and the page they landed on marked nothing. The bars
carry `select=<ord>` beside the fragment now and the server paints the same
`is-selected` on the card and on its OCR figure, so both paths end in the same
picture, with the script blocked as well as with it running. `select` has no
default, because `0` is a real ordinal and a page that arrives with a keyframe
already marked reports a click nobody made; the interception writes the same
address back with `history.replaceState`, so a reload and a copied link agree
with the navigating case.

The second is that **the mark itself was being clipped**. Every gold mark in
the frames panels was an `outline` with `outline-offset: 2px` on the `<img>`,
and `.framebtn` / `.ocrstage` are `overflow: hidden` with the image filling
them exactly — so the outline was drawn outside the image and therefore inside
the clip. Painted on every selection since the panel was built, and visible on
none. Three marks were affected: the timeline↔strip hover link, `:target`, and
the evidence selection. All three moved onto the box, whose own outline its own
overflow does not clip, and the negative is pinned too: no rule may put an
outline back on an image inside one of those boxes.

**3. The overlay's line list scrolls down, and only when it has to.**
`columns: 2` on a box with a bounded height does not draw two columns and
scroll down — it paginates the overflow **sideways**. Measured on a real slide,
the list was 15,501px wide inside a 1,126px scrollport, so a reader was looking
at one screenful of a horizontal filmstrip. On top of that, `scrollIntoView({
block: "nearest" })` resolves `inline` to `"nearest"` as well, so in that box
every reveal was a horizontal jump — including for a line already fully
visible, which is the symptom Tom reported. The list is a grid now, which
overflows in the direction the box always claimed, with `overflow: hidden auto`
so it can never go sideways again; and `revealLine()` measures the line against
the scrollport and moves `scrollTop` by the smallest amount that puts it
inside, or does nothing. The cost, stated: the lines read left-to-right and
then down rather than down one column and then the next.

**4. The transcript header is totals, not position.** §12.5 item 7 printed
`cues 1–150 of 1,203` and argued the position had to be visible. It answered
what the scrollbar already answered, moved under the reader every time the box
appended a batch, and was not a question a reader of a transcript has.
Overturned: the line is `307 cues · 12,480 words · 68,912 chars`, all three
server-computed over the whole video. Words and characters come from
`queries.cue_text_totals`, which uses **the chunk marker's own definition** —
`len(text.split())` (§12.5 item 9) — so a chunk's `n words` and the video's
`words` are the same kind of number, and a SQL space count cannot miscount a
newline inside a cue. It reads one column of one video's cues, on the one page
in this surface that is allowed a per-video read at all, and the text never
reaches the template. The lazy append is untouched; what it lost is the moving
number it maintained.

**5. The overview masthead keeps the state and the clock, and nothing else.**
Two facts re-homed for two different reasons. The state word wore a bare pill
on the title's baseline, at a different weight from the machine strings beside
it, reading as a caption that had drifted — so it takes the `statepair` §12.5
item 1 settled on, which this page already used once for `indexing allowed`.
One primitive, worn one way. The published span was never a masthead fact: it
says what is *in* the corpus rather than what state the corpus is in or when
this box last worked, so it is a second figure-note under `videos` in the
ledger, beside the count it is the range of.

*Checked across the whole vocabulary, not the one word that was on screen.*
`data_status` can print `empty | indexing | degraded | deferred | partial | ok`
(`tools/corpus_state.status_word`), and Tom hit the misalignment twice, once
with `degraded` and once with `indexing`. The `statepair` is a fixed-geometry
object — only the pill's *width* varies with the word — so all six were
measured in a browser at 1440: identical box top (38px), identical height
(20px), identical offset between the box's centre and the neighbouring text's
(1px). The alignment is word-independent by construction, which is what makes
this a fix rather than a re-tune.

**6. The jobs view says what a job holds, and explains its percentage.**

- **The state filter is this system's own control.** This overturns §12.2's
  third judgement call, which kept the platform's disclosure arrow rather than
  start an icon language. What that bought on the operator's machine was a
  rounded, shaded, OS-accented macOS control in a band of square 34px hairline
  boxes — the one object on the page that was not this system. `.pick` is
  `appearance: none` plus the band's own chrome, and the mark is a 6px square
  with two of its four hairlines in `--fg2`, turned 45°: the same 1px rule
  every box in the band is edged with, not a glyph. The *list* the control
  opens stays the platform's, which is the part a surface with no JavaScript
  cannot draw and should not try to. The sorted column's head keeps its
  caret-free underline — that argument was about a sort arrow and still holds.
- **A row says what the job contains.** The first item's video title, `+N more`
  for the rest, and the channel when every resolved item came from one; the id
  drops to the meta line beside the kind and the priority, where the other
  machine strings already were. `queries.job_contents` is one grouped query for
  the whole page (§6.3) and is deliberately off the poll target, because what a
  job holds does not change between two ticks. **The submitted URL is not on
  the row in either mode** — §2.4 redacts it, and the video it resolved to is
  corpus, published by id and title on two other pages. A job whose items have
  not been fetched says so with the count it has rather than wearing its own id
  as a name.
- **The percentage carries its own breakdown.** Pointing at the figure, or
  tabbing to it, prints the five item states it is made of — all five, zeroes
  included, because the point is that they add up to `n_items` — and the rule
  that turns them into one number: an item still in the pipeline counts the
  stages it has finished, out of `len(jobs.store.STAGES)`. Both strings are
  formatted server-side and both are patched by the tick, so a hint held open
  while a job advances stays true. The `hint` plate carries no shadow: this
  surface has none, and a plate with a hairline is already one step off the
  page.

**7. The videos band is the search.** Four parts. The three pickers take the
control family built for the jobs band. Every field takes its width from the
band rather than from the platform — a text input sized by `size` and a select
sized by its own longest option both change width when their contents change,
and one control resizing re-flows its whole row, which is the shift Tom saw;
one flexible field remains, so the geometry is a function of the viewport and
of nothing else (measured at four filter states: zero fields move, the band's
own top included). Apply stops being the thing you click: a picker submits on
the spot, a text field 450 ms after the typing stops, and the button stays in
the markup as the no-JavaScript path exactly as the transcript's pager does,
hidden only once the script has taken over — with the caret handed back after
the reload through `sessionStorage`, because which control had focus is not a
fact about the result set and does not belong in a link. And `ordered by
<order>` leaves the masthead: everything else in that strip is a *narrowing*,
an order takes no rows out, and the sort is already stated twice — by the
picker and by the sorted column's own gold underline. With it gone the strip
can be empty, so the paragraph is conditional rather than a blank line under
the title.

**The favicon, in the same pass.** Tom picked "the v." — the wordmark's own `v`
with the receipt's full stop beside it. The mark is the word, so at 16px the
mark is the word's first letter and its dot, not a second drawing; the film
frame §12.3 kept goes with it, because it pictured the medium rather than the
product's argument. Two properties are load-bearing and both are pinned: the
glyph **floats** on transparency, so one drawing serves a light and a dark tab
strip with no `prefers-color-scheme` variant this single-scheme surface has no
business carrying, and the gold is cored inside a 1-unit keyline in
`--gold-ink` — the same ink the system puts *on* gold — which keeps the shape
readable when the strip under it is white.

**How this pass was reviewed, and why it caught what it caught.** Against a
**read-only copy of the live corpus** — 182 videos, a talk with 164 shots and
189 OCR lines on one slide — served by a scratch instance on its own port, and
measured in a real browser rather than asserted in the suite. Items 1, 3, 5, 6
and 7 are each a bug the fixture cannot express: three keyframes do not page,
three OCR lines do not overflow a two-column box, and a job with no resolved
items has no title to print. The tests added with each fix reproduce the
*shape* of the corpus rather than its size — `?frames=1` makes the seeded video
a talk with more frames than one page — so they fail on the old code and cost
the suite nothing.

### 12.7 The merged frames view (2026-08-10) — and how to undo it

Round 4's item 2, kept in its own section for one reason: **it is the one
change in that pass Tom reserved the right to dislike**, so it landed alone, in
a commit that is only itself, and undoing it is one command.

```
git revert 1ed6516bc511be54ac29931a9f998271f8a1f8e6
```

or, if the id has moved under a rebase:

```
git revert $(git log --format=%H --grep='revert me to restore the split' -1)
```

Nothing else from round 4 rides in that commit, and it touches no
documentation, so the revert conflicts with nothing. **If it is reverted, this
section comes out with it** — by hand, since a revert will not remove it.

**What was wrong with two panels.** §5.3 gave the page a keyframe strip (192px
thumbnails of every frame) and, under it, an on-screen-text panel (512px stills
of the frames that had text, with the detection boxes drawn on them). They were
two views of two different questions and that held until round 3, which
(§12.5 items 5 and 6) made a still in the OCR panel open the *same* overlay a
strip keyframe opens, and moved the box↔line interaction into that overlay
because a detection box on a 512px still is a few millimetres of screen. After
that the second panel was the first panel's frames a second time, and the OCR
text printed under each of them was a receipt with nothing left to do: the
selecting had moved to the overlay.

**What the merge is.** One grid, `.frames`, of every keyframe on this page of
the strip, at the cell size a detection box needs rather than the cell size a
thumbnail wants — this grid is read, not swept. Per card: the 512px still with
its lime boxes over it, the timecode / ordinal / `ocr_state` / line count, the
shot and either the sharpness or which frame it duplicates, and — only when the
frame carries text — the same `--ocrbox-h` scrollbox of every line it read.
`align-items: start`, so a card holding thirty lines grows without stretching
the empty ones beside it.

**What that bought, beyond one panel instead of two.** One card is now the
whole of a frame — the shot bar's anchor (`#frame-N`), the evidence mark's
target, the lightbox's opener, and the scope the box↔line pairing is looked up
in. `.ocrgrid`, `.ocrframe` and `.ocrstage` are deleted (`.ocrstage` *was*
`.framebtn`: a fixed-aspect clipping box on `--black` behind a 1px inset
hairline), `.strip` becomes `.frames`, and `dashboard.js` stops keeping two
elements in step — `selectFrame` marks one node and `boxesFor` finds the card
by the `data-ocrframe` it always carried. The page also asks the frame cache
for **fewer** files than before: twenty-four stills, rather than twenty-four
thumbnails plus a second copy of every frame that had text.

**What it costs, stated so the revert decision is informed.** The 9rem contact
sheet is gone, and with it the ability to sweep twenty-four frames in one
glance at one screenful — four cards a row at 512px is a taller panel. The
argument for accepting that is that the strip's density was buying a *scan* the
page no longer needs to offer twice: the scene timeline above it is the thing
that answers "where in this video", at one bar per shot across the whole
runtime, and the frames grid answers "what is on this frame", which is a
reading and not a sweep.

## 13. The queue as a dashboard-wide flow (2026-08-12)

The phase-3 write side already made indexing possible; this amendment makes it
reachable from the surfaces where the next URL appears. It changes no write
policy and adds no route.

- **Every dashboard page links to `GET /dashboard/index` as `Add videos` when,
  and only when, the write side is registered.** The link is in the shared
  rail, so adding is at most one click away. It is absent — not disabled — in
  `VIDTHEQUE_PUBLIC_READONLY=1` and `VIDTHEQUE_AUTH=none`, exactly like the
  write routes behind it (§2.3, §3.2).
- ~~**The overview carries a quick-add form.**~~ **Removed 2026-08-13 (Tom).**
  It POSTed to the existing `/dashboard/index` handler with `expand=none`,
  `max_items=25` and normal priority — which is a second entry point to one
  write that also *decided*, in three hidden fields, what a pasted playlist URL
  meant. Adding videos is the rail item, on every page, and the form that owns
  the decision is one click behind it. Nothing about the handler, the batching,
  the Origin rule or the receipt changed; the overview got a panel of its height
  back.
- **`GET /dashboard/index` accepts `urls`, `expand` and `tags` as render-only
  prefill parameters.** It performs no normalisation, tool call, database read
  or write. `expand` must be one of the tool's three declared values or falls
  back to the form default; rendered `urls` and `tags` are capped at 16,384 and
  800 characters respectively so a deep link cannot create an unbounded HTML
  response. POST remains the only operation that interprets the values.
- **A video detail page uses that GET contract for `Queue more from this
  channel`.** The internal link prefills the video's stored source URL and
  `expand=channel_recent`; it does not queue anything until the operator
  submits the index form. A jobs empty state links to the same form. Both
  affordances follow the shared write-side predicate.

## 14. Dashboard search as owner inspection (2026-08-12)

This ships the search half of phase 5 as a dashboard document. It does not
change §1 non-goal 3: an agent searches with `search` at `/mcp`; there is no new
agent endpoint, MCP proxy or dashboard-only query shape to scrape.

- ~~A compact GET form in the shared rail opens `GET /dashboard/search`~~ **The
  rail carries a `Search` link to `GET /dashboard/search`, and the query box
  lives on that page.** *(Amended 2026-08-13 — §14.1.)* Every dashboard page
  still has one search entry. The result page is server-rendered and shareable:
  query, content channel, video-channel filter and pagination remain in the URL,
  and GET changes no state.
- The page enters through `public.api.search_payload`, the same handler behind
  both search JSON facades, which calls `tools.search.run`. It therefore gets
  the tool's ranking, result cap, text cap, pagination and credential-keyed
  `PUBLIC_CLAMPS` / `OWNER_CLAMPS`; it has no SQL and no second query
  implementation. The dashboard inspection rendering keeps the structured leg
  names/counts and every tool `note:` line verbatim. Corpus titles, channels and
  snippets remain autoescaped text.
- Every linked moment is admitted only when the tool returned an HTTPS
  `youtu.be` URL with a numeric `t=` value. The page prints that same
  `youtu.be/<id>?t=<second>` as the receipt; it never reconstructs a timestamp
  from display text or invents a link for a source that has none.
- Search is a read page and remains in the read-only projection. That is not a
  new public query policy: an anonymous projection request still receives the
  public clamp, while a bearer, session or trusted peer receives the owner
  clamp according to §2.4's existing matrix. The public welcome/search page is
  unchanged in this increment; the ask half of phase 5 remains unshipped.

### 14.1 Search is a page, not a rail widget (Tom, 2026-08-13)

**The decision.** The rail's search box is removed and `Search` becomes the
fourth destination in the first nav group, beside Overview, Videos and Jobs.
The reason is the one §14 did not weigh: a text field in the chrome has no empty
state, no filters, no leg counts and nothing at all to read until you have
already typed, which made the question this corpus exists to answer the only
surface on the dashboard you could not simply *go to*. Nothing about the handler
or the clamps changed; the entry point did.

What that costs and what it does not: `rail_query` leaves `_chrome()`, the
`.rail-search` rules leave the stylesheet, and the rail is four `.navlist a`s
with the active state it already had. §2.4's read-only projection is unchanged —
`Search` is a read page, so the item is there in the demo too, above the demo's
own `Search the corpus` link back to the welcome page.

**The page itself.** Presentation only. No tool payload, parameter, clamp or MCP
contract moves; everything below is the dashboard layer rendering what
`search_payload` already returned.

- **Human leg labels, with the machine key kept.** `transcript_fts` renders as
  *Transcript — keyword match (FTS)*, `frame_knn` as *Frames — visual candidates
  considered*, and so on for all eight keys, each with its unit — the three
  numbers are three units and are not summands (tool-surface.md §9.2). The raw
  key is printed beside every label in mono at label size, because this surface
  is an instrument and the key is what a bug report quotes. A key this table does
  not know renders under its own name rather than disappearing; the sub-legs are
  inset under the fused leg they explain.
- **Per-hit evidence badges.** `source` becomes the demo's own three words —
  `spoken`, `on-screen`, `frame`, both of the first two for `transcript+ocr` —
  so a badge means the same thing on both surfaces. The raw string stays on the
  badge group as `title="source=…"`, and an unrecognised source still gets a
  badge carrying its own name.
- **The frame is on the row.** A hit that names a keyframe renders it from the
  derived cache at `STRIP_WIDTH` in a 128×72 box, and clicking it opens the
  **same `#shot` overlay** at `LIGHTBOX_WIDTH` that the keyframe grid opens,
  through the same delegated handler in `dashboard.js` — one lightbox contract,
  not a second one to drift. No new cache width (§6.4) and never base64. The
  overlay carries no OCR-box toggle here: a search hit has no `.ocrline` rows on
  the page for boxes to be cloned from. A hit with no keyframe shows its channel
  in the box the frame would have taken, so the column stays a column.
- **Two links per hit, and they answer different questions.** The receipt still
  leaves for `youtu.be/<id>?t=<second>` under §14's existing admission rule. The
  title and the timecode now also point *into* this deployment: a hit with a
  keyframe lands on that frame on the video page
  (`?frame_offset=…&select=<ord>#frame-<ord>` — `ord` is dense per video, so the
  strip page is arithmetic, exactly as the shot bars have always computed it),
  and a transcript hit links to the video plainly, because it names its cues by
  *id* while the transcript panel pages by *offset* and there is no honest
  arithmetic between them.
- **The snippet is set as what it is evidence of**, following the demo: spoken
  text quoted in the human face, on-screen text mono and lime, frame text mono
  and muted. The query's own words are marked in it — gold on the human channel,
  `--fg` on the two mono ones — and a mark is "these are your words, here",
  never a claim about *why* a hit ranked, since the semantic legs do not match
  words at all. The marking is text runs plus a flag out of the view; the
  template decides what a marked run looks like, so no HTML is built from corpus
  data and every run is escaped like every other string here.
- **Lime on a second page, and it is still The Lime Rule.** DESIGN.md's
  dashboard bullet says lime appears only where there is on-screen-text
  evidence, and names the video page because that was the only page which had
  any. An OCR hit's snippet *is* an on-screen-text line and its badge *is* the
  seen channel's label — both are exactly what the rule reserves lime for. A
  spoken hit and a frame hit carry none.

## 15. Current pipeline readiness (2026-08-12)

The overview gains one display-only readiness panel. It is a measurement made
for the current page load — no row is written, no sample is retained, and no
chart or history is implied (§1 non-goal 5). It has no controls and edits no
configuration (§1 non-goal 4).

- MCP and database are `ready` when the page renders: the route is executing in
  the MCP process and the overview's bounded reads have succeeded. Vector search
  reports the live `db.vectors.enabled` effect; the private page also prints its
  drift reason when one exists.
- The private page probes the worker's documented `GET /status` over HTTP. It
  imports no worker Python. The probe runs concurrently with the overview reads,
  has a one-second request timeout, accepts at most 64 kB and twelve backend
  rows, and prints the task, model id actually reported by the worker, and
  whether that backend is loaded or cold. An unset worker URL says
  `unconfigured`; a timeout, transport failure, non-2xx response or malformed
  status says `unavailable`. None of those failures prevents the overview from
  rendering. `last health check` is the UTC second at which this observation
  completed, not a stored heartbeat.
- The §2.4 projection applies per field. It keeps **MCP ready** and **database
  ready** because successfully reading the page already reveals both; keeps the
  **vector-search state** because it changes what a visitor should believe about
  results; and keeps the **check clock**, which is only the timestamp of this
  page-local observation. It drops the **worker row**, **worker reachability**,
  **served model ids/load state**, and the existing **drift reason**, because
  those identify operator infrastructure and settings. The projection does not
  issue the worker request at all rather than fetching data it must discard.

**Amended 2026-08-13 (Tom): one panel, not two.** The declared-models panel at
the foot of the overview — `config`'s four checkpoint ids and dimensions, plus
the `indexing allowed/refused` state — is folded into this one, because the two
panels were halves of a single question and the reader had to hold the top of
the page in their head to answer it. The shape is now: a strip of five states
(MCP · database · worker · vector search · **indexing**), then **declared beside
served** as two tables on one row, the column heads naming which side is which
so neither table needs a heading. The whole panel wears the drift rule when the
halves disagree, which is where that rule always belonged. Nothing moves in the
projection's terms: the declared table is dropped there exactly as its own panel
was, the served table with the worker row, and the indexing state with them —
"indexing refused" is a sentence about a worker nobody visiting the demo can
reach. No new read, no new clamp, no history.

## 16. Job repair actions (2026-08-12)

### 16.1 Cancellation

The private write side adds `POST /dashboard/jobs/{job_id}/cancel`, exposed on
the job detail page and as a compact action on live rows. Like every dashboard
write (§2.4 and §3.3), the route is absent when
`VIDTHEQUE_PUBLIC_READONLY=1` or `VIDTHEQUE_AUTH=none`, is POST-only, and uses
the existing credential and Origin guard. The action is rendered only for
`queued|running` jobs.

Cancellation has two honest outcomes. A queued job, including one held behind
`not_before`, has no worker to cooperate with and is settled `cancelled`
immediately with its queued items; it never waits through the backoff merely to
notice the request. A running job stays `running`, sets
`cancel_requested=1`, and renders “cancel requested” until the pipeline stops
at the next stage boundary. The indexing pipeline checks after every stage,
including `frame_embed` before finalization, so the request cannot be followed
by a falsely successful item. Existing terminal jobs refuse the action.

A cancelled item settles its video the way a crash does (`_reset_video`'s
rule): `stale` when the video was already indexed — it keeps its data and
stays searchable — and `pending` only when a first index never finished.
Settling `pending` unconditionally would un-publish an indexed video whose
repair was cancelled. *(Amended 2026-08-12, review of PR #4.)*

### 16.2 Retry only failed or degraded items

The private detail page for a finished job offers
`POST /dashboard/jobs/{job_id}/retry` only when the job has a failed item or a
`done` item named by `degraded_items`. Registration and authorization follow
the same §2.4/§3.3 rules as cancellation: absent in read-only and `AUTH=none`
modes, POST-only, and guarded by the existing credential and Origin check.

The selection is bounded to 200 items and contains each failed item plus each
degraded item exactly once, even when several stages failed for one video.
Successful items are not submitted. Each batch goes through
`tools.indexing.index_video`; batches are at most `URLS_PER_JOB` (ten), so the
MCP tool's ten-URL cap is unchanged. The retry preserves the original job's
channels, tags, expansion bound and priority, and does not force a rebuild:
the service and pipeline therefore resume a degraded video at its failed stage
while retaining successful stages. The response is a receipt linking the old
job and every newly queued job — except when exactly one job was queued and
nothing needs explaining, which follows `index_submit`'s rule and redirects
to it: a retry receipt left as the POST response is a page whose reload
queues the repair twice. *(Amended 2026-08-12, review of PR #4.)*

### 16.3 Job triage filters and ordering

`GET /dashboard/jobs` and its polling endpoint accept server-side `error_code`,
`kind`, and `degraded=1` filters in addition to the existing state filter.
Degraded means that the job has a `done` item whose video currently has a
failed stage, the same predicate as `degraded_items`; it is not inferred from
the job's terminal state. `order` is explicit and one of `newest`, `priority`
(lower numeric priority first), or `wall_clock` (longest
`created_at`→finished/now span first), with stable id tie-breakers.

`jobs_store.list_jobs` owns every predicate and ordering so the page and JSON
poll target cannot disagree. All active choices, including row limit and
offset, survive in polling and both pager directions. The store still reads at
most `limit + 1`; the response exposes `has_more` and never computes a total.

### 16.4 Per-video indexing history

The video detail page adds a bounded **Recent indexing runs** panel. One
`jobs_store.recent_jobs_for_video(video_id, 10)` statement reads the latest ten
jobs whose items touched the video, newest first, with job state, kind, created
and finished clocks, job error code, and degraded stage names under the same
predicate as `degraded_items`. Every row links to the job detail page.

This is a detail-only query. The videos table performs no job-history read and
there is no per-video query fan-out on that list. The panel is capped directly;
it does not issue a count and does not claim an exact total.
