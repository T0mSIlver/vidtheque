# Roadmap

What is actually open, verified against the code on 2026-08-28 rather than
inherited from a handoff. Every line here was checked by reading the
implementation and running the tests that would exist if it were done; the
things that turned out to be *already done* are recorded at the bottom so
nobody rediscovers them a third time.

Ordered by how self-contained the work is, not by value. Value is Tom's call
and anything marked **[Tom]** is a decision before it is a ticket.

---

## Open

### 2026-09-18: publishing — one deployment shape, generations, light releases

Contract: `docs/design/publishing.md`, in its §5 order. None of it is built.

- Public box on the compose stack at a pinned tag, checked beside the running
  services, then the tunnel cutover from `:8100` to `:8080`.
- Dependency base images, then release `0.0.9`. The first tag builds both
  bases once; watch that run.
- Corpus generations: `vidtheque_mcp.corpus_snapshot`, the `rrsync` push, the
  activation unit with rollback, `scripts/publish_corpus.sh`. Delete
  `corpus-manifest.json` and the poller's corpus stage with it.
- Alignment as data: migration `0009`, the `editions align` and `propose`
  commands, the fixture at `schema_version: 2`.
- A full rehearsal before 2026-09-23: index, tag, publish, align, roll back.
- After the edition: drop the CUDA base image from the worker, behind a GPU
  validation run.

### 2026-09-15: AI Engineer Paris 2026 edition

- Review and approve `docs/design/aie-paris-2026.md`, especially the
  "Every main-stage talk" hero repair and the proposed follow title-term cap
  increase from 10 to 50.
- Implement the additive edition facade read, tag-scoped search and Ask, the
  `/paris` page, its document-policy and Caddy routes, and their CPU and browser
  tests only after the phase 1 contract review.
- Implement the edition-only Voxtral worker backend, schedule-derived context
  bias, 60-minute seam handling, and the three-arm private evaluation under
  `bench/` without retranscribing the existing corpus.
- Decide whether the organizer gate requires an affirmative reply or only a
  sent request. An explicit refusal blocks publication under either rule.
- Run a pre-publication delta audit for the Mistral credential and spend cap,
  edition facade, and untrusted transcript and slide-OCR handoff to agents.
- Add the 2026 stream ids, talk upload ids, and checked offsets after those
  videos exist. Refresh the committed schedule if the source page changes.
- Run the two 2025 source videos from an isolated private data directory,
  record long-VOD table growth and queue recovery, and publish no source
  material, transcript, per-talk result, or aggregate result from that
  rehearsal.

### Frontend replacement: landing, demo, and management dashboard

Tom chose a complete Next.js and React replacement before traffic switches
on 2026-09-05. See `docs/design/DECISIONS.md`. PRs #23 through #30 supply the
initial reader, API client, search, Ask, and component tests.

- Review and merge the initial stack with frontend checks in hosted CI.
- Implement direct browser-to-Python API calls, chosen by Tom. Verify routing,
  session handling, trusted client addresses, and any cross-origin access.
- Port the landing and demo while preserving their URLs and locked positioning.
  *Landed 2026-09-05:* both render from `web/`, and Python's `GET /`,
  `GET /demo` and `GET /static/{path}` registrations are gone along with the
  two static bundles and the tests that read their markup
  (`docs/design/frontend-migration.md` §1a, demo-site.md §1). One thing the
  removal handed over rather than kept, and it is a check before cutover rather
  than a ticket: `_DOCUMENT_HEADERS` — the CSP and its three companions — left
  with the pages, so it is the front end's to send on both of them and no test
  in `mcp/` can see whether it does. demo-site.md §7 item 0.
- Define the Python HTTP contracts needed by these pages. Keep state and
  operation policy in `mcp/`; remove presentation dependencies as pages move.
  *Landed 2026-09-05:* the first read slice — `GET /dashboard/api/overview`,
  `/ledger` and `/session`, over the assemblers the Jinja pages now share
  (`docs/design/frontend-migration.md`, dashboard.md §19). Every port below
  still needs its own contract written before its page.
