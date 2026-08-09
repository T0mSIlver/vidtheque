# The management dashboard — the primary surface for the index

**Status: DRAFT, pending Tom's review. Written 2026-08-09.** Nothing here is
implemented. Every fact about the current code was checked against the tree at
`7dc8226` and is cited with a path; every *recommendation* is mine and is marked
as one. The open questions in §10 are real forks, not rhetorical ones — the rest
of the document is what I would build if nobody answered them.

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

Today `/` serves `public/static/index.html` (`public/__init__.py:42`): search,
ask, and a six-video "in this corpus" list. **That page is the welcome page.**
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
   two policies, no second query layer. This also settles an open question
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
| `keyframe` | `scenedetect-<detector>-w<max_width>` | `pipeline/runner.py:707-708` |
| `ocr` | `config['ocr.model']` | `pipeline/runner.py:793`, `:855` |
| `frame_embed` | `config['frame_embed.model']` | `pipeline/runner.py:862`, `:916` |

So the provenance panel (§5.3) needs **no schema addition**. Four caveats the
page must be honest about rather than paper over:

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
  incremented in `claim_item` (`:159-179`).
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
  `VIDTHEQUE_RATE_LIMIT_BACKOFF_S` (default 300) for `E_RATE_LIMIT`, else 5 s
  (`jobs/runner.py:346-351`).
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

In `jobs/store.py`:

- `job_event_page(job_id, item_id, limit)`.
- `_JOB_SQL` selects `j.not_before` and `j.priority` (§4.4).

In `public/api.py`: the clamp constants become a policy object (§2.5.1).

**The one possible schema change** is `video_stages.worker_version TEXT` (§4.1
caveat 4) and it is Tom's call. If it lands, `index-schema.md` §1.3 changes in
the same commit — CLAUDE.md's contract rule.

**Two pre-existing bugs this work makes visible**, both backlog rather than
scope: `list-videos`'s `cues`/`frames` fields are declared and always empty
(`tools/library.py:190-191`), and `login_session_ttl_s` is hard-coded with no env
reader (`config.py:92`) while every other tunable has one.

---

## 8. Rollout — five shippable phases

**Phase 0 — today.** `public/` is the embryo. Nothing to do.

**Phase 1 — read-only dashboard, private mode.** The `/dashboard` route group,
`/dashboard/api/*` with owner clamps, three pages: overview, videos table, video
detail. No writes, no new auth (read views follow whatever `/frames/*` already
accepts). This is the highest value per line in the whole plan — the provenance
panel and the four browsers — and it incidentally delivers demo-site.md §7.4, a
JSON facade in private mode.

**Phase 2 — the jobs view.** Read-only. `not_before` as a countdown, `attempts`,
`degraded_items`, the `job_events` tail, 2 s polling. Closes the blind spot the
overnight batch found. Still no writes.

**Phase 3 — the write side.** Session login (bearer → the existing
`vidtheque_session` cookie), POST-only + `Origin` discipline, the index form,
re-index and tag actions, `VIDTHEQUE_DASHBOARD_TRUSTED_CIDRS`. New env vars land
in `deploy/.env.example` in the same commit.

**Phase 4 — the demo becomes the subset.** `VIDTHEQUE_PUBLIC_READONLY=1` serves
the welcome page plus the read-only projection; the demo page keeps its search
and ask and gains a link into the browsable corpus. This is the phase where
`public/` stops being a separate frontend and becomes a policy on the
dashboard's.

**Phase 5 — search and ask move in.** The dashboard grows the search box and the
ask pane, sharing `public/api.py`'s handlers; the welcome page becomes purely an
entry point. `delete_video` — the pipeline job, then the button — belongs here or
later.

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
