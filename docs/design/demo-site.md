# The public demo — read-only mode, the `/api` facade, and the demo page

Status: **design, then implementation.** Written before the code, per the task
brief; the implementation follows it and any divergence is folded back here in
the same commit (CLAUDE.md's contract rule).

What this adds: a way to point a browser at a vidtheque instance and *see the
corpus work* — search it, and ask a question about it — without an MCP client,
without an account, and without the ability to change anything. It is one
deployment mode of the same server, not a second application.

Sources it must not contradict: `tool-surface.md` (the token-discipline rules
apply verbatim to the facade), `DECISIONS.md` (auth modes, frame URLs),
`index-schema.md` (nothing here writes).

---

## 1. The shape

One flag turns the whole thing on:

```
VIDTHEQUE_PUBLIC_READONLY=1
```

It is a *mode*, resolved once at app-construction time, exactly like
`VIDTHEQUE_AUTH` (`auth/modes.py`): one branch in `app.py`, never a per-route
conditional. Set it and four things change together:

| | off (default) | on |
|---|---|---|
| write tools (`index-video`, `tag-video`) | registered | **never registered** |
| `/api/*` | absent (404) | served |
| `/` | the MCP mount | the demo page |
| rate limiting on `/api/*` and `/frames/*` | none | token bucket per IP |

The read surface is untouched: seven read tools, three resources,
`/frames/<id>.jpg`, `/healthz`, and `/mcp` all behave exactly as they do in a
private deployment. The public instance is a *real* MCP server that anyone can
add to their own agent — that is the point of the demo, and why the "add this
corpus to your own agent" panel exists on the page.

The intended production combination is:

```
VIDTHEQUE_AUTH=none
VIDTHEQUE_PUBLIC_READONLY=1
PUBLIC_URL=https://vidtheque.example.com
VIDTHEQUE_PUBLIC_HOSTNAME=vidtheque.example.com
OPENROUTER_API_KEY=sk-or-…
```

but the flag is orthogonal to the auth mode by construction. `token` or `oauth`
plus the flag is a valid (if odd) combination: a credentialed read-only
deployment. The frontend then cannot call `/api` without a credential and says
so; nothing breaks.

### 1.1 Why "not registered" and not "registered but refuses"

A tool that exists and errors still costs the model context on every session,
still appears in `tools/list`, and still invites a retry loop. A tool that was
never registered is absent from `tools/list`, and a `tools/call` for it comes
back as the SDK's unknown-tool error — the model reads "there is no such tool"
and moves on. That is the honest description of a read-only corpus.

Implementation: `register()` in `tools/__init__.py` grows one optional
`hidden: frozenset[str]` argument, threaded from `build_mcp_server`. The
*policy* — which tools are write tools — is not spelled out in the tools
package; it is derived from the annotations already in the contract:

```python
WRITE_TOOLS = frozenset(n for n, a in ANNOTATIONS.items() if not a.read_only_hint)
```

so a tenth tool with `readOnlyHint: False` is masked the day it is added, with
no second list to keep in sync. `job-status` stays: it is read-only, it is how
a curious visitor sees that indexing is a real pipeline, and it exposes
nothing a job id doesn't already name.

---

## 2. The `/api` facade

The demo page is a browser. A browser cannot do an MCP session handshake, and
should not have to: the frontend wants JSON, one request, no protocol version
negotiation. So `/api` is a **facade over the same tool implementations** —
`tools/search.run`, `tools/library.list_videos`, `tools/segment.run` — reading
their `structuredContent` and re-shaping it for the page. Not a second query
layer. Every clamp, every note, every `has_more` is the one the MCP tool
computed.

Facade rules:

- **Token discipline carries over.** The facade passes its own, *tighter*
  bounds into the tools: `limit` clamped to 1..20 (MCP allows 50),
  `max_text_chars` clamped to 120..1000 with a default of 400. There is no
  `max_text_chars=0` opt-out on a public endpoint — the full-transcript escape
  hatch is for an owner's agent, not for anonymous traffic. `/api/ask`'s
  internal tools are tighter still (§3.2).
- **Typed errors survive the translation.** A tool returning `isError` carries
  a `structuredContent.code`; `errors.HTTP_STATUS` already maps every `E_*`
  code to an HTTP status, so the facade returns that status with
  `{"error": code, "message": …, "next": …}`. One table, two consumers.
- **Nothing new is queried.** The facade adds exactly two derived fields per
  result — a `thumb` URL and a `timestamp` clock string — both computed from
  data the tool already returned.
- **One string is rewritten, and only one.** `text.TRUNCATION_MARKER` ends in
  "pass `max_text_chars=0` for full text" — advice a browser cannot take, since
  the facade has no such opt-out. `api.demo_text()` rewrites it to
  `…[N chars cut]…` for `/api` consumers. The pattern is built from the
  template, not retyped, so a change in `text.py` cannot leave it silently
  matching nothing. Nothing else about the tool's text is touched: in
  particular the facade does **not** sanitise, because the page never parses
  corpus text as HTML (§6).

### 2.1 `GET /api/search`

```
GET /api/search?q=kv+cache&content_type=all&limit=10&offset=0
```

| param | values | default |
|---|---|---|
| `q` | ≤ 512 chars | required in practice (an empty `q` with no filter is `E_EMPTY_QUERY`) |
| `content_type` | `all` \| `transcript` \| `ocr` \| `frame` | `all` |
| `limit` | 1..20 | 10 |
| `offset` | 0..1000 | 0 |
| `channel`, `video_id` | passthrough filters | — |

```json
{
  "query": "kv cache",
  "content_type": "all",
  "results": [
    {
      "source": "transcript",
      "video_id": "kCc8FmEb1nY",
      "title": "Let's build GPT: from scratch",
      "channel": "Andrej Karpathy",
      "start": 12.0,
      "end": 14.8,
      "timestamp": "0:12",
      "text": "we cache the keys and the values at every new token",
      "link": "https://youtu.be/kCc8FmEb1nY?t=10",
      "frame_id": "kCc8FmEb1nY-00000",
      "thumb": "https://…/frames/kCc8FmEb1nY-00000.jpg?w=320&q=70",
      "score": 0.0312
    }
  ],
  "pagination": {"limit": 10, "offset": 0, "has_more": true, "approx_total": 38},
  "notes": ["note: …"]
}
```

`thumb` is `null` when the hit has no `frame_id` (a transcript-only hit in a
video with no keyframes). The page falls back to a text card, not a broken
image.

`notes` is the same `note:` array the MCP payload prints. The page renders it
in a muted line — "`all` means all" is a promise to a human too, and a search
where the frame leg was skipped should say so.

### 2.2 `GET /api/videos`

The library listing, straight from `list-videos`:

```
GET /api/videos?limit=50&offset=0&q=&channel=
```

`limit` clamped 1..50, default 24. Returns `{"videos": [...], "pagination": …}`
with the tool's records verbatim (`video_id`, `title`, `channel`, `published`,
`duration`, `coverage`, `tags`, `link`) plus `thumb` — the video's first
keyframe, when it has one.