- Verify deployment, rollback, browser workflows, and CPU-only checks before
  switching traffic. *The removal half is done (2026-09-06): every replaced
  template, script and style is deleted, and the callers all had replacements
  first — dashboard.md §23 is the record. Research stays append-only.*
  **The deployment half landed the same day and the checklist below now lives
  in one place: `frontend-migration.md` §10.** `deploy/Caddyfile` is the edge
  that expresses §1a and §1d, the compose stack and the systemd box both run
  it, and §10 carries the eight through-the-edge checks, both rollback paths,
  and what is deleted after cutover (the development shim, on the day
  development runs behind the edge too — not before). What is left here is the
  running of it, on the box, with Tom.
  Through the edge: `GET /` returns the landing, `GET /demo` the reader, and
  each carries the four document headers (demo-site.md §7 item 0). Python has
  no page at `/` any more, so a misrouted edge shows the MCP mount's 404.
  And **a `POST` to `/dashboard/following` through the edge reaches Python**,
  **so does a `POST` to `/dashboard/index` beside it**, **and so does a `POST`
  to `/dashboard/login` beside both** *(2026-09-05)*: each is a path both
  processes own, split by method — `GET` to Next, the form's `POST` to Python
  (frontend-migration.md §1d). A proxy that routes any of the three on path
  alone answers the write with a document and nothing says so. Development has
  a shim for all three paths, not the rule: `web/next.config.ts`'s
  `PYTHON_FORM_POSTS` forwards them in `beforeFiles` on the
  `application/x-www-form-urlencoded` content type, and that entry is deleted
  the day development runs both processes behind one proxy.

The dashboard port, page by page — **all of it landed 2026-09-05, and the
Python HTML it replaced was deleted 2026-09-06** (dashboard.md §23,
DECISIONS.md "The Python dashboard HTML is gone"). Each page needed its JSON
contract first, then its React page, verified against **both** the owner and
the public read-only projection.

- **Overview and ledger** — *landed 2026-09-05*, over `/dashboard/api/overview`
  and `/dashboard/api/ledger`, with the management chassis under them
  (dashboard.md §19).
- **Videos, the table and the detail** — *landed 2026-09-05*, over
  `/dashboard/api/library` and `/dashboard/api/library/{video_id}`, with the
  cue pager for the transcript pane (dashboard.md §20, §5.2, §5.3).
- **Search** — *landed 2026-09-05*, over `/dashboard/api/search` under the
  handler's own parameter names, so a bookmarked query still resolves
  (dashboard.md §14.2, §14.3).
- **Jobs, the list and one job** — *landed 2026-09-05*, over the two poll
  targets, which gained the nine fields the templates rendered and the payloads
  dropped (dashboard.md §5.4, frontend-migration.md §6c).
- **Following, list and detail** — *landed 2026-09-05*, over
  `/dashboard/api/following` and `/dashboard/api/following/{slug}`, registered
  with the writes so the whole surface is 404 together where a deployment
  declines it (dashboard.md §18.6, §22).
- **Indexing and tags** — *landed 2026-09-05*: the index form, the table's
  per-row re-index, and the detail's manage panel with its tags write
  (dashboard.md §21).
- **Jobs controls** — *landed 2026-09-05*: cancel and retry-failed, the first
  writes to cross, over §3.3's cookie and Origin rules expressed with `fetch`.
- **Session and login** — *landed 2026-09-05*: the sign-in page, the cookie
  flow and sign-out. `POST /dashboard/login` stays Python's for good — the
  `Set-Cookie` is the write and a React shell cannot mint an `HttpOnly` cookie.
