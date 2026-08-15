# Following channels — the contract

**Status: DECIDED (Tom, 2026-08-15).** This un-defers the "Channel/playlist
subscriptions" row of `tool-surface.md` §6 and DECISIONS.md's v1 tool cut
(*"Subscriptions deferred … revisit post-v1"*). Implementation follows this
document; if implementation needs to diverge, this document changes in the same
commit and says why.

Written against the shipped spine — migration `0006_follows.sql`,
`mcp/src/vidtheque_mcp/follows/`, and the queue wiring in
`pipeline/runner.py`, `jobs/runner.py` and `app.py`. Every number quoted below
is a constant in one of those files, an env default in `deploy/.env.example`,
or a proposal that says so.

Sources it must not contradict: `positioning.md` (LOCKED — "follow" is a
blessed word and the roadmap line *follow channels — vidtheque keeps watching so
you don't have to* is what this makes true by construction), `index-schema.md`
§1.8/§1.9 (the storage), `tool-surface.md` (the tenth tool), `dashboard.md`
(the Following surface and the write-side predicate), `DECISIONS.md` (which
outranks all of them).

---

## 1. The thesis — a follow is not a setting, it is a stage with provenance

The obvious shape of this feature is a saved filter: a URL, a couple of
conditions, a cron entry, new videos appear. That shape is wrong here for one
reason, and the whole design falls out of it.

Every other subsystem in vidtheque explains itself. `video_stages` says which
model transcribed what and when. `job_events` says why a job waited ninety
minutes. `data_status` is `corpus-summary` diagnosing itself rather than
returning an empty answer and letting the model guess. A follow that quietly
dropped a four-minute video because its floor is eight would be the one place
this index goes silent — and silence is the defect (PRODUCT.md, "willing to
admit what is missing").

So a follow keeps a ledger. `follow_seen` holds one row per candidate the follow
has ever looked at, carrying the decision **and the number that made it**:
"shorter than your floor" is an opinion, `4:12, shorter than your 8:00 floor`
is a receipt. That is the same standard the rest of the product is held to — the
sentence, the slide, and the second — applied to the videos that never became
corpus at all.

Two consequences that are not negotiable:

- **A candidate is never dropped without a row.** Every branch of the check
  writes one, including the ones that queue and the ones that hold.
- **`reason` is a sentence a human reads, not an enum re-spelled.** The
  vocabulary in §6 is what a query groups by; the reason string is what the
  "what it passed over" band prints.

## 2. Why the storage was already there

`collections` has shipped `kind IN ('manual','channel','playlist')`,
`source_url`, `sync_cron` and `last_sync_at` since migration 0001, and
index-schema §1.8 said why in as many words: the storage is here now because
adding it later to a populated database is a migration for no reason. That call
was right, and it is why 0006 is small.

**A follow is therefore three things:**

1. a `collections` row (`kind='channel'` or `'playlist'`) — the identity, the
   slug, the source URL, and `last_sync_at` as the last-check stamp;
2. a `follows` row keyed by `collection_id` — the rules and the clock;
3. a `follow_seen` ledger — one row per candidate ever considered.

`collection_videos` already answers "what did this follow bring in", and it
answers it with the same rows the rest of the corpus is made of. Nothing about
membership is new.

The `follows` row is keyed by `collection_id` rather than carrying an id of its
own: the follow *is* the collection, and two names for one number is the drift
this schema spends its `CHECK` constraints preventing. `owner_id` is not
repeated for the same reason — `collections` carries it and this row cannot
outlive its parent. A `manual` collection simply has no `follows` row.

**`collections.sync_cron` stays NULL and unused.** The interval lives in
`follows.check_interval_s`. A cron expression is a config language, and the
dashboard is deliberately not a config editor (dashboard.md §1, non-goal 4):
"every 6 hours" typed as `0 */6 * * *` is a worse control and a parser nobody
asked for. The column is left in place because a literal cron may still earn
its place later — reserved, not forgotten. index-schema §1.8 now records that.

## 3. Why a check is a job

A follow check could have been a timer beside the queue. It is a `jobs` row
instead, `kind='follow_check'`, and the argument is operational rather than
architectural.

**YouTube pushes back, and that is an ordinary operating condition on this
box.** Bot checks and rate limits are measured behaviour, not an exception path
(`research/ytdlp-usage-audit-2026-08-10.md` §1). The queue already owns every
piece of the answer:

- the failure classification and the typed `E_RATE_LIMIT`;
- the 90-minute cool-off — `DEFAULT_RATE_LIMIT_BACKOFF_S = 5400` in
  `jobs/runner.py`, an estimate from the 60–90 minute waves that were observed,
  and its comment says so;
- the attempt ceiling (`RATE_LIMIT_ATTEMPT_CEILING = 6`), so a block the box
  paid for is not charged to the candidate;
- crash recovery, the heartbeat, the staleness sweep;
- and the war-story page that makes all of it visible (dashboard.md §5.4).

A timer would have had to reinvent every one of those, and would have been
invisible on the jobs page while it did. So a check is a job, it defers like
every other job, and it enqueues an **ordinary `index` job** for whatever
survived — one that happens to name its follow rather than a second indexing
path.

**Priority 10.** `jobs.priority` is lower-runs-first; `high` is 50 and `normal`
is 100. A check is a listing request that finishes in about a second; an index
item is minutes of GPU. At normal priority a check enqueued behind an overnight
batch would run hours after it was due and the follow would report a stale clock
through no fault of its own. Ten is far enough ahead of `high` that a check
never waits on indexing, and it costs the queue nothing, because a check has
nothing to wait *for*.

**The clock rides the loop's own tick.** `PipelineRunner` gained a pre-claim
hook and `follows.scheduler.enqueue_due` is its only caller — the queue keeps
meaning "claim jobs, drive items" and does not import a feature. Three rules
live there:

- At most `MAX_ENQUEUED_PER_TICK = 3` follows are made due per tick. This is not
  a cap on how many follows may exist; it bounds the burst after a long
  downtime, when every follow is overdue at once and each check is a request
  against a source that blocks boxes for asking too fast.
- A follow with a check already `queued` or `running` is skipped, so a check
  sitting behind a 90-minute backoff is not joined by a fresh one every tick.
  Its clock is pushed out by one interval anyway.
- **The clock moves when the check is queued, not when it finishes.** A check
  that fails still owes its next one an interval — otherwise a channel that
  404s is re-checked on every tick forever.

Nothing is enqueued when `VIDTHEQUE_FOLLOW_CHECKS=0`, and nothing is enqueued
when the pipeline is off (`VIDTHEQUE_RUN_PIPELINE=0`): a check queued in a
process that will never claim it is worse than no check, because the dashboard
would show it waiting forever.

## 4. The rules, and the order they run in

A check extracts **one flat listing per watched tab** (`MAX_LISTING = 50`
entries, one request whatever its length) and then decides. Nothing here talks
to the worker and nothing here downloads.

The evaluation order is the constraint the whole feature hangs on:

```
tab → title → already indexed → duration (listing) → duration (probe) → budget
```

**Cheapest first, and the only rule that can cost a request is last.**

| # | Rule | Cost | Decision if it rejects |
|---|---|---|---|
| 1 | `tab` — is this upload from a tab this follow watches? | free (the listing was tagged) | `skipped_tab` |
| 2 | `title_exclude`, then `title_include` | free (string compare) | `skipped_title` |
| 2b | horizon — is it newer than the follow, or inside the backfill? | free | `skipped_horizon` |
| 3 | already in the corpus? | one indexed DB read | `already_indexed` |
| 4 | duration, **from the listing** | free when the listing carried it | `skipped_duration` |
| 5 | duration, **from a probe** | one request, no download | `skipped_duration` |
| 6 | per-check limit, then the shared daily budget | free | `held_budget` |

**Exclude wins over include, and it wins by being asked first.** A title
matching both is a title the operator named twice, and the negative is the one
they meant.

**Title terms are plain substrings, matched case-insensitively — not regex.** A
regex in a stored config field is unbounded compute from a stored string, and
every rule here has to be cheap enough to run against a listing. Bounded at 10
terms of at most 80 characters each (`follows/params.py`).

**The two evaluation points for duration are the interesting part, and the
surface prints which one was used.** The flat listing carries `duration` for
most entries and not all; the rest cost one probe, which is still before any
download. `follow_seen.judged_from` is `listing` or `probe`, so a check that
spent requests says so. A single check may spend at most
`MAX_PROBES_PER_CHECK = 5` probes; anything left unjudged past that is **not
recorded at all**, so the next check reconsiders it — and the count is logged as
a `warn` on the job, because a check that quietly looked at less than it listed
would be exactly the silent narrowing this codebase refuses everywhere else
(`all` means all, tool-surface §1.2).

**An unknown duration is not zero and is not infinity.** A follow with a length
rule that cannot measure a candidate holds it for review rather than guessing:
guessing "too short" loses a talk, guessing "fine" spends the GPU. A follow with
no length rule has no opinion about length and never asks.

**The horizon.** `backfill` is how many uploads to reach back for at the moment
of following. The default is `0` — the follow starts from the day you made it —
because the alternative default is a follow that can queue two hundred videos of
GPU time the first time it runs. It is hard-capped at 25 in the tool *and* in
the schema; a bigger sweep is a deliberate `index-video expand=channel_recent`,
not something a follow does while you are asleep. There are two ways past the
horizon, because a listing does not always date its entries: an upload published
at or after the follow's `created_at` is new by the clock, and one of the newest
`backfill` entries is in by position.

**The rules, in the first cut**, all validated once in `follows/params.py`
because there are two callers (the tool and the dashboard form) and the
dashboard is built *on* the tool rather than beside it:

| Rule | Column | Default | Bound |
|---|---|---|---|
| tabs | `tabs` | `videos` | subset of `videos,streams,shorts` |
| min/max length | `min_duration_s`, `max_duration_s` | NULL (no floor/ceiling) | parsed by the offset axis (`480`, `8:00`, `1:30:00`) |
| title include/exclude | `title_include`, `title_exclude` | NULL | ≤ 10 terms, ≤ 80 chars each |
| stage set | `channels` | `all` | `all` or a subset of `transcript,ocr,frames` — **verbatim from `index-video`** |
| tags to apply | `tags` | NULL | ≤ 10, §3.7 validation |
| backfill horizon | `backfill` | `0` | 0..25 |
| per-check ceiling | `max_per_check` | `5` | 1..25 |
| arrival mode | `mode` | `auto` | `auto` \| `review` |
| interval | `check_interval_s` | `VIDTHEQUE_FOLLOW_INTERVAL_S` (21600) | ≥ 900, clamped at 7 days |

`channels` is `index-video`'s own vocabulary and not a new one. A podcast follow
that only wants transcripts is where that parameter finally pays for itself.

The 900-second floor is enforced three times, deliberately: as a typed error in
`params.py`, as a `CHECK` constraint in the schema, and at boot in
`PipelineSettings.validate` — a typo in the env would otherwise surface as a
constraint failure hours later, in a write nobody is watching.

## 5. The budget

**Arrival is automatic, with a ceiling.** Matching uploads index themselves
until the day's budget is spent; the rest are `held_budget` and reconsidered on
the next check. Nothing is ever dropped for being over budget — that is the
whole difference between a budget and a filter. A per-follow `mode=review`
overrides to hold everything for a human.

`VIDTHEQUE_FOLLOW_DAILY_HOURS` is **hours of video accepted per rolling 24
hours, across every follow together.** Two choices inside that sentence:

**Hours of video, not GPU-minutes.** A check knows a candidate's duration before
it knows what indexing it will cost — the cost depends on which stages are on,
what the STT policy resolves to, and whether the co-tenant has the GPU. Hours of
video is the number the check actually has, and it is the number an operator
reasons about ("I'll take eight hours of talks a day"). It converts to GPU time
by a factor the deployment already publishes (roughly 1–3 minutes per hour of
video, tool-surface §4.7) without the budget having to model it.

**Global, not per-follow.** Five follows with a per-follow budget would spend
five budgets, and the thing being protected — a box whose GPU is leased from a
co-tenant — is singular. The sum is one indexed query over `follow_seen` rows
with `decision='queued'` inside the window (`follow_seen_budget`, a partial
index, because that is the only decision the sum counts).

**The budget is spent in publication order.** Due follows are checked
**oldest-clock-first** (`follows_due`, ordered by `next_check_at`), and within a
check candidates are judged **oldest-upload-first**. So the day's hours go to
whoever has been waiting longest, rather than to whoever sorts first by id.

Two smaller ceilings sit in front of it and are not the same thing:
`max_per_check` bounds one check's acceptance (its overflow is also
`held_budget`, with a reason naming the per-check limit), and
`MAX_ENQUEUED_PER_TICK` bounds how many checks start at once.

An accepted candidate whose duration is unknown contributes nothing to the sum
and is therefore free. That is safe only because a follow with a length rule
holds an unknown duration for review, and a follow without one has no opinion
about long videos anyway.

`VIDTHEQUE_FOLLOW_DAILY_HOURS=0` disables the ceiling. That is the setting to
pick only if nothing else on the box wants the GPU.

**Resuming does not owe back-pay.** A follow paused for a week re-arms its clock
to *now*, not to when it would have been due. The catch-up burst that would
otherwise produce is exactly what the budget exists to prevent, so it must not
be created in the first place.

**Unfollowing frees the spend, and that is a known hole rather than a design.**
The sum is over `follow_seen`, and `collections` cascades to it, so a follow
that accepted six hours today and is deleted this evening takes those six hours
out of the rolling window with it — every other follow's next check sees them as
free, and the day can run to roughly twice the ceiling. It is recorded here
rather than fixed because the fix is a choice about where a spend lives, not a
patch: either the queued rows survive their follow (re-parented or nullified
instead of cascaded), or the spend is written at accept time to something
append-only that is not the ledger. Both add a table or a nullable owner to a
schema that currently reads cleanly, and neither is worth guessing at. §11.7.

## 6. The decision vocabulary

`follow_seen.decision` is one of nine words, enforced by `CHECK`:

| Decision | Means | Provisional? |
|---|---|---|
| `queued` | accepted; an `index` job holds it | terminal |
| `held_budget` | the day's hours (or this check's `max_per_check`) were spent | **yes** — re-decided on the next check |
| `held_review` | `mode=review`, or a length rule with no measurable length | yes — waits for a human |
| `skipped_tab` | from a tab this follow does not watch | terminal |
| `skipped_title` | an exclude term matched, or no include term did | terminal |
| `skipped_duration` | shorter than the floor or longer than the ceiling | terminal |
| `skipped_horizon` | published before the follow, and the backfill is spent | terminal |
| `already_indexed` | the corpus already has it; carries the `video_id` | terminal |
| `failed` | reserved for a candidate whose own handling failed | terminal |

