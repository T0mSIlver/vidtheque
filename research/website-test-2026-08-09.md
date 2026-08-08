# Public demo — real-browser QA, 2026-08-08/09

Target: the live demo instance on `http://127.0.0.1:8180/` (`VIDTHEQUE_PUBLIC_READONLY=1`,
`VIDTHEQUE_AUTH=none`, `PUBLIC_URL=http://127.0.0.1:8180`,
`DATA_DIR=/home/dev/vidtheque-demo-data`, 2 videos: `RjfbvDXpFls`, `Sir59K8ZDPU`).
Server process started 22:47; the static/API files it was serving are the ones dated
21:45–22:03 (`api.py`, `ask.py`, `static/*`). Driver: Playwright 1.62.1 / Chromium,
real page loads, no request interception except a forged `CF-Connecting-IP` on some
contexts to keep test traffic off the bucket reserved for the deliberate 429 probe.

**Scope note — the instance died mid-run.** At 22:57 the process on 8180 was replaced
(a `dev_stack.sh start` from another session; a different instance came up on 8100 with
`DATA_DIR=/home/dev/vidtheque-data`), and at the same minute the working-tree copies of
`public/api.py`, `public/ask.py` and `public/static/*` began being edited. Everything in
"Findings" and "Works well" below was observed in a browser before 22:57. The
[Not verified](#not-verified) section lists what the loss of the instance cost.

Ask budget: **6 requests to `/api/ask` total**, of which exactly **one** reached
OpenRouter (the end-to-end question). The other five were malformed bodies that are
rejected with 400 *before* the LLM call, used to drain the 5/min bucket for the
degradation test — so the money spent was one answer, ~11s, one model call chain.

Screenshots referenced below live in
`/tmp/claude-1000/-home-dev-work-website/fd2d97c8-90db-4661-b98f-f7672b649263/scratchpad/vidtheque-qa/shots/`
(session scratchpad, not committed).

---

## Findings, worst first

### S1-1 (ship blocker) — every query returns a full page of results; the zero-result state is unreachable

**Repro:** load `/`, search `qwertyuiopzxcv`.
**Observed:** status line reads `9 results`, nine hits render, none of them contains the
query, nothing is highlighted, `notes` is `[]` — the page says nothing about why these
results are here.

```
GET /api/search?q=qwertyuiopzxcv&limit=3
→ 9 hits, top score 0.0211, "notes":[]
GET /api/search?q=qwertyuiopzxcv&content_type=transcript
→ all three transcript blocks of RjfbvDXpFls, in corpus order
```

Same for the XSS payload string, and for `does this get refused?` (typed into the box by
accident during the degradation test — 9 results). The mechanism is the hybrid
FTS+vector RRF fusion (`db/queries.py`, `RRF_K = 60.0`): the vector leg has no minimum
similarity, so on a two-video corpus the k-nearest chunks are *always* the whole corpus.

Why it is a blocker and not a relevance nit: the first thing a sceptical visitor does to
a search demo is type garbage into it. Right now the demo answers garbage with confident,
timestamped, thumbnailed rows — which reads as "this thing returns the same results no
matter what I ask", the exact opposite of the claim the page is making. The
`nothing matched "…"` branch in `app.js` is dead code as shipped.

Cheapest honest fixes, in order of how little they change: (a) a score floor below which
the vector leg contributes nothing; (b) when the FTS leg matched nothing, emit a
`note:` the page already renders — "no exact matches; showing the closest passages" —
so the rows are labelled instead of implied.

### S1-2 (ship blocker) — an agent-facing truncation marker is rendered to visitors, and it names a parameter the public API refuses

**Repro:** search `opencode`. Four of the ten rows contain, mid-sentence:

```
…[1758 chars truncated — pass max_text_chars=0 for full text]…
```

Evidence: `shots/c-desktop-1440-dark.png`, `shots/c-mobile-375-light.png` (markers of
1703, 1758, 1794 and 513 chars visible on one screen).

Two things wrong at once. It is MCP-payload furniture leaking into a consumer page; and
the instruction it gives is impossible to follow here — `demo-site.md` §2 is explicit
that the facade has *no* `max_text_chars=0` opt-out, so the page is telling a human to
pass a parameter the endpoint would reject. The facade already re-shapes hits
(`_decorate_hit`); the marker should be replaced with a plain ellipsis before it reaches
the browser, or the page should render the head fragment only.

### S2-3 (high) — a results page ships ~0.9 MB of full-resolution JPEGs to fill 96×54 px thumbnails

The facade emits `/frames/<id>.jpg?w=320&q=70`, but the frames route "does not resize"
(`demo-site.md` §5) — `w` is accepted and ignored. Verified in the browser:
`img.naturalWidth × naturalHeight = 1280×720` for every thumbnail, while CSS renders
them at `96×54` (72×41 under 30rem).

Measured on the eight keyframes shown on the first page of `opencode`
(`/home/dev/vidtheque-demo-data/keyframes/`):

```
00021 231130   00029  99386   00031 105960   00032 110611
00040  60164   00050 108343   00072  58171   Sir…00043 133680
                                             SUM 907 445 B  (886 KB)
```

Corpus-wide sample: mean 123 KB/keyframe over 20 files. So the *document* is 21 KB and
the *images* are ~40× the pixels needed. On a public box this is also the rate limiter's
problem: the `frames` bucket is 120/min precisely because a visitor legitimately pulls
~10 images per screen, and each of those is 120 KB.

Fix is a real decision, not a nit: either generate a 320px derivative at index time, or
resize on the frames route with a cache. Worth taking before the demo is public.

### S2-4 (high) — ask answers render raw markdown as literal asterisks

**Repro:** ask *what CVE is shown in the opencode section?* (the one paid call).
**Observed** (`shots/d4-ask-answer.png`):

```
The CVE shown in the OpenCode section ("OpenCode booboos") is **CVE-2026-22812**,
displayed on a NIST National Vulnerability Database slide in Mario Zechner's talk
*Building pi in a World of Slop* [3][4].
```

The answer is correct — the expected `CVE-2026-22812`, with the CVSS vector and frame
citations. But `renderAnswer` inserts text nodes only (correct, and the reason there is
no XSS here), so `**`/`*` display verbatim. The system prompt says "plain prose, no
headings" and the model reasonably reads emphasis as prose. Either forbid markdown in
`SYSTEM_PROMPT`, or strip `**`/`*`/`` ` `` in `_answer()` — do not reach for a markdown
renderer, that would give up the no-`innerHTML` property that is currently doing real
work.

### S2-5 (high) — citation numbering has holes, and non-`[n]` bracket forms leak through the guard

Same answer. Citations shown: **[3]** and **[4]**. There is no [1] and no [2] anywhere on
the page. `Evidence.record` numbers every hit the model was *shown*, `_answer` returns
only the ones it *used*, so the visible sequence starts at 3 — which reads as a rendering
bug even though the links are right.

Worse, the last sentence ends:

```
…let any website you open access your OpenCode server [2's context snippet].
```

`_CITATION = re.compile(r"\[(\d{1,2})\]")` only strips exact `[n]` markers, so this
model-internal aside survives, and it points at a citation that was deliberately not
rendered. The integrity property the module advertises ("a marker pointing at nothing is
stripped rather than rendered as a dead link") holds for the form it matches and not for
its neighbours.

Suggested: renumber the *used* citations 1..n at response time (the link target is what
matters, not the model's internal index), and widen the strip to `\[[^\]]{0,40}\]` for
anything that is not a resolved citation.

### S3-6 (medium) — filter chips and the mode switch expose no state to assistive tech

`aria-pressed` is `null` on all four filter chips and both mode pills at every point in
the run (checked after each chip click and after switching to ask mode). The CSS even has
a `button.ghost[aria-pressed="true"]` rule that nothing sets. State is conveyed by accent
colour + font-weight 600 only. `demo-site.md` §6's own accessibility floor says "no
colour-only state"; a screen-reader user cannot tell which channel is filtered or which
mode they are in. `aria-pressed` on all six, or `role="radiogroup"` on `#chips`, is the
whole fix.

### S3-7 (medium) — head/meta hygiene: no `<h1>`, no OG/Twitter/canonical/theme-color

```
title:  "vidtheque — search a video corpus"        ✓
meta:   charset, viewport, description, color-scheme  (that is all)
link:   stylesheet, icon (data: URI SVG 🎞️)
headings on the page: H2 "Add this corpus to your own agent"   ← the only heading
```

The wordmark is an `<a class="wordmark">`, so the document has no `h1` and its only
heading is the MCP panel's. Absent, for the polish pass: `og:title`/`og:description`/
`og:type`/`og:url`/`og:image`, `twitter:card`, `<link rel="canonical">`, `theme-color`.
Nothing is broken; a link to this demo in Slack or on Twitter will render as a bare URL.

### S3-8 (medium) — clearing the box leaves a stale `?q=` in the URL

**Repro:** search anything, clear the input (or type spaces), press Enter.
**Observed:** results and status clear; `location.search` still reads
`?q=zzzqqqxyzzy+nothing+here`. `syncUrl` is only called on the non-empty path, and
`runSearch`'s early return skips it. Copying the URL at that moment shares a search the
visitor just abandoned.

### S3-9 (medium) — an unrecognised `?type=` leaves every chip unselected

**Repro:** `/?q=opencode&type=<script>alert(1)</script>`.
**Observed:** the search runs correctly as `all` (10 results, no injection — see
[works well](#works-well)), but the boot loop does
`chip.classList.toggle("is-on", chip.dataset.type === type)` over all four chips, so an
unknown value turns them *all* off. `chipsOn: []` — the filter row shows no active state
while `state.contentType` is `all`. Screenshot `shots/b3-junk-type.png`.

### S4-10 (low) — `?t=` is always two seconds before the timestamp on screen, with nothing saying so

Verified across ten links; it is exactly `floor(start) − 2` every time:

| shown | href |
|---|---|
| 2:01 | `youtu.be/RjfbvDXpFls?t=119` |
| 4:17 | `?t=255` |
| 7:13 | `?t=431` |
| 4:32 | `?t=270` |
| 10:42 | `?t=640` |
| 4:37 (ask citation [3]) | `?t=275` |

This matches `demo-site.md` §2.1's own example (`start 12.0` → `t=10`) so it is the
intended lead-in, and it is the right behaviour — you want the sentence to start. Noted
only because a visitor who checks will find `[4:37]` landing at 4:35, and no copy on the
page accounts for the difference.

### S4-11 (low) — `approx_total` can be smaller than the number of results actually returned

`GET /api/search?q=qwertyuiopzxcv&limit=3` → `"approx_total": 7`, but the same query at
`limit=10` returns 9 hits. Only surfaces in the UI through the `of ~N` suffix, which is
suppressed when `has_more` is false, so nobody saw it in the browser — but the bounded
count probe can undershoot the real count, not just overshoot.

### S4-12 (low) — over-long queries are only caught server-side

**Repro:** paste a 1080-char query, Enter.
**Observed:** `400` from `/api/search`, status line reads
`q is limited to 512 characters.` — clean, correct, keeps the query in the box. Two nits:
the browser logs a red console error for the 400 (the only console error of the whole
run), and the input has no `maxlength`, so the round trip is avoidable.

### S4-13 (low) — assorted small ones

- **Empty submit in ask mode does nothing at all** — no message, no status change
  (`runAsk` returns early). In search mode the same action at least clears the pane.
- **Focus rings are inconsistent**: `#q`, every `button` and every `.chip` get the 2px
  accent ring; the two prose links (`#repo`, `.wordmark`) fall back to the UA's
  `1px auto`. Tab order itself is correct and complete (input → Search → 4 chips → 2
  modes → Copy → repo → wordmark), autofocus lands on `#q`, and Enter activates a chip.
- **`/api/videos` is implemented and never called** — `app.js` only touches
  `/api/meta`, `/api/search`, `/api/ask`. The empty state is a single line ("2 videos
  indexed.") with nothing to click; the library endpoint is right there.
- **Frame/OCR snippets are raw pipe-joined OCR** ("`14:38 | OpenA | OpenCode context
  handling | EARENDIL | NEngh | runpo | ANEn | thdxr on Jul 5, 2025 …`"). Honest, and
  unreadable; on a demo page it is most of the vertical space of a `frame` row.
- **Every row repeats the same video title** — 9 of 10 rows on `opencode` read "Building
  pi in a World of Slop — Mario Zechner". No per-video grouping or de-emphasis.
- **The rate limiter trusts a forgeable header by default.** `CF-Connecting-IP` wins over
  the socket address, and this deployment has no Cloudflare in front of it: sending a
  different value gave a fresh 30/min bucket every time (that is how this test kept its
  traffic off the ask bucket). Documented behaviour (`demo-site.md` §4.3) and the right
  default *behind Cloudflare*; if the demo is ever exposed without an edge in front,
  `VIDTHEQUE_TRUSTED_IP_HEADER=` is the one-line fix and should be part of the deploy
  recipe rather than a caveat in a design doc.

---

## Works well

Ranked by how much it would have hurt to get wrong.

- **No XSS, and the defence is structural.** `<img src=x onerror=…>`,
  `<script>window.__xss2=1</script>`, `"><svg onload=alert(1)>` — typed into the box, and
  again through `?q=` on a cold load. No dialogs, no injected nodes, no globals set, the
  only `<script>` in the document is `app.js`. Everything from the corpus and the model
  goes in as a text node; there is no `innerHTML` in `app.js` and it shows.
- **Clean console and network.** Across ~20 page loads, six viewport/scheme combinations,
  the ask flow and the 429 path: zero page errors, zero failed requests, and exactly two
  console entries, both the browser's own log of a deliberate 4xx (the 512-char query and
  the 429).
- **Search → results → pagination is correct.** `opencode` → `10 results of ~12`, "More
  results" appends to 12 and removes itself; status updates to `12 results`.
- **The filter chips genuinely change the query**, not just the view: transcript 3, ocr 3,
  frame 6, all 10, each with `sources` containing only that channel, and `?type=` written
  to the URL and read back on load.
- **Every result row is a real link** — `<a href="https://youtu.be/…?t=…" target="_blank"
  rel="noopener noreferrer">` wrapping the whole row, so middle-click and open-in-new-tab
  work. Deep links point at the right video and second (see S4-10 for the 2s lead-in).
- **Thumbnails and the placeholder fallback both work.** Transcript-only hits with no
  keyframe render the muted `spoken` / `screen` / `frame` placeholder rather than a broken
  image, and the `img.onerror` → placeholder swap is wired.
- **Ask, end to end, was right the first time.** Question: *what CVE is shown in the
  opencode section?* → `CVE-2026-22812`, with the CVSS vector, in 11.0 s, `rounds` used,
  two frame citations rendered as accent superscript links into the video at 4:37 and
  4:32, plus the matching numbered source rows with working thumbnails
  (`shots/d4-ask-answer.png`). The "reading the corpus…" pane appears immediately and the
  submit button disables while in flight.
- **Degradation is exactly as designed** (`shots/d2-degraded-429.png`). Draining the ask
  bucket produced:

  ```
  HTTP/1.1 429   retry-after: 11   x-ratelimit-limit: 5   x-ratelimit-remaining: 0
  {"error":"E_RATE_LIMIT","message":"Too many requests — 5 per minute.",
   "retry_after_s":11,"bucket":"ask"}
  ```

  The pane renders `Too many requests — 5 per minute. Try again in 11s.` with a **Search
  instead** button that switches the mode pill, re-enables the chips, runs the query and
  hides the pane. The submit button is re-enabled in `finally`, so a refusal never leaves
  a dead form. Malformed bodies are refused `400` *before* any upstream call — which is
  what made a six-request budget enough to test both paths.
- **`?ask=1` arrives in ask mode and does not fire.** `/?q=what%20is%20pi&ask=1` →
  ask mode, question loaded, answer pane hidden, **0** requests to `/api/ask`. The budget
  guard works.
- **`/api/meta` matches what the page uses, field for field.** `mcp_url` is
  `PUBLIC_URL + /mcp` (`http://127.0.0.1:8180/mcp`), which is what `#mcp-url` shows, what
  the `claude mcp add --transport http vidtheque …` line embeds, and what the Copy button
  puts on the clipboard (read back via `navigator.clipboard.readText()`); the button
  flips to "Copied" and back after 1.6s. `ask_enabled: true` reveals the mode switch;
  `videos: 2` produces the "2 videos indexed." empty state; `repo` sets the footer link.
- **Layout holds everywhere tested.** 375 / 768 / 1440 × light / dark: no horizontal
  scroll at any size (`scrollWidth === clientWidth`), nothing overflowing the viewport,
  the controls row wraps, the copy row's `<code>` scrolls inside itself instead of pushing
  the page. Dark mode is a real second palette (`#131313`/`#e9e6e3`), driven by
  `prefers-color-scheme`, with no flash and no unstyled surface.
- **Keyboard-only use is fine.** Autofocus on the search box, complete tab order, the
  2px accent ring on every control, Enter submits the form and activates chips.
- **No external requests, ever.** Four requests on a cold load, all same-origin.

---

## Rough performance

Cold load (fresh context, empty cache, 1440×900):

| | |
|---|---|
| requests | 4 (`/`, `/static/style.css`, `/static/app.js`, `/api/meta`) |
| bytes | 21 069 (HTML 2 879, CSS 6 696, JS 11 216, meta 278) |
| TTFB | 3 ms |
| first contentful paint | 48 ms |
| DOMContentLoaded / load | 35 ms |
| interactive (MCP URL populated from `/api/meta`) | 68 ms |
| external hosts | none |
| `Cache-Control` | `public, max-age=300` on `/`, css, js; none on `/api/meta` |

Everything else is images: a results page for `opencode` adds ~886 KB of keyframe JPEGs
(S2-3), which is 97% of the page weight and the only number here that is not excellent.

Ask latency: 11.0 s wall clock from click to rendered answer, including the tool rounds.
Search felt instant throughout (sub-100 ms, not separately instrumented).

---

## Not verified

The instance was destroyed at 22:57, before these ran. All of them are cheap to redo
against a fresh `VIDTHEQUE_PUBLIC_READONLY=1` server:

- **The `search` bucket's 429 in the browser.** Only the `ask` bucket was exercised
  (S1/works-well above). The interesting case is that `/api/meta` shares the `search`
  bucket, so a visitor who is over the limit should get a page that cannot boot at all —
  `#mcp-url` stuck at `…`, mode switch hidden, "could not reach the server". That is a
  reading of `public/__init__.py:bucket_for` plus `app.js:boot`, **not** an observation;
  it deserves a browser check, and if it holds, a `/api/meta` exemption or a friendlier
  boot-failure state.
- The `frames` bucket (120/min) and whether a thumbnail 429 falls back to the placeholder.
- HTTP envelopes for `q` missing (`E_EMPTY_QUERY`), `content_type=bogus`,
  `limit`/`offset` clamping and non-numeric values, `GET /api/ask` (405), unknown
  `video_id`, missing frame ids, `/static/../` traversal, unknown paths, `/healthz`,
  `HEAD /`, `robots.txt`.
- Response security headers on `/` (none were sampled; `demo-site.md` §6 notes a CSP is
  a later addition — the page is already CSP-ready: one module script, no inline JS).
- Zero-result rendering, because no query could produce zero results (S1-1).
