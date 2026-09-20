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
conditional. Set it and three things change together:

| | off (default) | on |
|---|---|---|
| write tools (`index-video`, `tag-video`) | registered | **never registered** |
| `/api/*` | absent (404) | served |
| rate limiting on `/api/*`, `/frames/*` and `/mcp` | none | token bucket per IP |

**Amended 2026-09-05 (Tom): Python serves no pages. Next.js does.** The landing
at `/` and the demo at `/demo` are the front end's in `web/`, and the flag's
fourth effect — the two page registrations and the `/static/{asset:path}` route
that fed them — is gone from `public_routes`, which now returns the facade and
nothing else. `frontend-migration.md` §1a is the route ownership table.

Production is **one origin split by path**: a reverse proxy sends the exact page
GETs to Next and everything else to this app — `/api/*`, `/frames/*`, `/mcp`,
`/auth/*`, `/.well-known/*`, `/healthz`, `/videos/{id}/export.md` and every
`/dashboard/*` route. Python keeps every promise it made to an agent or to a
script; what it stopped serving is HTML.

Two consequences, both of which used to live in this document. **The document
policy is the front end's** — `_DOCUMENT_HEADERS` was written for `/`, `/demo`
and any `.html` under `/static/`, and this flag registers none of those now, so
§7 item 0 is the handover and the check on it. The one document Python still
serves of its own is the OAuth consent screen, which carries its own pair in
`auth/login.py`. And **the lab denylist is gone with the route it guarded**
(§6): there is no asset handler to deny a subtree to. `mcp/pyproject.toml`
still excludes `static/lab` from the wheel.

*The 2026-08-11 topology it replaces, kept because the reasoning still binds the
front end:* the landing took `/` from the demo because a visitor arriving cold at
a search box has to be sold the corpus before being asked to query it, and the
working corpus is one click in behind the landing's single CTA. Nothing
redirects — an old bookmark of `/` lands on the landing, which is the same site
saying the same thing, with `/demo` in its one gold button; a 302 would buy that
visitor one click at the cost of a rule that outlives everyone's memory of why it
exists. `/api/*`, `/frames/*`, `/mcp` and `/dashboard` did not move then and have
not moved since: the facade is addressed by agents and by the page's own script,
and a public JSON path is a promise in a way a page is not.

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

so a new tool with `readOnlyHint: False` is masked the day it is added, with no
second list to keep in sync. `job-status` stays: it is read-only, it is how
a curious visitor sees that indexing is a real pipeline, and it exposes
nothing a job id doesn't already name.

**One tool is masked that the derivation cannot reach**, and it is written down
beside it as `OWNER_ONLY_TOOLS`: `get-transcript` (tool-surface §4.11) is
read-only and idempotent and still must not exist here. It is the model-facing
half of `GET /videos/<id>/export.md`, which this deployment already gates on
proved ownership rather than on the read gate (tool-surface §6, `http/export.py`),
for the reason that carries over unchanged — every request here is `"open"`, and
a caller who can page one transcript to its end can page the next one. "Never a
full transcript" was written in §3.2 about what the `/api/ask` loop hands its
model; it binds the tool surface too.

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
  every one of them computed from data the tool already returned, except the
  frame a spoken hit is pictured with (§2.1). `thumb_large`
  is a *URL*, not bytes: the enlarged frame is fetched only if a visitor opens
  one (§6.4), and it is built here because the width has to be clamped (and,
  under `token`/`oauth`, signed) server-side.
- **Nothing it answers is cacheable** (added 2026-09-05). Every response the
  facade returns — the four reads and every typed refusal — carries
  `Cache-Control: no-store`, because it describes a corpus that changes under
  the reader and the same handlers serve `/dashboard/api/*` behind the owner
  gate, where a management read must never be held by a shared cache; the
  header is unconditional since a handler cannot see which prefix reached it.
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
| `tags` | at most 10 comma-separated namespaced tags, AND semantics | omitted |

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
      "frame_id": null,
      "thumb": "https://…/frames/kCc8FmEb1nY-00000.jpg?w=320&q=70",
      "thumb_large": "https://…/frames/kCc8FmEb1nY-00000.jpg?w=960&q=70",
      "score": 0.0312
    }
  ],
  "pagination": {"limit": 10, "offset": 0, "has_more": true, "approx_total": 38},
  "notes": ["note: …"]
}
```

A transcript hit matched no frame, so the tool returns it with `frame_id: null`
as in the example above. For those hits the facade pictures the moment
instead (amended 2026-09-18, Tom's review: an empty box labelled
"spoken" read as an image that failed to load): `thumb` and `thumb_large` point
at the keyframe showing at `start` — the latest keyframe at or before it, a
duplicate resolved to the frame it repeats, the first keyframe when the hit
precedes them all. `frame_id` stays `null`, because it names what matched. This
is the facade's one extra read per search, a single grouped query bounded by the
`limit` clamp; the MCP tool's payload is unchanged.

`thumb` and `thumb_large` are both `null` only when the video has no keyframes.
The page falls back to a text card, not a broken image — and offers no enlarge
control for a frame that does not exist.

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

### 2.2.1 `GET /api/videos/{video_id}` — removed 2026-09-07

One video, straight from `video-summary`, added 2026-09-01 for the Next.js
front end's `/videos/{id}` page and called by nothing else. Tom removed that
page on 2026-09-07 — it duplicated the dashboard's video detail — and the
endpoint went with its only caller rather than staying as a public surface
with no reader. The dashboard's own detail read (`/dashboard/api/library/{id}`)
was never this: it is behind the owner gate and carries the management fields
this never had (frontend-migration.md §6a).

The listing at §2.2 stays: the demo's cold page lists the corpus from it.

### 2.3 `GET /api/meta`

What the page needs to render itself, and nothing else:

```json
{
  "name": "vidtheque",
  "version": "0.0.1",
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

### 2.5 `GET /api/editions/{slug}`

This additive read serves a committed edition schedule, its talk alignment
table, and the queryable videos carrying the edition tag. The edition-specific
schema and derivation rules live in `aie-paris-2026.md` §2 and §3. This route
uses the facade rules above: no writes, `Cache-Control: no-store` on success
and refusal, typed errors, independent item and character caps, and no query
layer beside the existing tools and SQLite reads.

| param | values | default |
|---|---|---|
| `limit` | 1..100 | 50 |
| `offset` | 0..1000 | 0 |
| `video_limit` | 1..50 | 20 |
| `video_offset` | 0..1000 | 0 |

The response contains `edition`, `sessions`, `pagination`, `talks`, `videos`,
`video_pagination`, and `notes`. Both pagination objects carry `has_more` and
`next_offset`; neither requires an exact total. An unknown slug returns 404
with `E_UNKNOWN_EDITION`. A malformed committed fixture returns `E_INTERNAL`
instead of a partial schedule.

---

## 3. `/api/ask` — the LLM mode

```
POST /api/ask   {"q": "how does paged attention reduce fragmentation?", "tags": "series:aie-paris-2026"}
```

A server-side agent loop against OpenRouter's OpenAI-compatible
`/api/v1/chat/completions`, with **two** internal tools and a hard round cap.
It exists to show the thing the corpus is actually for — an agent that answers
from timestamped evidence — to a visitor who has not wired up an MCP client.

`q` is at most **1000 characters** and a longer one is refused with
`E_BAD_PARAM`, naming both the cap and the length sent. Until 2026-09-20 the cap
was 400 and it *truncated*: a pasted paragraph was answered as far as the 400th
character, with nothing in the payload saying the question had been cut. The
console's field stops at the same number, so the refusal is what a client
outside the page meets, never a visitor.

`tags` is optional and follows §2.1's ten-tag bound and AND semantics. When it
is present, the server fixes that scope for the whole loop: every search uses
it, and `get_segment_context` accepts only video ids returned by those scoped
searches. The model cannot remove the filter or introduce an arbitrary video
id. Omitting `tags` preserves the existing corpus-wide behavior.

The same POST also speaks a stream, to a client that sends
`Accept: text/event-stream` or `Accept: application/x-ndjson` (§3.5), narrating
the loop's tool calls while it runs. The body below is what a plain POST returns
and what either stream's final event carries — one loop, one payload.

```json
{
  "answer": "Paged attention keeps a block table … [1]",
  "citations": [
    {"n": 1, "video_id": "zduSFxRajkE", "title": "Making LLMs go brrr",
     "channel": "GPU MODE", "t": 13, "timestamp": "0:13",
     "link": "https://youtu.be/zduSFxRajkE?t=11", "thumb": "…",
     "source": "transcript", "text": "the block table keeps …",
     "read": [{"t": 9, "text": "so the question is where …"},
              {"t": 13, "text": "the block table keeps …"}]}
  ],
  "rounds": 2,
  "model": "deepseek/deepseek-v4-flash-0731"
}
```

`read` is what the model read of that moment, so a visitor can check the answer
against it (added 2026-09-19, Tom: "see exactly what the LLM read"). A citation
that came from a `get_segment_context` call carries the window's transcript,
line by line with each line's second, exactly the cues the tool printed under
the window's text budget; a search hit the model later read around gains that
window too. A citation the model only saw as a search hit has `read: null`,
because its `text` is all it read. The `text` of a drill-down is the moment
itself — the cue running at the cited second and the two after it — where it
used to be the window middle-truncated to 1000 chars, which cut out exactly
that line.

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

The model never sees the ten-tool surface. It sees two, described in a handful
of words each: the loop is a demo of the corpus, not the place for progressive
disclosure.

| tool | args | what it runs | bounds |
|---|---|---|---|
| `search` | `query`, `content_type?`, `limit?` | `tools/search.run` | the tool's own — `limit` 1..50 (default 10), `max_text_chars=1000`, `max_per_video=3` |
| `get_segment_context` | `video_id`, `t`, `window?` | `tools/segment.run` | the tool's own — `window` 5..300s (default 45), `max_text_chars` 90 per second of window and never past the tool's 20 000 |

**Amended 2026-08-15 (Tom).** The facade used to bound both tools tighter than
the MCP surface does — six hits of 300 chars, two per video, a 1200-char
window — and that was the demo's real bottleneck: the model answered from
scraps of the corpus and the answers read like it. The loop now asks for what
any MCP client gets.

**Amended 2026-09-20 (Tom).** The defaults were still the model's ceiling,
because the loop forwarded no argument it sent — and the defaults are wrong in
two opposite directions: a question spanning a dozen talks answered from ten
hits, a speaker's three-minute argument read forty-five seconds at a time. Two
arguments are forwarded now, `limit` and `window`, and nothing else. The
ceilings are unchanged, because they were never the model's to set: they are
the tools' own server-side clamps, and an argument outside them is clamped
rather than refused. `window` carries its text budget with it at the ratio the
defaults already set — 90 chars per second, so 45s is the 4000 chars it always
was — because a window returned middle-truncated to the middle 4000 chars is
the silent cut §3 refuses on a question. The tool's own 20 000-char ceiling
still applies above it, so a window past ~220s of dense speech is truncated
again; that ceiling is the MCP surface's and is not the loop's to move.

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

**Each hit leads with the talk and the speaker** (*amended 2026-08-11*), not
with the channel label:

```
[2] “Loop Engineering from First Principles” — Kyle Mistele (HumanLayer) · transcript at 17:57 (video_id=xIt_mTQp6mY, t=1077)
    the model gets the tool's own bounded text here
```

A conference upload carries its attribution inside the title — AI Engineer
publishes `Talk title — Speaker, Org` — and the `channel` beside it is the
*publisher*, not a person. Rendered as one blob after the source label, that
title is something the model skips: asked who said a thing, deepseek-v4-flash
reached for the label instead and wrote «In a transcript, loop engineering is
described as…», which names nothing a visitor can check (Tom, live corpus,
2026-08-11). So the title is split before the model sees it (last
whitespace-flanked dash wins; a tail longer than 70 characters or ending in `?`
is title, not a person — 179 of the live corpus's 182 uploads split cleanly, and
the other three fall back to the channel), the name leads the line, and the id
and `t` come last because they are arguments rather than something to write a
sentence about. The `[n]`, the source label and the clock string are all
unchanged; only their order and the name in front of them are new.

The drill-down window says whose talk it is in the same words, so an answer
built out of the round where the model did the most work has the same handle to
attribute with as one built out of a search hit.

### 3.3 The loop

```
system + user
  ├─ round 1..50: completion(tools=[search, get_segment_context])
  │                 └─ tool_calls?  → run them, append results, loop
  │                 └─ content?     → done
  └─ final:       completion(tool_choice="none")   ← forced answer
```

- **Max 50 tool rounds** (`VIDTHEQUE_ASK_MAX_ROUNDS`, was 4 until 2026-08-15).
  A model that is still calling tools on the last round gets one last
  completion with tools disabled, so the visitor always gets prose rather than
  a spinner. At 50 the round cap is a runaway guard; the wall clock below is
  what actually ends a long ask.
- **No per-round tool-call cap** (the old ceiling of six, with a note in the
  tool result, went 2026-08-15). A model that wants eight searches in a round
  is doing the work the demo exists to show; the deadline and the per-day ask
  budget are the money guards, not a slice of the batch.
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
- **An annotated marker resolves and renders bare** (*amended 2026-08-11*).
  Models label the marker with the source word the loop showed them —
  deepseek-v4-flash wrote `[29 transcript]` on the first real cross-video ask,
  and a bare-`[n]` pattern resolved nothing: broken prose *and* an empty
  citations array at once. Only the exact words a hit can carry are accepted
  (`transcript`, `ocr`, `frame`, and the `+` pair), so `[10 ms]` in prose stays
  prose, and what lands on the page is `[29]`. Both prompts ask for the bare
  marker as well; this is the belt to that braces.
- **A marker is one to four digits** (*amended 2026-09-20*). Two was enough
  while a search returned ten hits, and a marker the pattern misses is neither
  renumbered nor stripped — it lands in the prose as literal text beside the
  real ones. Letting the model widen a search (§3.2) made three-digit indices
  ordinary, and the first broad ask on the live corpus printed eight of them.
  The page's own renderer keeps two digits, deliberately: what reaches it is
  renumbered 1..N in order of first mention, not the register's indices.
- **Prose must name the source it attributes to** (*amended 2026-08-11*). The
  system prompt and the forced-answer nudge both carry the rule, with the
  failing phrase named rather than implied: never "in a transcript", never "one
  talk says", never "a slide shows" without the talk it belongs to — say
  «In Kyle Mistele's loop-engineering talk [2]…» or «Will Brown (Prime
  Intellect) frames it as… [19]». It is a rule, not a template: no per-sentence
  shape is dictated, and the two examples are there to show that both the talk
  and the speaker are handles a sentence can be built around. The channel
  (`transcript` / `ocr` / `frame`) says *how* the corpus knows a thing and is
  still asked for; it is never who said it.