**`UNIQUE (collection_id, source_id)`** is what stops a check reconsidering the
same upload every six hours for a year. A row is updated in place when a
decision changes, and that happens in exactly one direction that matters:
`held_budget` becomes `queued` when the window frees. `first_seen_at` survives a
re-decision and `decided_at` does not, which is what makes "how long did the
budget hold this up" answerable at all.

### 6.1 Why this is not the fifth state vocabulary

PRODUCT.md is explicit: four state vocabularies exist, they are deliberately not
unified, and **no surface may invent a fifth**. This one is not that, and the
argument is one sentence: *the four existing vocabularies all describe work that
was accepted; this one describes the decision to accept it.*

- `videos.index_state` — where an accepted video is in its pipeline.
- `jobs.state` — where an accepted unit of work is in the queue.
- `job_items.state` — where one accepted video inside that job is.
- `video_stages.state` — where one accepted stage of one accepted video is.

All four presuppose a `videos` row. A candidate that was passed over never
becomes one, so there is no id for `collection_videos` to hold, no
`index_state` to read, and no way to ask what a rule cost you. **Nothing else in
this schema records what happened to a video that was never indexed**, which is
precisely what these rows are.

The test the rule is really protecting against is a surface re-spelling an
existing state — a jobs page inventing "stalled", a video page inventing
"partial". `decision` re-spells nothing: no value here is a synonym for a value
there, and the two never appear in the same column. Where they meet, they are
joined rather than merged — a `queued` row carries `job_id`, and once the video
exists it carries `video_id` too, so the ledger hands off to the four
vocabularies rather than shadowing them.