### 2.3 `GET /api/meta`

What the page needs to render itself, and nothing else:

```json
{
  "name": "vidtheque",
  "version": "0.1.0",
  "mcp_url": "https://vidtheque.example.com/mcp",
  "auth": "none",
  "ask_enabled": true,
  "ask_model": "deepseek/deepseek-v4-flash-0731",
  "videos": 42,
  "limits": {"search_per_min": 30, "ask_per_min": 5, "ask_per_day": 50},
  "repo": "https://github.com/T0mSIlver/vidtheque"
}
```

`mcp_url` is derived from `PUBLIC_URL` — the same string `config.resource_url`
builds, so the copy button on the page and the OAuth `resource` can never
disagree. The page never hardcodes a hostname.

`ask_enabled` is false when no `OPENROUTER_API_KEY` is configured; the page
then hides the Ask toggle instead of offering a button that 503s.

---

## 3. `/api/ask` — the LLM mode

```
POST /api/ask   {"q": "how does paged attention reduce fragmentation?"}
```

A server-side agent loop against OpenRouter's OpenAI-compatible
`/api/v1/chat/completions`, with **two** internal tools and a hard round cap.
It exists to show the thing the corpus is actually for — an agent that answers
from timestamped evidence — to a visitor who has not wired up an MCP client.