- **Remove the replaced templates, scripts and styles** — *done 2026-09-06.*
  `views.py`, `templates/`, `static/`, the `/dashboard/static/*` route, the
  Jinja environment and the `jinja2` dependency are deleted; the rendered
  strings on the two jobs routes and the cue pager went with the scripts that
  read them, keeping `basis` (dashboard.md §23). Two enhancement layers went
  unported that day; they landed the next, below.

**The parity round** *(2026-09-06)*. Every ported page was read against the
template and the script it replaced, and what the audit found was closed page by
page — the payload fields first (dashboard.md §19–§21), then the pages:

- **Overview and ledger** — the four readings the port rounded off, the write
  state and the drift banner drawn from the payload rather than from a second
  request, and the failed-gap ceiling printed as a ceiling.
- **Videos, the table** — the band applies itself again and every control shows
  the filter that *ran*; a refused read redraws it from the filters the refusal
  echoes; the two ends of a date range stopped sharing one key; the table
  stacks on a phone.
- **Videos, the detail** — the frame opens in the page, the shot band previews
  the shot under the pointer, the transcript is addressable, and a dead
  thumbnail says which channel it came from.
- **Search** — the marks, the four snippet classes, a moment's title, place and
  dropped filter, and the frame overlay on the same contract as the frames
  view's.
- **Jobs, list and detail** — the band echoes the listing that answered, the
  tick patches the rows it has, the clocks move, the retry receipt carries what
  the redirect used to show, and the two redaction lines read the listing's own
  flag.
- **Following, list and detail** — the held band is a warning and not an error,
  a write refreshes the page it changed, and a refusal prints the `next:` line
  it was already given.
- **Indexing and tags** — the receipt outlives a reload, the form says what ran,
  and a refusal echoes the values the server resolved.
- **Session and login** — a refused submit empties the field and takes the caret
  back; a refusal is a page again, with somewhere to click.

**What stays different, on purpose.** The pages fetch in the browser, so they
need JavaScript where Jinja needed none — the trade §1a took deliberately.
Every write answers inline where the Jinja form answered with a redirect. React
formats the typed values the payloads now carry, and the rendered strings went
with the scripts that read them. The search band does **not** auto-submit,
because the Jinja band did not either — `data-autosubmit` was on the videos
form and on nothing else. And the search page's stills stay page-built at the
dashboard's 192 and 1280 rather than the payload's 320 and 960 (dashboard.md
§14.2).

Both enhancement layers — *landed 2026-09-06*, in `66f9cb1`:

- **Videos, the frames lightbox** — a frame card is a button again and the
  still opens in a native `<dialog>` at 1280px, its detection boxes over it at
  the payload's own 0–1 coordinates, its lines beside it, and the file still
  one click further in. The search page opens the same overlay on the same
  contract, and since the consolidation it is the same component
  (`web/src/components/dashboard/FrameOverlay.tsx`), with the OCR layer the video
  page's own (dashboard.md §5.3, §14).
- **Videos, the timeline scrub preview** — pointing along the shot band shows
  the shot under the pointer again: its still, its span and its kept ratio,
  and the nearest shot when the pointer is in a gap between two.

`5bba419` finished the page around them — the transcript's place is a URL again
and eight readings the port had rounded off came back — and amended
dashboard.md §5.3 for both commits.

The one thing the deletion had handed to `web/` rather than finished is done
too *(2026-09-06, `d7c1cd9`)*: the cue schema stopped asking for `at`, `conf`
and `chunk`, the three strings the endpoint no longer sends, and the timecode,
the log-probability and the chunk label are composed from the typed fields
beside them. See frontend-migration.md §3.

**The three JSON endpoints that answered in rendered strings** — settled and
done. `/dashboard/api/jobs`, `/dashboard/api/jobs/{job_id}` and
`/dashboard/api/videos/{id}/cues` predated the typed-values decision
(DECISIONS.md, 2026-09-05) and were the Jinja scripts' own poll targets. Tom's
rule was **typed fields beside the strings, strings cut at the port**: add the
typed half additively, and delete a rendered string in the commit that deletes
the page or script reading it.

