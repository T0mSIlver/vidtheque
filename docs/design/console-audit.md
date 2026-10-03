# Console audit (2026-10-03)

The input to `companion.md` §7: what each `/dashboard` page shows, what it
repeats, and a proposed verdict per block.

**Decided: Tom took every proposal as written (2026-10-03).** One PR per page
follows.

The rule applied: **every page answers one question a human has about the
machine, and a number appears on exactly one page.** "What is new" goes to the
feed (§6).

Taken from a scratch stack on a copy of the private corpus of 2026-10-03 (669
videos), at 1440 px and 390 px. Three things in the screenshots come from the
scratch stack and are not findings: every thumbnail and frame is broken
(keyframes were not copied), the worker reads `unavailable` (none was running,
so the overview has no served-models table and search skipped its vector legs),
and Following says checks are off (`VIDTHEQUE_FOLLOW_CHECKS=0` there). The
round "N" badge is Next's dev indicator.

Verdicts: **keep**, **move** (to another page or the feed), **merge** (into a
block on another page), **cut**.

## The proposal in one table

| Page today | Question it should answer | Change |
|---|---|---|
| Overview | Is the machine working right now? | Becomes **Health**: readiness, models, queue now, what is missing. Corpus band, recent, channels, tags and storage leave. |
| Ledger | What is in it? | Becomes **Corpus**: the band, videos by state, channels, tags, storage. Jobs by state and readiness leave. |
| Search | Can the machine find X, and through which leg? | Keep. |
| Videos | Which videos, in what state? | Keep; phone layout needs work. |
| Video detail | What did the machine store for this video? | Keep; drop the job-history duplicate. |
| Jobs | What is it doing, and what failed? | Keep; gains the jobs-by-state counts; hides `follow_check` by default. |
| Job detail | What did this run cost, and where did it stop? | Keep; stage table only for the item that failed. |
| Add videos | (a form) | Keep. |
| Following | What is it watching, and at what cost? | Keep; drop the head count. |
| Follow detail | What does this follow do, and what did it skip? | Keep; collapse "what it passed over". |

## Repeated numbers

Each row is one number, and every place it shows today.

| Number | Shown on | Proposed single home |
|---|---|---|
| videos, runtime, cues, keyframes, on-screen lines | Overview band, Ledger band, `corpus-summary` | Corpus |
| failed videos (1) | Overview band ("1 not ready"), Overview "what is missing", Ledger "videos by state" | Corpus (videos by state) |
| jobs failed in 24 h | Overview queue, Ledger jobs | Health |
| queued + running | Overview queue, Ledger jobs by state | Health (one line), Jobs (by state) |
| transcript without OCR | Overview, Ledger | Health |
| keyframe JPEGs, index file | Overview storage, Ledger | Corpus |
| readiness strip (5 states) | Overview, Ledger | Health |
| last indexed time | Overview head, Ledger head, Video detail | Health head; per-video on detail |
| follows (3) | Following head and first figure | Following figure |
| channel count / channel list | Ledger (count 5), Overview (list) | Corpus (the list; the count is its length) |
| recently indexed | Overview panel, Videos table sorted by `indexed_at` | the feed (§7 already says so) |

`corpus-summary` is the agent's read and stays as it is; the rule binds the
console's pages, so the band lives on one of them.

## Overview → Health

| Block | Repeats | Verdict | Reason |
|---|---|---|---|
| Head: `data_status`, indexed time | Ledger head | keep | The one-word answer to "is it OK". |
| Drift notice (when vectors are off) | none | keep | It is the health question. |
| Corpus band (5 figures) | Ledger, `corpus-summary` | move to Corpus | A count is not health. |
| Pipeline readiness strip | Ledger | keep | Health, and only here. |
| Models declared / served | none | keep | Health; drift lives between these two tables. |
| Recently indexed (8 rows) | Videos sorted by `indexed_at` | move to the feed | §7: "what is new" is the feed's. |
| The queue (2 lines) | Ledger jobs | keep | "Is it doing something now". |
| What is missing (3 lines) | Ledger, Overview band | keep, merge Ledger's 2 vector backlogs in | Gaps are health; the failed-video line links to Corpus instead of restating it. |
| Storage (2 bytes) | Ledger | move to Corpus | Size of what is in it. |
| Channels (5 rows) | Ledger count | move to Corpus | Replaces the bare count there. |
| Tags | Ledger count | move to Corpus | Same. |

## Ledger → Corpus

| Block | Repeats | Verdict | Reason |
|---|---|---|---|
| Head: counted, indexed | Overview head | keep "counted", cut "indexed" | One reading time is enough. |
| Corpus band | Overview | keep | Its single home. |
| Videos by state (5) | Overview band and gaps | keep | Its single home for "1 failed". |
| Jobs by state (5) + backoff + failed 24 h | Overview queue, Jobs page | move to Jobs | Counts of jobs belong where jobs are filtered; the figures are already links there. |
| What is missing (3) | Overview | merge into Health | Backlogs are health. |
| Filed under: channels 5, tags 2 | Overview lists | merge: show the lists | A count without names answers nothing. |
| Keyframe JPEGs, index file | Overview | keep | Single home. |
| Pipeline readiness | Overview | cut | Health's. |