```json
{
  "answer": "Paged attention keeps a block table … [1]",
  "citations": [
    {"n": 1, "video_id": "zduSFxRajkE", "title": "Making LLMs go brrr",
     "channel": "GPU MODE", "t": 13, "timestamp": "0:13",
     "link": "https://youtu.be/zduSFxRajkE?t=11", "thumb": "…",
     "source": "transcript", "text": "the block table keeps …"}
  ],
  "rounds": 2,
  "model": "deepseek/deepseek-v4-flash-0731"
}
```

### 3.1 The model

`OPENROUTER_MODEL`, default **`deepseek/deepseek-v4-flash-0731`** — Tom's
call (2026-08-08): the brief's "current free DeepSeek" no longer exists
(verified against `https://openrouter.ai/api/v1/models`: the DeepSeek family
is all paid, fourteen unrelated `:free` ids remain), so the demo runs the
paid-but-cheap pinned snapshot: $0.09/M in, 1M context, `tools` supported.
The per-day ask budget is the cost control. Free tool-capable fallbacks if
the account runs dry: `nvidia/nemotron-3-super-120b-a12b:free` (262k),
`google/gemma-4-31b-it:free`, `inclusionai/ling-3.0-tiny:free` (fastest).

**This is a one-line env change, not a code change** — which is the reason it
is an env var. Flagged for Tom in §7.

### 3.2 The two internal tools

The model never sees the nine-tool surface. It sees two, described in a handful
of words each, because a 4-round budget over a free model is not the place for
progressive disclosure:

| tool | args | what it runs | bounds |
|---|---|---|---|
| `search` | `query`, `content_type?` | `tools/search.run` | `limit=6`, `max_text_chars=300`, `max_per_video=2` |
| `get_segment_context` | `video_id`, `t` | `tools/segment.run` | `window=45`, `max_text_chars=1200` |

Both are handed to the model as the **text** block the tool already renders —
the model-readable form the whole contract is tuned for — never a JSON dump and
never a full transcript. The caps above are the facade's, tighter than the MCP
defaults, and they are server-side: the model cannot ask for more.

`get_segment_context` is the second tool for one reason: a search hit is a
sentence, and a good answer usually needs the sentence before and after it. One
drill-down round is the difference between "he mentions the block table" and an
answer that says what it does.

### 3.3 The loop

```
system + user
  ├─ round 1..4:  completion(tools=[search, get_segment_context])
  │                 └─ tool_calls?  → run them, append results, loop
  │                 └─ content?     → done
  └─ final:       completion(tool_choice="none")   ← forced answer
```

- **Max 4 tool rounds** (`VIDTHEQUE_ASK_MAX_ROUNDS`). A model that is still
  calling tools on round 5 gets one last completion with tools disabled, so
  the visitor always gets prose rather than a spinner.
- **Max 6 tool calls per round**, extras dropped with a note in the tool result
  — a parallel-tool-call storm on a free model is how the daily budget dies.
- A citation carries the evidence the model was shown — `source` and the
  bounded `text` of the hit, not just a title — so the page's source list reads
  exactly like a search row instead of a bare link.
- Every search result the loop sees is recorded, keyed by
  `(video_id, int(t))`. Citations in the response are **only** from that set:
  the model can cite `[3]`, but it cannot invent video 4. A citation marker
  pointing at nothing is stripped from the answer text rather than rendered as
  a dead link.
- The system prompt is short and says the two things that matter: answer only
  from tool results, and mark each claim with the `[n]` of the result it came
  from.
- One overall wall-clock budget (`VIDTHEQUE_ASK_TIMEOUT_S`, default 90) across
  the whole loop, not per request. A free-tier queue that stalls turns into a
  clean 503, not a held connection.

### 3.4 Degradation — the part that has to be right

The free tier *will* be unavailable. That is the normal case, not the edge
case, and the frontend is built around it: the Ask toggle degrades to search,
which always works.

| condition | status | `reason` |
|---|---|---|
| no `OPENROUTER_API_KEY` | 503 | `not_configured` |
| upstream 401/403 (key revoked, out of credit) | 503 | `upstream_rejected` |
| upstream 429 | 503 | `upstream_rate_limited` |
| upstream 5xx / timeout / connection error | 503 | `upstream_unavailable` |
| junk upstream body (HTML error page) | 503 | `upstream_unavailable` |

