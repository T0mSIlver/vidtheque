# vidtheque MCP tool surface — v1

Status: **implemented as of the current commits.** All nine tools and all three
resources ship; `index-video` queues real jobs and the seven-stage pipeline behind
it writes what `search` reads back. This is the surface the `mcp/` server
implements; the HTTP API underneath it is an implementation detail and may carry
extra knobs, but anything reachable from a model is specified here.

Two things to read alongside it. `docs/design/DECISIONS.md` **wins** wherever it
disagrees with this file — most visibly on the intra-video time axis, which is
`t_start`/`t_end` in the shipped tools everywhere this document still writes
`offset_start`/`offset_end`, and on the description budget (§4 note below).
`mcp/README.md` carries the running list of deviations with the reasoning for each;
the ones that change what a *caller* sees are annotated inline below as **Status:**
notes.

Sources for the decisions below: `research/HANDOFF-2026-08-08.md`,
`research/screenpipe-tool-surface-deep-dive.md` (28-tool surface at 20k stars, with
receipts for every token-discipline rule), `research/landscape-survey-video-mcp.md`
(what the competition ships and where they break). Where a rule exists because
screenpipe learned it the hard way, the issue number is cited inline — those are
not preferences, they are paid-for lessons.

Example payloads are illustrative. Video IDs in them are real videos; the numbers
in them are made up.

---

## 1. Design principles

1. **One search tool, many content types.** Not one tool per modality. screenpipe
   has held this since Dec 2024 and never regretted it. Modality is a parameter,
   never a tool name.
2. **`all` means all.** screenpipe's `content_type=all` silently drops two of its
   five types (live bug). Ours queries every leg, every time. When a *filter* makes
   a leg meaningless, the leg is skipped **and the payload says so in a `note:`
   line**. Never a silent narrowing.
3. **Relevance first.** screenpipe orders everything by recency and uses BM25 only
   to pick which 5,000 candidates survive. That is right for a screen timeline and
   fatal for a corpus — the best answer about attention is a 2023 video. `order`
   is explicit, defaults to `relevance`.
4. **Two time axes, never overloaded.** `published_*` selects *which videos*;
   `offset_*` selects *where inside a video*. One `start_time`/`end_time` pair
   cannot mean both.
5. **The payload teaches the next call.** Pagination hints, truncation opt-outs,
   and `next:` lines are printed inside the text the model reads. The model should
   never have to guess a parameter name it wasn't shown.
6. **Every list tool is double-capped** — items *and* characters, whichever binds
   first — and every expensive path (image encoding, OCR joins, related-tag
   co-occurrence) is bounded **independently of `limit`**. screenpipe shipped
   `limit=500&include_frames=true` spawning 500 ffmpeg processes; we don't.
