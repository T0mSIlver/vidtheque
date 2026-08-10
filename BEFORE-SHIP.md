# BEFORE SHIP — 2026-08-11

The ordered list for launch morning. Written overnight against
`research/release-staging-2026-08-11.md`, which is the reasoning; this is the
doing. `docs/deploy-public.md` stays the authority for the go-public checks
themselves and is referenced by section rather than copied.

**Nothing here has been executed. Nothing is public.**

Both documents were attacked by an independent reviewer before you read them
(codex `gpt-5.6-sol`, high effort, web search, read-only over this repo, driven
in a herdr pane) — 25 findings, 3 of them blockers, all folded in and credited
in `research/release-staging-2026-08-11.md` §10. The three blockers were:
**the public container never set its bind address** (Phase 4.1),
**the cutover command kills the worker it depends on** (Phase 4.2), and
**anonymous MCP calls can exhaust the GPU queue** (Phase 1, last item).

**Owners.** `[TOM]` needs Tom — a decision, a credential, host access, or a
promise only he can make. `[BOTH]` is the joint session. `[AGENT]` an agent can
do once told to. `[AGENT-NOW]` an agent could have done last night and did not,
because it touches a file a sibling agent owns or a decision that is Tom's.

**Durations** are working estimates including the reading, not stopwatch times.
The critical path is roughly **3–4 hours** from standup to a shared URL, of
which about 40 minutes is waiting on transfers and smoke tests.

**Read the four gates first.** Every one of them can be worked in parallel with
the build; none of them may be skipped to get the URL out.

---

## PHASE 0 — The gates (nothing public until every one is true)

Five gates here, plus the corpus freeze in Phase 2.1, which is the sixth and is
listed there because it is also the first build step. These are not ordered
among themselves. They are ordered *before everything*.

### G1 · Merge the security branch `[TOM]` · ~20 min + review

- [ ] Tom's overnight security worktree merges into `main`
- [ ] `make test` green after the merge (last agent run: **1121 tests**)
- [ ] `uv lock --check` clean, CI green on the merge commit
- [ ] Any finding in that branch that changes a *shipped default* is re-checked
      against `deploy/.env.example` — an env var without an entry there is a bug
      (CLAUDE.md)

> Everything downstream builds from the merged `main`. Do not clone the public
> box from a pre-merge commit "to save time"; the whole point of the branch is
> that it changes what is safe to expose.

### G2 · The trusted-CIDR / tunnel-proxy question `[BOTH]` · ~30 min

*Split in two, because the third check below cannot physically run before Phase
5 — the tunnel does not exist yet. **G2a** is a Phase-0 configuration gate;
**G2b** is a Phase-6 runtime gate that blocks sharing the URL.*

The deferred item from `HANDOFF-2026-08-10.md` that is marked "needs Tom".
Behind a tunnel the socket peer is *cloudflared*, so any CIDR covering
loopback or the container network makes **every anonymous visitor an owner** —
owner clamps on both API prefixes and the `max_text_chars=0` full-transcript
hatch that `demo-site.md` §2 reserves for an owner's agent.

**G2a — before the build (Phase 0):**

- [ ] `VIDTHEQUE_DASHBOARD_TRUSTED_CIDRS` is **empty** in the public box's
      `stack.env` — verify by grep, not by memory:
      `grep -E '^VIDTHEQUE_DASHBOARD_TRUSTED_CIDRS=.+' $DATA/stack.env` must
      print nothing
- [ ] Boot log carries **no** `treated as the owner` warning
      (`dashboard/settings.py:123`)

**G2b — after the tunnel, before sharing (Phase 6):**

- [ ] From outside, with no credential:
      `curl -s https://<host>/dashboard/api/meta | jq .clamps.policy` → `"public"`

**Both:**

- [ ] **Decision recorded**: is "ships empty, warns loudly" the permanent
      answer, or does the code learn to refuse proxy-origin CIDRs / require a
      credential when a trusted header is present? Either answer is fine.
      *No answer* is what fails this gate — write it into
      `research/security-audit-2026-08-11.md`

> Launching does not require the deeper code fix. It requires that the empty
> value is verified in the running process and that the deferral is a decision
> rather than an oversight.

### G3 · Organiser-consent email `[TOM]` · ~15 min to write, unknown to answer

`research/positioning-2026-08-10.md` §9.1 makes this load-bearing: *"the
organisers get asked **before** the demo is announced"*, and it is the honest
half of the answer to the hardest objection the launch will attract. The public
corpus is **191 videos from one channel** — 100% of it belongs to one
organiser.

- [ ] Email sent, naming: what is indexed, that nothing is redistributed, that
      every answer links back to the source at the second it was said, and that
      a channel comes out on request