The **daily budget** is the one refusal that is *not* a 503: it is enforced by
the rate limiter (§4), which answers `429` with `bucket: "ask_global"` before
the request reaches this code. One mechanism, one place — a second budget
counter inside the loop would be a second thing to keep in sync. The page
treats a 429 like the degraded case, with the `Retry-After` in the message.

**A 503 gives the day's token back.** Charging before the handler runs is what
makes the limiter cheap, and it is right for an ask that reaches the model —
but every row of the table above is a request that cost no model tokens at all.
Since the table's normal case is "the free tier is unavailable", and the day it
is *most* unavailable is launch day, without a refund one impatient visitor
retrying through a flap spends all 50 asks in ten minutes and every other
visitor gets `429 ask_global` with ~28 minutes per reclaimed token. So the
endpoint refunds `ask_global` on any non-200 (§4.4). Only the global one: the
per-IP minute bucket is the anti-hammer guard, not the cost control, and
someone retrying a broken upstream should still be slowed down.

Body, in every 503 case, the same shape:

```json
{
  "error": "llm_unavailable",
  "reason": "upstream_rate_limited",
  "message": "LLM mode unavailable — use search.",
  "retry_after_s": 60
}
```

**Never leaked:** the API key (obviously), the upstream response body, the
upstream status line, the model's system prompt, or any exception text from
`httpx2`. The server logs the upstream status and a 200-char slice of the body
at WARNING for the operator; the client gets the table above and nothing else.
An upstream error body is attacker-controlled text and a provider-detail leak;
there is no version of it that helps a visitor.

The frontend renders the `message` verbatim in the answer pane with a "search
instead" button, and keeps the query in the box.

---

## 4. Rate limiting

App-level, in-process, token bucket. On `/api/*` and `/frames/*`, in public
mode only.

**In-memory, single process, deliberately.** vidtheque is one uvicorn process
holding one SQLite writer; there is no second replica for a shared counter to
be shared with. If this ever grows a replica, the limiter is the first thing
that needs Redis — and that is a bigger change than swapping a backend, because
the SQLite writer would need to move first. Stated here so nobody reads the
in-memory dict as an oversight. It is also the reason the daily `ask` cap is
approximate across a restart: the buckets are reset by a redeploy. For a budget
guard on a free tier, that is acceptable; for anything where money is at stake,
it would not be.

### 4.1 The buckets

| bucket | routes | default | env |
|---|---|---|---|
| `search` | `/api/search`, `/api/videos`, `/api/meta` | 30/min per IP | `VIDTHEQUE_RATE_SEARCH_PER_MIN` |
| `ask` | `/api/ask` | 5/min per IP | `VIDTHEQUE_RATE_ASK_PER_MIN` |
| `ask_global` | `/api/ask` | 50/day, whole server | `VIDTHEQUE_RATE_ASK_PER_DAY` |
| `frames` | `/frames/*` | 120/min per IP | `VIDTHEQUE_RATE_FRAMES_PER_MIN` |

`frames` is loose on purpose: one screen of results is ~10 thumbnails, so a
visitor paging through the corpus legitimately fetches a hundred images a
minute. It is there to stop a scraper walking the whole keyframe directory, not
to police normal browsing.

`/api/ask` is charged against **both** its per-IP bucket and the global one; the
per-IP check runs first, so one visitor cannot spend the day's budget before
being told to slow down.

### 4.2 The bucket, exactly

Capacity `n`, refilled continuously at `n / window` tokens per second, capped at
`n`. A request costs one token. Empty bucket → 429.

Continuous refill rather than a fixed window because a fixed window lets a
client spend `2n` across a window boundary, and because a visitor who was
limited a moment ago should get their next request back in seconds, not at the
top of the minute. `Retry-After` is `ceil((1 - tokens) / rate)` — the real
answer to "when will this work", rounded up, minimum 1.

Capacity doubles as the burst allowance: 30/min means a visitor arriving cold
can fire 30 requests immediately and then one every two seconds. That is what a
search-as-you-type frontend needs, and the page debounces anyway.

The daily bucket is the same maths with `window = 86400`, so the budget trickles
back through the day instead of everything unblocking at UTC midnight.

Response on refusal:

```
HTTP/1.1 429 Too Many Requests
Retry-After: 4
X-RateLimit-Limit: 30
X-RateLimit-Remaining: 0

{"error": "E_RATE_LIMIT", "message": "Too many requests — 30 per minute.",
 "retry_after_s": 4, "bucket": "search"}
```