`follow_seen.video_id` cannot be filled at decision time for anything new, since
a candidate is queued before the video row exists. `store.link_landed` closes
the loop afterwards on the one key both sides share — the source id — and is
idempotent, so the check simply runs it every time.

## 7. The surfaces

**Reading is `corpus-summary include_follows=true`, not a second tool.** The
deferred sketch in tool-surface §6 already said `list-subscriptions` folds into
the summary rather than being its own tool, and that half of the sketch survives
intact. It is the one `include_*` that defaults **off**, so no existing payload
grew when it arrived. See tool-surface §4.3.

**Writing is one tool, `follow-channel`, dispatching on `action`** (follow |
unfollow | pause | resume | check_now), with `url` as the single handle for all
five. Specified in tool-surface §4.10. **Nothing in the tool reaches the
network**: creating a follow is a database row and the display name is read off
the URL, because a probe would make `action="follow"` a call that can get the box
rate-limited before the follow exists — and the first check is the request that
asks the source anyway.

**The dashboard's Following surface** — the rail item, the list page with its
add form, and the detail page with its three bands (the rule as a sentence with
`Edit` behind it, the check ledger, and *what it passed over* with `Index
anyway` on every row) — is specified in dashboard.md §18. It is built *on* the
tool (dashboard.md §2.2) and adds no policy: five of its six writes call
`follow-channel` itself, the sixth calls `follows/params.build_rules`, and that
module is the one validator both callers share.