- The system prompt is short and says the things that matter: answer only from
  tool results, mark each claim with the bare `[n]` of the result it came from
  (no words inside the brackets — the annotated-marker rule above), and
  — since the hits are labelled — *say which channel* a fact came from, with one
  hard rule for the case that fails silently: a frame is a visual match, so
  describe what it shows and never quote text from one. That last part is
  **encouragement, not a template**: no phrasing is dictated, because an answer
  that reads like a form is worse than one that reads like a person who watched
  the talk. It is worth its ~30 tokens because the alternative is prose that
  flattens "he said", "the slide read" and "the screen showed" into one voice —
  which is exactly the distinction the corpus exists to keep.
- One overall wall-clock budget (`VIDTHEQUE_ASK_TIMEOUT_S`, default 180 since
  2026-08-15; was 90) across the whole loop, not per request. A free-tier queue
  that stalls turns into a clean 503, not a held connection. 180 is past
  Cloudflare's 125s proxy read timeout — see §3.5, which was written when the
  budget fitted inside it. The budget ends in an answer, not in an error
  (amended 2026-09-19: a two-minute ask lost everything it had read to one
  timed-out model call). Tool rounds stop once less than 45 s is left, so the
  tools-off completion has time to write; a round whose call fails lands on the
  same completion when evidence is in hand and more than 10 s remains. A
  failure before anything was read is still the clean 503.
- **Output ceiling** `max_tokens=32768` on every completion (was 700). It is a
  runaway-generation backstop rather than a length policy: "under 150 words"
  is still the prompt's ask, and at 700 the clamp was cutting honest answers
  off mid-sentence.

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
endpoint refunds `ask_global` on a non-200 **that bought nothing upstream**
(§4.4). Only the global one: the per-IP minute bucket is the anti-hammer guard,
not the cost control, and someone retrying a broken upstream should still be
slowed down. And only the ones that were free: a loop that got a completion
back and then fell over has already spent the money, and handing the token back
for it would make a paid generation free to anyone willing to fail on purpose.

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

**Transport: two framings over the same POST, one event vocabulary.** NDJSON —
one JSON object per line, `Content-Type: application/x-ndjson` — came first and
is the better parse. `json.dumps` escapes every newline in a payload, so a
corpus title with a line break in it cannot split a frame.

**Amended 2026-08-09: SSE is the second framing, and the page's default.** The
original reasoning against SSE was about the *client* — `EventSource` is GET-only
and the ask is a POST with a body, so the browser side is `fetch` + a reader
either way, and on top of `fetch` SSE's framing is a parser to write where
NDJSON is `JSON.parse` per line. All of that is still true and none of it was
the deciding factor, because the deciding factor turned out to be a middlebox:
**Cloudflare buffers every proxied response except `text/event-stream`.**
Through the tunnel, the NDJSON stream arrived as ninety seconds of silence and
then a burst — precisely the failure this whole section exists to remove, and
invisible in local testing because there is no CDN in front of `localhost`.

So the media type is load-bearing infrastructure, not a taste question, and the
page asks for the one that survives the deployment. SSE here is a *wire format*,
not `EventSource`: the same `fetch` reader parses both, splitting on a blank
line instead of a newline and taking the payload off the `data:` field.

```
event: activity
data: {"event":"activity","id":1,"phase":"start","text":"Searching the corpus for “kv cache”"}

```

The `data:` payload is the whole event object, byte for byte the NDJSON line, so
the vocabulary below is stated once and no framing can drift from another. The
`event:` name is redundant to a reader that looks at `data.event` — it is there
because it is what makes this SSE, and it is stripped of CR/LF before it is
written, because a name that could frame itself is header injection in
miniature. The stream opens with a `: ok` comment: legal SSE, ignored by every
parser, and bytes in hand for a proxy that is deciding whether to buffer.