`E_RATE_LIMIT` is already in `errors.HTTP_STATUS` at 429; the facade and the
tool surface use the same code for the same condition.

### 4.3 Identifying the client

`VIDTHEQUE_TRUSTED_IP_HEADER`, default `CF-Connecting-IP`. When the header is
present it wins; otherwise the ASGI `client` address. Set it to empty to trust
only the socket.

**This is trust-on-configuration and the caveat is real:** any client can send
`CF-Connecting-IP: 1.2.3.4` and get a fresh bucket. Behind Cloudflare that is
impossible — the edge overwrites it — which is the deployment this is written
for. Exposed directly to the internet, set `VIDTHEQUE_TRUSTED_IP_HEADER=` and
take the socket address. The default favours the documented deployment and the
env var exists so the other one is one line away.

Keys are `(bucket, client)`. The dict is swept when it exceeds
`VIDTHEQUE_RATE_MAX_KEYS` (default 10,000): full buckets are dropped first,
because a full bucket is indistinguishable from a client that was never seen.

### 4.4 Refunds — the one thing charging-up-front gets wrong

Charging before the handler runs is what keeps the limiter a middleware instead
of a decorator on every route. The cost is that a request which turns out to
have consumed nothing has still been billed, and for `ask_global` — a *daily*
budget, 50 tokens trickling back over 24 hours — that is the difference between
a bad minute and a bad day (§3.4).

So the middleware records what it charged on the request scope, and
`ratelimit.refund(scope, …)` hands a charge back. Two callers, both narrow:

- **The middleware itself**, when a *later* bucket refuses: `/api/ask` charges
  per-IP and then global, and a request refused by the second must not stay
  charged to the first. A refused request costs nothing, anywhere.
- **`ask_endpoint`**, on any non-200. Nothing else refunds anything; a search
  that returns zero rows is still a search that ran.

A refund refills first and is capped at capacity, so it can never mint a token
the bucket never had, and refunding a bucket that no longer exists (swept) is a
no-op rather than a resurrection.

---

## 5. Frames in public mode

`get-frames` returns HMAC-signed capability URLs by default (DECISIONS.md), and
`/frames/*` accepts a signature, a bearer, or a session cookie. In
`AUTH=none` — the intended public deployment — `auth.frame_signer` is `None`
and the route is open, so the facade emits **unsigned** thumbnail URLs:

```
/frames/kCc8FmEb1nY-00000.jpg?w=320&q=70
```