The tool budget cost is recorded honestly in §10.4 below.

## 8. Configuration

Three env vars, all three in `deploy/.env.example`, which is the document of
record (AGENTS.md).

| Var | Default | What it does |
|---|---|---|
| `VIDTHEQUE_FOLLOW_CHECKS` | `1` | Master switch. Off → no `follow_check` is ever enqueued; existing follows keep their rules and their ledger and nothing checks them. Implicitly off when `VIDTHEQUE_RUN_PIPELINE=0`. Both cases are printed as a `note:` on every `follow-channel` payload — *the follow is stored and idle* — rather than refused. |
| `VIDTHEQUE_FOLLOW_DAILY_HOURS` | `16` (Tom, 2026-08-15) | Hours of video accepted per rolling 24h across every follow together. `0` disables the ceiling. Refused below 0 at boot. |
| `VIDTHEQUE_FOLLOW_INTERVAL_S` | `21600` (6h) | Default seconds between checks of one follow, when the follow does not set its own. Refused below 900 at boot. |

No other knob is added, and none of the three is editable from the dashboard
(§1 non-goal 4 again).

## 9. Non-goals

Stated so nobody has to guess, and so the next amendment has to argue with them.

1. **Not a cron editor.** The control is an interval in seconds with a floor,
   rendered as a sentence ("every 6 hours"). `sync_cron` stays NULL. A cron
   expression is a config language; this is a dashboard.