No keep-alive heartbeat. That was deliberate while the loop's wall-clock budget
(`VIDTHEQUE_ASK_TIMEOUT_S`) was 90 s: inside Cloudflare's read timeout, so a
stream that emitted nothing at all still ended before anything upstream gave up
on it. **The budget is 180 s since 2026-08-15 and that no longer holds.** What
carries the stream now is the activity events themselves — every step
writes two, and bytes reset the timer — so the exposure is a single completion
that generates for more than ~125 s without a tool call, which is the forced
final answer. If a 524 shows up there, the fix is a heartbeat comment on the
stream, not a smaller budget.

**Negotiation, not a second endpoint.** One route, one contract; `Accept` is the
whole difference:

| `Accept` contains | response |
|---|---|
| `text/event-stream` | SSE frames, `Content-Type: text/event-stream` |
| `application/x-ndjson` | NDJSON lines, `Content-Type: application/x-ndjson` |
| both | **SSE** — it is checked first, because it is the one that survives a CDN |
| neither (`application/json`, `*/*`, absent) | the §3 JSON body, byte for byte as before |

curl and any script written against the JSON body keep working, and the MCP
surface is untouched. The page asks for the stream only when the browser has
`ReadableStream` and `TextDecoder`, and sends
`text/event-stream, application/x-ndjson;q=0.9, application/json;q=0.8` — the
fallback is not a worse answer, it is the same answer with nothing to watch on
the way.

**Do not test this through `trycloudflare.com`.** The quick-tunnel hostnames do
not support SSE; a stream that works through a named tunnel will look broken
there, and the conclusion "SSE does not help" is the wrong one to draw twice.

The vocabulary is three events, and every one of them is built from data the
server already had:

| event | when | fields |
|---|---|---|
| `activity` … `"phase": "start"` | before a step's tool calls run | `id`, `text` — "Searching on-screen text for “CVE”", "Searching the corpus for “a” and “b”", "Reading the transcript around 12:34 in “…”" |
| `activity` … `"phase": "done"` | when the last of them lands | `id`, `result` — "6 hits in 2 talks", "5 lines of transcript", "nothing matched", counted over the step's calls |
| `answer` | once, last | `payload`: the §3 body, unchanged; `took_s`, the run's wall-clock seconds by the server's clock (added 2026-09-19, §3.6) |
| `error` | once, last, instead | `status: 503` and `payload`: the §3.4 body, unchanged |