That is the honest thing to do rather than a decoration: signing a URL on a
server that serves the same file unsigned to anyone who asks buys nothing, and
`get-frames` already says so in its payload ("URLs do not expire and are not
signed: this server runs with auth disabled"). What guards the keyframe
directory in public mode is **the rate limiter**, not the signature — a scraper
gets 120 images a minute and a 429, which makes walking a 600-frame-per-video
corpus slow enough to be pointless.

If the flag is combined with `token`/`oauth`, `frame_signer` exists and the
facade signs its thumbnails exactly as `get-frames` does, using the same
`FrameUrlSigner.url()`. One helper, both callers, no second signing scheme.

Thumbnails are requested at `w=320&q=70` — the route clamps `w` to 128..1280
and re-serves the stored JPEG; it does **not** resize (there is no image
pipeline on this path). The width is a hint the frontend also applies in CSS.

---

## 6. The page

Served at `/` from `vidtheque_mcp/public/static/`, three files, no build step,
no framework, no external requests. `index.html` + `app.js` + `style.css`,
shipped inside the wheel (hatchling includes package data under the package
directory).

The aesthetic target is a **search engine, not a dashboard**: a column of text
on a plain ground, one accent colour, system fonts, no cards-with-shadows, no
sidebar, no charts. The corpus is the content; the chrome should be nearly
invisible. Dark mode via `prefers-color-scheme` with both palettes defined as
CSS custom properties on `:root`.

The `<head>` is part of the deliverable, not boilerplate: a title that says
what the thing is, a description, `og:title`/`og:description` (no `og:image` —
a wrong one is worse than none), `viewport`, a `theme-color` per scheme so the
browser chrome follows the page, and a **drawn** film-frame favicon as an inline
SVG data URI rather than an emoji, because an emoji favicon renders as a
different glyph on every platform and disappears in a monochrome tab strip.

Layout, top to bottom:

1. **Header** — wordmark (the page's `<h1>`) and one line of what it is.
2. **Search box** — autofocus, submits on Enter. One primary button, labelled
   by the current mode (`Search` / `Ask`).
3. **Controls row** — filter chips on the left (`all` / `transcript` /
   `on-screen text` / `frames`, mapping to `content_type`, `all` by default),
   and on the right a two-pill **mode switch**, `search | ask ✨`. The switch is
   hidden entirely when `ask_enabled` is false. In ask mode the filter chips
   are hidden rather than disabled: the model picks the channel, so a filter
   there would be a control that does nothing.
4. **Results** — one row per hit, hairline-separated: thumbnail (or a muted
   placeholder naming the channel the hit came from — `spoken` / `screen` /
   `frame`), title · channel, the timestamped snippet with the query terms
   marked, and `[mm:ss] ↗ youtu.be` opening the video at the moment in a new
   tab. The whole row is one `<a>`, so middle-click works.
5. **Ask pane** (ask mode) — the answer as prose with `[n]` markers rendered as
   superscript links to the moment they cite, followed by the same result rows
   numbered to match. A 503 replaces the pane with the degradation message and
   a "search instead" button; a 429 says how long to wait.
6. **"Add this corpus to your own agent"** — the `mcp_url` from `/api/meta`, a
   copy button, and the one-liner:
   `claude mcp add --transport http vidtheque <mcp_url>`. This is the panel
   that makes the demo a demo *of an MCP server* rather than of a search box.
7. **Footer** — "a vidtheque demo — self-hosted video-corpus MCP server", the
   GitHub link, and the sentence that matters legally and ethically: results
   link to the original talks on YouTube; vidtheque indexes what it watched and
   sends you back to the source.

### 6.1 The states

A demo is judged on the four screens that are not "ten results came back".

- **Before the first search** the page teaches instead of sitting blank: three
  clickable example queries — real ones, tailored to the corpus, and written in
  `index.html` because they are copy — a sentence saying what a result *is*, and
  the list of videos actually indexed, from `/api/videos`. Editing the examples
  is editing one list in the HTML.
- **Nothing matched** names the query, and offers the widening that exists: if a
  content-type filter is on, a button back to `all`; otherwise fewer words, and
  the reminder that a phrase from a slide will not be in the spoken words. When
  the facade's `data_status` says `empty` (§2.1) it says *that* instead: an
  instance with nothing indexed is not a query the visitor got wrong.
- **Refused or broken** — a 429 renders the limiter's `Retry-After` as a
  *ticking* countdown with the retry disabled until it reaches zero; a failed
  fetch says the server could not be reached and offers the same retry; a 503
  in ask mode keeps its "search instead" button and counts down too. No error
  ever shows a status code or an upstream message.
  **A failed "More results" is a row-level notice, not a wipe:** the error
  renders into the foot, under the rows already on screen, and the count line
  goes back to counting them. The rows a visitor has are theirs — losing ten of
  them to a rate-limit hiccup on page two (30/min is easy to spend while
  exploring) reads as the corpus breaking, not the chrome. The next attempt
  clears the foot before it starts, so a retry can never layer fresh rows under
  a stale error box either.
- **Loading** is a skeleton of *one row per expected result*, each with a
  result's geometry — thumbnail box, title, meta, two lines of snippet — so the
  space is reserved before the rows land and nothing below them moves. The rows
  fade out down the list so a full page of grey reads as a reserve rather than
  a wall. *How many to expect* is a guess with no honest source: the count is
  not known until the response lands, so a full page reserved for a query that
  returns three shifts everything below it (measured: 0.20) — which on a
  three-video demo corpus is the common case, not the edge. The best evidence
  available is what the **last** search returned, so that is what the next one
  reserves; the first search of a session still guesses a full page. CLS is 0
  on the steady state and on any page that comes back full, not unconditionally.

Requests are spent on **Enter or a click, never on a keystroke**: a
search-as-you-type box against a shared 30/min bucket would refuse a visitor
mid-word.

**One in-flight discipline, across both modes.** A request aborts the one
before it and takes the next sequence number; a reply is rendered only if it is
still the newest **and** the mode it was issued in is still the mode on screen.
Switching mode is itself a cancellation — of the request *and* of its
half-drawn loading state, because a skeleton belongs to a search that is now
never going to land. The mode half is not theoretical: an ask can run for 90s
against a free tier, which is plenty of time for a visitor to give up and
search, and a late answer that reopens the answer pane over those results (or a
late search dropping rows under an answer) is the one failure a stranger will
blame on the corpus rather than on the chrome.

### 6.2 The floor

Accessibility: real landmarks and one `<h1>`, a real `<label>` for the search
box, `aria-pressed` on both chip groups (they are toggles, and a screen reader
should hear the state, not infer it from colour), `role="status"` on the result
count and `aria-live` on the answer pane, a single `:focus-visible` ring that
also lands on the result rows, `alt` text naming the video and the timestamp on
every thumbnail, and AA contrast in both schemes for every colour pair the page
actually uses.

Mobile is a first-class target, not a media query afterthought: at 375px the
input takes its own line with the primary action full-width under it, chips
wrap, the copy row stacks, and nothing overflows horizontally at 320px either.

**XSS.** OCR text is adversarial by construction — it is whatever was on
someone's screen, and a slide that says `<script>` is a normal slide. Two rules,
both testable: every string becomes a DOM text node (there is no `innerHTML`,
`insertAdjacentHTML`, `document.write` or `eval` anywhere in `app.js`, asserted
in `test_public.py`), and every URL that reaches an `href` or a `src` passes
`safeUrl()`, which returns only `http(s)` — so a `javascript:` link in a payload
becomes the plain video link instead.

**A search is a URL.** `?q=` and `?type=` are written with `replaceState` on
every search and read back on load, so a result page is shareable and the
browser's history behaves. `?ask=1` arrives *in* ask mode with the question
loaded but **does not fire**: an answer costs a slice of the daily model budget,
and a shared link (or a crawler) must not spend it on page load. One click does.

A real `<form>` and a real `<a href>` on every result, so Enter submits and
middle-click opens — the two things a search engine is expected to do.

No inline `<script>` beyond a nonce-free module tag — the page is static files
served from disk, so a CSP could be added later without rewriting it.

---

## 7. Open, for Tom

1. **The model default.** `deepseek/deepseek-v4-flash-0731` stands in for the DeepSeek
   free tier that no longer exists (§3.1). If you want a specific one, it is
   `OPENROUTER_MODEL=` and a restart.
2. **The daily budget is 50 asks.** That is a number chosen to be visibly
   conservative, not measured against anything. Raise it once the free tier's
   real behaviour is known.
3. **Visual choices are mine** and are the easiest thing here to overrule: one
   accent colour (a warm amber that reads on both grounds), the 46rem column,
   hairline rows over cards, thumbnails at 96px, and the `search | ask ✨` pill
   pair instead of a toggle button. The whole palette is six custom properties
   at the top of `style.css`. The palette was checked rather than assumed:
   every text/ground pair the page uses clears AA in both schemes (the light
   accent is the tightest at 4.8:1), so a nudge to either accent should be
   re-checked before it ships.
6. **The three example queries** on the cold page are corpus-specific copy in
   `index.html`. They stop being useful the day the corpus changes; there is no
   machinery to keep them honest, deliberately, because a generated example is
   a worse example.
7. **A gibberish query still returns results.** The vector leg has no relevance
   floor, so `zzzzqqqq` comes back with the whole corpus, semantically ranked.
   The zero-results state is therefore designed but nearly unreachable in
   practice. That is a query-layer decision, not a page one — flagged here
   because it is visible from the page and looks like a bug to a visitor.
5. **Nothing outstanding on the query layer.** Driving the facade surfaced a
   multi-word FTS break (`search q="kv cache"` → `E_INTERNAL`, fts5 syntax
   error near `"OR"`); it was fixed in parallel by the expanded-OCR-groups
   commit and both legs are green again. Worth noting only as evidence that a
   browser hitting the read surface finds things nine tool descriptions do
   not — which is half of why the demo is useful internally.
4. **`/api` is public-mode-only.** A private deployment that wants the JSON
   facade for its own tooling has to set the flag, which also masks its write
   tools. If that combination is ever wanted, the flag splits in two; it is not
   worth two flags today.