2. **No Google OAuth subscription import.** Reading a user's YouTube
   subscriptions means a Google OAuth client, a scope review, and a second
   identity provider inside a server that is already its own authorization
   server (DECISIONS.md #1). The value — a list of channel URLs — is not worth
   any of that. A file-based import is a separate question (§11.2).
3. **No thumbnails for candidates.** An un-indexed video has no keyframe on
   disk, and a YouTube thumbnail URL would be a runtime request to something off
   this box, which DESIGN.md bans outright. A candidate row is text: title,
   clock, tab, date, and the reason.
4. **No notifications.** No email, no webhook, no push. Held candidates wait on
   a page. Whether that is enough is §11.3.
5. **The public demo never gets a write.** `follow-channel` carries
   `readOnlyHint: false`, so it is masked from `tools/list` by the existing
   derivation in `public/readonly.py` — there is no second list to keep in sync
   (tool-surface §3.8). The dashboard's `Following` item lives under the same
   write-side predicate as `Add videos`: absent, not disabled, in
   `VIDTHEQUE_PUBLIC_READONLY=1` and `VIDTHEQUE_AUTH=none` — and so do its two
   **read** pages, because a page whose every affordance POSTs has nothing to
   show a deployment with no write side. `corpus-summary include_follows` is
   withheld on the same grounds, with a `note:` saying so rather than an empty
   section that would read as *nothing is followed*.
6. **Not a second indexing path.** A check enqueues an ordinary `index` job with
   ordinary args. Everything about retention, stages, tags, dedup and the
   in-flight guard is unchanged and is not re-specified here.
7. **Not multi-source.** `kind` is `channel` or `playlist`, both YouTube, both
   through the existing `Source` protocol. Nothing about the listing shape is
   generalised in advance.

## 10. Decision record

Decisions taken by Tom on 2026-08-15, recorded rather than re-opened. The
matching entry is in DECISIONS.md.

1. **Subscriptions are no longer deferred.** The v1 cut deferred them on
   tool-budget grounds with `index-video expand=channel_recent` as the
   on-demand answer. `positioning.md` (LOCKED) makes "follow the builders" the
   first pillar and names *follow channels* as the roadmap line that makes the
   position true by construction; on-demand expansion does not keep watching.

2. **Arrival is automatic, bounded by a daily budget.** The rejected
   alternative was review-by-default — every arrival waits for a human. That
   makes the product a queue of chores and contradicts the pillar it serves
   ("your agent watched it"). Review survives as a per-follow `mode`, which is
   the right shape: it is a property of the channel you are unsure about, not of
   the feature.

3. **The budget is global and counted in hours of video.** §5.

4. **The agent gets a write tool, and it is one tool.** `follow-channel`
   dispatches on `action`; reading stays on `corpus-summary`. **The cost is
   real and is recorded:** tool-surface §2 opens by arguing the tool budget is
   the point, and §6 deferred subscriptions partly *because* they looked like
   three tools pushing the surface to 12. This is one tool taking it to 10 —
   cheaper than the sketch, not free. Tom's stated intent is that *"we might
   merge all the dashboard management tools later on into one single tool"*, so
   the design constraint follows from it: **the tool dispatches on `action`, and
   no parameter is named in a way that would not survive that merge.** No
   `follow_url`, no `follow_id` — `url`, `follow`, `tags`, `channels`,
   `max_items`-shaped names that already mean the same thing elsewhere on the
   surface.

5. **The first cut of the rules is min/max duration, tab, title
   include/exclude, per-follow stage set, and a backfill horizon capped at 25.**
   Everything else an operator might want (view counts, chapter presence,
   language, "only if it has captions") is a rule that cannot be answered from a
   flat listing, and §4's ordering is the reason that matters.

6. **A check is a job at priority 10.** §3.

## 11. Open questions for Tom

Real forks. Everything above is a decision.

1. ~~**`VIDTHEQUE_FOLLOW_DAILY_HOURS=8` is a proposal, not a measurement.**~~
   **Answered (Tom, 2026-08-15): 16.** The proposal was 8, on the reasoning that
   eight hours of video is roughly 8–24 GPU-minutes at the published 1–3
   min/hour figure — small — but also eight hours of *download*, audio
   extraction and keyframe decode, on a box whose GPU is leased from llama.cpp
   and whose yt-dlp requests are the thing that gets it blocked. Tom doubled it.
   It remains the one number in this document that is a judgement about a
   particular machine rather than a measurement, so it is the first knob to
   revisit if following ever makes the box feel busy — and the honest way to
   settle it is a week of real arrivals against `follow_seen`, not more
   arithmetic.

2. **Does a Google Takeout `subscriptions.csv` import earn its place later?**
   Takeout is a file the operator already has, so it needs no OAuth client, no
   scope review and no Google identity anywhere near this server — it is a
   paste-a-file form that becomes N follows sharing one rule set. The cost is a
   bulk-create path with its own confirmation step (fifty follows created by one
   click, each with a clock), a CSV parser for a format Google may change, and a
   preview screen that has to render fifty channel titles resolved by fifty
   listing requests — or not resolve them, and create fifty follows from URLs
   nobody has seen. Worth a slot after the first cut has run for a while, or is
   "paste one channel at a time" simply the right ergonomic for a corpus you
   curate?

3. **Should the `held_review` queue eventually notify?** Today held candidates
   wait on a page, which means they wait until you look. A follow in
   `mode=review` that nobody visits for a month is a feature that silently does
   nothing — the failure this document opens by refusing, one level up. The
   options, cheapest first: a count in the rail's `Following` item (no new
   dependency, still requires opening the dashboard); a line in the
   `corpus-summary` payload so *the agent* mentions it mid-conversation, which
   is the vidtheque-shaped answer and costs a section in a capped rollup; a
   webhook env var (one `POST`, no template, no retry story); email (an SMTP
   config, a deliverability problem, and a second thing to secure). My
   inclination is the first two and never the last two. Confirm.

