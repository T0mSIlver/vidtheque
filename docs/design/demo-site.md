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

  *Amended 2026-08-09 (dashboard phase 5).* "Public endpoint" turned out to
  mean the wrong thing for a year's worth of one word: the bound was chosen by
  **which prefix** a request arrived on, and `/dashboard/api/*` — registered in
  every mode, and unguarded in the `AUTH=none` the demo ships as — is a public
  endpoint on the intended deployment however private its name reads. It is now
  chosen by the **credential**: a bearer, a login session or a trusted peer gets
  the owner bounds on either prefix, and everyone else gets the numbers above on
  either prefix. **None of the numbers in §2.1 or §2.2 changed**, and none of
  them can be reached by an anonymous caller any more. See dashboard.md §2.4 and
  `docs/deploy-public.md`'s clamp audit item.
- **Typed errors survive the translation.** A tool returning `isError` carries
  a `structuredContent.code`; `errors.HTTP_STATUS` already maps every `E_*`
  code to an HTTP status, so the facade returns that status with
  `{"error": code, "message": …, "next": …}`. One table, two consumers.
- **Nothing new is queried.** The facade adds three derived fields per result —
  a `timestamp` clock string and two frame URLs, `thumb` and `thumb_large` —
  every one of them computed from data the tool already returned. `thumb_large`
  is a *URL*, not bytes: the enlarged frame is fetched only if a visitor opens
  one (§6.4), and it is built here because the width has to be clamped (and,
  under `token`/`oauth`, signed) server-side.