*Landed 2026-09-05:* the cues endpoint was the only one that needed code — it
gained `start_s`, `end_s`, `avg_logprob`, `chunk_opens` and `chunk_closes`,
because the two jobs routes already carried their typed half.

*Landed 2026-09-06:* the strings are cut. Off the jobs pair went both `text`
blocks and each event's `at_text` — nine renderings of numbers sent beside them
— keeping `basis`, the sentence saying what the progress percentage is computed
over, as a field on the card because it is policy text. Off the cues endpoint
went `at`, `conf` and `chunk`. The readiness observation's ISO-8601
`checked_at` went the same way, leaving the epoch under the same name.

**The body type role has a second half the generated sheet cannot say**
*(open, 2026-09-16)*. DESIGN.md sets **body** at 16px "15px below `--bp-hand`",
and both old front doors dropped it with a `body { font-size: 15px }` at ≤780px
(`demo/style.css:1361`, `landing.css:438`, audit D17). `styles/type.css` is
generated by `web/scripts/tokens.mjs` from frontmatter where a role carries one
`fontSize`, so `.t-body` is 16px at every width, and the old rule's own form
would move nothing: on `/demo` at 390px the only string inheriting the body size
is a visually hidden label. The work is the role and the generator — a
breakpoint the frontmatter can express — not a media query in `globals.css`,
and it is a change to the visual contract's machinery. `frontend-migration.md`
§11.4 records why the parity round left it.

### 1. F11 — no tool parameter has a description

`Field(description=…)` appears nowhere in `mcp/src`. Every parameter's meaning
lives in the tool description prose instead, which is the budget §3 is trying to
protect. Mechanical, wide, and it touches every tool signature.

### 2. `cancel-job` and `delete-video` — F12's unfinished half

A queued job cannot be cancelled and an indexed video cannot be deleted through
the surface; `docs/takedown.md` deletes with raw SQL against a live database,
including the vec0 trigger and FK-pragma dance. F12's read-only posture shipped
(`public/readonly.py` derives `WRITE_TOOLS` from the annotations); the inverses
did not. Takes the surface to twelve, so it is a contract change, not a patch.

### 3. **Closed 2026-09-05** — a DOM-level test harness for the public page

The page said it in its own docstring: *"What the page does with that — a
notice under the rows it already has, rather than a wipe — needs a DOM-level
harness and is not asserted here."* Cross-cutting: a tooling choice and CI
wiring, bounded by the repo's rule against self-hosted runners.

*2026-09-01:* the Next.js front end in `web/` got one — Vitest, jsdom and
Testing Library, `pnpm test`, with the ask stream exercised against a recorded
real response. The Python-served demo page still had none, and this line stayed
open for it.

*2026-09-05:* there is no Python-served demo page. It is `web/`'s, which is the
surface with the harness, and the docstring above went with the test file it
was in. Closed by replacement rather than by instrumentation — worth the
distinction, because the assertion it wanted still has to exist on the React
page, and that is `web/`'s to carry, not this file's.

### 4. F1 — structured OCR, the big one

OCR reaches every query as `group_concat(o.text, ' | ' ORDER BY o.line_no)`, so
a table's cell loses its header and a two-column slide interleaves. The design
bench calls this the single highest-value change on its list. It touches OCR
extraction in `pipeline/`, the `ocr_lines` shape, and every query that flattens
it. Multi-session work.

### 5. The channel-count quirk — needs re-diagnosis before it is a ticket

Carried on a backlog since 2026-08-09 and never described anywhere. The string
"channel-count" appears in no other file, and `library.py:392` /
`queries.py:1487` are unchanged since before it was written. Someone has to
reproduce it before it can be scoped.

### 6. A wrong method on `/api/*` answers 404, not 405

