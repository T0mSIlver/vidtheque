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

```
id  weight  text                                        source
7   +0.9    Eval harnesses for coding agents             nightly (4 asks this week)
3   +0.6    Local inference on consumer GPUs             owner
12  -0.8    Model launch hype with no benchmarks         owner
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

### 2.3 Signals

One table, `signals(id, at, kind, video_id, offset_s, text, client)`:

| kind | from | strength |
|---|---|---|
| `mcp_search` | any `search` call (`text` = the query) | strongest: what you are working on |
| `mcp_read` | `video-summary`, `get-segment-context`, `get-transcript` on a video | strong |
| `ask_claude`, `thumb_up`, `thumb_down`, `mute` | the app | strong, explicit |
| `open`, `watch` (with the moment's offset) | the app | medium |
| `dismiss` | the app, a notification swiped away unopened | weak negative |

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

## 3. The verdict

### 3.1 What it is

One per video, written after indexing. Table
`verdicts(video_id, score, reason, summary, moments, profile_rev, model, created_at, notified_at)`.

- **score 0–3**, each tied to an action: 0 skip, 1 the summary is enough,
  2 watch the moments, 3 watch it whole. 1 is the default for an on-topic but
  ordinary video; a 2 means at least one moment is clearly worth this owner's
  time given the profile. The first backfill (100 videos, 2026-10-03) scored
  65 of them 2, which is too many for triage, so the prompt says so and breaks
  ties downward.
- **reason**: one line naming the profile entries it matched or hit
  ("evals ↑, launch hype ↓").
- **summary**: one paragraph.
- **moments**: up to three, each `{cue_id, offset_s, why}`.

**Receipts, always:** every moment is checked against `cues` before the
verdict is stored. A moment whose cue does not exist, or whose offset is not
inside that cue, is dropped, never repaired. A summary with no surviving
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
again, it continues where the last batch stopped, and `--video ID` reruns one.
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

### 3.3 Proving it gets better

Every verdict is kept with the profile revision that scored it, and the
signals say what you did with it. The console's ledger shows one number per
week: of the verdicts scored 2–3, the share you opened, watched or asked
about. If that number does not rise, the profile is not learning, and we will
see it.

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
- The feed's web pages reuse the existing owner session cookie.

## 6. The surfaces

Two surfaces, each answering one question. Nothing appears on both.

- **The feed** (new, phone-first, web and Android): *what should I watch?*
  Three screens and nothing else:
  - **Feed**: verdicts, newest first, scores 2–3 on top, 0–1 collapsed into
    "skipped (n)". A row is the channel, title, score, reason and duration.
  - **Video**: summary, moments (each a `youtu.be/ID?t=` link), thumbs up and
    down, mute, **Ask Claude**.
  - **Profile**: entries, history with revert, **Ask Claude to build my
    profile**, the notification threshold.
- **The console** (the current `/dashboard`): *is the machine healthy, and
  what is in it?* It keeps indexing, jobs, following, the library and the
  ledger, and loses everything the feed now answers. §7.

**Ask Claude** opens `https://claude.ai/new?q=<prompt>`, with a prompt naming
vidtheque, the video id, its title and channel, and leaving the question to
you. Whether the Claude Android app opens that link with the prompt filled in
is unverified, and is the first check of its issue. If it does not, the
button copies the prompt and opens the app.

**The Android app** lives in `android/`, a separate deployable like `web/`:
Kotlin, Jetpack Compose, Material 3, AppAuth for OAuth, Firebase Cloud
Messaging for push. It calls the same JSON endpoints as the web feed; it has
no logic the server does not have. Built by hosted GitHub Actions and
installed from GitHub releases as an APK. Play Store is a later decision.

**Push**: the app registers its FCM token (`devices(id, token, created_at,
last_seen)`). When a verdict meets the threshold (default 2,
`VIDTHEQUE_NOTIFY_MIN_SCORE`), the server sends one notification: channel,
score, and the reason with the best moment ("Theo · 3 · his eval setup,
14:02–22:40"). Below the threshold, nothing is sent. Firebase is free; its
service-account key is `VIDTHEQUE_FCM_CREDENTIALS`, and with it unset no push
is sent.

**Endpoints**: under the existing owner-only `/dashboard/api/*`, behind the
existing credential order (bearer or session) and write guard, so no new
prefix and no new guard: `feed`, `verdicts/{video_id}`, `signals` (POST),
`profile` (GET, POST ops, POST revert), `devices` (POST, DELETE).
`dashboard.md` gets their contract.

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

- **The weekly brief** (what changed across your channels this week) and
  **talking points over time** per channel. Both read the verdicts and
  signals v1 stores; they need nothing v1 must build differently.
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
7. **Measure**: the weekly precision number (§3.3), once a month of verdicts
   exists.