- **Agent text is humanised here, and only here.** The tool's text is written
  for a model and stays that way; the facade translates it for a reader on the
  way out, through one small module (§2.4). The facade still does **not**
  sanitise, because the page never parses corpus text as HTML (§6).

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
      "thumb": "https://…/frames/kCc8FmEb1nY-00000.jpg?w=192&q=70",
      "thumb_large": "https://…/frames/kCc8FmEb1nY-00000.jpg?w=960&q=70",
      "score": 0.0312
    }
  ],
  "pagination": {"limit": 10, "offset": 0, "has_more": true, "approx_total": 38},
  "notes": ["note: …"]
}
```

`thumb` and `thumb_large` are both `null` when the hit has no `frame_id` (a
transcript-only hit in a video with no keyframes). The page falls back to a text
card, not a broken image — and offers no enlarge control for a frame that does
not exist.

`notes` is the same `note:` array the MCP payload prints. The page renders it
in a muted line — "`all` means all" is a promise to a human too, and a search
where the frame leg was skipped should say so.

`data_status` is the search tool's own word for the state of the corpus, and it
is present **only on the empty path**, where the tool sets it (`"ok"`,
`"empty"`, …); a page of hits carries `null`. It is forwarded because
"nothing matched" and "nothing is indexed yet" are different screens: a `?q=`
link into a fresh instance would otherwise blame the query for an empty corpus
(§6.1).

### 2.2 `GET /api/videos`

The library listing, straight from `list-videos`:

```
GET /api/videos?limit=50&offset=0&q=&channel=
```

`limit` clamped 1..50, default 24. Returns `{"videos": [...], "pagination": …}`
with the tool's records verbatim (`video_id`, `title`, `channel`, `published`,
`duration`, `coverage`, `tags`, `link`) plus `thumb` — the video's first
keyframe, when it has one.

The facade does **not** pass `format`/`fields`: those shape the tool's *text*
block, and this path reads `structured_content`, which carries every field
regardless. Passing them read as though the facade were choosing a projection
it is not.

### 2.3 `GET /api/meta`

What the page needs to render itself, and nothing else:

```json
{
  "name": "vidtheque",
  "version": "0.1.0",
  "browse": "/dashboard",
  "mcp_url": "https://vidtheque.example.com/mcp",
  "auth": "none",
  "ask_enabled": true,
  "ask_model": "deepseek/deepseek-v4-flash-0731",
  "videos": 42,
  "clamps": {"policy": "public", "search_max_limit": 20, "videos_max_limit": 50},
  "limits": {"search_per_min": 30, "ask_per_min": 5, "ask_per_day": 50},
  "repo": "https://github.com/T0mSIlver/vidtheque"
}
```

`mcp_url` is derived from `PUBLIC_URL` — the same string `config.resource_url`
builds, so the copy button on the page and the OAuth `resource` can never
disagree. The page never hardcodes a hostname.

`ask_enabled` is false when no `OPENROUTER_API_KEY` is configured; the page
then hides the Ask toggle instead of offering a button that 503s.

`clamps` names the policy that answered, so a caller can see what it may ask
for rather than discovering it by being clamped (added with the dashboard's
second policy, dashboard.md §2.5.1).

`browse` is **added in dashboard phase 4 (2026-08-09)**: the path of the
browsable corpus, or `null`. It is a root-relative path and not a URL, because
the only consumer is this page and this page is already on the origin. The
masthead link (§6 item 1) is hidden in the markup and unhidden by this field, so
a deployment running `VIDTHEQUE_DASHBOARD=0` — or the edge rule in
`deploy/cloudflared.example.yml` that 404s `^/dashboard` at the tunnel — does
not leave an invitation to a dead page in the header. The page validates it as a
same-origin path before it reaches an `href` (one leading slash, never two);
`safeUrl()` is the wrong tool for this one, because it resolves against the
current document and would happily accept an absolute URL on another host.

### 2.4 The humanising layer

The MCP payload is written for a model, and that is a *contract*: it carries
markers a client can act on (`pass max_text_chars=0`), stand-in strings that keep
a field non-null, and `note:` prefixes that mark a line as machinery. None of it
moves — `tool-surface.md` owns it, and an agent keying on a `note:` prefix is
doing a supported thing.

The demo's reader has no API, so the translation happens at the facade. It is
`public/humanize.py` rather than three expressions inside `api.py` because the
dashboard is the second caller: a humanised snippet should read the same
wherever it is rendered. Three rules, all of them small:

| what | why |
|---|---|
| `TRUNCATION_MARKER` → a plain `…` | The marker's advice ("pass `max_text_chars=0` for full text") is for a client that has the parameter, and `/api` deliberately does not (§2). To a reader the marker means one thing: words are missing here. The pattern is built from the template in `text.py`, never retyped, so an edit there cannot leave it silently matching nothing — and two ellipses that meet collapse into one. |
| the frame leg's `visual match, no text hit` → `None` | The string keeps `text` non-null for a model reading a fixed shape. On the page it is a sentence pretending to be evidence, in the one place where the evidence is the picture (§6.3), so the snippet is dropped instead. This is the one literal that *is* retyped — the tool does not export it — and a test asserts the two still agree. |
| the `note:` prefix → dropped, sentence capitalised | The prefix tells a model "this line is machinery"; a reader gets that from the muted line it is rendered in. |
| `clip(text, n)` — one line, cut at the end | The fourth caller is the activity line (§3.5), which puts a *query* or a *talk's title* inside a sentence. That is a label, not a snippet: it is cut at the end rather than in the middle, because the front of a title is the part that identifies it, and it collapses whitespace for the same reason a moment row does. |

Whitespace is collapsed while it is in there, which is what makes a one-line
moment row (§6.5) a line.

What it deliberately does **not** do is rewrite the *body* of a note. Those
sentences are the query layer's own English, and a second copy of its vocabulary
kept here would drift silently the day someone edits a leg. Some of them still
name a parameter a browser has no way to pass; that is a query-layer wording
question, flagged in §7, not something to paper over with a lookup table.

---

## 3. `/api/ask` — the LLM mode

```
POST /api/ask   {"q": "how does paged attention reduce fragmentation?"}
```

A server-side agent loop against OpenRouter's OpenAI-compatible
`/api/v1/chat/completions`, with **two** internal tools and a hard round cap.
It exists to show the thing the corpus is actually for — an agent that answers
from timestamped evidence — to a visitor who has not wired up an MCP client.

The same POST also speaks a stream, to a client that sends
`Accept: application/x-ndjson` (§3.5), narrating the loop's tool calls while it
runs. The body below is what a plain POST returns and what the stream's final
event carries — one loop, one payload.

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

`get_segment_context` is handed to the model as the **text** block the tool
already renders — the model-readable form the whole contract is tuned for —
never a JSON dump and never a full transcript. `search` composes numbered lines
from the tool's structured hits, because the `[n]` the whole citation contract
rests on is the loop's, not the tool's; each line carries the hit's **source**
(`transcript` / `ocr` / `frame` / `transcript+ocr`) for the same reason the page
badges it — without the label, the prompt's "say which channel this came from"
asks the model to distinguish a slide from a sentence with nothing in front of
it that says which. The caps above are the facade's, tighter than the MCP
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
- **A drill-down window is evidence too.** `get_segment_context` records the
  window it returned under an `[n]` and tells the model that number, keyed on
  `(video_id, int(t))` like every other record — so drilling into a hit the
  model already has reuses that hit's number, and an answer written *only* from
  a drill-down still has sources. Without it the round where the model does the
  most work is the one that lands with an empty Sources list, every marker in
  it stripped for naming nothing.
- **Tool-call ids are synthesised when the model omits them.** An
  OpenAI-compatible upstream requires each `tool` message's `tool_call_id` to
  name exactly one call in the assistant turn before it; two id-less `search`
  calls (which is a cheap/free tier's habit) would otherwise both be sent as
  `"search"` and 400 the whole loop. The assistant turn is rewritten with the
  same ids the tool messages use, so the two can never disagree.
- Every search result the loop sees is recorded, keyed by
  `(video_id, int(t))`. Citations in the response are **only** from that set:
  the model can cite `[3]`, but it cannot invent video 4. A citation marker
  pointing at nothing is stripped from the answer text rather than rendered as
  a dead link — and stripping is a *rewrite*, not a deletion: the horizontal
  space that flanked the marker goes with it, so `"the block table [9] is"`
  comes back as `"the block table is"` and `"see [9]."` does not become
  `"see ."`. An answer is the thing a visitor screenshots; a typo in one is
  worth the twenty lines.
- The system prompt is short and says the things that matter: answer only from
  tool results, mark each claim with the `[n]` of the result it came from, and
  — since the hits are labelled — *say which channel* a fact came from, with one
  hard rule for the case that fails silently: a frame is a visual match, so
  describe what it shows and never quote text from one. That last part is
  **encouragement, not a template**: no phrasing is dictated, because an answer
  that reads like a form is worse than one that reads like a person who watched
  the talk. It is worth its ~30 tokens because the alternative is prose that
  flattens "he said", "the slide read" and "the screen showed" into one voice —
  which is exactly the distinction the corpus exists to keep.
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

### 3.5 The stream — making ninety seconds of work visible

The loop above can spend a minute and a half inside the corpus, and the page had
one static line to show for it ("reading the corpus…"). A visitor cannot tell
that from a hang. So the same loop is also an **event stream**: every tool call
it makes becomes one line a person can read, sent as it happens.

**What is not streamed: the answer.** Tom's call (2026-08-09) — no token
streaming, so no streaming-markdown renderer, no half-written prose that
rewrites itself, and no citation marker rendered before the citation it names
exists. The answer arrives whole, as the last event, exactly the payload §3
already specifies. What streams is the *work*, which is the part that is
otherwise invisible.

**Transport: NDJSON over the same POST.** One JSON object per line,
`Content-Type: application/x-ndjson`. Not SSE: `EventSource` is GET-only and the
ask is a POST with a body, so the browser side is `fetch` + a reader either way
— and on top of `fetch`, SSE's `event:`/`data:` framing is a parser to write
where NDJSON is `JSON.parse` per line. `json.dumps` escapes every newline in a
payload, so a corpus title with a line break in it cannot split a frame.

**Negotiation, not a second endpoint.** A request that sends
`Accept: application/x-ndjson` gets the stream; anything else gets the JSON body
byte for byte as before. `/api/ask` stays one route with one contract, curl and
any script written against it keep working, and the MCP surface is untouched.
The page asks for the stream only when the browser has `ReadableStream` and
`TextDecoder` — the fallback is not a worse answer, it is the same answer with
nothing to watch on the way.

The vocabulary is three events, and every one of them is built from data the
server already had:

| event | when | fields |
|---|---|---|
| `activity` … `"phase": "start"` | before a tool call runs | `id`, `text` — "Searching on-screen text for “CVE”", "Reading the transcript around 12:34 in “…”" |
| `activity` … `"phase": "done"` | when it lands | `id`, `result` — "6 hits in 2 talks", "5 lines of transcript", "nothing matched" |
| `answer` | once, last | `payload`: the §3 body, unchanged |
| `error` | once, last, instead | `status: 503` and `payload`: the §3.4 body, unchanged |

`id` pairs a `done` with its `start` — that pairing is what lets the page mark
exactly one line as the one still running. It is **not** a citation `[n]`; the
two numbering schemes never meet.

Two rules keep the lines honest, and they are the reason this is not just a
progress bar:

- **A line is derived from the call, not written about it.** The channel named
  is the `content_type` the model actually passed, mapped through the same three
  words the page's filter chips use. The talk named in a drill-down is a title
  the loop has *already seen* in a hit; a drill-down into a video it has not seen
  says `video kCc8FmEb1nY` rather than inventing a title for it.
- **A result is counted, never estimated.** "6 hits in 2 talks" is `len(hits)`
  and the distinct `video_id`s in them; "5 lines of transcript" is the cues the
  window returned. A tool that failed says the leg came back empty-handed and
  keeps the typed `E_*` code for the model, where it is useful.

An unknown tool name is narrated too ("Asking for “delete_everything”" → "no such
tool"), because a round in which the model did something the loop refused is not
a round that stalled, and a gap in the log would read as one.

**What a stream owes the budget.** §4.4's refund keys on a non-200, and a stream
is a 200 the moment its first byte is written — before the model has done
anything. So the accounting moves to where the outcome is known: **no `answer`
event, no charge.** It is a `finally`, not an error branch, because the ways a
stream ends without an error event are the interesting ones — the visitor closing
the tab, a mode switch aborting the fetch, the loop being cancelled — and every
one of them cost the same zero upstream tokens as a 503 did. Exactly one of the
two rules runs for any given request, so a failed stream is refunded once.

**A disconnect stops the work.** Starlette's streaming response fails its next
write once the client is gone, which closes the generator, which cancels the
upstream call that was in flight. Verified end to end against a real socket: a
visitor who abandons a stream after the first activity line costs no budget *and*
no second completion. What cannot be cancelled is a call already in flight at the
moment they leave; that one runs to its deadline and is thrown away.

**Refusals stay status codes.** Everything that can be refused before the model
is reached — a 429 from the limiter with its `Retry-After`, a 503 for a missing
key, a 400 for a bodyless request — happens *before* a byte of stream exists and
comes back as it always did, JSON and all. That is what keeps the page's
countdown working: a refusal delivered as an event inside a 200 would have to
reinvent the header. Only a request that reached the loop streams, and once it
streams it is committed to 200 with a terminal event.

`Cache-Control: no-store` and `X-Accel-Buffering: no` ride along: nginx buffers a
proxied response by default, which would hold every activity line until the
answer landed — that is, exactly the ninety seconds of silence this removes.

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
- **The ask *stream*, on any run that produced no answer** (§3.5). Not a third
  rule, the same one: a stream's status line is written before the model has
  done anything, so "non-200" cannot express it and "no answer, no charge" does.
  Exactly one of the two runs per request — a streamed request is a 200 — so a
  failed stream is refunded once, never twice.

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

Thumbnails are requested at `w=192&q=70` — 96×54 CSS pixels at 2x — and the
route *does* resize now, through the byte-capped `derived/` cache
(index-schema §6), clamping `w` server-side to 64..1280. A **frame** hit asks
for `w=320` instead: it matched on its image, the page renders it at 160×90, and
the width follows at the same 2x. Both are the facade's choice of a size the
route already allows; neither is a limit the browser gets to set.

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

1. **Header** — wordmark (the page's `<h1>`) and one line of what it is, and —
   *added in dashboard phase 4 (2026-08-09)* — **one link**, right-aligned on
   the wordmark's baseline: `Browse the corpus →`, into the dashboard's
   read-only projection (`dashboard.md` §2.4). One link and not a nav: this
   page is a search engine and the search box is its one primary action, so the
   link is a quiet outline rather than a second filled control beside it. It is
   hidden until `/api/meta` reports `browse` (§2.3). Below ~30rem it wraps to
   its own line under the tagline rather than squeezing it.
2. **Search box** — autofocus, submits on Enter. One primary button, labelled
   by the current mode (`Search` / `Ask`).
3. **Controls row** — filter chips on the left (`all` / `transcript` /
   `on-screen text` / `frames`, mapping to `content_type`, `all` by default),
   and on the right a two-pill **mode switch**, `search | ask ✨`. The switch is
   hidden entirely when `ask_enabled` is false. In ask mode the filter chips
   are hidden rather than disabled: the model picks the channel, so a filter
   there would be a control that does nothing.
4. **Results** — one card per *video*, hairline-separated (§6.5): a header with
   the frame, the title, the channel and the moment count, and under it that
   video's moments — timestamp, source badges, one line of snippet, each a link
   into youtu.be at that second. The text is always inside a real `<a>`, so
   middle-click works; the thumbnails are their own buttons and open the frame
   full size (§6.4). What a row says about *where the hit came from* is §6.3.
   The answer's Sources list keeps the flat row: a citation is one moment, not a
   video.
5. **Ask pane** (ask mode) — while the model works, the activity log (§6.6);
   when the answer lands, the answer as prose with `[n]` markers rendered as
   superscript links to the moment they cite, followed by the same result rows
   numbered to match, and the log folded underneath. A 503 replaces the pane
   with the degradation message and a "search instead" button; a 429 says how
   long to wait.
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

- **Before the first search** the page teaches instead of sitting blank: a
  handful of clickable example queries — real ones, tailored to the corpus, and
  written in `index.html` because they are copy — a sentence saying what a
  result *is*, and the list of videos actually indexed, from `/api/videos`.
  Editing the examples is editing one list in the HTML.

  *Amended 2026-08-09: five, from the verified set, and one of them names its
  channel.* The examples are now drawn from
  `research/demo-queries-2026-08-09.md`, which is 25 queries run against the
  real corpus rather than three written from memory, and they carry two rules
  the first three did not need:

  1. **The button's text is the query.** What you click is what you searched, so
    the result can be read back against the words that were sent. No example is
    labelled one thing and sends another.
  2. **`data-type` pins a channel; no `data-type` resets to `all`.** The
    flagship example is `owl:FunctionalProperty`, which exists in this corpus
    only as text on a slide — pinned to `ocr` it returns exactly one hit and
    nothing else can answer it at all, which is the whole argument for indexing
    frames. Unpinned, the other legs' noise buries it. A *visual* example has
    the same problem in reverse and takes `frame`. The pin sets the chip row
    rather than smuggling a parameter past it, so it is visible, it lands in the
    shared URL, and the "you are searching only …" widening tip already knows
    how to undo it. The reset is what stops the sequence *on-screen example →
    spoken example* from running the second query pinned to OCR and reporting an
    empty corpus.

  The five, and the channel each is there to demonstrate: `reward hacking`
  (all — the fused `[transcript+ocr]` badge), `owl:FunctionalProperty` (ocr),
  `architecture diagram with boxes and arrows` (frame),
  `small towns in Bavaria` (transcript — a paraphrase, not a keyword),
  `slop` (all).
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
- **Loading** is a skeleton with the results' own geometry — a card header
  (thumbnail box, title, channel) and one grey line per moment under it — so the
  space is reserved before the cards land and nothing below them moves. They
  fade down the list so a page of grey reads as a reserve rather than a wall.
  *What to expect* is a guess with no honest source: nothing is known until the
  response lands, and reserving the wrong shape shifts everything below it
  (measured: 0.20) — which on a three-video demo corpus is the common case, not
  the edge. Since results are grouped, the guess is now a **shape** — moments
  per card — and the best evidence available is the shape the **last** search
  had, so that is what the next one reserves. The first search of a session
  guesses `[4, 3, 3]`: ten hits over three talks, which is what this corpus
  usually answers. CLS is 0 on the steady state, not unconditionally.
- **Loading, in ask mode**, is not a skeleton — there is no shape to reserve,
  because nobody knows what the model will find. It is the activity log (§6.6),
  which is the honest version of the same promise: rather than reserving space
  for an answer, it shows the work that will produce one.

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
blame on the corpus rather than on the chrome. **A stream is held to the same
rule, per event rather than per reply:** the abort tears the connection down,
and staleness is re-checked before every line is drawn, so a stream that is no
longer the newest request cannot keep writing into a pane that has moved on.

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
middle-click opens — the two things a search engine is expected to do. (The
thumbnail is the one exception, and it is a `<button>` for a reason: §6.4.)

No inline `<script>` beyond a nonce-free module tag — the page is static files
served from disk, so a CSP could be added later without rewriting it.

### 6.3 Provenance — three sources, three kinds of evidence

`hit.source` flows end to end (`/api/search` hits and `/api/ask` citations
alike) and the page is *specific* about it, because the three are not
interchangeable and a snippet that does not say which one it is quietly claims
to be speech:

| `source` | badge(s) | how the snippet reads |
|---|---|---|
| `transcript` | `spoken` | a verbatim quotation of speech — set in quotation marks |
| `ocr` | `on-screen` | text that was *visible*, not said — monospaced behind a dashed rule |
| `frame` | `frame` | **no quotable text.** The image is the evidence, so it is rendered larger (160×90, `w=320`); any text is what happened to be visible in the frame, muted and never quoted |
| `transcript+ocr` | `spoken` + `on-screen` | both channels agreed; the text is whichever was longer, so it is presented as neither — plain, with both badges |

Two rules the styling obeys. **The word carries the meaning**: border style
(solid / dashed) and font (sans / mono) differ alongside the colour, so a badge
is still legible on a monochrome screen and to a colour-blind visitor — and the
label is a real word, not an icon. **Both schemes**: the badges use the same six
custom properties as everything else, so light and dark follow for free.

A `source` the page has never heard of still gets a badge with its own name.
Dropping provenance silently is the one failure that is worse than an ugly
badge — it is `all` means `all`, applied to a row.

The same treatment is in the answer's Sources list, because that list *is* a
list of search rows (one `hitRow`, two callers). The ask prompt asks for the
same distinction in prose (§3.3).

### 6.4 Click to enlarge

A thumbnail is 96 or 160 CSS pixels of somebody's slide: enough to recognise,
never enough to *read*. Clicking one opens the frame at `thumb_large`
(`w=960`) in a dialog with the title, the channel, the timestamp, and
`open at <t> on YouTube`.

It is the **native `<dialog>`**, opened with `showModal()`. Esc, the inert
background, the focus trap and the modal semantics are the platform's, so the
page implements none of them; it adds the two things the element does not give
for free — a click that lands on the dialog element itself (its padding is zero,
so `.shot-inner` covers the box and such a click landed on the backdrop)
dismisses, and focus returns to the **exact** thumbnail that opened it, tracked
rather than inferred because clicking a button does not focus it on every
browser. The Close button carries `autofocus` and is focused explicitly, because
`showModal()`'s own default would land on the YouTube link — and the first Enter
after opening a picture should not leave the page.

This is what turned the result row inside out. A row used to be one `<a>` around
everything, and a button cannot live inside an anchor. So the row is a flex
`<div>` holding two controls: the thumbnail button, and one anchor over *all*
the text. Middle-click and open-in-new-tab still work everywhere they used to
except on the image itself, which now has its own job. The geometry is unchanged
— the button strips every default (no padding, no border, `line-height: 0`), so
there is no layout shift and the skeleton still matches.

Feature-detected once: no `showModal`, no button — the thumbnail stays the inert
image it was, rather than becoming a control that half works.

The dialog lives in `index.html` as empty markup and is filled per click, and
it is filled the way everything else is: text nodes only, `safeUrl()` on the
image `src` and the link `href`.

### 6.5 Grouped by video

Ten flat rows are usually three talks, and a visitor reading the same title four
times is doing the grouping in their head. So a page of results is a **card per
video**: a header — frame, title, channel, `N moments` — and under it that
video's moments, each one a timestamp, its source badges (§6.3) and a single
line of snippet, each linking to `youtu.be` at that second.

The moment snippet is clamped to one line on purpose. This is a list to skim;
the sentence in full is one click away, in the video, at the second it was said.

Three properties worth stating, because each is a thing that could have gone
wrong:

- **Grouping is per page.** The server ranks and paginates; the page groups what
  it was handed and nothing else. Re-ranking across pages here would mean a
  video climbing the list because page 2 happened to arrive — the ordering
  contract is the query layer's (`relevance` first, `order` explicit), and a
  renderer must not quietly hold a second opinion. `has_more` and "More results"
  are untouched.
- **A second page merges into the card the video already has**, by a `Map` from
  `video_id` to the card on screen — one lookup, and a page 2 that repeats a
  title reads as the list restarting. The map is cleared on every fresh search,
  because a card that is no longer in the document is not a card to merge into.
- **A frame moment carries its own picture.** The header frame is *a* frame of
  the talk; a frame hit matched on a specific one, and the image is the evidence
  (§6.3), so it appears in the row rather than being represented by a header
  thumbnail from a different second.

The header is source-agnostic — it is whichever of the video's hits has a frame
behind it — because the header answers "which talk", not "which leg". Its title
links to the video itself, which is the moment link with the `?t=` taken off; a
hit with no deep link (a source that is not YouTube) gets a title in plain text
rather than a URL the page guessed.

The empty, no-results, rate-limited and failed-page states are unchanged: they
replace or annotate the results region as they always did, and a failed "More
results" still leaves the cards a visitor already has on screen (§6.1).

### 6.6 "Show its work" — the activity log

An ask used to be one line of copy and a spinner for up to ninety seconds. The
stream (§3.5) replaces it with the work itself: one row per tool call, appended
as the events arrive.

```
   Searching on-screen text for “kv cache” → 2 hits in 2 talks
   Searching the corpus for “block table” → 2 hits in 1 talk
