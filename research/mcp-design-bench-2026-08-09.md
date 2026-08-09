# vidtheque — MCP design bench, four Sonnet agents (2026-08-09)

Four subagents on **Sonnet — deliberately a weaker model than the one reading
this** — were each given real questions to answer against the running vidtheque
MCP server and told to log every wrong turn. That is the whole method: where a
Sonnet-grade model stumbles with the tools, the *tool design* is at fault. A
surface that only works when the caller is smart enough to reverse-engineer it
is not a surface, it is a puzzle.

Transcripts (JSONL, in the orchestrating session's subagent directory):

| Agent | Brief | Outcome |
|---|---|---|
| `ae153ff43f6c539d4` | cold-start ergonomics, deliberate misuse | full report; **the :8100 incident** (below) |
| `a2e83fa7891fd512a` | transcript-recall questions | full report, 8 questions answered |
| `a0b88abbfaf4fed3b` | visual / OCR questions | full report, 8 questions answered |
| `a295262c99aad8a1f` | synthesis + negative results | **produced nothing** (below) |

## Two things that are not tool-design findings, recorded so they are not re-derived

**The port.** All four were pointed at `http://127.0.0.1:8180/mcp`. Nothing was
listening there; the server binds `127.0.0.1:8100`. Three agents found the real
port on their own (via `ps`, `/proc/<pid>/net/tcp`, and the server's own
`Uvicorn running on http://127.0.0.1:8100` log line) and carried on; it cost
each of them 3-5 minutes and a handful of calls. It also poisoned the framing
they were given: agent `ae153ff43f6c539d4` was told `index-video` would be
masked on a read-only deployment, and it was not — **it queued a real indexing
job (`job_b38b4971572d`) and added a real `topic:test` tag to the live corpus,
neither of which any tool in the nine-tool surface can undo.** Its tool-usage
observations stand. Its corpus observations are the ones to discount: it was
looking at a corpus that it was itself mutating, concurrently with two other
agents reading it.

That incident is worth one design note rather than an apology: *a surface with
two mutating tools and no inverse for either is a surface you cannot safely hand
to a cheap model.* `tag-video` at least takes a remove; a queued job has no
cancel and an indexed video has no delete. Not v1 scope, but it is the reason
the read-only deployment mask (`public/readonly.py`) exists, and the reason it
has to be the *default* posture for anything an agent is pointed at.

**The agent that produced nothing.** `a295262c99aad8a1f` spent its entire run
waiting for a background readiness check on a server that was already up on a
different port, announcing five times that it would resume once notified. It
never called a tool. The failure is in the harness (a background poll against
the wrong port never completes, and the agent had no timeout of its own), not in
vidtheque — but it is the cheapest reminder available that *an agent blocked on
an unreachable endpoint does not degrade, it stops*, and that the synthesis and
negative-result questions are still unbenched.

---

## A. What worked — receipts, because the wins are load-bearing

These are the parts of the design that a weaker model used correctly without
being taught, and they should not be traded away in some later refactor.

**A1. Typed errors that name the next call.** Three deliberate misuses, three
clean recoveries, graded TEACHES by the agent that ran them:

| Misuse | Response |
|---|---|
| fabricated `video_id` on `video-summary` | `E_UNKNOWN_VIDEO … next: index-video url="…" to add it, or list-videos to browse` |
| `query=` instead of `q=` on `search` | `E_EMPTY_QUERY … next: pass q, or use list-videos` |
| `t_start=` on `get-segment-context` | `E_BAD_PARAM: t or cue_id is required. next: pass t exactly as a result gave it.` |

The error contract (§3.8) is doing exactly what it was designed to do. Keep the
`next:` line mandatory on every error.

**A2. Lenient time parsing.** `t: "05:30"` on `get-segment-context` was parsed to
`t=330.0` and returned the right window — no error, no round trip. The agent
flagged this unprompted as "the best possible outcome". `timeparse.py` earns its
keep.

**A3. Pagination that is copy-pasteable.** `Results: 5/12 (use offset=5 for
more)` → pasting `offset=5` returned five new non-overlapping results and an
updated hint. Tested to exhaustion, no gotchas. This is principle 5 ("the payload
teaches the next call") working at full strength.

**A4. `all` really means all, and says when it doesn't.** RRF fusion across
transcript/OCR/frame legs behaved as specified in every session; the `Legs:`
counts and the `note:` line for a skipped leg were both read and understood.
`max_per_video` was correctly enforced *and* correctly reported.

**A5. The frame leg is genuinely semantic.** A `content_type=frame` query for
"terminal window code editor IDE" surfaced code-bearing slides whose OCR text
contains none of those words — and, more valuable, let the agent conclude
*exhaustively* that the talk contains no terminal screenshot at all (20/20 of a
22-keyframe video), rather than guessing from an absent hit. Negative answers
with a bound on them are rare and worth advertising.

**A6. `frame_id` → URL → real JPEG round-trips cleanly.** Every frame id handed
out resolved to a fetchable 1280x720 JPEG with the correct `mimeType`. The
URL-not-ImageContent decision (§4.6) held up: the agents fetched images
constantly and it never cost them context.

**A7. The guide is good, once found.** "Well-written… the 5-step table, the
two-time-axes warning, and the 'note: means a leg was skipped' line were all
directly useful and matched what I observed in practice." The complaint was
never the content — it was discovery (D1).

**A8. Deep-linked citations, end to end.** Every answer in the two
question-answering runs came back with a `youtu.be/<id>?t=<s>` link that resolves
to the right second. That is the product working.

---

## B. Severity scale

| | Meaning |
|---|---|
| **S1** | Sent a model to a wrong answer, or made a correct answer unreachable. |
| **S2** | Cost extra calls or a fallback outside the tool surface. |
| **S3** | Friction, noise, or a hint that ages badly. |

---

## C. Applied in this commit (prose only)

Everything in this section touches only text the model reads — tool
descriptions, the `vidtheque://guide` and `vidtheque://context` resources, and
`next:` hint strings. `docs/design/tool-surface.md` was updated in the same
commit, per the contract rule.

## D. The findings

### D1 — There is no way to discover a resource URI · S2 · guide gap + payload gap

**What happened.** Three agents found `vidtheque://guide` only by guessing.
One tried `vidtheque://help` first, missed, then guessed `guide`. One found
`vidtheque://corpus` only because a CLI docstring happened to show it as an
example. Guessed URIs (`vidtheque://video/<id>`, `vidtheque://frame/<id>`) don't
404 — they crash the transport (D9). Quote: *"a cold-start agent has no
protocol-native way to discover this URI."*

**Defect.** The design assumed `resources/list`. Real clients frequently don't
surface it, and the harness here didn't expose it at all. Nothing in the *tool*
surface — the part every client shows — named the resources.

**Fix (applied).** `corpus-summary`'s description now lists all three URIs with a
one-line purpose each; it is the documented FIRST call, so the list costs least
there. `vidtheque://context` gained a `"resources"` array. The `vidtheque://corpus`
footer now points at the guide. The guide gained a **Resources** section stating
that exactly three exist and that `vidtheque://video/<id>` and friends do not —
guessing a URI is now explicitly named as the wrong move.

### D2 — `window` was invisible, and the `next:` hint pointed at the slow path · S2 · description + hint

**What happened.** Two independent, compounding failures on the same question
shape ("where does the speaker discuss X").

*(a)* `get-segment-context` accepts `window` (5-300s, default 45). One agent
discovered it **by guessing**, after burning three window-walk calls on a passage
that sat at a window edge: *"Undiscoverable parameters are undiscoverable."* The
description did say `START WITH window=45` — but it was the last line, and the
client showed only the first ~72-100 characters. The input schema carries no
per-parameter descriptions, so `window: number = 45` is all a full client shows
either.

*(b)* After a `search` hit, the footer's `next:` named only `get-segment-context`.
An agent followed it literally and took **four** guesses (t=360 → 666 → 420 →
470) to land on a quote — while `video-summary` on the same video would have
printed `7:27  The "YOLO" security philosophy and extensibility through
TypeScript  ?t=447` in **one** call. The guide ranks `video-summary` third in the
flow; the hint disagreed with the guide, and the hint won.

**Defect.** (a) A parameter that only exists in the tail of a description does
not exist. (b) A `next:` hint tuned to one result is a stronger signal than a
guide the model may not have read — so it has to agree with the guide.

**Fix (applied).** `get-segment-context`'s description states the range inline
and tells the caller to *raise `window`* rather than guess new `t` values; its
own `next:` line now says the same. `search`'s footer `next:` names both
follow-ups, `video-summary` first, with the reason ("fastest way to name the
moment"). The guide gained a sentence saying step 3 is the one people skip.

### D3 — OCR text is a flat join that hides the answer · S1 · payload shape (mitigated in prose)

**What happened.** **Five of eight** visual questions could only be answered by
downloading the JPEG and looking at it. OCR arrives as a `|`-joined,
reading-order string with no binding between a value and its label, and it
mangles exactly the characters that carry the answer:

- CVSS badge `8.8` came back as `Base Score: MIA` in one frame and `8.&` / `K&`
  in a near-duplicate. The agent resolved it by fetching the image, reading
  `8.8 HIGH`, and *recomputing the score from the printed CVSS vector* to be
  sure.
- A Terminal-Bench leaderboard's rank-`1 ●` bullet was read as `10`, and row
  order scrambled between adjacent frames.
- A slide with two quote/attribution pairs put both quotes and both attributions
  in a flat list — an attribution mix-up (Boris Cherny's quote is in *Coyle's*
  talk, not Zechner's) that only the image disambiguates.

**Defect.** The payload presents a lossy, layout-destroyed projection of the
frame as if it were the content, with nothing telling the caller when to distrust
it. Nothing in the surface said "the picture is the un-truncated text."

**Fix (applied, partial).** The guide gained a rule naming the failure mode with
these examples and instructing escalation to the image for tables, code, bullet
lists, quote/attribution pairs and any single number. `get-frames`'s description
now says the same in its USE WHEN — and its old `DO NOT USE … to read text that
is already in the payload (OCR text comes with the search result)` was **deleted**:
it was steering models away from the only path that worked. **Deferred:** the
structured-OCR payload change (F1).

### D4 — The truncation marker names a parameter the tool does not have · S1 · payload shape (mitigated in prose)

**What happened.** `get-frames` returned OCR hard-truncated at ~300 chars with
the shared marker `…[421 chars truncated — pass max_text_chars=0 for full text]…`.
The agent did exactly that. **Byte-identical output.** It tried `max_text_chars:5000`.
Byte-identical again. `get-frames` has no `max_text_chars` parameter at all —
the marker is a module-level constant (`text.TRUNCATION_MARKER`) shared with tools
where the parameter *is* real, and unknown parameters are dropped silently, so
both calls "succeeded". The agent gave up on the tool and fell back to `curl`.

This is the worst finding in the bench: an instruction printed by the server,
followed correctly, that does nothing and reports no failure. It converts a
token-discipline feature into a trap, and it burned two calls before the model
abandoned a tool that was working correctly in every other respect.

**Fix (applied, partial).** `get-frames`'s description now states the 300
chars/frame cap and that it has **no opt-out**, and points at the image as the
full text. The guide says the same. **Deferred:** make the marker per-call-site
so it can only name a parameter the calling tool actually accepts (F2), and give
`get-frames` a real `max_ocr_chars` (F3).

### D5 — `job-status` recommends re-indexing a healthy running job · S3 · hint (deferred, prose counterweight applied)

**What happened.** List-mode printed `next: index-video url="…" force_reindex=true
to retry a failed job` while the only job in the list was **running at 57%**.

**Defect.** A footer hint computed for the list, not for the state of the job in
it. Following it would have destroyed a job that was minutes from succeeding.

**Fix.** The hint lives in `tools/indexing.py`, **owned by another agent this
session — not touched.** As a counterweight, `job-status`'s description now ends:
*"A job still running needs another poll, not a re-index — force_reindex is for a
job that actually reported 'failed'."* The real fix is F4.

### D6 — `limit=500` silently becomes `limit=50` · S1 · payload gap (mitigated in prose)

**What happened.** `search limit=500` returned 50 results with **no error, no
warning, and no note anywhere in the text payload.** The only evidence was
`structured.pagination.limit: 50`, visible by diffing what you asked for against
what you got. No document stated the cap. Quote: *"will make an agent believe it
retrieved everything from a larger corpus when it didn't."*

**Defect.** Server-side clamping is correct (invariant: never prompt-only
limits). Clamping *silently* is not — it violates the project's own "`all` means
all, a narrowing is always announced" rule, one level down.

**Fix (applied, partial).** The guide gained a **Server-side limits** table
(`search limit` 1-50, `max_per_video` 1-20, `list-videos limit` 1-100,
`get-frames limit` 1-12, `window` 5-300) stating that out-of-range values are
clamped silently. `vidtheque://context` gained the same as a `"limits"` object.
`search`'s description says `limit clamps to 50, so page with offset`.
**Deferred:** print a `note:` when a clamp actually binds (F5) — that is the half
that helps a model which never read either resource.

### D7 — Spoken words and on-screen notation never match · S2 · guide gap

**What happened.** `search q="functional property OWL"` returned `transcript 0`
with OCR/frame hits only. The concept is discussed **at length** in speech — the
slides write `hasFather` and `owl:FunctionalProperty`, the speaker says "has
father is a functional property". Same failure shape on `CVE-2026-22812` and on
camelCase identifiers generally. The agent guessed the cause correctly and then
window-walked three times to recover.

**Defect.** A structural property of a multimodal corpus (slides are written,
speech is spoken; they never tokenize the same) that nothing in the surface
mentioned. The `Legs:` line already contains the diagnosis — a caller just has to
know how to read `transcript 0`.

**Fix (applied).** Guide rule: `transcript 0` next to on-screen hits usually
means the phrasing differs, not that the topic is unspoken; re-search the spoken
phrasing, or open `get-segment-context` at the top on-screen hit. **Deferred:** a
`note:` on a zero-hit leg saying it in the payload (F6).

### D8 — `get-segment-context` has no continuation affordance · S2 · hint

**What happened.** Twice, in two different sessions, the target sentence sat at
the edge of the default ±45s window. Neither the payload nor the description
suggested a next step, so both agents invented one — guessing new `t` values in
steps they had to invent too (t=570 → 630 → 700; t=360 → 666 → 420 → 470).

**Defect.** The one drill-down tool whose result can be visibly incomplete had no
"there is more, here is how to get it" line — while `search` has pagination hints,
`get-frames` has a cap note, and every error has a `next:`.

**Fix (applied).** The `next:` line now reads: *"if the line you want runs past
this window, call again with a larger `window=` (up to 300) rather than guessing
a new t; or search …"*. Description says the same. **Deferred:** the payload
already computes which cap bound first (`binding_cap`) — emitting a directed
`note:` only when the window (not the char budget) bound is F7.

### D9 — An unknown resource URI crashes the transport · S2 · code (deferred)

**What happened.** Reading `vidtheque://does-not-exist` (and `vidtheque://video/<id>`,
`vidtheque://frame/<id>`) produced `transport failure: unhandled errors in a
TaskGroup (1 sub-exception)`, exit code 2. Reproduced by two agents, twice each.
**It is byte-identical to the message for a server that is not running** — so an
agent cannot tell "your URI was wrong" from "the server is down", which is
precisely the confusion the port mix-up had already created.

**Defect.** Resource reads bypass the error contract that every tool call
honours. Every tool has a typed `E_*` code and a `next:` line; resources have a
bare asyncio traceback.

**Deferred:** F8 (code path, not prose).

### D10 — Frame-URL `w=` / `q=` are no-ops · S3 · code (deferred)

**What happened.** `get-frames` returns URLs carrying `?w=512&q=75`. Fetching
with and without the params returned **identical bytes** (117,735) at full
1280x720 both times. The `width`/`quality` tool parameters are clamped and
threaded into the URL, and the frame route ignores them.

**Deferred:** F9. `http/` is out of scope for this pass. Until it is fixed the
parameters advertise a control the caller does not have.

### D11 — `max_per_video=3` is noise on a small corpus · S3 · defaults (deferred)

**What happened.** On a two-video corpus, every multi-hit search tripped the cap
and printed the same "3 of N results came from X (max_per_video=3 bound)" line.
The bound is right for a 300-video library and pure noise for a 2-video one.

**Deferred:** F10.

### D12 — Truncated descriptions are the client's fault, and the design's problem anyway · S2 · description shape

**What happened.** Both question-answering agents complained they could only see
the first ~72-100 characters of each tool description, with no way to get the
rest (`--json` didn't help, `COLUMNS=500` didn't help). That is a CLI limitation —
a conformant client shows the whole description and the input schema. But it was
the *only* access path they had, and it is not an exotic failure mode: plenty of
real clients truncate, and every one of them shows line one.

**Defect (real, not the client's).** Nothing guaranteed that line one of each
description is a complete, useful sentence. Some were fine; `job-status` truncated
mid-sentence to *"Check the status of an indexing job started by index-video.
Call with no …"*, which reads as a broken instruction.

**Fix (applied, partial).** All nine first lines are now complete clauses under
80 characters, and the highest-value operational facts (limit clamps, the
`window` range, the escalate-to-image rule) moved out of the tail of the
descriptions into the guide and the `next:` hints, where a truncating client
cannot eat them. **Deferred:** per-parameter descriptions in the input schema
(F11) — the durable fix, since it is what a full client renders.

---

## E. What was changed, exactly

Shipped text (`mcp/`):

- `tools/descriptions.py` — `search` (limit clamp; `video-summary` as the
  follow-up), `corpus-summary` (the three resource URIs), `get-segment-context`
  (`window` range + raise-don't-guess), `get-frames` (escalate to the image;
  300-char cap with no opt-out; deleted the "don't look at the image" rule),
  `job-status` (running ≠ failed). All nine still inside the ≤120-word budget
  (DECISIONS.md); the test asserts it.
- `tools/resources.py` — `GUIDE` gained a Resources section, a Server-side limits
  table, the step-3 nudge, and three rules (spoken-vs-on-screen phrasing;
  OCR-is-a-flat-join → read the image; unknown parameters are dropped silently).
  `context_resource` gained `"resources"` and `"limits"`. The `corpus_resource`
  footer points at the guide.
- `tools/search.py` — the results-footer `next:` names `video-summary` first,
  then `get-segment-context`. Hint string only.
- `tools/segment.py` — the `next:` line teaches `window=` continuation. Hint
  string only.

Contract (`docs/design/tool-surface.md`, same commit, per the ground-truth rule):
§4.1, §4.3, §4.5, §4.6, §4.8 description blocks and the §4.1 / §4.5 return-shape
`next:` lines; §5.1 footer; §5.2 context JSON; §5.3 guide block **re-synced to
the shipped string** — it had drifted, still documenting `offset_start`/`offset_end`
after DECISIONS.md renamed the axis to `t_start`/`t_end`.

Not touched: `tools/indexing.py` (owned by another agent this session),
`public/`, `pipeline/`, `db/`, `http/`.

---

## F. Deferred — actionable list

Ordered by value-per-unit-risk. Every one of these is a code change, not prose.

| # | Change | File | From | Severity |
|---|---|---|---|---|
| **F1** | Structured OCR: preserve reading order per visual block, or emit lines/regions, so a table row's cells stay bound to their header and a quote stays bound to its attribution. The single highest-value change on this list — it is the root cause of 5 of 8 visual questions needing a raw image fetch. | pipeline + payload | D3 | S1 |
| **F2** | Make `TRUNCATION_MARKER` per-call-site. It must never name a parameter the calling tool does not accept. Pass the opt-out text (or `None`) into `middle_truncate` from each caller. | `text.py` + callers | D4 | S1 |
| **F3** | Give `get-frames` a real `max_ocr_chars` (0 = full), so the escalation to an image is a choice and not a workaround. | `tools/frames.py` | D4 | S1 |
| **F4** | Make `job-status`'s list-mode `next:` state-aware: `force_reindex` only when a job actually failed; a running job gets "poll again in ~60s". | `tools/indexing.py` **(owned — hand off)** | D5 | S2 |
| **F5** | Print `note: limit clamped 500 → 50 (server cap)` whenever a clamp binds, on every clamped parameter, in the text payload — not only in `structured`. Same rule as the leg-skip `note:`: a narrowing is announced. | `text.clamp` callers | D6 | S1 |
| **F6** | On a zero-hit leg with hits elsewhere, emit `note: 0 transcript hits — slides write identifiers, speech spells them out; try the spoken phrasing`. | `tools/search.py` | D7 | S2 |
| **F7** | When `binding_cap == "window"` and cues were dropped at an edge, print a directed `note: transcript continues past this window — window=N to include it`. The value is already computed. | `tools/segment.py` | D8 | S2 |
| **F8** | Wrap resource reads so an unknown URI returns a typed MCP error with a `next:` (and the list of the three valid URIs) instead of an unhandled TaskGroup exception. Currently indistinguishable from a dead server. | `app.py` / `server.py` | D9 | S2 |
| **F9** | Honour `w=` / `q=` on the frame route, or stop emitting them. Right now `width`/`quality` are accepted, clamped, threaded into the URL, and ignored. | `http/frames.py` | D10 | S3 |
| **F10** | Corpus-aware `max_per_video` default (e.g. `min(limit, …)` when the corpus is tiny), so a 2-video library stops printing the cap warning on every search. | `tools/search.py` | D11 | S3 |
| **F11** | Per-parameter descriptions in every tool's input schema (pydantic `Field(description=…)`). This is what a conformant client renders, and it is the durable answer to "the description got truncated". | `tools/__init__.py` | D12 | S2 |
| **F12** | An inverse for each mutating tool, or a hard default-on read-only posture for any deployment an agent is pointed at. A queued job has no cancel; an indexed video has no delete. | surface / `public/` | :8100 | S2 |

## G. Still unbenched

The synthesis and negative-result questions never ran (`a295262c99aad8a1f`). The
open questions they were meant to answer: does a model correctly report "the
corpus does not contain this" rather than confabulating from parametric memory;
does it combine evidence across two videos without conflating the speakers (D3's
attribution mix-up says this is a live risk); does `corpus-summary`'s
`data_status` actually stop a model from treating a still-indexing corpus as
complete. Worth re-running against a fixed port, with the fixes above in.
