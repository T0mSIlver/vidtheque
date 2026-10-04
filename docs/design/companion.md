# The companion: verdicts, an interest profile, a feed and an Android app

**Status: DECIDED (Tom, 2026-10-03). Nothing is built.** Written against the
tree at `b0fd6aa`. If implementation must diverge, this document changes in
the same commit and says why. `DECISIONS.md` outranks it.

Sources it must not contradict: `positioning.md` (LOCKED; unchanged by this
document, see §1), `following.md` (where new videos come from),
`dashboard.md` (the console this document slims down), `tool-surface.md` (the
tool it adds), `index-schema.md` (the tables it adds).

---

## 1. The thesis: one agent of ours, consuming the corpus like any other

The MCP server exists so your agent can use the corpus mid-task. The
companion adds one more agent on top of it, ours: a triage agent that reads
each new video from the channels you follow and answers one question for you,
**is this worth your time, and which part?**

Positioning holds as written. The agent still watched it; this time it also
tells you whether you should. "Second brain" stays out of public copy.

The loop, and the only thing v1 builds:

1. A followed channel publishes; vidtheque indexes the video (`following.md`).
2. The triage agent writes a **verdict**: a score, the reason, a short
   summary, and up to three moments with receipts (§3).
3. A verdict scored high enough becomes a **push notification** on the phone.
4. Tapping it opens the video in the **feed**: read the summary, jump to a
   moment on YouTube, or **ask Claude** about it (§6).
5. What you do there, and what your coding agent searches the corpus for,
   become **signals**. Every night they update your **interest profile**
   (§2), which scores the next verdict.

Step 5 is the part that has to get better over time, and the part no other
recommender has: what you are building this week, as seen by the searches
your agent runs.

## 2. The interest profile

### 2.1 Shape

A list of short entries in plain words, each with a weight and the evidence
for it. Readable, editable, revertible. Not an embedding you cannot inspect.