4. **Should `failing` clear itself?** A follow whose channel 404s is set
   `failing` by the check and stays there until a human resumes it — deliberate,
   because no amount of waiting fixes a renamed channel. But a channel that was
   briefly private, or a one-off extractor break, produces the same state and
   would recover on its own. The alternative is `failing` with a slow retry
   (say, one check a day) that clears on the first success. That is friendlier
   and it is also how a follow quietly keeps making requests against something
   that will never work again.

5. **Is a `note:` enough when `VIDTHEQUE_FOLLOW_CHECKS=0`?** As shipped,
   `follow-channel` creates the follow and prints *the follow is stored and
   idle* (tool-surface §4.10) — the `all` means all rule, and the same honesty
   `job-status` owes a deferred job. The rejected alternative was refusing with
   `E_FEATURE_DISABLED`, on the grounds that a follow is a standing promise and
   a note is read once. The note is right for the box being set up before the
   pipeline is turned on; it is thinner for the box where the switch was turned
   off *and forgotten*, where the row will sit idle indefinitely and nothing
   will say so again. If the second case is real on your box, the answer is not
   a refusal but a line on the dashboard's list page (dashboard.md §18.3) — say
   which.

6. **Is `max_per_check=5` doing a job the daily budget does not?** Both bound
   acceptance and both produce `held_budget`. `max_per_check` is per-follow and
   counts videos; the budget is global and counts hours. The case for keeping
   both: a single follow cannot monopolise one day's hours with one check. The
   case for dropping it: two ceilings with one decision word between them is a
   thing to explain twice on the detail page. Kept for the first cut; say if the
   detail page reads worse for it.

7. **Where should a spend live, so unfollowing does not refund it?** §5's known
   hole: the budget sums `follow_seen`, `collections` cascades to it, and an
   unfollow therefore returns hours already spent on download and GPU to the
   rolling window. The two honest fixes both cost something. *Keep the rows*:
   `follow_seen.collection_id` becomes nullable with `ON DELETE SET NULL`, and
   every read in `follows/store.py` grows a "belongs to a live follow" clause —
   cheap to write, and it leaves orphan ledger rows nothing on any surface can
   explain. *Record the spend separately*: an append-only row per accepted
   candidate, pruned on the same 30-day clock as `job_events` — one more table,
   but the budget stops being a property of a table that exists for another
   reason. There is also the third answer, which is that a corpus with a handful
   of follows will never notice, and a documented hole is cheaper than either.
   It is a real fork and it is yours.