A **step** is every call to one tool that the model asked for in one turn
(amended 2026-09-19, Tom: "searching for A, B and C, rather than searching for A,
searching for B, searching for C"). A turn that asks for three searches and two
reads is two steps, in the order of each tool's first call; a tool that does not
exist is a step of its own. The model still gets one reply per call, in the
order it made them. `id` pairs a `done` with its `start` — that pairing is what
lets the page mark exactly one line as the one still running. The page prints the `start` text only
(amended 2026-09-19, Tom's review): what the model is doing is worth watching,
“44 lines of transcript” is not; `result` stays in the stream for other clients.
The stream is sent `Cache-Control: no-store, no-transform`, because a proxy that
gzips it holds every event until the answer (Next's dev rewrite did). It is **not** a citation `[n]`; the
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

**What a stream owes the budget.** §4.4's refund keys on a status code, and a
stream is a 200 the moment its first byte is written — before the model has done
anything. So the accounting moves to where the cost is known: **no paid
completion, no charge.** It is a `finally`, not an error branch, because the ways
a stream ends without an error event are the interesting ones — the visitor
closing the tab, a mode switch aborting the fetch, the loop being cancelled.
Exactly one of the two rules runs for any given request, so a failed stream is
refunded once.

The condition may *not* be "did an `answer` event go out", which is what it was
until the 2026-08-09 review. A disconnect is something the client chooses, and
the first activity line it can wait for is proof that a completion already came
back: refunding on no-answer paid for a stranger's tool calls, once per
disconnect, for as long as they cared to repeat it. The flag is set when the
provider answers with a non-error status, so it covers the answer case and the
attack case with one fact.

**A disconnect stops the work.** Starlette's streaming response fails its next
write once the client is gone, which closes the generator, which cancels the
upstream call that was in flight. Verified end to end against a real socket: a
visitor who abandons a stream after the first activity line costs no *second*
completion. It does cost the first one, and is charged for it. What cannot be
cancelled is a call already in flight at the moment they leave; that one runs to
its deadline and is thrown away — and, having been generated, it is billed.
*Amended 2026-09-19: except a visitor's run, which finishes whoever is
reading (§3.6).*

**Refusals stay status codes.** Everything that can be refused before the model
is reached — a 429 from the limiter with its `Retry-After`, a 503 for a missing
key, a 400 for a bodyless request — happens *before* a byte of stream exists and
comes back as it always did, JSON and all. That is what keeps the page's
countdown working: a refusal delivered as an event inside a 200 would have to
reinvent the header. Only a request that reached the loop streams, and once it
streams it is committed to 200 with a terminal event.

`Cache-Control: no-store` and `X-Accel-Buffering: no` ride along on both
framings: nginx buffers a proxied response by default, which would hold every
activity line until the answer landed — that is, exactly the ninety seconds of
silence this removes.

**`X-Accel-Buffering` is an nginx header and nothing else reads it.** It is kept
because nginx is a plausible thing to put in front of this, not because it does
anything for the deployment that actually exists. Cloudflare decides on the
content type, which is why the fix for the tunnel was a second *framing* and not
a third header. Adding more buffering-hint headers in the hope that one of them
lands is how this file grows folklore; if a proxy buffers, find out what that
proxy keys on.

---

### 3.6 A visitor's ask outlives its stream (added 2026-09-19)

A phone that switches apps mid-ask drops the stream. Under §3.5 the loop
stopped with it: the question came back (the input still held it, and a
reloaded `?ask=` link loads it), but the answer was gone, the completions it
had run were paid for, and asking again started from zero. Tom's ruling
(DECISIONS.md, 2026-09-19): the answer comes back to the visitor who asked,
and to nobody else.

- **The visitor.** The page keeps a random id for the browser in
  `localStorage` (`v-` and 32 hex digits; no cookie) and sends it on every call
  it makes as the `X-Vidtheque-Visitor` header, and on an ask as `"visitor"` in
  the body too. The header is what the limiter charges (§4.1) and the loop
  prefers it; the body is the same id, and a client that sends only it still
  gets a run. Where storage is refused the id lasts as long as the page. An id
  outside `[A-Za-z0-9_-]{16,64}` is no id.
- **The run.** With a visitor id and a streaming `Accept`, the loop runs as its
  own task and every event it yields is kept. The stream only reads the run, so
  a reader leaving stops the reader, never the run. The same visitor asking the
  same question (spacing and case aside, same `tags`) while it runs, or within
  30 minutes of its answer, gets the run's events from the first and then the
  rest live, and the model is not asked again. A run that ends in an `error`
  event is not kept, so asking again is a new try. Another visitor asking the
  same words gets a run of their own: answers are never shared. Runs live in
  the process: a restart loses them, and at most 256 finished ones are kept,
  oldest first out.
- **`POST /api/ask/resume`**, same body, finds that visitor's run and streams
  it exactly like the ask did, or answers `204` when there is none (not `404`:
  a browser logs every failed fetch as a console error, and "no run" is the
  ordinary answer on a first visit). It never starts a run, and the limiter
  charges it to the search bucket, not the ask ones. The page calls it when a
  `?ask=` link loads, when Back or Forward lands on an ask, and when a page
  whose stream died becomes visible or comes back online.
- **The bill.** A new run is charged as before and refunded under §4.4 when it
  ends having bought nothing. A request that finds a run already going or kept
  bought nothing upstream, so `ask_global` goes back at once; the minute
  buckets stay spent, as for any retry. A visitor who leaves and never returns
  now costs a whole ask instead of the steps before they left, bounded as
  every ask is by the round cap, the deadline and the limits.
- **`took_s`.** A replay renders at once, so the page cannot time it: the
  answer event carries the run's duration by the server's clock, and "Worked
  for N s" prints that when it is there.

Without a visitor id — curl, a script, the JSON body — nothing here applies and
§3.5 holds as written.

## 4. Rate limiting

App-level, in-process. On `/api/*` and `/frames/*`, in public mode only. Two
shapes, because two different things are being guarded: **token buckets in
memory** for the per-minute rates, and **a UTC-day counter written to SQLite**
for the daily `ask` budget.

**In-memory, single process, deliberately.** vidtheque is one uvicorn process
holding one SQLite writer; there is no second replica for a shared counter to
be shared with. If this ever grows a replica, the limiter is the first thing
that needs Redis — and that is a bigger change than swapping a backend, because
the SQLite writer would need to move first. Stated here so nobody reads the
in-memory dict as an oversight. A redeploy resets the minute buckets and that
costs nothing: they guard against hammering, and nobody hammers across a
restart they did not know happened.

**Amended 2026-08-09 (Tom): the daily budget is not one of those.** This section
used to end "the daily `ask` cap is approximate across a restart… for a budget
guard on a free tier that is acceptable; for anything where money is at stake it
would not be." The model behind `/api/ask` is paid now, and on a launch day a
redeploy happens several times an hour — so a 50/day cap in a Python dict was
guarding money only until the next deploy. It is persisted; §4.2 is how.

### 4.1 The buckets

| bucket | routes | default | env |
|---|---|---|---|
| `search` | `/api/search`, `/api/videos`, `/api/meta` | 30/min per visitor | `VIDTHEQUE_RATE_SEARCH_PER_MIN` |
| `search_ip` | the same | 300/min per IP | `VIDTHEQUE_RATE_SEARCH_IP_PER_MIN` |
| `ask` | `/api/ask` | 5/min per visitor | `VIDTHEQUE_RATE_ASK_PER_MIN` |
| `ask_ip` | `/api/ask` | 60/min per IP | `VIDTHEQUE_RATE_ASK_IP_PER_MIN` |
| `ask_global` | `/api/ask` | 50/UTC day, whole server, **persisted** | `VIDTHEQUE_RATE_ASK_PER_DAY` |
| `frames` | `/frames/*` | 600/min per IP | `VIDTHEQUE_RATE_FRAMES_PER_MIN` |

**Per visitor, with the address as a ceiling (2026-09-20, Tom).** These were all
per IP, and a conference hall is one NAT address for hundreds of people: five
asks a minute and thirty searches a minute *for the room* is not a rate limit,
it is an outage in the first minute of a talk. So the two browser buckets are
charged against the visitor id the page already keeps (§3.6), sent as
`X-Vidtheque-Visitor` — a header rather than the body, because the limiter
charges before anything has read a byte, and buffering the body would mean
buffering the one path that streams.

A bucket named `<name>_ip` is that bucket's ceiling, charged to the address as
well. It exists because an id the page mints is an id a script can mint: the
visitor bucket buys fairness between people, never safety against one. What
bounds the money is unchanged — `ask_global`, counted for the whole server and
written down. A request with no id pays the ceiling alone and nothing else,
which is the honest answer for the two callers that cannot send one: an `<img>`
fetching a thumbnail, and the page's own server-rendered reads.

`frames` stays per IP for that reason, and is loose on purpose: one screen of
results is ~10 thumbnails, and a room browsing at once shares one address. It is
there to stop a scraper walking the whole keyframe directory, not to police
normal browsing.

`/api/ask` is charged three times: the visitor's bucket, the address ceiling,
and the global day. They are checked in that order — narrowest first — so one
visitor cannot spend the day's budget before being told to slow down, and a
refused request costs nothing in the buckets that had already let it through.

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

**The daily bucket is not a bucket.** It used to be the same maths with
`window = 86400`, so the budget trickled back through the day instead of
everything unblocking at UTC midnight — nicer behaviour, and unimplementable
durably. A refilling bucket's state is `tokens` plus a `time.monotonic()`
reading, and a monotonic clock has no meaning across a process boundary; writing
wall-clock instead would make the rate limiter depend on the system clock being
sane, which is a worse trade than the one made here.

A *daily budget* has a durable form that a rate does not: how much of today has
been spent. So `ask_global` is a counter — `spent` against a capacity, keyed on
`(bucket, client, UTC day)` — and the consequences are all visible:

- **A restart resumes the day.** The count is read back at boot; the process
  that comes up after a redeploy knows what the one before it spent.
- **The reset is midnight, not a trickle.** A spent day unblocks at 00:00 UTC.
  `Retry-After` on a refused `ask_global` is the seconds until then, which can be
  hours. That is the honest answer for a budget that resets by the date changing,
  and saying less would invite the retry the 429 exists to stop.
- **Nothing else changed.** The per-minute buckets are still continuous-refill
  buckets, still in memory, still reset by a redeploy. `ask_per_day` still means
  what it said; only the shape of the refill moved.

**In-memory stays the fast path.** The counter in the dict is what decides every
request, exactly as before — SQLite is the *record*, not the gate. Today's rows
are read once at boot and the deltas (`+1` on a charge, `-1` on a refund) are
queued and written behind the request by a single drain task, because the
limiter is a synchronous ASGI middleware and the mcp process owns exactly one
write connection (index-schema §5). A visitor never waits on the writer. An
orderly shutdown drains before the database closes; a `kill -9` can lose
whatever was in flight, which is one ask, not the day. The table is
`ask_budget` — index-schema §1.11 — pruned to the last 30 days at boot, which
leaves the operator a month of "what did the demo cost".

Response on refusal:

```
HTTP/1.1 429 Too Many Requests
Cache-Control: no-store
Retry-After: 4
X-RateLimit-Limit: 30
X-RateLimit-Remaining: 0

{"error": "E_RATE_LIMIT", "message": "Too many requests — 30 per minute.",
 "retry_after_s": 4, "bucket": "search"}
```

A refusal from an `_ip` bucket says **"Too many requests from this network"**,
because the visitor it refuses has asked nothing: they are behind a busy
address, and the unqualified sentence reads as an accusation of something they
did not do.

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
- **`ask_endpoint`**, on a non-200 that bought nothing upstream. Nothing else
  refunds anything; a search that returns zero rows is still a search that ran.
- **The ask *stream*, on any run that bought nothing upstream** (§3.5). Not a
  third rule, the same one: a stream's status line is written before the model
  has done anything, so "non-200" cannot express it. Exactly one of the two runs
  per request — a streamed request is a 200 — so a failed stream is refunded
  once, never twice.

**What "bought nothing" means, exactly.** One per-request flag, set the moment
the provider answers with a non-error status, because that response is a
generation and it is billed whether or not the loop ever turns it into prose.
An explicit refusal (401/403/429/5xx) and a request that never reached the
provider leave it alone — which is the launch-day flap this section exists for,
still refunded, still free. One caveat, deliberate: a read timeout after the
provider accepted the request may have been billed and is counted as unpaid,
because a timeout is far more often a dead upstream than a generated answer,
and nobody can force one on demand.

A refund refills first and is capped at capacity, so it can never mint a token
the bucket never had, and refunding a bucket that no longer exists (swept) is a
no-op rather than a resurrection.

**A refund reaches the row, not only the dict** (§4.2). `ask_global` is the
bucket that is written down and it is also the only one that is ever refunded,
so the two features are the same feature: a `-1` delta is queued exactly where
the `+1` was, floored at zero by the same rule that caps a bucket at capacity.
Without it, a launch-day flap that costs nothing in memory would cost the day on
the next restart — the failure this refund exists to prevent, deferred by one
deploy instead of fixed.

Two properties make that exact rather than approximate:

- **The charge and its refund name the same UTC day.** The day is read once per
  request and carried on the scope beside what was charged, so a ninety-second
  ask that starts at 23:59:30 gives its token back to the day it took it from.
  Yesterday's refund never lands on today's counter, which would otherwise hand
  out one free ask a day at midnight.
- **The refund path is synchronous all the way down to the delta.** It fires
  from a `finally` inside the stream's generator, which is the path taken when a
  visitor closes the tab — a budget that needed to `await` to be given back is a
  budget that leaks on exactly the disconnect it was written for.

The rule is identical across all three transports: the JSON POST, the NDJSON
stream and the SSE stream share one generator and one accounting block, and the
tests for the mid-stream refund are mirrored across both framings for that
reason.

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

Thumbnails are requested at `w=320&q=70` — 160×90 CSS pixels at 2x — and the
route *does* resize, through the byte-capped `derived/` cache (index-schema §6),
clamping `w` server-side to 64..1280. The width is the facade's choice of a size
the route already allows; it is not a limit the browser gets to set.

**One width, every hit** (*amended 2026-08-11, Tom's review*). There were two:
`w=192` (96×54) for a transcript or OCR hit, and `w=320` (160×90) for a **frame**
hit, on the argument that a frame matched on its image and the image is the
evidence, so it should be bigger. The argument is sound and the result is not:
in one Sources list a `frame` citation sat beside a `transcript+ocr` one at two
different sizes, and in the results a card cover was a third (128×72). Three
rectangles down one column, changing for a reason a reader cannot see. So the
frame's box is now every kind's box — the evidence keeps the room it deserved
and the list stops looking broken. `api.THUMB_WIDTH` is the only width left
(`FRAME_THUMB_WIDTH` is gone, and `ask.py` reads the same constant), the CSS has
one `.hit-thumb` rule, and below `--bp-hand` it is one size down (112×63) for
every kind alike.

---

## 6. The page

**Amended 2026-09-05: this section is a design contract, not a description of
Python code.** Everything below — the states, the floor, the provenance rules,
the enlarge dialog, the grouping, the activity log — is what the page must do,
and `web/` is what does it. What is *not* here any more is how it reached a
browser: `/demo`, `/`, the `/static/{asset:path}` handler, the document policy
they shared and the `lab/` denylist that bounded them all left `public/` with
the pages (§1). The paragraphs they occupied are below, marked, because the
arguments outlived the code.

*Until 2026-09-05:* the page was three files under `vidtheque_mcp/public/static/`
— `index.html` + `app.js` + `style.css` — no build step, no framework, no
external requests, shipped inside the wheel as package data. Both documents went
through one small helper and carried the same headers, because the CSP and its
three companions were a property of *being a document on this app* rather than
of a route, so the two pages could not drift apart on the policy the 2026-08-10
audit wrote. That property is now the front end's to hold, over both of its
pages, and no test in `mcp/` can see whether it does — §7 item 0.

*Also until 2026-09-05, the asset route and its denylist (added 2026-08-11).*
`/static/{asset:path}` resolved under the packaged `static/` root, refused
anything that escaped it, and typed the response by suffix — so *every* file
under `static/` was public the moment the demo was. That was one directory too
generous: `static/lab/` was the landing workshop, competing prototypes of a page
that had not shipped, and it answered at `/static/lab/versions/v5.html`
(`research/release-staging-2026-08-11.md` §9, finding 1). The route refused the
`lab/` subtree by prefix, checked on the *resolved* path so `../lab/…` was
covered by the same line — by *name* rather than by absence, because a prefix
covers the next prototype directory that nobody remembers to think about where a
`git mv` covers only what was moved. The prototypes retired to the
`archive/landing-lab` branch on 2026-08-12 and `static/lab/` has been gone from
`main` since. The rule it stood for now belongs to whatever serves `web/public/`:
adding a file there is publishing it.

### 6.1 The landing at `/` (added 2026-08-11)

*Where it lives, as of 2026-09-16: `web/src/components/public/landing/` and
`web/src/app/(public)/page.tsx`,
with the stills in `web/public/landing/`. The paths named below are the Python
bundle it was ported from and are gone from `main`; the design they describe is
unchanged, which is why this section is.*

`static/landing/` — `index.html` + `landing.css` + `landing.js`, its stills in
`grid/`, `moments/`, `pgm/` and `wall/`, and `data.js`, the read-only corpus
readout every count and frame on the page is rendered from. It makes **zero
network requests**: the landing argues, the demo works.

This is `lab/versions/v5.html`, the prototype Tom picked on 2026-08-10 and
which `DESIGN.md` names as the visual system's reference implementation. It
graduated unchanged in everything a reader can see. What changed is what a lab
piece may do and a served document may not:

- **The inline `<style>` and `<script>` moved out, verbatim, and the ten
  `style=` attributes became classes.** The CSP above carries no
  `unsafe-inline`, and that is the thing the audit actually bought — a policy
  with `unsafe-inline` in it is a policy worth quoting rather than having. The
  alternative was widening it (or maintaining a hash list) on the one page every
  visitor loads first, to avoid moving two blocks once. Script-side `.style`
  and `cssText` assignments stayed: the lift and the OCR boxes are built out of
  them, and CSSOM is not something CSP governs.
- **Asset paths are root-absolute** (`/static/landing/…`). The document is
  served at `/`, so a relative `src` resolves against the site root.
- **The vendored font copies are gone**; the page uses the packaged pair at
  `/static/fonts/`, which the lab copy was byte-identical to. One pair of
  faces, three surfaces.
- **The `landing v5 · projection room` stamp is gone** from the footer, the
  `<title>` and the `<html>` element — the lab's own label, flagged as such at
  cull time.
- **`<head>` gained the front door's furniture**: the drawn favicon,
  `theme-color`, `color-scheme` and the unfurl tags, each copied from the demo
  page rather than re-decided, so the two surfaces cannot disagree.
- **The CTA is a link to `/demo`** — reading "Open the demo" (Tom, 2026-08-11:
  the hero already performs the ask; the CTA names the next room; the demo's own
  action button keeps "Ask the corpus"). It was a button that advanced the
  canned hero cycle. The cycle is the landing's own show and still runs on its
  own (and its example chips still drive it); the CTA is the one control that
  *leaves*, which is why it is a real `<a>` — middle-click works, and it is the
  same affordance whether or not the script ran.

The demo's rail keeps its one link out, into `/dashboard`, and its wordmark —
already `href="/"` — now goes to the landing, which is what a wordmark means.

**Amended 2026-08-10 (Tom's voice cull).** The surface keeps labels, values and
one-line states; it no longer explains its mechanics or argues for its design.
The implementation facts removed from the page remain in this contract: the
endpoint is read-only (§1), exposes seven read tools (§1), and returns people
to the original talks (§6 item 4); the videos remain their creators' work. The
public framing now follows the locked positioning contract directly: the
knowledge of AI Engineer 2026, on tap; your agent watched it.

**Amended 2026-08-10 (the projection-room rebuild).** The look is no longer
described here. `DESIGN.md` is the visual contract for every product surface and
it names this one: *the demo is the landing's continuation, selling,
functional-first* — same ground, same gold, same lime, same faces, same zero
radius, same receipt slab, one rung down the display ladder, and motion only
where **this visitor's** query is running. What this file still owns is
everything below: function, data, clamps and copy. Three sentences of the old
paragraph are retired by that, and are recorded here so nobody restores them
from memory: the palette is not "six custom properties at the top of
`style.css`" (it is DESIGN.md's tokens, and the six role names are aliases into
them); the faces are not the system stack (Archivo VF and JetBrains Mono VF are
vendored beside the stylesheet); and there is no light scheme (`color-scheme:
dark`, one `theme-color`, no `prefers-color-scheme` block, no toggle — a
projection room does not have a day mode).

What survives unchanged is the *ratio*: the corpus is the content and the chrome
is nearly invisible. No sidebar, no charts, no card that exists to be a card.

The `<head>` is part of the deliverable, not boilerplate: a title that says
what the thing is, a description, `og:title`/`og:description` (no `og:image` —
a wrong one is worse than none), `viewport`, one `theme-color` (`#040405`) so the
browser chrome follows the page, and a **drawn** film-frame favicon as an inline
SVG data URI rather than an emoji, because an emoji favicon renders as a
different glyph on every platform and disappears in a monochrome tab strip.

Layout, top to bottom:

1. **Header** — wordmark (the page's `<h1>`) and the positioning line, “The
   knowledge of AI Engineer 2026, on tap. Your agent watched it.”, and —
   *added in dashboard phase 4 (2026-08-09)* — **one link**, right-aligned on
   the wordmark's baseline: `Browse the corpus →`, into the dashboard's
   read-only projection (`dashboard.md` §2.4). One link and not a nav: this
   page is a search surface and the search box is its one primary action, so the
   link is a quiet outline rather than a second filled control beside it. It is
   hidden until `/api/meta` reports `browse` (§2.3). Below ~30rem it wraps to
   its own line under the tagline rather than squeezing it.

   *Amended 2026-08-10 (the rebuild).* The header is the landing's rail: the
   wordmark (still the `<h1>`, per the Font-Logo Rule — the word, the gold full
   stop, nothing else), the corpus size as a micro-label, and the same one link,
   which keeps its accessible name `Browse the corpus` at every width and prints
   `browse →` below `--bp-hand`. The positioning line moved *down* into the
   hero, where it is the page's one big line and its lede — the rail is a rail,
   and a tagline in it competes with the thing it is a tagline for.
2. **Search box** — autofocus, submits on Enter. One primary button, labelled
   by the current mode (`Search` / `Ask`; the sparkle went, Tom, 2026-09-19).
   *Amended 2026-08-10:* it is the
   landing's query bar — a gold cue, the query in the machine's face, **a state
   cell that prints the machine's own word** (`ready` / `scanning` / `reading` /
   `no hits` / `refused`), then the action. The state cell is the honest signal
   that something is running (the Motion Law's alternative to a spinner), and it
   is a word first and a colour second.
3. **Mode tabs and controls row** — a two-tab **mode switch**, `search | ask`,
   sits on the query bar's top edge, hidden entirely when `ask_enabled` is
   false. Under the bar, one row: the filter chips (`all` / `transcript` /
   `on-screen text` / `frames`, mapping to `content_type`, `all` by default) in
   search, a one-line note in ask, where the model picks the channel. *Amended
   2026-09-18 (Tom):* the note reads "Every answer cites the talk and second."
   on both consoles. *Amended
   2026-09-16:* both modes' row contents, submit labels and cold-page panels
   share one grid cell each, the inactive one `visibility: hidden` and `inert`,
   so switching modes moves nothing on the page.
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
   numbered to match — `[1]`, `[2]`, `[3]` in order of first mention, renumbered
   by the API from the hit numbers the model cited (amended 2026-09-19); a
   spoken citation is pictured with the keyframe on screen at its second (§2.1), and the log folded underneath. A 503 replaces the pane
   with the degradation message and a "search instead" button; a 429 says how
   long to wait.

   *Amended 2026-08-11: the pane carries the measure, and the prose fills it.*
   `--prose` (70ch, DESIGN.md's cap on running text) sat on the paragraph
   inside a plate at the full `--bp-stack` column: 620px of text in a 947px
   bordered box at 1440, so every line broke two thirds of the way across and
   read as a wrap bug rather than as a measure. The cap belongs to the
   container — this pane holds one thing, running prose — so it is written on
   the pane, in the prose's own font size, which is what makes `ch` resolve
   against the face the paragraph is set in. The pane lands at ~673px, under
   the query bar it is the answer to (`--query-w`, 704px), and nothing inside
   it has to guess a second measure. Sources rows and the log narrow with it.

   *Amended 2026-08-11, same review, one round later: the measure is a column's
   and the pane is the page's.* The fix above is correct about the paragraph
   and wrong about the object: a 673px pane holding the answer, its sources and
   its work log is the thing this page exists for, laid out as a strip down the
   left of a 1440 screen with two thirds of the width unused. Long prose is
   still uncomfortable past ~90ch, so the answer is **layout, not a wider
   paragraph**:

   - **The chassis is `--maxw` (1460px)**, DESIGN.md's own column, like every
     other surface in the system. It was `--bp-stack` (1120px) on the argument
     that this page is read rather than toured; what that bought was margin.
   - **Above `--bp-wide` (1380px) — the demo's one own breakpoint, which
     DESIGN.md allows a surface for exactly this kind of structural change —
     the ask pane takes the full column and spends it on layout.** Two columns:
     the answer at `--prose` on the left, its Sources beside it on the right,
     separated by a hairline column rule. `--bp-wide` is not a taste threshold:
     it is the width at which 70ch of prose *and* a source row that can still
     print a whole receipt both fit inside the plate. Below it, one column and
     the pane is the measure, exactly as the previous amendment left it.
   - **The left column is the answer and how it was made**: the prose, then
     "Show its work", then the model line, which sits at the bottom of the
     column because the Sources span all three rows. A source list is nearly
     always taller than the paragraphs it supports.
   - **The work log uses the width.** While the model works there are no
     sources yet, so the pane is one full-width column and the log is the whole
     of it — a console, at a console's width (§6.6).
   - The pane is **the same width in both states**, so the answer arriving
     changes the pane's rows and never its width. A *degraded* pane — one
     sentence and a button — stays at the measure: widening a refusal is the
     one place the extra room says nothing.

   Measured at 1440: pane 1319px, prose column 621px, sources column 607px, no
   horizontal overflow, receipts intact. At 1920 the chassis caps at 1460 and
   the two columns are 621/604.

   *Amended 2026-09-19 (Tom, DECISIONS.md): one column, read as a chat turn.*
   The two columns above `--bp-wide` are retired: the Sources arriving beside
   the answer reshaped the whole pane at the moment it landed. The pane is one
   column in the query bar's measure (`--query-w`) at every width — the work,
   folded to one line (§6.6), then the prose, then the Sources, then the model
   line — so everything arrives under what is already on screen. A Sources
   snippet is clamped to three lines; the receipt under it is the whole of it. The
   excerpt is a control (Tom, same day: "see exactly what the LLM read"): a
   click opens what the model read — the `read` lines (§3) with their seconds,
   the cited moment ruled in gold — or, for a search hit, the whole excerpt,
   and "What the model read" under it names the action and the span.
   A `[n]` marker shows its moment on hover or keyboard focus — the frame, the
   talk, the second and the snippet — in a card fixed to the viewport (so the
   panel's clip cannot cut it), gone on leave, blur or scroll. A touch screen
   has no hover to end it, so a tap only follows the link.

   The console is centred on the page, and the hero above it on `/paris` and
   `/demo` with it: the form, the cold panel, the status line, a notice and the
   answer share the bar's column; result rows take a wider one (64rem) on the
   same axis, because a row spends the width on its snippet and its receipt.
6. **"Add this corpus to your own agent"** — the label `MCP endpoint`, the
   `mcp_url` from `/api/meta`, a copy button, and the one-liner:
   `claude mcp add --transport http vidtheque <mcp_url>`. *Amended 2026-08-10:*
   the one-liner gets a copy button of its own — it is the line somebody
   actually pastes — and a clipboard that refuses still selects the text, so
   there is always a way to take it.

   *Amended 2026-09-18 (Tom): three clients and custom connectors.* The four
   rows replace the endpoint and Claude-only pair, each with its own copy
   button: `mcp endpoint` / `<mcp_url>`, `claude code` /
   `claude mcp add --transport http vidtheque <mcp_url>`, `codex` /
   `codex mcp add vidtheque --url <mcp_url>`, and `mistral vibe` /
   `vibe mcp add vidtheque --url <mcp_url>`. Under them: "Claude, ChatGPT and
   Vibe: add a custom connector and paste the endpoint. No sign-in." Every
   command uses the one `mcp_url` returned by `/api/meta`.
7. **Footer** — the vidtheque name, the GitHub link, and one muted line
   that never gets culled: “The videos belong to the people who made
   them.” — the attribution ethic is page-visible wherever creators’
   content is served (positioning stress-test obligation), not only in
   this contract.

   *Amended 2026-08-11:* that line now carries **one link, “Removal on
   request”**, into `docs/takedown.md`. The same positioning obligation that
   put the sentence on the page also promises *“take a channel out on
   request — one row, one command, and we say so publicly”*
   (`research/positioning-2026-08-10.md` §9.1), and a promise whose procedure
   is unreachable from the surface making it is half a promise. Four words,
   no argument, in the voice the cull left: it states an operation, the way
   `MCP endpoint` does.

### 6.1 The states

A demo is judged on the four screens that are not "ten results came back".

- **Before the first search** the page offers a handful of clickable example
  queries — real ones, tailored to the corpus, and written in `index.html`
  because they are copy — and the list of videos actually indexed, from
  `/api/videos`.
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

  *Amended 2026-08-10 (the rebuild): four of the five are re-drawn from
  `research/demo-queries-2026-08-10.md`,* which is the harvest that was checked
  at click level — the cited second pulled back verbatim, every frame receipt
  fetched over HTTP and looked at. The two rules above are unchanged and the
  set still covers every chip in the row:

  1. `context window costs money tokens` (**ocr**) — the flagship, and a better
     one than the term it replaces: the payload is `transcript 0 · ocr 1` and
     **not one of the prices on that slide is spoken anywhere in the talk**
     (§2.1). It is the whole argument for reading the screen, in one result.
  2. `context engineering` (all) — the slide carries the Karpathy sentence
     verbatim while the transcript has only the paraphrase (§2.2), and rank 1 is
     a 22-second citation inside a 46-minute talk (§4.1).
  3. `architecture diagram with boxes and arrows` (**frame**) — carried over
     from the 2026-08-09 harvest, which the 08-10 file adds to rather than
     replaces: a visual query still has to reach the frame leg to demonstrate
     anything, and 08-10 has none.
  4. `human annotation calibrate LLM judge` (**transcript**) — three talks, two
     of them directly contradicting, every quote verbatim (§3.1).

  *Amended 2026-08-11 (Tom's review): there are four, and the refusal is not
  one of them.* The fifth was `FlashAttention-4` — `0/0`, naming the legs that
  ran and the one it deliberately did not (§5.1) — and the argument for it was
  that a demo whose example list contains a refusal makes the product's third
  pillar in one click. On the page it is the opposite: a stranger clicking the
  last chip in a row of five gets a screen whose entire payload is the word
  *nothing*, and the empty state stops being an edge case only by becoming a
  fifth of the demo. **Honesty is not the thing a demo has to prove** — every
  chip now returns evidence. The refusal path is unchanged and still one typed
  query away, and §5.1 remains the receipt for it.

  *Amended 2026-08-11 (Tom): **ask is the default mode**, and the examples come
  in two sets, one per mode.* The headline says *"Ask it something"*; the box
  under it now is the one that does. `search | ask` boots on **ask**, search is
  one click away and loses nothing.

  **The default is stated in the markup, not applied by `app.js`.** The button's
  word (`Ask`), the placeholder, the hidden content-type row, the pressed
  mode and the visible example set are one state, and applying it a round trip
  after `/api/meta` lands is the same stutter the font preloads exist to
  prevent. `state.askMode` starts `true` to match, and the two must move
  together. Exactly three things override the default, and each is explicit:
  `ask_enabled: false` from `/api/meta` (a deployment with no key — the one
  load that swaps mode on screen, and it is the misconfiguration, not the
  demo), `?ask=0`, and `?q=` with no `ask=` (§6.2).

  **Mode-appropriate examples.** A keyword chip under an ask box teaches the
  wrong thing twice: it tells a stranger this is a search box, and clicking it
  spends a model call on a phrase nobody would ever ask out loud. So `#ex-ask`
  and `#ex-search` are two sets in the HTML — still copy, still the two rules
  above — and `setAskMode` swaps them with the switch. What is on screen is
  always runnable in the mode that is on screen, and a chip click runs in the
  **current** mode. The search set is the four above, unchanged: keywords stay
  keywords. The ask set is five questions *(amended 2026-08-13, Tom's picks —
  the two judge questions were two shades of one argument and read as riddles;
  the announcement set wants hard, contested questions with no obvious
  answer)*, ordered by demo strength, each receipt-checked against the live
  310-talk corpus in `research/demo-queries-2026-08-13.md`. The default rule
  stands — **a question should share no vocabulary with its answer** (a
  question whose words are already in the transcript is a search wearing a
  question mark) — with one deliberate, named exception below:

  1. `Why does loop engineering look so much like building RLVR environments?`
     — Tom's flagship ("Tom's ask-mode flagship",
     `research/demo-queries-2026-08-10.md`): `fts 0 cues`, a pure vector
     leg, 7 citations across 6 talks, and an answer no single talk contains.
     The whole pitch in one click, so it is first.
  2. `How to do reinforcement learning when the task can't be verified?` —
     `fts 0`, 6 talks; "verifier's rule" comes back quoted by a different
     speaker than the one who coined it.
  3. `Does training on model-generated data compound quality or collapse it?`
     — 7 talks with a head-on disagreement: the collapse dogma and the talk
     that attacks it, plus the data-quality camp.
  4. `Is the harness or the model more important?` — 7 talks, three camps,
     one numeric receipt (52.4% → 76.2% with only the harness changed). The
     exception to the vocabulary rule: the fight cannot be named without its
     two nouns (`fts` fires, ~193), and the receipts earn it.
  5. `Why do agents write bad AGENTS.md?` — `fts 0`; retrieval leans on one
     talk's account of what a *good* AGENTS.md is, so this is the set's purest
     synthesis test, and it is deliberately last.

  Five, not three. The per-click budget rule is untouched — nothing fires
  without a click — and the five sit on five disjoint axes (agent loops,
  post-training, data, harnesses, docs-for-agents), so no click proves what
  another already proved. The question-mark/keyword split between the two
  sets, and everything else in this section, is unchanged.

  **Ask-first still has to teach.** A search box teaches itself — type words,
  get rows with those words in them. A question box does not: nothing on screen
  says whether the answer is *read out of* these talks or invented over them,
  and that distinction is the product. So each set carries one line under its
  heading (`.exnote`) — the ask set's says the answer is built by reading the
  corpus and comes back with the sentence, the talk and the second; the search
  set's names the three channels. The corpus listing from `/api/videos` is
  under both, unchanged.

  **Nothing fires an ask without a click.** The budget and rate-limit UX is
  untouched, no ask auto-runs on load, and the `?ask=1` rule below is unchanged
  — a shared question arrives loaded and unfired.
- **Nothing matched** is one sentence and one way out: `Nothing in the corpus
  matches this.`, then `Try one of the examples.` — a `.linky` that clears the
  query, resets the channel and puts the cold page (with its four chips) back.
  `Search all` survives as a second line, and only when a content-type filter
  is pinned: that one is not advice but the visitor's own filter, and leaving
  someone inside `frames only` is how a demo looks broken. When the facade's
  `data_status` says `empty` (§2.1) it says *that* instead: an instance with
  nothing indexed is not a query the visitor got wrong.

  *Amended 2026-08-11 (Tom's review).* It used to read `Nothing matched “<q>”.`
  over `Try fewer words.` Both are gone: the query is quoted back two
  centimetres under the box that still holds it, and "try fewer words" is a tip
  nobody takes, phrased as though the visitor had made a mistake. The state
  says what is true about the corpus and points at the four things that work.
- **The semantic-legs note never reaches this page.** A query with no lexical
  footing makes the search tool print `note: no word of this query occurs
  anywhere in the corpus, so the semantic (nearest-neighbour) legs were not
  queried — they would have returned their k nearest vectors regardless.` To an
  agent that is the difference between a genuinely empty answer and an
  under-searched one, and **the MCP tool keeps saying it** — §2.4's rule that
  the facade does not rewrite a note's body is intact. What changed
  (2026-08-11) is that the *demo* drops it whole: under a one-line "nothing
  matched" it is two clauses of query-layer internals defending a refusal.
  `humanize.AGENT_ONLY_NOTES` carries the clause it is matched on and
  `api_routes(demo=True)` is the only caller that asks — the dashboard's JSON
  and every future consumer keep the full commentary, which is why the flag is
  opt-in and lives on the route group rather than in `humanize.notes` itself.
- **Refused or broken** — a 429 renders the limiter's `Retry-After` as a
  *ticking* countdown with the retry disabled until it reaches zero; a failed
  fetch says the server could not be reached and offers the same retry; a 503
  in ask mode keeps its "search instead" button and counts down too. The
  countdown says only when to try again; it does not explain the limiter. No
  error ever shows a status code or an upstream message.
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
  for an answer, it shows the work that will produce one. *Amended 2026-08-11:
  the log's own box **is** reserved* — six lines, fixed, before the first event
  — because "we cannot reserve for the answer" was never a licence for the log
  to grow a line at a time under a reader.

*Amended 2026-09-16 (Tom): one console, and no skeleton.* Search and ask are
one client component on both `/demo` and `/paris`: one form, one input
element, one mode control, one output region
(`web/src/components/public/console/`, DECISIONS.md 2026-09-16). What changes on this
list:

- **The default mode is decided before the first paint, not corrected after
  it.** The server reads `/api/meta` and hands the console a resolved
  `askEnabled`, so a deployment with no key renders search from the start. The
  post-hydration swap described above is gone, and so is the reserved mode
  switch that appeared only when the boot call landed.
- **The console is awaited with the hero, not streamed under it.** A console
  that swaps in below the hero moves everything under it, the connect panel
  included: 0.15 layout shift at 1440px and 0.42 at 390px on `/demo?q=cache`,
  measured 2026-09-16. Only the chrome's meta leaves and `/paris`'s programme,
  which sits below the console, stream.
- **Loading keeps the previous screen.** A search in flight leaves the results
  (or the cold page) where they are, dimmed and `aria-busy`, while the state
  cell says `scanning`; the reply replaces them only when it lands, and a
  refusal replaces them with the refusal. The skeleton and its guessed shape
  are retired: replacing real rows with grey ones was itself the jump the shape
  guess tried to minimise.
- **Nothing on this list navigates.** An example, `Search all`, `one of the
  examples`, a chip, `Search instead`, a retry and "More results" all act on
  the console's own request. Each link keeps a real `href` for a middle-click.
  A retry repeats the failed request, never a page refresh.
- **Focus stays in the box.** Submitting no longer blurs the input, except on a
  coarse pointer, where the blur dismisses the keyboard over the results.
  Switching mode keeps the same element, its value and its caret.

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
every thumbnail, and AA contrast for every colour pair the page actually uses
(*amended 2026-08-10: one scheme, so one sweep. DESIGN.md carries the measured
ratios and fences `--fg3` as the system's single sub-4.5:1 value, which is why
no label that carries a fact is set in it on this surface*).

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

*Amended 2026-08-11, with ask as the default mode (§6.1).* The URL has to be
able to say **search**, or a link copied out of search reopens in ask. `ask` is
now three-valued: absent means the default, `?ask=1` is unchanged, and `?ask=0`
is search — written by the switch, read on load. One more rule keeps every link
made before the change honest: **`?q=` with no `ask=` at all means search**,
because that is what every `syncUrl` write and every previously shared result
link looks like. `?ask=1&q=…` is the one combination that loads a question
without running anything.

*Amended 2026-09-16 (Tom): a new URL shape, written without navigating.*
Search is `?q=…&type=…`, with no `type` for all and `?q=` alone for an empty
search where ask is the default. A loaded question is `?ask=…`. `ask=0` and
`ask=1` are gone, with no compatibility. A bare path opens the default mode.
The console writes a snapshot with `history.pushState` when it commits one
(a search, a chip, a mode switch, an ask) and never calls the router. Back and
forward restore the snapshot. A search the console already holds is shown
again without a request, and any other search is fetched again. A deep link
renders on the server: `?q=` arrives with page one in the HTML, and `?ask=`
arrives with the question loaded and unfired. *(Amended 2026-09-19: unfired
still, but the page asks `/api/ask/resume` whether this visitor has a run of
the question going or kept, and shows it if so — §3.6.)* On a deployment with no ask,
`?ask=…` becomes a search for the same words. The writes are
`history.pushState(null, "", url)` and `replaceState`, Next.js's documented
native History API integration, so the router's `useSearchParams` follows
them without a server round trip. The console restores whenever those search
params name a snapshot other than the committed one (DECISIONS.md
2026-09-16).

A real `<form>` and a real `<a href>` on every result, so Enter submits and
middle-click opens — the two things a search page is expected to do. (The
thumbnail is the one exception, and it is a `<button>` for a reason: §6.4.)

~~No inline `<script>` beyond a nonce-free module tag — the page is static files
served from disk, so a CSP could be added later without rewriting it.~~
*Superseded 2026-09-16:* `/demo` and `/paris` are Next pages rendered per
request. Every script carries the request's CSP nonce from `web/src/proxy.ts`
(frontend-migration.md §1b).

### 6.3 Provenance — three sources, three kinds of evidence

`hit.source` flows end to end (`/api/search` hits and `/api/ask` citations
alike) and the page is *specific* about it, because the three are not
interchangeable and a snippet that does not say which one it is quietly claims
to be speech:

| `source` | badge(s) | how the snippet reads |
|---|---|---|
| `transcript` | `spoken` | a verbatim quotation of speech — set in quotation marks |
| `ocr` | `on-screen` | text that was *visible*, not said — monospaced, and in `--seen` |
| `frame` | `frame` | **no quotable text.** The image is the evidence; any text is what happened to be visible in the frame, muted and never quoted |
| `transcript+ocr` | `spoken` + `on-screen` | both channels agreed; the text is whichever was longer, so it is presented as neither — plain, with both badges |

Two rules the styling obeys. **The word carries the meaning**: the face (sans /
mono) and the ground differ alongside the colour, so a badge is still legible on
a monochrome screen and to a colour-blind visitor — and the label is a real
word, not an icon.

*Amended 2026-08-10: the second rule is now the Lime Rule* (DESIGN.md), which is
stronger than "both schemes follow for free" and points the same way. `--seen`
— lime — marks **on-screen-text evidence and nothing else**: the `on-screen`
badge and the OCR line it labels, and that is the whole of it on this page. A
`frame` badge is *not* lime, because a visual match read no text; it is the
neutral plate. Gold stays on the receipt, the timecode and the found frame's
outline. Gold and lime never touch the same object: gold is what you asked for,
lime is what the machine saw. The dashed rule the OCR snippet used to carry is
gone with it — dashed means one thing in this system now (an honest-about-being-
next affordance), and the lime already says *read off the screen* in a way a
border style never did.

A `source` the page has never heard of still gets a badge with its own name.
Dropping provenance silently is the one failure that is worse than an ugly
badge — it is `all` means `all`, applied to a row.

The same treatment is in the answer's Sources list, because that list *is* a
list of search rows (one `hitRow`, two callers). The ask prompt asks for the
same distinction in prose (§3.3).

### 6.4 Click to enlarge

A thumbnail is 160 CSS pixels of somebody's slide: enough to recognise,
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
(Below `--bp-hand` the row goes to three lines rather than dropping anything,
and the snippet is clamped to two — a phone has no one-line row that is still a
sentence.)

*Amended 2026-08-10 (the rebuild): the moment ends in its receipt, printed.*
`youtu.be/<id>?t=<second>` is rendered as the receipt slab rather than implied
by an underlined timestamp, because "the sentence, the slide, and the second it
happened" is the product's argument and a demo that only *links* it is asking to
be taken on trust. Two consequences, both deliberate:

- **The card head prints the talk's id** (mono, muted, beside the channel and
  the moment count) and the moment prints the second. Together they are the
  receipt; separately they are what each row is actually about.
- **The receipt is a third control in the row**, a sibling of the thumbnail
  button and the text anchor, for the same reason those two are siblings: a link
  inside a link is neither valid nor operable. It is the *small* slab — border
  and ink, no filled block — because ten filled gold blocks down a page of
  results would spend the accent on the list instead of on the moment. The
  filled slab is kept for the answer's Sources rows and the enlarged frame,
  where there are three of them and they are the payoff.

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

**A source row's shape does not depend on how long the title is** (*amended
2026-08-11*). The answer's Sources row is a three-column grid — the `[n]`, the
frame at the box `.hit-thumb` already reserves, and everything else — with every
child placed by name, so a row rendered without its marker collapses the first
column instead of shifting the other two. As a wrapping flex row it was the
opposite: a short title sat beside its frame and a long one pushed the whole
text column onto a second line *under* the frame, with the receipt on a third.
On a corpus whose titles run from 20 to 120 characters that is a different
layout per talk. The title is clamped to two lines and ellipsised — it is the
row's label, not its content; the evidence is the snippet under it and the
receipt beside it, and the receipt is the thing that must never be truncated.
The receipt sits under the text it belongs to (and takes the whole row below
`--bp-hand`, where it is wider than a third of a 390px screen). The video card's
header (§6.5) never had the bug — it does not wrap — and is unchanged.

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
  claiming a second kind of work. *Amended 2026-08-10: the spinner is the
  landing's caret* — a gold block that blinks while the machine is inside the
  corpus, which is the Motion Law's own vocabulary for "the agent is asking"
  (and it stops, painted, under `prefers-reduced-motion`). Same rule, one
  mark, and no rotating ring anywhere in the system. *Amended 2026-08-11: the
  idle line steps aside **in the flow**.* Taking it out (`hidden`, i.e.
  `display: none`) shortened the document by one line at the start of every
  tool call and grew it back at the end, so a reader parked near the bottom of
  the page had their scroll position clamped down and anchored back up six or
  more times an ask — the page stuttering under them while the model worked.
  It is `visibility` now: gone from the screen and from the accessibility tree,
  still holding its line. Measured on the stub at 1440, hiding moved the page
  39px per tool call; parking moves it 0.
- **Nothing the page appends may move what is above it.** A scroll position is
  the reader's, and the page never touches it.

  *Amended 2026-08-11 (Tom: "still stuttering"): the log does not grow either.*
  Parking the idle line (above) fixed one line of movement and left the real
  one — the list itself. Every tool call appended a row, the pane grew 46px,
  and the connect band and the footer under it moved by that much, four to
  eight times an ask. On the stub at 1440 that measured as four growth shifts,
  CLS 0.078, *before the answer arrived*. So the live log is a **fixed box of
  six lines**, reserved before it is needed — the same discipline as the
  results skeleton (§6.1), for the same reason. A longer run scrolls inside the
  box, and the box scrolls itself to the newest line, which is the one worth
  reading; the page does not move at all. This is the one auto-scroll on the
  surface and it moves a 169px box, never the document.

  *Amended 2026-09-19 (Tom: "there is a scroll bar after a few lines; it makes no
  sense", "tool calls jump around", "the panel appears instantly and the page
  jumps"): the six rows stay, the box and its scrollbar go.* The live log is a
  plain list of single-line rows, still six rows tall before the first event: the
  last five steps and the one in flight. A new row grows in over `--t-state` and
  the oldest folds to nothing at the same time, so the block never changes
  height and nothing scrolls; a header counts the steps a fold has hidden from
  view. The blinking gold block is retired. The row in flight carries a gold
  mark and a light passing through its text, and between tool calls a last row,
  "Thinking", carries the same — DESIGN.md's Motion law names this motion and
  what it reports. Finished rows go quiet. The idle row folds with the others
  instead of being parked with `visibility`, inside the same fixed block, so the
  2026-08-11 measurement (0px per tool call) still holds. Under
  `prefers-reduced-motion` rows appear and leave at once and the live row is
  simply brighter. And the panel region under the form follows its content's
  height with one `--t-state` transition (`SmoothHeight`), so a click that swaps
  the examples for results, a log or an answer pushes the page instead of
  jolting it.

  Three smaller sources of the same complaint went with it, all measured on the
  stub: the **scrollbar** leaving as an ask emptied the results and returning as
  the answer filled the pane, moving every column 15px sideways and back
  (`scrollbar-gutter: stable`); the **state cell** resizing between `ready` and
  `scanning`, shoving the input's right edge 13px (a `min-width` for the longest
  word it prints); and the **mono face**, which the stylesheet discovered a
  round trip after the page had laid itself out — it is preloaded beside the
  text face now, because above the fold the machine channel is the rail label,
  the chips, the state cell, the query and every example.

  What is left, and stays: the pane opening when an ask starts, and the answer
  arriving. Both are the page answering the visitor. Between them — from the
  first activity event to the last — the pane holds 261px at 1440 and the
  document does not move: 2 layout shifts for a whole ask, down from 8.

  *Amended 2026-09-19 (Tom: "every new tool call or thinking block overriding
  the preceding one", "an expander above the final answer"): the work is one
  line.* The six rows go. The work is a native `<details>`, closed by default,
  whose summary is one line tall: the step in flight — or "Thinking" between
  steps — lit, and replaced by the next step as it starts; the outgoing line
  lifts out while the new one rises in over `--t-state`, in the same cell, so
  the line never changes height. The step count sits on its right. Opened, it
  lists every step so far, the one in flight lit; opening it is the visitor's
  choice, so the page growing then is theirs too. When the answer lands the
  summary reads "Worked for N s" — measured by the page from submit to answer —
  and the disclosure stays where it was, above the answer. Under
  `prefers-reduced-motion` a new step replaces the line at once and the live
  one is simply brighter.
- **The result is a text node with its arrow in it**, not a `::before`. The
  arrow is chrome, but a log a visitor copies out of the page should still read
  as a log, and so should one whose stylesheet never arrived.
- **When the answer lands, the log stops owning the pane.** *Superseded
  2026-09-19: the work is already folded, above the answer (see above).* It
  folded into a native `<details>` — "Show its work" — placed under the answer
  and its Sources.
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

0. **[2026-09-05] The document policy changed hands, and the handover is
   yours to check.** `_DOCUMENT_HEADERS` — the CSP with no `unsafe-` word in
   it, `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy: no-referrer` —
   left `public/` with the two pages that carried it. It is the front end's to
   send now, and one file sends it: `web/src/proxy.ts`, a middleware over every
   document `web/` serves — not the reverse proxy and not
   `next.config.ts:headers()`, the other two candidates this entry named when
   it was written. No test in `mcp/` can see whether it does. This is the one
   thing the removal handed over rather than kept, so it is the one thing to
   confirm before traffic switches: **`GET /` and `GET /demo` each answer with
   all four headers, and with the same `Content-Security-Policy` value** —
   which is what the old single helper guaranteed by construction and two
   independent surfaces do not. `frontend-migration.md` §1b writes out the
   policy that file sends in production, `style-src 'unsafe-inline'` and the
   dropped `data:` included, so the comparison has an exact string to make.
   `frame-ancestors 'none'` matters most — `/api/ask` spends money, and framing
   it is how a stranger spends it with someone else's clicks. Whatever sends
   it needs a test on the surface that sends it; the 2026-08-10 audit's verdict
   rested on the script never building HTML from data *with the policy as the
   second line for the day someone adds a sink*, and a second line nobody
   asserts is a second line nobody has.
1. **The model default.** `deepseek/deepseek-v4-flash-0731` stands in for the DeepSeek
   free tier that no longer exists (§3.1). If you want a specific one, it is
   `OPENROUTER_MODEL=` and a restart.
2. **The daily budget is 50 asks.** That is a number chosen to be visibly
   conservative, not measured against anything. Raise it once the free tier's
   real behaviour is known.
3. **Resolved 2026-08-10: the visual choices are the system's now.** This entry
   used to offer up a warm amber, a 46rem column and a six-property palette for
   you to overrule. You overruled the whole thing: `DESIGN.md` is the visual
   contract, the demo is the landing's continuation, and this page is one column
   at the system's chassis with the query bar and its chips capped at 44rem.
   (That column was `--bp-stack`; it is `--maxw` since 2026-08-11 — §6 item 5.)
   What is left open is not the palette but the *ratio* — how much of
   the hero the argument gets before the input starts. It is currently: kicker,
   one `headline` line, one lede, then the box.
6. **The four example queries** on the cold page are corpus-specific copy in
   `index.html`, re-drawn 2026-08-10 from the verified harvest (§6.1). They stop
   being useful the day the corpus changes; there is no machinery to keep them
   honest, deliberately, because a generated example is a worse example. The
   thing to re-check when the corpus moves is that `context window costs money
   tokens` still returns exactly one OCR hit.
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