7. **Never an exact total.** `has_more` comes from `LIMIT n+1` and a bounded count
   probe over the *same* CTE. screenpipe's duplicated count query is its stated
   main correctness liability ("filter logic written twice — must agree or
   pagination breaks"). We write the filter once.
8. **Progressive disclosure.** Summary → search → drill-down-by-id. Every
   description carries USE WHEN / DO NOT USE and a starting `limit`.
9. **Ids are evidence, not guesses.** Every drill-down handle in this document is
   returned by some other call. The guide resource says, verbatim, never to
   fabricate one.

---

## 2. The surface at a glance

Nine tools. Kebab-case, following screenpipe (their original Python server shipped
`search-content` in 2024-12 and the name/params are unchanged 20 months later).

| # | Tool | One-line purpose | readOnly | idempotent |
|---|---|---|---|---|
| 1 | `search` | Cross-video search over transcripts, on-screen text and frame imagery, with timestamped deep links. | ✅ | ✅ |
| 2 | `list-videos` | Browse/filter the library without a query — what is in the corpus. | ✅ | ✅ |
| 3 | `corpus-summary` | Pre-aggregated rollup of the whole library: channels, tags, coverage, gaps. | ✅ | ✅ |
| 4 | `video-summary` | Pre-aggregated rollup of one video: chapters, key texts, speakers, tags. | ✅ | ✅ |
| 5 | `get-segment-context` | Full detail around one moment: transcript window, nearby OCR, chapter, frame refs. | ✅ | ✅ |
| 6 | `get-frames` | Fetch keyframe images as authenticated URLs (default) or inline base64. | ✅ | ✅ |
| 7 | `index-video` | Add a video/playlist to the corpus. Async — returns a job id. | ❌ | ✅ |
| 8 | `job-status` | Poll an indexing job. | ✅ | ❌ |
| 9 | `tag-video` | Add/remove namespaced tags on an indexed video. | ❌ | ✅ |

Three resources: `vidtheque://corpus`, `vidtheque://context`, `vidtheque://guide`.

Deliberately **not** in v1: subscriptions, `get-clip`, markdown export, speaker
identity management, per-channel permissions. See §6 for the sketches and why.

---

## 3. Shared conventions

Everything in this section is implemented once, in one place, and referenced by
every tool. Where a tool's parameter table says "see §3.x", the semantics are
identical — no per-tool drift.

### 3.1 Identifiers

| Handle | Shape | Example | Where it comes from |
|---|---|---|---|
| `video_id` | YouTube 11-char id; for future non-YouTube sources, `<source>:<id>` | `kCc8FmEb1nY` | every result line, `list-videos` |
| `cue_id` | integer, stable per transcript cue | `1841` | `search` (`cues 1841-1849`), `get-segment-context` |
| `frame_id` | `<video_id>-<NNNNN>`, keyframe ordinal within the video, URL-safe by construction | `kCc8FmEb1nY-00412` | `search` frame hits, `get-segment-context`, `video-summary` |
| `job_id` | `job_` + 12 hex | `job_7f3a29b1c04d` | `index-video` |

There is deliberately **no query-dependent `segment_id`**. A search segment is a
cluster computed for *that* query (§3.10); handing the model an id that only
exists for one result set invites fabrication and stale drill-downs. Segments are
addressed the durable way: `(video_id, t)`.

`frame_id` embeds the `video_id` so a fabricated one is almost always detectably
wrong (unknown ordinal for a known video → typed `E_UNKNOWN_FRAME` naming the
valid range), and so `/frames/<frame_id>.jpg` is a flat, cache-friendly route.

### 3.2 Time: two axes

**Corpus axis** — `published_after` / `published_before`. Selects videos by upload
date. Accepts, and normalizes server-side:

- ISO 8601 (`2026-03-01`, `2026-03-01T12:00:00Z`)
- relative (`7d ago`, `3w ago`, `6mo ago`, `2y ago`)
- keywords (`now`, `today`, `yesterday`)

Bare dates resolve to start-of-day **UTC**. screenpipe advertised these formats
while its server rejected them, returning silent empty results for months
(issue #3124); they patched it in the MCP client. We normalize in the HTTP layer
so there is exactly one implementation and the raw API behaves the same.

**Intra-video axis** — `offset_start` / `offset_end`. Seconds from the start of a
video. Accepts a number (`723`) or a clock string (`12:03`, `1:12:03`), normalized
to seconds. Only meaningful when the result set is scoped to few videos.

**It is not harmless otherwise** (amended 2026-08-10 — this paragraph used to say
it was). `search {"q":"the","t_start":2019,"t_end":2020}` returned five hits from
seconds 33:39–33:40 of five different talks, tidily formatted and entirely wrong,
with no `note:` — the caller meant the year (terra eval §4.8). A wrong filter
that returns nothing is self-correcting; a wrong filter that returns six
plausible hits is not, and the consumer that hit it marked it the one probe where
recovery was impossible. Note that the *corpus* axis already caught the
mirror-image case: `published_after="2:00"` is a hard `E_BAD_TIME_FORMAT`. So, on
a call that names no `video_id`:

- a **year-shaped** bare number in `t_start`/`t_end` (integer, 1900–2100) is
  `E_BAD_PARAM`, naming what those seconds actually are, the corpus-axis call
  that means the year, and the clock spelling that means the position:
  *"t_start=2019 on the in-video axis means 2019 seconds (33:39) into every
  video … to select videos published in 2019, use `published_after="2019-01-01"
  published_before="2020-01-01"`. To really mean 33:39 inside a video, write
  `t_start="33:39"` — or scope the call with `video_id=`."*
- any other in-video window prints a `note:` naming the axis it used and the
  other one, the §2 "`all` means all" pattern applied to axis confusion.

A call scoped with `video_id=` gets neither: there the axis is unambiguous, and
2019 s into a 50-minute talk is a real position. The clock form is never
second-guessed — what the caller wrote is the only evidence of what they meant,
which is why the guard reads the raw argument rather than the parsed seconds.

Also accepted anywhere a "when did we ingest this" filter makes sense:
`indexed_after` / `indexed_before`, same normalizer.

Unparseable input is a hard, typed error (`E_BAD_TIME_FORMAT`) that echoes the
accepted formats — never a silently ignored filter.

### 3.3 Text truncation

Every field that can carry unbounded text is middle-truncated. Head truncation
loses the payoff of a transcript sentence; both ends carry signal.

- Parameter: `max_text_chars`, default **1000**, `0` = **opt out entirely**.
- Clamp: `0`, or `120..20000`. Values in `1..119` are clamped up to 120 (a
  truncation window smaller than the marker is useless).
- Marker: `…[N chars truncated — pass max_text_chars=0 for full text]…`
- The `0` opt-out is **tested**. screenpipe once shipped a build where `0`
  returned *only* the truncation marker.
- **The OCR leg shows a window, not a truncation.** An OCR hit is a whole
  keyframe (index-schema §2.5), so its text is FTS5's snippet around the matched
  terms — 64 tokens, line separators (` | `) intact — rather than the whole
  slide middle-truncated, which is as likely to cut the match out as to keep it.
  `max_text_chars=0` still means everything: the frame's every line, in reading
  order. `get-frames` on the `frame_id` in the hit shows the frame itself.
- **`search`'s transcript leg truncates around the match, not through it**
  (added 2026-08-09). A clustered segment is a passage, not a sentence, so
  middle-truncation's premise — the signal is at both ends — is wrong for it: at
  `max_text_chars=400` a two-minute cluster came back with the matched phrase
  cut out of the middle, so the result showed neither the words that matched nor
  a timestamp near them (`research/demo-queries-2026-08-09.md` §7.5). The window
  is placed over the **anchor cue** (§3.6) and slid inside the text, so the
  budget is spent on context around a match that is guaranteed to survive. Same
  marker, same budget, same tested `0` opt-out; up to two markers, one per end
  that was cut. This is the transcript-side spelling of what the OCR leg already
  got from `snippet()`. Everything else in the payload stays middle-truncated.

Truncation is the second cap, never the only one. Each tool also caps item counts
(§ per-tool "token discipline" blocks), and the **response as a whole** is capped
at `RESPONSE_MAX_CHARS` (default 60,000, env-tunable). If a response would exceed
it, items are dropped from the tail and the payload ends with:

```
Response truncated at 60000 chars — 4 results dropped. Re-run with a smaller limit or max_text_chars.
```

### 3.4 Pagination and `has_more`

No tool ever runs a second count query. The rule:

1. The ranked candidate CTE is cut at `CANDIDATE_CAP` (default **5000**,
   `ORDER BY rank`). This is the seam where the embedding reranker slots in.
2. The page is fetched with `LIMIT limit + 1` → `has_more`.
3. A **count probe** counts rows in the *same* CTE up to
   `ceiling = offset + max(limit + 30, 500)`. Cheap (bounded), and it cannot
   disagree with the page query because it is the same filter expression. The
   `500` floor is what keeps the printed total from being a function of the page
   the caller asked for: without it, `limit=1` answered `~30+` and `limit=50`
   `~80+` over the same 152-video corpus (terra eval §4.12) — the defect this
   section removed from `search` on 2026-08-09, left live on the one tool that
   keeps the probe. Counting a few hundred rows of an already-filtered CTE is
   the same bounded scan; what is forbidden is the unbounded `COUNT(*)`.

Rendering:

- probe finished below the ceiling → exact: `Results: 10/38 (use offset=10 for more)`
- probe hit the ceiling → approximate: `Results: 10/~40+ (use offset=10 for more)`
- last page: `Results: 8/8 (no more results)`
- **past the last page** (`offset > 0`, nothing shown) → the probe total, never
  the offset, plus where the end is:

  ```
  Videos: 0/181 (past the last page)
  This call has 181 videos; the last page starts at offset=100. next: re-run with offset=100, or offset=0 for the top.
  ```

  `structuredContent.pagination` carries `last_offset` on this payload only —
  the same key, with the same meaning, as `search`'s past-the-end payload
  (rule 4 below). The walked-to-the-end line is `offset + shown`, which is
  right only while `shown > 0`: jumped past the end it collapses onto the
  offset, and `Videos: 0/200` printed beside `approx_total: 181` in the same
  payload (terra eval §9.1) is the "the total moves with the page you asked
  for" shape this section removed from the in-range case. The probe is exact on
  this path by construction — an empty page means the count stopped below its
  `offset + …` ceiling — so no second query is needed to print it.

`~40+` reads as "at least 40, we stopped counting" — which is the truth, unlike an
unbounded `COUNT(*)` that screenpipe still runs (live bug: page capped at 5000
candidates, count uncapped, `Input` type counts with `LIKE '%q%'` full scan).

**`search` does not use the count probe, and that is the fix, not an exception**
(amended 2026-08-09). `search` fuses three legs and then dedups and caps them in
memory, so a probe over one leg's CTE could only ever count the wrong thing.
It counted rows *before* the cross-modal collapse and before `max_per_video` —
rows paging can never deliver — and its ceiling was `offset + limit + 30`, so
the number the guide teaches callers to read scaled with the page they asked
for: `3/~40+` at `limit=3` and `50/~130+` at `limit=50`, same query, same
corpus. The frame leg had no probe at all, so its "total" was the `limit + 1`
fetch: `5/6` for a query with eighteen matching frames. All three are one bug
(`research/demo-queries-2026-08-09.md` §7.8, §9.1.6). `search`'s rule instead:

1. Every leg fetches a **candidate pool** (`CANDIDATE_POOL`, default 400) with
   `LIMIT pool + 1` — the `+1` means "this leg had more to give". That row is a
   sentinel: it sets the flag and is then dropped, so the pool really is `pool`
   deep and the leg counts, `approx_total` and "the first 400 candidates per
   leg" cannot disagree by one.
2. The legs are fused, collapsed and capped, and `approx_total` is the length of
   what is left: post-dedup, post-cap, **page-independent**, and a count of
   results a caller can actually reach by paging. Zero extra queries.
3. `pool_exhausted` (structured content) is what makes it "approx": at least one
   leg filled its pool, so `~380+` rather than `380`. The last page of an
   exhausted pool reads `Results: 30/380 (end of the ranked pool)` — never
   `(no more results)`, which would be §7.7's lie in a new place — plus a
   `note:` saying deeper matches exist and naming the filters that reach them.
4. Paging past the end is a payload, not a silence: `Results: 0/80 (past the
   last page)`, the offset where the last page starts, and a `next:` back to it.
   It used to print `Results: 0/200` — a "total" equal to the offset — and then
   two blank lines, strictly less help than an empty search gets (§7.9). Rule 3
   still applies here, and it is the case that needs it most: past the end of an
   *exhausted* pool the line reads `Results: 0/400 (past the last page of the
   ranked pool)`, carries `pool_exhausted: true` and the narrowing `note:`, and
   says the pool filled rather than implying the corpus ended. Dropping the flag
   on this path made the one response that most invites "there is nothing past
   here" the one that printed a bounded count as a complete one.

`list-videos` keeps the probe: it really does page in SQL.

### 3.5 Output formats

List-shaped tools (`search`, `list-videos`) accept:

| param | values | default | note |
|---|---|---|---|
| `format` | `text` \| `tsv` | `text` | `text` = the model-readable block form shown per tool |
| `fields` | comma-separated column names | per-tool default set | `tsv` only |

`tsv` writes the keys once. screenpipe measured 25 elements at 2,410 tokens as
compact JSON, 3,008 as YAML (+25% — YAML is *worse*), and **644 as columnar TSV
with ids dropped (−73%)**. Their conclusion, verbatim: *"The win is not the syntax
… it is writing the keys once."* We never offer JSON or YAML as a model-facing
format; structured data goes in `structuredContent`, which conformant clients read
without spending prose tokens.

**An unknown `fields` name is `E_BAD_PARAM`, naming the valid columns.** Both
tools validate every name the caller wrote, *before* the ≤ 12 cap is applied, so
a typo in the thirteenth position is rejected rather than sliced away. `search`
used to emit the unknown name as a header with an empty cell under every row
while `order="bogus"` on the same call returned a clean typed error — one tool,
two standards (demo-queries §9.1.9). The valid sets are `search`'s
`TSV_FIELDS` (the keys of a result row) and `list-videos`' `LIST_FIELDS`; a
`fields` list is never silently narrowed, in either direction.

**`fields` shapes the text block only; `structuredContent` always carries the
whole row** — stated here because it was read as an omission (terra eval §4.11:
*"twelve keys per row for a caller who asked for two"*). It is the parameter
table's `tsv only`, and it is deliberate on both sides: the structured payload is
what the dashboard and the public JSON facade read (`public/api.py` says so at
its call site, and passes no `fields` precisely because it wants every key), and
a client that narrows the prose it pays for still gets the full record for free
in a channel it does not have to parse. The honest cost, recorded rather than
denied: a conformant client cannot use `fields` to shrink `structuredContent`,
and the documented-empty `cues`/`frames` columns ride along in it. If that ever
needs to change, the facades must ask for their columns by name first — the
change is theirs, not the tool's.

**An unknown *parameter* name is `E_BAD_PARAM` too** (amended 2026-08-10), and
it names the near miss. `search {"q":"context engineering","tag":"topic:test",
"sort_by":"recency"}` used to return a 200 and neither the filter nor the sort —
one tool, three standards, since the two neighbouring classes above were already
typed errors. The reply now is *"Unknown parameters for search: tag=, sort_by=.
They were rejected, not applied — a filter you think you passed was not."* with
*"did you mean tag= → tags=, sort_by= → order=?"* and the tool's full parameter
list. `t_start`/`t_end` on a tool that has no intra-video axis additionally get
the §3.2 sentence, because that one is an axis confusion rather than a typo.

**Two clauses the near miss carries when it needs them** (amended 2026-08-11,
terra eval §9.3 and §9.4):

- **A near miss whose units differ says so.** `page= → offset=` is the one
  entry in the alias table that hands back a wrong answer confidently: on a
  `limit=50` listing, a client that takes it literally reads rows 2-52
  believing it read page 2 — and gets a 200, which is the failure this error
  exists to end, one layer down. So the hint adds *"offset counts ROWS, not
  pages — page N is `offset=(N-1)×limit`, so page 2 of a limit=50 listing is
  `offset=50`"*. Dropping the alias was the alternative; it would only have
  sent the caller to the generic accepted-names list, which is where they were
  already going wrong.
- **A near miss into an enum carries the enum, when the value does not fit.**
  `kind="speech"` cost two round trips — `content_type=` on the first,
  `content_type must be one of …` on the second — while the server had the
  name, the value *and* the domain at the first call. It now appends
  *"content_type must be one of all, transcript, ocr, frame — 'speech' is not
  one of them."* Only when the value is out of domain: a rename carrying a
  valid value needs no lecture. The domains resolve from the tuples the tools
  validate against (`tools/params.py::enum_domain`), never a second copy —
  a stale copy would teach a domain the validator rejects.

Two details this cost:

- **It cannot be enforced inside a tool.** The SDK validates `tools/call`
  arguments against a pydantic model built from the handler signature, and that
  model ignores extras, so the name is gone before the handler runs. The guard
  wraps `MCPServer.call_tool`, the last place the raw arguments exist
  (`tools/params.py`, wired by `tools.register`).
- **Keys beginning with `_` are left alone.** That namespace is the protocol's
  and the client vendor's (`_meta` and friends); a server that 400s a client's
  own bookkeeping is a worse failure than the silence being fixed here.

`vidtheque://guide` documented the silence as intended (*"`tag=` … is dropped
silently like any other unknown name"*), which made it a contract choice rather
than a bug; the evidence retired the choice. Two independent consumers filed it
in one week — the terra eval §4.5, and a stress-testing agent that reached the
same conclusion without ever seeing this document.

### 3.6 Deep links

**Every timestamped item in every payload carries `https://youtu.be/<id>?t=<int>`.**
Seconds are integers (YouTube ignores fractions); `t` is the clamped floor of the
item's start minus `DEEPLINK_LEAD` (default 2s, so the sentence isn't already
half-spoken when playback starts). This is free precision we get from whisperX
word-level alignment, and it sidesteps screenpipe's live `offset_index` unit bug
(ms vs fps) entirely — we store seconds, not decode-frame indices.

Non-YouTube sources (later) fall back to `link: null` plus `frame_id`/`cue_id`;
the field is always present so the model's rendering doesn't branch.

**"The item's start" is the ANCHOR, not the segment's first second** (amended
2026-08-09). For everything that is a point in time — a keyframe, a cue — those
are the same thing. For a clustered transcript segment they are not: the
semantic leg expands its chunk to every cue inside it, so an island can be two
minutes wide, and the link used to point at second zero of it while the matched
phrase sat 25 seconds later. A two-minute-wide citation is the weakest thing in
the payload of a product whose headline is "timestamped citations"
(`research/demo-queries-2026-08-09.md` §7.5). The anchor is the **best-scoring
matched cue in the island** — score, then leg agreement, then position — and it
is carried in the payload as `match_start` / `match_cue_id` beside the segment's
`start`/`end`, and printed as `2:00–3:59 · match at 2:23`. Two consequences the
contract owes callers:

- **The citation is page-independent.** Cluster membership must not derive from
  the fetched prefix and `k` must not be a function of `offset + limit`, or the
  same hit at the same rank cites a different second at a different `limit` —
  it moved 34 seconds between `limit=3` and `limit=5` (§9.1.2). See §3.4's
  candidate pool, which is the mechanism.
- **The lead is deliberate and is documented where a caller reads it.**
  `start: 311.28` next to `?t=309` is `DEEPLINK_LEAD`, not an inconsistency; two
  independent agents filed it as a bug in one field test (§9.3.1, §9.4), so
  `vidtheque://guide` now says so in a clause.

**The compact `?t=` column is the same number** (amended 2026-08-10).
`video-summary` prints `?t=<int>` beside each chapter, key text and on-screen
highlight rather than repeating the whole URL under the `https://youtu.be/<id>`
it printed four lines above; `get-segment-context` prints it inside each
transcript and on-screen stamp. It is the identical arithmetic — the item's
start minus `DEEPLINK_LEAD` — and there is now exactly one implementation of it,
`text.deeplink_t()`, which `text.deeplink()` also calls. `video-summary`'s three
columns built the bare floor by hand until 2026-08-10, so a rule stated
unconditionally here and repeated in `vidtheque://guide` was honoured by four
tools out of five: an agent that had been told never to invent a timestamp
read the guide, saw the disagreement, and hand-corrected a chapter link to
`?t=398` (terra eval §4.3). A `t=` a payload prints as a *parameter* for the
next call (`get-segment-context t=…`) is never lead-adjusted — it is a position,
not a link.

### 3.7 Tags

Namespaced, lowercase, `<namespace>:<value>`. Reserved namespaces: `topic:`,
`person:`, `project:`, `source:`, `lang:`, `series:`. Anything else is rejected by
`tag-video` with the list of valid namespaces (open namespaces produce
`topic:x` / `Topic:X` / `topics:x` triplicates within a week).

Validation regex: `^[a-z0-9]+:[a-z0-9][a-z0-9._-]{0,63}$`.

`tags` filters use **AND** semantics (comma-separated). `include_related=true` adds
a co-occurrence block computed from tags appearing on the same videos as the
result set — bounded at **30 tags / 800ms**, and it **degrades to omission**, never
to an error or a slow response.

**A corpus with no tags does not advertise tags.** Every self-initiated tag
surface is conditional on a tag being attached to a video: `corpus-summary`'s
`Tags` block, the `tags` column of `vidtheque://corpus`, and
`vidtheque://context`'s `tag_namespaces`. Over the 75-video demo corpus all three
printed an empty feature — `Tags (top 0 of 1)`, a column of 200 empty cells, six
advertised namespaces — and the field test's tourist reasonably concluded the
filter existed and then invented `tag=` for it (demo-queries §9.1.9). Either ship
the feature or stop advertising it; the surfaces come back, unchanged, the moment
one video is tagged.

The tradeoff, deliberately: **a column the caller explicitly named is never
dropped.** `list-videos fields="video_id,tags"` still returns the `tags` column
over an untagged corpus, empty, exactly like the documented `cues`/`frames`
columns (§4.2). Silently dropping a requested column is the same failure as
silently accepting an unknown one, and §3.5 has just forbidden the second. The
rule is therefore: *the server volunteers tags only when there are tags; the
caller may always ask.* `vidtheque://context` splits on the same line — the
namespaces are published when tags are in use (in-use ones first, then the
reserved list) **or** when this deployment registers `tag-video` and the caller
could create one. A read-only demo of an untagged corpus publishes none.

The probe is free: the rollup and the per-row tag map are already fetched by the
surfaces that print them, so "does this corpus have tags" is answered by the data
in hand and costs no extra query.

### 3.8 Error contract

Errors are typed, actionable, and returned with `isError: true` so the model
retries differently instead of treating the error prose as data — screenpipe's
stated rationale, and the reason their 25/25 failing `keyword-search` calls were
noticed at all.

Shape: one text block, plus `structuredContent`:

```
error: E_UNKNOWN_VIDEO
Video "kCc8FmEb1nY" is not in the corpus.
next: list-videos (or the vidtheque://corpus resource) to browse what is indexed — that is the recovery whatever the id turns out to be. index-video url="https://youtu.be/kCc8FmEb1nY" is worth ~2-6 min of GPU ONLY if this id came from outside the corpus and is in front of you — a YouTube URL or page you were given. An id you recalled or assembled is not a video: indexing it fetches nothing, or the wrong thing.
```

**The remedy is shape-checked, and it states its precondition** (amended
2026-08-10). It used to concatenate whatever the caller sent into
`https://youtu.be/<it>` and recommend 2-6 min of GPU on the result; a
stress-testing consumer refused to follow it, unprompted (terra eval §4.6). Two
different failures live under one code, and they get different answers:

- **Input that cannot be a `video_id`** (§3.1's two shapes) — a title, a
  sentence, a 12-character string — is answered with the shape and an example,
  and no `index-video` line: *"video 3" is not a video_id, so nothing in the
  corpus can match it.* A pasted watch/shorts/embed URL is answered with the id
  from inside it, which is the one wrong shape worth translating rather than
  refusing.
- **A well-formed id this corpus does not have** cannot be told from a real one
  by this server — that is what "not in the corpus" means, and the eval's own
  example, `not-a-video`, is 11 legal characters. So the remedy names the
  condition under which the spend is worth it: a *copied* id is worth indexing,
  a *remembered* one is the fabrication the guide's first rule forbids.

**The order of those two clauses is load-bearing** (amended 2026-08-11, terra
eval §9.5). Two stress-testing consumers a week apart graded this "partly" for
the same reason, and neither was reading the shape check: the sentence *led*
with `index-video` on a string the consumer had just invented, so the offer to
spend 2-6 min of GPU arrived before the precondition that disqualifies it.
`list-videos` now leads — it is the recovery that is correct whatever the id
turns out to be — and `index-video` follows a precondition phrased as a test
the caller can apply ("came from outside the corpus and is in front of you"),
not as an act of introspection ("if it came from memory"). No shape check can
close this one: the string is a legal id, and the only evidence that separates
a copied id from an invented one is in the caller, not in the request.

```json
{"code":"E_UNKNOWN_VIDEO","message":"Video \"kCc8FmEb1nY\" is not in the corpus.",
 "next":"index-video url=…","retry_after_s":null}
```

**A `next:` may only name a tool this deployment registers.** The read-only
public deployment masks the write tools entirely — they are absent from
`tools/list`, not present-and-refusing (demo-site.md §1.1) — and the copy did not
follow: `vidtheque://guide` taught `index-video → job-status`, `E_UNKNOWN_VIDEO`
offered to index the id, and `job-status` closed on `force_reindex=true`. Every
dead end in the demo pointed at the one tool the demo does not have
(demo-queries §9.1.8). The mask is therefore resolved at construction and handed
to the tools (`Deps.hidden_tools`, set by `tools.register` from
`public/readonly.py`), and every hint that names a write tool goes through
`Deps.hint(tool, hint, otherwise)`. On a read-only server the remedy degrades to
what the caller *can* do, and says why:

```
error: E_UNKNOWN_VIDEO
Video "kCc8FmEb1nY" is not in the corpus.
next: list-videos to browse what is indexed — this server is read-only and cannot add videos, so a video that is not listed cannot be answered from.
```

The affected copy: the guide's "Adding to the library" line and its
"the answer is index-video" rule, `E_UNKNOWN_VIDEO`, `E_NOT_INDEXED`,
`list-videos`' incomplete-coverage footer, `corpus-summary`'s empty-corpus
`next_best_query`, `search`'s empty-corpus hint, and every `next:` in
`job-status`. There is no second list to keep in sync: the masked set is derived
from the `readOnlyHint` annotations, so a tenth tool is covered the day it is
added.

| Code | HTTP | When | `next:` hint |
|---|---|---|---|
| `E_BAD_TIME_FORMAT` | 400 | unparseable time value | echoes accepted formats with an example |
| `E_BAD_PARAM` | 400 | wrong type / out-of-domain enum / unknown `fields` name / unknown parameter name (§3.5) | names the parameter and its domain, and the near miss for an unknown name |
| `E_EMPTY_QUERY` | 400 | `search` with no `q` and no filters | "pass `q`, or use `list-videos` to browse" |
| `E_ORDER_SCOPE` | 400 | `order=video_time` without a single-video scope | "add `video_id=…`, or use `order=relevance`" |
| `E_UNKNOWN_VIDEO` | 404 | video not in corpus | `index-video` / `list-videos` |
| `E_UNKNOWN_FRAME` | 404 | bad `frame_id` | valid ordinal range for that video |
| `E_UNKNOWN_JOB` | 404 | bad `job_id` | "call `job-status` with no id for recent jobs" |
| `E_NOT_INDEXED` | 409 | video row exists, pipeline never ran | `index-video force_reindex=true` |
| `E_INDEXING` | 409 | video is mid-pipeline; partial data | `job-status job_id=…`, plus what *is* queryable now |
| `E_FEATURE_DISABLED` | 409 | filter needs a disabled feature (e.g. `speaker` with diarization off) | "omit `speaker=`" |
| `E_TIMEOUT` | 408 | 30s query budget exhausted | "narrow the range: add `channel=`, `video_id=`, or a tighter `published_after`" |
| `E_BUSY` | 503 | admission control full | `retry_after_s: 1`, and *only* that — "retry the IDENTICAL call in 1s — do not reformulate the query, a different one is refused exactly as fast; the limit is on concurrent searches, not on what a query costs". The hint used to offer "or narrow the query so it costs less", which cannot work (the semaphore is taken before the query is built) and which a consumer acted on instead of waiting (terra eval §4.9). With that removed, 2 of 3 consumers repeated the identical call and the third still re-worded twice, reading "retry the same call" as advice — so the wrong move is now named as an instruction (§9.7). If a third round still shows reformulation, the answer is a bigger semaphore, not more prose |
| `E_RATE_LIMIT` | 429 | per-client budget | `retry_after_s` |
| `E_TOO_LARGE` | 413 | request would exceed inline image budget | "use `return=url`, or lower `limit`" |
| `E_UNSUPPORTED_SOURCE` | 422 | `index-video` URL yt-dlp can't handle | lists supported sources |
| `E_INTERNAL` | 500 | anything else | "retry once; if it persists the server log has the trace id" (trace id included) |

408 + 503 + **real cancellation** ship together. screenpipe's outage (#4474) was a
30s timeout with *no* cancellation: the query kept running 153s holding its pooled
connection, concurrency drained the pool, and the whole app 500'd for minutes. The
fix is a SQLite progress handler firing every 1,000 VM ops against a 30s budget so
a dropped future actually interrupts `sqlite3_step`. Two admission layers: at most
2 concurrent uncached searches (`try_acquire` → immediate 503, **not** a queue),
and a heavier semaphore inside the frame/vector legs.

### 3.9 Annotations

Every tool carries MCP `annotations`: `title`, `readOnlyHint`, `idempotentHint`,
`openWorldHint`. `openWorldHint` is `false` for every query tool (the corpus is a
closed local index) and `true` for `index-video` (it fetches from the internet).

We use `idempotentHint` as a **cache-safety signal**: `true` when the same
arguments return the same answer until the corpus changes. That makes `job-status`
the one read tool with `idempotentHint: false`.

### 3.10 Search-side algorithms (shared by `search` and, in part, the summaries)

**Adjacent-cue clustering.** Transcript cues are ~1–3 seconds; ten of them are one
sentence. Emitting them as ten results is exactly the failure mode behind
screenpipe #2285 (81 near-duplicate rows in a 15-minute window, the top five
byte-identical). Matched cues in the same video within `cluster_gap` (default
**8s**) collapse into one segment; the segment text is the *contiguous* cue run
from the first to the last matched cue, so it reads as prose rather than keyword
confetti. Bounded by `cluster_max_seconds` (default 120) and by `max_text_chars`.
Result shape: `{video_id, start, end, match_start, match_cue_id, text, cue_ids[]}`
— the span, and the anchor inside it that the deep link points at (§3.6).

**`cluster_gap=0` is raw cues, and raw cues need per-cue evidence.** The
semantic leg's unit is a *chunk*; expanding one to its cues is how a citation
gets cue granularity, and clustering collapses the expansion again. With
clustering off, the expansion IS the payload — and it arrived as forty cues
ranked by position, so `dirty secret it does not exist` returned three
consecutive cues from one talk containing none of the query's words, rank 2
being "Thank you, Sid, for speaking" (`research/demo-queries-2026-08-09.md`
§7.6). Two rules fix it, and the first is not conditional on `cluster_gap`:

- **The vector leg's RRF rank is the rank of the CHUNK**, shared by every cue
  expanded out of it — not a `ROW_NUMBER()` over the expanded cues, which made
  the rank a function of cue density (a 40-cue chunk pushed the next-best chunk
  to rank 41, `1/101` instead of `1/62`, so a chattier caption track won).
- **At `cluster_gap=0` the semantic leg cites its chunk once**, at the chunk's
  anchor cue, plus any cue the lexical leg independently matched — that one has
  evidence of its own. One chunk, one citation.

**Per-video diversity cap.** `ROW_NUMBER() OVER (PARTITION BY video_id ORDER BY
rank) <= max_per_video`, default **3**, applied *before* the page slice.
screenpipe's comment on the equivalent app-level cap: *"without this, a single
dominant app can fill the entire result set."* One 3-hour lecture would otherwise
bury fifty videos. When the cap actually bit, the payload footer says so and names
the parameter to widen.

**The cap BACKFILLS; it never shortens the page.** It runs over the whole
candidate pool (§3.4), so a page reduced by the cap pulls the next candidates in
behind it, and `has_more` is computed after the cap over the same list. It used
to be applied to a page that had already been fetched at `limit` rows:
`limit=6, max_per_video=1` returned three results and asserted *"no more
results"* while the same query without the cap proved thirty-plus candidates
existed (`research/demo-queries-2026-08-09.md` §7.7). A cap that silently
shortens the page is a bug; a cap that shortens the page *and then says the
corpus is exhausted* is the same bug telling the caller to stop looking.

**One frame, one result.** A keyframe can be found by the OCR leg (its text) and
by the frame leg (its picture) at once, and a slide held across a shot boundary
is indexed as several keyframes with one `phash`. Either way the payload showed
the same picture in two or three slots, identical down to the timestamp string,
each one spending a slot of `max_per_video` (§7.4, the `Andon` reproducer). Both
collapse, on the identity the keyframe stage already computed: `phash` within a
leg (in SQL, so the pool is not spent on duplicates), `frame_id` across the two
legs (in the fusion step, where the RRF contributions add — two channels
agreeing is corroboration, and the provenance becomes `[ocr+frame]`). Keyframes
with `dup_of` set never reach either leg. The frame collapse runs *after* the
OCR-vs-transcript one, so a slide already absorbed into the narration it repeats
claims its frame id too and the picture cannot come back a second time as
`[frame]`; that is the `[transcript+ocr+frame]` provenance.

**OCR-vs-transcript dedup.** A slide usually says what the speaker is saying. Two
hits, same second, same claim. Within one video, an OCR hit whose frame timestamp
falls inside (or within 5s of) a transcript segment's span, and whose normalized
text (casefold, strip punctuation/runs of whitespace) is contained in the other or
shares ≥0.8 trigram Jaccard, collapses into one result. **The longer text wins** —
screenpipe's `deduplicate_ocr_and_ui` upgrades OCR text when the accessibility
text is longer, same principle. Provenance becomes `[transcript+ocr]` so the model
still knows both channels fired without a second query.

**The OCR leg matches whole frames.** On-screen text is indexed one document per
keyframe, not per OCR line (index-schema §2.5), because the text legs AND their
terms and a slide is a dozen lines: `vector retrieval` has to be able to match
the slide that titles "Vector databases" and bullets "…for retrieval augmented
generation". Consequences a caller can see: a term repeated down a slide is one
result rather than five at the same timestamp; a phrase wrapped across two lines
is findable; `min_chars`/`max_chars` measure the whole frame; and the text shown
is the matched window of it (§3.3).

**Cross-modal fusion.** BM25 scores and cosine similarities are not comparable, so
`content_type=all` fuses the three legs with **Reciprocal Rank Fusion**
(`score = Σ 1/(k + rank)`, k=60) rather than a hand-tuned weighted sum. RRF needs
no per-leg calibration and survives a leg returning nothing. The per-leg candidate
lists are each cut at `CANDIDATE_CAP` before fusion, and each contributes
`CANDIDATE_POOL` rows to it — **not `offset + limit`**. Fusion bonuses used to
fire only when both legs' copies of a hit landed inside the fetched prefix, so
the single largest score differentiator in the payload appeared and disappeared
with page size: `reward hacking` had a different rank 1 at `limit=3` and at
`limit=50`, and a visitor clicking "show more" got a different list rather than
more of the same one (`research/demo-queries-2026-08-09.md` §9.1.3). Ranking is
a function of the query; the page is a slice of it.

**The tie-break, in order** (added 2026-08-09). RRF ties are not an edge case:
every leg's and every sub-ranker's rank 1 scores exactly `1/(60+1)`, so the top
of a fused payload is usually a tie. The tie-break used to be `_sort_key`, whose
first element is `public_id` — alphabetically by video id, systematically
favouring ids that start with `-` or a digit — and an exact, unique string match
lost to a fuzzy neighbour that did not contain it at all (§7.1, the field test's
highest-impact finding). Relevance now decides, and identity only breaks what
relevance cannot:

1. **fused score** — the RRF sum;
2. **exact phrase** — the whole query occurs, as a phrase, in the hit's text;
3. **term coverage** — the share of the query's terms present as whole tokens;
4. **leg agreement** — how many rankers found this hit (FTS + vector, or two
   modalities collapsing into one result);
5. then `(public_id, start, source, frame_id, text[:64])`, which is arbitrary
   and exists only to make the order **total** — without it, page-boundary
   membership can differ between two identical calls, which is the guarantee
   `offset` depends on.

2 and 3 compare *normalized* text (casefold, punctuation to spaces), so
`CVE-2026-22812` matches `cve 2026 22812`. They are computed once over the
candidate pool, which is bounded independently of `limit`. Non-relevance
orderings keep their own first key (publish date, video time) and then use this
same list.

**Provenance prefixes** (so the model never needs a second, narrower query just to
learn where a hit came from): `[transcript]`, `[ocr]`, `[frame]`, `[transcript+ocr]`,
`[ocr+frame]`, `[transcript+ocr+frame]`, `[description]`.

---

## 4. Tools

**Status — "ships verbatim" no longer holds, by decision.** Every description block
below is headed *"ships verbatim"*, and they run 120–190 words each because each one
restates the same shared rules: the two time axes, case-insensitive substring
matching, ordering, never fabricate an id. DECISIONS.md caps tool descriptions at
**~120 words** and puts the shared rules in the `vidtheque://guide` resource (§5.3)
instead of repeating them nine times; DECISIONS wins, and a test asserts the budget.

So the blocks below are the **specification of what each description must convey** —
purpose, USE WHEN, DO NOT USE, the starting `limit` — not the literal shipped
strings. The shipped ones say the tool-specific part and point at the guide for the
rest. The blocks are left as written: they are still where the wording was argued
out, and trimming them here would lose the argument without gaining a contract.

**One shape rule, added 2026-08-09: the first line of every shipped description
is a complete sentence under 80 characters.** Plenty of clients render only
`description.splitlines()[0]`, or the first ~100 characters — two bench agents
had no access to anything else, and `job-status` was truncating to *"Check the
status of an indexing job started by index-video. Call with no …"*, which reads
as a broken instruction rather than a short one. All nine were rewrapped so the
truncation point falls at a sentence boundary. The corollary is the reason the
`next:` hints and the guide got the operational detail in the same pass: anything
load-bearing that sits in the tail of a description is, for some real fraction of
clients, not shipped at all
(`research/mcp-design-bench-2026-08-09.md` §D12).

**Purpose:** cross-video search over transcripts, on-screen text and frame imagery,
returning timestamped deep links.

**Description (ships verbatim):**

```
Search the indexed video corpus. Covers spoken content (transcripts), on-screen
text (OCR of keyframes), and frame imagery (visual similarity), with a timestamped
youtu.be deep link on every result.

USE WHEN: you need specific words, claims, numbers, code, or visuals from videos
the user has indexed — "where does he explain KV caching", "which video shows the
nvidia-smi output", "find the slide with the loss curve".

DO NOT USE: to find out what is in the corpus at all (use corpus-summary, or the
vidtheque://corpus resource); to understand one video end to end (use
video-summary); to read the full transcript around a moment you already found (use
get-segment-context with the video_id and t from a result here). Do not use this
to search the public YouTube catalogue — it only searches videos already indexed.

START WITH limit=5 and add filters before raising it. limit clamps to 50
server-side; page with offset rather than asking for more. content_type=all means
all three channels, always. Ordering defaults to relevance, not recency — pass
order=recency explicitly if the user asked for "latest". After a hit,
video-summary's chapter list names the moment faster than probing
get-segment-context.

Two independent time axes, do not confuse them: published_after/published_before
select WHICH VIDEOS by upload date; offset_start/offset_end select WHERE INSIDE a
video, in seconds. channel and video_title matching is case-insensitive and
substring-based.
```

**Parameters:**

| name | type | default | server-side constraint | notes |
|---|---|---|---|---|
| `q` | string | — | ≤ 512 chars | FTS5 query for text legs; encoded by the text encoder for the frame leg. Required unless ≥1 filter is set. Bare `*`/empty with filters = browse mode (skips FTS entirely, fast path). |
| `content_type` | enum `all\|transcript\|ocr\|frame` | `all` | — | `all` queries **all three**. Never silently narrowed; leg skips are printed as `note:`. |
| `limit` | int | `10` | **clamped 1..50** | Not prompt-only. screenpipe's "max 20" is advisory with no clamp (live bug). |
| `offset` | int | `0` | clamped 0..10000 | |
| `order` | enum `relevance\|recency\|video_time` | `relevance` | — | `recency` = video publish date desc, ties by relevance. `video_time` requires a single-video scope → else `E_ORDER_SCOPE`. |
| `video_id` | string \| string[] | — | ≤ 20 ids | Scope to specific videos. |
| `channel` | string | — | ≤ 128 chars | **Case-insensitive substring.** (screenpipe's `app_name` is case-sensitive exact — a footgun they repeat in every description.) |
| `video_title` | string | — | ≤ 256 chars | Case-insensitive substring. |
| `tags` | string | — | ≤ 10 tags | Comma-separated, AND semantics, namespaced (§3.7). |
| `include_related` | bool | `false` | 30 tags / 800ms, degrades to omission | Co-occurring-tag block. |
| `published_after` / `published_before` | string | — | §3.2 normalizer | Corpus axis. |
| `offset_start` / `offset_end` | number \| string | — | §3.2; seconds ≥ 0 | Intra-video axis. |
| `speaker` | string | — | ≤ 128 chars | Case-insensitive partial. Transcript leg only → other legs skipped with a `note:`. `E_FEATURE_DISABLED` if diarization is off corpus-wide. |
| `min_chars` / `max_chars` | int | — | 0..100000 | Filter by matched-segment text length. Text legs only. On the OCR leg the segment is the **frame** — all of its lines (index-schema §2.5) — so "short" means a sparse slide, not a short line. |
| `max_per_video` | int | `3` | clamped 1..20 | Diversity cap (§3.10). |
| `cluster_gap` | number | `8` | clamped 0..60 | Seconds; `0` disables clustering (returns raw cues — and the semantic leg then cites each chunk once, §3.10). |
| `max_text_chars` | int | `1000` | `0` or 120..20000 | §3.3. |
| `format` | enum `text\|tsv` | `text` | — | §3.5. |
| `fields` | string | `video_id,start,text,link,source` | ≤ 12 fields | `tsv` only. |

**Return shape.** One `text` block; `structuredContent` mirrors it as
`{results: [...], pagination: {limit, offset, has_more, approx_total,
pool_exhausted, last_offset?}, leg_counts: {...}, notes: [...],
related_tags?: {...}}`. Each result
carries `{source, video_id, title, channel, start, end, match_start,
match_cue_id, text, link, cue_ids, frame_id, score}` — `match_start` is the
anchor the `link` points at (§3.6), equal to `start` for the point-in-time legs;
`last_offset` appears only on the past-the-end payload (§3.4). **No image blocks
— ever.** Frame hits carry `frame_id`; images come from `get-frames` (§4.6),
which is the whole point of that tool.

```
Results: 10/~40+ (use offset=10 for more)
Query: "kv cache" · content_type=all · order=relevance · max_per_video=3
Legs: transcript 24 segments (fts 9 cues · vec 15/800 chunks) · ocr 9 · frame 7 (vec 11/800) (fused, RRF k=60; 5000-candidate cap not reached)

[transcript] Let's build GPT: from scratch — Andrej Karpathy (kCc8FmEb1nY)
  1:12:03–1:12:47 · match at 1:12:21 · https://youtu.be/kCc8FmEb1nY?t=4339
  …[612 chars truncated — pass max_text_chars=0 for full text]… so the reason we
  cache the keys and the values is that at every new token you would otherwise
  recompute attention over the entire prefix, which is quadratic. the cache makes
  it linear in the number of new tokens, and the price you pay is memory — which
  is why long-context inference is a memory-bandwidth problem, not compute.
  cues 1841-1849 · score 0.0312

[transcript+ocr] Fast LLM inference from scratch — Kevin Chen (BpXHvyqZ0Fk)
  22:41–23:10 · https://youtu.be/BpXHvyqZ0Fk?t=1359
  KV cache size = 2 · n_layers · n_heads · d_head · seq_len · dtype_bytes
  (slide text and narration matched within 3s; kept the longer text: ocr)
  cues 604-609 · frame BpXHvyqZ0Fk-00188 · score 0.0298

[frame] Visualizing transformers — 3Blue1Brown (eMlx5fFNoYc)
  09:14 · https://youtu.be/eMlx5fFNoYc?t=552
  visual match, no text hit · frame eMlx5fFNoYc-00073
  → get-frames frame_ids=["eMlx5fFNoYc-00073"] to look at it
  score 0.0241

[ocr] Making LLMs go brrr — GPU MODE (zduSFxRajkE)
  41:02 · https://youtu.be/zduSFxRajkE?t=2460
  paged kv cache · block table · 4% fragmentation vs 60-80% baseline
  frame zduSFxRajkE-00390 · score 0.0195

--- (6 more results elided in this example) ---

3 of 10 results came from kCc8FmEb1nY (max_per_video=3 bound). Raise max_per_video for more from it.
Text truncated at 1000 chars, around the matched passage — pass max_text_chars=0 for full text.
next: video-summary video_id="kCc8FmEb1nY" for the chapter list (fastest way to name the moment), or get-segment-context video_id="kCc8FmEb1nY" t=4321 for the full surrounding transcript.
```

**The `next:` line names two follow-ups as of 2026-08-09.** It used to name only
`get-segment-context`, and that is what agents did — the bench has one walking
four `get-segment-context` windows to find a passage whose chapter title
(`7:27 The "YOLO" security philosophy…`) `video-summary` would have printed in
one call, and another burning three windows on a passage sitting at a window edge
(`research/mcp-design-bench-2026-08-09.md` §D2, §D8). The guide already ranked
`video-summary` third in the flow; the hint now agrees with the guide.

A leg-skip note looks like this, on the line under `Legs:`:

```
note: speaker= applies to the transcript leg only — ocr and frame legs were not queried for this call.
```

**A phrase that exists only in a TITLE gets a note too** (added 2026-08-11,
terra eval §9.8). Titles, descriptions and channel names live in `videos_fts`,
which no `search` leg reads, so a query whose words are only in a title comes
back `fts 0` — truthfully — and the semantic leg ranks alone. Live, over a
corpus containing a talk *named* "…without the on-call tax", `q="on-call tax"`
put an unrelated talk at rank 1 and the eponymous one at ranks 2-4. **The one
place `search` cannot find a phrase is the title bar.** When the transcript FTS
sub-leg is empty and a title in scope does match, the payload says so and names
up to three:

```
note: no transcript or on-screen line contains these words (fts 0), but 1 video title does: "Always-on agents run production without the on-call tax" (vSx5IULvBns). Titles are not in the searched index, so a title match cannot rank a moment and did not rank one here. search video_title="…" filters by title; video-summary video_id="vSx5IULvBns" opens the first one.
```

The alternative — a title sub-leg or a title boost in the fusion — is
**deliberately not taken in v1**, for two reasons and one condition:

- A `search` result is a **moment with a receipt** (§3.6). A title matches the
  *video*, not a position in it, so promoting one to a result means either
  inventing `t=0` — a fabricated moment, which §1's first rule forbids — or a
  result with no deep link, which nothing downstream is shaped to read.
- A boost is a **tuned constant over a scored ranking**, the same class of
  change as the vec floors: it belongs to a bench run with a before/after per
  encoder (`research/vec-floor-calibration-2026-08-10.md`), not to a hint.
  `index-schema.md`'s FTS notes already sketch the mechanism
  (`videos_fts … rank MATCH 'bm25(10.0, 1.0, 3.0)'` weights title over
  description over channel); what is missing is the measurement, not the SQL.
- What is *not* deferrable is the silence: §2's *`all` means all* says a leg
  that cannot answer prints a `note:` and never narrows quietly, and a modality
  the legs structurally do not cover is the same promise. The note carries the
  receipt the ranking cannot.

Cost: one bounded FTS lookup (`LIMIT 3`, column-filtered to `title`), asked
only on the `fts 0` branch — where by definition there is nothing else to
spend on. It cannot be paid on a query that already has lexical footing.

**The `Legs:` line prints sub-legs, and the semantic ones print what they kept
(2026-08-10; units and the unbound band, 2026-08-11).** The leading number per
leg is the fused contribution to the ranking, in *segments*. The parenthetical
behind it is the *candidate* count per sub-leg, in each sub-leg's own unit —
cues for `fts`, chunks for the transcript `vec`, frames for the frame leg — and
`a/b` is kept-of-considered.

**The units are printed, and `a/b` is printed always.** Three numbers in three
units, none of them labelled, invited exactly one reading — that they add up —
and the guide's own illustrative example (`transcript 24 (fts 9 · vec 15/800)`)
happened to. No live payload does: `transcript 130 (fts 369 · vec 123/800)` is
a normal line, and it read as an arithmetic bug or, worse, as "369 talks say
this" (terra eval §9.2). And `a/b` used to be suppressed when the relevance
band kept everything, on the reasoning that two identical numbers are noise —
which made `vec 800` (the pool was not narrowed at all: read the scores, not
the count) print more tidily than `vec 11/800` (the band bit hard and the
survivors are few). The one case the caller most needs to notice looked like
the cleanest (§9.6). Both fixes are text on one line of one payload; neither
changes a count.

It exists because the guide tells callers to read this line, and a *merged*
count cannot carry the rule it teaches: a nearest-neighbour sub-leg always
returns its `k`, so `transcript` never reads `0` and "nine talks say this" is
indistinguishable from "the KNN returned its k". A terra consumer asked for a
complete inventory read `transcript 400` and shipped "the server returned 143
distinct talks" about `eval` as a fact
(`research/mcp-eval-terra-2026-08-10.md` §4.2). `fts 0` says it in one token.
`structuredContent.leg_counts` carries the same keys (`transcript`, `ocr`,
`frame`, `transcript_fts`, `transcript_vec`, `transcript_vec_knn`, `frame_vec`,
`frame_knn`) for clients that do not parse prose.

**The relevance floor on the two vector legs is two cuts, and both are
server-side.** A hit is dropped before fusion when its cosine distance exceeds
either the absolute ceiling (`VIDTHEQUE_VEC_MAX_DISTANCE` 0.55 /
`VIDTHEQUE_FRAME_MAX_DISTANCE` 0.65) *or* `best_hit_distance + margin`
(`VIDTHEQUE_VEC_MAX_MARGIN` 0.20 / `VIDTHEQUE_FRAME_MAX_MARGIN` 0.15, clamped
`0..2` on the way in). Neither is a tool parameter; there is no way to widen
either from a prompt.

They answer different questions, and the second one is why the ceiling alone is
not enough:

- **The ceiling separates a real query from a junk one.** Calibrated 2026-08-10
  on the repaired embedding space over 154 videos, 12 real / 10 junk queries:
  text best-hit distance is **real 0.220-0.459 vs junk 0.579-0.665**, an empty
  corridor 0.12 wide, and 0.55 sits inside it — all ten junk queries return
  zero chunks, no real query loses a hit. The frame leg only partly separates
  (real 0.382-0.623, junk 0.550-0.749), so 0.65 sits above the whole real range
  and lets a little junk through rather than cutting real recall.
- **The band bounds a real query's fan-out.** A junk query's `k` nearest are
  flat (best 0.579, 800th 0.771), so a band around its own best hit keeps all
  800 of them — the band cannot do the ceiling's job. A real query's distances
  rise steeply from a genuinely near best hit, and the margins are calibrated as
  "about the top 50 chunks / top 20-50 frames": the real 50th-nearest sits
  0.18-0.22 (text) and 0.09-0.18 (frame) from the best hit.

Absolute distances do not survive a change of embedder, so the ceilings are a
**bench item on every encoder swap** — the procedure and both measurements are in
`research/vec-floor-calibration-2026-08-10.md`; the band needs no re-measurement.
Both defaults shipped open at `1.0` until 2026-08-10, for a reason worth keeping
in view: the encoder had never once loaded its weights
(`research/embedding-random-init-2026-08-10.md`), and in a randomly-initialised
space real and junk best-hit distances overlap completely, so *no* absolute
ceiling was settable and every attempt to set one would have deleted real
recall. A ceiling is only as good as its last measurement.

Without it, §1.3's *relevance first* was not true of the shipped server: with
`k=800` and a ceiling of `1.0`, `q="turbopuffer"` answered `Results: 5/121` over
a 154-video corpus and a talk about neither turbopuffer nor the query's topic
took rank 1 — every score an RRF rank-1 tie
(`research/mcp-eval-terra-2026-08-10.md` §4.1). The cut is announced, never
silent: that is what `vec 15/800` on the `Legs:` line is for.

**Status — the frame leg, and how it degrades.** The frame leg needs the query in
the *frame* embedding space, which means the query text through the frame model
itself (the shipped unified embedder under its frame-retrieval instruction, or
SigLIP 2's text tower with the two-model configuration — see index-schema §4.5).
That is a dedicated worker endpoint, `POST /v1/embeddings/frame-query` — a sibling
path rather than a `space=` field on `/v1/embeddings`, because an unknown *field* is
ignored by a hosted OpenAI-compatible worker (you get text-space vectors at some
other width and compare them against the frame index) where an unknown *path* 404s.
Two consequences a caller can observe:

- A worker that predates the endpoint answers 404. That is a property of the worker
  build, not weather, so the result is **latched for the process lifetime**: one
  `note:` naming the missing text→frame encoder, the frame leg skipped, and no
  further calls until the server restarts. Same latch if the vectors come back at
  the wrong dimension. A worker that gains the encoder is picked up on the next
  restart, not mid-run.
- A **transient** failure — worker unreachable, timeout — prints its own `note:` and
  is deliberately *not* cached, so the leg recovers on the next search by itself.

Either way `content_type=all` still queries transcript and OCR and still says in the
payload that the frame leg did not run. `all` means all; a skipped leg is announced,
never silently dropped.

**A third case, and it is not a skip: a leg that ran over an index still being
filled.** Changing the embedding model rebuilds the vector tables and marks the
embed stages stale (index-schema §1.10), so for the length of one backfill a
vector leg searches fewer videos than the corpus contains. It does not fail and
it does not skip — nearest-neighbour search simply returns less — so it prints a
`note:` of its own, once per affected leg:

```
note: 75 videos in scope are waiting to be re-embedded after an embedding-model
change, so the frame leg's semantic half searched only the videos that are
current — keyword matching covered the rest. index-video force_reindex=false
url="…" (or the overnight batch) backfills them; no download or transcription is
involved.
```

The leg is deliberately **not** disabled for the window. Disabling it would be
worse than useless: the indexing stages that produce the vectors skip themselves
whenever the vector legs are off, so switching them off latches the backfill off
too. Honesty here is a `note:`, not a smaller answer. `corpus-summary` reports
the same window as `data_status: degraded` (§4.3).

Empty result set (no bare "no results"; screenpipe's `guidance.next_best_query`
principle applied to search):

```
Results: 0/0
Query: "flash attention 4" · content_type=all · published_after=2026-07-01

data_status: ok (corpus has 312 videos, newest published 2026-08-02, index fresh)
The corpus does contain 41 hits for "flash attention" with no date filter.
next: retry with published_after omitted, or search q="flash attention" limit=5.
```

**`data_status` here is the activity axis only, and it is derived, not asserted.**
"index fresh" was a hard-coded string: it printed while five jobs sat in the
queue, and was the third of three contradicting answers about the same queue in
one session (demo-queries §9.1.4). The word and the clause now come from the one
derivation `corpus-summary` and `vidtheque://context` read
(`tools/corpus_state.py`), narrowed to `ok` | `empty` | `indexing` | `deferred` —
this line answers "is the index settled?", so the coverage words (`partial`,
`degraded`) stay with `corpus-summary`, which prints both axes. What the three
surfaces may never again disagree about is whether anything is being indexed.

**The reason line is derived from the legs that ran, and the page is always
echoed.** It was the constant *"Every leg was queried and none of them
matched."* — false whenever the caller pinned `content_type` to one leg, and at
`content_type=all` it could contradict a `note:` four lines above it in the same
payload saying the semantic legs had been gated off (demo-queries §7.10,
§9.1.5). `all` means all; claiming `all` when one leg ran is that invariant
inverted. It now names the legs that ran (*"The transcript and ocr legs were
queried and nothing matched"* / *"All three legs were queried…"*) **and why the
others sat out** — a pinned `content_type` is the caller's own doing and has no
`note:` to read, everything else has one, and the two want different fixes. The
same payload also echoes the caller's `limit`/`offset`: the corpus-filter early
return used to default them to `0`, so an unmatched `channel=` answered with
`pagination.limit: 0` while an unmatched *query* echoed the real limit (§9.1.9).

**`fields` is validated against the columns this tool can emit** (§3.5), before
the search runs: an unknown name is `E_BAD_PARAM` listing the valid fields, not a
header with a blank cell under every row.

**Token discipline.**
- `limit` clamped 1..50 server-side; `max_text_chars` default 1000 with a tested
  `0` opt-out; whole-response cap 60,000 chars (§3.3).
- Double cap: at most `limit` items **and** at most `limit × (max_text_chars + 220)`
  chars of body before the response cap trims the tail.
- Bounded independently of `limit`: the FTS/vector candidate window
  (`CANDIDATE_CAP=5000` per leg), the **fusion pool** (`CANDIDATE_POOL=400` per
  leg) and with it the vector legs' `k`, the related-tags co-occurrence (30 tags
  / 800ms), the dedup comparison window (5s), and the cluster span (120s). None
  of these grow when `limit` grows — and as of 2026-08-09 none of them *shrink*
  with it either, which is the half that was missing: a bound that tracks the
  page is not a bound, it is a page-shaped ranking (§3.4, §3.6, §3.10).
- No images, ever. That path is `get-frames`, where its cost is visible.

**Errors:** `E_EMPTY_QUERY`, `E_BAD_TIME_FORMAT`, `E_BAD_PARAM` (including a
year-shaped `t_start`/`t_end` on an unscoped call, §3.2), `E_ORDER_SCOPE`,
`E_UNKNOWN_VIDEO` (any id in `video_id[]` unknown — names which), `E_FEATURE_DISABLED`,
`E_TIMEOUT`, `E_BUSY`.

**Annotations:** `{title: "Search video corpus", readOnlyHint: true,
idempotentHint: true, openWorldHint: false}`.

---

### 4.2 `list-videos`

**Purpose:** browse and filter the library itself — titles, channels, durations,
coverage — without a search query.

Resources can't take parameters, so the `vidtheque://corpus` resource is the cheap
unfiltered view and this is the filtered one. Both exist on purpose.

**Description (ships verbatim):**

```
List videos in the corpus, with optional filters. This is the browsable library:
title, channel, publish date, duration, tags, and which channels of data each
video has (transcript / OCR / frame embeddings).

USE WHEN: the user asks what is indexed, wants everything from one channel or tag,
or you need a video_id before calling video-summary or a scoped search. Also use
it after an empty search to check whether the video is even in the corpus.

DO NOT USE: to find content inside videos (use search); to get a picture of the
whole corpus at once (use corpus-summary — it is one call instead of paging).

START WITH limit=20, format="tsv". Filters are case-insensitive substrings.
Ordering defaults to most-recently-published.
```

**Parameters:**

| name | type | default | constraint | notes |
|---|---|---|---|---|
| `q` | string | — | ≤ 256 chars | Matches title + channel + description only. Not transcripts. |
| `channel` | string | — | ≤ 128 | Case-insensitive substring. |
| `tags` | string | — | ≤ 10 | AND semantics. |
| `published_after` / `published_before` | string | — | §3.2 | |
| `indexed_after` / `indexed_before` | string | — | §3.2 | |
| `has` | enum `transcript\|ocr\|frames\|all\|any` | `any` | — | Coverage filter — finds the half-indexed ones. |
| `order` | enum `recency\|title\|duration\|indexed_at\|relevance` | `recency` | — | `relevance` requires `q`. |
| `limit` | int | `20` | clamped 1..100 | |
| `offset` | int | `0` | 0..10000 | |
| `format` | enum `text\|tsv` | `tsv` | — | TSV by default: this output is inherently columnar. |
| `fields` | string | `video_id,title,channel,published,duration,coverage` | ≤ 12 | adds: `tags`, `indexed_at`, `index_state`, `cues`, `frames`, `link` |
| `max_text_chars` | int | `120` | `0` or 40..2000 | Per-cell (titles), not per-response. |

**`index_state` is a field, not a parameter.** The implementation
(`tools/library.list_videos`) also takes an `index_state=` filter
(`pending|indexing|ready|failed|stale|all`), and the MCP tool deliberately does
not expose it: a model asking the library what is *in* the corpus wants the
videos that have data to answer with, which is what omitting it means
(`ready` + `stale`, `queries.QUERYABLE_INDEX_STATES` — the clause this tool has
always had, now a default instead of a constant). The management dashboard is
the caller that needs the other four states, and it calls the service layer
directly (dashboard.md §5.2, §7). The *field* is exposed here because a row
that is `stale` reads as a healthy row without it.

**Return shape:**

```
Videos: 20/~50+ (use offset=20 for more)
Filter: channel~"karpathy" · order=recency

video_id	title	channel	published	duration	coverage
kCc8FmEb1nY	Let's build GPT: from scratch, in code, spelled out.	Andrej Karpathy	2023-01-17	1:56:20	t,o,f
zduSFxRajkE	Let's build the GPT Tokenizer	Andrej Karpathy	2024-02-20	2:13:35	t,o,f
l8pRSuU81PU	Let's reproduce GPT-2 (124M)	Andrej Karpathy	2024-06-09	4:01:26	t,o,-
…

coverage: t=transcript o=on-screen text f=frame embeddings -=missing
1 video is missing frame embeddings. next: index-video url="https://youtu.be/l8pRSuU81PU" force_reindex=true
next: video-summary video_id="kCc8FmEb1nY" for chapters and key texts.
```

**Token discipline.** `limit` clamped 1..100; TSV default (−73% vs JSON on
columnar shapes); titles truncated at 120 chars per cell; response cap applies.
Bounded independently of `limit`: the coverage rollup is read from denormalized
per-video counters, not computed by joining cue/frame tables per row.

The incomplete-coverage footer names `index-video`, and degrades on a deployment
that masks it (§3.8): *"3 video(s) have incomplete coverage. The channels they do
have are searchable; this server cannot re-index them."*

**The list says what it is not listing** (amended 2026-08-10). This tool counts
`QUERYABLE_INDEX_STATES` and `corpus-summary` counts every row, so the two
disagreed by two in the same minute with nothing on either payload connecting
them; a consumer asked point-blank for the exact count constructed the
reconciliation itself and got the number of mid-pipeline videos wrong (terra
eval §4.7). When the corpus holds videos this view cannot show, and no explicit
`index_state=` was passed, the footer names the gap in the same words
`corpus-summary`'s headline uses — both from `tools/corpus_state.read_video_states`:

```
note: 152 of the 154 videos in this corpus are queryable and can appear here; 2 still being indexed (index_state=indexing) cannot. corpus-summary counts all 154.
```

It costs one `GROUP BY index_state` over `videos`, and it prints only when the
two numbers actually differ — a corpus with nothing mid-pipeline says nothing,
and the dashboard's `index_state=all` view is withholding nothing to explain.

**Status — the `cues` and `frames` columns are blank.** Those two opt-in `fields`
are fed by the per-video counters above, and the schema does not carry them
(index-schema §1.2 has no such columns). The alternative — a `COUNT(*)` over `cues`
and `keyframes` per row, on a list path that returns up to 100 rows — is exactly the
unbounded-per-row work rule 6 exists to forbid, so the columns are emitted **present
and empty** rather than either dropped or paid for. `coverage` (t/o/f) is the
cheap boolean answer and it is populated. Ask for `cues,frames` today and you get
the headers with nothing under them; adding the counters is a migration plus a
trigger, and it changes no wire shape.

**Errors:** `E_BAD_TIME_FORMAT`, `E_BAD_PARAM`, `E_ORDER_SCOPE` (`order=relevance`
without `q`), `E_TIMEOUT`, `E_BUSY`.

**Annotations:** `{title: "List indexed videos", readOnlyHint: true,
idempotentHint: true, openWorldHint: false}`.

---

### 4.3 `corpus-summary`

**Purpose:** one pre-aggregated call that answers "what is in this library, and is
it healthy?" — the entry point of the progressive-disclosure chain.

This is the port of screenpipe's `activity-summary`, which exists because of
issue #2285 (row dumps burning context). Their own conclusion from #4294 is the
design rule: *"the LLM doesn't need a bespoke 'workflow' endpoint — it just
queries concise primitives across multiple time ranges and stitches the picture
itself."* So: rollups, toggles, caps — not a workflow engine.

**Description (ships verbatim):**

```
Pre-aggregated overview of the whole video corpus: how many videos, which
channels, which topics/tags, date span, coverage gaps, and what was indexed most
recently. One call instead of paging list-videos.

USE WHEN: this is your FIRST call in a session, or the user asks what is in the
library, what topics are covered, or whether something is indexed yet. Also call
it after an empty search — it tells you whether the corpus is empty, still
indexing, or simply does not contain that topic.

DO NOT USE: to find content (use search); for detail on one video (use
video-summary).

Turn off what you do not need: include_channels, include_tags, include_recent,
include_gaps, include_guidance (all default true). Every section is capped; raise
max_channels / max_tags only when the user is explicitly enumerating.

Three resources back this up: vidtheque://guide (tool flow and shared rules),
vidtheque://context (limits, id formats, time), vidtheque://corpus (the whole
library as TSV).
```

The resource list rides on this description because a client that does not
surface `resources/list` leaves the model with no protocol-native way to learn
the URIs — three of the four bench agents found `vidtheque://guide` only by
guessing it (`vidtheque://help` first), and one never found it at all
(`research/mcp-design-bench-2026-08-09.md` §D1). `corpus-summary` is the
documented first call, so it is where the list costs least.

**Parameters:**

| name | type | default | constraint | notes |
|---|---|---|---|---|
| `channel` | string | — | ≤ 128 | Scope the whole rollup to one channel. |
| `tags` | string | — | ≤ 10 | Scope to a collection. |
| `published_after` / `published_before` | string | — | §3.2 | |
| `include_channels` | bool | `true` | | |
| `include_tags` | bool | `true` | | |
| `include_recent` | bool | `true` | | Most recently indexed videos. |
| `include_gaps` | bool | `true` | | Coverage/failure diagnostics. |
| `include_guidance` | bool | `true` | | `next_best_query`. |
| `max_channels` | int | `10` | clamped 1..50 | |
| `max_tags` | int | `30` | clamped 1..100 | |
| `max_recent` | int | `8` | clamped 1..25 | |
| `max_text_chars` | int | `120` | `0` or 40..2000 | Per title/label. |

**Return shape:**

```
Corpus: 312 videos (310 queryable · 2 still being indexed) · 486h 12m · 1,240,331 transcript cues · 58,904 keyframes
Published span: 2019-04-02 → 2026-08-02 · last indexed: 2026-08-07 19:41 (2 videos)
data_status: ok

Channels (top 10 of 34):
  Andrej Karpathy          11 videos   24h 06m
  3Blue1Brown              28 videos   12h 41m
  GPU MODE                 41 videos   61h 55m
  Yannic Kilcher           37 videos   38h 12m
  …

Tags (top 30 of 112):
  topic:transformers 58 · topic:cuda 41 · topic:inference 39 · person:karpathy 11
  topic:quantization 22 · series:gpu-mode 41 · topic:rag 18 · lang:en 305 …

Recently indexed:
  2026-08-07  Flash Attention 3 walkthrough — GPU MODE (Qk7mF2xLp0A)  1:04:11
  2026-08-07  Speculative decoding, explained — Trelis (9dRk2XcVbNw)  38:20
  …

Gaps:
  4 videos have transcript but no OCR (indexed before OCR was enabled)
  1 video failed: HcQ8pL3vN1s — "yt-dlp: video unavailable (private)" on 2026-08-05
  0 video(s) mid-pipeline (index_state=indexing)
  5 indexing job(s) queued or running: 5 job(s) deferred until 2026-08-09T22:00:00Z, nothing running

next_best_query: search q="<topic>" limit=5 — or list-videos channel="GPU MODE" to browse that channel.
```

**The headline count is every row, and it says how many of them can answer**
(amended 2026-08-10). `search` and `list-videos` answer from
`QUERYABLE_INDEX_STATES`; this tool counts the table. The parenthetical is
printed only when the two differ, comes from the same
`tools/corpus_state.read_video_states` as `list-videos`' matching footer (§4.2),
and is carried as `queryable_videos` plus a `videos_by_index_state` map in
`structuredContent`. Before it, the two tools reported 154 and 152 in the same
minute with nothing reconciling them, and the consumer that had been asked for
the exact number wrote its own explanation into a deliverable — naming two
videos mid-pipeline where `Gaps:` said one (terra eval §4.7).

`data_status` is the endpoint diagnosing itself, so an empty answer never sends the
model guessing:

| value | meaning |
|---|---|
| `ok` | corpus populated, nothing queued, nothing missing |
| `empty` | nothing indexed yet → `next:` is `index-video` |
| `indexing` | work is **happening or imminent**: ≥1 job running, ≥1 queued job the runner may claim now, or ≥1 video `index_state='indexing'`; counts are a moving target → `next:` is `job-status` |
| `deferred` | the queue is non-empty and **none of it can run yet**: every queued job sits behind a future `jobs.not_before`. Counts are *not* moving; they will move at `deferred_until` |
| `partial` | ≥1 video indexed with missing channels (transcript-only, no frames) |
| `degraded` | ≥1 job failed in the last 24h, **or** ≥1 video is waiting to be re-embedded after an embedding-model change; results may be incomplete |

Precedence is worst-first: `empty` → `indexing` → `degraded` → `deferred` →
`partial` → `ok`. `degraded` outranks `deferred` deliberately, because a
deferral is often the backoff *after* a failure and "data is missing now" beats
"work is scheduled for later"; the queue clause below still prints in both cases.

**`deferred` is an extension of this vocabulary, added 2026-08-09, not a fifth
one.** dashboard.md §4.5 forbids a new consumer inventing its own words, and this
is the opposite move: one word added to the *shared* vocabulary, in the one place
it is derived (`tools/corpus_state.py`), and read by all three surfaces that
print `data_status`. It exists because the state it names was being reported as
`indexing`, which was false in the most visible payload in the product — the
first call of a session (demo-queries §9.1.4):

```
resource vidtheque://context     → "active_jobs": 5, "data_status": "indexing"
call corpus-summary '{}'         → data_status: indexing  /  0 videos currently indexing
call search '{"q":"🔥"}'          → data_status: ok (… index fresh)
```

Five jobs were queued with `not_before` in the future. Nothing was running,
nothing was about to, and the queue was correctly non-empty — the deferral was
real, the jobs resumed that night, and there was no word for it. `jobs.not_before`
is the column that reconciles the three payloads, `_JOB_SQL` has selected it
(with `defer_s`, the remainder) since the jobs view landed, and nothing read it
back.

**Two counters, one name each** — the other half of that finding. `corpus-summary`
printed a headline driven by rows in `jobs` two lines above a Gaps line counting
videos in `index_state='indexing'`, both called "indexing", and they disagreed
without either being wrong. They are now named apart everywhere:

- `gaps.indexing` / "N video(s) mid-pipeline (index_state=indexing)" — **videos**.
- `gaps.jobs_active` / `jobs_running` / `jobs_deferred` / `jobs_deferred_until`
  and the "N indexing job(s) queued or running" line — **rows in `jobs`**.

Whenever the queue is not idle the header carries the same clause the other
surfaces use:

```
data_status: deferred
queue: 5 job(s) deferred until 2026-08-09T22:00:00Z, nothing running
```

The split (running vs ready vs deferred) needs the rows themselves, so it reads a
bounded page of the active queue (25 jobs, `QUEUE_PAGE_CAP`); the *count* comes
from the exact aggregate. A queue deeper than the page prints the count and no
split — a queue that deep is unambiguously working — rather than a split it did
not read.

**The re-embed window is a `degraded`, and it says so in words too.** Changing
the embedding model rebuilds the vector tables and sets the `text_embed` /
`frame_embed` stages back to `pending` (index-schema §1.10). Nearest-neighbour
search over a half-filled index does not fail — it quietly returns less — so
this endpoint reports it rather than letting the corpus look complete:

```
data_status: degraded
note: 75 transcript and 75 frame vector set(s) are waiting to be re-embedded
after an embedding-model change — semantic search covers only the videos
already re-embedded; keyword search is unaffected. Nothing is re-downloaded or
re-transcribed by the backfill.
```

with `embed_backlog: {"text": N, "frame": M}` in the structured payload. It
reuses `degraded` rather than inventing a sixth value, per dashboard §4.5 —
"the corpus is complete and its semantic half is not" is what the word already
means here. `search` prints its own per-leg `note:` for the same window (§4.1),
and `index-video url="…"` with **no** `force_reindex` is the backfill: the
outstanding stages resume, and nothing is re-downloaded or re-transcribed.

**Token discipline.** Hard caps on every section (10 channels / 30 tags / 8 recent
by default; 50/100/25 ceilings). Repeated sibling rows collapse with `×N` in the
gaps section. Bounded independently of any `limit`: the channel and tag rollups
read materialized counter tables refreshed at index time, never `GROUP BY` over
the cue table. Worst case ≈ 3,500 chars with everything on at ceiling values.

**Errors:** `E_BAD_TIME_FORMAT`, `E_BAD_PARAM`, `E_TIMEOUT`, `E_BUSY`.

**Annotations:** `{title: "Summarize the corpus", readOnlyHint: true,
idempotentHint: true, openWorldHint: false}`.

---

### 4.4 `video-summary`

**Purpose:** pre-aggregated rollup of one video — chapters with timestamps,
speakers, key on-screen texts, tags, links — instead of dumping a two-hour
transcript.

**Description (ships verbatim):**

```
Structured overview of one indexed video: chapters with timestamps and deep links,
speakers, the most informative on-screen texts, tags, and links from the
description. Use it instead of reading the whole transcript.

USE WHEN: the user asks what a video covers, wants its structure, or you need to
pick a timestamp to drill into. Good second call after search returns a video you
have not seen before.

DO NOT USE: to search across videos (use search); to read the actual words around
a moment (use get-segment-context — this tool samples, it does not transcribe).

Everything heavy has an off switch: include_chapters, include_speakers,
include_key_texts, include_ocr_highlights, include_links, include_tags,
include_guidance. Caps: max_chapters=20, max_key_texts=12, max_chars=300 per item.
Full transcripts are never returned by this tool at any setting.
```

**Parameters:**

| name | type | default | constraint | notes |
|---|---|---|---|---|
| `video_id` | string | — | **required** | From search / list-videos. |
| `offset_start` / `offset_end` | number \| string | — | §3.2 | Summarize only part of a long video. |
| `include_chapters` | bool | `true` | | The publisher's chapter marks, as yt-dlp reports them. **Not derived** — see the status note below. |
| `include_speakers` | bool | `true` | | Omitted silently when diarization is off (this one *is* a silent omission — it's a presence question, not a filter). |
| `include_key_texts` | bool | `true` | | Sampled transcript lines with highest TF-IDF weight. |
| `include_ocr_highlights` | bool | `true` | | Distinct on-screen texts (slide titles, code, command lines). |
| `include_links` | bool | `false` | | URLs from the description. Off by default — link lists are pure noise until asked for. |
| `include_tags` | bool | `true` | | |
| `include_guidance` | bool | `true` | | |
| `max_chapters` | int | `20` | clamped 1..50 | |
| `max_key_texts` | int | `12` | clamped 1..30 | |
| `max_ocr_highlights` | int | `10` | clamped 1..30 | |
| `max_chars` | int | `300` | `0` or 80..1200 | Per item, middle-truncated. |
| `format` | enum `text\|outline` | `text` | — | `outline` = indented, `×N`-collapsed, ≤200 lines. |

**Return shape:**

```
Let's build GPT: from scratch, in code, spelled out.
Andrej Karpathy (kCc8FmEb1nY) · published 2023-01-17 · 1:56:20 · indexed 2026-06-02
https://youtu.be/kCc8FmEb1nY
data_status: ok (transcript ✓ · ocr ✓ 1,204 keyframes · frame embeddings ✓)
Tags: topic:transformers, topic:training, person:karpathy, lang:en

Chapters (14 of 14):
  00:00  intro: ChatGPT, Transformers, nanoGPT           ?t=0
  07:52  reading and exploring the data                  ?t=470
  09:23  tokenization, train/val split                   ?t=561
  …
  1:11:32 kv cache and inference-time cost               ?t=4290
  1:47:58 conclusions                                    ?t=6476

Speakers: Andrej Karpathy (99% of speech), unnamed_2 (0.4%, 00:03-00:11)

Key texts (12):
  09:41  "we're going to take the tiny shakespeare dataset and train a character-level model"  ?t=579
  33:12  "the crux of self-attention is that every token emits a query and a key"              ?t=1990
  …

On-screen text highlights (10):
  12:04  import torch; torch.manual_seed(1337)                       [code]      ?t=722
  35:50  wei = q @ k.transpose(-2,-1) * C**-0.5                      [code]      ?t=2148
  41:18  "attention is a communication mechanism"                    [slide]     ?t=2476
  ×3 near-identical terminal frames collapsed (48:02-48:40)
  …

next: get-segment-context video_id="kCc8FmEb1nY" t=581 window=60 for the actual words around the first key text above.
```

`data_status`: `ok` | `transcript_only` | `no_ocr` | `no_frames` | `indexing`
(with the `job_id`) | `failed` (with the error and a `force_reindex` hint).

**Empty sections say what is absent; they are never a bare heading, and the
`next:` is aimed** (amended 2026-08-10). `Chapters (0 of 0):` over nothing is
the shape §3.7 forbids for tags, and it left a caller unable to tell "this video
has none" from "this server did not compute them" — while the closing line
pointed at `t=0`, the first second of a 27-minute talk, directly beneath three
timestamped key texts it could have aimed at (terra eval §4.10). Each of the
three sections prints one honest line when it is empty (chapters: *"none — the
publisher marked none in the description, and this corpus does not derive
them"*; key texts name the `t_start`/`t_end` span when one was passed), and the
`next:` aims at the first key text, falling back to the first chapter after
0:00, and only then to `t=0`.

**Status — chapters are the publisher's, not derived.** This section used to
promise "YouTube chapters if present, else derived from scene+topic
segmentation". The pipeline stores what yt-dlp reports (`pipeline/sources.py`
`_chapters`, `pipeline/store.replace_chapters`) and derives nothing, so a talk
published without chapter marks has none — which is most conference talks. The
empty-section line above is therefore the contract's answer to "why are there no
chapters", and it does not claim a segmentation pass that does not run.

**Token discipline.** Caps above, all clamped server-side; `×N` collapsing for
runs of near-identical OCR (perceptual-hash buckets, computed at index time);
`include_links` off by default. Bounded independently of everything else: key-text
and OCR-highlight selection is O(caps), not O(video length). A 4-hour video and a
4-minute video cost the same tokens and roughly the same milliseconds. Worst case
≈ 6,000 chars at ceiling settings.

**Status — no salience table; an `NTILE` sample instead.** This paragraph used to
say the selection reads a precomputed per-video "salience" table built during
indexing. The schema carries no such table, and building one is a second copy of the
corpus to keep in sync for a bound that can be had without it. What ships:
`NTILE(:max_key_texts) OVER (ORDER BY start_s)` buckets the video's cues into
equal-width spans and takes the longest cue in each, so the sample is spread across
the running time by construction and the query emits `max_key_texts` rows from one
index scan. OCR highlights come off the `keyframes_live` partial index
(`dup_of IS NULL`, index-schema §1.6) with the same cap. Same guarantee the original
claim was making — O(caps) out, no per-row fan-out, cost independent of video
length — with no table to maintain.

**Errors:** `E_UNKNOWN_VIDEO`, `E_NOT_INDEXED`, `E_INDEXING` (returns whatever is
already queryable plus the job id), `E_BAD_PARAM`.

**Annotations:** `{title: "Summarize one video", readOnlyHint: true,
idempotentHint: true, openWorldHint: false}`.

---

### 4.5 `get-segment-context`

**Purpose:** full detail around one moment — the drill-down leaf of the guide.

**Description (ships verbatim):**

```
Everything around one moment in one video: the verbatim transcript window, the
on-screen text of nearby keyframes, the enclosing chapter, and frame ids you can
pass to get-frames.

USE WHEN: search or video-summary gave you a video_id and a timestamp and you need
the actual words — to quote accurately, to check context before/after, or to
decide whether a hit is relevant.

DO NOT USE: as a transcript dump (window is capped at 300s and 4000 chars — for
broad coverage call it two or three times at different t, or use video-summary);
to find the moment in the first place (use search).

Pass video_id and t exactly as they appeared in a previous result. Never invent
them. START WITH window=45 — seconds each side of t, clamped 5-300. If the line
you want is cut off at an edge of the window, raise window rather than guessing
new t values.
```

`window` earns its place in the description (and in the tool's own `next:` line)
because it is otherwise invisible: the input schema carries no per-parameter
descriptions, and a truncating client shows only the first line. A bench agent
found it by guessing after burning three window-walk calls
(`research/mcp-design-bench-2026-08-09.md` §D2).

**Parameters:**

| name | type | default | constraint | notes |
|---|---|---|---|---|
| `video_id` | string | — | **required** | |
| `t` | number \| string | — | **required**; clamped to `[0, duration]` | Seconds or `hh:mm:ss`. Out-of-range clamps and says so — no error for a slightly-late timestamp. |
| `window` | number | `45` | clamped 5..300 | Seconds each side of `t`. |
| `cue_id` | int | — | | Alternative to `t`; centres on that cue exactly. |
| `include_ocr` | bool | `true` | ≤ 8 frames, ≤ 1200 chars | On-screen text of keyframes in the window. |
| `include_frame_refs` | bool | `true` | ≤ 12 ids | Frame ids for `get-frames`. Ids only, never images. |
| `include_chapter` | bool | `true` | | |
| `include_links` | bool | `false` | ≤ 10 | Description links whose timestamp falls in the window. |
| `max_text_chars` | int | `4000` | `0` or 200..20000 | Transcript window budget. `0` still respects `window`, and also lifts the 300-char per-frame cut in the on-screen block — the marker there names this parameter, so it has to work (field test §7.3). The block's own 8-frame / 1200-char cap still binds and says so in words. |

**Return shape:**

```
kCc8FmEb1nY · Let's build GPT: from scratch — Andrej Karpathy
Chapter: "kv cache and inference-time cost" (1:11:32-1:19:04)
Window: 1:11:36-1:13:06 (t=4321 ±45s) · https://youtu.be/kCc8FmEb1nY?t=4276

TRANSCRIPT (cite one line: https://youtu.be/kCc8FmEb1nY + the ?t= printed on it)
[1:11:36 ?t=4294] and now if you think about what happens at generation time, you have a
[1:11:41 ?t=4299] prompt, and then you sample one token, and then you feed the whole thing
[1:11:47 ?t=4305] back in. so you are recomputing all of the keys and values for every
[1:11:52 ?t=4310] token you already processed, every single step.
[1:12:03 ?t=4321] so the reason we cache the keys and the values is that at every new token
[1:12:09 ?t=4327] you would otherwise recompute attention over the entire prefix, which is
[1:12:15 ?t=4333] quadratic. the cache makes it linear in the number of new tokens, and the
[1:12:22 ?t=4340] price you pay is memory.
…
[1:13:01 ?t=4379] which is why long-context inference is a memory-bandwidth problem.
(cues 1836-1861 · 1,910 chars, under the 4000 budget)

ON-SCREEN TEXT (3 keyframes)
[1:11:58 ?t=4316] kCc8FmEb1nY-00701  cache = {} ; for k,v in layers: cache[k].append(v)   [code]
[1:12:26 ?t=4344] kCc8FmEb1nY-00703  KV cache: O(n) memory, O(1) recompute per token      [slide]
[1:12:54 ?t=4372] kCc8FmEb1nY-00705  (terminal) nvidia-smi — 18,304MiB / 24,564MiB        [terminal]

FRAMES: kCc8FmEb1nY-00701, kCc8FmEb1nY-00703, kCc8FmEb1nY-00705
  → get-frames frame_ids=["kCc8FmEb1nY-00703"] to see the slide

next: if the line you want runs past this window, call again with a larger window= (up to 300) rather than guessing a new t; or search q="memory bandwidth" video_id="kCc8FmEb1nY" to find where else he says this.
```

**Every printed line is citable on its own** (amended 2026-08-10). This tool
prints 20–40 timestamped lines, and it used to print exactly one `youtu.be`
link: the header's, for the window anchor. A consumer quoting two moments of one
window has no link for the second and is forbidden (by `vidtheque://guide`) to
invent one, so it did the only remaining thing — reused the header link for
both, and shipped a citation 27 s off the words beside it (terra eval §4.4).
Each transcript and on-screen line therefore carries the compact §3.6 form
inside its stamp, `[1:12:03 ?t=4321]`, and the `TRANSCRIPT` header names the
base URL to append it to. The whole URL on 40 lines would spend roughly a third
of the transcript budget on repetition of one string; the suffix costs ~2%.
Conformant clients do not compose anything: `structuredContent.cues[]` now
carries `link` beside `cue_id`/`start`/`end`/`text`.

**Token discipline.** Double-capped: `window` seconds **and** `max_text_chars`,
whichever binds first, with the binding one named in the payload. OCR capped at
8 frames / 1200 chars, frame refs at 12 ids — all independent of `window`, so
`window=300` does not multiply the image-adjacent cost (screenpipe caps the
analogous `frame-context` at 50 nodes / 2000 chars for the same reason). Never
returns image content. The per-line `?t=` is counted against neither cap: it is
~9 characters of link, not of text, and a budget that shrank the quote to pay
for its own citation would be the wrong trade.

**Errors:** `E_UNKNOWN_VIDEO`, `E_NOT_INDEXED`, `E_INDEXING`, `E_BAD_PARAM`
(`cue_id` belonging to a different video names the right video).

**Annotations:** `{title: "Get context around a moment", readOnlyHint: true,
idempotentHint: true, openWorldHint: false}`.

---

### 4.6 `get-frames`

**Purpose:** deliver keyframe images — as authenticated URLs by default, or inline
base64 when the client actually renders it.

**This is the publishable contribution.** MCP's `ImageContent` is the correct
implementation and it is badly broken in real clients: Claude Code passes it
through as raw base64 *text*, ~15,000–25,000 tokens per image instead of ~1,600
(10–20× blowup), and the model cannot see it —
[claude-code#31208](https://github.com/anthropics/claude-code/issues/31208), closed
not-planned, with #14150 / #9152 / #4002 closed unfixed alongside it. The two
mitigations in the wild are `media-mcp`'s local file paths (useless for a remote
server — no shared filesystem) and serving a URL from the MCP server itself.
Nobody in the landscape survey ships the URL variant. We default to it.

**Description (ships verbatim):**

```
Fetch keyframe images from indexed videos, as URLs (default) or inline base64.

USE WHEN: a result mentions a slide, diagram, chart or UI and text is not
enough — you have frame ids from search, video-summary or get-segment-context.
Also when OCR reads garbled or clipped: dense slides (tables, code) are pixels,
not text.

DO NOT USE: to browse a video (frames are keyframes, not a filmstrip).

START WITH return="url" and open the URL. Every id you pass is fetched (max 12);
limit bounds only the video_id span mode. The ocr: line is capped at 300
chars/frame — max_text_chars=0 gives every line. return="image" inlines base64
JPEG, max 4 per call — 10-20x the nominal token cost on some clients.
```

**Changed 2026-08-09.** The old "DO NOT USE … to read text that is already in the
payload (OCR text is returned with the search result, no image needed)" was
wrong often enough to be harmful: on the bench, five of eight visual questions
were only answerable from the pixels, because the OCR text is a flat
reading-order join that is capped per frame and mangles digits and bullet glyphs
(`8.8` → `8.&`, rank `1 ●` → `10`). The description now says the opposite — a
garbled or clipped OCR line is a reason to open the image. It also states the
300-char cap (`research/mcp-design-bench-2026-08-09.md` §D3, §D4).

**Changed 2026-08-09 (second pass), and these are contract changes, not
wording.** The field test with unbriefed agents
(`research/demo-queries-2026-08-09.md` §7.3, §7.13, §9.1.1, §9.1.7) found four
ways this tool lied about what it had done:

1. **`limit` no longer slices `frame_ids`.** It bounds the `video_id` span mode
   only. `frame_ids[:limit]` was applied *before* validation, so with the
   default `limit=3` a caller passing five valid ids got three frames, an empty
   `failed:`, and the header `Frames: 3/3` — the denominator asserting it had
   them all. A malformed id in position 4+ was never parsed either, so the good
   `failed:` message never fired for it. Named ids are the caller's own cap and
   they already have a server-side one: `frame_ids` > 12 is `E_BAD_PARAM`, and
   the §7 token table has always budgeted the 12-id worst case. So every named
   id is now validated and fetched, and a `limit` that would have narrowed the
   list prints a `note:` instead — the `all` means `all` rule, applied to ids.
   The alternative (report the dropped ids in `failed:`) keeps a cap the caller
   never asked for and makes the money shot of both flagship flows depend on
   remembering to raise it.
2. **`max_text_chars` exists, with the documented `0` opt-out** (`0` or
   120..2000, default 300). The shared truncation marker prints *"pass
   max_text_chars=0 for full text"*; the parameter did not exist, the tool
   description said "no opt-out", and the guide said there never would be one —
   three sources, three behaviours. `0` returns the frame's every OCR line in
   reading order, which is the same promise §3.3 makes for the OCR leg. The
   image is still the only place the *layout* survives.
3. **Rows come back in the order the caller asked for.** The SQL orders by
   `(video_id, ord)`; a UI laying out a strip of frames asked in one order and
   got another.
4. **`expires_at` is `null`, not `0`, when a URL never expires.** In `none`
   mode every frame carried `"expires_at": 0`, which any consumer doing
   `now > expires_at` reads as "expired in 1970". The prose already said "URLs
   do not expire"; the number now agrees with it.

The same `0`-opt-out fix lands in `get-segment-context`'s on-screen block
(§4.5), which printed the same marker and ignored the same parameter; its
independent 8-frame / 1200-char cap still binds and still announces itself in
words.

**Parameters:**

| name | type | default | constraint | notes |
|---|---|---|---|---|
| `frame_ids` | string[] | — | ≤ 12 ids, `E_BAD_PARAM` above that | Either this **or** `video_id` (+ optional time range). All of them are fetched, in request order; `limit` does not narrow them. |
| `video_id` | string | — | | With `offset_start`/`offset_end`, returns the keyframes in that span. |
| `offset_start` / `offset_end` | number \| string | — | §3.2 | Span within `video_id`; span > 600s → `E_BAD_PARAM` with a narrowing hint. |
| `return` | enum `url\|image` | `url` | — | No `path` mode: this is a remote server. |
| `limit` | int | `3` | clamped 1..12 | Bounds the `video_id` span mode. Passed alongside `frame_ids` it prints a `note:` and changes nothing. |
| `width` | int | `512` | clamped 128..1280 | Longest edge; aspect preserved. |
| `quality` | int | `75` | clamped 20..95 | JPEG quality. |
| `include_ocr` | bool | `true` | | The frame's OCR text alongside — often removes the need to look at all. |
| `max_text_chars` | int | `300` | `0` or 120..2000 | Per-frame OCR budget, middle-truncated. `0` is the documented opt-out: the frame's every line, in reading order. |

**Return shape — `return="url"` (default):** one text block, no image blocks.

```
Frames: 3/3
kCc8FmEb1nY-00703 · 1:12:26 · https://youtu.be/kCc8FmEb1nY?t=4344
  image: https://vidtheque.example.com/frames/kCc8FmEb1nY-00703.jpg?w=512&q=75&exp=1786348800&sig=8f3a…
  ocr: "KV cache: O(n) memory, O(1) recompute per token"
kCc8FmEb1nY-00705 · 1:12:54 · https://youtu.be/kCc8FmEb1nY?t=4372
  image: https://vidtheque.example.com/frames/kCc8FmEb1nY-00705.jpg?w=512&q=75&exp=1786348800&sig=1c02…
  ocr: "(terminal) nvidia-smi — 18,304MiB / 24,564MiB"
…
URLs expire 2026-08-08T18:00:00Z. They are signed — no auth header needed to fetch them.
```

The header is `Frames: <fetched>/<asked for>`, and both halves are honest: a
named id that could not be fetched appears on a `failed:` line and counts in the
denominator, never in neither. `expires_at` in the structured payload is the
epoch second the signature dies, or `null` when URLs never expire (auth `none`),
matching the footer prose exactly. A `limit` passed alongside `frame_ids` adds:

```
note: limit bounds the video_id span mode; all 5 named frame_ids were looked up (the cap on named ids is 12) — the count above is what came back.
```

Signed URLs, not bearer-protected paths: the renderer that fetches the image is
usually a browser context that will not attach the OAuth token. HMAC over
`(frame_id, w, q, exp)` with the server key, 1h TTL, constant-time compare. The
`/frames/<id>.jpg` route also accepts the OAuth bearer for programmatic clients.

**Return shape — `return="image"`:** a text block per frame followed by its
`ImageContent`:

```
📷 kCc8FmEb1nY-00703 · Let's build GPT · 1:12:26 · https://youtu.be/kCc8FmEb1nY?t=4344
```
```json
{"type":"image","data":"/9j/4AAQSkZJRgABAQ…","mimeType":"image/jpeg"}
```

**`mimeType` is `image/jpeg` and the bytes are JPEG.** screenpipe labels
ffmpeg-emitted MJPEG bytes as `image/png` (live bug); a mislabelled image is a
client-side decode failure with no useful error.

When more images are requested than the inline budget allows, the extras
**downgrade to URLs rather than failing**:

```
Frames: 6/6 (4 inline, 2 as URLs — inline cap is 4 images / 6MB per call)
```

**Token discipline.**
- Two item caps, both server-side: `frame_ids` ≤ 12 (a hard `E_BAD_PARAM`, not a
  silent slice) and span-mode `limit` clamped 1..12. The worst case is the same
  12 frames either way, which is what the §7 table already budgets.
- Inline images capped at **4 per call and 6MB total,
  independent of `limit`** — this is the "bound the expensive path independently"
  rule (screenpipe's `MAX_INLINE_FRAMES_PER_SEARCH=20` + concurrency 4 + global
  semaphore 3, after `limit=500&include_frames=true` spawned 500 ffmpeg processes).
- OCR text capped at 300 chars/frame by default, `max_text_chars` 0 or 120..2000.
  The `0` opt-out is real here — a marker that names a parameter the tool does
  not accept is worse than no marker (§7.3 of the field test).
- Keyframe JPEGs are written at index time and served from disk. There is **no
  ffmpeg on the query path** — resizing is a cached PIL resample keyed on
  `(frame_id, w, q)`, single-flight per key, into the `derived/` cache of
  index-schema §6. The cache is evicted on a **byte cap**
  (`VIDTHEQUE_DERIVED_CACHE_MB`), not on a 30-minute TTL as this line used to
  say: a variant is immutable for as long as the keyframe behind it exists, so
  age is the wrong thing to expire on and disk is the thing worth bounding.
- Per-frame failures are collected, not fail-fast: the payload lists successes and
  a `failed:` line per bad id.

**Status — one gap between this section and what ships, and one clamp that is
wider at the route than at the tool.**

- **Signed-URL TTL is 24h, not the 1h written above** (`VIDTHEQUE_FRAME_URL_TTL`,
  default `86400`), per DECISIONS.md. Because the TTL is configurable, the shipped
  description names no figure at all ("URLs are signed and expire") and the
  footer prints the actual expiry timestamp it just signed.
- **`/frames/<id>.jpg` clamps `w` to 64..1280, the tool to 128..1280.** The
  demo facade (demo-site.md) renders a 96×54 grid and asks the route directly;
  128 is a floor for what a *model* should ask for, not for what a browser may.
  The signature binds the clamped pair, so widening the route's floor can only
  turn a URL that used to 401 into one that works.

**Resizing ships.** `w` and `q` are applied, not just signed: the route
resamples into `derived/` and serves the variant, `w` wider than the stored
keyframe returns the original rather than an upscale, and no parameters at all
returns the stored file byte for byte. Responses carry
`Cache-Control: public, max-age=…` when the URL itself is the credential (open
mode, or a signed URL — then capped at the signature's own remaining life) and
`private, max-age=…` under a bearer token or session cookie, where a shared
cache would otherwise re-serve one caller's frame to the next.

**Errors:** `E_UNKNOWN_FRAME` (names the valid ordinal range for that video),
`E_UNKNOWN_VIDEO`, `E_BAD_PARAM` (neither `frame_ids` nor `video_id`; span too
wide), `E_TOO_LARGE` (single frame > inline budget even alone → "use return=url"),
`E_BUSY`.

**Annotations:** `{title: "Get keyframe images", readOnlyHint: true,
idempotentHint: true, openWorldHint: false}`.

---

### 4.7 `index-video`

**Purpose:** add a video, playlist, or channel page to the corpus. Asynchronous —
returns a job id immediately.

Indexing is the only GPU path (download → whisperX → keyframes → OCR → embeddings)
and takes minutes. A synchronous tool call would blow every client's timeout, so
this returns a handle and `job-status` (§4.8) does the waiting.

**Description (ships verbatim):**

```
Add a video, playlist, or channel to the corpus. Returns immediately with a job
id — indexing runs in the background and takes roughly 1-3 minutes per hour of
video, longer if the GPU is busy.

USE WHEN: the user gives you a video URL that search says is not indexed, or asks
to add something to the library.

DO NOT USE: to fetch a transcript for immediate reading (nothing is queryable
until the job reaches "done" — poll job-status); on a URL the user did not ask
for. Do not call this repeatedly for the same URL: a video already in the corpus
returns its existing video_id without re-indexing unless force_reindex=true.

After calling, tell the user it is queued and give the estimate. Poll job-status
at most every 15 seconds.
```

**Parameters:**

| name | type | default | constraint | notes |
|---|---|---|---|---|
| `url` | string | — | one of `url` / `urls` required | Video, playlist, or channel URL. Bare 11-char ids accepted. |
| `urls` | string[] | — | ≤ 10 | Batch → one job id covering all of them. |
| `expand` | enum `none\|playlist\|channel_recent` | `playlist` | ≤ `max_items` | What a non-video URL expands to. `channel_recent` = newest N uploads. |
| `max_items` | int | `25` | clamped 1..200 | Hard ceiling on expansion. Exceeded → indexes the first `max_items` and says so. |
| `tags` | string | — | ≤ 10, §3.7 validation | Applied to every video in the job. |
| `force_reindex` | bool | `false` | | Re-runs the pipeline over an existing video. |
| `channels` | string | `all` | `all` \| subset of `transcript,ocr,frames` | Skip stages (e.g. transcript-only for a podcast). |
| `priority` | enum `normal\|high` | `normal` | | `high` jumps the queue; rate-limited per client. |

**Return shape:**

```
Job queued: job_7f3a29b1c04d
1 video: "Flash Attention 3 walkthrough" — GPU MODE (Qk7mF2xLp0A, 1:04:11)
Stages: download → transcribe → keyframes → ocr → embed
Queue position 2 · estimated 4-7 minutes (GPU currently busy with job_2b81c4)
Tags to apply: topic:attention, series:gpu-mode

Nothing from this video is searchable until the job reports done.
next: job-status job_id="job_7f3a29b1c04d" (poll no more than every 15s).
```

Already indexed, no `force_reindex`:

```
Already indexed: Qk7mF2xLp0A — "Flash Attention 3 walkthrough" (indexed 2026-08-07, coverage t,o,f)
No job created. next: video-summary video_id="Qk7mF2xLp0A", or index-video force_reindex=true to rebuild.
```

**A mixed wave is partitioned before the job exists.** The shortcut above needs
*every* URL to be current; a batch of ten where nine are already indexed used to
queue all ten, and `fetch` probes and downloads before any later stage can
discover the video was current — nine redundant downloads a wave, with the
retention default having deleted the mp4. Current videos are now inserted
already terminal as `skipped/E_ALREADY_INDEXED`, so they are counted and
explained in `job-status` without being work:

```
Job queued: job_7f3a29b1c04d
1 video(s):
  https://youtu.be/9dRk2XcVbNw
9 already indexed and left alone: Qk7mF2xLp0A, HcQ8pL3vN1s, …
```

The payload carries `items` (the work), `n_items` (the rows) and
`already_indexed` (their video ids). `force_reindex` skips the partition
entirely — it means "do it anyway". Downloading is gated the same way one level
down: the pipeline fetches audio only if `stt` is going to run and the mp4 only
if `keyframe` is, so resuming a video whose only outstanding stage is `ocr`
costs no bandwidth at all.

**Token discipline.** Playlist expansion capped at `max_items` (ceiling 200) and
the payload lists at most **10 titles**, then `… and 43 more`. Never echoes the
full expansion. Fixed-size response regardless of batch size.

**`force_reindex` on a video that is already being indexed** supersedes or
refuses, never silently no-ops (index-schema §1.9): a claim nobody is holding —
queued, or reclaimed from a process that died — is cancelled and the fresh job
takes the video; a *live* claim is `E_INDEXING` naming the job that holds it.
The one thing it must never do is create a job that skips its only item and
reports `done`, which is what it did until 2026-08-08.

**Errors:** `E_UNSUPPORTED_SOURCE` (yt-dlp can't handle it — lists what is
supported), `E_BAD_PARAM` (malformed URL, bad tag namespace — lists valid
namespaces), `E_INDEXING` (a live job already holds one of those videos —
carries its `job_id`), `E_RATE_LIMIT` (`retry_after_s`), `E_BUSY` (queue at
capacity — `retry_after_s`), `E_INTERNAL`.

**Annotations:** `{title: "Index a video", readOnlyHint: false,
idempotentHint: true, openWorldHint: true}`. Idempotent because the same URL
without `force_reindex` is a no-op returning the existing id; `openWorldHint: true`
because this is the one tool that reaches the internet.

---

### 4.8 `job-status`

**Purpose:** poll an indexing job, or list recent jobs.

**Description (ships verbatim):**

```
Check the status of an indexing job started by index-video. Call with no arguments
to list recent jobs.

USE WHEN: you started an index-video job and need to know whether the video is
searchable yet, or a tool told you a video is still indexing.

DO NOT USE: in a tight loop. Indexing takes minutes; poll at most every 15
seconds, and prefer telling the user "it is running, ask me again in a minute"
over polling repeatedly inside one turn.

A job is only searchable at state "done". States "transcribing" onward make the
transcript partially queryable — the response says exactly what is available. A
job still running needs another poll, not a re-index — force_reindex is for a job
that actually reported "failed".
```

The last sentence is a counterweight, and the hint it counterweighted is now
state-aware. The list-mode footer printed `next: index-video url="…"
force_reindex=true to retry a failed job` even when the only job was running
normally, and a bench agent read that as an instruction to re-index a job at 57%
(`research/mcp-design-bench-2026-08-09.md` §D5); in the read-only demo it named a
tool that is not registered at all (demo-queries §9.1.8). It now prints only when
the listed page actually contains a failed job, degrades per §3.8 when
`index-video` is masked, and otherwise points at this tool's own single-job view.
The same treatment applies to every `next:` in single-job mode (`done` with
nothing indexed, `failed`, `cancelled`) and to the `fix:` line under a degraded
job.

**A `queued` job says whether it is waiting for a slot or for a clock.** `queued`
covered both, and five jobs held behind `jobs.not_before` read as five jobs
indexing (§4.3, demo-queries §9.1.4). A deferred job now says so on its own line —
*"deferred until 2026-08-09T22:00:00Z (1794s to go) — this job is queued and is
not running; nothing is being indexed for it right now"* — the list mode carries
`deferred until …` per row and counts them in its header, and the structured
payload carries `deferred` and `defer_s` per job. Both come from `_JOB_SQL`'s
`not_before` / `defer_s`, computed on the clock the column was written against.

**Parameters:**

| name | type | default | constraint | notes |
|---|---|---|---|---|
| `job_id` | string | — | | Omit to list recent jobs. |
| `video_id` | string | — | | Alternative lookup: the most recent job for that video. |
| `state` | enum `all\|active\|failed\|done` | `active` | | List mode filter. |
| `limit` | int | `5` | clamped 1..20 | List mode. |

**Return shape (single job):**

```
job_7f3a29b1c04d · state: transcribing · 41% · started 2026-08-08T16:58:12Z (3m 04s ago)
Video: Qk7mF2xLp0A "Flash Attention 3 walkthrough" — GPU MODE (1:04:11)
  download   done   18s
  transcribe running  41%  (whisperX large-v3, ~2m 10s remaining)
  keyframes  pending
  ocr        pending
  embed      pending
Queryable now: nothing (transcript is written on stage completion, not streamed).
next: job-status job_id="job_7f3a29b1c04d" again in ~60s.
```

**Return shape (list mode):**

```
Jobs: 3 active, 1 failed (filter state=active) · 1 of the active jobs are deferred, 2 running
job_7f3a29b1c04d  transcribing  41%  Qk7mF2xLp0A  Flash Attention 3 walkthrough
job_2b81c40dd7e9  embedding     88%  9dRk2XcVbNw  Speculative decoding, explained
job_c19aa4e2b530  queued         0%  (playlist, 12 videos)  deferred until 2026-08-09T22:00:00Z
job_5d0e77af1cc2  failed         —   HcQ8pL3vN1s  yt-dlp: video unavailable (private)
next: index-video url="…" force_reindex=true to retry a failed job.
```

**The "Queryable now" line says what the job produced**, and is derived, never
assumed. It printed "everything from this job" on every `done` job until
2026-08-08, including one whose only item had been skipped and which had indexed
nothing at all:

```
Queryable now: the 12 video(s) this job indexed (1 failed, 2 skipped).
Queryable now: nothing — this job indexed no video.
skipped:
  https://youtu.be/Qk7mF2xLp0A: aB3dEfG7hIj is already claimed by job_2b81c40dd7e9
next: index-video url="…" force_reindex=true to index it now.
```

The payload carries `n_items`, `n_done`, `n_failed`, `n_skipped` and
`n_cancelled`; on a terminal job the four counts sum to `n_items`, so a job that
did nothing cannot read as one that did. `note` carries the job's own
`E_NOTHING_INDEXED` explanation when there is one.

**The stage table is read off `video_stages`, never inferred from the item.**
Only `fetch` and `stt` are load-bearing enough to fail a video; OCR, keyframes
and either embedding leg soft-fail so the rest of the video stays searchable
(§the pipeline). That is the right call and it was invisible: the item went
`done`, and the renderer printed all five wire rows `done` because the item was.
A stage that failed now prints `failed` on the row it failed, and the payload
carries **`n_degraded`** and **`degraded_stages`** beside the four counts —
`n_done` alone cannot tell "indexed" from "indexed without its frame search".

**One unreadable frame is not a failed stage.** The worker types its refusals
(`{"error": {message, type}}`, worker/openapi.json): `invalid_image` blames one
uploaded file, `invalid_input` and `invalid_media` blame the request. A frame the
worker cannot decode is skipped and the stage carries on `done` — the other 599
frames keep their on-screen text and their vectors — with the count and the
ordinals recorded on the stage row and in the job log, so `done` never quietly
means "most of it". A request-level refusal, an untyped 4xx, or a batch where
*every* frame was refused still fails the stage, because there is no partial
success there to protect.

A degraded video is **not** "already indexed" for `index-video`: resubmitting it
without `force_reindex` creates a job that re-runs its failed stages and leaves
the finished ones alone (that is `_should_run`, unchanged), and the response
names the stages it will resume. Before this it short-circuited, so the only way
back to the missing channel was `force_reindex` — which redoes the six stages
that were fine.

It also carries **`item_errors`**, the typed item codes counted
(`{"E_RATE_LIMIT": 1}`), because the job-level `error_code` is one summary and a
batch has as many causes as it has items. **`error_code` survives partial
success**, and `E_RATE_LIMIT` outranks every other code: a job that indexed nine
of ten videos and was throttled on the tenth is not a clean `done`, and an
unattended driver reading a null code is a driver that keeps hammering. The
runner sets it the moment it backs off, so it is there even when the retry
succeeded and no item ended up `failed`.

Failure detail carries the actionable cause, never a stack trace:

```
job_5d0e77af1cc2 · state: failed at stage "download" · 2026-08-05T09:12:44Z
error: yt-dlp reported "Private video. Sign in if you've been granted access."
This is not retryable without credentials. next: pick a different URL, or configure
YTDLP_COOKIES on the server.
```

**Token discipline.** List mode capped at 20 jobs; stage table is fixed-size (5
rows); error text capped at 400 chars with the yt-dlp/worker tail preserved (the
useful end); no logs are ever returned through MCP.

**Errors:** `E_UNKNOWN_JOB` ("call job-status with no arguments to list recent
jobs"), `E_BAD_PARAM`.

**Annotations:** `{title: "Check indexing job", readOnlyHint: true,
idempotentHint: false, openWorldHint: false}` — the one read tool where repeated
calls legitimately return different answers, so it must not be cached (§3.9).

---

### 4.9 `tag-video`

**Purpose:** curate the corpus — add or remove namespaced tags that then scope
`search`, `list-videos` and `corpus-summary`.

Tags are the corpus product's spine: they are how a library becomes collections,
and how `include_related` co-occurrence has anything to co-occur with. Something
has to write them, and making that a model-callable tool means the user can say
"tag everything from this playlist as `series:gpu-mode`" in the same conversation
where they discovered the playlist.

**Description (ships verbatim):**

```
Add or remove tags on an indexed video. Tags are namespaced — topic:, person:,
project:, source:, lang:, series: — and are used as filters by search,
list-videos and corpus-summary.

USE WHEN: the user asks to organise, label, or collect videos, or explicitly
approves a tag you proposed.

DO NOT USE: to tag speculatively. Never invent tags the user did not ask for —
a corpus with 400 machine-generated topic tags is worse than one with none.
Check existing tags first (corpus-summary include_tags=true) and reuse them rather
than coining near-duplicates.

Both add and remove are idempotent: adding an existing tag or removing an absent
one succeeds and reports no change.
```

**Parameters:**

| name | type | default | constraint | notes |
|---|---|---|---|---|
| `video_id` | string \| string[] | — | **required**, ≤ 50 ids | Batch tagging in one call. |
| `add` | string[] | — | ≤ 10 tags, §3.7 regex | |
| `remove` | string[] | — | ≤ 10 tags | |
| `dry_run` | bool | `false` | | Reports what would change. Useful before a 50-video batch. |

At least one of `add`/`remove` required.

**Return shape:**

```
Tagged 12 videos.
  added:   series:gpu-mode (12 videos, 11 new / 1 already present)
           topic:cuda (12 videos, 9 new / 3 already present)
  removed: topic:misc (4 videos)
Corpus now has 114 distinct tags across 6 namespaces.
next: search q="warp divergence" tags="series:gpu-mode" limit=5
```

**Token discipline.** Fixed-size response regardless of batch size — counts and at
most 10 tag lines, never a per-video echo. `dry_run` output is the same shape.

**Errors:** `E_UNKNOWN_VIDEO` (names which ids; **partial batches do not apply** —
the whole call is one transaction), `E_BAD_PARAM` (invalid namespace or format,
lists the valid namespaces and the regex).

**Annotations:** `{title: "Tag a video", readOnlyHint: false, idempotentHint: true,
openWorldHint: false}`.

---

## 5. Resources

Three, deliberately — the same count screenpipe settled on. Reads stay Tools:
their proposal to migrate reads to Resources (#4863) died as stale, and client
support for resources is still far behind tools.

### 5.1 `vidtheque://corpus` — the browsable library

`mimeType: text/tab-separated-values`. The cheap unfiltered view of the library, so
a client that surfaces resources lets the user *see* what is indexed without
spending a tool call. Capped at 200 rows, most recently published first.

```
# vidtheque corpus · 312 videos · 486h 12m · generated 2026-08-08T17:02:11Z
video_id	title	channel	published	duration	coverage	tags
Qk7mF2xLp0A	Flash Attention 3 walkthrough	GPU MODE	2026-08-02	1:04:11	tof	topic:attention,series:gpu-mode
9dRk2XcVbNw	Speculative decoding, explained	Trelis Research	2026-07-29	0:38:20	tof	topic:inference
…
# showing 200 of 312 — narrow with the list-videos tool (channel=, tags=, q=, published_after=) · read vidtheque://guide for the tool flow
```

The footer is the `format=outline` lesson applied to a resource: say what you
truncated and name the tool that narrows it. The pointer to `vidtheque://guide`
was added 2026-08-09: `vidtheque://corpus` is the resource a client is most
likely to surface on its own, which makes it the one place a model reliably
reads before it knows the guide exists.

### 5.2 `vidtheque://context` — precomputed timestamps and corpus facts

`mimeType: application/json`. Exists so the model never does date arithmetic —
screenpipe's version is the direct ancestor, and their bug #3124 (advertised
relative dates silently returning nothing) is what date-math-by-LLM looks like
when it fails.

```json
{
  "current_time": "2026-08-08T17:02:11Z",
  "timezone": "Europe/Paris",
  "timestamps": {
    "today_start": "2026-08-08T00:00:00Z",
    "yesterday_start": "2026-08-07T00:00:00Z",
    "one_week_ago": "2026-08-01T17:02:11Z",
    "one_month_ago": "2026-07-08T17:02:11Z",
    "one_year_ago": "2025-08-08T17:02:11Z"
  },
  "corpus": {
    "videos": 312,
    "total_duration_seconds": 1750320,
    "first_published": "2019-04-02",
    "last_published": "2026-08-02",
    "last_indexed": "2026-08-07T19:41:03Z",
    "active_jobs": 5,
    "running_jobs": 0,
    "deferred_jobs": 5,
    "deferred_until": "2026-08-09T22:00:00Z",
    "data_status": "deferred"
  },
  "channels_top": ["GPU MODE", "Yannic Kilcher", "3Blue1Brown", "Andrej Karpathy"],
  "tag_namespaces": ["topic", "person", "project", "source", "lang", "series"],
  "id_formats": {
    "video_id": "11-char YouTube id, e.g. kCc8FmEb1nY",
    "frame_id": "<video_id>-<5-digit keyframe ordinal>, e.g. kCc8FmEb1nY-00703",
    "job_id": "job_ + 12 hex"
  },
  "deep_link_format": "https://youtu.be/<video_id>?t=<seconds>",
  "resources": ["vidtheque://corpus", "vidtheque://context", "vidtheque://guide"],
  "limits": {
    "search.limit": [1, 50],
    "search.max_per_video": [1, 20],
    "list-videos.limit": [1, 100],
    "get-frames.limit": [1, 12],
    "get-frames.frame_ids": [1, 12],
    "get-frames.max_text_chars": [120, 2000],
    "get-segment-context.window": [5, 300],
    "out_of_range": "clamped silently — read the printed count, not the one you asked for"
  },
  "features": {"diarization": true, "ocr": true, "frame_embeddings": true},
  "server_version": "0.1.0"
}
```

`active_jobs` used to be the only job number here, and `data_status` was derived
locally as "`indexing` if `active_jobs` else `ok`" — one of the three
contradicting answers §4.3 documents. Four numbers replace it: `active` is
queued + running, `running_jobs` is what is executing, `deferred_jobs` is the
subset held behind a future `jobs.not_before`, and `deferred_until` is when the
earliest of them resumes (`null` when none are). `data_status` is the same word
`corpus-summary` prints, from the same derivation — this resource is the first
call of most sessions, so it is the payload that can least afford its own
opinion.

`tag_namespaces` is **conditional** (§3.7): the namespaces in use first, then the
reserved list, or the reserved list alone when this deployment registers
`tag-video`. An untagged corpus on a read-only server omits the key rather than
advertising six namespaces nothing can filter on.

`resources` and `limits` were added 2026-08-09. Both exist because the clamps are
silent by contract (§3.4) and a caller cannot tell a clamp from a complete answer
after the fact: a bench agent asked `search` for `limit=500`, got 50, and had no
signal anywhere in the text payload that it had been narrowed
(`research/mcp-design-bench-2026-08-09.md` §D6). Publishing the caps ahead of the
call is the cheap half of that fix; printing a `note:` when a clamp actually
binds was deferred on 2026-08-09 and **shipped for `search` on 2026-08-10**,
after a second vendor's agent filed it independently three times in one run
(terra eval §4.12):

```
note: clamped server-side: limit=500 → 50. The caps are in vidtheque://context; page with offset instead of raising limit.
```

It prints only when a clamp moved a number the caller actually sent — a default
that was never sent is not a clamp, and a payload that says so on every call is
the token cost this line exists to avoid. `list-videos` prints the identical
line, from the identical rule (later the same day): one of the two paging tools
announcing its clamps is the "one tool, two standards" shape §3.5 exists to
forbid. The guide's "clamped silently" table above is therefore now about
*values*, not about silence — the cap is still applied without refusing the
call, and the payload says it happened.

### 5.3 `vidtheque://guide` — progressive disclosure

`mimeType: text/markdown`. Copied nearly verbatim in *shape* from
`screenpipe://guide`, which is the single most transferable artefact in that repo.

**Two of its sentences are deployment-dependent** (§3.8). The text below is the
full deployment's; a server that masks the write tools swaps the "Adding to the
library" paragraph for *"This server is read-only: it exposes no tool that adds,
re-indexes or tags a video…"* and the "the answer is index-video" rule for
*"…say so plainly — this read-only server cannot add it, and a plausible answer
from memory is not an answer from this corpus."* Nothing else changes;
`tools/resources.py` renders both from one template so they cannot drift.

```markdown
# Using vidtheque

A persistent, searchable index of videos the user has chosen to keep. Work from
the top down — each step narrows what the next one has to read.

| Step | Tool | When |
|---|---|---|
| 1 | corpus-summary | "what's in the library?", "do I have anything on X?", and after any empty search |
| 2 | search | you need specific words, claims or visuals. START limit=5 |
| 3 | video-summary | you have a video_id and need its structure or a timestamp to aim at |
| 4 | get-segment-context | you have (video_id, t) and need the actual words |
| 5 | get-frames | text is not enough and you have frame ids. return="url" unless you render images |

Adding to the library: index-video → job-status. Nothing is searchable until the
job reports "done".

Step 3 is the one people skip. When the question is *where* a video discusses
something, `video-summary`'s chapter list usually names the moment in one call —
faster than walking `get-segment-context` windows outward from a search hit.

## Resources

There are exactly three, and this is the list:

- `vidtheque://guide` — this document.
- `vidtheque://corpus` — the whole library as TSV, 200 rows, newest first.
- `vidtheque://context` — JSON: current time, precomputed date boundaries,
  corpus counts, id formats, and the server-side limits below.

There are no other URIs. `vidtheque://video/<id>` and the like do not exist —
drill down with tools, not with invented resource URIs.

## Server-side limits

Values outside these are **clamped**, not rejected: asking for more does not get
you more, it gets you the cap. `search` prints a `note:` when a clamp moved a
value you sent (§5.2); the other tools are still silent, so read the printed
count, never the number you asked for.

| Parameter | Range | Default |
|---|---|---|
| `search limit` | 1–50 | 10 |
| `search max_per_video` | 1–20 | 3 |
| `list-videos limit` | 1–100 | 20 |
| `get-frames limit` (span mode) | 1–12 | 3 |
| `get-frames frame_ids` | ≤ 12 ids | — |
| `get-frames max_text_chars` | `0` or 120–2000 | 300 |
| `get-segment-context window` | 5–300 s | 45 |

To get past a cap, page with `offset` — the pagination line tells you the next
one. To check what you actually got, read the printed count, never the number
you asked for.

## Rules

- **Never fabricate ids or timestamps.** Only use video_id, frame_id, cue_id and
  t values that appeared in an actual result. A plausible-looking YouTube id that
  came from your memory is not in this corpus.
- **This searches only what is indexed.** It is not the YouTube catalogue. If
  something is missing, the answer is index-video, not a guess.
- Two time axes: `published_after`/`published_before` choose videos by upload
  date; `t_start`/`t_end` choose seconds inside a video. They are not
  interchangeable, and neither is the pagination `offset`.
- `channel` and `video_title` are case-insensitive substrings. The tag filter is
  `tags=` — plural, comma-separated, AND semantics. `tag=` is not a parameter,
  and like any other unknown name it is a typed `E_BAD_PARAM` that tells you the
  right one.
- Ordering defaults to relevance. Pass `order=recency` only if the user asked for
  "latest" or "newest".
- Start with `limit=5` and `max_text_chars=500`. Raise them when the first page
  proves the query is right.
- `max_text_chars=0` opts out of truncation entirely.
- Auto-generated captions are noisy: unusual spellings, no punctuation, wrong
  proper nouns. Prefer two or three words over an exact long phrase, and check
  `get-segment-context` before quoting anything verbatim.
- Every timestamped result carries a `https://youtu.be/<id>?t=<s>` link. Give the
  user the link, not just the timestamp. The link is deliberately **2 s early**:
  `?t=` is the matched moment minus a lead, so the player has seeked by the time
  the words begin. The two-second disagreement with the payload's own numbers is
  the lead, not a bug.
- `video-summary` and `get-segment-context` print the compact form: a bare
  `?t=<seconds>` beside each chapter, key text, transcript line and on-screen
  line. The citation for THAT line is the video's `https://youtu.be/<id>` plus
  THAT `?t=`. Never reuse the window header's link for a line further down the
  window — that is how a quote ends up 27 s off the words you quoted.
- A `search` transcript result is a *segment*: `start`–`end` is the passage,
  `match at` (`match_start`) is the moment inside it that matched, and that is
  what the link points at. Quote from around `match_start`, not from the top of
  the segment. It does not move when you change `limit`.
- `search` never returns images. Frame ids do; `get-frames` turns them into URLs.
- Read the pagination line: `Results: 10/~40+ (use offset=10 for more)` tells you
  your next call.
- A `note:` line means a leg was skipped and why. `all` always means all: a
  missing leg is always announced, never silently dropped.
- Read the `Legs:` counts, and the sub-legs in the parentheses:
  `transcript 130 segments (fts 369 cues · vec 123/800 chunks)`. **The three
  numbers are three units and they do not add up** — the leading one is what
  the leg contributed to the ranking, the other two are the candidates behind
  it. **`fts 0`** next to on-screen hits means the phrasing differs, not that
  the topic is unspoken — slides write `hasFather`, `owl:FunctionalProperty`,
  `CVE-2026-22812`; speech says "has father", "functional property". Re-search
  the spoken phrasing, or open `get-segment-context` at the top on-screen hit.
  `vec 123/800` is the semantic sub-leg: 123 of the 800 nearest chunks were
  near enough to this query to be ranked at all — the other 677 were "nearest",
  not "near". `vec 800/800` means the relevance band kept every one of them:
  the pool is as wide as the KNN, so read the scores, not the count.
- **`fts 0` says nothing about titles: the one place `search` cannot find a
  phrase is the title bar.** Titles, descriptions and channel names are not in
  the searched index, so a phrase that lives only in a video's title reads
  `fts 0` and leaves the ranking to the semantic leg — which is how a talk
  *named* after the phrase you typed can rank below one that is not. When that
  happens the payload names the matching titles in a `note:`. `video_title=`
  is the parameter that filters by title, on `search` and on `list-videos`.
- **On-screen text is a flat reading-order join, and it is capped per frame.**
  Tables, code, bullet lists and quote/attribution pairs come back unscrambled
  from the layout that made them readable, and OCR mangles digits and bullet
  glyphs (`8.8` → `8.&`, a rank `1 ●` → `10`). When the answer depends on which
  value sits in which cell, or on a number, read the image: `get-frames`
  `return="url"` and open the URL. `get-frames max_text_chars=0` gives the
  frame's every line in reading order — but the picture is still the only place
  the layout survives.
- `get-frames limit` bounds the `video_id` span mode only. Ids you name are all
  fetched, up to 12, in the order you asked for; a bad one comes back on a
  `failed:` line rather than vanishing.
- Use only parameter names a payload printed or the tool schema lists. An
  unknown name is rejected with `E_BAD_PARAM`, which names the parameter you
  probably meant and lists the tool's full set — so a call that returns results
  applied every argument you sent, and a call that did not says which one it
  could not.
```

**This block is now the shipped text, synced 2026-08-09** — it used to drift
(it still said `offset_start`/`offset_end` after DECISIONS.md renamed the axis to
`t_start`/`t_end`). Four sections were added the same day from the design bench
(`research/mcp-design-bench-2026-08-09.md`): the resource list and the "there are
no other URIs" line (§D1), the server-side limits table (§D6), the
spoken-vs-on-screen phrasing rule (§D7), and the OCR-is-a-flat-join rule with its
escalate-to-the-image instruction (§D3). The guide is where a rule belongs when it
is true of more than one tool — that is the whole reason DECISIONS.md lifted the
shared rules out of the nine descriptions.

---

## 6. Deliberately deferred

| Thing | Why not v1 | Sketch if it lands |
|---|---|---|
| **Channel/playlist subscriptions** | The tool budget is the point (screenpipe's 28 tools are a warning, not a model). Subscriptions are *configuration* with a cron behind them, not something a model needs mid-conversation — and they are three tools (`subscribe`, `list-subscriptions`, `unsubscribe`) for one workflow, pushing the surface to 12. `index-video expand=channel_recent` already covers "catch me up on this channel" on demand. | `subscribe(url, expand, tags, max_items, check_interval)` → subscription id; cron enqueues new uploads as normal jobs; `list-subscriptions` folds into `corpus-summary include_subscriptions=true` rather than being its own tool. |
| **`get-clip`** | An ffmpeg cut is a slow, storage-producing, mostly-redundant operation: `youtu.be/<id>?t=<s>` already sends a human to the exact moment, and a model cannot watch an MP4. It also drags in retention policy and a second signed-URL kind. | `get-clip(video_id, start, end, max 120s)` → async job → signed URL, same job machinery as indexing. |
| **Markdown export** | Real product value (Obsidian, "data never hostage") but it is a *user* workflow, not a model one, and it is the biggest single payload the server could emit. Belongs on the HTTP API and any future web UI. | `GET /videos/<id>/export.md` — no MCP tool. |
| **Speaker identity management** (`list-unnamed-speakers`, `update-speaker`, `merge-speakers`) | Three tools for a curation workflow that only pays off once diarization is on across a large corpus. `search speaker=` and `video-summary include_speakers` cover reading; writing can wait. | Straight port of screenpipe's trio if it earns its place. |
| **Permissions / row-level filtering** | Single-owner corpus. The transferable parts are noted for later: server-authoritative policy from the credential (never a model-settable field), enforcement at both middleware and post-query row level, and *no total leakage* when restrictions are active. | — |
| **`format=json`** | Model-facing JSON is strictly worse than TSV (−73%) and worse than text for prose. Structured consumers read `structuredContent`. | — |

---

## 7. Worst-case response budget

Every tool has a bounded worst case. This table is the thing to re-check when a
parameter gets added.

| Tool | Worst case at ceiling settings | What bounds it |
|---|---|---|
| `search` | ~50 items × (20,000 + 220) chars → **capped at 60,000 by the response cap** | `limit≤50`, `max_text_chars`, response cap, candidate cap 5000/leg |
| `list-videos` | 100 rows × ~180 chars ≈ 18,000 | `limit≤100`, TSV, 120-char cells |
| `corpus-summary` | ~3,500 | fixed section caps (50/100/25) |
| `video-summary` | ~6,000 | 50 chapters + 30 key texts + 30 OCR × 1,200 chars, response cap |
| `get-segment-context` | ~22,000 (`max_text_chars=20000` + 1,200 OCR) | window ≤300s **and** char budget |
| `get-frames` (`url`) | 12 × ~400 ≈ 5,000 at the default; 12 × ~2,000 ≈ 25,000 at `max_text_chars=2000`, and one full frame's OCR per frame at the `0` opt-out | ≤ 12 ids (`frame_ids` **or** `limit`), OCR 300/frame by default |
| `get-frames` (`image`) | 4 images / 6MB + ~1,600 chars | inline cap independent of `limit` |
| `index-video` | ~1,200 | ≤10 titles echoed |
| `job-status` | ~2,500 | ≤20 jobs, fixed stage table, 400-char errors |
| `tag-video` | ~800 | counts only, no per-video echo |

---

## 8. Open questions for Tom

These are the forks where I could argue either side. Everything else in this
document is a decision, not a proposal.

1. **`offset_start`/`offset_end` collides with `offset` (pagination).** Same
   prefix, completely different meaning, in the same parameter list — and models
   confuse near-identical names more reliably than they confuse different ones.
   The handoff fixed these names, so I used them, but the alternatives are
   `t_start`/`t_end` (short, matches `get-segment-context`'s `t`), or
   `video_time_start`/`video_time_end` (verbose, matches `order=video_time`), or
   renaming the pagination one to `page_offset`. Worth settling before the guide
   and every description hard-code it.

2. **`order=video_time` without a single-video scope: error or group-by-video?**
   I chose a typed `E_ORDER_SCOPE` error because "chronological across 300 videos"
   is meaningless. The alternative is to accept it and sort by
   `(video published desc, offset asc)`, which is defensible for a two- or
   three-video scope and never errors. Erroring teaches, permissiveness flows.

3. **Does `tag-video` make the v1 cut, or do tags arrive only via
   `index-video tags=`?** Tags are the corpus product's spine and `include_related`
   needs them, but it is the only pure-write tool and "never invent tags" is a
   guardrail I do not fully trust a model to hold. Dropping it takes v1 to 8 tools
   and makes tagging an `index-video`/HTTP-API concern.

4. **Subscriptions in v1 after all?** I deferred them (§6) on tool-budget grounds,
   and `index-video expand=channel_recent` covers the on-demand case. If the
   "watch-later replacement" framing is the product story you want to *lead* with
   rather than a v1.1 addition, they should be in — the cost is +1 to +3 tools and
   a cron surface.

5. **`corpus-summary` and `list-videos` overlap at the edges.** Two tools, both
   answering "what's in the library", separated by aggregate-vs-rows. A single
   `corpus` tool with `view=summary|list` would be 8 tools instead of 9 — but it
   would also merge two very different parameter sets, and screenpipe's split
   (`activity-summary` vs `search-content`) has held for 20 months. I kept them
   split; say if you want the merge.

6. **Signed frame URLs vs bearer-only.** Signed URLs (HMAC + 1h expiry) are the
   only thing a browser-side renderer can actually fetch, but they are a capability
   URL: anyone holding one reads that frame for an hour, no OAuth. Alternatives:
   shorter TTL (5 min, likelier to expire mid-conversation), or bearer-only
   (correct, but images never render in any client that does not proxy the fetch).
   I chose 1h signed. This is the one security-shaped decision in the surface.

7. **Should `search` fall back to the vector leg when FTS returns nothing?** A
   silent semantic fallback rescues bad keyword queries ("that video about the
   memory wall") but makes results non-explainable and hides a genuinely empty
   corpus. Options: never (current — the empty-result payload suggests a next
   query instead), automatic-with-a-`note:`, or an explicit `mode=keyword|semantic|hybrid`
   parameter. Related: whether `content_type=all` should *always* run hybrid text
   retrieval (BM25 + text embeddings fused by RRF) rather than BM25-only, which is
   what §3.10 currently specifies.

8. **FTS5 tokenizer: `unicode61` or `porter`?** screenpipe uses `unicode61`
   everywhere (no stemming) because screen text is full of compound identifiers.
   Our corpus is mostly spoken prose, where stemming ("cache"/"caching",
   "quantize"/"quantization") is a real recall win — but it hurts on code and CLI
   text in the OCR leg. Plausible answer: `porter` on the transcript FTS table,
   `unicode61` on the OCR one. That means two tokenizers and two ranking profiles
   to reason about in the fusion step.

9. **Description length.** The USE WHEN / DO NOT USE blocks above run 120–190
   words each; nine of them plus schemas is roughly 3.5–4.5k tokens of permanent
   context in every session. screenpipe spends far more (28 tools), but they are
   not competing with an agent's other MCP servers. If that is too much, the lever
   is moving the shared rules (two time axes, case-insensitivity, never fabricate
   ids) out of every description and into the `guide` resource alone — cheaper, but
   only works on clients that actually read resources.

10. **Diarization on by default?** `search speaker=`, `video-summary
    include_speakers` and the `E_FEATURE_DISABLED` path all exist for it, and
    whisperX gives it to us — but pyannote adds a model to the GPU lease dance and
    a licence-acceptance step to the setup docs. Ship v1 with `DIARIZE=0` and the
    parameters present-but-disabled, or `DIARIZE=1` and pay the setup friction?