Phone: the head line (`counted 2026-10-03T15:56:46Z · indexed …`) overflows the
390 px viewport by about 25 px; the page scrolls sideways.

## Search

| Block | Repeats | Verdict | Reason |
|---|---|---|---|
| Query form, three channel boxes | none | keep | |
| Leg breakdown (8 rows: FTS, vec, knn, OCR, frame…) | none | keep, collapsed by default | Owner inspection (§14); 8 rows push results below the fold on phone. |
| Leg notes (worker unreachable…) | none | keep | |
| Result moments | none | keep | |

## Videos

| Block | Repeats | Verdict | Reason |
|---|---|---|---|
| Filter band (9 controls) | none | keep, collapsed on phone | On phone it fills the whole first screen. |
| Count line "50 shown of 669" | Corpus band | keep | Pagination, and it changes with filters. |
| Table: frame, title, published, duration, state, coverage, tags, indexed, actions | none | keep | |
| Coverage as `t o f` letters | none | keep, relabel | Three bare letters need the tooltip to read. |
| Row actions Re-index, Tag | Video detail "manage" | keep | |

Phone: each row turns into a 9-line card; 50 rows make the page 16,800 px tall.
Proposal: on phone, a row is title, channel, state and indexed date; the rest is
on the detail page.

## Video detail

| Block | Repeats | Verdict | Reason |
|---|---|---|---|
| Head: title, state, data_status, channel, published, duration, language, indexed | none | keep | |
| Scene timeline | none | keep | |
| What was stored (6 figures) | none | keep | Per video, so no repeat. |
| Provenance (7 stages, model, started, took) | Job detail "stage by stage" | keep | Single home for stage timings. |
| Frames and what the machine read (24 per page) | none | keep | Empty in the scratch stack. |
| Transcript by chunk | none | keep | |
| Chapters | none | keep | Source metadata; the feed's video screen shows moments, not chapters. |
| Recent indexing runs | Jobs filtered by video | keep | One row, links to the job. |
| Manage: re-index, tags | Videos row actions | keep | |

Phone: 12,200 px tall, mostly frames. Proposal: frames collapsed behind their
count on phone.

## Jobs

| Block | Repeats | Verdict | Reason |
|---|---|---|---|
| Head: state, order, refresh | the filter band | cut | Restates the filters right below it. |
| Filter band | none | keep | |
| (new) jobs by state, from Ledger | Ledger | move here | Counts beside the filter they open. |
| Table | Follow detail "recent checks" for `follow_check` | keep, hide `follow_check` by default | 3 follows × 4 checks a day fill the first screen; on 2026-10-03, 11 of the first 13 rows were checks. Follow detail already lists them. |
| Row title "1 item(s), none fetched yet" on a done `follow_check` | none | fix | Wrong for a finished check; say "checked @handle". |

## Job detail

| Block | Repeats | Verdict | Reason |
|---|---|---|---|
| What it cost (wall, runner, queued, items) | none | keep | |
| Items table | none | keep | |
| Stage by stage, for the last item | Video detail provenance | keep only when that item failed or degraded | For a done item it repeats the video page, which is linked. |
| Event log | none | keep | Only home of warnings like "transcript came from yt_auto". |

## Add videos

| Block | Verdict | Reason |
|---|---|---|
| The form | keep | A write, no numbers. |

## Following

| Block | Repeats | Verdict | Reason |
|---|---|---|---|
| Head "follows 3" | first figure | cut | Same number twice, 20 px apart. |
| Figures: follows, active (paused, failing), brought in, held, due within the hour, budget | Follow detail "brought in" per follow | keep | Totals here, one follow's share there. |
| Table of follows | none | keep | |
| Follow form | Add videos (its "Add videos" button) | keep | |

## Follow detail

| Block | Repeats | Verdict | Reason |
|---|---|---|---|
| Head, rule, clocks, source | Following table row | keep | |
| Pause, check now, unfollow, edit rule | none | keep | |
| Recent checks (10) | Jobs with `kind=follow_check` | keep | Single home once Jobs hides checks. |
| Jobs this follow queued (10) | Jobs | keep | Scoped to this follow. |
| What it passed over (25 per page) | none | keep, collapse by decision | Every `skipped_horizon` row repeats the same two-line reason and date; the 25 rows take about 2,800 px. Show the decision chips with counts, rows on click. |

## The rail and the chrome

| Block | Verdict | Reason |
|---|---|---|
| Rail: Overview, Ledger, Search, Videos, Jobs, Add videos, Following | keep, rename Overview → Health and Ledger → Corpus | Names say the question. |
| `auth=token` above Sign out | cut | Operator detail with nothing to act on. |
| Phone: rail becomes 3 rows of links, then auth and Sign out | rework | About 240 px of the first screen before the page title. |
| Mixed time formats (`2026-10-03T15:56:46Z` beside `2026-10-03 08:16`) | fix | Ledger head and readiness use ISO UTC; everything else uses local minutes. |