*Amended 2026-10-03 (Tom):* an entry is a short topic, 2–4 words: at most 32
characters and 5 words, refused past that on every add (the profile tool, the
feed's profile ops, the nightly update) with a hint to split a compound topic
into separate entries. A verdict names the entries it matched as one-line
chips, so a long entry made a long chip. Entries written before the cap stay.

```
id  weight  text                                        source
7   +0.9    Coding agent evals                           nightly (4 asks this week)
3   +0.6    Local inference                              owner
12  -0.8    Launch hype                                  owner
```

Tables (one new migration; `index-schema.md` gets the entries):

- `profile_entries(id, owner_id, text, weight, source, created_at, retired_at)`.
  `weight` in [-1, 1]; a negative weight is a "less of this". Retired entries
  stay for the history.
- `profile_events(id, at, actor, op, entry_id, before, after, reason)`.
  `actor` is `owner`, `agent` (an MCP client), `nightly` or `app`; `op` is
  `add`, `drop`, `reweight` or `revert`. The latest event id is the profile's
  revision, which every verdict records.

  `source` is the actor that created the entry; the evidence shown beside it
  ("4 asks this week") is its `add` event's `reason`. "Entries the owner
  wrote" (§2.4) means `source` `owner` or `app`.

*Amended 2026-10-04 (#159, 0020):* an entry has a `kind`, `topic` or
`project`, and a project has an `expires_at`. A project is what the owner is
building now: it lapses 30 days after it was last added unless added again,
and adding a live project's text again restarts its 30 days without writing
an event, since nothing the verdicts read has changed. Every read skips an
entry past its expiry; the nightly update retires it with a `drop` event
(§2.4), which a revert undoes like any other, with a fresh 30 days. A profile
holds at most 10 live projects. Existing entries are topics that never lapse.

**The server-side profile is topics only.** No companies, people, pay or
job-search details, in an entry or in a reason, whoever writes it. Every
prompt that asks Claude to write the profile says so, and the memory routine
(§2.2) filters before anything leaves the box. The 32-character, 5-word cap
keeps an entry too short to carry much else.

### 2.2 Who writes it

- **Any agent, through one MCP tool, `profile`.** Called bare it returns the
  entries and the revision. It takes `add` (texts with weights), `drop` (ids)
  and `reweight` (id, weight). An agent edits what it means to change and never
  rewrites the list. It is the twelfth tool; `tool-surface.md` gets its
  entry. Write scope only.
- **"Ask Claude to build my profile".** A button on the profile screen opens
  Claude with a prompt (§6) that has it write your interests from what it
  knows about you, or interview you, and save them with `profile`. Works in
  ChatGPT too when vidtheque is a connector there. Without a connector, the
  fallback is pasting text into one box, split into entries by line.
- **The nightly update** (§2.4).
- **The owner**, on the profile screen.

*Amended 2026-10-04 (#159):* the profile learns from what the owner already
tells Claude, not from repeated questions. Three paths:

- **Ask Claude saves what it learned.** The video prompt (§6) ends by asking
  Claude to call `profile` when the conversation shows a topic the owner
  cares about or is tired of, with a weight and a one-line reason.
- **The interview, on demand.** The profile screen's button is now "Ask
  Claude to interview me": five questions (what you are building, what you
  want to learn, what you already know, what you are tired of, what kind of
  video is worth your time), then Claude shows the list and saves it, what
  you are building as `kind=project`. Rare by design: nothing ever prompts
  the owner to take it again.
- **Current projects from Claude's memory.** `scripts/memory_projects.py`
  runs where Claude Code keeps its memory (the dev box). It reads each
  `~/.claude/projects/*/memory/` changed in the last 30 days (the
  `MEMORY.md` index and the newest files, at most 8,000 characters a project
  and 60,000 in all), skipping unread any project directory or memory file
  whose name or description hits a deny list (job, interview, salary,
  recruiter and the like, plus the owner's own terms in
  `~/.config/vidtheque/memory-deny.txt`). `claude -p` with no tools, no MCP
  servers and no settings turns that text into at most 8 topics against a
  JSON schema, and is told to name no company or vendor. Each topic must then
  fit the entry caps, use plain characters, and miss the deny list and a
  built-in list of vendor names; the survivors go to `profile` as `kind=project`,
  weight 0.5, under a fixed reason, so the checked topic text is the only
  thing that leaves the memory. A project still in the memory is refreshed
  each run; one that drops out lapses after 30 days. Dry run by default;
  `--send` writes and appends the run to
  `~/.local/state/vidtheque/memory-projects.jsonl`.

  *Proposed, not installed:* a user systemd timer on the dev box runs it
  daily at 03:30, before the 04:00 nightly update, with `--send`, the
  private instance's MCP URL and a token file. The orchestrator installs it
  after reviewing a dry run.

### 2.3 Signals

One table, `signals(id, at, kind, video_id, offset_s, text, client)`:

| kind | from | strength |
|---|---|---|
| `mcp_search` | any `search` call, and a query on the feed's search page (`text` = the query) | strongest: what you are working on |
| `mcp_read` | `video-summary`, `get-segment-context`, `get-transcript` on a video | strong |
| `ask_claude`, `thumb_up`, `thumb_down`, `mute` | the app | strong, explicit |
| `open`, `watch` (with the moment's offset) | the app | medium |
| `dismiss` | the app, a notification swiped away unopened | weak negative |

*Amended 2026-10-03 (Tom: "why only 5 seconds?"):* thumbs up, thumbs down and
mute ("less like this" in the app) are a **state per video**, not only
events: one of `up`, `down`, `muted` or `none`, in `feedback` (index-schema
§1.17). A tap sets it and a second tap takes it back, at any time; the app
shows the stored state. Each set still writes its event to `signals`, the
log, but the nightly update does not read those events: it reads the videos
whose state moved since it last read them, as they stand now, so a tap taken
back before the night never reaches the profile and one taken back after it
reads as "took back".

*Amended 2026-10-04 (#127):* a query run on the feed's search page, in the
app or on the web, is an `mcp_search` too, with `client` `app` or `web`. It is
the same search, typed by the owner rather than their agent, and a query typed
on purpose says what you are working on as plainly as an agent's does. The
kind stays `mcp_search` rather than a new `search`, because the nightly update
reads kinds, not clients, and a new kind would mean rebuilding the `signals`
table for its `CHECK`. The page sends it once per query submitted; paging and
reloads send nothing. The console's search page sends none: it is inspection.

*Amended 2026-10-04 (#149):* a `search` that returns an error is not logged.
An agent that gets one fixes its arguments and searches again, so logging both
would count one query twice. Every session that reaches the instance through
the owner's connector counts as the owner: a coding agent reading the corpus
for a task logs `mcp_search` and `mcp_read` like any other client, under its
OAuth client id. A client that can set headers sends `X-Vidtheque-Signals: off`
when its reads say nothing about what the owner is working on.

*Amended 2026-10-04 (#157):* there is no player in the app; Tom watches in his
YouTube app. So a `watch` is the hand-off, and the app measures how long it
stays away: when it comes back, it sends the time since the hand-off, which
the server stores on that signal as `watched_s`, capped at what is left of the
video from the offset and at the time the server saw go by (dashboard.md
§25.10). It is an upper bound: a phone left in YouTube on another video still
counts, up to the cap.

The MCP signals are logged in the tool layer (`tools/base.py`), for every
client, owner-scoped. A client can opt out per session with a header
(`X-Vidtheque-Signals: off`). The triage agent's own calls are never signals.
Signals older than 180 days are deleted.
`signals` also carries `owner_id`, like every other owned table.

### 2.4 The nightly update

Once a day the update job gives a model the current entries, the day's
signals, and the verdicts those signals touched, and asks for
`add`/`drop`/`reweight` operations with a one-line reason each. They apply
automatically through the same code path as the tool, as `actor=nightly`.

Guards, all server-side: at most 5 operations a night, a weight moves by at
most 0.3 per night, entries the owner wrote are never dropped (only
reweighted), and the profile is capped at 40 live entries. The profile
screen shows the history; **revert** undoes one event or rolls back to a
revision.

As built (0013): the update runs on the job runner's poll tick, from
`VIDTHEQUE_NIGHTLY_HOUR` (default 4, the box's local time), once per local day;
`VIDTHEQUE_NIGHTLY=0` turns it off. "The day's signals" are those since the
last run that finished, at most a week back and the newest 200; the verdicts
are those of the videos they name, at most 50. Each op is its own
`store.apply` call with its own reason, so one refused op costs only itself.
The night's guards: past 5 applied ops the rest are refused, an entry changes
once a night, and a weight is clamped to within 0.3 of where it was; a new
entry counts as moving from 0, so it starts within ±0.3. The store refuses
dropping an owner entry and a 41st entry. `nightly_runs` holds one row per day:
the run claims it before the model call and marks it `done` in the
transaction that applies the ops, so a restart never runs a day twice. A
failed model call applies nothing and is retried up to 3 times that day, an
hour apart. A day with no signals is `idle` and calls no model.

*Amended 2026-10-04 (#159):* before it reads the signals, the run retires
every project past its expiry (§2.1), one `drop` event each as
`actor=nightly`, reason "project not written again in 30 days". Expiry is
not one of the night's 5 operations, calls no model and happens on idle days
too. It is not under the owner guard: an entry that carries an expiry was
written to lapse. The model sees a project marked as one and is told to
leave it be.

## 3. The verdict

### 3.1 What it is

One per video, written after indexing. Table
`verdicts(video_id, score, reason, summary, moments, matches, profile_rev, model, created_at, notified_at)`.

- **score 0–3** (*amended 2026-10-04, #156:* what the feed shows is the
  week's tier, §3.4), each tied to an action: 0 skip, 1 the summary is enough,
  2 watch the moments, 3 watch it whole. 1 is the default for an on-topic but
  ordinary video; a 2 means at least one moment is clearly worth this owner's
  time given the profile. The first backfill (100 videos, 2026-10-03) scored
  65 of them 2, which is too many for triage, so the prompt says so and breaks
  ties downward.
- **reason**: one plain sentence on why this score, no arrows.
- **matches**: the profile entries it hit, at most four, strongest first, each
  `{entry_id, direction, strength}`. `direction` is `up` or `down`, from the
  entry's weight sign; `strength` 2 means the entry is central to the video,
  1 that it comes up. The apps show them as chips, green up, red down.
- **summary**: a digest of at most 60 words: names, numbers, claims and
  techniques, led by what matters given the profile.
- **moments**: up to three, each `{cue_id, offset_s, end_cue_id, end_s, why}`,
  `why` at most 12 words. *Amended 2026-10-04 (#156):* a moment is a span. The
  model names the last cue it covers, `end_cue_id`, and `end_s` is that cue's
  end, read from `cues`, never from the model. The verdict reports the
  moments' minutes against the video's, "6 of 42 min", overlaps counted once:
  a 2 asks for minutes, and the feed has to know how many to fit them in a
  week (§6). A verdict written before spans has no end and no total until it
  is rescored; the backfill's `--rescore` rewrote the 100 newest.

**Receipts, always:** every moment is checked against `cues` before the
verdict is stored. A moment whose cue does not exist, or whose offset is not
inside that cue, is dropped, never repaired. So is one whose end cue is not
this video's or comes before its first cue. A summary with no surviving
moment is still stored; the feed says it has none.

### 3.2 How it is written

A new stage after `store.mark_ready` in `pipeline/runner.py` `_finalize`,
queued as its own job kind (`verdict`), so a slow or failed model never holds
up indexing and a verdict can be rerun alone.

Input, bounded independently of any limit an agent passes: the title,
channel, chapters, speakers and key texts `video-summary` already assembles,
the transcript middle-truncated to a fixed character budget, and the live
profile entries. Output is structured JSON, validated, then the receipt check.

**Novelty**, second iteration of the same stage: before the model call,
compare the video's chunk embeddings with chunks of videos you opened or
asked about in the last 90 days. Strong overlap goes into the prompt as
"already seen in <video>", so a fourth talk making the same point scores
lower. It uses `vec_chunks` as it is; nothing new in the worker.

**Exploration:** about one verdict in ten that would score 0 or 1 is
re-scored without the negative entries. If it reaches 2 it is shown, marked
as outside your profile. This keeps the profile from narrowing into a bubble.

Old videos get verdicts through a backfill command, not automatically.

As built (0010): the job runs at `priority = 200`, behind every indexing job,
so a verdict holds the queue for at most one model call. The transcript budget
is 40,000 characters, whole cues from both ends. `mark_ready` queues a verdict
when the video has none, or when a reindex removed a cue a stored moment cites;
a re-embed that leaves the transcript alone costs no model call. Verdicts are on
when the model is configured (§4), and `VIDTHEQUE_VERDICTS=0` turns them off.
The backfill is `vidtheque-mcp verdicts backfill --limit N`, newest first; run
again, it continues where the last batch stopped, and `--video ID` reruns one
(an id may start with `-`; `--video=ID` is the same). `--rescore` queues the
N newest videos that already have a verdict, to rewrite them under the current
prompt; it does not continue, so a second run queues the same videos again.
The triage agent reads through `video-summary` as `client=triage` with signals
off.

As built (0011), novelty and exploration: "opened or asked about" is a signal of
kind `open`, `watch`, `ask_claude` or `mcp_read` in the last 90 days, newest 200
videos. Up to 24 of the video's chunks, spread evenly, each look up their 3
nearest chunks among those videos; a seen video is named when it holds a chunk
within cosine distance 0.25 of at least a quarter of them, at most three lines,
largest share first. 0.25 is a first guess, not yet calibrated on the live
corpus. Exploration rolls only for a verdict scored 0–1 by a profile that has
negative entries, since without them the rescore is the same prompt. A rescore
of 2 or more replaces the verdict with `explored = 1`; a lower one leaves the
first verdict and costs the one extra call. The roll is injected, so tests pick it.

As built (0014), the digest: Tom's first read of the 0010 verdicts (2026-10-03)
found the summaries long and generic, and the reason, a comma-separated run of
entries with arrows, hard to skim. The prompt now carries the writing rules
(no "the speaker discusses", no hedging, no closing sentence, concrete nouns)
and states the word limits plainly ("never more than 60 words"); the model
is trusted to keep them, since cutting text mid-thought confused more than a
long summary did. The only server-side bound is the schema's character cap
(summary 2,000, reason and `why` 300), there for runaway output; an answer
past it fails as invalid. The profile goes into the prompt with its entry ids;
the model names matches by id and strength only. An id that is not a live
entry, or an entry of weight 0, is dropped, and the stage sets `direction`.
Verdicts written before 0014 have no matches until rescored. Inside the mcp
container, this queues every scored video again:

```bash
vidtheque-mcp verdicts backfill --limit 1000 $(python -c "import sqlite3; c = sqlite3.connect('/data/vidtheque.db'); print(' '.join('--video=' + r[0] for r in c.execute('SELECT v.public_id FROM verdicts d JOIN videos v ON v.id = d.video_id')))")
```

### 3.3 Proving it gets better

Every verdict is kept with the profile revision that scored it, and the
signals say what you did with it. The console's ledger shows one number per
week: of the verdicts scored 2–3, the share you opened, watched or asked
about. If that number does not rise, the profile is not learning, and we will
see it.

*Amended 2026-10-04 (#157, which absorbed #94):* the measure is widened and
starts now, rather than after a month, so the month mark (2026-11-04) has
data. It is four numbers a week, Monday to Monday on the box's clock, on the
app's profile screen and the console's Corpus page (the ledger's successor),
read live from `GET /dashboard/api/valued-time` (dashboard.md §25.12):

- **Hit rate**: of the videos scored 2–3 and published that week, the share
  kept, meaning thumbed up or watched past half the moments. A moment is
  watched when one stretch in the player starts at most 5 s after it and
  passes the middle of its span (a minute past it for a moment written before
  spans, never past the video's end); a video with no moment needs half its
  length, overlapping stretches counted once. Opens alone never count: opening the page or a bounce back from
  YouTube is not a hit.
- **Regret**: of the videos watched that week for a minute or more, the share
  thumbed down after it. Target under 10%.
- **Misses**: videos shared to the app from elsewhere (§6) that the feed did
  not offer: scored 0–1, or from a channel no follow covered when it was
  shared. A share waits, `pending`, until its verdict is written. A skip the
  owner answers "I'd watch this" in the weekly brief (#158, `skip_verdicts`)
  is a miss too; a video both shared and answered counts once.
- **Minutes asked against budget**: lands with the time budget (#156), on the
  same payload.

The first week that counts is 2026-10-12. Each figure says `capped` past 500
videos a week instead of reading more.
### 3.4 Rank within the week

*Added 2026-10-04 (#156).* The first backfill scored 65 of 100 videos 2, about
87 hours of moments: one video at a time, the model cannot say which 2 is
worth more than another. So the verdict keeps its 0–3 score, and its reason
line still explains it; the feed's order and its 3s come from comparing the
week's verdicts with each other.

- **The week** runs Monday 00:00 to Sunday 24:00 in the box's local time,
  keyed by its Monday as YYYY-MM-DD; a video belongs to the week it was
  published in. The weekly brief (#158) uses the same week.
- **The candidates** are the week's verdicts scored 2 or 3, at most 40,
  highest score and newest first.
- **One model call** (`week_rank`) reads the profile and every candidate's
  title, channel, minutes asked, score, reason, summary and moments, and
  answers an order and a `top` of at most 5, the videos worth watching whole.
  The server holds the answer to the candidates: unknown ids are dropped, a
  candidate left out goes last in the fallback order, at most 5 are on top
  whatever the model says, and the top ranks above the rest. A week with one
  candidate needs no call; it keeps its score.
- **When:** at the end of a verdict job, once no other verdict job waits, for
  each week touched by a verdict of the last 8 days whose candidates changed
  since its last ranking or whose last ranking failed, at most 52 weeks a pass,
newest first; weeks left over wait for the next pass.
  A backfill so ranks each week once, at the end. A failed ranking never fails
  the verdict; the week keeps its last ranking and is tried again next time.
- **What the feed shows** (`tier`): 3 for the week's top, 2 for the other
  candidates. A candidate judged since the last ranking follows the ranked
  ones and shows 2. A week never ranked (the model off, or written before
  0017) reads in the fallback order, score then profile matches then newest,
  and its first 5 scored 3 show 3. A 2+ verdict with no publication date has
  no week and shows 2.

Push still follows the verdict's own score (§6); moving it to the tier is
#158's.

## 4. The model behind the triage agent

Today the only LLM caller is the demo's `OpenRouter` class in
`public/ask.py`. It moves to a shared, provider-neutral client, and the
demo's ask uses it unchanged, keeping its own key and budget.

Two backends, one env var, `VIDTHEQUE_LLM_BACKEND`:

- **`api`** (the default): any OpenAI-compatible endpoint, configured by
  `VIDTHEQUE_LLM_BASE_URL`, `VIDTHEQUE_LLM_API_KEY` and
  `VIDTHEQUE_LLM_MODEL`. Covers OpenRouter, Mistral, OpenAI, and llama.cpp on
  the box.
- **`claude-code`** / **`codex`**: runs the unmodified `claude -p` or
  `codex exec` binary that the owner signed in to on the box with their own
  subscription. Anthropic's terms allow this; they forbid apps that offer
  Claude.ai login themselves or hold its tokens
  ([legal and compliance](https://code.claude.com/docs/en/legal-and-compliance),
  checked 2026-10-03). vidtheque never reads the credential.

The client is `mcp/src/vidtheque_mcp/llm.py`, one call for every backend: a
prompt in, text or a JSON object validated against a schema out.
`VIDTHEQUE_LLM_TIMEOUT_S` bounds each call; a subprocess past it is killed
with its children, and its output is capped at 1 MiB.

Tom's instance runs `api`: a verdict reads a whole transcript, and the
weekly Claude limit is the budget that runs out first. All new vars land in
`deploy/.env.example` with the code that reads them.

### 4.1 What the calls cost

*Added 2026-10-03 (Tom: "track every API call").* The client records every
completion in `llm_calls` (index-schema §1.16), whatever its outcome:
purpose (`verdict`, `verdict_explore` for the exploration rescore,
`week_rank` for the week's ranking (§3.4),
`nightly_update`, `unknown` for a caller that names none), the video, backend,
model, the token counts the backend reported, latency, outcome and cost.
`build_model` takes the database, so no caller can build a model whose calls
go unrecorded.

- **Tokens** come from the `usage` object. Mistral's API answers
  `{prompt_tokens, completion_tokens, total_tokens, prompt_tokens_details:
  {cached_tokens}}` for zai-glm-5-3 (checked with a real call, 2026-10-03);
  its thinking is counted in `completion_tokens`, with no separate figure, so
  `reasoning_tokens` stays NULL there. `claude -p` reports Anthropic's usage and
  its own `total_cost_usd`, which is recorded as the cost. `codex exec`
  reports nothing.
- **Cost** is micro-USD, fixed when the row is written: uncached prompt
  tokens at the input price, cached ones at the cached-input price, completion
  tokens at the output price. The prices are `config.LLM_PRICES`, overridden
  by `VIDTHEQUE_LLM_PRICE`. zai-glm-5-3 lists at $1.40 per million input
  tokens, $0.14 cached, $4.40 output
  ([model card](https://docs.mistral.ai/models/zai-glm-5-3), checked
  2026-10-03; mistral.ai/pricing does not list it). Unknown is NULL, never 0:
  a refusal with no `usage`, a model with no price, a cancelled call.
- **It is list price.** Tom's private box runs on a Vibe monthly plan, so the
  number is what the calls would cost pay-as-you-go, not what was billed.
- The public demo's ask (`public/ask.py`, OpenRouter, its own key and daily
  budget) is not recorded: it does not go through `build_model`, and the
  public box has no owner routes to show it.

The console's Health page and the app's profile screen show it, from
`GET /dashboard/api/costs` (dashboard.md §25.8).

## 5. Reach: the private instance on the internet

The OAuth authorization server is already built (`mcp/auth/`,
`VIDTHEQUE_AUTH=oauth`): authorize, token, register, CIMD, owner login and
consent. v1 builds no auth.

- The private instance gets a Cloudflare tunnel hostname, with
  `VIDTHEQUE_AUTH=oauth`, the same way the public box is reached
  (`deploy/cloudflared.example.yml`). No Cloudflare Access in front: Claude's
  connector must reach `/.well-known/*` and `/mcp` unauthenticated to start
  OAuth.
- Claude (web, desktop, phone) adds it as a custom connector, so every Claude
  session can use the corpus without the VPN.
- The Android app signs in through the same server as a public client with
  PKCE, its client metadata served by the instance as a CIMD document. One
  auth system for the agent and the app.

  As built (2026-10-03, #91): the document is `/auth/android/client.json`
  and the redirect `/auth/android/callback`, an https App Link, because CIMD
  only accepts a redirect on the client_id's origin. Android hands that link
  to the app once `/.well-known/assetlinks.json` names its signing key, so all
  three answer only when `VIDTHEQUE_ANDROID_CERT_SHA256` lists the key's
  fingerprints (`auth/android.py`). They sit under `/auth/` because the
  deploy proxies already send that prefix to Python. The server answers the
  app's client_id in-process instead of fetching its own public URL.
- The feed's web pages reuse the existing owner session cookie.

## 6. The surfaces

Two surfaces, each answering one question. Nothing appears on both.

- **The feed** (new, phone-first, web and Android): *what should I watch?*
  Three screens and nothing else (and, since #158, the weekly brief, §6.1):
  - **Feed**: verdicts, newest first, scores 2–3 on top, 0–1 collapsed into
    "skipped (n)". A row is the channel, title, score, reason and duration.
    *Amended 2026-10-04 (#146, #128):* a search on title and channel, a
    channel filter, a profile-entry filter and newest/oldest order narrow it
    (dashboard.md §25.2). Plain text, not semantic search, which is its own
    page (#127).
  - **Video**: summary, moments (each a `youtu.be/ID?t=` link), thumbs up and
    down, mute, **Ask Claude**.
  - **Profile**: entries, history with revert, **Ask Claude to build my
    profile**, the notification threshold.
- **Search** (*amended 2026-10-04, #127*), a side page on both, apart from the
  feed's own title, channel and date search: the same search the MCP `search`
  tool runs, over every channel. A hit is the video, its channel and date, and
  the matching moment with its timestamp; the moment opens `youtu.be/ID?t=`,
  the video opens the video screen, scored or not. dashboard.md §25.9.
- **The console** (the current `/dashboard`): *is the machine healthy, and
  what is in it?* It keeps indexing, jobs, following, the library and the
  ledger, and loses everything the feed now answers. §7.

**Ask Claude** opens `https://claude.ai/new?q=<prompt>`, with a prompt naming
vidtheque, the video id, its title and channel, and leaving the question to
you. *Amended 2026-10-04 (#159):* the prompt also asks Claude to save to
`profile` what the conversation shows the owner cares about or is tired of,
topics only (§2.2). Checked on Tom's phone on 2026-10-03: the Claude Android app opens that
link with the prompt filled in. The Android app copies the prompt only when no
app opens the link.

**The Android app** lives in `android/`, a separate deployable like `web/`:
Kotlin, Jetpack Compose, Material 3, Auth Tab (`androidx.browser`) for
OAuth, Firebase Cloud Messaging for push. *Amended 2026-10-03 (Tom):* Auth Tab
replaces AppAuth, whose last release is 0.11.1 from December 2021; the PKCE
exchange is a few dozen lines of the app's own. It calls the same JSON endpoints as the web feed; it has
no logic the server does not have. Built by hosted GitHub Actions and
installed from GitHub releases as an APK. Play Store is a later decision.

**Share target** (*added 2026-10-04, #157*): the app takes a YouTube video
link shared from any app ("found it elsewhere"). It indexes the video, logs
the share, and tells you whether the feed missed it (§3.3); it opens nothing.
`POST /dashboard/api/shares` (dashboard.md §25.11).

**Push**: the app registers its FCM token (`devices(id, token, created_at,
last_seen)`). When a verdict meets the threshold (default 3,
`VIDTHEQUE_NOTIFY_MIN_SCORE`), the server sends one notification: channel,
score, and the reason with the best moment ("Theo · 3 · his eval setup,
14:02–22:40"). Below the threshold, nothing is sent. Firebase is free; its
service-account key is `VIDTHEQUE_FCM_CREDENTIALS`, and with it unset no push
is sent. *Amended 2026-10-03 (Tom):* the default is 3, not 2. The first
backfill scored 83 of 100 videos 2 or more, so 2 would ping on almost
everything.

As built (#92, `push/`): the verdict stage pushes right after it stores the
verdict, and a failed push never fails the verdict. A verdict pushes once
(`notified_at`), and only when its video was published in the last 3 days, so
the backfill, which judges old videos through the same stage, stays silent.
The message is FCM HTTP v1, data only (video id, title, channel, score,
reason, best moment), and the app draws the notification. A token FCM calls
unregistered is deleted from `devices`.

*Amended 2026-10-04 (#158, Tom):* no daily cap. Every verdict that meets the
threshold is still pushed, and the app groups them with Android's notification
grouping, so a busy upload day collapses into one stack with a summary line
("4 new verdicts"). The Sunday brief is its own notification, on its own
channel, outside the group.

**Endpoints**: under the existing owner-only `/dashboard/api/*`, behind the
existing credential order (bearer or session) and write guard, so no new
prefix and no new guard: `feed`, `feed/facets`, `verdicts/{video_id}`, `signals` (POST),
`feedback` (POST, 0016), `watched` and `shares` (POST, 0018), `valued-time` (GET),
`profile` (GET, POST ops, POST revert), `devices` (POST, DELETE).
`dashboard.md` gets their contract.

### 6.1 The weekly brief

*Added 2026-10-04 (#158), taking the brief out of §8:* one page and one push on
Sunday morning, so nothing asks the owner to check the feed daily. It is a
fourth screen of the feed (`/feed/brief`, the app's brief screen), kept to
about one phone screen: long parts open on a tap.

- **The week's three videos** most worth the time, with their moments. Until
  #156's weekly ranks land, by score, then how strongly wanted entries hit
  them, then newest.
- **What speakers said** about the top three wanted entries: one model call a
  week (`purpose=weekly_brief`) reads the week's videos that hit each entry and
  the cues under their moments, and writes up to three points per entry and a
  disagreement between two videos when there is one. Every point cites one of
  those cues or is dropped, never repaired (§3.1's rule). No model, or a failed
  call, and the brief comes without the section and says why: a late brief is
  worth less than a short one.
- **Channel report card**: per followed channel, over 30 days, the videos, the
  share scored 2+, and the share watched, asked about or thumbed up. A channel
  with at least 4 judged videos and none of either is flagged with a **Pause**
  button. The brief never pauses a channel itself.
- **Profile changes**: the week's nightly events with their reasons, each with
  revert (§2.4's revert).
- **Skip audit**: three random skipped videos of the week, "would you have
  watched it?", yes or no each.
- **Check-in**: "Was last week's feed worth the time? 1–5", plus an optional
  "what was missing" line.
- **The ledger** for that week (§3.3): hit rate, regret and misses, in one line.

**Why skipped, with a fix.** Every skipped row, in the feed and in the audit,
names the "less of this" entry that sank it (its strongest `down` match).
"I'd watch this" (an audit "yes" is the same answer) sets the video's thumb up,
the strong signal the nightly update reads, and proposes easing that entry by
0.3 toward 0; the owner applies the proposal with one tap, or ignores it.

As built (0019): the brief is built on the job runner's tick on Sunday from
`VIDTHEQUE_BRIEF_HOUR` (default 9, local), for the calendar week #156 uses
(Monday to Sunday, keyed by its Monday), and pushed once to every device. Its
three picks, audit picks and "what speakers said" are kept as they were on
Sunday; the channel report, the profile changes and the answers are read when
the page opens. A push that reaches no phone is tried again on the next tick
that Sunday. `VIDTHEQUE_BRIEF=0` turns it off. A box down all Sunday has no
brief that week. Endpoints: dashboard.md §26; tables: index-schema §1.20.

## 7. The console overhaul

The console shows too much and repeats itself: the overview's "recently
indexed" repeats the videos table, and the overview, the ledger and
`corpus-summary` count the same numbers. The rule for the overhaul: **every
page answers one question a human has about the machine, and a number appears
on exactly one page.** "What is new" moves to the feed.

The cuts are decided page by page from screenshots, not from this document:
an audit lists what each page shows, what it repeats, and what nobody uses,
and Tom picks. Then one PR per page.

## 8. Not in v1

Kept out on purpose, so the loop ships polished:

- **Talking points over time** per channel. It reads the verdicts and
  signals v1 stores; it needs nothing v1 must build differently. *(The weekly
  brief, deferred here with it, is built: §6.1.)*
- Videos from channels you do not follow.
- An in-app chat. Questions go to Claude, which has the corpus.
- A memory system of our own beyond the profile. Claude's memory reaches
  vidtheque through the profile tool, never the other way.
- Multi-user. `owner_id` stays `1` (DECISIONS.md #2).
- iOS.

## 9. Order of work

Each line is a GitHub issue in the "Companion v1" milestone, linked from
`docs/ROADMAP.md`. Steps in one group can run in parallel.

1. **Reach**: the private instance on a tunnel with OAuth; Claude connector
   checked from the phone. *(§5)*
2. **Foundations**: the shared model client (§4); the profile store and the
   `profile` tool (§2.1–2.2); the signals log (§2.3).
3. **Verdicts**: the verdict stage and backfill (§3.1–3.2); then novelty and
   exploration; then the nightly update (§2.4).
4. **Feed on the web**: the endpoints and the three screens, with Ask Claude
   (§6).
5. **Android**: the app, then push (§6).
6. **Console**: the audit, then the cuts (§7).
7. **Measure**: the weekly ledger (§3.3), counting from 2026-10-12 (#157).
