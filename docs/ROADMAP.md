# Roadmap

What is actually open, verified against the code on 2026-08-28 rather than
inherited from a handoff. Every line here was checked by reading the
implementation and running the tests that would exist if it were done; the
things that turned out to be *already done* are recorded at the bottom so
nobody rediscovers them a third time.

Ordered by how self-contained the work is, not by value. Value is Tom's call.

---

## Open

### 1. Turn following on — no code, and it is why the corpus stopped growing

CT 9002 has **zero follows and zero collections**. `VIDTHEQUE_FOLLOW_CHECKS`
defaults to `1`, so the machinery is armed with nothing to check, and the last
video was indexed 2026-08-19. The follow contract shipped in 0.0.5 and has
never had a row. One `follow-channel action=follow` closes it.

### 2. F11 — no tool parameter has a description

`Field(description=…)` appears nowhere in `mcp/src`. Every parameter's meaning
lives in the tool description prose instead, which is the budget §3 is trying to
protect. Mechanical, wide, and it touches every tool signature.

### 3. `cancel-job` and `delete-video` — F12's unfinished half

A queued job cannot be cancelled and an indexed video cannot be deleted through
the surface; `docs/takedown.md` deletes with raw SQL against a live database,
including the vec0 trigger and FK-pragma dance. F12's read-only posture shipped
(`public/readonly.py` derives `WRITE_TOOLS` from the annotations); the inverses
did not. Takes the surface to twelve, so it is a contract change, not a patch.

### 4. A DOM-level test harness for the public page

`mcp/tests/test_public.py:2451` says it in its own docstring: *"What the page
does with that — a notice under the rows it already has, rather than a wipe —
needs a DOM-level harness and is not asserted here."* Cross-cutting: a tooling
choice and CI wiring, and the repo's rule against self-hosted runners bounds it.

### 5. F1 — structured OCR, the big one

OCR reaches every query as `group_concat(o.text, ' | ' ORDER BY o.line_no)`, so
a table's cell loses its header and a two-column slide interleaves. The design
bench calls this the single highest-value change on its list. It touches OCR
extraction in `pipeline/`, the `ocr_lines` shape, and every query that flattens
it. Multi-session work.

### 6. The channel-count quirk — needs re-diagnosis before it is a ticket

Carried on a backlog since 2026-08-09 and never described anywhere. The string
"channel-count" appears in no other file, and `library.py:392` /
`queries.py:1487` are unchanged since before it was written. Someone has to
reproduce it before it can be scoped.

### 7. A wrong method on `/api/*` answers 404, not 405

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
| following Q4 `failing` never clears itself | 2026-08-28, migration `0008_follow_retry.sql` — daily retry, bounded at seven, `fail_count` is the receipt |
| following Q7 unfollow refunds the daily budget | 2026-08-28, migration `0007_follow_spend.sql` — the spend outlives its follow, and `unfollow` says so |
| Demo page boots `undefined` when the search bucket is spent | 2026-08-28 — `/api/meta` shares the bucket; a 429 has a JSON body, so `.json()` resolved and the catch never fired |

**The lesson, which is why this file exists:** two of these were shipped the
same day the document listing them as open was written. A backlog inside a
dated record is a snapshot, and a snapshot read a fortnight later is wrong in a
way that costs a day. Open work lives here, in one file, or it does not exist.
