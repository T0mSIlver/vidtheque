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

### 4. F10 — `max_per_video` is a flat default on a corpus of any size

`search.py:165` defaults `max_per_video=3` whatever the corpus holds, so on a
small corpus the diversity cap is the thing hiding the answer. Needs one cheap
corpus-size read feeding the existing clamp.

### 5. F6 — a zero-hit transcript leg says nothing about why

When the transcript leg returns nothing and another leg has hits, the payload
does not say the likely reason: slides write identifiers, speech spells them
out. The advice exists only in the guide resource. `search.py:605` already has
the analogous title-footing note to copy, and the leg counts are already
computed.

### 6. F11 — no tool parameter has a description

`Field(description=…)` appears nowhere in `mcp/src`. Every parameter's meaning
lives in the tool description prose instead, which is the budget §3 is trying to
protect. Mechanical, wide, and it touches every tool signature.

### 7. `cancel-job` and `delete-video` — F12's unfinished half

A queued job cannot be cancelled and an indexed video cannot be deleted through
the surface; `docs/takedown.md` deletes with raw SQL against a live database,
including the vec0 trigger and FK-pragma dance. F12's read-only posture shipped
(`public/readonly.py` derives `WRITE_TOOLS` from the annotations); the inverses
did not. Takes the surface to twelve, so it is a contract change, not a patch.

### 8. A DOM-level test harness for the public page

`mcp/tests/test_public.py:2451` says it in its own docstring: *"What the page
does with that — a notice under the rows it already has, rather than a wipe —
needs a DOM-level harness and is not asserted here."* Cross-cutting: a tooling
choice and CI wiring, and the repo's rule against self-hosted runners bounds it.

### 9. F1 — structured OCR, the big one

OCR reaches every query as `group_concat(o.text, ' | ' ORDER BY o.line_no)`, so
a table's cell loses its header and a two-column slide interleaves. The design
bench calls this the single highest-value change on its list. It touches OCR
extraction in `pipeline/`, the `ocr_lines` shape, and every query that flattens
it. Multi-session work.

### 10. The channel-count quirk — needs re-diagnosis before it is a ticket

Carried on a backlog since 2026-08-09 and never described anywhere. The string
"channel-count" appears in no other file, and `library.py:392` /
`queries.py:1487` are unchanged since before it was written. Someone has to
reproduce it before it can be scoped.

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

**The lesson, which is why this file exists:** two of these were shipped the
same day the document listing them as open was written. A backlog inside a
dated record is a snapshot, and a snapshot read a fortnight later is wrong in a
way that costs a day. Open work lives here, in one file, or it does not exist.
