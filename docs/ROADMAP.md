# Roadmap

What is actually open, verified against the code on 2026-08-28 rather than
inherited from a handoff. Every line here was checked by reading the
implementation and running the tests that would exist if it were done; the
things that turned out to be *already done* are recorded at the bottom so
nobody rediscovers them a third time.

Ordered by how self-contained the work is, not by value. Value is Tom's call
and the two marked **[Tom]** are decisions before they are tickets.

---

## Open

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
  switching traffic. Remove replaced templates, scripts, styles, and obsolete
  instructions only after their callers have replacements. Research stays
  append-only.
  Through the edge: `GET /` returns the landing, `GET /demo` the reader, and
  each carries the four document headers (demo-site.md §7 item 0). Python has
  no page at `/` any more, so a misrouted edge shows the MCP mount's 404.

The dashboard port, page by page. Each needs its JSON contract first, then its
React page, verified against **both** the owner and the public read-only
projection — a page that renders a field the projection drops is the failure
mode this list exists to prevent.

- **Overview and ledger pages** — the JSON exists; the React pages are in
  progress today (2026-09-05).
- **Videos** — the table, its filters and ordering, and the video detail page,
  including the frames strip and the cue pagination.
- **Search** — the owner inspection page, over the handler `/api/search`
  already shares.
- **Jobs** — the list, the job detail page, and a poll target that replaces
  `static/jobs.js` without moving the 2 s tick or its server-side clamp.
- **Jobs controls** — cancel and retry-failed: the first writes to cross, so
  the first to need §3.3's cookie and Origin rules expressed over `fetch`.
- **Indexing** — the index form and its submission, with the same server-side
  bounds the form has now.
- **Tags** — the per-video tag write.
- **Following** — the list, the detail bands and the six writes; absent
  entirely when the deployment registers no write side (dashboard.md §18).
- **Session and login** — the sign-in page, the cookie flow and sign-out.
  `/dashboard/api/session` describes the deployment; the login POST itself has
  no JSON twin yet.

### 1. Turn following on — no code, and it is why the corpus stopped growing

CT 9002 has **zero follows and zero collections**. `VIDTHEQUE_FOLLOW_CHECKS`
defaults to `1`, so the machinery is armed with nothing to check, and the last
video was indexed 2026-08-19. The follow contract shipped in 0.0.5 and has
never had a row. One `follow-channel action=follow` closes it.

### 2. **[Tom]** The unfollow refund hole — `following.md` §11 Q7

`follows/store.py:217` deletes the `collections` row; the cascade takes
`follow_seen` with it, and the daily budget is a sum over `follow_seen`. So
unfollowing returns hours already spent on download and GPU to the rolling
window. The docstring names the cascade without noticing the consequence.

Three answers, and the document says the fork is Tom's:
- `follow_seen.collection_id` nullable with `ON DELETE SET NULL`, plus a
  "belongs to a live follow" clause on every read — cheap, leaves orphan ledger
  rows nothing on any surface explains;
- an append-only spend row per accepted candidate, pruned on the `job_events`
  clock — one more table, but the budget stops being a property of a table that
  exists for another reason;
- document the hole and move on, on the grounds that a corpus with a handful of
  follows will never notice.

### 3. `failing` never clears itself — `following.md` §11 Q4

A follow whose channel 404s is set `failing` by the check and stays there until
a human resumes it (`follows/check.py:159`). Deliberate for a renamed channel;
wrong for a channel that was briefly private or a one-off extractor break, which
produce the identical state. The alternative is a slow retry — one check a day —
that clears on the first success.

### 4. F11 — no tool parameter has a description

`Field(description=…)` appears nowhere in `mcp/src`. Every parameter's meaning
lives in the tool description prose instead, which is the budget §3 is trying to
protect. Mechanical, wide, and it touches every tool signature.

### 5. `cancel-job` and `delete-video` — F12's unfinished half

A queued job cannot be cancelled and an indexed video cannot be deleted through
the surface; `docs/takedown.md` deletes with raw SQL against a live database,
including the vec0 trigger and FK-pragma dance. F12's read-only posture shipped
(`public/readonly.py` derives `WRITE_TOOLS` from the annotations); the inverses
did not. Takes the surface to twelve, so it is a contract change, not a patch.

### 6. **Closed 2026-09-05** — a DOM-level test harness for the public page

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

### 7. F1 — structured OCR, the big one

OCR reaches every query as `group_concat(o.text, ' | ' ORDER BY o.line_no)`, so
a table's cell loses its header and a two-column slide interleaves. The design
bench calls this the single highest-value change on its list. It touches OCR
extraction in `pipeline/`, the `ocr_lines` shape, and every query that flattens
it. Multi-session work.

### 8. The channel-count quirk — needs re-diagnosis before it is a ticket

Carried on a backlog since 2026-08-09 and never described anywhere. The string
"channel-count" appears in no other file, and `library.py:392` /
`queries.py:1487` are unchanged since before it was written. Someone has to
reproduce it before it can be scoped.

### 9. A wrong method on `/api/*` answers 404, not 405

`Mount("/", app=mcp_app)` is last in the route list and matches everything.
Starlette scores a path-match-method-mismatch as a *partial* match and prefers
any later full match, so the mount answers first: `GET /api/ask` and
`POST /api/search` both read `404 Not Found`. The mount must stay last, so the
fix is not a reorder — it is either an explicit method-aware shim above the
mount, or accepting it and saying so in the contract. Pinned by
`test_a_wrong_method_is_a_404_because_the_mcp_mount_outranks_the_405`.

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
| Demo page boots `undefined` when the search bucket is spent | 2026-08-28 — `/api/meta` shares the bucket; a 429 has a JSON body, so `.json()` resolved and the catch never fired |

**The lesson, which is why this file exists:** two of these were shipped the
same day the document listing them as open was written. A backlog inside a
dated record is a snapshot, and a snapshot read a fortnight later is wrong in a
way that costs a day. Open work lives here, in one file, or it does not exist.