- [ ] **Name this gate honestly.** As drafted it passed on *sending* the email,
      which makes it a **notification** gate, not a consent gate — and the
      contract's word is "asked… before the demo is announced", with the answer
      treated as load-bearing. So Tom picks one, explicitly, in writing:
      **(a) consent received** is the gate (strongest, and what §9.1's published
      answer implies), or **(b) notification sent** is the gate and the URL goes
      up before a reply. Both are defensible. What is not defensible is a
      checkbox whose title says one and whose body says the other
- [ ] The removal promise has to be true (see G4b)

### G4 · The public surface holds nothing unfinished

**G4a · `/static/lab/*` is publicly reachable** `[AGENT]` · ~20 min

`Route("/static/{asset:path}")` serves any `.html` under `static/`, and
`static/lab/` holds **13 MB** of unreleased landing prototypes —
`hero.html` and `versions/v1…v6.html` with their asset directories. On launch
those answer at `https://<host>/static/lab/versions/v5.html`: six competing
versions of a page that has not shipped, carrying documented DESIGN.md
divergences, invented UI (V1's "multiviewer source bar"), and the m41 quote
whose profanity is still an open call.

- [ ] Decide the mechanism: move `lab/` out of the packaged `static/` tree
      (cleanest), or gate it behind `VIDTHEQUE_PUBLIC_READONLY`, or refuse
      `lab/` in the asset route
- [ ] Whichever: a test asserts `GET /static/lab/versions/v5.html` → 404 in
      public mode
- [ ] Same sweep for the repo-root `public/static/lab/` copy (`moment.html`,
      `grid.html`) — it is a known path accident (`HANDOFF-2026-08-10.md`,
      01:30) and is not served today, but it should not become served by
      accident either

**G4b · The removal path we promised** `[TOM]` decides, `[AGENT]` implements · ~45 min

The positioning contract commits publicly to *"take a channel out on request —
one row, one command, and we say so publicly"*, and lists as a repo-side
obligation that *"an unfollow/remove path exists and is documented… it needs a
runbook line in `docs/deploy-public.md`"*. **Neither exists**: there is no
`delete_video` / `delete-video` implementation in `mcp/src`, and no takedown
section in the runbook.

- [ ] Decide the scope for launch: a documented manual SQL + `rm -rf` procedure
      in `docs/deploy-public.md` is **sufficient** and honest; a tool is not
      required to make the promise true
- [ ] Write it. It must cover: the `videos` row, the cascade
      (`index-schema.md` §"the delete path is `videos` → …"), the keyframe
      directory, the derived-cache entries, and the FTS/vec integrity check
      afterwards
- [ ] The public page's ethic line and any FAQ copy point at it

> This is a gate because it is a promise made in the answer to *"did the
> speakers consent?"*. Publishing the answer without the substance is the one
> failure mode the positioning work explicitly set out to avoid.

---

## PHASE 1 — Standup decisions (15 min, `[BOTH]`, before anything is built)

Eight questions, all from `research/release-staging-2026-08-11.md` §11. Answer
them out loud, write them in the handoff, then stop discussing them.

- [ ] **Hostname.** Recommended: `vidtheque.tomvaucourt.com`. Verified last
      night: that zone is already on Cloudflare nameservers and already
      proxied, so `cloudflared tunnel route dns` works immediately and there is
      **no propagation wait on the critical path**. `vidtheque.dev` is still
      unregistered — buying it this morning adds a registrar step and a
      minutes-to-hours nameserver wait
- [ ] **Ask mode on day one?** Recommended **off**
      (`OPENROUTER_API_KEY=` empty → `/api/meta` reports `ask_enabled: false`
      and the page hides the toggle). If on: a **new, dedicated,
      spend-capped** OpenRouter key, created for this deployment, revocable
      alone. The shipped model id is **not free**
- [ ] **Embedder residency.** A public search calls the GPU at query time; with
      `EMBED_RESIDENT=0` and `IDLE_UNLOAD_SECONDS=300` the first search after
      five minutes of quiet pays **3.5 s of model load + 916 ms**. Recommended:
      `IDLE_UNLOAD_SECONDS=3600` for launch day (changes no invariant),
      `EMBED_RESIDENT=1` later if traffic is real
- [ ] **Dashboard public?** `VIDTHEQUE_DASHBOARD=1` plus the redactions, or the
      commented `^/dashboard → http_status:404` ingress rule. The runbook's §1
      policy question, still open
- [ ] **Tag `v0.0.1`?** It publishes GHCR images (`build-mcp`, `build-worker`
      both fire on `v*`) and makes the README's *"there are no releases and no
      published images yet"* false the moment it lands. Coupled decision, not a
      bug
- [ ] **Reboot policy.** Does the site come back by itself after a host reboot?
      `onboot` on the container **and** `systemctl enable cloudflared` are two
      separate claims — having one of each is the failure
- [ ] **Sandbox stays in demo mode after cutover?** It is the fastest rollback
      (re-point the tunnel's ingress at it, one line), but only if it is not
      reverted to indexing
- [ ] **When does indexing resume?** Not while public — see Phase 2 step 1
- [ ] **The unmetered `/mcp`.** `/mcp` is deliberately never rate limited, and
      under `AUTH=none` anyone can open a session; each `search` submits **two**
      jobs to an **unbounded** GPU queue, and the concurrency semaphore guards
      the SQLite work *after* the embedding. So a stranger with a loop can
      starve every visitor and the shared 3090. Nothing to fix this morning —
      but decide now: launch and watch, or spend the free plan's single
      rate-limiting rule on `/mcp`? (Detail:
      `research/release-staging-2026-08-11.md` §2.2b)

---

## PHASE 2 — Freeze and stage (≈45 min, mostly waiting)

### 2.1 · Freeze the corpus `[BOTH]` · 5 min + drain

**This is not tidiness.** A public search calls the worker twice on a 20-second
timeout, and the worker has **one FIFO GPU queue with no priority**
(`lifecycle.py`: "a single consumer task drains one job queue"). A whisperX
stage in flight (`VIDTHEQUE_STT_TIMEOUT_S=1800`) makes every visitor's search
wait up to 20 s and then silently return **FTS-only** — not an error, just a
worse answer. That is the worst launch-day failure mode available because it
looks like the product being bad.

And there is a second reason, worse than the first: **the job runner starts on
the public box too.** `run_pipeline` defaults `True` and is *not* tied to
public/readonly mode (`app.py:82,191`) — read-only unregisters the write
*tools*, not the *runner* — and `VIDTHEQUE_STALE_CLAIM_S=300` makes a job the
old process had claimed re-claimable five minutes later. A database migrated
with live queue rows will have the **public** box fetching from YouTube behind
the tunnel, from a fresh IP with no bot-check history, with whisperX in front of
every visitor's search.

Measured last night: **1 job `running`, 4 `queued` items, 1 `running` item.**
Not hypothetical.

- [ ] End the rolling-tranche standing order (`HANDOFF-2026-08-10.md`, 16:55)
- [ ] Let the queue drain — then **verify it is empty, not merely quiet**:
      `SELECT state, COUNT(*) FROM jobs GROUP BY 1` and the same for
      `job_items` → nothing `running`, nothing `queued`. Cancel or park
      (`not_before` far in the future) whatever will not finish
- [ ] **Cancel terminally — do not park.** Pushing `not_before` into the future
      leaves rows *queued*, which fails the check above and leaves a timer
      pointed at the public box
- [ ] `SELECT index_state, COUNT(*) FROM videos GROUP BY 1` → no `indexing`,
      no `pending`. A half-indexed video honestly reports `data_status:
      degraded` to a visitor; finish it or leave it out of the snapshot
- [ ] **The GPU queue is a separate claim from the SQLite queue.** STT, both
      embed legs, frame-query and CPU OCR all share the worker's one FIFO, so
      check the worker's own view too:
      `curl -s http://<worker>:8081/status | jq .queue`
      → depth 0, nothing in flight, consumer alive
- [ ] Record the final corpus numbers (last night's baseline: **191 ready**,
      67.0 h, 43,549 cues, 7,034 chunks, 11,781 keyframes, 184,301 OCR lines)

### 2.2 · Create the container `[TOM]` (host access) · ~20 min

Full sizing table and rationale: `research/release-staging-2026-08-11.md` §4.1.

- [ ] Debian 13 (trixie) template, matching the box that produced the data
- [ ] **unprivileged**, `features` **empty** — explicitly turn `nesting` **off**
      (it defaults on for GUI-created containers since ~PVE 8.3; nothing here
      needs it, and it is a weakened-namespace flag on an internet-facing box)
- [ ] rootfs **20 GB** (`pct resize` grows online, **cannot shrink**)
- [ ] corpus on a **volume mount point with `backup=1`**, *not* a bind mount —
      bind mounts are **never included in `vzdump`**, which turns "the container
      is backed up" into "everything except the corpus is backed up", and makes
      `pct rollback` a silent split-brain
- [ ] RAM **2 GB**, swap **0**, cores **2–4**
- [ ] No GPU device nodes. No HuggingFace cache. No `nesting`, no `keyctl`

### 2.3 · Install and clone `[TOM]`/`[AGENT]` · ~15 min

- [ ] `uv`, `ffmpeg`, `curl`, `ca-certificates`, one unprivileged service user
- [ ] `git clone` the **public repo at the post-merge commit** — a clean clone,
      not an rsync of a worktree
- [ ] `uv sync` (**not** `--group gpu`: this box has no CUDA and wants none)
- [ ] `cloudflared` from `pkg.cloudflare.com`, and **two trixie traps**:
      (a) the apt suite is **`any`** — there is **no `trixie` suite** in that
      repo (`dists/trixie/Release` 404s; `dists/any/Release` is live), so a
      "helpful" edit to the codename produces *"does not have a Release file"*;
      (b) fetch `cloudflare-main.gpg` **fresh** — the key rolled 2025-10-30 and
      the old keys were removed 2026-04-30, and Debian 13's `sqv` verifier
      rejects the old SHA-1-bound signatures. Do not copy a keyring over from
      the sandbox. `docs/deploy-public.md` §6.2 already has the right commands
- [ ] **Not** installed, and worth reading as a list: no SSH keys reaching
      anything else, no `gh`/GitHub token, no agent tooling or worktrees, no
      `~/backups`, no second HTTP server bound to `0.0.0.0` for previewing
      anything

### 2.4 · systemd units `[AGENT-NOW]` · ~20 min

`scripts/dev_stack.sh` is a development stack — `setsid nohup`, pidfiles, and
log files nothing rotates (`mcp.log` measured **10 MB after about a day**). A
box that must survive a crash and a reboot unattended wants a unit.

- [ ] `vidtheque-mcp.service`: `EnvironmentFile=$DATA/stack.env`,
      `Restart=on-failure`, `After=network-online.target`,
      `WantedBy=multi-user.target`, journald for logs
- [ ] cloudflared unit hardened: dedicated non-root user, `NoNewPrivileges=yes`,
      `ProtectSystem=strict`
- [ ] Introduces **no new environment variable** (if it does, it needs a
      `deploy/.env.example` entry in the same commit)

> Marked `[AGENT-NOW]` because it is a new file in nobody's lane. It was left
> undone deliberately: a unit that has never been run on the box it targets is
> a draft, and drafting it against a container that does not exist yet risks
> encoding the wrong paths.

---

## PHASE 3 — Move the corpus (≈30 min, mostly transfer)

Detail and verification queries: `research/release-staging-2026-08-11.md` §5.

- [ ] **3.1** Copy `keyframes/` (**1.4 GB**, 11,781 files) *while the old stack
      is still up* — append-only during indexing, immutable after, and it takes
      the bulk transfer off the cutover clock
- [ ] **3.2** Stop the old stack
- [ ] **3.3** Snapshot the database — **never a plain file copy** (WAL: a bare
      copy is a torn database). `VACUUM INTO '/path/vidtheque-2026-08-11.db'`
      after a clean stop; measured at 32 ms, compacted and consistent
- [ ] **3.4** Copy the snapshot (**195 MB**) and re-run the `keyframes/` rsync
      to catch step 3.1's window (seconds)
- [ ] **3.5** Take `audio/` (**1.4 GB**) — the input to STT and the only
      insurance against a talk being taken down after launch. To the backup if
      not to the container
- [ ] **3.6** Do **not** copy: `media/` (76 MB scaffolding), `derived/`
      (disposable LRU), `run/` (pidfiles + unrotated logs), and above all
      **`stack.env`** — rewrite it (Phase 4)
- [ ] **3.7** Let the new box mint its own `secret.key`; do not carry
      `openrouter.env`
- [ ] **3.8 Fresh rollback point** before cutover: snapshot + `keyframes/` under
      `/home/dev/backups/vidtheque-2026-08-11-cutover/`, `chmod a-w`. Last
      night's `/home/dev/backups/vidtheque-2026-08-11/` (2.8 GB, online-backup
      API, integrity checked) stays as the older generation — it is **ten
      videos stale**
- [ ] **3.9 Verify before believing it**: `PRAGMA integrity_check`, the FTS
      integrity insert, the four counts against the numbers in 2.1, the
      `index-schema.md` §3.3 vector-drift query, and
      `find $DATA/keyframes -type f -name '*.jpg' | wc -l` matching the
      `keyframes` row count. **Use the corrected snippet in
      `research/release-staging-2026-08-11.md` §5.6** — the obvious version is
      broken twice over: a quoted heredoc passes `$DATA` through literally, and
      the FTS integrity command is a *write* that a `mode=ro` connection
      refuses. A verification that always fails is one that gets skipped
- [ ] **3.10** `keyframes/` is **not** strictly append-only — a reindex deletes
      and republishes — so the second pass is `rsync --delete`, not additive,
      and step 2.1 must have shown no active stages

---

## PHASE 4 — Config and local pre-flight (≈30 min)

Run every check against `http://127.0.0.1:8100` **before the tunnel exists**.
A thing that is wrong here is wrong through the tunnel too, and cheaper to find.

### 4.1 · Write `stack.env` from `.env.example` `[BOTH]`

```sh
# --- PUBLIC container stack.env ---
VIDTHEQUE_AUTH=none
VIDTHEQUE_PUBLIC_READONLY=1
PUBLIC_URL=https://vidtheque.tomvaucourt.com          # https, full origin
VIDTHEQUE_PUBLIC_HOSTNAME=vidtheque.tomvaucourt.com   # bare host, no scheme
VIDTHEQUE_HOST=127.0.0.1        # <-- NOT the 0.0.0.0 default. See below.
VIDTHEQUE_PORT=8100             # <-- NOT the 8080 default. See below.
VIDTHEQUE_TRUSTED_IP_HEADER=CF-Connecting-IP          # the default — keep it
VIDTHEQUE_DASHBOARD_TRUSTED_CIDRS=                    # MUST be empty (G2)
VIDTHEQUE_DASHBOARD=1                                 # or 0, per Phase 1
WORKER_URL=http://<sandbox-ip>:8081
VIDTHEQUE_DATA_DIR=/home/<user>/vidtheque-data
OPENROUTER_API_KEY=                                   # empty = ask mode off
```

- [ ] **`VIDTHEQUE_HOST` and `VIDTHEQUE_PORT` are in the file.** They are the
      two lines this list originally forgot, and forgetting them is a blocker:
      `mcp/__main__.py:22-24` defaults to **`0.0.0.0:8080`**, and
      `dev_stack.sh` only ever passed them **inline**, so they have never lived
      in `stack.env`. A systemd unit that just reads `stack.env` therefore binds
      `0.0.0.0:8080` — the tunnel 502s against `127.0.0.1:8100`, *and* the origin
      becomes LAN-reachable, where a forged `CF-Connecting-IP` mints a fresh
      bucket per request and voids every per-IP limit
- [ ] **The embedder-residency knob goes on the OTHER box.**
      `IDLE_UNLOAD_SECONDS` / `EMBED_RESIDENT` are read by `worker/`, so they
      belong in the **sandbox's** `stack.env`. Put them here and Phase 1's
      latency decision silently never happens — and the Phase 6 cold-search
      measurement cheerfully confirms the old behaviour

```sh
# --- SANDBOX container stack.env (where the worker runs) ---
IDLE_UNLOAD_SECONDS=3600        # or EMBED_RESIDENT=1 — per Phase 1
```

- [ ] Key-set diff against `deploy/.env.example` (`docs/deploy-public.md` §2.4)
- [ ] `.env` / `stack.env` is not committed anywhere; `git log --oneline --
      deploy/.env` is empty

### 4.2 · The private worker link `[BOTH]` · 15 min

Topology B's one genuinely new rule — and `scripts/dev_stack.sh` **cannot
express it as written**. Two facts, both blockers if met at 11:00 instead of
now:

- its `start` pins `VIDTHEQUE_HOST=127.0.0.1` for the worker **inline**, so
  `stack.env` cannot override it;
- its `stop` stops **both** services and has no worker-only verb — so Phase 3.2's
  "stop the old stack" takes down the worker the public box is about to depend
  on, and nothing in the plan brings it back bridge-bound.

- [ ] **Decide the mechanism first**: edit `dev_stack.sh` so `VIDTHEQUE_HOST`
      comes from the environment, or give the worker its own systemd unit on the
      sandbox (which also answers the liveness gap below). Either is ~10 minutes
      *now* and an unbudgeted detour later
- [ ] Worker binds the **sandbox container's bridge address**
- [ ] Firewall accepts `:8081` **only** from the public container — the worker
      answers an unauthenticated OpenAI-compatible API and the network is the
      entire authorization story
- [ ] From the public container: `curl -fsS http://<sandbox-ip>:8081/healthz`
      and `/status` both answer
- [ ] From **anywhere else on the LAN**: both refused
- [ ] `mcp` still binds `127.0.0.1` — cloudflared is the only thing that talks
      to it, and that is the precondition that makes trusting
      `CF-Connecting-IP` sound (`docs/deploy-public.md` §4)

### 4.3 · Mode verification from outside the process `[BOTH]`

Not by reading the file — `docs/deploy-public.md` §2.1, §2.3, §2.5, §2.6:

- [ ] `curl -s 127.0.0.1:8100/api/meta | jq '{auth, ask_enabled, mcp_url, clamps, limits}'`
      → `auth: "none"`, `clamps.policy: "public"`, `mcp_url` already the public
      hostname
- [ ] `curl -s 127.0.0.1:8100/healthz` → **`writes_allowed: false`**
      (it reports `true` on the sandbox today, because that box is in indexing
      mode — this is the one-line proof the flag reached the process)
- [ ] `tools/list` on `/mcp`: **seven** read tools; `index-video` and
      `tag-video` **absent**, not present-and-refusing
- [ ] Write routes 404 (not 403):
      `for p in login logout index; do curl -s -o /dev/null -w "%{http_code} /$p\n" -X POST 127.0.0.1:8100/dashboard/$p; done`
- [ ] Redaction greps on `/dashboard` and `/dashboard/jobs` (§2.5) — no source
      URLs, no error text, no event messages, no model ids, no `auth=` line
- [ ] **`Legs:` names the vector leg — checked through MCP, not `/api/search`.**
      The facade does not carry `leg_counts`, so a demo page full of results is
      *no evidence at all* about the vector leg:
      `uv run --no-sync scripts/mcp_call.py --url http://127.0.0.1:8100/mcp call search '{"q":"evals","limit":3}' | grep -i '^Legs:'`
      **The gate is a non-zero `vec` count on both the transcript and frame
      legs** — `Legs:` prints the leg *names* even at `vec 0`, so "it named the
      vector leg" is a check that FTS-only search passes. On the facade side,
      the negative check is
      `curl -s '…/api/search?q=evals' | jq -r '.notes[]?'` → nothing about an
      unreachable embedding worker
- [ ] `make test` green on the box that will serve

---

## PHASE 5 — Tunnel and DNS (≈30 min)

`docs/deploy-public.md` §5–§6 is the procedure; this is the checklist.

- [ ] `cloudflared tunnel login` → pick the zone (already Active — verified)
- [ ] `cloudflared tunnel create vidtheque` — **copy the UUID and the literal
      credentials path out of the output**; do not assume the directory
      (`sudo` resolves `$HOME` to `/root`)
- [ ] `cp deploy/cloudflared.example.yml ~/.cloudflared/config.yml`, fill the
      three placeholders, service URL `http://127.0.0.1:8100`
- [ ] `cloudflared tunnel ingress validate`
- [ ] `cloudflared tunnel ingress rule … https://<host>/mcp` — and `/dashboard`,
      and a `/frames/x-00000.jpg` if the optional 404 rule was enabled
- [ ] `cloudflared tunnel route dns vidtheque <host>` — **do not hand-create the
      record**. It must be a **proxied** CNAME; proxied is what puts
      `CF-Connecting-IP` on the request, which is what makes the rate limiter
      work at all
- [ ] Run it in the **foreground once** and watch the log. Do Phase 6 while it
      is in the foreground
- [ ] **Do not test with a quick tunnel** — `trycloudflare.com` does not support
      SSE, and `/mcp` *is* an SSE transport, so it will report the product as
      broken
- [ ] Only after Phase 6 passes: `service install` with an explicit `--config`,
      then **read the generated `ExecStart`** — a known failure writes a
      DNS-proxy invocation and the unit reports `active (running)` with no
      tunnel behind it
- [ ] `systemctl is-enabled cloudflared` **and** `pct config <id> | grep onboot`
      — two separate claims, per Phase 1's reboot decision
### Cloudflare dashboard settings — do this BEFORE Phase 6, or Phase 6 lies to you

Detail and citations: `research/release-staging-2026-08-11.md` §6.5.

- [ ] **Configuration Rule → Browser Integrity Check: Off** for `/mcp`,
      `/api/*`, `/frames/*`. **BIC is ON by default on Free** and challenges
      *"visitors without a user agent or with a non-standard user agent"* —
      which is every MCP client, every agent HTTP stack, and half the smoke
      tests. This is the single most likely way to spend an hour debugging the
      application for an edge setting. Free gets 10 Configuration Rules
- [ ] **Bot Fight Mode: OFF.** It **cannot be skipped** by WAF custom rules or
      Page Rules (it runs outside the Ruleset Engine), it is whole-domain with
      no path scoping, and Cloudflare's own docs warn it *"may challenge API or
      mobile app traffic"*
- [ ] **Under Attack mode / high Security Level: OFF.** HTML challenge pages to
      a client expecting JSON, and headless pre-clearance is impossible
- [ ] **"Remove visitor IP headers" Managed Transform: OFF** (available on all
      plans; it deletes `cf-connecting-ip` and silently puts the whole internet
      in one rate-limit bucket). **Pseudo IPv4: OFF** (it overwrites
      `CF-Connecting-IP` with a hash). **No request-header Transform Rule on
      this hostname, ever**
- [ ] **0-RTT: OFF** — only GET/HEAD/OPTIONS ride as early data, and MCP opens
      its listening stream with a GET
- [ ] **No zone-wide "Cache Everything" rule.** Rocket Loader, Hotlink
      Protection, HTTP/3: off. Free Managed Ruleset and Email Obfuscation: leave
      on (the first can't be disabled; the second is HTML-only)
- [ ] Optional, and an **experiment not a step**: Configuration Rule →
      *Response Body Buffering: None* on `/api/ask` and `/mcp`. Plan
      availability on Free is undocumented; it is the lever against the
      ~100 KB edge prefix-buffer if the ask stream stalls late

---

## PHASE 6 — Smoke through the tunnel (≈30 min)

`docs/deploy-public.md` §7, run **from a device that is not the box** — a phone
on cellular is the honest test: no shared DNS cache, no `/etc/hosts`, no local
route.

- [ ] **`/mcp` first** — the 421 check. A `421 Misdirected Request` means
      `VIDTHEQUE_PUBLIC_HOSTNAME` does not name this hostname, and **nothing
      else on the site will have told you**: `/`, `/api/*`, `/frames/*`,
      `/dashboard/*` and `/healthz` all work perfectly while the actual product
      is dead
- [ ] `claude mcp add --transport http vidtheque https://<host>/mcp`, then
      `tools/list` → seven read tools, no write tools
- [ ] `/api/meta` → `mcp_url` is the public hostname (wrong `PUBLIC_URL` means
      **every visitor copies a URL pointing at their own laptop**)
- [ ] Open the page in a real browser with the network panel up: **zero** failed
      image requests. `thumb` URLs are absolute and on the public hostname — a
      `curl` returning the right string and a page that renders are not the same
      claim, and the dashboard renders fine on any hostname so it proves nothing
- [ ] One `get-frames` URL fetched from outside
- [ ] `/dashboard` and `/dashboard/jobs` → 200, redaction greps re-run **through
      the tunnel** (a flag reaching the local process does not prove it reached
      the process the tunnel points at)
- [ ] 35 rapid searches → 200s then 429s carrying `E_RATE_LIMIT` and
      `retry_after_s`; then a **second device on a different network** gets a
      fresh bucket. That single test is the whole client-IP story
- [ ] If ask mode ships: the SSE timestamp-spread test (§7.3), with the response
      `Content-Type: text/event-stream` confirmed first
- [ ] **The MCP GET listening stream**, explicitly. POST-carried tool results
      are the path reported to work through tunnels; the server-initiated GET
      stream has an [open, unanswered cloudflared
      bug](https://github.com/cloudflare/cloudflared/issues/1449) (SSE over GET
      buffered until close, POST fine). Not a rollback if it fails — but find
      out before a user does
- [ ] **A `curl` with no `User-Agent` at all** against `/mcp` and
      `/api/search`. If Browser Integrity Check is still armed on those paths,
      this is the request that finds out — and it is the cheapest analogue of
      somebody's homegrown agent
- [ ] **Cold-vs-warm first search**, measured: once after ≥ `IDLE_UNLOAD_SECONDS`
      of quiet, once immediately after. The gap is the number Phase 1's
      residency decision was guessing at — take it with data
- [ ] **Rehearse the rollback** (Phase 8) *before* the URL is shared. A rollback
      you have not run is a plan

---

## PHASE 7 — Share (5 min)

`docs/deploy-public.md` §7.5's sharing checklist, plus:

- [ ] G1–G4 all ticked, and the audit written to
      `research/security-audit-2026-08-11.md` with every item **pass**,
      **accepted risk with a sentence of reasoning**, or **blocked**. A blocked
      item means the URL is not shared
- [ ] `pct snapshot <id> pre-launch` taken on **both** containers, public one
      first — near-instant, and far cheaper than a `vzdump` restore for undoing
      the last twenty minutes
- [ ] `HANDOFF-2026-08-11.md` updated with: the hostname, the topology, where
      the worker lives, the reboot policy, and who to wake

---

## PHASE 8 — The first day

- [ ] **Log rotation.** `mcp.log` reached 10 MB in about a day of indexing with
      nothing rotating it, in a 20 GB container. journald (Phase 2.4) solves it;
      staying on `dev_stack.sh` needs a `logrotate` stanza. "We will notice" is
      not a plan
- [ ] **Disk headroom check.** Everything on this box is static except a
      byte-capped LRU, so *any* growth is a signal and nothing is watching
- [ ] **A liveness ping** — a Cloudflare health check, or a cron elsewhere
      curling `/healthz` through the tunnel. Something that notices the origin
      is down before a visitor does
- [ ] **A *worker* liveness ping, separately.** `/healthz` reports the
      *database's* vector state, not worker reachability, so **a dead worker
      leaves the site green and every search FTS-only, indefinitely.** Under
      topology B the worker is also the one service still running under
      `nohup` with no `Restart=`. Two small things: a systemd unit for the
      worker, and a check that curls the worker's own `:8081/healthz`
- [ ] **Exercise the reboot, do not assert it.** Reboot the container once
      *before* the URL is shared and confirm the site comes back — including
      the worker on the sandbox, which nothing currently restarts
- [ ] **Do not** stand up a metrics stack today. The items above are two unit
      files, a `df` and two `curl`s

### Rollback card — tear this off

**The tunnel is the whole exposure. Stopping the connector is the rollback.**
Seconds. No DNS TTL to wait out, no cache to purge.

```bash
sudo systemctl stop cloudflared          # visitors get a Cloudflare error page
sudo systemctl disable cloudflared       # …and it does not come back at reboot
```

Escalating, each step more permanent:

1. **Stop the connector.** Seconds, reversible with `start`.
   **But it does not un-publish the keyframes**: `/frames/*.jpg` is
   `Cache-Control: public, max-age=86400` and `.jpg` is default-cached by
   Cloudflare, so edge copies survive the origin for up to a day. If the
   rollback is about *content* rather than an outage, also do
   **Caching → Purge Everything** — and accept that already-downloaded copies
   are gone for good, as they are for anything ever published.
2. **Abandon the container.** Re-point the tunnel's ingress at the sandbox
   (`http://<sandbox-ip>:8100`) and debug the new box off the critical path.
   **This is not one line** — it needs the sandbox mcp *running* (Phase 3.2
   stopped it), *bound to the bridge* (`dev_stack.sh` pins loopback), port
   **8100** open (Phase 4.2 opened only 8081), and the sandbox in demo mode
   (Phase 1). Either arrange and **test** all four this morning, or strike this
   lever and rely on step 4 — an untested fallback is not a fallback.
3. **Turn the demo off, keep the box up.** `VIDTHEQUE_PUBLIC_READONLY=0` +
   restart — but that brings the **write tools back**, so only behind a stopped
   tunnel.
4. **`pct rollback <id> pre-launch`.** Undoes the container.
5. **Delete the DNS record**, then **`cloudflared tunnel delete vidtheque`**
   (invalidates the credentials; recreating means redoing Phase 5).

**Rotate on exposure.** Tunnel token or credentials JSON leaked → rotate,
restart the connector, force-disconnect existing connections via the API.
`OPENROUTER_API_KEY` leaked → revoke at OpenRouter **first**, change the config
second; the running process holds it in memory until restart.

---

## Appendix A — What an agent could do tonight

Flagged rather than done, and the reason for each.

| item | why it was not done |
|---|---|
| **G4a**, un-serve `/static/lab/*` | touches `mcp/src/vidtheque_mcp/public/`, which sibling agents own tonight — a concurrent edit there is how a commit swallows someone's work |
| **G4b**, the takedown runbook section | needs Tom's scope decision first (manual procedure vs. a tool), and it is a public promise |
| **Phase 2.4**, the systemd units | new files in nobody's lane, genuinely agent-doable — deliberately deferred because a unit written against a container that does not exist encodes the wrong paths |
| **`SECURITY.md`** | genuinely agent-doable and worth doing before the announcement: `.github/` holds only `workflows/`, so there is **no stated way to report a vulnerability** in a server strangers are being invited to point their agents at. Five lines and an address. Left undone because the address is Tom's to choose |
| `deploy/docker-compose.yml`'s stale stub comment | one-line fix, safe: it claims *"the MCP framework has not been chosen yet (see `mcp/NOTE.md`)"*, the framework **was** chosen (official SDK v2), `mcp/Dockerfile` is real, and **`mcp/NOTE.md` does not exist**. It is the first thing a stranger reads about the deployment the README's quickstart tells them to run |
| **README quickstart publishes the unsafe deployment** | `README.md` tells strangers to run `TUNNEL_TOKEN=… docker compose --profile tunnel up -d` against the **base** compose file, without the public overlay — which is exactly the silent footgun `docs/deploy-public.md` §6.1 describes: readonly off, write tools registered, port on `0.0.0.0`. Two lines to fix, aimed at exactly the people the announcement brings. Left for Tom only because it is the announcement's front door |
| **`worker/README.md` documents the retired model topology** | it still describes Qwen3-Embedding-0.6B (1024-d) + SigLIP 2 (1152-d) as two separate spaces; the shipped default is unified `Qwen3-VL-Embedding-2B` at 2048-d in one slot. A self-hoster reading the worker's own doc picks wrong on their first try |
| **"Any OpenAI-compatible provider" is not true as stated** | full operation also needs `/v1/embeddings/frame-query`, `/v1/embeddings/image` and `/v1/ocr`, which no ordinary provider serves. The honest sentence is "the transcript leg works; frame search and OCR need the worker" |
| `docs/deploy-public.md` §2.6's crawl arithmetic | models 3,460 keyframes; the corpus is 11,781, so the walk is ~98 min and the derived-cache estimate is ~3.4× low |
| `bench/` defaults hard-coding `/home/dev/vidtheque-data` | cosmetic; `embed_latency.py:387`, `frame_retrieval_spotcheck.py:53`. Not a secret, just sloppy in a public repo |
| One README line pointing at `deploy/.env.example` | 48 KB, the document of record, and the best artifact in the repo for a self-hoster — currently reachable only via a comment inside the quickstart |

## Appendix B — Numbers worth having in your head

| | |
|---|---|
| corpus | 191 ready, 67.0 h, 1 channel, 43,549 cues, 11,781 keyframes, 184,301 OCR lines |
| moves | DB 195 MB + keyframes 1.4 GB (+ audio 1.4 GB) ≈ **3.0 GB** |
| does not move | `media/` 76 MB, `derived/` 7.8 MB, `run/` 11 MB, `stack.env` |
| cold embedder | **3.5 s** load + **916 ms** first call, then 11–13 ms |
| query budget | **20 s per call**, no retry — and `content_type=all` makes **two sequential calls**, so a dead worker costs ~**40 s** before FTS-only results with a `note:` |
| rate limits | search 30/min · ask 5/min · frames 120/min · dashboard 120/min · ask 50/UTC-day (persisted) · `/mcp` **never limited** |
| tunnel limits | Proxy Read Timeout 125 s (ask budget is 90 s, deliberately inside) · body 100 MB · headers 128 KB |
| rollback point | `/home/dev/backups/vidtheque-2026-08-11/` (2.8 GB, `a-w`, **10 videos stale**) |