⟳  Reading the transcript around 12:34 in “Let's build GPT: from scratch”
```

Four decisions worth stating:

- **One spinner, on whatever is actually happening.** A running row carries it;
  an idle line under the list ("reading the corpus…") carries it between calls,
  when the slow thing is the model thinking rather than a tool running. The idle
  line steps aside while a row is running, so there is never a second spinner
  claiming a second kind of work.
- **The result is a text node with its arrow in it**, not a `::before`. The
  arrow is chrome, but a log a visitor copies out of the page should still read
  as a log, and so should one whose stylesheet never arrived.
- **When the answer lands, the log stops owning the pane.** It folds into a
  native `<details>` — "Show its work" — placed under the answer and its Sources.
  The evidence trail stays one click away, and the disclosure is the platform's:
  keyboard, semantics and state for free, as with the lightbox (§6.4).
- **The pane is a live region, and stays `aria-busy` until the answer.** Without
  that, a screen reader narrates six activity rows and then re-narrates two of
  them as their results land. `aria-busy` holds the announcement to one, when
  there is something worth announcing.

Everything in a row is a corpus or model string that the server phrased, and the
page's rules do not bend for it: text nodes only (§6.2), and no URL anywhere in
the log — an activity line links to nothing, so there is nothing for `safeUrl` to
guard. A title with a `<script>` in it is a title with a `<script>` in it.

**Progressive enhancement, on the page's side of it.** The `Accept` header that
asks for the stream is only sent when the browser has `ReadableStream` and
`TextDecoder`; otherwise the same POST returns the same answer in one piece, with
no log and no disclosure. Nobody gets a worse answer, and one code path renders
it either way. A stream that ends *without* a terminal event — a server that went
away mid-answer — degrades like any other failure: no partial answer is ever
shown.

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
8. **Some `note:` bodies name things a browser cannot do.** The facade drops the
   prefix and leaves the sentence (§2.4), and a few of those sentences are
   written for an operator — "the frame leg needs … `POST
   /v1/embeddings/frame-query` answered 404", "`min_chars`/`max_chars` are text
   filters". They are *true* and they are rare on the demo's paths, and a lookup
   table of nicer phrasings here would drift the day a leg is edited. The fix, if
   it is worth one, is in the query layer's wording, not in the page.
4. **Resolved (2026-08-09): `/api` is still public-mode-only, and it no longer
   matters.** A private deployment that wants JSON gets `/dashboard/api/*` —
   the *same handlers*, mounted under the dashboard's prefix with the owner
   clamp policy (dashboard.md §2.5.1, phase 1). So the flag never had to split
   in two: `/api/*` stays the anonymous surface with the tight bounds this
   document specifies, and the second caller arrived as a second policy rather
   than as a second query layer. `public/api.py`'s five clamp constants are now
   `PUBLIC_CLAMPS`, asserted against the numbers in §2.1 and §2.2 so the
   refactor cannot have widened them.

   *Amended 2026-08-09 (phase 5): "mounted … with the owner clamp policy" is no
   longer how the second caller is recognised.* Mounting under a prefix is a
   statement about where a route is, not about who called it, and in the
   `AUTH=none` demo nothing stands between the internet and that prefix — so an
   anonymous visitor was getting the owner policy, `max_text_chars=0` included.
   The policy now follows the credential (`public/api.py:policy_for`). The rest
   of this entry stands unchanged: still one set of handlers, still two
   policies, still no second query layer, and `/api/*` is still
   public-mode-only.
