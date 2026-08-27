# Lessons

Operational rules paid for by an incident, a benchmark, or a launch. Each one
is here because it is not derivable from the code and would otherwise have to
be relearned the expensive way.

Decisions live in `docs/design/DECISIONS.md`; open work lives in
`docs/ROADMAP.md`; conventions live in `AGENTS.md`. This file is only the
things that bit.

---

## Deploying and cutting over

**A cutover freezes the pipeline first, and the queue must be verified empty,
not merely quiet.** `run_pipeline` defaults `True` and is *not* tied to
read-only mode — read-only unregisters the write **tools**, not the **runner**
— and `VIDTHEQUE_STALE_CLAIM_S` makes a job the old process had claimed
re-claimable minutes later. So a database migrated with live queue rows gives
you the *public* box fetching from YouTube behind the tunnel, from a fresh IP
with no bot-check history. Check both tables:

```sql
SELECT state, COUNT(*) FROM jobs GROUP BY 1;      -- nothing running, nothing queued
SELECT state, COUNT(*) FROM job_items GROUP BY 1;
SELECT index_state, COUNT(*) FROM videos GROUP BY 1;  -- no indexing, no pending
```

At the last cutover this was 1 job running, 4 items queued, 1 item running.
Not hypothetical.

**Cancel terminally. Do not park.** Pushing `not_before` into the future looks
like a pause and is not one: the row still reads `queued`, so it fails the
emptiness check above, and it leaves a live timer pointed at the box you are
about to expose.

**Why it matters beyond tidiness:** the worker has one FIFO GPU queue with no
priority. A whisperX stage in flight makes every visitor's search wait out its
timeout and then return FTS-only — not an error, just a worse answer, which is
the worst failure mode available because it looks like the product being bad.

**Stopping the connector is the rollback, and it is seconds.** No DNS TTL to
wait out. But it does **not** un-publish the keyframes: `/frames/*.jpg` is
`Cache-Control: public, max-age=86400` and `.jpg` is edge-cached by default, so
copies outlive the origin for up to a day. If the rollback is about *content*
rather than an outage, purge the cache too — and accept that already-downloaded
copies are gone for good, as they are for anything ever published.

**Rotate on exposure.** A leaked tunnel token or credentials JSON: rotate,
restart the connector, then force-disconnect existing connections through the
API. A leaked `OPENROUTER_API_KEY`: revoke at OpenRouter **first**, change the
config second — the running process holds it in memory until restart.

**Keep the rollback card next to whichever box currently serves writes.** That
box has changed twice since launch. A rollback procedure written into a dated
launch runbook describes a topology that no longer exists, and the second lever
on the last one pointed at a container that had already been retired the same
day it shipped.

## Running the box

**A persistent `E_RATE_LIMIT` 403 loop on media downloads is a broken yt-dlp
build, not throttling.** vidtheque classifies YouTube's 403 as `E_RATE_LIMIT`,
which is right for the transient block it usually is — so a build YouTube has
broken looks like an endless rate limit: the identical 403 on every retry,
90-minute cool-offs, an attempt ceiling hours away. **Persistence across
cool-offs is the tell.** Test the current nightly standalone binary on the box
before touching anything else. The pin moves as a deliberate commit, separate
from the version bump.

**The real YouTube block is 60–90 minutes against the IP or guest session**, so
a 300 s backoff with 3 attempts burns the entire retry budget in ~12 minutes
and is still blocked at the end of it. This is why the backoff constant is
5400 s.

**The rate limiter's trust boundary is Cloudflare.** It keys off
`CF-Connecting-IP` in preference to the socket address, which is safe only with
an edge in front. Any deployment without one must clear
`VIDTHEQUE_TRUSTED_IP_HEADER`, or every per-IP limit is spoofable with a header.

**A feature that shipped is not a feature that is on.** Following shipped in
0.0.5 with checks defaulting to enabled, and the corpus still stopped growing
— because no channel was ever followed. Before diagnosing the pipeline, check
whether the thing has any rows: `SELECT COUNT(*) FROM follows`.

## Writing things down

**Open work does not live in a dated document.** A "Backlog" section inside a
handoff is a snapshot of one night. Two of the five items in the 2026-08-09
backlog had shipped by the evening of the day it was written, and reading it a
fortnight later cost a day of planning work already done. Open work belongs in
`docs/ROADMAP.md`; a dated record may describe what happened, never what is
left.

**A measurement is worth more than the prose around it.** Nearly every file in
`research/` survives an audit not because it reads well but because something
cites it — 25 source files name a research document, and the GPU and decode
benchmarks cannot be reproduced without the same hardware. Delete narration
freely; never delete a number you cannot re-measure.

**Cite the file, not the memory.** The design docs' habit of naming the
research file and section beside a constant is what makes the constants
auditable years later. `DEFAULT_RATE_LIMIT_BACKOFF_S = 5400` means nothing
alone; with `research/ytdlp-usage-audit-2026-08-10.md` beside it, it is a
finding.

## Design and assets

**Vendored fonts stay under a 200 KB total budget**, OFL-licensed and
latin-subset, with no CDN and no runtime network request. The rule predates
and outlives the specific faces it was written against; the current
Archivo + JetBrains Mono pair spends 75 KB of it.