`Mount("/", app=mcp_app)` is last in the route list and matches everything.
Starlette scores a path-match-method-mismatch as a *partial* match and prefers
any later full match, so the mount answers first: `GET /api/ask` and
`POST /api/search` both read `404 Not Found`. The mount must stay last, so the
fix is not a reorder — it is either an explicit method-aware shim above the
mount, or accepting it and saying so in the contract. Pinned by
`test_a_wrong_method_is_a_404_because_the_mcp_mount_outranks_the_405`.

### Backfill, or let the gap stand — the one decision the two follows left open

`backfill=0` on both, so the first check marked the whole back catalogue
`skipped_horizon` and queued nothing: following brings in what is published
*from now*. The corpus's newest video is 2026-08-19 and the follows were made
2026-08-30, so eleven days of uploads from `@aiDotEngineer` and `@t3dotgg` are
in neither. Closing that gap is `index-video` on the URLs, or unfollow and
follow again with `backfill=N` — there is no `action="edit"`, which is the same
missing verb `following.md` §11.7 names. Tom's call, because it is a download
burst and GPU time rather than a bug.

### Still deferred, unchanged, and still correctly deferred

`get-clip`, speaker identity management, row-level permissions and `format=json`
(`tool-surface.md` §6). A Google Takeout `subscriptions.csv` import and whether
`max_per_check=5` earns its place beside the daily budget (`following.md` §11
Q2, Q6) are open questions, not queued work.

---

## Done — do not re-plan these

Each was listed as open on a document still in the repo, and each was verified
shipped by reading the code and running its tests.

| Was listed as | Actually shipped by |
|---|---|
| Typed 400s → skip-frame, not fail-stage | `7f2c285`; `worker_client.py:79` `PER_ITEM_CODES`, bisection in `runner.py:1134` |
| Frame-level OCR FTS (task #20) | migration `0003_ocr_frame_fts.sql`; `ocr_frames_fts` is per-frame |
| F3 `get-frames max_text_chars` · F4 state-aware `job-status` hint | `f5e0364` |
| F5 `note:` when a clamp binds | `a60c0e6` |
| F9 `?w=`/`?q=` applied on the frame route | `5a599b5` |
| F8 unknown resource URI crashes the server | Never reproduced: returns `-32602 Unknown resource` cleanly |
| Subscriptions deferred | `follow-channel`, tool ten, 2026-08-15 |
| Markdown export deferred | `GET /videos/<id>/export.md`, 2026-08-28 |
| following Q3 held-review visibility | `library.py:558` prints `· N held` in `corpus-summary` |
| following Q5 note when checks are off | `follows.py:450` |
| F6 zero-hit transcript leg says nothing about why | 2026-08-28, `search.py` |
| F10 cap footer fires on a corpus too small to bind | 2026-08-28 — and it was naming the wrong video |
| HTTP envelope cases untested | 2026-08-28, `test_public_envelopes.py` — and they found the boot defect below |
| Following was never turned on | 2026-08-30 — `@aiDotEngineer` and `@t3dotgg`, /videos, 5 per check, `backfill=0`; both first checks listed clean |
| following Q4 `failing` never clears itself | 2026-08-28, migration `0008_follow_retry.sql` — daily retry, bounded at seven, `fail_count` is the receipt |
| following Q7 unfollow refunds the daily budget | 2026-08-28, migration `0007_follow_spend.sql` — the spend outlives its follow, and `unfollow` says so |
| Demo page boots `undefined` when the search bucket is spent | 2026-08-28 — `/api/meta` shares the bucket; a 429 has a JSON body, so `.json()` resolved and the catch never fired |

**The lesson, which is why this file exists:** two of these were shipped the
same day the document listing them as open was written. A backlog inside a
dated record is a snapshot, and a snapshot read a fortnight later is wrong in a
way that costs a day. Open work lives here, in one file, or it does not exist.
